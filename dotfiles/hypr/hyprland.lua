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
    },
})

------------------
---- MONITORS ----
------------------

-- NVIDIA monitors
hl.monitor({ output = "DP-3", mode = "2560x1440@180", position = "0x0",    scale = 1.25 })
hl.monitor({ output = "DP-4", mode = "2560x1440@144", position = "2048x0", scale = 1.25 })

-- iGPU monitors (NVIDIA pulled for freeze diagnosis 2026-07-12)
hl.monitor({ output = "DP-1", mode = "2560x1440@180", position = "0x0", scale = 1.25 })

-- HDMI-A-2 is shared: the 27E1Q monitor normally, the LG TV when it gets
-- plugged in temporarily.  Keying the mode to the EDID description instead of
-- the connector means neither one inherits the other's mode after a swap --
-- a connector-name rule pinned to the TV's 1080p left the monitor off its
-- native 1440p.  desc: rules match on a prefix and win over name rules.
hl.monitor({ output = "desc:HKC OVERSEAS LIMITED 27E1Q", mode = "2560x1440@144", position = "2048x0", scale = 1.25 })
-- Keyed by description so the mode follows the panel to whatever port it is
-- on; desc rules beat the connector-name rules above.  (The 2026-09 cap to
-- 1440p@60 on the 1080 Ti was nvidia-pstated pinning the display clock.)
hl.monitor({ output = "desc:HKC OVERSEAS LIMITED GN07", mode = "2560x1440@180", position = "0x0", scale = 1.25 })
-- Fallback for the TV (and anything else on that port): let Hyprland pick the
-- EDID-preferred mode.  `play` overrides it to 4K/23.976 for movies anyway and
-- restores whatever was active before playback.
hl.monitor({ output = "HDMI-A-2", mode = "preferred", position = "2048x0", scale = 1.25 })
-- hl.monitor({ output = "Unknown-1", disabled = true })

-- Colour management pipeline -- required for any HDR output.  Takes effect
-- only after a full Hyprland restart, not `hyprctl reload`.
-- render.cm_auto_hdr defaults to 1, so the output switches into HDR by itself
-- when a fullscreen app signals HDR content (mpv does this via
-- target-colorspace-hint) and stays ordinary SDR the rest of the time.
hl.config({
    render = {
        cm_enabled = true,
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
-- swaync is started by its home-manager systemd user service.
hl.on("hyprland.start", function()
    hl.exec_cmd("waybar")
    hl.exec_cmd("udiskie --tray --notify --automount")
end)


-------------------------------
---- ENVIRONMENT VARIABLES ----
-------------------------------

-- See https://wiki.hypr.land/Configuring/Core/Environment-variables/
-- hl.env("XCURSOR_THEME", "Bibata-Modern-Classic")
-- hl.env("XCURSOR_SIZE", "48")
hl.env("HYPRCURSOR_THEME", "rose-pine-hyprcursor")
hl.env("HYPRCURSOR_SIZE", "24")
-- iHD is the VAAPI driver for Gen8+ Intel graphics (Alder Lake UHD 730).
-- This said `nvidia` while the 1080 Ti was installed; with the card gone that
-- forced VAAPI to load a driver that does not exist here, so `Could not create
-- device` and every video silently fell back to software decoding.
hl.env("LIBVA_DRIVER_NAME", "iHD")
-- NVIDIA-only, restore these together with the card:
-- hl.env("LIBVA_DRIVER_NAME", "nvidia")
-- hl.env("__GLX_VENDOR_LIBRARY_NAME", "nvidia")
-- hl.env("NVD_BACKEND", "direct")
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
        kb_options = "grp:alt_shift_toggle",
        kb_rules   = "",

        follow_mouse = 1,

        sensitivity = 0, -- -1.0 - 1.0, 0 means no modification.

        touchpad = {
            natural_scroll = false,
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
hl.bind(mainMod .. " + L",      hl.dsp.exec_cmd("hyprlock"))
hl.bind(mainMod .. " + Escape", hl.dsp.exec_cmd("~/.config/hypr/scripts/power-menu.sh"))
hl.bind(mainMod .. " + N",      hl.dsp.exec_cmd("swaync-client -t -sw"))
hl.bind(mainMod .. " + T",      hl.dsp.exec_cmd("~/.config/hypr/scripts/dict-lookup.sh"))
hl.bind(mainMod .. " + P",      hl.dsp.window.pseudo())          -- dwindle
hl.bind(mainMod .. " + J",      hl.dsp.layout("togglesplit"))    -- dwindle

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


--------------------------------
---- WINDOWS AND WORKSPACES ----
--------------------------------

-- See https://wiki.hypr.land/Configuring/Core/Rules/Window-Rules/ and
-- https://wiki.hypr.land/Configuring/Core/Rules/Layer-Rules/

-- swaync sits on blur like the waybar pills
hl.layer_rule({ match = { namespace = "swaync-control-center" },     blur = true, ignore_alpha = 0 })
hl.layer_rule({ match = { namespace = "swaync-notification-window" }, blur = true, ignore_alpha = 0 })

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

-- Video players must never inherit active_opacity/inactive_opacity (0.95/0.8).
-- mpv-atmos deliberately plays in a floating, output-sized window rather than
-- real fullscreen (fullscreen kills HDR, see home.nix), so while you work on
-- DP-1 the movie counts as *unfocused* and would otherwise be drawn at 0.8.
--
-- Same reason: a floating player window still gets rounding = 10 and
-- border_size = 2, which clip the picture's corners and draw a frame around it.
-- Real fullscreen would suppress both, but that kills HDR (see above).
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

-- Start affinity as a tile window
hl.window_rule({ match = { class = "^(affinity.exe)$" }, tile = true })

-- Dictionary lookup modal (SUPER+T); border matches the wofi launcher
hl.window_rule({
    match        = { class = "^(dict-modal)$" },
    border_color = "rgb(cba6f7)",
    float        = true,
    center       = true,
    size         = { 1100, 620 },
})

-- Don't use fractional scaling for xwayland applications like steam
hl.config({
    xwayland = {
        force_zero_scaling = true,
    },
})
