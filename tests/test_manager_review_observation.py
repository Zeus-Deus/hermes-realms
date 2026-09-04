"""R3: observation failures must never discard a still-owned live realm."""

from pathlib import Path

import pytest

from realms import lifecycle, supervisor
from realms.manager import Manager


@pytest.mark.parametrize("cleanup", ["manager", "guardian"])
def test_failed_systemctl_observation_preserves_ownership(
    tmp_path, monkeypatch, cleanup
):
    manager = Manager(tmp_path)
    record = manager.start("observation-failure")
    original_env = lifecycle.host_control_env
    try:
        with monkeypatch.context() as patch:
            patch.setattr(
                lifecycle,
                "host_control_env",
                lambda: dict(
                    original_env(),
                    XDG_RUNTIME_DIR=str(tmp_path / "absent-runtime"),
                    DBUS_SESSION_BUS_ADDRESS="unix:path="
                    + str(tmp_path / "absent-bus"),
                ),
            )
            with pytest.raises(lifecycle.RealmError, match="inspect"):
                if cleanup == "manager":
                    manager.stop(record["id"])
                else:
                    supervisor.cleanup(tmp_path, record["id"])
        assert manager.registry.get(record["id"]) == record
        assert Path(record["runtime_dir"]).exists()
        lifecycle.validate_live(record)
    finally:
        # Even the RED path may have erased the record; keep the test's exact
        # ownership receipt so its disposable scope is never leaked.
        lifecycle.stop_scope(record)
        manager.registry.put(record)
        manager.stop(record["id"])


def test_stop_requires_a_successful_post_stop_observation(tmp_path, monkeypatch):
    manager = Manager(tmp_path)
    record = manager.start("post-stop-observation")
    original_run = lifecycle.subprocess.run
    original_env = lifecycle.host_control_env

    def fail_after_stop(command, **kwargs):
        result = original_run(command, **kwargs)
        if command[:3] == ["systemctl", "--user", "stop"]:
            monkeypatch.setattr(
                lifecycle,
                "host_control_env",
                lambda: {
                    "PATH": "/usr/bin:/bin",
                    "XDG_RUNTIME_DIR": str(tmp_path / "absent"),
                    "DBUS_SESSION_BUS_ADDRESS": "unix:path="
                    + str(tmp_path / "absent-bus"),
                },
            )
        return result

    try:
        with monkeypatch.context() as patch:
            # Patch the real execution seam; the actual owned scope is stopped.
            patch.setattr(lifecycle.subprocess, "run", fail_after_stop)
            with pytest.raises(lifecycle.RealmError, match="inspect"):
                lifecycle.stop_scope(record)
    finally:
        monkeypatch.setattr(lifecycle, "host_control_env", original_env)
        manager.stop(record["id"])


def test_crash_stranded_stopping_record_is_reconciled(tmp_path):
    manager = Manager(tmp_path)
    record = manager.start("stopping-recovery")
    manager.stop(record["id"])
    record["status"] = "stopping"
    manager.registry.put(record)
    assert manager.list() == []


def test_doctor_reports_unavailable_user_manager(tmp_path, monkeypatch):
    monkeypatch.setattr(
        lifecycle,
        "host_control_env",
        lambda: {
            "PATH": "/usr/bin:/bin",
            "XDG_RUNTIME_DIR": str(tmp_path / "absent"),
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=" + str(tmp_path / "absent-bus"),
        },
    )
    report = Manager(tmp_path).doctor()
    assert report["ok"] is False and report["systemd_user"] is False


def test_positive_missing_scope_allows_durable_record_cleanup(tmp_path):
    manager = Manager(tmp_path)
    record = manager.start("confirmed-absence")
    manager.stop(record["id"])
    manager.registry.put(record)
    assert manager.stop(record["id"]) is True
    assert not manager.registry.path(record["id"]).exists()
