"""Schedule a real filename replacement at SQLite's pathname/open boundary."""
import os
from pathlib import Path
import sqlite3

import pytest
from test_realms_vm_retention import load

pytestmark = pytest.mark.linux_only


@pytest.mark.parametrize('kind', ['viewer', 'owner'])
@pytest.mark.parametrize('swap', [False, True])
def test_sqlite_read_uses_validated_object(tmp_path, monkeypatch, kind, swap):
    home = tmp_path / 'profile'
    monkeypatch.setenv('HERMES_HOME', str(home))
    if kind == 'viewer':
        cls = load('viewer_state').ControlAuthority
        store = cls(home / 'realms/viewer')
        replacement = cls(home / 'replacement')
        with store._connection() as db:
            db.execute("INSERT INTO control_epochs VALUES('r-review',7)")
        with replacement._connection() as db:
            db.execute("INSERT INTO control_epochs VALUES('r-review',91)")
        read = lambda: cls.read_agent_epoch(store.directory, 'r-review')
        expected, forbidden = 7, 91
    else:
        cls = load('integration').OwnershipStore
        store = cls(home)
        replacement = cls(home / 'replacement')
        store.bind(session_origin='fresh', session_id='safe', task_id='task')
        replacement.bind(session_origin='fresh', session_id='replacement', task_id='task')
        read = lambda: store.resolve(task_id='task')
        expected, forbidden = 'safe', 'replacement'
    # The pathname is atomically replaced after fd validation and SQLite's
    # real pathname resolution, before its real open. No read is mocked.
    replacement.path.chmod(0o666 if kind == 'viewer' else 0o600)
    # SQLite no longer opens a filename. Schedule the identical replacement
    # after the first real fd read instead; never substitute a query or result.
    original = os.read
    inode = store.path.stat().st_ino
    seen = []
    def read_fd(fd, size):
        data = original(fd, size)
        if os.fstat(fd).st_ino == inode and not seen:
            seen.append(fd)
            if swap:
                os.replace(replacement.path, store.path)
        return data
    monkeypatch.setattr(os, 'read', read_fd)
    try:
        value = read()
    except (OSError, ValueError, sqlite3.Error):
        value = 'refused'
    assert len(seen) == 1
    assert value != forbidden, f'validated original but read replacement: {value!r}'
    assert value == expected or (swap and value == 'refused')
