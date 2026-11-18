"""Tekrar birleştirme rozeti: üretim, ayrıştırma ve entity kaydırma (saf)."""

from __future__ import annotations

import logging
import copy
import price_filter
import re
from collections.abc import Sequence
from constants import MESSAGE_LINK_LABEL, PRODUCT_LINK_TEXT
from keywords import exclusion_normalize
from sanitize import clean_url, utf16_length, utf16_slice
from searchbuttons import PRODUCT_LINK_LABEL, is_price_line
from telethon.tl import types
from typing import Any

log = logging.getLogger("telegram-filter")


# ---------------------------------------------------------------------------
# Tekrar birleştirme (aynı fırsat, tek mesaj)
# ---------------------------------------------------------------------------
#
# Aynı indirim 3-5 kanal tarafından dakikalar içinde, farklı fiyat/CTA
# yerleşimleriyle paylaşılabilir. Her kopyayı gruba atmak mesaj kalabalığı yapar;
# ürün adı sorgusu normalize edilerek ilk kopya bellekte kanonik kayıt olur.
#
#   * İlk kopya her zamanki gibi gönderilir ve ürün sorgusu ``DEDUP_CACHE``
#     sözlüğüne yazılır (mesaj başına EK API çağrısı YOKTUR;
#     her tekrarda geçmiş taramak hem bildirimi geciktirir hem FloodWait/429
#     riskini artırırdı).
#   * Sonraki aynı ürün sorgusuna sahip kopyalar gruba ATILMAZ; ilk mesajdaki
#     kaynak grup adı satırı KALDIRILIR ve yerine çoklu paylaşım notu işlenip
#     bildirim orada kapanır:
#     "📌 2 kere paylaşıldı: FırsatZ, İndirimde Al" (kullanıcı isteği:
#     "hangi gruptan geldiğini kaldırıp N kere paylaşıldı şu şu gruplar
#     diye yazsın"). Grup adı satırı yoksa not en sona eklenir.
#   * Not TEK SATIRDIR; grup adları virgülle yazılır. Sayı 5'i geçince vurgu
#     artar: "🚨 5 kere paylaşıldı: ... — KAÇIRMA!". Kalın + emoji,
#     Telegram'ın sunduğu en güçlü vurgu kombinasyonudur.
#   * Eşleşme penceresi ``dedup_window_hours`` ile sınırlıdır (varsayılan 12):
#     iki hafta sonra aynı ürün yine indirime girerse YENİ fırsat sayılır.
#   * Bot yeniden başlayınca bellek boşalır; açılışta hedeften SON
#     ``dedup_scan_limit`` mesaj (varsayılan 100, tek API çağrısı) okunup
#     önbellek yeniden kurulur. İşlem başına tarama yapılmaz.
#
# Yarış durumu: aynı anda gelen kopyalar için gönderen "rezervasyon" koyar;
# diğerleri onun bitmesini bekleyip tekrara düşer (bkz. main() içindeki
# dedup_before_send / dedup_after_send).

# Çoklu paylaşım notu (kullanıcı isteği): bildirimi KAPATAN son bloktur,
# "📌 2 kere paylaşıldı: FırsatZ, İndirimde Al" biçiminde yazılır; 5+ kaynakta
# baştaki emoji 🚨 olur ve sonuna "— KAÇIRMA!" eklenir.
# (Emoji grubu yakalanmaz; sayı her iki biçimde de 1. gruptur.)
DEDUP_BADGE_RE = re.compile(r"^(?:📌|🚨)\s*(\d+)\s+kere\s+paylaşıldı", re.IGNORECASE)
# Eski rozet biçimi: "✅ 2 kaynakta paylaşıldı · teyitli fırsat" / "🔥🔥 4 kaynakta ..."
# (üst satırda ya da fiyat satırının hemen altında dururdu). Yalnızca ESKİ
# gönderilmiş bildirimlerin rozeti sökülürken tanınır — geriye dönük uyumluluk.
DEDUP_BADGE_LEGACY_RE = re.compile(
    r"^(?:✅|🔥+|🚨)\s*(\d+)\s+kaynakta\s+paylaşıldı", re.IGNORECASE,
)
# Çok daha eski sürüm rozeti ikinci satırda kaynak adlarını listelerdi
# ("📌 Kaynaklar: ..."); açılış taramasında o satırı atlamak için korunur.
DEDUP_SOURCES_PREFIX = "📌"
# Rozette birden çok grup adı bu ayraçla yazılır. Virgül KULLANILMAZ: kaynak
# grup adları virgül içerebiliyor ve o durumda adlar bölünüp linkleri kayıyordu.
BADGE_SOURCE_SEPARATOR = " | "
# YENİ biçimin TEK GRUPLU rozete eklediği sabit süs. Yalnız bu ek, rozetin yeni
# biçimde yazıldığını ve ad içindeki virgülün AYRAÇ OLMADIĞINI kanıtlar.
# ``— KAÇIRMA!`` buraya GİREMEZ: aynı ek yeni biçimde de (5+ paylaşımda) çıkıyor,
# dolayısıyla eski/yeni ayrımında hiçbir şey kanıtlamaz. Nitekim onu "eski biçim"
# sanmak, ``🚨 5 kere paylaşıldı: A, B, C — KAÇIRMA!`` rozetindeki adları
# bölünmez hâle getiriyordu.
_BADGE_NEW_SINGLE_SUFFIX_RE = re.compile(r"·\s*tek\s+grup")
# Rozetin sonuna eklenen süsler. Yalnız AD'dan temizlenirler (ad sayılmazlar);
# eski/yeni biçim KARARINDA kullanılmazlar — ``— KAÇIRMA!`` yeni biçimde de var.
_BADGE_TRAILING_DECOR_RE = re.compile(r"\s*(?:—\s*KAÇIRMA!|·\s*tek\s+grup)\s*$")


DEDUP_TOKEN_RE = re.compile(r"[^\W\d_]+|\d+(?:[.,]\d+)*|\+", re.UNICODE)

_TITLE_STORE_PREFIX_RE = re.compile(
    r"^(?:amazon|hepsiburada|trendyol|n11|mediamarkt|teknosa|vatan|pazarama|"
    r"ciceksepeti|itopya|incehesap|boyner|defacto|lcw|migros|carrefour|"
    r"a101|bim|sok|ikea|koctas|decathlon)\s*:\s+",
)


def _fold_title(title: str | None) -> str:
    """Başlık karşılaştırması için Türkçe/ASCII harf, kesme ve mağaza öneki normalizasyonu.

    ``exclusion_normalize`` NFKD ile ş/ğ/ü/ö/ç harflerini sadeleştirir ancak
    noktasız ``ı`` harfini korur; ayrıca büyük ``I`` harfi ``normalize`` içinde
    ``ı`` olduğundan ``PROFASHION`` ile ``Profashion`` ayrışabiliyordu. Başlık
    kimliğinde ``ı -> i`` dönüşümü hem Türkçe ASCII yazımları (``Kirmizi`` /
    ``Kırmızı``) hem büyük harfli İngilizce marka/model adlarını eşitler.
    """
    folded = exclusion_normalize(title).replace("ı", "i")
    stripped = _TITLE_STORE_PREFIX_RE.sub("", folded)
    if stripped != folded and len(re.findall(r"[a-z0-9]+", stripped)) >= 2:
        folded = stripped
    return folded


def dedup_key(title: str | None) -> str | None:
    """Ürün adını kaynak biçimlerinden bağımsız, noktalama duyarsız anahtara çevir.

    Çağıran, fiyat/indirim/CTA satırlarını ayıklayan ``_search_query`` sonucunu
    verir. Harf ve sayı token'larını sıralamak; baştaki emoji, tire, noktalama,
    ``6.2L``/``6,2 L`` gibi yazım ve kelime sırası farklarını aynı üründe toplar.
    Token tekrarları korunur; benzer ama farklı model numaraları eşleşmez.
    """
    if not title or price_filter.is_generic_title(title):
        return None
    tokens = [token.replace(",", ".") for token in DEDUP_TOKEN_RE.findall(_fold_title(title))]
    key = " ".join(sorted(tokens))
    return key or None


def dedup_badge(
    count: int, *, sources: Sequence[str] | None = None, short: bool = False,
    compact: bool = False,
) -> str:
    """Çoklu paylaşım notunun metni; ilk gönderimde (``count < 2``) boş döner.

    Kullanıcı isteği: "2-3-4 kere paylaşıldı şu şu şu gruplarda" — not, en
    altta kaynak grup adı satırının YERİNE yazılıp mesajı kapatır. 5 ve üzeri
    sayıda vurgu artar (🚨 ... — KAÇIRMA!). ``compact=True`` adları korur ama
    süsü ("· tek grup", "— KAÇIRMA!") atar: dar açıklamada adlar sığsın diye
    ilk küçültme basamağı budur. ``short=True`` grup adlarını tümden yazmaz;
    yalnız adı olmayan (dolayısıyla link kaybı yaratmayan) durumlarda kullanılır.
    """
    if count < 2:
        return ""
    unique_names = list(
        dict.fromkeys(str(name).strip() for name in (sources or []) if str(name).strip())
    )
    # Ayraç VİRGÜL değil: grup adlarının kendisi virgül içerebiliyor ("… Kupon
    # Haberleri, İndirimde Al") ve virgülle birleştirmek rozeti yanlış yerden
    # bölüyor, ikinci grubun mesaj adresini kaybettiriyordu (kullanıcı bildirimi:
    # "ilk sayfanın linkini gömmüşüz ama ikinci bildirimin linki yok").
    names = BADGE_SOURCE_SEPARATOR.join(unique_names)
    if short:
        names = ""
    head = "📌" if count < 5 else "🚨"
    if compact:
        head = "📌"
    line = f"{head} {count} kere paylaşıldı"
    if names:
        line += f": {names}"
        if len(unique_names) == 1 and not compact:
            # Sayaç paylaşım adedini, liste kaynak adlarını tekil tutar. Aynı
            # gruptaki ikinci paylaşımda ikinci bir grup adı uydurmak yerine
            # tek grup olduğunu açıkça belirt.
            line += " · tek grup"
    if count >= 5 and not compact:
        line += " — KAÇIRMA!"
    return line


def dedup_badge_entities(
    badge: str, *, bot_api: bool = False, offset: int = 0,
    links: dict[str, str] | None = None, names: Sequence[str] | None = None,
) -> list[Any]:
    """Rozeti biçimlendir: başlık kalın, HER grup adı kendi mesajına bağlı.

    Kullanıcı isteği: nota yazılan her grup adı, o grubun kendi mesajına
    gömülü olsun. ``names`` verilirse (canlı rozet, ``entry["sources"]``)
    adlar METİNDEN yeniden ayrıştırılmaz: grup adının içinde ``" | "`` ayraç
    geçse bile tam adın üzerine tek link oturur. Verilmezse geriye dönük
    ayrıştırma (``dedup_badge_sources``) sürer. ``offset`` rozetin iletideki
    başlangıcıdır (bildirim metninin sonunda).
    """
    if not badge or not DEDUP_BADGE_RE.match(badge.strip()):
        return []
    entities: list[Any] = []
    headline = badge.split("\n")[0]
    start = int(offset)
    if bot_api:
        entities.append({"type": "bold", "offset": start,
                         "length": utf16_length(headline)})
    else:
        entities.append(types.MessageEntityBold(offset=start,
                                                length=utf16_length(headline)))
    for name, name_start, name_length in dedup_badge_name_spans(badge, names):
        url = (links or {}).get(name)
        if not url:
            continue
        if bot_api:
            entities.append({"type": "text_link", "offset": start + name_start,
                             "length": name_length, "url": str(url)})
        else:
            entities.append(types.MessageEntityTextUrl(
                offset=start + name_start, length=name_length, url=str(url)))
    return entities


def _dedup_badge_match(content: str) -> re.Match[str] | None:
    """Satır çoklu paylaşım notu mu? (yeni biçim ya da eski rozet)"""
    stripped = (content or "").strip()
    return DEDUP_BADGE_RE.match(stripped) or DEDUP_BADGE_LEGACY_RE.match(stripped)


def _badge_source_parts(line: str) -> list[str]:
    """Rozet satırındaki ad listesini ``|`` sınırlarıyla böl.

    Grup adlarının KENDİSİ virgül içerebilir ("İndirim Varsa Anlık Fırsat
    Alışveriş Kampanya Kupon Haberleri, İndirimde Al"). Virgülle birleştirme
    adları ikiye bölüyor, rozetteki link aralıkları yanlış ada oturuyor ve
    grubun/b grupların mesaj adresi kayboluyordu. Bu yüzden ayraç ``|``'dir.

    Yeni biçimde TEK ad yazıldığında rozet "· tek grup" eki alır ve o adın
    kendisi virgül içerebilir ("… Sıcak Fırsatlar, Alışveriş & Kupon"). Bu eki
    yok sayıp virgülden bölmek, tek grupta bile adı ikiye bölüp mesaj linkini
    kaybettiriyordu. Bu yüzden "· tek grup" eki varsa virgülden BÖLÜNMEZ; eki
    olmayan ayraçsız rozet eski (virgülle ayrılmış) biçim sayılıp bölünür.
    """
    payload = line.partition(":")[2]
    new_single = bool(_BADGE_NEW_SINGLE_SUFFIX_RE.search(payload))
    payload = _BADGE_TRAILING_DECOR_RE.sub("", payload)
    payload = payload.replace(BADGE_SOURCE_SEPARATOR, "\x00")
    parts = [part.strip() for part in payload.split("\x00")]
    if new_single:
        # "· tek grup": ad tektir ve virgül içerebilir; BÖLÜNMEZ.
        return [part for part in parts if part]
    # Ayraç yok: virgülle ayrılmış ESKİ biçim rozeti. ``— KAÇIRMA!`` eki burada
    # karar vermez (yeni biçim 5+ paylaşımda da yazar); tek gruplu yeni rozet ise
    # yukarıdaki ekle zaten yakalandı. Kalan belirsizlik yalnız "compact" (süssüz)
    # yeni rozette virgüllü tek addır; orada eski biçim lehine bölmek korunuyor,
    # çünkü eski rozetler geçmişte yazılmış ve hâlâ okunmak zorunda.
    if len(parts) == 1 and "," in parts[0]:
        parts = [part.strip() for part in parts[0].split(",")]
    return [part for part in parts if part]


def dedup_badge_sources(content: str | None) -> list[str]:
    """Rozet satırındaki grup adlarını ayıkla (güncelleme ve açılış taraması).

    Adlar ``|`` ile ayrılır; böylece ad içindeki virgül adı bölmez ve rozetteki
    her ad kendi mesaj adresini korur. Eski (virgülle ayrılmış) rozetler de
    okunabilir.
    """
    line = (content or "").strip()
    if not DEDUP_BADGE_RE.match(line):
        return []
    return _badge_source_parts(line)


def dedup_badge_names(content: str | None, links: dict[str, str] | None = None) -> list[str]:
    """Rozetteki grup adlarını, link ETİKETLERİNİ yetkili sayarak çöz.

    Grup adının kendisi ``" | "`` taşıyorsa (``Yarı Fiyatına Ürünler | Amazon
    Depo``, ``🎯indirimalarmi | Önemli Fırsatlar 💥``) ``dedup_badge_sources``
    adı ikiye böler ve link sözlüğünde tam ad bulunamadığı için o grup LINKsİZ
    kalıyordu. Gönderilmiş rozette o adın ``text_link`` etiketi TAM adı taşır;
    ``dedup_source_links`` bunu anahtar olarak verir. Ardışık parçalar tam
    etiketle birleştirilir; linki olmayan adlarda eski ayrıştırma sürer.
    Açılış taraması (preload) ve kaydı boşalmış canlı rozet için kullanılır.
    """
    line = (content or "").strip()
    parts = dedup_badge_sources(line)
    labels = {
        name for name in (links or {})
        if name and name != DEDUP_MESSAGE_LINK_KEY and name in line
    }
    if not parts or not labels:
        return parts
    merged: list[str] = []
    index = 0
    while index < len(parts):
        longest: tuple[int, str] | None = None
        for end in range(index + 2, len(parts) + 1):
            candidate = BADGE_SOURCE_SEPARATOR.join(parts[index:end])
            if candidate in labels:
                longest = (end, candidate)
        if longest:
            merged.append(longest[1])
            index = longest[0]
        else:
            merged.append(parts[index])
            index += 1
    return merged


def dedup_badge_name_spans(
    badge: str | None, names: Sequence[str] | None = None,
) -> list[tuple[str, int, int]]:
    """Rozetteki her grup adının ``(ad, UTF-16 başlangıç, uzunluk)`` konumları.

    ``names`` (canlı kayıttaki ``sources``) verilirse adlar METİNDEN yeniden
    bölünmez; tam adlar sırayla aranır. Bu, adın içindeki ``" | "`` ayracını
    korur ve linkin adın TAMAMINA oturmasını sağlar. Verilmezse eski
    ayrıştırma (``dedup_badge_sources``) kullanılır. Konumlar rozetin
    KENDİSİNE göredir; çağıran rozetin metindeki başlangıcını ekler.
    """
    text = badge or ""
    if not DEDUP_BADGE_RE.match(text.strip()):
        return []
    spans: list[tuple[str, int, int]] = []
    cursor = 0
    for name in (list(names) if names is not None else dedup_badge_sources(text)):
        index = text.find(name, cursor)
        if index < 0:
            continue  # "— KAÇIRMA!" / "· tek grup" kırpması adı bölmüş olabilir
        start = utf16_length(text[:index])
        spans.append((name, start, utf16_length(name)))
        cursor = index + len(name)
    return spans


def _entity_url_and_span(entity: Any) -> tuple[str, int, int] | None:
    """Entity'den ``(url, UTF-16 offset, uzunluk)``; bağlantı taşımıyorsa ``None``."""
    if isinstance(entity, dict):
        url = entity.get("url")
        offset, length = entity.get("offset", 0), entity.get("length", 0)
    else:
        url = getattr(entity, "url", None)
        offset, length = getattr(entity, "offset", 0), getattr(entity, "length", 0)
    if not url:
        return None
    try:
        offset, length = int(offset), int(length)
    except (TypeError, ValueError):
        return None
    if offset < 0 or length <= 0:
        return None
    return str(url), offset, length


def _is_telegram_link(url: str) -> bool:
    """Adres bir Telegram mesajı mı? (yayın/grup iletisinin kendi bağlantısı)"""
    lowered = str(url or "").strip().lower()
    return lowered.startswith(("https://t.me/", "http://t.me/", "https://telegram.me/",
                               "http://telegram.me/", "https://telegram.dog/",
                               "http://telegram.dog/"))


def dedup_source_links(text: str | None, entities: Sequence[Any] | None = None) -> dict[str, str]:
    """Metindeki Telegram bağlantılarını kapsadıkları yazıyla eşle (ad → adres).

    Hem yeni düzenin kaynak adı (adres adın İÇİNE gömülü) hem de eski biçimin
    ``🔗 Mesajı Gör: <adres>`` satırı okunur; açılış taraması bir bildirimin
    grup adlarını adresleriyle birlikte geri kurabilsin diye kullanılır.
    """
    body = text or ""
    mapping: dict[str, str] = {}
    for entity in entities or []:
        info = _entity_url_and_span(entity)
        if not info:
            continue
        url, offset, length = info
        if not _is_telegram_link(url):
            continue
        label = utf16_slice(body, offset, length).strip()
        if label and "t.me/" not in label.casefold():
            mapping.setdefault(label, url)
    # Eski biçim: adres görünür bir satırdadır, entity yoktur.
    for line in body.splitlines():
        stripped = line.strip()
        if MESSAGE_LINK_LABEL in stripped and "t.me/" in stripped.casefold():
            tail = stripped.split(MESSAGE_LINK_LABEL, 1)[1].lstrip(": ").strip()
            candidate = clean_url(tail)
            if _is_telegram_link(candidate):
                mapping.setdefault(DEDUP_MESSAGE_LINK_KEY, candidate)
    return mapping


# Görünür ``Mesajı Gör`` satırından okunan adresin geçici anahtarı: satır bir
# grup adı taşımaz, yalnız o bildirimin kendi adresidir.
DEDUP_MESSAGE_LINK_KEY = "\x00mesaj-link"


def _dedup_badge_location(text: str) -> tuple[int, int, int] | None:
    """Rozetin konumunu bul: not artık mesajı kapatan son satırdır.

    Öncelik yeni biçimdedir (en son anlamlı satır). Eski gönderilmiş
    bildirimlerin rozeti ya en üstte ya fiyat satırının altındaydı; o konumlar
    yalnızca geriye dönük olarak taranır ki geçmiş mesajlar da düzeltilebilsin.
    """
    lines = text.splitlines(keepends=True)
    offset = len(text)
    for line in reversed(lines):
        content = line.rstrip("\r\n")
        offset -= len(line)
        if not content.strip():
            continue
        match = _dedup_badge_match(content)
        if not match:
            break  # son anlamlı satır rozet değil → yeni biçim yok
        count = _dedup_badge_count(match)
        return count, offset, offset + len(content)

    offset = 0
    for index, line in enumerate(lines[:8]):
        content = line.rstrip("\r\n")
        match = _dedup_badge_match(content)
        if not match:
            offset += len(line)
            continue
        previous_nonempty = next(
            (prior.strip() for prior in reversed(lines[:index]) if prior.strip()),
            "",
        )
        has_price_before = any(is_price_line(prior) for prior in lines[:index])
        if index != 0 and not is_price_line(previous_nonempty) \
                and (has_price_before or index > 2):
            offset += len(line)
            continue
        return _dedup_badge_count(match), offset, offset + len(content)
    return None


def _dedup_badge_count(match: re.Match[str]) -> int:
    """Eşleşmeden paylaşım sayısını çıkar; okunamazsa 1 say."""
    try:
        return max(1, int(match.group(1)))
    except (ValueError, IndexError):
        return 1


def _strip_dedup_badge_details(
    text: str | None,
) -> tuple[int, str, tuple[int, int, int]]:
    """Rozeti çıkar ve (eski başlangıç, eski bitiş, kalan ayraç) UTF-16 aralığını ver."""
    text = text or ""
    location = _dedup_badge_location(text)
    if location is None:
        return 1, text, (0, 0, 0)
    count, line_start, line_end = location

    if line_start == 0:
        tail = text[line_end:].lstrip("\r\n")
        if tail and tail.splitlines()[0].strip().startswith(DEDUP_SOURCES_PREFIX):
            tail = tail.split("\n", 1)[1] if "\n" in tail else ""
            tail = tail.lstrip("\r\n")
        removed_end = len(text) - len(tail)
        return count, tail, (0, utf16_length(text[:removed_end]), 0)

    before = text[:line_start]
    after = text[line_end:]
    prefix = before.rstrip("\r\n")
    suffix = after.lstrip("\r\n")
    replacement = "\n\n" if prefix and suffix else ""
    removed_start = len(prefix)
    removed_end = len(text) - len(suffix)
    base = prefix + replacement + suffix
    return count, base, (
        utf16_length(text[:removed_start]),
        utf16_length(text[:removed_end]),
        utf16_length(replacement),
    )


def strip_dedup_badge(text: str | None) -> tuple[int, str]:
    """Çoklu paylaşım notunu (ya da eski rozeti) metinden güvenle söker."""
    count, base, _ = _strip_dedup_badge_details(text)
    return count, base


def shift_bot_entities(
    entities: Sequence[dict[str, Any]] | None, delta: int,
) -> list[dict[str, Any]]:
    """Bot API entity sözlüklerini ``delta`` UTF-16 birimi sağa kaydır (kopyalayarak)."""
    shifted: list[dict[str, Any]] = []
    for entity in entities or []:
        if not isinstance(entity, dict):
            continue
        clone = dict(entity)
        try:
            clone["offset"] = int(clone.get("offset", 0)) + delta
        except (TypeError, ValueError):
            continue
        shifted.append(clone)
    return shifted


def shift_telethon_entities(entities: Sequence[Any] | None, delta: int) -> list[Any]:
    """Telethon entity'lerini ``delta`` UTF-16 birimi sağa kaydır (kopyalayarak)."""
    shifted: list[Any] = []
    for entity in entities or []:
        try:
            clone = copy.copy(entity)
            clone.offset = int(getattr(entity, "offset", 0) or 0) + delta
            clone.length = int(getattr(entity, "length", 0) or 0)
        except Exception:  # noqa: BLE001 - bilinmeyen entity taşınamıyorsa atlanır
            log.debug("Rozet kaydırma entity'yi taşıyamadı: %s", type(entity).__name__)
            continue
        if clone.length > 0 and clone.offset >= 0:
            shifted.append(clone)
    return shifted


def dedup_current_prefix(text: str | None) -> str:
    """Yalnızca geriye dönük uyumluluk için eski üst-rozet önekini döndür."""
    full = text or ""
    location = _dedup_badge_location(full)
    if location is None or location[1] != 0:
        return ""
    _, base, _ = _strip_dedup_badge_details(full)
    return full[:len(full) - len(base)] if base and full.endswith(base) else full


def dedup_badge_insertion(text: str, badge: str) -> tuple[str, int, int, int]:
    """Notu mesajın en sonuna KENDİ BLOĞU olarak ekle ve bildirimi orada kapat.

    Çağıran taraf kaynak grup adı satırını zaten metinden çıkarmıştır
    (bkz. ``_dedup_apply_badge``); böylece not, grup adının yerini alır.
    """
    if not badge:
        return text, 0, 0, 0
    body = text.rstrip(" \t\r\n")
    prefix = "\n\n" if body else ""
    new_text = f"{body}{prefix}{badge}"
    insert_at = len(body)
    insert_length = len(prefix) + len(badge)
    badge_offset = utf16_length(body + prefix)
    return new_text, insert_at, insert_length, badge_offset


def _rebase_entities_for_dedup_badge(
    entities: Sequence[Any] | None,
    removed: tuple[int, int, int],
    insert_at: int,
    insert_length: int,
    *,
    bot_api: bool = False,
) -> list[Any]:
    """Entity offset'lerini notun kaldırılıp sona eklenmesine göre taşı.

    Bütün konumlar (``removed``, ``insert_at``, ``insert_length``) entity'lerle aynı
    birimde, yani UTF-16 kod birimi olarak verilir.
    """
    old_start, old_end, replacement_length = removed
    removed_length = max(0, old_end - old_start)
    after_delta = replacement_length - removed_length
    result: list[Any] = []
    for entity in entities or []:
        if bot_api:
            if not isinstance(entity, dict):
                continue
            clone: Any = dict(entity)
            try:
                offset = int(clone.get("offset", 0))
                length = int(clone.get("length", 0))
            except (TypeError, ValueError):
                continue
        else:
            try:
                clone = copy.copy(entity)
                offset = int(getattr(entity, "offset", 0) or 0)
                length = int(getattr(entity, "length", 0) or 0)
            except Exception:  # noqa: BLE001 - bilinmeyen entity taşınamıyorsa atla
                continue
        if offset < 0 or length <= 0:
            continue
        end = offset + length
        if removed_length and offset < old_end and end > old_start:
            continue  # eski rozetin kendi biçimlendirmesi / çakışan entity
        if removed_length and offset >= old_end:
            offset += after_delta
        if offset < insert_at < offset + length:
            length += insert_length
        elif offset >= insert_at:
            offset += insert_length
        if bot_api:
            clone["offset"] = offset
            clone["length"] = length
        else:
            try:
                clone.offset = offset
                clone.length = length
            except Exception:  # noqa: BLE001 - entity kopyalanamazsa atla
                continue
        result.append(clone)
    return result


def dedup_rebased_bot_entities(
    entities: Sequence[dict[str, Any]] | None, old_prefix_len: int, new_prefix_len: int,
) -> list[dict[str, Any]]:
    """Rozet değişiminde Bot API entity'lerini taşı: eski rozet içindekiler atılır."""
    kept: list[dict[str, Any]] = []
    for entity in entities or []:
        if not isinstance(entity, dict):
            continue
        try:
            offset = int(entity.get("offset", 0))
        except (TypeError, ValueError):
            continue
        if offset >= old_prefix_len:
            kept.append(entity)
    return shift_bot_entities(kept, new_prefix_len - old_prefix_len)


def dedup_rebased_tl_entities(
    entities: Sequence[Any] | None, old_prefix_len: int, new_prefix_len: int,
) -> list[Any]:
    """Rozet değişiminde Telethon entity'lerini taşı: eski rozet içindekiler atılır."""
    kept: list[Any] = []
    for entity in entities or []:
        try:
            offset = int(getattr(entity, "offset", 0) or 0)
        except (TypeError, ValueError):
            continue
        if offset >= old_prefix_len:
            kept.append(entity)
    return shift_telethon_entities(kept, new_prefix_len - old_prefix_len)


def note_dedup_source(
    entry: dict[str, Any], source_name: str | None, link: str | None = None,
) -> None:
    """Tekrarı paylaşan grubun adını (ve varsa mesaj adresini) kayda ekle.

    ``links`` sözlüğü ``{grup adı: o grubun mesajı}`` tutar. Rozette yazan HER
    adres, o adın kendi grubunun mesajına gömülür (kullanıcı isteği): iki grup
    paylaştığında yalnız ilk gelen mesajın linki değil, ikisinin linki de
    bulunur. Eski kayıtlarda/önbellek taramasında link bilinmiyorsa ad yine
    yazılır, yalnız o ad link taşımaz.
    """
    name = (source_name or "").strip()
    if not name:
        return
    names = entry.setdefault("sources", [])
    if not isinstance(names, list):
        names = entry["sources"] = []
    if name not in names:
        names.append(name)
    url = str(link or "").strip()
    if url:
        links = entry.setdefault("links", {})
        if not isinstance(links, dict):
            links = entry["links"] = {}
        # Aynı grup bir fırsatı iki kez paylaşırsa ad tek kez yazılır; adres de
        # ilk (bildirimin kendisini taşıyan) paylaşımda kalır. Böylece rozetteki
        # ad, bildirimde gösterilen mesajı açar.
        links.setdefault(name, url)


def dedup_badge_links(entry: dict[str, Any], names: Sequence[str]) -> dict[str, str]:
    """Rozette yazılacak adların adresleri: ``{ad: t.me linki}``.

    ``entry["links"]`` canlı paylaşımların adreslerini, ``entry["link_map"]``
    açılış taramasının gönderilmiş iletiden okuduğu adresleri tutar. İkisi
    AD BAZINDA birleştirilir: eski kod ``links`` içinde tek bir ad bile bulsa
    hemen dönüp kalan adların ``link_map`` adreslerini yok sayıyordu; rozette
    bazı adlar linkli, bazıları linksiz kalıyordu (kullanıcı bildirimi). Hiçbir
    adres yoksa ad yine yazılır, yalnız o ad link taşımaz.
    """
    result: dict[str, str] = {}
    links = entry.get("links")
    if isinstance(links, dict):
        for name in names:
            url = links.get(name)
            if url:
                result[name] = str(url)
    stored = entry.get("link_map")
    if isinstance(stored, dict):
        for name in names:
            if name in result:
                continue
            url = stored.get(name)
            if url and url != DEDUP_MESSAGE_LINK_KEY:
                result[name] = str(url)
        fallback = stored.get(DEDUP_MESSAGE_LINK_KEY)
        unique = list(dict.fromkeys(names))
        if fallback and len(unique) == 1 and unique[0] not in result:
            # Yalnız tek ad ve yalnız ``Mesajı Gör`` satırı biliniyorsa o adres
            # o adın kendisidir (eski biçim: ad + ayrı link satırı).
            result[unique[0]] = str(fallback)
    return result


def dedup_footer_name(text: str | None) -> str:
    """Gönderilmiş bir bildirimin en altındaki kaynak grup adını bul.

    Açılış taramasında (``dedup_preload``) eski bildirimlerin tekrar kaydına
    yazılır; tekrar yakalandığında bu satır kaldırılıp yerine çoklu paylaşım
    notu konur. Son blok yalnızca şu koşullarda grup adı sayılır: bildirim
    düzenine ait bir iz vardır (yeni düzende ``👉 Ürün Linki 👈``; eski düzende
    ``Mesajı Gör`` satırı — ham forward'larda ikisi de yoktur, yani ad bloğu da
    yoktur), tek satırdır, makul uzunluktadır ve link/etiket taşımaz.
    """
    blocks = [block.strip() for block in (text or "").split("\n\n") if block.strip()]
    if len(blocks) < 2:
        return ""
    last = blocks[-1]
    if "\n" in last or len(last) > 80 or last.startswith(("🔗", "📌", "🚨")):
        return ""
    if MESSAGE_LINK_LABEL in last or "t.me/" in last.casefold():
        return ""
    has_layout_mark = any(
        MESSAGE_LINK_LABEL in block
        or PRODUCT_LINK_TEXT in block
        or PRODUCT_LINK_LABEL in block
        or block.startswith("👉")
        for block in blocks[:-1]
    )
    if not has_layout_mark:
        return ""
    return last
