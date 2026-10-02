"""Standalone profile-aware realm CLI. Machine-readable JSON by default.

``list`` prints a table only when stdout is a terminal; pipes keep the JSON.
"""

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
    add_list_parser(commands, "Realms in this profile (table on a terminal, JSON when piped)")
    add_view_parser(commands)
    configure_delete_parser(commands)
    for name in ("stop", "env"):
        commands.add_parser(name).add_argument("id")
    commands.add_parser("doctor").add_argument("id", nargs="?")
    for name, argument in (("shot", "path"), ("resize", "size")):
        command = commands.add_parser(name)
        command.add_argument("id")
        command.add_argument(argument)
    review = commands.add_parser(
        "review", help="Release every earlier chat with no recorded Realm use (one decision)")
    review.add_argument("scope", choices=["unused"])
    review.add_argument("--dry-run", action="store_true", help="Count only; change nothing")
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
    add_list_parser(operations, "VM realms in this profile (table on a terminal, JSON when piped)")
    add_view_parser(operations)
    operations.add_parser(
        "remove-base", help="Delete this profile's base image (not the ISO)")
    operations.add_parser("clean", help="Remove stale ISOs; preserve all workspace data")
    operations.add_parser("stop").add_argument("id")
    delete = configure_delete_parser(operations)
    delete.add_argument(
        "--discard", action="store_true",
        help="Also delete a recovery-required workspace whose files no longer verify. "
             "Refused unless every compute unit is inactive and no process holds its files")
    prune = operations.add_parser(
        "prune", help="Permanently delete stopped and recovery-required VM workspaces "
                      "(running VMs are never touched; interactive only)")
    selection = prune.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all", action="store_true", dest="everything",
                           help="Every VM workspace whose compute is provably stopped")
    selection.add_argument("--older-than", type=float, metavar="DAYS",
                           help="Workspaces stopped and unused for at least DAYS days")
    selection.add_argument("--id", action="append", dest="ids", metavar="ID",
                           help="One exact VM ID (repeatable)")
    prune.add_argument("--keep", action="append", default=[], metavar="ID",
                       help="Never delete this VM ID, whatever its state (repeatable)")
    prune.add_argument("--dry-run", action="store_true",
                       help="List what would be deleted and kept; change nothing")


def add_list_parser(commands, help_text):
    command = commands.add_parser("list", help=help_text)
    output = command.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true",
                        help="Raw JSON records (the default when stdout is not a terminal)")
    output.add_argument("--table", action="store_true",
                        help="Readable table even when stdout is not a terminal")


def add_view_parser(commands):
    command = commands.add_parser(
        "view", help="Print a private link to a running realm's live screen (view-only)")
    command.add_argument("id", nargs="?", help="Realm or VM ID (default: the only running one)")
    command.add_argument("--control", action="store_true",
                         help="Allow taking over input from the page (pauses the agent while held)")
    command.add_argument("--tailnet", action="store_true",
                         help="Listen on this machine's Tailscale IP instead of 127.0.0.1")
    command.add_argument("--port", type=int, default=0,
                         help="Fixed listen port (default: any free port)")


def configure_delete_parser(commands):
    command = commands.add_parser("delete", help="Permanently delete a stopped workspace (interactive only)")
    command.add_argument("id")
    command.add_argument("--session-id", required=True,
                         help="Explicit owner selection, not session authentication")
    return command


def confirmed_delete(manager, args):
    """Profile-admin consent only; never a model action or same-UID barrier."""
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise RealmError("Delete requires an interactive terminal for confirmation")
    discard = getattr(args, "discard", False)
    if discard:
        snapshot = manager.discard_snapshot(args.id, session_id=args.session_id)
    else:
        snapshot = manager.delete_snapshot(args.id, session_id=args.session_id)
    record = snapshot["record"]
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()[:12]
    phrase = f"DELETE {args.id} {digest}"
    print("Permanently delete retained workspace data (no automatic Stop):", file=sys.stderr)
    if discard and record.get("recovery_reason"):
        print("Discarding a workspace that needs recovery: " + record["recovery_reason"], file=sys.stderr)
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
    deleted = manager.delete(args.id, session_id=args.session_id, expected_snapshot=snapshot,
                             **({"discard": True} if discard else {}))
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
        from .terminal import print_records
        print_records(manager.list(), args)
        return 0
    elif args.vm_operation == "view":
        return view(args, kind="vm")
    elif args.vm_operation == "remove-base":
        result = {"removed": manager.remove_base()}
    elif args.vm_operation == "delete":
        result = confirmed_delete(manager, args)
    elif args.vm_operation == "clean":
        result = clean(manager)
    elif args.vm_operation == "prune":
        result = prune(manager, args)
    else:
        result = {"stopped": manager.stop(args.id), "id": args.id}
    print(json.dumps(result, sort_keys=True))
    return 0


def _size(snapshot):
    """Bytes a discard would free, from the entries bound into its review."""
    contents = snapshot["deletion"].get("contents")
    if contents is not None:
        return sum(entry[3] for entry in contents)
    from pathlib import Path
    directory = Path(snapshot["record"]["session_dir"])
    return sum(p.lstat().st_size for p in directory.iterdir()) if directory.is_dir() else 0


def prune(manager, args):
    """Bulk owner-confirmed discard; running or still-held VMs are listed as kept."""
    older = None if args.older_than is None else args.older_than * 86400
    review = manager.prune_snapshot(everything=args.everything, older_than=older,
                                    ids=args.ids, keep=args.keep)
    rows = [{"id": vm_id, "status": snap["record"]["status"],
             "session_id": snap["record"]["session_id"],
             "reason": snap["record"].get("recovery_reason"),
             "bytes": _size(snap)} for vm_id, snap in sorted(review["delete"].items())]
    summary = {"would_delete": rows, "kept": review["kept"],
               "bytes": sum(row["bytes"] for row in rows)}
    if args.dry_run or not rows:
        return dict(summary, dry_run=bool(args.dry_run), deleted=[])
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise RealmError("Prune requires an interactive terminal for confirmation; use --dry-run to list")
    digest = hashlib.sha256(json.dumps(review["delete"], sort_keys=True).encode()).hexdigest()[:12]
    phrase = f"PRUNE {len(rows)} {digest}"
    print(f"Permanently delete {len(rows)} VM workspace(s), {summary['bytes'] / 2**30:.1f} GiB "
          "(no automatic Stop; running VMs are kept):", file=sys.stderr)
    for row in rows:
        print(f"  {row['id']}  {row['status']}  {row['session_id']}"
              + (f"  ({row['reason']})" if row["reason"] else ""), file=sys.stderr)
    for row in review["kept"]:
        print(f"  keep {row['id']}  {row['status']}: {row['reason']}", file=sys.stderr)
    print(f"Type {phrase} to confirm: ", end="", file=sys.stderr, flush=True)
    try:
        response = sys.stdin.readline()
    except (EOFError, KeyboardInterrupt):
        raise RealmError("Prune cancelled") from None
    if response.rstrip("\r\n") != phrase:
        raise RealmError("Prune cancelled; confirmation did not match")
    deleted = manager.prune(review)
    return dict(summary, dry_run=False, deleted=deleted)


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


def view(args, kind=None):
    from .terminal import view as serve
    return serve(args.home, args.id, control=args.control, tailnet=args.tailnet,
                 port=args.port, kind=kind)


def run(args):
    try:
        if args.operation == "install-driver":
            from .install_driver import run as install
            install(args)
            return 0
        if args.operation == "vm":
            return run_vm(args)
        if args.operation == "review":
            from .bulk_review import preview, release
            from .config import effective_home
            home = effective_home(args.home)
            if args.dry_run:
                scope = preview(home)
                result = {"would_release": len(scope["eligible"]), "held": len(scope["kept"]),
                          "decided": scope["decision"] is not None}
            else:
                # Running this command is the administrator's decision.
                result = release(home, provenance="cli")
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.operation == "view":
            return view(args)
        manager = Manager(args.home)
        if args.operation == "start":
            result = manager.start(args.session_id)
        elif args.operation == "list":
            from .terminal import print_records
            print_records(manager.list(), args)
            return 0
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
