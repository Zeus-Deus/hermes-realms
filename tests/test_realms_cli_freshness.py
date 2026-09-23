"""CLI-generated sessions keep ordinary tools without legacy permission review."""
import json
from pathlib import Path
import runpy
import sqlite3

import pytest
from realms_test_paths import HERMES_ROOT, PLUGIN_ROOT, install_user_plugin

pytestmark = pytest.mark.linux_only
ROOT = HERMES_ROOT


@pytest.fixture
def cli_fixture(tmp_path, monkeypatch):
    home = tmp_path / 'profile'
    home.mkdir()
    install_user_plugin(home)
    (home / 'config.yaml').write_text('plugins:\n  enabled: [hermes-realms]\nmodel:\n  default: fixture-no-inference\n  provider: custom\n  base_url: http://127.0.0.1:1/v1\n  context_length: 128000\nmemory:\n  memory_enabled: false\n  user_profile_enabled: false\n')
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('TERMINAL_CWD', str(tmp_path))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    import cli as module
    from hermes_cli.plugins import discover_plugins

    monkeypatch.setattr(module, '_hermes_home', home)
    monkeypatch.setitem(module.CLI_CONFIG, 'model', {'default': 'fixture-no-inference', 'provider': 'custom', 'base_url': 'http://127.0.0.1:1/v1', 'context_length': 128000})
    discover_plugins()
    # No model call is made; suppress unrelated interactive startup facilities.
    monkeypatch.setattr(module, '_prepare_deferred_agent_startup', lambda: None)
    monkeypatch.setattr(module.HermesCLI, '_install_tool_callbacks', lambda self: None)
    monkeypatch.setattr(module.HermesCLI, '_ensure_tirith_security', lambda self: None)
    import hermes_cli.mcp_startup as mcp
    monkeypatch.setattr(mcp, 'ensure_mcp_discovery_before_agent_build', lambda **kwargs: None)
    shell = module.HermesCLI(model='fixture-no-inference', provider='custom', api_key='fixture',
                            base_url='http://127.0.0.1:1/v1', toolsets=['terminal'], ignore_rules=True)
    try:
        yield module, shell, home
    finally:
        if shell.agent is not None:
            shell.agent.close()
        module._active_agent_ref = None


@pytest.mark.parametrize('rotation', ['initial', 'new-before-build', 'new-reused-agent'])
def test_new_cli_publishes_fresh_identity_before_ordinary_work(cli_fixture, rotation):
    _, shell, home = cli_fixture
    from hermes_cli.session_hook_context import agent_session_identity
    from agent.conversation_loop import _restore_or_build_system_prompt
    from model_tools import handle_function_call

    original = shell.session_id
    if rotation == 'new-reused-agent':
        assert shell._init_agent()
        _restore_or_build_system_prompt(shell.agent, None, [])
        _restore_or_build_system_prompt(shell.agent, None, [{'role': 'user', 'content': 'Earlier turn'}])
        assert agent_session_identity(shell.agent)['session_origin'] == 'resume'
    if rotation != 'initial':
        shell.new_session(silent=True)
        assert shell.session_id != original
    assert shell._init_agent()
    identity = agent_session_identity(shell.agent)
    assert identity['session_origin'] == 'fresh'
    assert identity['hermes_home'] == home
    assert identity['stored_session_id'] == shell.session_id
    _restore_or_build_system_prompt(shell.agent, None, shell.conversation_history)
    result = json.loads(handle_function_call('terminal', {'command': 'printf cli-ordinary'}, session_id=shell.session_id))
    assert result.get('exit_code') == 0, result
    assert result['output'] == 'cli-ordinary'
    load = runpy.run_path(str(PLUGIN_ROOT / 'realms/_binding.py'))['load_runtime']
    service = load('integration').RealmIntegration(home)
    assert service.owners.mode(shell.session_id, 'ask') == 'ask'
    assert not list((home / 'realms').glob('[rv]-*.json'))


@pytest.mark.parametrize('switch', ['resume-empty', 'resume-history', 'branch'])
@pytest.mark.parametrize('built', [False, True], ids=['lazy', 'reused-agent'])
def test_live_cli_switch_is_a_continuation_before_the_next_prompt(cli_fixture, switch, built):
    _, shell, home = cli_fixture
    from hermes_cli.session_hook_context import agent_session_identity
    from agent.conversation_loop import _restore_or_build_system_prompt
    from model_tools import handle_function_call

    if built:
        assert shell._init_agent()
        assert agent_session_identity(shell.agent)['session_origin'] == 'fresh'
    original_agent = shell.agent
    if switch == 'branch':
        shell._session_db.create_session(shell.session_id, source='cli', model='fixture-no-inference')
        shell.conversation_history = [{'role': 'user', 'content': 'Existing private transcript'}]
        shell._handle_branch_command('/branch continuity-fixture')
    else:
        target = 'legacy-cli-target'
        shell._session_db.create_session(target, source='cli', model='fixture-no-inference')
        if switch == 'resume-history':
            shell._session_db.append_message(target, role='user', content='Existing private transcript')
        shell._handle_resume_command('/resume ' + target)
        assert shell.session_id == target
    assert shell.agent is original_agent
    assert shell._plugin_session_identity['session_origin'] == 'resume'
    assert shell._plugin_session_identity['stored_session_id'] == shell.session_id
    assert shell._plugin_session_identity['hermes_home'] == home
    if built:
        assert agent_session_identity(shell.agent)['session_origin'] == 'resume'
    assert shell._init_agent()
    _restore_or_build_system_prompt(shell.agent, None, shell.conversation_history)
    result = json.loads(handle_function_call('terminal', {'command': 'printf forbidden'}, session_id=shell.session_id))
    assert result.get('error'), result
    with sqlite3.connect(home / 'realms/sessions.sqlite3') as db:
        contract = db.execute('SELECT execution_contract FROM owners WHERE id = ?', (shell.session_id,)).fetchone()
        assert contract is None or contract[0] is None


@pytest.mark.parametrize('continuation', ['resume', 'rebuild-with-history', 'unrecognized-id'])
def test_cli_continuation_cannot_claim_fresh_permissions(cli_fixture, continuation):
    module, shell, home = cli_fixture
    from hermes_cli.session_hook_context import agent_session_identity
    from agent.conversation_loop import _restore_or_build_system_prompt
    from model_tools import handle_function_call
    sid = shell.session_id
    # Seed the CLI's actual acquired store (the canonical runner isolates it).
    shell._session_db.create_session(sid, source='cli', model='fixture-no-inference')
    shell._session_db.append_message(sid, role='user', content='Historical private work')
    if continuation == 'resume':
        replacement = module.HermesCLI(model='fixture-no-inference', provider='custom', api_key='fixture',
                                      base_url='http://127.0.0.1:1/v1', toolsets=['terminal'], ignore_rules=True, resume=sid)
        # Retain the fixture's object for deterministic cleanup.
        shell.__dict__.update(replacement.__dict__)
    elif continuation == 'rebuild-with-history':
        shell.conversation_history = [{'role': 'user', 'content': 'Historical private work'}]
    else:
        shell.session_id = 'unknown-supplied-id'
    assert shell._init_agent()
    identity = agent_session_identity(shell.agent)
    assert identity['session_origin'] != 'fresh'
    _restore_or_build_system_prompt(shell.agent, None, shell.conversation_history)
    result = json.loads(handle_function_call('terminal', {'command': 'printf forbidden'}, session_id=shell.session_id))
    assert result.get('error'), result
    assert result.get('output') != 'forbidden'
    with sqlite3.connect(home / 'realms/sessions.sqlite3') as db:
        rows = db.execute('SELECT execution_contract FROM owners').fetchall()
        assert all(row[0] is None for row in rows)
