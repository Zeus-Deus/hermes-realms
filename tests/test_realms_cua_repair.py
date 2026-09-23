"""Repair retires only CUA; retained work and caller authority stay put."""
import json
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest
from realms_test_paths import PLUGIN_ROOT

PLUGIN = PLUGIN_ROOT


@pytest.fixture(params=["realm", "omarchy-vm"])
def repair_fixture(tmp_path, monkeypatch, request):
    from hermes_cli.session_execution import (
        SessionExecutionContext, register_session_execution_context,
        resolve_session_execution_context, remove_session_execution_context,
    )
    from tools.computer_use import tool

    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    plugin = runpy.run_path(str(PLUGIN / "plugin.py"))
    load = plugin["_load_runtime"]
    service = plugin["get_integration"](home)
    tools, commands, teardown = {}, {}, []
    ctx = SimpleNamespace(
        register_cli_command=lambda *a, **k: None,
        register_tool=lambda name, group, schema, handler, **kw: tools.update({name: (schema, handler)}),
        register_command=lambda name, handler, **kw: commands.update({name: handler}),
        register_middleware=lambda *a, **kw: None, register_hook=lambda *a, **k: None, register_skill=lambda *a, **k: None,
        on_unload=teardown.append,
    )
    plugin["register"](ctx)
    owner = service.bind(session_origin="fresh", session_id="parent", task_id="parent-task")
    foreign = service.bind(session_origin="fresh", session_id="foreign", task_id="foreign-task")
    service.owners.set_kind(owner, request.param)
    record = {"id": "r-" + "a" * 24, "generation": "b" * 32, "session_id": owner, "status": "running"}
    service._attachments[owner] = (record["generation"], (), record["id"])
    monkeypatch.setattr(service, "records", lambda who: [record] if who == owner else [])
    monkeypatch.setattr(service, "vm_records", lambda who: [record] if who == owner else [])
    contexts = load("target_contexts")
    leases = {}
    for who, purpose in ((owner, "cua"), (owner, "terminal"), (foreign, "cua")):
        sid = who + ":" + purpose
        register_session_execution_context(sid, SessionExecutionContext(env_set={"KEPT": sid}))
        lease = resolve_session_execution_context(session_id=sid)
        leases[who, purpose] = lease
        service._target_contexts[who, request.param, purpose] = (record["id"], record["generation"], lease)
    register_session_execution_context(owner, SessionExecutionContext(env_set={"PARENT": "kept"}), task_ids=["parent-task"])
    parent = resolve_session_execution_context(session_id=owner)
    retired = []
    monkeypatch.setattr(tool, "release_computer_use_execution_context", retired.append)
    def forbidden(*a, **k):
        pytest.fail("CUA repair must not start/stop the resource")
    monkeypatch.setattr(service.manager, "start", forbidden)
    monkeypatch.setattr(service.manager, "stop", forbidden)
    monkeypatch.setattr(service, "ready", forbidden)
    monkeypatch.setattr(service, "stop", forbidden)
    service._vm = SimpleNamespace(config=service.manager.config, start=forbidden, stop=forbidden, validate=lambda _: record)
    authority = load("bridge").get_profile_viewer(home).authority
    try:
        yield SimpleNamespace(service=service, owner=owner, foreign=foreign, record=record,
                              leases=leases, parent=parent, retired=retired, tools=tools,
                              commands=commands, authority=authority, contexts=contexts)
    finally:
        service._attachments.clear()
        contexts.release_targets(service, owner)
        contexts.release_targets(service, foreign)
        remove_session_execution_context(owner)
        for callback in teardown:
            callback()


@pytest.mark.linux_only
@pytest.mark.parametrize("surface", ["tool", "slash"])
def test_repair_invalidates_only_owners_cua_lease(repair_fixture, surface):
    from hermes_cli.session_execution import SessionExecutionError, resolve_session_execution_context
    f = repair_fixture
    schema, handler = f.tools["realm"]
    identity = {"session_id": f.owner, "task_id": "parent-task"}
    result = json.loads(handler({"action": "repair"}, **identity) if surface == "tool"
                        else f.commands["realm"]("repair", **identity))
    assert not result.get("error"), result
    assert "repair" in schema["parameters"]["properties"]["action"]["enum"]
    assert result["message"] == "CUA connection reset; next computer-use reconnects"
    cua = f.leases[f.owner, "cua"]
    with pytest.raises(SessionExecutionError):
        cua.check()
    assert f.retired == [cua]
    f.leases[f.owner, "terminal"].check()
    f.leases[f.foreign, "cua"].check()
    f.parent.check()
    assert resolve_session_execution_context(session_id=f.owner, task_id="parent-task") is f.parent
    assert f.service._attachments[f.owner][2] == f.record["id"]


@pytest.mark.linux_only
@pytest.mark.parametrize("refusal", ["human", "absent", "stopped", "arguments", "disabled", "foreign-identity"])
def test_repair_refusal_preserves_all_leases(repair_fixture, monkeypatch, refusal):
    f = repair_fixture
    raw = "repair"
    identity = {"session_id": f.owner, "task_id": "parent-task"}
    if refusal == "human":
        assert f.authority.acquire(f.record["id"], f.record["generation"], "human")
    elif refusal == "absent":
        monkeypatch.setattr(f.service, "records", lambda _: [])
        monkeypatch.setattr(f.service, "vm_records", lambda _: [])
    elif refusal == "stopped":
        f.record["status"] = "stopped"
    elif refusal == "arguments":
        raw += " foreign"
    elif refusal == "disabled":
        f.service.owners.set_mode(f.owner, "host")
    else:
        identity["task_id"] = "foreign-task"
    result = json.loads(f.commands["realm"](raw, **identity))
    assert result.get("error"), result
    assert not f.retired
    for lease in (*f.leases.values(), f.parent):
        lease.check()


@pytest.mark.linux_only
@pytest.mark.parametrize("transition", ["unchanged", "held", "takeover", "handback"])
def test_shot_fences_human_control_and_discards_revoked_pixels(repair_fixture, monkeypatch, transition):
    from PIL import Image
    f = repair_fixture
    if f.service.kind(f.owner) == "omarchy-vm":
        monkeypatch.setattr(f.service, "records", lambda _: [])
    monkeypatch.setattr(f.service.manager, "env", lambda _: {})
    captured = []

    def shot(realm_id, path):
        captured.append(path)
        Image.new("RGB", (8, 8), "blue").save(path)
        if transition in ("takeover", "handback"):
            assert f.authority.acquire(realm_id, f.record["generation"], "human")
            if transition == "handback":
                f.authority.release(realm_id, "human")
        return path

    monkeypatch.setattr(f.service.manager, "shot", shot)
    f.service._vm.shot = shot
    if transition == "held":
        assert f.authority.acquire(f.record["id"], f.record["generation"], "human")
    result = json.loads(f.commands["realm"]("shot", session_id=f.owner, task_id="parent-task"))
    if transition == "unchanged":
        assert not result.get("error"), result
        assert Path(result["path"]).exists()
    else:
        assert result.get("error"), result
        assert not list(f.service.manager.registry.root.glob("shot-*"))
    assert len(captured) == (0 if transition == "held" else 1)
    assert f.service.pre_tool(tool_name="terminal", args={"command": "pwd"}, session_id=f.owner) is None
    f.parent.check()
