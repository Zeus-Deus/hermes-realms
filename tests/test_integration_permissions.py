"""Permission acceptance through real discovery, agent dispatch and private Cua.

No model requests, live profile edits, host captures, or host input. Hermes has
manual/smart/off approval modes, not a built-in read-only mode: the read-only
case is an explicit deny-input approval callback at the real host boundary.
"""

from contextlib import contextmanager
import json
from pathlib import Path
import time
from unittest.mock import patch

import pytest

from test_integration import install_plugin

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.e2e


@contextmanager
def permission_session(tmp_path, monkeypatch, *, mode="standard", manifest=None):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    install_plugin(tmp_path)
    with (tmp_path / "config.yaml").open("a") as stream:
        stream.write(
            "model:\n  supports_vision: true\n  context_length: 64000\ncomputer_use:\n"
        )
        stream.write(f"  permission_mode: {mode}\n")
        if manifest is not None:
            stream.write(f"  capability_manifest: {json.dumps(str(manifest))}\n")
    from hermes_cli.plugins import discover_plugins, unload_plugins
    from hermes_cli.session_hook_context import hook_profile_scope
    from realms.integration import get_integration
    from run_agent import AIAgent
    from agent.agent_runtime_helpers import invoke_tool
    from tools.computer_use import tool

    discover_plugins()
    service = get_integration(tmp_path)
    sid = "permissions-" + tmp_path.name
    # Only external model/tool discovery is disabled; agent construction,
    # profile identity, plugin hooks, registry, approvals and driver are real.
    with (
        hook_profile_scope(tmp_path),
        patch("model_tools.get_tool_definitions", return_value=[]),
        patch("model_tools.check_toolset_requirements", return_value={}),
    ):
        agent = AIAgent(
            model="test-model",
            api_key="test-key",
            provider="custom",
            base_url="http://127.0.0.1:1/v1",
            enabled_toolsets=[],
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            skip_background_review=True,
            session_id=sid,
        )

    def dispatch(**args):
        with hook_profile_scope(tmp_path):
            return invoke_tool(agent, "computer_use", args, "task-" + sid)

    try:
        yield service, sid, dispatch, tool
    finally:
        tool.set_approval_callback(None)
        with hook_profile_scope(tmp_path):
            tool.release_computer_use_session(sid)
            unload_plugins()
            service.unload()
        agent.close()
        for realm in service.manager.list():
            service.manager.stop(realm["id"])


def wait_state(path, *, after_tick=0):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if path.exists():
            state = json.loads(path.read_text())
            if state["tick"] > after_tick:
                return state
        time.sleep(0.05)
    pytest.fail("private GTK probe did not advance its real event loop")


def start_probe(service, sid, tmp_path):
    from hermes_cli.plugins import get_pre_tool_call_directive
    from hermes_cli.session_execution import resolve_session_execution_context

    action, message = get_pre_tool_call_directive(
        "computer_use", {}, session_id=sid, task_id="task-" + sid
    )
    assert action is None, message
    realm = service.manager.list()[0]
    lease = resolve_session_execution_context(session_id=sid)
    assert lease.context.computer_use.runtime_dir == realm["runtime_dir"]
    assert lease.context.computer_use.private_daemon is True
    state = tmp_path / "gtk-state.json"
    service.manager.exec(
        realm["id"],
        [
            "/usr/bin/python3",
            str(ROOT / "tests/fixtures/gtk_probe.py"),
            str(state),
            "Permissions",
        ],
        cwd=ROOT,
    )
    wait_state(state)
    return realm, state


def assert_capture(result):
    assert isinstance(result, dict) and result.get("_multimodal") is True, result
    assert any(row.get("type") == "image_url" for row in result["content"])


def assert_unchanged(path, before):
    after = wait_state(path, after_tick=before["tick"] + 1)
    assert after["text"] == before["text"] == ""
    assert after["clicks"] == before["clicks"] == 0


def test_read_only_approval_blocks_agent_input_before_backend_start(
    tmp_path, monkeypatch
):
    with permission_session(tmp_path, monkeypatch) as (service, sid, dispatch, tool):
        realm, state = start_probe(service, sid, tmp_path)
        before = wait_state(state)
        approvals = []

        def read_only_approval(action, args, summary):
            approvals.append(action)
            assert sid not in tool._backends
            return "deny"

        tool.set_approval_callback(read_only_approval)
        for args in (
            {"action": "type", "text": "must-not-arrive"},
            {"action": "click", "coordinate": [100, 100]},
        ):
            result = json.loads(dispatch(app="screen", **args))
            assert result["error"] == "denied by user", result
        assert approvals == ["type", "click"]
        assert sid not in tool._backends
        assert not list(Path(realm["runtime_dir"]).glob("hc-*.sock"))
        assert_unchanged(state, before)
        # Reads remain usable with the same deny-input policy installed.
        assert_capture(dispatch(action="capture", app="screen", mode="vision"))
        assert tool._backend_permission_modes[sid] == "standard"
        assert approvals == ["type", "click"]


def test_standard_denied_approval_remains_denied_with_warm_private_driver(
    tmp_path, monkeypatch
):
    with permission_session(tmp_path, monkeypatch) as (service, sid, dispatch, tool):
        _, state = start_probe(service, sid, tmp_path)
        assert_capture(dispatch(action="capture", app="screen", mode="vision"))
        backend = tool._backends[sid]
        daemon = backend._embedded_daemon
        assert backend.permission_mode == daemon.permission_mode == "standard"
        assert daemon._process.poll() is None
        approvals = []
        tool.set_approval_callback(
            lambda action, *_: approvals.append(action) or "deny"
        )
        before = wait_state(state)
        for _ in range(2):
            result = json.loads(
                dispatch(
                    action="type",
                    app="screen",
                    text="must-not-arrive",
                    delivery_mode="foreground",
                )
            )
            assert result["error"] == "denied by user", result
        assert approvals == ["type", "type"]
        assert tool._backends[sid] is backend
        assert tool._backend_permission_modes[sid] == "standard"
        assert daemon._process.poll() is None
        assert_unchanged(state, before)
        assert_capture(dispatch(action="capture", app="screen", mode="vision"))


def test_bounded_manifest_reaches_contained_driver_unchanged_and_denies_input(
    tmp_path, monkeypatch
):
    manifest = tmp_path / "reviewed-capabilities.yaml"
    contents = b"""# Reviewed capture-only ceiling; preserve these exact bytes.
version: 3
expires_after: 10m
idle_timeout: 5m
allow:
  tools: [start_session, end_session, get_desktop_state, get_config, set_config, set_agent_cursor_enabled]
resources:
  desktop:
    display: true
"""
    manifest.write_bytes(contents)
    with permission_session(
        tmp_path, monkeypatch, mode="bounded", manifest=manifest
    ) as (service, sid, dispatch, tool):
        realm, state = start_probe(service, sid, tmp_path)
        assert_capture(dispatch(action="capture", app="screen", mode="vision"))
        backend = tool._backends[sid]
        daemon = backend._embedded_daemon
        assert (
            tool._backend_permission_modes[sid]
            == backend.permission_mode
            == daemon.permission_mode
            == "bounded"
        )
        assert daemon._process.poll() is None
        staged = Path(daemon._staged_manifest)
        assert staged.parent == Path(realm["runtime_dir"])
        assert staged.read_bytes() == manifest.read_bytes() == contents
        # Inspect only this owned process tree, never the ambient user's processes.
        import psutil
        from realms.lifecycle import validate_live

        validate_live(realm)
        pids = (
            (Path("/sys/fs/cgroup") / realm["cgroup"].lstrip("/") / "cgroup.procs")
            .read_text()
            .split()
        )
        drivers = []
        for pid in pids:
            try:
                process = psutil.Process(int(pid))
                argv = process.cmdline()
            except psutil.NoSuchProcess:
                continue
            if (
                argv
                and argv[0] == "/opt/realm-driver"
                and "--capability-manifest" in argv
            ):
                drivers.append(process)
        assert drivers, "no actual contained daemon found in the owned realm scope"
        for process in drivers:
            argv = process.cmdline()
            assert argv[argv.index("--permission-mode") + 1] == "bounded"
            assert argv[argv.index("--capability-manifest") + 1] == str(staged)
            assert "--approve-capability-manifest" in argv
            assert "--dangerously-bypass-approvals" not in argv
            proc = Path("/proc") / str(process.pid)
            assert (proc / "root" / str(staged).lstrip("/")).read_bytes() == contents
            mounts = [
                line.split() for line in (proc / "mountinfo").read_text().splitlines()
            ]
            assert any(
                row[4] == str(staged) and "ro" in row[5].split(",") for row in mounts
            )
        tool.set_approval_callback(lambda *_: "approve_once")
        before = wait_state(state)
        result = json.loads(
            dispatch(
                action="type",
                app="screen",
                text="outside-manifest",
                delivery_mode="foreground",
            )
        )
        assert result.get("ok") is False, result
        assert "manifest" in json.dumps(result).lower(), result
        assert_unchanged(state, before)
        assert tool._backends[sid] is backend
        assert_capture(dispatch(action="capture", app="screen", mode="vision"))


@pytest.mark.parametrize("manifest_kind", ["unset", "missing", "malformed"])
def test_invalid_bounded_manifest_never_downgrades_to_a_working_driver(
    tmp_path, monkeypatch, manifest_kind
):
    manifest = None if manifest_kind == "unset" else tmp_path / "capabilities.yaml"
    if manifest_kind == "malformed":
        manifest.write_text("version: [this is not valid YAML\n")
    with permission_session(
        tmp_path, monkeypatch, mode="bounded", manifest=manifest
    ) as (service, sid, dispatch, tool):
        realm, state = start_probe(service, sid, tmp_path)
        before = wait_state(state)
        for _ in range(2):
            result = dispatch(action="capture", app="screen", mode="vision")
            assert isinstance(result, str), "invalid manifest must not yield a capture"
            error = json.loads(result)
            assert "error" in error, error
            assert (
                "manifest" in error["error"].lower()
                or "authorization startup" in error["error"].lower()
            ), error
            assert sid not in tool._backends
            assert sid not in tool._backend_permission_modes
            assert tool._cua_permission_mode(sid) == "bounded"
            assert not list(Path(realm["runtime_dir"]).glob("hc-*.sock"))
        assert_unchanged(state, before)
