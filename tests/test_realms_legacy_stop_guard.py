"""Legacy Stop is a recovery refusal, never a destructive success."""
import json
import os
from pathlib import Path
from runpy import run_path
import uuid

import pytest
from realms_test_paths import PLUGIN_ROOT

ROOT = PLUGIN_ROOT
load = run_path(str(ROOT / "realms/_binding.py"))["load_runtime"]
pytestmark = pytest.mark.linux_only


def record_for(home, owner="legacy-owner", status="running"):
    generation = uuid.uuid4().hex
    runtime = f"/run/user/{os.getuid()}/hr-{generation[:16]}"
    return {
        "id": "r-" + generation[:24], "generation": generation,
        "uid": os.getuid(), "home": str(home), "session_id": owner,
        "runtime_dir": runtime, "size": "800x600",
        "scope": f"hermes-realm-{generation}.scope",
        "guardian_unit": f"hermes-realm-{generation}-guard.service",
        "status": status, "created_at": 0, "last_activity": 0,
        "vnc_socket": runtime + "/wayvnc.sock", "vnc_port": None,
    }


@pytest.mark.parametrize("state", ["running", "stopped", "cleanup_failed"])
def test_manager_and_cli_refuse_legacy_stop_without_effects(tmp_path, monkeypatch, capsys, state):
    managers, life = load("manager"), load("lifecycle")
    manager = managers.Manager(tmp_path)
    record = record_for(tmp_path, status=state)
    manager.registry.put(record)
    path = manager.registry.path(record["id"])
    before = path.read_bytes(), path.stat().st_ino
    effects = []
    monkeypatch.setattr(managers, "stop_scope", lambda r: effects.append(r["id"]))
    with pytest.raises(life.RealmError, match="legacy.*export required") as error:
        manager.stop(record["id"])
    assert type(error.value).__name__ == "LegacyExportRequired"
    assert record["id"] in str(error.value)
    assert str(Path(record["runtime_dir"]) / "home") in str(error.value)
    assert "Stop was not performed" in str(error.value)
    assert "reboot" in str(error.value)
    assert effects == []
    assert (path.read_bytes(), path.stat().st_ino) == before
    assert load("cli").main(["--home", str(tmp_path), "stop", record["id"]]) == 1
    output = capsys.readouterr()
    assert not output.out
    assert "export required" in json.loads(output.err)["error"]
    assert effects == []
    assert (path.read_bytes(), path.stat().st_ino) == before


@pytest.mark.parametrize("state", ["starting", "running", "stopping", "stopped", "cleanup_failed"])
def test_reconciliation_preserves_legacy_recovery_without_replacement(tmp_path, monkeypatch, state):
    managers, life = load("manager"), load("lifecycle")
    manager = managers.Manager(tmp_path)
    record = record_for(tmp_path, status=state)
    manager.registry.put(record)
    path = manager.registry.path(record["id"])
    before = path.read_bytes(), path.stat().st_ino
    effects = []
    monkeypatch.setattr(managers, "stop_scope", lambda r: effects.append(r["id"]))
    monkeypatch.setattr(managers, "scope_info", lambda unit: {"ActiveState": "inactive"})
    rows = manager.list()
    assert [row["id"] for row in rows] == [record["id"]], "recovery inventory was erased"
    assert "export required" in rows[0]["cleanup_note"]
    assert rows[0]["status"] == state
    with pytest.raises(life.RealmError, match="legacy.*export required"):
        managers.Manager(tmp_path).start(record["session_id"])
    assert effects == []
    assert (path.read_bytes(), path.stat().st_ino) == before
    assert not (manager.registry.root / "workspaces").exists()


@pytest.mark.parametrize("action", ["stop", "internal-stop", "finalize", "unload"])
def test_service_preflights_every_affected_record_before_authority_changes(tmp_path, monkeypatch, action):
    from hermes_cli.session_execution import (
        SessionExecutionContext, register_session_execution_context,
        resolve_session_execution_context, remove_session_execution_context,
    )
    integration, life = load("integration"), load("lifecycle")
    service = integration.RealmIntegration(tmp_path)
    owner = service.bind(session_id="legacy-owner")
    service.owners.set_mode(owner, "realm")
    modern, legacy = sorted([record_for(tmp_path, owner, "stopped") for _ in range(2)], key=lambda r: r["id"])
    load("workspace").create(modern)
    for record in (modern, legacy):
        service.manager.registry.put(record)
    service._attachments[owner] = (legacy["generation"], (), legacy["id"])
    service._setup_owners.add(owner)
    target = "legacy-guard-target-" + legacy["generation"]
    register_session_execution_context(target, SessionExecutionContext())
    lease = resolve_session_execution_context(session_id=target)
    assert lease is not None
    service._target_contexts[owner, "realm", "cua"] = (legacy["id"], legacy["generation"], lease)
    viewer = load("bridge").get_profile_viewer(tmp_path)
    assert viewer.authority.acquire(legacy["id"], legacy["generation"], "human")
    generation = service.owners.setup_generation(owner)
    epoch = viewer.authority.epoch(legacy["id"])
    registry = {r["id"]: service.manager.registry.path(r["id"]).read_bytes() for r in (modern, legacy)}
    attachment = dict(service._attachments)
    effects = []
    monkeypatch.setattr(load("manager"), "stop_scope", lambda r: effects.append(r["id"]))
    try:
        with pytest.raises(life.LegacyExportRequired):
            if action == "internal-stop":
                with service._lock, service.owners.activation_guard(owner):
                    service._stop(owner)
            elif action == "finalize":
                service.finalize(session_id=owner)
            elif action == "unload":
                service.unload()
            else:
                service.stop(owner)
        assert service.owners.setup_generation(owner) == generation
        assert service.owners.mode(owner, "host") == "realm"
        assert service._attachments == attachment
        lease.check()
        assert viewer.authority.epoch(legacy["id"]) == epoch
        assert viewer.is_controlled(legacy["id"])
        assert not service._unloaded
        assert effects == []
        assert {rid: service.manager.registry.path(rid).read_bytes() for rid in registry} == registry
        monkeypatch.setattr(integration, "setup_status", lambda **kwargs: {"ready": True})
        status = service.status(owner)
        row = next(row for row in status["realms"] if row["id"] == legacy["id"])
        assert "export required" in row["error"]
    finally:
        remove_session_execution_context(target)
        load("bridge").close_profile_viewer(tmp_path)


@pytest.mark.parametrize("damage", ["missing", "invalid", "downgraded"])
def test_invalid_modern_receipt_is_ownership_failure_before_service_effects(tmp_path, damage):
    service = load("integration").RealmIntegration(tmp_path)
    life = load("lifecycle")
    owner = service.bind(session_id="modern-owner")
    record = record_for(tmp_path, owner, "stopped")
    root = load("workspace").create(record)
    if damage == "missing":
        (root / "owner.json").unlink()
    elif damage == "invalid":
        (root / "owner.json").write_text("{}")
    else:
        record.pop("workspace_dir")
    service.manager.registry.put(record)
    generation = service.owners.setup_generation(owner)
    before = service.manager.registry.path(record["id"]).read_bytes()
    for action in (lambda: service.manager.stop(record["id"]), service.manager.list,
                   lambda: service.stop(owner)):
        with pytest.raises(life.OwnershipError) as error:
            action()
        assert not isinstance(error.value, life.LegacyExportRequired)
    assert service.owners.setup_generation(owner) == generation
    assert service.manager.registry.path(record["id"]).read_bytes() == before


def test_modern_stop_is_owner_scoped_and_keeps_legacy_peer_unchanged(tmp_path, monkeypatch):
    service = load("integration").RealmIntegration(tmp_path)
    owner = service.bind(session_id="modern-owner")
    legacy_owner = service.bind(session_id="legacy-owner")
    record = record_for(tmp_path, owner)
    root = load("workspace").create(record)
    sentinel = root / "home/sentinel"
    sentinel.write_bytes(b"modern-private-work")
    legacy = record_for(tmp_path, legacy_owner, "stopped")
    for item in (record, legacy):
        service.manager.registry.put(item)
    before = service.manager.registry.path(legacy["id"]).read_bytes()
    legacy_generation = service.owners.setup_generation(legacy_owner)
    effects = []
    monkeypatch.setattr(load("manager"), "stop_scope", lambda r: effects.append(r["id"]))
    service.stop(owner)
    assert effects == [record["id"]]
    assert service.manager.registry.get(record["id"])["status"] == "stopped"
    assert sentinel.read_bytes() == b"modern-private-work"
    assert service.manager.registry.path(legacy["id"]).read_bytes() == before
    assert service.owners.setup_generation(legacy_owner) == legacy_generation
    # A previously successful preflight is never sufficient to skip the current
    # under-lock record validation (e.g. another writer damaged the receipt).
    service.manager.preflight_stop(owner)
    (root / "owner.json").unlink()
    with pytest.raises(load("lifecycle").OwnershipError):
        service.manager.stop(record["id"])


@pytest.mark.integration
def test_stopped_legacy_with_only_volatile_home_is_not_successful_stop(tmp_path, capsys):
    import shutil

    service = load("integration").RealmIntegration(tmp_path)
    owner = service.bind(session_id="volatile-owner")
    record = record_for(tmp_path, owner, "stopped")
    runtime = Path(record["runtime_dir"])
    runtime.mkdir(mode=0o700)
    try:
        home = runtime / "home"
        home.mkdir(mode=0o700)
        sentinel = home / "sentinel"
        sentinel.write_bytes(b"volatile-only\x00work\xff")
        record["legacy_workspace_dir"] = str(home)
        service.manager.registry.put(record)
        path = service.manager.registry.path(record["id"])
        before = path.read_bytes(), path.stat().st_ino, sentinel.read_bytes(), sentinel.stat().st_ino
        for action in (lambda: service.manager.stop(record["id"]), lambda: service.stop(owner)):
            with pytest.raises(load("lifecycle").LegacyExportRequired):
                action()
        assert load("cli").main(["--home", str(tmp_path), "stop", record["id"]]) == 1
        assert "export required" in capsys.readouterr().err
        assert service.manager.list()[0]["id"] == record["id"]
        assert (path.read_bytes(), path.stat().st_ino, sentinel.read_bytes(), sentinel.stat().st_ino) == before
    finally:
        # No process or unit exists in this synthetic, uniquely owned runtime.
        shutil.rmtree(runtime)


def test_refusal_does_not_create_new_activation_state(tmp_path):
    import hashlib

    service = load("integration").RealmIntegration(tmp_path)
    owner = service.bind(session_id="legacy-without-activation-lock")
    record = record_for(tmp_path, owner, "stopped")
    with service.manager.registry.lock():
        service.manager.registry.put(record)
    lock = service.owners.root / ("activation-" + hashlib.sha256(owner.encode()).hexdigest() + ".lock")
    assert not lock.exists()
    with pytest.raises(load("lifecycle").LegacyExportRequired):
        service.stop(owner)
    assert not lock.exists(), "Stop refusal allocated activation state"
