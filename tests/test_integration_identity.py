"""Actual desktop ownership publication, compression and lifecycle routing."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from test_integration import install_plugin


def test_real_lazy_session_rpc_registers_profile_scoped_owners_before_any_turn(
    tmp_path, monkeypatch
):
    from tui_gateway import server
    from realms.integration import OwnershipStore, get_integration, OwnerError
    import pytest

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(server, "_sessions", {})
    # Do not launch model/background work; real session creation/identity hooks remain intact.
    monkeypatch.setattr(server, "_schedule_agent_build", lambda *a, **kw: None)
    monkeypatch.setattr(server, "_schedule_session_cap_enforcement", lambda: None)
    profiles = [home / "profiles" / name for name in ("a", "b")]
    for profile in profiles:
        install_plugin(profile)

    def create(profile):
        response = server._methods["session.create"](
            "new-" + profile.name, {"source": "desktop", "profile": profile.name}
        )
        assert "error" not in response, response
        result = response["result"]
        store = OwnershipStore(profile)
        owner = store.resolve(
            runtime_session_id=result["session_id"],
            stored_session_id=result["stored_session_id"],
        )
        assert owner == result["stored_session_id"]
        assert get_integration(profile).manager.list() == []
        return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, profiles))
    with pytest.raises(OwnerError):
        OwnershipStore(profiles[0]).resolve(
            runtime_session_id=results[0]["session_id"],
            stored_session_id=results[1]["stored_session_id"],
        )
    for profile, result in zip(profiles, results):
        server._finalize_session(
            server._sessions[result["session_id"]], end_reason="tui_close"
        )
        assert get_integration(profile).manager.list() == []


def test_real_resume_compression_task_and_finalization_keep_one_realm(
    tmp_path, monkeypatch
):
    from tui_gateway import server
    from hermes_state import SessionDB
    from run_agent import AIAgent
    from realms.integration import get_integration
    from hermes_cli.session_execution import resolve_session_execution_context
    from agent.tool_executor import _pre_tool_block, _ToolCallRef
    from agent.turn_context import _bind_turn_identity
    from agent.turn_finalizer import finalize_turn
    from agent.conversation_compression import recover_rotated_compression_session

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    ambient = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(ambient))
    monkeypatch.setattr(server, "_sessions", {})
    for name in (
        "_schedule_agent_build",
        "_schedule_session_cap_enforcement",
        "_start_session_services",
        "_schedule_mcp_late_refresh",
        "_wire_session_agent",
        "_emit",
    ):
        monkeypatch.setattr(server, name, lambda *a, **kw: None)
    home = ambient / "profiles" / "realm-owner"
    install_plugin(home)
    key = "original-conversation"
    with SessionDB(db_path=home / "state.db") as db:
        db.create_session(key, source="desktop")
    response = server._methods["session.resume"](
        "resume",
        {"session_id": key, "profile": home.name, "source": "desktop", "lazy": True},
    )
    assert "error" not in response, response
    sid = response["result"]["session_id"]
    record = server._sessions[sid]
    service = get_integration(home)
    assert service.owners.resolve(runtime_session_id=sid, stored_session_id=key) == key
    db = SessionDB(db_path=home / "state.db")
    # Suppress only external model discovery; actual construction and hook dispatch run.
    from hermes_cli.session_hook_context import hook_profile_scope

    with (
        hook_profile_scope(home),
        patch("model_tools.get_tool_definitions", return_value=[]),
        patch("model_tools.check_toolset_requirements", return_value={}),
    ):
        agent = AIAgent(
            model="test-model",
            api_key="test-key",
            base_url="http://127.0.0.1:1/v1",
            provider="custom",
            enabled_toolsets=[],
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            skip_background_review=True,
            session_db=db,
            session_id=key,
            platform="desktop",
        )
    server._attach_built_agent(record, agent)
    try:
        task, turn = _bind_turn_identity(agent, key, None, None, None, None)
        block, _ = _pre_tool_block(
            agent, _ToolCallRef("terminal", {"command": "true"}, task, "first", [])
        )
        assert block is None
        realm = service.manager.list()[0]
        owner = service.owners.resolve(
            runtime_session_id=sid, stored_session_id=key, task_id=task
        )
        assert owner == key
        with hook_profile_scope(home):
            lease = resolve_session_execution_context(session_id=key, task_id=task)
        assert lease is not None
        child = key + "-compressed"
        db.publish_compression_child(
            parent_session_id=key,
            child_session_id=child,
            source="desktop",
            model=agent.model,
            model_config={},
            system_prompt="compressed",
            require_compression_lease=False,
            messages=[{"role": "user", "content": "Compressed handoff"}],
        )
        assert recover_rotated_compression_session(agent)
        block, _ = _pre_tool_block(
            agent, _ToolCallRef("terminal", {"command": "true"}, task, "second", [])
        )
        assert block is None
        server._sync_session_key_after_compress(sid, record, restart_slash_worker=False)
        assert (
            service.owners.resolve(
                runtime_session_id=sid,
                stored_session_id=child,
                session_id=child,
                task_id=task,
            )
            == owner
        )
        assert service.manager.list()[0]["generation"] == realm["generation"]
        finalize_turn(
            agent,
            final_response="done",
            api_call_count=1,
            interrupted=False,
            failed=False,
            messages=[],
            conversation_history=[],
            effective_task_id=task,
            turn_id=turn,
            user_message="hello",
            original_user_message="hello",
            _should_review_memory=False,
            _turn_exit_reason="text_response(test)",
        )
        assert service.manager.list()[0]["id"] == realm["id"]
        server._finalize_session(record, end_reason="tui_close")
        assert service.manager.list() == []
        with hook_profile_scope(home):
            assert resolve_session_execution_context(task_id=task) is None
    finally:
        with hook_profile_scope(home):
            service.unload()
        agent.close()
        db.close()
