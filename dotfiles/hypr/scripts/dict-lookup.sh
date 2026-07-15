#!/usr/bin/env bash
# dict.cc DE-EN lookup in a floating kitty window (SUPER+T).
# Data: ~/.local/share/dictcc/de-en.tsv — download from
# https://www1.dict.cc/translation_file_request.php (personal use only,
# must not be redistributed, so it lives outside the nixos-config repo).

DICT="$HOME/.local/share/dictcc/de-en.tsv"
STATE="${XDG_RUNTIME_DIR:-/tmp}/dict-modal-collapsed"

# --search <query>: called by fzf on every keystroke; prints matches
# grouped under category headers (Nouns, Verbs, ...).
if [[ "$1" == "--search" ]]; then
    q="$2"
    [[ ${#q} -lt 2 ]] && exit 0
    grep -iF -- "$q" "$DICT" | awk -F'\t' -v q="$q" '
        BEGIN { lq = tolower(q) }
        # 0: whole field  1: first word  2: prefix inside a longer word
        # 3: elsewhere in the field  9: no match on this side
        function rank(f,   c) {
            if (f == lq) return 0
            if (index(f, lq) == 1) {
                c = substr(f, length(lq) + 1, 1)
                return c ~ /[[:alpha:]äöüß-]/ ? 2 : 1
            }
            return index(f, lq) ? 3 : 9
        }
        {
            d = tolower($1); e = tolower($2)
            if (!index(d, lq) && !index(e, lq)) next   # only match DE/EN, not tags
            cls = $4; sub(/ .*/, "", cls)
            if      (cls == "noun") cat = 1
            else if (cls == "verb") cat = 2
            else if (cls == "adj")  cat = 3
            else if (cls == "adv")  cat = 4
            else                    cat = 5
            rd = rank(d); re = rank(e)
            r = rd < re ? rd : re
            # matched side goes first; rank by the frequency of the
            # other side (the translation)
            if (rd <= re) { m1 = $1; m2 = $2; s = $6 }
            else          { m1 = $2; m2 = $1; s = $5 }
            if ($3 ~ /\[F\]/) s = 0   # fiction/title entries rank last
            printf "%d\t%d\t%d\t%d\t%s\t%s\t%s\n", cat, r, s, length($1) + length($2), m1, m2, $3
        }' | sort -t$'\t' -k2,2n -k3,3nr -k4,4n | \
        awk -F'\t' -v q="$q" -v col=" $(tr '\n' ' ' < "$STATE" 2>/dev/null) " '
        BEGIN {
            lq = tolower(q)
            name[1] = "Nouns"; name[2] = "Verbs"; name[3] = "Adjectives"
            name[4] = "Adverbs"; name[5] = "Other"
        }
        # Input is sorted by match quality alone, so categories are
        # queued in order of their best entry and printed best-first.
        # NUL-separated records for fzf --read0; $5 = matched side,
        # $6 = translation, $7 = subject tags
        {
            cat = $1
            if (!(cat in seen)) { seen[cat] = ++n; cats[n] = cat }
            if (++count[cat] > 30) next
            item = "  " $6
            if ($7 != "") item = item "\t\033[2m" $7 "\033[0m"
            if (tolower($5) != lq)
                item = item "\n      \033[3;38;2;249;226;175m" $5 "\033[0m"
            buf[cat] = buf[cat] item "\0"
        }
        END {
            for (i = 1; i <= n; i++) {
                cat = cats[i]
                if (index(col, " " cat " "))
                    printf "\033[1;35m▸ %s\033[0m\0", name[cat]
                else
                    printf "\033[1;35m▾ %s\033[0m\0%s", name[cat], buf[cat]
            }
        }'
    exit 0
fi

# --toggle <query> <item>: bound to Enter in fzf. On a category header,
# collapse/expand it and reload; on an entry, emit no action at all.
if [[ "$1" == "--toggle" ]]; then
    q="$2" item="$3"
    [[ "$item" == "▾ "* || "$item" == "▸ "* ]] || exit 0
    case "${item:2}" in
        Nouns) cat=1 ;; Verbs) cat=2 ;; Adjectives) cat=3 ;;
        Adverbs) cat=4 ;; Other) cat=5 ;; *) exit 0 ;;
    esac
    if grep -qx "$cat" "$STATE" 2>/dev/null; then
        grep -vx "$cat" "$STATE" > "$STATE.tmp"; mv "$STATE.tmp" "$STATE"
    else
        echo "$cat" >> "$STATE"
    fi
    echo "reload:$0 --search '${q//\'/\'\\\'\'}'"
    exit 0
fi

if [[ "$1" != "--inner" ]]; then
    exec kitty --class=dict-modal --title=Dictionary \
        -o window_padding_width=12 "$0" --inner
fi

if [[ ! -r "$DICT" ]]; then
    notify-send "Dictionary" "Data file missing: $DICT"
    exit 1
fi

: > "$STATE"   # start with all categories expanded

fzf --disabled --ansi --read0 --layout=reverse --no-sort \
    --prompt='❯ ' --info=hidden --highlight-line \
    --bind "change:reload:$0 --search {q}" \
    --bind "enter:transform:$0 --toggle {q} {}" \
    --color=bg+:#313244,bg:#1e1e2e,hl:#f38ba8 \
    --color=fg:#cdd6f4,header:#6c7086,pointer:#f5e0dc \
    --color=fg+:#cdd6f4,prompt:#f38ba8,hl+:#f38ba8 \
    < /dev/null > /dev/null
exit 0
