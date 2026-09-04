
from realms.manager import Manager


def test_doctor_reports_real_requirements_and_live_sleep_only_guard(tmp_path):
    m = Manager(tmp_path)
    assert hasattr(m, "doctor"), "doctor is missing"
    report = m.doctor()
    assert report["ok"] is True
    assert report["systemd_user"] is True
    assert report["tools"]["labwc"]
    r = m.start("doctor")
    try:
        live = m.doctor(r["id"])
        assert live["ok"] is True
        assert live["scope_owned"] is True
        assert live["sleep_only_inhibitor"] is True
        assert live["outputs"][0]["current_mode"]["width"] == 1920
        assert "AMD" in live["renderer_detail"]
        assert live["realm"]["generation"] == r["generation"]
    finally:
        m.stop(r["id"])
