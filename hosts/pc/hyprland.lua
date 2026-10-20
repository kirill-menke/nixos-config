-- The PC's part of the Hyprland config: its screens, colour management, the
-- cursor, the VA-API driver and the iPhone HID key. modules/home/desktop.nix
-- puts it ahead of the shared dotfiles/hypr/hyprland.lua, in the same Lua
-- chunk.

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


-------------------------------
---- ENVIRONMENT VARIABLES ----
-------------------------------

-- XCursor twin of the hyprcursor theme (home.pointerCursor in home.nix), for
-- clients that draw their own cursor: GTK3 apps such as waybar.
hl.env("XCURSOR_THEME", "BreezeX-RosePine-Linux")
hl.env("XCURSOR_SIZE", "24")
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


---------------------
---- KEYBINDINGS ----
---------------------

-- Super+I is handled by the root iphone-hid service, which reads the keyboard
-- directly; this no-op bind only keeps the key combo from reaching the app.
hl.bind("SUPER + I", hl.dsp.exec_cmd("true"))
