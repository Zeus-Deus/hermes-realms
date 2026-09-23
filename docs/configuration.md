# Configuration

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

## `default_mode`

The legacy `default_mode` values remain configuration vocabulary, not parent execution routes: `realm` permits target use, while `host`/`ask` require re-enabling/selecting target use. None makes ordinary terminal/file operations private or grants physical-desktop input.

See [safety and lifetime](safety.md) for what each manual control does.
