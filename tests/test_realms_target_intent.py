"""Setup presentation follows durable target requests, not plugin enablement."""
from pathlib import Path
import runpy

import pytest
from realms_test_paths import HERMES_ROOT, PLUGIN_ROOT

ROOT = HERMES_ROOT


@pytest.mark.linux_only
@pytest.mark.parametrize("requested", ["realm", "omarchy-vm"])
def test_target_request_survives_failed_setup_without_allocating_or_retargeting(tmp_path, monkeypatch, requested):
    load = runpy.run_path(str(PLUGIN_ROOT / "realms/_binding.py"))["load_runtime"]
    integration = load("integration")
    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    service = integration.RealmIntegration(home)
    owner = service.bind(session_origin="fresh", session_id="parent", task_id="task")
    missing = {"ready": False, "message": "fixture setup required"}
    monkeypatch.setattr(integration, "setup_status", lambda **kw: missing)
    monkeypatch.setattr(integration, "vm_setup_status", lambda *args: missing)
    assert service.status(owner)["requested"] is False
    service.pre_tool(tool_name="terminal", args={"command": "true"}, session_id=owner, task_id="task")
    assert service.status(owner)["requested"] is False
    with pytest.raises(integration.SetupError, match="fixture setup required"):
        service.command("on " + requested, session_id=owner, task_id="task")
    status = service.status(owner)
    assert status["requested"] is True
    assert status["requested_kind"] == requested
    assert status["kind"] == "realm", "failed selection must not replace an existing kind"
    assert service._vm is None
    assert service.manager.registry.records() == []
    reopened = integration.RealmIntegration(home)
    assert reopened.status(owner)["requested_kind"] == requested
    other = reopened.bind(session_origin="fresh", session_id="another-parent")
    assert reopened.status(other)["requested"] is False
    from hermes_cli.session_execution import resolve_session_execution_context
    assert resolve_session_execution_context(session_id=owner, task_id="task") is None


@pytest.mark.linux_only
@pytest.mark.parametrize("identity", [{}, {"stored_session_id": "historical"}])
def test_unbound_status_does_not_imply_a_target_request(tmp_path, monkeypatch, identity):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
    api = runpy.run_path(str(PLUGIN_ROOT / "dashboard/plugin_api.py"))
    result = api["list_realms"](**identity)
    assert result["requested"] is False
    assert result["requested_kind"] is None
    assert result["realms"] == []
    with api["get_integration"]().owners.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM owners").fetchone()[0] == 0
