"""Dashboard ownership contracts with the real runtime and a disposable home."""
import runpy
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from realms_test_paths import HERMES_ROOT, PLUGIN_ROOT

ROOT = HERMES_ROOT
PLUGIN = PLUGIN_ROOT
pytestmark = pytest.mark.linux_only


@pytest.fixture
def dashboard(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(home))
    (home / "config.yaml").write_text(
        "plugins:\n  realms:\n    default_mode: ask\n", encoding="utf-8"
    )
    api = runpy.run_path(str(PLUGIN / "dashboard/plugin_api.py"))
    service = api["get_integration"]()
    assert service.home == home.resolve()
    assert Path(api["_integration"].__file__).resolve() == PLUGIN / "realms/integration.py"
    import hermes_constants

    assert Path(hermes_constants.__file__).resolve() == ROOT / "hermes_constants.py"
    app = FastAPI()
    app.include_router(api["router"], prefix="/api/plugins/hermes-realms")
    try:
        with TestClient(app, base_url="http://localhost") as client:
            yield client, service
    finally:
        service.unload()


def ownership_rows(service):
    with service.owners.connection() as db:
        return (
            db.execute("SELECT id, mode FROM owners ORDER BY id").fetchall(),
            db.execute("SELECT kind, value, owner FROM aliases ORDER BY kind, value").fetchall(),
        )


@pytest.mark.parametrize("action", ["stop", "off"])
def test_session_action_uses_resolved_owner_without_affecting_peer(dashboard, monkeypatch, action):
    client, service = dashboard
    owner = service.bind(session_origin="fresh", session_id="a", runtime_session_id="runtime-a", stored_session_id="stored-a")
    peer = service.bind(session_origin="fresh", session_id="b", runtime_session_id="runtime-b", stored_session_id="stored-b")
    service.owners.set_mode(owner, "realm")
    service.owners.set_mode(peer, "realm")
    stopped = []
    monkeypatch.setattr(service, "_stop", stopped.append)
    response = client.post("/api/plugins/hermes-realms/realms/session/action", json={
        "runtime_session_id": "runtime-a", "stored_session_id": "stored-a", "action": action,
    })
    assert response.status_code == 200, response.text
    assert service.owners.mode(peer, "ask") == "realm"
    assert service.owners.mode(owner, "ask") == ("host" if action == "off" else "realm")
    assert stopped == ([owner] if action == "stop" else [])


def test_session_action_rejects_unbound_mixed_and_arbitrary_commands(dashboard, monkeypatch):
    client, service = dashboard
    service.bind(session_origin="fresh", session_id="a", runtime_session_id="runtime-a", stored_session_id="stored-a")
    service.bind(session_origin="fresh", session_id="b", runtime_session_id="runtime-b", stored_session_id="stored-b")
    before = ownership_rows(service)
    def forbidden(*args, **kwargs):
        pytest.fail("invalid session action reached dispatch")
    monkeypatch.setattr(service, "command", forbidden)
    for body, expected in [
        ({"action": "off"}, 403),
        ({"action": "stop", "stored_session_id": "unknown"}, 403),
        ({"action": "off", "runtime_session_id": "runtime-a", "stored_session_id": "stored-b"}, 403),
        ({"action": "delete", "stored_session_id": "stored-a"}, 422),
        ({"action": "off", "stored_session_id": "stored-a", "profile": "elsewhere"}, 422),
    ]:
        response = client.post("/api/plugins/hermes-realms/realms/session/action", json=body)
        assert response.status_code == expected, response.text
    assert ownership_rows(service) == before


def test_historical_listing_is_empty_without_registering_ownership(dashboard):
    client, service = dashboard
    owner = service.bind(session_origin="fresh", session_id="current", runtime_session_id="current-runtime")
    service.owners.set_mode(owner, "host")
    before = ownership_rows(service)
    for identity in (
        {},
        {"stored_session_id": "historical-stored"},
        {"runtime_session_id": "historical-runtime"},
        {"runtime_session_id": "historical-runtime", "stored_session_id": "historical-stored"},
    ):
        response = client.get("/api/plugins/hermes-realms/realms", params=identity)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["mode"] == service.manager.config.default_mode
        assert result["realms"] == []
        assert result["setup"]["ready"] is False
        assert ownership_rows(service) == before
    response = client.get(
        "/api/plugins/hermes-realms/realms", params={"runtime_session_id": "current-runtime"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "host"
    assert response.json()["realms"] == []
    assert ownership_rows(service) == before


def test_listing_and_watch_reject_invalid_ownership_without_binding(dashboard):
    client, service = dashboard
    service.bind(session_origin="fresh", session_id="a", runtime_session_id="runtime-a", stored_session_id="stored-a")
    service.bind(session_origin="fresh", session_id="b", runtime_session_id="runtime-b", stored_session_id="stored-b")
    before = ownership_rows(service)
    rejected = (
        {"runtime_session_id": "runtime-a", "stored_session_id": "unknown"},
        {"runtime_session_id": "unknown", "stored_session_id": "stored-a"},
        {"runtime_session_id": "runtime-a", "stored_session_id": "stored-b"},
        {"stored_session_id": " invalid "},
        {"runtime_session_id": ""},
        {"stored_session_id": ""},
        {"runtime_session_id": "", "stored_session_id": "unknown"},
        {"runtime_session_id": "unknown", "stored_session_id": ""},
        {"runtime_session_id": "", "stored_session_id": "stored-a"},
        {"runtime_session_id": "runtime-a", "stored_session_id": ""},
    )
    for identity in rejected:
        response = client.get("/api/plugins/hermes-realms/realms", params=identity)
        assert response.status_code == 403, (identity, response.text)
    for identity in (*rejected, {}, {"stored_session_id": "unknown"},
                     {"runtime_session_id": "unknown-runtime", "stored_session_id": "unknown"}):
        response = client.post("/api/plugins/hermes-realms/realms/missing/watch", json=identity)
        assert response.status_code == 403, (identity, response.text)
        assert response.json() == {"detail": "Session ownership mismatch"}
    assert ownership_rows(service) == before


def test_vm_watch_keeps_ownership_and_local_access_guards(dashboard, monkeypatch):
    client, service = dashboard
    service.bind(session_origin="fresh", session_id="owner", stored_session_id="history")
    service.bind(session_origin="fresh", session_id="other", stored_session_id="other-history")
    record = {"id": "v-fixture", "session_id": "owner", "status": "running"}
    monkeypatch.setattr(service.manager, "list", lambda: [])
    monkeypatch.setattr(service.vm, "list", lambda: [record])
    monkeypatch.setattr(service, "watch", lambda owner, realm_id: {"url": "/fixture-view"})
    with TestClient(client.app, base_url="http://localhost", client=("127.0.0.1", 1234)) as local:
        path = "/api/plugins/hermes-realms/realms/v-fixture/watch"
        identity = {"stored_session_id": "history"}
        assert local.post(path, json={"stored_session_id": "other-history"}).status_code == 403
        record["status"] = "starting"
        assert local.post(path, json=identity).status_code == 409
        record["status"] = "running"
        assert local.post(path, json=identity, headers={"forwarded": "for=external"}).status_code == 503
        response = local.post(path, json=identity)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {"url": "/fixture-view"}
