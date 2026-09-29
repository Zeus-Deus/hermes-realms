"""One explicit decision releases every held chat with no recorded Realm use."""
import json
import runpy

import pytest
from realms_test_paths import PLUGIN_ROOT

load = runpy.run_path(str(PLUGIN_ROOT / "realms/_binding.py"))["load_runtime"]
integration = load("integration")
pytestmark = pytest.mark.platforms("linux")

RECORD = "r-" + "e" * 24
# Held earlier conversations with no recorded Realm use.
UNUSED = {
    "unset": {},
    "ask": {"mode": "ask"},
    "disabled-held": {"mode": "host", "execution_contract": "legacy-held-v0"},
    "finished": {"setup_generation": 3},
}
# Held conversations the store records as using a Realm, or cannot vouch for.
USED = {
    "realm-mode": {"mode": "realm"},
    "kind": {"realm_kind": "realm"},
    "requested": {"requested_kind": "omarchy-vm"},
    "intent": {"setup_intent": "{}"},
    "record": {},
    "copied-receipt": {"execution_contract": "optional-targets-v1",
                       "permission_receipt": json.dumps({"home": "/elsewhere", "owner": "copied-receipt",
                                                         "contract": "optional-targets-v1"})},
}
# Not held at all.
FREE = {"fresh": {"execution_contract": "optional-targets-v1"}, "host": {"mode": "host"}}


def legacy_store(home):
    home.mkdir(parents=True, exist_ok=True)
    store = integration.OwnershipStore(home)
    with store.connection() as db:
        for owner, columns in {**UNUSED, **USED, **FREE}.items():
            names = ["id", *columns]
            db.execute(f"INSERT INTO owners({','.join(names)}) VALUES ({','.join('?' * len(names))})",
                       (owner, *columns.values()))
            db.execute("INSERT INTO aliases VALUES ('session_id',?,?)", (owner, owner))
    record = {"id": RECORD, "home": str(home.resolve()), "session_id": "record", "status": "stopped"}
    (home / "realms" / (RECORD + ".json")).write_text(json.dumps(record))
    return store


def states(store):
    return {owner: store.permission(owner)["state"] for owner in (*UNUSED, *USED, *FREE)}


def test_release_converts_only_unused_chats_like_a_single_review_and_is_idempotent(tmp_path):
    review = load("bulk_review")
    home = tmp_path / "profile"
    store = legacy_store(home)
    before = states(store)
    assert {o for o, s in before.items() if s == "legacy-pending"} == {*UNUSED, *USED}

    preview = review.preview(home)
    assert sorted(o for o, *_ in preview["eligible"]) == sorted(UNUSED)
    assert sorted(preview["kept"]) == sorted(USED)
    assert states(store) == before, "preview must not write"

    result = review.release(home, provenance="cli")
    assert (result["released"], result["held"]) == (len(UNUSED), len(USED))
    after = states(store)
    assert {o: after[o] for o in UNUSED} == dict.fromkeys(UNUSED, "optional")
    assert {o: after[o] for o in (*USED, *FREE)} == {o: before[o] for o in (*USED, *FREE)}
    # Same ledger semantics as /realm review: unset becomes stored ask, an
    # explicit choice (including Disable) is preserved; receipt names the owner.
    assert store.mode("unset", None) == "ask" and store.mode("ask", None) == "ask"
    assert store.mode("disabled-held", None) == "host"
    receipt = json.loads(store.permission("unset")["receipt"])
    assert receipt["owner"] == "unset" and receipt["home"] == str(home.resolve())
    assert receipt["provenance"] == "cli" and receipt["old_contract"] is None
    assert "fresh" not in json.dumps(receipt)
    assert store.permission("finished")["stored_mode"] == "ask"

    with store.connection() as db:
        snapshot = db.execute("SELECT * FROM owners ORDER BY id").fetchall()
    again = review.release(home, provenance="cli")
    assert (again["released"], again["held"]) == (0, len(USED))
    with store.connection() as db:
        assert db.execute("SELECT * FROM owners ORDER BY id").fetchall() == snapshot


def test_changed_scope_refuses_and_attached_execution_stays_held(tmp_path):
    review = load("bulk_review")
    home = tmp_path / "profile"
    store = legacy_store(home)
    reviewed = review.preview(home)["digest"]
    with store.connection() as db:
        db.execute("INSERT INTO owners(id) VALUES ('late')")
    with pytest.raises(integration.OwnerError, match="changed"):
        review.release(home, provenance="cli", expected=reviewed)
    assert set(o for o, s in states(store).items() if s == "legacy-pending") == {*UNUSED, *USED}
    result = review.release(home, provenance="cli", attached=lambda names: "ask" in names)
    assert store.permission("ask")["state"] == "legacy-pending"
    assert store.permission("late")["state"] == "optional"
    assert result["held"] == len(USED) + 1


def test_decision_is_remembered_for_older_chats_first_seen_later(tmp_path):
    review = load("bulk_review")
    home = tmp_path / "profile"
    store = legacy_store(home)
    store.bind(session_id="before-decision", session_origin="resume")
    assert store.permission("before-decision")["state"] == "legacy-pending"
    review.release(home, provenance="cli")
    assert store.permission("before-decision")["state"] == "optional"
    store.bind(session_id="opened-later", session_origin="resume")
    assert store.permission("opened-later")["state"] == "optional"
    assert store.mode("opened-later", None) == "ask"
    # A later chat that owns a resource record is still recorded Realm use.
    record = {"id": "r-" + "f" * 24, "home": str(home.resolve()), "session_id": "lost-owner", "status": "stopped"}
    (home / "realms" / (record["id"] + ".json")).write_text(json.dumps(record))
    store.bind(session_id="lost-owner", session_origin="resume")
    assert store.permission("lost-owner")["state"] == "legacy-pending"
    # A copied store from another profile does not carry the decision.
    other = tmp_path / "other"
    (other / "realms").mkdir(parents=True)
    (other / "realms/sessions.sqlite3").write_bytes((home / "realms/sessions.sqlite3").read_bytes())
    copied = integration.OwnershipStore(other)
    copied.bind(session_id="elsewhere", session_origin="resume")
    assert copied.permission("elsewhere")["state"] == "legacy-pending"


@pytest.fixture
def setup_module(tmp_path, monkeypatch):
    from test_realms_setup_progress import driver_archive
    installer, archive, _ = driver_archive(tmp_path, monkeypatch)
    original = installer.install
    monkeypatch.setattr(installer, "install", lambda *, home, **kw: original(home=home, archive=archive))
    monkeypatch.setattr(integration, "setup_status", lambda driver_executable: {
        "ready": installer.execution_verified(driver_executable), "message": "fixture dependencies"})
    monkeypatch.setattr(load("manager").Manager, "doctor", lambda self: {"ok": True})
    return lambda: runpy.run_path(str(PLUGIN_ROOT / "setup.py"))


def consented_run(module, home):
    """The host runner: re-describe in the same module, compare, run, verify."""
    reviewed = module["describe"](home)
    current = module["describe"](home)
    assert current["revision"] == reviewed["revision"]
    module["run"](home)
    after = module["describe"](home)
    assert after["ready"] and after["revision"] == reviewed["revision"], after
    return reviewed


def test_setup_describes_counts_and_run_releases_them(tmp_path, setup_module):
    home = tmp_path / "profile"
    store = legacy_store(home)
    described = setup_module()["describe"](home)
    text = "\n".join(described["details"])
    assert not described["ready"]
    assert (f"{len(UNUSED)} earlier chats with no Realm use will run on your normal desktop "
            "(agent outside the Realm; Realm available as a tool).") in text
    assert f"{len(USED)} chats that used a Realm keep asking for /realm review." in text
    assert states(store)["unset"] == "legacy-pending", "describe must not write"

    with store.connection() as db:
        db.execute("INSERT INTO owners(id) VALUES ('late')")
    changed = setup_module()["describe"](home)
    assert changed["revision"] != described["revision"]
    assert f"{len(UNUSED) + 1} earlier chats" in "\n".join(changed["details"])

    consented_run(setup_module(), home)
    assert {o for o, s in states(store).items() if s == "legacy-pending"} == set(USED)
    assert store.permission("late")["state"] == "optional"
    assert json.loads(store.permission("late")["receipt"])["provenance"] == "setup-consent"
    # Nothing left to decide: enabling again asks nothing.
    again = setup_module()["describe"](home)
    assert again["ready"] and again["revision"] == changed["revision"]


def test_host_setup_runner_describes_without_writing(tmp_path):
    """The host's own isolated describe subprocess shows the counts and binds them in the revision."""
    import subprocess
    import sys
    from hermes_cli.plugins_setup import _RUNNER
    home = tmp_path / "profile"
    store = legacy_store(home)
    raw = (home / "realms/sessions.sqlite3").read_bytes()

    def describe():
        out = subprocess.run([sys.executable, "-I", "-B", "-c", _RUNNER, str(PLUGIN_ROOT / "setup.py"),
                              "describe", str(home), ""], cwd=PLUGIN_ROOT, capture_output=True, text=True,
                             env={"HERMES_HOME": str(home), "PATH": "/usr/bin:/bin"}, check=True)
        return json.loads(out.stdout)

    first = describe()
    assert first["ready"] is False
    assert f"{len(UNUSED)} earlier chats with no Realm use" in "\n".join(first["details"])
    assert (home / "realms/sessions.sqlite3").read_bytes() == raw
    assert describe()["revision"] == first["revision"]
    with store.connection() as db:
        db.execute("INSERT INTO owners(id) VALUES ('late')")
    assert describe()["revision"] != first["revision"]


def test_setup_run_refuses_a_count_that_changed_after_review(tmp_path, setup_module):
    home = tmp_path / "profile"
    store = legacy_store(home)
    module = setup_module()
    module["describe"](home)
    with store.connection() as db:
        db.execute("INSERT INTO owners(id) VALUES ('late')")
    with pytest.raises(PermissionError, match="changed"):
        module["run"](home)
    assert states(store)["unset"] == "legacy-pending"


def test_fresh_profile_setup_covers_chats_opened_later(tmp_path, setup_module):
    home = tmp_path / "profile"
    home.mkdir()
    described = setup_module()["describe"](home)
    assert not described["ready"]
    assert "Older chats Realms has no record of" in "\n".join(described["details"])
    assert not (home / "realms").exists(), "describe must not create the store"
    consented_run(setup_module(), home)
    store = integration.OwnershipStore(home)
    store.bind(session_id="from-before-install", session_origin="resume")
    assert store.permission("from-before-install")["state"] == "optional"


@pytest.fixture
def registered(tmp_path, monkeypatch):
    from hermes_cli.plugins import discover_plugins, get_plugin_manager
    home = tmp_path / "profile"
    legacy_store(home)
    (home / "plugins").mkdir()
    (home / "plugins/hermes-realms").symlink_to(PLUGIN_ROOT, target_is_directory=True)
    (home / "config.yaml").write_text("plugins:\n  enabled: [hermes-realms]\n  realms:\n    default_mode: host\napprovals:\n  mode: off\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    discover_plugins(force=True)
    assert "hermes-realms" in get_plugin_manager()._plugins
    service = integration.get_integration(home)
    yield service
    # The record is an inert metadata fixture, not compute; unload would inspect it.
    (home / "realms" / (RECORD + ".json")).unlink()
    service.unload()


def test_slash_command_is_the_decision_and_released_chats_run_tools(registered):
    import model_tools
    from hermes_cli.plugins import get_plugin_command_handler
    from tools.registry import registry
    service = registered
    effects = []
    registry.register(name="unused_probe", toolset="test", schema={"name": "unused_probe", "parameters": {"type": "object"}},
                      handler=lambda args, **kw: effects.append(kw.get("session_id")) or json.dumps({"executed": True}))
    for owner in ("unset", "realm-mode"):
        assert json.loads(model_tools.handle_function_call("unused_probe", {}, session_id=owner))["error_code"] == "legacy_permission_review_required"
    # The agent cannot make this decision, even out of schema.
    assert json.loads(model_tools.handle_function_call("realm", {"action": "review"}, session_id="unset")).get("error")
    command = get_plugin_command_handler("realm")
    assert json.loads(command("review everything", session_id="unset")).get("error")
    assert service.owners.permission("unset")["state"] == "legacy-pending"

    result = json.loads(command("review unused", session_id="realm-mode"))
    assert (result["released"], result["held"]) == (len(UNUSED), len(USED)), result
    assert "/realm review" in result["message"]
    assert json.loads(model_tools.handle_function_call("unused_probe", {}, session_id="unset"))["executed"]
    assert json.loads(model_tools.handle_function_call("unused_probe", {}, session_id="realm-mode"))["error_code"] == "legacy_permission_review_required"
    assert effects == ["unset"]
    assert json.loads(command("review --all-unused", session_id="unset"))["released"] == 0
    assert service._attachments == {} and service._vm is None


def test_cli_dry_run_counts_and_review_releases(tmp_path, capsys):
    cli = load("cli")
    home = tmp_path / "profile"
    store = legacy_store(home)
    assert cli.main(["--home", str(home), "review", "unused", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "would_release": len(UNUSED), "held": len(USED), "decided": False}
    assert states(store)["unset"] == "legacy-pending"
    assert cli.main(["--home", str(home), "review", "unused"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert (result["released"], result["held"]) == (len(UNUSED), len(USED))
    assert json.loads(store.permission("ask")["receipt"])["provenance"] == "cli"
