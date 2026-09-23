# Omarchy VM

The base is installed from **Omarchy's GPG-verified release ISO** using a pinned vendored copy of [`omarchy vm`](https://github.com/omacom/omarchy/pull/10977); see [vendor provenance](../realms/vendor/VENDOR.md). Each conversation gets its own working overlay, not a shared profile desktop. Startup, download and memory figures depend on the host and workload; configured RAM is not a measured usage report.

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

## Selected copies and conflicts
`/realm push SOURCE [GUEST_PATH]` copies a selected host file/tree to the VM. `/realm pull GUEST_PATH LOCAL_PATH` requires an explicit host destination. Quote literal paths; spaces, Unicode, quotes, backslashes and wildcard characters are data, not glob selections.

Directory push uses its exact selected guest directory. Pull treats an existing destination directory as a **container** and returns the final path beneath it. Check the returned `destination`, especially on repeated copies.

Different existing bytes, entry types or link targets are conflicts, not permission to overwrite. Identical compatible entries are left in place; non-conflicting additions may be merged. Export changed guest work into a fresh explicit location, then inspect and reconcile it with the host project. Preserve both copies on conflict. Transfers are not crash-atomic whole-tree synchronization: interrupted work can need inspection, and cleanup must not erase a concurrent replacement.

Settings for the VM (`vm.memory`, `vm.network`, `vm.disk_size`, …) are listed in [configuration](configuration.md).
