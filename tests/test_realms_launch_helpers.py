"""Admission through the real helper bodies, with inert subprocess witnesses."""
from pathlib import Path
import subprocess

import pytest
from test_realms_vm_retention import owned, fresh, load

pytestmark = pytest.mark.linux_only


@pytest.mark.parametrize('stage', ['owner-receipt', 'script-env'])
@pytest.mark.parametrize('state', ['cold', 'stopped'])
@pytest.mark.parametrize('transition', ['none', 'config', 'manager', 'provider', 'control'])
def test_final_helper_admission(owned, monkeypatch, stage, state, transition):
    from hermes_cli.session_execution import SessionExecutionError
    from tools.terminal_targets import register_terminal_target_resolver, select_terminal_target
    manager, _, _ = owned
    fresh(owned, monkeypatch, start=False)
    monkeypatch.setenv('HERMES_HOME', str(manager.home))
    service = load('integration').get_integration(manager.home)
    service._vm = manager
    owner = service.bind(session_origin='fresh', session_id='retention-owner')
    service.owners.set_kind(owner, 'omarchy-vm')
    if state == 'stopped':
        initial = manager.start(owner)
        manager.stop(initial['id'])
    load('bridge').get_profile_viewer(manager.home)
    monkeypatch.setattr(load('integration'), 'vm_setup_status', lambda *_: {'ready': True})
    dispose = register_terminal_target_resolver('helper-test', service.terminal_context, selector=service.select_terminal_target)
    selected = select_terminal_target('helper-test', command='true', session_id=owner)
    lifetime = load('vm_owner_lifetime')
    vm = load('vm_manager')
    monkeypatch.setattr(manager, '_launch', vm.VmManager._launch.__get__(manager))
    events = []
    disposers = [dispose]
    def change():
        events.append('prepared')
        if transition == 'config':
            (manager.home / 'config.yaml').write_text('plugins:\n  realms:\n    size: 1280x720\n')
        elif transition == 'manager':
            from dataclasses import replace
            manager.config = replace(manager.config, size='1280x720')
        elif transition == 'provider':
            disposers.append(register_terminal_target_resolver('helper-test', service.terminal_context, selector=service.select_terminal_target))
        elif transition == 'control':
            authority = load('viewer_state').ControlAuthority(manager.home / 'realms/viewer')
            record = manager.registry.records()[0]
            assert authority.acquire(record['id'], record['generation'], 'human')
            authority.release(record['id'], 'human')
    if stage == 'owner-receipt':
        monkeypatch.setattr(lifetime, 'scope_info', lambda _: {'LoadState': 'not-found', 'ActiveState': 'inactive'})
        original = lifetime.atomic_json
        def publish(path, value):
            result = original(path, value)
            if Path(path).name == 'owner.json':
                change()
            return result
        monkeypatch.setattr(lifetime, 'atomic_json', publish)
    else:
        # This case independently tests the vendor boundary after ownership
        # preparation; the preceding case exercises the real ownership helper.
        monkeypatch.setattr(lifetime, 'install', lambda *a, **kw: None)
        original = manager._script_env
        def env(**kwargs):
            result = original(**kwargs)
            change()
            return result
        monkeypatch.setattr(manager, '_script_env', env)
    class Witness(Exception):
        pass
    def run(argv, **kwargs):
        events.append('launch')
        raise Witness()
    monkeypatch.setattr(subprocess, 'run', run)
    # Retirement would otherwise issue control-plane commands after our witness.
    try:
        with pytest.raises((SessionExecutionError, Witness)):
            selected.realize()
        assert events == (['prepared', 'launch'] if transition == 'none' else ['prepared'])
    finally:
        for dispose in disposers:
            dispose()
        load('bridge').close_profile_viewer(manager.home)
