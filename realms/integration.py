"""Trusted session ownership and plugin integration; no focused-session globals."""

from contextlib import contextmanager
from pathlib import Path
import os
import sqlite3


class OwnerError(PermissionError):
    """The supplied identifiers do not name one registered conversation."""


class OwnershipStore:
    """Profile-local cross-process aliases, populated only by trusted host hooks."""

    def __init__(self, home, *, readonly=False):
        self.root = Path(home).resolve() / "realms"
        self.path = self.root / "sessions.sqlite3"
        self.readonly = readonly
        if readonly:
            return
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS owners (id TEXT PRIMARY KEY, mode TEXT);
                CREATE TABLE IF NOT EXISTS aliases (
                    kind TEXT NOT NULL, value TEXT NOT NULL, owner TEXT NOT NULL,
                    PRIMARY KEY(kind, value));
            """)
            db.execute("BEGIN IMMEDIATE")
            # Added after the initial schema: an existing profile's store has no
            # realm_kind column, and a migration is cheaper than a rebuild that
            # would orphan live realms.
            if "realm_kind" not in {
                row[1] for row in db.execute("PRAGMA table_info(owners)")
            }:
                db.execute("ALTER TABLE owners ADD COLUMN realm_kind TEXT")
            if "setup_generation" not in {
                row[1] for row in db.execute("PRAGMA table_info(owners)")
            }:
                db.execute("ALTER TABLE owners ADD COLUMN setup_generation INTEGER NOT NULL DEFAULT 0")
            if "requested_kind" not in {
                row[1] for row in db.execute("PRAGMA table_info(owners)")
            }:
                db.execute("ALTER TABLE owners ADD COLUMN requested_kind TEXT")
            for column in ("execution_contract", "permission_receipt", "setup_intent"):
                if column not in {row[1] for row in db.execute("PRAGMA table_info(owners)")}:
                    # NULL is intentional: old writers must not mint new permissions.
                    db.execute(f"ALTER TABLE owners ADD COLUMN {column} TEXT")
        self.path.chmod(0o600)

    @contextmanager
    def activation_guard(self, owner):
        """Serialize lease check/start with teardown, across backend processes.

        Keep SQLite transactions short: startup can take minutes, and an open
        write transaction would block unrelated owners and nested owner reads.
        Callers hold their integration's RLock before taking this owner lock.
        """
        import fcntl  # windows-footgun: ok — Linux-only plugin
        import hashlib
        from .setup_plan import confined

        path = confined(self.root.parent, self.root / (
            "activation-" + hashlib.sha256(owner.encode()).hexdigest() + ".lock"
        ))
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)  # windows-footgun: ok — Linux-only plugin
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def setup_generation(self, owner, *, revoke=False):
        with self.connection(readonly=not revoke) as db:
            if revoke:
                db.execute("UPDATE owners SET setup_generation=setup_generation+1 WHERE id=?", (owner,))
            row = db.execute("SELECT setup_generation FROM owners WHERE id=?", (owner,)).fetchone()
            if row is None:
                raise OwnerError("Unregistered setup owner")
            return row[0]

    @contextmanager
    def connection(self, *, readonly=None):
        readonly = self.readonly if readonly is None else readonly
        if readonly:
            from .setup_plan import confined
            from .selection_store import read_snapshot
            with read_snapshot(confined(self.root.parent, self.path)) as db:
                yield db
            return
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def identifiers(values):
        result = []
        for kind, value in values.items():
            if value is None or value == "":
                continue
            if kind not in (
                "session_id",
                "runtime_session_id",
                "stored_session_id",
                "task_id",
            ):
                raise OwnerError("Unsupported identity field")
            if (
                not isinstance(value, str)
                or value != value.strip()
                or "\0" in value
                or len(value) > 256
            ):
                raise OwnerError("Invalid session identity")
            result.append((kind, value))
        if not result:
            raise OwnerError("An active conversation is required")
        return result

    def bind(self, *, session_origin=None, **values):
        identifiers = self.identifiers(values)
        if not values.get("session_id"):
            raise OwnerError("A trusted conversation identity is required")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            owners = {
                row[0]
                for kind, value in identifiers
                if (
                    row := db.execute(
                        "SELECT owner FROM aliases WHERE kind=? AND value=?",
                        (kind, value),
                    ).fetchone()
                )
            }
            if len(owners) > 1:
                raise OwnerError("Conflicting session owners")
            owner = next(iter(owners)) if owners else values["session_id"]
            db.execute("INSERT OR IGNORE INTO owners(id,execution_contract) VALUES (?,?)",
                       (owner, "optional-targets-v1" if session_origin == "fresh" else None))
            db.executemany(
                "INSERT OR IGNORE INTO aliases(kind,value,owner) VALUES (?,?,?)",
                [(kind, value, owner) for kind, value in identifiers],
            )
            return owner

    def resolve(self, *, allow_missing=False, **values):
        identifiers = self.identifiers(values)
        with self.connection(readonly=True) as db:
            rows = [
                db.execute(
                    "SELECT owner FROM aliases WHERE kind=? AND value=?", (kind, value)
                ).fetchone()
                for kind, value in identifiers
            ]
        if allow_missing and all(row is None for row in rows):
            return None
        if any(row is None for row in rows) or len({row[0] for row in rows}) != 1:
            raise OwnerError("Unregistered or conflicting session owners")
        return rows[0][0]

    def permission(self, owner):
        with self.connection(readonly=True) as db:
            row = db.execute("SELECT execution_contract,mode,permission_receipt FROM owners WHERE id=?", (owner,)).fetchone()
        if row is None:
            raise OwnerError("Unregistered permission owner")
        contract, mode, receipt = row
        state = "optional" if contract == "optional-targets-v1" else (
            "legacy-host" if contract is None and mode == "host" else "legacy-pending")
        if receipt is not None:
            import json
            try:
                accepted = json.loads(receipt)
                if (accepted["home"] != str(self.root.parent) or accepted["owner"] != owner
                        or accepted["contract"] != contract):
                    state = "legacy-pending"
            except (ValueError, KeyError, TypeError):
                state = "legacy-pending"
        return {"state": state, "contract": contract, "stored_mode": mode, "receipt": receipt}

    def mode(self, owner, default):
        with self.connection(readonly=True) as db:
            row = db.execute("SELECT mode FROM owners WHERE id=?", (owner,)).fetchone()
        return row[0] or default if row else default

    def kind(self, owner, default):
        with self.connection(readonly=True) as db:
            row = db.execute(
                "SELECT realm_kind FROM owners WHERE id=?", (owner,)
            ).fetchone()
        return row[0] or default if row else default

    def aliases(self, owner):
        with self.connection(readonly=True) as db:
            return tuple(
                row[0]
                for row in db.execute(
                    "SELECT DISTINCT value FROM aliases WHERE owner=?", (owner,)
                )
            )

    def set_mode(self, owner, mode):
        if mode not in ("realm", "host", "ask"):
            raise ValueError("Invalid realm mode")
        with self.connection() as db:
            if mode == "host":
                # Disable cannot confer ordinary execution on a held legacy owner.
                db.execute("UPDATE owners SET execution_contract='legacy-held-v0' "
                           "WHERE id=? AND execution_contract IS NULL AND mode IS NOT 'host'", (owner,))
            db.execute("UPDATE owners SET mode=? WHERE id=?", (mode, owner))

    def set_kind(self, owner, kind):
        from .config import KINDS

        if kind not in KINDS:
            raise ValueError("Invalid realm kind")
        with self.connection() as db:
            db.execute("UPDATE owners SET realm_kind=? WHERE id=?", (kind, owner))

    def requested_kind(self, owner):
        with self.connection(readonly=True) as db:
            row = db.execute("SELECT requested_kind FROM owners WHERE id=?", (owner,)).fetchone()
        return row[0] if row else None

    def request_kind(self, owner, kind):
        from .config import KINDS
        if kind not in KINDS:
            raise ValueError("Invalid requested realm kind")
        with self.connection() as db:
            db.execute("UPDATE owners SET requested_kind=? WHERE id=?", (kind, owner))


class SetupError(ValueError):
    """Missing prerequisites, safe to expose with administrative recovery steps."""


def setup_status(*, driver_executable=None):
    import shutil
    import sys

    from .config import driver_path
    from .install_driver import execution_verified

    driver = driver_executable if driver_executable is not None else driver_path()
    missing = []
    if sys.platform != "linux":
        missing.append("Linux")
    if not execution_verified(driver):
        missing.append("pinned Cua driver (run hermes realms install-driver in this profile)")
    missing.extend(
        name for name in (
                "labwc",
                "Xwayland",
                "wayvnc",
                "dbus-daemon",
                "systemd-run",
                "systemd-inhibit",
                "grim",
                "wlr-randr",
                "gdbus",
                "bwrap",
        ) if not shutil.which(name)
    )
    missing.extend(
        path for path in ("/usr/lib/at-spi-bus-launcher", "/usr/lib/at-spi2-registryd")
        if not os.access(path, os.X_OK)
    )
    return {
        "ready": not missing,
        "message": ("Realms setup required: " + "; ".join(missing)
                    + ". Install missing system packages with your distribution's package manager; "
                    "then run hermes realms doctor in this profile; host fallback is disabled.")
        if missing else "Realms dependencies are available.",
    }


def requirements_available(*, driver_executable=None):
    return setup_status(driver_executable=driver_executable)["ready"]


def vm_setup_status(home=None):
    """Readiness of the Omarchy VM kind, reported separately from the labwc one.

    A profile with a working labwc realm and no VM base image is *ready* — it
    just cannot use the VM kind yet. Folding the two together would block the
    default realm on a 5 GB download nobody asked for.
    """
    from .vm_manager import VmManager

    try:
        report = VmManager(home).doctor()
    except (ValueError, OSError) as exc:
        return {"ready": False, "message": "Omarchy VM realms unavailable: " + str(exc)}
    if report["ok"]:
        return {"ready": True, "message": "Omarchy VM realm dependencies are available."}
    return {
        "ready": False,
        "message": (
            "Omarchy VM realm setup required: "
            + "; ".join(report["missing"])
            + ". Install missing system packages with your distribution's package "
            "manager, then run hermes realms vm install to build the base image; "
            "host fallback is disabled."
        ),
    }


# Profile-keyed infrastructure only; current/focused identity never lives here.
_services = {}


def get_integration(home=None):
    from hermes_constants import get_hermes_home

    home = Path(home if home is not None else get_hermes_home()).resolve()
    if home not in _services:
        _services[home] = RealmIntegration(home)
    return _services[home]


# Keys stripped even if absent at registration: later ambient/shell snapshots
# must not resurrect a host display, portal, bus, driver or input endpoint.
HOST_ENV_KEYS = frozenset(
    {
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS",
        "DBUS_STARTER_ADDRESS",
        "DBUS_STARTER_BUS_TYPE",
        "AT_SPI_BUS_ADDRESS",
        "HYPRLAND_INSTANCE_SIGNATURE",
        "HYPRLAND_CMD",
        "SWAYSOCK",
        "I3SOCK",
        "YDOTOOL_SOCKET",
        "CUA_INJECT_SOCKET",
        "CUA_DRIVER_SOCKET",
        "CUA_DRIVER_RS_SOCKET",
        "CUA_DRIVER_RS_SESSION",
        "CUA_DRIVER_RS_STATE_DIR",
        "CUA_DRIVER_RS_PERMISSION_MODE",
        "CUA_DRIVER_PERMISSION_MODE",
        "CUA_DRIVER_RS_BYPASS_APPROVALS",
        "SESSION_MANAGER",
        "XAUTHORITY",
        "DESKTOP_STARTUP_ID",
        "XDG_ACTIVATION_TOKEN",
        "GTK_USE_PORTAL",
        "GTK_MODULES",
        "GTK3_MODULES",
        "GIO_EXTRA_MODULES",
        "QT_QPA_PLATFORMTHEME",
        "QT_PLUGIN_PATH",
        "GTK_PATH",
        "GDK_DEBUG",
        "PIPEWIRE_REMOTE",
        "PULSE_SERVER",
        "SSH_AUTH_SOCK",
        "GPG_AGENT_INFO",
    }
)


class RealmIntegration:
    def __init__(self, home):
        from .manager import Manager
        import threading

        self.manager = Manager(home)
        self.home = self.manager.home
        self.owners = OwnershipStore(self.home)
        from .config import driver_path

        self.driver_executable = driver_path(self.home)
        self._attachments = {}
        self._target_contexts = {}
        self._target_disposers = []
        self._lock = threading.RLock()
        from .windows import WindowCounter

        self._window_counter = WindowCounter()
        self._vm = None
        self._activation_config = None
        self._setup_owners = set()
        self._setup_request_sources = {}
        self._unloaded = False
        import uuid
        self._permission_backend = uuid.uuid4().hex

    @property
    def vm(self):
        """The VM kind's manager, built on first use.

        Deferred because the labwc realm must keep working on a host with no
        QEMU at all, and because reading VM config costs a config load that
        every non-VM session would otherwise pay.
        """
        with self._lock:
            from .config import Config
            from .vm_manager import VmManager

            config = self._activation_config or Config.load(self.home)
            if self._vm is None or self._vm.config != config:
                self._vm = VmManager(self.home)
                # The constructor may observe a newer config. Only the captured
                # snapshot governs this startup; never retain a removed override.
                self._vm.config = config
            return self._vm

    def reserve_setup(self, owner):
        from .permission_transition import require_optional
        require_optional(self, owner)
        with self._lock, self.owners.activation_guard(owner):
            if self._unloaded:
                raise OwnerError("Realms integration has been unloaded")
            generation = self.owners.setup_generation(owner, revoke=True)
            self._setup_owners.add(owner)
            return generation

    def finish_setup(self, owner):
        with self._lock:
            self._setup_owners.discard(owner)

    def activate_setup(self, owner, kind, config, generation, identity):
        """Eager setup activation with revocable authority and a frozen config.

        Unlike plain /realm on, setup success means an owned desktop exists.
        The owner lock covers both the lease check and the complete activation;
        off/finalize either revoke first or wait and tear the new desktop down.
        """
        from .config import Config

        expected = Config(**config)
        with self._lock, self.owners.activation_guard(owner):
            from .permission_transition import require_optional
            require_optional(self, owner)
            if self._unloaded or self.owners.setup_generation(owner) != generation:
                raise OwnerError("Setup activation was revoked")
            if self.owners.resolve(session_id=owner, **identity) != owner:
                raise OwnerError("Setup ownership changed")
            if Config.load(self.home) != expected:
                raise SetupError("Setup configuration changed; prepare again")
            if kind == "omarchy-vm" and expected.vm.omarchy_vm_path:
                raise SetupError("Setup requires the pinned vendored VM installer")
            previous_config = self.manager.config
            self._activation_config = expected
            self.manager.config = expected
            try:
                self._activate(owner, [kind], eager=True)
            finally:
                self.manager.config = previous_config
                self._activation_config = None

    def kind(self, owner):
        return self.owners.kind(owner, self.manager.config.default_kind)

    def select_computer_use_target(self, *, session_id, task_id=None):
        from .target_contexts import select_for
        return select_for(self, "cua", session_id=session_id, task_id=task_id)

    def select_terminal_target(self, *, command, session_id, task_id=None):
        from .target_contexts import select_for
        return select_for(self, "terminal", command=command, session_id=session_id, task_id=task_id)

    def computer_use_context(self, *, session_id, task_id=None):
        """Resolve a private CUA target without replacing caller identity."""
        from .target_contexts import context_for
        owner = self.bind(session_id=session_id, task_id=task_id)
        return context_for(self, owner, "cua")

    def terminal_context(self, *, command, session_id, task_id=None):
        """Explicit named-target commands retain the canonical terminal policy."""
        from .target_contexts import context_for, _access_epoch
        from .host_guard import host_escape
        from hermes_cli.session_execution import SessionExecutionError
        owner = self.bind(session_id=session_id, task_id=task_id)
        if self.kind(owner) == "realm" and (reason := host_escape(command)):
            raise SessionExecutionError(reason)
        context = context_for(self, owner, "terminal")
        record = self._attachments[owner]
        _access_epoch(self, owner, record[2])
        return context

    def execution_middleware(self, **kwargs):
        from .permission_transition import middleware
        return middleware(self, **kwargs)

    def bind(self, *, hermes_home=None, profile=None, surface=None, session_origin=None, **identity):
        if hermes_home is not None and Path(hermes_home).resolve() != self.home:
            raise OwnerError("Profile ownership mismatch")
        if session_origin == "fresh":
            from .permission_transition import _resources
            if _resources(self, identity.get("session_id")):
                session_origin = "resume"
        return self.owners.bind(
            session_origin=session_origin,
            **{
                key: identity.get(key)
                for key in (
                    "session_id",
                    "stored_session_id",
                    "runtime_session_id",
                    "task_id",
                )
            }
        )

    def records(self, owner):
        return [
            record for record in self.manager.list() if record["session_id"] == owner
        ]

    def vm_records(self, owner):
        return [r for r in self.vm.list() if r["session_id"] == owner]

    def _running_records(self, owner):
        records = self.vm_records(owner) if self.kind(owner) == "omarchy-vm" else self.records(owner)
        return [record for record in records if record.get("status") == "running"]

    def status(self, owner):
        from .permission_transition import held_status
        permission = self.owners.permission(owner)
        if permission["state"] != "optional":
            return held_status(self, owner, permission)
        from .bridge import get_profile_viewer

        viewer = get_profile_viewer(self.home)
        rows = [
            {
                "id": r["id"],
                "kind": "realm",
                "state": "live" if r["status"] == "running" else r["status"],
                "size": r["size"],
                "window_count": self._window_counter.count(r) if r["status"] == "running" else None,
                "controlled": viewer.is_controlled(r["id"]),
                "error": r.get("cleanup_note"),
            }
            for r in self.records(owner)
        ]
        kind = self.kind(owner)
        if kind == "omarchy-vm" or self._vm is not None:
            try:
                rows.extend(
                    {
                        "id": r["id"],
                        "kind": "omarchy-vm",
                        "state": "live" if r["status"] == "running" else r["status"],
                        "size": None,
                        "window_count": None,
                        "controlled": viewer.is_controlled(r["id"]),
                        "stats": self.vm.stats(r["id"]) if r["status"] == "running" else None,
                        "memory_mb": r.get("memory"),
                        "network": r.get("network"),
                        "error": r.get("recovery_reason"),
                    }
                    for r in self.vm_records(owner)
                )
            except (OwnerError, ValueError, OSError):
                # A status read must never take down the labwc rows with it.
                pass
        requested = self.owners.requested_kind(owner)
        return {
            "requested": requested is not None or bool(rows),
            "permission": permission,
            "requested_kind": requested or (kind if rows else None),
            "mode": self.owners.mode(owner, self.manager.config.default_mode),
            "kind": kind,
            "realms": rows,
            "setup": setup_status(driver_executable=self.driver_executable),
            "vm_setup": vm_setup_status(self.home) if "omarchy-vm" in (kind, requested) else None,
        }

    def watch(self, owner, realm_id):
        from .bridge import get_profile_viewer
        from .lifecycle import validate_live

        record = next((r for r in self.records(owner) if r["id"] == realm_id), None)
        if record is None:
            # A VM realm's viewer is QEMU's own VNC socket, which speaks the same
            # RFB the bridge already proxies for wayvnc.
            record = next(
                (r for r in self.vm_records(owner) if r["id"] == realm_id), None
            )
            if record is None:
                raise OwnerError("Realm ownership mismatch")
            self.vm.validate(realm_id)
        else:
            validate_live(record)
        viewer = get_profile_viewer(self.home).start()
        token = viewer.issue(realm_id, can_control=True, ttl=300)
        return {"url": viewer.origin + "/realms/" + realm_id + "/view#ticket=" + token}

    USAGE = (
        "Use /realm on [omarchy]|off|status|review|size WIDTHxHEIGHT|stop|repair|watch|shot"
        "|push SOURCE [DEST]|pull GUEST_PATH LOCAL_PATH"
    )

    def command(self, raw, **identity):
        import shlex

        owner = self.bind(**identity)
        parts = shlex.split(raw)
        action = parts[0] if parts else "status"
        if action not in ("status", "stop", "watch", "off", "review"):
            from .permission_transition import require_optional
            require_optional(self, owner)
        handler = self._COMMANDS.get(action)
        if handler is None:
            raise ValueError(self.USAGE)
        result = handler(self, owner, parts[1:], identity)
        return self.status(owner) if result is None else result

    def _command_on(self, owner, arguments, identity):
        """``/realm on`` selects private routing; ``/realm on omarchy`` its kind.

        The kind is a property of this conversation, so a later plain
        ``/realm on`` in the same chat keeps the kind that was chosen.
        """
        with self._lock, self.owners.activation_guard(owner):
            # An explicit stored off differs from an unset profile default.
            from .permission_transition import require_optional
            require_optional(self, owner)
            # Check under the same lock as Disable, before revoking setup intent.
            if identity.get("_agent") and self.owners.mode(owner, None) == "host":
                raise OwnerError(
                    "Realm use is disabled for this conversation. The user must "
                    "re-enable it with /realm on or the desktop setup controls; "
                    "ordinary tools remain available."
                )
            self.owners.setup_generation(owner, revoke=True)
            try:
                self._activate(owner, arguments)
            except SetupError:
                from .setup_continuation import record_request
                record_request(self, owner, identity)
                raise

    def _activate(self, owner, arguments, *, eager=False):
        from .config import Config, KINDS

        if len(arguments) > 1:
            raise ValueError(self.USAGE)
        with self._lock:
            previous_kind = self.kind(owner)
            previous_mode = self.owners.mode(owner, self.manager.config.default_mode)
            requested = {"omarchy": "omarchy-vm"}.get(arguments[0], arguments[0]) if arguments else previous_kind
            if requested not in KINDS:
                raise ValueError("Use /realm on [omarchy]")

            self.owners.request_kind(owner, requested)

            # Check before retiring the old desktop or publishing a new kind.
            setup = vm_setup_status(self.home) if requested == "omarchy-vm" else setup_status(driver_executable=self.driver_executable)
            if not setup["ready"]:
                raise SetupError(setup["message"])
            if requested != previous_kind:
                self._stop(owner)
            self.owners.set_kind(owner, requested)
            self.owners.set_mode(owner, "realm")
            try:
                if requested == "omarchy-vm" or eager:
                    # Setup eagerly starts both kinds; plain native /realm on
                    # still selects lazy allocation for its first eligible tool.
                    self.ready(owner)
                if eager and Config.load(self.home) != self._activation_config:
                    raise SetupError("Setup configuration changed during startup; prepare again")
            except BaseException:
                try:
                    self._stop(owner)
                finally:
                    self.owners.set_kind(owner, previous_kind)
                    self.owners.set_mode(owner, previous_mode)
                raise

    def _command_off(self, owner, arguments, identity):
        if arguments:
            raise ValueError(self.USAGE)
        with self._lock, self.owners.activation_guard(owner):
            self.owners.setup_generation(owner, revoke=True)
            self.owners.set_mode(owner, "host")
            from .target_contexts import release_targets
            # Disable agent use, not the human's desktop or its workspace.
            release_targets(self, owner)

    def _command_stop(self, owner, arguments, identity):
        self.stop(owner)

    def _command_status(self, owner, arguments, identity):
        return None

    def _command_review(self, owner, arguments, identity):
        from .permission_transition import review_manual
        if arguments or identity.get("_agent"):
            raise OwnerError("Permission conversion requires manual native review")
        return review_manual(self, owner, identity)

    def _command_repair(self, owner, arguments, identity):
        from .target_contexts import _access_epoch, release_targets

        if arguments:
            raise ValueError("Use /realm repair (CUA connection only)")
        with self._lock:
            records = self._running_records(owner)
            if not records:
                raise ValueError("Realm is not running; repair never creates a replacement")
            for record in records:
                _access_epoch(self, owner, record["id"])
            release_targets(self, owner, purpose="cua")
        return {"message": "CUA connection reset; next computer-use reconnects"}

    def _command_size(self, owner, arguments, identity):
        from .target_contexts import _access_epoch

        if len(arguments) != 1:
            raise ValueError("Use /realm size WIDTHxHEIGHT")
        with self._lock, self.owners.activation_guard(owner):
            if self.kind(owner) != "realm":
                raise ValueError("Resize is available only for a selected regular Realm")
            records = self._running_records(owner)
            if not records:
                raise ValueError(
                    "Realm is not running; enable it and run a desktop action first"
                )
            _access_epoch(self, owner, records[0]["id"])
            self.manager.resize(records[0]["id"], arguments[0])

    def _command_shot(self, owner, arguments, identity):
        from .target_contexts import _access_epoch

        if arguments:
            raise ValueError("Use /realm shot (own-session capture only)")
        import hashlib
        import shutil
        import struct
        import tempfile

        records = self._running_records(owner)
        if not records:
            raise ValueError("Realm is not running; host fallback is disabled")
        realm_id = records[0]["id"]
        epoch = _access_epoch(self, owner, realm_id)
        capture = "grim"
        if self.kind(owner) == "realm":
            source = self.manager
            self.manager.env(realm_id)  # Validate before creating any artifact.
        else:
            source = self.vm
            self.vm.validate(realm_id)
            capture = "grim-in-guest"
        directory = Path(
            tempfile.mkdtemp(prefix="shot-", dir=self.manager.registry.root)
        )
        try:
            target = Path(source.shot(realm_id, directory / "screen.png"))
            target.chmod(0o600)
            data = target.read_bytes()
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError(
                    "Realm capture did not return a PNG; host fallback is disabled"
                )
            width, height = struct.unpack(">II", data[16:24])
            result = {
                "realm_id": realm_id,
                "path": str(target),
                "mime_type": "image/png",
                "width": width,
                "height": height,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "capture": capture,
                "fallback": True,
            }
            if _access_epoch(self, owner, realm_id) != epoch:
                raise PermissionError("Realm control changed during capture; pixels discarded")
            return result
        except BaseException:
            shutil.rmtree(directory)
            raise

    def _command_watch(self, owner, arguments, identity):
        records = self._running_records(owner)
        if not records:
            raise ValueError("Realm is not running")
        if identity.get("surface") == "gateway":
            raise ValueError(
                "Remote viewers require an explicit viewer tunnel; use the local desktop Watch action"
            )
        return self.watch(owner, records[0]["id"])

    def _command_push(self, owner, arguments, identity):
        """Copy a host path into the VM guest. Copying IS the boundary."""
        from .target_contexts import _access_epoch

        if not 1 <= len(arguments) <= 2:
            raise ValueError("Use /realm push SOURCE [GUEST_PATH]")
        with self._lock, self.owners.activation_guard(owner):
            record = self._vm_record(owner)
            _access_epoch(self, owner, record["id"])
            return self.vm.push(record["id"], arguments[0], *arguments[1:])

    def _command_pull(self, owner, arguments, identity):
        from .target_contexts import _access_epoch

        if len(arguments) != 2:
            raise ValueError("Use /realm pull GUEST_PATH LOCAL_PATH")
        with self._lock, self.owners.activation_guard(owner):
            record = self._vm_record(owner)
            _access_epoch(self, owner, record["id"])
            return self.vm.pull(record["id"], arguments[0], arguments[1])

    def _vm_record(self, owner):
        records = self._running_records(owner) if self.kind(owner) == "omarchy-vm" else []
        if not records:
            raise ValueError(
                "No Omarchy VM realm is running for this conversation; "
                "use /realm on omarchy first"
            )
        return records[0]

    _COMMANDS = {
        "on": _command_on,
        "off": _command_off,
        "stop": _command_stop,
        "repair": _command_repair,
        "status": _command_status,
        "review": _command_review,
        "size": _command_size,
        "shot": _command_shot,
        "watch": _command_watch,
        "push": _command_push,
        "pull": _command_pull,
    }

    def _valid(self, record, owner):
        from .lifecycle import validate_live

        if self.owners.mode(owner, self.manager.config.default_mode) != "realm":
            return False
        current = next(
            (r for r in self.records(owner) if r["id"] == record["id"]), None
        )
        if current is None or current["generation"] != record["generation"]:
            return False
        validate_live(current)
        return True

    def ready(self, owner, *, before_start=None):
        """Start or reuse a private target without changing parent execution."""
        from .permission_transition import require_optional
        require_optional(self, owner)
        if self.owners.permission(owner)["receipt"] and self.owners.mode(owner, None) != "realm":
            raise OwnerError("Private testing is disabled or unselected; explicitly select it before target use")
        requested = self.owners.requested_kind(owner)
        if requested is not None and requested != self.kind(owner):
            raise SetupError("The requested target is not active; complete its setup or explicitly select another kind")
        if requested is None:
            self.owners.request_kind(owner, self.kind(owner))
        if self.kind(owner) == "omarchy-vm":
            return self._ready_vm(owner, before_start=before_start)
        with self._lock:
            if not self._running_records(owner):
                setup = setup_status(driver_executable=self.driver_executable)
                if not setup["ready"]:
                    raise SetupError(setup["message"])
            if before_start is not None:
                before_start()
            record = self.manager.start(owner, **({"before_start": before_start} if before_start else {}))
            self._attachments[owner] = (record["generation"], (), record["id"])
            return record

    def _vm_valid(self, record, owner):
        from .vm_manager import VmError
        from .target_contexts import compute_identity

        if self.owners.mode(owner, self.manager.config.default_mode) != "realm":
            return False
        if self.kind(owner) != "omarchy-vm":
            return False
        try:
            current = self.vm.validate(record["id"])
        except (OwnerError, VmError, OSError):
            return False
        return compute_identity(current) == compute_identity(record)

    def _ready_vm(self, owner, *, before_start=None):
        """VM lifetime belongs to the backend; ordinary tools stay on the host."""
        def before_allocate():
            setup = vm_setup_status(self.home)
            if not setup["ready"]:
                raise SetupError(setup["message"])

        with self._lock:
            if before_start is not None:
                before_start()
            record = self.vm.start(owner, before_allocate=before_allocate,
                                   **({"before_start": before_start} if before_start else {}))
            self._attachments[owner] = (record["generation"], (), record["id"])
            return record

    def input_allowed(self, realm_id):
        # A shared profile viewer authority must be available before input.
        from .bridge import get_profile_viewer

        return not get_profile_viewer(self.home).is_controlled(realm_id)

    def stop(self, owner):
        # Legacy owners may predate activation locks. Refuse before creating
        # one, then recheck after acquiring it before any authority change.
        self.manager.preflight_stop(owner)
        with self._lock, self.owners.activation_guard(owner):
            self.manager.preflight_stop(owner)
            self.owners.setup_generation(owner, revoke=True)
            self._stop(owner)

    def _stop(self, owner):
        """Teardown while the caller holds the owner activation guard."""
        from .target_contexts import release_targets

        with self._lock:
            records = self.manager.preflight_stop(owner)
            release_targets(self, owner)
            self._attachments.pop(owner, None)
            for record in records:
                from .bridge import get_profile_viewer

                get_profile_viewer(self.home).revoke(record["id"])
                self.manager.stop(record["id"])
            if self._vm is None and self.kind(owner) != "omarchy-vm":
                return
            from .vm_manager import VmError

            try:
                for record in self.vm_records(owner):
                    from .bridge import get_profile_viewer

                    get_profile_viewer(self.home).revoke(record["id"])
                    self.vm.stop(record["id"])
            except (VmError, OwnerError, OSError, ValueError):
                import logging

                logging.getLogger(__name__).exception("VM realm teardown failed")

    def reset(self, **identity):
        """A context reset is not a teardown boundary — keep the realm.

        ``on_session_reset`` means "same session, fresh context" (``/new``,
        ``/clear``, or simply the gateway building this session's agent). The
        realm was asked for explicitly and may hold minutes of work, so it
        outlives the transcript that happens to be in front of it; only
        ``finalize`` ends it. Reaping is never lost by keeping it: real
        finalize, the per-guest systemd owner watcher, and idle expiry all
        still apply.

        Reconciliation is the one thing worth doing here, and only when a
        ``VmManager`` already exists: a reset on a labwc-only host must not be
        what finally constructs one, because the ``vm`` property is deferred
        precisely so a host with no QEMU never pays for it.
        """
        if self._vm is None:
            return
        from .vm_manager import VmError

        try:
            self.vm.list()
        except (VmError, OwnerError, OSError, ValueError):
            import logging

            logging.getLogger(__name__).warning(
                "VM realm reconciliation on reset failed", exc_info=True)

    def finalize(self, **identity):
        keys = {
            key: identity.get(key)
            for key in (
                "session_id",
                "stored_session_id",
                "runtime_session_id",
                "task_id",
            )
        }
        owner = self.owners.resolve(allow_missing=True, **keys)
        if owner is not None:
            with self._lock:
                self.stop(owner)
                self._setup_request_sources.pop(owner, None)

    def unload(self):
        from .bridge import close_profile_viewer

        with self._lock:
            # Closing the shared viewer affects even unattached recovery rows.
            # Refuse the entire unload before any owner's authority is revoked.
            self.manager.preflight_stop()
            self._unloaded = True
            try:
                # An installer may not have attached a desktop yet. Revoke those
                # pending owners too; aliases intentionally outlive unloading.
                for owner in self._setup_owners | set(self._attachments):
                    self.stop(owner)
            finally:
                for dispose in self._target_disposers:
                    dispose()
                self._target_disposers.clear()
                if _services.get(self.home) is self:
                    del _services[self.home]
                close_profile_viewer(self.home)

    def pre_tool(self, *, tool_name, args, **identity):
        # Registration/discovery must not allocate a target or retarget normal
        # terminal/file work. Slow target startup runs at the explicit tool body.
        if tool_name in ("computer_use", "realm"):
            self.bind(**identity)
        return None
