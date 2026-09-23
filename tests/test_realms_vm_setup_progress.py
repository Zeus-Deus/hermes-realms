"""Inert subprocess operations: stage transport, NOT native VM qualification."""
from dataclasses import asdict
from pathlib import Path
import runpy
import subprocess
import threading
import time

import pytest
from realms_test_paths import HERMES_ROOT, PLUGIN_ROOT

ROOT = HERMES_ROOT
PLUGIN = PLUGIN_ROOT
load = runpy.run_path(str(PLUGIN / "realms/_binding.py"))["load_runtime"]
pytestmark = pytest.mark.linux_only


def wait_for(predicate):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if result := predicate():
            return result
        time.sleep(.01)
    pytest.fail("inert VM operation barrier not reached")


@pytest.mark.parametrize("cancel", [False, True])
def test_vm_worker_live_progress_is_owned_and_cancellable_before_ready(tmp_path, monkeypatch, cancel):
    flow, worker = load("setup_flow"), load("setup_worker")
    service = load("integration").RealmIntegration(tmp_path / "profile")
    service.bind(session_origin="fresh", session_id="owner", runtime_session_id="runtime")
    plan = {"home": str(service.home), "owner": "owner", "kind": "omarchy-vm",
            "config": asdict(load("config").Config.load(service.home)), "ready": False,
            "action": "install", "summary": "Inert VM fixture", "details": [], "packages": [], "blockers": []}
    monkeypatch.setattr(flow, "build_plan", lambda *a: plan)
    monkeypatch.setattr(flow, "require_install_resources", lambda *a: None)
    monkeypatch.setattr(worker, "require_install_resources", lambda *a: None)
    fixture = tmp_path / "child_fixture.py"
    fixture.write_text(f'''import os, runpy, time
from pathlib import Path
from contextlib import nullcontext
load = runpy.run_path({str(PLUGIN / "realms/_binding.py")!r})["load_runtime"]
vm = load("vm_manager")
root = Path({str(tmp_path)!r})
vm.require_resources = lambda *a, **kw: None
vm._base_runtime = lambda generation: root / "runtime"
vm.VmManager._free_port = lambda *a: 2345
vm.VmManager._force_stop = lambda *a: None
load("setup_process").vm_lifetime = lambda *a, **kw: nullcontext()
# Only the external vendor-operation boundary is inert. Real manager pin,
# subprocess environment, worker and owner-bound progress publisher remain.
run = vm.subprocess.run
def inert(argv, **kw):
    if argv[0] == str(vm.VENDORED_SCRIPT):
        if argv[1] == "stop":
            argv = ["/bin/true"]
        else:
            assert argv[1:] == ["install"], "verification-bypassing --iso"
            argv = ["/bin/bash", "-c", r''' + "'''" + r'''
source "$1" --help >/dev/null
vm_running() { return 1; }
pacman() { return 0; }
latest_iso_version() { printf fixture; }
openssl() { printf inert; }
gate() {
  printf '%s' "$BASHPID" > "$2/$1.pid"
  for ((i=0;i<1500;i++)); do
    [[ -f "$2/$1.release" ]] && return 0
    sleep .01
  done
  return 1
}
curl() {
  local out=""
  while (($#)); do
    if [[ $1 == -o ]]; then out=$2; shift; fi
    shift
  done
  gate downloading "$2_ROOT"
  printf inert > "$out"
}
# This success fixture does not qualify GPG signatures. A separate failure
# fixture exercises real verify_iso; no host keyring or network is touched.
verify_iso() { gate verifying "$2_ROOT"; }
build_cidata() { gate preparing "$2_ROOT"; }
qemu-img() { printf inert > "$DISK"; }
cp() { return 0; }
start_qemu() { return 0; }
wait_for_ssh_as_guest_user() { return 0; }
cmd_provision() { return 0; }
cmd_install
''' + "'''" + f'''.replace('$2_ROOT', {str(tmp_path)!r}), "fixture", str(vm.VENDORED_SCRIPT)]
    return run(argv, **kw)
vm.subprocess.run = inert
# The vendor refuses to prepare a guest without a public key; inert bytes only.
Path.home().joinpath(".ssh").mkdir(exist_ok=True)
Path.home().joinpath(".ssh/id_ed25519.pub").write_text("inert fixture, not a key")
''')
    run_child = worker.run_child

    def instrument(argv, **kwargs):
        assert argv[1:3] == ["-I", "-c"] and not kwargs.get("privileged")
        argv = list(argv)
        argv[3] = f"import sys,runpy; sys.path.insert(0,sys.argv[1]); runpy.run_path({str(fixture)!r}); " + argv[3]
        kwargs["env"]["HOME"] = str(tmp_path)
        # No systemd unit is created by the inert fixture.
        kwargs["cleanup_unit"] = None
        kwargs["timeout"] = 45
        run_child(argv, **kwargs)

    monkeypatch.setattr(worker, "run_child", instrument)
    effects = []
    verified, release_verify = threading.Event(), threading.Event()
    activated, release_activate = threading.Event(), threading.Event()

    def verify(*a):
        assert (service.home / "plugin-data/hermes-realms/vm/base/disk.qcow2").is_file()
        effects.append("verified")
        verified.set()
        assert release_verify.wait(15)

    def activate(*a):
        effects.append("activated")
        activated.set()
        assert release_activate.wait(15)

    monkeypatch.setattr(flow, "verify_ready", verify)
    monkeypatch.setattr(service, "activate_setup", activate)
    job = flow.start(service, "owner", "omarchy-vm", flow.prepare(service, "owner", "omarchy-vm")["consent"], {"runtime_session_id": "runtime"})
    try:
        for stage in ("downloading", "verifying", "preparing"):
            wait_for((tmp_path / (stage + ".pid")).exists)
            snapshot = flow.status(service, "owner", job["id"])
            assert snapshot["state"] == "running" and snapshot["cancellable"]
            assert snapshot["message"].startswith(stage.capitalize()), snapshot
            assert "driver" not in snapshot["message"] and not effects
            before = flow._read(service, job["id"])
            flow.installer_progress(service.home, job["id"], "owner", stage, kind="omarchy-vm")
            assert flow._read(service, job["id"]) == before
            with pytest.raises(PermissionError):
                flow.installer_progress(service.home, job["id"], "other", stage, kind="omarchy-vm")
            if cancel and stage == "verifying":
                assert flow.cancel(service, "owner", job["id"])["state"] == "cancelling"
                wait_for(lambda: flow.status(service, "owner", job["id"])["state"] == "cancelled")
                assert not effects
                frozen = flow._path(service, job["id"]).read_bytes()
                with pytest.raises(flow.SetupCancelled):
                    flow.installer_progress(service.home, job["id"], "owner", "preparing", kind="omarchy-vm")
                assert flow._path(service, job["id"]).read_bytes() == frozen
                break
            (tmp_path / (stage + ".release")).touch()
        if not cancel:
            assert verified.wait(15)
            assert flow.status(service, "owner", job["id"])["message"].startswith("Verifying readiness")
            release_verify.set()
            assert activated.wait(15)
            assert flow.status(service, "owner", job["id"])["state"] == "running"
            assert not flow.status(service, "owner", job["id"])["cancellable"]
            release_activate.set()
            wait_for(lambda: flow.status(service, "owner", job["id"])["state"] == "succeeded")
            assert effects == ["verified", "activated"]
    finally:
        flow.cancel(service, "owner", job["id"])
        release_verify.set()
        release_activate.set()
        for stage in ("downloading", "verifying", "preparing"):
            (tmp_path / (stage + ".release")).touch()
        for thread in threading.enumerate():
            if thread.name == "realms-setup-" + job["id"]:
                thread.join(30)
                assert not thread.is_alive()
    assert not service.manager.list()


@pytest.mark.parametrize("failure", ["download", "signature", "missing-signature", "hook"])
def test_vendor_failure_never_prepares_and_cached_iso_never_downloads(tmp_path, failure):
    """Execute vendor functions with inert commands, retaining real verify_iso."""
    vendor = load("vm_manager").VENDORED_SCRIPT
    script = r'''
source "$1" --help >/dev/null
vm_running() { return 1; }
pacman() { return 0; }
latest_iso_version() { printf fixture; }
openssl() { printf inert; }
curl() { printf curl >> "$HOME/operations"; return 1; }
gpg() { printf gpg >> "$HOME/operations"; return 1; }
build_cidata() { printf prepared >> "$HOME/operations"; exit 91; }
# Instrument the explicit stage boundary, not human log text.
installer_progress() {
  printf '%s\n' "$1" >> "$HOME/stages"
  [[ $MODE != hook ]]
}
mkdir -p "$HOME/.ssh" "$ISO_DIR"
printf inert > "$HOME/.ssh/id_ed25519.pub"
if [[ $MODE != download && $MODE != hook ]]; then
  printf inert > "$ISO_DIR/omarchy-fixture.iso"
  if [[ $MODE != missing-signature ]]; then
    printf inert > "$ISO_DIR/omarchy-fixture.iso.sig"
  fi
fi
cmd_install
'''
    result = subprocess.run(["/bin/bash", "-c", script, "fixture", str(vendor)],
                            env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "USER": "inert",
                                 "MODE": failure}, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0 and result.returncode != 91
    expected = ["downloading"] if failure in {"download", "hook"} else ["verifying"]
    assert (tmp_path / "stages").read_text().splitlines() == expected
    operations = tmp_path / "operations"
    assert (operations.read_text() if operations.exists() else "") == {
        "download": "curl", "signature": "gpg", "missing-signature": "", "hook": "",
    }[failure]
    assert not (tmp_path / ".local/state/omarchy/vm/disk.qcow2").exists()
