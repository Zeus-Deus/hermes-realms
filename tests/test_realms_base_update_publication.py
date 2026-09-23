"""Real supervised workers and tiny qcow2s; inert install/signature/compute only."""
import json
import os
from pathlib import Path
import runpy

import pytest
from realms_test_paths import HERMES_ROOT

common = runpy.run_path(str(Path(__file__).with_name("test_realms_base_update_flow.py")))
load, profile = common["load"], common["profile"]
wait_for, join_job, BINDING = common["wait_for"], common["join_job"], common["BINDING"]
images = runpy.run_path(str(Path(__file__).with_name("test_realms_vm_base_updates.py")))
pytestmark = pytest.mark.linux_only

# Fresh child imports production worker/manager. Only external install/compute
# observations are replaced. The supervisor, config, progress, selection,
# qcow2 bytes, fsync, job/registry locks and JSON argv are production paths.
INERT = r'''
import sys,runpy,pathlib,contextlib,time,subprocess,os
sys.path.insert(0, sys.argv[1])
load=runpy.run_path(sys.argv[2])["load_runtime"]
vm=load("vm_manager")
home=pathlib.Path(sys.argv[3]); gate=home/"gate"
vm._base_runtime=lambda generation: home/("runtime-"+generation)
vm.require_resources=lambda *a,**kw: None
vm.VmManager._free_port=lambda *a: 2399
vm.VmManager._force_stop=lambda *a: None
load("setup_process").vm_lifetime=lambda *a,**kw: contextlib.nullcontext()
def wait(name):
    deadline=time.monotonic()+15
    while not (gate/name).exists():
        if time.monotonic()>deadline: raise RuntimeError("fixture gate timeout")
        time.sleep(.02)
def install(self,args,*,vm_home,setup_job=None,**kw):
    with (gate/"operations").open("a") as out: out.write(str(args)+"\n")
    if args[0]=="install":
        assert args == ["install", "--version", "4.0.3"]
        assert setup_job[1] is None
        load("setup_flow").installer_progress(home,setup_job[0],None,"verifying",kind="omarchy-vm")
        subprocess.run(["/usr/bin/qemu-img","create","-f","qcow2",str(vm_home/"disk.qcow2"),"1M"],check=True)
        (vm_home/"OVMF_VARS.4m.fd").write_bytes(b"inert-firmware")
        (gate/"installed").write_text(str(os.getpid()))
        wait("install-release")
    elif args == ["stop"]:
        (gate/"stopped").touch()  # explicitly inert compute-retirement observation
    else: raise AssertionError(args)
vm.VmManager._run_script=install
atomic=vm.atomic_json
def publish(path,value):
    atomic(path,value)
    if pathlib.Path(path).name=="current-base.json":
        (gate/"published").touch()
        if (gate/"uncertain").exists(): raise OSError("inert post-replace fsync error")
vm.atomic_json=publish
# The manager has returned but the actual child/supervisor are still alive.
worker=load("setup_worker"); original=worker._install_vm
def retiring(*a,**kw):
    original(*a,**kw)
    (gate/"retiring").touch()
    wait("retire-release")
worker._install_vm=retiring
'''


@pytest.fixture
def update(profile, monkeypatch):
    manager = load("vm_manager").VmManager(profile.home)
    old = manager.base_home()
    images["image"](old, 17)
    load("lifecycle").atomic_json(old / "base.json", {"built_at": 1, "iso": "omarchy-4.0.2.iso"})
    workspace = profile.home / "retained.qcow2"
    images["qemu"]("/usr/bin/qemu-img", "create", "-f", "qcow2", "-b", str(old / "disk.qcow2"), "-F", "qcow2", str(workspace))
    images["qemu"]("/usr/bin/qemu-io", "-f", "qcow2", "-c", "write -P 221 4096 512", str(workspace))
    gate = profile.home / "gate"
    gate.mkdir()
    worker = load("setup_worker")
    monkeypatch.setattr(worker, "require_install_resources", lambda *a: None)
    run = worker.run_child
    def supervised(argv, **kw):
        assert kw["cleanup_unit"].startswith("hermes-vm-base-")
        assert argv[1:3] == ["-I", "-c"]
        argv = list(argv)
        argv[3] = INERT + "\n" + argv[3]
        kw["cleanup_unit"] = None  # No native systemd unit was created by the inert installer.
        return run(argv, **kw)
    monkeypatch.setattr(worker, "run_child", supervised)
    return profile, manager, old, workspace, gate


@pytest.mark.parametrize("winner", ["cancel", "commit", "uncertain", "config-drift", "base-drift"])
def test_exact_worker_publication_cancel_boundary_retains_old_chain(update, monkeypatch, winner):
    profile, manager, old, workspace, gate = update
    flow = load("setup_flow")
    retained = old.joinpath("disk.qcow2").read_bytes(), workspace.read_bytes()
    proposal = flow.prepare_base_update(profile, "4.0.3", BINDING)
    assert "https://iso.omarchy.org/omarchy-4.0.3.iso" in " ".join(proposal["details"])
    job = flow.start_base_update(profile, "4.0.3", BINDING, proposal["consent"])
    try:
        wait_for((gate / "installed").exists)
        if winner == "cancel":
            assert flow.cancel_base_update(profile, job["id"])["state"] in {"cancelling", "cancelled"}
        elif winner == "config-drift":
            (profile.home / "config.yaml").write_text("plugins:\n  realms:\n    vm:\n      memory: 8192\n")
        elif winner == "base-drift":
            load("lifecycle").atomic_json(old / "base.json", {"built_at": 2, "iso": "omarchy-4.0.2.iso"})
        elif winner == "uncertain":
            (gate / "uncertain").touch()
        (gate / "install-release").touch()
        if winner == "commit":
            wait_for((gate / "retiring").exists)
            current = flow.cancel_base_update(profile, job["id"])
            assert current["state"] == "running" and current["base_commit_started"]
            assert not current["cancellable"]
            assert not load("setup_process").cancellation_requested(flow._path(profile, job["id"]))
            with pytest.raises(ValueError, match="active"):
                flow._lock(profile)
            (gate / "retire-release").touch()
    finally:
        (gate / "install-release").touch()
        (gate / "retire-release").touch()
        join_job(job)
    final = flow.status_base_update(profile, job["id"])
    assert not Path(f"/proc/{int((gate / 'installed').read_text())}").exists()
    if winner == "commit":
        assert final["state"] == "succeeded"
        assert manager.base_home() != old
        assert manager.base_status()["iso"] == "omarchy-4.0.3.iso"
    elif winner == "uncertain":
        assert final["state"] == "failed" and final["error"] == "publication_uncertain"
        assert "uncertain" in final["message"].lower()
        assert manager.base_home() != old
        assert flow.cancel_base_update(profile, job["id"]) == final
        # Cold interrupted read must not relabel a selected base cancelled.
        row = flow._read(profile, job["id"])
        row["state"] = "cancelling"
        flow.atomic_json(flow._path(profile, job["id"]), row)
        assert flow.status_base_update(profile, job["id"])["error"] == "publication_uncertain"
    else:
        assert final["state"] == ("cancelled" if winner == "cancel" else "failed")
        assert manager.base_home() == old
        assert not (gate / "published").exists()
    assert old.joinpath("disk.qcow2").read_bytes() == retained[0]
    assert workspace.read_bytes() == retained[1]
    images["verify_image"](workspace, old, 17, dirty=True)
    assert not (profile.home / "realms/sessions.sqlite3").exists()


def test_reviewed_selection_is_checked_at_manager_entry(update):
    profile, manager, old, _, gate = update
    selection = load("vm_base").selection_receipt(profile.home)
    load("lifecycle").atomic_json(old / "base.json", {"built_at": 2})
    # Entry must refuse before resource admission, runtime allocation or install.
    with pytest.raises(load("vm_manager").VmError, match="selection"):
        manager.install_base(update=True, release="4.0.3", expected_selection=selection)
    assert not (gate / "operations").exists()
    assert not list(manager.data.glob(".install-*"))


def test_backend_death_after_commit_is_uncertain_not_cancelled(update):
    import subprocess
    import sys
    profile, manager, old, _, gate = update
    flow = load("setup_flow")
    binding_path = Path(load("setup_flow").__file__).with_name("_binding.py")
    code = r"""
import json,pathlib,runpy,sys,time
from types import SimpleNamespace
sys.path.insert(0,sys.argv[1])
load=runpy.run_path(sys.argv[2])["load_runtime"]
home=pathlib.Path(sys.argv[3]); flow=load("setup_flow"); worker=load("setup_worker")
plan=load("setup_plan"); original=plan._prerequisite_plan
def prerequisites(home,kind):
    result=original(home,kind); result.update(packages=[],blockers=[]); return result
plan._prerequisite_plan=prerequisites
flow.require_install_resources=lambda *a: None
worker.require_install_resources=lambda *a: None
run=worker.run_child
def supervised(argv,**kw):
    argv=list(argv); argv[3]=sys.argv[4]+"\n"+argv[3]; kw["cleanup_unit"]=None
    return run(argv,**kw)
worker.run_child=supervised
service=SimpleNamespace(home=home); binding=json.loads(sys.argv[5])
review=flow.prepare_base_update(service,"4.0.3",binding)
job=flow.start_base_update(service,"4.0.3",binding,review["consent"])
(home/"backend-job").write_text(job["id"])
while True: time.sleep(1)
"""
    process = subprocess.Popen([sys.executable, "-I", "-c", code, str(HERMES_ROOT),
                                str(binding_path), str(profile.home), INERT, json.dumps(BINDING)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               env={"PATH": "/usr/bin:/bin", "HOME": str(profile.home.parent), "HERMES_HOME": str(profile.home)})
    job = None
    try:
        wait_for((gate / "installed").exists)
        job = {"id": (profile.home / "backend-job").read_text()}
        (gate / "install-release").touch()
        wait_for((gate / "retiring").exists)
        process.kill()
        process.wait(timeout=5)
        wait_for(lambda: not Path(f"/proc/{int((gate / 'installed').read_text())}").exists())
        result = wait_for(lambda: (v if (v := flow.status_base_update(profile, job["id"]))["state"] == "failed" else None))
        assert result["error"] == "publication_uncertain"
        assert flow.cancel_base_update(profile, job["id"])["error"] == "publication_uncertain"
        assert manager.base_home() != old and old.joinpath("disk.qcow2").exists()
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        (gate / "install-release").touch()
        (gate / "retire-release").touch()


def test_unknown_selection_is_not_reported_unchanged(profile):
    flow = load("setup_flow")
    fd = flow._lock(profile)
    plan = load("setup_plan").build_base_update_plan(profile.home, "4.0.3", BINDING)
    row = {"id": "f" * 32, "home": str(profile.home), "owner": None,
           "operation": "vm-base-update", "scope": "profile", "kind": "omarchy-vm",
           "state": "cancelling", "message": "cancel", "base_generation": "a" * 32,
           "plan": plan, "created_at": 1}
    flow.atomic_json(flow._path(profile, row["id"]), row)
    data = load("config").vm_data_path(profile.home)
    data.mkdir(parents=True)
    (data / "current-base.json").write_text("corrupt selection")
    flow._release(fd)
    final = flow.status_base_update(profile, row["id"])
    assert final["state"] == "failed" and final["error"] == "publication_uncertain"

