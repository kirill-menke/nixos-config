"""Claude usage for the Glance card: weekly limit and who spent it.

    claude-usage collect DIR   NAS: write DIR/usage.json (claude-usage.nix)
    claude-usage scan          print this host's omp spend as JSON; the PC
                               pipes it to DIR/pc.json on the NAS

collect asks `omp usage` for the limits of the Claude account the NAS is
logged into (the weekly one, account-wide whichever machine spent it), then
adds up what the clients logged since that window began:

  omp sessions       every assistant message carries its cost
                     (~/.omp/agent/sessions; here and, via pc.json, the PC's)
  Claude Code        each transcript ends with a cost-state line
                     (~/.claude/projects; auto-apply's `claude -p` calls)

Costs are the API-price equivalents the clients compute, not money charged:
the plan bills none of it. Paperless's bridge keeps no transcript, so its
few calls are missing. The PC's sessions name the account they ran on only
as a hash ("credential_pin"); what ran on another account than the NAS's,
or in a session that names none, is left out.

A failed run writes {"ok": false, "error": ...} and exits 1; the Gatus check
on usage.json requires `ok`, so the phone hears about that as well.

collect also asks `claude auth status` for Claude Code's own login, the one
auto-apply's and Paperless's `claude -p` calls run on. That login ends after
a fixed time (28 days in November 2026) and nothing renews it: every call
fails from then on, until someone runs `claude auth login` on this box. Two
more Gatus checks read it: `login.lasting` turns false LOGIN_WARN_DAYS
before the end, `login.valid` at the end.
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime

HOME = os.path.expanduser("~")
OMP_SESSIONS = os.path.join(HOME, ".omp/agent/sessions")
CLAUDE_PROJECTS = os.path.join(HOME, ".claude/projects")
OMP = os.environ.get("OMP_BIN", "omp")
CLAUDE = os.environ.get("CLAUDE_BIN", "claude")
WEEK = 7 * 24 * 3600
# What `scan` reports: the current window can have begun up to a week ago.
SCAN_DAYS = 8
NIGHTLY_MARK = "/profile-nightly/run/prompt.md"
PC_SOURCES = 4
# gatus.nix names this number in its check "Code login lasts over 3 days".
LOGIN_WARN_DAYS = 3


def project_of(folder):
    """-Documents-projects-auto-apply -> auto-apply, -tmp -> tmp."""
    name = folder.strip("-") or "home"
    if "projects-" in name:
        name = name.split("projects-", 1)[1]
    return name[:28]


def hour_of(epoch):
    return time.strftime("%Y-%m-%dT%H", time.gmtime(epoch))


def read_omp_session(path, project, spend, newest):
    pin = ""
    prompt_seen = False
    with open(path, encoding="utf-8", errors="replace") as lines:
        for line in lines:
            if prompt_seen and '"usage"' not in line and not line.startswith(
                    '{"type":"credential_pin"'):
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("type") == "credential_pin":
                pin = (record.get("hash") or "")[:12]
                continue
            message = record.get("message")
            if not isinstance(message, dict):
                continue
            if not prompt_seen and message.get("role") == "user":
                prompt_seen = True
                if NIGHTLY_MARK in json.dumps(message.get("content")):
                    project = "profile-nightly"
                continue
            usage = message.get("usage")
            if (message.get("role") != "assistant"
                    or message.get("provider") != "anthropic"
                    or not isinstance(usage, dict)):
                continue
            stamp = record.get("timestamp") or ""
            if pin and stamp > newest[0]:
                newest[:] = [stamp, pin]
            cost = (usage.get("cost") or {}).get("total") or 0
            if cost:
                key = (stamp[:13], pin, project)
                spend[key] = spend.get(key, 0) + cost


def omp_spend(since):
    """{(UTC hour, account pin, project): usd} of sessions touched since,
    and the pin of the newest request."""
    spend = {}
    newest = ["", ""]
    for folder, _dirs, files in os.walk(OMP_SESSIONS):
        top = os.path.relpath(folder, OMP_SESSIONS).split(os.sep)[0]
        for name in files:
            path = os.path.join(folder, name)
            try:
                if name.endswith(".jsonl") and os.path.getmtime(path) >= since:
                    read_omp_session(path, project_of(top), spend, newest)
            except OSError:
                continue
    return spend, newest[1]


def read_transcript(path):
    """(UTC hour it began, entrypoint, usd) of one Claude Code transcript."""
    stamp = entry = None
    usd = 0
    with open(path, encoding="utf-8", errors="replace") as lines:
        for line in lines:
            if '"type":"cost-state"' in line:
                try:
                    usd = json.loads(line).get("totalCostUSD") or 0
                except ValueError:
                    pass
                continue
            if stamp is None:
                at = line.find('"timestamp":"')
                if at >= 0:
                    stamp = line[at + 13:at + 26]
            if entry is None:
                at = line.find('"entrypoint":"')
                if at >= 0:
                    entry = line[at + 14:line.index('"', at + 14)]
    return stamp, entry, usd


def claude_spend(since, cache_file):
    """{(UTC hour, project): usd} of transcripts touched since. A finished
    transcript never changes, so each is read once: the cache keeps
    [size, mtime, hour, entrypoint, usd] per path."""
    try:
        with open(cache_file) as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}
    seen = {}
    spend = {}
    for folder, _dirs, files in os.walk(CLAUDE_PROJECTS):
        top = os.path.relpath(folder, CLAUDE_PROJECTS).split(os.sep)[0]
        for name in files:
            path = os.path.join(folder, name)
            try:
                stat = os.stat(path)
                if not name.endswith(".jsonl") or stat.st_mtime < since:
                    continue
                mark = [stat.st_size, int(stat.st_mtime)]
                known = cache.get(path)
                if known is None or known[:2] != mark:
                    known = mark + list(read_transcript(path))
            except OSError:
                continue
            seen[path] = known
            stamp, entry, usd = known[2:]
            if usd:
                # `claude -p` (sdk-cli) is a job of that project; anything
                # else is somebody at a keyboard.
                headless = entry == "sdk-cli"
                project = project_of(top) if headless else "Claude Code"
                key = (stamp or hour_of(stat.st_mtime), project)
                spend[key] = spend.get(key, 0) + usd
    with open(cache_file + ".tmp", "w") as f:
        json.dump(seen, f)
    os.replace(cache_file + ".tmp", cache_file)
    return spend


def limits():
    done = subprocess.run(
        [OMP, "usage", "--json", "--provider", "anthropic", "--no-extensions"],
        capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        error = done.stderr.strip()[-200:]
        raise RuntimeError(f"omp usage exited {done.returncode}: {error}")
    reports = json.loads(done.stdout)["reports"]
    if not reports:
        raise RuntimeError("omp usage knows no Anthropic account")
    return reports[0]


def percent(limit):
    return round(limit["amount"]["usedFraction"] * 100)


def clock(epoch):
    return time.strftime("%a %H:%M", time.localtime(epoch))


def money(usd):
    return f"${usd:,.0f}" if usd >= 10 else f"${usd:.2f}"


def summary(state_dir):
    report = limits()
    by_id = {limit["id"]: limit for limit in report["limits"]}
    week = by_id["anthropic:7d"]
    resets = week["window"]["resetsAt"] / 1000
    start = resets - WEEK
    first_hour = hour_of(start)

    totals = {}

    def add(name, usd):
        totals[name] = totals.get(name, 0) + usd

    own, pin = omp_spend(start)
    for (hour, _pin, project), usd in own.items():
        if hour >= first_hour:
            add(f"NAS · {project}", usd)
    cache_file = os.path.join(state_dir, "claude-transcripts.json")
    for (hour, project), usd in claude_spend(start, cache_file).items():
        if hour >= first_hour:
            add(f"NAS · {project}", usd)

    pc_note = "no PC data yet"
    pc = {}
    try:
        with open(os.path.join(state_dir, "pc.json")) as f:
            pushed = json.load(f)
        for hour, their_pin, project, usd in pushed["spend"]:
            if hour >= first_hour and (not pin or their_pin == pin):
                pc[project] = pc.get(project, 0) + usd
        pc_note = f"PC data from {clock(pushed['generated'])}"
    except (OSError, ValueError, KeyError, TypeError):
        pass
    ranked = sorted(pc.items(), key=lambda item: -item[1])
    for project, usd in ranked[:PC_SOURCES]:
        add(f"PC · {project}", usd)
    rest = ranked[PC_SOURCES:]
    if rest:
        add(f"PC · {len(rest)} more", sum(usd for _project, usd in rest))

    meters = []
    for limit_id, name in (("anthropic:7d:fable", "Fable"),
                           ("anthropic:5h", "5-hour")):
        if limit_id in by_id:
            meters.append({"name": name,
                           "used_percent": percent(by_id[limit_id])})
    sources = sorted(totals.items(), key=lambda item: -item[1])
    return {
        "ok": True,
        "limit": {
            "used_percent": percent(week),
            "status": week.get("status"),
            "resets": clock(resets),
        },
        "meters": meters,
        "resets_available": (report.get("resetCredits") or {}).get(
            "redeemableCount") or 0,
        "since": clock(start),
        "sources": [{"name": name, "usd": money(usd)}
                    for name, usd in sources if usd >= 0.005],
        "total": money(sum(totals.values())),
        "pc": pc_note,
    }


def login():
    done = subprocess.run([CLAUDE, "auth", "status", "--json"],
                          capture_output=True, text=True, timeout=60)
    status = json.loads(done.stdout)
    valid = status.get("loggedIn") is True
    days = None
    if valid and status.get("loginExpiresAt"):
        end = datetime.fromisoformat(
            status["loginExpiresAt"].replace("Z", "+00:00"))
        days = round((end.timestamp() - time.time()) / (24 * 3600), 1)
        # Claude Code says "logged in" until its next call finds out.
        valid = days > 0
    return {
        "valid": valid,
        # A login that names no end (a setup token) lasts.
        "lasting": valid and (days is None or days > LOGIN_WARN_DAYS),
        "days_left": days,
    }


def collect(state_dir):
    try:
        out = summary(state_dir)
    except Exception as error:
        out = {"ok": False, "error": f"{type(error).__name__}: {error}"[:300]}
    try:
        out["login"] = login()
    except Exception as error:
        out["login"] = {"valid": False, "lasting": False,
                        "error": f"{type(error).__name__}: {error}"[:300]}
    out["updated"] = clock(time.time())
    target = os.path.join(state_dir, "usage.json")
    with open(target + ".tmp", "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(target + ".tmp", target)
    print(f"Claude Code login: {json.dumps(out['login'])}")
    if not out["ok"]:
        print(out["error"], file=sys.stderr)
        return 1
    print(f"{out['limit']['used_percent']}% of the weekly limit used,"
          f" {out['total']} logged since {out['since']}; {out['pc']}")
    return 0


def scan():
    spend, pin = omp_spend(time.time() - SCAN_DAYS * 24 * 3600)
    json.dump({
        "generated": int(time.time()),
        "pin": pin,
        "spend": [[*key, round(usd, 4)] for key, usd in sorted(spend.items())],
    }, sys.stdout)
    return 0


def main(argv):
    if argv[1:] == ["scan"]:
        return scan()
    if len(argv) == 3 and argv[1] == "collect":
        return collect(argv[2])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
