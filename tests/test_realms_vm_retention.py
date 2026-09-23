"""Durable workspace contract; native compute is qualified separately."""
from dataclasses import asdict
import os
from pathlib import Path
import runpy
import uuid

import pytest
from realms_test_paths import HERMES_ROOT, PLUGIN_ROOT

ROOT = HERMES_ROOT


def load(name):
    return runpy.run_path(str(PLUGIN_ROOT / "realms/_binding.py"))["load_runtime"](name)


@pytest.fixture
def owned(tmp_path, monkeypatch):
    vm = load("vm_manager")
    manager = vm.VmManager(tmp_path / "profile")
    generation = uuid.uuid4().hex
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(vm, "_generation_paths", lambda uid, gen: runtime)
    runtime.mkdir(mode=0o700)
    session = manager.registry.root / "vm" / generation
    session.mkdir(mode=0o700, parents=True)
    record = dict(id="v-" + generation[:24], generation=generation, uid=os.getuid(),
                  home=str(manager.home), session_id="retention-owner", kind=manager.KIND,
                  runtime_dir=str(runtime), session_dir=str(session),
                  unit=f"hermes-vm-{generation}.service",
                  guardian_unit=f"hermes-vm-{generation}-guard.service",
                  owner_protocol="pidfd-v2", status="running", ssh_port=2300,
                  ssh_host_key_pinned=True, vnc_socket=str(runtime / "vnc.sock"),
                  base_disk=str(manager.base_home() / "disk.qcow2"),
                  invocation_id="fixture-invocation", memory=manager.config.vm.memory,
                  network=manager.config.vm.network)
    manager.base_home().mkdir(parents=True)
    for name in ("disk.qcow2", "OVMF_VARS.4m.fd", "base.json"):
        (manager.base_home() / name).write_bytes(b"fixture-base")
    for name in ("disk.qcow2", "OVMF_VARS.4m.fd"):
        (session / name).write_bytes(b"guest-only unfinished work\x00\xff")
    load("lifecycle").atomic_json(session / "spec.json", asdict(manager.config))
    (runtime / "ssh_known_hosts").write_bytes(b"fixture-enrolled-key\n")
    (runtime / "ssh_known_hosts").chmod(0o600)
    manager.registry.put(record)
    compute = {"active": True, "retired": 0}
    def retire(r):
        compute.update(active=False, retired=compute["retired"] + 1)
    monkeypatch.setattr(load("vm_owner_lifetime"), "retire", retire)
    monkeypatch.setattr(load("vm_owner_lifetime"), "remove_dropin", lambda r: None)
    monkeypatch.setattr(vm, "shutdown_guest", lambda *a: None)
    monkeypatch.setattr(vm, "scope_info", lambda u: {"ActiveState": "active" if compute["active"] else "inactive"})
    monkeypatch.setattr(vm, "unit_active", lambda u: compute["active"])
    return manager, record, compute


def fresh(owned, monkeypatch, *, start=True):
    manager, old, compute = owned
    manager.registry.remove(old["id"])
    import shutil
    shutil.rmtree(old["runtime_dir"])
    shutil.rmtree(old["session_dir"])
    vm = load("vm_manager")
    monkeypatch.setattr(manager, "base_status", lambda: {"present": True})
    monkeypatch.setattr(vm, "require_resources", lambda *a, **kw: None)
    def clone(session, base):
        for name in ("disk.qcow2", "OVMF_VARS.4m.fd"):
            (session / name).write_bytes(b"initial-guest")
        record = manager.registry.get("v-" + session.name[:24])
        load("vm_workspace").create(record)
        return record["workspace"]
    monkeypatch.setattr(manager, "_clone_base", clone)
    def launch(record, **kwargs):
        compute["active"] = True
        record["invocation_id"] = uuid.uuid4().hex
        record["owner_invocation_id"] = uuid.uuid4().hex
        compute["invocation"] = record["invocation_id"]
        compute["memory"] = kwargs.get("config", manager.config).vm.memory
    monkeypatch.setattr(manager, "_launch", launch)
    monkeypatch.setattr(vm, "scope_info", lambda u: {
        "ActiveState": "active" if compute["active"] else "inactive",
        "InvocationID": compute.get("invocation")})
    def enroll(r):
        path = Path(r["runtime_dir"]) / "ssh_known_hosts"
        if not path.exists():
            path.write_bytes(b"fixture-enrolled-key\n")
            path.chmod(0o600)
    monkeypatch.setattr(vm, "enroll_host_key", enroll)
    return manager.start("retention-owner") if start else None


def test_restart_after_pin_before_publication_regenerates_endpoints(owned, monkeypatch):
    manager, _, compute = owned
    fresh(owned, monkeypatch, start=False)
    workspace = load("vm_workspace")
    retain_pin = workspace.retain_pin
    def interrupted(record):
        retain_pin(record)
        raise OSError("interrupted after durable pin")
    monkeypatch.setattr(workspace, "retain_pin", interrupted)
    with pytest.raises(OSError, match="after durable pin"):
        manager.start("retention-owner")
    stopped = manager.list()[0]
    assert stopped["status"] == "stopped" and not compute["active"]
    assert "vnc_socket" not in stopped
    monkeypatch.setattr(workspace, "retain_pin", retain_pin)
    monkeypatch.setattr(load("vm_owner_lifetime"), "validate_owner", lambda r: None)
    resumed = manager.start("retention-owner")
    readback = manager.registry.get(resumed["id"])
    assert readback["vnc_socket"] == str(Path(resumed["runtime_dir"]) / "vnc.sock")
    assert readback["qmp_socket"] == str(Path(resumed["runtime_dir"]) / "qmp.sock")
    assert manager.validate(resumed["id"])["id"] == stopped["id"]
    assert manager.stop(resumed["id"])
    assert manager.list()[0]["status"] == "stopped"


@pytest.mark.parametrize("restart", [False, True])
def test_stopped_ports_are_reallocated_without_replacing_work(owned, monkeypatch, tmp_path, restart):
    manager, _, _ = owned
    vm = load("vm_manager")
    monkeypatch.setattr(vm, "_generation_paths", lambda uid, gen: tmp_path / ("runtime-" + gen))
    record = fresh(owned, monkeypatch)
    manager.stop(record["id"])
    disk = Path(record["session_dir"]) / "disk.qcow2"
    before = disk.read_bytes(), disk.stat().st_ino, record["workspace"]
    # Deterministic socket availability: old endpoint is occupied on restart;
    # for fresh allocation the entire (one-port) pool was used by stopped work.
    ports = [record["ssh_port"] + 1] if restart else [record["ssh_port"]]
    monkeypatch.setattr(vm, "SSH_PORT_RANGE", ports)
    class Probe:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def bind(self, endpoint): assert endpoint[1] in ports
    monkeypatch.setattr(vm.socket, "socket", Probe)
    resumed = manager.start(record["session_id"] if restart else "new-owner")
    assert resumed["ssh_port"] in ports
    assert (disk.read_bytes(), disk.stat().st_ino, manager.registry.get(record["id"])["workspace"]) == before
    if restart:
        assert resumed["id"] == record["id"]
        assert resumed["compute_generation"] != record["compute_generation"]


@pytest.mark.parametrize("reservation", ["running", "starting", "recovery-required", "exhausted"])
def test_failed_port_admission_leaves_no_unregistered_directories(owned, monkeypatch, tmp_path, reservation):
    manager, _, _ = owned
    vm = load("vm_manager")
    monkeypatch.setattr(vm, "_generation_paths", lambda uid, gen: tmp_path / ("runtime-" + gen))
    record = fresh(owned, monkeypatch)
    record.update(status=reservation if reservation != "exhausted" else "running",
                  cleanup_required=reservation == "recovery-required")
    manager.registry.put(record)
    monkeypatch.setattr(manager, "_reconcile_locked", lambda: None)
    monkeypatch.setattr(vm, "SSH_PORT_RANGE", [] if reservation == "exhausted" else [record["ssh_port"]])
    before = set(tmp_path.glob("runtime-*")), set((manager.registry.root / "vm").iterdir())
    with pytest.raises(vm.VmError, match="no free loopback SSH port"):
        manager.start("new-owner")
    assert (set(tmp_path.glob("runtime-*")), set((manager.registry.root / "vm").iterdir())) == before
    assert manager.registry.records() == [record]


def test_missing_registry_blocks_replacement_and_preserves_orphan(owned, monkeypatch, tmp_path):
    manager, _, compute = owned
    vm = load("vm_manager")
    monkeypatch.setattr(vm, "_generation_paths", lambda uid, gen: tmp_path / ("runtime-" + gen))
    record = fresh(owned, monkeypatch)
    manager.stop(record["id"])
    manager.registry.remove(record["id"])
    session = Path(record["session_dir"])
    before = {p.name: p.read_bytes() for p in session.iterdir()}
    with pytest.raises(vm.VmError, match="[Uu]nregistered.*recovery"):
        manager.start(record["session_id"])
    assert not manager.registry.records() and not compute["active"]
    assert list((manager.registry.root / "vm").iterdir()) == [session]
    assert {p.name: p.read_bytes() for p in session.iterdir()} == before


@pytest.mark.parametrize("interrupt", ["pin", "session-tree", "registry"])
def test_interrupted_delete_is_retryable_without_poisoning_inventory(owned, monkeypatch, tmp_path, interrupt):
    manager, _, _ = owned
    vm = load("vm_manager")
    monkeypatch.setattr(vm, "_generation_paths", lambda uid, gen: tmp_path / ("runtime-" + gen))
    record = fresh(owned, monkeypatch)
    manager.stop(record["id"])
    peer = manager.start("peer")
    manager.stop(peer["id"])
    session = Path(record["session_dir"])
    rmtree, remove = vm.shutil.rmtree, manager.registry.remove
    def interrupted_tree(path):
        if Path(path) == session:
            if interrupt == "pin":
                (session / "ssh_known_hosts").unlink()
            else:
                rmtree(path)
            raise OSError("interrupted explicit deletion")
        rmtree(path)
    def interrupted_registry(vm_id):
        raise OSError("interrupted explicit deletion")
    if interrupt == "registry":
        monkeypatch.setattr(manager.registry, "remove", interrupted_registry)
    else:
        monkeypatch.setattr(vm.shutil, "rmtree", interrupted_tree)
    with pytest.raises(OSError, match="interrupted explicit deletion"):
        manager.delete(record["id"])
    rows = {r["id"]: r for r in manager.list()}
    assert rows[record["id"]]["status"] == "deleting"
    assert rows[peer["id"]]["status"] == "stopped"
    with pytest.raises(vm.VmError):
        manager.start(record["session_id"])
    with pytest.raises(vm.VmError, match="delet"):
        manager.stop(record["id"])
    monkeypatch.setattr(vm.shutil, "rmtree", rmtree)
    monkeypatch.setattr(manager.registry, "remove", remove)
    # Retry through a fresh manager, not process-local deletion authority.
    reopened = vm.VmManager(manager.home)
    assert reopened.delete(record["id"])
    assert not session.exists() and not manager.registry.path(record["id"]).exists()
    assert reopened.list() == [rows[peer["id"]]]


@pytest.mark.parametrize("change", ["session_dir", "runtime_dir", "disk.qcow2", "spec.json",
                                    "ssh_known_hosts", "binding", "receipt", "base", "live-unit", "unknown-unit"])
def test_delete_retry_revalidates_owned_receipts_and_units(owned, monkeypatch, change):
    manager, _, compute = owned
    record = fresh(owned, monkeypatch)
    manager.stop(record["id"])
    vm = load("vm_manager")
    rmtree = vm.shutil.rmtree
    def interrupted(path):
        raise OSError("deletion interruption")
    monkeypatch.setattr(vm.shutil, "rmtree", interrupted)
    with pytest.raises(OSError, match="deletion interruption"):
        manager.delete(record["id"])
    monkeypatch.setattr(vm.shutil, "rmtree", rmtree)
    kept = manager.registry.get(record["id"])
    session = Path(record["session_dir"])
    if change in {"session_dir", "runtime_dir"}:
        path = Path(record[change])
        path.rename(path.with_name(path.name + "-original"))
        path.mkdir(mode=0o700)
        (path / "unrelated").write_bytes(b"preserve replacement")
    elif change in {"disk.qcow2", "spec.json", "ssh_known_hosts"}:
        path = session / change
        path.rename(path.with_name(path.name + "-original"))
        path.write_bytes(b"preserve replacement")
        path.chmod(0o600)
    elif change == "base":
        Path(record["base_disk"]).write_bytes(b"changed base")
    elif change == "binding":
        kept["session_id"] = "another-conversation"
        manager.registry.put(kept)
    elif change == "receipt":
        kept.pop("deletion")
        manager.registry.put(kept)
    elif change == "live-unit":
        compute["active"] = True
    else:
        def unavailable(unit):
            raise vm.OwnershipError("unit observation unavailable")
        monkeypatch.setattr(vm, "scope_info", unavailable)
    before = {p: p.read_bytes() for p in session.iterdir() if p.is_file()}
    with pytest.raises(vm.OwnershipError):
        manager.delete(record["id"])
    assert {p: p.read_bytes() for p in session.iterdir() if p.is_file()} == before
    assert manager.registry.get(record["id"])["status"] == "deleting"


def test_restart_reuses_disk_spec_pin_and_base_receipts(owned, monkeypatch):
    manager, _, compute = owned
    record = fresh(owned, monkeypatch)
    disk = Path(record["session_dir"]) / "disk.qcow2"
    disk.write_bytes(b"unfinished guest source\x00\xff")
    inode = disk.stat().st_ino
    spec = (disk.parent / "spec.json").read_bytes()
    pin = (Path(record["runtime_dir"]) / "ssh_known_hosts").read_bytes()
    assert manager.stop(record["id"])
    assert not compute["active"]
    stopped = manager.list()[0]
    assert stopped["status"] == "stopped"
    assert (disk.parent / "ssh_known_hosts").read_bytes() == pin
    assert stopped["workspace"]["base"]
    # Host reboot loses /run; current host config must not replace frozen spec.
    import shutil
    shutil.rmtree(record["runtime_dir"], ignore_errors=True)
    manager.config = load("config").Config(vm={"memory": 4096})
    monkeypatch.setattr(manager, "_clone_base", lambda *a: pytest.fail("cloned replacement guest"))
    resumed = manager.start("retention-owner")
    assert resumed["id"] == record["id"]
    assert resumed["invocation_id"] != record["invocation_id"]
    assert compute["memory"] == record["memory"]
    assert disk.stat().st_ino == inode
    assert disk.read_bytes() == b"unfinished guest source\x00\xff"
    assert (disk.parent / "spec.json").read_bytes() == spec
    assert (Path(record["runtime_dir"]) / "ssh_known_hosts").read_bytes() == pin


@pytest.mark.parametrize("change", ["disk.qcow2", "spec.json", "ssh_known_hosts", "base", "binding", "missing-receipt"])
def test_damaged_stopped_workspace_is_visible_and_never_replaced(owned, monkeypatch, change):
    manager, _, compute = owned
    record = fresh(owned, monkeypatch)
    manager.stop(record["id"])
    session = Path(record["session_dir"])
    if change == "base":
        Path(record["base_disk"]).write_bytes(b"different base")
    elif change in {"binding", "missing-receipt"}:
        record = manager.registry.get(record["id"])
        if change == "binding":
            record["workspace"]["binding"]["session_id"] = "another-owner"
        else:
            record.pop("workspace")
        manager.registry.put(record)
    else:
        path = session / change
        path.rename(session / (change + ".original"))
        path.write_bytes(b"replacement")
    monkeypatch.setattr(manager, "_launch", lambda *a: pytest.fail("launched damaged workspace"))
    rows = manager.list()
    assert rows[0]["status"] == "recovery-required"
    assert rows[0]["recovery_reason"]
    with pytest.raises(load("lifecycle").RealmError, match="recovery"):
        manager.start("retention-owner")
    assert not compute["active"]
    assert session.exists()


@pytest.mark.parametrize("cause", ["idle", "backend-death"])
def test_reconciliation_stops_compute_but_keeps_workspace(owned, monkeypatch, cause):
    manager, _, compute = owned
    record = fresh(owned, monkeypatch)
    if cause == "idle":
        record["last_activity"] = 1
        manager.registry.put(record)
    else:
        compute["active"] = False
    kept = manager.list()[0]
    assert not compute["active"]
    assert kept["status"] == "stopped"
    assert (Path(kept["session_dir"]) / "disk.qcow2").exists()


def test_explicit_delete_is_stopped_only_and_base_cleanup_keeps_dependencies(owned, monkeypatch):
    manager, _, compute = owned
    record = fresh(owned, monkeypatch)
    with pytest.raises(load("vm_manager").VmError, match="stopped"):
        manager.delete(record["id"])
    manager.stop(record["id"])
    with pytest.raises(load("vm_manager").VmError, match="retained"):
        manager.remove_base()
    assert Path(record["base_disk"]).exists()
    assert manager.delete(record["id"])
    assert not Path(record["session_dir"]).exists()
    assert not manager.registry.path(record["id"]).exists()
    assert manager.remove_base()
    assert not Path(record["base_disk"]).exists()


def test_failed_retirement_is_a_visible_recovery_record(owned, monkeypatch):
    manager, record, compute = owned
    def unavailable(record):
        raise load("lifecycle").OwnershipError("owned compute observation unavailable")
    monkeypatch.setattr(load("vm_owner_lifetime"), "retire", unavailable)
    with pytest.raises(load("lifecycle").OwnershipError):
        manager.stop(record["id"])
    kept = manager.registry.get(record["id"])
    assert kept["status"] == "recovery-required"
    assert kept["cleanup_required"] is True
    assert manager.list()[0]["id"] == record["id"]
    assert compute["active"]
    assert Path(record["session_dir"]).exists()
    with pytest.raises(load("vm_manager").VmError):
        manager.validate(record["id"])


def test_compute_identity_changes_but_workspace_identity_does_not(owned, monkeypatch):
    manager, _, _ = owned
    record = fresh(owned, monkeypatch)
    epoch = record["compute_generation"]
    manager.stop(record["id"])
    next_record = manager.start(record["session_id"])
    assert next_record["compute_generation"] != epoch
    assert next_record["generation"] == record["generation"]


def test_clean_preserves_stopped_and_legacy_recovery_workspaces(owned):
    manager, record, _ = owned
    manager.stop(record["id"])
    result = load("cli").clean(manager)
    assert record["session_dir"] not in result["removed"]
    assert (Path(record["session_dir"]) / "disk.qcow2").exists()


def test_enrolled_pin_directory_is_synced_before_running_receipt(owned, monkeypatch):
    manager, _, _ = owned
    synced = []
    fsync = os.fsync
    def observe(fd):
        import stat
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            directory = Path(os.readlink(f"/proc/self/fd/{fd}"))
            if (directory / "ssh_known_hosts").exists():
                synced.append(directory)
        fsync(fd)
    monkeypatch.setattr(os, "fsync", observe)
    record = fresh(owned, monkeypatch)
    assert Path(record["session_dir"]) in synced


def test_retained_workspace_owner_and_profile_isolation(owned, monkeypatch, tmp_path):
    manager, _, _ = owned
    vm = load("vm_manager")
    monkeypatch.setattr(vm, "_generation_paths", lambda uid, generation: tmp_path / ("runtime-" + generation))
    first = fresh(owned, monkeypatch)
    second = manager.start("other-conversation")
    assert first["id"] != second["id"]
    assert first["session_dir"] != second["session_dir"]
    assert first["unit"] != second["unit"]
    disk = Path(first["session_dir"]) / "disk.qcow2"
    disk.write_bytes(b"first conversation's unfinished bytes")
    assert (Path(second["session_dir"]) / "disk.qcow2").read_bytes() == b"initial-guest"
    foreign = vm.VmManager(tmp_path / "foreign-profile")
    foreign.registry.put(first)
    with pytest.raises(vm.OwnershipError, match="ownership"):
        foreign.stop(first["id"])
    with pytest.raises(vm.OwnershipError, match="ownership"):
        foreign.delete(first["id"])
    assert disk.read_bytes() == b"first conversation's unfinished bytes"


def test_failed_restart_retains_workspace_instead_of_retrying_fresh(owned, monkeypatch):
    manager, _, _ = owned
    record = fresh(owned, monkeypatch)
    manager.stop(record["id"])
    def failed(record, **kwargs):
        raise OSError("fixture launch unavailable")
    monkeypatch.setattr(manager, "_launch", failed)
    with pytest.raises(OSError, match="unavailable"):
        manager.start(record["session_id"])
    assert Path(record["session_dir"]).exists()
    kept = manager.list()[0]
    assert kept["id"] == record["id"] and kept["status"] == "stopped"


def test_stop_retains_guest_bytes_and_owned_record(owned):
    manager, record, compute = owned
    disk = Path(record["session_dir"]) / "disk.qcow2"
    before = disk.read_bytes()
    assert manager.stop(record["id"])
    assert not compute["active"]
    assert disk.read_bytes() == before
    assert (disk.parent / "ssh_known_hosts").read_bytes() == b"fixture-enrolled-key\n"
    kept = manager.registry.get(record["id"])
    assert kept["status"] in {"stopped", "recovery-required"}
    assert manager.list()[0]["id"] == record["id"]
    with pytest.raises(load("vm_manager").VmError, match="not running"):
        manager.validate(record["id"])
