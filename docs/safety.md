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

### VM workspaces that need recovery
A stopped VM is `recovery-required` when its retained files no longer match the receipt taken when it was created: a disk, firmware or spec file was replaced, the SSH pin is missing (a launch that died before enrolling), or the base it depends on changed. Plain `vm delete` refuses those, because it only deletes what it can verify. Receipts bind files by inode and require each file to be on the same filesystem as its directory; they do not record the kernel's device number, which btrfs and device-mapper volumes renumber across reboots. Older receipts that did record it are compared without it, and a record that verifies again returns to `stopped` on the next `list`.

To remove a workspace that still does not verify, the owner confirms a discard:

```sh
hermes realms vm delete ID --session-id OWNER --discard
hermes realms vm prune --all --recovery-only --dry-run   # list what would be deleted and kept
hermes realms vm prune --all --recovery-only             # same list, then one typed confirmation
```

`--recovery-only` limits any selection to workspaces that can never be resumed (recovery-required, or an interrupted deletion); healthy stopped workspaces are kept, and so is a recovery-required workspace whose files verify again, which the next `list` returns to `stopped`. Without it, `prune --all` also discards healthy stopped workspaces of every session. `prune` also accepts `--older-than DAYS` (a finite number above zero) or exact `--id ID` selections, and `--keep ID` to exclude a VM whatever its state. A record whose workspace directory is already gone is discarded the same way, removing only the record. A discard is refused unless the record is stopped or recovery-required with confirmed retirement (`cleanup_required` false), the VM unit, its lifetime unit and its sleep guard are all inactive, and no process of this user holds any file under the workspace or runtime directory open. Running and starting VMs are listed as kept and never touched; the review itself reconciles, stops and starts nothing. The confirmation binds the record publication, compute state and every top-level entry of the workspace (name, inode, size, mtime); a workspace with a link, directory or other non-file entry is refused. Any change between review and confirmation, including a start/stop cycle, cancels the whole prune. Each directory is removed by first renaming it to a hidden `.NAME.deleting` tombstone beside it and checking the tombstone is the reviewed directory, so a directory swapped in after the last check is renamed back and kept. An interrupted discard can only be finished by another discard, which accepts entries already removed but not new or changed ones. If a prune stops part way, the error names the workspaces already deleted; the rest need a fresh review.

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

### One decision for every chat with no recorded Realm use
Most earlier conversations never used a Realm. Reviewing them one by one adds
nothing, so the user can release all of them with **one explicit decision**.
The rule stays the same: a user decision is still required, and missing metadata
is still not taken as freshness. The decision can be made in any of these ways:

- **Enable-time setup.** The consented plugin setup step lists `N earlier chats
  with no Realm use will run on your normal desktop (agent outside the Realm;
  Realm available as a tool). M chats that used a Realm keep asking for /realm
  review.` Accepting that consent is the decision. The reviewed scope is part of
  the setup revision. If the set of chats changes between review and run, the
  setup refuses and the consent is requested again.
- **`/realm review unused`** (also `--all-unused`), typed by the user. Typing the
  command is the decision, so there is no further prompt. The agent's `realm`
  tool cannot issue it.
- **`hermes realms review unused`** from the profile's administrator. Use
  `--dry-run` to count without changing anything.

"No recorded Realm use" means the same criteria that decide which conversations
a failed load pauses: no stored `realm` mode, no chosen or requested kind, no
setup intent, and no `r-`/`v-` resource record naming the conversation. It also
requires a recognized pre-contract permission (NULL or `legacy-held-v0`) and no
attached old execution lease. Each released conversation gets the same ledger
change as `/realm review`: contract `optional-targets-v1`, an unset mode stored
as `ask`, an explicit choice (including Disable) preserved, and a receipt bound
to this profile and owner. The receipt records the decision's scope
(`no-recorded-realm-use`), its digest and its provenance (`setup-consent`,
`slash-command` or `cli`). It is never labelled fresh. Conversations with
recorded Realm use stay held for individual `/realm review`. The operation is
idempotent.

The decision is stored for this profile, so an earlier conversation Realms
first sees afterwards gets the same treatment when it is bound, under the same
criteria (receipt provenance `remembered-decision`). An example is a chat from
before the plugin was installed that is reopened later. A store copied from
another profile does not carry the decision.

The host's setup contract has one yes/no consent per revision. Declining it
leaves the plugin disabled, and a disabled plugin holds no conversation.
Enabling while keeping every earlier chat held is therefore not offered at
enable time. Accepting also installs the driver. The bulk release has no
separate undo. With nothing left to decide, setup reports ready and asks
nothing more. A profile that already had Realms enabled before this change is
not asked again automatically; use `/realm review unused` there.

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

## When Realms cannot load
If the plugin is enabled but its runtime fails to import or start (for
example a Python dependency missing from the running backend), it still
registers its execution middleware so a conversation inside a Realm never
silently runs on the host. The middleware reads the ownership store without
writing it. Conversations it records as using a Realm (stored `realm` mode,
kind, requested kind, setup intent, or a resource record), and earlier
conversations still held for permission review, are paused with
`realms_load_failed` and the underlying error; all other conversations run
normally, as does a conversation the store has never seen (it cannot be in a
Realm). A call without a conversation identity is paused while any
conversation uses a Realm or is held. If the ownership store itself cannot be read, no
conversation can be told apart and every tool is paused with
`legacy_permission_review_required`, as before. The `realm` tool and `/realm`
commands report the same error.

For modern sessions, stop owned compute before disabling the runtime and restarting its backend. Disabling the UI alone changes no backend authority. Retained profile data and the explicitly installed driver are not implicitly deleted.

## Verification scope
Ordinary tests exercise registration, generic target routing, ownership failures, retention, conflict-safe transfer and UI behavior with isolated profiles. Passing those tests is not proof of a running app or guest.

Native tests are separately marked `integration`. Run them only in an explicitly approved disposable environment with the actual prerequisites; see [running the tests](testing.md).

Some setup cases download the pinned driver. Packaging must use the supported build toolchain; imports, editable fixtures or a source build do not establish packaged native acceptance. Full Desktop consent/retry/source-switch, both target kinds, real model discovery, human takeover, locked-screen behavior and final-candidate continuity require their own native evidence. Do not infer those results from screenshots or mock UI tests, and do not call this proposal release-ready while its qualification gates remain open.

## Known limitations

- **Delegated sub-agents share the session's Realm.** Hermes names the delegating session in the sub-agent's session identity (`parent_session_id`, set only by delegation; compression and branch lineage never count). Realms binds the sub-agent's aliases to the parent's owner, so it has exactly the parent's permission state, mode and kind: a pending `/realm review`, `/realm off` or legacy hold applies to the sub-agent too. A sub-agent that doesn't need a desktop runs ordinary tools on the host. One that needs a desktop joins the session's Realm, or starts it if there is none; the parent's own tools stay on the host until it chooses a Realm target. A sub-agent whose parent Realms never saw is refused rather than given its own authority.
- **Separate Realm is opt-in.** `realm(action="on", separate=true)` (sub-agents only) moves that sub-agent to its own owner: a clean Realm with the parent's routing mode, available only when the parent itself holds optional-target permission. It is never more than the parent, and it follows the parent live: a later `/realm off`, pending `/realm review` or legacy hold on the parent refuses the separate Realm's targets too. Like the parent's own `/realm off`, this releases agent use but does not stop the desktop; it stops with the session.
- **Lifetime is the session's.** A sub-agent finishing does not stop anything. The shared Realm and any separate sub-agent Realms stop when the delegating session is finalized, or idle out after `plugins.realms.idle_ttl` (default 1800 seconds).
- **Shared screen.** Parallel sub-agents in one Realm share its desktop and input. There is no per-agent lock; the parent coordinates its sub-agents, as on a normal desktop.
- **Shared control.** The session Realm has one owner, so its controls are shared too. Any sub-agent sharing it can stop it (`realm stop`) or switch its kind (`/realm on omarchy`, which stops the current desktop first) for the parent and every sibling. A sub-agent turning a Realm on sets the session's mode to Realm. None of this gives a sub-agent more than its parent.
- **`realms_session_unbound`.** A tool call from a conversation Realms has no record of (for example when an older Hermes skipped `on_session_start` for parallel sub-agents) is paused with this code instead of the earlier-permission message. Nothing is loosened: it is refused exactly as before.
- **Physical screen lock:** not tested.
