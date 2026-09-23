"""Tool/slash dispatch preserves selected paths literally before VM I/O."""
import json
from pathlib import Path
import runpy
import shlex
from types import SimpleNamespace

import pytest
from realms_test_paths import HERMES_ROOT, PLUGIN_ROOT

ROOT = HERMES_ROOT


@pytest.mark.linux_only
@pytest.mark.parametrize("arguments", [
    {"action": "push", "destination": "/guest-copy"},
    {"action": "push", "path": "", "destination": "/guest-copy"},
    {"action": "pull", "destination": "/host-copy"},
    {"action": "pull", "path": "/guest-source"},
])
def test_transfer_requires_selected_path_before_serializing(arguments):
    plugin = runpy.run_path(str(PLUGIN_ROOT / "plugin.py"))
    with pytest.raises(ValueError, match="path|destination"):
        plugin["_raw_command"](arguments)


@pytest.mark.linux_only
@pytest.mark.parametrize("surface", ["tool", "slash"])
@pytest.mark.parametrize("action", ["push", "pull"])
def test_transfer_dispatch_preserves_literal_paths(tmp_path, monkeypatch, surface, action):
    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    plugin = runpy.run_path(str(PLUGIN_ROOT / "plugin.py"))
    service = plugin["get_integration"](home)
    tools, commands, teardown = {}, {}, []
    ctx = SimpleNamespace(
        register_cli_command=lambda *a, **k: None,
        register_tool=lambda name, group, schema, handler, **kw: tools.update({name: handler}),
        register_command=lambda name, handler, **kw: commands.update({name: handler}),
        register_middleware=lambda *a, **kw: None, register_hook=lambda *a, **k: None, register_skill=lambda *a, **k: None,
        on_unload=teardown.append,
    )
    plugin["register"](ctx)
    # A running record is only ever realized after the profile viewer authority
    # exists; establish it as that path does so admission reads a real epoch.
    plugin["_load_runtime"]("bridge").get_profile_viewer(home)
    owner = service.bind(session_origin="fresh", session_id="transfer-parent", task_id="transfer-task")
    service.owners.set_kind(owner, "omarchy-vm")
    record = {"id": "vm-" + "a" * 24, "session_id": owner, "status": "running"}
    seen = []
    def transfer(realm_id, source, destination):
        seen.append((realm_id, source, destination))
        return {"dispatched": True}
    service._vm = SimpleNamespace(config=service.manager.config, list=lambda: [record],
                                  push=transfer, pull=transfer)
    source = "/selected project/O'Brien café [*]\\leaf\n.txt"
    destination = '/test copy/"literal" Ω\\result'
    identity = {"session_id": owner, "task_id": "transfer-task"}
    try:
        result = json.loads(tools["realm"]({"action": action, "path": source, "destination": destination}, **identity)
                            if surface == "tool" else commands["realm"](shlex.join([action, source, destination]), **identity))
        assert result == {"dispatched": True}
        assert seen == [(record["id"], source, destination)]
    finally:
        service._vm = None
        for callback in teardown:
            callback()
