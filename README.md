# Realms — optional private testing tools

Realms gives a conversation its own Linux test desktop without moving the agent's ordinary work into it. Normal terminal commands, files, cwd, research and GitHub operations keep the conversation's original configured backend and approvals. That backend may be remote; it does not automatically become the Desktop client's machine. There is nothing to exit after a private test.

Two session-owned targets are available:

- **Regular Realm** (`realm`): a private labwc/Wayland desktop with Xwayland support. It separates windows, pointer, clipboard and focus from the user's desktop, but shares the host kernel, filesystem and networking. **It is not a hostile-code sandbox.**
- **Omarchy VM** (`omarchy-vm`): an Omarchy QEMU/KVM guest with its own kernel, disk and Hyprland environment, for Omarchy/Quattro plugins, themes and system-level tests.

Enabling the plugin makes these capabilities available; ordinary coding does not allocate a desktop. An explicit target failure never falls back to the physical desktop. Normal host coding authority is not permission to drive that desktop.

## Host APIs and provenance

The plugin requires the generic consented native-plugin setup, session ownership, execution-context and Desktop viewer APIs described in [NousResearch/hermes-agent#103690](https://github.com/NousResearch/hermes-agent/pull/103690). Do not advertise compatibility with hosts lacking those interfaces. Optional terminal and computer-use routing uses generic target resolvers, not a Realms-specific override of the parent's environment.

The implementation is adopted from [hermes-realms](https://github.com/Zeus-Deus/hermes-realms) at `fbea3060e63621c1c73b7a6d5520dc6ff58e3227`. Runtime, Desktop, dashboard and skill live together here. Desktop builds discover this same `desktop/plugin.js`; there is no second implementation or dependency on a separately installed `realms` Python package. Original MIT attribution and all vendored noVNC/pako notices are preserved.

## Enable once, not once per task

The feature is **disabled by default** at discovery:

- Native discovery inventories `kind: standalone` without importing its Python until `plugins.enabled` includes `hermes-realms`. Explicit `plugins.disabled` wins.
- Its hooks, model tool, slash command, administrative CLI and skill are absent while disabled. Discovery does not download a driver or start a compositor, scope, viewer listener or Desktop contribution.
- Backend activation is per profile, without inheriting another profile's configuration. The dashboard API follows that activation gate.
- The Desktop half is a separate opt-in in Settings → Plugins (`defaultEnabled: false`). Enabling only the UI grants no backend authority.

The two controls affect different layers:

- **Desktop plugins → Realms** enables session viewing/management controls in the app.
- **Agent plugins → Applies to → hermes-realms** enables capabilities on the selected connected gateway profile.

Agent-plugin enablement reviews the pinned Cua release, checksums and profile-local destination. **Set up and enable** authorizes that setup and its readiness checks. Cancelling before confirmation leaves enablement unchanged. Failed verification or missing prerequisites do not authorize fallback to the host display.

After changing Python activation, restart the owning backend and start a new conversation rather than changing an existing conversation's cached tool schemas. Read [Legacy upgrade safety](#legacy-upgrade-safety) before restarting a backend with older active Realms.

## Coding and testing

The agent can discover the bundled `hermes-realms:realms` skill and existing Hermes computer-use capability, including through deferred tool discovery. Users do not need to supply slash commands.

1. Edit/build the normal project using ordinary tools and its existing host authentication.
2. Select the requested kind, or choose the kind appropriate to the task. Missing prerequisites lead to the existing native setup/consent flow, not an improvised installer or another desktop.
3. For a VM, copy only the selected test files. There is no host-home mount or implicit synchronization.
4. Run a target operation with `terminal(target="realm", ...)`. Use tracked background execution for a GUI application. The result's `target_cwd` belongs to that target, not the parent project.
5. Use the existing `computer_use` tool for capture, click, typing and drag. `app="screen"` captures the selected private desktop. Verify application effects, not merely a successful input response.
6. Export guest changes deliberately, inspect them and resolve conflicts in the normal project.
7. Continue normal editing or GitHub work with ordinary tools. No `/realm off`, guest GitHub login, credential forwarding or custom publisher is required.

Targets are owned by conversation, profile and connection, with generation/endpoint validation. Another conversation's resource is not a fallback. Regular Realms can access the existing host project filesystem; VM projects must be transferred explicitly.

## Setup and recovery

The pinned driver installer supports **Linux x86-64 only**. A regular Realm needs labwc, Xwayland, WayVNC, grim, wlr-randr, bubblewrap, D-Bus/AT-SPI, a working systemd user manager and the appropriate renderer prerequisites. The plugin does not alter or reload host Hyprland configuration.

An ordinary chat does not show a setup warning just because the plugin is enabled. When private testing is requested, ready prerequisites can be reused; otherwise the conversation offers the selected kind's setup action. The review distinguishes gateway-host packages from profile-local driver/base files. Supported Arch package installation uses native Polkit authorization, never a password in chat. Other hosts or missing KVM/key/privilege requirements may require an explicit blocker or administrator preparation.

Setup progress comes from the actual job. Reopening the chat observes that job, and stale owner/config/source confirmation is rejected. Cancelling a review starts nothing; cancellation does not mean already installed shared components are uninstalled. A completed download alone does not establish readiness.

When an agent's native Desktop/TUI request encountered missing setup, successful setup can resume that same conversation at an idle boundary. The backend rechecks the owner, selected kind, permissions and cancellation state. It asks the agent to reevaluate the current task, not replay an old command or click. Manual setup without a recorded agent request does not launch an unsolicited turn. Cancelled or uncertain continuation attempts are not automatically replayed. Setup readiness and continuation completion are separate states; a ready target does not prove the test finished.

Advanced/manual CLI preparation remains available:

```sh
hermes plugins enable hermes-realms
hermes realms --help
hermes realms doctor
hermes realms install-driver
```

Use `hermes -p NAME ...` for a named profile. CLI enablement also requires setup consent (default **No**); noninteractive callers must supply the exact profile/key/revision consent shown by that flow. Force/install flags are not substitutes for consent.

`hermes realms install-driver` installs the pinned cua-driver 0.23.2 into the active profile's `plugin-data/hermes-realms/bin/cua-driver`, verifies its checksum and execution, and leaves global binaries and packaged source untouched. `--archive /path/to/approved-release.tar.gz` uses an existing approved archive. `--target` is an administrative export override, not the runtime configuration. A valid installation can be reused without another download. Driver verification alone is not completed profile setup.

A broken GUI driver does not disable ordinary tools or target diagnostics. For a running target, `/realm repair` retires only its CUA connection; the next computer-use reconnects. Verify a fresh capture and application continuity. Repair does not reinstall the OS, allocate a substitute guest or replay uncertain input. Missing privileges, unavailable guests and damaged ownership remain explicit failures.

## Omarchy VM details

The base is installed from **Omarchy's GPG-verified release ISO** using a pinned vendored copy of [`omarchy vm`](https://github.com/omacom/omarchy/pull/10977); see [vendor provenance](realms/vendor/VENDOR.md). Each conversation gets its own working overlay, not a shared profile desktop. Startup, download and memory figures depend on the host and workload; configured RAM is not a measured usage report.

VM setup needs `qemu-full`, `edk2-ovmf`, `mtools`, access to `/dev/kvm` and an existing host `~/.ssh/id_ed25519` key pair. Setup does not rewrite host keys or virtualization permissions. Review the selected host/profile, downloads and storage before confirming.

```sh
hermes realms vm doctor
hermes realms vm install
hermes realms vm status
hermes realms vm settings
hermes realms vm settings --check-updates
```

- Files are copied explicitly, never automatically mounted or synchronized. The host private key and SSH agent are not copied or forwarded into the guest; X11 forwarding is also disabled.
- The guest disk is unencrypted and its desktop account has passwordless sudo. Keep personal tokens and authentication files out of it. Retained disks remain on the host after Stop.
- Guest networking is enabled by default. QEMU user networking can expose host loopback services through `10.0.2.2`; `plugins.realms.vm.network: false` selects restricted networking. A VM is not a promise of unlimited containment.
- Host terminal execution does not become guest execution. Targeted guest commands receive their target environment, not the host's API credentials or display/bus handles.
- Hermes computer-use is wired to the guest driver for capture and input. `/realm shot` is a separate permitted screenshot fallback, not a way around human control or CUA permission checks.

### Selected copies and conflicts

`/realm push SOURCE [GUEST_PATH]` copies a selected host file/tree to the VM. `/realm pull GUEST_PATH LOCAL_PATH` requires an explicit host destination. Quote literal paths; spaces, Unicode, quotes, backslashes and wildcard characters are data, not glob selections.

Directory push uses its exact selected guest directory. Pull treats an existing destination directory as a **container** and returns the final path beneath it. Check the returned `destination`, especially on repeated copies.

Different existing bytes, entry types or link targets are conflicts, not permission to overwrite. Identical compatible entries are left in place; non-conflicting additions may be merged. Export changed guest work into a fresh explicit location, then inspect and reconcile it with the host project. Preserve both copies on conflict. Transfers are not crash-atomic whole-tree synchronization: interrupted work can need inspection, and cleanup must not erase a concurrent replacement.

### Configuration

Values are under `plugins.realms` in the selected profile's config:

| Key | Default | Meaning |
|---|---|---|
| `default_kind` | `realm` | Kind selected when no kind was requested |
| `vm.memory` | `3072` | Configured guest RAM in MiB |
| `vm.network` | `true` | Whether guest networking is unrestricted |
| `vm.disk_size` | `40G` | Virtual size used when preparing the base image |
| `vm.boot_timeout` | `180` | SSH boot-readiness timeout in seconds |
| `vm.omarchy_vm_path` | *(vendored)* | Explicit custom already-headless VM launcher |

A retained workspace restarts from its frozen launch specification; changing profile defaults is not a promise to mutate an existing guest. Base-image update checks are opt-in. An unavailable update check reports unknown, not “up to date”; checking does not download or replace the current base.

`hermes realms vm clean` removes stale ISO downloads and reports preserved workspaces. It does **not** delete supposedly orphaned guest disks. `remove-base` refuses while retained workspace data depends on the base. Use the explicit administrative Delete commands below for stopped retained workspaces. Native session Delete controls are still being integrated; do not substitute generic Clean or guessed filesystem cleanup for Delete.

## Manual controls and lifetime

The legacy `default_mode` values remain configuration vocabulary, not parent execution routes: `realm` permits target use, while `host`/`ask` require re-enabling/selecting target use. None makes ordinary terminal/file operations private or grants physical-desktop input.

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

### Explicit administrative Delete

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

For selected saved work from an older **regular Realm**, follow [Manual export and cold recovery](docs/legacy-recovery.md) **before** upgrading or retiring the old runtime. The administrator verifies a durable external copy, retires the old Realm, restores into a new workspace and reviews conversation permissions separately. This is not automatic active-legacy handoff, whole-HOME or VM-disk migration. Complete cross-surface permission migration remains **not qualified**.

### Review earlier execution permissions

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

Native tests are separately marked `integration`. Use the canonical runner in an explicitly approved disposable environment, with actual prerequisites and retries disabled; `--include-integration` alone does not select marked tests:

```sh
HOME="$(mktemp -d)" scripts/run_tests.sh \
  tests/plugins/test_bundled_realms_cli.py \
  tests/plugins/test_realms_import_runtime.py \
  tests/plugins/test_realms_setup.py \
  -m integration --file-retries 0 -j 1 -W error
```

Some setup cases download the pinned driver. Packaging must use the supported build toolchain; imports, editable fixtures or a source build do not establish packaged native acceptance. Full Desktop consent/retry/source-switch, both target kinds, real model discovery, human takeover, locked-screen behavior and final-candidate continuity require their own native evidence. Do not infer those results from screenshots or mock UI tests, and do not call this proposal release-ready while its qualification gates remain open.

### Known limitations

- **Delegated sub-agents.** A sub-agent that requests a Realm target (`terminal(target="realm")` or `computer_use`) gets its own separate private Realm as a new owner. It does not inherit the parent's `/realm off` or chosen kind. The child's Realm is not stopped when the sub-agent finishes; it idles out after `plugins.realms.idle_ttl` (default 1800 seconds, 30 minutes) and its workspace is kept. A child cannot reach another owner's Realm, including its parent's.
- **Physical screen lock:** not tested.

## License

Original code: [MIT](LICENSE). Vendored assets: [third-party notices](realms/web/THIRD_PARTY.md), including all original license and author files. The MIT license does not replace the component licenses.
