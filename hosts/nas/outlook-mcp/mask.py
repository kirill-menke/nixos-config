"""Masks login codes and one-time-link tokens in Outlook MCP answers.

mux.py (the shared server's clients) and gate-filter.py (the public gate's
server) pass every answer through mask_reply, so an agent reading mail never
sees a login code or a one-time link's token. The rules are mail.py's (the omp
mail tool, ../../pc/mail.py): a code is what its code reader would take, masked
as ••••••, with every other appearance of it in the same mail; one-time-link
query values become •••. Ids, @odata links (paging) and file bytes stay as
they are.

Mirrored code: the part between the MIRROR markers is a verbatim copy of those
definitions in mail.py (which runs as a %load-ed file on the PC and can't import
this one). After changing either, check that they match:

    python3 mask.py --check-sync ../../pc/mail.py
"""

import bisect as _mbisect
import html.parser as _mhtmlparser
import json as _mjson
import re as _mre

# MIRROR START: mail.py's masker and the HTML-to-text it uses for html bodies.

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

# MIRROR END


# The opt-in for raw output: params._meta[REVEAL] = true on a tools/call
# request. Only mail.py's fill_mail_code sends it (the code reader, which types
# the code into a page and never prints it).
REVEAL = "vault-fill/reveal"
# Answers that hold no mail (the server's own metadata) stay as they are.
UNMASKED_METHODS = frozenset(
    {"initialize", "ping", "tools/list", "prompts/list", "resources/list", "resources/templates/list"}
)
# MCP-level keys whose values are no mail text: metadata and file bytes.
_MCP_KEEP = frozenset({"_meta", "blob", "mimeType"})


def take_reveal(message):
    """(the request without the opt-in key, whether it opted in): the key is
    removed from every request, so the server never sees it, but only a
    tools/call request can opt in."""
    params = message.get("params")
    meta = params.get("_meta") if isinstance(params, dict) else None
    if not isinstance(meta, dict) or REVEAL not in meta:
        return message, False
    reveal = message.get("method") == "tools/call" and meta[REVEAL] is True
    params = {k: v for k, v in params.items() if k != "_meta"}
    rest = {k: v for k, v in meta.items() if k != REVEAL}
    if rest:
        params["_meta"] = rest
    return {**message, "params": params}, reveal


def mask_mcp(data):
    """An MCP result, error or params value with its codes and one-time-link
    tokens masked: every `text` as a tool's output (JSON inside it by
    _mask_json, which keeps ids, @odata links and contentBytes), structuredContent
    by _mask_json, every other string as text. _meta, blob, mimeType and
    _KEEP_KEY values stay as they are."""
    if isinstance(data, str):
        return _mask_links(_mask(data)[0])
    if isinstance(data, list):
        return [mask_mcp(v) for v in data]
    if not isinstance(data, dict):
        return data
    out = {}
    for k, v in data.items():
        if k in _MCP_KEEP or _KEEP_KEY.fullmatch(k):
            out[k] = v
        elif k == "text" and isinstance(v, str):
            out[k] = _mask_output(v)
        elif k == "structuredContent":
            out[k] = _mask_json(v)
        else:
            out[k] = mask_mcp(v)
    return out


def mask_reply(message):
    """A JSON-RPC message from the server with its result, error and params masked."""
    return {k: mask_mcp(v) if k in ("result", "error", "params") else v for k, v in message.items()}


def _check_sync(path):
    """Exit 1 unless every mirrored definition equals mail.py's (by AST)."""
    import ast
    import sys

    def defs(source, names=None):
        out = {}
        for node in ast.parse(source).body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                name = node.name
            elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
            else:
                continue
            if names is None or name in names:
                out[name] = ast.dump(node)
        return out

    with open(__file__, encoding="utf-8") as f:
        own = f.read().split("\n")
    start, end = (next(i for i, line in enumerate(own) if line.startswith(m)) for m in ("# MIRROR START", "# MIRROR END"))
    mirrored = defs("\n".join(own[start + 1:end]))
    with open(path, encoding="utf-8") as f:
        theirs = defs(f.read(), mirrored)
    differ = sorted(n for n in mirrored if theirs.get(n) != mirrored[n])
    for name in differ:
        print(f"differs from {path}: {name}" + ("" if name in theirs else " (missing there)"))
    if not differ:
        print(f"in sync: {len(mirrored)} definitions")
    sys.exit(1 if differ else 0)


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3 or sys.argv[1] != "--check-sync":
        sys.exit("usage: mask.py --check-sync PATH/TO/mail.py")
    _check_sync(sys.argv[2])
