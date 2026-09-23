"""Standalone native Hermes registration. Nothing patches the host or prompt."""

import json
import shlex
from pathlib import Path

from hermes_constants import get_hermes_home
from hermes_cli.session_execution import SessionExecutionError
import runpy
import sys

_load_runtime = runpy.run_path(str(Path(__file__).resolve().parent / "realms/_binding.py"))["load_runtime"]
_integration = _load_runtime("integration")
get_integration = _integration.get_integration



SCHEMA = {
    "name": "realm",
    "description": "Manage this conversation's optional private test desktop. Ordinary terminal, files, cwd and host authentication stay unchanged; there is nothing to exit after testing. Use on to select realm (labwc) or omarchy-vm (real Omarchy/Hyprland testing). Off disables agent target use without stopping the viewed desktop; it is not needed for host work. Status diagnoses independently of the GUI driver. Repair resets only this target's CUA connection; verify a fresh capture without replaying uncertain input or replacing the desktop. Watch is view-only until human takeover. Preserve work before stop or kind changes. Push/pull copy selected VM files. Never fall back to physical-desktop input or bypass human control. Load skill hermes-realms:realms; existing approvals apply.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["on", "off", "status", "size", "stop", "repair", "watch", "shot",
                         "push", "pull"],
            },
            "size": {"type": "string", "description": "WIDTHxHEIGHT for action=size"},
            "kind": {
                "type": "string",
                "enum": ["realm", "omarchy-vm"],
                "description": "Desktop kind for action=on. realm (default) is a fast private labwc desktop; omarchy-vm is a disposable Omarchy guest in QEMU with its own kernel and disk.",
            },
            "path": {
                "type": "string",
                "description": "Host path for action=push, guest path for action=pull",
            },
            "destination": {
                "type": "string",
                "description": "Guest path for action=push, host path for action=pull",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}


def _raw_command(args):
    """Render tool arguments as the equivalent slash-command text.

    One parser for both surfaces: whatever ``/realm ...`` accepts is exactly
    what the tool accepts, and neither can drift into having its own rules.
    """
    action = args.get("action", "status")
    if action in ("push", "pull") and not args.get("path"):
        raise ValueError("VM transfer requires an explicit path")
    if action == "pull" and not args.get("destination"):
        raise ValueError("VM pull requires an explicit destination")
    arguments = {
        "size": [args.get("size", "")],
        "on": [{"omarchy-vm": "omarchy"}.get(args.get("kind", ""), args.get("kind", ""))],
        "push": [args.get("path", ""), args.get("destination", "")],
        "pull": [args.get("path", ""), args.get("destination", "")],
    }.get(action, [])
    return shlex.join([action, *(value for value in arguments if value)])


def register(ctx):
    cli = _load_runtime("cli")
    configure_parser, run = cli.configure_parser, cli.run

    ctx.register_cli_command(
        "realms", "Manage private Linux desktops and explicitly install their driver",
        configure_parser, run,
    )
    try:
        service = get_integration(get_hermes_home())
    except Exception:
        # Registration exceptions otherwise unload the plugin and silently remove
        # its permission middleware. Keep a concrete denial until storage can be
        # recovered and this backend is explicitly reopened.
        def unavailable(*args, **identity):
            return json.dumps({
                "error": "Realm permission storage is unavailable. Execution is paused; recover the original ownership store and reopen this backend. No guest was changed.",
                "error_code": "legacy_permission_review_required",
            })
        ctx.register_middleware("tool_execution", unavailable)
        ctx.register_tool("realm", "realms", SCHEMA, unavailable, check_fn=lambda: sys.platform == "linux")
        ctx.register_command("realm", unavailable, description="Realm permission storage requires recovery")
        return

    def command(raw, **identity):
        try:
            return json.dumps(service.command(raw, **identity))
        except Exception as exc:
            return json.dumps(
                {
                    "error": str(exc)
                    if isinstance(exc, (ValueError, PermissionError, SessionExecutionError))
                    else "Realm operation failed; host fallback is disabled."
                }
            )

    def tool(args, **identity):
        # Caller metadata and tool arguments cannot impersonate manual reenable.
        identity["_agent"] = True
        return command(_raw_command(args), **identity)

    # A missing optional driver must not hide its own management/setup tools.
    ctx.register_tool("realm", "realms", SCHEMA, tool, check_fn=lambda: sys.platform == "linux")
    ctx.register_command(
        "realm",
        command,
        description="Private desktop: on [omarchy], off, status, review permissions, size, stop, repair, watch, shot, push, pull",
    )
    ctx.register_hook("pre_tool_call", service.pre_tool)
    ctx.register_middleware("tool_execution", service.execution_middleware)
    ctx.register_hook("on_session_identity", service.bind)
    ctx.register_hook("on_session_start", service.bind)
    ctx.register_hook("on_session_finalize", service.finalize)
    ctx.register_hook("on_session_reset", service.reset)
    continuation = _load_runtime("setup_continuation")
    ctx.register_hook("on_session_identity", lambda **kw: continuation.capture_source(service, **kw))
    ctx.register_hook("on_session_idle", lambda **kw: continuation.deliver(service, **kw))
    ctx.on_unload(service.unload)
    from tools.computer_use.targets import register_target_resolver
    from tools.terminal_targets import register_terminal_target_resolver
    service._target_disposers.append(register_target_resolver(
        "hermes-realms", service.computer_use_context, hermes_home=service.home,
        selector=service.select_computer_use_target,
    ))
    service._target_disposers.append(register_terminal_target_resolver(
        "realm", service.terminal_context, hermes_home=service.home,
        selector=service.select_terminal_target,
    ))
    ctx.register_skill(
        "realms",
        Path(__file__).resolve().parent / "skills/realms/SKILL.md",
        description="Choose private realm versus explicit host desktop and manage its lifetime.",
    )
