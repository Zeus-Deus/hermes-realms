"""Owned compositor fixtures for product viewer resolution and prebound peers."""

from pathlib import Path
import tempfile
import unittest

from realms.bridge import close_profile_viewer, get_profile_viewer
from realms.manager import Manager


class ProductViewerTests(unittest.TestCase):
    def test_resolver_validates_live_scope_without_renewing_activity(self):
        with tempfile.TemporaryDirectory(prefix="viewer-live-review-") as home:
            manager = Manager(home)
            record = manager.start("viewer-live-review")
            server = get_profile_viewer(home)
            try:
                current = server.resolve_realm(record["id"])
                assert current is not None
                self.assertEqual(current["id"], record["id"])
                with manager.registry.lock():
                    saved = manager.registry.get(record["id"])
                    self.assertEqual(saved["last_activity"], record["last_activity"])
                    manager.registry.put(
                        dict(saved, invocation_id="not-the-owned-invocation")
                    )
                self.assertIsNone(server.resolve_realm(record["id"]))
            finally:
                with manager.registry.lock():
                    manager.registry.put(record)
                close_profile_viewer(home)
                manager.stop(record["id"])

    def test_product_prebound_peer_and_crossprocess_revocation(self):
        import os
        import socket
        import struct
        import subprocess
        import sys
        from websockets.sync.client import connect
        from websockets.exceptions import ConnectionClosed

        with tempfile.TemporaryDirectory(prefix="viewer-peer-live-") as home:
            manager = Manager(home)
            record = manager.start("viewer-peer-live")
            server = get_profile_viewer(home)
            try:
                with socket.socket(socket.AF_UNIX) as peer:
                    peer.connect(record["vnc_socket"])
                    pid, uid, _ = struct.unpack(
                        "3i", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                    )
                    self.assertIn(
                        pid,
                        (
                            record["processes"]["worker"]["pid"],
                            record["processes"]["vnc"]["pid"],
                        ),
                    )
                    self.assertEqual(uid, os.getuid())
                server.start()
                token = server.issue(record["id"], can_control=True)
                uri = (
                    server.origin.replace("http:", "ws:")
                    + "/api/realms/"
                    + record["id"]
                    + "/vnc?control=1"
                )
                with connect(
                    uri, origin=server.origin, subprotocols=["binary", "realm." + token]
                ) as ws:
                    self.assertEqual(ws.recv(timeout=5), b"RFB 003.008\n")
                    self.assertTrue(server.is_controlled(record["id"]))
                    code = "from realms.viewer_state import ControlAuthority; import sys; ControlAuthority(sys.argv[1]).revoke(sys.argv[2])"
                    subprocess.run(
                        [
                            sys.executable,
                            "-c",
                            code,
                            str(server.authority.directory),
                            record["id"],
                        ],
                        check=True,
                        cwd=Path(__file__).resolve().parents[1],
                        timeout=5,
                    )
                    with self.assertRaises(ConnectionClosed):
                        ws.recv(timeout=5)
            finally:
                close_profile_viewer(home)
                manager.stop(record["id"])


if __name__ == "__main__":
    unittest.main()
