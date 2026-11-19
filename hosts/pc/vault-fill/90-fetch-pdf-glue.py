# Sets a <select> (SEL, or the field choose() found) to the option OPT, found
# by its text (exact, then any case, then its value if no other option has
# it, then a piece of the text only one option has), or to option number IDX:
# by selectedIndex (pages give options empty or equal values: atu.de's are all
# "[object Object]"), with input and change, as a person would. Then reads
# the selected option's text back: {ok: text} or {err: why, with the options}.
_SELECT_JS = r"""
  const find = () => SEL === null ? window[Symbol.for("vf-field")] : VF.find({ css: SEL }).el ?? VF.all(SEL)[0];
  const el = find();
  if (!el) return { err: "no element" };
  if (el.localName !== "select") return { err: `it is a <${el.localName}>, not a <select>: use choose()` };
  const opts = [...el.options], txt = (o) => VF.norm(o.text || o.label || "");
  const names = () => `it has ${opts.length} option(s): ` + opts.slice(0, 15).map((o) => JSON.stringify(txt(o))).join(", ")
    + (opts.length > 15 ? ", …" : "");
  let o = null;
  if (IDX !== null) {
    o = opts[IDX] ?? null;
    if (!o) return { err: `no option index ${IDX}; ${names()}` };
  } else {
    const want = VF.norm(String(OPT)), low = want.toLowerCase();
    const only = (hits) => (hits.length === 1 ? hits[0] : null);
    o = opts.find((o) => txt(o) === want) ?? opts.find((o) => txt(o).toLowerCase() === low)
      ?? only(opts.filter((o) => o.value === String(OPT) && o.value !== ""));
    if (!o) {
      const part = opts.filter((o) => txt(o).toLowerCase().includes(low));
      if (part.length > 1) return { err: `${JSON.stringify(want)} fits ${part.length} options: `
        + part.slice(0, 15).map((o) => JSON.stringify(txt(o))).join(", ") + "; pass the whole text or index=" };
      o = part[0] ?? null;
    }
    if (!o) return { err: `no option ${JSON.stringify(want)}; ${names()}` };
  }
  const i = opts.indexOf(o), label = txt(o);
  if (el.disabled || o.disabled) return { err: `${JSON.stringify(label)} is disabled` };
  el.focus?.();
  Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "selectedIndex").set.call(el, i);
  o.selected = true;
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
  // The page's handlers (a controlled React select) may set it back.
  return new Promise((done) => setTimeout(() => {
    const now = el.isConnected ? el : find();
    const shows = now && now.selectedIndex >= 0 ? txt(now.options[now.selectedIndex]) : null;
    done(shows === label ? { ok: label, index: i }
      : { err: `set ${JSON.stringify(label)}, but the field shows ${JSON.stringify(shows)}; ${names()}` });
  }, 150));"""


# Texts that say a session ended; logged_out() adds the site note's own.
_LOGGED_OUT = [
    "sitzung beendet", "sitzung ist abgelaufen", "sitzung abgelaufen", "sie wurden abgemeldet",
    "sie wurden automatisch abgemeldet", "bitte melden sie sich erneut an", "bitte erneut anmelden",
    "tut uns leid", "session expired", "session has expired", "your session has ended",
    "you have been logged out", "you have been signed out", "please sign in again", "please log in again",
]


_ROUTE_RE = _re.compile(r"(?:(\d+)\s*(?:h|Std\.?)\s*)?(?:(\d+)\s*(?:min|Min\.?))?\s*\n\s*([\d.,]+)\s*(km|m)\s*\n\s*(?:über|via)\s+([^\n]+)")
# B-67 (LIFE-0002): Maps asks "Meinten Sie:" when a place it was given is
# ambiguous ("RoMed Klinik Prien, Harrasser Str. 61"); its suggestions follow,
# each a name line and an address line between icon glyphs.
_DIDYOUMEAN_RE = _re.compile(r"^(?:Meinten Sie|Did you mean)\s*:?\s*$", _re.M)
_GLYPH_LINE = _re.compile(r"^[\ue000-\uf8ff\s]*$")


def _maps_suggestions(text):
    """[(name, address)] Maps lists under its "Meinten Sie:" heading, in
    order; [] when it shows none."""
    m = _DIDYOUMEAN_RE.search(text)
    if not m:
        return []
    out, cur = [], []
    for line in text[m.end():].splitlines()[:40]:
        line = line.strip()
        if _GLYPH_LINE.match(line):
            if cur:
                out.append(cur)
                cur = []
            continue
        if line in ("Gespeichert", "Zuletzt verwendet", "Saved", "Recent") or len(cur) == 2:
            break
        cur.append(line)
    if cur:
        out.append(cur)
    return [(g[0], g[1] if len(g) > 1 else "") for g in out]


async def drive_time(origin, dest, *, mode="driving", timeout=40):
    """Travel time between two places from Google Maps directions in a shop
    tab of its own (never a public routing or geocoding API: those are
    third-party services). `mode`: driving, walking, bicycling or transit.
    Returns {minutes, km, route ("über …"), alternatives: [{minutes, km,
    route}], url}; the tab is closed. When Maps asks "Meinten Sie:" (an
    ambiguous place), it takes the first suggestion, says which, and the
    result gets "picked": [those suggestions]. Raises when Maps shows no
    route."""
    if mode not in ("driving", "walking", "bicycling", "transit"):
        raise ValueError("mode: driving, walking, bicycling or transit")
    url = "https://www.google.com/maps/dir/?api=1&" + _urlparse.urlencode(
        {"origin": origin, "destination": dest, "travelmode": mode})
    # No google.com site note printout (7.7 KB) for a drive time; a later goto still prints it.
    fresh = "google.com" not in _SITES_SEEN
    _SITES_SEEN.add("google.com")
    try:
        tab = await shop_tab(url, timeout=timeout)
    except BaseException:
        if fresh:
            _SITES_SEEN.discard("google.com")
        raise
    try:
        # Maps rewrites the URL to /maps/dir/<from>/<to>/… once it has the places
        # (after Google's consent page, which is rejected by itself).
        await tab.wait_for(url="google.com/maps/dir/", timeout=timeout)
        deadline, routes, text, picked = _time.monotonic() + timeout, [], "", []
        while _time.monotonic() < deadline:
            text = await tab.text(frames=False, _hints=False)
            routes = []
            for h, m, dist, unit, via in _ROUTE_RE.findall(text):
                if not (h or m):
                    continue
                km = float(dist.replace(".", "").replace(",", ".")) / (1000 if unit == "m" else 1)
                routes.append({"minutes": int(h or 0) * 60 + int(m or 0), "km": round(km, 1), "route": "über " + via.strip()})
            if routes:
                break
            sugg = _maps_suggestions(text)
            if sugg and len(picked) < 2:  # one per place at most (origin, destination)
                name, addr = sugg[0]
                try:
                    await tab.click(text=name, exact=True, timeout=10)
                except RuntimeError:
                    pass  # redrawn meanwhile: read again
                else:
                    picked.append(f"{name}, {addr}".rstrip(", "))
                    others = "; ".join(f"{n}, {a}".rstrip(", ") for n, a in sugg[1:3])
                    print(f"[drive_time: Maps asked 'Meinten Sie' and showed {len(sugg)} places; took the first:"
                          f" {picked[-1]!r}" + (f" (others: {others})" if others else "")
                          + "; pass that exact address if it's the wrong one]")
                    await _asyncio.sleep(1)
                    continue
            await _asyncio.sleep(0.5)
        if not routes:
            raise RuntimeError(f"Google Maps showed no {mode} route from {origin!r} to {dest!r}: "
                               f"{_re.sub(r'\s+', ' ', text)[:300]!r} ({await tab.url()})")
        return {**routes[0], "alternatives": routes[1:], "url": await tab.url(), **({"picked": picked} if picked else {})}
    finally:
        await tab.close()
        if fresh:
            _SITES_SEEN.discard("google.com")


# B-39: a bug in shop_map's fn (tab.html() before it existed: 18 URLs ran to
# failure in 279 s, then a rerun) fails every URL the same way. These error
# types, raised by the fn's own code, are what such a bug raises.
_FN_BUG_ERRORS = (AttributeError, NameError, TypeError, KeyError)


def _fn_codes(fns):
    """The code objects of the functions `fns` (partials and bound methods
    unwrapped) and of the lambdas/functions nested in them."""
    out, todo = set(), []
    for f in fns:
        f = getattr(f, "func", f)  # functools.partial
        f = getattr(f, "__func__", f)  # bound method
        co = getattr(f, "__code__", None) or getattr(getattr(type(f), "__call__", None), "__code__", None)
        if co is not None:
            todo.append(co)
    while todo:
        co = todo.pop()
        if co not in out:
            out.add(co)
            todo += [c for c in co.co_consts if isinstance(c, type(co))]
    return out


def _fn_bug(e, codes, caller):
    """(type, message, file, line) of `e` when the caller's fn raised it
    from its own code: an _FN_BUG_ERRORS error whose innermost frame lies
    outside vault-fill (the agent's cell or helper), in one of `codes`, or
    in `caller` (the call fn(tab) itself failed: wrong arguments); None for
    errors from vault-fill's own code (a page the tab methods failed on)."""
    if not isinstance(e, _FN_BUG_ERRORS) or e.__traceback__ is None:
        return None
    tb = e.__traceback__
    while tb.tb_next:
        tb = tb.tb_next
    co = tb.tb_frame.f_code
    if co.co_filename != _fn_bug.__code__.co_filename or co in codes or co is caller:
        return type(e), str(e), co.co_filename, tb.tb_lineno
    return None


async def _map_pick(tab, css_f, want, cap=8.0):
    """shop_map()'s css fields of the page in `tab`; B-71 (SHOP-0308: ALDI's
    city pages rendered their store list after the first read, so every
    address came back empty): while a `want` field is empty, read those
    again every 0.5 s as long as the page changes or has fetch/XHR in
    flight, ≤ `cap` s; 1 s without either ends it."""
    pick = _PICK_JS + "return Object.fromEntries(Object.entries(FIELDS).map(([k, s]) => [k, pick(null, s, k)]));"
    vals = await tab._guard(_vf(pick, FIELDS=css_f), 20)
    miss = {k: css_f[k] for k in want if _empty(vals.get(k))}
    if not miss:
        return vals
    state = f"[(document.body?.innerText ?? '').length, {_NETSTATE_JS}]"
    t0, calm, size = _time.monotonic(), 0.0, None
    while miss and _time.monotonic() - t0 < cap:
        await _asyncio.sleep(0.5)
        try:
            n, net = await tab._ev(state, 5)
            got = await tab._guard(_vf(pick, FIELDS=miss), 20)
        except (RuntimeError, _asyncio.TimeoutError):
            calm = 0.0  # mid-navigation
            continue
        vals.update({k: v for k, v in got.items() if not _empty(v)})
        miss = {k: s for k, s in miss.items() if _empty(vals.get(k))}
        busy = n != size or (net is not None and (net[0] > 0 or net[1] < 1000))
        size, calm = n, 0.0 if busy else calm + 0.5
        if calm >= 1.0:
            break
    return vals


async def shop_map(urls, fn=None, *, fields=None, required=None, concurrency=3, delay=0.5, wait=None, timeout=None):
    """For each URL: open it (goto, with `wait` and `timeout` like goto's) in one of up to
    `concurrency` (≤3) own tabs and return `fn(tab)` (async or not; None:
    tab.text()). Results in URL order; an item is the exception raised for
    that URL. Each failed URL is tried once more at the end, in a fresh tab
    (never one the hard rules blocked); what still failed is printed as
    "[shop_map: N of M failed: <url> (<error>), …]". At most one navigation
    per second per site, `delay` s between a tab's pages; the tabs are
    closed at the end.
    A bug in fn stops the run: when two URLs fail with the same
    AttributeError/NameError/TypeError/KeyError from fn's own code (same
    message and line) before any URL succeeded, the URLs not yet started
    are skipped and that error is raised, naming both URLs.
    fields={name: css|fn}: each item is a dict {url, the fields, missing}
    instead: css read in the page as in collect() (the first visible match's
    text, "css@attr" an attribute), fn(tab) as `fn` (raising a lookup error
    counts as empty). With `fn` too, a dict it returns is merged in (else it
    goes under "result"). "missing": the `required` fields (default: all of
    `fields`; may name keys of fn's dict too) that are empty, counted in
    the printout "[shop_map: N pages; 1 with empty price (<url>)]". B-71: a
    required css field still empty is read again while the page keeps
    changing or loading (≤ 8 s; it stops after 1 s of calm)."""
    urls = [_unmask_url(u) for u in urls]
    css_f, fn_f = _field_specs(fields, ("url", "result"))
    out, todo, tabs = [None] * len(urls), iter(range(len(urls))), []
    codes, finished, bugs, abort = _fn_codes([fn, *fn_f.values()]), [], {}, []

    async def read(i, tab):
        r = fn(tab) if fn else (None if fields or required else tab.text())
        r = await r if _inspect.isawaitable(r) else r
        if not (fields or required):
            return r
        row = {"url": _mask_url(urls[i]), **(r if isinstance(r, dict) else {} if r is None else {"result": r})}
        if css_f:
            req = css_f if required is None else [required] if isinstance(required, str) else required
            want = [k for k in req if k in css_f]
            row.update(await _map_pick(tab, css_f, want))
        for f, g in fn_f.items():
            row[f] = await _field_call(g, tab)
        return row

    async def one(i, tab):
        """(result or exception, the tab to go on with: None for a fresh one)."""
        try:
            if tab is None:
                tab = await shop_tab()
                tabs.append(tab)
            await _rate_wait(urls[i])
            await tab.goto(urls[i], wait=wait, timeout=timeout)
            return await read(i, tab), tab
        except (TabStuck, ConnectionError) as e:
            return e, None  # stuck, or the browser restarted: a fresh tab next
        except Exception as e:
            return e, tab

    async def worker():
        tab = None
        for i in todo:
            if abort:
                return  # a bug in fn: leave the rest (the tabs at work finish their page)
            out[i], tab = await one(i, tab)
            finished.append(i)
            sig = _fn_bug(out[i], codes, read.__code__)
            if sig and not any(not isinstance(out[j], Exception) for j in finished):
                bugs.setdefault(sig, []).append(i)
                if len(bugs[sig]) >= 2 and not abort:
                    abort.append(sig)
            if not abort:
                await _asyncio.sleep(delay)

    try:
        await _asyncio.gather(*(worker() for _ in range(max(1, min(int(concurrency), 3, len(urls))))))
        tab = None
        for i, r in enumerate(out):
            if not abort and isinstance(r, Exception) and not isinstance(r, HardRule):
                out[i], tab = await one(i, tab)
    finally:
        for t in tabs:
            try:
                await t.close()
            except Exception:
                pass
    if abort:
        a, b = bugs[abort[0]][:2]
        e = out[a]
        msg = (f"shop_map: fn raised the same {type(e).__name__} on {_mask_url(urls[a])} and {_mask_url(urls[b])}"
               f" before any URL succeeded: a bug in fn; the other {len(urls) - len(finished)} URLs were skipped."
               " Fix fn, try it on one URL, then rerun")
        print(f"[{msg}]")
        e.add_note(msg)
        raise e
    failed = [(u, r) for u, r in zip(urls, out) if isinstance(r, Exception)]
    if failed:
        print(f"[shop_map: {len(failed)} of {len(urls)} failed: "
              + ", ".join(f"{_mask_url(u)} ({type(r).__name__}: {str(r)[:100]})" for u, r in failed) + "]")
    if fields or required:
        print(f"[shop_map: {len(urls) - len(failed)} pages; "
              + _mark_missing(out, fields, required, lambda ix: ", ".join(_mask_url(urls[i])[:100] for i in ix)) + "]")
    return out


async def _run(args, timeout):
    p = await _asyncio.create_subprocess_exec(
        *args, stdin=_asyncio.subprocess.DEVNULL, stdout=_asyncio.subprocess.PIPE, stderr=_asyncio.subprocess.PIPE)
    try:
        out, err = await _asyncio.wait_for(p.communicate(), timeout)
    except _asyncio.TimeoutError:
        p.kill()
        raise TimeoutError(f"{_os.path.basename(args[0])} took over {timeout} s") from None
    if p.returncode != 0:
        raise RuntimeError(f"{_os.path.basename(args[0])}: {err.decode(errors='replace').strip()[:300]}")
    return out


def _ascii_url(url):
    """`url` with non-ASCII characters percent-encoded (UTF-8) and an IDN
    host in punycode ("…/suche?ort=München" → "…ort=M%C3%BCnchen")."""
    if url.isascii():
        return url
    u = _urlparse.urlsplit(url)
    host = u.hostname.encode("idna").decode() if u.hostname and not u.hostname.isascii() else u.hostname or ""
    netloc = host + (f":{u.port}" if u.port else "")
    if u.username:
        netloc = f"{u.username}{':' + u.password if u.password else ''}@{netloc}"
    q = lambda s: _urlparse.quote(s, safe="/?#[]@!$&'()*+,;=:%~")  # noqa: E731
    return _urlparse.urlunsplit((u.scheme, netloc, q(u.path), q(u.query), q(u.fragment)))


def _decode_body(body, encoding="", ctype=""):
    """`body` without its Content-Encoding (gzip, deflate, zstd; several
    in a row are undone last first). Without that header, a gzip body of a
    text type (some servers send one unasked: protonvpn.com) is recognized
    by its magic bytes; a gzip file (application/gzip, octet-stream) stays
    as it is. Raises on brotli: this Python has no decoder for it."""
    import zlib
    codings = [c.strip().lower() for c in (encoding or "").split(",") if c.strip() and c.strip().lower() != "identity"]
    if not codings and body[:2] == b"\x1f\x8b" and not _re.search(r"gzip|octet-stream|zip|tar|binary", ctype or "", _re.I):
        codings = ["gzip"]
    for c in reversed(codings):
        if c in ("gzip", "x-gzip"):
            body = zlib.decompress(body, 16 + zlib.MAX_WBITS)
        elif c == "deflate":
            try:
                body = zlib.decompress(body)  # zlib-wrapped, as the standard says
            except zlib.error:
                body = zlib.decompress(body, -zlib.MAX_WBITS)  # raw deflate, as some servers send it
        elif c == "zstd":
            from compression import zstd
            body = zstd.decompress(body)
        else:
            raise RuntimeError(f"the answer is {c}-compressed (Content-Encoding), which this Python can't decode")
    return body


def _http_get(url, headers=None, *, data=None, timeout=20):
    """(body, response headers, final URL) of a plain HTTP request that looks
    like a browser's. The body comes decompressed (_decode_body)."""
    h = {"User-Agent": _UA, "Accept-Language": "de-DE,de;q=0.9,en;q=0.8", **(headers or {})}
    req = _urlrequest.Request(_ascii_url(url), data=data, headers=h)
    try:
        with _urlrequest.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return _decode_body(body, r.headers.get("Content-Encoding"), r.headers.get("Content-Type")), r.headers, r.geturl()
    except _urlerror.HTTPError as e:
        raise RuntimeError(f"{_mask_url(url)}: HTTP {e.code} {e.reason}") from None


def _IMAGE_MAGIC(head):
    """The image type of these first bytes (png, jpg, tif, gif, webp), or None."""
    head = bytes(head[:16])
    if head.startswith(b"\x89PNG"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tif"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


async def pdf_text(src, tab=None, *, password=None, ocr=None, lang="deu+eng", timeout=60):
    """The text of a PDF: a local path, or a URL fetched with `tab`'s session
    (cookies, Referer; any site) or, without a tab, plainly with a browser's
    User-Agent; a JPEG/PNG/TIFF/GIF/WebP image goes straight to OCR. The
    downloaded file is deleted afterwards. Pages without a text layer (scans) are read by OCR
    (tesseract, `lang`); ocr=True forces it, ocr=False skips it. Several
    pages come as "[page N]" parts; OCR text is marked "[OCR]". Temporary
    files are deleted. `password`: the PDF's open password (a bank
    statement's birth date). Poppler takes it only on its command line
    (-upw), so it is visible in /proc to this user's processes for the
    second each poppler run lasts; nothing else keeps it."""
    src = _unmask_url(src)
    pw = ["-upw", str(password)] if password else []
    tmp = _tempfile.mkdtemp(prefix="vf-pdf-")  # mode 700
    try:
        path = _os.path.expanduser(str(src))
        if not _os.path.isfile(path):
            if tab is not None:
                data = await tab._download(src, timeout)
            else:
                await _rate_wait(src)
                u = _urlparse.urlsplit(src)
                data = (await _asyncio.to_thread(
                    _http_get, src, {"Accept": "application/pdf,*/*", "Referer": f"{u.scheme}://{u.netloc}/"},
                    timeout=timeout))[0]
            img = _IMAGE_MAGIC(data)
            if not img and not data.lstrip()[:5].startswith(b"%PDF"):
                kind = "an HTML page (a login or error page?)" if data.lstrip()[:1] == b"<" else f"{len(data)} bytes of something else"
                hint = ": retry with tab= (the site needs your session or its referrer)" if tab is None and kind.startswith("an HTML") else ""
                raise RuntimeError(f"{_mask_url(src)} gave no PDF but {kind}{hint}")
            path = _os.path.join(tmp, "doc." + (img or "pdf"))
            with open(_os.open(path, _os.O_WRONLY | _os.O_CREAT | _os.O_EXCL, 0o600), "wb") as f:
                f.write(data)
        else:
            with open(path, "rb") as f:
                img = _IMAGE_MAGIC(f.read(16))
        if img:  # a photo or scan as an image file (VE-10): straight to OCR
            t = (await _run([_TESSERACT, path, "-", "-l", lang], timeout)).decode(errors="replace").strip()
            return _mask_secrets(_mask_url("[OCR]\n" + t if t else "[OCR: no text found in the image]"))
        try:
            pages = (await _run([_PDFTOTEXT, "-layout", "-enc", "UTF-8", *pw, path, "-"], timeout)).decode(errors="replace").split("\f")
        except RuntimeError as e:
            if "password" in str(e).lower():
                raise RuntimeError(f"{_mask_url(src)} is password-protected" + (": that password is wrong" if pw else
                                   ": pdf_text(src, password=…)")) from None
            raise
        if len(pages) > 1 and not pages[-1].strip():
            pages.pop()
        # A scan: a page with (almost) no text, or little text beside an image.
        imaged = set()
        if ocr is None and any(len(p.strip()) < 300 for p in pages):
            listing = (await _run([_PDFIMAGES, "-list", *pw, path], timeout)).decode(errors="replace")
            imaged = {int(line.split()[0]) for line in listing.splitlines()[2:] if line.split()[:1] != [] and line.split()[0].isdigit()}
        for i, p in enumerate(pages):
            n = len(p.strip())
            if ocr is False or (not ocr and n >= 10 and not (n < 300 and i + 1 in imaged)):
                continue
            img = _os.path.join(tmp, f"p{i + 1}")
            await _run([_PDFTOPPM, "-r", "300", "-gray", "-png", "-singlefile", "-f", str(i + 1), "-l", str(i + 1), *pw, path, img], timeout)
            t = (await _run([_TESSERACT, img + ".png", "-", "-l", lang], timeout)).decode(errors="replace").strip()
            _os.unlink(img + ".png")
            if t and (ocr or n < 10):
                pages[i] = "[OCR]\n" + t
            elif len(t) > 2 * n:
                pages[i] = p.rstrip() + "\n[OCR of the page image]\n" + t  # a scan with a stamp or header
        if len(pages) == 1:
            return _mask_secrets(_mask_url(pages[0].strip()))
        return _mask_secrets(_mask_url("\n\n".join(f"[page {i + 1}]\n{p.strip()}" for i, p in enumerate(pages))))
    finally:
        _shutil.rmtree(tmp, ignore_errors=True)


class _HTMLText(_htmlparser.HTMLParser):
    """Text of an HTML page, roughly as a browser lays it out."""

    _SKIP = {"script", "style", "noscript", "template", "head", "svg", "iframe", "object"}
    _BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "header",
              "footer", "nav", "main", "aside", "table", "ul", "ol", "dl", "dt", "dd", "pre", "blockquote", "form",
              "hr", "title", "option", "figcaption"}

    def __init__(self, base=None, links=False):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0
        self.base, self.links, self.a = base, links, None  # a: (href, where its text starts in out)

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self.skip += 1
        elif tag in self._BLOCK:
            self.out.append("\n")
        elif tag in ("td", "th"):
            self.out.append(" ")
        elif tag == "a" and self.links and not self.skip:
            href = dict(attrs).get("href")
            if href and not href.startswith(("javascript:", "#")):
                self.a = (_urlparse.urljoin(self.base or "", href), len(self.out))

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in self._BLOCK:
            self.out.append("\n")
        elif tag == "a" and self.a:
            href, at = self.a
            words = _re.sub(r"\s+", " ", "".join(self.out[at:])).strip()
            self.out[at:] = [f" [{words}]({href}) "]
            self.a = None

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)

    def text(self):
        lines = (_re.sub(r"[ \t\u00a0]+", " ", line).strip() for line in "".join(self.out).split("\n"))
        return "\n".join(line for line in lines if line)


@_both
def fetch_text(url, *, timeout=20, limit=None, links=False):
    """A public page's text, without a browser (a browser's User-Agent; any
    charset); HTML is turned into text, with links=True each link as
    "[text](absolute url)" (a PDF link's target). No JavaScript runs: pages
    that render in the browser need shop_tab. `limit` cuts, and says so.
    Asks caches for a fresh copy (Cache-Control: no-cache; B-84: a CDN
    served waizmanntabelle's homepage of 5 months ago)."""
    url = _unmask_url(url)
    body, headers, final = _http_get(url, {"Cache-Control": "no-cache", "Pragma": "no-cache"}, timeout=timeout)
    ctype = headers.get("Content-Type", "")
    cs = headers.get_content_charset()
    if not cs and "html" in ctype:
        m = _re.search(rb"""<meta[^>]+charset=["']?([\w-]+)""", body[:4096], _re.I)
        cs = m.group(1).decode() if m else None
    try:
        text = body.decode(cs or "utf-8")
    except (LookupError, UnicodeDecodeError):
        text = body.decode("cp1252", errors="replace")
    if "html" in ctype or text.lstrip()[:1] == "<":
        p = _HTMLText(final or url, links)
        p.feed(text)
        text = p.text()
    text = _mask_secrets(_mask_url(text))
    if limit and len(text) > limit:
        text = text[:limit] + f"\n[… truncated, {len(text) - limit} more chars: fetch_text(limit=None)]"
    return text


# B-67 (LIFE-0021): a JSON answer wrapped in a JSONP callback (DWD's
# warnings.json: "warnWetter.loadWarnings({…});"), or behind an anti-XSSI
# prefix (")]}'", "for(;;);", "while(1);").
_XSSI_RE = _re.compile(r"^\s*(?:\)\]\}'?,?|for\s*\(\s*;\s*;\s*\)\s*;|while\s*\(\s*1\s*\)\s*;)\s*")
_JSONP_RE = _re.compile(r"^\s*(?:/\*\*/\s*)?(?:typeof\s+[\w$.]+\s*===?\s*['\"]function['\"]\s*&&\s*)?"
                        r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*|\[['\"][\w$]+['\"]\])*\s*\(([\s\S]*)\)\s*;?\s*$")


def _json_body(body):
    """The parsed JSON of a response body (bytes or str), also inside a
    JSONP callback or after an anti-XSSI prefix; else json's own
    JSONDecodeError, with the body's start (masked) as a note."""
    try:
        return _json.loads(body)
    except ValueError as e:
        first = e
    text = body.decode("utf-8-sig", errors="replace") if isinstance(body, bytes) else body
    bare = _XSSI_RE.sub("", text, count=1)
    m = _JSONP_RE.match(bare)
    for cand in (bare, m.group(1)) if m else (bare,):
        try:
            return _json.loads(cand)
        except ValueError:
            pass
    first.add_note(f"not JSON; the body starts: {_mask_secrets(_mask_json_text(_re.sub(r'\s+', ' ', text[:160])))!r}")
    raise first


@_both
def http_json(url, headers=None, *, data=None, timeout=20, reveal_keys=None):
    """GET (or with `data`, a dict/list sent as JSON, POST) a public JSON
    API without a browser; returns the parsed answer (a JSONP callback
    "cb({…});" or an anti-XSSI prefix ")]}'" is stripped). reveal_keys=["key"]:
    values under those JSON keys stay unmasked (a public config key the next
    request needs; see _mask_reveal)."""
    reveal = _reveal_set(reveal_keys)
    h = {"Accept": "application/json", **(headers or {})}
    if data is not None and not isinstance(data, (bytes, str)):
        data = _json.dumps(data)
        h.setdefault("Content-Type", "application/json")
    url = _unmask_url(url)
    body, _, _ = _http_get(url, h, data=data.encode() if isinstance(data, str) else data, timeout=timeout)
    data = _json_body(body)
    return _reveal_print("http_json", data, reveal) if reveal else _mask_secrets(data)


@_both
def doh(name, type="A"):
    """DNS records of `name` (A, AAAA, MX, TXT, NS, CAA, …) over DNS-over-HTTPS
    (Cloudflare): their data as strings; [] when there are none (NXDOMAIN
    too)."""
    q = _urlparse.urlencode({"name": name, "type": type})
    r = http_json(f"https://cloudflare-dns.com/dns-query?{q}", {"Accept": "application/dns-json"})
    if r.get("Status") not in (0, 3):
        raise RuntimeError(f"DNS {type} {name}: status {r.get('Status')}")
    want = r.get("Question", [{}])[0].get("type")
    return [a["data"] for a in r.get("Answer", []) if want is None or a.get("type") == want]
