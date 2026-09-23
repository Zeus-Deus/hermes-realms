"""Target-only execution leases: ordinary conversation aliases never point here."""
import hashlib
import sqlite3
from pathlib import Path
import sys

from hermes_cli.session_execution import (
    ComputerUseLaunchContext, SessionExecutionContext, SessionExecutionError,
    register_session_execution_context, remove_session_execution_context,
    resolve_session_execution_context,
)


def compute_identity(record):
    """A retained workspace can outlive several compute incarnations."""
    return record["generation"], record.get("compute_generation"), record.get("invocation_id")


def release_targets(service, owner, *, purpose=None):
    """Revoke only this plugin's target leases, not the caller's execution."""
    for key, value in list(service._target_contexts.items()):
        if key[0] == owner and (purpose is None or key[2] == purpose):
            service._target_contexts.pop(key)
            remove_session_execution_context(value[2].session_id)


def _access_epoch(service, owner, realm_id):
    from .viewer_state import ControlAuthority
    from .permission_transition import require_optional
    from .integration import OwnerError
    try:
        require_optional(service, owner)
    except OwnerError as exc:
        raise SessionExecutionError(str(exc)) from exc
    if service._unloaded or service.owners.mode(owner, service.manager.config.default_mode) != "realm":
        raise SessionExecutionError("Realm use is disabled for this conversation; normal tools remain available")
    try:
        epoch = ControlAuthority.read_agent_epoch(service.home / "realms" / "viewer", realm_id)
        if epoch is None:
            raise SessionExecutionError("Realm control authority is unavailable")
        return epoch
    except (OSError, ValueError, sqlite3.Error) as exc:
        raise SessionExecutionError("Realm control authority is unavailable or held") from exc


def _regular_context(service, owner, record, purpose):
    from .integration import HOST_ENV_KEYS, SetupError, setup_status
    env = service.manager.env(record["id"])
    prefix = (sys.executable, str(Path(__file__).with_name("launch.py")), str(service.home), record["id"], "--")
    launch = None
    if purpose == "cua":
        from .driver import create_driver_launcher
        setup = setup_status(driver_executable=service.driver_executable)
        if not setup["ready"]:
            raise SetupError(setup["message"])
        launch = ComputerUseLaunchContext(
            driver_command=create_driver_launcher(service.manager, record["id"], service.driver_executable),
            private_daemon=True, desktop_only=True, runtime_dir=record["runtime_dir"],
            no_overlay=not record["overlay"], session_name=record["id"], theme=record["cursor_theme"],
            access_epoch=lambda: _access_epoch(service, owner, record["id"]),
        )
    return SessionExecutionContext(
        env_set=env, env_unset=HOST_ENV_KEYS - env.keys(), command_prefix=prefix,
        computer_use=launch, backend_cwd=env.get("HOME"),
        validate=lambda: service._valid(record, owner),
        terminal_access_epoch=(lambda: _access_epoch(service, owner, record["id"])) if purpose == "terminal" else None,
    )


def _vm_context(service, owner, record, purpose):
    from .integration import HOST_ENV_KEYS
    launch = None
    values = {"env_unset": HOST_ENV_KEYS, "validate": lambda: service._vm_valid(record, owner)}
    if purpose == "cua":
        from .vm_cua import create_vm_driver_launcher, vm_desktop_attestor
        launch = ComputerUseLaunchContext(
            driver_command=create_vm_driver_launcher(service.vm, record["id"], service.driver_executable),
            private_daemon=True, desktop_only=True, runtime_dir=record["runtime_dir"],
            no_overlay=True, session_name=record["id"],
            desktop_attestor=vm_desktop_attestor(service.vm, record["id"]),
            access_epoch=lambda: _access_epoch(service, owner, record["id"]),
        )
    else:
        from .vm_launch import GUEST_TEMP_DIR, guest_shell_init
        values.update(command_prefix=service.vm.command_prefix(record["id"]),
                      backend_cwd=service.vm.guest_home(record["id"]),
                      backend_temp_dir=GUEST_TEMP_DIR, backend_shell_init=guest_shell_init(),
                      terminal_access_epoch=lambda: _access_epoch(service, owner, record["id"]))
    return SessionExecutionContext(computer_use=launch, **values)


_BUILDERS = {"realm": _regular_context, "omarchy-vm": _vm_context}


def context_for(service, owner, purpose, *, selected=None, record=None):
    from hermes_constants import get_hermes_home
    if get_hermes_home().resolve() != service.home:
        raise SessionExecutionError("Realm target belongs to another profile")
    if purpose not in ("cua", "terminal"):
        raise ValueError("Unknown Realm execution purpose")
    if service._unloaded or service.owners.mode(owner, service.manager.config.default_mode) != "realm":
        raise SessionExecutionError("Realm use is disabled for this conversation; normal tools remain available")
    with service._lock:
        kind = service.kind(owner)
        from .integration import SetupError
        generation = service.owners.setup_generation(owner)
        try:
            if selected is None:
                record = service.ready(owner)
            else:
                with service.owners.activation_guard(owner):
                    selected()
                    if record is None:
                        record = service.ready(owner, before_start=selected)
                    else:
                        valid = service._vm_valid if kind == "omarchy-vm" else service._valid
                        if not valid(record, owner):
                            raise SessionExecutionError("Selected compute is no longer live; request a fresh operation")
                        service._attachments[owner] = (record["generation"], (), record["id"])
        except SetupError:
            from .setup_continuation import record_request
            with service.owners.activation_guard(owner):
                if service.owners.setup_generation(owner) == generation:
                    record_request(service, owner, {"_agent": True})
            raise
        key = (owner, kind, purpose)
        current = service._target_contexts.get(key)
        incarnation = compute_identity(record)
        if current and current[:2] == (record["id"], incarnation):
            current[2].check()
            return current[2]
        if current:
            remove_session_execution_context(current[2].session_id)
        context = _BUILDERS[kind](service, owner, record, purpose)
        owner_key = hashlib.sha256(owner.encode()).hexdigest()[:16]
        identity = f"realm-target:{purpose}:{record['id']}:{owner_key}"
        register_session_execution_context(identity, context)
        lease = resolve_session_execution_context(session_id=identity)
        service._target_contexts[key] = (record["id"], incarnation, lease)
        return lease


def _selected_record(service, owner, kind):
    """Read ownership without Manager.list/VM.list reconciliation or adoption."""
    from .permission_transition import _resources
    rows = [row for row in _resources(service, owner, include_record=True) if row["kind"] == kind]
    if len(rows) > 1:
        raise SessionExecutionError("Ambiguous selected Realm resources")
    return rows[0]["record"] if rows else None


def _record_binding(record):
    if record is None:
        return None
    return (record["id"], compute_identity(record), record["status"],
            record.get("workspace_dir"), record.get("session_dir"))


def select_for(service, purpose, *, session_id, task_id=None, command=None):
    """Snapshot only. Cold intent is owner/kind/generation, not a cached backend.

    Realization may create that owner's first compute or restart its stopped
    workspace. A selected live incarnation is never restarted or substituted.
    """
    from hermes_constants import get_hermes_home
    from hermes_cli.session_execution import TargetSelection
    from .config import Config
    from .permission_transition import require_optional
    from .host_guard import host_escape

    identity = dict(session_id=session_id, task_id=task_id)
    owner = service.owners.resolve(**identity)
    kind = service.kind(owner)
    generation = service.owners.setup_generation(owner)
    config = Config.load(service.home)
    manager_config = service.manager.config
    vm_config = service._vm.config if service._vm is not None else config

    def check_owner():
        try:
            require_optional(service, owner)
            requested = service.owners.requested_kind(owner)
            if service.owners.mode(owner, service.manager.config.default_mode) != "realm":
                raise SessionExecutionError("Realm use is disabled for this conversation; normal tools remain available")
            if (get_hermes_home().resolve() != service.home or service._unloaded
                    or service.owners.resolve(**identity) != owner
                    or service.owners.setup_generation(owner) != generation
                    or service.kind(owner) != kind
                    or (requested is not None and requested != kind)
                    or (kind == "omarchy-vm" and service._vm is not None and service._vm.config != vm_config)
                    or Config.load(service.home) != config or service.manager.config != manager_config):
                raise SessionExecutionError("Selected Realm authority changed; request a fresh operation")
            permission = service.owners.permission(owner)
            if permission["receipt"] and permission["stored_mode"] != "realm":
                raise SessionExecutionError("Private testing is disabled or unselected")
        except SessionExecutionError:
            raise
        except Exception as exc:
            raise SessionExecutionError("Selected Realm ownership unavailable") from exc

    check_owner()
    if purpose == "terminal" and kind == "realm" and (reason := host_escape(command)):
        raise SessionExecutionError(reason)
    record = _selected_record(service, owner, kind)
    if record is not None and record["status"] not in ("running", "stopped"):
        raise SessionExecutionError("Selected Realm requires recovery before use")
    from .viewer_state import ControlAuthority
    cold_authority = (ControlAuthority.read_agent_epoch(service.home / "realms" / "viewer", "")
                      if record is None else None)
    binding = _record_binding(record)
    epoch = _access_epoch(service, owner, record["id"]) if record is not None else None
    realized = None
    preparing = None

    def check():
        check_owner()
        if binding is None:
            try:
                if ControlAuthority.read_agent_epoch(service.home / "realms" / "viewer", "") != cold_authority:
                    raise SessionExecutionError("Selected Realm control store changed")
            except (OSError, ValueError, sqlite3.Error) as exc:
                raise SessionExecutionError("Selected Realm control store unavailable") from exc
        expected = _record_binding(preparing) if preparing is not None else binding
        if _record_binding(_selected_record(service, owner, kind)) != expected:
            raise SessionExecutionError("Selected Realm incarnation changed; request a fresh operation")
        if expected is not None and _access_epoch(service, owner, expected[0]) != (epoch or 0):
            raise SessionExecutionError("Selected Realm control changed; request a fresh operation")

    def realize(*, before_start=None):
        nonlocal binding, epoch, realized, cold_authority
        def admit(*, prepared=None):
            nonlocal preparing
            # The manager holds its registry lock throughout preparation. Only
            # that start may advance our stopped/cold intent to a starting record;
            # the public selection never adopts an arbitrary replacement.
            if prepared is not None:
                if (prepared["session_id"] != owner or prepared["home"] != str(service.home)
                        or prepared["status"] != "starting"
                        or (record is not None and any(prepared.get(key) != record.get(key)
                            for key in (("id", "generation", "session_dir") if kind == "omarchy-vm"
                                        else ("id", "workspace_dir"))))):
                    raise SessionExecutionError("Prepared Realm does not match selected target")
            previous = preparing
            preparing = prepared
            try:
                check()
                if before_start is not None:
                    before_start()
            finally:
                preparing = previous
        with service._lock:
            check()
            if realized is not None:
                realized.check()
                return realized
            # First creation is permitted only after consent, never by selection.
            if record is None:
                from .bridge import get_profile_viewer
                get_profile_viewer(service.home)
                cold_authority = ControlAuthority.read_agent_epoch(service.home / "realms" / "viewer", "")
            lease = context_for(service, owner, purpose, selected=admit,
                                record=record if record is not None and record["status"] == "running" else None)
            check_owner()
            current = _selected_record(service, owner, kind)
            if current is None or current["status"] != "running":
                raise SessionExecutionError("Selected Realm did not become ready")
            if record is not None:
                if current["id"] != record["id"] or (record["status"] == "running" and _record_binding(current) != binding):
                    raise SessionExecutionError("Selected Realm was replaced during realization")
            current_epoch = _access_epoch(service, owner, current["id"])
            if current_epoch != (epoch if epoch is not None else 0):
                raise SessionExecutionError("Selected Realm control changed during realization")
            binding, epoch, realized = _record_binding(current), current_epoch, lease
            check()
            lease.check()
            return lease

    check()
    return TargetSelection(realize, check)
