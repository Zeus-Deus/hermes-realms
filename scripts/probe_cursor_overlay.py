"""Observe pinned Cua overlay pixels in fresh realms; never use a host daemon."""

from pathlib import Path
import base64
import json
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from PIL import ImageChops  # noqa: E402 — bootstrap paths/GI version before importing
from test_cursor_framebuffer import RawRFB, pixel_count  # noqa: E402 — bootstrap paths/GI version before importing
from realms.driver import create_driver_launcher, sandbox_command  # noqa: E402 — bootstrap paths/GI version before importing
from realms.lifecycle import alive  # noqa: E402 — bootstrap paths/GI version before importing
from realms.manager import Manager  # noqa: E402 — bootstrap paths/GI version before importing


def probe(home, evidence, overlay):
    home.mkdir()
    (home / "config.yaml").write_text("plugins:\n  realms:\n    size: 800x600\n")
    manager = Manager(home)
    record = manager.start("overlay-probe")
    client = None
    label = "overlay-on" if overlay else "overlay-off"
    receipt = {
        "realm_id": record["id"],
        "overlay_requested": overlay,
        "theme_requested": "cua.default",
    }
    try:
        env = manager.env(record["id"])
        binary = ROOT / "vendor/cua-driver"
        launcher = create_driver_launcher(manager, record["id"], binary)
        endpoint = str(Path(record["runtime_dir"]) / "overlay-probe.sock")
        args = [
            "serve",
            "--embedded",
            "--permission-mode",
            "standard",
            "--socket",
            endpoint,
            "--cursor-theme",
            "cua.default",
            "--cursor-reduced-motion",
            "on",
        ]
        if not overlay:
            args += ["--no-overlay"]
        job = manager.exec(record["id"], sandbox_command(record, binary, args))
        deadline = time.monotonic() + 15
        while not Path(endpoint).exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert Path(endpoint).exists(), Path(job["stderr_path"]).read_text()

        def call(name, params):
            result = subprocess.run(
                [launcher, "call", name, json.dumps(params), "--socket", endpoint],
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )
            assert result.returncode == 0, result.stderr + result.stdout[:1500]
            return json.loads(result.stdout)

        receipt["version"] = subprocess.run(
            [launcher, "--version"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        ).stdout.strip()
        captured = call("get_desktop_state", {})
        (evidence / (label + "-cua-screen.png")).write_bytes(
            base64.b64decode(captured["screenshot_png_b64"])
        )
        receipt["cua_capture_size"] = [
            captured["screen_width"],
            captured["screen_height"],
        ]
        client = RawRFB(record["vnc_socket"])
        client.move(80, 80)
        time.sleep(0.15)
        baseline = client.frame()
        baseline.save(evidence / (label + "-baseline.png"))
        receipt["actions"] = []
        for scope, x, y in [
            ("window", 280, 200),
            ("window", 560, 380),
            ("desktop", 180, 320),
            ("desktop", 560, 180),
        ]:
            params = {"x": x, "y": y, "scope": scope, "session": "theme-probe"}
            if scope == "desktop":
                params.pop(
                    "scope"
                )  # Typed target and legacy scope are mutually exclusive.
                params["target"] = {"kind": "desktop", "display_id": "primary"}
            response = call("move_cursor", params)
            samples = []
            best_count = -1
            best = None
            started = time.monotonic()
            for _ in range(20):
                frame = client.frame()
                delta = ImageChops.difference(baseline, frame)
                count = pixel_count(delta)
                samples.append(
                    {
                        "seconds": round(time.monotonic() - started, 3),
                        "pixels": count,
                        "bbox": delta.getbbox(),
                    }
                )
                if count > best_count:
                    best_count, best = count, frame
                time.sleep(0.1)
            assert best is not None
            best.save(evidence / f"{label}-{scope}-{x}-{y}.png")
            frame.save(evidence / f"{label}-{scope}-{x}-{y}-settled.png")
            receipt["actions"].append(
                {"params": params, "response": response, "samples": samples}
            )
        # Capture logs/threads before stopping owned scope, to distinguish request from liveness.
        threads = []
        cgroup = Path("/sys/fs/cgroup") / record["cgroup"].lstrip("/") / "cgroup.procs"
        for pid in cgroup.read_text().split():
            for thread in Path("/proc", pid, "task").glob("*/comm"):
                try:
                    name = thread.read_text().strip()
                    if "cua" in name or "overlay" in name:
                        threads.append(name)
                except FileNotFoundError:
                    pass
        receipt["driver_threads"] = sorted(threads)
        (evidence / (label + "-daemon.log")).write_text(
            Path(job["stderr_path"]).read_text()
        )
        manager.shot(record["id"], evidence / (label + "-grim.png"))
        return receipt
    finally:
        if client:
            client.close()
        manager.stop(record["id"])
        assert manager.list() == []
        assert not Path(record["runtime_dir"]).exists()
        assert not (Path("/sys/fs/cgroup") / record["cgroup"].lstrip("/")).exists()
        assert not any(alive(p) for p in record["processes"].values())
        receipt["cleanup"] = "registry/runtime/cgroup/processes removed"
        (evidence / (label + "-receipt.json")).write_text(
            json.dumps(receipt, indent=2) + "\n"
        )


def main():
    evidence = Path(sys.argv[1]).resolve()
    evidence.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="realms-overlay-") as directory:
        for overlay in (False, True):
            receipt = probe(Path(directory) / str(overlay), evidence, overlay)
            print(
                json.dumps(
                    {
                        "overlay_requested": overlay,
                        "version": receipt["version"],
                        "actions": [
                            {
                                "params": a["params"],
                                "response": a["response"],
                                "max_changed_pixels": max(
                                    s["pixels"] for s in a["samples"]
                                ),
                            }
                            for a in receipt["actions"]
                        ],
                        "driver_threads": receipt["driver_threads"],
                        "cleanup": receipt["cleanup"],
                    },
                    sort_keys=True,
                )
            )


if __name__ == "__main__":
    main()
