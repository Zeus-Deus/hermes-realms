# Realms

Realms provides optional private test desktops. The agent keeps its normal terminal, project files, cwd, research and GitHub authentication on its original configured backend. Only explicitly targeted operations run in the private environment. There is nothing to exit after testing, and normal coding access does not authorize control of your physical desktop.

- **Regular Realm** is a session-owned labwc/Wayland desktop, with Xwayland support. It separates GUI interaction but shares the host kernel, filesystem and network; it is not a hostile-code sandbox.
- **Omarchy VM** is a session-owned QEMU/KVM guest with its own disk, kernel and Omarchy/Hyprland environment, for compositor-specific and system-level testing.

> **Proposal and compatibility.** This proposal requires the generic native-plugin setup and session-extension interfaces described in [PR #103690](https://github.com/NousResearch/hermes-agent/pull/103690). Full release qualification is not complete. In particular, an existing active whole-session Realm must not be silently upgraded or treated as safely migrated; see the legacy warning below.

## Enable and prepare

The plugin is disabled by default. Discovery does not import its Python, expose its tools or skills, install a driver or start a desktop. Enable its agent runtime for the intended connected gateway profile. The Desktop UI has a separate opt-in in Settings → Plugins; a UI toggle alone grants no backend authority.

Enabling the agent plugin uses the native setup review for the pinned, verified profile-local driver. Confirmation can authorize setup; merely discovering the plugin does not. Existing conversations retain their cached tool schemas, so use a new conversation after changing runtime activation.

The installer supports **Linux x86-64**. Regular Realms need the private compositor, capture, D-Bus/AT-SPI and systemd-user prerequisites. Omarchy VM also needs QEMU, firmware/image tools, KVM access and an existing host SSH key pair. Supported Arch package setup requests native Polkit authorization. Other hosts, missing permissions or missing keys can require explicit administrator preparation; do not assume universal automatic installation.

Advanced CLI preparation remains available:

```sh
hermes plugins enable hermes-realms
hermes realms --help
hermes realms doctor
hermes realms install-driver
hermes realms vm doctor
```

Use `hermes -p NAME ...` for a named profile. `install-driver` writes only the selected profile's `plugin-data/hermes-realms/bin/`; `--archive /path/to/approved-release.tar.gz` reuses an existing approved archive. It does not modify global binaries or packaged source. Completed driver verification is not, by itself, completed profile setup.

## Ordinary work and private tests

Users do not need to supply slash commands. The agent can discover both kinds and the bundled Realms skill, honor a named kind, and use the existing Hermes computer-use tool for capture and input. Ordinary non-GUI work does not allocate a Realm.

Keep the normal checkout as the source of truth. For a VM, copy only the selected test files; there is no host-home mount or invisible two-way synchronization. Target commands use the explicit terminal target. Ordinary terminal and file calls remain ordinary before, during and after testing, including when the target's GUI driver fails.

Export guest changes deliberately. Different existing bytes, types or link targets are conflicts, not permission to overwrite. Identical compatible copies and non-conflicting additions can be reused. Pull into an existing directory treats it as a container; inspect the returned destination. Prefer a fresh export location before reconciling changes with the project.

The VM has an unencrypted disk and passwordless guest sudo. Do not copy personal tokens, SSH agents or authentication directories into it. Guest networking is enabled by default and can reach host services; this is not unlimited containment. Management uses verified guest identity without forwarding the host's private key or agent.

For a native Desktop/TUI agent request that needed setup, successful setup can resume the same conversation when it is idle and still authorized. The agent reevaluates the current task instead of replaying an old command. Manual setup alone does not create a new turn. Cancelled or uncertain continuation attempts are not automatically replayed; target readiness and completion of the test are separate outcomes.

## Session controls

The compact session UI offers setup when needed, then Watch and a management menu. The advanced commands use the same target lifecycle:

| Command | Effect |
|---|---|
| `/realm on [realm\|omarchy]` | Select/re-enable this conversation's target kind. Regular startup is lazy for targeted work; VM selection currently starts/reuses the guest. |
| `/realm status` | Inspect selected kind, readiness, running/stopped/recovery state and diagnostics independently of the GUI driver. |
| `/realm size WIDTHxHEIGHT` | Resize the selected live target. |
| `/realm watch` | Open a short-lived view-only viewer. |
| `/realm repair` | Reset the running target's CUA connection, without replacing its workspace or replaying uncertain input. |
| `/realm off` | Disable agent target use while retaining the target and human viewing. It does not switch ordinary tools back to a host route; they never left it. |
| `/realm stop` | End owned compute while retaining modern workspace data. This is not Delete. |

Explicit Disable survives backend restarts and cannot be silently reversed by the agent's `on` action. Re-enable manually with `/realm on` or the existing desktop setup review. An unset session preference is not a manual Disable: the agent can still select a target when a test needs it. Normal coding and human viewing remain available while disabled.

Watch and Pop out do not grant input. Human takeover excludes agent inspection/input on that target; an interrupted connection is not handback. Unrelated parent work remains available. Closing a viewer is not a request to stop or delete its target. Remote Watch/Pop out requires an explicit viewer tunnel, not a backend loopback URL.

Save application changes before Stop: retained files/disks do not preserve unsaved application memory. Modern workspaces survive compute retirement and can be explicitly reused after validation. Missing ownership or unregistered work must not be hidden by a fresh empty replacement. Generic VM Clean preserves workspace data and cleans stale downloads. Explicit administrative Delete is available for stopped workspaces:

```sh
hermes realms delete ID --session-id OWNER
hermes realms vm delete ID --session-id OWNER
```

Get the exact ID and `session_id` from the corresponding `list` command in the intended profile. Delete requires an interactive terminal and exact typed confirmation. It refuses running, foreign or changed targets, including a restart during confirmation. It never automatically stops compute, removes another workspace, or deletes shared VM base data. Cancelled/noninteractive confirmation deletes nothing; interrupted deletion needs a fresh review. Owner selection is administrative, not authenticated session identity. Native session Delete UI remains unqualified; there is no model-tool Delete or `/realm delete` slash command.

The legacy `default_mode` values now describe target availability, not the parent's execution environment. Explicit target failure never permits host-display fallback or bypassing human control, and existing approvals continue to apply.

## Legacy upgrade warning

An older running regular Realm can still hold destructive cleanup code and keep its HOME only under volatile `/run`. Replacing source files does not update that already-running guardian.

The candidate's manager/integration guards refuse unsafe legacy Start/Stop/finalize/unload and expose an export-required warning rather than intentionally triggering destructive cleanup. These guards are not a shutdown veto: host termination, the old lifecycle or reboot can still remove unexported work. Preserve and verify recoverable work before ending that lifecycle. Automatic active-legacy handoff and migration of prior whole-session isolation permissions remain release gates, not completed guarantees.

For modern sessions, stopping compute, disabling runtime activation, closing a viewer and deleting data are separate operations. Disabling the plugin does not imply permission to remove retained work or another profile's files.

## Verification limits

[Setup](setup.md), [configuration](configuration.md), [Omarchy VM](vm.md) and [running the tests](testing.md) document prerequisites, configuration, provenance and native test selection. Unit tests, screenshots and loader tests do not establish full application acceptance. Both target kinds, native consent, failure recovery, parallel ownership, human control, the actual deployment route and authorized locked-screen operation require their own evidence. Screen lock is not host suspend or power loss.
