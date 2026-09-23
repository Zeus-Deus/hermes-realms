"""Registered profile routes; real HTTP, ledger and workers, inert VM compute.

Production mounts this router under token/OAuth middleware. The final fixture
exercises the real loopback token gate with an inert token; OAuth and native
Desktop transport are outside this fixture qualification.
"""
import json
from pathlib import Path
import runpy
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from realms_test_paths import HERMES_ROOT, PLUGIN_ROOT

common = runpy.run_path(str(Path(__file__).with_name("test_realms_base_update_publication.py")))
load, profile, update = common["load"], common["profile"], common["update"]
wait_for, join_job, BINDING = common["wait_for"], common["join_job"], common["BINDING"]
ROOT = HERMES_ROOT
pytestmark = pytest.mark.linux_only


@pytest.fixture
def api(update):
    module = runpy.run_path(str(PLUGIN_ROOT / "dashboard/plugin_api.py"))
    app = FastAPI()
    app.include_router(module["router"])
    with TestClient(app) as client:
        yield client, module, update


def test_profile_routes_complete_exact_worker_without_session_database(api, monkeypatch):
    client, module, (profile, manager, old, _, gate) = api
    def forbidden(*a, **kw):
        pytest.fail("Profile routes accessed sessions or resolved latest")
    monkeypatch.setattr(load("integration"), "OwnershipStore", forbidden)
    monkeypatch.setattr(load("vm_manager").VmManager, "latest_version", forbidden)
    payload = {"release": "4.0.3", "review_binding": BINDING}
    before = sorted(profile.home.rglob("*"))
    prepared = client.post("/realms/vm/update/prepare", json=payload)
    assert prepared.status_code == 200, prepared.text
    assert prepared.headers["cache-control"] == "no-store"
    assert sorted(profile.home.rglob("*")) == before  # Later is no write request.
    for extra in ({"home": str(profile.home)}, {"runtime_session_id": "owner"}, {"packages": []}):
        assert client.post("/realms/vm/update/prepare", json=payload | extra).status_code == 422
    response = client.post("/realms/vm/update/start", json=payload | {"consent": prepared.json()["consent"]})
    assert response.status_code == 200, response.text
    job = response.json()
    url = f"/realms/vm/update/jobs/{job['id']}"
    try:
        wait_for((gate / "installed").exists)
        inventory = client.get("/realms/vm/settings")
        assert inventory.status_code == 200, inventory.text
        assert inventory.json()["base_update_job"]["id"] == job["id"]
        assert client.post(url + "/cancel", json={"home": "foreign"}).status_code == 422
        assert client.get(url).json()["scope"] == "profile"
        (gate / "install-release").touch()
        wait_for((gate / "retiring").exists)
        late = client.post(url + "/cancel", json={}).json()
        assert late["state"] == "running" and not late["cancellable"]
    finally:
        (gate / "install-release").touch()
        (gate / "retire-release").touch()
        join_job(job)
    assert client.get(url).json()["state"] == "succeeded"
    assert client.get("/realms/vm/settings").json()["base_update_job"]["state"] == "succeeded"
    assert not (profile.home / "realms/sessions.sqlite3").exists()
    assert manager.base_home() != old


def test_routes_refuse_cross_session_profile_and_home_receipts(api, monkeypatch, tmp_path):
    client, module, (profile, _, _, _, _) = api
    flow = load("setup_flow")
    service = load("integration").RealmIntegration(profile.home)
    service.bind(session_origin="fresh", session_id="owner", runtime_session_id="runtime")
    monkeypatch.setitem(module["resolve_owner"].__globals__, "get_integration", lambda: service)
    fd = flow._lock(profile)
    session = {"id": "a" * 32, "owner": "owner", "home": str(profile.home),
               "kind": "realm", "state": "succeeded", "message": "session", "created_at": 10}
    plan = load("setup_plan").build_base_update_plan(profile.home, "4.0.3", BINDING)
    job = {"id": "b" * 32, "owner": None, "home": str(profile.home),
           "kind": "omarchy-vm", "scope": "profile", "operation": "vm-base-update",
           "state": "failed", "message": "profile", "created_at": 1, "plan": plan,
           "base_generation": "c" * 32}
    try:
        for row in (session, job):
            flow.atomic_json(flow._path(profile, row["id"]), row)
        flow.atomic_json(flow._root(profile) / "active.json", {"operation": "clean", "id": None})
        before = flow._path(profile, job["id"]).read_bytes()
        for suffix, method in (("?runtime_session_id=runtime", client.get), ("/cancel", client.post)):
            kwargs = {} if method == client.get else {"json": {"runtime_session_id": "runtime"}}
            assert method(f"/realms/setup/jobs/{job['id']}" + suffix, **kwargs).status_code == 403
        assert client.get(f"/realms/vm/update/jobs/{session['id']}").status_code == 403
        assert client.post(f"/realms/vm/update/jobs/{session['id']}/cancel", json={}).status_code == 403
        assert flow.latest_base_update(SimpleNamespace(home=profile.home))["id"] == job["id"]
        assert flow.latest(service, "owner")["id"] == session["id"]
        assert flow._path(profile, job["id"]).read_bytes() == before
        foreign = SimpleNamespace(home=tmp_path / "foreign")
        flow._root(foreign, create=True)
        flow.atomic_json(flow._path(foreign, job["id"]), job)
        for call in (flow.status_base_update, flow.cancel_base_update):
            with pytest.raises(PermissionError):
                call(foreign, job["id"])
        for patch in ({"scope": "session"}, {"operation": "unknown"}, {"owner": "owner"}):
            flow.atomic_json(flow._path(profile, job["id"]), job | patch)
            assert client.get(f"/realms/vm/update/jobs/{job['id']}").status_code == 403
    finally:
        flow._release(fd)


def test_clean_is_excluded_through_actual_worker_retirement(update):
    profile, manager, old, _, gate = update
    flow, cli = load("setup_flow"), load("cli")
    iso = manager.data / "iso/omarchy-4.0.3.iso"
    iso.parent.mkdir(parents=True)
    iso.write_bytes(b"inert installer input")
    proposal = flow.prepare_base_update(profile, "4.0.3", BINDING)
    job = flow.start_base_update(profile, "4.0.3", BINDING, proposal["consent"])
    try:
        wait_for((gate / "installed").exists)
        with pytest.raises(ValueError, match="active"):
            cli.clean(manager)
        assert iso.read_bytes() == b"inert installer input"
        (gate / "install-release").touch()
        wait_for((gate / "retiring").exists)
        with pytest.raises(ValueError, match="active"):
            cli.clean(manager)
    finally:
        (gate / "install-release").touch()
        (gate / "retire-release").touch()
        join_job(job)
    result = cli.clean(manager)
    assert iso.exists()  # It is now the selected base's ISO.
    assert old.exists() and result["removed"] == []
    assert json.loads((flow._root(profile) / "active.json").read_text())["operation"] == "clean"


def test_clean_excludes_new_profile_and_session_setup(update, monkeypatch):
    profile, manager, _, _, _ = update
    flow = load("setup_flow")
    service = load("integration").RealmIntegration(profile.home)
    service.bind(session_origin="fresh", session_id="owner", runtime_session_id="runtime")
    session_plan = load("setup_plan").build_plan(service, "owner", "realm")
    session_plan.update(blockers=[], packages=[])
    monkeypatch.setattr(flow, "build_plan", lambda *a: session_plan)
    proposal = flow.prepare_base_update(profile, "4.0.3", BINDING)
    original = manager.base_status
    def during_clean():
        assert json.loads((flow._root(profile) / "active.json").read_text())["operation"] == "clean"
        with pytest.raises(ValueError, match="active"):
            flow.start_base_update(profile, "4.0.3", BINDING, proposal["consent"])
        with pytest.raises(ValueError, match="active"):
            flow.start(service, "owner", "realm", flow._consent(session_plan), {"runtime_session_id": "runtime"})
        return original()
    monkeypatch.setattr(manager, "base_status", during_clean)
    load("cli").clean(manager)
    assert flow.latest_base_update(profile) is None


def test_cancelled_package_drains_before_update_and_clean_admission(update, monkeypatch):
    import sys
    profile, manager, _, _, gate = update
    flow, worker, plans = load("setup_flow"), load("setup_worker"), load("setup_plan")
    original = plans._prerequisite_plan
    def packages(home, kind):
        plan = original(home, kind)
        plan["packages"] = ["qemu-full"]
        return plan
    monkeypatch.setattr(plans, "_prerequisite_plan", packages)
    run = flow.run_child  # Original supervisor; update fixture intercepts only worker.run_child.
    def harmless_package(argv, **kw):
        assert argv[:3] == ["/usr/bin/pkexec", "/usr/bin/timeout", "--signal=TERM"]
        assert kw["privileged"] is True
        code = """
import pathlib,time,sys,os
root=pathlib.Path(sys.argv[1]); (root/"package").write_text(str(os.getpid()))
end=time.monotonic()+15
while not (root/"package-release").exists():
    if time.monotonic()>end: sys.exit(2)
    time.sleep(.02)
(root/"package-done").touch()
"""
        run([sys.executable, "-I", "-c", code, str(gate)], **kw)
    monkeypatch.setattr(worker, "run_child", harmless_package)
    review = flow.prepare_base_update(profile, "4.0.3", BINDING)
    job = flow.start_base_update(profile, "4.0.3", BINDING, review["consent"])
    try:
        wait_for((gate / "package").exists)
        assert flow.cancel_base_update(profile, job["id"])["state"] == "cancelling"
        with pytest.raises(ValueError, match="active"):
            load("cli").clean(manager)
        with pytest.raises(ValueError, match="active"):
            flow.start_base_update(profile, "4.0.3", BINDING, review["consent"])
        assert not (gate / "package-done").exists()
        assert not (gate / "installed").exists()
    finally:
        (gate / "package-release").touch()
        join_job(job)
    assert (gate / "package-done").exists()
    assert not Path(f"/proc/{int((gate / 'package').read_text())}").exists()
    assert flow.status_base_update(profile, job["id"])["state"] == "cancelled"
    load("cli").clean(manager)


def test_worker_rejects_invalid_exact_release_before_packages(profile, monkeypatch):
    worker = load("setup_worker")
    plan = load("setup_plan").build_base_update_plan(profile.home, "4.0.3", BINDING)
    plan.update(release="4.0.3\n", packages=["qemu-full"])
    monkeypatch.setattr(worker, "run_child", lambda *a, **kw: pytest.fail("Invalid release reached package effects"))
    monkeypatch.setattr(worker, "require_install_resources", lambda *a: None)
    with pytest.raises(ValueError, match="release"):
        worker.install_plan(plan, lambda *a: None)


def test_registered_profile_routes_use_real_dashboard_token_middleware(update, monkeypatch):
    from hermes_cli import web_server
    profile, _, old, _, gate = update
    token = "inert-fixture-token-not-a-credential"
    monkeypatch.setattr(web_server, "_SESSION_TOKEN", token)
    module = runpy.run_path(str(PLUGIN_ROOT / "dashboard/plugin_api.py"))
    app = FastAPI()
    app.middleware("http")(web_server.auth_middleware)
    prefix = "/api/plugins/hermes-realms"
    app.include_router(module["router"], prefix=prefix)
    payload = {"release": "4.0.3", "review_binding": BINDING}
    with TestClient(app) as client:
        prepare = prefix + "/realms/vm/update/prepare"
        assert client.post(prepare, json=payload).status_code == 401
        assert client.post(prepare, json=payload, headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.post(prepare + "?token=" + token, json=payload).status_code == 401
        headers = {"Authorization": "Bearer " + token}
        proposal = client.post(prepare, json=payload, headers=headers)
        assert proposal.status_code == 200, proposal.text
        start_url = prefix + "/realms/vm/update/start"
        assert client.post(start_url, json=payload).status_code == 401
        result = client.post(start_url, json=payload | {"consent": proposal.json()["consent"]}, headers=headers)
        assert result.status_code == 200, result.text
        job = result.json()
        url = prefix + f"/realms/vm/update/jobs/{job['id']}"
        try:
            wait_for((gate / "installed").exists)
            assert client.get(url).status_code == 401
            assert client.post(url + "/cancel", json={}).status_code == 401
            result = client.post(url + "/cancel", json={}, headers=headers)
            assert result.status_code == 200 and result.json()["state"] in {"cancelling", "cancelled"}
        finally:
            (gate / "install-release").touch()
            (gate / "retire-release").touch()
            join_job(job)
        assert client.get(url, headers=headers).json()["state"] == "cancelled"
    assert old.joinpath("disk.qcow2").exists()
    assert not (profile.home / "realms/sessions.sqlite3").exists()



def test_corrupt_selection_prepare_is_sanitized_and_read_only(api):
    client, _, (profile, manager, _, _, _) = api
    pointer = manager.data / "current-base.json"
    pointer.write_text(json.dumps({"version": 1, "home": "foreign", "uid": 0, "generation": "a" * 32}))
    before = {p: p.read_bytes() for p in profile.home.rglob("*") if p.is_file()}
    response = client.post("/realms/vm/update/prepare", json={"release": "4.0.3", "review_binding": BINDING})
    assert response.status_code == 409
    assert "foreign" not in response.text
    assert {p: p.read_bytes() for p in profile.home.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("holder", ["session", "profile"])
def test_session_and_profile_workers_exclude_all_three_entrypoints(update, monkeypatch, holder):
    import sys
    profile, manager, _, _, gate = update
    flow = load("setup_flow")
    service = load("integration").RealmIntegration(profile.home)
    service.bind(session_origin="fresh", session_id="owner", runtime_session_id="runtime")
    session_plan = load("setup_plan").build_plan(service, "owner", "realm")
    session_plan.update(blockers=[], packages=[])
    monkeypatch.setattr(flow, "build_plan", lambda *a: session_plan)
    def harmless(plan, phase, **kw):
        phase("install")
        code = "import pathlib,time; pathlib.Path(" + repr(str(gate / "holder")) + ").touch(); time.sleep(15)"
        flow.run_child([sys.executable, "-I", "-c", code], env={"PATH": "/usr/bin:/bin"}, timeout=20, **kw)
    monkeypatch.setattr(flow, "install_plan", harmless)
    review = flow.prepare_base_update(profile, "4.0.3", BINDING)
    def session_start():
        return flow.start(service, "owner", "realm", flow._consent(session_plan), {"runtime_session_id": "runtime"})
    def profile_start():
        return flow.start_base_update(profile, "4.0.3", BINDING, review["consent"])
    job = session_start() if holder == "session" else profile_start()
    try:
        wait_for((gate / "holder").exists)
        for contender in (session_start, profile_start, lambda: load("cli").clean(manager)):
            with pytest.raises(ValueError, match="active"):
                contender()
    finally:
        if holder == "session":
            flow.cancel(service, "owner", job["id"])
        else:
            flow.cancel_base_update(profile, job["id"])
        join_job(job)
    load("cli").clean(manager)


