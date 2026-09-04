-- Optional user-config snippet; not installed automatically.
-- Copy realms.lua next to your user hyprland.lua before sourcing this example.
-- Omarchy's default helpers provide `o`; see README before enabling shortcuts.
local realms = dofile(os.getenv('HOME') .. '/.config/hypr/realms.lua')
realms.install(hl, o, { bindings = false })
-- After inspecting conflicts with `omarchy menu keybindings --print`, replace
-- bindings=false with bindings=true. Unbind conflicting keys explicitly first:
-- hl.unbind('SUPER + ALT + S')  -- ONLY if overriding that existing binding
