"""Setup review distinguishes unknown download size from base reuse."""
import json
from pathlib import Path
import runpy
import socket
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from realms_test_paths import PLUGIN_ROOT

PLUGIN = PLUGIN_ROOT
load = runpy.run_path(str(PLUGIN / "realms/_binding.py"))["load_runtime"]


@pytest.mark.linux_only
@pytest.mark.parametrize("has_base", [False, True])
def test_vm_review_reports_download_uncertainty_or_reuse_without_io(tmp_path, monkeypatch, has_base):
    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    service = load("integration").RealmIntegration(home)
    service.bind(session_id="owner", runtime_session_id="runtime", session_origin="fresh")
    if has_base:
        base = load("config").vm_data_path(home) / "base"
        base.mkdir(parents=True)
        # Only a presence fixture: this is deliberately not a bootable/verified VM.
        (base / "disk.qcow2").write_bytes(b"inert base-presence fixture")
        (base / "base.json").write_text(json.dumps({"built_at": 1}))
    before = {p: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns)
              for p in home.rglob("*") if p.is_file()}
    api = runpy.run_path(str(PLUGIN / "dashboard/plugin_api.py"))
    app = FastAPI()
    app.include_router(api["router"])

    def unexpected_io(*args, **kwargs):
        pytest.fail("a setup preview attempted network or subprocess I/O")

    with TestClient(app) as client, monkeypatch.context() as guard:
        guard.setattr(subprocess, "Popen", unexpected_io)
        guard.setattr(socket.socket, "connect", unexpected_io)
        response = client.post("/realms/setup/prepare", json={
            "runtime_session_id": "runtime", "kind": "omarchy-vm",
        })
        assert response.status_code == 200, response.text
        proposal = response.json()
        assert proposal["action"] == ("start" if has_base else "install")
        details = " ".join(proposal["details"]).lower()
        if has_base:
            assert "no iso download" in details and "reus" in details, details
            assert "checked" in details, "presence must not be labelled verified readiness"
        else:
            assert "download size is unknown" in details, details
        assert "physical storage" in details and "unknown" in details, details
        assert client.post("/realms/setup/prepare", json={
            "runtime_session_id": "runtime", "kind": "omarchy-vm",
        }).json()["consent"] == proposal["consent"]
    assert before == {p: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns)
                      for p in home.rglob("*") if p.is_file()}
