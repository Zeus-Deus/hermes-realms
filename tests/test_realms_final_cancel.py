"""Cancelled startup preserves recoverable work and operation cleanup."""
from pathlib import Path

import pytest
from test_realms_vm_retention import owned, fresh, load

pytestmark = pytest.mark.linux_only


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
