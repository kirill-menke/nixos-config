#!/usr/bin/env python3
"""Make Alt+Up / Alt+Down scroll an Orca terminal pane one line (like kitty).

Usage: orca-terminal-alt-arrow-scroll.py <app.asar>   (rewritten in place)

Upstream (anchors checked against 1.4.248) has no line-scroll shortcut and no
keybinding action to bind one to; Alt+Up/Down go to the shell or TUI as
ESC[1;3A / ESC[1;3B. This patches three spots in the minified renderer
bundles (out/renderer/assets/*.js):

  resolver  terminal shortcut resolver: Alt+Up/Down (no other modifier)
            resolve to {type:`scrollViewport`,lines:-1|1}, after every Orca
            keybinding check, so a user-bound Orca shortcut on these chords
            still wins
  pane      workspace pane dispatcher: scroll the active pane by `lines` and
            re-sync Orca's follow-output/pinned-viewport state; runs before
            the `!repeat` gate, so holding the key keeps scrolling
  preview   key handler of the terminals Orca mounts outside workspace panes
            (the agent dashboard's preview among them): same, via scrollLines

Each spot is found by its code, not by its chunk's file name, because the
bundler moves code between chunks: 1.4.248 put the resolver and the preview
handler into open-standalone-terminal-*.js (before: terminal-native-copy-
gutter-*.js and AgentKanbanBoard-*.js); the pane dispatcher is in
OnboardingInlineCommandTerminal-*.js. Every anchor must sit in exactly one
renderer file and match exactly once there, otherwise this exits non-zero and
the Nix build fails: after an Orca bump, re-check the anchors.

The `web/` bundles (browser-hosted Orca) are left alone.

The asar is rewritten without repacking: only the patched files' bytes,
offsets, sizes and integrity hashes change; everything else is kept as it is.
Since 1.4.232 the renderer bundles are `unpacked`: such targets are rewritten
in place under <app.asar>.unpacked/, and their header size/integrity are
updated to match.
"""
import hashlib
import json
import os
import re
import struct
import sys

BLOCK = 4 * 1024 * 1024
NAME = "orca-terminal-alt-arrow-scroll"
RENDERER = re.compile(r"out/renderer/assets/[\w-]+\.js")

# Last Orca keybinding check in the resolver, then the first raw-key rule.
RESOLVER = (r"(return\{type:`splitActivePane`,direction:`horizontal`\}\})"
            r"(if\(!(\w+)\.metaKey&&!\3\.ctrlKey&&!\3\.altKey&&\3\.shiftKey&&\3\.key===`Enter`\))")
PANE = (r"(if\((\w+)\.type===`selectAll`\)\{let \2=(\w+)\.getActivePane\(\)\?\?\3\.getPanes\(\)\[0\];"
        r"if\(!\2\)return;(\w+)\.repeat\|\|)")
# The pane dispatcher's existing scrollViewport branch names the viewport-state sync helper.
PANE_SYNC = r"\(\w+\(r\.terminal\),r\.terminal\.scrollToBottom\(\)\),(\w+)\(r\.terminal\)\}else if\("
PREVIEW = r"case`scrollViewport`:return (\w+)\.position===`top`\?(\w+)\.scrollToTop\(\):\2\.scrollToBottom\(\),"


def sub_once(name, pattern, repl, text):
    out, n = re.subn(pattern, repl, text, flags=re.S)
    if n != 1:
        sys.exit(f"{NAME}: anchor '{name}' matched {n} times (expected 1)")
    return out


def patch_resolver(js):
    return sub_once(
        "resolver",
        RESOLVER,
        lambda m: f"{m[1]}if(!{m[3]}.metaKey&&!{m[3]}.ctrlKey&&{m[3]}.altKey&&!{m[3]}.shiftKey&&"
        f"({m[3]}.key===`ArrowUp`||{m[3]}.key===`ArrowDown`))"
        f"return{{type:`scrollViewport`,lines:{m[3]}.key===`ArrowUp`?-1:1}};{m[2]}",
        js,
    )


def patch_pane_dispatcher(js):
    sync = re.findall(PANE_SYNC, js)
    if len(sync) != 1:
        sys.exit(f"{NAME}: anchor 'pane sync helper' matched {len(sync)} times (expected 1)")
    return sub_once(
        "pane dispatcher",
        PANE,
        lambda m: f"if({m[2]}.type===`scrollViewport`&&{m[2]}.lines){{{m[4]}.preventDefault(),"
        f"{m[4]}.stopImmediatePropagation();let altScrollPane={m[3]}.getActivePane()??{m[3]}.getPanes()[0];"
        f"altScrollPane&&(altScrollPane.terminal.scrollLines({m[2]}.lines),{sync[0]}(altScrollPane.terminal));return}}"
        f"{m[1]}",
        js,
    )


def patch_preview(js):
    return sub_once(
        "preview handler",
        PREVIEW,
        lambda m: f"case`scrollViewport`:return {m[1]}.lines?{m[2]}.scrollLines({m[1]}.lines):"
        f"{m[1]}.position===`top`?{m[2]}.scrollToTop():{m[2]}.scrollToBottom(),",
        js,
    )


# name -> (anchor that locates the file, patch function)
TARGETS = {
    "resolver": (RESOLVER, patch_resolver),
    "pane": (PANE, patch_pane_dispatcher),
    "preview": (PREVIEW, patch_preview),
}


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

    packed, unpacked = [], []  # (path, node); packed sorted into archive order below
    def walk(node, prefix):
        for name, child in node["files"].items():
            p = f"{prefix}{name}"
            if "files" in child:
                walk(child, p + "/")
            elif child.get("unpacked"):
                unpacked.append((p, child))
            elif "offset" in child:
                packed.append((p, child))
    walk(header, "")
    packed.sort(key=lambda e: int(e[1]["offset"]))

    def packed_bytes(node):
        start = base + int(node["offset"])
        return raw[start:start + node["size"]]

    def disk_path(path):
        return os.path.join(asar_path + ".unpacked", path)

    # Find each anchor's renderer file by content.
    renderer = {}
    for path, node in packed:
        if RENDERER.fullmatch(path):
            renderer[path] = packed_bytes(node)
    for path, node in unpacked:
        if RENDERER.fullmatch(path):
            with open(disk_path(path), "rb") as f:
                renderer[path] = f.read()
    owners = {}
    for key, (anchor, _) in TARGETS.items():
        found = [path for path, data in renderer.items()
                 if re.search(anchor, data.decode("utf-8", "replace"), flags=re.S)]
        if len(found) != 1:
            sys.exit(f"{NAME}: anchor '{key}' found in {len(found)} renderer files (expected 1): {found}")
        owners.setdefault(found[0], []).append(key)

    patched = {}
    for path, keys in owners.items():
        js = renderer[path].decode("utf-8")
        for key in keys:
            js = TARGETS[key][1](js)
        patched[path] = js.encode("utf-8")

    chunks, offset = [], 0
    for path, node in packed:
        data = patched.get(path)
        if data is None:
            data = packed_bytes(node)
        else:
            node["integrity"] = integrity(data)
        node["offset"] = str(offset)
        node["size"] = len(data)
        chunks.append(data)
        offset += len(data)
    disk_writes = []
    for path, node in unpacked:
        data = patched.get(path)
        if data is not None:
            node["integrity"] = integrity(data)
            node["size"] = len(data)
            disk_writes.append((disk_path(path), data))

    js = json.dumps(header, separators=(",", ":")).encode("utf-8")
    pad = (4 - len(js) % 4) % 4
    payload = struct.pack("<I", len(js)) + js + b"\0" * pad
    header_pickle = struct.pack("<I", len(payload)) + payload
    with open(asar_path, "wb") as f:
        f.write(struct.pack("<II", 4, len(header_pickle)))
        f.write(header_pickle)
        for c in chunks:
            f.write(c)
    for disk, data in disk_writes:
        with open(disk, "wb") as f:
            f.write(data)
    print(f"{NAME}: patched {asar_path} ({', '.join(f'{p}: {k}' for p, k in sorted(owners.items()))})")


if __name__ == "__main__":
    main(sys.argv[1])
