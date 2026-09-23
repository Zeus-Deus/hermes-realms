"""Cold permission conversion, never compute adoption or data disposition."""
import json
from pathlib import Path

from .integration import OwnerError

CONTRACT = "optional-targets-v1"
HOLD = (
    "This conversation's earlier Realm permissions have not been converted. "
    "Execution is paused; no host or guest operation was started. Review Use optional "
    "targets in this conversation's Realm controls, or run /realm review manually. "
    "Legacy guest work has not been moved or stopped."
)
# Envelopes are deliberately NOT allowed: nested status + execution is execution.
DISCOVERY = frozenset({"clarify", "tool_search", "tool_describe", "skills_list", "skill_view"})


def middleware(service, *, tool_name, args, next_call, session_id=None, task_id=None, **context):
    try:
        from hermes_constants import get_hermes_home
        if Path(get_hermes_home()).resolve() != service.home:
            raise OwnerError("Profile ownership mismatch")
        if session_id:
            service.owners.resolve(session_id=session_id)
            # Dispatch IDs are trusted metadata, not arguments. New turn task
            # aliases inherit a known owner's authority, including its hold.
            # This also keeps clarification usable after a cold resume.
            service.owners.bind(session_id=session_id, task_id=task_id)
        owner = service.owners.resolve(session_id=session_id, task_id=task_id)
        state = service.owners.permission(owner)["state"]
        allowed = state in ("optional", "legacy-host") or tool_name in DISCOVERY or (
            tool_name == "realm" and args == {"action": "status"})
    except Exception:
        # Core middleware intentionally skips throwing plugins. Return the denial here.
        allowed = False
    if not allowed:
        return json.dumps({"error": HOLD, "error_code": "legacy_permission_review_required"})
    return next_call(args)


def require_optional(service, owner):
    if service.owners.permission(owner)["state"] != "optional":
        raise OwnerError(HOLD)


def _resources(service, owner, *, include_record=False):
    """Bounded non-reconciling observations; never Manager.list or VM adoption."""
    import hashlib
    import os
    import re
    import stat
    rows = []
    root = service.home / "realms"
    if not root.exists():
        return rows
    with os.scandir(root) as entries:
        for index, entry in enumerate(entries):
            if index >= 1024:
                raise OwnerError("Too many resource records to review safely")
            if not re.fullmatch(r"[rv]-[0-9a-f]{24}\.json", entry.name):
                continue
            fd = os.open(entry.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 1024 * 1024:  # windows-footgun: ok — runtime package rejects non-Linux hosts
                    raise OwnerError("Resource record cannot be safely reviewed")
                with os.fdopen(fd, "rb", closefd=False) as stream:
                    raw = stream.read(1024 * 1024 + 1)
            finally:
                os.close(fd)
            record = json.loads(raw)
            if record.get("session_id") != owner:
                continue
            if record.get("home") != str(service.home) or record.get("id") != entry.name[:-5]:
                raise OwnerError("Resource ownership changed")
            rows.append({"id": record["id"], "kind": "realm" if entry.name.startswith("r-") else "omarchy-vm",
                         "state": record.get("status", "unknown"), "digest": hashlib.sha256(raw).hexdigest(),
                         "warning": "Guest work is not exported by permission conversion. Legacy volatile work may be lost on old cleanup or host shutdown."})
            if include_record:
                rows[-1]["record"] = record
    return sorted(rows, key=lambda row: row["id"])


def prepare(service, owner, identity):
    import hashlib
    from hermes_cli.session_execution import resolve_session_execution_context
    if service.owners.resolve(**identity) != owner:
        raise OwnerError("Permission review ownership changed")
    permission = service.owners.permission(owner)
    if permission["state"] == "optional":
        raise OwnerError("Permission review already accepted; refresh status")
    if permission["contract"] not in (None, "legacy-held-v0"):
        raise OwnerError("Permission metadata is unrecognized or belongs to another profile; recover the original ownership store")
    for alias in (owner, *service.owners.aliases(owner)):
        if resolve_session_execution_context(session_id=alias) is not None:
            raise OwnerError("Legacy execution is still attached. Finish/export legacy work and reopen on the new runtime; no lease was removed.")
    target_state = {"host": "disabled", "realm": "selected"}.get(permission["stored_mode"], "unselected")
    new_mode = permission["stored_mode"] if permission["stored_mode"] is not None else "ask"
    text = (
        "Ordinary terminal, files and research will run on this conversation's configured original backend "
        "with its normal cwd, authentication and approvals — not necessarily this Desktop's machine. "
        "Only explicitly targeted operations run in a Realm or VM; physical-desktop access is not granted. "
        "This replaces the earlier realm/ask routing permission for this conversation. "
        "No guest is stopped, deleted, exported, adopted or restarted. No pending tool is replayed. "
        "Earlier prompts and history are preserved unchanged. Target use after conversion: " + target_state + ". "
        "Explicit Disable is preserved. Continue only with a new explicit tool attempt."
    )
    scope = {"contract": CONTRACT, "home": str(service.home), "owner": owner,
             "identity": {k: v for k, v in identity.items() if v is not None},
             "old_mode": permission["stored_mode"], "old_contract": permission["contract"],
             "new_mode": new_mode,
             "target_state": target_state, "kind": service.owners.kind(owner, None),
             "requested_kind": service.owners.requested_kind(owner),
             "generation": service.owners.setup_generation(owner), "resources": _resources(service, owner),
             "backend": service._permission_backend, "text": text}
    digest = hashlib.sha256(json.dumps(scope, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {**scope, "digest": digest}


def accept_native(service, owner, identity, digest, *, provenance):
    """Called only by authenticated native REST or the manual elicitation handler.

    The digest detects scope drift, not user approval. Do not register this as a tool.
    """
    import hmac
    if provenance not in ("desktop", "manual-elicitation"):
        raise OwnerError("Native permission acceptance is required")
    with service._lock, service.owners.activation_guard(owner):
        review = prepare(service, owner, identity)
        if not isinstance(digest, str) or not hmac.compare_digest(digest, review["digest"]):
            raise OwnerError("Permission review changed; review again")
        receipt = json.dumps({**review, "provenance": provenance}, sort_keys=True)
        with service.owners.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "UPDATE owners SET execution_contract=?,permission_receipt=?,mode=? "
                "WHERE id=? AND execution_contract IS ? AND mode IS ?",
                (CONTRACT, receipt, review["new_mode"], owner, review["old_contract"], review["old_mode"])).rowcount
            if changed != 1:
                raise OwnerError("Permission review changed; review again")
        result = service.owners.permission(owner)
        if result["contract"] != CONTRACT or result["receipt"] != receipt:
            raise OwnerError("Permission acceptance could not be verified; refresh status")
        return result


def review_manual(service, owner, identity):
    from tools.approval_context import set_current_session_key, reset_current_session_key
    from tools.approval_prompt import request_elicitation_consent
    scope = {k: identity[k] for k in ("session_id", "runtime_session_id", "stored_session_id", "task_id") if identity.get(k)}
    review = prepare(service, owner, scope)
    key = identity.get("stored_session_id") or owner
    token = set_current_session_key(key)
    try:
        verdict = request_elicitation_consent(
            review["text"] + "\nProfile: " + str(service.home) + "\nConversation: " + owner + "\n" +
            "\n".join(row["id"] + ": " + row["warning"] for row in review["resources"]),
            "Use optional targets for this conversation", surface="realms-permission-review")
    finally:
        reset_current_session_key(token)
    if verdict != "accept":
        return {"accepted": False, "message": "Permission review cancelled; execution remains held. No guest was changed."}
    permission = accept_native(service, owner, scope, review["digest"], provenance="manual-elicitation")
    return {"accepted": True, "permission": permission,
            "message": "Permission conversion accepted. No tool was replayed; continue explicitly. Guest lifecycle and data are unchanged."}


def held_status(service, owner, permission):
    rows = [{**row, "state": "live" if row["state"] == "running" else row["state"],
             "window_count": None, "controlled": False,
             "error": "Legacy work: export required before unsafe lifecycle changes. " + row["warning"]}
            for row in _resources(service, owner)]
    return {"permission": permission, "requested": True, "requested_kind": None,
            "mode": permission["stored_mode"], "kind": service.kind(owner), "realms": rows,
            "message": HOLD if permission["state"] == "legacy-pending" else (
                "Legacy explicit host access is preserved. Private testing remains disabled; "
                "review optional targets before re-enabling."),
            "setup": None, "vm_setup": None}
