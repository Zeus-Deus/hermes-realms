import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from realms.manager import Manager

pytestmark = pytest.mark.e2e


def test_launcher_proxies_real_pipes_cwd_exit_and_sanitized_env(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKEND_SECRET_MUST_NOT_REAPPEAR", "forbidden-backend-value")
    m = Manager(tmp_path)
    r = m.start("pipes")
    try:
        env = dict(
            m.env(r["id"]),
            CUSTOM_CALLER_OVERRIDE="preserved",
            PYTHONPATH=str(Path(__file__).parents[1]),
        )
        code = "import json,os,sys,pathlib; print(json.dumps({'input':sys.stdin.read(),'cwd':os.getcwd(),'env':dict(os.environ),'cgroup':pathlib.Path('/proc/self/cgroup').read_text()})); print('stderr-real',file=sys.stderr); sys.exit(17)"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "realms.launch",
                str(tmp_path),
                r["id"],
                "--",
                sys.executable,
                "-c",
                code,
            ],
            env=env,
            cwd=tmp_path,
            input="real-pipe-input",
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 17, result.stderr
        receipt = json.loads(result.stdout)
        assert receipt["input"] == "real-pipe-input"
        assert receipt["cwd"] == str(tmp_path)
        assert receipt["env"]["CUSTOM_CALLER_OVERRIDE"] == "preserved"
        assert "BACKEND_SECRET_MUST_NOT_REAPPEAR" not in receipt["env"]
        assert r["scope"] in receipt["cgroup"]
        assert result.stderr.strip() == "stderr-real"
    finally:
        m.stop(r["id"])


def test_launcher_forwards_sigterm_and_waits_for_actual_child(tmp_path):

    m = Manager(tmp_path)
    r = m.start("signals")
    process = None
    try:
        env = dict(m.env(r["id"]), PYTHONPATH=str(Path(__file__).parents[1]))
        code = "import signal,time,sys; signal.signal(signal.SIGTERM,lambda *args: sys.exit(23)); print('ready',flush=True); time.sleep(300)"
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "realms.launch",
                str(tmp_path),
                r["id"],
                "--",
                sys.executable,
                "-c",
                code,
            ],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert process.stdout.readline().strip() == "ready"
        process.terminate()
        assert process.wait(timeout=10) == 23
    finally:
        m.stop(r["id"])
        if process is not None:
            process.communicate(timeout=5)


@pytest.mark.parametrize("caller_controls_tty", [False, True])
def test_prefix_works_from_any_cwd_with_real_controlling_pty(
    tmp_path, caller_controls_tty
):
    import fcntl
    import pty
    import struct
    import termios

    m = Manager(tmp_path)
    r = m.start("pty")
    master, slave = pty.openpty()
    process = None
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
        assert hasattr(m, "command_prefix"), "portable scope launcher prefix is missing"
        code = "import os,json; fd=os.open('/dev/tty',os.O_RDWR); print(json.dumps({'tty':os.isatty(0),'size':list(os.get_terminal_size(0))})); os.close(fd)"
        process = subprocess.Popen(
            [*m.command_prefix(r["id"]), sys.executable, "-c", code],
            env=m.env(r["id"]),
            cwd=tmp_path,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            start_new_session=True,
            preexec_fn=(lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0))
            if caller_controls_tty
            else None,
        )
        os.close(slave)
        slave = None
        assert process.wait(timeout=12) == 0
        output = b""
        while True:
            try:
                chunk = os.read(master, 65536)
                if not chunk:
                    break
                output += chunk
            except OSError:
                break
        receipt = json.loads(output.decode().strip())
        assert receipt == {"tty": True, "size": [120, 40]}
    finally:
        if slave is not None:
            os.close(slave)
        os.close(master)
        m.stop(r["id"])
        if process is not None:
            process.wait(timeout=5)
