"""Read real authority stores with scheduling only at the validated-fd seam."""
import os
from pathlib import Path
import sqlite3

import pytest

from test_realms_vm_retention import load

pytestmark = pytest.mark.linux_only


def stores(tmp_path, kind):
    home = tmp_path / 'profile'
    if kind == 'owner':
        cls = load('integration').OwnershipStore
        store, other = cls(home), cls(home / 'replacement')
        store.bind(session_origin='fresh', session_id='safe', task_id='task')
        other.bind(session_origin='fresh', session_id='replacement', task_id='task')
        read = lambda: store.resolve(task_id='task')
        expected, forbidden = 'safe', 'replacement'
        update = "UPDATE aliases SET owner='replacement' WHERE kind='task_id'"
    else:
        cls = load('viewer_state').ControlAuthority
        store, other = cls(home / 'realms/viewer'), cls(home / 'replacement')
        for current, epoch in [(store, 7), (other, 91)]:
            with current._connection() as db:
                db.execute('INSERT INTO control_epochs VALUES(?,?)', ('r-review', epoch))
        read = lambda: cls.read_agent_epoch(store.directory, 'r-review')
        expected, forbidden = 7, 91
        update = "UPDATE control_epochs SET value=91"
    return store, other, read, expected, forbidden, update


@pytest.mark.parametrize('kind', ['owner', 'viewer'])
@pytest.mark.parametrize('change', ['none', 'replace', 'symlink', 'chmod', 'write'])
def test_validated_fd_snapshot(tmp_path, monkeypatch, kind, change):
    store, other, read, expected, forbidden, update = stores(tmp_path, kind)
    inode = store.path.stat().st_ino
    original = os.fstat
    seen = []
    def scheduled(fd):
        info = original(fd)
        if info.st_ino == inode and not seen:
            seen.append(fd)
            if change == 'replace':
                other.path.chmod(0o666)
                os.replace(other.path, store.path)
            elif change == 'symlink':
                store.path.unlink()
                store.path.symlink_to(other.path)
            elif change == 'chmod':
                store.path.chmod(0o666)
            elif change == 'write':
                writer = sqlite3.connect(store.path)
                try:
                    writer.execute(update)
                    writer.commit()
                finally:
                    writer.close()
        return info
    monkeypatch.setattr(os, 'fstat', scheduled)
    try:
        value = read()
    except (OSError, ValueError, sqlite3.Error):
        value = 'refused'
    assert len(seen) == 1
    assert value != forbidden
    assert value == expected or (change != 'none' and value == 'refused')


@pytest.mark.parametrize('kind', ['owner', 'viewer'])
@pytest.mark.parametrize('state', ['valid', 'missing', 'corrupt', 'legacy', 'readonly', 'journal', 'wal'])
def test_selection_store_is_nonmutating(tmp_path, kind, state):
    store, other, read, expected, _, _ = stores(tmp_path, kind)
    if state == 'missing':
        store.path.unlink()
    elif state == 'corrupt':
        store.path.write_bytes(b'not sqlite')
    elif state == 'legacy':
        store.path.unlink()
        db = sqlite3.connect(store.path)
        db.execute('CREATE TABLE unrelated(value)')
        db.close()
        store.path.chmod(0o600)
    elif state == 'readonly':
        store.path.chmod(0o400)
    elif state == 'journal':
        Path(str(store.path) + '-journal').write_bytes(b'pending rollback')
    elif state == 'wal':
        db = sqlite3.connect(store.path)
        db.execute('PRAGMA journal_mode=WAL')
        db.close()
    def snapshot():
        return {p.name: (p.read_bytes(), p.stat().st_mode, p.stat().st_ino)
                for p in store.path.parent.iterdir() if p.is_file()}
    before = snapshot()
    try:
        value = read()
    except (OSError, ValueError, sqlite3.Error):
        value = 'refused'
    assert snapshot() == before
    if state == 'valid':
        assert value == expected
    elif state == 'readonly':
        assert value in (expected, 'refused')
    else:
        assert value == 'refused'


@pytest.mark.parametrize('kind', ['owner', 'viewer'])
def test_concurrent_commit_during_fd_copy_refuses(tmp_path, monkeypatch, kind):
    store, _, read, _, _, update = stores(tmp_path, kind)
    writer = sqlite3.connect(store.path)
    writer.execute('CREATE TABLE padding(value)')
    writer.execute('INSERT INTO padding VALUES(zeroblob(150000))')
    writer.commit()
    inode = store.path.stat().st_ino
    original = os.read
    seen = []
    def read_fd(fd, size):
        data = original(fd, size)
        if os.fstat(fd).st_ino == inode and not seen:
            seen.append(fd)
            writer.execute(update)
            writer.commit()
        return data
    monkeypatch.setattr(os, 'read', read_fd)
    try:
        with pytest.raises((OSError, ValueError, sqlite3.Error)):
            read()
        assert len(seen) == 1
    finally:
        writer.close()


@pytest.mark.parametrize('kind', ['owner', 'viewer'])
def test_snapshot_cannot_authorize_after_write_during_query(tmp_path, kind):
    store, _, _, _, _, update = stores(tmp_path, kind)
    snapshot = load('selection_store').read_snapshot
    with pytest.raises(ValueError, match='changed'):
        with snapshot(store.path) as db:
            db.execute('SELECT 1').fetchone()
            writer = sqlite3.connect(store.path)
            try:
                writer.execute(update)
                writer.commit()
            finally:
                writer.close()


@pytest.mark.parametrize('kind', ['owner', 'viewer'])
def test_committed_wal_cannot_hide_disabled_owner_or_human_hold(tmp_path, monkeypatch, kind):
    import subprocess
    import sys
    home = tmp_path / 'profile'
    monkeypatch.setenv('HERMES_HOME', str(home))
    service = load('integration').get_integration(home)
    service.bind(session_origin='fresh', session_id='parent')
    if kind == 'owner':
        path = service.owners.path
        update = "INSERT OR REPLACE INTO owners(id,mode,execution_contract) VALUES('parent','host','optional-targets-v1')"
    else:
        store = load('viewer_state').ControlAuthority(home / 'realms/viewer')
        path = store.path
        update = "INSERT INTO leases VALUES('','generation','human',1,1,0,1)"
    code = ('import os,sqlite3,sys; db=sqlite3.connect(sys.argv[1]); '
            'db.execute("PRAGMA journal_mode=WAL"); db.execute(sys.argv[2]); '
            'db.commit(); os._exit(0)')
    subprocess.run([sys.executable, '-B', '-I', '-c', code, str(path), update], check=True)
    Path(str(path) + '-shm').unlink()
    before = {p.name: p.read_bytes() for p in path.parent.iterdir() if p.is_file()}
    with pytest.raises((OSError, ValueError, sqlite3.Error)):
        service.select_terminal_target(command='true', session_id='parent')
    assert {p.name: p.read_bytes() for p in path.parent.iterdir() if p.is_file()} == before
