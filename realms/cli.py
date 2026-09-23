"""Standalone profile-aware realm CLI. Machine-readable JSON by default."""

import argparse
import hashlib
import json
import subprocess
import sys

from .manager import Manager, RealmError


def configure_parser(parser):
    parser.add_argument(
        "--home", help="Explicit Hermes profile home (default: effective HERMES_HOME)"
    )
    commands = parser.add_subparsers(dest="operation", required=True)
    commands.add_parser("start").add_argument("session_id")
    commands.add_parser("list")
    configure_delete_parser(commands)
    for name in ("stop", "env"):
        commands.add_parser(name).add_argument("id")
    commands.add_parser("doctor").add_argument("id", nargs="?")
    for name, argument in (("shot", "path"), ("resize", "size")):
        command = commands.add_parser(name)
        command.add_argument("id")
        command.add_argument(argument)
    execute = commands.add_parser("exec")
    execute.add_argument("id")
    execute.add_argument("realm_command", metavar="command", nargs=argparse.REMAINDER)
    from .install_driver import configure_parser as configure_install
    configure_install(commands.add_parser("install-driver", help="Explicitly download and verify the Linux x86-64 driver"))
    configure_vm_parser(commands.add_parser(
        "vm", help="Manage the Omarchy VM realm kind's shared base image"))


def configure_vm_parser(parser):
    operations = parser.add_subparsers(dest="vm_operation", required=True)
    install = operations.add_parser(
        "install",
        help="Download the signed Omarchy ISO and build this profile's base image")
    install.add_argument(
        "--iso", help="Use an already-downloaded ISO instead of fetching one")
    operations.add_parser("status", help="Base image, storage and prerequisites")
    settings = operations.add_parser(
        "settings", help="Base image, storage, memory and network as one block")
    settings.add_argument(
        "--check-updates", action="store_true",
        help="Also ask GitHub for the newest Omarchy release (needs network)")
    operations.add_parser("doctor", help="Check Omarchy VM prerequisites")
    operations.add_parser("list", help="Running VM realms in this profile")
    operations.add_parser(
        "remove-base", help="Delete this profile's base image (not the ISO)")
    operations.add_parser("clean", help="Remove stale ISOs; preserve all workspace data")
    operations.add_parser("stop").add_argument("id")
    configure_delete_parser(operations)


def configure_delete_parser(commands):
    command = commands.add_parser("delete", help="Permanently delete a stopped workspace (interactive only)")
    command.add_argument("id")
    command.add_argument("--session-id", required=True,
                         help="Explicit owner selection, not session authentication")


def confirmed_delete(manager, args):
    """Profile-admin consent only; never a model action or same-UID barrier."""
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise RealmError("Delete requires an interactive terminal for confirmation")
    snapshot = manager.delete_snapshot(args.id, session_id=args.session_id)
    record = snapshot["record"]
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()[:12]
    phrase = f"DELETE {args.id} {digest}"
    print("Permanently delete retained workspace data (no automatic Stop):", file=sys.stderr)
    print(json.dumps({key: record[key] for key in (
        "id", "home", "session_id", "status", "generation", "compute_generation",
        "workspace_dir", "session_dir", "runtime_dir",
    ) if key in record}, sort_keys=True), file=sys.stderr)
    print(f"Type {phrase} to confirm: ", end="", file=sys.stderr, flush=True)
    try:
        response = sys.stdin.readline()
    except (EOFError, KeyboardInterrupt):
        raise RealmError("Delete cancelled") from None
    if response.rstrip("\r\n") != phrase:
        raise RealmError("Delete cancelled; confirmation did not match")
    deleted = manager.delete(args.id, session_id=args.session_id, expected_snapshot=snapshot)
    if not deleted:
        raise RealmError("Delete confirmation target changed; confirm again")
    return {"deleted": True, "id": args.id}


def run_vm(args):
    from .vm_manager import VmManager

    manager = VmManager(args.home)
    if args.vm_operation == "install":
        # Long, loud and explicit: a 5 GB download plus an unattended install
        # is not something to run behind a spinner.
        result = manager.install_base(iso=getattr(args, "iso", None), stdout=sys.stderr)
    elif args.vm_operation == "status":
        result = {
            "base": manager.base_status(),
            "storage": manager.storage(),
            "realms": manager.list(),
        }
    elif args.vm_operation == "settings":
        result = manager.settings(check_updates=getattr(args, "check_updates", False))
    elif args.vm_operation == "doctor":
        report = manager.doctor()
        print(json.dumps(report, sort_keys=True))
        return 0 if report["ok"] else 1
    elif args.vm_operation == "list":
        result = manager.list()
    elif args.vm_operation == "remove-base":
        result = {"removed": manager.remove_base()}
    elif args.vm_operation == "delete":
        result = confirmed_delete(manager, args)
    elif args.vm_operation == "clean":
        result = clean(manager)
    else:
        result = {"stopped": manager.stop(args.id), "id": args.id}
    print(json.dumps(result, sort_keys=True))
    return 0


def clean(manager):
    """Clean disposable downloads, never infer permission to discard work."""
    from types import SimpleNamespace
    from .setup_flow import _lock, _release, _root
    from .lifecycle import atomic_json

    service = SimpleNamespace(home=manager.home)
    lock = _lock(service)
    try:
        atomic_json(_root(service) / "active.json", {"operation": "clean", "id": None})
        removed = []
        sessions = manager.registry.root / "vm"
        # An absent record may mean interrupted creation or recoverable legacy data.
        # A list-then-delete scan also races a concurrently admitted VM.
        preserved = sorted(str(path) for path in sessions.iterdir()) if sessions.is_dir() else []
        keep = (manager.base_status().get("iso") or "")
        iso_dir = manager.data / "iso"
        if iso_dir.is_dir():
            for iso in iso_dir.glob("omarchy-*.iso"):
                if iso.name != keep:
                    iso.unlink(missing_ok=True)
                    iso.with_suffix(".iso.sig").unlink(missing_ok=True)
                    removed.append(str(iso))
        return {"removed": removed, "preserved_workspaces": preserved, "storage": manager.storage()}
    finally:
        _release(lock)


def run(args):
    try:
        if args.operation == "install-driver":
            from .install_driver import run as install
            install(args)
            return 0
        if args.operation == "vm":
            return run_vm(args)
        manager = Manager(args.home)
        if args.operation == "start":
            result = manager.start(args.session_id)
        elif args.operation == "list":
            result = manager.list()
        elif args.operation == "delete":
            result = confirmed_delete(manager, args)
        elif args.operation == "stop":
            result = {"stopped": manager.stop(args.id), "id": args.id}
        elif args.operation == "doctor":
            result = manager.doctor(args.id)
            print(json.dumps(result, sort_keys=True))
            return 0 if result["ok"] else 1
        elif args.operation == "shot":
            result = {"path": manager.shot(args.id, args.path)}
        elif args.operation == "resize":
            result = manager.resize(args.id, args.size)
        elif args.operation == "exec":
            command = args.realm_command[1:] if args.realm_command[:1] == ["--"] else args.realm_command
            if not command:
                raise ValueError("exec requires a command after --")
            return subprocess.call(
                [*manager.command_prefix(args.id), *command], env=manager.env(args.id)
            )
        else:
            result = manager.env(args.id)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (RealmError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1


def main(argv=None):
    parser = argparse.ArgumentParser(prog="hermes realms")
    configure_parser(parser)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
