#!/usr/bin/env python3
"""Backport stablyai/orca#25256 (panel `commands.invoke`) into a built Orca app.asar.

Usage: orca-panel-commands-invoke.py <app.asar>   (rewritten in place)

Upstream (<= 1.4.224) lets a sandboxed plugin panel call only
workspace.readContext / terminal.sendText / notifications.show, so a panel can
never reach its own worker. PR #25256 adds a panel-only `commands.invoke` host
method; this applies the same change to the minified bundles:

  main/index.js            spec row, gate (null capability + panel-only),
                           handler, PluginService -> invokeCommand binding
  {renderer,web}/assets/plugin-panel-shell-*.js
                           spec row, so the host's panel bridge accepts the name

Every edit must match exactly once, otherwise this exits non-zero and the Nix
build fails: after an Orca bump, re-check the anchors (or drop this file once
the PR ships in a release).

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


def sub_once(name, pattern, repl, text):
    out, n = re.subn(pattern, repl, text, flags=re.S)
    if n != 1:
        sys.exit(f"orca-panel-commands-invoke: anchor '{name}' matched {n} times (expected 1)")
    return out


def patch_main(js):
    zod = re.search(r"requestId:(\w+)\.C\(\)\.min\(1\)\.max\(128\)", js)
    if not zod:
        sys.exit("orca-panel-commands-invoke: zod namespace anchor not found in main/index.js")
    z = zod.group(1)

    js = sub_once(
        "main spec row",
        r"(\w+)(\(\{name:`events\.subscribe`,[^}]*\}\))\];",
        lambda m: f"{m[1]}{m[2]},{m[1]}({{name:`commands.invoke`,since:`1.0`,scope:`plugin-private`,"
        f"capability:null,mutation:!1,panel:!0,panelOnly:!0,"
        f"params:{z}.y({{commandId:{z}.C().min(1).max(128),args:{z}.E().optional()}}).strict(),"
        f"result:{z}.y({{value:{z}.E()}})}})];",
        js,
    )
    js = sub_once(
        "gate panel-only",
        r"let (\w+)=(\w+)\((\w+)\);return \1\?(\w+)\.viaPanel&&!(\w+)\(\3\)\?"
        r"(\{granted:!1,code:`panel_forbidden`,error:`[^`]*`\}):",
        lambda m: f"let {m[1]}={m[2]}({m[3]});return {m[1]}?{m[4]}.viaPanel&&!{m[5]}({m[3]})?{m[6]}:"
        f"!{m[4]}.viaPanel&&{m[1]}.panelOnly?{{granted:!1,code:`capability_denied`,"
        f"error:`method ${{{m[3]}}} is only available to sandboxed panels`}}:",
        js,
    )
    js = sub_once(
        "gate null capability",
        r"(\w+)\.grantedCapabilities\.includes\((\w+)\.capability\)\?\{granted:!0\}",
        lambda m: f"({m[2]}.capability===null||{m[1]}.grantedCapabilities.includes({m[2]}.capability))?{{granted:!0}}",
        js,
    )
    js = sub_once(
        "handler",
        r"(\w+)(\(`events\.subscribe`,async\(\w+,\{pluginId:\w+,services:\w+\}\)=>"
        r"\{let\{events:\w+\}=\w+;return\{subscribed:[^}]*\}\}\))\]\)",
        lambda m: f"{m[1]}{m[2]},{m[1]}(`commands.invoke`,async(e,{{pluginId:t,services:n}})=>"
        f"({{value:(await n.invokePluginCommand(t,e.commandId,e.args))??null}}))])",
        js,
    )
    js = sub_once(
        "service binding",
        r"services:this\.runtimeDelegate\?(\w+\(\{delegate:this\.runtimeDelegate,[^}]*"
        r"subscribeEvents:\((\w+),(\w+)\)=>this\.eventBus\.subscribe\(\2,\3\)\}\)):null",
        lambda m: f"services:this.runtimeDelegate?{{...{m[1]},"
        f"invokePluginCommand:(e,t,n)=>this.invokeCommand(e,t,n)}}:null",
        js,
    )
    return js


def patch_panel_shell(js):
    return sub_once(
        "panel shell spec row",
        r"(\w+)(\(\{name:[`\"]events\.subscribe[`\"],[^}]*params:(\w+),result:(\w+)\}\))\];",
        lambda m: f"{m[1]}{m[2]},{m[1]}({{name:`commands.invoke`,since:`1.0`,scope:`plugin-private`,"
        f"capability:null,mutation:!1,panel:!0,panelOnly:!0,params:{m[3]},result:{m[4]}}})];",
        js,
    )


def targets(path):
    if path == "out/main/index.js":
        return patch_main
    if re.fullmatch(r"out/(renderer|web)/assets/plugin-panel-shell-[\w-]+\.js", path):
        return patch_panel_shell
    return None


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

    patched = set()
    chunks, offset = [], 0
    for path, node in files:
        start = base + int(node["offset"])
        data = raw[start:start + node["size"]]
        fn = targets(path)
        if fn:
            data = fn(data.decode("utf-8")).encode("utf-8")
            node["integrity"] = integrity(data)
            patched.add("main" if path.endswith("index.js") else "panel-shell")
        node["offset"] = str(offset)
        node["size"] = len(data)
        chunks.append(data)
        offset += len(data)
    if patched != {"main", "panel-shell"}:
        sys.exit(f"orca-panel-commands-invoke: expected main + panel-shell targets, patched {sorted(patched)}")

    js = json.dumps(header, separators=(",", ":")).encode("utf-8")
    pad = (4 - len(js) % 4) % 4
    payload = struct.pack("<I", len(js)) + js + b"\0" * pad
    header_pickle = struct.pack("<I", len(payload)) + payload
    with open(asar_path, "wb") as f:
        f.write(struct.pack("<II", 4, len(header_pickle)))
        f.write(header_pickle)
        for c in chunks:
            f.write(c)
    print(f"orca-panel-commands-invoke: patched {asar_path}")


if __name__ == "__main__":
    main(sys.argv[1])
