# The page-side helpers every ShopTab method and the shop filler use, as a
# factory: `(LIB)()` gives a fresh set, whose element list is cached for one
# use. "Deep" means: open shadow roots included, which bahn.de's consent banner,
# Lieferando's pie-button and Vaadin's fields need.
_LIB_JS = r"""() => {
  // Soft hyphens and zero-width characters don't count (atu.de's long German
  // words carry &shy;, invisible in text() but not equal to the typed word).
  const INVIS = /[\u00ad\u200b-\u200d\u2060\ufeff]/g;
  const norm = (s) => (s || "").replace(INVIS, "").replace(/\s+/g, " ").trim();
  const squash = (s) => (s || "").replace(INVIS, "").replace(/\s+/g, "").toLowerCase();
  const attr = (e, a) => (e && e.getAttribute && e.getAttribute(a)) || "";
  // The parent, across a shadow root's boundary to its host.
  const up = (n) => n.parentElement ?? n.parentNode?.host ?? null;
  const inside = (a, b) => { for (let n = a; n; n = up(n)) if (n === b) return true; return false; };
  let els = null, roots = null;
  // Every element in tree order, a shadow root's content after its host.
  // Only real elements and shadow roots: anti-bot scripts (PerimeterX) patch
  // shadowRoot/querySelectorAll and hand back other things (SC-09).
  const realRoot = (r) => r instanceof ShadowRoot;
  const elements = () => {
    if (els) return els;
    els = [];
    const rec = (root) => {
      for (const e of root.querySelectorAll("*")) {
        if (!(e instanceof Element) || typeof e.localName !== "string") continue;
        els.push(e);
        if (realRoot(e.shadowRoot)) rec(e.shadowRoot);
      }
    };
    rec(document);
    return els;
  };
  const all = (css) => (roots ??= [document, ...elements().filter((e) => realRoot(e.shadowRoot)).map((e) => e.shadowRoot)])
    .flatMap((r) => [...r.querySelectorAll(css)].filter((e) => e instanceof Element));
  const shown = (e) => {
    const s = getComputedStyle(e);
    if (s.display === "none" || s.visibility === "hidden" || s.visibility === "collapse") return false;
    if (s.display === "contents") return [...e.children, ...(e.shadowRoot?.children ?? [])].some(shown);
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const pointer = (e) => getComputedStyle(e).cursor === "pointer";
  const isField = (e) => (/^(input|textarea|select)$/.test(e.localName) && !/^(submit|button|reset|image|hidden)$/.test(e.type))
    || (e.isContentEditable && !up(e)?.isContentEditable);
  const ROLES = /^(button|link|radio|checkbox|tab|option|menuitem|menuitemradio|menuitemcheckbox|switch|combobox|treeitem|slider|spinbutton)$/;
  // Clickable by its markup; custom elements by a type or a button-like name
  // (pie-button, oc-button-v1, vaadin-combo-box-item).
  const semantic = (e) => /^(a|button|input|select|textarea|label|summary|option)$/.test(e.localName)
    || ROLES.test(attr(e, "role")) || e.hasAttribute("href") || e.hasAttribute("onclick")
    || (e.hasAttribute("tabindex") && attr(e, "tabindex") !== "-1")
    || (e.localName.includes("-") && (e.hasAttribute("type") || /button|btn|link|chip|tab|option|radio|checkbox|switch|select|combo|item/.test(e.localName)));
  const labelledby = (e) => attr(e, "aria-labelledby").split(/\s+/).filter(Boolean)
    .map((id) => e.getRootNode().getElementById?.(id)?.innerText ?? "").join(" ");
  // An element's text; for a field its label or placeholder, never its value.
  const label = (e) => {
    if (isField(e)) return norm(attr(e, "aria-label") || [...(e.labels ?? [])].map((l) => l.innerText).join(" ")
      || labelledby(e) || attr(e, "title") || attr(e, "placeholder") || attr(e, "label"));
    let t = e.innerText;
    if (!t && e.shadowRoot) t = [...e.shadowRoot.children].map((c) => c.innerText || "").join(" ");
    return norm(t || attr(e, "aria-label") || labelledby(e) || attr(e, "title") || attr(e, "alt")
      || (e.localName === "input" ? e.value : "") || attr(e, "label") || e.querySelector?.("img[alt]")?.alt
      || (e.localName.includes("-") ? e.textContent.trim() || attr(e.shadowRoot?.querySelector("[aria-label]"), "aria-label") : ""));
  };
  // The clickable thing around e: e or its nearest clickable ancestor, else
  // the outermost of a run of cursor:pointer elements (the cursor is inherited).
  const target = (e) => {
    let top = null;
    for (let n = e, i = 0; n && n !== document.body && i < 15; n = up(n), i++) {
      if (semantic(n)) return n;
      if (pointer(n)) top = n; else if (top) return top;
    }
    return top;
  };
  // Where a cursor:pointer run starts, outside any clickable element: the
  // clickable divs observe() lists (Check24's sort box, Lieferando's cards).
  const pointerOrigin = (e) => {
    if (!pointer(e) || (up(e) && pointer(up(e)))) return false;
    for (let n = up(e), i = 0; n && i < 10; n = up(n), i++) if (semantic(n)) return false;
    return !!label(e);
  };
  // Elements whose text contains `text`, as their clickable targets: clickable
  // ones first, then by match (q: visible text equal to it 3, containing it
  // 2, only its aria-label/title/alt 1), then those in an open layer (a
  // modal's "Weiter" over the page's), then the shortest text (a button
  // beats its container).
  const byText = (text, exact) => {
    const wq = squash(text), seen = new Set(), out = [];
    for (const e of elements()) {
      if (/^(script|style|noscript|template|html|head|body)$/.test(e.localName)) continue;
      const hay = squash(e.textContent) + "|" + squash(attr(e, "aria-label") + attr(e, "title") + attr(e, "alt")
        + attr(e, "placeholder") + attr(e, "label") + (e.localName === "input" && /^(submit|button|reset)$/.test(e.type) ? e.value : ""))
        + squash((e.labels instanceof NodeList ? [...e.labels] : []).map((l) => l.textContent).join(""));
      if (!hay.includes(wq)) continue;
      // Whitespace aside: "Artikel" and "anzeigen" in two spans may render as one word.
      const l = squash(label(e)), aria = squash(attr(e, "aria-label"));
      if (exact ? l !== wq && aria !== wq : !l.includes(wq) && !aria.includes(wq)) continue;
      if (!shown(e)) continue;
      const t = target(e), el = t ?? e;
      if (seen.has(el)) continue;
      seen.add(el);
      const vis = squash(el.localName === "input" ? el.value : el.innerText);
      const q = vis === wq ? 3 : vis.includes(wq) ? 2 : 1;
      out.push({ el, ok: t ? 1 : 0, q, n: label(el).length, top: 0 });
    }
    let keep = out;
    if (out.length > 1) {
      const L = layers();
      if (L.length) for (const c of out) c.top = L.some((d) => inside(c.el, d)) ? 1 : 0;
      // A wizard's or carousel's other slides: matches there go when one can be seen (rebuy.de).
      const can = out.filter((c) => reachable(c.el));
      if (can.length) keep = can;
    }
    return keep.sort((a, b) => (b.ok - a.ok) || (b.q - a.q) || (b.top - a.top) || (a.n - b.n));
  };
  // Whether a person can get to e: not under aria-hidden or inert, not faded
  // out (opacity 0), not slid out of a box that clips without scrolling.
  const reachable = (e) => {
    const r = e.getBoundingClientRect();
    for (let n = e, i = 0; n && n.nodeType === 1 && n !== document.body && i < 40; n = up(n), i++) {
      if (n.inert || attr(n, "aria-hidden") === "true") return false;
      const s = getComputedStyle(n);
      if (s.opacity === "0") return false;
      if (n !== e && (s.overflowX !== "visible" || s.overflowY !== "visible") && !/auto|scroll/.test(s.overflowX + s.overflowY)) {
        const b = n.getBoundingClientRect();
        if (r.right <= b.left + 1 || r.left >= b.right - 1 || r.bottom <= b.top + 1 || r.top >= b.bottom - 1) return false;
      }
    }
    return true;
  };
  // A match only by aria-label/title whose label adds a destructive word to
  // the query ("Diesen Job verwerfen: <title>"): never clicked for that text.
  const DESTRUCTIVE = /(^|[^\p{L}])(verwerfen|dismiss|entfernen|löschen|loeschen|remove|delete|ablehnen|melden|report|hide|ausblenden)([^\p{L}]|$)/iu;
  const destructive = (c, text) => {
    if (c.q !== 1) return false;
    const q = norm(text).toLowerCase();
    const extra = norm(`${attr(c.el, "aria-label")} ${attr(c.el, "title")}`).toLowerCase().split(q).join(" ");
    return DESTRUCTIVE.test(extra) && !DESTRUCTIVE.test(q);
  };
  // `within` as whole words, case and spacing aside ("07:19" not in "107:19",
  // "Milch" not in "Vollmilch"); every word must be there, in any order and
  // with anything between ("18:37 00:51" fits "18:37 – 00:51").
  const words = (s) => {
    const rxs = norm(s).split(" ").filter(Boolean).map((w) => new RegExp("(?<![\\p{L}\\p{N}])"
      + w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "(?![\\p{L}\\p{N}])", "iu"));
    return { test: (t) => rxs.every((rx) => rx.test(t)) };
  };
  const boxText = (n) => norm(n.innerText ?? n.textContent);
  // Each candidate with its card: the smallest box around it that also
  // holds the words `within` (the "Weiter" of the card with "07:19");
  // smallest card first, then clickable ones, then the shortest text.
  const near = (cands, within) => {
    const rx = words(within), out = [];
    for (const c of cands) {
      for (let n = c.el; n && n !== document.documentElement; n = up(n)) {
        const t = boxText(n);
        if (rx.test(t)) { out.push({ ...c, box: t.length, card: n }); break; }
      }
    }
    // A "card" that holds another match beside this one is a list of cards
    // (DD-02: "Details" within "Outlook" hit the whole list → Ryanair's): not a card.
    const own = out.filter((o) => !cands.some((d) => d.el !== o.el && (d.q ?? 0) === (o.q ?? 0) && !inside(o.el, d.el)
      && !inside(d.el, o.el) && inside(d.el, o.card)));
    return own.sort((a, b) => ((b.q ?? 0) - (a.q ?? 0)) || (a.box - b.box) || (b.ok - a.ok) || (a.n - b.n));
  };
  // An observe() ref whose element the page replaced (a list that redrew):
  // the one shown element with the stored selector, else with the same tag,
  // text and href; only in the document that observe() ran in.
  const reref = (fb) => {
    if (!fb || fb.doc !== window[Symbol.for("vf-doc")]) return [];
    if (fb.css && !fb.css.startsWith("[data-vf-ref")) {
      try { const c = all(fb.css).filter(shown); if (c.length === 1) return c; } catch {}
    }
    if (!fb.text) return [];
    return elements().filter((e) => e.localName === fb.tag && label(e).slice(0, 100) === fb.text
      && (!fb.href || absHref(e).slice(0, 300) === fb.href) && shown(e));
  };
  // spec: {css} | {text, exact} | {ref, fb} | {el} (the last element found), plus nth, within.
  // With `within`: also the card's text, and when another candidate sits in a
  // different card that holds those words too (neither card inside the
  // other), both cards' texts as `ambiguous`, unless nth picks one.
  const find = (spec) => {
    let c, again = null;
    if (spec.text != null && spec.css == null && spec.ref == null && !spec.el) c = byText(spec.text, spec.exact);
    else {
      let els = spec.el ? [window[Symbol.for("vf-el")]].filter((e) => e?.isConnected)
        : spec.ref != null ? all(`[data-vf-ref="${spec.ref}"]`) : all(spec.css);
      if (spec.ref != null && !els.some(shown) && spec.fb) {
        const alt = reref(spec.fb);
        again = alt.length;
        if (alt.length === 1) els = alt;
      }
      if (spec.also != null) {
        const w = squash(spec.also);
        els = els.filter((e) => { const l = squash(label(e)); return spec.exact ? l === w : l.includes(w); });
      }
      c = els.map((el) => ({ el, ok: 1, n: 0 }));
    }
    const total = c.length;
    c = c.filter((x) => shown(x.el));
    if (spec.text != null && c.length) {
      const bad = c.filter((x) => destructive(x, spec.text));
      c = c.filter((x) => !bad.includes(x));
      if (!c.length) return { el: null, total, refused: bad.slice(0, 5).map((x) => norm(attr(x.el, "aria-label") || attr(x.el, "title")).slice(0, 120)) };
    }
    const visible = c.length;
    if (!spec.within) return { el: c[spec.nth ?? 0]?.el ?? null, total, visible, again };
    c = near(c, spec.within);
    const pick = c[spec.nth ?? 0];
    if (!pick) return { el: null, total, visible, again };
    const card = boxText(pick.card).slice(0, 200);
    if (!spec.nth) {
      const other = c.find((d) => d.card !== pick.card && !inside(d.card, pick.card) && !inside(pick.card, d.card));
      if (other) return { el: null, total, ambiguous: [card, boxText(other.card).slice(0, 200)] };
    }
    return { el: pick.el, total, card, again };
  };
  const checkable = (e) => {
    if (!e) return null;
    const c = e.localName === "label" ? (e.control ?? e.querySelector("input")) : e;
    if (c && c.localName === "input" && /^(checkbox|radio)$/.test(c.type)) return c;
    if (/^(radio|checkbox|switch|menuitemradio|menuitemcheckbox)$/.test(attr(e, "role"))) return e;
    const inner = e.querySelectorAll?.("input[type=checkbox], input[type=radio]") ?? [];
    return inner.length === 1 ? inner[0] : null;
  };
  const checked = (c) => c == null ? null : typeof c.checked === "boolean" ? c.checked : attr(c, "aria-checked") === "true";
  const kind = (c) => c.localName === "input" ? c.type : attr(c, "role");
  const active = () => {
    let a = document.activeElement;
    while (a?.shadowRoot?.activeElement) a = a.shadowRoot.activeElement;
    return a;
  };
  const hitAt = (x, y) => {
    let h = document.elementFromPoint(x, y);
    while (h?.shadowRoot) {
      const i = h.shadowRoot.elementFromPoint(x, y);
      if (!i || i === h) break;
      h = i;
    }
    return h;
  };
  const onScreen = (e) => {
    const r = e.getBoundingClientRect();
    return r.width > 1 && r.height > 1 && r.right > 0 && r.left < innerWidth && r.bottom + scrollY > 0;
  };
  const fixedUp = (e) => {
    for (let n = e, i = 0; n && i < 4; n = up(n), i++) if (getComputedStyle(n).position === "fixed") return true;
    return false;
  };
  // Layers on top of the page that no markup calls a dialog (meineschufa's
  // address modal is a fixed div): the outermost fixed box (absolute with
  // z-index ≥ 100) around what shows at the viewport centre, or at a point
  // around it if it covers ≥ 40 % of the viewport; with text, and not an
  // app shell that holds the page itself (<main>, most of its text).
  const overlays = () => {
    const W = innerWidth, H = innerHeight, out = [];
    if (!document.body || W * H < 2500) return out;
    const bodyLen = (document.body.innerText ?? "").length;
    for (const [fx, fy] of [[0.5, 0.5], [0.25, 0.5], [0.75, 0.5], [0.5, 0.25], [0.5, 0.75]]) {
      const chain = [];
      for (let n = hitAt(W * fx, H * fy); n && n !== document.body && n !== document.documentElement; n = up(n)) {
        const s = getComputedStyle(n);
        if (s.position === "fixed" || (s.position === "absolute" && Number(s.zIndex) >= 100)) chain.push(n);
      }
      for (const n of chain.reverse()) {  // the outermost first
        if (out.some((o) => inside(n, o) || inside(o, n))) break;
        const r = n.getBoundingClientRect();
        const area = Math.max(0, Math.min(r.right, W) - Math.max(r.left, 0)) * Math.max(0, Math.min(r.bottom, H) - Math.max(r.top, 0));
        if (area < W * H * (fx === 0.5 && fy === 0.5 ? 0.02 : 0.4)) continue;
        const len = (n.innerText ?? "").trim().length;
        if (!len || (bodyLen > 1500 && len > 0.6 * bodyLen) || n.querySelector("main, [role=main]")) continue;
        out.push(n);
        break;
      }
    }
    return out;
  };
  // The open layers on top of the page, the topmost last: dialogs, popovers,
  // drawers (fixed), overlays.
  const DRAWERS = "[class*=drawer i], [class*=offcanvas i], [class*=flyout i], [class*=sidesheet i], [class*=side-sheet i]";
  // Fixed side panels named sidebar/side-panel/slide-in, on top (z-index ≥ 100): obi.de's store finder (LC-06);
  // a site's own sidebar navigation sits lower.
  const PANELS = "[class*=sidebar i], [class*=side-panel i], [class*=sidepanel i], [class*=slide-in i], [class*=slideout i], [class*=slide-out i]";
  const panel = (e) => getComputedStyle(e).position === "fixed" && Number(getComputedStyle(e).zIndex) >= 100
    && (e.innerText ?? "").trim().length > 0;
  const layers = () => {
    let pop = [];
    try { pop = all(":popover-open"); } catch {}  // an older Chrome
    const out = [...all("dialog[open], [role=dialog], [role=alertdialog], [aria-modal=true]"), ...pop]
      .filter((e) => shown(e) && onScreen(e));
    // A persistent side navigation (YouTube's guide drawer inside role=navigation, DE-02) is page, not a layer;
    // neither is an invisible scrim.
    const sideNav = (e) => !!(e.closest?.("nav, [role=navigation], [persistent]") || e.querySelector?.("nav, [role=navigation]"))
      || getComputedStyle(e).opacity === "0" || !(e.innerText ?? "").trim();
    for (const e of all(DRAWERS)) if (shown(e) && onScreen(e) && fixedUp(e) && !sideNav(e)) out.push(e);
    for (const e of all(PANELS)) if (shown(e) && onScreen(e) && panel(e) && !sideNav(e) && !out.some((o) => inside(e, o) || inside(o, e))) out.push(e);
    for (const e of overlays()) if (!out.some((o) => inside(e, o) || inside(o, e))) out.push(e);
    // Amazon's basket flyout (#ewc-*, "Subtotal") stays beside the page: not a dialog (SD-02).
    return out.filter((e) => !e.closest?.("[id^=ewc-], [class*=ewc-]") && !/^ewc-/.test(e.id ?? ""));
  };
  // What an icon-only control shows: "close (icon)" for a close icon (by the
  // class, id or icon name of it or its svg/i/span), else its svg's title or
  // icon name as "<name> (icon)", else "".
  const CLOSE_ICON = /(^|[^a-z])(close|closer|dismiss|schlie(ss|ß)en|xmark|x-mark|times|cross)([^a-z]|$)/i;
  const iconName = (e) => {
    const parts = [e, ...(e.querySelectorAll?.("svg, i, span, img, use") ?? [])].slice(0, 8);
    const names = parts.map((p) => `${typeof p.className === "string" ? p.className : attr(p, "class")} ${p.id ?? ""} ${attr(p, "href")}`
      + ` ${attr(p, "xlink:href")} ${attr(p, "data-icon")} ${attr(p, "data-testid")} ${attr(p, "src").split("/").pop()}`).join(" ");
    if (CLOSE_ICON.test(names)) return "close (icon)";
    const t = norm(e.querySelector?.("svg title")?.textContent);
    if (t) return t + " (icon)";
    const m = names.match(/(?:icon[-_]|#)([a-z][a-z0-9-]{2,30})/i);
    return m ? m[1] + " (icon)" : "";
  };
  const absHref = (e) => { try { return new URL(attr(e, "href"), location.href).href; } catch { return attr(e, "href"); } };
  const unique = (sel) => { try { return all(sel).length === 1; } catch { return false; } };
  const selectorFor = (e, ref) => {
    if (e.id && unique("#" + CSS.escape(e.id))) return "#" + CSS.escape(e.id);
    const n = attr(e, "name");
    if (n) {
      const s = `${e.localName}[name="${n.replace(/["\\]/g, "\\$&")}"]`;
      if (unique(s)) return s;
    }
    return `[data-vf-ref="${ref}"]`;
  };
  // The text a person sees: the flat tree (shadow roots, slots), without
  // display:none, visibility:hidden, opacity:0, collapsed or off-page boxes,
  // and without what fields hold. An iframe text() tagged in this call
  // (data-vf-frame="<frameTag>:<n>") leaves a line "\u0001<n>\u0001" where it sits.
  // With `hidden`, also the text of what is hidden in there (display:none,
  // visibility:hidden, opacity 0, collapsed boxes, closed <details>), each
  // part on a line "[hidden] …"; never closed drawers off-screen, nor
  // screen-reader-only 1-px text.
  const SKIP = /^(script|style|noscript|template|head|meta|link|title|svg|textarea|select|option|iframe|canvas|object|video|audio)$/;
  const ICON_UP = /(caret|arrow|trend|triangle)[-_ ]?(up|upward|increase)|(^|[^a-z])(up|increase)[-_ ]?(arrow|caret|trend)/i;
  const ICON_DOWN = /(caret|arrow|trend|triangle)[-_ ]?(down|downward|decrease)|(^|[^a-z])(down|decrease)[-_ ]?(arrow|caret|trend)/i;
  const hiddenText = (n) => {
    const parts = [];
    const w = document.createTreeWalker(n, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
      acceptNode: (k) => k.nodeType === 1 && SKIP.test(k.localName) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT });
    for (let k = w.nextNode(); k; k = w.nextNode()) {
      if (k.nodeType === 3) parts.push(k.data);
      else if (k.localName === "br" || !getComputedStyle(k).display.startsWith("inline")) parts.push(" | ");
    }
    const t = norm(parts.join("").replace(INVIS, "")).replace(/(\s*\|\s*)+/g, " | ").replace(/^ ?\| | \| ?$/g, "").trim();
    return t;  // whole: a cut "[hidden]" line lost the rest of an accordion (LE-04)
  };
  const visibleText = (root, skip, frameTag, hidden = false) => {
    if (!root) return "";
    const out = [];
    const hide = (n) => {
      if (!hidden) return;
      const t = hiddenText(n);
      if (t) out.push("\n\u0002" + t + "\n");
    };
    const rec = (n, vis) => {
      if (n.nodeType === 3) { if (vis === "visible") out.push(n.data); return; }
      if (n.nodeType === 11) { for (const k of n.childNodes) rec(k, vis); return; }
      if (n.nodeType !== 1 || skip?.has(n)) return;
      const t = n.localName;
      if (t === "br") { out.push("\n"); return; }
      if (t === "input") { if (/^(submit|button|reset)$/.test(n.type) && vis === "visible") out.push(" " + n.value + " "); return; }
      if (t === "iframe" && frameTag) {
        const f = attr(n, "data-vf-frame");
        if (f.startsWith(frameTag + ":")) out.push("\n\u0001" + f.slice(frameTag.length + 1) + "\u0001\n");
        return;
      }
      // A caret/arrow/trend icon by a number carries its sign (a table's ▲ 3,2 % / ▼ 1,1 %, ME-04).
      if (vis === "visible" && /^(i|svg|span|img|em)$/.test(t) && !n.textContent.trim()) {
        const nm = `${typeof n.className === "string" ? n.className : attr(n, "class")} ${attr(n, "aria-label")} ${attr(n, "title")} ${attr(n, "data-icon")} ${attr(n, "alt")}`;
        const dir = ICON_UP.test(nm) ? "▲" : ICON_DOWN.test(nm) ? "▼" : "";
        if (dir && /\d/.test(n.parentElement?.textContent ?? "") && getComputedStyle(n).display !== "none") { out.push(" " + dir + " "); return; }
      }
      if (SKIP.test(t)) return;
      const s = getComputedStyle(n);
      if (s.display === "none" || s.opacity === "0" || s.contentVisibility === "hidden") return hide(n);
      if (hidden && vis === "visible" && s.visibility !== "visible") return hide(n);
      if (s.display !== "contents" && n !== document.body && n !== document.documentElement) {  // YouTube's body is 0 px high (DE-02)
        const r = n.getBoundingClientRect();
        if ((r.width <= 1 || r.height <= 1) && (s.overflow !== "visible" || s.clip !== "auto" || s.clipPath !== "none"))
          return r.width > 1 ? hide(n) : undefined;  // a collapsed accordion, not screen-reader text
        if (/absolute|fixed/.test(s.position) && (r.right + scrollX <= 0 || r.bottom + scrollY <= 0)) return;
        // A fixed box past the right or bottom edge can't be scrolled to (a closed drawer).
        if (s.position === "fixed" && (r.left >= innerWidth || r.top >= innerHeight)) return;
      }
      const cell = /^table-cell/.test(s.display), blk = !cell && !s.display.startsWith("inline") && s.display !== "contents";
      out.push(blk ? "\n" : cell ? " " : "");
      const closed = t === "details" && !n.open;
      const kids = n.shadowRoot ? n.shadowRoot.childNodes
        : t === "slot" ? (n.assignedNodes().length ? n.assignedNodes() : n.childNodes)
        : closed ? [...n.children].filter((c) => c.localName === "summary")
        : n.childNodes;
      for (const k of kids) rec(k, s.visibility);
      if (closed) for (const c of n.children) if (c.localName !== "summary") hide(c);
      out.push(blk ? "\n" : cell ? " " : "");
    };
    rec(root, getComputedStyle(root.nodeType === 1 ? root : document.body).visibility);
    return out.join("").replace(INVIS, "").split("\n").map((l) => l.replace(/[ \t\u00a0]+/g, " ").trim()).filter(Boolean)
      .join("\n").replace(/\u0002/g, "[hidden] ");
  };
  // Cookie banners: buttons that reject, or keep only what is necessary. Only
  // inside something that looks like a consent banner, so that a bare
  // "Ablehnen" elsewhere (an offer, a contract change) is never clicked.
  const REJECT = [
    /^(alle|alles) ablehnen$/, /^(reject|decline|refuse|deny)( all)?( cookies)?$/,  // hertz.de cookieconsent "Decline Cookies"
    /^alle (optionalen |nicht notwendigen |nicht erforderlichen |zusätzlichen )?cookies ablehnen$/,
    /^(optionale|opt|nicht notwendige|nicht erforderliche|zusätzliche|alle optionalen) (cookies )?ablehnen$/,
    /^(reject|decline) (optional|non essential|non-essential|additional|all optional) cookies$/,
    /^(nur|ausschließlich) (die )?(technisch )?(notwendigen?|erforderlichen?|essenziellen?|essentiellen?|funktionalen?)( (cookies|technologien|dienste|tools))?( (akzeptieren|zulassen|erlauben|verwenden|nutzen|speichern|auswählen))?$/,
    /^nur (erforderliches|notwendiges)( zulassen| akzeptieren| erlauben)?$/,
    /^(notwendige|erforderliche|essenzielle) (cookies|technologien|dienste) (akzeptieren|zulassen|erlauben)$/,
    /^(notwendige|erforderliche|essenzielle|essentielle) cookies$/,
    /^mit (den )?(erforderlichen|notwendigen) (einstellungen|cookies) fortfahren$/,
    /^(?=.*\b(only|use|accept|allow|permit)\b)(only )?((use|accept|allow|permit) )?(only )?(strictly )?(necessary|essential|required)( (cookies|technologies|services))?( only)?$/,
    /^(einwilligung|zustimmung) (ablehnen|verweigern)$/,
    /^(ablehnen|reject|decline|verweigern|nicht zustimmen|nicht einverstanden|ablehnen und schließen|ablehnen und weiter)$/,
    /^(weiter|fortfahren) ohne (einwilligung|zustimmung|zu akzeptieren|akzeptieren|cookies)$/,
    /^ohne (einwilligung|zustimmung|akzeptieren|zu akzeptieren|cookies) (fortfahren|weiter|fortsetzen)$/,  // ADAC (VE-08)
    /^(continue|proceed) without (accepting|consent|agreeing|cookies)$/,
    /^(hide|ausblenden)$/,
  ];
  // Reject buttons of the big consent platforms (OneTrust, Cookiebot, Didomi,
  // Usercentrics), by id.
  const REJECT_IDS = "#onetrust-reject-all-handler, #CybotCookiebotDialogBodyButtonDecline, #didomi-notice-disagree-button,"
    + " [data-testid=uc-deny-all-button], button[data-action=consent][data-action-type=deny], .ot-pc-refuse-all-handler"
    + ", #CybotCookiebotDialogBodyLevelButtonLevelOptinDeclineAll, #cmpwelcomebtnno, .cmpboxbtnno, #cmpbntnotxt, a.cmptxt_btn_no, [data-cookie-refuse], ._brlbs-refuse-btn, a._brlbs-refuse, .cmplz-btn.cmplz-deny, .cky-btn-reject, #cn-refuse-cookie, #cookie_action_close_header_reject, [data-cli_action=reject], .cm-btn-decline, .cn-decline, .osano-cm-denyAll, .iubenda-cs-reject-btn, #axeptio_btn_dismiss, [data-cookiefirst-action=reject], .didomi-continue-without-agreeing, .ccm--decline-cookies, #truste-consent-required, [data-tid=banner-decline], .moove-gdpr-infobar-reject-btn, #cookiescript_reject, #ppms_cm_reject-all, .shopify-pc__banner__btn-decline";
  // Saves the banner's toggles as they are: only clicked when all are off.
  const CONFIRM = /^((meine )?auswahl bestätigen|confirm (my )?selection|(auswahl|einstellungen|dienste|einwilligungen) speichern|save (services|settings|(my )?selection|(my )?choices|preferences|consents?))$/;
  // Usercentrics' first layer may have no reject: it sits in the second
  // layer that its settings button opens (starbulls.reservix.de).
  const MORE = /^(einstellungen|mehr|mehr optionen|mehr informationen|einstellungen anpassen|verwalten|einstellungen verwalten|settings|more|more options|more information|customi[sz]e|manage|manage settings)$/;
  const uc = (e) => { for (let n = e; n; n = up(n)) if (/^usercentrics-(root|cmp-ui)$/.test(n.id ?? "")) return n; return null; };
  const SETTINGS = /^(cookie[- ]?einstellungen|cookie[- ]?settings|cookie[- ]?präferenzen|datenschutz[- ]?einstellungen|privacy settings|cookies verwalten|manage cookies|cookie preferences|individuelle einstellungen|einstellungen oder ablehnen)$/;
  const ACCEPT = /akzeptier|zustimm|accept|agree|allow all|alle erlauben|alle zulassen|einverstanden/;
  const acceptsToo = (b) => !!b && /cookie|consent|einwillig|datenschutz|tracking|privacy/i.test((b.innerText ?? "").slice(0, 5000))
    && elements().some((x) => inside(x, b) && /^(button|a)$/.test(x.localName) && shown(x) && ACCEPT.test(label(x).toLowerCase()));
  const CTX_ATTR = /cookie|consent|gdpr|dsgvo|\bcmp|privacy|datenschutz|usercentrics|onetrust|didomi|truste|borlabs|cookiebot|klaro|sp_message|einwilligung|tracking/i;
  const CTX_TEXT = /cookie|tracking|consent|einwilligung/i;
  // The consent banner around e (or e itself, with `self`), or null.
  const bannerOf = (e, self = false) => {
    for (let n = self ? e : up(e), i = 0; n && n !== document.body && n !== document.documentElement && i < 30; n = up(n), i++) {
      const cls = typeof n.className === "string" ? n.className : attr(n, "class");
      if (CTX_ATTR.test(`${n.id} ${cls} ${n.localName} ${attr(n, "aria-label")} ${attr(n, "data-testid")}`)) return n;
      const s = getComputedStyle(n);
      const dialog = /^(dialog|alertdialog)$/.test(attr(n, "role")) || attr(n, "aria-modal") === "true"
        || n.localName === "dialog" || s.position === "fixed" || s.position === "sticky";
      if (dialog && CTX_TEXT.test((n.innerText ?? "").slice(0, 5000))) return n;
    }
    // A whole consent page (consent.google.com, consent.youtube.com) is the banner.
    return /^consent\./.test(location.hostname) && document.body ? document.body : null;
  };
  const banner = (e) => !!bannerOf(e);
  // The outermost banner box around b (its toggles may sit in a sibling part).
  const bannerTop = (b) => {
    let top = b;
    for (let n = up(b), i = 0; n && n !== document.body && i < 10; n = up(n), i++) if (banner(n) || CTX_ATTR.test(n.id ?? "")) top = n;
    return top;
  };
  // A consent-settings opener that is no banner (B-59): a CMP revisit button,
  // a footer "Cookie-Einstellungen" link, a javascript:Cookiebot.renew() link,
  // outside any lifted box. Clicking one opens the settings layer unasked.
  const OPENER_CODE = /Cookiebot\.(renew|show)|UC_UI\.show|showSecondLayer|ToggleInfoDisplay|__cmp\(|__tcfapi|_sp_\.|loadPrivacyManagerModal|klaro\.show|BorlabsCookie|CookieConsent\.(renew|show)|cmplz_|cookieyes|ccm19|CCM\.open|showConsent|openConsent|showCookie|openCookie|cookieSettings|consentmanager/i;
  const OPENER_CLS = /(^|\s)(ot-sdk-show-settings|optanon-show-settings|cky-btn-revisit|cky-revisit[\w-]*|cmplz-manage-consent|cmplz-show-banner|_brlbs-btn-cookie-preference|borlabs-cookie-preference|uc-privacy-button|cookiebot-renew|cc-revoke|ccm-settings-summoner|iubenda-cs-preferences-link|termly-display-preferences|osano-cm-widget)(\s|$)/i;
  const raised = (n) => {
    const s = getComputedStyle(n);
    return /^(fixed|sticky)$/.test(s.position) || (s.position === "absolute" && Number(s.zIndex) >= 100) || n.localName === "dialog"
      || /^(dialog|alertdialog)$/.test(attr(n, "role")) || attr(n, "aria-modal") === "true";
  };
  const opener = (e) => {
    const b = e.closest?.("a, button, [role=button]") ?? e;
    if (OPENER_CLS.test(attr(b, "class")) || b.id === "ot-sdk-btn" || attr(b, "data-cc") === "c-settings") return true;
    let lifted = false;
    for (let n = b, i = 0; n && n !== document.body && i < 25; n = up(n), i++) if (raised(n)) { lifted = true; break; }
    if (!lifted && (OPENER_CODE.test(attr(b, "onclick")) || (/^javascript:/i.test(attr(b, "href")) && OPENER_CODE.test(attr(b, "href"))))) return true;
    const foot = b.closest?.("footer, [role=contentinfo]");
    if (!foot) return false;
    for (let n = b; n && n !== foot; n = up(n)) if (raised(n)) return false;
    return true;
  };
  const press = (e) => {
    e.scrollIntoView?.({ block: "center", behavior: "instant" });  // eBay's sits below the fold
    const inner = e.shadowRoot?.querySelector("button, a[href], [role=button]");
    (inner ?? e).click();
    return label(e) || attr(e, "id");
  };
  const consent = () => {
    for (const e of all(REJECT_IDS)) if (shown(e)) return press(e);
    let best = null, rank = REJECT.length, confirm = null, more = null;
    for (const e of elements()) {
      const tc = e.textContent ?? "";
      if (tc.length > 120 || (!tc.trim() && !e.hasAttribute("aria-label") && e.localName !== "input")) continue;
      if (!(semantic(e) || e.localName.includes("-") || pointer(e))) continue;
      const l = label(e).toLowerCase().replace(/[^\p{L}\p{N} -]+/gu, " ").replace(/\s+/g, " ").trim();
      if (!confirm && CONFIRM.test(l) && shown(e) && banner(e)) confirm = e;
      if (!more && (MORE.test(l) || /^uc-(more-button|customize-anchor)$/.test(attr(e, "data-testid"))) && shown(e) && uc(e) && !opener(e)) more = e;
      // Any banner whose only other way out accepts (zecplus.de "Alles Akzeptieren"/"Einstellungen", OneTrust
      // "Cookie-Einstellungen"): its settings open the layer where everything optional can stay off.
      if (!more && (MORE.test(l) || SETTINGS.test(l)) && shown(e) && banner(e) && acceptsToo(bannerOf(e)) && !opener(e)) more = e;
      const i = REJECT.findIndex((rx) => rx.test(l));
      if (i < 0 || i >= rank || !shown(e) || !banner(e)) continue;
      best = e;
      rank = i;
    }
    if (best) return press(best);
    if (confirm) {
      const box = bannerTop(bannerOf(confirm));
      const toggles = elements().filter((t) => inside(t, box) && (t.matches("input[type=checkbox]")
        || /^(checkbox|switch)$/.test(attr(t, "role"))) && !t.disabled && attr(t, "aria-disabled") !== "true");
      if (toggles.length && !toggles.some((t) => checked(t))) return press(confirm);
    }
    // Opens the second layer once per document; the caller polls on.
    const mk = Symbol.for("vf-consent-more");
    if (!more || window[mk]) return null;
    window[mk] = true;
    return { opened: press(more) };
  };
  return { norm, attr, up, inside, elements, all, shown, pointer, isField, semantic, label, target,
           pointerOrigin, find, checkable, checked, kind, active, hitAt, absHref, selectorFor,
           visibleText, consent, banner, bannerOf, words, onScreen, fixedUp, overlays, layers, iconName, reachable };
}"""


def _vf(body, **args):
    """A page expression running `body` (with `return`) with VF, the page
    helpers, and each keyword argument as a JSON constant."""
    head = "".join(f"const {k} = {_json.dumps(v)}; " for k, v in args.items())
    return f"(() => {{ const VF = ({_LIB_JS})(); {head}{body} }})()"


# Rejects a cookie banner once it shows up, polling for 12 s from the
# document's start; once per document.
_CONSENT_POLL_JS = r"""(() => {
  const k = Symbol.for("vf-consent");
  if (window[k]) return;
  window[k] = "watching";
  const lib = (__LIB__);
  let n = 0;
  const tick = () => {
    let hit = null;
    try { hit = lib().consent(); } catch {}
    if (hit?.opened) n = Math.min(n, 16);  // a second layer opened: its reject comes next
    else if (hit) window[k] = hit;
    if (!hit || hit.opened) if (++n < 24) setTimeout(tick, 500);
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", tick, { once: true });
  else tick();
})()""".replace("__LIB__", _LIB_JS)
# WebAuthn off: Chrome's native passkey dialog never renders on the shop
# browser's display, yet takes all mouse and key input (Amazon, T03). Proxies
# keep the functions looking native.
_PASSKEY_JS = r"""(() => {
  const wrap = (obj, name, apply) => {
    const d = obj && Object.getOwnPropertyDescriptor(obj, name);
    if (d && typeof d.value === "function")
      try { Object.defineProperty(obj, name, { ...d, value: new Proxy(d.value, { apply }) }); } catch {}
  };
  const deny = () => Promise.reject(new DOMException("The operation either timed out or was not allowed.", "NotAllowedError"));
  wrap(window.CredentialsContainer?.prototype, "get", deny);
  wrap(window.CredentialsContainer?.prototype, "create", deny);
  wrap(window.PublicKeyCredential, "isConditionalMediationAvailable", () => Promise.resolve(false));
  wrap(window.PublicKeyCredential, "isUserVerifyingPlatformAuthenticatorAvailable", () => Promise.resolve(false));
})()"""
# Counts the page's fetch/XHR requests in flight (for text()'s "[loading: …]"
# line and tab.idle()); websockets and beacons don't count, a request older
# than 15 s (a long-poll) neither. Proxies keep the functions looking native.
_NETCOUNT_JS = r"""(() => {
  const K = Symbol.for("vf-net");
  if (window[K]) return;
  const st = window[K] = { live: new Map(), n: 0, last: performance.now() };
  const start = () => { const id = ++st.n; st.live.set(id, performance.now()); return id; };
  const end = (id) => { if (st.live.delete(id)) st.last = performance.now(); };
  try {
    if (typeof window.fetch === "function") window.fetch = new Proxy(window.fetch, { apply(t, self, args) {
      const id = start();
      try { const p = Reflect.apply(t, self, args); p.then(() => end(id), () => end(id)); return p; } catch (e) { end(id); throw e; }
    } });
    const send = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = new Proxy(send, { apply(t, self, args) {
      const id = start();
      self.addEventListener("loadend", () => end(id), { once: true });
      try { return Reflect.apply(t, self, args); } catch (e) { end(id); throw e; }
    } });
  } catch {}
})()"""
# [requests in flight (not long-polls), ms since the last one ended]; null without the counter.
_NETSTATE_JS = r"""(() => { const st = window[Symbol.for("vf-net")]; if (!st) return null; const now = performance.now();
  return [[...st.live.values()].filter((t) => now - t < 15000).length, now - st.last]; })()"""
# Runs in every document of a shop tab before the page's own scripts.
_NEWDOC_JS = _PASSKEY_JS + ";\n" + _NETCOUNT_JS + ";\n" + _CONSENT_POLL_JS

# goto()'s settle check: [body text length, whether a loading indicator shows
# in the viewport, document.readyState]: aria-busy=true, a progressbar, or a box whose class/id
# says spinner/loader/loading/skeleton/shimmer/progress and holds ≤ 40 chars
# of text (a checkout's step bar holds its step names).
_BUSY_JS = r"""(() => {
  const n = (document.body?.innerText ?? "").length;
  // Whole name parts only: "is-loading", "ld-spinner", "LoadingSpinner_x", "progress-bar",
  // never "nav-progressive-attribute" (amazon.de) or "lazyloaded".
  const NAME = /(^|[\s_-])(spinner|spinning|loader|loading|skeleton|shimmer|progress(-?bar|-?indicator|-?circle|-?ring)?)([\s_-]|$|(?=[A-Z]))|(Spinner|Loader|Loading|Skeleton|Shimmer|ProgressBar)(?![a-z])/;
  const NOT = /loaded|lazy|preload|download|upload/i;
  const shows = (e) => {
    const r = e.getBoundingClientRect();
    if (r.width < 2 || r.height < 2 || r.bottom <= 0 || r.right <= 0 || r.top >= innerHeight || r.left >= innerWidth) return false;
    for (let a = e; a && a.nodeType === 1; a = a.parentElement) {
      const s = getComputedStyle(a);
      if (s.display === "none" || s.visibility === "hidden" || +s.opacity < 0.05) return false;
    }
    return true;
  };
  for (const e of document.querySelectorAll("[aria-busy=true], [role=progressbar], progress, [class], [id]")) {
    const busy = e.getAttribute("aria-busy") === "true";
    if (!busy && !e.matches("[role=progressbar], progress")) {
      const name = `${typeof e.className === "string" ? e.className : e.getAttribute("class") ?? ""} ${e.id}`;
      if (!NAME.test(name) || NOT.test(name)) continue;
    }
    if (!busy && (e.innerText ?? "").trim().length > 40) continue;
    if (shows(e)) return [n, true, document.readyState];
  }
  return [n, false, document.readyState];
})()"""

# search()'s finder (run with _vf). OPEN=false: {box, what, seen, opener}: the
# best search field (marked data-vf-search="box"), else a search icon button
# that may open one ("open"). OPEN=true: {submit}: the box's search button ("go").
_SEARCH_JS = r"""
  const WORD = /search|such|find|recherch/i, NAMES = /^(q|s|k|kw|query|keywords?|term|searchterm|search_?query|ssearch)$/i;
  const mark = (e, v) => { e.setAttribute("data-vf-search", v); return `[data-vf-search="${v}"]`; };
  if (OPEN) {
    const box = VF.all('[data-vf-search="box"]')[0];
    if (!box) return {};
    let go = box.form?.querySelector("button[type=submit], input[type=submit], button:not([type])");
    for (let n = VF.up(box), i = 0; !go && n && i < 4; n = VF.up(n), i++)
      go = [...n.querySelectorAll("button, [role=button], input[type=submit]")].find((b) => VF.shown(b)
        && WORD.test(`${VF.label(b)} ${VF.attr(b, "aria-label")} ${VF.attr(b, "title")} ${VF.attr(b, "class")}`)) ?? null;
    return go && VF.shown(go) ? { submit: mark(go, "go") } : {};
  }
  for (const e of VF.all("[data-vf-search]")) e.removeAttribute("data-vf-search");
  const fields = VF.all("input, textarea, [role=searchbox]").filter((e) => VF.shown(e) && !e.disabled && !e.readOnly
    && !/^(hidden|checkbox|radio|submit|button|reset|password|email|tel|number|date|file|image|range|color)$/.test(e.type ?? ""));
  const score = (e) => {
    const attrs = `${e.name ?? ""} ${e.id ?? ""} ${VF.attr(e, "placeholder")} ${VF.attr(e, "aria-label")} ${VF.attr(e, "title")}`;
    let s = 0;
    if (e.type === "search" || VF.attr(e, "role") === "searchbox") s += 3;
    if (WORD.test(attrs)) s += 2;
    if (NAMES.test(e.name ?? "") || NAMES.test(e.id ?? "")) s += 2;
    if (e.closest?.("[role=search], form[action*=search i], form[action*=such i]")) s += 1;
    const r = e.getBoundingClientRect();
    if (r.top < innerHeight && r.bottom > 0) s += 0.5;
    return s;
  };
  let best = null, top = 0;
  for (const e of fields) { const s = score(e); if (s > top) { best = e; top = s; } }
  const seen = fields.slice(0, 8).map((e) => (VF.label(e) || VF.attr(e, "name") || e.id || e.localName).slice(0, 40));
  if (best && top >= 2)
    return { box: mark(best, "box"), what: (VF.attr(best, "placeholder") || VF.label(best) || best.name || best.id).slice(0, 60), seen };
  const opener = VF.all("button, a, [role=button], summary").find((b) => VF.shown(b) && VF.label(b).length < 30
    && /search|such|lupe|magnif/i.test(`${VF.label(b)} ${VF.attr(b, "aria-label")} ${VF.attr(b, "title")} ${VF.attr(b, "class")} ${b.id}`));
  return { seen, opener: opener ? mark(opener, "open") : null };"""

