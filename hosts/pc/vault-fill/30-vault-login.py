# The shop filler: runs as a child process, so the secret lives only there,
# between rbw's output and the DevTools command that types it. The field
# may sit in a frame (_FRAMES_PY): login fields are checked against the site
# of the document that holds the field, right before typing.
_SHOP_FILL_PY = r'''
import asyncio, json, os, sys

def vf(body, **args):
    head = "".join(f"const {k} = {json.dumps(v)}; " for k, v in args.items())
    return "(() => { const VF = (" + LIB + ")(); " + head + body + " })()"

# Finds the field, remembers it (for CLEAR only a secret one), focuses it and
# selects what it holds, so the value replaces it; says which site's document
# holds it, whether it took the focus, and where it is.
PREPARE = """
  const el = SEL === null ? VF.active() : VF.all(SEL).find(VF.shown);
  if (!el || el === document.body || /^i?frame$/.test(el.localName)) return false;
  if (SECRET) (window[Symbol.for("vault-fill")] ??= new Set()).add(el);
  el.setAttribute("data-vf-vault", "");  // fields() shows "••• (vault)", never the value
  window[Symbol.for("vault-fill-el")] = el;
  el.scrollIntoView({ block: "center", behavior: "instant" });
  el.focus();
  try { el.select?.(); } catch {}
  const r = el.getBoundingClientRect();
  return { host: location.hostname, focused: VF.inside(VF.active(), el) && document.hasFocus(),
           x: r.x + Math.min(r.width / 2, 20), y: r.y + r.height / 2 };"""
# submit=True after Enter: the form's submit button, when the field is still
# shown on the same URL (Enter did nothing); else null.
FORM_SUBMIT = """
  const el = window[Symbol.for("vault-fill-el")];
  if (location.href !== HREF || !el?.isConnected || !VF.shown(el)) return null;
  const f = el.form ?? el.closest?.("form");
  const b = f ? [...f.querySelectorAll("button[type=submit], input[type=submit], button:not([type])")].find(VF.shown) : null;
  if (!b) return null;
  b.scrollIntoView({ block: "center", behavior: "instant" });
  const r = b.getBoundingClientRect();
  return [r.x + r.width / 2, r.y + r.height / 2];"""
SHOWN = "return VF.all(SEL).some(VF.shown);"
CENTER = """
  const el = VF.all(SEL).find(VF.shown);
  if (!el) return null;
  el.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
  const r = el.getBoundingClientRect();
  return [r.x + r.width / 2, r.y + r.height / 2];"""
# A code (totp, code) typed into one of N split boxes (B-75). BOXES: the field
# and the shown text inputs around it that are one-character boxes (maxlength 1,
# or no wider than 72 px and 1.6× their height), from the nearest ancestor up
# while every input in it is such a box, until LEN of them: the LEN from the
# field on, kept in vf-boxes (secret: among the vault fields, so CLEAR and LEFT
# see them all). Fewer than LEN: no split boxes ({n}). A field gone meanwhile
# (re-rendered) is found again by SEL.
CODE_FIELDS = ("totp", "code")
BOXES = """
  const K = Symbol.for("vf-boxes");
  let el = window[Symbol.for("vault-fill-el")];
  if (!el?.isConnected && SEL !== null) el = VF.all(SEL).find(VF.shown);
  window[K] = null;
  if (!el?.isConnected) return { n: 0 };
  window[Symbol.for("vault-fill-el")] = el;
  const texty = (e) => e.localName === "input" && /^(text|tel|number|password|search)$/.test(e.type)
    && !e.disabled && !e.readOnly && VF.shown(e);
  const box = (e) => {
    if (e.maxLength === 1) return true;
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.width <= 72 && r.width <= 1.6 * r.height;
  };
  let g = [el];
  if (texty(el) && box(el)) {
    for (let a = VF.up(el), i = 0; a && i < 6 && g.length < LEN; a = VF.up(a), i++) {
      const ins = [...a.querySelectorAll("input"), ...(a.shadowRoot?.querySelectorAll("input") ?? [])].filter(texty);
      if (!ins.includes(el) || !ins.every(box)) break;
      g = ins;
    }
  }
  if (g.length < LEN) return { n: g.length };
  const i = g.indexOf(el), s = i + LEN <= g.length ? i : g.length - LEN;
  g = g.slice(s, s + LEN);
  window[K] = g;
  if (SECRET) for (const e of g) {
    (window[Symbol.for("vault-fill")] ??= new Set()).add(e);
    e.setAttribute("data-vf-vault", "");
  }
  const id = (e) => e.id ? "#" + CSS.escape(e.id) : "";
  return { n: g.length, url: location.href,
           css: id(g[0]) && id(g.at(-1)) ? `${id(g[0])}…${id(g.at(-1))}` : `${LEN} inputs` };"""
# How the boxes are now, never what they hold: ok when each holds exactly one
# character; busy when all are disabled or read-only (the page checks the code).
BOXES_READ = """
  const g = window[Symbol.for("vf-boxes")] ?? [];
  const live = g.filter((e) => e.isConnected && VF.shown(e));
  const n = (e) => ((e.value ?? "").match(/[\\p{L}\\p{N}]/gu) ?? []).length;
  return { url: location.href, live: live.length, busy: live.length > 0 && live.every((e) => e.disabled || e.readOnly),
           filled: live.filter((e) => n(e) > 0).length, ok: live.length === g.length && live.every((e) => n(e) === 1) };"""
# Before typing them one by one: the boxes emptied (input events, so the page's
# own state follows).
BOXES_EMPTY = """
  for (const e of window[Symbol.for("vf-boxes")] ?? []) {
    if (!e.isConnected || !e.value) continue;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(e, "");
    e.dispatchEvent(new Event("input", { bubbles: true }));
  }"""
# Box I focused, its content selected.
BOX_FOCUS = """
  const el = (window[Symbol.for("vf-boxes")] ?? [])[I];
  if (!el?.isConnected) return false;
  el.scrollIntoView({ block: "center", behavior: "instant" });
  el.focus();
  try { el.select?.(); } catch {}
  const r = el.getBoundingClientRect();
  return { host: location.hostname, focused: VF.inside(VF.active(), el) && document.hasFocus(),
           x: r.x + Math.min(r.width / 2, 20), y: r.y + r.height / 2 };"""

def check(a, host, doc):
    """Login fields go only into a document of the entry's site."""
    host = (host or "").lower()
    if a["sites"] is not None and not any(host == s or host.endswith("." + s) for s in a["sites"]):
        where = "this page" if doc.parent is None else "the frame that holds the field"
        raise RuntimeError(f"{where} ({host or doc.url[:60]}) is not the entry's site "
                           f"({', '.join(a['sites'])}); pass any_site=True if it is")

async def find(fs, sel, frame, timeout, first=None):
    """The document with a visible `sel`: `first`, then the top one (unless
    `frame`), then the frames (those `frame` names); None after `timeout` s."""
    expr, loop = vf(SHOWN, SEL=sel), asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        for d in [first] * (first is not None) + [fs.top] * (frame is None):
            try:
                if await d.ev(expr, 5):
                    return d
            except _FRAME_ERRORS:
                pass
        if frame is not None:
            docs = await fs.named(frame)
        else:
            await fs.load()
            docs = fs.shown_docs()
        d, _ = await _frame_search(fs, expr, bool, [x for x in docs if first is None or x.frame_id != first.frame_id])
        if d is not None:
            return d
        if loop.time() > deadline:
            return None
        await asyncio.sleep(0.25)

async def field(fs, a):
    """The document of the field: the one with a visible selector, or for
    selector None the one whose element has the focus."""
    if a["selector"] is None:
        return await fs.focused()
    doc = await find(fs, a["selector"], a["frame"], 15)
    if doc is None:
        raise RuntimeError(f"no visible element {a['selector']} to type into; searched {fs.describe()}")
    return doc

async def focus(fs, cdp, doc, expr, what):
    """`expr` focuses a field and says where it is ({host, focused, x, y});
    a frame that keeps the focus from the script gets a real click into the
    field. None: `expr` found no field."""
    r = await doc.ev(expr, 5)
    if not r:
        return None
    for _ in range(4 if doc.parent is not None and not r["focused"] else 0):
        await asyncio.sleep(0.08)  # the focus crosses processes
        r = await doc.ev(expr, 5) or r
        if r["focused"]:
            break
    if doc.parent is not None and not r["focused"]:
        # A frame that kept the focus from the script: a real click into the field.
        x, y, _ = await fs.point(doc, r["x"], r["y"])
        if x is not None:
            await cdp.click_at(x, y)
            await asyncio.sleep(0.1)
        r = await doc.ev(expr, 5)
        if not (r and r["focused"]):
            raise RuntimeError(f"the field {what} in the frame {doc.host} doesn't take the focus")
    return r

async def boxes_now(doc, a, href, n):
    """How the split boxes are (BOXES_READ), or None when the page went on:
    navigated, the boxes gone, or all disabled (checking the code). Boxes
    re-rendered meanwhile are found again by the selector, once."""
    for again in (True, False):
        try:
            s = await doc.ev(vf(BOXES_READ), 5)
        except (RuntimeError, asyncio.TimeoutError, ConnectionError):
            return None
        if s["url"] != href or s["busy"]:
            return None
        if s["live"] == n:
            return s
        if not again or a["selector"] is None:
            return None
        g = await doc.ev(vf(BOXES, SEL=a["selector"], LEN=n, SECRET=a["field"].lower() not in PLAIN), 5)
        if not (g and g.get("css")):
            return None
    return None

async def split_boxes(fs, cdp, doc, a, value, grp):
    """The code went into the first of its split boxes (BOXES found them):
    when they don't hold one character each (the page didn't pass it on),
    empties them and types one character per box; raises when they still
    don't. Returns what to add to "typed the …", never the value."""
    n, css, s = len(value), grp["css"], None
    for wait in (0.15, 0.35):
        await asyncio.sleep(wait)
        s = await boxes_now(doc, a, grp["url"], n)
        if s is None:
            return ""  # the page went on
        if s["ok"]:
            return f" ({n} split boxes {css}, one character each)"
    first = s["filled"]
    await doc.ev(vf(BOXES_EMPTY), 5)
    for i, ch in enumerate(value):
        r = await focus(fs, cdp, doc, vf(BOX_FOCUS, I=i), f"{css} (box {i + 1})")
        if not r:
            raise RuntimeError(f"the split box {i + 1} of {css} went away while the {a['field']} was typed into it")
        check(a, r["host"], doc)
        if ch.isascii() and ch.isalnum():
            await cdp.press(ch)  # a key press: what boxes that ignored inserted text take
        else:
            await cdp.send("Input.insertText", {"text": ch})
        await asyncio.sleep(0.05)
    for wait in (0.15, 0.35):
        await asyncio.sleep(wait)
        s = await boxes_now(doc, a, grp["url"], n)
        if s is None or s["ok"]:
            return (f" ({n} split boxes {css}: the first box didn't pass it on ({first} of {n} filled), so one"
                    " character per box" + ("; then the page went on)" if s is None else ", now one each)"))
    raise RuntimeError(
        f"typed the {n}-character {a['field']}{fs.where(doc)} into the {n} split boxes {css}, but only {s['filled']}"
        f" of {n} hold one character each, also after typing one per box, so they were emptied: the page may not"
        " be ready yet; wait for its code prompt, then fill again once")

async def fill(a):
    cdp = await _CDP.connect(a["ws"])
    fs = _Frames(cdp, a["tab"], a["port"])
    secret = a["field"].lower() not in PLAIN
    try:
        doc = await field(fs, a)
        check(a, await doc.ev("location.hostname", 5), doc)  # before reading the vault
        env = {**os.environ, "XDG_RUNTIME_DIR": a["runtime_dir"]}
        p = await asyncio.create_subprocess_exec(
            *a["args"], stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=env)
        out, err = await p.communicate()
        if p.returncode != 0:
            raise RuntimeError("rbw: " + (err.decode().strip() or f"exit {p.returncode}"))
        value = out.decode().removesuffix("\n").removesuffix("\r")
        if not value:
            raise RuntimeError(f"the entry has no {a['field']}")
        try:
            if a["selector"] is None:
                doc = await fs.focused()  # where the focus is now
            prepare = vf(PREPARE, SEL=a["selector"], SECRET=secret)
            r = await focus(fs, cdp, doc, prepare, a["selector"] or "")
            if not r:
                raise RuntimeError(f"no visible element {a['selector'] or '(the focused one)'} to type into")
            check(a, r["host"], doc)  # the document that holds the field, right before typing
            grp = None
            if a["field"].lower() in CODE_FIELDS and len(value) > 1:
                grp = await doc.ev(vf(BOXES, SEL=a["selector"], LEN=len(value), SECRET=secret), 5)
                grp = grp if grp and grp.get("css") else None
            # One trusted input event for the whole value, like Puppeteer's sendCharacter.
            await cdp.send("Input.insertText", {"text": value})
            await doc.ev(NOTIFY, 5)
            note = ""
            if grp:
                note = await split_boxes(fs, cdp, doc, a, value, grp)
            value = None
            typed = f"typed the {a['field']}{fs.where(doc)}{note}"
            if a["submit"] is None:
                return f"{typed}; nothing submitted"
            await asyncio.sleep(0.1)  # the page's handlers run before the submit
            try:
                await doc.ev(WATCH, 5)
            except (RuntimeError, asyncio.TimeoutError):
                pass  # no error text then, the rest works
            if a["submit"] in ("Enter", True):
                href = await doc.ev("location.href", 5)
                await cdp.press("Enter")
                if a["submit"] is True:
                    # Enter, else (the form is still there after 1.5 s) its submit button.
                    await asyncio.sleep(1.5)
                    try:
                        r = await doc.ev(vf(FORM_SUBMIT, HREF=href), 5)
                    except (RuntimeError, asyncio.TimeoutError, ConnectionError):
                        r = None  # it navigated: Enter did it
                    if r:
                        x, y = r
                        if doc.parent is not None:
                            x, y, _ = await fs.point(doc, x, y)
                        await cdp.click_at(x, y)
            else:
                sdoc = await find(fs, a["submit"], a["frame"], 15, first=doc)
                if sdoc is None:
                    raise RuntimeError(f"no visible element {a['submit']} to submit with; searched {fs.describe()}")
                await asyncio.sleep(0.15)
                x, y = await sdoc.ev(vf(CENTER, SEL=a["submit"]), 5)
                if sdoc.parent is not None:
                    x, y, _ = await fs.point(sdoc, x, y)
                await cdp.click_at(x, y)
            if not secret:
                # A username stays: the next step of a two-step login needs it.
                await asyncio.sleep(1.5)
                return f"{typed} and submitted" + await alerts(doc)
            for _ in range(20):
                await asyncio.sleep(0.25)
                try:
                    if not await doc.ev(LEFT, 5):
                        return f"{typed} and submitted"
                except (RuntimeError, asyncio.TimeoutError, ConnectionError):
                    return f"{typed} and submitted"
        except BaseException:
            await clear(doc)
            raise
        said = await alerts(doc)  # before clear(): emptying fields brings new errors
        await clear(doc)
        return (f"{typed} and submitted, but the form was still there after 5 s, "
                "so its vault fields were emptied: check whether it worked" + said)
    finally:
        await fs.close()
        cdp.close()

async def alerts(doc):
    try:
        said = await doc.ev(ALERTS, 5)
    except (RuntimeError, asyncio.TimeoutError, ConnectionError):
        return ""  # the page navigated
    return "; the page showed: " + " | ".join(said) if said else ""

async def clear(doc):
    try:
        await doc.ev(CLEAR, 5)
    except Exception:
        pass  # the page navigated: nothing left to empty

try:
    print(asyncio.run(fill(json.loads(os.environ["VAULT_FILL_ARGS"]))))
except Exception as e:
    print(str(e) or type(e).__name__, file=sys.stderr)
    sys.exit(1)
'''


def _rbw(*args, timeout=30):
    return _subprocess.run(
        [_RBW, *args],
        env=_ENV,
        stdin=_subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@_both
def vault_unlock(timeout=300):
    """Unlock the vault. When it is locked, a password window opens on the
    PC's screen, and Kirill types the master password into it."""
    if _rbw("unlocked").returncode == 0:
        return "unlocked"
    try:
        r = _rbw("unlock", timeout=timeout)
    except _subprocess.TimeoutExpired:
        raise RuntimeError(
            "The vault is locked, and nobody typed the master password into the "
            "window on the PC. Ask Kirill to unlock it (`rbw unlock`)."
        ) from None
    if r.returncode != 0:
        raise RuntimeError(f"rbw unlock failed: {r.stderr.strip()}")
    return "unlocked"


def _vault_entries():
    """Every entry's name, user, folder and websites (no secrets), fresh from
    the server when it answers."""
    vault_unlock()
    _rbw("sync", timeout=60)  # on failure, the local copy
    r = _rbw("list", "--raw")
    if r.returncode != 0:
        raise RuntimeError(f"rbw list failed: {r.stderr.strip()}")
    rows = []
    for e in _json.loads(r.stdout or "[]"):
        sites = []
        for u in e.get("uris") or []:
            u = u.get("uri") if isinstance(u, dict) else u
            if not isinstance(u, str) or (_re.match(r"[a-z][a-z0-9+.-]*://", u, _re.I) and not _re.match(r"https?://", u, _re.I)):
                continue
            try:
                host = _urlparse.urlsplit(u if "://" in u else "https://" + u).hostname
            except ValueError:
                continue
            if host and _site(host) not in sites:
                sites.append(_site(host))
        rows.append({"name": e.get("name") or "", "user": e.get("user") or "", "folder": e.get("folder") or "",
                     "sites": sites, "id": e.get("id") or ""})
    return rows


def _entry_by_user(user):
    """The one vault entry whose user is `user` (any case), else None."""
    hits = [e for e in _vault_entries() if e["user"] and e["user"].lower() == user.strip().lower()]
    return hits[0] if len(hits) == 1 else None


@_both
def vault_list():
    """Every vault entry: name, user, folder. No secrets."""
    return [{k: e[k] for k in ("name", "user", "folder")} for e in _vault_entries()]


@_both
def vault_find(term):
    """Vault entries whose name, user, folder or website contains `term`
    (case-insensitive), fresh from the server: name, user, folder, sites, id.
    Notes are not searched (they may hold secrets). No hit: prints the
    closest names and returns []."""
    rows, t = _vault_entries(), term.lower()
    hits = [e for e in rows if any(t in s.lower() for s in (e["name"], e["user"], e["folder"], *e["sites"]))]
    if not hits:
        close = _difflib.get_close_matches(t, [e["name"].lower() for e in rows], n=5, cutoff=0.5)
        names = [e["name"] for e in rows if e["name"].lower() in close]
        print(f"no entry for {term!r}; closest: {', '.join(names) or 'none'}")
    return hits


def _site(host):
    """Same site = same registrable domain (amazon.de covers www.amazon.de),
    with two-part suffixes kept together: co.uk and co.jp, and hosts where
    anyone can get a subdomain (ts.net covers every tailnet). IP addresses
    stay whole."""
    p = _re.sub(r"^www\.", "", host.lower()).split(".")
    if len(p) < 3 or _re.fullmatch(r"[\d.]+", host) or host.startswith("["):
        return ".".join(p)
    two = (len(p[-1]) == 2 and p[-2] in {"co", "com", "org", "net", "ac", "gov", "edu", "or", "ne"}) or (
        ".".join(p[-2:]) in {"ts.net", "github.io", "vercel.app", "netlify.app", "pages.dev", "web.app"}
    )
    return ".".join(p[-3:] if two else p[-2:])


def _entry_sites(pick, strict=True):
    """The registrable domains of the entry's web URIs (Bitwarden also stores
    app ids such as androidapp://…, which are skipped). strict: raise when
    there are none (or rbw fails), else []."""
    r = _rbw("get", "--field", "uris", *pick)
    if r.returncode != 0:
        if not strict:
            return []
        raise RuntimeError(f"rbw: {r.stderr.strip()}")
    sites = set()
    for uri in r.stdout.split():
        if not _re.match(r"https?://", uri, _re.I):
            if _re.match(r"[a-z][a-z0-9+.-]*://", uri, _re.I):
                continue
            uri = "https://" + uri
        try:
            host = _urlparse.urlsplit(uri).hostname
        except ValueError:
            continue
        if host:
            sites.add(_site(host))
    if not sites and strict:
        raise RuntimeError(
            "the entry has no website to check this page against; pass any_site=True if this page is its site"
        )
    return sorted(sites)


# Sites vault_fill never fills for (hard rule, no override): banks, and logins
# that text Kirill's phone. Matched on the first label of the registrable
# domain (sparkasse-koelnbonn.de, dkb.de, airbnb.co.uk) and on entry names.
_BANK_SITE = _re.compile(r"^(.*sparkasse.*|easybank|barclay.*|trade-?republic|comdirect|dkb|ing|ing-?diba|n26"
                         r"|consorsbank|commerzbank|deutsche-?bank|postbank|.*volksbank.*|vr|vr-?bank.*)$", _re.I)
_BANK_NAME = _re.compile(r"\b(sparkasse|easybank|barclays?|barclaycard|trade ?republic|comdirect|dkb|ing|ing[- ]?diba"
                         r"|n26|consorsbank|commerzbank|deutsche[ -]?bank|postbank|volksbank|vr[ -]?bank)\b", _re.I)
_SMS_TRAPS = {"kleinanzeigen": "its re-login sends an SMS to Kirill's phone",
              "airbnb": "it sends an SMS to Kirill's phone right after the e-mail step"}


def _never_fill(names=(), sites=()):
    """Why vault_fill refuses these entry names / sites (hard rule), or None."""
    for s in sites:
        label = s.lower().split(".")[0]
        if _BANK_SITE.match(label):
            return f"{s} is a bank: bank logins are off limits"
        if label in _SMS_TRAPS:
            return f"{s}: {_SMS_TRAPS[label]}"
    for n in names:
        if not n:
            continue
        m = _BANK_NAME.search(n)
        if m:
            return f"{n!r} is a bank entry ({m.group(0)}): bank logins are off limits"
        m = _re.search(r"kleinanzeigen|airbnb", n, _re.I)
        if m:
            return f"{n!r}: {_SMS_TRAPS[m.group(0).lower()]}"
    return None


def _refuse(why):
    raise HardRule(f"vault_fill refused: {why} (hard rule; there is no override): stop, tell Kirill")


# Login pages a brand keeps on another registrable domain: such a host counts
# as the site of an entry whose websites match one of its brand's patterns
# (fnmatch on registrable domains). Any other host still needs any_site=True.
# auth.lastminute.com for lastminute.de (MOVE-0150).
_MICROSOFT = ("microsoft.com", "live.com", "outlook.com", "outlook.de", "hotmail.com", "hotmail.de", "office.com",
              "microsoft365.com", "xbox.com", "skype.com", "msn.com", "bing.com")
_AUTH_HOSTS = {
    "auth.lastminute.com": ("lastminute.*",),
    "account.booking.com": ("booking.*",),
    "login.live.com": _MICROSOFT,
    "login.microsoftonline.com": _MICROSOFT,
    "id.atlassian.com": ("atlassian.com", "atlassian.net", "trello.com", "bitbucket.org"),
}


def _auth_hosts(sites):
    """The login hosts of _AUTH_HOSTS that serve one of `sites`."""
    return sorted(h for h, pats in _AUTH_HOSTS.items()
                  if _site(h) not in sites and any(_fnmatch.fnmatch(s, p) for s in sites for p in pats))


async def _fill_once(tab, selector, pick, field, submit, any_site, frame=None):
    # The entry's websites are read before any secret, for the hard rules too.
    known = _entry_sites(pick, strict=False)
    why = _never_fill(sites=known)
    if why:
        _refuse(why)
    sites = (known or _entry_sites(pick)) if field.lower() in _LOGIN_FIELDS and not any_site else None
    if sites:
        sites = sites + _auth_hosts(sites)
    args = [_RBW, "get", "--field", field, *pick]
    if isinstance(tab, ShopTab):
        spec = {
            "ws": tab._ws_url,
            "args": args,
            "sites": sites,
            "field": field,
            "selector": selector,
            "submit": submit,
            "runtime_dir": _ENV["XDG_RUNTIME_DIR"],
            "tab": tab.id,
            "port": _SHOP_PORT,
            "frame": frame,
        }
        consts = {
            "CLEAR": _CLEAR_EXPR,
            "LEFT": _LEFT_EXPR,
            "NOTIFY": _NOTIFY_EXPR,
            "WATCH": _ALERT_WATCH,
            "ALERTS": _ALERT_READ,
            "LIB": _LIB_JS,
            "PLAIN": sorted(_PLAIN_FIELDS),
        }
        code = (_CDP_PY + _FRAMES_PY + "".join(f"\n{k} = {v!r}" for k, v in consts.items()) + "\n"
                + _SHOP_FILL_PY)
        p = await _asyncio.create_subprocess_exec(
            _sys.executable,
            "-c",
            code,
            env={**_ENV, "VAULT_FILL_ARGS": _json.dumps(spec)},
            stdin=_asyncio.subprocess.DEVNULL,
            stdout=_asyncio.subprocess.PIPE,
            stderr=_asyncio.subprocess.PIPE,
        )
        out, err = await _asyncio.wait_for(p.communicate(), 90)
        if p.returncode != 0:
            raise RuntimeError(err.decode().strip() or f"the filler exited with {p.returncode}")
        return out.decode().strip()
    submit = "Enter" if submit is True else submit  # the legacy path has no button fallback
    js = _FILL_JS
    for key, val in {
        "__ARGS__": _json.dumps(args),
        "__SITES__": _json.dumps(sites),
        "__FIELD__": _json.dumps(field),
        "__SELECTOR__": _json.dumps(selector),
        "__SUBMIT__": _json.dumps(submit),
        "__PLAIN__": _json.dumps(sorted(_PLAIN_FIELDS)),
        "__RUNTIME_DIR__": _json.dumps(_ENV["XDG_RUNTIME_DIR"]),
        "__CLEAR__": _json.dumps(_CLEAR_EXPR),
        "__LEFT__": _json.dumps(_LEFT_EXPR),
        "__NOTIFY__": _json.dumps(_NOTIFY_EXPR),
        "__WATCH__": _json.dumps(_ALERT_WATCH),
        "__ALERTS__": _json.dumps(_ALERT_READ),
    }.items():
        js = js.replace(key, val)
    return await tab.run(js, timeout=60)


# Per-site login locks this kernel holds: {site: {"fd", "timer"}}. A lock is
# an flock on a file next to the shop browser's own lock; a timer thread lets
# it go, so it also ends between cells.
_LOGIN_LOCKS = globals().get("_LOGIN_LOCKS") or {}
_LOGIN_MUTEX = globals().get("_LOGIN_MUTEX") or _threading.Lock()


def _login_lock_file(site):
    return _shop_file("login-" + _re.sub(r"[^a-z0-9.-]", "_", site.lower()) + ".lock")


async def _site_login_lock(site, wait=15):
    """Take the login lock of `site` (a registrable domain) for this kernel:
    "ours" (also when already ours). When another agent holds it, waits up
    to `wait` s for it to go and takes nothing: "freed" (that agent's login
    may have logged this one in too) or "held"."""
    with _LOGIN_MUTEX:
        held = _LOGIN_LOCKS.get(site)
        if held:
            if held["timer"]:
                held["timer"].cancel()
                held["timer"] = None
            return "ours"
    fd = _os.open(_login_lock_file(site), _os.O_RDWR | _os.O_CREAT, 0o600)
    deadline, waited = _time.monotonic() + wait, False
    while True:
        try:
            _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if _time.monotonic() > deadline:
                _os.close(fd)
                return "held"
            waited = True
            await _asyncio.sleep(0.5)
    if waited:
        _os.close(fd)
        return "freed"
    _os.ftruncate(fd, 0)
    _os.pwrite(fd, str(_os.getpid()).encode(), 0)
    with _LOGIN_MUTEX:
        _LOGIN_LOCKS[site] = {"fd": fd, "timer": None}
    return "ours"


# After another agent's login: is `SEL` (null: unknown) there, and a field
# of a login form (password, one-time code, user name or e-mail)?
_LOGIN_FORM_JS = r"""
  const LOGIN = 'input[type=password], input[autocomplete~="one-time-code"], input[autocomplete~=username], '
    + 'input[type=email], input[name*=user i], input[name*=email i], input[name*=login i], input[id*=email i]';
  return { field: SEL === null ? null : !!VF.find({ css: SEL }).el, form: VF.all(LOGIN).some(VF.shown) };"""


def _site_login_release(site, after=30):
    """Let the login lock of `site` go `after` s from now (0: at once)."""
    def drop():
        with _LOGIN_MUTEX:
            held = _LOGIN_LOCKS.pop(site, None)
        if held:
            _os.close(held["fd"])  # releases the flock

    with _LOGIN_MUTEX:
        held = _LOGIN_LOCKS.get(site)
        if not held:
            return
        if held["timer"]:
            held["timer"].cancel()
        if after <= 0:
            held["timer"] = None
    if after <= 0:
        drop()
        return
    t = _threading.Timer(after, drop)
    t.daemon = True
    with _LOGIN_MUTEX:
        held["timer"] = t
    t.start()


async def _login_form(tab, selector, frame):
    """_LOGIN_FORM_JS over the page and its frames (only those `frame`
    names, if given): {field, form}, each true if any document has it."""
    expr = _vf(_LOGIN_FORM_JS, SEL=selector)
    st = {"field": None, "form": False} if frame is not None else await tab._ev(expr, 10)
    if st["field"]:
        return st
    fs = tab._frames()
    try:
        await fs.load()
        for d in await fs.named(frame) if frame is not None else fs.shown_docs():
            try:
                s = await d.ev(expr, 5)
            except _FRAME_ERRORS:
                continue
            st = {k: st[k] or s[k] for k in ("field", "form")}
    finally:
        await fs.close()
    return st


async def vault_fill(
    tab, selector, entry, field="password", *, user=None, folder=None, submit=None, any_site=False, frame=None
):
    """Type `field` of vault entry `entry` into `selector` (None: the element
    that has the focus), then click `submit` (a selector, "Enter", or True:
    Enter, and the form's submit button when Enter did nothing).

    tab: a ShopTab, or (legacy) an agent_tab or its name. entry: name, URI or
    id of the entry (vault_find shows names and ids; a username that only one
    entry has picks that entry, and says so); `user` and `folder` pick one of
    several entries with that name. field: password, username, totp (the
    current code), number, exp_month, exp_year, cvv, cardholder, or a custom
    field's name. Login fields go only into a page of the entry's own site,
    unless any_site=True. `submit` clicks the first visible match: with
    several submit buttons, use a precise selector, or None and then click().
    Returns what it did, never the value; when the form stays after the
    submit, also the error text the page showed.

    Frames: `selector` and `submit` are looked for in the page, then in its
    frames (submit first in the field's own document), or only in the frame
    `frame` names (a host, a piece of its URL, or the <iframe>'s CSS
    selector). Login fields are checked against the site of the document
    that holds the field (for selector None: the focused one, in whatever
    frame), so a login frame of another site is refused unless any_site.

    Only one kernel logs in to a site at a time. While another agent's
    login on the site runs, this waits for it (≤ 60 s), reloads the page and
    looks again: when `selector` and every login field are gone then, that
    login logged this tab in too, and it returns "not filled: …" without
    filling; when the page shows a login form without `selector`, it
    raises (start the login over); else it fills.

    Several entries match `entry`: ValueError naming the user= (or folder=)
    values that pick one. The shop browser restarted or the tab went away
    meanwhile: BrowserRestarted (open the tab again; logins persist).
    """
    if isinstance(tab, str):
        tab = browser.tab(tab)  # noqa: F821 (the eval kernel's browser)
    try:
        return await _vault_fill(tab, selector, entry, field, user=user, folder=folder, submit=submit,
                                 any_site=any_site, frame=frame)
    except (BrowserRestarted, HardRule, ValueError):
        raise
    except Exception as e:
        if "multiple entries found" in str(e):
            raise ValueError(_several_entries(entry, user, folder)) from None
        # A restart shows up as whatever broke first, e.g. "[Errno 104]
        # Connection reset by peer" from the filler (LIFE-0338).
        err = _restart_error(tab.id, tab._browser) if isinstance(tab, ShopTab) else None
        if err is not None:
            raise err from e
        raise


def _several_entries(entry, user, folder):
    """What picks one entry when `entry` (with `user`/`folder`) matches
    several (rbw: "multiple entries found"; MONEY-0047)."""
    try:
        rows = _vault_entries()
    except RuntimeError:
        rows = []
    e = entry.strip().lower()
    try:
        host = _urlparse.urlsplit(e if "://" in e else "https://" + e).hostname or ""
    except ValueError:
        host = ""
    hits = [r for r in rows if r["name"].lower() == e or r["id"] == entry.strip()
            or ("." in host and _site(host) in r["sites"])]
    hits = [r for r in hits if (not user or r["user"].lower() == user.strip().lower())
            and (not folder or r["folder"] == folder)]
    users = sorted({r["user"] for r in hits})
    folders = sorted({r["folder"] for r in hits})
    said = f"several vault entries match {entry!r}" + (f" with user={user!r}" if user else "") + (
        f" in folder={folder!r}" if folder else "")
    if len(users) > 1 and not user:
        return f"{said}: pass user=<one of: {', '.join(map(repr, users))}> (only one of Kirill's own addresses)"
    if len(folders) > 1 and not folder:
        return f"{said}: pass folder=<one of: {', '.join(map(repr, folders))}>"
    if len(hits) > 1:
        return f"{said}: pass the entry's id instead (one of: {', '.join(r['id'] for r in hits)})"
    return f"{said}: vault_find({entry!r}) lists them; pass user=… or folder=…"


async def _vault_fill(tab, selector, entry, field, *, user, folder, submit, any_site, frame):
    if frame is not None and not isinstance(tab, ShopTab):
        raise ValueError("frame= needs a ShopTab")
    pick = (["--folder", folder] if folder else []) + [entry] + ([user] if user else [])
    # Hard rule, before the vault is read: no bank, no login that texts Kirill's phone.
    page = ""
    if isinstance(tab, ShopTab):
        page = _urlparse.urlsplit(await tab.url()).hostname or ""
    try:
        entry_host = _urlparse.urlsplit(entry if "://" in entry else "https://" + entry.strip()).hostname or ""
    except ValueError:
        entry_host = ""
    why = _never_fill([entry, folder], [_site(h) for h in (page, entry_host) if h and "." in h])
    if why:
        _refuse(why)
    lock = None
    if isinstance(tab, ShopTab) and field.lower() in _LOGIN_FIELDS:
        url = await tab.url()
        host = _urlparse.urlsplit(url).hostname or ""
        lock = _site(host) if host else None
        got = await _site_login_lock(lock, wait=60) if lock else "ours"
        if got == "freed":
            await tab.goto(url)
            st = await _login_form(tab, selector, frame)
            gone, now = await tab.logged_out(), await tab.url()
            if not st["field"] and not st["form"] and not gone:
                return (f"not filled: another agent's login to {lock} finished meanwhile, and after a reload the"
                        f" page ({now}) shows neither {selector or 'a login field'} nor any other login field:"
                        " probably logged in now; check with text()")
            if not st["field"]:
                raise RuntimeError(
                    f"another agent's login to {lock} finished meanwhile; after a reload the page ({now}) shows"
                    + (f" {gone!r}" if gone else " a login form") + f" but not {selector or 'the focused field'}:"
                    " start the login over from its first step")
            got = await _site_login_lock(lock, wait=60)
        if got != "ours":
            raise RuntimeError(
                f"another agent has been logging in to {lock} in the shared browser for over a minute: "
                "reload the page later (goto) and check whether you are logged in before filling again"
            )
    try:
        vault_unlock()
        try:
            return await _fill_once(tab, selector, pick, field, submit, any_site, frame)
        except Exception as e:
            if "couldn't find entry" not in str(e):
                raise
        _rbw("sync", timeout=60)  # maybe added since the agent's last sync
        try:
            return await _fill_once(tab, selector, pick, field, submit, any_site, frame)
        except Exception as e:
            # A username given as the entry (LD-01): the one entry with that user.
            hit = None if (user or folder or "couldn't find entry" not in str(e)) else _entry_by_user(entry)
            if hit is None:
                raise
        why = _never_fill([hit["name"], hit["folder"]], hit["sites"])
        if why:
            _refuse(why)
        said = await _fill_once(tab, selector, [hit["id"]] if hit["id"] else [hit["name"], hit["user"]], field, submit,
                                any_site, frame)
        return f"(no entry is named {entry!r}; it is the user of the entry {hit['name']!r}, so that one) " + said
    finally:
        if lock:
            # Kept until the next step's fill (username, then password, then
            # code) or 30 s after a submit, while the site logs in.
            _site_login_release(lock, 30 if submit else 120)


async def agent_tab(name, url=None, *, visible=False, timeout=60):
    """Legacy: open tab `name` of omp's Puppeteer browser in the agents'
    persistent Chrome profile, for what only Puppeteer does (its API differs:
    tab.evaluate takes an expression string). Use shop_tab for web tasks.
    Headless unless visible=True, for when Kirill must act (2FA, CAPTCHA).
    Only one Chrome can use the profile: to switch between headless and
    visible, first `await browser.close(all=True, kill=True)`; it can't run
    while the shop browser does (`await shop_close(all=True)` first)."""
    if _shop_pid() or _shop_running():
        raise RuntimeError("the shop browser has the agents' profile: `await shop_close(all=True)` first")
    # No extensions: the Bitwarden one Chrome's policy forces into every
    # profile spins the browser once tab.run attaches, and tab.run then hangs.
    args = [f"--user-data-dir={_AGENT_PROFILE}", "--no-first-run", "--disable-extensions"]
    args.append("--ozone-platform-hint=auto" if visible else "--headless=new")
    app = {"path": _CHROME, "args": args, "relay": False, "tern": False}
    opts = {"name": name, "app": app, "persist": True, "timeout": timeout}
    if url:
        opts["url"] = url
    if visible:
        opts["headed"] = True
    return await browser.open(**opts)  # noqa: F821


