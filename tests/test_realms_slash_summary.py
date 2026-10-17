"""``/realm on|off|status`` answers a person with one line; the model's tool keeps the JSON.

The plugin is discovered as a user plugin and the command is invoked the way the classic CLI,
TUI and gateway invoke it (``invoke_plugin_command`` with the dispatch context). No compositor or
VM is started: the service's own record listing returns the running realm.
"""
import json
import runpy

import pytest
from realms_test_paths import PLUGIN_ROOT

load = runpy.run_path(str(PLUGIN_ROOT / "realms/_binding.py"))["load_runtime"]
pytestmark = pytest.mark.platforms("linux")

LIVE = "r-" + "c" * 24


@pytest.fixture
def realm(tmp_path, monkeypatch):
    from hermes_cli.plugins import discover_plugins, get_plugin_command_handler, get_plugin_manager
    from hermes_cli.plugins_command import invoke_plugin_command, plugin_command_context
    from tools.registry import registry

    home = tmp_path / "profile"
    (home / "plugins").mkdir(parents=True)
    (home / "plugins/hermes-realms").symlink_to(PLUGIN_ROOT, target_is_directory=True)
    (home / "config.yaml").write_text("plugins:\n  enabled: [hermes-realms]\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    integration = load("integration")
    ready = {"value": True}
    monkeypatch.setattr(integration, "setup_status", lambda **kwargs: {
        "ready": ready["value"], "message": "Realms setup required: labwc. Then run hermes realms doctor."})
    monkeypatch.setattr(integration, "vm_setup_status", lambda *args: {"ready": True, "message": ""})
    discover_plugins(force=True)
    loaded = get_plugin_manager()._plugins["hermes-realms"]
    assert loaded.enabled and loaded.error is None, loaded.error
    service = integration.get_integration(home)
    # A chat started after the upgrade, as Hermes' session-start hook binds it.
    service.bind(session_origin="fresh", session_id="cli-chat")
    handler = get_plugin_command_handler("realm")
    context = plugin_command_context(session_id="cli-chat", stored_session_id="cli-chat", surface="cli")

    def slash(raw):
        return invoke_plugin_command(handler, raw, **context)

    def tool(**args):
        return json.loads(registry.get_entry("realm").handler(args, session_id="cli-chat"))

    def go_live(*, controlled=False):
        owner = service.bind(session_id="cli-chat")
        record = {"id": LIVE, "generation": "g" * 32, "session_id": owner, "status": "running",
                  "size": "1280x800", "home": str(service.home)}
        monkeypatch.setattr(service.manager, "list", lambda: [record])
        monkeypatch.setattr(service._window_counter, "count", lambda r: 1)
        viewer = load("bridge").get_profile_viewer(service.home)
        monkeypatch.setattr(viewer, "is_controlled", lambda realm_id: controlled)

    yield type("Realm", (), {"slash": staticmethod(slash), "tool": staticmethod(tool), "ready": ready,
                             "go_live": staticmethod(go_live), "service": service})
    service.unload()


def test_on_off_and_status_print_one_human_line(realm):
    assert realm.slash("status") == "Realm available · none yet; the agent starts one when a test needs it"
    assert realm.slash("on") == "Realm on · realm · starts when needed"
    realm.go_live()
    assert realm.slash("status") == "Realm on · realm · live (1 window)"
    assert realm.slash("") == realm.slash("status")
    realm.go_live(controlled=True)
    assert realm.slash("status") == "Realm on · realm · live · you have control"
    realm.go_live()
    # Off keeps the human's desktop running; the line says so.
    assert realm.slash("off") == "Realm off · realm still running, the agent won't use it"
    assert realm.slash("status").startswith("Realm off")
    for line in (realm.slash("on"), realm.slash("status"), realm.slash("off")):
        assert "\n" not in line and not line.lstrip().startswith("{")


def test_setup_and_refusals_read_as_one_line_with_the_next_step(realm):
    realm.ready["value"] = False
    refused = realm.slash("on")
    assert refused.startswith("Realm: ") and "hermes realms doctor" in refused and "\n" not in refused
    # The request is remembered, so status names the fix instead of the raw setup dict.
    assert realm.slash("status") == "Realm: setup needed — run hermes realms doctor"
    realm.ready["value"] = True
    assert realm.slash("size") == "Realm: Use /realm size WIDTHxHEIGHT"


def test_json_flag_and_the_model_tool_keep_the_machine_readable_status(realm):
    realm.slash("on")
    realm.go_live()
    as_json = json.loads(realm.slash("status --json"))
    from_tool = realm.tool(action="status")
    assert as_json == from_tool
    assert from_tool["mode"] == "realm" and from_tool["realms"][0]["id"] == LIVE
    # The tool's refusals stay JSON errors the model can read.
    assert realm.tool(action="size")["error"] == "Use /realm size WIDTHxHEIGHT"
    # --json reaches the command parser as nothing: it is not an extra argument.
    assert json.loads(realm.slash("off --json"))["mode"] == "host"
    assert realm.tool(action="status")["mode"] == "host"


def test_a_pending_review_says_what_to_run(realm):
    realm.service.bind(session_id="earlier-chat")  # a chat from before the upgrade
    from hermes_cli.plugins import get_plugin_command_handler
    from hermes_cli.plugins_command import invoke_plugin_command
    line = invoke_plugin_command(get_plugin_command_handler("realm"), "status", session_id="earlier-chat")
    assert line == "Realm: review needed — run /realm review"


def test_commands_with_their_own_results_answer_as_before(realm):
    # watch/shot/push/pull/review/repair return their own data (a URL, a path), never a status line.
    result = json.loads(realm.slash("watch"))
    assert result == {"error": "Realm is not running"}
