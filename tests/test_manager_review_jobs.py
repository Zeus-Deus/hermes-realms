"""R5: fire-and-forget reaping and explicit, bounded result retention."""

from pathlib import Path
import subprocess
import sys
import time

import pytest

from realms import manager as manager_module
from realms.lifecycle import RealmError, atomic_json
from realms.manager import Manager


@pytest.fixture
def bounded_worker(tmp_path, monkeypatch):
    clock = tmp_path / "clock"
    atomic_json(clock, 0)
    worker_code = f"""import sys, json
from pathlib import Path
from realms import bootstrap
bootstrap.JOB_LIMIT = 4
bootstrap.JOB_RETENTION_SECONDS = 300
bootstrap.job_clock = lambda: json.loads(Path({str(clock)!r}).read_text())
bootstrap.worker(sys.argv[1])
"""
    guardian_code = f"""import sys, subprocess
from realms import supervisor
original = subprocess.Popen
def instrument(command, **kwargs):
    command = list(command)
    if 'realms.bootstrap' in command:
        at = command.index('realms.bootstrap')
        command[at - 1:at + 2] = ['-c', {worker_code!r}]
    return original(command, **kwargs)
subprocess.Popen = instrument
supervisor.run(sys.argv[1], sys.argv[2])
"""
    original_popen = subprocess.Popen

    def instrument(command, **kwargs):
        command = list(command)
        if "realms.supervisor" in command:
            at = command.index("realms.supervisor")
            command[at - 1 : at + 1] = ["-c", guardian_code]
        return original_popen(command, **kwargs)

    manager = Manager(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(manager_module.subprocess, "Popen", instrument)
        record = manager.start("bounded-worker")
    try:
        yield manager, record, clock
    finally:
        manager.stop(record["id"])


def wait_for(predicate):
    deadline = time.monotonic() + 6
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert predicate()


def test_completed_results_keep_window_then_expire_without_polling(bounded_worker):
    manager, record, clock = bounded_worker
    jobs = [
        manager.exec(record["id"], ["/bin/echo", "retained-result"]) for _ in range(4)
    ]
    wait_for(lambda: all(not Path(f"/proc/{job['pid']}").exists() for job in jobs))
    with pytest.raises(RealmError, match="capacity"):
        manager.exec(record["id"], ["/bin/true"])
    assert all(
        Path(job["stdout_path"]).read_text() == "retained-result\n" for job in jobs
    )
    atomic_json(clock, 299)
    assert (
        manager._rpc(record["id"], op="job", job_id=jobs[0]["job_id"])["stdout"]
        == "retained-result\n"
    )
    atomic_json(clock, 301)
    wait_for(lambda: all(not Path(job["stdout_path"]).exists() for job in jobs))
    assert not list(Path(record["runtime_dir"]).glob("*.stderr"))
    with pytest.raises(RealmError, match="(expired|unknown)"):
        manager._rpc(record["id"], op="job", job_id=jobs[0]["job_id"])
    assert (
        manager.exec(record["id"], ["/bin/echo", "slot-reused"], wait=True)["stdout"]
        == "slot-reused\n"
    )


def test_captured_output_is_bounded_and_reports_overflow(bounded_worker):
    manager, record, _ = bounded_worker
    code = "import os; os.write(1, b'A' * 1048576); os.write(2, b'B' * 1048576); print('finished')"
    result = manager.exec(record["id"], [sys.executable, "-c", code], wait=True)
    assert Path(result["stdout_path"]).stat().st_size <= 262144
    assert Path(result["stderr_path"]).stat().st_size <= 262144
    assert result["returncode"] == 0, (
        "capture limits must not kill or break the child's writes"
    )
    assert result["stdout_truncated"] and result["stderr_truncated"]
    assert result["output_complete"] is True
    assert result["output_limit_bytes"] == 262144
    assert result["stdout"] == "A" * 262144


def test_inherited_active_output_is_not_expired_or_truncated(bounded_worker, tmp_path):
    manager, record, clock = bounded_worker
    release = tmp_path / "release-output"
    code = f"""import os, time
from pathlib import Path
print('prefix', flush=True)
if os.fork() == 0:
    while not Path({str(release)!r}).exists():
        time.sleep(.01)
    print('suffix', flush=True)
    os._exit(0)
os._exit(0)
"""
    job = manager.exec(record["id"], [sys.executable, "-c", code])
    wait_for(lambda: not Path(f"/proc/{job['pid']}").exists())
    atomic_json(clock, 301)
    status = manager._rpc(record["id"], op="job", job_id=job["job_id"])
    assert status["returncode"] == 0 and status["output_complete"] is False
    assert Path(job["stdout_path"]).read_text() == "prefix\n"
    release.touch()
    wait_for(
        lambda: manager._rpc(record["id"], op="job", job_id=job["job_id"])[
            "output_complete"
        ]
    )
    assert Path(job["stdout_path"]).read_text() == "prefix\nsuffix\n"
    atomic_json(clock, 602)
    wait_for(lambda: not Path(job["stdout_path"]).exists())


def test_failed_exec_does_not_leak_capture_files(bounded_worker):
    manager, record, _ = bounded_worker
    for _ in range(8):
        with pytest.raises(RealmError, match="No such file"):
            manager.exec(record["id"], ["/definitely-absent-realms-review-command"])
    assert not list(Path(record["runtime_dir"]).glob("*.stdout"))
    assert not list(Path(record["runtime_dir"]).glob("*.stderr"))


def test_fire_and_forget_children_are_reaped_without_job_queries(tmp_path):
    manager = Manager(tmp_path)
    record = manager.start("unpolled-jobs")
    try:
        jobs = [manager.exec(record["id"], ["/bin/true"]) for _ in range(8)]
        deadline = time.monotonic() + 5
        while (
            any(Path(f"/proc/{job['pid']}").exists() for job in jobs)
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)
        assert all(not Path(f"/proc/{job['pid']}").exists() for job in jobs), (
            "unpolled jobs remain zombies"
        )
    finally:
        manager.stop(record["id"])
