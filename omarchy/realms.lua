-- Optional host-viewer integration only. Never routes apps inside a realm.
-- Native SDK locks the prefix before mapping; page titles cannot replace it.
local M = {}
local prefix = '^Hermes Viewer %[hermes%-realms/(realm%-[a-f0-9]+)%] — '
local function identity(window)
  if not window or type(window.initial_title) ~= 'string' or type(window.address) ~= 'string'
      or not window.address:match('^0x%x+$') then return nil end
  local id = window.initial_title:match(prefix)
  if not id or #id ~= 70 or type(window.title) ~= 'string' or window.title:match(prefix) ~= id then return nil end
  return id
end

function M.install(hl, o, options)
  options = options or {}
  local slots, by_id, by_address = {}, {}, {}
  local sequence, selected = 0, nil
  o.window({ initial_title = '^Hermes Viewer \\[hermes-realms/realm-[a-f0-9]{64}\\] — .*$' }, {
    workspace = 'special:hermes-realms silent', float = true, no_initial_focus = true,
    focus_on_activate = false, suppress_event = 'activate activatefocus',
    size = { 'monitor_w * 0.8', 'monitor_h * 0.8' }, center = true,
  })

  local function latest()
    local best
    for _, slot in pairs(slots) do
      if not best or slot.sequence > best.sequence then best = slot end
    end
    return best
  end
  local function show(slot)
    if not slot then return end
    sequence = sequence + 1
    slot.sequence, selected = sequence, slot.index
    hl.dispatch(hl.dsp.workspace.toggle_special('hermes-' .. slot.index))
  end
  local function opened(window)
    local id = identity(window)
    if not id or by_address[window.address] then return end
    local slot = by_id[id]
    if not slot then
      local previous = window.workspace and tonumber(window.workspace.name:match('^special:hermes%-(%d+)$'))
      local index = previous and previous >= 1 and previous <= 90 and not slots[previous] and previous or 1
      while slots[index] do index = index + 1 end
      -- Hyprland allows at most 97 special workspaces; leave room for others.
      if index > 90 then return end
      slot = { id = id, index = index, addresses = {} }
      by_id[id], slots[index] = slot, slot
    end
    sequence = sequence + 1
    slot.sequence = sequence
    slot.addresses[window.address], by_address[window.address] = true, slot
    local workspace = 'special:hermes-' .. slot.index
    if not window.workspace or window.workspace.name ~= workspace then
      hl.dispatch(hl.dsp.window.move({ window = 'address:' .. window.address, workspace = workspace, follow = false }))
    end
  end
  local function closed(window)
    if not window then return end
    local slot = by_address[window.address]
    if not slot then return end
    slot.addresses[window.address], by_address[window.address] = nil, nil
    if not next(slot.addresses) then by_id[slot.id], slots[slot.index] = nil, nil end
  end
  hl.on('window.open', opened)
  hl.on('window.close', closed)
  hl.on('config.reloaded', function()
    local windows = hl.get_windows()
    -- Stable recovery order; existing scratchpad indices survive reload.
    table.sort(windows, function(a, b) return a.address < b.address end)
    for _, window in ipairs(windows) do opened(window) end
  end)
  if options.bindings ~= false then
    -- Caller must inspect conflicting bindings before opting into this snippet.
    o.bind('SUPER + ALT + S', 'Latest Hermes realm viewer', function() show(latest()) end)
    o.bind('SUPER + ALT + SHIFT + S', 'Cycle Hermes realm viewers', function()
      local current = selected or (latest() and latest().index) or 0
      for index = current + 1, 90 do if slots[index] then show(slots[index]); return end end
      for index = 1, current do if slots[index] then show(slots[index]); return end end
    end)
    for index = 1, 9 do
      o.bind('SUPER + ALT + ' .. index, 'Hermes realm viewer ' .. index, function() show(slots[index]) end)
    end
  end
end

return M
