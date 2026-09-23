"""Manual Delete filesystem/CLI qualification; no real compute is launched."""
import os
from pathlib import Path
import runpy
import uuid

import pytest
from realms_test_paths import HERMES_ROOT, PLUGIN_ROOT, install_user_plugin

pytestmark = pytest.mark.linux_only

ROOT = HERMES_ROOT
load = runpy.run_path(str(PLUGIN_ROOT / "realms/_binding.py"))["load_runtime"]


def tree(path):
    return {str(p.relative_to(path)): (p.lstat().st_ino, p.read_bytes())
            for p in path.rglob("*") if p.is_file()}


@pytest.fixture
def regular(tmp_path, monkeypatch):
    mod = load("manager")
    manager = mod.Manager(tmp_path / "profile")
    records = []
    for owner in ("owner-one", "foreign-owner"):
        generation = uuid.uuid4().hex
        record = dict(id="r-" + generation[:24], generation=generation,
                      uid=os.getuid(), home=str(manager.home), session_id=owner,
                      runtime_dir=f"/run/user/{os.getuid()}/hr-{generation[:16]}",
                      scope=f"hermes-realm-{generation}.scope",
                      guardian_unit=f"hermes-realm-{generation}-guard.service",
                      status="stopped", created_at=0, last_activity=0)
        root = load("workspace").create(record)
        (root / "home/sentinel").write_bytes(owner.encode() + b"\x00\xff")
        manager.registry.put(record)
        records.append(record)
    monkeypatch.setattr(mod, "scope_info", lambda unit: {"ActiveState": "inactive"})
    return manager, records[0], records[1]


@pytest.fixture
def vm(tmp_path, monkeypatch):
    fixtures = runpy.run_path(str(PLUGIN_ROOT / "tests/test_realms_vm_retention.py"))
    owned = fixtures["owned"].__wrapped__(tmp_path, monkeypatch)
    manager, _, _ = owned
    monkeypatch.setattr(load("vm_manager"), "_generation_paths",
                        lambda uid, gen: tmp_path / ("runtime-" + gen))
    record = fixtures["fresh"](owned, monkeypatch)
    manager.stop(record["id"])
    peer = manager.start("foreign-owner")
    manager.stop(peer["id"])
    return manager, record, peer


def test_vm_confirmation_rejects_restart_and_foreign_owner(vm):
    manager, record, peer = vm
    snapshot = manager.delete_snapshot(record["id"], session_id=record["session_id"])
    with pytest.raises(load("lifecycle").OwnershipError, match="another session"):
        manager.delete(record["id"], session_id="foreign-owner", expected_snapshot=snapshot)
    resumed = manager.start(record["session_id"])
    manager.stop(record["id"])
    assert resumed["generation"] == record["generation"]
    assert resumed["compute_generation"] != record["compute_generation"]
    before = tree(manager.home)
    with pytest.raises(load("lifecycle").OwnershipError, match="changed"):
        manager.delete(record["id"], session_id=record["session_id"], expected_snapshot=snapshot)
    assert tree(manager.home) == before
    snapshot = manager.delete_snapshot(record["id"], session_id=record["session_id"])
    peer_before = tree(Path(peer["session_dir"]))
    assert manager.delete(record["id"], session_id=record["session_id"], expected_snapshot=snapshot)
    assert not Path(record["session_dir"]).exists()
    assert tree(Path(peer["session_dir"])) == peer_before


def test_regular_snapshot_rejects_republished_record(regular):
    manager, record, peer = regular
    snapshot = manager.delete_snapshot(record["id"], session_id=record["session_id"])
    # Even byte-identical publication after a compute cycle revokes consent.
    manager.registry.put(record)
    before = tree(manager.home)
    with pytest.raises(load("lifecycle").OwnershipError, match="changed"):
        manager.delete(record["id"], session_id=record["session_id"], expected_snapshot=snapshot)
    assert tree(manager.home) == before
    snapshot = manager.delete_snapshot(record["id"], session_id=record["session_id"])
    peer_before = tree(Path(peer["workspace_dir"]))
    assert manager.delete(record["id"], session_id=record["session_id"], expected_snapshot=snapshot)
    assert not Path(record["workspace_dir"]).exists()
    assert tree(Path(peer["workspace_dir"])) == peer_before


def cli_pty(argv, respond, mutate=lambda: None, *, native=False):
    """Real argparse and real tty streams; inherited compute-only test doubles."""
    import errno
    import pty
    import re
    import select
    import sys
    import time
    import traceback

    pid, fd = pty.fork()
    if pid == 0:
        try:
            sys.stdin = os.fdopen(os.dup(0), "r")
            sys.stdout = os.fdopen(os.dup(1), "w", buffering=1)
            sys.stderr = os.fdopen(os.dup(2), "w", buffering=1)
            if native:
                sys.argv = ["hermes", "realms", *argv]
                from hermes_cli.main import main
                code = main() or 0
            else:
                code = load("cli").main(argv)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        except BaseException:
            traceback.print_exc()
            code = 99
        os._exit(code)
    output = b""
    replied = False
    deadline = time.monotonic() + 10
    try:
        while time.monotonic() < deadline:
            if not select.select([fd], [], [], 0.1)[0]:
                continue
            try:
                data = os.read(fd, 65536)
            except OSError as exc:
                if exc.errno == errno.EIO:
                    break
                raise
            if not data:
                break
            output += data
            prompt = re.search(rb"Type (DELETE [^\r\n]+) to confirm: ", output)
            if prompt and not replied:
                mutate()
                os.write(fd, respond(prompt.group(1)))
                replied = True
        else:
            pytest.fail("CLI prompt timed out: " + output.decode())
    finally:
        os.close(fd)
        waited, status = os.waitpid(pid, 0)
    assert waited == pid
    return os.waitstatus_to_exitcode(status), output.decode()


def cli_args(manager, record, *, owner=True):
    args = ["--home", str(manager.home)]
    if record["id"].startswith("v-"):
        args += ["vm"]
    args += ["delete", record["id"]]
    if owner:
        args += ["--session-id", record["session_id"]]
    return args


@pytest.mark.parametrize("kind", ["regular", "vm"])
def test_cli_pty_confirmed_delete(request, kind):
    manager, record, peer = request.getfixturevalue(kind)
    workspace_key = "session_dir" if kind == "vm" else "workspace_dir"
    peer_before = tree(Path(peer[workspace_key]))
    code, output = cli_pty(cli_args(manager, record), lambda phrase: phrase + b"\n")
    assert code == 0, output
    assert '"deleted": true' in output
    assert record["session_id"] in output and record[workspace_key] in output
    assert not Path(record[workspace_key]).exists()
    assert not manager.registry.path(record["id"]).exists()
    assert tree(Path(peer[workspace_key])) == peer_before


@pytest.mark.parametrize("kind", ["regular", "vm"])
@pytest.mark.parametrize("response", [b"no\n", b"\n", b"\x04", b"\x03"])
def test_cli_pty_cancel_preserves_every_byte(request, kind, response):
    manager, record, _ = request.getfixturevalue(kind)
    # Lock creation itself is not a workspace mutation.
    with manager.registry.lock():
        pass
    before = tree(manager.home)
    code, output = cli_pty(cli_args(manager, record), lambda phrase: response)
    assert code == 1 and "cancelled" in output, output
    assert tree(manager.home) == before


@pytest.mark.parametrize("kind", ["regular", "vm"])
def test_cli_rejects_noninteractive(request, kind, monkeypatch, capsys):
    import io
    manager, record, _ = request.getfixturevalue(kind)
    before = tree(manager.home)
    monkeypatch.setattr("sys.stdin", io.StringIO("yes\n"))
    assert load("cli").main(cli_args(manager, record)) == 1
    assert "interactive" in capsys.readouterr().err
    assert tree(manager.home) == before


@pytest.mark.parametrize("kind", ["regular", "vm"])
@pytest.mark.parametrize("case", ["missing-owner", "foreign-owner", "running", "force", "yes"])
def test_cli_selection_and_stopped_guards(request, kind, case):
    manager, record, _ = request.getfixturevalue(kind)
    args = cli_args(manager, record, owner=case != "missing-owner")
    if case == "foreign-owner":
        args[-1] = "foreign-owner"
    elif case == "running":
        record = manager.registry.get(record["id"])
        record["status"] = "running"
        manager.registry.put(record)
    elif case in ("force", "yes"):
        args += ["--" + case]
    with manager.registry.lock():
        pass
    before = tree(manager.home)
    code, output = cli_pty(args, lambda phrase: pytest.fail("must not ask for invalid selection"))
    assert code in (1, 2), output
    assert "Type DELETE" not in output
    assert tree(manager.home) == before


@pytest.mark.parametrize("kind", ["regular", "vm"])
@pytest.mark.parametrize("change", ["cycle", "receipt", "foreign", "objects", "compute"])
def test_cli_stale_prompt_preserves_current_and_foreign_bytes(request, kind, change, monkeypatch):
    manager, record, peer = request.getfixturevalue(kind)
    before = {}
    # A file-backed observer crosses fork without touching systemd.
    state = manager.home / "compute-state"
    state.write_text("inactive")
    module = load("vm_manager" if kind == "vm" else "manager")
    if change == "compute":
        monkeypatch.setattr(module, "scope_info", lambda unit: {"ActiveState": state.read_text()})

    def mutate():
        current = manager.registry.get(record["id"])
        if change == "cycle":
            if kind == "vm":
                resumed = manager.start(record["session_id"])
                manager.stop(record["id"])
                assert resumed["generation"] == record["generation"]
            else:
                # No compositor: publish the lifecycle transitions with stable
                # IDs/generation to exercise the conservative publication fence.
                for status in ("starting", "running", "stopped"):
                    current["status"] = status
                    manager.registry.put(current)
        elif change in ("receipt", "foreign"):
            current["memory" if change == "receipt" else "session_id"] = 1234 if change == "receipt" else "foreign-owner"
            manager.registry.put(current)
        elif change == "objects":
            root = Path(current["session_dir"] if kind == "vm" else current["workspace_dir"])
            root.rename(root.with_name(root.name + "-saved"))
            root.mkdir(mode=0o700)
            (root / "foreign-bytes").write_bytes(b"replacement must survive")
        else:
            state.write_text("active")
        before.update(tree(manager.home))

    code, output = cli_pty(cli_args(manager, record), lambda phrase: phrase + b"\n", mutate)
    assert code == 1, output
    assert before, "the prompt must have been reached"
    assert tree(manager.home) == before


@pytest.mark.parametrize("kind", ["regular", "vm"])
@pytest.mark.parametrize("confirm", [False, True])
def test_native_hermes_realms_pty(request, kind, confirm, monkeypatch):
    manager, record, peer = request.getfixturevalue(kind)
    monkeypatch.setenv("HERMES_HOME", str(manager.home))
    # Test-only plugin opt-in; no setup, host profile or installed app changes.
    install_user_plugin(manager.home)
    (manager.home / "config.yaml").write_text("plugins:\n  enabled: [hermes-realms]\n")
    key = "session_dir" if kind == "vm" else "workspace_dir"
    before, peer_before = tree(Path(record[key])), tree(Path(peer[key]))
    code, output = cli_pty(cli_args(manager, record),
                           lambda phrase: phrase + b"\n" if confirm else b"no\n",
                           native=True)
    assert code == (0 if confirm else 1), output
    assert "Type DELETE" in output
    assert tree(Path(peer[key])) == peer_before
    if confirm:
        assert not Path(record[key]).exists()
        assert not manager.registry.path(record["id"]).exists()
    else:
        assert tree(Path(record[key])) == before


@pytest.mark.parametrize("kind", ["regular", "vm"])
def test_cli_explicit_retry_after_interrupted_deletion(request, kind, monkeypatch):
    manager, record, peer = request.getfixturevalue(kind)
    key = "session_dir" if kind == "vm" else "workspace_dir"
    before = tree(Path(peer[key]))
    # Interrupt after retained bytes are gone but before registry retirement.
    def interrupted(target):
        raise OSError("injected registry interruption")
    with monkeypatch.context() as fault:
        fault.setattr(manager.registry, "remove", interrupted)
        with pytest.raises(OSError, match="registry interruption"):
            manager.delete(record["id"])
    assert manager.registry.get(record["id"])["status"] == "deleting"
    code, output = cli_pty(cli_args(manager, record), lambda phrase: phrase + b"\n")
    assert code == 0, output
    assert not Path(record[key]).exists()
    assert not manager.registry.path(record["id"]).exists()
    assert tree(Path(peer[key])) == before
