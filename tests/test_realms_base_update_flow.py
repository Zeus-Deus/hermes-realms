"""Profile base maintenance must not acquire conversation authority."""
from pathlib import Path
import runpy
import subprocess
from types import SimpleNamespace

import pytest
from realms_test_paths import HERMES_ROOT, PLUGIN_ROOT

ROOT = HERMES_ROOT
load = runpy.run_path(str(PLUGIN_ROOT / "realms/_binding.py"))["load_runtime"]
pytestmark = pytest.mark.linux_only


def test_profile_update_prepare_needs_no_session_and_has_no_effects(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    flow = load("setup_flow")
    vm = load("vm_manager")

    def no_effect(*args, **kwargs):
        pytest.fail("Preparing an exact-release proposal must not resolve latest or spawn installation")

    monkeypatch.setattr(subprocess, "Popen", no_effect)
    monkeypatch.setattr(vm.VmManager, "latest_version", no_effect)
    binding = {"connectionId": "test-source", "profile": "test-profile"}
    before = sorted(str(path.relative_to(home)) for path in home.rglob("*"))
    proposal = flow.prepare_base_update(SimpleNamespace(home=home), "4.0.3", binding)

    assert proposal["operation"] == "vm-base-update"
    assert proposal["scope"] == "profile"
    assert proposal["kind"] == "omarchy-vm"
    assert proposal["release"] == "4.0.3"
    assert proposal["review_binding"] == binding
    assert isinstance(proposal["consent"], str) and proposal["consent"]
    assert isinstance(proposal["details"], list)
    assert isinstance(proposal["blockers"], list)
    assert not (home / "realms/sessions.sqlite3").exists()
    assert sorted(str(path.relative_to(home)) for path in home.rglob("*")) == before


BINDING = {"connectionId": "fixture-source", "profile": "fixture-profile"}


@pytest.fixture
def profile(tmp_path, monkeypatch):
    """Inert prerequisite observations; real config, receipts, locks and ledger."""
    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    plan = load("setup_plan")
    original = plan._prerequisite_plan
    def prerequisites(home, kind):
        result = original(home, kind)
        result.update(blockers=[], packages=[])
        return result
    monkeypatch.setattr(plan, "_prerequisite_plan", prerequisites)
    monkeypatch.setattr(load("setup_flow"), "require_install_resources", lambda plan: None)
    return SimpleNamespace(home=home)


def wait_for(predicate):
    import time
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.02)
    pytest.fail("Profile fixture did not reach expected state")


def join_job(job):
    import threading
    for thread in threading.enumerate():
        if thread.name == "realms-setup-" + job["id"]:
            thread.join(10)
            assert not thread.is_alive()


@pytest.mark.parametrize("release", [None, "v4.0.3", "04.0.3", "4.0.3\n", "4.0.3-rc1", "../../file"])
def test_invalid_release_is_rejected_before_local_effects(profile, release):
    flow = load("setup_flow")
    before = sorted(profile.home.rglob("*"))
    with pytest.raises(ValueError, match="release"):
        flow.prepare_base_update(profile, release, BINDING)
    assert sorted(profile.home.rglob("*")) == before


@pytest.mark.parametrize("drift", ["config", "base", "revision", "binding", "after-lock"])
def test_review_drift_refuses_launch(profile, monkeypatch, drift):
    flow = load("setup_flow")
    proposal = flow.prepare_base_update(profile, "4.0.3", BINDING)
    binding = dict(BINDING)
    if drift in {"config", "after-lock"}:
        def change():
            (profile.home / "config.yaml").write_text("plugins:\n  realms:\n    vm:\n      memory: 8192\n")
        if drift == "config":
            change()
        else:
            original = flow._lock
            def changed_lock(service):
                fd = original(service)
                change()
                return fd
            monkeypatch.setattr(flow, "_lock", changed_lock)
    elif drift == "base":
        base = load("config").vm_data_path(profile.home) / "base"
        base.mkdir(parents=True)
        (base / "base.json").write_text('{"built_at": 1}')
    elif drift == "revision":
        monkeypatch.setattr(load("vm_manager"), "VENDORED_SHA256", "changed")
    else:
        binding["connectionId"] = "replacement"
    def forbidden(*args, **kwargs):
        pytest.fail("Stale confirmation launched a worker")
    monkeypatch.setattr(flow.threading.Thread, "start", forbidden)
    with pytest.raises(ValueError, match="consent"):
        flow.start_base_update(profile, "4.0.3", binding, proposal["consent"])
    assert not list(flow._root(profile).glob("[0-9a-f]" * 32 + ".json"))
    assert not (profile.home / "realms/sessions.sqlite3").exists()


def test_profile_cancel_retires_real_supervised_child_without_session_authority(profile, monkeypatch):
    import json
    import os
    import sys
    flow, worker = load("setup_flow"), load("setup_worker")
    marker = profile.home / "harmless-child.pid"
    def install(plan, phase, **kwargs):
        assert plan["release"] == "4.0.3" and plan["owner"] is None
        phase("install")
        flow.installer_progress(profile.home, Path(kwargs["cancel_path"]).stem, None, "downloading", kind="omarchy-vm")
        worker.run_child([sys.executable, "-I", "-c",
                          "import os,time,pathlib; pathlib.Path(" + repr(str(marker)) + ").write_text(str(os.getpid())); time.sleep(60)"],
                         env={"PATH": "/usr/bin:/bin"}, timeout=30, **kwargs)
    monkeypatch.setattr(flow, "install_plan", install)
    def forbidden(*a, **kw):
        pytest.fail("Profile update touched conversation authority or latest")
    monkeypatch.setattr(load("integration"), "OwnershipStore", forbidden)
    monkeypatch.setattr(flow, "verify_ready", forbidden)
    monkeypatch.setattr(load("vm_manager").VmManager, "latest_version", forbidden)
    proposal = flow.prepare_base_update(profile, "4.0.3", BINDING)
    job = flow.start_base_update(profile, "4.0.3", BINDING, proposal["consent"])
    try:
        wait_for(marker.exists)
        current = flow._read(profile, job["id"])
        assert current["scope"] == "profile" and current["owner"] is None
        assert "activation_generation" not in current and "continuation" not in current
        assert flow.latest(profile, "owner") is None
        for call in (flow.status, flow.cancel):
            with pytest.raises(PermissionError):
                call(profile, None, job["id"])
        with pytest.raises(ValueError, match="active"):
            flow.start_base_update(profile, "4.0.3", BINDING, proposal["consent"])
        assert flow.cancel_base_update(profile, job["id"])["state"] in {"cancelling", "cancelled"}
    finally:
        flow.cancel_base_update(profile, job["id"])
        join_job(job)
    assert not Path(f"/proc/{int(marker.read_text())}").exists()
    final = flow.status_base_update(SimpleNamespace(home=profile.home), job["id"])
    assert final["state"] == "cancelled"
    assert flow.latest_base_update(SimpleNamespace(home=profile.home)) == final
    assert not (profile.home / "realms/sessions.sqlite3").exists()
    assert not list(load("config").vm_data_path(profile.home).glob("bases/*"))


@pytest.mark.parametrize("consent", ["非ascii", "", "a" * 65])
def test_invalid_consent_refuses_before_lock(profile, consent):
    flow = load("setup_flow")
    with pytest.raises(ValueError, match="consent"):
        flow.start_base_update(profile, "4.0.3", BINDING, consent)
    assert not flow._root(profile).exists()


def test_profile_job_never_enters_continuation_or_public_owner_state(profile):
    flow = load("setup_flow")
    fd = flow._lock(profile)
    record = {"id": "d" * 32, "operation": "vm-base-update", "scope": "profile",
              "owner": None, "home": str(profile.home), "kind": "omarchy-vm",
              "state": "succeeded", "created_at": 1, "message": "done"}
    try:
        flow.atomic_json(flow._path(profile, record["id"]), record)
        public = flow.status_base_update(profile, record["id"])
        assert "continuation" not in public and "owner" not in public
        # No session resolved: even then the profile discriminator must reject
        # before the continuation lock/owner activation boundary is accessed.
        service = SimpleNamespace(home=profile.home, _unloaded=False,
                                  owners=SimpleNamespace(resolve=lambda **kw: None))
        load("setup_continuation").deliver(service, submit=lambda *a: pytest.fail("continued profile job"),
                                           hermes_home=profile.home)
    finally:
        flow._release(fd)


@pytest.mark.parametrize("location", ["job", "active-lock", "active-json"])
def test_special_metadata_does_not_block_profile_status(profile, location):
    import os
    import sys
    flow = load("setup_flow")
    fd = flow._lock(profile)
    row = {"id": "e" * 32, "operation": "vm-base-update", "scope": "profile",
           "owner": None, "home": str(profile.home), "kind": "omarchy-vm",
           "state": "running", "created_at": 1, "message": "running"}
    flow.atomic_json(flow._path(profile, row["id"]), row)
    flow.atomic_json(flow._root(profile) / "active.json", {"id": row["id"]})
    path = {"job": flow._path(profile, row["id"]),
            "active-lock": flow._root(profile) / "active.lock",
            "active-json": flow._root(profile) / "active.json"}[location]
    path.unlink()
    os.mkfifo(path)
    code = """
import runpy,sys
from pathlib import Path
from types import SimpleNamespace
load=runpy.run_path(sys.argv[1])["load_runtime"]
try:
    load("setup_flow").status_base_update(SimpleNamespace(home=Path(sys.argv[2])),sys.argv[3])
except PermissionError:
    sys.exit(0)
sys.exit(1)
"""
    child = subprocess.Popen([sys.executable, "-I", "-c", code,
                              str(PLUGIN_ROOT / "realms/_binding.py"), str(profile.home), row["id"]],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert child.wait(timeout=3) == 0
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
        flow._release(fd)

