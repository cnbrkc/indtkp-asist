"""Kaynak mesaj dinleme: olay işleme ve hızlı tarama döngüsü."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from telethon import errors, events, utils
from telethon.tl import functions, types

from dedupflow import dedup_after_send, dedup_before_send
from dedupmatch import dedup_offer_key, next_poll_interval, offer_terms, recover_poll_interval
from delivery import deliver
from keywords import matches, normalize
from runtime import Runtime
from sanitize import WORD_TOKEN_RE, _as_message, extract_links, is_blank_reply
from smarttitle import smart_dedup_title

log = logging.getLogger("telegram-filter")


async def on_new_message(rt: Runtime, event: events.NewMessage.Event) -> None:
    if event.chat_id not in rt.bot.SOURCE_IDS or event.chat_id in rt.bot.CONTROL_IDS:
        return
    if rt.bot.DESTINATION_ID is not None and event.chat_id == rt.bot.DESTINATION_ID:
        return  # hedefe kendi gönderdiğimiz mesajı tekrar iletmeyelim
    if rt.bot.RESTARTING:
        return  # bilinçli restart drain — mesaj cursor ile kurtarılır
    if rt.bot.live_event_should_skip(event):
        return  # aynı mesaj hızlı taramayla zaten işlendi
    # cursor ilk katman — ID bazlı tekrar engeli (yalnızca RAM, persist’siz)
    # Yeni grup & ilk poll öncesi race: RAM’de yoksa bootstrap (patlama yok)
    try:
        _cid = int(event.chat_id); _mid = int(event.id)
        if _cid not in rt.bot.CURSOR_RAM:
            # İlk live update mesajı tek başına güvenli bir başlangıç noktası
            # değildir: update'ler eşzamanlı işlenirken daha eski ID önce
            # gelebilir. Poll, sınırlı/yaş filtreli bootstrap'ı belirleyecek.
            rt.bot.CURSOR_RAM[_cid] = {"cursor": 0, "pending": set(), "completed": set(),
                                "failed": set(), "failed_at": {}, "initialized": False}
            log.info("Yeni kaynak canlı olayla görüldü; güvenli cursor poll'de belirlenecek: %s", _cid)
        _cur = int(rt.bot.CURSOR_RAM.get(_cid, {}).get("cursor", 0) or 0)
        if _mid <= _cur:
            return
    except Exception:
        pass
    key = (event.chat_id, event.id)
    rt.bot.SOURCE_IN_FLIGHT.add(key)
    rt.bot._cursor_mark_pending(int(event.chat_id), int(event.id))
    try:
        await process_source_event(rt, event)
    except BaseException:
        rt.bot.LIVE_SEEN.pop(key, None)
        rt.bot.POLL_DISPATCHED.pop(key, None)
        rt.bot._cursor_mark_failed(int(event.chat_id), int(event.id))
        raise
    finally:
        rt.bot.SOURCE_IN_FLIGHT.discard(key)


async def process_source_event(rt: Runtime, event: Any) -> None:
    rt.bot.STATS["seen"] += 1
    source_seen = rt.bot.STATS.setdefault("source_seen", {})
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
        rt.bot._cursor_mark_success(int(event.chat_id), int(event.id))
        return
    if not matches(text, rt.bot.FILTER_INCLUDE, rt.bot.FILTER_EXCLUDE, rt.bot.FILTER_MODE,
                   include_enabled=rt.bot.FILTER_INCLUDE_ENABLED,
                   exclude_enabled=rt.bot.FILTER_EXCLUDE_ENABLED):
        log.debug("Eşleşmedi (chat=%s id=%s): %.80s", event.chat_id, event.id, text)
        rt.bot._cursor_mark_success(int(event.chat_id), int(event.id))
        return

    rt.bot.STATS["matched"] += 1
    rt.bot.STATS["last_match"] = rt.bot.time.time()
    source_name = next((item["name"] for item in rt.bot.SOURCES if item["id"] == event.chat_id), str(event.chat_id))
    rt.bot.STATS["last_match_source"] = source_name
    log.info("Eşleşti: %s / mesaj %s / %.80s", source_name, event.id, text)

    # Tekrar birleştirme: aynı ürün sorgusu pencere içindeyse gruba yeni mesaj
    # ATILMAZ; ilk mesaja rozet işlenir. Kayıp hedefte yeni bildirim gönderilir.
    # Ürün sorgusu aynı temizlenmiş/gönderilecek içerikten ayıklanır.
    # Product identity must not vary with the optional display-header setting.
    dedup_title = smart_dedup_title(event)
    terms = offer_terms(event) if rt.bot.DEDUP_ENABLED else (None, "")
    dedup_id = dedup_offer_key(dedup_title, terms) if rt.bot.DEDUP_ENABLED else None
    if not dedup_id:
        ok, _, _ = await deliver(rt, event, source_name)
        if not ok:
            rt.bot.LIVE_SEEN.pop((event.chat_id, event.id), None)
            rt.bot.POLL_DISPATCHED.pop((event.chat_id, event.id), None)
            rt.bot._cursor_mark_failed(int(event.chat_id), int(event.id))
        else:
            rt.bot._cursor_mark_success(int(event.chat_id), int(event.id))
        return
    decision, token, effective_dedup_id = await dedup_before_send(rt, 
        dedup_id, dedup_title, source_name,
        footer_name=source_name if rt.bot.SOURCE_FOOTER else None,
        offer=terms, event=event,
        source_link=rt.bot.build_message_link(
            event, next((item for item in rt.bot.SOURCES if item["id"] == event.chat_id), None)),
    )
    if decision == "dup":
        rt.bot.STATS["deduped"] += 1
        log.info("Tekrar birleştirildi (kaynak=%s): %.60s", source_name, dedup_title)
        rt.bot._cursor_mark_success(int(event.chat_id), int(event.id))
        return
    ok, info = False, None
    try:
        ok, _, info = await deliver(rt, event, source_name)
    finally:
        await dedup_after_send(rt, effective_dedup_id, token, ok, info)
        if not ok:
            rt.bot.LIVE_SEEN.pop((event.chat_id, event.id), None)
            rt.bot.POLL_DISPATCHED.pop((event.chat_id, event.id), None)
            rt.bot._cursor_mark_failed(int(event.chat_id), int(event.id))
        else:
            rt.bot._cursor_mark_success(int(event.chat_id), int(event.id))


async def fast_poll_loop(rt: Runtime) -> None:
    """Kaynakları tek istekle (getPeerDialogs) yoklar; yeni mesajı canlı olayla aynı yoldan işler."""
    input_peers: dict[int, Any] = {}
    failures = 0
    poll_interval = rt.bot.FAST_POLL_SECONDS
    rt.bot.POLL_TELEMETRY["interval"] = poll_interval
    log.info("⚡ Hızlı tarama açık: kaynaklar %.0f sn'de bir yoklanacak.", rt.bot.FAST_POLL_SECONDS)

    delivery_slots = asyncio.Semaphore(4)

    async def run_event(event: Any) -> None:
        try:
            async with delivery_slots:
                await on_new_message(rt, event)
        except Exception as exc:  # noqa: BLE001 - tek mesaj hatası taramayı durdurmasın
            log.warning("Hızlı tarama mesajı işlenemedi (%s): %s", type(exc).__name__, exc)

    source_retry_log_at: dict[tuple[int, str], float] = {}
    source_not_before: dict[int, float] = {}

    def defer_source_after_flood(chat_id: int, exc: BaseException, stage: str) -> None:
        wait = max(1, int(getattr(exc, "seconds", 1) or 1)) + 1
        source_not_before[int(chat_id)] = max(
            source_not_before.get(int(chat_id), 0.0), rt.bot.time.monotonic() + wait,
        )
        log.warning("Kaynak taraması FloodWait (%s, kaynak=%s): %s sn ertelendi; diğer kaynaklar sürüyor.",
                    stage, chat_id, wait)

    def should_log_source_retry(chat_id: int, stage: str) -> bool:
        key = (int(chat_id), stage)
        now = rt.bot.time.monotonic()
        if now - source_retry_log_at.get(key, 0.0) >= 300:
            source_retry_log_at[key] = now
            return True
        return False

    while True:
        try:
            await asyncio.sleep(max(rt.bot.FAST_POLL_SECONDS, poll_interval))
            if not rt.bot.FAST_POLL:
                continue
            poll_started = rt.bot.time.monotonic()
            targets: list[tuple[int, Any]] = []
            for chat_id in sorted(rt.bot.SOURCE_IDS - rt.bot.CONTROL_IDS):
                if source_not_before.get(chat_id, 0.0) > rt.bot.time.monotonic():
                    continue
                peer = input_peers.get(chat_id)
                if peer is None:
                    try:
                        peer = await rt.client.get_input_entity(chat_id)
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
                rt.bot.POLL_TELEMETRY["last_elapsed"] = rt.bot.time.monotonic() - poll_started
                continue
            dialog_started = rt.bot.time.monotonic()
            result = await rt.client(functions.messages.GetPeerDialogsRequest(
                peers=[types.InputDialogPeer(peer) for _, peer in targets],
            ))
            dialog_elapsed = rt.bot.time.monotonic() - dialog_started
            rt.bot.POLL_TELEMETRY["last_dialog_elapsed"] = dialog_elapsed
            if rt.bot.RESTARTING:
                rt.bot.POLL_TELEMETRY["last_elapsed"] = rt.bot.time.monotonic() - poll_started
                await asyncio.sleep(0.5)
                continue
            tops = {utils.get_peer_id(d.peer): int(d.top_message or 0) for d in result.dialogs}
            events_by_source: dict[int, list[Any]] = {}
            for chat_id, peer in targets:
                if rt.bot.RESTARTING:
                    break
                top = tops.get(chat_id)
                if top is None:
                    if should_log_source_retry(chat_id, "dialog"):
                        log.warning("Hızlı tarama yanıtında kaynak diyaloğu yok (kaynak=%s); sonraki turda yeniden denenecek.", chat_id)
                    continue
                cur_entry = rt.bot.CURSOR_RAM.get(chat_id)
                initializing = cur_entry is None or not cur_entry.get("initialized", True)
                if initializing:
                    # Cursor yoksa/toparlanmış kaynaksa sınırlı bir start-up
                    # pencere tara. Eski geçmişi patlatma; son 30 dk ve en
                    # fazla 100 ileti. Geçmiş sorgusu hata verirse cursor'ı
                    # ilerletme, sonraki turda güvenle tekrar dene.
                    try:
                        messages = await rt.client.get_messages(
                            peer, limit=rt.bot.FAST_POLL_BACKFILL_LIMIT, max_id=int(top) + 1,
                        )
                    except errors.FloodWaitError as exc:
                        defer_source_after_flood(chat_id, exc, "bootstrap history")
                        continue
                    except Exception as exc:  # noqa: BLE001 - retry without changing cursor
                        if should_log_source_retry(chat_id, "bootstrap-history"):
                            log.warning("Başlangıç geçmişi çekilemedi (kaynak=%s): %s: %s; sonraki turda yeniden denenecek.",
                                        chat_id, type(exc).__name__, exc)
                        continue
                    cutoff = rt.bot.time.time() - rt.bot.FAST_POLL_BACKFILL_MAX_AGE
                    recent = []
                    for message in messages:
                        try:
                            stamp = message.date.timestamp()
                        except Exception:  # noqa: BLE001 - malformed/missing date is not safe to replay
                            continue
                        if stamp >= cutoff:
                            recent.append(message)
                    recent.sort(key=lambda message: int(message.id))
                    st = rt.bot._cursor_ensure(chat_id)
                    known_ids = set(st["pending"]) | set(st["failed"]) | set(st["completed"])
                    known_ids = {int(mid) for mid in known_ids if int(mid) > 0}
                    if not recent:
                        st["cursor"] = min(int(top), min(known_ids) - 1) if known_ids else int(top)
                        st["initialized"] = True
                        rt.bot._cursor_advance(chat_id)
                        log.info("Yeni kaynak için cursor bootstrap: %s → %s (son %d ileti içinde son %.0f dk'da ileti yok).",
                                 chat_id, st["cursor"], rt.bot.FAST_POLL_BACKFILL_LIMIT, rt.bot.FAST_POLL_BACKFILL_MAX_AGE / 60)
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
                            rt.bot._cursor_mark_success(chat_id, stale_id)
                    messages = recent
                    log.info("Yeni kaynak için sınırlı kurtarma: %s → cursor=%s, %d ileti (son %.0f dk).",
                             chat_id, st["cursor"], len(messages), rt.bot.FAST_POLL_BACKFILL_MAX_AGE / 60)
                    cur = int(st["cursor"])
                else:
                    cur = int(cur_entry.get("cursor", 0) or 0)
                    if top <= cur:
                        continue
                    try:
                        # oldest-first, capped batch: a noisy source cannot
                        # monopolize this pass; remaining IDs stay beyond cursor.
                        messages = await rt.client.get_messages(
                            peer, min_id=cur, limit=rt.bot.FAST_POLL_BATCH_LIMIT, reverse=True,
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
                    st = rt.bot._cursor_ensure(chat_id)
                    for gid in range(int(st["cursor"]) + 1, max_fetched + 1):
                        if gid in fetched_ids:
                            break
                        if gid in st["pending"] or gid in st["failed"]:
                            break
                        st["cursor"] = gid
                    rt.bot._cursor_advance(chat_id)
                    cur = int(st["cursor"])
                for message in messages:
                    mid = int(getattr(message, "id", 0) or 0)
                    if mid <= cur or (chat_id, mid) in rt.bot.LIVE_SEEN:
                        continue
                    rt.bot._remember(rt.bot.POLL_DISPATCHED, chat_id, mid)
                    event = events.NewMessage.Event(message)
                    try:
                        event._set_client(rt.client)
                    except Exception:  # noqa: BLE001 - attach client even without entity cache
                        event._client = rt.client
                    event._fast_poll = True
                    log.info("⚡ Hızlı tarama yeni mesajı yakaladı (kaynak=%s, mesaj=%s).", chat_id, mid)
                    events_by_source.setdefault(chat_id, []).append(event)
            # Interleave dispatch by source: a backlog in the first channel
            # must not put every other source behind it in the delivery lock.
            if not rt.bot.RESTARTING:
                source_order = sorted(events_by_source)
                while any(events_by_source.values()):
                    for chat_id in source_order:
                        pending_events = events_by_source[chat_id]
                        if not pending_events:
                            continue
                        task = asyncio.create_task(run_event(pending_events.pop(0)))
                        rt.poll_delivery_tasks.add(task)
                        task.add_done_callback(rt.poll_delivery_tasks.discard)
            # Measure the whole scan pass (entity resolution, dialog request,
            # and all per-source history fetches), not just GetPeerDialogs.
            poll_elapsed = rt.bot.time.monotonic() - poll_started
            rt.bot.POLL_TELEMETRY["last_elapsed"] = poll_elapsed
            if poll_elapsed >= max(2.0, rt.bot.FAST_POLL_SECONDS):
                poll_interval = next_poll_interval(poll_interval, poll_elapsed)
                rt.bot.POLL_TELEMETRY["interval"] = poll_interval
                log.info("Tam hızlı tarama turu %.1f sn sürdü (diyalog isteği %.1f sn); aralık %.0f sn'ye çıkarıldı.",
                         poll_elapsed, dialog_elapsed, poll_interval)
            else:
                recovered = recover_poll_interval(poll_interval, rt.bot.FAST_POLL_SECONDS)
                if recovered != poll_interval:
                    poll_interval = recovered
                    rt.bot.POLL_TELEMETRY["interval"] = poll_interval
            failures = 0
        except asyncio.CancelledError:
            raise
        except errors.FloodWaitError as exc:
            wait = max(1, int(getattr(exc, "seconds", 5))) + 1
            poll_interval = next_poll_interval(poll_interval, wait)
            rt.bot.POLL_TELEMETRY["interval"] = poll_interval
            rt.bot.POLL_TELEMETRY["flood_count"] += 1
            rt.bot.POLL_TELEMETRY["last_flood_wait"] = float(wait)
            log.warning("Hızlı tarama FloodWait: %s sn bekleniyor; yeni yoklama aralığı %.0f sn. "
                        "Canlı mesaj dinleme devam ediyor. [bu oturumda tarama FloodWait=%d]",
                        wait, poll_interval, int(rt.bot.POLL_TELEMETRY["flood_count"]))
            await asyncio.sleep(wait)
        except Exception as exc:  # noqa: BLE001 - geçici hata; canlı olaylar çalışmaya devam eder
            failures += 1
            if failures in {1, 5, 20}:
                log.warning("Hızlı tarama hatası (%s): %s", type(exc).__name__, exc)
            await asyncio.sleep(min(30, 2 * failures))
