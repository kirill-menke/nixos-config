# The tool omp agents use for web tasks: a shared invisible Chrome they drive
# (the shop browser, ShopTab), and the vault fill, which types a Vaultwarden
# secret into a page without the value reaching the chat, a log, a file or a
# command line. ./vault.nix installs it as ~/.local/share/vault-fill/vault-fill.py.
#
# In a Python eval cell (a cell holding only this line):
#   %load ~/.local/share/vault-fill/vault-fill.py
#
# One API for every web task: ShopTab, a tab of the shop browser. That is a
# normal Chrome on an invisible X display (Xvfb), driven over a bare DevTools
# connection, with the agents' own profile, so logins persist; bot checks
# (Akamai on adidas.de, …) let it through. Agents share it: every kernel gets
# its own windows, and the last one to call shop_close() quits Chrome.
#   tab = await shop_tab("https://www.adidas.de/")  # a new tab; nothing shows on screen
#   await shop_close()           # closes this kernel's tabs; the last one out quits Chrome
#   await shop_close(all=True)   # quits Chrome even while other agents use it (agent_tab
#                                # needs that); tabs of ended kernels never keep it open
#   shop_tabs()                  # every open tab: id, title, url, owner ([] if not running)
#   await shop_tab(target="paypal")   # attach to an open tab (id, or part of title/URL);
#                                # never one another live agent owns
#   await shop_map(urls, fn)     # fn(tab) per URL in up to 3 own tabs, in URL order
#                                # (an item is the exception if that URL failed; failures
#                                # are retried once, then printed "[shop_map: N of M failed: …]")
#                                # a bug in fn (the same AttributeError/NameError/TypeError/KeyError from fn's
#                                # code on 2 URLs before any succeeded) stops the run: raised, naming both URLs
#   await shop_map(urls, fields={"price": ".price", "sku": "meta[name=sku]@content"}, required=[…])
#                                # → [{url, price, sku, missing}] (fn's dict merged in);
#                                # prints "[shop_map: N pages; 1 with empty price (<url>)]"
#                                # a required css field still empty is re-read while the page renders (≤ 8 s, 1 s calm ends it)
#   await drive_time("Seebruck", "Prien am Chiemsee")   # Google Maps in a tab of its own:
#                                # {minutes, km, route, alternatives, url}; never a public router; a Maps
#                                # "Meinten Sie" takes the first suggestion, says so, adds "picked"
#   profile_grep("Kennzeichen")  # profile facts: [{file, line_no, line}] of the owner files;
#                                # IBANs/cards masked (DE••…••1234, ••••1234; reveal=True only to type
#                                # one into the task's own form); ≤ 15 rows per call (limit=), ≤ 30 distinct rows
#                                # per cell over all calls (limit=None: all); prints "N more … files=[…]";
#                                # never grep ~/Documents/agents/profile (sources/ and inbox/ are off limits)
#
# Hang guard: text/observe/links/cards/url/title/page_json give up after
# timeout=20 s, stop the page's script and raise TabStuck; that tab then fails
# fast until goto() or shop_tab(target=tab.id). eval(timeout=60) too. Give an
# eval cell a timeout ≥ 3× the tool timeout, and one risky call per cell: a
# cell that runs out wipes the kernel.
# BrowserRestarted (a ConnectionError): the shared browser restarted or quit,
# or the tab is gone; the message says when. Open the tab again with
# shop_tab(url) and go on (logins persist); shop_tab(target=<that id>) says so too.
#
# Task clock: from 1 min after the TASK-START cell's T0, the first tab call of each cell prints
# "[task 2.4 of 8 min]"; stop for budget only when it says ≥ 7 min. (vf_task_start("<ID>") /
# vf_task_end("<ID>") print the markers and set the clock too, once the transcript tools accept them.)
#
# ShopTab cheat-sheet (all async; selectors are CSS and reach into open shadow roots):
#   goto(url, wait="text")       DOMContentLoaded (not load), then without wait: until the text
#                                stops changing and no spinner shows, ≤10 s; rejects cookie banners, prints the
#                                site note. HardRule: wallet.google.com, myaccount…/device-
#                                activity, amazon …/ap/cvf/accountrecovery are blocked for
#                                every tab/popup/frame; Google's phone challenge raises: stop
#                                goto on ERR_HTTP2_PROTOCOL_ERROR (sportscheck.com) loads that host over HTTP/1.1
#                                outside Chrome for the rest of the tab and retries once (it says so)
#                                a www/apex host with no TCP connect in 8 s (or refused / no DNS) is tried once
#                                as its twin (www.crm.de ↔ crm.de) and says so; both dead: one error naming both
#                                the 4th goto() on one tab in one cell raises SerialGoto before navigating:
#                                ≥ 4 URLs → one shop_map(urls, fn, concurrency=3); goto(url, serial=True) when the
#                                pages must load in order (shop_map's own and library gotos don't count)
#                                a page showing only header/nav/footer (< 300 chars besides, an empty <main> or
#                                large same-origin iframe) gets ≤ 8 s more to render its content (B-71)
#   wait_for("text" | ["a", "b"], selector=, url= (one or a list), gone=)   → the text that matched; also
#                                in iframes and in shown <select>/dropdown/overlay option lists (says so);
#                                url= a substring of scheme+host+path (the query too only if it has ?/#)
#                                or a regex of the whole URL; gone= a list: all. TimeoutError says what the
#                                page shows + its line closest to the text; after 8 s already while the page
#                                is a login/2FA/"Konto schützen"/captcha step url= doesn't name.
#                                Without timeout= a text-only wait ends after ≥ 5 s once the page has loaded and
#                                been quiet 3 s (no request, nothing changing) without it, naming the closest
#                                line (a guessed wording); timeout=N waits the full N s, but N > 60 still ends once
#                                the page has been quiet 20 s without it (strict=True: the full N); goto(wait=) alike (30 s),
#                                and shop_tab(url, wait=) / shop_map(wait=) too
#                                SiteError (a TimeoutError, .url) in ~2 s when a site error shows that wasn't
#                                there when the wait began ("Das hat leider nicht geklappt", "Tut uns Leid",
#                                "Page Not Found", "502 Bad Gateway"); goto(url, wait=) too, on the new page
#                                url(), title(), wait_load()
#   url(), eval(), observe()/links() hrefs, text(), plain fetch_text()/pdf_text() (no tab.), errors: one-time-link tokens
#                                (?token=, ?code=, ?sig=, magic-link paths …) show as ••• (•••2, … for more links
#                                that look alike); goto(), fetch_json(), fetch_text(), pdf_text(), http_json(),
#                                shop_map(), shop_tab(target=) take such a URL as it is and use the real one
#   idle(timeout=10, quiet=1.0)  → True once no fetch/XHR ran for quiet s; text()/observe() end with
#                                "[loading: N requests in flight …]" meanwhile. Before writing
#                                "none"/"no X": await tab.idle() then re-read
#                                (long-polls don't count: a request open 10 s without an answer, and its endpoint)
#   text(selector=None, limit=None)   visible text, uncut unless limit= (then it says
#                                so); open dialogs, drawers, popovers come first as
#                                [dialog] parts, iframes (also cross-site) where they
#                                sit as [frame <host>] … [/frame], the tab panel as
#                                [tabpanel]; text("main") skips nav and footer
#                                last line "[page count: '345 Treffer' | page 1 of 18]" when the page states one;
#                                a word your cell looks for that the text lacks but the page hides prints
#                                "[find: 'X' isn't in this text, but the page has it N× (…)]" (once per word and
#                                cell); "[empty: 'Heading' — its section shows nothing yet (lazy?): scrolled to it]":
#                                await tab.idle(), then text() again before "not on the page"
#                                "[title: …]" before the text when the page title has words it lacks (a grade, a
#                                label only the title gives); "[text: only header/nav so far (N chars of main
#                                content) …]": the page may still be rendering: idle() and re-read; on such a page
#                                or behind a gate ("Überprüfen Sie Ihr Konto", "Standortdienste", "sign in to see")
#                                "[data: N JSON responses since load: <call>{key,key.sub,…} · … — tab.find('<term>')
#                                reads them]": the fact may be in data the page already loaded: find() it
#   find("Homespot", "Router", regex=False)   every place the page holds them: visible, hidden (collapsed
#                                accordion / tab panel / display:none, with the label hiding it), <select>
#                                options, attributes (alt/title/aria-label/data-*/value), page JSON (ld+json,
#                                __NEXT_DATA__, noscript, __NUXT__…) and the fetch/XHR JSON bodies since load,
#                                frames too → [{term, where, snippet}] (where 'xhr <url> a.b[0].c' = the path
#                                for fetch_json/page_json); prints them, each summary ending "on page '<title>' <url>"
#                                (is that still the page that held the fact?). Run it before any "not shown"/Missing
#   html(css=None)               outerHTML of the page / first match (data only the markup holds); masked like text()
#   observe("filter")            visible controls (those in open dialogs first): ref,
#                                selector, text, tag, role, id, name, placeholder,
#                                disabled, checked, href, card; the filter matches all
#                                of those; observe() = all. A button in a button with
#                                the same text is listed once. Then the controls of
#                                the iframes on show, each with frame: "<host>"
#   fields("filter", frame=)     read a form back: [{label, selector, value, kind, ref}]
#                                (frames too); select = chosen text, checkbox True/False;
#                                password (type=password / shown as dots)/vault-filled "•••", card "•••• 1234",
#                                CVV "•••"; + required: True, invalid: "<error text>" where they apply; unset: True
#                                for a select / custom select (s-select, MUI, react-select) on "Bitte auswählen"
#   fields(problems=True)        the required-but-empty, invalid and unset fields: why "Weiter" stays disabled
#                                or does nothing (unset selects count even when the page doesn't mark them required)
#   fill({"Vorname": "Kirill", "Anrede": "Herr", "#email": "…", "AGB": True})   a whole form in
#                                one call (selects/radios first); → fields() of them + {key, problem};
#                                reads every text field back at the end: {key, field, wanted, got,
#                                problem} (printed) when it shows something else; type=date takes
#                                "23.05.1998" (→ ISO); a label in the page's form and in a footer/newsletter
#                                box means the form's; several left: the one with the other keys' fields
#                                react-select/MUI/ng-select… fields go through choose() (never typed); a tel
#                                field's own "+49" isn't doubled; an autocomplete list left open is closed
#                                "PLZ *"/"Telefon (optional)" match "PLZ"/"Telefon"; a key no field has names the page's
#                                closest labels, and the NEXT click() on that page raises once ("fill() left 'kWh/Jahr'
#                                unfilled"): fix the key (fields()), or click again; a form shown 3×: copy 1, said
#   search("query")              the site's own search box → {url, text} of the result page
#                                (+ note, printed, when it used the header's site-wide box while the page
#                                has fields of its own; never a skip link; textareas/Wunsch/PLZ fields count);
#                                no box: the error names the site's search URL (GET search form / OpenSearch);
#                                rejects/hides a cookie banner first; a "Wohin"/"Campingort" prompt button counts
#                                the 4th submit (search / type(enter=True) / press("Enter")) on one tab in one
#                                cell raises SerialGoto: shop_map over the site's search URLs; serial=True
#   Frames: click/type/set_value/select/choose/type_code/wait_for(selector=)/vault_fill
#                                look in the page, then in its iframes (cross-site and
#                                nested too; says "in frame <host>"); frame="host" |
#                                "url part" | "iframe css" looks in that one only.
#                                frames(): [{host, url, cross_site, box, visible}]
#   click("css" | text="…" | ref=N, within="words of its card", force=True)
#                                within= takes the smallest card holding all those whole words (any order;
#                                a match in an open overlay first; a "card" holding another match is a list),
#                                raises if two cards hold them; says which card. text= prefers a match in an open dialog/popover, then
#                                whole words (text only inside a longer word, "Weiter" in "WEITERE":
#                                raises naming them), equal > starting with it > containing it,
#                                buttons/links over containers, aria-label/title only last; never an
#                                aria-only match that adds verwerfen/remove/löschen/… (raises; text= = that whole aria-label clicks it).
#                                Case and CSS uppercase never count ("GRÖSSE" = "Größe"); a bare price text ("€31.99")
#                                clicks its card's one Select/Auswählen button and says so.
#                                A disabled button raises at once naming the empty required fields;
#                                a cover that stays 3 s raises (a spinner is waited out)
#                                Order: open dialog → a frame's match (embedded widget) → the page; equal
#                                matches: the first in reading order, said "(1 of N equal matches …)". A
#                                label around a radio + another button: the radio. Waits ≤ 5 s for the XHRs
#                                it started, then ≤ 1 s for a late confirm layer ("it opened a dialog: …").
#                                Not found: raises after 3 s once the page is quiet 2 s (else timeout=),
#                                naming the closest elements ("closest: select#city-select")
#                                A submit/"Weiter" whose click changes nothing (or that is disabled) adds
#                                "; fields(problems=True): 'Ort' combobox unset (required)"
#                                A custom widget whose mouse click changed nothing gets the press's pointer events by
#                                script (menus that open on pointerdown), then element.click(); a menu toggle (aria-haspopup,
#                                aria-expanded="false") that opened nothing gets them too, else "(its menu … didn't open)".
#                                A click whose effect is late or value-only (a quantity set by script, a counter after
#                                its XHR, a tab after 1 s) gets no second click and says so ("(it changed the value of
#                                'qty')", "(it opened a window …)"); "nothing on the page changed" only after ≤ 2 s more.
#                                A ui-state-disabled/…-disabled day or cell raises (force=True clicks anyway); within=
#                                prefers the card holding it as one phrase; a hidden radio's CSS clicks its label;
#                                a popup that closes itself after the click: "… closed itself: go on in the tab that opened it"
#   overlay_hide(css=None)       display:none on what covered the last click (or css=, or the box/iframe
#                                over the viewport, chat widgets); nothing clicked → "hid div#chat (…)"
#   click_popup(…)               click, then the window this tab opened, as a ShopTab (one that closes itself at
#                                once, an OAuth popup: prints so and returns this tab)
#   drag((x1, y1), (x2, y2), steps=15) | drag("css", to=(x, y))   held-button drag
#                                in viewport CSS px (sliders, sortable lists)
#   type("css", "text", enter=True)    plain text, never a secret (that's vault_fill);
#                                reads it back, retypes key by key if the field differs; "css" may be the
#                                field's label/placeholder ("PLZ / Ort"); a fixed "+49" is kept, not doubled
#                                inserted text gets one keyup (pages that enable things on keyup)
#   set_value("css", "03.11.2026")     value + input/change: readonly date fields
#   pick_date("Abreise" | "css", "2026-12-19", fmt=None)   any date field: jQuery UI (hasDatepicker) via setDate
#                                + its onSelect, read back by getDate (outside minDate/maxDate: raises naming them);
#                                else the value in the field's own format (type=date ISO, flatpickr, placeholder TT.MM.JJJJ,
#                                a format in its label "(DD/MM/YYYY)", its value "Dec 10th 2026"; fmt="DD.MM.YYYY" forces one;
#                                one the field calls invalid doesn't count), and when the page rejects that, opens the field's
#                                calendar and clicks the day (react-datepicker, vue2/shadow roots, flatpickr on a wrapper, li day
#                                cells, paging ("❯", icon chevrons) or scrolling months ≤ 24). Reads it back; raises
#                                when the field doesn't show the date. pickadate (.picker__input): its picker API, the hidden
#                                submit field must fill. No input: a div/button that opens a calendar ("Abholtag") or the
#                                heading over an inline month grid ("Ab wann") works too; a picker's Apply/Confirm is clicked
#   pick_range("2026-12-10", "2026-12-19")   start and end day in an open calendar (one field: pick_date);
#                                day cells by data-date/aria-label date; pages months (also "❯"/chevron/"chevron_right" icons,
#                                a calendar in a dialog); a disabled day raises; then the picker's own Apply/"Confirm selection"
#   keys("03.11.2026")           key events into the focused element: date pickers,
#                                contenteditable; returns what that field shows then
#                                (check it: self-advancing fields can lose a key)
#                                press("Enter"|"Tab"|"Escape"|"End"|"Home"|
#                                "ArrowLeft"|"PageDown"|"Delete"|"Ctrl+A"|"Control+,"|"Shift+Tab"|…)
#   type_code("input.otp", code) one character per input of split code fields; boxes that only draw
#                                the code → the page's input[autocomplete=one-time-code]; reads back:
#                                raises "typed 6 characters, but the field shows 0: the real input is …";
#                                one box of ≥ 4 numbered ones (#pin-code-input-0…5) → one character into each
#   select("css", "label" | index=N)   a native <select>, by option text; options that load on focus are waited for; reads it back; then blurs it and waits ≤ 1.5 s for the fields its choice loads
#   choose("Anrede" | "css", "Herr")   a custom dropdown (react-select, MUI, ng-select, select2, input+list):
#                                mouse press on its control; an autocomplete (mat-autocomplete): input cleared, first letters typed;
#                                else ArrowDown, else its first letters; the exact option; reads the control back (typed + Enter, or
#                                ArrowDown to it + Enter, if the click didn't take); slim-select/select2: their hidden <select>;
#                                no such option: raises listing ≤ 12 options seen
#   links("filter", regex=False, limit=200)  [{text, href}] of the visible links, one row per href;
#                                regex=True: a Python regex (re.I) on "text href"; more than limit: prints
#                                "[links: N more …]"; "[links: N rows; the page says M ('…')…]" on another
#                                exact count (collect() too)
#   nav_links(match=None)        every header/nav/footer/account-menu link (opens collapsed menus: hover, a click
#                                only on aria-expanded/aria-haspopup toggles; never anything saying log out/abmelden/
#                                ausloggen/delete/kündigen: "not touched: …"; follows one off-site "Online Shop"/SSO link)
#                                → [{area, label, url, hidden?, via?}]; prints them by area once per site.
#                                Before "the site has no page X": run it and quote what it listed
#   cards("css" | "href pattern")      [{text, href}] per result card (text + aria-labels
#                                of icons: star ratings; href: the link that matched)
#   load_all("Mehr anzeigen" | "css", item="css")   clicks "load more" until the item
#                                count stays put (also while the button hides); count;
#                                load_all(None, item=…, container="css") scrolls an endless list (or that box) instead
#   collect("css", container=None)   a virtualized list (rows replaced while scrolling):
#                                scrolls its box step by step → every [{text, href}] once; rows in several scroll
#                                boxes (sidebar + main): only the largest box's, printout names the others'
#                                container=; all=True: every match. A grid's aria-rowcount/aria-setsize short
#                                at the end → a second slower pass, then "the grid says 50 → incomplete: rows …"
#   collect("css", fields={"price": ".price", "stars": ".stars", "id": "@data-id",
#                                "eur": lambda r: re.search(r"([\d.,]+) €", r["price"])}, required=[…])
#                                → rows also get the fields + "missing" [the required ones that are
#                                empty; default: all]; prints "…; 2 with empty price (rows 4, 17)".
#                                css: first visible match's text (aria-label if none), "css@attr" an attribute
#   count_proof(rows | css=".row")   before reporting a list, a total or "none": the page's own count
#                                ("1–20 von 45", "345 Treffer", "12 Bestellungen", "Keine Treffer" = 0) vs. the rows read →
#                                {source_count, read, complete, evidence, more, note}; prints one line for the report
#   capture(r"/api/", tab.click(text="Suchen"))  JSON the page fetched meanwhile (JSONP too; none:
#                                prints the fetch/XHR URLs it saw); raw=True also non-JSON: {type, text} or
#                                {type, body: bytes} (protobuf; bytes NOT masked: print only the fields you need)
#                                a request that sent a body has "post_data" (JSON parsed / form {name: value} / text; masked)
#   download("css" | "link text" | text=/ref=…, timeout=30) | download(url="…")   → {path, name, size,
#                                mime}: the file in a 0700 dir of this kernel (never ~/Downloads), under the
#                                site's name; a file the page builds itself (Blob: PayPal's CSV) too.
#                                A selector/ref that is a container clicks the one download link in it (several:
#                                ValueError naming them as download('[data-vf-dl="N"]')); a click that starts
#                                nothing (no download/request/window) fails after 5 s, naming the page's buttons
#                                an error dialog/alert after the click ("Wir entschuldigen uns", "Fehler") raises at
#                                once quoting it; a new form with empty fields (an address step) raises after 5 s
#                                naming them; Blobs: <a download> data: URLs, FileSaver, fetch().blob() files too
#                                Deleted by shop_close(), not by close(); read it with pdf_text(path)/open()
#   Masked in whatever a tab method, http_json, fetch_text or pdf_text returns (text, observe,
#                                fields, eval, capture, …): IBAN → "DE••…••1234", card number →
#                                "••••1234", JWT / Bearer and credential values (…token, …secret,
#                                api_key, password, session …) → "•••(masked N chars)"; CSRF tokens,
#                                page tokens, ids, cursors stay. Report IBANs/cards as shown.
#                                In text: a password after "Passwort/Kennwort/password/PIN …: / lautet
#                                / is" → "••••"; a key under an "API key/token/secret" label →
#                                "•••(masked key …abcd)"; a login/activation code after its label
#                                (Freischaltcode, Rezeptcode, Aktivierungscode, PIN, TAN, "Code:" …,
#                                also on the next line or in a table cell, OCR too) → "••••(len N)".
#                                A login code next to "is your … code" / "Your … code is:" → "••••••", also
#                                in other languages ("验证码为：X", "認証コード：X", "Ваш код подтверждения: X" …);
#                                on a webmail page (Outlook web, Gmail, GMX …) every code mail_texts
#                                would mask (list rows, previews, reading pane) → "••••••". Read
#                                codes with mail_code()/fill_mail_code(), never from the inbox page.
#                                Never unmask them (eval/DOM reads included).
#   fetch_json(url)              GET (or method=, body=, json=obj → POST) from the page, with its cookies;
#                                another origin's GET that CORS blocks is fetched once via http_json (no cookies; says so)
#                                reveal_keys=["key"]: values under those JSON keys come unmasked (a public config
#                                key the next request needs; credential keys refused; pass it on, never print it)
#   page_json(keys=["price"])    embedded state: __NEXT_DATA__, ld+json, __NUXT__, …
#   logged_out()                 "Sitzung beendet"-like text on the page, else None
#   captcha()                    hCaptcha/reCAPTCHA/Turnstile: clicks the checkbox;
#                                {kind, state, prompt, image, tile, left}; "challenge":
#                                `read` image (4 shots 0.8 s apart, with grid), then
#   captcha_click([(x, y), (x1, y1, x2, y2) …], then="next"|"verify"|"skip")  → the same
#                                dict (4-tuples drag, in a tile); stop at
#                                "blocked" (3 failures/30 min per site). Friendly
#                                Captcha: waits, never clicks (wait_captcha() too).
#                                state "invisible": an invisible reCAPTCHA/v3 badge or hCaptcha, no challenge:
#                                just submit the form; "solved" + next=[…]: no token, but the page went on
#                                (a code field / "code sent" / another page)
#   captcha_image("#captcha_img")  → PNG/JPG path of an inline image captcha (data: URL decoded, canvas, else
#                                the element's screenshot); deleted by close()
#                                Cloudflare's "Just a moment… / Performing security verification" page:
#                                {kind: "cloudflare", state: "managed", passed, waited, note} after ≤ 15 s
#                                waiting, its checkbox if one shows, one reload; passed False: stop retrying
#   dismiss_consent()            reject / necessary-only on a cookie banner (CMP reject ids first:
#                                Cookiebot, consentmanager, OneTrust, Usercentrics…; Sourcepoint frames; runs
#                                by itself on every page; never accepts tracking)
#   close_dialog()               nothing open: does nothing ("nothing to close"; never a footer "Cookie-Einstellungen"
#                                link); else the top dialog's close/decline control, else Escape; a cookie banner
#                                without reject: its settings → optional toggles off → save the selection;
#                                a banner with neither (only "Alle akzeptieren"): hidden, nothing accepted;
#                                a layer that stays, a full-page popup iframe, a chat window: hidden (says so)
#                                an open date picker: its Apply/Confirm, never Cancel/×/Escape (they drop the picked dates)
#   dialog(accept=False)         answer a JS confirm/prompt that blocks the page
#   eval("js expression") / eval("(sel, n) => …", "a", 3) (args: JSON), close()   screenshot(selector=None) → PNG path (`read` it; selector: one element; deleted by
#                                close()/shop_close(); never `rm /tmp/shop-*.png`: others' files)
#
# Without a tab: await pdf_text(url_or_path, tab=None) (a tab: with its session;
# OCR for scans and image files; its temporary download is deleted afterwards).
# fetch_text(url, links=True), http_json(url, reveal_keys=None) (JSONP cb(…) / ")]}'" stripped), doh("kirill.es", "NS"),
# (gzip/deflate/zstd answers are decompressed, even unasked gzip; brotli raises); fetch_text asks caches for a fresh copy (no-cache)
# vault_find, vault_list, site_note and shop_tabs() are plain calls that run at once;
# `await` in front of them works too. `read` extracts PDFs too; `grep` does
# not. Per site, shop_map, fetch_json and pdf_text make at most one request
# per second. Mail rows (mail_search, mail_scan & co.) and their keys: mail.py's own cheat-sheet.
#
# - click(text=…) finds the nearest clickable thing around that text (links
#   split into spans, custom elements, cursor:pointer cards). It scrolls the
#   target into view, refuses when something covers it (after 3 s; force=True
#   clicks it by script anyway, overlay_hide() hides the cover), and when the
#   mouse click changed nothing, sends the press's pointer events (custom
#   widgets), then clicks it by script (element.click()). A
#   visible checkbox, radio or switch that stays as it was (not a link/pill
#   wrapping a hidden one) raises an error; errors name the page's title and URL.
# - observe() refs and their [data-vf-ref] selectors last until navigation (a
#   later observe() keeps the numbers; a frame's refs end when that frame
#   navigates); `selector` (#id, [name=…]) lasts longer. When the page redrew
#   the element (an autocomplete list), click/type/set_value(ref=N) find it
#   again by its text if that is unique.
# - The first time a kernel loads a site, its note from
#   ~/Documents/agents/memory/sites/<domain>.md is printed: read it. To read it
#   before choosing the first URL: brief("otto.de") prints it together with the
#   site's vault entries and its accounts-registry lines (one call for all three).
# - Passkeys are switched off in every shop tab (Chrome's passkey dialog is
#   invisible here and would swallow all input). A popup's first page runs
#   before click_popup can switch them off there: if it asks for a passkey,
#   reload it (goto).
#
# Secrets go only through the vault:
#   vault_find("amazon")      # entries by name, user, folder, URI; no secrets
#   vault_list()              # every entry: name, user, folder
#   await vault_fill(tab, "#ap_email", "amazon.de", "username", submit="#continue")
#   await vault_fill(tab, "#ap_password", "amazon.de", submit="#signInSubmit")
#   await vault_fill(tab, "input[name=otp]", "amazon.de", "totp", submit="Enter")
#   await vault_fill(tab, "#ci-otpCode-0", "paypal.com", "totp")  # split boxes: name the first; it reads them back,
#       # types one digit per box when the first didn't pass the code on, raises "only k of 6 hold one character each"
#   await vault_fill(tab, "#pw", "paypal", user="kirill@…", folder=None)  # user=/folder=
#                             # pick one of several entries with that name
#                             # (several entries, none picked → ValueError "pass user=<one of: …>")
#   pp = await tab.click_popup("iframe[title=PayPal]", at=(0.25, 0.5))
#   await vault_fill(pp, "#password", "<entry>", submit='button[name="action"]')
#   await vault_fill(tab, "#email", "ryanair.com", "username")  # in an iframe too (frame=)
# The secret goes from rbw straight into the page, in a child process, so it
# never passes through this kernel. observe() and text() never show what
# fields hold, but screenshots and eval() do: give the last fill of a form a
# `submit`, and inspect the page only after it. If the form is still there 5 s
# after the submit (a failed login, a declined card), the fields filled from
# the vault are emptied again and vault_fill returns the error text the page
# showed. Usernames are no secret: they stay, so two-step logins keep them.
# Login fields go only into a document of the entry's site: in an iframe (also
# the focused field of selector=None), that iframe's own site counts.
# Brand login hosts count as the entry's site (auth.lastminute.com for lastminute.*, login.live.com /
# login.microsoftonline.com for Microsoft, account.booking.com, id.atlassian.com); others need any_site=True.
# A browser restart during vault_fill raises BrowserRestarted (open the tab again; logins persist).
# The shop browser renders WebGL on the CPU (SwiftShader): map widgets (store finders, outage maps) work.
# `submit` clicks the first visible match: on a form with several submit
# buttons, pass a precise selector, or submit=None and then click(text=…).
# Only one kernel logs in to a site at a time: while another agent's login
# runs (until 30 s after its submit), vault_fill waits (≤ 60 s), reloads the
# page, and when the field and every other login field are gone, returns
# "not filled: … probably logged in now" instead of filling.
#
# agent_tab (omp's Puppeteer browser) is legacy, for the few things only
# Puppeteer does; bot-protected sites answer it with 403. It uses the same
# profile, so it can't run while the shop browser runs (shop_close() first).

