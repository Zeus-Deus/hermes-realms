"""Real private-UNIX WayVNC transport and active viewer revocation."""

from pathlib import Path
import tempfile
import time
import unittest
from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus


class UnixViewerTests(unittest.TestCase):
    def test_private_unix_transport_and_active_revocation(self):
        from realms.manager import Manager
        from realms.bridge import ViewerServer

        with tempfile.TemporaryDirectory(prefix="viewer-unix-") as home:
            manager = Manager(home)
            record = manager.start("viewer-unix")
            server = None
            try:
                # Use the manager-owned peer, not an unrecorded second WayVNC.
                endpoint = Path(record["vnc_socket"])
                self.assertTrue(endpoint.is_socket())
                self.assertEqual(endpoint.stat().st_mode & 0o777, 0o600)
                authority_dir = Path(home) / "viewer-authority"
                server = ViewerServer(
                    lambda rid: record if rid == record["id"] else None,
                    state_dir=authority_dir,
                ).start()
                ticket = server.issue(record["id"], can_control=True)
                uri = (
                    server.origin.replace("http:", "ws:")
                    + "/api/realms/"
                    + record["id"]
                    + "/vnc"
                )
                with connect(
                    uri + "?control=1",
                    origin=server.origin,
                    subprotocols=["binary", "realm." + ticket],
                ) as ws:
                    self.assertEqual(ws.recv(timeout=5), b"RFB 003.008\n")
                    self.assertTrue(server.is_controlled(record["id"]))
                    with self.assertRaises(InvalidStatus):
                        connect(
                            uri + "?control=1",
                            origin=server.origin,
                            subprotocols=["binary", "realm." + ticket],
                        )
                    import subprocess
                    import sys

                    code = "from realms.viewer_state import ControlAuthority; import sys; a=ControlAuthority(sys.argv[1]); assert a.controlled(sys.argv[2]); a.revoke(sys.argv[2])"
                    result = subprocess.run(
                        [sys.executable, "-c", code, str(authority_dir), record["id"]],
                        cwd=Path(__file__).resolve().parents[1],
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    with self.assertRaises(ConnectionClosed):
                        ws.recv(timeout=3)
                deadline = time.monotonic() + 3
                while (
                    server.is_controlled(record["id"]) and time.monotonic() < deadline
                ):
                    time.sleep(0.05)
                self.assertFalse(server.is_controlled(record["id"]))
            finally:
                if server:
                    server.stop()
                manager.stop(record["id"])
