"""Fırsat teslimi: hedefe kopyalama/iletme, bot bildirimi ve yeniden deneme."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

from telethon import errors, events
from telethon.tl import types

from botentities import (
    bot_api_entities,
    bot_media_descriptor,
    composed_has_content,
    media_buffer,
    media_upload_name,
    reupload_attributes,
    source_name_entity,
)
from constants import BOT_API_MEDIA_LIMIT_MB, CAPTION_LIMIT, MESSAGE_LIMIT
from dmcontrol import enqueue_private_offer
from producttitle import _search_query
from runtime import Runtime
from sanitize import first_sent_entities, first_sent_text, sanitize_message, sent_message_ids
from searchbuttons import build_inline_keyboard

log = logging.getLogger("telegram-filter")


async def delete_account_copy(rt: Runtime, sent_ids: Sequence[int], source_name: str) -> bool:
    """Bildirim botu gönderdikten sonra hedefteki hesap kopyasını sil.

    Böylece grupta her fırsat tek mesaj olarak kalır: botun bildirimi
    (telefonuna uyarı düşen mesaj). Bildirim gidemezse kopya SİLİNMEZ;
    mesajsız kalmaktansa iki kopya iyidir. Silme başarısız olursa yalnızca
    uyarı loglanır, ileti yine başarılı sayılır.
    """
    if not sent_ids or rt.bot.DESTINATION_ID is None:
        return False
    try:
        await rt.client.delete_messages(rt.bot.DESTINATION, list(sent_ids), revoke=True)
    except Exception as exc:  # noqa: BLE001 - silinemezse mesaj kalsın
        log.warning("Hesap kopyası silinemedi (kaynak=%s): %s: %s",
                    source_name, type(exc).__name__, exc)
        return False
    rt.bot.STATS["cleaned"] += 1
    log.info("Hesap kopyası silindi (%d mesaj); grupta bot bildirimi kaldı (kaynak=%s).",
             len(sent_ids), source_name)
    return True


def source_of(rt: Runtime, event: events.NewMessage.Event) -> dict[str, Any] | None:
    """Kaynak kaydını bul (kaynak adı, kullanıcı adı, t.me linki için)."""
    return next((item for item in rt.bot.SOURCES if item["id"] == event.chat_id), None)


def offer_link(rt: Runtime, event: events.NewMessage.Event) -> str | None:
    return rt.bot.build_message_link(event, source_of(rt, event)) if rt.bot.MESSAGE_LINK_LINE else None


async def send_forward(rt: Runtime, event: events.NewMessage.Event) -> Any:
    return await rt.client.forward_messages(rt.bot.DESTINATION, event.message, from_peer=event.chat_id)


async def send_copy(rt: Runtime, event: events.NewMessage.Event) -> Any:
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
    link = offer_link(rt, event)
    source_record = source_of(rt, event)
    footer_name = (source_record or {}).get("name") if rt.bot.SOURCE_FOOTER else None
    if media and not is_webpage:
        composed = rt.bot.compose_message(
            event, limit=CAPTION_LIMIT - 24, link_kinds=rt.bot.LINK_KINDS,
            message_link=link, source_name=footer_name,
        )
        return await rt.client.send_file(
            rt.bot.DESTINATION,
            media,
            caption=composed["text"],
            formatting_entities=composed["entities"],
            force_document=False,
        )
    composed = rt.bot.compose_message(
        event, limit=MESSAGE_LIMIT - 100, link_kinds=rt.bot.LINK_KINDS,
        message_link=link, source_name=footer_name,
    )
    return await rt.client.send_message(
        rt.bot.DESTINATION,
        composed["text"],
        formatting_entities=composed["entities"],
        link_preview=False,
    )


async def send_media(rt: Runtime, event: events.NewMessage.Event) -> Any:
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
    if rt.bot.MAX_MEDIA_MB and size and size > rt.bot.MAX_MEDIA_MB * 1024 * 1024:
        raise ValueError(f"medya {size // (1024 * 1024)} MB, sınır {rt.bot.MAX_MEDIA_MB} MB")
    data = await rt.client.download_media(message, bytes)
    if not data:
        raise ValueError("medya indirilemedi")
    # Aynı teslim içinde bildirim botu da aynı medyayı kullanacaksa
    # ikinci kez indirilmesin (bkz. notify_offer).
    _media_cache = rt.bot.MEDIA_DOWNLOAD_CACHE.get()
    if _media_cache is not None:
        try:
            _media_cache[(int(event.chat_id), int(event.id))] = data
        except (TypeError, ValueError):
            pass
    source_record = source_of(rt, event)
    footer_name = (source_record or {}).get("name") if rt.bot.SOURCE_FOOTER else None
    composed = rt.bot.compose_message(
        event, limit=CAPTION_LIMIT - 24, link_kinds=rt.bot.LINK_KINDS,
        message_link=offer_link(rt, event), source_name=footer_name,
    )
    return await rt.client.send_file(
        rt.bot.DESTINATION,
        media_buffer(data, media_upload_name(message)),
        caption=composed["text"] or None,
        formatting_entities=composed["entities"],
        attributes=reupload_attributes(message),
        force_document=False,
    )


async def send_text_only(rt: Runtime, event: events.NewMessage.Event) -> Any:
    # Gövdeye ek olarak gizli linkler (varsa) ve kaynak adına gömülü mesaj
    # adresi eklenir; ürün linki bir şekilde kaçsa bile tek dokunuşla fırsata
    # ulaşılır (ad yazılamazsa "🔗 Mesajı Gör" satırı geri gelir).
    source_record = source_of(rt, event)
    footer_name = (source_record or {}).get("name") if rt.bot.SOURCE_FOOTER else None
    composed = rt.bot.compose_message(
        event, limit=MESSAGE_LIMIT - 400, link_kinds=rt.bot.LINK_KINDS,
        message_link=offer_link(rt, event), source_name=footer_name,
    )
    if not composed_has_content(composed):
        raise ValueError("mesajda metin yok")
    has_media = bool(getattr(event.message, "media", None))
    note = "\n\n⚠️ Kaynak medyayı korumalı işaretlediği için medya iletilemedi." if has_media else ""
    return await rt.client.send_message(
        rt.bot.DESTINATION,
        composed["text"] + note,
        formatting_entities=composed["entities"],
        link_preview=False,
    )


async def send_link_card(rt: Runtime, event: events.NewMessage.Event) -> Any:
    """Son çare: kaynak adı + t.me bağlantısı. Ekranda görülebilir tek şey budur."""
    link = rt.bot.build_message_link(event, source_of(rt, event))
    if not link:
        raise ValueError("bu sohbet türü için t.me bağlantısı üretilemiyor")
    source_record = source_of(rt, event)
    footer_name = (source_record or {}).get("name") if rt.bot.SOURCE_FOOTER else None
    composed = rt.bot.compose_message(
        event, limit=2000, link_kinds=rt.bot.LINK_KINDS,
        message_link=link, source_name=footer_name,
        hidden_links=False,  # bu ileti biçimsiz gider; URL metinde görünmeli
    )
    if composed_has_content(composed):
        body = composed["text"]
    else:
        body = f"🔗 {rt.bot.STATS['last_match_source'] or 'kaynak'} kanalındaki mesaj:\n{link}"
        if composed["appendix"]:
            body += f"\n{composed['appendix']}"
        body += "\n(medya korumalı olduğu için iletilemedi, bağlantıdan açabilirsin)"
    return await rt.client.send_message(rt.bot.DESTINATION, body[:MESSAGE_LIMIT], link_preview=False)


async def send_account_with_retry(rt: Runtime, mode: str, event: Any) -> Any:
    # Account fallback must not silently drop an offer on FloodWait either.
    # Keep other offer sends behind this wait rather than stampeding Telegram.
    async with rt.account_send_lock:
        while True:
            try:
                return await rt.senders[mode](event)
            except (errors.FloodWaitError, errors.SlowModeWaitError) as exc:
                wait = max(1, exc.seconds) + 1
                log.warning("Hesap gönderimi hız sınırı (%s): %s sn beklenip aynı fırsat yeniden denenecek.",
                            mode, wait)
                await asyncio.sleep(wait)


async def notify_offer(rt: Runtime, 
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
    if not rt.bot.NOTIFY_BOT_TOKEN or rt.bot.DESTINATION_ID is None:
        return False, no_info, ""
    message_link = offer_link(rt, event)
    footer_name = source_name if rt.bot.SOURCE_FOOTER else None

    descriptor = bot_media_descriptor(event) if rt.bot.NOTIFY_MEDIA else None
    if descriptor is not None:
        limit_mb = min(BOT_API_MEDIA_LIMIT_MB.get(descriptor["kind"], 50),
                       rt.bot.MAX_MEDIA_MB or BOT_API_MEDIA_LIMIT_MB.get(descriptor["kind"], 50))
        if descriptor["size"] and descriptor["size"] > limit_mb * 1024 * 1024:
            log.info("Bildirim medyası %s MB, sınır %s MB → metin olarak gönderilecek.",
                     descriptor["size"] // (1024 * 1024), limit_mb)
            descriptor = None

    if descriptor is not None:
        composed = rt.bot.compose_message(
            event, limit=CAPTION_LIMIT - 24, link_kinds=rt.bot.BOT_LINK_KINDS,
            message_link=message_link, source_name=footer_name, has_keyboard=True,
            source_name_url=message_link,
        )
        entities = bot_api_entities(composed["message"], composed["content"]) + source_name_entity(composed)
        keyboard = build_inline_keyboard(event, body_text=composed["text"], body_entities=entities)
        t_media = rt.bot.time.perf_counter()
        # Yedek zincirde (send_media) aynı teslim içinde indirildiyse
        # yeniden indirme; önbellek yalnızca bu deliver() bağlamında yaşar.
        data = None
        _media_cache = rt.bot.MEDIA_DOWNLOAD_CACHE.get()
        if _media_cache is not None:
            try:
                data = _media_cache.get((int(event.chat_id), int(event.id)))
            except (TypeError, ValueError):
                data = None
        if data is None:
            try:
                data = await rt.client.download_media(event.message, bytes)
            except Exception as exc:  # noqa: BLE001 - medya inmezse bildirim yine gitsin
                log.warning("Bildirim medyası indirilemedi (%s): %s", type(exc).__name__, exc)
                data = None
        t_downloaded = rt.bot.time.perf_counter()
        if data:
            log.info("⏱ Bildirim medyası: %s, %.1f MB, indirme %.1f sn.",
                     descriptor["kind"], len(data) / (1024 * 1024), t_downloaded - t_media)
            rt.bot.MEDIA_TIMING.set({"kind": descriptor["kind"], "mb": len(data) / (1024 * 1024),
                              "download": t_downloaded - t_media, "upload": 0.0})
            send_result = await rt.bot.send_bot_media(
                rt.bot.NOTIFY_BOT_TOKEN, rt.bot.DESTINATION_ID,
                kind=descriptor["kind"], filename=descriptor["filename"],
                mime_type=descriptor["mime"], data=data,
                caption=composed["text"], entities=entities, keyboard=keyboard,
            )
            ok, detail = send_result
            if ok:
                upload_seconds = rt.bot.time.perf_counter() - t_downloaded
                log.info("Bildirim gönderildi (medya: %s, kaynak: %s, yükleme %.1f sn).",
                         descriptor["kind"], source_name, upload_seconds)
                timing = rt.bot.MEDIA_TIMING.get()
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

    composed = rt.bot.compose_message(
        event, limit=MESSAGE_LIMIT - 200, link_kinds=rt.bot.BOT_LINK_KINDS,
        message_link=message_link, source_name=footer_name, has_keyboard=True,
        source_name_url=message_link,
    )
    entities = bot_api_entities(composed["message"], composed["content"]) + source_name_entity(composed)
    text = composed["text"] or f"🔔 Yeni fırsat – {source_name}"
    keyboard = build_inline_keyboard(event, body_text=text, body_entities=entities)
    send_result = await rt.bot.send_bot_ping(
        rt.bot.NOTIFY_BOT_TOKEN, rt.bot.DESTINATION_ID, text,
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


async def notify_offer_with_retry(rt: Runtime, 
    event: events.NewMessage.Event, source_name: str,
) -> tuple[bool, dict[str, Any], str]:
    """Explicit gateway failures are retryable; ambiguous network errors are not replayed."""
    retry_delays = (0.5, 2.0)
    for attempt in range(len(retry_delays) + 1):
        sent, info, detail = await notify_offer(rt, event, source_name)
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


async def deliver(rt: Runtime, 
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
    rt.bot.MEDIA_DOWNLOAD_CACHE.set({})
    t_begin = rt.bot.time.perf_counter()
    try:
        received_lag = max(0.0, rt.bot.time.time() - event.message.date.timestamp())
    except Exception:  # noqa: BLE001 - tarih yoksa ölçüm atlanır
        received_lag = -1.0
    skip_bot = False

    def note_notification_failure(detail: str = "") -> None:
        if rt.bot.NOTIFY_BOT_TOKEN and rt.bot.DESTINATION_ID is not None:
            rt.bot.STATS["notification_failed"] = int(rt.bot.STATS.get("notification_failed", 0) or 0) + 1
            log.error("Hedef içeriği iletilmiş olsa da telefon bildirimi başarısız (kaynak=%s, mesaj=%s, neden=%s); toplam=%d.",
                      source_name, getattr(event, "id", "?"), detail or "bilinmeyen hata",
                      rt.bot.STATS["notification_failed"])

    if rt.bot.FAST_NOTIFY and rt.bot.NOTIFY_BOT_TOKEN and rt.bot.DESTINATION_ID is not None:
        # Hızlı yol: önce bildirim botu. Başarılıysa hesap kopyası hiç atılmaz
        # (silinecek bir şey kalmaz); başarısızsa normal zincir aynen çalışır.
        bot_sent, bot_info, bot_error = await notify_offer_with_retry(rt, event, source_name)
        if bot_sent:
            rt.bot.STATS["forwarded"] += 1
            rt.bot.STATS["modes"]["bot"] = rt.bot.STATS["modes"].get("bot", 0) + 1
            info = {
                "mode": "bot", "bot": bot_info, "account_ids": [], "account_deleted": False,
                "account_text": None, "account_entities": None,
            }
            try:
                enqueue_private_offer(rt, event, info)
            except Exception as exc:
                log.warning("Özel kopya kuyruğa alınamadı; grup korunuyor: %s", type(exc).__name__)
            log.info("⏱ %s | telegram→bot %.1f sn | bildirim (hızlı yol) %.1f sn",
                     source_name, received_lag, rt.bot.time.perf_counter() - t_begin)
            rt.bot.maybe_report_slow(rt.client, source_name, received_lag, [("Bildirim (hızlı yol)", rt.bot.time.perf_counter() - t_begin)])
            return True, "bot", info
        note_notification_failure(bot_error)
        skip_bot = True
        log.info("Hızlı bildirim gönderilemedi → normal iletim zincirine dönülüyor (kaynak: %s).", source_name)
    sanitized = sanitize_message(event)
    modes = rt.bot.DELIVERY_CHAIN
    product_title = _search_query(sanitized)
    if sanitized.changed or product_title:
        # Forward özgün iletiyi değiştirmeden taşır; temizleme gereken veya
        # üstüne ürün başlığı eklenecek iletilerde kopyalama zincirine geç
        # ki hem çıktı düzeni (başlık en üstte) korunsun hem de yasaklı
        # içerik kaçmasın. Zincirde yalnızca forward varsa yine forward
        # denenir: hiç iletmemektense başlıksız iletmek iyidir.
        filtered = [mode for mode in rt.bot.DELIVERY_CHAIN if mode != "forward"]
        modes = filtered or list(rt.bot.DELIVERY_CHAIN)
        reason = "temizleme" if sanitized.changed else "ürün başlığı"
        log.info("Mesaj %s gerektiriyor; 'forward' atlanacak (kaynak: %s).", reason, source_name)
    for mode in modes:
        try:
            result = await send_account_with_retry(rt, mode, event)
        except Exception as exc:  # noqa: BLE001 - bir yol patlarsa sıradakini dene
            last_error = f"{type(exc).__name__}: {exc}"
            log.info("İletim yolu '%s' başarısız (%s) → sıradaki deneniyor.", mode, last_error)
            continue
        rt.bot.STATS["forwarded"] += 1
        rt.bot.STATS["modes"][mode] = rt.bot.STATS["modes"].get(mode, 0) + 1
        if mode not in ("forward", "copy"):
            log.info("Mesaj '%s' yedeğiyle iletildi (kaynak: %s).", mode, source_name)
        account_ids = sent_message_ids(result)
        t_account = rt.bot.time.perf_counter()
        # Tek mesaj modu: bildirim botu gönderdiyse hesabın attığı kopyayı sil.
        if skip_bot:
            bot_sent, bot_info = False, {}
        else:
            bot_sent, bot_info, bot_error = await notify_offer_with_retry(rt, event, source_name)
            if not bot_sent:
                note_notification_failure(bot_error)
        t_notified = rt.bot.time.perf_counter()
        account_deleted = False
        if bot_sent and rt.bot.SINGLE_MESSAGE:
            account_deleted = await delete_account_copy(rt, account_ids, source_name)
        log.info("⏱ %s | telegram→bot %.1f sn | hesap kopyası %.1f sn | bildirim %.1f sn | silme %.1f sn | toplam %.1f sn",
                 source_name, received_lag, t_account - t_begin, t_notified - t_account,
                 rt.bot.time.perf_counter() - t_notified, rt.bot.time.perf_counter() - t_begin)
        rt.bot.maybe_report_slow(rt.client, source_name, received_lag, [
            ("Hesap kopyası", t_account - t_begin), ("Bildirim", t_notified - t_account),
            ("Silme", rt.bot.time.perf_counter() - t_notified),
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
            enqueue_private_offer(rt, event, info)
        except Exception as exc:
            log.warning("Özel kopya kuyruğa alınamadı; grup korunuyor: %s", type(exc).__name__)
        return True, mode, info

    rt.bot.STATS["failed"] += 1
    log.error("Hiçbir iletim yolu çalışmadı (kaynak=%s, mesaj=%s). Son hata: %s",
              source_name, getattr(event, "id", "?"), last_error)
    return False, last_error, {}
