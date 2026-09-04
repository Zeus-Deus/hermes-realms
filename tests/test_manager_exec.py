import json
import os
from pathlib import Path
import sys
import time

import pytest
from realms.manager import Manager
from realms.lifecycle import identity

pytestmark = pytest.mark.e2e


def test_exec_owns_daemonized_descendants_and_preserves_workspace(tmp_path):
    m = Manager(tmp_path)
    r = m.start("exec")
    ids = []
    try:
        assert hasattr(m, "exec"), "scoped exec is missing"
        receipt = tmp_path / "child.json"
        code = """import json, os, pathlib, time
if os.fork() == 0:
    os.setsid()
    if os.fork() == 0:
        pathlib.Path('child.json').write_text(json.dumps({'pid':os.getpid(),'cwd':os.getcwd(),'env':dict(os.environ)}))
        time.sleep(300)
    os._exit(0)
os.wait()
print('parent-complete', flush=True)
"""
        job = m.exec(r["id"], [sys.executable, "-c", code], cwd=tmp_path, wait=True)
        assert job["returncode"] == 0
        assert job["stdout"].strip() == "parent-complete"
        deadline = time.monotonic() + 5
        while not receipt.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        child = json.loads(receipt.read_text())
        assert child["cwd"] == str(tmp_path)
        assert child["env"]["XDG_RUNTIME_DIR"] == r["runtime_dir"]
        assert child["env"]["HOME"] != os.environ["HOME"]
        assert "HYPRLAND_INSTANCE_SIGNATURE" not in child["env"]
        assert r["scope"] in Path(f"/proc/{child['pid']}/cgroup").read_text()
        ids = [identity(child["pid"])]
    finally:
        m.stop(r["id"])
    assert all(identity(p["pid"]) is None for p in ids)
