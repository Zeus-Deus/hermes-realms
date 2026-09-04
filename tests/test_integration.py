"""Real profile-local state and native plugin contract tests."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


def test_ownership_is_durable_and_requires_both_identifiers(tmp_path):
    from realms.integration import OwnershipStore, OwnerError

    store = OwnershipStore(tmp_path)
    owner = store.bind(
        session_id="conversation-a",
        stored_session_id="stored-a",
        runtime_session_id="runtime-a",
        task_id="task-a",
    )
    store.bind(
        session_id="conversation-b",
        stored_session_id="stored-b",
        runtime_session_id="runtime-b",
    )
    assert (
        OwnershipStore(tmp_path).resolve(
            runtime_session_id="runtime-a", stored_session_id="stored-a"
        )
        == owner
    )
    with pytest.raises(OwnerError):
        store.resolve(runtime_session_id="runtime-a", stored_session_id="stored-b")
    with pytest.raises(OwnerError):
        store.resolve(runtime_session_id="runtime-a", stored_session_id="unknown")
    with pytest.raises(OwnerError):
        store.bind(session_id="conversation-b", runtime_session_id="runtime-a")
    with pytest.raises(OwnerError):
        store.resolve(runtime_session_id=" runtime-a")
    assert (
        OwnershipStore(tmp_path / "other-profile").resolve(
            runtime_session_id="runtime-a", allow_missing=True
        )
        is None
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            'from realms.integration import OwnershipStore; import sys; print(OwnershipStore(sys.argv[1]).resolve(runtime_session_id="runtime-a", stored_session_id="stored-a"))',
            str(tmp_path),
        ],
        text=True,
        capture_output=True,
        check=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert result.stdout.strip() == owner
    assert (
        store.bind(
            session_id="compressed-a", stored_session_id="stored-a", task_id="next-task"
        )
        == owner
    )
    assert store.resolve(session_id="compressed-a", task_id="next-task") == owner


def test_modes_are_explicit_persisted_and_never_start_for_status(tmp_path, monkeypatch):
    from realms.integration import RealmIntegration

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    service = RealmIntegration(tmp_path)
    identity = dict(session_id="a", stored_session_id="stored-a")
    assert service.command("status", **identity) == {"mode": "realm", "realms": []}
    assert service.manager.list() == []
    assert service.command("off", **identity)["mode"] == "host"
    assert RealmIntegration(tmp_path).command("status", **identity)["mode"] == "host"
    assert (
        service.pre_tool(
            tool_name="terminal", args={}, session_id="a", task_id="task-a"
        )
        is None
    )
    assert service.manager.list() == []  # no host command/input is actually executed
    assert service.command("on", **identity)["mode"] == "realm"
    assert service.manager.list() == []  # on enables lazy creation
    (tmp_path / "config.yaml").write_text(
        "plugins:\n  realms:\n    default_mode: ask\n"
    )
    ask = RealmIntegration(tmp_path)
    blocked = ask.pre_tool(
        tool_name="computer_use", args={}, session_id="b", task_id="task-b"
    )
    assert blocked["action"] == "block"
    assert "/realm on" in blocked["message"] and "/realm off" in blocked["message"]
    assert ask.pre_tool(tool_name="read_file", args={}, session_id="b") is None
    assert ask.manager.list() == []
    assert ask.command("stop", session_id="b")["mode"] == "ask"


def test_pinned_driver_missing_does_not_fall_back_to_host(tmp_path):
    from realms.integration import requirements_available

    assert (
        requirements_available(driver_executable=tmp_path / "missing-driver") is False
    )


def test_on_is_idempotent_and_off_revokes_before_explicit_host_mode(
    tmp_path, monkeypatch
):
    from realms.integration import RealmIntegration
    from hermes_cli.session_execution import resolve_session_execution_context

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    service = RealmIntegration(tmp_path)
    try:
        assert (
            service.pre_tool(tool_name="terminal", args={}, session_id="a", task_id="t")
            is None
        )
        record = service.manager.list()[0]
        lease = resolve_session_execution_context(session_id="a")
        service.command("on", session_id="a")
        assert service.manager.list()[0]["generation"] == record["generation"]
        service.command("off", session_id="a")
        assert service.manager.list() == []
        assert resolve_session_execution_context(session_id="a") is None
        with pytest.raises(Exception):
            lease.check()
        assert (
            service.pre_tool(
                tool_name="computer_use", args={}, session_id="a", task_id="t"
            )
            is None
        )
        assert service.manager.list() == []  # no host input dispatched
    finally:
        service.unload()
        for record in service.manager.list():
            service.manager.stop(record["id"])


@pytest.mark.parametrize("cached", [False, True])
def test_launcher_tamper_blocks_initial_or_cached_execution(
    tmp_path, monkeypatch, cached
):
    from realms.integration import RealmIntegration
    from hermes_cli.session_execution import resolve_session_execution_context

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    service = RealmIntegration(tmp_path)
    try:
        if cached:
            assert (
                service.pre_tool(tool_name="terminal", args={}, session_id="tamper")
                is None
            )
            record = service.manager.list()[0]
            lease = resolve_session_execution_context(session_id="tamper")
        else:
            record = service.manager.start("tamper")
        launcher = Path(record["runtime_dir"]) / "cua-contained"
        launcher.write_text("#!/bin/sh\nexit 99\n")
        launcher.chmod(0o700)
        result = service.pre_tool(tool_name="terminal", args={}, session_id="tamper")
        assert result is not None and result["action"] == "block"
        if cached:
            with pytest.raises(Exception):
                lease.check()
    finally:
        service.stop("tamper")
        service.unload()


def install_plugin(home):
    root = Path(__file__).resolve().parents[1]
    target = home / "plugins" / "hermes-realms"
    target.mkdir(parents=True)
    for name in ("__init__.py", "plugin.py", "plugin.yaml", "skills", "dashboard"):
        (target / name).symlink_to(root / name)
    (home / "config.yaml").write_text(
        "approvals:\n  mode: manual\nplugins:\n  enabled: [hermes-realms]\n"
    )
    return target


def test_directory_install_loads_without_repo_pythonpath(tmp_path):
    import hermes_cli.plugins
    import json

    install_plugin(tmp_path)
    env = dict(
        os.environ,
        HERMES_HOME=str(tmp_path),
        PYTHONPATH=str(Path(hermes_cli.plugins.__file__).resolve().parents[1]),
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            'from hermes_cli.plugins import discover_plugins,get_plugin_command_handler; discover_plugins(); h=get_plugin_command_handler("realm"); assert h is not None; print(h("status",session_id="standalone"))',
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["mode"] == "realm"


def test_native_discovery_slash_tool_skill_and_dependency_gate(tmp_path, monkeypatch):
    import json
    from hermes_cli.plugins import (
        discover_plugins,
        get_plugin_command_handler,
        get_plugin_manager,
    )
    from hermes_cli.plugins_command import invoke_plugin_command
    from tools.registry import registry

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    install_plugin(tmp_path)
    discover_plugins()
    handler = get_plugin_command_handler("realm")
    assert callable(handler), "realm slash must be discovered from standalone package"
    result = invoke_plugin_command(
        handler,
        "on",
        session_id="before-first-turn",
        task_id=None,
        stored_session_id="stored",
        hermes_home=tmp_path,
    )
    assert json.loads(result)["mode"] == "realm"
    entry = registry.get_entry("realm")
    assert entry is not None and entry.check_fn() is True
    result = json.loads(
        registry.dispatch("realm", {"action": "status"}, session_id="before-first-turn")
    )
    assert result["mode"] == "realm" and result["realms"] == []
    skill = get_plugin_manager()._plugin_skills["hermes-realms:realms"]
    assert skill["path"].is_file()
    monkeypatch.setenv("PATH", str(tmp_path / "no-dependencies"))
    assert entry.check_fn() is False
    from hermes_cli.plugins import get_pre_tool_call_directive

    action, message = get_pre_tool_call_directive(
        "terminal", {}, session_id="before-first-turn", task_id="t"
    )
    assert action == "block" and message


def test_lazy_native_hook_routes_real_terminal_and_finalizes_only_at_boundary(
    tmp_path, monkeypatch
):
    import json
    import shlex
    from realms.integration import get_integration
    from hermes_cli.plugins import (
        discover_plugins,
        get_pre_tool_call_directive,
        invoke_hook,
        unload_plugins,
    )
    from hermes_cli.session_execution import resolve_session_execution_context
    from tools.terminal_tool import terminal_tool, set_approval_callback

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "must-not-leak")
    monkeypatch.setenv("YDOTOOL_SOCKET", "/must-not-leak")
    install_plugin(tmp_path)
    discover_plugins()
    service = get_integration(tmp_path)
    set_approval_callback(
        lambda *_args, **_kwargs: "once"
    )  # real approval transport for this test only
    try:
        assert service.manager.list() == []
        action, message = get_pre_tool_call_directive(
            "terminal", {}, session_id="live-a", task_id="task-a"
        )
        assert action is None, message
        record = service.manager.list()[0]
        lease = resolve_session_execution_context(session_id="live-a", task_id="task-a")
        assert lease is not None
        assert "HYPRLAND_INSTANCE_SIGNATURE" in lease.context.env_unset
        assert "AT_SPI_BUS_ADDRESS" in lease.context.env_set
        with pytest.raises(TypeError):
            lease.context.env_set["DISPLAY"] = ":0"
        code = 'import os,json;print("PROBE="+json.dumps({k:os.getenv(k) for k in ["XDG_RUNTIME_DIR","DBUS_SESSION_BUS_ADDRESS","HYPRLAND_INSTANCE_SIGNATURE","YDOTOOL_SOCKET"]}));print(open("/proc/self/cgroup").read())'
        result = json.loads(
            terminal_tool(
                f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}",
                task_id="task-a",
                session_id="live-a",
                workdir=str(tmp_path),
            )
        )
        assert result["exit_code"] == 0, result
        assert (
            record["runtime_dir"] in result["output"]
            and record["scope"] in result["output"]
        )
        assert "must-not-leak" not in result["output"]
        assert (
            get_pre_tool_call_directive(
                "terminal", {}, session_id="live-a", task_id="task-a"
            )[0]
            is None
        )
        assert resolve_session_execution_context(session_id="live-a") is lease
        invoke_hook("on_session_end", session_id="live-a")
        assert service.manager.list()[0]["id"] == record["id"]
        assert (
            service.command("size 800x600", session_id="live-a")["realms"][0]["size"]
            == "800x600"
        )
        invoke_hook("on_session_finalize", session_id="live-a")
        assert service.manager.list() == []
        assert resolve_session_execution_context(session_id="live-a") is None
        with pytest.raises(Exception):
            lease.check()
    finally:
        set_approval_callback(None)
        for record in service.manager.list():
            service.manager.stop(record["id"])
        unload_plugins()
