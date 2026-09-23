"""Live actions must not target retained or unselected desktops."""
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest
from realms_test_paths import HERMES_ROOT, PLUGIN_ROOT

ROOT = HERMES_ROOT


@pytest.fixture(params=["realm", "omarchy-vm"])
def targets(tmp_path, monkeypatch, request):
    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    load = runpy.run_path(str(PLUGIN_ROOT / "realms/_binding.py"))["load_runtime"]
    service = load("integration").RealmIntegration(home)
    owner = service.bind(session_origin="fresh", session_id="action-owner")
    service.owners.set_kind(owner, request.param)
    rows = {kind: {"id": prefix + "a" * 24, "generation": "b" * 32,
                   "session_id": owner, "status": "running", "size": "800x600"}
            for kind, prefix in [("realm", "r-"), ("omarchy-vm", "vm-")]}
    monkeypatch.setattr(service, "records", lambda _: [rows["realm"]])
    monkeypatch.setattr(service, "vm_records", lambda _: [rows["omarchy-vm"]])
    def forbidden(*args, **kwargs):
        pytest.fail("dispatched a live operation to a stopped or unselected target")
    for method in ("env", "shot", "resize"):
        monkeypatch.setattr(service.manager, method, forbidden)
    service._vm = SimpleNamespace(config=service.manager.config, validate=forbidden,
                                  shot=forbidden, push=forbidden, pull=forbidden)
    monkeypatch.setattr(service, "watch", forbidden)
    return service, owner, request.param, rows


@pytest.mark.linux_only
@pytest.mark.parametrize("command", ["watch", "shot", "repair", "size 800x600", "push source", "pull source destination"])
def test_live_action_refuses_stopped_selected_target_without_using_other_kind(targets, command):
    service, owner, kind, rows = targets
    rows[kind]["status"] = "stopped"
    with pytest.raises(ValueError):
        service.command(command, session_id=owner)
    assert rows[kind]["status"] == "stopped"


@pytest.mark.linux_only
def test_watch_selects_current_live_kind_not_retained_other_kind(targets, monkeypatch):
    service, owner, kind, rows = targets
    other = "omarchy-vm" if kind == "realm" else "realm"
    rows[other]["status"] = "stopped"
    monkeypatch.setattr(service, "watch", lambda who, realm_id: {"owner": who, "realm_id": realm_id})
    result = service.command("watch", session_id=owner)
    assert result == {"owner": owner, "realm_id": rows[kind]["id"]}
