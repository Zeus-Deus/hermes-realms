"""One explicit decision for every held chat with no recorded Realm use.

``/realm review`` converts one earlier conversation after a native decision.
This applies the same conversion to every held conversation whose ownership
row and resource records show no Realm use, after one decision by the user:
the consented plugin setup step, ``/realm review unused`` or
``hermes realms review unused``. Missing metadata is still not treated as
freshness: the receipt records the decision and its scope, never ``fresh``.

"No recorded Realm use" is the load-failure scoping (``realm_state``): no
``realm`` mode, chosen or requested kind, setup intent, or ``r-``/``v-``
resource record. Everything else stays held for individual review.

The decision is remembered for this profile, so an earlier chat Realms first
sees afterwards (for example one reopened from before the plugin was
installed) is converted when it is bound, under the same criteria.
"""
import hashlib
import json
import os
import time

from .integration import OwnerError, OwnershipStore
from .permission_transition import CONTRACT
from .realm_state import STATE_COLUMNS, permission_state, record_owners

SCOPE = "no-recorded-realm-use"
PROVENANCES = frozenset({"setup-consent", "slash-command", "cli"})
TEXT = (
    "Earlier chats with no recorded Realm use run ordinary tools on their configured "
    "original backend with normal approvals, outside any Realm; a Realm stays available "
    "as a tool. Chats that used a Realm keep their individual /realm review. No guest is "
    "started, stopped, exported or deleted, no tool is replayed, and history is unchanged."
)


def _read(home):
    """(rows, aliases, decision) from a read-only snapshot; never writes the store."""
    from .selection_store import read_snapshot
    from .setup_plan import confined

    path = home / "realms" / "sessions.sqlite3"
    if not os.path.lexists(path):
        return [], {}, None
    for attempt in range(5):
        try:
            with read_snapshot(confined(home, path)) as db:
                columns = {row[1] for row in db.execute("PRAGMA table_info(owners)")}
                wanted = ("mode", "execution_contract", "permission_receipt", *STATE_COLUMNS)
                select = ", ".join(name if name in columns else "NULL" for name in wanted)
                rows = [dict(zip(("id", *wanted), row))
                        for row in db.execute(f"SELECT id, {select} FROM owners ORDER BY id")]
                aliases = {value: owner for value, owner in db.execute("SELECT value, owner FROM aliases")}
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                decision = None
                if "review_decisions" in tables:
                    row = db.execute("SELECT receipt FROM review_decisions WHERE scope=?", (SCOPE,)).fetchone()
                    decision = row[0] if row else None
            return rows, aliases, decision
        except ValueError:
            # A concurrent writer's rollback journal; the snapshot refuses rather than tears.
            if attempt == 4:
                raise
            time.sleep(0.1)


def _used(home, aliases):
    owners = set()
    for session in record_owners(home / "realms"):
        owners.add(aliases.get(session, session))
    return owners


def _valid_decision(home, raw):
    try:
        decision = json.loads(raw)
        return decision if decision["home"] == str(home) and decision["scope"] == SCOPE else None
    except (TypeError, ValueError, KeyError):
        return None


def preview(home):
    """Classify held owners without writing. The digest binds the reviewed scope."""
    home = OwnershipStore(home, readonly=True).root.parent
    rows, aliases, decision = _read(home)
    used = _used(home, aliases)
    eligible, kept = [], []
    for row in rows:
        owner = row["id"]
        state = permission_state(home, owner, row["execution_contract"], row["mode"], row["permission_receipt"])
        if state != "legacy-pending":
            continue
        if (row["mode"] == "realm" or any(row[name] is not None for name in STATE_COLUMNS)
                or owner in used or row["execution_contract"] not in (None, "legacy-held-v0")):
            kept.append(owner)
        else:
            eligible.append((owner, row["execution_contract"], row["mode"]))
    digest = hashlib.sha256(json.dumps(
        {"scope": SCOPE, "home": str(home), "eligible": eligible, "kept": kept, "text": TEXT},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"home": str(home), "eligible": eligible, "kept": kept, "digest": digest,
            "decision": _valid_decision(home, decision) if decision else None}


def _attached(owners, aliases_of):
    """True when this process holds an execution lease for one of the owner's names."""
    try:
        from hermes_cli.session_execution import resolve_session_execution_context
    except ImportError:
        return False  # a separate setup/CLI process holds no backend lease
    return any(resolve_session_execution_context(session_id=name) is not None
               for name in (owners, *aliases_of(owners)))


def owner_receipt(home, owner, old_contract, old_mode, digest, provenance):
    new_mode = old_mode if old_mode is not None else "ask"
    return new_mode, json.dumps({
        "contract": CONTRACT, "home": str(home), "owner": owner, "scope": SCOPE,
        "old_contract": old_contract, "old_mode": old_mode, "new_mode": new_mode,
        "target_state": {"host": "disabled"}.get(old_mode, "unselected"),
        "resources": [], "text": TEXT, "digest": digest, "provenance": provenance,
    }, sort_keys=True)


def release(home, *, provenance, expected=None, attached=None):
    """Convert every eligible held owner and remember the decision. Idempotent.

    ``expected`` is the digest the user reviewed; a changed scope refuses.
    """
    if provenance not in PROVENANCES:
        raise OwnerError("Bulk permission review requires an explicit user decision")
    store = OwnershipStore(home)
    home = store.root.parent
    review = preview(home)
    if expected is not None and expected != review["digest"]:
        raise OwnerError("The chats to release changed since they were reviewed; review again")
    if attached is None:
        attached = lambda owner: _attached(owner, store.aliases)
    # An attached old execution lease refuses conversion, as in /realm review.
    skipped = [owner for owner, *_ in review["eligible"] if attached(owner)]
    released = []
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        for owner, old_contract, old_mode in review["eligible"]:
            if owner in skipped:
                continue
            new_mode, receipt = owner_receipt(home, owner, old_contract, old_mode, review["digest"], provenance)
            # Re-check every criterion inside the write transaction.
            changed = db.execute(
                "UPDATE owners SET execution_contract=?, permission_receipt=?, mode=? "
                "WHERE id=? AND execution_contract IS ? AND mode IS ? AND permission_receipt IS NULL "
                "AND realm_kind IS NULL AND requested_kind IS NULL AND setup_intent IS NULL",
                (CONTRACT, receipt, new_mode, owner, old_contract, old_mode)).rowcount
            (released if changed == 1 else skipped).append(owner)
        if released or review["decision"] is None:
            # Keep an existing decision's digest stable when nothing changed.
            decision = json.dumps({"home": str(home), "scope": SCOPE, "digest": review["digest"],
                                   "provenance": provenance, "text": TEXT}, sort_keys=True)
            db.execute("INSERT OR REPLACE INTO review_decisions(scope, receipt) VALUES (?, ?)", (SCOPE, decision))
    held = len(review["kept"]) + len(skipped)
    return {
        "released": len(released), "held": held, "digest": review["digest"],
        "message": (f"Released {len(released)} earlier chats with no recorded Realm use: they run "
                    "ordinary tools on their configured backend, with a Realm available as a tool. "
                    f"{held} chats that used a Realm still need /realm review in that chat. "
                    "No tool was replayed and no guest was changed."),
    }


def adopt_on_bind(db, home, owner):
    """Apply a remembered decision to an earlier owner first bound after it.

    Runs inside ``OwnershipStore.bind``'s write transaction for a newly
    inserted, non-fresh row. Fails closed: any doubt leaves the row held.
    """
    try:
        row = db.execute("SELECT receipt FROM review_decisions WHERE scope=?", (SCOPE,)).fetchone()
        decision = _valid_decision(home, row[0]) if row else None
        if decision is None:
            return
        aliases = {value: target for value, target in db.execute("SELECT value, owner FROM aliases")}
        if owner in _used(home, aliases):
            return
        new_mode, receipt = owner_receipt(home, owner, None, None, decision["digest"], "remembered-decision")
        db.execute(
            "UPDATE owners SET execution_contract=?, permission_receipt=?, mode=? "
            "WHERE id=? AND execution_contract IS NULL AND mode IS NULL AND permission_receipt IS NULL "
            "AND realm_kind IS NULL AND requested_kind IS NULL AND setup_intent IS NULL",
            (CONTRACT, receipt, new_mode, owner))
    except Exception:
        return
