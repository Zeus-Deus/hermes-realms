"""Disabled target actions stop at registered dispatch, not the I/O endpoint."""
import json
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest
from realms_test_paths import HERMES_ROOT, PLUGIN_ROOT

ROOT = HERMES_ROOT


@pytest.mark.linux_only
@pytest.mark.parametrize("mode", ["host", "ask"])
@pytest.mark.parametrize("arguments,kind,method", [
    ({"action": "size", "size": "1024x768"}, "realm", "resize"),
    ({"action": "push", "path": "selected", "destination": "guest-copy"}, "omarchy-vm", "push"),
    ({"action": "pull", "path": "guest-copy", "destination": "export"}, "omarchy-vm", "pull"),
])
def test_disabled_action_refuses_before_io_but_keeps_observation(tmp_path, monkeypatch, mode, arguments, kind, method):
    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    plugin = runpy.run_path(str(PLUGIN_ROOT / "plugin.py"))
    service = plugin["get_integration"](home)
    tools, teardown, effects = {}, [], []
    context = SimpleNamespace(
        register_cli_command=lambda *a, **k: None,
        register_tool=lambda name, group, schema, handler, **kw: tools.update({name: handler}),
        register_command=lambda *a, **k: None,
        register_middleware=lambda *a, **kw: None, register_hook=lambda *a, **k: None,
        register_skill=lambda *a, **k: None,
        on_unload=teardown.append,
    )
    plugin["register"](context)
    # A running record is only ever realized after the profile viewer authority
    # exists; establish it as that path does so admission reads a real epoch.
    plugin["_load_runtime"]("bridge").get_profile_viewer(home)
    owner = service.bind(session_origin="fresh", session_id="action-owner")
    service.owners.set_kind(owner, kind)
    service.owners.set_mode(owner, mode)
    record = {"id": "r-" + "a" * 24, "generation": "b" * 32,
              "session_id": owner, "status": "running", "size": "800x600"}

    def execute(*args):
        effects.append((method, args))
        return {"dispatched": method}

    try:
        # Only resource endpoints are substituted. Mode persistence and the
        # actual registered tool/slash dispatcher exercise the admission path.
        with monkeypatch.context() as scoped:
            scoped.setattr(service, "records", lambda who: [record] if kind == "realm" else [])
            scoped.setattr(service, "vm_records", lambda who: [record] if kind == "omarchy-vm" else [])
            scoped.setattr(service, "status", lambda who: {"mode": service.owners.mode(who, "realm")})
            scoped.setattr(service, "watch", lambda who, rid: {"view_only": True, "id": rid})
            scoped.setattr(service.manager, "resize", execute)
            service._vm = SimpleNamespace(config=service.manager.config, push=execute, pull=execute)
            result = json.loads(tools["realm"](arguments, session_id=owner))
            assert "disabled" in result.get("error", "").lower(), result
            assert effects == []
            assert json.loads(tools["realm"]({"action": "status"}, session_id=owner)) == {"mode": mode}
            assert json.loads(tools["realm"]({"action": "watch"}, session_id=owner))["view_only"]
            assert record["status"] == "running"
            service.owners.set_mode(owner, "realm")
            result = json.loads(tools["realm"](arguments, session_id=owner))
            assert not result.get("error"), result
            assert len(effects) == 1
    finally:
        service._vm = None
        for callback in teardown:
            callback()
