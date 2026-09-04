import os
from pathlib import Path
import signal
import time

import pytest
from realms.manager import Manager
from realms.lifecycle import identity

pytestmark = pytest.mark.e2e


def wait_empty(m, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and m.list():
        time.sleep(0.1)
    assert m.list() == []


def test_idle_expiry_tears_down_without_a_living_manager(tmp_path):
    (tmp_path / "config.yaml").write_text("plugins:\n  realms:\n    idle_ttl: 1\n")
    m = Manager(tmp_path)
    r = m.start("idle")
    try:
        time.sleep(0.65)
        m.env(r["id"])
        time.sleep(0.65)
        assert m.list(), "recent activity must extend idle expiry"
        del m
        wait_empty(Manager(tmp_path), timeout=4)
        assert not Path(r["runtime_dir"]).exists()
        assert all(identity(p["pid"]) is None for p in r["processes"].values())
    finally:
        Manager(tmp_path).stop(r["id"])


@pytest.mark.parametrize(
    "component",
    [
        "bus",
        "compositor",
        "atspi",
        "registry",
        "vnc",
        "worker",
        "xwayland",
        "supervisor",
    ],
)
def test_component_crash_reaps_whole_scope_automatically(tmp_path, component):
    m = Manager(tmp_path)
    r = m.start("crash-" + component)
    process = (
        r["supervisor"] if component == "supervisor" else r["processes"][component]
    )
    try:
        os.kill(process["pid"], signal.SIGKILL)
        deadline = time.monotonic() + 8
        # Do not call the manager: cleanup must occur even when Hermes is gone.
        while Path(r["runtime_dir"]).exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not Path(r["runtime_dir"]).exists()
        assert m.list() == []
        assert all(identity(p["pid"]) is None for p in r["processes"].values())
        assert not (Path("/sys/fs/cgroup") / r["cgroup"].lstrip("/")).exists()
    finally:
        m.stop(r["id"])
