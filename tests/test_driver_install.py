import importlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class DriverInstallTests(unittest.TestCase):
    def test_verified_release_installs_and_executes_idempotently(self):
        archive = os.environ.get("REALMS_TEST_CUA_ARCHIVE")
        if not archive:
            self.skipTest("Provide the real pinned release archive")
        try:
            install = importlib.import_module("realms.install_driver").install_archive
        except ModuleNotFoundError:
            self.fail("A verified portable driver installer is required")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "cua-driver"
            install(Path(archive), target)
            result = subprocess.run(
                [target, "--version"], capture_output=True, text=True, check=True
            )
            self.assertIn("0.23.2", result.stdout)
            original = target.stat().st_ino
            install(Path(archive), target)
            self.assertEqual(target.stat().st_ino, original)
            corrupted = Path(directory) / "bad.tar.gz"
            corrupted.write_bytes(b"not the approved release")
            with self.assertRaises(ValueError):
                install(corrupted, target)
            self.assertEqual(target.stat().st_ino, original)
