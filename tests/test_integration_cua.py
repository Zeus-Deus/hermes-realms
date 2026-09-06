"""Pinned real Cua capture through the plugin, no host input."""

import importlib
import json

from test_integration import install_plugin


def test_real_cua_capture_uses_private_runtime_and_preserves_standard_approval(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    install_plugin(tmp_path)
    with (tmp_path / "config.yaml").open("a") as stream:
        stream.write(
            "model:\n  supports_vision: true\ncomputer_use:\n  permission_mode: standard\n"
        )
    from hermes_cli.plugins import (
        discover_plugins,
        get_pre_tool_call_directive,
        get_plugin_manager,
        unload_plugins,
    )
    from hermes_cli.session_execution import resolve_session_execution_context
    from tools.computer_use.tool import handle_computer_use, set_approval_callback

    discover_plugins()
    # Observe the service registered with the host, not a separate library import.
    native = get_plugin_manager()._plugins["hermes-realms"].module
    assert native is not None
    plugin = importlib.import_module(native.__name__ + ".plugin")
    service = plugin.get_integration(tmp_path)
    bridge = importlib.import_module(type(service).__module__.rsplit(".", 1)[0] + ".bridge")
    try:
        action, message = get_pre_tool_call_directive(
            "computer_use", {}, session_id="cua-a", task_id="t-a"
        )
        assert action is None, message
        lease = resolve_session_execution_context(session_id="cua-a")
        realm = service.manager.list()[0]
        assert lease.context.computer_use.runtime_dir == realm["runtime_dir"]
        result = handle_computer_use(
            {"action": "capture", "app": "screen", "mode": "vision"}, session_id="cua-a"
        )
        assert isinstance(result, dict) and result.get("_multimodal") is True, result
        assert any(row.get("type") == "image_url" for row in result["content"]), result
        from tools.computer_use import tool

        assert tool._backend_permission_modes["cua-a"] == "standard"
        set_approval_callback(lambda *_: "deny")
        result = json.loads(
            handle_computer_use(
                {"action": "click", "app": "screen", "coordinate": [20, 20]},
                session_id="cua-a",
            )
        )
        assert result["error"] == "denied by user"
        from urllib.parse import urlsplit, parse_qs
        from websockets.sync.client import connect

        parts = urlsplit(service.command("watch", session_id="cua-a")["url"])
        ticket = parse_qs(parts.fragment)["ticket"][0]
        with connect(
            "ws://" + parts.netloc + "/api/realms/" + realm["id"] + "/vnc?control=1",
            origin=parts.scheme + "://" + parts.netloc,
            subprotocols=["binary", "realm." + ticket],
        ) as ws:
            assert ws.recv(timeout=3).startswith(b"RFB ")
            set_approval_callback(lambda *_: "approve_once")
            result = json.loads(
                handle_computer_use(
                    {
                        "action": "click",
                        "app": "screen",
                        "coordinate": [20, 20],
                        "delivery_mode": "foreground",
                    },
                    session_id="cua-a",
                )
            )
            assert (
                result["ok"] is False
                and "input paused by session owner" in result["message"]
            )
        viewer = bridge.get_profile_viewer(tmp_path)
        assert viewer.origin == parts.scheme + "://" + parts.netloc
        assert viewer._thread is not None and viewer._thread.is_alive()
        unload_plugins()
        assert viewer._thread is None
        assert service.manager.list() == []
    finally:
        set_approval_callback(None)
        unload_plugins()
        for realm in service.manager.list():
            service.manager.stop(realm["id"])
