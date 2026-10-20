#!/usr/bin/env python3
"""agents-busy: exit 0 while a coding agent is at work, 1 otherwise.

hypridle's battery suspend asks it first (modules/home/desktop.nix). omp no longer
holds a logind idle lock while it works (its power.sleepPrevention is
off), so during an agent run the screen locks and turns off as in any
idle stretch; only the suspend waits until no agent works.

Stateless: each call looks at what the agents show right now. Nothing
runs in between and nothing is kept.
- omp shows its run state in its terminal's title (its setting
  tui.titleState, on by default): "π ⠋ name", a spinner frame, while it
  works, "π > name" when it's your turn and "π ! name" while it waits on
  you. Hyprland has every window's title.
- Orca keeps each agent pane's latest hook report in
  agent-hooks/last-status.json, for every agent it runs (omp, Claude
  Code), with the agent's state.

Whatever can't be read counts as idle: a stale "busy" would keep a laptop
on battery awake. Not seen: an omp in a kitty tab that isn't the one
shown (the window carries that tab's title only), or one with no
terminal window at all.

  agents-busy      the exit status only
  agents-busy -v   also print each agent at work, one per line
"""

import json
import os
import sys
import time

# Next to this file; on PYTHONPATH when packaged. osd's Hyprland socket
# helper, bar-popup's and nightlight's too, needs no GTK.
from osd import hyprland

OMP = "π "  # omp's terminal title: then its run state, then the session
OMP_NOT_WORKING = (">", "!")  # your turn, waiting on you; else a frame

ORCA_DIR = os.path.expanduser("~/.config/orca")
ORCA_RUNTIME = os.path.join(ORCA_DIR, "orca-runtime.json")  # its pid
ORCA_STATUS = os.path.join(ORCA_DIR, "agent-hooks", "last-status.json")
# Orca's states of an agent at work; "monitoring": watching background
# tasks it started.
ORCA_WORKING = {"working", "monitoring"}


def omp_working():
    """The sessions of the omp windows at work, by their titles."""
    clients = hyprland("j/clients")
    out = []
    for client in clients if isinstance(clients, list) else ():
        title = client.get("title") if isinstance(client, dict) else None
        if not isinstance(title, str) or not title.startswith(OMP):
            continue
        state, _, name = title[len(OMP):].partition(" ")
        # A spinner frame is one character (a static ":" under WSL): a
        # longer word is a title of something else that starts with π.
        if len(state) == 1 and state not in OMP_NOT_WORKING:
            out.append(f"omp · {name or 'session'}")
    return out


def alive(pid):
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    return True


def orca_alive():
    """Whether the Orca that keeps last-status.json runs: its entries
    outlive it, and a pid file older than this boot names a pid that
    something else may have now."""
    try:
        with open(ORCA_RUNTIME, "rb") as fh:
            pid = json.load(fh)["pid"]
        written = os.stat(ORCA_RUNTIME).st_mtime
        with open("/proc/uptime", "rb") as fh:
            booted = time.time() - float(fh.read().split()[0])
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return False
    return written > booted and alive(pid)


def orca_working():
    """'Orca · worktree · agent' for each Orca pane whose agent is at
    work, itself or through subagents it waits for."""
    if not orca_alive():
        return []
    try:
        with open(ORCA_STATUS, "rb") as fh:
            entries = list(json.load(fh)["entries"].values())
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return []
    out = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        presence = entry.get("agentPresence") or {}
        payload = entry.get("payload") or {}
        pid = (presence.get("process") or {}).get("pid")
        if presence.get("ended") or (pid is not None and not alive(pid)):
            continue
        subagents = payload.get("subagents") or ()
        if payload.get("state") in ORCA_WORKING or any(
                isinstance(s, dict) and s.get("state") in ORCA_WORKING
                for s in subagents):
            worktree = str(entry.get("worktreeId") or "").partition("::")[2]
            place = os.path.basename(worktree.rstrip("/")) or "Orca"
            agent = payload.get("agentType") or entry.get("source") or "agent"
            out.append(f"Orca · {place} · {agent}")
    return out


def main(argv):
    if argv[1:] not in ([], ["-v"]):
        print("usage: agents-busy [-v]", file=sys.stderr)
        return 2
    working = omp_working() + orca_working()
    if argv[1:] == ["-v"]:
        for line in working:
            print(line)
    return 0 if working else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
