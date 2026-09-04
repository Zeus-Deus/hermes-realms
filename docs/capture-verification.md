# Capture fallback and cursor evidence

All captures/input in this receipt used new temporary profile-owned labwc realms. No host display, host input, live development-app restart, or installed driver mutation was used. The standalone product daemon was pinned to **cua-driver 0.23.2**, embedded, standard permission mode, and bubblewrap-contained.

## Own-session screenshot API (TDD)

`realm({"action":"shot"})` and `/realm shot` resolve the trusted conversation owner, require an existing realm, validate its live scope/environment, and call the existing `Manager.shot(id, path)` grim RPC. No realm ID, display, or filesystem destination is exposed in the tool schema. A unique mode-0700 `realms/shot-*/` directory is created inside the owning profile; the PNG is mode 0600. Errors remove incomplete capture artifacts, with no host fallback.

The skill and tool description reserve this action for failed realm Cua `app: screen` capture, not approval bypass. This API does not automatically intercept Cua calls or invent a successful capture.

- **RED:** `tests/test_capture_fallback.py` first failed through real native plugin discovery/registry dispatch: expected `path`, got `{"error":"Use /realm on|off|status|size WIDTHxHEIGHT|stop|watch"}`. Two actual independently sized realms were running; both were removed in `finally`.
- **GREEN:** the same test then passed with a real 800×600 PNG from owner A, not the 1024×768 owner B. A repeated shot creates a different path without overwriting the first.
- Added real fail-closed coverage for missing realm, conflicting task/session owners, mismatched profile, an attempted explicit realm argument, another session's absent realm, and a tampered private Wayland binding. No mock compositor, screenshot, or fabricated RPC result.

Saved real API return: [`capture-evidence/realm-shot-api.json`](capture-evidence/realm-shot-api.json); copied screenshot: [`realm-shot.png`](capture-evidence/realm-shot.png). The return includes `realm_id`, the actual temporary-profile `path`, `mime_type`, `width`, `height`, `bytes`, `sha256`, `capture: "grim"`, and `fallback: true`. The recorded PNG is **1476 bytes**, **800×600**, SHA-256 `0e78e1e00e1b64efefcadff280b26c47573f6b85aa1a36b6d7773abfbc2088f9`. The profile path is pytest-owned and may later expire; the evidence copy persists.

## Native WayVNC fallback: actual framebuffer pixels

`tests/test_cursor_framebuffer.py` opens only the owned private Unix VNC endpoint, negotiates RFB 3.8 with **Raw encoding only**, and deliberately does **not** advertise a client-side Cursor pseudo-encoding. It sends real pointer events on a static, empty 800×600 realm desktop.

- Pointer coordinates: **(160,180) → (540,360)**.
- **103 changed pixels in each cursor crop**, **0 changes outside those crops**.
- Full-frame difference bounding box: **(159,179,552,381)**.
- A repeated complete frame remains identical, excluding application animation as the cause.
- PNGs: [`native-at-a.png`](capture-evidence/native-at-a.png), [`native-at-b.png`](capture-evidence/native-at-b.png), [`native-move-diff.png`](capture-evidence/native-move-diff.png).
- Machine receipt: [`native-cursor-receipt.json`](capture-evidence/native-cursor-receipt.json).

A test-harness initialization issue was corrected before measuring: resizing immediately after the VNC handshake caused neatvnc to disconnect a raw-only client without resize extensions. The regression starts at the desired configured size instead. A Pillow deprecated pixel iterator was replaced, not suppressed, for warning-strict runs.

## Cua theme overlay: supported, actually observed in fresh daemon

Contrary to an assumption that the overlay is absent on this platform, **the pinned 0.23.2 layer-shell overlay does render in the tested fresh realm**. `scripts/probe_cursor_overlay.py` creates independent overlay-disabled and overlay-enabled daemons with `--cursor-theme cua.default --cursor-reduced-motion on`; only the disabled control adds `--no-overlay`. It observes raw WayVNC frames over each action, rather than trusting an option or action success response.

`tests/test_cursor_overlay.py` automates both lanes:

| Real action | Overlay disabled | Overlay enabled |
|---|---|---|
| Synthetic `move_cursor`, explicit `session: theme-probe`, two target positions | **0 changed pixels** at both positions | Thousands of changed pixels; settled cursor appears in each requested target crop |
| Desktop-target `move_cursor`, two target positions | **206 changed pixels** (old/new native cursor) for each action | Visible overlay plus native movement |
| Actual driver thread observation | No `cua-overlay-wl` thread | `cua-overlay-wl` present |

The explicit desktop call uses `target: {kind: desktop, display_id: primary}` **without** legacy `scope`; combining both is correctly rejected as `invalid_action_target`. The corrected calls return `route: global_input` and the independent framebuffer confirms the private pointer movement. Synthetic calls return `route: synthetic_events`; the independent pixels, not the driver's `effect: unverifiable`, establish overlay visibility.

Evidence: [`overlay-on-window-280-200.png`](capture-evidence/overlay-on-window-280-200.png) shows the larger themed cursor and temporary `theme-probe` label, separate from the small native arrow parked at (80,80). [`overlay-on-window-560-380-settled.png`](capture-evidence/overlay-on-window-560-380-settled.png) shows the settled glowing pointer near the second position; its transient label has disappeared. Raw sample timings, bounds, exact action parameters/responses, and process observations are in [`overlay-on-receipt.json`](capture-evidence/overlay-on-receipt.json) and [`overlay-off-receipt.json`](capture-evidence/overlay-off-receipt.json). Pixel totals for the animated themed overlay can vary slightly between runs; the regression uses spatial and magnitude assertions, not exact animated hashes.

**Qualification:** this establishes capability and actual rendering in these fresh, explicitly configured product-contained daemons. It does **not** establish that an existing Hermes DEV/model session has an active overlay; that serving process and action lifecycle require independent observation. Configuring `overlay: true` alone is not evidence. Where no overlay is observed, report degraded **native WayVNC pointer fallback**, which works for actual private pointer movement, **not** for synthetic-only logical cursor moves. The theme label/color can vary or fade; do not promise an always-visible session badge. Both cursor presentations can coexist.

## Commands and cleanup

Focused warning-strict command:

```sh
REALMS_TEST_CAPTURE_EVIDENCE=$PWD/docs/capture-evidence \
PYTHONPATH=../hermes-realms-host-sdk:$PWD \
../hermes-realms-host-sdk/.venv/bin/python -W error -m pytest \
  -o asyncio_default_fixture_loop_scope=function \
  tests/test_capture_fallback.py tests/test_cursor_framebuffer.py tests/test_cursor_overlay.py -q -s
```

Initial focused receipt: **5 passed in 25.51s**. Final run added `tests/test_integration.py` to that command and strengthened overlay target-crop/native-pointer assertions: **14 passed in 34.99s**, saved verbatim in [`focused-test-receipt.txt`](capture-evidence/focused-test-receipt.txt). The script can also be run directly:

```sh
PYTHONPATH=../hermes-realms-host-sdk:$PWD \
../hermes-realms-host-sdk/.venv/bin/python -W error \
  scripts/probe_cursor_overlay.py docs/capture-evidence
```

Full warning-strict product-suite command:

```sh
REALMS_TEST_CUA_BINARY=$PWD/vendor/cua-driver \
REALMS_TEST_CUA_ARCHIVE=$PWD/../_cua_research/realms-spike/vendor/cua.tar.gz \
PYTHONPATH=../hermes-realms-host-sdk:$PWD \
../hermes-realms-host-sdk/.venv/bin/python -W error -m pytest \
  -o asyncio_default_fixture_loop_scope=function tests/ -q
```

Receipt: **63 passed, 1 skipped, 1 failed in 74.97s**. The failure is the already documented `tests/test_integration_cua.py` unclosed `TextIOWrapper`/`PytestUnraisableExceptionWarning`; it is outside this change's ownership and was not hidden or fixed in another agent's files. The skipped legacy TCP fixture is not the product Unix VNC lane.

Each cursor test/probe stops its owned manager in `finally` and asserts empty registry, absent runtime, absent cgroup, and dead recorded process identities. Screenshot API tests stop both owners and verify removed runtimes. No current app/realm was stopped to perform this work.
