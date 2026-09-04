"""Independent Python processes must agree on takeover and revocation."""

import importlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class SharedViewerTests(unittest.TestCase):
    def test_profile_factory_is_lazy_and_scoped(self):
        bridge = importlib.import_module("realms.bridge")
        self.assertTrue(
            hasattr(bridge, "get_profile_viewer"), "Profile viewer factory missing"
        )
        with (
            tempfile.TemporaryDirectory() as first,
            tempfile.TemporaryDirectory() as second,
        ):
            a = bridge.get_profile_viewer(first)
            b = bridge.get_profile_viewer(second)
            self.assertIs(bridge.get_profile_viewer(first), a)
            self.assertIsNot(a, b)
            self.assertEqual(a.origin, "")
            a.authority.acquire("r", "g", "lease")
            self.assertTrue(bridge.get_profile_viewer(first).is_controlled("r"))
            self.assertFalse(b.is_controlled("r"))
            a.authority.release("r", "lease")
            bridge.close_profile_viewer(first)
            bridge.close_profile_viewer(second)

    def test_control_lease_and_revocation_cross_process(self):
        try:
            Authority = importlib.import_module("realms.viewer_state").ControlAuthority
        except ModuleNotFoundError:
            self.fail("Viewer takeover authority must span backend processes")
        with tempfile.TemporaryDirectory() as directory:
            authority = Authority(directory)
            self.assertTrue(authority.acquire("realm", "generation", "lease-a"))
            code = 'from realms.viewer_state import ControlAuthority; import sys; a=ControlAuthority(sys.argv[1]); assert a.controlled("realm"); assert not a.acquire("realm","generation","lease-b"); a.revoke("realm"); print(a.epoch("realm"))'
            result = subprocess.run(
                [sys.executable, "-c", code, directory],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "1")
            self.assertEqual(authority.epoch("realm"), 1)
            authority.release("realm", "wrong-lease")
            self.assertTrue(authority.controlled("realm"))
            authority.release("realm", "lease-a")
            self.assertFalse(authority.controlled("realm"))
            self.assertTrue(authority.acquire("realm", "generation", "lease-c"))
            self.assertTrue(authority.refresh("realm", "lease-c"))
            authority.release("realm", "lease-c")
