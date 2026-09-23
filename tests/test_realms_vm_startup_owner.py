"""Native owner lifetime, with diskless sleep units instead of QEMU.

Run only through scripts/run_tests.sh -m integration. Every signal targets a
retained Popen, every unit cleanup checks a creation-returned InvocationID.
No guest, Cua, display endpoint, installed Desktop or personal VM is used.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import time
# Also re-run as an isolated child (-I), where the tests directory is not importable.
_paths = __import__("runpy").run_path(str(Path(__file__).resolve().with_name("realms_test_paths.py")))
HERMES_ROOT, PLUGIN_ROOT = _paths["HERMES_ROOT"], _paths["PLUGIN_ROOT"]

ROOT = HERMES_ROOT
SELF = Path(__file__).resolve()
BINDING = PLUGIN_ROOT / "realms/_binding.py"


def load(name):
    return runpy.run_path(str(BINDING))["load_runtime"](name)


def _wait(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.05)
    raise AssertionError("native owner-lifetime observation timed out")


def _child(role, root):
    with (root / (role + ".stderr")).open("wb") as errors:
        return subprocess.Popen(
            [sys.executable, "-I", str(SELF), role, str(root)], cwd=ROOT,
            env={"PATH": "/usr/bin:/bin", "HOME": str(root),
                 "HERMES_HOME": str(root / "profile"), "LANG": "C.UTF-8"},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errors,
        )


def _start(root):
    vm = load("vm_manager")
    lifecycle = load("lifecycle")

    class DisklessManager(vm.VmManager):
        def base_status(self):
            return {"present": True}

        def _clone_base(self, session_dir, base_disk):
            # This test has no disk, firmware or credentials to clone.
            pass

        def _run_script(self, arguments, **kwargs):
            assert arguments == ["launch"]
            record = self.registry.records()[0]
            subprocess.run([
                "/usr/bin/systemd-run", "--user", "--quiet", "--collect",
                "--service-type=exec", "--unit=" + kwargs["unit"],
                "--property=RuntimeMaxSec=90", "/usr/bin/env", "-i",
                "PATH=/usr/bin:/bin", "HOME=" + str(root),
                "HERMES_HOME=" + str(root / "profile"), "/usr/bin/sleep", "90",
            ], env=lifecycle.host_control_env(), capture_output=True, check=True, timeout=15)
            info = lifecycle.scope_info(record["unit"])
            assert info["ActiveState"] == "active"
            record["invocation_id"] = info["InvocationID"]
            lifecycle.atomic_json(root / "launched.json", record)
            # The real vendor waits for SSH here. Never return a fake viewer
            # socket or readiness result; the owner is killed before this ends.
            time.sleep(60)
            raise AssertionError("fixture owner was not killed before readiness")

    DisklessManager(root / "profile").start("diskless-owner-test")


def _handoff(root):
    record = json.loads((root / "launched.json").read_text(encoding="utf-8"))
    manager = load("vm_manager").VmManager(root / "profile")
    manager._bind_to_owner(record)
    load("lifecycle").atomic_json(root / "handoff.json", record)
    time.sleep(60)


def _stop_created(unit, invocation):
    lifecycle = load("lifecycle")
    info = lifecycle.scope_info(unit)
    if info["ActiveState"] not in {"inactive", "failed"}:
        assert info["InvocationID"] == invocation, (unit, info)
        result = subprocess.run(
            ["/usr/bin/systemctl", "--user", "stop", unit],
            env=lifecycle.host_control_env(), capture_output=True, timeout=15,
        )
        # Collected-unit races are acceptable only with a terminal readback.
        after = lifecycle.scope_info(unit)
        assert after["ActiveState"] in {"inactive", "failed"}, (result, after)


@contextmanager
def _guest(root):
    process = _child("start", root)
    record = None
    try:
        def launched():
            assert process.poll() is None, (root / "start.stderr").read_text()
            path = root / "launched.json"
            return json.loads(path.read_text()) if path.exists() else None
        record = _wait(launched)
        yield process, record
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        if record is None:
            # Startup may fail before the fixture sees the vendor's receipt.
            # Preserve diagnostics and use only this disposable profile's
            # validated registry; never leave its pre-launch policy behind.
            manager = load("vm_manager").VmManager(root / "profile")
            with manager.registry.lock():
                for pending in manager.registry.records():
                    assert pending["home"] == str((root / "profile").resolve())
                    load("lifecycle").atomic_json(root / "startup-failure.json", pending)
                    manager._remove_locked(pending)
        if record:
            lifecycle = load("lifecycle")
            units = {record["unit"]: record["invocation_id"]}
            if record.get("owner_invocation_id"):
                units[load("vm_owner_lifetime").owner_unit(record)] = record["owner_invocation_id"]
            # Keep the initial state before fixture rescue, not just a finally
            # success flag that could conceal a stranded unit.
            lifecycle.atomic_json(root / "before-cleanup.json", {
                unit: lifecycle.scope_info(unit) for unit in units
            })
            for unit, invocation in units.items():
                _stop_created(unit, invocation)
            if record.get("owner_invocation_id"):
                load("vm_owner_lifetime").remove_dropin(record)
            load("vm_manager").validate_vm_record(record)
            shutil.rmtree(record["runtime_dir"])
            shutil.rmtree(record["session_dir"])


if __name__ == "__main__":
    # This is the test's administrative process, not a payload-only launcher.
    # Config.load must resolve this checkout's canonical config reader under -I.
    sys.path.insert(0, str(ROOT))
    role, directory = sys.argv[1:]
    {"start": _start, "handoff": _handoff}[role](Path(directory))
else:
    import pytest

    pytestmark = [pytest.mark.linux_only, pytest.mark.integration]

    def test_owner_sigkill_before_readiness_retires_starting_unit(tmp_path):
        lifecycle = load("lifecycle")
        with _guest(tmp_path) as (owner, record):
            assert record["status"] == "starting"
            owner.kill()
            owner.wait(timeout=10)
            # Observe before any finally stop; no application cleanup ran.
            _wait(lambda: lifecycle.scope_info(record["unit"])["ActiveState"] in {"inactive", "failed"})
            assert not (tmp_path / "handoff.json").exists()

    @pytest.mark.parametrize("rejection", ["retiring", "foreign", "foreign-receipt", "stale-owner"])
    def test_live_owner_handoff_preserves_invocation_and_refuses_invalid_receipt(tmp_path, rejection):
        lifecycle = load("lifecycle")
        with _guest(tmp_path) as (old_owner, record):
            new_owner = _child("handoff", tmp_path)
            try:
                def adopted():
                    assert new_owner.poll() is None, (tmp_path / "handoff.stderr").read_text()
                    return (tmp_path / "handoff.json").exists()
                _wait(adopted)
                old_owner.kill()
                old_owner.wait(timeout=10)
                lifetime = load("vm_owner_lifetime")
                # Observe the actual guard's acknowledgement of the new pidfd,
                # not a blind delay shorter than a polling loop.
                _wait(lambda: lifetime.read_receipt(record).get("watched_owner", {}).get("pid") == new_owner.pid)
                info = lifecycle.scope_info(record["unit"])
                assert info["ActiveState"] == "active"
                assert info["InvocationID"] == record["invocation_id"]
                assert lifecycle.scope_info(lifetime.owner_unit(record))["InvocationID"] == record["owner_invocation_id"]

                if rejection == "foreign":
                    bad = dict(record, home=str(tmp_path / "other-profile"))
                else:
                    bad = record
                    with lifetime.owner_lock(record):
                        receipt = lifetime.read_receipt(record)
                        if rejection == "retiring":
                            receipt["state"] = "retiring"
                        elif rejection == "foreign-receipt":
                            receipt["binding"]["home"] = str(tmp_path / "other-profile")
                        else:
                            receipt["owner"]["start_time"] += 1
                        lifecycle.atomic_json(lifetime.receipt_path(record), receipt)
                with pytest.raises(lifecycle.OwnershipError):
                    load("vm_manager").VmManager(tmp_path / "profile")._bind_to_owner(bad)
                # A refused foreign adoption must not itself stop the VM.
                if rejection == "foreign":
                    assert lifecycle.scope_info(record["unit"])["InvocationID"] == record["invocation_id"]
                    assert lifecycle.scope_info(record["unit"])["ActiveState"] == "active"
                new_owner.kill()
                new_owner.wait(timeout=10)
                _wait(lambda: lifecycle.scope_info(record["unit"])["ActiveState"] in {"inactive", "failed"})
            finally:
                if new_owner.poll() is None:
                    new_owner.kill()
                new_owner.wait(timeout=10)
