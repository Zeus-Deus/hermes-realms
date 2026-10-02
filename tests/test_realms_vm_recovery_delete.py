"""Retained VM workspaces across reboots, and owner-confirmed discard/prune.

No real guest runs: compute is a per-unit test double, but receipts, files,
directories and the process-holder check are real.
"""
import os
from pathlib import Path
import runpy
import subprocess
import sys

import pytest
from realms_test_paths import PLUGIN_ROOT

pytestmark = pytest.mark.platforms("linux")

load = runpy.run_path(str(PLUGIN_ROOT / "realms/_binding.py"))["load_runtime"]
retention = runpy.run_path(str(PLUGIN_ROOT / "tests/test_realms_vm_retention.py"))
cli_pty = runpy.run_path(str(PLUGIN_ROOT / "tests/test_realms_delete_cli.py"))["cli_pty"]


def tree(path):
    return {str(p.relative_to(path)): (p.lstat().st_ino, p.read_bytes())
            for p in Path(path).rglob("*") if p.is_file() and not p.is_symlink()}


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    """Three workspaces of one profile with independent per-unit compute state."""
    owned = retention["owned"].__wrapped__(tmp_path, monkeypatch)
    manager, _, _ = owned
    vm = load("vm_manager")
    lifetime = load("vm_owner_lifetime")
    monkeypatch.setattr(vm, "_generation_paths", lambda uid, gen: tmp_path / ("runtime-" + gen))
    retention["fresh"](owned, monkeypatch, start=False)
    active = set()

    def units(record):
        return {record["unit"], lifetime.owner_unit(record), record["guardian_unit"]}

    def launch(record, **kwargs):
        active.update(units(record))
        record["invocation_id"] = record["unit"]

    def retire(record):
        active.difference_update(units(record))

    monkeypatch.setattr(manager, "_launch", launch)
    monkeypatch.setattr(lifetime, "retire", retire)
    monkeypatch.setattr(vm, "scope_info", lambda unit: {
        "ActiveState": "active" if unit in active else "inactive",
        "InvocationID": unit if unit in active else ""})
    monkeypatch.setattr(vm, "unit_active", lambda unit: unit in active)
    monkeypatch.setattr(lifetime, "validate_owner", lambda record: None)
    stopped = manager.start("stopped-owner")
    manager.stop(stopped["id"])
    damaged = manager.start("damaged-owner")
    manager.stop(damaged["id"])
    running = manager.start("running-owner")
    return manager, stopped, damaged, running, active


def renumber_devices(monkeypatch, offset=7, *, only=None):
    """Make the kernel report another st_dev for the same inodes, as after a reboot.

    btrfs subvolumes and device-mapper volumes get their anonymous device
    numbers at mount time, so one file's st_dev can differ between boots.
    """
    real_stat, real_fstat = os.stat, os.fstat

    def shift(info):
        values = list(info)
        values[2] = info.st_dev + offset
        # Keep nanosecond timestamps, which the tuple form alone would drop.
        return os.stat_result(values, {name: getattr(info, name) for name in
                                       ("st_atime_ns", "st_mtime_ns", "st_ctime_ns",
                                        "st_atime", "st_mtime", "st_ctime")})

    def stat(path, *args, **kwargs):
        return shift(real_stat(path, *args, **kwargs)) if only is None else real_stat(path, *args, **kwargs)

    def fstat(fd):
        info = real_fstat(fd)
        return shift(info) if only is None or info.st_ino == only else info

    monkeypatch.setattr(os, "stat", stat)
    monkeypatch.setattr(os, "lstat", lambda path, **kwargs: stat(path, follow_symlinks=False))
    monkeypatch.setattr(os, "fstat", fstat)


def test_retained_workspace_survives_device_renumbering(fleet, monkeypatch):
    manager, stopped, _, running, active = fleet
    disk = Path(stopped["session_dir"]) / "disk.qcow2"
    before = tree(stopped["session_dir"])
    with monkeypatch.context() as reboot:
        renumber_devices(reboot)
        rows = {row["id"]: row for row in manager.list()}
        assert rows[stopped["id"]]["status"] == "stopped", rows[stopped["id"]].get("recovery_reason")
        assert rows[running["id"]]["status"] == "running"
        assert manager.stop(stopped["id"])
        assert manager.registry.get(stopped["id"])["status"] == "stopped"
    assert tree(stopped["session_dir"]) == before
    assert disk.exists()


def test_receipts_from_older_versions_with_device_numbers_heal(fleet):
    """Earlier receipts stored st_dev; records already marked recovery-required recover."""
    manager, stopped, _, _, _ = fleet
    record = manager.registry.get(stopped["id"])
    workspace = record["workspace"]
    for receipt in (*workspace["files"].values(), *workspace["base"].values(), workspace["pin"]):
        receipt["device"] = 4242
    record.update(status="recovery-required",
                  recovery_reason="VM retained workspace file changed: disk.qcow2")
    manager.registry.put(record)
    healed = {row["id"]: row for row in manager.list()}[stopped["id"]]
    assert healed["status"] == "stopped" and "recovery_reason" not in healed
    snapshot = manager.delete_snapshot(stopped["id"], session_id="stopped-owner")
    assert manager.delete(stopped["id"], session_id="stopped-owner", expected_snapshot=snapshot)
    assert not Path(stopped["session_dir"]).exists()


@pytest.mark.parametrize("change", ["replaced-disk", "file-on-other-filesystem", "unconfirmed-retirement"])
def test_real_changes_still_require_recovery(fleet, monkeypatch, change):
    manager, stopped, _, _, _ = fleet
    disk = Path(stopped["session_dir"]) / "disk.qcow2"
    if change == "replaced-disk":
        disk.rename(disk.with_name("original.qcow2"))
        disk.write_bytes(b"swapped in")
    elif change == "file-on-other-filesystem":
        # A mount over the file can reuse its inode number; it is never the same file.
        renumber_devices(monkeypatch, only=disk.stat().st_ino)
    else:
        record = manager.registry.get(stopped["id"])
        record.update(status="recovery-required", cleanup_required=True,
                      recovery_reason="Compute retirement was not confirmed")
        manager.registry.put(record)
    row = {row["id"]: row for row in manager.list()}[stopped["id"]]
    assert row["status"] == "recovery-required"
    with pytest.raises(load("lifecycle").RealmError):
        manager.start("stopped-owner")


def damage(manager, record):
    """The other real-world cause: a launch that died before its SSH pin was retained."""
    (Path(record["session_dir"]) / "ssh_known_hosts").unlink()
    rows = {row["id"]: row for row in manager.list()}
    assert rows[record["id"]]["status"] == "recovery-required"
    return manager.registry.get(record["id"])


def test_discard_deletes_only_a_confirmed_recovery_workspace(fleet):
    manager, stopped, damaged, running, active = fleet
    damage(manager, damaged)
    others = {key: tree(r["session_dir"]) for key, r in (("s", stopped), ("r", running))}
    with pytest.raises(load("vm_manager").VmError, match="--discard"):
        manager.delete(damaged["id"], session_id="damaged-owner")
    with pytest.raises(load("lifecycle").OwnershipError, match="another session"):
        manager.discard_snapshot(damaged["id"], session_id="stopped-owner")
    snapshot = manager.discard_snapshot(damaged["id"], session_id="damaged-owner")
    assert manager.delete(damaged["id"], session_id="damaged-owner",
                          expected_snapshot=snapshot, discard=True)
    assert not Path(damaged["session_dir"]).exists()
    assert not manager.registry.path(damaged["id"]).exists()
    assert {key: tree(r["session_dir"]) for key, r in (("s", stopped), ("r", running))} == others
    assert manager.registry.get(running["id"])["status"] == "running"


@pytest.mark.parametrize("live", ["running", "starting", "active-unit", "open-file", "cleanup-required"])
def test_discard_never_touches_live_or_unretired_compute(fleet, live):
    manager, _, damaged, running, active = fleet
    damage(manager, damaged)
    target = damaged
    holder = None
    if live in {"running", "starting"}:
        target = running
        if live == "starting":
            record = manager.registry.get(running["id"])
            record["status"] = "starting"
            manager.registry.put(record)
    elif live == "active-unit":
        active.add(load("vm_owner_lifetime").owner_unit(damaged))
    elif live == "open-file":
        # A real process holding the overlay, as a QEMU outside its unit would.
        disk = open(Path(damaged["session_dir"]) / "disk.qcow2", "rb")
        holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stdin=disk)
        disk.close()
    else:
        record = manager.registry.get(damaged["id"])
        record["cleanup_required"] = True
        manager.registry.put(record)
    before = tree(manager.home)
    try:
        with pytest.raises(load("lifecycle").RealmError) as refused:
            manager.discard_snapshot(target["id"], session_id=target["session_id"])
        with pytest.raises(load("lifecycle").RealmError):
            manager.delete(target["id"], discard=True)
        if live == "open-file":
            assert str(holder.pid) in str(refused.value)
        review = manager.prune_snapshot(everything=True)
        assert target["id"] not in review["delete"]
        assert target["id"] in {row["id"] for row in review["kept"]}
        assert tree(manager.home) == before
    finally:
        if holder is not None:
            holder.kill()
            holder.wait()
    if live == "open-file":
        assert manager.delete(damaged["id"], discard=True)


@pytest.mark.parametrize("change", ["added-file", "rewritten-file", "replaced-directory"])
def test_discard_confirmation_binds_the_reviewed_contents(fleet, change):
    manager, _, damaged, _, _ = fleet
    damage(manager, damaged)
    snapshot = manager.discard_snapshot(damaged["id"], session_id="damaged-owner")
    session = Path(damaged["session_dir"])
    if change == "added-file":
        (session / "late-work").write_bytes(b"written after review")
    elif change == "rewritten-file":
        (session / "spec.json").write_text('{"memory": 1}')
    else:
        session.rename(session.with_name(session.name + "-saved"))
        session.mkdir(mode=0o700)
        (session / "disk.qcow2").write_bytes(b"replacement")
    before = tree(manager.home)
    with pytest.raises(load("lifecycle").OwnershipError, match="changed"):
        manager.delete(damaged["id"], session_id="damaged-owner", expected_snapshot=snapshot, discard=True)
    assert tree(manager.home) == before


def test_discard_refuses_links_and_directories_inside_the_workspace(fleet, tmp_path):
    manager, _, damaged, _, _ = fleet
    damage(manager, damaged)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_bytes(b"not workspace data")
    (Path(damaged["session_dir"]) / "link").symlink_to(outside)
    with pytest.raises(load("lifecycle").OwnershipError, match="non-file entry: link"):
        manager.delete(damaged["id"], discard=True)
    assert (outside / "keep").read_bytes() == b"not workspace data"
    assert Path(damaged["session_dir"]).exists()


def test_interrupted_discard_retries_only_unchanged_remains(fleet, monkeypatch):
    manager, _, damaged, _, _ = fleet
    damage(manager, damaged)
    with monkeypatch.context() as fault:
        fault.setattr(manager.registry, "remove", lambda vm_id: (_ for _ in ()).throw(OSError("interrupted")))
        with pytest.raises(OSError, match="interrupted"):
            manager.delete(damaged["id"], discard=True)
    assert manager.registry.get(damaged["id"])["status"] == "deleting"
    with pytest.raises(load("vm_manager").VmError):
        manager.delete(damaged["id"])  # a discard is never finished as a verified delete
    assert manager.delete(damaged["id"], discard=True)
    assert not manager.registry.path(damaged["id"]).exists()


def test_prune_review_is_read_only_and_keeps_running_and_kept_ids(fleet):
    manager, stopped, damaged, running, active = fleet
    damage(manager, damaged)
    before = tree(manager.home)
    registry = {p.name: p.read_bytes() for p in manager.registry.root.glob("v-*.json")}
    review = manager.prune_snapshot(everything=True, keep=[stopped["id"]])
    assert set(review["delete"]) == {damaged["id"]}
    kept = {row["id"]: row["reason"] for row in review["kept"]}
    assert kept[stopped["id"]] == "Explicitly kept"
    assert "compute is not stopped" in kept[running["id"]]
    assert tree(manager.home) == before
    assert {p.name: p.read_bytes() for p in manager.registry.root.glob("v-*.json")} == registry
    assert set(manager.prune_snapshot(everything=True)["delete"]) == {stopped["id"], damaged["id"]}
    with pytest.raises(ValueError, match="explicitly"):
        manager.prune_snapshot()


def test_prune_refuses_everything_when_any_target_changed(fleet):
    manager, stopped, damaged, running, _ = fleet
    damage(manager, damaged)
    review = manager.prune_snapshot(everything=True)
    resumed = manager.start("stopped-owner")
    manager.stop(resumed["id"])
    before = tree(manager.home)
    with pytest.raises(load("lifecycle").OwnershipError, match="changed since review"):
        manager.prune(review)
    assert tree(manager.home) == before


def prune_args(manager, *extra):
    return ["--home", str(manager.home), "vm", "prune", *extra]


def test_cli_prune_dry_run_then_confirmed(fleet, capsys):
    import json
    manager, stopped, damaged, running, active = fleet
    damage(manager, damaged)
    before = tree(manager.home)
    assert load("cli").main(prune_args(manager, "--all", "--keep", running["id"], "--dry-run")) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["dry_run"] is True and result["deleted"] == []
    assert {row["id"] for row in result["would_delete"]} == {stopped["id"], damaged["id"]}
    assert result["bytes"] > 0
    assert {row["id"] for row in result["kept"]} == {running["id"]}
    assert tree(manager.home) == before
    # Without a terminal, nothing beyond the listing happens.
    assert load("cli").main(prune_args(manager, "--all")) == 1
    assert "interactive" in capsys.readouterr().err
    assert tree(manager.home) == before
    running_before = tree(running["session_dir"])
    code, output = cli_pty(prune_args(manager, "--all"), lambda phrase: phrase + b"\n")
    assert code == 0, output
    assert "PRUNE 2 " in output and "keep " + running["id"] in output
    assert not Path(stopped["session_dir"]).exists() and not Path(damaged["session_dir"]).exists()
    assert tree(running["session_dir"]) == running_before
    assert manager.registry.get(running["id"])["status"] == "running"


@pytest.mark.parametrize("response", [b"no\n", b"\x04"])
def test_cli_prune_cancel_preserves_every_byte(fleet, response):
    manager, _, damaged, _, _ = fleet
    damage(manager, damaged)
    before = tree(manager.home)
    code, output = cli_pty(prune_args(manager, "--all"), lambda phrase: response)
    assert code == 1 and "cancelled" in output, output
    assert tree(manager.home) == before


def test_cli_delete_discard_is_interactive(fleet):
    manager, _, damaged, _, _ = fleet
    damage(manager, damaged)
    args = ["--home", str(manager.home), "vm", "delete", damaged["id"], "--session-id", "damaged-owner"]
    code, output = cli_pty(args, lambda phrase: pytest.fail("verified delete must refuse"))
    assert code == 1 and "--discard" in output, output
    code, output = cli_pty([*args, "--discard"], lambda phrase: phrase + b"\n")
    assert code == 0, output
    assert "needs recovery" in output or "recovery" in output
    assert not Path(damaged["session_dir"]).exists()


def test_systemd_unavailable_refuses_discard_and_prune_and_never_heals(fleet, monkeypatch):
    """Unknown compute state is never permission to delete or to resume."""
    manager, stopped, damaged, _, _ = fleet
    damage(manager, damaged)
    record = manager.registry.get(stopped["id"])
    for receipt in record["workspace"]["files"].values():
        receipt["device"] = 4242
    record.update(status="recovery-required", recovery_reason="old receipt")
    manager.registry.put(record)
    vm = load("vm_manager")

    def unavailable(unit):
        raise load("lifecycle").RealmError("could not inspect systemd unit " + unit)
    monkeypatch.setattr(vm, "scope_info", unavailable)
    before = tree(manager.home)
    rows = {row["id"]: row for row in manager.list()}
    assert rows[stopped["id"]]["status"] == "recovery-required"
    with pytest.raises(load("lifecycle").RealmError, match="could not inspect"):
        manager.discard_snapshot(damaged["id"], session_id="damaged-owner")
    with pytest.raises(load("lifecycle").RealmError, match="could not inspect"):
        manager.delete(damaged["id"], discard=True)
    review = manager.prune_snapshot(everything=True)
    assert review["delete"] == {}
    kept = {row["id"]: row["reason"] for row in review["kept"]}
    assert "could not inspect" in kept[damaged["id"]] and "could not inspect" in kept[stopped["id"]]
    assert tree(manager.home) == before


def test_confirmed_prune_all_keeps_shared_base_and_iso(fleet):
    manager, stopped, damaged, running, _ = fleet
    damage(manager, damaged)
    iso = manager.data / "iso"
    iso.mkdir(parents=True, exist_ok=True)
    (iso / "omarchy.iso").write_bytes(b"shared installer")
    shared = tree(manager.data)
    assert any(name.startswith("base/") for name in shared) and "iso/omarchy.iso" in shared
    deleted = manager.prune(manager.prune_snapshot(everything=True))
    assert set(deleted) == {stopped["id"], damaged["id"]}
    assert tree(manager.data) == shared
    assert manager.registry.get(running["id"])["status"] == "running"


def test_prune_recovery_only_keeps_healthy_stopped_workspaces(fleet, capsys):
    import json
    manager, stopped, damaged, running, _ = fleet
    damage(manager, damaged)
    review = manager.prune_snapshot(everything=True, recovery_only=True)
    assert set(review["delete"]) == {damaged["id"]}
    kept = {row["id"]: row["reason"] for row in review["kept"]}
    assert "not recovery-required" in kept[stopped["id"]]
    assert running["id"] in kept
    assert load("cli").main(prune_args(manager, "--all", "--recovery-only", "--dry-run")) == 0
    result = json.loads(capsys.readouterr().out)
    assert [row["id"] for row in result["would_delete"]] == [damaged["id"]]
    code, output = cli_pty(prune_args(manager, "--all", "--recovery-only"), lambda phrase: phrase + b"\n")
    assert code == 0, output
    assert "PRUNE 1 " in output
    assert not Path(damaged["session_dir"]).exists()
    assert manager.registry.get(stopped["id"])["status"] == "stopped"
    assert Path(stopped["session_dir"]).exists()


def test_prune_recovery_only_keeps_records_that_verify_again(fleet):
    """A stale recovery mark (from an older receipt) is resumable, so it is not pruned."""
    manager, stopped, damaged, _, _ = fleet
    damage(manager, damaged)
    record = manager.registry.get(stopped["id"])
    for receipt in record["workspace"]["files"].values():
        receipt["device"] = 4242
    record.update(status="recovery-required", recovery_reason="VM retained workspace file changed: disk.qcow2")
    manager.registry.put(record)
    registry = {p.name: p.read_bytes() for p in manager.registry.root.glob("v-*.json")}
    review = manager.prune_snapshot(everything=True, recovery_only=True)
    assert set(review["delete"]) == {damaged["id"]}
    kept = {row["id"]: row["reason"] for row in review["kept"]}
    assert "verifies again" in kept[stopped["id"]]
    # The review itself still changes nothing; list is what heals.
    assert {p.name: p.read_bytes() for p in manager.registry.root.glob("v-*.json")} == registry
    assert stopped["id"] in manager.prune_snapshot(everything=True)["delete"]


@pytest.mark.parametrize("days", ["-1", "0", "nan", "inf", "-inf", "soon"])
def test_cli_prune_older_than_requires_a_finite_positive_age(fleet, days, capsys):
    manager, stopped, damaged, _, _ = fleet
    damage(manager, damaged)
    before = tree(manager.home)
    with pytest.raises(SystemExit) as exit_:
        load("cli").main(prune_args(manager, "--older-than=" + days, "--dry-run"))
    assert exit_.value.code == 2
    assert "positive" in capsys.readouterr().err
    assert tree(manager.home) == before


def test_cli_prune_older_than_selects_by_age(fleet, capsys):
    import json
    manager, stopped, damaged, _, _ = fleet
    damage(manager, damaged)
    assert load("cli").main(prune_args(manager, "--older-than", "0.5", "--dry-run")) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["would_delete"] == []
    assert {stopped["id"], damaged["id"]} <= {row["id"] for row in result["kept"]}


def test_partial_prune_failure_reports_what_was_already_deleted(fleet, monkeypatch):
    manager, stopped, damaged, _, _ = fleet
    damage(manager, damaged)
    review = manager.prune_snapshot(everything=True)
    first, second = sorted(review["delete"])
    original = manager._delete_locked

    def failing(record, **kwargs):
        if record["id"] == second:
            raise OSError("disk error")
        return original(record, **kwargs)
    monkeypatch.setattr(manager, "_delete_locked", failing)
    with pytest.raises(load("lifecycle").RealmError) as failed:
        manager.prune(review)
    message = str(failed.value)
    assert "deleted " + first in message and second in message and "disk error" in message
    assert failed.value.deleted == [first]
    assert not manager.registry.path(first).exists()
    assert manager.registry.path(second).exists()


def test_record_without_workspace_directory_can_be_discarded(fleet):
    """A workspace already removed by hand leaves only a record; that is discardable too."""
    import shutil
    manager, stopped, damaged, running, _ = fleet
    damage(manager, damaged)
    shutil.rmtree(damaged["session_dir"])
    assert {row["id"]: row for row in manager.list()}[damaged["id"]]["status"] == "recovery-required"
    snapshot = manager.discard_snapshot(damaged["id"], session_id="damaged-owner")
    assert snapshot["deletion"]["contents"] == []
    # A directory that reappears after review revokes the confirmation.
    Path(damaged["session_dir"]).mkdir(mode=0o700)
    with pytest.raises(load("lifecycle").OwnershipError, match="changed"):
        manager.delete(damaged["id"], session_id="damaged-owner", expected_snapshot=snapshot, discard=True)
    Path(damaged["session_dir"]).rmdir()
    snapshot = manager.discard_snapshot(damaged["id"], session_id="damaged-owner")
    assert manager.delete(damaged["id"], session_id="damaged-owner", expected_snapshot=snapshot, discard=True)
    assert not manager.registry.path(damaged["id"]).exists()
    assert manager.registry.get(running["id"])["status"] == "running"
    assert set(manager.prune_snapshot(everything=True)["delete"]) == {stopped["id"]}


@pytest.mark.parametrize("discard", [False, True])
def test_workspace_swapped_after_final_check_is_not_deleted(fleet, monkeypatch, tmp_path, discard):
    """The last check and the removal see the same directory, or nothing is removed."""
    manager, stopped, damaged, _, _ = fleet
    target = damaged if discard else stopped
    if discard:
        damage(manager, damaged)
    session = Path(target["session_dir"])
    vm = load("vm_manager")
    real_rename = os.rename
    swapped = tmp_path / "swapped-in"

    def swap_then_rename(source, destination):
        # Another program replaces the workspace between verification and removal.
        if Path(source) == session and not swapped.exists() and not (tmp_path / "original").exists():
            real_rename(session, tmp_path / "original")
            swapped.mkdir(mode=0o700)
            (swapped / "unrelated").write_bytes(b"not this workspace")
            real_rename(swapped, session)
        return real_rename(source, destination)
    monkeypatch.setattr(vm.os, "rename", swap_then_rename)
    with pytest.raises(load("lifecycle").OwnershipError, match="replaced"):
        manager.delete(target["id"], discard=discard)
    assert (session / "unrelated").read_bytes() == b"not this workspace"
    assert (tmp_path / "original").exists()
    assert manager.registry.path(target["id"]).exists()


def test_interrupted_removal_of_the_renamed_workspace_finishes_on_retry(fleet, monkeypatch):
    manager, stopped, _, _, _ = fleet
    session = Path(stopped["session_dir"])
    vm = load("vm_manager")
    rmtree = vm.shutil.rmtree

    def interrupted(path):
        if Path(path).parent == session.parent:
            raise OSError("interrupted")
        rmtree(path)
    monkeypatch.setattr(vm.shutil, "rmtree", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        manager.delete(stopped["id"])
    assert not session.exists()
    leftovers = [p.name for p in session.parent.iterdir()]
    assert leftovers and all(name.startswith(".") for name in leftovers if stopped["generation"] in name)
    monkeypatch.setattr(vm.shutil, "rmtree", rmtree)
    # A process still holding a file in the tombstone blocks the retry too.
    tombstone = session.with_name("." + session.name + ".deleting")
    disk = open(tombstone / "disk.qcow2", "rb")
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stdin=disk)
    disk.close()
    try:
        assert holder.pid in load("vm_manager").open_holders(manager.registry.get(stopped["id"]))
    finally:
        holder.kill()
        holder.wait()
    assert manager.delete(stopped["id"])
    assert not any(stopped["generation"] in p.name for p in session.parent.iterdir())
    assert not manager.registry.path(stopped["id"]).exists()
