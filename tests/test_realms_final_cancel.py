"""Cancelled startup preserves recoverable work and operation cleanup."""
from pathlib import Path

import pytest
from test_realms_vm_retention import owned, fresh, load

pytestmark = pytest.mark.platforms("linux")


def test_cancel_at_final_admission_retains_clone_and_allows_new_operation(owned, monkeypatch):
    from hermes_cli.session_execution import SessionExecutionContext
    manager, _, compute = owned
    fresh(owned, monkeypatch, start=False)
    monkeypatch.setenv('HERMES_HOME', str(manager.home))
    service = load('integration').get_integration(manager.home)
    service._vm = manager
    owner = service.bind(session_origin='fresh', session_id='retention-owner')
    service.owners.set_kind(owner, 'omarchy-vm')
    load('bridge').get_profile_viewer(manager.home)
    monkeypatch.setattr(load('integration'), 'vm_setup_status', lambda *_: {'ready': True})
    monkeypatch.setitem(load('target_contexts')._BUILDERS, 'omarchy-vm', lambda *a: SessionExecutionContext())
    selected = service.select_terminal_target(command='true', session_id=owner)
    def cancel():
        if manager.registry.records():
            raise KeyboardInterrupt('cancel at final admission')
    try:
        with pytest.raises(KeyboardInterrupt):
            selected.realize(before_start=cancel)
        record = manager.registry.records()[0]
        assert record['status'] == 'stopped' and record['launch_pending'] is True
        assert not compute['active']
        disk = Path(record['session_dir']) / 'disk.qcow2'
        saved = disk.read_bytes(), disk.stat().st_ino, record['id']
        # Keep the traceback/selection alive while exercising the next operation.
        service.select_terminal_target(command='true', session_id=owner).realize().check()
        current = manager.registry.records()[0]
        assert 'launch_pending' not in current
        assert (disk.read_bytes(), disk.stat().st_ino, current['id']) == saved
        assert load('vm_workspace').validate(current)
    finally:
        load('target_contexts').release_targets(service, owner)
        load('bridge').close_profile_viewer(manager.home)


@pytest.mark.parametrize('resume', [False, True])
def test_guardian_failure_before_boot_retains_a_resumable_clone(owned, monkeypatch, resume):
    """The owner guardian is armed before QEMU starts. If arming fails, the
    clone never booted: it must stay resumable, not demand recovery."""
    import subprocess
    manager, _, compute = owned
    fresh(owned, monkeypatch, start=False)
    fake_launch = manager._launch
    monkeypatch.delattr(manager, '_launch')
    def guardian_failed(record, **kwargs):
        raise subprocess.CalledProcessError(1, ['systemd-run'])
    monkeypatch.setattr(load('vm_owner_lifetime'), 'install', guardian_failed)
    if resume:
        # A never-booted retained clone fails again on its next attempt.
        def cancel(**prepared):
            if prepared:
                raise KeyboardInterrupt('cancel at final admission')
        with pytest.raises(KeyboardInterrupt):
            manager.start('retention-owner', before_start=cancel)
        assert manager.registry.records()[0]['launch_pending'] is True
    with pytest.raises(subprocess.CalledProcessError):
        manager.start('retention-owner')
    record = manager.registry.records()[0]
    assert record['status'] == 'stopped' and record['launch_pending'] is True
    assert 'recovery_reason' not in record and not compute['active']
    disk = Path(record['session_dir']) / 'disk.qcow2'
    saved = disk.read_bytes(), disk.stat().st_ino, record['id']

    monkeypatch.setattr(manager, '_launch', fake_launch)
    monkeypatch.setattr(load('vm_owner_lifetime'), 'validate_owner', lambda r: None)
    resumed = manager.start('retention-owner')
    assert resumed['status'] == 'running' and 'launch_pending' not in resumed
    assert (disk.read_bytes(), disk.stat().st_ino, resumed['id']) == saved
    assert load('vm_workspace').validate(resumed)


def test_guardian_failure_after_an_enrolled_boot_is_not_treated_as_unlaunched(owned, monkeypatch):
    """A workspace that has booted before keeps its strict pin requirement."""
    import subprocess
    manager, _, compute = owned
    record = fresh(owned, monkeypatch)
    manager.stop(record['id'])
    monkeypatch.delattr(manager, '_launch')
    def guardian_failed(record, **kwargs):
        raise subprocess.CalledProcessError(1, ['systemd-run'])
    monkeypatch.setattr(load('vm_owner_lifetime'), 'install', guardian_failed)
    with pytest.raises(subprocess.CalledProcessError):
        manager.start('retention-owner')
    current = manager.registry.records()[0]
    assert current['status'] == 'stopped' and 'launch_pending' not in current
    assert load('vm_workspace').validate(current)
