import asyncio as _asyncio
import atexit as _atexit
import base64 as _base64
import bisect as _bisect
import contextlib as _contextlib
import contextvars as _contextvars
import datetime as _datetime
import difflib as _difflib
import fcntl as _fcntl
import fnmatch as _fnmatch
import functools as _functools
import glob as _glob
import html.parser as _htmlparser
import inspect as _inspect
import json as _json
import os as _os
import re as _re
import shutil as _shutil
import subprocess as _subprocess
import sys as _sys
import tempfile as _tempfile
import threading as _threading
import time as _time
import urllib.error as _urlerror
import urllib.parse as _urlparse
import urllib.request as _urlrequest

_RBW = "@rbw@"
_CHROME = "@chrome@"
_XVFB = "@xvfb@"
_PDFTOTEXT = "@pdftotext@"
_PDFTOPPM = "@pdftoppm@"
_PDFIMAGES = _os.path.join(_os.path.dirname(_PDFTOPPM), "pdfimages")
_TESSERACT = "@tesseract@"
_MAGICK = "@magick@"  # ImageMagick 7: captcha() draws its montage with it,
_FONT = "@font@"  # in this font (it finds none by itself)
# What plain HTTP helpers (fetch_text, pdf_text without a tab) send as browser.
_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
# The agents' own Chrome profile: logins made in it survive between sessions,
# so it lives in ~/.local/state, where no cache wipe reaches it.
_AGENT_PROFILE = _os.path.expanduser("~/.local/state/omp-shop-chrome")
# T3 Code starts sessions without XDG_RUNTIME_DIR; rbw would then look for its
# agent, the one holding the unlocked key, somewhere else.
_ENV = {**_os.environ, "XDG_RUNTIME_DIR": f"/run/user/{_os.getuid()}"}
# Fields that log in to a site: typed only into a page of the entry's site.
_LOGIN_FIELDS = {"password", "username", "user", "totp", "code"}
# Fields that aren't secret: never emptied after a submit.
_PLAIN_FIELDS = {"username", "user"}
# The shop browser's X display and DevTools port.
_SHOP_DISPLAY = 94
_SHOP_PORT = 9394
# Per-site notes agents keep; printed the first time a kernel visits the site.
_SITE_NOTES = _os.path.expanduser("~/Documents/agents/memory/sites")
_SITES_SEEN = globals().get("_SITES_SEEN") or set()  # kept across a re-%load
# Screenshots and captcha montages this kernel made: {path: tab id}. close()
# deletes its tab's, shop_close() and the kernel's exit all of them; never
# a wildcard (other agents' files share /tmp).
_SHOTS = globals().get("_SHOTS") or {}


def _shots_drop(tab_id=None):
    for p, tid in list(_SHOTS.items()):
        if tab_id is None or tid == tab_id:
            if _os.path.isdir(p):
                _shutil.rmtree(p, ignore_errors=True)
            else:
                try:
                    _os.unlink(p)
                except FileNotFoundError:
                    pass
            _SHOTS.pop(p, None)


if not globals().get("_SHOTS_ATEXIT"):
    _SHOTS_ATEXIT = _atexit.register(lambda: _shots_drop())


# The plain helpers (fetch_text, http_json, doh, vault_find, …) run at once,
# but agents often write `await` before every call: their str/list/dict
# results can be awaited too, and give themselves.
class _AwaitableStr(str):
    def __await__(self):
        return str(self)
        yield  # noqa: unreachable; makes this a generator


class _AwaitableList(list):
    def __await__(self):
        return list(self)
        yield  # noqa


class _AwaitableDict(dict):
    def __await__(self):
        return dict(self)
        yield  # noqa


# Secrets in what the tools hand back (DF-01: capture() printed a console's
# secret_token; B-01: HUK capture() JSON and page text showed full IBANs).
# _mask_secrets(x) is the one masker: every public ShopTab method's result
# goes through it (_restart_aware), and so do capture/fetch_json/page_json/
# http_json/fetch_text/pdf_text. In any string, at any depth:
#   IBAN (any registry country, its exact length, mod-97 valid, with or
#   without spaces) → "DE••…••1234"; card number (brand prefix, brand length,
#   Luhn; plain or in groups of 4 / Amex 4-6-5) → "••••1234"; a JWT or
#   "Bearer …"/"Basic …" credential → "•••(masked …)"; a login/activation
#   code after its label (Freischaltcode, Rezeptcode, PIN, TAN, Code: …, also
#   on the next line or in a table cell, B-52) → "••••(len N)"; a login code
#   next to "is your … code"/"Your … code is" or another language's code phrase
#   ("验证码为：X", "Ваш код подтверждения: X", B-70), and on a webmail page
#   every code mail.py would mask (B-69) → "••••••".
# In JSON: the string values of credential keys (any case, _ or - ignored,
# also as a suffix: x-api-key, userPassword) become "•••(masked N chars)";
# generic ones (…token, …secret, …auth, …session, …sid, …sig) only when the
# value looks like a credential (≥ 16 chars of [A-Za-z0-9._~+/=:-] with letters
# and digits), a bare …key only with a random-looking value (mixed case and
# digits, or ≥ 24 hex). CSRF/XSRF tokens, page tokens, cursors and ids stay:
# agents need them for the next call. Values of iban / card-number keys keep
# their last 4 even when the checksum fails (test data). A list under a key
# counts as that key's values. Requests the tools send
# themselves still use the real values.
_SECRET_KEYS = ("secrettoken", "accesstoken", "refreshtoken", "idtoken", "apikey", "clientsecret", "password", "passwd",
                "authorization", "bearer", "privatekey")
_GENERIC_SECRET_KEYS = ("token", "secret", "auth", "session", "sessionid", "sid", "sig", "signature")
_KEEP_SECRET_KEYS = ("csrf", "xsrf", "page", "cursor", "next", "continuation")
_CRED_VALUE = _re.compile(r"(?=[^\s]*[A-Za-z])(?=[^\s]*\d)[A-Za-z0-9._~+/=:-]{16,}")
# Under a bare …key (sortKey, a list item's key holding a product id), only a
# random-looking value: mixed case and digits, or ≥ 24 hex digits.
_KEY_VALUE = _re.compile(r"(?=\S*[a-z])(?=\S*[A-Z])(?=\S*\d)[A-Za-z0-9._~+/=:-]{16,}|[0-9a-fA-F]{24,}")
_CARD_KEYS = ("cardnumber", "ccnumber", "creditcardnumber", "cardno", "primaryaccountnumber")


def _key_norm(k):
    return _re.sub(r"[^a-z]", "", str(k).lower())


def _secret_key(k, v=None):
    """Whether key `k` holds a credential; generic keys only with a
    credential-looking value `v`."""
    n = _key_norm(k)
    if not n:
        return False
    if n.endswith(_SECRET_KEYS):
        return True
    if not isinstance(v, str) or any(w in n for w in _KEEP_SECRET_KEYS):
        return False
    if n.endswith(_GENERIC_SECRET_KEYS):
        return bool(_CRED_VALUE.fullmatch(v))
    return n.endswith("key") and bool(_KEY_VALUE.fullmatch(v))


# IBAN registry: country → length.
_IBAN_LEN = dict(
    AD=24, AE=23, AL=28, AT=20, AZ=28, BA=20, BE=16, BG=22, BH=22, BI=27, BR=29, BY=28, CH=21, CR=22, CY=28, CZ=24,
    DE=22, DJ=27, DK=18, DO=28, EE=20, EG=29, ES=24, FI=18, FK=18, FO=18, FR=27, GB=22, GE=22, GI=23, GL=18, GR=27,
    GT=28, HN=28, HR=21, HU=28, IE=22, IL=23, IQ=23, IS=26, IT=27, JO=30, KW=30, KZ=20, LB=28, LC=32, LI=21, LT=20,
    LU=20, LV=21, LY=25, MC=27, MD=24, ME=22, MK=19, MN=20, MR=27, MT=31, MU=30, NI=28, NL=18, NO=15, OM=23, PK=24,
    PL=28, PS=29, PT=25, QA=29, RO=24, RS=22, RU=33, SA=24, SC=31, SD=18, SE=24, SI=19, SK=24, SM=27, SO=23, ST=25,
    SV=28, TL=23, TN=24, UA=29, VA=22, VG=24, XK=20, YE=30)
_IBAN_START = _re.compile(r"(?<![A-Za-z0-9])([A-Z]{2})\d{2}")


def _iban_ok(s):
    """`s` (compact, upper case) passes the IBAN mod-97 check."""
    return int("".join(str(int(c, 36)) for c in s[4:] + s[:4])) % 97 == 1


def _mask_ibans(t):
    """IBANs in `t` (the country's length, single spaces allowed between
    characters, nothing alphanumeric glued on) → "DE••…••1234"."""
    out, last = [], 0
    for m in _IBAN_START.finditer(t):
        n = _IBAN_LEN.get(m.group(1))
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
        if len(s) == n and not (j < len(t) and t[j].isalnum()) and _iban_ok(s):
            out += [t[last:m.start()], f"{s[:2]}••…••{s[-4:]}"]
            last = j
    return "".join(out) + t[last:] if out else t


# Card candidates: 14–19 digits plain, or groups of 4 (Amex 4-6-5) joined by
# one space or dash; never inside a longer number, word or dotted/dashed id.
_CARD_RE = _re.compile(r"(?<![\w+.,/-])(\d{14,19}|\d{4}([ -])\d{4}\2\d{4}\2\d{4}(?:\2\d{1,3})?|\d{4}([ -])\d{6}\3\d{4,5})"
                       r"(?![\w]|[.,/-]\d)")


def _card_ok(d):
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


def _card_sub(m):
    d = _re.sub(r"\D", "", m.group(1))
    if _card_ok(d):
        return f"••••{d[-4:]}"
    if m.group(2) and len(d) > 16 and _card_ok(d[:16]):  # 4 groups of 4, then another number
        return f"••••{d[12:16]}" + m.group(1)[19:]
    return m.group(0)


_JWT_RE = _re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_BEARER_RE = _re.compile(r"\b(Bearer|Basic)(\s+)([A-Za-z0-9._~+/=-]{16,})", _re.I)

# B-30: secrets that sit in prose or in a labelled table cell, not in JSON.
# (a) A value after a password word (Passwort, Initialkennwort, password,
# Zugangsdaten, PIN …) and ":", "=", "lautet", "is"/"ist" (≤ 60 chars of the
# same sentence in between), or in the next cell/line, or after one space →
# "••••". After "lautet" or an initial/one-time/start password word any value
# of ≥ 6 chars counts ("Initialkennwort … lautet: <8 letters>"); otherwise only
# a password-looking one (a digit, a symbol inside, or mixed case after the
# first letter), so "Password is required" and "Passwort vergessen?" stay.
# Code and selectors (backticks, quoted or bracketed text, #id) are no values.
# (b) A ≥ 24-char [A-Za-z0-9_-] token that looks like a key (letters and
# digits; mixed case, hex/UUID, or no dashes) within 300 chars after an
# "API key"/"API token"/"access key"/"client secret"/"secret"/"token" label
# (opensubtitles' consumers table: "API key" header, the key 2 lines below),
# and within 200 chars after each one masked (more rows of the same column)
# → "•••(masked key …abcd)" (last 4). Words inside URLs and CSS selectors
# (after ? & / . # [ =) are not labels: _mask_urls owns URLs.
_PW_LABEL = _re.compile(r"(?<![?&/.#\[=_])(?:(?i:\b(\w*passwor[dt]|\w*kennwort|passcode|zugangsdaten|zugangscode))\b"
                        r"|\b(PIN)\b)")
_PW_STRONG = _re.compile(r"(?i)initial|einmal|start|erst|vorläufig|temporär|temporary|one.?time")
_PW_TAIL = _re.compile(r"(?P<mid>[^\n.?!:;\[\]=<>{}\"()`]{0,60}?)(?P<sep>:|=|\blautet\b:?|\bis\b:?|\bist\b:?)"
                       r"[^\S\n]*(?:\n[^\S\n]*){0,2}(?P<val>[^\s•]\S{5,})")
_PW_TAIL_CELL = _re.compile(r"(?:\t|[^\S\n]*\n)[^\S\n]*(?P<val>[^\s•]\S{5,})(?=\s|$)")
_PW_TAIL_SPACE = _re.compile(r"[^\S\n](?P<val>[^\s•]\S{5,})(?=[\s.,;)]|$)")
_PW_SYMBOL = _re.compile(r"[!@#$%^&*+=_~/\\|§]")


def _pw_value(v, strong):
    """(offset, length) of the secret in `v` (quotes around it and trailing
    punctuation left out) when it looks like a password; any value of ≥ 6
    chars when `strong`; else None. Code and selectors in notes or docs
    (`#ap_password`, ("Login", submit="Enter")) are no values."""
    lead = len(v) - len(v.lstrip("\"'„“»")) if strong else 0
    if v[lead:lead + 1] in ("`", "(", "#", "\"", "'", "„", "“", "»") or "`" in v:
        return None
    v = v[lead:].rstrip(".,;:!?)\"'»“”*")
    if len(v) < 6 or not _re.search(r"[A-Za-z0-9]", v) or "://" in v or v.startswith("www.") \
            or _re.search(r"[\[\]{}<>()]", v):
        return None
    if strong or _re.search(r"\d", v) or _PW_SYMBOL.search(v[1:-1]) \
            or (_re.search(r"[A-Z]", v[1:]) and _re.search(r"[a-z]", v)):
        return lead, len(v)
    return None


def _mask_pw_phrases(t):
    """`t` with values after password words → "••••" (rule (a) above)."""
    out, last = [], 0
    for m in _PW_LABEL.finditer(t):
        if m.start() < last:
            continue
        strong = bool(_PW_STRONG.search(m.group(1) or ""))
        v = None
        tm = _PW_TAIL.match(t, m.end())
        if tm:
            v = _pw_value(tm.group("val"), strong or tm.group("sep").lower().startswith("lautet"))
        if not v:
            tm = _PW_TAIL_CELL.match(t, m.end())
            v = tm and _pw_value(tm.group("val"), strong)
        if not v:
            tm = _PW_TAIL_SPACE.match(t, m.end())
            v = tm and _pw_value(tm.group("val"), False)
        if v:
            s = tm.start("val") + v[0]
            out += [t[last:s], "••••"]
            last = s + v[1]
    return "".join(out) + t[last:] if out else t


_KEY_LABEL = _re.compile(r"(?<![?&/.#\[=])(?i:\b(?:api[ _-]?(?:keys?|tokens?|secrets?|schlüssel)|apikey"
                         r"|access[ _-]?(?:keys?|tokens?)|(?:client|app|consumer)[ _-]?secrets?|secret(?:[ _-]?keys?)?s?"
                         r"|(?:auth|bearer|private)[ _-]?(?:tokens?|keys?)|personal access tokens?|tokens?)\b)")
_KEY_TOKEN = _re.compile(r"(?<![\w/.?&%+@•-])[A-Za-z0-9_-]{24,}(?![\w•-]|[./%+@]\w)")


def _key_like(v):
    """`v` looks like an API key: letters and digits, and mixed case, or
    hex/UUID, or no dashes (slugs like a-b-s24-ultra have them)."""
    if not (_re.search(r"\d", v) and _re.search(r"[A-Za-z]", v)):
        return False
    if _re.search(r"[a-z]", v) and _re.search(r"[A-Z]", v):
        return True
    if _re.fullmatch(r"[0-9a-fA-F-]+", v) and len(v.replace("-", "")) >= 24:
        return True
    return "-" not in v


def _mask_key_labels(t, window=300, more=200):
    """`t` with key-looking tokens after an API key/token/secret label →
    "•••(masked key …abcd)" (rule (b) above)."""
    out, last, end = [], 0, -1
    for m in _KEY_LABEL.finditer(t):
        if m.end() <= end:
            continue
        pos, end = m.end(), m.end() + window
        while km := _KEY_TOKEN.search(t, pos, min(end, len(t))):
            v = km.group(0)
            if _key_like(v) and km.start() >= last:
                out += [t[last:km.start()], f"•••(masked key …{v[-4:]})"]
                last, end = km.end(), km.end() + more
            pos = km.end()
    return "".join(out) + t[last:] if out else t


# B-52: login/activation codes in letters, PDFs and pages (pdf_text's OCR of a
# TK letter printed the Meine-TK Freischaltcode, pdftotext -layout:
# "Freischaltcode.<spaces>XXXX9XXX", and "Rezeptcode: XXX9 XXXX XXXX X9XX").
# A value after a code label → "••••(len N)" (N letters and digits).
#   Labels: strong = Freischalt(ungs)code/-nummer, Aktivierungscode/-schlüssel,
#   Rezeptcode, Einmal-Kennwort/-passwort/-code, Zugangscode,
#   Registrierungscode, Initial-PIN, activation/one-time code …; weak = a
#   login compound (Bestätigungscode, Verifizierungscode, Login-Code, SMS-Code,
#   Sicherheitscode …) or "Code" alone (Ihr Code, security code), PIN, TAN
#   (mTAN, pushTAN). Not other compounds (Getriebecode, Rücksendecode,
#   Postcode), Gutschein/Rabatt/Promo/QR/PNR … codes, a "Code" on a line with
#   a promo (%, spar, Rabatt, €), nor a label heading a compound (TAN-Liste, ?code=).
#   Where: right after the label (after ":", ".", "=", "-", "lautet", "ist",
#   "is", or a "(…)" note; ≤ 60 chars of the same sentence may come before a
#   ":"/"lautet"), across spaces, tabs, "|" cells and ≤ 3 line breaks (OCR);
#   "Code" alone only after ":"/"lautet"/"is" or with the value alone on its
#   line; after a strong label also alone on its line/cell, or in the label's
#   column, within the next 3 lines (a table: label row, value row below),
#   stopping at the next field's label (…nummer, Datum, IBAN, Telefon).
#   Value: 6–24 letters and digits, groups joined by one space or "-" when
#   equally long or short with digits (XXXX XXXX XXXX XXXX, 938 459), with a
#   digit or ≥ 6 capitals (letters-only: all capitals); PIN/TAN also 4–5 digits.
#   Dates, amounts, ids glued to . , / : = + _ @, units (128GB), words with
#   lower-case letters and no digit stay.
_CODE_LABEL = _re.compile(
    r"(?<![\w?&/.#\[=_])(?:"
    r"(?P<strong>(?i:(?:frei[- ]?schalt(?:ungs)?|aktivierungs|rezept|einmal|zugangs|registrierungs|initial"
    r"|activation|one[- ]?time)[- ]?(?:code|kennwort|passwort|schlüssel|key|pin|nummer)))"
    r"|(?P<pre>[^\W\d_]{0,30}[- ]?)(?P<weak>(?i:code))"
    r"|(?P<pin>(?:[a-z]{1,5}|Initial-|Karten-|Online-)?(?:PIN|TAN)))(?!\w|-\w)")
# A weak "…code" label: a login word glued before it (_CODE_LOGIN: Bestätigungs-,
# Verifizierungs-, Sicherheits-, Login-, security …), or "Code" alone, where the
# word before (Ihr, your, diesen …) isn't one of _CODE_NEG (Gutschein, Rabatt,
# Promo, QR, Post, PNR, Artikel …). Other compounds (Getriebecode,
# Rücksendecode, CampaignCode, Postcode) are ids, not secrets.
_CODE_LOGIN = _re.compile(
    r"(?i)(?:bestätigungs|verifizierungs|verifikations|verification|verify|sicherheits|security|log-?in|anmelde"
    r"|sign-?in|authentifizierungs|authentication|auth|access|zugriffs|otp|sms|e-?mail|mail|backup|recovery"
    r"|wiederherstellungs|entsperr|unlock|zwei-?faktor|2-?fa|2-?faktor|geheim|secret|pass|service|identifikations"
    r"|geräte|device|confirm|bestätigen|activate|aktivieren)")
_CODE_NEG = _re.compile(
    r"(?i)(?:gutschein|rabatt|promo|aktions|voucher|coupon|discount|gift|post|country|länder|farb|colou?r|fehler"
    r"|error|status|source|html|response|return|event|artikel|produkt|product|modell?|serien|serial|tarif|referral"
    r"|empfehl|swift|bank|steuer|bestell|order|kunden|customer|region|sprach|language|währung|currency|airport"
    r"|flughafen|buchungs|booking|reservation|confirmation|tracking|sendungs)\w*"
    r"|qr|bar|uni|en|de|geo|typ|type|style|size|kurz|short|embed|ansi|dress|iata|iso|ean|bic|tax|sku|zip|area|dial"
    r"|item|http|exit|pnr|sort")
# A shop's promo code ("Spare 5 % EXTRA … - Code: X"): the label's line before it.
_CODE_PROMO = _re.compile(r"(?i)%|\bspar|rabatt|\bsale\b|\boff\b|discount|gutschein|angebot|\bdeal|aktion|coupon"
                          r"|promo|gratis|versandkostenfrei|free shipping|\bsave\b|€")
_CODE_SEP = _re.compile(r"[^\S\n]*(?:\([^()\n]{0,60}\)[^\S\n]*)?(?P<sep>[:=.–—-]|(?i:lautet|lauten|ist|is|are|sind)\b:?)?"
                        r"(?P<ws>(?:[^\S\n]|\|)*(?:\n(?:[^\S\n]|\|)*){0,3})")
_CODE_MID = _re.compile(r"(?:(?!\.\s)[^\n:?!;|<>{}\[\]\"]){1,60}?(?P<sep>:|(?i:\blautet\b|\bist\b|\bis\b):?)"
                        r"(?P<ws>[^\S\n]*(?:\n[^\S\n]*){0,3})")
# The last word of every label, searched in the lower-cased text (literals: fast
# on long pages); _code_labels() matches _CODE_LABEL only in the 45 chars before.
_CODE_END = _re.compile(r"(?:code|kennwort|passwort|schlüssel|key|pin|nummer|tan)(?!\w|-\w)")
_CODE_TOK = _re.compile(r"[A-Za-z0-9]+")
_CODE_CELL = _re.compile(r"(?:^|\t|\|| {2,})[^\S\n|]*(?=[A-Za-z0-9])", _re.M)
_CODE_OTHER_FIELD = _re.compile(r"(?i)nummer|\bnr\b|iban|telefon|\btel\b|\bfax\b|datum|aktenzeichen|e-mail")
_CODE_STOP = frozenset(
    "ABGELAUFEN ABSENDEN ACHTUNG ANZEIGEN BESTÄTIGEN BITTE CONFIRM CONTINUE COPY DATUM EINGEBEN ENTER ERFORDERLICH "
    "ERNEUT EXPIRED HINWEIS HINWEISE INVALID KOPIEREN NUMMER PLEASE REQUIRED RESEND SENDEN SUBMIT VERIFY WEITER "
    "WICHTIG WICHTIGE".split())


def _code_at(t, p, short=False):
    """(end, n) when a code-looking value (rule above) starts at t[p]: its end
    and its number of letters and digits; else None. `short`: 4–5 digits
    count too (PIN/TAN)."""
    m = _CODE_TOK.match(t, p)
    if not m or (p and (t[p - 1].isalnum() or t[p - 1] in "•_@=+/")):
        return None
    first = m.group(0)
    toks, end = [first], m.end()
    if not _re.search(r"[a-z]", first):
        while end + 1 < len(t) and t[end] in " -":
            g = _CODE_TOK.match(t, end + 1)
            if not g or _re.search(r"[a-z]", g.group(0)) or sum(map(len, toks)) + len(g.group(0)) > 24:
                break
            g = g.group(0)
            if (len(g) != len(first) or not _re.search(r"\d", g) and len(g) > 6) \
                    and not (len(first) <= 6 and len(g) <= 6 and _re.search(r"\d", g) and _re.search(r"\d", first)):
                break
            toks.append(g)
            end += 1 + len(g)
    if end < len(t) and (t[end].isalnum() or t[end] in "•_@=+" or t[end] in ".,/:'" and end + 1 < len(t)
                         and t[end + 1].isalnum()):
        return None
    if _re.match(r"[^\S\n]?(?:€|%|EUR\b|Euro\b)", t[end:end + 6]):
        return None
    v, raw = "".join(toks), t[p:end]
    n, digits = len(v), sum(c.isdigit() for c in v)
    if n > 24 or n < (4 if short and digits == n else 6) or _re.fullmatch(r"(?:19|20)\d\d-\d\d-\d\d", raw):
        return None
    if digits == 0:
        if v != v.upper() or sum(c.isupper() for c in v) < 6 or any(x in _CODE_STOP for x in toks):
            return None
    elif digits < n and (_re.fullmatch(r"\d+[A-Za-z]{1,3}", v) or _re.fullmatch(r"\d{1,2}h\d{2}", v, _re.I)):
        return None
    return end, n


def _code_window(t, m):
    """Spans of the values in the 3 lines after a strong code label `m`: alone
    on its line/cell, or a cell in the label's column (more rows of the same
    column follow)."""
    ls = t.rfind("\n", max(0, m.start() - 300), m.start()) + 1
    col0, col1 = m.start() - ls, m.end() - ls
    pos, rows, out, hit_col = t.find("\n", m.end()), 0, [], None
    while pos != -1 and rows < 3:
        a = pos + 1
        pos = t.find("\n", a)
        line = t[a:pos if pos != -1 else len(t)]
        if not line.strip():
            continue
        rows += 1
        if _CODE_LABEL.search(line) or _CODE_OTHER_FIELD.search(line):
            break  # the next field's label: what follows is its value
        for c in _CODE_CELL.finditer(line):
            q = c.end()
            r = _code_at(t, a + q)
            if not r:
                continue
            rest = t[r[0]:pos if pos != -1 else len(t)]
            alone = not line[:q].strip() and not rest.strip()
            # in the label's column: text() joins table cells with one space
            col_ok = (col0 - 4 <= q <= col1 + 4) if hit_col is None else abs(q - hit_col) <= 2
            if alone and hit_col is None or col_ok and (not rest or rest[0].isspace()):
                out.append((a + q, r[0], r[1]))
                hit_col, rows = q, 0
                break
    return out


def _code_labels(t, low):
    """The _CODE_LABEL matches in `t` (`low` = t.lower()), left to right,
    without overlaps."""
    last = 0
    src, rx = (low, _CODE_END) if len(low) == len(t) else (t, _re.compile(_CODE_END.pattern, _re.I))
    for a in rx.finditer(src):
        for m in _CODE_LABEL.finditer(t, max(last, a.start() - 45), a.end()):
            if m.end() == a.end():
                last = m.end()
                yield m
                break


def _mask_code_labels(t, low=None):
    """`t` with login/activation codes after a code label → "••••(len N)"
    (rule B-52 above)."""
    out, last = [], 0
    for m in _code_labels(t, t.lower() if low is None else low):
        if m.start() < last:
            continue
        bare = False
        if m.group("weak"):
            pre = m.group("pre") or ""
            word = pre.rstrip("- ")
            if not word or pre.endswith(" "):  # "Code" alone, or after a word of its own (Ihr, your, QR)
                line0 = max(t.rfind("\n", max(0, m.start() - 80), m.start()) + 1, m.start() - 80)
                if word and _CODE_NEG.fullmatch(word) or _CODE_PROMO.search(t, line0, m.start()):
                    continue
                bare = True
            elif not _CODE_LOGIN.fullmatch(word):
                continue
        short, spans = bool(m.group("pin")), []
        for rx in (_CODE_SEP, _CODE_MID):
            g = rx.match(t, m.end())
            if not g or g.end() == m.end():
                continue
            sep, gap = g.group("sep") or "", g.group(0)
            r = _code_at(t, g.end(), short and (sep[:1] in (":", "=") or "\n" in gap))
            if not r:
                continue
            eol = t.find("\n", r[0])
            alone = "\n" in gap and not t[r[0]:eol if eol != -1 else len(t)].strip()
            if sep or alone or not bare and m.group("weak") is None \
                    or not bare and not _re.fullmatch(r"[^\S\n]", gap):
                spans = [(g.end(), r[0], r[1])]
                break
        if not spans and m.group("strong"):
            spans = _code_window(t, m)
        for s, e, n in spans:
            if s >= last:
                out += [t[last:s], f"••••(len {n})"]
                last = e
    return "".join(out) + t[last:] if out else t


# B-69: login codes in page text (R6-DIGI-0336: an Outlook-on-the-web inbox
# list printed "<code> is your Polymarket login code" through text(); the code
# sits before its code word, which B-52 above doesn't look at). mail.py's code
# detector, copied with an _lc/_LC prefix (mail.py and vault-fill load into one
# kernel; keep the copies in step with mail.py's _code_matches & co.), masks
# them as mail.py's output does: "••••••", and every other appearance of the
# same value in that string too. Two strengths:
#   mail UI (_MASK_MAIL, set by _mask_scope(tab) for a tab on a webmail host:
#   _mail_ui_url; or a text that shows the inbox and 2 more mail folders on
#   lines of their own: _mail_ui_text): mail.py's whole rule (_lc_matches): a
#   4–8 digit number, letters and digits mixed, capitals, hyphenated words,
#   ≤ 120 chars after a code word or ≤ 40 before one, or alone under a login
#   heading;
#   anywhere else only a code next to a possessive code phrase (_lc_adjacent):
#   "<code> is your … code" / "<code> ist Ihr …code", "Your … code is <code>",
#   "Dein Anmeldecode: <code>", "Ihr Einmalpasswort lautet <code>" (≤ 40 chars
#   between "your" and the code word; not a promo/voucher/post/zip/order/
#   booking/ticket/reference … code: _LC_NEG, _CODE_NEG). Order numbers,
#   prices, postcodes, phone numbers and ids elsewhere on a page stay.
_LC_MASKED = "••••••"
# Words that name a login code (Code, Verifizierungscode, PIN, TAN, Einmalpasswort …),
# and the codes that aren't one. Other languages too (B-70, DeepSeek's "验证码为："):
# código/codice/kod/kode/koodi/kód and password words (es/pt/it/pl/sv/da/no/tr/fi/
# cs/hu/nl/fr/vi), Russian/Greek/Arabic/Hebrew/Hindi/Thai, and Chinese, Japanese
# and Korean ones, which have no word boundary (a katakana/hangul word before コード/
# 코드 belongs to it, so that クーポンコード/쿠폰 코드 meet _LC_NOT_LOGIN).
_LC_WORDS = _re.compile(
    r"\b\w*(?:code|kennwort|passwort|password|passcode)\w*|\b(?:pin|tan|otp|einmal\w*|one[- ]time)\b"
    r"|\b\w*(?:c[óo]dig[oa]s?|codic[ei]|kod(?:e[nm]?|u\w*|y)?|koodi\w*|kód\w*|senha|contraseña|wachtwoord|"
    r"lösenord|hasło|şifre\w*|heslo|jelszó\w*)\b|\b(?:mot de passe|mã (?:xác (?:minh|nhận)|otp))\b"
    r"|\b(?:код\w*|парол\w*|одноразов\w*|κωδικ\w*)|\b\w*(?:رمز|كود)\w*|קוד|कोड|รหัส"
    r"|验证码|驗證碼|校验码|校驗碼|动态码|動態碼|确认码|確認碼|认证码|認證碼|安全码|安全碼|登录码|登錄碼|登入码|登入碼"
    r"|验证代码|驗證代碼|动态密码|動態密碼|一次性密码|一次性密碼|临时密码|臨時密碼"
    r"|認証コード|確認コード|認証番号|確認番号|暗証番号|ワンタイム(?:パスワード|コード)?|パスコード"
    r"|[\u30a1-\u30fa\u30fc]{0,12}コード|인증\s?번호|(?:[\uac00-\ud7a3]{1,8}\s?)?코드|일회용\s?비밀번호|비밀번호", _re.I)
_LC_NOT_LOGIN = _re.compile(
    r"gutschein|rabatt|promo|voucher|coupon|discount|aktions|transakt|transaction|bar|qr|"
    r"post|zip|area|country|referral|empfehl|html|source|"
    r"kupong|cup[oó]n|cupom|descuento|desconto|sconto|купон|скидк|промо|クーポン|プロモ|招待|紹介|"
    r"쿠폰|할인|추천|초대|优惠|優惠|兑换|兌換|邀请|邀請", _re.I)
# Scripts without spaces between words (kana, CJK, hangul): a code may touch them
# ("验证码为123456", "123456입니다"), so the boundaries below don't count them as word
# characters.
_LC_CJK = "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff\uff66-\uff9f"
# 123456, 938 459 / 1234-5678, or one digit per line or cell (ELSTER); not a price
# (€, EUR, %, 円/元/¥).
_LC_DIGITS = _re.compile(r"(?<!(?![" + _LC_CJK + r"])[\w€$£¥￥])(?<!\d[.,/:-])(?<!\d\s)"
                         r"(\d(?:\s{1,3}\d){3,7}(?!\s{0,3}\d)|\d{3,4}[ -]\d{3,4}(?![ -]?\d)|\d{4,8})"
                         r"(?!(?![" + _LC_CJK + r"])[\w%€]|[.,:/-]\d|\s?(?:€|EUR\b|%|円|元))")
# Alphanumeric codes (myRyanair: 9a9aaa9a; K7Q2PX9M, K7Q2-PX9M): 4–12 letters
# and digits with at least one of each, or two equal halves of 3–6 joined by
# "-" or a space (then each half has a digit); not part of a URL, address, host
# name, #colour, price or longer word (kana/CJK/hangul next to it are no word: _LC_CJK).
_LC_ALNUM_START = _re.compile(r"(?<!(?![" + _LC_CJK + r"])[\w#@&=/\\.:+?%$€£-])[A-Za-z0-9]")
_LC_ALNUM_END = r"(?!(?![" + _LC_CJK + r"])[\w@]|[.:/=+\\-]\w|\s?(?:€|%))"
_LC_ALNUM_TWO = _re.compile(r"([A-Za-z0-9]{3,6})([ -])([A-Za-z0-9]{3,6})" + _LC_ALNUM_END)
_LC_ALNUM_ONE = _re.compile(r"[A-Za-z0-9]{4,12}" + _LC_ALNUM_END)
# Order, invoice, customer, booking, model … numbers are no login code.
_LC_NOT_CONTEXT = _re.compile(
    r"bestell|rechnung|kunden|auftrag|order|invoice|customer|buchung|booking|reserv|vertrag|"
    r"contract|sendung|tracking|shipment|paket|parcel|referenz|reference|ticket|modell|model|"
    r"artikel|article|serien|serial|produkt|product|iban|aktenzeichen|vorgang|postleitzahl|"
    r"pedido|factura|fattura|commande|facture|ordine|заказ|订单|訂單|运单|運單|快递|快遞|注文|予約|주문|예약", _re.I)
_LC_URLISH = _re.compile(r"(?:https?://|www\.)[^\s<>\"']+|(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_LC_UNIT = _re.compile(r"\d+[A-Za-z]{1,3}")  # 10px, 15min, 128GB, 12th
_LC_CLOCK = _re.compile(r"\d{1,2}h\d{2}", _re.I)
# What may stand between a code word and a code right after it: "Code: X", "code is X",
# "验证码为：X", "コードは X", "인증번호는 X".
_LC_STRONG_GAP = _re.compile(r"[\s:：=>»\"'„“”「」【】–-]*(?:(?:(?:is|ist|lautet|est|es|è|é)\b|[为為是は는은])[\s:：]*)?",
                             _re.I)
_LC_ALNUM_PENALTY = 60  # ranks an alphanumeric code 60 characters farther: digits win when unsure


def _lc_alnum_ok(value, strong):
    """Whether `value` (separator removed) may be a login code: letters and
    digits mixed (≥ 2 letter/digit changes, or 1 right after a code word but
    no unit like 10px); no clock time (15h30) or _LC_NOT_LOGIN word."""
    if not (any(c.isdigit() for c in value) and any(c.isalpha() for c in value)):
        return False
    if _LC_NOT_LOGIN.search(value) or _LC_CLOCK.fullmatch(value):
        return False
    shifts = sum(a.isdigit() != b.isdigit() for a, b in zip(value, value[1:]))
    return shifts >= 2 or (strong and not _LC_UNIT.fullmatch(value))


def _lc_alnum_matches(src, words, ends, gap):
    """(gap + _LC_ALNUM_PENALTY, span, value) for the alphanumeric codes in `src`."""
    urls = [u.span() for u in _LC_URLISH.finditer(src)]
    out, done = [], 0
    for s in _LC_ALNUM_START.finditer(src):
        p = s.start()
        if p < done or any(a <= p < b for a, b in urls):
            continue
        for rx in (_LC_ALNUM_TWO, _LC_ALNUM_ONE):
            m = rx.match(src, p)
            if not m:
                continue
            if rx is _LC_ALNUM_TWO:
                a, sep, b = m.groups()
                if len(a) != len(b) or (sep == " " and not (_re.search(r"\d", a) and _re.search(r"\d", b))):
                    continue
                value = a + b
            else:
                value = m[0]
            if not _re.search(r"\d", value):
                continue
            end, i = m.end(), _bisect.bisect_right(ends, p) - 1
            own_line = strong = False
            if i >= 0:
                between = src[ends[i]:p].split("\n")
                eol = src.find("\n", end)
                own_line = (len(between) >= 2 and len(between[0]) <= 80 and not "".join(between[1:]).strip()
                            and not src[end:eol if eol >= 0 else len(src)].strip(" ."))
                strong = own_line or bool(_LC_STRONG_GAP.fullmatch(src[ends[i]:p]))
            if not _lc_alnum_ok(value, strong):
                continue
            g = gap(p, end)
            if g is None and own_line:
                g = p - ends[i]
            if g is None:
                break
            context = src[max(0, p - 40):p] + " " + " ".join(w[0] for w in words if -40 <= w.start() - end <= 40)
            if _LC_NOT_CONTEXT.search(context):
                break
            out.append((g + _LC_ALNUM_PENALTY, (p, end), value))
            done = end
            break
    return out


# Letter-only codes (Booking.com: 6 capital letters, "Booking.com – ABCDEF is your
# verification code") and capital letters with digits (Booking.com too: "Booking.com -
# ABCDE9 is your verification code", one letter/digit change, which _lc_alnum_ok wants
# right after a code word): 4–8 capital letters, or 4–10 capitals and digits with a
# capital, ≤ 40 characters from a code word, set off from the text (right after the
# code word, after a colon or dash, or alone on its line), or at a line's start or
# after a dash/colon before "is your … code".
# Not a code word, _LC_NOT_LOGIN or _LC_ALPHA_STOP word, unit (128GB) or clock time, not
# next to another capitalised word (headings), nor after an order/booking-like word
# (_LC_NOT_CONTEXT; domains like Booking.com ignored).
_LC_ALPHA = _re.compile(r"(?<!(?![" + _LC_CJK + r"])[\w#@&=/\\.:+?%$€£-])(?=[0-9]*[A-Z])[A-Z0-9]{4,10}" + _LC_ALNUM_END)
_LC_ALPHA_IS_YOUR = _re.compile(r"[ \t]+(?:is|IS|ist|IST)[ \t]+(?i:your|dein|ihr)\b[^\n]{0,40}?(?i:code)")
_LC_ALPHA_STOP = frozenset(
    "ABOUT ACCOUNT ACHTUNG ACTION ALLE AKTIV CLICK CONFIRM CONTACT COPY DATE DEIN DEINE EINFACH EMAIL ENTER FAQS "
    "FREE GILT GMBH HELP HERE HIER HILFE HTTP HTTPS IBAN IHRE IMPORTANT IMPRESSUM INFO JETZT KLICK KONTAKT KONTO "
    "LINK LOGIN LOGO MAIL MEHR NAME NEVER NEWS NICHT NIEMALS NOTE ONLINE OPEN PASS PRIVACY SALE SECURE SECURITY "
    "SEPA SERVICE SHOP SIGN SUPPORT SWIFT TEAM TERMS TEXT THAT THIS TIME TOTP USER VALID VERIFY WARNING WICHTIG "
    "WITH YOUR".split())
_LC_ALPHA_JOIN = frozenset({"IS", "IST", "LAUTET"})  # "YOUR CODE IS ABCDEF"
_LC_ALPHA_PENALTY = 120  # ranked behind digit and alphanumeric codes
_LC_ALPHA_DOMAIN = _re.compile(r"\b[\w-]+\.(?:com|de|at|ch|net|org|eu|io|co|uk|fr|es|it|nl)\b", _re.I)
_LC_PREV_WORD = _re.compile(r"([^\W\d_]+)[^\w\n]*$")
_LC_NEXT_WORD = _re.compile(r"[^\w\n]*([^\W\d_]+)")
_LC_ALPHA_LEAD = _re.compile(r"(?:^|[:–—|-])[ \t]*$")  # line start, "Code:", "Booking.com – "


def _lc_alpha_matches(src, words, starts, ends):
    """(gap + _LC_ALPHA_PENALTY, span, value) for the letter-only codes in `src`."""
    urls = [u.span() for u in _LC_URLISH.finditer(src)]
    out = []
    for m in _LC_ALPHA.finditer(src):
        p, end, value = m.start(), m.end(), m[0]
        if value.isalpha() and len(value) > 8 or not value.isalpha() and (_LC_UNIT.fullmatch(value)
                                                                          or _LC_CLOCK.fullmatch(value)):
            continue
        if (value in _LC_ALPHA_STOP or _LC_WORDS.fullmatch(value) or _LC_NOT_LOGIN.search(value)
                or any(a <= p < b for a, b in urls)):
            continue
        bol, eol = src.rfind("\n", 0, p) + 1, src.find("\n", end)
        before, after = src[bol:p], src[end:eol if eol >= 0 else len(src)]
        lead = bool(_LC_ALPHA_LEAD.search(before))
        if lead and _LC_ALPHA_IS_YOUR.match(after):
            out.append((_LC_ALPHA_PENALTY, (p, end), value))
            continue
        i, j = _bisect.bisect_right(ends, p) - 1, _bisect.bisect_left(starts, end)
        gaps = ([p - ends[i]] if i >= 0 and p - ends[i] <= 40 else []) + (
            [starts[j] - end + 80] if j < len(starts) and starts[j] - end <= 40 else [])
        if not gaps:
            continue
        strong = i >= 0 and bool(_LC_STRONG_GAP.fullmatch(src[ends[i]:p]))
        alone = not before.strip() and not after.strip(" .")
        if not (strong or alone or lead and before.strip()):
            continue
        prev, nxt = _LC_PREV_WORD.search(before), _LC_NEXT_WORD.match(after)
        if any(w and w[1].isupper() and not _LC_WORDS.fullmatch(w[1]) and w[1] not in _LC_ALPHA_JOIN
               for w in (prev, nxt)):
            continue
        context = src[max(0, p - 40):p] + " " + " ".join(w[0] for w in words if -40 <= w.start() - end <= 40)
        if _LC_NOT_CONTEXT.search(_LC_ALPHA_DOMAIN.sub(" ", context)):
            continue
        out.append((min(gaps) + _LC_ALPHA_PENALTY, (p, end), value))
    return out


# Word codes (Notion: "Your temporary Notion login code is apple-river-stone-lamp"):
# 3 or more lower-case words of 3+ letters joined by hyphens, ≤ 40 characters from
# a code word; not inside a URL, address, longer hyphenated word or a #selector
# (vault-fill only: site notes cite `#cvf-…` ids).
_LC_WORD_CODE = _re.compile(r"(?<![\w./@#-])[a-z]{3,}(?:-[a-z]{3,}){2,}(?![\w@-]|[./]\w)")
_LC_WORD_PENALTY = 90  # behind digit and alphanumeric codes, before letter-only ones


def _lc_word_matches(src, starts, ends):
    """(gap + _LC_WORD_PENALTY, span, value) for the hyphenated word codes in `src`."""
    urls = [u.span() for u in _LC_URLISH.finditer(src)]
    out = []
    for m in _LC_WORD_CODE.finditer(src):
        p, end = m.span()
        if any(a <= p < b for a, b in urls):
            continue
        i, j = _bisect.bisect_right(ends, p) - 1, _bisect.bisect_left(starts, end)
        gaps = ([p - ends[i]] if i >= 0 and p - ends[i] <= 40 else []) + (
            [starts[j] - end + 80] if j < len(starts) and starts[j] - end <= 40 else [])
        if gaps:
            out.append((min(gaps) + _LC_WORD_PENALTY, (p, end), m[0]))
    return out


# Codes under a login heading (Notion 2026: "Login to Notion" and the code alone on
# the next line, the code word only in the subject): a line of ≤ 40 characters with
# log in/sign in/anmelden/einloggen/verify/bestätigen, then a line holding only
# letters and digits mixed (_lc_alnum_ok, 4–12).
_LC_LOGIN_HEAD = _re.compile(
    r"(?im)^(?=[^\n]{0,40}\n)[^\n]*?\b(?:log[ -]?in|sign[ -]?in|anmeld\w*|einlogg\w*|verif\w*|bestätig\w*)\b[^\n]*\n"
    r"(?:[^\S\n]*\n)*[^\S\n]*(?P<value>[A-Za-z0-9]{4,12})[^\S\n]*(?=\n|$)")


def _lc_headed_matches(src):
    """(_LC_ALNUM_PENALTY, span, value) for the codes right under a login heading."""
    return [(_LC_ALNUM_PENALTY, m.span("value"), m["value"]) for m in _LC_LOGIN_HEAD.finditer(src)
            if _lc_alnum_ok(m["value"], True)]


# A voucher word right before a code word ("Promo code", "Gutschein-Code"), or right
# after one ("código de cupón", "codice sconto", "code promo").
_LC_VOUCHER_BEFORE = _re.compile(r"(?:gutschein|voucher|kupong|coupon|promo|rabatt|discount|gift ?card)[\s-]*$", _re.I)
_LC_VOUCHER_AFTER = _re.compile(r"[\s-]*(?:(?:de|di|do|da|del|du)\s+)?(?:cup[oó]n|cupom|promo|descuento|desconto|sconto|"
                                r"r[ée]duction|rabais|regalo|presente|voucher|coupon|gutschein|rabatt)", _re.I)


def _lc_matches(src):
    """(gap to the nearest code word, span, value) for each code in `src` that
    could be a login code (mail.py's _code_matches with alnum=True): a 4–8
    digit number (_LC_DIGITS), an alphanumeric one (_lc_alnum_matches), one of
    capital letters (and digits: _lc_alpha_matches) or hyphenated words
    (_lc_word_matches), ≤ 120 characters after a code word or ≤ 40 before one
    (the latter ranked 80 farther); an alphanumeric code alone on the line
    after a code word or a login heading (_lc_headed_matches) counts too.
    Years (19xx, 20xx) don't count, nor codes of a voucher's code word ("Promo
    code", "Gutschein-Code")."""
    words = [w for w in _LC_WORDS.finditer(src)
             if not _LC_NOT_LOGIN.search(w[0]) and not _LC_VOUCHER_BEFORE.search(src, max(0, w.start() - 16), w.start())
             and not _LC_VOUCHER_AFTER.match(src, w.end())]
    if not words:
        return _lc_headed_matches(src)
    starts, ends = [w.start() for w in words], [w.end() for w in words]  # both ascending

    def gap(start, end):
        gaps = []
        i = _bisect.bisect_right(ends, start) - 1  # the last word ending before it
        if i >= 0 and start - ends[i] <= 120:
            gaps.append(start - ends[i])
        i = _bisect.bisect_left(starts, end)  # the first word starting after it
        if i < len(starts) and starts[i] - end <= 40:
            gaps.append(starts[i] - end + 80)
        return min(gaps) if gaps else None

    out = []
    for m in _LC_DIGITS.finditer(src):
        value = _re.sub(r"\D", "", m[1])
        if not value.isascii():  # fullwidth (１２３４５６) or other script digits: typed as 0–9
            value = "".join(str(int(c)) for c in value)
        if len(value) == 4 and value[:2] in ("19", "20"):
            continue
        g = gap(m.start(1), m.end(1))
        if g is not None:
            out.append((g, m.span(1), value))
    out += _lc_alnum_matches(src, words, ends, gap)
    out += _lc_alpha_matches(src, words, starts, ends)
    out += _lc_word_matches(src, starts, ends)
    out += _lc_headed_matches(src)
    return out


_LC_DIGIT_RUN_START = _re.compile(r"(?<![0-9])[0-9]")
_LC_SEP = _re.compile(r"[\s-]{0,3}")


def _lc_value_spans(text, values):
    """Spans of every appearance of one of `values` (digit strings), not inside
    a longer number: the digits together, spaced, hyphenated or one per line.
    The longest wins where several fit."""
    longest, n = max(map(len, values)), len(text)
    for m in _LC_DIGIT_RUN_START.finditer(text):
        pos, digits, end = m.start(), "", None
        while len(digits) < longest and pos < n and "0" <= text[pos] <= "9":
            digits += text[pos]
            pos += 1
            if pos < n and "0" <= text[pos] <= "9":
                continue
            if digits in values:
                end = pos
            nxt = _LC_SEP.match(text, pos).end()
            if nxt == pos or nxt >= n or not "0" <= text[nxt] <= "9":
                break
            pos = nxt
        if end:
            yield m.start(), end


def _lc_token_spans(text, value):
    """Spans of every appearance of the alphanumeric code `value`, not inside a
    longer word, any case: together, spaced or hyphenated."""
    rx = _re.compile(r"(?<![A-Za-z0-9])" + _LC_SEP.pattern.join(map(_re.escape, value)) + r"(?![A-Za-z0-9])", _re.I)
    return [m.span() for m in rx.finditer(text)]


# The possessive code phrases of _lc_adjacent (vault-fill only).
_LC_POSS = r"(?:your|dein(?:e[nmrs]?)?|ihr(?:e[nmrs]?)?|this|these|diese[nmrs]?|the following|folgende[nmrs]?|votre|tu)"
_LC_CW = r"(?:[\w-]*code|pin|tan|passcode|otp|einmal[\w-]*|[\w-]*kennwort|[\w-]*passwort|[\w-]*password)"
_LC_IS_YOUR = _re.compile(r"[ \t]+(?:is|ist|est|es)[ \t]+" + _LC_POSS + r"\b(?P<mid>[^\n]{0,40}?)\b(?P<w>" + _LC_CW
                          + r")\b", _re.I)
_LC_YOUR_IS = _re.compile(r"\b" + _LC_POSS + r"\b(?P<mid>[^\n.:]{0,40}?)\b(?P<w>" + _LC_CW
                          + r")\b[ \t]*(?:(?:is|ist|lautet|est|es)\b[ \t]*:?|:)[ \t]*(?:\n[ \t]*)?", _re.I)
_LC_NEG = _re.compile(_LC_NOT_LOGIN.pattern + "|" + _LC_NOT_CONTEXT.pattern, _re.I)
_LC_TOKEN_START = _re.compile(r"(?<![^\s:–—|(\"'„“»])\S")
# Any code word at all (lower-cased text): the cheap test before the rules
# (literals only: fast; pin/tan/otp inside a word are dropped by _lc_adjacent).
_LC_QUICK = _re.compile(r"code|pin|tan|passw|kennw|otp|einmal|one.time")
# B-70, anywhere (vault-fill only; the webmail rule above takes all of mail.py's
# words): a code right after another language's login-code phrase
# (_LC_I18N_WORDS: zh/ja/ko ones, es/pt/it/fr/pl/ru/uk/tr/cs/nl ones with a login
# qualifier — "código de verificación", "код подтверждения" — and "your code":
# "tu código", "ваш код"; never a bare código/kod/код, a 確認番号 booking
# number or a coupon code), on its line or the next, behind ":", "：", "=", "-",
# is/es/est/è/é/это/jest/to, 是/为/は/는 or an opening bracket ("验证码为：X",
# "認証コードは「X」です", "Tu código de verificación es X", "Ваш код: X"); or
# right before one, joined by such a word or "your" ("X是您的验证码", "X es tu
# código de verificación"; whitespace alone isn't enough: "DEEPSEEK 验证码").
_LC_I18N_POSS = r"tu|su|seu|teu|il\s+tuo|il\s+suo|il\s+vostro|twój|pański|ваш|твой"
_LC_I18N_WORDS = (
    r"(?:\b(?:" + _LC_I18N_POSS + r")\s+)?(?:"
    r"验证码|驗證碼|校验码|校驗碼|动态码|動態碼|确认码|確認碼|认证码|認證碼|安全码|安全碼|登录码|登錄碼|登入码|登入碼"
    r"|验证代码|驗證代碼|动态密码|動態密碼|一次性密码|一次性密碼|临时密码|臨時密碼"
    r"|認証コード|確認コード|検証コード|ログインコード|セキュリティコード|認証番号|暗証番号|パスコード|ワンタイム[ァ-ヿ]*"
    r"|인증\s?번호|인증\s?코드|확인\s?코드|보안\s?코드|일회용\s?비밀번호|임시\s?비밀번호"
    r"|\bc[oó]digo\s+(?:de\s+)?(?:verificaci[oó]n|verifica[cç][aã]o|seguridad|seguran[cç]a|acceso|acesso|confirmaci[oó]n"
    r"|confirma[cç][aã]o|activaci[oó]n|ativa[cç][aã]o|inicio\s+de\s+sesi[oó]n|login|un\s+solo\s+uso|uso\s+[uú]nico)\b"
    r"|\bcodice\s+(?:di\s+)?(?:verifica|sicurezza|accesso|conferma|attivazione|monouso)\b"
    r"|\bcode\s+(?:de\s+|d['’]\s*)?(?:v[ée]rification|s[ée]curit[ée]|confirmation|connexion|validation|acc[eè]s|activation)\b"
    r"|\bcode\s+[àa]\s+usage\s+unique\b"
    r"|\bkod\s+(?:weryfikacyjny|bezpiecze[nń]stwa|dost[eę]pu|potwierdzaj[aą]cy|potwierdzenia|aktywacyjny|logowania|jednorazowy)\b"
    r"|\bjednorazowy\s+kod\b"
    r"|\bкод\s+(?:подтверждения|верификации|проверки|для\s+входа|входа|доступа|безопасности|активации|підтвердження|доступу)\b"
    r"|\b(?:проверочный|одноразовый)\s+(?:код|пароль)\b"
    r"|\b(?:doğrulama|güvenlik|onay)\s+kodu\b|\btek\s+kullanımlık\s+(?:şifre|kod)\w*"
    r"|\b(?:ověřovací|bezpečnostní|přihlašovací)\s+kód\b"
    r"|\b(?:verificatie|beveiligings|inlog|bevestigings)code\b"
    r")|\b(?:" + _LC_I18N_POSS + r")\s+(?:c[oó]digo|codice|kod|код)\b")
_LC_I18N_PHRASE = _re.compile(_LC_I18N_WORDS, _re.I)
_LC_I18N_QUICK = _re.compile(r"码|碼|コード|ワンタイム|番号|번호|코드|密码|密碼|digo|codice|code|kod|код|kód")
_LC_I18N_AFTER = _re.compile(
    r"[ \t]*(?:[(（][^)）\n]{0,20}[)）])?(?:[ \t:：=—–-]|(?:is|ist|est|es|è|é|это|jest|wynosi|to|je|lautet)\b|是|为|為|は|는|은|이)*"
    r"(?:\n[ \t]*)?[\[【「『(（\"'“«]?[ \t]*", _re.I)
_LC_I18N_LINK = _re.compile(
    r"(?:[ \t]*(?:[:：—–-]|(?:is|es|est|è|é|это|jest|to|je|ist)\b|是|为|為|は|가|이|作为|作為|您的|你的|您|你"
    r"|(?:" + _LC_I18N_POSS + r"|votre|ton|uw|váš)\b))+[ \t]*", _re.I)


def _lc_i18n(t, low=None):
    """(span, value) of each code next to another language's code phrase
    (rule B-70 above). Only looked for around _LC_I18N_QUICK's literals."""
    low = t.lower() if low is None else low
    quick = _LC_I18N_QUICK.finditer(low) if len(low) == len(t) else _re.finditer(_LC_I18N_QUICK.pattern, t, _re.I)
    windows = []
    for q in quick:
        a, b = max(0, q.start() - 60), min(len(t), q.end() + 60)
        if windows and a <= windows[-1][1]:
            windows[-1][1] = b
        else:
            windows.append([a, b])
    out, seen = [], set()
    for a, b in windows:
        for m in _LC_I18N_PHRASE.finditer(t, a, b):
            if m.span() in seen:
                continue
            seen.add(m.span())
            g = _LC_I18N_AFTER.match(t, m.end())
            r = _lc_value_at(t, g.end())
            if r:
                out.append(((g.end(), r[0]), r[1]))
            bol = max(t.rfind("\n", 0, m.start()) + 1, m.start() - 40)
            for k in range(bol, m.start()):
                if not ("0" <= t[k] <= "9" or "A" <= t[k] <= "Z" or "a" <= t[k] <= "z") \
                        or k and t[k - 1].isascii() and t[k - 1].isalnum():
                    continue
                r = _lc_value_at(t, k)
                if r and _LC_I18N_LINK.fullmatch(t, r[0], m.start()):
                    out.append(((k, r[0]), r[1]))
                    break
    return out


def _lc_value_at(t, p):
    """(end, value) of a login-code-looking value starting at t[p] (mail.py's
    value rules, as right after a code word), else None."""
    m = _LC_DIGITS.match(t, p)
    if m and m.start(1) == p:
        v = _re.sub(r"\D", "", m[1])
        if not v.isascii():  # fullwidth (１２３４５６): as _lc_matches
            v = "".join(str(int(c)) for c in v)
        if not (len(v) == 4 and v[:2] in ("19", "20")):
            return m.end(1), v
    if _LC_ALNUM_START.match(t, p):
        for rx in (_LC_ALNUM_TWO, _LC_ALNUM_ONE):
            m = rx.match(t, p)
            if not m:
                continue
            if rx is _LC_ALNUM_TWO:
                a, sep, b = m.groups()
                if len(a) != len(b) or (sep == " " and not (_re.search(r"\d", a) and _re.search(r"\d", b))):
                    continue
                v = a + b
            else:
                v = m[0]
            if _lc_alnum_ok(v, True):
                return m.end(), v
    m = _LC_ALPHA.match(t, p)
    if m and not (m[0].isalpha() and len(m[0]) > 8 or m[0] in _LC_ALPHA_STOP or _LC_WORDS.fullmatch(m[0])
                  or _LC_NOT_LOGIN.search(m[0]) or _LC_UNIT.fullmatch(m[0]) or _LC_CLOCK.fullmatch(m[0])):
        return m.end(), m[0]
    m = _LC_WORD_CODE.match(t, p)
    return (m.end(), m[0]) if m else None


def _lc_phrase_ok(m):
    """Whether the code phrase `m` names a login code: no promo, voucher,
    post/zip, order, booking, ticket, reference … word between "your" and the
    code word or glued before it (Booking.com-like domains ignored)."""
    w = m["w"]
    words = _re.findall(r"[^\W\d_]+", m["mid"]) + [_re.sub(r"(?i)(?:code|pin|tan|passcode|otp|kennwort|passwort|"
                                                           r"password)$", "", w).strip("-")]
    return not (_LC_NEG.search(_LC_ALPHA_DOMAIN.sub(" ", m["mid"] + " " + w))
                or any(x and _CODE_NEG.fullmatch(x) for x in words))


def _lc_adjacent(t, low=None):
    """(span, value) of each code right next to a possessive code phrase:
    before "is your … code" (also its last two words: "938 459 is your code")
    or after "your … code is/:", and next to another language's code phrase
    (_lc_i18n). Only looked for around a code word (_LC_QUICK,
    _LC_I18N_QUICK in `low` = t.lower()): fast on long pages."""
    low = t.lower() if low is None else low
    quick = _LC_QUICK.finditer(low) if len(low) == len(t) else _re.finditer(_LC_QUICK.pattern, t, _re.I)
    windows = []
    for q in quick:
        s, e = q.span()
        if e - s == 3 and (s and t[s - 1].isalpha() or e < len(t) and t[e].isalpha()):
            continue  # "shopping", "standard", "hotpot"
        a, b = max(0, s - 100), min(len(t), e + 40)
        if windows and a <= windows[-1][1]:
            windows[-1][1] = b
        else:
            windows.append([a, b])
    out = []
    for a, b in windows:
        for m in _LC_IS_YOUR.finditer(t, a, b):
            if not _lc_phrase_ok(m):
                continue
            bol = max(t.rfind("\n", 0, m.start()) + 1, m.start() - 40)
            for k in [k.start() for k in _LC_TOKEN_START.finditer(t, bol, m.start())][-2:]:
                r = _lc_value_at(t, k)
                if r and r[0] == m.start():
                    out.append(((k, r[0]), r[1]))
                    break
        for m in _LC_YOUR_IS.finditer(t, a, b):
            r = _lc_phrase_ok(m) and _lc_value_at(t, m.end())
            if r:
                out.append(((m.end(), r[0]), r[1]))
    return out + _lc_i18n(t, low)


# A mail client's folder list (Outlook, Gmail, GMX, web.de, Roundcube …): the
# inbox and ≥ 2 more of these kinds, each on a line of its own (an unread
# count may follow).
_LC_FOLDERS = _re.compile(
    r"(?im)^[ \t]*(?:(posteingang|inbox|eingang)|(gesendete elemente|gesendete objekte|gesendet|sent|sent items|sent mail)"
    r"|(entwürfe|drafts)|(junk-e-mail|junk e-mail|junk email|junk|spam|spamverdacht)"
    r"|(gelöschte elemente|gelöscht|papierkorb|deleted items|trash|bin)|(archiv|archive|all mail|alle e-mails)"
    r"|(postausgang|outbox))(?:[ \t]*\(?\d+\)?)?[ \t]*$")
# Webmail hosts (and mail.*/webmail.*/email.*/owa.*/roundcube.* ones); GMX,
# web.de, iCloud, Yahoo, AOL, t-online … under a /mail path.
_MAIL_UI_HOST = _re.compile(
    r"(?:^|\.)(?:outlook\.(?:live|office|office365)\.com|outlook\.cloud\.microsoft|mail\.google\.com|app\.tuta\.com"
    r"|app\.fastmail\.com|office\.mailbox\.org|(?:mail|webmail|email|owa|roundcube)\.[\w-]+(?:\.[\w-]+)+)$")
_MAIL_UI_PATH = _re.compile(r"(?:^|\.)(?:gmx\.(?:net|de|at|ch|com)|web\.de|icloud\.com|yahoo\.com|aol\.com|t-online\.de"
                            r"|freenet\.de|ionos\.de|1und1\.de|posteo\.de|proton\.me)$")


def _mail_ui_url(url):
    """Whether `url` is a webmail client's page."""
    try:
        u = _urlparse.urlsplit(url or "")
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return bool(_MAIL_UI_HOST.search(host)
                or _MAIL_UI_PATH.search(host) and _re.match(r"/(?:\w+/)?(?:web)?mail\b", u.path, _re.I))


def _mail_ui_text(t):
    """Whether text `t` shows a mail client's folder list (_LC_FOLDERS)."""
    seen = set()
    for m in _LC_FOLDERS.finditer(t):
        seen.add(m.lastindex)
        if 1 in seen and len(seen) >= 3:
            return True
    return False


# True while the result of a call on a tab that shows a webmail is masked
# (_mask_scope): then every string gets the mail rule.
_MASK_MAIL = _contextvars.ContextVar("vf_mask_mail", default=False)


async def _tab_mail_ui(tab):
    """Whether `tab`'s page is a webmail (_mail_ui_url of the main frame's URL,
    which the DevTools client follows; asked once if it hasn't seen one)."""
    cdp = getattr(tab, "_cdp", None)
    if cdp is None:
        return False
    url = getattr(cdp, "page_url", None)
    if url is None:
        try:
            frame = (await cdp.send("Page.getFrameTree", timeout=3))["frameTree"]["frame"]
        except Exception:
            return False
        url = frame.get("url")
        if cdp.page_url is None:
            cdp.page_url, cdp._main_frame = url, frame.get("id")
    return _mail_ui_url(url)


@_contextlib.asynccontextmanager
async def _mask_scope(tab):
    """`async with _mask_scope(tab): …mask…`: inside it, _mask_text uses the
    mail rule when `tab` shows a webmail (_MASK_MAIL)."""
    tok = _MASK_MAIL.set(await _tab_mail_ui(tab))
    try:
        yield
    finally:
        _MASK_MAIL.reset(tok)


def _mask_login_codes(t, mail=False):
    """`t` with its login codes as "••••••" (rule B-69 above): mail=True the
    mail rule (_lc_matches), else only codes next to a code phrase
    (_lc_adjacent); every other appearance of a code found goes too."""
    hits = [(s, v) for _, s, v in _lc_matches(t)] if mail else _lc_adjacent(t)
    if not hits:
        return t
    found = {v for _, v in hits}
    spans = [s for s, _ in hits]
    digits = {v for v in found if v.isdigit()}
    if digits:
        spans += _lc_value_spans(t, digits)
    for v in found - digits:
        spans += _lc_token_spans(t, v)
    out, pos = [], 0
    for start, end in sorted(spans):
        if start < pos:  # overlaps the previous mask
            pos = max(pos, end)
            continue
        out += [t[pos:start], _LC_MASKED]
        pos = end
    out.append(t[pos:])
    return "".join(out)


def _mask_text(s):
    """`s` with IBANs, card numbers, JWTs, Bearer/Basic credentials,
    login/activation codes after a code label, login codes next to a code
    phrase (all of mail.py's on a webmail page), password-phrase values and
    labelled API keys masked; the same object when nothing matched."""
    if not isinstance(s, str) or len(s) < 9:
        return s
    t = s
    if _re.search(r"[A-Z]{2}\d{2}", t):
        t = _mask_ibans(t)
    if _re.search(r"\d{4}", t):
        t = _CARD_RE.sub(_card_sub, t)
    if "eyJ" in t:
        t = _JWT_RE.sub(lambda m: f"•••(masked JWT {len(m.group(0))} chars)", t)
    if "earer" in t or "asic" in t or "EARER" in t or "ASIC" in t:
        t = _BEARER_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}•••(masked {len(m.group(3))} chars)", t)
    low = t.lower()
    mail = _MASK_MAIL.get() or len(t) >= 200 and ("eingang" in low or "inbox" in low) and _mail_ui_text(t)
    if mail:
        t = _mask_login_codes(t, True)  # first: every appearance of a code gets the mail form
        low = t.lower()
    if "passw" in low or "kennw" in low or "zugang" in low or "passcode" in low or "PIN" in t:
        t = _mask_pw_phrases(t)  # first: its "••••" form stays for what it already masked
        low = t.lower()
    if _CODE_END.search(low):
        t = _mask_code_labels(t, low)
    if ("key" in low or "token" in low or "secret" in low or "schlüssel" in low) and _re.search(r"[A-Za-z0-9_-]{24}", t):
        t = _mask_key_labels(t)
    if not mail and (_LC_QUICK.search(low) or _LC_I18N_QUICK.search(low)):
        t = _mask_login_codes(t)
    if t == s:
        return s
    try:
        return type(s)(t)  # _AwaitableStr stays awaitable
    except Exception:
        return t


def _mask_value(k, v):
    """Value `v` under JSON key `k`, masked (a list's items as if each stood
    under `k`: page_json(keys=…) returns {key: [values]})."""
    n = _key_norm(k)
    if type(v) is list and v and n:
        new = [_mask_value(k, x) for x in v]
        return v if all(a is b for a, b in zip(new, v)) else new
    if n.endswith("iban") and isinstance(v, str) and len(_re.sub(r"[^A-Za-z0-9]", "", v)) >= 8 and "•" not in v:
        c = _re.sub(r"[^A-Za-z0-9]", "", v)
        return f"{c[:2].upper() if c[:2].isalpha() else ''}••…••{c[-4:]}"
    if (n.endswith(_CARD_KEYS) or n == "pan") and isinstance(v, (str, int)) and not isinstance(v, bool):
        d = _re.sub(r"\D", "", str(v))
        if len(d) >= 12:
            return f"••••{d[-4:]}"
    if isinstance(v, str) and v and _secret_key(k, v):
        return f"•••(masked {len(v)} chars)"
    return _mask_secrets(v)


def _mask_secrets(obj):
    """`obj` (a string or parsed JSON) with credentials, IBANs and card
    numbers masked, at any depth. Returns `obj` itself when nothing changed;
    str/list/dict subclasses keep their type when something did."""
    if isinstance(obj, str):
        return _mask_text(obj)
    if isinstance(obj, dict):
        new = {k: _mask_value(k, v) for k, v in obj.items()}
        if all(new[k] is v for k, v in obj.items()):
            return obj
    elif isinstance(obj, (list, tuple)):
        new = [_mask_secrets(v) for v in obj]
        if all(a is b for a, b in zip(new, obj)):
            return obj
    else:
        return obj
    if type(obj) in (dict, list):
        return new
    try:
        return type(obj)(new)
    except Exception:
        return new


_SECRET_PAIR = _re.compile(r'("([^"\\]{1,60})"\s*:\s*")((?:[^"\\]|\\.)*)(")')


def _mask_json_text(s):
    """The same for JSON text in an error message (a snippet of a body)."""
    s = _SECRET_PAIR.sub(lambda m: m.group(1) + f"•••(masked {len(m.group(3))} chars)" + m.group(4)
                         if m.group(3) and _secret_key(m.group(2), m.group(3)) else m.group(0), s)
    return _mask_text(s)


def _both(fn):
    """`fn(…)` and `await fn(…)` give the same result."""
    kinds = {str: _AwaitableStr, list: _AwaitableList, dict: _AwaitableDict}

    @_functools.wraps(fn)
    def call(*args, **kwargs):
        r = fn(*args, **kwargs)
        return kinds[type(r)](r) if type(r) in kinds else r
    return call


# Page-side expressions both fillers use. Fields filled from the vault are
# remembered in window[Symbol.for("vault-fill")].
# Empties every field filled from the vault on this page.
_CLEAR_EXPR = r"""(() => {
  const filled = window[Symbol.for("vault-fill")] ?? new Set();
  for (const el of filled) {
    const proto = el instanceof HTMLInputElement ? HTMLInputElement.prototype
      : el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : null;
    if (!proto) continue;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, "");
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }
  filled.clear();
})()"""
# Vault-filled fields still holding a value.
_LEFT_EXPR = r"""[...(window[Symbol.for("vault-fill")] ?? [])]
  .filter((el) => el.isConnected && el.value).length"""
# input and change on the field just filled: some frameworks take a value only
# on change, and HUK's two-step login submitted an empty e-mail field without.
_NOTIFY_EXPR = r"""(() => {
  const el = window[Symbol.for("vault-fill-el")];
  for (const t of ["input", "change"]) el?.dispatchEvent(new Event(t, { bubbles: true }));
})()"""
# Records, from the submit on, the text of error and alert elements that show
# up, also those gone again before the filler looks (Cloudflare's).
_ALERT_SEL = (
    "[role=alert],[role=alertdialog],[role=status],[aria-live=assertive],[aria-live=polite],"
    "[class*=error i],[class*=alert i],[class*=invalid i],[class*=warn i],[class*=danger i],"
    "[class*=notification i],[class*=toast i],[class*=feedback i],[id*=error i]"
)
_ALERT_WATCH = r"""(() => {
  const SEL = __SEL__, k = Symbol.for("vf-alerts");
  window[k]?.obs.disconnect();
  // innerText never holds what a field contains.
  const text = (e) => (e.innerText || "").replace(/\s+/g, " ").trim().slice(0, 200);
  const showing = (e) => e.checkVisibility({ opacityProperty: true, visibilityProperty: true });
  const st = window[k] = { seen: new Set(),
    before: new Set([...document.querySelectorAll(SEL)].filter(showing).map(text)) };
  const note = (n) => {
    const e = n.nodeType === 1 ? n : n.parentElement;
    if (!e) return;
    const hit = e.closest(SEL);
    for (const a of hit ? [hit] : [...e.querySelectorAll(SEL)].slice(0, 5))
      if (showing(a) && text(a)) st.seen.add(text(a));
  };
  st.obs = new MutationObserver((ms) => {
    for (const m of ms) m.type === "childList" ? m.addedNodes.forEach(note) : note(m.target);
  });
  st.obs.observe(document.body, { subtree: true, childList: true, characterData: true,
    attributes: true, attributeFilter: ["class", "style", "hidden", "aria-hidden", "aria-invalid"] });
})()""".replace("__SEL__", _json.dumps(_ALERT_SEL))
# What _ALERT_WATCH saw change after the submit (also the same error again),
# plus what shows now and didn't before the submit.
_ALERT_READ = r"""(() => {
  const SEL = __SEL__, st = window[Symbol.for("vf-alerts")];
  if (!st) return [];
  st.obs.disconnect();
  const now = [...document.querySelectorAll(SEL)]
    .filter((e) => e.checkVisibility({ opacityProperty: true, visibilityProperty: true }))
    .map((e) => (e.innerText || "").replace(/\s+/g, " ").trim().slice(0, 200));
  const out = [...new Set([...st.seen, ...now.filter((t) => !st.before.has(t))])].filter(Boolean);
  return out.filter((t) => !out.some((o) => o !== t && o.includes(t))).slice(0, 5);
})()""".replace("__SEL__", _json.dumps(_ALERT_SEL))

_FILL_JS = r"""
const ARGS = __ARGS__, SITES = __SITES__, FIELD = __FIELD__;
const SELECTOR = __SELECTOR__, SUBMIT = __SUBMIT__;
const SECRET = !__PLAIN__.includes(FIELD.toLowerCase());
const env = { ...process.env, XDG_RUNTIME_DIR: __RUNTIME_DIR__ };
async function rbw(args) {
  const p = Bun.spawn(args, { stdin: "ignore", stdout: "pipe", stderr: "pipe", env });
  const [out, err, code] = await Promise.all([
    new Response(p.stdout).text(), new Response(p.stderr).text(), p.exited]);
  if (code !== 0) throw new Error("rbw: " + (err.trim() || "exit " + code));
  return out.replace(/\r?\n$/, "");
}
// SITES: the registrable domains of the entry's websites; null: no check.
if (SITES) {
  const here = new URL(page.url()).hostname.toLowerCase();
  if (!SITES.some((s) => here === s || here.endsWith("." + s)))
    throw new Error(`this page (${here}) is not the entry's site (${SITES.join(", ")}); pass any_site=True if it is`);
}
// After a navigation there is nothing left to empty, and nothing left over.
const clear = () => page.evaluate(__CLEAR__).catch(() => {});
const left = () => page.evaluate(__LEFT__).catch(() => 0);
const said = async () => {
  const s = await page.evaluate(__ALERTS__).catch(() => []);
  return s?.length ? "; the page showed: " + s.join(" | ") : "";
};
// Remembers the field (for clear() only a secret one), focuses it and selects
// what it holds, so the value replaces it.
const prepare = (el, secret) => {
  if (secret) (window[Symbol.for("vault-fill")] ??= new Set()).add(el);
  window[Symbol.for("vault-fill-el")] = el;
  el.focus();
  try { el.select?.(); } catch {}
};
let value = await rbw(ARGS);
if (!value) throw new Error(`the entry has no ${FIELD}`);
try {
  const el = SELECTOR === null
    ? await page.evaluateHandle(() => document.activeElement)
    : await page.waitForSelector(SELECTOR, { visible: true, timeout: 15000 });
  await el.evaluate(prepare, SECRET);
  // One trusted input event for the whole value, as Playwright's fill does,
  // instead of a round of the page's key handlers per character.
  await page.keyboard.sendCharacter(value);
  value = null;
  await page.evaluate(__NOTIFY__).catch(() => {});
  if (SUBMIT === null) return `typed the ${FIELD}; nothing submitted`;
  await page.evaluate(__WATCH__).catch(() => {});
  if (SUBMIT === "Enter") await page.keyboard.press("Enter");
  else await (await page.waitForSelector(SUBMIT, { visible: true, timeout: 15000 })).click();
  if (!SECRET) {
    await new Promise((r) => setTimeout(r, 1500));
    return `typed the ${FIELD} and submitted` + await said();
  }
  for (let i = 0; i < 20; i++) {
    await new Promise((r) => setTimeout(r, 250));
    if (!(await left())) return `typed the ${FIELD} and submitted`;
  }
} catch (e) {
  await clear();
  throw e;
}
const s = await said();
await clear();
return `typed the ${FIELD} and submitted, but the form was still there after 5 s, ` +
  "so its vault fields were emptied: check whether it worked" + s;
"""

# A DevTools client on a bare WebSocket, standard library only. It sends just
# the commands it is asked to; no Runtime.enable, which bot checks detect (the
# shop tabs enable only Page, which pages can't see). Kept as source because
# the shop filler runs it again in a child process (_SHOP_FILL_PY), which is
# where secrets live.
_CDP_PY = r'''
import asyncio, base64, json, os, struct, urllib.parse

class _CDP:
    def __init__(self, reader, writer):
        self._r, self._w = reader, writer
        self._n = 0
        # The main frame's URL from Page events (Page enabled: shop tabs); None
        # until one came. _tab_mail_ui reads it.
        self.page_url = self._main_frame = None
        self._pending = {}
        # Called with every event (a message without an id); capture() uses it.
        self._listeners = []
        self._task = asyncio.ensure_future(self._read_loop())

    @classmethod
    async def connect(cls, ws_url):
        u = urllib.parse.urlsplit(ws_url)
        reader, writer = await asyncio.open_connection(u.hostname, u.port, limit=1 << 26)
        key = base64.b64encode(os.urandom(16)).decode()
        writer.write(
            f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode()
        )
        status = (await reader.readuntil(b"\r\n\r\n")).split(b"\r\n", 1)[0]
        if b" 101 " not in status:
            writer.close()
            raise ConnectionError(f"DevTools handshake failed: {status.decode(errors='replace')}")
        return cls(reader, writer)

    async def _read_loop(self):
        message = b""
        try:
            while True:
                b0, b1 = await self._r.readexactly(2)
                n = b1 & 0x7F
                if n == 126:
                    n = struct.unpack(">H", await self._r.readexactly(2))[0]
                elif n == 127:
                    n = struct.unpack(">Q", await self._r.readexactly(8))[0]
                mask = await self._r.readexactly(4) if b1 & 0x80 else None
                data = await self._r.readexactly(n)
                if mask:
                    data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
                op = b0 & 0x0F
                if op == 8:
                    break
                if op == 9:
                    self._frame(10, data)
                    continue
                if op not in (0, 1, 2):
                    continue
                message += data
                if not b0 & 0x80:
                    continue
                msg, message = json.loads(message), b""
                if "id" not in msg:
                    method = msg.get("method")
                    if method == "Page.frameNavigated":
                        f = msg.get("params", {}).get("frame", {})
                        if not f.get("parentId"):
                            self.page_url, self._main_frame = f.get("url"), f.get("id")
                    elif method == "Page.navigatedWithinDocument":
                        p = msg.get("params", {})
                        if p.get("frameId") == self._main_frame:
                            self.page_url = p.get("url")
                    for listener in list(self._listeners):
                        try:
                            listener(msg)
                        except Exception:
                            pass  # a listener's bug must not end the connection
                    continue
                fut = self._pending.pop(msg.get("id"), None)
                if fut is None or fut.done():
                    continue
                if "error" in msg:
                    fut.set_exception(RuntimeError(f"DevTools: {msg['error'].get('message')}"))
                else:
                    fut.set_result(msg.get("result", {}))
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("DevTools connection closed"))
            self._pending.clear()

    def _frame(self, op, data):
        n = len(data)
        if n < 126:
            head = bytes([0x80 | op, 0x80 | n])
        elif n < 1 << 16:
            head = bytes([0x80 | op, 0x80 | 126]) + struct.pack(">H", n)
        else:
            head = bytes([0x80 | op, 0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        self._w.write(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    async def send(self, method, params=None, timeout=60):
        if self._task.done():
            raise ConnectionError("DevTools connection closed")
        self._n += 1
        n = self._n
        fut = asyncio.get_running_loop().create_future()
        self._pending[n] = fut
        self._frame(1, json.dumps({"id": n, "method": method, "params": params or {}}).encode())
        await self._w.drain()
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(n, None)  # a late answer finds nobody waiting

    async def evaluate(self, expression, timeout=60, context=None):
        params = {"expression": expression, "returnByValue": True, "awaitPromise": True}
        if context is not None:
            params["contextId"] = context
        r = await self.send("Runtime.evaluate", params, timeout)
        if "exceptionDetails" in r:
            d = r["exceptionDetails"]
            text = d.get("exception", {}).get("description") or d.get("text") or "error"
            raise RuntimeError(f"page script: {text.splitlines()[0]}")
        return r["result"].get("value")

    async def click_at(self, x, y):
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            await self.send(
                "Input.dispatchMouseEvent",
                {"type": kind, "x": x, "y": y, "button": "left", "clickCount": 1},
            )

    async def drag_at(self, x1, y1, x2, y2, steps=15):
        """Press the left button at (x1, y1), move it to (x2, y2) in `steps`
        eased moves (~16 ms apart, as a hand does), release it there."""
        ev = lambda kind, x, y, **kw: self.send("Input.dispatchMouseEvent", {  # noqa: E731
            "type": kind, "x": x, "y": y, "button": "left", "clickCount": 1, **kw})
        await self.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x1, "y": y1})
        await ev("mousePressed", x1, y1, buttons=1)
        await asyncio.sleep(0.05)
        n = max(1, int(steps))
        for i in range(1, n + 1):
            t = i / n
            e = t * t * (3 - 2 * t)  # smoothstep: slow start and end
            await ev("mouseMoved", x1 + (x2 - x1) * e, y1 + (y2 - y1) * e, buttons=1)
            await asyncio.sleep(0.016)
        await asyncio.sleep(0.05)
        await ev("mouseReleased", x2, y2, buttons=0)

    _KEYS = {"Enter": (13, "\r"), "Return": (13, "\r"), "Tab": (9, ""), "Escape": (27, ""), "Backspace": (8, ""),
             "Delete": (46, ""), "ArrowDown": (40, ""), "ArrowUp": (38, ""), "ArrowLeft": (37, ""),
             "ArrowRight": (39, ""), "Home": (36, ""), "End": (35, ""), "PageUp": (33, ""),
             "PageDown": (34, ""), "Space": (32, " ")}
    _MODS = {"Alt": 1, "Ctrl": 2, "Control": 2, "Meta": 4, "Shift": 8}
    # US-layout punctuation keys: windowsVirtualKeyCode, DOM code
    _PUNCT = {",": (188, "Comma"), ".": (190, "Period"), "-": (189, "Minus"), "=": (187, "Equal"),
              "+": (187, "Equal"), "/": (191, "Slash"), ";": (186, "Semicolon"), "'": (222, "Quote"),
              "[": (219, "BracketLeft"), "]": (221, "BracketRight"), "\\": (220, "Backslash"),
              "`": (192, "Backquote")}

    async def press(self, key):
        """A key, optionally with modifiers: "End", "Shift+Tab", "Ctrl+A",
        "Control+,"; a lone "+" (or "Ctrl++") is the plus key."""
        if key.endswith("+") and (len(key) == 1 or key.endswith("++")):
            mods, base = (key[:-2].split("+") if len(key) > 1 else []), "+"
        else:
            *mods, base = key.split("+")
        if any(m not in self._MODS for m in mods):
            raise ValueError(f"unknown modifier in {key!r}; known: {', '.join(self._MODS)}")
        bits = sum({self._MODS[m] for m in mods})
        if base in self._KEYS:
            code, text = self._KEYS[base]
            name, phys = (" " if base == "Space" else "Enter" if base == "Return" else base), \
                ("Enter" if base == "Return" else base)
        elif len(base) == 1 and base.isascii() and base.isalnum():
            code, text = ord(base.upper()), base
            name, phys = base, ("Digit" if base.isdigit() else "Key") + base.upper()
        elif base in self._PUNCT:
            (code, phys), text, name = self._PUNCT[base], base, base
        else:
            raise ValueError(f"unknown key {key!r}; known: {', '.join(self._KEYS)}, letters, digits, "
                             f"{' '.join(self._PUNCT)}")
        if bits & 7:
            text = ""  # a shortcut types nothing
        down = {"type": "keyDown", "key": name, "code": phys, "windowsVirtualKeyCode": code, "modifiers": bits}
        if text:
            down["text"] = text
        if bits == 2 and base.lower() == "a":
            down["commands"] = ["selectAll"]
        await self.send("Input.dispatchKeyEvent", down)
        await self.send("Input.dispatchKeyEvent",
                        {"type": "keyUp", "key": name, "code": phys, "windowsVirtualKeyCode": code, "modifiers": bits})

    def close(self):
        self._task.cancel()
        self._w.close()
'''
exec(_CDP_PY)

# The frames of a shop tab, for every ShopTab call and for the shop filler,
# which runs this source again in its child process. In-process frames (the
# page's own site) run page expressions in an isolated world on the tab's
# connection; cross-site frames (their own DevTools targets, found by the
# parentId chain of this tab only) on a connection of their own. Mouse events
# always go to the tab itself, at top-level CSS px: the frame's offset (its
# <iframe>'s content box, summed over nested frames) plus the point inside it.
_FRAMES_PY = r'''
import asyncio, json, re, urllib.parse, urllib.request

_FRAME_ERRORS = (RuntimeError, ConnectionError, OSError, asyncio.TimeoutError, KeyError, TypeError, ValueError)
_FRAME_GROUP = "vf-frames"
# Frames that are ads or trackers (observe() leaves them out).
_AD_FRAME = re.compile(r"(^|\.)(doubleclick\.net|googlesyndication\.com|googleadservices\.com|adnxs\.com"
                       r"|amazon-adsystem\.com|criteo\.(com|net)|taboola\.com|outbrain\.com|rubiconproject\.com"
                       r"|pubmatic\.com|casalemedia\.com|smartadserver\.com|yieldlab\.net|adform\.net|teads\.tv"
                       r"|googletagmanager\.com|demdex\.net|everesttech\.net|bat\.bing\.com)$")
# Runs on a frame's <iframe>, in its parent document: the content box in that
# document's viewport (CSS px), the scale of a CSS transform, whether it shows,
# its place among the document's frames. MODE "scroll" first scrolls it into
# view, "at" scrolls the document until the point (X, Y) is in view, "hit"
# also says what is at (X, Y): true for this frame, else that element.
_OWNER_JS = r"""function (MODE, X, Y) {
  const s = getComputedStyle(this);
  let r = this.getBoundingClientRect();
  if (MODE === "scroll" && (r.top < 0 || r.left < 0 || r.bottom > innerHeight || r.right > innerWidth)) {
    this.scrollIntoView({ block: r.height > innerHeight ? "start" : "center", inline: "center", behavior: "instant" });
    r = this.getBoundingClientRect();
  }
  if (MODE === "at" && (X < 0 || Y < 0 || X >= innerWidth || Y >= innerHeight)) {
    scrollBy(X < 0 || X >= innerWidth ? X - innerWidth / 2 : 0, Y < 0 || Y >= innerHeight ? Y - innerHeight / 2 : 0);
    r = this.getBoundingClientRect();
  }
  const sx = this.offsetWidth ? r.width / this.offsetWidth : 1, sy = this.offsetHeight ? r.height / this.offsetHeight : 1;
  const px = (k) => parseFloat(s[k]) || 0;
  const out = {
    x: r.left + (this.clientLeft + px("paddingLeft")) * sx, y: r.top + (this.clientTop + px("paddingTop")) * sy,
    w: Math.max(0, this.clientWidth - px("paddingLeft") - px("paddingRight")) * sx,
    h: Math.max(0, this.clientHeight - px("paddingTop") - px("paddingBottom")) * sy,
    sx, sy, vw: innerWidth, vh: innerHeight,
    shown: r.width > 1 && r.height > 1 && s.visibility === "visible" && this.checkVisibility({ opacityProperty: true }),
    index: [...this.ownerDocument.querySelectorAll("iframe, frame")].indexOf(this),
  };
  if (MODE === "hit") {
    let h = document.elementFromPoint(X, Y);
    while (h?.shadowRoot) { const i = h.shadowRoot.elementFromPoint(X, Y); if (!i || i === h) break; h = i; }
    out.hit = h === this ? true : h ? (h.localName + " " + (h.innerText || "").replace(/\s+/g, " ").trim().slice(0, 60)).trim() : null;
  }
  return out;
}"""
# The focused element (through open shadow roots) if it is a frame's <iframe>.
_FOCUSED_FRAME_JS = """(() => {
  let a = document.activeElement;
  while (a?.shadowRoot?.activeElement) a = a.shadowRoot.activeElement;
  return a && /^i?frame$/.test(a.localName) ? a : null;
})()"""


class _Doc:
    """A document of a shop tab where page expressions run: the top one
    (parent None) or a frame's. ox, oy, sx, sy map its viewport to the top
    page's CSS px; box is its viewport there, [x, y, w, h]."""

    def __init__(self, fs, cdp, frame_id, url="", parent=None, *, frame=None, target=None, loader=None):
        self.fs, self.cdp, self.frame_id, self.url, self.parent = fs, cdp, frame_id, url, parent
        self.frame = frame  # an in-process frame's Page.getFrameTree info; its world is made on first use
        self.target = target  # a cross-site frame: its own target id (= frame id)
        self.loader = loader or (frame or {}).get("loaderId")
        self.ctx = None
        self.depth = 0 if parent is None else parent.depth + 1
        self.ox = self.oy = 0.0
        self.sx = self.sy = 1.0
        self.box, self.shown, self.index, self.vw, self.vh = None, parent is None, -1, None, None

    @property
    def host(self):
        return urllib.parse.urlsplit(self.url).hostname or ""

    @property
    def blank(self):
        return not self.url.startswith(("http:", "https:"))

    def within(self, other):
        n = self.parent
        while n is not None and n is not other:
            n = n.parent
        return n is other

    async def ev(self, expr, timeout=20):
        if self.frame is not None and self.ctx is None:
            self.ctx = await self.fs._world(self.cdp, self.frame)
        try:
            return await self.cdp.evaluate(expr, timeout, context=self.ctx)
        except ConnectionError:
            raise RuntimeError(f"the frame {self.host or self.url} went away") from None


class _Frames:
    """The frames of one shop tab, listed when first needed (load) during one
    call: document order, nested up to 3 deep. `worlds` caches isolated-world
    context ids per (frame id, loader id) across calls. close() ends the
    connections it opened to cross-site frames."""

    def __init__(self, cdp, tab_id, port, worlds=None):
        self.cdp, self.tab_id, self.port = cdp, tab_id, port
        self.worlds = {} if worlds is None else worlds
        self.subs, self.owners, self.docs, self.at = {}, {}, [], None
        self.top = _Doc(self, cdp, tab_id)

    async def close(self):
        if self.at is not None:  # the <iframe> handles and documents the lookups kept
            try:
                await self.cdp.send("Runtime.releaseObjectGroup", {"objectGroup": _FRAME_GROUP}, 3)
            except _FRAME_ERRORS:
                pass
        for s in self.subs.values():
            s.close()
        self.subs.clear()

    async def _world(self, cdp, f):
        key = (f["id"], f.get("loaderId"))
        if key not in self.worlds:
            for k in [k for k in self.worlds if k[0] == f["id"]]:
                del self.worlds[k]  # an older document of that frame
            r = await cdp.send("Page.createIsolatedWorld", {"frameId": f["id"], "worldName": "vf-frames"}, 5)
            self.worlds[key] = r["executionContextId"]
        return self.worlds[key]

    async def _sub(self, t):
        sub = self.subs.get(t["id"])
        if sub is None or sub._task.done():
            sub = self.subs[t["id"]] = await asyncio.wait_for(_CDP.connect(t["webSocketDebuggerUrl"]), 5)
        return sub

    async def load(self, fresh=0.7):
        """The frames (not the top document), listed again when the last list
        is older than `fresh` s."""
        loop = asyncio.get_running_loop()
        if self.at is not None and loop.time() - self.at < fresh:
            return self.docs
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=3) as r:
                iframes = [t for t in json.loads(r.read()) if t.get("type") == "iframe"]
        except (OSError, ValueError):
            iframes = []
        kids = {}

        async def visit(cdp, tid, root):
            try:
                tree = (await cdp.send("Page.getFrameTree", timeout=5))["frameTree"]
            except _FRAME_ERRORS:
                return
            if root.parent is not None:
                root.url, root.loader = tree["frame"].get("url", root.url), tree["frame"].get("loaderId")
            local = [root]

            def walk(node, parent):
                for ch in node.get("childFrames", []):
                    if parent.depth >= 3:
                        continue
                    f = ch["frame"]
                    d = _Doc(self, cdp, f["id"], f.get("url", ""), parent, frame=f)
                    kids.setdefault(parent, []).append(d)
                    local.append(d)
                    walk(ch, d)

            walk(tree, root)
            for t in iframes:
                if t.get("parentId") != tid or not t.get("webSocketDebuggerUrl"):
                    continue
                parent = root if len(local) == 1 else await self._owner_doc(cdp, t["id"], local)
                if parent is None or parent.depth >= 3:
                    continue
                try:
                    sub = await self._sub(t)
                except (OSError, ConnectionError, asyncio.TimeoutError):
                    continue
                d = _Doc(self, sub, t["id"], t.get("url", ""), parent, target=t["id"])
                kids.setdefault(parent, []).append(d)
                await visit(sub, t["id"], d)

        await visit(self.cdp, self.tab_id, self.top)
        out = []

        async def order(parent):
            ch = kids.get(parent, [])
            for d in ch:
                await self._measure(d)
            ch.sort(key=lambda d: d.index if d.index >= 0 else 1 << 30)
            for d in ch:
                out.append(d)
                await order(d)

        await order(self.top)
        self.docs, self.at = out, loop.time()
        return out

    async def _owner_doc(self, cdp, frame_id, local):
        """Which of the in-process documents `local` holds the <iframe> of
        frame `frame_id` (a cross-site frame of the same target)."""
        try:
            own = await cdp.send("DOM.getFrameOwner", {"frameId": frame_id}, 5)
            obj = (await cdp.send("DOM.resolveNode", {"backendNodeId": own["backendNodeId"],
                                                      "objectGroup": _FRAME_GROUP}, 5))["object"]
            r = await cdp.send("Runtime.callFunctionOn", {"objectId": obj["objectId"], "objectGroup": _FRAME_GROUP,
                                                          "functionDeclaration": "function () { return this.ownerDocument; }"}, 5)
            want = (await cdp.send("DOM.describeNode", {"objectId": r["result"]["objectId"]}, 5))["node"]["backendNodeId"]
            for d in local:
                params = {"expression": "document", "objectGroup": _FRAME_GROUP}
                if d.frame is not None:
                    params["contextId"] = d.ctx = d.ctx or await self._world(cdp, d.frame)
                r = await cdp.send("Runtime.evaluate", params, 5)
                if (await cdp.send("DOM.describeNode", {"objectId": r["result"]["objectId"]}, 5))["node"]["backendNodeId"] == want:
                    return d
        except _FRAME_ERRORS:
            pass
        return None

    async def _owner(self, d, mode="box", x=None, y=None):
        """_OWNER_JS on frame d's <iframe>, in its parent document."""
        p = d.parent
        oid = self.owners.get(d.frame_id)
        if oid is None:
            own = await p.cdp.send("DOM.getFrameOwner", {"frameId": d.frame_id}, 5)
            params = {"backendNodeId": own["backendNodeId"], "objectGroup": _FRAME_GROUP}
            if p.frame is not None:
                params["executionContextId"] = p.ctx = p.ctx or await self._world(p.cdp, p.frame)
            oid = self.owners[d.frame_id] = (await p.cdp.send("DOM.resolveNode", params, 5))["object"]["objectId"]
        r = await p.cdp.send("Runtime.callFunctionOn", {
            "objectId": oid, "functionDeclaration": _OWNER_JS, "returnByValue": True,
            "arguments": [{"value": mode}, {"value": x}, {"value": y}]}, 5)
        if "exceptionDetails" in r:
            raise RuntimeError("frame owner: " + str(r["exceptionDetails"].get("text")))
        return r["result"]["value"]

    async def _measure(self, d, mode="box", x=None, y=None):
        p = d.parent
        try:
            b = await self._owner(d, mode, x, y)
        except _FRAME_ERRORS:
            self.owners.pop(d.frame_id, None)
            d.shown = False
            return None
        d.ox, d.oy = p.ox + b["x"] * p.sx, p.oy + b["y"] * p.sy
        d.sx, d.sy = p.sx * b["sx"], p.sy * b["sy"]
        d.box = [round(d.ox), round(d.oy), round(b["w"] * p.sx), round(b["h"] * p.sy)]
        d.shown, d.index, p.vw, p.vh = p.shown and b["shown"], b["index"], b["vw"], b["vh"]
        return b

    def shown_docs(self):
        return [d for d in self.docs if d.shown and not d.blank]

    def by_frame(self, frame_id):
        return next((d for d in self.docs if d.frame_id == frame_id), None)

    async def named(self, frame):
        """The frames `frame` names (a host or a piece of the URL, else a CSS
        selector of an <iframe> in the top document), with their frames."""
        await self.load()
        want = frame.lower()
        hits = [d for d in self.docs if not d.blank and (want == d.host or want in d.url.lower())]
        if not hits:
            expr = ("(() => { try { return [...document.querySelectorAll(" + json.dumps(frame) + ")]"
                    ".find((e) => /^i?frame$/.test(e.localName)) ?? null; } catch { return null; } })()")
            try:
                r = await self.cdp.send("Runtime.evaluate", {"expression": expr, "objectGroup": _FRAME_GROUP}, 5)
                oid = r["result"].get("objectId")
                fid = oid and (await self.cdp.send("DOM.describeNode", {"objectId": oid}, 5))["node"].get("frameId")
            except _FRAME_ERRORS:
                fid = None
            hits = [d for d in self.docs if fid and d.frame_id == fid]
        return [d for d in self.docs if d.shown and any(d is h or d.within(h) for h in hits)]

    def describe(self, docs=None):
        """"the page and 2 frames: a.example, b.example": what a search looked at."""
        docs = self.shown_docs() if docs is None else docs
        hosts = list(dict.fromkeys(d.host for d in docs))
        return (f"the page and {len(docs)} frame{'' if len(docs) == 1 else 's'}"
                + (": " + ", ".join(hosts[:8]) + (", …" if len(hosts) > 8 else "") if hosts else ""))

    @staticmethod
    def where(doc):
        """" in frame <host>" for a frame's document, "" for the top one."""
        return f" in frame {doc.host or doc.url[:60]}" if doc is not None and doc.parent is not None else ""

    async def focused(self):
        """The document whose element has the focus: from the top one down
        through each focused <iframe>. RuntimeError when that frame can't be
        reached (nested too deep)."""
        doc = self.top
        for _ in range(6):
            params = {"expression": _FOCUSED_FRAME_JS, "objectGroup": _FRAME_GROUP}
            if doc.frame is not None:
                params["contextId"] = doc.ctx = doc.ctx or await self._world(doc.cdp, doc.frame)
            r = await doc.cdp.send("Runtime.evaluate", params, 5)
            oid = r["result"].get("objectId")
            if not oid:
                return doc
            fid = (await doc.cdp.send("DOM.describeNode", {"objectId": oid}, 5))["node"].get("frameId")
            await self.load(fresh=0)
            nxt = self.by_frame(fid)
            if nxt is None:
                raise RuntimeError("the focus is in a frame nested too deep to check")
            doc = nxt
        return doc

    async def point(self, doc, x, y, scroll=True):
        """(X, Y, cover): the point (x, y) of frame `doc` in top-level CSS px,
        after scrolling its ancestors until it shows (scroll=True); cover: what
        hides it in an ancestor document (the point must hit the <iframe> at
        every level), None when nothing does."""
        chain, n = [], doc
        while n.parent is not None:
            chain.append(n)
            n = n.parent
        chain.reverse()
        for _ in range(4):
            for c in chain:
                if await self._measure(c) is None:
                    return None, None, "nothing: the frame went away"
            X, Y = doc.ox + x * doc.sx, doc.oy + y * doc.sy
            off = None
            for c in reversed(chain):
                p = c.parent
                px, py = (X - p.ox) / p.sx, (Y - p.oy) / p.sy
                if not (0 <= px < (p.vw or 1e9) and 0 <= py < (p.vh or 1e9)):
                    off = (c, px, py)
                    break
            if off is None:
                break
            if not scroll:
                return X, Y, "nothing: the point is off screen"
            await self._owner(off[0], "at", off[1], off[2])
        else:
            return X, Y, "nothing: the point is off screen"
        for c in chain:
            p = c.parent
            b = await self._owner(c, "hit", (X - p.ox) / p.sx, (Y - p.oy) / p.sy)
            if b.get("hit") is not True:
                return X, Y, b.get("hit") or "nothing: the point is off screen"
        return X, Y, None


async def _frame_search(fs, expr, ok=bool, docs=None):
    """(doc, result) of the first of `docs` (default: the shown frames, in
    document order) where page expression `expr` gives a result `ok` takes;
    (None, None) if none does."""
    if docs is None:
        await fs.load()
        docs = fs.shown_docs()
    for d in docs:
        try:
            r = await d.ev(expr, 5)
        except _FRAME_ERRORS:
            continue
        if ok(r):
            return d, r
    return None, None
'''
exec(_FRAMES_PY)

