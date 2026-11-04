# The mail tool: omp agents read Kirill's Outlook mail (search, full text,
# login codes) through the Outlook MCP on the NAS, from a Python eval kernel,
# also in sessions that have no mcp__outlook_* tools mounted.
# ./vault.nix installs it as ~/.local/share/vault-fill/mail.py.
#
# In a Python eval cell:
#   %load ~/.local/share/vault-fill/mail.py
#   hits = mail_search("ADE9CD82644")                # newest first; each row has exactly the keys
#       # id, date ("2026-11-02 21:06 +01:00", local), from ("Name <addr>"), subject: no receivedDateTime
#   mail_search(sender="adidas", since="2026-11-01")  # sender: part of the From name or address
#       # a server filter (contains() on From address/name, all folders incl. Junk, every page; no
#       # since=: all mail) → complete by construction: prints "[N mails (server filter: From contains
#       # "adidas", since …, all folders)]"; with a query too: those with every query word in
#       # subject/preview/From, plus KQL's hits for the query in the body ("[+N more by KQL …]")
#   mail_search("Uber")  # KQL ignores accents and matches words anywhere: use sender= for a merchant
#   mail_search("Rückerstattungsbescheid")  # no KQL hit → subjects containing every word (server
#       # filter, substrings: "bescheid" finds it too), "[no KQL hit; N mails (server filter …)]"
#   mail_search("PadelCity")  # still no hit for 1–2 plain words → also run as sender="PadelCity"
#       # (privaterelay From addresses), printing "[no KQL hit for …; matched it as sender instead: N mails]"
#   mail_search("Hi-Fi Klubben")  # also searches "HiFi Klubben"; hits all from one other domain
#       # (paypal.de) print "[hint: … also try sender="hifiklubben"]"
#   mail_search("Rechnung", since="2026-10-01", until="2026-11-01", top=None)  # top=None: all
#       # since/until: "3d"/"2h"/"10m" ago, a date, an ISO time, a datetime or time.time();
#       # until is exclusive. More hits than top prints "[capped at N; pass top=/until=]".
#   mail_list("2026-10-01", "2026-11-01")              # every mail of the period, same row keys (folder="inbox" …)
#   s = mail_scan("2026-01-01", senders=["aws", "nelly"], classify={"dunning": r"mahnung|past due|überfällig",
#                 "failed": r"fehlgeschlagen|declined|abgelehnt"})  # or query="Zahlung" (subject/From words)
#       # EVERY mail of the period, all folders incl. Junk, all pages, filtered on the server ($filter,
#       # not KQL): rows {id, date, from, subject, folder, preview} (+ class), codes masked; s.total (all
#       # mails of the period), s.filter, s.counts; prints "[mail_scan: N of TOTAL mails …, all folders
#       # (Posteingang 8, Junk-E-Mail 3); classes: …]". For complete lists, "none found" and totals.
#   mail_scan("2025-01-01", query='Eggers OR Bettwaren OR "Premium Bettwaren"')  # OR: one server filter
#       # per alternative, merged, each one's count printed ("any of …: Eggers 0, Bettwaren 3, …") and in
#       # s.alternatives; "a phrase" is one term; words without OR must all match
#   print(mail_text(hits[0]["id"]))                    # whole mail as text, repeats removed;
#                                                      # links=True keeps the link addresses
#   texts = mail_texts([h["id"] for h in hits])        # mail_texts(ids) → list of texts (print()
#                                                      # shows them joined); 20 per MCP call, dated; size-safe
#   mail_links(id, match="nachverfolgen|track")        # [{label, url, final}]; no request made; bare
#       # https URLs in plain-text mails count too (label: the text before, else the line above)
#   mail_links(id, resolve=True)  # redirects followed (HEAD), only for the sender's own site and
#                                 # parcel/shop trackers; leave it off on phishing mails
#   await open_mail_link(tab, id, match="download|confirm")  # or index= (mail_links order): opens the
#       # unmasked one-time link in the ShopTab, returns {label, host} only (never the URL); refuses hosts
#       # other than the sender's site/trackers (as resolve=True) and unsubscribe/delete/cancel-like links
#   mail_attachments(id, text=True, match="pdf")       # each with its `text`, masked like mail_text (also
#       # Freischaltcode/Rezeptcode under their label, OCR-spaced); PDFs/scans via pdf_text (OCR):
#       # read attachments this way, not with `read` or a raw pdf_text print; inline logos left out
#   mail_attachments(id, save=True, match="pdf")       # [{…, path}] in a 0700 tmp dir; delete it after
#   import time; t0 = time.time()                      # just before the site sends a code
#   code = mail_code(t0, sender="doctolib")            # finds a code only inside fill_mail_code (below)
#   await fill_mail_code(tab, "#otp", t0, sender="doctolib")  # submit=None: URL unchanged 3 s after
#       # typing and one submit-like button in the form → clicks it and says so ("not submitted: …" else)
#   await fill_mail_code(tab, "#otp", t0, sender="doctolib", submit="Enter")  # or a selector; False: never
#   await fill_mail_code(tab, "input[maxlength='1']", t0, sender="doctolib")  # split boxes
#   await fill_mail_code(tab, "input[id^=pin-code-input]", t0, sender="trymagic", frame="auth.magic.link")
#       # frame= → type_code there. The result ends with what the field shows ("it shows 6 of 6"); split
#       # boxes (also indexed ones like #pin-code-input-0…5) not filled with the code → it types into each
#       # box, reads back, raises "… split boxes … show 1 of 6" if still short (fill_mail_code again with the
#       # same t0: same code, no new mail)
#   await fill_mail_code(tab, "#otp", t0, sender="x", pattern=r"Kennung:\s*(\w{6})")  # own regex (group 1)
#       # a mail from sender= without a code ("No account found") raises MailNoCode (a TimeoutError)
#       # ~12 s later with its masked subject, not after 120 s: read it (mail_text(e.mail_id)), don't retry
#       # the page says the code wasn't sent ("Failed to send code", "zu viele Versuche") or shows a
#       # captcha challenge → MailNotSent (a TimeoutError) at once, naming it: fix that, then a new t0
#   outlook("read-mail-text", {"messageIds": [id], "maxChars": 300000})  # any other MCP tool: a str
#   outlook("list-mail-messages", {"filter": …, "skip": 25}, parse=True)  # its JSON parsed
#   outlook_tools()                                    # a dict: tool name → its arguments
#
# All of these run at once (they block); only fill_mail_code and open_mail_link are
# async. `await` in front of any of them works too and gives the same result.
#
# A login code is a secret: never print code.value, only pass it on (fill_mail_code
# types it with the shop browser's tab.type_code, also into split one-character boxes).
# Mail text comes back with login codes masked as ••••••: mail_search/mail_list
# subjects, mail_text(s), mail_links labels, MailCode.subject and every string of
# any outlook() answer (ids, contentBytes and @odata links excepted). A number counts
# as a code where mail_code would take it (4–8 digits, also "938 459" or one digit
# per line, ≤ 120 characters after or ≤ 40 before a word like Code/PIN/TAN/Passwort,
# also código/codice/kod/код/验证码/認証コード/인증번호 …, touching CJK text too),
# and so does an alphanumeric one (4–12 letters and digits mixed, like myRyanair's
# 9a9aaa9a or K7Q2-PX9M; not order/invoice/model numbers, URLs, #colours or 10px;
# a digit code wins when unsure), or 4–8 capital letters or 4–10 capitals and digits
# (Booking.com's "ABCDEF is your verification code", "Booking.com - ABCDE9 is your
# verification code", "Code: ABCDEF"; set off ≤ 40 characters from a code word, not
# a heading or a booking reference), 3+ hyphen-joined lower-case words near a code
# word (Notion's "login code is word-word-word"), a mixed one alone on the line under
# a "Login to …"/"Anmelden" heading, and every other appearance of it in that mail.
# Mailed passwords too: the value after Passwort/Kennwort/password/Zugangsdaten/PIN
# and ":"/"lautet"/"is" ("Initialkennwort lautet: ••••••"). Codes under a label in
# letters, scans and tables: after Freischaltcode/Aktivierungscode/Rezeptcode/
# Einmal-Kennwort/Zugangscode 6–24 capitals and digits on the same line, the next
# one or in the cell below, also grouped or OCR-spaced ("ABCD EFGH IJKL MNOP",
# "K 7 Q X 9 P 2 M"); after PIN/TAN/…code only grouped ones (not booking/
# reservation/BIC/QR codes).
# Voucher/coupon/gift-card codes ("Gutscheincode: SOMMER-2026") keep their last 4 (••••2026).
# One-time links are masked there too: the values of token/code/otp/key/signature/
# sig/magic/verify (and t from 16 characters) query parameters show as •••, and on
# a path with a link…/l/confirm…/verif…/activat… segment (tk.de/link?id=…) the
# id/lid/uid values from 16 characters and opaque 32+-character path segments after it
# (after Aktivierung/aktivieren/bestaetigen/Bestätigung/freischalten/verifizieren/optin
# from 20 characters: tsoto.net/Aktivierung/•••);
# mail_links(id) gets those URLs masked from the NAS's server as well (only fill_mail_code and
# open_mail_link read unmasked; open_mail_link opens such a link without showing it).
# reveal_codes=True turns this masking off: not for agents. The NAS's server masks the
# same way, and answers unmasked only to fill_mail_code and open_mail_link, so mail_code alone finds no code
# and reveal_codes changes nothing. Dates are local time with the UTC offset.
# IBANs (registry length + mod-97) show as DE••…••1234 and card numbers (brand
# prefix + length + Luhn) as ••••1234 in the same outputs (and mail_scan rows),
# JWTs and Bearer/Basic credentials as •••(masked …), also with reveal_codes
# (except mail_links' URLs, which open_mail_link needs); the NAS's server doesn't mask these.
# Read only: none of these functions sends, moves, flags or deletes mail. mail_links
# (resolve=True) requests (HEAD) only links on the From address's site or a known
# parcel/shop tracker, never unsubscribe/confirm/login/delete-like ones.
#
# Graph rejects $search together with $filter, and contains(from/…) filters are
# "InefficientFilter" unless next to a receivedDateTime range (sent with
# count=true, as the MCP tool asks for contains()): mail_search uses KQL $search
# alone for a query (the search index lags new mail by up to a minute or two; KQL
# over all messages skips Junk and Deleted Items, so those two folders are
# searched too), a filter on the period (since=, else since 2000) plus
# contains(From address or name, x) for sender= (KQL from:x missed mails: 49 of
# 50), checking the From fields (and, with a query, subject/preview/From for every
# query word, accents ignored) here too; only if the server refuses that filter,
# it lists every mail of the period (at most 3000, ~1 s per 100) and filters here.
# With a query and sender=, KQL's hits for both (from:x, from:x*, the bare word,
# then from:"<From name>" for up to 3 From names of the hits) are added: the body.
# With neither, an exact receivedDateTime $filter; mail_list, mail_scan and mail_code use that too.
# mail_scan filters on the server with the same contains() on the From fields
# (senders, any of them) and on subject/From (query words, each); bodyPreview and
# the body can't be filtered on (Graph: "does not support filtering").
# A one-word query whose hits mostly lack the word in subject and From
# (accents counted: KQL's "uber" finds "über") prints a hint to use sender=.
# A query with no KQL hit gets a server filter contains(subject, word) for every
# word; still none and one or two plain words (no KQL syntax, no word with 3+
# digits) and no sender=: the sender= path with those words as well.
# A hyphen between letters ("Hi-Fi") is also searched joined ("HiFi"; KQL splits
# them differently), and a query of 1–3 plain words whose hits all come from one
# domain without the words in their From prints a hint to try sender=.
# Throttled MCP answers (ApplicationThrottled/MailboxConcurrency, 429, 503,
# TooManyRequests: many agents reading at once) are retried after 0.5, 1, 2, 4 s
# (jittered, ≤ ~10 s), mail_texts per mail, and only then reported. A
# read-mail-text answer over the server's 80,000 characters is read again in
# halves; a single mail that big comes cut ("[cut at N chars …]").
# The MCP returns bodies as text (Graph converts HTML); an HTML body would be
# converted here. read-mail-text takes
# `messageIds` (a list), get-mail-message `messageId`; MS365_MCP_MAX_TOP caps a page
# at 25 mails, and the functions here follow @odata.nextLink (skiptoken) for more.

import asyncio as _masyncio
import atexit as _matexit
import base64 as _mbase64
import bisect as _mbisect
import collections as _mcollections
import concurrent.futures as _mfutures
import contextvars as _mcontextvars
import datetime as _mdt
import functools as _mfunctools
import html.parser as _mhtmlparser
import itertools as _mitertools
import json as _mjson
import os as _mos
import random as _mrandom
import queue as _mqueue
import re as _mre
import subprocess as _msub
import shutil as _mshutil
import tempfile as _mtempfile
import threading as _mthreading
import time as _mtime
import urllib.error as _murlerror
import urllib.parse as _murlparse
import urllib.request as _murlrequest
import unicodedata as _municodedata


# The functions here run at once, but agents often write `await` before
# every call: their str/list/dict results (and a MailCode) can be awaited
# too, and give themselves.
class _MAwaitableStr(str):
    def __await__(self):
        return str(self)
        yield  # noqa: unreachable; makes this a generator


class _MAwaitableList(list):
    def __await__(self):
        return list(self)
        yield  # noqa


class _MAwaitableDict(dict):
    def __await__(self):
        return dict(self)
        yield  # noqa


def _mboth(fn):
    """`fn(…)` and `await fn(…)` give the same result."""
    kinds = {str: _MAwaitableStr, list: _MAwaitableList, dict: _MAwaitableDict}

    @_mfunctools.wraps(fn)
    def call(*args, **kwargs):
        r = fn(*args, **kwargs)
        return kinds[type(r)](r) if type(r) in kinds else r
    return call


# The NAS's shared Outlook MCP server, through this PC's SSH tunnel
# (hosts/pc/outlook-mcp.nix): no ssh login or server start per kernel.
_MCP_CMD = ["/run/current-system/sw/bin/outlook-mcp-client"]


class _McpSession:
    """One MCP stdio session to the shared Outlook server, initialised once and reused."""

    def __init__(self):
        self._lock = _mthreading.Lock()
        self._proc = None
        self._next_id = 0

    def _start(self, timeout):
        self.close()
        self._proc = _msub.Popen(_MCP_CMD, stdin=_msub.PIPE, stdout=_msub.PIPE,
                                 stderr=_msub.PIPE, text=True, bufsize=1)
        self._lines = lines = _mqueue.Queue()
        self._stderr = errors = _mcollections.deque(maxlen=20)
        proc = self._proc

        def pump_out():
            for line in proc.stdout:
                lines.put(line)
            lines.put(None)  # the session ended

        def pump_err():
            errors.extend(proc.stderr)

        _mthreading.Thread(target=pump_out, daemon=True).start()
        _mthreading.Thread(target=pump_err, daemon=True).start()
        self._request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                     "clientInfo": {"name": "omp-mail", "version": "1"}}, timeout)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, msg):
        self._proc.stdin.write(_mjson.dumps(msg) + "\n")
        self._proc.stdin.flush()

    def _request(self, method, params, timeout):
        self._next_id += 1
        rid = self._next_id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = _mtime.monotonic() + timeout
        while True:
            left = deadline - _mtime.monotonic()
            if left <= 0:
                raise TimeoutError(f"outlook MCP: no answer to {method} in {timeout} s")
            try:
                line = self._lines.get(timeout=left)
            except _mqueue.Empty:
                continue
            if line is None:
                raise ConnectionError("outlook MCP ended: " + "".join(self._stderr)[-500:])
            try:
                m = _mjson.loads(line)
            except ValueError:
                continue
            if m.get("id") == rid:
                if "error" in m:
                    raise RuntimeError(f"outlook MCP {method}: {m['error']}")
                return m["result"]

    def request(self, method, params=None, timeout=90):
        """Send one request; (re)starts the session first if needed, and once
        more if the session broke. A timeout ends the session, unretried."""
        with self._lock:
            for attempt in (1, 2):
                try:
                    if self._proc is None or self._proc.poll() is not None:
                        self._start(timeout)
                    return self._request(method, params or {}, timeout)
                except TimeoutError:
                    self.close()
                    raise
                except OSError:  # BrokenPipeError, ConnectionError: the session ended
                    self.close()
                    if attempt == 2:
                        raise

    def close(self):
        if self._proc is not None:
            try:
                self._proc.stdin.close()
            except OSError:
                pass
            try:
                self._proc.wait(timeout=2)
            except _msub.TimeoutExpired:
                self._proc.kill()
            self._proc = None


try:
    _MCP.close()  # a second %load replaces the session
except NameError:
    pass
_MCP = _McpSession()
_matexit.register(_MCP.close)


@_mboth
def outlook_tools():
    """A dict: each Outlook MCP tool's name → its argument schemas."""
    tools = _MCP.request("tools/list")["tools"]
    return {t["name"]: t.get("inputSchema", {}).get("properties", {}) for t in tools}


# Graph's throttling answers ("ApplicationThrottled: MailboxConcurrency limit" when
# many agents read at once, 429/503, TooManyRequests): retried after these
# pauses (each ×0.75–1.25 at random; ≤ ~9.5 s in all), then reported.
_THROTTLED = _mre.compile(r"ApplicationThrottled|MailboxConcurrency|TooManyRequests|Too Many Requests|"
                          r"throttl|ServiceUnavailable|Service Unavailable|\b(?:429|503)\b", _mre.I)
_RETRY_PAUSES = (0.5, 1, 2, 4)
_ERROR_JSON = _mre.compile(r'\s*\{\s*"error"\s*:')


def _pause(seconds):
    """Sleep `seconds` ×0.75–1.25 (jitter, so throttled agents don't retry in step)."""
    _mtime.sleep(seconds * _mrandom.uniform(0.75, 1.25))


# True only in fill_mail_code's worker thread: its calls ask the NAS's server for
# unmasked answers (params._meta, see hosts/nas/outlook-mcp/mask.py); every other
# call gets codes masked by the server too. An older server ignores the key.
_REVEAL = _mcontextvars.ContextVar("mail_reveal", default=False)


def _call(tool, args=None, timeout=90):
    """One Outlook MCP tool's text output, codes unmasked by this file (the
    server masks them unless _REVEAL is set): internal only.
    Throttled answers are retried after _RETRY_PAUSES."""
    params = {"name": tool, "arguments": args or {}}
    if _REVEAL.get():
        params["_meta"] = {"vault-fill/reveal": True}
    for pause in _RETRY_PAUSES + (None,):
        try:
            r = _MCP.request("tools/call", params, timeout)
        except RuntimeError as e:  # a JSON-RPC error
            if pause is None or not _THROTTLED.search(str(e)):
                raise
            _pause(pause)
            continue
        text = "\n".join(c.get("text", "") for c in r.get("content", []))
        failed = r.get("isError") or _ERROR_JSON.match(text)
        if pause is not None and failed and _THROTTLED.search(text[:2000]):
            _pause(pause)
            continue
        if r.get("isError") or (failed and _THROTTLED.search(text[:2000])):
            raise RuntimeError(f"{tool}: {_mask(text[:500])[0]}")
        return text


def _parse(tool, text):
    try:
        return _mjson.loads(text)
    except ValueError:
        raise RuntimeError(f"{tool}: {_mask(text[:500])[0]}") from None


def _raw_json(tool, args):
    """One tool's JSON output, codes unmasked: internal only (mail_code needs the digits)."""
    return _parse(tool, _call(tool, args))


@_mboth
def outlook(tool, args=None, timeout=90, parse=False, reveal_codes=False):
    """Call one Outlook MCP tool: its text output (a str), or with parse=True
    its JSON parsed. Login codes and one-time-link tokens in every string of
    any tool's answer come masked (see the header; ids, contentBytes and
    @odata links stay as they are); reveal_codes=True turns that off, not for agents."""
    text = _call(tool, args, timeout)
    text = _mmask_output(text if reveal_codes else _mask_output(text))  # IBANs/cards: always
    return _parse(tool, text) if parse else text


def _pages(tool, args, meta=None):
    """The items of a list tool's answer, page after page (25 a page, following
    @odata.nextLink through `skiptoken`). `meta` (a dict) gets the first page's
    @odata.count as "count" (asked for with count=True)."""
    args = dict(args, top=25)
    while True:
        r = _raw_json(tool, args)
        if meta is not None and "@odata.count" in r:
            meta.setdefault("count", r["@odata.count"])
        yield from r.get("value", [])
        link = r.get("@odata.nextLink")
        if not link:
            return
        args["skiptoken"] = link


def _kept(tool, args, keep, top):
    """The messages `keep` accepts, up to `top` of them (None: all), and
    whether there were more."""
    out = []
    for m in _pages(tool, args):
        if keep(m):
            if top is not None and len(out) >= top:
                return out, True
            out.append(m)
    return out, False


# Folders KQL $search over all messages skips (MF-09's missed AWS dunning
# notices sat in Junk): _collect searches them as well. mail_search sets
# _KQL_SKIPPED to a set that gets the ids found only there.
_KQL_SKIPS = ("junkemail", "deleteditems")
_KQL_SKIPPED = _mcontextvars.ContextVar("kql_skipped", default=None)


def _collect(tool, args, keep, top):
    """_kept; a KQL search over all messages (list-mail-messages with
    search=) also searches the _KQL_SKIPS folders, merged newest first."""
    out, more = _kept(tool, args, keep, top)
    if tool != "list-mail-messages" or "search" not in args:
        return out, more
    found, only = {m["id"]: m for m in out}, _KQL_SKIPPED.get()
    for folder in _KQL_SKIPS:
        extra, extra_more = _kept("list-mail-folder-messages", dict(args, mailFolderId=folder), keep, top)
        more = more or extra_more
        for m in extra:
            if m["id"] not in found:
                found[m["id"]] = m
                if only is not None:
                    only.add(m["id"])
    if len(found) == len(out):
        return out, more
    out = sorted(found.values(), key=lambda m: m["receivedDateTime"], reverse=True)
    if top is not None and len(out) > top:
        return out[:top], True
    return out, more


def _since(since):
    """`since` as an aware UTC datetime (None stays None)."""
    if since is None:
        return None
    if isinstance(since, (int, float)):
        return _mdt.datetime.fromtimestamp(since, _mdt.timezone.utc)
    if isinstance(since, _mdt.datetime):
        return since.astimezone(_mdt.timezone.utc)  # naive: local time
    if isinstance(since, _mdt.date):
        return _mdt.datetime(since.year, since.month, since.day).astimezone(_mdt.timezone.utc)
    m = _mre.fullmatch(r"\s*(\d+)\s*([mhd])\s*", since)
    if m:
        unit = {"m": "minutes", "h": "hours", "d": "days"}[m[2]]
        return _mdt.datetime.now(_mdt.timezone.utc) - _mdt.timedelta(**{unit: int(m[1])})
    return _mdt.datetime.fromisoformat(since.replace("Z", "+00:00")).astimezone(_mdt.timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _sender(msg):
    a = (msg.get("from") or {}).get("emailAddress") or {}
    name, addr = a.get("name") or "", a.get("address") or ""
    return f"{name} <{addr}>" if name and name != addr else addr


def _local(iso):
    """A Graph time as local time with its UTC offset: 2026-11-02 21:06 +01:00."""
    t = _mdt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    return t.strftime("%Y-%m-%d %H:%M %:z")


_LIST_FIELDS = ["id", "receivedDateTime", "from", "subject"]


def _hits(msgs, reveal_codes):
    """[{id, date, from, subject}] of `msgs`, newest first, subjects masked."""
    out = []
    for m in sorted(msgs, key=lambda m: m["receivedDateTime"], reverse=True):
        subject = m.get("subject") or ""
        out.append({"id": m["id"], "date": _local(m["receivedDateTime"]), "from": _sender(m),
                    "subject": _mmask_text(subject if reveal_codes else _mask(subject)[0])})
    return out


def _from_has(msg, sender):
    """Whether `sender` is part of the message's From name or address (case,
    spaces and hyphens ignored: "Deutsche Bahn" matches deutschebahn.com,
    "Hi-Fi Klubben" matches "HiFi Klubben")."""
    have, want = _sender(msg).casefold(), sender.casefold()
    squeeze = _mfunctools.partial(_mre.sub, r"[\s-]+", "")
    return want in have or squeeze(want) in squeeze(have)


@_mboth
def mail_search(query=None, since=None, sender=None, top=20, until=None, reveal_codes=False):
    """Mails across all folders, newest first: [{id, date, from, subject}].
    `query`: KQL/free text (words, "subject:x", an order number …; KQL ignores
    accents and matches words anywhere, so a one-word query whose hits mostly lack
    it in subject and From prints a hint; no KQL hit: the subjects that contain
    every query word, by a server filter; still none and one or two plain words:
    tried as sender= too, saying so; "Hi-Fi" is also searched as "HiFi";
    1–3 plain words whose hits all come from one domain without them print a
    sender= hint); `sender`: part of the sender's name or address, matched by
    a server filter over every mail of the period (all folders, all pages; no
    since=: all mail), printing "[N mails (server filter …)]"; with a query too,
    those whose subject/preview/From have every query word, plus KQL's hits
    for the query (the body) from that sender. `since`, `until` (exclusive): see
    the header. At most `top` mails (None: all); prints "[capped at N; …]" when
    there were more. Subjects come with login codes masked unless
    reveal_codes=True (not for agents)."""
    since_dt, until_dt = _since(since), _since(until)

    def in_time(m):
        t = m["receivedDateTime"]
        return (not since_dt or t >= _iso(since_dt)) and (not until_dt or t < _iso(until_dt))

    if query or sender:
        def search(sender_kql, sender, query):
            # "Hi-Fi Klubben" misses From "HiFi Klubben": also search "HiFi Klubben"
            joined = _HYPHEN.sub("", query) if query else query
            got = [search_one(sender_kql, sender, q) for q in dict.fromkeys([query, joined])]
            if len(got) == 1:
                return got[0]
            found = {m["id"]: m for msgs, _ in got for m in msgs}
            out = sorted(found.values(), key=lambda m: m["receivedDateTime"], reverse=True)
            more = any(more for _, more in got)
            if top is not None and len(out) > top:
                return out[:top], True
            return out, more

        def search_one(sender_kql, sender, query):
            kql = [sender_kql] if sender_kql else []
            if query:  # "phrases" stay phrases
                kql.append(query.replace('"', '\\"'))
            # whole days, in whatever zone the index uses; exact in in_time
            if since_dt:
                kql.append(f"received>={(since_dt - _mdt.timedelta(days=1)).date().isoformat()}")
            if until_dt:
                kql.append(f"received<={(until_dt + _mdt.timedelta(days=1)).date().isoformat()}")
            args = {"select": _LIST_FIELDS, "search": '"' + " AND ".join(kql) + '"'}
            keep = (lambda m: in_time(m) and _from_has(m, sender)) if sender else in_time
            return _collect("list-mail-messages", args, keep, top)

        def kql_by_sender(sender, query):
            """KQL's hits for `query` from `sender` (the body too), widened by the hits' From names."""
            sender = sender.replace('"', "").strip()
            if " " in sender:
                tries = [f'from:\\"{sender}\\"', f'\\"{sender}\\"']
            else:  # from:huk can miss huk-coburg.de; the bare word finds it in the address
                tries = [f"from:{sender}", f"from:{sender}*", sender]
            for t in tries:
                msgs, more = search(t, sender, query)
                if msgs:  # from:aws misses no-reply@amazonaws.com; from:"Amazon Web Services" has it
                    return _widen(msgs, more, sender, lambda k: search(k, sender, query), top)
            return [], False

        def by_sender(sender, query):
            msgs, more = _sender_listing(sender, since_dt, until_dt, top, query=query)
            if not query:
                return msgs, more
            kql, kql_more = kql_by_sender(sender, query)
            found = {m["id"]: m for m in msgs}
            extra = [m for m in kql if m["id"] not in found]
            if not extra:
                return msgs, more
            print(f"[+{len(extra)} more by KQL: the query in the body]")
            out = sorted(msgs + extra, key=lambda m: m["receivedDateTime"], reverse=True)
            if top is not None and len(out) > top:
                return out[:top], True
            return out, more or kql_more

        skipped = set()  # ids _collect found only in the _KQL_SKIPS folders
        reset = _KQL_SKIPPED.set(skipped)
        try:
            if sender:
                msgs, more = by_sender(sender, query)
            else:
                msgs, more = search(None, None, query)
                by_subject = False
                if not msgs:  # KQL misses parts of compound words ("Rückerstattungsbescheid")
                    msgs, more = _subject_listing(query, since_dt, until_dt, top)
                    by_subject = bool(msgs)
                name = _sender_word(query) if not msgs else None
                if name:  # a merchant behind a relay address (PadelCity via privaterelay.appleid.com)
                    msgs, more = by_sender(name, None)
                    print(f'[no KQL hit for "{name}"; matched it as sender instead: {len(msgs)} mails]')
                # the hints explain KQL's hits; the subject filter's have the words in their subject
                hint = None if name or by_subject else _accent_hint(query, msgs) or _domain_hint(query, msgs)
                if hint:
                    print(hint)
        finally:
            _KQL_SKIPPED.reset(reset)
        n = sum(m["id"] in skipped for m in msgs)
        if n:
            print(f"[{n} of these from Junk/Deleted Items (KQL skips those folders: searched separately)]")
    else:
        args = {"select": _LIST_FIELDS, "orderby": ["receivedDateTime desc"]}
        flt = [f"receivedDateTime ge {_iso(since_dt)}"] if since_dt else []
        flt += [f"receivedDateTime lt {_iso(until_dt)}"] if until_dt else []
        if flt:
            args["filter"] = " and ".join(flt)
        msgs, more = _collect("list-mail-messages", args, in_time, top)
    if more:
        print(f"[capped at {top}; pass top=/until=]")
    return _hits(msgs, reveal_codes)


def _widen(msgs, more, sender, search, top, names=3):
    """`msgs` plus the mails KQL finds under the From names of `msgs` (up to
    `names` of them, not the sender word itself) that match `sender` too;
    newest first, at most `top`."""
    found = {m["id"]: m for m in msgs}
    seen = {sender.casefold().replace(" ", "")}
    for m in sorted(msgs, key=lambda m: m["receivedDateTime"], reverse=True):
        name = ((m.get("from") or {}).get("emailAddress") or {}).get("name") or ""
        name = name.replace('"', "").replace("\\", "").strip()
        if not name or "@" in name or name.casefold().replace(" ", "") in seen:
            continue
        seen.add(name.casefold().replace(" ", ""))
        extra, extra_more = search(f'from:\\"{name}\\"')
        more = more or extra_more
        for e in extra:
            found.setdefault(e["id"], e)
        if len(seen) > names:
            break
    out = sorted(found.values(), key=lambda m: m["receivedDateTime"], reverse=True)
    if top is not None and len(out) > top:
        return out[:top], True
    return out, more


_ALL_TIME = _mdt.datetime(2000, 1, 1, tzinfo=_mdt.timezone.utc)  # "no since=": Graph's contains() needs a range
_FALLBACK_CAP = 3000  # mails listed at most (only when the server refuses the From filter)


def _odata(text):
    """`text` as an OData string literal."""
    return "'" + text.replace("'", "''") + "'"


def _sender_filter(senders):
    """A $filter clause keeping mails whose From address or name contains one
    of `senders` (case ignored; also with hyphens, and with all spaces and
    hyphens, dropped, as _from_has does). Graph runs contains() only next to
    a receivedDateTime range (alone or under orderby: InefficientFilter)."""
    parts = []
    for s in senders:
        s = s.strip()
        for v in dict.fromkeys([s, s.replace("-", ""), _mre.sub(r"[\s-]+", "", s)]):
            if v:
                parts += [f"contains(from/emailAddress/{f},{_odata(v)})" for f in ("address", "name")]
    return "(" + " or ".join(dict.fromkeys(parts)) + ")"


def _word_filter(word):
    """A $filter clause: `word` in the subject or the From name or address
    (bodyPreview and the body can't be filtered on)."""
    return "(" + " or ".join(f"contains({f},{_odata(word)})" for f in
                             ("subject", "from/emailAddress/name", "from/emailAddress/address")) + ")"


def _period(since_dt, until_dt):
    """The receivedDateTime $filter range (no since: _ALL_TIME) and its words for a note."""
    flt = f"receivedDateTime ge {_iso(since_dt or _ALL_TIME)}"
    text = f"since {_local(_iso(since_dt))}" if since_dt else "all mail"
    if until_dt:
        flt += f" and receivedDateTime lt {_iso(until_dt)}"
        text += f" until {_local(_iso(until_dt))}"
    return flt, text


def _sender_listing(sender, since_dt, until_dt, top, pages=None, query=None):
    """The mails from `sender` (_from_has) received from `since_dt` (None: all
    mail) to `until_dt`: the receivedDateTime range plus _sender_filter on the
    server, every page (all folders, Junk too), so the list is complete; if the
    server refuses that filter, the whole range, at most _FALLBACK_CAP mails,
    filtered here. With a `query`, only mails whose subject, preview or From
    contain every query word (_query_words; case and accents ignored). Prints
    "[N mails (server filter: From contains …)]". `pages`: a stand-in for _pages (tests)."""
    flt, period = _period(since_dt, until_dt)
    words = _query_words(query)
    fields = _LIST_FIELDS + ["bodyPreview"] if words else _LIST_FIELDS
    args = {"select": fields, "filter": f"{flt} and {_sender_filter([sender])}",
            "orderby": ["receivedDateTime desc"], "count": True}

    def has_words(m):
        have = _fold(f"{m.get('subject') or ''} {m.get('bodyPreview') or ''} {_sender(m)}")
        return all(w in have for w in words)

    def listing(a):
        it = (pages or _pages)("list-mail-messages", a)
        head = next(it, None)  # a refused filter fails here, on the first page
        return _mitertools.chain([head] if head is not None else [], it)

    served = True
    try:
        msgs = listing(args)
    except RuntimeError:  # the server refused the From filter: list the whole range
        served = False
        msgs = listing({"select": fields, "filter": flt, "orderby": ["receivedDateTime desc"]})
    out, more, listed, last = [], False, 0, None
    for m in msgs:
        if listed >= _FALLBACK_CAP:
            break
        listed, last = listed + 1, m["receivedDateTime"]
        if _from_has(m, sender) and has_words(m):
            if top is not None and len(out) >= top:
                more = True
                break
            out.append(m)
    with_words = f", every word of {query!r} in subject/preview/From" if words else ""
    if served:
        note = f'[{len(out)} mails (server filter: From contains "{sender}"{with_words}, {period}, all folders)'
    else:
        note = (f"[{len(out)} mails (the server refused the From filter: listed {listed} mails {period}, "
                f"From checked here{with_words})")
    if listed >= _FALLBACK_CAP and not more:
        note += f"; listed {listed} mails, stopped at {_FALLBACK_CAP} (back to {_local(last)}): pass a later since="
    print(note + "]")
    return out, more


def _subject_listing(query, since_dt, until_dt, top, pages=None):
    """For a query KQL finds nothing for: the mails whose subject contains
    every query word as a substring (KQL matches word starts only, so it misses
    "bescheid" in "Rückerstattungsbescheid"), by a server filter over the
    period (no since: all mail; all folders, every page); case and accents
    checked here too. Prints "[no KQL hit; N mails (server filter …)]" when
    there are any. `pages`: a stand-in for _pages (tests)."""
    words = list(dict.fromkeys(_query_words(query, fold=False)))
    if not words:
        return [], False
    flt, period = _period(since_dt, until_dt)
    args = {"select": _LIST_FIELDS, "orderby": ["receivedDateTime desc"], "count": True,
            "filter": " and ".join([flt] + [f"contains(subject,{_odata(w)})" for w in words])}
    folded = [_fold(w) for w in words]
    out, more = [], False
    try:
        for m in (pages or _pages)("list-mail-messages", args):
            if all(w in _fold(m.get("subject") or "") for w in folded):
                if top is not None and len(out) >= top:
                    more = True
                    break
                out.append(m)
    except RuntimeError as e:  # the server refused the filter: KQL's 0 stands
        print(f"[no KQL hit; the subject filter was refused: {str(e)[:120]}]")
        return [], False
    if out:
        print(f"[no KQL hit; {len(out)} mails (server filter: subject contains {words}, {period}, all folders)]")
    return out, more


def _fold(text):
    """`text` casefolded with accents removed ("Über" → "uber")."""
    text = _municodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in text if not _municodedata.combining(c))


_KQL_OPS = {"and", "or", "not", "near"}


def _query_words(query, fold=True):
    """The words of a KQL `query`, folded (fold=False: as written): property
    prefixes (subject:) and AND/OR/NOT dropped, quotes ignored."""
    text = _mre.sub(r"\b\w+(?::|[<>]=?|=)", " ", (query or "").replace('"', " "))
    words = [w for w in _KQL_WORD.findall(text) if _fold(w) not in _KQL_OPS]
    return [_fold(w) for w in words] if fold else words


_KQL_WORD = _mre.compile(r"[^\W_][\w.'-]*", _mre.U)


def _sender_word(query, most=2):
    """For a query of one to `most` plain words: the text to try as sender=
    (quotes dropped); None for KQL syntax (subject:, AND …) and for
    order/invoice-like numbers (3+ digits in a word), whose 0 hits are real."""
    words = (query or "").replace('"', " ").split()
    if not 1 <= len(words) <= most or any(
            not _KQL_WORD.fullmatch(w) or w.casefold() in _KQL_OPS or sum(c.isdigit() for c in w) > 2
            for w in words):
        return None
    return " ".join(words)


# A hyphen between letters ("Hi-Fi"): mail_search also searches the word joined ("HiFi").
_HYPHEN = _mre.compile(r"(?<=[^\W\d_])-(?=[^\W\d_])")


def _domain_hint(query, msgs):
    """For a query of 1–3 plain words whose hits all come from one sender
    domain, none with the words in its From (name or address; spaces and
    hyphens ignored): a hint to try sender=<the words joined, lower case>
    ("Hi-Fi Klubben" found only PayPal's mails); None otherwise."""
    words = _sender_word(query, 3)
    if not msgs or not words or any(_from_has(m, words) for m in msgs):
        return None
    sites = {_msite(((m.get("from") or {}).get("emailAddress") or {}).get("address") or "") for m in msgs}
    if len(sites) != 1:
        return None
    joined = _mre.sub(r"[\W_]+", "", _fold(words))
    return (f'[hint: all {len(msgs)} hits are from {sites.pop()}, none from "{words}"; '
            f'also try sender="{joined}"]')


def _accent_hint(query, msgs):
    """For a one-word `query`: a hint line when more than half of `msgs` don't
    have the word in their subject or From, accents counted (KQL matches
    "über" for Uber, and words anywhere in the mail); None otherwise."""
    word = (query or "").strip().strip('"').strip()
    if not msgs or not _KQL_WORD.fullmatch(word):
        return None
    want = word.casefold()
    miss = sum(want not in f"{m.get('subject') or ''} {_sender(m)}".casefold() for m in msgs)
    if miss * 2 <= len(msgs):
        return None
    return (f'[hint: {miss} of {len(msgs)} hits lack "{word}" in subject and From: KQL ignores '
            f'accents and matches words anywhere; for a merchant pass sender="{word}"]')


@_mboth
def mail_list(since, until=None, folder=None, reveal_codes=False):
    """Every mail received from `since` to `until` (exclusive; forms as in the
    header), newest first: [{id, date, from, subject}], subjects masked as in
    mail_search. `folder`: a folder id or a well-known name (inbox, junkemail,
    sentitems, archive, deleteditems …); None: all folders."""
    flt = f"receivedDateTime ge {_iso(_since(since))}"
    if until is not None:
        flt += f" and receivedDateTime lt {_iso(_since(until))}"
    args = {"select": _LIST_FIELDS, "filter": flt, "orderby": ["receivedDateTime desc"]}
    tool = "list-mail-messages"
    if folder:
        tool, args["mailFolderId"] = "list-mail-folder-messages", folder
    return _hits(_collect(tool, args, lambda m: True, None)[0], reveal_codes)


_SCAN_FIELDS = _LIST_FIELDS + ["bodyPreview", "parentFolderId"]
_FOLDER_NAMES = {}  # folder id → "Name" or "Parent/Child", read once per kernel


def _folder_names():
    """{folder id: display name} of every mail folder, child folders as
    "Parent/Child"; {} when the folders can't be listed."""
    if not _FOLDER_NAMES:
        todo = [("list-mail-folders", {}, "")]
        try:
            while todo:
                tool, args, prefix = todo.pop()
                args = dict(args, select=["id", "displayName", "childFolderCount"])
                for f in _pages(tool, args):
                    name = prefix + (f.get("displayName") or "?")
                    _FOLDER_NAMES[f["id"]] = name
                    if f.get("childFolderCount"):
                        todo.append(("list-mail-child-folders", {"mailFolderId": f["id"]}, name + "/"))
        except RuntimeError:
            pass
    return _FOLDER_NAMES


def _classifier(classify):
    """mail_scan's `classify` as a function row → label or None."""
    if classify is None or callable(classify):
        return classify
    rules = [(label, _mre.compile(rx, _mre.I) if isinstance(rx, str) else rx) for label, rx in classify.items()]

    def by_rules(row):
        text = f"{row['subject']}\n{row['preview']}\n{row['from']}"
        folded = _fold(text)
        return next((label for label, rx in rules if rx.search(text) or rx.search(folded)), None)
    return by_rules


class _MScan(list):
    """mail_scan's rows, with .total (every mail of the period, all folders),
    .filter (the $filter used; with OR the filters as run, joined by " OR "),
    .counts ({label: n} with classify, else {}), .alternatives ({alternative:
    mails} for a query with OR, else {})."""
    total, filter, counts, alternatives = 0, "", {}, {}

    def __await__(self):
        return self
        yield  # noqa


_SCAN_TOKEN = _mre.compile(r'"([^"]*)"?|(\S+)')


def _scan_alternatives(query):
    """mail_scan's `query` as alternatives: split at OR (any case, outside
    quotes), each a list of terms that must all match: a "quoted phrase"
    whole, every other word alone (_query_words: subject: prefixes and AND/NOT
    dropped). [] for no words."""
    alts, terms = [], []
    for m in _SCAN_TOKEN.finditer(query or ""):
        if m[2] is not None and m[2].casefold() == "or":
            alts.append(terms)
            terms = []
        elif m[1] is not None:
            phrase = " ".join(m[1].split())
            if phrase:
                terms.append(phrase)
        else:
            terms += _query_words(m[2], fold=False)
    alts.append(terms)
    return list(dict.fromkeys(tuple(dict.fromkeys(t)) for t in alts if t))


def _scan_label(terms):
    return " ".join(f'"{t}"' if " " in t else t for t in terms)


@_mboth
def mail_scan(since, until=None, senders=None, query=None, classify=None):
    """Every mail received from `since` to `until` (exclusive; forms as in the
    header) in all folders (Junk and Deleted Items too, which KQL skips), every
    page, filtered on the server ($filter, no KQL): `senders` (a str or a list:
    part of the From address or name, as mail_search's sender=; any of them),
    `query` (words that must each be in the subject or the From; not the body:
    Graph can't filter on it; a "quoted phrase" counts as one term; OR between
    alternatives: one server filter per alternative, merged without
    duplicates, each alternative's count printed). Newest first: [{id, date,
    from, subject, folder, preview}], plus "class" with `classify`: a function
    row → label (or None), or {label: regex} (the first regex found in
    subject, preview or From, case ignored, accents also ignored). Login codes
    and one-time-link tokens come masked. The result has .total, .filter,
    .counts and .alternatives (_MScan); prints one summary line, and a warning
    when the server counted more than were read."""
    since_dt, until_dt = _since(since), _since(until)
    if since_dt is None:
        raise ValueError("mail_scan needs since= (Graph filters contains() only inside a date range)")
    period = f"receivedDateTime ge {_iso(since_dt)}"
    if until_dt:
        period += f" and receivedDateTime lt {_iso(until_dt)}"
    senders = [senders] if isinstance(senders, str) else list(senders or [])
    senders = [s.strip() for s in senders if s and s.strip()]
    alts = _scan_alternatives(query)
    words = list(alts[0]) if len(alts) == 1 else []
    base = [_sender_filter(senders)] if senders else []
    filters = [" and ".join([period] + base + [_word_filter(w) for w in terms]) for terms in alts or [()]]
    clauses = bool(base or alts)
    args = {"select": _SCAN_FIELDS, "orderby": ["receivedDateTime desc"], "count": True}
    meta, note, found, short, by_alt = {}, "", {}, False, {}
    try:
        for terms, flt in zip(alts or [()], filters):
            one = {}
            msgs = list(_pages("list-mail-messages", dict(args, filter=flt), one))
            short |= one.get("count") is not None and one["count"] != len(msgs)
            meta["count"] = meta.get("count", 0) + (one["count"] if one.get("count") is not None else len(msgs))
            meta["read"] = meta.get("read", 0) + len(msgs)
            by_alt[_scan_label(terms)] = len(msgs)
            for m in msgs:
                found.setdefault(m["id"], m)
    except RuntimeError as e:
        if not clauses:
            raise
        # the server refused the contains() filter: list the period, filter here
        note = f"; the server refused the filter ({str(e)[:120]}): listed the period and filtered here"
        filters, meta, found, by_alt = [period], {}, {}, {}
        listed = list(_pages("list-mail-messages", dict(args, filter=period), meta))
        for terms in alts or [()]:
            folded = [_fold(w) for w in terms]
            hits = [m for m in listed
                    if (not senders or any(_from_has(m, s) for s in senders))
                    and all(w in _fold(f"{m.get('subject') or ''} {_sender(m)}") for w in folded)]
            by_alt[_scan_label(terms)] = len(hits)
            for m in hits:
                found.setdefault(m["id"], m)
        meta = {"count": None, "total": meta.get("count")}
    msgs = list(found.values())
    if clauses and "total" not in meta:
        r = _raw_json("list-mail-messages", {"select": ["id"], "filter": period, "count": True, "top": 1})
        meta["total"] = r.get("@odata.count")
    names = _folder_names()
    label = _classifier(classify)
    rows = _MScan()
    for m in sorted(msgs, key=lambda m: m["receivedDateTime"], reverse=True):
        _mmask_json(_mask_json(m))
        row = {"id": m["id"], "date": _local(m["receivedDateTime"]), "from": _sender(m),
               "subject": m.get("subject") or "", "folder": names.get(m.get("parentFolderId"), "?"),
               "preview": _mre.sub(r"\s+", " ", m.get("bodyPreview") or "").strip()}
        if label:
            row["class"] = label(row)
        rows.append(row)
    rows.total = meta.get("total") if clauses else meta.get("count", len(rows))
    rows.filter = filters[0] if len(filters) == 1 else " OR ".join(f"({f})" for f in filters)
    rows.alternatives = by_alt if len(alts) > 1 else {}
    rows.counts = dict(_mcollections.Counter(r["class"] for r in rows if r.get("class"))) if label else {}
    folders = _mcollections.Counter(r["folder"] for r in rows)
    end = _local(_iso(until_dt)) if until_dt else "now"
    line = (f"[mail_scan: {len(rows)} of {rows.total} mails {_local(_iso(since_dt))} → {end}, all folders ("
            + ", ".join(f"{k} {n}" for k, n in folders.most_common()) + ")")
    line += f"; senders={senders}" if senders else ""
    line += f"; words={words}" if words else ""
    if rows.alternatives:
        line += ("; any of (one server filter each, merged): "
                 + ", ".join(f"{k} {n}" for k, n in rows.alternatives.items()))
    if label:
        unclassified = len(rows) - sum(rows.counts.values())
        line += "; classes: " + ", ".join(f"{k} {n}" for k, n in rows.counts.items()) + f", none {unclassified}"
    print(line + note + "]")
    if short:
        print(f"[warning: the server counted {meta['count']} mails, {meta['read']} read: run it again]")
    return rows


class _HtmlText(_mhtmlparser.HTMLParser):
    """HTML body → text lines: blocks on their own lines, links as <url>,
    no head/style/script, hidden elements or images."""
    _BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table",
              "section", "article", "header", "footer", "blockquote", "hr", "ul", "ol"}
    _VOID = {"br", "hr", "img", "meta", "link", "input", "col", "area", "base", "wbr", "source"}

    def __init__(self, links):
        super().__init__(convert_charrefs=True)
        self.links, self.out, self.skip, self.href = links, [], [], None

    def handle_starttag(self, tag, attrs):
        if self.skip:
            if tag == self.skip[0]:
                self.skip[1] += 1
            return
        a = dict(attrs)
        style = (a.get("style") or "").replace(" ", "").lower()
        if tag not in self._VOID and (tag in ("head", "style", "script", "title")
                                      or "display:none" in style or "hidden" in a):
            self.skip = [tag, 1]
        elif tag in self._BLOCK:
            self.out.append("\n")
        elif tag in ("td", "th"):
            self.out.append("  ")
        elif tag == "a" and (a.get("href") or "").startswith(("http", "mailto:")):
            self.href = a["href"]

    def handle_endtag(self, tag):
        if self.skip:
            if tag == self.skip[0]:
                self.skip[1] -= 1
                if not self.skip[1]:
                    self.skip = []
            return
        if tag in self._BLOCK:
            self.out.append("\n")
        elif tag == "a" and self.href:
            if self.links:
                self.out.append(f"<{self.href}>")
            self.href = None

    def handle_data(self, data):
        if not self.skip:
            self.out.append(_mre.sub(r"\s+", " ", data))


_INVISIBLE = _mre.compile("[\u00ad\u034f\u180e\u200b-\u200f\u2060-\u2064\ufeff]")
_LINK = _mre.compile(r"<(?:https?://|mailto:)[^>\s]*>")


def _drop_repeats(items):
    """Drop runs of 1–12 lines that repeat the lines right before them."""
    out, i = [], 0
    while i < len(items):
        for p in range(1, 13):
            if len(out) >= p and [k for _, k, _ in out[-p:]] == [k for _, k, _ in items[i:i + p]]:
                i += p
                break
        else:
            out.append(items[i])
            i += 1
    return out


def _drop_copies(items):
    """Drop blocks of >= 3 lines and >= 80 characters shown further up already."""
    out, i = [], 0
    while i < len(items):
        best = 0
        for j, (_, k, _) in enumerate(out):
            if k != items[i][1]:
                continue
            m = 0
            while j + m < len(out) and i + m < len(items) and out[j + m][1] == items[i + m][1]:
                m += 1
            if m >= 3 and sum(len(k) for _, k, _ in items[i:i + m]) >= 80:
                best = max(best, m)
        if best:
            i += best
        else:
            out.append(items[i])
            i += 1
    return out


def _clean_lines(text, links):
    """Lines without invisible padding, image placeholders, repeats of the
    previous lines or copies of earlier blocks (mobile + desktop layouts)."""
    text = _INVISIBLE.sub("", text.replace("\r\n", "\n").replace("\xa0", " "))
    text = _mre.sub(r"\[(?:https?|cid):[^\]]*\]", "", text)  # [image url] placeholders
    items, blank = [], False  # (line, comparison key, blank line before it)
    for raw in text.split("\n"):
        line = _mre.sub(r"[ \t]+", " ", raw if links else _LINK.sub("", raw)).strip()
        key = _LINK.sub("", line).strip().casefold()
        if key:
            items.append((line, key, blank and bool(items)))
        blank = not key
    while True:
        n = len(items)
        items = _drop_copies(_drop_repeats(items))
        if len(items) == n:
            break
    return "\n".join(("\n" if b else "") + line for line, _, b in items)


def _raw_body(msg, links):
    """The body as text (HTML converted), before any cleaning."""
    body = msg.get("body") or {}
    text = body.get("content") or ""
    if body.get("contentType") == "html":
        p = _HtmlText(links)
        p.feed(text)
        p.close()
        text = "".join(p.out)
    return text


def _body_text(msg, links):
    return _clean_lines(_raw_body(msg, links), links)


def _get(mid):
    return _raw_json("get-mail-message", {
        "messageId": mid,
        "select": ["id", "receivedDateTime", "from", "toRecipients", "subject", "body"]})


def _render(sender, to, received, subject, body, max_chars, reveal_codes):
    """A mail as text: From/To/Date/Subject, then the body; codes and
    one-time-link tokens masked."""
    if not reveal_codes:
        subject, found = _mask(subject)
        body, found = _mask(body, found)
        body = _mask_links(body)
        subject = _mask(subject, found)[0]
    text = _mmask_text(f"From: {sender}\nTo: {to}\nDate: {_local(received)}\nSubject: {subject}\n\n{body}")
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n… [cut: {len(text) - max_chars} more characters]"
    return text


@_mboth
def mail_text(id, max_chars=20000, links=False, reveal_codes=False):
    """One mail as readable text: From/To/Date/Subject, then the body with its
    duplicated blocks removed. links=True keeps link addresses as <url> (with
    one-time-link tokens as •••: mail_links has the real URL). Login
    codes come masked unless reveal_codes=True (not for agents)."""
    m = _get(id)
    to = ", ".join(_sender({"from": r}) for r in m.get("toRecipients") or [])
    return _render(_sender(m), to, m["receivedDateTime"], m.get("subject") or "",
                   _body_text(m, links), max_chars, reveal_codes)


_MTEXTS_SEP = f"\n\n{'=' * 72}\n\n"


class _MTexts(list):
    """mail_texts' result: a list of per-mail texts whose str()/print() is
    them joined between ==== lines. `"x" in texts` and str methods
    (texts.splitlines() …) work on the joined text; `await texts` gives itself."""
    def __str__(self):
        return _MTEXTS_SEP.join(self)

    def __format__(self, spec):
        return format(str(self), spec)

    def __contains__(self, item):
        return item in str(self) if isinstance(item, str) else list.__contains__(self, item)

    def __getattr__(self, name):  # only for names a list lacks
        if name.startswith("__"):
            raise AttributeError(name)
        return getattr(str(self), name)

    def __await__(self):
        return self
        yield  # noqa


# read-mail-text refuses an answer over the server's size limit
# (MS365_MCP_READ_MAIL_TEXT_MAX_CHARS, 80,000): "Response would be 83,712
# characters (limit 80,000)".
_TOO_BIG = _mre.compile(r"would be\s+[\d,.\s]+characters?\s*\(limit\s+([\d,.]+)", _mre.I)


def _read_texts(ids, max_chars):
    """{id: read-mail-text entry} for `ids`. Entries Graph throttled (no text,
    an ApplicationThrottled-like error) are asked for again, alone, after
    _RETRY_PAUSES, and kept as they last came. An answer over the server's
    size limit is read again as two halves, down to one mail; a single mail
    still too big gets a smaller maxChars and, if that cut it (truncated),
    `cut_at` = that maxChars (under 1000: the error is raised)."""
    try:
        got = _raw_json("read-mail-text", {"messageIds": ids, "maxChars": max_chars})
    except RuntimeError as e:
        big = _TOO_BIG.search(str(e))
        if not big:
            raise
        if len(ids) > 1:
            half = (len(ids) + 1) // 2
            return {**_read_safe(ids[:half], max_chars), **_read_safe(ids[half:], max_chars)}
        limit = int(_mre.sub(r"\D", "", big[1]) or 80000)
        smaller = min(max_chars, limit) // 2  # JSON escaping can double the text
        if smaller < 1000:
            raise
        by_id = _read_texts(ids, smaller)
        for r in by_id.values():
            if isinstance(r, dict) and "text" in r and r.get("truncated"):
                r.setdefault("cut_at", smaller)
        return by_id
    by_id = {r.get("id"): r for r in got if isinstance(r, dict)}
    todo = [m for m in ids if _throttled_entry(by_id.get(m))]
    for pause in _RETRY_PAUSES:
        if not todo:
            break
        _pause(pause)
        got = _raw_json("read-mail-text", {"messageIds": todo, "maxChars": max_chars})
        by_id.update((r.get("id"), r) for r in got if isinstance(r, dict))
        todo = [m for m in todo if _throttled_entry(by_id.get(m))]
    return by_id


def _read_safe(ids, max_chars):
    """_read_texts, with an error (after its retries) as an {"id", "error"}
    entry for each of `ids` instead of an exception."""
    try:
        return _read_texts(ids, max_chars)
    except (RuntimeError, OSError) as e:
        return {m: {"id": m, "error": str(e)[:300]} for m in ids}


def _throttled_entry(r):
    return isinstance(r, dict) and "text" not in r and bool(_THROTTLED.search(_mjson.dumps(r)[:2000]))


@_mboth
def mail_texts(ids, max_chars=20000, links=False, reveal_codes=False):
    """Several mails: a list of texts in the order of `ids`, each as mail_text
    shows it (with its date); print() shows them joined between ==== lines.
    Reads 20 mails per MCP call (read-mail-text, which also drops quoted reply
    history); mails Graph throttled are read again (backoff as in _call) before
    their entry says so; a batch over the server's size limit (80,000
    characters) is read in halves, and a single mail over it comes cut, marked
    "[cut at N chars …]". Errors become that mail's entry, never an exception."""
    ids, out = list(ids), _MTexts()
    for i in range(0, len(ids), 20):
        chunk = ids[i:i + 20]
        by_id = _read_safe(chunk, min(max_chars, 300000))
        for mid in chunk:
            r = by_id.get(mid)
            if r is None or "text" not in r or "received" not in r:
                why = (r.get("error") or r) if r else "not returned"
                out.append(f"[{mid}: {_mask(str(why))[0][:300]}]")
                continue
            text = _render(r.get("from") or "", ", ".join(r.get("to") or []), r["received"],
                           r.get("subject") or "", _clean_lines(r["text"], links),
                           max_chars, reveal_codes)
            if r.get("cut_at"):
                text += f"\n… [cut at {r['cut_at']} chars: the whole mail is over read-mail-text's size limit]"
            out.append(text)
    return out


# Links not to request: unsubscribe, confirm, log-in, delete, cancel, vote … (label,
# host or path; an HTTP HEAD can trigger them like a click).
_SKIP_LINK = _mre.compile(
    r"unsub|abmeld|abbestell|austrag|opt-?out|preference|einstellung|confirm|bestätig|verif|"
    r"activat|aktivier|login|log-in|signin|sign-in|anmeld|passw|reset|delete|lösch|cancel|storn|"
    r"kündig|widerruf|accept|decline|ablehn|zustimm|approve|rsvp|survey|umfrage|feedback|bewert",
    _mre.I)
_MUA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"


class _NoRedirect(_murlrequest.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # a 3xx raises HTTPError; _resolve checks each hop itself


def _skip_url(url):
    parts = _murlparse.urlsplit(url)
    return bool(_SKIP_LINK.search(parts.netloc + parts.path))


def _resolve(url, hops=10, timeout=10):
    """(final URL, note or None): HEAD requests along the redirects, stopping
    before a hop that _SKIP_LINK names."""
    opener = _murlrequest.build_opener(_NoRedirect)
    for _ in range(hops):
        req = _murlrequest.Request(url, method="HEAD", headers={"User-Agent": _MUA})
        try:
            with opener.open(req, timeout=timeout):
                return url, None
        except _murlerror.HTTPError as e:
            loc = e.headers.get("Location")
            if not (300 <= e.code < 400 and loc):
                return url, f"HTTP {e.code}"
            url = _murlparse.urljoin(url, loc)
            if _skip_url(url):
                return url, "not requested: an unsubscribe/confirm/login-like link"
        except (OSError, ValueError) as e:  # URLError, timeouts, bad URLs
            return url, f"error: {e}"
    return url, f"more than {hops} redirects"


# Link hosts mail_links(resolve=True) requests besides the sender's own site:
# parcel and shop trackers.
_TRACKERS = {"dhl.de", "dhl.com", "hermesworld.com", "myhermes.de", "dpd.de", "dpd.com", "ups.com",
             "gls-group.eu", "gls-pakete.de", "amazon.de", "amazon.com", "trackyourparcel.eu",
             "evri.com", "globaleco.app", "deutschepost.de"}
_SECOND_LEVEL = {"co", "com", "net", "org", "or", "gv", "ac", "gov", "edu", "ne", "go"}


def _msite(host):
    """The registrable domain of `host` (no public suffix list: the last two
    labels, three under a 2-letter TLD's co./com./or. … level): "nolp.dhl.de" →
    "dhl.de", "shop.example.co.uk" → "example.co.uk"."""
    labels = (host or "").lower().rstrip(".").split("@")[-1].split(":")[0].split(".")
    n = 3 if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL else 2
    return ".".join(labels[-n:])


@_mboth
def mail_links(id, match=None, resolve=False, reveal_codes=False):
    """A mail's web links: [{label, url, final}], label = the text before the
    link on its line. `match`: a regex (case-insensitive) on label or url that
    picks links. resolve=True: follow links' redirects with HEAD requests, so a
    tracking link shows its target (DHL's piececode …) in `final`; only links on
    the From address's own site or a parcel/shop tracker (_TRACKERS) are
    requested (a HEAD to a phishing host confirms the address), others get final
    None and a `note`, as do unsubscribe/confirm/login/delete-like links.
    Login codes come masked unless reveal_codes=True."""
    return _links_of(_get(id), match, resolve, reveal_codes)


def _found_codes(m):
    """The login codes of message `m` (body and subject), for masking them elsewhere."""
    return _mask(_body_text(m, links=False), _mask(m.get("subject") or "")[1])[1]


# A link in a mail body: Graph's text form "label<https://…>" (_LINK), or a bare
# https URL in a plain-text body (DARS's confirm link, MOVE-0212); a bare one
# loses its trailing sentence punctuation and an unbalanced ")".
_LINK_OR_BARE = _mre.compile(_LINK.pattern + r"|(?<![\w@/=\"'])https?://[^\s<>\"'\[\]]+")


def _bare_url(url):
    """A bare URL without the punctuation that ends the sentence around it."""
    url = url.rstrip(".,;:!?'\"")
    while url.endswith(")") and url.count(")") > url.count("("):
        url = url[:-1].rstrip(".,;:!?")
    return url


def _links_of(m, match, resolve, reveal_codes):
    """mail_links for the message `m` already read. A bare URL with no text
    before it on its line takes the line above (≤ 80 characters) as label."""
    text = _INVISIBLE.sub("", _raw_body(m, links=True).replace("\r\n", "\n").replace("\xa0", " "))
    text = _mre.sub(r"\[(?:https?|cid):[^\]]*\]", "", text)  # [image url] placeholders
    links, by_url, above = [], {}, ""
    for line in text.split("\n"):
        pos = 0
        for lm in _LINK_OR_BARE.finditer(line):
            bare = not lm[0].startswith("<")
            if bare and line.startswith("<", lm.end()):  # Graph's "https://x.de<https://x.de/…>": its label
                continue
            url = _bare_url(lm[0]) if bare else lm[0][1:-1]
            label = _mre.sub(r"\s+", " ", line[pos:lm.start()]).strip(" :|-–")
            if bare and not label and pos == 0 and len(above) <= 80:
                label = above.strip(" :|-–")
            pos = lm.start() + len(url) if bare else lm.end()
            if not url.startswith("http"):
                continue
            if url in by_url:
                by_url[url]["label"] = by_url[url]["label"] or label
                continue
            by_url[url] = {"label": label, "url": url, "final": None}
            links.append(by_url[url])
        if line.strip():
            above = _mre.sub(r"\s+", " ", line).strip()
    if match:
        links = [x for x in links if _mre.search(match, x["label"], _mre.I)
                 or _mre.search(match, x["url"], _mre.I)]
    if resolve:
        todo = []
        sender_site = _msite((((m.get("from") or {}).get("emailAddress") or {}).get("address") or ""))
        for x in links:
            try:
                site = _msite(_murlparse.urlsplit(x["url"]).hostname or "")
            except ValueError:  # a malformed URL: never requested
                site = ""
            if not site or site != sender_site and site not in _TRACKERS:
                x["note"] = "not resolved (not the sender's site)"
            elif _SKIP_LINK.search(x["label"]) or _skip_url(x["url"]):
                x["note"] = "not requested: an unsubscribe/confirm/login-like link"
            else:
                todo.append(x)
        with _mfutures.ThreadPoolExecutor(8) as pool:
            for x, (final, note) in zip(todo, pool.map(_resolve, [x["url"] for x in todo])):
                x["final"] = final
                if note:
                    x["note"] = note
        unique, seen = [], set()  # tracking links differ per click: one per label and target
        for x in links:
            key = (x["label"], x["final"] or x["url"])
            if key not in seen:
                seen.add(key)
                unique.append(x)
        links = unique
    if not reveal_codes:
        found = _found_codes(m)
        for x in links:
            for k in ("label", "url", "final"):
                if x.get(k):
                    x[k] = _mmask_text(_mask(x[k], found)[0])
    return links


# Links open_mail_link never opens: a click on them can unsubscribe, delete,
# cancel, revoke or decline (hard rules: no cancellations or contract changes).
_NO_OPEN_LINK = _mre.compile(
    r"unsub|abmeld|abbestell|austrag|opt-?out|delete|lösch|cancel|storn|kündig|widerruf|revoke|"
    r"decline|ablehn|deactivat|deaktivier", _mre.I)


async def open_mail_link(tab, id, match=None, index=None):
    """Open one of a mail's links in the ShopTab `tab` (tab.goto) with its real
    URL: one-time links (magic login, confirm e-mail, "Download Data") whose
    token mail_links(id) shows as •••. Pick it by `index` (the order of
    mail_links(id)) or `match`, a regex (case-insensitive) on its label or its
    URL's host and path (as mail_links shows them); several matches open the first.
    Only links on the From address's own site or a known tracker/shop host
    (_TRACKERS, as mail_links(resolve=True)) are opened, never unsubscribe/
    delete/cancel-like ones (_NO_OPEN_LINK): else ValueError. Returns {label, host},
    never the URL; errors name the host only."""
    if (match is None) == (index is None):
        raise ValueError("open_mail_link: pass match= or index= (one of them)")

    def read_links():  # to_thread runs it in a copy of the context: _REVEAL stays set there only
        _REVEAL.set(True)
        m = _get(id)
        found, links, by_key = _found_codes(m), [], {}
        for x in _links_of(m, None, False, True):  # as mail_links(id) dedups its masked URLs
            key = _mmask_text(_mask(_mask_links(x["url"]), found)[0])
            label = _mmask_text(_mask(_mask_links(x["label"]), found)[0])
            if key in by_key:
                by_key[key]["label"] = by_key[key]["label"] or label
                continue
            by_key[key] = {"label": label, "shown": key, "url": x["url"]}
            links.append(by_key[key])
        sender = ((m.get("from") or {}).get("emailAddress") or {}).get("address") or ""
        return links, _msite(sender)
    links, sender_site = await _masyncio.to_thread(read_links)
    if index is not None:
        if not -len(links) <= index < len(links):
            raise IndexError(f"open_mail_link: index {index}, but the mail has {len(links)} links")
        pick = links[index]
    else:
        def shown(x):
            parts = _murlparse.urlsplit(x["shown"])
            return parts.netloc + parts.path
        hits = [x for x in links if _mre.search(match, x["label"], _mre.I) or _mre.search(match, shown(x), _mre.I)]
        if not hits:
            raise LookupError(f"open_mail_link: no link matches {match!r} ({len(links)} links)")
        pick = hits[0]
    try:
        parts = _murlparse.urlsplit(pick["url"])
        host = (parts.hostname or "").lower()
    except ValueError:
        parts, host = None, ""
    label = pick["label"]
    if not parts or parts.scheme not in ("http", "https") or not host:
        raise ValueError(f"open_mail_link: not opened, not a web link ({label!r})")
    site = _msite(host)
    if site != sender_site and site not in _TRACKERS:
        raise ValueError(f"open_mail_link: not opened, {host} is not the sender's site ({sender_site}) "
                         f"nor a known tracker ({label!r})")
    if _NO_OPEN_LINK.search(label) or _NO_OPEN_LINK.search(parts.netloc + parts.path):
        raise ValueError(f"open_mail_link: not opened, an unsubscribe/delete/cancel-like link ({label!r} on {host})")
    try:
        await tab.goto(pick["url"])
    except Exception as e:  # its message may name the URL: only the host leaves here
        msg = _mask_links(str(e).replace(pick["url"], f"<the link on {host}>"))
        try:
            err = type(e)(f"open_mail_link: {msg}")
        except Exception:
            err = RuntimeError(f"open_mail_link: {type(e).__name__}: {msg}")
        raise err from None
    return {"label": label, "host": host}


# mail_attachments(text=True): which attachments are read as text here, and
# which go through vault-fill's pdf_text (text layer, OCR for scans).
_TEXT_ATTACHMENT = _mre.compile(r"^text/|json|xml|csv|calendar|vcard|\.(?:txt|csv|tsv|ics|vcf|json|xml|html?|md|log)$",
                                _mre.I)
_HTML_ATTACHMENT = _mre.compile(r"html|\.html?$", _mre.I)
_PDF_ATTACHMENT = _mre.compile(r"pdf|^image/(?:jpe?g|png|tiff|gif|webp)|\.(?:pdf|jpe?g|png|tiff?|gif|webp)$", _mre.I)


def _attachment_text(a, data, path, max_chars):
    """Attachment `a`'s text (its bytes `data`, saved at `path`), masked as
    mail_text masks a body (login codes, codes under a label, passwords,
    one-time links, IBANs, cards); raises ValueError for a kind it can't read."""
    kind = f"{a['type']} {a['name']}"
    if _PDF_ATTACHMENT.search(a["type"]) or _PDF_ATTACHMENT.search(a["name"]):
        pdf_text = globals().get("pdf_text")
        if pdf_text is None:
            raise ValueError("a PDF/image needs vault-fill.py %load-ed in this kernel (its pdf_text reads it)")
        with _mfutures.ThreadPoolExecutor(1) as pool:  # its own event loop: this runs inside the kernel's
            text = pool.submit(_masyncio.run, pdf_text(path)).result()
    elif _TEXT_ATTACHMENT.search(a["type"]) or _TEXT_ATTACHMENT.search(a["name"]):
        charset = _mre.search(r"charset=([\w.-]+)", a["type"], _mre.I)
        try:
            text = data.decode(charset[1] if charset else "utf-8-sig")
        except (UnicodeDecodeError, LookupError):
            text = data.decode("cp1252", errors="replace")
        if _HTML_ATTACHMENT.search(a["type"]) or _HTML_ATTACHMENT.search(a["name"]):
            text = _body_text({"body": {"contentType": "html", "content": text}}, links=False)
    else:
        raise ValueError(f"no text reader for {kind.strip()!r} (text, HTML, CSV, ICS, JSON, XML, PDF, images)")
    text = _mmask_text(_mask_links(_mask(text)[0]))
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n… [cut: {len(text) - max_chars} more characters]"
    return text


@_mboth
def mail_attachments(id, save=False, match=None, inline=False, text=False, max_chars=20000):
    """A mail's attachments: [{id, name, type, size, inline}]. Inline ones
    (logos, images in the body) are left out unless inline=True; `match`: a
    regex (case-insensitive) on name or content type, e.g. "pdf". save=True also
    writes each file into a new private temp dir (0700, files 0600) and adds its
    `path`; delete the dir when done. text=True adds each one's `text` (≤
    max_chars), masked as mail_text masks a body (login codes, also a
    Freischaltcode/Rezeptcode under its label or in OCR spacing, passwords,
    one-time links, IBANs, cards): text, HTML, CSV, ICS, JSON and XML read
    here, PDFs and images through vault-fill's pdf_text (OCR for scans; needs
    vault-fill.py %load-ed); without save=True its files are deleted
    afterwards. An attachment that has no file content (an attached mail) or
    no readable text gets an `error` instead."""
    out = [{"id": a["id"], "name": a.get("name") or "", "type": a.get("contentType") or "",
            "size": a.get("size"), "inline": bool(a.get("isInline"))}
           for a in _pages("list-mail-attachments", {
               "messageId": id, "select": ["id", "name", "contentType", "size", "isInline"]})]
    if match:
        out = [a for a in out if _mre.search(match, a["name"], _mre.I) or _mre.search(match, a["type"], _mre.I)]
    skipped = 0 if inline else sum(a["inline"] for a in out)
    if skipped:
        out = [a for a in out if not a["inline"]]
        print(f"[{skipped} inline attachment{'s' * (skipped > 1)} (logos, body images) left out; inline=True keeps them]")
    if not (save or text) or not out:
        return out
    folder, used = _mtempfile.mkdtemp(prefix="mail-attachments-"), set()
    try:
        for i, a in enumerate(out):
            try:
                c = _raw_json("get-mail-attachment-content", {"messageId": id, "attachmentId": a["id"]})
                data = _mbase64.b64decode(c["contentBytes"])
            except KeyError:
                a["error"] = f"no contentBytes in the answer (keys: {sorted(c)})"
                continue
            except (RuntimeError, ValueError) as e:
                a["error"] = str(e)[:300]
                continue
            name = _mre.sub(r"[/\\\x00]", "_", a["name"]).lstrip(".") or "attachment"
            if name in used:
                name = f"{i}-{name}"
            used.add(name)
            path = _mos.path.join(folder, name)
            fd = _mos.open(path, _mos.O_WRONLY | _mos.O_CREAT | _mos.O_EXCL, 0o600)
            with _mos.fdopen(fd, "wb") as f:
                f.write(data)
            if save:
                a["path"] = path
            if text:
                try:
                    a["text"] = _attachment_text(a, data, path, max_chars)
                except Exception as e:  # noqa: BLE001 — that attachment's error, the others still come
                    a["error"] = _mmask_text(_mask(f"text: {type(e).__name__}: {e}")[0])[:300]
    finally:
        if not save:
            _mshutil.rmtree(folder, ignore_errors=True)
    return out


class MailCode:
    """A login code read from a mail (digits, or letters and digits). It is in
    .value: pass it on (fill_mail_code, tab.type_code), never print it."""
    __slots__ = ("_value", "sender", "subject", "date", "mail_id")

    def __init__(self, value, msg):
        self._value = value
        self.sender, self.subject = _sender(msg), _mask(msg.get("subject") or "", {value})[0]
        self.date, self.mail_id = _local(msg["receivedDateTime"]), msg["id"]

    @property
    def value(self):
        return self._value

    def __len__(self):
        return len(self._value)

    @property
    def kind(self):
        """digit, or character for an alphanumeric code: for messages."""
        return "digit" if self._value.isdigit() else "character"

    def __repr__(self):
        return f"<MailCode: {len(self._value)} {self.kind}s from {self.sender}, {self.date}, {self.subject!r}>"

    def __await__(self):  # `await mail_code(…)` works too
        return self
        yield  # noqa: unreachable; makes this a generator

    __str__ = __repr__

    def __format__(self, spec):
        return repr(self)

    def __reduce__(self):
        raise TypeError("a MailCode is not serialised")


# Words that name a login code (Code, Verifizierungscode, PIN, TAN, Einmalpasswort …),
# and the codes that aren't one. Other languages too (B-70, DeepSeek's "验证码为："):
# código/codice/kod/kode/koodi/kód and password words (es/pt/it/pl/sv/da/no/tr/fi/
# cs/hu/nl/fr/vi), Russian/Greek/Arabic/Hebrew/Hindi/Thai, and Chinese, Japanese
# and Korean ones, which have no word boundary (a katakana/hangul word before コード/
# 코드 belongs to it, so that クーポンコード/쿠폰 코드 meet _NOT_LOGIN).
_CODE_WORDS = _mre.compile(
    r"\b\w*(?:code|kennwort|passwort|password|passcode)\w*|\b(?:pin|tan|otp|einmal\w*|one[- ]time)\b"
    r"|\b\w*(?:c[óo]dig[oa]s?|codic[ei]|kod(?:e[nm]?|u\w*|y)?|koodi\w*|kód\w*|senha|contraseña|wachtwoord|"
    r"lösenord|hasło|şifre\w*|heslo|jelszó\w*)\b|\b(?:mot de passe|mã (?:xác (?:minh|nhận)|otp))\b"
    r"|\b(?:код\w*|парол\w*|одноразов\w*|κωδικ\w*)|\b\w*(?:رمز|كود)\w*|קוד|कोड|รหัส"
    r"|验证码|驗證碼|校验码|校驗碼|动态码|動態碼|确认码|確認碼|认证码|認證碼|安全码|安全碼|登录码|登錄碼|登入码|登入碼"
    r"|验证代码|驗證代碼|动态密码|動態密碼|一次性密码|一次性密碼|临时密码|臨時密碼"
    r"|認証コード|確認コード|認証番号|確認番号|暗証番号|ワンタイム(?:パスワード|コード)?|パスコード"
    r"|[\u30a1-\u30fa\u30fc]{0,12}コード|인증\s?번호|(?:[\uac00-\ud7a3]{1,8}\s?)?코드|일회용\s?비밀번호|비밀번호", _mre.I)
_NOT_LOGIN = _mre.compile(
    r"gutschein|rabatt|promo|voucher|coupon|discount|aktions|transakt|transaction|bar|qr|"
    r"post|zip|area|country|referral|empfehl|html|source|"
    r"kupong|cup[oó]n|cupom|descuento|desconto|sconto|купон|скидк|промо|クーポン|プロモ|招待|紹介|"
    r"쿠폰|할인|추천|초대|优惠|優惠|兑换|兌換|邀请|邀請", _mre.I)
# Scripts without spaces between words (kana, CJK, hangul): a code may touch them
# ("验证码为123456", "123456입니다"), so the boundaries below don't count them as word
# characters.
_CJK = "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff\uff66-\uff9f"
# 123456, 938 459 / 1234-5678, or one digit per line or cell (ELSTER); not a price
# (€, EUR, %, 円/元/¥).
_CODE = _mre.compile(r"(?<!(?![" + _CJK + r"])[\w€$£¥￥])(?<!\d[.,/:-])(?<!\d\s)"
                     r"(\d(?:\s{1,3}\d){3,7}(?!\s{0,3}\d)|\d{3,4}[ -]\d{3,4}(?![ -]?\d)|\d{4,8})"
                     r"(?!(?![" + _CJK + r"])[\w%€]|[.,:/-]\d|\s?(?:€|EUR\b|%|円|元))")


# Alphanumeric codes (myRyanair: 9a9aaa9a; K7Q2PX9M, K7Q2-PX9M): 4–12 letters
# and digits with at least one of each, or two equal halves of 3–6 joined by
# "-" or a space (then each half has a digit); not part of a URL, address, host
# name, #colour, price or longer word (kana/CJK/hangul next to it are no word: _CJK).
_ALNUM_START = _mre.compile(r"(?<!(?![" + _CJK + r"])[\w#@&=/\\.:+?%$€£-])[A-Za-z0-9]")
_ALNUM_END = r"(?!(?![" + _CJK + r"])[\w@]|[.:/=+\\-]\w|\s?(?:€|%))"
_ALNUM_TWO = _mre.compile(r"([A-Za-z0-9]{3,6})([ -])([A-Za-z0-9]{3,6})" + _ALNUM_END)
_ALNUM_ONE = _mre.compile(r"[A-Za-z0-9]{4,12}" + _ALNUM_END)
# Order, invoice, customer, booking, model … numbers are no login code.
_NOT_CODE_CONTEXT = _mre.compile(
    r"bestell|rechnung|kunden|auftrag|order|invoice|customer|buchung|booking|reserv|vertrag|"
    r"contract|sendung|tracking|shipment|paket|parcel|referenz|reference|ticket|modell|model|"
    r"artikel|article|serien|serial|produkt|product|iban|aktenzeichen|vorgang|postleitzahl|"
    r"pedido|factura|fattura|commande|facture|ordine|заказ|订单|訂單|运单|運單|快递|快遞|注文|予約|주문|예약", _mre.I)
_URLISH = _mre.compile(r"(?:https?://|www\.)[^\s<>\"']+|(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_UNIT = _mre.compile(r"\d+[A-Za-z]{1,3}")  # 10px, 15min, 128GB, 12th
_CLOCK = _mre.compile(r"\d{1,2}h\d{2}", _mre.I)
# What may stand between a code word and a code right after it: "Code: X", "code is X",
# "验证码为：X", "コードは X", "인증번호는 X".
_STRONG_GAP = _mre.compile(r"[\s:：=>»\"'„“”「」【】–-]*(?:(?:(?:is|ist|lautet|est|es|è|é)\b|[为為是は는은])[\s:：]*)?",
                           _mre.I)
_ALNUM_PENALTY = 60  # ranks an alphanumeric code 60 characters farther: digits win when unsure


def _alnum_ok(value, strong):
    """Whether `value` (separator removed) may be a login code: letters and
    digits mixed (≥ 2 letter/digit changes, or 1 right after a code word but
    no unit like 10px); no clock time (15h30) or _NOT_LOGIN word."""
    if not (any(c.isdigit() for c in value) and any(c.isalpha() for c in value)):
        return False
    if _NOT_LOGIN.search(value) or _CLOCK.fullmatch(value):
        return False
    shifts = sum(a.isdigit() != b.isdigit() for a, b in zip(value, value[1:]))
    return shifts >= 2 or (strong and not _UNIT.fullmatch(value))


def _alnum_matches(src, words, ends, gap):
    """(gap + _ALNUM_PENALTY, span, value) for the alphanumeric codes in `src`."""
    urls = [u.span() for u in _URLISH.finditer(src)]
    out, done = [], 0
    for s in _ALNUM_START.finditer(src):
        p = s.start()
        if p < done or any(a <= p < b for a, b in urls):
            continue
        for rx in (_ALNUM_TWO, _ALNUM_ONE):
            m = rx.match(src, p)
            if not m:
                continue
            if rx is _ALNUM_TWO:
                a, sep, b = m.groups()
                if len(a) != len(b) or (sep == " " and not (_mre.search(r"\d", a) and _mre.search(r"\d", b))):
                    continue
                value = a + b
            else:
                value = m[0]
            if not _mre.search(r"\d", value):
                continue
            end, i = m.end(), _mbisect.bisect_right(ends, p) - 1
            own_line = strong = False
            if i >= 0:
                between = src[ends[i]:p].split("\n")
                eol = src.find("\n", end)
                own_line = (len(between) >= 2 and len(between[0]) <= 80 and not "".join(between[1:]).strip()
                            and not src[end:eol if eol >= 0 else len(src)].strip(" ."))
                strong = own_line or bool(_STRONG_GAP.fullmatch(src[ends[i]:p]))
            if not _alnum_ok(value, strong):
                continue
            g = gap(p, end)
            if g is None and own_line:
                g = p - ends[i]
            if g is None:
                break
            context = src[max(0, p - 40):p] + " " + " ".join(w[0] for w in words if -40 <= w.start() - end <= 40)
            if _NOT_CODE_CONTEXT.search(context):
                break
            out.append((g + _ALNUM_PENALTY, (p, end), value))
            done = end
            break
    return out


# Letter-only codes (Booking.com: 6 capital letters, "Booking.com – ABCDEF is your
# verification code") and capital letters with digits (Booking.com too: "Booking.com -
# ABCDE9 is your verification code", one letter/digit change, which _alnum_ok wants
# right after a code word): 4–8 capital letters, or 4–10 capitals and digits with a
# capital, ≤ 40 characters from a code word, set off from the text (right after the
# code word, after a colon or dash, or alone on its line), or at a line's start or
# after a dash/colon before "is your … code".
# Not a code word, _NOT_LOGIN or _ALPHA_STOP word, unit (128GB) or clock time, not
# next to another capitalised word (headings), nor after an order/booking-like word
# (_NOT_CODE_CONTEXT; domains like Booking.com ignored).
_ALPHA = _mre.compile(r"(?<!(?![" + _CJK + r"])[\w#@&=/\\.:+?%$€£-])(?=[0-9]*[A-Z])[A-Z0-9]{4,10}" + _ALNUM_END)
_ALPHA_IS_YOUR = _mre.compile(r"[ \t]+(?:is|IS|ist|IST)[ \t]+(?i:your|dein|ihr)\b[^\n]{0,40}?(?i:code)")
_ALPHA_STOP = frozenset(
    "ABOUT ACCOUNT ACHTUNG ACTION ALLE AKTIV CLICK CONFIRM CONTACT COPY DATE DEIN DEINE EINFACH EMAIL ENTER FAQS "
    "FREE GILT GMBH HELP HERE HIER HILFE HTTP HTTPS IBAN IHRE IMPORTANT IMPRESSUM INFO JETZT KLICK KONTAKT KONTO "
    "LINK LOGIN LOGO MAIL MEHR NAME NEVER NEWS NICHT NIEMALS NOTE ONLINE OPEN PASS PRIVACY SALE SECURE SECURITY "
    "SEPA SERVICE SHOP SIGN SUPPORT SWIFT TEAM TERMS TEXT THAT THIS TIME TOTP USER VALID VERIFY WARNING WICHTIG "
    "WITH YOUR".split())
_ALPHA_JOIN = frozenset({"IS", "IST", "LAUTET"})  # "YOUR CODE IS ABCDEF"
_ALPHA_PENALTY = 120  # ranked behind digit and alphanumeric codes
_ALPHA_DOMAIN = _mre.compile(r"\b[\w-]+\.(?:com|de|at|ch|net|org|eu|io|co|uk|fr|es|it|nl)\b", _mre.I)
_PREV_WORD = _mre.compile(r"([^\W\d_]+)[^\w\n]*$")
_NEXT_WORD = _mre.compile(r"[^\w\n]*([^\W\d_]+)")
_ALPHA_LEAD = _mre.compile(r"(?:^|[:–—|-])[ \t]*$")  # line start, "Code:", "Booking.com – "


def _alpha_matches(src, words, starts, ends):
    """(gap + _ALPHA_PENALTY, span, value) for the letter-only codes in `src`."""
    urls = [u.span() for u in _URLISH.finditer(src)]
    out = []
    for m in _ALPHA.finditer(src):
        p, end, value = m.start(), m.end(), m[0]
        if value.isalpha() and len(value) > 8 or not value.isalpha() and (_UNIT.fullmatch(value)
                                                                          or _CLOCK.fullmatch(value)):
            continue
        if (value in _ALPHA_STOP or _CODE_WORDS.fullmatch(value) or _NOT_LOGIN.search(value)
                or any(a <= p < b for a, b in urls)):
            continue
        bol, eol = src.rfind("\n", 0, p) + 1, src.find("\n", end)
        before, after = src[bol:p], src[end:eol if eol >= 0 else len(src)]
        lead = bool(_ALPHA_LEAD.search(before))
        if lead and _ALPHA_IS_YOUR.match(after):
            out.append((_ALPHA_PENALTY, (p, end), value))
            continue
        i, j = _mbisect.bisect_right(ends, p) - 1, _mbisect.bisect_left(starts, end)
        gaps = ([p - ends[i]] if i >= 0 and p - ends[i] <= 40 else []) + (
            [starts[j] - end + 80] if j < len(starts) and starts[j] - end <= 40 else [])
        if not gaps:
            continue
        strong = i >= 0 and bool(_STRONG_GAP.fullmatch(src[ends[i]:p]))
        alone = not before.strip() and not after.strip(" .")
        if not (strong or alone or lead and before.strip()):
            continue
        prev, nxt = _PREV_WORD.search(before), _NEXT_WORD.match(after)
        if any(w and w[1].isupper() and not _CODE_WORDS.fullmatch(w[1]) and w[1] not in _ALPHA_JOIN
               for w in (prev, nxt)):
            continue
        context = src[max(0, p - 40):p] + " " + " ".join(w[0] for w in words if -40 <= w.start() - end <= 40)
        if _NOT_CODE_CONTEXT.search(_ALPHA_DOMAIN.sub(" ", context)):
            continue
        out.append((min(gaps) + _ALPHA_PENALTY, (p, end), value))
    return out


# Word codes (Notion: "Your temporary Notion login code is apple-river-stone-lamp"):
# 3 or more lower-case words of 3+ letters joined by hyphens, ≤ 40 characters from
# a code word; not inside a URL, address or longer hyphenated word.
_WORD_CODE = _mre.compile(r"(?<![\w./@-])[a-z]{3,}(?:-[a-z]{3,}){2,}(?![\w@-]|[./]\w)")
_WORD_PENALTY = 90  # behind digit and alphanumeric codes, before letter-only ones


def _word_matches(src, starts, ends):
    """(gap + _WORD_PENALTY, span, value) for the hyphenated word codes in `src`."""
    urls = [u.span() for u in _URLISH.finditer(src)]
    out = []
    for m in _WORD_CODE.finditer(src):
        p, end = m.span()
        if any(a <= p < b for a, b in urls):
            continue
        i, j = _mbisect.bisect_right(ends, p) - 1, _mbisect.bisect_left(starts, end)
        gaps = ([p - ends[i]] if i >= 0 and p - ends[i] <= 40 else []) + (
            [starts[j] - end + 80] if j < len(starts) and starts[j] - end <= 40 else [])
        if gaps:
            out.append((min(gaps) + _WORD_PENALTY, (p, end), m[0]))
    return out


# Codes under a login heading (Notion 2026: "Login to Notion" and the code alone on
# the next line, the code word only in the subject): a line of ≤ 40 characters with
# log in/sign in/anmelden/einloggen/verify/bestätigen, then a line holding only
# letters and digits mixed (_alnum_ok, 4–12).
_LOGIN_HEAD = _mre.compile(
    r"(?im)^(?=[^\n]{0,40}\n)[^\n]*?\b(?:log[ -]?in|sign[ -]?in|anmeld\w*|einlogg\w*|verif\w*|bestätig\w*)\b[^\n]*\n"
    r"(?:[^\S\n]*\n)*[^\S\n]*(?P<value>[A-Za-z0-9]{4,12})[^\S\n]*(?=\n|$)")


def _headed_matches(src):
    """(_ALNUM_PENALTY, span, value) for the codes right under a login heading."""
    return [(_ALNUM_PENALTY, m.span("value"), m["value"]) for m in _LOGIN_HEAD.finditer(src)
            if _alnum_ok(m["value"], True)]


# A voucher word right before a code word ("Promo code", "Gutschein-Code"), or right
# after one ("código de cupón", "codice sconto", "code promo").
_VOUCHER_BEFORE = _mre.compile(r"(?:gutschein|voucher|kupong|coupon|promo|rabatt|discount|gift ?card)[\s-]*$", _mre.I)
_VOUCHER_AFTER = _mre.compile(r"[\s-]*(?:(?:de|di|do|da|del|du)\s+)?(?:cup[oó]n|cupom|promo|descuento|desconto|sconto|"
                              r"r[ée]duction|rabais|regalo|presente|voucher|coupon|gutschein|rabatt)", _mre.I)


def _code_matches(src, alnum=True):
    """(gap to the nearest code word, span, value) for each code in `src` that
    could be a login code: a 4–8 digit number (_CODE) or, with alnum, an
    alphanumeric one (_alnum_matches), one of capital letters (and digits:
    _alpha_matches) or hyphenated words (_word_matches), ≤ 120 characters after
    a code word or ≤ 40 before one (the latter ranked 80 farther); an
    alphanumeric code alone on the line after a code word or a login heading
    (_headed_matches) counts too. Years (19xx, 20xx) don't count, nor codes of a
    voucher's code word ("Promo code", "Gutschein-Code": _mask_vouchers)."""
    words = [w for w in _CODE_WORDS.finditer(src)
             if not _NOT_LOGIN.search(w[0]) and not _VOUCHER_BEFORE.search(src, max(0, w.start() - 16), w.start())
             and not _VOUCHER_AFTER.match(src, w.end())]
    if not words:
        return _headed_matches(src) if alnum else []
    starts, ends = [w.start() for w in words], [w.end() for w in words]  # both ascending

    def gap(start, end):
        gaps = []
        i = _mbisect.bisect_right(ends, start) - 1  # the last word ending before it
        if i >= 0 and start - ends[i] <= 120:
            gaps.append(start - ends[i])
        i = _mbisect.bisect_left(starts, end)  # the first word starting after it
        if i < len(starts) and starts[i] - end <= 40:
            gaps.append(starts[i] - end + 80)
        return min(gaps) if gaps else None

    out = []
    for m in _CODE.finditer(src):
        value = _mre.sub(r"\D", "", m[1])
        if not value.isascii():  # fullwidth (１２３４５６) or other script digits: typed as 0–9
            value = "".join(str(int(c)) for c in value)
        if len(value) == 4 and value[:2] in ("19", "20"):
            continue
        g = gap(m.start(1), m.end(1))
        if g is not None:
            out.append((g, m.span(1), value))
    if alnum:
        out += _alnum_matches(src, words, ends, gap)
        out += _alpha_matches(src, words, starts, ends)
        out += _word_matches(src, starts, ends)
        out += _headed_matches(src)
    return out


def _find_code(subject, text):
    """The login code in a mail, None if there is none: the code nearest to a
    code word (_code_matches; a digit code wins when unsure, but not one right
    after an order/postcode-like word when there is an alphanumeric one); a
    subject wins over the body."""
    for src in (subject, text):
        found = _code_matches(src)
        if any(not v.isdigit() for _, _, v in found):
            found = [(g, (s, e), v) for g, (s, e), v in found
                     if not v.isdigit() or not _NOT_CODE_CONTEXT.search(src[max(0, s - 40):s])]
        if found:
            return min(found)[2]
    return None


_MMASKED = "••••••"


_DIGIT_RUN_START = _mre.compile(r"(?<![0-9])[0-9]")
_SEP = _mre.compile(r"[\s-]{0,3}")
_HTML_SEP = _mre.compile(r"(?:\s|-|&nbsp;|&#160;|<[^<>]{0,300}>){0,12}")


def _value_spans(text, values, html):
    """Spans of every appearance of one of `values` (digit strings), not inside
    a longer number: the digits together, spaced, hyphenated, one per line, or
    (html) one per tag or cell. The longest wins where several fit."""
    sep, longest, n = _HTML_SEP if html else _SEP, max(map(len, values)), len(text)
    for m in _DIGIT_RUN_START.finditer(text):
        pos, digits, end = m.start(), "", None
        while len(digits) < longest and pos < n and "0" <= text[pos] <= "9":
            digits += text[pos]
            pos += 1
            if pos < n and "0" <= text[pos] <= "9":
                continue
            if digits in values:
                end = pos
            nxt = sep.match(text, pos).end()
            if nxt == pos or nxt >= n or not "0" <= text[nxt] <= "9":
                break
            pos = nxt
        if end:
            yield m.start(), end


def _token_spans(text, value, html):
    """Spans of every appearance of the alphanumeric code `value`, not inside a
    longer word, any case: together, spaced, hyphenated or (html) split by tags."""
    sep = _HTML_SEP.pattern if html else _SEP.pattern
    rx = _mre.compile(r"(?<![A-Za-z0-9])" + sep.join(map(_mre.escape, value)) + r"(?![A-Za-z0-9])", _mre.I)
    return [m.span() for m in rx.finditer(text)]


# Passwords sent by mail ("Ihr Initialkennwort lautet: …", "Passwort: …", "Your
# password is …", "Zugangsdaten: …", "PIN: …"): the value after the word (and ≤ 5
# more words) and ":", "lautet"/"lauten" or "is"/"ist", also on the next line: 6+
# characters without spaces, not an e-mail address, URL, date or label
# ("Benutzername:"); a plain word (letters only, no inner capital) only after
# ":"/"lautet" as the last word of its line and not a word like "vergessen"/"reset".
_PASSWORD = _mre.compile(
    r"(?i:\b\w*(?:passwor[dt]|kennwort)(?![^\W\d_])|\b(?:zugangsdaten|pin)\b)"
    r"(?:[^\S\n]+[^\s:<>]{1,40}){0,5}?[^\S\n]*"
    r"(?P<conn>:|(?<!\w)(?i:lautet|lauten|is|ist)\b:?)"
    r"[^\S\n]*(?:\n[^\S\n]*)?[\"'„“”»]?"
    r"(?P<value>[^\s<>\"'„“”«/][^\s<>\"'„“”«]{5,})")
_PW_WORDS = frozenset(
    "vergessen ändern aendern zurücksetzen zuruecksetzen erneuern festlegen vergeben eingeben anfordern "
    "geändert erfolgreich abgelaufen gesperrt falsch ungültig erforderlich aktualisiert hinterlegt "
    "forgot forgotten reset change changed required expired incorrect invalid updated locked".split())
_PW_NOT = _mre.compile(r"[\w.+-]+@[\w.-]+|(?:https?:|www\.).*|[(\[].*|.*:|\d{1,2}\.\d{1,2}\.\d{2,4}|\d{1,2}:\d{2}.*",
                       _mre.I)


def _password_matches(text):
    """(span, value) for each password value in `text` (_PASSWORD); trailing
    sentence punctuation stays outside."""
    out = []
    for m in _PASSWORD.finditer(text):
        value = m["value"].rstrip(".,;)")
        if len(value) < 6 or _PW_NOT.fullmatch(value):
            continue
        if value.isalpha() and not any(c.isupper() for c in value[1:]):
            eol = text.find("\n", m.end())
            if (m["conn"].rstrip(":").lower() in ("is", "ist") or value.casefold() in _PW_WORDS
                    or text[m.end():eol if eol >= 0 else len(text)].strip(" .")):
                continue
        out.append(((m.start("value"), m.start("value") + len(value)), value))
    return out


# Codes under a label in letters, scans and tables (attachment text, pdf_text's
# OCR: TK's "Freischaltcode", a DiGA "Rezeptcode", "Aktivierungscode",
# "Einmal-Kennwort", "Zugangscode", and PIN, TAN or any other …code): 6–24
# capitals and digits, also in groups ("ABCD EFGH IJKL MNOP", "X9-K2-M7-Q4") or
# one by one as OCR spaces them ("K 7 Q X 9 P 2 M"); letters only as one word (not
# an _ALPHA_STOP one) or in groups of 4 or 1. After PIN/TAN/…code only grouped or
# spaced values (one word there is _code_matches' call). Where: right after the
# label (and ":", ".", "=", ";", "|", "lautet"/"ist"/"is"); alone in its cell on the next
# line (≤ 2 blank lines between) when the label ends its line; in the table cell
# under the label (cells 2+ spaces apart, starting ≤ 6 columns off); for the
# strong labels (_LABEL_STRONG) also after ≤ 8 words and ":"/"lautet"/"ist" on the
# same line, or on the next line after a sentence naming them ("Ihr
# Freischaltcode für Meine TK lautet ⏎ ⏎ K7QX 9P2M"). Not in URLs, nor after a
# voucher, QR, BIC, error … label (_NOT_LOGIN, _LABEL_NOT) or, for the other
# labels, an order/booking/reservation/customer … word ≤ 30 characters before
# on the line (_NOT_CODE_CONTEXT: Airbnb's "Reservation code").
_LABEL_WORD = _mre.compile(r"\b(?:[^\W\d_]+-)?(?:\w*code|einmal-?(?:kennwort|passwort)|pin|tan)\b", _mre.I)
_LABEL_STRONG = _mre.compile(r"freischalt|aktivierung|rezept|zugang|einmal", _mre.I)
_LABEL_NOT = _mre.compile(r"swift|bic|blz|bank|sort|farb|colou?r|länder|sprach|language|währung|currency|"
                          r"fehler|error|status|plz|postal|steuer|tax|vat|filial|branch", _mre.I)
_LABEL_GAP = _mre.compile(r"[^\S\n]*(?:[:.=;|][^\S\n]*)?(?:(?:lautet|lauten|ist|is)\b[^\S\n]*:?[^\S\n]*)?", _mre.I)
_LABEL_FILLER = _mre.compile(
    r"(?:[^\S\n]+[^\s:]{1,30}){1,8}?[^\S\n]*(?::|\b(?:lautet|lauten|ist|is)\b:?)[^\S\n]*", _mre.I)
_LABEL_VALUE = _mre.compile(r"[A-Z0-9](?:[ -]?[A-Z0-9]){5,23}(?![\w-]|[.,/:]\w)")
_LABEL_CELL_END = _mre.compile(r"[^\S\n]{2,}|[^\S\n]*(?:\n|$)")
_LABEL_COLUMNS = _mre.compile(r"[^\S\n]{2,}\S")
_CELL = _mre.compile(r"\S+(?: \S+)*")


def _label_value(text, pos, cell, strong):
    """(span, value without separators) of a _LABEL_VALUE at `pos`, None if
    there is none; cell=True: it must end its cell (2+ spaces or the line end);
    strong=False: it must be grouped or spaced."""
    v = _LABEL_VALUE.match(text, pos)
    if not v or cell and not _LABEL_CELL_END.match(text, v.end()):
        return None
    raw, value = v[0], v[0].replace(" ", "").replace("-", "")
    if not strong and raw == value:
        return None
    if value.isalpha():
        groups = _mre.split(r"[ -]", raw)
        if len(groups) == 1 and value in _ALPHA_STOP or len(groups) > 1 and (
                len({len(g) for g in groups}) > 1 or len(groups[0]) not in (1, 4)):
            return None
    elif not value.isdigit() and (_UNIT.fullmatch(value) or _CLOCK.fullmatch(value)):
        return None
    return v.span(), value


def _label_below(text, start, end, strong):
    """_label_value on the line(s) under the label at `start`–`end`: in the
    cell under it when its line has columns, else at the start of the next
    line with text when the label ends its line (strong: any short line)."""
    ls, le = text.rfind("\n", 0, start) + 1, text.find("\n", end)
    if le < 0:
        return None
    rest, pos = text[end:le], le + 1
    for _ in range(3):  # the next line with text, ≤ 2 blank lines between
        nl = text.find("\n", pos)
        nl = len(text) if nl < 0 else nl
        if text[pos:nl].strip():
            break
        if nl >= len(text):
            return None
        pos = nl + 1
    else:
        return None
    if _LABEL_COLUMNS.search(rest.rstrip()):  # a table's header row
        col = start - ls
        head = next((c.start() for c in _CELL.finditer(text, ls, le) if c.start() - ls <= col < c.end() - ls), col)
        for c in _CELL.finditer(text, pos, nl):
            if abs((c.start() - pos) - (head - ls)) <= 6:
                return _label_value(text, c.start(), True, strong)
        return None
    if rest.strip(" \t:.") and not (strong and len(rest) <= 80):
        return None
    return _label_value(text, pos + len(text[pos:nl]) - len(text[pos:nl].lstrip()), True, strong)


def _labelled_matches(text):
    """(span, value without separators) for each code under a label (_LABEL_WORD)."""
    out, urls = [], [u.span() for u in _URLISH.finditer(text)]
    for m in _LABEL_WORD.finditer(text):
        label, start, end = m[0], m.start(), m.end()
        if (_NOT_LOGIN.search(label) or _LABEL_NOT.search(label)
                or _VOUCHER_BEFORE.search(text, max(0, start - 16), start) or any(a <= start < b for a, b in urls)):
            continue
        strong = bool(_LABEL_STRONG.search(label))
        if not strong and _NOT_CODE_CONTEXT.search(text[max(0, start - 30):end].rsplit("\n", 1)[-1]):
            continue
        hit = _label_value(text, _LABEL_GAP.match(text, end).end(), False, strong)
        if not hit and strong:
            f = _LABEL_FILLER.match(text, end)
            hit = f and _label_value(text, f.end(), False, strong)
        hit = hit or _label_below(text, start, end, strong)
        if hit:
            out.append(hit)
    return out


def _mask(text, values=(), html=False):
    """(`text` with its login codes and passwords as ••••••, the set of their
    values): the codes _find_code chooses from (digit ones only in raw html),
    passwords (_password_matches), codes under a label (_labelled_matches; not
    in raw html), and every appearance of them or of `values` (codes found
    elsewhere in the same mail); voucher codes keep their last 4 characters
    (_mask_vouchers)."""
    found = set(values)
    if not text:
        return text, found
    spans = []
    for _, span, value in _code_matches(text, alnum=not html):
        spans.append(span)
        found.add(value)
    for span, value in _password_matches(text):
        spans.append(span)
        found.add(value)
    for span, value in () if html else _labelled_matches(text):
        spans.append(span)
        found.add(value)
    digits = {v for v in found if v.isdigit()}
    if digits:
        spans += _value_spans(text, digits, html)
    for value in found - digits:
        spans += _token_spans(text, value, html)
    if not spans:
        return _mask_vouchers(text), found
    out, pos = [], 0
    for start, end in sorted(spans):
        if start < pos:  # overlaps the previous mask
            pos = max(pos, end)
            continue
        out += [text[pos:start], _MMASKED]
        pos = end
    out.append(text[pos:])
    return _mask_vouchers("".join(out)), found


# Voucher, coupon and gift-card codes ("Gutscheincode: SOMMER-2026", "Kupongkode
# AB12CD34", "promo code is WELCOME15"): only their last 4 characters stay
# ("••••2026"). The code: 4–30 letters, digits and inner hyphens with a digit,
# or all capitals; right after the word, ":", "is"/"ist"/"lautet" (or tags).
_VOUCHER = _mre.compile(
    r"(?i:\b(?:gutschein|voucher|kupong|coupon|promo|rabatt|aktions|geschenkkart|gift ?card|discount)\w*(?:-\w+)*"
    r"(?:[ -](?:code|kode|nummer|number|nr\.?|pin)\b)?)(?!\w)"
    r"(?P<gap>(?:[\s:=>»\"'„“”–-]|&nbsp;|<[^<>]{0,300}>)*(?:(?i:is|ist|lautet)\b(?:[\s:]|&nbsp;)*)?)"
    r"(?P<value>[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){3,29})" + _ALNUM_END + r"(?!\s?EUR\b)")


def _mask_vouchers(text):
    """`text` with voucher codes (_VOUCHER) as ••••<last 4>."""
    def repl(m):
        value = m["value"]
        if not (any(c.isdigit() for c in value) or value.isupper()) or value in _ALPHA_STOP \
                or _CODE_WORDS.fullmatch(value):
            return m[0]
        clean = value.replace("-", "")
        return m[0][:m.start("value") - m.start()] + "••••" + (clean[-4:] if len(clean) > 4 else "")
    return _VOUCHER.sub(repl, text) if text else text


# One-time links (confirm/verify/magic-login URLs): the values of these query
# parameters are masked as ••• in mail texts (`t` only from 16 characters on);
# mail_links still gives the real URL. Also inside a %-encoded wrapped URL and
# behind HTML's &amp;.
_ONE_TIME_PARAM = _mre.compile(
    r"(?P<pre>[?&;]|%3F|%26)(?P<name>[\w.-]*token|[\w.-]*code|otp|[\w.-]*key|signature|sig|magic|verify|t)"
    r"(?P<eq>=|%3D)(?P<value>(?:[^&\s\"'<>#\])%]|%(?!26|3D|3F)[0-9A-F]{2})+)", _mre.I)


def _mask_links(text):
    """`text` with the values of one-time-link query parameters as •••, and
    the tokens of one-time links without such a parameter (_mmask_url)."""
    def repl(m):
        if m["name"].lower() == "t" and len(m["value"]) < 16:
            return m[0]
        return m["pre"] + m["name"] + m["eq"] + "•••"
    if not text:
        return text
    text = _ONE_TIME_PARAM.sub(repl, text)
    return _URL_TOKEN.sub(lambda m: _mmask_url(m[0]), text) if _URL_START.search(text) else text


# One-time links that carry their token in an id or the path (tk.de/link?id=…,
# …/confirm/<token>): on a URL whose path has a segment link…, l, confirm…,
# verif… or activat…, the values of id/lid/uid from 16 characters on, and the
# opaque path segments (32+ characters, letters and digits, no word slug) after
# that segment, show as •••; after a German one (Aktivierung, aktivieren,
# bestaetigen, Bestätigung, freischalten, verifizieren, optin) from 20 characters
# on (tsoto.net/…/Aktivierung/<token>). Each URL wrapped %-encoded inside another counts alone.
_URL_START = _mre.compile(r"https?(?::|%3A)(?://|%2F%2F)", _mre.I)
_URL_TOKEN = _mre.compile(r"https?(?::|%3A)(?://|%2F%2F)[^\s\"'<>]*", _mre.I)
_URL_QUERY = _mre.compile(r"[?#]|%3F|%23", _mre.I)
_PATH_SEP = _mre.compile(r"/|%2F", _mre.I)
_PATH_MARK = _mre.compile(r"(?:link|confirm|verif|activat)[\w-]*|l", _mre.I)
_PATH_MARK_DE = _mre.compile(r"(?:aktivier|best(?:ae|ä|%C3%A4)tig|freischalt|verifizier|opt-?in)[\w%-]*", _mre.I)
_ID_PARAM = _mre.compile(
    r"(?P<pre>[?&;]|%3F|%26)(?P<name>id|lid|uid)(?P<eq>=|%3D)"
    r"(?P<value>(?:[^&\s\"'<>#\])%]|%(?!26|3D|3F)[0-9A-F]{2})+)", _mre.I)
_OPAQUE = _mre.compile(r"(?=[^/]*\d)(?=[^/]*[A-Za-z])[\w.~=+%-]{20,}")
_SLUG = _mre.compile(r"(?:[A-Za-z]+|\d+)(?:[-_.](?:[A-Za-z]+|\d+)){2,}")


def _mmask_url(url):
    """`url` (with the URLs %-encoded inside it) with its one-time-link ids
    and path tokens as ••• (see _URL_START)."""
    starts = [m.start() for m in _URL_START.finditer(url)] + [len(url)]
    out = [url[:starts[0]]]
    for a, b in zip(starts, starts[1:]):
        piece = url[a:b]
        rest = _URL_START.match(piece).end()
        q = _URL_QUERY.search(piece, rest)
        path_end = q.start() if q else len(piece)
        seps = [m.end() for m in _PATH_SEP.finditer(piece, rest, path_end)]
        if not seps:
            out.append(piece)
            continue
        bounds = [(s, e.start() if e else path_end)
                  for s, e in zip(seps, [_PATH_SEP.search(piece, s, path_end) for s in seps])]
        marked = next((k for k, (s, e) in enumerate(bounds)
                       if _PATH_MARK.fullmatch(piece, s, e) or _PATH_MARK_DE.fullmatch(piece, s, e)), None)
        if marked is None:
            out.append(piece)
            continue
        least = 20 if _PATH_MARK_DE.fullmatch(piece, *bounds[marked]) else 32
        parts, pos = [], 0
        for s, e in bounds[marked + 1:]:
            if e - s >= least and _OPAQUE.fullmatch(piece, s, e) and not _SLUG.fullmatch(piece, s, e):
                parts += [piece[pos:s], "•••"]
                pos = e
        query = _ID_PARAM.sub(lambda m: m["pre"] + m["name"] + m["eq"] + "•••" if len(m["value"]) >= 16 else m[0],
                              piece[path_end:])
        out.append("".join(parts) + piece[pos:path_end] + query)
    return "".join(out)


def _mask_msg(msg):
    """Mask the login codes and one-time-link tokens in one message of a tool's
    JSON output (Graph's message, or read-mail-text's {subject, text}), in place."""
    subject = msg.get("subject")
    found = _mask(subject)[1] if isinstance(subject, str) else set()
    for key in ("body", "uniqueBody"):
        body = msg.get(key)
        if isinstance(body, dict) and isinstance(body.get("content"), str):
            if body.get("contentType") == "html":  # find the codes as a reader sees them
                found |= _mask(_body_text({"body": body}, links=False))[1]
                body["content"], found = _mask(body["content"], found, html=True)
            else:
                body["content"], found = _mask(body["content"], found)
            body["content"] = _mask_links(body["content"])
    for key in ("text", "bodyPreview"):
        if isinstance(msg.get(key), str):
            msg[key], found = _mask(msg[key], found)
            msg[key] = _mask_links(msg[key])
    if isinstance(subject, str):
        msg["subject"] = _mask(subject, found)[0]


_MSG_KEYS = ("subject", "body", "uniqueBody", "text", "bodyPreview")
# Strings outlook() leaves as they are: ids (a changed one breaks the next call),
# file bytes, @odata links (the next page's skiptoken).
_KEEP_KEY = _mre.compile(r"@odata\..*|id|\w*Id|contentBytes|changeKey|conversationIndex|webLink", _mre.I)


def _mask_json(data):
    """Mask, in place, every message-like dict (_mask_msg) and every other
    string (codes and one-time links) at any depth of a tool's parsed JSON,
    except _KEEP_KEY values; returns `data` (masked, if a bare string)."""
    if isinstance(data, str):
        return _mask_links(_mask(data)[0])
    if isinstance(data, list):
        for i, v in enumerate(data):
            data[i] = _mask_json(v)
    elif isinstance(data, dict):
        msg = any(k in data for k in _MSG_KEYS)
        if msg:
            _mask_msg(data)
        for k, v in data.items():
            if not (msg and k in _MSG_KEYS) and not _KEEP_KEY.fullmatch(k):
                data[k] = _mask_json(v)
    return data


def _mask_output(text):
    """A tool's output (JSON, else plain text) with its login codes and
    one-time-link tokens masked in every string (_mask_json)."""
    try:
        data = _mjson.loads(text)
    except ValueError:
        return _mask_links(_mask(text)[0])
    before = _mjson.dumps(data)
    data = _mask_json(data)
    if _mjson.dumps(data) == before:
        return text
    if _mre.match(r"\s*[\[{]\s*\n", text):
        return _mjson.dumps(data, ensure_ascii=False, indent=2)
    return _mjson.dumps(data, ensure_ascii=False, separators=(",", ":"))


# IBANs, card numbers, JWTs and Bearer/Basic credentials in mail output: IBAN →
# "DE••…••1234", card → "••••1234" (Kirill's rule: last 4 digits only), always,
# also with reveal_codes. Copied from vault-fill/10-core-cdp-frames.py
# (_IBAN_LEN … _mask_text), renamed with _m: agents %load both files into one
# kernel, and the same names would replace vault-fill's own.
_MIBAN_LEN = dict(
    AD=24, AE=23, AL=28, AT=20, AZ=28, BA=20, BE=16, BG=22, BH=22, BI=27, BR=29, BY=28, CH=21, CR=22, CY=28, CZ=24,
    DE=22, DJ=27, DK=18, DO=28, EE=20, EG=29, ES=24, FI=18, FK=18, FO=18, FR=27, GB=22, GE=22, GI=23, GL=18, GR=27,
    GT=28, HN=28, HR=21, HU=28, IE=22, IL=23, IQ=23, IS=26, IT=27, JO=30, KW=30, KZ=20, LB=28, LC=32, LI=21, LT=20,
    LU=20, LV=21, LY=25, MC=27, MD=24, ME=22, MK=19, MN=20, MR=27, MT=31, MU=30, NI=28, NL=18, NO=15, OM=23, PK=24,
    PL=28, PS=29, PT=25, QA=29, RO=24, RS=22, RU=33, SA=24, SC=31, SD=18, SE=24, SI=19, SK=24, SM=27, SO=23, ST=25,
    SV=28, TL=23, TN=24, UA=29, VA=22, VG=24, XK=20, YE=30)
_MIBAN_START = _mre.compile(r"(?<![A-Za-z0-9])([A-Z]{2})\d{2}")


def _miban_ok(s):
    """`s` (compact, upper case) passes the IBAN mod-97 check."""
    return int("".join(str(int(c, 36)) for c in s[4:] + s[:4])) % 97 == 1


def _mmask_ibans(t):
    """IBANs in `t` (the country's length, single spaces allowed between
    characters, nothing alphanumeric glued on) → "DE••…••1234"."""
    out, last = [], 0
    for m in _MIBAN_START.finditer(t):
        n = _MIBAN_LEN.get(m.group(1))
        if not n or m.start() < last:
            continue
        j, got = m.start(), []
        while len(got) < n and j < len(t):
            c = t[j]
            if c.isascii() and (c.isdigit() or c.isupper()):
                got.append(c)
                j += 1
            elif c in " \u00a0" and got and t[j - 1] not in " \u00a0" and j + 1 < len(t) and t[j + 1].isascii() \
                    and t[j + 1].isalnum():
                j += 1
            else:
                break
        s = "".join(got)
        if len(s) == n and not (j < len(t) and t[j].isalnum()) and _miban_ok(s):
            out += [t[last:m.start()], f"{s[:2]}••…••{s[-4:]}"]
            last = j
    return "".join(out) + t[last:] if out else t


# Card candidates: 14–19 digits plain, or groups of 4 (Amex 4-6-5) joined by
# one space or dash; never inside a longer number, word or dotted/dashed id.
_MCARD_RE = _mre.compile(r"(?<![\w+.,/-])(\d{14,19}|\d{4}([ -])\d{4}\2\d{4}\2\d{4}(?:\2\d{1,3})?|\d{4}([ -])\d{6}\3\d{4,5})"
                         r"(?![\w]|[.,/-]\d)")


def _mcard_ok(d):
    """`d` (digits) is a card number: brand prefix, brand length, Luhn."""
    n, p2, p4 = len(d), int(d[:2]), int(d[:4])
    brand = ((d[0] == "4" and n in (16, 19)) or (51 <= p2 <= 55 and n == 16) or (2221 <= p4 <= 2720 and n == 16)
             or (p2 in (34, 37) and n == 15) or (p2 in (36, 38, 39) or 300 <= int(d[:3]) <= 305) and n in (14, 16)
             or (p4 == 6011 or 644 <= int(d[:3]) <= 649 or p2 in (62, 65)) and 16 <= n <= 19
             or 3528 <= p4 <= 3589 and 16 <= n <= 19 or (p2 == 50 or 56 <= p2 <= 69) and 16 <= n <= 19)
    if not brand:
        return False
    total = 0
    for i, c in enumerate(reversed(d)):
        x = int(c) * (2 if i % 2 else 1)
        total += x - 9 if x > 9 else x
    return total % 10 == 0


def _mcard_sub(m):
    d = _mre.sub(r"\D", "", m.group(1))
    if _mcard_ok(d):
        return f"••••{d[-4:]}"
    if m.group(2) and len(d) > 16 and _mcard_ok(d[:16]):  # 4 groups of 4, then another number
        return f"••••{d[12:16]}" + m.group(1)[19:]
    return m.group(0)


_MJWT_RE = _mre.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_MBEARER_RE = _mre.compile(r"\b(Bearer|Basic)(\s+)([A-Za-z0-9._~+/=-]{16,})", _mre.I)


def _mmask_text(s):
    """`s` with IBANs, card numbers, JWTs and Bearer/Basic credentials masked."""
    if not isinstance(s, str) or len(s) < 14:
        return s
    if _mre.search(r"[A-Z]{2}\d{2}", s):
        s = _mmask_ibans(s)
    if _mre.search(r"\d{4}", s):
        s = _MCARD_RE.sub(_mcard_sub, s)
    if "eyJ" in s:
        s = _MJWT_RE.sub(lambda m: f"•••(masked JWT {len(m.group(0))} chars)", s)
    if "earer" in s or "asic" in s or "EARER" in s or "ASIC" in s:
        s = _MBEARER_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}•••(masked {len(m.group(3))} chars)", s)
    return s


def _mmask_json(data):
    """(`data` with _mmask_text applied, in place, to every string at any
    depth except _KEEP_KEY values, whether anything changed)."""
    if isinstance(data, str):
        new = _mmask_text(data)
        return new, new != data
    changed = False
    if isinstance(data, list):
        for i, v in enumerate(data):
            data[i], c = _mmask_json(v)
            changed |= c
    elif isinstance(data, dict):
        for k, v in data.items():
            if not _KEEP_KEY.fullmatch(k):
                data[k], c = _mmask_json(v)
                changed |= c
    return data, changed


def _mmask_output(text):
    """A tool's output (JSON, else plain text) with _mmask_json applied."""
    try:
        data = _mjson.loads(text)
    except ValueError:
        return _mmask_text(text)
    data, changed = _mmask_json(data)
    if not changed:
        return text
    if _mre.match(r"\s*[\[{]\s*\n", text):
        return _mjson.dumps(data, ensure_ascii=False, indent=2)
    return _mjson.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _pattern_code(pattern, subject, text):
    """The first match of `pattern` (a regex str or compiled) in `subject`, else
    in `text`: its group 1 if it has groups, else the whole match; None if none."""
    rx = _mre.compile(pattern) if isinstance(pattern, str) else pattern
    for src in (subject, text):
        m = rx.search(src)
        if m:
            value = (m[1] if rx.groups else m[0]) or ""
            value = _mre.sub(r"\s+", "", value)
            if value:
                return value
    return None


class MailNoCode(TimeoutError):
    """mail_code: the sender's mail came, but it holds no code (an error or
    notice mail: "No account found"). .subject (masked), .date, .mail_id."""

    def __init__(self, text, msg, subject):
        super().__init__(text)
        self.subject, self.date, self.mail_id = subject, _local(msg["receivedDateTime"]), msg["id"]


class MailNotSent(TimeoutError):
    """fill_mail_code: the page says the code mail wasn't sent ("Failed to send
    code", "zu viele Versuche") or shows a captcha challenge the site waits
    for, so no mail will come. A TimeoutError, as MailNoCode. .page_text (the
    page's message, masked, or None), .captcha (the captcha seen, or None)."""

    def __init__(self, text, page_text, captcha):
        super().__init__(text)
        self.page_text, self.captcha = page_text, captcha


# Set (in fill_mail_code's worker thread) to an Event that ends mail_code's wait
# early: fill_mail_code gave up (MailNotSent, cancelled).
_MCODE_STOP = _mcontextvars.ContextVar("mail_code_stop", default=None)


def mail_code(since, sender=None, subject_hint=None, timeout=120, interval=4, pattern=None, grace=12):
    """Wait for a mail received after `since` (from `sender`, with
    `subject_hint` in its subject; both case-insensitive parts) that holds a
    login code (4–8 digits, 4–12 letters and digits like 9a9aaa9a, 4–8
    capital letters or 4–10 capitals and digits near a code word like
    Booking.com's, or hyphenated words like Notion's); returns it as a
    MailCode, newest mail first. `pattern`: a regex (str or compiled) to use
    instead of that detector: its first match in the subject, else the body
    (group 1 if it has groups; whitespace dropped). Polls the exact
    receivedDateTime filter, which sees new mail before the search index.
    With sender= or subject_hint=, a matching mail without a code ends the
    wait `grace` s later (time for a second mail that has it): MailNoCode (a
    TimeoutError) naming its masked subject, instead of waiting `timeout`."""
    since_dt = _since(since)
    deadline = _mtime.monotonic() + timeout
    seen, codeless = set(), None
    while True:
        msgs = _pages("list-mail-messages", {  # all pages: the mail may be under 25 newer ones
            "filter": f"receivedDateTime ge {_iso(since_dt)}",
            "orderby": ["receivedDateTime desc"],
            "select": _LIST_FIELDS,
        })
        for m in msgs:
            if m["id"] in seen:
                continue
            if sender and sender.casefold() not in _sender(m).casefold():
                continue
            if subject_hint and subject_hint.casefold() not in (m.get("subject") or "").casefold():
                continue
            seen.add(m["id"])
            full = _get(m["id"])
            subject, body = full.get("subject") or "", _body_text(full, links=False)
            value = _pattern_code(pattern, subject, body) if pattern is not None else _find_code(subject, body)
            if value:
                return MailCode(value, full)
            if (sender or subject_hint) and codeless is None:
                codeless = full
                deadline = min(deadline, _mtime.monotonic() + grace)
        if _mtime.monotonic() + interval > deadline:
            if codeless is not None:
                subj = _mmask_text(_mask(codeless.get("subject") or "")[0])
                raise MailNoCode(
                    f"{sender or subject_hint}: {subj!r} ({_local(codeless['receivedDateTime'])}) — no code: "
                    f"the mail since {_local(_iso(since_dt))} has none (an error or notice mail?); "
                    f"read it with mail_text({codeless['id']!r})", codeless, subj)
            raise TimeoutError(
                f"no mail with a code since {_local(_iso(since_dt))}"
                + (f" from {sender!r}" if sender else "")
                + (f" with {subject_hint!r} in the subject" if subject_hint else "")
                + f" within {timeout} s ({len(seen)} matching mails had no code)")
        stop = _MCODE_STOP.get()
        if stop is None:
            _mtime.sleep(interval)
        elif stop.wait(interval):
            raise TimeoutError("mail_code: the wait was given up (fill_mail_code stopped it)")


# fill_mail_code's submit=None: the one visible, enabled button of the code
# field's form (else of the page) that isn't resend/back/cancel/help/another
# login way or a buy/pay/book one; marked with data-vf-code-submit for tab.click.
_CODE_SUBMIT_JS = r"""((sel) => {
  const inp = document.querySelector(sel);
  if (!inp) return {n: -1};
  const scope = inp.closest('form') || document;
  const skip = /resend|erneut|nochmal|noch einmal|anfordern|another|new code|neuen code|send again|zurück|back|cancel|abbrechen|hilfe|help|ändern|change|edit|bearbeiten|andere|other|different|passwor|google|apple|facebook|microsoft|passkey|kaufen|buy|bezahl|pay|bestell|order|buchen|book|abonn|subscribe|schließen|close/i;
  const shown = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const label = e => (e.innerText || e.value || e.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ');
  const found = [...scope.querySelectorAll('button, input[type=submit], input[type=button], [role=button]')]
    .filter(e => shown(e) && !e.disabled && e.getAttribute('aria-disabled') !== 'true' && !skip.test(label(e)));
  if (found.length !== 1) return {n: found.length};
  document.querySelectorAll('[data-vf-code-submit]').forEach(e => e.removeAttribute('data-vf-code-submit'));
  found[0].setAttribute('data-vf-code-submit', '1');
  return {n: 1, label: label(found[0]).slice(0, 40)};
})(%s)"""
_CODE_SUBMIT_WAIT = 3  # s the URL may take to change by itself after typing


async def _code_submit(tab, selector, before):
    """fill_mail_code's submit=None: when the URL stays `before` for
    _CODE_SUBMIT_WAIT s and the form has a single submit-like button
    (_CODE_SUBMIT_JS), click it. Returns what happened, for the result."""
    end = _mtime.monotonic() + _CODE_SUBMIT_WAIT
    while _mtime.monotonic() < end:
        await _masyncio.sleep(0.5)
        if await tab.url() != before:
            return ", the page moved on by itself"
    r = await tab.eval(_CODE_SUBMIT_JS % _mjson.dumps(selector)) or {}
    if r.get("n") == -1:
        return ", the code field is gone (the page took the code)"
    if r.get("n") != 1:
        return (f"; not submitted: {r.get('n', 0)} buttons could be the submit one: click it "
                f"(or pass submit=)")
    try:
        await tab.click("[data-vf-code-submit]")
    except Exception as e:  # noqa: BLE001 — reported, the code is typed already
        return f"; clicking {r.get('label')!r} failed ({type(e).__name__}: {str(e)[:150]}): submit it yourself"
    return (f", then clicked {r.get('label')!r} (the form's only submit button; the URL hadn't changed "
            f"{_CODE_SUBMIT_WAIT} s after typing)")


# fill_mail_code's send check (B-62, DeepSeek's "Failed to send code" under an
# invisible hCaptcha): a short visible message saying the code wasn't sent
# (STRONG; "try again later"/"rate limit" only next to an error word, as help
# texts say it too), also a toast that came and went since the last look (a
# MutationObserver keeps the first one); an open captcha challenge
# (hCaptcha/reCAPTCHA image grid, Arkose); and the captcha frames on the page.
_SEND_FAIL_JS = r"""((reset) => {
  const STRONG = /fail(?:ed)? to send|failed sending|sending failed|could ?n[o'’]t (?:be )?sen[dt]|can(?:no|['’])t send|unable to send|error (?:while )?sending|(?:was|has|have)(?: been)? not (?:been )?sent|wasn['’]t sent|too many (?:requests|attempts|tries|codes)|konnte(?:n)? nicht (?:ge|ver)?(?:sendet|schickt) werden|nicht (?:ge|ver)sendet werden|fehler beim (?:senden|versand|versenden|verschicken)|(?:senden|versand|versenden) (?:ist )?fehlgeschlagen|zu viele (?:versuche|anfragen|anforderungen|codes)/i;
  const WEAK = /try again later|versuche(?:n sie)? es später|später (?:erneut|noch einmal|nochmal)|rate.?limit/i;
  const ERR = /error|fehler|fail|fehlgeschlagen|leider|sorry|went wrong|unable|nicht möglich|something/i;
  const hit = t => STRONG.test(t) || (WEAK.test(t) && ERR.test(t));
  const KEY = '__vfSendFail';
  if (reset) window[KEY] = null;  // a new fill_mail_code: an old failure (before a resend) doesn't count
  const shown = e => { if (!e) return false; const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0'; };
  const message = node => { const e = node.nodeType === 1 ? node : node.parentElement;
    const own = node.textContent || '';
    if (!e || !(STRONG.test(own) || WEAK.test(own))) return null;
    const t = (e.innerText || e.textContent || '').trim().replace(/\s+/g, ' ');
    return t && t.length <= 300 && hit(t) && shown(e) ? t.slice(0, 160) : null; };
  if (!window[KEY + 'Obs'] && document.body) {
    window[KEY + 'Obs'] = new MutationObserver(ms => { if (window[KEY]) return;
      for (const m of ms) for (const n of (m.type === 'characterData' ? [m.target] : m.addedNodes)) {
        const w = n.nodeType === 1 ? document.createTreeWalker(n, NodeFilter.SHOW_TEXT) : null;
        for (let t = w ? w.nextNode() : n; t; t = w ? w.nextNode() : null) {
          const msg = message(t); if (msg) { window[KEY] = msg; return; } } } });
    window[KEY + 'Obs'].observe(document.body, {childList: true, subtree: true, characterData: true});
  }
  let fail = window[KEY] || null;
  if (!fail && document.body) {
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let t = w.nextNode(), n = 0; t && n < 20000; t = w.nextNode(), n++) { fail = message(t); if (fail) break; }
  }
  const kind = src => /hcaptcha/i.test(src) ? 'hCaptcha' : /recaptcha/i.test(src) ? 'reCAPTCHA'
    : /arkoselabs|funcaptcha/i.test(src) ? 'Arkose' : /challenges\.cloudflare/i.test(src) ? 'Turnstile' : null;
  const frames = [...document.querySelectorAll('iframe')].map(f => [f, kind(f.src || '')]).filter(([, k]) => k);
  const open = frames.find(([f]) => /frame=challenge|\/bframe|arkoselabs|funcaptcha/i.test(f.src) && shown(f) && (() => {
    const r = f.getBoundingClientRect(); return r.width > 100 && r.height > 100 && r.bottom > 0 && r.top < innerHeight; })());
  return {fail, challenge: open ? open[1] : null, captcha: frames.length ? frames[0][1] : null};
})(%s)"""
_SEND_CHECK_EVERY = 10  # s between two looks at the page while fill_mail_code waits


async def _send_check(tab, reset=False):
    """Raise MailNotSent when the page says the code wasn't sent or shows an
    open captcha challenge (_SEND_FAIL_JS); a failed look counts as nothing.
    reset=True: forget a message an earlier call's observer kept."""
    try:
        r = await tab.eval(_SEND_FAIL_JS % ("true" if reset else "false"))
    except Exception:  # noqa: BLE001 — the page is navigating or gone: look again later
        return
    if not isinstance(r, dict):
        return
    captcha = r.get("challenge") or r.get("captcha")
    if r.get("fail"):
        text = _mmask_text(_mask(str(r["fail"]))[0])
        raise MailNotSent(
            f"no code mail will come: the page says {text!r}"
            + (f" (a {captcha} captcha is on the page: it may have blocked the send)" if captcha else "")
            + "; fix that (wait and resend once, or solve the captcha), then fill_mail_code with a new since",
            text, captcha)
    if r.get("challenge"):
        raise MailNotSent(
            f"no code mail will come yet: a {r['challenge']} captcha challenge is open on the page; solve it "
            f"(tab.captcha()), then fill_mail_code with a new since", None, r["challenge"])


# fill_mail_code's read-back of split code boxes (B-75, magic.link's six
# #pin-code-input-N boxes in an iframe: the code went into the first one only):
# fields() shows one-character boxes as one "code" row ("2 of 6 boxes filled");
# other boxes (no maxlength=1) are ≥ 4 rows in one frame whose id/name selector
# (else label) differs only by a consecutive index ("#pin-code-input-0" … "-5"),
# named like a code box (_BOX_NAME) and holding no more characters than the code.
_BOX_INDEX = _mre.compile(r"^(.*?)(\d+)(\D*)$")
_BOX_NAME = _mre.compile(r"code|otp|pin|digit|one.?time|passcode|verif|2fa|mfa|token|char|ziffer|stelle|box", _mre.I)
_BOX_KINDS = ("text", "tel", "number", "")
_BOXES_FILLED = _mre.compile(r"(\d+) of (\d+) boxes filled")
_IN_FRAME = _mre.compile(r" in frame ([^\s;,()]+)")


async def _code_boxes(tab, frame, n, code):
    """The one group of `n` split code boxes fields() shows (in `frame`; the
    top page when None): (selector for all of them in order, filled boxes, n,
    whether they hold exactly `code`; None for a one-character-box row, which
    shows only counts). None when there is no such group, several, or no
    fields(). Never returns or prints a box's characters."""
    if not callable(getattr(tab, "fields", None)):
        return None
    try:
        rows = await tab.fields(frame=frame, limit=400, timeout=5) if frame else await tab.fields(limit=400, timeout=5)
    except Exception:  # noqa: BLE001 — the page moved on (the code was taken) or no such frame
        return None
    if not isinstance(rows, list):
        return None
    rows = [r for r in rows if isinstance(r, dict) and not r.get("disabled") and (frame or not r.get("frame"))]
    found, runs = [], {}
    for r in rows:
        if r.get("kind") == "code":
            m = _BOXES_FILLED.fullmatch(str(r.get("value")))
            if m and int(m[2]) == n:
                found.append((r["selector"], int(m[1]), n, None))
        elif r.get("kind") in _BOX_KINDS and isinstance(r.get("value"), str):
            sel = r.get("selector") or ""
            key = sel if not sel.startswith("[data-vf-ref=") else r.get("label") or ""
            m = _BOX_INDEX.match(key)
            if m and _BOX_NAME.search(f"{m[1]} {r.get('label') or ''}"):
                runs.setdefault((r.get("frame"), key is sel, m[1], m[3]), []).append((int(m[2]), r))
    for run in runs.values():
        run.sort(key=lambda x: x[0])
        values = [r["value"] for _, r in run]
        if (len(run) == n >= 4 and all(b[0] - a[0] == 1 for a, b in zip(run, run[1:]))
                and sum(map(len, values)) <= n):
            found.append((", ".join(r["selector"] for _, r in run), sum(1 for v in values if v), n,
                          "".join(values) == code))
    return found[0] if len(found) == 1 else None


def _boxes_ok(box):
    return box[1] == box[2] and box[3] is not False


async def _type_into_boxes(tab, selector, code, frame, how, error):
    """fill_mail_code after tab.type_code(selector) (`how`: its result, None
    if it raised `error`): when the code's frame shows one group of
    len(code) split boxes that isn't filled with the code (some boxes empty,
    or one box holding several characters), type it into each box of that
    group and read them back. Returns (selector used, note for the result);
    raises RuntimeError when the boxes still don't hold it (or `error` when
    there is no such group)."""
    if frame is None and how:
        m = _IN_FRAME.search(how)
        frame = m[1] if m else None
    box = await _code_boxes(tab, frame, len(code), code)
    if box is None or _boxes_ok(box) or (how is not None and box[1] == 0):
        if error is not None:  # no box group to fall back on (0 filled after a good type_code: the code went
            raise error        # into the page's real input, as type_code reports)
        return selector, ""
    where = f" in frame {frame}" if frame else ""
    first = f"{selector} raised ({str(error)[:200]})" if error is not None else (
        f"{selector} filled {box[1]} of {box[2]} split boxes" + (" (not with the code)" if box[1] == box[2] else ""))
    retry_error = None
    try:
        await (tab.type_code(box[0], code, frame=frame) if frame else tab.type_code(box[0], code))
    except RuntimeError as e:  # type_code counts only boxes it changed: a box that kept its character
        if type(e) is not RuntimeError:  # reads as short, so the boxes' own read-back below decides
            raise
        retry_error = e
    again = await _code_boxes(tab, frame, len(code), code)
    if retry_error is not None and (again is None or not _boxes_ok(again)):
        raise RuntimeError(f"typed {len(code)} characters, but {first}{where}; typing into each box of "
                           f"{box[0]} failed too: {str(retry_error)[:300]}. fill_mail_code again with the same "
                           f"since re-reads the same code (no new mail)") from None
    if again is not None and not _boxes_ok(again) and again[1] > 0:
        raise RuntimeError(f"typed {len(code)} characters, but the split boxes {box[0]}{where} show {again[1]} of "
                           f"{again[2]}" + (" (not the code)" if again[1] == again[2] else "")
                           + f" ({first}, and again after typing into each box). fill_mail_code again with the "
                           f"same since re-reads the same code (no new mail)")
    shown = "the page went on" if again is None else f"{again[1]} of {again[2]} filled"
    return box[0], f"; {first}{where}, so it typed the code into each box ({box[0]}): {shown}"


async def fill_mail_code(tab, selector, since, sender=None, subject_hint=None,
                         timeout=120, submit=None, frame=None, pattern=None):
    """Wait for the code mail (mail_code) and type its code into `selector` on
    a ShopTab with tab.type_code: one input gets the whole code, several
    (split one-character boxes, matched in order, also in open shadow roots) a
    character each. frame: passed on to tab.type_code (a frame's host/URL part
    or its <iframe> selector; only when given).
    submit: "Enter" or a selector to click after; None (default): when the
    URL hasn't changed 3 s after typing and the code field's form has a single
    submit-like button, click it (not with frame= nor when type_code found the
    field in a frame); False: never. pattern:
    mail_code's regex override, for codes its detector misses. A code-less
    mail from the sender raises MailNoCode ~12 s later (mail_code). On a
    ShopTab it looks at the page first and every 10 s of the wait: a message
    that the code wasn't sent ("Failed to send code", "zu viele Versuche") or
    an open captcha challenge raises MailNotSent (a TimeoutError) at once.
    Split boxes (B-75): after typing it reads back the code's frame (fields());
    when one group of len(code) split boxes there (one-character boxes, or
    indexed ones like magic.link's #pin-code-input-0…5) isn't filled with the
    code (the selector hit one box, or type_code raised), it types the code
    into each box of that group and reads them back again: still not filled →
    RuntimeError "typed 6 characters, but the split boxes … show 1 of 6"
    (fill_mail_code again with the same since re-reads the same code).
    Returns what it did and what the field shows (type_code's read-back),
    without the code."""
    check = callable(getattr(tab, "captcha", None)) and callable(getattr(tab, "eval", None))
    if check:
        await _send_check(tab, reset=True)
    stop = _mthreading.Event()

    def read_code():  # to_thread runs it in a copy of the context: _REVEAL stays set there only
        _REVEAL.set(True)
        _MCODE_STOP.set(stop)
        return mail_code(since, sender, subject_hint, timeout, pattern=pattern)
    job = _masyncio.ensure_future(_masyncio.to_thread(read_code))
    try:
        while not (await _masyncio.wait({job}, timeout=_SEND_CHECK_EVERY if check else None))[0]:
            await _send_check(tab)
    except BaseException:  # MailNotSent or a cancelled cell: end the mail wait too
        stop.set()
        job.add_done_callback(lambda j: j.cancelled() or j.exception())
        raise
    code = job.result()
    auto = submit is None and frame is None and all(hasattr(tab, a) for a in ("url", "eval", "click"))
    before = await tab.url() if auto else None
    how = error = None
    try:
        how = await (tab.type_code(selector, code.value) if frame is None
                     else tab.type_code(selector, code.value, frame=frame))
    except RuntimeError as e:
        if type(e) is not RuntimeError:  # HardRule and the like: not a typing problem
            raise
        error = e
    typed, boxes = await _type_into_boxes(tab, selector, code.value, frame, how, error)
    done = f", then {submit}" if submit else ""
    if submit == "Enter":
        await tab.press("Enter")
    elif submit:
        await tab.click(submit)
    elif auto and not (isinstance(how, str) and _IN_FRAME.search(how)) and not _IN_FRAME.search(boxes):
        done = await _code_submit(tab, typed, before)
    elif submit is None:  # frame= given or found (the submit JS looks at the top page only), or no ShopTab
        done = "; not submitted (frame= or no ShopTab): click the submit button or pass submit="
    head = f"typed the {len(code)}-{code.kind} code from {code.sender} ({code.date}, {code.subject!r}) "
    own = f"typed the {len(code)}-character code "
    if error is None and isinstance(how, str) and how.startswith(own):  # type_code's what-and-read-back
        return head + how[len(own):] + boxes + done
    return head + f"into {selector}" + boxes + done
