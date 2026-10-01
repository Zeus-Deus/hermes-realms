"""Readable `list` output and the terminal `view` link, over real sockets."""
import io
import json
import os
from pathlib import Path
import re
import runpy
import socket
import subprocess
import sys
import tempfile
import threading
import time

import pytest
from realms_test_paths import HERMES_ROOT, PLUGIN_ROOT

load = runpy.run_path(str(PLUGIN_ROOT / "realms/_binding.py"))["load_runtime"]
terminal = load("terminal")
pytestmark = pytest.mark.platforms("linux")

NOW = 1_800_000_000.0


def records():
    return [
        {"id": "v-" + "a" * 24, "kind": "omarchy-vm", "status": "recovery-required",
         "memory": 3072, "session_id": "old", "created_at": NOW - 3 * 86400,
         "last_activity": NOW - 2 * 86400, "recovery_reason": "VM retained workspace file changed: disk.qcow2"},
        {"id": "r-" + "b" * 24, "status": "stopped", "size": "1920x1080",
         "session_id": "s-stopped", "created_at": NOW - 7200, "last_activity": NOW - 3600,
         "workspace_dir": "/w"},
        {"id": "v-" + "c" * 24, "kind": "omarchy-vm", "status": "running", "memory": 3072,
         "session_id": "s-live", "created_at": NOW - 4000, "last_activity": NOW - 25},
    ]


def test_table_is_readable_running_first_with_states_and_reasons():
    text = terminal.table(records(), now=NOW, width=120)
    lines = text.splitlines()
    assert lines[0].split() == ["ID", "KIND", "STATE", "SIZE", "SESSION", "AGE", "ACTIVE"]
    assert lines[1].startswith("v-" + "c" * 24)
    assert "running" in lines[1] and "3 GB RAM" in lines[1] and "1h" in lines[1] and "25s" in lines[1]
    assert "needs recovery" in lines[2] and "1920x1080" in lines[3]
    assert "1 running, 1 needs recovery, 1 stopped" in text
    assert "needs recovery (1): VM retained workspace file changed: disk.qcow2" in text
    assert "hermes realms view" in text
    assert "{" not in text
    assert terminal.table([], now=NOW) == "No realms in this profile."


def test_long_sessions_are_clipped_to_the_terminal_width():
    rows = [dict(records()[2], session_id="coding_" + "f" * 64)]
    text = terminal.table(rows, now=NOW, width=100)
    assert max(len(line) for line in text.splitlines()) <= 100
    assert "…" in text


class Tty(io.StringIO):
    def isatty(self):
        return True


class Args:
    json = False
    table = False


def test_json_stays_the_contract_for_pipes_and_with_flag():
    piped = io.StringIO()
    terminal.print_records(records(), Args(), piped)
    assert json.loads(piped.getvalue()) == records()
    flagged = Tty()
    args = Args()
    args.json = True
    terminal.print_records(records(), args, flagged)
    assert json.loads(flagged.getvalue()) == records()
    human = Tty()
    terminal.print_records(records(), Args(), human)
    assert "STATE" in human.getvalue() and not human.getvalue().lstrip().startswith("[")


def test_ssh_hint_names_this_server_and_its_ssh_port():
    hint = terminal.ssh_hint(40123, {"SSH_CONNECTION": "100.70.1.2 51234 100.119.0.9 22"})
    assert re.fullmatch(r"ssh -N -L 40123:127\.0\.0\.1:40123 \S+@100\.119\.0\.9", hint)
    hint = terminal.ssh_hint(40123, {"SSH_CONNECTION": "10.0.0.2 51234 10.0.0.9 2222"})
    assert hint.endswith(" -p 2222 " + hint.split()[-1]) and "@10.0.0.9" in hint
    assert terminal.ssh_hint(40123, {}) is None


def test_hyperlink_only_for_terminals():
    url = "http://127.0.0.1:1/realms/x/view#ticket=t"
    assert terminal.hyperlink(url, io.StringIO()) == url
    linked = terminal.hyperlink(url, Tty())
    assert linked.startswith("\033]8;;" + url) and url in linked


def test_tailnet_address_rejects_anything_outside_tailscale_range():
    class Done:
        def __init__(self, out):
            self.stdout = out

    assert terminal.tailnet_address(lambda *a, **k: Done("100.101.102.103\n")) == "100.101.102.103"
    with pytest.raises(ValueError, match="no Tailscale"):
        terminal.tailnet_address(lambda *a, **k: Done("192.168.1.4\n"))


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.4", "::"])
def test_viewer_refuses_wildcard_and_lan_binds(host):
    server = load("bridge").ViewerServer(lambda rid: None)
    with pytest.raises(ValueError, match="loopback or a Tailscale"):
        server.start(host=host)


def vnc_source():
    """Private 0600 Unix RFB source recording what the viewer forwards."""
    runtime = tempfile.mkdtemp(prefix="realm-term-")
    os.chmod(runtime, 0o700)
    endpoint = Path(runtime) / "vnc"
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(endpoint))
    endpoint.chmod(0o600)
    listener.listen()
    listener.settimeout(0.1)
    received, stopping = [], threading.Event()

    def serve():
        while not stopping.is_set():
            try:
                peer, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with peer:
                peer.settimeout(0.1)
                peer.sendall(b"RFB 003.008\n")
                while not stopping.is_set():
                    try:
                        data = peer.recv(1024)
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    if not data:
                        break
                    received.append(data)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    def close():
        stopping.set()
        listener.close()
        thread.join(5)

    return runtime, endpoint, received, close


def test_view_serves_view_only_link_and_closes_when_realm_stops(tmp_path, monkeypatch):
    from websockets.sync.client import connect

    runtime, endpoint, received, close = vnc_source()
    lifecycle = load("lifecycle")
    record = {"id": "r-" + "d" * 24, "generation": "g1", "status": "running", "size": "320x240",
              "session_id": "owner", "runtime_dir": runtime, "vnc_socket": str(endpoint),
              "processes": {"worker": lifecycle.identity(os.getpid())}}
    live = threading.Event()
    live.set()
    monkeypatch.setattr(terminal, "_find", lambda home, rid, kind=None: record)
    out = io.StringIO()
    result = {}
    worker = threading.Thread(target=lambda: result.update(code=terminal.view(
        tmp_path / "profile", record["id"], stream=out, environ={},
        alive=lambda home, rec: record if live.is_set() else None)))
    worker.start()
    try:
        deadline = time.monotonic() + 10
        while "#ticket=" not in out.getvalue():
            assert time.monotonic() < deadline, out.getvalue()
            time.sleep(0.05)
        text = out.getvalue()
        assert text.startswith(f"Realm {record['id']} · running · 320x240 · session owner")
        url = re.search(r"http://127\.0\.0\.1:\d+/realms/\S+", text).group(0)
        assert url.endswith("&view=1") and "ssh -N" not in text
        origin, token = url.split("/realms/")[0], re.search(r"ticket=([^&]+)", url).group(1)
        ws_url = origin.replace("http:", "ws:") + f"/api/realms/{record['id']}/vnc"
        with connect(ws_url, origin=origin, subprotocols=["binary", "realm." + token]) as ws:
            assert ws.recv(timeout=5) == b"RFB 003.008\n"
            ws.send(b"RFB 003.008\n" + b"\x01" + b"\x01")
            ws.send(bytes([5, 0, 0, 10, 0, 10]))  # PointerEvent: dropped server-side
            ws.send(bytes([3, 1, 0, 0, 0, 0, 0, 1, 0, 1]))  # update request: forwarded
            time.sleep(0.5)
        forwarded = b"".join(received)
        assert forwarded.endswith(bytes([3, 1, 0, 0, 0, 0, 0, 1, 0, 1]))
        assert bytes([5, 0, 0, 10, 0, 10]) not in forwarded
        # A control capability was never issued for this link.
        with pytest.raises(Exception):
            with connect(ws_url + "?control=1", origin=origin,
                         subprotocols=["binary", "realm." + token]) as ws:
                ws.recv(timeout=3)
        live.clear()
        worker.join(10)
        assert not worker.is_alive() and result["code"] == 0
        assert "stopped; viewer closed" in out.getvalue()
        port = int(origin.rsplit(":", 1)[1])
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
    finally:
        live.clear()
        worker.join(10)
        close()


def test_view_prints_tunnel_for_ssh_and_refuses_stopped_realm(tmp_path, monkeypatch):
    record = {"id": "r-" + "e" * 24, "generation": "g", "status": "running", "session_id": "o"}
    stop = threading.Event()
    stop.set()
    monkeypatch.setattr(terminal, "_find", lambda home, rid, kind=None: record)
    out = io.StringIO()
    assert terminal.view(tmp_path / "p", record["id"], stream=out, stop=stop,
                         environ={"SSH_CONNECTION": "10.1.1.1 5000 10.2.2.2 22"},
                         alive=lambda home, rec: rec) == 0
    port = re.search(r"127\.0\.0\.1:(\d+)/", out.getvalue()).group(1)
    assert f"ssh -N -L {port}:127.0.0.1:{port} " in out.getvalue()
    assert "Viewer closed." in out.getvalue()
    stopped = dict(record, status="recovery-required")
    monkeypatch.setattr(terminal, "_find", lambda home, rid, kind=None: stopped)
    with pytest.raises(ValueError, match="needs recovery; only a running realm"):
        terminal.view(tmp_path / "p", stopped["id"], stream=io.StringIO(), environ={})


def test_cli_list_stays_json_when_piped(tmp_path):
    env = {key: os.environ[key] for key in ("PATH", "LANG", "TZ") if key in os.environ}
    env.update(HOME=str(tmp_path), HERMES_HOME=str(tmp_path / "profile"), PYTHONPATH=str(HERMES_ROOT))
    base = [sys.executable, str(PLUGIN_ROOT / "cli.py"), "--home", str(tmp_path / "profile")]
    for argv in (["list"], ["list", "--json"], ["vm", "list"]):
        piped = subprocess.run(base + argv, capture_output=True, text=True, env=env, timeout=60)
        assert piped.returncode == 0, piped.stderr
        assert json.loads(piped.stdout) == []
    table = subprocess.run(base + ["vm", "list", "--table"], capture_output=True, text=True,
                           env=env, timeout=60)
    assert table.returncode == 0 and table.stdout.strip() == "No realms in this profile."
