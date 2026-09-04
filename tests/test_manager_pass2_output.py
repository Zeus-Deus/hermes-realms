"""P2-1: retrieve both captured streams through the actual private RPC."""

from pathlib import Path
import sys
import time

import pytest

from realms.manager import Manager


@pytest.mark.parametrize(
    "payload",
    [b"\x00" * 262144, (b'\x00\x01\n"\\\xff' + "é😀".encode()) * 24000],
    ids=["worst-case", "mixed-utf8"],
)
def test_escaped_streams_are_retrievable_at_capture_cap(tmp_path, payload):
    manager = Manager(tmp_path)
    record = manager.start("pass2-escaped-streams")
    # Keep argv small; generate bytes inside the actual scoped child.
    seed = payload[:12] if len(set(payload)) > 1 else b"\x00"
    count = len(payload) // len(seed)
    code = f"import os; data = {seed!r} * {count}; os.write(1, data); os.write(2, data)"
    try:
        result = manager.exec(record["id"], [sys.executable, "-c", code], wait=True)
        deadline = time.monotonic() + 5
        while not result["output_complete"] and time.monotonic() < deadline:
            result = manager._rpc(record["id"], op="job", job_id=result["job_id"])
            time.sleep(0.02)
        assert result["returncode"] == 0 and result["output_complete"]
        for stream in ("stdout", "stderr"):
            assert result[stream] == payload[:262144].decode("utf-8", errors="replace")
            assert Path(result[stream + "_path"]).read_bytes() == payload[:262144]
            assert result[stream + "_truncated"] == (len(payload) > 262144)
        retained = manager._rpc(record["id"], op="job", job_id=result["job_id"])
        assert retained == result
    finally:
        manager.stop(record["id"])
        assert not Path(record["runtime_dir"]).exists()
        assert not manager.registry.path(record["id"]).exists()
