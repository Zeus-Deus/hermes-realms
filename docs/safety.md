# Safety, lifetime and controls

## Manual controls and lifetime

| Control | Current target behavior |
|---|---|
| `/realm on [realm\|omarchy]` | Select/re-enable the kind. Plain `on` preserves the chosen kind. Regular allocation is lazy until a targeted operation; VM selection currently starts/reuses the guest. Consented setup eagerly starts either kind. |
| `/realm status` | Inspect requested kind, readiness, live/stopped/recovery state and target diagnostics independently of CUA. |
| `/realm size WIDTHxHEIGHT` | Resize the selected live target. |
| `/realm watch` / Desktop Watch | Open a view-only viewer; observing does not grant input permission. |
| Desktop Pop out | Open the target in a separate viewer window, initially view-only. |
| `/realm repair` | Reset only the selected running target's CUA connection. |
| `/realm off` / Disable | Revoke agent target use and pending setup intent, retaining the running target, workspace and human viewing. Not an escape to host execution. |
| `/realm stop` / Stop | End owned compute while retaining modern workspace data. It does not disable future explicitly requested target use. |

An explicit Disable persists across backend restarts. The agent's `on` action cannot clear it, switch kinds around it, or revoke pending setup intent. The user can re-enable with `/realm on` or the existing desktop setup review. An unset session preference is different: the agent may select a target when needed, even when the profile default is `host`. Ordinary tools and human viewing remain available while disabled.

Save application changes before Stop: preserving workspace files/disks is not preserving unsaved application memory. Normal turn completion does not stop the target. Session finalization, owner loss or idle retirement end compute according to lifecycle policy while retaining modern workspaces. Restart reuses verified work with a fresh compute incarnation; missing receipts or unregistered work block silent replacement. Delete is a distinct destructive operation, not implied by Stop, Disable, Repair, viewer close or cleanup.

Watch/Pop out require a local connection unless an explicit viewer tunnel is available. Authenticated owner-scoped renewal keeps a live viewer authorized without reopening it. Do not persist or publish viewer tickets. Ordinary transport loss reconnects with bounded backoff in view-only mode; revoked authorization is terminal and needs a fresh Watch. Human-control interruption retains exclusion until explicit recovery/handback, and unrelated parent work can continue. Never use a shell capture/input utility to bypass that exclusion.

Viewing is not a guarantee of indefinite compute lifetime. A locked screen is also not a suspended or powered-off host; lock-independent operation needs native qualification on the intended setup.

## Explicit administrative Delete
Delete is interactive and separate from Stop. Inspect `hermes realms list` or `hermes realms vm list` in the intended profile to obtain the exact target ID and its `session_id`, then use the matching kind:

```sh
hermes realms delete ID --session-id OWNER
hermes realms vm delete ID --session-id OWNER
```

The command requires a terminal and displays a target-specific phrase to type exactly. It refuses running compute, a mismatched owner, and a changed workspace/compute/registry snapshot. A stop/start/stop cycle during confirmation invalidates the prompt even if the target ID is unchanged. Cancellation or noninteractive input deletes nothing; there is no force/yes flag or automatic Stop.

Confirmed deletion removes the selected retained workspace, not shared VM base data or another session's work. An interrupted `deleting` operation requires fresh inspection and confirmation before retrying. `--session-id` is an administrative selection check, not authenticated conversation identity or a security boundary against other programs running as the same user. These commands are not a model-tool Delete action or a `/realm delete` slash command.

## Legacy upgrade safety
Older regular Realms may keep HOME only under volatile `/run`, and their already-running guardians may hold destructive cleanup code even after source files change. Updating the files does not migrate those processes.

The candidate's manager/integration guards refuse unsafe legacy Start/Stop/finalize/unload and leave an export-required warning in status rather than triggering new destructive teardown or allocating replacement work. These guards are **not an upgrader or a shutdown veto**: they do not neutralize the old guardian, host termination, idle cleanup or reboot. Preserve and verify recoverable work before any upgrade/recovery/disposal that could end that old lifecycle.

For selected saved work from an older **regular Realm**, follow [Manual export and cold recovery](legacy-recovery.md) **before** upgrading or retiring the old runtime. The administrator verifies a durable external copy, retires the old Realm, restores into a new workspace and reviews conversation permissions separately. This is not automatic active-legacy handoff, whole-HOME or VM-disk migration. Complete cross-surface permission migration remains **not qualified**.

## Review earlier execution permissions
The ownership ledger distinguishes optional-target conversations from earlier
`realm`, `ask`, or unset permissions. Missing or unrecognized metadata is not
evidence of a fresh conversation. Protected conversations pause execution in
the existing tool-execution middleware, including file reads, browsers, Python,
delegation and deferred execution. Only nonexecuting discovery/clarification
and Realm status remain available to the agent. An explicitly stored legacy
`host` choice retains ordinary backend access, but cannot re-enable targets
without review. Manual Disable never converts a held conversation.

On a cold candidate runtime, use **Use optional targets…** in this conversation's
existing Realm status controls. The manual CLI/gateway equivalent is **`/realm
review`**. Both require a new explicit native decision, independently of YOLO,
approval-off or remembered tool grants. Cancel, unavailable consent transport,
changed owner/profile/backend, or changed review scope leaves permissions alone.
After acceptance, make a new explicit tool attempt; the held operation is not
replayed. Ordinary tools use the conversation's configured original backend,
not necessarily the Desktop client's machine. Physical-desktop authority is not
granted. Explicit mode/kind and Disable remain unchanged; an unset mode becomes
stored `ask` in the acceptance transaction so the reviewed unselected state
cannot change with a later profile default. Converted unset/ask target use
stays unselected until explicitly enabled.

This is permission conversion only: no prompt/history rewriting, mode reset,
target startup, guest adoption, export, Stop or Delete. It does not establish
data durability or authorize retirement of old compute. An attached old parent
execution lease refuses review. Before retiring an active legacy regular Realm,
export and verify its selected saved work using the manual procedure above;
permission acceptance does not perform that recovery. Freshness is
published by unseeded native session creation, classic CLI creation and `/new`,
and core-generated agent IDs. CLI `/resume` and `/branch` remain continuations;
caller-supplied IDs without trusted lifecycle provenance remain conservative
and may require review even when the caller intended a new session.

For modern sessions, stop owned compute before disabling the runtime and restarting its backend. Disabling the UI alone changes no backend authority. Retained profile data and the explicitly installed driver are not implicitly deleted.

## Verification scope
Ordinary tests exercise registration, generic target routing, ownership failures, retention, conflict-safe transfer and UI behavior with isolated profiles. Passing those tests is not proof of a running app or guest.

Native tests are separately marked `integration`. Run them only in an explicitly approved disposable environment with the actual prerequisites; see [running the tests](testing.md).

Some setup cases download the pinned driver. Packaging must use the supported build toolchain; imports, editable fixtures or a source build do not establish packaged native acceptance. Full Desktop consent/retry/source-switch, both target kinds, real model discovery, human takeover, locked-screen behavior and final-candidate continuity require their own native evidence. Do not infer those results from screenshots or mock UI tests, and do not call this proposal release-ready while its qualification gates remain open.

## Known limitations

- **Delegated sub-agents.** A sub-agent that requests a Realm target (`terminal(target="realm")` or `computer_use`) gets its own separate private Realm as a new owner. It does not inherit the parent's `/realm off` or chosen kind. The child's Realm is not stopped when the sub-agent finishes; it idles out after `plugins.realms.idle_ttl` (default 1800 seconds, 30 minutes) and its workspace is kept. A child cannot reach another owner's Realm, including its parent's.
- **Physical screen lock:** not tested.
