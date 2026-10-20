-- Hyprland Lua config.  hyprlang (hyprland.conf) is deprecated since 0.55 and
-- is dropped one or two releases later, so this is the same setup ported to
-- the hl.* API.  API reference: share/hypr/stubs/hl.meta.lua in the Hyprland
-- package (home-manager writes a .luarc.json pointing at it) and
-- https://wiki.hypr.land/Configuring/
--
-- Runtime changes go through `hyprctl eval '<lua>'`: `hyprctl keyword` no
-- longer exists, and `hyprctl dispatch` takes an hl.dsp.* expression.

hl.config({
    misc = {
        disable_splash_rendering = true,
        -- Wake from DPMS off (`screens-off` alias) on any key press.
        key_press_enables_dpms = true,
    },
})

---------------------
---- MY PROGRAMS ----
---------------------

local terminal    = "kitty"
local fileManager = "nautilus"
local menu        = "wofi --show drun"


-------------------
---- AUTOSTART ----
-------------------

-- hyprland.start fires once per compositor start (not on config reloads),
-- which is what exec-once used to be.
-- waybar, swaync and udiskie are started by their home-manager systemd user
-- services (modules/home/desktop.nix): a second udiskie from here would race the
-- service to automount and put a second icon in the tray.
hl.on("hyprland.start", function()
    -- The mic-mute key's LED comes up dark, while WirePlumber may bring the
    -- microphone back muted: osd waits for it (up to 30 s), then sets it.
    hl.exec_cmd("osd mic led")
end)

-- Both holds below send swaync-client commands. When Hyprland starts it
-- loads this file before any monitor, or its Wayland socket, exists; every
-- later load is a reload. At a start swaync holds nothing yet, and a
-- swaync-client call would have D-Bus start swaync before there is a
-- display for it, so both holds start out known to be off.
local reloading = #hl.get_monitors() > 0

-- Screen sharing: swaync holds notification popups back while a monitor
-- or a region of it is captured, so a message preview never shows up in a
-- call. The messages still land in the panel, and the bar's bell turns
-- into a crossed bell. A window share sends only that window, so it needs
-- no hold. "screenshare.state" (active, type, name) fires per capture
-- session and follows its frames, not the share: with nothing changing on
-- screen a session reports false after 500 ms and true at the next frame,
-- and a screenshot is a session too. So the hold starts with the first
-- frame and ends after 10 s without one, which spans those pauses (the bar
-- redraws every few seconds) and keeps a screenshot's hold short.
local SHARE_WINDOW = 1           -- Screenshare::eScreenshareType
local capturing, held = 0, false -- sessions sending frames; hold added
local release
-- "repeat", disarmed between uses: a oneshot timer can't be re-armed
-- once it has fired
release = hl.timer(function()
    release:set_enabled(false)
    held = false
    hl.exec_cmd("swaync-client -sw -Ir screenshare")
end, { timeout = 10000, type = "repeat" })
release:set_enabled(false)
-- A reload runs this file afresh, count and timer included, while swaync
-- keeps a hold the last run added: a reload inside the 10 s wait used to
-- leave it on until Release in the panel. Sessions report only changes,
-- so a reload can't tell a share still running from one just ended: the
-- hold counts as unknown (nil) and the 10 s wait starts over. A running
-- share's next report stops the wait and adds the hold again; with none,
-- the hold ends as after a share.
if reloading then
    held = nil
    release:set_enabled(true)
end

hl.on("screenshare.state", function(active, kind)
    if kind == SHARE_WINDOW then return end
    if active then
        capturing = capturing + 1
        release:set_enabled(false)
        if held ~= true then  -- swaync ignores a hold it already has
            held = true
            hl.exec_cmd("swaync-client -sw -Ia screenshare")
        end
    else
        -- never below zero: a config reload resets the count mid-share
        capturing = math.max(capturing - 1, 0)
        if capturing == 0 then release:set_enabled(true) end
    end
end)

-- Fullscreen: a film, a game or slides filling the monitor where popups
-- show (the focused one) hold them back as a screen share does, under an
-- app id of their own: they still land in the panel, the bell turns into
-- the crossed bell, and critical ones still show. A maximised window keeps
-- the bar in view and holds nothing. Rather than trust each event's
-- arguments, every event that can change the answer reads it afresh: the
-- focused monitor's workspace, and whether its window is fullscreen. After
-- a reload the answer is unknown, so the first event (config.reloaded)
-- sends it, and a hold from before the reload can't outlive it.
local FULLSCREEN = 2  -- fullscreen_mode: 0 none, 1 maximised, 2 fullscreen
local fs_held = false -- hold added; nil: unknown, so the next answer is sent
if reloading then fs_held = nil end

local function sync_fullscreen_hold()
    local ws = hl.get_active_workspace()
    local full = ws ~= nil and ws.has_fullscreen and ws.fullscreen_mode >= FULLSCREEN
    if full ~= fs_held then
        fs_held = full
        hl.exec_cmd("swaync-client -sw -I" .. (full and "a" or "r") .. " fullscreen")
    end
end

-- window.active as well: closing a fullscreen window moves the focus, so
-- the answer is read again even if the close itself reported too early.
-- Cheap: a few field reads per focus change, a command only on a change.
for _, event in ipairs({ "window.fullscreen", "window.active", "window.destroy",
                         "window.move_to_workspace", "workspace.active",
                         "monitor.focused", "monitor.removed", "config.reloaded" }) do
    hl.on(event, sync_fullscreen_hold)
end


-------------------------------
---- ENVIRONMENT VARIABLES ----
-------------------------------

-- See https://wiki.hypr.land/Configuring/Core/Environment-variables/
-- (The cursor theme's variables are in each host's part.)
hl.env("ELECTRON_OZONE_PLATFORM_HINT", "auto")


-----------------------
---- LOOK AND FEEL ----
-----------------------

-- Option names and types: https://wiki.hypr.land/Configuring/Config-options/
hl.config({
    general = {
        gaps_in  = 5,
        gaps_out = 10,

        border_size = 2,

        col = {
            active_border   = { colors = { "rgba(d1002fee)", "rgba(9b0080ee)" }, angle = 45 },
            inactive_border = "rgba(595959aa)",
        },

        -- Set to true to enable resizing windows by clicking and dragging on borders and gaps
        resize_on_border = true,

        -- Please see https://wiki.hypr.land/Configuring/Advanced-and-Cool/Tearing/ before you turn this on
        allow_tearing = false,

        layout = "dwindle",
    },

    decoration = {
        rounding       = 10,
        rounding_power = 2,

        -- Change transparency of focused and unfocused windows
        active_opacity   = 0.95,
        inactive_opacity = 0.8,

        shadow = {
            enabled      = true,
            range        = 4,
            render_power = 3,
            color        = "rgba(1a1a1aee)",
        },

        blur = {
            enabled  = true,
            size     = 3,
            passes   = 1,
            vibrancy = 0.1696,
        },
    },

    animations = {
        enabled = true,
    },

    -- See https://wiki.hypr.land/Configuring/Layouts/Dwindle-Layout/ for more
    dwindle = {
        preserve_split = true, -- You probably want this
    },

    -- See https://wiki.hypr.land/Configuring/Layouts/Master-Layout/ for more
    master = {
        new_status = "master",
    },
})

-- Curves and animations, see https://wiki.hypr.land/Configuring/Core/Animations/
hl.curve("easeOutQuint",   { type = "bezier", points = { { 0.23, 1 },    { 0.32, 1 } } })
hl.curve("easeInOutCubic", { type = "bezier", points = { { 0.65, 0.05 }, { 0.36, 1 } } })
hl.curve("linear",         { type = "bezier", points = { { 0, 0 },       { 1, 1 } } })
hl.curve("almostLinear",   { type = "bezier", points = { { 0.5, 0.5 },   { 0.75, 1.0 } } })
hl.curve("quick",          { type = "bezier", points = { { 0.15, 0 },    { 0.1, 1 } } })

hl.animation({ leaf = "global",        enabled = true, speed = 10,   bezier = "default" })
hl.animation({ leaf = "border",        enabled = true, speed = 5.39, bezier = "easeOutQuint" })
hl.animation({ leaf = "windows",       enabled = true, speed = 4.79, bezier = "easeOutQuint" })
hl.animation({ leaf = "windowsIn",     enabled = true, speed = 4.1,  bezier = "easeOutQuint", style = "popin 87%" })
hl.animation({ leaf = "windowsOut",    enabled = true, speed = 1.49, bezier = "linear",       style = "popin 87%" })
hl.animation({ leaf = "fadeIn",        enabled = true, speed = 1.73, bezier = "almostLinear" })
hl.animation({ leaf = "fadeOut",       enabled = true, speed = 1.46, bezier = "almostLinear" })
hl.animation({ leaf = "fade",          enabled = true, speed = 3.03, bezier = "quick" })
hl.animation({ leaf = "layers",        enabled = true, speed = 3.81, bezier = "easeOutQuint" })
hl.animation({ leaf = "layersIn",      enabled = true, speed = 4,    bezier = "easeOutQuint", style = "fade" })
hl.animation({ leaf = "layersOut",     enabled = true, speed = 1.5,  bezier = "linear",       style = "fade" })
hl.animation({ leaf = "fadeLayersIn",  enabled = true, speed = 1.79, bezier = "almostLinear" })
hl.animation({ leaf = "fadeLayersOut", enabled = true, speed = 1.39, bezier = "almostLinear" })
hl.animation({ leaf = "workspaces",    enabled = true, speed = 1.94, bezier = "almostLinear", style = "fade" })
hl.animation({ leaf = "workspacesIn",  enabled = true, speed = 1.21, bezier = "almostLinear", style = "fade" })
hl.animation({ leaf = "workspacesOut", enabled = true, speed = 1.94, bezier = "almostLinear", style = "fade" })

-- Ref https://wiki.hypr.land/Configuring/Core/Rules/Workspace-Rules/
-- "Smart gaps" / "No gaps when only"
-- uncomment all if you wish to use that.
-- hl.workspace_rule({ workspace = "w[tv1]", gaps_out = 0, gaps_in = 0 })
-- hl.workspace_rule({ workspace = "f[1]",   gaps_out = 0, gaps_in = 0 })
-- hl.window_rule({ match = { float = false, workspace = "w[tv1]" }, border_size = 0, rounding = 0 })
-- hl.window_rule({ match = { float = false, workspace = "f[1]" },   border_size = 0, rounding = 0 })


---------------
---- INPUT ----
---------------

hl.config({
    input = {
        kb_layout  = "us,de",
        kb_variant = "",
        kb_model   = "",
        -- No grp:alt_shift_toggle: Alt+Shift is a bind below, which switches
        -- every keyboard at once.
        kb_options = "",
        kb_rules   = "",

        follow_mouse = 1,

        sensitivity = 0, -- -1.0 - 1.0, 0 means no modification.

        touchpad = {
            natural_scroll = true,
        },
    },
})


---------------------
---- KEYBINDINGS ----
---------------------

-- See https://wiki.hypr.land/Configuring/Core/Binds/ and
-- https://wiki.hypr.land/Configuring/Core/Dispatchers/
local mainMod = "SUPER" -- Sets "Windows" key as main modifier

hl.bind(mainMod .. " + Return", hl.dsp.exec_cmd(terminal))
hl.bind(mainMod .. " + Q",      hl.dsp.window.close())
hl.bind(mainMod .. " + E",      hl.dsp.exec_cmd(fileManager))
hl.bind(mainMod .. " + V",      hl.dsp.window.float({ action = "toggle" }))
hl.bind(mainMod .. " + Space",  hl.dsp.exec_cmd(menu))
hl.bind(mainMod .. " + L",      hl.dsp.exec_cmd("pidof hyprlock || hyprlock"))
-- The bar's power card. --key puts a card at the bar's right end, as the
-- pointer may be anywhere when a key opens it (bar-popup).
hl.bind(mainMod .. " + Escape", hl.dsp.exec_cmd("bar-popup power --key"))
-- The power key opens it too: logind leaves a short press to us
-- (HandlePowerKey=ignore, hosts/laptop/default.nix) and still powers off when the
-- key is held 5 s, the way out of a hung session. Not locked: on the lock
-- screen a short press does nothing.
hl.bind("XF86PowerOff", hl.dsp.exec_cmd("bar-popup power --key"))
-- The notification panel. An open card closes first, as opening a card
-- closes the panel, so the two never cover each other.
hl.bind(mainMod .. " + SHIFT + N", hl.dsp.exec_cmd("bar-popup close; swaync-client -t -sw"))
hl.bind(mainMod .. " + N",      hl.dsp.exec_cmd("~/.config/hypr/scripts/notes.sh"))
hl.bind(mainMod .. " + T",      hl.dsp.exec_cmd("~/.config/hypr/scripts/dict-lookup.sh"))
hl.bind(mainMod .. " + P",      hl.dsp.window.pseudo())          -- dwindle
-- The focus timer, as the pomodoro pill's middle click: starts it at the
-- last lengths, pauses it, resumes it. Not on the lock screen.
hl.bind(mainMod .. " + SHIFT + P", hl.dsp.exec_cmd("pomodoro-bar toggle"))
hl.bind(mainMod .. " + J",      hl.dsp.layout("togglesplit"))    -- dwindle

-- The bar's cards from the keyboard: SUPER+B, then a card's letter. While
-- the submap is on, the bar shows its name (waybar's hyprland/submap): the
-- legend built here from one table, each letter beside its module's glyph,
-- in the bar's order. It is kept compact (a thin space inside a pair, one
-- space between pairs, no title): a 361 px pill, which at 1920 px still
-- fits beside the left side's attention, privacy and camera pills; with a
-- track playing too there is no room, waybar shows "…", and the letters
-- work all the same. A letter opens its card and ends the submap
-- ("reset"), also with SUPER still held; the card then takes the keys
-- (Tab, arrows, Enter, Esc) as after a click. Any other key only ends it
-- (catchall; ignore_mods, so SUPER+Q too), a new press of SUPER as well,
-- so a stray SUPER+B costs one key press. The power card has SUPER+Escape
-- and the power key.
local bar_cards = {
    { "H", "system",    "\u{f035b}" }, -- md-memory: the hardware pill
    { "X", "attention", "\u{f0026}" }, -- md-alert
    { "N", "network",   "\u{f0928}" }, -- md-wifi_strength_4
    { "A", "battery",   "\u{f0079}" }, -- md-battery
    { "B", "bluetooth", "\u{f00af}" }, -- md-bluetooth
    { "S", "sound",     "\u{f057e}" }, -- md-volume_high
    { "D", "display",   "\u{f00df}" }, -- md-brightness_6
    { "W", "weather",   "\u{f0590}" }, -- md-weather_cloudy
    { "C", "calendar",  "\u{f00f6}" }, -- md-calendar_today: the clock
    { "P", "pomodoro",  "\u{f252}" },  -- fa-hourglass_half
}
local legend = {}
for _, card in ipairs(bar_cards) do legend[#legend + 1] = card[1] .. "\u{2009}" .. card[3] end
local bar_submap = table.concat(legend, " ")
hl.bind(mainMod .. " + B", hl.dsp.submap(bar_submap))
hl.define_submap(bar_submap, "reset", function()
    for _, card in ipairs(bar_cards) do
        local open = hl.dsp.exec_cmd("bar-popup " .. card[2] .. " --key")
        hl.bind(card[1], open)
        hl.bind(mainMod .. " + " .. card[1], open)
    end
    hl.bind("catchall", hl.dsp.submap("reset"), { ignore_mods = true })
end)

-- Move focus with mainMod + arrow keys
hl.bind(mainMod .. " + left",  hl.dsp.focus({ direction = "left" }))
hl.bind(mainMod .. " + right", hl.dsp.focus({ direction = "right" }))
hl.bind(mainMod .. " + up",    hl.dsp.focus({ direction = "up" }))
hl.bind(mainMod .. " + down",  hl.dsp.focus({ direction = "down" }))

-- Switch workspaces with mainMod + [0-9]
-- Move active window to a workspace with mainMod + SHIFT + [0-9]
for i = 1, 10 do
    local key = i % 10 -- 10 maps to key 0
    hl.bind(mainMod .. " + " .. key,         hl.dsp.focus({ workspace = i }))
    hl.bind(mainMod .. " + SHIFT + " .. key, hl.dsp.window.move({ workspace = i }))
end

-- Special workspace (scratchpad)
hl.bind(mainMod .. " + M",         hl.dsp.workspace.toggle_special("magic"))
hl.bind(mainMod .. " + SHIFT + M", hl.dsp.window.move({ workspace = "special:magic" }))

-- Move/resize windows with mainMod + LMB/RMB and dragging
hl.bind(mainMod .. " + mouse:272", hl.dsp.window.drag(),   { mouse = true })
hl.bind(mainMod .. " + mouse:273", hl.dsp.window.resize(), { mouse = true })

-- Screenshots with Hyprshot
hl.bind(mainMod .. " + S", hl.dsp.exec_cmd("hyprshot -m region -o ~/Pictures/Screenshots"))

-- Remap PgUp and PgDn to jump to beginning and end of line, respectively
hl.bind("Prior",         hl.dsp.exec_cmd("wtype -k Home"))
hl.bind("SHIFT + Prior", hl.dsp.exec_cmd("wtype -M shift -k Home"))
hl.bind("Next",          hl.dsp.exec_cmd("wtype -k End"))
hl.bind("SHIFT + Next",  hl.dsp.exec_cmd("wtype -M shift -k End"))

-- Keyboard layout: Alt+Shift, as xkb's grp:alt_shift_toggle had it (Alt
-- then either Shift, or Shift then Left Alt: Right Alt is the de layout's
-- AltGr), but for every keyboard at once, and only as a chord of its own.
-- The xkb toggle switched only the keyboard it was pressed on, while the
-- laptop has nine (hotkeys, power button, ...) and the bar's language pill
-- shows the power button's. Every keyboard goes to the layout after the one
-- the keyboard in use has ("main" in `hyprctl devices`), so one plugged in
-- since, which starts on the first, falls in line too (kb-layout-sync,
-- modules/home/desktop.nix, gives it the layout in use a second after it comes).
-- The pill's click flips the layout the pill shows instead.
local next_layout = [[hyprctl switchxkblayout all $(hyprctl -j devices | jq '.keyboards[] | select(.main) | (.active_layout_index + 1) % (.layout | split(",") | length)')]]
-- A chord of its own: the layout switches when the two are let go with no
-- other key or click in between. Switching on the press, as before, made
-- every Alt+Shift shortcut switch it too (VS Code's Shift+Alt+Right/Left,
-- Shift+Alt+I, Shift+Alt+drag). "input.keyboard.key" reaches Lua before
-- the binds do, so any other key disarms the chord first; a click does
-- through the mouse binds. non_consuming: apps still get the Shift and Alt
-- presses and the clicks; locked: it also works on the lock screen, for a
-- password typed in the other layout (hyprlock.conf shows DE there).
local CHORD_KEYS = { [50] = true, [62] = true, [64] = true, [108] = true } -- xkb: Shift_L/R, Alt_L/R
local chord = false
hl.on("input.keyboard.key", function(keycode, _, state)
    if state == 1 and not CHORD_KEYS[keycode] then chord = false end
end)
local function arm() chord = true end
local function disarm() chord = false end
local function fire()
    if chord then
        chord = false
        hl.exec_cmd(next_layout)
    end
end
local arm_opts  = { non_consuming = true, locked = true }
-- transparent: every release re-shadows the binds whose key is still held,
-- so after a capital letter (Shift held while A came up) letting go of
-- Shift would not count.
local fire_opts = { non_consuming = true, locked = true, release = true, transparent = true }
hl.bind("ALT + Shift_L", arm, arm_opts)
hl.bind("ALT + Shift_R", arm, arm_opts)
hl.bind("SHIFT + Alt_L", arm, arm_opts)
-- At the release the mods still hold the key let go (Hyprland updates them
-- after the key event), hence ALT + SHIFT.
hl.bind("ALT + SHIFT + Shift_L", fire, fire_opts)
hl.bind("ALT + SHIFT + Shift_R", fire, fire_opts)
hl.bind("ALT + SHIFT + Alt_L", fire, fire_opts)
hl.bind("ALT + SHIFT + mouse:272", disarm, { non_consuming = true })
hl.bind("ALT + SHIFT + mouse:273", disarm, { non_consuming = true })

-- Laptop keys. osd (waybar-popups) makes the change and shows the level in
-- a transient swaync popup that each press replaces. locked: they also work
-- on the lock screen; repeating: holding a key keeps stepping.
local key_flags = { repeating = true, locked = true }
hl.bind("XF86AudioRaiseVolume",  hl.dsp.exec_cmd("osd volume up"),       key_flags)
hl.bind("XF86AudioLowerVolume",  hl.dsp.exec_cmd("osd volume down"),     key_flags)
hl.bind("XF86AudioMute",         hl.dsp.exec_cmd("osd volume mute"),     { locked = true })
hl.bind("XF86AudioMicMute",      hl.dsp.exec_cmd("osd mic mute"),        { locked = true })
hl.bind("XF86MonBrightnessUp",   hl.dsp.exec_cmd("osd brightness up"),   key_flags)
hl.bind("XF86MonBrightnessDown", hl.dsp.exec_cmd("osd brightness down"), key_flags)
hl.bind("XF86AudioPlay",  hl.dsp.exec_cmd("playerctl play-pause"), { locked = true })
hl.bind("XF86AudioPause", hl.dsp.exec_cmd("playerctl play-pause"), { locked = true })
hl.bind("XF86AudioNext",  hl.dsp.exec_cmd("playerctl next"),       { locked = true })
hl.bind("XF86AudioPrev",  hl.dsp.exec_cmd("playerctl previous"),   { locked = true })


--------------------------------
---- WINDOWS AND WORKSPACES ----
--------------------------------

-- See https://wiki.hypr.land/Configuring/Core/Rules/Window-Rules/ and
-- https://wiki.hypr.land/Configuring/Core/Rules/Layer-Rules/

-- swaync's panel and popups draw a translucent base (alpha(@base, 0.92 and
-- 0.95), swaync/style.css), so Hyprland blurs what shows through them;
-- ignore_alpha 0 blurs behind every pixel that isn't fully clear. The bar
-- has no layer rule: its pills are opaque surface0.
hl.layer_rule({ match = { namespace = "swaync-control-center" },     blur = true, ignore_alpha = 0 })
hl.layer_rule({ match = { namespace = "swaync-notification-window" }, blur = true, ignore_alpha = 0 })

-- The bar's cards stay out of screen shares and recordings, as the popups
-- swaync holds back do: the network card shows addresses and network
-- names, the Bluetooth card device names. A capture, a screenshot too,
-- gets a black box in the card's place. Only the card's own surface is
-- named "bar-popup" (bar-popup.py); the click-catching backdrops,
-- "bar-popup-backdrop", fill whole monitors and must stay out of this
-- rule, or the whole shared screen would turn black.
hl.layer_rule({ match = { namespace = "bar-popup" }, no_screen_share = true })

-- Ignore maximize requests from apps. You'll probably like this.
hl.window_rule({ match = { class = ".*" }, suppress_event = "maximize" })
hl.window_rule({
    match  = { title = "(about:blank)" },
    float  = true,
    center = true,
    size   = { 800, 600 },
})

-- Keep the file manager fully opaque so the Catppuccin theme isn't washed out
hl.window_rule({ match = { class = "org.gnome.Nautilus" }, opacity = "1.0 override 1.0 override" })

-- Video players must never inherit active_opacity/inactive_opacity (0.95/0.8),
-- and rounding/border would clip the picture's corners and frame it.
for _, player in ipairs({ "^(mpv)$", "^(io.github.celluloid_player.Celluloid)$" }) do
    hl.window_rule({
        match       = { class = player },
        opacity     = "1.0 override 1.0 override",
        opaque      = true,
        rounding    = 0,
        border_size = 0,
    })
end

-- Fix some dragging issues with XWayland
hl.window_rule({
    match = {
        class      = "^$",
        title      = "^$",
        xwayland   = true,
        float      = true,
        fullscreen = false,
        pin        = false,
    },
    no_initial_focus = true,
})

-- Dictionary lookup modal (SUPER+T); border matches the wofi launcher
hl.window_rule({
    match        = { class = "^(dict-modal)$" },
    border_color = "rgb(cba6f7)",
    float        = true,
    center       = true,
    size         = { 1100, 620 },
})

-- Quick notes modal (SUPER+N), styled like the dictionary modal
hl.window_rule({
    match        = { class = "^(notes-modal)$" },
    border_color = "rgb(cba6f7)",
    float        = true,
    center       = true,
    size         = { 1100, 620 },
})

-- The attention module's details (its right click, and the Attention
-- card's terminal button) and the card's unit logs (journalctl): kitty,
-- floating like the dictionary modal
hl.window_rule({
    match        = { class = "^(attention-details)$" },
    border_color = "rgb(cba6f7)",
    float        = true,
    center       = true,
    size         = { 1100, 620 },
})

-- Tecla, the keyboard map the bar's layout pill opens on a right click
-- (where @ is on DE): floating like the dictionary modal, at its own
-- 800x400, which holds the whole keyboard; Escape closes it.
hl.window_rule({
    match        = { class = "^(org.gnome.Tecla)$" },
    border_color = "rgb(cba6f7)",
    float        = true,
    center       = true,
})

-- Don't use fractional scaling for xwayland applications like steam
hl.config({
    xwayland = {
        force_zero_scaling = true,
    },
})
