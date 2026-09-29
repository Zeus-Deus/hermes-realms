"""A Realms runtime that cannot load pauses only conversations that use a Realm."""
import importlib.abc
import json
from pathlib import Path
import runpy
import sys

import pytest
from realms_test_paths import PLUGIN_ROOT

load = runpy.run_path(str(PLUGIN_ROOT / "realms/_binding.py"))["load_runtime"]
integration = load("integration")
pytestmark = pytest.mark.platforms("linux")


class _NoYaml(importlib.abc.MetaPathFinder):
    """Reproduce a backend whose environment lacks the plugin's PyYAML."""

    def find_spec(self, name, path=None, target=None):
        if name == "yaml" or name.startswith("yaml."):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return None


def _profile(tmp_path, monkeypatch, *, store=None):
    home = tmp_path / "profile"
    (home / "plugins").mkdir(parents=True)
    (home / "plugins/hermes-realms").symlink_to(PLUGIN_ROOT, target_is_directory=True)
    (home / "config.yaml").write_text("plugins:\n  enabled: [hermes-realms]\napprovals:\n  mode: off\n")
    if store is not None:
        store(home)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def _realm_sessions(home):
    owners = integration.OwnershipStore(home)
    for session in ("plain", "in-realm", "has-record", "off-with-kind"):
        owners.bind(session_id=session, session_origin="fresh")
    owners.bind(session_id="in-realm", task_id="in-realm-subagent")
    owners.set_mode("in-realm", "realm")
    owners.set_kind("in-realm", "realm")
    owners.set_kind("off-with-kind", "omarchy-vm")
    owners.set_mode("off-with-kind", "host")
    record = {"id": "r-" + "a" * 24, "home": str(home.resolve()), "session_id": "has-record", "status": "running"}
    (home / "realms" / (record["id"] + ".json")).write_text(json.dumps(record))


def _discover(monkeypatch):
    from hermes_cli.plugins import discover_plugins, get_plugin_manager
    monkeypatch.delitem(sys.modules, "yaml", raising=False)
    monkeypatch.setattr(sys, "meta_path", [_NoYaml(), *sys.meta_path])
    discover_plugins(force=True)
    manager = get_plugin_manager()
    assert "hermes-realms" in manager._plugins
    return manager


def _probe(effects):
    from tools.registry import registry
    registry.register(name="load_failure_probe", toolset="test",
                      schema={"name": "load_failure_probe", "parameters": {"type": "object"}},
                      handler=lambda args, **kw: effects.append(kw.get("task_id")) or json.dumps({"executed": True}))


def test_missing_dependency_pauses_only_realm_conversations_and_names_the_cause(tmp_path, monkeypatch):
    import model_tools
    from hermes_cli.plugins import get_plugin_command_handler
    home = _profile(tmp_path, monkeypatch, store=_realm_sessions)
    manager = _discover(monkeypatch)
    assert manager.has_middleware("tool_execution")
    effects = []
    _probe(effects)

    def call(name, args=None, **identity):
        return json.loads(model_tools.handle_function_call(name, args or {}, **identity))

    # A conversation with no Realm state, and one Hermes has never seen, keep working.
    assert call("load_failure_probe", session_id="plain") == {"executed": True}
    assert call("load_failure_probe", session_id="never-seen") == {"executed": True}
    assert call("terminal", {"command": "true"}, session_id="plain").get("error_code") != "realms_load_failed"
    assert len(effects) == 2

    # Conversations recorded as using a Realm fail closed with the real cause.
    for identity in ({"session_id": "in-realm"}, {"session_id": "other", "task_id": "in-realm-subagent"},
                     {"session_id": "has-record"}, {"session_id": "off-with-kind"}):
        for name, args in (("load_failure_probe", {}), ("terminal", {"command": "true"}), ("computer_use", {})):
            result = call(name, args, **identity)
            assert result["error_code"] == "realms_load_failed", (identity, name, result)
            assert "No module named 'yaml'" in result["error"]
            assert "hermes pm repair" in result["error"]
            assert "permission storage" not in result["error"]
    assert len(effects) == 2

    # The realm tool and every /realm command answer with the cause instead of a storage error.
    command = get_plugin_command_handler("realm")
    for raw in ("status", "off", "review", "on"):
        for session in ("plain", "in-realm"):
            result = json.loads(command(raw, session_id=session))
            assert result["error_code"] == "realms_load_failed", (raw, session, result)
            assert "No module named 'yaml'" in result["error"]
    result = call("realm", {"action": "status"}, session_id="plain")
    assert result["error_code"] == "realms_load_failed" and "No module named 'yaml'" in result["error"]
    # Nothing was written while degraded.
    assert integration.OwnershipStore(home, readonly=True).mode("plain", None) is None


def test_missing_dependency_without_any_store_blocks_nothing(tmp_path, monkeypatch):
    import model_tools
    home = _profile(tmp_path, monkeypatch)
    _discover(monkeypatch)
    effects = []
    _probe(effects)
    assert json.loads(model_tools.handle_function_call("load_failure_probe", {}, session_id="any")) == {"executed": True}
    assert json.loads(model_tools.handle_function_call("load_failure_probe", {})) == {"executed": True}
    assert not (home / "realms/sessions.sqlite3").exists()


def test_call_without_identity_is_paused_while_any_realm_is_recorded(tmp_path, monkeypatch):
    import model_tools
    _profile(tmp_path, monkeypatch, store=_realm_sessions)
    _discover(monkeypatch)
    effects = []
    _probe(effects)
    result = json.loads(model_tools.handle_function_call("load_failure_probe", {}))
    assert result["error_code"] == "realms_load_failed"
    assert effects == []


def test_pre_upgrade_store_schema_is_read_without_migration(tmp_path, monkeypatch):
    import sqlite3
    import model_tools

    def old_store(home):
        (home / "realms").mkdir()
        with sqlite3.connect(home / "realms/sessions.sqlite3") as db:
            db.executescript("CREATE TABLE owners (id TEXT PRIMARY KEY, mode TEXT);"
                             "CREATE TABLE aliases(kind TEXT,value TEXT,owner TEXT,PRIMARY KEY(kind,value));"
                             "INSERT INTO owners VALUES ('old-realm','realm'),('old-plain',NULL);"
                             "INSERT INTO aliases VALUES ('session_id','old-realm','old-realm'),"
                             "('session_id','old-plain','old-plain');")
    home = _profile(tmp_path, monkeypatch, store=old_store)
    before = (home / "realms/sessions.sqlite3").read_bytes()
    _discover(monkeypatch)
    effects = []
    _probe(effects)
    # A working runtime holds both for permission review; a failed load must not allow more.
    for session in ("old-realm", "old-plain"):
        result = json.loads(model_tools.handle_function_call("load_failure_probe", {}, session_id=session))
        assert result["error_code"] == "realms_load_failed", (session, result)
    assert json.loads(model_tools.handle_function_call("load_failure_probe", {}, session_id="never-seen")) == {"executed": True}
    assert effects == [None]
    assert (home / "realms/sessions.sqlite3").read_bytes() == before


def test_unreadable_store_with_missing_dependency_still_pauses_everything(tmp_path, monkeypatch):
    import model_tools
    from hermes_cli.plugins import get_plugin_command_handler

    def corrupt(home):
        (home / "realms").mkdir()
        (home / "realms/sessions.sqlite3").write_bytes(b"invalid SQLite fixture")
    _profile(tmp_path, monkeypatch, store=corrupt)
    _discover(monkeypatch)
    effects = []
    _probe(effects)
    result = json.loads(model_tools.handle_function_call("load_failure_probe", {}, session_id="plain"))
    assert result["error_code"] == "legacy_permission_review_required"
    assert "permission storage is unavailable" in result["error"]
    assert effects == []
    status = json.loads(get_plugin_command_handler("realm")("status", session_id="plain"))
    assert "permission storage is unavailable" in status["error"]
