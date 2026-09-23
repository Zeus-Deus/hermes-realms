"""Independent correction review probes. No native compute is launched."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from test_realms_vm_retention import owned, fresh, load

pytestmark = pytest.mark.linux_only


@pytest.mark.parametrize('state', ['cold', 'stopped'])
@pytest.mark.parametrize('transition', ['none', 'config', 'provider'])
def test_real_vm_late_prelaunch_authority(owned, monkeypatch, state, transition):
    from hermes_cli.session_execution import SessionExecutionContext, SessionExecutionError
    from tools.terminal_targets import register_terminal_target_resolver, select_terminal_target
    manager, _, compute = owned
    fresh(owned, monkeypatch, start=False)
    monkeypatch.setenv('HERMES_HOME', str(manager.home))
    service = load('integration').get_integration(manager.home)
    service._vm = manager
    owner = service.bind(session_origin='fresh', session_id='retention-owner')
    service.owners.set_kind(owner, 'omarchy-vm')
    if state == 'stopped':
        record = manager.start(owner)
        manager.stop(record['id'])
    load('bridge').get_profile_viewer(manager.home)
    monkeypatch.setattr(load('integration'), 'vm_setup_status', lambda *_: {'ready': True})
    monkeypatch.setitem(load('target_contexts')._BUILDERS, 'omarchy-vm', lambda *a: SessionExecutionContext())
    disposers = [register_terminal_target_resolver('late-review', service.terminal_context, selector=service.select_terminal_target)]
    selection = select_terminal_target('late-review', command='true', session_id=owner)
    events = []
    def transition_now():
        events.append('late-prerequisite')
        if transition == 'config':
            (manager.home / 'config.yaml').write_text('plugins:\n  realms:\n    size: 1280x720\n')
        elif transition == 'provider':
            disposers.append(register_terminal_target_resolver('late-review', service.terminal_context, selector=service.select_terminal_target))
    if state == 'cold':
        original = manager._clone_base
        def clone(*a):
            result = original(*a)
            transition_now()
            return result
        monkeypatch.setattr(manager, '_clone_base', clone)
    else:
        original = manager._free_port
        def free_port(*a):
            result = original(*a)
            transition_now()
            return result
        monkeypatch.setattr(manager, '_free_port', free_port)
    launch = manager._launch
    def witnessed_launch(record, **kwargs):
        events.append('compute-launch')
        return launch(record, **kwargs)
    monkeypatch.setattr(manager, '_launch', witnessed_launch)
    try:
        if transition == 'none':
            selection.realize().check()
            assert events == ['late-prerequisite', 'compute-launch']
        else:
            with pytest.raises(SessionExecutionError):
                selection.realize()
            assert events == ['late-prerequisite'], events
            kept = manager.registry.records()[0]
            assert kept['status'] == 'stopped'
            disk = Path(kept['session_dir']) / 'disk.qcow2'
            saved = disk.read_bytes(), disk.stat().st_ino, kept['id']
            # A new unchanged selection must resume the same retained data.
            (manager.home / 'config.yaml').unlink(missing_ok=True)
            monkeypatch.setattr(manager, '_clone_base', original if state == 'cold' else manager._clone_base)
            if state == 'stopped':
                monkeypatch.setattr(manager, '_free_port', original)
            select_terminal_target('late-review', command='true', session_id=owner).realize().check()
            current = manager.registry.records()[0]
            assert (disk.read_bytes(), disk.stat().st_ino, current['id']) == saved
    finally:
        for dispose in disposers:
            dispose()
        load('target_contexts').release_targets(service, owner)
        load('bridge').close_profile_viewer(manager.home)


@pytest.mark.parametrize('kind', ['owner', 'viewer'])
def test_readonly_store_never_creates_wal_sidecars(tmp_path, monkeypatch, kind):
    home = tmp_path / 'profile'
    monkeypatch.setenv('HERMES_HOME', str(home))
    service = load('integration').get_integration(home)
    service.bind(session_origin='fresh', session_id='parent')
    if kind == 'owner':
        store = service.owners
        path = store.path
        update = "INSERT INTO owners(id,mode) VALUES('unrelated','realm')"
    else:
        store = load('viewer_state').ControlAuthority(home / 'realms/viewer')
        path = store.path
        update = "INSERT INTO control_epochs VALUES('r-review',2)"
    read = lambda: service.select_terminal_target(command='true', session_id='parent')
    code = 'import os,sqlite3,sys; d=sqlite3.connect(sys.argv[1]); d.execute("PRAGMA journal_mode=WAL"); d.execute(sys.argv[2]); d.commit(); os._exit(0)'
    subprocess.run([sys.executable, '-c', code, str(path), update], check=True)
    Path(str(path) + '-shm').unlink()
    before = {p.name: p.read_bytes() for p in path.parent.iterdir() if p.is_file()}
    try:
        read()
    except (OSError, ValueError, sqlite3.Error):
        pass  # A non-mutating refusal is acceptable.
    after = {p.name: p.read_bytes() for p in path.parent.iterdir() if p.is_file()}
    assert after == before, sorted(after.keys() - before.keys())


@pytest.mark.parametrize('transition', ['none', 'config', 'provider'])
def test_real_regular_late_prelaunch_authority(tmp_path, monkeypatch, transition):
    from hermes_cli.session_execution import SessionExecutionError
    from tools.terminal_targets import register_terminal_target_resolver, select_terminal_target
    home = tmp_path / 'profile'
    monkeypatch.setenv('HERMES_HOME', str(home))
    service = load('integration').get_integration(home)
    owner = service.bind(session_origin='fresh', session_id='regular-owner')
    load('bridge').get_profile_viewer(home)
    monkeypatch.setattr(load('integration'), 'setup_status', lambda **kw: {'ready': True})
    disposers = [register_terminal_target_resolver('regular-late-review', service.terminal_context, selector=service.select_terminal_target)]
    selection = select_terminal_target('regular-late-review', command='true', session_id=owner)
    # /run is private tmpfs in the reviewed bwrap runner, not the host /run.
    Path('/run/user/' + str(os.getuid())).mkdir(parents=True, exist_ok=True)
    workspace = load('workspace')
    original = workspace.create
    events = []
    def create(record):
        result = original(record)
        events.append('workspace-prepared')
        if transition == 'config':
            (home / 'config.yaml').write_text('plugins:\n  realms:\n    size: 1280x720\n')
        elif transition == 'provider':
            disposers.append(register_terminal_target_resolver('regular-late-review', service.terminal_context, selector=service.select_terminal_target))
        return result
    monkeypatch.setattr(workspace, 'create', create)
    class LaunchWitness(Exception):
        pass
    def refuse_native_launch(argv, **kwargs):
        assert argv[0] == 'systemd-run'
        events.append('compute-launch')
        raise LaunchWitness()
    monkeypatch.setattr(load('manager').subprocess, 'Popen', refuse_native_launch)
    monkeypatch.setattr(load('manager'), 'remove_runtime', lambda *a: None)
    try:
        try:
            selection.realize()
        except (LaunchWitness, SessionExecutionError):
            pass
        assert events == (['workspace-prepared', 'compute-launch'] if transition == 'none' else ['workspace-prepared']), events
    finally:
        for dispose in disposers:
            dispose()
        load('target_contexts').release_targets(service, owner)
        load('bridge').close_profile_viewer(home)


@pytest.mark.parametrize('transition', ['none', 'config', 'provider'])
def test_real_regular_refusal_retains_and_retries(tmp_path, monkeypatch, transition):
    from hermes_cli.session_execution import SessionExecutionError
    from tools.terminal_targets import register_terminal_target_resolver, select_terminal_target
    home = tmp_path / 'profile'
    monkeypatch.setenv('HERMES_HOME', str(home))
    service = load('integration').get_integration(home)
    owner = service.bind(session_origin='fresh', session_id='regular-owner')
    load('bridge').get_profile_viewer(home)
    monkeypatch.setattr(load('integration'), 'setup_status', lambda **kw: {'ready': True})
    disposers = [register_terminal_target_resolver('regular-late-review', service.terminal_context, selector=service.select_terminal_target)]
    # /run is private tmpfs in the reviewed bwrap runner, not the host /run.
    Path('/run/user/' + str(os.getuid())).mkdir(parents=True, exist_ok=True)
    workspace = load('workspace')
    original = load('manager').atomic_json
    events = []
    def create(path, record):
        result = original(path, record)
        if Path(path).name != 'spec.json':
            return result
        events.append('workspace-prepared')
        if transition == 'config':
            (home / 'config.yaml').write_text('plugins:\n  realms:\n    size: 1280x720\n')
        elif transition == 'provider':
            disposers.append(register_terminal_target_resolver('regular-late-review', service.terminal_context, selector=service.select_terminal_target))
        return result
    class LaunchWitness(Exception):
        pass
    def refuse_native_launch(argv, **kwargs):
        assert argv[0] == 'systemd-run'
        events.append('compute-launch')
        raise LaunchWitness()
    monkeypatch.setattr(load('manager').subprocess, 'Popen', refuse_native_launch)
    monkeypatch.setattr(load('manager'), 'scope_info', lambda _: {'ActiveState': 'inactive'})
    # Seed a retained workspace through the real start/cleanup, intercepting only
    # the launch. This is an explicit failed compute attempt, not fabricated state.
    with pytest.raises(LaunchWitness):
        select_terminal_target('regular-late-review', command='true', session_id=owner).realize()
    kept = service.manager.registry.records()[0]
    assert kept['status'] == 'stopped'
    workspace_path = Path(kept['workspace_dir'])
    marker = workspace_path / 'retained-marker'
    marker.write_bytes(b'keep my work')
    saved = marker.read_bytes(), marker.stat().st_ino, kept['id']
    events.clear()
    monkeypatch.setattr(load('manager'), 'atomic_json', create)
    selection = select_terminal_target('regular-late-review', command='true', session_id=owner)
    try:
        try:
            selection.realize()
        except (LaunchWitness, SessionExecutionError):
            pass
        assert events == (['workspace-prepared', 'compute-launch'] if transition == 'none' else ['workspace-prepared']), events
        kept = service.manager.registry.records()[0]
        assert kept['status'] == 'stopped'
        assert (marker.read_bytes(), marker.stat().st_ino, kept['id']) == saved
        (home / 'config.yaml').unlink(missing_ok=True)
        monkeypatch.setattr(load('manager'), 'atomic_json', original)
        with pytest.raises(LaunchWitness):
            select_terminal_target('regular-late-review', command='true', session_id=owner).realize()
    finally:
        for dispose in disposers:
            dispose()
        load('target_contexts').release_targets(service, owner)
        load('bridge').close_profile_viewer(home)
