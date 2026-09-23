"""Selection and recheck must not initialize or repair authority stores."""
import json
import runpy
from pathlib import Path

import pytest
from realms_test_paths import HERMES_ROOT, PLUGIN_ROOT

ROOT = HERMES_ROOT
pytestmark = pytest.mark.linux_only


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("state", ["initial", "current", "missing", "corrupt", "legacy", "symlink"])
def test_cold_selection_reads_store_and_defers_creation(tmp_path, monkeypatch, state):
    from hermes_cli.session_execution import SessionExecutionContext
    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    load = runpy.run_path(str(PLUGIN_ROOT / "realms/_binding.py"))["load_runtime"]
    service = load("integration").get_integration(home)
    owner = service.bind(session_origin="fresh", session_id="parent", task_id="task")
    directory = home / "realms/viewer"
    path = directory / "authority.sqlite"
    if state != "initial":
        authority = load("viewer_state").ControlAuthority(directory)
        if state == "missing":
            path.unlink()
        elif state == "corrupt":
            path.write_bytes(b"not sqlite")
        elif state == "legacy":
            with authority._connection() as db:
                db.execute("DROP TABLE control_epochs")
        elif state == "symlink":
            saved = path.with_suffix(".saved")
            path.rename(saved)
            path.symlink_to(saved)
    before = snapshot(home)
    starts = []
    record = {"id": "r-" + "a" * 24, "generation": "b" * 32, "status": "running", "home": str(home), "session_id": owner}
    def start(o, *, before_start=None):
        if before_start:
            before_start()
        starts.append(o)
        (home / "realms" / (record["id"] + ".json")).write_text(json.dumps(record))
        return record
    monkeypatch.setattr(service.manager, "start", start)
    monkeypatch.setattr(service.manager, "list", lambda: [])
    monkeypatch.setattr(load("integration"), "setup_status", lambda **kw: {"ready": True})
    monkeypatch.setitem(load("target_contexts")._BUILDERS, "realm", lambda *a: SessionExecutionContext())
    try:
        if state not in {"initial", "current"}:
            with pytest.raises(Exception):
                service.select_terminal_target(command="true", session_id="parent", task_id="task")
        else:
            selection = service.select_terminal_target(command="true", session_id="parent", task_id="task")
            selection.check()
        assert snapshot(home) == before
        assert starts == []
        if state in {"initial", "current"}:
            selection.realize().check()
            assert starts == [owner] and path.is_file()
    finally:
        load("target_contexts").release_targets(service, owner)
        load("bridge").close_profile_viewer(home)


@pytest.mark.parametrize("transition", ["disappear", "corrupt"])
def test_cold_recheck_refuses_lost_authority_without_repair(tmp_path, monkeypatch, transition):
    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    load = runpy.run_path(str(PLUGIN_ROOT / "realms/_binding.py"))["load_runtime"]
    service = load("integration").get_integration(home)
    service.bind(session_origin="fresh", session_id="parent", task_id="task")
    authority = load("viewer_state").ControlAuthority(home / "realms/viewer")
    selection = service.select_terminal_target(command="true", session_id="parent", task_id="task")
    if transition == "disappear":
        authority.path.unlink()
    else:
        authority.path.write_bytes(b"corrupt")
    before = snapshot(home)
    with pytest.raises(Exception):
        selection.check()
    assert snapshot(home) == before
