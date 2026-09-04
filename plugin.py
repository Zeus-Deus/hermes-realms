"""Standalone native Hermes registration. Nothing patches the host or prompt."""

import json
from pathlib import Path

from hermes_constants import get_hermes_home
from realms.integration import get_integration, requirements_available


SCHEMA = {
    "name": "realm",
    "description": "Manage this conversation’s private desktop. Use on for private realm, off ONLY for explicit user-requested host access, status, size, stop or watch. Use shot only when realm Cua app=screen capture fails; never fall back to host capture. Load skill hermes-realms:realms for natural-language desktop intent. Existing approvals always apply.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["on", "off", "status", "size", "stop", "watch", "shot"],
            },
            "size": {"type": "string", "description": "WIDTHxHEIGHT for action=size"},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}


def register(ctx):
    service = get_integration(get_hermes_home())

    def command(raw, **identity):
        try:
            return json.dumps(service.command(raw, **identity))
        except Exception as exc:
            return json.dumps(
                {
                    "error": str(exc)
                    if isinstance(exc, (ValueError, PermissionError))
                    else "Realm operation failed; host fallback is disabled."
                }
            )

    def tool(args, **identity):
        action = args.get("action", "status")
        raw = action + (" " + args.get("size", "") if action == "size" else "")
        return command(raw, **identity)

    ctx.register_tool("realm", "realms", SCHEMA, tool, check_fn=requirements_available)
    ctx.register_command(
        "realm",
        command,
        description="Private desktop: on, off, status, size, stop, watch, shot",
    )
    ctx.register_hook("pre_tool_call", service.pre_tool)
    ctx.register_hook("on_session_identity", service.bind)
    ctx.register_hook("on_session_start", service.bind)
    ctx.register_hook("on_session_finalize", service.finalize)
    ctx.register_hook("on_session_reset", service.finalize)
    ctx.on_unload(service.unload)
    ctx.register_skill(
        "realms",
        Path(__file__).resolve().parent / "skills/realms/SKILL.md",
        description="Choose private realm versus explicit host desktop and manage its lifetime.",
    )
