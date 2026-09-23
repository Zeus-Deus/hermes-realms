"""Legacy permission authority is separate from target/data lifecycle."""
import json
from pathlib import Path
import runpy
import sqlite3
import subprocess
import sys

import pytest
from realms_test_paths import HERMES_ROOT, PLUGIN_ROOT

REPO = HERMES_ROOT
PLUGIN = PLUGIN_ROOT
load = runpy.run_path(str(PLUGIN / "realms/_binding.py"))["load_runtime"]
integration = load("integration")
pytestmark = pytest.mark.linux_only


def legacy_home(tmp_path):
    home = tmp_path / "profile"
    (home / "realms").mkdir(parents=True)
    with sqlite3.connect(home / "realms/sessions.sqlite3") as db:
        db.executescript("CREATE TABLE owners (id TEXT PRIMARY KEY, mode TEXT);"
                         "CREATE TABLE aliases(kind TEXT,value TEXT,owner TEXT,PRIMARY KEY(kind,value));")
        for owner, mode in (("private", "realm"), ("ask", "ask"), ("unset", None), ("off", "host")):
            db.execute("INSERT INTO owners VALUES (?,?)", (owner, mode))
            db.execute("INSERT INTO aliases VALUES ('session_id',?,?)", (owner, owner))
    return home


def test_contract_classification_survives_reopen_without_blessing_old_writers(tmp_path):
    home = legacy_home(tmp_path)
    store = integration.OwnershipStore(home)
    expected = {"private": "legacy-pending", "ask": "legacy-pending", "unset": "legacy-pending", "off": "legacy-host"}
    assert {o: store.permission(o)["state"] for o in expected} == expected
    store.bind(session_id="fresh", session_origin="fresh")
    store.bind(session_id="unknown")
    store.bind(session_id="private", runtime_session_id="new-runtime", session_origin="fresh")
    with store.connection() as db:
        db.execute("INSERT INTO owners(id) VALUES ('old-writer')")
    code = """
import sys,runpy,json
sys.path.insert(0,sys.argv[1])
load=runpy.run_path(sys.argv[2])["load_runtime"]
s=load("integration").OwnershipStore(sys.argv[3])
print(json.dumps({o:s.permission(o)["state"] for o in ['private','ask','unset','off','fresh','unknown','old-writer']}))
"""
    observed = json.loads(subprocess.check_output([sys.executable, "-c", code, str(REPO), str(PLUGIN / "realms/_binding.py"), str(home)], text=True))
    assert observed == {**expected, "fresh": "optional", "unknown": "legacy-pending", "old-writer": "legacy-pending"}
    assert store.mode("unset", None) is None
    assert store.mode("off", None) == "host"
    assert store.resolve(runtime_session_id="new-runtime") == "private"


@pytest.fixture
def registered(tmp_path, monkeypatch):
    from hermes_cli.plugins import discover_plugins, get_plugin_manager
    home = legacy_home(tmp_path)
    (home / "plugins").mkdir()
    (home / "plugins/hermes-realms").symlink_to(PLUGIN, target_is_directory=True)
    (home / "config.yaml").write_text("plugins:\n  enabled: [hermes-realms]\n  realms:\n    default_mode: host\napprovals:\n  mode: off\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    discover_plugins(force=True)
    manager = get_plugin_manager()
    assert "hermes-realms" in manager._plugins
    service = integration.get_integration(home)
    yield service, manager
    service.unload()


def test_registered_dispatch_holds_legacy_despite_early_approve_and_failures(registered, monkeypatch):
    import model_tools
    from tools.registry import registry
    from tools import approval
    service, manager = registered
    effects = []
    # This pre-hook deliberately wins first-directive resolution.
    manager._hooks.setdefault("pre_tool_call", []).insert(0, lambda **kw: {"action": "approve"})
    monkeypatch.setattr(approval, "_YOLO_MODE_FROZEN", True)
    registry.register(name="permission_probe", toolset="test", schema={"name": "permission_probe", "parameters": {"type": "object"}},
                      handler=lambda args, **kw: effects.append(args) or json.dumps({"executed": True}))
    for name in ("terminal", "read_file", "computer_use", "execute_code", "permission_probe", "tool_call"):
        args = {"command": "true"} if name == "terminal" else {}
        if name == "tool_call":
            args = {"calls": [{"name": "realm", "arguments": {"action": "status"}}, {"name": "permission_probe", "arguments": {}}]}
        result = json.loads(model_tools.handle_function_call(name, args, session_id="private"))
        if name == "tool_call":
            assert result.get("error"), result  # direct dispatcher refuses mixed locals before middleware
        else:
            assert result.get("error_code") == "legacy_permission_review_required", (name, result)
    assert effects == []
    assert json.loads(model_tools.handle_function_call("permission_probe", {}, session_id="off"))["executed"]
    service.bind(session_id="fresh", session_origin="fresh")
    assert json.loads(model_tools.handle_function_call("permission_probe", {}, session_id="fresh"))["executed"]
    result = json.loads(model_tools.handle_function_call("realm", {"action": "status"}, session_id="private"))
    assert result["permission"]["state"] == "legacy-pending"
    assert service._vm is None and service._attachments == {}
    for identity in ({}, {"session_id": "missing"}, {"session_id": "private", "task_id": "unbound"}):
        assert json.loads(model_tools.handle_function_call("permission_probe", {}, **identity))["error_code"] == "legacy_permission_review_required"
    monkeypatch.setattr(service.owners, "permission", lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError("broken")))
    assert json.loads(model_tools.handle_function_call("permission_probe", {}, session_id="off"))["error_code"] == "legacy_permission_review_required"
    assert len(effects) == 2


@pytest.mark.parametrize("mode_owner", ["private", "ask", "unset", "off"])
def test_pending_native_activation_cannot_change_authority_or_compute(registered, monkeypatch, mode_owner):
    service, _ = registered
    before = service.owners.permission(mode_owner)
    generation = service.owners.setup_generation(mode_owner)
    monkeypatch.setattr(service, "_activate", lambda *a, **kw: pytest.fail("legacy activation"))
    for raw in ("on", "on omarchy", "repair", "size 800x600", "push a b", "pull a b"):
        with pytest.raises(integration.OwnerError, match="permissions"):
            service.command(raw, session_id=mode_owner)
    with pytest.raises(integration.OwnerError, match="permissions"):
        load("setup_flow").start(service, mode_owner, "realm", "fake", {})
    with pytest.raises(integration.OwnerError, match="permissions"):
        service.reserve_setup(mode_owner)
    assert service.owners.permission(mode_owner) == before
    assert service.owners.setup_generation(mode_owner) == generation
    assert service._attachments == {}


def test_native_review_cancel_scope_drift_accept_and_prompt_bytes(registered, monkeypatch):
    service, _ = registered
    transition = load("permission_transition")
    owner = service.bind(session_id="private", runtime_session_id="runtime", stored_session_id="stored")
    identity = {"runtime_session_id": "runtime", "stored_session_id": "stored"}
    before = service.owners.permission(owner)
    proposal = transition.prepare(service, owner, identity)
    assert service.owners.permission(owner) == before
    assert not list(service.home.glob("realms/activation-*")), "read/cancel must create no lock"
    assert "configured original backend" in proposal["text"]
    assert "physical-desktop" in proposal["text"]
    service.owners.set_mode(owner, "ask")
    with pytest.raises(integration.OwnerError, match="changed"):
        transition.accept_native(service, owner, identity, proposal["digest"], provenance="desktop")
    assert service.owners.permission(owner)["state"] == "legacy-pending"
    proposal = transition.prepare(service, owner, identity)
    with pytest.raises(integration.OwnerError):
        transition.accept_native(service, "off", {"session_id": "off"}, proposal["digest"], provenance="desktop")
    for name in ("ready", "stop", "_stop", "_activate"):
        monkeypatch.setattr(service, name, lambda *a, **kw: pytest.fail("permission conversion touched lifecycle"))
    result = transition.accept_native(service, owner, identity, proposal["digest"], provenance="desktop")
    assert result["state"] == "optional"
    assert service.owners.mode(owner, None) == "ask"
    receipt = json.loads(integration.OwnershipStore(service.home, readonly=True).permission(owner)["receipt"])
    assert receipt["digest"] == proposal["digest"] and receipt["provenance"] == "desktop"
    with pytest.raises(integration.OwnerError):
        transition.accept_native(service, owner, identity, proposal["digest"], provenance="desktop")
    assert service.owners.permission("off")["state"] == "legacy-host"


def test_native_api_and_manual_elicitation_are_not_model_confirmation(registered, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from hermes_cli.plugins import get_plugin_command_handler
    from tools import approval_prompt
    service, _ = registered
    service.bind(session_id="private", runtime_session_id="runtime", stored_session_id="stored")
    api = runpy.run_path(str(PLUGIN / "dashboard/plugin_api.py"))
    app = FastAPI()
    app.include_router(api["router"])
    identity = {"runtime_session_id": "runtime", "stored_session_id": "stored"}
    with TestClient(app) as client:
        proposal = client.post("/realms/permissions/prepare", json=identity)
        assert proposal.status_code == 200, proposal.text
        assert client.post("/realms/permissions/accept", json={**identity, "digest": proposal.json()["digest"], "confirmed": True}).status_code == 422
        assert service.owners.permission("private")["state"] == "legacy-pending"
        result = client.post("/realms/permissions/accept", json={**identity, "digest": proposal.json()["digest"]})
        assert result.status_code == 200 and result.json()["state"] == "optional"
    command = get_plugin_command_handler("realm")
    seen = []
    def decline(*args, **kwargs):
        seen.append(args)
        return "decline"
    monkeypatch.setattr(approval_prompt, "request_elicitation_consent", decline)
    result = json.loads(command("review", session_id="ask"))
    assert result["accepted"] is False
    assert seen and service.owners.permission("ask")["state"] == "legacy-pending"
    # Even a hand-crafted out-of-schema model request must not reach consent.
    import model_tools
    result = json.loads(model_tools.handle_function_call("realm", {"action": "review"}, session_id="off"))
    assert result.get("error") and len(seen) == 1
    monkeypatch.setattr(approval_prompt, "request_elicitation_consent", lambda *a, **kw: "accept")
    result = json.loads(command("review", session_id="ask"))
    assert result["accepted"] is True
    assert service.owners.permission("ask")["state"] == "optional"


def test_disable_does_not_convert_and_unselected_conversion_does_not_enable_targets(registered):
    service, _ = registered
    service.command("off", session_id="private")
    assert service.owners.mode("private", None) == "host"
    assert service.owners.permission("private")["state"] == "legacy-pending"
    t = load("permission_transition")
    for owner in ("private", "unset", "ask", "off"):
        proposal = t.prepare(service, owner, {"session_id": owner})
        t.accept_native(service, owner, {"session_id": owner}, proposal["digest"], provenance="desktop")
        with pytest.raises(integration.OwnerError):
            service.ready(owner)
        assert service._attachments == {}


def test_managed_inline_and_concurrent_dispatch_held_with_fresh_task_positive(registered, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    from agent.tool_executor import _run_agent_tool_execution_middleware
    import model_tools
    from tools.registry import registry
    service, _ = registered
    service.bind(session_id="private", task_id="old-task")
    agent = SimpleNamespace(session_id="private")
    effects = []
    def attempt(name):
        result = _run_agent_tool_execution_middleware(
            agent, function_name=name, function_args={}, effective_task_id="old-task", tool_call_id=name,
            execute=lambda args: effects.append(name))
        assert json.loads(result.result)["error_code"] == "legacy_permission_review_required"
        assert not result.dispatched
    for name in ("delegate_task", "tool_call", "todo", "memory", "terminal", "read_file", "browser_exec", "cronjob", "unknown"):
        attempt(name)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(attempt, ["terminal", "read_file", "execute_code", "delegate_task"]))
    assert effects == []
    service.bind(session_id="fresh", session_origin="fresh")
    registry.register(name="permission_task_probe", toolset="test", schema={"name": "permission_task_probe", "parameters": {"type": "object"}},
                      handler=lambda args, **kw: json.dumps({"executed": True}))
    assert json.loads(model_tools.handle_function_call("permission_task_probe", {}, session_id="fresh", task_id="next-task"))["executed"]
    assert service.owners.resolve(task_id="next-task") == "fresh"
    assert json.loads(model_tools.handle_function_call("permission_task_probe", {}, session_id="private", task_id="next-task"))["error_code"] == "legacy_permission_review_required"


def test_native_create_and_resume_classify_before_lazy_agent_build(registered, monkeypatch):
    from tui_gateway import server
    from hermes_state import SessionDB
    service, _ = registered
    monkeypatch.setattr(server, "_sessions", {})
    monkeypatch.setattr(server, "_schedule_agent_build", lambda *a, **kw: None)
    monkeypatch.setattr(server, "_schedule_session_cap_enforcement", lambda: None)
    with SessionDB(db_path=service.home / "state.db") as db:
        db.create_session("private", source="desktop")
    created = server._methods["session.create"]("new", {"source": "desktop"})
    assert "error" not in created, created
    result = created["result"]
    owner = service.owners.resolve(runtime_session_id=result["session_id"])
    assert service.owners.permission(owner)["state"] == "optional"
    resumed = server._methods["session.resume"]("old", {"session_id": "private", "source": "desktop", "lazy": True})
    assert "error" not in resumed, resumed
    assert service.owners.resolve(runtime_session_id=resumed["result"]["session_id"]) == "private"
    assert service.owners.permission("private")["state"] == "legacy-pending"
    for record in server._sessions.values():
        server._finalize_session(record)


def test_permission_conversion_preserves_real_cached_prompt_and_history(registered, monkeypatch):
    from unittest.mock import patch
    from hermes_state import SessionDB
    from run_agent import AIAgent
    from agent.conversation_loop import _restore_or_build_system_prompt
    service, _ = registered
    old = "Legacy conversation is private.\nLiteral old bytes: café\n"
    with SessionDB(db_path=service.home / "state.db") as db:
        db.create_session("private", source="cli")
        db.update_system_prompt("private", old)
        db.append_message("private", role="user", content="Original input\n")
        db.append_message("private", role="assistant", content="Original reply\n")
        before = db.get_messages("private")
        with patch("model_tools.get_tool_definitions", return_value=[]), patch("model_tools.check_toolset_requirements", return_value={}):
            agent = AIAgent(model="test-model", api_key="test", provider="custom", base_url="http://127.0.0.1:1/v1",
                            session_db=db, session_id="private", enabled_toolsets=[], quiet_mode=True,
                            skip_context_files=True, skip_memory=True, skip_background_review=True)
        monkeypatch.setattr(agent, "_build_system_prompt", lambda *a: pytest.fail("old prompt was rebuilt"))
        try:
            history = [{"role": r["role"], "content": r["content"]} for r in before]
            _restore_or_build_system_prompt(agent, None, history)
            assert agent._cached_system_prompt == old
            t = load("permission_transition")
            review = t.prepare(service, "private", {"session_id": "private"})
            t.accept_native(service, "private", {"session_id": "private"}, review["digest"], provenance="desktop")
            _restore_or_build_system_prompt(agent, None, history)
            assert agent._cached_system_prompt == old
            assert db.get_session("private")["system_prompt"] == old
            assert db.get_messages("private") == before
            assert history == [{"role": r["role"], "content": r["content"]} for r in before]
        finally:
            agent.close()


def test_owned_resource_without_metadata_is_not_fresh_and_review_is_nonreconciling(registered, monkeypatch, request):
    service, _ = registered
    resource = {"id": "r-" + "d" * 24, "home": str(service.home), "session_id": "lost-owner", "status": "stopped"}
    path = service.home / "realms" / (resource["id"] + ".json")
    raw = json.dumps(resource).encode()
    path.write_bytes(raw)
    request.addfinalizer(lambda: path.unlink(missing_ok=True))
    owner = service.bind(session_id="lost-owner", session_origin="fresh")
    assert service.owners.permission(owner)["state"] == "legacy-pending"
    monkeypatch.setattr(service.manager, "list", lambda: pytest.fail("review reconciled legacy compute"))
    t = load("permission_transition")
    review = t.prepare(service, owner, {"session_id": owner})
    assert review["resources"][0]["id"] == resource["id"]
    assert service.status(owner)["realms"][0]["id"] == resource["id"]
    path.write_bytes(raw + b" ")
    with pytest.raises(integration.OwnerError, match="changed"):
        t.accept_native(service, owner, {"session_id": owner}, review["digest"], provenance="desktop")
    assert path.read_bytes() == raw + b" "
    # Registry is an inert, intentionally incomplete metadata fixture, not compute.
    path.unlink()


def test_copied_receipt_unknown_contract_and_old_parent_lease_fail_closed(registered):
    from hermes_cli.session_execution import SessionExecutionContext, register_session_execution_context, remove_session_execution_context
    service, _ = registered
    t = load("permission_transition")
    owner = "private"
    register_session_execution_context(owner, SessionExecutionContext())
    try:
        with pytest.raises(integration.OwnerError, match="still attached"):
            t.prepare(service, owner, {"session_id": owner})
    finally:
        remove_session_execution_context(owner)
    proposal = t.prepare(service, owner, {"session_id": owner})
    t.accept_native(service, owner, {"session_id": owner}, proposal["digest"], provenance="desktop")
    other = integration.OwnershipStore(service.home.parent / "copied")
    other.bind(session_id=owner)
    with other.connection() as db:
        db.execute("UPDATE owners SET execution_contract=?, permission_receipt=? WHERE id=?", (
            "optional-targets-v1", service.owners.permission(owner)["receipt"], owner))
    assert other.permission(owner)["state"] == "legacy-pending"
    with service.owners.connection() as db:
        db.execute("UPDATE owners SET execution_contract='unknown-future' WHERE id='ask'")
    with pytest.raises(integration.OwnerError):
        t.prepare(service, "ask", {"session_id": "ask"})


def test_cached_target_admission_cannot_bypass_pending_contract(registered):
    from hermes_cli.session_execution import SessionExecutionError
    service, _ = registered
    with pytest.raises(SessionExecutionError, match="permission"):
        load("target_contexts")._access_epoch(service, "private", "inert-target")


def test_pending_managed_discovery_can_bind_its_new_trusted_task(registered, monkeypatch):
    from types import SimpleNamespace
    from agent import tool_executor
    service, _ = registered
    monkeypatch.setattr(tool_executor, "_begin_tool_execution", lambda *a: None)
    monkeypatch.setattr(tool_executor, "_run_with_activity_heartbeat", lambda agent, name, fn: fn())
    agent = SimpleNamespace(session_id="private", _tool_guardrails=SimpleNamespace(
        before_call=lambda *a: SimpleNamespace(allows_execution=True)))
    for name in ("clarify", "tool_search", "tool_describe", "skill_view", "skills_list"):
        result = tool_executor._run_agent_tool_execution_middleware(
            agent, function_name=name, function_args={}, effective_task_id="new-discovery-task",
            tool_call_id=name, execute=lambda args: json.dumps({"discovery": True}))
        assert json.loads(result.result) == {"discovery": True}
    assert service.owners.permission("private")["state"] == "legacy-pending"


def test_unset_acceptance_persists_the_reviewed_unselected_mode(registered):
    service, _ = registered
    t = load("permission_transition")
    review = t.prepare(service, "unset", {"session_id": "unset"})
    assert review["target_state"] == "unselected"
    t.accept_native(service, "unset", {"session_id": "unset"}, review["digest"], provenance="desktop")
    for default in ("host", "realm", "ask"):
        assert service.owners.mode("unset", default) == "ask"
    receipt = json.loads(service.owners.permission("unset")["receipt"])
    assert receipt["old_mode"] is None and receipt["new_mode"] == "ask"


def test_resource_fifo_is_refused_before_blocking_open(registered, request):
    import os
    service, _ = registered
    fifo = service.home / "realms" / ("r-" + "e" * 24 + ".json")
    os.mkfifo(fifo, 0o600)
    request.addfinalizer(lambda: fifo.unlink(missing_ok=True))
    code = '''
import sys,runpy
sys.path.insert(0,sys.argv[1])
load=runpy.run_path(sys.argv[2])["load_runtime"]
s=load("integration").RealmIntegration(sys.argv[3])
try:
    load("permission_transition").prepare(s,"private",{"session_id":"private"})
except PermissionError:
    print("refused FIFO")
else:
    raise AssertionError("FIFO admitted")
'''
    with subprocess.Popen([sys.executable, "-c", code, str(REPO), str(PLUGIN / "realms/_binding.py"), str(service.home)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
        try:
            out, err = child.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate()
            pytest.fail("resource review blocked opening an actual FIFO")
        assert child.returncode == 0, err
        assert out.strip() == "refused FIFO"


def test_corrupt_store_at_real_plugin_registration_cannot_drop_the_hold(tmp_path, monkeypatch):
    from hermes_cli.plugins import discover_plugins, get_plugin_manager
    from tools.registry import registry
    import model_tools
    home = tmp_path / "corrupt-profile"
    (home / "plugins").mkdir(parents=True)
    (home / "plugins/hermes-realms").symlink_to(PLUGIN, target_is_directory=True)
    (home / "realms").mkdir()
    (home / "realms/sessions.sqlite3").write_bytes(b"invalid SQLite fixture")
    (home / "config.yaml").write_text("plugins:\n  enabled: [hermes-realms]\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    discover_plugins(force=True)
    effects = []
    registry.register(name="permission_corrupt_probe", toolset="test", schema={"name":"permission_corrupt_probe","parameters":{"type":"object"}},
                      handler=lambda args, **kw: effects.append(True) or json.dumps({"executed":True}))
    result = json.loads(model_tools.handle_function_call("permission_corrupt_probe", {}, session_id="historical"))
    assert result.get("error_code") == "legacy_permission_review_required", result
    assert effects == []
    assert get_plugin_manager().has_middleware("tool_execution")
    assert (home / "realms/sessions.sqlite3").read_bytes() == b"invalid SQLite fixture"


def test_manual_review_has_no_unattended_or_yolo_acceptance_fallback(registered, monkeypatch):
    from hermes_cli.plugins import get_plugin_command_handler
    from tools import approval, approval_context
    service, _ = registered
    monkeypatch.setattr(approval, "_YOLO_MODE_FROZEN", True)
    monkeypatch.setattr(approval_context, "_is_gateway_approval_context", lambda: True)
    # Actual elicitation routing, with no human notifier registered for this owner.
    seen = []
    monkeypatch.setattr(approval, "_gateway_notify_cb", lambda key: seen.append(key) or None)
    before = service.owners.permission("private")
    result = json.loads(get_plugin_command_handler("realm")("review", session_id="private"))
    assert result["accepted"] is False
    assert seen == ["private"]
    assert service.owners.permission("private") == before


def test_simultaneous_accepts_commit_only_one_receipt(registered):
    from concurrent.futures import ThreadPoolExecutor
    service, _ = registered
    t = load("permission_transition")
    review = t.prepare(service, "private", {"session_id": "private"})
    def accept():
        try:
            t.accept_native(service, "private", {"session_id": "private"}, review["digest"], provenance="desktop")
            return True
        except integration.OwnerError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        verdicts = list(pool.map(lambda _: accept(), range(2)))
    assert verdicts.count(True) == 1 and verdicts.count(False) == 1
    assert integration.OwnershipStore(service.home, readonly=True).permission("private")["state"] == "optional"


@pytest.mark.parametrize("phase", ["before-commit", "after-commit"])
def test_process_death_respects_atomic_permission_commit(registered, phase):
    service, _ = registered
    code = '''
import os,sys,runpy
from contextlib import contextmanager
sys.path.insert(0,sys.argv[1])
load=runpy.run_path(sys.argv[2])["load_runtime"]
s=load("integration").RealmIntegration(sys.argv[3]); t=load("permission_transition")
identity={"session_id":"unset"}; review=t.prepare(s,"unset",identity)
original=s.owners.connection
class Connection:
    def __init__(self,db): self.db=db; self.wrote=False
    def __getattr__(self,name): return getattr(self.db,name)
    def execute(self,sql,*args):
        result=self.db.execute(sql,*args)
        if sql.startswith("UPDATE owners SET execution_contract=?,permission_receipt=?"):
            self.wrote=True
            if sys.argv[4]=="before-commit": os._exit(37)
        return result
@contextmanager
def connection(**kwargs):
    with original(**kwargs) as db:
        proxy=Connection(db)
        yield proxy
    if proxy.wrote: os._exit(37)
s.owners.connection=connection
t.accept_native(s,"unset",identity,review["digest"],provenance="desktop")
raise AssertionError("death boundary not exercised")
'''
    child = subprocess.run([sys.executable, "-c", code, str(REPO), str(PLUGIN / "realms/_binding.py"), str(service.home), phase],
                           capture_output=True, text=True, timeout=10)
    assert child.returncode == 37, child.stderr
    reopened = integration.OwnershipStore(service.home, readonly=True)
    if phase == "before-commit":
        # A killed writer leaves a rollback journal. Selection must refuse, not
        # repair it before consent. Explicit maintenance can recover it; the
        # unchanged atomicity assertions below then inspect the recovered state.
        before = {p.name: p.read_bytes() for p in reopened.root.iterdir() if p.is_file()}
        with pytest.raises(ValueError, match="journal"):
            reopened.permission("unset")
        assert {p.name: p.read_bytes() for p in reopened.root.iterdir() if p.is_file()} == before
        with reopened.connection(readonly=False) as db:
            db.execute("UPDATE owners SET mode=mode WHERE id=?", ("unset",))
    permission = reopened.permission("unset")
    assert permission["state"] == ("optional" if phase == "after-commit" else "legacy-pending")
    assert reopened.mode("unset", None) == ("ask" if phase == "after-commit" else None)


@pytest.mark.parametrize("kind", ["directory", "symlink"])
def test_special_resource_rejection_does_not_leak_descriptors(registered, request, kind):
    import os
    service, _ = registered
    path = service.home / "realms" / ("r-" + "f" * 24 + ".json")
    if kind == "directory":
        path.mkdir()
        request.addfinalizer(path.rmdir)
    else:
        sentinel = service.home / "outside-record"
        sentinel.write_text("must not read this as a record")
        path.symlink_to(sentinel)
        request.addfinalizer(path.unlink)
    def fd_count():
        with os.scandir("/proc/self/fd") as entries:
            return len(list(entries))
    before = fd_count()
    for _ in range(3):
        with pytest.raises((PermissionError, OSError)):
            load("permission_transition")._resources(service, "private")
    assert fd_count() == before
