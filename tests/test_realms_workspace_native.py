"""Real private desktops: workspace lifetime is not compute lifetime."""
from pathlib import Path
from runpy import run_path
import time

import pytest
from realms_test_paths import PLUGIN_ROOT


ROOT = PLUGIN_ROOT
load = run_path(str(ROOT / "realms/_binding.py"))["load_runtime"]


def retired(record):
    life = load("lifecycle")
    deadline = time.monotonic() + 12
    states = []
    while time.monotonic() < deadline:
        states = [life.scope_info(record[key])["ActiveState"] for key in ("scope", "guardian_unit")]
        if all(s in ("inactive", "failed") for s in states):
            break
        time.sleep(.1)
    assert all(s in ("inactive", "failed") for s in states)
    assert not Path(record["runtime_dir"]).exists()
    assert not any(life.alive(p) for p in record["processes"].values())
    assert not life.alive(record["supervisor"])


@pytest.mark.integration
@pytest.mark.linux_only
def test_stop_restart_retains_private_bytes_and_owner(tmp_path):
    manager = load("manager").Manager(tmp_path)
    records = []
    try:
        first = manager.start("owner-one")
        records.append(first)
        home = Path(manager.env(first["id"])["HOME"])
        result = manager.exec(first["id"], ["/bin/sh", "-c", 'printf "retained-work" > "$HOME/sentinel"'], wait=True)
        assert result["returncode"] == 0
        assert (home / "sentinel").read_bytes() == b"retained-work"
        manager.stop(first["id"])
        retired(first)
        assert (home / "sentinel").is_file(), "Stop discarded the private workspace"
        assert (home / "sentinel").read_bytes() == b"retained-work"
        assert home.is_relative_to(tmp_path)
        reopened = load("manager").Manager(tmp_path)
        assert reopened.list()[0]["status"] == "stopped"
        with pytest.raises(load("lifecycle").RealmError, match="not running"):
            reopened.env(first["id"])
        second = reopened.start("owner-one")
        records.append(second)
        assert second["generation"] != first["generation"]
        assert Path(reopened.env(second["id"])["HOME"]) == home
        result = reopened.exec(second["id"], ["/bin/sh", "-c", 'cat "$HOME/sentinel"'], wait=True)
        assert result["stdout"] == "retained-work"
        peer = reopened.start("owner-two")
        records.append(peer)
        peer_home = Path(reopened.env(peer["id"])["HOME"])
        assert peer_home != home
        assert not (peer_home / "sentinel").exists()
        assert home.stat().st_mode & 0o777 == 0o700
        assert peer_home.stat().st_mode & 0o777 == 0o700
        with pytest.raises(load("lifecycle").RealmError, match="stopped"):
            reopened.delete(second["id"])
        # A delayed old guardian must not retire the restarted generation.
        load("supervisor").cleanup(tmp_path, first["id"], first["generation"])
        load("lifecycle").validate_live(second)
        manager.stop(second["id"])
        retired(second)
        load("lifecycle").validate_live(peer)
        with pytest.raises(load("lifecycle").OwnershipError, match="another session"):
            reopened.delete(second["id"], session_id="owner-two")
        assert (home / "sentinel").read_bytes() == b"retained-work"
        assert reopened.delete(second["id"], session_id="owner-one")
        assert not home.exists()
        assert [r["id"] for r in reopened.list()] == [peer["id"]]
        assert peer_home.exists()
    finally:
        for record in reversed(records):
            manager.stop(record["id"])


@pytest.mark.integration
@pytest.mark.linux_only
@pytest.mark.parametrize("failure", ["exception", "owner-exit"])
def test_interrupted_delete_allows_fresh_manager_peer_start(tmp_path, monkeypatch, failure):
    import json
    import subprocess
    import sys

    managers, life = load("manager"), load("lifecycle")
    manager = managers.Manager(tmp_path)
    record = manager.start("delete-owner")
    records = [record]
    root = Path(record["workspace_dir"])
    (root / "home/sentinel").write_bytes(b"authorized-removal")
    original_unlink = Path.unlink

    def interrupt(path, *args, **kwargs):
        if path == root / "owner.json":
            raise OSError("native receipt unlink interruption")
        return original_unlink(path, *args, **kwargs)

    try:
        manager.stop(record["id"])
        retired(record)
        if failure == "exception":
            with monkeypatch.context() as fault:
                fault.setattr(Path, "unlink", interrupt)
                with pytest.raises(OSError, match="receipt unlink interruption"):
                    manager.delete(record["id"], session_id="delete-owner")
        else:
            # Exit after real HOME removal, without exception unwinding or manager state.
            child = subprocess.run([sys.executable, "-c", """
import os, sys
from pathlib import Path
from runpy import run_path
load = run_path(sys.argv[1])["load_runtime"]
original = Path.unlink
def interrupt(path, *args, **kwargs):
    if path == Path(sys.argv[4]) / "owner.json":
        os._exit(73)
    return original(path, *args, **kwargs)
Path.unlink = interrupt
load("manager").Manager(sys.argv[2]).delete(sys.argv[3], session_id="delete-owner")
""", str(ROOT / "realms/_binding.py"), str(tmp_path), record["id"], str(root)],
                                   capture_output=True, timeout=15, check=False)
            assert child.returncode == 73, child.stderr.decode()
        assert not (root / "home").exists()
        assert (root / "owner.json").exists()
        fresh = managers.Manager(tmp_path)
        assert fresh.list()[0]["status"] == "deleting"
        peer = fresh.start("unrelated-owner")
        records.append(peer)
        result = fresh.exec(peer["id"], ["/bin/sh", "-c", 'printf peer > "$HOME/peer"; cat "$HOME/peer"'], wait=True)
        assert result["returncode"] == 0 and result["stdout"] == "peer"
        with pytest.raises(life.OwnershipError, match="another session"):
            fresh.delete(record["id"], session_id="unrelated-owner")
        assert fresh.delete(record["id"], session_id="delete-owner")
        assert not root.exists()
        life.validate_live(peer)
        assert [row["id"] for row in fresh.list()] == [peer["id"]]
    finally:
        for item in reversed(records):
            manager.stop(item["id"])
            retired(item)
        print("NATIVE_DELETE_RECEIPT=" + json.dumps({
            "failure": failure, "records": records,
            "deleted_workspace_absent": not root.exists(),
            "inventory": manager.list(),
        }))


@pytest.mark.integration
@pytest.mark.linux_only
@pytest.mark.parametrize("cause", ["idle", "guardian", "component"])
def test_automatic_compute_cleanup_retains_workspace(tmp_path, cause):
    import subprocess

    life = load("lifecycle")
    manager = load("manager").Manager(tmp_path)
    record = manager.start("automatic-cleanup")
    home = Path(manager.env(record["id"])["HOME"])
    (home / "sentinel").write_bytes(b"work-survives-automatic-cleanup")
    inaccessible = home / "private-subdirectory"
    inaccessible.mkdir(mode=0o700)
    (inaccessible / "work").write_bytes(b"do-not-traverse-on-stop")
    inaccessible.chmod(0)
    try:
        if cause == "idle":
            with manager.registry.lock():
                current = manager.registry.get(record["id"])
                current["last_activity"] = time.time() - current["idle_ttl"] - 1
                manager.registry.put(current)
        else:
            key = "guardian_unit" if cause == "guardian" else "scope"
            info = life.scope_info(record[key])
            expected = record["guardian_invocation_id" if cause == "guardian" else "invocation_id"]
            assert info["ActiveState"] == "active" and info["InvocationID"] == expected
            # Only the exact recorded invocation in this fresh disposable profile.
            subprocess.run(["systemctl", "--user", "kill", "--signal=SIGKILL",
                            "--kill-whom=all", record[key]], env=life.host_control_env(),
                           check=True, capture_output=True, timeout=10)
        deadline = time.monotonic() + 15
        rows = []
        while time.monotonic() < deadline:
            rows = manager.list()
            if rows and rows[0]["status"] == "stopped":
                break
            time.sleep(.1)
        assert rows[0]["status"] == "stopped"
        retired(record)
        assert (home / "sentinel").read_bytes() == b"work-survives-automatic-cleanup"
        assert inaccessible.stat().st_mode & 0o777 == 0
        inaccessible.chmod(0o700)
        assert (inaccessible / "work").read_bytes() == b"do-not-traverse-on-stop"
        restarted = manager.start("automatic-cleanup")
        assert restarted["generation"] != record["generation"]
        assert Path(manager.env(restarted["id"])["HOME"]) == home
    finally:
        inaccessible.chmod(0o700)
        manager.stop(record["id"])


@pytest.mark.integration
@pytest.mark.linux_only
def test_failed_runtime_removal_retains_work_and_retryable_ownership(tmp_path):
    life = load("lifecycle")
    manager = load("manager").Manager(tmp_path)
    record = manager.start("failed-runtime-removal")
    home = Path(manager.env(record["id"])["HOME"])
    (home / "sentinel").write_bytes(b"private-work")
    blocked = Path(record["runtime_dir"]) / "nontraversable"
    blocked.mkdir(mode=0o700)
    (blocked / "runtime-file").write_bytes(b"runtime-only")
    blocked.chmod(0)
    try:
        with pytest.raises(life.RealmError, match="runtime cleanup failed"):
            manager.stop(record["id"])
        assert manager.registry.get(record["id"])["status"] == "cleanup_failed"
        assert (home / "sentinel").read_bytes() == b"private-work"
        assert life.scope_info(record["scope"])["ActiveState"] in ("inactive", "failed")
    finally:
        blocked.chmod(0o700)
        manager.stop(record["id"])
    retired(record)
    assert manager.list()[0]["status"] == "stopped"
    assert (home / "sentinel").read_bytes() == b"private-work"
