    async def text(self, selector=None, limit=None, *, hidden=False, frames=True, dialogs=True, timeout=20, _hints=True):
        """The visible text of the page or of the first visible match of
        `selector` ("main" skips most sites' navigation and footer; a page
        without a non-empty <main> gives the whole page, marked so). Hidden
        parts and field contents are left out; hidden=True adds the text of
        collapsed accordions, closed <details>, hidden tab panels and
        display:none parts in there, each on a line "[hidden] …" (never field
        values). Open dialogs, popovers, drawers and overlays come first as
        [dialog] sections (they are on top), the shown tab panel outside that
        part after it as [tabpanel]. Iframes (frames=True, also cross-site
        ones) come where they sit, between [frame <host>] and [/frame].
        Alerts the page showed come as [alert]. `limit` cuts the main part,
        and says so. While a new document has no body yet, waits for it.
        With fetch/XHR requests in flight it first waits ≤ 1.5 s for them;
        still busy then, a last line says "[loading: N requests in flight,
        page may be incomplete]": before writing "none"/"no X", await
        tab.idle() and read again (long-polls don't count). With a selector, open dialogs outside it
        get a line "[dialog open: '…' — text() shows it]", and a main region
        holding little of the page says so ("[main holds N chars of M …]").
        A count the page states of its list shows on a last line "[page
        count: '345 Treffer' | page 1 of 5]" (count_proof checks a list
        against it). A word the cell's own code looks for (a string in it)
        that this text lacks but the page holds hidden, in an attribute or in
        its JSON/XHR data prints (not in the result, so `"x" in t` stays
        about the text) "[find: 'Homespot' isn't in this text, but the page
        has it 3× (hidden 'Gebuchte Optionen' 2, xhr 1): await
        tab.find('Homespot')]" (once per word and cell; not for UI strings
        in i18n JSON, nor for hidden text the previous page had too). A
        heading whose section is there but shows nothing (a lazy widget)
        gets a last line "[empty: 'Skigebiete in der Nähe' — its section
        shows nothing yet (lazy?): scrolled to it; await tab.idle(), then
        read again]"; collapsed/hidden sections don't count.
        B-71 (whole page or "main"): while the page shows only its
        header/nav/footer (< 300 chars besides, an empty <main> or large
        same-origin iframe), waits ≤ 8 s for its content (once per URL;
        goto() already does); still so, a last line "[text: only header/nav
        so far (N chars of main content): the page may still be rendering:
        …]". B-88: a line "[title: …]" before the page's text (first,
        unless dialogs are open) when document.title has words (4+ letters)
        the text lacks (a grade only the title gives); on such a thin page,
        or one whose content shows a gate (sign in, verify your account,
        upgrade, Standortdienste, consent, "nicht verfügbar"), a line
        "[data: 7 JSON responses since load: service.svc?action=
        GetConsumerMailbox{PopEnabled,ImapEnabled,…} · … — tab.find('<term>')
        reads them]" (≤ 5 bodies, ≤ 8 key paths each, no values): the fact
        may be in data the page already loaded."""
        hint_code = _direct_cell_call(_sys._getframe(0), ShopTab) if _hints else None
        await self._quiet_reads()
        core = await self._core_wait() if _hints and selector in (None, "main") else None
        tag = f"{_os.getpid()}.{_time.monotonic_ns()}" if frames else None
        fr = await self._frame_texts(tag, hidden) if frames else {}
        frlen = sum(len(t) for _, t in fr.values())
        deadline = _time.monotonic() + timeout
        while True:
            r = await self._guard(_vf(_INFLIGHT_JS + _TEXT_JS, SEL=selector, DIALOGS=bool(dialogs), FRAME=tag,
                                      HIDDEN=bool(hidden), EMPTY=bool(_hints)), timeout)
            if not (r and r.get("loading")):
                break
            if _time.monotonic() > deadline:
                return f"[the page at {await self.url()} has no body yet: still loading]"
            await _asyncio.sleep(0.2)
        if r is None:
            return f"[no element matches {selector!r}]"

        def place(s):
            def put(m):
                f = fr.pop(int(m.group(1)), None)  # each frame once
                return f"[frame {f[0]}]\n{place(f[1])}\n[/frame]" if f else ""
            return _FRAME_MARK.sub(put, s)

        main = place(r["text"])
        full = main
        if limit and len(main) > limit:
            main = main[:limit] + f"\n[… truncated, {len(main) - limit} more chars: text(limit=None)]"
        parts = [f"[alert] {a}" for a in self._alerts]
        self._alerts = []
        parts += [n for n in r.get("notes", []) if n.startswith("[dialog open")]
        parts += [place(s) for s in r["before"]]
        at = len(parts)  # B-88's title line: after the alerts and dialogs (they are on top), before the page
        parts += [r["head"] + main] if r["head"] or main else []
        parts += [place(s) for s in r["after"]]
        while fr:  # frames whose iframe the text doesn't show where it sits
            host, t = fr.pop(next(iter(fr)))
            parts.append(f"[frame {host}]\n{place(t)}")
        parts += [n for n in r.get("notes", []) if not n.startswith("[dialog open")]
        if _hints:
            shown = "\n\n".join(p for p in parts if p)
            if core:  # B-71/B-88 lines; the page count stays the last line (B-45)
                title = _title_line(core.get("title"), full + "\n" + shown, core.get("href"))
                if title:
                    parts.insert(at, title)
                thin = core["thin"] and frlen < 300
                n, empty_main, empty_frame = core["core"], core["main"] == 0, core["frame"] == 0
                if thin and (core.get("busy") or empty_main or empty_frame or n == 0 or (core["chrome"] and n < 120)):
                    why = "; its <main> is empty" if empty_main else "; its large frame is empty" if empty_frame else ""
                    lead = "only header/nav so far" if core["chrome"] else "little text so far"
                    parts.append(f"[text: {lead} ({n} chars of main content{why}): the page may still be rendering: "
                                 "await tab.idle() and re-read, or capture its JSON]")
                if thin or core["gate"]:
                    terms = [t.lower() for t in _cell_terms(hint_code)] if hint_code is not None else []
                    try:
                        line = _data_line(await self._ev(_vf(_DATA_JS, TERMS=terms), 10))
                    except (RuntimeError, _asyncio.TimeoutError):
                        line = None
                    if line:
                        parts.append(line)
            cands, pages = _count_candidates(shown)
            says = list(dict.fromkeys(repr(c["evidence"][:80]) for c in cands))[:3]
            if pages:
                says.append(f"page {pages[0]} of {pages[1]}")
            if says:
                parts.append(f"[page count: {' | '.join(says)}]")
            if hint_code is not None:
                for line in await self._find_hints(hint_code, shown):
                    print(line)
        if r.get("inflight"):
            parts.append(f"[loading: {r['inflight']} request(s) in flight, page may be incomplete: await tab.idle(), then read again]")
        return _mask_url("\n\n".join(p for p in parts if p))

    async def _find_hints(self, code, shown):
        """text()'s "[find: …]" lines: the cell's search words (_cell_terms)
        missing from `shown` that the page holds elsewhere (top document).
        B-66: once per word and cell (_HINT_SAID); JSON under translation
        keys left out (I18N); none for a word only hidden text holds that
        the tab's previous page held alike (a "No results" template); the
        alternatives of one "a|b" string share a line."""
        if _HINT_SAID["code"] is not code:
            _HINT_SAID.update(code=code, terms=set())
        flat = _re.sub(r"\s+", " ", shown).lower()
        groups = {}
        terms = [t for t in _cell_terms(code, groups=groups) if _re.sub(r"\s+", " ", t).lower() not in flat
                 and t.lower() not in _HINT_SAID["terms"]]
        if not terms:
            return []
        try:
            r = await self._ev(_vf(_FIND_JS, TERMS=terms, RX=False, MAXH=3, JSONX=True, I18N=True), 10)
        except (RuntimeError, _asyncio.TimeoutError):
            return []
        page = (r.get("href") or "").split("#")[0]
        found = {}  # word → its "kind n" list
        for i, t in enumerate(terms):
            by = {}
            for k, n in r["total"].items():
                ti, kind = k.split(" ", 1)
                if int(ti) == i and kind != "visible":
                    by[kind] = by.get(kind, 0) + n
            hid = frozenset(h["snippet"] for h in r["hits"] if h["term"] == i and h["kind"] == "hidden")
            last = self._hint_hidden.get(t.lower())
            self._hint_hidden[t.lower()] = (page, hid, by.get("hidden", 0))
            if not by or (set(by) == {"hidden"} and last and last[0] != page and last[1:] == (hid, by["hidden"])):
                continue  # nowhere else, or the same hidden text as on the previous page: a template
            lab = next((h.get("label") for h in r["hits"] if h["term"] == i and h["kind"] == "hidden" and h.get("label")), None)
            found[t] = (sum(by.values()), ", ".join(f"{k} {lab!r} {n}" if k == "hidden" and lab else f"{k} {n}" for k, n in by.items()))
        out, done = [], set()
        for t in found:
            if t in done:
                continue
            same = [u for u in found if u not in done and groups.get(u) is not None and groups.get(u) == groups.get(t)] or [t]
            done.update(same)
            _HINT_SAID["terms"].update(u.lower() for u in same)
            if len(same) == 1:
                n, kinds = found[t]
                out.append(f"[find: {t!r} isn't in this text, but the page has it {n}× ({kinds}): await tab.find({t!r})]")
            else:
                each = "; ".join(f"{u!r} {found[u][0]}× ({found[u][1]})" for u in same)
                words = ", ".join(repr(u) for u in same)
                out.append(f"[find: {words} aren't in this text, but the page has them ({each}): await tab.find({words})]")
        return [_mask_secrets(_mask_url(x)) for x in out]

    async def _quiet_reads(self, cap=1.5):
        """Before a read: with fetch/XHR in flight, wait ≤ `cap` s for quiet
        (lists that come by XHR, SE-09); idle at once costs one evaluation."""
        try:
            st = await self._ev(_NETSTATE2_JS, 5)
        except (RuntimeError, _asyncio.TimeoutError):
            return
        if st and st[0]:
            await self.idle(timeout=cap, quiet=0.3)

    async def idle(self, timeout=10, quiet=1.0):
        """Wait until the page has had no fetch/XHR request in flight for
        `quiet` s (long-polls don't count: a request open 10 s without a
        response, and later ones to its endpoint; nor websockets and
        beacons): True, or False after `timeout` s. Use it before concluding
        that something is missing: lists that load by XHR come late (MD-03)."""
        deadline = _time.monotonic() + timeout
        while True:
            try:
                st = await self._ev(_NETSTATE2_JS, 5)
            except TabStuck:
                raise
            except (RuntimeError, _asyncio.TimeoutError):
                st = [1, 0]  # mid-navigation: not idle
            if st is None:
                await self._ev(_NETCOUNT_JS, 5)  # a document from before this tab's setup: count from now
                st = [0, 0]
            if st[0] == 0 and st[1] >= quiet * 1000:
                return True
            if _time.monotonic() > deadline:
                return False
            await _asyncio.sleep(0.2)

    async def _frame_texts(self, tag=None, hidden=False):
        """{n: (host, text)} of the tab's frames with text: in-process frames
        through an isolated world, cross-site ones (their own targets)
        through their own connection; nested ones too. With `tag`, each
        frame's iframe element gets data-vf-frame="<tag>:<n>" before any
        text is read, so that the texts mark where it sits. hidden: as text()'s."""
        expr = _vf("return innerWidth * innerHeight < 2500 ? null : VF.visibleText(document.body, null, FT, HID);",
                   FT=tag, HID=bool(hidden))
        try:
            iframes = [t for t in _shop_json("/json/list") if t.get("type") == "iframe"]
        except (OSError, ValueError):
            iframes = []
        oopif = {t["id"] for t in iframes}
        out, count = {}, [0]
        soft = (RuntimeError, ConnectionError, _asyncio.TimeoutError, KeyError)

        async def mark(cdp, frame_id):
            count[0] += 1
            n = count[0]
            if tag is None:
                return n
            try:
                own = await cdp.send("DOM.getFrameOwner", {"frameId": frame_id}, 5)
                obj = (await cdp.send("DOM.resolveNode", {"backendNodeId": own["backendNodeId"]}, 5))["object"]
                await cdp.send("Runtime.callFunctionOn", {
                    "objectId": obj["objectId"], "arguments": [{"value": f"{tag}:{n}"}],
                    "functionDeclaration": "function (v) { this.setAttribute('data-vf-frame', v); }"}, 5)
                await cdp.send("Runtime.releaseObject", {"objectId": obj["objectId"]}, 5)
            except soft:
                pass  # gone meanwhile: its text comes at the end
            return n

        async def visit(cdp, tid, depth):
            try:
                tree = await cdp.send("Page.getFrameTree", timeout=5)
            except (RuntimeError, ConnectionError, _asyncio.TimeoutError):
                tree = None
            local = []

            def walk(node):
                for ch in node.get("childFrames", []):
                    if ch["frame"]["id"] not in oopif:
                        local.append(ch["frame"])
                    walk(ch)

            if tree:
                walk(tree["frameTree"])
            local = [fr for fr in local[:20] if fr.get("url", "").startswith("http")]
            subs = [t for t in iframes if t.get("parentId") == tid and depth <= 3 and t.get("webSocketDebuggerUrl")]
            nums = {fr["id"]: await mark(cdp, fr["id"]) for fr in local}
            nums.update({t["id"]: await mark(cdp, t["id"]) for t in subs})
            for fr in local:
                try:
                    w = await cdp.send("Page.createIsolatedWorld", {"frameId": fr["id"], "worldName": "vf-text"}, 5)
                    t = await cdp.evaluate(expr, 5, context=w["executionContextId"])
                except soft:
                    continue
                if t:
                    out[nums[fr["id"]]] = (_urlparse.urlsplit(fr["url"]).hostname, t)
            for t in subs:
                try:
                    sub = await _asyncio.wait_for(_CDP.connect(t["webSocketDebuggerUrl"]), 5)
                except (OSError, ConnectionError, _asyncio.TimeoutError):
                    continue
                try:
                    await visit(sub, t["id"], depth + 1)  # tags its iframes before its text is read
                    text = await sub.evaluate(expr, 5)
                    if text:
                        out[nums[t["id"]]] = (_urlparse.urlsplit(t["url"]).hostname, text)
                except (RuntimeError, ConnectionError, _asyncio.TimeoutError):
                    pass
                finally:
                    sub.close()

        await visit(self._cdp, self.id, 0)
        return dict(sorted(out.items()))

    async def wait_for(self, text=None, *, selector=None, url=None, gone=None, frame=None, timeout=None, strict=False,
                       _errbase=None, _quiet=None):
        """Wait until the page shows `text` (a list/tuple: any of them), has
        a visible `selector`, its URL matches `url`, and/or `gone` text (a
        list/tuple: all of them) has disappeared; returns the text that matched (None without `text`).
        Text matches ignore case and count only visible text, that of
        iframes too (also cross-site ones: payments.google.com), and the
        options of shown <select>s, dropdowns and overlay lists (a printed
        line says when the match was only there).
        `url`: a substring of the URL without its query and fragment (with
        them when it contains "?" or "#"), else (when it has regex
        characters) a regex for re.search on the whole URL, or a compiled
        re.Pattern: r"^https://www\\.paypal\\.com/myaccount"; a list/tuple
        of them: any of them.
        `selector` also counts in the tab's frames when the page has none;
        `frame` (a host, a piece of its URL, or the <iframe>'s CSS selector)
        looks for it in that frame only. TimeoutError after `timeout` s
        (20), with the URL, the open dialog's text, the top of the page's
        text and the page's line closest to `text`; a text-only wait without
        timeout= ends after ≥ 5 s already once the page has loaded and been
        quiet for 3 s (no fetch/XHR in flight, no element or text changing,
        frames too) without the text (a guessed wording: see "closest");
        timeout=N waits the full N s; one over 60 s (B-79) still ends once
        the page has been loaded and quiet for 20 s without the text (a
        finished scan whose wording differs), unless strict=True. After 8 s
        already while the page is a step in between that `url` doesn't name
        (a login, 2FA or "Konto schützen" page, a captcha), naming it.
        SiteError (a
        TimeoutError) within ~2 s when a site error text shows that wasn't
        on the page when the wait began ("Das hat leider nicht geklappt",
        "Tut uns Leid", "Ein Fehler ist aufgetreten", "Hoppla", "502 Bad
        Gateway", …): a retry costs seconds, not the whole timeout."""
        quiet = (timeout is None) if _quiet is None else _quiet
        timeout = 20 if timeout is None else timeout
        if text is None and selector is None and url is None and gone is None:
            raise ValueError("wait_for needs text, selector, url or gone")
        texts = None if text is None else [text] if isinstance(text, str) else list(text)
        if texts is not None and (not texts or not all(isinstance(t, str) for t in texts)):
            raise ValueError("wait_for text: a string, or a list/tuple of strings")
        low = None if texts is None else [t.lower() for t in texts]
        gones = None if gone is None else [gone] if isinstance(gone, str) else list(gone)
        if gones is not None and (not gones or not all(isinstance(g, str) for g in gones)):
            raise ValueError("wait_for gone: a string, or a list/tuple of strings (all of them must be gone)")
        gones = None if gones is None else [g.lower() for g in gones]
        urls = None if url is None else list(url) if isinstance(url, (list, tuple)) else [url]
        if urls is not None and (not urls or not all(isinstance(u, (str, _re.Pattern)) for u in urls)):
            raise ValueError("wait_for url: a string or re.Pattern, or a list/tuple of them (any of them matches)")

        def url_ok(href):
            return urls is not None and any(_url_match(u, href) for u in urls)

        # B-61: a text-only wait on a loaded page that stays quiet ends early (not a selector, url or gone wait):
        # after 3 s without timeout=; B-79: after 20 s with one over 60 s (wait_for's or goto's), unless strict.
        quiet_js = getattr(self, "_QUIET_JS", None)  # part 70's DOM/network quiet probe
        calm_s = None
        if quiet_js is not None and texts is not None and selector is None and url is None and gone is None:
            calm_s = 3 if quiet else 20 if timeout > 60 and not strict else None
        def poll(chk):
            return _vf(
                _OPTION_TEXT_JS +
                "const raw = TEXTS === null && GONE === null && !CHK ? '' : VF.visibleText(document.body), t = raw.toLowerCase();"
                "let hit = TEXTS === null ? -1 : TEXTS.findIndex((x) => t.includes(x)), opt = false;"
                "if (TEXTS !== null && hit < 0) { const o = OPTS().toLowerCase(); hit = TEXTS.findIndex((x) => o.includes(x)); opt = hit >= 0; }"
                "const hs = (css, n) => VF.all(css).filter(VF.shown).slice(0, n).map((e) => VF.norm(e.innerText)).filter(Boolean);"
                "const head = [document.title, ...hs('h1', 2)].filter(Boolean).join(' | ').slice(0, 300);"
                "const more = hs('h2, [role=heading], legend', 6).join(' | ').slice(0, 300);"
                "let err = null;"
                "if (CHK) { const rx = new RegExp(ERR, 'i');"
                " err = [document.title, ...raw.split('\\n')].filter((l) => l && l.length <= 200 && rx.test(l)).slice(0, 3); }"
                "let n = null; if (CHK) { n = raw.length; for (let i = 0; i < raw.length; i++) n = (n * 31 + raw.charCodeAt(i)) | 0; }"
                "return { href: location.href, sel: SEL === null || !!VF.find({ css: SEL }).el, head, more, opt, err,"
                " text: hit, gone: GONE === null || !GONE.some((x) => t.includes(x)), n };",
                TEXTS=low, SEL=None if frame is not None else selector, GONE=gones, CHK=chk, ERR=_SITE_ERROR_RX,
            )
        expr, expr_chk = poll(False), poll(True)
        errbase = None if _errbase is None else set(_errbase)  # error lines on the page when the wait began
        pending, last_chk = {}, -1.0  # new error line → when first seen
        in_frame = _vf("return !!VF.find({ css: SEL }).el;", SEL=selector)
        what = ", ".join(f"{k}={v!r}" for k, v in
                         {"text": text, "selector": selector, "url": url, "gone": gone, "frame": frame}.items()
                         if v is not None)
        fs = self._frames()
        inter = None  # (href, why, since) of a step in between the page shows
        sig = {}  # B-61: "text"/"frames" → (its hash/text, since when unchanged)
        try:
            t0 = deadline = _time.monotonic()
            deadline += timeout
            while _time.monotonic() < deadline:
                chk = _time.monotonic() - last_chk >= 0.9
                if chk:
                    last_chk = _time.monotonic()
                try:
                    r = await self._ev(expr_chk if chk else expr, timeout=5)
                except TabStuck:
                    raise
                except (RuntimeError, _asyncio.TimeoutError):
                    r = None  # mid-navigation
                await self._rules(t0, r["href"] if r else "")
                if r and selector is not None and (frame is not None or not r["sel"]):
                    try:
                        r["sel"] = (await _frame_search(fs, in_frame, bool, await self._frame_docs(fs, frame)))[0] is not None
                    except _NoFrame:
                        r["sel"] = False  # not there yet
                if r and r["sel"] and (url is None or url_ok(r["href"])):
                    hit, ok_gone, opt = r["text"], r["gone"], r["opt"]
                    if (low is not None and hit < 0) or (gone is not None and ok_gone):
                        frames = "\n".join(t for _, t in (await self._frame_texts()).values()).lower()
                        if calm_s and sig.get("frames", (None,))[0] != frames:
                            sig["frames"] = (frames, _time.monotonic())
                        if low is not None and hit < 0:
                            hit = next((i for i, x in enumerate(low) if x in frames), -1)
                        ok_gone = ok_gone and (gones is None or not any(g in frames for g in gones))
                    if (low is None or hit >= 0) and ok_gone:
                        await self._after_load()
                        if opt:
                            print(f"[wait_for: {texts[hit]!r} shows in an option list or dropdown, not in the page's text]")
                        return None if texts is None else texts[hit]
                if r and r.get("err") is not None:
                    if errbase is None:
                        errbase = set(r["err"])
                    else:
                        now = _time.monotonic()
                        for line in r["err"]:
                            if line in errbase:
                                continue
                            if now - pending.setdefault(line, now) >= 0.8:
                                raise SiteError(
                                    f"wait_for stopped after {now - t0:.1f} of {timeout} s: the page shows a site error "
                                    f"that wasn't there when the wait began: {_mask_url(line)!r}{await self._where()}; "
                                    f"not {what}. Do the task's other reads first, then retry once (goto/click again); "
                                    "if it stays, report it with the times", _mask_url(r["href"]))
                        for line in [k for k in pending if k not in r["err"]]:
                            del pending[line]
                if r:
                    href = r["href"].split("#")[0]
                    why = None if url_ok(r["href"]) else _interstitial(href, r["head"], r["more"])
                    now = _time.monotonic()
                    if not why:
                        inter = None
                    elif inter is None or inter[:2] != (href, why):
                        inter = (href, why, now)
                    elif now - inter[2] >= _INTERSTITIAL_DWELL:
                        raise TimeoutError(
                            f"wait_for gave up after {now - t0:.0f} of {timeout} s: the page looks like {why} and stayed so "
                            f"{_INTERSTITIAL_DWELL:g} s without {what}{await self._where()}{await self._shows()}"
                            ": handle that step (the site note may say how), then wait again")
                if calm_s and r and chk:
                    now = _time.monotonic()
                    if sig.get("text", (None,))[0] != r.get("n"):
                        sig["text"] = (r.get("n"), now)
                    try:
                        q = await self._ev(quiet_js, 3)  # its first call starts the element observer
                    except (RuntimeError, _asyncio.TimeoutError):
                        q = None
                    calm = min(now - s for _, s in sig.values())
                    if (now - t0 >= max(5, calm_s) and q and q[3] == "complete" and q[0] >= calm_s * 1000 and q[1] == 0
                            and q[2] >= calm_s * 1000 and calm >= calm_s and inter is None and not pending):
                        given = (f"after {now - t0:.1f} s (no timeout= given)" if calm_s == 3
                                 else f"after {now - t0:.0f} of {timeout} s")
                        raise TimeoutError(
                            f"wait_for gave up {given}: the page has loaded and been "
                            f"quiet for {calm_s} s (no request in flight, nothing changing) without {what}{self._searched(fs)}"
                            f"{await self._where()}{await self._shows()}{await self._closest(texts)}: the wording may "
                            "differ (closest), the text may sit in a hidden part (tab.find(…)) or need a click first; "
                            + ("timeout=N waits the full N s" if calm_s == 3 else f"strict=True waits the full {timeout} s"))
                await _asyncio.sleep(0.25)
            searched = self._searched(fs)
        finally:
            await fs.close()
        raise TimeoutError(f"waited {timeout} s for {what}{searched}{await self._where()}{await self._shows()}"
                           f"{await self._closest(texts)}")

    async def _closest(self, texts):
        """For wait_for's TimeoutError: "; closest to 'x': 'line'" per text
        (the page's visible text and option lists), or "; nothing close"."""
        if not texts:
            return ""
        try:
            page = await self._ev(_vf(_OPTION_TEXT_JS + "return VF.visibleText(document.body) + '\\n' + OPTS();"), 5)
        except (RuntimeError, _asyncio.TimeoutError):
            return ""
        out = []
        for t in texts[:5]:
            score, line = _closest_line(t, page or "")
            out.append(f"closest to {t!r}: {line[:120]!r}" if score >= 0.45 else f"nothing close to {t!r} on the page")
        return _mask_url("; " + "; ".join(out))

    async def _shows(self, n=400):
        """For errors: "; a dialog is open: …; the page shows: …" (the open
        dialogs' text, then ~n chars from the top of the main content)."""
        try:
            r = await self._ev(_vf(_TEXT_JS, SEL="main", DIALOGS=True, FRAME=None, HIDDEN=False), 5)
        except (RuntimeError, _asyncio.TimeoutError):
            return ""
        if not r or r.get("loading"):
            return "; the page has no body yet"
        flat = lambda s: _re.sub(r"\s+", " ", s).strip()  # noqa: E731
        out = ""
        if r["before"]:
            out += "; a dialog is open: " + repr(flat(" | ".join(r["before"]))[:300])
        main = flat(r["text"])
        out += "; the page shows: " + repr(main[:n] + ("…" if len(main) > n else ""))
        return _mask_url(out)

    async def observe(self, filter=None, limit=80, *, frame=None, timeout=20, _fields=False):
        """The visible controls and fields (open shadow roots and custom
        elements included), numbered: ref, tag, role, text, a ready-made
        `selector` (#id, else [name=…], else [data-vf-ref=…]), and when they
        apply id, name, placeholder, disabled, checked, href, and for an
        unlabeled checkbox, switch or field `card`, the text around it.
        Split one-character code boxes (≥ 4) are one item: role "code",
        boxes: N, a selector matching all of them (for type_code). Icon-only
        buttons show as "close (icon)" / "<icon name> (icon)". No field
        values. `filter` keeps those whose text, id, name, placeholder,
        aria-label, tag, role or card contains it (case-insensitive; "a|b"
        for either); None or "" keeps all. Controls in open dialogs and
        overlays come first; those in closed or hidden dialogs are left out.
        A control inside another one with the same text (a custom button and
        its inner button) is listed once, as the inner one.

        Refs last until navigation: a later observe() keeps the numbers it
        gave before. When the page redraws a list after observe() (an
        autocomplete), click/type/set_value(ref=N) find the element again by
        its selector, or by its tag, text and href if exactly one matches.

        Then the controls of the frames on show (in or near the viewport,
        at least 50×20 px, no ad frames), each with `frame`: its host, up to
        `limit` per frame; `frame=` (a host, a piece of its URL, or the
        <iframe>'s CSS selector) lists only that frame's. Refs are unique in
        the tab; click/type/set_value/select(ref=N) act in the ref's frame,
        and a frame's refs end when that frame loads another page."""
        js = r"""
          const DOC = Symbol.for("vf-doc"), RK = Symbol.for("vf-refs");
          window[DOC] ??= Math.random().toString(36).slice(2);
          const shut = (e) => {
            for (let n = e, i = 0; n && i < 40; n = VF.up(n), i++) {
              if (n.inert) return true;
              const dlg = n.localName === "dialog" || /^(dialog|alertdialog)$/.test(VF.attr(n, "role")) || n.hasAttribute?.("aria-modal");
              if (dlg && ((n.localName === "dialog" && !n.open) || VF.attr(n, "aria-hidden") === "true")) return true;
            }
            return false;
          };
          const dialogs = VF.layers();  // dialogs, popovers, drawers, overlays
          const hits = [], byEl = new Map(), boxed = new Set();
          const one = (x) => x.localName === "input" && x.maxLength === 1 && !/^(checkbox|radio|hidden|submit|button|reset|file)$/.test(x.type);
          // fields(): what a field shows, never a secret: password and vault-filled
          // fields "•••", card numbers their last 4, CVVs and codes not at all.
          const CARD = /card.?(num|no\b)|cardnumber|ccnum|kreditkarte|karten.?(nummer|nr)|card number/i;
          const CVV = /cvv|cvc|csc|security.?code|sicherheitscode|prüf(ziffer|nummer)|pruef(ziffer|nummer)|kartenprüf/i;
          // fields() (B-34): custom selects (role=combobox outside an input, s-select, mat-select, MUI,
          // a listbox button) are rows of their own unless they hold a native field; a select or one of
          // those still on its placeholder ("Bitte auswählen", "Please select", "--", "") is unset.
          const klass = (n) => typeof n.className === "string" ? n.className : VF.attr(n, "class");
          const NATIVE = "input:not([type=hidden]), select, textarea";
          // Hosts (through shadow roots) of a shown native field or of any radio/checkbox: not a custom
          // select themselves (HUK's huk-radio-select holds radios; react-select holds its input).
          let fieldHosts = null;
          const hostsOfFields = () => {
            if (fieldHosts) return fieldHosts;
            fieldHosts = new Set();
            for (const f of VF.all(`${NATIVE}, [role=radio], [role=checkbox], [role=switch], [role=radiogroup]`)) {
              if (!(/^(radio|checkbox)$/.test(f.type) || VF.attr(f, "role") || VF.shown(f))) continue;
              for (let n = VF.up(f), i = 0; n && i < 30; n = VF.up(n), i++) fieldHosts.add(n);
            }
            return fieldHosts;
          };
          const custom = (e) => !/^(input|select|textarea|option)$/.test(e.localName) && !e.isContentEditable
            && (VF.attr(e, "role") === "combobox" || (e.localName === "button" && VF.attr(e, "aria-haspopup") === "listbox")
                || /(^|-)(select|dropdown|combobox|combo-box)$/.test(e.localName) || /(^|\s)MuiSelect-select(\s|$)/.test(klass(e)))
            && !hostsOfFields().has(e);
          const shownText = (e) => VF.norm(e.innerText)
            || VF.norm([...(e.shadowRoot?.children ?? [])].map((c) => c.innerText || c.textContent || "").join(" "));
          const customLabel = (e) => VF.norm(VF.attr(e, "aria-labelledby").split(/\s+/).filter(Boolean)
              .map((id) => e.getRootNode().getElementById?.(id)).filter((n) => n && n !== e && !e.contains(n)).map((n) => n.innerText).join(" "))
            || VF.norm(VF.attr(e, "aria-label"))
            || (e.id ? VF.norm(e.getRootNode().querySelector?.(`label[for="${CSS.escape(e.id)}"]`)?.innerText) : "")
            || VF.norm(VF.attr(e, "label"));
          const PLACEHOLDER = (s) => {
            const t = VF.norm(s).replace(/^[\s\-–—.…:*_]+|[\s\-–—.…:!*_]+$/g, "").toLowerCase();
            return t === "" || (/^(bitte\s+)?((\S+\s+){0,2}(aus)?w(ä|ae)hlen|ausw(ä|ae)hlen)(\s+sie)?(\s+\S+){0,3}$/.test(t) && t.split(" ").length <= 6)
              || /^(please\s+)?(select|choose)(\s+\S+){0,3}$/.test(t) || /^(keine auswahl|nicht ausgewählt|none selected|nothing selected|auswahl)$/.test(t);
          };
          // react-select & co: the input stays empty; the choice or the placeholder shows beside it.
          const comboShown = (x) => {
            for (let n = VF.up(x), i = 0; n && n !== document.body && i < 4; n = VF.up(n), i++) {
              if ([...n.querySelectorAll(NATIVE)].filter(VF.shown).length > 1) break;
              if (!/select|[-_](control|container)(\s|$)/i.test(klass(n))) continue;
              const v = [...n.querySelectorAll("[class*=singleValue], [class*=single-value], [class*=multiValue], [class*=multi-value]")].find(VF.shown);
              if (v) return { text: VF.norm(v.innerText), unset: false };
              const p = [...n.querySelectorAll("[class*=placeholder]")].find((k) => VF.shown(k) && !k.contains(x));
              if (p) return { text: VF.norm(p.innerText), unset: true };
            }
            return null;
          };
          const combo = (x) => x.localName === "input" && (VF.attr(x, "role") === "combobox" || VF.attr(x, "aria-autocomplete") === "list");
          const unsetOf = (x) => {
            if (x.localName === "select") {
              if (x.multiple) return false;
              const o = x.selectedOptions[0];
              return !o || ((o.index === 0 || x.value === "") && (o.disabled || o.hidden || PLACEHOLDER(o.text)));
            }
            if (custom(x)) return PLACEHOLDER(shownText(x)) || [...(x.querySelectorAll?.("[class*=placeholder i]") ?? [])].some(VF.shown);
            if (combo(x) && !x.value) return !!comboShown(x)?.unset;
            return false;
          };
          // required: the attribute / aria-required (also on a custom field's host or its hidden native
          // input), Magento/knockout data-validate & required-entry, a label ending in "*", a wrapper of
          // this field alone marked required (Magento's .field.required: its "*" is CSS).
          const REQ_WRAP = /(^|\s)(_?required|is-required|required-entry|field--required|form-group--required|mandatory)(\s|$)/i;
          const requiredOf = (x, e, text) => {
            if (x.required === true || (x.required === undefined && x.hasAttribute?.("required"))
                || VF.attr(x, "aria-required") === "true" || VF.attr(e, "aria-required") === "true") return true;
            if (/(^|\s)required-entry(\s|$)/.test(klass(x)) || /required/i.test(VF.attr(x, "data-validate")) || VF.attr(x, "data-required") === "true") return true;
            if (custom(x) && [...(x.parentElement?.children ?? [])].some((c) => c !== x && c.matches("input[required], select[required]")
                && (VF.attr(c, "aria-hidden") === "true" || !VF.shown(c)))) return true;  // its hidden native twin
            if (/\*\s*$/.test(text)) return true;
            for (let n = VF.up(x), i = 0; n && n !== document.body && i < 3; n = VF.up(n), i++) {
              if (/^(form|fieldset)$/.test(n.localName) || [...n.querySelectorAll(NATIVE)].filter(VF.shown).length > 1) break;
              if (REQ_WRAP.test(klass(n))) return true;
            }
            return false;
          };
          const value = (e, label, boxes) => {
            if (e.localName !== "input" && /^(checkbox|switch|radio)$/.test(VF.attr(e, "role"))) return VF.checked(e);
            if (boxes) return `${boxes.filter((b) => b.value).length} of ${boxes.length} boxes filled`;
            if (e.localName === "select") return [...e.selectedOptions].map((o) => VF.norm(o.text)).join(", ");
            if (custom(e)) return shownText(e).slice(0, 300);
            if (combo(e) && !e.value) { const s = comboShown(e); if (s && !s.unset) return s.text; }
            if (e.localName === "input" && /^(checkbox|radio)$/.test(e.type)) {
              if (e.checked || VF.shown(e)) return e.checked;
              // A hidden, styled box whose state lives on its label (SE-08): aria-checked in or on it, or a "checked" class.
              const lab = e.labels?.[0] ?? e.closest?.("label");
              for (const n of [lab, lab?.parentElement].filter(Boolean)) {
                if (VF.attr(n, "aria-checked") === "true" || (n === lab && n.querySelector("[aria-checked=true]"))) return true;
                const c = typeof n.className === "string" ? n.className : VF.attr(n, "class");
                if (/(^|[\s_-])(is-)?checked([\s_-]|$)/i.test(c)) return true;
              }
              return false;
            }
            if (e.localName === "input" && e.type === "file") return [...(e.files ?? [])].map((f) => f.name).join(", ");
            const v = (/^(input|textarea)$/.test(e.localName) ? e.value : e.innerText) ?? "";
            if (!v) return "";
            if (e.hasAttribute("data-vf-vault")) return "••• (vault)";
            const ac = VF.attr(e, "autocomplete").toLowerCase(), names = `${e.id} ${VF.attr(e, "name")} ${label}`;
            // Masked as the page masks it (type=password, CSS text-security), not by autocomplete:
            // address forms say autocomplete="new-password" to stop autofill (SHOP-0148).
            if (e.type === "password" || !/^(none|)$/.test(getComputedStyle(e).webkitTextSecurity ?? ""))
              return "••• (password)";
            if (/cc-csc/.test(ac) || CVV.test(names)) return "•••";
            if (/cc-/.test(ac) || CARD.test(names)) return "•••• " + v.replace(/\s+/g, "").slice(-4);
            return v.length > 300 ? v.slice(0, 300) + "…" : v;
          };
          // fields(): required, and why a field is invalid (native validity without
          // firing events, aria-invalid, Angular/Bootstrap/Material error classes),
          // with the error text shown by it when there is one.
          const errText = (x) => {
            for (const id of `${VF.attr(x, "aria-errormessage")} ${VF.attr(x, "aria-describedby")}`.split(/\s+/).filter(Boolean)) {
              const m = x.getRootNode?.().getElementById?.(id) ?? document.getElementById(id);
              const t = m && VF.shown(m) ? VF.norm(m.innerText) : "";
              if (t && (id === VF.attr(x, "aria-errormessage") || /error|fehler|invalid|alert/i.test(`${id} ${VF.attr(m, "class")} ${VF.attr(m, "role")}`)))
                return t.slice(0, 150);
            }
            for (let n = VF.up(x), i = 0; n && n !== document.body && i < 3; n = VF.up(n), i++) {
              if (/^(form|fieldset)$/.test(n.localName) || n.querySelectorAll("input:not([type=hidden]), select, textarea").length > 1) break;
              const m = [...n.querySelectorAll("[role=alert], [aria-live=assertive], mat-error, nx-error, [class*=error], [class*=invalid], [class*=fehler]")]
                .find((k) => !k.contains(x) && VF.shown(k) && VF.norm(k.innerText) && VF.norm(k.innerText).length < 200);
              if (m) return VF.norm(m.innerText).slice(0, 150);
            }
            return "";
          };
          const BAD_CLASS = /(^|\s)(ng-invalid|is-invalid|invalid|has-error|error|mage-error|mat-form-field-invalid)(\s|$)|--(error|invalid)(\s|$)/;
          const BAD_WRAP = /(^|\s)(has-error|is-invalid|mat-form-field-invalid|mat-mdc-form-field-invalid)(\s|$)|--(error|invalid|has-error)(\s|$)/;
          // Magento/knockout/jQuery-validate error boxes beside the field, or an aria-invalid twin
          // (the hidden native select behind a custom one), in a wrapper of this field alone.
          const SIB_ERR = ".mage-error, div.field-error, label.error[generated], .invalid-feedback, [class*=error-message], [class*=errorMessage], [class*=error-msg]";
          const sibErr = (x) => {
            for (let n = VF.up(x), i = 0; n && n !== document.body && i < 2; n = VF.up(n), i++) {
              if (/^(form|fieldset)$/.test(n.localName) || [...n.querySelectorAll(NATIVE)].filter(VF.shown).length > 1) break;
              const m = [...n.querySelectorAll(SIB_ERR)].find((k) => k !== x && !k.contains(x) && !/^(input|select|textarea)$/.test(k.localName)
                && VF.shown(k) && VF.norm(k.innerText));
              if (m) return VF.norm(m.innerText).slice(0, 150);
              if ([...n.querySelectorAll("[aria-invalid=true]")].some((k) => k !== x && !k.contains(x) && !VF.shown(k))) return "aria-invalid";
            }
            return "";
          };
          // A custom error the page set (setCustomValidity) for a value the field no longer holds is stale.
          const CV = window[Symbol.for("vf-net2")]?.cv;
          const FLAGS = ["valueMissing", "typeMismatch", "patternMismatch", "tooLong", "tooShort", "rangeUnderflow", "rangeOverflow", "stepMismatch", "badInput"];
          const invalidOf = (x, e) => {
            if (x.willValidate && x.validity && !x.validity.valid) {
              const stale = x.validity.customError && !FLAGS.some((k) => x.validity[k]) && CV?.has(x) && CV.get(x) !== x.value;
              if (!stale) return errText(x) || x.validationMessage || "invalid";
            }
            if (VF.attr(x, "aria-invalid") === "true" || VF.attr(e, "aria-invalid") === "true") return errText(x) || "aria-invalid";
            const c = (n) => typeof n.className === "string" ? n.className : VF.attr(n, "class");
            if (BAD_CLASS.test(c(x)) || BAD_CLASS.test(c(e))) return errText(x) || "marked invalid";
            for (let n = VF.up(x), i = 0; n && n !== document.body && i < 2; n = VF.up(n), i++)
              if (!/^(form|fieldset)$/.test(n.localName) && BAD_WRAP.test(c(n))) return errText(x) || "marked invalid";
            return sibErr(x);
          };
          const emptyOf = (x, boxes) => {
            if (boxes) return boxes.some((b) => !b.value);
            if (x.localName === "input" && x.type === "radio")
              return !x.checked && !(x.name && VF.all(`input[type=radio][name="${CSS.escape(x.name)}"]`).some((r) => r.checked));
            if ((x.localName === "input" && x.type === "checkbox") || /^(checkbox|switch|radio)$/.test(VF.attr(x, "role"))) return !VF.checked(x);
            if (x.localName === "select") return !x.value || unsetOf(x);
            if (/^(input|textarea)$/.test(x.localName)) return !x.value.trim() && !(combo(x) && comboShown(x)?.unset === false);
            if (custom(x)) return unsetOf(x);
            return !VF.norm(x.innerText);
          };
          const inputOf = new Map();  // fields(): a label standing for its hidden checkbox/radio
          const TOGGLE_ROLE = /^(checkbox|switch|radio)$/;
          for (let e of VF.elements()) {
            if (e === document.body || e === document.documentElement) continue;
            // MUI's hidden native input beside its role=combobox box: the box is the row.
            if (FIELDS && e.localName === "input" && VF.attr(e, "aria-hidden") === "true" && e.parentElement?.querySelector?.("[role=combobox]")) continue;
            if (FIELDS && e.localName === "input" && /^(checkbox|radio)$/.test(e.type) && !VF.shown(e)) {
              // A styled checkbox: the input is hidden, its label is what shows (VD-07).
              const lab = e.labels?.[0] ?? e.closest?.("label");
              if (!lab || inputOf.has(lab) || byEl.has(lab) || !VF.shown(lab)) continue;
              inputOf.set(lab, e);
              e = lab;
            } else if (!(FIELDS ? VF.isField(e) || (TOGGLE_ROLE.test(VF.attr(e, "role")) && e.localName !== "input") || custom(e)
                       : VF.isField(e) || e.localName === "iframe" || VF.semantic(e) || VF.pointerOrigin(e))) continue;
            if (boxed.has(e) || !VF.shown(e) || shut(e)) continue;
            // Split code boxes (one character each, ≥ 4 together): one item for all of them.
            let boxes = null;
            if (one(e))
              for (let n = VF.up(e), i = 0; n && n !== document.body && i < 4 && !boxes; n = VF.up(n), i++) {
                const b = [...n.querySelectorAll("input")].filter((x) => one(x) && VF.shown(x));
                if (b.length >= 4) boxes = b;
              }
            for (const b of boxes ?? []) boxed.add(b);
            const cust = FIELDS && custom(e);
            const text = (cust ? customLabel(e) : VF.label(e) || (VF.isField(e) || e.localName === "iframe" ? "" : VF.iconName(e))).slice(0, 100);
            const id = e.id, name = VF.attr(e, "name"), placeholder = VF.attr(e, "placeholder") || VF.attr(e, "data-placeholder");
            const role = boxes ? "code" : VF.attr(e, "role") || (/^(input|button)$/.test(e.localName) ? e.type : "");
            const toggle = (e.localName === "input" && /^(checkbox|radio)$/.test(e.type)) || e.hasAttribute("aria-checked")
              || /^(radio|checkbox|switch)$/.test(role) || inputOf.has(e);
            let card = "";
            // An unlabeled toggle or field: the text around it ("Geben Sie den 6-stelligen Code ein").
            if ((toggle || VF.isField(e) || cust) && !text)
              for (let n = VF.up(e), i = 0; n && n !== document.body && i < 6 && !card; n = VF.up(n), i++)
                card = VF.norm(n.innerText).slice(0, 100);
            // An editor (CKEditor's editable, role=textbox): the label shown before it (DE-08).
            if (FIELDS && e.isContentEditable && !card)
              for (let n = e, i = 0; n && n !== document.body && i < 5 && !card; n = VF.up(n), i++)
                card = VF.norm(n.previousElementSibling?.innerText ?? "").slice(0, 100);
            const hay = `${text} ${id} ${name} ${placeholder} ${VF.attr(e, "aria-label")} ${e.localName} ${role} ${card}`.toLowerCase();
            if (WANT.length && !WANT.some((w) => hay.includes(w))) continue;
            const h = { e, text, id, name, placeholder, role, toggle, card, boxes, cust, inp: inputOf.get(e) ?? null,
                        top: dialogs.some((d) => VF.inside(e, d)) };
            hits.push(h);
            byEl.set(e, h);
            // The outer one of a control in a control with the same text goes.
            for (let n = VF.up(e), i = 0; n && i < 12; n = VF.up(n), i++) {
              const o = byEl.get(n);
              if (o && (o.text === text || (cust && o.cust))) o.drop = true;
            }
          }
          const keep = hits.filter((h) => !h.drop);
          keep.sort((a, b) => b.top - a.top);  // stable: page order within each group
          // Numbers this document gave before stay; a copied attribute doesn't count.
          const seen = new Map();
          for (const e of VF.all("[data-vf-ref]")) seen.set(VF.attr(e, "data-vf-ref"), (seen.get(VF.attr(e, "data-vf-ref")) ?? 0) + 1);
          // START: the tab's highest ref so far, unless this is a new top document (DOCID: the old one's).
          let next = Math.max(window[RK] ?? 0, DOCID === null || window[DOC] === DOCID ? START : 0);
          const used = new Set(), out = [];
          for (const h of keep.slice(0, LIMIT)) {
            const e = h.e, old = VF.attr(e, "data-vf-ref");
            let ref = Number(old);
            if (!(ref > 0 && ref <= next) || seen.get(old) > 1 || used.has(ref)) {
              ref = ++next;
              e.setAttribute("data-vf-ref", ref);
            }
            used.add(ref);
            const item = { ref, tag: e.localName, role: h.role, text: h.text, selector: VF.selectorFor(e, ref) };
            if (h.boxes) {
              for (const b of h.boxes) b.setAttribute("data-vf-code", ref);
              item.selector = `[data-vf-code="${ref}"]`;  // all the boxes, in order: type_code(selector, code)
              item.boxes = h.boxes.length;
            }
            if (h.id) item.id = h.id.slice(0, 100);
            if (h.name) item.name = h.name.slice(0, 100);
            if (h.placeholder) item.placeholder = h.placeholder.slice(0, 100);
            if (e.disabled === true || VF.attr(e, "aria-disabled") === "true") item.disabled = true;
            if (h.toggle) item.checked = VF.checked(h.inp ?? e);
            if (h.card) item.card = h.card;
            if (FIELDS) {
              const x = h.inp ?? e, r = e.getBoundingClientRect();
              // A copy a person can't see (Shopify's #autofill_* fields, a slid-away step): fill() prefers the others.
              if (!VF.reachable(e) || r.width <= 2 || r.height <= 2 || r.right < 0 || r.bottom + scrollY < 0
                  || r.left > Math.max(innerWidth, document.documentElement.scrollWidth)) item.offscreen = true;
              item.kind = h.boxes ? "code" : x.localName === "input" ? x.type : x.localName === "select" ? (x.multiple ? "select-multiple" : "select")
                : x.localName === "textarea" ? "textarea" : TOGGLE_ROLE.test(VF.attr(x, "role")) ? VF.attr(x, "role") : h.cust ? "combobox" : "editable";
              item.value = value(x, h.text, h.boxes);
              if (requiredOf(x, e, h.text)) item.required = true;
              const bad = invalidOf(x, e);
              if (bad) item.invalid = bad;
              if (unsetOf(x)) item.unset = true;
              if (item.required || bad) item.empty = emptyOf(x, h.boxes);
            }
            if (e.hasAttribute("href")) item.href = VF.absHref(e).slice(0, 300);
            out.push(item);
          }
          window[RK] = next;
          return { doc: window[DOC], items: out, next, frames: VF.all("iframe, frame").some(VF.shown), inflight: INFLIGHT() };"""
        want = [w.strip() for w in (filter or "").lower().split("|") if w.strip()]
        await self._quiet_reads()
        items, frames, note = [], True, []
        if frame is None:
            r = await self._guard(_vf(_INFLIGHT_JS + js, WANT=want, LIMIT=int(limit), START=self._refs_max, FIELDS=bool(_fields),
                                      DOCID=self._refs_doc or ""), timeout)
            if r["doc"] != self._refs_doc:
                self._refs, self._refs_doc, self._refs_max = {}, r["doc"], 0
            self._refs_max = max(self._refs_max, r["next"])
            for it in r["items"]:
                self._refs[it["ref"]] = {"doc": r["doc"], "tag": it["tag"], "text": it["text"],
                                         "href": it.get("href"), "css": it["selector"], "frame": None}
            items, frames = r["items"], r["frames"]
            for it in items:
                if it.get("href"):
                    it["href"] = _mask_url(it["href"])  # click(ref=) uses the real one kept above
            if r.get("inflight") and not _fields:
                note = [{"ref": None, "tag": "", "role": "note", "selector": "",
                         "text": f"[loading: {r['inflight']} request(s) in flight, page may be incomplete: await tab.idle(), then observe again]"}]
        if not frames:
            return items + note
        fs = self._frames()
        try:
            if frame is None:
                await fs.load()
                vh = fs.top.vh or 1e9
                docs = [d for d in fs.shown_docs() if d.box[2] >= 50 and d.box[3] >= 20
                        and -vh / 2 < d.box[1] + d.box[3] and d.box[1] < vh * 1.5 and not _AD_FRAME.search(d.host)]
            else:
                docs = await self._frame_docs(fs, frame)
            for d in docs:
                try:
                    r = await d.ev(_vf(_INFLIGHT_JS + js, WANT=want, LIMIT=int(limit), START=self._refs_max, DOCID=None,
                                       FIELDS=bool(_fields)), timeout)
                except (RuntimeError, _asyncio.TimeoutError):
                    continue  # gone or busy meanwhile
                self._refs_max = max(self._refs_max, r["next"])
                for it in r["items"]:
                    it["frame"] = d.host
                    self._refs[it["ref"]] = {"doc": r["doc"], "tag": it["tag"], "text": it["text"],
                                             "href": it.get("href"), "css": it["selector"],
                                             "frame": d.frame_id, "loader": d.loader, "host": d.host}
                for it in r["items"]:
                    if it.get("href"):
                        it["href"] = _mask_url(it["href"])
                items += r["items"]
        finally:
            await fs.close()
        return items + note

    async def fields(self, filter=None, *, problems=False, frame=None, limit=200, timeout=20):
        """What the visible form fields hold, to read back a form before going
        on: [{label, selector, value, kind, ref}] (+ frame: its host, for a
        field in a frame; frames as in observe(), whose refs these share).
        value: the text; a select's chosen option text; True/False for a
        checkbox or radio; split code boxes "4 of 6 boxes filled". Never a
        secret: password fields (type=password or shown as dots) show
        "••• (password)", fields vault_fill filled "••• (vault)" (empty ones
        ""), card numbers "•••• <last 4>", CVV fields "•••". Rows also get
        required: True (required / aria-required / data-validate required /
        a label ending in "*" / a Magento-style required wrapper, open shadow
        roots too) and invalid: why (the field's error text, the browser's
        validation message, "aria-invalid", "marked invalid", a Magento/
        knockout error box beside it; a custom error the page set for an
        earlier value is dropped) when they apply, and unset: True for a
        select or custom select (s-select, mat-select, MUI, react-select,
        role=combobox) still on its placeholder ("Bitte auswählen",
        "Please select", "--", empty); kind "combobox" for a custom one.
        problems=True: only the shown, enabled fields that block a form:
        required ones left empty, invalid ones, and unset selects even when
        the page doesn't mark them required (why "Weiter" stays disabled or
        does nothing). `filter` as in observe()."""
        items = await self.observe(filter, limit, frame=frame, timeout=timeout, _fields=True)
        out = []
        for it in items:
            if problems and (it.get("disabled") or it.get("offscreen")
                             or not (it.get("invalid") or it.get("unset") or (it.get("required") and it.get("empty")))):
                continue
            f = {"label": it["text"] or it.get("placeholder") or it.get("card") or it.get("name") or it.get("id") or "",
                 "selector": it["selector"], "value": it["value"], "kind": it["kind"], "ref": it["ref"]}
            if it.get("card") and it["card"] != f["label"]:
                f["near"] = it["card"]  # the text shown before or around it (an editor's visible label)
            if it.get("frame"):
                f["frame"] = it["frame"]
            if it.get("disabled"):
                f["disabled"] = True
            if it.get("placeholder") and it["placeholder"] != f["label"]:
                f["placeholder"] = it["placeholder"]
            if it.get("offscreen"):
                f["offscreen"] = True  # a copy no one sees (fill() prefers the shown ones)
            if it.get("required"):
                f["required"] = True
            if it.get("invalid"):
                f["invalid"] = it["invalid"]
            if it.get("unset"):
                f["unset"] = True  # still on the placeholder option / text
            out.append(f)
        return out

    async def count_proof(self, rows=None, css=None, *, selector="main", timeout=20):
        """Proof that a list read is complete, before reporting a list, a
        total or "none": the count the page states of its own list (a range
        "21–40 of 345", "345 Treffer/Ergebnisse/results", "Showing 20 of
        345", "12 Bestellungen/orders/Transaktionen", "Insgesamt: 12",
        "Bestellungen (12)", "Keine Treffer/No results" = 0) against the rows
        read: `rows` (a list: its length; or a number), else the visible
        elements CSS `css` matches. Returns {source_count, read, complete,
        evidence: the page's line holding the count, shown: the rows a
        range puts on this page, pages: (page, of) of a pager, more: the
        label of a visible next-page/load-more control, others: the page's
        other counts, note} and prints one line to paste into the report.
        complete is True only when the page states an exact count and read
        equals it; no count, an approximate one ("über 1.000"), or no rows
        given: False, and note says why. Looks in `selector` (default
        "main"), then in the whole page."""
        if isinstance(rows, (bool, str, bytes)) or not (rows is None or isinstance(rows, int) or hasattr(rows, "__len__")):
            raise ValueError("count_proof rows: the rows read (a list) or their number")
        r = await self._guard(_vf(
            "const els = SEL === null ? [] : VF.all(SEL).filter(VF.shown);"
            "const MORE = /^(weiter|nächste|nächste seite|next|next page|mehr laden|mehr anzeigen|weitere laden|"
            "weitere anzeigen|weitere ergebnisse( anzeigen| laden)?|alle ergebnisse anzeigen|load more|show more|see more|"
            "more results|show more results|›|»|>|→)$/i;"
            "const on = (e) => VF.shown(e) && !e.disabled && VF.attr(e, 'aria-disabled') !== 'true'"
            " && !/(^|[\\s_-])(is-)?disabled([\\s_-]|$)/i.test(VF.attr(e, 'class'));"
            "const more = VF.all('a[href], button, [role=button], [role=link]').find((e) => on(e)"
            " && (VF.attr(e, 'rel').split(/\\s+/).includes('next') || MORE.test(VF.norm(VF.label(e)))));"
            "return { css: els.length, more: more ? (VF.norm(VF.label(more)) || 'next').slice(0, 60) : null };",
            SEL=css), timeout)
        read = (rows if isinstance(rows, int) else len(rows)) if rows is not None else r["css"] if css else None
        page = await self.text(selector, timeout=timeout, _hints=False)
        cands, pages = _count_candidates(page)
        if not cands and selector:
            page = await self.text(None, timeout=timeout, _hints=False)
            cands, pages = _count_candidates(page)
        best = cands[0] if cands else None
        out = {"source_count": best["n"] if best else None, "read": read, "complete": False,
               "evidence": best["evidence"] if best else None, "shown": best.get("shown") if best else None,
               "pages": pages, "more": r["more"],
               "others": list(dict.fromkeys(f'{c["n"]} ({c["evidence"][:60]!r})' for c in cands[1:]
                                            if c["n"] != best["n"]))[:5] if best else [],
               "note": ""}
        if rows is not None and css:
            out["css_count"] = r["css"]
        notes = []
        if read is None:
            notes.append("no rows given: pass rows= (the rows read) or css= (one per row)")
        if best is None:
            notes.append("the page states no count of its own list: completeness unproven"
                         + (f"; it offers {r['more']!r}: more rows likely" if r["more"] else "")
                         + (f"; pager: page {pages[0]} of {pages[1]}" if pages else ""))
        elif best["approx"]:
            notes.append(f"the page's count is approximate ({best['evidence'][:60]!r}): check the list's end yourself")
        elif read is not None:
            n = best["n"]
            if read == n:
                out["complete"] = True
            elif read < n:
                gap = f"read {read} of the page's {n}"
                if best.get("shown") and read == best["shown"]:
                    gap += f" (only this page's {best['shown']})"
                notes.append(gap + ": load the rest (load_all/collect/next page" + (f" {r['more']!r}" if r["more"] else "")
                             + ") before reporting a list, total or none")
            else:
                notes.append(f"read {read}, more than the page's {n}: the rows hold extras (ads, duplicates, "
                             "another list): narrow css= or dedupe")
        if out["others"]:
            notes.append("other counts on the page: " + "; ".join(out["others"]))
        out["note"] = "; ".join(notes)
        where = _urlparse.urlsplit(await self.url())
        state = ("complete" if out["complete"] else "UNPROVEN" if not best or read is None or best["approx"]
                 else "INCOMPLETE" if read < best["n"] else "MISMATCH")
        line = (f"[count_proof {state}: read {read}, the site says "
                + (f"{best['n']}{'+' if best['approx'] else ''} ({best['evidence'][:100]!r})" if best else "no count")
                + f" on {where.hostname}{where.path[:80]}" + (f"; {out['note']}" if out["note"] else "") + "]")
        print(line)
        return out

    async def nav_links(self, match=None, *, open_menus=True, follow=True, timeout=20):
        """Every link of the site's header, navigation, footer and account /
        user menu, before claiming a site has no page or feature X (quote the
        scan): [{area, label, url}] (+ hidden: True for a link in a collapsed
        menu, via: the menu or hop that showed it). area: header, nav,
        account, menu (an open drawer/popover), footer, or "sso <host>".
        open_menus: opens the header/nav/account menu toggles (hover, then a
        click; never a link, a submit or a cart/search/language control, and
        never anything whose text, aria-label, title, href, id/class or test
        id says log out/abmelden/ausloggen/sign out/delete/löschen/kündigen/
        cancel: "not touched: …" names those; a toggle without aria-expanded
        / aria-haspopup is only hovered) and closes them again. follow=True
        follows one off-site
        "Online Shop"/portal/SSO link in a tab of its own (closed after) and
        adds that site's menus; a string follows the link with that label;
        False none. `match` (a string, "a|b" for either, or a re.Pattern)
        keeps the links whose label or URL holds it. Prints the links grouped
        by area (in full once per site and kernel, then a summary line)."""
        links, hops, notes, opened = await self._nav_scan(open_menus, follow, timeout)
        href = await self._href()
        host = _urlparse.urlsplit(href).hostname or ""
        hopped = None
        if follow and hops:
            hop = hops[0]
            try:
                t = await shop_tab(hop["url"], timeout=timeout)
            except (HardRule, BrowserRestarted):
                raise
            except Exception as e:  # noqa: BLE001  (the scan of this site stands on its own)
                notes.append(f"could not follow {hop['label']!r}: {str(e)[:200]}")
            else:
                try:
                    sub, _, sub_notes, sub_opened = await t._nav_scan(open_menus, False, timeout)
                    landed = _urlparse.urlsplit(await t._href()).hostname or ""
                finally:
                    await t.close()
                if _site(landed) == _site(host):
                    notes.append(f"following {hop['label']!r} came back to {landed}: no partner site")
                else:
                    seen = {ln["url"] for ln in links if ln["url"]}
                    sub = [ln for ln in sub if not ln["url"] or ln["url"] not in seen]
                    for ln in sub:
                        ln["area"], ln["via"] = f"sso {landed}", hop["label"]
                    links += sub
                    notes += sub_notes
                    hopped = (hop["label"], landed, len(sub))
        elif isinstance(follow, str) and not hops:
            notes.append(f"no shown link labelled {follow!r} to follow")
        if match is not None:
            if isinstance(match, _re.Pattern):
                keep = lambda s: bool(match.search(s))  # noqa: E731
            else:
                alts = [a.strip().lower() for a in str(match).split("|") if a.strip()]
                keep = lambda s: any(a in s.lower() for a in alts)  # noqa: E731
            links = [ln for ln in links if keep(f"{ln['label']} {ln['url'] or ''}")]
        out = []
        for ln in links:
            row = {"area": ln["area"], "label": ln["label"], "url": _mask_url(ln["url"]) if ln["url"] else None}
            if ln.get("hidden"):
                row["hidden"] = True
            if ln.get("via"):
                row["via"] = ln["via"]
            out.append(row)

        def short(u):
            if not u:
                return "(no link)"
            p = _urlparse.urlsplit(u)
            if not p.hostname:
                return u[:70]  # mailto:, tel:
            s = (p.path or "/") + (f"?{p.query}" if p.query else "")
            return (s if p.hostname == host else f"{p.hostname}{s}")[:70]

        areas = {}
        for row in out:
            areas.setdefault(row["area"], []).append(row)
        head = (f"[nav_links {host}: {len(out)} links" + (f" matching {match!r}" if match is not None else "") + ": "
                + ", ".join(f"{a} {len(v)}" for a, v in areas.items())
                + (f"; menus opened: {', '.join(repr(o) for o in opened)}" if opened else "")
                + (f"; followed {hopped[0]!r} to {hopped[1]} ({hopped[2]} links)" if hopped else "")
                + (f"; {'; '.join(notes)}" if notes else "") + "]")
        sig = (len(out), tuple(areas))
        if match is None and _NAV_PRINTED.get(host) == sig:
            print(_mask_url(head[:-1] + "; listed in full before: the return value has them, match= filters]"))
            return out
        if match is None:
            _NAV_PRINTED[host] = sig
        lines = [head]
        for a, rows in areas.items():
            items = [f"{r['label'] or '(icon)'} {short(r['url'])}" for r in rows[:60]]
            more = f" … +{len(rows) - 60} more (returned)" if len(rows) > 60 else ""
            lines.append(f"  {a}: " + " · ".join(items) + more)
        print(_mask_url("\n".join(lines)))
        return out

    async def _nav_scan(self, open_menus, follow, timeout):
        """nav_links() on this page alone: (links, hops, notes, menus opened)."""
        hop_label = follow if isinstance(follow, str) else None
        r = await self._guard(_vf(_NAV_JS, MARK=bool(open_menus), FOLLOW=hop_label), timeout)
        found = {ln["key"]: ln for ln in r["links"]}
        notes, opened = [], []
        if r.get("skipped"):
            notes.append("not touched (log out/delete/cancel words): " + ", ".join(repr(s) for s in r["skipped"]))
        start = (await self._href()).split("#")[0]
        deadline = _time.monotonic() + timeout
        for tg in r["toggles"] if open_menus else []:
            if _time.monotonic() > deadline:
                notes.append(f"stopped opening menus after {timeout} s")
                break
            new = 0
            try:
                for click in (False, True) if not tg.get("hover") else (False,):  # hover first; a click when hovering shows nothing new
                    if not await self._ev(_vf(_NAV_TOGGLE_JS, I=tg["i"], OPEN=True, CLICK=click, DANGER_RX=_NAV_DANGER_RX), 5):
                        break
                    await _asyncio.sleep(0.8 if click else 0.5)
                    s = await self._ev(_vf(_NAV_JS, MARK=False, FOLLOW=None), 10)
                    for ln in s["links"]:
                        old = found.get(ln["key"])
                        if old is None or (old["hidden"] and not ln["hidden"]):
                            if tg["area"] == "account" or ln["area"] == "menu":
                                ln["area"] = tg["area"] if tg["area"] != "menu" else ln["area"]
                            ln["via"] = tg["label"]
                            found[ln["key"]] = ln
                            new += 1
                    if new:
                        break
                await self._ev(_vf(_NAV_TOGGLE_JS, I=tg["i"], OPEN=False, CLICK=False, DANGER_RX=_NAV_DANGER_RX), 5)
            except (RuntimeError, _asyncio.TimeoutError):
                pass  # the page redrew or moved: checked below
            if new:
                opened.append(tg["label"])
            now = (await self._href()).split("#")[0]
            if now != start:
                notes.append(f"menu toggle {tg['label']!r} opened {_mask_url(now)}; went back")
                await self.goto(start)
                break
        return list(found.values()), r["hops"], notes, opened

    _FIND_ORDER = {"visible": 0, "hidden": 1, "option": 2, "attr": 3, "json": 4, "xhr": 5}

    async def find(self, *terms, regex=False, frames=True, limit=20, timeout=20):
        """Every place the page holds `terms` (case-insensitive; regex=True or
        a re.Pattern: regexes), before writing "not shown"/Missing: the
        visible text, hidden text (collapsed accordions, closed <details>,
        hidden tab panels, display:none, aria-hidden; with the label of what
        hides it), <select> options, attributes (aria-label, alt, title,
        placeholder, value, content, href, data-*; never password or
        vault-filled fields), the page's JSON (<script type=…json>,
        __NEXT_DATA__, <noscript>, window.__NUXT__/__APOLLO_STATE__/…) and
        the JSON bodies of the fetch/XHR responses the page got since it
        loaded (keys and values); open shadow roots and frames (frames=True)
        too. → [{term, where, snippet}] (+ frame: its host), where:
        'visible' | "hidden '<accordion label>'" | "option of '<select>'" |
        'attr <tag> <name>' | 'json <script> <path>' | 'xhr <url> <path>'
        (path like data.offers[0].supplier.name, for fetch_json/page_json).
        Prints a summary per term, ending with the page it searched (title
        and URL: is it still the page that held the fact?), and one line
        per hit (≤ 40). ≤ `limit` hits per term, kind and document. Masked
        like text()."""
        if len(terms) == 1 and isinstance(terms[0], (list, tuple)):
            terms = tuple(terms[0])
        if any(isinstance(t, _re.Pattern) for t in terms):
            regex = True
        terms = [t.pattern if isinstance(t, _re.Pattern) else t for t in terms]
        if not terms or not all(isinstance(t, str) and t.strip() for t in terms):
            raise ValueError("find: one or more words, e.g. await tab.find('Homespot', 'Router')")
        expr = _vf(_FIND_JS, TERMS=terms, RX=bool(regex), MAXH=int(limit), JSONX=True)
        await self._quiet_reads()
        top = await self._guard(expr, timeout)
        if top["bad"]:
            raise ValueError(f"find: not a valid regex: {top['bad'][0]}")
        docs, nframes = [(None, top)], 0
        if frames:
            fs = self._frames()
            try:
                for d in await fs.load():
                    if d.blank or _AD_FRAME.search(d.host):
                        continue
                    try:
                        docs.append((d.host, await d.ev(expr, timeout)))
                        nframes += 1
                    except (RuntimeError, _asyncio.TimeoutError):
                        continue  # gone meanwhile
            finally:
                await fs.close()
        out, totals = [], [{} for _ in terms]
        for host, res in docs:
            for k, n in res["total"].items():
                i, kind = k.split(" ", 1)
                totals[int(i)][kind] = totals[int(i)].get(kind, 0) + n
            for h in res["hits"]:
                row = {"term": terms[h["term"]], **self._find_row(h)}
                if host:
                    row["frame"] = host
                out.append(row)
        out.sort(key=lambda r: self._FIND_ORDER.get(r["where"].split(" ", 1)[0], 9))
        out = [{k: _mask_secrets(_mask_url(v)) if isinstance(v, str) else v for k, v in r.items()} for r in out]
        n_json = sum(res["nsrc"] for _, res in docs)
        searched = (f"searched visible and hidden text, options, attributes, {n_json} page JSON source(s), "
                    + (f"{top['nxhr']} XHR/fetch JSON bodies since load" if top["recorder"]
                       else "no XHR record (a tab from before this vault-fill version: open a new shop_tab())")
                    + (f", {nframes} frame(s)" if frames else "")
                    + f"; on page {(top.get('title') or '').strip()[:80]!r} {_mask_url(top['href'])[:160]}")
        for i, t in enumerate(terms):
            tot = totals[i]
            print(f"[find {t!r}: " + (", ".join(f"{k} {n}" for k, n in sorted(tot.items(), key=lambda kv: self._FIND_ORDER.get(kv[0], 9)))
                                      if tot else "no hit") + f" — {searched}]")
        for r in out[:40]:
            print(f"  {r['term']!r} · {r['where']}{' (frame ' + r['frame'] + ')' if r.get('frame') else ''} · {r['snippet'][:220]}")
        if len(out) > 40:
            print(f"  … {len(out) - 40} more hits: the return value has them")
        return out

    @staticmethod
    def _find_row(h):
        """{where, snippet} of one _FIND_JS hit, values masked by their key."""
        kind = h["kind"]
        if kind == "visible":
            return {"where": "visible", "snippet": h["snippet"]}
        if kind == "hidden":
            return {"where": f"hidden {h['label']!r}" if h.get("label") else "hidden", "snippet": h["snippet"]}
        if kind == "option":
            return {"where": f"option of {h['label']!r}" if h.get("label") else "option", "snippet": h["snippet"]}
        full = h.get("full", "")
        masked = _mask_value(h.get("key") or "", full) if full else full
        if kind == "attr":
            where = f"attr {h['tag']}{'#' + h['id'] if h.get('id') else ''} {h['attr']}"
            return {"where": where, "snippet": masked if masked is not full else h["snippet"]}
        src = h["src"][:160] if kind == "json" else _mask_url(h["src"])[:160]
        where = f"{kind} {src} {h['path'] or '(top)'}"
        if h.get("keyhit"):
            val = masked if masked is not full else _mask_json_text(full)
            return {"where": where, "snippet": f"{h['key']}: {val}"}
        return {"where": where, "snippet": masked if masked is not full else h["snippet"]}

    async def html(self, css=None, *, timeout=20):
        """The outerHTML of the page (css=None) or of the first element CSS
        `css` matches (open shadow roots too), for data only the markup
        holds (an offerEndDate attribute, data-*): parse it with re or
        html.parser, print only what you need. Masked like text(): one-time-
        link tokens, IBANs, card numbers, JWTs; the value attributes of
        password and credential-named inputs and JSON credential pairs as
        •••. "[no element matches '<css>']" when none does."""
        r = await self._guard(_vf("const e = SEL === null ? document.documentElement"
                                  " : (VF.find({ css: SEL }).el ?? VF.all(SEL)[0] ?? null); return e ? e.outerHTML : null;",
                                  SEL=css), timeout)
        if r is None:
            return f"[no element matches {css!r}]"
        return _mask_url(_mask_html(r))
