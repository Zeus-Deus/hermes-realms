"""Real vendor shell/manager transport; inert downloads, GPG and compute, no VM boot."""
from contextlib import nullcontext
from dataclasses import asdict
import json
from pathlib import Path
import runpy
import subprocess
import uuid

import pytest
from realms_test_paths import HERMES_ROOT, PLUGIN_ROOT

ROOT = HERMES_ROOT
load = runpy.run_path(str(PLUGIN_ROOT / "realms/_binding.py"))["load_runtime"]
base_tests = runpy.run_path(str(Path(__file__).with_name("test_realms_vm_base_updates.py")))
pytestmark = pytest.mark.linux_only
A, B = "4.0.2", "4.0.3"

# Keep fetch_iso/verify_iso/cmd_install intact. Only external operations are inert.
SHELL = r'''
source "$1" --help >/dev/null
shift
vm_running() { printf 'compute-check\n' >> "$HOME/effects"; return 1; }
pacman() { printf 'package-check\n' >> "$HOME/effects"; }
openssl() { printf 'inert-not-a-password'; }
latest_iso_version() { printf 'latest\n' >> "$HOME/effects"; printf '4.0.3'; }
curl() {
  local out="" url="${!#}"
  printf 'download %s\n' "$url" >> "$HOME/effects"
  while (($#)); do
    if [[ $1 == -o ]]; then out=$2; shift; fi
    shift
  done
  [[ -n $out ]] || return 99
  printf 'inert download %s' "$url" > "$out"
}
gpg() {
  printf 'verify %s\n' "$*" >> "$HOME/effects"
  if [[ -f "$HOME/bad-signature" ]]; then
    printf '[GNUPG:] VALIDSIG 0000000000000000000000000000000000000000\n'
  else
    printf '[GNUPG:] VALIDSIG 40DFB630FF42BCFFB047046CF0134EE680CAC571\n'
  fi
}
build_cidata() { printf 'prepared\n' >> "$HOME/effects"; printf inert > "$CIDATA"; }
qemu-img() { printf inert-disk > "$DISK"; }
cp() { printf inert-firmware > "$OVMF_VARS"; }
start_qemu() { printf 'boot %s\n' "$*" >> "$HOME/effects"; }
wait_for_ssh_as_guest_user() { return 0; }
cmd_provision() { return 0; }
cmd_install "$@"
'''


@pytest.fixture
def rig(tmp_path, monkeypatch):
    vm = load("vm_manager")
    manager = vm.VmManager(tmp_path / "profile")
    home = tmp_path / "host"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh/id_ed25519.pub").write_text("inert-not-a-key")
    monkeypatch.setattr(vm, "_base_runtime", lambda gen: tmp_path / ("b-" + gen))
    monkeypatch.setattr(vm, "_generation_paths", lambda uid, gen: tmp_path / ("r-" + gen))
    monkeypatch.setattr(vm, "require_resources", lambda *a, **kw: None)
    monkeypatch.setattr(manager, "_free_port", lambda taken: 2399)
    monkeypatch.setattr(manager, "_force_stop", lambda unit: None)
    monkeypatch.setattr(load("setup_process"), "vm_lifetime", lambda *a, **kw: nullcontext())
    run = subprocess.run
    def inert(argv, **kw):
        if argv[0] == str(vm.VENDORED_SCRIPT):
            kw["env"]["HOME"] = str(home)
            if argv[1] == "install":
                argv = ["/bin/bash", "-c", SHELL, "fixture", argv[0], *argv[2:]]
            else:
                assert argv[1:] == ["stop"]
                argv = ["/bin/true"]
        return run(argv, **kw)
    monkeypatch.setattr(vm.subprocess, "run", inert)
    return manager, home


@pytest.mark.parametrize("route,cached", [("vendor", False), ("manager", False), ("manager", True), ("manager-update", False), ("worker", False), ("default", False)])
def test_selected_signed_iso_reaches_receipt_without_latest_lookup(rig, monkeypatch, route, cached):
    manager, home = rig
    vm = load("vm_manager")
    iso_dir = manager.data / "iso"
    iso_dir.mkdir(parents=True, exist_ok=True)
    # A newer cached filename must not replace the actual selection in metadata.
    (iso_dir / f"omarchy-{B}.iso").write_text("inert newer ISO")
    (iso_dir / f"omarchy-{B}.iso.sig").write_text("inert signature")
    if cached:
        (iso_dir / f"omarchy-{A}.iso").write_text("inert selected ISO")
        (iso_dir / f"omarchy-{A}.iso.sig").write_text("inert signature")
    release = B if route == "default" else A
    if route == "vendor":
        manager._run_script(["install", "--version", A], vm_home=manager.base_home())
    elif route == "worker":
        monkeypatch.setattr(vm, "VmManager", lambda h: manager)
        load("setup_worker")._install_vm(manager.home, asdict(manager.config), uuid.uuid4().hex, release=A)
    elif route == "default":
        manager.install_base()
    else:
        manager.install_base(release=A, update=route == "manager-update")
    result = manager.base_status()
    assert (manager.base_home() / "disk.qcow2").is_file()
    if route != "vendor":
        assert result["present"]
        assert result["iso"] == f"omarchy-{release}.iso"
        assert vm._iso_version(result["iso"]) == release
    effects = (home / "effects").read_text().splitlines()
    assert effects.count("latest") == (1 if route == "default" else 0)
    downloads = [e for e in effects if e.startswith("download ")]
    assert downloads == ([] if cached or route == "default" else [
        f"download https://iso.omarchy.org/omarchy-{A}.iso",
        f"download https://iso.omarchy.org/omarchy-{A}.iso.sig",
    ])
    verification = [e for e in effects if e.startswith("verify ")]
    assert verification == [f"verify --homedir /etc/pacman.d/gnupg --status-fd 1 --verify {iso_dir}/omarchy-{release}.iso.sig {iso_dir}/omarchy-{release}.iso"]
    boot = next(e for e in effects if e.startswith("boot "))
    assert f"file={iso_dir}/omarchy-{release}.iso,media=cdrom" in boot
    assert effects.index(verification[0]) < effects.index("prepared") < effects.index(boot)
    if route != "vendor":
        assert json.loads((manager.base_home() / "base.json").read_text())["iso"] == result["iso"]


INVALID = ["", "v4.0.2", "../4.0.2", "https://iso.omarchy.org/4.0.2", "--help",
           "4.0.2\n", " 4.0.2", "4.0.2/../4.0.3", "4.0.2;true", "04.0.2", "4.0", "4.0.2-rc1"]


@pytest.mark.parametrize("route", ["manager", "vendor"])
@pytest.mark.parametrize("release", INVALID)
def test_invalid_release_refuses_before_filesystem_or_external_operations(rig, route, release):
    manager, home = rig
    before = {p: (p.stat().st_ino, p.read_bytes()) for p in manager.home.rglob("*") if p.is_file()}
    dirs = set(manager.home.rglob("*"))
    if route == "manager":
        with pytest.raises(load("vm_manager").VmError, match="invalid.*release"):
            manager.install_base(release=release)
    else:
        result = manager._run_script(["install", "--version", release], vm_home=manager.base_home(), check=False)
        assert result.returncode != 0 and "invalid" in result.stderr.lower(), result.stderr
    assert not (home / "effects").exists()
    assert set(manager.home.rglob("*")) == dirs
    assert {p: (p.stat().st_ino, p.read_bytes()) for p in before} == before


@pytest.mark.parametrize("case", ["type", "custom", "iso", "empty-iso", "vendor-iso", "vendor-empty-iso", "vendor-duplicate"])
def test_explicit_pin_cannot_fall_back_to_an_unverifiable_contract(rig, case):
    from dataclasses import replace
    manager, home = rig
    before = set(manager.home.rglob("*"))
    if case.startswith("vendor"):
        args = ["--version", A, *( ["--version", B] if case == "vendor-duplicate" else
                                  ["--iso", "" if case == "vendor-empty-iso" else str(home / "local.iso")])]
        result = manager._run_script(["install", *args], vm_home=manager.base_home(), check=False)
        assert result.returncode != 0
    else:
        if case == "custom":
            manager.config = replace(manager.config, vm=replace(manager.config.vm, omarchy_vm_path="/bin/true"))
        options = {"iso": "" if case == "empty-iso" else home / "local.iso"} if "iso" in case else {}
        with pytest.raises(load("vm_manager").VmError, match="release|version|vendored"):
            manager.install_base(release=123 if case == "type" else A, **options)
    assert not (home / "effects").exists()
    assert set(manager.home.rglob("*")) == before


@pytest.mark.parametrize("cached", [False, True])
def test_selected_signature_failure_preserves_current_base_and_dirty_work(rig, cached):
    manager, home = rig
    old = manager.base_home()
    base_tests["image"](old, 17)
    load("lifecycle").atomic_json(old / "base.json", {"built_at": 1, "generation": uuid.uuid4().hex})
    record = base_tests["workspace"](manager, old, "stopped")
    disk = Path(record["session_dir"]) / "disk.qcow2"
    base_tests["qemu"]("/usr/bin/qemu-io", "-f", "qcow2", "-c", "write -P 221 4096 512", str(disk))
    before = {p: (p.stat().st_ino, p.read_bytes()) for p in [*old.iterdir(), disk]}
    records = manager.registry.records()
    iso_dir = manager.data / "iso"
    iso_dir.mkdir(exist_ok=True)
    if cached:
        (iso_dir / f"omarchy-{A}.iso").write_text("inert ISO")
        (iso_dir / f"omarchy-{A}.iso.sig").write_text("inert signature")
    (home / "bad-signature").touch()
    with pytest.raises(load("vm_manager").VmError, match="install failed"):
        manager.install_base(update=True, release=A)
    effects = (home / "effects").read_text()
    assert "latest" not in effects and "prepared" not in effects and "boot " not in effects
    assert f"omarchy-{A}.iso.sig {iso_dir}/omarchy-{A}.iso" in effects
    assert ("download " in effects) is not cached
    assert not (iso_dir / f"omarchy-{A}.iso").exists()
    assert manager.base_home() == old and manager.base_status()["present"]
    assert not (manager.data / "current-base.json").exists()
    assert manager.registry.records() == records
    assert {p: (p.stat().st_ino, p.read_bytes()) for p in before} == before
    load("vm_workspace").validate(record)
    base_tests["verify_image"](disk, old, 17, dirty=True)
