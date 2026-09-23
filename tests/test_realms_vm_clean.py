"""Unregistered VM work is recoverable data, not an expendable cache."""
from pathlib import Path
import json
import runpy

import pytest
from realms_test_paths import HERMES_ROOT, PLUGIN_ROOT

ROOT = HERMES_ROOT


@pytest.mark.linux_only
def test_clean_preserves_unregistered_workspace_and_reports_it(tmp_path, monkeypatch, capsys):
    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    load = runpy.run_path(str(PLUGIN_ROOT / "realms/_binding.py"))["load_runtime"]
    manager = load("vm_manager").VmManager(home)
    workspace = manager.registry.root / "vm" / "unregistered-work"
    workspace.mkdir(parents=True)
    sentinel = workspace / "source.bin"
    payload = b"recoverable source\x00\xff\n"
    sentinel.write_bytes(payload)
    stale_iso = manager.data / "iso" / "omarchy-stale.iso"
    stale_iso.parent.mkdir(parents=True)
    stale_iso.write_bytes(b"disposable download fixture")
    assert load("cli").main(["--home", str(home), "vm", "clean"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert sentinel.is_file(), "Clean deleted unregistered work"
    assert sentinel.read_bytes() == payload
    assert str(workspace) not in result["removed"]
    assert str(workspace) in result["preserved_workspaces"]
    assert not stale_iso.exists()
    assert str(stale_iso) in result["removed"]
