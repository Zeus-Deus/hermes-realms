"""Install the real wheel into a clean disposable interpreter."""

from pathlib import Path
import subprocess

import pytest


@pytest.mark.e2e
def test_installable_cli_and_viewer_assets(tmp_path):
    repo = Path(__file__).parents[1]
    venv = tmp_path / "venv"
    subprocess.run(
        ["uv", "venv", str(venv), "--python", "3.11"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["uv", "pip", "install", "--python", str(venv / "bin/python"), str(repo)],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    result = subprocess.run(
        [
            str(venv / "bin/python"),
            "-I",
            "-c",
            "from realms.bridge import ViewerServer; from pathlib import Path; import realms.bridge; assert (Path(realms.bridge.__file__).parent/'web/viewer.html').is_file(); print('installed-ok')",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    import json

    command = [str(venv / "bin/hermes-realm"), "--home", str(tmp_path / "profile")]
    diagnosed = subprocess.run(
        [*command, "doctor"], cwd=tmp_path, capture_output=True, text=True
    )
    assert diagnosed.returncode == 0, diagnosed.stderr
    started = subprocess.run(
        [*command, "start", "installed-cli"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=40,
    )
    assert started.returncode == 0, started.stderr
    record = json.loads(started.stdout)
    stopped = subprocess.run(
        [*command, "stop", record["id"]],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert stopped.returncode == 0, stopped.stderr
    assert not Path(record["runtime_dir"]).exists()
