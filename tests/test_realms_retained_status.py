"""Retained work remains visible without probing nonexistent compute."""
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest
from realms_test_paths import HERMES_ROOT, PLUGIN_ROOT

ROOT = HERMES_ROOT


@pytest.mark.linux_only
@pytest.mark.parametrize("kind,state,note_key", [
    ("realm", "stopped", "cleanup_note"),
    ("omarchy-vm", "recovery-required", "recovery_reason"),
])
def test_retained_status_preserves_recovery_note_without_live_probes(tmp_path, monkeypatch, kind, state, note_key):
    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    load = runpy.run_path(str(PLUGIN_ROOT / "realms/_binding.py"))["load_runtime"]
    integration = load("integration")
    service = integration.RealmIntegration(home)
    owner = service.bind(session_origin="fresh", session_id="retained-parent")
    service.owners.set_kind(owner, kind)
    note = "Retained work requires explicit recovery"
    record = {"id": "r-" + "a" * 24, "generation": "b" * 32,
              "session_id": owner, "status": state, "size": "800x600", note_key: note}
    def no_live_probe(*args, **kwargs):
        pytest.fail("status probed stopped compute")
    monkeypatch.setattr(service.manager, "list", lambda: [record] if kind == "realm" else [])
    monkeypatch.setattr(service._window_counter, "count", no_live_probe)
    service._vm = SimpleNamespace(config=service.manager.config,
                                  list=lambda: [record] if kind == "omarchy-vm" else [],
                                  stats=no_live_probe)
    monkeypatch.setattr(integration, "setup_status", lambda **kwargs: {"ready": True})
    monkeypatch.setattr(integration, "vm_setup_status", lambda *args: {"ready": True})
    status = service.status(owner)
    assert len(status["realms"]) == 1
    row = status["realms"][0]
    assert row["state"] == state
    assert row["error"] == note
    assert row["window_count"] is None
    assert row.get("stats") is None


@pytest.mark.linux_only
@pytest.mark.parametrize("state", ["stopped", "running"])
def test_regular_restart_rechecks_setup_but_live_reuse_does_not(tmp_path, monkeypatch, state):
    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    load = runpy.run_path(str(PLUGIN_ROOT / "realms/_binding.py"))["load_runtime"]
    integration = load("integration")
    service = integration.RealmIntegration(home)
    owner = service.bind(session_origin="fresh", session_id="restart-parent")
    record = {"id": "r-" + "a" * 24, "generation": "b" * 32,
              "session_id": owner, "status": state}
    monkeypatch.setattr(service.manager, "list", lambda: [record])
    starts, checks = [], []
    def start(who):
        starts.append(who)
        return record
    def missing_setup(**kwargs):
        checks.append(True)
        return {"ready": False, "message": "fixture setup unavailable"}
    monkeypatch.setattr(service.manager, "start", start)
    monkeypatch.setattr(integration, "setup_status", missing_setup)
    if state == "stopped":
        with pytest.raises(integration.SetupError, match="fixture setup unavailable"):
            service.ready(owner)
        assert checks and not starts
    else:
        assert service.ready(owner) is record
        assert starts == [owner] and not checks
