"""Guest lifetime contract: real owned children/pipes, no Cua, SSH or live VM.

The module must load without site-packages. A child-start witness here is NOT
socket readiness or authorization.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import runpy
import signal
import subprocess
import sys
import threading
import time

import pytest
from realms_test_paths import HERMES_ROOT, PLUGIN_ROOT

ROOT = HERMES_ROOT
MODULE = PLUGIN_ROOT / "realms/vm_driver_lifetime.py"
pytestmark = pytest.mark.linux_only

# The witness is published only after the signal handler, stdin and fd checks.
# Its stdout deliberately looks like valid control: it must NOT renew the lease.
CHILD = r"""
import json, os, pathlib, signal, sys, time
root = pathlib.Path(sys.argv[1])
stubborn = sys.argv[2] == 'yes'
def term(signum, frame):
    (root / 'term-seen').touch()
    if not stubborn:
        raise SystemExit(0)
signal.signal(signal.SIGTERM, term)
pipe_identity = tuple(json.loads(sys.argv[3]))
leaked_control = []
for entry in pathlib.Path('/proc/self/fd').iterdir():
    try:
        info = os.fstat(int(entry.name))
    except OSError:
        continue
    if (info.st_dev, info.st_ino) == pipe_identity:
        leaked_control.append(int(entry.name))
payload = {'pid': os.getpid(), 'env': dict(os.environ),
           'stdin_eof': os.read(0, 1) == b'', 'leaked_control': leaked_control}
(root / 'starting.json').write_text(json.dumps(payload), encoding='utf-8')
(root / 'starting.json').replace(root / 'started.json')
# Emergency fixture bound, not the supervisor's retirement mechanism.
deadline = time.monotonic() + 90
while time.monotonic() < deadline:
    os.write(1, b'HEARTBEAT\n')
    os.write(2, b'HEARTBEAT\n')
    time.sleep(.1)
"""
STOP = "import pathlib,sys; pathlib.Path(sys.argv[1]).touch()"


def _wait_for_start(path, finished, errors, timeout=10):
    deadline = time.monotonic() + timeout
    while not path.exists():
        assert not finished.is_set(), f"supervisor exited before child witness: {errors}"
        assert time.monotonic() < deadline, "owned child did not publish its startup witness"
        finished.wait(.02)
    return json.loads(path.read_text(encoding="utf-8"))


@contextmanager
def _running(tmp_path, monkeypatch, *, stubborn=False):
    supervisor_type = runpy.run_path(str(MODULE))["GuestDriverSupervisor"]
    monkeypatch.setenv("REALMS_LIFETIME_HOST_ONLY", "must-not-cross")
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    env = {
        "HOME": str(home), "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_CACHE_HOME": str(home / "cache"), "XDG_RUNTIME_DIR": str(tmp_path),
        "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
        "WAYLAND_DISPLAY": "fixture-not-a-real-display",
        "CUA_DRIVER_RS_ENABLE_WAYLAND": "1",
        "CUA_DRIVER_RS_TELEMETRY_ENABLED": "0",
    }
    read_fd, write_fd = os.pipe()
    info = os.fstat(read_fd)
    # Even explicitly inheritable control descriptors must not reach the driver.
    os.set_inheritable(read_fd, True)
    os.set_inheritable(write_fd, True)
    supervisor = None
    thread = None
    finished = threading.Event()
    results, errors = [], []
    try:
        supervisor = supervisor_type(
            [sys.executable, "-I", "-S", "-c", CHILD, str(tmp_path),
             "yes" if stubborn else "no", json.dumps([info.st_dev, info.st_ino])],
            env=env, cwd=str(ROOT),
            stop_argv=[sys.executable, "-I", "-S", "-c", STOP,
                       str(tmp_path / "stop-called")],
            heartbeat_timeout=3.0, stop_timeout=2.0, exit_timeout=2.0,
            terminate_timeout=2.0, kill_timeout=2.0,
        )

        def run():
            try:
                results.append(supervisor.run(control_fd=read_fd))
            except BaseException as error:
                errors.append(error)
            finally:
                finished.set()

        thread = threading.Thread(target=run, daemon=True, name="guest-lifetime-fixture")
        thread.start()
        witness = _wait_for_start(tmp_path / "started.json", finished, errors)
        assert supervisor.process.pid == witness["pid"]
        assert witness["stdin_eof"], "driver stdin must not be the SSH control pipe"
        assert witness["leaked_control"] == [], "driver retained an owner-pipe descriptor"
        assert "REALMS_LIFETIME_HOST_ONLY" not in witness["env"]
        assert all(witness["env"].get(key) == value for key, value in env.items())
        yield supervisor, write_fd, finished, results, errors
    finally:
        # EOF cases use dup2 below to retain the numeric descriptor until cleanup.
        os.close(write_fd)
        if thread is not None:
            thread.join(timeout=12)
        if supervisor is not None and supervisor.process is not None:
            # A broken implementation must not leave even a harmless fixture behind.
            # Only this supervisor's exact Popen is signalled, never a discovered PID.
            process = supervisor.process
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        if thread is not None:
            thread.join(timeout=5)
        os.close(read_fd)
        assert thread is None or not thread.is_alive(), "supervisor failed bounded teardown"


def _assert_reaped(supervisor, finished, results, errors, reason):
    assert finished.wait(12), f"guest driver not retired on {reason}"
    assert not errors, errors
    process = supervisor.process
    # Check BEFORE poll()/wait(): those would reap a zombie and mask the defect.
    assert process.returncode is not None, "supervisor returned without waiting for its child"
    with pytest.raises(ChildProcessError):
        os.waitpid(process.pid, os.WNOHANG)
    assert results == [{"reason": reason, "driver_pid": process.pid,
                        "driver_returncode": process.returncode}]


def test_only_owner_heartbeats_extend_the_owned_guest_driver(tmp_path, monkeypatch):
    """Valid control spans lease periods without leaking env or driver stdio."""
    # Copying just this module to a guest must work without site-packages/Hermes.
    probe = subprocess.run(
        [sys.executable, "-I", "-S", "-c",
         "import runpy,sys; print(runpy.run_path(sys.argv[1])['GuestDriverSupervisor'].__name__)",
         str(MODULE)], cwd=str(ROOT), env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5,
    )
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == "GuestDriverSupervisor"
    with _running(tmp_path, monkeypatch) as (supervisor, owner, finished, results, errors):
        deadline = time.monotonic() + 7
        while time.monotonic() < deadline:
            os.write(owner, b"HEARTBEAT\n")
            assert not finished.is_set(), (results, errors)
            assert supervisor.process.poll() is None
            finished.wait(.2)
        # A real child running beyond two lease periods, not just Popen success.
        assert not finished.is_set(), (results, errors)
        os.write(owner, b"STOP\n")
        _assert_reaped(supervisor, finished, results, errors, "stop")
        assert (tmp_path / "stop-called").exists()
        assert (tmp_path / "term-seen").exists(), "stop ACK alone did not retire this child"
        assert supervisor.process.returncode == 0


@pytest.mark.parametrize("stubborn", [False, True], ids=["term-exits", "term-ignored"])
@pytest.mark.parametrize("ending,reason", [
    ("eof", "eof"), ("silent", "heartbeat_timeout"), ("stop", "stop"),
    ("malformed", "protocol_error"), ("oversize", "protocol_error"),
    ("partial", "heartbeat_timeout"),
])
def test_owner_loss_reaps_the_actual_guest_driver(tmp_path, monkeypatch, ending, reason, stubborn):
    """EOF, silence and bad frames cannot strand a TERM-resistant guest child."""
    with _running(tmp_path, monkeypatch, stubborn=stubborn) as state:
        supervisor, owner, finished, results, errors = state
        os.write(owner, b"HEARTBEAT\n")
        if ending == "eof":
            # Close the LAST writer to the pipe, retaining the numeric fd as an
            # owned /dev/null descriptor for deterministic context cleanup.
            with open(os.devnull, "wb") as replacement:
                os.dup2(replacement.fileno(), owner)
        elif ending == "stop":
            os.write(owner, b"STOP\n")
        elif ending == "malformed":
            os.write(owner, b"HEARTBEAT extra\n")
        elif ending == "oversize":
            os.write(owner, b"X" * 4096)
        elif ending == "partial":
            os.write(owner, b"HEART")
        _assert_reaped(supervisor, finished, results, errors, reason)
        assert (tmp_path / "stop-called").exists(), "retirement skipped bounded Cua stop"
        assert (tmp_path / "term-seen").exists(), "retirement never reached the owned driver"
        assert supervisor.process.returncode == (-signal.SIGKILL if stubborn else 0)
