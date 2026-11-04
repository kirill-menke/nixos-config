"""One ms-365-mcp-server for every local MCP client.

Usage: mux.py SOCKET GROUP SERVER-COMMAND...

Clients connect to the Unix socket SOCKET (mode 0660, group GROUP) and speak
MCP's stdio framing, one JSON-RPC message per line: exactly what they would
write to the server's stdin. This process runs the one real server as a child
and initialises it once. It answers each client's `initialize` and `ping`
itself, forwards every other request with its id rewritten (so concurrent
clients can't collide), and sends each response back to the client that asked.
The server's notifications are dropped (no client here subscribes to any),
and requests the server sends to a client (sampling, roots) get an error.

Upstream's HTTP mode can't do this job: it expects every request to carry the
caller's own Microsoft token, while this server signs in from the local token
cache. When the server exits, its requests in flight fail with an error and
it is restarted and initialised again; clients stay connected.

Every answer reaches its client with login codes and one-time-link tokens
masked (mask.py), unless the request was a tools/call whose params carry
`_meta: {"vault-fill/reveal": true}` (mail.py's code reader). That key is
removed before the server sees the request.
"""

import asyncio
import grp
import itertools
import json
import os
import sys

import mask

SOCKET, GROUP, *SERVER = sys.argv[1:]

# Protocol versions answered as the client asks; anything else gets the version
# the server negotiated with this process.
CLIENT_VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18"}
INIT_TIMEOUT = 60
# Mail bodies and attachment contents (base64) arrive as single lines.
LINE_LIMIT = 64 * 1024 * 1024


def log(text):
    print(text, file=sys.stderr, flush=True)


class Client:
    count = itertools.count(1)

    def __init__(self, writer):
        self.name = f"client {next(Client.count)}"
        self.writer = writer

    def send(self, message):
        if not self.writer.is_closing():
            self.writer.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")


class Server:
    def __init__(self):
        self.proc = None
        self.ids = itertools.count(1)
        self.pending = {}  # upstream id -> (client, client's id, masked) or a Future
        self.init_result = None
        self.ready = asyncio.Event()
        self.masking = set()  # tasks masking an answer

    async def run(self):
        delay = 1
        while True:
            started = asyncio.get_running_loop().time()
            try:
                await self.start()
                code = await self.proc.wait()
            except Exception as error:  # noqa: BLE001 - any start failure means retry
                log(f"server start failed: {error!r}")
                if self.proc and self.proc.returncode is None:
                    self.proc.kill()
                    await self.proc.wait()
                code = self.proc.returncode if self.proc else None
            self.ready.clear()
            self.fail_all(f"the Outlook MCP server exited ({code})")
            if asyncio.get_running_loop().time() - started > 60:
                delay = 1
            log(f"server exited ({code}); restarting in {delay} s")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    async def start(self):
        self.proc = await asyncio.create_subprocess_exec(
            *SERVER, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, limit=LINE_LIMIT
        )
        asyncio.create_task(self.read(self.proc))
        reply = asyncio.get_running_loop().create_future()
        uid = next(self.ids)
        self.pending[uid] = reply
        await self.write(
            {
                "jsonrpc": "2.0",
                "id": uid,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "outlook-mcp-mux", "version": "1"},
                },
            }
        )
        message = await asyncio.wait_for(reply, INIT_TIMEOUT)
        if "error" in message:
            raise RuntimeError(f"initialize failed: {message['error']}")
        self.init_result = message["result"]
        await self.write({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.ready.set()
        info = self.init_result.get("serverInfo", {})
        log(f"server ready: {info.get('name')} {info.get('version')}, pid {self.proc.pid}")

    async def read(self, proc):
        while True:
            try:
                line = await proc.stdout.readline()
            except ValueError:  # one line beyond LINE_LIMIT: unusable, skip it
                log("dropped a server message longer than the line limit")
                continue
            if not line:
                return
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            if "id" in message and "method" not in message:
                target = self.pending.pop(message["id"], None)
                if isinstance(target, asyncio.Future):
                    if not target.done():
                        target.set_result(message)
                elif target is not None:
                    client, cid, masked = target
                    reply = {**message, "id": cid}
                    if masked:
                        task = asyncio.create_task(self.send_masked(client, reply))
                        self.masking.add(task)
                        task.add_done_callback(self.masking.discard)
                    else:
                        client.send(reply)
            elif "id" in message:
                await self.write(
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "error": {"code": -32601, "message": "outlook-mcp-mux: no client to ask"},
                    }
                )

    async def send_masked(self, client, reply):
        """Mask an answer in a worker thread (a big mail takes a few hundred ms),
        so other clients' messages keep flowing meanwhile."""
        try:
            reply = await asyncio.to_thread(mask.mask_reply, reply)
        except Exception as error:  # noqa: BLE001 - never pass an unmasked answer on
            log(f"masking failed: {error!r}")
            reply = error_reply(reply["id"], "outlook-mcp-mux: masking the answer failed")
        client.send(reply)

    async def write(self, message):
        if not self.proc or self.proc.returncode is not None:
            raise ConnectionError("the Outlook MCP server is not running")
        self.proc.stdin.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
        await self.proc.stdin.drain()

    async def forward(self, client, message):
        await self.ready.wait()
        message, reveal = mask.take_reveal(message)
        uid = next(self.ids)
        masked = not reveal and message["method"] not in mask.UNMASKED_METHODS
        self.pending[uid] = (client, message["id"], masked)
        try:
            await self.write({**message, "id": uid})
        except (ConnectionError, OSError) as error:
            self.pending.pop(uid, None)
            client.send(error_reply(message["id"], str(error)))

    async def cancel(self, client, message):
        params = message.get("params") or {}
        for uid, target in list(self.pending.items()):
            if isinstance(target, tuple) and target[:2] == (client, params.get("requestId")):
                self.pending.pop(uid)
                await self.write({**message, "params": {**params, "requestId": uid}})

    async def forget(self, client):
        """A client left: drop its requests and tell the server to stop them."""
        for uid, target in list(self.pending.items()):
            if isinstance(target, tuple) and target[0] is client:
                self.pending.pop(uid)
                try:
                    await self.write(
                        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": uid}}
                    )
                except (ConnectionError, OSError):
                    pass

    def fail_all(self, reason):
        for uid, target in list(self.pending.items()):
            self.pending.pop(uid)
            if isinstance(target, asyncio.Future):
                if not target.done():
                    target.set_exception(ConnectionError(reason))
            else:
                client, cid, _ = target
                client.send(error_reply(cid, reason))


def error_reply(cid, text):
    return {"jsonrpc": "2.0", "id": cid, "error": {"code": -32603, "message": text}}


async def handle(server, client, message):
    method = message.get("method")
    if method is None:
        return  # a client's reply to a server request; none are ever sent
    if "id" not in message:
        if method == "notifications/cancelled":
            await server.cancel(client, message)
        return  # notifications/initialized and the like: the server got its own
    if method == "initialize":
        try:
            await asyncio.wait_for(server.ready.wait(), INIT_TIMEOUT)
        except TimeoutError:
            client.send(error_reply(message["id"], "the Outlook MCP server is not ready"))
            return
        result = dict(server.init_result)
        asked = (message.get("params") or {}).get("protocolVersion")
        if asked in CLIENT_VERSIONS:
            result["protocolVersion"] = asked
        client.send({"jsonrpc": "2.0", "id": message["id"], "result": result})
    elif method == "ping":
        client.send({"jsonrpc": "2.0", "id": message["id"], "result": {}})
    else:
        await server.forward(client, message)


async def serve(server, reader, writer):
    client = Client(writer)
    try:
        while True:
            try:
                line = await reader.readline()
            except ValueError:
                log(f"{client.name}: message longer than the line limit, closing")
                break
            if not line:
                break
            try:
                message = json.loads(line)
            except ValueError:
                continue
            for item in message if isinstance(message, list) else [message]:
                if isinstance(item, dict):
                    await handle(server, client, item)
    finally:
        await server.forget(client)
        writer.close()


async def main():
    server = Server()
    asyncio.create_task(server.run())
    if os.path.exists(SOCKET):
        os.unlink(SOCKET)
    listener = await asyncio.start_unix_server(
        lambda r, w: serve(server, r, w), path=SOCKET, limit=LINE_LIMIT
    )
    os.chown(SOCKET, -1, grp.getgrnam(GROUP).gr_gid)
    os.chmod(SOCKET, 0o660)
    log(f"listening on {SOCKET}")
    async with listener:
        await listener.serve_forever()


asyncio.run(main())
