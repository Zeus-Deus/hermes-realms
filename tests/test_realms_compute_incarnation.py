"""A durable VM workspace must not preserve a dead compute lease."""
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest
from realms_test_paths import PLUGIN_ROOT

PLUGIN = PLUGIN_ROOT


@pytest.fixture
def vm_target(tmp_path, monkeypatch):
    from hermes_cli.session_execution import SessionExecutionContext

    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    plugin = runpy.run_path(str(PLUGIN / "plugin.py"))
    service = plugin["get_integration"](home)
    contexts = plugin["_load_runtime"]("target_contexts")
    owner = service.bind(session_origin="fresh", session_id="parent", task_id="parent-task")
    service.owners.set_kind(owner, "omarchy-vm")
    current = {
        "id": "vm-" + "a" * 24, "generation": "b" * 32,
        "compute_generation": "c" * 32, "invocation_id": "d" * 32,
        "session_id": owner, "status": "running",
    }
    service._vm = SimpleNamespace(config=service.manager.config, validate=lambda _: dict(current))
    monkeypatch.setattr(service, "ready", lambda _: dict(current))
    monkeypatch.setitem(contexts._BUILDERS, "omarchy-vm", lambda svc, who, record, purpose:
        SessionExecutionContext(
            env_set={"COMPUTE": record["compute_generation"]},
            validate=lambda: svc._vm_valid(record, who),
        ))
    try:
        yield service, contexts, owner, current
    finally:
        contexts.release_targets(service, owner)
        service._attachments.clear()


@pytest.mark.linux_only
@pytest.mark.parametrize("purpose", ["terminal", "cua"])
@pytest.mark.parametrize("changed", ["compute_generation", "invocation_id"])
def test_old_vm_lease_refuses_changed_compute(vm_target, purpose, changed):
    from hermes_cli.session_execution import SessionExecutionError

    service, contexts, owner, current = vm_target
    old = contexts.context_for(service, owner, purpose)
    old.check()
    current[changed] = "e" * 32
    with pytest.raises(SessionExecutionError):
        old.check()


@pytest.mark.linux_only
@pytest.mark.parametrize("purpose", ["terminal", "cua"])
def test_explicit_vm_resolution_replaces_compute_lease(vm_target, purpose):
    from hermes_cli.session_execution import SessionExecutionError

    service, contexts, owner, current = vm_target
    old = contexts.context_for(service, owner, purpose)
    assert contexts.context_for(service, owner, purpose) is old
    current.update(compute_generation="e" * 32, invocation_id="f" * 32)
    replacement = contexts.context_for(service, owner, purpose)
    assert replacement is not old
    replacement.check()
    with pytest.raises(SessionExecutionError):
        old.check()
