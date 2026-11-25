-- The laptop's part of the Hyprland config: its screens and the lid.
-- modules/home/desktop.nix puts it ahead of the shared
-- dotfiles/hypr/hyprland.lua, in the same Lua chunk.

------------------
---- MONITORS ----
------------------

-- Extended desktop, each screen at its native resolution:
-- laptop panel on the left, external Dell U2722D (QHD, max 60Hz over HDMI,
-- 1.25 scale -> 2048x1152 logical) to its right.
local internal = { output = "eDP-1", mode = "1920x1200@60.00", position = "0x0", scale = 1 }
local external = { output = "HDMI-A-1", mode = "2560x1440@60", position = "1920x0", scale = 1.25 }
-- Docked: two more U2722Ds right of the panel, JXTL3P3 on the left and
-- 8XTL3P3 on the right, also at 1.25 (2048x1152 logical each). Keyed by
-- their EDID description, which holds the serial: the dock's connector
-- names (DP-3 and DP-5 on 2026-11-25) follow the order the cables came
-- up in. desc: rules match on a prefix and win over name rules.
local dock_left = { output = "desc:Dell Inc. DELL U2722D JXTL3P3", mode = "2560x1440@60", position = "1920x0", scale = 1.25 }
local dock_right = { output = "desc:Dell Inc. DELL U2722D 8XTL3P3", mode = "2560x1440@60", position = "3968x0", scale = 1.25 }
hl.monitor(dock_left)
hl.monitor(dock_right)
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
local desc_rules = { dock_left, dock_right }
local LAYOUTS = { extend = true, mirror = true, external = true, builtin = true }

-- A screen's own rule: by its connector name, or by its description for a
-- desc: rule (hl.get_monitors with all = true lists the dark screens too),
-- else any_other.
local function own_rule_of(output)
    if own_rule[output] then return own_rule[output] end
    for _, monitor in ipairs(hl.get_monitors({ all = true })) do
        if monitor.name == output then
            local desc = "desc:" .. monitor.description
            for _, rule in ipairs(desc_rules) do
                if desc:sub(1, #rule.output) == rule.output then return rule end
            end
        end
    end
    return any_other
end

-- The whole rule for a screen, with extra on top. One with a desc: rule
-- gets it under that desc: output, since a rule under its connector name
-- would lose to it.
local function full_rule(output, extra)
    local own = own_rule_of(output)
    local rule = {}
    for k, v in pairs(own) do rule[k] = v end
    rule.output = own == any_other and output or own.output
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
