import json
from pathlib import Path
import socket
import tempfile

import pytest
from realms.manager import Manager
from realms.lifecycle import OwnershipError

pytestmark = pytest.mark.e2e


def test_rejects_foreign_display_or_runtime_before_use_or_delete(tmp_path):
    m = Manager(tmp_path)
    r = m.start("ownership")
    record_path = m.registry.path(r["id"])
    ready_path = Path(r["runtime_dir"]) / "ready.json"
    original = ready_path.read_text()
    victim = tmp_path / "unrelated-directory"
    victim.mkdir()
    (victim / "keep").write_text("untouched")
    try:
        with m.registry.lock():
            try:
                for key, value in (
                    ("vnc_socket", "/run/user/1000/bus"),
                    ("guardian_unit", "dbus.service"),
                ):
                    record_path.write_text(json.dumps(dict(r, **{key: value})))
                    with pytest.raises(OwnershipError):
                        m.registry.get(r["id"])
            finally:
                record_path.write_text(json.dumps(r))
        # Use a real foreign socket without depending on or touching a host display.
        with tempfile.TemporaryDirectory(prefix="foreign-display-") as foreign_runtime:
            endpoint = Path(foreign_runtime) / "wayland-0"
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as foreign_display:
                foreign_display.bind(str(endpoint))
                foreign_display.listen(1)
                assert endpoint.is_socket()
                ready = json.loads(original)
                ready["env"]["WAYLAND_DISPLAY"] = endpoint.name
                ready["env"]["XDG_RUNTIME_DIR"] = foreign_runtime
                ready_path.write_text(json.dumps(ready))
                with pytest.raises(OwnershipError):
                    m.env(r["id"])
                assert endpoint.is_socket()
        ready_path.write_text(original)
        corrupted = dict(r, runtime_dir=str(victim))
        record_path.write_text(json.dumps(corrupted))
        with pytest.raises(OwnershipError):
            m.stop(r["id"])
        assert (victim / "keep").read_text() == "untouched"
        record_path.write_text(json.dumps(r))
        corrupted = json.loads(json.dumps(r))
        corrupted["processes"]["worker"]["start_time"] += 1
        record_path.write_text(json.dumps(corrupted))
        with pytest.raises(OwnershipError):
            m.env(r["id"])
    finally:
        ready_path.write_text(original)
        record_path.write_text(json.dumps(r))
        m.stop(r["id"])


def test_registry_rejects_symlink_without_changing_target(tmp_path):
    victim = tmp_path / "unrelated"
    victim.mkdir(mode=0o755)
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "realms").symlink_to(victim, target_is_directory=True)
    before = victim.stat().st_mode
    with pytest.raises(OwnershipError):
        Manager(profile)
    assert victim.stat().st_mode == before
    assert list(victim.iterdir()) == []
