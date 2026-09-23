"""Registered backend vertical: deterministic host/model, real discovery and turns.

No packages, guests, credentials or external model are used. Only the installer
child, host readiness/desktop allocation and model reply are deterministic seams.
"""
from dataclasses import asdict
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest
from realms_test_paths import HERMES_ROOT, install_user_plugin

pytestmark = pytest.mark.linux_only


@pytest.fixture
def rig(tmp_path, monkeypatch):
    from hermes_cli import plugins
    from hermes_cli.plugins_discovery import collect_directory_manifests
    from hermes_cli.plugins_manifest import manifest_key
    from tui_gateway import server
    from tools.registry import registry

    home = tmp_path / "profile"
    reopening = (home / "realms" / "sessions.sqlite3").exists()
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_SAFE_MODE", raising=False)
    install_user_plugin(home)
    other = [manifest_key(m) for m in collect_directory_manifests() if m.name != "hermes-realms"]
    (home / "config.yaml").write_text(json.dumps({"plugins": {
        "enabled": ["hermes-realms"], "disabled": other}}))
    manager = plugins.get_plugin_manager()
    manager.discover_and_load()
    module = manager._plugins["hermes-realms"].module.plugin
    service = module.get_integration(home)
    flow = module._load_runtime("setup_flow")
    integration = module._integration
    ready, installs, starts, turns, events = [False], [], [], [], []
    monkeypatch.setattr(integration, "setup_status", lambda **kw: {
        "ready": ready[0], "message": "deterministic prerequisite missing"})
    monkeypatch.setattr(flow, "build_plan", lambda s, owner, kind: dict(
        home=str(home), owner=owner, kind=kind, config=asdict(s.manager.config),
        ready=False, action="install", summary="fixture setup", details=[], blockers=[], packages=[]))

    def installed(*a, **kw):
        installs.append(kw)
        ready[0] = True

    monkeypatch.setattr(module._load_runtime("setup_worker"), "run_child", installed)
    monkeypatch.setattr(flow, "verify_ready", lambda *a: None)

    def desktop(owner):
        starts.append(owner)
        return {"id": "fixture-desktop", "generation": "fixture-generation", "session_id": owner}

    monkeypatch.setattr(service.manager, "start", desktop)
    for name in ("_wire_callbacks", "_sync_agent_model_with_config", "_register_session_cwd",
                 "_sync_session_key_after_compress"):
        monkeypatch.setattr(server, name, lambda *a, **kw: None)
    monkeypatch.setattr(server, "_session_cwd", lambda s: str(tmp_path))
    monkeypatch.setattr(server, "_get_usage", lambda a: {})
    monkeypatch.setattr(server, "_tts_stream_begin", lambda: None)
    monkeypatch.setattr(server, "_emit", lambda *a, **kw: events.append(a))
    sessions = []

    def session(owner="owner", sid="runtime", source="desktop"):
        agent = SimpleNamespace(session_id=owner, clear_interrupt=lambda: None)

        def model(message, conversation_history=None, **kw):
            from hermes_constants import get_hermes_home
            turns.append((owner, get_hermes_home(), message, conversation_history))
            return {"final_response": "deterministic reevaluation", "messages": [
                *(conversation_history or []), {"role": "user", "content": message},
                {"role": "assistant", "content": "deterministic reevaluation"}]}

        agent.run_conversation = model
        s = dict(agent=agent, session_key=owner, profile_home=str(home), source=source,
                 history=[{"role": "user", "content": "Test my current task privately"},
                          {"role": "assistant", "content": "Setup is needed"}],
                 history_lock=threading.RLock(), history_version=0, running=False,
                 attached_images=[], cols=80, slash_worker=None, inflight_turn=None)
        server._sessions[sid] = s
        server._publish_session_identity(sid, s, session_origin="resume" if reopening else "fresh")
        sessions.append((sid, s))
        return s

    s = session()
    tool = registry.get_entry("realm", scope=manager.scope_key).handler

    def request(s=s):
        result = json.loads(tool({"action": "on", "kind": "realm"}, **server._session_hook_identity(s)))
        assert "deterministic prerequisite missing" in result.get("error", ""), result

    def setup(expected="succeeded"):
        proposal = flow.prepare(service, "owner", "realm")
        job = flow.start(service, "owner", "realm", proposal["consent"], {"runtime_session_id": "runtime"})
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if flow.status(service, "owner", job["id"])["state"] not in {"running", "cancelling"}:
                break
            time.sleep(.01)
        assert flow.status(service, "owner", job["id"])["state"] == expected
        # Terminal publication precedes worker retirement/profile-lock release.
        for thread in threading.enumerate():
            if thread.name == "realms-setup-" + job["id"]:
                thread.join(10)
                assert not thread.is_alive()
        return job

    def poll(s=s, sid="runtime"):
        consumer = getattr(server, "_poll_plugin_idle_once", None)
        assert callable(consumer), "native notification poller has no registered plugin idle consumer"
        result = consumer(sid, s)
        thread = s.get("_run_thread")
        if thread:
            thread.join(10)
            assert not thread.is_alive()
        return result

    yield SimpleNamespace(**locals())
    for sid, current in sessions:
        if thread := current.get("_run_thread"):
            thread.join(10)
        server._release_active_session_slot(current)
        server._sessions.pop(sid, None)
    service._attachments.clear()  # deterministic desktops are not host resources
    manager.unload()


def test_unrelated_idle_poll_does_not_wait_for_long_activation(rig, monkeypatch):
    import contextvars

    peer = rig.session(owner="peer", sid="peer-runtime")
    # Explicit manual setup for owner; neither conversation requested a resume.
    with rig.service.owners.connection() as db:
        assert db.execute("SELECT setup_intent FROM owners WHERE id='peer'").fetchone()[0] is None
    entered, release, polled = threading.Event(), threading.Event(), threading.Event()
    desktop = rig.service.manager.start
    errors = []

    def slow_start(owner):
        assert owner == "owner"
        entered.set()
        if not release.wait(15):
            raise RuntimeError("probe barrier timed out")
        return desktop(owner)

    monkeypatch.setattr(rig.service.manager, "start", slow_start)
    proposal = rig.flow.prepare(rig.service, "owner", "realm")
    job = rig.flow.start(rig.service, "owner", "realm", proposal["consent"], {"runtime_session_id": "runtime"})

    def poll():
        try:
            assert rig.server._poll_plugin_idle_once("peer-runtime", peer) is False
        except BaseException as exc:
            errors.append(exc)
        finally:
            polled.set()

    thread = threading.Thread(target=contextvars.copy_context().run, args=(poll,))
    try:
        assert entered.wait(5), "real activate_setup did not reach start seam"
        thread.start()
        finished_while_activation_held = polled.wait(2)
    finally:
        release.set()
        if thread.ident is not None:
            thread.join(10)
        for worker in threading.enumerate():
            if worker.name == "realms-setup-" + job["id"]:
                worker.join(10)
                assert not worker.is_alive()
    assert not thread.is_alive()
    assert not errors, errors
    assert rig.flow._read(rig.service, job["id"])["state"] == "succeeded"
    assert not rig.turns
    assert finished_while_activation_held, "ordinary peer idle poll waited behind unrelated activation"


def test_registered_setup_success_continues_same_idle_conversation(rig):
    rig.request()
    before = list(rig.s["history"])
    job = rig.setup()
    assert rig.installs and rig.starts == ["owner"]
    assert rig.poll() is True
    assert len(rig.turns) == 1
    owner, home, message, history = rig.turns[0]
    assert (owner, home) == ("owner", rig.home)
    assert history == before
    assert "reevaluate" in message.lower() and "replay" in message.lower()
    assert rig.s["history"][:len(before)] == before
    assert rig.flow.status(rig.service, "owner", job["id"])["continuation"] == "settled"
    assert any(event[0] == "message.complete" for event in rig.events)
    assert rig.poll() is False
    assert len(rig.turns) == 1


@pytest.mark.parametrize("failures", [1, 2])
def test_failed_setup_retry_continues_original_request_once(rig, monkeypatch, failures):
    rig.request()
    before = list(rig.s["history"])
    worker = rig.module._load_runtime("setup_worker")
    installed = worker.run_child

    def fail_install(*args, **kwargs):
        raise RuntimeError("synthetic installer failure")

    monkeypatch.setattr(worker, "run_child", fail_install)
    for _ in range(failures):
        failed = rig.setup(expected="failed")
        assert rig.flow.status(rig.service, "owner", failed["id"])["continuation"] == "invalidated"
        assert rig.poll() is False
        assert not rig.starts and not rig.turns
        assert "synthetic installer failure" not in rig.flow._path(rig.service, failed["id"]).read_text()
    monkeypatch.setattr(worker, "run_child", installed)
    retried = rig.setup()
    assert rig.starts == ["owner"]
    assert rig.flow.status(rig.service, "owner", retried["id"])["continuation"] == "pending"
    peer = rig.session(owner="peer", sid="peer-runtime")
    assert rig.poll(peer, "peer-runtime") is False
    assert rig.poll() is True
    assert len(rig.turns) == 1
    assert rig.turns[0][3] == before
    assert "reevaluate" in rig.turns[0][2].lower() and "replay" in rig.turns[0][2].lower()
    assert rig.flow.status(rig.service, "owner", retried["id"])["continuation"] == "settled"
    assert rig.poll() is False
    later = rig.setup()
    assert rig.flow.status(rig.service, "owner", later["id"])["continuation"] == "none"
    assert rig.poll() is False
    assert len(rig.turns) == 1


@pytest.mark.parametrize("boundary", ["failing", "retry", "retry-published", "retry-published-no-poll"])
def test_source_change_between_request_and_retry_is_not_revived(rig, monkeypatch, boundary):
    rig.request()
    worker = rig.module._load_runtime("setup_worker")
    installed = worker.run_child

    def fail(*args, **kwargs):
        if boundary == "failing":
            rig.s["source"] = "tui"
        raise RuntimeError("fixture install failure")

    monkeypatch.setattr(worker, "run_child", fail)
    failed = rig.setup(expected="failed")
    assert rig.flow.status(rig.service, "owner", failed["id"])["continuation"] == "invalidated"
    assert not rig.poll()
    if boundary != "failing":
        rig.s["source"] = "tui"
        if boundary.startswith("retry-published"):
            rig.s["plugin_session_identity"].update(source="tui", surface="tui")
            rig.server._publish_session_identity("runtime", rig.s)
            assert rig.service._setup_request_sources["owner"]["source"] == "tui"
        if boundary != "retry-published-no-poll":
            assert not rig.poll()
    # No new agent request. Returning to the original surface must not
    # revive an intent whose source changed while/between failed attempts.
    rig.s["source"] = "desktop"
    if boundary.startswith("retry-published"):
        rig.s["plugin_session_identity"].update(source="desktop", surface="desktop")
        rig.server._publish_session_identity("runtime", rig.s)
        assert rig.service._setup_request_sources["owner"]["source"] == "desktop"
    monkeypatch.setattr(worker, "run_child", installed)
    retried = rig.setup()
    started = rig.poll()
    assert started is False
    assert not rig.turns
    assert rig.flow.status(rig.service, "owner", retried["id"])["continuation"] == "none"


@pytest.mark.parametrize("bad", ["missing-home", "missing-owner"])
def test_malformed_intent_still_publishes_terminal_failed_receipt(rig, monkeypatch, bad):
    rig.request()
    with rig.service.owners.connection() as db:
        intent = json.loads(db.execute("SELECT setup_intent FROM owners WHERE id='owner'").fetchone()[0])
        intent.pop(bad.removeprefix("missing-"))
        db.execute("UPDATE owners SET setup_intent=? WHERE id='owner'", (json.dumps(intent),))
    def fail(*a, **kw):
        raise RuntimeError("fixture install failure")
    monkeypatch.setattr(rig.module._load_runtime("setup_worker"), "run_child", fail)
    failed = rig.setup(expected="failed")
    record = rig.flow._read(rig.service, failed["id"])
    assert record["state"] == "failed"
    assert record["continuation"]["state"] == "invalidated"
    with rig.service.owners.connection() as db:
        retained = json.loads(db.execute("SELECT setup_intent FROM owners WHERE id='owner'").fetchone()[0])
    assert retained == intent, "malformed intent must not be renewed"


@pytest.mark.parametrize("boundary,invalidate", [
    (boundary, invalidate)
    for boundary in ("failing", "retry")
    for invalidate in ("disable", "generation", "kind", "finalize", "source")
] + [("failing", "cancel"), ("failing", "permission"), ("failing", "new-request")])
def test_retry_does_not_revive_revoked_or_replaced_request(rig, monkeypatch, boundary, invalidate):
    rig.request()
    worker = rig.module._load_runtime("setup_worker")
    installed = worker.run_child
    monkeypatch.setattr(rig.integration, "vm_setup_status", lambda *a: {
        "ready": False, "message": "deterministic prerequisite missing"})

    def revoke_generation():
        with rig.service._lock, rig.service.owners.activation_guard("owner"):
            rig.service.owners.setup_generation("owner", revoke=True)

    def change_kind():
        with pytest.raises(rig.integration.SetupError):
            rig.service.command("on omarchy", session_id="owner")

    def finalize():
        rig.service.finalize(session_id="owner")
        rig.server._publish_session_identity("runtime", rig.s, session_origin="resume")

    def revoke(job_id):
        actions = {
            "cancel": lambda: rig.flow.cancel(rig.service, "owner", job_id),
            "disable": lambda: rig.service.command("off", session_id="owner"),
            "generation": revoke_generation,
            "kind": change_kind,
            "finalize": finalize,
            "permission": lambda: _hold_permission(rig.service),
            "source": lambda: rig.s.update(source="tui"),
            "new-request": rig.request,
        }
        actions[invalidate]()

    def fail_install(*args, **kwargs):
        if boundary == "failing":
            revoke(Path(kwargs["cancel_path"]).stem)
        raise RuntimeError("synthetic installer failure")

    monkeypatch.setattr(worker, "run_child", fail_install)
    failed = rig.setup(expected="cancelled" if invalidate == "cancel" else "failed")
    assert rig.flow.status(rig.service, "owner", failed["id"])["continuation"] == "invalidated"
    assert rig.poll() is False
    assert not rig.starts and not rig.turns
    if boundary == "retry":
        revoke(failed["id"])
    if invalidate == "permission":
        # Even restoring permission cannot make the failed attempt renew intent.
        with rig.service.owners.connection() as db:
            db.execute("UPDATE owners SET execution_contract='optional-targets-v1' WHERE id='owner'")
    if invalidate == "new-request":
        with rig.service.owners.connection() as db:
            raw, generation = db.execute("SELECT setup_intent, setup_generation FROM owners WHERE id='owner'").fetchone()
        assert json.loads(raw)["generation"] == generation, (raw, generation)
    monkeypatch.setattr(worker, "run_child", installed)
    retried = rig.setup()
    assert rig.poll() is (invalidate == "new-request"), rig.flow._read(rig.service, retried["id"])
    assert len(rig.turns) == int(invalidate == "new-request")
    assert rig.flow.status(rig.service, "owner", retried["id"])["continuation"] != "pending"
    assert rig.poll() is False


@pytest.mark.parametrize("invalidate", ["disable", "kind", "generation", "requested-kind", "permission", "source"])
def test_ready_receipt_rechecks_authority_at_admission(rig, invalidate):
    rig.request()
    job = rig.setup()
    service = rig.service
    actions = {
        "disable": lambda: service.command("off", session_id="owner"),
        "kind": lambda: service.owners.set_kind("owner", "omarchy-vm"),
        "generation": lambda: service.owners.setup_generation("owner", revoke=True),
        "requested-kind": lambda: service.owners.request_kind("owner", "omarchy-vm"),
        "permission": lambda: _hold_permission(service),
        "source": lambda: rig.s.update(source="tui"),
    }
    actions[invalidate]()
    assert rig.poll() is False
    assert rig.turns == []
    assert rig.flow.status(service, "owner", job["id"])["continuation"] == "invalidated"


def _hold_permission(service):
    with service.owners.connection() as db:
        db.execute("UPDATE owners SET execution_contract='legacy-held-v0' WHERE id='owner'")


@pytest.mark.parametrize("blocker", ["running", "queued_prompt", "queued_prompts", "_auto_continue_scheduled",
                                    "_closing", "_finalized"])
def test_busy_and_retired_sessions_defer_without_claiming(rig, blocker):
    rig.request()
    job = rig.setup()
    path = rig.flow._path(rig.service, job["id"])
    stamp = path.stat().st_mtime_ns
    rig.s[blocker] = True
    assert rig.poll() is False
    assert rig.turns == []
    assert path.stat().st_mtime_ns == stamp, "busy sessions must not claim even transiently"
    rig.s[blocker] = False
    assert rig.poll() is True


@pytest.mark.parametrize("intent", ["manual", "unknown-agent"])
def test_setup_without_trusted_request_never_starts_a_model(rig, intent):
    if intent == "manual":
        with pytest.raises(rig.integration.SetupError):
            rig.service.command("on realm", **rig.server._session_hook_identity(rig.s))
    else:
        result = json.loads(rig.tool({"action": "on", "kind": "realm"}, session_id="library-owner"))
        assert "error" in result
    job = rig.setup()
    assert rig.poll() is False
    assert not rig.turns
    assert rig.flow.status(rig.service, "owner", job["id"])["continuation"] == "none"


def test_cancelled_setup_invalidates_continuation_without_activation(rig, monkeypatch):
    rig.request()
    def cancel_child(*args, **kw):
        rig.flow.cancel(rig.service, "owner", Path(kw["cancel_path"]).stem)
    monkeypatch.setattr(rig.module._load_runtime("setup_worker"), "run_child", cancel_child)
    job = rig.setup(expected="cancelled")
    assert rig.poll() is False
    assert not rig.starts and not rig.turns
    assert rig.flow.status(rig.service, "owner", job["id"])["continuation"] == "invalidated"


def test_foreign_owner_and_profile_cannot_consume_pending_setup(rig, monkeypatch):
    rig.request()
    job = rig.setup()
    peer = rig.session(owner="peer", sid="peer-runtime")
    assert rig.poll(peer, "peer-runtime") is False
    foreign = rig.tmp_path / "foreign-profile"
    foreign.mkdir()
    (foreign / "config.yaml").write_text((rig.home / "config.yaml").read_text())
    # Same owner spelling in a different profile is not the requesting owner.
    rig.s["profile_home"] = str(foreign)
    assert rig.poll() is False
    rig.s["profile_home"] = str(rig.home)
    monkeypatch.setenv("HERMES_HOME", str(foreign))
    assert rig.poll() is True, "owner scope must win over ambient foreground profile"
    assert rig.turns[0][1] == rig.home
    assert rig.flow.status(rig.service, "owner", job["id"])["continuation"] == "settled"



def _cold_consume(home, *, retry=False):
    """New interpreter, new discovered plugin/backend; deterministic model only."""
    mp = pytest.MonkeyPatch()
    fixture = rig.__wrapped__(Path(home).parent, mp)
    r = next(fixture)
    try:
        if retry:
            r.setup()
        result = r.poll()
        receipt = r.flow.latest(r.service, "owner")["continuation"]
        return {"started": result, "turns": len(r.turns), "receipt": receipt, "installs": len(r.installs)}
    finally:
        try:
            next(fixture)
        except StopIteration:
            pass
        mp.undo()


def _reopen_in_new_process(rig, *, retry=False):
    import os
    import subprocess
    import sys
    rig.server._release_active_session_slot(rig.s)
    rig.server._sessions.pop("runtime")
    code = ("import os,runpy,json,sys; sys.path.insert(0, os.path.dirname(sys.argv[1])); m=runpy.run_path(sys.argv[1]); "
            "print('REOPEN_RESULT='+json.dumps(m['_cold_consume'](sys.argv[2], retry=sys.argv[3] == 'True')))")
    child = subprocess.run([sys.executable, "-c", code, __file__, str(rig.home), str(retry)],
                           cwd=HERMES_ROOT, env=dict(os.environ),
                           capture_output=True, text=True, timeout=60)
    assert child.returncode == 0, child.stdout + child.stderr
    return json.loads(next(line.split("=", 1)[1] for line in (child.stdout + child.stderr).splitlines()
                           if line.startswith("REOPEN_RESULT=")))


@pytest.mark.parametrize("source", ["tui", "desktop"])
def test_new_request_after_source_change_retries_once(rig, monkeypatch, source):
    rig.request()
    worker = rig.module._load_runtime("setup_worker")
    installed = worker.run_child

    def fail(*args, **kwargs):
        raise RuntimeError("fixture install failure")

    monkeypatch.setattr(worker, "run_child", fail)
    rig.setup(expected="failed")
    for current in ("tui", source):
        rig.s["source"] = current
        rig.s["plugin_session_identity"].update(source=current, surface=current)
        rig.server._publish_session_identity("runtime", rig.s)
    rig.request()
    monkeypatch.setattr(worker, "run_child", installed)
    rig.setup()
    assert rig.poll() is True
    assert rig.poll() is False
    assert len(rig.turns) == 1


@pytest.mark.parametrize("round_trip", [False, True])
def test_failed_request_source_authority_survives_cold_retry(rig, monkeypatch, round_trip):
    rig.request()

    def fail(*args, **kwargs):
        raise RuntimeError("fixture install failure")

    monkeypatch.setattr(rig.module._load_runtime("setup_worker"), "run_child", fail)
    rig.setup(expected="failed")
    if round_trip:
        for current in ("tui", "desktop"):
            rig.s["source"] = current
            rig.s["plugin_session_identity"].update(source=current, surface=current)
            rig.server._publish_session_identity("runtime", rig.s)
    result = _reopen_in_new_process(rig, retry=True)
    assert result == {"started": not round_trip, "turns": int(not round_trip),
                      "receipt": "none" if round_trip else "settled", "installs": 1}


def test_pending_success_reopens_in_new_backend_without_reinstall(rig):
    rig.request()
    rig.setup()
    result = _reopen_in_new_process(rig)
    assert result == {"started": True, "turns": 1, "receipt": "settled", "installs": 0}
    assert len(rig.installs) == 1


def test_lost_terminal_receipt_stays_uncertain_across_restart(rig, monkeypatch):
    from tui_gateway.turn_marker import read_turn_marker
    rig.request()
    job = rig.setup()
    lifecycle = rig.module._load_runtime("lifecycle")
    write = lifecycle.atomic_json
    def fail_receipt(path, data):
        if (data.get("continuation") or {}).get("state") == "settled":
            raise OSError("deterministic receipt disk failure")
        return write(path, data)
    monkeypatch.setattr(lifecycle, "atomic_json", fail_receipt)
    assert rig.poll() is True
    assert len(rig.turns) == 1
    assert rig.flow.status(rig.service, "owner", job["id"])["continuation"] == "uncertain"
    marker = read_turn_marker(rig.home, "owner")
    assert marker and marker["auto_continue"] is False
    result = _reopen_in_new_process(rig)
    assert result == {"started": False, "turns": 0, "receipt": "uncertain", "installs": 0}


def test_native_poller_delivers_without_a_mounted_setup_widget(rig):
    rig.request()
    rig.setup()
    stop = rig.server._start_notification_poller("runtime", rig.s)
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if rig.flow.latest(rig.service, "owner")["continuation"] == "settled":
                break
            time.sleep(.02)
        assert rig.flow.latest(rig.service, "owner")["continuation"] == "settled"
    finally:
        stop.set()
        for event, thread in rig.server._notification_pollers:
            if event is stop:
                thread.join(5)
                assert not thread.is_alive()
    assert len(rig.turns) == 1


@pytest.mark.parametrize("change", ["conversation", "runtime-record", "source"])
def test_idle_admission_cannot_follow_a_changed_runtime(rig, monkeypatch, change):
    rig.request()
    job = rig.setup()
    continuation = rig.module._load_runtime("setup_continuation")
    deliver = continuation.deliver
    def raced(service, *, submit, **identity):
        def changed(message, **kwargs):
            if change == "conversation":
                rig.s["session_key"] = "replacement"
                rig.s["agent"].session_id = "replacement"
            elif change == "runtime-record":
                rig.server._sessions["runtime"] = dict(rig.s)
            else:
                rig.s["source"] = "tui"
            return submit(message, **kwargs)
        return deliver(service, submit=changed, **identity)
    monkeypatch.setattr(continuation, "deliver", raced)
    assert rig.poll() is False
    assert not rig.turns
    assert rig.flow.status(rig.service, "owner", job["id"])["continuation"] == "pending"



@pytest.mark.parametrize("boundary", ["before-submit", "native-admission"])
def test_stop_during_idle_admission_cancels_receipt_without_replay(rig, monkeypatch, boundary):
    rig.request()
    job = rig.setup()
    before = list(rig.s["history"])
    interrupts = []
    rig.s["agent"].hard_interrupt = lambda: interrupts.append(True)
    native_submit = rig.server._run_prompt_submit
    native_admit = rig.server._ensure_active_session_slot

    def interrupt():
        assert rig.s["running"]
        rig.server._interrupt_session_turn("runtime", rig.s)

    def raced_submit(*args, **kwargs):
        if boundary == "before-submit":
            interrupt()
        return native_submit(*args, **kwargs)

    def raced_admit(*args, **kwargs):
        if boundary == "native-admission":
            interrupt()
        return native_admit(*args, **kwargs)

    with monkeypatch.context() as race:
        race.setattr(rig.server, "_run_prompt_submit", raced_submit)
        race.setattr(rig.server, "_ensure_active_session_slot", raced_admit)
        assert rig.poll() is False
    assert interrupts and not rig.turns
    assert rig.s["history"] == before
    assert not rig.s["running"]
    assert rig.flow.status(rig.service, "owner", job["id"])["continuation"] == "cancelled"
    assert rig.poll() is False
    assert _reopen_in_new_process(rig) == {
        "started": False, "turns": 0, "receipt": "cancelled", "installs": 0}


@pytest.mark.parametrize("boundary", ["native-rejection", "ownership-refusal", "wrapper-release", "wrapper-error"])
def test_cancelled_continuation_cannot_release_a_new_ordinary_turn(rig, monkeypatch, boundary):
    rig.request()
    job = rig.setup()
    entered, release = threading.Event(), threading.Event()
    native_submit = rig.server._run_prompt_submit
    model = rig.s["agent"].run_conversation
    ordinary = "Continue normal editing, not the private test"

    def held_model(message, **kwargs):
        assert message == ordinary
        entered.set()
        assert release.wait(10)
        return model(message, **kwargs)

    rig.s["agent"].run_conversation = held_model

    def start_ordinary():
        error, _ = rig.server._lock_in_submit_turn(
            "ordinary", "runtime", rig.s, ordinary, {}, False, [], None, None)
        assert error is None
        assert native_submit("ordinary", "runtime", rig.s, ordinary)
        assert entered.wait(5)

    def raced(*args, **kwargs):
        rig.server._interrupt_session_turn("runtime", rig.s)
        if boundary == "wrapper-release":
            rejected = native_submit(*args, **kwargs)
            assert rejected is False
            start_ordinary()
            return rejected
        start_ordinary()
        if boundary == "wrapper-error":
            raise RuntimeError("ambiguous stale submission failure")
        if boundary == "ownership-refusal":
            with monkeypatch.context() as refused:
                refused.setattr(rig.server, "_ensure_active_session_slot",
                                lambda *a: SimpleNamespace(reason="stale admission refused"))
                return native_submit(*args, **kwargs)
        return native_submit(*args, **kwargs)

    try:
        with monkeypatch.context() as race:
            race.setattr(rig.server, "_run_prompt_submit", raced)
            assert rig.server._poll_plugin_idle_once("runtime", rig.s) is False
        assert rig.s["running"] is True, "stale rejection released a newer live turn"
        assert rig.s["_run_thread"].is_alive()
        assert rig.server._poll_plugin_idle_once("runtime", rig.s) is False
        expected = "uncertain" if boundary == "wrapper-error" else "cancelled"
        assert rig.flow.status(rig.service, "owner", job["id"])["continuation"] == expected
    finally:
        release.set()
        if thread := rig.s.get("_run_thread"):
            thread.join(10)
            assert not thread.is_alive()
    assert len(rig.turns) == 1 and rig.turns[0][2] == ordinary
    assert not rig.s["running"]


def test_request_from_native_model_tool_dispatch_is_continued(rig):
    from model_tools import handle_function_call
    agent = rig.s["agent"]
    model = agent.run_conversation
    replies = []
    def requesting_model(message, conversation_history=None, **kw):
        result = json.loads(handle_function_call("realm", {"action": "on", "kind": "realm"},
                                               session_id=agent.session_id, task_id="owner"))
        replies.append(result)
        return {"final_response": "Setup needed", "messages": [
            *(conversation_history or []), {"role": "user", "content": message},
            {"role": "assistant", "content": "Setup needed"}]}
    agent.run_conversation = requesting_model
    try:
        rig.s["running"] = True
        assert rig.server._run_prompt_submit("request", "runtime", rig.s, "Test the current task privately")
        rig.s["_run_thread"].join(10)
        assert not rig.s["_run_thread"].is_alive()
    finally:
        agent.run_conversation = model
    assert "deterministic prerequisite missing" in replies[0].get("error", "")
    rig.setup()
    assert rig.poll() is True
    assert len(rig.turns) == 1



def test_finalized_native_identity_cannot_mint_a_later_request(rig):
    rig.service.finalize(session_id="owner")
    rig.request()
    job = rig.setup()
    assert rig.flow.status(rig.service, "owner", job["id"])["continuation"] == "none"


def test_plugin_unload_removes_the_idle_consumer(rig):
    rig.request()
    rig.setup()
    rig.manager.unload()
    assert rig.poll() is False
    assert not rig.turns



@pytest.mark.parametrize("target", ["cua", "terminal"])
def test_first_explicit_target_request_records_native_intent(rig, target):
    from tools.computer_use.targets import resolve_target_context
    rig.service.owners.set_mode("owner", "realm")
    rig.manager.invoke_hook("on_session_start", **dict(
        rig.server._session_hook_identity(rig.s), task_id="owner"))
    with pytest.raises(rig.integration.SetupError, match="deterministic prerequisite missing"):
        if target == "cua":
            resolve_target_context("owner", "owner")
        else:
            from tools.terminal_targets import resolve_terminal_target
            resolve_terminal_target("realm", command="printf no-replay", session_id="owner", task_id="owner")
    rig.setup()
    assert rig.poll() is True
    assert len(rig.turns) == 1
    assert rig.poll() is False
    assert len(rig.turns) == 1


@pytest.mark.parametrize("target", ["cua", "terminal"])
def test_unpublished_task_cannot_record_native_intent(rig, target):
    from hermes_cli.session_execution import SessionExecutionError
    from tools.computer_use.targets import resolve_target_context
    rig.service.owners.set_mode("owner", "realm")
    before = {p: (p.read_bytes(), p.stat().st_mode, p.stat().st_ino)
              for p in rig.service.owners.root.iterdir() if p.is_file()}
    with pytest.raises(SessionExecutionError, match="target selection failed"):
        if target == "cua":
            resolve_target_context("owner", "owner")
        else:
            from tools.terminal_targets import resolve_terminal_target
            resolve_terminal_target("realm", command="printf no-replay", session_id="owner", task_id="owner")
    assert before == {p: (p.read_bytes(), p.stat().st_mode, p.stat().st_ino)
                      for p in rig.service.owners.root.iterdir() if p.is_file()}
    with rig.service.owners.connection(readonly=True) as db:
        assert db.execute("SELECT setup_intent FROM owners WHERE id='owner'").fetchone()[0] is None
    assert not rig.starts
    assert rig.poll() is False
    assert not rig.turns
