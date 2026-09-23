"""Selection is inert; realization retains owner, incarnation and control authority."""
import json
from pathlib import Path
import runpy

import pytest
from realms_test_paths import HERMES_ROOT, PLUGIN_ROOT

pytestmark = pytest.mark.linux_only
ROOT = HERMES_ROOT


@pytest.fixture
def selected_service(tmp_path, monkeypatch):
    from hermes_cli.session_execution import SessionExecutionContext, ComputerUseLaunchContext
    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    load = runpy.run_path(str(PLUGIN_ROOT / "realms/_binding.py"))["load_runtime"]
    service = load("integration").get_integration(home)
    owner = service.bind(session_origin="fresh", session_id="parent", task_id="task")
    contexts = load("target_contexts")
    starts = []
    record = {"id": "r-" + "a" * 24, "generation": "b" * 32, "status": "running",
              "home": str(home), "session_id": owner, "workspace_dir": str(tmp_path / "workspace")}
    def persist():
        (home / "realms" / (record["id"] + ".json")).write_text(json.dumps(record), encoding="utf-8")
    def ready(_, *, before_start=None):
        if before_start is not None:
            before_start()
        starts.append(True)
        persist()
        service._attachments[owner] = (record["generation"], (), record["id"])
        return dict(record)
    monkeypatch.setattr(service, "ready", ready)
    monkeypatch.setattr(service, "_valid", lambda *_: True)
    monkeypatch.setattr(service, "_vm_valid", lambda *_: True)
    def builder(s, o, r, purpose):
        access = lambda: contexts._access_epoch(s, o, r["id"])
        return SessionExecutionContext(
            command_prefix=("/usr/bin/env",), backend_cwd=str(tmp_path),
            terminal_access_epoch=access if purpose == "terminal" else None,
            computer_use=ComputerUseLaunchContext(private_daemon=True, access_epoch=access) if purpose == "cua" else None)
    monkeypatch.setitem(contexts._BUILDERS, "realm", builder)
    monkeypatch.setitem(contexts._BUILDERS, "omarchy-vm", builder)
    authority = load("bridge").get_profile_viewer(home).authority
    yield service, owner, starts, record, persist, authority
    # No native teardown: the records describe inert test transports only.
    contexts.release_targets(service, owner)


@pytest.mark.parametrize("purpose", ["cua", "terminal"])
@pytest.mark.parametrize("state", ["cold", "live", "stopped", "vm-cold", "vm-live"])
@pytest.mark.parametrize("transition", ["none", "stop", "disable", "kind", "handback", "replacement", "profile", "owner"])
def test_selection_never_provisions_or_revives_stale_authority(selected_service, monkeypatch, purpose, state, transition):
    from hermes_cli.session_execution import SessionExecutionError
    service, owner, starts, record, persist, authority = selected_service
    if state.startswith("vm-"):
        service.owners.set_kind(owner, "omarchy-vm")
        record["id"] = "v-" + "a" * 24
        record["compute_generation"] = "c" * 32
    if state in ("live", "vm-live", "stopped"):
        record["status"] = "stopped" if state == "stopped" else "running"
        persist()
    method = service.select_computer_use_target if purpose == "cua" else service.select_terminal_target
    kwargs = {"session_id": "parent", "task_id": "task"}
    if purpose == "terminal":
        kwargs["command"] = "printf inert"
    before = {p.name: p.read_bytes() for p in (service.home / "realms").glob("*.json")}
    selection = method(**kwargs)
    selection.check()
    assert starts == [] and not service._target_contexts and not service._attachments
    assert before == {p.name: p.read_bytes() for p in (service.home / "realms").glob("*.json")}
    if transition == "stop":
        service.owners.setup_generation(owner, revoke=True)
    elif transition == "disable":
        service.owners.set_mode(owner, "host")
    elif transition == "kind":
        service.owners.set_kind(owner, "realm" if state.startswith("vm-") else "omarchy-vm")
    elif transition == "handback":
        if state in ("cold", "vm-cold"):
            # An intervening activation is not the operation's authorized cold start.
            persist()
        assert authority.acquire(record["id"], record["generation"], "human")
        authority.release(record["id"], "human")
    elif transition == "replacement":
        record["generation"] = "d" * 32
        persist()
    elif transition == "profile":
        monkeypatch.setenv("HERMES_HOME", str(service.home / "other"))
    elif transition == "owner":
        with service.owners.connection() as db:
            db.execute("UPDATE aliases SET owner='other' WHERE value='task'")
    if transition != "none":
        with pytest.raises(SessionExecutionError):
            selection.realize()
        assert starts == []
    else:
        record["status"] = "running"
        lease = selection.realize()
        selection.check()
        lease.check()
        assert bool(starts) == (state not in ("live", "vm-live"))
        assert authority.acquire(record["id"], record["generation"], "human")
        authority.release(record["id"], "human")
        with pytest.raises(SessionExecutionError):
            selection.check()
        lease.check()


@pytest.mark.parametrize("tool_name", ["computer_use", "terminal"])
@pytest.mark.parametrize("approval", ["approved", "denied", "stop", "handback"])
def test_registered_plugin_cold_use_requires_unchanged_consent(selected_service, monkeypatch, tool_name, approval):
    from types import SimpleNamespace
    import tools.computer_use_tool  # noqa: F401 — register the public handler
    from tools.computer_use import tool as cua
    from tools import terminal_tool as terminal
    from tools.registry import registry
    from tools.terminal_scope import set_terminal_scope, reset_terminal_scope
    from tools.computer_use.session_context import check_access_epoch
    service, owner, starts, record, persist, authority = selected_service
    plugin = runpy.run_path(str(PLUGIN_ROOT / "plugin.py"))
    ctx = SimpleNamespace(**{name: (lambda *a, **kw: None) for name in (
        "register_cli_command", "register_tool", "register_command", "register_middleware",
        "register_hook", "register_skill", "on_unload")})
    plugin["register"](ctx)
    effects = []
    class InertBackend(cua._NoopBackend):
        def __init__(self, mode, *, execution_context):
            super().__init__()
            self.execution_context = execution_context
        def click(self, **kw):
            check_access_epoch(self.execution_context)
            effects.append(True)
            return super().click(**kw)
    monkeypatch.setattr(cua, "_new_backend", InertBackend)
    for name in ("_active_environments", "_last_activity", "_task_env_overrides",
                 "_session_cwd", "_session_cwd_observed", "_container_aliases"):
        monkeypatch.setattr(terminal, name, {})
    monkeypatch.setenv("HOME", str(service.home.parent))
    monkeypatch.delenv("BASH_ENV", raising=False)
    scope = set_terminal_scope({"TERMINAL_ENV": "local", "TERMINAL_CWD": str(service.home.parent)})
    prompts = []
    def consent(*a, **kw):
        prompts.append(True)
        assert starts == [] and not service._target_contexts and not terminal._active_environments
        if approval == "stop":
            service.owners.setup_generation(owner, revoke=True)
        elif approval == "handback":
            persist()
            assert authority.acquire(record["id"], record["generation"], "human")
            authority.release(record["id"], "human")
        if tool_name == "computer_use":
            return json.dumps({"error": "denied"}) if approval == "denied" else None
        return {"approved": approval != "denied", "description": "test consent"}
    monkeypatch.setattr(cua, "_request_approval", consent)
    monkeypatch.setattr(terminal, "_check_all_guards", consent)
    args = ({"action": "click", "coordinate": [1, 2], "capture_after": False} if tool_name == "computer_use"
            else {"command": "printf private-target", "target": "realm"})
    try:
        result = registry.dispatch(tool_name, args, session_id="parent", task_id="task")
        data = json.loads(result) if isinstance(result, str) else result
        assert prompts == [True], data
        assert starts == ([True] if approval == "approved" else []), data
        if approval == "approved":
            assert effects if tool_name == "computer_use" else data["output"] == "private-target"
        else:
            assert not effects and (data.get("error") or data.get("code") == "policy_denied"), data
    finally:
        for env in terminal._active_environments.values():
            env.cleanup()
        for dispose in service._target_disposers:
            dispose()
        reset_terminal_scope(scope)
