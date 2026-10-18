"""The terminal Realm viewer (footer ``v``): frames over the real viewer bridge, rendering.

No compositor or VM is started: a private 0600 Unix RFB source stands in for WayVNC
behind the real ticket-checked, view-only ViewerServer.
"""
import base64
import os
from pathlib import Path
import re
import runpy
import socket
import struct
import tempfile
import threading
import time
import zlib

import pytest
from realms_test_paths import PLUGIN_ROOT

load = runpy.run_path(str(PLUGIN_ROOT / "realms/_binding.py"))["load_runtime"]
pytestmark = pytest.mark.platforms("linux")

W, H = 4, 2
# BGRX pixels, row-major: red, green, blue, white / black, grey, yellow, cyan.
PIXELS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255),
          (0, 0, 0), (128, 128, 128), (255, 255, 0), (0, 255, 255)]


def _bgrx(pixels):
    return b"".join(bytes((b, g, r, 0)) for r, g, b in pixels)


def rfb_source():
    """A minimal RFB 3.8 server (None security, Raw rects) on a private 0600 socket."""
    runtime = tempfile.mkdtemp(prefix="realm-live-")
    os.chmod(runtime, 0o700)
    endpoint = Path(runtime) / "vnc"
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(endpoint))
    endpoint.chmod(0o600)
    listener.listen()
    listener.settimeout(0.1)
    seen, stopping = [], threading.Event()

    def recv_exact(peer, n):
        data = b""
        while len(data) < n and not stopping.is_set():
            try:
                chunk = peer.recv(n - len(data))
            except socket.timeout:
                continue
            if not chunk:
                raise ConnectionError
            data += chunk
        return data

    def serve(peer):
        with peer:
            peer.settimeout(0.1)
            try:
                peer.sendall(b"RFB 003.008\n")
                seen.append(recv_exact(peer, 12))
                peer.sendall(b"\x01\x01")  # one security type: None
                seen.append(recv_exact(peer, 1))
                peer.sendall(b"\x00\x00\x00\x00")  # SecurityResult OK
                seen.append(recv_exact(peer, 1))  # ClientInit
                fmt = bytes([32, 24, 0, 1, 0, 255, 0, 255, 0, 255, 16, 8, 0, 0, 0, 0])
                name = b"fixture"
                peer.sendall(struct.pack(">HH", W, H) + fmt + struct.pack(">I", len(name)) + name)
                while not stopping.is_set():
                    kind = recv_exact(peer, 1)
                    if not kind:
                        return
                    length = {0: 19, 3: 9, 4: 7, 5: 5}.get(kind[0])
                    if kind[0] == 2:
                        head = recv_exact(peer, 3)
                        count = struct.unpack(">H", head[1:3])[0]
                        seen.append(kind + head + recv_exact(peer, 4 * count))
                        continue
                    if length is None:
                        seen.append(("unexpected", kind))
                        return
                    body = recv_exact(peer, length)
                    seen.append(kind + body)
                    if kind[0] == 3 and body[0] == 0:  # first, non-incremental request
                        peer.sendall(b"\x00\x00" + struct.pack(">H", 1)
                                     + struct.pack(">HHHHi", 0, 0, W, H, 0) + _bgrx(PIXELS))
            except (ConnectionError, OSError):
                return

    threads = []

    def accept():
        while not stopping.is_set():
            try:
                peer, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            thread = threading.Thread(target=serve, args=(peer,), daemon=True)
            threads.append(thread)
            thread.start()

    worker = threading.Thread(target=accept, daemon=True)
    worker.start()

    def close():
        stopping.set()
        listener.close()
        worker.join(5)
        for thread in threads:
            thread.join(5)

    return runtime, endpoint, seen, close


@pytest.fixture
def bridge(tmp_path):
    runtime, endpoint, seen, close = rfb_source()
    record = {"id": "r-" + "a" * 24, "generation": "g", "runtime_dir": runtime,
              "vnc_socket": str(endpoint),
              "processes": {"worker": load("lifecycle").identity(os.getpid())}}
    server = load("bridge").ViewerServer(lambda rid: record if rid == record["id"] else None,
                                         state_dir=tmp_path / "authority")
    server.start()
    try:
        yield server, record, seen
    finally:
        server.stop()
        close()


def test_client_reads_the_screen_through_the_view_only_bridge(bridge):
    server, record, seen = bridge
    live = load("live_view")
    token = server.issue(record["id"], can_control=False, ttl=60)
    client = live.RfbClient.connect(server.origin, record["id"], token)
    try:
        assert (client.width, client.height) == (W, H)
        deadline = time.monotonic() + 5
        while not client.poll(timeout=0.2):
            assert time.monotonic() < deadline, "no frame arrived"
        assert client.rgb() == bytes(c for pixel in PIXELS for c in pixel)
    finally:
        client.close()
    sent = [m for m in seen if isinstance(m, bytes)]
    assert sent[0] == b"RFB 003.008\n" and sent[1] == b"\x01" and sent[2] == b"\x01"
    # The client asks for frames only: no key (4), pointer (5) or clipboard (6) messages.
    assert {m[0] for m in sent[3:]} <= {0, 2, 3}


def test_client_is_refused_without_a_valid_ticket(bridge):
    server, record, _ = bridge
    live = load("live_view")
    with pytest.raises(live.ViewerError):
        live.RfbClient.connect(server.origin, record["id"], "not-a-ticket")


def test_client_connects_with_websockets_14_which_has_no_proxy_option(bridge, monkeypatch):
    """The manifest allows websockets 14, whose ``connect`` has no ``proxy`` keyword."""
    import inspect
    import websockets.sync.client as ws_client

    real = ws_client.connect

    def connect_14(uri, *, origin=None, compression="deflate", max_size=2**20, open_timeout=10,
                   subprotocols=None):
        return real(uri, origin=origin, compression=compression, max_size=max_size,
                    open_timeout=open_timeout, subprotocols=subprotocols)

    assert "proxy" not in inspect.signature(connect_14).parameters
    monkeypatch.setattr(ws_client, "connect", connect_14)
    server, record, _ = bridge
    live = load("live_view")
    client = live.RfbClient.connect(server.origin, record["id"],
                                    server.issue(record["id"], can_control=False, ttl=60))
    try:
        deadline = time.monotonic() + 5
        while not client.poll(timeout=0.2):
            assert time.monotonic() < deadline, "no frame arrived"
        assert client.rgb() == bytes(c for pixel in PIXELS for c in pixel)
    finally:
        client.close()


def test_copyrect_moves_overlapping_rows_like_a_scroll():
    """CopyRect within the screen: overlapping source and destination copy the source as it was."""
    live = load("live_view")
    client = live.RfbClient(ws=None)
    client.width, client.height = 3, 4
    client.framebuffer = bytearray(b"".join(bytes((r, c, 0, 0)) for r in range(4) for c in range(3)))
    client._copy(0, 0, 0, 1, 3, 3)  # scroll down one row
    rows = [bytes(client.framebuffer[r * 12:(r + 1) * 12]) for r in range(4)]
    expected = [b"".join(bytes((r, c, 0, 0)) for c in range(3)) for r in (0, 0, 1, 2)]
    assert rows == expected
    client._copy(1, 1, 0, 0, 2, 2)  # a sub-rectangle, up and left
    assert bytes(client.framebuffer[0:8]) == expected[1][4:12]
    assert bytes(client.framebuffer[12:20]) == expected[2][4:12]
    with pytest.raises(live.ViewerError):
        client._copy(2, 0, 0, 0, 2, 1)  # source runs off the right edge


def test_kitty_frame_carries_the_exact_pixels_and_fits_the_cells():
    live = load("live_view")
    rgb = bytes(c for pixel in PIXELS for c in pixel)
    out = live.kitty_frame(rgb, W, H, columns=40, rows=10, image_id=7)
    chunks = re.findall(rb"\x1b_G([^;]*);([^\x1b]*)\x1b\\", out)
    assert chunks, out[:80]
    control = dict(item.split(b"=") for item in chunks[0][0].split(b","))
    assert control[b"a"] == b"T" and control[b"f"] == b"24" and control[b"o"] == b"z"
    assert (control[b"s"], control[b"v"]) == (str(W).encode(), str(H).encode())
    assert control[b"i"] == b"7" and control[b"q"] == b"2"
    assert int(control[b"c"]) <= 40 and int(control[b"r"]) <= 10
    payload = base64.b64decode(b"".join(data for _, data in chunks))
    assert zlib.decompress(payload) == rgb


def test_halfblock_frame_paints_two_pixel_rows_per_cell():
    live = load("live_view")
    rgb = bytes(c for pixel in PIXELS for c in pixel)
    text = live.halfblock_frame(rgb, W, H, columns=W, rows=1)
    # Top half = first row's colour, bottom half = the row below it.
    assert "\x1b[38;2;255;0;0m\x1b[48;2;0;0;0m▀" in text
    assert "\x1b[38;2;0;255;255m" not in text.split("▀")[0]
    assert text.count("▀") == W


def test_tmux_uses_half_blocks_even_inside_a_kitty_graphics_terminal():
    """Default tmux drops kitty graphics commands, so pixels mode would paint a blank view."""
    graphics_mode = load("live_view").graphics_mode
    kitty = {"TERM": "xterm-kitty", "KITTY_WINDOW_ID": "1"}
    assert graphics_mode(kitty) == "pixels"
    assert graphics_mode({**kitty, "TERM": "tmux-256color", "TMUX": "/tmp/tmux-1000/default,1,0"}) == "blocks"
    assert graphics_mode({"TERM": "screen-256color", "TERM_PROGRAM": "ghostty"}) == "blocks"


def test_fit_keeps_the_screen_aspect_inside_the_terminal():
    live = load("live_view")
    cols, rows = live.fit(1920, 1080, columns=200, rows=50, cell_ratio=2.0)
    assert cols <= 200 and rows <= 50
    assert abs((cols / (rows * 2.0)) - 1920 / 1080) < 0.1
