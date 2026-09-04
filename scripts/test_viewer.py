"""Boot actual labwc/GTK/WayVNC and run the real noVNC browser acceptance."""

from pathlib import Path
import os
import subprocess
import sys
import tempfile
import time

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from realms.manager import Manager  # noqa: E402 — bootstrap paths/GI version before importing
from realms.bridge import ViewerServer  # noqa: E402 — bootstrap paths/GI version before importing


def main():
    with tempfile.TemporaryDirectory(prefix="realms-browser-e2e-") as home:
        manager = Manager(home)
        record = manager.start("browser-e2e")
        realm_id = record["id"]
        server = ViewerServer(
            lambda rid: next((r for r in manager.list() if r["id"] == rid), None)
        ).start()
        try:
            fixture = Path(record["runtime_dir"]) / "fixture.json"
            manager.exec(
                realm_id,
                [
                    "/usr/bin/python3",
                    str(root / "tests/fixtures/gtk_probe.py"),
                    str(fixture),
                    "Viewer",
                ],
            )
            deadline = time.monotonic() + 10
            while not fixture.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            if not fixture.exists():
                raise RuntimeError("Actual GTK fixture did not become ready")
            env = dict(
                os.environ,
                REALMS_VIEWER_URL=server.origin
                + f"/realms/{realm_id}/view#ticket="
                + server.issue(realm_id, can_control=True),
                REALMS_TEST_FIXTURE=str(fixture),
                REALMS_TEST_SCREENSHOT=os.environ.get(
                    "REALMS_TEST_SCREENSHOT", str(Path(home) / "viewer.png")
                ),
            )
            completed = subprocess.run(
                ["node", str(root / "tests/viewer-browser.cjs")], env=env
            )
            return completed.returncode
        finally:
            server.stop()
            manager.stop(realm_id)
            assert manager.list() == [], "Browser acceptance leaked a realm"
            assert not Path(record["runtime_dir"]).exists(), (
                "Browser acceptance leaked runtime sockets"
            )


if __name__ == "__main__":
    raise SystemExit(main())
