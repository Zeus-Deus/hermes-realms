"""Setup describe runs before Hermes installs the plugin's declared dependencies.

The host describes setup in an isolated child of the dependency environment,
asks for consent, and only then installs ``python_dependencies`` for run. So
describe must work, and report the truth, with none of them importable.
"""
import json
import subprocess
import sys

import pytest
from realms_test_paths import HERMES_ROOT, PLUGIN_ROOT
from test_realms_review_unused import consented_run, legacy_store, setup_module  # noqa: F401  (fixture)

pytestmark = pytest.mark.platforms("linux")

# Runs before the host's own runner in the same isolated child: no declared
# dependency can be imported, and the fixture driver's pins and system probes
# are applied to the runtime that setup.py itself loads.
_PRELUDE = r'''
import importlib.abc, json, runpy, sys
fixture = json.loads(sys.argv.pop())
DECLARED = {"yaml", "fastapi", "uvicorn", "websockets", *fixture["blocked"]}
class _Undeclared(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in DECLARED:
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return None
sys.meta_path.insert(0, _Undeclared())
for name in list(sys.modules):
    if name.split(".")[0] in DECLARED:
        del sys.modules[name]
sys.path.insert(0, fixture["hermes"])
load = runpy.run_path(fixture["binding"])["load_runtime"]
installer = load("install_driver")
installer.ARCHIVE_SHA256, installer.BINARY_SHA256 = fixture["archive"], fixture["binary"]
load("integration").setup_status = lambda driver_executable: {
    "ready": installer.execution_verified(driver_executable), "message": "fixture dependencies"}
'''


def describe_without_dependencies(home, *, blocked=()):
    from hermes_cli.plugins_setup import _RUNNER
    installer = __import__("runpy").run_path(str(PLUGIN_ROOT / "realms/_binding.py"))["load_runtime"]("install_driver")
    fixture = {"hermes": str(HERMES_ROOT), "binding": str(PLUGIN_ROOT / "realms/_binding.py"),
               "archive": installer.ARCHIVE_SHA256, "binary": installer.BINARY_SHA256,
               "blocked": list(blocked)}
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", _PRELUDE + _RUNNER, str(PLUGIN_ROOT / "setup.py"),
         "describe", str(home), "", json.dumps(fixture)],
        cwd=PLUGIN_ROOT, env={"HERMES_HOME": str(home), "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_describe_reports_a_completed_setup_without_declared_dependencies(tmp_path, setup_module):
    home = tmp_path / "profile"
    legacy_store(home)
    (home / "config.yaml").write_text("plugins:\n  realms:\n    size: 1280x720\n", encoding="utf-8")
    consented_run(setup_module(), home)
    expected = setup_module()["describe"](home)
    assert expected["ready"] and expected["driver_ready"]

    assert describe_without_dependencies(home) == expected


def test_describe_without_dependencies_sees_a_changed_config_as_incomplete(tmp_path, setup_module):
    home = tmp_path / "profile"
    home.mkdir()
    consented_run(setup_module(), home)
    (home / "config.yaml").write_text("plugins:\n  realms:\n    size: 1280x720\n", encoding="utf-8")
    described = describe_without_dependencies(home)
    assert described["driver_ready"] is False and described["ready"] is False
    assert described["revision"] == setup_module()["describe"](home)["revision"]


@pytest.mark.parametrize("config", ["plugins:\n  realms: [\n", "plugins:\n  realms:\n    size: tiny\n"])
def test_describe_without_dependencies_treats_unreadable_settings_as_incomplete(tmp_path, setup_module, config):
    home = tmp_path / "profile"
    home.mkdir()
    consented_run(setup_module(), home)
    (home / "config.yaml").write_text(config, encoding="utf-8")
    described = describe_without_dependencies(home)
    assert described["driver_ready"] is False and described["ready"] is False


def test_describe_treats_an_unimportable_settings_reader_as_incomplete(tmp_path, setup_module):
    """A host whose config reader cannot import is asked to run setup, not crashed."""
    home = tmp_path / "profile"
    legacy_store(home)
    consented_run(setup_module(), home)
    expected = setup_module()["describe"](home)
    described = describe_without_dependencies(home, blocked=["ruamel", "hermes_yaml"])
    assert described["driver_ready"] is False and described["ready"] is False
    assert described["revision"] == expected["revision"]


def test_describe_without_dependencies_before_any_install(tmp_path):
    home = tmp_path / "profile"
    legacy_store(home)
    described = describe_without_dependencies(home)
    assert described["ready"] is False and described["driver_ready"] is False
    assert "4 earlier chats with no Realm use" in "\n".join(described["details"])
