"""Akıllı başlık: web sayfası ipuçları ve çoklu ürün başlığı (saf)."""

from __future__ import annotations

import logging
import price_filter
import re
from botentities import entities_for_text
from collections.abc import Sequence
from constants import MESSAGE_LIMIT
from header import split_header_line
from keywords import normalize
from producttitle import (
    _campaign_title_if_clipped,
    _coupon_campaign_title,
    _coupon_dedup_identity,
    _coupon_preview_message,
    _is_coupon_teaser_title,
    _search_query,
    linked_product_title,
)
from sanitize import WORD_TOKEN_RE, message_text, sanitize_message
from typing import Any

log = logging.getLogger("telegram-filter")


def _webpage_hints(obj: Any) -> list[str]:
    """Mesajdaki link önizlemesinin başlığı (varsa) başlık seçimine ipucu olur."""
    hints: list[str] = []
    try:
        media = getattr(obj, "media", None) or getattr(getattr(obj, "message", None), "media", None)
        webpage = getattr(media, "webpage", None)
        for attr in ("title", "description"):
            value = getattr(webpage, attr, None)
            if isinstance(value, str) and value.strip():
                hints.append(value.strip())
    except Exception:  # noqa: BLE001 - ipucu yoksa sorun değil
        return []
    return hints


def _multi_product_titles(text: str) -> list[str]:
    """Explicitly numbered/listed product blocks yield separate title identities."""
    blocks = price_filter._numbered_product_blocks(text or "")
    if len(blocks) < 2:
        return []
    titles = []
    for _, block in blocks:
        title = price_filter.extract_title(block, limit=MESSAGE_LIMIT)
        if (title and not price_filter.is_generic_title(title)
                and len(WORD_TOKEN_RE.findall(title)) >= 3
                and not re.search(r"\b(?:ürün|urun)\b", normalize(title))):
            titles.append(title)
    return titles if len(titles) >= 2 else []


def _multi_product_display_title(titles: Sequence[str]) -> str:
    """Keep a compact, readable five-word label for each product in the header."""
    return " — ".join(" ".join(title.split()[:5]) for title in titles)


def smart_header_info(obj: Any, cleaned_obj: Any, body: str) -> tuple[str, "price_filter.PriceInfo"]:
    """Temizlenmiş mesajdan (başlık, fiyat) üret; hata olursa boş döner."""
    try:
        entities = entities_for_text(cleaned_obj, body)
        strike = price_filter.strike_spans_from_entities(body, entities)
        title, info = price_filter.analyze(body, strike, _webpage_hints(obj))
        linked = linked_product_title(cleaned_obj)
        if info.reason == "birden fazla ürün bloğu: seçim belirsiz":
            product_titles = _multi_product_titles(body)
            if product_titles:
                return _multi_product_display_title(product_titles), info
        campaign_title = _campaign_title_if_clipped(
            body, title, MESSAGE_LIMIT, allow_code_only_marker=True,
        )
        full_campaign_title = _coupon_campaign_title(
            body, MESSAGE_LIMIT, include_code=True,
            allow_code_only_marker=True,
        )
        # A card-code/category teaser is not a product name. Multi-tier coupon
        # tables likewise need the campaign heading, never one arbitrary tier.
        coupon_preview = bool(
            full_campaign_title and (
                _is_coupon_teaser_title(title)
                or _coupon_preview_message(body, full_campaign_title)
            )
        )
        return linked or (full_campaign_title if coupon_preview else campaign_title) or title, info
    except Exception as exc:  # noqa: BLE001 - başlık/fiyat hatası bildirimi engellemesin
        log.warning("Başlık/fiyat tespiti başarısız (%s): %s", type(exc).__name__, exc)
        return "", price_filter.PriceInfo(None, "", "none", "hata")


def smart_dedup_title(event: Any) -> str:
    """Yeni düzende tekrar anahtarı için kullanılan başlık (FİYATSIZ).

    Kaynak mesajdan ayıklanır; bildirim üst satırına yazılan fiyat buraya
    giremez, çünkü üst satır yalnızca çıktı metnidir. Böylece "fiyat - ad"
    düzenine geçmek tekrar (dedup) anahtarlarını değiştirmez.
    """
    cleaned = sanitize_message(event)
    text = message_text(cleaned)
    linked = linked_product_title(cleaned, limit=MESSAGE_LIMIT)
    if linked:
        return linked
    campaign_identity = _coupon_dedup_identity(text)
    if campaign_identity:
        return campaign_identity
    product_titles = _multi_product_titles(text)
    if product_titles:
        return " / ".join(product_titles)[:MESSAGE_LIMIT]
    campaign_title = _campaign_title_if_clipped(
        text,
        price_filter.extract_title(text, _webpage_hints(event), limit=MESSAGE_LIMIT),
        MESSAGE_LIMIT,
        include_code=True,
        allow_code_only_marker=True,
    )
    if campaign_title:
        return campaign_title
    # If the source's first line already contains "price - product name",
    # keep that product name; otherwise a following Prime/shipping line can be
    # mistaken for the title by generic title extraction.
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    header_price, header_title = split_header_line(first_line)
    if header_price and header_title and not price_filter.is_generic_title(header_title):
        return header_title
    first_title, first_price = price_filter.analyze(first_line)
    if first_price.value is not None and first_price.confidence in {"high", "medium"} and first_title:
        return first_title
    # Display headers may be truncated; dedup must retain model/size suffixes.
    title = price_filter.extract_title(text, _webpage_hints(event), limit=MESSAGE_LIMIT)
    fallback = _search_query(cleaned, limit=MESSAGE_LIMIT)
    return title or (fallback if not price_filter.is_generic_title(fallback) else "")
