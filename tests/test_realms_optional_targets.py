"""Ordinary conversations must not depend on an optional desktop's health."""
from pathlib import Path
import runpy

import pytest
from realms_test_paths import PLUGIN_ROOT

PLUGIN = PLUGIN_ROOT


def load(name):
    return runpy.run_path(str(PLUGIN / "realms/_binding.py"))["load_runtime"](name)


@pytest.mark.linux_only
@pytest.mark.parametrize("tool_name", ["terminal", "read_file", "clarify", "execute_code"])
def test_fresh_conversation_ordinary_tools_do_not_prepare_a_realm(tmp_path, monkeypatch, tool_name):
    home = tmp_path / "agent-home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    service = load("integration").RealmIntegration(home)
    owner = service.bind(session_origin="fresh", session_id="ordinary-conversation", task_id="ordinary-task")

    def unexpected_start(*args, **kwargs):
        pytest.fail("an ordinary tool tried to prepare a private target")

    monkeypatch.setattr(service, "ready", unexpected_start)
    result = service.pre_tool(
        tool_name=tool_name,
        args={"command": "printf ordinary", "path": "project.txt"},
        session_id=owner,
        task_id="ordinary-task",
    )
    assert result is None
    from hermes_cli.session_execution import resolve_session_execution_context
    assert resolve_session_execution_context(session_id=owner, task_id="ordinary-task") is None
    assert service._vm is None


@pytest.mark.linux_only
def test_starting_a_private_desktop_does_not_retarget_parent_tools(tmp_path, tmp_path_factory, monkeypatch):
    import socket
    from hermes_cli.session_execution import (
        remove_session_execution_context, resolve_session_execution_context,
    )

    home = tmp_path / "agent-home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    integration = load("integration")
    service = integration.RealmIntegration(home)
    owner = service.bind(session_origin="fresh", session_id="parent", task_id="parent-task")
    runtime = tmp_path_factory.mktemp("rt")
    runtime.chmod(0o700)
    record = {"id": "r-test", "generation": "e" * 32, "session_id": owner,
              "runtime_dir": str(runtime), "overlay": False, "cursor_theme": "cua.default"}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as display:
        # Bind relative to the owned fixture: long canonical test homes must not
        # exceed sockaddr_un solely because of the runner's evidence location.
        monkeypatch.chdir(runtime)
        display.bind("wayland-test")
        monkeypatch.setattr(integration, "setup_status", lambda **kw: {"ready": True})
        monkeypatch.setattr(service.manager, "start", lambda selected_owner: record)
        monkeypatch.setattr(service.manager, "env", lambda selected_id: {
            "XDG_RUNTIME_DIR": str(runtime), "WAYLAND_DISPLAY": "wayland-test",
            "CUA_DRIVER_RS_ENABLE_WAYLAND": "1",
        })
        monkeypatch.setattr(load("driver"), "create_driver_launcher", lambda *args: "/bin/true")
        monkeypatch.setattr(service, "_valid", lambda *args: True)
        try:
            assert service.ready(owner)["id"] == record["id"]
            assert resolve_session_execution_context(session_id=owner, task_id="parent-task") is None
            cua = service.computer_use_context(session_id=owner, task_id="parent-task")
            assert cua.session_id != owner
            assert cua.context.computer_use.desktop_only
            assert resolve_session_execution_context(session_id=owner, task_id="parent-task") is None
        finally:
            load("target_contexts").release_targets(service, owner)
            remove_session_execution_context(owner)


@pytest.mark.linux_only
def test_plugin_exposes_management_and_cua_before_driver_setup(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from tools.computer_use import tool as cua_tool
    from tools.computer_use import cua_backend_driver

    home = tmp_path / "agent-home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(cua_backend_driver, "cua_driver_binary_available", lambda: False)
    plugin = runpy.run_path(str(PLUGIN / "plugin.py"))
    monkeypatch.setattr(load("integration"), "requirements_available", lambda **kw: False)
    tools, teardown = {}, []
    def register_tool(name, toolset, schema, handler, **kw):
        tools[name] = kw
    ctx = SimpleNamespace(
        register_cli_command=lambda *a, **k: None, register_tool=register_tool,
        register_command=lambda *a, **k: None, register_middleware=lambda *a, **kw: None, register_hook=lambda *a, **k: None,
        register_skill=lambda *a, **k: None, on_unload=teardown.append,
    )
    try:
        plugin["register"](ctx)
        assert tools["realm"]["check_fn"]()
        assert cua_tool.check_computer_use_requirements()
        service = plugin["get_integration"](home)
        assert service._vm is None
        owner = service.bind(session_origin="fresh", session_id="disabled-parent", task_id="disabled-task")
        service.owners.set_mode(owner, "host")
        from tools.terminal_targets import resolve_terminal_target
        from hermes_cli.session_execution import SessionExecutionError
        with pytest.raises(SessionExecutionError, match="disabled"):
            resolve_terminal_target("realm", command="pwd", session_id=owner, task_id="disabled-task")
    finally:
        for callback in teardown:
            callback()
    assert not cua_tool.check_computer_use_requirements()


@pytest.mark.linux_only
@pytest.mark.parametrize("action", ["off", "stop"])
def test_manual_target_actions_do_not_remove_an_unrelated_parent_context(tmp_path, monkeypatch, action):
    from hermes_cli.session_execution import (
        SessionExecutionContext, register_session_execution_context,
        resolve_session_execution_context, remove_session_execution_context,
    )
    home = tmp_path / "agent-home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    service = load("integration").RealmIntegration(home)
    owner = service.bind(session_origin="fresh", session_id="parent", task_id="parent-task")
    register_session_execution_context(owner, SessionExecutionContext(env_set={"EXISTING_CONTEXT": "kept"}))
    previous = resolve_session_execution_context(session_id=owner)
    try:
        service.command(action, session_id=owner, task_id="parent-task")
        assert resolve_session_execution_context(session_id=owner) is previous
    finally:
        remove_session_execution_context(owner)


@pytest.mark.linux_only
def test_disable_revokes_agent_routes_without_stopping_the_viewed_target(tmp_path, monkeypatch):
    from hermes_cli.session_execution import (
        SessionExecutionContext, SessionExecutionError, register_session_execution_context,
        resolve_session_execution_context, remove_session_execution_context,
    )
    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    service = load("integration").RealmIntegration(home)
    owner = service.bind(session_origin="fresh", session_id="parent", task_id="parent-task")
    stopped = []
    monkeypatch.setattr(service, "_stop", lambda selected: stopped.append(selected))
    attachment = ("generation", (), "private-target")
    service._attachments[owner] = attachment
    register_session_execution_context("private-cua", SessionExecutionContext())
    lease = resolve_session_execution_context(session_id="private-cua")
    assert lease is not None
    service._target_contexts[owner, "realm", "cua"] = ("private-target", "generation", lease)
    try:
        service.command("off", session_id=owner, task_id="parent-task")
        assert stopped == [], "Disable is not Stop; the human viewer must remain usable"
        assert service._attachments[owner] is attachment
        assert service.owners.mode(owner, "realm") == "host"
        with pytest.raises(SessionExecutionError):
            lease.check()
        assert service.pre_tool(tool_name="terminal", args={"command": "true"},
                                session_id=owner, task_id="parent-task") is None
    finally:
        remove_session_execution_context("private-cua")
