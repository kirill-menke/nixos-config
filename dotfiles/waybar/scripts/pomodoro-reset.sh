#!/usr/bin/env bash
# Right-click action: cancel any running timer and return to idle.
STATE="${XDG_RUNTIME_DIR:-/tmp}/pomodoro-state.json"
rm -f "$STATE" "$STATE.tmp"
