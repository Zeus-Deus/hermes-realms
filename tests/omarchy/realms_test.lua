-- Behavioral test: execute the integration with a recording native boundary.
local ok, realms = pcall(dofile, 'omarchy/realms.lua')
assert(ok and type(realms.install) == 'function', 'realm integration install helper is missing')
local events, rules, binds, calls = {}, {}, {}, {}
local windows = {}
local function action(kind) return function(args) return { kind = kind, args = args } end end
local hl = {
  on = function(event, fn) events[event] = fn end,
  get_windows = function() return windows end,
  dispatch = function(value) calls[#calls+1] = value end,
  dsp = { window = { move = action('move') }, workspace = { toggle_special = action('toggle') } },
}
local o = { window = function(match, effects) rules[#rules+1] = { match = match, effects = effects } end,
  bind = function(keys, description, fn) binds[keys] = fn end }
realms.install(hl, o)
assert(#rules == 1)
assert(rules[1].effects.workspace == 'special:hermes-realms silent')
assert(rules[1].effects.float == true and rules[1].effects.no_initial_focus == true)
assert(rules[1].effects.focus_on_activate == false)
local function window(id, address)
  return { address = address, title = 'Hermes Viewer [hermes-realms/realm-' .. string.rep(id, 64) .. '] — Realm · coder', initial_title = 'Hermes Viewer [hermes-realms/realm-' .. string.rep(id, 64) .. '] — Realm · coder', workspace = { name = 'special:hermes-realms' } }
end
local a, b = window('a','0x123'), window('b','0x456')
events['window.open'](a)
events['window.open'](b)
assert(#calls == 2, 'only silent moves happen on open')
assert(calls[1].kind == 'move' and calls[1].args.follow == false)
assert(calls[1].args.workspace == 'special:hermes-1' and calls[1].args.window == 'address:0x123')
assert(calls[2].args.workspace == 'special:hermes-2')
binds['SUPER + ALT + S']()
assert(calls[#calls].kind == 'toggle' and calls[#calls].args == 'hermes-2')
binds['SUPER + ALT + SHIFT + S']()
assert(calls[#calls].args == 'hermes-1')
binds['SUPER + ALT + 2']()
assert(calls[#calls].args == 'hermes-2')
events['window.close'](b)
local count = #calls
binds['SUPER + ALT + 2']()
assert(#calls == count, 'closed realm index must not open empty scratchpad')
binds['SUPER + ALT + S']()
assert(calls[#calls].args == 'hermes-1')
for _, bad in ipairs({
  { title = 'browser', initial_title = 'browser', address = '0xabc' },
  { title = a.title, initial_title = 'browser', address = '0xabc' },
  { title = a.title, initial_title = a.title, address = '0x123; exec evil' },
  window('z', '0xabc'),
}) do
  local before = #calls
  events['window.open'](bad)
  assert(#calls == before, 'non-native or malformed identity must not route')
end
print('PASS: silent native-title routing, latest/cycle/index, closed cleanup, malformed identity rejection')

-- Reload should rebuild existing slots without revealing or moving them.
windows = { a }
a.workspace.name = 'special:hermes-7'
events, rules, binds, calls = {}, {}, {}, {}
realms.install(hl, o)
assert(type(events['config.reloaded']) == 'function', 'reload reconciliation is missing')
events['config.reloaded']()
assert(#calls == 0, 'reload must not move an existing correctly-routed viewer')
binds['SUPER + ALT + 7']()
assert(calls[#calls].args == 'hermes-7')
events['window.close'](a)
local before = #calls
binds['SUPER + ALT + S']()
assert(#calls == before)
print('PASS: reload restores viewer indexes, no focus or move on reload')
