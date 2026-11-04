    async def links(self, filter=None, limit=200, *, regex=False, timeout=20):
        """The visible links: [{text, href}], absolute hrefs, one row per href
        (B-84: the first text of it; a later one when that is empty);
        `filter` keeps those whose text or href contains it (any case), with
        regex=True (or a compiled pattern) those a Python regex finds in
        "text href" (re.I). More than `limit` rows: the first `limit`, and it
        prints "[links: N more …]"."""
        js = r"""
          const seen = new Set(), out = [];
          // A link whose own box is empty (its content positioned absolutely) shows through its children.
          const vis = (e) => VF.shown(e) || [...e.children].some((c) => VF.shown(c));
          for (const e of VF.all("[href]")) {
            if (/^(link|base)$/.test(e.localName) || !vis(e)) continue;
            const href = VF.absHref(e), text = VF.label(e).slice(0, 120);
            if (seen.has(text + "\n" + href)) continue;
            seen.add(text + "\n" + href);
            out.push({ text, href });
            if (out.length >= 3000) break;
          }
          return out;"""
        rows = await self._guard(_vf(js), timeout)
        if isinstance(filter, _re.Pattern) or (regex and filter):
            rx = filter if isinstance(filter, _re.Pattern) else _re.compile(filter, _re.I)
            rows = [r for r in rows if rx.search(f"{r['text']} {r['href']}")]
        elif filter:
            want = filter.lower()
            rows = [r for r in rows if want in f"{r['text']} {r['href']}".lower()]
            if not rows and "|" in filter:
                print(f"[links: no link contains {filter!r} as it is; for alternatives: links({filter!r}, regex=True)]")
        by_href = {}
        for r in rows:
            prev = by_href.setdefault(r["href"], r)
            if prev is not r and not prev["text"] and r["text"]:
                by_href[r["href"]] = r
        out = list(by_href.values())
        if len(out) > int(limit):
            print(f"[links: {int(limit)} of {len(out)} links (one per href); {len(out) - int(limit)} more:"
                  f" links(…, limit={len(out)}) or a narrower filter]")
            out = out[:int(limit)]
        await self._count_note("links", out, timeout)
        return out

    async def _count_note(self, who, rows, timeout=20):
        """B-45: prints "[<who>: N rows; the page says M ('…'): …]" when the
        page states an exact count of its list that isn't len(rows)."""
        try:
            cands, _ = _count_candidates(await self.text(None, timeout=timeout, _hints=False))
        except (RuntimeError, _asyncio.TimeoutError):
            return
        best = cands[0] if cands else None
        if best and not best.get("approx") and best["n"] != len(rows):
            print(f"[{who}: {len(rows)} rows; the page says {best['n']} ({best['evidence'][:60]!r}):"
                  " count_proof(rows) / load_all before a list or total]")

    async def cards(self, spec, limit=50, *, timeout=20):
        """The result cards of a list page: [{text, href}] with each card's
        visible text (lines joined by " | "), plus the aria-labels of its
        text-less parts (star ratings: "4,5 von 5 Sternen"). `spec` is a CSS
        selector for the cards (href: its first link), or else a pattern
        (regex, else substring) of their links' href, e.g. "/s-anzeige/" or
        "/rooms/": a card is then the largest box around such a link holding
        no other of them, and href that link."""
        js = r"""
          let cards = null;
          const own = new Map();  // card → the matching link's href
          try { cards = VF.all(SPEC).filter(VF.shown); } catch {}
          if (!cards?.length) {
            let rx = null;
            try { rx = new RegExp(SPEC); } catch {}
            const links = VF.all("[href]").filter((e) => !/^(link|base)$/.test(e.localName) && VF.shown(e));
            const hrefs = new Map(links.map((l) => [l, VF.absHref(l)]).filter(([, h]) => rx ? rx.test(h) : h.includes(SPEC)));
            const seen = new Set();
            cards = [];
            for (const [l, h] of hrefs) {
              if (seen.has(h)) continue;
              seen.add(h);
              let c = l;
              for (let n = VF.up(l); n && n !== document.body; n = VF.up(n)) {
                let other = false;
                for (const [o, oh] of hrefs) if (oh !== h && VF.inside(o, n)) { other = true; break; }
                if (other) break;
                c = n;
              }
              cards.push(c);
              own.set(c, h);
            }
          }
          const quiet = VF.all("[aria-label]").filter((e) => !VF.semantic(e) && !VF.norm(e.innerText) && VF.shown(e));
          return cards.slice(0, LIMIT).map((c) => {
            const a = c.hasAttribute("href") ? c : VF.all("[href]").find((e) => VF.inside(e, c) && VF.shown(e)) ?? c.closest("[href]");
            const labels = [...new Set(quiet.filter((e) => VF.inside(e, c))
              .map((e) => VF.norm(VF.attr(e, "aria-label"))).filter(Boolean))].slice(0, 5);
            const text = [VF.visibleText(c).split("\n").join(" | "), ...labels].join(" | ").slice(0, 600);
            return { text, href: own.get(c) ?? (a ? VF.absHref(a) : null) };
          });"""
        return await self._guard(_vf(js, SPEC=spec, LIMIT=int(limit)), timeout)

    async def load_all(self, more=None, item="a[href]", *, container=None, settle=4.0, max_rounds=100, timeout=180):
        """Load a whole paged list and return how many visible `item`
        elements (CSS) it has then. `more`: the "load more" button, a CSS
        selector or else its text ("Mehr anzeigen"); clicked again and again,
        also when it hides while the next batch loads, until it is gone and
        the count stayed the same for `settle` s (or two clicks in a row
        added nothing). more=None: scrolls to the end instead (infinite
        lists); `container`: the CSS of the box that scrolls (a sidebar, a
        dialog), else the page. Prints the count and the number of
        clicks/scrolls. A virtualized list (rows replaced while scrolling):
        collect()."""
        js = r"""
          const items = VF.all(ITEM).filter(VF.shown);
          let css = 0;
          try { css = MORE !== null && VF.all(MORE).some(VF.shown); } catch {}
          const text = MORE !== null && !css && !!VF.find({ text: MORE }).el;
          if (SCROLL) {
            const box = BOX === null ? null : VF.all(BOX)[0];
            if (box) box.scrollTop = box.scrollHeight;
            else { items.at(-1)?.scrollIntoView({ block: "end", behavior: "instant" }); scrollTo(0, document.scrollingElement.scrollHeight); }
          }
          return { n: items.length, css: !!css, text };"""

        async def state(scroll=False):
            try:
                return await self._ev(_vf(js, ITEM=item, MORE=more, SCROLL=scroll, BOX=container), 10)
            except TabStuck:
                raise
            except (RuntimeError, _asyncio.TimeoutError):
                return None  # mid-render

        async def change(n, scroll=False, button=False):
            """Waits up to `settle` s for the count to change from n (or,
            with `button`, for the button to show up again)."""
            end = _time.monotonic() + settle
            while _time.monotonic() < end:
                await _asyncio.sleep(0.3)
                st = await state(scroll)
                if st and (st["n"] != n or (button and (st["css"] or st["text"]))):
                    return True
            return False

        deadline = _time.monotonic() + timeout
        st = await state()
        n = st["n"] if st else 0
        rounds = fruitless = 0
        while _time.monotonic() < deadline and rounds < max_rounds and fruitless < 2:
            if more is None:
                rounds += 1
                if not await change(n, scroll=True):
                    break  # the end: scrolling brought nothing for `settle` s
            elif st and (st["css"] or st["text"]):
                try:
                    await (self.click(more, timeout=5) if st["css"] else self.click(text=more, timeout=5))
                    rounds += 1
                except RuntimeError:
                    pass  # hid or got covered meanwhile: look again
                fruitless = 0 if await change(n) else fruitless + 1
            elif not await change(n, button=True):
                break  # no button, and nothing came for `settle` s
            st = await state()
            n = st["n"] if st else n
        what = "scrolls" if more is None else "clicks"
        print(f"[load_all: {n} items {item!r} after {rounds} {what}"
              + ("; stopped at the time limit" if _time.monotonic() >= deadline else "")
              + ("; stopped at max_rounds" if rounds >= max_rounds else "") + "]")
        return n

    async def collect(self, item, *, container=None, key="href|text", fields=None, required=None, max_rounds=60,
                      step=0.8, pause=0.4, all=False):
        """Read a virtualized list (rows rendered only near the view and
        replaced while scrolling: Spotify's library, long tables): scrolls
        the items' nearest scrollable box step by step (`step` of its
        height) and collects each `item` (CSS) once, until two rounds add
        nothing at the box's end, or `max_rounds`. Returns [{text, href}] in
        list order; prints "[collect: N items, end reached|stopped after
        max_rounds]". key: "href|text" keys by the item's link, else its
        text; "text" by its text only.
        Which rows: `container` (CSS) keeps the items inside it, and it (or,
        if it doesn't scroll, the items' nearest scroll box) is scrolled.
        Without it, when the items sit in several scroll boxes (a sidebar
        library and the main tracklist), only those of the largest box on
        screen (one in an open dialog first) are read, and the printout names
        the others with a container= that picks them; all=True reads every
        match (no scoping).
        Complete or not: when the list states its size (aria-rowcount of its
        grid/table, aria-setsize) and rows are missing at the end, it scrolls
        back to the top and reads it once more, slower; still short, it
        says "the grid says 50 (aria-rowcount 51) → incomplete: …".
        fields={name: css|fn} adds those values to each row: css inside the
        item gives the text of its first visible match that has some
        (aria-label/alt if no text; never one display:none hides),
        "css@attr" that attribute ("@data-id": the item's own; href/src
        absolute); fn(row) is called on the finished row ({text, href, the
        css fields}; a regex match gives its group; raising counts as empty).
        A row seen again fills its empty css fields (lazy prices). Each row
        then gets "missing": the `required` fields (default: all of them)
        that are empty, and the printout counts them by list index:
        "…; 2 with empty price (rows 4, 17)"."""
        css_f, fn_f = _field_specs(fields, ("text", "href", "_i"))
        js = _PICK_JS + r"""
          const ST = Symbol.for("vf-collect");
          const LIST = "[role=grid],[role=treegrid],[role=table],[role=list],[role=listbox],[role=tree],[role=feed],table,ul,ol";
          const NAMED = LIST + ",[role=region],[role=navigation],[role=main],[role=complementary],nav,aside,section,main";
          const scrolls = (n) => { const s = getComputedStyle(n); return /(auto|scroll|overlay)/.test(s.overflowY) && n.scrollHeight > n.clientHeight + 4; };
          const boxOf = (e) => {
            for (let n = e ? VF.up(e) : null; n && n !== document.body && n !== document.documentElement; n = VF.up(n))
              if (n instanceof Element && scrolls(n)) return n;
            return null;
          };
          const area = (b) => {
            if (!b) return innerWidth * innerHeight;
            const r = b.getBoundingClientRect();
            return Math.max(0, Math.min(r.right, innerWidth) - Math.max(r.left, 0)) * Math.max(0, Math.min(r.bottom, innerHeight) - Math.max(r.top, 0));
          };
          // What a group of items sits in: the nearest labelled list/landmark that holds them (two at
          // least) and no other group's item, else the nearest such element with a unique id:
          // [what it is, a container= CSS for it].
          const named = (es, others) => {
            let byId = null;
            for (let n = VF.up(es[0]); n && n !== document.body; n = VF.up(n)) {
              if (!(n instanceof Element) || es.filter((x) => VF.inside(x, n)).length < Math.min(2, es.length)) continue;
              if (others.some((x) => VF.inside(x, n))) break;
              const al = n.getAttribute("aria-label");
              if (al && n.matches(NAMED))
                return [`${n.getAttribute("role") || n.localName} "${al.slice(0, 60)}"`, `[aria-label="${al.replace(/(["\\])/g, "\\$1")}"]`];
              if (!byId && n.id && document.querySelectorAll("#" + CSS.escape(n.id)).length === 1) byId = [`#${n.id}`, "#" + CSS.escape(n.id)];
            }
            return byId ?? [null, null];
          };
          const num = (v) => { const n = parseInt(v ?? "", 10); return Number.isFinite(n) && n > 0 ? n : null; };
          const all = VF.all(ITEM).filter(VF.shown);
          let st = window[ST], fresh = false;
          if (FIRST || !st || !st.ready || (st.box && !st.box.isConnected) || (st.root && !st.root.isConnected)) {
            fresh = true;
            st = window[ST] = { box: null, root: null, scoped: false, left: [], label: null, ready: all.length > 0 };
            const cont = BOX === null ? null : VF.all(BOX)[0] ?? null;
            if (cont) {
              const inside = all.filter((e) => VF.inside(e, cont));
              st.root = inside.length ? cont : null;
              st.box = scrolls(cont) || !inside.length ? cont : boxOf(inside[0]);
            } else if (!ALL && all.length) {
              const groups = new Map();
              for (const e of all) { const b = boxOf(e); if (!groups.has(b)) groups.set(b, []); groups.get(b).push(e); }
              // An open dialog's list first (as click() prefers it), then the largest box on screen.
              const modal = (b) => !!b?.closest?.("dialog[open], [role=dialog], [role=alertdialog], [aria-modal=true]");
              const rank = (b) => [modal(b) ? 1 : 0, area(b)];
              let best = null;
              for (const [b, es] of groups) {
                const [m, a] = rank(b), [bm, ba] = best ? rank(best[0]) : [-1, -1];
                if (m > bm || (m === bm && a > ba)) best = [b, es];
              }
              st.box = best[0];
              if (groups.size > 1) {
                st.scoped = modal(best[0]) ? "an open dialog's scroll box" : "the largest scroll box";
                st.label = named(best[1], all.filter((e) => !best[1].includes(e)))[0];
                st.left = [...groups].filter(([b]) => b !== best[0])
                  .map(([b, es]) => { const [label, css] = named(es, all.filter((e) => !es.includes(e))); return { n: es.length, label, css }; });
              }
            } else st.box = all[0] ? boxOf(all[0]) : null;
          }
          const items = st.root ? all.filter((e) => VF.inside(e, st.root)) : st.scoped ? all.filter((e) => boxOf(e) === st.box) : all;
          const link = (e) => e.hasAttribute("href") ? VF.absHref(e) : (e.querySelector("a[href]") ? VF.absHref(e.querySelector("a[href]")) : "");
          const index = (e) => num(e.closest?.("[aria-rowindex]")?.getAttribute("aria-rowindex"))
            ?? num(e.closest?.("[aria-posinset]")?.getAttribute("aria-posinset"));
          const rows = items.map((e) => ({ text: VF.norm(e.innerText).slice(0, 300), href: link(e), _i: index(e),
                                           ...Object.fromEntries(Object.entries(FIELDS).map(([k, s]) => [k, pick(e, s, k)])) }));
          // The size the list states: aria-setsize of the items' set, else aria-rowcount of their grid/table.
          let total = null, how = null, list = null;
          const set = items[0]?.closest?.("[aria-setsize]");
          const grid = items[0]?.closest?.("[aria-rowcount]");
          if (set && num(set.getAttribute("aria-setsize"))) [total, how] = [num(set.getAttribute("aria-setsize")), "aria-setsize"];
          else if (grid && num(grid.getAttribute("aria-rowcount"))) [total, how] = [num(grid.getAttribute("aria-rowcount")), "aria-rowcount"];
          if (total) { const l = (how === "aria-setsize" ? set : grid).closest?.(LIST); list = l ? (l.getAttribute("role") || l.localName) : "list"; }
          const box = st.box, sc = box ?? document.scrollingElement;
          const before = sc.scrollTop, h = box ? box.clientHeight : innerHeight;
          const end = !TOP && before + h >= sc.scrollHeight - 2;
          if (TOP) sc.scrollTop = 0;
          else if (SCROLL && !end) sc.scrollTop = before + h * STEP;
          return { rows, end, box: !!box, total, how, list, scoped: st.scoped, label: st.label, left: fresh ? st.left : null };"""
        seen, out, still, rounds, end, left, again = {}, [], 0, 0, False, [], False
        idx, total, how, lst, scoped_to, wait = set(), None, None, None, None, pause

        def gap():
            """(what the list says it holds, the row numbers never read) when it states its size."""
            if not total:
                return None, []
            if idx:  # rows numbered: rowindex counts header rows too (data from 2 when the header isn't an item)
                lo = 1 if (how == "aria-setsize" or min(idx) == 1) else 2
                return total - lo + 1, [i for i in range(lo, total + 1) if i not in idx]
            n = total - (how == "aria-rowcount")  # unnumbered rows: allow one header row
            return n, list(range(len(out) + 1, n + 1))

        while rounds < max_rounds:
            rounds += 1
            r = await self._guard(_vf(js, ITEM=item, BOX=container, SCROLL=True, STEP=float(step), FIELDS=css_f,
                                      ALL=bool(all), FIRST=rounds == 1, TOP=False), 20)
            if r["left"] is not None:  # the scope was (re)chosen this round
                left, scoped_to, where = r["left"], r["label"] if r["scoped"] else None, r["scoped"]
            total, how, lst = r["total"] or total, r["how"] or how, r["list"] or lst
            added = 0
            for row in r["rows"]:
                i = row.pop("_i", None)
                k = (row["href"] or row["text"]) if key == "href|text" else row["text"]
                if not k:
                    continue
                if i:
                    idx.add(i)
                if k not in seen:
                    seen[k] = row
                    row["_i"] = i
                    out.append(row)
                    added += 1
                else:
                    old = seen[k]
                    for f in css_f:
                        if _empty(old.get(f)) and not _empty(row.get(f)):
                            old[f] = row[f]  # rendered meanwhile
            still = still + 1 if not added else 0
            end = r["end"]
            if end and still >= 2:
                if again or rounds >= max_rounds or not gap()[1]:
                    break
                # Rows the list says it has never showed (rendered late, or skipped): once more from the top, slower.
                again, still, wait = True, 0, max(1.2, pause * 3)
                await self._guard(_vf(js, ITEM=item, BOX=container, SCROLL=False, STEP=float(step), FIELDS=css_f,
                                      ALL=bool(all), FIRST=False, TOP=True), 20)
            await _asyncio.sleep(wait)
        if again and out and not any(row["_i"] is None for row in out):
            out.sort(key=lambda row: row["_i"])  # the second pass added rows the first one skipped
        for row in out:
            row.pop("_i", None)
        done = "end reached" if end and still >= 2 else f"stopped after {rounds} rounds (max_rounds)"
        if left:
            done += (f"; read the list in {where}" + (f" ({scoped_to})" if scoped_to else "") + "; left out "
                     + ", ".join(f"{x['n']} matches in {x['label'] or 'another scroll box'}"
                                 + (f" (container={x['css']!r})" if x["css"] else "") for x in left[:3])
                     + "; all=True reads every match")
        says, never = gap()
        if never:
            shown = ", ".join(map(str, never[:6])) + (f" … ({len(never)} in all)" if len(never) > 6 else "")
            done += (f"; the {lst} says {says}" + (f" ({how} {total})" if how == "aria-rowcount" and says != total else f" ({how})")
                     + f" → incomplete: rows {shown}" + (f" ({'aria-rowindex' if how == 'aria-rowcount' else 'aria-posinset'})"
                                                        if idx else "")
                     + " never showed" + (" after a second, slower pass" if again else "")
                     + "; try pause=1.5, a container=, or the list's JSON (capture)")
        if fields or required:
            for row in out:
                for f, fn in fn_f.items():
                    row[f] = await _field_call(fn, row)
            done += "; " + _mark_missing(out, fields, required,
                                         lambda ix: ("row " if len(ix) == 1 else "rows ") + ", ".join(map(str, ix)))
        print(f"[collect: {len(out)} items, {done}]")
        await self._count_note("collect", out)
        return out

    async def capture(self, url_regex, action=None, *, wait=1.0, timeout=15, raw=False):
        """Record the JSON responses of the page's own fetch/XHR requests
        whose URL matches `url_regex` while `action` runs (an awaitable such
        as tab.click(text="Suchen"), or a function returning one; None: just
        listen `wait` s). Waits `wait` s more for late requests. Returns
        [{url, status, json}] (JSONP/anti-XSSI wrappers stripped); when that
        is empty, prints which fetch/XHR URLs the page did request meanwhile.
        A request that sent a body (a POST search) also has "post_data":
        its JSON parsed, a form body as {name: value}, else its text; masked
        (B-84: send it again with fetch_json(url, json=…)).
        raw=True also returns the matched answers that aren't JSON
        (protobuf, text): {url, status, type, text} for a text type (masked
        like text()), {url, status, type, body: bytes} otherwise (bytes are
        NOT masked: decode them and print only the fields you need).
        DevTools' network domain is on only meanwhile."""
        rx = _re.compile(url_regex)
        seen, done, other, mime, posts = {}, {}, [], {}, {}

        def on(msg):
            m, p = msg.get("method"), msg.get("params", {})
            if m == "Network.requestWillBeSent":
                q = p.get("request", {})
                if q.get("hasPostData") or q.get("postData"):
                    posts[p["requestId"]] = q.get("postData")
            elif m == "Network.responseReceived" and p.get("type") in ("XHR", "Fetch"):
                if rx.search(p["response"]["url"]):
                    seen[p["requestId"]] = {"url": p["response"]["url"], "status": p["response"]["status"]}
                    mime[p["requestId"]] = p["response"].get("mimeType") or ""
                else:
                    other.append(p["response"]["url"])
            elif m in ("Network.loadingFinished", "Network.loadingFailed"):
                done[p["requestId"]] = m == "Network.loadingFinished"

        async def body_of(rid):
            """{"post_data": …} of request `rid`, or {} when it sent none."""
            if rid not in posts:
                return {}
            text = posts[rid]
            if text is None:  # too big for the event: ask for it
                try:
                    text = (await self._cdp.send("Network.getRequestPostData", {"requestId": rid}, 5)).get("postData")
                except (RuntimeError, _asyncio.TimeoutError):
                    return {"post_data": None}
            return {"post_data": _capture_body(text)}

        self._cdp._listeners.append(on)
        try:
            await self._net_on()
            if action is not None:
                r = action() if callable(action) else action
                if _inspect.isawaitable(r):
                    await r
            await _asyncio.sleep(wait)
            deadline = _time.monotonic() + timeout
            while any(rid not in done for rid in seen) and _time.monotonic() < deadline:
                await _asyncio.sleep(0.1)
            out, skipped, binary = [], [], 0
            for rid, info in list(seen.items()):  # the listener adds to it meanwhile
                if not done.get(rid):
                    skipped.append(info["url"])
                    continue
                try:
                    b = await self._cdp.send("Network.getResponseBody", {"requestId": rid})
                except RuntimeError:
                    skipped.append(info["url"])  # no longer kept
                    continue
                data = _base64.b64decode(b["body"]) if b.get("base64Encoded") else b["body"].encode()
                info = {**info, **await body_of(rid)}
                try:
                    out.append({**info, "json": _mask_secrets(_json_body(data))})
                    continue
                except ValueError:
                    pass
                kind = mime.get(rid, "")
                if raw and (kind.startswith("text/") or _re.search(r"json|javascript|xml|urlencoded", kind)):
                    out.append({**info, "type": kind, "text": _mask_secrets(_mask_url(data.decode(errors="replace")))})
                elif raw:
                    out.append({**info, "type": kind, "body": data})
                else:
                    skipped.append(info["url"])  # not JSON
                    binary += 1
            if not out:
                urls = list(dict.fromkeys(_mask_url(u)[:160] for u in other))
                print(f"[capture: no JSON response matched {url_regex!r}"
                      + (f"; matched but not JSON or unfinished: {[_mask_url(u) for u in skipped[:5]]}" if skipped else "")
                      + (f" ({binary} not JSON: capture(…, raw=True) returns their bodies)" if binary else "")
                      + (f"; the page's other fetch/XHR meanwhile ({len(urls)}): " + ", ".join(urls[:15])
                         if urls else "; the page made no other fetch/XHR (cross-site iframes aren't seen)") + "]")
            return out
        finally:
            self._cdp._listeners.remove(on)
            await self._net_off()

    async def screenshot(self, path=None, *, selector=None):
        """Save a PNG of the viewport; returns its path (show it with `read`).
        selector="css": only that element (its first shown match, scrolled
        into view; the whole element, also beyond the viewport). Without
        `path`: /tmp/shop-<pid>-<n>.png (mode 600), deleted by this tab's
        close(), shop_close() and the kernel's exit; never delete
        screenshots with a wildcard (other agents' files). A `path` that
        isn't one (no .png/.jpg/.webp ending and no "/": "#img_captcha") is
        taken as selector=, and it says so. Screenshots show card numbers
        typed into fields."""
        if path and selector is None and "/" not in path and not _re.search(r"\.(png|jpe?g|webp)$", path, _re.I):
            print(f"[screenshot: {path!r} is no file path (.png): taken as selector={path!r}]")
            path, selector = None, path
        params = {"format": "png"}
        if selector is not None:
            box = await self._ev(_vf("""const el = VF.all(SEL).find(VF.shown);
              if (!el) return null;
              el.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
              const r = el.getBoundingClientRect();
              return [r.x + scrollX, r.y + scrollY, r.width, r.height];""", SEL=selector), 10)
            if not box or box[2] < 1 or box[3] < 1:
                raise ValueError(f"screenshot: no shown element matches {selector!r}{await self._where()}")
            params.update(captureBeyondViewport=True,
                          clip={"x": box[0], "y": box[1], "width": box[2], "height": box[3], "scale": 1})
        r = await self._cdp.send("Page.captureScreenshot", params)
        return self._shot_file(_base64.b64decode(r["data"]), path)

    def _shot_file(self, data, path=None, ext="png"):
        """Write screenshot/captcha_image bytes to `path` (mode 600), else to
        a new /tmp/shop-<pid>-<n>.<ext> that close() deletes."""
        if path:
            with open(path, "wb") as f:
                f.write(data)
            _os.chmod(path, 0o600)
            return path
        n = len(_SHOTS)
        while True:
            n += 1
            path = f"/tmp/shop-{_os.getpid()}-{n}.{ext}"
            try:
                fd = _os.open(path, _os.O_WRONLY | _os.O_CREAT | _os.O_EXCL, 0o600)
                break
            except FileExistsError:
                continue
        with _os.fdopen(fd, "wb") as f:
            f.write(data)
        _SHOTS[path] = self.id
        return path

    async def fetch_json(self, url, *, method="GET", body=None, json=None, headers=None, timeout=10, reveal_keys=None):
        """Fetch `url` from inside the page (its cookies and origin, like the
        site's own scripts) and return the parsed JSON (a JSONP callback or an
        anti-XSSI prefix is stripped, as in http_json). `body`: a dict/list
        (sent as JSON) or a string (a JSON string gets Content-Type
        application/json too, unless `headers` set one). json=obj: POST
        (unless method= says otherwise) of obj as JSON. At most one request
        per second per site (with shop_map and pdf_text); give up after
        `timeout` s. A GET of another origin that the page may not fetch
        (CORS: "Failed to fetch") is tried once more without the browser
        (http_json: no cookies), and it says so. reveal_keys=["key"]: values
        under those JSON keys stay unmasked (a public config key the next
        request needs; see _mask_reveal)."""
        if json is not None:
            if body is not None:
                raise ValueError("fetch_json takes body= or json=, not both")
            body, method = json, "POST" if method == "GET" else method
        reveal = _reveal_set(reveal_keys)
        page = await self._href()
        url = _urlparse.urljoin(page, _unmask_url(url))
        await _rate_wait(url)
        h = dict(headers or {})
        typed = any(k.lower() == "content-type" for k in h)
        if body is not None and not isinstance(body, str):
            body = _json.dumps(body)
            if not typed:
                h["Content-Type"] = "application/json"
        elif isinstance(body, str) and not typed and body.strip()[:1] in ("{", "["):
            try:
                _json.loads(body)
                h["Content-Type"] = "application/json"
            except ValueError:
                pass
        h.setdefault("Accept", "application/json")
        js = f"""(async () => {{
          const ac = new AbortController(), t = setTimeout(() => ac.abort(), {int(timeout * 1000)});
          try {{
            const r = await fetch({_json.dumps(url)}, {{ method: {_json.dumps(method)}, headers: {_json.dumps(h)},
              body: {_json.dumps(body)}, credentials: "include", signal: ac.signal }});
            return {{ status: r.status, text: await r.text() }};
          }} catch (e) {{ return {{ error: String(e) }}; }} finally {{ clearTimeout(t); }}
        }})()"""
        r = await self._guard(js, timeout + 10)
        if "error" in r:
            origin = lambda u: _urlparse.urlsplit(u)[:2]  # noqa: E731 — (scheme, host[:port])
            if ("Failed to fetch" in r["error"] and method.upper() == "GET" and origin(url) != origin(page)
                    and url.startswith(("http://", "https://"))):
                try:
                    data = await _asyncio.to_thread(http_json, url, {k: v for k, v in h.items() if k.lower() != "cookie"},
                                                    timeout=timeout, reveal_keys=reveal_keys)
                except Exception as e:  # noqa: BLE001 — one error type, as before
                    raise RuntimeError(f"fetch {_mask_url(url)}: {r['error']} (another origin: CORS?); without the"
                                       f" browser (http_json) too: {type(e).__name__}: {_mask_url(str(e))[:200]}") from None
                print(f"[fetch_json: the page may not fetch {_mask_url(url)} (another origin: CORS); fetched it once"
                      " without the browser instead (http_json: no cookies, no login)]")
                return data
            raise RuntimeError(f"fetch {_mask_url(url)}: {r['error']}")
        if not 200 <= r["status"] < 300:
            raise RuntimeError(f"fetch {_mask_url(url)}: HTTP {r['status']}: {_mask_json_text(r['text'])[:300]}")
        try:
            data = _json_body(r["text"])
        except ValueError:
            raise RuntimeError(f"fetch {_mask_url(url)}: not JSON: {_mask_json_text(r['text'])[:300]}") from None
        return _reveal_print("fetch_json", data, reveal) if reveal else _mask_secrets(data)

    async def page_json(self, keys=None, *, timeout=20):
        """The state a page embeds: next_data (#__NEXT_DATA__), ld_json (every
        script[type="application/ld+json"], as one flat list of objects: nested lists and @graph unpacked), nuxt (window.__NUXT__ or
        #__NUXT_DATA__), apollo (__APOLLO_STATE__), state (__INITIAL_STATE__
        or __PRELOADED_STATE__); missing ones are left out. `keys`: names to
        look for anywhere in the HTML as `"key": value` (up to 20 values
        each), returned under "keys"."""
        js = r"""
          const safe = (v) => {
            try {
              const seen = new WeakSet();
              return JSON.parse(JSON.stringify(v, (k, x) => {
                if (typeof x === "function") return undefined;
                if (x && typeof x === "object") { if (seen.has(x)) return undefined; seen.add(x); }
                return x;
              }));
            } catch { return null; }
          };
          const parse = (s) => { try { return JSON.parse(s); } catch { return null; } };
          const out = {};
          const nd = document.getElementById("__NEXT_DATA__");
          if (nd) out.next_data = parse(nd.textContent);
          // One flat list of objects: nested arrays and @graph containers unpacked (SH-16).
          const flat = (x) => Array.isArray(x) ? x.flatMap(flat)
            : x && typeof x === "object" && Array.isArray(x["@graph"]) ? x["@graph"].flatMap(flat) : x ? [x] : [];
          const ld = [...document.querySelectorAll('script[type="application/ld+json"]')].flatMap((s) => flat(parse(s.textContent)));
          if (ld.length) out.ld_json = ld;
          const nuxt = window.__NUXT__ ?? parse(document.getElementById("__NUXT_DATA__")?.textContent ?? "null");
          if (nuxt) out.nuxt = safe(nuxt);
          if (window.__APOLLO_STATE__) out.apollo = safe(window.__APOLLO_STATE__);
          const st = window.__INITIAL_STATE__ ?? window.__PRELOADED_STATE__;
          if (st) out.state = safe(st);
          if (KEYS.length) {
            const html = document.documentElement.outerHTML, found = {};
            for (const k of KEYS) {
              const e = k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
              const rx = new RegExp(`["']?${e}["']?\\s*[:=]\\s*("(?:[^"\\\\]|\\\\.){0,300}"|-?\\d+(?:\\.\\d+)?(?:e[+-]?\\d+)?|true|false|null)`, "g");
              const vals = [];
              for (const m of html.matchAll(rx)) { const v = parse(m[1]); vals.push(v === null && m[1] !== "null" ? m[1] : v); if (vals.length >= 20) break; }
              found[k] = vals;
            }
            out.keys = found;
          }
          return out;"""
        return _mask_secrets(await self._guard(_vf(js, KEYS=list(keys or [])), timeout))

    async def logged_out(self):
        """The phrase on the page that says the session ended ("Sitzung
        beendet", "Tut uns Leid", "session expired", …, and those in the site
        note's `logged out:` line), or None."""
        url = await self.url()
        host = _urlparse.urlsplit(url).hostname or ""
        phrases = _note_logged_out(_site(host)) if host else []
        phrases += _LOGGED_OUT
        js = r"""
          const t = VF.visibleText(document.body).toLowerCase().replace(/\s+/g, " ");
          return P.find((p) => t.includes(p)) ?? null;"""
        return await self._guard(_vf(js, P=phrases))

    async def wait_captcha(self, timeout=30):
        """Wait until a Friendly Captcha on the page has solved itself (it
        works in the background; this clicks nothing). True when solved,
        False after `timeout` s, None when the page has none. Only where
        Kirill's rules allow captcha handling."""
        js = r"""
          const f = VF.all('[name="frc-captcha-solution"], [name="frc-captcha-response"]');
          if (!f.length) return "none";
          return f.every((e) => e.value && !e.value.startsWith(".")) ? "done" : "pending";"""
        deadline = _time.monotonic() + timeout
        while True:
            s = await self._ev(_vf(js), 10)
            if s != "pending":
                return None if s == "none" else True
            if _time.monotonic() > deadline:
                return False
            await _asyncio.sleep(0.5)

    async def _captcha_find(self, fs, deep=False):
        """The page's captcha: {kind, box: its checkbox frame, chal: its
        image challenge frame, page: the document with its token field,
        invisible: the box is an invisible/v3 widget (reCAPTCHA size=invisible,
        hCaptcha checkbox-invisible: no checkbox, it runs on submit)}, or
        None. Pages without frames or captcha markup cost one evaluation;
        `deep` looks at the frames anyway (Cloudflare's managed challenge
        keeps its widget in a closed shadow root, out of querySelector's sight)."""
        if not deep and not await self._ev(
                "!!document.querySelector('iframe, frame, .frc-captcha, .cf-turnstile, [data-sitekey]')", 5):
            return None
        if await self._ev("!!document.querySelector('.frc-captcha, [name=frc-captcha-solution], [name=frc-captcha-response]')", 5):
            return {"kind": "friendly"}
        await fs.load(fresh=0)
        found, invisible = {}, set()
        for d in fs.docs:
            if "hcaptcha" in d.host and "hcaptcha.html" in d.url:
                try:
                    part = await d.ev("location.hash", 5)
                except _FRAME_ERRORS:
                    continue
                kind, role = "hcaptcha", "chal" if "frame=challenge" in part else "box" if "frame=checkbox" in part else None
                if "checkbox-invisible" in part or "size=invisible" in part:
                    invisible.add(id(d))
            elif "/recaptcha/" in d.url and ("/anchor" in d.url or "/bframe" in d.url):
                kind, role = "recaptcha", "box" if "/anchor" in d.url else "chal"
                if "size=invisible" in d.url:
                    invisible.add(id(d))
            elif d.host == "challenges.cloudflare.com":
                kind, role = "turnstile", "box"
            elif d.host.endswith("frcapi.com"):
                return {"kind": "friendly"}
            else:
                continue
            c = found.setdefault(kind, {"kind": kind, "box": None, "chal": None})
            if role and (c[role] is None or (d.shown and not c[role].shown)):
                c[role] = d
        for c in found.values():
            c["page"] = (c["box"] or c["chal"]).parent
            c["invisible"] = c["box"] is not None and id(c["box"]) in invisible
            return c
        return None

    @staticmethod
    def _captcha_shows(d):
        """Whether a challenge frame is open (a hidden one waits off-page)."""
        return bool(d is not None and d.shown and d.box and d.box[2] >= 100 and d.box[3] >= 100
                    and d.box[1] + d.box[3] > 0)

    async def _captcha_token(self, c):
        try:
            return bool(await self._ev(_CAPTCHA_TOKEN_JS, 5, c["page"]))
        except (RuntimeError, _asyncio.TimeoutError):
            return False

    def _captcha_drop(self):
        """Delete the last montage."""
        if self._captcha:
            _shutil.rmtree(self._captcha["dir"], ignore_errors=True)
            _SHOTS.pop(self._captcha["dir"], None)
        self._captcha = None

    async def _captcha_montage(self, c, fs):
        """{prompt, image, tile} of the open challenge: 4 screenshots of its
        frame 0.8 s apart (animated challenges show only some icons at a
        time), each with a labelled grid every 50 px, tiled 2×2."""
        chal = c["chal"]
        try:
            await fs._owner(chal, "scroll")
        except _FRAME_ERRORS:
            pass
        await fs._measure(chal)
        x, y, w, h = chal.box
        try:
            prompt = _re.sub(r"\s+", " ", await chal.ev(_CAPTCHA_PROMPT_JS, 5) or "").strip()[:300] or None
        except (RuntimeError, _asyncio.TimeoutError):
            prompt = None
        sx, sy = await self._ev("[scrollX, scrollY]", 5)
        tmp = _tempfile.mkdtemp(prefix="vf-captcha-")  # mode 700
        _SHOTS[tmp] = self.id
        self._captcha = {"dir": tmp, "frame": chal.frame_id, "kind": c["kind"]}
        tiles = []
        for i in range(4):
            await _asyncio.sleep(0.8 if i else 1.0)  # the first: its pictures load after it opens
            r = await self._cdp.send("Page.captureScreenshot", {
                "format": "png", "clip": {"x": x + sx, "y": y + sy, "width": w, "height": h, "scale": 1}})
            png = _base64.b64decode(r["data"])
            tiles.append(_os.path.join(tmp, f"shot{i}.png"))
            with open(_os.open(tiles[-1], _os.O_WRONLY | _os.O_CREAT, 0o600), "wb") as f:
                f.write(png)
        tw, th = int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")
        lines = " ".join(f"line {v},0 {v},{th}" for v in range(50, tw, 50)) + " " + \
            " ".join(f"line 0,{v} {tw},{v}" for v in range(50, th, 50))
        labels = " ".join(f"text {v + 2},10 '{v}'" for v in range(100, tw, 100)) + " " + \
            " ".join(f"text 2,{v - 2} '{v}'" for v in range(100, th, 100))
        image = _os.path.join(tmp, "captcha.png")
        await _run([_MAGICK, *tiles, "-fill", "none", "-stroke", "rgba(255,0,255,0.35)", "-draw", lines,
                    "-stroke", "none", "-fill", "rgba(200,0,200,0.9)", "-font", _FONT, "-pointsize", "11",
                    "-draw", labels, "(", "-clone", "0,1", "+append", ")", "(", "-clone", "2,3", "+append", ")",
                    "-delete", "0-3", "-append", image], 30)
        _os.chmod(image, 0o600)
        for p in tiles:
            _os.unlink(p)
        self._captcha.update(image=image, tile=[tw, th], ratio=tw / w)
        return {"prompt": prompt, "image": image, "tile": [tw, th]}

    async def _captcha_after(self, fs, site, mode, timeout, kind):
        """The state after a click (mode "checkbox", "next", "verify" or
        "points"): solved as soon as the token is there; else, after the
        checkbox, ≤ `timeout` s for a challenge to open, after Next ≤ 1.5 s
        for its next round, after Verify ≤ `timeout` s: a challenge still
        open then is a failed try. An open challenge comes with its montage.
        B-86 (DeepSeek: "failed", yet the code mail had gone out): before a
        "failed", the page is checked for its next step that wasn't there
        when captcha() began (a code field, "Resend in 51s", "code sent",
        another page): then "solved", `next` naming it."""
        wait = {"checkbox": timeout, "next": min(timeout, 1.5), "verify": timeout, "points": 0.3}[mode]
        deadline, c, opened = _time.monotonic() + wait, None, False
        while True:
            await _asyncio.sleep(0.3)
            c = await self._captcha_find(fs)
            if c and c["kind"] != "friendly":
                kind = c["kind"]
                if await self._captcha_token(c):
                    self._captcha_drop()
                    return _captcha_state(kind, "solved", _captcha_failures(site))
                opened = self._captcha_shows(c["chal"])
            else:
                opened = False
            if _time.monotonic() > deadline or (opened and mode == "checkbox"):
                break
        if not opened or mode == "verify":
            nxt = await self._captcha_moved()
            if nxt:
                self._captcha_drop()
                return _captcha_state(kind, "solved", _captcha_failures(site), next=nxt,
                                      note="no token seen, but the page shows its next step: " + "; ".join(nxt)
                                      + " (go on with that step)")
        self._captcha_drop()
        fails = _captcha_failures(site, add=mode == "verify" and opened)
        if not opened:
            return _captcha_state(kind, "failed", fails,
                                  note="no token, and no challenge open: look at the page (text(), screenshot())")
        if len(fails) >= 3:
            return _captcha_state(kind, "blocked", fails, note="3 failed tries in 30 min: stop and record it")
        return _captcha_state(kind, "failed" if mode == "verify" else "challenge", fails,
                              **await self._captcha_montage(c, fs))

    async def _captcha_steps(self):
        """[the page's host+path, then its next-step signs: "code field 'X'",
        "says 'Resend in 51s'"] (B-86); [] while it loads."""
        try:
            return await self._ev(_vf(_CAPTCHA_NEXT_JS), 5) or []
        except (RuntimeError, _asyncio.TimeoutError):
            return []

    async def _captcha_moved(self):
        """The next-step signs that weren't there when captcha() began: a
        new code field or sent notice, or another page ("now on <path>")."""
        base = getattr(self, "_captcha_base", None)
        now = await self._captcha_steps()
        if not base or not now:
            return []
        new = [s for s in now[1:] if s not in base[1:]]
        if now[0] != base[0]:
            new.append(f"now on {_mask_url(now[0])}")
        return new

    async def _captcha_ready(self, box):
        """Wait ≤ 5 s until the checkbox frame's script runs (a click before
        that does nothing)."""
        ready = ("document.readyState === 'complete' && (location.hostname === 'challenges.cloudflare.com'"
                 " || !!document.querySelector('#checkbox, #recaptcha-anchor, [role=checkbox]'))")
        for _ in range(25):
            try:
                if await box.ev(ready, 5):
                    return
            except (RuntimeError, _asyncio.TimeoutError):
                pass
            await _asyncio.sleep(0.2)

    async def _captcha_press(self, fs, box):
        """Click the checkbox in frame `box` with the mouse, after scrolling the
        widget's <iframe>s into view, outermost first (DD-08: "off screen").
        Returns what covers it instead (then nothing is clicked), else None."""
        chain, n = [], box
        while n.parent is not None:
            chain.append(n)
            n = n.parent
        for c2 in reversed(chain):
            try:
                await fs._owner(c2, "scroll")
            except (RuntimeError, _asyncio.TimeoutError, KeyError, TypeError):
                pass
        x, y, cover = await fs.point(box, min(30, box.box[2] / box.sx * 0.1), box.box[3] / box.sy / 2)
        if cover and "off screen" in cover:
            await _asyncio.sleep(0.3)  # smooth scrolling
            x, y, cover = await fs.point(box, min(30, box.box[2] / box.sx * 0.1), box.box[3] / box.sy / 2)
        if cover and box.parent is not None and box.parent.parent is None and await self._shadow_hit(fs, box, x, y):
            cover = None  # its <iframe> sits in a closed shadow root: the page's hit test sees only the root's host
        if cover:
            return cover
        await self._cdp.click_at(x, y)
        return None

    @staticmethod
    async def _shadow_hit(fs, d, X, Y):
        """Whether the top-level point (X, Y) hits frame d's <iframe> as its
        own (closed) shadow root sees it (Cloudflare's widgets: there the
        document's elementFromPoint gives the root's host, a "div")."""
        p, oid = d.parent, fs.owners.get(d.frame_id)
        if oid is None:
            return False
        try:
            r = await p.cdp.send("Runtime.callFunctionOn", {
                "objectId": oid, "functionDeclaration": _SHADOW_HIT_JS, "returnByValue": True,
                "arguments": [{"value": (X - p.ox) / p.sx}, {"value": (Y - p.oy) / p.sy}]}, 5)
        except _FRAME_ERRORS:
            return False
        return r.get("result", {}).get("value") is True

    async def _cf_managed(self):
        """Cloudflare's managed challenge on the page: {type, title, said}, or
        None; {busy: True} while a document loads or the page is between two."""
        try:
            return await self._ev(_CF_MANAGED_JS, 5)
        except (RuntimeError, _asyncio.TimeoutError):
            return {"busy": True}

    async def _cf_gone(self, wait):
        """True once the managed challenge has given way to another page
        (polled every 0.5 s, ≤ `wait` s); that page is then settled."""
        deadline = _time.monotonic() + wait
        while _time.monotonic() < deadline:
            await _asyncio.sleep(0.5)
            if await self._cf_managed() is None:
                await self._settle(cap=5)
                return True
        return False

    async def _cf_pass(self, m, wait, fs, site, fails):
        """captcha() on Cloudflare's managed challenge `m`: ≤ `wait` s for it
        to pass by itself; else its checkbox, if one shows, is clicked (one of
        the 3 tries) and ≤ `wait` s more; else (or still there) the page
        loads once more and ≤ `wait` s again. Returns state "managed" with
        `passed` and what happened."""
        t0, steps = _time.monotonic(), []
        passed = await self._cf_gone(wait)
        if not passed:
            steps.append(f"it did not pass by itself in {wait:.0f} s")
            c = None if len(fails) >= 3 else await self._captcha_find(fs, deep=True)
            box = c["box"] if c and c["kind"] == "turnstile" else None
            if box is not None and box.shown:
                await self._captcha_ready(box)
                cover = await self._captcha_press(fs, box)
                if cover:
                    steps.append(f"its checkbox is covered by {cover}")
                else:
                    passed = await self._cf_gone(wait)
                    steps.append("its checkbox was clicked" + ("" if passed else f", it still showed {wait:.0f} s later"))
                    if not passed:
                        fails = _captcha_failures(site, add=True)
        if not passed:
            u = _urlparse.urlsplit(await self._href())
            q = _urlparse.parse_qsl(u.query, keep_blank_values=True)
            keep = [(k, v) for k, v in q if not k.startswith("__cf_chl")]  # its own retry tokens
            url = _urlparse.urlunsplit(u._replace(query=_urlparse.urlencode(keep))) if len(keep) < len(q) else u.geturl()
            await self.goto(url)
            passed = await self._cf_gone(wait)
            steps.append("the page was loaded once more" + ("" if passed else f", and the check still showed {wait:.0f} s later"))
        dt = _time.monotonic() - t0
        what = f"Cloudflare's managed check ({m.get('said') or m.get('title') or 'Just a moment…'!r})"
        if passed:
            note = f"{what} passed after {dt:.1f} s" + (f" ({'; '.join(steps)})" if steps else "") + \
                f"; the page is now {await self.title()!r}"
        else:
            note = f"{what} did not pass in {dt:.0f} s: {'; '.join(steps)}. The site turns this browser away now:" \
                " don't retry in a loop; use fetch_text() or another source, or record it"
        return _captcha_state("cloudflare", "managed", fails, passed=passed, waited=round(dt, 1), note=note)

    async def captcha(self, *, timeout=8):
        """Find the captcha on the page (hCaptcha, reCAPTCHA v2, Cloudflare
        Turnstile, Friendly Captcha), click its checkbox, wait ≤ `timeout` s:
        {kind, state, prompt, image, tile, attempt, left}. state: "solved",
        "none" (no captcha), "challenge" (an image puzzle: `prompt` is its
        instruction, `image` a PNG to `read`: 4 frames 0.8 s apart, tiled 2×2,
        each `tile` [w, h] px with a grid; then captcha_click), "invisible"
        (an invisible reCAPTCHA/v3 badge or invisible hCaptcha with no
        challenge open: nothing to click; submit the form normally, and call
        captcha() again only if a picture challenge opens then), "failed", or
        "blocked": 3 failed tries on this site in 30 min (counted for all
        agents), so stop. "solved" with `next`: no token was seen, but the
        page went on to its next step (a code field, "code sent"). Friendly
        Captcha is only waited for, never clicked.
        Cloudflare's managed challenge (a whole page "Just a moment…" /
        "Performing security verification", mostly without a checkbox): kind
        "cloudflare", state "managed", `passed` True/False, `waited` s and a
        `note` saying what happened: ≤ max(timeout, 15) s for it to pass by
        itself, then its checkbox if one shows, else one reload, ≤ that again.
        Only where Kirill's rules allow solving captchas."""
        site = _site(_urlparse.urlsplit(await self.url()).hostname or "")
        self._captcha_drop()
        fails = _captcha_failures(site)
        fs = self._frames()
        try:
            m = await self._cf_managed()
            if m and not m.get("busy"):
                return await self._cf_pass(m, max(timeout, 15), fs, site, fails)
            c = await self._captcha_find(fs)
            if c is None:
                return _captcha_state(None, "none", fails)
            if len(fails) >= 3:
                return _captcha_state(c["kind"], "blocked", fails, note="3 failed tries in 30 min: stop and record it")
            if c["kind"] == "friendly":
                done = await self.wait_captcha(timeout=max(timeout, 30))
                return _captcha_state("friendly", "solved" if done else "failed", fails,
                                      **({} if done else {"note": "it did not solve itself in 30 s"}))
            if await self._captcha_token(c):
                return _captcha_state(c["kind"], "solved", fails)
            self._captcha_base = await self._captcha_steps()
            if self._captcha_shows(c["chal"]):
                return _captcha_state(c["kind"], "challenge", fails, **await self._captcha_montage(c, fs))
            if c.get("invisible"):
                return _captcha_state(c["kind"], "invisible", fails,
                                      note="an invisible captcha (no checkbox; it runs when the form is sent): submit"
                                      " the form normally; call captcha() again only if a picture challenge opens")
            if c["kind"] == "turnstile":
                for _ in range(8):  # it often passes without a click
                    await _asyncio.sleep(0.4)
                    if await self._captcha_token(c):
                        return _captcha_state(c["kind"], "solved", fails)
            box = c["box"]
            if box is None or not box.shown:
                return _captcha_state(c["kind"], "failed", fails, note="its checkbox doesn't show")
            await self._captcha_ready(box)
            for attempt in range(2):
                cover = await self._captcha_press(fs, box)
                if cover:
                    return _captcha_state(c["kind"], "failed", fails, note=f"its checkbox is covered by {cover}")
                st = await self._captcha_after(fs, site, "checkbox", timeout / 2 if attempt == 0 else timeout, c["kind"])
                if st["state"] != "failed":
                    return st
                await fs.load(fresh=0)
                box = fs.by_frame(box.frame_id) or box
            return st
        finally:
            await fs.close()

    async def drag(self, start, end=None, *, to=None, steps=15, timeout=10):
        """Drag with the left button held: from `start`, (x, y) in the tab's
        CSS px (the viewport, as screenshot() shows it) or a CSS selector
        (its centre, scrolled into view first), to `end` / `to`, (x, y) in
        the same px, in `steps` smooth moves (slider puzzles, sortable
        lists). Mouse and pointer events, like a hand; in the top document.
        captcha_click() drags in a captcha's montage."""
        end = end if end is not None else to
        if end is None:
            raise ValueError("drag needs where to: drag(start, (x, y)) or drag(selector, to=(x, y))")
        if isinstance(start, str):
            js = _vf("""const el = VF.all(SEL).find(VF.shown);
              if (!el) return null;
              el.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
              const r = el.getBoundingClientRect();
              return [r.x + r.width / 2, r.y + r.height / 2];""", SEL=start)
            deadline = _time.monotonic() + timeout
            while not (xy := await self._ev(js, 5)):
                if _time.monotonic() > deadline:
                    raise RuntimeError(f"no visible element {start!r} to drag{await self._where()}")
                await _asyncio.sleep(0.25)
            start = xy
        (x1, y1), (x2, y2) = start, end
        await self._act(self._cdp.drag_at(x1, y1, x2, y2, steps))
        return f"dragged from ({round(x1)}, {round(y1)}) to ({round(x2)}, {round(y2)})"

    async def captcha_click(self, points=(), then=None, *, timeout=8):
        """Click the points [(x, y), …] of the montage captcha() gave (any
        tile: taken modulo the tile size; a 4-tuple (x1, y1, x2, y2) drags
        from the first point to the second, both in that tile), then, with
        `then` ("next", "verify", "skip"), the challenge's button. Returns
        captcha()'s dict: "solved" once the token is there, "challenge" with
        the next round's montage, "failed" (a Verify that led to a new
        challenge: one of the 3 tries) with the new one, or "blocked"."""
        st = self._captcha
        if not st or "tile" not in st:
            raise RuntimeError("no captcha challenge open here: captcha() first")
        site = _site(_urlparse.urlsplit(await self.url()).hostname or "")
        fails = _captcha_failures(site)
        if len(fails) >= 3:
            self._captcha_drop()
            return _captcha_state(st["kind"], "blocked", fails, note="3 failed tries in 30 min: stop and record it")
        fs = self._frames()
        try:
            await fs.load(fresh=0)
            chal = fs.by_frame(st["frame"])
            if chal is None or not self._captcha_shows(chal):
                return await self._captcha_after(fs, site, "points", timeout, st["kind"])
            (tw, th), k = st["tile"], st["ratio"]
            for p in points:
                ends = [await fs.point(chal, (px % tw) / k / chal.sx, (py % th) / k / chal.sy)
                        for px, py in ([p[:2], p[2:4]] if len(p) == 4 else [p[:2]])]
                if all(e[0] is not None for e in ends):
                    if len(ends) == 2:
                        await self._cdp.drag_at(ends[0][0], ends[0][1], ends[1][0], ends[1][1])
                    else:
                        await self._cdp.click_at(ends[0][0], ends[0][1])
                await _asyncio.sleep(0.3)
            verify = False
            if then:
                b = await chal.ev(_CAPTCHA_BUTTON_JS, 5)
                if not b:
                    raise RuntimeError("the challenge shows no Next/Verify/Skip button" + await self._where())
                x, y, _ = await fs.point(chal, b["x"], b["y"])
                await self._cdp.click_at(x, y)
                verify = then == "verify" or bool(_re.search(r"verif|prüf|submit|bestätig", b["label"], _re.I))
            return await self._captcha_after(fs, site, ("verify" if verify else "next") if then else "points",
                                             timeout, st["kind"])
        finally:
            await fs.close()

    async def captcha_image(self, css, *, path=None, timeout=10):
        """The picture of an inline image captcha ("Sicherheitsabfrage", a
        rotating-letter circle; B-86, MONEY-0229: a data: URL <img>) as a
        0600 file to `read`; returns its path. `css`: the <img> (its data:
        URL is decoded as it is), a <canvas>, or any element (then its
        screenshot, as screenshot(selector=)). Without `path` it goes like a
        screenshot (deleted by close()/shop_close()). Reading it and typing
        the answer yourself is allowed (Kirill's rules)."""
        deadline = _time.monotonic() + timeout
        js = _vf("""const el = VF.all(SEL).find(VF.shown) ?? null;
          if (!el) return null;
          const img = el.localName === "img" ? el : el.localName === "canvas" ? null : el.querySelector("img");
          const src = img ? img.currentSrc || img.src : "";
          if (src.startsWith("data:")) return { data: src };
          if (el.localName === "canvas") { try { return { data: el.toDataURL("image/png") }; } catch (e) {} }
          return { shot: true };""", SEL=css)
        while not (r := await self._ev(js, 5)):
            if _time.monotonic() > deadline:
                raise ValueError(f"captcha_image: no shown element matches {css!r}{await self._where()}")
            await _asyncio.sleep(0.25)
        if r.get("shot"):
            return await self.screenshot(path, selector=css)
        m = _re.match(r"data:([^;,]*)((?:;[^;,]*)*),(.*)", r["data"], _re.S)
        if not m:
            raise ValueError(f"captcha_image: {css!r} holds no readable data: URL")
        mime = (m.group(1) or "").lower()
        data = _base64.b64decode(m.group(3)) if ";base64" in m.group(2).lower() else _urlparse.unquote_to_bytes(m.group(3))
        ext = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg", "image/webp": "webp", "image/gif": "gif"}.get(mime)
        if ext is None:  # svg, bmp, …: a PNG that `read` shows
            tmp = _tempfile.mkdtemp(prefix="vf-capimg-")
            try:
                src = _os.path.join(tmp, "in." + ((_mimetypes.guess_extension(mime) or ".img").lstrip(".")))
                with open(_os.open(src, _os.O_WRONLY | _os.O_CREAT, 0o600), "wb") as f:
                    f.write(data)
                await _run([_MAGICK, src, _os.path.join(tmp, "out.png")], 30)
                with open(_os.path.join(tmp, "out.png"), "rb") as f:
                    data, ext = f.read(), "png"
            finally:
                _shutil.rmtree(tmp, ignore_errors=True)
        return self._shot_file(data, path, ext)

    async def _download(self, url, timeout=60, meta=False):
        """The bytes at `url`, fetched with this tab's session: from the page
        (cookies, Referer), else, for other sites (CORS), by the browser's
        network stack with the cookies. meta=True: (bytes, {type, disposition,
        url}) with the answer's Content-Type, Content-Disposition, final URL."""
        url = _urlparse.urljoin(await self._href(), _unmask_url(url))
        await _rate_wait(url)
        js = f"""(async () => {{
          const ac = new AbortController(), t = setTimeout(() => ac.abort(), {int(timeout * 1000)});
          try {{
            const r = await fetch({_json.dumps(url)}, {{ credentials: "include", signal: ac.signal }});
            if (!r.ok) return {{ status: r.status }};
            const b = new Uint8Array(await r.arrayBuffer());
            let s = "";
            for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode.apply(null, b.subarray(i, i + 0x8000));
            return {{ status: r.status, b64: btoa(s), type: r.headers.get("content-type"),
                      disposition: r.headers.get("content-disposition"), url: r.url }};
          }} catch (e) {{ return {{ error: String(e) }}; }} finally {{ clearTimeout(t); }}
        }})()"""
        r = await self._guard(js, timeout + 10)
        if r.get("b64") is not None:
            data = _base64.b64decode(r["b64"])
            return (data, {k: r.get(k) for k in ("type", "disposition", "url")}) if meta else data
        why = r.get("error") or f"HTTP {r.get('status')}"
        tree = await self._cdp.send("Page.getFrameTree", timeout=5)
        res = (await self._cdp.send("Network.loadNetworkResource", {
            "frameId": tree["frameTree"]["frame"]["id"], "url": url,
            "options": {"disableCache": False, "includeCredentials": True}}, timeout))["resource"]
        if not res.get("success") or not 200 <= (res.get("httpStatusCode") or 0) < 300:
            raise RuntimeError(f"download {_mask_url(url)}: {why}; the browser got HTTP {res.get('httpStatusCode')}"
                               f" {res.get('netErrorName') or ''}".rstrip())
        chunks = []
        try:
            while True:
                c = await self._cdp.send("IO.read", {"handle": res["stream"], "size": 1 << 20}, timeout)
                chunks.append(_base64.b64decode(c["data"]) if c.get("base64Encoded") else c["data"].encode())
                if c.get("eof"):
                    break
        finally:
            try:
                await self._cdp.send("IO.close", {"handle": res["stream"]}, 5)
            except (RuntimeError, ConnectionError, _asyncio.TimeoutError):
                pass
        data = b"".join(chunks)
        if not meta:
            return data
        h = {k.lower(): v for k, v in (res.get("headers") or {}).items()}
        return data, {"type": h.get("content-type"), "disposition": h.get("content-disposition"), "url": url}

    async def download(self, click=None, *, url=None, timeout=30, **kw):
        """Download a file; returns {path, name, size, mime}. What starts it:
        `click`, a CSS selector or else the text of the link/button (or an
        awaitable / a function returning one, like capture()'s action);
        click()'s keywords (text=, ref=, within=, frame=, …) work too. Or
        `url`: the file there, fetched with this tab's session (name from its
        Content-Disposition). Chrome saves it into a 0700 directory of this
        kernel (never ~/Downloads) under the site's file name, mode 600; a
        file the page builds itself (a Blob: PayPal's CSV) is taken from the
        page when Chrome saves none within 2 s. Only downloads of this tab
        (its frames, the windows it opens) count; a second one the same
        click started is cancelled. TimeoutError when none came within
        `timeout` s, and already after 5 s when the click started nothing at
        all (no download, no request, no new window, no Blob), naming the
        page's download links/buttons. A selector or ref= that is a container
        (observe()'s tab-panel wrapper) clicks the one download link/button
        inside it, and says so; with several inside it raises ValueError
        naming them. The files go with shop_close() and the kernel's exit
        (not with close())."""
        if url is not None:
            if click is not None or kw:
                raise ValueError("download takes click=… (or click()'s keywords) or url=…, not both")
            data, info = await self._download(url, timeout, meta=True)
            name = _dl_disposition(info.get("disposition")) or _dl_url_name(info.get("url") or url)
            return _dl_keep(name, data=data, mime=info.get("type"))
        if click is None and not kw:
            raise ValueError("download needs click= (a selector, a text or an action), click()'s text=/ref=, or url=")
        if isinstance(click, str) and not kw.get("text") and not await self._ev(_vf(
                "try { return VF.all(SEL).length > 0; } catch (e) { return false; }", SEL=click), 5):
            kw, click = {**kw, "text": click}, None  # no such CSS match: it is the link's text
        target = await self._dl_target(click, kw)
        if target is not None:
            click, kw = target, {k: v for k, v in kw.items() if k != "ref"}
        quiet = kw.get("frame") is None  # a frame's own requests aren't seen here
        shared = _dl_shared_dir()
        b = await _shop_browser_cdp()
        st = {"infos": {}, "begun": {}, "end": {}, "got": {}}

        def on(msg):
            m, p = msg.get("method"), msg.get("params", {})
            if m in ("Target.targetCreated", "Target.targetInfoChanged"):
                st["infos"][p["targetInfo"]["targetId"]] = p["targetInfo"]
            elif m == "Browser.downloadWillBegin":
                st["begun"][p["guid"]] = p
            elif m == "Browser.downloadProgress":
                st["got"][p["guid"]] = p.get("receivedBytes", 0)
                if p.get("state") in ("completed", "canceled"):
                    st["end"][p["guid"]] = p

        frames, asked = set(), set()

        async def mine():
            """The guids of this tab's downloads, in the order they began."""
            infos = list(st["infos"].values())
            fam = _target_family(infos, self.id)
            for t in infos:  # the windows it opened (a link with target=_blank that is a download)
                if t.get("type") == "page" and t.get("openerId") in fam:
                    fam |= _target_family(infos, t["targetId"])
            new = {p["frameId"] for p in st["begun"].values()} - fam - frames - asked
            if new:  # a frame of the page itself? (other agents' downloads are seen too)
                asked.update(new)
                todo = [(await self._cdp.send("Page.getFrameTree", timeout=5))["frameTree"]]
                while todo:
                    n = todo.pop()
                    frames.add(n["frame"]["id"])
                    todo += n.get("childFrames", [])
            return [g for g, p in st["begun"].items() if p["frameId"] in fam | frames]

        net = {"n": 0}

        def on_net(msg):
            """Counts the requests the page starts (trackers and pixels left out)."""
            if msg.get("method") == "Network.requestWillBeSent":
                p = msg.get("params", {})
                if p.get("type") not in _DL_IDLE_TYPES and not _DL_TRACKER.search(p.get("request", {}).get("url", "")):
                    net["n"] += 1

        b._listeners.append(on)
        self._cdp._listeners.append(on_net)
        try:
            await self._net_on()
            await b.send("Target.setDiscoverTargets", {"discover": True})
            before = set(st["infos"])  # the targets there were; a window the click opens comes after
            # Browser-wide, but every agent sets the same; it ends with the last connection that set it.
            await b.send("Browser.setDownloadBehavior", {"behavior": "allowAndName", "downloadPath": shared,
                                                         "eventsEnabled": True})
            try:
                await self._ev(_BLOB_HOOK_JS.replace("__ON__", "true"), 5)
                await self._ev(_vf(_DL_WATCH_JS, PRE=True), 5)
            except (RuntimeError, _asyncio.TimeoutError):
                pass  # mid-navigation: Chrome's own downloads still count
            deadline = _time.monotonic() + timeout
            if click is None:
                said = await self.click(**kw)
            else:
                r = click() if callable(click) and not _inspect.isawaitable(click) else click
                if isinstance(r, str):
                    r = self.click(r, **kw)
                said = await r if _inspect.isawaitable(r) else r
            what = repr(said) if isinstance(said, str) else "the action"
            blob_at, got, clicked, watched = None, None, _time.monotonic(), 0.0
            while got is None:
                own = await mine()
                done = [g for g in own if st["end"].get(g, {}).get("state") == "completed"]
                if done:
                    got = ("file", done[0])
                    break
                if own and all(g in st["end"] for g in own):
                    try:
                        blobs = [x for x in await self._ev(_BLOB_LIST_JS, 5) or [] if x["size"]]
                    except (RuntimeError, _asyncio.TimeoutError):
                        blobs = []
                    if blobs:  # the page revoked its object URL before Chrome read it; the Blob is still here
                        x = _dl_blob_pick(blobs)
                        got = ("blob", {**x, "name": x.get("name") or st["begun"][own[0]]["suggestedFilename"]})
                        break
                    raise RuntimeError(f"the download of {st['begun'][own[0]]['suggestedFilename']!r} was cancelled"
                                       f" (by the browser or the site){await self._where()}")
                if not own:
                    try:
                        blobs = await self._ev(_BLOB_LIST_JS, 5) or []
                    except (RuntimeError, _asyncio.TimeoutError):
                        blobs = []  # mid-navigation
                    blobs = [x for x in blobs if x["size"]]
                    now = _time.monotonic()
                    if not blobs and (now - watched >= 1 or (now - clicked >= 5 > watched - clicked)):
                        watched = now  # B-80: an error dialog or a form instead of the file
                        await self._dl_watch(what, clicked)
                    if (quiet and not blobs and not net["n"] and _time.monotonic() - clicked >= 5
                            and not any(t.get("openerId") in _target_family(list(st["infos"].values()), self.id)
                                        for i, t in st["infos"].items() if i not in before)):
                        raise TimeoutError(f"nothing started within 5 s after {what}: no download, no request, no new"
                                           f" window; {await self._dl_candidates()}{await self._where()}")
                    if blobs:
                        blob_at = blob_at or _time.monotonic()
                        if _time.monotonic() - blob_at >= 2:
                            got = ("blob", _dl_blob_pick(blobs))
                            break
                if _time.monotonic() > deadline:
                    for g in own:
                        await _dl_cancel(b, g)
                    so_far = f"; {st['got'].get(own[0], 0)} bytes had come" if own else ""
                    raise TimeoutError(f"no download finished within {timeout} s after {what}{so_far}; a big file"
                                       f" needs a larger timeout={await self._where()}")
                await _asyncio.sleep(0.25)
            if got[0] == "file":
                p = st["begun"][got[1]]
                path = st["end"][got[1]].get("filePath") or _os.path.join(shared, got[1])
                out = _dl_keep(p["suggestedFilename"] or _dl_url_name(p["url"]), src=path)
            else:
                x = got[1]
                r = await self._guard(_BLOB_READ_JS.replace("__I__", str(int(x["i"]))), timeout + 10)
                name = x.get("name") or _dl_disposition(x.get("disp")) or (
                    "download" + (_mimetypes.guess_extension((x["type"] or "").split(";")[0]) or ""))
                out = _dl_keep(name, data=_base64.b64decode(r), mime=x["type"] or None)
            await _asyncio.sleep(0.5)  # a second download the same click started (click()'s script click)
            for g in await mine():
                if g != (got[1] if got[0] == "file" else None):
                    await _dl_cancel(b, g, st["end"].get(g, {}).get("filePath") or _os.path.join(shared, g))
            return out
        finally:
            b._listeners.remove(on)
            b.close()
            self._cdp._listeners.remove(on_net)
            await self._net_off()
            try:
                await self._ev(_BLOB_HOOK_JS.replace("__ON__", "false"), 5)
            except (RuntimeError, ConnectionError, _asyncio.TimeoutError):
                pass

    async def _dl_target(self, click, kw):
        """B-63 (MONEY-0119: observe("Herunterladen") ref 1 was a tab panel
        holding the buttons; download(ref=1) clicked it twice, 30 + 60 s):
        when download()'s selector or ref= is no link/button but a container,
        the CSS of the one download link/button inside it (it says so); a
        ValueError naming them when it holds several; None to click it as
        given (a link/button, a text= or frame= click, an action, no match)."""
        ref = kw.get("ref")
        if (kw.get("text") is not None or kw.get("frame") is not None or kw.get("within") or kw.get("nth")
                or self._in_frame_ref(ref) or not (isinstance(click, str) or (click is None and ref is not None))):
            return None
        sel = click if isinstance(click, str) else f'[data-vf-ref="{int(ref)}"]'
        try:
            r = await self._ev(_vf(_DL_TARGET_JS, SEL=sel, PAGE=False), 5)
        except (RuntimeError, _asyncio.TimeoutError):
            return None
        if not r:
            return None
        where = f"ref {ref}" if click is None else repr(sel)
        if r["pick"]:
            print(f"[download: {where} is a container ({r['what']}), not a link or button; clicking"
                  f" {r['pick']['name']!r} inside it ({r['pick']['css']})]")
            return r["pick"]["css"]
        raise ValueError(f"download: {where} is a container ({r['what']}) holding {r['n']} links/buttons; pass one: "
                         + "; ".join(f"{c['name']!r} → download({c['css']!r})" for c in r["cands"]))

    async def _dl_candidates(self):
        """"download links/buttons on the page: 'PDF' → download('[data-vf-dl="3"]'), …"
        for a download() that started nothing."""
        try:
            r = await self._ev(_vf(_DL_TARGET_JS, SEL="body", PAGE=True), 5)
        except (RuntimeError, _asyncio.TimeoutError):
            r = None
        if not r or not r["cands"]:
            return "no download link or button shows on the page (a dialog or menu to open first?)"
        return ("download links/buttons on the page: "
                + "; ".join(f"{c['name']!r} → download({c['css']!r})" for c in r["cands"]))

    async def _dl_watch(self, what, clicked):
        """B-80 (IKEA's "Wird geladen" dialog that became "Kontakt zu IKEA … Wir
        entschuldigen uns", 30 + 60 s; bahn's "Rechnung erstellen" opening an
        address form, 30 s): while download() waits for a file that hasn't
        begun, a dialog/alert text with error words that wasn't there before
        the click raises RuntimeError at once, quoting it; new empty form
        fields (one required, or two) raise 5 s after the click, naming them."""
        try:
            r = await self._ev(_vf(_DL_WATCH_JS, PRE=False), 5)
        except (RuntimeError, _asyncio.TimeoutError):
            return  # mid-navigation
        if not r:
            return
        if r.get("err"):
            raise RuntimeError(f"download: after {what} the page shows an error instead of a file:"
                               f" {_mask_secrets(_mask_url(r['err']))!r}; nothing was downloaded{await self._where()}")
        if r.get("form") and _time.monotonic() - clicked >= 5:
            raise RuntimeError(f"download: {what} opened a form instead of a file; its empty fields: "
                               + ", ".join(repr(f) for f in r["form"])
                               + ". Fill them (and press its button) first, then download() the link it shows"
                               + await self._where())

    async def _h1_route(self, host):
        """From now on, serve this tab's requests to `host` (with ":port"
        when its URLs have one) over HTTP/1.1 outside Chrome (urllib, with
        the browser's cookies for each URL and the request's own headers),
        for a server that breaks off Chrome's HTTP/2 (ERR_HTTP2_PROTOCOL_ERROR:
        sportscheck.com). Its redirects go back to Chrome; pages the hard
        rules block are still failed."""
        hosts = self.__dict__.setdefault("_h1_hosts", set())
        if host in hosts:
            return
        cdp = self._cdp

        class NoRedirect(_urlrequest.HTTPRedirectHandler):
            def redirect_request(self, *a, **kw):
                return None  # a 3xx comes back as HTTPError: Chrome follows it itself

        def fetch(req, cookie):
            skip = ("accept-encoding", "cookie", "content-length", "host", "connection")
            h = {k: v for k, v in req.get("headers", {}).items() if not k.startswith(":") and k.lower() not in skip}
            h["Accept-Encoding"] = "gzip, deflate"
            if cookie:
                h["Cookie"] = cookie
            data = None
            if req.get("postDataEntries"):
                data = b"".join(_base64.b64decode(e.get("bytes", "")) for e in req["postDataEntries"])
            elif req.get("postData") is not None:
                data = req["postData"].encode()
            r = _urlrequest.Request(_ascii_url(req["url"]), data=data, headers=h, method=req.get("method", "GET"))
            try:
                with _urlrequest.build_opener(NoRedirect).open(r, timeout=30) as resp:
                    status, headers, body = resp.status, resp.headers, resp.read()
            except _urlerror.HTTPError as e:
                status, headers, body = e.code, e.headers, e.read()
            body = _decode_body(body, headers.get("Content-Encoding"), headers.get("Content-Type"))
            drop = ("content-encoding", "content-length", "transfer-encoding", "connection", "keep-alive")
            return status, [{"name": k, "value": v} for k, v in headers.items() if k.lower() not in drop], body

        async def serve(rid, req):
            try:
                if any(_fnmatch.fnmatch(req["url"], p) for p in _BLOCK_PATTERNS):
                    raise HardRule(_blocked_msg(req["url"]))
                ck = (await cdp.send("Network.getCookies", {"urls": [req["url"]]}, 10)).get("cookies", [])
                status, headers, body = await _asyncio.to_thread(fetch, req, "; ".join(f"{c['name']}={c['value']}" for c in ck))
                await cdp.send("Fetch.fulfillRequest", {"requestId": rid, "responseCode": status, "responseHeaders": headers,
                                                        "body": _base64.b64encode(body).decode()}, 30)
            except Exception as e:  # noqa: BLE001 — any failure fails just this request, as a network error would
                try:
                    await cdp.send("Fetch.failRequest", {"requestId": rid, "errorReason":
                                                         "BlockedByClient" if isinstance(e, HardRule) else "Failed"}, 5)
                except (RuntimeError, ConnectionError, _asyncio.TimeoutError):
                    pass

        def on(msg):
            if msg.get("method") == "Fetch.requestPaused" and not msg.get("sessionId"):
                p = msg["params"]
                _asyncio.ensure_future(serve(p["requestId"], p["request"]))

        if not hosts:
            cdp._listeners.append(on)
        hosts.add(host)
        await cdp.send("Fetch.enable", {"patterns": [{"urlPattern": f"*://{h}/*", "requestStage": "Request"}
                                                     for h in sorted(hosts)]}, 10)

    async def close(self):
        """Close this tab; deletes its screenshots and captcha montage (its
        downloads stay until shop_close())."""
        self._captcha_drop()
        _shots_drop(self.id)
        self._cdp.close()
        async with _shop_lock():
            if not _shop_close_tab(self.id):
                return  # the browser didn't answer: the tab stays this kernel's, for shop_close()
            owners = _shop_owners()
            if owners.pop(self.id, None) is not None:
                _shop_set_owners(owners)


import email.message as _emailmsg  # noqa: E402 — download(): Content-Disposition file names
import mimetypes as _mimetypes  # noqa: E402

# captcha() (B-86): [host+path, next-step signs]: the shown code/OTP fields
# and up to 4 "code sent" / "Resend in 51s" texts (each number as #, so a
# running countdown stays the same sign).
_CAPTCHA_NEXT_JS = r"""
  const out = [location.host + location.pathname];
  const CODE = /code|otp|verif|\bpin\b|\btan\b|one-time|einmal|bestätig/i, NOT = /captcha|promo|gutschein|coupon|voucher|postal|plz|zip/i;
  for (const f of VF.all("input")) {
    if (!/^(text|tel|number|)$/.test(f.type) || !VF.shown(f)) continue;
    const what = `${f.name} ${f.id} ${f.placeholder} ${VF.attr(f, "aria-label")} ${f.autocomplete}`;
    if ((f.autocomplete === "one-time-code" || CODE.test(what)) && !NOT.test(what))
      out.push(`code field ${JSON.stringify(VF.norm(VF.label(f) || f.name || f.id).slice(0, 40))}`);
  }
  const t = VF.norm((document.body?.innerText ?? "").slice(0, 20000));
  const RX = /resend(?: code)?(?: in)? \d+ ?s\b|erneut senden(?: in)? \d+|(?:code|e-?mail|link) (?:has been |was |wurde )?(?:sent|gesendet|verschickt|versandt)|(?:wir haben|we(?:'ve| have)) [^.]{0,60}?(?:gesendet|geschickt|sent)\b|check your (?:e-?mail|inbox)|prüfen sie ihr(?:en)? (?:posteingang|e-mail)/gi;
  const said = new Set([...t.matchAll(RX)].map((m) => `says ${JSON.stringify(m[0].slice(0, 80).replace(/\d+/g, "#"))}`));
  return [...out, ...[...said].slice(0, 4)];"""

# captcha(): Cloudflare's managed challenge, a whole page ("Just a moment…",
# "Performing security verification") whose widget sits in a closed shadow
# root. Its markers: window._cf_chl_opt (only challenge pages set it; ordinary
# pages behind Cloudflare load /cdn-cgi/challenge-platform/… too, so that
# doesn't count), its #challenge-* boxes, or its title plus its text.
_CF_MANAGED_JS = r"""(() => {
  if (document.readyState === "loading") return { busy: true };
  const o = window._cf_chl_opt;
  const mark = document.querySelector("#challenge-form, #challenge-stage, #challenge-running, #challenge-body-text,"
    + " #challenge-error-text, #cf-challenge-running, #cf-please-wait");
  const title = (document.title || "").trim();
  const titled = /^(just a moment|nur einen moment|einen moment|un instant|un momento|bitte warten)/i.test(title);
  const said = (/performing security verification|verifying you are human|verify you are human|checking your browser|checking if the site connection is secure|sicherheitsüberprüfung wird durchgeführt|überprüfung, ob sie ein mensch sind|bestätigen sie, dass sie ein mensch sind/i
    .exec(((document.body && document.body.innerText) || "").slice(0, 4000)) || [null])[0];
  if (!(o || mark || (titled && said))) return null;
  return { type: (o && o.cType) || null, title: title.slice(0, 120), said };
})()"""

# captcha(): whether point (X, Y) of the parent document hits this <iframe>
# as its own shadow root sees it (a closed one hides it from the document).
_SHADOW_HIT_JS = """function (X, Y) {
  const root = this.getRootNode();
  return root instanceof ShadowRoot && root.elementFromPoint(X, Y) === this;
}"""

# download(): the Blobs the page makes into object URLs while it runs (a CSV
# built in the page, then saved through <a download> or opened in a window),
# with the download name of the <a> that points at one. B-80 (PayPal's
# reports, Vodafone's invoices: hand-written hooks): also the file of an
# <a download> with a data: URL, anchors clicked by dispatchEvent (FileSaver:
# never in the document), and file-like answers the page reads with
# fetch(…).blob() (an attachment or a pdf/csv/zip/… type; "fetched", used
# only when no object URL was made). __ON__: true arms (and forgets earlier
# ones), false stops and lets them go.
_BLOB_HOOK_JS = r"""(() => {
  const K = Symbol.for("vf-blobs-2");
  let s = window[K];
  if (!s) {
    s = window[K] = { on: false, list: [], names: {} };
    const keep = (u, b, more) => {
      const x = s.list.find((y) => y.b === b);
      if (!x) s.list.push({ u, b, ...more });
      else if (u) { x.u = u; x.fetched = false; }
    };
    const make = URL.createObjectURL;
    URL.createObjectURL = function (o) {
      const u = make.apply(this, arguments);
      if (s.on && o instanceof Blob) keep(u, o, {});
      return u;
    };
    const note = (a) => {
      if (!s.on || !a || typeof a.href !== "string") return;
      if (a.href.startsWith("blob:")) s.names[a.href] = a.download || "";
      else if (a.href.startsWith("data:") && a.hasAttribute("download") && !(a.href in s.names)) {
        const u = a.href;
        s.names[u] = a.download || "";
        fetch(u).then((r) => r.blob()).then((b) => keep(u, b, {})).catch(() => {});
      }
    };
    const click = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function () { note(this); return click.apply(this, arguments); };
    const send = EventTarget.prototype.dispatchEvent;
    HTMLAnchorElement.prototype.dispatchEvent = function (e) {
      if (e && e.type === "click") note(this);
      return send.apply(this, arguments);
    };
    addEventListener("click", (e) => note(e.target && e.target.closest && e.target.closest("a[href]")), true);
    const FILE = /pdf|csv|zip|octet-stream|excel|spreadsheet|officedocument|opendocument|text\/plain|calendar/i;
    const blob = Response.prototype.blob;
    Response.prototype.blob = function () {
      const cd = this.headers.get("content-disposition") || "", ct = this.headers.get("content-type") || "";
      return blob.apply(this, arguments).then((b) => {
        if (s.on && b && b.size && (/attachment|filename/i.test(cd) || FILE.test(b.type || ct))) keep(null, b, { fetched: true, cd });
        return b;
      });
    };
  }
  s.on = __ON__;
  s.list = [];
  s.names = {};
  return true;
})()"""
_BLOB_LIST_JS = r"""(() => {
  const s = window[Symbol.for("vf-blobs-2")];
  return s ? s.list.map((x, i) => ({ i, size: x.b.size, type: x.b.type, name: (x.u && s.names[x.u]) || null,
                                     fetched: !!x.fetched, disp: x.cd || null })) : [];
})()"""
_BLOB_READ_JS = r"""(async () => {
  const b = new Uint8Array(await window[Symbol.for("vf-blobs-2")].list[__I__].b.arrayBuffer());
  let s = "";
  for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode.apply(null, b.subarray(i, i + 0x8000));
  return btoa(s);
})()"""


def _dl_blob_pick(blobs):
    """download()'s Blob: the last one an <a download> named, else the last
    object URL, else the last file-like fetch(…).blob() answer."""
    named = [x for x in blobs if x.get("name")]
    made = [x for x in blobs if not x.get("fetched")]
    return (named or made or blobs)[-1]


# B-80: download()'s watch while no file has begun. PRE (before the click):
# remembers the dialog/alert texts showing and marks the shown form fields.
# Else {err: a new dialog/alert text with error words, form: labels of new
# empty shown fields when one is required or two are new (null in a new
# document: its fields aren't new to the user)}.
_DL_WATCH_JS = r"""
  const K = Symbol.for("vf-dl-watch");
  const ERR = /fehler|fehlgeschlagen|entschuldig|nicht möglich|nicht verfügbar|konnte nicht|leider (?:nicht|ist|kein)|\berror\b|\bfailed\b|try again|erneut versuchen|went wrong|could not|unable to/i;
  const say = (e) => VF.norm(e.innerText || "").slice(0, 300);
  const boxes = () => VF.all('[role=dialog], [role=alertdialog], dialog[open], [aria-modal=true], [role=alert],'
    + ' [role=status], [aria-live=assertive]').filter(VF.shown);
  const SKIP = /^(hidden|submit|button|reset|image|checkbox|radio|search|file|range|color)$/;
  const fields = () => VF.all("input, select, textarea").filter((f) => !SKIP.test(f.type) && VF.shown(f) && !f.disabled && !f.readOnly);
  if (PRE) {
    window[K] = { said: new Set(boxes().map(say)) };
    for (const f of fields()) f.setAttribute("data-vf-dlpre", "");
    return true;
  }
  const st = window[K], said = st ? st.said : new Set();
  const err = boxes().map(say).find((t) => t && !said.has(t) && ERR.test(t)) ?? null;
  let form = null;
  if (st) {
    const empty = fields().filter((f) => !f.hasAttribute("data-vf-dlpre") && !(f.value || "").trim());
    const req = empty.filter((f) => f.required || f.getAttribute("aria-required") === "true");
    if (req.length || empty.length >= 2)
      form = empty.slice(0, 8).map((f) => VF.norm(VF.label(f) || f.name || f.id || f.localName).slice(0, 40));
  }
  return { err: err && err.slice(0, 240), form };"""
# B-63: download()'s target when its selector/ref is a container (PAGE false:
# SEL's first shown match; null when that is a link/button itself or holds
# none), or the page's download-looking links/buttons (PAGE true). Each
# candidate gets a [data-vf-dl=N] selector for the next call.
_DL_TARGET_JS = r"""
  const el = PAGE ? document.body : VF.all(SEL).find(VF.shown) ?? null;
  if (!el) return null;
  const SEM = "a[href], button, [role=button], [role=link], [role=menuitem], input[type=submit], input[type=button],"
    + " input[type=image], summary, [download]";
  if (!PAGE && (el.matches(SEM) || el.closest?.("a[href], button, [role=button], [role=link], [role=menuitem], summary"))) return null;
  const FILE = /\.(pdf|csv|zip|xlsx?|docx?|odt|ods|ics|txt|xml|json|png|jpe?g)(?:[?#]|$)/i;
  const WORD = /herunterladen|download|\bpdf\b|\bcsv\b|export|speichern|\bsave\b/i;
  const name = (e) => VF.norm(VF.label(e) || VF.attr(e, "aria-label") || VF.attr(e, "title") || VF.attr(e, "href") || e.localName).slice(0, 60);
  const dl = (e) => e.hasAttribute("download") || FILE.test(VF.attr(e, "href"))
    || WORD.test(`${name(e)} ${VF.attr(e, "aria-label")} ${VF.attr(e, "title")}`);
  const cands = (PAGE ? VF.all(SEM) : [...el.querySelectorAll(SEM)]).filter(VF.shown);
  if (!PAGE && !cands.length) return null;
  const good = cands.filter(dl);
  const pick = PAGE ? null : good.length === 1 ? good[0] : !good.length && cands.length === 1 ? cands[0] : null;
  const K = Symbol.for("vf-dl-n");
  const mark = (e) => {
    if (!e.hasAttribute("data-vf-dl")) e.setAttribute("data-vf-dl", String(window[K] = (window[K] ?? 0) + 1));
    return `[data-vf-dl="${e.getAttribute("data-vf-dl")}"]`;
  };
  const list = PAGE || good.length ? good : cands;
  const cls = typeof el.className === "string" && el.className.trim() ? "." + el.className.trim().split(/\s+/)[0] : "";
  return { what: el.localName + (VF.attr(el, "role") ? `[role=${VF.attr(el, "role")}]` : "") + cls,
           pick: pick ? { css: mark(pick), name: name(pick) } : null, n: list.length,
           cands: list.slice(0, 8).map((e) => ({ css: mark(e), name: name(e) })) };"""
# download()'s "the click started nothing" check ignores these requests.
_DL_IDLE_TYPES = ("Ping", "Image", "Font", "Stylesheet", "Media", "Manifest", "CSPViolationReport", "TextTrack", "Preflight")
_DL_TRACKER = _re.compile(r"google-analytics\.com|googletagmanager\.com|doubleclick\.net|/g/collect\b|hotjar\.|clarity\.ms"
                          r"|facebook\.com/tr|bat\.bing\.com|matomo|piwik|etracker|sentry\.io|nr-data\.net|segment\.io"
                          r"|mixpanel\.com|amplitude\.com|browser-intake-datadoghq")


def _dl_shared_dir():
    """Where the shop browser saves downloads while a download() runs: one
    0700 directory per browser (every agent sets the same, so they never
    point the browser elsewhere under each other); files are named by their
    download id there, moved out at once; ones older than 1 h are left-overs."""
    d = _os.path.join(_ENV["XDG_RUNTIME_DIR"], f"omp-shop-{_SHOP_PORT}-downloads")
    _os.makedirs(d, mode=0o700, exist_ok=True)
    now = _time.time()
    for e in _os.scandir(d):
        try:
            if e.is_file() and now - e.stat().st_mtime > 3600:
                _os.unlink(e.path)
        except OSError:
            pass
    return d


def _dl_dir():
    """This kernel's 0700 directory of downloaded files; shop_close() and the
    kernel's exit delete it (a tab's close() doesn't)."""
    global _DL_DIR
    d = globals().get("_DL_DIR")
    if not d or not _os.path.isdir(d):
        d = _DL_DIR = _tempfile.mkdtemp(prefix="vf-dl-")
    _SHOTS[d] = None  # no tab's: only _shots_drop() without a tab id takes it
    return d


def _dl_disposition(cd):
    """The file name of a Content-Disposition header (filename*= first, as
    browsers take it), or None."""
    if not cd:
        return None
    m = _re.search(r"filename\*\s*=\s*([\w!#$%&+^`{}~.-]*)'[^']*'([^;\s]+)", cd, _re.I)
    if m:
        try:
            return _urlparse.unquote(m[2].strip('"'), encoding=m[1] or "utf-8", errors="strict") or None
        except (LookupError, UnicodeDecodeError):
            pass
    msg = _emailmsg.Message()
    msg["Content-Disposition"] = cd
    try:
        return msg.get_filename() or None
    except (ValueError, LookupError):
        return None


def _dl_url_name(url):
    """The last path segment of `url`, decoded; "download" when it has none."""
    if not url or url.startswith(("blob:", "data:")):
        return "download"
    return _urlparse.unquote(_urlparse.urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]) or "download"


def _dl_sniff(head):
    for magic, mime in ((b"%PDF", "application/pdf"), (b"PK\x03\x04", "application/zip"), (b"\x89PNG", "image/png"),
                        (b"\xff\xd8\xff", "image/jpeg"), (b"GIF8", "image/gif")):
        if head.startswith(magic):
            return mime
    return None


def _dl_keep(name, *, data=None, src=None, mime=None):
    """Store a downloaded file (bytes `data`, or moved from path `src`) in
    _dl_dir() as `name` made safe (no path, no control or URL characters,
    ≤ 150 chars, " (2)" etc. when taken; an IBAN or card number in it
    masked, so the path it returns is the one tools show), mode 600:
    {path, name, size, mime}."""
    name = _re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|=&#%]+', "_", _os.path.basename((name or "").replace("\\", "/")))
    name = _mask_secrets(name.strip().strip(".")) or "download"
    stem, ext = _os.path.splitext(name)
    if len(name) > 150:
        stem, ext = stem[:150 - len(ext[:20])], ext[:20]
    d, n = _dl_dir(), 1
    while True:
        path = _os.path.join(d, stem + (f" ({n})" if n > 1 else "") + ext)
        try:
            fd = _os.open(path, _os.O_WRONLY | _os.O_CREAT | _os.O_EXCL, 0o600)
            break
        except FileExistsError:
            n += 1
    if src is not None:
        _os.close(fd)
        _shutil.move(src, path)
        _os.chmod(path, 0o600)
    else:
        with _os.fdopen(fd, "wb") as f:
            f.write(data)
    with open(path, "rb") as f:
        head = f.read(8)
    mime = (mime or "").split(";")[0].strip().lower()
    if mime in ("", "application/octet-stream", "binary/octet-stream", "application/force-download"):
        mime = _mimetypes.guess_type(path)[0] or _dl_sniff(head) or mime or "application/octet-stream"
    return {"path": path, "name": _os.path.basename(path), "size": _os.path.getsize(path), "mime": mime}


async def _dl_cancel(b, guid, path=None):
    """Stop download `guid` (Chrome deletes its part file) and delete its
    finished file at `path`."""
    try:
        await b.send("Browser.cancelDownload", {"guid": guid}, 5)
    except (RuntimeError, ConnectionError, _asyncio.TimeoutError):
        pass
    if path:
        try:
            _os.unlink(path)
        except OSError:
            pass


# collect()/shop_map() fields: pick(root, "css" | "css@attr" | "@attr", name) → the
# value in `root` (an item; null: the whole page, shadow roots too).
_PICK_JS = r"""
  const pick = (root, spec, name) => {
    const m = /^([\s\S]*?)@([\w:.-]+)$/.exec(spec);
    const css = (m ? m[1] : spec).trim(), a = m ? m[2] : null;
    let els;
    try {
      els = !css ? [root ?? document.documentElement]
        : root ? [...(root.matches(css) ? [root] : []), ...root.querySelectorAll(css)] : VF.all(css);
    } catch { throw new Error(`fields: ${name}: ${JSON.stringify(css)} is no valid CSS`); }
    if (a) {
      const e = els.find((x) => x.hasAttribute(a));
      if (!e) return "";
      if (!/^(href|src)$/.test(a)) return VF.norm(VF.attr(e, a));
      try { return new URL(VF.attr(e, a), location.href).href; } catch { return VF.attr(e, a); }
    }
    // Text: the first shown match that has some; else one only zero-sized (an
    // aria-label-only star rating), never one display:none or visibility:hidden hides.
    const vis = els.filter((x) => x.checkVisibility ? x.checkVisibility({ visibilityProperty: true }) : VF.shown(x));
    for (const e of [...vis.filter(VF.shown), ...vis]) {
      const t = VF.label(e);
      if (t) return t.slice(0, 300);
    }
    return "";
  };
"""


def _field_specs(fields, taken):
    """({name: css}, {name: fn}) of collect()/shop_map()'s `fields`."""
    if not fields:
        return {}, {}
    if not isinstance(fields, dict):
        raise TypeError("fields: a dict {name: css or a function}")
    css, fns = {}, {}
    for k, v in fields.items():
        if k in (*taken, "missing"):
            raise ValueError(f"fields: {k!r} is taken by the result itself; name it otherwise")
        if isinstance(v, str):
            css[k] = v
        elif callable(v):
            fns[k] = v
        else:
            raise TypeError(f"fields: {k!r} must be a CSS string or a function, not {type(v).__name__}")
    return css, fns


async def _field_call(fn, arg):
    """fn(arg), awaited if need be; a regex match gives its first group
    (else the whole match); a lookup error (no match, no such key) gives
    None, i.e. empty."""
    try:
        v = fn(arg)
        if _inspect.isawaitable(v):
            v = await v
    except (AttributeError, IndexError, KeyError, ValueError, TypeError):
        return None
    if isinstance(v, _re.Match):
        v = v.group(1) if v.re.groups else v.group(0)
    return v


def _empty(v):
    """None, or a blank string or empty container (0 and False are values)."""
    if isinstance(v, str):
        return not v.strip()
    return v is None or (isinstance(v, (list, tuple, dict, set)) and not v)


def _mark_missing(rows, fields, required, where):
    """Gives each row (exceptions skipped) "missing": its `required` fields
    (default: all of `fields`) that are empty; returns the printout's part:
    "2 with empty price (<where(indexes)>)…" or "price, rating filled in all"."""
    req = list(fields or ()) if required is None else [required] if isinstance(required, str) else list(required)
    empty = {}
    for i, r in enumerate(rows):
        if isinstance(r, BaseException) or not isinstance(r, dict):
            continue
        r["missing"] = [f for f in req if _empty(r.get(f))]
        for f in r["missing"]:
            empty.setdefault(f, []).append(i)
    if not req:
        return "no required fields"
    if not empty:
        return f"{', '.join(req)} filled in all"
    return "; ".join(f"{len(ix)} with empty {f} ({where(ix[:8])}{', …' if len(ix) > 8 else ''})"
                     for f in req if (ix := empty.get(f)))


def _capture_body(text):
    """capture()'s post_data (B-84, SHOP-0307: expert.de's POST /api/search
    body): a request body's JSON parsed, a form body (a=1&b=2) as {name:
    value}, else its text (≤ 4000 chars); masked like any result."""
    if text is None:
        return None
    try:
        return _mask_secrets(_json.loads(text))
    except ValueError:
        pass
    if _re.fullmatch(r"[^=&\s]+=[^&\s]*(?:&[^=&\s]+=[^&\s]*)*", text):
        return _mask_secrets(dict(_urlparse.parse_qsl(text, keep_blank_values=True)))
    return _mask_secrets(_mask_url(text[:4000]))


def _h2_fallback(goto):
    """goto() that, when the server breaks off Chrome's HTTP/2
    (ERR_HTTP2_PROTOCOL_ERROR), routes the tab's requests to that host over
    HTTP/1.1 (ShopTab._h1_route), says so, and tries once more."""
    @_functools.wraps(goto)
    async def call(self, url, *args, **kwargs):
        try:
            return await goto(self, url, *args, **kwargs)
        except RuntimeError as e:
            host = _urlparse.urlsplit(url).netloc.rpartition("@")[2].lower()
            if "ERR_HTTP2_PROTOCOL_ERROR" not in str(e) or not host or host in getattr(self, "_h1_hosts", ()):
                raise
        await self._h1_route(host)
        print(f"[goto: {host} broke off Chrome's HTTP/2 (ERR_HTTP2_PROTOCOL_ERROR); this tab now loads {host} over"
              " HTTP/1.1 outside Chrome, with the browser's cookies; retrying once]")
        return await goto(self, url, *args, **kwargs)
    return call


ShopTab.goto = _h2_fallback(ShopTab.goto)


# B-50: the masker hid raceresult's public list `key` (config JSON) that the
# next request needed (LIFE-0111). fetch_json/http_json(reveal_keys=[…])
# leave the values under the named JSON keys unmasked; IBANs, card numbers,
# JWTs and Bearer credentials inside them still are, and keys naming a
# password, session/access/refresh/id token, client secret, private key or
# Authorization header can't be revealed. The audit still flags a printed
# value: reveal a key to pass it on, not to print it.
_NEVER_REVEAL = ("password", "passwd", "secrettoken", "accesstoken", "refreshtoken", "idtoken", "clientsecret",
                 "authorization", "bearer", "privatekey", "session", "sessionid", "sid", "cookie")


def _reveal_set(keys):
    """The normalized names (_key_norm) of reveal_keys (a name or a list of
    names); ValueError for a credential key."""
    if not keys:
        return frozenset()
    names = [keys] if isinstance(keys, str) else list(keys)
    bad = [k for k in names if _key_norm(k).endswith(_NEVER_REVEAL)]
    if bad:
        raise ValueError(f"reveal_keys: {', '.join(map(repr, bad))} name a credential (password, session, access/refresh/id"
                         " token, client secret, private key, Authorization); those stay masked")
    return frozenset(n for n in map(_key_norm, names) if n)


def _mask_reveal(obj, reveal):
    """(_mask_secrets(obj) except that string values under a JSON key whose
    _key_norm is in `reveal` (at any depth; a list under it counts as its
    values) only get _mask_text, the number of values returned as they
    came that the masker would have hidden)."""
    hits = [0]

    def val(k, v, rev):
        if type(v) is list:
            return [val(k, x, rev) for x in v]
        if not rev or not isinstance(v, str):
            return walk(v) if isinstance(v, (dict, list, tuple)) else _mask_value(k, v)
        t = _mask_text(v)
        hits[0] += t is v and _mask_value(k, v) != v
        return t

    def walk(o):
        if isinstance(o, dict):
            return {k: val(k, v, _key_norm(k) in reveal) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [walk(x) for x in o]
        return _mask_secrets(o)
    return walk(obj), hits[0]


def _reveal_print(who, obj, reveal):
    """_mask_reveal(obj, reveal)'s object; prints how many values it left
    unmasked."""
    obj, n = _mask_reveal(obj, reveal)
    if n:
        print(f"[{who}: reveal_keys left {n} value{'s' if n != 1 else ''} unmasked; pass {'them' if n != 1 else 'it'}"
              " on, don't print: the audit flags printed keys]")
    return obj


def _restart_aware(fn):
    """A ShopTab method whose failure on a closed DevTools connection says
    whether the browser restarted or the tab is gone (BrowserRestarted).
    Its result is masked (_mask_secrets, _mask_urls), with the method's
    reveal_keys= exempt."""
    @_functools.wraps(fn)
    async def call(self, *args, **kwargs):
        try:
            r = _mask_urls(await fn(self, *args, **kwargs))
            reveal = kwargs.get("reveal_keys")
            async with _mask_scope(self):
                return _mask_reveal(r, _reveal_set(reveal))[0] if reveal else _mask_secrets(r)
        except (BrowserRestarted, HardRule, TabStuck):
            raise
        except Exception as e:
            if not self._cdp._task.done():
                raise  # the connection lives: an ordinary failure
            err = _restart_error(self.id, self._browser)
            if err is None:
                raise
            raise err from e
    return call


for _name, _fn in list(vars(ShopTab).items()):
    if not _name.startswith("_") and _name != "close" and _inspect.iscoroutinefunction(_fn):
        setattr(ShopTab, _name, _restart_aware(_fn))


