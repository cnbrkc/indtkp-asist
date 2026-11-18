"""Pure decision engine for the personal Telegram offer filters.

This module deliberately has no Telegram client or config-store dependency.
It evaluates the legacy whole-message keyword rules, shared exclusions, and
independent product-title/real-price rules using the existing ``price_filter``
parser. Price and title extraction are each performed at most once per decision.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Sequence

import price_filter
from private_bot import normalize_keyword

DM_PRICE_FIELD = "dm_price_max"
DM_PRODUCT_PRICE_FIELD = "dm_product_price_rules"
MAX_RULE_KEYWORD_LENGTH = 100
MAX_RULE_PRICE = 100_000_000


@dataclass(frozen=True)
class ProductPriceRule:
    """Canonical product-title substring and inclusive maximum price."""

    keyword: str
    threshold: float
    canonical: str

    @property
    def identity(self) -> tuple[str, float]:
        return self.keyword, self.threshold


@dataclass(frozen=True)
class FilterResult:
    """One explainable rule result; for ``dmharic``, matched means it blocks."""

    group: str
    matched: bool
    reason: str
    rule: str | None = None


@dataclass(frozen=True)
class OfferFacts:
    """Config-independent parsing reused when a queued offer is revalidated."""

    source_text: str
    strike: tuple[tuple[int, int], ...]
    normalized_text: str
    title: str
    price: price_filter.PriceInfo | None
    title_extracted: bool
    price_extracted: bool


@dataclass(frozen=True)
class DMDecision:
    """Immutable result returned by the side-effect-free decision function."""

    should_send: bool
    title: str
    price: price_filter.PriceInfo | None
    results: tuple[FilterResult, ...]
    facts: OfferFacts

    @property
    def matched_groups(self) -> tuple[str, ...]:
        """Sending groups that matched (excludes the diagnostic blocker group)."""
        return tuple(dict.fromkeys(
            result.group for result in self.results
            if result.matched and result.group != "dmharic"
        ))


def _list_value(config: dict[str, Any], field: str) -> list[Any]:
    value = config.get(field)
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _enabled(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().casefold() in {
        "1", "true", "yes", "evet", "on", "açık", "acik", "aktif",
    }


def _normalized_strings(values: Sequence[Any]) -> tuple[str, ...]:
    return tuple(
        normalized for value in values
        if isinstance(value, str) and value.strip()
        if (normalized := normalize_keyword(value))
    )


def dm_price_threshold(config: dict[str, Any]) -> float | None:
    """Read the legacy single DM maximum-price setting; missing/bad means off."""
    values = _list_value(config, DM_PRICE_FIELD)
    return price_filter.parse_threshold(values[0]) if values else None


def _canonical_amount(value: float) -> str:
    # Preserve every precision accepted by price_filter.parse_threshold. Rounding
    # to cents here could change an entered rule when its canonical form is saved
    # and parsed again (for example 1,2345 becoming 1,23).
    amount = format(Decimal(str(value)).normalize(), "f")
    return amount.replace(".", ",")


def parse_product_price_rule(raw: Any) -> ProductPriceRule:
    """Parse ``product phrase < maximum price`` or raise a user-readable error.

    Threshold parsing is delegated to ``price_filter``; this is not a second
    price parser. A canonical representation makes case/spacing/currency
    variants of the same rule deduplicate reliably.
    """
    text = str(raw if raw is not None else "").strip()
    if text.count("<") != 1 or ">" in text:
        raise ValueError("Biçim 'ürün adı < fiyat' olmalı. Örnek: telefon < 100")
    keyword_raw, threshold_raw = text.split("<", 1)
    keyword = normalize_keyword(keyword_raw)
    if not keyword:
        raise ValueError("Ürün adı/ifadesi boş olamaz.")
    if len(keyword) > MAX_RULE_KEYWORD_LENGTH:
        raise ValueError(f"Ürün adı/ifadesi en fazla {MAX_RULE_KEYWORD_LENGTH} karakter olabilir.")
    threshold = price_filter.parse_threshold(threshold_raw)
    if threshold is None or not 0 <= threshold <= MAX_RULE_PRICE:
        raise ValueError("Fiyat eşiği anlaşılamadı. Örnek: telefon < 100 veya TCL < 30.000 TL")
    canonical = f"{keyword} < {_canonical_amount(threshold)}"
    return ProductPriceRule(keyword, threshold, canonical)


def product_price_rules(config: dict[str, Any]) -> tuple[ProductPriceRule, ...]:
    """Return valid, unique configured rules; tolerate absent/legacy fields."""
    result: list[ProductPriceRule] = []
    seen: set[tuple[str, float]] = set()
    for raw in _list_value(config, DM_PRODUCT_PRICE_FIELD):
        try:
            rule = parse_product_price_rule(raw)
        except (TypeError, ValueError):
            continue
        if rule.identity not in seen:
            result.append(rule)
            seen.add(rule.identity)
    return tuple(result)


def has_active_rules(config: dict[str, Any]) -> bool:
    """Cheap guard used before sanitizing/queuing an offer; decision validates values."""
    include_on = _enabled(config.get("dm_enabled")) and any(
        isinstance(value, str) and value.strip()
        for value in _list_value(config, "dm_keywords")
    )
    has_price_setting = bool(_list_value(config, DM_PRICE_FIELD))
    has_product_setting = any(
        isinstance(value, str) and value.strip()
        for value in _list_value(config, DM_PRODUCT_PRICE_FIELD)
    )
    return include_on or has_price_setting or has_product_setting


def _contains_any(normalized_text: str, phrases: Sequence[str]) -> tuple[str, ...]:
    return tuple(phrase for phrase in phrases if phrase in normalized_text)


def _price_failure(info: price_filter.PriceInfo | None, threshold: float) -> str:
    if info is None or info.value is None:
        detail = info.reason if info is not None else "fiyat tespiti yapılmadı"
        return f"gerçek ürün fiyatı bulunamadı ({detail})"
    if info.confidence not in {"high", "medium"}:
        return f"fiyat güveni yetersiz ({info.reason})"
    return f"{info.text} > {price_filter.format_price(threshold)}"


def evaluate_dm_offer(
    config: dict[str, Any],
    text: str,
    strike: Sequence[tuple[int, int]] = (),
    *,
    facts: OfferFacts | None = None,
) -> DMDecision:
    """Evaluate all four DM groups once and return a reason for every decision.

    ``dmdahil`` keeps its established contiguous-substring matching over the
    complete sanitized message. ``dmharic`` uses that same scope and gates both
    legacy sending rules. Product-price rules instead require a match in the
    extracted product title and an inclusive match against the real price.
    """
    text = text or ""
    strike_key = tuple(tuple(span) for span in (strike or ()))
    if facts is not None and (facts.source_text != text or facts.strike != strike_key):
        facts = None
    normalized_text = facts.normalized_text if facts is not None else normalize_keyword(text)
    include_phrases = _normalized_strings(_list_value(config, "dm_keywords"))
    exclude_phrases = _normalized_strings(_list_value(config, "dm_exclude_keywords"))
    include_on = _enabled(config.get("dm_enabled"))
    threshold = dm_price_threshold(config)
    rules = product_price_rules(config)

    # All price-dependent groups share one extraction, regardless of how many
    # thresholds/rules are configured. Parsing facts from enqueue are reused
    # when the queue worker rechecks current config; missing facts are computed
    # lazily if a different filter was enabled while the offer was queued.
    price_extracted = bool(facts and facts.price_extracted)
    price_info = facts.price if price_extracted and facts is not None else None
    if (threshold is not None or rules) and not price_extracted:
        price_info = price_filter.extract_price(text, strike_key)
        price_extracted = True
    title_extracted = bool(facts and facts.title_extracted)
    title = facts.title if title_extracted and facts is not None else ""
    if rules and not title_extracted:
        title = price_filter.extract_title(text)
        title_extracted = True
    normalized_title = normalize_keyword(title) if title else ""
    excluded = _contains_any(normalized_text, exclude_phrases)
    exclusion_match = bool(excluded)

    results: list[FilterResult] = []
    include_hit = _contains_any(normalized_text, include_phrases)
    if not include_on:
        include_result = FilterResult("dmdahil", False, "DM dahil filtresi kapalı")
    elif not include_phrases:
        include_result = FilterResult("dmdahil", False, "dmdahil listesinde kayıt yok")
    elif not include_hit:
        include_result = FilterResult("dmdahil", False, "dahil ifadesi mesajda bulunamadı")
    elif exclusion_match:
        include_result = FilterResult(
            "dmdahil", False,
            "dmharic engelledi: " + ", ".join(excluded),
        )
    else:
        include_result = FilterResult(
            "dmdahil", True, "dahil ifade eşleşti: " + ", ".join(include_hit),
        )
    results.append(include_result)

    if threshold is None:
        price_result = FilterResult("dmfiyat", False, "dmfiyat eşiği kapalı veya geçersiz")
    elif price_info is None or not price_filter.price_info_matches(price_info, threshold):
        price_result = FilterResult("dmfiyat", False, _price_failure(price_info, threshold))
    elif exclusion_match:
        price_result = FilterResult(
            "dmfiyat", False,
            "dmharic engelledi: " + ", ".join(excluded),
        )
    else:
        price_result = FilterResult(
            "dmfiyat", True,
            f"gerçek ürün fiyatı {price_info.text} ≤ {price_filter.format_price(threshold)}",
        )
    results.append(price_result)

    if not exclude_phrases:
        exclude_result = FilterResult("dmharic", False, "hariç listesi boş; engel yok")
    elif exclusion_match:
        exclude_result = FilterResult(
            "dmharic", True, "hariç ifade eşleşti: " + ", ".join(excluded),
        )
    else:
        exclude_result = FilterResult("dmharic", False, "hariç ifadeler mesajda bulunamadı")
    results.append(exclude_result)

    if not rules:
        title_price_results = [
            FilterResult("dm_urun_fiyat", False, "ürün adı < fiyat kuralı kapalı")
        ]
    elif not title:
        title_price_results = [
            FilterResult("dm_urun_fiyat", False, "ürün başlığı tespit edilemedi", rule.canonical)
            for rule in rules
        ]
    else:
        title_price_results = []
        for rule in rules:
            if rule.keyword not in normalized_title:
                title_price_results.append(FilterResult(
                    "dm_urun_fiyat", False,
                    f"ürün başlığında {rule.keyword!r} bulunamadı", rule.canonical,
                ))
                continue
            if price_info is None or not price_filter.price_info_matches(price_info, rule.threshold):
                title_price_results.append(FilterResult(
                    "dm_urun_fiyat", False,
                    _price_failure(price_info, rule.threshold), rule.canonical,
                ))
                continue
            title_price_results.append(FilterResult(
                "dm_urun_fiyat", True,
                f"başlıkta {rule.keyword!r} var ve gerçek ürün fiyatı "
                f"{price_info.text} ≤ {price_filter.format_price(rule.threshold)}",
                rule.canonical,
            ))
    results.extend(title_price_results)

    should_send = (
        include_result.matched
        or price_result.matched
        or any(result.matched for result in title_price_results)
    )
    parsed_facts = OfferFacts(
        source_text=text,
        strike=strike_key,
        normalized_text=normalized_text,
        title=title,
        price=price_info,
        title_extracted=title_extracted,
        price_extracted=price_extracted,
    )
    return DMDecision(should_send, title, price_info, tuple(results), parsed_facts)
