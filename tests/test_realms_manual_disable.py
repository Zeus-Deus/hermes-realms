"""An explicit session Disable outranks automatic agent activation."""
import json
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest
from realms_test_paths import PLUGIN_ROOT

PLUGIN = PLUGIN_ROOT


@pytest.mark.linux_only
@pytest.mark.parametrize("kind", ["realm", "omarchy-vm"])
def test_registered_agent_cannot_clear_disable_but_manual_reenable_can(tmp_path, monkeypatch, kind):
    home = tmp_path / "profile"
    home.mkdir()
    # An unset owner mode must still allow natural-language selection even if
    # the profile default is host. It is not an explicit session Disable.
    (home / "config.yaml").write_text("plugins:\n  realms:\n    default_mode: host\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    plugin = runpy.run_path(str(PLUGIN / "plugin.py"))
    integration = plugin["_integration"]
    service = plugin["get_integration"](home)
    commands, tools, teardown, starts = {}, {}, [], []
    ctx = SimpleNamespace(
        register_cli_command=lambda *a, **kw: None,
        register_tool=lambda name, group, schema, handler, **kw: tools.update({name: handler}),
        register_command=lambda name, handler, **kw: commands.update({name: handler}),
        register_middleware=lambda *a, **kw: None, register_hook=lambda *a, **kw: None,
        register_skill=lambda *a, **kw: None,
        on_unload=teardown.append,
    )
    plugin["register"](ctx)
    monkeypatch.setattr(integration, "setup_status", lambda **kw: {"ready": True})
    monkeypatch.setattr(integration, "vm_setup_status", lambda *a: {"ready": True})
    monkeypatch.setattr(service, "ready", lambda owner: starts.append(owner))
    monkeypatch.setattr(service, "status", lambda owner: {
        "mode": service.owners.mode(owner, service.manager.config.default_mode),
        "kind": service.kind(owner),
    })
    owner = service.bind(session_origin="fresh", session_id="disabled-owner")
    peer = service.bind(session_origin="fresh", session_id="ordinary-peer")
    try:
        assert json.loads(commands["realm"]("off", session_id=owner))["mode"] == "host"
        generation = service.owners.setup_generation(owner)
        result = json.loads(tools["realm"](
            {"action": "on", "kind": kind}, session_id=owner, _agent=False,
        ))
        assert "disabled" in result.get("error", "").lower(), result
        assert starts == []
        reopened = integration.OwnershipStore(home, readonly=True)
        assert reopened.mode(owner, None) == "host"
        assert reopened.setup_generation(owner) == generation
        assert reopened.requested_kind(owner) is None
        for name in ("terminal", "read_file", "clarify"):
            assert service.pre_tool(tool_name=name, args={}, session_id=owner) is None
        assert json.loads(tools["realm"]({"action": "on", "kind": "realm"}, session_id=peer))["mode"] == "realm"
        assert reopened.mode(owner, None) == "host"
        assert json.loads(commands["realm"]("on " + kind, session_id=owner))["mode"] == "realm"
        assert reopened.kind(owner, None) == kind
        assert not json.loads(tools["realm"]({"action": "on", "kind": kind}, session_id=owner)).get("error")
    finally:
        for callback in teardown:
            callback()
