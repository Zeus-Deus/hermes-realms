"""Explicit live lane: talks to real wayvnc + a real GTK fixture, never mocks."""

import importlib
import json
import os
import pathlib
import struct
import time
import unittest
from websockets.sync.client import connect
from websockets.exceptions import InvalidStatus


class LiveViewerTests(unittest.TestCase):
    def test_authenticated_view_only_and_takeover_against_real_wayvnc(self):
        if not os.environ.get("REALMS_TEST_VNC_PORT"):
            self.skipTest(
                "Explicit real WayVNC/GTK lane; set REALMS_TEST_VNC_PORT and REALMS_TEST_FIXTURE"
            )
        try:
            Server = importlib.import_module("realms.bridge").ViewerServer
        except ModuleNotFoundError:
            self.fail("Authenticated binary viewer bridge is not implemented")
        port = int(os.environ["REALMS_TEST_VNC_PORT"])
        state_path = pathlib.Path(os.environ["REALMS_TEST_FIXTURE"])
        server = Server(
            lambda rid: (
                {"id": "a", "generation": "probe", "vnc_port": port}
                if rid == "a"
                else None
            )
        )
        server.start()
        try:
            import urllib.request

            with urllib.request.urlopen(server.origin + "/realms/a/view") as response:
                html = response.read().decode()
                self.assertIn("Take over", html)
                self.assertIn("/assets/viewer.js", html)
                self.assertEqual(response.headers.get("Referrer-Policy"), "no-referrer")
            uri = server.origin.replace("http:", "ws:") + "/api/realms/a/vnc"
            token = server.issue("a", can_control=True)
            with self.assertRaises(InvalidStatus):
                connect(
                    uri, origin=server.origin, subprotocols=["binary", "realm.invalid"]
                )
            with self.assertRaises(InvalidStatus):
                connect(
                    uri,
                    origin="https://evil.example",
                    subprotocols=["binary", "realm." + token],
                )
            before = json.loads(state_path.read_text())
            for control, character in [(False, "v"), (True, "c")]:
                with connect(
                    uri + ("?control=1" if control else ""),
                    origin=server.origin,
                    subprotocols=["binary", "realm." + token],
                ) as ws:
                    pending = bytearray()

                    def read(n):
                        while len(pending) < n:
                            pending.extend(ws.recv(timeout=5))
                        out = bytes(pending[:n])
                        del pending[:n]
                        return out

                    version = read(12)
                    self.assertEqual(version, b"RFB 003.008\n")
                    ws.send(version)
                    self.assertIn(1, read(read(1)[0]))
                    ws.send(b"\x01")
                    self.assertEqual(read(4), b"\0" * 4)
                    ws.send(b"\x01")
                    info = read(24)
                    read(struct.unpack(">I", info[20:24])[0])
                    ws.send(
                        struct.pack(">BBHH", 5, 1, 960, 456)
                        + struct.pack(">BBHH", 5, 0, 960, 456)
                    )
                    # Position explicitly: a click in a long entry can move
                    # the caret into the middle of prior probe text.
                    ws.send(
                        struct.pack(">BBHI", 4, 1, 0, 0xFF57)
                        + struct.pack(">BBHI", 4, 0, 0, 0xFF57)
                    )
                    ws.send(
                        struct.pack(">BBHI", 4, 1, 0, ord(character))
                        + struct.pack(">BBHI", 4, 0, 0, ord(character))
                    )
                    # Wait for the actual GTK timer to advance after the network input.
                    initial = json.loads(state_path.read_text())["tick"]
                    until = time.monotonic() + 5
                    while (
                        json.loads(state_path.read_text())["tick"] < initial + 2
                        and time.monotonic() < until
                    ):
                        time.sleep(0.05)
                    after = json.loads(state_path.read_text())
                    self.assertGreaterEqual(after["tick"], initial + 2)
                    if control:
                        self.assertEqual(after["text"], before["text"] + "c")
                    else:
                        self.assertEqual(after["text"], before["text"])
                    self.assertEqual(server.is_controlled("a"), control)
            server.revoke("a")
            with self.assertRaises(InvalidStatus):
                connect(
                    uri, origin=server.origin, subprotocols=["binary", "realm." + token]
                )
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
