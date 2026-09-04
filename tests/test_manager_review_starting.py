"""R4: crash durable startup writes without ever touching another realm."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from realms.lifecycle import RealmError
from realms.manager import Manager


def crash_before_guardian(home):
    code = """import os, sys
from realms.manager import Manager
import realms.manager as module
module.subprocess.Popen = lambda *args, **kwargs: os._exit(71)
Manager(sys.argv[1]).start('interrupted')
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(home)],
        env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1])),
        timeout=10,
    )
    assert result.returncode == 71
    manager = Manager(home)
    records = manager.registry.records()
    assert len(records) == 1 and records[0]["status"] == "starting"
    return manager, records[0]


@pytest.mark.parametrize("power_loss", [False, True])
def test_abandoned_prelaunch_record_is_reconciled(tmp_path, power_loss):
    manager, record = crash_before_guardian(tmp_path)
    try:
        record["created_at"] = time.time() - 120
        manager.registry.put(record)
        if power_loss:
            shutil.rmtree(record["runtime_dir"])
        assert manager.list() == []
        assert not Path(record["runtime_dir"]).exists()
        replacement = manager.start("interrupted")
        try:
            assert replacement["id"] != record["id"]
        finally:
            manager.stop(replacement["id"])
    finally:
        manager.stop(record["id"])


def test_crash_after_ready_recovers_only_the_verified_invocation(tmp_path):
    code = """import os, sys
from realms.manager import Manager
from realms.lifecycle import Registry
original = Registry.put
def crash(self, record):
    if record['status'] == 'running':
        os._exit(72)
    original(self, record)
Registry.put = crash
Manager(sys.argv[1]).start('ready-crash')
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1])),
        timeout=40,
    )
    assert result.returncode == 72
    manager = Manager(tmp_path)
    record = manager.registry.records()[0]
    try:
        assert record["status"] == "starting"
        recovered = manager.list()
        assert len(recovered) == 1 and recovered[0]["status"] == "running"
        assert recovered[0]["id"] == record["id"]
        assert manager.start("ready-crash")["generation"] == record["generation"]
        manager.env(record["id"])
    finally:
        manager.stop(record["id"])


def test_incomplete_live_startup_is_rolled_back_by_its_guardian(tmp_path):
    code = """import os, sys
from pathlib import Path
from realms.manager import Manager
import realms.manager as module
original = module.atomic_json
def crash(path, value):
    if Path(path).name == 'owner.json':
        os._exit(73)
    original(path, value)
module.atomic_json = crash
Manager(sys.argv[1]).start('incomplete-ready')
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1])),
        timeout=40,
    )
    assert result.returncode == 73
    manager = Manager(tmp_path)
    with manager.registry.lock():
        record = manager.registry.records()[0]
        record["created_at"] = time.time() - 120
        manager.registry.put(record)
    try:
        assert record["status"] == "starting"
        deadline = time.monotonic() + 5
        while Path(record["runtime_dir"]).exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not Path(record["runtime_dir"]).exists(), (
            "guardian kept an abandoned startup alive"
        )
        assert manager.list() == []
    finally:
        manager.stop(record["id"])


@pytest.mark.parametrize("key", ["invocation_id", "guardian_invocation_id"])
def test_startup_recovery_rejects_changed_invocation_receipts(tmp_path, key):
    from realms.lifecycle import OwnershipError, atomic_json, validate_live

    manager = Manager(tmp_path)
    record = manager.start("identity-recovery")
    owner = Path(record["runtime_dir"]) / "owner.json"
    try:
        with manager.registry.lock():
            manager.registry.put(dict(record, status="starting"))
            atomic_json(owner, dict(record, **{key: "0" * 32}))
        with pytest.raises(OwnershipError, match="invocation"):
            manager.list()
        assert manager.registry.get(record["id"])["status"] == "starting"
        validate_live(record)
    finally:
        atomic_json(owner, record)
        manager.registry.put(record)
        manager.stop(record["id"])


def test_unrecognized_live_guardian_is_neither_adopted_nor_stopped(tmp_path):
    from realms.lifecycle import host_control_env, scope_info

    manager, record = crash_before_guardian(tmp_path)
    record["created_at"] = time.time() - 120
    manager.registry.put(record)
    subprocess.run(
        [
            "systemd-run",
            "--user",
            "--quiet",
            "--collect",
            "--service-type=exec",
            "--unit=" + record["guardian_unit"],
            "/bin/sleep",
            "60",
        ],
        env=host_control_env(),
        check=True,
        timeout=5,
    )
    try:
        invocation = scope_info(record["guardian_unit"])["InvocationID"]
        assert manager.list() == [record]
        with pytest.raises(RealmError, match="starting"):
            manager.start("interrupted")
        observed = scope_info(record["guardian_unit"])
        assert (
            observed["InvocationID"] == invocation
            and observed["ActiveState"] == "active"
        )
    finally:
        subprocess.run(
            ["systemctl", "--user", "stop", record["guardian_unit"]],
            env=host_control_env(),
            check=True,
            timeout=8,
        )
        manager.stop(record["id"])


def test_recent_startup_reservation_prevents_duplicate_owner(tmp_path):
    manager, record = crash_before_guardian(tmp_path)
    try:
        assert manager.list() == [record]
        with pytest.raises(RealmError, match="starting"):
            manager.start("interrupted")
        assert manager.registry.records() == [record]
    finally:
        for existing in manager.registry.records():
            manager.stop(existing["id"])
