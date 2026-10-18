"""This conversation's Realm in the Hermes classic CLI footer (``ctx.register_footer_item``).

The item is discovered through real Hermes plugin discovery and painted by the real footer; the
terminal viewer reads frames through the real ticket-checked, view-only ViewerServer from a
private 0600 Unix RFB source (no compositor or VM is started).
"""
import os
import pty
import runpy
from types import SimpleNamespace

import pytest
from realms_test_paths import PLUGIN_ROOT

load = runpy.run_path(str(PLUGIN_ROOT / "realms/_binding.py"))["load_runtime"]
pytestmark = pytest.mark.platforms("linux")

LIVE = "r-" + "a" * 24


def _service(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    integration = load("integration")
    monkeypatch.setattr(integration, "setup_status", lambda **kwargs: {"ready": True, "message": ""})
    monkeypatch.setattr(integration, "vm_setup_status", lambda *args: {"ready": True, "message": ""})
    return integration.RealmIntegration(home)


def _live(service, owner, monkeypatch, *, controlled=False, records=None):
    """The service's own record listing returns this owner's realm as running (no compositor)."""
    record = {"id": LIVE, "generation": "g" * 32, "session_id": owner, "status": "running",
              "size": "1280x800", "home": str(service.home)}
    rows = [record] if records is None else records
    monkeypatch.setattr(service.manager, "list", lambda: rows)
    monkeypatch.setattr(service._window_counter, "count", lambda r: 2)
    from_bridge = load("bridge")
    viewer = from_bridge.get_profile_viewer(service.home)
    monkeypatch.setattr(viewer, "is_controlled", lambda realm_id: controlled)
    return record


def test_footer_shows_only_this_sessions_requested_or_live_realm(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    footer = load("footer").FooterItem(service, ttl=0)
    session = {"session_id": "mine", "stored_session_id": "mine", "hermes_home": str(service.home)}
    # Never seen by Realms: nothing, and the snapshot did not register the session.
    assert footer.snapshot(session) is None
    assert service.owners.resolve(allow_missing=True, session_id="mine") is None
    owner = service.bind(session_origin="fresh", session_id="mine")
    other = service.bind(session_origin="fresh", session_id="other")
    assert footer.snapshot(session) is None  # bound but no desktop asked for
    _live(service, other, monkeypatch)
    assert footer.snapshot(session) is None  # another conversation's realm is not ours
    service.owners.request_kind(owner, "omarchy-vm")
    item = footer.snapshot(session)
    assert item["label"] == "◈ omarchy realm · on" and item["rows"] == [] and item["actions"] == []
    _live(service, owner, monkeypatch)
    item = footer.snapshot(session)
    assert item["tone"] == "ok" and item["label"].endswith("· live")
    assert item["rows"] == [{"key": LIVE, "label": "realm " + LIVE[:10],
                             "detail": "1280x800 · 2 windows · live"}]
    assert [(a["id"], a["key"]) for a in item["actions"]] == [
        ("watch", "enter"), ("view", "v"), ("shot", "s"), ("stop", "x")]
    assert next(a for a in item["actions"] if a["id"] == "stop")["confirm"] is True
    # Another profile's CLI never shows this profile's realm.
    assert footer.snapshot({**session, "hermes_home": str(tmp_path / "elsewhere")}) is None


def test_enter_opens_the_realm_where_it_draws_best_and_the_other_place_stays_one_key_away():
    describe = load("footer").describe
    status = {"requested": True, "realms": [{"id": LIVE, "kind": "realm", "state": "live"}]}

    def keys(**kw):
        return {a["id"]: a["key"] for a in describe(status, **kw)["actions"]}

    assert keys(terminal_view=True, pixels=True) == {"view": "enter", "watch": "b", "shot": "s", "stop": "x"}
    assert keys(terminal_view=True, pixels=False) == {"watch": "enter", "view": "v", "shot": "s", "stop": "x"}
    # No terminal to draw in (plugin host process): Enter is the browser and there is no view.
    assert keys(terminal_view=False, pixels=False) == {"watch": "enter", "shot": "s", "stop": "x"}


def test_human_control_and_stopped_realms_change_tone_and_drop_live_actions(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    footer = load("footer").FooterItem(service, ttl=0)
    owner = service.bind(session_origin="fresh", session_id="mine")
    _live(service, owner, monkeypatch, controlled=True)
    item = footer.snapshot({"session_id": "mine"})
    assert item["tone"] == "warn" and "you have control" in item["label"]
    stopped = {"id": LIVE, "generation": "g" * 32, "session_id": owner, "status": "stopped",
               "size": "1280x800", "home": str(service.home)}
    _live(service, owner, monkeypatch, records=[stopped])
    item = footer.snapshot({"session_id": "mine"})
    assert item["tone"] == "dim" and item["label"] == "◈ realm · stopped" and item["actions"] == []


def test_badge_names_only_the_states_worth_a_glance():
    """The status-bar badge: live, control and a pending review; nothing for quieter states."""
    describe = load("footer").describe
    live = {"id": LIVE, "kind": "omarchy-vm", "state": "live"}

    def badge(status):
        return (describe(status) or {}).get("badge")

    assert badge({"requested": True, "realms": [live]}) == "◈ live"
    assert badge({"requested": True, "realms": [{**live, "controlled": True}]}) == "◈ control"
    assert badge({"requested": True, "permission": {"state": "legacy-pending"},
                  "realms": [{**live, "error": "Legacy work"}]}) == "◈ review"
    for quiet in ({"requested": True, "realms": []},
                  {"requested": True, "realms": [{**live, "state": "stopped"}]},
                  {"requested": True, "realms": [{**live, "state": "recovery-required", "error": "x"}]}):
        item = describe(quiet)
        assert item is not None and "badge" not in item
    for status in ({"requested": True, "realms": [live]},
                   {"requested": True, "realms": [{**live, "controlled": True}]}):
        assert len(badge(status)) <= 12

def test_snapshot_is_cached_between_polls(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    now = [100.0]
    footer = load("footer").FooterItem(service, ttl=2.0, clock=lambda: now[0])
    owner = service.bind(session_origin="fresh", session_id="mine")
    _live(service, owner, monkeypatch)
    reads = []
    status = service.status
    monkeypatch.setattr(service, "status", lambda o: reads.append(o) or status(o))
    first = footer.snapshot({"session_id": "mine"})
    now[0] += 1.0
    assert footer.snapshot({"session_id": "mine"}) == first and len(reads) == 1
    now[0] += 1.5
    footer.snapshot({"session_id": "mine"})
    assert len(reads) == 2


def test_actions_act_only_on_this_sessions_realm(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    footer = load("footer").FooterItem(service, ttl=0)
    owner = service.bind(session_origin="fresh", session_id="mine")
    _live(service, owner, monkeypatch)
    watched, stopped = [], []
    monkeypatch.setattr(service, "watch", lambda o, rid: watched.append((o, rid)) or {
        "url": f"http://127.0.0.1:1/realms/{rid}/view#ticket=t"})
    monkeypatch.setattr(service, "stop", lambda o, only=None: stopped.append(o))
    result = footer.on_action("watch", {"session_id": "mine"}, LIVE)
    assert result["open_url"].startswith("http://127.0.0.1:") and watched == [(owner, LIVE)]
    with pytest.raises(ValueError, match="not running"):
        footer.on_action("watch", {"session_id": "mine"}, "r-" + "b" * 24)
    assert "stopped" in footer.on_action("stop", {"session_id": "mine"}, LIVE)["notice"]
    assert stopped == [owner]
    with pytest.raises(ValueError):
        footer.on_action("delete-everything", {"session_id": "mine"}, LIVE)


def test_actions_on_a_retained_stopped_row_never_reach_the_live_realm(tmp_path, monkeypatch):
    """The item's actions show on every row; a stopped row's x x / s must not hit the live VM."""
    service = _service(tmp_path, monkeypatch)
    footer = load("footer").FooterItem(service, ttl=0)
    owner = service.bind(session_origin="fresh", session_id="mine")
    service.owners.set_kind(owner, "omarchy-vm")
    retained = {"id": LIVE, "generation": "g" * 32, "session_id": owner, "status": "stopped",
                "size": "1280x800", "home": str(service.home)}
    vm = {"id": "v-" + "d" * 24, "generation": "h" * 32, "session_id": owner, "status": "running"}
    monkeypatch.setattr(service, "records", lambda who: [retained] if who == owner else [])
    monkeypatch.setattr(service, "vm_records", lambda who: [vm] if who == owner else [])
    stopped, shots = [], []
    monkeypatch.setattr(service, "stop", lambda o, only=None: stopped.append(o))
    monkeypatch.setitem(type(service)._COMMANDS, "shot", lambda self, o, args, identity: shots.append(o) or {
        "realm_id": vm["id"], "path": "/realm/screen.png"})
    session = {"session_id": "mine"}
    for action in ("stop", "shot", "watch", "view"):
        with pytest.raises(ValueError, match="not running"):
            footer.on_action(action, session, retained["id"])
        with pytest.raises(ValueError, match="not running"):
            footer.on_action(action, session, "v-" + "e" * 24)  # nothing this chat owns
    assert stopped == [] and shots == []
    # The live row itself still works.
    assert "stopped" in footer.on_action("stop", session, vm["id"])["notice"]
    assert footer.on_action("shot", session, vm["id"])["notice"].endswith("/realm/screen.png")
    assert stopped == [owner] and shots == [owner]


def test_a_realm_replaced_after_the_row_check_is_never_stopped_or_captured(tmp_path, monkeypatch):
    """The row is rechecked under the owner's activation lock, where a kind switch also runs."""
    service = _service(tmp_path, monkeypatch)
    footer = load("footer").FooterItem(service, ttl=0)
    owner = service.bind(session_origin="fresh", session_id="mine")
    regular = _live(service, owner, monkeypatch)
    vm = {"id": "v-" + "d" * 24, "generation": "h" * 32, "session_id": owner, "status": "running"}
    monkeypatch.setattr(service, "vm_records", lambda who: [vm] if who == owner else [])
    torn_down, shots = [], []
    monkeypatch.setattr(service, "_stop", lambda o: torn_down.append(o))
    monkeypatch.setitem(type(service)._COMMANDS, "shot", lambda self, o, args, identity: shots.append(o) or {
        "realm_id": vm["id"], "path": "/realm/screen.png"})
    switch = service._running_records

    def switched_after_first_check(who):
        # Another process switches this chat to the VM right after the footer's first look.
        monkeypatch.setattr(service, "_running_records", switch)
        result = switch(who)
        service.owners.set_kind(owner, "omarchy-vm")
        return result

    for action in ("stop", "shot"):
        service.owners.set_kind(owner, "realm")
        monkeypatch.setattr(service, "_running_records", switched_after_first_check)
        with pytest.raises(ValueError, match="not running"):
            footer.on_action(action, {"session_id": "mine"}, regular["id"])
    assert torn_down == [] and shots == []


def test_cached_snapshot_never_crosses_profiles(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch)
    footer = load("footer").FooterItem(service, ttl=60)
    owner = service.bind(session_origin="fresh", session_id="mine")
    _live(service, owner, monkeypatch)
    assert footer.snapshot({"session_id": "mine", "hermes_home": str(service.home)}) is not None
    assert footer.snapshot({"session_id": "mine", "hermes_home": str(tmp_path / "elsewhere")}) is None


def test_conflicting_aliases_show_nothing(tmp_path, monkeypatch):
    """session_id of chat A with stored_session_id of chat B must not pick either chat's realm."""
    service = _service(tmp_path, monkeypatch)
    footer = load("footer").FooterItem(service, ttl=0)
    a = service.bind(session_origin="fresh", session_id="chat-a")
    service.bind(session_origin="fresh", session_id="chat-b", stored_session_id="stored-b")
    _live(service, a, monkeypatch)
    assert footer.snapshot({"session_id": "chat-a"}) is not None
    assert footer.snapshot({"session_id": "chat-a", "stored_session_id": "stored-b"}) is None


def test_an_earlier_chat_awaiting_review_shows_review_without_a_desktop(tmp_path, monkeypatch):
    """Its tools are held until /realm review, so the footer and badge say so even with no rows."""
    service = _service(tmp_path, monkeypatch)
    footer = load("footer").FooterItem(service, ttl=0)
    owner = service.bind(session_id="earlier-chat")  # bound without session_origin: pre-upgrade
    assert service.owners.permission(owner)["state"] == "legacy-pending"
    item = footer.snapshot({"session_id": "earlier-chat"})
    assert item is not None and item["label"].endswith("· review needed") and item["badge"] == "◈ review"
    assert item["actions"] == []


def test_real_discovery_paints_the_realm_in_the_hermes_footer(tmp_path, monkeypatch):
    """Discovered as a user plugin, the item reaches the real footer for this CLI session."""
    from hermes_cli.plugins import discover_plugins, get_plugin_footer_items, get_plugin_manager
    from hermes_cli.cli_subagent_monitor import install_dock
    from tests.waiting import wait_until

    home = tmp_path / "profile"
    (home / "plugins").mkdir(parents=True)
    (home / "plugins/hermes-realms").symlink_to(PLUGIN_ROOT, target_is_directory=True)
    (home / "config.yaml").write_text("plugins:\n  enabled: [hermes-realms]\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    discover_plugins(force=True)
    loaded = get_plugin_manager()._plugins["hermes-realms"]
    assert loaded.enabled and loaded.error is None, loaded.error
    assert [item["name"] for item in get_plugin_footer_items()] == ["realm"]
    service = load("integration").get_integration(home)
    owner = service.bind(session_origin="fresh", session_id="cli-session")
    integration = load("integration")
    monkeypatch.setattr(integration, "setup_status", lambda **kwargs: {"ready": True, "message": ""})
    _live(service, owner, monkeypatch)
    cli = SimpleNamespace(agent=None, session_id="cli-session", _invalidate=lambda *a, **k: None, _app=None)
    install_dock(cli)
    monitor = cli._subagent_monitor

    def painted():
        monitor.refresh()
        return "◈ realm · live" in monitor.dock_text(columns=120, rows=30)
    wait_until(painted, what="realm in the footer")
    # Hosts that paint a status-bar badge get it with the item; older ones ignore the key.
    assert get_plugin_footer_items()[0]["snapshot"]({"session_id": "cli-session"}).get("badge") == "◈ live"
    text = monitor.dock_text(columns=120, rows=30)
    assert "realm " + LIVE[:10] in text and "1280x800 · 2 windows" in text
    assert [r["row_key"] for r in monitor.roster if r["kind"] == "plugin"] == [LIVE]
    # A different conversation in the same CLI process sees nothing of it.
    cli.session_id = "someone-else"
    wait_until(lambda: monitor.refresh() is not None and not monitor.plugin_items, what="hidden for others")


# -- the terminal viewer ---------------------------------------------------------------------------

def test_terminal_view_shows_frames_through_the_view_only_bridge_and_ends_its_ticket(tmp_path):
    """``v`` on the footer row: frames through the real bridge, closed by ``q``, ticket gone."""
    from test_realms_live_view import rfb_source

    runtime, endpoint, seen, close = rfb_source()
    record = {"id": LIVE, "generation": "g", "runtime_dir": runtime, "vnc_socket": str(endpoint),
              "processes": {"worker": load("lifecycle").identity(os.getpid())}}
    server = load("bridge").ViewerServer(lambda rid: record if rid == LIVE else None,
                                         state_dir=tmp_path / "authority")
    server.start()
    controller, terminal = pty.openpty()
    try:
        import fcntl
        import struct
        import termios
        fcntl.ioctl(terminal, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        stream = os.fdopen(terminal, "r+b", buffering=0, closefd=False)
        body = load("footer").terminal_view(server, LIVE, stdin=stream, stdout=stream,
                                            environ={"TERM": "xterm-256color"})
        token = body.url.split("ticket=", 1)[1].split("&", 1)[0]
        assert body.url.endswith("&view=1") and server.tickets.check(token, LIVE, "g:0") is not None
        import threading
        output, done = bytearray(), threading.Event()

        def drain():
            while not done.is_set():
                try:
                    chunk = os.read(controller, 65536)
                except OSError:
                    return
                output.extend(chunk)
                if b"\xe2\x96\x80" in output and not done.is_set():  # a half-block frame painted
                    os.write(controller, b"q")

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        assert body() == "Realm view closed"
        done.set()
        assert b"view-only" in output and b"\x1b[38;2;255;0;0m" in output  # the red pixel arrived
        assert server.tickets.check(token, LIVE, "g:0") is None  # the ticket ended with the view
        sent = [m for m in seen if isinstance(m, bytes)]
        assert {m[0] for m in sent[3:]} <= {0, 2, 3}  # only frame requests, never input
    finally:
        os.close(controller)
        os.close(terminal)
        server.stop()
        close()
