import struct
from pathlib import Path

import pytest
from realms.manager import Manager

pytestmark = pytest.mark.e2e


def test_resize_changes_live_output_without_restarting(tmp_path):
    m = Manager(tmp_path)
    r = m.start("resize")
    try:
        assert hasattr(m, "resize"), "live resize is missing"
        changed = m.resize(r["id"], "800x600")
        assert changed["size"] == "800x600"
        assert changed["processes"] == r["processes"]
        shot = Path(m.shot(r["id"], tmp_path / "resized.png")).read_bytes()
        assert struct.unpack(">II", shot[16:24]) == (800, 600)
        assert Manager(tmp_path).list()[0]["size"] == "800x600"
        with pytest.raises(ValueError):
            m.resize(r["id"], "0x0")
    finally:
        m.stop(r["id"])
