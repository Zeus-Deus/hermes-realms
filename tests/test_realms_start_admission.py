"""Real ready orchestration; only allocation and target transport are inert."""
import json
import runpy
from pathlib import Path

import pytest

from test_realms_vm_retention import owned
from realms_test_paths import HERMES_ROOT, PLUGIN_ROOT

ROOT = HERMES_ROOT
pytestmark = pytest.mark.linux_only


@pytest.mark.parametrize("kind", ["realm", "omarchy-vm"])
@pytest.mark.parametrize("state", ["cold", "stopped"])
@pytest.mark.parametrize("transition", ["none", "config", "handback", "provider", "manager-config"])
def test_prerequisites_cannot_authorize_stale_start(tmp_path, monkeypatch, kind, state, transition):
    from hermes_cli.session_execution import SessionExecutionContext, SessionExecutionError
    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    load = runpy.run_path(str(PLUGIN_ROOT / "realms/_binding.py"))["load_runtime"]
    service = load("integration").get_integration(home)
    owner = service.bind(session_origin="fresh", session_id="parent", task_id="task")
    service.owners.set_kind(owner, kind)
    record = {"id": ("v-" if kind == "omarchy-vm" else "r-") + "a" * 24,
              "generation": "b" * 32, "status": "stopped", "home": str(home),
              "session_id": owner, "workspace_dir": str(tmp_path / "work")}
    path = home / "realms" / (record["id"] + ".json")
    if state == "stopped":
        path.write_text(json.dumps(record))
    authority = load("bridge").get_profile_viewer(home).authority
    starts, prepared = [], []
    def prerequisites(*a, **kw):
        prepared.append(True)
        if transition == "config":
            (home / "config.yaml").write_text("plugins:\n  realms:\n    size: 1280x720\n")
        elif transition == "handback":
            # A new intervening resource invalidates a cold selection too.
            if state == "cold":
                path.write_text(json.dumps(record))
            assert authority.acquire(record["id"], record["generation"], "human")
            authority.release(record["id"], "human")
        elif transition == "manager-config":
            from dataclasses import replace
            manager = service.vm if kind == "omarchy-vm" else service.manager
            manager.config = replace(manager.config, size="1280x720")
        elif transition == "provider":
            disposers.append(register_terminal_target_resolver("admission", service.terminal_context,
                selector=service.select_terminal_target))
        return {"ready": True, "message": "inert prerequisites"}
    def start(o, *, before_allocate=None, before_start=None):
        if before_allocate:
            before_allocate()
        if before_start:
            before_start()
        starts.append(o)
        record["status"] = "running"
        path.write_text(json.dumps(record))
        return dict(record)
    monkeypatch.setattr(load("integration"), "setup_status", prerequisites)
    monkeypatch.setattr(load("integration"), "vm_setup_status", prerequisites)
    monkeypatch.setattr(service.manager, "list", lambda: [])
    monkeypatch.setattr(service.manager if kind == "realm" else service.vm, "start", start)
    monkeypatch.setitem(load("target_contexts")._BUILDERS, kind,
                        lambda *a: SessionExecutionContext(command_prefix=("/usr/bin/env",)))
    from tools.terminal_targets import register_terminal_target_resolver, select_terminal_target
    disposers = [register_terminal_target_resolver("admission", service.terminal_context,
        selector=service.select_terminal_target)]
    selection = select_terminal_target("admission", command="true", session_id="parent", task_id="task")
    try:
        if transition == "none":
            selection.realize().check()
            assert starts == [owner]
        else:
            with pytest.raises(SessionExecutionError):
                selection.realize()
            assert starts == []
        assert prepared == [True]
    finally:
        for dispose in disposers:
            dispose()
        load("target_contexts").release_targets(service, owner)
        load("bridge").close_profile_viewer(home)


@pytest.mark.parametrize("transition", ["none", "config", "handback"])
def test_real_vm_restart_admission_after_resource_preparation(owned, monkeypatch, transition):
    from test_realms_vm_retention import fresh, load
    from hermes_cli.session_execution import SessionExecutionContext, SessionExecutionError
    manager, _, compute = owned
    fresh(owned, monkeypatch, start=False)
    monkeypatch.setenv("HERMES_HOME", str(manager.home))
    service = load("integration").get_integration(manager.home)
    service._vm = manager
    owner = service.bind(session_origin="fresh", session_id="retention-owner")
    service.owners.set_kind(owner, "omarchy-vm")
    record = manager.start(owner)
    manager.stop(record["id"])
    authority = load("bridge").get_profile_viewer(manager.home).authority
    disk = Path(record["session_dir"]) / "disk.qcow2"
    before = disk.read_bytes(), disk.stat().st_ino
    launches = []
    launch = manager._launch
    def observed(r, **kwargs):
        launches.append(r["id"])
        launch(r, **kwargs)
    monkeypatch.setattr(manager, "_launch", observed)
    def resources(*a):
        if transition == "config":
            (manager.home / "config.yaml").write_text("plugins:\n  realms:\n    size: 1280x720\n")
        elif transition == "handback":
            assert authority.acquire(record["id"], record["generation"], "human")
            authority.release(record["id"], "human")
    monkeypatch.setattr(load("vm_manager"), "require_resources", resources)
    monkeypatch.setitem(load("target_contexts")._BUILDERS, "omarchy-vm", lambda *a: SessionExecutionContext())
    selection = service.select_terminal_target(command="true", session_id=owner)
    try:
        if transition == "none":
            selection.realize().check()
            assert launches == [record["id"]]
        else:
            with pytest.raises(SessionExecutionError):
                selection.realize()
            assert launches == [] and not compute["active"]
            assert manager.registry.get(record["id"])["status"] == "stopped"
        assert (disk.read_bytes(), disk.stat().st_ino) == before
    finally:
        load("target_contexts").release_targets(service, owner)
        load("bridge").close_profile_viewer(manager.home)
