"""Delegated subagents share their session's Realm and never exceed the parent."""
import json
import runpy
import threading

import pytest
from realms_test_paths import PLUGIN_ROOT

pytestmark = pytest.mark.platforms("linux")


def load(name):
    return runpy.run_path(str(PLUGIN_ROOT / "realms/_binding.py"))["load_runtime"](name)


@pytest.fixture
def service(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    return load("integration").RealmIntegration(home)


def _record(service, owner, suffix="a"):
    record = {"id": "r-" + suffix * 24, "generation": "b" * 32, "status": "running",
              "home": str(service.home), "session_id": owner}
    (service.home / "realms" / (record["id"] + ".json")).write_text(json.dumps(record), encoding="utf-8")
    return record


def _middleware(service, **identity):
    calls = []
    result = service.execution_middleware(tool_name="terminal", args={"command": "true"},
                                          next_call=lambda args: calls.append(args) or "ran", **identity)
    return result, calls


def test_concurrent_subagents_join_the_parents_realm_owner(service):
    parent = service.bind(session_origin="fresh", session_id="parent", task_id="parent-task")
    owners, errors = [], []

    def start(index):
        try:
            owners.append(service.bind(session_origin="fresh", session_id=f"child-{index}",
                                       parent_session_id="parent"))
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=start, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == [] and owners == [parent] * 4
    record = _record(service, parent)
    # Each child's own task alias still resolves to the one session Realm.
    for index in range(4):
        assert _middleware(service, session_id=f"child-{index}", task_id=f"child-task-{index}")[0] == "ran"
        assert service.owners.resolve(session_id=f"child-{index}", task_id=f"child-task-{index}") == parent
        resources = load("permission_transition")._resources(service, service.owners.resolve(session_id=f"child-{index}"))
        assert [row["id"] for row in resources] == [record["id"]]


def test_reads_during_sibling_writes_are_not_mistaken_for_a_permission_hold(service):
    """Parallel subagents bind (write) while their siblings' tools read the owner store.
    A read waits for a sibling's in-flight write to finish instead of reporting a legacy hold."""
    service.bind(session_origin="fresh", session_id="parent")
    for index in range(4):
        service.bind(session_origin="fresh", session_id=f"child-{index}", parent_session_id="parent")
    stop = threading.Event()
    denied = []

    def writer():
        index = 0
        while not stop.is_set():
            index += 1
            service.bind(session_origin="fresh", session_id=f"late-{index}", parent_session_id="parent")

    def reader(index):
        for turn in range(150):
            result, _ = _middleware(service, session_id=f"child-{index}", task_id=f"child-task-{index}-{turn}")
            if result != "ran":
                denied.append(result)

    writers = [threading.Thread(target=writer) for _ in range(2)]
    readers = [threading.Thread(target=reader, args=(i,)) for i in range(4)]
    for thread in writers + readers:
        thread.start()
    for thread in readers:
        thread.join()
    stop.set()
    for thread in writers:
        thread.join()
    assert denied == []


def test_subagent_never_gets_more_than_its_parent(service):
    held = service.bind(session_id="old-parent")  # not fresh: legacy permissions pending
    assert service.owners.permission(held)["state"] == "legacy-pending"
    assert service.bind(session_origin="fresh", session_id="child", parent_session_id="old-parent") == held
    result, calls = _middleware(service, session_id="child", task_id="child-task")
    assert calls == [] and json.loads(result)["error_code"] == "legacy_permission_review_required"

    disabled = service.bind(session_origin="fresh", session_id="host-parent")
    service.owners.set_mode(disabled, "host")
    service.bind(session_origin="fresh", session_id="host-child", parent_session_id="host-parent")
    with pytest.raises(PermissionError, match="disabled"):
        service.command("on", session_id="host-child", _agent=True)
    with pytest.raises(PermissionError, match="disabled"):
        service.command("on --separate", session_id="host-child", _agent=True)


def test_subagent_of_an_unknown_parent_is_refused_not_given_its_own_authority(service):
    with pytest.raises(PermissionError):
        service.bind(session_origin="fresh", session_id="orphan", parent_session_id="never-bound")
    result, calls = _middleware(service, session_id="orphan", task_id="orphan-task")
    assert calls == [] and json.loads(result)["error_code"] == "realms_session_unbound"


def test_session_with_no_record_reports_unbound_honestly(service):
    result, calls = _middleware(service, session_id="skipped-start", task_id="task")
    payload = json.loads(result)
    assert calls == []
    assert payload["error_code"] == "realms_session_unbound"
    assert "not been converted" not in payload["error"]


def test_realm_outlives_subagents_and_ends_with_the_session(service, monkeypatch):
    parent = service.bind(session_origin="fresh", session_id="parent")
    service.bind(session_origin="fresh", session_id="child", parent_session_id="parent")
    stopped = []
    monkeypatch.setattr(service, "stop", stopped.append)
    service.finalize(session_id="child")
    assert stopped == []
    service.finalize(session_id="parent")
    assert stopped == [parent]


def test_separate_realm_is_opt_in_and_still_ends_with_the_session(service, monkeypatch):
    parent = service.bind(session_origin="fresh", session_id="parent")
    service.owners.set_mode(parent, "ask")
    service.bind(session_origin="fresh", session_id="child", parent_session_id="parent")
    activated = []
    monkeypatch.setattr(service, "_activate", lambda owner, arguments, **kw: activated.append((owner, arguments)))
    service.command("on --separate", session_id="child", task_id="child-task", _agent=True)
    separate = service.owners.resolve(session_id="child", task_id="child-task")
    assert separate != parent and activated == [(separate, [])]
    assert service.owners.resolve(session_id="parent") == parent
    # Its own Realm, but no more than the parent: same conversion state and routing.
    assert service.owners.permission(separate)["state"] == "optional"
    assert service.owners.mode(separate, None) == "ask"

    with pytest.raises(PermissionError, match="subagent"):
        service.command("on --separate", session_id="parent", _agent=True)

    stopped = []
    monkeypatch.setattr(service, "stop", stopped.append)
    service.finalize(session_id="child")
    assert stopped == []
    service.finalize(session_id="parent")
    assert sorted(stopped) == sorted([parent, separate])


def test_separate_realm_follows_the_parent_being_turned_off(service, monkeypatch):
    parent = service.bind(session_origin="fresh", session_id="parent")
    service.bind(session_origin="fresh", session_id="child", parent_session_id="parent")
    monkeypatch.setattr(service, "_activate", lambda owner, arguments, **kw: service.owners.set_mode(owner, "realm"))
    service.command("on --separate", session_id="child", task_id="child-task", _agent=True)
    separate = service.owners.resolve(session_id="child", task_id="child-task")
    assert separate != parent and service.owners.mode(separate, None) == "realm"

    service.command("off", session_id="parent")
    assert service.owners.mode(separate, None) == "host"
    with pytest.raises(PermissionError, match="disabled"):
        service.command("on", session_id="child", task_id="child-task", _agent=True)
    with pytest.raises(Exception, match="disabled"):
        service.select_computer_use_target(session_id="child", task_id="child-task")


def test_separate_realm_follows_a_parent_permission_hold(service):
    parent = service.bind(session_origin="fresh", session_id="parent")
    service.bind(session_origin="fresh", session_id="child", parent_session_id="parent")
    separate = service._separate(parent, {"session_id": "child", "task_id": "child-task"})
    with service.owners.connection() as db:
        db.execute("UPDATE owners SET execution_contract=NULL WHERE id=?", (parent,))
    assert service.owners.permission(parent)["state"] == "legacy-pending"
    assert service.owners.permission(separate)["state"] == "legacy-pending"
    result, calls = _middleware(service, session_id="child", task_id="child-task")
    assert calls == [] and json.loads(result)["error_code"] == "legacy_permission_review_required"
