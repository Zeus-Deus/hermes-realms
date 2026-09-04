"""Real bubblewrap mount/PID/network boundaries, not environment-only checks."""

import importlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest


class DriverContainmentTests(unittest.TestCase):
    def test_real_driver_captures_inside_namespaces(self):
        binary = os.environ.get("REALMS_TEST_CUA_BINARY")
        if not binary:
            self.skipTest("Set REALMS_TEST_CUA_BINARY to an actual cua-driver >=0.23.2")
        from realms.driver import sandbox_command
        from realms.manager import Manager
        import time

        with tempfile.TemporaryDirectory(prefix="driver-live-") as home:
            manager = Manager(home)
            realm = manager.start("driver-live")
            try:
                env = manager.env(realm["id"])
                from realms.driver import create_driver_launcher

                launcher = create_driver_launcher(manager, realm["id"], binary)
                self.assertEqual(
                    create_driver_launcher(manager, realm["id"], binary), launcher
                )
                version = subprocess.run(
                    [launcher, "--version"],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                self.assertEqual(version.returncode, 0, version.stderr)
                self.assertIn("0.23.2", version.stdout)
                endpoint = str(Path(realm["runtime_dir"]) / "isolated-cua.sock")
                process = manager.exec(
                    realm["id"],
                    sandbox_command(
                        realm,
                        binary,
                        [
                            "serve",
                            "--embedded",
                            "--permission-mode",
                            "standard",
                            "--socket",
                            endpoint,
                        ],
                    ),
                )
                deadline = time.monotonic() + 15
                while not Path(endpoint).exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                if not Path(endpoint).exists():
                    self.fail(Path(process["stderr_path"]).read_text())
                result = subprocess.run(
                    sandbox_command(
                        realm,
                        binary,
                        ["call", "get_desktop_state", "{}", "--socket", endpoint],
                    ),
                    env=env,
                    text=True,
                    capture_output=True,
                    timeout=30,
                )
                self.assertEqual(
                    result.returncode, 0, result.stderr + result.stdout[:1000]
                )
                data = json.loads(result.stdout)
                import base64

                self.assertEqual(
                    (data["screen_width"], data["screen_height"]), (1920, 1080)
                )
                self.assertTrue(
                    base64.b64decode(data["screenshot_png_b64"]).startswith(
                        b"\x89PNG\r\n\x1a\n"
                    )
                )
                fixture = Path(realm["runtime_dir"]) / "fixture.json"
                manager.exec(
                    realm["id"],
                    [
                        "/usr/bin/python3",
                        str(Path(__file__).parent / "fixtures/gtk_probe.py"),
                        str(fixture),
                        "Containment",
                    ],
                )
                deadline = time.monotonic() + 8
                while not fixture.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(fixture.exists())
                # Observe the actual surface first; 600x360 centered on 1920x1080.
                captured = subprocess.run(
                    [launcher, "call", "get_desktop_state", "{}", "--socket", endpoint],
                    env=env,
                    text=True,
                    capture_output=True,
                    timeout=15,
                )
                self.assertEqual(captured.returncode, 0, captured.stderr)
                args = {
                    "x": 960,
                    "y": 508,
                    "target": {"kind": "desktop", "display_id": "primary"},
                    "delivery_mode": "foreground",
                    "session": "containment",
                }
                result = subprocess.run(
                    [launcher, "call", "click", json.dumps(args), "--socket", endpoint],
                    env=env,
                    text=True,
                    capture_output=True,
                    timeout=15,
                )
                self.assertEqual(
                    result.returncode, 0, result.stderr + result.stdout[:1000]
                )
                deadline = time.monotonic() + 5
                while (
                    json.loads(fixture.read_text())["clicks"] == 0
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.05)
                self.assertEqual(
                    json.loads(fixture.read_text())["clicks"], 1, result.stdout[:1200]
                )
            finally:
                manager.stop(realm["id"])

    def test_private_runtime_visible_but_host_socket_and_devices_hidden(self):
        try:
            build = importlib.import_module("realms.driver").sandbox_command
        except ModuleNotFoundError:
            self.fail("Owned cua driver needs actual socket/device containment")
        with tempfile.TemporaryDirectory(prefix="driver-home-") as home:
            from realms.manager import Manager

            manager = Manager(home)
            realm = manager.start("containment-test")
            try:
                env = manager.env(realm["id"])
                with tempfile.TemporaryDirectory(
                    prefix="reviewed-manifest-"
                ) as approved:
                    manifest = Path(approved) / "policy.json"
                    manifest.write_text('{"fixture":"reviewed policy bytes"}')
                    code = 'import pathlib,sys; p=pathlib.Path(sys.argv[2]); print(p.read_text());\ntry: p.write_text("changed")\nexcept OSError: print("READ_ONLY")'
                    check = subprocess.run(
                        build(
                            realm,
                            "/usr/bin/python3",
                            ["-c", code, "--capability-manifest", str(manifest)],
                        ),
                        env=env,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(check.returncode, 0, check.stderr)
                    self.assertEqual(
                        check.stdout.strip(), manifest.read_text() + "\nREAD_ONLY"
                    )
                host_socket = (
                    Path("/run/user") / str(os.getuid()) / "realms-host-canary.sock"
                )
                with socket.socket(socket.AF_UNIX) as listener:
                    listener.bind(str(host_socket))
                    try:
                        code = (
                            'import os,json; print(json.dumps({"private":os.path.exists(os.environ["XDG_RUNTIME_DIR"]+"/"+os.environ["WAYLAND_DISPLAY"]),"host":os.path.exists('
                            + repr(str(host_socket))
                            + '),"uinput":os.path.exists("/dev/uinput"),"input":os.path.exists("/dev/input"),"home":os.path.exists('
                            + repr(str(Path.home()))
                            + '),"pids":len([p for p in os.listdir("/proc") if p.isdigit()])}))'
                        )
                        result = subprocess.run(
                            build(realm, "/usr/bin/python3", ["-c", code]),
                            env=env,
                            capture_output=True,
                            text=True,
                            check=True,
                        )
                        data = json.loads(result.stdout)
                        self.assertTrue(data["private"])
                        self.assertFalse(data["host"])
                        self.assertFalse(data["uinput"])
                        self.assertFalse(data["input"])
                        self.assertFalse(data["home"])
                        self.assertLess(data["pids"], 8)
                    finally:
                        host_socket.unlink()
            finally:
                manager.stop(realm["id"])


if __name__ == "__main__":
    unittest.main()
