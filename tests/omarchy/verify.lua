-- Parser-only receipt: Hyprland --verify-config -c "$PWD/tests/omarchy/verify.lua"
-- Executed from repository root; no production config or compositor is changed.
dofile('/usr/share/omarchy/default/hypr/helpers.lua')
dofile('omarchy/realms.lua').install(hl, o)
