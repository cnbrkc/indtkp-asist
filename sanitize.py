"""Mesaj temizleme, gizli bağlantılar ve bağlantı eki (saf metin/entity işleri)."""

from __future__ import annotations

import logging
import copy
import re
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from keywords import normalize
from types import SimpleNamespace
from typing import Any

log = logging.getLogger("telegram-filter")


LINK_APPENDIX_LIMIT = 4       # ileti sonuna en fazla kaç ürün satırı ve (ayrıca) kaç 🔗 satırı yazılsın


# ---------------------------------------------------------------------------
# Gizli bağlantılar (metin altına gizlenmiş linkler, buton linkleri)
# ---------------------------------------------------------------------------
#
# İndirim kanalları ürün linkini çoğu zaman "Fırsata Git" yazısının ALTINA
# gizler (MessageEntityTextUrl) ya da mesajın altındaki inline butona koyar.
# Bu bağlantılar düz metinde görünmediği için eski sürüm yalnızca "Fırsata Git"
# yazıyordu. Aşağıdaki yardımcılar bağlantıyı nerede olursa olsun bulur,
# iletiyle birlikte gönderir ve bildirimde tıklanabilir tutar.

URL_RE = re.compile(
    r"(?:"
    r"(?:https?://|whatsapp://|t\.me/|telegram\.me/|www\.|wa\.me/|wa\.link/)"
    r"[^\s<>\"')\]}]+"
    r"|(?<![\w.-])(?:[\w-]+\.)*(?:wa\.me|wa\.link|whatsapp\.(?:com|net)|instagram\.com|instagr\.am|"
    r"akakce\.com|marketfiyati\.org\.tr|cimri\.com|google\.com(?:\.tr)?)(?:[/?#][^\s<>\"')\]}]*)?"
    r"(?=$|[\s<>\"')\]},;:!?…])"
    r")",
    re.IGNORECASE,
)
_URL_TAIL_TRIM = ".,;:!?…\"'”’)]}>»"
DISCLOSURE_TOKEN_RE = re.compile(r"(?<![\w#])#?(?:işbirliği|reklam)(?!\w)", re.IGNORECASE)
# Bir "satır sonu bloğu": ardışık satır sonları ve aralarındaki yatay boşluklar.
# Temizlikten arta kalan fazladan boş satırları bulmak için kullanılır.
_BLANK_RUN_RE = re.compile(r"[ \t\r]*(?:\n[ \t\r]*)+")
_EDGE_BLANK_RE = re.compile(r"\A[ \t\r\n]+|[ \t\r\n]+\Z")
# Arama sorgusunun başındaki emoji/sembol ("🛍️", "-", "|") sorguya girmemeli.
_LEADING_NOISE_RE = re.compile(r"\A[^\w]+", re.UNICODE)

# --- Kanal tanıtımı ve hashtag satırları ------------------------------------
# Kaynaklar mesajın altına kendi kanallarını ve etiket yığınını ekler:
#   💚Whatsapp Önemli Fırsatlar
#   #amazon #indirimalarmi
# Kullanıcı isteği: bunlar bildirime hiç girmesin ("tertemiz görünüm"). İki
# kural bilerek dar tutulur; böylece fiyat/ürün bilgisi taşıyan satır asla
# silinmez (veri kaybı yok):
#   1) Satırdaki TÜM kelimeler hashtag'lerden geliyorsa (salt etiket satırı).
#   2) Satır bir mesajlaşma kanalını (WhatsApp/Telegram) tanıtıyor ve
#      kelimelerinin TAMAMI tanıtım sözlüğünden geliyorsa.
# Sözlükte olmayan tek bir kelime satırı korur: "WhatsApp'tan bilgi ...
# 9 TL" içerik satırı olduğu gibi kalır.
# Kelime = harf/sayı dizisi (emoji ve noktalama atlanır). Türkçe ek, kesme
# işaretiyle ayrıldığında ("WhatsApp'ın", "kanalımıza") kelimenin parçası
# sayılır; böylece ek yüzünden tanıtım satırı gözden kaçmaz.
WORD_TOKEN_RE = re.compile(r"([^\W_]+)(?:['’][^\W_]+)?", re.UNICODE)
HASHTAG_WORD_RE = re.compile(r"#([^\W_]+)", re.UNICODE)
# Kök sözlüğü: 4+ harfli kökler önek eşleşir (fırsat→fırsatlar, katıl→katılın),
# kısa kelimeler birebir eşleşir (ve, ile, mi…). Türkçe küçük harfle yazılır.
PROMO_WORD_ROOTS = frozenset("""
abone aile amazon bize bizi biz bildirim bu burada buradan burdan da davet de diğer diger
duyuru edin ekle ekleyin fırsat gel gelin grup grub gün güncel haber hemen her hesap
herkes hepsiburada ile için indirim instagram ınstagram istersen kampanya kanal katıl kaçır link
olun önemli paylaş sayfa sitemiz şimdi takip telegram topluluk trendyol tüm üye
ve whatsapp watsap web wp yazın
""".split())
# Tanıtım satırı sayılmak için satırda bu köklerden biri geçmelidir: satır bir
# sosyal/mesajlaşma kanalını (WhatsApp/Telegram/Instagram) tanıtıyorsa tanıtımdır.
CHANNEL_WORD_ROOTS = (
    "whatsapp", "watsap", "whats", "wp", "telegram",
    "instagram", "ınstagram",
)


def utf16_length(text: str) -> int:
    """Telegram offset/length değerleri UTF-16 kod birimi sayar (emoji 2 birim)."""
    return len((text or "").encode("utf-16-le")) // 2


def utf16_slice(text: str, offset: int, length: int) -> str:
    """UTF-16 offset'leriyle güvenli metin dilimi (emoji içeren mesajlarda düz dilim kayar)."""
    if not text or offset is None or length is None or offset < 0 or length <= 0:
        return ""
    data = text.encode("utf-16-le")
    return data[offset * 2:(offset + length) * 2].decode("utf-16-le", "replace")


def clean_url(url: Any) -> str:
    """Bağlantıyı kırp, sonundaki noktalama işaretlerini at, şema ekle."""
    text = str(url or "").strip()
    while text and text[-1] in _URL_TAIL_TRIM:
        text = text[:-1]
    lowered = text.lower()
    scheme_less_service = re.match(
        r"(?:[\w-]+\.)*(?:wa\.me|wa\.link|whatsapp\.(?:com|net)|instagram\.com|instagr\.am|"
        r"akakce\.com|marketfiyati\.org\.tr|cimri\.com|google\.com(?:\.tr)?)(?:$|[/?#])",
        lowered,
    )
    if lowered.startswith(("t.me/", "telegram.me/", "www.")) or scheme_less_service:
        text = "https://" + text
    return text


def _as_message(obj: Any) -> Any:
    """Olay (Event) veya mesaj nesnesi verildiğinde mesajı döndür."""
    inner = getattr(obj, "message", None)
    if inner is not None and not isinstance(inner, str):
        return inner
    return obj


def message_text(obj: Any) -> str:
    """Mesajın ham metni (biçimlendirmeden bağımsız); metin girdisini de kabul et."""
    if isinstance(obj, str):
        return obj
    message = _as_message(obj)
    text = getattr(message, "message", None)
    if isinstance(text, str):
        return text
    raw = getattr(obj, "raw_text", None)
    return raw if isinstance(raw, str) else ""


def message_entities(obj: Any) -> list[Any]:
    message = _as_message(obj)
    entities = getattr(message, "entities", None)
    return list(entities) if entities else []


def sent_message_ids(result: Any) -> list[int]:
    """``send_message``/``forward_messages`` çıktısından mesaj ID'lerini topla.

    Tek mesaj da liste de dönebilir; ID yoksa boş liste verir (silme atlanır).
    """
    items = result if isinstance(result, (list, tuple, set)) else [result]
    ids: list[int] = []
    for item in items:
        message_id = getattr(item, "id", None)
        if isinstance(message_id, int) and not isinstance(message_id, bool):
            ids.append(message_id)
    return ids


def first_sent_item(result: Any) -> Any | None:
    """Gönderim çıktısının ilk mesaj nesnesi (ID'lisi tercih edilir)."""
    items = result if isinstance(result, (list, tuple, set)) else [result]
    for item in items:
        message_id = getattr(item, "id", None)
        if isinstance(message_id, int) and not isinstance(message_id, bool):
            return item
    for item in items:
        if item is not None:
            return item
    return None


def first_sent_text(result: Any) -> str | None:
    """Gönderilen iletinin metni/açıklaması; okunamazsa ``None``."""
    text = getattr(first_sent_item(result), "message", None)
    return text if isinstance(text, str) and text else None


def first_sent_entities(result: Any) -> list[Any] | None:
    """Gönderilen iletinin biçim entity'leri; yoksa ``None``."""
    entities = getattr(first_sent_item(result), "entities", None)
    return list(entities) if entities else None


def button_link(button: Any) -> tuple[str, str] | None:
    """Bir inline butonun (url, etiket) bilgisini döndür.

    Telethon iki farklı şema kullanabiliyor: eski sürümlerde ``KeyboardButtonUrl``
    doğrudan ``.url`` taşır; yeni sürümlerde ``KeyboardInlineButton`` içindeki
    ``.type`` (``InlineButtonTypeUrl``/``InlineButtonTypeWebView``) URL'yi tutar.
    """
    label = str(getattr(button, "text", "") or "").strip()
    url = getattr(button, "url", None)
    if not url:
        inner = getattr(button, "type", None)
        url = getattr(inner, "url", None)
        if not url:
            # "Kopyala" butonu bazen bağlantının kendisini kopyalatır.
            copy_text = getattr(inner, "copy_text", None)
            if isinstance(copy_text, str) and URL_RE.match(copy_text.strip()):
                url = copy_text
    if not url:
        return None
    cleaned = clean_url(url)
    if not cleaned:
        return None
    return cleaned, label


def is_whatsapp_url(url: Any) -> bool:
    """WhatsApp bağlantı alan adlarını tanı; benzer adlı yabancı alanları eşleştirme."""
    text = clean_url(url)
    lowered = text.lower()
    if lowered.startswith("whatsapp://"):
        return True
    if "://" not in lowered:
        text = "https://" + text
    try:
        host = (urllib.parse.urlsplit(text).hostname or "").rstrip(".").lower()
    except ValueError:
        return False
    return (
        host == "wa.me" or host.endswith(".wa.me")
        or host == "wa.link" or host.endswith(".wa.link")
        or host == "whatsapp.com" or host.endswith(".whatsapp.com")
        or host == "whatsapp.net" or host.endswith(".whatsapp.net")
    )


def is_instagram_url(url: Any) -> bool:
    """Instagram tanıtım bağlantısını alan adıyla tanı; benzer alanları eleme."""
    text = clean_url(url)
    if "://" not in text.lower():
        text = "https://" + text
    try:
        host = (urllib.parse.urlsplit(text).hostname or "").rstrip(".").lower()
    except ValueError:
        return False
    return (
        host == "instagram.com" or host.endswith(".instagram.com")
        or host == "instagr.am" or host.endswith(".instagr.am")
    )


def _utf16_to_index(text: str, offset: int) -> int:
    """Telegram UTF-16 offset'ini Python karakter indeksine çevir."""
    target = max(0, int(offset or 0))
    if target == 0:
        # Offset 0 metnin başıdır; aşağıdaki döngü onu ilk karakterin içi
        # sayıp 1 döndürürdü ve baştaki biçim ilk karakterini kaybederdi.
        return 0
    units = 0
    for index, char in enumerate(text):
        width = 2 if ord(char) > 0xFFFF else 1
        if units + width > target:
            # Telegram entity'leri normalde bir Unicode karakterinin ortasına
            # düşmez; bozuk girdide karakteri korumak için sağa yuvarla.
            return index + 1
        units += width
        if units == target:
            return index + 1
    return len(text)


def _merge_spans(spans: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """Örtüşen/bitişik silme aralıklarını tek aralığa indir."""
    ordered = sorted((start, end) for start, end in spans if end > start)
    merged: list[list[int]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _with_adjacent_space(text: str, start: int, end: int) -> tuple[int, int]:
    """Yalnızca silinen parçanın yanındaki boşluk/tabı al; başka metne dokunma."""
    left, right = start, end
    while left > 0 and text[left - 1] in " \t":
        left -= 1
    if left == start:
        while right < len(text) and text[right] in " \t":
            right += 1
    return left, right


def _blank_line_spans(text: str, *, keep: int = 2) -> list[tuple[int, int]]:
    """Fazla boş satırları ``keep`` satır sonuna indirecek silme aralıkları.

    WhatsApp linki/``#reklam`` etiketi silinince geriye çoklu boş satır kalır;
    bu aralıklar onları tek boş satıra indirir (``keep=2`` → bölümler arasında
    bir boş satır). Ayrıca satır sonu bloklarının içindeki yatay boşluklar ile
    baştaki/sondaki boş satırlar atılır. **Yalnızca** boşluk/tab/``\\r``/satır
    sonu karakterleri silinir; metin içeriğine dokunulmaz, veri kaybolmaz.
    """
    drop: list[int] = []
    for match in _BLANK_RUN_RE.finditer(text):
        newlines = [i for i in range(match.start(), match.end()) if text[i] == "\n"]
        kept = set(newlines[:keep])
        drop.extend(i for i in range(match.start(), match.end()) if i not in kept)
    # Kenardaki boş blok satır sonu içeriyorsa (yani boş satırsa) tümüyle atılır;
    # ilk satırın girintisi gibi satır içi boşluklar korunur.
    for edge in _EDGE_BLANK_RE.finditer(text):
        if "\n" in edge.group(0):
            drop.extend(range(edge.start(), edge.end()))
    return _merge_spans([(index, index + 1) for index in sorted(set(drop))])


def _remove_spans(text: str, spans: Sequence[tuple[int, int]]) -> str:
    for start, end in reversed(spans):
        text = text[:start] + text[end:]
    return text


def _index_after_removals(index: int, removed_spans: Sequence[tuple[int, int]]) -> int:
    removed = sum(
        max(0, min(index, end) - start)
        for start, end in removed_spans if index > start
    )
    return max(0, index - removed)


def _disclosure_spans(
    text: str,
    *,
    protected: Sequence[tuple[int, int]] = (),
) -> list[tuple[int, int]]:
    """Yalnızca bağımsız ``işbirliği``/``reklam`` kelimelerini ve hashtag'lerini bul."""
    spans: list[tuple[int, int]] = []
    for match in DISCLOSURE_TOKEN_RE.finditer(text or ""):
        if any(match.start() < end and start < match.end() for start, end in protected):
            continue
        spans.append(_with_adjacent_space(text, match.start(), match.end()))
    return _merge_spans(spans)


def clean_disclosure_tokens(text: str) -> str:
    """Disclosure etiketlerini kaldır; URL'lerin içindeki parçaları değiştirme."""
    text = text or ""
    url_spans: list[tuple[int, int]] = []
    for match in URL_RE.finditer(text):
        raw_url = match.group(0).rstrip(_URL_TAIL_TRIM)
        if raw_url:
            url_spans.append((match.start(), match.start() + len(raw_url)))
    return _remove_spans(text, _disclosure_spans(text, protected=url_spans))


def _word_matches(token: str, roots: Iterable[str]) -> bool:
    """Kelime kök sözlüğüyle eşleşiyor mu? (4+ harfli kökler önek, kısalar tam)."""
    word = normalize(token)
    for root in roots:
        if word == root or (len(root) >= 4 and word.startswith(root)):
            return True
    return False


def _promo_line_spans(text: str, entities: Sequence[Any] = ()) -> list[tuple[int, int]]:
    """Kanal tanıtımı ve salt hashtag satırlarının silinecek aralıkları.

    Kullanıcı isteği: bildirimde "💚Whatsapp Önemli Fırsatlar" ve
    "#amazon #indirimalarmi" gibi satırlar hiç görünmesin. Kurallar dardır;
    fiyat/ürün satırı taşıyan bir satır silinmez (bkz. PROMO_WORD_ROOTS).
    Silinecek aralık satır sonunu İÇERMEZ; artakalan boş satırlar
    ``_blank_line_spans`` ile sıkıştırılır. Görünen etikette Instagram yazmasa
    bile gerçek Instagram entity'si, genel tanıtım etiketini hedefli şekilde
    tanır (ör. Instagram'a bağlı ``Kanalımıza katılın``).
    """
    instagram_entity_spans: list[tuple[int, int]] = []
    for entity in entities:
        if not is_instagram_url(getattr(entity, "url", None)):
            continue
        try:
            offset = int(getattr(entity, "offset", 0) or 0)
            length = int(getattr(entity, "length", 0) or 0)
            start = _utf16_to_index(text, offset)
            end = _utf16_to_index(text, offset + length)
        except (TypeError, ValueError, AttributeError):
            continue
        if end > start:
            instagram_entity_spans.append((start, end))
    spans: list[tuple[int, int]] = []
    for match in re.finditer(r"[^\n]+", text or ""):
        raw_line = match.group(0)
        masked = URL_RE.sub(" ", raw_line)  # URL harfleri kelime sayılmasın
        words = WORD_TOKEN_RE.findall(masked)
        if not words:
            continue
        tagged = HASHTAG_WORD_RE.findall(masked)
        if tagged and len(tagged) == len(words):
            spans.append((match.start(), match.end()))  # yalnızca etiketler
            continue
        has_instagram_link = any(
            is_instagram_url(url.group(0)) for url in URL_RE.finditer(raw_line)
        ) or any(
            start < match.end() and end > match.start()
            for start, end in instagram_entity_spans
        )
        if not has_instagram_link \
                and not any(_word_matches(word, CHANNEL_WORD_ROOTS) for word in words):
            continue
        if all(_word_matches(word, PROMO_WORD_ROOTS) for word in words):
            spans.append((match.start(), match.end()))
    # Tüm içerik silinecekse (örn. mesaj yalnızca tanıtım satırlarından oluşuyorsa)
    # hiçbir şey silme: boş bildirim göndermektense satır kalsın.
    if spans and not any(char.isalnum() for char in _remove_spans(text, spans)):
        return []
    return spans


def _remap_entities(
    text: str,
    new_text: str,
    entities: Sequence[Any],
    removed_spans: Sequence[tuple[int, int]],
) -> list[Any]:
    """Metin temizlenince UTF-16 entity aralıklarını doğru yere kaydır."""
    result: list[Any] = []

    for entity in entities:
        old_offset = int(getattr(entity, "offset", 0) or 0)
        old_length = int(getattr(entity, "length", 0) or 0)
        if old_offset < 0 or old_length <= 0:
            continue
        old_start = _utf16_to_index(text, old_offset)
        old_end = _utf16_to_index(text, old_offset + old_length)
        start = _index_after_removals(old_start, removed_spans)
        end = max(start, _index_after_removals(old_end, removed_spans))
        if end <= start:
            continue
        try:
            if isinstance(entity, dict):
                cloned = dict(entity)
                cloned["offset"] = utf16_length(new_text[:start])
                cloned["length"] = utf16_length(new_text[start:end])
            else:
                cloned = copy.copy(entity)
                cloned.offset = utf16_length(new_text[:start])
                cloned.length = utf16_length(new_text[start:end])
        except Exception:  # noqa: BLE001 - entity bilinmiyorsa içeriği koruyup atla
            log.debug("Mesaj entity'si temizlenen metne taşınamadı: %s", type(entity).__name__)
            continue
        if cloned.length > 0:
            result.append(cloned)
    return result


def _sanitize_collect_removed_spans(text: str, entities: Sequence[Any]) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """WhatsApp/Instagram URL'leri, disclosure ve promo satırları için silinecek aralıkları topla."""
    removed: list[tuple[int, int]] = []
    visible_urls: list[tuple[int, int]] = []
    for match in URL_RE.finditer(text):
        raw_url = match.group(0).rstrip(_URL_TAIL_TRIM)
        if not raw_url:
            continue
        span = (match.start(), match.start() + len(raw_url))
        visible_urls.append(span)
        if is_whatsapp_url(raw_url) or is_instagram_url(raw_url):
            removed.append(_with_adjacent_space(text, *span))
    disclosure_spans = _disclosure_spans(text, protected=visible_urls)
    removed.extend(disclosure_spans)
    removed.extend(_promo_line_spans(text, entities))
    return removed, visible_urls


def _sanitize_apply_text_cleaning(text: str, entities: Sequence[Any], removed: list[tuple[int, int]]) -> tuple[str, list[Any], list[tuple[int, int]], list[tuple[int, int]]]:
    """Silinecek aralıkları metinden çıkar ve entity offset'lerini UTF-16 bazında kaydır."""
    removed_spans = _merge_spans(removed)
    cleaned_text = _remove_spans(text, removed_spans)
    remapped_entities = _remap_entities(text, cleaned_text, entities, removed_spans) if removed_spans else list(entities)
    blank_spans = _blank_line_spans(cleaned_text)
    if blank_spans:
        compact_text = _remove_spans(cleaned_text, blank_spans)
        remapped_entities = _remap_entities(cleaned_text, compact_text, remapped_entities, blank_spans)
        cleaned_text = compact_text
    return cleaned_text, remapped_entities, removed_spans, blank_spans


def _sanitize_filter_entities(remapped_entities: Sequence[Any]) -> list[Any]:
    """Gizli WhatsApp/Instagram entity'lerini temizlenmiş listeden çıkar."""
    return [
        entity for entity in remapped_entities
        if not is_whatsapp_url(getattr(entity, "url", None))
        and not is_instagram_url(getattr(entity, "url", None))
    ]


def _sanitize_collect_preserved_links(
    message: Any, text: str, entities: Sequence[Any],
    removed_spans: Sequence[tuple[int, int]], blank_spans: Sequence[tuple[int, int]],
) -> list[dict[str, str]]:
    """Silinen disclosure etiketi altında kalan gizli URL'leri preserved_links'e taşı."""
    preserved_links = list(getattr(message, "preserved_links", None) or [])
    for entity in entities:
        url = getattr(entity, "url", None)
        if not url or is_whatsapp_url(url) or is_instagram_url(url):
            continue
        old_start = _utf16_to_index(text, int(getattr(entity, "offset", 0) or 0))
        old_end = _utf16_to_index(text, int(getattr(entity, "offset", 0) or 0) + int(getattr(entity, "length", 0) or 0))
        new_start = _index_after_removals(old_start, removed_spans)
        new_end = _index_after_removals(old_end, removed_spans)
        if new_end - new_start < old_end - old_start:
            new_start = _index_after_removals(new_start, blank_spans)
            new_end = _index_after_removals(new_end, blank_spans)
        if old_end > old_start and new_end <= new_start:
            preserved_links.append({"url": clean_url(url), "label": "", "kind": "webpage"})
    return preserved_links


def _sanitize_dedup_preserved_links(preserved_links: list[dict[str, str]]) -> list[dict[str, str]]:
    """Aynı URL'nin tekrar taşınmasını ve Instagram hedeflerini engelle."""
    unique: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in preserved_links:
        if not isinstance(item, dict):
            continue
        url = clean_url(item.get("url"))
        key = url.rstrip("/").lower()
        if not url or is_instagram_url(url) or key in seen:
            continue
        seen.add(key)
        unique.append({"url": url, "label": str(item.get("label") or "").strip(), "kind": str(item.get("kind") or "webpage")})
    return unique


def _sanitize_clean_markup(markup: Any) -> tuple[Any | None, bool]:
    """Inline butonlardaki WhatsApp/Instagram URL'lerini ve disclosure etiketlerini temizle."""
    if markup is None:
        return None, False
    cleaned_rows: list[SimpleNamespace] = []
    markup_changed = False
    for row in getattr(markup, "rows", None) or []:
        cleaned_buttons: list[Any] = []
        for button in getattr(row, "buttons", None) or []:
            info = button_link(button)
            if info and (is_whatsapp_url(info[0]) or is_instagram_url(info[0])):
                markup_changed = True
                continue
            if not info:
                cleaned_buttons.append(button)
                continue
            label = str(getattr(button, "text", "") or "")
            new_label = clean_disclosure_tokens(label)
            if new_label != label:
                markup_changed = True
                clone = copy.copy(button)
                clone.text = new_label.strip() or "Bağlantı"
                cleaned_buttons.append(clone)
            else:
                cleaned_buttons.append(button)
        if cleaned_buttons:
            cleaned_rows.append(SimpleNamespace(buttons=cleaned_buttons))
        elif getattr(row, "buttons", None):
            markup_changed = True
    return SimpleNamespace(rows=cleaned_rows), markup_changed


def _sanitize_clean_media(media: Any) -> Any | None:
    """WhatsApp/Instagram webpage önizlemesini temizle; diğer medyayı koru."""
    webpage = getattr(media, "webpage", None)
    if webpage is not None and (is_whatsapp_url(getattr(webpage, "url", None)) or is_instagram_url(getattr(webpage, "url", None))):
        return None
    return media


def sanitize_message(obj: Any) -> SimpleNamespace:
    """İletilecek kopyayı temizle; kaynak Telethon mesajına hiçbir zaman dokunma.

    Kaldırılanlar: bağımsız ``#işbirliği``/``işbirliği``/``#reklam``/``reklam``
    ifadeleri, doğrudan WhatsApp/Instagram URL'leri, bu hedeflere ait gizli
    entity'ler/inline butonlar/önizlemeler ve kanal tanıtımı/salt hashtag
    satırları (``_promo_line_spans``). Instagram tanıtım satırları kaldırılır;
    diğer kanal ekleri, ürün metni, entity, medya ve linkler korunur.
    Silme işleminin artığında oluşan çoklu boş satırlar tek boş satıra indirilir
    (``_blank_line_spans``); bu adım yalnızca boşluk karakterlerini alır, metin
    içeriğini değiştirmez.
    """
    message = _as_message(obj)
    text = message_text(obj)
    entities = message_entities(message)

    removed, _ = _sanitize_collect_removed_spans(text, entities)
    cleaned_text, remapped_entities, removed_spans, blank_spans = _sanitize_apply_text_cleaning(text, entities, removed)
    cleaned_entities = _sanitize_filter_entities(remapped_entities)

    preserved_links = _sanitize_collect_preserved_links(message, text, entities, removed_spans, blank_spans)
    preserved_links = _sanitize_dedup_preserved_links(preserved_links)

    markup = getattr(message, "reply_markup", None)
    cleaned_markup, markup_changed = _sanitize_clean_markup(markup)

    media = _sanitize_clean_media(getattr(message, "media", None))

    changed = (
        cleaned_text != text
        or len(cleaned_entities) != len(entities)
        or markup_changed
        or media is not getattr(message, "media", None)
    )
    return SimpleNamespace(
        id=getattr(message, "id", None),
        message=cleaned_text,
        raw_text=cleaned_text,
        entities=cleaned_entities,
        reply_markup=cleaned_markup,
        media=media,
        preserved_links=preserved_links,
        file=getattr(message, "file", None),
        video=getattr(message, "video", None),
        changed=changed,
    )


def extract_links(obj: Any) -> list[dict[str, str]]:
    """Mesajdaki tüm bağlantıları bul: gizli hyperlink, buton, önizleme, düz URL.

    Her kayıt ``{"url", "label", "kind"}`` sözlüğüdür; ``kind`` şunlardan biri:
    ``entity`` (yazının altına gizlenmiş), ``button`` (inline buton),
    ``webpage`` (link önizlemesi), ``text`` (metinde açıkça görünen).
    """
    message = _as_message(obj)
    text = message_text(obj)
    found: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(url: Any, label: Any = None, kind: str = "text") -> None:
        cleaned = clean_url(url)
        if not cleaned or not URL_RE.match(cleaned):
            return
        key = cleaned.rstrip("/").lower()
        if key in seen:
            return
        seen.add(key)
        found.append({"url": cleaned, "label": str(label or "").strip() or None, "kind": kind})

    for entity in message_entities(message):
        url = getattr(entity, "url", None)
        if url:
            label = utf16_slice(text, getattr(entity, "offset", 0), getattr(entity, "length", 0))
            add(url, label, "entity")

    markup = getattr(message, "reply_markup", None)
    for row in getattr(markup, "rows", None) or []:
        for button in getattr(row, "buttons", None) or []:
            info = button_link(button)
            if info:
                add(info[0], info[1], "button")

    webpage = getattr(getattr(message, "media", None), "webpage", None)
    if webpage is not None:
        add(getattr(webpage, "url", None), getattr(webpage, "title", None), "webpage")

    for url in URL_RE.findall(text):
        add(url, None, "text")

    for item in getattr(message, "preserved_links", None) or []:
        if isinstance(item, dict):
            add(item.get("url"), item.get("label"), str(item.get("kind") or "webpage"))

    return found


def is_blank_reply(obj: Any) -> bool:
    """Başka bir mesaja YANIT olan ve ürün/fiyat/bağlantı taşıyamayan ileti mi?

    Kaynaklar kendi fırsatına açıklamasız görsel ya da çıkartmayla yanıt verir; bu
    ileti yalnız "Mesajı Gör" satırı ile kaynak adından oluşan boş bir bildirim
    üretiyordu. Fail-open: temizlenmiş metinde tek bir harf/rakam ya da herhangi
    türde bağlantı (gizli hyperlink, buton, önizleme, görünür URL) varsa ileti
    korunur; yanıt OLMAYAN açıklamasız görsel de (afiş bir fırsat olabilir) eskisi
    gibi iletilir. Forum konusunun kök bağlantısı yanıt sayılmaz.
    """
    header = getattr(_as_message(obj), "reply_to", None)
    if header is None or (getattr(header, "forum_topic", False)
                          and getattr(header, "reply_to_top_id", None) is None):
        return False
    cleaned = sanitize_message(obj)
    return not any(char.isalnum() for char in message_text(cleaned)) and not extract_links(cleaned)


def visible_urls(text: str) -> set[str]:
    """Metinde gözle görülen bağlantılar (bunları tekrar yazmaya gerek yok)."""
    return {clean_url(url).rstrip("/").lower() for url in URL_RE.findall(text or "")}


def missing_links(
    obj: Any,
    limit: int = LINK_APPENDIX_LIMIT,
    kinds: Sequence[str] | None = ("button", "webpage"),
    skip: Callable[[dict[str, str]], bool] | None = None,
) -> list[dict[str, str]]:
    """Metinde görünmeyen bağlantılar: gizli hyperlink, buton, link önizlemesi.

    ``kinds`` verilirse yalnızca o türler döner (bkz. ``LINK_KIND_GROUPS``).
    ``skip`` çağıranın zaten başka yerde taşıdığı bağlantıları eler. Eleme sınırdan
    ÖNCE yapılır: elenen bağlantı ``limit`` hakkı tüketmez, arkasındaki gerçek
    bağlantı düşmez.
    """
    visible = visible_urls(message_text(obj))
    allowed = None if kinds is None else set(kinds)
    result: list[dict[str, str]] = []
    for item in extract_links(obj):
        if item["kind"] == "text":
            continue
        if allowed is not None and item["kind"] not in allowed:
            continue
        if item["url"].rstrip("/").lower() in visible:
            continue
        if skip is not None and skip(item):
            continue
        result.append(item)
        if len(result) >= limit:
            break
    return result


def link_appendix_lines(
    obj: Any,
    kinds: Sequence[str] | None = ("button", "webpage"),
    *,
    exclude_urls: Iterable[str] = (),
    has_keyboard: bool = False,
) -> list[tuple[str, bool]]:
    """Gizli bağlantıların ``🔗`` satırları: ``(satır, vazgeçilmez mi?)``.

    Başka yerde açılabilen adres satır olmaz: ``exclude_urls`` çağıranın kendi
    taşıdığı adreslerdir (``👉 Ürün Linki 👈`` satırı); kaynağın Google/Akakçe/Cimri
    arama butonu ise ileti inline klavyeyle gidiyorsa (``has_keyboard``; bot bildirimi)
    orada düğme olarak kalır. Klavyesi olmayan hesap kopyasında arama butonunun tek
    taşıyıcısı bu satırdır; o yüzden orada yazılır.

    Bu elemeler sınırdan önce yapıldığı için elenen bağlantı satır hakkı tüketmez.
    Mesajın ilk ``LINK_APPENDIX_LIMIT`` gizli bağlantısı (hiç eleme olmasaydı
    yazılacak olanlar) vazgeçilmezdir. Eleme sayesinde yer bulan sonraki bağlantılar
    ve klavyede de durabilen buton satırları yedeklidir: ``compose_message`` bunları
    yalnız gövdeyi kırpmayacaksa yazar.
    """
    excluded = {clean_url(url).rstrip("/").lower() for url in exclude_urls if url}

    def carried_elsewhere(item: dict[str, str]) -> bool:
        if item["url"].rstrip("/").lower() in excluded:
            return True
        return has_keyboard and item["kind"] == "button" and is_search_link(item["url"])

    first = {item["url"].rstrip("/").lower() for item in missing_links(obj, kinds=kinds)}
    lines: list[tuple[str, bool]] = []
    for item in missing_links(obj, kinds=kinds, skip=carried_elsewhere):
        label = (item.get("label") or "").strip()
        text = (f"🔗 {label[:40]}: {item['url']}" if label and label.lower() not in item["url"].lower()
                else f"🔗 {item['url']}")
        required = item["url"].rstrip("/").lower() in first and not (has_keyboard and item["kind"] == "button")
        lines.append((text, required))
    return lines


def build_link_appendix(
    obj: Any,
    kinds: Sequence[str] | None = ("button", "webpage"),
    *,
    exclude_urls: Iterable[str] = (),
    has_keyboard: bool = False,
) -> str:
    """Gizli bağlantıları iletinin sonuna ekle; başka yerde açılabilen adresi yineleme.

    Satır kuralları için bkz. ``link_appendix_lines``.
    """
    return "\n".join(
        text for text, _ in link_appendix_lines(obj, kinds, exclude_urls=exclude_urls, has_keyboard=has_keyboard))


def is_search_link(url: Any) -> bool:
    """Adres bir fiyat/web araması mı (Google, Akakçe, Cimri)? Etikete bakılmaz."""
    cleaned = clean_url(url)
    if _price_search_service(cleaned):
        return True
    try:
        parsed = urllib.parse.urlsplit(cleaned)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    # Gövdedeki "🔎 Google" web aramasıdır; play.google.com gibi mağazalar ürün kalır.
    return host.removeprefix("www.") in {"google.com", "google.com.tr"} and parsed.path.startswith("/search")


def _price_search_service(url: str, label: str = "") -> str | None:
    """Bir URL'nin Google Shopping, Akakçe veya Cimri olduğunu belirle."""
    try:
        parsed = urllib.parse.urlsplit(clean_url(url))
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().rstrip(".")
    path = parsed.path.lower()
    query = urllib.parse.parse_qs(parsed.query)
    label_lower = (label or "").casefold()
    if (
        host == "akakce.com" or host.endswith(".akakce.com")
        or "akakçe" in label_lower or "akakce" in label_lower
    ):
        return "akakce"
    if (
        host == "cimri.com" or host.endswith(".cimri.com")
        or "cimri" in label_lower
    ):
        return "cimri"
    if (
        host == "google.com" or host.endswith(".google.com")
        or host == "google.com.tr" or host.endswith(".google.com.tr")
    ):
        shopping_url = (
            path.startswith("/shopping")
            or "shop" in query.get("tbm", [])
            or "28" in query.get("udm", [])
            or "shopping" in label_lower
            or "alışveriş" in label_lower
        )
        if shopping_url:
            return "google_shopping"
    if "google shopping" in label_lower or "google alışveriş" in label_lower:
        return "google_shopping"
    return None
