"""Explicit session/profile consent and durable jobs in the serving profile."""
from contextlib import contextmanager
import contextvars
import fcntl  # windows-footgun: ok — Linux-only plugin
import hashlib
import hmac
import json
import os
import re
import threading
import time
import uuid

from .lifecycle import atomic_json
from .setup_plan import build_plan, confined
from .setup_worker import install_plan, run_child, verify_ready
from .vm_resources import require_install_resources


def _root(service, *, create=False):
    root = confined(service.home, service.home / "plugin-data" / "hermes-realms" / "setup-jobs")
    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def _consent(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def prepare(service, owner, kind):
    _root(service)
    plan = build_plan(service, owner, kind)
    return {key: plan[key] for key in ("kind", "ready", "action", "summary", "details")} | {"consent": _consent(plan)}


def prepare_base_update(service, release, review_binding):
    from .setup_plan import build_base_update_plan
    _root(service)
    plan = build_base_update_plan(service.home, release, review_binding)
    return {key: plan[key] for key in (
        "operation", "scope", "kind", "release", "review_binding", "summary", "details", "blockers"
    )} | {"consent": _consent(plan)}


def _path(service, job_id):
    if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise ValueError("Invalid setup job id")
    return confined(service.home, _root(service) / (job_id + ".json"))


def _read(service, job_id):
    path = _path(service, job_id)
    with _progress_metadata(path) as fd:
        raw = os.read(fd, 65537)
    if len(raw) > 65536:
        raise ValueError("Setup job metadata is too large")
    record = json.loads(raw)
    if not isinstance(record, dict):
        raise PermissionError("Invalid setup job metadata")
    if record.get("id") != job_id or record.get("home") != str(service.home):
        raise PermissionError("Setup job ownership mismatch")
    _operation(record)
    return record


def _operation(record):
    pair = record.get("operation"), record.get("scope")
    if pair == ("vm-base-update", "profile") and "owner" in record and record["owner"] is None and record.get("kind") == "omarchy-vm":
        return "vm-base-update"
    legacy = "operation" not in record and "scope" not in record
    if (legacy or pair == ("session-setup", "session")) and isinstance(record.get("owner"), str) and record["owner"]:
        return "session-setup"
    raise PermissionError("Invalid setup operation scope")


def _require_scope(record, operation, owner):
    if _operation(record) != operation or record.get("owner") != owner:
        raise PermissionError("Setup job ownership mismatch")


def _public(record):
    return {key: record[key] for key in ("id", "kind", "state", "message", "error", "operation", "scope", "base_commit_started") if key in record} | ({
        "continuation": (record.get("continuation") or {}).get("state", "none")
    } if _operation(record) == "session-setup" else {}) | {
        "cancellable": record["state"] == "running" and record.get("cancel_protocol") == 1
        and not record.get("activation_started", False) and not record.get("base_commit_started", False)
    }


def _lock(service):
    path = confined(service.home, _root(service, create=True) / "active.lock")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)  # windows-footgun: ok — Linux-only plugin
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise ValueError("A setup job is already active in this profile") from None
    return fd


def _release(fd):
    # A lifetime supervisor may retain this same open-file description until
    # its owned units stop. LOCK_UN would release its exclusion as well.
    os.close(fd)


@contextmanager
def _job_guard(service, job_id):
    # Short updates and the activation commit share one cross-process boundary.
    path = _path(service, job_id).with_suffix(".lock")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)  # windows-footgun: ok — Linux-only plugin
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


class SetupCancelled(Exception):
    """An accepted cancellation prevents subsequent setup phases."""


def _check_cancel(record):
    if record["state"] in {"cancelling", "cancelled"}:
        raise SetupCancelled()


def cancel(service, owner, job_id):
    return _cancel(service, owner, job_id, "session-setup")


def cancel_base_update(service, job_id):
    return _cancel(service, None, job_id, "vm-base-update")


def _cancel(service, owner, job_id, operation):
    record = _read(service, job_id)
    _require_scope(record, operation, owner)
    # Jobs launched by older workers have no cancellation control channel.
    if record.get("activation_started") or record.get("base_commit_started") or record.get("cancel_protocol") != 1:
        return _status(service, owner, job_id, operation)
    with _job_guard(service, job_id):
        record = _read(service, job_id)
        _require_scope(record, operation, owner)
        if record["state"] == "running" and not record.get("activation_started") and not record.get("base_commit_started"):
            # Revoke only this job's lease; a later manual lifecycle generation
            # must not be invalidated by a stale Cancel request.
            if operation == "session-setup":
                with service.owners.activation_guard(owner):
                    if service.owners.setup_generation(owner) == record["activation_generation"]:
                        service.owners.setup_generation(owner, revoke=True)
            record.update(state="cancelling", message="Cancelling setup; waiting for owned work to stop. An active package transaction must finish safely.")
            atomic_json(_path(service, job_id), record)
        # Terminal jobs are immutable. Completion/activation that committed
        # first wins; Cancel must never Stop a preexisting/replacement target.
    return _status(service, owner, job_id, operation)


def status(service, owner, job_id):
    return _status(service, owner, job_id, "session-setup")


def status_base_update(service, job_id):
    return _status(service, None, job_id, "vm-base-update")


def _status(service, owner, job_id, operation):
    record = _read(service, job_id)
    _require_scope(record, operation, owner)
    if record["state"] in {"running", "cancelling"}:
        # A process crash releases flock. No PID reuse assumptions or polling writes.
        path = confined(service.home, _root(service) / "active.lock")
        with _progress_metadata(path) as fd:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                # The active receipt distinguishes a later job from this one.
                active = confined(service.home, _root(service) / "active.json")
                with _progress_metadata(active) as active_fd:
                    raw = os.read(active_fd, 4097)
                if len(raw) > 4096:
                    raise ValueError("Setup active metadata is too large")
                current = json.loads(raw).get("id")
                interrupted = current != job_id
            else:
                interrupted = True
            if interrupted:
                # Completion may precede retirement or a replacement lock holder.
                record = _read(service, job_id)
                _require_scope(record, operation, owner)
                interrupted = record["state"] in {"running", "cancelling"}
            if interrupted:
                if operation == "vm-base-update":
                    from .setup_base_update import failure
                    record.update(failure(record))
                elif record["state"] == "cancelling":
                    record.update(state="cancelled", message="Setup cancelled. Existing packages, verified artifacts and workspace data were retained.")
                else:
                    record.update(state="failed", message="Setup was interrupted. Review prerequisites and retry.", error="interrupted")
    return _public(record)


def latest(service, owner):
    return _latest(service, owner, "session-setup")


def latest_base_update(service):
    return _latest(service, None, "vm-base-update")


def _latest(service, owner, operation):
    root = _root(service)
    if not root.exists():
        return None
    owned = []
    for path in root.glob("*.json"):
        if re.fullmatch(r"[0-9a-f]{32}", path.stem):
            record = _read(service, path.stem)
            if _operation(record) == operation and record.get("owner") == owner:
                owned.append(record)
    return _status(service, owner, max(owned, key=lambda r: r["created_at"])["id"], operation) if owned else None


_PHASES = {"packages": "Installing approved system packages…", "install": "Installing and verifying realm prerequisites…", "verify": "Verifying readiness…", "start": "Starting this conversation's realm…"}


_INSTALLER_STAGES = {
    "downloading": "Downloading the pinned Realm driver…",
    "verifying": "Verifying the pinned Realm driver…",
    "preparing": "Preparing the profile-local Realm driver…",
}

_VM_INSTALLER_STAGES = {
    "downloading": "Downloading the Omarchy ISO and signature…",
    "verifying": "Verifying the Omarchy ISO signature…",
    "preparing": "Preparing the Omarchy VM base image…",
}


@contextmanager
def _progress_metadata(path):
    """Never wait on a special file while retaining the cancellation guard."""
    import stat

    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)  # windows-footgun: ok — Linux-only plugin
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():  # windows-footgun: ok — Linux-only plugin
            raise PermissionError("Invalid setup progress metadata")
        yield fd
    finally:
        os.close(fd)


def _require_active(service, job_id):
    active = confined(service.home, _root(service) / "active.json")
    with _progress_metadata(active) as fd:
        raw = os.read(fd, 4097)
    if len(raw) > 4096:
        raise ValueError("Setup progress metadata is too large")
    active_record = json.loads(raw)
    if not isinstance(active_record, dict) or active_record.get("id") != job_id:
        raise PermissionError("Setup job is no longer active")
    # A stranded running receipt is not an active worker. Do not acquire
    # exclusion or keep a retired worker alive merely to publish its message.
    path = confined(service.home, _root(service) / "active.lock")
    with _progress_metadata(path) as fd:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            raise PermissionError("Setup worker has retired")


def installer_progress(home, job_id, owner, stage, *, kind="realm"):
    """Trusted installer callbacks may update presentation, never authority."""
    from pathlib import Path
    from types import SimpleNamespace

    from .integration import OwnershipStore

    service = SimpleNamespace(home=Path(home))
    stages = {"realm": _INSTALLER_STAGES, "omarchy-vm": _VM_INSTALLER_STAGES}.get(kind, {})
    if stage not in stages:
        raise ValueError("Unsupported installer stage")
    with _job_guard(service, job_id):
        current = _read(service, job_id)
        if current.get("owner") != owner:
            raise PermissionError("Setup job ownership mismatch")
        _check_cancel(current)
        if (current["state"] != "running" or current.get("activation_started") or current.get("base_commit_started")
                or current.get("kind") != kind or current.get("cancel_protocol") != 1):
            raise PermissionError("Setup job no longer accepts installer progress")
        _require_active(service, job_id)
        if _operation(current) == "session-setup":
            owners = OwnershipStore(service.home, readonly=True)
            if owners.setup_generation(owner) != current.get("activation_generation"):
                raise PermissionError("Setup ownership was revoked")
        current["message"] = stages[stage]
        atomic_json(_path(service, job_id), current)


def start(service, owner, kind, consent, identity):
    from .permission_transition import require_optional
    require_optional(service, owner)
    # No writes (including lock creation) until the exact proposal is accepted.
    _root(service)
    plan = build_plan(service, owner, kind)
    if not isinstance(consent, str) or not hmac.compare_digest(consent, _consent(plan)):
        raise ValueError("Setup consent does not match the current proposal; prepare again")
    if plan["blockers"]:
        raise ValueError("Setup prerequisites need attention; review the proposal before retrying")
    identity = {key: value for key, value in identity.items() if value is not None}
    if set(identity) - {"runtime_session_id", "stored_session_id"}:
        raise PermissionError("Invalid setup ownership identity")
    if service.owners.resolve(session_id=owner, **identity) != owner:
        raise PermissionError("Setup ownership mismatch")
    require_install_resources(plan)
    lock = _lock(service)
    record = {"id": uuid.uuid4().hex, "owner": owner, "home": str(service.home), "kind": kind,
              "operation": "session-setup", "scope": "session",
              "state": "running", "cancel_protocol": 1, "message": "Preparing realm setup…", "created_at": time.time()}
    return _launch_job(service, record, plan, lock, identity)


def start_base_update(service, release, review_binding, consent):
    from .setup_plan import build_base_update_plan
    def reviewed_plan():
        plan = build_base_update_plan(service.home, release, review_binding)
        if (not isinstance(consent, str) or not re.fullmatch(r"[0-9a-f]{64}", consent)
                or not hmac.compare_digest(consent, _consent(plan))):
            raise ValueError("Setup consent does not match the current proposal; prepare again")
        if plan["blockers"]:
            raise ValueError("Setup prerequisites need attention; review the proposal before retrying")
        return plan
    _root(service)
    plan = reviewed_plan()
    require_install_resources(plan)
    lock = _lock(service)
    try:
        plan = reviewed_plan()
    except BaseException:
        _release(lock)
        raise
    plan["base_generation"] = uuid.uuid4().hex
    record = {"id": uuid.uuid4().hex, "owner": None, "home": str(service.home),
              "kind": "omarchy-vm", "operation": "vm-base-update", "scope": "profile",
              "state": "running", "cancel_protocol": 1, "created_at": time.time(),
              "message": "Preparing profile base update…", "base_commit_started": False,
              "base_generation": plan["base_generation"], "plan": plan}
    return _launch_job(service, record, plan, lock, None)


def _launch_job(service, record, plan, lock, identity):
    session = _operation(record) == "session-setup"
    owner, kind = record["owner"], record["kind"]
    try:
        if session:
            record["activation_generation"] = service.reserve_setup(owner)
            from .setup_continuation import attach_request
            attach_request(service, record)
        atomic_json(_path(service, record["id"]), record)
        atomic_json(confined(service.home, _root(service) / "active.json"), {"id": record["id"]})

        def work():
            job_id = record["id"]

            def phase(name):
                with _job_guard(service, job_id):
                    current = _read(service, job_id)
                    _check_cancel(current)
                    current["message"] = _PHASES[name]
                    atomic_json(_path(service, job_id), current)
            try:
                install_plan(plan, phase, lock_fd=lock, cancel_path=_path(service, job_id))
                if not session:
                    with _job_guard(service, job_id):
                        current = _read(service, job_id)
                        _check_cancel(current)
                        from .setup_base_update import completed
                        completed(current)
                        current.update(state="succeeded", message="Profile base update completed; existing workspaces were retained.")
                        atomic_json(_path(service, job_id), current)
                    return
                phase("verify")
                verify_ready(service.home, kind)
                with _job_guard(service, job_id):
                    current = _read(service, job_id)
                    _check_cancel(current)
                    current["activation_started"] = True
                    current["message"] = _PHASES["start"]
                    atomic_json(_path(service, job_id), current)
                    # Cancellation and activation serialize here. If activation
                    # won, return its actual terminal result rather than stopping
                    # a target that might have predated or replaced this job.
                    service.activate_setup(owner, kind, plan["config"],
                                           record["activation_generation"], identity)
                    current.update(state="succeeded", message="Realm is ready for this conversation.")
                    atomic_json(_path(service, job_id), current)
            except Exception:
                with _job_guard(service, job_id):
                    current = _read(service, job_id)
                    if not session:
                        from .setup_base_update import failure
                        current.update(failure(current))
                    elif current["state"] == "cancelling":
                        current.update(state="cancelled", message="Setup cancelled. Existing packages, verified artifacts and workspace data were retained.")
                    else:
                        # Never expose installer/SSH diagnostics or credentials.
                        current.update(state="failed", message="Realm setup failed. Check prerequisites and administrator authorization, then retry.", error="setup_failed")
                        from .setup_continuation import retain_failed_request
                        retain_failed_request(service, current)
                    if current.get("continuation"):
                        current["continuation"]["state"] = "invalidated"
                    atomic_json(_path(service, job_id), current)
            finally:
                try:
                    if session:
                        service.finish_setup(owner)
                finally:
                    _release(lock)

        context = contextvars.copy_context()
        threading.Thread(target=context.run, args=(work,), name="realms-setup-" + record["id"], daemon=True).start()
    except BaseException:
        try:
            if session:
                service.finish_setup(owner)
        finally:
            _release(lock)
        raise
    return _public(record.copy())
