"""config.json değerlerini çalışma zamanı ayarlarına (appstate) uygulama."""

from __future__ import annotations

import logging
import os

import appstate
import composer
from confutil import config_flag, link_appendix_mode, link_kinds_for, parse_admin_ids
from keywords import filter_state_of, match_mode_of, normalize

log = logging.getLogger("telegram-filter")


def apply_runtime_config(config: dict) -> list[str]:
    """Global çalışma ayarlarını config'ten yenile (Telegram bağlantısı gerekmez)."""

    notes: list[str] = []
    appstate.FILTER_INCLUDE = [normalize(x) for x in config.get("include_keywords") or []]
    appstate.FILTER_EXCLUDE = [normalize(x) for x in config.get("exclude_keywords") or []]
    appstate.FILTER_MODE = match_mode_of(config)
    filter_state = filter_state_of(config)
    appstate.FILTER_INCLUDE_ENABLED = filter_state["include"]
    appstate.FILTER_EXCLUDE_ENABLED = filter_state["exclude"]
    appstate.ADMIN_IDS = parse_admin_ids(config.get("admin_user_id"))
    appstate.DELIVERY_CHAIN = composer.build_delivery_chain(config)
    try:
        appstate.MAX_MEDIA_MB = int(config.get("max_media_mb", 25))
    except (TypeError, ValueError):
        log.warning("max_media_mb sayı değil, 25 kabul edildi.")
        appstate.MAX_MEDIA_MB = 25
    appstate.LINK_APPENDIX_MODE = link_appendix_mode(config)
    appstate.LINK_KINDS = link_kinds_for(appstate.LINK_APPENDIX_MODE, bot=False)
    appstate.BOT_LINK_KINDS = link_kinds_for(appstate.LINK_APPENDIX_MODE, bot=True)
    appstate.MESSAGE_LINK_LINE = config_flag(config.get("message_link"), True)
    appstate.SOURCE_FOOTER = config_flag(config.get("source_footer"), True)
    appstate.NOTIFY_MEDIA = config_flag(config.get("notify_media"), True)
    appstate.SINGLE_MESSAGE = config_flag(config.get("single_message"), True)
    appstate.CLEAN_COMMANDS = config_flag(config.get("clean_commands"), True)
    # Varsayılan kapalı: davranış değişikliği yalnız config.json açtığında
    # üretimde görülür; mevcut testler/altın referans eski düzeni sınamaya devam eder.
    appstate.CLEAN_QUOTE_DUPLICATES = config_flag(config.get("clean_quote_duplicates"), False)
    appstate.SMART_HEADER = config_flag(config.get("smart_header"), False)
    appstate.FAST_NOTIFY = config_flag(config.get("fast_notify"), False)
    appstate.FAST_POLL = config_flag(config.get("fast_poll"), True)
    try:
        appstate.FAST_POLL_SECONDS = min(30.0, max(1.0, float(config.get("fast_poll_seconds", 3))))
    except (TypeError, ValueError):
        appstate.FAST_POLL_SECONDS = 3.0
    try:
        appstate.FAST_POLL_BACKFILL_MAX_AGE = min(120.0, max(1.0, float(config.get("fast_poll_backfill_minutes", 15)))) * 60.0
    except (TypeError, ValueError):
        appstate.FAST_POLL_BACKFILL_MAX_AGE = 900.0
    try:
        appstate.SLOW_REPORT_SECONDS = max(0.0, float(config.get("slow_report_seconds", 8)))
    except (TypeError, ValueError):
        appstate.SLOW_REPORT_SECONDS = 8.0
    appstate.DEDUP_ENABLED = config_flag(config.get("dedup_enabled"), True)
    try:
        appstate.DEDUP_WINDOW_SECONDS = max(1, min(72, int(config.get("dedup_window_hours", 12)))) * 3600
    except (TypeError, ValueError):
        log.warning("dedup_window_hours sayı değil, 12 saat kabul edildi.")
        appstate.DEDUP_WINDOW_SECONDS = 12 * 3600
    try:
        appstate.DEDUP_SCAN_LIMIT = max(0, min(100, int(config.get("dedup_scan_limit", 100))))
    except (TypeError, ValueError):
        log.warning("dedup_scan_limit sayı değil, 100 kabul edildi.")
        appstate.DEDUP_SCAN_LIMIT = 100
    # Bildirim token'ının tek kaynağı environment/Actions secret'ı olsun;
    # config.json içindeki eski notify_bot_token alanı artık kullanılmaz.
    token = str(os.getenv("NOTIFY_BOT_TOKEN", "") or "").strip()
    appstate.NOTIFY_BOT_TOKEN = "" if token.lower() in {"null", "none", "yok"} else token

    if not appstate.FILTER_INCLUDE_ENABLED:
        notes.append("🔓 Dahili kelime filtresi kapalı; anahtar kelimeler yok sayılıyor"
                     + (" (harici kelimeler yine engeller)." if appstate.FILTER_EXCLUDE_ENABLED
                        else " ve harici engelleme de kapalı: her mesaj iletilir."))
    if not appstate.FILTER_EXCLUDE_ENABLED:
        notes.append("🚫 Harici kelime filtresi kapalı; engelleme yapılmıyor.")
    return notes


def changed_groups(before: dict, after: dict) -> set[str]:
    """Liste akışında kaynak listesi değiştiyse kaynakları yeniden çöz."""
    return {"sources"} if before.get("source_chats") != after.get("source_chats") else set()
