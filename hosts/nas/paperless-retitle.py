"""Second chance for documents Claude could not file when they arrived.

Paperless's workflow "Claude files new documents" runs once, on Document
Added. When the bridge (paperless-claude) has no answer at that moment, most
often because the Claude plan's weekly limit is used up, the document keeps
its file name as title and nothing asks again. This does, once a day, and
applies what the workflow would have: owner kirill if unset, AI title
(overwrite), AI correspondent (only if empty, may create), AI document type
and tags (only if empty, existing names only; the Inbox tag stays), plus a
note saying so. It asks the same bridge, which adds the filing conventions
and all existing names.

Only documents whose title still equals their file name are touched, so a
run that finds none asks Claude nothing. Documents added in the last
SETTLE_MINUTES are left to the workflow, which may still be at them, and
ones older than LOOKBACK_DAYS are left alone.

While Claude's limit is still used up, the run stops at the first document
and exits 0: the next run takes them. Any other failure exits 1.
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

PAPERLESS_URL = os.environ["PAPERLESS_URL"]
PAPERLESS_USER = os.environ["PAPERLESS_USER"]
BRIDGE_URL = os.environ["BRIDGE_URL"]
PASSWORD_FILE = os.path.join(
    os.environ["CREDENTIALS_DIRECTORY"], "paperless-password")
LOOKBACK_DAYS = 30
SETTLE_MINUTES = 60
# The bridge gives one `claude` run 300 s.
BRIDGE_TIMEOUT = 400
NOTE = ("Filed by paperless-retitle (Claude could not be asked when this"
        " was added).")

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "correspondent": {"type": "string"},
        "document_type": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "correspondent", "document_type", "tags"],
}


class LimitReached(Exception):
    """The bridge says Claude's plan limit is used up."""


def auth():
    with open(PASSWORD_FILE) as f:
        password = f.read().strip()
    token = base64.b64encode(f"{PAPERLESS_USER}:{password}".encode())
    return "Basic " + token.decode()


def api(method, path, body=None, **query):
    url = f"{PAPERLESS_URL}/api/{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    request = urllib.request.Request(
        url,
        data=None if body is None else json.dumps(body).encode(),
        method=method,
        headers={"Authorization": auth(),
                 "Accept": "application/json; version=9",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def every(path, **query):
    page, out = 1, []
    while True:
        result = api("GET", path, page=page, page_size=100, **query)
        out += result["results"]
        if not result.get("next"):
            return out
        page += 1


def waiting(now):
    """Documents still titled with their file name, oldest first."""
    since = now - timedelta(days=LOOKBACK_DAYS)
    until = now - timedelta(minutes=SETTLE_MINUTES)
    # The API ignores a filter it does not know and answers with every
    # document, so the window is checked here as well.
    docs = every("documents/", added__gte=since.isoformat(), ordering="added")
    return [
        doc for doc in docs
        if since <= datetime.fromisoformat(doc["added"]) <= until
        and doc["title"] == os.path.splitext(
            doc.get("original_file_name") or "")[0]
    ]


def suggest(doc):
    prompt = (
        "Classify this document for the archive: title, correspondent, "
        "document type and tags.\n\n"
        f"Filename:\n{doc['original_file_name']}\n\n"
        f"Content:\n{(doc.get('content') or '')[:12000]}"
    )
    body = {
        "model": "claude",
        "messages": [{"role": "user", "content": prompt}],
        "tools": [{"type": "function", "function": {
            "name": "classify", "parameters": SCHEMA}}],
    }
    request = urllib.request.Request(
        BRIDGE_URL, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(
                request, timeout=BRIDGE_TIMEOUT) as response:
            answer = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")[:300]
        if "limit" in detail.lower():
            raise LimitReached(detail) from error
        raise RuntimeError(f"bridge answered {error.code}: {detail}")
    call = answer["choices"][0]["message"]["tool_calls"][0]
    return json.loads(call["function"]["arguments"])


def main():
    docs = waiting(datetime.now(timezone.utc))
    if not docs:
        print("no document waits for a title", flush=True)
        return 0

    owner = next(
        u["id"] for u in every("users/") if u["username"] == PAPERLESS_USER)
    correspondents = {c["name"]: c["id"] for c in every("correspondents/")}
    types = {t["name"]: t["id"] for t in every("document_types/")}
    tags = {t["name"]: t for t in every("tags/")}
    inbox = {t["id"] for t in tags.values() if t["is_inbox_tag"]}

    failed = 0
    for done, doc in enumerate(docs):
        try:
            s = suggest(doc)
        except LimitReached as limit:
            print(f"Claude's limit is used up, {len(docs) - done} document(s)"
                  f" wait for the next run: {limit}", flush=True)
            break
        except Exception as error:
            failed += 1
            print(f"#{doc['id']}: no suggestion: {error}", flush=True)
            continue
        patch = {"title": s["title"][:128]}
        if doc.get("owner") is None:
            patch["owner"] = owner
        name = s["correspondent"].strip()
        if doc["correspondent"] is None and name:
            if name not in correspondents:
                correspondents[name] = api(
                    "POST", "correspondents/",
                    {"name": name, "matching_algorithm": 0})["id"]
            patch["correspondent"] = correspondents[name]
        if doc["document_type"] is None and s["document_type"] in types:
            patch["document_type"] = types[s["document_type"]]
        if not set(doc["tags"]) - inbox:
            chosen = {tags[n]["id"] for n in s["tags"] if n in tags} - inbox
            if chosen:
                patch["tags"] = sorted(set(doc["tags"]) | chosen)
        api("PATCH", f"documents/{doc['id']}/", patch)
        api("POST", f"documents/{doc['id']}/notes/", {"note": NOTE})
        print(f"#{doc['id']}: {json.dumps(patch, ensure_ascii=False)}",
              flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
