import json

import pytest
from realms.manager import Manager

pytestmark = pytest.mark.e2e


def test_stale_durable_record_is_reconciled_before_reuse(tmp_path):
    m = Manager(tmp_path)
    r = m.start("recover")
    m.stop(r["id"])
    # Replay an actual prior record, as after a power loss removed /run but not
    # the durable profile. No live unit or unrelated process may be adopted.
    m.registry.path(r["id"]).write_text(json.dumps(r))
    assert Manager(tmp_path).list() == []
    replacement = Manager(tmp_path).start("recover")
    try:
        assert replacement["generation"] != r["generation"]
    finally:
        Manager(tmp_path).stop(replacement["id"])
