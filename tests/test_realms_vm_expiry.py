"""Automatic retention uses real workspace files and the existing deletion guards.

The fleet fixture supplies unit observations without starting any VM or service.
Open-file protection is exercised with an actual child process.
"""
from dataclasses import replace
import json
from pathlib import Path
import runpy
import subprocess
import sys
import time

import pytest
from realms_test_paths import PLUGIN_ROOT

pytestmark = pytest.mark.platforms("linux")
recovery = runpy.run_path(str(PLUGIN_ROOT / "tests/test_realms_vm_recovery_delete.py"))
fleet = recovery["fleet"]
load = recovery["load"]
DAY = 86400


def age(manager, record, *, days=15):
    record = manager.registry.get(record["id"])
    record.update(stopped_at=time.time() - days * DAY,
                  last_activity=time.time() - days * DAY)
    manager.registry.put(record)
    return record


def expire(manager, **kwargs):
    with manager.registry.lock():
        manager._expire_locked(**kwargs)


def test_default_expiry_removes_old_stopped_work_during_list(fleet):
    manager, stopped, recent, running, active = fleet
    age(manager, stopped)
    base = recovery["tree"](manager.base_home())
    iso = manager.data / "iso" / "omarchy-test.iso"
    iso.parent.mkdir(parents=True)
    iso.write_bytes(b"shared installer")
    running_files = recovery["tree"](running["session_dir"])
    active_before = active.copy()
    assert manager.config.vm.workspace_retention_days == 14
    assert {r["id"] for r in manager.list()} == {recent["id"], running["id"]}
    assert not Path(stopped["session_dir"]).exists()
    assert not Path(stopped["runtime_dir"]).exists()
    assert active == active_before
    assert recovery["tree"](running["session_dir"]) == running_files
    assert recovery["tree"](manager.base_home()) == base
    assert iso.read_bytes() == b"shared installer"


def test_start_cleans_other_expired_work_but_resumes_requested_workspace(fleet):
    manager, stopped, other, _, _ = fleet
    age(manager, stopped)
    age(manager, other)
    disk = Path(stopped["session_dir"]) / "disk.qcow2"
    before = disk.stat().st_ino, disk.read_bytes()
    resumed = manager.start(stopped["session_id"])
    assert resumed["id"] == stopped["id"]
    assert (disk.stat().st_ino, disk.read_bytes()) == before
    assert not Path(other["session_dir"]).exists()


def test_expiry_never_stops_a_long_running_or_starting_vm(fleet, monkeypatch):
    manager, stopped, _, running, _ = fleet
    age(manager, running, days=100)
    starting = age(manager, stopped, days=100)
    starting.update(status="starting")
    manager.registry.put(starting)
    before = {r["id"]: manager.registry.path(r["id"]).read_bytes()
              for r in (starting, running)}
    def unexpected(*args, **kwargs):
        pytest.fail("expiry must never retire compute")
    monkeypatch.setattr(manager, "_stop_locked", unexpected)
    monkeypatch.setattr(manager, "_remove_locked", unexpected)
    expire(manager)
    assert all(manager.registry.path(vm_id).read_bytes() == content
               for vm_id, content in before.items())


@pytest.mark.parametrize("days,retention,deleted", [(13, 14, False), (15, 30, False),
                                                    (15, 0, False), (15, 7, True)])
def test_retention_uses_current_profile_setting(fleet, days, retention, deleted):
    manager, stopped, _, _, _ = fleet
    age(manager, stopped, days=days)
    manager.config = replace(manager.config, vm=replace(
        manager.config.vm, workspace_retention_days=retention))
    expire(manager)
    assert Path(stopped["session_dir"]).exists() is not deleted


@pytest.mark.parametrize("field,value", [("stopped_at", None), ("stopped_at", 0),
                                         ("stopped_at", float("nan")),
                                         ("last_activity", float("inf")),
                                         ("last_activity", "old"),
                                         ("stopped_at", True)])
def test_unknown_or_invalid_age_never_authorizes_expiry(fleet, field, value):
    manager, stopped, _, _, _ = fleet
    record = age(manager, stopped)
    record[field] = value
    manager.registry.put(record)
    expire(manager)
    assert Path(stopped["session_dir"]).exists()


@pytest.mark.parametrize("field", ["stopped_at", "last_activity"])
def test_recent_stop_or_activity_restarts_the_retention_window(fleet, field):
    manager, stopped, _, _, _ = fleet
    record = age(manager, stopped)
    record[field] = time.time()
    manager.registry.put(record)
    expire(manager)
    assert Path(stopped["session_dir"]).exists()


def test_expiry_reclaims_old_recovery_work_only_after_confirmed_retirement(fleet):
    manager, stopped, damaged, _, _ = fleet
    for row in (stopped, damaged):
        record = age(manager, row)
        (Path(record["session_dir"]) / "ssh_known_hosts").unlink()
        record.update(status="recovery-required")
        if row == damaged:
            record["cleanup_required"] = True
        manager.registry.put(record)
    expire(manager)
    assert not Path(stopped["session_dir"]).exists()
    assert Path(damaged["session_dir"]).exists()


def test_expiry_keeps_stale_stopped_record_with_an_active_compute_unit(fleet):
    manager, stopped, _, _, active = fleet
    age(manager, stopped)
    active.add(stopped["guardian_unit"])
    expire(manager)
    assert Path(stopped["session_dir"]).exists()


def test_systemd_failure_keeps_expired_work_and_does_not_fail_cleanup(fleet, monkeypatch):
    manager, stopped, _, _, _ = fleet
    age(manager, stopped)
    def unavailable(unit):
        raise load("lifecycle").RealmError("systemd unavailable")
    monkeypatch.setattr(load("vm_manager"), "scope_info", unavailable)
    expire(manager)
    assert Path(stopped["session_dir"]).exists()


def test_one_blocked_workspace_does_not_prevent_other_expiry(fleet):
    manager, stopped, other, _, active = fleet
    age(manager, stopped)
    age(manager, other)
    active.add(stopped["guardian_unit"])
    expire(manager)
    assert Path(stopped["session_dir"]).exists()
    assert not Path(other["session_dir"]).exists()


def test_real_holder_process_blocks_expiry_until_it_releases_disk(fleet):
    manager, stopped, _, _, _ = fleet
    age(manager, stopped)
    disk = Path(stopped["session_dir"]) / "disk.qcow2"
    with subprocess.Popen([sys.executable, "-c",
                           "import sys; f=open(sys.argv[1],'rb'); print('ready',flush=True); sys.stdin.read()",
                           str(disk)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          text=True) as holder:
        assert holder.stdout.readline().strip() == "ready"
        expire(manager)
        assert disk.exists()
        holder.communicate(timeout=10)
    expire(manager)
    assert not disk.exists()


def test_expiry_preserves_orphans_and_unexpected_links(fleet, tmp_path):
    manager, stopped, _, _, _ = fleet
    age(manager, stopped)
    outside = tmp_path / "keep.txt"
    outside.write_bytes(b"unrelated data")
    (Path(stopped["session_dir"]) / "link").symlink_to(outside)
    orphan = manager.registry.root / "vm" / "unregistered-work"
    orphan.mkdir()
    (orphan / "work").write_bytes(b"recoverable")
    expire(manager)
    assert Path(stopped["session_dir"]).exists()
    assert outside.read_bytes() == b"unrelated data"
    assert (orphan / "work").read_bytes() == b"recoverable"


def test_interrupted_automatic_expiry_retries_but_manual_deletion_does_not(fleet, monkeypatch):
    manager, stopped, manual, _, _ = fleet
    age(manager, stopped)
    record = age(manager, manual)
    load("vm_workspace").begin_discard(record)
    record["status"] = "deleting"
    manager.registry.put(record)
    workspace = load("vm_workspace")
    real_remove = workspace.remove_directory
    def interrupted(path, bound):
        if path == Path(stopped["session_dir"]):
            raise OSError("interrupted removal")
        return real_remove(path, bound)
    monkeypatch.setattr(workspace, "remove_directory", interrupted)
    expire(manager)
    partial = manager.registry.get(stopped["id"])
    assert partial["status"] == "deleting"
    assert partial["deletion"]["automatic"] is True
    monkeypatch.setattr(workspace, "remove_directory", real_remove)
    expire(manager)
    assert not Path(stopped["session_dir"]).exists()
    assert Path(manual["session_dir"]).exists()


@pytest.mark.parametrize("intent", [None, []])
def test_unknown_deletion_intent_is_preserved(fleet, intent):
    manager, stopped, _, _, _ = fleet
    record = age(manager, stopped)
    record.update(status="deleting", deletion=intent)
    manager.registry.put(record)
    expire(manager)
    assert Path(stopped["session_dir"]).exists()


@pytest.mark.parametrize("value", [-1, True, "14", float("nan"), float("inf")])
def test_retention_setting_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="workspace_retention_days"):
        load("config").VmConfig(workspace_retention_days=value)


def test_retention_setting_is_profile_local_and_old_specs_still_load(tmp_path):
    Config = load("config").Config
    home = tmp_path / "profile"
    home.mkdir()
    config = home / "config.yaml"
    config.write_text("plugins:\n  realms:\n    vm:\n      workspace_retention_days: 0\n")
    before = config.read_bytes()
    assert Config.load(home).vm.workspace_retention_days == 0
    assert Config.load(tmp_path / "other").vm.workspace_retention_days == 14
    assert Config(**json.loads('{"vm":{"memory":3072}}')).vm.workspace_retention_days == 14
    assert config.read_bytes() == before
