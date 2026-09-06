# Agent Realms — verification scope

This is a reviewed summary, **not raw execution output**. Original receipts, screenshots, session transcripts and source hashes were preserved privately before publication cleanup. They are intentionally absent from source distributions. Do not substitute example output for a real run, or infer that deleting files removes them from Git history.

## Latest core-workflow E2E (2026-09-06)

The privately audited run used product revision `e92c3bf6290fc6a075ca1676cda5fbea3173d220` and a personal-fork Hermes DEV renderer/backend containing both public [host-extension PR commits](../integration/README.md). It was not a clean upstream-PR-only build. The renderer, backend, homes, profiles and user-data were isolated. No product source changes were needed.

- Two real model sessions copied, compiled, launched and operated **supplied GTK scaffolds** in independent realms. They did not independently author the apps. Real GTK callbacks and independent state/transcript/cgroup checks established the effects.
- Watch displayed a real noVNC frame. View-only input was blocked; automation through the actual viewer delivered takeover input to one app without changing the other.
- A real agent input attempt during takeover returned `policy_denied`, `retryable=false`. No input bypass was attempted. Returning control restored view-only, and a later agent click succeeded.
- Pop out opened a distinct isolated page target, view-only. Node, process and the Hermes host bridge were unavailable in the guest. This is not proof of native compositor placement or window bounds.
- Closing the first session removed its runtime, scope and owned processes without disturbing the second. Closing the second and stopping the owned DEV tree left no owned realm/DEV survivors. The authorized temporary model-login copy was removed.

**Not proven in that run:** AX/app-window targeting (discovery was empty; full-desktop vision and coordinates were used), optional live Hyprland scratchpad mapping, physical-host locking, remote viewers, non-Linux platforms, or arbitrary shell/filesystem sandboxing. Viewer input was automated through the real viewer, not a physical human mouse. Private evidence was inspected for this summary, not re-executed during publication cleanup.

## Historical component results

These results refer to older development runs and their privately retained receipts. They are not new acceptance tests of the public host PR or the cleanup diff. Do not add overlapping lane totals.

| Recorded lane | Recorded result |
|---|---|
| Product suite, canonical host runner, `-W error`, no retries | 112 passed, 0 failed, 5 skipped |
| Explicit pinned-driver lane | 5 passed, no skips |
| Changed host Python files, warning-strict, no retries | 101 passed, 0 failed |
| Changed desktop host tests and typechecks | 42 passed; typechecks exited 0 |
| Independent product review lane | 54 passed; review reported no blocking findings |
| Independent host review lane | 224 passed, 4 Windows-only skips; no blocking findings reported |

Four environment-gated product skips were covered by the explicit driver lane. The remaining legacy manually launched TCP WayVNC fixture was skipped, not passed; the product uses separately tested private Unix transport. The full upstream repository suite and native Windows/macOS behavior were not run. An upstream normal-warning-policy pass is not a warning-strict pass.

Historical installed-wheel testing reported real startup, PNG capture, an HTTP read of packaged noVNC and complete teardown outside the source tree. Historical development-app checks also covered host-route restoration, resume/compression, resize and a deliberately failed background shell command; the failure must not be called a successful tool execution.

Historical locked-screen testing reported continued rendering/private input for 67.1 seconds. Historical scratchpad testing reported silent routing, focus preservation, toggles and cleanup on a separate headless Hyprland output, **not** successful nested-Wayland presentation. An early experiment exposing physical input devices was stopped before test input. These do not establish current-build host-lock or native mapping acceptance. The old prose stated 53 cleaned processes while its archived scratchpad receipt recorded 41; the larger total is not repeated as verified.

Earlier component receipts and caveats are summarized in [capture](capture-verification.md), [manager](phase1-verification.md), [window/remote guards](windows-verification.md) and [desktop](../desktop/verification.md).

## Reproduce safely

Set `PRODUCT` to this checkout and `HOST` to an explicitly chosen compatible Hermes development checkout. Use the host's canonical runner for integration files; it supplies the expected Python environment and changes its working directory to the host checkout. Child Python processes must explicitly use the product cwd.

```sh
cd "$HOST"
scripts/run_tests.sh "$PRODUCT/tests" --file-retries 0 -W error -j 2
```

**Do not run the full suite on an unapproved live host.** Many tests launch real systemd scopes, labwc, Xwayland, D-Bus and input-capable services. They require a disposable, explicitly scoped Linux environment even when test names lack an E2E marker. Skips are not acceptance passes.

For the explicit installer/repair lane, supply `REALMS_TEST_CUA_ARCHIVE` with the exact pinned archive. Containment additionally requires `REALMS_TEST_CUA_BINARY`; the canonical runner strips these opt-in variables. Never remount a host filesystem to test noexec; the repair test uses a private user/mount namespace.

For the real browser lane, only in an authorized isolated Linux environment:

```sh
cd "$PRODUCT"
REALMS_PLAYWRIGHT_MODULE=/absolute/path/to/node_modules/playwright \
  /absolute/path/to/hermes-python -W error scripts/test_viewer.py
```

Capture evidence belongs in a private directory outside the checkout. Set `REALMS_TEST_CAPTURE_EVIDENCE` explicitly there if collecting cursor-test images. See [desktop test setup](../desktop/README.md) for standalone JS dependencies.

For a packaging-only check (no compositor or host input), run `python -m pytest tests/test_release_packaging.py -q` from the checkout or unpacked sdist. It requires pytest in the selected Python environment and `uv` on PATH, plus network access or a populated cache for the declared build backend. Run it from a clean source tree: its temporary source-copy exclusions are a test convenience, not a general privacy sandbox.

## Publication-cleanup checks (2026-09-06)

These checks were executed in an isolated product worktree with private build/test dependencies, without starting a compositor or touching a production profile:

- `python -W error -m pytest tests/test_viewer_auth.py tests/test_viewer_protocol.py tests/test_shared_viewer.py tests/test_viewer_forwarding.py -q`: **13 passed, 10 subtests passed in 0.53s**. This selected standalone lane uses real disposable Unix sinks/cross-process authority where applicable; it is not the full host integration suite.
- `node --experimental-vm-modules --test tests/desktop/plugin.test.mjs` with the documented dependency override: **8 passed, 0 failed, 0 skipped**. Before cleanup, the worktree run failed to resolve `jsdom` through its hardcoded sibling SDK path. Only the test dependency resolver changed; no runtime code changed. The expected experimental VM-module warning remained visible.
- `lua tests/omarchy/realms_test.lua`: both documented PASS lines. No live compositor parser/reload or scratchpad mapping was run.
- `uv build`: source distribution and wheel built. Archive inventories found **0 raw-evidence/patch entries** in either cleaned artifact; all bundled third-party notices remained. Restoring four exact private original evidence/patch files temporarily and rebuilding the sdist still excluded all four.
- The cleaned wheel was installed in a disposable Python 3.11 environment and imported with `-I` outside the source tree. Viewer assets and `hermes-realm --help` passed. Native installed realm startup was deliberately not rerun.
- Gitleaks 8.30.1 scanned a retained-source snapshot: no findings. Its full reachable-history run flagged four generic-key candidates that were independently verified to equal tracked-file SHA-256 hashes, not credentials. **Neither result is a guarantee that the Git history is safe to publish**: the existing root commit still contains raw development evidence and identifying commit metadata.

The initial audit preserved private receipts with exact commands, output, inventories and hashes. It made no commits, pushes, history rewrites or visibility changes. Its licensing and instruction-file approval gates were resolved during the follow-up below.

## Cleanup follow-up (2026-09-06)

- The owner selected MIT for original project code. `LICENSE`, SPDX package metadata and the license included in the wheel agree; third-party notices are unchanged.
- The approved `AGENTS.md` update replaces private research/sibling-checkout references with repository documentation, retains runtime safety rules and keeps raw execution evidence outside Git. `CLAUDE.md` contains only `@AGENTS.md`.
- Independent history review found no confirmed credential leak or private personal conversation. Earlier secret-scanner findings were independently matched source SHA-256 values, and capability candidates were fixed test fixtures. The owner approved retaining the commit email; the reviewed runtime IDs, hardware details and engineering test screenshot did not require a history rewrite. Ordinary history is retained, including those older test artifacts, even though new source archives exclude them. This is a scoped privacy assessment, not a proof that scanners detect every private fact.
- The release-packaging regression test first failed because `CLAUDE.md` was absent. After cleanup it built both artifacts and verified the import file, MIT license/metadata, third-party notices and private-evidence exclusions. A separate run from the actual unpacked sdist exposed a Git-dependent test staging bug; after removing that dependency, the same test passed outside Git as well.
- The focused warning-strict Python lane including this packaging test passed **14 tests and 10 subtests**. Desktop tests passed **8 with no failures or skips**; Lua tests passed without live configuration changes. An independently installed wheel passed MIT metadata, viewer imports/assets and CLI help checks. Fresh source and artifact scans reported no leaks; first-party Markdown links and `git diff --check` passed.

## Public-PR-only integration (2026-09-06)

A separate DEV renderer and backend were built from clean public host revision `fa6e9ac63155780dc368bf02f86c34413d441788`, with fresh scoped Python/npm dependencies and isolated homes/user-data. Host source had no local changes. Runtime hashes matched the unchanged Realms implementation; no model credentials were used.

- Two real private GTK desktops were created. Deterministic host terminal/Cua calls exercised app input; they were not model-generated tool calls.
- The actual DEV Realms contribution exposed Watch. Its real noVNC guest rendered the private app, blocked view-only input, accepted takeover input, and denied a host Cua input attempt with `policy_denied`. Returning control allowed Cua input again. Guest Node/require/host-bridge globals were unavailable.
- Pop out opened a separate restricted target, but its retained observation was Disconnected; a live Pop out framebuffer was **not verified in this run**. The earlier personal-fork model run's live Pop out result does not substitute for that missing check.
- Closing one session removed only its realm while the other remained live. Closing the second and stopping the owned DEV processes left no owned survivors; the test listener ports were closed.
- The final canonical product lane, warning-strict with no file retries, passed **113 tests, 0 failed, 5 skipped**. Its first concurrent run caught the then-unfinished `CLAUDE.md` packaging change; the final full rerun passed rather than suppressing that failure.
- The changed public-host Python lane passed **214 tests, 0 failed, 2 Windows-only skips**. These totals overlap other lanes and must not be added together as unique test counts.

This closes the tested public-PR source-compatibility gap, not the prerequisite upstream merge. It does not establish a credentialed model conversation on that exact host, native Hyprland placement, physical-host lock behavior, remote viewers, AX targeting, or hostile-code sandboxing. The earlier model-driven run and this deterministic integration run remain distinct evidence. No repository visibility change is part of cleanup.

## Security boundaries

GUI separation is **not a hostile-code sandbox**. Ordinary realm commands retain development filesystem/network access. The trusted Cua driver separately uses private namespaces/device/socket exposure. Raw WayVNC uses a mode-0600 Unix socket under a mode-0700 runtime. Browser capabilities are authenticated, generation-bound, origin-checked and revocable; do not put real tickets in docs, logs or issue reports. Remote Watch/Pop out remains unsupported. Standard/bounded/unrestricted permission policy remains Hermes's responsibility.
