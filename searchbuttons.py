"""Fiyat satırı etiketi, arama düğmeleri ve satır içi klavye (saf)."""

from __future__ import annotations

import urllib.request
from collections.abc import Sequence
from constants import MAX_INLINE_KEYBOARD_BUTTONS, PRICE_SEARCH_BUTTONS
from header import header_search_title
from producttitle import _search_query
from sanitize import (
    _LEADING_NOISE_RE,
    _price_search_service,
    button_link,
    clean_url,
    extract_links,
    sanitize_message,
    visible_urls,
)
from typing import Any


PRICE_LINE_LABEL = "Fiyat:"
# Eski biçimin link etiketi; yalnızca geriye dönük tanıma için tutulur
# (eski bildirimlerin tekrar önbelleği hâlâ okunabilir).
PRODUCT_LINK_LABEL = "🔗 Ürün fırsat linki:"


def is_price_line(line: str | None) -> bool:
    """Satır bir fiyat satırı mı? Baştaki emoji/sembol (``💰``) hoş görülür.

    Yalnızca geriye dönük tanıma için tutulur: eski bildirimlerdeki
    ``💰Fiyat: …`` satırları ve rozet konumları bu sayede okunabilir kalır.
    Yeni bildirimlerde ayrı bir fiyat satırı yoktur; fiyat, ürün adıyla birlikte
    üst satıra emojisiz yazılır (``₺56 — Ürün adı``; bkz. ``build_header_line``)
    ve mesajın gövdesi olduğu gibi iletilir.
    """
    content = _LEADING_NOISE_RE.sub("", (line or "").strip())
    return content.startswith(PRICE_LINE_LABEL)


# Cimri sorgusu KISALTILMAZ: ürün başlığının tamamı aranır. Daha önce uzun
# sorguların Cloudflare WAF'ına takıldığı sanılıyordu ve sorgu ilk iki
# kelimeye iniyordu; denemede sorunun Telegram'ın dahili tarayıcısında olduğu,
# aynı adres dış tarayıcıda açılınca tam sorguyla arama yaptığı görüldü
# (kullanıcı isteği: "ilk iki kelimeyi aramayı bıraksın, tüm ürün başlığını
# arasın"). Google Alışveriş ve Akakçe zaten tam sorgu kullanıyordu.


def _cimri_search_query(query: str) -> str:
    """Cimri sorgusu: ürün başlığının tamamı (yalnızca boşluklar sıkıştırılır)."""
    return " ".join((query or "").split())


def _price_search_url(service: str, query: str) -> str:
    """Ürün arama adresini kur (query başlık ayıklayıcısından gelir).

    ``header_search_title`` çağrısının tek nedeni bildirim ÜST SATIRININ
    (``₺56 — Ad``, ``❓ — Ad``) arama sorgusuna karışmasını imkânsız
    kılmaktır: tutar, para birimi, ayraç ve ``❓`` burada her koşulda düşer,
    servise salt ürün adı gider.
    """
    query = header_search_title(query)
    if service == "akakce":
        return "https://www.akakce.com/arama/?" + urllib.parse.urlencode({"q": query})
    if service == "google_shopping":
        params = urllib.parse.urlencode((
            ("udm", "28"), ("q", query), ("hl", "tr"), ("gl", "tr"),
        ))
        return "https://www.google.com/search?" + params
    if service == "cimri":
        # Cimri'nin arama sonuç sayfası bu yapıyı kullanıyor. Fiyat artan
        # sıralaması en ucuz teklifi üste taşır; virgül URL'de sabit bırakılır.
        short = _cimri_search_query(query) or query
        return "https://www.cimri.com/arama?sort=price,asc&q=" + urllib.parse.quote_plus(short)
    raise ValueError(f"Bilinmeyen fiyat arama hizmeti: {service}")


def build_inline_keyboard(obj: Any, *, body_text: str | None = None,
                          body_entities: Sequence[dict[str, Any]] = ()) -> dict | None:
    """Remove a source button only when its URL survives in the delivered body."""
    body_urls = visible_urls(body_text or "")
    body_urls.update(clean_url(e.get("url")).rstrip("/").lower()
                     for e in body_entities if e.get("type") == "text_link" and e.get("url"))
    message = sanitize_message(obj)
    markup = getattr(message, "reply_markup", None)
    rows: list[list[dict[str, str]]] = []
    search_buttons: dict[str, list[dict[str, str]]] = {
        service: [] for service, _ in PRICE_SEARCH_BUTTONS
    }
    existing_button_count = 0
    for row in getattr(markup, "rows", None) or []:
        buttons: list[dict[str, str]] = []
        for button in getattr(row, "buttons", None) or []:
            info = button_link(button)
            if not info:
                continue
            url, label = info
            item = {"text": (label or url)[:64], "url": url}
            existing_button_count += 1
            service = _price_search_service(url, label or "")
            if not service and body_text is not None and clean_url(url).rstrip("/").lower() in body_urls:
                existing_button_count -= 1
                continue
            if service in search_buttons:
                search_buttons[service].append(item)
            else:
                buttons.append(item)
        # Kaynak butonları kendi sıraları ve satır gruplarıyla korunur.
        if buttons:
            rows.append(buttons)

    present_services = {
        service
        for link in extract_links(message)
        if (service := _price_search_service(link["url"], link.get("label") or ""))
    }
    query = header_search_title(_search_query(message))
    available_slots = max(0, MAX_INLINE_KEYBOARD_BUTTONS - existing_button_count)
    generated = 0
    search_row: list[dict[str, str]] = []
    for service, label in PRICE_SEARCH_BUTTONS:
        # Kaynaktaki bilinen arama butonu korunur, ancak standart sıraya alınır.
        if search_buttons[service]:
            search_row.extend(search_buttons[service])
            continue
        # Gövdedeki/gizli bağlantı aynı hizmeti zaten sunuyorsa çoğaltma.
        if service in present_services or not query or generated >= available_slots:
            continue
        search_row.append({"text": label, "url": _price_search_url(service, query)})
        generated += 1

    if search_row:
        rows.append(search_row)
    return {"inline_keyboard": rows} if rows else None


def _entities_within(entities: Sequence[Any] | None, limit: int) -> list[Any]:
    """UTF-16 sınırını aşan entity'leri at (Telegram taşan entity'yi reddeder)."""
    kept: list[Any] = []
    for entity in entities or []:
        offset = int(getattr(entity, "offset", 0) or 0)
        length = int(getattr(entity, "length", 0) or 0)
        if length <= 0 or offset < 0 or offset + length > limit:
            continue
        kept.append(entity)
    return kept
