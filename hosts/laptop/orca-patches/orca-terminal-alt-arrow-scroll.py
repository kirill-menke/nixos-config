#!/usr/bin/env python3
"""Make Alt+Up / Alt+Down scroll an Orca terminal pane one line (like kitty).

Usage: orca-terminal-alt-arrow-scroll.py <app.asar>   (rewritten in place)

Upstream (<= 1.4.224) has no line-scroll shortcut and no keybinding action to
bind one to; Alt+Up/Down go to the shell or TUI as ESC[1;3A / ESC[1;3B. This
patches the minified renderer bundles:

  renderer/assets/terminal-native-copy-gutter-*.js
      terminal shortcut resolver: Alt+Up/Down (no other modifier) resolve to
      {type:`scrollViewport`,lines:-1|1}, after every Orca keybinding check, so
      a user-bound Orca shortcut on these chords still wins
  renderer/assets/OnboardingInlineCommandTerminal-*.js
      workspace pane dispatcher: scroll the active pane by `lines` and re-sync
      Orca's follow-output/pinned-viewport state; runs before the `!repeat`
      gate, so holding the key keeps scrolling
  renderer/assets/AgentKanbanBoard-*.js
      dashboard preview terminal: same, via scrollLines

The `web/` bundles (browser-hosted Orca) are left alone.

Every edit must match exactly once, otherwise this exits non-zero and the Nix
build fails: after an Orca bump, re-check the anchors.

The asar is rewritten without repacking: only the patched files' bytes,
offsets and integrity hashes change; `unpacked` entries and everything else are
kept as they are.
"""
import hashlib
import json
import re
import struct
import sys

BLOCK = 4 * 1024 * 1024
NAME = "orca-terminal-alt-arrow-scroll"


def sub_once(name, pattern, repl, text):
    out, n = re.subn(pattern, repl, text, flags=re.S)
    if n != 1:
        sys.exit(f"{NAME}: anchor '{name}' matched {n} times (expected 1)")
    return out


def patch_resolver(js):
    # Last Orca keybinding check in the resolver, then the first raw-key rule.
    return sub_once(
        "resolver",
        r"(return\{type:`splitActivePane`,direction:`horizontal`\}\})"
        r"(if\(!(\w+)\.metaKey&&!\3\.ctrlKey&&!\3\.altKey&&\3\.shiftKey&&\3\.key===`Enter`\))",
        lambda m: f"{m[1]}if(!{m[3]}.metaKey&&!{m[3]}.ctrlKey&&{m[3]}.altKey&&!{m[3]}.shiftKey&&"
        f"({m[3]}.key===`ArrowUp`||{m[3]}.key===`ArrowDown`))"
        f"return{{type:`scrollViewport`,lines:{m[3]}.key===`ArrowUp`?-1:1}};{m[2]}",
        js,
    )


def patch_pane_dispatcher(js):
    # The existing scrollViewport branch names the viewport-state sync helper.
    sync = re.findall(r"\(\w+\(r\.terminal\),r\.terminal\.scrollToBottom\(\)\),(\w+)\(r\.terminal\);return\}", js)
    if len(sync) != 1:
        sys.exit(f"{NAME}: anchor 'pane sync helper' matched {len(sync)} times (expected 1)")
    return sub_once(
        "pane dispatcher",
        r"(if\((\w+)\.type===`selectAll`\)\{let \2=(\w+)\.getActivePane\(\)\?\?\3\.getPanes\(\)\[0\];"
        r"if\(!\2\)return;(\w+)\.repeat\|\|)",
        lambda m: f"if({m[2]}.type===`scrollViewport`&&{m[2]}.lines){{{m[4]}.preventDefault(),"
        f"{m[4]}.stopImmediatePropagation();let altScrollPane={m[3]}.getActivePane()??{m[3]}.getPanes()[0];"
        f"altScrollPane&&(altScrollPane.terminal.scrollLines({m[2]}.lines),{sync[0]}(altScrollPane.terminal));return}}"
        f"{m[1]}",
        js,
    )


def patch_preview(js):
    return sub_once(
        "preview handler",
        r"case`scrollViewport`:return (\w+)\.position===`top`\?(\w+)\.scrollToTop\(\):\2\.scrollToBottom\(\),",
        lambda m: f"case`scrollViewport`:return {m[1]}.lines?{m[2]}.scrollLines({m[1]}.lines):"
        f"{m[1]}.position===`top`?{m[2]}.scrollToTop():{m[2]}.scrollToBottom(),",
        js,
    )


TARGETS = {
    "resolver": (r"out/renderer/assets/terminal-native-copy-gutter-[\w-]+\.js", patch_resolver),
    "pane": (r"out/renderer/assets/OnboardingInlineCommandTerminal-[\w-]+\.js", patch_pane_dispatcher),
    "preview": (r"out/renderer/assets/AgentKanbanBoard-[\w-]+\.js", patch_preview),
}


def target(path):
    for key, (pattern, fn) in TARGETS.items():
        if re.fullmatch(pattern, path):
            return key, fn
    return None, None


def integrity(data):
    blocks = [hashlib.sha256(data[i:i + BLOCK]).hexdigest() for i in range(0, len(data), BLOCK)] or [
        hashlib.sha256(b"").hexdigest()]
    return {"algorithm": "SHA256", "hash": hashlib.sha256(data).hexdigest(), "blockSize": BLOCK, "blocks": blocks}


def main(asar_path):
    with open(asar_path, "rb") as f:
        raw = f.read()
    # Chromium pickle: [4][header pickle size] then pickle([payload size][json len][json][pad]).
    (header_pickle_size,) = struct.unpack_from("<I", raw, 4)
    (json_len,) = struct.unpack_from("<I", raw, 12)
    header = json.loads(raw[16:16 + json_len])
    base = 8 + header_pickle_size

    files = []  # (path, node) for packed files, in archive order
    def walk(node, prefix):
        for name, child in node["files"].items():
            p = f"{prefix}{name}"
            if "files" in child:
                walk(child, p + "/")
            elif "offset" in child and not child.get("unpacked"):
                files.append((p, child))
    walk(header, "")
    files.sort(key=lambda e: int(e[1]["offset"]))

    patched = []
    chunks, offset = [], 0
    for path, node in files:
        start = base + int(node["offset"])
        data = raw[start:start + node["size"]]
        key, fn = target(path)
        if fn:
            data = fn(data.decode("utf-8")).encode("utf-8")
            node["integrity"] = integrity(data)
            patched.append(key)
        node["offset"] = str(offset)
        node["size"] = len(data)
        chunks.append(data)
        offset += len(data)
    if sorted(patched) != sorted(TARGETS):
        sys.exit(f"{NAME}: expected one each of {sorted(TARGETS)}, patched {sorted(patched)}")

    js = json.dumps(header, separators=(",", ":")).encode("utf-8")
    pad = (4 - len(js) % 4) % 4
    payload = struct.pack("<I", len(js)) + js + b"\0" * pad
    header_pickle = struct.pack("<I", len(payload)) + payload
    with open(asar_path, "wb") as f:
        f.write(struct.pack("<II", 4, len(header_pickle)))
        f.write(header_pickle)
        for c in chunks:
            f.write(c)
    print(f"{NAME}: patched {asar_path}")


if __name__ == "__main__":
    main(sys.argv[1])
