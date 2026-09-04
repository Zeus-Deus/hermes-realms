"""Release-pinned overlay-on/off pixel evidence, not inferred configuration."""


import pytest
from PIL import Image, ImageChops

from scripts.probe_cursor_overlay import probe
from test_cursor_framebuffer import evidence_dir, pixel_count


@pytest.mark.parametrize("overlay", [False, True])
def test_cua_overlay_pixels_and_native_pointer_fallback(tmp_path, overlay):
    directory = evidence_dir(tmp_path)
    receipt = probe(tmp_path / "profile", directory, overlay)
    label = "overlay-on" if overlay else "overlay-off"
    with Image.open(directory / (label + "-baseline.png")) as source:
        baseline = source.convert("RGB")
    assert receipt["version"] == "cua-driver 0.23.2"
    assert receipt["cua_capture_size"] == [800, 600]
    assert receipt["cleanup"] == "registry/runtime/cgroup/processes removed"
    logical = receipt["actions"][:2]
    desktop = receipt["actions"][2:]
    for action in logical:
        assert action["response"]["route"] == "synthetic_events"
        changed = max(sample["pixels"] for sample in action["samples"])
        assert changed > 500 if overlay else changed == 0
        x, y = action["params"]["x"], action["params"]["y"]
        with Image.open(directory / f"{label}-window-{x}-{y}-settled.png") as source:
            difference = ImageChops.difference(baseline, source.convert("RGB"))
        at_target = pixel_count(difference.crop((x - 16, y - 16, x + 64, y + 80)))
        assert at_target > 20 if overlay else at_target == 0
        # A logical overlay move did not move the actual native pointer at 80,80.
        assert pixel_count(difference.crop((60, 60, 120, 120))) == 0
    assert ("cua-overlay-wl" in receipt["driver_threads"]) == overlay
    for action in desktop:
        assert action["response"]["route"] == "global_input"
        # With overlays disabled, only the old/new native pointer footprints change.
        changed = max(sample["pixels"] for sample in action["samples"])
        assert changed > 20
        if not overlay:
            assert changed < 1000
    print(
        "CUA_OVERLAY_RECEIPT="
        + str(
            {
                "overlay_requested": overlay,
                "logical_max_pixels": [
                    max(s["pixels"] for s in a["samples"]) for a in logical
                ],
                "desktop_max_pixels": [
                    max(s["pixels"] for s in a["samples"]) for a in desktop
                ],
                "threads": receipt["driver_threads"],
                "cleanup": receipt["cleanup"],
            }
        )
    )
