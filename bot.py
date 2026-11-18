"""Telegram indirim mesajı filtresi.

Kişisel Telegram hesabı (user client) ile çalışır; bot hesabı kaynak kanalları
okuyamaz. Yapılandırma config.json dosyasından gelir, sırlar ortam
değişkenlerinden gelir.

Önemli tasarım notu: Telethon'daki `events.NewMessage(chats=...)` filtresi chat
listesini *ilk mesaj geldiğinde* çözer ve tek bir chat bile çözülemezse tüm
handler devre dışı kalır (üstelik hata yalnızca "Task exception was never
retrieved" olarak loglanır). Bu yüzden burada bütün chat'ler açılışta tek tek
çözülür, çözülemeyenler atlanır ve handler'lara hazır int ID listesi verilir.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import contextvars
import base64
import copy
import hashlib
import io
import json
import logging
import math
import mimetypes
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from telethon import TelegramClient, errors, events, utils
from telethon.sessions import StringSession
from telethon.tl import functions, types

import price_filter
import dm_filters
import timefmt
from telegram_retry import gate_stats, request_with_retry
from private_bot import (
    BotAPI, BotAPIError, PrivateControlEvent, PrivateOfferQueue,
    edit_dm_keywords, normalize_keyword, poll_health, poll_private_commands,
    private_copy_request,
)

from confutil import (  # noqa: F401 - bot.X uyumluluğu için yeniden dışa aktarım
    BOT_LINK_KIND_GROUPS,
    LINK_KIND_GROUPS,
    chat_values,
    config_flag,
    config_path,
    link_appendix_mode,
    link_kinds_for,
    load_config,
    parse_admin_ids,
    parse_chat_value,
)
from keywords import (  # noqa: F401 - bot.X uyumluluğu için yeniden dışa aktarım
    MATCH_MODE_ALIASES,
    _TURKISH_CASE_MAP,
    canonical_match_mode,
    choice_keys,
    exclusion_normalize,
    filter_state_of,
    match_mode_of,
    matches,
    normalize,
)
from sanitize import (  # noqa: F401 - bot.X uyumluluğu için yeniden dışa aktarım
    CHANNEL_WORD_ROOTS,
    DISCLOSURE_TOKEN_RE,
    HASHTAG_WORD_RE,
    LINK_APPENDIX_LIMIT,
    PROMO_WORD_ROOTS,
    URL_RE,
    WORD_TOKEN_RE,
    _BLANK_RUN_RE,
    _EDGE_BLANK_RE,
    _LEADING_NOISE_RE,
    _URL_TAIL_TRIM,
    _as_message,
    _blank_line_spans,
    _disclosure_spans,
    _index_after_removals,
    _merge_spans,
    _price_search_service,
    _promo_line_spans,
    _remap_entities,
    _remove_spans,
    _sanitize_apply_text_cleaning,
    _sanitize_clean_markup,
    _sanitize_clean_media,
    _sanitize_collect_preserved_links,
    _sanitize_collect_removed_spans,
    _sanitize_dedup_preserved_links,
    _sanitize_filter_entities,
    _utf16_to_index,
    _with_adjacent_space,
    _word_matches,
    build_link_appendix,
    button_link,
    clean_disclosure_tokens,
    clean_url,
    extract_links,
    first_sent_entities,
    first_sent_item,
    first_sent_text,
    is_blank_reply,
    is_instagram_url,
    is_search_link,
    is_whatsapp_url,
    link_appendix_lines,
    message_entities,
    message_text,
    missing_links,
    sanitize_message,
    sent_message_ids,
    utf16_length,
    utf16_slice,
    visible_urls,
)

from constants import (  # noqa: F401 - bot.X uyumluluğu için yeniden dışa aktarım
    APPENDIX_PRODUCT,
    APPENDIX_REQUIRED,
    APPENDIX_SPARE,
    BOT_API_MEDIA_LIMIT_MB,
    CAPTION_LIMIT,
    MAX_INLINE_KEYBOARD_BUTTONS,
    MEDIA_FILE_PATH_RE,
    MESSAGE_LIMIT,
    MESSAGE_LINK_LABEL,
    NON_PRODUCT_HOSTS,
    NON_PRODUCT_HOST_RE,
    PRICE_SEARCH_BUTTONS,
    PRODUCT_LINK_TEXT,
    TRACKING_QUERY_KEYS,
    TRACKING_QUERY_PREFIXES,
)
from textutil import (  # noqa: F401 - bot.X uyumluluğu için yeniden dışa aktarım
    display_name,
    humanize,
)
from analysis import (  # noqa: F401 - bot.X uyumluluğu için yeniden dışa aktarım
    ANALYSIS_STOP_WORDS,
    ANALYSIS_TOKEN_RE,
    DEAL_FILLER_WORDS,
    STOP_WORDS,
    content_tokens,
    message_title,
    title_tokens,
)
from producttitle import (  # noqa: F401 - bot.X uyumluluğu için yeniden dışa aktarım
    SEARCH_QUERY_LIMIT,
    _DOTLESS_I_FOLD,
    _NON_COUPON_CAMPAIGN_RE,
    _SEARCH_AMOUNT,
    _SEARCH_CTA_ONLY_WORDS,
    _SEARCH_CURRENCY,
    _SEARCH_DEAL_STEMS,
    _SEARCH_DISCOUNT_RE,
    _SEARCH_NOISE_WORDS,
    _SEARCH_PRICE_RE,
    _campaign_norm,
    _campaign_title_if_clipped,
    _coupon_campaign_title,
    _coupon_codes,
    _coupon_dedup_identity,
    _coupon_preview_message,
    _has_safe_price,
    _is_coupon_announcement,
    _is_coupon_teaser_title,
    _is_tracking_query,
    _search_candidate_score,
    _search_is_deal_word,
    _search_line_candidate,
    _search_query,
    is_product_link,
    linked_product_title,
)
from searchbuttons import (  # noqa: F401 - bot.X uyumluluğu için yeniden dışa aktarım
    PRICE_LINE_LABEL,
    PRODUCT_LINK_LABEL,
    _cimri_search_query,
    _entities_within,
    _price_search_url,
    build_inline_keyboard,
    is_price_line,
)
from header import (  # noqa: F401 - bot.X uyumluluğu için yeniden dışa aktarım
    HEADER_SEPARATOR,
    LEGACY_HEADER_SEPARATOR,
    PREV_HEADER_SEPARATOR,
    PRICE_CURRENCY_MARK,
    PRICE_LINE_PREFIX,
    PRICE_MISSING_MARK,
    _HEADER_PRICE_RE,
    build_header_line,
    fit_header_line,
    header_price_text,
    header_search_title,
    is_header_price,
    split_header_line,
    utf16_budget,
)
from smarttitle import (  # noqa: F401 - bot.X uyumluluğu için yeniden dışa aktarım
    _multi_product_display_title,
    _multi_product_titles,
    _webpage_hints,
    smart_dedup_title,
    smart_header_info,
)
from quotes import (  # noqa: F401 - bot.X uyumluluğu için yeniden dışa aktarım
    _QUOTE_CODE_LINE_RE,
    _QUOTE_LINK_ENTITY_KINDS,
    _line_has_link,
    quoted_code_lines,
    quoted_detected_line_spans,
    strip_quoted_detected_lines,
)
from composeparts import (  # noqa: F401 - bot.X uyumluluğu için yeniden dışa aktarım
    _compose_assemble_text,
    _compose_body_links,
    _compose_build_entities,
    _compose_cut_body,
    _compose_drop_overflow_parts,
    _compose_fit_appendix,
    _compose_join,
    _compose_other_than_body,
    _compose_truncate_body,
    _compose_truncate_header,
)
from botentities import (  # noqa: F401 - bot.X uyumluluğu için yeniden dışa aktarım
    BOT_ENTITY_TYPES,
    bot_api_entities,
    bot_api_entity,
    bot_media_descriptor,
    composed_has_content,
    entities_for_text,
    media_buffer,
    media_upload_name,
    reupload_attributes,
    source_name_entity,
)
from dedupbadge import (  # noqa: F401 - bot.X uyumluluğu için yeniden dışa aktarım
    BADGE_SOURCE_SEPARATOR,
    DEDUP_BADGE_LEGACY_RE,
    DEDUP_BADGE_RE,
    DEDUP_MESSAGE_LINK_KEY,
    DEDUP_SOURCES_PREFIX,
    DEDUP_TOKEN_RE,
    _BADGE_NEW_SINGLE_SUFFIX_RE,
    _BADGE_TRAILING_DECOR_RE,
    _TITLE_STORE_PREFIX_RE,
    _badge_source_parts,
    _dedup_badge_count,
    _dedup_badge_location,
    _dedup_badge_match,
    _entity_url_and_span,
    _fold_title,
    _is_telegram_link,
    _rebase_entities_for_dedup_badge,
    _strip_dedup_badge_details,
    dedup_badge,
    dedup_badge_entities,
    dedup_badge_insertion,
    dedup_badge_links,
    dedup_badge_name_spans,
    dedup_badge_names,
    dedup_badge_sources,
    dedup_footer_name,
    dedup_key,
    dedup_source_links,
    note_dedup_source,
    shift_bot_entities,
    shift_telethon_entities,
    strip_dedup_badge,
)
from dedupmatch import (  # noqa: F401 - bot.X uyumluluğu için yeniden dışa aktarım
    _ASIN_RE,
    _MEASUREMENT_TOKEN_RE,
    _TITLE_BENIGN_EXTRA_WORDS,
    _TITLE_COLOR_WORDS,
    _TITLE_DIM_RE,
    _TITLE_HYPHEN_MODEL_RE,
    _TITLE_INCH_RE,
    _TITLE_NUM_UNIT_RE,
    _TITLE_STOPWORDS,
    _TITLE_UNIT_MAP,
    _TITLE_VARIANT_WORDS,
    _TITLE_WORD_RE,
    _UPGRADE_CONDITION_RE,
    _canonicalize_title_units,
    _dedup_whole_tl,
    _find_matching_dedup_key,
    _keep_detailed_prefix_title,
    _multibuy_blocked_by_condition_or_asin,
    _multibuy_editable_valid,
    _multibuy_entry_eligible,
    _multibuy_price_structure_valid,
    _ordered_title_prefix_match,
    _simple_linked_offer_price,
    _stem_title_word,
    _title_capacities,
    _title_has_strong_identity,
    _title_model_codes,
    _title_profile,
    better_or_different_offer,
    bot_history_ids_shared,
    dedup_offer_key,
    is_referenced_multibuy_upgrade,
    is_referenced_product_price_drop,
    new_dedup_entry,
    next_poll_interval,
    offer_terms,
    plain_offer_for_upgrade,
    product_asins,
    product_link_ids,
    recover_poll_interval,
    titles_match_product,
)
from commandtext import (  # noqa: F401 - bot.X uyumluluğu için yeniden dışa aktarım
    CMD_ANALYZE,
    CMD_DM_COMMANDS,
    CMD_SETTINGS_ADD,
    CMD_SETTINGS_MENU,
    CMD_SETTINGS_REMOVE,
    CMD_SETTINGS_REVERT,
    CMD_SETTINGS_SAVE,
    CMD_SOURCE_TEST,
    CMD_TG_FILTER_CLOSE,
    CMD_TG_FILTER_OPEN,
    CMD_WEB_TRACK_MENU,
    COMMAND_ANALYZE,
    COMMAND_DESCRIPTIONS,
    COMMAND_DM_FILTER_CLOSE,
    COMMAND_DM_FILTER_OPEN,
    COMMAND_DM_STATUS,
    COMMAND_HELP,
    COMMAND_ID,
    COMMAND_RESTART,
    COMMAND_SETTINGS_ADD,
    COMMAND_SETTINGS_MENU,
    COMMAND_SETTINGS_REMOVE,
    COMMAND_SETTINGS_REVERT,
    COMMAND_SETTINGS_SAVE,
    COMMAND_SOURCES,
    COMMAND_SOURCE_TEST,
    COMMAND_START,
    COMMAND_STATUS,
    COMMAND_TEST,
    COMMAND_TG_FILTER_CLOSE,
    COMMAND_TG_FILTER_OPEN,
    COMMAND_WEB_TRACK,
    ConfigStore,
    DM_LIST_FIELDS,
    DM_PRICE_FIELD,
    DM_PRODUCT_PRICE_FIELD,
    FILTER_TARGET_ALIASES,
    FILTER_TARGET_META,
    FILTER_TARGET_NUMBER,
    FILTER_TARGET_ORDER,
    HELP_TEXT,
    LIST_BATCH_MAX,
    LIST_ITEM_MAX_LENGTH,
    LIST_PREVIEW_MAX,
    PENDING,
    PENDING_TTL_SECONDS,
    PendingKey,
    SAVE_STATUS_TEXT,
    SETTINGS_COMMANDS,
    TELEGRAM_LIST_ALIASES,
    TELEGRAM_LIST_FIELDS,
    TELEGRAM_LIST_MENU,
    TELEGRAM_LIST_META,
    WEBTAKIP_CHOICES,
    WEBTAKIP_FIELD,
    _HELP_CATEGORIES,
    _HELP_LINES,
    _log_dm_decision,
    _stage_list_add,
    _stage_list_remove,
    _webtakip_count,
    build_filter_result_text,
    build_filter_toggle_prompt,
    build_list_category_prompt,
    build_list_change_confirmation,
    build_list_value_prompt,
    build_main_menu_text,
    dm_decision,
    dm_price_threshold,
    dm_should_send,
    drop_pending,
    filter_state_lines,
    format_value_preview,
    new_filter_edit,
    new_telegram_edit,
    peek_pending,
    pending_key,
    resolve_confirmation_choice,
    resolve_filter_target,
    resolve_telegram_list,
    set_pending,
    split_product_price_rule_values,
    split_values,
    stage_filter_toggle,
    stage_price_threshold_change,
    stage_product_price_rule_change,
    stage_telegram_list_change,
    telegram_list_item_key,
    telegram_list_items,
)
from analysis import (  # noqa: F401 - bot.X uyumluluğu için yeniden dışa aktarım
    ANALYSIS_ALL_TOKENS,
    ANALYSIS_DEFAULT_LIMIT,
    ANALYSIS_MAX_LIMIT,
    ANALYSIS_MIN_LIMIT,
    ANALYSIS_PRICE_LINE_MAX_WORDS,
    ANALYSIS_QUERY_MAX_LINES,
    ANALYSIS_SCAN_RUNNING,
    ANALYSIS_SCAN_START,
    ANALYSIS_TITLE_LIMIT,
    ANALYSIS_TOP,
    _ANALYSIS_PRICE_LINE_SKIP_RE,
    _price_line_extra_words,
    analysis_query_pattern,
    analysis_status_id,
    build_analysis_report,
    build_query_lines,
    chunk_text,
    format_ranking,
    list_contains_token,
    parse_analysis_args,
    parse_analysis_request,
    parse_price_value,
    query_price,
    query_product_title,
    start_analysis_scan,
    tidy_product_title,
    update_analysis_scan,
)

# Runner UTC çalışır; log saatlerini TSİ'ye sabitle ki kullanıcı saatle
# karşılaştırabilsin (Actions log'unda "10:44" görünen satırlar TSİ 13:44'tü).
# Sınıf özniteliği olarak atandığı için staticmethod sarmalayıcı gerekir;
# aksi hâlde Python bunu bağlı metot sanıp iki argüman geçirir.
logging.Formatter.converter = staticmethod(
    lambda seconds: timefmt.local(seconds).timetuple()
)
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("telegram-filter")

STARTED_AT = time.time()
STATS = {
    "seen": 0,        # kaynaklarda görülen mesaj
    "matched": 0,     # anahtar kelimeye uyan mesaj
    "forwarded": 0,   # hedefe iletilen mesaj
    "failed": 0,      # hedefe hiçbir teslim yolu çalışmayan kaynak mesajı
    "notification_failed": 0,  # içerik iletilse bile Bot API telefon bildirimi başarısız
    "commands": 0,    # çalıştırılan komut
    "modes": {},      # hangi iletim yolu kaç kez işe yaradı
    "cleaned": 0,     # bildirim gittikten sonra silinen hesap kopyası
    "cleaned_commands": 0,  # komut sohbetinde silinen eski komut/yanıt mesajı
    "deduped": 0,     # aynı ürün sorgusuyla birleştirilen (yeniden gönderilmeyen) tekrar
    "dedup_edits": 0,  # tekrarda ilk mesaja işlenen çoklu paylaşım notu güncellemesi
    "last_match": None,
    "last_match_source": None,
    "source_seen": {},  # kaynak chat_id -> bu oturumda alınan canlı NewMessage sayısı
}

# Kaynak/kontrol listelerinin açılışta çözülmüş hâli (main() doldurur).
SOURCES: list[dict[str, Any]] = []
SOURCE_IDS: set[int] = set()
SOURCE_FAILURES: list[tuple[Any, Exception]] = []
CONTROL_IDS: set[int] = set()
CONTROL_NAMES: list[str] = []
DESTINATION: Any = "me"       # hedefin çözülmüş hâli (int ID veya "me")
DESTINATION_LABEL = "me"
DESTINATION_ID: int | None = None
NOTIFY_BOT_TOKEN = ""
DELIVERY_CHAIN: list[str] = []
MAX_MEDIA_MB = 0
SELF_ID: int | None = None
# Aktif filtre/komut durumları (apply_runtime_config yazar).
FILTER_INCLUDE: list[str] = []
FILTER_EXCLUDE: list[str] = []
FILTER_MODE = "any"           # any | all (dahili kelimeler nasıl eşleşsin)
FILTER_INCLUDE_ENABLED = True  # 🔎 dahili kelime filtresi açık mı?
FILTER_EXCLUDE_ENABLED = True  # 🚫 harici kelime engeli açık mı?
ADMIN_IDS: set[int] = set()
SOURCE_FOOTER = True  # bildirimin en altına kaynak grup adını KALIN yaz (etiket yok; link: mesajın kendisi)
NOTIFY_MEDIA = True   # bildirim botu medyayı da göndersin
MESSAGE_LINK_LINE = True      # mesajın t.me adresini kaynak adına göm (ad yoksa "🔗 Mesajı Gör" satırı)
LINK_APPENDIX_MODE = "smart"  # smart | all | off (bkz. link_appendix_mode)
# Tek mesaj modu: bildirim botu mesajı gruba attıysa, hesabın attığı kopya silinir.
# Böylece her fırsat grupta tek mesaj olarak kalır (botun bildirimi = uyarı düşen mesaj).
SINGLE_MESSAGE = True
# Komut sohbeti temizliği: yeni bir komut/yanıt geldiğinde bir önceki komut
# alışverişi (senin komutun + botun yanıtı) silinir; ekranda yalnızca son mesaj
# kalır. İndirim bildirimleri bu kayda girmediği için ASLA silinmez.
CLEAN_COMMANDS = True
# Alıntı temizliği (kullanıcı isteği, "ilk adım"): üst satırda GÜVENLE tespit
# edilen ürün adı satırı ile tespit edilen tek başına kupon kodu satırı alıntıdan
# silinir. Emin olunmayan satır ASLA silinmez. config: clean_quote_duplicates.
CLEAN_QUOTE_DUPLICATES = False
# chat_id -> son komut alışverişinin mesaj ID'leri (yalnızca komut diyaloğu).
COMMAND_MESSAGES: dict[int, list[int]] = {}
# Tekrar birleştirme (dedup): aynı normalize ürün sorgulu fırsat tek mesaja toplanır.
# Ayrıntı: aşağıdaki "Tekrar birleştirme" bölümü.
DEDUP_ENABLED = True       # aynı ürün başlıklı tekrarlar birleştirilsin mi?
DEDUP_WINDOW_SECONDS = 12 * 3600  # normalize ürün başlığı kaç saniye "aynı fırsat" sayılsın
DEDUP_SCAN_LIMIT = 100     # açılışta hedefteki son kaç mesaj önbelleğe alınsın
DEDUP_MAX_ENTRIES = 1000   # bellekte tutulacak en fazla başlık (en eskiler düşer)
# normalize ürün sorgusu -> kayıt sözlüğü (bkz. new_dedup_entry).
DEDUP_CACHE: dict[str, dict[str, Any]] = {}
DEDUP_LOCK = asyncio.Lock()  # kayıt/rezervasyon kısa kritik bölümü


# ---------------------------------------------------------------------------
# Ortam / yapılandırma doğrulama
# ---------------------------------------------------------------------------

def _check_env_secrets(problems: list[str]) -> None:
    api_id = os.getenv("API_ID", "").strip()
    api_hash = os.getenv("API_HASH", "").strip()
    session_string = os.getenv("SESSION_STRING", "").strip()
    if not api_id:
        problems.append("API_ID secret'ı boş. Settings → Secrets and variables → Actions → Secrets sekmesine ekle.")
    else:
        try:
            int(api_id)
        except ValueError:
            problems.append(f"API_ID sayı olmalı, gelen değer okunamadı (uzunluk {len(api_id)}).")
    if not api_hash:
        problems.append("API_HASH secret'ı boş. my.telegram.org → API development tools'dan alıp secret olarak ekle.")
    elif len(api_hash) != 32:
        problems.append(f"API_HASH 32 karakter olmalı, gelen değer {len(api_hash)} karakter.")
    if not session_string:
        problems.append("SESSION_STRING secret'ı boş. generate_session.py ile üretip secret olarak ekle.")
    if not os.getenv("GH_PAT", "").strip():
        log.warning("GH_PAT boş: otomatik yenileme zinciri çalışmaz, workflow'u elle başlatman gerekir.")


def _check_config_flags_and_dm(config: dict, problems: list[str]) -> None:
    if not isinstance(config.get("private_control", False), bool):
        problems.append("config.json → private_control true/false olmalı.")
    if not isinstance(config.get("dm_enabled", False), bool):
        problems.append("config.json → dm_enabled true/false olmalı.")
    for field in ("dm_keywords", "dm_exclude_keywords"):
        dm_words = config.get(field, [])
        if (not isinstance(dm_words, list) or len(dm_words) > 100
                or any(not isinstance(w, str) or not w.strip() or len(w) > 100 for w in dm_words)):
            problems.append(f"config.json → {field} en fazla 100 dolu metinden oluşmalı (her biri ≤100 karakter).")
    price_items = config.get("dm_price_max", [])
    if not isinstance(price_items, list) or len(price_items) > 1 or any(
        price_filter.parse_threshold(item) is None for item in price_items
    ):
        problems.append("config.json → dm_price_max en fazla 1 sayı içermeli (örn. [\"10000\"]).")
    product_price_items = config.get(dm_filters.DM_PRODUCT_PRICE_FIELD, [])
    invalid_product_price_items = not isinstance(product_price_items, list) or len(product_price_items) > 100
    seen_product_price_rules: set[tuple[str, float]] = set()
    if isinstance(product_price_items, list):
        for item in product_price_items:
            try:
                rule = dm_filters.parse_product_price_rule(item)
            except (TypeError, ValueError):
                invalid_product_price_items = True
                break
            if rule.identity in seen_product_price_rules:
                invalid_product_price_items = True
                break
            seen_product_price_rules.add(rule.identity)
    if invalid_product_price_items:
        problems.append(
            "config.json → dm_product_price_rules en fazla 100 geçerli, yinelenmeyen "
            "'ürün adı < fiyat' kuralı içermeli."
        )
    for flag in ("smart_header", "fast_notify", "fast_poll"):
        if not isinstance(config.get(flag, False), bool):
            problems.append(f"config.json → {flag} true/false olmalı.")
    if config.get("private_control") and not os.getenv("NOTIFY_BOT_TOKEN", "").strip():
        log.warning("Özel komutlar için NOTIFY_BOT_TOKEN gerekli; Kayıtlı Mesajlar yedek kontrol olarak açık.")


def _check_sources_and_modes(config: dict, problems: list[str]) -> None:
    sources = chat_values(config.get("source_chats"))
    if not sources:
        problems.append("config.json → source_chats boş. En az bir kanal/grup eklenmeli.")
    if canonical_match_mode(config.get("match_mode", "any")) is None:
        problems.append(
            "config.json → match_mode yalnızca 'any', 'all' veya 'forward_all' olabilir "
            "(forward_all, dahili kelimeleri yok sayar; hariç kelimeler yine engeller)."
        )
    if str(config.get("copy_mode", "forward")).lower() not in {"forward", "copy"}:
        problems.append("config.json → copy_mode yalnızca 'forward' veya 'copy' olabilir.")
    explicit_modes = config.get("delivery_modes")
    if explicit_modes is not None:
        if not isinstance(explicit_modes, (list, tuple)) or not explicit_modes:
            problems.append("config.json → delivery_modes boş olmayan bir liste olmalı.")
        else:
            unknown = [str(m) for m in explicit_modes if str(m).strip().lower() not in DELIVERY_MODES]
            if unknown:
                problems.append(
                    "config.json → delivery_modes içinde bilinmeyen değer var: "
                    f"{unknown} (geçerli: {', '.join(DELIVERY_MODES)})"
                )


def _check_limits_and_destination(config: dict, problems: list[str]) -> bool:
    try:
        if int(config.get("max_media_mb", 25)) < 0:
            problems.append("config.json → max_media_mb negatif olamaz.")
    except (TypeError, ValueError):
        problems.append("config.json → max_media_mb bir sayı olmalı.")
    try:
        window = int(config.get("dedup_window_hours", 12))
        if not 1 <= window <= 72:
            problems.append("config.json → dedup_window_hours 1–72 saat arasında olmalı.")
    except (TypeError, ValueError):
        problems.append("config.json → dedup_window_hours bir sayı olmalı.")
    try:
        scan = int(config.get("dedup_scan_limit", 100))
        if not 0 <= scan <= 100:
            problems.append("config.json → dedup_scan_limit 0–100 arasında olmalı.")
    except (TypeError, ValueError):
        problems.append("config.json → dedup_scan_limit bir sayı olmalı.")
    try:
        parse_chat_value(config.get("destination", "me"))
    except ValueError as exc:
        problems.append(f"config.json → destination geçersiz: {exc}")
    control = config.get("control_chat", "me")
    control_values = list(control) if isinstance(control, (list, tuple)) else [control]
    group_control = False
    for value in control_values:
        try:
            if parse_chat_value(value) != "me":
                group_control = True
        except ValueError as exc:
            problems.append(f"config.json → control_chat geçersiz: {exc}")
    return group_control


def check_environment(config: dict | None = None) -> list[str]:
    """Eksik/hatalı ayarları toplar. Boş liste = her şey hazır."""
    problems: list[str] = []
    _check_env_secrets(problems)
    if config is None:
        return problems
    _check_config_flags_and_dm(config, problems)
    _check_sources_and_modes(config, problems)
    group_control = _check_limits_and_destination(config, problems)
    if group_control and not parse_admin_ids(config.get("admin_user_id")):
        problems.append(
            "config.json → control_chat bir grup ama admin_user_id boş. "
            "Grup komutları kimse tarafından kullanılamaz; kendi kullanıcı ID'ni yaz."
        )
    return problems


def print_report(config: dict, problems: list[str]) -> None:
    """Actions log'unda okunabilir bir açılış raporu bırak (sır değeri basmaz)."""
    def mask(name: str) -> str:
        return "var" if os.getenv(name, "").strip() else "YOK"

    print("=" * 62, flush=True)
    print("Telegram indirim takipçisi - yapılandırma raporu", flush=True)
    print("=" * 62, flush=True)
    print(f"Ortam değişkenleri : API_ID={mask('API_ID')} API_HASH={mask('API_HASH')} "
          f"SESSION_STRING={mask('SESSION_STRING')} GH_PAT={mask('GH_PAT')} "
          f"NOTIFY_BOT_TOKEN={mask('NOTIFY_BOT_TOKEN')}", flush=True)
    print(f"Kaynaklar          : {len(config.get('source_chats') or [])} adet", flush=True)
    print(f"Hedef              : {config.get('destination', 'me')}", flush=True)
    control_label = ("Bot özel sohbeti + Kayıtlı Mesajlar"
                     if config_flag(config.get("private_control"), False)
                     else config.get("control_chat", "me"))
    print(f"Kontrol sohbeti    : {control_label}", flush=True)
    print(f"Admin ID'leri      : {sorted(parse_admin_ids(config.get('admin_user_id'))) or 'tanımsız'}", flush=True)
    filter_mode = match_mode_of(config)
    filter_label = {
        "any": "any (biri yeterli)",
        "all": "all (hepsi zorunlu)",
        "forward_all": "forward_all (dahili kelimeler yok sayılır)",
    }[filter_mode]
    print(f"Anahtar kelimeler  : {config.get('include_keywords') or '(hepsi)'} ({filter_label})", flush=True)
    print(f"Hariç kelimeler    : {config.get('exclude_keywords') or '(yok)'}", flush=True)
    filter_state = filter_state_of(config)
    print(f"Filtre durumu      : dahili {'AÇIK' if filter_state['include'] else 'KAPALI'} | "
          f"harici {'AÇIK' if filter_state['exclude'] else 'KAPALI'} "
          f"(/tgfiltreac ve /tgfiltrekapat ile değiştirilir)", flush=True)
    print(f"İletim sırası      : {' → '.join(build_delivery_chain(config))}", flush=True)
    print(f"Medya sınırı       : {config.get('max_media_mb', 25)} MB", flush=True)
    mode = link_appendix_mode(config)
    mode_label = {"smart": f"akıllı ({PRODUCT_LINK_TEXT})", "all": "tüm gizli linkler", "off": "kapalı"}[mode]
    print(f"Bağlantı ekleri     : {mode_label} "
          f"| mesaj linki (grup adına gömülü): {'açık' if config_flag(config.get('message_link')) else 'kapalı'} "
          f"| bildirim medyası: {'açık' if config_flag(config.get('notify_media')) else 'kapalı'} "
          f"| tek mesaj: {'açık' if config_flag(config.get('single_message')) else 'kapalı'} "
          f"| kaynak adı (kalın, mesaja bağlı): {'açık' if config_flag(config.get('source_footer')) else 'kapalı'} "
          f"| komut temizliği: {'açık' if config_flag(config.get('clean_commands')) else 'kapalı'} "
          f"| alıntı temizliği: {'açık' if config_flag(config.get('clean_quote_duplicates'), False) else 'kapalı'}", flush=True)
    try:
        window_hours = int(config.get("dedup_window_hours", 12))
    except (TypeError, ValueError):
        window_hours = 12
    try:
        scan_limit = int(config.get("dedup_scan_limit", 100))
    except (TypeError, ValueError):
        scan_limit = 100
    print(f"Tekrar birleştirme : {'açık' if config_flag(config.get('dedup_enabled'), True) else 'kapalı'} "
          f"(pencere: {window_hours} sa · açılış taraması: son {scan_limit} mesaj)", flush=True)
    print(f"Otomatik yenileme  : {config.get('auto_restart', True)} "
          f"({os.getenv('RESTART_AFTER_MINUTES', '330')} dk sonra)", flush=True)
    if problems:
        print("-" * 62, flush=True)
        print("SORUNLAR:", flush=True)
        for problem in problems:
            print(f"  ✗ {problem}", flush=True)
    else:
        print("Sonuç              : yapılandırma geçerli ✅", flush=True)
    print("=" * 62, flush=True)


_INVITE_LINK_RE = re.compile(
    r"^https?://(?:t\.me|telegram\.me)/(?:\+|joinchat/)([A-Za-z0-9_-]+)/*$",
    re.IGNORECASE,
)


async def resolve_chat(client: TelegramClient, value: int | str) -> tuple[int, str, Any]:
    """Resolve joined invite links without joining or paying on the user's behalf."""
    invite = _INVITE_LINK_RE.fullmatch(value.strip()) if isinstance(value, str) else None
    if invite:
        checked = await client(functions.messages.CheckChatInviteRequest(invite.group(1)))
        if not isinstance(checked, types.ChatInviteAlready) or getattr(checked.chat, "left", False):
            raise ValueError("Önce takip hesabıyla daveti açıp üyeliği tamamla; onay/ücret gerekebilir.")
        entity = checked.chat
    else:
        try:
            entity = await client.get_entity(value)
        except ValueError:
            # StringSession does not persist Telethon's entity cache. Refresh
            # dialogs for a private numeric ID after a fresh process starts.
            if not isinstance(value, int):
                raise
            await client.get_dialogs()
            entity = await client.get_entity(value)
    return utils.get_peer_id(entity), display_name(entity), entity


async def dispatch_next_run(pat: str) -> tuple[bool, str]:
    """Yeni bir Actions çalışması başlatır. GH_PAT yoksa manuel moda düşer."""
    if not pat:
        return False, "GH_PAT tanımlı değil; Actions sayfasından 'Run workflow' ile başlat."
    repository = os.getenv("GITHUB_REPOSITORY", "")
    workflow = os.getenv("GITHUB_WORKFLOW_FILE", "telegram-monitor.yml")
    ref = os.getenv("GITHUB_REF_NAME", "main")
    if not repository:
        return False, "GITHUB_REPOSITORY bulunamadı (Actions dışında mı çalışıyor?)."
    url = f"https://api.github.com/repos/{repository}/actions/workflows/{workflow}/dispatches"
    request = urllib.request.Request(
        url,
        data=json.dumps({"ref": ref}).encode(),
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {pat}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "telegram-indirim-takipci",
        },
    )
    try:
        with await asyncio.to_thread(urllib.request.urlopen, request, timeout=20) as response:
            response.read()
        log.info("Sonraki GitHub Actions çalışması başlatıldı (%s/%s @ %s).", repository, workflow, ref)
        return True, f"Yeni çalışma başlatıldı ({repository}@{ref})."
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        log.error("Actions başlatılamadı: HTTP %s %s", exc.code, body)
        return False, f"GitHub isteği başarısız: HTTP {exc.code}. GH_PAT'in 'Actions: Read and write' yetkisi var mı?"
    except Exception as exc:  # noqa: BLE001 - ağ hatası bot'u öldürmemeli
        log.exception("Actions başlatılamadı.")
        return False, f"GitHub isteği başarısız: {type(exc).__name__}."


def encode_multipart(fields: dict[str, Any], files: Sequence[tuple[str, str, str, bytes]]) -> tuple[bytes, str]:
    """Basit multipart/form-data gövdesi kur (Bot API dosya yüklemeleri için).

    ``files`` üçlüleri: (alan adı, dosya adı, MIME türü, içerik).
    """
    boundary = "----IndirimTakipci" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for name, value in fields.items():
        if value is None or value == "":
            continue
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8")
        )
    for field, filename, mime, data in files:
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
            f"Content-Type: {mime}\r\n\r\n".encode("utf-8") + bytes(data) + b"\r\n"
        )
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class BotSendResult(tuple):
    """``(ok, detail)`` gibi açılan ama gönderilen mesajın ID'sini de taşıyan sonuç.

    Var olan ``ok, detail = await send_bot_ping(...)`` çağrıları ve testlerdeki
    düz 2'li fake'ler bozulmadan çalışmaya devam eder; yeni kod mesaj ID'sine
    ``getattr(sonuç, "message_id", None)`` ile erişir (fake'lerde ``None`` döner).
    """

    def __new__(cls, ok: bool, detail: str, message_id: int | None = None) -> BotSendResult:
        self = super().__new__(cls, (bool(ok), str(detail)))
        self.ok = bool(ok)
        self.detail = str(detail)
        self.message_id = message_id
        return self


def message_id_from_result(result: Any) -> int | None:
    """Bot API ``result`` sözlüğünden mesaj ID'sini al; yoksa ``None``."""
    message_id = result.get("message_id") if isinstance(result, dict) else None
    if isinstance(message_id, bool):
        return None
    return int(message_id) if isinstance(message_id, int) else None


async def _bot_api_request_full(
    token: str, method: str, *, payload: bytes, content_type: str, what: str,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Bot API çağrısı; başarılı sonucun ``result`` sözlüğünü de döndürür."""
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=payload,
        method="POST",
        headers={"Content-Type": content_type},
    )
    async def request_once() -> dict[str, Any]:
        try:
            with await asyncio.to_thread(urllib.request.urlopen, request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8", "replace") or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            try:
                body = json.loads(detail)
            except ValueError:
                body = {"description": f"HTTP {exc.code}"}
            body["error_code"] = exc.code
            body["ok"] = False
            return body

    try:
        body = await request_with_retry(token, method, request_once)
        if body.get("ok"):
            result = body.get("result")
            return True, f"{what} gönderildi", result if isinstance(result, dict) else None
        detail = str(body.get("description", "Bot API ok=false"))
        if method.startswith("editMessage") and "message is not modified" in detail.lower():
            return True, "değişiklik yok (zaten güncel)", None
        log.error("Bildirim bot'u hata verdi: %s %s", body.get("error_code", "?"), detail[:200])
        return False, f"HTTP {body.get('error_code', '?')}: {detail}", None
    except Exception as exc:  # noqa: BLE001 - belirsiz ağ hatasını tekrar yollama
        log.warning("Bildirim gönderilemedi: %s", type(exc).__name__)
        return False, f"{type(exc).__name__}", None


async def _bot_api_request(
    token: str, method: str, *, payload: bytes, content_type: str, what: str,
) -> tuple[bool, str]:
    ok, detail, _ = await _bot_api_request_full(
        token, method, payload=payload, content_type=content_type, what=what,
    )
    return ok, detail


async def send_bot_ping(
    token: str,
    chat_id: int | str,
    text: str,
    *,
    entities: list[dict[str, Any]] | None = None,
    keyboard: dict[str, Any] | None = None,
    link_preview: bool | None = None,
) -> tuple[bool, str]:
    """Bot API üzerinden bildirim mesajı atar.

    Takipçi *kendi hesabınla* gönderdiği için Telegram o mesajları senin kendi
    mesajın sayar ve bildirim üretmez. Bildirim isteyenler @BotFather'dan bir bot
    oluşturup hedef gruba ekler; bu fonksiyon o bot adına mesajı atar.

    ``entities`` ve ``keyboard`` verilirse mesaj, kaynaktaki biçimlendirmeyi
    (gizli bağlantılar dâhil) ve buton linklerini korur.

    Dönen değer ``(ok, detail)`` gibi açılır; ``.message_id`` özniteliği
    gönderilen Bot API mesajının ID'sini verir (başarısızlıkta ``None``).
    """
    if not token:
        return BotSendResult(False, "NOTIFY_BOT_TOKEN secret/ortam değişkeni tanımlı değil.")
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text[:MESSAGE_LIMIT]}
    if entities:
        payload["entities"] = json.dumps(entities)
    if keyboard:
        payload["reply_markup"] = json.dumps(keyboard)
    if link_preview is not None:
        payload["link_preview_options"] = json.dumps({"is_disabled": not link_preview})
    ok, detail, result = await _bot_api_request_full(
        token, "sendMessage",
        payload=json.dumps(payload).encode(), content_type="application/json", what="bildirim",
    )
    return BotSendResult(ok, detail, message_id_from_result(result))


async def send_bot_media(
    token: str,
    chat_id: int | str,
    *,
    kind: str,
    filename: str,
    mime_type: str,
    data: bytes,
    caption: str | None = None,
    entities: list[dict[str, Any]] | None = None,
    keyboard: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Bildirim botuyla fotoğraf/video/dosya gönder (medya da bildirim üretsin).

    Dönen değer ``(ok, detail)`` gibi açılır; ``.message_id`` özniteliği
    gönderilen Bot API mesajının ID'sini verir (başarısızlıkta ``None``).
    """
    if not token:
        return BotSendResult(False, "NOTIFY_BOT_TOKEN secret/ortam değişkeni tanımlı değil.")
    method = {"photo": "sendPhoto", "video": "sendVideo"}.get(kind, "sendDocument")
    field = {"photo": "photo", "video": "video"}.get(kind, "document")
    fields: dict[str, Any] = {"chat_id": chat_id}
    if caption:
        fields["caption"] = caption[:CAPTION_LIMIT]
    if entities:
        fields["caption_entities"] = json.dumps(entities)
    if keyboard:
        fields["reply_markup"] = json.dumps(keyboard)
    body, content_type = encode_multipart(fields, [(field, filename, mime_type, data)])
    ok, detail, result = await _bot_api_request_full(
        token, method, payload=body, content_type=content_type, what=f"bildirim medyası ({kind})",
    )
    return BotSendResult(ok, detail, message_id_from_result(result))


async def delete_bot_message(token: str, chat_id: int | str, message_id: int) -> tuple[bool, str]:
    payload = {"chat_id": chat_id, "message_id": message_id}
    return await _bot_api_request(token, "deleteMessage", payload=json.dumps(payload).encode(),
                                  content_type="application/json", what="eski bildirim")


async def edit_bot_text(
    token: str,
    chat_id: int | str,
    message_id: int,
    text: str,
    *,
    entities: list[dict[str, Any]] | None = None,
    keyboard: dict[str, Any] | None = None,
    link_preview: bool | None = None,
) -> tuple[bool, str]:
    """Bildirim botunun gönderdiği bir metin mesajını yerinde günceller.

    Tekrar birleştirmede ilk mesaja çoklu paylaşım notu ("3 kere paylaşıldı: ...")
    işlerken kullanılır. Bot kendi mesajını düzenleyebilir; içerik değişmediyse
    Telegram "message is not modified" der, bu başarı sayılır.
    """
    if not token:
        return False, "NOTIFY_BOT_TOKEN secret/ortam değişkeni tanımlı değil."
    payload: dict[str, Any] = {
        "chat_id": chat_id, "message_id": message_id, "text": text[:MESSAGE_LIMIT],
    }
    if entities:
        payload["entities"] = json.dumps(entities)
    if keyboard:
        payload["reply_markup"] = json.dumps(keyboard)
    if link_preview is not None:
        payload["link_preview_options"] = json.dumps({"is_disabled": not link_preview})
    ok, detail = await _bot_api_request(
        token, "editMessageText",
        payload=json.dumps(payload).encode(), content_type="application/json", what="bildirim güncellemesi",
    )
    if not ok and "message is not modified" in detail:
        return True, "değişiklik yok (zaten güncel)"
    return ok, detail


async def edit_bot_caption(
    token: str,
    chat_id: int | str,
    message_id: int,
    caption: str,
    *,
    entities: list[dict[str, Any]] | None = None,
    keyboard: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Bildirim botunun gönderdiği bir medyanın açıklamasını yerinde günceller."""
    if not token:
        return False, "NOTIFY_BOT_TOKEN secret/ortam değişkeni tanımlı değil."
    payload: dict[str, Any] = {
        "chat_id": chat_id, "message_id": message_id, "caption": caption[:CAPTION_LIMIT],
    }
    if entities:
        payload["caption_entities"] = json.dumps(entities)
    if keyboard:
        payload["reply_markup"] = json.dumps(keyboard)
    ok, detail = await _bot_api_request(
        token, "editMessageCaption",
        payload=json.dumps(payload).encode(), content_type="application/json", what="bildirim güncellemesi",
    )
    if not ok and "message is not modified" in detail:
        return True, "değişiklik yok (zaten güncel)"
    return ok, detail


# ---------------------------------------------------------------------------
# İletim zinciri (korumalı içerik için alternatifli yol)
# ---------------------------------------------------------------------------

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


# Yeni bildirim üst bloğu (TEK ÜST SATIR: "₺56 — ürün adı" ya da "❓ — ürün adı";
# alıntılanmış orijinal mesajın üstünde).
# config.json → smart_header; anahtar yoksa KAPALI (eski düzen aynen sürer).
SMART_HEADER = False
# Hızlı bildirim: bot bildirimi ÖNCE gider; hesap kopyası yalnızca bot başarısız olursa atılır.
# config.json → fast_notify; anahtar yoksa KAPALI (eski sıra aynen sürer).
FAST_NOTIFY = False
# Hızlı tarama: Telegram bazı yoğun kanalların güncellemelerini geç (10-60 sn) iletiyor.
# Bu görev kaynakları birkaç saniyede bir tek istekle yoklar, yeni mesajı hemen işler.
# config.json → fast_poll (varsayılan AÇIK; kapatmak için false), fast_poll_seconds (varsayılan 3).
FAST_POLL = True
FAST_POLL_SECONDS = 3.0
# P6: açılışta her kaynak için geriye dönük tarama sınırları. Kesinti
# sırasında gelen mesajlar yalnızca bu pencere içinde yakalanır; daha eski
# mesajlar topluca yeniden bildirilmez. Kalıcı imleç tutulmaz.
FAST_POLL_BACKFILL_LIMIT = 100       # yeni/yeniden başlayan kaynak başına başlangıç penceresi
FAST_POLL_BACKFILL_MAX_AGE = 1800.0  # başlangıçta yalnızca son 30 dk içindeki iletiler
FAST_POLL_BATCH_LIMIT = 30           # kaynak başına bir turda en fazla 30 yeni ileti
# P0 ölçümleme: hızlı taramanın anlık durumu (/durum ve kalp atışında görünür).
# Yalnızca sayaç/aralık bilgisi tutar; davranış değişmez.
POLL_TELEMETRY: dict[str, float] = {
    "interval": FAST_POLL_SECONDS,   # o an kullanılan yoklama aralığı (sn)
    "last_elapsed": 0.0,             # son tam tarama turunun süresi (sn)
    "last_dialog_elapsed": 0.0,      # son GetPeerDialogs isteğinin süresi (sn)
    "flood_count": 0,                # bu oturumdaki tarama FloodWait sayısı
    "last_flood_wait": 0.0,          # son tarama FloodWait beklemesi (sn)
}
LIVE_SEEN: "collections.OrderedDict[tuple[int, int], float]" = collections.OrderedDict()
POLL_DISPATCHED: "collections.OrderedDict[tuple[int, int], float]" = collections.OrderedDict()
SOURCE_IN_FLIGHT: set[tuple[int, int]] = set()
# Kaynak bazlı kalıcı cursor (yalnızca RAM — bilinçli /restart’ta tek commit)
CURSOR_RAM: dict[int, dict[str, Any]] = {}
RESTARTING = False  # /restart drain sırasında yeni mesajlar bekletilsin
# Yavaş bildirim raporu: toplam süre bu eşiği (sn) aşarsa Kayıtlı Mesajlar'a döküm gelir. 0 = kapalı.
SLOW_REPORT_SECONDS = 8.0
_SLOW_REPORT_LAST = 0.0
MEDIA_TIMING: "contextvars.ContextVar[dict | None]" = contextvars.ContextVar("media_timing", default=None)
# P5: tek bir deliver() çağrısı içinde indirilen medyanın bildirime taşınması;
# (chat_id, message_id) -> bytes. deliver() bitince bağlam sıfırlanır, kalıcı
# önbellek değildir; eşzamanlı teslimler birbirinin bağlamını görmez.
MEDIA_DOWNLOAD_CACHE: "contextvars.ContextVar[dict | None]" = contextvars.ContextVar(
    "media_download_cache", default=None)


def _remember(store_: "collections.OrderedDict[tuple[int, int], float]", chat_id: Any, message_id: Any) -> None:
    store_[(int(chat_id), int(message_id))] = time.time()
    while len(store_) > 5000:
        store_.popitem(last=False)


def live_event_should_skip(event: Any) -> bool:
    """Atomically claim a source/message pair before the first await.

    Both live and polled events use the same registry, including in-flight
    deliveries. POLL_DISPATCHED only marks scheduled work, not completed work.
    """
    key = (int(event.chat_id), int(event.id))
    if key in LIVE_SEEN or key in SOURCE_IN_FLIGHT:
        return True
    if key in POLL_DISPATCHED and not getattr(event, "_fast_poll", False):
        return True
    _remember(LIVE_SEEN, *key)
    return False


# --- Cursor (tek cursor / grup) — yalnızca RAM, bilinçli /restart’ta tek persist

def _cursor_ensure(chat_id: int) -> dict[str, Any]:
    cid = int(chat_id)
    if cid not in CURSOR_RAM:
        CURSOR_RAM[cid] = {"cursor": 0, "pending": set(), "completed": set(), "failed": set(), "failed_at": {}, "initialized": True}
    return CURSOR_RAM[cid]


def _cursor_advance(chat_id: int) -> None:
    """Sade ardışık ilerleme: yalnızca completed zinciri ilerler; gap’ler poll’de doğrulanır."""
    st = _cursor_ensure(chat_id)
    while True:
        nxt = int(st["cursor"]) + 1
        if nxt in st["pending"]:
            break
        if nxt in st["failed"]:
            break  # başarısız — sonraki poll’de retry edilecek, kaybetme yok
        if nxt in st["completed"]:
            st["completed"].remove(nxt)
            st["cursor"] = nxt
            continue
        break
    # Budama kaldırıldı: cursor önünde poll ile henüz doğrulanmamış
    # bir boşluk varken bile pending/failed boş olabilir. 500+ kayıtlı
    # tamamlanmış zinciri silmek cursor'u kilitleyebilir; bu yüzden
    # completed kayıtları güvenle tüketilene kadar korunur.


def _cursor_mark_pending(chat_id: int, msg_id: int) -> None:
    st = _cursor_ensure(chat_id)
    mid = int(msg_id)
    st["pending"].add(mid)
    # önceki failed aynı ID yeniden deneniyorsa temizle
    st["failed"].discard(mid)
    st["failed_at"].pop(mid, None)


def _cursor_mark_success(chat_id: int, msg_id: int) -> None:
    st = _cursor_ensure(chat_id)
    mid = int(msg_id)
    st["pending"].discard(mid)
    st["failed"].discard(mid)
    st["failed_at"].pop(mid, None)
    st["completed"].add(mid)
    _cursor_advance(chat_id)


def _cursor_mark_failed(chat_id: int, msg_id: int) -> None:
    st = _cursor_ensure(chat_id)
    mid = int(msg_id)
    st["pending"].discard(mid)
    st["failed"].add(mid)
    st["failed_at"][mid] = time.time()


def cursor_load_from_config(config: dict) -> None:
    """config.json → source_cursors RAM’e yükle (main başlangıcında)."""
    CURSOR_RAM.clear()
    raw = config.get("source_cursors")
    if not isinstance(raw, dict):
        return
    for k, v in raw.items():
        try:
            cid = int(k)
            cur = int(v)
            CURSOR_RAM[cid] = {"cursor": cur, "pending": set(), "completed": set(), "failed": set(), "failed_at": {}, "initialized": True}
        except Exception:
            continue


def cursor_collect_for_persist() -> dict[str, int]:
    """Yalnızca başlangıç noktası belirlenmiş RAM cursor’larını kalıcılaştır."""
    return {
        str(int(cid)): int(st["cursor"])
        for cid, st in CURSOR_RAM.items()
        if st.get("initialized", True)
    }


async def cursor_persist_for_restart(store: ConfigStore) -> tuple[bool, str]:
    """Bilinçli /restart öncesi tek toplu kalıcı kayıt. Başarı → restart serbest."""
    # Mevcut atomic_write_json + commit_and_push + GIT_WRITE_LOCK yolunu kullan
    store.config["source_cursors"] = cursor_collect_for_persist()
    ok, note = await save_config(store, "cursor: restart öncesi yedek")
    return ok, note


def maybe_report_slow(tg_client: Any, source_name: str, lag: float, steps: list[tuple[str, float]]) -> None:
    """Toplam süre eşiği aşarsa Kayıtlı Mesajlar'a süre dökümü gönder (arka planda; en fazla dakikada 1)."""
    global _SLOW_REPORT_LAST
    processing = sum(seconds for _, seconds in steps)
    total = processing + max(lag, 0.0)
    if SLOW_REPORT_SECONDS <= 0 or total < SLOW_REPORT_SECONDS:
        return
    now = time.time()
    if now - _SLOW_REPORT_LAST < 60:
        return
    _SLOW_REPORT_LAST = now
    lines = [f"⏱ Yavaş bildirim ({total:.0f} sn) — {source_name}"]
    if lag >= 0:
        lines.append(f"• Telegram → bot: {lag:.1f} sn")
    lines += [f"• {name}: {seconds:.1f} sn" for name, seconds in steps]
    media = MEDIA_TIMING.get()
    if media:
        lines.append(
            f"• Medya: {media.get('kind')}, {media.get('mb', 0):.1f} MB, "
            f"indirme {media.get('download', 0):.1f} sn, yükleme {media.get('upload', 0):.1f} sn"
        )
    text = "\n".join(lines)

    async def _send() -> None:
        try:
            await tg_client.send_message("me", text)
        except Exception as exc:  # noqa: BLE001 - rapor gönderilemese de akış sürsün
            log.warning("Yavaş bildirim raporu gönderilemedi: %s", type(exc).__name__)

    try:
        asyncio.get_running_loop().create_task(_send())
    except RuntimeError:
        pass


def _compose_resolve_header(
    obj: Any, cleaned_obj: Any, source_body: str, fallback_title: str,
) -> tuple[str, str, str, bool]:
    """Üst satır (fiyat + ürün adı) ve alıntı işaretini SMART_HEADER moduna göre kur."""
    product_title = fallback_title
    price_text = ""
    quote_body = False
    if SMART_HEADER:
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
    if CLEAN_QUOTE_DUPLICATES and SMART_HEADER and quote_body:
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
# Aynı sohbete Bot API ~1/sn sınırı uygular; not güncellemeleri bunun
# altında kalmak için mesaj başına bu kadar aralık bırakır. Aralık içinde
# gelen tekrarlar tek düzenlemede birleşir (düzenleme kilidi altında sayaç
# yeniden okunur); bekleme Bot API kilidi alınmadan yapılır, bu yüzden yeni
# bildirimler rozet beklemesinin arkasında kilitlenmez.
DEDUP_EDIT_MIN_INTERVAL = 5.0
# Rezervasyon koyup bitirmeyen göndereni bekleme süresi (sonrası devralma).
DEDUP_RESERVE_TIMEOUT = 60.0
# Yarım kalmış rezervasyonun önbellekten atılma süresi (temizlik).
DEDUP_RESERVE_TTL = 300.0


def prune_dedup_cache(
    cache: dict[str, dict[str, Any]], now: float, window_seconds: float, max_entries: int,
) -> int:
    """Süresi dolmuş ve yarım kalmış kayıtları at; fazlaysa en eskileri düşür.

    Dönen değer atılan kayıt sayısıdır. Bekleyen (``pending``) kayıtlar
    ``DEDUP_RESERVE_TTL`` dolmadan atılmaz; kapasite aşımında önce hazır
    kayıtlar elenir.
    """
    dropped = 0
    for key in list(cache):
        entry = cache.get(key) or {}
        age = now - float(entry.get("first_seen", now))
        if entry.get("pending"):
            owner = entry.get("owner")
            if age > DEDUP_RESERVE_TTL and (owner is None or owner.done()):
                entry["failed"] = True
                try:
                    entry["ready"].set()
                except Exception:  # noqa: BLE001 - temizlik akışı durdurmaz
                    pass
                del cache[key]
                dropped += 1
        elif age > window_seconds:
            del cache[key]
            dropped += 1
    overflow = len(cache) - max(1, int(max_entries))
    if overflow > 0:
        ordered = sorted(cache.items(), key=lambda item: float(item[1].get("first_seen", now)))
        for key, entry in ordered:
            if overflow <= 0:
                break
            if entry.get("pending"):
                continue
            del cache[key]
            overflow -= 1
            dropped += 1
        for key, entry in ordered:
            if overflow <= 0:
                break
            owner = entry.get("owner")
            if owner is not None and not owner.done():
                continue
            if key in cache:
                del cache[key]
                overflow -= 1
                dropped += 1
    return dropped


def _bot_api_stats_line() -> str:
    """P0 ölçümleme özeti: Bot API 429 sayaçları (token/içerik yazılmaz)."""
    if not NOTIFY_BOT_TOKEN:
        return "• Bot API hız sınırı: token yok (ölçüm kapalı)"
    stats = gate_stats(NOTIFY_BOT_TOKEN)
    line = (
        f"• Bot API hız sınırı: 429={stats['rate_limit_count']}"
        f" · retry bekleme {stats['retry_wait_seconds']:.1f} sn"
        f" · kilit bekleme {stats['lock_wait_seconds']:.1f} sn"
        f" · soğuma bekleme {stats['cooldown_wait_seconds']:.1f} sn"
    )
    by_method = stats.get("rate_limits_by_method") or {}
    if by_method:
        line += " · metot: " + ", ".join(
            f"{name}×{count}" for name, count in sorted(by_method.items())
        )
    return line


def build_status_text(config: dict, *, connected: bool = True) -> str:
    last = STATS["last_match"]
    last_line = "henüz eşleşme yok"
    if last:
        last_line = f"{humanize(time.time() - last)} önce ({STATS['last_match_source']})"
    keywords = telegram_list_items(config, "include_keywords")
    exclude_words = telegram_list_items(config, "exclude_keywords")
    mode = match_mode_of(config)
    state = filter_state_of(config)
    keyword_text = ", ".join(str(item) for item in keywords) or "(kelime yok)"
    exclude_text = ", ".join(str(item) for item in exclude_words) or "(kelime yok)"
    if state["include"]:
        filter_line = f"• 🔎 Telegram dahili filtre: AÇIK · {keyword_text} ({mode})"
    else:
        filter_line = "• 🔎 Telegram dahili filtre: KAPALI · kelimeler yok sayılıyor"
    if state["exclude"]:
        filter_line += f"\n• 🚫 Telegram harici filtre: AÇIK · {exclude_text}"
    else:
        filter_line += "\n• 🚫 Telegram harici filtre: KAPALI · engelleme yapılmıyor"
    filter_line += "\n• Değiştirmek için /tgfiltreac ve /tgfiltrekapat"
    if not connected:
        listening = "• Dinleme: BAĞLANTI YOK — Telegram bağlantısı bekleniyor."
    elif not SOURCE_IDS:
        listening = "• Dinleme: KAYNAK YOK — kaynak listesini kontrol et."
    else:
        listening = "• Dinleme: AKTİF — kaynak mesajları bekleniyor."
    return (
        "✅ Takipçi aktif\n"
        f"{listening}\n"
        f"• Çalışma süresi: {humanize(time.time() - STARTED_AT)}"
        f" · başlangıç {timefmt.stamp(STARTED_AT)} TSİ (şimdi {timefmt.clock()} TSİ)\n"
        f"• Dinlenen kaynak: {len(SOURCE_IDS)}/{len(config.get('source_chats') or [])}"
        + (f" (çözülemeyen {len(SOURCE_FAILURES)})" if SOURCE_FAILURES else "")
        + "\n"
        f"• Görülen: {STATS['seen']} | Eşleşen: {STATS['matched']} | İletilen: {STATS['forwarded']}"
        + (f" | Hedefe iletilemeyen: {STATS['failed']}" if STATS["failed"] else "")
        + (f" | Telefon bildirimi başarısız: {STATS['notification_failed']}"
           if STATS.get("notification_failed") else "")
        + "\n"
        f"• Son eşleşme: {last_line}\n"
        f"• Hedef: {DESTINATION_LABEL}\n"
        f"• Kontrol sohbeti: {('Bot özel sohbeti + Kayıtlı Mesajlar' if config_flag(config.get('private_control'), False) else ', '.join(CONTROL_NAMES) or 'me')}\n"
        f"{filter_line}\n"
        f"• İletim sırası: {' → '.join(DELIVERY_CHAIN) or 'yok'}"
        + (f" | kullanılan: {', '.join(f'{k}×{v}' for k, v in STATS['modes'].items())}"
           if STATS["modes"] else "")
        + "\n"
        f"• Tek mesaj modu: {'açık' if SINGLE_MESSAGE else 'kapalı'}"
        + (f" · bildirim sonrası silinen hesap kopyası: {STATS.get('cleaned', 0)}"
           if SINGLE_MESSAGE and STATS.get("cleaned") else "")
        + "\n"
        f"• Komut temizliği: {'açık' if CLEAN_COMMANDS else 'kapalı'}"
        + (f" · silinen eski komut mesajı: {STATS.get('cleaned_commands', 0)}"
           if CLEAN_COMMANDS and STATS.get("cleaned_commands") else "")
        + "\n"
        + (
            f"• Tekrar birleştirme: açık · birleştirilen {STATS.get('deduped', 0)}"
            f" · rozet {STATS.get('dedup_edits', 0)} · izlenen {len(DEDUP_CACHE)} başlık"
            if DEDUP_ENABLED else "• Tekrar birleştirme: kapalı"
        )
        + "\n"
        + (
            f"• Hızlı tarama: açık · aralık {POLL_TELEMETRY['interval']:.0f} sn"
            f" · son yoklama {POLL_TELEMETRY['last_elapsed']:.1f} sn"
            f" · FloodWait {int(POLL_TELEMETRY['flood_count'])}"
            + (f" (son {POLL_TELEMETRY['last_flood_wait']:.0f} sn)" if POLL_TELEMETRY["flood_count"] else "")
            if FAST_POLL else "• Hızlı tarama: kapalı"
        )
        + "\n"
        + _bot_api_stats_line()
    )


def build_source_text() -> str:
    lines = [f"İzlenen {len(SOURCES)} kaynak:"]
    lines += [f"• {item['name']}  [{item['id']}]" + ("" if item["joined"] else "  ⚠️ ÜYE DEĞİLSİN")
              for item in SOURCES]
    for value, exc in SOURCE_FAILURES:
        lines.append(f"✗ {value} çözülemedi: {exc}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Kalıcılık: atomik dosya yazımı + repo'ya işleme
# ---------------------------------------------------------------------------

def atomic_write_json(path: Path, data: dict) -> None:
    """JSON'u yarıda kalmayacak şekilde yaz: geçici dosya → fsync → os.replace.

    Yarım yazılmış bir config.json bot'u açılışta çökertir; bu yüzden doğrudan
    hedef dosyaya yazmıyoruz.
    """
    path = Path(path)
    persisted_data = dict(data)
    persisted_data.pop("notify_bot_token", None)
    payload = json.dumps(persisted_data, ensure_ascii=False, indent=2) + "\n"
    directory = str(path.parent) if str(path.parent) else "."
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=directory,
        prefix=f".{path.name}.", suffix=".tmp", delete=False,
    )
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise
    try:  # dizin girdisinin de diske inmesini garantile
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


def _run_git(args: Sequence[str], cwd: str, timeout: int = 30) -> tuple[int, str]:
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except FileNotFoundError:
        return 127, "git komutu bulunamadı"
    except subprocess.TimeoutExpired:
        return 124, f"git {args[0]} zaman aşımına uğradı"
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def git_repo_root(path: str | os.PathLike[str]) -> Path | None:
    """Verilen yolun bağlı olduğu git deposunun kökünü bul; yoksa None."""
    code, out = _run_git(["rev-parse", "--show-toplevel"], cwd=str(path))
    if code == 0 and out:
        return Path(out.splitlines()[0].strip())
    return None


def push_token() -> str:
    """Depoya yazmak için kullanılabilecek token (ilk dolu olan)."""
    for name in ("CONFIG_PUSH_TOKEN", "GITHUB_TOKEN", "GH_PAT"):
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def current_branch(repo: Path) -> str:
    code, out = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=str(repo))
    branch = out.splitlines()[0].strip() if code == 0 and out else ""
    if not branch or branch == "HEAD":  # detached HEAD
        branch = os.getenv("GITHUB_REF_NAME", "").strip()
    return branch


GIT_WRITE_LOCK = threading.RLock()


def commit_and_push(path: Path, message: str) -> tuple[str, str]:
    with GIT_WRITE_LOCK:
        return _commit_and_push_locked(path, message)


def _commit_and_push_locked(path: Path, message: str) -> tuple[str, str]:
    """config.json'ı commit'leyip origin'e gönder: (durum, detay).

    durum: pushed | clean | local | no-repo | error
    Hiçbir durumda istisna fırlatmaz; çağıran yalnızca raporlar.
    """
    path = Path(path)
    repo = git_repo_root(path.parent if path.parent != Path("") else ".")
    commit_message = f"config: {message}" if message else "config: güncelleme"
    if repo is None:
        return _push_via_api(path, commit_message)
    try:
        relative = str(path.resolve().relative_to(repo.resolve()))
    except (ValueError, OSError):
        return "no-repo", "config.json depo dışında"

    token = push_token()
    code, out = _run_git(["status", "--porcelain", "--", relative], cwd=str(repo))
    if code != 0:
        return "error", f"git status başarısız: {out[:160]}"
    if not out.strip():
        return "clean", "depoda commit edilecek değişiklik yok"

    code, out = _run_git(["add", "--", relative], cwd=str(repo))
    if code != 0:
        return "error", f"git add başarısız: {out[:160]}"
    code, out = _run_git(
        ["-c", "user.name=telegram-indirim-takipci", "-c",
         "user.email=actions@github.invalid", "commit", "-m", commit_message, "--", relative],
        cwd=str(repo),
    )
    if code != 0:
        return "error", f"git commit başarısız: {out[:160]}"

    # Sıra önemli: Actions'ın GITHUB_REF_NAME'i pull_request olayında dal adı
    # değil "4/merge" gibi bir birleştirme referansıdır. Onu doğrudan refspec
    # olarak kullanmak depoda gereksiz bir dal açar; yerel dal daha güvenilir.
    branch = (os.getenv("CONFIG_PUSH_BRANCH", "").strip()
              or current_branch(repo)
              or os.getenv("GITHUB_REF_NAME", "").strip())
    if not branch:
        return "local", "dal adı bulunamadı; commit yerelde kaldı"

    code, out = push_branch(repo, branch, token)
    if code != 0 and ("non-fast-forward" in out or "fetch first" in out or "rejected" in out):
        _run_git(["pull", "--rebase", "--no-edit", "origin", branch], cwd=str(repo), timeout=60)
        code, out = push_branch(repo, branch, token)
    if code != 0:
        if not token:
            return "local", (f"depoya gönderilemedi ve push token'ı yok; commit yerelde kaldı "
                             f"(git push origin {branch}). Hata: {out[:160]}")
        return "error", f"git push başarısız: {out[:200]}"
    return "pushed", f"origin/{branch}"


def push_branch(repo: Path, branch: str, token: str = "") -> tuple[int, str]:
    """Dalı ``origin``e gönder.

    Önce ortamda hazır kimlik bilgisi varmış gibi düz ``git push`` dener;
    GitHub Actions'ta ``actions/checkout`` kimliği ``.git/config``e yazar ve
    job'a ``contents: write`` verildiği için bu tek başına yeterlidir.
    Başarısız olursa token'ı ``http.extraheader`` ile enjekte ederek tekrar
    dener (VM'de çalıştırma senaryosu).

    Sıra bilinçli: Actions'ta üstüne bir de ``http.extraheader`` eklemek
    çift ``Authorization`` başlığı üretip push'u bozabilir.
    """
    plain = ["push", "origin", f"HEAD:{branch}"]
    code, out = _run_git(plain, cwd=str(repo), timeout=60)
    if code == 0 or not token:
        return code, out
    auth = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return _run_git(
        ["-c", f"http.extraheader=AUTHORIZATION: basic {auth}", *plain],
        cwd=str(repo), timeout=60,
    )


def _github_api(method: str, url: str, token: str, payload: dict | None = None) -> tuple[bool, Any, str]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "telegram-indirim-takipci",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8", "replace")
        return True, (json.loads(body) if body else {}), ""
    except urllib.error.HTTPError as exc:
        return False, None, f"HTTP {exc.code} {exc.read().decode('utf-8', 'replace')[:160]}"
    except Exception as exc:  # noqa: BLE001 - ağ hatası ayar komutunu öldürmemeli
        return False, None, f"{type(exc).__name__}: {exc}"


def _push_via_api(path: Path, message: str) -> tuple[str, str]:
    """git yoksa/config depo dışındaysa GitHub Contents API ile güncelle."""
    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
    token = push_token()
    if not repository or not token:
        return "no-repo", "GITHUB_REPOSITORY veya push token'ı yok"
    branch = (os.getenv("CONFIG_PUSH_BRANCH") or os.getenv("GITHUB_REF_NAME") or "main").strip()
    url = f"https://api.github.com/repos/{repository}/contents/{urllib.parse.quote(path.name)}"
    ok, current, _ = _github_api("GET", f"{url}?ref={branch}", token)
    payload = {
        "message": message,
        "content": base64.b64encode(path.read_bytes()).decode(),
        "branch": branch,
    }
    if ok and isinstance(current, dict) and current.get("sha"):
        payload["sha"] = current["sha"]
    ok, _, detail = _github_api("PUT", url, token, payload)
    if not ok:
        return "error", detail or "Contents API isteği başarısız"
    return "pushed", f"{repository}@{branch} (API)"


async def save_config(store: ConfigStore, note: str = "") -> tuple[bool, str]:
    """config.json'ı atomik yaz ve mümkse repo'ya işle: (yazıldı_mı, rapor)."""
    try:
        await asyncio.to_thread(atomic_write_json, store.path, store.config)
    except OSError as exc:
        return False, (f"⚠️ Ayar çalışan botta aktif ama config.json yazılamadı "
                       f"({type(exc).__name__}: {exc}) — yeniden başlatınca kaybolur.")
    status, detail = await asyncio.to_thread(commit_and_push, store.path, note)
    return True, SAVE_STATUS_TEXT.get(status, SAVE_STATUS_TEXT["error"]).format(detail=detail)


# ---------------------------------------------------------------------------
# Çalışan bot'a ayar uygulama
# ---------------------------------------------------------------------------

def apply_runtime_config(config: dict) -> list[str]:
    """Global çalışma ayarlarını config'ten yenile (Telegram bağlantısı gerekmez)."""
    global SMART_HEADER, FAST_NOTIFY, SLOW_REPORT_SECONDS, FAST_POLL, FAST_POLL_SECONDS
    global FILTER_INCLUDE, FILTER_EXCLUDE, FILTER_MODE, ADMIN_IDS
    global FILTER_INCLUDE_ENABLED, FILTER_EXCLUDE_ENABLED
    global DELIVERY_CHAIN, MAX_MEDIA_MB
    global LINK_APPENDIX_MODE, LINK_KINDS, BOT_LINK_KINDS
    global MESSAGE_LINK_LINE, SOURCE_FOOTER, NOTIFY_MEDIA, NOTIFY_BOT_TOKEN
    global SINGLE_MESSAGE, CLEAN_COMMANDS, CLEAN_QUOTE_DUPLICATES
    global DEDUP_ENABLED, DEDUP_WINDOW_SECONDS, DEDUP_SCAN_LIMIT

    notes: list[str] = []
    FILTER_INCLUDE = [normalize(x) for x in config.get("include_keywords") or []]
    FILTER_EXCLUDE = [normalize(x) for x in config.get("exclude_keywords") or []]
    FILTER_MODE = match_mode_of(config)
    filter_state = filter_state_of(config)
    FILTER_INCLUDE_ENABLED = filter_state["include"]
    FILTER_EXCLUDE_ENABLED = filter_state["exclude"]
    ADMIN_IDS = parse_admin_ids(config.get("admin_user_id"))
    DELIVERY_CHAIN = build_delivery_chain(config)
    try:
        MAX_MEDIA_MB = int(config.get("max_media_mb", 25))
    except (TypeError, ValueError):
        log.warning("max_media_mb sayı değil, 25 kabul edildi.")
        MAX_MEDIA_MB = 25
    LINK_APPENDIX_MODE = link_appendix_mode(config)
    LINK_KINDS = link_kinds_for(LINK_APPENDIX_MODE, bot=False)
    BOT_LINK_KINDS = link_kinds_for(LINK_APPENDIX_MODE, bot=True)
    MESSAGE_LINK_LINE = config_flag(config.get("message_link"), True)
    SOURCE_FOOTER = config_flag(config.get("source_footer"), True)
    NOTIFY_MEDIA = config_flag(config.get("notify_media"), True)
    SINGLE_MESSAGE = config_flag(config.get("single_message"), True)
    CLEAN_COMMANDS = config_flag(config.get("clean_commands"), True)
    # Varsayılan kapalı: davranış değişikliği yalnız config.json açtığında
    # üretimde görülür; mevcut testler/altın referans eski düzeni sınamaya devam eder.
    CLEAN_QUOTE_DUPLICATES = config_flag(config.get("clean_quote_duplicates"), False)
    SMART_HEADER = config_flag(config.get("smart_header"), False)
    FAST_NOTIFY = config_flag(config.get("fast_notify"), False)
    FAST_POLL = config_flag(config.get("fast_poll"), True)
    try:
        FAST_POLL_SECONDS = min(30.0, max(1.0, float(config.get("fast_poll_seconds", 3))))
    except (TypeError, ValueError):
        FAST_POLL_SECONDS = 3.0
    try:
        SLOW_REPORT_SECONDS = max(0.0, float(config.get("slow_report_seconds", 8)))
    except (TypeError, ValueError):
        SLOW_REPORT_SECONDS = 8.0
    DEDUP_ENABLED = config_flag(config.get("dedup_enabled"), True)
    try:
        DEDUP_WINDOW_SECONDS = max(1, min(72, int(config.get("dedup_window_hours", 12)))) * 3600
    except (TypeError, ValueError):
        log.warning("dedup_window_hours sayı değil, 12 saat kabul edildi.")
        DEDUP_WINDOW_SECONDS = 12 * 3600
    try:
        DEDUP_SCAN_LIMIT = max(0, min(100, int(config.get("dedup_scan_limit", 100))))
    except (TypeError, ValueError):
        log.warning("dedup_scan_limit sayı değil, 100 kabul edildi.")
        DEDUP_SCAN_LIMIT = 100
    # Bildirim token'ının tek kaynağı environment/Actions secret'ı olsun;
    # config.json içindeki eski notify_bot_token alanı artık kullanılmaz.
    token = str(os.getenv("NOTIFY_BOT_TOKEN", "") or "").strip()
    NOTIFY_BOT_TOKEN = "" if token.lower() in {"null", "none", "yok"} else token

    if not FILTER_INCLUDE_ENABLED:
        notes.append("🔓 Dahili kelime filtresi kapalı; anahtar kelimeler yok sayılıyor"
                     + (" (harici kelimeler yine engeller)." if FILTER_EXCLUDE_ENABLED
                        else " ve harici engelleme de kapalı: her mesaj iletilir."))
    if not FILTER_EXCLUDE_ENABLED:
        notes.append("🚫 Harici kelime filtresi kapalı; engelleme yapılmıyor.")
    return notes


def changed_groups(before: dict, after: dict) -> set[str]:
    """Liste akışında kaynak listesi değiştiyse kaynakları yeniden çöz."""
    return {"sources"} if before.get("source_chats") != after.get("source_chats") else set()


def _lookup_source_id(source_id_map: dict[str, Any], value: Any) -> Any | None:
    key = str(value).strip()
    if key in source_id_map:
        return source_id_map[key]
    if isinstance(value, str) and value.startswith("@"):
        alt = value.lstrip("@")
        if alt in source_id_map:
            return source_id_map[alt]
        low = alt.lower()
        for k, v in source_id_map.items():
            if isinstance(k, str) and k.lstrip("@").lower() == low:
                return v
    return None


def _is_transient_id_error(exc: Exception) -> bool:
    """Ağ/timeout/ServerError gibi geçici hatalar — ID korunmalı."""
    if isinstance(exc, (OSError, ConnectionError, TimeoutError, asyncio.TimeoutError, asyncio.CancelledError)):
        return True
    try:
        from telethon.errors.rpcbaseerrors import InvalidDCError, ServerError, TimedOutError

        if isinstance(exc, (ServerError, TimedOutError, InvalidDCError)):
            return True
    except Exception:
        pass
    return False


def _is_stale_id_error(exc: Exception) -> bool:
    """Kesin geçersiz/erişilemez ID — temizlenip username fallback yapılabilir."""
    if isinstance(exc, ValueError):
        return True
    # Telethon RPC kalıcı hataları: varlık yok / özel / geçersiz
    stale_candidates = (
        getattr(errors, "ChannelInvalidError", None),
        getattr(errors, "ChannelIdInvalidError", None),
        getattr(errors, "ChatInvalidError", None),
        getattr(errors, "ChatIdInvalidError", None),
        getattr(errors, "PeerIdInvalidError", None),
        getattr(errors, "UsernameInvalidError", None),
        getattr(errors, "UsernameNotOccupiedError", None),
        getattr(errors, "InviteHashInvalidError", None),
        getattr(errors, "InviteHashExpiredError", None),
        getattr(errors, "ChatForbiddenError", None),
    )
    stale_types = tuple(t for t in stale_candidates if t is not None)
    if stale_types and isinstance(exc, stale_types):
        return True
    return False


def _is_invite_link(value: Any) -> bool:
    return isinstance(value, str) and bool(_INVITE_LINK_RE.fullmatch(value.strip()))


async def _prefill_missing_ids_from_dialogs(client: TelegramClient, values: list[int | str],
                                            source_id_map: dict[str, Any]) -> int:
    """Eksik ID'leri tek bir get_dialogs çağrısıyla doldur — 47x ResolveUsername'i önler.

    Telegram `get_dialogs` bir RPC'de tüm üye olunan sohbetleri döndürür; username
    eşleştirmesi yerel yapılır, FloodWait tetiklenmez. Sadece @username kaynakları
    için geçerli, invite linkler ayrı kalır.
    """
    missing = [v for v in values if _lookup_source_id(source_id_map, v) is None and not _is_invite_link(v)]
    if not missing:
        return 0
    # Sadece username tabanlı eksikler varsa dene
    try:
        dialogs = await client.get_dialogs()
    except Exception as exc:
        log.warning("Dialog listesi alınamadı (%s: %s) — tek tek username çözümüne düşülüyor",
                    type(exc).__name__, exc)
        return 0
    # username -> peer_id haritası
    username_to_id: dict[str, int] = {}
    for dlg in dialogs or []:
        ent = getattr(dlg, "entity", None)
        if ent is None:
            # Bazı Telethon sürümlerinde dialog direkt entity olabilir
            ent = dlg
        uname = getattr(ent, "username", None)
        if not uname:
            continue
        try:
            pid = utils.get_peer_id(ent)
        except Exception:
            continue
        username_to_id[str(uname).strip().lower()] = pid

    filled = 0
    for v in missing:
        key = str(v).strip()
        # @xxx -> xxx lower
        lookup = key.lstrip("@").lower() if isinstance(v, str) else ""
        pid = username_to_id.get(lookup)
        if pid is not None and _lookup_source_id(source_id_map, v) is None:
            source_id_map[key] = pid
            filled += 1
            log.info("Kaynak ID dialoglardan bulundu: %s -> %s", v, pid)
    if filled:
        log.info("Dialoglardan %s/%s eksik kaynak ID'si dolduruldu", filled, len(missing))
    return filled


async def resolve_sources(client: TelegramClient, config: dict, quiet: bool = False) -> list[str]:
    """Kaynakları tek tek çöz; biri bozuksa diğerleri çalışmaya devam eder."""
    global SOURCES, SOURCE_IDS, SOURCE_FAILURES
    resolved: list[dict[str, Any]] = []
    failures: list[tuple[Any, Exception]] = []
    stored_ids = config.get("source_chat_ids")
    source_id_map: dict[str, Any] = dict(stored_ids) if isinstance(stored_ids, dict) else {}
    values = chat_values(config.get("source_chats"))
    # FloodWait'i tetiklemeden önce dialoglardan toplu ID doldurmayı dene
    if values:
        try:
            await _prefill_missing_ids_from_dialogs(client, values, source_id_map)
        except Exception as exc:
            log.warning("Dialog ön-doldurma atlandı: %s: %s", type(exc).__name__, exc)
    flood_wait_seconds: int | None = None
    flood_wait_exc: Exception | None = None
    for idx, value in enumerate(values):
        key = str(value).strip()
        cached_id = _lookup_source_id(source_id_map, value)
        if cached_id is not None:
            try:
                peer_id, name, entity = await resolve_chat(client, int(cached_id))
                resolved.append({
                    "id": peer_id,
                    "name": name,
                    "requested": value,
                    "username": getattr(entity, "username", None),
                    "joined": not bool(getattr(entity, "left", False)),
                })
                if str(source_id_map.get(key)) != str(peer_id):
                    source_id_map[key] = peer_id
                continue
            except errors.FloodWaitError as exc:
                flood_wait_seconds = max(flood_wait_seconds or 0, int(getattr(exc, "seconds", 0) or 0))
                flood_wait_exc = exc
                failures.append((value, exc))
                log.error("Kaynak çözülemedi (ID ile): %r -> %s: %s", value, type(exc).__name__, exc)
                continue
            except Exception as exc:  # noqa: BLE001 - geçici vs stale ayrımı
                if _is_transient_id_error(exc):
                    log.warning("Kaynak ID %s (%r) geçici hata: %s: %s — ID korunuyor, username denenmeyecek",
                                cached_id, value, type(exc).__name__, exc)
                    failures.append((value, exc))
                    continue
                if _is_stale_id_error(exc):
                    log.warning("Kaynak ID %s (%r) artık erişilemiyor: %s: %s -> kullanıcı adıyla yeniden denenecek",
                                cached_id, value, type(exc).__name__, exc)
                    # Stale ID'yi temizle — username başarılı olursa yeni ID eklenecek
                    for k in list(source_id_map.keys()):
                        if str(source_id_map[k]) == str(cached_id) and str(k).strip().lstrip("@").lower() == key.lstrip("@").lower():
                            source_id_map.pop(k, None)
                    if key in source_id_map and str(source_id_map.get(key)) == str(cached_id):
                        source_id_map.pop(key, None)
                else:
                    # Kesin stale değil ve transient değil — güvenli tarafta kal, ID'yi silme
                    log.warning("Kaynak ID %s (%r) geçici/bilinmeyen hata: %s: %s — ID korunuyor",
                                cached_id, value, type(exc).__name__, exc)
                    failures.append((value, exc))
                    continue
        # FloodWait aktifken yeni username denemesi yapma — ID'li kaynaklar zaten yukarıda denendi
        # Invite linkler ResolveUsername değil, ayrı limitte; park etme
        if flood_wait_seconds is not None and not _is_invite_link(value):
            failures.append((value, flood_wait_exc))
            log.warning("Kaynak atlandı (FloodWait park %ss): %r", flood_wait_seconds, value)
            continue
        try:
            peer_id, name, entity = await resolve_chat(client, value)
        except errors.FloodWaitError as exc:
            flood_wait_seconds = int(getattr(exc, "seconds", 0) or 0)
            flood_wait_exc = exc
            failures.append((value, exc))
            log.error("Kaynak çözülemedi: %r -> %s: %s", value, type(exc).__name__, exc)
            log.warning("FloodWait %ss: kalan bilinmeyen kaynaklar bu oturumda park edildi; "
                        "bilinen ID'lerle devam ediliyor (sonraki açılışta tekrar denenecek)",
                        flood_wait_seconds)
            continue
        except Exception as exc:  # noqa: BLE001 - tek kanal tüm bot'u düşürmemeli
            failures.append((value, exc))
            log.error("Kaynak çözülemedi: %r -> %s: %s", value, type(exc).__name__, exc)
            continue
        resolved.append({
            "id": peer_id,
            "name": name,
            "requested": value,
            "username": getattr(entity, "username", None),
            "joined": not bool(getattr(entity, "left", False)),
        })
        if source_id_map.get(key) != peer_id:
            source_id_map[key] = peer_id
    # Kalıcı haritayı yalnızca mevcut source_chats anahtarlarıyla sınırla (stale temizlenir)
    # Fakat FloodWait'te park edilenler için eski ID varsa koru — yoksa sonraki restart'ta tekrar username dener
    if source_id_map:
        allowed_keys = {str(v).strip() for v in values}
        pruned: dict[str, Any] = {}
        for k, v in source_id_map.items():
            if k in allowed_keys:
                pruned[k] = v
        # Park edilenlerin eski ID'sini kaybetmemek için pruned üzerine başarılıları ekle
        # Zaten pruned içinde var, resolved ile güncellendi; parked'ler pruned'da kalır
        config["source_chat_ids"] = pruned
        # Başarılıları kesin ekle (pruned sonrası override)
        for item in resolved:
            rk = str(item["requested"]).strip()
            if config["source_chat_ids"].get(rk) != item["id"]:
                config["source_chat_ids"][rk] = item["id"]
    elif resolved:
        config["source_chat_ids"] = {str(item["requested"]).strip(): item["id"] for item in resolved}
    SOURCES, SOURCE_FAILURES = resolved, failures
    SOURCE_IDS = {item["id"] for item in resolved}
    notes: list[str] = []
    for item in resolved:
        flag = "" if item["joined"] else "  <-- ÜYE DEĞİLSİN, mesaj gelmez!"
        if not quiet:
            log.info("Kaynak hazır: %-28s id=%-15s istenen=%s%s",
                     item["name"], item["id"], item["requested"], flag)
        elif not item["joined"]:
            notes.append(f"⚠️ {item['name']} kanalına üye değilsin; mesaj gelmez.")
    # Ayırt edici rapor: tanımlı / çözülen / park edilen
    defined = len(values)
    resolved_cnt = len(resolved)
    parked_cnt = sum(1 for _, exc in failures if isinstance(exc, errors.FloodWaitError))
    other_fail = len(failures) - parked_cnt
    if failures:
        for value, exc in failures:
            log.warning("Kaynak atlandı: %r (%s)", value, exc)
        if parked_cnt:
            wait_h = f" ({humanize(flood_wait_seconds)} bekle)" if flood_wait_seconds else ""
            notes.append(f"⚠️ {parked_cnt} kaynak FloodWait nedeniyle park edildi{wait_h}: "
                         + ", ".join(str(v) for v, e in failures if isinstance(e, errors.FloodWaitError))[:200])
        if other_fail:
            notes.append(f"⚠️ {other_fail} kaynak çözülemedi: "
                         + ", ".join(str(value) for value, exc in failures if not isinstance(exc, errors.FloodWaitError))[:200])
        if resolved_cnt < defined:
            # Kısmi başlatmayı normal gibi gösterme — açıkça raporla
            log.warning("⚠️ KISMI BAŞLATMA: %s/%s kaynak dinleniyor, %s park edildi, %s hatalı",
                        resolved_cnt, defined, parked_cnt, other_fail)
            notes.append(f"ℹ️ Tanımlı {defined}, çözülen {resolved_cnt}, park edilen {parked_cnt}, hatalı {other_fail}")
    else:
        if not quiet:
            log.info("Kaynaklar tamam: %s/%s çözüldü", resolved_cnt, defined)
    return notes


async def resolve_control(client: TelegramClient, config: dict,
                          quiet: bool = False) -> tuple[list[str], bool]:  # noqa: ARG001 - dış API uyumu
    """Kontrol sohbetlerini çöz; Kayıtlı Mesajlar her zaman açık kalır.

    Dönen ikinci değer: istenen sohbetlerden en az biri çözülebildi mi?
    `quiet` ileriye dönük sessiz mod için ayrılmıştır.
    """
    global CONTROL_IDS, CONTROL_NAMES
    ids: set[int] = set()
    names: list[str] = []
    control_config = config.get("control_chat", "me")
    values = control_config if isinstance(control_config, (list, tuple)) else [control_config]
    for value in chat_values(values):
        try:
            peer_id, name, _ = await resolve_chat(client, value)
        except Exception as exc:  # noqa: BLE001
            log.error("control_chat çözülemedi: %r -> %s. Kayıtlı Mesajlar (me) kullanılıyor.", value, exc)
            continue
        ids.add(peer_id)
        names.append(f"{name} [{peer_id}]")
    if SELF_ID is not None:
        ids.add(SELF_ID)  # Kayıtlı Mesajlar her zaman kontrol edilebilir
    resolved = bool(names)
    if not names:
        names.append("me (Kayıtlı Mesajlar)")
    CONTROL_IDS, CONTROL_NAMES = ids, names
    # Not: açılışta çağıran taraf loglar; burada tekrar loglamıyoruz.
    return [f"🎛 Komut sohbeti: {', '.join(names)}"], resolved


async def resolve_destination(client: TelegramClient, config: dict, quiet: bool = False) -> list[str]:  # noqa: ARG001 - dış API uyumu
    """Hedef sohbeti çöz; olmazsa Kayıtlı Mesajlar'a düş.

    `quiet` ileriye dönük sessiz mod için ayrılmıştır.
    """
    global DESTINATION, DESTINATION_ID, DESTINATION_LABEL
    value = parse_chat_value(config.get("destination", "me"))
    try:
        peer_id, name, _ = await resolve_chat(client, value)
        DESTINATION, DESTINATION_ID = peer_id, peer_id
        DESTINATION_LABEL = f"{name} [{peer_id}]"
    except Exception as exc:  # noqa: BLE001
        log.error("destination çözülemedi: %r -> %s. Kayıtlı Mesajlar'a düşülüyor.", value, exc)
        DESTINATION, DESTINATION_ID = "me", None
        DESTINATION_LABEL = "me (Kayıtlı Mesajlar)"
        return [f"⚠️ destination çözülemedi ({value}); Kayıtlı Mesajlar'a düşüldü."]
    return [f"🎯 Hedef: {DESTINATION_LABEL}"]


async def resolve_chat_groups(
    client: TelegramClient, config: dict, groups: Iterable[str], strict: bool = False,
) -> tuple[list[str], str | None]:
    """Sohbet gerektiren ayarları (yeniden) çöz.

    ``strict=True`` iken (Telegram'dan gelen ayar değişikliği) çözülemeyen
    destination/control_chat ölümcül sayılır ve değişiklik geri alınır; açılışta
    ise uyarı verip Kayıtlı Mesajlar'a düşmek daha güvenlidir.
    """
    notes: list[str] = []
    if "sources" in groups:
        notes += await resolve_sources(client, config)
        if not SOURCE_IDS:
            return notes, ("hiçbir kaynak çözülemedi; source_chats listesini ve "
                           "hesabın kanallara üyeliğini kontrol et")
    if "destination" in groups:
        notes += await resolve_destination(client, config)
        if strict and DESTINATION_ID is None:
            return notes, "destination çözülemedi; sohbet ID'sini/kullanıcı adını kontrol et"
    if "control" in groups:
        control_notes, resolved = await resolve_control(client, config)
        notes += control_notes
        if strict and not resolved:
            return notes, "control_chat çözülemedi; komutları kullanamazsın, değer geri alındı"
    return notes, None


def note_command_messages(chat_id: int, message_ids: Sequence[int]) -> list[int]:
    """Son komut alışverişinin mesaj ID'lerini kaydet; silinecek eskileri döndür.

    Yalnızca komut diyaloğuna ait mesajlar (kullanıcının komutu + botun yanıtı)
    buraya yazılır. İndirim bildirimleri bu kayda hiç girmediği için temizlikten
    etkilenmez; böylece yanlışlıkla fırsat mesajı silinmez.
    """
    chat = int(chat_id or 0)
    current = [int(item) for item in message_ids if item]
    previous = COMMAND_MESSAGES.get(chat, [])
    COMMAND_MESSAGES[chat] = current
    return [item for item in previous if item not in current]


def analyze_titles(titles: Iterable[str], *, keep_everything: bool = False) -> dict[str, Any]:
    """Başlıkları tara; kelime ve ilk kelime frekanslarını çıkar.

    Aynı başlık birden çok kanaldan gelmişse bir kez sayılır (tekrar
    birleştirme); böylece tek bir kampanya tüm istatistiği bozmaz.
    """
    words: Counter[str] = Counter()
    first_words: Counter[str] = Counter()
    seen: set[str] = set()
    counts = {"titles": 0, "unique": 0, "duplicates": 0, "filtered": 0}
    for title in titles:
        text = (title or "").strip()
        if not text:
            continue
        counts["titles"] += 1
        key = normalize(text)
        if key in seen:
            counts["duplicates"] += 1
            continue
        seen.add(key)
        counts["unique"] += 1
        # Sayılar ve işlevsiz kelimeler "elenen" sayılır: kapsam notu bunu gösterir.
        tokens = title_tokens(text, keep_everything=True)
        kept = content_tokens(tokens, keep_everything=keep_everything)
        counts["filtered"] += len(tokens) - len(kept)
        words.update(kept)
        if kept:
            first_words[kept[0]] += 1
    return {"counts": counts, "words": words, "first_words": first_words}


# ---------------------------------------------------------------------------
# Ana akış
# ---------------------------------------------------------------------------

async def auto_restart_scheduler(
    client: TelegramClient, destination: Any, pat: str, delay_seconds: int, *,
    prepare_restart: Callable[[], Any] | None = None,
    resume_after_failure: Callable[[], Any] | None = None,
) -> None:  # noqa: ARG001 - dış API uyumu
    """Eski Actions çalışmasını güvenli biçimde devret, sonra yenisini başlat.

    ``prepare_restart`` cursor'ı kalıcılaştırıp devam eden teslimatları
    boşaltmalıdır. Yeni workflow başlatılamazsa ``resume_after_failure`` eski
    sürecin kaynakları dinlemeye devam etmesini sağlar.
    """
    await asyncio.sleep(delay_seconds)
    log.info("Takipçi oturumu yenileniyor (%s doldu).", humanize(delay_seconds))
    if prepare_restart is not None:
        prepared = await prepare_restart()
        if not prepared:
            log.error("Otomatik yenileme iptal: kaynak cursor'ı güvenle kaydedilemedi.")
            return
    ok, detail = await dispatch_next_run(pat)
    if not ok:
        log.error("Otomatik yenileme başlatılamadı: %s", detail)
        if resume_after_failure is not None:
            await resume_after_failure()


async def heartbeat() -> None:
    """Actions log'unda 'hâlâ dinliyor mu?' sorusunu cevaplayan periyodik satır."""
    while True:
        await asyncio.sleep(1800)
        log.info(
            "Kalp atışı: %s çalışıyor | görülen=%d eşleşen=%d iletilen=%d"
            " | tarama aralığı=%.0f sn (son yoklama %.1f sn, FloodWait=%d)"
            " | Bot API 429=%d (retry bekleme %.1f sn, kilit bekleme %.1f sn)",
            humanize(time.time() - STARTED_AT), STATS["seen"], STATS["matched"], STATS["forwarded"],
            POLL_TELEMETRY["interval"], POLL_TELEMETRY["last_elapsed"], int(POLL_TELEMETRY["flood_count"]),
            gate_stats(NOTIFY_BOT_TOKEN)["rate_limit_count"],
            gate_stats(NOTIFY_BOT_TOKEN)["retry_wait_seconds"],
            gate_stats(NOTIFY_BOT_TOKEN)["lock_wait_seconds"],
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Telegram indirim mesajı filtresi")
    parser.add_argument(
        "--check", action="store_true",
        help="Telegram'a bağlanmadan secret/config kontrolü yap ve raporu yazdır",
    )
    parser.add_argument("--config", default=None, help="config.json yolu (varsayılan: $CONFIG_FILE veya config.json)")
    return parser


def run_check(config_path: str | None) -> int:
    try:
        config = load_config(config_path)
    except FileNotFoundError:
        print(f"✗ config.json bulunamadı (bakılan yol: {config_path or os.getenv('CONFIG_FILE', 'config.json')})", flush=True)
        return 1
    except json.JSONDecodeError as exc:
        print(f"✗ config.json geçerli bir JSON değil: {exc}", flush=True)
        return 1

    problems = check_environment(config)
    print_report(config, problems)
    return 1 if problems else 0


async def main(argv: Sequence[str] | None = None) -> int:
    # Filtre/link bayraklarını apply_runtime_config yazar; burada yalnızca
    # kimlik atanır (diğer adlar yalnızca okunur, `global` gerekmez).
    global SELF_ID

    args = build_parser().parse_args(argv)
    if args.check:
        return run_check(args.config)

    config = load_config(args.config)
    problems = check_environment(config)
    print_report(config, problems)
    if problems:
        for problem in problems:
            log.error("Yapılandırma sorunu: %s", problem)
        raise SystemExit(1)

    # Config nesnesi çalışan filtre ve geçici liste taslakları için kullanılır.
    store = ConfigStore(config_path(args.config), config)

    gh_pat = os.getenv("GH_PAT", "").strip()
    auto_restart = bool(config.get("auto_restart", True))
    restart_minutes = max(1, int(os.getenv("RESTART_AFTER_MINUTES", "330")))

    # Telegram bağlantısı gerektirmeyen ayarlar (filtre, zincir, bayraklar).
    for note in apply_runtime_config(config):
        log.info("%s", note)

    client = TelegramClient(
        StringSession(os.environ["SESSION_STRING"].strip()),
        int(os.environ["API_ID"].strip()),
        os.environ["API_HASH"].strip(),
    )

    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit(
            "SESSION_STRING geçersiz veya oturumun süresi dolmuş. "
            "generate_session.py ile yeni bir session üretip GitHub secret'ını güncelle."
        )

    me = await client.get_me()
    SELF_ID = me.id
    log.info("Bağlanıldı: %s (id=%s)", display_name(me), me.id)

    if LINK_APPENDIX_MODE == "smart":
        log.info("Bağlantı ekleri: akıllı mod — ürün adresi '%s' yazısına gömülür "
                 "(buton, gizli hyperlink ya da görünür URL); alıntıdaki bağlantılar yedek kalır.",
                 PRODUCT_LINK_TEXT)
    elif LINK_APPENDIX_MODE == "all":
        log.info("Bağlantı ekleri: tüm gizli linkler iletinin sonuna yazılacak.")
    if MESSAGE_LINK_LINE:
        log.info("Orijinal mesajın t.me adresi kaynak grup adının içine gömülecek "
                 "(ayrı '🔗 %s' satırı yok).", MESSAGE_LINK_LABEL)
    if NOTIFY_BOT_TOKEN:
        log.info("Bildirim biçimi (basit düzen): en üstte ürün başlığı → '%s' → altında mesaj "
                 "OLDUĞU GİBİ → en altta kalın kaynak adı%s; mesajın adresi o adın içine "
                 "gömülüdür. Tekrarlı paylaşımlarda kaynak adı satırı kalkar, yerine çoklu "
                 "paylaşım notu yazılır (📌 N kere paylaşıldı: <gruplar>; her ad kendi "
                 "mesajına bağlı).",
                 PRODUCT_LINK_TEXT, " + medya" if NOTIFY_MEDIA else "")
    if CLEAN_COMMANDS:
        log.info("Komut temizliği açık: yeni komutta önceki komut/yanıt silinir, "
                 "bildirimlere dokunulmaz.")
    if DEDUP_ENABLED:
        log.info("Tekrar birleştirme açık: aynı ürün sorgusu %s boyunca tek mesajda toplanır, "
                 "açılışta hedefteki son %d ileti taranır.",
                 humanize(DEDUP_WINDOW_SECONDS), DEDUP_SCAN_LIMIT)
    else:
        log.info("Tekrar birleştirme kapalı: her eşleşme ayrı mesaj olarak iletilir.")

    # --- Kaynakları, kontrol sohbetini ve hedefi çöz.
    _orig_ids = dict(store.config.get("source_chat_ids") or {}) if isinstance(store.config.get("source_chat_ids"), dict) else {}
    notes, fatal = await resolve_chat_groups(
        client, config, {"sources", "destination", "control"},
    )
    for note in notes:
        log.info("%s", note)
    if fatal:
        raise SystemExit(f"{fatal}; source_chats listesini ve hesap üyeliğini kontrol et.")
    # Kaynak ID'lerini kalıcılaştır — sonraki açılışlarda username FloodWait'ini önler
    _new_ids = store.config.get("source_chat_ids")
    if isinstance(_new_ids, dict) and _new_ids != _orig_ids:
        try:
            await asyncio.to_thread(atomic_write_json, store.path, store.config)
            log.info("Kaynak ID'leri kalıcılaştırıldı: %s/%s", len(_new_ids), len(chat_values(store.config.get("source_chats"))))
            # GitHub kalıcılığı: bir sonraki runner aynı ID'leri görsün; sessiz, hatası startup'ı öldürmez
            # Yalnızca gerçek repo checkout'unda push et — testteki /tmp dosyası repo dışı
            if push_token() and git_repo_root(store.path.parent) is not None:
                _st, _dt = await asyncio.to_thread(commit_and_push, store.path, "kaynak ID'leri güncellendi")
                log.info("Kaynak ID push: %s %s", _st, _dt)
        except Exception as exc:  # noqa: BLE001 - kalıcılık hatası bot'u durdurmasın
            log.warning("Kaynak ID'leri yazılamadı: %s: %s", type(exc).__name__, exc)
    not_joined = [item["name"] for item in SOURCES if not item["joined"]]
    if not_joined:
        log.warning(
            "Bu hesaptan üye olunmayan kaynaklar var, bunlardan mesaj GELMEZ: %s", ", ".join(not_joined)
        )
    if ADMIN_IDS:
        log.info("Komut kullanabilecek admin ID'leri: %s", sorted(ADMIN_IDS))
    elif len(CONTROL_IDS) > 1:
        log.warning("admin_user_id boş: grup komutları kimse tarafından kullanılamaz.")
    if not NOTIFY_BOT_TOKEN:
        log.warning(
            "NOTIFY_BOT_TOKEN secret/ortam değişkeni tanımlı değil: mesajları kendi hesabın "
            "gönderdiği için Telegram BİLDİRİM ÜRETMEZ. Sesli bildirim istiyorsan @BotFather'dan "
            "bir bot oluşturup gruba ekle ve token'ı NOTIFY_BOT_TOKEN secret'ında sakla."
        )

    def is_control_event(event: events.NewMessage.Event) -> bool:
        """Komut yalnızca kontrol sohbetinden gelirse işlenir."""
        if isinstance(event, PrivateControlEvent):
            return config_flag(store.config.get("private_control"), False)
        if config_flag(store.config.get("private_control"), False):
            return event.chat_id == SELF_ID  # emergency fallback, not the offer group
        return event.chat_id in CONTROL_IDS

    def is_admin_event(event: events.NewMessage.Event) -> bool:
        """Kayıtlı Mesajlar'a yazan hesabın sahibi; gruptaysa admin listesi geçerli."""
        if isinstance(event, PrivateControlEvent):
            return event.sender_id in (ADMIN_IDS | {SELF_ID}) and event.chat_id == event.sender_id
        if event.chat_id == SELF_ID:
            return True
        return event.sender_id in ADMIN_IDS

    async def delete_command_messages(chat_id: int, message_ids: Sequence[int]) -> None:
        """Komut sohbetindeki eski komut/yanıt mesajlarını sil.

        Yalnızca ``COMMAND_MESSAGES`` kaydından gelen ID'ler silinir; indirim
        bildirimleri bu kayda girmediği için silinmeleri mümkün değildir.
        Silme başarısız olsa bile (ör. yetki yok) komut akışı devam eder.
        """
        ids = [int(item) for item in message_ids if item]
        if not ids:
            return
        try:
            await client.delete_messages(int(chat_id or 0), ids, revoke=True)
        except Exception as exc:  # noqa: BLE001 - silinemezse komut yine çalışsın
            log.warning("Eski komut mesajları silinemedi (chat=%s, adet=%d): %s: %s",
                        chat_id, len(ids), type(exc).__name__, exc)
            return
        STATS["cleaned_commands"] += len(ids)
        log.debug("Komut sohbeti temizlendi: chat=%s, %d mesaj.", chat_id, len(ids))

    async def control_reply(event: events.NewMessage.Event, text: str, *,
                            limit: int = 3500,
                            stale: Sequence[int] = ()) -> list[int]:
        """Komut yanıtını gönder ve önceki komut alışverişini sil.

        ``stale``: bu alışveriş sırasında oluşup sonradan silinmesi gereken ek
        mesajlar (ör. /analiz ilerleme mesajı). Bildirimler hiçbir zaman
        silinmez; temizlik yalnızca komut diyaloğuyla sınırlıdır.
        """
        sent_ids: list[int] = []
        for chunk in chunk_text(text, limit):
            sent_ids += sent_message_ids(await event.reply(chunk))
        # Keep private dialogue history; never let the user-client cleanup
        # delete a DM offer or a bot message with a colliding numeric ID.
        if isinstance(event, PrivateControlEvent) or not CLEAN_COMMANDS:
            return sent_ids
        chat_id = int(getattr(event, "chat_id", 0) or 0)
        current = [int(getattr(event, "id", 0) or 0), *sent_ids]
        outdated = note_command_messages(chat_id, current)
        outdated += [int(item) for item in stale if item]
        await delete_command_messages(chat_id, outdated)
        return sent_ids

    async def delete_account_copy(sent_ids: Sequence[int], source_name: str) -> bool:
        """Bildirim botu gönderdikten sonra hedefteki hesap kopyasını sil.

        Böylece grupta her fırsat tek mesaj olarak kalır: botun bildirimi
        (telefonuna uyarı düşen mesaj). Bildirim gidemezse kopya SİLİNMEZ;
        mesajsız kalmaktansa iki kopya iyidir. Silme başarısız olursa yalnızca
        uyarı loglanır, ileti yine başarılı sayılır.
        """
        if not sent_ids or DESTINATION_ID is None:
            return False
        try:
            await client.delete_messages(DESTINATION, list(sent_ids), revoke=True)
        except Exception as exc:  # noqa: BLE001 - silinemezse mesaj kalsın
            log.warning("Hesap kopyası silinemedi (kaynak=%s): %s: %s",
                        source_name, type(exc).__name__, exc)
            return False
        STATS["cleaned"] += 1
        log.info("Hesap kopyası silindi (%d mesaj); grupta bot bildirimi kaldı (kaynak=%s).",
                 len(sent_ids), source_name)
        return True

    async def apply_setting_change(before: dict,
                                   groups: set[str] | None = None) -> tuple[list[str], str | None]:
        """Ayar değişikliğini çalışan bot'a uygula.

        ``groups`` verilmezse hangi sohbet gruplarının değiştiği ``before`` ile
        karşılaştırılarak bulunur. Dönen ikinci değer doluysa değişiklik
        geçersiz sayılır ve çağıran taraf geri alır.
        """
        notes = apply_runtime_config(store.config)
        targets = groups if groups is not None else changed_groups(before, store.config)
        resolved, fatal = await resolve_chat_groups(client, store.config, targets, strict=bool(targets))
        return notes + resolved, fatal

    settings_lock = asyncio.Lock()
    private_api = BotAPI(NOTIFY_BOT_TOKEN) if NOTIFY_BOT_TOKEN else None
    # Optional feature: failure to initialize must not prevent channel listening.
    tracker = None
    if private_api and config_flag(store.config.get("private_control"), False):
        try:
            from product_tracker import ProductTracker

            def persist_products(path, data):
                # Contents API avoids dirtying the shared checkout while config
                # commits/rebases use it. The next runner checks out this snapshot.
                with GIT_WRITE_LOCK:
                    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
                    token = push_token()
                    repo = git_repo_root(store.path.parent)
                    branch = (os.getenv("CONFIG_PUSH_BRANCH", "").strip()
                              or (current_branch(repo) if repo else "")
                              or os.getenv("GITHUB_REF_NAME", "").strip())
                    if not repository or not token or not branch:
                        return False, "GitHub kalıcı kayıt ortamı eksik."
                    url = f"https://api.github.com/repos/{repository}/contents/{path.name}"
                    ok, remote, detail = _github_api("GET", f"{url}?ref={urllib.parse.quote(branch, safe='')}", token)
                    if not ok or not isinstance(remote, dict) or not remote.get("sha"):
                        log.warning("Ürün takip dosyasının GitHub sürümü okunamadı: %s",
                                    detail or "beklenmeyen yanıt")
                        return False, "Takip dosyasının GitHub sürümü okunamadı."
                    payload = {
                        "message": "config: ürün takip güncellemesi",
                        "branch": branch, "sha": remote["sha"],
                        "content": base64.b64encode((json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()).decode(),
                    }
                    ok, _, detail = _github_api("PUT", url, token, payload)
                    if not ok:
                        log.warning("Ürün takip ayarı GitHub'a yazılamadı: %s", detail)
                    return ok, "Ürün takip ayarı GitHub'a yazıldı." if ok else "GitHub yazımı başarısız."

            tracker = ProductTracker(
                store.path.with_name("product_watches.json"), private_api, persist_products,
                owner_id=SELF_ID,
                header_formatter=lambda cents, title: build_header_line(header_price_text(cents / 100), title),
            )
        except Exception:
            log.exception("Ürün takibi başlatılamadı; kanal dinleme devam ediyor.")

    async def prepare_private_offer(item):
        # Turning off/changing the filter also cancels queued, unsent copies.
        decision = dm_decision(
            store.config, item["match_text"], item.get("strike") or (),
            facts=item.get("dm_facts"),
        )
        _log_dm_decision(decision)
        if not decision.should_send:
            return None
        info = item.get("bot")
        if not info:
            ids = item.get("account_ids") or []
            if not ids:
                raise BotAPIError("Grup kopyası bulunamadı.")
            message = await client.get_messages(item["destination"], ids=ids[0])
            if message is None:
                raise BotAPIError("Grup kopyası artık yok.")
            text = getattr(message, "message", "") or ""
            media = getattr(message, "media", None)
            info = {
                "message_id": message.id,
                "kind": "media" if media and not isinstance(media, types.MessageMediaWebPage) else "text",
                "text": text,
                "entities": bot_api_entities(message, text),
                "keyboard": item.get("keyboard"),
            }
            if info["kind"] == "media" and not bot_history_ids_shared(item["destination"]):
                log.warning("Normal gruptaki hesap medya ID'si Bot API'ye taşınamaz; özel bildirim metin olarak gönderilecek.")
                info["kind"] = "text"
                info["message_id"] = None
                info["text"] = text or "📎 Medyalı fırsat: gruptaki hesap kopyasına bak."
        return private_copy_request(SELF_ID, item["destination"], info)

    private_offers = PrivateOfferQueue(private_api, prepare_private_offer)

    def enqueue_private_offer(event, info):
        if not private_api or not dm_filters.has_active_rules(store.config):
            return
        sanitized = sanitize_message(event)
        text = message_text(sanitized)
        strike = price_filter.strike_spans_from_entities(text, entities_for_text(sanitized, text))
        decision = dm_decision(store.config, text, strike)
        _log_dm_decision(decision)
        if decision.should_send:
            private_offers.submit({
                "destination": DESTINATION_ID, "match_text": text, "strike": strike,
                "dm_facts": decision.facts,
                "bot": info.get("bot"), "account_ids": info.get("account_ids"),
                "keyboard": build_inline_keyboard(event),
            })

    def dm_status():
        words = [str(value) for value in telegram_list_items(store.config, "dm_keywords")]
        excluded = [str(value) for value in telegram_list_items(store.config, "dm_exclude_keywords")]
        enabled = config_flag(store.config.get("dm_enabled"), False)
        threshold = dm_price_threshold(store.config)
        product_rules = dm_filters.product_price_rules(store.config)
        dm_sending_enabled = (enabled and bool(words)) or threshold is not None or bool(product_rules)
        show_values = lambda values: format_value_preview(values, limit=12) if values else "(boş)"
        rule_status = lambda is_open, count: f"{'AÇIK' if is_open else 'KAPALI'} · {count} kural"
        return (
            "📩 KİŞİSEL DM FİLTRELERİ\n"
            f"🎯 DM gönderimi: {'AÇIK' if dm_sending_enabled else 'KAPALI'}\n"
            f"🎯 dmdahil: {rule_status(enabled, len(words))}\n"
            f"DM dahil · Kelimeler: {show_values(words)}\n"
            f"🔕 dmharic: {rule_status(bool(excluded), len(excluded))} · dmdahil/dmfiyat için ortak engel\n"
            f"DM hariç: {show_values(excluded)}\n"
            f"💰 dmfiyat: {rule_status(threshold is not None, 1 if threshold is not None else 0)} · "
            f"Eşik: {price_filter.format_price(threshold) if threshold is not None else '(kapalı)'}\n"
            f"🏷️ Ürün adı < fiyat: {rule_status(bool(product_rules), len(product_rules))}\n"
            f"   Kayıtlar: {show_values([rule.canonical for rule in product_rules])}\n"
            "Ürün adı/fiyat kuralları diğer DM listelerinden bağımsızdır; başlık ve gerçek ürün fiyatı birlikte eşleşir.\n"
            "dmharic, yalnız dmdahil ve dmfiyat'ı engeller. Grup gönderimi ve tekrar birleştirmesi değişmez.\n"
            f"Bu oturum: gönderilen {private_offers.sent}, hata {private_offers.failed}, "
            f"kuyrukta {private_offers.queue.qsize()}, taşma {private_offers.dropped}\n"
            "/ekle → dmdahil / dmharic / dmfiyat / ürün adı < fiyat → değer → /kaydet\n"
            "/cikar → filtre → kayıt numarası veya tam kural → /kaydet\n"
            "/dmfiltreac - dmdahil grubunu aç\n"
            "/dmfiltrekapat - dmdahil grubunu kapat"
        )

    async def private_channel_health() -> str:
        """Özel komut kanalının teşhisi; /test ile görünür (token asla yazılmaz)."""
        if not NOTIFY_BOT_TOKEN:
            return ("🔎 Özel komut kanalı: KAPALI — NOTIFY_BOT_TOKEN tanımlı değil.\n"
                    "Komutlar Kayıtlı Mesajlar'dan işlenir; bot sana özel mesaj atamaz.")
        lines = ["🔎 Özel komut kanalı (bildirim botu):"]
        try:
            me = await private_api.call("getMe", {}) or {}
            username = me.get("username")
            lines.append(f"• Bot: @{username}" if username
                         else "• Bot: kullanıcı adı alınamadı (token geçersiz olabilir).")
        except BotAPIError as exc:
            lines.append(f"• Bot: alınamadı — token geçersiz olabilir (hata {exc.code}).")
        except Exception as exc:
            lines.append(f"• Bot: alınamadı ({type(exc).__name__}).")
        try:
            webhook = await private_api.call("getWebhookInfo", {}) or {}
            if webhook.get("url"):
                lines.append("• Webhook: VAR — getUpdates ile çelişir; özel komutlar "
                             "çalışmaz. Webhook'u kaldırıp takipçiyi yeniden başlat.")
            else:
                lines.append("• Webhook: yok")
        except Exception as exc:
            lines.append(f"• Webhook: alınamadı ({type(exc).__name__}).")
        health = poll_health()
        status = health.get("status")
        label = {
            "off": "başlatılmadı (private_control kapalı)",
            "starting": "başlatılıyor",
            "running": "çalışıyor",
            "webhook": "webhook var — kapalı",
            "unauthorized": "token geçersiz (401) — kapalı",
            "conflict": "409 çakışma — aynı token'ı başka bir süreç kullanıyor",
        }.get(status, str(status))
        lines.append(f"• Poller: {label}")
        if health.get("last_error"):
            lines.append(f"• Son hata: {health['last_error']}")
        lines.append("Bot ilk özel mesajı kendisi atamaz: botun özel sohbetinde /start yaz "
                     "ve botu engellemediğinden emin ol.")
        return "\n".join(lines)

    async def save_dm_setting(event, *, action=None, raw="", enabled=True):
        async with settings_lock:
            before = store.snapshot()
            if action is not None:
                existing = store.config.get("dm_keywords", [])
                try:
                    words = edit_dm_keywords(existing, raw, action)
                except ValueError as exc:
                    await control_reply(event, str(exc))
                    return
                if words == existing:
                    pending = peek_pending(pending_key(event))
                    if pending and pending.get("stage") == "dm_words":
                        drop_pending(pending_key(event))
                    await control_reply(event, "ℹ️ Bu kelimeler zaten listede; ayarlar değişmedi.\n" + dm_status())
                    return
                store.config["dm_keywords"] = words
                # Adding a new interest opts in; removal preserves a paused
                # filter and switches it off when the last keyword is removed.
                enabled = bool(words) and (action == "add" or config_flag(before.get("dm_enabled"), False))
            store.config["dm_enabled"] = enabled
            ok, note = await save_config(store, "Kişisel bildirim ayarı güncellendi")
            if not ok:
                store.restore(before)
                await control_reply(event, "❌ Kaydedilemedi; önceki ayar korundu.\n" + note)
                return
            pending = peek_pending(pending_key(event))
            if pending and pending.get("stage") == "dm_words":
                drop_pending(pending_key(event))
            await control_reply(event, "✅ Anında uygulandı. Grup ayarları değişmedi.\n" + dm_status() + "\n" + note)

    async def handle_dm_command(event, command, rest):
        if not isinstance(event, PrivateControlEvent) or event.sender_id != SELF_ID:
            await control_reply(event, "🎯 Kişisel ayarlar yalnızca hesap sahibinin botla özel sohbetinden yönetilir.")
            return
        if command == COMMAND_DM_STATUS:
            await control_reply(event, dm_status() + (
                "\n\nBu komut yalnızca bilgi gösterir. Kural eklemek / çıkarmak için /ekle veya "
                "/cikar → dmdahil / dmharic / dmfiyat / ürün adı < fiyat seçip /kaydet kullanın."
                if rest else ""
            ))
        elif command == COMMAND_DM_FILTER_OPEN:
            enabled = True
            if enabled and not store.config.get("dm_keywords"):
                await control_reply(event, "Önce /ekle → dmdahil ile kelimelerini ekleyip /kaydet. Boş liste hiçbir şeyi göndermez.")
                return
            await save_dm_setting(event, enabled=enabled)
        elif command == COMMAND_DM_FILTER_CLOSE:
            await save_dm_setting(event, enabled=False)
        else:
            await control_reply(event, f"Bilinmeyen komut: {command}\n\n{HELP_TEXT}")

    async def can_edit_list(event, field):
        if (field in DM_LIST_FIELDS or field in {DM_PRICE_FIELD, DM_PRODUCT_PRICE_FIELD}) and (
            not isinstance(event, PrivateControlEvent) or event.sender_id != SELF_ID
        ):
            await control_reply(event, "🎯 Kişisel ayarlar yalnızca hesap sahibinin botla özel sohbetinden yönetilir.")
            return False
        return True

    async def save_pending_change(event: events.NewMessage.Event,
                                 key: PendingKey) -> None:
        """Taslağı çakışma kontrolünden geçir, uygula ve sadece şimdi GitHub'a yaz."""
        async with settings_lock:
            pending = peek_pending(key)
            if pending is None:
                await control_reply(event, "ℹ️ Kaydedilecek bekleyen bir değişiklik yok. Önce /ekle veya /cikar.")
                return
            if pending.get("stage") == "webdetakip":
                # product_tracker kayıtlarını product_watches.json'da anında
                # kalıcı tutar; /ekle akışındaki ayrı /kaydet adımı yoktur.
                drop_pending(key)
                await control_reply(event, "ℹ️ Ürün takip kayıtları product_watches.json'da; bu akışta kayıt anında kalıcılaşır, ayrı /kaydet adımı yok. Ayar taslağı kapatıldı. Takip menüsü: /webtakip")
                return
            if pending.get("stage") != "confirm":
                await control_reply(event, "ℹ️ İşlem henüz tamamlanmadı. Önce istenen liste/değer seçimini yap veya /iptal.")
                return

            field = str(pending.get("field") or "")
            base = pending.get("base_config") or {}
            draft = pending.get("draft_config") or {}
            if field not in TELEGRAM_LIST_FIELDS or field not in draft:
                drop_pending(key)
                await control_reply(event, "❌ Taslak geçersiz olduğu için iptal edildi. Yeniden /ekle veya /cikar ile başla.")
                return

            if not await can_edit_list(event, field):
                return
            before = store.snapshot()
            if before.get(field) != base.get(field):
                drop_pending(key)
                await control_reply(event,
                    f"⚠️ {TELEGRAM_LIST_META[field]['title']} taslağı hazırlanırken başka bir değişiklik yapılmış.\n"
                    "Güncel listeyi korudum; lütfen /ekle veya /cikar ile yeniden başla."
                )
                return

            candidate = copy.deepcopy(before)
            candidate[field] = copy.deepcopy(draft[field])
            if field == "dm_keywords":
                candidate["dm_enabled"] = bool(candidate[field]) and (
                    pending.get("action") == "add" or config_flag(before.get("dm_enabled"), False)
                )
            groups = changed_groups(before, candidate)
            store.config = candidate
            notes, fatal = await apply_setting_change(before)
            if fatal:
                store.restore(before)
                apply_runtime_config(store.config)
                if groups:
                    await resolve_chat_groups(client, store.config, groups, strict=False)
                drop_pending(key)
                await control_reply(event,
                    f"❌ Değişiklik uygulanamadı: {fatal}\n"
                    "Önceki ayarlar korundu; taslağı iptal ettim. Grup/kanal bilgisini kontrol edip yeniden dene."
                )
                return

            ok, save_note = await save_config(store, f"{TELEGRAM_LIST_META[field]['title']} güncellendi")
            if not ok:
                store.restore(before)
                apply_runtime_config(store.config)
                if groups:
                    await resolve_chat_groups(client, store.config, groups, strict=False)
                set_pending(key, **pending)
                await control_reply(event, "\n".join([
                    "❌ Değişiklik dosyaya yazılamadı; çalışan ayarlar geri yüklendi.",
                    save_note,
                    "Taslak korundu. Dosya erişimini düzelttikten sonra /kaydet ile tekrar deneyebilir veya /iptal edebilirsin.",
                ]))
                return

            drop_pending(key)
            log.info("Telegram liste ayarı kaydedildi: %s (%s)", field, pending.get("action"))
            await control_reply(event, "\n".join([
                "✅ Değişiklik kaydedildi ve çalışan ayarlara uygulandı.",
                f"📂 {TELEGRAM_LIST_META[field]['title']}",
                f"📊 Güncel kayıt sayısı: {len(telegram_list_items(store.config, field))}",
                *notes,
                save_note,
            ]))

    async def apply_filter_toggle(event: events.NewMessage.Event, action: str, target: str) -> None:
        """Filtreyi aç/kapat, config'e yaz ve GitHub'a gönder (anında uygulanır).

        Dahili ve harici filtre ayrı ayrı yönetilir; ikisi de kapatılabilir.
        Dosyaya yazma başarısız olursa önceki durum geri yüklenir.
        """
        async with settings_lock:
            before = store.snapshot()
            ok, candidate, note = stage_filter_toggle(before, action, target)
            if not ok:
                drop_pending(pending_key(event))
                await control_reply(event, note)
                return

            store.config = candidate
            apply_runtime_config(store.config)
            what = "Filtreler" if target == "both" else FILTER_TARGET_META[target]["title"] + " filtresi"
            ok_save, save_note = await save_config(
                store, f"{what} {'açıldı' if action == 'open' else 'kapatıldı'}",
            )
            if not ok_save:
                store.restore(before)
                apply_runtime_config(store.config)
                await control_reply(event, "❌ Filtre değişikliği kaydedilemedi; önceki durum geri yüklendi.\n" + save_note)
                return

            drop_pending(pending_key(event))
            log.info("Filtre %s: %s (hedef=%s)", "açıldı" if action == "open" else "kapatıldı",
                     what, target)
            await control_reply(event, note + "\n" + save_note)

    async def handle_filter_command(event: events.NewMessage.Event, action: str, rest: str) -> None:
        """`/tgfiltreac` ve `/tgfiltrekapat`: argümanla hemen uygula, argüman yoksa sor."""
        key = pending_key(event)
        pending = peek_pending(key)
        if pending and pending.get("stage") == "confirm":
            await control_reply(event,
                "📝 Önce bekleyen liste taslağını sonuçlandır.\n"
                "✅ /kaydet ile kaydet veya ↩️ /iptal ile vazgeç."
            )
            return

        raw = rest.strip()
        if not raw:
            set_pending(key, **new_filter_edit(action))
            await control_reply(event, build_filter_toggle_prompt(action, store.config))
            return

        target = resolve_filter_target(raw)
        if target is None:
            # Soru açık kalsın: kullanıcı 1/2/3 yazabilir ya da /iptal edebilir.
            set_pending(key, **new_filter_edit(action))
            await control_reply(event, "\n".join([
                f"❌ '{raw}' anlaşılmadı.",
                "1 (dahili), 2 (harici) veya 3 (ikisi) yazabilirsin.",
                "",
                build_filter_toggle_prompt(action, store.config),
            ]))
            return

        await apply_filter_toggle(event, action, target)

    async def list_query_matches(event: events.NewMessage.Event, targets: list[Any],
                                 per_source: int, query: str) -> None:
        """Tüm kaynakların son ``per_source`` mesajında ``query`` geçenleri listele.

        Tarama, istatistik analiziyle aynı okuma yolunu kullanır (salt okuma;
        hiçbir mesaj gönderilmez/değiştirilmez). Tarama boyunca "Analiz
        başladı" ilerleme mesajı kaynak kaynak güncellenir, rapor gelince
        silinir. Sonuç satırları ``ürün adı (tam) - fiyat`` biçimindedir; uzun
        listeler ``control_reply`` tarafından birden çok mesaja bölünür.
        """
        pattern = analysis_query_pattern(query)
        started = time.time()
        status = await start_analysis_scan(
            event, targets, per_source,
            purpose="Bitince eşleşen ürünleri (ürün adı - fiyat) göndereceğim.",
            query=query,
        )
        status_id = analysis_status_id(status)
        texts: list[str] = []
        failures: list[str] = []
        matches = 0
        for index, item in enumerate(targets, start=1):
            try:
                async for message in client.iter_messages(item["id"], limit=per_source):
                    text = message_text(message)
                    texts.append(text)
                    if text and pattern.search(normalize(text)):
                        matches += 1
            except Exception as exc:  # noqa: BLE001 - tek kaynak taramayı durdurmasın
                failures.append(f"{item['name']} ({type(exc).__name__})")
                log.warning("Geçmiş okunamadı (%s): %s: %s", item["name"], type(exc).__name__, exc)
            await update_analysis_scan(status, index, len(targets),
                                       f"{matches} eşleşme · sorgu: '{query}'")
        lines = build_query_lines(texts, pattern)
        log.info("Sorgu listesi: '%s' → %d eşleşme (%d mesaj, %d kaynak).",
                 query, len(lines), len(texts), len(targets))
        header = [
            f"🔎 '{query}' içeren mesajlar: {len(lines)} ürün",
            f"• Kaynak: {len(targets)} grup · kaynak başına son {per_source} mesaj"
            f" · süre: {humanize(time.time() - started)}",
        ]
        if failures:
            header.append(f"• ⚠️ Okunamayan kaynak: {', '.join(failures)}")
        if not lines:
            await control_reply(event, "\n".join(header + ["", "Eşleşen mesaj bulunamadı."]),
                                stale=[status_id])
            return
        shown = lines[:ANALYSIS_QUERY_MAX_LINES]
        body = header + ["", *shown]
        if len(lines) > len(shown):
            body.append(f"… ilk {len(shown)} gösteriliyor; daha az sonuç için sayı/sorguyu daralt.")
        body.append("ℹ️ Satır biçimi: ürün adı (tam) - fiyat · fiyat satırı varsa en düşük fiyat.")
        await control_reply(event, "\n".join(body), stale=[status_id])

    async def analyze_history(event: events.NewMessage.Event, rest: str) -> None:
        """Kaynak geçmişinin başlıklarını tara; kelime istatistiklerini gönder.

        Yalnızca mesajın ilk anlamlı satırı (başlık) okunur; her kaynaktan en
        fazla ``per_source`` mesaj alınır. Tek bir kaynak okunamazsa tarama
        durmaz, raporda "okunamayan kaynak" olarak listelenir.
        Sorgu verilirse (``/analiz 1000 tcl 65``) istatistik yerine eşleşen
        ürünlerin listesi döner (bkz. ``list_query_matches``).
        """
        per_source, keep_everything, query = parse_analysis_request(rest)

        targets = [item for item in SOURCES if item.get("joined")]
        if not targets:
            await control_reply(event, "ℹ️ Taranacak kaynak yok. Önce /kaynaklar ile listeyi kontrol et.")
            return

        if query:
            await list_query_matches(event, targets, per_source, query)
            return

        started = time.time()
        # İlerleme mesajı bilinçli olarak temizlik kaydına girmez: rapor gelince
        # "stale" listesiyle silinir, böylece ekranda yalnızca istatistik raporu
        # kalır. Uzun taramaların çalıştığı bu mesajdan görülür.
        status = await start_analysis_scan(
            event, targets, per_source,
            purpose="Uzun sürebilir; bitince iki istatistiği göndereceğim.",
        )
        status_id = analysis_status_id(status)

        titles: list[str] = []
        failures: list[str] = []
        for index, item in enumerate(targets, start=1):
            try:
                async for message in client.iter_messages(item["id"], limit=per_source):
                    title = message_title(message_text(message))
                    if title:
                        titles.append(title)
            except Exception as exc:  # noqa: BLE001 - tek kaynak taramayı durdurmasın
                failures.append(f"{item['name']} ({type(exc).__name__})")
                log.warning("Geçmiş okunamadı (%s): %s: %s", item["name"], type(exc).__name__, exc)
            await update_analysis_scan(status, index, len(targets), f"{len(titles)} başlık")

        analysis = analyze_titles(titles, keep_everything=keep_everything)
        log.info("Kelime analizi: %d başlık, %d tekil, %d kaynak, %s.",
                 analysis["counts"]["titles"], analysis["counts"]["unique"], len(targets),
                 "tüm kelimeler" if keep_everything else "filtreli")
        report = build_analysis_report(
            analysis,
            config=store.config,
            sources=len(targets),
            per_source=per_source,
            keep_everything=keep_everything,
            elapsed=time.time() - started,
            failures=failures,
        )
        await control_reply(event, report, stale=[status_id])

    async def test_source_access(event: events.NewMessage.Event, rest: str) -> None:
        """Geçmiş erişimini, ham mesaj bütünlüğünü ve yerel kullanım yolunu sınar.

        Her kaynakta son tek mesaj okunur; mesaj gönderilmez/değiştirilmez ve
        toplu test sırasında medya dosyaları indirilmez. Seçili kaynakta ham
        metin uzunluğu/hash'i, entity aralıkları, medya/link metadatası ve
        compose_message kuru çalıştırması ayrıca raporlanır.
        """
        requested = rest.strip().strip("'\"")
        all_sources = list(SOURCES)
        targets = all_sources
        unresolved: list[tuple[Any, Exception]] = list(SOURCE_FAILURES)
        if requested:
            needle = requested.casefold()
            target = None
            if needle.isdigit():
                index = int(needle)
                if 1 <= index <= len(all_sources):
                    target = all_sources[index - 1]
            for item in all_sources:
                aliases = {
                    str(item.get("requested", "")).strip().lstrip("@").casefold(),
                    str(item.get("username", "") or "").strip().lstrip("@").casefold(),
                    str(item.get("name", "")).strip().casefold(),
                    str(item.get("id", "")).strip().casefold(),
                }
                if needle in aliases or requested.casefold() in {
                    str(item.get("requested", "")).strip().casefold(),
                    str(item.get("username", "") or "").strip().casefold(),
                }:
                    target = item
                    break
            if target is not None:
                targets = [target]
                unresolved = []
            else:
                failed = next((entry for entry in SOURCE_FAILURES
                               if needle in {str(entry[0]).strip().lstrip("@").casefold(),
                                             str(entry[0]).strip().casefold()}), None)
                if failed:
                    targets = []
                    unresolved = [failed]
                else:
                    await control_reply(
                        event,
                        "❌ Kaynak bulunamadı. /kaynaklar ile sırayı gör; kullanım: "
                        "/kaynaktest [sıra | @kullanıcı_adı | -100... ID].",
                    )
                    return

        tested = 0
        readable = 0
        joined = 0
        raw_checked = 0
        usable = 0
        show_full_excerpt = bool(requested)
        lines = [
            "🧪 KAYNAK MESAJ ERİŞİM TESTİ",
            "━━━━━━━━━━━━━━━━━━━━",
            f"• Denenen çözülmüş kaynak: {len(targets)}",
            "• Her kaynakta yalnızca en yeni 1 mesaj okunur; tüm geçmiş taranmaz.",
            "• Ham mesaj değiştirilmeden denetlenir; kullanım testi yalnızca yerel kuru çalıştırmadır.",
            "",
        ]
        live_counts = STATS.get("source_seen", {})
        if not isinstance(live_counts, dict):
            live_counts = {}
        for item in targets:
            tested += 1
            name = " ".join(str(item.get("name") or item.get("id") or "kaynak").split())
            configured_as = " ".join(str(item.get("requested") or item.get("id") or "").split())
            source_label = f"{name} [{configured_as}]" if configured_as else name
            live_count = int(live_counts.get(item["id"], 0) or 0)
            is_joined = bool(item.get("joined"))
            if is_joined:
                joined += 1
            try:
                latest = None
                async for message in client.iter_messages(item["id"], limit=1):
                    latest = message
                    break
                readable += 1
                if latest is None:
                    lines.append(
                        f"✅ {source_label} — geçmiş okunuyor, henüz mesaj yok · canlı event: {live_count}"
                    )
                    continue

                message_id = getattr(latest, "id", None)
                date = getattr(latest, "date", None)
                age = ""
                try:
                    timestamp = date.timestamp() if hasattr(date, "timestamp") else float(date)
                    age = f" · {humanize(time.time() - timestamp)} önce"
                except (TypeError, ValueError, OverflowError, OSError):
                    pass

                raw_field = getattr(_as_message(latest), "message", None)
                raw_text = message_text(latest)
                empty_media_message = raw_field is None and not raw_text
                raw_exact = isinstance(raw_field, str) and raw_text == raw_field
                raw_integrity_ok = raw_exact or empty_media_message
                media = getattr(_as_message(latest), "media", None)
                media_descriptor = bot_media_descriptor(latest)
                if media is None:
                    media_label = "yok"
                elif media_descriptor:
                    size = int(media_descriptor.get("size", 0) or 0)
                    media_label = f"{media_descriptor['kind']} · {size} B"
                else:
                    media_label = type(media).__name__

                entities = message_entities(latest)
                text_length = utf16_length(raw_text)
                valid_entities = sum(
                    1 for entity in entities
                    if int(getattr(entity, "offset", 0) or 0) >= 0
                    and int(getattr(entity, "length", 0) or 0) > 0
                    and int(getattr(entity, "offset", 0) or 0)
                    + int(getattr(entity, "length", 0) or 0) <= text_length
                )
                links = extract_links(latest)
                product_title = _search_query(latest)
                dry_run = compose_message(
                    latest,
                    limit=CAPTION_LIMIT - 24 if media else MESSAGE_LIMIT - 100,
                    link_kinds=LINK_KINDS,
                    source_name=name if SOURCE_FOOTER else None,
                )
                layout_ok = bool(dry_run["text"] or media or links)
                if dry_run["header"]:
                    # Basit düzen: üst satır ("₺56 — ürün adı") en üstte,
                    # mesajın aslı hemen altında.
                    layout_ok = (
                        layout_ok
                        and dry_run["content"].startswith(dry_run["header"] + "\n\n")
                        and dry_run["body"] == message_text(sanitize_message(latest))
                    )
                can_use = (
                    raw_integrity_ok and valid_entities == len(entities) and layout_ok
                    and bool(raw_text.strip() or media or links)
                )
                if can_use:
                    usable += 1
                raw_checked += int(raw_integrity_ok)

                mark = "✅" if is_joined else "⚠️ ÜYE DEĞİL"
                membership_note = "" if is_joined else " · yeni mesajlar gelmeyebilir"
                suffix = f" · #{message_id}" if message_id is not None else ""
                lines.append(
                    f"{mark} {source_label} — son mesaj{suffix}{age} · canlı event: {live_count}{membership_note}"
                )
                digest = hashlib.sha256(raw_text.encode("utf-8", "replace")).hexdigest()[:12]
                raw_status = (
                    "birebir OK" if raw_exact
                    else "metin yok (medya mesajı)" if empty_media_message
                    else "ham metin alanı yok/uyuşmuyor"
                )
                lines.append(
                    f"   Ham: {raw_status} · {len(raw_text)} karakter · sha256 {digest}"
                    f" · entity {valid_entities}/{len(entities)} · link {len(links)} · medya {media_label}"
                )
                layout_note = f" · başlık {product_title or 'bulunamadı'}"
                if SMART_HEADER:
                    # Üst satır = "fiyat - ürün adı"; `başlık` fiyatsız hâlidir
                    # ve Google/Akakçe/Cimri aramaları ile dedup o değeri kullanır.
                    layout_note += f" · üst satır {(dry_run['header'] or 'yok')[:70]}"
                lines.append(
                    "   Kuru kullanım: " + ("✅ kullanılabilir" if can_use else "⚠️ kontrol gerekli")
                    + layout_note
                )
                if show_full_excerpt and raw_text:
                    excerpt_limit = 800
                    excerpt = raw_text[:excerpt_limit]
                    if len(raw_text) > excerpt_limit:
                        excerpt += "\n… (rapor için kısaltıldı; tam metin hash/uzunlukla kontrol edildi)"
                    indented = excerpt.replace("\n", "\n      ")
                    lines.append(f"   Ham mesaj örneği:\n      {indented}")
            except Exception as exc:  # noqa: BLE001 - bir kaynak diğerlerini engellemesin
                log.warning("Kaynak erişim testi başarısız (%s): %s: %s",
                            name, type(exc).__name__, exc)
                lines.append(
                    f"❌ {source_label} — geçmiş/ham mesaj kullanımı kontrol edilemedi"
                    f" ({type(exc).__name__}) · canlı event: {live_count}"
                )

        for value, exc in unresolved:
            lines.append(f"❌ {value} — kaynak çözülemedi ({type(exc).__name__})")
        lines += [
            "",
            f"• Geçmiş okuma: {readable}/{tested} · ham içerik kontrolü: {raw_checked}/{readable}"
            f" · kullanılabilir kuru deneme: {usable}/{readable}",
            f"• Hesaptan üye olunan: {joined}/{tested} · canlı event sayacı bot bu çalışmaya başladığından beri tutulur.",
            "ℹ️ Bu test kaynak mesajını göndermez/değiştirmez ve medya dosyasını indirmez; ham alanı,"
            " entity/link/medya metadatasını ve mesajın yerel hazırlanma yolunu kontrol eder.",
            "ℹ️ Canlı iletim bot çalışırken ve hesap kanala üyeyken yapılır; bot kapalıyken kaçan mesajlar otomatik geri alınmaz.",
        ]
        await control_reply(event, "\n".join(lines))

    async def handle_settings_command(event: events.NewMessage.Event,
                                      command: str, rest: str) -> None:
        """Üç izinli liste için taslak akışını yönet."""
        key = pending_key(event)
        pending = peek_pending(key)

        if command in CMD_SETTINGS_MENU:
            await control_reply(event, build_main_menu_text())
            return

        if command in CMD_SETTINGS_ADD or command in CMD_SETTINGS_REMOVE:
            if pending and pending.get("stage") == "confirm":
                await control_reply(event,
                    "📝 Önce bekleyen taslağı sonuçlandır.\n"
                    "✅ /kaydet ile kaydet veya ↩️ /iptal ile vazgeç."
                )
                return
            if rest.strip():
                await control_reply(event,
                    "Komutu tek başına gönder: /ekle veya /cikar.\n"
                    "Ardından listelerden birini seçip değeri ayrı mesaj olarak yaz."
                )
                return

            action = "add" if command in CMD_SETTINGS_ADD else "remove"
            set_pending(key, **new_telegram_edit(action, store.config))
            webtakip_count = _webtakip_count(store)
            await control_reply(event, build_list_category_prompt(action, store.config, webtakip_count))
            return

        if command in CMD_SETTINGS_SAVE:
            if pending is None:
                await control_reply(event, "ℹ️ Kaydedilecek bekleyen bir değişiklik yok. Başlamak için /ekle veya /cikar.")
            else:
                await save_pending_change(event, key)
            return

        if command in CMD_SETTINGS_REVERT:
            if pending is None:
                await control_reply(event, "ℹ️ İptal edilecek bekleyen bir işlem yok.")
            else:
                drop_pending(key)
                await control_reply(event, "↩️ İşlem iptal edildi. Taslak silindi; ayarlar ve GitHub değişmedi.")
            return

        return

    async def handle_pending_message(event: events.NewMessage.Event,
                                     raw: str) -> bool:
        """Liste menüsü/değer bekleniyorsa bu mesajı taslak akışına uygula."""
        key = pending_key(event)
        item = peek_pending(key)
        if item is None:
            return False

        text = raw.strip()
        if item.get("stage") == "dm_words":
            if not isinstance(event, PrivateControlEvent) or event.sender_id != SELF_ID:
                return False
            await save_dm_setting(event, action=item["action"], raw=text)
            return True
        if item.get("stage") == "filter":
            action = str(item.get("action") or "open")
            target = resolve_filter_target(text)
            if target is None:
                set_pending(key, **item)
                await control_reply(event, "\n".join([
                    "❌ Seçimi anlayamadım. 1, 2, 3 yazabilir ya da filtre adını yazabilirsin.",
                    "",
                    build_filter_toggle_prompt(action, store.config),
                ]))
                return True
            await apply_filter_toggle(event, action, target)
            return True

        if item.get("stage") == "category":
            field = resolve_telegram_list(text)
            if field is None:
                set_pending(key, **item)
                await control_reply(event, "❌ Seçimi anlayamadım. 1–8 yazabilir; 7 (webdetakip) eski seçimi korur. Listedeki adı da geçer.\n\n"
                                    + build_list_category_prompt(str(item.get("action")), store.config, _webtakip_count(store)))
                return True

            if field == WEBTAKIP_FIELD:
                if not isinstance(event, PrivateControlEvent) or event.sender_id != SELF_ID:
                    await control_reply(event, "🎯 Kişisel ayarlar yalnızca hesap sahibinin botla özel sohbetinden yönetilir.")
                    return True
                if tracker is None:
                    drop_pending(key)
                    await control_reply(event, "❌ Ürün takibi şu an kullanılamıyor (bildirim botu yapılandırması eksik).")
                    return True
                item["stage"] = "webdetakip"
                item["field"] = WEBTAKIP_FIELD
                set_pending(key, **item)
                if str(item.get("action")) == "remove":
                    await tracker.job("/webtakipler", event.chat_id, "")
                    await control_reply(event, "Çıkarılacak ürünün /webtakipler listesindeki numarasını gönder (ör. 3). İptal: /iptal")
                else:
                    await control_reply(event, "Akakçe ürün detay linkini gönder (…akakce.com/…-fiyati,ID.html). Hedef fiyatı bir sonraki mesajda istenecek. İptal: /iptal")
                return True

            if not await can_edit_list(event, field):
                return True
            config = item.get("draft_config") or store.config
            items = telegram_list_items(config, field)
            if item.get("action") == "remove" and not items:
                drop_pending(key)
                await control_reply(event, f"ℹ️ {TELEGRAM_LIST_META[field]['title']} listesi boş; çıkarılacak kayıt yok.")
                return True

            item["stage"] = "value"
            item["field"] = field
            set_pending(key, **item)
            await control_reply(event, build_list_value_prompt(str(item.get("action")), field, config))
            return True

        if item.get("stage") == "webdetakip":
            if tracker is None:
                drop_pending(key)
                await control_reply(event, "❌ Ürün takibi şu an çalışmıyor (bildirim botu yapılandırması eksik).")
                return True
            if not isinstance(event, PrivateControlEvent) or event.sender_id != SELF_ID:
                await control_reply(event, "🎯 Kişisel ayarlar yalnızca hesap sahibinin botla özel sohbetinden yönetilir.")
                return True
            action = str(item.get("action") or "add")
            ok = True
            if action == "add":
                if not text.startswith(("https://www.akakce.com/", "https://akakce.com/")):
                    await control_reply(event, "Akakçe ürün detay linki bekleniyor (…akakce.com/…-fiyati,ID.html). İptal: /iptal")
                    return True
                ok = await tracker.handle(event, text, existing_pending=False)
            else:
                try:
                    number = int(text.split()[0])
                except (ValueError, IndexError):
                    await control_reply(event, "Çıkarılacak ürünün /webtakipler listesindeki numarasını gönder (ör. 3). İptal: /iptal")
                    return True
                ok = await tracker.handle(event, f"/webtakipsil {number}", existing_pending=False)
            # product_tracker kendi kayıtlarını product_watches.json'da anında
            # kalıcı tutar; bu nedenle ayar taslağına ayrı bir /kaydet adımı yok.
            drop_pending(key)
            if not ok:
                await control_reply(event, "⚠️ İşlem anlaşılamadı; lütfen Akakçe linki veya geçerli bir ürün numarası gönder.")
            return True

        if item.get("stage") == "value":
            action = str(item.get("action") or "")
            field = str(item.get("field") or "")
            if not await can_edit_list(event, field):
                return True
            draft = item.get("draft_config") or store.config
            ok, candidate, values, message = stage_telegram_list_change(draft, field, action, text)
            if not ok:
                set_pending(key, **item)
                prompt = build_list_value_prompt(action, field, draft) if field in TELEGRAM_LIST_FIELDS else ""
                await control_reply(event, "\n".join(part for part in [message, prompt] if part))
                return True

            if action == "add" and field == "source_chats":
                resolved_values = []
                for value in values:
                    try:
                        peer_id, _, _ = await resolve_chat(client, value)
                        # Never persist a private invite token in config/Git history.
                        resolved_values.append(peer_id if isinstance(value, str) and _INVITE_LINK_RE.fullmatch(value.strip()) else value)
                    except Exception as exc:  # noqa: BLE001 - geçersiz kaynak taslağa alınmasın
                        set_pending(key, **item)
                        await control_reply(event, "\n".join([
                            f"❌ Kaynak çözülemedi ({type(exc).__name__}).",
                            "Hiçbir kayıt taslağa alınmadı. Daveti takip hesabıyla açıp önce katıl; "
                            "onay veya ücretli abonelik gerekiyorsa tamamlanmasını bekle.",
                            "Linkin geçerli ve hesabın erişiminin açık olduğunu kontrol et.",
                            "",
                            build_list_value_prompt(action, field, draft),
                        ]))
                        return True
                if resolved_values != values:
                    # Re-stage with IDs so duplicate handling and confirmation use
                    # exactly the values that will be saved, never the invite.
                    ok, candidate, values, message = stage_telegram_list_change(
                        draft, field, action, "\n".join(map(str, resolved_values)),
                    )
                    if not ok:
                        set_pending(key, **item)
                        await control_reply(event, message)
                        return True

            item["stage"] = "confirm"
            item["draft_config"] = candidate
            item["field"] = field
            item["value"] = values
            set_pending(key, **item)
            await control_reply(event, build_list_change_confirmation(action, field, values, candidate, message))
            return True

        if item.get("stage") == "confirm":
            choice = resolve_confirmation_choice(text)
            if choice == "save":
                await save_pending_change(event, key)
                return True
            if choice == "cancel":
                drop_pending(key)
                await control_reply(event, "↩️ İşlem iptal edildi. Taslak silindi; ayarlar ve GitHub değişmedi.")
                return True
            set_pending(key, **item)
            await control_reply(event, "📝 Taslak hazır. ✅ /kaydet (veya kaydet) ile kaydet; ↩️ /iptal (veya iptal) ile vazgeç.")
            return True

        drop_pending(key)
        return False

    # --- İletim yolları -----------------------------------------------------
    # Korumalı (noforwards) kanallarda forward ve copy patlar; o yüzden sırayla
    # denenir: forward -> copy -> medyayı indirip yeniden yükle -> sadece metin -> link.

    def source_of(event: events.NewMessage.Event) -> dict[str, Any] | None:
        """Kaynak kaydını bul (kaynak adı, kullanıcı adı, t.me linki için)."""
        return next((item for item in SOURCES if item["id"] == event.chat_id), None)

    def offer_link(event: events.NewMessage.Event) -> str | None:
        return build_message_link(event, source_of(event)) if MESSAGE_LINK_LINE else None

    async def send_forward(event: events.NewMessage.Event) -> Any:
        return await client.forward_messages(DESTINATION, event.message, from_peer=event.chat_id)

    async def send_copy(event: events.NewMessage.Event) -> Any:
        """Mesajı biçimiyle birlikte yeniden gönder.

        Gizli hyperlink'ler entity olarak korunur. Kaynaktaki ``reply_markup``
        kullanıcı hesabından gönderilemediği için (inline klavyeler yalnızca
        botlara açıktır) buton linkleri metne yazılır. Orijinal mesajın adresi
        en alttaki kaynak adının içine gömülür (ad yazılamazsa
        ``🔗 Mesajı Gör: <t.me>`` satırı geri gelir).
        """
        message = event.message
        media = getattr(message, "media", None)
        is_webpage = isinstance(media, types.MessageMediaWebPage)
        link = offer_link(event)
        source_record = source_of(event)
        footer_name = (source_record or {}).get("name") if SOURCE_FOOTER else None
        if media and not is_webpage:
            composed = compose_message(
                event, limit=CAPTION_LIMIT - 24, link_kinds=LINK_KINDS,
                message_link=link, source_name=footer_name,
            )
            return await client.send_file(
                DESTINATION,
                media,
                caption=composed["text"],
                formatting_entities=composed["entities"],
                force_document=False,
            )
        composed = compose_message(
            event, limit=MESSAGE_LIMIT - 100, link_kinds=LINK_KINDS,
            message_link=link, source_name=footer_name,
        )
        return await client.send_message(
            DESTINATION,
            composed["text"],
            formatting_entities=composed["entities"],
            link_preview=False,
        )

    async def send_media(event: events.NewMessage.Event) -> Any:
        """Medyayı indirip hedefe SIFIRDAN yükle (forward kısıtını atlar).

        Önemli: indirilen veriyi düz ``bytes`` olarak ``send_file``a vermek
        fotoğrafın "unnamed" adlı bir belgeye dönüşmesine yol açar (Telethon
        dosya adını ``getattr(file, 'name', 'unnamed')`` ile tahmin eder).
        Bu yüzden uzantılı isimli bir akış kullanılır.
        """
        message = event.message
        if not getattr(message, "media", None) or isinstance(message.media, types.MessageMediaWebPage):
            raise ValueError("mesajda indirilebilir medya yok")
        size = getattr(getattr(message, "file", None), "size", None) or 0
        if MAX_MEDIA_MB and size and size > MAX_MEDIA_MB * 1024 * 1024:
            raise ValueError(f"medya {size // (1024 * 1024)} MB, sınır {MAX_MEDIA_MB} MB")
        data = await client.download_media(message, bytes)
        if not data:
            raise ValueError("medya indirilemedi")
        # Aynı teslim içinde bildirim botu da aynı medyayı kullanacaksa
        # ikinci kez indirilmesin (bkz. notify_offer).
        _media_cache = MEDIA_DOWNLOAD_CACHE.get()
        if _media_cache is not None:
            try:
                _media_cache[(int(event.chat_id), int(event.id))] = data
            except (TypeError, ValueError):
                pass
        source_record = source_of(event)
        footer_name = (source_record or {}).get("name") if SOURCE_FOOTER else None
        composed = compose_message(
            event, limit=CAPTION_LIMIT - 24, link_kinds=LINK_KINDS,
            message_link=offer_link(event), source_name=footer_name,
        )
        return await client.send_file(
            DESTINATION,
            media_buffer(data, media_upload_name(message)),
            caption=composed["text"] or None,
            formatting_entities=composed["entities"],
            attributes=reupload_attributes(message),
            force_document=False,
        )

    async def send_text_only(event: events.NewMessage.Event) -> Any:
        # Gövdeye ek olarak gizli linkler (varsa) ve kaynak adına gömülü mesaj
        # adresi eklenir; ürün linki bir şekilde kaçsa bile tek dokunuşla fırsata
        # ulaşılır (ad yazılamazsa "🔗 Mesajı Gör" satırı geri gelir).
        source_record = source_of(event)
        footer_name = (source_record or {}).get("name") if SOURCE_FOOTER else None
        composed = compose_message(
            event, limit=MESSAGE_LIMIT - 400, link_kinds=LINK_KINDS,
            message_link=offer_link(event), source_name=footer_name,
        )
        if not composed_has_content(composed):
            raise ValueError("mesajda metin yok")
        has_media = bool(getattr(event.message, "media", None))
        note = "\n\n⚠️ Kaynak medyayı korumalı işaretlediği için medya iletilemedi." if has_media else ""
        return await client.send_message(
            DESTINATION,
            composed["text"] + note,
            formatting_entities=composed["entities"],
            link_preview=False,
        )

    async def send_link_card(event: events.NewMessage.Event) -> Any:
        """Son çare: kaynak adı + t.me bağlantısı. Ekranda görülebilir tek şey budur."""
        link = build_message_link(event, source_of(event))
        if not link:
            raise ValueError("bu sohbet türü için t.me bağlantısı üretilemiyor")
        source_record = source_of(event)
        footer_name = (source_record or {}).get("name") if SOURCE_FOOTER else None
        composed = compose_message(
            event, limit=2000, link_kinds=LINK_KINDS,
            message_link=link, source_name=footer_name,
            hidden_links=False,  # bu ileti biçimsiz gider; URL metinde görünmeli
        )
        if composed_has_content(composed):
            body = composed["text"]
        else:
            body = f"🔗 {STATS['last_match_source'] or 'kaynak'} kanalındaki mesaj:\n{link}"
            if composed["appendix"]:
                body += f"\n{composed['appendix']}"
            body += "\n(medya korumalı olduğu için iletilemedi, bağlantıdan açabilirsin)"
        return await client.send_message(DESTINATION, body[:MESSAGE_LIMIT], link_preview=False)

    SENDERS = {
        "forward": send_forward,
        "copy": send_copy,
        "media": send_media,
        "text": send_text_only,
        "link": send_link_card,
    }

    account_send_lock = asyncio.Lock()

    async def send_account_with_retry(mode: str, event: Any) -> Any:
        # Account fallback must not silently drop an offer on FloodWait either.
        # Keep other offer sends behind this wait rather than stampeding Telegram.
        async with account_send_lock:
            while True:
                try:
                    return await SENDERS[mode](event)
                except (errors.FloodWaitError, errors.SlowModeWaitError) as exc:
                    wait = max(1, exc.seconds) + 1
                    log.warning("Hesap gönderimi hız sınırı (%s): %s sn beklenip aynı fırsat yeniden denenecek.",
                                mode, wait)
                    await asyncio.sleep(wait)

    # --- Tekrar birleştirme --------------------------------------------------
    # Aynı başlık aynı anda 3-5 kanaldan gelebilir; gönderen rezervasyon koyar,
    # diğerleri bekleyip tekrara düşer. Rozet aynı ürünün kilidiyle birleştirilir;
    # kayıp hedefte bu olayın kendisi yeni bildirim gönderebilir.

    async def dedup_before_send(
        key: str, title: str, source_name: str | None = None,
        footer_name: str | None = None,
        offer: tuple[float | None, str] | None = None,
        event: Any = None,
        source_link: str | None = None,
    ) -> tuple[str, Any, str]:
        """Karar ver: ``("send", token, hedef_anahtar)`` yeni gönderim, ``("dup", None, hedef_anahtar)`` tekrar.

        Rezervasyon sahibi takılırsa bekleyen devralır; fırsatın kaybolmasındansa
        fazladan bir mesaj iyidir (fail-open). ``source_name``, tekrarlarda son
        bloğa yazılacak grup adı listesine eklenir. ``footer_name`` ilk
        bildirimin en altındaki kaynak grup adı satırıdır; tekrar yakalandığında
        bu satır kaldırılıp yerine çoklu paylaşım notu yazılır. ``source_link``
        o paylaşımın t.me adresidir: nota yazılan grup adı bu adrese gömülür
        (kullanıcı isteği — her grubun kendi mesajı).
        """
        token: Any = object()
        while True:
            async with DEDUP_LOCK:
                prune_dedup_cache(DEDUP_CACHE, time.time(), DEDUP_WINDOW_SECONDS, DEDUP_MAX_ENTRIES)
                active_key = (
                    key if key in DEDUP_CACHE
                    else (_find_matching_dedup_key(
                        DEDUP_CACHE, title,
                        product_asins(event) if event is not None else (),
                        product_link_ids(event) if event is not None else (),
                    ) or key)
                )
                entry = DEDUP_CACHE.get(active_key)
                if entry is None or entry.get("failed") or (
                    not entry.get("pending")
                    and better_or_different_offer(entry.get("offer"), offer)
                ):
                    target_key = active_key
                    previous = entry if entry and event is not None and (
                        is_referenced_multibuy_upgrade(entry, title, event, offer, time.time())
                        or is_referenced_product_price_drop(entry, title, event, offer, time.time())
                    ) else None
                    entry = new_dedup_entry(title, token, source_name, footer_name)
                    note_dedup_source(entry, source_name, source_link)
                    entry["owner"] = asyncio.current_task()
                    entry["offer"] = offer
                    entry["plain_offer"] = plain_offer_for_upgrade(event, offer) if event is not None else False
                    entry["product_asins"] = product_asins(event) if event is not None else set()
                    link_ids = product_link_ids(event) if event is not None else set()
                    if link_ids:
                        entry["product_link_ids"] = link_ids
                    if previous:
                        entry["upgrade_previous"] = previous
                        entry["count"] = int(previous.get("count", 1)) + 1
                        entry["sources"] = list(previous.get("sources") or [])
                        old_links = previous.get("links")
                        if isinstance(old_links, dict) and old_links:
                            entry["links"] = dict(old_links)
                        note_dedup_source(entry, source_name, source_link)
                    DEDUP_CACHE[target_key] = entry
                    return "send", token, target_key
                if not entry.get("pending"):
                    _keep_detailed_prefix_title(entry, title)
                    if event is not None:
                        entry.setdefault("product_asins", set()).update(product_asins(event))
                        link_ids = product_link_ids(event)
                        if link_ids:
                            entry.setdefault("product_link_ids", set()).update(link_ids)
                    entry["count"] += 1
                    entry["last_seen"] = time.time()
                    note_dedup_source(entry, source_name, source_link)
                    waiter_event = None
                else:
                    waiter_event = entry["ready"]
            if waiter_event is None:
                await dedup_apply_badge(active_key)
                # Deleted/wrong-ID messages must not swallow this occurrence.
                if entry.get("failed"):
                    continue
                return "dup", None, active_key
            try:
                await asyncio.wait_for(waiter_event.wait(), timeout=DEDUP_RESERVE_TIMEOUT)
            except TimeoutError:
                async with DEDUP_LOCK:
                    current = DEDUP_CACHE.get(active_key)
                    if current is entry and current.get("pending"):
                        owner = current.get("owner")
                        if owner is not None and not owner.done():
                            continue  # still sending / waiting for retry_after
                        log.warning("Tekrar rezervasyonu %ss'de bitmedi, devralınıyor (başlık: %.40s).",
                                    int(DEDUP_RESERVE_TIMEOUT), title)
                        current["failed"] = True
                        current["ready"].set()
                        token = object()
                        DEDUP_CACHE[active_key] = new_dedup_entry(
                            title, token, source_name, footer_name,
                        )
                        note_dedup_source(DEDUP_CACHE[active_key], source_name, source_link)
                        DEDUP_CACHE[active_key]["owner"] = asyncio.current_task()
                        DEDUP_CACHE[active_key]["offer"] = offer
                        DEDUP_CACHE[active_key]["plain_offer"] = plain_offer_for_upgrade(event, offer) if event is not None else False
                        return "send", token, active_key
                # State may have changed just as the timeout fired; re-read it.

    async def dedup_after_send(
        key: str, token: Any, success: bool, info: dict[str, Any] | None,
    ) -> None:
        """Rezervasyonu sonuçlandır: başarılıysa rozet bilgisini yaz, değilse düşür."""
        async with DEDUP_LOCK:
            entry = DEDUP_CACHE.get(key)
            if entry is None or entry.get("token") is not token:
                return  # devralınmış ya da budanmış; güncel kayda dokunma
            if not success or (NOTIFY_BOT_TOKEN and not (info or {}).get("bot")):
                entry["failed"] = True
                entry["ready"].set()
                if DEDUP_CACHE.get(key) is entry:
                    previous = entry.get("upgrade_previous")
                    if previous:
                        DEDUP_CACHE[key] = previous
                    else:
                        del DEDUP_CACHE[key]
                return
            info = info or {}
            bot_info = info.get("bot") or {}
            bot_id = bot_info.get("message_id")
            account_ids = list(info.get("account_ids") or [])
            if isinstance(bot_id, int) and not isinstance(bot_id, bool):
                entry["editable"] = "bot"
                entry["bot_message_id"] = bot_id
                entry["kind"] = "media" if bot_info.get("kind") == "media" else "text"
                entry["text"] = bot_info.get("text") or ""
                entry["entities_bot"] = list(bot_info.get("entities") or [])
                entry["keyboard"] = bot_info.get("keyboard")
            elif account_ids and not info.get("account_deleted"):
                entry["editable"] = "account"
                entry["account_message_id"] = account_ids[0]
                entry["text"] = info.get("account_text") or ""
                entry["entities_tl"] = list(info.get("account_entities") or [])
            else:
                # Rozet işlenemez (örn. bot ID'si alınamadı) ama tekrar
                # kaydı durur: sonraki kopyalar yine birleştirilir.
                entry["editable"] = None
            previous = entry.pop("upgrade_previous", None)
            if previous and not entry.get("editable"):
                # Unknown new message ID: never remove the known good old one.
                entry["count"] = 1
                entry["sources"] = [name for name in [entry.get("footer_name")] if name]
                previous = None
            entry["pending"] = False
            entry.pop("owner", None)
            entry["ready"].set()
        if previous:
            # Send first, then badge; only delete the old message after both
            # operations succeeded. A failure leaves two recoverable messages.
            await dedup_apply_badge(key)
            async with DEDUP_LOCK:
                can_delete = (DEDUP_CACHE.get(key) is entry
                              and entry.get("badge_count") == entry.get("count")
                              and not entry.get("failed"))
            if can_delete:
                async with previous["edit_lock"]:
                    try:
                        if previous.get("editable") == "bot" and DESTINATION_ID is not None:
                            deleted, detail = await delete_bot_message(
                                NOTIFY_BOT_TOKEN, DESTINATION_ID, previous["bot_message_id"])
                            if not deleted:
                                log.warning("Eski fırsat silinemedi: %s", detail)
                        elif previous.get("editable") == "account":
                            await delete_account_copy([previous["account_message_id"]], "indirimli fırsat")
                    except Exception as exc:
                        log.warning("Eski fırsat korunuyor: %s", type(exc).__name__)

    async def dedup_apply_badge(key: str) -> None:
        """İlk mesaja rozet işle; geçici hata akışı durdurmaz."""
        try:
            await _dedup_apply_badge(key)
        except Exception as exc:  # noqa: BLE001 - rozet görevi asla çökmemeli
            log.warning("Rozet görevi beklenmedik hatayla bitti: %s: %s", type(exc).__name__, exc)

    async def _dedup_apply_badge(key: str) -> None:
        async with DEDUP_LOCK:
            entry = DEDUP_CACHE.get(key)
            if entry is None or entry.get("pending") or not entry.get("editable"):
                return
            edit_lock = entry["edit_lock"]
        async with edit_lock:
            async with DEDUP_LOCK:
                if DEDUP_CACHE.get(key) is not entry or entry.get("pending") or not entry.get("editable"):
                    return
                if entry.get("badge_count") == entry.get("count"):
                    return
                count = int(entry.get("count", 0))
                editable = entry["editable"]
                kind = entry.get("kind", "text")
                current_text = entry.get("text") or ""
                bot_entities = list(entry.get("entities_bot") or [])
                tl_entities = list(entry.get("entities_tl") or [])
                keyboard = entry.get("keyboard")
                bot_message_id = entry.get("bot_message_id")
                account_message_id = entry.get("account_message_id")
                footer_name = (entry.get("footer_name") or "").strip()
                wait = DEDUP_EDIT_MIN_INTERVAL - (time.time() - float(entry.get("last_edit", 0.0)))
            if count < 2 or not current_text:
                return
            if wait > 0:
                await asyncio.sleep(wait)
            count = int(entry.get("count", count))
            # Eski not (varsa) çıkarılır; kaynak grup adı satırı KALDIRILIR ve
            # yerine çoklu paylaşım notu yazılıp mesaj kapatılır (kullanıcı
            # isteği: "hangi gruptan geldiğini kaldırıp N kere paylaşıldı şu şu
            # gruplar diye yazsın"). Grup adı bilinmiyorsa/sonda değilse not
            # eskisi gibi en sona eklenir.
            _, base, removed_badge = _strip_dedup_badge_details(current_text)
            badge_at_end = removed_badge != (0, 0, 0) \
                and removed_badge[1] == utf16_length(current_text) and removed_badge[2] == 0
            if footer_name and base.endswith(footer_name):
                # Rozet yokken ad kendi bloğuyla ("\n\n" + ad) durur; sondaki
                # rozet söküldüyse ayraç zaten rozetle birlikte gitmiştir
                # (ayracın kalan "\n" işareti adın hemen önünde durur).
                if removed_badge == (0, 0, 0):
                    cut = len(base) - len(footer_name) - 2 \
                        if base.endswith("\n\n" + footer_name) else -1
                elif badge_at_end:
                    cut = len(base) - len(footer_name)
                    if cut > 0 and base[cut - 1] != "\n":
                        cut = -1
                else:
                    cut = -1  # rozet ortada bir yerde: ad bloğuna dokunma
                if cut >= 0:
                    base = base[:cut]
                    removed_badge = (
                        utf16_length(current_text[:cut]), utf16_length(current_text), 0,
                    )
            if not base.strip():
                log.warning("Rozet gövdesi boş, güncelleme atlandı (başlık: %.40s).",
                            entry.get("title", ""))
                return
            names = list(entry.get("sources") or [])
            if not names:
                # Önbellek açılış taramasıyla kurulduysa adlar eski nottan,
                # link etiketleri de varsa TAM adlarla okunur (içinde " | "
                # geçen grup adları bölünmez).
                names = dedup_badge_names(
                    current_text.splitlines()[-1] if current_text else "",
                    entry.get("link_map") or entry.get("links") or {},
                )
            # Kullanıcı isteği: nota yazılan HER grup adı kendi mesajına
            # bağlanır (grup adı adresin içine gömülür).
            name_links = dedup_badge_links(entry, names)
            badges = [
                dedup_badge(count, sources=names),
                # Yer daraldığında ilk küçültme adları KORUR (süsü atar): ad
                # giderse adın taşıdığı mesaj linki de giderdi. Adları tümden
                # atan kısa biçim yalnız hiçbir ad link taşımıyorsa denenir.
                dedup_badge(count, sources=names, compact=True),
            ]
            if not name_links:
                badges.append(dedup_badge(count, sources=names, short=True))
            tried: set[str] = set()
            for badge in badges:
                if not badge or badge in tried:
                    continue
                tried.add(badge)
                new_text, insert_at, insert_length, badge_offset = dedup_badge_insertion(base, badge)
                # Entity konumları UTF-16 birimidir; ekleme noktası da aynı birimle
                # verilmelidir. Karakter sayısıyla verildiğinde emojili mesajlarda
                # gövdenin sonundaki entity'ler (alıntı, ``👉 Ürün Linki 👈`` bağlantısı)
                # rozet kadar kayıyor, bağlantı etiketinden kopuyordu.
                insert_at, insert_length = (utf16_length(new_text[:insert_at]),
                                            utf16_length(new_text[insert_at:]))
                if editable == "bot":
                    limit = CAPTION_LIMIT if kind == "media" else MESSAGE_LIMIT
                    if len(new_text) > limit:
                        continue  # sığmadı, kısa rozeti dene
                    shifted = _rebase_entities_for_dedup_badge(
                        bot_entities, removed_badge, insert_at, insert_length, bot_api=True,
                    )
                    new_entities: list[dict[str, Any]] | None = sorted([
                        *shifted,
                        *dedup_badge_entities(badge, bot_api=True, offset=badge_offset,
                                              links=name_links, names=names),
                    ], key=lambda entity: int(entity.get("offset", 0)))
                    if DESTINATION_ID is None or not isinstance(bot_message_id, int):
                        break
                    if kind == "media":
                        ok, detail = await edit_bot_caption(
                            NOTIFY_BOT_TOKEN, DESTINATION_ID, bot_message_id, new_text,
                            entities=new_entities, keyboard=keyboard,
                        )
                    else:
                        ok, detail = await edit_bot_text(
                            NOTIFY_BOT_TOKEN, DESTINATION_ID, bot_message_id, new_text,
                            entities=new_entities, keyboard=keyboard, link_preview=False,
                        )
                    if ok:
                        async with DEDUP_LOCK:
                            live = DEDUP_CACHE.get(key)
                            if live is entry:
                                live["text"] = new_text
                                live["entities_bot"] = new_entities
                                live["last_edit"] = time.time()
                                live["edit_fails"] = 0
                                live["badge_count"] = count
                        STATS["dedup_edits"] += 1
                        log.info("Rozet güncellendi (%s paylaşım, %s farklı kaynak, bot iletisi %s).",
                                 count, len(entry.get("sources") or []), bot_message_id)
                        return
                    if "message to edit not found" in detail.lower() or "message can't be edited" in detail.lower():
                        entry["failed"] = True
                        entry["editable"] = None
                        log.warning("Eski bot iletisi düzenlenemiyor; fırsat yeni bildirim olarak gönderilecek.")
                        return
                    log.info("Rozet bot iletisine işlenemedi (%s); kısa rozet deneniyor.", detail)
                else:
                    if not isinstance(account_message_id, int):
                        break
                    shifted_tl = _rebase_entities_for_dedup_badge(
                        tl_entities, removed_badge, insert_at, insert_length,
                    )
                    new_tl = sorted([
                        *shifted_tl,
                        *dedup_badge_entities(badge, bot_api=False, offset=badge_offset,
                                              links=name_links, names=names),
                    ], key=lambda entity: int(getattr(entity, "offset", 0) or 0))
                    try:
                        await client.edit_message(
                            DESTINATION, account_message_id, new_text,
                            formatting_entities=new_tl, link_preview=False,
                        )
                    except errors.FloodWaitError as exc:
                        log.warning("Rozet FloodWait (%s sn); kısa rozet denenmeden bırakılıyor.", exc.seconds)
                        entry["last_edit"] = time.time() + exc.seconds
                        return
                    except (errors.MessageIdInvalidError, errors.MessageAuthorRequiredError):
                        entry["failed"] = True
                        entry["editable"] = None
                        return
                    except errors.MessageNotModifiedError:
                        async with DEDUP_LOCK:
                            live = DEDUP_CACHE.get(key)
                            if live is entry:
                                live["text"] = new_text
                                live["entities_tl"] = new_tl
                                live["last_edit"] = time.time()
                                live["badge_count"] = count
                        return
                    except Exception as exc:  # noqa: BLE001 - rozet hatası fırsatı öldürmez
                        log.info("Rozet hesap iletisine işlenemedi (%s: %s); kısa rozet deneniyor.",
                                 type(exc).__name__, exc)
                        continue
                    async with DEDUP_LOCK:
                        live = DEDUP_CACHE.get(key)
                        if live is entry:
                            live["text"] = new_text
                            live["entities_tl"] = new_tl
                            live["last_edit"] = time.time()
                            live["edit_fails"] = 0
                            live["badge_count"] = count
                    STATS["dedup_edits"] += 1
                    log.info("Rozet güncellendi (%s paylaşım, %s farklı kaynak, hesap iletisi %s).",
                             count, len(entry.get("sources") or []), account_message_id)
                    return
            # Buraya düşüldüyse rozet işlenemedi; iki üst üste hatada bu
            # başlık için düzenlemeyi bırak (birleştirme sürer).
            async with DEDUP_LOCK:
                live = DEDUP_CACHE.get(key)
                if live is entry:
                    live["edit_fails"] = int(live.get("edit_fails", 0)) + 1
                    if live["edit_fails"] >= 2:
                        live["editable"] = None
                        log.info("Rozet iki kez başarısız oldu; bu başlık rozetsiz birleşecek.")

    async def dedup_preload() -> None:
        """Açılışta hedefin son iletilerini okuyup önbelleği ısıt (tek tarama).

        Güvenle eşlenebilen geçmişte yeniden bildirimi önler. Normal gruptaki
        bot mesajlarının hesap ID'leri Bot API ID'si olarak kullanılamaz.
        İnsan sohbeti ASLA kayda alınmaz: yalnızca hesabın gönderdikleri ve
        bildirim biçimindeki (rozetli/"Mesajı Gör"lü) iletiler işlenir.
        Kayda alınmayan bir ileti en fazla fazladan bir bildirime yol açar;
        yanlış kayıt ise fırsat kaybettirirdi — temkinli taraf seçildi.
        """
        if not DEDUP_ENABLED or DEDUP_SCAN_LIMIT <= 0 or DESTINATION_ID is None:
            return
        try:
            recent: list[Any] = []
            async for message in client.iter_messages(DESTINATION, limit=DEDUP_SCAN_LIMIT):
                recent.append(message)
        except Exception as exc:  # noqa: BLE001 - tarama açılışı öldürmemeli
            log.warning("Tekrar önbelleği ısıtılamadı (%s: %s); boş başlanıyor.",
                        type(exc).__name__, exc)
            return
        if NOTIFY_BOT_TOKEN and not bot_history_ids_shared(DESTINATION_ID):
            # Temel grupta MTProto ve Bot API mesaj ID uzayları ayrıdır: eski
            # bot iletisi düzenlenemez ama kimliği (başlık+fiyat/koşul) yine de
            # güvenle kayda alınabilir. Kayıt editable=None olur; böylece
            # restart sonrası aynı fırsat yeniden bildirilmez, yanlış ID ile
            # düzenleme de denenmez. (P4)
            log.info("Normal grupta eski bot mesajları düzenlenemez; "
                     "yalnızca tekrar bastırma için kayda alınacak.")
        now = time.time()
        added = 0
        failed = 0
        first_error = ""
        for message in reversed(recent):  # eskiden yeniye: ilk kopya kanonik olur
            try:
                text = getattr(message, "message", None) or ""
                if not isinstance(text, str) or not text.strip():
                    continue
                sender = getattr(message, "sender_id", None)
                if sender is None:
                    sender = getattr(getattr(message, "sender", None), "id", None)
                badge_count, base = strip_dedup_badge(text)
                looks_like_deal = (
                    badge_count > 1
                    or MESSAGE_LINK_LABEL in text
                    or PRODUCT_LINK_TEXT in text    # yeni düzen: 👉 Ürün Linki 👈
                    or PRODUCT_LINK_LABEL in text  # eski biçim
                    or any(is_price_line(line)      # eski sabit düzen (💰Fiyat: …)
                           for line in text.splitlines())
                )
                if sender == SELF_ID:
                    if NOTIFY_BOT_TOKEN:
                        continue  # account-only history is NOT a delivered notification
                    editable: str | None = "account"
                elif looks_like_deal:
                    if NOTIFY_BOT_TOKEN:
                        bot_id = NOTIFY_BOT_TOKEN.split(":", 1)[0]
                        if str(sender) != bot_id:
                            continue  # another bot/human is not editable by this bot
                        # Temel grupta ID uzayları ayrı: kayıt bastırma amaçlı
                        # tutulur (editable=None), bot ID'si düzenleme için
                        # KESİNLİKLE kullanılmaz.
                        editable = "bot" if bot_history_ids_shared(DESTINATION_ID) else None
                    else:
                        editable = None
                else:
                    continue  # insan sohbeti: kayda alma
                title = _search_query(base) or message_title(base)
                if SMART_HEADER:
                    first_line = next((ln.strip() for ln in base.splitlines() if ln.strip()), "")
                    if first_line.endswith("…"):
                        continue  # truncated history header is not a safe product identity
                    history_price, history_title = split_header_line(first_line)
                    if history_price:
                        # Üst satır "fiyat - ürün adı" (ya da eski "💰 …" satırı).
                        # Ad yoksa (salt fiyat/❓) güvenli ürün kimliği yoktur:
                        # canlı yol da başlıksız ileti için anahtar kurmaz. Body'den
                        # rastgele satır toplamak yanlış anahtara yol açabilir.
                        if not history_title:
                            continue
                        title = _campaign_title_if_clipped(
                            base, history_title, MESSAGE_LIMIT, include_code=True,
                            allow_code_only_marker=True,
                        ) or history_title
                    elif first_line:
                        title = _campaign_title_if_clipped(
                            base, first_line, MESSAGE_LIMIT, include_code=True,
                            allow_code_only_marker=True,
                        ) or first_line   # başlığın tek başına üstte olduğu düzen
                terms = offer_terms(message, prefer_header=True)
                key = dedup_offer_key(title, terms)
                if not key:
                    continue
                seen_at = now
                sent_date = getattr(message, "date", None)
                if sent_date is not None:
                    try:
                        seen_at = float(sent_date.timestamp()) if hasattr(sent_date, "timestamp") \
                            else float(sent_date)
                    except (TypeError, ValueError):
                        seen_at = now
                if now - seen_at > DEDUP_WINDOW_SECONDS:
                    continue
                message_id = getattr(message, "id", None)
                if not isinstance(message_id, int) or isinstance(message_id, bool):
                    continue
                media = getattr(message, "media", None)
                is_media = bool(media) and not isinstance(media, types.MessageMediaWebPage)
                raw_entities = list(getattr(message, "entities", None) or [])
                async with DEDUP_LOCK:
                    target_key = (
                        key if key in DEDUP_CACHE
                        else (_find_matching_dedup_key(DEDUP_CACHE, title) or key)
                    )
                    existing = DEDUP_CACHE.get(target_key)
                    if existing is None or better_or_different_offer(existing.get("offer"), terms):
                        # A newer/lower-price notification supersedes the old one;
                        # do not restore the expensive message after a restart.
                        record = new_dedup_entry(title, object(), preload=True)
                        record["pending"] = False
                        record["failed"] = False
                        record["ready"].set()
                        record["count"] = max(1, badge_count)
                        # Tekrarda kaldırılacak kaynak grup adı satırı (varsa).
                        record["footer_name"] = dedup_footer_name(base)
                        # Bildirimdeki her Telegram bağlantısı: grup adına gömülü
                        # adresler (yeni düzen) ve eski ``Mesajı Gör`` satırı.
                        link_map = dedup_source_links(text, raw_entities)
                        if link_map:
                            record["link_map"] = link_map
                        if badge_count > 1:
                            names = dedup_badge_names(
                                text.splitlines()[-1] if text else "", link_map,
                            )
                            if names:
                                record["sources"] = names
                        record["first_seen"] = seen_at
                        record["last_seen"] = seen_at
                        record["editable"] = editable
                        record["kind"] = "media" if is_media else "text"
                        record["text"] = text
                        record["offer"] = terms
                        record["entities_tl"] = raw_entities
                        if editable == "bot":
                            record["bot_message_id"] = message_id
                            record["entities_bot"] = [
                                converted for entity in raw_entities
                                if (converted := bot_api_entity(entity)) is not None
                            ]
                        elif editable == "account":
                            record["account_message_id"] = message_id
                        DEDUP_CACHE[target_key] = record
                        added += 1
                    elif not existing.get("pending"):
                        if badge_count > 1:
                            existing["count"] = max(int(existing.get("count", 1)), badge_count)
                        else:
                            existing["count"] = int(existing.get("count", 1)) + 1
                        existing["last_seen"] = max(float(existing.get("last_seen", seen_at)), seen_at)
            except Exception as exc:  # noqa: BLE001 - tek ileti taramayı durdurmaz
                failed += 1
                first_error = first_error or f"{type(exc).__name__}: {exc}"
                log.debug("Önbelleğe alınamayan ileti: %s: %s", type(exc).__name__, exc)
        if failed:
            # İletiler tek tek debug düzeyinde kalır; toplamı görünür olmalı: buradaki bir
            # kod hatası taramanın bir dalını sessizce devre dışı bırakabilir.
            log.warning("Tekrar önbelleği: %d/%d ileti işlenemedi (ilk hata: %s).",
                        failed, len(recent), first_error)
        if added:
            log.info("Tekrar önbelleği ısıtıldı: %d başlık (son %d ileti tarandı).",
                     added, len(recent))

    async def notify_offer(
        event: events.NewMessage.Event, source_name: str,
    ) -> tuple[bool, dict[str, Any]]:
        """Bildirim botuyla fırsatın kopyasını at; (gönderildi_mi, bilgi) döner.

        Tasarım (basit düzen): en üstte ÜST SATIR — ``smart_header`` açıksa
        ``₺56 — ürün adı`` (fiyat en başta; fiyat yoksa ``❓ — ürün adı``),
        kapalıysa ürün başlığı →
        altında mesaj OLDUĞU GİBİ → gizli link ekleri (varsa) →
        "🔗 Mesajı Gör: <t.me linki>" → en altta kaynak grup adı
        (etiketsiz, linksiz, yalnızca kalın).
        Tekrarlı paylaşımlarda kaynak grup adı satırı kaldırılır ve yerine
        çoklu paylaşım notu yazılır (bkz. ``_dedup_apply_badge``).

        Arama düğmeleri (Google Alışveriş / Akakçe / Cimri) kaynak mesajdan
        ayıklanan FİYATSIZ adla kurulur; üst satıra yazılan fiyat arama
        sorgusuna taşınmaz. Metin gönderiminde Telegram bağlantı önizlemesi
        kapalıdır; ürün bağlantısı ve tıklanabilirliği korunur, mesajın altında
        ikinci bir Amazon ürün kartı oluşmaz.

        Dönen ilk değer, tek mesaj modunda hesap kopyasının silinip
        silinmeyeceğini belirler (bkz. delete_account_copy). İkinci değer,
        tekrar birleştirmenin rozet işleyebilmesi için gönderilen bot
        mesajının kimlik/bilgi sözlüğüdür (boş olabilir).
        """
        no_info: dict[str, Any] = {}
        if not NOTIFY_BOT_TOKEN or DESTINATION_ID is None:
            return False, no_info, ""
        message_link = offer_link(event)
        footer_name = source_name if SOURCE_FOOTER else None

        descriptor = bot_media_descriptor(event) if NOTIFY_MEDIA else None
        if descriptor is not None:
            limit_mb = min(BOT_API_MEDIA_LIMIT_MB.get(descriptor["kind"], 50),
                           MAX_MEDIA_MB or BOT_API_MEDIA_LIMIT_MB.get(descriptor["kind"], 50))
            if descriptor["size"] and descriptor["size"] > limit_mb * 1024 * 1024:
                log.info("Bildirim medyası %s MB, sınır %s MB → metin olarak gönderilecek.",
                         descriptor["size"] // (1024 * 1024), limit_mb)
                descriptor = None

        if descriptor is not None:
            composed = compose_message(
                event, limit=CAPTION_LIMIT - 24, link_kinds=BOT_LINK_KINDS,
                message_link=message_link, source_name=footer_name, has_keyboard=True,
                source_name_url=message_link,
            )
            entities = bot_api_entities(composed["message"], composed["content"]) + source_name_entity(composed)
            keyboard = build_inline_keyboard(event, body_text=composed["text"], body_entities=entities)
            t_media = time.perf_counter()
            # Yedek zincirde (send_media) aynı teslim içinde indirildiyse
            # yeniden indirme; önbellek yalnızca bu deliver() bağlamında yaşar.
            data = None
            _media_cache = MEDIA_DOWNLOAD_CACHE.get()
            if _media_cache is not None:
                try:
                    data = _media_cache.get((int(event.chat_id), int(event.id)))
                except (TypeError, ValueError):
                    data = None
            if data is None:
                try:
                    data = await client.download_media(event.message, bytes)
                except Exception as exc:  # noqa: BLE001 - medya inmezse bildirim yine gitsin
                    log.warning("Bildirim medyası indirilemedi (%s): %s", type(exc).__name__, exc)
                    data = None
            t_downloaded = time.perf_counter()
            if data:
                log.info("⏱ Bildirim medyası: %s, %.1f MB, indirme %.1f sn.",
                         descriptor["kind"], len(data) / (1024 * 1024), t_downloaded - t_media)
                MEDIA_TIMING.set({"kind": descriptor["kind"], "mb": len(data) / (1024 * 1024),
                                  "download": t_downloaded - t_media, "upload": 0.0})
                send_result = await send_bot_media(
                    NOTIFY_BOT_TOKEN, DESTINATION_ID,
                    kind=descriptor["kind"], filename=descriptor["filename"],
                    mime_type=descriptor["mime"], data=data,
                    caption=composed["text"], entities=entities, keyboard=keyboard,
                )
                ok, detail = send_result
                if ok:
                    upload_seconds = time.perf_counter() - t_downloaded
                    log.info("Bildirim gönderildi (medya: %s, kaynak: %s, yükleme %.1f sn).",
                             descriptor["kind"], source_name, upload_seconds)
                    timing = MEDIA_TIMING.get()
                    if timing:
                        timing["upload"] = upload_seconds
                    return True, {
                        "message_id": getattr(send_result, "message_id", None),
                        "kind": "media",
                        "text": composed["text"],
                        "entities": entities,
                        "keyboard": keyboard,
                    }, ""
                log.warning("Bildirim medyası gönderilemedi (%s) → metne düşülüyor.", detail)

        composed = compose_message(
            event, limit=MESSAGE_LIMIT - 200, link_kinds=BOT_LINK_KINDS,
            message_link=message_link, source_name=footer_name, has_keyboard=True,
            source_name_url=message_link,
        )
        entities = bot_api_entities(composed["message"], composed["content"]) + source_name_entity(composed)
        text = composed["text"] or f"🔔 Yeni fırsat – {source_name}"
        keyboard = build_inline_keyboard(event, body_text=text, body_entities=entities)
        send_result = await send_bot_ping(
            NOTIFY_BOT_TOKEN, DESTINATION_ID, text,
            entities=entities, keyboard=keyboard, link_preview=False,
        )
        ok, detail = send_result
        if ok:
            log.info("Bildirim gönderildi (metin, kaynak: %s).", source_name)
            return True, {
                "message_id": getattr(send_result, "message_id", None),
                "kind": "text",
                "text": text,
                "entities": entities,
                "keyboard": keyboard,
            }, ""
        log.warning("Bildirim gönderilemedi: %s", detail)
        return False, no_info, str(detail)

    async def notify_offer_with_retry(
        event: events.NewMessage.Event, source_name: str,
    ) -> tuple[bool, dict[str, Any], str]:
        """Explicit gateway failures are retryable; ambiguous network errors are not replayed."""
        retry_delays = (0.5, 2.0)
        for attempt in range(len(retry_delays) + 1):
            sent, info, detail = await notify_offer(event, source_name)
            if sent:
                return True, info, ""
            # Telegram's own request_with_retry already handles 429. A definite
            # 502/503/504 can be retried without retrying timeouts/connection
            # failures whose first send may have been accepted.
            retryable = any(f"HTTP {status}:" in detail for status in (502, 503, 504))
            if not retryable or attempt >= len(retry_delays):
                return False, info, detail
            delay = retry_delays[attempt]
            log.warning("Bildirim geçici HTTP ağ geçidi hatası; aynı bildirim %.1f sn sonra yeniden denenecek (%d/%d).",
                        delay, attempt + 1, len(retry_delays))
            await asyncio.sleep(delay)
        return False, {}, "yeniden deneme sınırı"

    async def deliver(
        event: events.NewMessage.Event, source_name: str,
    ) -> tuple[bool, str, dict[str, Any]]:
        """Sırayla iletim yollarını dene; ilk başarılı olanı kullan.

        Üçüncü değer, tekrar birleştirmenin ilk mesaja rozet işleyebilmesi
        için gönderim bilgisidir: ``bot`` (bot mesajı sözlüğü ya da ``None``),
        ``account_ids`` (hesabın gönderdiği mesaj ID'leri),
        ``account_deleted`` (tek mesaj modunda silinip silinmediği),
        ``account_text``/``account_entities`` (hesap iletisinin metni/biçimi).
        """
        last_error = "denenmedi"
        # P5: bu teslim boyunca geçerli medya aktarım bağlamı (kalıcı değil).
        MEDIA_DOWNLOAD_CACHE.set({})
        t_begin = time.perf_counter()
        try:
            received_lag = max(0.0, time.time() - event.message.date.timestamp())
        except Exception:  # noqa: BLE001 - tarih yoksa ölçüm atlanır
            received_lag = -1.0
        skip_bot = False

        def note_notification_failure(detail: str = "") -> None:
            if NOTIFY_BOT_TOKEN and DESTINATION_ID is not None:
                STATS["notification_failed"] = int(STATS.get("notification_failed", 0) or 0) + 1
                log.error("Hedef içeriği iletilmiş olsa da telefon bildirimi başarısız (kaynak=%s, mesaj=%s, neden=%s); toplam=%d.",
                          source_name, getattr(event, "id", "?"), detail or "bilinmeyen hata",
                          STATS["notification_failed"])

        if FAST_NOTIFY and NOTIFY_BOT_TOKEN and DESTINATION_ID is not None:
            # Hızlı yol: önce bildirim botu. Başarılıysa hesap kopyası hiç atılmaz
            # (silinecek bir şey kalmaz); başarısızsa normal zincir aynen çalışır.
            bot_sent, bot_info, bot_error = await notify_offer_with_retry(event, source_name)
            if bot_sent:
                STATS["forwarded"] += 1
                STATS["modes"]["bot"] = STATS["modes"].get("bot", 0) + 1
                info = {
                    "mode": "bot", "bot": bot_info, "account_ids": [], "account_deleted": False,
                    "account_text": None, "account_entities": None,
                }
                try:
                    enqueue_private_offer(event, info)
                except Exception as exc:
                    log.warning("Özel kopya kuyruğa alınamadı; grup korunuyor: %s", type(exc).__name__)
                log.info("⏱ %s | telegram→bot %.1f sn | bildirim (hızlı yol) %.1f sn",
                         source_name, received_lag, time.perf_counter() - t_begin)
                maybe_report_slow(client, source_name, received_lag, [("Bildirim (hızlı yol)", time.perf_counter() - t_begin)])
                return True, "bot", info
            note_notification_failure(bot_error)
            skip_bot = True
            log.info("Hızlı bildirim gönderilemedi → normal iletim zincirine dönülüyor (kaynak: %s).", source_name)
        sanitized = sanitize_message(event)
        modes = DELIVERY_CHAIN
        product_title = _search_query(sanitized)
        if sanitized.changed or product_title:
            # Forward özgün iletiyi değiştirmeden taşır; temizleme gereken veya
            # üstüne ürün başlığı eklenecek iletilerde kopyalama zincirine geç
            # ki hem çıktı düzeni (başlık en üstte) korunsun hem de yasaklı
            # içerik kaçmasın. Zincirde yalnızca forward varsa yine forward
            # denenir: hiç iletmemektense başlıksız iletmek iyidir.
            filtered = [mode for mode in DELIVERY_CHAIN if mode != "forward"]
            modes = filtered or list(DELIVERY_CHAIN)
            reason = "temizleme" if sanitized.changed else "ürün başlığı"
            log.info("Mesaj %s gerektiriyor; 'forward' atlanacak (kaynak: %s).", reason, source_name)
        for mode in modes:
            try:
                result = await send_account_with_retry(mode, event)
            except Exception as exc:  # noqa: BLE001 - bir yol patlarsa sıradakini dene
                last_error = f"{type(exc).__name__}: {exc}"
                log.info("İletim yolu '%s' başarısız (%s) → sıradaki deneniyor.", mode, last_error)
                continue
            STATS["forwarded"] += 1
            STATS["modes"][mode] = STATS["modes"].get(mode, 0) + 1
            if mode not in ("forward", "copy"):
                log.info("Mesaj '%s' yedeğiyle iletildi (kaynak: %s).", mode, source_name)
            account_ids = sent_message_ids(result)
            t_account = time.perf_counter()
            # Tek mesaj modu: bildirim botu gönderdiyse hesabın attığı kopyayı sil.
            if skip_bot:
                bot_sent, bot_info = False, {}
            else:
                bot_sent, bot_info, bot_error = await notify_offer_with_retry(event, source_name)
                if not bot_sent:
                    note_notification_failure(bot_error)
            t_notified = time.perf_counter()
            account_deleted = False
            if bot_sent and SINGLE_MESSAGE:
                account_deleted = await delete_account_copy(account_ids, source_name)
            log.info("⏱ %s | telegram→bot %.1f sn | hesap kopyası %.1f sn | bildirim %.1f sn | silme %.1f sn | toplam %.1f sn",
                     source_name, received_lag, t_account - t_begin, t_notified - t_account,
                     time.perf_counter() - t_notified, time.perf_counter() - t_begin)
            maybe_report_slow(client, source_name, received_lag, [
                ("Hesap kopyası", t_account - t_begin), ("Bildirim", t_notified - t_account),
                ("Silme", time.perf_counter() - t_notified),
            ])
            info = {
                "mode": mode,
                "bot": bot_info if bot_sent else None,
                "account_ids": account_ids,
                "account_deleted": account_deleted,
                "account_text": first_sent_text(result),
                "account_entities": first_sent_entities(result),
            }
            try:
                enqueue_private_offer(event, info)
            except Exception as exc:
                log.warning("Özel kopya kuyruğa alınamadı; grup korunuyor: %s", type(exc).__name__)
            return True, mode, info

        STATS["failed"] += 1
        log.error("Hiçbir iletim yolu çalışmadı (kaynak=%s, mesaj=%s). Son hata: %s",
                  source_name, getattr(event, "id", "?"), last_error)
        return False, last_error, {}

    # Tekrar önbelleğini sıfırla ve hedefin son iletileriyle ısıt: yeniden
    # başlamalarda yalnızca güvenle eşlenebilen geçmişi kullan (tek tarama).
    DEDUP_CACHE.clear()
    LIVE_SEEN.clear()
    POLL_DISPATCHED.clear()
    SOURCE_IN_FLIGHT.clear()
    await dedup_preload()
    # Cursor: RAM sadece, bilinçli /restart’ta tek persist
    cursor_load_from_config(store.config)
    # İlk kurulum: source_cursors yoksa bootstrap ilk poll’de top ile yapılır (patlama yok)
    if not store.config.get("source_cursors"):
        log.info("Cursor: kalıcı kayıt yok — ilk poll’de sınırlı geriye tarama yapılacak; başlangıçtan önceki geçmiş bastırılacak.")

    # Handler'lara `chats=` VERMİYORUZ: Telethon o filtreyi ilk mesajda çözer ve
    # çözümleme hatası tüm update akışını öldürür. Filtreyi burada kendimiz yapıyoruz.
    @client.on(events.NewMessage())
    async def on_control_message(event: events.NewMessage.Event) -> None:
        if not is_control_event(event):
            return
        raw = (event.raw_text or "").strip()
        if not raw:
            return
        command = normalize(raw.split()[0]) if raw.startswith("/") else ""

        if not is_admin_event(event):
            if not command:
                return
            # Sessizce yok saymak yerine net bir hata ver: kullanıcı ID'sini
            # gösterip nasıl yetki vereceğini de söylüyoruz.
            STATS["commands"] += 1
            log.warning("Yetkisiz komut denemesi: %s (chat=%s, sender=%s)",
                        command, event.chat_id, event.sender_id)
            await event.reply(
                "⛔ Bu komutu kullanmaya yetkin yok.\n"
                f"• Komut: {command}\n"
                f"• Kontrol sohbeti: {event.chat_id}\n"
                f"• Senin kullanıcı ID'n: {event.sender_id}\n"
                f"• Yetkili ID'ler: {', '.join(str(i) for i in sorted(ADMIN_IDS)) or 'tanımsız'}\n"
                "Yetki için hesap sahibinden admin_user_id değerini config.json'da güncellemesini iste."
            )
            return

        # Owner-only optional feature. Existing config drafts always retain priority.
        if tracker and isinstance(event, PrivateControlEvent) and event.sender_id == SELF_ID:
            handled = await tracker.handle(event, raw, existing_pending=peek_pending(pending_key(event)) is not None)
            if handled:
                pending = peek_pending(pending_key(event))
                if pending and pending.get("stage") == "webdetakip":
                    # Ürün takibi kendi dosyasını anında günceller; /ekle
                    # taslağına devam edilmemesi için kapatılır.
                    drop_pending(pending_key(event))
                    if command in {"/iptal", "/webtakipiptal"}:
                        await control_reply(event, "↩️ Ürün takip taslağı iptal edildi; ayar düzenleme taslağı da kapatıldı.")
                    elif command == COMMAND_SETTINGS_SAVE:
                        await control_reply(event, "ℹ️ Ürün takip kayıtları product_watches.json'da; ayar düzenleme taslağı kapatıldı. Takip menüsü: /webtakip")
                    else:
                        await control_reply(event, "ℹ️ Ürün takip işlemi işlendi; ayar düzenleme taslağı kapatıldı. Takip menüsü: /webtakip")
                return

        # Komut değil: çok adımlı akışta beklenen değer olabilir.
        if not command:
            await handle_pending_message(event, raw)
            return

        STATS["commands"] += 1
        # Bekleyen liste düzenleme taslağını yalnızca /kaydet veya /iptal kapatır;
        # /durum gibi başka komutlar taslağı sessizce düşürmez.
        rest = raw[len(raw.split()[0]):].strip()
        # `/analiz350` → `/analiz 350`: bitişik yazılan mesaj sayısı.
        shorthand = re.fullmatch(re.escape(COMMAND_ANALYZE) + r"(\d+)", command)
        if shorthand:
            rest = f"{shorthand.group(1)} {rest}".strip()
            command = COMMAND_ANALYZE
        log.info("Komut alındı: %s (chat=%s, sender=%s)", command, event.chat_id, event.sender_id)

        # Sabit komutlar önce: alan değerleriyle çakışabilecek komutları çöz.
        if command == COMMAND_START:
            await control_reply(event, "👋 Komutlarını artık burada verebilirsin. Fırsatlar aynı hedef gruba gider.\n"
                                "Grup ayarları: /ayar · Durum: /durum · Tüm komutlar: /komutlar\n"
                                "Kişisel fırsatlar: /dmfiltre · /ekle /cikar → dmdahil / dmharic / dmfiyat / ürün adı < fiyat\n"
                                "Özel sohbet geçmişi silinmez. Kayıtlı Mesajlar yedek kontrol olarak açık.")
        elif command in CMD_DM_COMMANDS:
            await handle_dm_command(event, command, rest)
        elif command == COMMAND_STATUS:
            text = build_status_text(store.config, connected=client.is_connected())
            dm_on = dm_filters.has_active_rules(store.config)
            text += f"\n• 📩 Kişisel DM fırsat motoru: {'ETKİN' if dm_on else 'KAPALI'}"
            if tracker is not None:
                wt_on = bool(tracker.state.get("enabled", False))
                text += f"\n• 🌐 Web ürün takibi: {'AÇIK' if wt_on else 'KAPALI'}"
            if isinstance(event, PrivateControlEvent):
                text += "\n\n" + dm_status()
            await control_reply(event, text)
        elif command == COMMAND_TEST:
            text = (
                f"🧪 Deneme mesajı – {timefmt.local().strftime('%Y-%m-%d %H:%M:%S')} TSİ\n"
                f"Hedef: {DESTINATION_LABEL}\n"
                f"Kaynaklar: {len(SOURCE_IDS)} | Görülen: {STATS['seen']} | Eşleşen: {STATS['matched']}"
            )
            try:
                await client.send_message(DESTINATION, text)
                reply = f"✅ Deneme mesajı gönderildi: {DESTINATION_LABEL}"
            except Exception as exc:  # noqa: BLE001
                await control_reply(event, f"❌ Deneme mesajı gönderilemedi: {type(exc).__name__}: {exc}")
                return
            if NOTIFY_BOT_TOKEN and DESTINATION_ID is not None:
                ok, detail = await send_bot_ping(
                    NOTIFY_BOT_TOKEN, DESTINATION_ID,
                    "🔔 Bildirim denemesi\n\n(Bildirimlerde mesajın kopyası + '🔗 Mesajı Gör' linki gelir.)",
                )
                reply += "\n" + ("🔔 Bot bildirimi de gönderildi (telefonuna düşmeli)." if ok
                                 else f"⚠️ Bot bildirimi gönderilemedi: {detail}")
                reply += ("\nGizli linkler ve buton linkleri de bildirime eklenir; "
                          "medya varsa bot onu da gönderir.")
            else:
                reply += ("\n⚠️ NOTIFY_BOT_TOKEN yok: mesajı kendi hesabın gönderdiği için "
                          "bildirim almazsın. BotFather'dan bot oluşturup gruba ekle ve token'ı "
                          "GitHub Actions secret'ı olarak tanımla.")
            reply += "\n\n" + await private_channel_health()
            await control_reply(event, reply)
        elif command == COMMAND_SOURCES:
            await control_reply(event, build_source_text())
        elif command in CMD_SOURCE_TEST:
            await test_source_access(event, rest)
        elif command == COMMAND_ID:
            if isinstance(event, PrivateControlEvent):
                await control_reply(event,
                    f"🆔 Özel sohbet / kullanıcı ID'n: {event.sender_id}\n"
                    f"🎯 Fırsat hedefi değişmedi: {DESTINATION_LABEL}\n"
                    "Bu özel sohbet ID'sini destination olarak yazmana gerek yok."
                )
                return
            try:
                chat = await event.get_chat()
                chat_kind = type(chat).__name__
            except Exception:  # noqa: BLE001 - sohbet cache'te olmayabilir
                chat_kind = "bilinmiyor"
            await control_reply(event,
                f"🆔 Bu sohbetin ID'si: {event.chat_id}\n"
                f"• Tür: {chat_kind}\n"
                f"• Senin kullanıcı ID'n: {event.sender_id}\n"
                f"config.json için:\n"
                f'  "control_chat": {event.chat_id},\n'
                f'  "admin_user_id": {event.sender_id}'
            )
        elif command == COMMAND_RESTART:
            # Yalnızca bilinçli /restart’ta cursor yedeği — otomatik restart değil
            global RESTARTING
            RESTARTING = True
            # 1) Uçuş drain — yeni mesajlar RESTARTING bayrağıyla zaten bekletiliyor
            drain_deadline = time.time() + 6
            while time.time() < drain_deadline and (SOURCE_IN_FLIGHT or poll_delivery_tasks):
                await asyncio.sleep(0.2)
            if SOURCE_IN_FLIGHT or poll_delivery_tasks:
                log.warning("Restart drain %ss doldu — %s uçuş + %s poll görevi kaldı; mevcut ardışık cursor ile yedek alınıyor.",
                            6, len(SOURCE_IN_FLIGHT), len(poll_delivery_tasks))
            # 2) Tek toplu persist (mevcut atomic_write_json + GIT_WRITE_LOCK yolunu kullan)
            ok_save, save_note = await cursor_persist_for_restart(store)
            if not ok_save:
                RESTARTING = False
                await control_reply(event, "✗ Restart iptal — cursor kalıcılaştırılamadı: " + save_note)
                return
            # 3) Dispatch yalnızca persist başarıyla bittiyse
            ok, message = await dispatch_next_run(gh_pat)
            if ok:
                await control_reply(event, "🔄 " + message + "\n" + save_note)
            else:
                RESTARTING = False
                await control_reply(event, "⚠️ " + message + "\n" + save_note + "\nCursor yedeği alındı ama yeni çalışma başlatılamadı; mevcut süreç çalışmaya devam ediyor.")
        elif command == COMMAND_HELP:
            await control_reply(event, HELP_TEXT)
        elif command == COMMAND_WEB_TRACK:
            await control_reply(event, "Ürün takip menüsü için bildirim botunun özel sohbetinde /webtakip yaz.")
        elif command in CMD_ANALYZE:
            await analyze_history(event, rest)
        elif command in CMD_TG_FILTER_OPEN:
            await handle_filter_command(event, "open", rest)
        elif command in CMD_TG_FILTER_CLOSE:
            await handle_filter_command(event, "close", rest)
        elif command in SETTINGS_COMMANDS:
            await handle_settings_command(event, command, rest)
        else:
            await control_reply(event, f"Bilinmeyen komut: {raw}\n\n{HELP_TEXT}")

    @client.on(events.NewMessage())
    async def on_new_message(event: events.NewMessage.Event) -> None:
        if event.chat_id not in SOURCE_IDS or event.chat_id in CONTROL_IDS:
            return
        if DESTINATION_ID is not None and event.chat_id == DESTINATION_ID:
            return  # hedefe kendi gönderdiğimiz mesajı tekrar iletmeyelim
        if RESTARTING:
            return  # bilinçli restart drain — mesaj cursor ile kurtarılır
        if live_event_should_skip(event):
            return  # aynı mesaj hızlı taramayla zaten işlendi
        # cursor ilk katman — ID bazlı tekrar engeli (yalnızca RAM, persist’siz)
        # Yeni grup & ilk poll öncesi race: RAM’de yoksa bootstrap (patlama yok)
        try:
            _cid = int(event.chat_id); _mid = int(event.id)
            if _cid not in CURSOR_RAM:
                # İlk live update mesajı tek başına güvenli bir başlangıç noktası
                # değildir: update'ler eşzamanlı işlenirken daha eski ID önce
                # gelebilir. Poll, sınırlı/yaş filtreli bootstrap'ı belirleyecek.
                CURSOR_RAM[_cid] = {"cursor": 0, "pending": set(), "completed": set(),
                                    "failed": set(), "failed_at": {}, "initialized": False}
                log.info("Yeni kaynak canlı olayla görüldü; güvenli cursor poll'de belirlenecek: %s", _cid)
            _cur = int(CURSOR_RAM.get(_cid, {}).get("cursor", 0) or 0)
            if _mid <= _cur:
                return
        except Exception:
            pass
        key = (event.chat_id, event.id)
        SOURCE_IN_FLIGHT.add(key)
        _cursor_mark_pending(int(event.chat_id), int(event.id))
        try:
            await process_source_event(event)
        except BaseException:
            LIVE_SEEN.pop(key, None)
            POLL_DISPATCHED.pop(key, None)
            _cursor_mark_failed(int(event.chat_id), int(event.id))
            raise
        finally:
            SOURCE_IN_FLIGHT.discard(key)

    async def process_source_event(event: Any) -> None:
        STATS["seen"] += 1
        source_seen = STATS.setdefault("source_seen", {})
        if isinstance(source_seen, dict):
            source_seen[event.chat_id] = int(source_seen.get(event.chat_id, 0) or 0) + 1
        text = event.raw_text or ""
        # Exact, content-free teaser only. Any media, URL, hidden link, or button may
        # carry a real deal; fail open rather than suppressing it. Kaynak bu ifadeyi
        # "Yoğun ilgi görüyor 📣" diye yazar: emoji/noktalama karşılaştırmaya girmez.
        # Kelimeler birebir bu üçü olduğundan biçim entity'si (kalın, özel emoji)
        # içerik ekleyemez; bağlantı taşıyan entity'yi ``extract_links`` yakalar.
        msg = _as_message(event)
        teaser = (WORD_TOKEN_RE.findall(normalize(text)) == ["yoğun", "ilgi", "görüyor"]
                  and getattr(msg, "media", None) is None
                  and not getattr(msg, "reply_markup", None)
                  and not extract_links(event))
        if teaser or is_blank_reply(event):
            log.info("İçeriksiz mesaj elendi (chat=%s id=%s): ürün, fiyat ya da bağlantı yok.",
                     event.chat_id, event.id)
            _cursor_mark_success(int(event.chat_id), int(event.id))
            return
        if not matches(text, FILTER_INCLUDE, FILTER_EXCLUDE, FILTER_MODE,
                       include_enabled=FILTER_INCLUDE_ENABLED,
                       exclude_enabled=FILTER_EXCLUDE_ENABLED):
            log.debug("Eşleşmedi (chat=%s id=%s): %.80s", event.chat_id, event.id, text)
            _cursor_mark_success(int(event.chat_id), int(event.id))
            return

        STATS["matched"] += 1
        STATS["last_match"] = time.time()
        source_name = next((item["name"] for item in SOURCES if item["id"] == event.chat_id), str(event.chat_id))
        STATS["last_match_source"] = source_name
        log.info("Eşleşti: %s / mesaj %s / %.80s", source_name, event.id, text)

        # Tekrar birleştirme: aynı ürün sorgusu pencere içindeyse gruba yeni mesaj
        # ATILMAZ; ilk mesaja rozet işlenir. Kayıp hedefte yeni bildirim gönderilir.
        # Ürün sorgusu aynı temizlenmiş/gönderilecek içerikten ayıklanır.
        # Product identity must not vary with the optional display-header setting.
        dedup_title = smart_dedup_title(event)
        terms = offer_terms(event) if DEDUP_ENABLED else (None, "")
        dedup_id = dedup_offer_key(dedup_title, terms) if DEDUP_ENABLED else None
        if not dedup_id:
            ok, _, _ = await deliver(event, source_name)
            if not ok:
                LIVE_SEEN.pop((event.chat_id, event.id), None)
                POLL_DISPATCHED.pop((event.chat_id, event.id), None)
                _cursor_mark_failed(int(event.chat_id), int(event.id))
            else:
                _cursor_mark_success(int(event.chat_id), int(event.id))
            return
        decision, token, effective_dedup_id = await dedup_before_send(
            dedup_id, dedup_title, source_name,
            footer_name=source_name if SOURCE_FOOTER else None,
            offer=terms, event=event,
            source_link=build_message_link(
                event, next((item for item in SOURCES if item["id"] == event.chat_id), None)),
        )
        if decision == "dup":
            STATS["deduped"] += 1
            log.info("Tekrar birleştirildi (kaynak=%s): %.60s", source_name, dedup_title)
            _cursor_mark_success(int(event.chat_id), int(event.id))
            return
        ok, info = False, None
        try:
            ok, _, info = await deliver(event, source_name)
        finally:
            await dedup_after_send(effective_dedup_id, token, ok, info)
            if not ok:
                LIVE_SEEN.pop((event.chat_id, event.id), None)
                POLL_DISPATCHED.pop((event.chat_id, event.id), None)
                _cursor_mark_failed(int(event.chat_id), int(event.id))
            else:
                _cursor_mark_success(int(event.chat_id), int(event.id))

    async def private_status_changed(status: str, detail: str) -> None:
        """Rutin açılışı sessiz tut; gerçek özel komut arızalarını bildirmeyi sürdür."""
        if status == "running":
            log.info("Özel komutlar hazır: %s", detail)
            return
        if status == "recovered":
            text = "✅ Özel komut kanalı toparlandı; botun özel sohbetinden komut verebilirsin."
        elif status == "webhook":
            text = ("⚠️ Özel komutlar ÇALIŞMIYOR: bildirim botunda bir webhook tanımlı; "
                    "getUpdates ile çelişiyor. Webhook'u kaldırıp takipçiyi yeniden başlat "
                    "(webhook otomatik silinmez). Komutlar şimdilik Kayıtlı Mesajlar'dan işlenir.")
        elif status == "unauthorized":
            text = ("⚠️ Özel komutlar ÇALIŞMIYOR: bot token'ı geçersiz (401). "
                    "NOTIFY_BOT_TOKEN secret'ını BotFather'dan kontrol edip güncelle. "
                    "Komutlar şimdilik Kayıtlı Mesajlar'dan işlenir.")
        elif status == "conflict":
            text = ("⚠️ Özel komutlar ÇALIŞMIYOR: aynı bot token'ıyla ikinci bir getUpdates "
                    f"tüketicisi var ({detail}). Eski bir çalışma/VM/başka bir proje aynı botu "
                    "kullanıyor olabilir; onu kapat. Bu arada komutlar Kayıtlı Mesajlar'dan işlenir.")
        else:
            return
        try:
            await client.send_message(DESTINATION, text)
        except Exception as exc:  # noqa: BLE001
            log.warning("Özel komut durum uyarısı gruba gönderilemedi: %s", type(exc).__name__)

    poll_delivery_tasks: set[asyncio.Task] = set()

    async def fast_poll_loop() -> None:
        """Kaynakları tek istekle (getPeerDialogs) yoklar; yeni mesajı canlı olayla aynı yoldan işler."""
        input_peers: dict[int, Any] = {}
        failures = 0
        poll_interval = FAST_POLL_SECONDS
        POLL_TELEMETRY["interval"] = poll_interval
        log.info("⚡ Hızlı tarama açık: kaynaklar %.0f sn'de bir yoklanacak.", FAST_POLL_SECONDS)

        delivery_slots = asyncio.Semaphore(4)

        async def run_event(event: Any) -> None:
            try:
                async with delivery_slots:
                    await on_new_message(event)
            except Exception as exc:  # noqa: BLE001 - tek mesaj hatası taramayı durdurmasın
                log.warning("Hızlı tarama mesajı işlenemedi (%s): %s", type(exc).__name__, exc)

        source_retry_log_at: dict[tuple[int, str], float] = {}
        source_not_before: dict[int, float] = {}

        def defer_source_after_flood(chat_id: int, exc: BaseException, stage: str) -> None:
            wait = max(1, int(getattr(exc, "seconds", 1) or 1)) + 1
            source_not_before[int(chat_id)] = max(
                source_not_before.get(int(chat_id), 0.0), time.monotonic() + wait,
            )
            log.warning("Kaynak taraması FloodWait (%s, kaynak=%s): %s sn ertelendi; diğer kaynaklar sürüyor.",
                        stage, chat_id, wait)

        def should_log_source_retry(chat_id: int, stage: str) -> bool:
            key = (int(chat_id), stage)
            now = time.monotonic()
            if now - source_retry_log_at.get(key, 0.0) >= 300:
                source_retry_log_at[key] = now
                return True
            return False

        while True:
            try:
                await asyncio.sleep(max(FAST_POLL_SECONDS, poll_interval))
                if not FAST_POLL:
                    continue
                poll_started = time.monotonic()
                targets: list[tuple[int, Any]] = []
                for chat_id in sorted(SOURCE_IDS - CONTROL_IDS):
                    if source_not_before.get(chat_id, 0.0) > time.monotonic():
                        continue
                    peer = input_peers.get(chat_id)
                    if peer is None:
                        try:
                            peer = await client.get_input_entity(chat_id)
                        except errors.FloodWaitError as exc:
                            defer_source_after_flood(chat_id, exc, "entity")
                            continue
                        except Exception as exc:  # noqa: BLE001 - source isolated; retry next cycle
                            if should_log_source_retry(chat_id, "entity"):
                                log.warning("Hızlı taramada kaynak çözümlenemedi (kaynak=%s): %s: %s; sonraki turda yeniden denenecek.",
                                            chat_id, type(exc).__name__, exc)
                            continue
                        input_peers[chat_id] = peer
                    targets.append((chat_id, peer))
                if not targets:
                    POLL_TELEMETRY["last_elapsed"] = time.monotonic() - poll_started
                    continue
                dialog_started = time.monotonic()
                result = await client(functions.messages.GetPeerDialogsRequest(
                    peers=[types.InputDialogPeer(peer) for _, peer in targets],
                ))
                dialog_elapsed = time.monotonic() - dialog_started
                POLL_TELEMETRY["last_dialog_elapsed"] = dialog_elapsed
                if RESTARTING:
                    POLL_TELEMETRY["last_elapsed"] = time.monotonic() - poll_started
                    await asyncio.sleep(0.5)
                    continue
                tops = {utils.get_peer_id(d.peer): int(d.top_message or 0) for d in result.dialogs}
                events_by_source: dict[int, list[Any]] = {}
                for chat_id, peer in targets:
                    if RESTARTING:
                        break
                    top = tops.get(chat_id)
                    if top is None:
                        if should_log_source_retry(chat_id, "dialog"):
                            log.warning("Hızlı tarama yanıtında kaynak diyaloğu yok (kaynak=%s); sonraki turda yeniden denenecek.", chat_id)
                        continue
                    cur_entry = CURSOR_RAM.get(chat_id)
                    initializing = cur_entry is None or not cur_entry.get("initialized", True)
                    if initializing:
                        # Cursor yoksa/toparlanmış kaynaksa sınırlı bir start-up
                        # pencere tara. Eski geçmişi patlatma; son 30 dk ve en
                        # fazla 100 ileti. Geçmiş sorgusu hata verirse cursor'ı
                        # ilerletme, sonraki turda güvenle tekrar dene.
                        try:
                            messages = await client.get_messages(
                                peer, limit=FAST_POLL_BACKFILL_LIMIT, max_id=int(top) + 1,
                            )
                        except errors.FloodWaitError as exc:
                            defer_source_after_flood(chat_id, exc, "bootstrap history")
                            continue
                        except Exception as exc:  # noqa: BLE001 - retry without changing cursor
                            if should_log_source_retry(chat_id, "bootstrap-history"):
                                log.warning("Başlangıç geçmişi çekilemedi (kaynak=%s): %s: %s; sonraki turda yeniden denenecek.",
                                            chat_id, type(exc).__name__, exc)
                            continue
                        cutoff = time.time() - FAST_POLL_BACKFILL_MAX_AGE
                        recent = []
                        for message in messages:
                            try:
                                stamp = message.date.timestamp()
                            except Exception:  # noqa: BLE001 - malformed/missing date is not safe to replay
                                continue
                            if stamp >= cutoff:
                                recent.append(message)
                        recent.sort(key=lambda message: int(message.id))
                        st = _cursor_ensure(chat_id)
                        known_ids = set(st["pending"]) | set(st["failed"]) | set(st["completed"])
                        known_ids = {int(mid) for mid in known_ids if int(mid) > 0}
                        if not recent:
                            st["cursor"] = min(int(top), min(known_ids) - 1) if known_ids else int(top)
                            st["initialized"] = True
                            _cursor_advance(chat_id)
                            log.info("Yeni kaynak için cursor bootstrap: %s → %s (son %d ileti içinde son %.0f dk'da ileti yok).",
                                     chat_id, st["cursor"], FAST_POLL_BACKFILL_LIMIT, FAST_POLL_BACKFILL_MAX_AGE / 60)
                            continue
                        baseline_ids = [int(recent[0].id), *known_ids]
                        st["cursor"] = max(0, min(baseline_ids) - 1)
                        st["initialized"] = True
                        # Mark stale messages that fall *inside* the recent ID
                        # range as intentionally skipped; otherwise a later
                        # min_id poll could rediscover and deliver them.
                        recent_ids = {int(message.id) for message in recent}
                        for stale_message in messages:
                            stale_id = int(getattr(stale_message, "id", 0) or 0)
                            if stale_id > st["cursor"] and stale_id not in recent_ids:
                                _cursor_mark_success(chat_id, stale_id)
                        messages = recent
                        log.info("Yeni kaynak için sınırlı kurtarma: %s → cursor=%s, %d ileti (son %.0f dk).",
                                 chat_id, st["cursor"], len(messages), FAST_POLL_BACKFILL_MAX_AGE / 60)
                        cur = int(st["cursor"])
                    else:
                        cur = int(cur_entry.get("cursor", 0) or 0)
                        if top <= cur:
                            continue
                        try:
                            # oldest-first, capped batch: a noisy source cannot
                            # monopolize this pass; remaining IDs stay beyond cursor.
                            messages = await client.get_messages(
                                peer, min_id=cur, limit=FAST_POLL_BATCH_LIMIT, reverse=True,
                            )
                        except errors.FloodWaitError as exc:
                            defer_source_after_flood(chat_id, exc, "history")
                            continue
                        except Exception as exc:  # noqa: BLE001 - don't move cursor; retry next cycle
                            if should_log_source_retry(chat_id, "history"):
                                log.warning("Geçmiş çekilemedi (kaynak=%s, cursor=%s): %s: %s; sonraki turda yeniden denenecek.",
                                            chat_id, cur, type(exc).__name__, exc)
                            continue
                        messages = sorted(messages, key=lambda message: int(getattr(message, "id", 0) or 0))
                    # Poll-confirmed leading ID gaps are deleted messages; do not
                    # skip an ID currently being delivered or awaiting retry.
                    fetched_ids = {int(getattr(m, "id", 0) or 0) for m in messages if getattr(m, "id", None)}
                    if fetched_ids:
                        max_fetched = max(fetched_ids)
                        st = _cursor_ensure(chat_id)
                        for gid in range(int(st["cursor"]) + 1, max_fetched + 1):
                            if gid in fetched_ids:
                                break
                            if gid in st["pending"] or gid in st["failed"]:
                                break
                            st["cursor"] = gid
                        _cursor_advance(chat_id)
                        cur = int(st["cursor"])
                    for message in messages:
                        mid = int(getattr(message, "id", 0) or 0)
                        if mid <= cur or (chat_id, mid) in LIVE_SEEN:
                            continue
                        _remember(POLL_DISPATCHED, chat_id, mid)
                        event = events.NewMessage.Event(message)
                        try:
                            event._set_client(client)
                        except Exception:  # noqa: BLE001 - attach client even without entity cache
                            event._client = client
                        event._fast_poll = True
                        log.info("⚡ Hızlı tarama yeni mesajı yakaladı (kaynak=%s, mesaj=%s).", chat_id, mid)
                        events_by_source.setdefault(chat_id, []).append(event)
                # Interleave dispatch by source: a backlog in the first channel
                # must not put every other source behind it in the delivery lock.
                if not RESTARTING:
                    source_order = sorted(events_by_source)
                    while any(events_by_source.values()):
                        for chat_id in source_order:
                            pending_events = events_by_source[chat_id]
                            if not pending_events:
                                continue
                            task = asyncio.create_task(run_event(pending_events.pop(0)))
                            poll_delivery_tasks.add(task)
                            task.add_done_callback(poll_delivery_tasks.discard)
                # Measure the whole scan pass (entity resolution, dialog request,
                # and all per-source history fetches), not just GetPeerDialogs.
                poll_elapsed = time.monotonic() - poll_started
                POLL_TELEMETRY["last_elapsed"] = poll_elapsed
                if poll_elapsed >= max(2.0, FAST_POLL_SECONDS):
                    poll_interval = next_poll_interval(poll_interval, poll_elapsed)
                    POLL_TELEMETRY["interval"] = poll_interval
                    log.info("Tam hızlı tarama turu %.1f sn sürdü (diyalog isteği %.1f sn); aralık %.0f sn'ye çıkarıldı.",
                             poll_elapsed, dialog_elapsed, poll_interval)
                else:
                    recovered = recover_poll_interval(poll_interval, FAST_POLL_SECONDS)
                    if recovered != poll_interval:
                        poll_interval = recovered
                        POLL_TELEMETRY["interval"] = poll_interval
                failures = 0
            except asyncio.CancelledError:
                raise
            except errors.FloodWaitError as exc:
                wait = max(1, int(getattr(exc, "seconds", 5))) + 1
                poll_interval = next_poll_interval(poll_interval, wait)
                POLL_TELEMETRY["interval"] = poll_interval
                POLL_TELEMETRY["flood_count"] += 1
                POLL_TELEMETRY["last_flood_wait"] = float(wait)
                log.warning("Hızlı tarama FloodWait: %s sn bekleniyor; yeni yoklama aralığı %.0f sn. "
                            "Canlı mesaj dinleme devam ediyor. [bu oturumda tarama FloodWait=%d]",
                            wait, poll_interval, int(POLL_TELEMETRY["flood_count"]))
                await asyncio.sleep(wait)
            except Exception as exc:  # noqa: BLE001 - geçici hata; canlı olaylar çalışmaya devam eder
                failures += 1
                if failures in {1, 5, 20}:
                    log.warning("Hızlı tarama hatası (%s): %s", type(exc).__name__, exc)
                await asyncio.sleep(min(30, 2 * failures))

    # Özel komutlar ve fırsat kuyruğu ayrı görevlerde çalışır; açılış mesajı gönderilmez.
    private_tasks = []
    if tracker:
        private_tasks.append(asyncio.create_task(tracker.run()))
    if private_api:
        private_tasks.append(asyncio.create_task(private_offers.run()))
        if config_flag(store.config.get("private_control"), False):
            private_tasks.append(asyncio.create_task(poll_private_commands(
                private_api, lambda: ADMIN_IDS | {SELF_ID}, on_control_message,
                private_status_changed,
            )))
            await asyncio.sleep(0)  # poller görevi ilk adımını atsın
            for _ in range(40):  # poller başlangıcına en fazla ~10 sn tanı
                if poll_health()["status"] not in {"off", "starting"}:
                    break
                await asyncio.sleep(0.25)

    async def prepare_scheduled_restart() -> bool:
        global RESTARTING
        RESTARTING = True
        drain_deadline = time.time() + 6
        while time.time() < drain_deadline and (SOURCE_IN_FLIGHT or poll_delivery_tasks):
            await asyncio.sleep(0.2)
        if SOURCE_IN_FLIGHT or poll_delivery_tasks:
            log.warning("Otomatik restart drain %ss doldu — %s uçuş + %s poll görevi kaldı; güvenli cursor kaydedilip yenileme sürüyor.",
                        6, len(SOURCE_IN_FLIGHT), len(poll_delivery_tasks))
        ok_save, save_note = await cursor_persist_for_restart(store)
        if not ok_save:
            RESTARTING = False
            log.error("Otomatik restart cursor kaydı başarısız; mevcut süreç dinlemeye devam edecek: %s", save_note)
            return False
        log.info("Otomatik restart cursor'ı kaydedildi: %s", save_note)
        return True

    async def resume_after_scheduled_restart_failure() -> None:
        global RESTARTING
        RESTARTING = False
        log.warning("Yeni çalışma başlatılamadı; mevcut süreç kaynakları dinlemeye devam edecek.")

    if auto_restart and gh_pat:
        asyncio.create_task(auto_restart_scheduler(
            client, DESTINATION, gh_pat, max(60, restart_minutes * 60),
            prepare_restart=prepare_scheduled_restart,
            resume_after_failure=resume_after_scheduled_restart_failure,
        ))
    elif auto_restart:
        log.warning("auto_restart açık ama GH_PAT yok; oturum Actions süresi bitince kapanacak.")
    asyncio.create_task(heartbeat())

    if FAST_POLL:
        private_tasks.append(asyncio.create_task(fast_poll_loop()))

    log.info("Dinleniyor... (kaynak=%d, kontrol=%s, hedef=%s)", len(SOURCE_IDS), sorted(CONTROL_IDS), DESTINATION_LABEL)
    try:
        await client.run_until_disconnected()
    finally:
        shutdown_tasks = [*private_tasks, *poll_delivery_tasks]
        for task in shutdown_tasks:
            task.cancel()
        await asyncio.gather(*shutdown_tasks, return_exceptions=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:  # noqa: BLE001 - Actions log'unda net bir iz bırak
        log.exception("Takipçi beklenmeyen bir hatayla durdu.")
        raise
