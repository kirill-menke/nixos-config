# Run by `wl-paste --primary --watch` (the primary-marked user service,
# modules/home/desktop.nix) for every new primary selection, that is each
# time text is marked anywhere. It notes the time in
# $XDG_RUNTIME_DIR/primary-marked for the middle click that translates text
# marked moments ago (hyprland.lua). No note for a selection without text:
# Firefox announces an empty one of its own at times (seen 2026-11-05),
# and a middle click would then translate the clipboard. Nor for one that
# a password manager flags as sensitive, or for none at all; a middle click
# then stays a plain click.
marked="$XDG_RUNTIME_DIR/primary-marked"
# The selection itself comes on stdin: grep stops at its first letter, cat
# takes the rest, so the app that marked it can hand all of it over.
if [ "${CLIPBOARD_STATE:-}" = data ] && grep -q '[^[:space:]]'; then
    date +%s > "$marked.tmp"
    mv "$marked.tmp" "$marked"  # never half written when Hyprland reads it
else
    rm -f "$marked"
fi
cat > /dev/null
