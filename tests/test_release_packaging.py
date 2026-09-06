"""Build the public source archive and its wheel without private evidence."""

from email.parser import BytesParser
from pathlib import Path
import shutil
import subprocess
import tarfile
import zipfile


def test_public_distributions_include_license_and_exclude_private_evidence(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    # Work from both a Git checkout and an unpacked source distribution.
    shutil.copytree(
        repo,
        source,
        ignore=shutil.ignore_patterns(
            ".git", ".worktrees", ".venv", "node_modules", "__pycache__",
            ".pytest_cache", ".ruff_cache", "build", "dist", "*.egg-info",
            "cua-driver", ".env", ".env.*", "*.log", "*.sqlite", "*.db",
        ),
    )

    # Deliberate packaging fixtures, not execution receipts or real secrets.
    excluded = (
        "docs/e2e/packaging-fixture.json",
        "docs/capture-evidence/packaging-fixture.png",
        "docs/packaging-fixture.txt",
        "integration/packaging-fixture.patch",
        ".env",
    )
    for name in excluded:
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("packaging exclusion fixture\n")

    artifacts = tmp_path / "dist"
    subprocess.run(
        ["uv", "build", "--out-dir", str(artifacts)],
        cwd=source,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    with tarfile.open(next(artifacts.glob("*.tar.gz"))) as archive:
        files = {}
        for member in archive.getmembers():
            if member.isfile() and "/" in member.name:
                stream = archive.extractfile(member)
                assert stream is not None
                with stream:
                    files[member.name.split("/", 1)[1]] = stream.read()
    assert not set(excluded).intersection(files)
    assert files["CLAUDE.md"] == b"@AGENTS.md\n"
    assert files["LICENSE"].startswith(b"MIT License\n")
    assert b"_cua_research" not in files["AGENTS.md"]
    for name in ("plugin.yaml", "desktop/plugin.js", "skills/realms/SKILL.md"):
        assert name in files

    with zipfile.ZipFile(next(artifacts.glob("*.whl"))) as archive:
        names = archive.namelist()
        metadata = BytesParser().parsebytes(
            archive.read(next(name for name in names if name.endswith(".dist-info/METADATA")))
        )
        assert metadata["License-Expression"] == "MIT"
        assert metadata.get_all("License-File") == ["LICENSE"]
        assert archive.read(next(name for name in names if name.endswith(".dist-info/licenses/LICENSE"))) == files["LICENSE"]
        for name in (
            "realms/web/vendor/novnc/AUTHORS",
            "realms/web/vendor/novnc/LICENSE.txt",
            "realms/web/vendor/novnc/docs/LICENSE.MPL-2.0",
            "realms/web/vendor/novnc/vendor/pako/LICENSE",
        ):
            assert archive.read(name) == files[name]
        assert not any(name.startswith(("docs/e2e/", "docs/capture-evidence/")) for name in names)
