# Configuration

Values are under `plugins.realms` in the selected profile's config:

| Key | Default | Meaning |
|---|---|---|
| `default_kind` | `realm` | Kind selected when no kind was requested |
| `vm.memory` | `3072` | Configured guest RAM in MiB |
| `vm.network` | `true` | Whether guest networking is unrestricted |
| `vm.disk_size` | `40G` | Virtual size used when preparing the base image |
| `vm.boot_timeout` | `180` | SSH boot-readiness timeout in seconds |
| `vm.workspace_retention_days` | `14` | Automatically delete unused stopped VM workspaces after this many days; `0` keeps them indefinitely |
| `vm.omarchy_vm_path` | *(vendored)* | Explicit custom already-headless VM launcher |

A retained workspace restarts from its frozen launch specification; changing profile defaults is not a promise to mutate an existing guest. Base-image update checks are opt-in. An unavailable update check reports unknown, not “up to date”; checking does not download or replace the current base.

VM workspace retention uses the **current profile setting**, including for workspaces created before this feature. On VM list/status or startup, workspaces unused for at least 14 days since the later of their last activity and verified stop are eligible for deletion. There is no background cleanup service: an unused profile is cleaned the next time its VMs are listed or started. A startup preserves the stopped workspace it is explicitly resuming. Running/starting VMs, uncertain shutdowns, open disks, unregistered directories and shared bases/ISOs are preserved. Failed safety checks defer cleanup. Old guest-only files are permanently removed with their overlay; copy useful work out before expiry, or set `vm.workspace_retention_days: 0` for persistent VM workspaces. The existing compute idle policy is separate from disk retention.

`hermes realms vm clean` removes stale ISO downloads and reports preserved workspaces. It does **not** delete supposedly orphaned guest disks. `remove-base` refuses while retained workspace data depends on the base. Use the explicit administrative Delete commands below for stopped retained workspaces. Native session Delete controls are still being integrated; do not substitute generic Clean or guessed filesystem cleanup for Delete.

## `default_mode`

The legacy `default_mode` values remain configuration vocabulary, not parent execution routes: `realm` permits target use, while `host`/`ask` require re-enabling/selecting target use. None makes ordinary terminal/file operations private or grants physical-desktop input.

See [safety and lifetime](safety.md) for what each manual control does.
