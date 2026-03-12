#!/usr/bin/env bash
set -euo pipefail

HYPR_DIR="${HOME}/.config/hypr"
SPLASHES="${HYPR_DIR}/splashes.txt"
SOURCE="${HOME}/Pictures/backgrounds/background.jpg"
CACHE_DIR="${HOME}/.cache/hypr"
OUTPUT="${CACHE_DIR}/daily-wallpaper.jpg"

mkdir -p "$CACHE_DIR"

# --- Parse today's entry ---
TOTAL=$(grep -c "^No\. " "$SPLASHES")
if [[ "$TOTAL" -eq 0 ]]; then
  echo "No splash entries found in $SPLASHES" >&2
  exit 1
fi

DAY=$(( 10#$(date +%j) ))           # 1–366, forced base-10
IDX=$(( (DAY - 1) % TOTAL + 1 ))   # 1-indexed entry to display

LINE_NUM=$(grep -n "^No\. " "$SPLASHES" | sed -n "${IDX}p" | cut -d: -f1)
NUMBER=$(sed -n  "${LINE_NUM}p"         "$SPLASHES")
HEADING=$(sed -n "$((LINE_NUM + 1))p"   "$SPLASHES")

# Read body: everything from line LINE_NUM+2 until the next --- separator
BODY=$(awk -v start="$((LINE_NUM + 2))" '
  NR < start     { next }
  /^---$/        { exit }
  NR == start    { buf = $0; next }
                 { buf = buf "\n" $0 }
  END { gsub(/^\n+|\n+$/, "", buf); printf "%s", buf }
' "$SPLASHES")

# --- Resolve fonts via fontconfig ---
FONT_REGULAR=$(fc-match --format="%{file}" "JetBrains Mono:style=Regular")
FONT_BOLD=$(fc-match    --format="%{file}" "JetBrains Mono:style=Bold")

# --- Image dimensions ---
read -r IMG_W IMG_H < <(magick identify -format "%w %h\n" "$SOURCE")

# --- Generate body caption first to measure its actual height ---
BODY_W=700
BODY_CAPTION="${CACHE_DIR}/body-caption.png"

magick -background none \
  -fill "rgba(255,255,255,0.85)" \
  -font "$FONT_REGULAR" \
  -pointsize 15 \
  -gravity Center \
  -size "${BODY_W}x" \
  caption:"$BODY" \
  "$BODY_CAPTION"

BODY_IMG_H=$(magick identify -format "%h" "$BODY_CAPTION")

# Layout: bottom-center, offsets measured upward from image bottom (South gravity)
BODY_Y=40                                   # bottom edge of body block
HEAD_Y=$(( BODY_Y + BODY_IMG_H + 18 ))     # heading baseline
NUM_Y=$(( HEAD_Y + 28 ))                    # number baseline

# --- Composite onto wallpaper ---
magick "$SOURCE" \
  \
  `# Number label — shadow then text` \
  -fill "rgba(0,0,0,0.65)" \
  -font "$FONT_REGULAR" \
  -pointsize 15 \
  -gravity South \
  -annotate "+2+$(( NUM_Y - 2 ))" "$NUMBER" \
  -fill "rgba(255,255,255,0.55)" \
  -annotate "+0+${NUM_Y}" "$NUMBER" \
  \
  `# Heading — shadow then text` \
  -fill "rgba(0,0,0,0.65)" \
  -font "$FONT_BOLD" \
  -pointsize 21 \
  -gravity South \
  -annotate "+2+$(( HEAD_Y - 2 ))" "$HEADING" \
  -fill white \
  -annotate "+0+${HEAD_Y}" "$HEADING" \
  \
  `# Body caption (pre-rendered, center-aligned, variable height)` \
  "$BODY_CAPTION" \
  -gravity South \
  -geometry "+0+${BODY_Y}" \
  -composite \
  -quality 95 \
  "$OUTPUT"

# --- Reload hyprpaper (only works inside a running Hyprland session) ---
# Restarting is simpler than preload/unload since the output path is fixed
if [[ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]]; then
  systemctl --user restart hyprpaper
fi
