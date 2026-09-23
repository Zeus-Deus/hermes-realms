"""A setup-job receipt, not a mailbox: resume intent never contains tool arguments."""
import json
from pathlib import Path


def capture_source(service, *, hermes_home=None, **identity):
    """Native lifecycle provenance; tool args and manual commands cannot mint it."""
    if (hermes_home is None or Path(hermes_home).resolve() != service.home
            or identity.get("surface") not in {"desktop", "tui"}
            or not all(identity.get(k) for k in ("stored_session_id", "runtime_session_id", "source"))):
        return
    owner = service.owners.resolve(**{k: identity.get(k) for k in
                                     ("session_id", "stored_session_id", "runtime_session_id")})
    with service._lock:
        _invalidate_changed_source(service, owner, identity)
        service._setup_request_sources[owner] = {
            k: identity[k] for k in ("stored_session_id", "runtime_session_id", "source", "surface")}


def _invalidate_changed_source(service, owner, identity):
    if not identity.get("source") or not identity.get("surface"):
        return
    # Ordinary idle sessions have no intent to revoke. Do not make them wait
    # behind an unrelated activation; observed intents still take the guarded
    # re-read below so contention cannot discard a source-change observation.
    with service.owners.connection() as db:
        row = db.execute("SELECT setup_intent FROM owners WHERE id=?", (owner,)).fetchone()
    if not row or not row[0]:
        return
    # Persist the observation, not just final-source equality: returning to the
    # old surface must not revive a retained request or an attached receipt.
    with service._lock, service.owners.activation_guard(owner), service.owners.connection() as db:
        row = db.execute("SELECT setup_intent FROM owners WHERE id=?", (owner,)).fetchone()
        intent = json.loads(row[0]) if row and row[0] else None
        if isinstance(intent, dict) and any(
                intent.get(k) != identity.get(k) for k in ("source", "surface")):
            db.execute(
                "UPDATE owners SET setup_intent=NULL, setup_generation=setup_generation+1 "
                "WHERE id=? AND setup_intent=?", (owner, row[0]))


def record_request(service, owner, identity):
    """Only a missing-prerequisite result from a native agent request can opt in."""
    source = service._setup_request_sources.get(owner)
    if (not identity.get("_agent") or not source
            or service.owners.mode(owner, None) == "host"):
        return
    intent = dict(source)
    intent.update(home=str(service.home), owner=owner, kind=service.owners.requested_kind(owner),
                  generation=service.owners.setup_generation(owner))
    with service.owners.connection() as db:
        db.execute("UPDATE owners SET setup_intent=? WHERE id=?", (json.dumps(intent), owner))


def attach_request(service, record):
    with service.owners.connection() as db:
        row = db.execute("SELECT setup_intent FROM owners WHERE id=?", (record["owner"],)).fetchone()
    intent = json.loads(row[0]) if row and row[0] else None
    if (intent and intent["kind"] == record["kind"]
            and intent["generation"] == record["activation_generation"] - 1):
        record["continuation"] = {"state": "pending", "intent": intent}


def retain_failed_request(service, record):
    """Carry only this unrevoked failed attempt's intent to the next generation."""
    receipt = record.get("continuation") or {}
    if record["state"] != "failed" or receipt.get("state") != "pending":
        return
    intent = receipt["intent"]
    owner, generation = record["owner"], record["activation_generation"]
    with service._lock, service.owners.activation_guard(owner):
        source = service._setup_request_sources.get(owner)
        if (service._unloaded or not source
                or any(intent.get(k) != v for k, v in source.items())
                or intent.get("home") != str(service.home) or intent.get("owner") != owner
                or intent["kind"] != record["kind"] or intent["generation"] != generation - 1
                or service.owners.permission(owner)["state"] != "optional"):
            return
        renewed = dict(intent, generation=generation)
        # Compare-and-swap, not a fresh request: Cancel/Disable or a newer
        # request must win even if they arrived while the installer was failing.
        with service.owners.connection() as db:
            db.execute(
                "UPDATE owners SET setup_intent=? WHERE id=? AND setup_generation=? "
                "AND setup_intent=? AND requested_kind=? AND mode IS NOT 'host'",
                (json.dumps(renewed), owner, generation, json.dumps(intent), record["kind"]))


def deliver(service, *, submit, hermes_home=None, **identity):
    from .setup_flow import _root, _read, _path, _job_guard, _operation
    from .lifecycle import atomic_json
    if hermes_home is None or Path(hermes_home).resolve() != service.home or service._unloaded:
        return
    owner = service.owners.resolve(**{k: identity.get(k) for k in
                                     ("session_id", "stored_session_id", "runtime_session_id")})
    _invalidate_changed_source(service, owner, identity)
    for path in _root(service).glob("*.json"):
        if len(path.stem) != 32:
            continue
        record = _read(service, path.stem)
        if _operation(record) != "session-setup" or record["owner"] != owner or record["state"] != "succeeded":
            continue
        with _job_guard(service, record["id"]), service._lock, service.owners.activation_guard(owner):
            record = _read(service, record["id"])
            if _operation(record) != "session-setup" or record["owner"] != owner:
                continue
            receipt = record.get("continuation") or {}
            if receipt.get("state") != "pending":
                continue
            from .permission_transition import require_optional
            from .integration import OwnerError
            intent = receipt["intent"]
            try:
                require_optional(service, owner)
                valid = (not service._unloaded and record["state"] == "succeeded"
                         and service.owners.mode(owner, None) == "realm"
                         and service.kind(owner) == record["kind"]
                         and service.owners.requested_kind(owner) == record["kind"]
                         and service.owners.setup_generation(owner) == record["activation_generation"]
                         and intent["source"] == identity.get("source")
                         and intent["surface"] == identity.get("surface"))
            except OwnerError:
                valid = False
            if not valid:
                receipt["state"] = "invalidated"
                atomic_json(path, record)
                continue
            # Write before submitting. A crash on either side of dispatch is
            # ambiguous and MUST NOT replay an already-started model turn.
            receipt["state"] = "uncertain"
            atomic_json(path, record)

            def terminal(outcome, job_id=record["id"]):
                with _job_guard(service, job_id):
                    current = _read(service, job_id)
                    current["continuation"]["state"] = outcome["status"]
                    atomic_json(_path(service, job_id), current)

            from concurrent.futures import CancelledError
            try:
                started = submit(
                    "Realm setup succeeded for this conversation. Reevaluate your current task and "
                    "continue the requested private test only if it is still needed and authorized. "
                    "Do not replay a previous click or command; inspect current state first.",
                    terminal_callback=terminal)
            except CancelledError:
                receipt["state"] = "cancelled"
                atomic_json(path, record)
                return
            if not started:
                receipt["state"] = "pending"
                atomic_json(path, record)
            return
