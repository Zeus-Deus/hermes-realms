"""R7: real guardian, deterministic decision/cleanup barrier."""

from pathlib import Path
import subprocess
import time

import pytest

from realms import manager as manager_module
from realms.lifecycle import RealmError, alive
from realms.manager import Manager


@pytest.mark.parametrize("renewal", ["env", "reuse"])
def test_idle_commit_rejects_renewal_before_cleanup(tmp_path, monkeypatch, renewal):
    (tmp_path / "config.yaml").write_text("plugins:\n  realms:\n    idle_ttl: 1\n")
    decided = tmp_path / "decided"
    release = tmp_path / "release"
    original_popen = subprocess.Popen
    code = f"""import sys, time
from pathlib import Path
from realms import supervisor
original = supervisor.cleanup
def pause(home, realm_id):
    Path({str(decided)!r}).touch()
    deadline = time.monotonic() + 12
    while not Path({str(release)!r}).exists() and time.monotonic() < deadline:
        time.sleep(.01)
    original(home, realm_id)
supervisor.cleanup = pause
supervisor.run(sys.argv[1], sys.argv[2])
"""

    def instrument_guardian(command, **kwargs):
        command = list(command)
        if "realms.supervisor" in command:
            at = command.index("realms.supervisor")
            command[at - 1 : at + 1] = ["-c", code]
        return original_popen(command, **kwargs)

    manager = Manager(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(manager_module.subprocess, "Popen", instrument_guardian)
        record = manager.start("idle-race")
    try:
        deadline = time.monotonic() + 8
        while not decided.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert decided.exists(), "guardian did not reach cleanup barrier"
        assert alive(record["processes"]["worker"]), (
            "barrier must precede physical teardown"
        )
        with pytest.raises(RealmError, match="(not running|stopping)"):
            manager.env(record["id"]) if renewal == "env" else manager.start(
                "idle-race"
            )
        assert manager.registry.get(record["id"])["status"] == "stopping"
    finally:
        release.touch()
        manager.stop(record["id"])
    assert not Path(record["runtime_dir"]).exists()
