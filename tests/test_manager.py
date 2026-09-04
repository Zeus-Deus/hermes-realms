def test_default_manager_uses_effective_home_and_safe_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from realms.manager import Manager

    manager = Manager()
    assert manager.home == tmp_path
    assert manager.config.default_mode == "realm"
    assert manager.config.size == "1920x1080"
    assert manager.config.renderer == "gles2"
    assert manager.config.overlay is True
    assert manager.config.cursor_theme == "cua.default"
    assert manager.list() == []


def test_profile_yaml_settings_are_loaded_and_validated(tmp_path):
    import pytest
    from realms.manager import Manager

    (tmp_path / "config.yaml").write_text(
        "plugins:\n  realms:\n    renderer: pixman\n    idle_ttl: 3\n    size: 800x600\n    overlay: false\n    default_mode: ask\n"
    )
    config = Manager(tmp_path).config
    assert config.renderer == "pixman"
    assert config.idle_ttl == 3
    assert config.size == "800x600"
    assert config.overlay is False
    assert config.default_mode == "ask"
    for invalid in [
        "size: '-1x800'",
        "renderer: other",
        "idle_ttl: -2",
        "overlay: 'false'",
        "default_mode: other",
    ]:
        (tmp_path / "config.yaml").write_text(
            "plugins:\n  realms:\n    " + invalid + "\n"
        )
        with pytest.raises(ValueError):
            Manager(tmp_path)


def test_failed_scope_launch_rolls_back_registry_and_runtime(tmp_path, monkeypatch):
    import os
    import pytest
    from realms.manager import Manager, RealmError

    binaries = tmp_path / "bin"
    binaries.mkdir()
    (binaries / "systemd-run").symlink_to("/usr/bin/false")
    monkeypatch.setenv("PATH", str(binaries) + ":" + os.environ["PATH"])
    m = Manager(tmp_path)
    try:
        with pytest.raises(RealmError, match="startup failed"):
            m.start("deliberately-failed-scope")
        assert m.list() == []
    finally:
        for record in m.list():
            m.stop(record["id"])


def test_exec_failure_before_scope_creation_is_rolled_back(tmp_path, monkeypatch):
    import pytest
    from realms.manager import Manager

    binaries = tmp_path / "bin"
    binaries.mkdir()
    executable = binaries / "systemd-run"
    executable.write_text("#!/definitely-missing-interpreter\n")
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(binaries))
    m = Manager(tmp_path)
    try:
        with pytest.raises(OSError):
            m.start("failed-exec")
        assert m.list() == []
    finally:
        for record in m.list():
            m.stop(record["id"])
