"""Fiyat ve başlık tespiti + DM fiyat kuralı.

Bu modül bot.py'den BAĞIMSIZDIR (telethon/bot içe aktarmaz); mevcut kelime
filtrelerine hiç karışmaz. Üç iş yapar:

1. ``extract_price``  – mesajdaki asıl ÜRÜN fiyatını bulur. Kupon, indirim
   tutarı, taksit, kargo, "… TL ve üzeri", eski/üstü çizili fiyat gibi tutarları
   fiyat saymaz.
2. ``extract_title``  – mesajın ürün başlığını seçer (satır puanlama).
3. ``price_matches``  – fiyat ≤ eşik mi? (DM fiyat kuralı)

Hiçbir şey bulunamazsa ``None``/boş döner; çağıran taraf sessizce geçer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

# --------------------------------------------------------------------------
# Yardımcılar
# --------------------------------------------------------------------------


def norm(text: str) -> str:
    """Türkçe uyumlu küçük harf (İ→i, I→ı) ve boşluk sadeleştirme."""
    text = (text or "").replace("İ", "i").replace("I", "ı").lower()
    return " ".join(text.replace("\u00a0", " ").replace("\u202f", " ").split())


def utf16_to_char_index(text: str, offset: int) -> int:
    """Telegram UTF-16 konumunu Python karakter indeksine çevir."""
    units = 0
    for index, char in enumerate(text):
        if units >= offset:
            return index
        units += 2 if ord(char) > 0xFFFF else 1
    return len(text)


def strike_spans_from_entities(text: str, entities: Iterable[Any] | None) -> list[tuple[int, int]]:
    """Üstü çizili (strikethrough) metin aralıklarını karakter indeksi olarak ver."""
    spans: list[tuple[int, int]] = []
    for entity in entities or []:
        if type(entity).__name__ not in {"MessageEntityStrike"}:
            continue
        try:
            start = utf16_to_char_index(text, int(entity.offset))
            end = utf16_to_char_index(text, int(entity.offset) + int(entity.length))
        except (TypeError, ValueError, AttributeError):
            continue
        if end > start:
            spans.append((start, end))
    return spans


# --------------------------------------------------------------------------
# Tutar ayrıştırma
# --------------------------------------------------------------------------

_CUR = r"(?:TL|₺|TRY|YTL)"
_NUM = r"(?:\d{1,3}\s+bin(?:\s+\d{1,3})?|\d{1,3}(?:[.\u00a0\u202f, \t]\d{3})+(?:[,.]\d{1,2})?|\d+(?:[,.]\d{1,2})?)"
_LETTER = "A-Za-zÇĞİÖŞÜçğıöşü"
# Bozuk sayının başını fiyat sayma (1..299 / 1,2,3); tek son noktalama serbest.
_AMOUNT_END = r"(?!\d|[.,][\d.,])"

PRICE_RE = re.compile(
    rf"(?<![\w.,])(?:"
    rf"(?P<pre>{_CUR})\s*(?P<a1>{_NUM}){_AMOUNT_END}"
    rf"|(?P<a2>{_NUM})\s*(?P<bin>bin\s*)?(?P<cur>{_CUR}|lira)(?![{_LETTER}])(?P<suf>[’'][^\W\d_]+)?"
    rf")",
    re.IGNORECASE,
)

# "Fiyat: 1299", "💰 1.299", "2000 kuponla 50499" gibi para
# birimi yazılmayan etiketli tutarlar. Kupon/kod etiketindeki yalın sayıların
# küçük kampanya miktarlarına dönüşmesi find_candidates içinde ayrıca engellenir.
LABELED_RE = re.compile(
    rf"(?:fiyat\w*|price|sepette|net|kuponla|kuponuyla|kupon\s+ile|kodla|koduyla|kod\s+ile)"
    rf"\s*[:=\-–]?\s*(?P<a>{_NUM}){_AMOUNT_END}"
    rf"(?=\s*(?:{_CUR}|lira(?![{_LETTER}])|$|[!?,;:)\]])|\.(?![{_LETTER}]))",
    re.IGNORECASE,
)
_COUPON_LABEL_RE = re.compile(r"^(?:kuponla|kuponuyla|kupon\s+ile|kodla|koduyla|kod\s+ile)\b", re.IGNORECASE)
EMOJI_LINE_RE = re.compile(
    rf"^\s*(?:[💰🏷💵💸🔥✅💲]\ufe0f?\s*)+(?P<a>{_NUM})\s*$"
)


def parse_amount(raw: str) -> float | None:
    """'1.299,90' / '1,299.90' / '1299' / '129,9' → float."""
    text = (raw or "").strip()
    bins = re.fullmatch(r"(\d{1,3})\s+bin(?:\s+(\d{1,3}))?", text, re.IGNORECASE)
    if bins:
        tail = int(bins.group(2) or 0)
        if tail >= 1000:
            return None
        return float(int(bins.group(1)) * 1000 + tail)
    text = re.sub(r"[\s\u00a0\u202f]+", "", text)
    if not text or not re.fullmatch(r"[\d.,]+", text):
        return None
    has_dot, has_comma = "." in text, "," in text
    if has_dot and has_comma:
        decimal = "," if text.rfind(",") > text.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        text = text.replace(thousands, "").replace(decimal, ".")
    elif has_dot or has_comma:
        sep = "." if has_dot else ","
        parts = text.split(sep)
        if len(parts) > 2:
            text = "".join(parts)                                   # 1.234.567
        elif len(parts[1]) == 3 and 1 <= len(parts[0]) <= 3 and parts[0] != "0":
            text = "".join(parts)                                   # 1.299 → 1299
        else:
            text = parts[0] + "." + parts[1]                        # 129,90 → 129.90
    try:
        value = float(text)
    except ValueError:
        return None
    return value if value >= 0 else None


def format_price(value: float) -> str:
    """1299 → '1.299 TL', 159.9 → '159,90 TL', 0 → '0 TL'."""
    if abs(value - round(value)) < 0.005:
        whole = f"{int(round(value)):,}".replace(",", ".")
        return f"{whole} TL"
    whole, frac = f"{value:,.2f}".split(".")
    return f"{whole.replace(',', '.')},{frac} TL"


def parse_threshold(raw: Any) -> float | None:
    """'10000', '10.000', '10.000₺', '50 tl', '10 bin' → sayı. Okunamazsa None."""
    text = norm(str(raw or ""))
    text = re.sub(r"₺|\b(?:tl|try|lira)\b", " ", text).strip()
    multiplier = 1
    if re.search(r"\bbin\b$", text):
        multiplier, text = 1000, re.sub(r"\s*bin$", "", text)
    text = text.replace(" ", "")
    value = parse_amount(text)
    if value is None:
        return None
    return value * multiplier


# --------------------------------------------------------------------------
# Fiyat bağlamı
# --------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[^\W_]+(?:[’'][^\W_]+)?", re.UNICODE)

# Tutarın HEMEN SONRA gelen kelime(ler)i → bu tutar ürün fiyatı değildir
_AFTER_NEG = (
    "indirim", "kupon", "kod", "iade", "puan", "cashback", "hediye", "çek", "ceki", "taksit",
    "üzeri", "uzeri", "üstü", "ustu", "alışveriş", "alisveris", "kazan", "tasarruf", "fark",
    "kargo", "bonus", "promosyon", "limit", "ödül", "odul", "değerinde", "degerinde", "değer",
    "düştü", "dustu", "düşüş", "dusus", "ucuz", "avantaj", "indirimle", "aylık", "aylik",
    "teslimat", "gönderim", "gonderim", "harcama", "sepet", "tık", "tik",
)
_AFTER_OLD = ("yerine", "idi", "iken", "vardı", "vardi", "olan", "olarak satılıyordu", "liste")
_BEFORE_NEG = (
    "taksit", "kupon", "kod", "indirim", "kargo", "iade", "puan", "hediye", "cashback", "limit",
    "fark", "tasarruf", "bonus", "promosyon", "kazanç", "kazanc", "aylık", "aylik", "ödül",
    "odul", "minimum", "min", "alışverişlerde", "alisverislerde", "harcama", "x", "×", "teslimat",
    "gönderim", "gonderim",
)
_BEFORE_OLD = ("eski", "önceki", "onceki", "liste", "piyasa", "normal", "etiket", "tavsiye", "ortalama")
_BEFORE_HISTORY = ("düşük", "dusuk", "geçmiş", "gecmis", "rekor")
_POS_LAST = frozenset(
    "ile ise olarak net sadece yalnızca yalnizca son güncel guncel şimdi simdi yeni nihai toplam kuponla koduyla kodla kuponuyla "
    "sepette fiyat fiyatı fiyati ödenecek odenecek ödeme odeme".split()
)
_HARD_NEG_BEFORE = frozenset("taksit kargo iade puan limit hediye cashback teslimat".split())
_STRONG_BEFORE = frozenset(
    "fiyat fiyatı fiyati price sepette yeni güncel guncel şimdi simdi net son nihai".split()
)
_THRESHOLD_DATIVE_RE = re.compile(
    r"^[’']\s?(?:ye|ya|e|a)\s+\d[\d.,]*\s*(?:tl|₺)?\s*(?:kupon|indirim|iade|hediye)", re.IGNORECASE
)                                                           # "300 TL'ye 50 TL kupon" → 300 eşiktir
_DATIVE_RE = re.compile(r"^[’']\s?(?:ye|ya|e|a)\b", re.IGNORECASE)
_ABLATIVE_RE = re.compile(r"^[’']\s?(?:den|dan|ten|tan)\b", re.IGNORECASE)
_MONTHLY_RE = re.compile(r"^\s*(?:/\s*ay|[x×]\s*\d|\+\s*kdv)", re.IGNORECASE)
_FREE_RE = re.compile(r"(?<![\w])(?:ücretsiz|ucretsiz|bedava|free)(?![\w])", re.IGNORECASE)
_FREE_BLOCK_RE = re.compile(
    r"kargo|teslimat|gönderim|gonderim|iade|kurulum|montaj|deneme|üyelik|uyelik|aboneli|"
    r"ilk\s+ay|değişim|degisim|nakliye|hızlı|hizli|prime|"
    r"\d+\s+alana\s+\d+\s+bedava",
    re.IGNORECASE,
)
_COUPON_SLASH_RE = re.compile(r"\d[\d.,]*\s*/\s*$")          # "1.000/300TL" → eşik/indirim yazımı
_COND_BEFORE_RE = re.compile(
    r"(?:\d+\s*adet(?:\s*al[ıi]m\w*)?|al[ıi]mda|al[ıi]mlarda|\d+\.\s*ürün\w*|ikinci\s+ürün\w*|"
    r"\d+\s*al\s*\d+\s*öde)\s*$"
)                                                           # "2 adet alımda 48 TL", "2. ürün 1 TL"
_FIRST_PARTY_RE = re.compile(r"\b1p\b")                    # Amazon (1P) son fiyatı → eski/başka fiyat
# Kampanya koşulu tutarı: "2. ürün 1 TL", "ikinci ürüne 1₺", "2. ürün: 1 TL",
# "2 al 2. 1₺", "2 alana 1 TL", "1 TL ek ürün". Bu tutar ürünün satış fiyatı
# değildir: ikinci/ek ürünün ya da bir alım koşulunun fiyatıdır. Yalnızca aynı
# satırdaki bağlamdan tanınır; "2 al 100 TL" gibi toplu fiyatlar etkilenmez.
_CAMPAIGN_BEFORE_RE = re.compile(
    r"(?:\b2\s*\.\s*|\bikinci\s+|\bek\s+)(?:ürün|urun)\w*"
    r"(?:\s+(?:fiyat\w*))?\s*[:=\-–]?\s*$"
    r"|\b\d+\s*al\s*\d+\s*\.\s*$"
    r"|\b\d+\s*al(?:ana|ınca|ırsan|ırsanız|arak)\s*[,:]?\s*$"
)
# "Kampanya: 1 TL ek ürün": yalnızca kampanya etiketli satırda ve "fiyat" kelimesi
# olmadığında tutarın sonrasına bakılır ("Kampanya fiyatı: 49,90 TL ek ürün" gerçek fiyattır).
_CAMPAIGN_EXTRA_AFTER_RE = re.compile(r"^\s*(?:ek|ikinci)\s+(?:ürün|urun)\b")
_CAMPAIGN_LABEL_RE = re.compile(r"\bkampanya\w*")
_PRICE_EMOJI_RE = re.compile(r"[💰🏷💵💸🔥✅💲🎯🔻💳]")
_HISTORY_HINT_RE = re.compile(r"\bgün|\bay\b|geçmiş|gecmis|ortalama|haftan|ayın|ayin|yılın|yilin|son \d+")


@dataclass
class Candidate:
    value: float
    shown: str
    cls: str          # price | old | history | other
    strong: bool
    line: int
    start: int
    end: int = 0
    role: str = ""
    reason: str = ""


@dataclass
class PriceInfo:
    value: float | None
    shown: str
    confidence: str   # high | medium | low | none
    reason: str

    @property
    def text(self) -> str:
        return format_price(self.value) if self.value is not None else ""


def _tokens(text: str) -> list[str]:
    return [norm(match.group()) for match in _TOKEN_RE.finditer(text or "")]


def _stem_in(token: str, stems: Sequence[str]) -> bool:
    token = token.split("’")[0].split("'")[0]
    for stem in stems:
        if token == stem or (len(stem) >= 4 and token.startswith(stem)):
            # "indirimli fiyat" gibi sıfatları engelleme sayma
            if stem == "indirim" and token.startswith("indirimli"):
                continue
            return True
    return False


# Çok satırlı kampanya: "2. ürün:" / "2 al 2." / "Ek ürün" satırının HEMEN ARDINDAKİ
# satır yalnızca bir tutarsa ("1 TL", "1₺") o tutar kampanya koşuludur. Emojili ya da
# başka kelime içeren satırlar (🏷 49,90 TL) bu kuralın dışındadır: gerçek fiyat kalır.
_BARE_AMOUNT_LINE_RE = re.compile(rf"(?:{_CUR}\s*)?{_NUM}\s*(?:{_CUR})?", re.IGNORECASE)


def _campaign_line_continues(prev_line: str | None, line: str) -> bool:
    """Önceki satır kampanya koşulunda bitiyor ve bu satır yalnız tutar mı?"""
    return bool(prev_line and _CAMPAIGN_BEFORE_RE.search(norm(prev_line))
                and _BARE_AMOUNT_LINE_RE.fullmatch(line.strip()))


def is_campaign_extra_amount(line: str, start: int, end: int, prev_line: str | None = None) -> bool:
    """``line[start:end]`` tutarı kampanya koşulu mu (ürün fiyatı değil)?

    Tutarın ÖNCESİNDEKİ aynı satır bağlamına bakar: "2. ürün", "ikinci ürün",
    "ek ürün", "2 al 2.", "2 alana". Tek başına tutar olan satır için (``prev_line``)
    bir önceki satır da kampanya koşulunda bitiyorsa tutar kampanyadır. Tutarın SONRASINA bakılmaz: "🏷
    49,90 TL 2. ürün 1 TL" satırında gerçek fiyat 49,90 TL'dir ve kampanya
    ifadesi ondan sonra gelir. ``'ye al`` ("1 TL'ye al") tek başına kampanya değildir.
    """
    if _campaign_line_continues(prev_line, line):
        return True
    before = norm(line[:start])
    if _CAMPAIGN_BEFORE_RE.search(before):
        return True
    return bool(_CAMPAIGN_EXTRA_AFTER_RE.match(norm(line[end:]))
                and _CAMPAIGN_LABEL_RE.search(before) and "fiyat" not in before)


def _classify(line: str, start: int, end: int, abs_start: int, strike: Sequence[tuple[int, int]],
              suffix: str = "") -> tuple[str, bool]:
    """Tutarın türünü belirle. ``end`` para biriminin bittiği yerdir; ``suffix`` 'ye/'den gibi ektir."""
    after_raw = line[end:]  # end zaten para biriminin sonu; ek kaynak metinde mevcut
    before_raw = line[:start]
    after_clean = after_raw.strip()
    before_tokens = _tokens(before_raw)[-3:]

    if any(a <= abs_start < b for a, b in strike):
        return "old", False
    # Kampanya koşulu ('ye al / kampanyası gibi ekler ürün fiyatı gibi görünse de)
    # ürün fiyatı değildir; diğer sınıflandırmalardan önce ayrılır.
    if is_campaign_extra_amount(line, start, end):
        return "campaign", False

    if _THRESHOLD_DATIVE_RE.match(norm(after_clean)):
        return "other", False
    if _DATIVE_RE.match(after_clean):
        following = _tokens(_DATIVE_RE.sub("", after_clean, count=1))[:1]
        if following and following[0].startswith(("çıkmış", "cikmis", "yükselmiş", "yukselmis")):
            return "old", False                       # "422 TL'ye çıkmıştı, tekrar 199 TL"
        if re.search(r"\b(?:kargo|teslimat|gönderim|gonderim)\b", norm(before_raw[-64:])):
            return "other", False                     # e.g. fee for a multi-use shipping pack
        return "price", True                           # "1.199 TL'ye düştü"
    if _ABLATIVE_RE.match(after_clean):
        rest = _tokens(_ABLATIVE_RE.sub("", after_clean, count=1))[:1]
        if not (rest and rest[0].startswith("başlayan")):
            return "old", False                        # "2.999 TL'den 1.999 TL'ye"
    if _MONTHLY_RE.match(after_raw):
        return "other", False

    connector = bool(re.match(r"^\s*[+&,;|/\-–]", after_raw))   # "6.499 TL + kargo bedava"
    after_tokens = _tokens(after_raw)[:2]
    if not connector and after_tokens and not re.match(r"^\s*[🚚🚛]", after_raw):
        if _stem_in(after_tokens[0], _AFTER_NEG):
            return "other", False
        if len(after_tokens) > 1 and after_tokens[0] in {"ve", "ya", "veya"} \
                and _stem_in(after_tokens[1], _AFTER_NEG):
            return "other", False
        if _stem_in(after_tokens[0], _AFTER_OLD):
            return "old", False

    last = before_tokens[-1] if before_tokens else ""
    near = before_tokens[-2:]
    if _COUPON_SLASH_RE.search(before_raw):
        return "other", False
    if _FIRST_PARTY_RE.search(norm(before_raw[-30:])):
        return "old", False
    if _COND_BEFORE_RE.search(norm(before_raw[-40:])):
        return "cond", False
    if any(_stem_in(tok, _BEFORE_OLD) for tok in near):
        return "old", False
    if any(_stem_in(tok, _BEFORE_HISTORY) for tok in before_tokens) and _HISTORY_HINT_RE.search(norm(line)):
        return "history", False
    hard = any(tok in _HARD_NEG_BEFORE for tok in near)
    if not hard and last in _POS_LAST:
        return "price", True
    if any(_stem_in(tok, _BEFORE_NEG) for tok in near):
        return "other", False
    strong = any(tok in _STRONG_BEFORE for tok in before_tokens)
    words_outside = [tok for tok in _tokens(line) if not re.fullmatch(r"[\d.,]+", tok)]
    if _PRICE_EMOJI_RE.search(before_raw) and len(words_outside) <= 4:
        strong = True
    return "price", strong


def find_candidates(text: str, strike: Sequence[tuple[int, int]] = ()) -> list[Candidate]:
    candidates: list[Candidate] = []
    offset = 0
    lines = (text or "").split("\n")
    for index, line in enumerate(lines):
        prev_line = lines[index - 1] if index else None
        seen_spans: list[tuple[int, int]] = []
        for match in PRICE_RE.finditer(line):
            raw = match.group("a1") or match.group("a2")
            value = parse_amount(raw)
            if value is None:
                continue
            if match.group("bin"):
                value *= 1000
            core_end = match.end("cur") if match.group("cur") else match.end("a1")
            cls, strong = _classify(line, match.start(), core_end, offset + match.start(), strike,
                                    suffix=match.group("suf") or "")
            if cls in {"price", "cond"} and _campaign_line_continues(prev_line, line):
                cls, strong = "campaign", False   # çok satırlı kampanya: "2. ürün" / "1 TL"
            candidates.append(Candidate(value, match.group().strip(), cls, strong, index, match.start(), core_end))
            seen_spans.append(match.span())
        for regex in (LABELED_RE, EMOJI_LINE_RE):
            match = regex.search(line)
            if not match:
                continue
            if any(s <= match.start("a") < e for s, e in seen_spans):
                continue
            coupon_label = regex is LABELED_RE and _COUPON_LABEL_RE.match(
                norm(line[match.start():match.start("a")])
            )
            value = parse_amount(match.group("a"))
            if value is None or value > 10_000_000:
                continue
            if coupon_label and value < 100:
                # Para birimsiz kupon etiketi yalnız belirgin (en az 100 TL)
                # fiyatları tamamlar. ``kuponla 2 al 1 öde`` içindeki 2, fiyat
                # adayına dönüşmemeli; mevcut küçük etiketli fiyatlar değişmez.
                continue
            cls, _ = _classify(line, match.start("a"), match.end("a"), offset + match.start("a"), strike)
            candidates.append(Candidate(value, match.group().strip(), cls, True, index, match.start("a"), match.end("a")))
        offset += len(line) + 1
    # A warehouse/used listing may quote the new item's price for comparison.
    # Only demote that exact label when a separate strong offer price and an
    # explicit used/refurbished condition are present; ordinary "yeni fiyatı"
    # announcements and independent condition prices retain their semantics.
    used = re.search(r"\b(?:ikinci\s+el|2\s*\.\s*el|yenilenmiş|yenilenmis|refurbished|renewed)\b", norm(text))
    if used:
        lines_by_index = text.split("\n")
        references = [c for c in candidates if c.cls == "price" and re.search(
            r"\byeni\s+(?:ürün\s+)?fiyat[ıi]\s*[:=]\s*$",
            norm(lines_by_index[c.line][:c.start]),
        )]
        offers = [c for c in candidates if c not in references and c.cls == "price" and c.strong]
        if len({c.value for c in offers}) == 1 and len({c.value for c in references}) == 1:
            for c in references:
                c.cls, c.strong = "old", False
    _relate_candidates(text, candidates)
    return candidates


def _relate_candidates(text: str, candidates: list[Candidate]) -> None:
    """Yerel filtreleri koruyarak aynı teklifin tutarlarını ilişkilendir.

    cls eski tüketiciler (özellikle dedup) için korunur; role/reason metin
    dökmeden testlerde ve teşhiste kullanılabilir. Sayısal indirim hesaplamayız.
    """
    lines = text.split("\n")
    for c in candidates:
        line = lines[c.line]
        before, after = norm(line[:c.start]), norm(line[c.end:])
        c.role = {"price": "normal", "cond": "conditional", "old": "old",
                  "history": "history", "other": "unrelated", "campaign": "campaign"}[c.cls]
        # Üstü çizili ve eski/geçmiş tutarlar asla yeniden terfi ettirilmez.
        if c.cls in {"old", "history"}:
            c.reason = c.role
            continue
        clause = re.split(r"[/|(—–]", before)[-1]
        # Explicit per-item multi-buy amount; the optional "adet" after
        # alımda is used by some channels. Do not promote coupon amounts.
        if c.cls in {"price", "cond"} and re.search(
            r"\b\d+\s*adet\s*al[ıi]mda\s+(?:adet|tanesi|birim)\s*$", clause
        ):
            c.cls, c.role, c.strong, c.reason = "cond", "multi_unit", True, "multi_unit"
            continue
        # Parenthesized pack breakdown is not a second independent total.
        if c.cls == "price" and re.match(r"^\s*/\s*(?:adet|tane)\s*\)", after) \
                and "(" in before:
            c.cls, c.role, c.strong, c.reason = "cond", "unit", True, "unit"
            continue
        if c.cls == "price" and re.match(r"^(?:ye|ya|e|a)\s+düş\w*", after):
            c.role, c.strong, c.reason = "discounted", True, "discounted"
            continue
        if c.cls == "price" and re.fullmatch(r"\s*[🏷💰💵💸💲]\ufe0f?\s*", line[:c.start]):
            c.strong = True
        if c.cls in {"price", "cond", "other"} and re.search(
            r"(?:ikinci\s+el|2\s*\.\s*el|yenilenmiş|yenilenmis|renewed|refurbished)\b[^/\n]*$",
            before,
        ):
            # Açık kondisyon etiketi, aynı mesajdaki yeni ürün referansından
            # ayrı bir tekliftir. Birden fazla farklı kondisyon fiyatı varsa
            # extract_price mevcut çoklu-değer belirsizliğini korur.
            c.cls, c.role, c.strong = "price", "condition_price", True
        elif c.cls in {"price", "cond", "other"} and re.fullmatch(
            r"sepette[*_~`\s.!]*", after,
        ):
            # ``Sepette 43.728 TL`` ile ``43.728 TL sepette`` aynı açık
            # sepet fiyatıdır. Yalnız satırın tamamındaki yalın ``sepette``
            # kuyruğu kabul edilir; ``… TL sepette indirim`` gibi indirim
            # tutarları ve kupon/kargo açıklamaları bu kurala girmez.
            c.cls, c.role, c.strong = "price", "discounted", True
        elif c.cls == "other" and re.fullmatch(
            r"\(\s*(?:kargo|teslimat|gönderim|gonderim)\s+(?:bedava|ücretsiz|ucretsiz)\s*\)[*_~`\s.!]*",
            after,
        ) and not re.search(r"(?:kargo|teslimat|gönderim|gonderim)\s*[:=]?\s*$", before):
            # ``217 TL (Kargo Bedava)`` kargo ÜCRETİ değil, fiyatın yanında
            # duran parantez içi kargo avantajıdır. ``Kargo: 30 TL`` ve
            # parantezli ücret açıklamaları bu dar kuralın dışında kalır.
            c.cls, c.role = "price", "normal"
            c.strong = bool(
                _PRICE_EMOJI_RE.search(line[:c.start])
                or re.search(r"(?:fiyat\w*|price)\s*[:=]?\s*$", before)
            )
        elif re.match(r"^[’'](?:ye|ya|e|a)\s+\d[\d.,]*\s*(?:tl|₺|try)\s*(?:kupon|indirim)", after):
            c.cls, c.role = "other", "threshold"
        elif re.search(r"(?:kupon|kod)\s+(?:uygulayınca|uygulandığında|uygulandiginda)\s*$", before) and c.cls == "other":
            c.cls, c.role, c.strong = "price", "discounted", True
        elif c.cls in {"price", "cond", "other"} and re.search(
            r"(?:kuponla|kuponuyla|kupon\s+ile|kodla|koduyla|kod\s+ile)\s*[:=]?\s*$",
            before,
        ):
            # ``2000 kuponla 50499`` ve ``100 TL kupon ile 90 TL``:
            # sondaki tutar nihai fiyattır; önceki kupon tutarı aşağıdaki
            # discount_amount dalında ayrıca elenir. Ürün/sepet kuponu rol
            # ayrımı mevcut son bağlam kuralıyla birebir aynı tutulur.
            clause = re.split(r"[/|(]", before)[-1]
            c.cls, c.strong = "price", True
            c.role = "product_coupon" if re.search(r"ürün alt", clause) else "basket_coupon" if "sepette" in clause else "discounted"
        elif c.cls == "other" and len(candidates) == 1 \
                and re.fullmatch(r"kupon\s+ile[*_~`\s.!]*", after) \
                and not _TOKEN_RE.findall(before) \
                and _has_product_context_before(lines, c.line):
            # ``<ürün>\n1376 TL kupon ile``: tek tutar ve gerçek ürün satırı
            # varken bu, kupon miktarı değil kuponla verilen ürün fiyatıdır.
            # ``Sepette 250 TL kupon`` veya iki tutarlı biçim bu dala girmez.
            c.cls, c.role, c.strong = "price", "discounted", True
        elif re.match(r"^(?:ve\s+)?(?:üzeri|uzeri|üstü|ustu|alışveriş|harcama)", after):
            c.cls, c.role = "other", "threshold"
        elif re.match(r"^(?:kupon|indirim|kod)", after) or re.search(r"(?:kupon|indirim)\s*[:=]?\s*$", before):
            c.cls, c.role = "other", "discount_amount"
        elif c.cls in {"other", "price"} and not after.strip("*_~` \t") and any(
            previous.line == c.line and previous.role == "discount_amount"
            and previous.end <= c.start
            and re.fullmatch(
                r"(?:indirimle|indirim\s+ile|indirim\s+sonrası(?:nda)?)[*_~`\s]*",
                norm(line[previous.end:c.start]),
            )
            for previous in candidates
        ):
            # ``14.250 TL / ödeme aşamasında 750 TL indirimle 13.500 TL``:
            # son tutar, hemen öncesindeki ``indirimle`` yüzünden indirim tutarı
            # filtresine takılmamalı. Yalnız aynı satırda, hemen önce açık bir
            # indirim tutarı bulunan ve satır sonunda duran nihai tutarı terfi et.
            c.cls, c.role, c.strong = "price", "discounted", True
        elif c.cls in {"price", "cond", "other"} and not after.strip("*_~` \t.!") and re.search(
            r"(?:ödeme|odeme)\s+(?:aşamasında|asamasinda)\s*[:=]?\s*$", before,
        ):
            # ``600 TL / ödeme aşamasında 454 TL``: ödeme adımındaki ikinci
            # tutar açık nihai fiyattır. ``750 TL indirimle 13.500 TL`` içindeki
            # 750 TL yukarıdaki discount_amount dalından buraya ulaşmaz.
            c.cls, c.role, c.strong = "price", "discounted", True
        elif c.cls in {"price", "cond", "other"} and not after.strip("*_~` \t.!") and re.search(
            r"\b(?:plus|prime)\s+(?:üyelere|üyelerine|uyelere|uyelerine)\s+özel\s*[:=]?\s*$",
            before,
        ):
            # Açık üyelik teklifi, yakın ama bağlamsız iki fiyatın genel
            # belirsizliğini bozmadan güvenilir alternatif olarak ayrılır.
            c.cls, c.role, c.strong = "price", "member_price", True
        elif c.cls == "other" and re.match(
            r"^[*_~`\s]*[—–-]\s*sepette\b[^/|]*\b(?:indirim\s+kod|kupon(?:la|uyla|\s+ile|\s+kod))", after,
        ) and (
            _PRICE_EMOJI_RE.search(line[:c.start])
            or re.search(r"(?:fiyat\w*|price)\s*[:=]?\s*$", before)
        ):
            # ``🏷 889 TL — sepette "KOTON300" indirim kodunu uygulayın`` ve
            # ``🏷 1.649 TL — sepette 250 TL kupon ile``: kupon AÇIKLAMASI
            # etiketli fiyatı indirim tutarına çevirmez. İkinci 250 TL'nin
            # kendi ``after`` bağlamı sepette ile başlamadığı için terfi etmez.
            c.cls, c.role, c.strong = "price", "normal", True
        elif re.match(r"^seçili\s+ürün.*satın\s+aldığınızda", after):
            c.cls, c.role = "other", "threshold"
        elif re.search(r"\bkampanya\s*:\s*$", before) and re.match(r"^\(\s*\d+\s*adet\s*\)", after):
            # Etiketli kampanya + sonda miktar, birim fiyat kanıtı değildir.
            c.cls, c.role = "cond", "campaign_quantity"
        elif c.cls in {"price", "cond", "other"} and re.search(
            r"\b2\s*\.\s*s[ıiuü]\s*[:=]?\s*$", before,
        ):
            # ``2.si 1 TL`` ordinal kampanya koşuludur; ürünün taban fiyatı
            # değildir. Bu dar biçim, açık nihai fiyat bulunan mevcut
            # ``2. ürün`` guard'ını değiştirmeden ayrıca tanınır.
            c.cls, c.role, c.strong = "campaign", "campaign", False
        elif re.search(r"(?:\b1\s+adet|\badedi|\btanesi|(?:adet|birim|tane)\s*(?:baş[ıi]|fiyat[ıi]))\s*[:=]?\s*$", before) and c.cls in {"price", "cond", "other"}:
            # Yakındaki kupon sözcüğü açık birim fiyatı gölgeleyebilir.
            # Kupon tutarı/eskiler yukarıda elenir; kargo/taksit terfi etmez.
            unit_prefix = re.sub(r"(?:\b1\s+adet|\badedi|\btanesi|(?:adet|birim|tane)\s*(?:baş[ıi]|fiyat[ıi]))\s*[:=]?\s*$", "", before)
            if not re.search(r"(?:kargo|taksit|teslimat)\s*[:=]?\s*$", unit_prefix):
                explicit_multi = re.search(r"\d+\s*adet\s*al[ıi]m(?:da|larda)\s*", before) \
                    or re.search(r"\d+\s+al\s+\d+\s+öde\b", before)
                c.cls, c.role, c.strong = "cond", "multi_unit" if explicit_multi else "unit", True
        elif re.search(r"\d+\s+al\s+\d+\s+öde(?:\s+ile)?(?:\s+toplam)?\s*$", before) and c.cls in {"price", "cond"}:
            c.cls, c.role, c.strong = "cond", "multi_total" if before.endswith("toplam") or re.match(r"^toplam\b", after) else "multi_unit", False
        elif c.cls in {"price", "cond"}:
            # Slash/parenthesis yeni alternatifin sınırıdır; önceki kupon
            # açıklamasının sonraki fiyatlara sızmasına izin verme.
            clause = re.split(r"[/|(]", before)[-1]
            multi = re.search(r"\d+\s*adet\s*al[ıi]m(?:da|larda)\s*(?:(?:(?:adet|birim|tane)\s*baş[ıi]|tanesi)\s*)?$", clause)
            unit = re.search(r"(?:(?:adet|birim|tane)\s*(?:baş[ıi]|fiyat[ıi])|tanesi)\s*[:=]?\s*$", clause)
            total = re.search(r"(?:toplam|toplamda)\s*[:=]?\s*$", clause) or re.match(r"^(?:toplam|toplamda)\b", after)
            if total and re.search(r"\d+\s*adet", clause):
                c.cls, c.role = "cond", "multi_total"
            elif multi or unit:
                c.cls, c.role = "cond", "multi_unit" if multi else "unit"
                c.strong = bool(unit)  # açık tanesi/adet başı; yalın alımda kalıbından farklı
            elif re.search(r"kupon(?:la|uyla|\s+uygulayınca|\s+uygulandığında)|kupon ile|koduyla|kodla", clause):
                c.role = "product_coupon" if re.search(r"ürün alt", clause) else "basket_coupon" if "sepette" in clause else "discounted"
                c.strong = True
            elif re.search(r"(?:yeni|net|nihai|indirimli|sepette)\s*[:=]?\s*$", clause):
                c.role = "discounted"
        c.reason = c.role

    # Refine the old catch-all role without changing which amounts are price candidates.
    for c in candidates:
        line = lines[c.line]
        if c.role == "normal" and (
            any(unit.role == "unit" and unit.line == c.line and unit.start > c.end
                for unit in candidates)
            or any(unit.role == "multi_unit" and unit.line == c.line and unit.start > c.end
                   and re.search(r"\d+\s+al\s+\d+\s+öde\b", norm(line[c.end:unit.start]))
                   for unit in candidates)
        ):
            c.role = "pack_total"
        elif c.role == "unrelated":
            context = norm(line[max(0, c.start - 32):min(len(line), c.end + 32)])
            if re.search(r"kargo|teslimat|gönderim|gonderim", context):
                c.role = "shipping"
            elif re.search(r"cashback|nakit\s+iade|puan|bonus", context):
                c.role = "cashback"
            elif re.search(r"taksit|aylık|aylik", context):
                c.role = "installment"
        c.reason = c.role


def ambiguous_campaign_line(line: str) -> bool:
    """Aynı satırda kampanya tutarı ile GÜÇLÜ işaretsiz (etiketsiz) başka tutar varsa ``True``.

    "1 TL 2. ürün 49,90 TL" ve "49,90 TL 2. ürün 1 TL" yapısal olarak aynıdır; hangisinin
    ürün fiyatı olduğu metinden kesin çıkmaz. Güçlü işaretli tutar ("🏷 49,90 TL",
    "Fiyat: …") güvenilir kabul edilir ve belirsizlik sayılmaz.
    """
    cands = find_candidates(line)
    campaigns = [c for c in cands if c.cls == "campaign"]
    if not campaigns:
        return False
    if any(c.role in {"multi_unit", "unit"} for c in cands):
        # "2 adet alımda 256 TL" gibi açık çoklu alım/birim bağlamı tutarı teklifi zaten
        # tanımlar; mevcut çözümleme bu satırda belirsizlik üretmez.
        return False
    plain = [c for c in cands if c.cls == "price"]
    # Güçlü işaret (🏷, "Fiyat:") yalnızca kampanya tutarından ÖNCE gelen tutara aittir;
    # sonradan gelen tutarın işareti kampanyaya ait olabilir.
    trusted = [c for c in plain if c.strong and not any(k.start < c.start for k in campaigns)]
    return bool(plain) and not trusted


_EXPLICIT_PRICE_ROLES = {
    "multi_unit", "unit", "discounted", "product_coupon", "basket_coupon",
    "condition_price", "member_price",
}


def campaign_plain_ambiguous(cands: Sequence[Candidate]) -> bool:
    """Mesajda kampanya tutarı + etiketsiz ürün tutarı var, etiketli fiyat ve açık bağlam yok.

    Etiketli (güçlü işaretli) fiyat varsa ``🏷 49,90 TL`` gibi gerçek fiyat korunur.
    Çoklu alım / nihai fiyat bağlamı (``2 adet alımda 256 TL``) varsa mevcut çözümleme
    geçerlidir. Kampanya tutarı yoksa bu kural hiç devreye girmez.
    """
    if not any(c.cls == "campaign" for c in cands):
        return False
    if any(c.role in _EXPLICIT_PRICE_ROLES for c in cands):
        return False
    if any(c.cls == "price" and c.strong for c in cands):
        return False
    return any(c.cls == "price" for c in cands)


def has_campaign_marker(text: str) -> bool:
    """Mesajda kampanya koşulu izi var mı (``2. ürün:`` satırı ya da kampanya tutarı)?"""
    if any(_CAMPAIGN_BEFORE_RE.search(norm(line)) for line in (text or "").split("\n")):
        return True
    return any(c.cls == "campaign" for c in find_candidates(text))


_PRODUCT_HEADER_RE = re.compile(r"(?<!\w)\d+\s*\.\s*ürün\s*[:：]", re.IGNORECASE)
_FEATURED_PRODUCT_RE = re.compile(r"\b(?:öne\s+çıkan|önerilen|seçili|ana)\s+ürün\b\s*[:：-]", re.IGNORECASE)


def _numbered_product_blocks(text: str) -> list[tuple[int, str]]:
    """Explicit ``1. ürün:`` labels delimit products; ordinary campaign text does not."""
    matches = list(_PRODUCT_HEADER_RE.finditer(text or ""))
    if len(matches) < 2:
        return []
    return [(match.start(), (text or "")[match.start():matches[i + 1].start() if i + 1 < len(matches) else len(text)])
            for i, match in enumerate(matches)]


def _selected_product_block(text: str, strike: Sequence[tuple[int, int]] = ()) -> tuple[str, list[tuple[int, str]]] | None:
    """Select one explicitly featured or uniquely priced product block, never cross-pair."""
    blocks = _numbered_product_blocks(text)
    if not blocks:
        return None
    priced: list[tuple[int, str, PriceInfo]] = []
    for start, block in blocks:
        local_strike = [(max(0, a - start), min(len(block), b - start))
                        for a, b in strike if a < start + len(block) and b > start]
        info = extract_price(block, local_strike)
        if info.value is not None and info.confidence in {"high", "medium"}:
            priced.append((start, block, info))
    featured = [(start, block, info) for start, block, info in priced
                if _FEATURED_PRODUCT_RE.search(block)]
    if len(featured) == 1:
        return featured[0][1], blocks
    if len(featured) > 1 or len(priced) != 1:
        return "", blocks
    return priced[0][1], blocks


def extract_price(text: str, strike: Sequence[tuple[int, int]] = ()) -> PriceInfo:
    """Mesajdaki ürün fiyatını seç. ``confidence`` 'low' ise filtre kullanmaz."""
    selected = _selected_product_block(text or "", strike)
    if selected is not None:
        block, _ = selected
        if not block:
            return PriceInfo(None, "", "none", "birden fazla ürün bloğu: seçim belirsiz")
        start = (text or "").find(block)
        local_strike = [(max(0, a - start), min(len(block), b - start))
                        for a, b in strike if a < start + len(block) and b > start]
        return extract_price(block, local_strike)
    lines = (text or "").split("\n")
    unsure = {index for index, line in enumerate(lines) if ambiguous_campaign_line(line)}
    if unsure:
        # Belirsiz satırın tutarları ürün fiyatı sayılmaz; kalan satırlarda aranır.
        # Satırlar aynı uzunlukta boşlukla maskelenir: üstü çizili (strike) konumlar kaymaz.
        masked = "\n".join(" " * len(line) if index in unsure else line
                           for index, line in enumerate(lines))
        info = extract_price(masked, strike)
        if info.value is None:
            return PriceInfo(None, "", "none", "kampanya ile aynı satırda etiketsiz tutar: belirsiz")
        return info
    cands = find_candidates(text, strike)
    if campaign_plain_ambiguous(cands):
        # Çok satırlı mesaj: kampanya tutarı + etiketsiz tutar, etiketli fiyat yok.
        # Hangisinin ürün fiyatı olduğu güvenilir değil: fiyat bilinmiyor (❓).
        return PriceInfo(None, "", "none", "kampanya tutarı ile etiketsiz tutar: belirsiz")
    finals = [
        c for c in cands
        if c.role in {"discounted", "product_coupon", "basket_coupon", "condition_price", "member_price"}
    ]
    normal = [c for c in cands if c.cls == "price" and c not in finals]
    units = [c for c in cands if c.role in {"multi_unit", "unit"}]
    # Önce açık nihai fiyat; çoklu alım sadece bu teklife daha avantajlı bir
    # alternatifse seçilebilir. Eşik/kupon tutarları bu havuza hiç giremez.
    pool = finals or [c for c in normal if c.strong] or normal
    if pool or units:
        values = {c.value for c in pool}
        best = pool[0] if pool else units[0]
        ambiguous = len(values) > 1 or (not pool and bool(units) and not best.strong)
        # A plain unit price ("tanesi 30 TL") must not replace a stated pack
        # total. Explicit multi-buy prices retain the established offer behavior.
        for c in units:
            if c.role == "multi_unit" and c.value < best.value:
                best = c
            elif c.role == "unit" and c.value < best.value and c.line not in {p.line for p in pool}:
                # A separately stated per-unit price remains eligible. A parenthetical
                # unit breakdown beside a pack total does not replace that total.
                best = c
        # İlişkisiz farklı fiyatlarda min() ile güvenli fiyat uydurma.
        confidence = "low" if ambiguous else "medium" if best.cls == "cond" or not best.strong else "high"
        return PriceInfo(best.value, best.shown, confidence, best.reason)
    conds = [c for c in cands if c.cls == "cond"]
    if conds:
        best = conds[0]
        return PriceInfo(best.value, best.shown, "low", best.reason)
    if not cands:
        for line in (text or "").split("\n"):
            if _FREE_RE.search(line) and not _FREE_BLOCK_RE.search(line):
                return PriceInfo(0.0, "ücretsiz", "medium", "ücretsiz/bedava")
        return PriceInfo(None, "", "none", "fiyat yok")
    fallback = [c for c in cands if c.cls in {"history", "old"}]
    if fallback:
        best = min(fallback, key=lambda c: (c.value, c.line, c.start))
        return PriceInfo(best.value, best.shown, "low", "yalnız eski/geçmiş fiyat")
    if any(c.cls == "campaign" for c in cands):
        # Kampanya koşulu tutarı (ör. "2. ürün 1 TL") ürün fiyatı değildir: değeri
        # verilmez; belirsiz fiyat davranışı (❓) korunur.
        return PriceInfo(None, "", "none", "yalnız kampanya koşulu tutarı")
    return PriceInfo(None, "", "none", "yalnız kupon/indirim/taksit tutarı")


def price_matches(text: str, threshold: float | None,
                  strike: Sequence[tuple[int, int]] = ()) -> tuple[bool, PriceInfo]:
    """Fiyat ≤ eşik ise (güvenilir tespitle) True."""
    info = extract_price(text, strike)
    if threshold is None or info.value is None or info.confidence not in {"high", "medium"}:
        return False, info
    return info.value <= threshold + 1e-9, info


# --------------------------------------------------------------------------
# Başlık
# --------------------------------------------------------------------------

_URL_RE = re.compile(r"(?:https?://|www\.|t\.me/)\S+", re.IGNORECASE)
_MENTION_RE = re.compile(r"(?<!\w)[@#][\w.]+")
_SYMBOL_RE = re.compile(
    "[\U0001F000-\U0001FFFF\u2600-\u27BF\u2B00-\u2BFF\u2190-\u21FF\u25A0-\u25FF"
    "\u2300-\u23FF\ufe0f\u200d\u20e3\u2000-\u200b\u2028-\u202f]"
)
_PERCENT_RE = re.compile(r"%\s*\d+(?:[.,]\d+)?|\d+(?:[.,]\d+)?\s*%")
_LABEL_PREFIX_RE = re.compile(r"^\s*(?:ürün adı|ürün ismi|ürün|başlık|model|isim)\s*[:\-–]\s*", re.IGNORECASE)
_LEAD_TRIM_RE = re.compile(r"^[\s|:;,\-–—•·>»*_~=/\\.!?]+")
_TRAIL_TRIM_RE = re.compile(r"[\s|:;,\-–—•·>»*_~=/\\!?]+$")
_META_LABEL_RE = re.compile(r"^\s*([^\W\d_][^\d:]{0,18}?)\s*:\s*\S")
_TITLE_LABELS = frozenset({"ürün", "ürün adı", "ürün ismi", "başlık", "model", "isim"})
_META_WORDS_RE = re.compile(
    r"kupon|\bkod\b|kodu|taksit|kargo|satıcı|satici|stok|geçerli|gecerli|tarihine kadar|"
    r"fiyat geçmişi|fiyat gecmisi|en düşük|en dusuk|ortalama|yorum|puan|değerlendirme|"
    r"(?<![\w-])link(?![\w-])|tıkla|tikla|sepette|iade|teslimat|ilk \d+|adet ile sınırlı|ödeme|odeme|mağaza|magaza|medyan|gerçek fırsat|gercek firsat|taraf satıcı|taraf satici|fiyat\w*\s*:|fark\s*:|son fiyat|kontrol edin|tanesi|uygula\w*|yerine|(?:adet|birim|tane)\s*(?:baş[ıi]|fiyat[ıi])",
    re.IGNORECASE,
)

DEAL_WORDS = frozenset(
    "fırsat firsat fırsatı firsati indirim indirimli indirimde kampanya kampanyası efsane süper super "
    "flaş flas şok sok son dakika ucuz yeni düştü dustu düşüş dusus fiyat fiyatı fiyati hata hatalı "
    "hatali yanlış yanlis kaçmaz kacmaz kaçırma kacirma kupon kod kodu stok stokta sınırlı sinirli "
    "bedava ücretsiz ucretsiz müthiş muthis harika inanılmaz inanilmaz muhteşem muhtesem dev bomba "
    "patladı patladi rekor tarihi en düşük dusuk taksit kargo hemen acele alın alin al kaçırmayın "
    "kacirmayin bugün bugun şimdi simdi sadece yalnızca yalnizca tl try ₺ ve ile bu da de den "
    "dan ye ya için icin sepette sepet link tıkla tikla incele git gör gor "
    "ilk adet geçerli gecerli kadar fiyatına fiyatina fiyatıyla fiyatiyla indi düşürdü dusurdu "
    "uyarı uyari dikkat önemli onemli duyuru haber güncel guncel yenilendi 3p alımda alimda tanıtım tanitim "
    "işbirliği isbirligi reklam medyanın medyanin".split()
)
STORES = frozenset(
    "amazon hepsiburada trendyol n11 mediamarkt teknosa vatan çiçeksepeti ciceksepeti a101 bim şok "
    "migros carrefour decathlon ikea koçtaş koctas lcw defacto boyner morhipo getir yemeksepeti "
    "steam epic gamivo ebay aliexpress temu pazarama ptt avm itopya incehesap gittigidiyor "
    "sahibinden dolap kitapyurdu d&r idefix".split()
)
_STORE_PATTERN = "|".join(re.escape(store) for store in sorted(STORES, key=len, reverse=True))
_STORE_FROM_PREFIX_RE = re.compile(
    rf"^\s*(?:{_STORE_PATTERN})['’](?:den|dan|ten|tan)\s+",
    re.IGNORECASE,
)
_STORE_LOCATIVE_PREFIX_RE = re.compile(
    rf"^(?:{_STORE_PATTERN})['’](?:de|da|te|ta)\b",
    re.IGNORECASE,
)


def strip_store_prefix(text: str) -> str:
    """Yalnız ``n11'den Ürün`` gibi kaynak önekini kaldır; ``N11’de`` korunur."""
    return _STORE_FROM_PREFIX_RE.sub("", text or "", count=1)


_UNIT_RE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s?(?:gb|tb|mb|mah|w|kw|inç|inch|in|cm|mm|ml|lt|l|kg|gr|g|adet|'?li|'?lı|"
    r"'?lu|'?lü|hz|mp|ghz|rpm|bar|litre|metre|m|watt|volt|v|mbps|ssd|ram)\b|\d+\"",
    re.IGNORECASE,
)
_PRICE_LEAD_RE = re.compile(r"^\s*[\U0001F4B0\U0001F3F7\U0001F4B8\U0001F3AF\U0001F53B\U0001F4B3\u2705]")   # 💰 🏷 💸 🎯 🔻 💳 ✅ → fiyat satırı
_PRODUCT_EMOJI_RE = re.compile(r"^\s*[\U0001F6CD\U0001F6D2\U0001F4E6\U0001F48E]")   # 🛍 🛒 📦 💎 → ürün satırı
_MODEL_RE = re.compile(r"\b(?=[A-Za-z0-9\-/]*[A-Za-z])(?=[A-Za-z0-9\-/]*\d)[A-Za-z0-9\-/]{3,}\b")
_CATALOG_CONNECTION_RE = re.compile(r"\bUSB-[A-Z0-9]+\b", re.IGNORECASE)


def _remove_noise(line: str) -> str:
    line = _URL_RE.sub(" ", line)
    line = _MENTION_RE.sub(" ", line)
    line = _SYMBOL_RE.sub(" ", line)

    def keep_product_percentage(match: re.Match[str]) -> str:
        suffix = norm(line[match.end():])
        # Leading composition/feature percentages (e.g. "%0 Yağlı Yoğurt") are
        # part of a product name; discount/statistic percentages remain noise.
        composition = bool(re.fullmatch(r"%\s*(?:0|100)", match.group())
                           and re.match(r"\s*(?:yer\s+f[ıi]st[ıi]ğ[ıi]|kakao|pamuk|doğal)\b", suffix))
        if (not line[:match.start()].strip() or composition) and re.search(r"[^\W\d_]", suffix) \
                and not re.search(r"indirim|altında|fark|tasarruf", suffix):
            return match.group()
        return " "

    line = _PERCENT_RE.sub(keep_product_percentage, line)
    line = _LABEL_PREFIX_RE.sub("", line)
    return " ".join(line.split())


_TAG_RE = re.compile(r"^\s*[\[(]\s*([^\])\n]{1,25}?)\s*[\])]\s*")


def _strip_tags(text: str) -> str:
    """Baştaki "[Amazon]" / "(Kupon)" gibi etiketleri sil (içi yalnız fırsat/mağaza sözcüğüyse)."""
    while True:
        match = _TAG_RE.match(text)
        if not match:
            return text
        words = [norm(w) for w in _TOKEN_RE.findall(match.group(1))]
        if words and all(w in DEAL_WORDS or w in STORES for w in words):
            text = text[match.end():]
        else:
            return text


_EDGE_JUNK = "!?.,:;|-–—•/ "


def _strip_edge_price(line: str) -> str:
    """Satırın en başındaki/sonundaki fiyatı sil; ortadaki (ör. kupon "1.000/300TL") kalsın."""
    text = line.rstrip()
    matches = list(PRICE_RE.finditer(text))
    if matches:
        last = matches[-1]
        if last.end() >= len(text.rstrip(_EDGE_JUNK)) and not text[:last.start()].rstrip().endswith("/"):
            text = text[:last.start()]
    matches = list(PRICE_RE.finditer(text))
    if matches:
        first = matches[0]
        if first.start() <= len(text) - len(text.lstrip(_EDGE_JUNK)):
            text = text[first.end():]
    return text.strip()


def _trim_edges(line: str) -> str:
    """Baş/sondaki fırsat sözcüklerini, kenar fiyatını ve ayraçları temizle (ürün adı ortada kalır)."""
    line = strip_store_prefix(_strip_tags(line))

    def bare(word: str) -> str:
        return norm(re.sub(r"^[^\w]+|[^\w]+$", "", word))

    words = line.split()
    changed = True
    while changed and words:
        changed = False
        current = " ".join(words)
        stripped = _strip_edge_price(current)          # önce kenar fiyatı ("… 5.399 TL"), sonra sözcükler
        if stripped != current:
            words, changed = stripped.split(), True
            continue
        preserve_descriptor = bare(words[0]) in {"yeni", "super", "ilk"} and len(words) >= 3
        if (bare(words[0]) in DEAL_WORDS and not preserve_descriptor) or not bare(words[0]):
            words.pop(0)
            changed = True
        elif bare(words[-1]) in DEAL_WORDS or bare(words[-1]) in STORES or not bare(words[-1]):
            words.pop()
            changed = True
    line = " ".join(words)
    line = _LEAD_TRIM_RE.sub("", line)
    line = _TRAIL_TRIM_RE.sub("", line)
    return line.strip()


def _segments(line: str) -> list[str]:
    parts = re.split(r"\s[|•·]\s|\s[–—]\s", line)
    return [part for part in parts if part.strip()]


_GENERIC_TITLE_RE = re.compile(
    r"^(?:yoğun\s+ilgi\s+görüyor\s*$|durum\s*:|takip\s+kuponu\b|kampanya(?:lar)?\s*(?::|\d)|(?:\d+\s*g(?:ün)?\s+)?medyan\b|"
    r"(?:anlık|anlik) indirim|bildirimlerinizi|\d+\s+adet kaldı|"
    r"ürün\s+altından\s+(?:aktif|etkinleştir)\w*|"
    r"(?:sepete\s+)?\d+\s+adet(?:\s+sepete)?\s+ekle\w*|"
    r"(?:başlangıç|baslangic|bitiş|bitis|tarih)\s*:|"
    r"(?:amazon|n11|trendyol|hepsiburada)\s+indirim kuponu\s*$|"
    r"sohbet\s+(?:grubumuz|kanalımız)(?:\s+(?:için|icin))?\s*$|"
    r"mesajı(?:\s+(?:gör|gor|beğen|begen)\w*)?(?:\s+\d+)?\s*$|"
    r"[a-z0-9][a-z0-9_-]*\s+(?:koduyla|kodla|kuponuyla|kuponla)"
    r"(?:\s+(?:fiyat\w*\s*)?(?:\d[\d.,]*\s*(?:tl|₺|try)|₺\s*\d[\d.,]*))?\s*$)",
    re.IGNORECASE,
)
_CONDITION_WORDS = frozenset(
    "tekli alımda alimda alımı alimi alım alim adet tane al alana birine ine "
    "ikincisi ikinci ürün urun ile ve tek fiyatına fiyatina geliyor civarına civarina".split()
)


def is_generic_title(text: str) -> bool:
    """Reject standalone conditions/statistics/CTA, not product pack sizes.

    Numeric tokens previously earned model/quantity bonuses, allowing e.g.
    '3 adet alımda' to outrank a long real product name. Such a fragment is
    unsafe as a dedup identity even when it is the only available text.
    """
    plain = norm(_remove_noise(text)).strip(" ()[]{}.,:;!-")
    if not plain or plain in STORES or _GENERIC_TITLE_RE.search(plain):
        return True
    if _STORE_LOCATIVE_PREFIX_RE.match(plain) and re.search(r"\b\d+\s+alana\s+\d+\b", plain):
        # Mağaza + BOGO + kupon kodu satırı ürün kimliği değildir. Arama
        # temizliği ``bedava``yı kaldırsa bile kalan ``1 alana 1`` yakalanır.
        return True
    first_word = plain.split(maxsplit=1)[0] if plain else ""
    if first_word in STORES and re.search(r"\btanıtım\b", plain):
        # ``Amazon tanıtım @kanal GOOGLE`` ve ``n11 (Satıcı: n11)
        # Tanıtım`` mağaza/kanal metadata'sıdır; başındaki 🛍️ ve içindeki
        # sayısal mağaza adı bunları gerçek ürün adından daha yüksek puanlayabiliyordu.
        return True
    if re.search(r"\bseçili ürünlerde\b", plain):
        # Arama sorgusu ``Sepette 2.ye %50 İndirim`` kısmını temizledikten
        # sonra bile kalan ``Seçili ürünlerde…`` kampanya metadata'sıdır;
        # indirim/kupon kelimesinin artık görünmesi gerekmez.
        return True
    if re.search(r"\b(?:çıkmıştı|cikmisti|tekrar)\b", plain) \
            and re.search(r"\b(?:sepette|kargo|teslimat|paket\w*|ödemek|alabilirsiniz)\b", plain):
        return True
    if "bildirimlerinizi aç" in plain or "bildirimlerinizi ac" in plain:
        return True
    words = re.findall(r"[^\W\d_]+", PRICE_RE.sub(" ", plain))
    return not words or all(w in _CONDITION_WORDS or w in DEAL_WORDS or w in STORES for w in words)


def _is_noise(text: str) -> bool:
    if is_generic_title(text):
        return True
    tokens = [norm(re.sub(r"^[^\w]+|[^\w]+$", "", word)) for word in text.split()]
    tokens = [tok for tok in tokens if tok]
    if not tokens or not re.search(r"[^\W\d_]", text):
        return True
    return all(tok in DEAL_WORDS or tok in STORES for tok in tokens)


def _has_product_context_before(lines: Sequence[str], line_index: int) -> bool:
    """Kuponlu yalın tutardan önce bağımsız bir ürün satırı bulunduğunu doğrula."""
    for raw in reversed(list(lines[:line_index])):
        if not raw.strip() or _URL_RE.search(raw):
            continue
        cleaned = _trim_edges(_remove_noise(raw))
        if cleaned and not _is_noise(cleaned):
            return True
    return False


def _score_line(raw: str, cleaned: str, position: int, hint_tokens: set[str], lead_product: bool = False) -> float:
    words = cleaned.split()
    count = len(words)
    if count == 0:
        return -99.0
    score = 0.0
    if _MODEL_RE.search(cleaned):
        score += 3
    if _UNIT_RE.search(cleaned):
        score += 2
    if 2 <= count <= 16:
        score += 1.5
    elif count == 1:
        score -= 1
    if 12 <= len(cleaned) <= 120:
        score += 1
    if len(cleaned) > 170:
        score -= 3
    plain = _SYMBOL_RE.sub("", raw).strip()
    label = _META_LABEL_RE.match(plain)
    if label and norm(label.group(1)) not in _TITLE_LABELS and len(label.group(1).split()) <= 2:
        score -= 3
    if _META_WORDS_RE.search(norm(plain)):
        score -= 3
    # Bazı kanallar ürün adını ``✅ Kapüşonlu Uzun Kollu …`` diye işaretler.
    # Sayı/fiyat/kampanya metadatası taşımayan açıklayıcı satırı fiyat emojisi
    # diye cezalandırma; sayısal ``✅ 1.100TL - S/4XL`` kalıbı eski kuralda kalır.
    checkmark_product = (
        raw.lstrip().startswith("✅") and count >= 4
        and not any(char.isdigit() for char in cleaned)
        and not PRICE_RE.search(raw) and not _META_WORDS_RE.search(norm(plain))
    )
    if lead_product or checkmark_product:
        score += 4
    elif _PRICE_LEAD_RE.match(raw):
        score -= 3                       # "💰 2 adet alımda 250₺" gibi fiyat satırı başlık olmasın
    elif count <= 4 and PRICE_RE.search(raw):
        score -= 3                       # "✅1.100TL - S/4XL Arası Bedenler" gibi kısa fiyat satırı
    if cleaned.isupper() and count <= 3:
        score -= 2
    if sum(ch.isdigit() for ch in cleaned) > len(cleaned) * 0.5:
        score -= 3
    if raw.lstrip().startswith(("•", "-", "*", "✔", "✅", "▪", "➡", "👉")) and position > 0 \
            and not checkmark_product:
        score -= 1
    score += {0: 2.0, 1: 1.0, 2: 0.5}.get(position, 0)
    if hint_tokens and count >= 3:
        line_tokens = {norm(w) for w in words}
        overlap = len(line_tokens & hint_tokens) / max(1, len(line_tokens))
        if overlap >= 0.5:
            score += 3
    return score


def priced_catalog_title(text: str) -> str:
    """Extract a catalog name following a leading price/financing parenthesis."""
    line = next((s.strip() for s in (text or "").splitlines() if s.strip()), "")
    match = PRICE_RE.search(line)
    if not match or _SYMBOL_RE.sub("", line[:match.start()]).strip():
        return ""
    rest = line[match.end():].lstrip()
    if not rest.startswith("("):
        return ""
    depth = 0
    for i, char in enumerate(rest):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                financing, rest = rest[1:i], rest[i + 1:].strip()
                break
    else:
        return ""
    if not re.search(r"taksit|banka|indirim|peşin|pesin", norm(financing)):
        return ""
    if (len(rest.split()) < 4 or not re.match(r"[A-ZÇĞİÖŞÜ]{2,}\b", rest)
            or not _MODEL_RE.search(rest)):
        return ""
    return rest


def extract_title(text: str, hints: Sequence[str] = (), limit: int = 160) -> str:
    """Mesajdan ürün başlığını seç. Bulamazsa boş döner."""
    catalog = priced_catalog_title(text)
    if catalog:
        return extract_title(catalog, hints, limit)
    selected = _selected_product_block(text or "")
    if selected is not None:
        block, _ = selected
        if not block:
            return ""
        block = _PRODUCT_HEADER_RE.sub("", block, count=1)
        block = _FEATURED_PRODUCT_RE.sub("", block, count=1)
        return extract_title(block, hints, limit)
    hint_tokens: set[str] = set()
    for hint in hints or ():
        hint_tokens.update(norm(w) for w in _TOKEN_RE.findall(hint or ""))
    entries: list[tuple[float, int, str, str]] = []
    position = 0
    lines = (text or "").split("\n")
    for index, raw in enumerate(lines):
        if not raw.strip():
            continue
        # Folder-marked category lines are channel metadata, not product names.
        # Do not reject category words in ordinary product titles.
        if re.match(r"^\s*📂\ufe0f?", raw):
            position += 1
            continue
        pieces = _segments(raw)
        best: tuple[float, str] | None = None
        for piece_index, piece in enumerate(pieces):
            cleaned = _trim_edges(_remove_noise(piece))
            combined_prefix = ""
            if piece_index and _UNIT_RE.search(cleaned):
                prefix = _trim_edges(_remove_noise(pieces[piece_index - 1]))
                if prefix and not _is_noise(prefix) and not _META_WORDS_RE.search(norm(prefix)):
                    combined_prefix = prefix
                    cleaned = f"{prefix} – {cleaned}"
            if is_generic_title(piece) or _is_noise(cleaned):
                continue
            score = _score_line(raw, cleaned, position, hint_tokens, bool(_PRODUCT_EMOJI_RE.match(raw)))
            if combined_prefix and not _MODEL_RE.search(combined_prefix) \
                    and _MODEL_RE.search(cleaned[len(combined_prefix):]):
                # ``Ütü – 2400W … (DST5010/10)``: birleşik ad, tire sonrası
                # model/birim kimliğini korur. Uzunluğun genel puan cezası,
                # yalnız ``Philips 5000 Serisi Buharlı Ütü`` önekini seçtirmemeli.
                score += 1.0
            if best is None or score > best[0]:
                best = (score, cleaned)
        if best is not None:
            entries.append((best[0], index, best[1], raw))
        position += 1
    if not entries:
        return ""
    entries.sort(key=lambda item: (-item[0], item[1]))
    score, index, title, _ = entries[0]
    if score < 1.0:
        title = next((entry[2] for entry in sorted(entries, key=lambda item: item[1])), "")
        index = next((entry[1] for entry in sorted(entries, key=lambda item: item[1])), index)
    # Kısa başlık (marka) + hemen altındaki devam satırı
    if len(title.split()) <= 2 and index + 1 < len(lines):
        nxt = lines[index + 1]
        if nxt.strip() and not re.match(r"^\s*📂\ufe0f?", nxt):
            cleaned = _trim_edges(_remove_noise(nxt))
            if cleaned and not _is_noise(cleaned) and not _META_WORDS_RE.search(norm(nxt)) \
                    and not _META_LABEL_RE.match(nxt) and len(cleaned.split()) <= 12:
                title = f"{title} {cleaned}"
    elif index > 0 and (re.match(r"[a-zçğıöşü\d]", title) or title.lstrip().startswith(("+", "&"))
                        or lines[index].lstrip().startswith(("+", "&"))) and lines[index - 1].strip():
        prev = lines[index - 1]
        cleaned = _trim_edges(_remove_noise(prev))
        continuation = re.sub(r"^[+&]\s*", "", title)
        if not re.match(r"^\s*📂\ufe0f?", prev) and cleaned and len(cleaned.split()) <= 2 and not _is_noise(cleaned) \
                and not _META_WORDS_RE.search(norm(prev)) and not _META_LABEL_RE.match(prev) \
                and not cleaned.endswith(":"):
            title = f"{cleaned} {continuation}"
    title = " ".join(title.split())
    if len(title) > limit:
        # Katalog adındaki açık model/bağlantı kimliğini kırpıp atmak, ürün
        # kimliğini ve dedup eşleşmesini zayıflatır. Model kodu sınırın ötesinde
        # kalan ya da USB-A/USB-C gibi katalog bağlantısı taşıyan makul uzunluktaki
        # adlara, çağıran daha dar bir limit istemediyse 80 karakter ek alan ver;
        # diğer uzun başlıklar mevcut 160 karakter görünümünü korur.
        identity_extension = limit >= 160 and bool(
            _MODEL_RE.search(title[limit:]) or _CATALOG_CONNECTION_RE.search(title)
        )
        if not identity_extension or len(title) > limit + 80:
            cut = title[:limit].rsplit(" ", 1)[0].rstrip(" ,.-–|")
            title = cut + "…"
    return title


def analyze(text: str, strike: Sequence[tuple[int, int]] = (),
            hints: Sequence[str] = ()) -> tuple[str, PriceInfo]:
    """(başlık, fiyat bilgisi) — bildirim üst bloğu için."""
    return extract_title(text, hints), extract_price(text, strike)
