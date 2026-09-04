# Agent Realms — execution verification

Verified on Linux/Omarchy with real labwc, WayVNC, GTK, Cua, a rebuilt Hermes DEV app and its isolated backend. Receipts below distinguish live execution from unit tests. No production desktop/profile installation was performed. Home-directory names in archived text are redacted as `<user-home>`; credentials and viewer capabilities are not archived.

## Final automated gates

| Lane | Actual result | Receipt |
|---|---|---|
| Complete product suite, canonical runner, `-W error`, no retries | **112 passed, 0 failed, 5 skipped**, source hashes unchanged throughout | [output](e2e/product-tests.txt), [source hashes](e2e/product-tests.json) |
| Explicit real pinned-driver lane | **5 passed**, no skips | [output](e2e/driver-tests.txt), [result](e2e/driver-tests.json) |
| All changed host Python test files, canonical runner, `-W error`, no retries | **101 passed, 0 failed**, source hashes unchanged | [output](e2e/host-tests.txt), [source hashes](e2e/host-tests.json) |
| Changed desktop tests and renderer/Electron typechecking | **42 passed**, typechecks exited 0 | [receipt](e2e/desktop-tests.json) |
| Independent final product review | **PASS**, no blocking security or logic findings; independent real-process lane **54 passed** | [review](e2e/product-review.json) |
| Independent final host review | **PASS**, no blocking security or logic findings; focused lanes **224 passed**, 4 Windows-only skips | [review](e2e/host-review.json) |
| Installed wheel outside source tree | Start, real PNG capture, packaged noVNC HTTP asset, stop and empty registry/runtime checks passed | See packaging section below |

The five canonical product skips are environment-gated. The explicit driver lane reruns the containment/installation/repair files and covers four of those skipped cases. The remaining skip is the historical manually launched TCP WayVNC fixture. The product uses private Unix WayVNC and has separately passing real Unix transport and browser tests. Do not add overlapping lane totals together.

The full upstream Hermes repository suite and native Windows/macOS behavior were not run. Existing upstream process-registry fixtures have unclosed-pipe warnings under `-W error`; their normal-policy lane passed. These were not suppressed or counted as clean warning-strict tests. The newly added/changed host tests are warning-strict green.

## Actual development-app acceptance

The app was built from the companion checkout, Electron was explicitly rebundled with `--dev`, and the app/backend ran inside a separate private desktop with isolated `HOME`, `HERMES_HOME`, XDG directories and Electron user-data. DEV CDP was 9444 and Vite was 5176; no production app was restarted. Real model turns used the normal registered tool pipeline, not synthesized responses.

- **Two sessions:** real model A launched and operated GTK fixture A; model B independently launched and operated fixture B. Readback showed `alpha-final`/one click and `beta-final`/one click on separate runtimes. [Readback](e2e/dual-model.json).
- **Status and Watch:** the real app displayed the compact Realm badge, actual foreign-toplevel count, status row, Watch and Pop out. The active preview was visually checked from the actual compositor framebuffer after a nonblank RFB frame arrived. [Screenshot](e2e/watch.png).
- **Takeover:** a human-controlled viewer blocked a real model input attempt with `policy_denied`, `retryable=false`, and no escalation. Human input incremented only A. Returning control allowed the agent to click again; returning to view-only blocked viewer input. B remained unchanged. [Viewer readback](e2e/viewer-controls.json).
- **Guest isolation:** real embedded/native viewers exposed neither Node nor the Hermes preload bridge. Notification permission was denied. Native Electron permission and overlapping-window lifecycle regressions also passed.
- **Resize:** the real `/realm size 1280x720` RPC succeeded; private screenshot dimensions were checked. The full suite also exercises live resize against real compositor captures.
- **Host mode:** real `/realm off` followed by a normal model terminal turn restored the DEV app's host environment and removed the session's previous runtime. This deliberately targeted the isolated DEV host, not the user's physical desktop. [Readback](e2e/host-mode.json).
- **Resume/compression:** actual session resume and compression were exercised. Compression returned `status=compressed`; token counts decreased from 34933 to 31365. The initial 30-second RPC timed out while work continued; a longer awaited request supplied the receipt. [Reduced receipt](e2e/compression.json). Ownership/alias behavior is also covered through native plugin discovery and real session lifecycle tests.
- **Fresh final backend:** `/realm on` in a lazy session returned no running realms, as intended. Its first ordinary terminal tool then created the private realm. A deliberately readonly `WAYLAND_DISPLAY` in that private home's Bash startup caused a real model background command to exit **126** before writing its marker. The process tool returned the readonly diagnostic; no retry or GUI input was performed. [Actual tool result](e2e/fresh-dev-routing.json).

## Locked-screen product test

The completed plugin path was tested under the real Omarchy lock for **67.1 seconds**, using three timed samples. Both private desktops continued rendering with distinct screenshot hashes. Realm A accepted three clicks and `-locked0-locked32-locked65`; realm B remained at zero clicks and empty text. Both runtimes were subsequently removed with no owned survivors.

[Samples and hashes](e2e/lock-samples.json) · [Cleanup](e2e/lock-cleanup.json)

The earlier Phase 0 also rendered the actual Codemux dev build in a private GLES realm and verified private input. Lock state was obtained from `omarchy-shell lock isLocked`, not inferred from a process name. Sleep inhibition is separate: only sleep is inhibited, never idle or lock.

## Omarchy scratchpad runtime

Real Hyprland 0.56.2, installed Omarchy helpers and unchanged SDK Electron viewers were exercised with private WayVNC sources:

- Distinct, silent placement in `special:hermes-1` and `special:hermes-2`.
- Original focus retained; activation attempts suppressed.
- Real virtual-keyboard latest/cycle/numbered toggles, reload reconstruction and last-window cleanup passed.
- Cleanup verified 53 owned processes with zero survivors, inactive scopes, removed runtimes/sockets and a closed viewer listener.

[Result](e2e/omarchy-runtime.json) · [Cleanup](e2e/omarchy-cleanup.json)

**Qualification:** Aquamarine's nested Wayland output did not appear, so this used a real Hyprland **headless output**, not successful nested-Wayland presentation. An early noop-libseat experiment opened physical input devices; it was stopped before test input and replaced with private `/dev` isolation. No live Hyprland configuration was edited.

## Final cleanup

After the fresh-backend routing test, both remaining test desktops were stopped. Their owned processes were checked by PID/start time, runtimes were absent, systemd scopes were inactive and both registries were empty. Temporary credential copies created for the DEV rig were removed; source credentials were not edited.

[Final scope readback](e2e/final-cleanup.json) · [Temporary-copy cleanup](e2e/temporary-credential-cleanup.json)

Earlier Phase 0 probes were also stopped with their exact runtime ownership checked. Test fixtures/evidence remain on disk; the DEV app and its Vite/backend processes are not left running.

## Reproduce

From the companion Hermes checkout:

```sh
scripts/run_tests.sh /absolute/path/to/hermes-realms/tests --file-retries 0 -W error -j 2
```

For the explicit driver lane, run the three `test_driver_containment.py`, `test_driver_install.py`, and `test_driver_repair.py` unittest modules with `REALMS_TEST_CUA_BINARY` pointing to the verified 0.23.2 executable and `REALMS_TEST_CUA_ARCHIVE` pointing to its verified release archive. The canonical host runner intentionally strips these opt-in environment variables.

For real Chromium/noVNC/GTK:

```sh
cd /absolute/path/to/hermes-realms
REALMS_PLAYWRIGHT_MODULE=/absolute/path/to/node_modules/playwright \
  /absolute/path/to/hermes-python -W error scripts/test_viewer.py
```

That browser lane returned `connected=true`, `viewOnly=true`, `takeoverInput=true`, `returnedToAgent=true`, `errors=[]` and verified runtime cleanup. Omarchy's complete isolated harness and original raw DEV receipts are retained locally under `../_cua_research/realms-omarchy-runtime/` and `../_cua_research/realms-dev-e2e/`; those directories are not required runtime dependencies.

## Packaging and compatibility

`uv build` produced a source distribution and `hermes_realms-0.1.0-py3-none-any.whl`. The wheel was installed into a separate Python 3.13 environment and exercised from outside the source tree; imports were asserted to come from that environment. Real realm startup, a 6121-byte PNG, a successful HTTP read of bundled `rfb.js`, and complete stop/registry/runtime cleanup passed. The main integrated test environment uses Python 3.11. The complete directory/source distribution is needed for native plugin registration; the wheel alone supplies the standalone CLI/library, not a stock-Hermes plugin installer.

The companion generic host changes are required. This is **not a drop-in plugin for unmodified upstream Hermes**. The plugin never patches installed Hermes source. See the repository README for installation, explicit profile targeting and opt-in Omarchy configuration.

## Security and known boundaries

- Ordinary terminal commands retain development filesystem/network access. **GUI/process separation is not a hostile-code sandbox.** Trusted Cua separately runs under bubblewrap with private runtime/device/socket exposure.
- Raw WayVNC is a mode-0600 Unix socket beneath a mode-0700 directory, replacing the plan's unauthenticated loopback TCP. The HTTP/WebSocket viewer is independently authenticated, generation-bound, origin-checked and revocable.
- Standard/bounded/unrestricted permission behavior is preserved. A deny-input approval policy is tested; there is no invented `approvals.mode: read-only` setting. Real bounded-policy and takeover guards are covered by `test_integration_permissions.py`.
- Remote Watch/Pop out is rejected before capability issuance; there is no remote viewer tunnel in v1.
- Filesystem deletion failure is surfaced and leaves durable `cleanup_failed` ownership for retry; it is never reported as successful deletion and never repaired with blanket chmod.
- Cursor receipts distinguish verified native fallback from fresh-daemon themed overlays. See [capture-verification.md](capture-verification.md) and [windows-verification.md](windows-verification.md) for historical component evidence; their old intermediate warning/pending notes are superseded by the final lanes above.
- The host review retains a non-blocking follow-up for same-profile concurrent identity-observer notification/reconciliation coverage. Consumers remain fail-closed; no authorization bypass was found.
