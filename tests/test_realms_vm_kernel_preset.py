"""CIDATA leaves kernel selection to the chosen stock installer's defaults."""

import json
import os
from pathlib import Path
import subprocess

import pytest
from realms_test_paths import HERMES_ROOT, PLUGIN_ROOT


ROOT = HERMES_ROOT
VENDOR = PLUGIN_ROOT / "realms/vendor/omarchy-vm"


def generate_cidata(tmp_path, disk_size="40G", vendor=VENDOR):
    """Run the real generator; replace only host queries and image/crypto I/O."""
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh/id_ed25519.pub").write_text("synthetic-public-key\n")
    vm = tmp_path / "vm"
    vm.mkdir()
    (vm / "credentials").write_text("synthetic-password\n")
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    output = tmp_path / "cidata"
    output.mkdir()
    # As in the vendor dispatcher tests, load definitions without dispatching
    # install/launch. build_cidata itself is executed, not a copied JSON preset.
    definitions = tmp_path / "definitions.sh"
    definitions.write_text(vendor.read_text().split('\ncommand="${1:-}"', 1)[0])
    result = subprocess.run(
        [
            "bash",
            "-c",
            """
source "$1"
openssl() { printf '%s\n' synthetic-hash; }
timedatectl() { printf '%s\n' UTC; }
git() { return 1; }
truncate() { :; }
mformat() { :; }
mcopy() { shift 2; for file; do [[ $file == ::/ ]] || cp "$file" "$OUTPUT/"; done; }
build_cidata
""",
            "cidata-probe",
            str(definitions),
        ],
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(home),
            "USER": "probe",
            "TMPDIR": str(scratch),
            "OUTPUT": str(output),
            "OMARCHY_VM_HOME": str(vm),
            "OMARCHY_VM_DISK_SIZE": disk_size,
            "OMARCHY_VM_HOSTNAME": "synthetic-guest",
            "OMARCHY_VM_QMP_SOCKET": str(tmp_path / "qmp.sock"),
            "OMARCHY_VM_VNC_SOCKET": str(tmp_path / "vnc.sock"),
        },
        capture_output=True,
        text=True,
        timeout=30,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    return {path.name: path.read_text() for path in output.iterdir()}


@pytest.mark.linux_only
@pytest.mark.parametrize("disk_size", ["40G", "64G"])
def test_cidata_defers_both_kernel_choices_without_changing_vm_intent(
    tmp_path, disk_size
):
    files = generate_cidata(tmp_path, disk_size)
    config = json.loads(files["user_configuration.json"])
    install = config["omarchy_install"]
    # Both selectors override the media default; removing only one is unsafe.
    assert (config.get("kernels"), install.get("storage", {}).get("kernel")) == (
        None,
        None,
    )
    assert install["mode"] == "full_disk"
    assert install["defer_provisioning"] is False
    assert install["target_mount"] == "/mnt"
    assert install["boot"]["enable_fallback"] is True
    disk = config["disk_config"]
    assert disk["config_type"] == "default_layout"
    assert "disk_encryption" not in disk
    assert "disk_encryption" not in config
    (device,) = disk["device_modifications"]
    assert device["device"] == "/dev/vda" and device["wipe"] is True
    boot, root = device["partitions"]
    assert boot["fs_type"] == "fat32" and boot["mountpoint"] == "/boot"
    assert root["fs_type"] == "btrfs"
    assert root["start"]["value"] == boot["start"]["value"] + boot["size"]["value"]
    assert (
        root["start"]["value"] + root["size"]["value"]
        == int(disk_size[:-1]) * 1024**3 - 1024**2
    )
    assert {entry["name"] for entry in root["btrfs"]} == {"@", "@home", "@log", "@pkg"}
    assert files["user_encrypt_installation.txt"] == "false\n"
    assert files["authorized_keys"] == "synthetic-public-key\n"
    assert config["hostname"] == "synthetic-guest"
    assert config["network_config"] == {"type": "iso"}
    credentials = json.loads(files["user_credentials.json"])
    assert credentials["users"][0]["username"] == "probe"
    assert credentials["users"][0]["sudo"] is True
