"""Alıntıda tespit edilen satırları temizleme (saf)."""

from __future__ import annotations

import price_filter
import re
from collections.abc import Sequence
from keywords import normalize
from producttitle import _coupon_codes
from sanitize import (
    URL_RE,
    _blank_line_spans,
    _merge_spans,
    _remap_entities,
    _remove_spans,
    _utf16_to_index,
)
from typing import Any


# --- Alıntıda TESPİT EDİLEN satırların silinmesi ----------------------------
# Kullanıcı isteği ("ilk adım"): üst satırda zaten yazan ürün adı ile tespit
# edilen tek başına kupon kodu satırı alıntıda tekrar durmasın. "Garanti"
# şartı: yalnızca emin olunan satır silinir; kuşkulu satır AYNEN kalır.
_QUOTE_CODE_LINE_RE = re.compile(r"[A-Z0-9İĞÜŞÖÇ]{4,}")
_QUOTE_LINK_ENTITY_KINDS = frozenset({
    "MessageEntityUrl", "MessageEntityMention", "MessageEntityMentionName",
    "MessageEntityTextMention", "MessageEntityHashtag", "MessageEntityCashtag",
    "MessageEntityEmail", "MessageEntityPhone", "MessageEntityBotCommand",
    "MessageEntityBankCard",
})


def quoted_code_lines(text: str) -> set[str]:
    """Tek başına duran ve ``_coupon_codes`` ile DOĞRULANMIŞ kod satırları.

    ``TERTEMİZ75`` gibi bağlamı olmayan tek satırlar ``_coupon_codes`` tarafından
    tanınmıyorsa "tespit edemedik" sayılır ve SİLİNMEZ (kullanıcı şartı).
    """
    codes = {normalize(code) for code in _coupon_codes(text or "")}
    if not codes:
        return set()
    return {
        line for line in (text or "").splitlines()
        if _QUOTE_CODE_LINE_RE.fullmatch(line.strip())
        and normalize(line.strip()) in codes
    }


def _line_has_link(text: str, start: int, end: int, entities: Sequence[Any]) -> bool:
    """Satır tıklanabilir bir bağlantı/etiket taşıyor mu? Öyleyse silinmez."""
    raw = text[start:end]
    if URL_RE.search(raw) or "@" in raw or "#" in raw:
        return True
    for entity in entities:
        if isinstance(entity, dict):
            url = entity.get("url")
            kind = entity.get("type")
            offset, length = entity.get("offset", 0), entity.get("length", 0)
        else:
            url = getattr(entity, "url", None)
            kind = type(entity).__name__
            offset, length = getattr(entity, "offset", 0), getattr(entity, "length", 0)
        if not (url or kind in _QUOTE_LINK_ENTITY_KINDS):
            continue
        try:
            e_start = _utf16_to_index(text, int(offset or 0))
            e_end = _utf16_to_index(text, int(offset or 0) + int(length or 0))
        except (TypeError, ValueError):
            continue
        if e_start < end and e_end > start:
            return True
    return False


def quoted_detected_line_spans(
    text: str, entities: Sequence[Any], product_title: str, hints: Sequence[str] = (),
) -> list[tuple[int, int]]:
    """Alıntıdan silinecek satırların karakter aralıkları (satır sonu hariç)."""
    if not (text or "").strip() or not (product_title or "").strip():
        return []
    wanted: set[str] = set()
    picked = price_filter.title_source_line(text, hints)
    # Yalnızca ÜST SATIRDA yazan adın kaynak satırı silinir: kampanya başlığı,
    # linkten gelen ad ya da farklı bir başlık söz konusuysa dokunulmaz.
    if picked and normalize(picked[2]) == normalize(product_title):
        wanted.add(picked[1])
    wanted.update(quoted_code_lines(text))
    if not wanted:
        return []
    spans = [
        (match.start(), match.end())
        for match in re.finditer(r"[^\n]+", text)
        if match.group(0) in wanted and not _line_has_link(text, match.start(), match.end(), entities)
    ]
    if not spans or not any(char.isalnum() for char in _remove_spans(text, spans)):
        return []  # alıntı boşalacaksa hiçbir satır silinmez
    return spans


def strip_quoted_detected_lines(
    text: str, entities: Sequence[Any], product_title: str, hints: Sequence[str] = (),
) -> tuple[str, list[Any]]:
    """Tespit edilen ad/kod satırlarını alıntıdan çıkar; entity'leri UTF-16 ile taşı.

    Silme aralıkları karakter indeksidir; ``_remap_entities`` ve
    ``_blank_line_spans`` temizlikte kullanılan güvenli yolu yeniden kullanır:
    gizli hyperlink'ler kendi etiketlerinde kalır, artan boş satırlar sıkışır.
    """
    spans = quoted_detected_line_spans(text, entities, product_title, hints)
    if not spans:
        return text, list(entities)
    merged = _merge_spans(spans)
    cleaned = _remove_spans(text, merged)
    remapped = _remap_entities(text, cleaned, entities, merged)
    blank = _blank_line_spans(cleaned)
    if blank:
        compact = _remove_spans(cleaned, blank)
        remapped = _remap_entities(cleaned, compact, remapped, blank)
        cleaned = compact
    return cleaned, remapped
