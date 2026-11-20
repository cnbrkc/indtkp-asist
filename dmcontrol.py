"""Kişisel fırsat (DM) kuyruğu ve DM komutları."""

from __future__ import annotations

import logging

from telethon.tl import types

import appstate
import dm_filters
import price_filter
from botentities import bot_api_entities, entities_for_text
from commandtext import (
    COMMAND_DM_FILTER_CLOSE,
    COMMAND_DM_FILTER_OPEN,
    COMMAND_DM_STATUS,
    HELP_TEXT,
    _log_dm_decision,
    dm_decision,
    dm_price_threshold,
    drop_pending,
    format_value_preview,
    peek_pending,
    pending_key,
    telegram_list_items,
)
from confutil import config_flag
from controlbase import control_reply
from dedupmatch import bot_history_ids_shared
from private_bot import (
    BotAPIError,
    PrivateControlEvent,
    edit_dm_keywords,
    poll_health,
    private_copy_request,
)
from runtime import Runtime
from sanitize import message_text, sanitize_message
from searchbuttons import build_inline_keyboard

log = logging.getLogger("telegram-filter")


async def prepare_private_offer(rt: Runtime, item):
    # Turning off/changing the filter also cancels queued, unsent copies.
    decision = dm_decision(
        rt.store.config, item["match_text"], item.get("strike") or (),
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
        message = await rt.client.get_messages(item["destination"], ids=ids[0])
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
    return private_copy_request(appstate.SELF_ID, item["destination"], info)


def enqueue_private_offer(rt: Runtime, event, info):
    if not rt.private_api or not dm_filters.has_active_rules(rt.store.config):
        return
    sanitized = sanitize_message(event)
    text = message_text(sanitized)
    strike = price_filter.strike_spans_from_entities(text, entities_for_text(sanitized, text))
    decision = dm_decision(rt.store.config, text, strike)
    _log_dm_decision(decision)
    if decision.should_send:
        rt.private_offers.submit({
            "destination": appstate.DESTINATION_ID, "match_text": text, "strike": strike,
            "dm_facts": decision.facts,
            "bot": info.get("bot"), "account_ids": info.get("account_ids"),
            "keyboard": build_inline_keyboard(event),
        })


def dm_status(rt: Runtime):
    words = [str(value) for value in telegram_list_items(rt.store.config, "dm_keywords")]
    excluded = [str(value) for value in telegram_list_items(rt.store.config, "dm_exclude_keywords")]
    enabled = config_flag(rt.store.config.get("dm_enabled"), False)
    threshold = dm_price_threshold(rt.store.config)
    product_rules = dm_filters.product_price_rules(rt.store.config)
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
        f"Bu oturum: gönderilen {rt.private_offers.sent}, hata {rt.private_offers.failed}, "
        f"kuyrukta {rt.private_offers.queue.qsize()}, taşma {rt.private_offers.dropped}\n"
        "/ekle → dmdahil / dmharic / dmfiyat / ürün adı < fiyat → değer → /kaydet\n"
        "/cikar → filtre → kayıt numarası veya tam kural → /kaydet\n"
        "/dmfiltreac - dmdahil grubunu aç\n"
        "/dmfiltrekapat - dmdahil grubunu kapat"
    )


async def private_channel_health(rt: Runtime) -> str:
    """Özel komut kanalının teşhisi; /test ile görünür (token asla yazılmaz)."""
    if not appstate.NOTIFY_BOT_TOKEN:
        return ("🔎 Özel komut kanalı: KAPALI — NOTIFY_BOT_TOKEN tanımlı değil.\n"
                "Komutlar Kayıtlı Mesajlar'dan işlenir; bot sana özel mesaj atamaz.")
    lines = ["🔎 Özel komut kanalı (bildirim botu):"]
    try:
        me = await rt.private_api.call("getMe", {}) or {}
        username = me.get("username")
        lines.append(f"• Bot: @{username}" if username
                     else "• Bot: kullanıcı adı alınamadı (token geçersiz olabilir).")
    except BotAPIError as exc:
        lines.append(f"• Bot: alınamadı — token geçersiz olabilir (hata {exc.code}).")
    except Exception as exc:
        lines.append(f"• Bot: alınamadı ({type(exc).__name__}).")
    try:
        webhook = await rt.private_api.call("getWebhookInfo", {}) or {}
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


async def save_dm_setting(rt: Runtime, event, *, action=None, raw="", enabled=True):
    async with rt.settings_lock:
        before = rt.store.snapshot()
        if action is not None:
            existing = rt.store.config.get("dm_keywords", [])
            try:
                words = edit_dm_keywords(existing, raw, action)
            except ValueError as exc:
                await control_reply(rt, event, str(exc))
                return
            if words == existing:
                pending = peek_pending(pending_key(event))
                if pending and pending.get("stage") == "dm_words":
                    drop_pending(pending_key(event))
                await control_reply(rt, event, "ℹ️ Bu kelimeler zaten listede; ayarlar değişmedi.\n" + dm_status(rt))
                return
            rt.store.config["dm_keywords"] = words
            # Adding a new interest opts in; removal preserves a paused
            # filter and switches it off when the last keyword is removed.
            enabled = bool(words) and (action == "add" or config_flag(before.get("dm_enabled"), False))
        rt.store.config["dm_enabled"] = enabled
        ok, note = await rt.bot.save_config(rt.store, "Kişisel bildirim ayarı güncellendi")
        if not ok:
            rt.store.restore(before)
            await control_reply(rt, event, "❌ Kaydedilemedi; önceki ayar korundu.\n" + note)
            return
        pending = peek_pending(pending_key(event))
        if pending and pending.get("stage") == "dm_words":
            drop_pending(pending_key(event))
        await control_reply(rt, event, "✅ Anında uygulandı. Grup ayarları değişmedi.\n" + dm_status(rt) + "\n" + note)


async def handle_dm_command(rt: Runtime, event, command, rest):
    if not isinstance(event, PrivateControlEvent) or event.sender_id != appstate.SELF_ID:
        await control_reply(rt, event, "🎯 Kişisel ayarlar yalnızca hesap sahibinin botla özel sohbetinden yönetilir.")
        return
    if command == COMMAND_DM_STATUS:
        await control_reply(rt, event, dm_status(rt) + (
            "\n\nBu komut yalnızca bilgi gösterir. Kural eklemek / çıkarmak için /ekle veya "
            "/cikar → dmdahil / dmharic / dmfiyat / ürün adı < fiyat seçip /kaydet kullanın."
            if rest else ""
        ))
    elif command == COMMAND_DM_FILTER_OPEN:
        enabled = True
        if enabled and not rt.store.config.get("dm_keywords"):
            await control_reply(rt, event, "Önce /ekle → dmdahil ile kelimelerini ekleyip /kaydet. Boş liste hiçbir şeyi göndermez.")
            return
        await save_dm_setting(rt, event, enabled=enabled)
    elif command == COMMAND_DM_FILTER_CLOSE:
        await save_dm_setting(rt, event, enabled=False)
    else:
        await control_reply(rt, event, f"Bilinmeyen komut: {command}\n\n{HELP_TEXT}")
