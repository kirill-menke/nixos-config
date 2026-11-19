# Page-side bodies of ShopTab.click (run with _vf). The element found is kept
# in window[Symbol.for("vf-el")] between steps.
_LOCATE_JS = r"""
  const { el, total, visible, card, ambiguous, refused, again } = VF.find(SPEC);
  if (!el) return { total, visible, ambiguous, refused, again };
  window[Symbol.for("vf-el")] = el;
  const ctl = VF.checkable(el);
  // No cover: the element's own parts (an svg icon in it) and ancestors,
  // a label of it or of its control (a styled radio's label lies on it), and
  // anything in the small box around it that holds no other control (a
  // payment tile's logo over its radio: Shopify).
  const CTL = "input:not([type=hidden]), select, textarea, button, a[href], [role=button], [role=radio], [role=checkbox]";
  const own = (c) => c === el || c === ctl || VF.inside(c, el) || VF.inside(el, c);
  const mine = (h) => {
    if (VF.inside(h, el) || VF.inside(el, h)) return true;
    for (const l of [...(el.labels instanceof NodeList ? el.labels : []), ...(ctl?.labels instanceof NodeList ? ctl.labels : [])]) if (VF.inside(h, l)) return true;
    for (let n = h, i = 0; n && i < 10; n = VF.up(n), i++)
      if (n.localName === "label") {
        const c = n.control ?? n.querySelector("input");
        if (c && own(c)) return true;
      }
    for (let n = VF.up(el), i = 0; n && n !== document.body && i < 3; n = VF.up(n), i++) {
      if (!VF.inside(h, n)) continue;
      return [...n.querySelectorAll(CTL)].every((c) => own(c) || (c.localName === "label" && own(c.control)));
    }
    return false;
  };
  // Fixed or sticky, but no dialog: a header or bar the page scrolls under.
  const sticky = (h) => {
    for (let n = h, i = 0; n && n !== document.body && i < 20; n = VF.up(n), i++) {
      if (/^(dialog|alertdialog)$/.test(VF.attr(n, "role")) || VF.attr(n, "aria-modal") === "true" || n.localName === "dialog") return false;
      if (/^(fixed|sticky)$/.test(getComputedStyle(n).position)) return true;
    }
    return false;
  };
  const measure = () => {
    const r = el.getBoundingClientRect();
    const x = r.x + r.width * AT[0], y = r.y + r.height * AT[1];
    return { r, x, y, hit: VF.hitAt(x, y) };
  };
  let m = measure();
  if (SCROLL && (m.r.top < 0 || m.r.left < 0 || m.r.bottom > innerHeight || m.r.right > innerWidth)) {
    el.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
    m = measure();
  }
  const isBanner = (h) => !!VF.bannerOf(h, true);
  if (SCROLL && m.hit && !mine(m.hit) && sticky(m.hit) && !isBanner(m.hit)) {
    el.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
    m = measure();
  }
  const name = (e) => (e.localName + " " + VF.label(e).slice(0, 60)).trim();
  const cover = m.hit && !mine(m.hit) ? m.hit : null;
  let coverText = null;
  if (cover) {
    for (let n = cover, i = 0; n && n !== document.body && i < 25; n = VF.up(n), i++) {
      const s = getComputedStyle(n);
      if (/^(dialog|alertdialog)$/.test(VF.attr(n, "role")) || VF.attr(n, "aria-modal") === "true"
          || n.localName === "dialog" || s.position === "fixed") { coverText = VF.norm(n.innerText).slice(0, 200); break; }
    }
  }
  return { x: m.x, y: m.y, total, what: name(el), card, again,
           covered: !m.hit ? "nothing: the point is off screen" : cover ? name(cover) : null,
           coverText, banner: cover ? isBanner(cover) : false };"""
# Starts counting what the click changes: DOM mutations (also in the shadow
# roots on its way), fetches, navigations, focus; for a checkbox, radio or
# switch its checked state, for a tab its aria-selected.
_ARM_JS = r"""
  const el = window[Symbol.for("vf-el")], k = Symbol.for("vf-click");
  if (!el?.isConnected) return { gone: "the element left the page (it reloaded or redrew)" };
  const old = window[k];
  if (old) { old.obs.disconnect(); old.perf?.disconnect(); window.navigation?.removeEventListener("navigate", old.nav); }
  const c = VF.checkable(el);
  let tab = null;
  for (let n = el, i = 0; n && i < 6 && !c; n = VF.up(n), i++) if (VF.attr(n, "role") === "tab") { tab = n; break; }
  const st = window[k] = { n: 0, url: location.href, focus: VF.active(), c, checked: VF.checked(c),
                           tab, selected: tab ? VF.attr(tab, "aria-selected") === "true" : null, top: topLayer(),
                           layers: VF.layers() };
  function topLayer() { try { return VF.all(":popover-open, dialog[open]").length; } catch { return VF.all("dialog[open]").length; } }
  const opts = { subtree: true, childList: true, attributes: true, characterData: true };
  st.obs = new MutationObserver((ms) => { st.n += ms.length; });
  st.obs.observe(document, opts);
  for (let r = el.getRootNode(); r instanceof ShadowRoot; r = r.host.getRootNode()) st.obs.observe(r, opts);
  if (el.shadowRoot) st.obs.observe(el.shadowRoot, opts);
  try {
    st.perf = new PerformanceObserver((l) => { st.n += l.getEntries().length; });
    st.perf.observe({ type: "resource" });
  } catch {}
  st.nav = () => { st.n++; };
  window.navigation?.addEventListener("navigate", st.nav);
  const off = (n) => !!n && (n.disabled === true || VF.attr(n, "aria-disabled") === "true");
  return { url: st.url, kind: c ? VF.kind(c) : null, checked: st.checked, tab: !!tab, selected: st.selected,
           disabled: off(c) || off(el) };"""
# A checkbox the page replaced (re-rendered) or a new URL counts as changed.
_EFFECT_JS = r"""
  const st = window[Symbol.for("vf-click")];
  if (!st) return { changed: true, url: location.href };
  if (location.href !== st.url) return { changed: true, url: location.href };
  const el = window[Symbol.for("vf-el")], f = VF.active();
  if (st.c) {
    if (!st.c.isConnected) return { changed: true, url: location.href };
    const now = VF.checked(st.c);
    return { changed: now !== st.checked, checked: now, url: location.href };
  }
  if (st.tab) return { changed: !st.tab.isConnected || (!st.selected && VF.attr(st.tab, "aria-selected") === "true"), url: location.href };
  const moved = f !== st.focus && !(f && el && (VF.inside(f, el) || VF.inside(el, f)));
  let top = st.top;
  try { top = VF.all(":popover-open, dialog[open]").length; } catch {}
  // A popover opening changes no attribute: count the open ones.
  return { changed: st.n > 0 || moved || top !== st.top, url: location.href };"""
# Ends the counting; returns the text of the layers (dialogs, overlays) the
# click opened, for click() to say.
_DISARM_JS = r"""
  const k = Symbol.for("vf-click"), st = window[k];
  if (!st) return { opened: [] };
  st.obs.disconnect(); st.perf?.disconnect(); window.navigation?.removeEventListener("navigate", st.nav);
  delete window[k];
  const opened = VF.layers().filter((l) => !st.layers.includes(l)).map((l) => VF.norm(VF.visibleText(l)).slice(0, 160)).filter(Boolean);
  return { opened, checked: st.c?.isConnected ? VF.checked(st.c) : null };"""
# A script click, for a label on its control (React's radios), and inside a
# custom element on the button in its shadow root. SVG elements have no
# click(): they get the events a mouse click sends.
_JSCLICK_JS = r"""
  const el = window[Symbol.for("vf-el")];
  if (!el?.isConnected) return false;
  const t = el.localName === "label" && el.control ? el.control : el;
  const e = t.shadowRoot?.querySelector("button, a[href], input, [role=button]") ?? t;
  // In a task of its own, so that a confirm() it opens doesn't block this evaluation.
  if (typeof e.click === "function") { setTimeout(() => e.click(), 0); return true; }
  const r = e.getBoundingClientRect();
  const o = { bubbles: true, cancelable: true, composed: true, view: window, button: 0,
              clientX: r.x + r.width / 2, clientY: r.y + r.height / 2 };
  setTimeout(() => {
    for (const k of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"])
      e.dispatchEvent(k.startsWith("pointer") ? new PointerEvent(k, { ...o, pointerId: 1, isPrimary: true }) : new MouseEvent(k, o));
  }, 0);
  return true;"""
# close_dialog(): the top open layer (VF.layers: the one shown at its own
# centre, the last such) and the control that closes it, kept as the next
# click's element: an aria-label/title/text close control ("Schließen", "×"),
# an icon-only close button, else a decline ("Später", "Nein danke",
# "Überspringen"); never one whose label commits. {none} without a layer,
# {consent} for a cookie banner (dismiss_consent's job), {escape} when it has
# no such control. GONE: whether the layer kept in vf-layer is closed now.
_CLOSE_DIALOG_JS = r"""
  const LK = Symbol.for("vf-layer");
  if (GONE) { const l = window[LK]; return !l || !l.isConnected || !VF.shown(l) || !VF.layers().includes(l); }
  const L = VF.layers();
  if (!L.length) return { none: true };
  const onTop = L.filter((l) => {
    const r = l.getBoundingClientRect();
    const h = VF.hitAt(Math.min(Math.max(r.x + r.width / 2, 0), innerWidth - 1), Math.min(Math.max(r.y + r.height / 2, 0), innerHeight - 1));
    return h && VF.inside(h, l);
  });
  const layer = onTop.at(-1) ?? L.at(-1);
  window[LK] = layer;
  const text = VF.norm(VF.visibleText(layer)).slice(0, 120);
  // A cookie banner only when it speaks of cookies/consent (SE-08: Bergzeit's add-to-cart layer sat in a "consent" box).
  if (VF.bannerOf(layer, true) && /cookie|consent|einwillig|datenschutz|tracking|privacy/i.test((layer.innerText ?? "").slice(0, 5000)))
    return { consent: true, text };
  // A dialog that fills the viewport with nothing else on the page beside it
  // is the page (PayPal's connected-apps view, DD-02): closing it navigates away.
  const lr = layer.getBoundingClientRect();
  const area = Math.max(0, Math.min(lr.right, innerWidth) - Math.max(lr.left, 0)) * Math.max(0, Math.min(lr.bottom, innerHeight) - Math.max(lr.top, 0));
  const rest = VF.norm(document.body.innerText).length - VF.norm(layer.innerText).length;
  const backdrop = /rgba\([^)]*,\s*0?\.\d+\)$/.test(getComputedStyle(layer).backgroundColor);  // a dimmed page behind a modal
  if (area >= 0.85 * innerWidth * innerHeight && rest < 300 && !backdrop && !L.some((l) => l !== layer && !VF.inside(l, layer)))
    return { page: true, text };
  const COMMIT = /übernehmen|speichern|akzeptier|zustimm|bestätig|einverstanden|abonnier|kaufen|bestellen|buchen|anmelden|registrier|aktualisier|weiter|fortfahren|senden|absenden|annehmen|erlauben|zulassen|ja\b|accept|agree|allow|save|subscribe|confirm|continue|submit|buy|order|book|sign|update|yes\b|\bok\b/i;
  const CLOSE = /^(close|close dialog|close modal|close window|dismiss|×|✕|✖|╳|x|schließen|schliessen|dialog schließen|dialog schliessen|fenster schließen|fenster schliessen|overlay schließen|popup schließen|hinweis schließen|close \(icon\))$/i;
  const DECLINE = /^(später|später erinnern|vielleicht später|nein,? danke|nein danke|überspringen|nicht jetzt|jetzt nicht|no,? thanks|skip|maybe later|not now|later)$/i;
  const ctl = VF.all("button, [role=button], a, [aria-label], [title], [class*=close i], [onclick]")
    .filter((e) => VF.inside(e, layer) && VF.shown(e) && !e.disabled && VF.attr(e, "aria-disabled") !== "true");
  const name = (e) => VF.norm(VF.attr(e, "aria-label") || VF.attr(e, "title") || VF.label(e) || VF.iconName(e));
  const pick = (e, how) => { window[Symbol.for("vf-el")] = e; return { found: name(e).slice(0, 60) || e.localName, how, text }; };
  for (const e of ctl) { const n = name(e); if (CLOSE.test(n) && !COMMIT.test(n)) return pick(e, "close"); }
  for (const e of ctl) {
    const n = name(e);
    if (!COMMIT.test(n) && n.length < 40 && /schließen|schliessen|\bclose\b/i.test(n)) return pick(e, "close");
  }
  for (const e of ctl) if (!VF.norm(e.innerText) && VF.iconName(e) === "close (icon)") return pick(e, "close");
  for (const e of ctl) { const n = name(e); if (DECLINE.test(n)) return pick(e, "decline"); }
  return { escape: true, text };"""

# The page side of ShopTab.text: the text of SEL (null: the body) without
# the open dialogs, popovers, drawers and overlays in it (VF.layers); those
# as sections to put first (they are on top), the shown tab panel outside
# it as one to put after. {loading: true} while a new document has no body
# yet. FRAME: text()'s tag for the iframes it numbered (see VF.visibleText).
_TEXT_JS = r"""
  if (SEL === null && !document.body) return { loading: true };
  let root = SEL === null ? document.body : (VF.find({ css: SEL }).el ?? VF.all(SEL)[0] ?? null), head = "";
  if (SEL === "main" && (!root || !VF.visibleText(root).trim())) { root = document.body; head = "[no <main>: whole page]\n"; }
  if (!root) return null;
  const boxes = [];
  const add = (el, kind) => {
    if (VF.inside(root, el) || boxes.some((b) => VF.inside(el, b.el) || VF.inside(b.el, el))) return;
    boxes.push({ el, kind });
  };
  if (DIALOGS) {
    for (const e of VF.layers()) add(e, "dialog");
    for (const e of VF.all("[role=tabpanel]")) if (VF.shown(e) && !VF.inside(e, root)) add(e, "tabpanel");
  }
  const skip = new Set(boxes.map((b) => b.el));
  const part = (kind) => boxes.filter((b) => b.kind === kind).map((b) => VF.visibleText(b.el, null, FRAME, HIDDEN))
    .filter(Boolean).map((t) => `[${kind}] ${t}`);
  const net = window[Symbol.for("vf-net")], now = performance.now();
  const text = VF.visibleText(root, skip, FRAME, HIDDEN);
  const notes = [];
  if (SEL !== null && root !== document.body) {
    // Dialogs open outside the region (DE-03): one line each, so they aren't missed.
    for (const d of VF.all("dialog[open], [role=dialog], [role=alertdialog], [aria-modal=true]")) {
      if (VF.inside(d, root) || VF.inside(root, d) || boxes.some((b) => VF.inside(d, b.el)) || !VF.shown(d) || !VF.onScreen(d)) continue;
      const title = VF.norm(d.querySelector?.("h1, h2, h3, [role=heading]")?.innerText || VF.attr(d, "aria-label") || d.innerText).slice(0, 60);
      if (title) notes.push(`[dialog open: '${title}' — text() shows it]`);
    }
    const all = VF.norm(document.body.innerText).length, mine = VF.norm(text).length;
    const mainLike = root.localName === "main" || VF.attr(root, "role") === "main" || /^(main|content|page|app)$/i.test(root.id ?? "");
    if (mainLike && all - mine > 500 && mine < 0.3 * all) notes.push(`[${SEL} holds ${mine} chars of ${all} on the page: text() for all]`);
  }
  // B-57 (EMPTY: text() only): a heading whose section — everything after it in document order up to the next
  // heading of its level or above — has elements but shows nothing (no text, no image, no control): a widget
  // that loads when scrolled to (LIFE-0315 "Skigebiete in der Nähe"). A section holding hidden text (an
  // accordion, a tab panel) doesn't count (hidden=True / find() show it). The first such heading is scrolled
  // to, so that it may load.
  const HEADS = "h1, h2, h3, h4, [role=heading]", MEDIA = "img, svg, picture, video, canvas, iframe, object, embed, input, select, textarea, button, a[href], [role=img], [role=button]";
  const lvl = (h) => /^h[1-6]$/.test(h.localName) ? +h.localName[1] : +(VF.attr(h, "aria-level") || 2);
  const drawn = (e) => e.checkVisibility ? e.checkVisibility({ visibilityProperty: true }) : e.getClientRects().length > 0;
  const NOCOUNT = /^(script|style|link|meta|template|noscript)$/;
  const sectionEmpty = (h) => {  // true: drawn elements, nothing shown; false: something shown, any text, or can't tell
    const w = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    w.currentNode = h;
    let els = 0, seen = 0;
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      if (h.contains(n)) continue;
      if (++seen > 400) return false;
      if (n.nodeType === 3) {
        const p = n.parentElement;
        if (!n.data.trim() || !p || NOCOUNT.test(p.localName) || p.closest("script, style, template, noscript")) continue;
        return false;  // shown text: filled; hidden text: collapsed, not lazy
      }
      if ((n.matches(HEADS) && lvl(n) <= lvl(h)) || n.localName === "footer" || VF.attr(n, "role") === "contentinfo") break;
      if (NOCOUNT.test(n.localName)) continue;
      if (drawn(n)) els++;
      if ((n.matches(MEDIA) && !(n.localName === "input" && n.type === "hidden")) || (n.shadowRoot && VF.visibleText(n).trim())) return false;  // a lazy image is content too
    }
    return els > 0;
  };
  let empties = 0;
  for (const h of typeof EMPTY !== "undefined" && EMPTY ? [...root.querySelectorAll(HEADS)].slice(0, 150) : []) {
    if (empties >= 3) break;
    if (!VF.shown(h) || boxes.some((b) => VF.inside(h, b.el)) || h.closest("footer, nav, [role=contentinfo], [role=navigation]")) continue;
    if (h.matches("[aria-expanded], [aria-controls]") || h.querySelector("[aria-expanded], [aria-controls], a[href]") || h.closest("summary, a[href]")) continue;  // an accordion header, a teaser link
    const name = VF.norm(h.innerText);
    if (!name || name.length > 80 || !sectionEmpty(h)) continue;
    if (!empties) h.scrollIntoView({ block: "center" });
    empties++;
    notes.push(`[empty: '${name}' — its section shows nothing yet (lazy?)${empties === 1 ? ": scrolled to it" : ""}; await tab.idle(), then read again]`);
  }
  return { head, text, before: part("dialog"), after: part("tabpanel"), notes,
           inflight: typeof INFLIGHT === "function" ? INFLIGHT()  // text() adds it (_INFLIGHT_JS: no long-polls)
             : net ? [...net.live.values()].filter((t) => now - t < 15000).length : 0 };"""

# Where text() puts a frame's text: a line "\x01<n>\x01" from VF.visibleText.
_FRAME_MARK = _re.compile(r"\x01(\d+)\x01")


def _url_match(want, href):
    """wait_for's url=: a substring of `href` without its query and fragment
    (with them when `want` has "?" or "#": a "myaccount" in a challenge's
    returnUri query doesn't count, MO-01), else, when it looks like one, a
    regex on the whole URL (or a re.Pattern)."""
    if isinstance(want, _re.Pattern):
        return bool(want.search(href))
    target = href if ("?" in want or "#" in want) else _re.split(r"[?#]", href, maxsplit=1)[0]
    if want in target:
        return True
    if not _re.search(r"[\\^$*+\[\](){}|]", want):
        return False
    try:
        return bool(_re.search(want, href))
    except _re.error:
        return False


# eval(js, *args): `js` is a function expression (arrow or function).
_EVAL_FN = _re.compile(r"\s*(?:async\s+)?(?:function\b|\([^()]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)")

# One-time-link tokens in the URLs the tools show (B-02: after
# open_mail_link, url() and goto's messages held the magic-login token).
# The value of a query or fragment parameter with one of these names (any
# case; _ - . ignored) shows as •••, from 6 characters on (t, hash, sid and
# session from 16; pagination tokens and harmless codes stay); so does a
# path segment of ≥ 16 characters with letters and digits after /magic/,
# /token/, /verify/, /confirm/, /activate/, /reset/, /login/, /auth/,
# /otp/, /invite/ or /unlock/. The real URL stays in this kernel: goto() of
# a URL masked here (other parameters changed too) opens the real one. Real
# URLs that would mask alike (a list of /invoice?token=… links) get •••2,
# •••3, … from the second on, so each masked one opens its own.
_URL_SECRET_NAME = _re.compile(
    r"(?!(?:page|next|prev|continuation|cursor|sync|resume)token$)\w*token"
    r"|(?!(?:postal|zip|post|plz|country|lang|language|locale|currency|region|area|state|city|airport|iata|station|"
    r"product|article|item|sku|ean|category|cat|sort|search|status|error|response|http|color|colour|size|style|"
    r"tracking|shop|store|branch|promo|coupon|campaign|ref|referral|partner|source)code$)\w*code"
    r"|otp|(?:api|access|secret|auth|login|signin|session|private|magic|onetime|sign|license|activation)?key"
    r"|sig|\w*signature|\w*credential|auth|authorization|magic\w*|ticket|verify|verification|hmac|password|passwd|pwd"
    r"|t|hash|sid|session|\w*sessionid|phpsessid")
_URL_SECRET_LONG = _re.compile(r"t|hash|sid|session")  # masked from 16 characters on
_URL_PARAM = _re.compile(
    r"(?P<pre>[?&;#]|%3F|%26|%23)(?P<name>[A-Za-z][\w.\-\[\]]{0,40})(?P<eq>=|%3D)"
    r"(?P<value>(?:[^&#\s\"'<>%\])]|%(?!26|3D|3F|23)[0-9A-Fa-f]{2})+)", _re.I)
_URL_PATH_TOKEN = _re.compile(
    r"(?P<pre>/(?:magic(?:[-_]?link)?|tokens?|verify|verification|confirm|confirmation|activate|activation|reset|"
    r"password[-_]?reset|login|signin|sign[-_]in|auth|otp|invite|invitation|unlock|one[-_]?time)/)"
    r"(?P<value>[\w\-.~%]{16,})(?=[/?#\s\"'<>)\]]|$)", _re.I)
_URL_REAL = globals().get("_URL_REAL") or {}  # masked URL → the real one (the last 500), for goto()
_MASKED = "•••"


def _mask_one_url(u):
    """One URL-like chunk with its one-time-link tokens masked (see above);
    an http(s) URL masked here is kept for _unmask_url."""
    def param(m):
        n, v = _re.sub(r"[_.\-\[\]]", "", m["name"]).lower(), m["value"]
        if v.startswith(_MASKED) or not _URL_SECRET_NAME.fullmatch(n):
            return m[0]
        if len(v) < (16 if _URL_SECRET_LONG.fullmatch(n) else 6):
            return m[0]
        return m["pre"] + m["name"] + m["eq"] + _MASKED

    def path(m):
        v = m["value"]
        return m["pre"] + _MASKED if _re.search(r"\d", v) and _re.search(r"[A-Za-z]", v) else m[0]

    out = _URL_PATH_TOKEN.sub(path, _URL_PARAM.sub(param, u))
    if out != u and out.startswith(("http://", "https://")):
        key, n = out, 1
        while _URL_REAL.get(key, u) != u:  # another real URL masks alike: number this one
            n += 1
            key = out.replace(_MASKED, f"{_MASKED}{n}")
        out = key
        _URL_REAL.pop(out, None)
        _URL_REAL[out] = u
        while len(_URL_REAL) > 500:
            del _URL_REAL[next(iter(_URL_REAL))]
    return out


def _mask_url(s):
    """`s` (a URL, or text holding URLs) with one-time-link tokens as •••."""
    if not isinstance(s, str) or ("=" not in s and "/" not in s):
        return s
    return _re.sub(r"[^\s\"'<>]+", lambda m: _mask_one_url(m[0]) if "=" in m[0] or "/" in m[0] else m[0], s)


def _mask_urls(obj):
    """_mask_url on every string of a JSON value, at any depth (eval())."""
    if isinstance(obj, str):
        return _mask_url(obj)
    if isinstance(obj, list):
        return [_mask_urls(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _mask_urls(v) for k, v in obj.items()}
    return obj


def _unmask_url(url):
    """The real URL behind one this kernel masked: the same one, else one with
    the same scheme, host and path whose masked parameters get the real
    values of the latest such URL. ValueError when a ••• stays."""
    if not isinstance(url, str) or _MASKED not in url:
        return url
    if url in _URL_REAL:
        return _URL_REAL[url]
    base = _re.split(r"[?#]", url, maxsplit=1)[0]
    marks = lambda s: {m["name"]: m["value"] for m in _URL_PARAM.finditer(s) if m["value"].startswith(_MASKED)}  # noqa: E731
    want = marks(url)
    for masked, real in reversed(_URL_REAL.items()):
        if _re.split(r"[?#]", masked, maxsplit=1)[0] != base:
            continue
        have = marks(masked)
        if any(have.get(k, v) != v for k, v in want.items()):
            continue  # another real URL's numbered mask (•••2 …)
        values = {}
        for m in _URL_PARAM.finditer(real):
            values.setdefault(m["name"], m["value"])
        rbase = _re.split(r"[?#]", real, maxsplit=1)[0]
        out = rbase + url[len(base):]
        out = _URL_PARAM.sub(lambda m: m["pre"] + m["name"] + m["eq"] + values[m["name"]]
                             if m["value"].startswith(_MASKED) and m["name"] in values else m[0], out)
        if _MASKED not in out:
            return out
    raise ValueError(f"{url[:200]} holds a masked token (•••) this kernel has no real value for: open the link "
                     "itself (open_mail_link, a click), not a copy of its URL")


# count_proof(): the counts a page states of its own list, in the order
# they are trusted: (1) a range "21–40 of 345" / "1 - 20 von 345"; (2)
# "345 Treffer/Ergebnisse/results", "3 of 8 results", "Showing 20 of 345",
# "Keine Treffer/No results" (0); (3) "12 Bestellungen/orders/
# Transaktionen/…", "Insgesamt: 12", "keine Bestellungen" (0); (4) a heading
# count "Bestellungen (12)". Pagers ("Seite 1 von 5") come apart, as pages.
_CNT_NUM = r"(\d{1,3}(?:[.,\u202f\u00a0 ]\d{3})+(?![\d.,]\d)|\d+)"
_CNT_RESULT = r"(?:treffer[n]?|(?:such)?ergebnis(?:se|sen)?|(?:search )?results?|matches|hits)"
_CNT_LIST = (
    r"(?:bestellungen|orders?|artikel|items?|produkte?n?|products?|transaktionen|transactions?|umsätzen?|buchungen|"
    r"zahlungen|payments?|rechnungen|invoices?|belegen?|receipts?|dokumenten?|documents?|dateien|files?|einträgen?|"
    r"entries|angeboten?|offers?|anzeigen|listings?|inseraten?|positionen|records?|datensätzen?|zeilen|rows?|"
    r"elementen?|terminen?|appointments?|nachrichten|messages?|fahrten|trips?|reisen|tickets?|"
    r"verträgen?|contracts?|abonnements?|abos?|subscriptions?|geräten?|devices?|sendungen|shipments?|lieferungen|"
    r"deliveries|jobs?|stellen(?:angebote)?|objekten?|immobilien|properties|hotels?|unterkünften?|flügen?|flights?|"
    r"verbindungen|connections?|kontakten?|contacts?|mitglieder|members?|repositor(?:y|ies)|projekten?|projects?|"
    r"vorgängen?|fällen?|cases?|anfragen|requests?|aktivitäten|activities|charges?|abbuchungen|lastschriften|"
    r"gutschriften|statements?|kontoauszüge|mails?|e-mails?|emails?)")
_CNT_WORDS = rf"(?:{_CNT_RESULT}|{_CNT_LIST})"
_CNT_PER_PAGE = r"(?!\s+(?:pro|per|je)\s+(?:seite|page))"  # "9 Artikel pro Seite" is a setting
_CNT_PATTERNS = [  # (tier, regex, kind); group "n": the count, "a"/"b": a range shown
    (1, rf"(?<![\d.,/]){_CNT_NUM.replace('(', '(?P<a>', 1)}\s*(?:[-–—]|bis|to)\s*{_CNT_NUM.replace('(', '(?P<b>', 1)}"
        rf"\s*(?:of|von|aus|out of|\s/)\s*(?:insgesamt\s+|total\s+|über\s+|more than\s+)?{_CNT_NUM.replace('(', '(?P<n>', 1)}"
        r"(?![\d.,]?\d|/\d)", "range"),
    (2, rf"(?:showing|zeige|zeigt|angezeigt|anzeige|displaying|viewing)\s*:?\s*{_CNT_NUM.replace('(', '(?P<b>', 1)}"
        rf"\s+(?:of|von|aus)\s+{_CNT_NUM.replace('(', '(?P<n>', 1)}", "shown"),
    (2, rf"(?<![\d.,/]){_CNT_NUM.replace('(', '(?P<b>', 1)}\s+(?:of|von|aus)\s+{_CNT_NUM.replace('(', '(?P<n>', 1)}"
        rf"\s+{_CNT_WORDS}\b", "shown"),
    (2, rf"(?<![\d.,/]){_CNT_NUM.replace('(', '(?P<n>', 1)}\s*\+?\s*(?:passende[n]?\s+|gefundene[n]?\s+|matching\s+)?"
        rf"{_CNT_RESULT}\b{_CNT_PER_PAGE}", "count"),
    (2, rf"\b{_CNT_RESULT}\s*:?\s*\(?\s*{_CNT_NUM.replace('(', '(?P<n>', 1)}"
        r"(?![\d.,]\d|\s*[€$%]|\s*(?:[-–—]|bis|to|von|of)\s*\d)", "count"),
    (2, rf"\b(?:keine|kein|no|null)\s+(?:passenden\s+|weiteren\s+|matching\s+)?{_CNT_RESULT}\b|\bnothing found\b"
        r"|\bnichts gefunden\b", "zero"),
    (3, rf"(?<![\d.,/]){_CNT_NUM.replace('(', '(?P<n>', 1)}[^\S\n]*\+?[^\S\n]*(?:\w+e[nr]?[^\S\n]+)?{_CNT_LIST}\b"
        rf"{_CNT_PER_PAGE}", "count"),
    (3, rf"\b(?:insgesamt|gesamt|total|anzahl|gesamtanzahl)\s*:?\s*{_CNT_NUM.replace('(', '(?P<n>', 1)}"
        r"(?![\d.,]\d|\s*(?:[€$%£]|eur\b|usd\b|chf\b|kb\b|mb\b|gb\b|min\b|std\b|h\b|km\b|kg\b))", "count"),
    (3, rf"\b(?:keine|kein|no)\s+(?:weiteren\s+)?{_CNT_LIST}\b", "zero"),
    (4, rf"\b{_CNT_LIST}\s*\(\s*{_CNT_NUM.replace('(', '(?P<n>', 1)}\s*\)", "count"),
]
_CNT_PATTERNS = [(t, _re.compile(p, _re.I), k) for t, p, k in _CNT_PATTERNS]
_CNT_PAGES = _re.compile(rf"\b(?:seite|page|pagina)\s*{_CNT_NUM}\s*(?:of|von|aus|/)\s*{_CNT_NUM}\b", _re.I)
# Counts that aren't the list's: a basket's, a wish list's, a rating's.
_CNT_NOT = _re.compile(r"(warenkorb|einkaufswagen|cart|basket|merkliste|wunschliste|wish ?list|favorit\w*|sterne?n?|stars?|"
                       r"bewertet|rated|notification|benachrichtigung)\W{0,3}(\w+\W{1,3}){0,2}$", _re.I)
_CNT_APPROX = _re.compile(r"(über|mehr als|more than|over|about|around|ca\.?|circa|rund|etwa|approx\w*|~|>)\s*$", _re.I)


def _cnt_int(s):
    return int(_re.sub(r"\D", "", s))


def _count_candidates(text):
    """The counts `text` states of a list: [{n, tier, kind, evidence, pos,
    approx, shown?}] sorted by tier, then place; and the pages a pager
    states, (page, of) or None."""
    out, taken = [], []
    for tier, rx, kind in _CNT_PATTERNS:
        for m in rx.finditer(text):
            if any(s < m.end() and m.start() < e for s, e in taken):
                continue  # a stronger pattern read this place already
            before = text[max(0, m.start() - 40):m.start()]
            prev = text.rfind("\n", 0, max(0, text.rfind("\n", 0, m.start())))  # this line and the one before
            if _CNT_NOT.search(text[max(prev + 1, m.start() - 40):m.start()]):
                continue
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_end = text.find("\n", m.end())
            line = text[line_start:line_end if line_end >= 0 else len(text)].strip()
            ev = line if len(line) <= 120 else text[max(line_start, m.start() - 40):m.end() + 40].strip()
            c = {"n": 0 if kind == "zero" else _cnt_int(m["n"]), "tier": tier, "kind": kind, "evidence": ev,
                 "pos": m.start(), "approx": bool(_CNT_APPROX.search(before[-14:]))
                 or text[m.end():m.end() + 1] == "+" or "+" in m[0] or "über" in m[0].lower() or "more than" in m[0].lower(),
                 "key": _re.sub(r"[\W\d_]+", "", m[0].lower())}
            if kind in ("range", "shown"):
                a = _cnt_int(m["a"]) if kind == "range" else 1
                c["shown"] = max(0, _cnt_int(m["b"]) - a + 1)
            out.append(c)
            taken.append((m.start(), m.end()))
    # A list word counted in many places (each card's "12 items") is the rows', not the list's.
    seen = {}
    for c in out:
        if c["tier"] >= 3 and c["kind"] == "count":
            seen.setdefault(c["key"], set()).add(c["n"])
    out = [c for c in out if not (c["tier"] >= 3 and c["kind"] == "count" and len(seen[c["key"]]) >= 3)]
    out.sort(key=lambda c: (c["tier"], c["pos"]))
    pages = None
    for m in _CNT_PAGES.finditer(text):
        p, of = _cnt_int(m[1]), _cnt_int(m[2])
        if 0 < p <= of:
            pages = (p, of)
            break
    return out, pages


import socket as _socket  # noqa: E402  (goto's TCP probe)

# goto()'s www/apex twin (B-20): a host that takes no TCP connection within
# _CONNECT_WAIT s, or that Chrome can't reach (refused, no DNS), is tried
# once as its twin (www.crm.de ↔ crm.de), and goto() says so.
_NET_DEAD = ("ERR_CONNECTION_REFUSED", "ERR_CONNECTION_TIMED_OUT", "ERR_NAME_NOT_RESOLVED",
             "ERR_NAME_RESOLUTION_FAILED", "ERR_ADDRESS_UNREACHABLE", "ERR_CONNECTION_FAILED")
_CONNECT_WAIT = 8.0


def _url_twin(url):
    """`url` on its www/apex twin host (www.x.de ↔ x.de), or None: not
    http(s), an IP address, localhost or a deeper subdomain."""
    p = _urlparse.urlsplit(url)
    host = p.hostname or ""
    if p.scheme not in ("http", "https") or "." not in host or _re.fullmatch(r"[\d.]+|[\da-f:]+", host):
        return None
    if host.startswith("www."):
        twin = host[4:]
        if "." not in twin:
            return None
    elif host.count(".") == 1:
        twin = "www." + host
    else:
        return None
    return _urlparse.urlunsplit((p.scheme, twin + (f":{p.port}" if p.port else ""), p.path, p.query, p.fragment))


async def _tcp_probe(url, timeout=_CONNECT_WAIT, said=None):
    """(True, ip) when the host of `url` takes a TCP connection within
    `timeout` s (its addresses tried at once), else (False, why); `said`:
    the seconds the message names (default `timeout`)."""
    p = _urlparse.urlsplit(url)
    port = p.port or (443 if p.scheme == "https" else 80)
    loop = _asyncio.get_running_loop()
    deadline = loop.time() + timeout
    try:
        infos = await _asyncio.wait_for(loop.getaddrinfo(p.hostname, port, type=_socket.SOCK_STREAM), timeout)
    except (OSError, _asyncio.TimeoutError):
        return False, "no DNS address"
    ips = list(dict.fromkeys(i[4][0] for i in infos))[:4]

    async def one(ip):
        _, w = await _asyncio.open_connection(ip, port)
        w.close()
        return ip

    tasks = [_asyncio.ensure_future(one(ip)) for ip in ips]
    errs = []
    try:
        for fut in _asyncio.as_completed(tasks, timeout=max(0.1, deadline - loop.time())):
            try:
                return True, await fut
            except TimeoutError:  # as_completed's deadline (an OSError too), or the kernel's ETIMEDOUT
                break
            except OSError as e:
                errs.append(_os.strerror(e.errno) if e.errno else type(e).__name__)
    finally:
        for t in tasks:
            t.cancel()
    why = (errs[0].lower() if errs and len(errs) == len(ips)
           else f"no TCP connect within {said or timeout:g} s")
    return False, f"{why} ({', '.join(ips)})"


# wait_for() (B-19): a page it sits on instead of the one waited for (a
# login, a 2FA or "Konto schützen" step, a captcha), and the page's line
# closest to the text it waited for. Login words count only in the title
# or h1 (a sidebar's "Anmelden" box is no login page); the others in any
# heading.
_INTERSTITIAL_URL = _re.compile(
    r"(?:^|[/._-])(log-?in|sign-?in|sign_in|anmelden|anmeldung|einloggen|auth|authorize|authenticate|oauth2?|sso|saml|"
    r"checkpoint|challenge|captcha|verify|verification|verifizierung|2fa|mfa|otp|two-?factor|interstitial|"
    r"security-?check|account-?security|account-?protection|konto-?schuetzen|konto-?schutz|consent)(?=$|[/._-])", _re.I)
_INTERSTITIAL_HOST = _re.compile(r"^(login|signin|auth|sso|consent|identity|idp)\.", _re.I)
_INTERSTITIAL_TEXT = _re.compile(
    r"konto schützen|schützen sie ihr konto|(handy|mobil(funk|telefon)?|telefon)nummer (hinzufügen|bestätigen|angeben)|"
    r"add (a |your )?(mobile|phone)( number)?|protect your account|secure your account|verify (it'?s|that it'?s) you|"
    r"bestätigen sie,? dass sie es sind|identität (bestätigen|verifizieren)|zwei-?faktor|two-?(factor|step)|2-step|"
    r"bestätigungscode|verification code|sicherheitscode|enter (the )?code|"
    r"captcha|are you (a )?(human|robot)|kein roboter|security check|sicherheitsüberprüfung|access denied|"
    r"zugriff verweigert|session (expired|abgelaufen)|sitzung (ist )?abgelaufen", _re.I)
_INTERSTITIAL_LOGIN = _re.compile(r"\b(anmelden|anmeldung|einloggen|log ?in|sign ?in)\b", _re.I)
_INTERSTITIAL_DWELL = 8.0  # s on such a page, without a match, before wait_for gives up


def _interstitial(href, head, more=""):
    """Why the page at `href` with title/h1 `head` and other headings
    `more` looks like a step in between (login, 2FA, account protection,
    captcha), or None."""
    p = _urlparse.urlsplit(href or "")
    if _INTERSTITIAL_HOST.match(p.hostname or ""):
        return f"a {p.hostname.split('.')[0]} host"
    m = _INTERSTITIAL_URL.search(p.path)
    if m:
        return f"a {m[1].lower()} page"
    for h in (head, more):
        m = _INTERSTITIAL_TEXT.search(h or "")
        if m:
            return f"a page headed {m[0]!r}"
    if head and _INTERSTITIAL_LOGIN.search(head):
        return f"a page headed {head[:100]!r}"
    return None


def _closest_line(want, text):
    """The line of `text` closest to `want` (case-insensitive): (score
    0…1, line), or (0, None)."""
    w = want.lower().strip()
    best = (0.0, None)
    if not w:
        return best
    lines = list(dict.fromkeys(ln.strip() for ln in text.splitlines() if 1 < len(ln.strip()) <= 300))[:3000]
    for ln in lines:
        low = ln.lower()
        sm = _difflib.SequenceMatcher(None, w, low, autojunk=False)
        m = sm.find_longest_match(0, len(w), 0, len(low))
        part = m.size / len(w) * 0.9 if m.size >= min(4, len(w)) else 0.0
        if part <= best[0] and sm.real_quick_ratio() <= best[0]:
            continue
        score = max(sm.ratio(), part)
        if score > best[0]:
            best = (score, ln)
    return best


# nav_links() (B-16): every link of the site's header, navigation, footer
# and account/user menu, also those in collapsed menus. A scan returns
# {links: [{key, area, label, url, hidden}], toggles: [{i, label, area,
# hover}], hops: [{label, url}], skipped: [label]}; MARK numbers the menu
# toggles (data-vf-nav). B-58: a toggle or hop whose text, aria-label,
# title, href, id/class or test id says log out / delete / cancel is never
# touched (skipped names it); a toggle without aria-expanded/aria-haspopup
# (or <summary>) is only hovered (hover: true), never clicked.
_NAV_JS = r"""
  const ACCOUNT = /\b(mein(e|en)?\s+(konto|bereich|profil|daten|space|account|bestellungen)|my\s+(account|space|profile|orders|area)|kundenkonto|konto|account|profil|profile|anmelden|einloggen|log\s?in|sign\s?in|login|abmelden|log\s?out|sign\s?out|hallo|hello)\b/i;
  const ACCOUNT_ATTR = /account|konto|user|profile|profil|login|avatar|customer|kunde|my-?area|myarea|signin|member/i;
  const SKIP = /warenkorb|einkaufswagen|basket|\bcart\b|\bsuche\b|search|sprache|language|\bland\b|country|region|währung|currency|cookie|teilen|share|drucken|print/i;
  const DANGER = /log[\s_-]?(out|off)|sign[\s_-]?(out|off)|abmeld|ausloggen|auslog|session (beenden|end)|sitzung beenden|abbestellen|unsubscribe|deaktivier|deactivat|löschen|loeschen|\bdelete|entfernen|\bremove|kündig|kuendig|\bcancel|stornier|widerruf/i;
  const said = (e) => [VF.label(e), VF.norm(e.textContent).slice(0, 200), VF.attr(e, "title"), VF.attr(e, "href"), VF.attr(e, "value"),
    VF.attr(e, "name"), VF.attr(e, "data-testid"), VF.attr(e, "data-test"), VF.attr(e, "data-qa"), VF.attr(e, "data-action"),
    VF.attr(e, "onclick").slice(0, 200), cls(e)].join(" ");
  const HOP = /online.?shop|\bshop\b|portal|my space|mein bereich/i;
  const HOP_URL = /\bsso\b|saml|oauth|jwt|[?&](token|ticket)=/i;
  // App-store and deep-link hosts ("Jetzt in der App einlösen"): never a site of its own.
  const APP = /(^|\.)(onelink\.me|app\.link|adj\.st|adjust\.com|apps\.apple\.com|play\.google\.com|appgallery\.huawei\.com|page\.link)$/i;
  const cls = (n) => `${n.id ?? ""} ${typeof n.className === "string" ? n.className : VF.attr(n, "class")} ${VF.attr(n, "aria-label")}`;
  const site = (h) => h.split(".").slice(-2).join(".");
  const here = site(location.hostname);
  const LAYERS = VF.layers();
  const areaOf = (e, lab) => {
    let acc = false, foot = false, head = false, nav = false;
    for (let n = VF.up(e), i = 0; n && n !== document.body && i < 40; n = VF.up(n), i++) {
      const t = n.localName, role = VF.attr(n, "role"), k = cls(n);
      if (i < 8 && ACCOUNT_ATTR.test(k)) acc = true;
      if (t === "footer" || role === "contentinfo" || /(^|[\s_-])(site-?)?footer([\s_-]|$)/i.test(k)) foot = true;
      if (t === "header" || role === "banner" || /(^|[\s_-])(site-?|page-?|main-?)?header([\s_-]|$)/i.test(k)) head = true;
      if (t === "nav" || /^(navigation|menubar|menu)$/.test(role) || /(^|[\s_-])(main-?)?(nav|navigation|navbar|topbar|menu)([\s_-]|$)/i.test(k)) nav = true;
    }
    const layer = LAYERS.some((d) => VF.inside(e, d));
    if ((ACCOUNT.test(lab) && (head || nav || layer || foot)) || (acc && (head || nav || layer))) return "account";
    return foot ? "footer" : head ? "header" : nav ? "nav" : layer ? "menu" : null;
  };
  const links = [], seen = new Set();
  for (const e of VF.all("a[href], [role=menuitem], [role=link]")) {
    const href = e.hasAttribute("href") ? VF.attr(e, "href").trim() : "";
    const url = !href || /^(#|javascript:)/i.test(href) ? null : VF.absHref(e);
    if (!url && e.localName === "a" && VF.attr(e, "role") !== "menuitem") continue;
    const lab = (VF.label(e) || VF.norm(e.textContent) || VF.iconName(e) || "").slice(0, 80);
    if (!lab && !url) continue;
    const area = areaOf(e, lab);
    if (!area) continue;
    const key = `${lab.toLowerCase()}|${url ?? ""}`;
    if (seen.has(key)) continue;
    seen.add(key);
    links.push({ key, area, label: lab, url, hidden: !VF.shown(e) });
  }
  const toggles = [], skipped = [];
  if (MARK) {
    for (const e of VF.all("[data-vf-nav]")) e.removeAttribute("data-vf-nav");
    for (const e of VF.all("button, [role=button], summary, a, [aria-haspopup], [aria-expanded]")) {
      if (toggles.length >= 8 || !VF.shown(e)) continue;
      const lab = (VF.label(e) || VF.iconName(e) || "").slice(0, 60);
      if (SKIP.test(lab) || SKIP.test(cls(e))) continue;
      const area = areaOf(e, lab);
      if (!area || area === "footer") continue;
      const href = VF.attr(e, "href").trim();
      if (e.localName === "a" && href && !/^(#|javascript:)/i.test(href)) continue;  // a real link: never followed here
      if (e.localName === "button" && e.form && e.type === "submit") continue;  // would send its form
      const exp = VF.attr(e, "aria-expanded"), pop = VF.attr(e, "aria-haspopup");
      const marked = exp === "false" || (pop && pop !== "false" && exp !== "true") || (e.localName === "summary" && !e.parentElement?.open);
      const closed = marked || (area === "account" && exp !== "true");
      if (!closed) continue;
      if (DANGER.test(said(e))) {  // B-58: "Ausloggen" as an account-menu toggle logged a shared session out
        if (skipped.length < 8) skipped.push(lab || VF.norm(e.textContent).slice(0, 60) || e.localName);
        continue;
      }
      e.setAttribute("data-vf-nav", toggles.length);
      toggles.push({ i: toggles.length, label: lab, area, hover: !marked });
    }
  }
  const hops = [];
  for (const e of VF.all("a[href]")) {
    if (hops.length >= 5 || !VF.shown(e)) continue;
    const lab = (VF.label(e) || "").slice(0, 80), url = VF.absHref(e);
    let host = "";
    try { host = new URL(url).hostname; } catch { continue; }
    if (!/^https?:/.test(url) || SKIP.test(lab) || APP.test(host) || /\bapp\b/i.test(lab)) continue;
    if (FOLLOW ? lab.toLowerCase().includes(FOLLOW.toLowerCase()) : site(host) !== here && (HOP.test(lab) || HOP_URL.test(url))) {
      if (DANGER.test(said(e)) || DANGER.test(url)) { if (MARK && skipped.length < 8) skipped.push(lab || url.slice(0, 60)); continue; }
      hops.push({ label: lab, url });
    }
  }
  return { links: links.slice(0, 600), toggles, hops, skipped };"""

# Opens (OPEN: hover events, then with CLICK a click) or closes (a click when
# it says it is open, leave events, Escape) menu toggle I of _NAV_JS. A
# toggle that now says log out / delete (_NAV_JS's DANGER) is never clicked.
_NAV_TOGGLE_JS = r"""
  const e = VF.all(`[data-vf-nav="${I}"]`)[0];
  if (!e) return false;
  const DANGER = new RegExp(DANGER_RX, "i");
  const said = `${VF.label(e)} ${VF.norm(e.textContent).slice(0, 200)} ${VF.attr(e, "title")} ${VF.attr(e, "href")} ${VF.attr(e, "aria-label")}`;
  const ev = (type) => e.dispatchEvent(new (type.startsWith("pointer") ? PointerEvent : MouseEvent)(type, { bubbles: !/enter|leave/.test(type), composed: true }));
  if (OPEN) {
    for (const t of ["pointerover", "pointerenter", "mouseover", "mouseenter"]) ev(t);
    if (CLICK && !DANGER.test(said)) e.click();
  } else {
    if (!DANGER.test(said) && (VF.attr(e, "aria-expanded") === "true" || (e.localName === "summary" && e.parentElement?.open))) e.click();
    for (const t of ["pointerout", "pointerleave", "mouseout", "mouseleave"]) ev(t);
    (document.activeElement ?? document.body).dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", code: "Escape", bubbles: true }));
  }
  return true;"""
_NAV_DANGER_RX = _re.search(r'const DANGER = /(.*)/i;', _NAV_JS).group(1)  # one source for both scripts
_NAV_PRINTED = globals().get("_NAV_PRINTED") or {}  # host → number of links printed in full (per kernel)

# wait_for() (B-19): OPTS() is the text of the page's option lists that
# visibleText leaves out or misses: native <select> options, and rendered
# [role=option]/menu items and overlay panes (Angular CDK / nx-dropdown, MUI).
_OPTION_TEXT_JS = r"""const OPTS = () => [
  ...VF.all("select").filter(VF.shown).flatMap((s) => [...s.options].map((o) => o.text)),
  ...VF.all("[role=option], [role=menuitem], [role=menuitemradio], [role=menuitemcheckbox], [role=treeitem], mat-option, nx-dropdown-item, .cdk-overlay-pane")
    .filter((e) => e.getClientRects().length > 0).map((e) => `${e.innerText || e.textContent || ""} ${VF.attr(e, "aria-label")}`),
].join("\n");"""

# Pass 10 (B-33, B-34, B-49): a second recorder in every document of a shop
# tab (added by _setup after _NEWDOC_JS, so it wraps that one's proxies):
# - the fetch/XHR requests in flight with their endpoint (origin + path) and
#   whether bytes came: a request open ≥ 10 s without a response or bytes is
#   a long-poll, and its endpoint stays one (Dropbox, Google Keep cycle a new
#   long-poll every few seconds, B-49); _INFLIGHT_JS / _NETSTATE2_JS count
#   the others;
# - the bodies of JSON responses since the document started (≤ 80 bodies,
#   ≤ 2 MB each, ≤ 8 MB in all; NDJSON/streams left out) for tab.find();
# - the value a field had when the page called setCustomValidity("…") on it,
#   so fields() can drop a custom error the page left behind after the
#   value changed (docmorris, SHOP-0229).
# window[Symbol.for("vf-net2")] is set only once the wrappers are in place.
_NETLOG_JS = r"""(() => {
  const K = Symbol.for("vf-net2");
  if (window[K]) return;
  const st = { live: new Map(), n: 0, last: performance.now(), slow: new Set(), json: [], bytes: 0, cv: new WeakMap() };
  const key = (u) => { try { const x = new URL(u, location.href); return x.origin + x.pathname; } catch { return String(u).split(/[?#]/)[0]; } };
  const start = (u) => { const id = ++st.n; st.live.set(id, { t: performance.now(), key: key(u), got: false }); return id; };
  const end = (id) => {
    const r = st.live.get(id);
    if (!r) return;
    st.live.delete(id);
    if (!r.got && performance.now() - r.t >= 10000) st.slow.add(r.key);
    if (!st.slow.has(r.key)) st.last = performance.now();
  };
  // A cookie banner's vendor lists (OneTrust, Cookiebot, Usercentrics, …): noise for find().
  const CMP = /cookielaw\.org|onetrust\.com|cookiebot\.com|usercentrics\.eu|privacy-mgmt\.com|consentmanager\.net|didomi\.io|trustarc\.com|cookiefirst\.com|iubenda\.com/;
  const keep = (url, text) => {
    if (typeof text !== "string" || !text || text.length > 2e6 || CMP.test(String(url || ""))) return;
    st.json.push({ url: String(url || "").slice(0, 600), t: Date.now(), text });
    st.bytes += text.length;
    while (st.json.length > 80 || st.bytes > 8e6) st.bytes -= st.json.shift().text.length;
  };
  const JSONCT = (ct) => /json/i.test(ct) && !/ndjson|json-seq|stream/i.test(ct);
  try {
    if (typeof window.fetch === "function") window.fetch = new Proxy(window.fetch, { apply(t, self, args) {
      let u = "";
      try { const a = args[0]; u = typeof a === "string" ? a : a?.url ?? String(a); } catch {}
      const id = start(u);
      let p;
      try { p = Reflect.apply(t, self, args); } catch (e) { end(id); throw e; }
      try {
        p.then((r) => {
          const rec = st.live.get(id);
          if (rec) rec.got = true;
          end(id);
          try {
            const ct = r.headers.get("content-type") || "", len = Number(r.headers.get("content-length") || 0);
            if (JSONCT(ct) && len <= 2e6 && r.type !== "opaque") r.clone().text().then((x) => keep(r.url || u, x), () => {});
          } catch {}
        }, () => end(id));
      } catch {}
      return p;
    } });
    const urls = new WeakMap();
    const open = XMLHttpRequest.prototype.open, send = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = new Proxy(open, { apply(t, self, args) {
      try { urls.set(self, String(args[1])); } catch {}
      return Reflect.apply(t, self, args);
    } });
    XMLHttpRequest.prototype.send = new Proxy(send, { apply(t, self, args) {
      const id = start(urls.get(self) ?? "");
      try {
        self.addEventListener("progress", () => { const r = st.live.get(id); if (r) r.got = true; });
        self.addEventListener("loadend", () => {
          end(id);
          try {
            const rt = self.responseType, ct = self.getResponseHeader("content-type") || "", url = self.responseURL || urls.get(self);
            if (rt === "json") { if (self.response != null) keep(url, JSON.stringify(self.response)); }
            else if ((rt === "" || rt === "text") && (JSONCT(ct) || /^\s*[\[{]/.test(self.responseText.slice(0, 20)))) keep(url, self.responseText);
          } catch {}
        }, { once: true });
      } catch {}
      try { return Reflect.apply(t, self, args); } catch (e) { end(id); throw e; }
    } });
    for (const C of [window.HTMLInputElement, window.HTMLSelectElement, window.HTMLTextAreaElement]) {
      const f = C?.prototype?.setCustomValidity;
      if (typeof f === "function") C.prototype.setCustomValidity = new Proxy(f, { apply(t, self, args) {
        try { if (args[0]) st.cv.set(self, self.value); else st.cv.delete(self); } catch {}
        return Reflect.apply(t, self, args);
      } });
    }
    window[K] = st;
  } catch {}
})()"""
# INFLIGHT(): the page's fetch/XHR requests in flight that a reader should
# wait for: not long-polls (see _NETLOG_JS), and a request with bytes coming
# for ≤ 60 s; without the recorder, vf-net's count (≤ 15 s old).
_INFLIGHT_JS = r"""const INFLIGHT = () => {
  const s2 = window[Symbol.for("vf-net2")], now = performance.now();
  if (s2) {
    let n = 0;
    for (const r of s2.live.values()) {
      if (!r.got && now - r.t >= 10000) s2.slow.add(r.key);
      if (!s2.slow.has(r.key) && (now - r.t < 10000 || (r.got && now - r.t < 60000))) n++;
    }
    return n;
  }
  const net = window[Symbol.for("vf-net")];
  return net ? [...net.live.values()].filter((t) => now - t < 15000).length : 0;
};"""
# [requests in flight (INFLIGHT), ms since the last non-long-poll one ended];
# null without either recorder (idle(), _quiet_reads).
_NETSTATE2_JS = "(() => { " + _INFLIGHT_JS + r"""
  const s2 = window[Symbol.for("vf-net2")], st = window[Symbol.for("vf-net")];
  if (!s2 && !st) return null;
  return [INFLIGHT(), performance.now() - (s2 ?? st).last]; })()"""

# B-71 / B-88: what the page shows besides its header, nav and footer (its
# core: the body without the outermost header/nav/footer/[role=banner|
# navigation|contentinfo]; a header/footer inside main/article/section is the
# content's own). thin: a page with a header/nav/footer whose core (+ the
# text of the largest same-origin iframe over ≥ 25 % of the viewport) is
# under 300 chars, or <main> empty while the core is under 1500, or that
# iframe empty. gate: the first _GATE_RX phrase in the core (_GATE_WEAK_RX
# in a short one). sig changes when the core, <main> or the iframe grow.
# net/res: ms since the last fetch/XHR / resource ended; age: the document's.
_CORE_JS = r"""
  const b = document.body;
  if (!b) return null;
  const CH = "header, nav, footer, [role=banner], [role=navigation], [role=contentinfo]";
  const SECT = "main, article, aside, section, [role=main], [role=article]";
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  const drawn = (e) => e.checkVisibility ? e.checkVisibility() : e.getClientRects().length > 0;
  const chrome = [...document.querySelectorAll(CH)].filter((e) => drawn(e) && !e.parentElement?.closest(CH)
    && !(/^(header|footer)$/.test(e.localName) && e.parentElement?.closest(SECT)));
  const ct = chrome.map((e) => norm(e.innerText)).filter(Boolean);
  let core = norm(b.innerText);
  for (const t of ct) core = core.replace(t, " ");
  if (core.length < 2000) core = norm(VF.visibleText(b, new Set(chrome)));  // open shadow roots too
  const m = document.querySelector("main, [role=main]");
  const main = m && core.length < 1500 ? norm(VF.visibleText(m)).length : null;
  let frame = null, fw = null, state = document.readyState;
  const area = innerWidth * innerHeight;
  for (const f of document.querySelectorAll("iframe")) {
    const r = f.getBoundingClientRect(), a = r.width * r.height;
    if (a < 0.25 * area || (frame && a <= frame.a)) continue;
    let d = null;
    try { d = f.contentDocument; } catch {}
    if (d) { frame = { a, n: norm(d.body?.innerText).length }; fw = f.contentWindow; if (state === "complete") state = d.readyState; }  // cross-site frames: text() reads them itself
  }
  const fn = frame ? frame.n : 0;
  const thin = (ct.length > 0 && core.length + fn < 300) || (main === 0 && core.length < 1500) || (frame !== null && frame.n === 0);
  const g = new RegExp(GATE, "iu").exec(core.slice(0, 50000)) ?? (core.length < 1500 ? new RegExp(WEAK, "iu").exec(core) : null);
  const since = (w) => {  // [fetch/XHR in flight (≤ 10 s old), ms since one ended, ms since a resource ended] of window w
    const s = w[Symbol.for("vf-net2")] ?? w[Symbol.for("vf-net")], now = w.performance.now();
    const res = w.performance.getEntriesByType("resource").reduce((x, e) => Math.max(x, e.responseEnd), 0);
    if (!s) return [0, 1e9, now - res];
    return [[...s.live.values()].filter((x) => now - (x.t ?? x) < 10000).length, now - s.last, now - res];
  };
  const top = since(window);
  let inflight = INFLIGHT(), net = top[1], res = top[2];
  if (fw) try { const f = since(fw); inflight += f[0]; net = Math.min(net, f[1]); res = Math.min(res, f[2]); } catch {}
  return { thin, core: core.length, main, frame: frame && frame.n, chrome: ct.length, gate: g ? g[0] : null,
           sig: `${core.length}|${main}|${fn}`, inflight, net, res, age: performance.now(), state,
           title: document.title, href: location.href };"""
# B-88: a gate over the content (account check, location or consent wall,
# access denied): its data may already be loaded. JS regex sources (flags
# iu). _GATE_RX counts anywhere in the core; _GATE_WEAK_RX (words long pages
# use for other things: sign in, upgrade, "nicht verfügbar") only in a core
# under 1500 chars.
_GATE_RX = (
    r"(?<!\p{L})(?:überprüfen sie ihr konto|bestätigen sie ihr konto|verify your account|verify (?:it'?s|that it'?s) you|"
    r"standortdienste|standort(?:freigabe|zugriff|ermittlung)|location services|location access|access denied|"
    r"zugriff verweigert|(?:melden sie sich|loggen sie sich) an,? um|(?:sign|log) ?in to (?:see|view|continue|access|read)|"
    r"upgrade to (?:see|view|unlock|access|read))(?!\p{L})")
_GATE_WEAK_RX = (
    r"(?<!\p{L})(?:anmelden|melden sie sich an|einloggen|sign ?in|log ?in|upgrade|consent|einwilligung|zustimmen|"
    r"nicht verfügbar|not available|unavailable)(?!\p{L})")
# B-88: the JSON the page holds that its text doesn't show: the recorder's
# response bodies (_NETLOG_JS) and the page's JSON scripts, without i18n,
# consent, telemetry and flag noise. TERMS: the cell's words (lower case);
# bodies holding one come first, then responses before scripts, the newest
# first. ≤ 5 items, one per label: its URL's last path segment (an id-like
# one as "•" after its parent; an action/operation query value added), and
# ≤ 8 key paths (last 2 keys) from its first non-wrapper level down: keys
# matching TERMS, then booleans, then the rest breadth-first; arrays by their
# first item; data-like keys (emails, numbers, hashes) as •. No values.
_DATA_JS = r"""
  const st = window[Symbol.for("vf-net2")];
  const NOISE = /i18n|translat|locali[sz]|\blocales?\b|\blang(uages?)?\b|feature|\bflags?\b|experiment|telemetry|analytics|collect|beacon|metrics|track|consent|cookie|\/logs?\b|logging|sentry|datadog|newrelic|optimizely|launchdarkly|captcha/i;
  const ID = /^(\d+|[0-9a-f]{8}-[0-9a-f-]{20,}|[0-9a-f]{16,}|(?=[\w-]*\d)(?=[\w-]*[A-Za-z])[\w-]{20,})$/i;
  const label = (u) => {
    let x;
    try { x = new URL(u, location.href); } catch { return "?"; }
    const seg = x.pathname.split("/").filter(Boolean);
    let s = seg.pop() || x.hostname;
    if (ID.test(s)) s = (seg.length && !ID.test(seg[seg.length - 1]) ? seg[seg.length - 1] + "/" : "") + "•";
    const a = [...x.searchParams].find(([k]) => /^(action|method|op|operation|operationname|fn|endpoint|query_?name|service)$/i.test(k));
    if (a && a[1] && !ID.test(a[1])) s += "?" + a[0] + "=" + a[1].slice(0, 40);
    return s.slice(0, 70);
  };
  const KEYX = /.@.|\d{5,}|^[0-9a-f-]{16,}$|^.{41,}$/i;
  const obj = (x) => x !== null && typeof x === "object";
  const keys = (v) => {
    while (obj(v)) {  // wrappers: an array (its first item), an object with one object key
      if (Array.isArray(v)) { v = v[0]; continue; }
      const ks = Object.keys(v);
      if (ks.length === 1 && obj(v[ks[0]])) v = v[ks[0]]; else break;
    }
    if (!obj(v)) return [];
    const out = [], q = [[v, []]];
    while (q.length && out.length < 80) {
      const [o, p] = q.shift();
      for (const k of Object.keys(o).slice(0, 50)) {
        const x = o[k], pk = [...p, KEYX.test(k) ? "•" : k];
        const sub = Array.isArray(x) ? (obj(x[0]) ? x[0] : null) : obj(x) ? x : null;
        if (sub && pk.length < 4) q.push([sub, pk]); else out.push({ p: pk, b: typeof x === "boolean" });
      }
    }
    const tm = (e) => TERMS.some((t) => e.p.some((k) => k.toLowerCase().includes(t)));
    return out.map((e, i) => [(tm(e) ? 0 : 2) + (e.b ? 0 : 1), i, e]).sort((a, c) => a[0] - c[0] || a[1] - c[1])
      .slice(0, 8).map(([, , e]) => e.p.slice(-2).join("."));
  };
  const bodies = [];
  for (const j of st?.json ?? []) if (j.text.length >= 20 && !NOISE.test(j.url)) bodies.push({ lab: label(j.url), text: j.text, t: j.t, s: 0 });
  for (const e of document.querySelectorAll('script[type="application/json"], script[type="application/ld+json"]')) {
    const x = e.textContent || "";
    if (x.length >= 20 && x.length <= 2e6 && !NOISE.test(e.id || ""))
      bodies.push({ lab: e.id ? "#" + e.id : e.type.includes("ld+") ? "ld+json" : "script", text: x, t: 0, s: 1 });
  }
  for (const e of bodies) e.hit = TERMS.length > 0 && TERMS.some((t) => e.text.toLowerCase().includes(t)) ? 1 : 0;
  bodies.sort((a, c) => (c.hit - a.hit) || (a.s - c.s) || (c.t - a.t));
  const items = [], seen = new Set();
  for (const e of bodies) {
    if (items.length >= 5) break;
    if (seen.has(e.lab)) continue;
    let v;
    try { v = JSON.parse(e.text); } catch { continue; }
    const k = keys(v);
    if (!k.length) continue;
    seen.add(e.lab);
    items.push({ lab: e.lab, keys: k });
  }
  return { n: bodies.filter((e) => !e.s).length, m: bodies.filter((e) => e.s).length, items };"""
# text()'s title line (B-88): generic title words that don't count as missing.
_TITLE_STOP = frozenset((
    "und and the der die das des den dem ein eine einen for für mit with von from bei auf zum zur ihr ihre your our "
    "unser unsere home startseite offizielle official site website webseite online shop store kaufen buy günstig "
    "cheap bestellen page seite willkommen welcome www com net org gmbh inc ltd").split())


def _title_line(title, text, href):
    """B-88: "[title: …]" when document.title has a word (4+ letters,
    not a generic one or part of the host name) or a [bracketed] /
    (parenthesised) part that `text` lacks (a grade only the title gives:
    "… im Waizmann-Test [sehr gut]"), else None."""
    t = _re.sub(r"\s+", " ", title or "").strip()
    if not t:
        return None
    flat, host = _re.sub(r"\s+", " ", text).lower(), (_urlparse.urlsplit(href or "").hostname or "").lower()
    words = [w for w in _re.findall(r"[^\W\d_]{4,}", t.lower()) if w not in flat and w not in host and w not in _TITLE_STOP]
    parts = [p for p in _re.findall(r"[\[(]([^\[\]()]{3,80})[\])]", t.lower()) if p.strip() not in flat]
    return f"[title: {t[:200]}]" if words or parts else None


def _data_line(d):
    """B-88: text()'s "[data: …]" line from a _DATA_JS result, or None;
    items after the first stop once the line would pass ~600 chars."""
    if not d or not d.get("items"):
        return None
    n, m = d["n"], d["m"]
    src = " + ".join(x for x in (f"{n} JSON response{'' if n == 1 else 's'} since load" if n else "",
                                 f"{m} JSON script{'' if m == 1 else 's'} in the page" if m else "") if x)
    items = []
    for it in d["items"]:
        s = f"{it['lab']}{{{','.join(it['keys'])}}}"
        if items and sum(len(x) + 3 for x in items) + len(s) > 520:
            break
        items.append(s if len(s) <= 400 else s[:399] + "…}")
    return f"[data: {src}: {' · '.join(items)} — tab.find('<term>') reads them]"


# tab.find() (B-33): where the page holds a term besides its visible text.
# TERMS: strings (RX: regex sources, case-insensitive); MAXH: hits kept per
# term and kind; JSONX: also script JSON, embedded state, the recorder's XHR
# bodies. Kinds: visible (a line of the visible text), hidden (collapsed /
# display:none / closed <details> / aria-hidden text, with the label of the
# accordion, tab or section that hides it), option (a <select>'s option),
# attr (aria-label/alt/title/placeholder/value/content/href/data-*, never
# password fields or vault-filled ones, never live field values), json
# (<script type=…json>, __NEXT_DATA__, <noscript>, window.__NUXT__ & co),
# xhr (a recorded response body): {key, path, preview} for JSON hits.
# I18N (text()'s hints, B-66): leaves out JSON under translation keys
# (i18n, initialI18nStore, translations, locale, …): site-wide UI strings.
_FIND_JS = r"""
  const bad = [];
  const rxs = TERMS.map((t) => { if (!RX) return null; try { return new RegExp(t, "i"); } catch (e) { bad.push(String(e)); return /(?!)/; } });
  const low = TERMS.map((t) => t.toLowerCase());
  const at = (s, i) => {
    if (typeof s !== "string" || !s) return null;
    if (RX) { const m = rxs[i].exec(s); return m ? [m.index, Math.max(1, m[0].length)] : null; }
    const k = s.toLowerCase().indexOf(low[i]);
    return k < 0 ? null : [k, low[i].length];
  };
  const snip = (s, m) => {
    const a = Math.max(0, m[0] - 70), b = Math.min(s.length, m[0] + m[1] + 90);
    return (a ? "…" : "") + s.slice(a, b).trim() + (b < s.length ? "…" : "");
  };
  const hits = [], total = {};
  const add = (i, h) => {
    const k = `${i} ${h.kind}`;
    total[k] = (total[k] ?? 0) + 1;
    if (total[k] <= MAXH) hits.push({ term: i, ...h });
  };
  const cls = (e) => typeof e.className === "string" ? e.className : VF.attr(e, "class");
  // visible text, line by line
  const lines = VF.visibleText(document.body).split("\n");
  for (const l of lines) for (let i = 0; i < TERMS.length; i++) { const m = at(l, i); if (m) add(i, { kind: "visible", snippet: snip(l, m) }); }
  // hidden text: text nodes the page doesn't render, with what hides them
  const rendered = (e) => e.checkVisibility ? e.checkVisibility({ opacityProperty: true, visibilityProperty: true }) : VF.shown(e);
  const collapsed = (e) => {
    for (let n = e, i = 0; n && n !== document.body && i < 30; n = VF.up(n), i++) {
      if (VF.attr(n, "aria-hidden") === "true" || n.hasAttribute?.("hidden")) return n;
      const r = n.getBoundingClientRect();
      if ((r.width <= 1 || r.height <= 1) && getComputedStyle(n).overflow !== "visible") return n;
    }
    return null;
  };
  const outerHidden = (e) => {
    let h = e;
    for (let n = VF.up(e), i = 0; n && n !== document.body && i < 30; n = VF.up(n), i++) if (!rendered(n) || collapsed(n) === n) h = n;
    return h;
  };
  const labelOf = (h) => {
    for (let e = h, i = 0; e && e !== document.body && i < 8; e = VF.up(e), i++) {
      if (e.localName === "details") { const s = [...e.children].find((c) => c.localName === "summary"); if (s) return VF.norm(s.textContent).slice(0, 80); }
      if (e.id) {
        const id = CSS.escape(e.id);
        const c = VF.all(`[aria-controls~="${id}"], [href="#${id}"], [data-target="#${id}"], [data-bs-target="#${id}"]`)[0];
        if (c && VF.norm(VF.label(c))) return VF.norm(VF.label(c)).slice(0, 80);
      }
      const lb = VF.attr(e, "aria-labelledby");
      if (lb) { const t = VF.norm(lb.split(/\s+/).map((x) => e.getRootNode().getElementById?.(x)?.textContent ?? "").join(" ")); if (t) return t.slice(0, 80); }
      if (VF.attr(e, "aria-label") && !/^(input|select|textarea|button)$/.test(e.localName)) return VF.norm(VF.attr(e, "aria-label")).slice(0, 80);
      const p = e.previousElementSibling;
      if (p && rendered(p)) { const t = VF.norm(p.innerText); if (t && t.length <= 120) return t.slice(0, 80); }
    }
    return "";
  };
  const roots = [document, ...VF.elements().filter((e) => e.shadowRoot instanceof ShadowRoot).map((e) => e.shadowRoot)];
  const NOTEXT = /^(script|style|noscript|template|textarea|title|head)$/;
  let seenText = 0;
  const shownSnips = new Set();
  for (const root of roots) {
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n && seenText < 2000; n = w.nextNode()) {
      const d = n.data;
      if (!d || d.length < 2) continue;
      const want = TERMS.map((_, i) => at(d, i) ? i : -1).filter((i) => i >= 0);
      if (!want.length) continue;
      const p = n.parentElement;
      if (!p || NOTEXT.test(p.localName) || p.closest("head, script, style, noscript, template, textarea")) continue;
      seenText++;
      if (p.localName === "option" || p.localName === "optgroup") {
        const sel = p.closest("select");
        for (const i of want) add(i, { kind: "option", label: sel ? VF.norm(VF.label(sel)).slice(0, 80) : "", snippet: VF.norm(d).slice(0, 160) });
        continue;
      }
      if (rendered(p) && !collapsed(p)) continue;  // shown: a visible line has it
      const h = outerHidden(p), lab = labelOf(h);
      let ctx = p;
      for (let i = 0; i < 3 && VF.norm(ctx.textContent).length < 60 && ctx.parentElement && ctx.parentElement !== h.parentElement; i++) ctx = ctx.parentElement;
      const s = VF.norm(ctx.textContent);
      for (const i of want) {
        const m = at(s, i);
        const sn = m ? snip(s, m) : VF.norm(d).slice(0, 160);
        if (shownSnips.has(i + sn)) continue;
        shownSnips.add(i + sn);
        add(i, { kind: "hidden", label: lab, snippet: sn });
      }
    }
  }
  // attributes
  const ATTR = /^(aria-label|aria-description|aria-valuetext|alt|title|placeholder|value|content|label|href|datetime|data-(?!vf).+)$/;
  for (const e of VF.elements()) {
    if (e.hasAttribute("data-vf-vault") || (e.localName === "input" && e.type === "password")) continue;
    for (const a of e.attributes) {
      if (!ATTR.test(a.name) || !a.value) continue;
      for (let i = 0; i < TERMS.length; i++) {
        const m = at(a.value, i);
        if (!m) continue;
        add(i, { kind: "attr", tag: e.localName, id: (e.id || "").slice(0, 60), attr: a.name,
                 key: a.name === "value" && VF.attr(e, "name") ? VF.attr(e, "name") : a.name,
                 full: a.value.slice(0, 500), snippet: snip(a.value, m) });
      }
    }
  }
  // JSON: page scripts, embedded state, recorded XHR bodies
  let budget = 400000, nsrc = 0, nxhr = 0;
  const pathOf = (p, k) => /^[A-Za-z_$][\w$]*$/.test(k) ? (p ? `${p}.${k}` : k) : `${p}[${JSON.stringify(k)}]`;
  const preview = (x) => { try { const s = typeof x === "string" ? x : JSON.stringify(x); return (s ?? String(x)).slice(0, 300); } catch { return String(x).slice(0, 300); } };
  const walk = (v, path, key, kind, src, depth, seen) => {
    if (budget-- <= 0 || depth > 40) return;
    if (v && typeof v === "object") {
      if (seen.has(v)) return;
      seen.add(v);
      if (Array.isArray(v)) { for (let j = 0; j < v.length && budget > 0; j++) walk(v[j], `${path}[${j}]`, key, kind, src, depth + 1, seen); return; }
      let ks;
      try { ks = Object.keys(v); } catch { return; }
      for (const k of ks) {
        if (typeof I18N !== "undefined" && I18N && /^(i18n|i18nStore|initialI18nStore|translations?|locales?|localeData|localeMessages)$/i.test(k)) continue;
        let x;
        try { x = v[k]; } catch { continue; }
        if (typeof x === "function") continue;
        const p = pathOf(path, k);
        for (let i = 0; i < TERMS.length; i++) if (at(k, i)) add(i, { kind, src, path: p, key: k, full: preview(x), keyhit: true });
        walk(x, p, k, kind, src, depth + 1, seen);
      }
      return;
    }
    if (typeof v === "string" || typeof v === "number") {
      const s = VF.norm(String(v));
      for (let i = 0; i < TERMS.length; i++) { const m = at(s, i); if (m) add(i, { kind, src, path, key, full: s.slice(0, 500), snippet: snip(s, m) }); }
    }
  };
  const raw = (text, kind, src) => {
    const s = VF.norm(text.replace(/<[^>]+>/g, " "));
    for (let i = 0; i < TERMS.length; i++) { const m = at(s, i); if (m) add(i, { kind, src, path: "(text)", key: "", full: "", snippet: snip(s, m) }); }
  };
  const parse = (s) => { try { return JSON.parse(s); } catch { return undefined; } };
  if (JSONX) {
    const seenSrc = new Set();
    for (const s of VF.all("script, noscript")) {
      const ty = VF.attr(s, "type").toLowerCase();
      if (s.localName === "script" && !/json/.test(ty) && !/^__(NEXT|NUXT)_DATA__$/.test(s.id)) continue;
      const text = s.textContent || "";
      if (!text.trim() || text.length > 5e6) continue;
      const src = s.id ? `${s.localName}#${s.id}` : s.localName === "noscript" ? "noscript" : `script[type=${ty}]`;
      nsrc++;
      const v = parse(text.trim());
      if (v === undefined) raw(text, "json", src);
      else walk(v, "", "", "json", src, 0, new WeakSet());
      if (s.id) seenSrc.add(s.id);
    }
    for (const g of ["__NEXT_DATA__", "__NUXT__", "__APOLLO_STATE__", "__INITIAL_STATE__", "__PRELOADED_STATE__", "__remixContext"]) {
      let v;
      try { v = window[g]; } catch { continue; }
      if (!v || typeof v !== "object" || seenSrc.has(g)) continue;
      nsrc++;
      walk(v, "", "", "json", `window.${g}`, 0, new WeakSet());
    }
    for (const b of window[Symbol.for("vf-net2")]?.json ?? []) {
      nxhr++;
      const v = parse(b.text);
      if (v === undefined) raw(b.text, "xhr", b.url);
      else walk(v, "", "", "xhr", b.url, 0, new WeakSet());
    }
  }
  return { hits, total, bad, nsrc, nxhr, recorder: !!window[Symbol.for("vf-net2")], href: location.href, title: document.title };"""


# wait_for() / goto(wait=) (B-38): error texts a site shows instead of the
# page (Vodafone "Das hat leider nicht geklappt", Amazon "Tut uns Leid",
# camping.info "Ein Fehler ist aufgetreten", 404 pages): a line of ≤ 200
# chars of the visible text, or the title, holding one, new since the wait
# began, seen twice ≥ 0.8 s apart, ends the wait with SiteError. A JS regex
# source too (no \b by ä).
_SITE_ERROR_RX = (
    r"nicht geklappt|tut uns leid|technische[rn]? (fehler|probleme?|störung)|ein fehler ist aufgetreten"
    r"|es ist ein fehler aufgetreten|ist (leider )?etwas schief ?gelaufen|etwas ist schief ?gelaufen|hoppla"
    r"|something went wrong|an error (has )?occurred|service (temporarily )?unavailable|bad gateway"
    r"|gateway time-?out|internal server error|seite (wurde )?nicht gefunden|page not found|server error"
    r"|not a functioning page|diese seite (existiert|gibt es) (leider )?nicht"
    r"|(error|fehler|http)[ :-]*(404|50[234])\b|\b(404|50[234])[ :-]+(not found|nicht gefunden|bad gateway|service|gateway|error|fehler)"
    r"|(bitte )?versuchen sie es (später|in kürze) (erneut|noch( ein)?mal)|please try again later")


class SiteError(TimeoutError):
    """The page shows a site error (wait_for, goto(wait=)) that wasn't there
    when the wait began. A TimeoutError, so `except TimeoutError` still
    catches it; .url: the page's URL (masked)."""

    def __init__(self, message, url=None):
        super().__init__(message)
        self.url = url


class SerialGoto(RuntimeError):
    """B-77: the 4th goto() (or search submit) on one tab within one cell,
    raised before it navigates: shop_map(urls, fn, concurrency=3) reads
    pages in parallel; serial=True when they must load in order."""


# The elapsed-time readout (B-36): every tab call of a cell, once that
# cell, prints "[task 2.4 of 8 min]" when the task started ≥ 1 min ago.
# The start: vf_task_start(id), else the T0 the TASK-START marker cell sets
# (`import time; T0 = time.time(); print("TASK-START <ID>", T0)`); the later
# of the two counts. vf_task_end(id) ends it. The cap: $VF_TASK_CAP_MIN (8).
_TASK = globals().get("_TASK") or {"said": None, "ended": 0.0}


def vf_task_start(task_id):
    """TASK-START marker of `task_id` (printed as the transcript tools read
    it) and the start of the elapsed-time readout on every tab call."""
    t0 = _time.time()
    _os.environ["VF_TASK_T0"] = repr(t0)
    globals()["T0"] = t0
    _TASK.update(said=None, ended=0.0)
    print(f"TASK-START {task_id} {int(t0)}")


def vf_task_end(task_id):
    """TASK-END marker of `task_id` with its minutes; the readout stops."""
    t1, t0 = _time.time(), _task_t0()
    print(f"TASK-END {task_id} {int(t1)} {round((t1 - t0) / 60, 2) if t0 else '?'}")
    _TASK.update(said=None, ended=t1)
    _os.environ.pop("VF_TASK_T0", None)


def _task_t0():
    """The running task's start (epoch s), or None."""
    now, out = _time.time(), []
    for v in (_os.environ.get("VF_TASK_T0"), globals().get("T0")):
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if 0 <= now - v < 6 * 3600 and v > _TASK["ended"]:
            out.append(v)
    return max(out) if out else None


def _cell_code():
    """The code of the kernel cell running now (its outermost module frame
    in this namespace), or None (a task shop_map started, no kernel)."""
    f, code, g = _sys._getframe(1), None, globals()
    while f is not None:
        if f.f_code.co_name == "<module>" and f.f_globals is g:
            code = f.f_code
        f = f.f_back
    return code


def _task_note():
    """Print "[task 2.4 of 8 min]" once per cell, ≥ 1 min into a task."""
    t0 = _task_t0()
    if t0 is None or _time.time() - t0 < 60:
        return
    code = _cell_code()
    if code is None or code is _TASK["said"]:
        return
    _TASK["said"] = code
    try:
        cap = float(_os.environ.get("VF_TASK_CAP_MIN") or 8)
    except ValueError:
        cap = 8.0
    print(f"[task {(_time.time() - t0) / 60:.1f} of {cap:g} min]")


# text()'s hint (B-33): the strings of the running cell (its constants and
# those of the functions it defines) that look like words searched for in
# the text: no code, selectors, URLs, format pieces or common keys.
_CELL_STOP = frozenset((
    "main body text href label value url title name data json html button input select option true false none null "
    "items item rows row utf-8 click submit link links price id key type role div span table form page result "
    "results error print await tab self frame hidden visible selector filter limit timeout wait goto css frames "
    "snippet where term terms kind path content dialog dialogs list dict string number count").split())


def _cell_terms(code, limit=6, *, groups=None):
    """Candidate search words among the string constants of cell `code`.
    groups: a dict that gets word → its source string for the words of an
    "a|b" string (alternatives share one hint line)."""
    out, todo, seen = [], [code], set()
    while todo and len(out) < 40:
        c = todo.pop()
        if id(c) in seen:
            continue
        seen.add(id(c))
        stack = list(c.co_consts)
        while stack:
            k = stack.pop()
            if isinstance(k, type(code)):
                todo.append(k)
            elif isinstance(k, (tuple, frozenset)):
                stack.extend(k)
            elif isinstance(k, str):
                for w in k.split("|") if "|" in k and "://" not in k else [k]:
                    if (3 <= len(w) <= 60 and w == w.strip() and w[0] not in ".-:,;" and w[-1] not in ":,;("
                            and _re.search(r"[^\W\d_]", w) and not _re.search(r"[/\\#\[\]{}<>=$*^@%\n\"']|://", w)
                            and w.lower() not in _CELL_STOP and not _re.fullmatch(r"[A-Z0-9_]+|R6-\S+|A-\d\S*", w)
                            and (len(w) >= 7 or " " in w or _re.search(r"[A-ZÄÖÜ\d]|[^\x00-\x7f]", w))
                            and w not in out):
                        out.append(w)
                        if groups is not None and "|" in k:
                            groups[w] = k
    return out[:limit]


# text()'s hints (B-66): the words hinted in the running cell (one hint per
# word and cell, not one per loop iteration).
_HINT_SAID = globals().get("_HINT_SAID") or {"code": None, "terms": set()}


# goto()'s loop guard (B-60, B-77): the cell whose direct goto()/submit
# calls are counted, {(tab id, kind): n}, whether its SerialGoto was printed.
_GOTO_RUN = {"code": None, "n": {}, "said": False}


def _direct_cell_call(frame, cls):
    """Whether `frame` (a ShopTab method's) was called by the running cell's
    own code: between it and the cell's outermost frame only the cell's
    code objects and functions earlier cells defined (kernel cell code);
    not another ShopTab method, nor a library function (shop_map and its
    retries of a cell-defined fn, vault_fill, a file exec'd into the kernel)
    → the cell's code, or None."""
    code = _cell_code()
    if code is None:
        return None
    codes, todo = set(), [code]
    while todo:
        c = todo.pop()
        if c in codes:
            continue
        codes.add(c)
        todo += [k for k in c.co_consts if isinstance(k, type(code))]
    f, foreign, direct = frame.f_back, False, False
    while f is not None:
        if f.f_code in codes:
            direct = not foreign  # the outermost cell frame decides
        elif f.f_code.co_name != "call" and (
                isinstance(f.f_locals.get("self"), cls)
                or (f.f_code.co_filename != code.co_filename and not f.f_globals.get("__name__", "").startswith("asyncio"))):
            foreign = True  # another ShopTab method, or library code (this file, a file exec'd into the kernel)
        f = f.f_back
    return code if direct else None


def _mask_html(s):
    """tab.html()'s text: input values the page holds as secrets (type=
    password, a credential name) as •••, JSON credential pairs masked."""
    def tag(m):
        t = m[0]
        name = _re.search(r"""\b(?:name|id)\s*=\s*["']?([^"'\s>]+)""", t, _re.I)
        val = _re.search(r"""\bvalue\s*=\s*(["'])(.*?)\1""", t, _re.I | _re.S)
        if not val or not val[2]:
            return t
        if _re.search(r"""\btype\s*=\s*["']?password""", t, _re.I) or (name and _secret_key(name[1], val[2])):
            return t[:val.start(2)] + _MASKED + t[val.end(2):]
        return t
    return _mask_json_text(_re.sub(r"<input\b[^>]*>", tag, s, flags=_re.I))


# The page side of ShopTab.pick_range for one day (Y, M, D): keeps its cell
# as the next click's element ({found: true}), else the month button towards
# it ({move: "next"|"prev"}), with the months the calendar shows.
_DAY_JS = r"""
  const MONTHS = [["januar", "january", "jan", "jän"], ["februar", "february", "feb"], ["märz", "march", "mar", "mrz"],
    ["april", "apr"], ["mai", "may"], ["juni", "june", "jun"], ["juli", "july", "jul"], ["august", "aug"],
    ["september", "sep", "sept"], ["oktober", "october", "okt", "oct"], ["november", "nov"], ["dezember", "december", "dez", "dec"]];
  const monthOf = (w) => MONTHS.findIndex((names) => names.includes(w.toLowerCase().replace(/\.$/, ""))) + 1;
  const pad = (n) => String(n).padStart(2, "0"), iso = `${Y}-${pad(M)}-${pad(D)}`;
  const de = MONTHS[M - 1][0], en = ["january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december"][M - 1];
  const RX = [new RegExp(`\\b${D}\\.? ${de} ${Y}\\b`), new RegExp(`\\b${en} ${D}(st|nd|rd|th)?,? ${Y}\\b`),
    new RegExp(`\\b${D} ${en} ${Y}\\b`), new RegExp(`\\b${pad(D)}\\.${pad(M)}\\.${Y}\\b`), new RegExp(`\\b${iso}\\b`)];
  const off = (e) => e.disabled || VF.attr(e, "aria-disabled") === "true"
    || /outside|disabled|other-?month|adjacent|hidden/i.test(VF.attr(e, "class"));
  const cells = VF.all("button, td, [role=gridcell], [role=option], [class*=datepicker__day], [role=button], [data-timestamp], [data-date], [data-day]").filter(VF.shown);
  const want = (e) => {
    const ts = VF.attr(e, "data-timestamp");
    if (/^\d{9,}$/.test(ts)) { const d = new Date(+ts); return d.getFullYear() === Y && d.getMonth() + 1 === M && d.getDate() === D; }
    const dd = VF.attr(e, "data-date") || VF.attr(e, "data-day") || VF.attr(e, "data-iso");
    if (/^\d{4}-\d{2}-\d{2}/.test(dd)) return dd.startsWith(iso);
    const a = `${VF.attr(e, "aria-label")} ${VF.attr(e, "title")}`.toLowerCase().replace(/\s+/g, " ").trim();
    return !!a && RX.some((rx) => rx.test(a));
  };
  // The months on show: headings like "November 2026".
  const shownMonths = [];
  for (const e of VF.all("h1, h2, h3, h4, h5, h6, [role=heading], [aria-live], caption, div, span, p, th, button, strong, label")) {
    const t = VF.norm(e.innerText);
    const m = t.length < 25 && /^([A-Za-zÄÖÜäöü]+\.?) (\d{4})$/.exec(t);
    if (m && monthOf(m[1]) && VF.shown(e) && ![...e.children].some((c) => VF.norm(c.innerText) === t))
      shownMonths.push({ y: +m[2], m: monthOf(m[1]), el: e });
  }
  let hit = cells.find((e) => want(e) && !off(e));
  if (!hit) {
    // The day's number in the grid under the heading of its month.
    const head = shownMonths.find((h) => h.y === Y && h.m === M);
    for (let n = head?.el, i = 0; n && !hit && i < 8; n = VF.up(n), i++) {
      const inBox = cells.filter((c) => VF.inside(c, n));
      if (inBox.length < 28) continue;
      hit = inBox.find((c) => VF.norm(c.innerText) === String(D) && !off(c)) ?? null;
    }
  }
  const months = shownMonths.map((h) => `${h.y}-${pad(h.m)}`);
  if (hit) { window[Symbol.for("vf-el")] = hit; return { found: true, months }; }
  if (!shownMonths.length) return { found: false, move: null, months };
  const key = Y * 12 + M, keys = shownMonths.map((h) => h.y * 12 + h.m);
  const move = key > Math.max(...keys) ? "next" : key < Math.min(...keys) ? "prev" : null;
  if (!move) return { found: false, move: null, months };
  const NAV = move === "next"
    ? /(next|nächste[rn]?|folgende[rn]?) (month|monat)|^(next|weiter)$|^[›»→>❯⟩▶▸]$|next-?month|month-?next|[-_]icon-right\b/i
    : /(previous|prev|vorherige[rn]?|letzte[rn]?) (month|monat)|^(previous|prev|zurück)$|^[‹«←<❮⟨◀◂]$|prev(ious)?-?month|month-?prev|[-_]icon-left\b/i;
  const btn = VF.all("button, [role=button], a, span, div").filter(VF.shown).find((e) => {
    const l = `${VF.attr(e, "aria-label")}|${VF.attr(e, "title")}|${VF.norm(e.innerText).length <= 30 ? VF.norm(e.innerText) : ""}|${VF.attr(e, "class")}`;
    return !e.disabled && VF.attr(e, "aria-disabled") !== "true" && l.split("|").some((p) => NAV.test(p.trim()));
  });
  if (!btn) return { found: false, move: null, months };
  window[Symbol.for("vf-el")] = btn;
  return { found: false, move, months };"""


# captcha(): whether the page (the widget's document) holds a response token.
_CAPTCHA_TOKEN_JS = """[...document.querySelectorAll('[name=h-captcha-response], [name=g-recaptcha-response],'
  + ' #g-recaptcha-response, [name=cf-turnstile-response]')].some((e) => (e.value || "").length > 20)"""
# The instruction of an image challenge (hCaptcha, reCAPTCHA), else its frame's first text.
_CAPTCHA_PROMPT_JS = """(document.querySelector('.prompt-text, .challenge-prompt, .rc-imageselect-instructions,'
  + ' .rc-imageselect-desc-wrapper, [class*=prompt i] h2, h2') ?? document.body)?.innerText?.slice(0, 400) ?? null"""
# The challenge's Next/Verify/Skip button (one button whose label changes): label and centre.
_CAPTCHA_BUTTON_JS = """(() => {
  const b = document.querySelector('.button-submit, #recaptcha-verify-button, [class*=submit i][role=button], button[type=submit]');
  if (!b) return null;
  const r = b.getBoundingClientRect();
  return { label: (b.innerText || b.value || b.getAttribute('aria-label') || '').trim(), x: r.x + r.width / 2, y: r.y + r.height / 2 };
})()"""


def _captcha_failures(site, add=False):
    """Times of the failed captcha tries on `site` in the last 30 min, kept
    for all kernels in the runtime directory; add=True records one more."""
    path = _shop_file("captcha-" + _re.sub(r"[^a-z0-9.-]", "_", (site or "page").lower()) + ".json")
    with open(_os.open(path, _os.O_RDWR | _os.O_CREAT, 0o600), "r+") as f:
        _fcntl.flock(f, _fcntl.LOCK_EX)
        try:
            times = _json.loads(f.read() or "[]")
        except ValueError:
            times = []
        now = _time.time()
        times = [t for t in times if now - t < 1800] + ([now] if add else [])
        f.seek(0)
        f.truncate()
        f.write(_json.dumps(times))
    return times


def _captcha_state(kind, state, fails, **more):
    """captcha()'s answer; attempt: this try's number, left: tries before the stop."""
    return {"kind": kind, "state": state, "prompt": None, "image": None, "tile": None,
            "attempt": len(fails) + 1, "left": max(0, 3 - len(fails)), **more}


class TabStuck(TimeoutError):
    """A shop tab didn't answer in time; its script was stopped. The tab
    fails fast until goto() or shop_tab(target=its id)."""


class _NoFrame(RuntimeError):
    """frame= names no shown frame (yet): lookups keep waiting for it."""


class _RefGone(RuntimeError):
    """An observe() ref whose frame is gone or loaded another page."""


class _ClickRequests:
    """While a click runs: the fetch/XHR requests to the page's own site it
    starts, and a navigation of the tab's main frame, through DevTools'
    network domain (on only meanwhile; capture() shares it). settle() waits
    for them, so that a goto() right after the click doesn't cut off an
    "add to cart", and text() reads the page the click led to."""

    CAP = 2.5  # s to wait for the click's fetch/XHR requests
    NAV_CAP = 15  # s to wait for a navigation it started

    def __init__(self, tab, url):
        self.tab, self.site = tab, _site(_urlparse.urlsplit(url).hostname or "")
        self.pending, self.docs = set(), {}

    def _on(self, msg):
        m, p = msg.get("method"), msg.get("params", {})
        if m == "Network.requestWillBeSent":
            kind, rid = p.get("type"), p["requestId"]
            if kind == "Document" and p.get("frameId") == self.tab.id:
                self.docs.setdefault(rid, False)
            elif kind in ("XHR", "Fetch") and _site(_urlparse.urlsplit(p["request"]["url"]).hostname or "") == self.site:
                self.pending.add(rid)
        elif m in ("Network.loadingFinished", "Network.loadingFailed"):
            self.pending.discard(p["requestId"])
            if p["requestId"] in self.docs:
                self.docs[p["requestId"]] = True

    async def __aenter__(self):
        self.tab._cdp._listeners.append(self._on)
        await self.tab._net_on()
        return self

    async def __aexit__(self, *exc):
        self.tab._cdp._listeners.remove(self._on)
        await self.tab._net_off()

    async def settle(self):
        """(fetch/XHR requests still running, whether it navigated) after
        waiting ≤ CAP s for those and ≤ NAV_CAP s for the new page."""
        end = _time.monotonic() + self.CAP
        while (self.pending or not all(self.docs.values())) and _time.monotonic() < end:
            await _asyncio.sleep(0.05)
        end = _time.monotonic() + self.NAV_CAP
        while not all(self.docs.values()) and _time.monotonic() < end:
            await _asyncio.sleep(0.1)
        if self.docs:
            try:
                await self.tab.wait_load(self.NAV_CAP)
            except TimeoutError:
                pass  # the caller's next call says so
        return len(self.pending), bool(self.docs)


class _FrameList:
    """tab.frames()'s result: `await` it (on the tab's connection), or use it
    at once as a list (len, iteration, indexing, printing): then the frames
    are read once in a thread over a DevTools connection of its own (DE-08)."""

    def __init__(self, tab):
        self._tab, self._list = tab, None

    def __await__(self):
        return self._tab._frames_list().__await__()

    def _sync(self):
        if self._list is None:
            import concurrent.futures as cf

            async def read():
                cdp = await _CDP.connect(self._tab._ws_url)
                try:
                    return await self._tab._frames_list(cdp)
                finally:
                    cdp.close()
            with cf.ThreadPoolExecutor(1) as ex:
                self._list = ex.submit(_asyncio.run, read()).result(60)
        return self._list

    def __iter__(self):
        return iter(self._sync())

    def __len__(self):
        return len(self._sync())

    def __getitem__(self, i):
        return self._sync()[i]

    def __eq__(self, other):
        return self._sync() == other

    def __repr__(self):
        return repr(self._sync())


class ShopTab:
    """One page of the shop browser. Everything is async. Selectors are CSS
    and reach into open shadow roots; click/observe also find things by their
    text. Text and observe() never return what input fields hold. The usage
    header of this file has a cheat-sheet."""

    def __init__(self, target_id, ws_url, cdp):
        self.id, self._ws_url, self._cdp = target_id, ws_url, cdp
        self._stuck = None  # why the tab stopped answering; set by _stall
        self._dialog = None  # the JS confirm/prompt/beforeunload open now
        self._browser = _shop_pid()  # the browser's pid: a later one means it restarted (BrowserRestarted)
        self._alerts = []  # alert() texts accepted since the last text()
        self._navigating = False  # goto() leaves the page even if it asks
        self._dialogs = 0  # JS dialogs opened so far: a click's effect too
        self._refs, self._refs_doc = {}, None  # observe()'s refs: {ref: tag, text, href, css, frame} of that document
        self._refs_max = 0  # the highest ref given in it or its frames: refs stay unique per tab
        self._worlds = {}  # isolated-world context ids of its in-process frames (see _Frames)
        self._captcha = None  # the challenge captcha() showed last: where its montage tiles are
        self._net = 0  # users of DevTools' network domain (click, capture)
        self._hint_hidden = {}  # text()'s hints (B-66): word → (page, hidden snippets, count) of the last page read
        self._core_said = None  # the URL whose thin page _core_wait already waited on (B-71)
        cdp._listeners.append(self._on_event)

    def __repr__(self):
        return f"<ShopTab {self.id}>"

    def _on_event(self, msg):
        m, p = msg.get("method"), msg.get("params", {})
        if m == "Page.javascriptDialogOpening":
            self._dialogs += 1
            if p.get("type") == "alert" or (p.get("type") == "beforeunload" and self._navigating):
                # alert: only an OK button; accepted, its text kept for text().
                if p.get("type") == "alert":
                    self._alerts.append(p.get("message", ""))
                _asyncio.ensure_future(self._answer(True))
            else:
                self._dialog = p
        elif m == "Page.javascriptDialogClosed":
            self._dialog = None

    async def _answer(self, accept, text=None):
        params = {"accept": accept}
        if text is not None:
            params["promptText"] = text
        try:
            await self._cdp.send("Page.handleJavaScriptDialog", params, timeout=5)
        except (RuntimeError, ConnectionError, _asyncio.TimeoutError):
            pass  # gone already

    async def dialog(self, accept=False, text=None):
        """Answer the JS confirm/prompt/"leave page?" dialog that blocks this
        tab (TabStuck names it): accept=False is Cancel / stay. Returns its
        message, or None if none is open."""
        d = self._dialog
        if not d:
            return None
        await self._answer(accept, text)
        self._dialog, self._stuck = None, None
        return d.get("message", "")

    async def _setup(self):
        # Scripts added here last as long as this connection, and run on new
        # documents only with the Page domain on; runImmediately also covers
        # the document already open (a popup's first page has run its own
        # scripts by then).
        await self._cdp.send("Page.enable")
        await self._cdp.send(
            "Page.addScriptToEvaluateOnNewDocument", {"source": _NEWDOC_JS, "runImmediately": True}
        )
        # After it, so it wraps those proxies: long-polls, JSON bodies, custom validity (pass 10).
        await self._cdp.send(
            "Page.addScriptToEvaluateOnNewDocument", {"source": _NETLOG_JS, "runImmediately": True}
        )
        # Each tab acts as the focused one, though they share the display.
        await self._cdp.send("Emulation.setFocusEmulationEnabled", {"enabled": True})

    async def _net_on(self):
        """DevTools' network domain on, for click() and capture() (counted:
        a click inside a capture keeps it on)."""
        self._net += 1
        if self._net == 1:
            await self._cdp.send("Network.enable", {"maxResourceBufferSize": 20_000_000, "maxTotalBufferSize": 100_000_000})

    async def _net_off(self):
        self._net -= 1
        if self._net == 0:
            try:
                await self._cdp.send("Network.disable", timeout=5)
            except (RuntimeError, ConnectionError, _asyncio.TimeoutError):
                pass  # the tab went away

    async def _act(self, coro, timeout=20):
        """Await an input command, unless the JS dialog it opens blocks the
        page (its answer then comes once the dialog is answered)."""
        task = _asyncio.ensure_future(coro)
        task.add_done_callback(lambda t: t.cancelled() or t.exception())  # no "never retrieved"
        deadline = _time.monotonic() + timeout
        while not task.done():
            if self._dialog:
                return None
            if _time.monotonic() > deadline:
                task.cancel()
                raise _asyncio.TimeoutError()
            await _asyncio.wait({task}, timeout=0.05)
        return task.result()

    async def _ev(self, expression, timeout=20, doc=None):
        """An evaluation for the tool's own polls: a timeout is just a
        TimeoutError (mid-navigation); a stuck tab, or one a JS dialog
        blocks, fails at once. `doc`: a frame's document (see _Frames).
        Prints the task's elapsed time once per cell (_task_note)."""
        _task_note()
        if self._stuck:
            raise TabStuck(self._stuck)
        if self._dialog:
            raise TabStuck(f"a JS {self._dialog.get('type')} blocks the tab: {self._dialog.get('message', '')[:200]!r}; "
                           "tab.dialog(accept=False) answers it (Cancel), accept=True confirms")
        if doc is not None and doc.parent is not None:
            return await doc.ev(expression, timeout)
        return await self._cdp.evaluate(expression, timeout)

    def _frames(self):
        """This tab's frames, listed when a call first needs them; the caller
        closes it (await fs.close())."""
        return _Frames(self._cdp, self.id, _SHOP_PORT, self._worlds)

    def _in_frame_ref(self, ref):
        """observe()'s record of `ref` if that element is in a frame."""
        info = self._refs.get(ref) if ref is not None else None
        return info if info and info.get("frame") else None

    async def _frame_docs(self, fs, frame=None, ref=None):
        """The frames a lookup searches, in document order: those `frame`
        names, the frame of observe() ref `ref`, else every shown one."""
        info = self._in_frame_ref(ref)
        if info:
            await fs.load()
            d = fs.by_frame(info["frame"])
            if d is None or d.loader != info["loader"]:
                raise _RefGone(f"ref {ref} was in the frame {info['host']}, which "
                               + ("is gone" if d is None else "has loaded another page since") + ": observe() again")
            return [d]
        if frame is not None:
            docs = await fs.named(frame)
            if not docs:
                raise _NoFrame(f"no shown frame matches frame={frame!r}; the tab has {fs.describe()}")
            return docs
        await fs.load()
        return fs.shown_docs()

    async def _find(self, expr, ok=bool, *, frame=None, ref=None, fs):
        """One look for what page expression `expr` finds: (doc, result) of
        the top document, unless `frame` names frames or `ref` is a frame's,
        else of the first frame whose result `ok` takes (frames are listed
        only then); doc None if none does, with the top document's result."""
        r0 = None
        if frame is None and not self._in_frame_ref(ref):
            r0 = await self._ev(expr)
            if ok(r0) or ref is not None:
                return (fs.top if ok(r0) else None), r0
        d, r = await _frame_search(fs, expr, ok, await self._frame_docs(fs, frame, ref))
        return (d, r) if d else (None, r0)

    _in = staticmethod(_Frames.where)

    @staticmethod
    def _searched(fs):
        """"; searched the page and 2 frames: …" once a lookup listed frames."""
        return f"; searched {fs.describe()}" if fs.at is not None else ""

    def frames(self):
        """The tab's frames, in document order, nested up to 3 deep: [{host,
        url, cross_site, box: [x, y, w, h] in the top page's CSS px, visible}].
        Works with `await tab.frames()` and without await (then read over a
        connection of its own, in a thread)."""
        return _FrameList(self)

    async def _frames_list(self, cdp=None):
        fs = self._frames() if cdp is None else _Frames(cdp, self.id, _SHOP_PORT)
        try:
            docs = await fs.load()
            href = await (self.url() if cdp is None else cdp.evaluate("location.href", 5))
            top = _site(_urlparse.urlsplit(href).hostname or "")
            vw, vh = fs.top.vw or 1e9, fs.top.vh or 1e9
            return [{"host": d.host, "url": _mask_url(d.url[:300]), "cross_site": bool(d.host) and _site(d.host) != top,
                     "box": d.box, "visible": bool(d.shown and d.box and d.box[0] < vw and d.box[1] < vh
                                                   and d.box[0] + d.box[2] > 0 and d.box[1] + d.box[3] > 0)}
                    for d in docs]
        finally:
            await fs.close()

    async def _guard(self, expression, timeout=20):
        """An evaluation that, when the page doesn't answer in `timeout` s,
        stops its script and marks the tab stuck (TabStuck)."""
        try:
            return await self._ev(expression, timeout)
        except TabStuck:
            raise
        except _asyncio.TimeoutError:
            pass
        await self._stall(timeout)

    async def _stall(self, timeout):
        try:
            await self._cdp.send("Runtime.terminateExecution", timeout=3)
        except (RuntimeError, ConnectionError, _asyncio.TimeoutError):
            pass
        try:
            url = next((t["url"] for t in _shop_pages() if t["id"] == self.id), "?")
        except (OSError, ValueError):
            url = "?"
        why = f"the tab at {_mask_url(url)} did not answer within {timeout} s"
        if self._dialog:
            why += (f"; a JS {self._dialog.get('type')} blocks it: {self._dialog.get('message', '')[:200]!r} "
                    "(tab.dialog(accept=False) answers it)")
        self._stuck = why
        raise TabStuck(why + "; its script was stopped, and the tab fails fast until goto() or shop_tab(target=…)")

    async def eval(self, expression, *args, timeout=60):
        """Evaluate a JS expression (not a function) in the page; returns its
        JSON value, its strings with one-time-link tokens in URLs as •••
        (goto() of such a URL opens the real one). With `args`: `expression`
        is a function, called with them as JSON values:
        eval("(sel, n) => document.querySelectorAll(sel).length > n", "a", 3)
        (one number after a non-function is the old eval(js, timeout)).
        After `timeout` s: TabStuck (see the header)."""
        if args and not _EVAL_FN.match(expression):
            if len(args) == 1 and isinstance(args[0], (int, float)) and not isinstance(args[0], bool):
                timeout, args = args[0], ()  # eval(js, 30): the timeout, as before
            else:
                raise ValueError("eval(js, *args): with args, js must be a function: "
                                 "'(sel, n) => …' or 'function (a) { … }'")
        if args:
            expression = f"({expression})(...{_json.dumps(list(args))})"
        return _mask_urls(await self._guard(expression, timeout))

    async def url(self, *, timeout=20):
        """The page's URL, one-time-link tokens (magic login, ?token=, ?code=,
        ?sig=, …) as •••; goto() of it opens the real one."""
        return _mask_url(await self._guard("location.href", timeout))

    async def _href(self, timeout=20):
        """The page's real URL, for the tools' own requests (never shown)."""
        return await self._guard("location.href", timeout)

    async def title(self, *, timeout=20):
        return await self._guard("document.title", timeout)

    async def _where(self):
        """Text like " (page: <title> <url>)" for errors; "" if it can't tell."""
        try:
            t, u = await self._ev("[document.title, location.href]", 3)
            return f" (page: {t[:80]!r} {_mask_url(u)})"
        except Exception:
            return ""

    async def _rules(self, since, href=None):
        """Raise HardRule when, since `since` (monotonic), the guard blocked a
        navigation of this tab, its frames or a window it opened (that window
        is closed), or when the tab shows Google's phone challenge. `href`:
        the URL just read ("" skips that check), None reads it."""
        hits = [b for b in _GUARD_BLOCKS if b[0] >= since]
        if hits:
            ids, popups, infos = {self.id}, set(), []
            try:
                b = await _shop_browser_cdp()
                try:
                    infos = (await b.send("Target.getTargets", timeout=5))["targetInfos"]
                finally:
                    b.close()
                ids |= _target_family(infos, self.id)
                tree = [(await self._cdp.send("Page.getFrameTree", timeout=5))["frameTree"]]
                while tree:
                    n = tree.pop()
                    ids.add(n["frame"]["id"])
                    tree += n.get("childFrames", [])
            except (OSError, ValueError, KeyError, RuntimeError, ConnectionError, _asyncio.TimeoutError):
                pass
            popups = {t["targetId"] for t in infos if t.get("type") == "page"
                      and (t.get("openerId") in ids or t.get("openerFrameId") in ids)}
            for h in hits:
                u, fid, opener = h[1], h[2], h[3] if len(h) > 3 else None
                if fid in ids or fid in popups or opener in ids:
                    if fid in popups:
                        await _close_blocked(fid)
                    raise HardRule(_blocked_msg(_mask_url(u)))
        if href is None:
            try:
                href = await self._ev("location.href", 5)
            except (RuntimeError, _asyncio.TimeoutError):
                href = ""
        if href and _PHONE_RE.search(href):
            raise HardRule(_phone_msg(_mask_url(href)))

    async def _after_load(self):
        """After a page loads: reject its cookie banner once it shows (this
        also covers documents opened before _setup), print the site note."""
        try:
            url = await self._ev(_CONSENT_POLL_JS + ";\nlocation.href", timeout=5)
        except (RuntimeError, _asyncio.TimeoutError, ConnectionError):
            return  # mid-navigation: the next wait does it
        _site_note(url)

    async def wait_load(self, timeout=30, *, _dom=False):
        """Wait until a document other than about:blank (where new windows
        start) has loaded. When it still loads after `timeout` s (a stalled
        tracker) but already shows text, warns and returns. (_dom: goto's
        wait, for DOMContentLoaded only; _settle does the rest.)"""
        ready = 'document.readyState !== "loading"' if _dom else 'document.readyState === "complete"'
        expr = ready + ' && location.href !== "about:blank"'
        deadline = _time.monotonic() + timeout
        while _time.monotonic() < deadline:
            try:
                if await self._ev(expr, timeout=5):
                    await self._after_load()
                    return
            except TabStuck:
                raise
            except (RuntimeError, _asyncio.TimeoutError):
                pass  # mid-navigation
            await _asyncio.sleep(0.2)
        try:
            state, href, n = await self._ev(
                '[document.readyState, location.href, (document.body?.innerText ?? "").trim().length]', 5)
        except TabStuck:
            raise
        except (RuntimeError, _asyncio.TimeoutError):
            state = None
        if state in ("interactive", "complete") and href != "about:blank" and n:
            print(f"[wait_load: {_mask_url(href)} still loading after {timeout} s; going on with what it shows]")
            await self._after_load()
            return
        raise TimeoutError(f"the page did not finish loading in {timeout} s{await self._where()}")

    async def _settle(self, cap=10.0, quiet=0.5):
        """Wait until the body text is non-empty and unchanged for `quiet` s
        (SPAs render after the load event; 1 s while `load` hasn't fired)
        and no spinner, skeleton or aria-busy box shows in the viewport, at
        most `cap` s."""
        deadline, last, since = _time.monotonic() + cap, None, _time.monotonic()
        while _time.monotonic() < deadline:
            try:
                n, busy, state = await self._ev(_BUSY_JS, 5)
            except TabStuck:
                raise
            except (RuntimeError, _asyncio.TimeoutError, TypeError, ValueError):
                n, busy, state = None, False, None
            now = _time.monotonic()
            if n != last:
                last, since = n, now
            elif n and not busy and now - since >= (quiet if state == "complete" else max(quiet, 1.0)):
                return
            await _asyncio.sleep(0.15)

    def _serial_step(self, kind, serial, frame):
        """B-77: count one direct call of the running cell (`frame`: the
        calling ShopTab method's) per tab and kind ("goto", "submit"); the
        4th and later ones raise SerialGoto before they act, unless `serial`.
        The first raise of a cell also prints its message, so a loop that
        swallows the error (except Exception: continue) still says it."""
        code = _direct_cell_call(frame, ShopTab)
        if code is None:
            return
        if _GOTO_RUN["code"] is not code:
            _GOTO_RUN.update(code=code, n={}, said=False)
        key = (self.id, kind)
        n = _GOTO_RUN["n"][key] = _GOTO_RUN["n"].get(key, 0) + 1
        if n < 4 or serial:
            return
        if kind == "goto":
            msg = ("4th goto in this cell: use shop_map(urls, fn, concurrency=3), or goto(..., serial=True) "
                   "when the pages must load in order")
            hint = "[goto: 4th page in this cell; shop_map(urls, fn, concurrency=3) reads them in parallel]"
        else:
            msg = ("4th search submit on this tab in this cell: use shop_map over the site's search URLs "
                   "(…?q=<term>) with concurrency=3, or serial=True when the searches must run in order")
            hint = "[search: 4th submit in this cell; shop_map over search URLs reads them in parallel]"
        if not _GOTO_RUN["said"]:
            _GOTO_RUN["said"] = True
            print(f"{hint} SerialGoto raised: this call and the next ones in this cell did not run ({msg})")
        raise SerialGoto(msg)

    async def _core(self):
        """One _CORE_JS look at the page (B-71), or None (no body yet,
        mid-navigation)."""
        try:
            return await self._ev(_vf(_INFLIGHT_JS + _CORE_JS, GATE=_GATE_RX, WEAK=_GATE_WEAK_RX), 5)
        except TabStuck:
            raise
        except (RuntimeError, _asyncio.TimeoutError):
            return None

    async def _core_wait(self, cap=8.0):
        """B-71: while the page shows little besides its header, nav and
        footer (_CORE_JS's thin), wait ≤ `cap` s for it to grow: until it
        isn't thin, or it has been calm (no fetch/XHR in flight or ended,
        no resource loaded, the core unchanged) for 1.2 s (0.6 s when it
        already was so; 0.8 s more while `load` hasn't fired). Once per URL
        (goto, then text() wait only once). The last look; "busy" in it:
        still changing after `cap` s."""
        c = await self._core()
        if not c or not c["thin"] or self._core_said == c["href"]:
            return c
        t0 = since = _time.monotonic()
        quiet = c["state"] == "complete" and not c["inflight"] and c["net"] >= 2000 and c["res"] >= 2000 and c["age"] >= 3000
        last = c["sig"]
        while True:
            await _asyncio.sleep(0.3)
            n = await self._core()
            now = _time.monotonic()
            if n is not None:
                c = n
                if not c["thin"]:
                    return c
                if c["sig"] != last:
                    last, since, quiet = c["sig"], now, False
                calm = (0.6 if quiet else 1.2) + (0 if c["state"] == "complete" else 0.8)  # a stalled tracker keeps `load` away
                if not c["inflight"] and c["net"] >= 1000 and c["res"] >= 1000 and now - since >= calm:
                    break
            if now - t0 >= cap:
                c["busy"] = True
                break
        self._core_said = c["href"]
        return c

    async def goto(self, url, *, wait=None, timeout=None, serial=False):
        """Navigate; then wait for DOMContentLoaded (not `load`: trackers and
        carousels may never finish), and for text `wait` if given, else
        until the text stops changing (1 s while still loading) and no
        spinner, skeleton or aria-busy box shows (≤10 s), and while the page
        shows only its header/nav/footer (< 300 chars besides, an empty
        <main> or large same-origin iframe) ≤ 8 s more for its content
        (B-71). A cookie banner on
        the page is rejected by itself (best effort). Clears TabStuck.
        HardRule when the hard rules block the page (wallet.google.com, …)
        or it lands on Google's phone challenge. A URL whose token url(),
        eval() or observe() showed as ••• opens the real one (ValueError if
        this kernel never saw it); messages show URLs masked. A www/apex host
        that takes no TCP connection within 8 s, or that Chrome can't reach
        (refused, no DNS), is tried once as its twin (www.crm.de ↔ crm.de),
        with a printed line saying so. With `wait`: SiteError (a
        TimeoutError) within ~2 s when the new page shows a site error
        ("Das hat leider nicht geklappt", "Tut uns Leid", "502 Bad Gateway")
        instead of the text; without timeout= (30 s) it ends after ≥ 5 s once
        the page is loaded and quiet for 3 s without it (as wait_for); a
        timeout= over 60 s ends once it has been quiet 20 s without it.
        B-77: the 4th goto on one tab in one cell raises SerialGoto before
        navigating ("use shop_map(urls, fn, concurrency=3)"); serial=True
        when the pages must load in order (shop_map's own gotos don't count)."""
        quiet, timeout = timeout is None, 30 if timeout is None else timeout
        self._serial_step("goto", serial, _sys._getframe(0))
        url, shown = _unmask_url(url), _mask_url(url)
        self._stuck = None
        self._core_said = None
        t0 = _time.monotonic()
        if self._dialog:
            await self.dialog(accept=self._dialog.get("type") == "beforeunload")  # leave the old page
        twin = _url_twin(url)
        while True:
            try:
                self._navigating = True
                nav = _asyncio.ensure_future(self._cdp.send("Page.navigate", {"url": url}, timeout))
                nav.add_done_callback(lambda t: t.cancelled() or t.exception())
                dead = await self._unreachable(nav, url) if twin else None
                r = {} if dead else await nav
            except _asyncio.TimeoutError:
                try:
                    await self._cdp.send("Page.stopLoading", timeout=3)
                except (RuntimeError, ConnectionError, _asyncio.TimeoutError):
                    pass
                raise TimeoutError(f"{shown} did not answer within {timeout} s (the server may be down)") from None
            finally:
                self._navigating = False
            if twin and not dead and any(e in r.get("errorText", "") for e in _NET_DEAD):
                dead = r["errorText"].removeprefix("net::")
            if not dead:
                break
            host, th = _urlparse.urlsplit(url).hostname, _urlparse.urlsplit(twin).hostname
            ok, why = await _tcp_probe(twin)
            if not ok:
                if r:
                    raise RuntimeError(f"navigation to {shown} failed: {r['errorText']}; its twin {th}: {why}")
                raise TimeoutError(f"{shown}: {dead}, and its twin {th}: {why}: the server is down or unreachable from here")
            print(f"[goto: {host}: {dead}; used {th}]")
            url, shown, twin = twin, _mask_url(twin), None
        if r.get("errorText"):
            if "BLOCKED_BY_CLIENT" in r["errorText"]:
                await _asyncio.sleep(0.05)  # the guard's event may come just after
                await self._rules(t0, "")
            raise RuntimeError(f"navigation to {shown} failed: {r['errorText']}")
        await self.wait_load(timeout, _dom=True)
        await self._rules(t0)
        if wait:
            await self.wait_for(text=wait, timeout=timeout, _errbase=(), _quiet=quiet)  # the new page: any error text counts
        else:
            await self._settle()
            await self._core_wait()
            await self._rules(t0)

    async def _unreachable(self, nav, url):
        """While Page.navigate `nav` is still pending after 2 s: why `url`'s
        host takes no TCP connection within _CONNECT_WAIT s in all (the
        navigation is then stopped), else None."""
        done, _ = await _asyncio.wait({nav}, timeout=2.0)
        if done:
            return None
        probe = _asyncio.ensure_future(_tcp_probe(url, _CONNECT_WAIT - 2.0, said=_CONNECT_WAIT))
        try:
            done, _ = await _asyncio.wait({nav, probe}, return_when=_asyncio.FIRST_COMPLETED)
        finally:
            if not probe.done():
                probe.cancel()
        if nav in done or probe.cancelled() or probe.result()[0]:
            return None
        nav.cancel()
        try:
            await self._cdp.send("Page.stopLoading", timeout=3)
        except (RuntimeError, ConnectionError, _asyncio.TimeoutError):
            pass
        return probe.result()[1]

