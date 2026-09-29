"""Explicit enable-time setup, invoked only by the host's consent lifecycle."""
from pathlib import Path
from dataclasses import asdict
import json
import os
import platform
import runpy


def _load(name):
    return runpy.run_path(str(Path(__file__).resolve().parent / "realms/_binding.py"))["load_runtime"](name)


def _revision(installer):
    return f"{installer.VERSION}:{installer.ARCHIVE_SHA256}:{installer.BINARY_SHA256}"


def _receipt_data(home, target, installer):
    config = _load("config")
    return {
        "profile": str(config.effective_home(home)),
        "revision": _revision(installer),
        "driver": installer._execution_identity(target),
        "config": asdict(config.Config.load(home)),
    }


# Scope digest shown by describe in this module instance. The host's runner
# calls describe and then run in the same instance, so run releases exactly
# what the user reviewed and refuses a scope that changed in between.
_REVIEWED = {}


def _earlier_chats(hermes_home):
    """(pending, revision part, detail lines) for the one-decision bulk review."""
    review = _load("bulk_review").preview(hermes_home)
    released, held = len(review["eligible"]), len(review["kept"])
    pending = bool(released) or review["decision"] is None
    _REVIEWED[str(hermes_home)] = review["digest"]
    lines = [
        f"{released} earlier chats with no Realm use will run on your normal desktop "
        "(agent outside the Realm; Realm available as a tool).",
        f"{held} chats that used a Realm keep asking for /realm review.",
    ]
    if review["decision"] is None:
        lines.append("Older chats Realms has no record of yet are treated the same way when reopened. "
                     "Nothing is started, stopped or deleted, and no tool is replayed.")
    return pending, review["digest"] if pending else review["decision"]["digest"], lines


def describe(hermes_home):
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64"):
        return {
            "revision": "unsupported-platform",
            "ready": False,
            "driver_revision": "unsupported-platform",
            "driver_ready": False,
            "summary": "Realms setup requires Linux x86-64",
            "details": ["No verified Cua release is available for this host. Leave Realms disabled."],
        }
    installer = _load("install_driver")
    target = _load("config").driver_path(hermes_home)
    status = _load("integration").setup_status(driver_executable=target)
    completed = False
    if status["ready"] and not os.access(target.parent, os.W_OK | os.X_OK):
        status = {
            "ready": False,
            "message": "Realms setup cannot update its driver directory. Restore this profile's directory write/search permissions and retry setup.",
        }
    if status["ready"]:
        try:
            installer.profile_target(hermes_home)
            completed = json.loads(target.with_name(".realms-setup.json").read_text(encoding="utf-8")) == _receipt_data(hermes_home, target, installer)
        except (OSError, ValueError, ImportError):
            # Hermes describes setup before installing the plugin's declared
            # dependencies. Settings that cannot be read yet are not proof of a
            # completed setup: run rewrites the receipt once they are present.
            completed = False
    # One decision for every earlier chat with no recorded Realm use. The
    # scope is part of the revision, so a changed count re-requests consent.
    pending, scope, earlier = _earlier_chats(hermes_home)
    return {
        "revision": _revision(installer) + ":" + scope,
        "ready": completed and not pending,
        # The in-app per-conversation setup only installs and checks the driver.
        "driver_revision": _revision(installer),
        "driver_ready": completed,
        "summary": f"Install and verify Cua driver {installer.VERSION} for Realms",
        "details": [
            installer.URL,
            f"Destination: {target}",
            f"Archive SHA-256: {installer.ARCHIVE_SHA256}",
            f"Binary SHA-256: {installer.BINARY_SHA256}",
            "Downloads the pinned release if needed, writes only this profile's driver, and executes --version to verify it.",
            "Does not install system packages, change the global Cua driver, start a desktop, or alter approvals.",
            status["message"],
            *earlier,
        ],
    }


def run(hermes_home, *, progress=None):
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64"):
        raise ValueError("Realms setup requires Linux x86-64; leave this plugin disabled on this host")
    reviewed = _REVIEWED.get(str(hermes_home))
    if reviewed is not None:
        # Enable-time consent: the host described this setup in this module
        # instance, and the consent covers exactly the chats listed there.
        # The in-app driver setup runs without a description and releases nothing.
        _load("bulk_review").release(hermes_home, provenance="setup-consent", expected=reviewed)
    installer = _load("install_driver")
    target = installer.profile_target(hermes_home)
    receipt = target.with_name(".realms-setup.json")
    receipt.unlink(missing_ok=True)
    installer.install(home=hermes_home, **({"progress": progress} if progress is not None else {}))
    status = _load("integration").setup_status(driver_executable=target)
    if not status["ready"]:
        raise ValueError(status["message"])
    report = _load("manager").Manager(hermes_home).doctor()
    if not report["ok"]:
        raise ValueError("Realms setup failed: a working systemd user manager is required. Run hermes realms doctor in this profile; host fallback is disabled.")
    installer.write_receipt(receipt, _receipt_data(hermes_home, target, installer))
