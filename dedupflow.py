"""Tekrar birleştirme akışı: gönderim öncesi/sonrası kontrol, rozet işleme ve açılış yüklemesi."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from telethon import errors
from telethon.tl import types

import appstate
import time
from analysis import message_title
from botentities import bot_api_entity
from constants import CAPTION_LIMIT, MESSAGE_LIMIT, MESSAGE_LINK_LABEL, PRODUCT_LINK_TEXT
from dedupbadge import (
    _rebase_entities_for_dedup_badge,
    _strip_dedup_badge_details,
    dedup_badge,
    dedup_badge_entities,
    dedup_badge_insertion,
    dedup_badge_links,
    dedup_badge_names,
    dedup_footer_name,
    dedup_source_links,
    note_dedup_source,
    strip_dedup_badge,
)
from dedupmatch import (
    _find_matching_dedup_key,
    _keep_detailed_prefix_title,
    better_or_different_offer,
    bot_history_ids_shared,
    dedup_offer_key,
    is_referenced_multibuy_upgrade,
    is_referenced_product_price_drop,
    new_dedup_entry,
    offer_terms,
    plain_offer_for_upgrade,
    product_asins,
    product_link_ids,
)
from delivery import delete_account_copy
from header import split_header_line
from producttitle import _campaign_title_if_clipped, _search_query
from runtime import Runtime
from sanitize import utf16_length
from searchbuttons import PRODUCT_LINK_LABEL, is_price_line

log = logging.getLogger("telegram-filter")


async def dedup_before_send(rt: Runtime, 
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
        async with appstate.DEDUP_LOCK:
            rt.bot.prune_dedup_cache(appstate.DEDUP_CACHE, time.time(), appstate.DEDUP_WINDOW_SECONDS, appstate.DEDUP_MAX_ENTRIES)
            active_key = (
                key if key in appstate.DEDUP_CACHE
                else (_find_matching_dedup_key(
                    appstate.DEDUP_CACHE, title,
                    product_asins(event) if event is not None else (),
                    product_link_ids(event) if event is not None else (),
                ) or key)
            )
            entry = appstate.DEDUP_CACHE.get(active_key)
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
                appstate.DEDUP_CACHE[target_key] = entry
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
            await dedup_apply_badge(rt, active_key)
            # Deleted/wrong-ID messages must not swallow this occurrence.
            if entry.get("failed"):
                continue
            return "dup", None, active_key
        try:
            await asyncio.wait_for(waiter_event.wait(), timeout=appstate.DEDUP_RESERVE_TIMEOUT)
        except TimeoutError:
            async with appstate.DEDUP_LOCK:
                current = appstate.DEDUP_CACHE.get(active_key)
                if current is entry and current.get("pending"):
                    owner = current.get("owner")
                    if owner is not None and not owner.done():
                        continue  # still sending / waiting for retry_after
                    log.warning("Tekrar rezervasyonu %ss'de bitmedi, devralınıyor (başlık: %.40s).",
                                int(appstate.DEDUP_RESERVE_TIMEOUT), title)
                    current["failed"] = True
                    current["ready"].set()
                    token = object()
                    appstate.DEDUP_CACHE[active_key] = new_dedup_entry(
                        title, token, source_name, footer_name,
                    )
                    note_dedup_source(appstate.DEDUP_CACHE[active_key], source_name, source_link)
                    appstate.DEDUP_CACHE[active_key]["owner"] = asyncio.current_task()
                    appstate.DEDUP_CACHE[active_key]["offer"] = offer
                    appstate.DEDUP_CACHE[active_key]["plain_offer"] = plain_offer_for_upgrade(event, offer) if event is not None else False
                    return "send", token, active_key


async def dedup_after_send(rt: Runtime, 
    key: str, token: Any, success: bool, info: dict[str, Any] | None,
) -> None:
    """Rezervasyonu sonuçlandır: başarılıysa rozet bilgisini yaz, değilse düşür."""
    async with appstate.DEDUP_LOCK:
        entry = appstate.DEDUP_CACHE.get(key)
        if entry is None or entry.get("token") is not token:
            return  # devralınmış ya da budanmış; güncel kayda dokunma
        if not success or (appstate.NOTIFY_BOT_TOKEN and not (info or {}).get("bot")):
            entry["failed"] = True
            entry["ready"].set()
            if appstate.DEDUP_CACHE.get(key) is entry:
                previous = entry.get("upgrade_previous")
                if previous:
                    appstate.DEDUP_CACHE[key] = previous
                else:
                    del appstate.DEDUP_CACHE[key]
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
        await dedup_apply_badge(rt, key)
        async with appstate.DEDUP_LOCK:
            can_delete = (appstate.DEDUP_CACHE.get(key) is entry
                          and entry.get("badge_count") == entry.get("count")
                          and not entry.get("failed"))
        if can_delete:
            async with previous["edit_lock"]:
                try:
                    if previous.get("editable") == "bot" and appstate.DESTINATION_ID is not None:
                        deleted, detail = await rt.bot.delete_bot_message(
                            appstate.NOTIFY_BOT_TOKEN, appstate.DESTINATION_ID, previous["bot_message_id"])
                        if not deleted:
                            log.warning("Eski fırsat silinemedi: %s", detail)
                    elif previous.get("editable") == "account":
                        await delete_account_copy(rt, [previous["account_message_id"]], "indirimli fırsat")
                except Exception as exc:
                    log.warning("Eski fırsat korunuyor: %s", type(exc).__name__)


async def dedup_apply_badge(rt: Runtime, key: str) -> None:
    """İlk mesaja rozet işle; geçici hata akışı durdurmaz."""
    try:
        await _dedup_apply_badge(rt, key)
    except Exception as exc:  # noqa: BLE001 - rozet görevi asla çökmemeli
        log.warning("Rozet görevi beklenmedik hatayla bitti: %s: %s", type(exc).__name__, exc)


async def _dedup_apply_badge(rt: Runtime, key: str) -> None:
    async with appstate.DEDUP_LOCK:
        entry = appstate.DEDUP_CACHE.get(key)
        if entry is None or entry.get("pending") or not entry.get("editable"):
            return
        edit_lock = entry["edit_lock"]
    async with edit_lock:
        async with appstate.DEDUP_LOCK:
            if appstate.DEDUP_CACHE.get(key) is not entry or entry.get("pending") or not entry.get("editable"):
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
            wait = appstate.DEDUP_EDIT_MIN_INTERVAL - (time.time() - float(entry.get("last_edit", 0.0)))
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
                if appstate.DESTINATION_ID is None or not isinstance(bot_message_id, int):
                    break
                if kind == "media":
                    ok, detail = await rt.bot.edit_bot_caption(
                        appstate.NOTIFY_BOT_TOKEN, appstate.DESTINATION_ID, bot_message_id, new_text,
                        entities=new_entities, keyboard=keyboard,
                    )
                else:
                    ok, detail = await rt.bot.edit_bot_text(
                        appstate.NOTIFY_BOT_TOKEN, appstate.DESTINATION_ID, bot_message_id, new_text,
                        entities=new_entities, keyboard=keyboard, link_preview=False,
                    )
                if ok:
                    async with appstate.DEDUP_LOCK:
                        live = appstate.DEDUP_CACHE.get(key)
                        if live is entry:
                            live["text"] = new_text
                            live["entities_bot"] = new_entities
                            live["last_edit"] = time.time()
                            live["edit_fails"] = 0
                            live["badge_count"] = count
                    appstate.STATS["dedup_edits"] += 1
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
                    await rt.client.edit_message(
                        appstate.DESTINATION, account_message_id, new_text,
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
                    async with appstate.DEDUP_LOCK:
                        live = appstate.DEDUP_CACHE.get(key)
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
                async with appstate.DEDUP_LOCK:
                    live = appstate.DEDUP_CACHE.get(key)
                    if live is entry:
                        live["text"] = new_text
                        live["entities_tl"] = new_tl
                        live["last_edit"] = time.time()
                        live["edit_fails"] = 0
                        live["badge_count"] = count
                appstate.STATS["dedup_edits"] += 1
                log.info("Rozet güncellendi (%s paylaşım, %s farklı kaynak, hesap iletisi %s).",
                         count, len(entry.get("sources") or []), account_message_id)
                return
        # Buraya düşüldüyse rozet işlenemedi; iki üst üste hatada bu
        # başlık için düzenlemeyi bırak (birleştirme sürer).
        async with appstate.DEDUP_LOCK:
            live = appstate.DEDUP_CACHE.get(key)
            if live is entry:
                live["edit_fails"] = int(live.get("edit_fails", 0)) + 1
                if live["edit_fails"] >= 2:
                    live["editable"] = None
                    log.info("Rozet iki kez başarısız oldu; bu başlık rozetsiz birleşecek.")


async def dedup_preload(rt: Runtime) -> None:
    """Açılışta hedefin son iletilerini okuyup önbelleği ısıt (tek tarama).

    Güvenle eşlenebilen geçmişte yeniden bildirimi önler. Normal gruptaki
    bot mesajlarının hesap ID'leri Bot API ID'si olarak kullanılamaz.
    İnsan sohbeti ASLA kayda alınmaz: yalnızca hesabın gönderdikleri ve
    bildirim biçimindeki (rozetli/"Mesajı Gör"lü) iletiler işlenir.
    Kayda alınmayan bir ileti en fazla fazladan bir bildirime yol açar;
    yanlış kayıt ise fırsat kaybettirirdi — temkinli taraf seçildi.
    """
    if not appstate.DEDUP_ENABLED or appstate.DEDUP_SCAN_LIMIT <= 0 or appstate.DESTINATION_ID is None:
        return
    try:
        recent: list[Any] = []
        async for message in rt.client.iter_messages(appstate.DESTINATION, limit=appstate.DEDUP_SCAN_LIMIT):
            recent.append(message)
    except Exception as exc:  # noqa: BLE001 - tarama açılışı öldürmemeli
        log.warning("Tekrar önbelleği ısıtılamadı (%s: %s); boş başlanıyor.",
                    type(exc).__name__, exc)
        return
    if appstate.NOTIFY_BOT_TOKEN and not bot_history_ids_shared(appstate.DESTINATION_ID):
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
            if sender == appstate.SELF_ID:
                if appstate.NOTIFY_BOT_TOKEN:
                    continue  # account-only history is NOT a delivered notification
                editable: str | None = "account"
            elif looks_like_deal:
                if appstate.NOTIFY_BOT_TOKEN:
                    bot_id = appstate.NOTIFY_BOT_TOKEN.split(":", 1)[0]
                    if str(sender) != bot_id:
                        continue  # another bot/human is not editable by this bot
                    # Temel grupta ID uzayları ayrı: kayıt bastırma amaçlı
                    # tutulur (editable=None), bot ID'si düzenleme için
                    # KESİNLİKLE kullanılmaz.
                    editable = "bot" if bot_history_ids_shared(appstate.DESTINATION_ID) else None
                else:
                    editable = None
            else:
                continue  # insan sohbeti: kayda alma
            title = _search_query(base) or message_title(base)
            if appstate.SMART_HEADER:
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
            if now - seen_at > appstate.DEDUP_WINDOW_SECONDS:
                continue
            message_id = getattr(message, "id", None)
            if not isinstance(message_id, int) or isinstance(message_id, bool):
                continue
            media = getattr(message, "media", None)
            is_media = bool(media) and not isinstance(media, types.MessageMediaWebPage)
            raw_entities = list(getattr(message, "entities", None) or [])
            async with appstate.DEDUP_LOCK:
                target_key = (
                    key if key in appstate.DEDUP_CACHE
                    else (_find_matching_dedup_key(appstate.DEDUP_CACHE, title) or key)
                )
                existing = appstate.DEDUP_CACHE.get(target_key)
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
                    appstate.DEDUP_CACHE[target_key] = record
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
