from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from realms.manager import Manager

pytestmark = pytest.mark.e2e


def test_reusing_session_refreshes_idle_lease(tmp_path):
    m = Manager(tmp_path)
    r = m.start("same")
    try:
        time.sleep(0.1)
        reused = Manager(tmp_path).start("same")
        assert reused["id"] == r["id"]
        assert reused["last_activity"] > r["last_activity"]
    finally:
        m.stop(r["id"])


def test_concurrent_process_allocations_and_profile_boundary(tmp_path):
    def start(pair):
        profile, session_id = pair
        env = dict(os.environ, HERMES_HOME=str(profile))
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import json,sys; from realms.manager import Manager; print(json.dumps(Manager().start(sys.argv[1])))",
                session_id,
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            capture_output=True,
            text=True,
            timeout=40,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    requests = [
        (tmp_path / "a", "same"),
        (tmp_path / "a", "same"),
        (tmp_path / "a", "other"),
        (tmp_path / "b", "same"),
    ]
    records = []
    try:
        with ThreadPoolExecutor(4) as pool:
            records = list(pool.map(start, requests))
        unique = {r["id"]: r for r in records}
        assert len(unique) == 3
        assert all(r["vnc_port"] is None for r in unique.values())
        assert len({r["vnc_socket"] for r in unique.values()}) == 3
        assert len({r["runtime_dir"] for r in unique.values()}) == 3
        environments = [Manager(r["home"]).env(r["id"]) for r in unique.values()]
        assert len({e["DISPLAY"] for e in environments}) == 3
        assert len({e["DBUS_SESSION_BUS_ADDRESS"] for e in environments}) == 3
        assert len({e["AT_SPI_BUS_ADDRESS"] for e in environments}) == 3
        assert len(Manager(tmp_path / "a").list()) == 2
        assert len(Manager(tmp_path / "b").list()) == 1
        print("CONCURRENCY_RECEIPT=" + json.dumps(list(unique.values())))
    finally:
        for profile in (tmp_path / "a", tmp_path / "b"):
            m = Manager(profile)
            for r in m.list():
                m.stop(r["id"])
