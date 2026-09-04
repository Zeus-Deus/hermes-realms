"""Forward-time authorization against a disposable byte sink, without polling races."""

import asyncio
import os
from pathlib import Path
import struct
import tempfile
import unittest

from starlette.websockets import WebSocket

from realms.bridge import ViewerServer
from realms.lifecycle import identity
from realms.viewer_auth import Tickets


class ForwardingTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, invalidate, *, shared=True, processes=None):
        with tempfile.TemporaryDirectory(prefix="viewer-forward-") as directory:
            runtime = Path(directory)
            endpoint = runtime / "vnc.sock"
            received = bytearray()
            sink_done = asyncio.Event()

            async def sink(reader, writer):
                try:
                    while data := await reader.read(65536):
                        received.extend(data)
                finally:
                    writer.close()
                    await writer.wait_closed()
                    sink_done.set()

            listener = await asyncio.start_unix_server(sink, path=str(endpoint))
            endpoint.chmod(0o600)
            record = dict(
                id="r",
                generation="g",
                runtime_dir=directory,
                vnc_socket=str(endpoint),
                processes=processes or {"worker": identity(os.getpid())},
            )
            server = ViewerServer(
                lambda rid: record, state_dir=runtime / "authority" if shared else None
            )
            now = [0.0]
            server.tickets = Tickets(clock=lambda: now[0])
            server.origin = "http://127.0.0.1:12345"
            token = server.issue("r", can_control=True, ttl=1)
            wire = b"RFB 003.008\n\x01\x01" + struct.pack(">BBHH", 5, 1, 12, 14)
            messages = iter(
                [
                    {"type": "websocket.connect"},
                    {"type": "websocket.receive", "bytes": wire},
                    {"type": "websocket.disconnect", "code": 1000},
                ]
            )
            sent = []

            async def receive():
                message = next(messages)
                if message["type"] == "websocket.receive":
                    # The real ASGI receive boundary: invalidation happens after
                    # connection authorization and before this frame is parsed.
                    invalidate(server, record, now)
                return message

            async def send(message):
                sent.append(message)

            ws = WebSocket(
                {
                    "type": "websocket",
                    "query_string": b"control=1",
                    "headers": [
                        (b"origin", server.origin.encode()),
                        (b"host", b"127.0.0.1:12345"),
                        (
                            b"sec-websocket-protocol",
                            ("binary, realm." + token).encode(),
                        ),
                    ],
                },
                receive,
                send,
            )
            try:
                await asyncio.wait_for(server._vnc(ws, "r"), 3)
                await asyncio.wait_for(sink_done.wait(), 3)
            finally:
                listener.close()
                await listener.wait_closed()
            return bytes(received), sent

    async def test_expired_ticket_never_forwards_input(self):
        for shared in (False, True):
            with self.subTest(shared=shared):
                received, _ = await self.exercise(
                    lambda server, record, now: now.__setitem__(0, 2), shared=shared
                )
                self.assertEqual(received, b"")

    async def test_changed_realm_binding_never_forwards_input(self):
        def epoch(server, record, now):
            import subprocess
            import sys

            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from realms.viewer_state import ControlAuthority; import sys; ControlAuthority(sys.argv[1]).revoke('r')",
                    str(server.authority.directory),
                ],
                check=True,
                cwd=Path(__file__).resolve().parents[1],
                timeout=5,
            )

        def generation(server, record, now):
            record["generation"] = "replacement"

        def endpoint(server, record, now):
            Path(record["vnc_socket"]).unlink()

        for invalidation in (epoch, generation, endpoint):
            with self.subTest(invalidation=invalidation.__name__):
                received, _ = await self.exercise(invalidation)
                self.assertEqual(received, b"")

    async def test_lost_control_lease_never_forwards_input(self):
        def expired(server, record, now):
            import sqlite3

            with sqlite3.connect(server.authority.path) as db:
                db.execute("UPDATE leases SET expires=0")

        def replaced(server, record, now):
            expired(server, record, now)
            self.assertTrue(server.authority.acquire("r", "g:0", "other-controller"))

        def wrong_generation(server, record, now):
            import sqlite3

            with sqlite3.connect(server.authority.path) as db:
                db.execute("UPDATE leases SET generation='other-generation'")

        for invalidation in (expired, replaced, wrong_generation):
            with self.subTest(invalidation=invalidation.__name__):
                received, _ = await self.exercise(invalidation)
                self.assertEqual(received, b"")

    async def test_lost_memory_control_lease_never_forwards_input(self):
        def replaced(server, record, now):
            server._controls["r"] = "other-controller"

        received, _ = await self.exercise(replaced, shared=False)
        self.assertEqual(received, b"")

    async def test_unrelated_same_uid_unix_listener_rejected_before_accept(self):
        import subprocess
        import sys

        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"]
        )
        try:
            recorded = identity(process.pid)
            received, sent = await self.exercise(
                lambda *args: None, processes={"worker": recorded, "vnc": recorded}
            )
            self.assertNotIn("websocket.accept", [message["type"] for message in sent])
            self.assertEqual(received, b"")
        finally:
            process.terminate()
            process.wait(timeout=5)

    async def test_owned_unix_creator_forwards_input(self):
        for role in ("worker", "vnc"):
            with self.subTest(role=role):
                received, sent = await self.exercise(
                    lambda *args: None, processes={role: identity(os.getpid())}
                )
                self.assertIn("websocket.accept", [message["type"] for message in sent])
                self.assertTrue(received.endswith(struct.pack(">BBHH", 5, 1, 12, 14)))

    async def test_reused_peer_pid_is_not_the_recorded_creator(self):
        own = identity(os.getpid())
        assert own is not None
        recorded = dict(own)
        recorded["start_time"] -= 1
        received, sent = await self.exercise(
            lambda *args: None, processes={"worker": recorded}
        )
        self.assertNotIn("websocket.accept", [message["type"] for message in sent])
        self.assertEqual(received, b"")


if __name__ == "__main__":
    unittest.main()
