"""One-line, human answers for ``/realm`` commands whose result is this conversation's status.

The model's ``realm`` tool and ``/realm ... --json`` keep the full status dict; a person typing
``/realm on`` reads ``Realm on · omarchy realm · live`` instead of that dict.
"""

import shlex

KIND_LABELS = {"realm": "realm", "omarchy-vm": "omarchy realm"}
# Actions that answer with the status itself (``RealmIntegration.command`` returns ``status()``).
STATUS_ACTIONS = ("on", "off", "status", "stop", "size")
_DOCTOR = {"realm": "hermes realms doctor", "omarchy-vm": "hermes realms vm doctor"}


def slash_request(raw):
    """``(human, raw)``: whether a slash answer is summarised, and the command without ``--json``.

    Other actions (watch, shot, push, pull, review, repair) answer with their own result as
    before, and their arguments, which may be paths, are passed on untouched.
    """
    try:
        parts = shlex.split(raw or "")
    except ValueError:
        return False, raw  # the command parser reports the quoting error as before
    if (parts[0] if parts else "status") not in STATUS_ACTIONS:
        return False, raw
    if "--json" in parts:
        return False, shlex.join(part for part in parts if part != "--json")
    return True, raw


def render(result):
    """The slash answer for one command result (a status dict or ``{"error": ...}``)."""
    if isinstance(result, dict) and result.get("error"):
        return "Realm: " + " ".join(str(result["error"]).split())
    return summary(result)


def _windows(count):
    return f" ({count} window{'' if count == 1 else 's'})" if isinstance(count, int) else ""


def _detail(row):
    state = row.get("state") or "unknown"
    if state == "live":
        return "live · you have control" if row.get("controlled") else "live" + _windows(row.get("window_count"))
    if row.get("error"):
        error = " ".join(str(row["error"]).split())
        return "needs attention: " + (error if len(error) <= 120 else error[:119] + "…")
    return state.replace("-", " ")


def summary(status):
    """``Realm on · omarchy realm · live (1 window)``, ``Realm off``, ``Realm: setup needed …``."""
    if (status.get("permission") or {}).get("state", "optional") != "optional":
        return "Realm: review needed — run /realm review"
    rows = status.get("realms") or []
    live = [row for row in rows if row.get("state") == "live"]
    shown = (live or rows or [None])[0]
    kind = (shown or {}).get("kind") or status.get("requested_kind") or status.get("kind") or "realm"
    name = KIND_LABELS.get(kind, kind)
    if status.get("mode") != "realm":
        return "Realm off" + (f" · {name} still running, the agent won't use it" if live else "")
    if not status.get("requested"):
        return "Realm available · none yet; the agent starts one when a test needs it"
    setup = status.get("vm_setup") if kind == "omarchy-vm" else status.get("setup")
    if not live and not (setup or {}).get("ready", True):
        return f"Realm: setup needed — run {_DOCTOR.get(kind, _DOCTOR['realm'])}"
    if shown is None:
        return f"Realm on · {name} · starts when needed"
    more = f" (+{len(rows) - 1} more)" if len(rows) > 1 else ""
    return f"Realm on · {name} · {_detail(shown)}{more}"
