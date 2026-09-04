# Desktop / Omarchy verification receipts

Scope: external `desktop/`, optional `omarchy/`, and their scoped tests only. No commits, no Python backend edits, no installed app or user Hyprland configuration changes.

## Test-first slices executed

Each implementation slice had an executed failing test followed by passing execution:

1. Session query owner/scoped native polling: `realmQueryOptions` missing → implemented.
2. Registered status/tile/list components and mode display: `default.register` missing → implemented.
3. Fresh-ticket owner-scoped native viewer actions: `openRealmViewer` missing → implemented.
4. Watch click wiring and failure UX: actual click produced zero native calls → implemented handlers and safe error feedback.
5. Malformed supplied owner IDs / stale badges: invalid identity incorrectly enabled query → reject partial identity fallback; show unavailable instead of stale live badge.
6. Lua scratchpads: install helper missing → implemented silent routing/latest/cycle/index/cleanup.
7. Lua reload recovery: `config.reloaded` hook missing → implemented restoring existing indexes without moving/revealing the viewer.

Final desktop command:

```sh
node --experimental-vm-modules --test tests/desktop/plugin.test.mjs
```

Actual result: **6 tests, 6 pass, 0 fail**, including real React/React DOM/jsdom rendering, real React Query caching, owner isolation, buttons, native-boundary recording, stale-error behavior, and a real loopback HTTP failure service reached by the native five-second polling interval plus bounded retry. The live polling case completed in approximately six seconds. Node prints its expected `VM Modules is an experimental feature` notice; no React warnings were emitted. Success model data and native OS actions are explicit unit fixtures, **not** a claim of successful full backend/desktop E2E.

Omarchy command:

```sh
lua tests/omarchy/realms_test.lua
```

Actual output:

```text
PASS: silent native-title routing, latest/cycle/index, closed cleanup, malformed identity rejection
PASS: reload restores viewer indexes, no focus or move on reload
```

Native installed parser (read-only packaged Omarchy helper):

```sh
Hyprland --verify-config -c "$PWD/tests/omarchy/verify.lua"
```

Actual output ends `======== Config parsing result:` then **`config ok`** on Hyprland **0.56.2**. This is the real Hyprland parser, not a mocked Lua syntax check. It does not launch/reload the compositor or install bindings.

Read-only native Lua probes (no `hl.dispatch` execution):

- `hl.get_windows()[1]` field shape returned `address_format=true initial_title_type=string workspace_name_type=string`.
- `hl.dsp.window.move({window="address:0x1",workspace="special:hermes-test",follow=false})` constructed **HL.Dispatcher**.
- `hl.dsp.workspace.toggle_special("hermes-test")` constructed **HL.Dispatcher**.

SDK source verification: native identity is `Hermes Viewer [<plugin>/<id>] — <label>` in `hermes-realms-host-sdk/apps/desktop/electron/browser-windows.ts`. That implementation explicitly documents the unsupported per-window WM_CLASS/app_id limitation. Our rule uses the locked initial-title identity and does not rely on `hermes-realm-viewer` WM_CLASS.

## Remaining integration acceptance (parent owns)

- Align backend GET/POST routes with `../../_cua_research/realms-plugin-ui-api.md` and explicit owner fields.
- Load the unified plugin in the newly built DEV desktop against that real backend. Prove each tile/list/status row, Watch preview, binary noVNC stream, and passive native Pop out using real sessions.
- Prove live special workspace placement/focus preservation with DEV viewer windows in an explicitly authorized disposable compositor/config. The parser and recorded dispatcher tests are not that live focus proof.
- Verify client-reachable viewer URLs for remote connections; backend loopback alone cannot satisfy remote Watch.

Parent may incorporate this receipt into repository `docs/verification.md`; that shared documentation directory was outside this subagent's edit ownership.
