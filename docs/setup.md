# Setup

Realms gives a conversation its own Linux test desktop without moving the agent's ordinary work into it. Normal terminal commands, files, cwd, research and GitHub operations keep the conversation's original configured backend and approvals. That backend may be remote; it does not automatically become the Desktop client's machine. There is nothing to exit after a private test.

Two session-owned targets are available:

- **Regular Realm** (`realm`): a private labwc/Wayland desktop with Xwayland support. It separates windows, pointer, clipboard and focus from the user's desktop, but shares the host kernel, filesystem and networking. **It is not a hostile-code sandbox.**
- **Omarchy VM** (`omarchy-vm`): an Omarchy QEMU/KVM guest with its own kernel, disk and Hyprland environment, for Omarchy/Quattro plugins, themes and system-level tests.

Enabling the plugin makes these capabilities available; ordinary coding does not allocate a desktop. An explicit target failure never falls back to the physical desktop. Normal host coding authority is not permission to drive that desktop.

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

After changing Python activation, restart the owning backend and start a new conversation rather than changing an existing conversation's cached tool schemas. Read [Legacy upgrade safety](safety.md#legacy-upgrade-safety) before restarting a backend with older active Realms.

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

## Host APIs
The plugin requires the generic consented native-plugin setup, session ownership, execution-context and Desktop viewer APIs described in [NousResearch/hermes-agent#103690](https://github.com/NousResearch/hermes-agent/pull/103690). Do not advertise compatibility with hosts lacking those interfaces. Optional terminal and computer-use routing uses generic target resolvers, not a Realms-specific override of the parent's environment.

Runtime, Desktop, dashboard and skill live together in this repository. Desktop builds discover this same `desktop/plugin.js`; there is no second implementation or dependency on a separately installed `realms` Python package. Original MIT attribution and all vendored noVNC/pako notices are preserved.
