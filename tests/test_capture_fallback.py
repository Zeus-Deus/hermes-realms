"""Actual own-session grim fallback, through native plugin discovery/dispatch."""

import hashlib
import json
from pathlib import Path
import stat
import struct

import pytest

from test_integration import install_plugin


def test_shot_tool_captures_only_own_realm_into_private_profile(tmp_path, monkeypatch):
    from hermes_cli.plugins import discover_plugins, unload_plugins
    from realms.integration import get_integration
    from tools.registry import registry

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    install_plugin(tmp_path)
    discover_plugins()
    service = get_integration(tmp_path)
    a = service.manager.start("capture-a")
    b = service.manager.start("capture-b")
    try:
        service.bind(session_id="capture-a", task_id="task-a")
        service.bind(session_id="capture-b", task_id="task-b")
        service.manager.resize(a["id"], "800x600")
        service.manager.resize(b["id"], "1024x768")
        result = json.loads(
            registry.dispatch(
                "realm", {"action": "shot"}, session_id="capture-a", task_id="task-a"
            )
        )
        assert "path" in result, result
        target = Path(result["path"])
        data = target.read_bytes()
        assert target.is_relative_to(tmp_path / "realms")
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
        assert data.startswith(b"\x89PNG\r\n\x1a\n")
        assert struct.unpack(">II", data[16:24]) == (800, 600)
        assert result == dict(
            realm_id=a["id"],
            path=str(target),
            mime_type="image/png",
            width=800,
            height=600,
            bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            capture="grim",
            fallback=True,
        )
        assert (
            "shot"
            in registry.get_entry("realm").schema["parameters"]["properties"]["action"][
                "enum"
            ]
        )
        again = json.loads(
            registry.dispatch("realm", {"action": "shot"}, session_id="capture-a")
        )
        assert again["path"] != result["path"] and target.read_bytes() == data
        print("SHOT_API_RECEIPT=" + json.dumps(result, sort_keys=True))
        import os

        if evidence := os.environ.get("REALMS_TEST_CAPTURE_EVIDENCE"):
            directory = Path(evidence)
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            (directory / "realm-shot.png").write_bytes(data)
            (directory / "realm-shot-api.json").write_text(
                json.dumps(result, indent=2) + "\n"
            )
    finally:
        service.manager.stop(a["id"])
        service.manager.stop(b["id"])
        unload_plugins()
        assert service.manager.list() == []
        assert (
            not Path(a["runtime_dir"]).exists() and not Path(b["runtime_dir"]).exists()
        )


def test_shot_fails_closed_for_missing_conflicting_or_invalid_realm(
    tmp_path, monkeypatch
):
    from realms.integration import RealmIntegration, OwnerError
    from realms.lifecycle import OwnershipError

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    service = RealmIntegration(tmp_path)
    service.bind(session_id="a", task_id="task-a")
    service.bind(session_id="b", task_id="task-b")
    with pytest.raises(ValueError, match="not running"):
        service.command("shot", session_id="a")
    assert service.manager.list() == []
    record = service.manager.start("a")
    ready = Path(record["runtime_dir"]) / "ready.json"
    original = ready.read_bytes()
    try:
        with pytest.raises(OwnerError):
            service.command("shot", session_id="a", task_id="task-b")
        with pytest.raises(OwnerError):
            service.command("shot", session_id="a", hermes_home=tmp_path / "other")
        with pytest.raises(ValueError, match="own-session"):
            service.command("shot " + record["id"], session_id="b")
        with pytest.raises(ValueError, match="not running"):
            service.command("shot", session_id="b")
        data = json.loads(original)
        data["env"]["WAYLAND_DISPLAY"] = "invalid-not-a-host-target"
        ready.write_text(json.dumps(data))
        with pytest.raises(OwnershipError):
            service.command("shot", session_id="a")
        assert list(service.manager.registry.root.glob("shot-*")) == []
    finally:
        ready.write_bytes(original)
        service.manager.stop(record["id"])
        service.unload()
        assert service.manager.list() == []
        assert not Path(record["runtime_dir"]).exists()
