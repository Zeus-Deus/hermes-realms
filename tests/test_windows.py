"""Status counts actual private compositor toplevels, never processes."""

from pathlib import Path
import json
import os
import signal
import subprocess
import time

from realms.integration import RealmIntegration


def test_status_counts_empty_private_realm(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    service = RealmIntegration(tmp_path)
    realm = service.manager.start("windows")
    try:
        assert service.status("windows")["realms"][0]["window_count"] == 0
        other = service.manager.start("other")
        fixture = Path(__file__).with_name("fixtures") / "gtk_probe.py"

        def expect(owner, count):
            value = None
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                value = service.status(owner)["realms"][0]["window_count"]
                if value == count:
                    return
                time.sleep(0.05)
            assert value == count

        jobs = []
        for label in ("One", "Two"):
            jobs.append(
                service.manager.exec(
                    realm["id"],
                    [
                        "/usr/bin/python3",
                        str(fixture),
                        str(Path(realm["runtime_dir"]) / (label + ".json")),
                        label,
                    ],
                )
            )
            expect("windows", len(jobs))
            expect("other", 0)
        third = service.manager.exec(
            other["id"],
            [
                "/usr/bin/python3",
                str(fixture),
                str(Path(other["runtime_dir"]) / "Other.json"),
                "Other",
            ],
        )
        expect("other", 1)
        expect("windows", 2)
        # Send TERM through the owned realm, never desktop input or a host PID.
        for index, job in enumerate(jobs):
            service.manager.exec(
                realm["id"], ["/usr/bin/kill", "-TERM", str(job["pid"])], wait=True
            )
            expect("windows", 1 - index)
            expect("other", 1)
        service.manager.exec(
            other["id"], ["/usr/bin/kill", "-TERM", str(third["pid"])], wait=True
        )
        expect("other", 0)
        service.manager.stop(other["id"])
    finally:
        for record in service.manager.list():
            service.manager.stop(record["id"])
        service.unload()


def test_counter_caches_real_queries_with_short_ttl_and_bounded_entries(
    tmp_path, monkeypatch
):
    from realms import windows

    assert hasattr(windows, "WindowCounter"), "Status needs a bounded snapshot cache"
    counter = windows.WindowCounter(ttl=0.2, max_entries=1)
    service = RealmIntegration(tmp_path)
    calls = []
    original = windows._snapshot

    def observe(*args):
        calls.append(None)
        return original(*args)

    monkeypatch.setattr(windows, "_snapshot", observe)
    first = service.manager.start("first")
    second = service.manager.start("second")
    try:
        for _ in range(4):
            assert counter.count(first) == 0
        assert len(calls) == 1
        time.sleep(0.21)
        assert counter.count(first) == 0
        assert len(calls) == 2
        assert counter.count(second) == 0
        assert counter.count(first) == 0  # LRU capacity one evicted first
        assert len(calls) == 4
        assert (
            service.manager.registry.get(first["id"])["last_activity"]
            == first["last_activity"]
        )
    finally:
        for record in service.manager.list():
            service.manager.stop(record["id"])
        service.unload()


def test_scope_validation_timeout_is_unknown_not_status_failure(monkeypatch):
    from realms import windows

    def unavailable(record):
        raise subprocess.TimeoutExpired("systemctl", 5)

    # Fault injection at the external systemd boundary; no fabricated count.
    monkeypatch.setattr(windows, "validate_live", unavailable)
    assert windows.count_windows({}) is None


def test_unavailable_compositor_is_unknown_and_recovers_without_host_fallback(
    tmp_path, monkeypatch
):
    from realms.windows import count_windows, WindowCounter

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    service = RealmIntegration(tmp_path)
    realm = service.manager.start("unavailable")
    compositor = realm["processes"]["compositor"]["pid"]
    counter = WindowCounter(ttl=0.2)
    try:
        os.kill(compositor, signal.SIGSTOP)
        started = time.monotonic()
        assert counter.count(realm) is None
        assert time.monotonic() - started < 3
        os.kill(compositor, signal.SIGCONT)
        assert counter.count(realm) is None  # cache failures, do not hammer
        time.sleep(0.21)
        assert counter.count(realm) == 0
        ready_path = Path(realm["runtime_dir"]) / "ready.json"
        original = ready_path.read_text()
        ready = json.loads(original)
        ready["env"]["WAYLAND_DISPLAY"] = "/not/a/private/wayland/socket"
        try:
            ready_path.write_text(json.dumps(ready))
            assert count_windows(realm) is None
        finally:
            ready_path.write_text(original)
        service.manager.stop(realm["id"])
        assert count_windows(realm) is None  # dead is not empty
    finally:
        if service.manager.list():
            os.kill(compositor, signal.SIGCONT)
            service.manager.stop(realm["id"])
        service.unload()
