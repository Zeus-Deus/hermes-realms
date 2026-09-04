"""Native directory-plugin entry point (also usable without a pip install)."""

from pathlib import Path
import sys

# Native and dashboard loaders use different module names. One canonical package
# keeps profile infrastructure shared inside a process; durable state spans them.
_root = str(Path(__file__).resolve().parent)
if _root not in sys.path:
    sys.path.append(_root)


def register(ctx):
    from .plugin import register as register_plugin

    return register_plugin(ctx)
