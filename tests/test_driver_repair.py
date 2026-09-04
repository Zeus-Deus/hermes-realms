"""The real pinned archive must repair lost execution permission."""

import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

from realms.install_driver import install_archive


class DriverRepairTests(unittest.TestCase):
    def test_reinstall_verified_nonexecutable_driver_is_executable_and_owned(self):
        archive = os.environ.get("REALMS_TEST_CUA_ARCHIVE")
        if not archive:
            self.skipTest("Provide the real pinned release archive")
        with tempfile.TemporaryDirectory(prefix="driver-repair-") as directory:
            target = Path(directory) / "cua-driver"
            install_archive(archive, target)
            target.chmod(0o600)
            self.assertFalse(os.access(target, os.X_OK))
            installed = Path(install_archive(archive, target))
            self.assertTrue(
                os.access(installed, os.X_OK),
                "Successful reinstall left driver nonexecutable",
            )
            info = installed.lstat()
            self.assertTrue(stat.S_ISREG(info.st_mode))
            self.assertEqual(info.st_uid, os.getuid())
            version = subprocess.run(
                [installed, "--version"], check=True, capture_output=True, text=True
            )
            self.assertIn("0.23.2", version.stdout)
            print(version.stdout.strip() + "; repaired executable owned target")

    def test_noexec_target_does_not_report_success(self):
        archive = os.environ.get("REALMS_TEST_CUA_ARCHIVE")
        if not archive:
            self.skipTest("Provide the real pinned release archive")
        import sys

        with tempfile.TemporaryDirectory(prefix="driver-noexec-") as directory:
            code = """
import pathlib, subprocess, sys
from realms.install_driver import install_archive
subprocess.run(["mount", "-t", "tmpfs", "-o", "noexec,mode=700", "tmpfs", sys.argv[2]], check=True)
try:
    install_archive(sys.argv[1], pathlib.Path(sys.argv[2]) / "cua-driver")
except PermissionError:
    print("noexec installation rejected")
else:
    raise AssertionError("installer reported success on a noexec mount")
"""
            result = subprocess.run(
                ["unshare", "-Urnm", sys.executable, "-c", code, archive, directory],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=Path(__file__).resolve().parents[1],
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
