"""Status must interpret the fresh scoped receipt after worker retirement."""
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest
from realms_test_paths import HERMES_ROOT, PLUGIN_ROOT

ROOT = HERMES_ROOT
load = runpy.run_path(str(PLUGIN_ROOT / "realms/_binding.py"))["load_runtime"]
pytestmark = pytest.mark.linux_only


@pytest.mark.parametrize("operation", ["vm-base-update", "session-setup"])
@pytest.mark.parametrize("replacement", ["clean", "new-job", "released"])
@pytest.mark.parametrize("outcome", [
    "succeeded", "failed", "cancelled", "running", "cancelling", "committing",
    "replaced-owner", "replaced-operation", "replaced-home",
])
def test_status_uses_fresh_scoped_receipt(tmp_path, monkeypatch, operation, replacement, outcome):
    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    flow = load("setup_flow")
    service = SimpleNamespace(home=home)
    profile = operation == "vm-base-update"
    owner = None if profile else "fixture-owner"
    row = {
        "id": "a" * 32, "home": str(home), "owner": owner,
        "operation": operation, "scope": "profile" if profile else "session",
        "kind": "omarchy-vm", "state": "running", "message": "working",
        "cancel_protocol": 1, "created_at": 1, "base_generation": "b" * 32,
        "base_commit_started": profile and outcome in {"succeeded", "committing"},
    }
    fresh: dict = dict(row, state=outcome, message="stored worker result")
    if outcome == "failed":
        fresh["error"] = "stored_failure"
    if outcome == "committing" or outcome.startswith("replaced-"):
        fresh["state"] = "running"
    if outcome == "replaced-owner":
        fresh["owner"] = "different-owner"
    if outcome == "replaced-operation":
        fresh.update(operation="session-setup" if profile else "vm-base-update",
                     scope="session" if profile else "profile",
                     owner="fixture-owner" if profile else None)
    if outcome == "replaced-home":
        fresh["home"] = str(tmp_path / "other-profile")

    holders = [flow._lock(service)]
    path = flow._path(service, row["id"])
    active = flow._root(service) / "active.json"
    flow.atomic_json(path, row)
    flow.atomic_json(active, {"id": row["id"]})
    read = flow._read
    reads = []
    stored = {}

    def scheduled_read(service, job_id):
        reads.append(job_id)
        record = read(service, job_id)
        if len(reads) == 1:
            # Complete/retire only AFTER status has really read running bytes.
            with flow._job_guard(service, job_id):
                flow.atomic_json(path, fresh)
            flow._release(holders.pop())
            if replacement != "released":
                holders.append(flow._lock(service))
                flow.atomic_json(active, {"id": "clean" if replacement == "clean" else "c" * 32})
            stored.update(data=path.read_bytes(), stat=path.stat(), active=active.read_bytes())
        return record

    monkeypatch.setattr(flow, "_read", scheduled_read)
    query = (lambda: flow.status_base_update(service, row["id"])) if profile else (
        lambda: flow.status(service, owner, row["id"]))
    try:
        if outcome.startswith("replaced-"):
            with pytest.raises(PermissionError):
                query()
        else:
            result = query()
            if outcome in {"succeeded", "failed", "cancelled"}:
                assert result["state"] == fresh["state"]
                assert result == flow._public(fresh)
            elif outcome == "cancelling":
                assert result["state"] == "cancelled"
            else:
                assert result["state"] == "failed"
                assert result["error"] == (
                    "publication_uncertain" if profile and outcome == "committing"
                    else "setup_failed" if profile else "interrupted")
            # Repeated polling must remain presentation-only, including interruptions.
            assert query() == result
        assert len(reads) >= 2
        assert path.read_bytes() == stored["data"]
        assert path.stat().st_ino == stored["stat"].st_ino
        assert path.stat().st_mtime_ns == stored["stat"].st_mtime_ns
        assert active.read_bytes() == stored["active"]
    finally:
        for fd in holders:
            flow._release(fd)
