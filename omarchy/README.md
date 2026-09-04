# Optional Omarchy viewer scratchpads

This is opt-in source, **not an installer**. No actual `~/.config/hypr` or packaged Omarchy files are modified by this repository.

`realms.lua` is a Lua module; `example.lua` shows how to source it from user configuration after copying the module beside `hyprland.lua`. First inspect conflicts with `omarchy menu keybindings --print`. The example disables shortcuts until you deliberately enable them; unbind any existing conflicts explicitly before enabling. Back up user files before installation, then validate any actual configuration changes with `hyprctl reload` and `hyprctl configerrors`.

## Behavior

- Uses **native, locked initial title** `Hermes Viewer [hermes-realms/realm-<64 lowercase hex>] — …`. The SDK implementation in `hermes-realms-host-sdk/apps/desktop/electron/browser-windows.ts` prefixes/locks this identity before revealing the viewer. Page title is not used as realm identity. Electron has no supported per-window Linux WM_CLASS/app_id override; this integration does not pretend `hermes-realm-viewer` is a class.
- Static rule: floating, centered, 80% monitor size, `no_initial_focus`, `focus_on_activate=false`, suppressed activate requests, initial `special:hermes-realms silent` holding workspace. No `pin=true` (that would place it on every workspace).
- `window.open` allocates a stable, first-free index, then moves the viewer with `follow=false` to `special:hermes-N`. Neither open nor reload dispatches focus or toggles a workspace.
- With `bindings=true`: **Super+Alt+S** toggles the most recently opened/toggled realm; **Super+Alt+Shift+S** cycles live realms; **Super+Alt+1…9** toggles an index. Only an explicit hotkey reveals a viewer.
- Closing the final native viewer for a realm releases its index. Multiple native windows for the same realm share the scratchpad. Up to 90 indexes are tracked; direct keys address the first nine. Hyprland has a total special-workspace limit, including non-Realm workspaces.
- Configuration reload rebuilds indexes from mapped windows; existing `special:hermes-N` assignments survive. Recency itself is not persisted; reload recovery uses stable address order.
- This module only handles host viewer windows. It does not launch a compositor, call `hyprctl` for input, inhibit idle/lock, move the real cursor, or control realm apps.

The prefix is a window-routing convention, not a hostile-local-client authentication boundary: a same-user application can imitate an OS window title. Remote page content cannot replace the SDK's locked title. Viewer authentication remains the backend's responsibility.

## Verify without installing

From repository root:

```sh
lua tests/omarchy/realms_test.lua
Hyprland --verify-config -c "$PWD/tests/omarchy/verify.lua"
```

The parser test loads **read-only** packaged Omarchy helpers, then this module. It does not launch a compositor, reload the running compositor, install bindings or touch user configuration. Tested on Hyprland 0.56.2: `config ok`. Runtime field formats and dispatcher construction were also probed read-only; live opening/hiding of a DEV native viewer still belongs to full-app E2E verification.

Sources checked while implementing:
- https://wiki.hypr.land/Configuring/Basics/Window-Rules/
- https://raw.githubusercontent.com/hyprwm/hyprland-wiki/main/content/configuring/core/dispatchers.md
- https://raw.githubusercontent.com/hyprwm/hyprland-wiki/main/content/configuring/core/advanced-configuration/events.md
- `/usr/share/hypr/stubs/hl.meta.lua` and `/usr/share/omarchy/default/hypr/helpers.lua`
