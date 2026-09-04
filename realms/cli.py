"""Standalone profile-aware realm CLI. Machine-readable JSON by default."""

import argparse
import json
import subprocess
import sys

from .manager import Manager, RealmError


def main(argv=None):
    parser = argparse.ArgumentParser(prog="hermes-realm")
    parser.add_argument(
        "--home", help="Explicit Hermes profile home (default: effective HERMES_HOME)"
    )
    commands = parser.add_subparsers(dest="operation", required=True)
    commands.add_parser("start").add_argument("session_id")
    commands.add_parser("list")
    for name in ("stop", "env"):
        commands.add_parser(name).add_argument("id")
    commands.add_parser("doctor").add_argument("id", nargs="?")
    for name, argument in (("shot", "path"), ("resize", "size")):
        command = commands.add_parser(name)
        command.add_argument("id")
        command.add_argument(argument)
    execute = commands.add_parser("exec")
    execute.add_argument("id")
    execute.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        manager = Manager(args.home)
        if args.operation == "start":
            result = manager.start(args.session_id)
        elif args.operation == "list":
            result = manager.list()
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
            command = args.command[1:] if args.command[:1] == ["--"] else args.command
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


if __name__ == "__main__":
    raise SystemExit(main())
