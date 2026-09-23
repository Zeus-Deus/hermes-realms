"""HTTP Delete qualification with real retained workspaces, inert compute only."""
from pathlib import Path
import runpy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from realms_test_paths import HERMES_ROOT, PLUGIN_ROOT

pytestmark = pytest.mark.linux_only
ROOT = HERMES_ROOT
fixtures = runpy.run_path(str(PLUGIN_ROOT / "tests/test_realms_delete_cli.py"))
load = fixtures["load"]
tree = fixtures["tree"]
regular = fixtures["regular"]
vm = fixtures["vm"]


@pytest.fixture(params=["regular", "vm"])
def target(request, monkeypatch):
    kind = request.param
    manager, record, peer = request.getfixturevalue(kind)
    monkeypatch.setenv("HERMES_HOME", str(manager.home))
    service = load("integration").RealmIntegration(manager.home)
    if kind == "regular":
        service.manager = manager
    else:
        service._vm = manager
    for row, runtime, stored in [(record, "native", "history"), (peer, "peer", "peer-history")]:
        service.owners.bind(session_id=row["session_id"], runtime_session_id=runtime,
                            stored_session_id=stored)
    api = runpy.run_path(str(PLUGIN_ROOT / "dashboard/plugin_api.py"))
    monkeypatch.setitem(api["resolve_owner"].__globals__, "get_integration", lambda: service)
    app = FastAPI()
    # Production mounts this router under mandatory token/OAuth middleware.
    # This fixture qualifies the route/owner/storage boundary, not that middleware.
    app.include_router(api["router"], prefix="/api/plugins/hermes-realms")
    for row in (record, peer):
        with service._lock, service.owners.activation_guard(row["session_id"]), manager.registry.lock():
            pass
    with TestClient(app) as client:
        yield client, service, manager, record, peer, kind


def post(target, action, body=None, realm_id=None):
    client, _, _, record, _, _ = target
    return client.post(f"/api/plugins/hermes-realms/realms/{realm_id or record['id']}/delete/{action}",
                       json=body if body is not None else {"runtime_session_id": "native", "stored_session_id": "history"})


def confirm(target, review, **identity):
    return post(target, "confirm", {"runtime_session_id": "native", "stored_session_id": "history",
                                    "consent": review["consent"], **identity})


def test_review_then_delete_removes_only_selected_workspace(target, monkeypatch):
    _, service, manager, record, peer, kind = target
    def forbidden(*args, **kwargs):
        pytest.fail("Delete must not reconcile inventory or stop compute")
    for selected in (service.manager, service.vm):
        monkeypatch.setattr(selected, "list", forbidden)
        monkeypatch.setattr(selected, "stop", forbidden)
    before = tree(manager.home)
    response = post(target, "prepare")
    assert response.status_code == 200, response.text
    review = response.json()
    assert set(review) == {"realm_id", "kind", "state", "consent", "details"}
    assert review["realm_id"] == record["id"]
    assert review["kind"] == ("realm" if kind == "regular" else "omarchy-vm")
    assert review["state"] == "stopped"
    assert isinstance(review["consent"], str) and review["consent"]
    assert review["details"] and all(isinstance(s, str) for s in review["details"])
    assert response.headers["cache-control"] == "no-store"
    assert tree(manager.home) == before
    key = "workspace_dir" if kind == "regular" else "session_dir"
    peer_before = tree(Path(peer[key]))
    response = confirm(target, review)
    assert response.status_code == 200, response.text
    assert response.json() == {"realm_id": record["id"], "deleted": True}
    assert response.headers["cache-control"] == "no-store"
    assert not Path(record[key]).exists()
    assert not manager.registry.path(record["id"]).exists()
    assert tree(Path(peer[key])) == peer_before


@pytest.mark.parametrize("case", ["identity", "owner", "mixed-owner", "profile", "record-profile",
    "running", "compute", "publication", "objects", "consent", "unicode-consent", "missing", "invalid-id"])
def test_confirmation_rejects_changed_authority_or_target(target, monkeypatch, case, tmp_path):
    _, service, manager, record, _, kind = target
    review = post(target, "prepare").json()
    identity = {}
    realm_id = record["id"]
    expected = 409
    if case == "identity":
        identity = {"runtime_session_id": None}  # Same owner, different supplied identity.
    elif case == "owner":
        identity = {"runtime_session_id": "peer", "stored_session_id": "peer-history"}
    elif case == "mixed-owner":
        identity = {"stored_session_id": "peer-history"}
        expected = 403
    elif case == "profile":
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "other-profile"))
        expected = 403
    elif case in ("running", "record-profile", "publication"):
        row = manager.registry.get(realm_id)
        if case == "running":
            row["status"] = "running"
        elif case == "record-profile":
            row["home"] = str(tmp_path / "other-profile")
        manager.registry.put(row)
    elif case == "compute":
        monkeypatch.setattr(load("manager" if kind == "regular" else "vm_manager"),
                            "scope_info", lambda unit: {"ActiveState": "active"})
    elif case == "objects":
        workspace = Path(record["workspace_dir" if kind == "regular" else "session_dir"])
        workspace.rename(workspace.with_name(workspace.name + "-saved"))
        workspace.mkdir(mode=0o700)
        (workspace / "foreign").write_bytes(b"replacement must survive")
    elif case in ("consent", "unicode-consent"):
        review["consent"] = "0" * 64 if case == "consent" else "\u2603"
    elif case == "missing":
        manager.registry.remove(realm_id)
        expected = 404
    elif case == "invalid-id":
        realm_id = "not-a-realm"
        expected = 409
    before = tree(manager.home)
    response = post(target, "confirm", {"runtime_session_id": "native", "stored_session_id": "history",
                    "consent": review["consent"], **identity}, realm_id=realm_id)
    assert response.status_code == expected, response.text
    assert set(response.json()) == {"detail"}
    assert len(response.json()["detail"]) < 250
    assert str(manager.home) not in response.text
    assert tree(manager.home) == before


@pytest.mark.parametrize("field", ["profile", "home", "path", "snapshot", "expected_snapshot", "owner", "session_id", "kind"])
@pytest.mark.parametrize("action", ["prepare", "confirm"])
def test_request_rejects_authority_bypass_fields(target, field, action):
    _, _, manager, _, _, _ = target
    before = tree(manager.home)
    body = {"runtime_session_id": "native", field: "bypass"}
    if action == "confirm":
        body["consent"] = "unreviewed"
    assert post(target, action, body).status_code == 422
    assert tree(manager.home) == before


@pytest.mark.parametrize("action", ["prepare", "confirm"])
def test_delete_serializes_with_owner_activation(target, monkeypatch, action):
    import fcntl
    import hashlib
    import os
    _, service, manager, record, _, _ = target
    review = post(target, "prepare").json()
    original = manager.delete_snapshot
    lock = service.owners.root / ("activation-" + hashlib.sha256(record["session_id"].encode()).hexdigest() + ".lock")
    def guarded(*args, **kwargs):
        fd = os.open(lock, os.O_RDWR)
        try:
            blocked = False
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                blocked = True
            assert blocked, "HTTP Delete must hold the real owner activation lock"
        finally:
            os.close(fd)
        return original(*args, **kwargs)
    monkeypatch.setattr(manager, "delete_snapshot", guarded)
    response = post(target, "prepare") if action == "prepare" else confirm(target, review)
    assert response.status_code == 200, response.text


def test_interrupted_delete_requires_fresh_review_before_retry(target, monkeypatch):
    _, _, manager, record, peer, kind = target
    key = "workspace_dir" if kind == "regular" else "session_dir"
    peer_before = tree(Path(peer[key]))
    review = post(target, "prepare").json()
    def interrupted(target_id):
        raise OSError("private path, viewer ticket and injected registry interruption")
    with monkeypatch.context() as fault:
        fault.setattr(manager.registry, "remove", interrupted)
        response = confirm(target, review)
    assert response.status_code == 503, response.text
    assert "private" not in response.text and "ticket" not in response.text
    assert "review" in response.text.lower()
    assert manager.registry.get(record["id"])["status"] == "deleting"
    assert not Path(record[key]).exists()
    assert confirm(target, review).status_code == 409
    response = post(target, "prepare")
    assert response.status_code == 200, response.text
    retry = response.json()
    assert retry["state"] == "deleting" and retry["consent"] != review["consent"]
    assert confirm(target, retry).json() == {"realm_id": record["id"], "deleted": True}
    assert not manager.registry.path(record["id"]).exists()
    assert confirm(target, retry).status_code == 404
    assert tree(Path(peer[key])) == peer_before


@pytest.mark.parametrize("change", ["publication", "objects", "missing"])
def test_manager_lock_rechecks_after_http_digest_comparison(target, monkeypatch, change):
    _, _, manager, record, _, kind = target
    review = post(target, "prepare").json()
    original = manager.delete
    before = {}
    def racing_delete(realm_id, **kwargs):
        if change == "publication":
            manager.registry.put(manager.registry.get(realm_id))
        elif change == "objects":
            workspace = Path(record["workspace_dir" if kind == "regular" else "session_dir"])
            workspace.rename(workspace.with_name(workspace.name + "-saved"))
            workspace.mkdir(mode=0o700)
            (workspace / "new-work").write_bytes(b"replacement must survive")
        else:
            manager.registry.remove(realm_id)
        before.update(tree(manager.home))
        return original(realm_id, **kwargs)
    monkeypatch.setattr(manager, "delete", racing_delete)
    response = confirm(target, review)
    assert response.status_code == (404 if change == "missing" else 409), response.text
    assert before and tree(manager.home) == before


@pytest.mark.parametrize("action", ["prepare", "confirm"])
def test_owner_is_resolved_again_after_waiting_for_activation(target, monkeypatch, action):
    from contextlib import contextmanager
    _, service, _, _, peer, _ = target
    review = post(target, "prepare").json()
    original = service.owners.activation_guard
    @contextmanager
    def changed(owner):
        with original(owner):
            with service.owners.connection() as db:
                db.execute("UPDATE aliases SET owner=? WHERE value IN ('native', 'history')", (peer["session_id"],))
            yield
    monkeypatch.setattr(service.owners, "activation_guard", changed)
    response = post(target, "prepare") if action == "prepare" else confirm(target, review)
    assert response.status_code == 403, response.text


@pytest.mark.parametrize("state", ["running", "starting", "stopping"])
def test_prepare_requires_explicit_stop_without_mutation(target, state):
    _, _, manager, record, _, _ = target
    current = manager.registry.get(record["id"])
    current["status"] = state
    manager.registry.put(current)
    before = tree(manager.home)
    response = post(target, "prepare")
    assert response.status_code == 409, response.text
    assert "stop first" in response.text.lower()
    assert tree(manager.home) == before


@pytest.mark.parametrize("invalid", [False, True])
def test_literal_id_dispatch_never_probes_other_kind(target, monkeypatch, invalid):
    _, service, manager, record, _, kind = target
    opposite = service.vm if kind == "regular" else service.manager
    def forbidden(*args, **kwargs):
        raise AssertionError("Delete dispatched to an unselected manager")
    monkeypatch.setattr(opposite, "delete_snapshot", forbidden)
    if invalid:
        monkeypatch.setattr(manager, "delete_snapshot", forbidden)
        response = post(target, "prepare", realm_id=record["id"] + "suffix")
        assert response.status_code == 409
    else:
        manager.registry.remove(record["id"])
        assert post(target, "prepare").status_code == 404
