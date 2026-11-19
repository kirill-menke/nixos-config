"""OpenAI-style chat endpoint for Paperless-ngx's AI, answered by Claude Code.

Paperless (backend "openai-like") posts /v1/chat/completions with one tool, the
classification schema, and requires a tool call back. This runs `claude -p`
with that schema as --json-schema, on the Claude plan kirill is logged into,
and returns the structured output as the tool call. Requests without tools get
the plain text answer. Streaming is not supported.

Paperless's prompt only offers up to five correspondents/types and ten tags
taken from similar documents, and asks for a generic title. The system prompt
here adds the archive's filing conventions and the complete list of existing
names, so suggestions resolve to existing objects by exact name.

One `claude` at a time (RAM), and identical prompts within CACHE_TTL are
answered from memory: the workflow's AI actions each ask about the same
document, and only the "Filename:" line differs once the first one has
renamed it.
"""

import base64
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ["PORT"])
PAPERLESS_URL = os.environ["PAPERLESS_URL"]
PAPERLESS_USER = os.environ["PAPERLESS_USER"]
CLAUDE_MODEL = os.environ["CLAUDE_MODEL"]
CLAUDE_TIMEOUT = 300
CACHE_TTL = 900
PASSWORD_FILE = os.path.join(os.environ["CREDENTIALS_DIRECTORY"], "paperless-password")

CONVENTIONS = """\
Filing conventions of this archive. It belongs to Kirill Menke, who lives in
Bavaria, Germany; most documents are German. These conventions take precedence
over generic guidance in the request.

Title: English, at most 128 characters, "<what it is> – <specifics>". Keep the
German name of an official form or notice in parentheses after its English
name. Include identifying numbers (invoice, policy, contract numbers), the
period covered and the key amount. Amounts as 1,234.56 €, dates as DD.MM.YYYY.
Do not start with a date and do not use the document type alone. Examples:
- Payslip August 2026 (Verdienstabrechnung) – net payout 1,609.97 €
- Hetzner invoice 081000544224 – cloud server kirill.es, October 2025 (5.18 €)
- Operating cost statement 2022 (Betriebskostenabrechnung) – flat Bahnhofplatz 3, credit 1,182.18 €
- Personal liability insurance 702/308477-P-14 – policy supplement (Nachtrag zum Versicherungsschein), change 06.02.2026
- TK – partial reimbursement of rabies vaccinations (254.04 €), prescription requested

Correspondent: the organisation or person who issued the document, never
Kirill himself; for bank statements and transfer printouts, the bank. If it is
one of the existing correspondents below (also under another spelling,
abbreviation or legal form), give that exact name; otherwise the
organisation's usual short name.

Document type: exactly one name from the existing document types below.

Tags: only names from the existing tags below, none if nothing fits.

Storage path: none.

Dates: the document's own issue date first.

Put these exact names in the plain name fields (tags, correspondents,
document_types), and in the matched_*/…_ids fields as well when the request
lists them as available candidates.
"""

claude_lock = threading.Lock()
cache = {}


def paperless_names(endpoint, keep=lambda item: True):
    with open(PASSWORD_FILE) as f:
        password = f.read().strip()
    auth = base64.b64encode(f"{PAPERLESS_USER}:{password}".encode()).decode()
    request = urllib.request.Request(
        f"{PAPERLESS_URL}/api/{endpoint}/?page_size=100000",
        headers={"Authorization": f"Basic {auth}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        results = json.load(response)["results"]
    return sorted(item["name"] for item in results if keep(item))


def system_prompt(paperless_system):
    correspondents = paperless_names("correspondents")
    document_types = paperless_names("document_types")
    tags = paperless_names("tags", keep=lambda tag: not tag["is_inbox_tag"])
    return "\n\n".join(
        part
        for part in (
            paperless_system,
            CONVENTIONS,
            "Existing correspondents:\n" + "\n".join(correspondents),
            "Existing document types:\n" + "\n".join(document_types),
            "Existing tags:\n" + "\n".join(tags),
        )
        if part
    )


def text_of(message):
    content = message.get("content") or ""
    if isinstance(content, list):
        return "\n".join(part.get("text", "") for part in content)
    return content


def run_claude(system, prompt, schema):
    command = [
        "claude", "-p",
        "--model", CLAUDE_MODEL,
        "--output-format", "json",
        # No tools, MCP servers, settings, hooks or skills: document text is
        # untrusted, so the model gets nothing to act with.
        "--tools", "",
        "--strict-mcp-config",
        "--setting-sources", "",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--system-prompt", system,
    ]
    if schema is not None:
        command += ["--json-schema", json.dumps(schema)]
    done = subprocess.run(
        command,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=CLAUDE_TIMEOUT,
        cwd=os.environ["RUNTIME_DIRECTORY"],
    )
    try:
        result = json.loads(done.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"claude exited {done.returncode}: {done.stderr.strip()[-500:]}")
    if result.get("is_error") or done.returncode != 0:
        raise RuntimeError(f"claude failed: {result.get('result') or done.stderr.strip()[-500:]}")
    if schema is not None and result.get("structured_output") is None:
        raise RuntimeError("claude returned no structured output")
    return result


def completion(body):
    messages = body["messages"]
    paperless_system = "\n\n".join(text_of(m) for m in messages if m["role"] == "system")
    turns = [m for m in messages if m["role"] != "system"]
    if len(turns) == 1:
        prompt = text_of(turns[0])
    else:
        prompt = "\n\n".join(f"{m['role'].upper()}:\n{text_of(m)}" for m in turns)

    tool = (body.get("tools") or [None])[0]
    schema = tool["function"]["parameters"] if tool else None

    key = hashlib.sha256(
        json.dumps(
            [paperless_system, re.sub(r"^Filename:\n.*\n", "", prompt, flags=re.M), schema],
        ).encode(),
    ).hexdigest()

    with claude_lock:
        now = time.monotonic()
        for stale in [k for k, (at, _) in cache.items() if now - at > CACHE_TTL]:
            del cache[stale]
        if key in cache:
            result = cache[key][1]
            print(f"cache hit {key[:12]}", flush=True)
        else:
            started = time.monotonic()
            result = run_claude(system_prompt(paperless_system), prompt, schema)
            cache[key] = (time.monotonic(), result)
            print(
                f"claude {key[:12]}: {time.monotonic() - started:.1f}s, "
                f"{'structured' if schema else 'text'}",
                flush=True,
            )

    if tool:
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": f"call_{uuid.uuid4().hex}",
                "type": "function",
                "function": {
                    "name": tool["function"]["name"],
                    "arguments": json.dumps(result["structured_output"]),
                },
            }],
        }
        finish_reason = "tool_calls"
    else:
        message = {"role": "assistant", "content": result["result"]}
        finish_reason = "stop"

    usage = result.get("usage", {})
    prompt_tokens = sum(
        usage.get(k, 0)
        for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    )
    completion_tokens = usage.get("output_tokens", 0)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model", CLAUDE_MODEL),
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def error(self, status, message):
        self.reply(status, {"error": {"message": message, "type": "server_error"}})

    def do_POST(self):
        if self.path.rstrip("/") not in ("/v1/chat/completions", "/chat/completions"):
            self.error(404, f"no route {self.path}")
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if body.get("stream"):
            self.error(400, "streaming is not supported")
            return
        try:
            self.reply(200, completion(body))
        except Exception as exc:
            print(f"error: {exc}", flush=True)
            self.error(502, str(exc))

    def log_message(self, format, *args):
        pass


ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
