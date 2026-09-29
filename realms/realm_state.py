"""Which conversations hold Realm state or a permission hold. Standard library only.

The degraded registration path reads this when the rest of the runtime cannot
import, so keep it free of third-party dependencies.
"""
import json
import os
from pathlib import Path
import re
import stat

RECORD = re.compile(r"[rv]-[0-9a-f]{24}\.json")
# Owner columns that exist only once a conversation selected, requested or set
# up a Realm. ``mode`` is checked separately: an explicit ``host`` is a recorded
# decision, not Realm use.
STATE_COLUMNS = ("realm_kind", "requested_kind", "setup_intent")


def permission_state(home, owner, contract, mode, receipt):
    """Classify one owner row exactly as the execution middleware does."""
    state = "optional" if contract == "optional-targets-v1" else (
        "legacy-host" if contract is None and mode == "host" else "legacy-pending")
    if receipt is not None:
        try:
            accepted = json.loads(receipt)
            if (accepted["home"] != str(home) or accepted["owner"] != owner
                    or accepted["contract"] != contract):
                state = "legacy-pending"
        except (ValueError, KeyError, TypeError):
            state = "legacy-pending"
    return state


def record_owners(root):
    """Session ids named by regular-Realm and VM resource records.

    An unsafe or unreadable record raises: callers cannot tell who owns it.
    """
    owners = set()
    root = Path(root)
    if not root.exists():
        return owners
    with os.scandir(root) as entries:
        for index, entry in enumerate(entries):
            if index >= 1024:
                raise PermissionError("Too many resource records to review safely")
            if not RECORD.fullmatch(entry.name):
                continue
            fd = os.open(entry.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 1024 * 1024:  # windows-footgun: ok — runtime package rejects non-Linux hosts
                    raise PermissionError("Resource record cannot be safely reviewed")
                with os.fdopen(fd, "rb", closefd=False) as stream:
                    raw = stream.read(1024 * 1024 + 1)
            finally:
                os.close(fd)
            record = json.loads(raw)
            owner = record.get("session_id") if isinstance(record, dict) else None
            if not isinstance(owner, str) or not owner:
                raise PermissionError("Resource record has no owner")
            owners.add(owner)
    return owners
