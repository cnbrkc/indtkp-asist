"""Mesaj kurma: iletim zinciri, üst satır, ek bölümü ve nihai metin/entity üretimi."""

from __future__ import annotations

import copy
import logging
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

import appstate
import price_filter
from botentities import entities_for_text
from composeparts import (
    _compose_assemble_text,
    _compose_build_entities,
    _compose_drop_overflow_parts,
    _compose_fit_appendix,
    _compose_truncate_body,
    _compose_truncate_header,
)
from constants import (
    APPENDIX_PRODUCT,
    APPENDIX_REQUIRED,
    APPENDIX_SPARE,
    MAX_INLINE_KEYBOARD_BUTTONS,
    MESSAGE_LIMIT,
    MESSAGE_LINK_LABEL,
    PRODUCT_LINK_TEXT,
)
from header import build_header_line, header_price_text, PRICE_MISSING_MARK
from producttitle import _is_coupon_announcement, _search_query, is_product_link
from quotes import strip_quoted_detected_lines
from sanitize import (
    _as_message,
    button_link,
    extract_links,
    LINK_APPENDIX_LIMIT,
    link_appendix_lines,
    message_text,
    sanitize_message,
    utf16_length,
)
from smarttitle import _webpage_hints, smart_header_info

log = logging.getLogger("telegram-filter")


# Birçok indirim kanalı "içeriği koru" (noforwards) ayarını açar. O kanallarda
# forward CHAT_FORWARDS_RESTRICTED hatası verir, copy de aynı şekilde patlayabilir.
# Bu yüzden tek bir yol denemiyoruz: sırayla dene, hata alırsan bir sonrakine geç.
DELIVERY_MODES = ("forward", "copy", "media", "text", "link")


def build_delivery_chain(config: dict) -> list[str]:
    """Denenecek iletim yollarının sırasını kur.

    `delivery_modes` açıkça verilmişse o sırayı kullanır; verilmediyse eski
    `copy_mode` alanından türetir ve geri kalan yolları yedek olarak ekler.
    """
    explicit = config.get("delivery_modes")
    if explicit:
        chain = [str(m).strip().lower() for m in explicit if str(m).strip()]
    else:
        preferred = str(config.get("copy_mode", "forward")).strip().lower()
        chain = [preferred] if preferred in DELIVERY_MODES else ["forward"]
    # Bilinmeyen isimleri at, tekrar edenleri koruyarak temizle, yedekleri ekle.
    seen: list[str] = []
    for mode in chain:
        if mode in DELIVERY_MODES and mode not in seen:
            seen.append(mode)
        elif mode not in DELIVERY_MODES:
            log.warning("Bilinmeyen delivery_mode %r yok sayıldı (geçerli: %s).", mode, ", ".join(DELIVERY_MODES))
    for fallback in DELIVERY_MODES:
        if fallback not in seen:
            seen.append(fallback)
    return seen


def build_message_link(event: Any, source: dict | None = None) -> str | None:
    """Mesajın t.me bağlantısını kur; kurulamıyorsa None döner."""
    message_id = getattr(event, "id", None) or getattr(getattr(event, "message", None), "id", None)
    if not message_id:
        return None
    username = (source or {}).get("username") or getattr(getattr(event, "chat", None), "username", None)
    if not username and source:
        # ID ile çözülen özel süpergruplarda entity.username boş olabilir; ama
        # config'de istenen değer hâlâ herkese açık @kullanıcıadı'dır. Link
        # üretilemezse note_dedup_source adı linksiz yazar ve rozette o kaynak
        # tıklanamaz (kullanıcı bildirimi: "orijinal link gömülmemiş").
        requested = str(source.get("requested") or "").strip()
        if requested.startswith("@"):
            username = requested.lstrip("@")
    if username:
        return f"https://t.me/{str(username).lstrip('@')}/{message_id}"
    chat_id = getattr(event, "chat_id", None)
    # t.me/c/<id>/<mesaj> yalnızca kanal/süpergrup için çalışır (-100... ile başlar).
    if isinstance(chat_id, int) and chat_id < -1000000000000:
        return f"https://t.me/c/{abs(chat_id) - 1000000000000}/{message_id}"
    return None


def product_links(obj: Any, limit: int = LINK_APPENDIX_LIMIT) -> list[dict[str, str]]:
    """Bildirimde ``👉 Ürün Linki 👈`` olarak verilecek bağlantılar (kaynak sırasıyla).

    Kaynak şablonları farklıdır; ürün adresi butonda, yazının altına gizli
    hyperlink'te, görünür URL'de ya da yalnız önizlemede durabilir. Buton,
    kaynağın bilinçli "ürüne git" çağrısıdır: ürüne gidebilen HER buton listelenir
    (aynı adres gövdede gizli link olsa da). Buton yoksa mesajdaki İLK ürün
    bağlantısı tek satır olur; alıntıdaki diğer bağlantılar yerinde, yedek olarak
    kalır. Arama servisleri ve sohbet/sosyal ağ adresleri ürün linki sayılmaz.
    """
    links = [item for item in extract_links(obj) if is_product_link(item["url"])]
    markup = getattr(_as_message(obj), "reply_markup", None)
    button_urls = {
        info[0].rstrip("/").lower()
        for row in getattr(markup, "rows", None) or []
        for button in getattr(row, "buttons", None) or []
        if (info := button_link(button))
    }
    buttons = [item for item in links if item["url"].rstrip("/").lower() in button_urls]
    return buttons[:limit] if buttons else links[:1]


def _compose_resolve_header(
    obj: Any, cleaned_obj: Any, source_body: str, fallback_title: str,
) -> tuple[str, str, str, bool]:
    """Üst satır (fiyat + ürün adı) ve alıntı işaretini SMART_HEADER moduna göre kur."""
    product_title = fallback_title
    price_text = ""
    quote_body = False
    if appstate.SMART_HEADER:
        smart_title, smart_price = smart_header_info(obj, cleaned_obj, source_body)
        multiple_products = smart_price.reason == "birden fazla ürün bloğu: seçim belirsiz"
        product_title = smart_title or (
            ("Birden fazla ürün" if multiple_products else "")
            or (product_title if not price_filter.is_generic_title(product_title) else "")
        )
        if smart_price.value is not None and smart_price.confidence in {"high", "medium"}:
            price_text = header_price_text(smart_price.value)
        elif multiple_products and product_title != "Birden fazla ürün":
            price_text = ""  # reliable per-item titles are enough; there is no single bundle price
        elif multiple_products:
            price_text = PRICE_MISSING_MARK
        elif product_title and _is_coupon_announcement(source_body):
            # Kullanıcı isteği: "indirim kuponu bildirimlerinde de [soru işareti]
            # koymayalım". Kupon/kampanya duyurusunda ürün fiyatı YOKTUR; bu bir
            # eksiklik değil, iletinin türüdür. `❓` yazmak bilgi vermiyordu.
            # "Fiyatı olan gönderiyi kupon sanıp çöpe atma" koruması artık dal
            # sırasına bağlı değil: `_is_coupon_announcement` güvenli fiyat varsa
            # kendi içinde False döner (bkz. `_has_safe_price`). Yani buraya
            # yalnız gerçekten fiyatsız duyurular gelir.
            price_text = ""
        else:
            price_text = PRICE_MISSING_MARK
        quote_body = bool(source_body.strip())
    if price_text == PRICE_MISSING_MARK and not product_title:
        price_text = ""
    header_text = build_header_line(price_text, product_title)
    return header_text, price_text, product_title, quote_body


def _compose_build_appendix(
    cleaned_obj: Any, source_body: str, link_kinds: Sequence[str] | None, hidden_links: bool = True,
    has_keyboard: bool = False, product_limit: int = LINK_APPENDIX_LIMIT,
) -> tuple[list[tuple[str, int]], list[tuple[int, int, str]], list[str]]:
    """Ek satırlar: önce ``👉 Ürün Linki 👈``, sonra başka türlü taşınamayan linkler.

    İlk değer ``(satır, öncelik)`` listesidir (bkz. ``APPENDIX_PRODUCT`` …); sınıra
    sığdırma ``_compose_fit_appendix`` içindedir. İkinci değer ürün satırlarının ek
    içindeki ``(UTF-16 başlangıç, uzunluk, URL)`` konumlarıdır; URL metne yazılmaz, bu
    aralığa gizli hyperlink olarak gömülür. ``hidden_links=False`` (biçimsiz son çare
    iletisi) URL'yi satıra açıkça yazar. ``link_appendix: all`` (gizli hyperlink'ler de
    listelenir) ham URL dökümünü, ``off`` ise eksiz düzeni aynen korur; ikisinde de
    ürün satırı üretilmez.

    En çok ``product_limit`` ürün satırı ve ayrıca en çok ``LINK_APPENDIX_LIMIT`` ``🔗``
    satırı yazılır. Satır alamayan ürün butonu ``has_keyboard`` (bot bildirimi) ise klavyede
    düğme olarak kalır; klavyesiz hesap kopyasında ``🔗 etiket: adres`` satırı olur.

    Üçüncü değer yalın ektir: ürün satırı ve eleme olmasaydı yazılacak ``🔗`` satırları
    (mesajın ilk ``LINK_APPENDIX_LIMIT`` gizli bağlantısı ve etiketi silinen linkler).
    Karakter sınırında ölçü olarak kullanılır (bkz. ``_compose_fit_appendix``).
    """
    candidates = (product_links(cleaned_obj, limit=MAX_INLINE_KEYBOARD_BUTTONS)
                  if link_kinds and "entity" not in link_kinds else [])
    products = candidates[:max(0, min(LINK_APPENDIX_LIMIT, product_limit))]
    lines: list[tuple[str, int]] = []
    spans: list[tuple[int, int, str]] = []
    cursor = 0
    for number, item in enumerate(products, 1):
        label = PRODUCT_LINK_TEXT if len(products) == 1 else PRODUCT_LINK_TEXT.replace("Linki", f"Linki {number}")
        if hidden_links:
            spans.append((cursor, utf16_length(label), item["url"]))
        else:
            label = f"{label}: {item['url']}"
        lines.append((label, APPENDIX_PRODUCT))
        cursor += utf16_length(label) + 1

    def extend(found: Sequence[tuple[str, bool]]) -> None:
        # Aynı satır iki gruptan da gelebilir (korunan link ``all`` modunda ana grupta
        # da görünür); tek kez yazılır ve en yüksek önceliği alır.
        written = {text: index for index, (text, _) in enumerate(lines)}
        for text, required in found:
            rank = APPENDIX_REQUIRED if required else APPENDIX_SPARE
            if text not in written:
                written[text] = len(lines)
                lines.append((text, rank))
            elif lines[written[text]][1] < rank:
                lines[written[text]] = (text, rank)

    plain: list[str] = []
    if link_kinds:
        extend(link_appendix_lines(
            cleaned_obj, link_kinds, has_keyboard=has_keyboard,
            exclude_urls=[item["url"] for item in (candidates if has_keyboard else products)],
        ))
        plain.extend(text for text, _ in link_appendix_lines(cleaned_obj, link_kinds))
    carried = getattr(cleaned_obj, "preserved_links", None) or []
    if carried:
        carried_only = SimpleNamespace(
            message=source_body, entities=[], reply_markup=None, media=None, preserved_links=carried,
        )
        extend(link_appendix_lines(
            carried_only, ("webpage",), exclude_urls=[item["url"] for item in products],
        ))
        plain.extend(text for text, _ in link_appendix_lines(carried_only, ("webpage",)) if text not in plain)
    return lines, spans, plain


def compose_message(
    obj: Any,
    *,
    limit: int = MESSAGE_LIMIT,
    link_kinds: Sequence[str] | None = ("button", "webpage"),
    message_link: str | None = None,
    message_link_label: str = MESSAGE_LINK_LABEL,
    source_name: str | None = None,
    hidden_links: bool = True,
    has_keyboard: bool = False,
    source_name_url: str | None = None,
) -> dict[str, Any]:
    """Basit bildirim düzenini kur (kullanıcı istekleri):

    1. en üstte TEK SATIR üst başlık (kalın) — ``smart_header`` açıksa önce
       fiyat, sonra ``—``, sonra ürün adı (``₺56 — Penti Kız Çocuk ...``);
       fiyat güvenle bulunamadığında fiyat yerine ``❓`` yazılır
       (``❓ — Penti Kız Çocuk ...``). Kapalıysa yalnız ürün adı gelir.
       Fiyatın başta olması, bildirimin kesilen ön izlemesinde fiyat ile ürün
       adının ilk kelimelerinin birlikte görülmesini sağlar,
    2. ``👉 Ürün Linki 👈`` — ürün adresi bu yazıya gömülüdür (bkz. ``product_links``);
       alıntıdaki özgün bağlantılar yedek olarak yerinde kalır. Altında, başka türlü
       taşınamayan bağlantılar ``🔗 etiket: adres`` satırı olur (bkz. ``link_appendix_lines``),
    3. altında kaynak mesaj **olduğu gibi** (yalnızca güvenlik temizliği
       uygulanır: reklam/işbirliği etiketleri, WhatsApp/Instagram linkleri,
       Instagram tanıtımları, kanal tanıtımı ve salt hashtag satırları),
    4. en altta kaynak grup adı (kalın) — **orijinal mesajın adresi bu adın
       İÇİNE gömülüdür** (dokununca açılır, URL metinde görünmez). Kullanıcı
       isteği: ayrı ``🔗 Mesajı Gör`` satırı kaldırıldı; mesaj kısaldı ve
       bağlantı yine tek dokunuşla ulaşılabilir.

    Ürün satırı (2) bilerek alıntının ÜSTÜNDEDİR (kullanıcı isteği): fiyat-ad
    satırının hemen altında ürün adresi durur, kaynak mesaj onun altına iner.
    Aynı gerekçe, bildirimin kesilen ön izlemesinde ürün bağlantısının da
    görünmesini sağlar.

    Üst satır YALNIZCA görünen metindir: arama sorguları (Google Alışveriş,
    Akakçe, Cimri) ve tekrar (dedup) anahtarı kaynak mesajdan ayıklanan
    fiyatsız ad üzerinden kurulur; başlığa eklenen fiyat hiçbir aramaya
    taşınmaz (bkz. ``split_header_line`` / ``header_search_title``).

    Tekrarlı paylaşımlarda kaynak grup adı satırı kaldırılır ve yerine
    çoklu paylaşım notu yazılır (``📌 N kere paylaşıldı: <gruplar>``;
    bkz. ``_dedup_apply_badge``). Notta yazan HER grup adı, o grubun kendi
    mesajına bağlanır. Ürün başlığı bulunamayan mesajlarda üst satır yalnızca
    fiyatı taşır; başlık da bulunamadıysa üst satır olmaz, gövde ve alt
    satırlar aynen iletilir.

    Kaynak adı kapanmışsa (``source_footer: false`` ya da ad hiç yoksa)
    orijinal mesajın adresi kaybolmaz: yerine eski ``🔗 Mesajı Gör: <t.me>``
    satırı yazılır (tek başına bu satır, adresin son taşıyıcısıdır).

    ``has_keyboard`` iletinin inline klavyeyle gideceğini söyler (yalnız bot bildirimi;
    kullanıcı hesabı klavye gönderemez). Klavyede duran kaynak butonu satır olmak
    zorunda değildir: arama butonları hiç yazılmaz, diğer buton satırları da yalnız
    gövdeyi kırpmıyorsa yazılır (bkz. ``_compose_fit_appendix``). Klavyesiz kopyada
    bu satırlar butonun tek taşıyıcısıdır.

    Kaynak mesaj nesnesine hiçbir zaman dokunulmaz; yalnızca kopya yeniden
    kurulur. ``body`` orijinal mesajı, ``content`` en alt blok (kaynak adı)
    hariç her şeyi; ``entities`` ise tüm çıktı için doğru UTF-16 konumlarını
    taşır.
    """
    cleaned_obj = sanitize_message(obj)
    source_body = message_text(cleaned_obj)
    product_title = _search_query(cleaned_obj)

    header_text, price_text, product_title, quote_body = _compose_resolve_header(
        obj, cleaned_obj, source_body, product_title,
    )

    body = source_body
    body_entities = entities_for_text(cleaned_obj, source_body)
    body_obj = cleaned_obj
    if appstate.CLEAN_QUOTE_DUPLICATES and appstate.SMART_HEADER and quote_body:
        # Yalnızca GÖRÜNEN alıntı temizlenir. Başlık, fiyat, tekrar kimliği,
        # arama sorgusu ve ürün linkleri ORİJİNAL (kırpılmamış) mesajdan
        # hesaplanmaya devam eder; doğru tespitler değişmez.
        new_body, new_body_entities = strip_quoted_detected_lines(
            source_body, body_entities, product_title, _webpage_hints(obj),
        )
        if new_body != source_body:
            body, body_entities = new_body, new_body_entities
            body_obj = copy.copy(cleaned_obj)
            body_obj.message = body
            body_obj.raw_text = body
            body_obj.entities = body_entities

    name = (source_name or "").strip() or None
    # Kaynak adı varsa orijinal mesajın adresi ADIN İÇİNE gömülür; ayrı satır
    # yazılmaz (kullanıcı isteği). Ad yoksa adresin tek taşıyıcısı eski
    # ``🔗 Mesajı Gör`` satırıdır, o yüzden yalnız o durumda yazılır.
    # Çoklu paylaşım notunda adlar kendi paylaşımlarına bağlandığı için ilk
    # bildirimin adı da ona gönderilen adrese bağlanır (``source_name_url``).
    name_url = (source_name_url or message_link) if name else None
    source_line = (
        f"🔗 {message_link_label}: {message_link}" if (message_link and not name_url) else ""
    )
    sep = "\n\n"
    # ÖLÇÜ BÜTÇESİ SABİT: ek satırları, eskiden ``🔗 Mesajı Gör`` satırının durduğu
    # yeri de hesaba katar. Mesaj linki artık ada gömülü olduğu için o satır
    # metinden kalktı; bütçe de birlikte büyüseydi fazladan ``🔗`` satırları
    # gövdenin sonundan yer yerdi (gövde bir satır uzun kalabildiği için ürün
    # satırı artık "sığmıyor" görünüp düşerdi). Bütçeyi sabit tutmak iki şeyi
    # garanti eder: hiçbir bağlantı yeni yer işgali yüzünden kapanmaz ve ürün
    # satırları eskisi gibi yazılmaya devam eder. Kullanıcı isteği: bildirim
    # yalnız KISALSIN, başka hiçbir şey değişmesin.
    # ``real_tail`` gerçek metindir (mesaj linki adın içinde, ayrı satır yok);
    # ``measure_tail`` ise o satırın bütçesini de taşır.
    measure_tail = (source_line or (f"🔗 {message_link_label}: {message_link}" if message_link else ""),
                    name or "")
    appendix_text, appendix_links = _compose_fit_appendix(
        lambda product_limit: _compose_build_appendix(
            cleaned_obj, source_body, link_kinds, hidden_links, has_keyboard, product_limit),
        body_obj, header_text, body, measure_tail, limit, sep,
        real_tail=(source_line, name or ""),
    )

    segments: list[tuple[str, str]] = [
        ("header", header_text),
        ("appendix", appendix_text),
        ("body", body),
        ("source", source_line),
        ("name", name or ""),
    ]

    segments, appendix_text, name, source_line = _compose_drop_overflow_parts(
        segments, appendix_text, name, source_line, limit, sep,
    )
    if not name:
        # Kaynak adı düştüyse gömülü adres de düşerdi: adresin son taşıyıcısı
        # olarak ``Mesajı Gör`` satırını geri getir (metin ve entity tutarlı).
        source_line = f"🔗 {message_link_label}: {message_link}" if message_link else ""
        name_url = None
        segments = [(kind, source_line if kind == "source" else value) for kind, value in segments]
        segments, appendix_text, name, source_line = _compose_drop_overflow_parts(
            segments, appendix_text, name, source_line, limit, sep,
        )
        if not source_line:
            name_url = None
    segments, header_text, price_text, product_title = _compose_truncate_header(
        segments, header_text, price_text, product_title, limit, sep,
    )
    segments, body = _compose_truncate_body(segments, limit, sep)

    text, content, offsets, body_offset, name_offset = _compose_assemble_text(segments, sep)
    content_entities, name_entities = _compose_build_entities(
        header_text, body, body_entities, name, quote_body, offsets, body_offset, name_offset,
        appendix_links if appendix_text else (), name_url,
    )

    composed_message = SimpleNamespace(
        message=content,
        raw_text=content,
        entities=list(content_entities),
    )
    return {
        "text": text,
        "body": body,
        "content": content,
        "appendix": appendix_text,
        "source_line": source_line,
        "source_url": message_link if (source_line or name_url) else None,
        "source_name": name,
        "source_name_offset": name_offset,
        "source_name_length": utf16_length(name) if (name_offset >= 0 and name) else 0,
        "source_name_url": name_url if name_offset >= 0 else None,
        "source_name_entities": name_entities,
        "header": header_text,
        "price": price_text,
        "product_title": product_title or None,
        "title": product_title,
        "body_offset": body_offset,
        "entities": [*content_entities, *name_entities],
        "message": composed_message,
    }
