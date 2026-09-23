"""Native Delete review; lifecycle and filesystem ownership stay in managers."""
import hashlib
import hmac
import json
import re
from pathlib import Path

from hermes_constants import get_hermes_home
from .integration import OwnerError


def _review(service, owner, identity, realm_id):
    if Path(get_hermes_home()).resolve() != service.home:
        raise OwnerError("Delete profile changed")
    if service.owners.resolve(**identity) != owner:
        raise OwnerError("Delete owner changed")
    if not re.fullmatch(r"[rv]-[0-9a-f]{24}", realm_id):
        raise ValueError("Invalid Delete target")
    kind = "omarchy-vm" if realm_id.startswith("v-") else "realm"
    manager = service.vm if kind == "omarchy-vm" else service.manager
    if not manager.registry.path(realm_id).exists():
        raise FileNotFoundError(realm_id)
    snapshot = manager.delete_snapshot(realm_id, session_id=owner)
    scope = {"home": str(service.home), "owner": owner, "identity": identity,
             "kind": kind, "realm_id": realm_id, "snapshot": snapshot}
    digest = hashlib.sha256(json.dumps(scope, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return manager, snapshot, {"realm_id": realm_id, "kind": kind,
        "state": snapshot["record"]["status"], "consent": digest,
        "details": ["Permanently delete this stopped workspace and all guest work in it.",
                    "Other workspaces and shared base images are retained. This cannot be undone."]}


def prepare(service, owner, identity, realm_id):
    with service._lock, service.owners.activation_guard(owner):
        return _review(service, owner, identity, realm_id)[2]


def confirm(service, owner, identity, realm_id, consent):
    # Same lock order as setup/restart. The manager still fences concurrent
    # registry publication and workspace replacement at its deletion boundary.
    with service._lock, service.owners.activation_guard(owner):
        manager, snapshot, review = _review(service, owner, identity, realm_id)
        if not re.fullmatch(r"[0-9a-f]{64}", consent) or not hmac.compare_digest(consent, review["consent"]):
            raise ValueError("Delete review changed")
        deleted = manager.delete(realm_id, session_id=owner, expected_snapshot=snapshot)
        if not deleted:
            raise FileNotFoundError(realm_id)
        return {"realm_id": realm_id, "deleted": True}
