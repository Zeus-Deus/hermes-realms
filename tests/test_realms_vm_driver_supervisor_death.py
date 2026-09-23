"""Supervisor death must retire its exact foreground child, not its peers.

Self-exec roles keep subreaper policy out of pytest and any threaded backend.
Only harmless finite Python children and tmp_path files are used; no VM/Cua,
process-group signals, discovered-PID kills, or host desktop resources.
"""
import ctypes
import json
import os
from pathlib import Path
import runpy
import select
import signal
import subprocess
import sys
import threading
import time
# Also re-run as an isolated child (-I), where the tests directory is not importable.
_paths = __import__("runpy").run_path(str(Path(__file__).resolve().with_name("realms_test_paths.py")))
HERMES_ROOT, PLUGIN_ROOT = _paths["HERMES_ROOT"], _paths["PLUGIN_ROOT"]

ROOT = HERMES_ROOT
MODULE = PLUGIN_ROOT / "realms/vm_driver_lifetime.py"
SELF = Path(__file__).resolve()


def _identity(pid):
    fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
    return {"pid": pid, "ppid": int(fields[1]), "ticks": fields[19]}


def _publish(path, payload):
    staging = path.with_suffix(".pending")
    staging.write_text(json.dumps(payload), encoding="utf-8")
    staging.replace(path)


def _signal_pidfd(fd, signum):
    # Standalone Python builds can omit the wrapper despite kernel support.
    libc = ctypes.CDLL(None, use_errno=True)
    send = libc.pidfd_send_signal
    send.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]
    send.restype = ctypes.c_int
    if send(fd, signum, None, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _argv(role, root):
    return [sys.executable, "-I", "-S", str(SELF), role, str(root)]


def _driver(root):
    # Make a TERM-only parent-death mechanism insufficient. Finite lifetime is
    # an emergency fixture bound, far longer than the asserted retirement bound.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    _publish(root / "driver.json", _identity(os.getpid()))
    time.sleep(60)


def _supervisor(root):
    spec = json.loads((root / "spec.json").read_text(encoding="utf-8"))
    owner = runpy.run_path(str(MODULE))["GuestDriverSupervisor"](**spec)

    def witness():
        deadline = time.monotonic() + 10
        while owner.process is None and time.monotonic() < deadline:
            time.sleep(.01)
        if owner.process is not None:
            # This PID comes from the retained product Popen, NOT the child file.
            _publish(root / "owner.json", _identity(owner.process.pid))

    observer = threading.Thread(target=witness, daemon=True)
    observer.start()
    # Keep run() on the main thread so a future signal handler is testable.
    owner.run(control_fd=0)
    observer.join(timeout=2)


def _await_witness(root, supervisor):
    deadline = time.monotonic() + 10
    paths = (root / "owner.json", root / "driver.json")
    while not all(path.exists() for path in paths):
        assert supervisor.poll() is None, "supervisor exited before startup witnesses"
        assert time.monotonic() < deadline, "missing owned-child startup witness"
        time.sleep(.02)
    owner, driver = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    assert owner == driver, (owner, driver)
    assert owner["ppid"] == supervisor.pid, "driver must retain the supervisor's direct Popen PID across exec"
    return owner


def _reap_adopted():
    # This is an isolated subreaper containing only this fixture's descendants.
    # waitpid never signals, and returns ECHILD only after every orphan is reaped.
    # Unverified children are allowed their finite emergency lifetime, not killed.
    deadline = time.monotonic() + 70
    while True:
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        assert time.monotonic() < deadline, "fixture descendants did not finish/reap"
        if pid == 0:
            time.sleep(.02)


def _reaper(root, death_signal):
    libc = ctypes.CDLL(None, use_errno=True)
    # PR_SET_CHILD_SUBREAPER affects this fresh exec only, never pytest/backend.
    if libc.prctl(36, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    open_pidfd = runpy.run_path(str(MODULE.with_name("setup_process.py")))["open_pidfd"]
    env = {
        "HOME": str(root), "HERMES_HOME": str(root / "hermes"),
        "XDG_CONFIG_HOME": str(root / "config"), "XDG_CACHE_HOME": str(root / "cache"),
        "XDG_RUNTIME_DIR": str(root), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
        "CUA_DRIVER_RS_TELEMETRY_ENABLED": "0",
    }
    _publish(root / "spec.json", {
        "argv": _argv("driver", root), "env": env, "cwd": str(ROOT),
        # A successful stop ACK deliberately cannot retire the TERM-ignoring child.
        "stop_argv": [sys.executable, "-I", "-S", "-c", "pass"],
        "heartbeat_timeout": 60.0, "stop_timeout": 2.0, "exit_timeout": 2.0,
        "terminate_timeout": 2.0, "kill_timeout": 2.0,
    })
    supervisor = peer = None
    child_fd = None
    verified = False
    receipt = {}
    try:
        # A peer in the same process group/session must survive. Do not isolate
        # it with setsid: that would conceal accidental broad group cleanup.
        peer = subprocess.Popen(
            [sys.executable, "-I", "-S", "-c", "import time; time.sleep(60)"],
            cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        supervisor = subprocess.Popen(
            _argv("supervisor", root), cwd=str(ROOT), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        identity = _await_witness(root, supervisor)
        assert _identity(identity["pid"]) == identity
        child_fd = open_pidfd(identity["pid"])
        # Validate AFTER capture too: a PID reuse between read and pidfd_open
        # must not authorize signalling the new process. Retain this fd to end.
        assert _identity(identity["pid"]) == identity
        assert supervisor.poll() is None
        verified = True
        watcher = select.poll()
        watcher.register(child_fd, select.POLLIN)
        assert not watcher.poll(0), "driver exited before supervisor-death injection"
        assert supervisor.stdin is not None
        supervisor.stdin.write(b"HEARTBEAT\n")
        supervisor.stdin.flush()
        supervisor.send_signal(death_signal)  # exact retained direct Popen only
        supervisor.wait(timeout=12)
        # Keep the control writer OPEN: neither EOF nor lease expiry can satisfy
        # this invariant. Observe exit before any fixture rescue/reaping occurs.
        receipt["driver_exited_before_cleanup"] = bool(watcher.poll(8000))
        receipt["peer_survived"] = peer.poll() is None
        receipt["supervisor_returncode"] = supervisor.returncode
        receipt["identity"] = identity
    finally:
        if supervisor is not None:
            if supervisor.stdin is not None:
                supervisor.stdin.close()
            try:
                supervisor.wait(timeout=12)
            except subprocess.TimeoutExpired:
                supervisor.kill()
                supervisor.wait(timeout=5)
            assert supervisor.stderr is not None
            receipt["supervisor_stderr"] = supervisor.stderr.read().decode("utf-8", errors="replace")
            supervisor.stderr.close()
        if child_fd is not None:
            try:
                if verified:
                    try:
                        _signal_pidfd(child_fd, signal.SIGKILL)
                    except ProcessLookupError:
                        pass  # already exited; never fall back to numeric PID
            finally:
                os.close(child_fd)
        if peer is not None:
            if peer.poll() is None:
                peer.kill()
            peer.wait(timeout=5)
        _reap_adopted()
    receipt["all_fixture_children_reaped"] = True
    print(json.dumps(receipt), flush=True)


if __name__ == "__main__":
    role, root_arg = sys.argv[1:3]
    root = Path(root_arg)
    if role == "reaper":
        _reaper(root, int(sys.argv[3]))
    else:
        {"supervisor": _supervisor, "driver": _driver}[role](root)
else:
    import pytest

    pytestmark = pytest.mark.linux_only

    @pytest.mark.parametrize("death_signal", ["SIGTERM", "SIGKILL"])
    def test_supervisor_death_retires_only_its_owned_driver(tmp_path, death_signal):
        """A real killed supervisor cannot strand even a TERM-resistant driver."""
        result = subprocess.run(
            [*_argv("reaper", tmp_path), str(int(getattr(signal, death_signal)))],
            cwd=str(ROOT), env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
            start_new_session=True,  # confine even a regressed group kill to this fixture
        )
        assert result.returncode == 0, result.stderr
        receipt = json.loads(result.stdout)
        assert receipt["peer_survived"], receipt
        assert receipt["all_fixture_children_reaped"], receipt
        assert receipt["driver_exited_before_cleanup"], (
            "supervisor death stranded its exact owned driver; fixture pidfd rescue "
            "does not count as product retirement", receipt
        )
