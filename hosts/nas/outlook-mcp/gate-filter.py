"""The public gate's server, with its answers masked.

Usage: gate-filter.py SERVER-COMMAND...

mcp-auth-proxy runs this instead of ms-365-mcp-server. It runs the server as a
child and passes the stdio protocol through both ways, but every message the
server sends comes out with login codes and one-time-link tokens masked
(mask.py), except answers to requests that hold no mail (tools/list and the
like). Unlike mux.py it honours no opt-in: the reveal key is removed from
requests and ignored. A message that can't be masked is never passed on; a
request gets an error answer instead.
"""

import json
import subprocess
import sys
import threading

import mask


def log(text):
    print(f"gate-filter: {text}", file=sys.stderr, flush=True)


def encode(message):
    return json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


def to_server(server, methods):
    """Client → server: remember each request's method by id, drop the reveal key."""
    for line in sys.stdin.buffer:
        try:
            message = json.loads(line)
        except ValueError:
            message = None
        if isinstance(message, (dict, list)):
            items = message if isinstance(message, list) else [message]
            out = []
            for item in items:
                if isinstance(item, dict) and "method" in item:
                    item, _ = mask.take_reveal(item)
                    if "id" in item:
                        methods[json.dumps(item["id"])] = item["method"]
                out.append(item)
            line = encode(out if isinstance(message, list) else out[0])
        server.stdin.write(line)
        server.stdin.flush()
    server.stdin.close()


def masked(item, methods):
    """One message from the server as the client may see it, or None to drop it."""
    if not isinstance(item, dict):
        return None
    method = methods.pop(json.dumps(item["id"]), None) if "id" in item and "method" not in item else None
    if method in mask.UNMASKED_METHODS:
        return item
    try:
        return mask.mask_reply(item)
    except Exception as error:  # noqa: BLE001 - never pass an unmasked message on
        log(f"masking failed: {error!r}")
        if "id" in item and "method" not in item:
            return {"jsonrpc": "2.0", "id": item["id"],
                    "error": {"code": -32603, "message": "gate-filter: masking the answer failed"}}
        return None


def main():
    server = subprocess.Popen(sys.argv[1:], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    methods = {}  # JSON of a request id -> its method
    threading.Thread(target=to_server, args=(server, methods), daemon=True).start()
    out = sys.stdout.buffer
    for line in server.stdout:
        try:
            message = json.loads(line)
        except ValueError:
            log(f"dropped {len(line)} bytes of non-JSON output")
            continue
        if isinstance(message, list):
            message = [m for m in (masked(item, methods) for item in message) if m is not None]
        else:
            message = masked(message, methods)
        if message:
            out.write(encode(message))
            out.flush()
    sys.exit(server.wait())


if __name__ == "__main__":
    main()
