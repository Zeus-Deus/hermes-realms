"""Progress metadata must not hold cancellation hostage to special files."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
from realms_test_paths import HERMES_ROOT, PLUGIN_ROOT

ROOT = HERMES_ROOT
load = runpy.run_path(str(PLUGIN_ROOT / "realms/_binding.py"))["load_runtime"]
pytestmark = pytest.mark.linux_only

CHILD = """
import json, runpy, sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
load = runpy.run_path(str(Path(sys.argv[4]) / 'realms/_binding.py'))['load_runtime']
flow = load('setup_flow')
if sys.argv[2] == 'progress':
    try:
        flow.installer_progress(sys.argv[3], 'a' * 32, 'owner', 'downloading')
    except (OSError, ValueError):
        print(json.dumps({'rejected': True}))
    else:
        print(json.dumps({'rejected': False}))
else:
    service = SimpleNamespace(home=Path(sys.argv[3]), owners=load('integration').OwnershipStore(sys.argv[3]))
    flow.cancel(service, 'owner', 'a' * 32)
"""


@pytest.fixture
def job(tmp_path):
    flow = load("setup_flow")
    home = tmp_path / "profile"
    owners = load("integration").OwnershipStore(home)
    with owners.connection() as db:
        db.execute("INSERT INTO owners(id, setup_generation) VALUES (?, ?)", ("owner", 0))
    service = SimpleNamespace(home=home, owners=owners)
    fd = flow._lock(service)
    path = flow._path(service, "a" * 32)
    flow.atomic_json(path, {"id": "a" * 32, "home": str(home), "owner": "owner", "kind": "realm",
                           "state": "running", "cancel_protocol": 1, "activation_generation": 0,
                           "message": "original"})
    flow.atomic_json(flow._root(service) / "active.json", {"id": "a" * 32})
    try:
        yield flow, service, path
    finally:
        flow._release(fd)


@pytest.mark.parametrize("name,shape", [
    ("active.json", "fifo"), ("active.lock", "fifo"),
    ("active.json", "directory"), ("active.lock", "directory"),
    ("active.json", "oversized"), ("active.json", "array"),
])
def test_progress_rejection_releases_guard_for_actual_cancel(job, name, shape):
    flow, service, path = job
    metadata = flow._root(service) / name
    metadata.rename(metadata.with_suffix(metadata.suffix + ".original"))
    if shape == "fifo":
        os.mkfifo(metadata, 0o600)
    elif shape == "directory":
        metadata.mkdir()
    elif shape == "oversized":
        metadata.write_text(json.dumps({"id": "a" * 32, "padding": "x" * 65536}))
    else:
        metadata.write_text("[]")
    children = []
    try:
        progress = subprocess.Popen([sys.executable, "-I", "-c", CHILD, str(ROOT), "progress", str(service.home), str(PLUGIN_ROOT)],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        children.append(progress)
        try:
            stdout, stderr = progress.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pytest.fail("progress blocked on metadata while retaining the job guard")
        assert progress.returncode == 0, stderr
        assert json.loads(stdout)["rejected"]
        assert flow._read(service, "a" * 32)["message"] == "original"
        cancel = subprocess.Popen([sys.executable, "-I", "-c", CHILD, str(ROOT), "cancel", str(service.home), str(PLUGIN_ROOT)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        children.append(cancel)
        deadline = time.monotonic() + 5
        while flow._read(service, "a" * 32)["state"] != "cancelling":
            assert time.monotonic() < deadline, "Cancel could not commit its control record"
            time.sleep(.01)
        assert load("setup_process").cancellation_requested(path)
        # Legacy status() may block/fail on the corrupt metadata AFTER committing.
        # This assertion qualifies cancellation delivery, not the API response.
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)


@pytest.mark.parametrize("name", ["active.json", "active.lock"])
def test_progress_metadata_rejection_closes_descriptors(job, name):
    flow, service, _ = job
    metadata = flow._root(service) / name
    metadata.rename(metadata.with_suffix(metadata.suffix + ".original"))
    metadata.mkdir()
    before = len(list(Path("/proc/self/fd").iterdir()))
    for _ in range(32):
        with pytest.raises((OSError, ValueError)):
            flow.installer_progress(service.home, "a" * 32, "owner", "downloading")
    assert len(list(Path("/proc/self/fd").iterdir())) == before
