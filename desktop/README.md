# Desktop half

`plugin.js` is the checked-in, single-file plain ESM shipping entry. Installed-plugin discovery copies it; the runtime loader does **not** compile it or resolve sibling imports. No build is needed when installing this package. It is **off by default**, independently of the profile's Python activation. Enable it in Settings → Plugins → Realms only after preparing the owning backend; see [setup and dependency](../README.md).

## Authoring

Edit `plugin-source.js` and its sibling source modules, not generated `plugin.js`. From the repository root, use the existing lockfile-installed esbuild (no plugin-specific dependency installation):

```sh
node plugins/hermes-realms/desktop/build.mjs
node plugins/hermes-realms/desktop/build.mjs --check
```

Commit the generated entry with source changes. `--check` rebuilds in memory and fails on stale bytes without writing. Output is deterministic, without source maps, machine paths or timestamps. Only `@hermes/plugin-sdk`, `react`, and `react/jsx-runtime` remain external; the host supplies these modules. Named exports remain available to the component tests, which import the delivered `plugin.js`. Byte parity is a packaging gate, not behavioral coverage: verify installed discovery through the real Desktop loader as well as the component tests.

The session status stack and list/tile badges use each contribution's own runtime/stored identity, profile and connection. There is no global focused-session routing. APIs use the generic owner-scoped REST SDK; Watch uses the transient isolated-session preview and Pop out the native viewer SDK supplied by prerequisite #103690. Remote viewing is intentionally unsupported without a viewer tunnel.

Backend routes are `GET /realms?runtime_session_id=…&stored_session_id=…` and `POST /realms/{id}/watch`, namespaced by the SDK under `/api/plugins/hermes-realms`. Registration adds no poller or network request; mounted query components poll only after UI opt-in. See the host's `apps/desktop/src/contrib/bundled-realms.test.ts` for bundled loader/toggle coverage. Full native desktop validation remains a separate approved Linux DEV lane.
