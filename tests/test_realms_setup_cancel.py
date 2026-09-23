"""Running Cancel through registered HTTP routes and owned setup supervision."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import runpy
import select
import subprocess
import sys
import threading
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from realms_test_paths import HERMES_ROOT, PLUGIN_ROOT

ROOT = HERMES_ROOT
PLUGIN = PLUGIN_ROOT
load = runpy.run_path(str(PLUGIN / "realms/_binding.py"))["load_runtime"]
pytestmark = pytest.mark.linux_only


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.02)
    pytest.fail("cancel fixture did not reach expected state")


@pytest.fixture
def rig(tmp_path, monkeypatch):
    flow = load("setup_flow")
    integration = load("integration")
    service = integration.RealmIntegration(tmp_path / "profile")
    monkeypatch.setenv("HERMES_HOME", str(service.home))
    service.bind(session_origin="fresh", session_id="owner", runtime_session_id="runtime")
    service.bind(session_origin="fresh", session_id="other", runtime_session_id="other-runtime")
    def plan(service, owner, kind):
        return {"home": str(service.home), "owner": owner, "kind": kind,
                "config": asdict(load("config").Config.load(service.home)),
                "ready": False, "action": "repair", "summary": "Inert fixture",
                "details": [], "packages": [], "blockers": []}
    monkeypatch.setattr(flow, "build_plan", plan)
    monkeypatch.setattr(integration, "get_integration", lambda: service)
    api = runpy.run_path(str(PLUGIN / "dashboard/plugin_api.py"))
    app = FastAPI()
    app.include_router(api["router"])
    with TestClient(app) as client:
        yield flow, service, client


def start(client):
    body = {"runtime_session_id": "runtime", "kind": "realm"}
    proposal = client.post("/realms/setup/prepare", json=body)
    assert proposal.status_code == 200
    response = client.post("/realms/setup/start", json={**body, "consent": proposal.json()["consent"]})
    assert response.status_code == 200
    return response.json()


def test_cancel_registered_job_is_owner_bound_idempotent_and_retains_work(rig, monkeypatch):
    flow, service, client = rig
    entered, release = threading.Event(), threading.Event()
    def install(plan, phase, **kwargs):
        phase("install")
        entered.set()
        assert release.wait(10)
    monkeypatch.setattr(flow, "install_plan", install)
    effects = []
    monkeypatch.setattr(flow, "verify_ready", lambda *a: effects.append("verify"))
    monkeypatch.setattr(service, "activate_setup", lambda *a: effects.append("activate"))
    retained = service.home / "retained-guest"
    retained.write_bytes(b"guest-only\x00work")
    before = retained.stat()
    job = start(client)
    assert entered.wait(5)
    url = f"/realms/setup/jobs/{job['id']}/cancel"
    try:
        assert client.post(url, json={"runtime_session_id": "other-runtime"}).status_code == 403
        assert client.post(url, json={"runtime_session_id": "runtime", "stored_session_id": "unknown"}).status_code == 403
        assert client.post(url, json={"runtime_session_id": "unknown"}).status_code == 403
        assert client.post(url, json={"runtime_session_id": "runtime", "home": "elsewhere"}).status_code == 422
        response = client.post(url, json={"runtime_session_id": "runtime"})
        assert response.status_code == 200
        assert response.json()["state"] == "cancelling"
        assert client.post(url, json={"runtime_session_id": "runtime"}).json() == response.json()
        assert flow.status(service, "owner", job["id"])["state"] == "cancelling"
        with pytest.raises(ValueError, match="active"):
            flow._lock(service)
    finally:
        release.set()
        for thread in threading.enumerate():
            if thread.name == "realms-setup-" + job["id"]:
                thread.join(10)
                assert not thread.is_alive()
    assert effects == []
    final = wait_for(lambda: (v if (v := flow.status(service, "owner", job["id"]))["state"] == "cancelled" else None))
    assert client.post(url, json={"runtime_session_id": "runtime"}).json() == final
    assert flow.latest(service, "owner") == final
    assert retained.read_bytes() == b"guest-only\x00work" and retained.stat().st_ino == before.st_ino
    assert not service.manager.list()
    assert service.owners.mode("owner", "ask") == "ask"
    assert json.loads(flow._path(service, job["id"]).read_text())["state"] == "cancelled"
    assert Path(load("integration").__file__).is_relative_to(PLUGIN)
    import hermes_cli.session_execution as core
    assert Path(core.__file__).is_relative_to(ROOT), core.__file__


@pytest.mark.parametrize("winner", ["cancel", "activate"])
def test_cancel_activation_race_has_one_commit_boundary(rig, monkeypatch, winner):
    flow, service, client = rig
    entered, release = threading.Event(), threading.Event()
    effects = []
    monkeypatch.setattr(flow, "install_plan", lambda *a, **kw: None)
    def verify(*args):
        if winner == "cancel":
            entered.set()
            assert release.wait(10)
    def activate(*args):
        record = flow._read(service, flow.latest(service, "owner")["id"])
        assert record["activation_started"] is True
        effects.append("activated")
        if winner == "activate":
            entered.set()
            assert release.wait(10)
    monkeypatch.setattr(flow, "verify_ready", verify)
    monkeypatch.setattr(service, "activate_setup", activate)
    job = start(client)
    assert entered.wait(5)
    answers = []
    canceller = threading.Thread(target=lambda: answers.append(flow.cancel(service, "owner", job["id"])))
    try:
        canceller.start()
        if winner == "cancel":
            wait_for(lambda: flow.status(service, "owner", job["id"])["state"] == "cancelling")
        else:
            wait_for(lambda: answers)
            assert answers[0]["state"] == "running"
            assert answers[0]["cancellable"] is False
    finally:
        release.set()
        canceller.join(10)
        for thread in threading.enumerate():
            if thread.name == "realms-setup-" + job["id"]:
                thread.join(10)
                assert not thread.is_alive()
    assert not canceller.is_alive()
    expected = "cancelled" if winner == "cancel" else "succeeded"
    assert flow.status(service, "owner", job["id"])["state"] == expected
    assert effects == ([] if winner == "cancel" else ["activated"])
    # A later same-owner job has independent cancellation authority.
    monkeypatch.setattr(flow, "verify_ready", lambda *a: None)
    monkeypatch.setattr(service, "activate_setup", lambda *a: effects.append("replacement"))
    next_job = start(client)
    wait_for(lambda: flow.status(service, "owner", next_job["id"])["state"] == "succeeded")
    assert flow.cancel(service, "owner", job["id"])["state"] == expected
    assert flow.status(service, "owner", next_job["id"])["state"] == "succeeded"
    assert effects[-1] == "replacement"


@pytest.mark.parametrize("superseded", [False, True])
def test_cancel_revokes_only_its_captured_activation_generation(rig, monkeypatch, superseded):
    flow, service, client = rig
    entered, release = threading.Event(), threading.Event()
    def install(*args, **kwargs):
        entered.set()
        assert release.wait(10)
    monkeypatch.setattr(flow, "install_plan", install)
    effects = []
    monkeypatch.setattr(service, "_activate", lambda *a, **kw: effects.append("activated"))
    job = start(client)
    try:
        assert entered.wait(5)
        captured = flow._read(service, job["id"])["activation_generation"]
        replacement = service.owners.setup_generation("owner", revoke=True) if superseded else None
        assert flow.cancel(service, "owner", job["id"])["state"] == "cancelling"
        if superseded:
            assert service.owners.setup_generation("owner") == replacement
        else:
            assert service.owners.setup_generation("owner") != captured
        with pytest.raises(load("integration").OwnerError, match="revoked"):
            service.activate_setup("owner", "realm", asdict(load("config").Config.load(service.home)),
                                   captured, {"runtime_session_id": "runtime"})
        assert effects == []
    finally:
        release.set()
        for thread in threading.enumerate():
            if thread.name == "realms-setup-" + job["id"]:
                thread.join(10)
                assert not thread.is_alive()


def test_activation_admission_is_visible_as_too_late_to_cancel(rig, monkeypatch):
    flow, service, client = rig
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(flow, "install_plan", lambda *a, **kw: None)
    monkeypatch.setattr(flow, "verify_ready", lambda *a: None)
    def activate(*args):
        entered.set()
        assert release.wait(10)
    monkeypatch.setattr(service, "activate_setup", activate)
    job = start(client)
    try:
        assert entered.wait(5)
        current = flow.status(service, "owner", job["id"])
        assert current["cancellable"] is False
        # No synchronous wait for a target boot after the commit boundary won.
        assert flow.cancel(service, "owner", job["id"]) == current
    finally:
        release.set()
        for thread in threading.enumerate():
            if thread.name == "realms-setup-" + job["id"]:
                thread.join(10)
                assert not thread.is_alive()
    assert flow.status(service, "owner", job["id"])["state"] == "succeeded"


def test_old_running_job_without_cancel_control_is_not_claimed_cancellable(rig):
    flow, service, client = rig
    lock = flow._lock(service)
    record = {"id": "d" * 32, "owner": "owner", "home": str(service.home),
              "kind": "realm", "state": "running", "created_at": time.time()}
    flow.atomic_json(flow._path(service, record["id"]), record)
    flow.atomic_json(flow._root(service) / "active.json", {"id": record["id"]})
    try:
        current = flow.status(service, "owner", record["id"])
        assert current["cancellable"] is False
        assert flow.cancel(service, "owner", record["id"]) == current
        assert flow._read(service, record["id"]) == record
    finally:
        flow._release(lock)


def test_cancel_refuses_copied_profile_receipt_and_invalid_jobs(rig, tmp_path):
    flow, service, client = rig
    foreign = load("integration").RealmIntegration(tmp_path / "foreign")
    foreign.bind(session_origin="fresh", session_id="owner", runtime_session_id="runtime")
    record = {"id": "b" * 32, "owner": "owner", "home": str(service.home),
              "kind": "realm", "state": "running", "created_at": time.time()}
    flow._root(foreign, create=True)
    flow.atomic_json(flow._path(foreign, record["id"]), record)
    with pytest.raises(PermissionError, match="ownership"):
        flow.cancel(foreign, "owner", record["id"])
    assert json.loads(flow._path(foreign, record["id"]).read_text()) == record
    assert not flow._path(foreign, record["id"]).with_suffix(".lock").exists()
    assert client.post("/realms/setup/jobs/not-a-job/cancel", json={"runtime_session_id": "runtime"}).status_code == 409
    assert client.post("/realms/setup/jobs/" + "c" * 32 + "/cancel", json={"runtime_session_id": "runtime"}).status_code == 404


def test_supervisor_keeps_owned_leader_unreaped_until_group_cleanup(tmp_path, monkeypatch):
    lifetime = load("setup_process")
    original = lifetime._kill_group
    observed = []
    def owned_signal(process):
        assert process.returncode is None, "process-group authority was reaped before signalling"
        observed.append(process.pid)
        original(process)
    monkeypatch.setattr(lifetime, "_kill_group", owned_signal)
    fd = lifetime.open_pidfd(os.getpid())
    try:
        assert lifetime.supervise([sys.executable, "-c", "pass"], {"PATH": "/usr/bin:/bin"}, 5, fd) == 0
        assert observed
    finally:
        os.close(fd)


def test_cancel_does_not_claim_rollback_of_interrupted_activation(rig):
    flow, service, client = rig
    lock = flow._lock(service)
    record = {"id": "a" * 32, "owner": "owner", "home": str(service.home),
              "kind": "realm", "state": "running", "created_at": time.time(),
              "cancel_protocol": 1, "activation_started": True, "message": "Starting this conversation's realm…"}
    flow.atomic_json(flow._path(service, record["id"]), record)
    flow.atomic_json(flow._root(service) / "active.json", {"id": record["id"]})
    flow._release(lock)  # Backend disappeared at an uncertain activation boundary.
    result = client.post(f"/realms/setup/jobs/{record['id']}/cancel", json={"runtime_session_id": "runtime"})
    assert result.status_code == 200
    assert result.json()["state"] == "failed"
    assert result.json()["error"] == "interrupted"
    assert json.loads(flow._path(service, record["id"]).read_text()) == record


def test_privileged_policy_drains_real_transaction_without_later_phases(rig, tmp_path, monkeypatch):
    flow, service, client = rig
    worker = load("setup_worker")
    witness, done, release = [tmp_path / name for name in ("started", "done", "release")]
    calls = []
    original = worker.run_child
    def harmless_package(argv, **kwargs):
        calls.append((argv, kwargs))
        assert kwargs["privileged"] is True
        original(["/usr/bin/timeout", "--signal=TERM", "--kill-after=1s", "5s",
                  sys.executable, "-c", "import pathlib,time\n"
                  f"pathlib.Path({str(witness)!r}).touch()\n"
                  f"while not pathlib.Path({str(release)!r}).exists(): time.sleep(.02)\n"
                  f"pathlib.Path({str(done)!r}).touch()"], **kwargs)
    plan_builder = flow.build_plan
    def plan(*args):
        return {**plan_builder(*args), "packages": ["labwc"]}
    monkeypatch.setattr(flow, "build_plan", plan)
    monkeypatch.setattr(worker, "run_child", harmless_package)
    effects = []
    monkeypatch.setattr(flow, "verify_ready", lambda *a: effects.append("verify"))
    monkeypatch.setattr(service, "activate_setup", lambda *a: effects.append("activate"))
    job = start(client)
    try:
        wait_for(witness.exists)
        # Fresh service: cancellation cannot depend on a worker's local Event.
        fresh = load("integration").RealmIntegration(service.home)
        assert flow.cancel(fresh, "owner", job["id"])["state"] == "cancelling"
        time.sleep(.15)
        assert not done.exists()
        assert flow.status(fresh, "owner", job["id"])["state"] == "cancelling"
        with pytest.raises(ValueError, match="active"):
            flow._lock(fresh)
    finally:
        release.touch()
        for thread in threading.enumerate():
            if thread.name == "realms-setup-" + job["id"]:
                thread.join(10)
                assert not thread.is_alive()
    assert done.exists(), "privileged policy killed the package transaction"
    assert flow.status(service, "owner", job["id"])["state"] == "cancelled"
    assert effects == [] and len(calls) == 1
    assert calls[0][0][:6] == ["/usr/bin/pkexec", "/usr/bin/timeout", "--signal=TERM", "--kill-after=10s", "1800s", "/usr/bin/pacman"]
    lock = flow._lock(service)
    flow._release(lock)


def test_cancel_retires_real_child_through_supervisor_before_deadline(rig, tmp_path, monkeypatch):
    flow, service, client = rig
    witness, completed = tmp_path / "pid", tmp_path / "completed"
    def install(plan, phase, **kwargs):
        phase("install")
        load("setup_worker").run_child(
            [sys.executable, "-c", "import os,pathlib,time; "
             f"pathlib.Path({str(witness)!r}).write_text(str(os.getpid())); "
             f"time.sleep(3); pathlib.Path({str(completed)!r}).touch()"],
            env={"PATH": "/usr/bin:/bin"}, timeout=5, **kwargs)
    monkeypatch.setattr(flow, "install_plan", install)
    effects = []
    monkeypatch.setattr(flow, "verify_ready", lambda *a: effects.append("verify"))
    monkeypatch.setattr(service, "activate_setup", lambda *a: effects.append("activate"))
    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(6)"]) as peer:
        job = start(client)
        fd = None
        try:
            wait_for(witness.exists)
            fd = load("setup_process").open_pidfd(int(witness.read_text()))
            watcher = select.poll()
            watcher.register(fd, select.POLLIN)
            assert not watcher.poll(0)
            response = client.post(f"/realms/setup/jobs/{job['id']}/cancel", json={"runtime_session_id": "runtime"})
            assert response.status_code == 200
            assert watcher.poll(1000), "Cancel did not retire the supervised child"
            wait_for(lambda: flow.status(service, "owner", job["id"])["state"] == "cancelled")
            assert not completed.exists()
            assert effects == []
            assert peer.poll() is None
        finally:
            if fd is not None:
                os.close(fd)
            for thread in threading.enumerate():
                if thread.name == "realms-setup-" + job["id"]:
                    thread.join(8)
                    assert not thread.is_alive()
            peer.terminate()
            peer.wait(timeout=5)
