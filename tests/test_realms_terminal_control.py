"""Pending target commands cannot outlive the human-control epoch they saw."""
import json
from pathlib import Path
import runpy
import shlex
from dataclasses import replace

import pytest
from realms_test_paths import HERMES_ROOT, PLUGIN_ROOT

ROOT = HERMES_ROOT


@pytest.mark.linux_only
@pytest.mark.parametrize("background", [False, True])
@pytest.mark.parametrize("transition", ["unchanged", "takeover", "handback"])
def test_target_approval_wait_preserves_control_epoch(tmp_path, monkeypatch, background, transition):
    from tools import terminal_tool as tt
    from tools.registry import registry
    from tools.terminal_scope import set_terminal_scope, reset_terminal_scope
    from tools.terminal_targets import register_terminal_target_resolver
    from tools.process_registry import process_registry
    from hermes_cli.session_execution import resolve_session_execution_context

    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("BASH_ENV", raising=False)
    for name in ("_active_environments", "_last_activity", "_task_env_overrides",
                 "_session_cwd", "_session_cwd_observed", "_container_aliases"):
        monkeypatch.setattr(tt, name, {})
    scope = set_terminal_scope({"TERMINAL_ENV": "local", "TERMINAL_CWD": str(tmp_path)})
    load = runpy.run_path(str(PLUGIN_ROOT / "realms/_binding.py"))["load_runtime"]
    service = load("integration").RealmIntegration(home)
    contexts = load("target_contexts")
    owner = service.bind(session_origin="fresh", session_id="parent", task_id="parent-task")
    record = {"id": "r-" + "a" * 24, "generation": "b" * 32,
              "status": "running", "home": str(home), "session_id": owner}
    (home / "realms" / (record["id"] + ".json")).write_text(json.dumps(record))
    target = tmp_path / "target"
    target.mkdir()
    monkeypatch.setattr(service.manager, "env", lambda _: {"HOME": str(target)})
    monkeypatch.setattr(service, "_valid", lambda *_: True)

    def ready(_):
        service._attachments[owner] = (record["generation"], (), record["id"])
        return record

    monkeypatch.setattr(service, "ready", ready)
    builder = contexts._BUILDERS["realm"]
    # Replace only the GUI transport with a real inert local shell. Keep the
    # production provider, lease construction and shared control authority.
    monkeypatch.setitem(contexts._BUILDERS, "realm", lambda *args: replace(
        builder(*args), command_prefix=("/usr/bin/env",)))
    authority = load("bridge").get_profile_viewer(home).authority
    dispose = register_terminal_target_resolver("realm", service.terminal_context, selector=service.select_terminal_target)
    marker = target / "effect"
    command = "touch " + shlex.quote(str(marker))
    approval_seen = []

    def approval(value, *args, **kwargs):
        if value == command:
            approval_seen.append(value)
            if transition != "unchanged":
                assert authority.acquire(record["id"], record["generation"], "human")
                if transition == "handback":
                    authority.release(record["id"], "human")
        return {"approved": True}

    monkeypatch.setattr(tt, "_check_all_guards", approval)

    def call(args):
        result = registry.dispatch("terminal", args, session_id="parent", task_id="parent-task")
        return json.loads(result) if isinstance(result, str) else result

    try:
        result = call({"command": command, "target": "realm", "background": background})
        if result.get("session_id"):
            process_registry.wait(result["session_id"], timeout=10)
        assert approval_seen == [command]
        parent = call({"command": "printf parent-alive"})
        assert parent["output"] == "parent-alive", parent
        assert resolve_session_execution_context(session_id="parent", task_id="parent-task") is None
        if transition == "unchanged":
            assert marker.exists(), result
        else:
            assert not marker.exists(), "target command executed after control changed during approval"
            assert result.get("error"), result
    finally:
        dispose()
        for env in tt._active_environments.values():
            env.cleanup()
        # Inert record is not a native resource and must not enter teardown.
        (home / "realms" / (record["id"] + ".json")).unlink()
        service.unload()
        reset_terminal_scope(scope)
