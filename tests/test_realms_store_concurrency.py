"""Other Hermes processes write the plugin's stores while this one reads them.

Each CLI chat and each profile backend is its own process. A subagent starting in
one chat writes the ownership store while every other chat's tool calls read it.
Such a write must never surface as a permission hold in a conversation that is
already allowed to run, and a store that genuinely cannot be read must say so
instead of asking the user for a permission review.
"""
import json
from pathlib import Path
import runpy
import subprocess
import sys
import time

import pytest
from realms_test_paths import PLUGIN_ROOT

pytestmark = pytest.mark.platforms("linux")

BINDING = str(PLUGIN_ROOT / "realms/_binding.py")
SECONDS = 3.0


def load(name):
    return runpy.run_path(BINDING)["load_runtime"](name)


# Child processes load the plugin exactly as a separate Hermes process does.
OWNER_WRITER = """
import runpy, sys, time
store = runpy.run_path(sys.argv[1])["load_runtime"]("integration").OwnershipStore(sys.argv[2])
end, tag, count = time.time() + float(sys.argv[3]), sys.argv[4], 0
while time.time() < end:
    # What every subagent start and every new turn's task alias does.
    store.bind(session_origin="fresh", session_id=f"{tag}-{count}", task_id=f"{tag}-task-{count}")
    count += 1
    time.sleep(0.005)
print(count)
"""

VIEWER_WRITER = """
import runpy, sys, time
authority = runpy.run_path(sys.argv[1])["load_runtime"]("viewer_state").ControlAuthority(sys.argv[2])
end, count = time.time() + float(sys.argv[3]), 0
while time.time() < end:
    authority.revoke("r-elsewhere")   # a Watch viewer revoking its tickets
    count += 1
    time.sleep(0.005)
print(count)
"""


def _writers(code, target, count):
    return [subprocess.Popen([sys.executable, "-I", "-c", code, BINDING, str(target), str(SECONDS), f"w{index}"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for index in range(count)]


def _finish(writers):
    writes = 0
    for writer in writers:
        out, err = writer.communicate(timeout=60)
        assert writer.returncode == 0, err
        writes += int(out)
    return writes


@pytest.fixture
def service(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    service = load("integration").RealmIntegration(home)
    yield service
    service.unload()


def _middleware(service, **identity):
    calls = []
    result = service.execution_middleware(tool_name="terminal", args={"command": "true"},
                                          next_call=lambda args: calls.append(args) or "ran", **identity)
    return result, calls


def test_other_processes_starting_subagents_never_hold_an_allowed_conversation(service):
    service.bind(session_origin="fresh", session_id="chat", task_id="chat-task")
    writers = _writers(OWNER_WRITER, service.home, 2)
    reads, denied = 0, []
    while any(writer.poll() is None for writer in writers):
        result, calls = _middleware(service, session_id="chat", task_id="chat-task")
        reads += 1
        if result != "ran" or not calls:
            denied.append(result)
    writes = _finish(writers)
    assert writes > 50 and reads > 50, (writes, reads)  # the two really overlapped
    assert denied == [], f"{len(denied)}/{reads} tool calls refused, first: {denied[0]}"


def test_realm_control_reads_survive_another_process_writing_the_viewer_store(tmp_path):
    authority = load("viewer_state").ControlAuthority
    directory = tmp_path / "profile" / "realms" / "viewer"
    authority(directory)
    writers = _writers(VIEWER_WRITER, directory, 2)
    reads, failures = 0, []
    while any(writer.poll() is None for writer in writers):
        try:
            assert authority.read_agent_epoch(directory, "r-mine") == 0
        except Exception as exc:  # noqa: BLE001 - every failure is the finding
            failures.append(f"{type(exc).__name__}: {exc}")
        reads += 1
    writes = _finish(writers)
    assert writes > 50 and reads > 50, (writes, reads)
    assert failures == [], f"{len(failures)}/{reads} reads failed, first: {failures[0]}"


def test_unreadable_store_is_reported_as_such_not_as_a_permission_review(service):
    service.bind(session_origin="fresh", session_id="chat", task_id="chat-task")
    journal = Path(str(service.owners.path) + "-journal")
    journal.write_bytes(b"left behind by a writer that crashed")
    result, calls = _middleware(service, session_id="chat", task_id="chat-task")
    payload = json.loads(result)
    assert calls == []  # still fails closed: nothing ran
    assert payload["error_code"] == "realms_store_unavailable"
    assert "/realm review" not in payload["error"] and "not been converted" not in payload["error"]
    assert "not a permission" in payload["error"]
    journal.unlink()
    assert _middleware(service, session_id="chat", task_id="chat-task")[0] == "ran"


def test_an_earlier_conversation_still_awaits_its_review(service):
    """The honest storage error must not weaken a real legacy hold."""
    service.bind(session_id="earlier-chat")  # not fresh: converted only by an explicit review
    result, calls = _middleware(service, session_id="earlier-chat", task_id="t")
    assert calls == [] and json.loads(result)["error_code"] == "legacy_permission_review_required"


HOLD_SHARED = """
import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)
fcntl.flock(fd, fcntl.LOCK_SH)   # another process's long read of the store
print("held", flush=True)
time.sleep(float(sys.argv[2]))
"""


def test_tool_calls_in_a_known_turn_only_read_the_store(service):
    """Only a new alias is a write. Every other tool call must not queue behind
    (or block) other chats' store reads."""
    service.bind(session_origin="fresh", session_id="chat", task_id="turn-1")
    reader = subprocess.Popen([sys.executable, "-I", "-c", HOLD_SHARED, str(service.owners.root), "4"],
                              stdout=subprocess.PIPE, text=True)
    try:
        assert reader.stdout.readline().strip() == "held"
        started = time.monotonic()
        assert _middleware(service, session_id="chat", task_id="turn-1")[0] == "ran"
        assert time.monotonic() - started < 1.0
    finally:
        reader.kill()
        reader.wait()
    # A new turn's task id is still recorded, and inherits the conversation's owner.
    assert _middleware(service, session_id="chat", task_id="turn-2")[0] == "ran"
    assert service.owners.resolve(task_id="turn-2") == "chat"


def test_last_resort_load_failure_does_not_ask_for_a_permission_review(tmp_path, monkeypatch):
    """When not even the degraded reader loads, every call is refused, but honestly."""
    from types import SimpleNamespace
    plugin = runpy.run_path(str(PLUGIN_ROOT / "plugin.py"))
    registered = {}
    ctx = SimpleNamespace(
        register_middleware=lambda kind, fn: registered.update(middleware=fn),
        register_tool=lambda *a, **k: None, register_command=lambda *a, **k: None)
    monkeypatch.setitem(plugin["_register_degraded"].__globals__, "_load_runtime",
                        lambda name: (_ for _ in ()).throw(ImportError("reader unavailable")))
    plugin["_register_degraded"](ctx, ImportError("runtime unavailable"))
    payload = json.loads(registered["middleware"](tool_name="terminal", args={}, next_call=None,
                                                  session_id="chat"))
    assert payload["error_code"] == "realms_store_unavailable"
    assert "/realm review" not in payload["error"]


def test_store_access_nested_in_a_write_does_not_deadlock(service):
    """Bind's write transaction can read the store again (remembered bulk decision)."""
    started = time.monotonic()
    owners = service.owners
    with owners.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        assert owners.resolve(allow_missing=True, session_id="nobody") is None
        db.execute("INSERT INTO owners(id) VALUES ('nested')")
    assert owners.permission("nested")["state"] == "legacy-pending"
    assert time.monotonic() - started < 5
