-- The PC's part of the Hyprland config: its screens, colour management and
-- the iPhone HID key. modules/home/desktop.nix puts it ahead of the shared
-- dotfiles/hypr/hyprland.lua, in the same Lua chunk.

------------------
---- MONITORS ----
------------------

-- Both panels are keyed by their EDID description, not the connector: the
-- connector names change with the GPU setup (the 1080 Ti alone, as now: DP-1
-- and DP-2; the iGPU alone: DP-1 and HDMI-A-2; both: the card's from DP-3
-- on), and the 27E1Q has shared HDMI-A-2 with the LG TV, where a
-- connector-name rule pinned to the TV's 1080p left it off its native 1440p.
-- desc: rules match on a prefix and win over name rules.
hl.monitor({ output = "desc:HKC OVERSEAS LIMITED 27E1Q", mode = "2560x1440@144", position = "2048x0", scale = 1.25 })
-- (The 2026-09 cap to 1440p@60 on the 1080 Ti was nvidia-pstated pinning the
-- display clock.)
hl.monitor({ output = "desc:HKC OVERSEAS LIMITED GN07", mode = "2560x1440@180", position = "0x0", scale = 1.25 })
-- Fallback for the TV (and anything else on that port): let Hyprland pick the
-- EDID-preferred mode.  `play` overrides it to 4K/23.976 for movies anyway and
-- restores whatever was active before playback.
hl.monitor({ output = "HDMI-A-2", mode = "preferred", position = "2048x0", scale = 1.25 })

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

-- No LIBVA_DRIVER_NAME: libva picks the VA-API driver of the GPU in use, NVDEC
-- (nvidia-vaapi-driver) with the 1080 Ti in, iHD on the iGPU with it out. A
-- fixed name breaks the other setup: "nvidia" failed with the card out, and
-- "iHD" fails with it in (the iGPU is off then), which left Firefox decoding
-- video in software.


---------------------
---- KEYBINDINGS ----
---------------------

-- Super+I is handled by the root iphone-hid service, which reads the keyboard
-- directly; this no-op bind only keeps the key combo from reaching the app.
hl.bind("SUPER + I", hl.dsp.exec_cmd("true"))
