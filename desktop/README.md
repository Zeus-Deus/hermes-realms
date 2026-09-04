# Realms desktop half

`plugin.js` is an uncompiled ESM plugin, loaded from the unified `hermes-realms` package. Requires the generic session contribution / scoped REST / native viewer SDK described in `../../_cua_research/realms-desktop-api.md`. Enable the desktop half in the DEV app's Settings → Plugins; unified desktop halves are opt-in independently of backend enablement.

- Status stack: effective Realm/Host/Ask mode, live window count, takeover indicator, Watch and Pop out. Counts come directly from the private labwc `zwlr_foreign_toplevel_manager_v1` protocol, not processes or app PIDs. The read-only snapshot uses a one-second TTL, a bounded 128-entry per-service cache and a one-second protocol timeout. Unavailable/failed enumeration displays `windows unknown`, not zero. Polling does not renew the realm's idle lease. Empty realms indicate lazy startup, not failure.
- Tile and sidebar badges use the same owner-scoped query cache. No focused-session atoms are read.
- Every request scopes connection + profile, with runtime/stored owner IDs. Both IDs, when supplied, must match the response's owner fields. List contributions support stored-only ownership.
- Native React Query polling (5 seconds; one retry) works without plugin sockets, including OAuth remote routing. Failure removes stale live actions and provides Retry. Error messages never echo native/REST exceptions which may contain ticket URLs.
- Each explicit **local** Watch/Pop out click requests a fresh ticket. URLs are never persisted in plugin storage or query cache. Watch uses `host.openPreview`; Pop out uses `ctx.os.openViewer`. Neither direct Electron access nor DOM manipulation is used.
- **Remote Watch and Pop out are unsupported**, including SSH-forwarded REST connections and OAuth remotes. Status polling remains supported. The UI disables both actions and explains that a dedicated viewer tunnel is missing; action handlers also reject before issuing a REST ticket. Only the host's exact `connectionId === 'local'` contract (`@hermes/shared` `LOCAL_CONNECTION_ID`) permits viewer actions. To view a realm today, run Hermes locally on the realm machine and use that local connection. Forwarding only REST over SSH does not forward the separately allocated viewer HTTP/WebSocket listener; no automatic remote viewer tunnel is implemented.
- Native viewer IDs are `realm-` + SHA-256 of JSON `[connectionId, profile, realmId]`. Runtime/stored IDs are not part of the native ID, so a renewed ticket/session remap does not duplicate a realm viewer.
- Viewer content is remote/unprivileged; takeover belongs to the viewer and server, not this row. A live `controlled` flag displays `user control`.

## Backend contract

See `../../_cua_research/realms-plugin-ui-api.md` for exact schemas. Relative routes are `GET /realms?runtime_session_id=…&stored_session_id=…` and `POST /realms/{id}/watch`; native SDK supplies `/api/plugins/hermes-realms`. Remote viewer URLs must be client-reachable. A backend loopback URL is not a remote tunnel.

## Scoped tests

From repository root (using installed dependencies in the sibling host SDK checkout, read-only):

```sh
node --experimental-vm-modules --test tests/desktop/plugin.test.mjs
lua tests/omarchy/realms_test.lua
Hyprland --verify-config -c "$PWD/tests/omarchy/verify.lua"
```

The JS harness executes the external ESM with real React, React DOM, jsdom, and React Query. Native SDK primitives/actions are the recording boundary; UI model data are explicit test fixtures, not claimed backend results. A separate polling test uses an actual loopback HTTP failure service and real five-second polling/retry. See `verification.md` for what has and has not been proven. No test edits or reloads production Hermes/Hyprland configuration.
