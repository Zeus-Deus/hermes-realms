"""This conversation's Realm in the Hermes classic CLI live-work footer.

Hermes polls ``snapshot`` about once a second off its render path (``ctx.register_footer_item``);
the answer is cached here for two seconds so the footer never costs more than the desktop's own
status refresh. Nothing here binds a conversation or starts a desktop: an owner Realms never saw,
or one that has not asked for a desktop, shows nothing. Actions act only on this session's own
realms, exactly like ``/realm watch|stop|shot``.
"""

import os
import threading
import time

from .integration import OwnerError
from .status_text import KIND_LABELS

TTL = 2.0
_IDENTITY = ("session_id", "stored_session_id", "hermes_home", "profile", "surface")


def _identity(session):
    return {key: session.get(key) for key in _IDENTITY if session.get(key)}


def _short(realm_id):
    return realm_id[:10] if isinstance(realm_id, str) else "?"


def _detail(row):
    if row.get("controlled"):
        return "you have control"
    if row.get("error") and row.get("state") != "live":
        return " ".join(str(row["error"]).split())
    parts = []
    if row.get("size"):
        parts.append(row["size"])
    stats = row.get("stats") or {}
    if stats.get("memory_bytes"):
        parts.append(f"{stats['memory_bytes'] // (1024 * 1024)} MB")
    elif row.get("memory_mb"):
        parts.append(f"{row['memory_mb']} MB")
    count = row.get("window_count")
    if count is not None:
        parts.append(f"{count} window" + ("" if count == 1 else "s"))
    parts.append(row.get("state") or "unknown")
    return " · ".join(parts)


def _live_actions(terminal_view, pixels):
    """Enter shows the realm where it looks best: in this terminal when it draws real pixels
    (kitty graphics), else in the browser. The other place stays one key away."""
    watch = {"id": "watch", "label": "browser" if pixels else "watch", "key": "b" if pixels else "enter"}
    view = {"id": "view", "label": "view" if pixels else "view here", "key": "enter" if pixels else "v"}
    lead = [view, watch] if terminal_view and pixels else [watch] + ([view] if terminal_view else [])
    return lead + [{"id": "shot", "label": "shot", "key": "s"},
                   {"id": "stop", "label": "stop", "key": "x", "confirm": True}]


def describe(status, setup_job=None, *, terminal_view=True, pixels=False):
    """The footer item for one owner's ``status()`` (plus its latest setup job), or None."""
    rows = status.get("realms") or []
    live = [row for row in rows if row.get("state") == "live"]
    kind = (live[0]["kind"] if live else status.get("requested_kind") or status.get("kind") or "realm")
    name = "◈ " + KIND_LABELS.get(kind, kind)
    state = (status.get("permission") or {}).get("state", "optional")
    held = state != "optional"
    setting_up = bool(setup_job) and setup_job.get("state") in ("running", "cancelling")
    # A pending review holds every tool in this chat, desktop or not, so it always shows.
    if not live and not setting_up and state != "legacy-pending" and (
            not status.get("requested") or held and not rows or status.get("mode") == "host"):
        return None
    # Readiness belongs to the kind this chat asked for, not to a retained row of another kind.
    requested = status.get("requested_kind") or status.get("kind") or "realm"
    setup = status.get("vm_setup") if requested == "omarchy-vm" else status.get("setup")
    ready = (setup or {}).get("ready", True)
    if not live and not ready:
        name = "◈ " + KIND_LABELS.get(requested, requested)
    badge = None  # the status-bar glyph: only the states worth a glance from anywhere
    if held:
        label, tone, badge = name + " · review needed", "warn", "◈ review"
    elif any(row.get("controlled") for row in live):
        label, tone, badge = name + " · you have control", "warn", "◈ control"
    elif live:
        label, tone, badge = name + " · live", "ok", "◈ live"
    elif setting_up:
        label, tone = name + " · setting up", "dim"
    elif not ready:
        label, tone = name + " · setup needed", "warn"
    elif any(row.get("error") for row in rows):
        label, tone = name + " · needs attention", "warn"
    elif rows:
        label, tone = name + " · " + (rows[0].get("state") or "stopped"), "dim"
    else:
        label, tone = name + " · on", "dim"
    items = [{"key": row["id"], "label": f"{KIND_LABELS.get(row.get('kind'), 'realm')} {_short(row['id'])}",
              "detail": _detail(row)} for row in rows if isinstance(row.get("id"), str)]
    if setting_up and not items:
        items.append({"key": "setup", "label": "setup", "detail": (setup_job or {}).get("message") or "running"})
    actions = _live_actions(terminal_view, pixels) if live and not held else []
    item = {"label": label, "tone": tone, "rows": items, "actions": actions}
    if badge:
        item["badge"] = badge  # read-only status-bar segment; older Hermes ignores the key
    return item


class FooterItem:
    def __init__(self, service, *, ttl=TTL, clock=time.monotonic):
        self.service = service
        self.ttl = ttl
        self._clock = clock
        self._cache = {}
        self._lock = threading.Lock()
        # Under plugins.isolation: host this code runs in a process without the user's terminal.
        self.terminal_view = os.environ.get("HERMES_PLUGIN_HOST_PROCESS") != "1"
        from .live_view import graphics_mode
        self.pixels = self.terminal_view and graphics_mode() == "pixels"

    def owner(self, session):
        """The owner this session's ids already resolve to; never binds a new one.

        Every registered alias must agree: ids that resolve to two conversations are refused, as
        the ownership store refuses them when bound together.
        """
        owners = self.service.owners
        found = set()
        for field, value in (("session_id", session.get("session_id")),
                             ("stored_session_id", session.get("stored_session_id")),
                             ("session_id", session.get("stored_session_id"))):
            if value:
                owner = owners.resolve(allow_missing=True, **{field: value})
                if owner is not None:
                    found.add(owner)
        if len(found) > 1:
            raise OwnerError("Conflicting session owners")
        return next(iter(found), None)

    def _other_profile(self, session):
        if not session.get("hermes_home"):
            return False
        from pathlib import Path
        return Path(session["hermes_home"]).resolve() != self.service.home

    def snapshot(self, session):
        if self._other_profile(session):
            return None  # another profile's footer never shows this profile's realms, cached or not
        key = (session.get("session_id"), session.get("stored_session_id"))
        now = self._clock()
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and now < cached[0]:
                return cached[1]
        value = self._read(session)
        with self._lock:
            self._cache = {key: (self._clock() + self.ttl, value)}
        return value

    def _read(self, session):
        if self._other_profile(session):
            return None
        try:
            owner = self.owner(session)
        except OwnerError:
            return None  # conflicting aliases: show nothing rather than guess
        if owner is None:
            return None
        from . import setup_flow
        status = self.service.status(owner)
        try:
            job = setup_flow.latest(self.service, owner)
        except (OwnerError, PermissionError, ValueError, OSError):
            job = None
        return describe(status, job, terminal_view=self.terminal_view, pixels=self.pixels)

    def forget(self):
        with self._lock:
            self._cache.clear()

    def on_action(self, action_id, session, row_key):
        try:
            return self._act(action_id, session, row_key)
        finally:
            self.forget()  # the next poll shows what the action changed

    def _act(self, action_id, session, row_key):
        if action_id not in ("watch", "view", "shot", "stop"):
            raise ValueError(f"Unknown realm action {action_id!r}")
        identity = _identity(session)
        service = self.service
        owner = service.bind(**identity)

        def require_selected():
            # Every row carries the item's actions; only the selected row's own live realm is a
            # target, so a retained stopped row never stops or captures the realm that replaced it.
            if not any(r["id"] == row_key for r in service._running_records(owner)):
                raise ValueError("Realm is not running")

        require_selected()
        if action_id == "stop":
            service.stop(owner, only=row_key)
            return {"notice": "Realm stopped; its workspace is kept"}
        if action_id == "shot":
            # Owner-wide like /realm shot, so recheck the row under the owner's activation lock:
            # a kind switch or teardown in another process cannot slip in before the capture.
            with service._lock, service.owners.activation_guard(owner):
                require_selected()
                shot = service.command("shot", **identity)
            return {"notice": f"Realm screenshot saved: {shot['path']}"}
        if action_id == "watch":
            return {"open_url": self.service.watch(owner, row_key)["url"], "notice": "Opening the Realm viewer"}
        if not self.terminal_view:
            raise ValueError("Viewing in this terminal needs plugins.isolation: in_process")
        from .bridge import get_profile_viewer
        return {"run_in_terminal": terminal_view(get_profile_viewer(self.service.home).start(), row_key)}


def terminal_view(viewer, realm_id, **run_options):
    """A blocking body that shows ``realm_id`` view-only in the real terminal until closed.

    The ticket is view-only (the bridge drops input server-side) and ends with the view; the
    header's browser link (``body.url``) shares it, so it stops working once the view closes.
    ``run_options`` reach ``live_view.run`` (tests pass a pty and an environment).
    """
    from . import live_view

    token = viewer.issue(realm_id, can_control=False, ttl=300)
    url = f"{viewer.origin}/realms/{realm_id}/view#ticket={token}&view=1"
    title = ("Omarchy VM " if realm_id.startswith("v-") else "Realm ") + realm_id

    def body():
        try:
            client = live_view.RfbClient.connect(viewer.origin, realm_id, token)
            try:
                reason = live_view.run(client, title=title, browser_url=url,
                                       keep_alive=lambda: viewer.renew(realm_id, token, ttl=300),
                                       **run_options)
            finally:
                client.close()
        finally:
            viewer.tickets.discard(token)
        return "Realm view closed" if reason == "closed" else f"Realm view ended: {reason}"

    body.url = url
    return body
