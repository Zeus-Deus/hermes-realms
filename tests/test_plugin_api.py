"""Real host dashboard mount/auth and profile-local ownership tests."""

import json
from pathlib import Path

from test_integration import install_plugin


def test_cold_dashboard_process_mounts_api_without_native_plugin_import(tmp_path):
    import os
    import secrets
    import socket
    import subprocess
    import sys
    import time
    import urllib.request
    import urllib.error
    import hermes_cli.plugins

    install_plugin(tmp_path)
    token = secrets.token_urlsafe(24)
    env = dict(
        os.environ,
        HERMES_HOME=str(tmp_path),
        HERMES_DASHBOARD_SESSION_TOKEN=token,
        PYTHONPATH=str(Path(hermes_cli.plugins.__file__).resolve().parents[1]),
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    address = "http://127.0.0.1:" + str(listener.getsockname()[1])
    script = (
        "import socket,sys,uvicorn; from hermes_cli.web_server import app; "
        'uvicorn.Server(uvicorn.Config(app,log_level="error",access_log=False,lifespan="off"))'
        ".run(sockets=[socket.socket(fileno=int(sys.argv[1]))])"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(listener.fileno())],
        cwd=tmp_path,
        env=env,
        pass_fds=(listener.fileno(),),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    listener.close()
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                response = urllib.request.urlopen(
                    urllib.request.Request(
                        address + "/api/plugins/hermes-realms/realms",
                        headers={"X-Hermes-Session-Token": token},
                    ),
                    timeout=1,
                )
                break
            except urllib.error.HTTPError as exc:
                assert False, f"Live API failed: {exc.code} {exc.read()!r}"
            except (OSError, urllib.error.URLError):
                assert time.monotonic() < deadline and process.poll() is None
                time.sleep(0.05)
        with response:
            assert json.load(response) == {"mode": "realm", "realms": []}
    finally:
        process.terminate()
        process.communicate(timeout=10)


def test_watch_mints_scoped_ticket_and_shared_takeover_pauses_agent(
    tmp_path, monkeypatch
):
    import subprocess
    import sys
    from urllib.parse import urlsplit, parse_qs
    from websockets.sync.client import connect
    from fastapi.testclient import TestClient
    from fastapi.routing import APIRoute
    import importlib

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    install_plugin(tmp_path)
    from hermes_cli import web_server
    from hermes_cli.web_server_dashboard import _mount_plugin_api_routes

    _mount_plugin_api_routes()
    # Use the actual mounted endpoint's factory, not the generic library namespace.
    route = next(
        route for route in web_server.app.routes
        if isinstance(route, APIRoute)
        and route.path == "/api/plugins/hermes-realms/realms/{realm_id}/watch"
    )
    service = route.endpoint.__globals__["get_integration"](tmp_path)
    bridge = importlib.import_module(type(service).__module__.rsplit(".", 1)[0] + ".bridge")
    service.bind(
        session_id="watch-a",
        runtime_session_id="runtime-a",
        stored_session_id="stored-a",
    )
    service.bind(
        session_id="watch-b",
        runtime_session_id="runtime-b",
        stored_session_id="stored-b",
    )
    # Same router uses request-scoped profile, despite native mount in another test.
    client = TestClient(
        web_server.app, base_url="http://127.0.0.1", client=("127.0.0.1", 5000)
    )
    headers = {web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN}
    owner = {"runtime_session_id": "runtime-a", "stored_session_id": "stored-a"}
    try:
        record = service.ready("watch-a")
        path = "/api/plugins/hermes-realms/realms/" + record["id"] + "/watch"
        response = client.post(path, headers=headers, json=owner)
        assert response.status_code == 200, response.text
        url = response.json()["url"]
        assert client.post(path, headers=headers, json=owner).json()["url"] != url
        assert (
            client.post(
                path, headers=headers, json={"runtime_session_id": "runtime-b"}
            ).status_code
            == 403
        )
        remote = TestClient(
            web_server.app,
            base_url="https://remote.example",
            client=("192.0.2.1", 5000),
        )
        assert remote.post(path, headers=headers, json=owner).status_code == 503
        assert service.status("watch-a")["realms"][0]["window_count"] == 0
        parts = urlsplit(url)
        origin = parts.scheme + "://" + parts.netloc
        ticket = parse_qs(parts.fragment)["ticket"][0]
        with connect(
            "ws://" + parts.netloc + "/api/realms/" + record["id"] + "/vnc?control=1",
            origin=origin,
            subprotocols=["binary", "realm." + ticket],
        ) as ws:
            assert ws.recv(timeout=3).startswith(b"RFB ")
            assert service.input_allowed(record["id"]) is False
            assert service.status("watch-a")["realms"][0]["controlled"] is True
            child = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import sys;from realms.integration import RealmIntegration; print(RealmIntegration(sys.argv[1]).input_allowed(sys.argv[2]))",
                    str(tmp_path),
                    record["id"],
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=True,
            )
            assert child.stdout.strip() == "False"
        assert service.command("watch", session_id="watch-a")["url"].startswith(origin)
        viewer = bridge.get_profile_viewer(tmp_path)
        assert viewer.origin == origin
        assert viewer._thread is not None and viewer._thread.is_alive()
        service.command("stop", session_id="watch-a")
        assert client.post(path, headers=headers, json=owner).status_code == 404
        service.unload()
        assert viewer._thread is None
    finally:
        service.unload()
        bridge.close_profile_viewer(tmp_path)
        for record in service.manager.list():
            service.manager.stop(record["id"])


def test_real_dashboard_mount_requires_auth_and_same_owner_pair(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from realms.integration import get_integration

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    install_plugin(tmp_path)
    service = get_integration(tmp_path)
    service.bind(
        session_id="a", runtime_session_id="runtime-a", stored_session_id="stored-a"
    )
    service.bind(
        session_id="b", runtime_session_id="runtime-b", stored_session_id="stored-b"
    )
    from hermes_cli import web_server
    from hermes_cli.web_server_dashboard import _mount_plugin_api_routes

    _mount_plugin_api_routes()
    client = TestClient(web_server.app)
    url = "/api/plugins/hermes-realms/realms"
    assert client.get(url).status_code == 401
    headers = {web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN}
    response = client.get(
        url,
        headers=headers,
        params={"runtime_session_id": "runtime-a", "stored_session_id": "stored-a"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"mode": "realm", "realms": []}
    assert client.get(url, headers=headers).json() == {"mode": "realm", "realms": []}
    for pair in (
        {"runtime_session_id": "runtime-a", "stored_session_id": "stored-b"},
        {"runtime_session_id": "runtime-a", "stored_session_id": "unknown"},
    ):
        assert client.get(url, headers=headers, params=pair).status_code == 403
        assert (
            client.post(
                url + "/r-does-not-exist/watch", headers=headers, json=pair
            ).status_code
            == 403
        )
    assert (
        client.post(
            url + "/r-does-not-exist/watch",
            headers=headers,
            json={"runtime_session_id": "runtime-a"},
        ).status_code
        == 404
    )
