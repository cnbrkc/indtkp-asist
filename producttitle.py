"""Ürün bağlantısı tespiti, kupon kampanya başlığı ve arama sorgusu üretimi (saf)."""

from __future__ import annotations

import price_filter
import re
import urllib.request
from constants import (
    MEDIA_FILE_PATH_RE,
    MESSAGE_LIMIT,
    NON_PRODUCT_HOSTS,
    NON_PRODUCT_HOST_RE,
    TRACKING_QUERY_KEYS,
    TRACKING_QUERY_PREFIXES,
)
from keywords import normalize
from sanitize import (
    URL_RE,
    WORD_TOKEN_RE,
    _LEADING_NOISE_RE,
    _as_message,
    _price_search_service,
    clean_disclosure_tokens,
    clean_url,
    is_instagram_url,
    is_search_link,
    is_whatsapp_url,
    message_entities,
    message_text,
    utf16_slice,
)
from typing import Any


def _is_tracking_query(query: str) -> bool:
    """Sorgu yalnız izleme/iş ortaklığı etiketi mi taşıyor? (bkz. ``TRACKING_QUERY_KEYS``)

    Fail-open: tanınmayan tek bir anahtar ya da değeri adres olan bir parametre
    (yönlendirme hedefi) varsa sorgu içerik sayılır.
    """
    return all(
        "://" not in value
        and (key.lower() in TRACKING_QUERY_KEYS or key.lower().startswith(TRACKING_QUERY_PREFIXES))
        for key, value in urllib.parse.parse_qsl(query, keep_blank_values=True)
    )


def is_product_link(url: Any) -> bool:
    """Bağlantı ürün/fırsat sayfasına götürebilir mi? (bkz. ``NON_PRODUCT_HOSTS``)

    Yolu olmayan adres (``https://www.amazon.com.tr``) mağazanın ana sayfasıdır; "🛍 Amazon"
    gibi mağaza adına gömülü böyle bir link ürün linki sayılmaz. Ana sayfaya eklenen
    iş ortaklığı/izleme etiketi (``/?tag=kanal-21``, ``/?utm_source=…``) bunu değiştirmez.
    Doğrudan görsel/video dosyası da (``…/urun.jpg``) ürün sayfası değildir.
    """
    cleaned = clean_url(url)
    if not cleaned or is_search_link(cleaned) or is_whatsapp_url(cleaned) or is_instagram_url(cleaned):
        return False
    try:
        parsed = urllib.parse.urlsplit(cleaned)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    return (parsed.scheme in {"http", "https"} and "." in host
            and bool(parsed.path.strip("/") or not _is_tracking_query(parsed.query))
            and not MEDIA_FILE_PATH_RE.search(parsed.path)
            and not NON_PRODUCT_HOST_RE.search(host)
            and not any(host == item or host.endswith("." + item) for item in NON_PRODUCT_HOSTS))


# Fiyatlar ve indirim yüzdeleri ürün adı değildir; bazı kaynaklar bunları
# başlığın önüne, bazıları arkasına yazar. Arama sorgusunda yalnızca satırdaki
# ürün metni kalır. Bu işlem tamamen yereldir (ağ isteği yoktur).
_SEARCH_AMOUNT = r"(?:\d{1,3}(?:[.,\s]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
_SEARCH_CURRENCY = r"(?:YTL|TRY|TL|USD|EUR|₺|[$€£])"
_SEARCH_PRICE_RE = re.compile(
    rf"(?<![\w])(?:{_SEARCH_CURRENCY}\s*{_SEARCH_AMOUNT}|"
    rf"{_SEARCH_AMOUNT}\s*{_SEARCH_CURRENCY})(?![\w])",
    re.IGNORECASE,
)
_SEARCH_DISCOUNT_RE = re.compile(
    r"(?<![\w])(?:(?:%|yüzde|yuzde)\s*\d{1,3}(?:[.,]\d+)?"
    r"(?:['’]?(?:ya|ye|a|e|ı|i|u|ü))?|"
    r"\d{1,3}(?:[.,]\d+)?\s*%(?:['’]?(?:ya|ye|a|e|ı|i|u|ü))?)"
    r"(?:\s*(?:indirim(?:li)?|discount|off|tasarruf))?",
    re.IGNORECASE,
)
_SEARCH_NOISE_WORDS = frozenset(normalize(word) for word in """
    fiyat fiyatı fiyati fiyatlar price pricing tl try ytl usd eur
    indirim indirimli indirimde indirimden discount discounts off tasarruf savings
    yüzde yuzde oran oranı orani percent
    kampanya kampanyası kampanyasi kampanyalı kampanyali campaign fırsat firsat
    fırsatı firsati fırsata firsata fırsatın firsatin deal
    sepette sepete kupon kuponu kuponla coupon voucher kod kodu promo
    bugün bugun şimdi simdi güncel guncel son eski yeni düşen dusen düştü dustu
    düşüş dusus varan kadar yerine daha ucuza ucuzladı ucuzladi
    stok stokta stoklarla sınırlı sinirli geçerli gecerli
    taksit taksitle peşin pesin kargo ücretsiz ucretsiz bedava
    ürün ürünü urun urunu ürüne urune başlık baslik özellik özelliği ozellik detay
    link linki linkten product ürünler urunler
    hemen kaçmaz kacmaz kaçırma kacirma tıkla tikla satın satin
    alırken alirken oranında oraninda
    whatsapp whatsapptan whatsapp'tan reklamcı reklamci reklam bilgi gizli bağlantı baglanti
    bağlantısı baglantisi burada
    den ye
""".split())
_SEARCH_CTA_ONLY_WORDS = frozenset({"al", "bak", "git", "gor", "gör", "incele", "tikla", "tıkla"})


def _search_line_candidate(raw_line: str) -> str:
    """Fiyat/indirim/CTA satırlarını ayıkla, ürün adı olabilecek metni döndür."""
    if re.match(r"^\s*📂\ufe0f?", raw_line or ""):
        return ""
    line = URL_RE.sub(" ", raw_line or "")
    line = clean_disclosure_tokens(line)
    line = price_filter.strip_store_prefix(line)
    # Hashtag-only tanıtım satırları sanitize_message tarafından kaldırılır;
    # başlıkta gerçek bir ürün adı varsa kelimeyi koruyup # işaretini sil.
    line = re.sub(r"(?<!\w)#(?=\w)", "", line)
    line = _SEARCH_PRICE_RE.sub(" ", line)
    line = _SEARCH_DISCOUNT_RE.sub(" ", line)
    def remove_search_noise(match: re.Match[str]) -> str:
        token = normalize(match.group())
        # "link" is CTA noise as a standalone word, but part of the brand
        # TP-Link when hyphenated. Preserve that one compound brand token.
        hyphenated_link = (
            token == "link" and match.start() > 1 and line[match.start() - 1] == "-"
            and line[match.start() - 2].isalpha()
        )
        return " " if not hyphenated_link and (
            token in _SEARCH_NOISE_WORDS or token in _SEARCH_CTA_ONLY_WORDS
        ) else match.group()

    line = WORD_TOKEN_RE.sub(remove_search_noise, line)
    line = _LEADING_NOISE_RE.sub("", line)
    line = re.sub(r"[^\w\s&+.,;:'’/–—-]+", " ", line, flags=re.UNICODE)
    line = re.sub(r"[()\[\]{}]+", " ", line)
    line = re.sub(r"\s+", " ", line).strip(" \t\r\n,;:.|/\\-–—·•")
    if not any(char.isalpha() for char in line):
        return ""
    tokens = [normalize(token) for token in WORD_TOKEN_RE.findall(line)]
    if tokens and set(tokens) <= _SEARCH_CTA_ONLY_WORDS:
        return ""
    return line


# Arama sorgusu ve bildirim başlığı en fazla bu uzunlukta tutulur.
SEARCH_QUERY_LIMIT = 200


# Fiyat/indirim/tanıtım başlığı kelimeleri: yalnızca bunlardan oluşan satır
# ürün adı değildir (ör. "DÜŞÜŞÜ", "FİYAT DÜŞÜŞÜ", "Fark", "En düşük fiyat").
_SEARCH_DEAL_STEMS = ("düş", "dus", "indir", "fiyat", "fark", "dibi", "zirve",
                      "tasarruf", "ucuz", "en düşük", "en dusuk")


def _search_is_deal_word(word: str) -> bool:
    normalized = normalize(word)
    return any(normalized.startswith(normalize(stem)) for stem in _SEARCH_DEAL_STEMS)


def _search_candidate_score(candidate: str) -> int:
    """Bir aday satırın ürün adı olma olasılığını yerel olarak puanlar.

    Ağ isteği ya da ek ayrıştırma yoktur; yalnızca satır metni incelenir.
    """
    words = [word for word in WORD_TOKEN_RE.findall(candidate)]
    score = 0
    if len(words) >= 2:
        score += 1
    else:
        # Tek kelimelik satırlar çoğunlukla mağaza/kategori etiketidir
        # ("🛍️ Amazon", "📂 Süpermarket"); ürün adı ise genelde çok kelimelidir.
        score -= 1
    if ":" in candidate:
        # "Amazon: 60 günün en düşük" gibi etiketli/istatistik satırları.
        score -= 2
    if words and all(_search_is_deal_word(word) for word in words):
        # Yalnızca fiyat/indirim kelimelerinden oluşan başlık ("DÜŞÜŞÜ").
        score -= 3
    return score


def linked_product_title(obj: Any, limit: int = 160) -> str:
    """Recognize a product-name hyperlink, not CTA or search links.

    Entity offsets are UTF-16. Only a single unambiguous, model-bearing label
    spanning at least four words is trusted; otherwise keep normal extraction.
    """
    text = message_text(obj)
    candidates = []
    for entity in message_entities(_as_message(obj)):
        url = getattr(entity, "url", None)
        if not url or not URL_RE.match(str(url)) or _price_search_service(str(url), ""):
            continue
        label = utf16_slice(text, getattr(entity, "offset", 0), getattr(entity, "length", 0)).strip()
        if (len(label.split()) < 4 or not price_filter._MODEL_RE.search(label)
                or price_filter.PRICE_RE.search(label) or "\n" in label
                or price_filter.is_generic_title(label)):
            continue
        title = price_filter.extract_title(label, limit=limit)
        if title:
            candidates.append(title)
    return candidates[0] if len(candidates) == 1 else ""


def _coupon_codes(text: str) -> list[str]:
    """Return explicit uppercase promo codes, excluding ordinary words near "kod"."""
    code_pattern = r"[A-Z0-9İĞÜŞÖÇ]{4,}"
    patterns = (
        rf"\b({code_pattern})\s+(?i:kod(?:u|uyla|unu)?|kuponu)\b",
        rf"(?i:\b(?:kod(?:u|uyla|unu)?|kupon(?:u)?)\s*[:：]?\s*)({code_pattern})\b",
        rf"\b({code_pattern})\s*\(\s*kopyalamak\s+için\s+dokun\s*\)",
    )
    codes = [match.group(1) for pattern in patterns for match in re.finditer(pattern, text or "")]
    generic_labels = {"INDIRIM", "İNDİRİM", "KUPO", "KUPON", "COUPON", "KOD", "KODU"}
    codes = [code for code in codes if code.upper() not in generic_labels]
    return list(dict.fromkeys(codes))


def _coupon_campaign_title(
    text: str, limit: int, *, include_code: bool = False,
    allow_code_only_marker: bool = False,
) -> str:
    """Return a complete coupon/campaign heading, not a clipped teaser.

    Coupon-only announcements often contain only eligibility and discount
    amounts, not a product price. Product/model-bearing messages remain on the
    normal title path so coupon metadata cannot replace a real product name.
    """
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    campaign_marker = r"\b(?:kupon\w*|indirim\s*kodu|kampanya\s*kodu)\b"
    if allow_code_only_marker:
        campaign_marker = r"\b(?:kupon\w*|kod\w*|indirim\s*kodu|kampanya\s*kodu)\b"
    if not re.search(campaign_marker, _campaign_norm(text)):
        return ""
    product_line = False
    for line in lines:
        title_markers = [match.start() for pattern in (
            price_filter._UNIT_RE, price_filter._MODEL_RE,
        ) if (match := pattern.search(line))]
        title_markers.extend(match.start() for match in re.finditer(r"\d{7,}|\d+\.\d+-\d+", line))
        marker = min(title_markers, default=len(line))
        title_prefix = line[:marker]
        if (
            len(WORD_TOKEN_RE.findall(_search_line_candidate(line))) >= 5
            and (
                (not re.search(r"kopyalamak|alışveriş|alisveris|üzeri|indirim|kupon|kod", normalize(line))
                 and bool(title_markers))
                or (bool(price_filter._UNIT_RE.search(line))
                    and len(WORD_TOKEN_RE.findall(_search_line_candidate(title_prefix))) >= 3
                    and not re.search(r"indirim|kupon|kod", normalize(title_prefix)))
            )
        ):
            product_line = True
            break
    if product_line:
        return ""

    candidates: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        # Preserve currency/percent/coupon words: generic product-title cleanup
        # correctly removes these from product titles but must not clip offers.
        candidate = clean_disclosure_tokens(line)
        candidate = re.sub(r"[^\w\s%₺.,:'’/-]+", " ", candidate, flags=re.UNICODE)
        candidate = re.sub(r"\s+", " ", candidate).strip(" \t,;:.|/-")
        plain = normalize(candidate)
        has_amount = bool(re.search(r"(?:\d[\d.,]*\s*(?:tl|₺|try)|%\s*\d)", normalize(line)))
        has_campaign_word = bool(re.search(r"\b(?:indirim|ındırım)\w*\b", _campaign_norm(candidate)))
        category_coupon_line = bool(
            re.search(r"\b(?:kupon|coupon)\w*\b", plain)
            and index > 0
            and re.search(r"linkteki|kategori", normalize(lines[index - 1]))
        )
        percent_coupon_line = bool(
            re.search(r"\bkupon\w*\b", plain)
            and re.search(r"%\s*\d", normalize(line))
            and re.search(r"\bek\b", plain)
        )
        if candidate and has_amount and (has_campaign_word or category_coupon_line or percent_coupon_line) and len(WORD_TOKEN_RE.findall(candidate)) >= 3:
            candidates.append((index, candidate))

    if not candidates:
        return ""

    # Prefer the most self-contained threshold/discount sentence.
    selected_index, selected = max(
        candidates,
        key=lambda item: (len(WORD_TOKEN_RE.findall(item[1])), len(item[1])),
    )
    # A category context immediately before a short coupon line is part of the
    # offer, not a product name (e.g. “Sofra Mutfak / 75 TL kupon”).
    if selected_index > 0 and re.search(r"linkteki|kategori", normalize(lines[selected_index - 1])):
        prefix = _search_line_candidate(lines[selected_index - 1])
        if prefix and normalize(prefix) not in normalize(selected):
            selected = f"{prefix} {selected}"

    category_line = next((line for line in lines if re.search(r"\bkategori\s*[:：]", line, re.I)), "")
    category_match = re.search(r"\bkategori\s*[:：]\s*(.+)$", category_line, re.I)
    category = _search_line_candidate(category_match.group(1)) if category_match else ""
    if category and normalize(category) not in normalize(selected):
        selected = f"{category} {selected}"
    if include_code:
        codes = _coupon_codes(text)
        missing_codes = [code for code in codes if normalize(code) not in normalize(selected)]
        if missing_codes:
            selected += " — " + ", ".join(missing_codes)
    if not re.search(r"\bamazon\b", normalize(selected)) and any(
        re.search(r"\bamazon\b", normalize(line))
        and re.search(r"\b(?:indirim|kupon|kod)\b", normalize(line))
        for line in lines
    ):
        selected = "Amazon " + selected
    return selected[:limit].strip()


def _coupon_preview_message(text: str, campaign_title: str) -> bool:
    """True when the source starts with a code card or a genuinely clipped teaser."""
    first = next((line.strip() for line in (text or "").splitlines() if line.strip()), "")
    if re.fullmatch(r"[A-Z0-9İĞÜŞÖÇ]{4,}\s*\(\s*kopyalamak\s+için\s+dokun\s*\)\s*❓?", first):
        return True
    # Kupon kodu ÇIPLAK bir satırda durabilir ("FIRSAT1000"), kampanya cümlesi
    # ise ilk satırdadır. Kart biçimi (kopyalamak için dokun) olmasa da tek
    # başına duran kod satırı, iletinin kupon duyurusu olduğunu gösterir;
    # eskiden yalnız ilk satır incelendiği için bu düzen kaçıyor ve başlığa
    # gövdeden rastgele bir satır ("hesabınızda aktif etmek …") düşüyordu.
    first_title_tokens = {normalize(token) for token in WORD_TOKEN_RE.findall(first)}
    campaign_tokens_all = {normalize(token) for token in WORD_TOKEN_RE.findall(campaign_title)}
    if campaign_title and first_title_tokens and first_title_tokens <= campaign_tokens_all:
        for line in (text or "").splitlines():
            token = line.strip()
            if not re.fullmatch(r"[A-Z0-9İĞÜŞÖÇ]{4,}", token):
                continue
            if normalize(token) in {normalize(code) for code in _coupon_codes(text)}:
                return True
    if "❓" not in first or not campaign_title:
        return False
    first_tokens = {normalize(token) for token in WORD_TOKEN_RE.findall(first)}
    campaign_tokens = {normalize(token) for token in WORD_TOKEN_RE.findall(campaign_title)}
    shared = first_tokens & campaign_tokens
    clipped_to_fragment = len(first_tokens) <= 2 and len(campaign_tokens) >= 6
    return bool((len(shared) >= 4 and len(campaign_tokens) >= len(shared) + 2)
                or clipped_to_fragment)


# Noktasız büyük ``I`` Türkçe küçültmede ``ı`` olur (``Indirim`` → ``ındirim``)
# ve ``indirim`` deseni kaçar. ``normalize`` davranışı BİLEREK değiştirilmiyor
# (arama sorgusu, eşleştirme, dedup ondan besleniyor); yalnız kampanya
# sözcüklerini tanıyan bu yardımcıda katlama yapılıyor.
_DOTLESS_I_FOLD = str.maketrans({"ı": "i"})


def _campaign_norm(text: str) -> str:
    """``normalize`` + noktasız ``ı`` katlaması (yalnız kampanya sözcükleri)."""
    return normalize(text).translate(_DOTLESS_I_FOLD)


# Kupon/kod olmadan da yayınlanan kampanya kalıpları: "2 Öde 1'i Money
# Hediye", "Aynı üründen 2 alana biri tutarında Money iade". Kullanıcı
# bildirimi: bu iletiler kupon duyurusudur, ürün fiyatı beklenmez.
# Not: "2 alana 1 TL" gibi kalıplar BİLEREK dışarıda — mevcut davranış o
# iletilerde başlığı `❓` ile bırakıyor ve o davranış korunuyor.
_NON_COUPON_CAMPAIGN_RE = re.compile(
    r"\b\d+\s*öde\s*\d+['’]?[iı]?\b|\bmoney\b|"
    r"\b(?:biri|bir)\s*tutar[ıi]nda\b",
    re.UNICODE,
)


def _has_safe_price(text: str) -> bool:
    """Metinde üst satıra yazılabilecek GÜVENLİ bir ürün fiyatı var mı?

    Bu ölçüm kupon sınıflandırmasından **bağımsızdır**: doğrudan
    ``price_filter.extract_price`` sonucuna bakar. Amaç, "fiyat var mı" kararının
    iki ayrı yerde (dal sırası ve kupon tespiti) yeniden yorumlanmasını önlemek.
    ``_is_coupon_announcement`` bu yardımcıyı kullanır; böylece fiyatlı gönderi,
    dalların sırası değişse bile kupon sayılmaz.
    """
    info = price_filter.extract_price(text)
    return info.value is not None and info.confidence in {"high", "medium"}


def _is_coupon_announcement(text: str) -> bool:
    """İleti bir kupon/kampanya duyurusu mu (ürün fiyatı beklenmez)?

    Üç ölçüt: (a) güvenli ürün fiyatı YOK, (b) kupon/kod sözcüğü geçmeyen
    kampanya kalıbı (``2 Öde 1'i Money Hediye``) VAR ya da tam bir kampanya
    cümlesi kurulabiliyor (eşik + indirim tutarı).

    Fiyat koşulu burada, fonksiyonun İÇİNDE aranır — çağıranın dal sırasına
    güvenilmez. Kullanıcı şartı: "fiyat olan bir gönderiyi kupon sanıp çöpe
    atmamalıyız". Üst satırdaki ``❓``, fiyatı OLMAYAN duyurularda yanlış bilgi
    olduğu için kaldırılır; fiyatı olan gönderide ise fiyat her koşulda korunur.
    """
    if _has_safe_price(text):
        return False
    # Kampanya sözcüğü ("kupon", "indirim kodu") hiç geçmeyen duyurular da var:
    # "Migros'ta … 2 Öde 1'i Money Hediye". Bunlar da ürün fiyatı taşımaz;
    # aksi hâlde başlıkta haksız bir ``❓`` kalıyordu.
    if _NON_COUPON_CAMPAIGN_RE.search(_campaign_norm(text)):
        return True
    campaign = _coupon_campaign_title(
        text, MESSAGE_LIMIT, include_code=True, allow_code_only_marker=True,
    )
    if not campaign:
        return False
    # Kampanya cümlesi VARSA ve güvenli ürün fiyatı YOKSA ileti duyurudur.
    # "Fiyat yok" koşulu yukarıda bir kez, bu fonksiyonun içinde ölçülür; çağıran
    # dalın önce çalışmasına bağlı DEĞİLDİR (eskiden öyleydi ve sessiz bir
    # kırılganlıktı: dal sırası değişirse fiyat kaybolabilirdi).
    return True


def _is_coupon_teaser_title(title: str) -> bool:
    """Recognize non-product previews such as promo-code cards/category coupons."""
    plain = normalize(title)
    first = next((line.strip() for line in (title or "").splitlines() if line.strip()), "")
    code_preview = bool(re.fullmatch(
        r"[A-Z0-9İĞÜŞÖÇ]{4,}\s*\(\s*kopyalamak\s+için\s+dokun\s*\)\s*❓?", first,
    ))
    category_coupon = bool(re.search(r"\blinkteki\b.*\b(?:ürünlerinde|urunlerinde)\b", plain))
    return code_preview or category_coupon


def _campaign_title_if_clipped(
    text: str, title: str, limit: int, *, include_code: bool = False,
    allow_code_only_marker: bool = False,
) -> str:
    """Use a complete coupon sentence only when the chosen title is its prefix fragment."""
    campaign = _coupon_campaign_title(
        text, limit, include_code=include_code,
        allow_code_only_marker=allow_code_only_marker,
    )
    if not campaign:
        return ""
    # A bare product price followed by generic coupon instructions is not a
    # complete campaign title. Require an eligibility threshold or both the
    # basket minimum and discount amounts before replacing a teaser.
    amounts = re.findall(r"\d[\d.,]*\s*(?:tl|₺|try)(?![a-z])", normalize(campaign))
    has_threshold = bool(re.search(
        r"\b(?:ve\s+)?(?:uzeri|üstü|alisveris|harcama|kategorisinde|kategorilerinde)\b",
        normalize(campaign),
    ))
    if not has_threshold and len(amounts) < 2:
        return ""
    title_tokens = {normalize(token) for token in WORD_TOKEN_RE.findall(title or "")}
    campaign_tokens = {normalize(token) for token in WORD_TOKEN_RE.findall(campaign)}
    # Don't replace a distinct product name. A clipped preview title is a strict
    # subset of the complete sentence and is materially shorter.
    if title_tokens and title_tokens <= campaign_tokens and len(campaign_tokens) >= len(title_tokens) + 2:
        return campaign
    if include_code and title_tokens:
        base_campaign = _coupon_campaign_title(
            text, limit, allow_code_only_marker=allow_code_only_marker,
        )
        base_tokens = {normalize(token) for token in WORD_TOKEN_RE.findall(base_campaign)}
        if title_tokens == base_tokens and campaign_tokens > base_tokens:
            return campaign
    return ""


def _coupon_dedup_identity(text: str) -> str:
    """Canonical identity for coupon-only campaigns; stable across copy variants."""
    campaign = _coupon_campaign_title(
        text, MESSAGE_LIMIT, include_code=True, allow_code_only_marker=True,
    )
    codes = _coupon_codes(text)
    if not campaign or not codes:
        return ""
    merchant = next((store for store in sorted(price_filter.STORES, key=len, reverse=True)
                     if re.search(rf"\b{re.escape(normalize(store))}\b", normalize(text))), "")
    category_line = next((line for line in (text or "").splitlines()
                          if re.search(r"\bkategori\s*[:：]", line, re.I)), "")
    category_match = re.search(r"\bkategori\s*[:：]\s*(.+)$", category_line, re.I)
    category = _search_line_candidate(category_match.group(1)) if category_match else ""
    amounts = []
    for shown in re.findall(r"\d[\d.,]*\s*(?:tl|try|₺)", normalize(campaign)):
        value = price_filter.parse_threshold(shown)
        if value is not None:
            amounts.append(f"{value:.2f}")
    return "Kampanya " + " ".join(
        part for part in (merchant, category, *codes, *amounts)
        if part and normalize(part) not in {"indirim", "kupon", "coupon", "kod", "kodu"}
    )


def _search_query(obj: Any, limit: int = SEARCH_QUERY_LIMIT) -> str:
    """Farklı kaynak şablonlarından ürün adını bulup arama sorgusu kur.

    Fiyat/indirim satırları atlanır. Adaylar arasından en yüksek puanlı olan
    seçilir (eşitlikte en üstteki kazanır; tüm adaylar zayıfsa ilk aday
    korunur, yani eski davranış bozulmaz). Metin değiştirilmez, servise istek
    atılmaz ve bildirim yoluna ek gecikme getirmez.
    """
    linked = linked_product_title(obj, limit=limit)
    if linked:
        return linked
    catalog = price_filter.priced_catalog_title(message_text(obj))
    if catalog:
        return price_filter.extract_title(catalog, limit=limit)
    raw_text = message_text(obj)
    campaign_title = _coupon_campaign_title(raw_text, limit)
    if campaign_title:
        return campaign_title
    text = URL_RE.sub(" ", raw_text)
    best_candidate = ""
    best_score = None
    for raw_line in text.splitlines():
        candidate = _search_line_candidate(raw_line)
        if not candidate or price_filter.is_generic_title(candidate):
            continue
        score = _search_candidate_score(candidate)
        # ``✅`` ile işaretlenen, sayısal metadata taşımayan açıklayıcı ürün
        # satırı kupon talimatına karşı eşitlikte kalmasın. Başlık ayıklayıcıdaki
        # dar kuralın arama sorgusundaki karşılığıdır.
        if raw_line.lstrip().startswith("✅") \
                and len(WORD_TOKEN_RE.findall(candidate)) >= 4 \
                and not any(char.isdigit() for char in candidate):
            score += 2
        if best_score is None or score > best_score:
            best_candidate, best_score = candidate, score
    best_candidate = best_candidate[:limit].strip()
    complete_campaign = _campaign_title_if_clipped(
        raw_text, best_candidate, limit, allow_code_only_marker=True,
    )
    return complete_campaign or best_candidate
