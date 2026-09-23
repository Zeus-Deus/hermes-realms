"""Legacy continuity using only creation-owned, finite diskless sleep units.

No QEMU, SSH connection, display or personal profile. Run with the canonical
runner and -m integration. Retain invocation receipts before fixture rescue.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import runpy
import signal
import shutil
import subprocess
import sys
import time
import uuid
# Also re-run as an isolated child (-I), where the tests directory is not importable.
_paths = __import__("runpy").run_path(str(Path(__file__).resolve().with_name("realms_test_paths.py")))
HERMES_ROOT, PLUGIN_ROOT = _paths["HERMES_ROOT"], _paths["PLUGIN_ROOT"]

ROOT = HERMES_ROOT
SELF = Path(__file__).resolve()
BINDING = PLUGIN_ROOT / "realms/_binding.py"


def load(name):
    return runpy.run_path(str(BINDING))["load_runtime"](name)


def wait_for(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(.05)
    raise AssertionError("migration observation timed out")


def child(role, root):
    with (root / (role + ".stderr")).open("wb") as errors:
        return subprocess.Popen(
            [sys.executable, "-I", str(SELF), role, str(root)], cwd=ROOT,
            env={"PATH": "/usr/bin:/bin", "HOME": str(root),
                 "HERMES_HOME": str(root / "profile"), "LANG": "C.UTF-8"},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errors,
        )


def adopt(root):
    migration = load("vm_owner_migration")
    mode = (root / "mode").read_text(encoding="utf-8")
    if mode != "normal":
        send = migration._signal

        def pause_after_suspend(fd, number):
            send(fd, number)
            if number == signal.SIGSTOP:
                # Real syscall first; no mocked ownership/dependency result.
                load("lifecycle").atomic_json(root / "suspended.json", {"signal_sent": True})
                wait_for(lambda: (root / "continue").exists(), timeout=30)

        migration._signal = pause_after_suspend
    manager = load("vm_manager").VmManager(root / "profile")
    with manager.registry.lock():
        record = manager.registry.records()[0]
        manager._bind_to_owner(record)
        manager.registry.put(record)
    load("lifecycle").atomic_json(root / "adopted.json", record)
    time.sleep(90)


def validate(root):
    manager = load("vm_manager").VmManager(root / "profile")
    with manager.registry.lock():
        record = manager.registry.records()[0]
        # Routing helpers must validate, never bind their short-lived PID.
        load("vm_owner_lifetime").validate_owner(record)
    load("lifecycle").atomic_json(root / "validated.json", record)


def run_validation(root):
    helper = child("validate", root)
    try:
        assert helper.wait(timeout=20) == 0, (root / "validate.stderr").read_text()
    finally:
        if helper.poll() is None:
            helper.kill()
        helper.wait(timeout=10)


def start_unit(unit, argv, properties=()):
    lifecycle = load("lifecycle")
    assert lifecycle.scope_info(unit)["LoadState"] == "not-found"
    subprocess.run([
        "/usr/bin/systemd-run", "--user", "--quiet", "--collect",
        "--service-type=exec", "--unit=" + unit,
        "--property=RuntimeMaxSec=120", "--property=TimeoutStopSec=5",
        *["--property=" + prop for prop in properties],
        *argv,
    ], env=lifecycle.host_control_env(), capture_output=True, check=True, timeout=15)
    info = lifecycle.scope_info(unit)
    assert info["ActiveState"] == "active" and info["InvocationID"]
    return info


def unchanged(record):
    info = load("lifecycle").scope_info(record["unit"])
    assert info["ActiveState"] == "active", info
    assert info["InvocationID"] == record["invocation_id"], info


@contextmanager
def legacy_guest(root, *, watcher=True, foreign_command=False, stop_hook=False):
    lifecycle = load("lifecycle")
    vm = load("vm_manager")
    generation = uuid.uuid4().hex
    runtime = vm._generation_paths(os.getuid(), generation)
    runtime.mkdir(mode=0o700)
    home = (root / "profile").resolve()
    session = home / "realms/vm" / generation
    session.mkdir(mode=0o700, parents=True)
    record: dict = dict(id="v-" + generation[:24], generation=generation, uid=os.getuid(),
                  home=str(home), runtime_dir=str(runtime), session_dir=str(session),
                  unit=f"hermes-vm-{generation}.service",
                  guardian_unit=f"hermes-vm-{generation}-guard.service",
                  status="running", session_id="legacy-migration",
                  vnc_socket=str(runtime / "vnc.sock"), last_activity=time.time())
    owner = subprocess.Popen(["/usr/bin/sleep", "120"], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    units = {}
    vm.VmRegistry(home).put(record)
    try:
        info = start_unit(record["unit"], ["/usr/bin/sleep", "120"])
        record.update(invocation_id=info["InvocationID"], cgroup=info["ControlGroup"])
        units[record["unit"]] = info["InvocationID"]
        peer_unit = f"hermes-migration-peer-{generation}.service"
        peer = start_unit(peer_unit, ["/usr/bin/sleep", "120"])
        units[peer_unit] = peer["InvocationID"]
        if watcher:
            # Frozen historical command, deliberately NOT the production
            # migration matcher: weakening that matcher cannot change this.
            old_unit = record["unit"].removesuffix(".service") + "-owner.service"
            info = start_unit(old_unit, ["/bin/sh", "-c",
                f'while kill -0 {owner.pid} 2>/dev/null; do sleep 5; done; '
                f'exec systemctl --user stop {peer_unit if foreign_command else record["unit"]}'],
                ["BindsTo=" + record["unit"], "After=" + record["unit"],
                 *(["ExecStop=/usr/bin/true"] if stop_hook else [])])
            units[old_unit] = info["InvocationID"]
        vm.VmRegistry(home).put(record)
        lifecycle.atomic_json(root / "created.json", {"record": record, "units": units})
        yield owner, record, dict(unit=peer_unit, invocation_id=peer["InvocationID"])
    finally:
        # The migrator persists its receipt before any policy mutation. Capture
        # even partial attempts for exact-invocation cleanup, never bare names.
        manager = vm.VmManager(home)
        current = manager.registry.get(record["id"])
        lifetime = load("vm_owner_lifetime")
        path = lifetime.receipt_path(current)
        if path.exists():
            try:
                receipt = lifetime.read_receipt(current)
            except lifecycle.OwnershipError:
                receipt = None
            if receipt and receipt.get("guard_invocation_id"):
                units[lifetime.owner_unit(current)] = receipt["guard_invocation_id"]
        if current.get("owner_invocation_id"):
            units[lifetime.owner_unit(current)] = current["owner_invocation_id"]
        lifecycle.atomic_json(root / "before-cleanup.json", {
            unit: lifecycle.scope_info(unit) for unit in units})
        for unit, invocation in units.items():
            lifetime.stop_invocation(unit, invocation)
        if owner.poll() is None:
            owner.kill()
        owner.wait(timeout=10)
        lifetime.remove_dropin(current)
        shutil.rmtree(runtime)
        shutil.rmtree(session)


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    role, directory = sys.argv[1:]
    {"adopt": adopt, "validate": validate}[role](Path(directory))
else:
    import pytest

    pytestmark = [pytest.mark.linux_only, pytest.mark.integration]

    @pytest.mark.parametrize("death", ["normal", "old-owner-after-freeze", "adopter-after-freeze"])
    def test_legacy_routing_then_adoption_preserves_guest_and_peer(tmp_path, death):
        lifecycle = load("lifecycle")
        lifetime = load("vm_owner_lifetime")
        with legacy_guest(tmp_path) as (old_owner, record, peer):
            run_validation(tmp_path)
            unchanged(record)
            assert not lifetime.receipt_path(record).exists()
            (tmp_path / "mode").write_text(death, encoding="utf-8")
            new_owner = child("adopt", tmp_path)
            try:
                if death != "normal":
                    def suspended():
                        assert new_owner.poll() is None, (tmp_path / "adopt.stderr").read_text()
                        return (tmp_path / "suspended.json").exists()
                    wait_for(suspended)
                    current = load("vm_manager").VmRegistry(record["home"]).get(record["id"])
                    process = current["legacy_owner"]["process"]
                    wait_for(lambda: Path(f"/proc/{process['pid']}/stat").read_text().rsplit(")", 1)[1].split()[0] == "T")
                    unchanged(record)
                    unchanged(peer)
                    if death == "adopter-after-freeze":
                        new_owner.kill()
                        new_owner.wait(timeout=10)
                        wait_for(lambda: lifecycle.scope_info(record["unit"])["ActiveState"] in {"inactive", "failed"})
                        unchanged(peer)
                        return
                    old_owner.kill()
                    old_owner.wait(timeout=10)
                    (tmp_path / "continue").touch()
                def adopted():
                    assert new_owner.poll() is None, (tmp_path / "adopt.stderr").read_text()
                    path = tmp_path / "adopted.json"
                    return json.loads(path.read_text()) if path.exists() else None
                migrated = wait_for(adopted)
                assert lifetime.read_receipt(migrated)["watched_owner"]["pid"] == new_owner.pid
                old_unit = record["unit"].removesuffix(".service") + "-owner.service"
                wait_for(lambda: lifecycle.scope_info(old_unit)["ActiveState"] in {"inactive", "failed"})
                if old_owner.poll() is None:
                    old_owner.kill()
                    old_owner.wait(timeout=10)
                # Old watcher is witnessed retired; not a delay shorter than
                # its polling interval. The real guest invocation never changed.
                unchanged(record)
                unchanged(peer)
                run_validation(tmp_path)
                assert lifetime.read_receipt(migrated)["owner"]["pid"] == new_owner.pid
                new_owner.kill()
                new_owner.wait(timeout=10)
                wait_for(lambda: lifecycle.scope_info(record["unit"])["ActiveState"] in {"inactive", "failed"})
                unchanged(peer)
            finally:
                if new_owner.poll() is None:
                    new_owner.kill()
                new_owner.wait(timeout=10)

    @pytest.mark.parametrize("rejection", [
        "foreign", "foreign-command", "invocation", "malformed", "missing-modern",
        "retiring", "incomplete-migration", "missing-watcher", "stop-hook",
        "failure-action", "success-action",
    ])
    def test_refused_migration_never_stops_guest_or_peer(tmp_path, rejection, monkeypatch):
        lifecycle = load("lifecycle")
        lifetime = load("vm_owner_lifetime")
        with legacy_guest(tmp_path, watcher=rejection != "missing-watcher",
                          foreign_command=rejection == "foreign-command",
                          stop_hook=rejection == "stop-hook") as (_, record, peer):
            bad = dict(record)
            bad.update({
                "foreign": {"home": str(tmp_path / "other")},
                "invocation": {"invocation_id": peer["invocation_id"]},
                "missing-modern": {"owner_protocol": "pidfd-v2"},
                "incomplete-migration": {"owner_migration": "preparing"},
            }.get(rejection, {}))
            if rejection in {"retiring", "malformed"}:
                with lifetime.owner_lock(bad, create=True):
                    if rejection == "retiring":
                        lifecycle.atomic_json(lifetime.receipt_path(bad), {
                            "binding": lifetime._binding(bad), "state": "retiring",
                            "guard_invocation_id": None,
                        })
                    else:
                        path = lifetime.receipt_path(bad)
                        path.write_text("not json", encoding="utf-8")
                        path.chmod(0o600)
            manager = load("vm_manager").VmManager(tmp_path / "profile")
            if rejection in {"failure-action", "success-action"}:
                # Never configure exit/reboot actions on the login manager.
                # Simulate only the read-only observation, and trap the first
                # installation boundary if validation incorrectly accepts it.
                migration = load("vm_owner_migration")
                properties = migration._properties
                field = "FailureAction" if rejection == "failure-action" else "SuccessAction"
                def observed(unit, names):
                    info = properties(unit, names)
                    if unit.endswith("-owner.service"):
                        info[field] = "exit"
                    return info
                def unexpected_install(*args, **kwargs):
                    raise AssertionError("unsafe watcher reached migration installation")
                monkeypatch.setattr(migration, "_properties", observed)
                monkeypatch.setattr(lifetime, "install", unexpected_install)
            with pytest.raises(lifecycle.OwnershipError):
                with manager.registry.lock():
                    manager._bind_to_owner(bad)
            if rejection in {"failure-action", "success-action"}:
                assert manager.registry.get(record["id"]) == record
            unchanged(record)
            unchanged(peer)
