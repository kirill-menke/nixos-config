-- The laptop's part of the Hyprland config: its screens, the lid and the
-- cursor. modules/home/desktop.nix puts it ahead of the shared
-- dotfiles/hypr/hyprland.lua, in the same Lua chunk.

------------------
---- MONITORS ----
------------------

-- Extended desktop, each screen at its native resolution:
-- laptop panel on the left, external Dell U2722D (QHD, max 60Hz over HDMI,
-- 1.25 scale -> 2048x1152 logical) to its right.
local internal = { output = "eDP-1", mode = "1920x1200@60.00", position = "0x0", scale = 1 }
local external = { output = "HDMI-A-1", mode = "2560x1440@60", position = "1920x0", scale = 1.25 }
-- Any other external display: preferred mode, placed to the right
local any_other = { output = "", mode = "preferred", position = "auto-right", scale = 1 }
hl.monitor(any_other)

local function lid_closed()
    local ok, closed = pcall(function()
        local f = io.open("/proc/acpi/button/lid/LID0/state")
        if not f then return false end
        local state = f:read("*a")
        f:close()
        return state:find("closed") ~= nil
    end)
    return ok and closed
end

hl.monitor(external)
-- Read at every config load, so the internal screen also stays off when
-- Hyprland starts (or reloads) with the lid already closed.
if lid_closed() then
    hl.monitor({ output = "eDP-1", disabled = true })
else
    hl.monitor(internal)
end

-- Disable internal screen when lid is closed, restore when opened.
-- hl.monitor merges into the output's existing rule, hence disabled = false.
hl.bind("switch:on:Lid Switch",  function() hl.monitor({ output = "eDP-1", disabled = true }) end, { locked = true })
hl.bind("switch:off:Lid Switch", function()
    local rule = { disabled = false }
    for k, v in pairs(internal) do rule[k] = v end
    hl.monitor(rule)
end, { locked = true })

-- The Display card's Screens chooser, for the laptop docked or at a
-- projector with its lid open: card_display.py runs `hyprctl eval
-- 'screen_layout("mirror", { "HDMI-A-1" })'`, naming every screen but the
-- panel, lit or not (Lua here sees only the lit ones, hl.get_monitors()).
--   extend    each screen a desktop of its own, placed as above
--   mirror    the other screens show the panel's picture
--   external  the other screens only, the panel dark
--   builtin   the panel only, the others dark
-- Each screen gets its whole rule from this file with disabled and mirror
-- on top (mirror = "" ends a mirror). hl.monitor() merges a partial rule
-- only into a rule for that very output: a screen with none of its own
-- would start from Hyprland's defaults (auto scale), not from any_other.
-- The rules hold until a config reload brings back the layout above. The
-- lid still switches the panel; with it shut, no layout lights the panel
-- or darkens the others.
local own_rule = { [internal.output] = internal, [external.output] = external }
local LAYOUTS = { extend = true, mirror = true, external = true, builtin = true }

local function full_rule(output, extra)
    local rule = {}
    for k, v in pairs(own_rule[output] or any_other) do rule[k] = v end
    rule.output = output
    for k, v in pairs(extra) do rule[k] = v end
    return rule
end

function screen_layout(layout, others)
    if not LAYOUTS[layout] then
        error("screen_layout: no layout '" .. tostring(layout) .. "'")
    end
    local shut = lid_closed()
    hl.monitor(full_rule(internal.output, { disabled = shut or layout == "external" }))
    local mirror = layout == "mirror" and internal.output or ""
    for _, output in ipairs(others or {}) do
        hl.monitor(full_rule(output, { disabled = layout == "builtin" and not shut, mirror = mirror }))
    end
end


-------------------------------
---- ENVIRONMENT VARIABLES ----
-------------------------------

-- XCursor theme of home.pointerCursor (home-shared.nix).
hl.env("XCURSOR_THEME", "catppuccin-mocha-dark-cursors")
hl.env("XCURSOR_SIZE", "24")

