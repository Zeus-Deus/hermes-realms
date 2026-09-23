"""Real small qcow2 fixtures; installer/compute observations are inert, no VM boot."""
from contextlib import contextmanager
from dataclasses import asdict
import json
import os
from pathlib import Path
import runpy
import subprocess
import uuid

import pytest
from realms_test_paths import HERMES_ROOT, PLUGIN_ROOT

ROOT = HERMES_ROOT
load = runpy.run_path(str(PLUGIN_ROOT / "realms/_binding.py"))["load_runtime"]
pytestmark = pytest.mark.linux_only


def qemu(*args):
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=15).stdout


def image(base, marker):
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    qemu("/usr/bin/qemu-img", "create", "-f", "qcow2", str(base / "disk.qcow2"), "1M")
    qemu("/usr/bin/qemu-io", "-f", "qcow2", "-c", f"write -P {marker} 0 512", str(base / "disk.qcow2"))
    for name in ("OVMF_VARS.4m.fd", "credentials", "cidata.img"):
        (base / name).write_bytes(bytes([marker]) * 32)  # inert, NOT credentials


def verify_image(disk, base, marker, dirty=False):
    chain = json.loads(qemu("/usr/bin/qemu-img", "info", "--backing-chain", "--output=json", str(disk)))
    assert chain[0]["full-backing-filename"] == str(base / "disk.qcow2")
    assert len(chain) == 2
    qemu("/usr/bin/qemu-io", "-r", "-f", "qcow2", "-c", f"read -P {marker} 0 512", str(disk))
    if dirty:
        qemu("/usr/bin/qemu-io", "-r", "-f", "qcow2", "-c", "read -P 221 4096 512", str(disk))


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    vm = load("vm_manager")
    manager = vm.VmManager(tmp_path / "profile")
    old = manager.base_home()
    image(old, 17)
    load("lifecycle").atomic_json(old / "base.json", {"built_at": 1, "generation": uuid.uuid4().hex})
    monkeypatch.setattr(vm, "_generation_paths", lambda uid, gen: tmp_path / ("r-" + gen))
    monkeypatch.setattr(vm, "_base_runtime", lambda gen: tmp_path / ("b-" + gen))
    monkeypatch.setattr(vm, "require_resources", lambda *a, **kw: None)
    monkeypatch.setattr(manager, "_free_port", lambda taken: 2399)
    monkeypatch.setattr(manager, "_force_stop", lambda unit: None)
    state = {"stopped": False, "operations": []}
    @contextmanager
    def lifetime(*a, **kw):
        yield
        state["stopped"] = True  # inert successful supervisor retirement observation
    monkeypatch.setattr(load("setup_process"), "vm_lifetime", lifetime)
    def installer(args, *, vm_home, **kw):
        state["operations"].append(args)
        assert manager.base_home() == old
        if args == ["install"]:
            image(vm_home, 34)
        else:
            assert args == ["stop"]
    monkeypatch.setattr(manager, "_run_script", installer)
    return manager, old, state


def workspace(manager, base, status, *, session_id=None):
    generation = uuid.uuid4().hex
    runtime = load("vm_manager")._generation_paths(os.getuid(), generation)
    runtime.mkdir(mode=0o700)
    session = manager.registry.root / "vm" / generation
    session.mkdir(mode=0o700, parents=True)
    record: dict = dict(id="v-" + generation[:24], generation=generation, uid=os.getuid(),
                  home=str(manager.home), session_id=session_id or generation, kind=manager.KIND,
                  runtime_dir=str(runtime), session_dir=str(session),
                  unit=f"hermes-vm-{generation}.service",
                  guardian_unit=f"hermes-vm-{generation}-guard.service",
                  status=status, ssh_port=2300, ssh_host_key_pinned=True,
                  vnc_socket=str(runtime / "vnc.sock"), base_disk=str(base / "disk.qcow2"),
                  memory=manager.config.vm.memory, network=manager.config.vm.network)
    load("lifecycle").atomic_json(session / "spec.json", asdict(manager.config))
    manager.registry.put(record)
    record["workspace"] = manager._clone_base(session, base / "disk.qcow2")
    (runtime / "ssh_known_hosts").write_bytes(b"inert enrolled pin\n")
    (runtime / "ssh_known_hosts").chmod(0o600)
    load("vm_workspace").retain_pin(record)
    manager.registry.put(record)
    return record


@pytest.mark.parametrize("state", [
    "running", "stopped", "recovery-required", "foreign-owner", "stale-workspace",
    "stale-before-admission", "missing-pin", "permission-held",
])
def test_integration_ready_reuses_running_owner_with_corrupt_selection(fixture, monkeypatch, state):
    manager, old, _ = fixture
    service = load("integration").RealmIntegration(manager.home)
    owner = service.bind(session_origin="fresh", session_id="caller-owner")
    service.owners.set_mode(owner, "realm")
    service.owners.set_kind(owner, "omarchy-vm")
    service._vm = manager
    status = state if state in {"stopped", "recovery-required"} else "running"
    record = workspace(manager, old, status, session_id=owner)
    manager.registry.put(record)
    manager.install_base(update=True)
    pointer = manager.data / "current-base.json"
    pointer.write_text("broken allocation selection")
    vm = load("vm_manager")
    active = {record["unit"]: "old-invocation"} if status == "running" else {}
    launched = []
    def launch(r, **kwargs):
        launched.append(r["id"])
        r["invocation_id"] = uuid.uuid4().hex
        active[r["unit"]] = r["invocation_id"]
    # Inert compute/SSH only: start, resume, reconciliation, ownership metadata,
    # workspace validation, pin restoration and admission remain production code.
    monkeypatch.setattr(manager, "_launch", launch)
    monkeypatch.setattr(load("vm_owner_migration"), "migrate", lambda *a: None)
    monkeypatch.setattr(vm, "scope_info", lambda u: {
        "ActiveState": "active" if u in active else "inactive", "InvocationID": active.get(u)})
    monkeypatch.setattr(vm, "enroll_host_key", vm.require_host_key)
    monkeypatch.setattr(load("vm_owner_lifetime"), "retire", lambda r: active.pop(r["unit"], None))
    monkeypatch.setattr(load("vm_owner_lifetime"), "remove_dropin", lambda r: None)
    if state == "foreign-owner":
        record["home"] = str(manager.home / "foreign")
        manager.registry.put(record)
    elif state == "stale-workspace":
        record["status"] = "stopped"
        manager.registry.put(record)
        (Path(record["session_dir"]) / "spec.json").write_text("{}")
    elif state == "stale-before-admission":
        lock = manager.registry.lock
        @contextmanager
        def changed_before_admission():
            with lock():
                current = manager.registry.get(record["id"])
                current["status"] = "recovery-required"
                manager.registry.put(current)
                yield
        monkeypatch.setattr(manager.registry, "lock", changed_before_admission)
    elif state == "missing-pin":
        (Path(record["runtime_dir"]) / "ssh_known_hosts").unlink()
    elif state == "permission-held":
        with service.owners.connection() as db:
            db.execute("UPDATE owners SET execution_contract=NULL,mode='ask' WHERE id=?", (owner,))
    before = service.owners.permission(owner)
    disk = Path(record["session_dir"]) / "disk.qcow2"
    preserved = disk.stat().st_ino, disk.read_bytes(), record["workspace"]
    if state in {"running", "stopped"}:
        result = service.ready(owner)
        assert result["id"] == record["id"]
        assert result["workspace"] == record["workspace"]
        assert service._attachments[owner][2] == record["id"]
        assert launched == ([record["id"]] if state == "stopped" else [])
    else:
        error, match = {
            "foreign-owner": (vm.OwnershipError, "ownership mismatch"),
            "missing-pin": (vm.OwnershipError, "pin|trust"),
            "permission-held": (load("integration").OwnerError, "permissions have not been converted"),
        }.get(state, (vm.VmError, "requires recovery"))
        with pytest.raises(error, match=match):
            service.ready(owner)
        assert not launched and owner not in service._attachments
    assert service.owners.permission(owner) == before
    assert (disk.stat().st_ino, disk.read_bytes(), record["workspace"]) == preserved
    assert pointer.read_text() == "broken allocation selection"
    verify_image(disk, old, 17)


@pytest.mark.parametrize("failure", ["corrupt-selection", "foreign-selection", "missing-prerequisites", "removed-before-admission"])
def test_integration_fresh_allocation_keeps_setup_failure(fixture, monkeypatch, failure):
    import fcntl
    manager, old, _ = fixture
    integration = load("integration")
    service = integration.RealmIntegration(manager.home)
    owner = service.bind(session_origin="fresh", session_id="fresh-caller")
    service.owners.set_mode(owner, "realm")
    service.owners.set_kind(owner, "omarchy-vm")
    service._vm = manager
    manager.install_base(update=True)
    pointer = manager.data / "current-base.json"
    if failure in {"corrupt-selection", "removed-before-admission"}:
        pointer.write_text("broken allocation selection")
    elif failure == "foreign-selection":
        value = json.loads(pointer.read_text())
        value["home"] = str(manager.home / "foreign")
        pointer.write_text(json.dumps(value))
    if failure == "removed-before-admission":
        record = workspace(manager, old, "stopped", session_id=owner)
        manager.registry.put(record)
        lock = manager.registry.lock
        @contextmanager
        def removed_before_admission():
            with lock():
                manager.registry.remove(record["id"])
                yield
        monkeypatch.setattr(manager.registry, "lock", removed_before_admission)
    which = load("vm_manager").shutil.which
    monkeypatch.setattr(load("vm_manager").shutil, "which", lambda name: None if name == "qemu-system-x86_64" else which(name))
    setup_calls = []
    setup = integration.vm_setup_status
    def observe_setup(home):
        # A second descriptor cannot acquire the same profile registry lock.
        with (manager.registry.root / ".lock").open("a") as probe:
            with pytest.raises(BlockingIOError):
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        setup_calls.append(home)
        return setup(home)
    monkeypatch.setattr(integration, "vm_setup_status", observe_setup)
    def forbidden(*a, **kw):
        pytest.fail("fresh setup failure reached resource/compute admission")
    monkeypatch.setattr(load("vm_manager"), "require_resources", forbidden)
    monkeypatch.setattr(manager, "_launch", forbidden)
    before = service.owners.permission(owner), pointer.read_bytes()
    error = load("vm_manager").OwnershipError if failure == "foreign-selection" else integration.SetupError
    match = "ownership|selection" if failure == "foreign-selection" else (
        "qemu-system-x86_64" if failure == "missing-prerequisites" else "Expecting value")
    with pytest.raises(error, match=match):
        service.ready(owner)
    assert setup_calls == [manager.home]
    assert manager.registry.records() == []
    assert owner not in service._attachments
    assert (service.owners.permission(owner), pointer.read_bytes()) == before


def test_completed_update_selects_new_base_without_changing_retained_dependencies(fixture, monkeypatch):
    manager, old, state = fixture
    records = [workspace(manager, old, status) for status in ("running", "stopped")]
    for r in records:
        qemu("/usr/bin/qemu-io", "-f", "qcow2", "-c", "write -P 221 4096 512", str(Path(r["session_dir"]) / "disk.qcow2"))
    before = {p: (p.stat().st_ino, p.read_bytes()) for p in old.iterdir()}
    receipts = manager.registry.records()
    generation = uuid.uuid4().hex
    result = manager.install_base(update=True, generation=generation)
    selected = manager.base_home()
    assert selected != old and selected.name == generation
    assert state["stopped"] and state["operations"] == [["install"], ["stop"]]
    assert result["present"] and result["path"] == str(selected / "disk.qcow2")
    assert load("setup_plan").base_present(manager.home)
    assert load("vm_manager").VmManager(manager.home).base_home() == selected
    assert {p: (p.stat().st_ino, p.read_bytes()) for p in old.iterdir()} == before
    assert manager.registry.records() == receipts
    for r in records:
        load("vm_workspace").validate(r)
        verify_image(Path(r["session_dir"]) / "disk.qcow2", old, 17, dirty=True)
    new = workspace(manager, selected, "stopped")
    load("vm_workspace").validate(new)
    verify_image(Path(new["session_dir"]) / "disk.qcow2", selected, 34)


def test_competing_completed_update_refuses_stale_publication(fixture, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    manager, old, state = fixture
    first, second = uuid.uuid4().hex, uuid.uuid4().hex
    admitted = {g: threading.Event() for g in (first, second)}
    release = {g: threading.Event() for g in (first, second)}
    def installer(args, *, vm_home, unit, **kw):
        generation = unit.removeprefix("hermes-vm-base-").removesuffix(".service")
        if args == ["install"]:
            image(vm_home, 34 if generation == first else 51)
            admitted[generation].set()
            assert release[generation].wait(10), "slow installation held selection lock"
    monkeypatch.setattr(manager, "_run_script", installer)
    with ThreadPoolExecutor(max_workers=2) as pool:
        winner = pool.submit(manager.install_base, update=True, generation=first)
        loser = pool.submit(manager.install_base, update=True, generation=second)
        try:
            assert all(event.wait(10) for event in admitted.values())
            assert manager.base_home() == old
            release[first].set()
            assert winner.result(timeout=10)["present"]
            selected = manager.base_home()
            receipt = (manager.data / "current-base.json").read_bytes()
            release[second].set()
            with pytest.raises(load("vm_manager").VmError, match="changed|competing|failed"):
                loser.result(timeout=10)
            assert manager.base_home() == selected
            assert (manager.data / "current-base.json").read_bytes() == receipt
            assert old.is_dir()
            record = workspace(manager, selected, "stopped")
            verify_image(Path(record["session_dir"]) / "disk.qcow2", selected, 34)
        finally:
            for event in release.values():
                event.set()


def test_clone_uses_one_physical_base_for_disk_firmware_and_auxiliary_files(fixture):
    manager, old, _ = fixture
    manager.install_base(update=True)
    assert manager.base_home() != old
    record = workspace(manager, old, "stopped")
    session = Path(record["session_dir"])
    verify_image(session / "disk.qcow2", old, 17)
    for name in ("OVMF_VARS.4m.fd", "credentials", "cidata.img"):
        assert (session / name).read_bytes() == (old / name).read_bytes()


@pytest.mark.parametrize("status", ["running", "stopped", "recovery-required"])


def test_retained_owner_resolution_does_not_consult_current_base(fixture, monkeypatch, status):
    manager, old, _ = fixture
    record = workspace(manager, old, status)
    manager.install_base(update=True)
    # Corrupt ONLY the future allocation selection; old physical work is healthy.
    (manager.data / "current-base.json").write_text("invalid selection")
    monkeypatch.setattr(manager, "_reconcile_locked", lambda: None)
    monkeypatch.setattr(manager, "_bind_to_owner", lambda r: None)
    monkeypatch.setattr(load("vm_manager"), "unit_active", lambda u: True)
    resumed = []
    def resume(r, *, before_start=None):
        load("vm_workspace").validate(r)
        if before_start is not None:
            before_start()
        resumed.append(r["id"])  # inert compute-start boundary; never boot QEMU
        return r
    monkeypatch.setattr(manager, "_resume_locked", resume)
    if status == "recovery-required":
        with pytest.raises(load("vm_manager").VmError, match="requires recovery"):
            manager.start(record["session_id"])
    else:
        assert manager.start(record["session_id"])["id"] == record["id"]
        assert resumed == ([record["id"]] if status == "stopped" else [])
    verify_image(Path(record["session_dir"]) / "disk.qcow2", old, 17)


@pytest.mark.parametrize("failure", ["installer", "stop", "retirement", "publication", "missing-firmware", "symlink-disk"])


def test_failed_update_keeps_prior_selection_and_dirty_work(fixture, monkeypatch, failure):
    manager, old, _ = fixture
    record = workspace(manager, old, "stopped")
    disk = Path(record["session_dir"]) / "disk.qcow2"
    qemu("/usr/bin/qemu-io", "-f", "qcow2", "-c", "write -P 221 4096 512", str(disk))
    before = manager.registry.records(), disk.stat().st_ino, disk.read_bytes()
    installer = manager._run_script
    def failed(args, *, vm_home, **kw):
        installer(args, vm_home=vm_home, **kw)
        if args == ["install"]:
            if failure == "installer":
                raise subprocess.CalledProcessError(1, "inert signed installer")
            if failure == "missing-firmware":
                (vm_home / "OVMF_VARS.4m.fd").unlink()
            if failure == "symlink-disk":
                (vm_home / "disk.qcow2").unlink()
                (vm_home / "disk.qcow2").symlink_to(old / "disk.qcow2")
        elif failure == "stop":
            raise subprocess.CalledProcessError(1, "inert stop")
    monkeypatch.setattr(manager, "_run_script", failed)
    if failure == "retirement":
        @contextmanager
        def uncertain(*a, **kw):
            yield
            raise OSError("inert compute retirement unknown")
        monkeypatch.setattr(load("setup_process"), "vm_lifetime", uncertain)
    if failure == "publication":
        replace = os.replace
        def unavailable(src, dst):
            if Path(dst).name == "current-base.json":
                assert manager.base_home() == old
                raise OSError("inert pointer publication failure")
            return replace(src, dst)
        monkeypatch.setattr(os, "replace", unavailable)
    with pytest.raises((load("lifecycle").RealmError, ValueError, OSError)):
        manager.install_base(update=True)
    assert manager.base_home() == old and manager.base_status()["present"]
    assert not (manager.data / "current-base.json").exists()
    assert (manager.registry.records(), disk.stat().st_ino, disk.read_bytes()) == before
    load("vm_workspace").validate(record)
    verify_image(disk, old, 17, dirty=True)


def test_update_publication_syncs_physical_generation_before_pointer(fixture, monkeypatch):
    manager, old, state = fixture
    fsync, replace = os.fsync, os.replace
    synced = set()
    def observe(fd):
        synced.add(Path(os.readlink(f"/proc/self/fd/{fd}")))
        fsync(fd)
    def publish(src, dst):
        if Path(dst).name == "current-base.json":
            generation = json.loads(Path(src).read_bytes())["generation"]
            base = manager.data / "bases" / generation
            staging = manager.data / (".install-" + generation)
            assert state["stopped"] and manager.base_home() == old
            assert base.parent in synced
            assert manager.data in synced  # the new bases/ entry must be durable too
            for name in ("disk.qcow2", "OVMF_VARS.4m.fd", "credentials", "cidata.img"):
                assert staging / name in synced
            assert base in synced or staging in synced
        replace(src, dst)
    monkeypatch.setattr(os, "fsync", observe)
    monkeypatch.setattr(os, "replace", publish)
    assert manager.install_base(update=True)["present"]


def test_storage_counts_retained_bases_and_remove_refuses_generations(fixture):
    manager, old, _ = fixture
    manager.install_base(update=True)
    selected = manager.base_home()
    expected = sum(p.stat().st_size for base in (old, selected) for p in base.iterdir() if p.is_file())
    assert manager.storage()["base_bytes"] == expected
    with pytest.raises(load("vm_manager").VmError, match="generation|retained"):
        manager.remove_base()
    assert manager.base_home() == selected and old.is_dir() and selected.is_dir()


@pytest.mark.parametrize("change", ["literal-path", "foreign-profile", "generation", "symlink", "inode", "metadata"])


def test_versioned_workspace_still_rejects_changed_dependency(fixture, change):
    manager, old, _ = fixture
    manager.install_base(update=True)
    base = manager.base_home()
    record = workspace(manager, base, "stopped")
    disk = base / "disk.qcow2"
    if change == "literal-path":
        record["base_disk"] = str(base) + "/./disk.qcow2"
        # Even a newly created receipt must refuse a nonliteral dependency.
        with pytest.raises(load("lifecycle").OwnershipError):
            load("vm_workspace").create(record)
        return
    if change == "foreign-profile":
        record["base_disk"] = str(manager.home.parent / "foreign" / "bases" / base.name / "disk.qcow2")
    elif change == "generation":
        metadata = json.loads((base / "base.json").read_text())
        metadata["generation"] = uuid.uuid4().hex
        (base / "base.json").write_text(json.dumps(metadata))
    elif change == "symlink":
        disk.rename(base / "original.qcow2")
        disk.symlink_to(base / "original.qcow2")
    elif change == "inode":
        disk.rename(base / "original.qcow2")
        disk.write_bytes((base / "original.qcow2").read_bytes())
    else:
        (base / "base.json").write_text('{}')
    with pytest.raises(load("lifecycle").OwnershipError):
        load("vm_workspace").validate(record)


@pytest.mark.parametrize("replacement", ["empty-generation", "redirected-parent"])


def test_publication_revalidates_physical_destination(fixture, monkeypatch, replacement):
    manager, old, _ = fixture
    generation = uuid.uuid4().hex
    installer = manager._run_script
    destination = manager.data / "bases" / generation
    def raced(args, **kw):
        installer(args, **kw)
        if args == ["stop"]:
            if replacement == "empty-generation":
                destination.mkdir(mode=0o700, parents=True)
            else:
                unrelated = manager.home / "unrelated"
                unrelated.mkdir()
                destination.parent.symlink_to(unrelated)
    monkeypatch.setattr(manager, "_run_script", raced)
    with pytest.raises((load("lifecycle").RealmError, ValueError, OSError)):
        manager.install_base(update=True, generation=generation)
    assert manager.base_home() == old and manager.base_status()["present"]
    assert not (destination / "disk.qcow2").exists()


@pytest.mark.parametrize("change", ["fifo", "directory", "symlink", "foreign-home", "bad-generation", "invalid-json"])


def test_invalid_selection_is_not_a_legacy_fallback(fixture, change):
    manager, old, _ = fixture
    manager.install_base(update=True)
    pointer = manager.data / "current-base.json"
    metadata = json.loads(pointer.read_text())
    pointer.unlink()
    if change == "fifo":
        os.mkfifo(pointer)
    elif change == "directory":
        pointer.mkdir()
    elif change == "symlink":
        pointer.symlink_to(old / "base.json")
    elif change == "invalid-json":
        pointer.write_text("not json")
    else:
        metadata["home" if change == "foreign-home" else "generation"] = "other"
        pointer.write_text(json.dumps(metadata))
    with pytest.raises((load("lifecycle").RealmError, ValueError, OSError)):
        manager.base_home()
    assert old.is_dir()


def test_public_start_uses_selected_base_then_resumes_old_physical_workspace(fixture, monkeypatch):
    manager, old, _ = fixture
    record = workspace(manager, old, "stopped")
    manager.install_base(update=True)
    selected = manager.base_home()
    vm, lifetime = load("vm_manager"), load("vm_owner_lifetime")
    active = {}
    def launch(r, **kwargs):
        r["invocation_id"] = uuid.uuid4().hex
        active[r["unit"]] = r["invocation_id"]
    def enroll(r):
        pin = Path(r["runtime_dir"]) / "ssh_known_hosts"
        if not pin.exists():
            pin.write_bytes(b"inert enrolled pin\n")
            pin.chmod(0o600)
    monkeypatch.setattr(manager, "_launch", launch)
    monkeypatch.setattr(manager, "_bind_to_owner", lambda r: None)
    monkeypatch.setattr(vm, "enroll_host_key", enroll)
    monkeypatch.setattr(vm, "scope_info", lambda unit: {
        "ActiveState": "active" if unit in active else "inactive", "InvocationID": active.get(unit)})
    monkeypatch.setattr(lifetime, "retire", lambda r: active.pop(r["unit"], None))
    monkeypatch.setattr(lifetime, "remove_dropin", lambda r: None)
    new = manager.start("new-owner")
    verify_image(Path(new["session_dir"]) / "disk.qcow2", selected, 34)
    for name in ("OVMF_VARS.4m.fd", "credentials", "cidata.img"):
        assert (Path(new["session_dir"]) / name).read_bytes() == (selected / name).read_bytes()
    (manager.data / "current-base.json").write_text("broken allocation selection")
    resumed = manager.start(record["session_id"])
    assert resumed["id"] == record["id"] and resumed["workspace"] == record["workspace"]
    assert manager.start(record["session_id"])["id"] == record["id"]
    load("vm_workspace").validate(resumed)
    verify_image(Path(resumed["session_dir"]) / "disk.qcow2", old, 17)


def test_base_status_uses_one_selection_snapshot(fixture, monkeypatch):
    manager, old, _ = fixture
    manager.install_base(update=True)
    selected = manager.base_home()
    reads = []
    def changing_selection():
        reads.append(True)
        return old if len(reads) == 1 else selected
    monkeypatch.setattr(manager, "base_home", changing_selection)
    status = manager.base_status()
    assert status["path"] == str(old / "disk.qcow2")
    assert status["generation"] == json.loads((old / "base.json").read_text())["generation"]
    assert len(reads) == 1
