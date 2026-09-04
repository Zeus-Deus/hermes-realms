"""Real raw RFB pixels: no client cursor extension, no host display/input."""

import json
import os
from pathlib import Path
import socket
import struct
import time

from PIL import Image, ImageChops

from realms.lifecycle import alive
from realms.manager import Manager


class RawRFB:
    """Minimal raw-only RFB 3.8 client to observe server-rendered pixels."""

    def __init__(self, endpoint):
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(5)
        self.socket.connect(endpoint)
        assert self.read(12) == b"RFB 003.008\n"
        self.socket.sendall(b"RFB 003.008\n")
        types = self.read(self.read(1)[0])
        assert 1 in types, types
        self.socket.sendall(b"\x01")
        assert self.read(4) == bytes(4)
        self.socket.sendall(b"\x01")
        header = self.read(24)
        self.width, self.height = struct.unpack(">HH", header[:4])
        self.name = self.read(struct.unpack(">I", header[20:24])[0]).decode()
        # 32-bit little-endian RGB; request Raw only, NEVER Cursor pseudo-encoding.
        pixel_format = struct.pack(
            ">BBBBHHHBBB3x", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0
        )
        self.socket.sendall(b"\x00\x00\x00\x00" + pixel_format)
        self.socket.sendall(struct.pack(">BBHi", 2, 0, 1, 0))

    def read(self, length):
        chunks = bytearray()
        while len(chunks) < length:
            data = self.socket.recv(length - len(chunks))
            assert data, "RFB closed before complete frame"
            chunks.extend(data)
        return bytes(chunks)

    def move(self, x, y):
        self.socket.sendall(struct.pack(">BBHH", 5, 0, x, y))

    def frame(self):
        self.socket.sendall(struct.pack(">BBHHHH", 3, 0, 0, 0, self.width, self.height))
        assert self.read(1) == b"\x00"
        _, count = struct.unpack(">BH", self.read(3))
        image = Image.new("RGB", (self.width, self.height))
        area = 0
        for _ in range(count):
            x, y, width, height, encoding = struct.unpack(">HHHHi", self.read(12))
            assert encoding == 0, encoding
            raw = self.read(width * height * 4)
            rectangle = Image.frombytes("RGB", (width, height), raw, "raw", "BGRX")
            image.paste(rectangle, (x, y))
            area += width * height
        assert area == self.width * self.height, "Expected a full non-incremental frame"
        return image

    def close(self):
        self.socket.close()


def pixel_count(image):
    data = image.tobytes()
    return sum(bool(r or g or b) for r, g, b in zip(data[::3], data[1::3], data[2::3]))


def evidence_dir(tmp_path):
    directory = Path(
        os.environ.get("REALMS_TEST_CAPTURE_EVIDENCE", str(tmp_path / "evidence"))
    )
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory


def test_native_wayvnc_cursor_is_in_framebuffer_and_moves(tmp_path):
    (tmp_path / "config.yaml").write_text("plugins:\n  realms:\n    size: 800x600\n")
    manager = Manager(tmp_path)
    record = manager.start("native-cursor-regression")
    client = None
    evidence = evidence_dir(tmp_path)
    receipt = {
        "realm_id": record["id"],
        "transport": "private UNIX RFB raw-only",
        "client_cursor_encoding": False,
        "positions": [[160, 180], [540, 360]],
    }
    try:
        client = RawRFB(record["vnc_socket"])
        images = []
        for x, y in receipt["positions"]:
            client.move(x, y)
            # Poll actual frames for a stable state; do not infer rendering from flags.
            deadline = time.monotonic() + 5
            previous = client.frame()
            while time.monotonic() < deadline:
                time.sleep(0.08)
                current = client.frame()
                if ImageChops.difference(previous, current).getbbox() is None:
                    break
                previous = current
            else:
                raise AssertionError("Native cursor framebuffer never settled")
            images.append(current)
        difference = ImageChops.difference(*images)
        areas = [(x - 32, y - 32, x + 64, y + 64) for x, y in receipt["positions"]]
        changes = [pixel_count(difference.crop(area)) for area in areas]
        outside = difference.copy()
        for area in areas:
            outside.paste((0, 0, 0), area)
        receipt.update(
            changed_pixels=changes,
            outside_changed_pixels=pixel_count(outside),
            difference_bbox=difference.getbbox(),
        )
        for name, image in zip(("native-at-a.png", "native-at-b.png"), images):
            image.save(evidence / name)
        difference.save(evidence / "native-move-diff.png")
        assert all(count > 20 for count in changes), receipt
        assert receipt["outside_changed_pixels"] == 0, receipt
        # A repeated full frame must stay unchanged: no animation/application confound.
        assert ImageChops.difference(images[-1], client.frame()).getbbox() is None
    finally:
        if client:
            client.close()
        manager.stop(record["id"])
        assert manager.list() == []
        assert not Path(record["runtime_dir"]).exists()
        assert not (Path("/sys/fs/cgroup") / record["cgroup"].lstrip("/")).exists()
        assert not any(alive(p) for p in record["processes"].values())
        receipt["cleanup"] = "registry/runtime/cgroup/processes removed"
        (evidence / "native-cursor-receipt.json").write_text(
            json.dumps(receipt, indent=2) + "\n"
        )
        print("NATIVE_CURSOR_RECEIPT=" + json.dumps(receipt, sort_keys=True))
