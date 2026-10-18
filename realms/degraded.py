"""Registration used when the Realms runtime cannot load.

Only conversations the ownership store records as using a Realm are paused:
their tools must never silently run on the host instead. Earlier
conversations the working runtime would hold for permission review stay held
too. Every other conversation keeps working. If the store cannot be read, nobody can be told
apart, so everything is paused as before. Nothing here writes to the store.
Standard library only; see realm_state.
"""
import json
import logging
import os
from pathlib import Path

from .realm_state import STATE_COLUMNS, permission_state, record_owners

STORAGE = (
    "Realm permission storage is unavailable. Execution is paused; recover the "
    "original ownership store and reopen this backend. No guest was changed."
)
IDENTITY = ("session_id", "runtime_session_id", "stored_session_id", "task_id")
SESSION_KEYS = ("session_id", "runtime_session_id", "stored_session_id")


def _cause(exc):
    return str(exc) or type(exc).__name__


def _remedy(exc):
    if isinstance(exc, ImportError):
        return ("Restart Hermes to finish enabling it; if this persists, run "
                "hermes pm repair and restart.")
    if isinstance(exc, ValueError) and "configuration" in str(exc):
        return "Fix plugins.realms in config.yaml, then restart Hermes."
    return ("Restart Hermes; if this persists, disable the plugin and report "
            "this error.")


class Degraded:
    def __init__(self, home, exc):
        self.home = Path(home).resolve()
        self.exc = exc
        logging.getLogger(__name__).error(
            "Realms could not load; pausing only conversations that use a Realm",
            exc_info=(type(exc), exc, exc.__traceback__))

    def load_error(self):
        return {
            "error": ("Realms could not load: " + _cause(self.exc) + ". " + _remedy(self.exc)
                      + " Conversations using a Realm are paused until then; other "
                        "conversations are unaffected. No guest was changed."),
            "error_code": "realms_load_failed",
        }

    def storage_error(self):
        return {"error": STORAGE + " Realms also could not load: " + _cause(self.exc) + ".",
                "error_code": "realms_store_unavailable"}

    def snapshot(self):
        """(alias -> owner, owners holding Realm state). Raises when unreadable."""
        from .selection_store import read_snapshot
        from .setup_plan import confined

        root = self.home / "realms"
        path = root / "sessions.sqlite3"
        owners = set(record_owners(root))
        aliases = {}
        if not os.path.lexists(path):
            if any(os.path.lexists(str(path) + suffix) for suffix in ("-journal", "-wal", "-shm")):
                raise ValueError("Ownership store journal requires explicit recovery")
            return aliases, owners
        with read_snapshot(confined(self.home, path)) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(owners)")}
            conditions = ["mode = 'realm'"] + [f"{name} IS NOT NULL" for name in STATE_COLUMNS if name in columns]
            owners.update(row[0] for row in db.execute("SELECT id FROM owners WHERE " + " OR ".join(conditions)))
            # Mirror the healthy middleware: a held earlier conversation stays
            # held. A failed load must never allow more than a working one.
            select = ", ".join(name if name in columns else "NULL"
                               for name in ("execution_contract", "permission_receipt"))
            for owner, mode, contract, receipt in db.execute(f"SELECT id, mode, {select} FROM owners"):
                if permission_state(self.home, owner, contract, mode, receipt) == "legacy-pending":
                    owners.add(owner)
            aliases = {(kind, value): owner for kind, value, owner in db.execute("SELECT kind,value,owner FROM aliases")}
        return aliases, owners

    def middleware(self, *, tool_name, args, next_call, **context):
        try:
            aliases, owners = self.snapshot()
            identity = [(key, context.get(key)) for key in IDENTITY if context.get(key)]
            if not identity:
                # Without an identity the caller could be any conversation.
                blocked = bool(owners)
            else:
                blocked = any(
                    aliases.get((key, value)) in owners or (key in SESSION_KEYS and value in owners)
                    for key, value in identity)
        except Exception:
            return json.dumps(self.storage_error())
        if blocked:
            return json.dumps(self.load_error())
        return next_call(args)

    def respond(self, *args, **identity):
        """The realm tool and /realm: report why Realms is unavailable."""
        try:
            self.snapshot()
        except Exception:
            return json.dumps(self.storage_error())
        return json.dumps(self.load_error())
