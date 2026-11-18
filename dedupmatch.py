"""Tekrar birleştirme eşleştirme: ürün başlığı karşılaştırma ve fırsat anahtarı (saf)."""

from __future__ import annotations

import asyncio
import math
import price_filter
import re
import time
import urllib.request
from botentities import entities_for_text
from collections.abc import Iterable
from dedupbadge import _fold_title, dedup_key
from header import split_header_line
from sanitize import clean_url, extract_links, message_text
from telethon import utils
from telethon.tl import types
from typing import Any


def bot_history_ids_shared(chat_id: int) -> bool:
    """Only channels/supergroups have a shared MTProto/Bot API ID namespace."""
    return utils.resolve_id(chat_id)[1] is types.PeerChannel


def next_poll_interval(current: float, flood_wait: float) -> float:
    # Do not immediately return to the rate that Telegram just rejected.
    return max(current, min(300.0, max(current * 2, flood_wait, 10.0)))


def recover_poll_interval(current: float, base: float) -> float:
    """Başarılı (ve hızlı) bir yoklamadan sonra aralığı kademeli gevşet.

    Her başarılı yoklama aralığı yarıya indirir (taban ``base``). Yoklamanın
    kendisi geçerli aralıkla beklediği için istek hızı bir anda artmaz;
    300 sn'den 3 sn'ye dönüş bile art arda başarılı yoklamalar gerektirir.
    """
    if current <= base:
        return current
    return max(base, current / 2)


def offer_terms(obj: Any, *, prefer_header: bool = False) -> tuple[float | None, str]:
    """Güvenilir ürün fiyatını döndür; koşul, mağaza ve bağlantılar kimlik değildir.

    ``prefer_header`` yalnız kendi composed bildirimlerimizi geçmişten yüklerken
    kullanılır. Canlı kaynakta eski bir başlık ile daha düşük gövde fiyatı birlikte
    bulunabilir; bu nedenle varsayılan davranış gövdeyi ayrıştırmaya devam eder.
    """
    text = message_text(obj)
    if prefer_header:
        first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
        header_price, header_title = split_header_line(first_line)
        if header_price and header_title and not price_filter.is_generic_title(header_title):
            # Composed bildirimin fiyatı zaten canlı yolda doğrulanmıştır. Restart
            # sonrası gövdedeki eski/kupon/kampanya tutarlarıyla yeniden belirsizlik
            # üretmek, aynı teklifi ``None -> fiyat`` gibi gösterip ikinci bildirim
            # attırıyordu. Başlık yoluyla aynı sözleşmeyi fiyat için de kullan.
            header_info = price_filter.extract_price(header_price)
            if header_info.value is not None and header_info.confidence in {"high", "medium"}:
                return header_info.value, ""
    strike = price_filter.strike_spans_from_entities(text, entities_for_text(obj, text))
    info = price_filter.extract_price(text, strike)
    value = info.value if info.confidence in {"high", "medium"} else None
    return value, ""


_UPGRADE_CONDITION_RE = re.compile(
    r"\b(?:ikinci\s+el|2\s*\.\s*el|yenilenmiş|yenilenmis|refurbished|renewed|"
    r"açık\s+kutu|acik\s+kutu|depo\s+ürünü|depo\s+urunu)\b", re.IGNORECASE,
)
_ASIN_RE = re.compile(r"/(?:dp|gp/product)/([a-z0-9]{10})(?:[/?#]|$)", re.IGNORECASE)


def product_asins(obj: Any) -> set[str]:
    return {match.group(1).upper() for link in extract_links(obj)
            if (match := _ASIN_RE.search(link["url"]))}


def product_link_ids(obj: Any) -> set[str]:
    """Stable explicit product references used only as a conservative dedup bridge."""
    identities: set[str] = set()
    for link in extract_links(obj):
        try:
            parsed = urllib.parse.urlsplit(clean_url(link["url"]))
        except ValueError:
            continue
        host = (parsed.hostname or "").lower().rstrip(".")
        path = parsed.path.rstrip("/")
        # Hepsiburada's short links are stable product references; the t.me
        # source permalink and generated search buttons are not product IDs.
        if host == "app.hb.biz" and path:
            identities.add(f"hb:{path.lower()}")
    return identities


def plain_offer_for_upgrade(obj: Any, offer: tuple | None) -> bool:
    """Only a clearly standalone normal offer may be replaced automatically."""
    if not offer or offer[0] is None:
        return False
    text = message_text(obj)
    if _UPGRADE_CONDITION_RE.search(text):
        return False
    strike = price_filter.strike_spans_from_entities(text, entities_for_text(obj, text))
    candidates = price_filter.find_candidates(text, strike)
    prices = [c for c in candidates if c.cls == "price" and c.role in {"normal", "pack_total"}]
    return (len({c.value for c in prices}) == 1 and bool(prices)
            and _dedup_whole_tl(prices[0].value) == _dedup_whole_tl(offer[0])
            and not any(c.role in {"multi_unit", "condition_price", "member_price", "discounted", "basket_coupon", "product_coupon"}
                        for c in candidates))


def _simple_linked_offer_price(obj: Any, offer: tuple | None) -> bool:
    """Accept only one clear, unconditional final price for a linked product."""
    if not offer or offer[0] is None:
        return False
    text = message_text(obj)
    if _UPGRADE_CONDITION_RE.search(text):
        return False
    strike = price_filter.strike_spans_from_entities(text, entities_for_text(obj, text))
    info = price_filter.extract_price(text, strike)
    if info.value is None or info.confidence not in {"high", "medium"} or info.value != offer[0]:
        return False
    candidates = price_filter.find_candidates(text, strike)
    blocked_roles = {"multi_unit", "unit", "condition_price", "member_price", "basket_coupon", "product_coupon"}
    if any(candidate.role in blocked_roles for candidate in candidates):
        return False
    values = {candidate.value for candidate in candidates if candidate.cls == "price"}
    return values == {offer[0]}


def is_referenced_product_price_drop(
    entry: dict[str, Any], title: str, obj: Any,
    offer: tuple | None, now: float,
) -> bool:
    """Allow a safe replacement only for the same explicit item link and a lower price.

    Generic matching still announces genuine price drops separately. This narrow
    bridge is reserved for repeated listings whose product URL/ASIN and title
    both agree, so a cheaper copy can replace the old notice and carry the share
    badge without merging similarly named variants.
    """
    old = entry.get("offer")
    old_links = set(entry.get("product_link_ids") or ())
    old_links.update(f"asin:{asin}" for asin in entry.get("product_asins") or ())
    new_links = product_link_ids(obj)
    new_links.update(f"asin:{asin}" for asin in product_asins(obj))
    if (entry.get("failed") or entry.get("pending")
            or not _multibuy_editable_valid(entry)
            or not old_links or old_links != new_links
            or not titles_match_product(entry.get("title"), title)
            or not old or old[0] is None or not offer or offer[0] is None
            or not better_or_different_offer(old, offer)):
        return False
    first_seen = float(entry.get("first_seen", 0) or 0)
    if now < first_seen or now - first_seen > 180:
        return False
    if not _simple_linked_offer_price(entry.get("text") or "", old):
        return False
    return _simple_linked_offer_price(obj, offer)


def _multibuy_entry_eligible(entry: dict[str, Any], title: str, offer: tuple | None, now: float) -> bool:
    """Entry'nin 3 dakikalık bridge için uygun olup olmadığını kontrol et."""
    old = entry.get("offer")
    if (entry.get("preload") or not entry.get("plain_offer") or entry.get("failed")
            or entry.get("pending") or now - float(entry.get("first_seen", 0)) > 180
            or now < float(entry.get("first_seen", 0))
            or dedup_key(entry.get("title")) != dedup_key(title)
            or not old or old[0] is None or not offer or offer[0] is None
            or not better_or_different_offer(old, offer)):
        return False
    return True


def _multibuy_editable_valid(entry: dict[str, Any]) -> bool:
    """Kaydın düzenlenebilir (bot/account) mesaj ID'si geçerli mi?"""
    if entry.get("editable") == "bot":
        return isinstance(entry.get("bot_message_id"), int)
    if entry.get("editable") == "account":
        return isinstance(entry.get("account_message_id"), int)
    return False


def _multibuy_blocked_by_condition_or_asin(entry: dict[str, Any], obj: Any) -> bool:
    """Upgrade bloklayıcı koşul (ikinci el vb.) veya ASIN uyuşmazlığı var mı?"""
    text = message_text(obj)
    if _UPGRADE_CONDITION_RE.search(text):
        return True
    old_asins, new_asins = entry.get("product_asins") or set(), product_asins(obj)
    if old_asins and new_asins and old_asins != new_asins:
        return True
    return False


def _multibuy_price_structure_valid(entry: dict[str, Any], obj: Any, offer: tuple | None) -> bool:
    """Mesajda eski taban fiyat + daha ucuz multi-buy yapısı doğru mu?"""
    old = entry.get("offer")
    text = message_text(obj)
    strike = price_filter.strike_spans_from_entities(text, entities_for_text(obj, text))
    candidates = price_filter.find_candidates(text, strike)
    base = [c for c in candidates if c.cls == "price" and c.role in {"normal", "pack_total"}
            and c.strong and _dedup_whole_tl(c.value) == _dedup_whole_tl(old[0])]
    cheaper = [c for c in candidates if c.role == "multi_unit" and c.value == offer[0]]
    normal_values = {c.value for c in candidates if c.cls == "price" and c.role in {"normal", "pack_total"}}
    multi_values = {c.value for c in candidates if c.role == "multi_unit"}
    return (len(base) == 1 and len(cheaper) == 1 and base[0].line == cheaper[0].line
            and len(normal_values) == 1 and len(multi_values) == 1
            and not any(c.role in {"condition_price", "member_price", "discounted", "basket_coupon", "product_coupon"}
                        for c in candidates))


def is_referenced_multibuy_upgrade(entry: dict[str, Any], title: str, obj: Any,
                                    offer: tuple | None, now: float) -> bool:
    """Conservative 3-minute bridge: same exact title, old base + lower multi-buy."""
    if not _multibuy_entry_eligible(entry, title, offer, now):
        return False
    if not _multibuy_editable_valid(entry):
        return False
    if _multibuy_blocked_by_condition_or_asin(entry, obj):
        return False
    return _multibuy_price_structure_valid(entry, obj, offer)

def dedup_offer_key(title: str, offer: tuple[float | None, str] | None = None) -> str | None:  # noqa: ARG001 - dış API uyumu
    """Tekrar anahtarı ürün adıdır; fiyat yalnızca aynı ürünün fiyat değişimini denetler.

    `offer` geriye dönük uyumluluk için korunur; anahtar yalnızca başlığa
    bağlıdır. Çağıranlar fiyatı ayrıca `better_or_different_offer` ile
    karşılaştırır.
    """
    return dedup_key(title)


_TITLE_STOPWORDS = frozenset({"ve", "ile", "icin", "olan"})

_TITLE_BENIGN_EXTRA_WORDS = frozenset({
    "orijinal", "yeni", "profesyonel", "otomatik", "dijital", "elektrikli",
    "sarjli", "tasinabilir", "cok", "amacli", "fonksiyonlu",
})

_TITLE_COLOR_WORDS = frozenset({
    "siyah", "beyaz", "gri", "antrasit", "kirmizi", "mavi", "lacivert",
    "yesil", "sari", "turuncu", "mor", "lila", "pembe", "kahverengi", "bej",
    "krem", "altin", "gumus", "bronz", "bakir", "bordo", "turkuaz", "haki",
    "vizon", "ekru", "pudra", "mercan", "fuşya", "fusya", "hardal", "kiremit",
    "indigo", "uzay", "gece", "yildiz", "titanyum", "grafit",
    "black", "white", "grey", "gray", "red", "blue", "green", "yellow",
    "pink", "purple", "silver", "gold", "rose", "midnight", "starlight",
    "graphite", "titanium", "navy", "beige", "brown", "orange",
})

_TITLE_VARIANT_WORDS = frozenset({
    "pro", "max", "plus", "ultra", "mini", "lite", "slim", "fe", "se", "ti",
    "super", "oled", "qled", "wifi", "lte", "cellular", "kablolu", "kablosuz",
    "wireless", "bluetooth", "xs", "s", "m", "l", "xl", "xxl", "xxxl",
    "erkek", "kadin", "cocuk", "bebek", "unisex",
    "kilif", "koruyucu", "kordon", "kayis", "adaptor", "sarj", "kablo",
    "yedek", "filtre", "baslik", "kartus", "torba", "standi", "tutucu",
    "jakarli", "pamuklu", "keten", "kadife", "saten", "mat", "parlak",
    "simli", "desenli", "cizgili",
})

_TITLE_UNIT_MAP = {
    "ml": "ml", "cl": "cl",
    "l": "l", "lt": "l", "litre": "l", "liter": "l",
    "kg": "kg", "kilogram": "kg",
    "g": "g", "gr": "g", "gram": "g", "mg": "mg",
    "gb": "gb", "tb": "tb", "mb": "mb",
    "mah": "mah", "wh": "wh",
    "kw": "kw", "kilowatt": "kw", "w": "w", "watt": "w",
    "v": "v", "volt": "v",
    "hz": "hz", "ghz": "ghz", "mhz": "mhz",
    "mp": "mp", "bar": "bar", "rpm": "rpm",
    "cm": "cm", "santim": "cm", "santimetre": "cm",
    "mm": "mm", "milimetre": "mm",
    "m": "m", "metre": "m",
    "inc": "inc", "inch": "inc",
    "adet": "adet", "ad": "adet", "tane": "adet",
    "li": "li", "lu": "li", "parca": "parca",
}

_TITLE_DIM_RE = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)(?:\s*[x×]\s*(\d+(?:\.\d+)?))?"
    r"(?:\s*(cm|mm|m))?\b",
)
_TITLE_INCH_RE = re.compile(r'(\d+(?:\.\d+)?)\s*(?:"|\'\'|”|″|\binc\b|\binch\b)')
_TITLE_NUM_UNIT_RE = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*("
    + "|".join(sorted(_TITLE_UNIT_MAP, key=len, reverse=True))
    + r")\b",
)
_TITLE_HYPHEN_MODEL_RE = re.compile(r"\b(\d+)-([a-z])\b")
_TITLE_WORD_RE = re.compile(r"[a-z0-9]+(?:\.\d+[a-z0-9]*)?|\+")
_MEASUREMENT_TOKEN_RE = re.compile(
    r"^\d+(?:\.\d+)?(?:x\d+(?:\.\d+)?)*(?:ml|cl|l|kg|g|mg|gb|tb|mb|mah|wh|"
    r"kw|w|v|hz|ghz|mhz|mp|bar|rpm|cm|mm|m|inc|adet|li|parca|k|d)$",
)


def _canonicalize_title_units(text: str) -> str:
    text = re.sub(r"(\d),(\d)", r"\1.\2", text)

    def _dim_repl(m: re.Match[str]) -> str:
        parts = [m.group(1), m.group(2)]
        if m.group(3):
            parts.append(m.group(3))
        return "x".join(parts) + (m.group(4) or "")

    text = _TITLE_DIM_RE.sub(_dim_repl, text)
    text = _TITLE_INCH_RE.sub(r"\1inc", text)
    text = _TITLE_NUM_UNIT_RE.sub(
        lambda m: f"{m.group(1)}{_TITLE_UNIT_MAP[m.group(2)]}", text,
    )
    text = _TITLE_HYPHEN_MODEL_RE.sub(r"\1\2", text)
    return text


def _stem_title_word(word: str) -> str:
    if (
        word == "+"
        or any(ch.isdigit() for ch in word)
        or word in _TITLE_COLOR_WORDS
        or word in _TITLE_VARIANT_WORDS
    ):
        return word
    if word == "makinasi":
        word = "makinesi"
    if len(word) >= 6 and word.isalpha():
        if word.endswith(("si", "su")) and len(word) - 2 >= 4:
            return word[:-2]
        if word.endswith(("i", "u")) and len(word) - 1 >= 4:
            return word[:-1]
    return word


def _title_profile(title: str | None) -> dict[str, Any] | None:
    """Başlıktan anlamlı kelimeler, ilk-3-kelime sinyali ve ayırt edici nitelikleri çıkar."""
    if not title or price_filter.is_generic_title(title):
        return None
    canonical = _canonicalize_title_units(_fold_title(title))
    raw_tokens = _TITLE_WORD_RE.findall(canonical)
    words = [t for t in raw_tokens if t not in _TITLE_STOPWORDS]
    if not words:
        return None
    specs = tuple(sorted(w for w in words if w == "+" or any(ch.isdigit() for ch in w)))
    has_model_code = any(
        len(w) >= 4
        and any(ch.isalpha() for ch in w)
        and any(ch.isdigit() for ch in w)
        and not _MEASUREMENT_TOKEN_RE.fullmatch(w)
        for w in words
    )
    colors = frozenset(w for w in words if w in _TITLE_COLOR_WORDS)
    variants = frozenset(w for w in words if w in _TITLE_VARIANT_WORDS)
    stems = [_stem_title_word(w) for w in words]
    return {
        "words": words,
        "stems": stems,
        "stem_set": frozenset(stems),
        "specs": specs,
        "has_model_code": has_model_code,
        "colors": colors,
        "variants": variants,
    }


def _title_has_strong_identity(title: str | None) -> bool:
    prof = _title_profile(title)
    return bool(prof and len(prof["stems"]) >= 3)


def _title_model_codes(profile: dict[str, Any]) -> set[str]:
    """Model kodları ölçü/kapasite token'larından ayır."""
    return {
        word for word in profile["words"]
        if len(word) >= 4 and any(ch.isalpha() for ch in word) and any(ch.isdigit() for ch in word)
        and not _MEASUREMENT_TOKEN_RE.fullmatch(word)
    }


def _title_capacities(profile: dict[str, Any]) -> set[str]:
    """Açık kapasite farkları (128 GB/256 GB, 150 ml/250 ml) ürün varyantıdır."""
    return {
        word for word in profile["words"]
        if re.fullmatch(r"\d+(?:\.\d+)?(?:gb|tb|ml|cl|mah|wh|kg|g|adet)", word)
    }


def _ordered_title_prefix_match(pa: dict[str, Any], pb: dict[str, Any]) -> bool:
    """Uzun katalog adının, kırılmış kısa adın güvenli devamı olup olmadığını sına.

    Sıralı en az 10 kelimelik önek ve sayısal/model kimliği şarttır. Son kelime
    ``Kapan`` → ``Kapanma`` gibi kırılmış olabilir. Uzun taraftaki ek sayılar
    yalnız açık model koduna (``DST7030/20``) ait olabilir; yeni kapasite,
    ölçü, yıl veya başka sayısal varyantlar bu yolla birleştirilmez.
    """
    words_a, words_b = pa["words"], pb["words"]
    if len(words_a) == len(words_b):
        return False
    short, long = (words_a, words_b) if len(words_a) < len(words_b) else (words_b, words_a)
    short_profile = pa if len(words_a) < len(words_b) else pb
    if len(short) < 10 or not (short_profile["specs"] or short_profile["has_model_code"]):
        return False
    boundary = len(short) - 1
    if short[:boundary] != long[:boundary]:
        return False
    short_last, long_last = short[-1], long[boundary]
    if short_last != long_last and (
        len(short_last) < 4 or not short_last.isalpha()
        or not long_last.startswith(short_last)
    ):
        return False
    suffix = long[len(short):]
    if not suffix:
        return False
    model_codes = _title_model_codes({"words": suffix})
    for index, word in enumerate(suffix):
        if not any(char.isdigit() for char in word):
            continue
        if word in model_codes:
            continue
        if word.isdigit() and index > 0 and suffix[index - 1] in model_codes:
            continue  # ``DST7030/20`` kodunun ``20`` parçası
        return False
    return True


def titles_match_product(title_a: str | None, title_b: str | None) -> bool:
    """Başlıkları karşılaştır: aynı model kodu yeterli güçlü eşleşmedir.

    Mağaza, bağlantı, Prime/kargo/kupon koşulları ürün adını geçersiz kılamaz.
    Açık renk, kapasite ve ürün-varyant çelişkileri ise korunur.
    """
    key_a, key_b = dedup_key(title_a), dedup_key(title_b)
    if not key_a or not key_b:
        return False
    if key_a == key_b:
        return True
    pa, pb = _title_profile(title_a), _title_profile(title_b)
    if not pa or not pb or pa["words"][0] != pb["words"][0]:
        return False

    # A shared explicit model code bridges editorial title variations such as
    # inch/cm descriptions and optional feature lists.
    shared_codes = _title_model_codes(pa) & _title_model_codes(pb)
    if shared_codes:
        critical_variants = _TITLE_VARIANT_WORDS - {"ultra"}
        variants_a = pa["variants"] & critical_variants
        variants_b = pb["variants"] & critical_variants
        return (pa["colors"] == pb["colors"]
                and variants_a == variants_b
                and _title_capacities(pa) == _title_capacities(pb))

    codes_a, codes_b = _title_model_codes(pa), _title_model_codes(pb)
    if codes_a and codes_b and codes_a.isdisjoint(codes_b):
        return False

    # With no shared model code, allow only close same-brand title variants. Exact
    # model/spec, color and product-type conflicts still prevent a merge.
    if (pa["colors"] != pb["colors"] or pa["variants"] != pb["variants"]
            or (("+" in pa["words"]) != ("+" in pb["words"]))):
        return False
    capacities_a, capacities_b = _title_capacities(pa), _title_capacities(pb)
    if capacities_a and capacities_b and capacities_a != capacities_b:
        return False
    if _ordered_title_prefix_match(pa, pb):
        # Genel spec-set karşılaştırması, uzun addaki ``DST7030/20`` kodunu
        # ürün farkı sanıyordu. Yukarıdaki sıralı önek ve varyant kontrolleri
        # geçildiyse model koduna ait bu ek değerler artık çelişki değildir.
        return True
    specs_a = set(pa["specs"])
    specs_b = set(pb["specs"])
    if specs_a and specs_b and specs_a != specs_b:
        return False
    words_a, words_b = pa["stem_set"], pb["stem_set"]
    overlap = len(words_a & words_b)
    return overlap >= 4 and overlap / min(len(words_a), len(words_b)) >= 0.7


def _find_matching_dedup_key(
    cache: dict[str, dict[str, Any]], title: str | None,
    asins: Iterable[str] = (), link_ids: Iterable[str] = (),
) -> str | None:
    """Find an earlier item by strong title match or identical explicit product ID."""
    current_asins = set(asins or ())
    current_links = set(link_ids or ())
    can_match_title = _title_has_strong_identity(title)
    if not can_match_title and not (current_asins or current_links):
        return None
    best_key: str | None = None
    best_rank: tuple[int, float] = (-1, -1.0)
    for cand_key, cand_entry in cache.items():
        if cand_entry.get("failed"):
            continue
        same_asins = bool(current_asins) and current_asins == set(cand_entry.get("product_asins") or ())
        same_links = bool(current_links) and current_links == set(cand_entry.get("product_link_ids") or ())
        same_title = can_match_title and titles_match_product(cand_entry.get("title"), title)
        if not (same_asins or same_links or same_title):
            continue
        rank = (0 if cand_entry.get("pending") else 1, float(cand_entry.get("last_seen", 0.0)))
        if rank > best_rank:
            best_rank, best_key = rank, cand_key
    return best_key


def _keep_detailed_prefix_title(entry: dict[str, Any], candidate: str) -> None:
    """Güvenli uzun-adevamı eşleşmesinde model kodlu zengin adı kayda taşı.

    Kısa ad kayıtta bırakılırsa iki farklı uzun model, aynı kısa ad üzerinden
    zincirleme biçimde tek fırsata birleşebilir. Yalnız doğrulanmış sıralı
    önek eşleşmesinde ve aday gerçekten daha ayrıntılıysa başlığı zenginleştir;
    dedup anahtarını, sayacı veya gönderilmiş bildirimi değiştirme.
    """
    existing = entry.get("title")
    if not isinstance(existing, str) or not candidate or existing == candidate:
        return
    pa, pb = _title_profile(existing), _title_profile(candidate)
    if not pa or not pb or len(pb["words"]) <= len(pa["words"]):
        return
    if titles_match_product(existing, candidate) and _ordered_title_prefix_match(pa, pb):
        entry["title"] = candidate


def _dedup_whole_tl(value: Any) -> int | None:
    """Yalnızca dedup karşılaştırmasında kullanılan tam-TL değeri.

    Gerçek fiyatın kendisi değildir: hiçbir yere saklanmaz, formatlanmaz ve
    gösterime/DM eşiklerine girmez. ``None``, negatif ya da sonlu olmayan
    girdiler asla 0'a çevrilmez; bilinmeyen fiyat bilinmeyen kalır (``None``).
    Fiyatlar negatif olamadığı için ``int()`` kesmesi taban (floor) ile aynıdır.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    return int(number)


def better_or_different_offer(old: tuple | None, new: tuple | None) -> bool:
    if old is None or new is None:
        return False
    if new[0] is None:
        return False
    if old[0] is None:
        return True
    # Kuruş farkı tek başına yeni fırsat değildir: aynı tam TL içindeki
    # fiyatlar (örn. 1.299,90 ↔ 1.299) karşılaştırmada aynı kabul edilir.
    # Gerçek fiyatlar (old[0]/new[0]) aynen saklanır ve gösterilir; burada
    # yalnızca geçici tam-TL karşılaştırma değeri türetilir.
    old_whole = _dedup_whole_tl(old[0])
    new_whole = _dedup_whole_tl(new[0])
    if old_whole is not None and new_whole is not None and old_whole == new_whole:
        return False
    # Yalnız tam TL'ye kırpma/yuvarlama ölçeğindeki farkları tolere et.
    # Hem 1 TL hem %0,01 üst sınırı: küçük fiyatlı ürünün gerçek düşüşünü yutma.
    rounded = (float(old[0]).is_integer() or float(new[0]).is_integer())
    tolerance = min(1.0, min(old[0], new[0]) * 0.0001) if rounded else 0.01
    return new[0] < old[0] - max(0.01, tolerance)


def new_dedup_entry(title: str, token: Any, source_name: str | None = None,
                    footer_name: str | None = None,
                    preload: bool = False) -> dict[str, Any]:
    """Gönderim rezervasyonu konmuş yeni önbellek kaydı (``pending=True``).

    ``sources`` ilk kopyayı paylaşan grupları tutar; çoklu paylaşım notu
    ("📌 N kere paylaşıldı: <gruplar>") bu adlarla kurulur (bkz. ``dedup_badge``).
    ``links`` ise ``{grup adı: o grubun mesaj adresi}`` eşlemesidir: rozette
    yazan her ad kendi mesajına gömülür (kullanıcı isteği — şimdiye kadar yalnız
    ilk gelen kopyanın adresi kullanılıyordu). ``footer_name`` bildirimin en
    altındaki kaynak grup adı satırıdır; tekrar yakalandığında bu satır
    kaldırılıp yerine çoklu paylaşım notu yazılır (kullanıcı isteği: "hangi
    gruptan geldiğini kaldırıp N kere paylaşıldı şu şu gruplar diye yazsın").
    """
    now = time.time()
    name = (source_name or "").strip()
    return {
        "title": title,
        "preload": bool(preload),     # kayıt açılış taramasından mı geldi?
        "sources": [name] if name else [],
        # "links" ({grup adı: t.me linki}) yalnızca bir paylaşımın adresi
        # bilindiğinde kurulur (bkz. note_dedup_source); açılış taraması kendi
        # eşlemesini "link_map" altında tutar.
        "footer_name": (footer_name or "").strip(),
        "count": 1,
        "first_seen": now,
        "last_seen": now,
        "pending": True,     # ilk mesaj henüz gönderilmedi
        "failed": False,     # gönderim yarım kaldı / devralındı
        "ready": asyncio.Event(),
        "token": token,      # rezervasyon sahibi (devralmayı ayırt eder)
        "editable": None,    # "bot" | "account" | None
        "bot_message_id": None,
        "account_message_id": None,
        "kind": "text",      # "text" | "media" (bot düzenlemesinin yöntemini seçer)
        "text": "",          # ilk mesajın güncel tam metni (not işlenirken güncellenir)
        "entities_bot": None,  # Bot API entity sözlükleri (güncel metne göre)
        "entities_tl": None,   # Telethon entity'leri (güncel metne göre)
        "keyboard": None,
        "edit_lock": asyncio.Lock(),
        "last_edit": 0.0,
        "edit_fails": 0,     # üst üste rozet hatası (2'de düzenleme bırakılır)
    }
