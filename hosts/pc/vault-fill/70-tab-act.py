    # fill()'s view of where a field sits: per data-vf-ref, [its form's number (closest <form>, else
    # dialog; -1: none), whether it sits in a footer or a newsletter box]; KEYS (CSS keys): the
    # ref of each one's shown match, one outside a footer/newsletter box first (duplicate ids).
    _FILL_CTX_JS = r"""
      const foot = (e) => {
        for (let n = e, i = 0; n && i < 14; n = VF.up(n), i++) {
          if (n.localName === "footer" || VF.attr(n, "role") === "contentinfo") return true;
          if (/newsletter/i.test(`${n.id ?? ""} ${typeof n.className === "string" ? n.className : ""} ${VF.attr(n, "action")} ${VF.attr(n, "name")}`)) return true;
        }
        return false;
      };
      const forms = new Map(), at = {}, css = {};
      for (const e of VF.all("input, select, textarea, [contenteditable], [data-vf-ref]")) {
        const ref = e.getAttribute("data-vf-ref");
        if (!ref) continue;
        const f = e.closest?.("form") ?? e.closest?.("dialog, [role=dialog]") ?? null;
        if (f && !forms.has(f)) forms.set(f, forms.size);
        at[ref] = [f ? forms.get(f) : -1, foot(e)];
      }
      for (const k of KEYS) {
        let els = [];
        try { els = VF.all(k).filter(VF.shown); } catch {}
        const e = els.find((x) => !foot(x)) ?? els[0];
        css[k] = e ? Number(e.getAttribute("data-vf-ref")) || null : null;
      }
      return { at, css };"""

    # fill(): whether field REF is a custom select (react-select's input in its __control, MUI's
    # Select box, ng-select, select2, choices.js, vue-select; a readonly input or a box with
    # role=combobox / aria-haspopup=listbox), which choose() opens and picks from; a free-text
    # autocomplete (role=combobox on a plain input) is typed into (B-41).
    _COMBO_KIND_JS = r"""
      const SEL_CLS = /(^|\s)([\w-]*__control|css-[\w]+-control|MuiSelect-select|ng-select-container|select2-selection|choices__inner|vs__dropdown-toggle|multiselect__tags|v-select__slot)(\s|$)/;
      const el = VF.all(`[data-vf-ref="${REF}"]`)[0];
      if (!el) return false;
      const pops = VF.attr(el, "role") === "combobox" || VF.attr(el, "aria-haspopup") === "listbox";
      if (!/^(input|textarea)$/.test(el.localName)) return pops && !el.isContentEditable;
      for (let n = el, i = 0; n && n !== document.body && i < 6; n = VF.up(n), i++) if (SEL_CLS.test(VF.attr(n, "class"))) return true;
      return el.readOnly && pops;"""
    # fill(): whether typed field REF still has its suggestion list open (aria-expanded, its
    # aria-controls list shown, or it has the focus while a listbox shows); then it keeps the
    # focus for an Escape. BLUR: takes the focus away.
    _SUGGEST_OPEN_JS = r"""
      const el = VF.all(`[data-vf-ref="${REF}"]`)[0];
      if (!el?.isConnected) return false;
      if (BLUR) { el.blur(); return true; }
      const id = VF.attr(el, "aria-controls") || VF.attr(el, "aria-owns");
      const list = id ? el.getRootNode().getElementById?.(id) : null;
      const open = VF.attr(el, "aria-expanded") === "true" || (!!list && VF.shown(list))
        || (VF.inside(VF.active(), el) && VF.all("[role=listbox]").some(VF.shown));
      if (open) el.focus();
      return open;"""

    # type(): a keyup on the typed field (vf-typed) after inserted text, for a page that checks the
    # field on keyup (LIFE-0003: an inline onkeyup enabled the Umkreis select, B-74).
    _KEYUP_JS = r"""
      const el = window[Symbol.for("vf-typed")];
      if (!el?.isConnected) return false;
      const code = /^[a-z]$/i.test(KEY) ? "Key" + KEY.toUpperCase() : /^\d$/.test(KEY) ? "Digit" + KEY : KEY === " " ? "Space" : "";
      el.dispatchEvent(new KeyboardEvent("keyup", { key: KEY, code, bubbles: true, cancelable: true, composed: true }));
      return true;"""
    # select(): after the change, the select loses the focus (blur, focusout: pages that react on
    # it), then ≤ 1.5 s for the page to settle (no DOM change for 0.25 s and no fetch/XHR of the
    # page in flight): the fields a choice shows come by then (MONEY-0229, B-74).
    _SELECT_SETTLE_JS = r"""
      let el = null;
      try { el = VF.find({ css: SEL }).el ?? VF.all(SEL)[0]; } catch {}
      if (el?.isConnected && VF.inside(VF.active(), el)) el.blur();
      const busy = () => {  // part 20's request counters (vf-net2: long-polls left out), as _QUIET_JS reads them
        const now = performance.now(), n2 = window[Symbol.for("vf-net2")], n1 = window[Symbol.for("vf-net")];
        if (n2) return [...n2.live.values()].some((r) => now - r.t < 15000 && !n2.slow?.has(r.key));
        return !!n1 && [...n1.live.values()].some((t) => now - t < 15000);
      };
      return new Promise((done) => {
        const t0 = performance.now();
        let last = t0;
        const o = new MutationObserver(() => { last = performance.now(); });
        o.observe(document, { subtree: true, childList: true, attributes: true });
        const tick = () => {
          const now = performance.now();
          if ((now - last >= 250 && !busy()) || now - t0 >= 1500) { o.disconnect(); done(true); } else setTimeout(tick, 50);
        };
        setTimeout(tick, 50);
      });"""

    @staticmethod
    def _nlab(s):
        """A field label as fill()/type() compare it: lower case, spaces
        collapsed, without a required mark or "(optional)" ("PLZ *" = "plz":
        MONEY-0112, B-74)."""
        s = _re.sub(r"^\*\s*", "", _re.sub(r"\s+", " ", str(s or "")).strip().lower())
        for _ in range(2):
            s = _re.sub(r"\s*(\*|:|\((optional|freiwillig|pflichtfeld|pflicht|required|erforderlich)\))\s*$", "", s)
        return s

    @staticmethod
    def _iso_day(s):
        """'23.05.1998' / '3.5.98' (German order) → '1998-05-23'; ISO stays; else None."""
        s = str(s).strip()
        if _re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            return s
        m = _re.fullmatch(r"(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4}|\d{2})", s)
        if not m:
            return None
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:  # two digits: this century up to this year, else the last one
            y += 2000 if y <= _datetime.date.today().year % 100 else 1900
        try:
            return _datetime.date(y, mo, d).isoformat()
        except ValueError:
            return None

    async def fill(self, values, *, frame=None, timeout=15):
        """Fill a form in one call: {key: value}. A key is a field's label,
        placeholder or aria-label (as fields() shows it: exact, then any
        case, then a piece only one field's label has; a required "*", a
        trailing ":" or "(optional)" don't count), a CSS selector, or an
        observe()/fields() ref (int). Values: text for text fields (typed as
        type() does, autosuggest fields too; a tel field's fixed "+49" isn't
        doubled), the option text for a select or custom dropdown
        (select()/choose(); a react-select/MUI/ng-select… field is opened
        and picked by choose(), never typed into), True/False for a checkbox,
        switch or radio (a radio by its own label: {"Herr": True}, or
        {"Anrede": "Herr"} when "Herr" is a radio's label), a code for split
        code boxes, "23.05.1998" or ISO for a type=date field. Selects,
        radios and checkboxes go first, then the text fields (a select may
        re-render the form: fields are looked up again after each). Never a
        password field (vault_fill). A key that fits fields in a footer or
        newsletter box and elsewhere means the one elsewhere; several left:
        the one in the form that holds the other keys' fields; the same form
        on the page N times over (same labels in each): the first one shown,
        the same copy for every key, and it prints "went into copy 1 of 3
        equal forms". An
        autocomplete list the last typed field left open is closed (Escape,
        blur). Returns fields() of the fields it touched, as they show now,
        plus {key, problem} for each key that matched no field or several
        (it never guesses; a key with no field names the page's 5 closest
        labels) or whose filling failed, and {key, field, wanted,
        got, problem} for each text field that, read back at the end, shows
        something else than given (whitespace and punctuation aside; the
        whole text, not fields()' 300 shown characters; a fixed country
        prefix beside a tel field counts as given), printed too. A key left
        unfilled (no field, or several) makes the next click() on the same
        page (≤ 5 min) raise once, naming it: fix the key, or click again."""
        if not isinstance(values, dict) or not values:
            raise ValueError("fill() takes a dict {label or selector: value}")
        norm = lambda s: _re.sub(r"\s+", " ", str(s)).strip()  # noqa: E731
        css = lambda k: isinstance(k, str) and bool(_re.match(r"^([#.\[]|[a-z][\w-]*[#.\[:])", k))  # noqa: E731

        nlab = self._nlab

        cref = {}  # CSS key → the data-vf-ref fields() gave its element (top document)
        ctx = {}  # ref → [form number, in a footer/newsletter box] (top document)

        async def refresh():
            fs = await self.fields(frame=frame, limit=400)
            if frame is None:
                got = await self._ev(_vf(self._FILL_CTX_JS, KEYS=[k for k in values if css(k)])) or {}
                cref.clear()
                cref.update(got.get("css") or {})
                ctx.clear()
                ctx.update({int(k): v for k, v in (got.get("at") or {}).items()})
            return fs

        here = lambda f: ctx.get(f["ref"], [-1, False]) if not f.get("frame") else [-1, False]  # noqa: E731

        def match(key, fs, every=False):
            if isinstance(key, int):
                hits = [f for f in fs if f["ref"] == key]
            elif css(key):
                hits = [f for f in fs if f["selector"] == key or (cref.get(key) and f["ref"] == cref[key] and not f.get("frame"))]
            else:
                k = nlab(_re.sub(r"\s*(…|\.\.\.)$", "", norm(key)))  # "Ich habe die AGB …"
                # The text near a field (an editor's label) counts for exact and prefix matches only.
                names = lambda f, near: [nlab(x) for x in (f["label"], f.get("placeholder") or "",  # noqa: E731
                                                           (f.get("near") or "") if near else "")]
                hits, footer = [], []
                for test, near in ((lambda n: n == k, True), (lambda n: n.startswith(k), True), (lambda n: k in n, False)):
                    hits = [f for f in fs if any(n and test(n) for n in names(f, near))]
                    # A footer's or newsletter box's field only when no other one fits at all (DIGI-0013).
                    footer = footer or [f for f in hits if here(f)[1]]
                    hits = [f for f in hits if not here(f)[1]]
                    if hits:
                        break
                hits = hits or footer
            shown = [f for f in hits if not f.get("offscreen")]
            return hits if every else shown or hits  # hidden copies (Shopify's #autofill_*) don't count when a shown one matches

        async def dropdown(key):
            """Whether a custom dropdown has the label `key` (choose() may open it);
            never a nav button or menu (VD-08)."""
            return await self._ev(_vf("""const k = VF.norm(KEY).toLowerCase();
              const isDrop = (c) => c && (c.localName === "select" || VF.attr(c, "role") === "combobox"
                || /^(listbox|dialog|grid|tree)$/.test(VF.attr(c, "aria-haspopup"))
                || (VF.attr(c, "aria-haspopup") === "true" && !!c.closest?.("form")));
              for (const l of VF.all("label")) {
                if (!VF.label(l).toLowerCase().includes(k)) continue;
                const c = l.control ?? l.querySelector("select, [role=combobox], [aria-haspopup]")
                  ?? l.parentElement?.querySelector("select, [role=combobox], [aria-haspopup]");
                if (isDrop(c)) return true;
              }
              return VF.all("[role=combobox], [aria-haspopup]").some((c) => isDrop(c) && VF.shown(c) && `${VF.attr(c, "aria-label")}
                ${VF.attr(c, "placeholder")} ${VF.attr(c, "aria-labelledby").split(/\\s+/).map((i) => c.getRootNode().getElementById?.(i)?.innerText ?? "").join(" ")}`
                .toLowerCase().includes(k));""", KEY=str(key))) if frame is None else True

        problems, touched, wanted, typed, suggest = [], [], {}, {}, []
        left, twin = [], {"i": None, "n": None, "keys": []}  # keys no field took (B-74); the copy of a repeated form

        def miss(key, problem, fs=None):
            """A key no field took (none or several fit): a problem, kept for click()'s check; with `fs`, the 5
            labels closest to it ("kWh/Jahr" → 'Ihr Verbrauch', MONEY-0017)."""
            if fs is not None:
                names = list(dict.fromkeys(norm(f["label"])[:60] for f in fs
                                           if f.get("label") and f["kind"] != "password" and not f.get("offscreen")))
                k = nlab(str(key))
                names.sort(key=lambda n: -_difflib.SequenceMatcher(None, k, nlab(n)).ratio())
                if names:
                    problem += "; labels on the page: " + ", ".join(repr(n) for n in names[:5])
            problems.append({"key": key, "problem": problem})
            left.append(f"{key!r} ({problem.split(';')[0].split(':')[0][:40]})")

        def copies(hits, fs):
            """`hits` are the same field in copies of one form (a calculator shown 3×, MONEY-0134): each in a form
            of its own whose fields have the same labels as the others'."""
            if any(f.get("frame") for f in hits) or len({(nlab(f["label"]), f["kind"]) for f in hits}) != 1:
                return False
            forms = [here(f)[0] for f in hits]
            if min(forms) < 0 or len(set(forms)) != len(forms):
                return False
            sets = {n: sorted(nlab(f["label"]) for f in fs if here(f)[0] == n) for n in forms}
            return all(s == sets[forms[0]] for s in sets.values())

        def home(key, fs):
            """The form that holds the other keys' fields (each matching one): its number, or None."""
            n = {}
            for k in values:
                h = match(k, fs) if k != key else []
                if len(h) == 1 and here(h[0])[0] >= 0:
                    n[here(h[0])[0]] = n.get(here(h[0])[0], 0) + 1
            best = sorted(n.items(), key=lambda kv: -kv[1])
            return best[0][0] if best and (len(best) == 1 or best[0][1] > best[1][1]) else None

        async def act(key, value, fs):
            hits = match(key, fs)
            if isinstance(value, str) and not hits and not css(key) and not isinstance(key, int):
                # {"Anrede": "Herr"} with radios labelled Herr/Frau.
                radios = [f for f in fs if f["kind"] == "radio" and norm(f["label"]).lower() == norm(value).lower()]
                if len(radios) == 1:
                    hits, value = radios, True
            if len(hits) > 1 and not all(f["kind"] == "radio" for f in hits):
                # Several fit: the one in the form that holds the other keys' fields (a footer's e-mail field shares the id).
                h = home(key, fs)
                mine = [f for f in hits if h is not None and here(f)[0] == h]
                if len(mine) == 1:
                    hits = mine
            if len(hits) > 1 and not all(f["kind"] == "radio" for f in hits):
                every = match(key, fs, every=True)
                if copies(every, fs) and twin["n"] in (None, len(every)):
                    # The same form N times: the first one shown, the same copy for every key (B-74), said below.
                    if twin["i"] is None:
                        twin["i"], twin["n"] = next((i for i, f in enumerate(every) if not f.get("offscreen")), 0), len(every)
                    hits = [every[twin["i"]]]
                    twin["keys"].append(key)
            if len(hits) > 1:
                same_radio = all(f["kind"] == "radio" for f in hits) and isinstance(value, str)
                pick = [f for f in hits if norm(f["label"]).lower() == norm(value).lower()] if same_radio else []
                if len(pick) != 1:
                    miss(key, "matches several fields: " + ", ".join(f"{f['label'][:40]!r} ({f['selector']})" for f in hits[:6]))
                    return
                hits, value = pick, True
            if not hits:
                if isinstance(value, str) and not isinstance(key, int):
                    if not css(key) and not await dropdown(key):
                        miss(key, "no form field or dropdown has that label", fs)
                        return
                    try:  # a custom dropdown (no form field): choose() opens it and picks the option
                        await self.choose(key, value, frame=frame, timeout=timeout)
                        touched.append(key)
                        return
                    except RuntimeError as e:
                        miss(key, f"no field matches it ({str(e)[:400]})")
                        return
                if isinstance(value, bool) and not isinstance(key, int):
                    obs = [o for o in await self.observe(key if not css(key) else None, frame=frame)
                           if o.get("role") in ("checkbox", "switch", "radio") or "checked" in o]
                    obs = [o for o in obs if css(key) and o["selector"] == key or not css(key)
                           and norm(key).lower() in norm(o.get("text") or o.get("card") or "").lower()]
                    if len(obs) == 1:
                        if bool(obs[0].get("checked")) != value:
                            await self.click(ref=obs[0]["ref"], timeout=timeout)
                        touched.append(obs[0]["ref"])
                        return
                    if len(obs) > 1:
                        miss(key, "matches several toggles: " + ", ".join(repr((o.get("text") or "")[:40]) for o in obs[:6]))
                        return
                miss(key, "no field matches it", fs)
                return
            f = hits[0]
            kind, where = f["kind"], {"frame": f["frame"]} if f.get("frame") else {}
            if kind == "password":
                problems.append({"key": key, "problem": "a password field: use vault_fill(), never fill()"})
                return
            if f.get("disabled"):
                problems.append({"key": key, "problem": f"disabled ({f['label'][:40]!r}): not filled"})
                return
            if kind in ("checkbox", "radio"):
                if not isinstance(value, bool):
                    problems.append({"key": key, "problem": f"a {kind}: give True or False"})
                    return
                if f["value"] != value:
                    if kind == "radio" and not value:
                        problems.append({"key": key, "problem": "a radio is unset by choosing another one"})
                        return
                    await self.click(ref=f["ref"], timeout=timeout)
                wanted[f["selector"]] = (key, value)
            elif isinstance(value, bool):
                problems.append({"key": key, "problem": f"a {kind} field: give text, not {value}"})
                return
            elif kind.startswith("select"):
                await self.select(f["selector"], str(value), **where)
            elif kind == "code":
                await self.type_code(f["selector"], str(value), timeout=timeout, **where)
            elif kind in ("date", "datetime-local", "month", "time", "week", "range", "color"):
                if kind == "date":
                    iso = self._iso_day(value)
                    if iso is None:
                        problems.append({"key": key, "problem": f"a date field: give YYYY-MM-DD or DD.MM.YYYY, not {str(value)[:40]!r}"})
                        return
                    value = iso  # a native date input keeps only ISO (MONEY-0026)
                await self.set_value(None, str(value), ref=f["ref"], timeout=timeout)
                typed[f["ref"]] = (key, str(value), f["selector"])
            elif (isinstance(value, str) and not f.get("frame") and kind not in ("textarea", "email", "tel", "number", "url")
                  and await self._ev(_vf(self._COMBO_KIND_JS, REF=f["ref"]), 5)):
                # A react-select/MUI/… select (B-41): opened and picked like choose() does, never typed.
                said = await self.choose(f'[data-vf-ref="{f["ref"]}"]', str(value), timeout=timeout)
                if said.endswith("(the field doesn't show it: check)"):
                    problems.append({"key": key, "problem": said[:200]})
            else:
                said = await self.type(None, str(value), ref=f["ref"], timeout=timeout)
                typed[f["ref"]] = (key, str(value), f["selector"])
                if "autosuggest" in str(said):
                    suggest.append(f["ref"])
            touched.append(f["selector"])

        self._vf_fill_left = None
        first = lambda f: f["kind"].startswith("select") or f["kind"] in ("radio", "checkbox")  # noqa: E731
        fs = await refresh()
        early, late = [], []
        for key, value in values.items():
            hits = match(key, fs)
            (early if isinstance(value, bool) or not hits or all(first(f) for f in hits) else late).append((key, value))
        for key, value in early + late:
            try:
                await act(key, value, fs)
            except (HardRule, BrowserRestarted, TabStuck):
                raise
            except Exception as e:
                problems.append({"key": key, "problem": f"{type(e).__name__}: {str(e)[:400]}"})
            fs = await refresh()  # a select may have re-rendered the form
        # An autocomplete list a typed field left open covers the next button (SHOP-0119): Escape, then blur.
        for ref in suggest[-1:] if frame is None else []:
            try:
                if await self._ev(_vf(self._SUGGEST_OPEN_JS, REF=ref, BLUR=False), 5):
                    await self._act(self._cdp.press("Escape"))
                    await self._ev(_vf(self._SUGGEST_OPEN_JS, REF=ref, BLUR=True), 5)
            except (RuntimeError, _asyncio.TimeoutError):
                pass
        # A radio/checkbox that didn't take the click (VE-08: "Herr" stayed unset): say so.
        for f in fs:
            if (f["selector"] in wanted and f["value"] != wanted[f["selector"]][1]
                    and not any(p["key"] == wanted[f["selector"]][0] for p in problems)):
                key, value = wanted[f["selector"]]
                problems.append({"key": key, "problem": f"still {'unchecked' if value else 'checked'} after the click"})
        # Each text field read back once everything is in (a later field or select may have cleared it; LIFE-0145).
        flat = lambda s: _re.sub(r"[\W_]+", "", str(s).lower())  # noqa: E731

        def same(got, value):
            g, v = flat(got), flat(value)
            if g == v:
                return True
            if "•" in got:  # fields() masks an IBAN or card number ("DE••…••5938"): compare masked, spaces aside (MONEY-0036)
                return _re.sub(r"\s+", "", got) == _re.sub(r"\s+", "", _mask_text(_re.sub(r"\s+", "", str(value))))
            if got.endswith("…") and len(got) == 301 and v.startswith(flat(got[:-1])):
                return True  # fields() shows 300 characters of a long text (MONEY-0149)
            # A fixed country prefix shown beside a tel field ("+49" | "170 1234567"): the rest matches.
            return (str(value).lstrip().startswith(("+", "00")) and v.endswith(g) and 0 < len(v) - len(g) <= 5
                    and _re.fullmatch(r"(00)?\d{1,3}0?", v[:len(v) - len(g)]) is not None)

        for ref, (key, value, sel) in typed.items():
            row = [f for f in fs if f["ref"] == ref] or [f for f in fs if f["selector"] == sel and not sel.startswith("[data-vf-ref")]
            if not row:
                row = match(key, fs)
            if len(row) != 1 or not isinstance(row[0]["value"], str) or row[0]["value"].startswith("•"):
                continue  # gone (the page went on), or masked: nothing to compare
            got = row[0]["value"]
            if not same(got, value) and not any(p["key"] == key for p in problems):
                problems.append({"key": key, "field": row[0]["label"][:60], "wanted": value, "got": got,
                                 "problem": f"the field shows {got[:60]!r}, not {value[:60]!r}"})
        out = [f for f in fs if f["selector"] in touched or f["ref"] in touched]
        named = {f["label"] for f in out}
        out += [f for f in fs if any(isinstance(k, str) and not css(k) and k in touched and norm(k).lower() in norm(f["label"]).lower()
                                     for k in values) and f["label"] not in named]
        if twin["keys"]:
            print(f"[fill: {', '.join(repr(k) for k in twin['keys'])} went into copy {twin['i'] + 1} of {twin['n']}"
                  " equal forms on the page (the first one shown)]")
        if problems:
            print(_mask_text(f"[fill: {len(problems)} problem(s): "
                             + "; ".join(f"{p['key']!r}: {p['problem'][:300]}" for p in problems) + "]"
                             + (" [the next click() on this page raises once: nothing was filled for "
                                + ", ".join(k for k in left) + "]" if left else "")))
        if left:
            # MONEY-0017 (B-74): "kWh/Jahr" matched no label, the click after it priced the default 1400 kWh.
            try:
                url = await self._ev("location.href", 3)
            except (RuntimeError, _asyncio.TimeoutError):
                url = None
            self._vf_fill_left = (left, _time.monotonic(), url)
        return out + problems

    async def _fill_left_check(self):
        """click()'s check after a fill() that left keys unfilled (no field,
        or several, fit): raises once, ≤ 5 min after it, on the same page
        (B-74, MONEY-0017); the next click goes on."""
        left = getattr(self, "_vf_fill_left", None)
        if not left:
            return
        self._vf_fill_left = None
        keys, t, url = left
        if _time.monotonic() - t > 300:
            return
        try:
            now = await self._ev("location.href", 3)
        except (RuntimeError, _asyncio.TimeoutError):
            return
        if now != url:
            return
        raise RuntimeError(f"not clicked: the fill() before left {', '.join(keys)} unfilled, so this click would go on"
                           " without them (a price for a default value). Fill them by a label fields() shows, a ref or a"
                           " CSS selector first; or call click() again to go on without them" + await self._where())

    # search()'s finder: 20's _SEARCH_JS with B-23's changes, as replacements (each a no-op once 20
    # has it; requested in passes/pass-09b-requests.md):
    #  - a field counts by its <label> text too; one whose name/placeholder/label says Wunsch/Wohin/
    #    Reiseziel/destination/domain/PLZ (a textarea too: united-domains, DIGI-0106) scores 1.5
    #    (+0.5 in view = enough) when its form has ≤ 3 text fields (never a contact form's PLZ);
    #  - skip links ("Zur Suche springen", a[href^="#"] .sr-only: MOVE-0241) open nothing;
    #  - the box says whether it sits in the header/nav (site: the site-wide box) and which other
    #    fields the page has (own); with no box, the site's search URL: a GET search form's action
    #    with its field (pattern) and an OpenSearch description (opensearch).
    _SEARCH2_JS = (_SEARCH_JS
        .replace('  const mark = (e, v) =>',
                 '  const MORE = /wunsch|wohin|reiseziel|destination|where to|domain|plz|postleitzahl|zip/i;\n'
                 '  const TEXTY = (x) => /^(input|textarea)$/.test(x.localName) && !/^(hidden|checkbox|radio|submit|button|reset|image)$/.test(x.type) && VF.shown(x);\n'
                 '  const small = (e) => !e.form || [...e.form.elements].filter(TEXTY).length <= 3;\n'
                 '  const SKIP = /springen|skip|jump|direkt zu|gehe zu|go to|zum inhalt/i;\n'
                 '  const skip = (b) => b.localName === "a" && /^#./.test(VF.attr(b, "href")) && (SKIP.test(`${VF.label(b)} ${VF.attr(b, "aria-label")}`)'
                 ' || /skip|sr-only|visually-hidden|screen-reader/i.test(VF.attr(b, "class")));\n'
                 '  const SITE = "header, nav, [role=banner], [role=navigation]";\n'
                 '  const mark = (e, v) =>', 1)
        .replace('${VF.attr(e, "title")}`;\n    let s = 0;', '${VF.attr(e, "title")} ${VF.label(e)}`;\n    let s = 0;', 1)
        .replace('    if (WORD.test(attrs)) s += 2;\n', '    if (WORD.test(attrs)) s += 2;\n    else if (MORE.test(attrs) && small(e)) s += 1.5;\n', 1)
        .replace('    return { box: mark(best, "box"), what: (VF.attr(best, "placeholder") || VF.label(best) || best.name || best.id).slice(0, 60), seen };',
                 '    return { box: mark(best, "box"), what: (VF.attr(best, "placeholder") || VF.label(best) || best.name || best.id).slice(0, 60), seen,\n'
                 '             site: !!best.closest?.(SITE), own: fields.filter((e) => e !== best && !e.closest?.(SITE + ", footer"))'
                 '.slice(0, 5).map((e) => (VF.attr(e, "placeholder") || VF.label(e) || e.name || e.id || e.localName).slice(0, 40)) };', 1)
        .replace('find((b) => VF.shown(b) && VF.label(b).length < 30', 'find((b) => VF.shown(b) && !skip(b) && VF.label(b).length < 30', 1)
        .replace('  return { seen, opener: opener ? mark(opener, "open") : null };',
                 '  let pattern = null;\n'
                 '  for (const f of VF.all("form[role=search], [role=search] form, form[action*=search i], form[action*=such i]")) {\n'
                 '    const q = [...f.elements].find((x) => /^(input|textarea)$/.test(x.localName) && x.name && !/^(hidden|checkbox|radio|submit|button|reset|image|password)$/.test(x.type));\n'
                 '    if (!q || (f.method || "get").toLowerCase() !== "get") continue;\n'
                 '    try { const u = new URL(VF.attr(f, "action") || location.href, location.href); u.search = ""; pattern = `${u.href}?${encodeURIComponent(q.name)}={query}`; break; } catch {}\n'
                 '  }\n'
                 '  const os = document.querySelector("link[rel=search][type*=opensearch]");\n'
                 # B-46: a button or combobox whose label is the site's search prompt (camping.info's "Campingort").
                 '  const PROMPT = /^(wohin\\??|reiseziel|urlaubsziel|ziel|zielort|destination|where to\\??|location|ort|campingort|reiseort|urlaubsort|suchbegriff|was suchen sie\\??|what are you looking for\\??)$/i;\n'
                 '  const prompt = opener ? null : VF.all("button, a, [role=button], [role=combobox], [aria-haspopup], summary").find((b) => VF.shown(b) && !skip(b)'
                 ' && PROMPT.test(VF.norm(VF.label(b) || VF.attr(b, "aria-label"))) && !b.closest?.("nav, footer")) ?? null;\n'
                 '  return { seen, opener: opener ? mark(opener, "open") : prompt ? mark(prompt, "open") : null, prompt: !!prompt,\n'
                 '           pattern, opensearch: os ? os.href : null };', 1))
    # search(): after its opener's click, the text field that has the focus counts as the box.
    _FOCUS_BOX_JS = r"""
      const a = VF.active();
      if (!a || !/^(input|textarea)$/.test(a.localName) || !VF.shown(a) || a.readOnly
          || /^(hidden|checkbox|radio|submit|button|reset|password|email|tel|number|date|file|image|range|color)$/.test(a.type)) return {};
      a.setAttribute("data-vf-search", "box");
      return { box: '[data-vf-search="box"]', what: (VF.attr(a, "placeholder") || VF.label(a) || a.name || a.id).slice(0, 60) };"""

    async def _search_url(self, r):
        """The site's search URL for a page with no search box: from a GET
        search form, else from its OpenSearch description; or None."""
        if r.get("pattern"):
            return r["pattern"]
        if not r.get("opensearch"):
            return None
        try:
            xml = await self._ev(f"fetch({_json.dumps(r['opensearch'])}).then((r) => r.ok ? r.text() : '')"
                                 ".catch(() => '')", 5)
        except (RuntimeError, _asyncio.TimeoutError):
            return None
        m = _re.search(r'<Url\b[^>]*type="text/html"[^>]*template="([^"]+)"', xml or "") or \
            _re.search(r'<Url\b[^>]*template="([^"]+)"[^>]*type="text/html"', xml or "")
        return m.group(1).replace("&amp;", "&").replace("{searchTerms}", "{query}") if m else None

    async def search(self, query, *, timeout=20, serial=False):
        """Search with the site's own search box: finds the visible one
        (type=search, role=searchbox, or a field or textarea whose name/id/
        placeholder/aria-label/label says q/query/search/suche…; Wunsch/
        Wohin/PLZ/domain in a small form; else clicks a search icon button
        that opens one, never a skip link, or a button whose label is the
        site's search prompt (Wohin, Reiseziel, Campingort…) and takes the
        field that then has the focus), types `query`, submits (Enter,
        else the search button), waits for the result page. A cookie banner
        gets rejected (or hidden) first. Returns {url,
        text}: the result page's URL and the first ~800 chars of its main
        text; plus "note" (also printed) when it used the site-wide box in
        the header while the page has fields of its own. Raises naming what
        it saw when there is no search box, with the site's search URL
        pattern when a search form or OpenSearch link gives one. B-77: the
        4th submit (search / type(enter=True) / press("Enter")) on one tab
        in one cell raises SerialGoto; serial=True."""
        self._serial_step("submit", serial, _sys._getframe(0))
        try:
            await self._consent_clear()  # a banner over the box swallows the typing (SHOP-0015)
        except RuntimeError:
            pass
        r = await self._ev(_vf(self._SEARCH2_JS, OPEN=False))
        if not r.get("box") and r.get("opener"):
            await self.click(r["opener"], timeout=5)
            deadline = _time.monotonic() + 3
            while not r.get("box") and _time.monotonic() < deadline:
                await _asyncio.sleep(0.25)
                r = await self._ev(_vf(self._SEARCH2_JS, OPEN=False))
                if not r.get("box"):
                    r = {**r, **await self._ev(_vf(self._FOCUS_BOX_JS))}
        if not r.get("box"):
            url = await self._search_url(r)
            raise RuntimeError("no search box on this page (looked for type=search, role=searchbox, fields named"
                               " q/query/search/suche…, search icon buttons); fields seen: "
                               f"{r.get('seen') or 'none'}"
                               + (f"; the site's search URL: {_mask_url(url)} (goto() it with the query)" if url else "")
                               + await self._where())
        note = None
        if r.get("site") and r.get("own"):
            note = (f"used the site-wide search box {r['what']!r} in the header; the page's own fields: "
                    f"{', '.join(repr(o) for o in r['own'])}: type() into one of those if that is the search you mean")
            print(f"[search: {note}]")
        before = await self._ev('[location.href, (document.body?.innerText ?? "").length]', 5)
        # A textarea (a domain list) takes Enter as a new line: its form's button submits it.
        area = await self._ev(_vf("return VF.all(BOX)[0]?.localName === 'textarea';", BOX=r["box"]), 5)
        await self.type(r["box"], query, enter=not area, _echo=False)

        async def moved(secs):
            deadline = _time.monotonic() + secs
            while _time.monotonic() < deadline:
                try:
                    now = await self._ev('[location.href, (document.body?.innerText ?? "").length]', 5)
                except (RuntimeError, _asyncio.TimeoutError):
                    now = None  # mid-navigation
                if now is None or now[0] != before[0] or abs(now[1] - before[1]) > 200:
                    return True
                await _asyncio.sleep(0.25)
            return False

        if area or not await moved(4):
            go = await self._ev(_vf(self._SEARCH2_JS, OPEN=True))
            if go.get("submit"):
                await self.click(go["submit"], timeout=5)
            if not await moved(timeout / 2):
                raise RuntimeError(f"typed {query!r} into the search box {r['what']!r} and submitted, but the page"
                                   f" didn't change{await self._where()}")
        try:
            await self.wait_load(timeout, _dom=True)
        except TimeoutError:
            pass  # it shows what it shows
        await self._settle()
        await self.idle(timeout=5, quiet=0.5)  # result lists render by XHR after the URL changed (SE-09)
        text = await self.text("main", limit=800)
        out = {"url": await self.url(), "text": text[:800]}
        if note:
            out["note"] = note
        return out

    def _ref_spec(self, selector, ref):
        """find()'s spec for a CSS selector or an observe() ref; a
        [data-vf-ref=N] selector counts as ref N."""
        m = _re.fullmatch(r'\[data-vf-ref="?(\d+)"?\]', selector or "")
        if ref is None and m:
            ref = int(m.group(1))
        if ref is None:
            return {"css": selector}
        return {"ref": ref, "fb": self._refs.get(ref)}

    # Reject buttons of consent platforms, by id/class/attribute (each only ever rejects), tried
    # before VF.consent()'s label rules (B-46): Cookiebot, consentmanager.net, Borlabs, Complianz,
    # CookieYes, Cookie Notice, CookieLawInfo, Klaro, Osano, iubenda, Axeptio, CookieFirst, Didomi,
    # CCM19, Usercentrics, OneTrust, TrustArc, Termly, Moove, Cookie Script, Piwik PRO, Shopify.
    # Returns its label (or id), else null; CFRAME: whether a consent platform's <iframe> shows.
    _CMP_REJECT = ("#CybotCookiebotDialogBodyButtonDecline, #CybotCookiebotDialogBodyLevelButtonLevelOptinDeclineAll,"
                   " #cmpwelcomebtnno, .cmpboxbtnno, #cmpbntnotxt, a.cmptxt_btn_no, [data-cookie-refuse], ._brlbs-refuse-btn,"
                   " a._brlbs-refuse, .cmplz-btn.cmplz-deny, .cky-btn-reject, #cn-refuse-cookie,"
                   " #cookie_action_close_header_reject, [data-cli_action=reject], .cm-btn-decline, .cn-decline,"
                   " .osano-cm-denyAll, .iubenda-cs-reject-btn, #axeptio_btn_dismiss, [data-cookiefirst-action=reject],"
                   " #didomi-notice-disagree-button, .didomi-continue-without-agreeing, .ccm--decline-cookies,"
                   " [data-testid=uc-deny-all-button], #onetrust-reject-all-handler, .ot-pc-refuse-all-handler,"
                   " #truste-consent-required, [data-tid=banner-decline], .moove-gdpr-infobar-reject-btn,"
                   " #cookiescript_reject, #ppms_cm_reject-all, .shopify-pc__banner__btn-decline")
    _CMP_JS = r"""
      const CF = /consent|cmp|privacy-mgmt|sourcepoint|consensu|trustarc|truste|cookie|usercentrics|onetrust|didomi|sp_message/i;
      const cframe = VF.all("iframe").some((f) => VF.shown(f) && CF.test(`${f.id} ${VF.attr(f, "title")} ${VF.attr(f, "name")} ${VF.attr(f, "src")}`));
      for (const e of VF.all(REJECT)) {
        if (!VF.shown(e) || e.disabled) continue;
        e.scrollIntoView?.({ block: "center", behavior: "instant" });
        (e.shadowRoot?.querySelector("button, a[href], [role=button]") ?? e).click();
        return [VF.norm(VF.label(e)).slice(0, 60) || e.id || e.localName, cframe];
      }
      return [VF.consent(), cframe];"""
    # In a consent platform's own frame (Sourcepoint, TrustArc): its reject or necessary-only button.
    _CMP_FRAME_JS = r"""
      const RJ = /^(alle ablehnen|alles ablehnen|ablehnen|nicht zustimmen|zustimmung verweigern|einwilligung (ablehnen|verweigern)|reject( all)?|decline( all)?|deny( all)?|refuse( all)?|nur (technisch )?(notwendige|erforderliche|essenzielle|essentielle)( cookies)?( (akzeptieren|zulassen|erlauben|verwenden))?|(use |accept |allow )?(only )?(strictly )?(necessary|essential)( cookies)?( only)?|(weiter|fortfahren) ohne (einwilligung|zustimmung|cookies)|continue without (accepting|consent|agreeing))$/;
      for (const e of VF.all("button, a, [role=button], input[type=button], input[type=submit]")) {
        const l = VF.norm(VF.label(e) || VF.attr(e, "title") || VF.attr(e, "aria-label")).toLowerCase().replace(/[^\p{L}\p{N} -]+/gu, " ").replace(/\s+/g, " ").trim();
        if (VF.shown(e) && RJ.test(l)) { e.click(); return VF.norm(VF.label(e)).slice(0, 60); }
      }
      return null;"""
    _CMP_HOST = _re.compile(r"consent|cmp|privacy-mgmt|sourcepoint|consensu|trustarc|truste|cookie|usercentrics|onetrust|didomi", _re.I)

    async def dismiss_consent(self, timeout=0):
        """Click the reject or necessary-only button of a cookie banner
        (German or English, in open shadow roots too; never one that accepts
        tracking): a consent platform's reject button by its id first
        (Cookiebot's "Einstellungen"-labelled decline, consentmanager,
        OneTrust, Usercentrics, Didomi, Borlabs, …), else by its label; a
        Usercentrics banner without one gets its settings layer opened and
        rejected there; a consent platform's frame (Sourcepoint) gets its
        reject clicked in there. Polls up to `timeout` s. Returns its label,
        or None when there is none. Every page load already does this by
        itself."""
        deadline = _time.monotonic() + timeout
        framed = False
        while True:
            try:
                hit, cframe = await self._ev(_vf(self._CMP_JS, REJECT=self._CMP_REJECT), timeout=10) or (None, False)
            except TabStuck:
                raise
            except (RuntimeError, _asyncio.TimeoutError):
                hit, cframe = None, False
            if isinstance(hit, dict):  # it opened the banner's second layer: the reject is there
                deadline, hit = max(deadline, _time.monotonic() + 4), None
            if not hit and cframe and not framed:
                framed = True
                hit = await self._consent_frames()
            if hit or _time.monotonic() >= deadline:
                return hit
            await _asyncio.sleep(0.3)

    async def _consent_frames(self):
        """The reject button in a shown consent platform frame: its label
        and " (in frame <host>)", or None."""
        fs = self._frames()
        try:
            await fs.load()
            for d in fs.shown_docs():
                if d.parent is None or not self._CMP_HOST.search(f"{d.host} {d.url}"):
                    continue
                try:
                    got = await d.ev(_vf(self._CMP_FRAME_JS), 5)
                except (RuntimeError, _asyncio.TimeoutError):
                    continue
                if got:
                    return f"{got} (in frame {d.host})"
        except (RuntimeError, _asyncio.TimeoutError, _NoFrame):
            return None
        finally:
            await fs.close()
        return None

    # A consent-settings opener that is no banner (B-59, MONEY-0006): a footer link such as
    # "Cookie-Einstellungen"/"Privatsphäre" (in <footer>/contentinfo, not in a fixed/sticky box or
    # dialog there), a CMP's revisit/preferences button by its class, or a link/button outside any
    # lifted box whose href/onclick reopens a CMP (Cookiebot.renew, UC_UI.show…, OneTrust's
    # ToggleInfoDisplay, Sourcepoint's privacy manager, klaro.show, …). Never clicked, hidden, or
    # taken as a sign that a banner shows.
    _CONSENT_OPENER_JS = r"""
      const OPENER_CODE = /Cookiebot\.(renew|show)|UC_UI\.show|showSecondLayer|ToggleInfoDisplay|__cmp\(|__tcfapi|_sp_\.|loadPrivacyManagerModal|klaro\.show|BorlabsCookie|CookieConsent\.(renew|show)|cmplz_|cookieyes|ccm19|CCM\.open|showConsent|openConsent|showCookie|openCookie|cookieSettings|consentmanager/i;
      const OPENER_CLS = /(^|\s)(ot-sdk-show-settings|optanon-show-settings|cky-btn-revisit|cky-revisit[\w-]*|cmplz-manage-consent|cmplz-show-banner|_brlbs-btn-cookie-preference|borlabs-cookie-preference|uc-privacy-button|cookiebot-renew|cc-revoke|ccm-settings-summoner|iubenda-cs-preferences-link|termly-display-preferences|osano-cm-widget)(\s|$)/i;
      const raised = (n) => { const s = getComputedStyle(n);
        return /^(fixed|sticky)$/.test(s.position) || (s.position === "absolute" && Number(s.zIndex) >= 100) || n.localName === "dialog"
          || /^(dialog|alertdialog)$/.test(VF.attr(n, "role")) || VF.attr(n, "aria-modal") === "true"; };
      const opener = (e) => {
        const b = e.closest?.("a, button, [role=button]") ?? e;
        if (OPENER_CLS.test(VF.attr(b, "class")) || b.id === "ot-sdk-btn" || VF.attr(b, "data-cc") === "c-settings") return true;
        let up = false;
        for (let n = b, i = 0; n && n !== document.body && i < 25; n = VF.up(n), i++) if (raised(n)) { up = true; break; }
        if (!up && (OPENER_CODE.test(VF.attr(b, "onclick")) || (/^javascript:/i.test(VF.attr(b, "href")) && OPENER_CODE.test(VF.attr(b, "href"))))) return true;
        const foot = b.closest?.("footer, [role=contentinfo]");
        if (!foot) return false;
        for (let n = b; n && n !== foot; n = VF.up(n)) if (raised(n)) return false;
        return true;
      };
    """
    # close_dialog() with no open layer (B-59): whether a cookie banner shows at all, before any
    # consent click: a shown CMP reject button (REJECT) or consent frame, a consent page
    # (consent.google.com), else a shown accept/reject/save/ok button (no opener) in a consent
    # banner box that is lifted or carries a CMP id/class, and isn't a whole page. Its label, or null.
    _CONSENT_SHOWN_JS = _CONSENT_OPENER_JS + r"""
      if (/^consent\./.test(location.hostname)) return "consent page";
      const W = innerWidth, H = innerHeight;
      const CF = /consent|cmp|privacy-mgmt|sourcepoint|consensu|trustarc|truste|cookie|usercentrics|onetrust|didomi|sp_message/i;
      for (const f of VF.all("iframe")) {
        if (!VF.shown(f) || !CF.test(`${f.id} ${VF.attr(f, "title")} ${VF.attr(f, "name")} ${VF.attr(f, "src")}`)) continue;
        const r = f.getBoundingClientRect();
        if (r.width * r.height >= 0.02 * W * H) return "a consent frame";
      }
      for (const e of VF.all(REJECT)) if (VF.shown(e) && !opener(e)) return VF.norm(VF.label(e)).slice(0, 40) || e.id || e.localName;
      const WAY = /akzeptier|zustimm|accept|agree|allow|erlauben|zulassen|einverstanden|verstanden|got it|^ok$|ablehnen|reject|decline|deny|refuse|notwendig|erforderlich|necessary|essential|speichern|save|bestätigen|confirm/i;
      const CMP = /cookie|consent|\bcmp|gdpr|dsgvo|usercentrics|onetrust|didomi|borlabs|cookiebot|klaro|einwillig/i;
      for (const b of VF.all("button, a, [role=button], input[type=button], input[type=submit]")) {
        if (!VF.shown(b) || opener(b)) continue;
        const l = VF.norm(VF.label(b) || VF.attr(b, "aria-label") || b.value || "");
        if (!l || l.length > 60 || !WAY.test(l) || !VF.banner(b)) continue;
        const box = VF.bannerOf(b);
        if (!box || VF.norm(box.innerText ?? "").length > 6000) continue;
        let up = false;
        for (let n = b, i = 0; n && n !== document.body && i < 25; n = VF.up(n), i++) if (raised(n)) { up = true; break; }
        if (up || CMP.test(`${box.id} ${VF.attr(box, "class")}`)) return l.slice(0, 40);
      }
      return null;"""

    # Last resort for a cookie banner no button gets out of (B-46): each shown banner box (around a
    # button whose label accepts or opens settings, in a consent banner) at its outermost lifted
    # ancestor, plus a text-less scrim over ≥ half the viewport, get display:none !important
    # (data-vf-hidden), and a scroll lock on <html>/<body> is lifted. Nothing is clicked or accepted.
    # A consent-settings opener (a footer link) is no such button (B-59).
    _CONSENT_HIDE_JS = _CONSENT_OPENER_JS + r"""
      const ACC = /akzeptier|zustimm|accept|agree|allow|erlauben|zulassen|einverstanden|einstellungen|settings|anpassen|customi|ok|verstanden|got it/i;
      const lifted = (n) => { const s = getComputedStyle(n);
        return /^(fixed|sticky)$/.test(s.position) || (s.position === "absolute" && Number(s.zIndex) >= 100); };
      const out = [], found = [];
      for (const b of VF.all("button, a, [role=button], input[type=button], input[type=submit]")) {
        if (!VF.shown(b) || opener(b) || !ACC.test(VF.label(b) || VF.attr(b, "aria-label")) || !VF.banner(b)) continue;
        let box = VF.bannerOf(b);
        for (let n = box, i = 0; n && n !== document.body && n !== document.documentElement && i < 30; n = VF.up(n), i++) if (lifted(n)) box = n;
        if (!box || box === document.body || found.some((f) => VF.inside(box, f))) continue;
        if (!lifted(box) && !/cookie|consent|cmp|einwillig/i.test(`${box.id} ${VF.attr(box, "class")}`)) continue;
        found.push(box);
      }
      if (!found.length) return null;
      const W = innerWidth, H = innerHeight;
      for (const e of VF.all("div, section, aside")) {
        if (!VF.shown(e) || getComputedStyle(e).position !== "fixed" || VF.norm(e.innerText ?? "") || found.includes(e)) continue;
        const r = e.getBoundingClientRect();
        if (r.width * r.height >= 0.5 * W * H) found.push(e);
      }
      for (const e of found) {
        const cls = typeof e.className === "string" && e.className.trim() ? "." + e.className.trim().split(/\s+/)[0] : "";
        out.push(`${e.localName}${e.id ? "#" + e.id : cls}`);
        e.setAttribute("data-vf-hidden", e.style.display || "-");
        e.style.setProperty("display", "none", "important");
      }
      for (const r of [document.documentElement, document.body])
        if (r && getComputedStyle(r).overflow === "hidden") { r.setAttribute("data-vf-unlocked", r.style.overflow || "-"); r.style.setProperty("overflow", "auto", "important"); }
      return { hidden: out };"""

    async def _consent_clear(self):
        """Out of a cookie banner by any means that accepts nothing: reject
        (dismiss_consent), else settings → save only the necessary
        (_consent_out), else hide the banner (display:none). What it did, or
        None when no banner shows."""
        hit = await self.dismiss_consent()
        if hit:
            return f"rejected the cookie banner ({hit!r})"
        try:
            said, why = await self._consent_out(), None
        except RuntimeError as e:
            said, why = None, str(e)
        if said:
            return said
        try:
            h = await self._ev(_vf(self._CONSENT_HIDE_JS), 10)
        except (RuntimeError, _asyncio.TimeoutError):
            h = None
        if h and h.get("hidden"):
            return ((why.split(" (page:")[0] + "; so " if why else "a cookie banner without a reject or settings button: ")
                    + f"hid {', '.join(h['hidden'][:3])} (display:none; nothing accepted)")
        if why:
            raise RuntimeError(why)
        return None

    # overlay_hide()'s page side (B-21). Which boxes: CSS_'s shown matches; else LAYER: the layer
    # close_dialog kept (vf-layer) plus a scrim left over the centre; else POINT: what covers that
    # point (the last click's "covered by"): its outermost lifted box (fixed, sticky, absolute with
    # z-index ≥ 100), or the static branch around it, never one holding the click target (vf-el);
    # else the lifted boxes (or iframes: MOVE-0023's about:srcdoc popup) over the viewport's centre
    # and 4 points around it that cover ≥ MIN of it and leave ≥ 300 chars of page text beside them,
    # plus (CHATS) lifted chat iframes (chat/messenger/intercom/…, SHOP-0018's Neocom) ≥ 3 % of it.
    # HIDE: display:none !important on each (data-vf-hidden keeps the old inline display).
    _OVERLAY_JS = r"""
      const W = innerWidth, H = innerHeight;
      const area = (e) => { const r = e.getBoundingClientRect();
        return Math.max(0, Math.min(r.right, W) - Math.max(r.left, 0)) * Math.max(0, Math.min(r.bottom, H) - Math.max(r.top, 0)); };
      const CHAT = /chat|messenger|intercom|zendesk|zopim|livechat|tawk|drift|hubspot|crisp|tidio|neocom|freshchat|olark|userlike|smartsupp|helpscout|beacon/i;
      const sig = (e) => `${e.id ?? ""} ${typeof e.className === "string" ? e.className : VF.attr(e, "class")} ${VF.attr(e, "title")} ${VF.attr(e, "name")} ${VF.attr(e, "src")} ${VF.attr(e, "aria-label")}`;
      const lifted = (n) => { const s = getComputedStyle(n);
        return s.position === "fixed" || s.position === "sticky" || (s.position === "absolute" && Number(s.zIndex) >= 100); };
      const topBox = (h) => {
        let out = null;
        for (let n = h, i = 0; n && n !== document.body && n !== document.documentElement && i < 40; n = VF.up(n), i++) if (lifted(n)) out = n;
        return out;
      };
      const host = (f) => { try { return new URL(f.src, location.href).host || "about:srcdoc"; } catch { return "about:srcdoc"; } };
      const desc = (e) => {
        const cls = typeof e.className === "string" && e.className.trim() ? "." + e.className.trim().split(/\s+/)[0] : "";
        const f = e.localName === "iframe" ? e : e.querySelector?.("iframe");
        const what = f && !VF.norm(e.innerText ?? "") ? `iframe ${VF.attr(f, "title") || host(f)}` : VF.norm(e.innerText ?? "").slice(0, 60);
        return `${e.localName}${e.id ? "#" + e.id : cls}` + (what ? ` (${what})` : "");
      };
      const bodyLen = VF.norm(document.body?.innerText ?? "").length;
      const rest = (e) => bodyLen - VF.norm(e.innerText ?? "").length;
      const el = window[Symbol.for("vf-el")], target = el?.isConnected ? el : null;
      const holds = (c) => !!target && VF.inside(target, c);
      let found = [];
      if (CSS_) {
        found = VF.all(CSS_).filter(VF.shown);
        if (!found.length) return { none: `nothing shown matches ${CSS_}` };
      } else if (LAYER) {
        const l = window[Symbol.for("vf-layer")];
        if (l?.isConnected && VF.shown(l)) found.push(l);
      } else if (POINT) {
        const h = VF.hitAt(POINT[0], POINT[1]);
        let c = h && topBox(h);
        if (h && (!c || holds(c))) {  // static, or a lifted box around the target too: the branch over it
          c = h;
          for (let n = VF.up(h); n && n !== document.body && n !== document.documentElement && !holds(n); n = VF.up(n)) c = n;
        }
        if (c && !holds(c) && c !== document.body) found.push(c);
      }
      if (!CSS_ && (LAYER || !found.length)) {
        for (const [fx, fy] of [[0.5, 0.5], [0.25, 0.5], [0.75, 0.5], [0.5, 0.25], [0.5, 0.75]]) {
          const h = VF.hitAt(W * fx, H * fy), c = h && topBox(h);
          if (!c || found.some((f) => VF.inside(c, f) || VF.inside(f, c)) || c.querySelector?.("main, [role=main]") || holds(c)) continue;
          // LAYER: only a scrim (no text) is left of the dialog; else anything that covers enough.
          if (LAYER ? !VF.norm(c.innerText ?? "") && area(c) >= 0.5 * W * H : area(c) >= MIN * W * H && rest(c) >= 300) found.push(c);
        }
        if (CHATS && !LAYER) for (const f of VF.all("iframe")) {
          const c = VF.shown(f) && area(f) >= 0.03 * W * H ? topBox(f) : null;
          if (c && (CHAT.test(sig(f)) || CHAT.test(sig(c))) && !holds(c) && !found.some((x) => VF.inside(c, x) || VF.inside(x, c))) found.push(c);
        }
      }
      if (!found.length) return { none: "nothing covers the page" };
      if (!HIDE) return { found: found.map(desc) };
      const out = found.map(desc);
      for (const e of found) { e.setAttribute("data-vf-hidden", e.style.display || "-"); e.style.setProperty("display", "none", "important"); }
      return { hidden: out };"""

    # close_dialog's way out of a cookie banner without a reject button (MOVE-0234). Only buttons
    # in a fixed/sticky banner box that speaks of cookies (never a privacy settings page's form).
    # STEP "open": its settings button ("Anpassen", "Einstellungen", "Customize", …) kept as the
    # next click's element; "save": in the banner(s) now shown, every checked optional toggle
    # unchecked, then its save-the-selection button pressed (never one that accepts all), kept in
    # vf-consent-save; "left": whether that button still shows. A consent-settings opener (a footer
    # "Cookie-Einstellungen" link) is never one of its buttons (B-59).
    _CONSENT_SAVE_JS = _CONSENT_OPENER_JS + r"""
      const KEPT = Symbol.for("vf-consent-save");
      if (STEP === "left") { const s = window[KEPT]; return !!s && s.isConnected && VF.shown(s); }
      const SETTINGS = /^(anpassen|einstellungen|cookie[- ]?einstellungen( anpassen)?|einstellungen (anpassen|verwalten|ändern)|einstellungen oder ablehnen|ablehnen oder einstellungen|individuelle einstellungen|individuell (anpassen|einstellen|konfigurieren)|auswahl anpassen|mehr optionen|optionen( verwalten)?|details|zwecke( anzeigen)?|konfigurieren|präferenzen|cookie[- ]?präferenzen|datenschutz[- ]?einstellungen|privatsphäre[- ]?einstellungen|customi[sz]e( settings| choices)?|settings|settings or reject|reject or settings|cookie settings|privacy settings|manage( options| preferences| cookies| settings| choices)?|preferences|more options|configure|let me choose|show purposes)$/i;
      const SAVE = /^(auswahl speichern|speichern|einstellungen speichern|einstellungen bestätigen|präferenzen speichern|auswahl bestätigen|auswahl übernehmen|übernehmen|bestätigen|nur ausgewählte( akzeptieren| erlauben| zulassen)?|ausgewählte (akzeptieren|erlauben|zulassen)|auswahl (akzeptieren|erlauben|zulassen)|weiter mit auswahl|save|save (settings|preferences|choices|selection|my choices|& exit|and exit)|confirm (choices|selection|my choices)|allow selection|accept selected|accept selection|zustimmung speichern|speichern (und|&) schließen|auswahl speichern (und|&) schließen|save (and|&) (close|exit))$/i;
      const ALL = /(^|\s)(alle|all|everything)(\s|$)/i;
      const name = (e) => VF.norm(VF.label(e) || VF.attr(e, "aria-label")).toLowerCase().replace(/[^\p{L}\p{N} &-]+/gu, " ").replace(/\s+/g, " ").trim();
      // On top of the page: fixed/sticky, an absolute box with z-index ≥ 100, or a dialog (B-46: camping.info, autohero).
      const lifted = (e) => { for (let n = e, i = 0; n && i < 25; n = VF.up(n), i++) { const s = getComputedStyle(n);
        if (/^(fixed|sticky)$/.test(s.position) || (s.position === "absolute" && Number(s.zIndex) >= 100) || n.localName === "dialog"
          || /^(dialog|alertdialog)$/.test(VF.attr(n, "role")) || VF.attr(n, "aria-modal") === "true") return true; } return false; };
      const cookies = (e) => /cookie|consent|einwillig|tracking/i.test((VF.bannerOf(e)?.innerText ?? "").slice(0, 5000));
      const btns = VF.all("button, a, [role=button], input[type=button], input[type=submit]")
        .filter((e) => VF.shown(e) && !opener(e) && VF.banner(e) && lifted(e) && cookies(e));
      if (STEP === "open") {
        const b = btns.find((e) => SETTINGS.test(name(e)));
        if (!b) return null;
        window[Symbol.for("vf-el")] = b;
        return VF.norm(VF.label(b)).slice(0, 60);
      }
      const save = btns.find((e) => SAVE.test(name(e)) && !ALL.test(name(e)));
      if (!save) return null;
      const box = VF.bannerOf(save);
      let off = 0;
      for (const t of VF.all("input[type=checkbox], [role=switch], [role=checkbox]")) {
        if (!VF.inside(t, box) || t.disabled || VF.attr(t, "aria-disabled") === "true" || !VF.checked(t)) continue;
        (t.localName === "input" && !VF.shown(t) ? (t.labels?.[0] ?? t) : t).click();
        off++;
      }
      window[KEPT] = save;
      save.click();
      return { saved: VF.norm(VF.label(save)).slice(0, 60), off };"""

    async def _consent_out(self):
        """A cookie banner without a reject button: open its settings, then
        save the selection with every optional toggle off. What it did, or
        None when no such banner shows; raises when it stays open."""
        opened = await self._ev(_vf(self._CONSENT_SAVE_JS, STEP="open"))
        if opened:
            await self.click(_spec={"el": True}, timeout=3)
            hit = await self.dismiss_consent(timeout=2)  # its second layer may have a reject button
            if hit:
                return f"rejected the cookie banner in its settings ({opened!r} → {hit!r})"
        saved = await self._ev(_vf(self._CONSENT_SAVE_JS, STEP="save"))  # (that layer may be open already)
        if not saved:
            if opened:
                raise RuntimeError(f"a cookie banner without a reject button: its {opened!r} opened no layer with a"
                                   " save-the-selection button" + await self._where())
            return None
        await _asyncio.sleep(1)
        try:
            left = await self._ev(_vf(self._CONSENT_SAVE_JS, STEP="left"), 5)
        except (RuntimeError, _asyncio.TimeoutError):
            left = False  # it reloaded the page
        if left:
            raise RuntimeError(f"a cookie banner without a reject button: {saved['saved']!r} left it open" + await self._where())
        return ("cookie banner without a reject button: " + (f"opened {opened!r}, " if opened else "")
                + f"unchecked {saved['off']} optional toggle(s), saved with {saved['saved']!r}")

    async def overlay_hide(self, css=None):
        """Hide what covers the page or a click target, when no close
        control does it (a chat window over the buy button, a full-page
        popup iframe, a layer close_dialog() couldn't close): display:none
        on it; nothing is clicked. `css`: those elements; else what covered
        the last click()'s target ("covered by …", the last 2 minutes),
        else the fixed (or on-top absolute) box or iframe over the middle of
        the viewport that leaves page text beside it, and chat widgets'
        iframes. Returns what it hid ("hid div#chat (iframe Neocom)"), or
        "nothing covers the page". Top document only."""
        point = getattr(self, "_vf_cover", None)
        point = point[:2] if css is None and point and _time.monotonic() - point[2] < 120 else None
        self._vf_cover = None
        r = await self._ev(_vf(self._OVERLAY_JS, CSS_=css, LAYER=False, POINT=point, MIN=0.1, CHATS=True, HIDE=True), 10)
        if r.get("none"):
            if css is not None:
                raise RuntimeError(r["none"] + await self._where())
            return r["none"]
        return f"hid {', '.join(r['hidden'][:4])} (display:none; nothing clicked)"

    async def close_dialog(self, *, timeout=5):
        """Close the top open dialog, overlay or drawer (what text() shows as
        [dialog]): its close control (aria-label/title/text "Schließen",
        "Close", "×", "Dialog schließen", an icon-only close button), else,
        when it has none, a decline ("Später", "Nein danke", "Überspringen",
        "Not now"), else Escape. Never a control whose label accepts, saves,
        subscribes or goes on. A cookie banner gets dismiss_consent() (a
        consent platform's reject by its id first); one without a reject
        button gets its settings ("Anpassen", "Konfigurieren", …) opened,
        the optional toggles unchecked and the selection saved; a banner
        with neither (only "Alle akzeptieren") gets hidden, its scrim too
        (display:none; nothing accepted). A footer link that opens the
        consent settings ("Cookie-Einstellungen") is never clicked. A layer
        still open after all that, and with no dialog open a full-page popup
        iframe or a chat window over the page, get hidden (overlay_hide).
        With no dialog, cookie banner or such layer it does nothing and says
        "nothing to close". An open date picker's "Cancel"/"Abbrechen"/
        "Clear" is never clicked (it drops the picked dates): its own
        "Apply"/"Confirm selection"/"Übernehmen" instead, else it raises.
        Returns what it did;
        RuntimeError for a dialog that is the page itself, or a cookie
        banner it can't get out of."""
        r = await self._ev(_vf(_CLOSE_DIALOG_JS, GONE=False))
        if r.get("none"):
            # A cookie bar no layer test sees: its reject button, else (MOVE-0234) settings + save, else hidden (B-46);
            # only when a banner shows at all (B-59: a wizard reset by a footer "Cookie-Einstellungen" click).
            try:
                shows = await self._ev(_vf(self._CONSENT_SHOWN_JS, REJECT=self._CMP_REJECT), 10)
            except (RuntimeError, _asyncio.TimeoutError):
                shows = None
            if shows:
                said = await self._consent_clear()
                if said:
                    return said
            # Layers no markup calls one and that hold no text: an iframe popup, a chat window (B-21).
            h = await self._ev(_vf(self._OVERLAY_JS, CSS_=None, LAYER=False, POINT=None, MIN=0.25, CHATS=True, HIDE=True), 10)
            if h.get("hidden"):
                return f"no open dialog; hid {', '.join(h['hidden'][:4])} over the page (overlay_hide: display:none)"
            return "nothing to close: no open dialog, cookie banner or layer over the page (nothing clicked)"
        if r.get("consent"):
            said = await self._consent_clear()
            if said:
                return said
            raise RuntimeError(f"the top layer is a cookie banner without a reject button: {r['text']!r}{await self._where()}")
        if r.get("page"):
            raise RuntimeError(f"this dialog is the page (nothing else shows beside it): {r['text']!r}; close_dialog()"
                               " leaves it: use its own buttons or goto() elsewhere" + await self._where())

        async def gone():
            end = _time.monotonic() + timeout
            while True:
                try:
                    if await self._ev(_vf(_CLOSE_DIALOG_JS, GONE=True), 5):
                        return True
                except (RuntimeError, _asyncio.TimeoutError):
                    return True  # the document went away: a navigation
                if _time.monotonic() > end:
                    return False
                await _asyncio.sleep(0.2)

        # A date picker with its own Apply/Confirm (B-72, MOVE-0224): Cancel, × or Escape drop the dates picked
        # in it, so its Apply instead; a Cancel/Clear with no Apply beside it isn't clicked at all.
        try:
            cal = await self._ev(_vf(self._CAL_CLOSE_JS, ESC=bool(r.get("escape"))), 5)
        except (RuntimeError, _asyncio.TimeoutError):
            cal = None
        ok = await self._cal_apply() if cal else ""
        way = "Escape" if r.get("escape") else repr(r["found"])
        if ok:
            how = f"{ok[2:]} (not {way}, which would drop the dates picked in it)"
            if await gone():
                return f"closed the calendar {r['text'][:80]!r}: {how}"
        elif cal and cal.get("cancel"):
            raise RuntimeError(f"the open dialog is a calendar whose way out is {way}, which drops the dates picked in it:"
                               " not clicked; pick_range()/pick_date() confirm it, or click"
                               f"(text={r['found']!r}) on purpose" + await self._where())
        elif r.get("escape"):
            how = "pressed Escape (it has no close control)"
            await self._act(self._cdp.press("Escape"))
        else:
            how = f"clicked {r['found']!r}" + (" (it has no close control)" if r["how"] == "decline" else "")
            try:
                await self.click(_spec={"el": True}, timeout=3)
            except RuntimeError as e:
                how = f"clicking {r['found']!r} failed ({str(e)[:80]})"
        if await gone():
            return f"closed the dialog {r['text'][:80]!r}: {how}"
        if not r.get("escape"):
            await self._act(self._cdp.press("Escape"))
            how += ", then Escape"
            if await gone():
                return f"closed the dialog {r['text'][:80]!r}: {how}"
        # Still there (MOVE-0258's CHECK24 overlay): hide it and a scrim left behind.
        h = await self._ev(_vf(self._OVERLAY_JS, CSS_=None, LAYER=True, POINT=None, MIN=0, CHATS=False, HIDE=True), 10)
        if h.get("hidden") and await gone():
            return f"{how}, but it stayed: hid {', '.join(h['hidden'][:3])} (overlay_hide: display:none)"
        raise RuntimeError(f"{how}, but the dialog {r['text'][:120]!r} is still open{await self._where()}")

    # click(text=)'s ranking (B-18), put in front of _LOCATE_JS: VF.find for a text spec without
    # `within` (that keeps VF.find's own) collects the candidates as VF's byText does, but:
    #  - the target of text in a weak clickable (tabindex/onclick/a cursor:pointer run; not a label
    #    or toggle: click checks its state) that holds much more text than it (a dialog or card
    #    container: SHOP-0247, DIGI-0106) is the innermost clickable box around the text, not the
    #    container (its centre is elsewhere);
    #  - order: in an open layer first (dialogs, popovers, what an aria-expanded control opened:
    #    MOVE-0320); then whole-word matches before ones only inside a longer word; clickable;
    #    then exact > starting with it > containing it (MOVE-0021); button/a/[role=button|option|
    #    tab|menuitem…] before other clickables; one not holding another match before the box
    #    around it; then for a non-exact match the shortest text; then reading order (the first
    #    card, not a later one with a shorter text: MOVE-0317, B-40);
    #  - only matches inside longer words ("Weiter" in "WEITERE TICKETS", MOVE-0049): none, and
    #    VF.partial names them (click raises);
    #  - a label around a radio/checkbox that also holds another control (bauhaus' store button
    #    inside "Reservieren & Abholen", SHOP-0017): its own shown input, else the innermost box
    #    with the text outside that control, never the label's centre (B-40);
    #  - VF.info: {layer: the pick sits in an open layer, tier: its match (4 exact … 1)}; VF.ties:
    #    {n, ctx} when several matches rank the same (click says "1 of N").
    _RANK_JS = r"""
      {
        const orig = VF.find;
        const INVIS = /[\u00ad\u200b-\u200d\u2060\ufeff]/g;
        // ß/ẞ as ss: CSS text-transform: uppercase renders "Größe" as "GRÖSSE" (SHOP-0325, B-64); case never counts.
        const squash = (s) => (s || "").replace(INVIS, "").replace(/\s+/g, "").toLowerCase().replace(/ß/g, "ss");
        const fold = (s) => VF.norm(s).replace(INVIS, "").replace(/ß/g, "ss").replace(/ẞ/g, "SS");
        const esc = (c) => c.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        const STRONG = /^(button|link|option|tab|menuitem|menuitemradio|menuitemcheckbox|treeitem|radio|checkbox|switch)$/;
        const strong = (e) => (/^(button|summary|option)$/.test(e.localName)) || (e.localName === "a" && e.hasAttribute("href"))
          || (e.localName === "input" && /^(submit|button|reset|image)$/.test(e.type)) || STRONG.test(VF.attr(e, "role"))
          || (e.localName.includes("-") && /button|btn|link|chip|tab|option|item/.test(e.localName))
          || /(^|[\s_-])(btn|button|cta)([\s_-]|$)/i.test(typeof e.className === "string" ? e.className : "");
        const DESTRUCTIVE = /(^|[^\p{L}])(verwerfen|dismiss|entfernen|löschen|loeschen|remove|delete|ablehnen|melden|report|hide|ausblenden)([^\p{L}]|$)/iu;
        const layered = () => {
          const L = VF.layers();
          for (const t of VF.all("[aria-expanded=true][aria-controls]")) {
            const id = VF.attr(t, "aria-controls").split(/\s+/)[0];
            const p = id && t.getRootNode().getElementById?.(id);
            if (p && VF.shown(p)) L.push(p);
          }
          for (const p of VF.all("[role=listbox], [role=menu]"))
            if (VF.shown(p) && /^(absolute|fixed)$/.test(getComputedStyle(p).position)) L.push(p);
          return L;
        };
        const rank = (text, exact) => {
          const wq = squash(text), chars = [...wq].map(esc).join("\\s*");
          const word = new RegExp(`(?<![\\p{L}\\p{N}])${chars}(?![\\p{L}\\p{N}])`, "iu");
          const start = new RegExp(`^[^\\p{L}\\p{N}]*${chars}(?![\\p{L}\\p{N}])`, "iu");
          const tier = (s) => { s = fold(s); if (!s) return 0; const q = squash(s);
            return q === wq ? 4 : start.test(s) ? 3 : word.test(s) ? 2 : q.includes(wq) ? 1 : 0; };
          const seen = new Set(), out = [];
          for (const e of VF.elements()) {
            if (/^(script|style|noscript|template|html|head|body)$/.test(e.localName)) continue;
            const hay = squash(e.textContent) + "|" + squash(VF.attr(e, "aria-label") + VF.attr(e, "title") + VF.attr(e, "alt")
              + VF.attr(e, "placeholder") + VF.attr(e, "label") + (e.localName === "input" && /^(submit|button|reset)$/.test(e.type) ? e.value : ""))
              // A custom element's own "labels" property may be no NodeList (ing.de: "not iterable" broke every text= click, MONEY-0133).
              + squash((e.labels instanceof NodeList ? [...e.labels] : []).map((l) => l.textContent).join(""));
            if (!hay.includes(wq)) continue;
            const l = squash(VF.label(e)), aria = squash(VF.attr(e, "aria-label"));
            if (exact ? l !== wq && aria !== wq : !l.includes(wq) && !aria.includes(wq)) continue;
            if (!VF.shown(e)) continue;
            const t = VF.target(e);
            let el = t ?? e;
            if (t && t !== e && !strong(t) && !VF.checkable(t)) {
              const own = VF.norm(e.innerText ?? e.textContent).length;
              if (VF.norm(t.innerText ?? "").length > Math.max(3 * wq.length + 40, 2 * own + 40)) {
                el = e;  // a container: the innermost clickable box around the text
                for (let n = e, i = 0; n && n !== t && i < 15; n = VF.up(n), i++) if (VF.pointer(n) || VF.semantic(n)) { el = n; break; }
              }
            }
            if (seen.has(el)) continue;
            seen.add(el);
            const vis = el.localName === "input" ? el.value : el.innerText;
            const sv = squash(vis), q = sv === wq ? 3 : sv.includes(wq) ? 2 : 1;
            let m = tier(vis);
            if (m < 2) m = Math.max(m, tier(VF.attr(el, "aria-label") || VF.attr(el, "title") || VF.label(el)) - 0.5);
            const lab = VF.label(el).length, hit = wrapped(el, wq);
            if (hit !== el) { if (seen.has(hit)) continue; seen.add(hit); }
            out.push({ el: hit, ok: t ? 1 : 0, q, m, s: strong(el) ? 1 : /^h[1-6]$/.test(el.localName) ? -1 : 0, n: lab, top: 0, outer: 0 });
          }
          let keep = out;
          if (out.length > 1) {
            const L = layered();
            if (L.length) for (const c of out) c.top = L.some((d) => VF.inside(c.el, d)) ? 1 : 0;
            const can = out.filter((c) => VF.reachable(c.el));
            if (can.length) keep = can;
            for (const c of keep) c.outer = keep.some((d) => d !== c && d.el !== c.el && VF.inside(d.el, c.el)) ? 1 : 0;
          }
          return keep.sort(cmp);  // stable: equal ones stay in reading order
        };
        const w = (c) => c.m >= 2 ? 1 : 0;
        const cmp = (a, b) => (b.top - a.top) || (w(b) - w(a)) || (b.ok - a.ok) || (b.m - a.m) || (b.s - a.s)
          || (a.outer - b.outer) || (a.m < 4 ? a.n - b.n : 0);
        // A label (or box) around a radio/checkbox that holds another control too: its input, else the text.
        const NESTED = "button, a[href], select, textarea, [role=button], [role=link], input:not([type=hidden])";
        const wrapped = (el, wq) => {
          const ctl = VF.checkable(el);
          if (!ctl || ctl === el || ctl.localName !== "input") return el;
          const others = [...el.querySelectorAll(NESTED)].filter((x) => x !== ctl && VF.shown(x));
          if (!others.length) return el;
          const r = ctl.getBoundingClientRect();
          if (VF.shown(ctl) && r.width >= 6 && r.height >= 6 && getComputedStyle(ctl).opacity !== "0") return ctl;
          let best = null;
          for (const x of el.querySelectorAll("*"))
            if (VF.shown(x) && squash(x.textContent).includes(wq) && !others.some((o) => VF.inside(x, o) || VF.inside(o, x))) best = x;
          return best ?? el;
        };
        const ctx = (e) => {
          const own = VF.norm(e.innerText ?? e.textContent).length;
          for (let n = VF.up(e), i = 0; n && n !== document.body && i < 8; n = VF.up(n), i++) {
            const t = VF.norm(n.innerText ?? "");
            if (t.length >= own + 12) return t.slice(0, 70);
          }
          return VF.norm(e.innerText ?? e.textContent).slice(0, 70);
        };
        // A removing word the query says itself, or the whole aria-label/title asked for: on purpose
        // (SHOP-0103: "Entferne Futura 27 aus dem Warenkorb", the cart's own remove button).
        const REMOVING = /(^|[^\p{L}])(entfern|lösch|loesch|remove|delete|verwerf|dismiss|ablehn|ausblend)/iu;
        const refuse = (c, text) => {
          const q = VF.norm(text).toLowerCase(), w = squash(text);
          if (DESTRUCTIVE.test(q) || REMOVING.test(q)) return [];
          return c.filter((x) => x.q === 1 && squash(VF.attr(x.el, "aria-label")) !== w && squash(VF.attr(x.el, "title")) !== w
            && DESTRUCTIVE.test(VF.norm(`${VF.attr(x.el, "aria-label")} ${VF.attr(x.el, "title")}`).toLowerCase().split(q).join(" ")));
        };
        // text= that is only a price in a card whose one button picks it (MOVE-0361: "€31.99" beside
        // Wizz's "SELECT"): that button, never one that commits, removes or saves (B-64). Else null.
        const PRICE = /^[^\p{L}\p{N}]{0,3}(\d[\d.,'\s]*\s?(€|eur|\$|£|chf|usd|gbp)|(€|\$|£|eur|chf)\s?\d[\d.,'\s]*)\s?(\*|\/\s?\p{L}{1,12}|pro \p{L}{1,12}|p\.?\s?p\.?)?$/iu;
        const PICKS = /^(select|auswählen|auswahl|wählen|choose|pick|tarif wählen|angebot wählen|zum angebot|angebot ansehen|view deal|ansehen|view|see|anzeigen|details)( [\p{L}\p{N} ]{0,20})?$/iu;
        const priceButton = (el) => {
          if (strong(el) || VF.checkable(el) || el.closest?.("button, a[href], [role=button], [role=option], [role=radio], label")) return null;
          if (!PRICE.test(VF.norm(el.innerText ?? el.textContent ?? ""))) return null;
          for (let n = VF.up(el), i = 0; n && n !== document.body && i < 5; n = VF.up(n), i++) {
            const b = [...n.querySelectorAll("button, a[href], [role=button], input[type=submit], input[type=button]")].filter((x) => VF.shown(x) && !x.disabled);
            if (!b.length) continue;
            if (b.length > 1 || VF.norm(n.innerText ?? "").length > 400) return null;
            return PICKS.test(VF.norm(VF.label(b[0]) || VF.attr(b[0], "aria-label") || b[0].value || "")) ? b[0] : null;
          }
          return null;
        };
        const done = (p, out) => {
          const b = p && priceButton(p.el);
          VF.info = p ? { layer: VF.layers().some((d) => VF.inside(p.el, d)), tier: p.m } : null;
          if (b) { VF.info.swap = VF.norm(p.el.innerText ?? "").slice(0, 40); out.el = b; }
          return out;
        };
        // within= (B-64): the smallest card around each match that holds the words (whole, any order);
        // a match in an open layer first (an overlay's card over the list's: MOVE-0233), then the
        // smallest card (LIFE-0320: the "Manage" of the "Archived chats" row, not the pane around it),
        // then the better match. A card holding another match beside its own is a list, not a card;
        // two cards neither inside the other (same layer) raise as ambiguous unless nth picks one.
        const inCard = (spec) => {
          let c = rank(spec.text, spec.exact);
          const total = c.length;
          c = c.filter((x) => VF.shown(x.el));
          const visible = c.length;
          if (c.length) {
            const bad = refuse(c, spec.text);
            c = c.filter((x) => !bad.includes(x));
            if (!c.length) return { el: null, total, refused: bad.slice(0, 5).map((x) => VF.norm(VF.attr(x.el, "aria-label") || VF.attr(x.el, "title")).slice(0, 120)) };
          }
          if (c.some((x) => x.m >= 2)) c = c.filter((x) => x.m >= 2);
          const rxs = VF.norm(spec.within).split(" ").filter(Boolean).map((w) => new RegExp(`(?<![\\p{L}\\p{N}])${esc(fold(w))}(?![\\p{L}\\p{N}])`, "iu"));
          const out = [];
          for (const x of c)
            for (let n = x.el; n && n !== document.documentElement; n = VF.up(n)) {
              const t = fold(n.innerText ?? n.textContent ?? "");
              if (rxs.every((rx) => rx.test(t))) { out.push({ ...x, card: n, box: t.length }); break; }
            }
          // A card that holds another match at least as good beside its own is a list (a weaker one beside it,
          // "Jetzt reservieren …" next to the "Reservieren" button, is not).
          let own = out.filter((o) => !out.some((d) => d.el !== o.el && d.m >= o.m && !VF.inside(o.el, d.el) && !VF.inside(d.el, o.el)
            && VF.inside(d.el, o.card)));
          // Cards that hold within= as one phrase (case, spaces and punctuation aside) beat ones that hold only its
          // words apart (MONEY-0017: "… bis 2027" vs a 2030 card saying "Laufzeit bis 31.12.2027", B-73).
          const flat = (s) => fold(s).toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");
          const phrase = flat(spec.within);
          if (phrase && own.some((o) => flat(o.card.innerText ?? o.card.textContent).includes(phrase)))
            own = own.filter((o) => flat(o.card.innerText ?? o.card.textContent).includes(phrase));
          own.sort((a, b) => (b.top - a.top) || (a.box - b.box) || (b.m - a.m) || (b.s - a.s) || (b.ok - a.ok) || (a.n - b.n));
          const pick = own[spec.nth ?? 0];
          if (!pick) return { el: null, total, visible };
          const card = VF.norm(pick.card.innerText ?? pick.card.textContent).slice(0, 200);
          if (!spec.nth) {
            const other = own.find((d) => d.card !== pick.card && d.top === pick.top && !VF.inside(d.card, pick.card) && !VF.inside(pick.card, d.card));
            if (other) return { el: null, total, ambiguous: [card, VF.norm(other.card.innerText ?? other.card.textContent).slice(0, 200)] };
          }
          return done(pick, { el: pick.el, total, visible, card, again: null });
        };
        VF.find = (spec) => {
          if (spec.text == null || spec.css != null || spec.ref != null || spec.el) return orig(spec);
          if (spec.within) return inCard(spec);
          let c = rank(spec.text, spec.exact);
          const total = c.length;
          c = c.filter((x) => VF.shown(x.el));
          if (c.length) {
            const bad = refuse(c, spec.text);
            c = c.filter((x) => !bad.includes(x));
            if (!c.length) return { el: null, total, refused: bad.slice(0, 5).map((x) => VF.norm(VF.attr(x.el, "aria-label") || VF.attr(x.el, "title")).slice(0, 120)) };
          }
          if (c.length && c[0].m < 2) {  // only inside longer words
            VF.partial = c.slice(0, 4).map((x) => `${VF.norm(VF.label(x.el)).slice(0, 60)} (${x.el.localName})`);
            return { el: null, total, visible: 0 };
          }
          if (c.length) {
            const ties = c.filter((x) => cmp(x, c[0]) === 0);
            VF.ties = ties.length > 1 ? { n: ties.length, ctx: ties.slice(0, 4).map((x) => ctx(x.el)) } : null;
            return done(c[spec.nth ?? 0] ?? c[0], { el: c[spec.nth ?? 0]?.el ?? null, total, visible: c.length, again: null });
          }
          return { el: null, total, visible: 0, again: null };
        };
      }
    """

    async def _locate(self, spec, at, doc=None):
        """Find the element, scroll it into view if needed, and measure it
        until it stops moving (smooth scrolling, layout shifts). In a frame
        (`doc`), x and y are that frame's px."""
        # info/ties: VF.find's rank (_RANK_JS); frames: shown <iframe>s big enough to hold a form (B-40).
        body = (self._RANK_JS + "VF.info = VF.ties = null;\nconst hit = (() => {" + _LOCATE_JS + "\n})();\n"
                "if (hit && !('x' in hit) && VF.partial) hit.partial = VF.partial;\n"
                "if (hit && VF.info) { hit.info = VF.info; hit.ties = VF.ties; }\n"
                "if (hit && 'x' in hit) hit.frames = VF.all('iframe').filter((f) => { const r = f.getBoundingClientRect();"
                " return VF.shown(f) && r.width >= 200 && r.height >= 100; }).length;\nreturn hit;")
        hit = await self._ev(_vf(body, SPEC=spec, AT=list(at), SCROLL=True), doc=doc)
        keep = {k: hit[k] for k in ("card", "again", "info", "ties", "frames") if k in hit} if hit else {}
        for _ in range(10):
            if not hit or "x" not in hit:
                return hit
            await _asyncio.sleep(0.08)
            again = await self._ev(_vf(_LOCATE_JS, SPEC={"el": True}, AT=list(at), SCROLL=False), doc=doc)
            if again and "x" in again:
                again.update(keep)
            if again and "x" in again and abs(again["x"] - hit["x"]) < 1 and abs(again["y"] - hit["y"]) < 1:
                return again
            hit = again
        return hit

    async def _locate_any(self, spec, at, frame, fs):
        """(doc, hit) of _locate: the top document's, unless `frame` (or the
        ref's frame) says otherwise or it has no visible match; then the
        first frame's with one. For text= (no `within`): a match in an open
        dialog of the page first, then one in a shown frame (≥ 200×100 px,
        no ad) that matches at least as well (exact/starts/word) as the
        page's, then the page's (MONEY-0035's Terminland frame, B-40). hit
        has x, y in top-level CSS px."""
        ok = lambda h: bool(h) and ("x" in h or bool(h.get("ambiguous")))  # noqa: E731
        ref = spec.get("ref")
        hit = None
        if frame is None and not self._in_frame_ref(ref):
            hit = await self._locate(spec, at)
            if ok(hit) or ref is not None:
                info = (hit or {}).get("info") or {}
                if ("x" in (hit or {}) and hit.get("frames") and info and not info.get("layer")
                        and spec.get("text") is not None and not spec.get("within")):
                    for d in await self._frame_docs(fs, None, None):
                        if d.box[2] < 200 or d.box[3] < 100 or _AD_FRAME.search(d.host or ""):
                            continue
                        got = await self._frame_hit(spec, at, fs, d, ok)
                        if got and "x" in got and ((got.get("info") or {}).get("tier") or 0) >= (info.get("tier") or 0):
                            got["over"] = hit["what"]
                            return d, got
                return fs.top, hit
        for d in await self._frame_docs(fs, frame, ref):
            h = await self._frame_hit(spec, at, fs, d, ok)
            if h:
                return d, h
        return fs.top, hit

    async def _frame_hit(self, spec, at, fs, d, ok):
        """_locate in frame document `d`, its point in top-level px; None
        when it has no match there or the point can't reach it."""
        try:
            h = await self._locate(spec, at, d)
        except (RuntimeError, _asyncio.TimeoutError):
            return None
        if not ok(h):
            return None
        if "x" in h:
            # The <iframe>s scrolled into view; the point must hit them at every level.
            x, y, cover = await fs.point(d, h["x"], h["y"])
            if x is None:
                return None
            h["x"], h["y"] = x, y
            if cover and not h.get("covered"):
                h.update(covered=cover, coverText=None, banner=False)
        return h

    async def _effect(self, seconds, doc=None):
        deadline = _time.monotonic() + seconds
        while True:
            await _asyncio.sleep(0.1)
            if self._dialog:
                return {"changed": True, "url": None}  # a confirm/prompt: the page waits for it
            try:
                eff = await self._ev(_vf(_EFFECT_JS), timeout=3, doc=doc)
            except TabStuck:
                raise
            except (RuntimeError, _asyncio.TimeoutError):
                eff = {"changed": True, "url": None}  # the document went away: a navigation
            if eff["changed"] or _time.monotonic() > deadline:
                return eff

    async def _opened(self):
        """Ids of the windows this tab (or a frame in it) opened."""
        try:
            b = await _shop_browser_cdp()
        except (OSError, ConnectionError, ValueError):
            return set()
        try:
            infos = (await b.send("Target.getTargets", timeout=5))["targetInfos"]
        except (RuntimeError, ConnectionError, _asyncio.TimeoutError):
            return set()
        finally:
            b.close()
        fam = _target_family(infos, self.id)
        return {t["targetId"] for t in infos if t.get("type") == "page" and t.get("openerId") in fam}

    async def click(self, selector=None, *, text=None, ref=None, within=None, exact=False, nth=0,
                    at=(0.5, 0.5), force=False, frame=None, timeout=15, _spec=None, _doc=None):
        """Click the element given by a CSS `selector`, its `text` (the
        nearest clickable thing around that text; `exact` for the whole
        text), or `ref` from observe(), with real mouse events, after
        scrolling it into view. A ref the page redrew since is found again
        by its text when that is unique (see observe). `within`: words of
        the card to pick the match in (e.g. the times of a connection card,
        the product's name): every word must be in it, whole, in any order;
        a match in an open dialog/overlay first, then the one whose card
        (the smallest box holding the words and the match) is smallest; a
        "card" that holds another match beside its own is a list, not a
        card; when another card (neither inside the other) holds those
        words too it raises with both cards' texts, unless only one of them
        holds `within` as one phrase (case, spaces, punctuation aside): that
        one wins (`nth` picks the n-th match instead). `at` is the point inside it as
        fractions (for buttons in iframes, e.g. PayPal's). text= without
        `within` prefers, in this order: a match in an open dialog/popover,
        a whole-word match (never "Weiter" → "WEITERE TICKETS": text only
        inside longer words raises, naming them), clickable, exact over
        starting with it over containing it, a button/link/option/tab over
        other clickables, the button inside a dialog or card container
        rather than the container, then (not exact) the shortest; equal
        ones go by reading order, the first card's, and it says "(1 of N
        equal matches …; they sit in: …)". Case never counts, CSS-uppercased
        text neither ("GRÖSSE" = "Größe"). A price text that is no control
        ("€31.99") clicks its card's one pick button ("Select", "Auswählen")
        and says so. A match only by an aria-label/title that adds a
        removing word is refused, unless text= is that whole label or says
        the removing word itself ("Entferne … aus dem Warenkorb"). A label
        around a radio/checkbox that also holds another control (a store
        button) clicks the radio; a CSS selector that matches only a hidden
        radio/checkbox clicks its shown label and says so.

        Refuses if something covers it (after trying to reject a cookie
        banner, or, without a reject button, its settings + save or hiding
        it; its own label, its icons and sticky headers don't count;
        a cover that stays 3 s raises, a loading one is waited out);
        force=True clicks it by script anyway, overlay_hide() hides the
        cover. No match: it raises after 3 s once the page has been quiet
        for 2 s (no element added/removed, no request), else after
        `timeout`, naming the closest elements. A disabled button raises at
        once, naming the form's empty required or invalid fields; so does a
        target disabled only by a class on it or its cell (ui-state-disabled,
        …-disabled, aria-disabled on its td). When the
        mouse click changes nothing within 1 s (no DOM change, navigation,
        fetch, focus move, new window, a request of its own still running;
        for a tab: aria-selected), it first checks what changes no DOM: form
        values, cart counters, live regions, the title ("(it changed the
        value of 'Anzahl')"), then waits 0.6 s more (a late effect never gets
        a second click); only then it sends the press's pointer/mouse events
        by script (a menu that opens on pointerdown), then, when nothing
        opened, clicks by script. Before it says "nothing on the page
        changed" it looks once more at values, counters and new windows.
        A menu/popup toggle (aria-expanded="false",
        aria-haspopup) whose click opened nothing gets pointer/mouse events
        and a click by script; still closed: it says so. A visible
        checkbox, radio or switch that stays as it was raises an error (a
        link or pill wrapping a hidden one doesn't). Then it waits
        ≤ 5 s for the fetch/XHR requests to the page's own site the click
        started (an "add to cart"), ≤ 15 s for a page it navigated to, and
        ≤ 1 s for a dialog that opens late (a confirm box after the XHR).
        Returns what it clicked, with the card's text when `within` was
        given, the new URL after a navigation, a dialog it opened, and
        requests still running. A window that closes itself right after the
        click (an OAuth/consent popup after its "Weiter") is no error: "…
        then this window closed itself …: go on in the tab that opened it".
        Right after a fill() that left keys unfilled (no or several
        fields), the first click() raises once naming them (see fill).

        Frames: with no visible match in the page, it looks in the tab's
        frames (document order, also cross-site ones, nested), and says
        "in frame <host>"; for text= a match in a shown frame (≥ 200×100,
        no ad) that fits at least as well as the page's wins over a page
        match outside an open dialog (an embedded booking widget; it says
        "the frame's match, not the page's …"). `frame` (a host, a piece of
        its URL, or the <iframe>'s CSS selector) looks in that frame only.
        The mouse click goes to the frame's point on the page."""
        if _spec is not None:
            spec = dict(_spec)
        elif ref is not None or selector is not None:
            spec = self._ref_spec(selector, ref)
            if text is not None:
                spec.update(also=text, exact=exact)  # both: the selector's match that shows this text (MD-07)
        elif text is not None:
            spec = {"text": text, "exact": exact}
        else:
            raise ValueError("click needs a selector, text= or ref=")
        spec.update(nth=nth, within=within)
        desc = {k: v for k, v in spec.items() if k != "fb" and v not in (None, 0, False)}
        if _spec is None:
            await self._fill_left_check()  # B-74: a fill() that just left keys unfilled
        note = ""
        if _spec is None and _doc is None and frame is None and selector is not None and ref is None and text is None:
            # A hidden radio/checkbox (Lidl's swatches, SHOP-0217, B-73): its shown label instead.
            try:
                lab = await self._ev(_vf(self._HIDDEN_TOGGLE_JS, SEL=selector), 5)
            except (RuntimeError, _asyncio.TimeoutError):
                lab = None
            if lab:
                spec, note = {"el": True, "nth": 0, "within": None}, f" (the label of {selector}: that input itself is hidden)"
        fs = self._frames()
        t0 = _time.monotonic()
        try:
            try:
                said = await self._click(spec, desc, within, at, force, frame, timeout, fs, _doc)
            finally:
                await fs.close()
            await self._rules(t0, "" if self._dialog else None)
        except (HardRule, TabStuck):
            raise
        except Exception as e:
            # A popup that closes itself after its "Weiter" (Google/GitHub OAuth: DIGI-0118, DIGI-0128, B-83) is a
            # normal end, not a crash.
            gone_like = (isinstance(e, (ConnectionError, OSError, _asyncio.TimeoutError)) or self._cdp._task.done()
                         or _re.search(r"closed|target|session|websocket", str(e), _re.I))
            if _spec is not None or getattr(self, "_vf_pressed", 0) < t0 or not gone_like or not await self._window_closed():
                raise
            what = ", ".join(f"{k}={v!r}" for k, v in desc.items() if k not in ("nth", "exact"))
            return (f"clicked {what}; then this window closed itself (an OAuth or consent popup that finished?):"
                    " go on in the tab that opened it")
        return said + note

    async def _window_closed(self):
        """Whether this tab's window is gone (within 1 s) while the browser it
        ran in still runs: it closed itself, it didn't crash (B-83)."""
        for _ in range(5):
            try:
                pid = _shop_pid()
                if pid is None or (self._browser is not None and pid != self._browser):
                    return False
                if self.id not in {p["id"] for p in _shop_pages()}:
                    return True
            except (OSError, ValueError):
                return False
            await _asyncio.sleep(0.2)
        return False

    # A click target disabled only by a class (B-73, SHOP-0324: jQuery UI's day "21" in a td with
    # ui-state-disabled): a disabled class token (ui-state-disabled, disabled, is-disabled,
    # …-disabled, …--disabled, ui-datepicker-unselectable) on it or on a box ≤ 3 levels up that
    # holds ≤ 60 characters (its cell), or aria-disabled="true" on such a box: "class
    # 'ui-state-disabled' on its td"; else "".
    _OFF_CLASS_JS = r"""
      const el = window[Symbol.for("vf-el")];
      const RX = /(^|\s)(ui-state-disabled|ui-datepicker-unselectable|disabled|is-disabled|[\w-]+--disabled|[\w-]+-disabled)(?=\s|$)/;
      for (let n = el, i = 0; n?.isConnected && n !== document.body && i < 4; n = VF.up(n), i++) {
        if (i && VF.norm(n.innerText ?? "").length > 60) break;
        const m = RX.exec(VF.attr(n, "class"));
        const at = i ? ` on its ${n.localName}` : "";
        if (m) return `class ${JSON.stringify(m[2])}${at}`;
        if (i && VF.attr(n, "aria-disabled") === "true") return `aria-disabled${at}`;
      }
      return "";"""
    # What a click may change without a DOM mutation (B-73, LIFE-0329: a quantity field's value set
    # by script; a cart counter, a live region, the title): MARK keeps them; else the ones that
    # changed since, by name ("the value of 'Anzahl'", "'2' → '3'"), never a field's value.
    _SNAP_JS = r"""
      const K = Symbol.for("vf-snap");
      const CNT = "[class*=cart i] [class*=count i], [class*=basket i] [class*=count i], [class*=warenkorb i] [class*=count i], "
        + "[class*=cart i][class*=count i], [class*=minicart i], [class*=badge i], [aria-live]:not([aria-live=off]), [role=status]";
      const take = () => {
        const m = new Map();
        for (const e of VF.all("input, select, textarea")) {
          if (/^(password|file)$/.test(e.type)) continue;
          const v = /^(checkbox|radio)$/.test(e.type) ? String(e.checked) : String(e.value ?? "");
          m.set(e, ["field", (VF.label(e) || VF.attr(e, "aria-label") || e.name || e.id || e.localName).slice(0, 40), v]);
        }
        for (const e of VF.all(CNT)) if (!m.has(e)) m.set(e, ["text", "", VF.norm(e.innerText ?? "").slice(0, 200)]);
        return m;
      };
      if (MARK) { window[K] = { m: take(), title: document.title }; return true; }
      const st = window[K];
      if (!st) return [];
      const now = take(), out = [];
      for (const [e, [kind, name, v]] of st.m) {
        const n = now.get(e);
        if (!n || n[2] === v || (kind === "text" && !e.isConnected)) continue;
        out.push(kind === "field" ? `the value of ${JSON.stringify(name)}` : `${JSON.stringify(v.slice(0, 30))} → ${JSON.stringify(n[2].slice(0, 30))}`);
      }
      if (document.title !== st.title) out.push("the page title");
      return out.slice(0, 3);"""
    # click(css): a radio/checkbox that matches but is hidden (Lidl's colour swatches, B-73, SHOP-0217):
    # its shown label (for= or around it) kept as vf-el; its text, else null.
    _HIDDEN_TOGGLE_JS = r"""
      let els = [];
      try { els = VF.all(SEL); } catch { return null; }
      const e = els[0];
      if (!e || els.some(VF.shown) || e.localName !== "input" || !/^(radio|checkbox)$/.test(e.type)) return null;
      const l = [...(e.labels ?? [])].find(VF.shown) ?? [e.closest?.("label")].find((x) => x && VF.shown(x)) ?? null;
      if (!l) return null;
      window[Symbol.for("vf-el")] = l;
      return VF.norm(VF.label(l) || l.innerText || "").slice(0, 60) || "label";"""

    # A disabled click target (B-18, MOVE-0049): whether the disabled attribute holds (also via a
    # disabled fieldset; else only aria-disabled), and the empty required or invalid fields of its
    # form (else of the page) as "label (css)".
    _DISABLED_JS = r"""
      const el = window[Symbol.for("vf-el")];
      if (!el?.isConnected) return null;
      const form = el.form ?? el.closest?.("form") ?? null, out = [];
      for (const f of VF.all("input, select, textarea")) {
        if ((form && !VF.inside(f, form)) || f.type === "hidden" || f.disabled || !VF.shown(f)) continue;
        const req = f.required || VF.attr(f, "aria-required") === "true";
        const empty = /^(checkbox|radio)$/.test(f.type)
          ? !(f.checked || (f.name && VF.all(`input[name="${CSS.escape(f.name)}"]`).some((r) => r.checked)))
          : !String(f.value ?? "").trim();
        const bad = !(f.validity?.valid ?? true);
        if (!(req && empty) && !bad) continue;
        const name = (VF.label(f) || VF.attr(f, "placeholder") || f.name || f.id || f.localName).slice(0, 40);
        out.push(`${name} (${VF.selectorFor(f, f.getAttribute("data-vf-ref") ?? "?").replace('[data-vf-ref="?"]', f.localName)})`
          + (req && empty ? "" : " (invalid)"));
      }
      return { hard: !!el.matches?.(":disabled"), empty: [...new Set(out)].slice(0, 8) };"""
    # Whether the click target's radio/checkbox is hidden: true for a link/button/pill wrapping it
    # (a date picker's "Datum wählen", a filter pill: LIFE-0015, MOVE-0320: not a toggle whose
    # state the click must change), "hidden" for a label or other box around it, else false.
    _PILL_JS = r"""
      const el = window[Symbol.for("vf-el")], c = VF.checkable(el);
      if (!c || c === el) return false;
      const s = getComputedStyle(c), r = c.getBoundingClientRect();
      if (VF.shown(c) && s.opacity !== "0" && r.width >= 2 && r.height >= 2) return false;
      return el.localName !== "label" && (/^(a|button|summary)$/.test(el.localName) || /^(button|link|tab)$/.test(VF.attr(el, "role")))
        ? true : "hidden";"""
    # What the click changed besides a toggle's state: DOM, requests, URL (for a pill); a layer it opened (else).
    _MUTATED_JS = "(() => { const st = window[Symbol.for('vf-click')]; return !!st && (st.n > 0 || location.href !== st.url); })()"
    _OPENED_JS = "const st = window[Symbol.for('vf-click')]; return !!st && VF.layers().some((l) => !st.layers.includes(l));"
    # click(): whether the target is a menu/popup toggle that must open something (B-64): {exp: its
    # aria-expanded ("false"), pop: aria-haspopup} on it or ≤ 3 levels up; null for anything else
    # (also one already expanded). Keeps the menus/listboxes/dialogs shown now (vf-pop-before).
    _POPUP_JS = r"""
      const el = window[Symbol.for("vf-el")];
      for (let n = el, i = 0; n && i < 4; n = VF.up(n), i++) {
        const exp = n.hasAttribute?.("aria-expanded") ? VF.attr(n, "aria-expanded") : null, hp = VF.attr(n, "aria-haspopup");
        const pop = !!hp && hp !== "false";
        if (exp === "false" || (exp === null && pop)) {
          window[Symbol.for("vf-pop-before")] = new Set(VF.all("[role=menu], [role=listbox], [role=dialog], [role=tree], [role=grid], [popover]").filter(VF.shown));
          return { exp, pop };
        }
        if (exp !== null) return null;
      }
      return null;"""
    # A press by script on the click target (a menu that opens on pointerdown: Notion): pointerdown,
    # mousedown, pointerup, mouseup (CLICK: and click) at its centre, in a task of its own. NATIVE
    # false: not for a link, button, form control, label or summary (false: a script click instead;
    # their mousedown may only log analytics).
    _PRESS_JS = r"""
      const el = window[Symbol.for("vf-el")];
      if (!el?.isConnected) return false;
      if (!NATIVE && el.matches?.("a[href], button, input, select, textarea, label, summary, option")) return false;
      const r = el.getBoundingClientRect();
      const o = { bubbles: true, cancelable: true, composed: true, view: window, button: 0, buttons: 1,
                  clientX: r.x + r.width / 2, clientY: r.y + r.height / 2 };
      setTimeout(() => {
        for (const k of ["pointerdown", "mousedown", "pointerup", "mouseup", ...(CLICK ? ["click"] : [])])
          el.dispatchEvent(k.startsWith("pointer") ? new PointerEvent(k, { ...o, pointerId: 1, isPrimary: true, pointerType: "mouse" }) : new MouseEvent(k, o));
      }, 0);
      return true;"""
    # Since the click was armed: a new URL, a layer or popover that opened, a menu/listbox/dialog
    # shown since _POPUP_JS (POP), the toggle's aria-controls target shown, or its aria-expanded
    # turned "true" (EXP: what it was). Mere DOM changes (a ripple, a tooltip) don't count.
    _STRONG_JS = r"""
      const st = window[Symbol.for("vf-click")];
      if (!st) return true;
      if (location.href !== st.url || VF.layers().some((l) => !st.layers.includes(l))) return true;
      let top = st.top;
      try { top = VF.all(":popover-open, dialog[open]").length; } catch {}
      if (top !== st.top) return true;
      const before = POP ? window[Symbol.for("vf-pop-before")] : null;
      if (before && VF.all("[role=menu], [role=listbox], [role=dialog], [role=tree], [role=grid], [popover]").some((m) => VF.shown(m) && !before.has(m))) return true;
      const el = window[Symbol.for("vf-el")];
      for (let n = el, i = 0; n?.isConnected && i < 4; n = VF.up(n), i++) {
        const id = VF.attr(n, "aria-controls").split(/\s+/)[0], t = id && n.getRootNode().getElementById?.(id);
        if (t && VF.shown(t)) return true;
        if (n.hasAttribute?.("aria-expanded")) return VF.attr(n, "aria-expanded") === "true" && EXP !== "true";
      }
      return false;"""
    # A cover that goes away by itself: a spinner or loading layer (click waits it out).
    _BUSY_COVER = _re.compile(r"load|spinner|progress|busy|wait|lade|bitte warten|skeleton", _re.I)

    @staticmethod
    def _disabled_msg(what, dis):
        empty = (dis or {}).get("empty") or []
        return (f"{what} is disabled, so the click does nothing; "
                + (f"empty required or invalid fields: {', '.join(empty)}" if empty
                   else "no empty required field shows: fields() and the page's error texts say what it still wants"))

    # A submit-type click target (B-34): a submit input, a form's (default) submit button, or a
    # "Weiter/Next/Continue/Berechnen" label. 0: not one; 1: one outside a form; 2: one in a form.
    _SUBMITISH_JS = r"""
      const el = window[Symbol.for("vf-el")];
      if (!el?.isConnected) return 0;
      const b = el.closest?.("button, input, [role=button], a") ?? el;
      const form = b.form ?? b.closest?.("form") ?? null;
      const l = VF.norm(VF.label(b) || VF.attr(b, "aria-label") || (b.localName === "input" ? b.value : "")).toLowerCase();
      const yes = (b.localName === "input" && /^(submit|image)$/.test(b.type)) || (b.localName === "button" && b.type === "submit" && b.form)
        || /(^|[^\p{L}])(weiter|next|continue|berechnen)([^\p{L}]|$)/u.test(l);
      return !yes ? 0 : form ? 2 : 1;"""
    # Whether each selector's element sits in the click target's form.
    _INFORM_JS = r"""
      const el = window[Symbol.for("vf-el")], b = el?.closest?.("button, input, [role=button], a") ?? el;
      const form = b?.form ?? b?.closest?.("form") ?? null;
      return SELS.map((s) => { try { const f = VF.all(s)[0]; return !!form && !!f && VF.inside(f, form); } catch { return false; } });"""

    async def _problems_note(self, doc):
        """For a submit-type target whose click changed nothing (or that is
        disabled): "; fields(problems=True): 'Ort' combobox unset (required), …"
        (B-34), the fields of its own form when it has one; "" for any other
        target."""
        try:
            how = await self._ev(_vf(self._SUBMITISH_JS), 3, doc)
            if not how:
                return ""
            rows = await self.fields(problems=True, timeout=8)
            if how == 2 and getattr(doc, "parent", None) is None and rows:  # the page's own form: its rows only
                own = [f for f in rows if not f.get("frame")]
                keep = await self._ev(_vf(self._INFORM_JS, SELS=[f["selector"] for f in own]), 3, doc) if own else []
                rows = [f for f, k in zip(own, keep) if k]
        except (RuntimeError, _asyncio.TimeoutError):
            return ""

        def row(f):
            s = f"{str(f.get('label') or f.get('selector') or '?')[:40]!r} {f.get('kind') or 'field'}"
            if f.get("unset"):
                s += " unset"
            elif f.get("invalid"):
                s += f" invalid: {str(f['invalid'])[:60]!r}"
            else:
                s += " empty"
            return s + (" (required)" if f.get("required") else "")
        if not rows:
            return "; fields(problems=True): none (no empty required, invalid or unset field shows)"
        more = f" (+{len(rows) - 6} more)" if len(rows) > 6 else ""
        return "; fields(problems=True): " + ", ".join(row(f) for f in rows[:6]) + more

    _CLICK_XHR_CAP = 5  # s click() waits for the fetch/XHR requests it started (B-40)
    # [mutations since the click was armed, whether a layer opened since]; null when not armed.
    _LATE_JS = ("const st = window[Symbol.for('vf-click')];"
                " return st ? [st.n, VF.layers().some((l) => !st.layers.includes(l))] : null;")

    async def _snap_diff(self, doc):
        """What _SNAP_JS says changed since the click was armed; [] if
        nothing (or the document went away)."""
        try:
            return await self._ev(_vf(self._SNAP_JS, MARK=False), 3, doc) or []
        except (RuntimeError, _asyncio.TimeoutError):
            return []

    async def _late_effect(self, reqs, doc, windows, wait):
        """A click that showed no effect in its first wait (B-73): a form
        value, counter or live region it changed (_SNAP_JS), else within
        `wait` s more a DOM change, a request it started or a new window. The
        note for the click's result (" (it changed the value of 'Anzahl')"),
        or None: nothing at all."""
        diff = await self._snap_diff(doc)
        if not diff:
            eff = await self._effect(wait, doc)
            if eff["changed"] or reqs.pending:
                return " (its effect came late)"
            if await self._opened() - windows:
                return " (it opened a window: click_popup attaches to it)"
            diff = await self._snap_diff(doc)
        return f" (it changed {', '.join(diff)})" if diff else None

    async def _late_layer(self, doc):
        """After a click that changed the page in place: ≤ 1 s for a layer it
        opens late (a confirm box rendered after its XHR), ending early once
        the page has been still for 0.4 s; _DISARM_JS then names it."""
        end, last, calm = _time.monotonic() + 1.0, None, _time.monotonic()
        while _time.monotonic() < end and not self._dialog:
            try:
                r = await self._ev(_vf(self._LATE_JS), 3, doc)
            except (RuntimeError, _asyncio.TimeoutError):
                return  # the document went away
            if not r or r[1]:
                return
            if r[0] != last:
                last, calm = r[0], _time.monotonic()
            elif _time.monotonic() - calm >= 0.4:
                return
            await _asyncio.sleep(0.1)

    # Whether the top document is quiet: [ms since an element was last added or removed (counted
    # from the first call in this document on), fetch/XHR in flight (not long-polls), ms since the
    # last one ended, readyState].
    _QUIET_JS = r"""(() => {
      const K = Symbol.for("vf-quiet");
      let st = window[K];
      if (!st) {
        st = window[K] = { last: performance.now() };
        new MutationObserver((ms) => {
          for (const m of ms) for (const n of [...m.addedNodes, ...m.removedNodes])
            if (n.nodeType === 1 && !/^(script|style|link|meta)$/.test(n.localName)) { st.last = performance.now(); return; }
        }).observe(document, { subtree: true, childList: true });
      }
      // fetch/XHR in flight: part 20's counter (vf-net), or the newer one with long-polls left out (vf-net2).
      const now = performance.now(), n2 = window[Symbol.for("vf-net2")], n1 = window[Symbol.for("vf-net")];
      let live = 0, since = 1e9;
      if (n2) { live = [...n2.live.values()].filter((r) => now - r.t < 15000 && !n2.slow?.has(r.key)).length; since = now - n2.last; }
      else if (n1) { live = [...n1.live.values()].filter((t) => now - t < 15000).length; since = now - n1.last; }
      return [now - st.last, live, since, document.readyState];
    })()"""

    async def _page_quiet(self):
        """Whether the page is loaded and has been quiet for 2 s: no element
        added or removed, no fetch/XHR in flight or ended (B-42)."""
        try:
            r = await self._ev(self._QUIET_JS, 3)
        except (RuntimeError, _asyncio.TimeoutError):
            return False
        return bool(r) and r[3] == "complete" and r[0] >= 2000 and r[1] == 0 and r[2] >= 2000

    # For "no visible element": up to 3 elements closest to what was asked (SPEC: text, a ref's
    # text, or the words of a CSS selector's ids/classes/attribute values), by their label,
    # placeholder, aria-label, id, name, class: "select#city 'Ort'"; hidden ones say so. Never a
    # field's value.
    _NEAR_JS = r"""
      const sq = (s) => VF.norm(s).toLowerCase();
      const q = sq(SPEC.text ?? SPEC.also ?? SPEC.fb?.text ?? "");
      const toks = [];
      if (!q && SPEC.css)
        for (const m of SPEC.css.matchAll(/[#.]([\w-]+)|\[[\w-]+[*^$~|]?=["']?([^"'\]]+)["']?\]/g))
          toks.push(...(m[1] ?? m[2] ?? "").split(/[-_\s]+|(?<=[a-z])(?=[A-Z])/).filter((t) => t.length >= 2).map((t) => t.toLowerCase()));
      if (!q && !toks.length) return [];
      const grams = (s) => { const g = new Set(); s = s.replace(/\s+/g, ""); for (let i = 0; i < s.length - 1; i++) g.add(s.slice(i, i + 2)); return g; };
      const qg = grams(q), qw = q.split(/[^\p{L}\p{N}]+/u).filter((x) => x.length >= 2);
      const txt = (e) => VF.isField(e) ? "" : (e.textContent ?? "").slice(0, 120);
      const lab = (e) => sq(VF.label(e) || VF.attr(e, "placeholder") || VF.attr(e, "aria-label") || txt(e));
      const out = [];
      const C = "a, button, input:not([type=hidden]), select, textarea, label, summary, option, li, [role], [aria-label], [placeholder], [tabindex]";
      for (const e of VF.all(C).slice(0, 4000)) {
        const l = lab(e);
        if (l.length > 80) continue;
        let s = 0;
        if (q) {
          if (!l) continue;
          const lg = grams(l);
          let n = 0;
          for (const g of qg) if (lg.has(g)) n++;
          s = qg.size + lg.size ? 2 * n / (qg.size + lg.size) : 0;
          if (l === q) s = 2;
          else if (qw.some((x) => l.split(/[^\p{L}\p{N}]+/u).includes(x))) s = Math.max(s, 0.6);
        } else {
          const hay = `${e.id} ${VF.attr(e, "name")} ${VF.attr(e, "class")} ${VF.attr(e, "for")} ${l}`.toLowerCase();
          s = toks.filter((t) => hay.includes(t)).length / toks.length;
        }
        if (s >= 0.5) out.push([s + (VF.shown(e) ? 0.01 : 0), e]);
      }
      out.sort((a, b) => b[0] - a[0]);
      const res = [];
      for (const [, e] of out) {
        const role = VF.attr(e, "role"), name = VF.norm(VF.label(e) || VF.attr(e, "placeholder") || txt(e)).slice(0, 50);
        const d = `${e.localName}${e.id ? "#" + e.id : ""}${role ? `[role=${role}]` : ""}${name ? " " + JSON.stringify(name) : ""}${VF.shown(e) ? "" : " (hidden)"}`;
        if (!res.includes(d)) res.push(d);
        if (res.length >= 3) break;
      }
      return res;"""

    async def _nearest(self, spec, doc=None):
        """"a 'Weiter' (hidden), select#city 'Ort'": what comes closest to a
        spec that found nothing, or ""."""
        if not spec or spec.get("el"):
            return ""
        try:
            r = await self._ev(_vf(self._NEAR_JS, SPEC={k: spec.get(k) for k in ("text", "also", "css", "fb")}), 5, doc)
        except (RuntimeError, _asyncio.TimeoutError):
            return ""
        return ", ".join(r or [])

    async def _click(self, spec, desc, within, at, force, frame, timeout, fs, doc):
        pinned = doc
        t_start = _time.monotonic()
        deadline = t_start + timeout
        tried_consent, missing, rearmed, cover = False, None, False, None
        while True:
            try:
                if pinned is None:
                    doc, hit = await self._locate_any(spec, at, frame, fs)
                else:
                    hit = await self._locate(spec, at, pinned)
                    if pinned.parent is not None and hit and "x" in hit:
                        x, y, cover = await pinned.fs.point(pinned, hit["x"], hit["y"])
                        hit["x"], hit["y"] = x, y
                        if cover and not hit.get("covered"):
                            hit.update(covered=cover, coverText=None, banner=False)
            except (TabStuck, _RefGone):
                raise
            except _NoFrame as e:
                hit, missing = None, str(e)  # the frame may still come
            except (RuntimeError, _asyncio.TimeoutError):
                hit = None  # mid-navigation
            if hit and hit.get("ambiguous"):
                a, b = hit["ambiguous"]
                raise RuntimeError(f"within={within!r} fits two cards: {a!r} and {b!r}; "
                                   "pass words only one card has, or nth=" + await self._where())
            if hit and hit.get("refused"):
                raise RuntimeError(
                    f"click(text={spec.get('text')!r}) matched only by aria-label/title, and those labels add a"
                    f" removing word: {hit['refused']}: not clicked; click the element whose visible text it is"
                    " (observe() it), or the dismiss button by its own label on purpose" + await self._where())
            if hit and hit.get("partial") and "x" not in hit and _time.monotonic() > min(deadline, t_start + 3):
                raise RuntimeError(
                    f"click(text={spec.get('text')!r}): no element shows it as a whole word, only inside longer words:"
                    f" {'; '.join(hit['partial'])}: not clicked; pass the full text you mean" + await self._where())
            if hit and "x" in hit and hit["x"] is not None and (force or not hit["covered"]):
                if not force:
                    await self._cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": hit["x"], "y": hit["y"]})
                    await _asyncio.sleep(0.05)  # hover effects are no effect of the click
                try:
                    armed = await self._ev(_vf(_ARM_JS), doc=doc)
                except (RuntimeError, _asyncio.TimeoutError) as e:
                    armed = {"gone": str(e)[:120]}  # the page navigated meanwhile
                if armed and not armed.get("gone"):
                    break
                if rearmed:
                    raise RuntimeError(f"{hit['what']}{self._in(doc)} went away twice as it was being clicked"
                                       f" ({armed.get('gone') if armed else 'gone'}): the page keeps reloading"
                                       " or redrawing; wait_for() it to settle, or use plain HTTP" + await self._where())
                rearmed = True
                await _asyncio.sleep(0.5)
                continue
            if hit and hit.get("covered") and not tried_consent:
                tried_consent = True
                # A banner that came late (HUK, kitzsteinhorn) may need a moment.
                if await self.dismiss_consent(timeout=3 if hit.get("banner") else 0):
                    continue
                if hit.get("banner") and not spec.get("el"):  # no reject button: settings + save, else hidden (B-46)
                    try:
                        if await self._consent_clear():
                            continue
                    except RuntimeError:
                        pass
            fast = False
            if hit and hit.get("covered"):
                # The same static cover for 3 s (after the cookie-banner try): it stays (B-18: 19 × 15 s).
                if cover is None or cover[0] != hit["covered"]:
                    cover = (hit["covered"], _time.monotonic())
                else:
                    fast = (_time.monotonic() - cover[1] >= 3
                            and not self._BUSY_COVER.search(f"{hit['covered']} {hit.get('coverText') or ''}"))
            else:
                cover = None
                # No match, and the page has been quiet for 2 s (no DOM change, no request): it won't come (B-42).
                # Asked on every miss, so that its observer counts from the first one.
                quiet = await self._page_quiet()
                fast = quiet and _time.monotonic() - t_start >= 3 and _time.monotonic() <= deadline
            if _time.monotonic() > deadline or fast:
                where = await self._where()
                if hit and hit.get("covered"):
                    inside = f" (it shows: {hit['coverText']!r})" if hit.get("coverText") else ""
                    if hit.get("x") is not None and not (doc is not None and doc.parent is not None):
                        self._vf_cover = (hit["x"], hit["y"], _time.monotonic())  # for overlay_hide()
                    raise RuntimeError(f"{hit['what']}{self._in(doc)} is covered by {hit['covered']}{inside}: "
                                       "close that (close_dialog(); overlay_hide() hides what covers it),"
                                       " or click(..., force=True)" + where)
                if missing:
                    raise RuntimeError(missing + where)
                hidden = (hit.get("total") or 0) - (hit.get("visible") or 0) if hit else 0
                msg = f"no visible element for {desc}" + (f" ({hidden} hidden)" if hidden > 0 else "")
                if within and hit and hit.get("visible"):
                    msg += f": {hit['visible']} visible, none in a card with the words {within!r}"
                if "ref" in spec and hit and hit.get("again") is not None:
                    msg += (f": the page redrew ref {spec['ref']}, and {hit['again']} elements show its text"
                            f" {spec['fb']['text']!r}: observe() again, or click(text=…, within=…)")
                if fast:
                    msg += " (the page has been quiet for 2 s)"
                near = await self._nearest(spec, doc)
                raise RuntimeError(msg + (f"; closest: {near}" if near else "") + self._searched(fs) + where)
            await _asyncio.sleep(0.25)
        in_frame = doc is not None and doc.parent is not None
        what, x, y, card = hit["what"] + self._in(doc), hit["x"], hit["y"], hit.get("card")
        toggles = ("checkbox", "radio", "switch", "menuitemcheckbox", "menuitemradio")
        disabled = None
        if armed.get("disabled") and not force and armed["kind"] not in toggles:
            disabled = await self._ev(_vf(self._DISABLED_JS), 5, doc)
            if disabled and disabled["hard"]:
                try:
                    await self._ev(_vf(_DISARM_JS), timeout=3, doc=doc)
                except (RuntimeError, _asyncio.TimeoutError):
                    pass
                raise RuntimeError(self._disabled_msg(what, disabled) + await self._problems_note(doc) + await self._where())
        if not force and armed["kind"] not in toggles and not (disabled and disabled["hard"]):
            # Disabled by a class only (SHOP-0324: "clicked span 21" on a ui-state-disabled day, B-73): not clicked.
            off = await self._ev(_vf(self._OFF_CLASS_JS), 3, doc)
            if off:
                try:
                    await self._ev(_vf(_DISARM_JS), timeout=3, doc=doc)
                except (RuntimeError, _asyncio.TimeoutError):
                    pass
                raise RuntimeError(f"{what} is disabled ({off}): not clicked, as that choice isn't available;"
                                   " force=True clicks it anyway" + await self._problems_note(doc) + await self._where())
        shape = await self._ev(_vf(self._PILL_JS), 5, doc) if armed["kind"] in toggles else False
        pill, hidden_toggle = shape is True, shape in (True, "hidden")
        windows = await self._opened()
        dialogs = self._dialogs
        try:  # form values, cart counters, live regions: what a click may change with no DOM mutation (B-73)
            await self._ev(_vf(self._SNAP_JS, MARK=True), 3, doc)
        except (RuntimeError, _asyncio.TimeoutError):
            pass
        # A menu/popup toggle (aria-expanded="false", or aria-haspopup without aria-expanded): the click must open it.
        popup = (None if force or armed["kind"] in toggles or spec.get("el")  # internal clicks (choose, pick_date) check their own
                 else await self._ev(_vf(self._POPUP_JS), 3, doc))
        async with _ClickRequests(self, armed["url"]) as reqs:
            reqs.CAP = self._CLICK_XHR_CAP  # the requests it started finish before it returns (B-40: "basket empty")
            how = ""
            # A download the click starts is its effect (no second click by script: p9D2b, B-25).
            downloads = []
            listen = lambda m: downloads.append(1) if m.get("method") == "Page.downloadWillBegin" and not m.get("sessionId") else None  # noqa: E731
            self._cdp._listeners.append(listen)
            self._vf_pressed = _time.monotonic()  # the click went out: a window that closes from now on closed itself (B-83)
            try:
                if force:
                    await self._ev(_vf(_JSCLICK_JS), doc=doc)
                    how = " by script"
                else:
                    for kind in ("mousePressed", "mouseReleased"):
                        await self._act(self._cdp.send(
                            "Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y, "button": "left", "clickCount": 1}
                        ))
                eff = await self._effect(1.0, doc)
            finally:
                self._cdp._listeners.remove(listen)
            if downloads and not eff["changed"]:
                eff = {"changed": True, "url": eff.get("url")}
                how += " (it started a download)"
            if hidden_toggle and not eff["changed"] and await self._ev(
                    self._MUTATED_JS if pill else _vf(self._OPENED_JS), 3, doc):
                eff = {"changed": True, "url": eff.get("url")}  # it opened what it opens; its hidden toggle stays
                pill = True
            if not eff["changed"] and reqs.pending and armed["kind"] not in toggles:
                # A request it started is still running (an "add to basket" with no other sign yet): no second click.
                eff = {"changed": True, "url": eff.get("url")}
            if not eff["changed"] and not force:
                if self._dialogs > dialogs:
                    eff = {"changed": True, "url": eff.get("url")}
                    how = " (it opened a JS dialog)"
                    if self._dialog:
                        how += f": {self._dialog.get('message', '')[:200]!r}; answer it with tab.dialog(accept=…)"
                elif (armed["kind"] in ("radio", "menuitemradio") and armed["checked"]) or (armed["tab"] and armed["selected"]):
                    eff = {"changed": True, "url": eff.get("url")}
                    how = " (already selected)"
                elif await self._opened() - windows:
                    eff = {"changed": True, "url": eff.get("url")}
                    how = " (it opened a window: click_popup attaches to it)"
                elif armed["kind"] not in toggles and (late := await self._late_effect(reqs, doc, windows, 0.6)) is not None:
                    # A late or value-only effect (LIFE-0329: a quantity set by script, then a third ticket from the
                    # script click, B-73): no second click.
                    eff, how = {"changed": True, "url": eff.get("url")}, late
                else:
                    # A custom widget that opens on pointerdown only (Notion's switcher, MONEY-0356, B-64): the
                    # press's pointer/mouse events on it by script, a change after them counts; else (and for a
                    # link, button, label: Check24's labels, IKEA's links) a script click as before.
                    pressed = armed["kind"] not in toggles and await self._ev(_vf(self._PRESS_JS, CLICK=False, NATIVE=False), doc=doc)
                    eff = await self._effect(0.4, doc) if pressed else eff
                    if pressed and eff["changed"]:
                        how = " (the mouse click changed nothing, so pointer/mouse events by script)"
                    else:
                        await self._ev(_vf(_JSCLICK_JS), doc=doc)
                        eff = await self._effect(0.6, doc)
                        how = " (the mouse click changed nothing, so by script)"
                    if not eff["changed"] and armed["kind"] not in toggles:
                        # Before "nothing changed" (B-73): values/counters, a window it opened meanwhile (no more waiting).
                        if (late := await self._late_effect(reqs, doc, windows, 0)) is not None:
                            eff, how = {"changed": True, "url": eff.get("url")}, how + late
            elif (popup and not force and not reqs.pending and eff.get("url") == armed["url"] and not await self._opened() - windows
                  and not await self._ev(_vf(self._STRONG_JS, EXP=popup.get("exp"), POP=True), 3, doc)):
                # It changed something, but its menu didn't open (MONEY-0356): pointer/mouse events and a click on it by script.
                await self._ev(_vf(self._PRESS_JS, CLICK=True, NATIVE=True), doc=doc)
                await _asyncio.sleep(0.4)
                if await self._ev(_vf(self._STRONG_JS, EXP=popup.get("exp"), POP=True), 3, doc):
                    how = " (the mouse click opened nothing, so pointer/mouse events and a click by script)"
                else:
                    how = " (its menu or popup didn't open: no layer showed, aria-expanded didn't turn true)"
            if self._dialog:
                # A confirm/prompt blocks the page: nothing else answers until it is answered.
                return (f"clicked {what}{how}; it opened a JS {self._dialog.get('type')}: "
                        f"{self._dialog.get('message', '')[:200]!r}; answer it with tab.dialog(accept=…)")
            running, navigated = await reqs.settle()
            if eff["changed"] and not navigated and not self._dialog:
                await self._late_layer(doc)  # a confirm layer that renders after the XHR (SHOP-0017)
            if self._dialog:
                return (f"clicked {what}{how}; it opened a JS {self._dialog.get('type')}: "
                        f"{self._dialog.get('message', '')[:200]!r}; answer it with tab.dialog(accept=…)")
        opened = []
        final = None
        try:
            dis = await self._ev(_vf(_DISARM_JS), timeout=3, doc=doc) or {}
            opened, final = dis.get("opened") or [], dis.get("checked")
        except (RuntimeError, _asyncio.TimeoutError):
            pass  # a new document: nothing armed there
        if opened and not eff["changed"] and armed["kind"] in toggles and hidden_toggle:
            # Its toggle is hidden and the click opened a layer: a label/pill that opens a popover (MOVE-0320).
            eff = {"changed": True, "url": eff.get("url")}
            pill = True
        if not pill and armed["kind"] in ("checkbox", "switch", "menuitemcheckbox") and eff["changed"] and final is not None:
            # Toggled once? A label's click may toggle it twice, the second time a moment later (MD-03).
            again = _vf("const c = VF.checkable(window[Symbol.for('vf-el')]); return c?.isConnected ? VF.checked(c) : null;")
            await _asyncio.sleep(0.4)
            final = await self._ev(again, doc=doc)
        if (not pill and armed["kind"] in ("checkbox", "switch", "menuitemcheckbox") and eff["changed"] and final is not None
                and final == armed["checked"]):
            if final == armed["checked"] and "by script" not in how:
                await self._ev(_vf(_JSCLICK_JS), doc=doc)
                await _asyncio.sleep(0.6)
                final = await self._ev(again, doc=doc)
                how += " (it toggled back, so once more by script)"
            if final == armed["checked"]:
                state = "checked" if final else "unchecked"
                raise RuntimeError(f"clicked {what}{how}, but it is still {state}: it toggled and toggled back"
                                   + await self._where())
        if armed["kind"] in toggles and not eff["changed"]:
            state = "checked" if armed["checked"] else "unchecked"
            if armed.get("disabled"):
                raise RuntimeError(f"clicked {what}{how}, but it is disabled (still {state}): that choice isn't"
                                   " available" + await self._where())
            raise RuntimeError(f"clicked {what}{how}, but it is still {state}"
                               + (f"; it opened a dialog: {' | '.join(repr(t) for t in opened[:2])}" if opened else "")
                               + await self._where())
        if not eff["changed"] and armed.get("disabled") and armed["kind"] not in toggles:
            if disabled is None:
                try:
                    disabled = await self._ev(_vf(self._DISABLED_JS), 5, doc)
                except (RuntimeError, _asyncio.TimeoutError):
                    pass
            raise RuntimeError(self._disabled_msg(what, disabled) + await self._problems_note(doc) + await self._where())
        now = eff.get("url")
        if in_frame:
            # The frame's own URL; the page's only when the click navigated it.
            moved = f"; the frame is now {_mask_url(now)}" if now and now != armed["url"] else ""
            now = armed["url"] = None
            if navigated:
                try:
                    now = await self._ev("location.href", 5)
                except (RuntimeError, _asyncio.TimeoutError):
                    pass
                if now:
                    await self._after_load()
        else:
            moved = ""
            if navigated:
                try:
                    now = await self._ev("location.href", 5)
                except (RuntimeError, _asyncio.TimeoutError):
                    pass
            if now != armed["url"]:
                await self._after_load()
        said = f"clicked {what}{how}"
        if hit.get("again") == 1:
            said += f" (the page had redrawn ref {spec['ref']}; found again by its text)"
        if hit.get("over"):
            said += f" (the frame's match, not the page's {hit['over']!r})"
        if (hit.get("info") or {}).get("swap"):
            said += f" (the button of the card with {hit['info']['swap']!r}: that price text itself is no control)"
        ties = hit.get("ties") or {}
        if ties.get("n", 0) > 1:
            n = spec.get("nth") or 0
            said += (f" ({'1' if not n else f'nth={n}'} of {ties['n']} equal matches"
                     + (", the first in reading order" if not n else "")
                     + f"; they sit in: {' | '.join(repr(t) for t in ties.get('ctx', [])[:3])}: within=… or nth=… picks another)")
        if not eff["changed"]:
            said += ": the tab is still not selected" if armed["tab"] else ": nothing on the page changed"
            if not armed["tab"] and armed["kind"] not in toggles:
                said += await self._problems_note(doc)
        if card:
            said += f" in card: {card!r}"
        if navigated and now and now != armed["url"]:
            said += f"; the page is now {_mask_url(now)}"
        said += moved
        if opened:
            said += "; it opened a dialog: " + " | ".join(repr(t) for t in opened[:2])
        if running:
            said += f"; {running} request(s) it started still running after {reqs.CAP} s"
        return said

    async def click_popup(self, selector=None, *, timeout=30, **kw):
        """click(), then return the window it opens (e.g. PayPal's) as a
        ShopTab of this kernel. Only a window whose opener is this tab (or a
        frame in it) counts, never one another agent opened meanwhile. A
        window that closes itself before it can be used (an OAuth popup that
        finished at once: GitHub, Google) is a normal end: it prints so and
        returns this tab (the opener) to go on in."""
        me, t0 = _os.getpid(), _time.monotonic()
        b = await _shop_browser_cdp()
        st = {"armed": False, "infos": {}, "new": [], "gone": set()}
        ready = _asyncio.Event()

        def on(msg):
            m = msg.get("method")
            if m == "Target.targetDestroyed":
                st["gone"].add(msg["params"].get("targetId"))
                ready.set()
                return
            if m not in ("Target.targetCreated", "Target.targetInfoChanged"):
                return
            info = msg["params"]["targetInfo"]
            st["infos"][info["targetId"]] = info
            if st["armed"] and info.get("type") == "page" and info["targetId"] not in st["new"]:
                # A new window, or (a named one reused) an old one navigating.
                st["new"].append(info["targetId"])
            ready.set()

        def closed():
            print("[click_popup: the window it opened closed itself (an OAuth or consent popup that finished?);"
                  " returned this tab, the one that opened it: go on here]")
            return self

        b._listeners.append(on)
        try:
            # Reports every target that exists first (before the answer).
            await b.send("Target.setDiscoverTargets", {"discover": True})
            known = set(st["infos"])
            st["armed"] = True
            await self.click(selector, **kw)
            deadline = _time.monotonic() + timeout
            while True:
                infos = st["infos"]
                fam = _target_family(infos.values(), self.id)
                mine = [t for t in st["new"] if infos[t].get("openerId") in fam]
                mine.sort(key=lambda t: t in known)  # a brand-new window before a reused one
                if mine:
                    tid = mine[0]
                    break
                left = deadline - _time.monotonic()
                if left <= 0:
                    raise TimeoutError("the click opened no window of this tab" + await self._where())
                ready.clear()
                try:
                    await _asyncio.wait_for(ready.wait(), min(left, 0.5))
                except _asyncio.TimeoutError:
                    pass
        finally:
            b.close()
        if tid in st["gone"]:
            return closed()  # DIGI-0102: GitHub's OAuth popup came and went (B-83)
        async with _shop_lock():
            owners = _shop_owners()
            if owners.get(tid) not in (None, me) and _pid_alive(owners[tid]):
                raise RuntimeError("the window this tab opened belongs to another agent's kernel already")
            owners[tid] = me
            _shop_set_owners(owners)
        try:
            tab = await _shop_attach(tid)
            await tab.wait_load(timeout)
            await tab._rules(t0)
        except HardRule:
            await tab.close()
            raise
        except (ConnectionError, OSError, RuntimeError, TimeoutError, _asyncio.TimeoutError):
            # "DevTools connection closed" (DIGI-0102): gone by now, so it closed itself; else a real failure.
            try:
                gone = tid not in {p["id"] for p in _shop_pages()}
            except (OSError, ValueError):
                gone = False
            if not gone:
                raise
            return closed()
        return tab

    async def _lookup(self, expr, ok=bool, *, what, fs, frame=None, ref=None, timeout=10, doc=None, near=None):
        """Poll _find (with `doc`: that document only) until page expression
        `expr` finds something: (doc, result); after `timeout` s, or after 3 s
        once the page has been quiet for 2 s (B-42), a RuntimeError saying
        "no visible element `what`", what comes closest to spec `near`, and
        what it searched."""
        t0, missing = _time.monotonic(), None
        deadline = t0 + timeout
        while True:
            try:
                if doc is not None:
                    r = await self._ev(expr, doc=doc)
                    d = doc if ok(r) else None
                else:
                    d, r = await self._find(expr, ok, frame=frame, ref=ref, fs=fs)
                if d is not None:
                    return d, r
            except _NoFrame as e:
                missing = str(e)  # the frame may still come
            quiet = await self._page_quiet()  # every miss: its observer counts from the first one
            quiet = quiet and _time.monotonic() - t0 >= 3 and _time.monotonic() <= deadline
            if _time.monotonic() > deadline or quiet:
                if missing:
                    raise RuntimeError(missing + await self._where())
                close = await self._nearest(near, doc) if near else ""
                raise RuntimeError(f"no visible element {what}" + (" (the page has been quiet for 2 s)" if quiet else "")
                                   + (f"; closest: {close}" if close else "") + self._searched(fs) + await self._where())
            await _asyncio.sleep(0.25)

    async def _field_by_label(self, key, frame=None):
        """The ref of the one field fields() shows with label, placeholder or
        aria-label `key` (exact, any case, the start, then a piece; shown
        ones before hidden copies); None when none has it; raises naming
        them when several do."""
        norm = self._nlab
        k = norm(key)
        fs = [f for f in await self.fields(frame=frame, limit=400) if f["kind"] not in ("checkbox", "radio", "password")]
        for test in (lambda n: n == k, lambda n: n.startswith(k), lambda n: k in n):
            hits = [f for f in fs if any(n and test(n) for n in (norm(f["label"]), norm(f.get("placeholder"))))]
            hits = [f for f in hits if not f.get("offscreen")] or hits
            if len(hits) == 1:
                return hits[0]["ref"]
            if len(hits) > 1:
                raise RuntimeError(f"type: {key!r} fits several fields: "
                                   + ", ".join(f"{f['label'][:40]!r} ({f['selector']})" for f in hits[:6])
                                   + ": pass its selector or ref=")
        return None

    async def type(self, selector=None, value=None, *, ref=None, text=None, frame=None, clear=True, enter=False,
                   blur=False, timeout=10, serial=False, _echo=True, _doc=None):
        """Type plain text (never a secret: use vault_fill) into `selector`
        (or `ref` from observe()): an input, textarea or contenteditable.
        clear=False keeps what it holds. It makes sure the field has the
        focus first (focus again, else a click into it). An autosuggest field
        (role=combobox, aria-autocomplete, list=, an autosuggest/typeahead
        box) gets the text key by key, so that its list opens; any other
        field gets it inserted, then one keyup (pages that check it on keyup). When the text
        went into another field instead (a page that moves the focus), that
        field gets its value back and the text is typed again into this one.
        Then reads the field back: when it shows something else (a masked or
        Angular field, a prefilled value that stayed), selects all, deletes
        it and types the text key by key. blur=True then takes the focus away
        (pages that check a field on blur). `selector` may also be the
        field's label, placeholder or aria-label as fields() shows it (exact,
        then any case, then the start, then a piece only one field has; a
        required "*" or "(optional)" doesn't count). A
        tel field with a fixed country prefix ("+49" beside it or kept in its
        value) gets the number without that prefix, so it isn't doubled.
        Returns what it did, and what the field shows if that differs. Frames
        as in click(): the page first, then its frames, or `frame`.
        type(ref=N, text="…") / value="…" work too. B-77: enter=True is a
        submit: the 4th submit (search / type(enter=True) / press("Enter"))
        on one tab in one cell raises SerialGoto; serial=True."""
        if enter:
            self._serial_step("submit", serial, _sys._getframe(0))
        if value is None:
            value = text
        if value is None or (selector is None and ref is None):
            raise ValueError('type needs a field and the text: type("css", "text") or type(ref=N, text="text")')
        if selector is not None and ref is None and _doc is None and not _re.match(r"^\s*[#.\[*]", selector):
            # "PLZ / Ort" is a label, not CSS (MONEY-0216): the one field fields() shows with it.
            try:
                css_ok = 1 if frame is not None else await self._ev(_vf(
                    "try { return VF.all(SEL).some(VF.shown) ? 1 : 0; } catch { return -1; }", SEL=selector), 5)
            except (RuntimeError, _asyncio.TimeoutError):
                css_ok = 1
            if css_ok != 1:
                got = await self._field_by_label(selector, frame)
                if got is not None:
                    ref, selector = got, None
                elif css_ok == -1:
                    near = await self._nearest({"text": selector})
                    raise RuntimeError(f"type: no field has the label, placeholder or aria-label {selector!r}, and it isn't"
                                       " a CSS selector" + (f"; closest: {near}" if near else "") + await self._where())
        spec = self._ref_spec(selector, ref)
        lead = _re.match(r"^\s*(?:\+|00)(\d{1,3})(?=[\s\-/()]|\d)", str(value))
        js = r"""
          let { el } = VF.find(SPEC);
          if (el?.localName === "label" && el.control) el = el.control;  // a label's ref: its field
          if (!el) return false;
          window[Symbol.for("vf-typed")] = el;
          // The other fields' values, to tell when the text lands in one of them.
          window[Symbol.for("vf-others")] = VF.all("input, textarea").filter((e) => e !== el && e.type !== "password"
            && !/^(checkbox|radio|submit|button|reset|hidden|file|image|range|color)$/.test(e.type)).map((e) => [e, e.value]);
          el.scrollIntoView({ block: "center", behavior: "instant" });
          for (let i = 0; i < 2 && !VF.inside(VF.active(), el); i++) el.focus();
          // A fixed country prefix (LEAD: the typed one's digits) in the field's value or beside it (SHOP-0315).
          let prefix = null, keep = 0;
          if (LEAD && (el.type === "tel" || /tel|phone|mobil|handy/i.test(`${el.name ?? ""} ${el.id} ${VF.attr(el, "autocomplete")} ${VF.label(el)}`))) {
            const m = (el.value ?? "").match(new RegExp(`^\\s*(\\+|00)${LEAD}\\b\\s*`));
            if (m) { prefix = m[0].trim(); keep = m[0].length; }
            for (let n = VF.up(el), i = 0; !prefix && n && n !== document.body && i < 3; n = VF.up(n), i++)
              for (const x of n.querySelectorAll("span, div, label, p, b, strong, button, select"))
                if (!VF.inside(el, x) && !VF.inside(x, el) && VF.shown(x) && new RegExp(`^\\+\\s?${LEAD}$`).test(VF.norm(x.localName === "select" ? x.options[x.selectedIndex]?.text ?? "" : x.innerText).replace(/^.*?(\+\s?\d+)\)?$/, "$1"))) { prefix = "+" + LEAD; break; }
          }
          if (CLEAR && el.isContentEditable) {
            const r = document.createRange();
            r.selectNodeContents(el);
            getSelection().removeAllRanges();
            getSelection().addRange(r);
          } else if (CLEAR && keep) try { el.setSelectionRange(keep, el.value.length); } catch {}
          else if (CLEAR) try { el.select?.(); } catch {}
          let suggest = VF.attr(el, "role") === "combobox" || el.hasAttribute("aria-autocomplete") || el.hasAttribute("list");
          for (let n = VF.up(el), i = 0; n && !suggest && i < 4; n = VF.up(n), i++) {
            const c = typeof n.className === "string" ? n.className : VF.attr(n, "class");
            suggest = VF.attr(n, "role") === "combobox" || /autosuggest|autocomplete|typeahead|combobox/i.test(c);
          }
          const r = el.getBoundingClientRect();
          return { focused: VF.inside(VF.active(), el) && document.hasFocus(), suggest, filled: !!(el.value ?? el.innerText),
                   prefix, keep, x: r.x + Math.min(r.width / 2, 20), y: r.y + r.height / 2 };"""
        # What the field shows; never for a password field.
        read = r"""(() => {
          const el = window[Symbol.for("vf-typed")];
          if (!el?.isConnected || el.type === "password") return null;
          if (FOCUS) el.focus();
          return el.isContentEditable ? el.innerText : el.value ?? null;
        })()"""
        # When this field lacks the text and another one got (the start of) it
        # meanwhile: their labels; their old values go back (input/change). A
        # field the page filled from this one (a city from the PLZ) stays.
        stray = r"""
          const sq = (s) => (s ?? "").replace(/\s+/g, "").toLowerCase(), want = sq(VAL), head = want.slice(0, 3);
          const el = window[Symbol.for("vf-typed")];
          if (!head || (el?.isConnected && sq(el.isContentEditable ? el.innerText : el.value).includes(want))) return [];
          const out = [];
          for (const [e, v] of window[Symbol.for("vf-others")] ?? []) {
            if (!e.isConnected || e.value === v || !sq(e.value).includes(head) || sq(v).includes(head)) continue;
            out.push(VF.selectorFor(e, "?").replace('[data-vf-ref="?"]', e.localName) + (VF.label(e) ? ` (${VF.label(e).slice(0, 40)})` : ""));
            const proto = e instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
            Object.getOwnPropertyDescriptor(proto, "value").set.call(e, v);
            for (const t of ["input", "change"]) e.dispatchEvent(new Event(t, { bubbles: true }));
          }
          return out;"""
        what = selector if ref is None else f"ref={ref}"
        fs = self._frames()
        note = ""
        try:
            expr = _vf(js, SPEC=spec, CLEAR=clear, LEAD=lead.group(1) if lead else None)
            doc, r = await self._lookup(expr, what=what, fs=fs, frame=frame, ref=spec.get("ref"),
                                        timeout=timeout, doc=_doc, near=spec)
            if not r["focused"]:
                await self._click_into(doc, expr, r, what)
            if r.get("prefix"):
                # "+49 170 …" into a field that keeps "+49" itself: only the rest (else "+49 49 170 …").
                value = _re.sub(r"^\s*(?:\+|00)\d{1,3}[\s\-/]*(?:\(0\)\s*)?", "", value, count=1)
                note = f"; kept the field's own {r['prefix']!r} and typed the rest"

            async def put(keyed):
                if keyed:
                    if clear and r.get("filled") and not r.get("keep"):
                        await self._cdp.press("Ctrl+A")
                        await self._cdp.press("Backspace")
                    await self.keys(value, _echo=False)
                else:
                    await self._cdp.send("Input.insertText", {"text": value})
                    # Inserted text sends no key events: one keyup, for pages that check a field on keyup
                    # (LIFE-0003: the Umkreis select stayed disabled, B-74).
                    await self._ev(_vf(self._KEYUP_JS, KEY=value[-1:] or " "), 5, doc)
                await _asyncio.sleep(0.05)

            await put(r.get("suggest"))
            how = "typed" + (" key by key (an autosuggest field)" if r.get("suggest") else "")
            moved = await self._ev(_vf(stray, VAL=value), 5, doc)
            if moved:
                # The page moved the focus (decathlon's PLZ field took the city): back into this one.
                note += f"; the text went into {', '.join(moved)} first: that is undone"
                r = await self._ev(expr, 5, doc) or r
                if not r.get("focused"):
                    await self._click_into(doc, expr, r, what)
                await put(True)
                again = await self._ev(_vf(stray, VAL=value), 5, doc)
                if again:
                    raise RuntimeError(f"typing into {what}{self._in(doc)} lands in {', '.join(again)} (undone): the page"
                                       " moves the focus; click into the field, then keys()" + await self._where())
                how = "typed key by key"
            flat = lambda s: _re.sub(r"[\W_]+", "", s.lower())  # noqa: E731
            same = lambda shown: shown is None or flat(shown) == flat(value) or (  # noqa: E731
                bool(r.get("prefix")) and flat(shown).endswith(flat(value)) and len(flat(shown)) - len(flat(value)) <= 5)
            shown = await self._ev(read.replace("FOCUS", "false"), 5, doc) if clear else None
            if not same(shown):
                if r.get("keep"):
                    await self._ev(expr, 5, doc)  # focus, and select what follows the prefix
                    await self._cdp.press("Backspace")
                else:
                    await self._ev(read.replace("FOCUS", "true"), 5, doc)
                    await self._cdp.press("Ctrl+A")
                    await self._cdp.press("Backspace")
                await self.keys(value, _echo=False)
                await _asyncio.sleep(0.05)
                shown = await self._ev(read.replace("FOCUS", "false"), 5, doc)
                how = "typed it again key by key, as the field showed something else"
            if enter:
                await self._cdp.press("Enter")
            elif blur:
                await self._ev("window[Symbol.for('vf-typed')]?.blur(), true", 5, doc)
        finally:
            await fs.close()
        if not _echo:
            return how
        return how + self._in(doc) + note + ("" if same(shown) else f"; the field shows {shown!r}")

    async def _click_into(self, doc, expr, r, what):
        """A frame without the focus (keys would go elsewhere): wait for the
        focus `expr` moved there (it crosses processes), else a real click
        into the field (r: where `expr` found it), then `expr` (focus,
        select) again."""
        for _ in range(4):
            await _asyncio.sleep(0.08)
            r = await self._ev(expr, doc=doc) or r
            if r.get("focused"):
                return
        x, y, _ = await doc.fs.point(doc, r["x"], r["y"])
        if x is not None:
            await self._cdp.click_at(x, y)
            await _asyncio.sleep(0.1)
        r = await self._ev(expr, doc=doc)
        if not (r and r["focused"]):
            raise RuntimeError(f"{what}{self._in(doc)} doesn't take the focus" + await self._where())

    async def set_value(self, selector, value, *, ref=None, frame=None, timeout=10):
        """Set the value of a field, also a readonly one (date pickers that
        open a calendar), and send input and change events, as frameworks
        expect. Plain text only, never a secret. Returns what it shows.
        Frames as in click()."""
        spec = self._ref_spec(selector, ref)
        js = r"""
          const { el } = VF.find(SPEC);
          if (!el) return null;
          if (el.type === "password") return { error: "a password field: use vault_fill" };
          const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
            : el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
          const d = Object.getOwnPropertyDescriptor(proto, "value");
          if (!d || !(el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement))
            return { error: "not an input, textarea or select" };
          d.set.call(el, VAL);
          for (const t of ["input", "change"]) el.dispatchEvent(new Event(t, { bubbles: true }));
          el.dispatchEvent(new FocusEvent("blur"));
          return { value: el.value };"""
        fs = self._frames()
        try:
            doc, r = await self._lookup(_vf(js, SPEC=spec, VAL=str(value)), what=selector if ref is None else f"ref={ref}",
                                        fs=fs, frame=frame, ref=spec.get("ref"), timeout=timeout)
        finally:
            await fs.close()
        if "error" in r:
            raise RuntimeError(f"set_value {selector or ref}{self._in(doc)}: {r['error']}")
        return f"set it{self._in(doc)}; the field shows {r['value']!r}"

    async def pick_range(self, start, end, *, timeout=10):
        """In an open calendar (MUI date range pickers and the like), click
        the day `start`, then the day `end` (datetime.date or "YYYY-MM-DD"):
        a day cell found by its date (data-date, datetime, a timestamp, an
        aria-label "18 January 2027"; li cells too: Omio), else by its
        number under its month's heading; paging months with its
        next/previous buttons (also an arrow glyph "❯" or an icon-only
        chevron in the calendar's header), or scrolling a box of stacked
        months (hostelworld), ≤ 24 steps; a calendar in a dialog is found
        even when other numbers come before it in the page, and when a
        month button elsewhere on the page doesn't page it, the calendar's
        own arrow is used. A day shown but disabled raises saying so. Then,
        in a date-picker dialog or overlay, its own "Apply"/"Confirm
        selection"/"Übernehmen"/"Fertig"/"OK" button is clicked (never in a
        layer with more than the calendar). Open the picker first (click its
        field); for MUI, the start field."""
        days = [d if isinstance(d, _datetime.date) else _datetime.date.fromisoformat(str(d)) for d in (start, end)]
        for d in days:
            await self._click_day(d, timeout)
        return f"picked {days[0].isoformat()} – {days[1].isoformat()}" + await self._cal_apply()

    async def _cal_apply(self, doc=None, scope=""):
        """After the days: the open date picker's own Apply/Confirm button
        clicked (_CAL_APPLY_JS), as "; clicked its 'Confirm selection'"; ""
        when it has none (B-72)."""
        try:
            label = await self._ev(_vf(scope + self._CAL_APPLY_JS), 5, doc)
        except (RuntimeError, _asyncio.TimeoutError):
            return ""
        if not label:
            return ""
        await self.click(_spec={"el": True}, timeout=3, _doc=doc)
        await _asyncio.sleep(0.3)
        return f"; clicked its {label!r}"

    # pick_date's calendar: VF.all() looks only inside the field's open calendar (vf-cal), else skips what
    # showed before its click on the field (vf-cal-before): other calendars on the page.
    _CAL_SCOPE_JS = r"""
      const CAL = window[Symbol.for("vf-cal")], BEFORE = window[Symbol.for("vf-cal-before")], all = VF.all;
      if (CAL?.isConnected) VF.all = (css) => all(css).filter((e) => VF.inside(e, CAL));
      else if (BEFORE) VF.all = (css) => all(css).filter((e) => !BEFORE.has(e));
    """

    # What day cells look like (as _DAY_JS's cells).
    _CAL_CELLS = "button, td, [role=gridcell], [role=option], [class*=datepicker__day], [role=button], [data-date], [data-day]"

    # pick_date/pick_range's day cell by its date (B-48), before _DAY_JS's: a cell (also li: Omio's
    # li[data-e2e=calendarDay]) whose own or inner attributes (data-*, datetime, aria-label, title)
    # hold the date (ISO, YYYYMMDD, DD.MM.YYYY, a timestamp, the date written out), the smallest
    # such; else the day's number among ≥ 28 such cells under its month's heading. {found}
    # (kept as vf-el: its inner button if any), {disabled} when that day can't be picked, else {}.
    _DAY_ATTR_JS = r"""
      const pad = (n) => String(n).padStart(2, "0"), iso = `${Y}-${pad(M)}-${pad(D)}`;
      const DE = ["januar", "februar", "märz", "april", "mai", "juni", "juli", "august", "september", "oktober", "november", "dezember"][M - 1];
      const EN = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"][M - 1];
      const forms = new Set([iso, `${Y}${pad(M)}${pad(D)}`, `${pad(D)}.${pad(M)}.${Y}`, `${D}.${M}.${Y}`,
        ...(D > 12 || D === M ? [`${pad(D)}/${pad(M)}/${Y}`, `${pad(M)}/${pad(D)}/${Y}`] : [])]);
      const RX = [new RegExp(`(^|\\D)${D}\\.? ?(${DE}|${EN}),? ${Y}\\b`), new RegExp(`\\b${EN} ${D}(st|nd|rd|th)?,? ${Y}\\b`)];
      const isDay = (v) => {
        const s = VF.norm(v).toLowerCase();
        if (!s) return false;
        if (forms.has(s) || s.startsWith(iso + "t") || s.startsWith(iso + " ")) return true;
        if (/^\d{10}(\d{3})?$/.test(s)) { const d = new Date(s.length === 10 ? +s * 1000 : +s); return d.getFullYear() === Y && d.getMonth() + 1 === M && d.getDate() === D; }
        return s.length < 80 && RX.some((rx) => rx.test(s));
      };
      const attrs = (x) => [...x.attributes].filter((a) => /^(data-|datetime$|aria-label$|title$)/.test(a.name)).map((a) => a.value);
      const want = (e) => [e, ...[...e.querySelectorAll("*")].slice(0, 6)].some((x) => attrs(x).some(isDay));
      const off = (e) => e.disabled || VF.attr(e, "aria-disabled") === "true" || /disabled|unavailable|blocked|outside|other-?month/i.test(VF.attr(e, "class"));
      const CELLS = "li, td, button, a, [role=gridcell], [role=option], [role=button], [data-e2e*=day i], [data-testid*=day i], [data-qa*=day i], [class*=day i], time";
      const cells = VF.all(CELLS).filter((e) => VF.shown(e) && VF.norm(e.innerText ?? "").length <= 40);
      // One a click reaches first (2), then one off screen (1), then a covered one (0): a closed picker's
      // cells can still count as shown (pickadate's second calendar, MOVE-0032).
      const reach = (e) => {
        const r = e.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
        if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) return 1;
        const root = e.getRootNode(), h = (root.elementFromPoint ? root : document).elementFromPoint(x, y);
        return h && (e.contains(h) || h.contains(e)) ? 2 : 0;
      };
      const size = (e) => e.querySelectorAll("*").length;
      const hits = cells.filter(want).map((e) => [reach(e), size(e), e]).sort((a, b) => (b[0] - a[0]) || (a[1] - b[1])).map((x) => x[2]);
      let hit = hits.find((e) => !off(e) && !(e.closest?.("li, td, [role=gridcell]") && off(e.closest("li, td, [role=gridcell]")))) ?? null;
      if (!hit && hits.length) return { disabled: true };
      if (!hit) {
        const MONTHS = [DE, EN, EN.slice(0, 3), DE.slice(0, 3)];
        // Plain div/span day numbers count as cells here (bruendl's calendar, B-72).
        const pool = [...cells, ...VF.all("div, span").filter((e) => !e.firstElementChild && /^\d{1,2}$/.test(VF.norm(e.innerText ?? "")) && VF.shown(e))];
        for (const h of VF.all("h1, h2, h3, h4, h5, h6, [role=heading], caption, div, span, p, th, strong, label, [data-e2e*=month i]")) {
          const t = VF.norm(h.innerText ?? "").toLowerCase();
          const m = t.length < 25 && /^([a-zäöü]+\.?) (\d{4})$/.exec(t);
          if (!m || +m[2] !== Y || !MONTHS.includes(m[1].replace(/\.$/, "")) || !VF.shown(h) || [...h.children].some((c) => VF.norm(c.innerText).toLowerCase() === t)) continue;
          for (let n = VF.up(h), i = 0; n && !hit && i < 8; n = VF.up(n), i++) {
            const box = pool.filter((c) => VF.inside(c, n));
            if (box.length < 28) continue;
            const day = box.filter((c) => VF.norm(c.innerText ?? "").split(/\s/)[0] === String(D) && !c.querySelector(CELLS));
            if (day.length) { hit = day.find((c) => !off(c)) ?? null; if (!hit) return { disabled: true }; }
            break;
          }
          if (hit) break;
        }
      }
      if (!hit) return {};
      window[Symbol.for("vf-el")] = hit.querySelector("button, [role=button], a[href]") ?? hit;
      return { found: true };"""
    # The open calendar (B-72): calBox() is the smallest box around ≥ 28 shown day-number cells, found
    # from any of them (a passenger count "1" earlier in the page no longer hides a dialog's calendar:
    # MOVE-0224), cells in an open dialog/overlay first; null when there is none. calCount(): how many
    # such cells each box ≤ 10 levels above them holds.
    _CAL_BOX_JS = r"""
      // A dialog element shown by a class (Holafly's <dialog class="modal modal--active"> has no open attribute).
      const CAL_LAYER = "[role=dialog], dialog, [aria-modal=true], .cdk-overlay-pane, [popover], .modal, [class*=modal i][class*=active i],"
        + " [class*=modal i][class*=open i], [class*=modal i][class*=show i]";
      const isDay = (e) => /^\d{1,2}$/.test(VF.norm(e.innerText ?? "")) && ![...e.children].some((c) => /^\d{1,2}$/.test(VF.norm(c.innerText ?? "")));
      const calLeaves = () => VF.all("td, button, li, a, span, div, [role=gridcell], [role=button]").filter((e) => isDay(e) && VF.shown(e)).slice(0, 600);
      const calCount = (leaves = calLeaves()) => {
        const cnt = new Map();
        for (const l of leaves) for (let n = VF.up(l), i = 0; n && n !== document.body && i < 10; n = VF.up(n), i++) cnt.set(n, (cnt.get(n) ?? 0) + 1);
        return cnt;
      };
      const calBox = (leaves = calLeaves()) => {
        const cnt = calCount(leaves);
        const lay = (l) => !!l.closest?.(CAL_LAYER);
        for (const l of [...leaves.filter(lay), ...leaves.filter((l) => !lay(l))])
          for (let n = VF.up(l), i = 0; n && n !== document.body && i < 10; n = VF.up(n), i++) if (cnt.get(n) >= 28) return n;
        return null;
      };
    """
    # A month arrow _DAY_JS's names miss (B-54, MOVE-0127: hertz.de's "❯"): in the calendar (calBox) or
    # ≤ 3 levels above it (in a dialog/overlay: up to it, ≤ 8: Royal Air Maroc's arrows sit in the
    # dialog's head above two months, B-72), an enabled control whose aria-label/title says
    # next/previous (month), whose text is one arrow glyph or a Material icon name (chevron_right,
    # navigate_next, …), or that is icon-only with a next/right/chevron-right class (UP:
    # previous/left); kept as vf-el; false when there is none.
    _CAL_NAV_JS = _CAL_BOX_JS + r"""
      const leaves = calLeaves(), box = calBox(leaves);
      if (!box) return false;
      const layer = box.closest?.(CAL_LAYER) ?? null;
      let area = box;
      for (let i = 0; i < (layer ? 8 : 3) && area !== layer && VF.up(area) && VF.up(area) !== document.body; i++) area = VF.up(area);
      const W = UP ? /(^|[^\p{L}])(prev|previous|vorherige[rn]?|vorige[rn]?|zurück|back|früher|earlier)([^\p{L}]|$)/iu
                   : /(^|[^\p{L}])(next|nächste[rn]?|folgende[rn]?|vorwärts|forward|später|later)([^\p{L}]|$)/iu;
      const G = UP ? /^[‹«←<❮⟨〈◀◂◄❰⇦⮜]$/ : /^[›»→>❯⟩〉▶▸►❱⇨⮞]$/;
      const LIG = UP ? /^(chevron_left|keyboard_arrow_left|navigate_before|arrow_back(_ios(_new)?)?|arrow_left|west)$/i
                     : /^(chevron_right|keyboard_arrow_right|navigate_next|arrow_forward(_ios)?|arrow_right|east)$/i;
      // An icon class: one class token (or href/data-icon) that ends in next/right/chevron-right… (an "-icon"/"-btn"/
      // "-month" suffix allowed); never a layout utility (Tailwind's right-0, text-right, rtl:…: Holafly, B-72).
      const C = UP ? /(^|[_-])(prev|previous|left|back|west|chevron-?left|chevron-?west|arrow-?left|arrow-?west|caret-?left|angle-?left|icon-left)(-(icon|btn|button|arrow|month|sm|md|lg|small|large))?$/i
                   : /(^|[_-])(next|right|forward|east|chevron-?right|chevron-?east|arrow-?right|arrow-?east|caret-?right|angle-?right|icon-right)(-(icon|btn|button|arrow|month|sm|md|lg|small|large))?$/i;
      const UTIL = /^(text|float|pull|justify|border|rounded|align|items|self|place|content|inset|origin|bg|from|to|via|translate|space|divide|scroll-[mp][trblxy]?|[mp][trblxy]?)-/i;
      const cls = (x) => `${VF.attr(x, "class")} ${VF.attr(x, "data-icon")} ${VF.attr(x, "href")} ${VF.attr(x, "xlink:href")}`;
      const iconCls = (x) => cls(x).split(/\s+/).some((t) => t && !t.includes(":") && !UTIL.test(t) && C.test(t));
      const off = (x) => x.disabled || VF.attr(x, "aria-disabled") === "true" || /(^|[\s_-])disabled([\s_-]|$)/i.test(VF.attr(x, "class"));
      // Best first: named (aria-label/title) > an arrow glyph or icon name > an icon class on a control or
      // around an icon > a bare empty box with that class (hertz.de: an empty th.t-next before the "❯" one).
      let best = null, top = 0;
      for (const e of VF.all("button, a, [role=button], th, td, li, span, div, i, svg, mat-icon")) {
        if (!VF.inside(e, area) || VF.inside(e, box) && leaves.some((c) => VF.inside(e, c) || VF.inside(c, e)) || !VF.shown(e)) continue;
        const t = VF.norm(e.innerText ?? e.textContent ?? "");
        if (t.length > 30) continue;
        // A box around a control is no control: the control itself comes up in its turn.
        const ctl = /^(button|a)$/.test(e.localName) || VF.attr(e, "role") === "button";
        if (!ctl && e.querySelector?.("button, a, [role=button]")) continue;
        const named = W.test(`${VF.attr(e, "aria-label")} ${VF.attr(e, "title")}`);
        const icon = t.length <= 1 && (ctl ? [e, ...e.querySelectorAll("*")].slice(0, 6) : [e]).some(iconCls);
        const score = named ? 4 : G.test(t) || LIG.test(t) ? 3 : icon && (ctl || e.querySelector?.("svg, i, img")) ? 2 : icon ? 1 : 0;
        if (score <= top) continue;
        const b = e.closest?.("button, a, [role=button]") ?? e;
        if (off(b) || !VF.inside(b, area)) continue;
        best = b; top = score;
      }
      if (!best) return false;
      window[Symbol.for("vf-el")] = best;
      return true;"""
    # After the days (B-72, MOVE-0224 "Confirm selection"): the open calendar's own Apply/Confirm
    # button, in the dialog or overlay it sits in, kept as vf-el; its label, else null. Only a layer
    # that is a date picker (≤ 400 characters of text beside the calendar: never a booking form's
    # confirm), never an inline calendar's.
    _CAL_APPLY_JS = _CAL_BOX_JS + r"""
      const box = calBox();
      if (!box) return null;
      let layer = box.closest?.(CAL_LAYER) ?? null;
      for (let n = VF.up(box), i = 0; !layer && n && n !== document.body && i < 6; n = VF.up(n), i++) {
        const s = getComputedStyle(n);
        if (s.position === "fixed" || (s.position === "absolute" && (parseInt(s.zIndex) || 0) >= 1)) layer = n;
      }
      if (!layer || VF.norm(layer.innerText ?? "").length - VF.norm(box.innerText ?? "").length > 400) return null;
      const OK = /^(apply|confirm|confirm selection|confirm dates?|done|ok|okay|übernehmen|daten übernehmen|datum übernehmen|auswahl übernehmen|anwenden|fertig|bestätigen|auswahl bestätigen|select dates?|set dates?)$/i;
      const name = (x) => VF.norm(VF.label(x) || VF.attr(x, "aria-label") || x.value || "");
      const b = [...layer.querySelectorAll("button, [role=button], input[type=button], input[type=submit], a")].find((x) => VF.shown(x)
        && !x.disabled && VF.attr(x, "aria-disabled") !== "true" && !VF.inside(x, box) && OK.test(name(x)));
      if (!b) return null;
      window[Symbol.for("vf-el")] = b;
      return name(b).slice(0, 40);"""
    # close_dialog(): whether the dialog it closes holds an open calendar (its close control vf-el ≤ 8
    # levels below a box around it; ESC: the calendar sits in an open dialog/overlay): {cancel: that
    # control is a Cancel/Clear, which drops the picked dates (B-72, MOVE-0224)}; else null.
    _CAL_CLOSE_JS = _CAL_BOX_JS + r"""
      const box = calBox();
      if (!box) return null;
      if (ESC) return box.closest?.(CAL_LAYER) ? { cancel: false } : null;
      const c = window[Symbol.for("vf-el")];
      if (!c?.isConnected) return null;
      let near = false;
      for (let n = VF.up(c), i = 0; n && n !== document.body && i < 8 && !near; n = VF.up(n), i++) near = VF.inside(box, n);
      if (!near) return null;
      const l = VF.norm(VF.label(c) || VF.attr(c, "aria-label") || VF.attr(c, "title") || c.value || "").toLowerCase();
      return { cancel: /^(cancel|abbrechen|clear|clear dates?|löschen|daten löschen|auswahl löschen|zurücksetzen|reset|verwerfen|discard|leeren)$/.test(l) };"""
    # pick_date without a field that shows the date (B-72: an inline calendar, a div that keeps its
    # label): whether day vf-el (as _DAY_ATTR_JS kept it) shows as picked.
    _DAY_PICKED_JS = r"""
      const e = window[Symbol.for("vf-el")];
      if (!e?.isConnected) return null;
      const cell = e.closest?.("td, li, [role=gridcell], [role=option]") ?? e;
      return [e, cell, VF.up(e)].some((x) => x && (["aria-selected", "aria-pressed", "aria-checked"].some((a) => VF.attr(x, a) === "true")
        || /(^|[\s_-])(selected|active|chosen|checked|picked|range-start|range-end|in-range)([\s_-]|$)/i.test(VF.attr(x, "class"))));"""
    # A calendar whose months stack in a scrolling box (hostelworld, Holafly): that box (around its
    # day cells first: plain divs count, B-72) scrolled by most of its height (UP: back); false when
    # there is none or it is at its end.
    _CAL_SCROLL_JS = _CAL_BOX_JS + r"""
      const cells = [...calLeaves().slice(0, 3), ...VF.all("button, td, li, [role=gridcell], [data-date]").filter(VF.shown).slice(0, 3)];
      for (const c of cells) for (let n = VF.up(c), i = 0; n && n !== document.body && i < 12; n = VF.up(n), i++) {
        const s = getComputedStyle(n);
        if (!/auto|scroll/.test(s.overflowY) || n.scrollHeight <= n.clientHeight + 10) continue;
        if (UP ? n.scrollTop <= 0 : n.scrollTop + n.clientHeight >= n.scrollHeight - 2) return false;
        n.scrollBy(0, (UP ? -0.8 : 0.8) * n.clientHeight);
        return true;
      }
      return false;"""
    # _DAY_JS's month button (vf-el) is no form's go-on button: "Weiter"/"Next"/"Continue" that is a
    # submit or a primary/cta/confirm-styled button (Holafly's modal "Weiter" commits the start date and
    # closes the calendar, MOVE-0323, B-72).
    _NAV_OK_JS = r"""
      const el = window[Symbol.for("vf-el")];
      if (!el?.isConnected) return true;
      const b = el.closest?.("button, a, [role=button], input") ?? el.querySelector?.("button, a, [role=button], input") ?? el;
      const l = VF.norm(VF.label(el) || VF.label(b) || VF.attr(b, "aria-label") || b.value || "").toLowerCase();
      return !(/^(weiter|next|continue|fortfahren)$/.test(l)
        && (b.type === "submit" || /(^|[\s_-])(primary|submit|cta|confirm)/i.test(`${VF.attr(el, "class")} ${VF.attr(b, "class")}`)));"""
    # pick_range: the dialog/overlay the open calendar sits in, kept as vf-cal (so _CAL_SCOPE_JS looks
    # only in there); false when it sits in none.
    _CAL_LAYER_JS = _CAL_BOX_JS + r"""
      const l = calBox()?.closest?.(CAL_LAYER) ?? null;
      window[Symbol.for("vf-cal")] = l;
      window[Symbol.for("vf-cal-before")] = null;
      return !!l;"""

    async def _click_day(self, d, timeout, doc=None, scoped=False):
        """Click day `d` in the open calendar (of `doc`; scoped: in pick_date's
        calendar; else, when it sits in a dialog/overlay, in that only):
        by its date attributes, else by _DAY_JS; paging months with its
        next/previous buttons, or scrolling a box of stacked months, ≤ 24
        steps."""
        layer = False
        if not scoped:
            # A calendar in a dialog: never a "next" button of the page behind it, whose click closes the dialog
            # (Holafly: "no day 2027-02-10", B-72).
            try:
                layer = bool(await self._ev(_vf(self._CAL_LAYER_JS), 5, doc))
            except (RuntimeError, _asyncio.TimeoutError):
                layer = False
        scope = self._CAL_SCOPE_JS if scoped or layer else ""
        try:
            await self._click_day_in(d, timeout, doc, scope)
        finally:
            if layer:
                try:
                    await self._ev("window[Symbol.for('vf-cal')] = null; true", 5, doc)
                except Exception:  # noqa: BLE001 — the page may have navigated away
                    pass

    async def _click_day_in(self, d, timeout, doc, scope):
        """_click_day's work, with `scope` (_CAL_SCOPE_JS or "") before each
        page script."""
        args = {"Y": d.year, "M": d.month, "D": d.day}
        deadline, steps, last, stuck = _time.monotonic() + max(timeout, 20), 0, None, False
        while True:
            r2 = await self._ev(_vf(scope + self._DAY_ATTR_JS, **args), doc=doc)
            if r2.get("found"):
                break
            if r2.get("disabled"):
                raise RuntimeError(f"day {d.isoformat()} shows in the calendar but can't be picked (disabled: booked out"
                                   " or outside the allowed dates)" + await self._where())
            r = await self._ev(_vf(scope + _DAY_JS, **args), doc=doc)
            if r.get("found"):
                break
            shown = r.get("months") or []
            month = f"{d.year}-{d.month:02}"
            fail = (f"no day {d.isoformat()} in an open calendar"
                    + (f" (it shows {', '.join(shown)})" if shown else ""))
            if steps >= 24 or _time.monotonic() > deadline or month in shown:
                raise RuntimeError(fail + await self._where())
            # A month button _DAY_JS found elsewhere on the page that didn't page this calendar (B-72): its own arrow
            # from now on; so too when that button is a form's go-on "Weiter" (Holafly's modal confirm, MOVE-0323).
            stuck = stuck or (r.get("move") is not None and last is not None and shown == last)
            if r.get("move") is not None and not stuck and not await self._ev(_vf(self._NAV_OK_JS), 5, doc):
                stuck = True
            last = None
            if r.get("move") is None or stuck:
                up = month < min(shown) if shown else d < _datetime.date.today().replace(day=1)
                # The month arrow _DAY_JS's names miss (hertz.de's "❯", an icon-only chevron: B-54, MOVE-0127),
                # else a box of stacked months to scroll.
                if await self._ev(_vf(scope + self._CAL_NAV_JS, UP=up), doc=doc):
                    await self.click(_spec={"el": True}, timeout=3, _doc=doc)
                elif not await self._ev(_vf(scope + self._CAL_SCROLL_JS, UP=up), doc=doc):
                    raise RuntimeError(fail + await self._where())
            else:
                await self.click(_spec={"el": True}, timeout=3, _doc=doc)  # the next/previous month button
                last = shown
            steps += 1
            await _asyncio.sleep(0.3)
        await self.click(_spec={"el": True}, timeout=3, _doc=doc)
        await _asyncio.sleep(0.3)

    async def pick_date(self, field=None, date=None, *, ref=None, fmt=None, frame=None, timeout=10):
        """Set the date field `field` (a CSS selector, or the text of its
        label, aria-label, placeholder or name; or ref=N from observe()) to
        `date` ("YYYY-MM-DD" or a datetime.date), and read it back.
        A jQuery UI datepicker (class hasDatepicker) is set through its own
        API: setDate, read back with getDate, then its onSelect (the shop's
        model takes it); a date its minDate/maxDate refuse raises naming
        them, and the old date stays. pickadate (.picker__input) likewise:
        set("select"), read back, and its hidden submit field must then hold
        the date (else it raises). Else first as a value, with
        input/change/blur events: type=date and
        datetime-local fields get ISO, flatpickr gets setDate() (also one on
        a wrapper div around the input), any other
        field the date in the format it shows (its value, placeholder,
        data-date-format, a format in its label, aria-describedby or title:
        "(DD/MM/YYYY)", "TT.MM.JJJJ"; else by the page's language:
        DD.MM.YYYY for de, MM/DD/YYYY for en-US, DD/MM/YYYY for other
        languages, then ISO), or
        `fmt` (tokens YYYY YY MMMM MMM MM M DD D Do dddd ddd, [literal]: e.g.
        "DD.MM.YYYY", "MMM Do YYYY"); a format the field then calls invalid
        (aria-invalid, an "Invalid value" beside it) doesn't count. When the
        field then doesn't show the
        date (the page reset it) or isn't an input, it puts the old value
        back, opens the field's calendar with a click and clicks the day
        (as pick_range finds it: react-datepicker, vue2-datepicker, shadow
        roots, li cells; paging or scrolling months), then its own
        Apply/Confirm/Übernehmen button if the field doesn't show it yet.
        No input at all: `field` may name a button or box that opens a
        calendar ("Abholtag"), or the heading/label over an always-open
        month grid ("Ab wann"): the day is clicked there, and it counts
        when that box shows the date or the day shows as selected.
        Returns how and what the field shows; raises when it doesn't show
        the date. Frames as in click()."""
        if date is None or (field is None and ref is None):
            raise ValueError('pick_date needs a field and a date: pick_date("css" | "label", "YYYY-MM-DD") or (ref=N, date=…)')
        d = date if isinstance(date, _datetime.date) else _datetime.date.fromisoformat(str(date))
        spec = self._ref_spec(field, ref) if ref is not None else None
        js = self._CAL_BOX_JS + r"""
          let el = null, inline = null, cnt = null;
          const count = () => cnt ??= calCount();
          if (SPEC) el = VF.find(SPEC).el;
          else {
            try { const c = VF.all(FIELD); el = c.find(VF.shown) ?? c.find((e) => e._flatpickr) ?? null; } catch {}
            const want = VF.norm(FIELD).toLowerCase();
            for (const l of el ? [] : VF.all("label")) {
              if (!VF.label(l).toLowerCase().includes(want)) continue;
              el = [l.control, l.querySelector("input"), VF.up(l)?.querySelector("input:not([type=hidden])")]
                .find((c) => c && (VF.shown(c) || c._flatpickr)) ?? null;  // not a hidden ISO copy
              if (el) break;
            }
            el ??= VF.all("input, textarea, [role=combobox], [aria-haspopup], button").find((e) => VF.shown(e)
              && `${VF.label(e)} ${VF.attr(e, "placeholder")} ${VF.attr(e, "name")}`.toLowerCase().includes(want)) ?? null;
            // No field (B-72): a month grid under a heading or label with that text (ERGO's inline "Ab wann"),
            // else a button or clickable box that names it and opens a calendar (bruendl's "Abholtag" div).
            const named = el || !want ? [] : VF.all("label, legend, h1, h2, h3, h4, h5, h6, p, span, div, strong, b, button, a, [role=button], [tabindex]")
              .filter((e) => { const t = VF.norm(e.innerText ?? "").toLowerCase(); return t.length <= 80 && t.includes(want) && VF.shown(e); })
              .sort((a, b) => VF.norm(a.innerText).length - VF.norm(b.innerText).length).slice(0, 20);
            for (const c of named) {
              for (let n = c, i = 0; n && n !== document.body && i < 6 && !inline; n = VF.up(n), i++) if ((count().get(n) ?? 0) >= 28) inline = n;
              if (inline) { el = inline; break; }
              const b = [c, VF.up(c), VF.up(VF.up(c))].find((x) => x && x !== document.body
                && (x.matches?.("button, a, [role=button], [tabindex]:not([tabindex='-1'])") || VF.pointer(x)));
              if (b) { el = b; break; }
            }
          }
          if (!el) return null;
          // A selector or ref that is the month grid itself: never clicked (its centre is a day).
          if (!inline && !el.matches("input, textarea") && (count().get(el) ?? 0) >= 28) inline = el;
          // The input in a wrapper (a custom element, its shadow root, a div); flatpickr's visible altInput.
          const IN = "input:not([type=hidden]), textarea";
          const inp = inline ? null : el.matches("input, textarea") ? el : el.querySelector(IN) ?? el.shadowRoot?.querySelector(IN) ?? null;
          const prev = inp?.previousElementSibling;
          // flatpickr on the input, on the original before its altInput, or on a wrapper (wrap: true; LIFE-0236).
          const up = (n) => { for (let x = n, i = 0; x && i < 4; x = VF.up(x), i++) if (x._flatpickr) return x._flatpickr; return null; };
          const fp = inline ? null : inp?._flatpickr ?? (prev?._flatpickr?.altInput === inp ? prev._flatpickr : null) ?? up(el)
            ?? [...(el.querySelectorAll?.("*") ?? [])].find((x) => x._flatpickr)?._flatpickr ?? null;
          const f = fp?.altInput ?? fp?.input ?? inp;
          if (f?.type === "password") return { error: "a password field: use vault_fill" };
          window[Symbol.for("vf-field")] = f ?? el;
          window[Symbol.for("vf-fp")] = fp;
          window[Symbol.for("vf-el")] = f && VF.shown(f) ? f : el;  // what opens the calendar
          // A jQuery UI datepicker (class hasDatepicker; an inline one on a div too): set through its own API (B-54).
          const $ = window.jQuery ?? (window.$?.fn?.jquery ? window.$ : null);
          const jq = $?.fn?.datepicker ? [f, el, inp].find((x) => x && (x.classList?.contains("hasDatepicker") || !!$(x).data?.("datepicker"))) ?? null : null;
          window[Symbol.for("vf-jq")] = jq;
          // pickadate (.picker__input, B-72: MOVE-0032): through its picker API, which also fills its hidden field.
          const pk = $?.fn?.pickadate && !jq ? [f, inp, el].find((x) => x && !!$(x).data?.("pickadate")?.set) ?? null : null;
          window[Symbol.for("vf-pk")] = pk;
          // Its date format may sit in its label ("Gültig ab (DD/MM/YYYY)"), aria-describedby or title (MOVE-0212).
          const said = (x) => !x ? "" : `${VF.label(x)} ${VF.attr(x, "title")} ` + VF.attr(x, "aria-describedby").split(/\s+/).filter(Boolean)
            .map((i) => x.getRootNode().getElementById?.(i)?.innerText ?? "").join(" ");
          return { input: !!f, type: (f?.type ?? "").toLowerCase(), fp: !!fp, jq: !!jq, pk: !!pk, inline: !!inline,
                   value: f ? f.value : VF.norm(el.innerText).slice(0, 80),
                   hints: [VF.attr(f, "placeholder"), VF.attr(f, "data-date-format"), VF.attr(el, "data-date-format"), VF.attr(f, "data-format"),
                           VF.norm(said(f ?? el)).slice(0, 200)],
                   lang: (document.documentElement.lang || navigator.language || "").toLowerCase(),
                   what: inline ? `the calendar under ${JSON.stringify(VF.norm(FIELD || "").slice(0, 40))}`
                     : ((f ?? el).localName + " " + VF.label(f ?? el).slice(0, 60)).trim() };"""
        # jQuery UI (B-54, MOVE-0147: the input showed the new date, the widget kept 27/11, the cart got 27/11):
        # setDate, read back with getDate; same day: its onSelect via _selectDate (else a change event);
        # another day (minDate/maxDate clamp it): the old date goes back, and it says which bounds.
        jq_put = r"""
          const t = window[Symbol.for("vf-jq")], $ = window.jQuery ?? window.$;
          if (!t?.isConnected || !$?.fn?.datepicker) return null;
          const iso = (d) => d instanceof Date && !isNaN(d) ? `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}` : null;
          const opt = (k) => { try { const v = $(t).datepicker("option", k); return v instanceof Date ? iso(v) : v == null ? null : String(v); } catch { return null; } };
          const was = $(t).datepicker("getDate");
          $(t).datepicker("setDate", new Date(Y, M - 1, D));
          const got = iso($(t).datepicker("getDate"));
          if (got !== ISO) {
            $(t).datepicker("setDate", was);
            return { got, min: opt("minDate"), max: opt("maxDate"), format: opt("dateFormat") };
          }
          let fired = false;
          try { if ($.datepicker?._selectDate && $.datepicker._getInst?.(t)) { $.datepicker._selectDate(t); fired = true; } } catch {}
          if (!fired && t.matches?.("input")) {
            for (const k of ["input", "change"]) t.dispatchEvent(new Event(k, { bubbles: true }));
          }
          return { got: iso($(t).datepicker("getDate")), fired };"""
        # pickadate (B-72, MOVE-0032: the visible input showed the date, its hidden startDatedate stayed empty and
        # the search did nothing): set("select"), read back with get("select"); its hidden field (P._hidden,
        # else <name>_submit) must hold something then; another day (min/max/disabled): the old one goes back.
        pk_put = r"""
          const t = window[Symbol.for("vf-pk")], $ = window.jQuery ?? window.$;
          const p = t?.isConnected && $ ? $(t).data("pickadate") : null;
          if (!p?.set) return null;
          const iso = (d) => d instanceof Date && !isNaN(d) ? `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}` : null;
          const item = (s) => iso(s?.obj);
          const was = p.get("select");
          p.set("select", [Y, M - 1, D]);
          const got = item(p.get("select"));
          if (got !== ISO) {
            if (was?.obj) p.set("select", was.obj); else if (got) p.clear?.();
            return { got, min: item(p.get("min")), max: item(p.get("max")) };
          }
          const h = p._hidden ?? (t.name ? t.form?.querySelector?.(`input[type=hidden][name="${CSS.escape(t.name + "_submit")}"]`) : null) ?? null;
          return { got, hidden: h ? [h.name || "(no name)", h.value] : null };"""
        # After a value: whether the field says it is invalid (aria-invalid, a bad input or pattern, an
        # error text beside it: Angular's "Invalid value", MOVE-0212); a short reason, else "".
        bad_js = r"""
          const f = window[Symbol.for("vf-field")];
          if (!f?.isConnected) return "";
          if (VF.attr(f, "aria-invalid") === "true") return "aria-invalid";
          const v = f.validity;
          if (v && (v.badInput || v.patternMismatch || v.typeMismatch)) return f.validationMessage || "invalid input";
          for (let n = VF.up(f), i = 0; n && i < 4 && n !== document.body; n = VF.up(n), i++) {
            if (VF.norm(n.innerText ?? "").length > 400) break;
            for (const e of n.querySelectorAll("[role=alert], mat-error, [class*=error i], [class*=invalid i], [aria-live]")) {
              const t = VF.norm(e.innerText ?? "");
              if (t && t.length < 120 && VF.shown(e) && /invalid|ungültig|ungueltig|falsch|format|not a valid|kein gültiges/i.test(t)) return t;
            }
          }
          return "";"""
        # The value as the page would set it, then input/change/blur (React, Vue, jQuery), or flatpickr's setDate.
        put = r"""
          const f = window[Symbol.for("vf-field")], fp = window[Symbol.for("vf-fp")];
          if (!f?.isConnected) return null;
          if (fp && FP) fp.setDate(new Date(Y, M - 1, D), true);
          else {
            const proto = f instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
            Object.getOwnPropertyDescriptor(proto, "value").set.call(f, VAL);
            for (const t of ["input", "change"]) f.dispatchEvent(new Event(t, { bubbles: true }));
            f.dispatchEvent(new FocusEvent("blur"));
            f.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
          }
          return true;"""
        read = r"""
          const f = window[Symbol.for("vf-field")];
          return !f?.isConnected ? null : f.matches("input, textarea") ? f.value : VF.norm(f.innerText).slice(0, 200);"""
        fs = self._frames()
        tried = []
        try:
            doc, f = await self._lookup(_vf(js, SPEC=spec, FIELD=field), what=field if ref is None else f"ref={ref}",
                                        fs=fs, frame=frame, ref=ref, timeout=timeout)
            if "error" in f:
                raise RuntimeError(f"pick_date {field or ref}{self._in(doc)}: {f['error']}")
            what = f["what"] + self._in(doc)
            if f.get("jq"):
                r = await self._ev(_vf(jq_put, Y=d.year, M=d.month, D=d.day, ISO=d.isoformat()), 5, doc)
                if r and r.get("got") == d.isoformat():
                    await _asyncio.sleep(0.3)
                    shown = await self._ev(_vf(read), 5, doc)
                    return (f"set {what} to {d.isoformat()} (jQuery UI datepicker: setDate, read back by getDate, then "
                            + ("its onSelect" if r.get("fired") else "a change event") + f"); the field shows {shown!r}")
                if r:
                    bounds = ", ".join(f"{k} {r[k]!r}" for k in ("min", "max") if r.get(k))
                    raise RuntimeError(f"pick_date {what}: its jQuery UI datepicker doesn't take {d.isoformat()}: it gave"
                                       f" {r.get('got')!r}" + (f" (its bounds: {bounds})" if bounds else "")
                                       + "; it keeps its old date" + await self._where())
            if f.get("pk"):
                r = await self._ev(_vf(pk_put, Y=d.year, M=d.month, D=d.day, ISO=d.isoformat()), 5, doc)
                if r and r.get("got") == d.isoformat():
                    hidden = r.get("hidden")
                    if hidden and not hidden[1]:
                        raise RuntimeError(f"pick_date {what}: its pickadate took {d.isoformat()}, but its hidden field"
                                           f" {hidden[0]!r} stays empty, so the form would send no date" + await self._where())
                    await _asyncio.sleep(0.3)
                    shown = await self._ev(_vf(read), 5, doc)
                    return (f"set {what} to {d.isoformat()} (pickadate: set('select'), read back"
                            + (f"; its hidden field {hidden[0]!r} = {hidden[1]!r}" if hidden else "") + f"); the field shows {shown!r}")
                if r:
                    bounds = ", ".join(f"{k} {r[k]!r}" for k in ("min", "max") if r.get(k))
                    raise RuntimeError(f"pick_date {what}: its pickadate doesn't take {d.isoformat()}: it gave {r.get('got')!r}"
                                       + (f" (its bounds: {bounds})" if bounds else "") + "; it keeps its old date" + await self._where())
            if f["input"]:
                for form, lang in self._date_forms(f, fmt):
                    text = self._date_text(d, form, lang)
                    await self._ev(_vf(put, VAL=text, FP=f["fp"], Y=d.year, M=d.month, D=d.day), 5, doc)
                    await _asyncio.sleep(0.4)  # a framework that rejects it puts its own value back
                    shown = await self._ev(_vf(read), 5, doc)
                    if shown is not None and self._date_shown(shown, d):
                        bad = await self._ev(_vf(bad_js), 5, doc)
                        if not bad:
                            return f"set {what} to {d.isoformat()}" + (" (flatpickr)" if f["fp"] else "") + f"; the field shows {shown!r}"
                        tried.append(f"set {'it by flatpickr' if f['fp'] else repr(text)}: the field says invalid ({bad[:60]!r})")
                    else:
                        tried.append(f"set {'it by flatpickr' if f['fp'] else repr(text)}: it showed {shown!r}")
                    if f["fp"]:
                        break
                await self._ev(_vf(put, VAL=f["value"], FP=False, Y=0, M=0, D=0), 5, doc)
            # The calendar: what shows after a click on the field and didn't before (a page can hold other
            # calendars), else the field's own always-open one; else it says so instead of clicking a day elsewhere.
            # New day-number cells count too (plain divs: bruendl's calendar, B-72).
            cal = self._CAL_BOX_JS + r"""
              const k = Symbol.for("vf-cal-before");
              const vis = (e) => e.checkVisibility ? e.checkVisibility({ opacityProperty: true, visibilityProperty: true })
                : e.getClientRects().length > 0;
              if (MARK) { window[k] = new WeakSet(VF.elements().filter(vis)); return 0; }
              const before = window[k];
              return Math.max(VF.all(CELLS).filter((e) => !before?.has(e) && VF.shown(e)).length,
                              calLeaves().filter((e) => !before?.has(e)).length);"""
            # A calendar open before (the field's own, opened earlier) isn't new: an outside press closes it, then once more.
            outside = r"""
              VF.active()?.blur?.();
              for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup"])
                document.body.dispatchEvent(new (t.startsWith("pointer") ? PointerEvent : MouseEvent)(t, { bubbles: true, clientX: 1, clientY: 1 }));
              return true;"""
            inline, applied, picked = f.get("inline"), "", None
            try:
                if inline:
                    # A month grid with no field (B-72: ERGO's "Ab wann"): its days, no click to open it.
                    await self._ev("window[Symbol.for('vf-cal-before')] = null;"
                                   " window[Symbol.for('vf-cal')] = window[Symbol.for('vf-field')]; true", 5, doc)
                for again in (False, True) if not inline else ():
                    if again:
                        await self._ev(_vf(outside), 5, doc)
                        await _asyncio.sleep(0.3)
                    await self._ev(_vf(cal, MARK=True, CELLS=self._CAL_CELLS), 5, doc)
                    await self.click(_spec={"el": True}, timeout=3, _doc=doc)
                    for _ in range(6):  # ≤ 1.5 s for it to render
                        await _asyncio.sleep(0.25)
                        if (cells := await self._ev(_vf(cal, MARK=False, CELLS=self._CAL_CELLS), 5, doc)) >= 7:
                            break
                    if cells >= 7:
                        break
                else:
                    # Open all along (an always-open calendar): the field's own, in a box ≤ 6 levels around it.
                    own = r"""
                      const f = window[Symbol.for("vf-field")], cells = VF.all(CELLS).filter(VF.shown);
                      window[Symbol.for("vf-cal-before")] = null;
                      for (let n = f && VF.up(f), i = 0; n && i < 6 && n !== document.body; n = VF.up(n), i++)
                        if (cells.filter((c) => VF.inside(c, n)).length >= 28) { window[Symbol.for("vf-cal")] = n; return true; }
                      return false;"""
                    if not inline and not await self._ev(_vf(own, CELLS=self._CAL_CELLS), 5, doc):
                        raise RuntimeError(f"pick_date {what}: " + "; ".join(tried + ["clicking it opened no calendar"])
                                           + await self._where())
                try:
                    await self._click_day(d, timeout, doc, scoped=True)
                except RuntimeError as e:
                    raise RuntimeError(f"pick_date {what}: " + "; ".join([*tried, str(e)])) from None
                shown = None if inline else await self._ev(_vf(read), 5, doc)
                if not inline and (shown is None or not self._date_shown(shown, d)):
                    # A picker that takes the day only with its own Apply/Confirm/Übernehmen (B-72).
                    applied = await self._cal_apply(doc, self._CAL_SCOPE_JS)
                    if applied:
                        shown = await self._ev(_vf(read), 5, doc)
                if not f["input"] and (shown is None or not self._date_shown(shown, d)):
                    # No field shows it (an inline calendar; a div that keeps its label): the day's own mark.
                    if (await self._ev(_vf(self._CAL_SCOPE_JS + self._DAY_ATTR_JS, Y=d.year, M=d.month, D=d.day), 5, doc) or {}).get("found"):
                        picked = await self._ev(_vf(self._DAY_PICKED_JS), 5, doc)
            finally:
                try:
                    await self._ev("window[Symbol.for('vf-cal-before')] = window[Symbol.for('vf-cal')] = null", 5, doc)
                except Exception:  # noqa: BLE001 — the page may have navigated away
                    pass
            if not f["input"] and (shown is None or not self._date_shown(shown, d)):
                if picked:
                    return (f"picked {d.isoformat()} in {what}{applied}; the day shows as selected"
                            + (f" ({what} shows {shown!r})" if shown else ""))
                raise RuntimeError(f"pick_date {what}: clicked {d.isoformat()} in its calendar{applied}, but neither does"
                                   f" {'it' if inline else what} show it ({shown!r}) nor does the day show as selected"
                                   + await self._where())
            if shown is None or not self._date_shown(shown, d):
                raise RuntimeError(f"pick_date {what}: clicked {d.isoformat()} in its calendar{applied}, but the field shows {shown!r}"
                                   + "".join(f"; {t}" for t in tried) + await self._where())
            return f"picked {d.isoformat()} in the calendar of {what}{applied}; the field shows {shown!r}" + (
                f" (setting the value didn't hold: {tried[0]})" if tried else "")
        finally:
            await fs.close()

    _MONTHS = {"en": ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
                      "november", "december"],
               "de": ["januar", "februar", "märz", "april", "mai", "juni", "juli", "august", "september", "oktober",
                      "november", "dezember"]}
    _MONTHS_SHORT = {"en": ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
                     "de": ["jan", "feb", "mär", "apr", "mai", "jun", "jul", "aug", "sep", "okt", "nov", "dez"]}
    _WEEKDAYS = {"en": ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"],
                 "de": ["montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag", "sonntag"]}
    _WEEKDAYS_SHORT = {"en": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"], "de": ["mo", "di", "mi", "do", "fr", "sa", "so"]}

    @classmethod
    def _date_text(cls, d, form, lang="en"):
        """`d` written in `form` (pick_date's fmt tokens) with `lang`'s names."""
        lang = (lang or "en")[:2] if (lang or "en")[:2] in cls._MONTHS else "en"
        nth = "th" if 11 <= d.day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(d.day % 10, "th")
        tok = {"YYYY": f"{d.year:04}", "YY": f"{d.year % 100:02}", "MMMM": cls._MONTHS[lang][d.month - 1].capitalize(),
               "MMM": cls._MONTHS_SHORT[lang][d.month - 1].capitalize(), "MM": f"{d.month:02}", "M": str(d.month),
               "DD": f"{d.day:02}", "Do": f"{d.day}." if lang == "de" else f"{d.day}{nth}", "D": str(d.day),
               "dddd": cls._WEEKDAYS[lang][d.weekday()].capitalize(), "ddd": cls._WEEKDAYS_SHORT[lang][d.weekday()].capitalize()}
        return "".join(tok.get(t, t[1:-1] if t.startswith("[") else t)
                       for t in _re.findall(r"\[[^\]]*\]|YYYY|YY|MMMM|MMM|MM|M|DD|Do|D|dddd|ddd|.", form))

    @classmethod
    def _date_form(cls, s, lang):
        """The format (form, names' language) of the date or placeholder `s`
        ("19.12.2026", "TT.MM.JJJJ", "mm/dd/yyyy", "Dec 10th 2026"), or None."""
        s = (s or "").strip()
        low = s.lower().replace("tt", "dd").replace("jjjj", "yyyy").replace("jj", "yy")
        m = _re.search(r"\b(d{1,2}|m{1,2}|y{4}|y{2})([./ -])(d{1,2}|m{1,2}|y{4}|y{2})\2(d{1,2}|m{1,2}|y{4}|y{2})\b", low)
        if m and {g[0] for g in m.group(1, 3, 4)} == {"d", "m", "y"}:
            return m.group(2).join(g.upper() for g in m.group(1, 3, 4)), lang
        m = _re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", s)
        if m:
            return "YYYY-MM-DD", lang
        m = _re.search(r"\b(\d{1,2})([./-])(\d{1,2})\2(\d{4}|\d{2})\b", s)
        if m:
            a, sep, b, y = m.groups()
            us = sep == "/" and (int(b) > 12 or (int(a) <= 12 and lang in ("en", "en-us")))
            day, mon = ("M" if len(a) == 1 else "MM", "D" if len(b) == 1 else "DD") if us else \
                       ("D" if len(a) == 1 else "DD", "M" if len(b) == 1 else "MM")
            return sep.join([day, mon, "YYYY" if len(y) == 4 else "YY"]), lang
        # Written out: "Dec 10th 2026", "Fr, 19. Dezember 2026".
        out, seen, names = [], set(), None
        for t in _re.findall(r"\d+(?:st|nd|rd|th)|\d+|[^\W\d_]+|[\W_]+", s):
            w, kind = t.lower(), None
            for lg in ("de", "en") if lang.startswith("de") else ("en", "de"):
                for k, table in (("MMMM", cls._MONTHS), ("MMM", cls._MONTHS_SHORT), ("dddd", cls._WEEKDAYS), ("ddd", cls._WEEKDAYS_SHORT)):
                    if w.rstrip(".") in table[lg]:
                        kind, names = k, names or lg
                        break
                if kind:
                    break
            if kind is None:
                kind = f"[{t}]" if not t[0].isdigit() else "Do" if not t.isdigit() else \
                    "YYYY" if len(t) == 4 else "DD" if len(t) == 2 else "D"
            if kind[0] in "YMDd":
                if kind[0] in seen and kind[0] != "d":
                    return None
                seen.add(kind[0])
            out.append(kind)
        return ("".join(out), names) if {"Y", "M", "D"} <= seen and names else None

    @classmethod
    def _date_forms(cls, f, fmt):
        """pick_date's formats to try for field info `f`: [(form, lang)]."""
        lang = f["lang"] or "en"
        if fmt:
            return [(fmt, lang[:2])]
        if f["fp"] or f["type"] == "date":
            return [("YYYY-MM-DD", "en")]
        if f["type"] == "datetime-local":
            return [("YYYY-MM-DD[T" + (f["value"][11:16] or "00:00") + "]", "en")]
        forms = [(x[0], x[1][:2]) for s in [f["value"], *f["hints"]] if (x := cls._date_form(s, lang))]
        forms += [("DD.MM.YYYY" if lang.startswith("de") else "MM/DD/YYYY" if lang in ("en", "en-us") else "DD/MM/YYYY", lang[:2]),
                  ("YYYY-MM-DD", "en")]
        return list(dict.fromkeys(forms))[:3]

    @classmethod
    def _date_shown(cls, text, d):
        """Whether `text` (a field's value) shows the date `d`, in any common way."""
        t = _re.sub(r"\s+", " ", (text or "").replace("\u00a0", " ")).lower()
        D, M, Y, y = d.day, d.month, d.year, f"{d.year % 100:02}"
        year = f"(?:{Y}|'?{y})"
        names = "|".join(_re.escape(n) for lg in ("en", "de") for n in (cls._MONTHS[lg][M - 1], cls._MONTHS_SHORT[lg][M - 1]))
        if not _re.search(r"(?<!\d)(19|20)\d\d(?!\d)", t):
            year = f"(?:{year})?"  # "Fr, 19. Dez": no year shown at all
        return any(_re.search(p, t) for p in (
            rf"(?<!\d){Y}[-/.]0?{M}[-/.]0?{D}(?!\d)",
            rf"(?<!\d)0?{D}\. ?0?{M}\. ?{year}(?!\d)",
            rf"(?<!\d)0?{D}[/-]0?{M}[/-]{year}(?!\d)",
            rf"(?<!\d)0?{M}/0?{D}/{year}(?!\d)",
            rf"(?<!\d)0?{D}(?:st|nd|rd|th)?\.? ?(?:of )?(?:{names})\.?,? ?{year}(?!\d)",
            rf"(?:{names})\.? 0?{D}(?:st|nd|rd|th)?,? ?{year}(?!\d)"))

    async def keys(self, text, delay=0.03, *, _echo=True):
        """Type `text` as key presses, one per character, into the element
        that has the focus: for date pickers and contenteditable spinbuttons
        that ignore inserted text. "\\n" presses Enter, "\\t" Tab. Returns
        what the element that had the focus shows then (never a password
        field's value): fields that move the focus on by themselves can lose
        a key typed right after focusing them."""
        mark = r"""(() => {
          let a = document.activeElement;
          while (a?.shadowRoot?.activeElement) a = a.shadowRoot.activeElement;
          window[Symbol.for("vf-keys")] = a;
          return true;
        })()"""
        if _echo:
            await self._ev(mark, 5)
        for ch in text:
            if ch in "\n\t":
                await self._cdp.press("Enter" if ch == "\n" else "Tab")
                continue
            if ch.isascii() and ch.isalnum():
                code, vk = ("Digit" if ch.isdigit() else "Key") + ch.upper(), ord(ch.upper())
            elif ch == " ":
                code, vk = "Space", 32
            else:
                code, vk = "", 0
            down = {"type": "keyDown", "key": ch, "code": code, "text": ch, "unmodifiedText": ch,
                    "windowsVirtualKeyCode": vk, "modifiers": 8 if ch.isupper() else 0}
            await self._cdp.send("Input.dispatchKeyEvent", down)
            await self._cdp.send("Input.dispatchKeyEvent", {**down, "type": "keyUp", "text": "", "unmodifiedText": ""})
            await _asyncio.sleep(delay)
        if not _echo:
            return None
        await _asyncio.sleep(0.05)
        shown = await self._ev(r"""(() => {
          const el = window[Symbol.for("vf-keys")];
          if (!el?.isConnected || el === document.body || el.type === "password" || /^i?frame$/.test(el.localName)) return null;
          return el.value ?? el.getAttribute("aria-valuetext") ?? el.innerText ?? null;
        })()""", 5)
        return f"typed {len(text)} keys" + (f"; the field shows {shown!r}" if shown is not None else "")

    async def press(self, key, *, serial=False):
        """Press a key: Enter, Tab, Escape, Backspace, Delete, Space, Home,
        End, PageUp/Down, ArrowUp/Down/Left/Right, a letter, digit or
        punctuation key, with Alt/Control(Ctrl)/Meta/Shift modifiers as
        "Ctrl+A" (select all), "Shift+Tab", "Control+,"; a lone "+" is the
        key. B-77: "Enter"/"Return" is a submit: the 4th submit (search /
        type(enter=True) / press("Enter")) on one tab in one cell raises
        SerialGoto; serial=True."""
        if key.rsplit("+", 1)[-1] in ("Enter", "Return"):
            self._serial_step("submit", serial, _sys._getframe(0))
        await self._act(self._cdp.press(key))

    # type_code's page side. FIND: the shown matches of SEL (kept in vf-code-boxes, with what each
    # shows now; one match that belongs to ≥ 4 shown inputs whose id/name/aria-label differ only by
    # a consecutive index, #pin-code-input-0…5: that whole group) and the page's one input that
    # takes a whole one-time code (kept in vf-otc): autocomplete=one-time-code, not type=hidden, no
    # maxlength under the code's length; shown or not (input-otp, WorkOS: a transparent input over
    # drawn slots, B-12). READ: how many characters the boxes show beyond what they showed before
    # (input values, else their text; letters, digits, mask dots; ALL: every box's characters, for
    # a code typed one per box, so a retry into boxes that already held some reads full) and how
    # many the one-time-code input holds; never the characters.
    _CODE_JS = r"""
      const BOX = Symbol.for("vf-code-boxes"), OTC = Symbol.for("vf-otc");
      const CH = /[\p{L}\p{N}•●*]/gu, cnt = (s) => ((s ?? "").match(CH) ?? []).length;
      const val = (e) => /^(input|textarea)$/.test(e.localName) ? e.value : (e.innerText ?? "");
      if (READ) {
        const b = window[BOX] ?? [], o = window[OTC];
        const live = b.filter(([e]) => e.isConnected);
        return { url: location.href, gone: !live.length && !o?.isConnected,
                 boxes: live.reduce((a, [e, v]) => a + (ALL || val(e) !== v ? cnt(val(e)) : 0), 0),
                 otc: o?.isConnected ? cnt(o.value) : 0 };
      }
      let els = VF.all(SEL).filter(VF.shown), group = false;
      if (els.length === 1 && els[0].localName === "input" && LEN > 1) {
        const e = els[0], esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        let box = VF.up(e);
        for (let i = 0; box && i < 4 && box.querySelectorAll("input").length < 4; i++) box = VF.up(box);
        const peers = box ? [...box.querySelectorAll("input")].filter((x) => x.type !== "hidden" && VF.shown(x)) : [];
        for (const a of ["id", "name", "aria-label"]) {
          const v = e.getAttribute(a) ?? "";
          for (const m of v.matchAll(/\d+/g)) {
            const rx = new RegExp("^" + esc(v.slice(0, m.index)) + "(\\d+)" + esc(v.slice(m.index + m[0].length)) + "$");
            const got = peers.map((x) => [x, (x.getAttribute(a) ?? "").match(rx)]).filter(([, g]) => g)
              .map(([x, g]) => [x, +g[1]]).sort((p, q) => p[1] - q[1]);
            if (got.length >= 4 && got.length >= LEN && got.every(([, k], j) => !j || k === got[j - 1][1] + 1)) {
              els = got.map(([x]) => x);
              group = true;
              break;
            }
          }
          if (group) break;
        }
      }
      window[BOX] = els.map((e) => [e, val(e)]);
      const otc = VF.all('input[autocomplete~="one-time-code" i]').filter((e) => e.type !== "hidden" && !e.disabled
        && !e.readOnly && (e.maxLength < 0 || e.maxLength >= LEN));
      let o = otc.length === 1 ? otc[0] : null;
      // Several (a second code form on the page): the one in the smallest box around the first match.
      for (let n = els[0] ? VF.up(els[0]) : null, i = 0; !o && otc.length > 1 && n && i < 5; n = VF.up(n), i++) {
        const near = otc.filter((e) => VF.inside(e, n));
        if (near.length === 1) o = near[0]; else if (near.length) break;
      }
      window[OTC] = o;
      const css = o ? (o.id ? `input#${CSS.escape(o.id)}` : "input" + (o.name ? `[name="${o.name}"]` : "")
        + '[autocomplete="one-time-code"]') : null;
      return { n: els.length, otc: css, among: !!o && els.includes(o), group,
               inputs: els.every((e) => /^(input|textarea)$/.test(e.localName) || e.isContentEditable) };"""
    _OTC_FOCUS_JS = r"""
      const e = window[Symbol.for("vf-otc")];
      if (!e?.isConnected) return null;
      e.focus();
      const r = e.getBoundingClientRect();
      return { focused: VF.active() === e && document.hasFocus(), filled: !!e.value, x: r.x + r.width / 2, y: r.y + r.height / 2 };"""
    # A one-time-code input that takes no focus (display:none): the value, with input and change.
    _OTC_SET_JS = r"""
      const e = window[Symbol.for("vf-otc")];
      if (!e?.isConnected) return false;
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(e, VAL);
      for (const t of ["input", "change"]) e.dispatchEvent(new Event(t, { bubbles: true }));
      return true;"""

    async def type_code(self, selector, code, *, frame=None, timeout=15):
        """Type a one-time code into the inputs matching `selector`, in
        order, one character each (split 6-digit fields); if it matches one
        input, the whole code goes into it, unless that input is one of ≥ 4
        whose id/name/aria-label differ only by a consecutive index
        (#pin-code-input-0…5): then one character into each of them. A code
        typed one per box reads back every box's characters (a retry into
        boxes that already held some reads full). When the boxes only draw the code
        and the page has one input[autocomplete=one-time-code] that takes it
        whole (visible or not), the code goes there. Then reads back how many
        characters the field shows: when fewer than the code has, it types it
        into that one-time-code input (if not done yet), else raises "typed 6,
        the field shows 0" naming the real input. Never returns the code.
        Frames as in click(): the page first, then its frames, or `frame`."""
        code = str(code)
        find = _vf(self._CODE_JS, SEL=selector, LEN=len(code), READ=False, ALL=False)
        fs = self._frames()
        try:
            doc, r = await self._lookup(find, lambda r: bool(r) and (r["n"] > 0 or bool(r["otc"])), what=selector,
                                        fs=fs, frame=frame, timeout=timeout)
            where, n, real = self._in(doc), r["n"], r["otc"]
            before = await self._ev("location.href", 5, doc)

            async def shows(every=False):
                await _asyncio.sleep(0.15)
                try:
                    s = await self._ev(_vf(self._CODE_JS, SEL=None, LEN=0, READ=True, ALL=every), 5, doc)
                except (RuntimeError, _asyncio.TimeoutError):
                    return None  # the document went away: the code was taken (auto-submit)
                if s["gone"] or s["url"] != before:
                    return None
                return max(s["boxes"], s["otc"])

            async def into_real():
                f = await self._ev(_vf(self._OTC_FOCUS_JS), 5, doc)
                if f is None:
                    return False
                if doc.parent is not None and not f["focused"]:
                    try:
                        await self._click_into(doc, _vf(self._OTC_FOCUS_JS), f, real)
                        f["focused"] = True
                    except RuntimeError:
                        f["focused"] = False
                if f["focused"] or doc.parent is None and await self._ev(
                        "document.activeElement === window[Symbol.for('vf-otc')]", 5, doc):
                    if f["filled"]:
                        await self._cdp.press("Ctrl+A")
                        await self._cdp.press("Backspace")
                    await self.keys(code, _echo=False)
                else:
                    await self._ev(_vf(self._OTC_SET_JS, VAL=code), 5, doc)
                return True

            if n == 0 or (real and not r["among"] and not r["inputs"]):
                # Only drawn slots (or nothing) match: the real input takes the code.
                await into_real()
                got, how = await shows(), f"typed the {len(code)}-character code into the page's one-time-code input {real}{where}"
                if n:
                    how += f" ({selector} matches {n} drawn boxes)"
                tried = True
            elif n == 1 and not r.get("group"):
                await self.type(selector, code, _echo=False, _doc=doc)
                got, how, tried = await shows(), f"typed the {len(code)}-character code into {selector}{where}", bool(r["among"])
            else:
                if n < len(code) and not real:
                    raise RuntimeError(f"{selector}{where} matches {n} inputs, but the code has {len(code)} characters")
                tried = False
                if n >= len(code):
                    focus = r"""
                      const el = GROUP ? window[Symbol.for("vf-code-boxes")][I][0] : VF.all(SEL).filter(VF.shown)[I];
                      el.focus();
                      try { el.select?.(); } catch {}
                      const r = el.getBoundingClientRect();
                      return { focused: VF.active() === el && document.hasFocus(), x: r.x + r.width / 2, y: r.y + r.height / 2 };"""
                    for i, ch in enumerate(code):
                        expr = _vf(focus, SEL=selector, I=i, GROUP=bool(r.get("group")))
                        f = await self._ev(expr, doc=doc)
                        if doc.parent is not None and not f["focused"]:
                            await self._click_into(doc, expr, f, selector)
                        await self.keys(ch, _echo=False)
                    got = await shows(every=True)
                else:
                    got = 0
                how = f"typed the {len(code)}-character code into {n} inputs{where}" + (
                    f" ({selector} and its {n - 1} numbered siblings, one character each)" if r.get("group") else "")
            if got is not None and got < len(code) and real and not tried:
                first = got
                if await into_real():
                    got = await shows()
                    how = (f"typed the {len(code)}-character code into the page's one-time-code input {real}{where}"
                           f" ({selector} showed {first} of {len(code)} characters)")
                    tried = True
            if got is not None and got < len(code):
                raise RuntimeError(f"typed {len(code)} characters, but the field shows {got}: "
                                   + (f"the real input is {real}, and it doesn't take them either" if real and tried
                                      else f"the real input is {real}" if real
                                      else f"{selector} isn't the code field (look for it in fields())")
                                   + where + await self._where())
            return how + ("; the page went on" if got is None else f"; it shows {got} of {len(code)}")
        finally:
            await fs.close()

    async def select(self, selector, option=None, *, index=None, frame=None):
        """Choose `option` in the native <select> `selector`: by its text
        (exact, then any case, then its value when only that option has it,
        then a piece of text only one option has), or index=N (0-based).
        Sets selectedIndex, sends input and change, and reads the selected
        option's text back: raises, naming the options, when no option fits,
        several do, or the field then shows another one (read again 300 ms
        later first: a React select the page redrew). Options that load
        once it is focused or pressed (one placeholder option until then)
        are waited for ≤ 1.5 s, then it tries again. Then the select loses
        the focus (blur) and it waits ≤ 1.5 s for the page to settle (no DOM change, no request in flight), so the
        fields the choice shows are there. Frames as in click()."""
        if (option is None) == (index is None):
            raise ValueError("select needs option or index=")
        fs = self._frames()
        try:
            doc, r = await self._find(_vf(_SELECT_JS, SEL=selector, OPT=option, IDX=index),
                                      lambda r: bool(r) and r.get("err") != "no element", frame=frame, fs=fs)
        except _NoFrame as e:
            raise RuntimeError(str(e)) from None
        finally:
            await fs.close()
        if doc is None:
            raise RuntimeError(f"select {selector}: no element{self._searched(fs)}")
        if "err" in r and (ok := await self._select_again(r["err"], selector, doc)):
            await self._select_settle(selector, doc)
            return f"selected {ok!r} in {selector}{self._in(doc)} (the page redrew the field)"
        woke = ""
        if "err" in r and r["err"].startswith("no option") and await self._select_wake(selector, doc):
            r = await self._ev(_vf(_SELECT_JS, SEL=selector, OPT=option, IDX=index), doc=doc)  # MOVE-0016
            woke = " (its options loaded once focused)"
        if "err" in r:
            raise RuntimeError(f"select {selector}{self._in(doc)}: {r['err']}")
        await self._select_settle(selector, doc)
        return f"selected {r['ok']!r} in {selector}{self._in(doc)}{woke}"

    async def _select_settle(self, selector, doc):
        """After select(): blur, then ≤ 1.5 s for the page to settle (B-74,
        MONEY-0229: the dependent fields come after the change)."""
        try:
            await self._ev(_vf(self._SELECT_SETTLE_JS, SEL=selector), 5, doc)
        except (RuntimeError, _asyncio.TimeoutError):
            pass  # the page went on

    async def _select_again(self, err, selector, doc):
        """_SELECT_JS said 'set "X", but the field shows …' (PayPal's React
        select reads null while it redraws, MONEY-0327): X when the select
        (`selector`, else the one kept in vf-field) shows it 300 ms later."""
        m = _re.match(r'set ("(?:[^"\\]|\\.)*"), but the field shows', err)
        if not m:
            return None
        want = _json.loads(m.group(1))
        await _asyncio.sleep(0.3)
        try:
            now = await self._ev(_vf(r"""
              const f = window[Symbol.for("vf-field")];
              const el = SEL === null ? (f?.isConnected ? f : null) : (VF.all(SEL).find(VF.shown) ?? VF.all(SEL)[0]);
              return el?.localName === "select" && el.selectedIndex >= 0
                ? VF.norm(el.options[el.selectedIndex].text || el.options[el.selectedIndex].label || "") : null;""",
                                     SEL=selector), 5, doc)
        except (RuntimeError, _asyncio.TimeoutError):
            return None
        return want if now == want else None

    # choose()'s field. A custom select gets its control box kept as the next click's element and
    # as vf-field (what it shows is read there), its text input as vf-combo-in: react-select's
    # __control (also emotion's css-…-control), MUI's Select/Autocomplete, ng-select, select2,
    # choices.js, vue-select, Vuetify, a role=combobox / aria-haspopup=listbox box, an input over a
    # ul[role=listbox]; a hidden native <select> with such a widget beside it: that widget too
    # (widget), so the shown control gets the choice as well (MONEY-0230, B-41).
    _CHOOSE_JS = r"""
      const CTL_CLS = /(^|\s)([\w-]*__control|css-[\w]+-control|MuiSelect-select|MuiAutocomplete-inputRoot|ng-select-container|select2-selection|choices__inner|vs__dropdown-toggle|multiselect__tags|v-select__slot|v-field__input)(\s|$)/;
      let el = null;
      try { const c = VF.all(FIELD); el = c.find(VF.shown) ?? c.find((e) => e.localName === "input" && VF.up(e) && VF.shown(VF.up(e))) ?? null; } catch {}
      if (!el) {
        const want = VF.norm(FIELD).toLowerCase();
        const CTL = "input:not([type=hidden]), select, [role=combobox], [aria-haspopup]";
        for (const l of VF.all("label")) {
          if (!VF.label(l).toLowerCase().includes(want)) continue;
          const c = l.control ?? l.querySelector(CTL) ?? l.parentElement?.querySelector(CTL);
          if (c && (VF.shown(c) || c.localName === "select" || (VF.up(c) && VF.shown(VF.up(c))))) { el = c; break; }  // a hidden native select too
        }
        const named = (e) => `${VF.label(e)} ${VF.attr(e, "aria-label")} ${VF.attr(e, "label")} ${VF.attr(e, "placeholder")} `
          + VF.attr(e, "aria-labelledby").split(/\s+/).filter(Boolean).map((i) => e.getRootNode().getElementById?.(i)?.innerText ?? "").join(" ");
        el ??= VF.elements().find((e) => e.matches("select, [role=combobox], [aria-haspopup], input[readonly], [label]")
          && VF.shown(e) && named(e).toLowerCase().includes(want)) ?? null;
        el ??= VF.find({ text: FIELD }).el;
      }
      if (!el) return null;
      // A custom element (s-select, huk24.de) wrapping a hidden native <select>: select() on that.
      const nat = el.localName === "select" ? el
        : (el.querySelectorAll?.("select").length === 1 ? el.querySelector("select") : null) ?? el.shadowRoot?.querySelector("select")
          ?? (el.id ? VF.all(`label[for="${CSS.escape(el.id)}"]`)[0]?.control : null) ?? null;
      if (nat?.localName === "select") el = nat;
      let ctl = null, widget = null;
      // A widget beside a hidden native <select> (slim-select's .ss-main, which ignores option clicks
      // by script: B-65, SHOP-0027; select2, choices.js): select() on the native one, by data-id or as
      // a sibling ≤ 4 levels up; the widget box must show it too.
      if (el.localName !== "select") {
        const did = VF.attr(el.closest?.("[data-id]") ?? el, "data-id");
        let twin = did ? VF.all(`select[data-id="${CSS.escape(did)}"]`).find((s) => !VF.shown(s)) ?? null : null, wbox = twin ? el : null;
        for (let n = el, i = 0; !twin && n && n !== document.body && i < 4; n = VF.up(n), i++)
          for (const s of [n.previousElementSibling, n.nextElementSibling])
            if (s?.localName === "select" && !VF.shown(s) && s.options.length >= 2) { twin = s; wbox = n; break; }
        if (twin) { widget = wbox; el = twin; }
      }
      if (el.localName !== "select") {
        for (let n = el, i = 0; n && n !== document.body && i < 6; n = VF.up(n), i++) {
          if (CTL_CLS.test(VF.attr(n, "class"))) { ctl = n; break; }
          if (!ctl && n.localName !== "input" && (VF.attr(n, "role") === "combobox" || VF.attr(n, "aria-haspopup") === "listbox")) ctl = n;
        }
        if (!ctl && el.localName === "input" && (VF.attr(el, "role") === "combobox" || VF.attr(el, "aria-haspopup") === "listbox"
            || VF.up(el)?.querySelector("[role=listbox]"))) ctl = el;
      } else if (!VF.shown(el) && !widget) {
        for (let n = VF.up(el), i = 0; n && !widget && n !== document.body && i < 3; n = VF.up(n), i++)
          widget = [...n.querySelectorAll("input:not([type=hidden]), [role=combobox], [aria-haspopup]")].find((w) => w !== el && VF.shown(w)) ?? null;
        // A styled box beside it (slim-select's .ss-main, nice-select, chosen, selectize).
        for (const n of [el, VF.up(el)]) for (const s of widget || !n ? [] : [n.nextElementSibling, n.previousElementSibling])
          if (s && !widget && VF.shown(s) && /(^|[\s_-])(ss-main|select2|choices|selectize|chosen|nice-select|custom-select|select|dropdown)([\s_-]|$)/i.test(VF.attr(s, "class"))) widget = s;
      }
      const box = ctl ?? el, inp = box.localName === "input" ? box : box.querySelector?.("input:not([type=hidden])") ?? null;
      window[Symbol.for("vf-el")] = window[Symbol.for("vf-field")] = box;
      window[Symbol.for("vf-combo-in")] = inp;
      window[Symbol.for("vf-widget")] = widget;
      // An autocomplete (Angular mat-autocomplete, aria-autocomplete): its list comes after typing (MOVE-0212).
      const auto = !!inp && (/^(list|both)$/.test(VF.attr(inp, "aria-autocomplete")) || inp.hasAttribute("matautocomplete")
        || /(^|\s)mat(-mdc)?-autocomplete-trigger(\s|$)/.test(VF.attr(inp, "class")));
      return { select: el.localName === "select", input: !!inp && !inp.readOnly, combo: !!ctl, widget: !!widget, auto,
               what: (box.localName + " " + VF.label(el).slice(0, 60)).trim() };"""
    # What the kept field (vf-field) shows includes OPT; true when it closed with its dialog.
    _SHOWS_JS = r"""
      const f = window[Symbol.for("vf-field")];
      if (!f?.isConnected || !VF.shown(f)) return true;
      const sel = f.localName === "select" && f.selectedIndex >= 0 ? f.options[f.selectedIndex].text : "";
      return `${f.value ?? ""} ${sel} ${f.innerText ?? ""} ${VF.attr(f, "value")} ${f.shadowRoot ? VF.visibleText(f) : ""}`
        .replace(/\s+/g, " ").toLowerCase().includes(OPT);"""
    # The custom select's text input (else its box) takes the focus, for keys.
    _COMBO_FOCUS_JS = r"""
      const i = window[Symbol.for("vf-combo-in")], f = window[Symbol.for("vf-field")], t = i?.isConnected ? i : f;
      if (!t?.isConnected) return false;
      t.focus();
      return VF.inside(VF.active(), t) || VF.inside(VF.active(), f);"""

    async def choose(self, field, option, *, exact=False, frame=None, timeout=10):
        """Pick `option` (its text) in a dropdown: `field` is a CSS selector,
        or the text of its label, placeholder or button. A native <select>
        gets select() (options that load on focus are waited for ≤ 1.5 s;
        a shown widget that stands for a hidden one, or sits beside it:
        slim-select, select2, gets the choice too); a custom one (react-select,
        MUI, ng-select, select2,
        Vaadin, an input over a list, …) is opened with a real mouse press on
        its control box, else (an autocomplete: Angular mat-autocomplete)
        its input cleared and the option's first letters typed, else
        ArrowDown, else by typing the option's first
        letters; then the option with that exact text (else the shortest
        containing it, unless exact) is clicked, and what the control shows
        is read back (when it doesn't show it: typed and Enter into its
        input, else picked with the keyboard: ArrowDown to it, Enter; then
        read again). No such option: raises listing ≤ 12 options it saw.
        Frames as in click()."""
        fs = self._frames()
        opt = _re.sub(r"\s+", " ", option).strip().lower()
        try:
            try:
                doc, f = await self._find(_vf(self._CHOOSE_JS, FIELD=field), frame=frame, fs=fs)
            except _NoFrame as e:
                raise RuntimeError(str(e)) from None
            if not doc:
                near = await self._nearest({"text": field} if not _re.match(r"^[#.\[]", field) else {"css": field})
                raise RuntimeError(f"no visible field {field!r}" + (f"; closest: {near}" if near else "") + self._searched(fs))
            f["what"] += self._in(doc)
            if f["select"]:
                r = await self._ev(_vf(_SELECT_JS, SEL=None, OPT=option, IDX=None), doc=doc)
                if "err" in r and (ok := await self._select_again(r["err"], None, doc)):
                    r = {"ok": ok}
                woke = ""
                if "err" in r and r["err"].startswith("no option") and await self._select_wake(None, doc):
                    r = await self._ev(_vf(_SELECT_JS, SEL=None, OPT=option, IDX=None), doc=doc)  # options loaded on focus (MOVE-0016)
                    woke = " (its options loaded once focused)"
                if "err" in r and not (f.get("widget") and r["err"].startswith("no option")):
                    raise RuntimeError(f"choose {field!r}: {r['err']}")
                said = f"chose {r['ok']!r} in {f['what']}{woke}" if "ok" in r else ""
                if said and not f.get("widget"):
                    return said
                # The hidden select holds it (or lacks it: an AJAX list); the widget standing for it must show it too.
                w_in = await self._ev("(() => { const w = window[Symbol.for('vf-widget')]; window[Symbol.for('vf-el')] ="
                                      " window[Symbol.for('vf-field')] = w; const i = w?.localName === 'input' ? w"
                                      " : w?.querySelector?.('input:not([type=hidden])') ?? null;"
                                      " window[Symbol.for('vf-combo-in')] = i; return !!i && !i.readOnly; })()", 5, doc)
                if said and await self._ev(_vf(self._SHOWS_JS, OPT=r["ok"].lower()), 5, doc):
                    return said
                f.update(combo=True, input=bool(w_in), what=f["what"] + " (its shown widget)")
                if said:
                    option = r["ok"]
                    opt = option.lower()
            # Already open (an autocomplete after type()): its backdrop may cover the field.
            pick = await self._option(option, exact, 0, strict=True, doc=doc)
            if not pick and f.get("combo"):
                pick = await self._combo_open(option, exact, f, doc, timeout)
            elif not pick:
                try:
                    await self.click(_spec={"el": True}, timeout=2, _doc=doc)
                except RuntimeError as e:
                    if "covered" not in str(e) or not (pick := await self._option(option, exact, 0, doc=doc)):
                        raise
                if not pick:
                    pick = await self._option(option, exact, min(timeout, 2.5), doc=doc)
                if not pick and f["input"]:
                    await self._ev(_vf(self._COMBO_CLEAR_JS), 5, doc)
                    await self._cdp.send("Input.insertText", {"text": option})
                    pick = await self._option(option, exact, timeout, doc=doc)
            if not pick:
                seen = await self._options_seen(doc)
                raise RuntimeError(f"no option {option!r} showed up after opening {f['what']}"
                                   + (" (mouse press, typing its first letters, ArrowDown)" if f.get("combo") else "")
                                   + (f"; options seen: {seen}" if seen else "; no option showed at all")
                                   + await self._where())
            await self.click(_spec={"el": True}, _doc=doc)
            await _asyncio.sleep(0.1)
            shows = await self._ev(_vf(self._SHOWS_JS, OPT=opt), doc=doc)
            how = ""
            if not shows and f.get("combo") and f["input"] and await self._ev(_vf(self._COMBO_CLEAR_JS), 5, doc):
                await self._cdp.send("Input.insertText", {"text": option})  # react-select: the typed text's first option, Enter
                await _asyncio.sleep(0.3)
                await self._act(self._cdp.press("Enter"))
                await _asyncio.sleep(0.2)
                shows = await self._ev(_vf(self._SHOWS_JS, OPT=opt), doc=doc)
                how = " (the click didn't take: typed it and pressed Enter)"
            elif not shows and f.get("combo") and await self._combo_keys(opt, doc):
                await _asyncio.sleep(0.3)
                shows = await self._ev(_vf(self._SHOWS_JS, OPT=opt), doc=doc)
                how = " (the click didn't take: picked with ArrowDown and Enter)"
            return f"chose {pick} in {f['what']}{how}" + ("" if shows else " (the field doesn't show it: check)")
        finally:
            await fs.close()

    async def _combo_open(self, option, exact, f, doc, timeout):
        """Open custom select `f` (choose()'s field, kept as vf-el) until
        `option` shows: a real mouse press on its control box; else, for an
        autocomplete (its list comes after typing: mat-autocomplete), its
        input cleared and the option's first letters typed, then the whole
        option; else ArrowDown in it; else its first letters typed (into its
        cleared input, else as keys). Returns _option's pick, or None."""
        await self._ev(_vf(self._COMBO_FOCUS_JS), 5, doc)
        try:
            await self.click(_spec={"el": True}, timeout=2, _doc=doc)
        except RuntimeError as e:
            if "covered" not in str(e):
                raise
        if pick := await self._option(option, exact, 0.8, doc=doc):
            return pick
        head = option.strip()[:4] if f.get("auto") else option.strip()[:3]
        if f.get("auto") and f["input"] and await self._ev(_vf(self._COMBO_CLEAR_JS), 5, doc):
            # No ArrowDown: it would pick the list's first entry (MOVE-0212: Slovenia instead of Germany).
            await self._cdp.send("Input.insertText", {"text": head})
            if pick := await self._option(option, exact, min(timeout, 2.5), doc=doc):
                return pick
            await self._cdp.send("Input.insertText", {"text": option.strip()[len(head):]})
            return await self._option(option, exact, min(timeout, 2.5), doc=doc)
        if await self._ev(_vf(self._COMBO_FOCUS_JS), 5, doc):
            await self._act(self._cdp.press("ArrowDown"))
            if pick := await self._option(option, exact, 0.8, doc=doc):
                return pick
            if f["input"] and await self._ev(_vf(self._COMBO_CLEAR_JS), 5, doc):
                await self._cdp.send("Input.insertText", {"text": head})
            else:
                await self.keys(head[:1], _echo=False)  # a listbox's type-ahead
            return await self._option(option, exact, min(timeout, 2.5), doc=doc)
        return None

    # choose(): its input (else its box) takes the focus, emptied first (typed letters filter from
    # scratch, not after the shown value: "SloveniaGerm").
    _COMBO_CLEAR_JS = r"""
      const i = window[Symbol.for("vf-combo-in")], f = window[Symbol.for("vf-field")], t = i?.isConnected ? i : f;
      if (!t?.isConnected) return false;
      t.focus();
      if (t.matches?.("input, textarea") && !t.readOnly && t.value) {
        const proto = t instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(proto, "value").set.call(t, "");
        t.dispatchEvent(new Event("input", { bubbles: true }));
      }
      return VF.inside(VF.active(), t) || VF.inside(VF.active(), f);"""
    # The options on show, for "no option …" (B-65: "Gebrauchtwagen" vs "Gebrauchtfahrzeug"): ≤ 13
    # labels of shown options (role=option and the like, else list items of a listbox or option
    # classes), else the kept native select's options.
    _OPTIONS_SEEN_JS = r"""
      const field = window[Symbol.for("vf-field")], out = [];
      const add = (t) => { t = VF.norm(t).slice(0, 60); if (t && !out.includes(t)) out.push(t); };
      for (const sel of ["[role=option], [role=menuitem], [role=menuitemradio], [role=treeitem], mat-option, vaadin-combo-box-item, vaadin-select-item, vaadin-item",
                         "[role=listbox] li, [class*=option i]"]) {
        for (const e of VF.all(sel)) {
          if (e === field || !VF.shown(e) || [...e.querySelectorAll(sel)].some(VF.shown)) continue;
          add(VF.label(e) || e.textContent || "");
          if (out.length >= 13) break;
        }
        if (out.length) return out;
      }
      if (field?.localName === "select") for (const o of [...field.options].slice(0, 13)) add(o.text || o.label || "");
      return out;"""

    async def _options_seen(self, doc):
        """"'A', 'B', … (12 of more)": the options on show (≤ 12), or ""."""
        try:
            seen = await self._ev(_vf(self._OPTIONS_SEEN_JS), 5, doc) or []
        except (RuntimeError, _asyncio.TimeoutError):
            return ""
        return ", ".join(repr(s) for s in seen[:12]) + (" (12 of more)" if len(seen) > 12 else "")

    # The option a listbox has active now (focus, aria-activedescendant, MUI's focus classes): its label.
    _ACTIVE_OPT_JS = r"""
      const OPT = "[role=option], [role=menuitem], [role=menuitemradio], option, mat-option, li";
      let o = VF.active()?.closest?.(OPT) ?? null;
      if (!o) for (const x of VF.all("[aria-activedescendant]")) {
        const id = VF.attr(x, "aria-activedescendant"), t = id && x.getRootNode().getElementById?.(id);
        if (t && VF.shown(t)) { o = t; break; }
      }
      o ??= VF.all("[role=option].Mui-focusVisible, [role=option].Mui-focused, [role=option].focused, [role=option].active, [role=option].highlighted")
        .find(VF.shown) ?? null;
      return o ? VF.norm(VF.label(o) || o.textContent || "").toLowerCase() : null;"""

    async def _combo_keys(self, opt, doc):
        """A custom select that ignored the option's click (MUI Select / time
        picker, MOVE-0017): its box focused, ArrowDown (opens it, or moves),
        on until the active option is `opt` (≤ 60 down, then ≤ 60 up), Enter.
        True when it pressed Enter on it."""
        if not await self._ev(_vf(self._COMBO_FOCUS_JS), 5, doc):
            return False
        for key in ("ArrowDown", "ArrowUp"):
            last, same = None, 0
            for _ in range(60):
                await self._act(self._cdp.press(key))
                await _asyncio.sleep(0.08)
                now = await self._ev(_vf(self._ACTIVE_OPT_JS), 5, doc)
                if now == opt:
                    await self._act(self._cdp.press("Enter"))
                    return True
                same = same + 1 if now == last else 0
                last = now
                if same >= 2:  # the end of the list (or no list): the other way
                    break
        return False

    async def _select_wake(self, selector, doc):
        """A native select whose options load once it is focused or pressed
        (MOVE-0016: one option until clicked): focus it and send it the
        pointer/mouse events of a press (no native popup), then wait ≤ 1.5 s
        for its options to change. True when they did."""
        js = r"""
          const f = window[Symbol.for("vf-field")];
          const el = SEL === null ? (f?.isConnected ? f : null) : (VF.all(SEL).find(VF.shown) ?? VF.all(SEL)[0]);
          if (el?.localName !== "select") return null;
          if (!WAKE) return [...el.options].map((o) => o.text).join("\u0001");
          const n = [...el.options].map((o) => o.text).join("\u0001");
          el.focus();
          const r = el.getBoundingClientRect(), o = { bubbles: true, cancelable: true, view: window, clientX: r.x + 4, clientY: r.y + 4 };
          for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"])
            el.dispatchEvent(t.startsWith("pointer") ? new PointerEvent(t, { ...o, pointerId: 1, isPrimary: true }) : new MouseEvent(t, o));
          return n;"""
        try:
            before = await self._ev(_vf(js, SEL=selector, WAKE=True), 5, doc)
            if before is None:
                return False
            end = _time.monotonic() + 1.5
            while _time.monotonic() < end:
                await _asyncio.sleep(0.25)
                if await self._ev(_vf(js, SEL=selector, WAKE=False), 5, doc) != before:
                    await _asyncio.sleep(0.2)  # the rest of the list
                    return True
        except (RuntimeError, _asyncio.TimeoutError):
            pass
        return False

    async def _option(self, option, exact, seconds, strict=False, doc=None):
        """Wait for a visible option with that text and keep it as the next
        click's element; returns its description. strict: only elements
        marked as options, not list items that merely look like them."""
        js = r"""
          const want = VF.norm(OPT).toLowerCase(), field = window[Symbol.for("vf-field")];
          const tiers = [
            "[role=option], [role=menuitem], [role=menuitemradio], [role=treeitem], option, mat-option, vaadin-combo-box-item, vaadin-select-item, vaadin-item",
            "li, [role=gridcell], [class*=option i], [class*=item i]",
          ];
          for (const sel of tiers.slice(0, STRICT ? 1 : 2)) {
            const c = VF.all(sel).filter((e) => e !== field && VF.shown(e));
            const lab = (e) => VF.label(e).toLowerCase();
            const el = c.find((e) => lab(e) === want)
              ?? (EXACT ? null : c.filter((e) => lab(e).includes(want)).sort((a, b) => lab(a).length - lab(b).length)[0]);
            if (el) {
              window[Symbol.for("vf-el")] = el;
              return (el.localName + " " + VF.label(el).slice(0, 60)).trim();
            }
          }
          return null;"""
        deadline = _time.monotonic() + seconds
        while True:
            pick = await self._ev(_vf(js, OPT=option, EXACT=exact, STRICT=strict), doc=doc)
            if pick or _time.monotonic() > deadline:
                return pick
            await _asyncio.sleep(0.2)

