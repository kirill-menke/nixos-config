#!/bin/sh
# bar-popup NAME [--key]: open the card NAME under the pointer (at the bar's
# right end with --key, which the key bindings pass), swap to it, or close
# it if it is the one open. bar-popup close: close any card; the bell runs
# it as it opens swaync's panel.
#
# The cards' process, bar-popup-host (bar-popup.py), stays once started,
# and busctl hands it the request in a few ms, where starting Python for
# that took ~0.5 s a click. Only while none runs is it started here, and it
# opens the card from its own command line; `close` then has nothing to
# close. The package (modules/home/desktop.nix) swaps the busctl and the host below
# for their store paths, and its build fails if those lines change.
[ -n "$1" ] || { echo "usage: bar-popup NAME [--key] | close" >&2; exit 2; }
call() {
    busctl --user call com.kirill.barpopup /com/kirill/barpopup \
        org.freedesktop.Application ActivateAction 'sava{sv}' "$@" 0 \
        >/dev/null 2>&1
}
case "$1 $2" in
"close ") call close 0; exit 0 ;;
*" --key") call key 1 s "$1" ;;
*) call toggle 1 s "$1" ;;
esac || exec bar-popup-host "$@"
