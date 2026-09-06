"""P2-2: real filesystem failures retain non-running, retryable ownership."""

from pathlib import Path
import os
import signal
import subprocess
import sys
import time

import pytest

from realms.lifecycle import OwnershipError, RealmError, alive, scope_info, stop_scope
from realms.manager import Manager


def lock_data(runtime):
    locked = runtime / "home" / "readonly-cache"
    locked.mkdir(parents=True)
    (locked / "data").write_text("private job data")
    locked.chmod(0)
    return locked


def assert_pending(manager, record, locked):
    current = manager.registry.get(record["id"])
    assert current["status"] == "cleanup_failed"
    assert current["generation"] == record["generation"]
    assert "Permission denied" in current["cleanup_error"]
    assert locked.exists() and locked.stat().st_mode & 0o777 == 0
    assert scope_info(record["scope"])["ActiveState"] in ("inactive", "failed")
    assert all(not alive(process) for process in record.get("processes", {}).values())
    with pytest.raises(RealmError, match="not running"):
        manager.env(record["id"])


def finish(manager, record, locked):
    # Only repair this exact test-owned directory, never a symlink target.
    if locked.exists():
        locked.chmod(0o700)
    stop_scope(record)
    if not manager.registry.path(record["id"]).exists():
        manager.registry.put(record)  # RED cleanup must not strand our test data.
    manager.stop(record["id"])
    assert not Path(record["runtime_dir"]).exists()
    assert not manager.registry.path(record["id"]).exists()


def test_stop_retains_filesystem_failure_for_retry(tmp_path):
    manager = Manager(tmp_path)
    record = manager.start("pass2-stop-cleanup")
    runtime = Path(record["runtime_dir"])
    locked = runtime / "home" / "readonly-cache"
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    (outside / "keep").write_text("untouched")
    outside.chmod(0o500)
    # A symlink sibling must never trigger permission repair outside runtime.
    (runtime / "outside-link").symlink_to(outside, target_is_directory=True)
    try:
        code = "from pathlib import Path; p=Path.home()/'readonly-cache'; p.mkdir(); (p/'data').write_text('private job data'); p.chmod(0)"
        assert (
            manager.exec(record["id"], [sys.executable, "-c", code], wait=True)[
                "returncode"
            ]
            == 0
        )
        with pytest.raises(RealmError, match="runtime cleanup failed"):
            manager.stop(record["id"])
        assert_pending(manager, record, locked)
        with pytest.raises(RealmError, match="runtime cleanup failed"):
            manager.stop(record["id"])
        assert_pending(manager, record, locked)
        assert outside.stat().st_mode & 0o777 == 0o500
        locked.chmod(0o700)
        assert manager.stop(record["id"]) is True
        assert not runtime.exists() and not manager.registry.path(record["id"]).exists()
    finally:
        finish(manager, record, locked)
        outside.chmod(0o700)


@pytest.mark.parametrize("component", ["worker", "supervisor"])
def test_guardian_retains_cleanup_failure_after_component_crash(
    tmp_path, monkeypatch, component
):
    manager = Manager(tmp_path)
    original_popen = subprocess.Popen

    def warnings_visible(command, **kwargs):
        if "realms.supervisor" in command:
            command = list(command)
            command.insert(1, "--setenv=PYTHONWARNINGS=always::ResourceWarning")
        return original_popen(command, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "Popen", warnings_visible)
        record = manager.start("pass2-guardian-cleanup")
    locked = lock_data(Path(record["runtime_dir"]))
    try:
        process = (
            record["supervisor"]
            if component == "supervisor"
            else record["processes"][component]
        )
        os.kill(process["pid"], signal.SIGKILL)
        deadline = time.monotonic() + 10
        while (
            scope_info(record["guardian_unit"])["ActiveState"]
            not in ("inactive", "failed")
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        assert scope_info(record["guardian_unit"])["ActiveState"] in (
            "inactive",
            "failed",
        )
        # No Manager.list/stop during autonomous cleanup: inspect durable result.
        assert_pending(manager, record, locked)
        assert "ResourceWarning" not in Path(record["log_path"]).read_text()
        with pytest.raises(RealmError, match="runtime cleanup failed"):
            manager.stop(record["id"])
        locked.chmod(0o700)
        assert manager.stop(record["id"])
    finally:
        finish(manager, record, locked)


@pytest.mark.parametrize(
    "status", ["starting", "running", "stopping", "cleanup_failed"]
)
def test_recovery_retains_filesystem_failure_until_retry_succeeds(tmp_path, status):
    manager = Manager(tmp_path)
    record = manager.start("pass2-recovery-cleanup")
    manager.stop(record["id"])
    # The supervisor can exit before its unit finishes ExecStopPost. Replaying
    # a crash receipt earlier races that live guardian's legitimate cleanup.
    deadline = time.monotonic() + 10
    while scope_info(record["guardian_unit"])["ActiveState"] not in (
        "inactive", "failed"
    ) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert scope_info(record["guardian_unit"])["ActiveState"] in ("inactive", "failed")
    assert scope_info(record["scope"])["ActiveState"] in ("inactive", "failed")
    # Replay this disposable realm's durable receipt after verified unit exit,
    # as left by a manager crash; never synthesize systemctl observations.
    runtime = Path(record["runtime_dir"])
    runtime.mkdir(mode=0o700)
    locked = lock_data(runtime)
    record.update(status=status, created_at=time.time() - 120)
    manager.registry.put(record)
    try:
        with pytest.raises(RealmError, match="runtime cleanup failed"):
            Manager(tmp_path).list()
        assert_pending(manager, record, locked)
        with pytest.raises(RealmError, match="runtime cleanup failed"):
            Manager(tmp_path).start(record["session_id"])
        assert len(manager.registry.records()) == 1
        assert_pending(manager, record, locked)
        locked.chmod(0o700)
        assert Manager(tmp_path).list() == []
        assert not runtime.exists()
    finally:
        finish(manager, record, locked)


@pytest.mark.parametrize("phase", ["launcher", "ready"])
def test_startup_rollback_retains_failed_runtime_cleanup(tmp_path, monkeypatch, phase):
    from realms import manager as manager_module

    manager = Manager(tmp_path)
    saved = {}
    original_popen = subprocess.Popen
    original_json = manager_module.atomic_json

    def obstruct():
        record = manager.registry.records()[0]
        saved["record"] = record
        saved["locked"] = lock_data(Path(record["runtime_dir"]))

    def fail_launcher(command, **kwargs):
        if "realms.supervisor" in command:
            obstruct()
            command = [str(tmp_path / "missing-launcher"), *command[1:]]
        return original_popen(command, **kwargs)

    def fail_receipt(path, value):
        if Path(path).name == "owner.json":
            obstruct()
            # Actual EACCES after real compositor/scope readiness.
            (saved["locked"] / "data").read_text()
        return original_json(path, value)

    try:
        with monkeypatch.context() as patch:
            if phase == "launcher":
                patch.setattr(subprocess, "Popen", fail_launcher)
            else:
                patch.setattr(manager_module, "atomic_json", fail_receipt)
            with pytest.raises(RealmError, match="runtime cleanup failed"):
                manager.start("pass2-startup-cleanup")
        record, locked = saved["record"], saved["locked"]
        assert_pending(manager, record, locked)
        with pytest.raises(RealmError, match="runtime cleanup failed"):
            Manager(tmp_path).list()
        locked.chmod(0o700)
        assert Manager(tmp_path).list() == []
        assert not Path(record["runtime_dir"]).exists()
    finally:
        if "record" in saved:
            finish(manager, saved["record"], saved["locked"])


@pytest.mark.parametrize("tamper", ["symlink", "generation"])
def test_cleanup_retry_preserves_ownership_checks(tmp_path, tamper):
    manager = Manager(tmp_path)
    record = manager.start("pass2-cleanup-safety")
    runtime = Path(record["runtime_dir"])
    locked = lock_data(runtime)
    held = runtime.with_name(runtime.name + "-held")
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    (outside / "keep").write_text("untouched")
    outside.chmod(0o500)
    try:
        with pytest.raises(RealmError, match="runtime cleanup failed"):
            manager.stop(record["id"])
        deadline = time.monotonic() + 10
        while (
            scope_info(record["guardian_unit"])["ActiveState"]
            not in ("inactive", "failed")
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        assert scope_info(record["guardian_unit"])["ActiveState"] in (
            "inactive",
            "failed",
        )
        pending = manager.registry.get(record["id"])
        if tamper == "symlink":
            runtime.rename(held)
            runtime.symlink_to(outside, target_is_directory=True)
        else:
            manager.registry.put(dict(pending, generation="0" * 32))
        with pytest.raises(OwnershipError):
            manager.stop(record["id"])
        assert manager.registry.path(record["id"]).exists()
        assert outside.stat().st_mode & 0o777 == 0o500
        assert (outside / "keep").read_text() == "untouched"
    finally:
        if runtime.is_symlink():
            runtime.unlink()
            held.rename(runtime)
        manager.registry.put(record)
        finish(manager, record, locked)
        outside.chmod(0o700)
