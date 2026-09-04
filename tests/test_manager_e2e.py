"""Real systemd/wlroots receipts; every test uses a disposable profile."""

import json
import os
from pathlib import Path
import socket
import time

import pytest
from realms.manager import Manager

pytestmark = pytest.mark.e2e


def wait_for(predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    assert predicate(), "condition did not become true"


def test_real_scope_private_desktop_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    m = Manager()
    assert hasattr(m, "start"), "real lifecycle is not implemented"
    r = m.start("lifecycle")
    try:
        assert r["session_id"] == "lifecycle"
        assert r["status"] == "running"
        assert m.list()[0]["id"] == r["id"]
        e = m.env(r["id"])
        runtime = Path(e["XDG_RUNTIME_DIR"])
        assert runtime.stat().st_mode & 0o777 == 0o700
        assert runtime != Path(f"/run/user/{os.getuid()}")
        assert (runtime / e["WAYLAND_DISPLAY"]).is_socket()
        assert e["DBUS_SESSION_BUS_ADDRESS"].startswith(f"unix:path={runtime}/")
        assert e["AT_SPI_BUS_ADDRESS"].startswith(f"unix:path={runtime}/")
        assert e["DISPLAY"] != os.environ.get("DISPLAY")
        assert "HYPRLAND_INSTANCE_SIGNATURE" not in e
        assert "YDOTOOL_SOCKET" not in e
        assert e["LABWC_UPDATE_ACTIVATION_ENV"] == "false"
        assert e["ATSPI_DBUS_IMPLEMENTATION"] == "dbus-daemon"
        assert e["CUA_DRIVER_RS_ENABLE_WAYLAND"] == "1"
        for process in r["processes"].values():
            assert r["scope"] in Path(f"/proc/{process['pid']}/cgroup").read_text()
        assert "vnc_socket" in r, "private Unix VNC transport is missing"
        assert r["vnc_port"] is None
        assert Path(r["vnc_socket"]).parent == runtime
        assert Path(r["vnc_socket"]).stat().st_mode & 0o777 == 0o600
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            import struct

            conn.settimeout(3)
            conn.connect(r["vnc_socket"])
            pid, uid, gid = struct.unpack(
                "3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            )
            assert uid == os.getuid()
            assert pid in {process["pid"] for process in r["processes"].values()}
            assert conn.recv(12).startswith(b"RFB ")
        tcp_inodes = set()
        for table in ("tcp", "tcp6"):
            for line in Path("/proc/net/" + table).read_text().splitlines()[1:]:
                fields = line.split()
                if fields[3] == "0A":
                    tcp_inodes.add("socket:[" + fields[9] + "]")
        for fd in Path(f"/proc/{r['processes']['vnc']['pid']}/fd").iterdir():
            assert os.readlink(fd) not in tcp_inodes, "VNC must not own a TCP listener"
        print("LIFECYCLE_RECEIPT=" + json.dumps(r, sort_keys=True))
    finally:
        m.stop(r["id"])
    assert m.list() == []
    assert not Path(r["runtime_dir"]).exists()
    assert not Path("/tmp/.X11-unix/X" + e["DISPLAY"][1:]).exists()
    for process in r["processes"].values():
        wait_for(lambda: not Path(f"/proc/{process['pid']}").exists())
    with pytest.raises(OSError):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.connect(r["vnc_socket"])


def test_capture_real_pixels_in_both_renderers(tmp_path):
    import struct

    for renderer in ("gles2", "pixman"):
        profile = tmp_path / renderer
        profile.mkdir()
        (profile / "config.yaml").write_text(
            f"plugins:\n  realms:\n    renderer: {renderer}\n"
        )
        m = Manager(profile)
        r = m.start("capture")
        try:
            assert hasattr(m, "shot"), "capture is not implemented"
            target = profile / "capture.png"
            assert Path(m.shot(r["id"], target)) == target
            data = target.read_bytes()
            assert data[:8] == b"\x89PNG\r\n\x1a\n"
            assert struct.unpack(">II", data[16:24]) == (1920, 1080)
            log = Path(r["log_path"]).read_text()
            if renderer == "gles2":
                assert "GL renderer: AMD" in log
                assert "llvmpipe" not in log
            else:
                assert "pixman" in log.lower()
            print(
                "RENDER_RECEIPT="
                + json.dumps(
                    {"renderer": renderer, "bytes": len(data), "log": r["log_path"]}
                )
            )
        finally:
            m.stop(r["id"])
        assert not Path(r["runtime_dir"]).exists()
