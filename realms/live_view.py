"""Watch a running realm inside the terminal (``/realm view`` on the classic CLI).

Frames come through the profile's own viewer bridge with a view-only ticket, exactly like
the desktop Watch tab: the bridge proves the VNC peer belongs to the realm and drops every
input message server-side. This client also never sends input; it only asks for frames.

Kitty-graphics terminals (kitty, Ghostty, WezTerm) get real pixels, downscaled smoothly to
the terminal's pixel size. Everything else gets truecolour half-blocks.
"""

import base64
import inspect
import os
import select
import struct
import sys
import time
import zlib


class ViewerError(Exception):
    """The viewer could not connect or lost the realm."""


# 32bpp little-endian BGRX, true colour.
_PIXEL_FORMAT = bytes([32, 24, 0, 1, 0, 255, 0, 255, 0, 255, 16, 8, 0, 0, 0, 0])
_ENCODINGS = (0, 1, -223)  # Raw, CopyRect, DesktopSize
_KITTY_IDS = (0x52454131, 0x52454132)  # two ids we alternate between, far from the CLI pet's


class RfbClient:
    """A view-only RFB 3.8 client speaking over the realm viewer bridge's WebSocket."""

    def __init__(self, ws):
        self._ws = ws
        self._buffer = bytearray()
        self.width = self.height = 0
        self.framebuffer = bytearray()

    @classmethod
    def connect(cls, origin, realm_id, token, *, timeout=10):
        from websockets.exceptions import InvalidHandshake, InvalidStatus
        from websockets.sync.client import connect
        from websockets.typing import Subprotocol

        url = origin.replace("http:", "ws:", 1) + f"/api/realms/{realm_id}/vnc"
        # websockets 15 honours proxy environment variables unless told not to; 14 has no proxy
        # support (and no such keyword), so it already connects straight to the loopback viewer.
        direct = {"proxy": None} if "proxy" in inspect.signature(connect).parameters else {}
        try:
            ws = connect(url, origin=origin, compression=None, max_size=None, **direct,
                         open_timeout=timeout,
                         subprotocols=[Subprotocol("binary"), Subprotocol("realm." + token)])
        except (InvalidHandshake, InvalidStatus, OSError) as exc:
            raise ViewerError(f"viewer refused the connection: {exc}") from None
        client = cls(ws)
        try:
            client._handshake(timeout)
        except BaseException:
            client.close()
            raise
        return client

    def _read(self, size, timeout):
        from websockets.exceptions import ConnectionClosed

        deadline = time.monotonic() + timeout
        while len(self._buffer) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            try:
                data = self._ws.recv(timeout=remaining)
            except TimeoutError:
                raise
            except ConnectionClosed:
                raise ViewerError("the realm's screen closed") from None
            if isinstance(data, str):
                raise ViewerError("viewer sent text where binary RFB was expected")
            self._buffer.extend(data)
        chunk = bytes(self._buffer[:size])
        del self._buffer[:size]
        return chunk

    def _handshake(self, timeout):
        try:
            if self._read(12, timeout) != b"RFB 003.008\n":
                raise ViewerError("unsupported RFB server version")
            self._ws.send(b"RFB 003.008\n")
            types = self._read(self._read(1, timeout)[0], timeout)
            if 1 not in types:
                raise ViewerError("realm screen requires unsupported authentication")
            self._ws.send(b"\x01")
            if self._read(4, timeout) != b"\x00\x00\x00\x00":
                raise ViewerError("realm screen rejected the session")
            self._ws.send(b"\x01")  # shared: never disconnect other viewers
            self.width, self.height = struct.unpack(">HH", self._read(4, timeout))
            self._read(16, timeout)
            self._read(struct.unpack(">I", self._read(4, timeout))[0], timeout)
        except TimeoutError:
            raise ViewerError("realm screen did not answer") from None
        self.framebuffer = bytearray(self.width * self.height * 4)
        self._ws.send(b"\x00\x00\x00\x00" + _PIXEL_FORMAT)
        self._ws.send(struct.pack(">BxH", 2, len(_ENCODINGS))
                      + b"".join(struct.pack(">i", e) for e in _ENCODINGS))
        self._request(incremental=False)

    def _request(self, *, incremental):
        self._ws.send(struct.pack(">BBHHHH", 3, int(incremental), 0, 0, self.width, self.height))

    def poll(self, timeout=0.0):
        """Process server messages for up to ``timeout`` seconds; True if the screen changed."""
        changed = False
        deadline = time.monotonic() + timeout
        while True:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                kind = self._read(1, remaining if not self._buffer else 5)[0]
            except TimeoutError:
                return changed
            try:
                if kind == 0:
                    self._update()
                    changed = True
                    self._request(incremental=True)
                elif kind == 1:
                    _, _, count = struct.unpack(">BHH", self._read(5, 5))
                    self._read(6 * count, 5)
                elif kind == 2:
                    pass
                elif kind == 3:
                    self._read(3, 5)
                    self._read(struct.unpack(">I", self._read(4, 5))[0], 5)
                else:
                    raise ViewerError(f"unsupported RFB server message {kind}")
            except TimeoutError:
                raise ViewerError("realm screen stopped mid-message") from None
            if changed and not self._buffer:
                return True

    def _update(self):
        _, count = struct.unpack(">BH", self._read(3, 5))
        for _ in range(count):
            x, y, w, h, encoding = struct.unpack(">HHHHi", self._read(12, 5))
            if encoding == 0:
                self._raw(x, y, w, h, self._read(w * h * 4, 30))
            elif encoding == 1:
                sx, sy = struct.unpack(">HH", self._read(4, 5))
                self._copy(sx, sy, x, y, w, h)
            elif encoding == -223:
                self.width, self.height = w, h
                self.framebuffer = bytearray(w * h * 4)
            else:
                raise ViewerError(f"unsupported RFB encoding {encoding}")

    def _raw(self, x, y, w, h, data):
        if x + w > self.width or y + h > self.height:
            raise ViewerError("RFB rectangle outside the screen")
        stride, span = self.width * 4, w * 4
        for row in range(h):
            start = (y + row) * stride + x * 4
            self.framebuffer[start:start + span] = data[row * span:(row + 1) * span]

    def _copy(self, sx, sy, x, y, w, h):
        if max(sx, x) + w > self.width or max(sy, y) + h > self.height:
            raise ViewerError("RFB copy outside the screen")
        stride, span = self.width * 4, w * 4
        # Snapshot the source rows first so overlapping copies (scrolling) read the old pixels.
        rows = []
        for r in range(h):
            start = (sy + r) * stride + sx * 4
            rows.append(bytes(self.framebuffer[start:start + span]))
        for r, data in enumerate(rows):
            start = (y + r) * stride + x * 4
            self.framebuffer[start:start + span] = data

    def rgb(self):
        out = bytearray(self.width * self.height * 3)
        out[0::3], out[1::3], out[2::3] = (self.framebuffer[2::4], self.framebuffer[1::4],
                                          self.framebuffer[0::4])
        return bytes(out)

    def close(self):
        try:
            self._ws.close(code=1000)
        except Exception:
            pass


def fit(width, height, *, columns, rows, cell_ratio):
    """Largest cell box with the screen's aspect ratio. ``cell_ratio`` = cell height / width."""
    cols = max(1, min(columns, int(rows * cell_ratio * width / height)))
    return cols, max(1, min(rows, round(cols * height / (width * cell_ratio))))


def scale(rgb, width, height, target_w, target_h):
    """Smooth downscale (Pillow, area average) or plain sampling when Pillow is missing."""
    target_w, target_h = min(target_w, width), min(target_h, height)
    if (target_w, target_h) == (width, height):
        return rgb, width, height
    try:
        from PIL import Image
    except ImportError:
        out = bytearray()
        for ty in range(target_h):
            row = (ty * height // target_h) * width
            for tx in range(target_w):
                start = (row + tx * width // target_w) * 3
                out += rgb[start:start + 3]
        return bytes(out), target_w, target_h
    image = Image.frombytes("RGB", (width, height), rgb)
    return image.resize((target_w, target_h), Image.Resampling.BOX).tobytes(), target_w, target_h


def kitty_frame(rgb, width, height, *, columns, rows, image_id):
    """Kitty graphics escape: zlib RGB in chunks, placed over ``columns`` x ``rows`` cells."""
    payload = base64.b64encode(zlib.compress(rgb, 1))
    chunks = [payload[i:i + 4096] for i in range(0, len(payload), 4096)] or [b""]
    out = []
    for index, chunk in enumerate(chunks):
        more = int(index < len(chunks) - 1)
        if index == 0:
            control = (f"a=T,f=24,o=z,s={width},v={height},i={image_id},c={columns},r={rows},"
                       f"C=1,q=2,m={more}")
        else:
            control = f"m={more}"
        out.append(b"\x1b_G" + control.encode() + b";" + chunk + b"\x1b\\")
    return b"".join(out)


def halfblock_frame(rgb, width, height, *, columns, rows):
    """Truecolour ``▀`` cells: foreground = upper pixel row, background = the one below."""
    pixels, w, h = scale(rgb, width, height, columns, rows * 2)
    lines = []
    for cell_row in range(min(rows, (h + 1) // 2)):
        parts, last = [], None
        top, bottom = cell_row * 2 * w * 3, min(cell_row * 2 + 1, h - 1) * w * 3
        for x in range(w):
            fg = tuple(pixels[top + x * 3:top + x * 3 + 3])
            bg = tuple(pixels[bottom + x * 3:bottom + x * 3 + 3])
            if (fg, bg) != last:
                parts.append("\x1b[38;2;%d;%d;%dm\x1b[48;2;%d;%d;%dm" % (fg + bg))
                last = (fg, bg)
            parts.append("▀")
        lines.append("".join(parts) + "\x1b[0m")
    return "\n".join(lines)


def graphics_mode(environ=None):
    """``pixels`` for kitty-graphics terminals, else ``blocks`` (env only, never a query)."""
    env = os.environ if environ is None else environ
    term, program = env.get("TERM", "").lower(), env.get("TERM_PROGRAM", "").lower()
    if program == "vscode":
        return "blocks"
    if (env.get("KITTY_WINDOW_ID") or "kitty" in term or "ghostty" in term
            or program in ("ghostty", "wezterm") or env.get("WEZTERM_EXECUTABLE")):
        return "pixels"
    return "blocks"


def hyperlink(url, text):
    return f"\x1b]8;;{url}\x1b\\{text}\x1b]8;;\x1b\\"


def _write(fd, data):
    view = memoryview(data.encode() if isinstance(data, str) else data)
    while view:
        try:
            view = view[os.write(fd, view):]
        except BlockingIOError:
            select.select([], [fd], [], 1)


def _terminal_size(fd):
    import fcntl
    import termios

    rows, cols, xpix, ypix = struct.unpack(
        "HHHH", fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8))
    return cols, rows, xpix, ypix


def run(client, *, title, browser_url=None, mode=None, fps=10, stdin=None, stdout=None,
        keep_alive=None, environ=None):
    """Show ``client``'s screen full-window until q/Esc/Ctrl+C or the realm goes away.

    Runs on the real terminal (the caller suspended any prompt UI); returns a short reason.
    """
    import termios
    import tty

    stdin = stdin or sys.__stdin__
    stdout = stdout or sys.__stdout__
    fd_in, fd_out = stdin.fileno(), stdout.fileno()
    mode = mode or graphics_mode(environ)
    saved = termios.tcgetattr(fd_in)
    shown_ids, frame_index, size, show_url = [], 0, None, False
    reason = "closed"
    try:
        tty.setraw(fd_in)  # Ctrl+C arrives as a key, not a signal for the whole CLI
        _write(fd_out, "\x1b[?1049h\x1b[?25l\x1b[2J")
        dirty, last_frame, last_tick = True, 0.0, time.monotonic()
        while True:
            ready, _, _ = select.select([fd_in], [], [], 0)
            if ready:
                key = os.read(fd_in, 64)
                # A lone Esc closes; arrow keys etc. (Esc + sequence) are ignored.
                if key == b"\x1b" or key[:1] in (b"q", b"Q", b"\x03"):
                    break
                if key[:1] in (b"u", b"U") and browser_url:
                    show_url, size, dirty = not show_url, None, True
            try:
                dirty = client.poll(timeout=0.05) or dirty
            except ViewerError as exc:
                reason = str(exc)
                break
            now = time.monotonic()
            if keep_alive and now - last_tick >= 60:
                last_tick = now
                if not keep_alive():
                    reason = "viewer authorization ended"
                    break
            current = _terminal_size(fd_out)
            if current != size:
                size, dirty = current, True
                _write(fd_out, "\x1b[0m\x1b[2J")
            if not dirty or now - last_frame < 1 / fps:
                continue
            dirty, last_frame = False, now
            cols, rows, xpix, ypix = size
            header = [_header(title, mode, browser_url, cols)]
            if show_url:
                header.append(hyperlink(browser_url, browser_url))
            avail_rows = max(1, rows - len(header))
            cell_w = xpix / cols if xpix and cols else 10
            cell_h = ypix / rows if ypix and rows else 20
            box_cols, box_rows = fit(client.width, client.height, columns=cols, rows=avail_rows,
                                     cell_ratio=cell_h / cell_w if mode == "pixels" else 2.0)
            out = ["\x1b[H" + "".join(f"\x1b[{i + 1};1H\x1b[2K{line}"
                                       for i, line in enumerate(header))]
            if mode == "pixels":
                rgb, w, h = scale(client.rgb(), client.width, client.height,
                                  int(box_cols * cell_w), int(box_rows * cell_h))
                image_id = _KITTY_IDS[frame_index % 2]
                out.append(f"\x1b[{len(header) + 1};1H")
                _write(fd_out, "".join(out))
                _write(fd_out, kitty_frame(rgb, w, h, columns=box_cols, rows=box_rows,
                                           image_id=image_id))
                for old in shown_ids:
                    _write(fd_out, f"\x1b_Ga=d,d=I,i={old},q=2\x1b\\")
                shown_ids = [image_id]
            else:
                frame = halfblock_frame(client.rgb(), client.width, client.height,
                                        columns=box_cols, rows=box_rows)
                for i, line in enumerate(frame.split("\n")):
                    out.append(f"\x1b[{len(header) + 1 + i};1H{line}")
                _write(fd_out, "".join(out))
            frame_index += 1
    finally:
        cleanup = "".join(f"\x1b_Ga=d,d=I,i={i},q=2\x1b\\" for i in _KITTY_IDS)
        try:
            _write(fd_out, cleanup + "\x1b[0m\x1b[2J\x1b[?25h\x1b[?1049l")
        finally:
            termios.tcsetattr(fd_in, termios.TCSADRAIN, saved)
    return reason


def _header(title, mode, browser_url, columns):
    hints = ["view-only", "q close"]
    if browser_url:
        hints.append("u link")
    text = f" ◈ {title} · " + " · ".join(hints)
    link = f" · {hyperlink(browser_url, 'open in browser ↗')}" if browser_url else ""
    plain = text + (" · open in browser ↗" if browser_url else "")
    if len(plain) > columns:
        return "\x1b[7m" + text[:max(0, columns)] + "\x1b[0m"
    return "\x1b[7m" + text + link + " " * (columns - len(plain)) + "\x1b[0m"
