"""Locations shared by the Realms tests: this plugin checkout and the Hermes source tree.

The tests exercise the plugin through real Hermes modules, so they need a Hermes
checkout. Point ``HERMES_AGENT_DIR`` at one (see ``docs/testing.md``).
"""
import os
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
# Self-launching test helpers re-run their own file in an isolated child (``-I``, minimal
# environment) with the Hermes checkout as the working directory.
_ISOLATED_CHILD_ROOT = Path.cwd() if (Path.cwd() / "hermes_cli").is_dir() else Path("hermes-agent-dir-unset")
HERMES_ROOT = Path(os.environ.get("HERMES_AGENT_DIR") or _ISOLATED_CHILD_ROOT).expanduser().resolve()


def install_user_plugin(home):
    """Install this checkout into ``home`` the way ``hermes plugins install`` does: a user plugin."""
    target = Path(home) / "plugins" / "hermes-realms"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.symlink_to(PLUGIN_ROOT, target_is_directory=True)
    return target
