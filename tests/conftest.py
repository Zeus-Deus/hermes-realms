"""Run the Realms tests against a Hermes checkout, using the plugin from THIS repository.

Hermes' own ``tests/conftest.py`` provides the hermetic test environment (temporary
HOME/HERMES_HOME, credential scrubbing, live-system guards, OS markers). It is loaded
here as a plugin so both suites share the same isolation rules.
"""
import os
from pathlib import Path
import sys

import pytest

from realms_test_paths import HERMES_ROOT, PLUGIN_ROOT

if not (HERMES_ROOT / "hermes_cli").is_dir() or not (HERMES_ROOT / "tests" / "conftest.py").is_file():
    raise pytest.UsageError(
        "Set HERMES_AGENT_DIR to a Hermes source checkout (see docs/testing.md); "
        f"got {os.environ.get('HERMES_AGENT_DIR')!r}"
    )
if (HERMES_ROOT / "plugins" / "hermes-realms").exists():
    raise pytest.UsageError(
        "HERMES_AGENT_DIR bundles its own plugins/hermes-realms; use a Hermes checkout without it "
        "so these tests exercise this repository's plugin"
    )

for path in (str(HERMES_ROOT), str(Path(__file__).resolve().parent)):
    if path not in sys.path:
        sys.path.insert(0, path)

pytest_plugins = ["tests.conftest"]
