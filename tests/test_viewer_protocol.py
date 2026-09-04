"""Security contracts for the real viewer's client-to-VNC boundary."""

import importlib
import struct
import unittest


class ViewerProtocolTests(unittest.TestCase):
    def protocol(self, control=False):
        try:
            module = importlib.import_module("realms.rfb")
        except ModuleNotFoundError:
            self.fail("Realms needs a server-enforced, incremental RFB client filter")
        return module.ClientFilter(control=control)

    def test_viewer_drops_input_even_across_fragmented_frames(self):
        stream = b"RFB 003.008\n" + b"\x01\x01"
        setup = struct.pack(">BBH", 2, 0, 1) + struct.pack(">i", 0)
        request = struct.pack(">BBHHHH", 3, 0, 0, 0, 640, 480)
        key = struct.pack(">BBHI", 4, 1, 0, ord("x"))
        pointer = struct.pack(">BBHH", 5, 1, 120, 140)
        clip = struct.pack(">B3xI", 6, 6) + b"secret"
        raw = stream + setup + request + key + pointer + clip + request
        f = self.protocol()
        forwarded = b"".join(f.feed(bytes([byte])) for byte in raw)
        self.assertEqual(forwarded, stream + setup + request + request)

    def test_extended_clipboard_is_bounded_and_filtered(self):
        handshake = b"RFB 003.008\n\x01\x01"
        caps = struct.pack(">B3xi", 6, -8) + b"\x1f\x00\x00\x01" + b"\x00" * 4
        request = struct.pack(">BBHHHH", 3, 0, 0, 0, 640, 480)
        for control in (False, True):
            f = self.protocol(control=control)
            raw = handshake + caps + request
            expected = handshake + (caps if control else b"") + request
            self.assertEqual(b"".join(f.feed(bytes([b])) for b in raw), expected)

    def test_takeover_passes_input_but_invalid_frames_fail_closed(self):
        stream = b"RFB 003.008\n" + b"\x01\x01" + struct.pack(">BBHH", 5, 1, 120, 140)
        f = self.protocol(control=True)
        self.assertEqual(f.feed(stream), stream)
        with self.assertRaises(ValueError):
            f.feed(b"\x70")
        f = self.protocol()
        f.feed(b"RFB 003.008\n\x01\x01")
        with self.assertRaises(ValueError):
            f.feed(struct.pack(">B3xI", 6, 2**31))


if __name__ == "__main__":
    unittest.main()
