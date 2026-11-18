"""Anahtar kelime eşleştirme (Türkçe büyük/küçük harf normalizasyonu dahil)."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from confutil import config_flag
from typing import Any


# ---------------------------------------------------------------------------
# Anahtar kelime eşleştirme
# ---------------------------------------------------------------------------

# Python'un casefold()'u "İ" harfini "i" + birleşik nokta (U+0307) yapar, yani
# "İNDİRİM".casefold() == "i̇ndi̇ri̇m" olur ve "indirim" ile EŞLEŞMEZ.
# Türkçe büyük harfleri önce kendimiz indirgiyoruz.
_TURKISH_CASE_MAP = str.maketrans({"İ": "i", "I": "ı"})


def normalize(text: str | None) -> str:
    """Türkçe duyarlı küçük harf normalizasyonu."""
    return (text or "").translate(_TURKISH_CASE_MAP).casefold()


# Eşleştirme modları.
#   any         = include_keywords'ten en az biri geçsin
#   all         = include_keywords'ün hepsi geçsin
#   forward_all = filtre KAPALI; kaynaklardaki her mesaj iletilir
#                 (exclude_keywords bu modda da engellemeye devam eder)
# Kullanıcının yazabileceği alternatif yazımlar → kurallı mod adı.
MATCH_MODE_ALIASES = {
    # --- any: kelimelerden biri yeterli
    "any": "any", "or": "any", "veya": "any", "herhangi": "any",
    "herhangibiri": "any", "biri": "any", "birisi": "any", "yada": "any",
    # --- all: kelimelerin hepsi zorunlu
    "all": "all", "and": "all", "ve": "all", "hepsi": "all", "tumu": "all",
    "tümü": "all", "tumu_var": "all", "hepside": "all", "hepsi_olsun": "all",
    # --- forward_all: tüm mesajları ilet
    "forward_all": "forward_all", "forwardall": "forward_all",
    "all_messages": "forward_all", "allmessages": "forward_all",
    "allmsgs": "forward_all", "all_msgs": "forward_all",
    "tum_mesajlar": "forward_all", "tüm_mesajlar": "forward_all",
    "tummesajlar": "forward_all", "tum_mesaj": "forward_all",
    "hepsini_gonder": "forward_all", "hepsini_gönder": "forward_all",
    "hepsini_ilet": "forward_all", "hepsiniyolla": "forward_all",
    "tumunu_gonder": "forward_all", "tümünü_gönder": "forward_all",
    "hepsi_gelsin": "forward_all", "hersini_gonder": "forward_all",
    "passthrough": "forward_all", "nofilter": "forward_all",
    "filtresiz": "forward_all", "filtre_yok": "forward_all",
    "filtreyok": "forward_all", "filtreleme": "forward_all",
    "*": "forward_all", "hepsi_gonder": "forward_all",
}


def choice_keys(text: Any) -> tuple[str, ...]:
    """Seçim değerinin olası yazımları: boşluk/tire/büyük-küçük farkını yok sayar."""
    base = normalize(str(text or "")).strip()
    return (base.replace(" ", "_").replace("-", "_"),
            base.replace(" ", "").replace("-", ""))


def canonical_match_mode(value: Any) -> str | None:
    """``match_mode`` yazımını kurallı hâle getir; tanınmıyorsa ``None``."""
    for key in choice_keys(value):
        mode = MATCH_MODE_ALIASES.get(key)
        if mode:
            return mode
    return None


def match_mode_of(config: dict) -> str:
    """config'ten kurallı eşleştirme modunu oku (varsayılan ``any``)."""
    return canonical_match_mode(config.get("match_mode")) or "any"


def exclusion_normalize(text: str | None) -> str:
    """Brand spelling variants: L’Oréal / L'Oreal / Loreal, without joining words."""
    text = unicodedata.normalize("NFKD", normalize(text))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(re.sub(r"['’‘`ʼ]", "", text).split())


def matches(
    text: str | None,
    include: Sequence[str],
    exclude: Sequence[str],
    mode: str = "any",
    *,
    include_enabled: bool = True,
    exclude_enabled: bool = True,
) -> bool:
    """Mesaj metnini anahtar kelimelere göre değerlendir.

    İki filtre birbirinden bağımsızdır (/tgfiltreac ve /tgfiltrekapat ile yönetilir):
      * ``exclude_enabled`` açıkken ``exclude`` kelimelerinden biri geçen mesaj
        engellenir (varsayılan davranış).
      * ``include_enabled`` kapalıysa ``include`` listesi tamamen yok sayılır;
        yalnızca harici filtre uygulanır.
      * İkisi de kapalıysa kaynaklardaki her mesaj iletilir.

    Eski ``match_mode: forward_all`` ayarı da dahili filtreyi kapatır (geriye
    dönük uyumluluk).
    """
    normalized = normalize(text)
    excluded_text = exclusion_normalize(text)
    if exclude_enabled and any(
        (word := exclusion_normalize(term)) and word in excluded_text for term in exclude
    ):
        return False
    if not include_enabled:
        return True
    resolved = canonical_match_mode(mode) or "any"
    if resolved == "forward_all":
        return True
    if not include:
        return True
    found = [word in normalized for word in include]
    return all(found) if resolved == "all" else any(found)


def filter_state_of(config: dict) -> dict[str, bool]:
    """İki filtrenin açık/kapalı durumunu config'ten oku.

    ``include_enabled`` / ``exclude_enabled`` asıl anahtarlardır; eski
    ``match_mode: forward_all`` ayarı dahili filtreyi kapalı sayar.
    """
    include_enabled = config_flag(config.get("include_enabled"), True)
    if match_mode_of(config) == "forward_all":
        include_enabled = False
    return {
        "include": include_enabled,
        "exclude": config_flag(config.get("exclude_enabled"), True),
    }
