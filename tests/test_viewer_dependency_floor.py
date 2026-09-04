"""Exercise the actual viewer listener at the package's declared dependency floors."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib
import unittest


class ViewerFloorTests(unittest.TestCase):
    def test_declared_minimum_dependencies_start_http_and_websocket(self):
        uv = shutil.which("uv")
        if uv is None:
            self.skipTest("uv is required for the isolated minimum-dependency lane")
        root = Path(__file__).resolve().parents[1]
        dependencies = tomllib.loads((root / "pyproject.toml").read_text())["project"][
            "dependencies"
        ]
        # Test packaging metadata behavior, not a frozen expected version.
        floors = [
            dependency.replace(">=", "==").split(",")[0] for dependency in dependencies
        ]
        with tempfile.TemporaryDirectory(prefix="viewer-floor-") as directory:
            python = str(Path(directory) / "bin/python")
            subprocess.run(
                [uv, "venv", "--python", "3.11", directory],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [uv, "pip", "install", "--python", python, *floors],
                check=True,
                capture_output=True,
                text=True,
            )
            code = """
import urllib.request
import uvicorn, fastapi, websockets
from websockets.sync.client import connect
from websockets.exceptions import InvalidStatus
from realms.bridge import ViewerServer
server = ViewerServer(lambda rid: None)
try:
    server.start()
    with urllib.request.urlopen(server.origin + "/realms/test/view", timeout=5) as response:
        assert response.status == 200
        assert b"Take over" in response.read()
    try:
        with connect(server.origin.replace("http:", "ws:") + "/api/realms/test/vnc",
                     origin=server.origin, subprotocols=["binary", "realm.invalid"]):
            raise AssertionError("unauthorized websocket accepted")
    except InvalidStatus as exc:
        assert exc.response.status_code == 403
    print("HTTP 200; WebSocket authorization 403; uvicorn=" + uvicorn.__version__
          + "; fastapi=" + fastapi.__version__ + "; websockets=" + websockets.__version__)
finally:
    server.stop()
"""
            result = subprocess.run(
                [python, "-c", code],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print(result.stdout.strip())


if __name__ == "__main__":
    unittest.main()
