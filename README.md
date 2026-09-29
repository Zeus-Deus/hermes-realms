# Hermes Realms

Realms is an optional Hermes plugin that gives an agent a private Linux desktop for testing GUI and desktop software. The agent opens, clicks and screenshots apps inside the Realm, never on your screen, pointer or clipboard. Its ordinary work (terminal, files, git) stays where it was.

## Two kinds

| Kind | What it is | Use it when |
|---|---|---|
| **Regular Realm** | A lightweight private labwc desktop on your machine. Starts fast. | Most GUI testing. It shares your kernel, files and network, so it is **not** a VM or a sandbox for hostile code. |
| **Omarchy VM** | A full Omarchy (Arch) virtual machine in QEMU/KVM, with its own kernel and disk. | You need a full distro, system-level changes, Omarchy/Hyprland itself, or real isolation. Files are copied in and out explicitly. |

## Using it

Just ask the agent. It picks a Realm when it needs to test something, and asks you to approve setup the first time.

Manual controls, if you want them:

| Control | What it does |
|---|---|
| `/realm on` | Let the agent use a Realm in this conversation. `/realm on omarchy` picks the Omarchy VM. |
| `/realm off` | Stop the agent from using a Realm. The desktop and your view of it stay. |
| `/realm stop` | Shut the Realm down and keep its workspace. |
| `/realm watch` | Open a view-only window onto the running Realm. |
| `/realm status` | Show the kind, whether it is running, and setup state. |
| `/realm repair` | Reset the agent's GUI-control connection to a running Realm. |
| `/realm review` | Let an earlier chat run tools again after the upgrade (asks first). `/realm review unused` does this for every earlier chat that never used a Realm. |
| `/realm push SOURCE [GUEST_PATH]` | Copy a file or folder into the Omarchy VM. |
| `/realm pull GUEST_PATH LOCAL_PATH` | Copy a file or folder out of the Omarchy VM. |
| **Delete workspace…** in the ⋯ menu | Permanently delete a stopped Realm's retained workspace (Desktop app). |

## Install

```sh
hermes plugins install Zeus-Deus/hermes-realms
hermes plugins enable hermes-realms
```

Linux x86-64 only. Enabling shows a setup review before anything is installed. In the Desktop app, also turn on **Settings → Plugins → Realms** for the session controls.

## Known limitations

- **Sub-agents** get their own separate Realm. It does not inherit the parent's `/realm off` or chosen kind, and it idles out (30 minutes by default) instead of stopping when the sub-agent finishes.
- **Chats started before this upgrade:** enabling the plugin lists how many earlier chats never used a Realm. Accepting the setup lets all of them run on your normal desktop again (the agent works outside the Realm, and a Realm is still available as a tool). This one decision covers all of them. Earlier chats that did use a Realm still need `/realm review` once in that chat; until then their tools fail with `legacy_permission_review_required`. If Realms was already enabled, run `/realm review unused` (or `hermes realms review unused`; add `--dry-run` to only count). New chats aren't affected.
- **If Realms fails to load** (for example a missing Python dependency right after enabling), tools pause with `realms_load_failed` and the real error only in chats that use a Realm or still need `/realm review`; other chats keep working. `/realm status` shows the cause. Restart Hermes, or run `hermes pm repair` and restart.
- **Physical screen lock:** not tested.

## More

- [User guide](docs/user-guide.md)
- [Setup and prerequisites](docs/setup.md)
- [Omarchy VM](docs/vm.md)
- [Configuration](docs/configuration.md)
- [Safety, lifetime and Delete](docs/safety.md)
- [Recovering work from an older Realm](docs/legacy-recovery.md)
- [Running the tests](docs/testing.md)

## License

Original code: [MIT](LICENSE). Vendored assets: [third-party notices](realms/web/THIRD_PARTY.md), including all original license and author files. The MIT license does not replace the component licenses.
