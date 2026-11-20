"""/durum ve kaynak durum metinleri."""

from __future__ import annotations

import time

import appstate
import botapi
import timefmt
from commandtext import telegram_list_items
from confutil import config_flag
from keywords import filter_state_of, match_mode_of
from textutil import humanize


def build_status_text(config: dict, *, connected: bool = True) -> str:
    last = appstate.STATS["last_match"]
    last_line = "henüz eşleşme yok"
    if last:
        last_line = f"{humanize(time.time() - last)} önce ({appstate.STATS['last_match_source']})"
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
    elif not appstate.SOURCE_IDS:
        listening = "• Dinleme: KAYNAK YOK — kaynak listesini kontrol et."
    else:
        listening = "• Dinleme: AKTİF — kaynak mesajları bekleniyor."
    return (
        "✅ Takipçi aktif\n"
        f"{listening}\n"
        f"• Çalışma süresi: {humanize(time.time() - appstate.STARTED_AT)}"
        f" · başlangıç {timefmt.stamp(appstate.STARTED_AT)} TSİ (şimdi {timefmt.clock()} TSİ)\n"
        f"• Dinlenen kaynak: {len(appstate.SOURCE_IDS)}/{len(config.get('source_chats') or [])}"
        + (f" (çözülemeyen {len(appstate.SOURCE_FAILURES)})" if appstate.SOURCE_FAILURES else "")
        + "\n"
        f"• Görülen: {appstate.STATS['seen']} | Eşleşen: {appstate.STATS['matched']} | İletilen: {appstate.STATS['forwarded']}"
        + (f" | Hedefe iletilemeyen: {appstate.STATS['failed']}" if appstate.STATS["failed"] else "")
        + (f" | Telefon bildirimi başarısız: {appstate.STATS['notification_failed']}"
           if appstate.STATS.get("notification_failed") else "")
        + "\n"
        f"• Son eşleşme: {last_line}\n"
        f"• Hedef: {appstate.DESTINATION_LABEL}\n"
        f"• Kontrol sohbeti: {('Bot özel sohbeti + Kayıtlı Mesajlar' if config_flag(config.get('private_control'), False) else ', '.join(appstate.CONTROL_NAMES) or 'me')}\n"
        f"{filter_line}\n"
        f"• İletim sırası: {' → '.join(appstate.DELIVERY_CHAIN) or 'yok'}"
        + (f" | kullanılan: {', '.join(f'{k}×{v}' for k, v in appstate.STATS['modes'].items())}"
           if appstate.STATS["modes"] else "")
        + "\n"
        f"• Tek mesaj modu: {'açık' if appstate.SINGLE_MESSAGE else 'kapalı'}"
        + (f" · bildirim sonrası silinen hesap kopyası: {appstate.STATS.get('cleaned', 0)}"
           if appstate.SINGLE_MESSAGE and appstate.STATS.get("cleaned") else "")
        + "\n"
        f"• Komut temizliği: {'açık' if appstate.CLEAN_COMMANDS else 'kapalı'}"
        + (f" · silinen eski komut mesajı: {appstate.STATS.get('cleaned_commands', 0)}"
           if appstate.CLEAN_COMMANDS and appstate.STATS.get("cleaned_commands") else "")
        + "\n"
        + (
            f"• Tekrar birleştirme: açık · birleştirilen {appstate.STATS.get('deduped', 0)}"
            f" · rozet {appstate.STATS.get('dedup_edits', 0)} · izlenen {len(appstate.DEDUP_CACHE)} başlık"
            if appstate.DEDUP_ENABLED else "• Tekrar birleştirme: kapalı"
        )
        + "\n"
        + (
            f"• Hızlı tarama: açık · aralık {appstate.POLL_TELEMETRY['interval']:.0f} sn"
            f" · son yoklama {appstate.POLL_TELEMETRY['last_elapsed']:.1f} sn"
            f" · FloodWait {int(appstate.POLL_TELEMETRY['flood_count'])}"
            + (f" (son {appstate.POLL_TELEMETRY['last_flood_wait']:.0f} sn)" if appstate.POLL_TELEMETRY["flood_count"] else "")
            if appstate.FAST_POLL else "• Hızlı tarama: kapalı"
        )
        + "\n"
        + botapi._bot_api_stats_line()
    )


def build_source_text() -> str:
    lines = [f"İzlenen {len(appstate.SOURCES)} kaynak:"]
    lines += [f"• {item['name']}  [{item['id']}]" + ("" if item["joined"] else "  ⚠️ ÜYE DEĞİLSİN")
              for item in appstate.SOURCES]
    for value, exc in appstate.SOURCE_FAILURES:
        lines.append(f"✗ {value} çözülemedi: {exc}")
    return "\n".join(lines)
