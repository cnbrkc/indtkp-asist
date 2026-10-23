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
_NUM = r"\d{1,3}(?:[.\u00a0\u202f,]\d{3})+(?:[,.]\d{1,2})?|\d+(?:[,.]\d{1,2})?"
_LETTER = "A-Za-zÇĞİÖŞÜçğıöşü"

PRICE_RE = re.compile(
    rf"(?<![\w.,])(?:"
    rf"(?P<pre>{_CUR})\s*(?P<a1>{_NUM})(?![\d])"
    rf"|(?P<a2>{_NUM})\s*(?P<bin>bin\s*)?(?P<cur>{_CUR}|lira)(?![{_LETTER}])(?P<suf>[’'][^\W\d_]+)?"
    rf")",
    re.IGNORECASE,
)

# "Fiyat: 1299", "💰 1.299" gibi para birimi yazılmayan etiketli tutarlar
LABELED_RE = re.compile(
    rf"(?:fiyat\w*|price|sepette|net)\s*[:=\-–]?\s*(?P<a>{_NUM})(?![\d.,]*\s*"
    rf"(?:%|gb|tb|mb|mah|w\b|inç|inch|cm|mm|ml|lt\b|l\b|kg|gr\b|g\b|adet|hz|mp|ay\b|gün|saat))",
    re.IGNORECASE,
)
EMOJI_LINE_RE = re.compile(
    rf"^\s*(?:[💰🏷💵💸🔥✅💲]\ufe0f?\s*)+(?P<a>{_NUM})\s*$"
)


def parse_amount(raw: str) -> float | None:
    """'1.299,90' / '1,299.90' / '1299' / '129,9' → float."""
    text = (raw or "").replace("\u00a0", "").replace("\u202f", "").strip()
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
    r"ilk\s+ay|değişim|degisim|nakliye|hızlı|hizli|prime",
    re.IGNORECASE,
)
_COUPON_SLASH_RE = re.compile(r"\d[\d.,]*\s*/\s*$")          # "1.000/300TL" → eşik/indirim yazımı
_COND_BEFORE_RE = re.compile(
    r"(?:\d+\s*adet(?:\s*al[ıi]m\w*)?|al[ıi]mda|al[ıi]mlarda|\d+\.\s*ürün\w*|ikinci\s+ürün\w*|"
    r"\d+\s*al\s*\d+\s*öde)\s*$"
)                                                           # "2 adet alımda 48 TL", "2. ürün 1 TL"
_FIRST_PARTY_RE = re.compile(r"\b1p\b")                    # Amazon (1P) son fiyatı → eski/başka fiyat
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


def _classify(line: str, start: int, end: int, abs_start: int, strike: Sequence[tuple[int, int]],
              suffix: str = "") -> tuple[str, bool]:
    """Tutarın türünü belirle. ``end`` para biriminin bittiği yerdir; ``suffix`` 'ye/'den gibi ektir."""
    after_raw = suffix + line[end:]
    before_raw = line[:start]
    after_clean = after_raw.strip()
    before_tokens = _tokens(before_raw)[-3:]

    if any(a <= abs_start < b for a, b in strike):
        return "old", False

    if _THRESHOLD_DATIVE_RE.match(norm(after_clean)):
        return "other", False
    if _DATIVE_RE.match(after_clean):
        return "price", True                           # "1.199 TL'ye düştü"
    if _ABLATIVE_RE.match(after_clean):
        rest = _tokens(_ABLATIVE_RE.sub("", after_clean, count=1))[:1]
        if not (rest and rest[0].startswith("başlayan")):
            return "old", False                        # "2.999 TL'den 1.999 TL'ye"
    if _MONTHLY_RE.match(after_raw):
        return "other", False

    connector = bool(re.match(r"^\s*[+&,;|/\-–]", after_raw))   # "6.499 TL + kargo bedava"
    after_tokens = _tokens(after_raw)[:2]
    if not connector and after_tokens:
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
    for index, line in enumerate((text or "").split("\n")):
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
            candidates.append(Candidate(value, match.group().strip(), cls, strong, index, match.start()))
            seen_spans.append(match.span())
        for regex in (LABELED_RE, EMOJI_LINE_RE):
            match = regex.search(line)
            if not match:
                continue
            if any(s <= match.start("a") < e for s, e in seen_spans):
                continue
            value = parse_amount(match.group("a"))
            if value is None or value > 10_000_000:
                continue
            cls, _ = _classify(line, match.start("a"), match.end("a"), offset + match.start("a"), strike)
            candidates.append(Candidate(value, match.group().strip(), cls, True, index, match.start("a")))
        offset += len(line) + 1
    return candidates


def extract_price(text: str, strike: Sequence[tuple[int, int]] = ()) -> PriceInfo:
    """Mesajdaki ürün fiyatını seç. ``confidence`` 'low' ise filtre kullanmaz."""
    cands = find_candidates(text, strike)
    prices = [c for c in cands if c.cls == "price"]
    if prices:
        strong = [c for c in prices if c.strong]
        pool = strong or prices
        best = min(pool, key=lambda c: (c.value, c.line, c.start))
        return PriceInfo(best.value, best.shown, "high" if strong else "medium",
                         "etiketli/bağlamlı fiyat" if strong else "fiyat")
    conds = [c for c in cands if c.cls == "cond"]
    if conds:
        best = min(conds, key=lambda c: (c.value, c.line, c.start))
        return PriceInfo(best.value, best.shown, "medium", "çoklu alım fiyatı")
    if not cands:
        for line in (text or "").split("\n"):
            if _FREE_RE.search(line) and not _FREE_BLOCK_RE.search(line):
                return PriceInfo(0.0, "ücretsiz", "medium", "ücretsiz/bedava")
        return PriceInfo(None, "", "none", "fiyat yok")
    fallback = [c for c in cands if c.cls in {"history", "old"}]
    if fallback:
        best = min(fallback, key=lambda c: (c.value, c.line, c.start))
        return PriceInfo(best.value, best.shown, "low", "yalnız eski/geçmiş fiyat")
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
    r"(?<![\w-])link(?![\w-])|tıkla|tikla|sepette|iade|teslimat|ilk \d+|adet ile sınırlı|ödeme|odeme|mağaza|magaza|medyan|gerçek fırsat|gercek firsat|taraf satıcı|taraf satici|fiyat\w*\s*:|fark\s*:|son fiyat|kontrol edin",
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
_UNIT_RE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s?(?:gb|tb|mb|mah|w|kw|inç|inch|in|cm|mm|ml|lt|l|kg|gr|g|adet|'?li|'?lı|"
    r"'?lu|'?lü|hz|mp|ghz|rpm|bar|litre|metre|m|watt|volt|v|mbps|ssd|ram)\b|\d+\"",
    re.IGNORECASE,
)
_PRICE_LEAD_RE = re.compile(r"^\s*[\U0001F4B0\U0001F3F7\U0001F4B8\U0001F3AF\U0001F53B\U0001F4B3\u2705]")   # 💰 🏷 💸 🎯 🔻 💳 ✅ → fiyat satırı
_PRODUCT_EMOJI_RE = re.compile(r"^\s*[\U0001F6CD\U0001F6D2\U0001F4E6\U0001F48E]")   # 🛍 🛒 📦 💎 → ürün satırı
_MODEL_RE = re.compile(r"\b(?=[A-Za-z0-9\-/]*[A-Za-z])(?=[A-Za-z0-9\-/]*\d)[A-Za-z0-9\-/]{3,}\b")


def _remove_noise(line: str) -> str:
    line = _URL_RE.sub(" ", line)
    line = _MENTION_RE.sub(" ", line)
    line = _PERCENT_RE.sub(" ", line)
    line = _SYMBOL_RE.sub(" ", line)
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
    line = _strip_tags(line)

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
        if bare(words[0]) in DEAL_WORDS or not bare(words[0]):
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


def _is_noise(text: str) -> bool:
    tokens = [norm(re.sub(r"^[^\w]+|[^\w]+$", "", word)) for word in text.split()]
    tokens = [tok for tok in tokens if tok]
    if not tokens or not re.search(r"[^\W\d_]", text):
        return True
    return all(tok in DEAL_WORDS or tok in STORES for tok in tokens)


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
    if lead_product:
        score += 4
    elif _PRICE_LEAD_RE.match(raw):
        score -= 3                       # "💰 2 adet alımda 250₺" gibi fiyat satırı başlık olmasın
    elif count <= 4 and PRICE_RE.search(raw):
        score -= 3                       # "✅1.100TL - S/4XL Arası Bedenler" gibi kısa fiyat satırı
    if cleaned.isupper() and count <= 3:
        score -= 2
    if sum(ch.isdigit() for ch in cleaned) > len(cleaned) * 0.5:
        score -= 3
    if raw.lstrip().startswith(("•", "-", "*", "✔", "✅", "▪", "➡", "👉")) and position > 0:
        score -= 1
    score += {0: 2.0, 1: 1.0, 2: 0.5}.get(position, 0)
    if hint_tokens and count >= 3:
        line_tokens = {norm(w) for w in words}
        overlap = len(line_tokens & hint_tokens) / max(1, len(line_tokens))
        if overlap >= 0.5:
            score += 3
    return score


def extract_title(text: str, hints: Sequence[str] = (), limit: int = 160) -> str:
    """Mesajdan ürün başlığını seç. Bulamazsa boş döner."""
    hint_tokens: set[str] = set()
    for hint in hints or ():
        hint_tokens.update(norm(w) for w in _TOKEN_RE.findall(hint or ""))
    entries: list[tuple[float, int, str, str]] = []
    position = 0
    lines = (text or "").split("\n")
    for index, raw in enumerate(lines):
        if not raw.strip():
            continue
        pieces = _segments(raw)
        best: tuple[float, str] | None = None
        for piece in pieces:
            cleaned = _trim_edges(_remove_noise(piece))
            if _is_noise(cleaned):
                continue
            score = _score_line(raw, cleaned, position, hint_tokens, bool(_PRODUCT_EMOJI_RE.match(raw)))
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
        if nxt.strip():
            cleaned = _trim_edges(_remove_noise(nxt))
            if cleaned and not _is_noise(cleaned) and not _META_WORDS_RE.search(norm(nxt)) \
                    and not _META_LABEL_RE.match(nxt) and len(cleaned.split()) <= 12:
                title = f"{title} {cleaned}"
    elif index > 0 and re.match(r"[a-zçğıöşü\d]", title) and lines[index - 1].strip():
        prev = lines[index - 1]
        cleaned = _trim_edges(_remove_noise(prev))
        if cleaned and len(cleaned.split()) <= 2 and not _is_noise(cleaned) \
                and not _META_WORDS_RE.search(norm(prev)) and not _META_LABEL_RE.match(prev) \
                and not cleaned.endswith(":"):
            title = f"{cleaned} {title}"
    title = " ".join(title.split())
    if len(title) > limit:
        cut = title[:limit].rsplit(" ", 1)[0].rstrip(" ,.-–|")
        title = cut + "…"
    return title


def analyze(text: str, strike: Sequence[tuple[int, int]] = (),
            hints: Sequence[str] = ()) -> tuple[str, PriceInfo]:
    """(başlık, fiyat bilgisi) — bildirim üst bloğu için."""
    return extract_title(text, hints), extract_price(text, strike)
