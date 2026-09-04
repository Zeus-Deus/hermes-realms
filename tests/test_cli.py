import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from realms.manager import Manager

pytestmark = pytest.mark.e2e


def invoke(home, *args):
    return subprocess.run(
        [sys.executable, "-m", "cli", *args],
        cwd=Path(__file__).parents[1],
        env=dict(os.environ, HERMES_HOME=str(home)),
        capture_output=True,
        text=True,
        timeout=45,
    )


def test_cli_lifecycle_is_persistent_across_processes(tmp_path):
    result = invoke(tmp_path, "start", "cli-session")
    assert result.returncode == 0, result.stderr
    r = json.loads(result.stdout)
    try:
        assert json.loads(invoke(tmp_path, "list").stdout)[0]["id"] == r["id"]
        e = json.loads(invoke(tmp_path, "env", r["id"]).stdout)
        assert e["XDG_RUNTIME_DIR"] == r["runtime_dir"]
        assert "HYPRLAND_INSTANCE_SIGNATURE" not in e
        assert invoke(tmp_path, "stop", r["id"]).returncode == 0
        assert json.loads(invoke(tmp_path, "list").stdout) == []
        assert not Path(r["runtime_dir"]).exists()
    finally:
        Manager(tmp_path).stop(r["id"])


def test_cli_doctor_resize_capture_and_real_exec_exit_status(tmp_path):
    m = Manager(tmp_path)
    r = m.start("cli-operations")
    try:
        result = invoke(tmp_path, "doctor", r["id"])
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["sleep_only_inhibitor"] is True
        result = invoke(tmp_path, "resize", r["id"], "640x480")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["size"] == "640x480"
        target = tmp_path / "cli.png"
        result = invoke(tmp_path, "shot", r["id"], str(target))
        assert result.returncode == 0, result.stderr
        assert target.read_bytes().startswith(b"\x89PNG")
        result = invoke(
            tmp_path,
            "exec",
            r["id"],
            "--",
            sys.executable,
            "-c",
            "import sys; print('cli-real'); sys.exit(19)",
        )
        assert result.returncode == 19, result.stderr
        assert result.stdout.strip() == "cli-real"
        invalid = invoke(tmp_path, "env", "../../foreign")
        assert invalid.returncode == 1
        assert "invalid realm id" in json.loads(invalid.stderr)["error"]
    finally:
        m.stop(r["id"])
