"""Driver setup diagnostics must not opt into vendor telemetry."""
import json
from pathlib import Path
import runpy
import sys

import pytest
from realms_test_paths import PLUGIN_ROOT


@pytest.mark.linux_only
@pytest.mark.parametrize("inherited", [None, "1"])
def test_install_verification_disables_telemetry(tmp_path, monkeypatch, inherited):
    if inherited is None:
        monkeypatch.delenv("CUA_DRIVER_RS_TELEMETRY_ENABLED", raising=False)
    else:
        monkeypatch.setenv("CUA_DRIVER_RS_TELEMETRY_ENABLED", inherited)
    module = runpy.run_path(str(PLUGIN_ROOT / "realms/install_driver.py"))
    observed = tmp_path / "observed.json"
    binary = tmp_path / "driver"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib\n"
        f"pathlib.Path({str(observed)!r}).write_text(json.dumps(os.getenv('CUA_DRIVER_RS_TELEMETRY_ENABLED')))\n"
        f"print('cua-driver {module['VERSION']}')\n"
    )
    binary.chmod(0o700)
    module["verify_execution"](binary)
    assert json.loads(observed.read_text()) == "0"
