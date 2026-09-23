"""Installer-owned stages use the existing owner-bound setup job, not logs."""
import hashlib
import io

import os
from pathlib import Path
import runpy
import sys
import tarfile
import threading
import time

import pytest
from realms_test_paths import HERMES_ROOT, PLUGIN_ROOT

ROOT = HERMES_ROOT
PLUGIN = PLUGIN_ROOT
load = runpy.run_path(str(PLUGIN / "realms/_binding.py"))["load_runtime"]
pytestmark = pytest.mark.linux_only


def driver_archive(tmp_path, monkeypatch):
    installer = load("install_driver")
    executed = tmp_path / "executed"
    data = (f"#!{sys.executable}\nfrom pathlib import Path\n"
            f"Path({str(executed)!r}).touch()\nprint('cua-driver {installer.VERSION}')\n").encode()
    archive = tmp_path / "release.tar.gz"
    with tarfile.open(archive, "w:gz") as package:
        member = tarfile.TarInfo(installer.MEMBER)
        member.size = len(data)
        package.addfile(member, io.BytesIO(data))
    monkeypatch.setattr(installer, "ARCHIVE_SHA256", hashlib.sha256(archive.read_bytes()).hexdigest())
    monkeypatch.setattr(installer, "BINARY_SHA256", hashlib.sha256(data).hexdigest())
    return installer, archive, executed


@pytest.mark.parametrize("with_progress", [True, False])
def test_installer_reports_real_operations_without_changing_legacy_use(tmp_path, monkeypatch, with_progress):
    installer, archive, executed = driver_archive(tmp_path, monkeypatch)
    target = tmp_path / "profile/bin/cua-driver"
    stages = []

    def progress(stage):
        assert not executed.exists(), "stage arrived after execution finished"
        assert not target.exists(), "fresh installation was already published"
        stages.append(stage)

    def download(url, *, timeout):
        assert url == installer.URL and timeout == 60
        assert stages == (["downloading"] if with_progress else [])
        return archive.open("rb")

    monkeypatch.setattr(installer.urllib.request, "urlopen", download)
    kwargs = {"progress": progress} if with_progress else {}
    assert installer.install(target=target, **kwargs) == str(target)
    assert stages == (["downloading", "verifying", "preparing"] if with_progress else [])
    assert executed.exists() and installer.execution_verified(target)


def test_reused_driver_only_reports_verification_and_bad_archive_never_prepares(tmp_path, monkeypatch):
    installer, archive, executed = driver_archive(tmp_path, monkeypatch)
    target = tmp_path / "driver"
    installer.install(archive=archive, target=target)
    identity = target.stat()
    stages = []
    monkeypatch.setattr(installer.urllib.request, "urlopen", lambda *a, **kw: pytest.fail("reused driver downloaded"))
    installer.install(target=target, progress=stages.append)
    assert stages == ["verifying"]
    assert target.stat() == identity
    archive.write_bytes(b"bad archive")
    stages.clear()
    with pytest.raises(ValueError, match="checksum"):
        installer.install(archive=archive, target=target, progress=stages.append)
    assert stages == ["verifying"]
    assert installer.execution_verified(target)


def test_progress_only_changes_current_owned_job_message_and_cannot_undo_cancel(tmp_path, monkeypatch):
    from dataclasses import asdict

    flow = load("setup_flow")
    service = load("integration").RealmIntegration(tmp_path / "profile")
    service.bind(session_origin="fresh", session_id="owner", runtime_session_id="runtime")
    plan = {"home": str(service.home), "owner": "owner", "kind": "realm",
            "config": asdict(load("config").Config.load(service.home)), "ready": False,
            "action": "repair", "summary": "Inert fixture", "details": [], "packages": [], "blockers": []}
    monkeypatch.setattr(flow, "build_plan", lambda *a: plan)
    entered, release = threading.Event(), threading.Event()

    def install(*a, **kw):
        entered.set()
        assert release.wait(10)

    monkeypatch.setattr(flow, "install_plan", install)
    monkeypatch.setattr(flow, "verify_ready", lambda *a: pytest.fail("cancelled job verified"))
    proposal = flow.prepare(service, "owner", "realm")
    job = flow.start(service, "owner", "realm", proposal["consent"], {"runtime_session_id": "runtime"})
    try:
        assert entered.wait(5)
        before = flow._read(service, job["id"])
        flow.installer_progress(service.home, job["id"], "owner", "downloading")
        after = flow._read(service, job["id"])
        assert after.pop("message").startswith("Downloading")
        before.pop("message")
        assert after == before
        assert flow.status(service, "owner", job["id"])["cancellable"]
        with pytest.raises(PermissionError):
            flow.installer_progress(service.home, job["id"], "other", "verifying")
        accepted = flow.cancel(service, "owner", job["id"])
        assert accepted["state"] == "cancelling"
        frozen = flow._path(service, job["id"]).read_bytes()
        with pytest.raises(flow.SetupCancelled):
            flow.installer_progress(service.home, job["id"], "owner", "preparing")
        assert flow._path(service, job["id"]).read_bytes() == frozen
    finally:
        flow.cancel(service, "owner", job["id"])
        release.set()
        for thread in threading.enumerate():
            if thread.name == "realms-setup-" + job["id"]:
                thread.join(10)
                assert not thread.is_alive()
    assert flow.status(service, "owner", job["id"])["state"] == "cancelled"


@pytest.mark.parametrize("refusal", ["succeeded", "failed", "activating", "stale-job", "revoked", "retired", "foreign-home", "vm", "ready", "legacy"])
def test_progress_refuses_obsolete_or_unauthorized_receipts_without_writes(tmp_path, refusal):
    flow = load("setup_flow")
    service = load("integration").RealmIntegration(tmp_path / "profile")
    service.bind(session_origin="fresh", session_id="owner")
    lock = flow._lock(service)
    job_id = "a" * 32
    record = {"id": job_id, "home": str(service.home), "owner": "owner", "kind": "realm",
              "state": "running", "cancel_protocol": 1, "message": "original",
              "activation_generation": service.owners.setup_generation("owner")}
    stage = "preparing"
    active_id = job_id
    if refusal in {"succeeded", "failed"}:
        record["state"] = refusal
    elif refusal == "activating":
        record["activation_started"] = True
    elif refusal == "stale-job":
        active_id = "b" * 32
    elif refusal == "revoked":
        service.owners.setup_generation("owner", revoke=True)
    elif refusal == "retired":
        flow._release(lock)
        lock = None
    elif refusal == "foreign-home":
        record["home"] = str(tmp_path / "foreign")
    elif refusal == "vm":
        record["kind"] = "omarchy-vm"
    elif refusal == "ready":
        stage = "ready"
    elif refusal == "legacy":
        record.pop("cancel_protocol")
    flow.atomic_json(flow._path(service, job_id), record)
    flow.atomic_json(flow._root(service) / "active.json", {"id": active_id})
    before = flow._path(service, job_id).read_bytes()
    try:
        with pytest.raises((PermissionError, ValueError)):
            flow.installer_progress(service.home, job_id, "owner", stage)
        assert flow._path(service, job_id).read_bytes() == before
    finally:
        if lock is not None:
            flow._release(lock)


def wait_for(predicate):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if value := predicate():
            return value
        time.sleep(.01)
    pytest.fail("installer fixture did not reach its operation barrier")


@pytest.mark.parametrize("cancel", [False, True])
def test_supervised_installer_child_publishes_live_stages_before_ready(tmp_path, monkeypatch, cancel):
    from dataclasses import asdict
    import select

    installer, archive, executed = driver_archive(tmp_path, monkeypatch)
    flow, worker = load("setup_flow"), load("setup_worker")
    service = load("integration").RealmIntegration(tmp_path / "profile")
    service.bind(session_origin="fresh", session_id="owner", runtime_session_id="runtime")
    plan = {"home": str(service.home), "owner": "owner", "kind": "realm",
            "config": asdict(load("config").Config.load(service.home)), "ready": False,
            "action": "install", "summary": "Inert archive fixture", "details": [], "packages": [], "blockers": []}
    monkeypatch.setattr(flow, "build_plan", lambda *a: plan)
    # Instrument only external-operation seams in the real fresh-exec child.
    fixture = tmp_path / "child_fixture.py"
    fixture.write_text(f'''import os, runpy, time
from pathlib import Path
load = runpy.run_path({str(PLUGIN / "realms/_binding.py")!r})["load_runtime"]
installer = load("install_driver")
installer.ARCHIVE_SHA256 = {installer.ARCHIVE_SHA256!r}
installer.BINARY_SHA256 = {installer.BINARY_SHA256!r}
root = Path({str(tmp_path)!r})
def gate(stage):
    (root / (stage + ".pid")).write_text(str(os.getpid()))
    deadline = time.monotonic() + 8
    while not (root / (stage + ".release")).exists():
        if time.monotonic() > deadline: raise RuntimeError("fixture deadline")
        time.sleep(.01)
def download(*a, **kw):
    gate("downloading")
    return Path({str(archive)!r}).open("rb")
installer.urllib.request.urlopen = download
archive_open = installer.tarfile.open
def open_archive(*a, **kw):
    gate("verifying")
    return archive_open(*a, **kw)
installer.tarfile.open = open_archive
verify = installer.verify_execution
def execution(*a, **kw):
    gate("preparing")
    return verify(*a, **kw)
installer.verify_execution = execution
load("integration").setup_status = lambda driver_executable: {{"ready": installer.execution_verified(driver_executable), "message": "fixture dependencies"}}
load("manager").Manager.doctor = lambda self: {{"ok": True}}
''')
    run_child = worker.run_child

    def instrument(argv, **kwargs):
        assert argv[1:3] == ["-I", "-c"] and not kwargs.get("privileged")
        argv = list(argv)
        argv[3] = f"import sys,runpy; sys.path.insert(0,sys.argv[1]); runpy.run_path({str(fixture)!r}); " + argv[3]
        kwargs["timeout"] = 15
        run_child(argv, **kwargs)

    monkeypatch.setattr(worker, "run_child", instrument)
    effects = []
    verify_entered, verify_release = threading.Event(), threading.Event()
    activate_entered, activate_release = threading.Event(), threading.Event()

    def verify_ready(*a):
        assert executed.exists() and installer.execution_verified(service.driver_executable)
        effects.append("verified")
        verify_entered.set()
        assert verify_release.wait(10)

    def activate(*a):
        effects.append("activated")
        activate_entered.set()
        assert activate_release.wait(10)

    monkeypatch.setattr(flow, "verify_ready", verify_ready)
    monkeypatch.setattr(service, "activate_setup", activate)
    job = flow.start(service, "owner", "realm", flow.prepare(service, "owner", "realm")["consent"], {"runtime_session_id": "runtime"})
    child_fd = None
    try:
        for stage in ("downloading", "verifying", "preparing"):
            witness = tmp_path / (stage + ".pid")
            wait_for(witness.exists)
            if child_fd is None:
                pid = int(witness.read_text())
                assert pid != os.getpid()
                child_fd = load("setup_process").open_pidfd(pid)
            snapshot = flow.status(service, "owner", job["id"])
            assert snapshot["state"] == "running" and snapshot["cancellable"]
            assert snapshot["message"].startswith(stage.capitalize()), snapshot
            assert not effects
            with pytest.raises(PermissionError):
                flow.status(service, "other", job["id"])
            if cancel and stage == "verifying":
                assert flow.cancel(service, "owner", job["id"])["state"] == "cancelling"
                wait_for(lambda: flow.status(service, "owner", job["id"])["state"] == "cancelled")
                assert not executed.exists() and not effects
                break
            (tmp_path / (stage + ".release")).touch()
        if not cancel:
            assert verify_entered.wait(10)
            assert flow.status(service, "owner", job["id"])["message"].startswith("Verifying readiness")
            verify_release.set()
            assert activate_entered.wait(10)
            snapshot = flow.status(service, "owner", job["id"])
            assert snapshot["state"] == "running" and not snapshot["cancellable"]
            assert snapshot["message"].startswith("Starting")
            activate_release.set()
            wait_for(lambda: flow.status(service, "owner", job["id"])["state"] == "succeeded")
            assert effects == ["verified", "activated"]
        poll = select.poll()
        poll.register(child_fd, select.POLLIN)
        assert poll.poll(5000), "supervised installer did not retire"
    finally:
        flow.cancel(service, "owner", job["id"])
        verify_release.set()
        activate_release.set()
        for stage in ("downloading", "verifying", "preparing"):
            (tmp_path / (stage + ".release")).touch()
        for thread in threading.enumerate():
            if thread.name == "realms-setup-" + job["id"]:
                thread.join(20)
                assert not thread.is_alive()
        if child_fd is not None:
            os.close(child_fd)
    lock = flow._lock(service)
    flow._release(lock)
    assert not service.manager.list(), "backend fixture started a desktop"


def test_standalone_setup_keeps_no_callback_contract_and_verification_receipt(tmp_path, monkeypatch):
    installer, archive, executed = driver_archive(tmp_path, monkeypatch)
    home = tmp_path / "standalone-profile"
    target = installer.profile_target(home)
    installer.install(home=home, archive=archive)
    original = installer.install
    calls = []

    def legacy_install(*, home):
        calls.append(home)
        return original(home=home)

    monkeypatch.setattr(installer, "install", legacy_install)
    monkeypatch.setattr(load("integration"), "setup_status", lambda driver_executable: {
        "ready": installer.execution_verified(driver_executable), "message": "fixture dependencies"})
    monkeypatch.setattr(load("manager").Manager, "doctor", lambda self: {"ok": True})
    setup = runpy.run_path(str(PLUGIN / "setup.py"))
    assert not setup["describe"](home)["ready"]
    setup["run"](home)
    assert calls == [home]
    assert setup["describe"](home)["ready"]
    assert installer.execution_verified(target) and executed.exists()


def test_worker_refuses_foreign_cancel_path_before_spawning(tmp_path, monkeypatch):
    from dataclasses import asdict

    worker = load("setup_worker")
    home = tmp_path / "profile"
    plan = {"home": str(home), "owner": "owner", "kind": "realm", "packages": [],
            "config": asdict(load("config").Config.load(home)), "action": "install"}
    monkeypatch.setattr(worker, "run_child", lambda *a, **kw: pytest.fail("foreign job spawned"))
    with pytest.raises(ValueError, match="profile"):
        worker.install_plan(plan, lambda phase: None, cancel_path=tmp_path / ("a" * 32 + ".json"))
