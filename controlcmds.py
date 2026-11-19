"""Kontrol sohbeti komutları: ayar, filtre, taslak, analiz ve ana komut dağıtıcısı."""

from __future__ import annotations

import asyncio
import copy
import logging
import re
from typing import Any

from telethon import events

import dm_filters
import timefmt
from analysis import (
    ANALYSIS_QUERY_MAX_LINES,
    analysis_query_pattern,
    analysis_status_id,
    build_analysis_report,
    build_query_lines,
    message_title,
    parse_analysis_request,
    start_analysis_scan,
    update_analysis_scan,
)
from commandtext import (
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
    COMMAND_ANALYZE,
    COMMAND_HELP,
    COMMAND_ID,
    COMMAND_RESTART,
    COMMAND_SETTINGS_SAVE,
    COMMAND_SOURCES,
    COMMAND_START,
    COMMAND_STATUS,
    COMMAND_TEST,
    COMMAND_WEB_TRACK,
    DM_LIST_FIELDS,
    DM_PRICE_FIELD,
    DM_PRODUCT_PRICE_FIELD,
    FILTER_TARGET_META,
    HELP_TEXT,
    PendingKey,
    SETTINGS_COMMANDS,
    TELEGRAM_LIST_FIELDS,
    TELEGRAM_LIST_META,
    WEBTAKIP_FIELD,
    _webtakip_count,
    build_filter_toggle_prompt,
    build_list_category_prompt,
    build_list_change_confirmation,
    build_list_value_prompt,
    build_main_menu_text,
    drop_pending,
    new_filter_edit,
    new_telegram_edit,
    peek_pending,
    pending_key,
    resolve_confirmation_choice,
    resolve_filter_target,
    resolve_telegram_list,
    set_pending,
    stage_filter_toggle,
    stage_telegram_list_change,
    telegram_list_items,
)
from confutil import config_flag
from controlbase import control_reply, is_admin_event, is_control_event
from dmcontrol import dm_status, handle_dm_command, private_channel_health, save_dm_setting
from keywords import normalize
from private_bot import PrivateControlEvent
from runtime import Runtime
from sanitize import message_text
from sourcetest import test_source_access
from textutil import humanize

log = logging.getLogger("telegram-filter")


async def apply_setting_change(rt: Runtime, before: dict,
                               groups: set[str] | None = None) -> tuple[list[str], str | None]:
    """Ayar değişikliğini çalışan bot'a uygula.

    ``groups`` verilmezse hangi sohbet gruplarının değiştiği ``before`` ile
    karşılaştırılarak bulunur. Dönen ikinci değer doluysa değişiklik
    geçersiz sayılır ve çağıran taraf geri alır.
    """
    notes = rt.bot.apply_runtime_config(rt.store.config)
    targets = groups if groups is not None else rt.bot.changed_groups(before, rt.store.config)
    resolved, fatal = await rt.bot.resolve_chat_groups(rt.client, rt.store.config, targets, strict=bool(targets))
    return notes + resolved, fatal


async def can_edit_list(rt: Runtime, event, field):
    if (field in DM_LIST_FIELDS or field in {DM_PRICE_FIELD, DM_PRODUCT_PRICE_FIELD}) and (
        not isinstance(event, PrivateControlEvent) or event.sender_id != rt.bot.SELF_ID
    ):
        await control_reply(rt, event, "🎯 Kişisel ayarlar yalnızca hesap sahibinin botla özel sohbetinden yönetilir.")
        return False
    return True


async def save_pending_change(rt: Runtime, event: events.NewMessage.Event,
                             key: PendingKey) -> None:
    """Taslağı çakışma kontrolünden geçir, uygula ve sadece şimdi GitHub'a yaz."""
    async with rt.settings_lock:
        pending = peek_pending(key)
        if pending is None:
            await control_reply(rt, event, "ℹ️ Kaydedilecek bekleyen bir değişiklik yok. Önce /ekle veya /cikar.")
            return
        if pending.get("stage") == "webdetakip":
            # product_tracker kayıtlarını product_watches.json'da anında
            # kalıcı tutar; /ekle akışındaki ayrı /kaydet adımı yoktur.
            drop_pending(key)
            await control_reply(rt, event, "ℹ️ Ürün takip kayıtları product_watches.json'da; bu akışta kayıt anında kalıcılaşır, ayrı /kaydet adımı yok. Ayar taslağı kapatıldı. Takip menüsü: /webtakip")
            return
        if pending.get("stage") != "confirm":
            await control_reply(rt, event, "ℹ️ İşlem henüz tamamlanmadı. Önce istenen liste/değer seçimini yap veya /iptal.")
            return

        field = str(pending.get("field") or "")
        base = pending.get("base_config") or {}
        draft = pending.get("draft_config") or {}
        if field not in TELEGRAM_LIST_FIELDS or field not in draft:
            drop_pending(key)
            await control_reply(rt, event, "❌ Taslak geçersiz olduğu için iptal edildi. Yeniden /ekle veya /cikar ile başla.")
            return

        if not await can_edit_list(rt, event, field):
            return
        before = rt.store.snapshot()
        if before.get(field) != base.get(field):
            drop_pending(key)
            await control_reply(rt, event,
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
        groups = rt.bot.changed_groups(before, candidate)
        rt.store.config = candidate
        notes, fatal = await apply_setting_change(rt, before)
        if fatal:
            rt.store.restore(before)
            rt.bot.apply_runtime_config(rt.store.config)
            if groups:
                await rt.bot.resolve_chat_groups(rt.client, rt.store.config, groups, strict=False)
            drop_pending(key)
            await control_reply(rt, event,
                f"❌ Değişiklik uygulanamadı: {fatal}\n"
                "Önceki ayarlar korundu; taslağı iptal ettim. Grup/kanal bilgisini kontrol edip yeniden dene."
            )
            return

        ok, save_note = await rt.bot.save_config(rt.store, f"{TELEGRAM_LIST_META[field]['title']} güncellendi")
        if not ok:
            rt.store.restore(before)
            rt.bot.apply_runtime_config(rt.store.config)
            if groups:
                await rt.bot.resolve_chat_groups(rt.client, rt.store.config, groups, strict=False)
            set_pending(key, **pending)
            await control_reply(rt, event, "\n".join([
                "❌ Değişiklik dosyaya yazılamadı; çalışan ayarlar geri yüklendi.",
                save_note,
                "Taslak korundu. Dosya erişimini düzelttikten sonra /kaydet ile tekrar deneyebilir veya /iptal edebilirsin.",
            ]))
            return

        drop_pending(key)
        log.info("Telegram liste ayarı kaydedildi: %s (%s)", field, pending.get("action"))
        await control_reply(rt, event, "\n".join([
            "✅ Değişiklik kaydedildi ve çalışan ayarlara uygulandı.",
            f"📂 {TELEGRAM_LIST_META[field]['title']}",
            f"📊 Güncel kayıt sayısı: {len(telegram_list_items(rt.store.config, field))}",
            *notes,
            save_note,
        ]))


async def apply_filter_toggle(rt: Runtime, event: events.NewMessage.Event, action: str, target: str) -> None:
    """Filtreyi aç/kapat, config'e yaz ve GitHub'a gönder (anında uygulanır).

    Dahili ve harici filtre ayrı ayrı yönetilir; ikisi de kapatılabilir.
    Dosyaya yazma başarısız olursa önceki durum geri yüklenir.
    """
    async with rt.settings_lock:
        before = rt.store.snapshot()
        ok, candidate, note = stage_filter_toggle(before, action, target)
        if not ok:
            drop_pending(pending_key(event))
            await control_reply(rt, event, note)
            return

        rt.store.config = candidate
        rt.bot.apply_runtime_config(rt.store.config)
        what = "Filtreler" if target == "both" else FILTER_TARGET_META[target]["title"] + " filtresi"
        ok_save, save_note = await rt.bot.save_config(
            rt.store, f"{what} {'açıldı' if action == 'open' else 'kapatıldı'}",
        )
        if not ok_save:
            rt.store.restore(before)
            rt.bot.apply_runtime_config(rt.store.config)
            await control_reply(rt, event, "❌ Filtre değişikliği kaydedilemedi; önceki durum geri yüklendi.\n" + save_note)
            return

        drop_pending(pending_key(event))
        log.info("Filtre %s: %s (hedef=%s)", "açıldı" if action == "open" else "kapatıldı",
                 what, target)
        await control_reply(rt, event, note + "\n" + save_note)


async def handle_filter_command(rt: Runtime, event: events.NewMessage.Event, action: str, rest: str) -> None:
    """`/tgfiltreac` ve `/tgfiltrekapat`: argümanla hemen uygula, argüman yoksa sor."""
    key = pending_key(event)
    pending = peek_pending(key)
    if pending and pending.get("stage") == "confirm":
        await control_reply(rt, event,
            "📝 Önce bekleyen liste taslağını sonuçlandır.\n"
            "✅ /kaydet ile kaydet veya ↩️ /iptal ile vazgeç."
        )
        return

    raw = rest.strip()
    if not raw:
        set_pending(key, **new_filter_edit(action))
        await control_reply(rt, event, build_filter_toggle_prompt(action, rt.store.config))
        return

    target = resolve_filter_target(raw)
    if target is None:
        # Soru açık kalsın: kullanıcı 1/2/3 yazabilir ya da /iptal edebilir.
        set_pending(key, **new_filter_edit(action))
        await control_reply(rt, event, "\n".join([
            f"❌ '{raw}' anlaşılmadı.",
            "1 (dahili), 2 (harici) veya 3 (ikisi) yazabilirsin.",
            "",
            build_filter_toggle_prompt(action, rt.store.config),
        ]))
        return

    await apply_filter_toggle(rt, event, action, target)


async def list_query_matches(rt: Runtime, event: events.NewMessage.Event, targets: list[Any],
                             per_source: int, query: str) -> None:
    """Tüm kaynakların son ``per_source`` mesajında ``query`` geçenleri listele.

    Tarama, istatistik analiziyle aynı okuma yolunu kullanır (salt okuma;
    hiçbir mesaj gönderilmez/değiştirilmez). Tarama boyunca "Analiz
    başladı" ilerleme mesajı kaynak kaynak güncellenir, rapor gelince
    silinir. Sonuç satırları ``ürün adı (tam) - fiyat`` biçimindedir; uzun
    listeler ``control_reply`` tarafından birden çok mesaja bölünür.
    """
    pattern = analysis_query_pattern(query)
    started = rt.bot.time.time()
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
            async for message in rt.client.iter_messages(item["id"], limit=per_source):
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
        f" · süre: {humanize(rt.bot.time.time() - started)}",
    ]
    if failures:
        header.append(f"• ⚠️ Okunamayan kaynak: {', '.join(failures)}")
    if not lines:
        await control_reply(rt, event, "\n".join(header + ["", "Eşleşen mesaj bulunamadı."]),
                            stale=[status_id])
        return
    shown = lines[:ANALYSIS_QUERY_MAX_LINES]
    body = header + ["", *shown]
    if len(lines) > len(shown):
        body.append(f"… ilk {len(shown)} gösteriliyor; daha az sonuç için sayı/sorguyu daralt.")
    body.append("ℹ️ Satır biçimi: ürün adı (tam) - fiyat · fiyat satırı varsa en düşük fiyat.")
    await control_reply(rt, event, "\n".join(body), stale=[status_id])


async def analyze_history(rt: Runtime, event: events.NewMessage.Event, rest: str) -> None:
    """Kaynak geçmişinin başlıklarını tara; kelime istatistiklerini gönder.

    Yalnızca mesajın ilk anlamlı satırı (başlık) okunur; her kaynaktan en
    fazla ``per_source`` mesaj alınır. Tek bir kaynak okunamazsa tarama
    durmaz, raporda "okunamayan kaynak" olarak listelenir.
    Sorgu verilirse (``/analiz 1000 tcl 65``) istatistik yerine eşleşen
    ürünlerin listesi döner (bkz. ``list_query_matches``).
    """
    per_source, keep_everything, query = parse_analysis_request(rest)

    targets = [item for item in rt.bot.SOURCES if item.get("joined")]
    if not targets:
        await control_reply(rt, event, "ℹ️ Taranacak kaynak yok. Önce /kaynaklar ile listeyi kontrol et.")
        return

    if query:
        await list_query_matches(rt, event, targets, per_source, query)
        return

    started = rt.bot.time.time()
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
            async for message in rt.client.iter_messages(item["id"], limit=per_source):
                title = message_title(message_text(message))
                if title:
                    titles.append(title)
        except Exception as exc:  # noqa: BLE001 - tek kaynak taramayı durdurmasın
            failures.append(f"{item['name']} ({type(exc).__name__})")
            log.warning("Geçmiş okunamadı (%s): %s: %s", item["name"], type(exc).__name__, exc)
        await update_analysis_scan(status, index, len(targets), f"{len(titles)} başlık")

    analysis = rt.bot.analyze_titles(titles, keep_everything=keep_everything)
    log.info("Kelime analizi: %d başlık, %d tekil, %d kaynak, %s.",
             analysis["counts"]["titles"], analysis["counts"]["unique"], len(targets),
             "tüm kelimeler" if keep_everything else "filtreli")
    report = build_analysis_report(
        analysis,
        config=rt.store.config,
        sources=len(targets),
        per_source=per_source,
        keep_everything=keep_everything,
        elapsed=rt.bot.time.time() - started,
        failures=failures,
    )
    await control_reply(rt, event, report, stale=[status_id])


async def handle_settings_command(rt: Runtime, event: events.NewMessage.Event,
                                  command: str, rest: str) -> None:
    """Üç izinli liste için taslak akışını yönet."""
    key = pending_key(event)
    pending = peek_pending(key)

    if command in CMD_SETTINGS_MENU:
        await control_reply(rt, event, build_main_menu_text())
        return

    if command in CMD_SETTINGS_ADD or command in CMD_SETTINGS_REMOVE:
        if pending and pending.get("stage") == "confirm":
            await control_reply(rt, event,
                "📝 Önce bekleyen taslağı sonuçlandır.\n"
                "✅ /kaydet ile kaydet veya ↩️ /iptal ile vazgeç."
            )
            return
        if rest.strip():
            await control_reply(rt, event,
                "Komutu tek başına gönder: /ekle veya /cikar.\n"
                "Ardından listelerden birini seçip değeri ayrı mesaj olarak yaz."
            )
            return

        action = "add" if command in CMD_SETTINGS_ADD else "remove"
        set_pending(key, **new_telegram_edit(action, rt.store.config))
        webtakip_count = _webtakip_count(rt.store)
        await control_reply(rt, event, build_list_category_prompt(action, rt.store.config, webtakip_count))
        return

    if command in CMD_SETTINGS_SAVE:
        if pending is None:
            await control_reply(rt, event, "ℹ️ Kaydedilecek bekleyen bir değişiklik yok. Başlamak için /ekle veya /cikar.")
        else:
            await save_pending_change(rt, event, key)
        return

    if command in CMD_SETTINGS_REVERT:
        if pending is None:
            await control_reply(rt, event, "ℹ️ İptal edilecek bekleyen bir işlem yok.")
        else:
            drop_pending(key)
            await control_reply(rt, event, "↩️ İşlem iptal edildi. Taslak silindi; ayarlar ve GitHub değişmedi.")
        return

    return


async def handle_pending_message(rt: Runtime, event: events.NewMessage.Event,
                                 raw: str) -> bool:
    """Liste menüsü/değer bekleniyorsa bu mesajı taslak akışına uygula."""
    key = pending_key(event)
    item = peek_pending(key)
    if item is None:
        return False

    text = raw.strip()
    if item.get("stage") == "dm_words":
        if not isinstance(event, PrivateControlEvent) or event.sender_id != rt.bot.SELF_ID:
            return False
        await save_dm_setting(rt, event, action=item["action"], raw=text)
        return True
    if item.get("stage") == "filter":
        action = str(item.get("action") or "open")
        target = resolve_filter_target(text)
        if target is None:
            set_pending(key, **item)
            await control_reply(rt, event, "\n".join([
                "❌ Seçimi anlayamadım. 1, 2, 3 yazabilir ya da filtre adını yazabilirsin.",
                "",
                build_filter_toggle_prompt(action, rt.store.config),
            ]))
            return True
        await apply_filter_toggle(rt, event, action, target)
        return True

    if item.get("stage") == "category":
        field = resolve_telegram_list(text)
        if field is None:
            set_pending(key, **item)
            await control_reply(rt, event, "❌ Seçimi anlayamadım. 1–8 yazabilir; 7 (webdetakip) eski seçimi korur. Listedeki adı da geçer.\n\n"
                                + build_list_category_prompt(str(item.get("action")), rt.store.config, _webtakip_count(rt.store)))
            return True

        if field == WEBTAKIP_FIELD:
            if not isinstance(event, PrivateControlEvent) or event.sender_id != rt.bot.SELF_ID:
                await control_reply(rt, event, "🎯 Kişisel ayarlar yalnızca hesap sahibinin botla özel sohbetinden yönetilir.")
                return True
            if rt.tracker is None:
                drop_pending(key)
                await control_reply(rt, event, "❌ Ürün takibi şu an kullanılamıyor (bildirim botu yapılandırması eksik).")
                return True
            item["stage"] = "webdetakip"
            item["field"] = WEBTAKIP_FIELD
            set_pending(key, **item)
            if str(item.get("action")) == "remove":
                await rt.tracker.job("/webtakipler", event.chat_id, "")
                await control_reply(rt, event, "Çıkarılacak ürünün /webtakipler listesindeki numarasını gönder (ör. 3). İptal: /iptal")
            else:
                await control_reply(rt, event, "Akakçe ürün detay linkini gönder (…akakce.com/…-fiyati,ID.html). Hedef fiyatı bir sonraki mesajda istenecek. İptal: /iptal")
            return True

        if not await can_edit_list(rt, event, field):
            return True
        config = item.get("draft_config") or rt.store.config
        items = telegram_list_items(config, field)
        if item.get("action") == "remove" and not items:
            drop_pending(key)
            await control_reply(rt, event, f"ℹ️ {TELEGRAM_LIST_META[field]['title']} listesi boş; çıkarılacak kayıt yok.")
            return True

        item["stage"] = "value"
        item["field"] = field
        set_pending(key, **item)
        await control_reply(rt, event, build_list_value_prompt(str(item.get("action")), field, config))
        return True

    if item.get("stage") == "webdetakip":
        if rt.tracker is None:
            drop_pending(key)
            await control_reply(rt, event, "❌ Ürün takibi şu an çalışmıyor (bildirim botu yapılandırması eksik).")
            return True
        if not isinstance(event, PrivateControlEvent) or event.sender_id != rt.bot.SELF_ID:
            await control_reply(rt, event, "🎯 Kişisel ayarlar yalnızca hesap sahibinin botla özel sohbetinden yönetilir.")
            return True
        action = str(item.get("action") or "add")
        ok = True
        if action == "add":
            if not text.startswith(("https://www.akakce.com/", "https://akakce.com/")):
                await control_reply(rt, event, "Akakçe ürün detay linki bekleniyor (…akakce.com/…-fiyati,ID.html). İptal: /iptal")
                return True
            ok = await rt.tracker.handle(event, text, existing_pending=False)
        else:
            try:
                number = int(text.split()[0])
            except (ValueError, IndexError):
                await control_reply(rt, event, "Çıkarılacak ürünün /webtakipler listesindeki numarasını gönder (ör. 3). İptal: /iptal")
                return True
            ok = await rt.tracker.handle(event, f"/webtakipsil {number}", existing_pending=False)
        # product_tracker kendi kayıtlarını product_watches.json'da anında
        # kalıcı tutar; bu nedenle ayar taslağına ayrı bir /kaydet adımı yok.
        drop_pending(key)
        if not ok:
            await control_reply(rt, event, "⚠️ İşlem anlaşılamadı; lütfen Akakçe linki veya geçerli bir ürün numarası gönder.")
        return True

    if item.get("stage") == "value":
        action = str(item.get("action") or "")
        field = str(item.get("field") or "")
        if not await can_edit_list(rt, event, field):
            return True
        draft = item.get("draft_config") or rt.store.config
        ok, candidate, values, message = stage_telegram_list_change(draft, field, action, text)
        if not ok:
            set_pending(key, **item)
            prompt = build_list_value_prompt(action, field, draft) if field in TELEGRAM_LIST_FIELDS else ""
            await control_reply(rt, event, "\n".join(part for part in [message, prompt] if part))
            return True

        if action == "add" and field == "source_chats":
            resolved_values = []
            for value in values:
                try:
                    peer_id, _, _ = await rt.bot.resolve_chat(rt.client, value)
                    # Never persist a private invite token in config/Git history.
                    resolved_values.append(peer_id if isinstance(value, str) and rt.bot._INVITE_LINK_RE.fullmatch(value.strip()) else value)
                except Exception as exc:  # noqa: BLE001 - geçersiz kaynak taslağa alınmasın
                    set_pending(key, **item)
                    await control_reply(rt, event, "\n".join([
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
                    await control_reply(rt, event, message)
                    return True

        item["stage"] = "confirm"
        item["draft_config"] = candidate
        item["field"] = field
        item["value"] = values
        set_pending(key, **item)
        await control_reply(rt, event, build_list_change_confirmation(action, field, values, candidate, message))
        return True

    if item.get("stage") == "confirm":
        choice = resolve_confirmation_choice(text)
        if choice == "save":
            await save_pending_change(rt, event, key)
            return True
        if choice == "cancel":
            drop_pending(key)
            await control_reply(rt, event, "↩️ İşlem iptal edildi. Taslak silindi; ayarlar ve GitHub değişmedi.")
            return True
        set_pending(key, **item)
        await control_reply(rt, event, "📝 Taslak hazır. ✅ /kaydet (veya kaydet) ile kaydet; ↩️ /iptal (veya iptal) ile vazgeç.")
        return True

    drop_pending(key)
    return False


async def on_control_message(rt: Runtime, event: events.NewMessage.Event) -> None:
    if not is_control_event(rt, event):
        return
    raw = (event.raw_text or "").strip()
    if not raw:
        return
    command = normalize(raw.split()[0]) if raw.startswith("/") else ""

    if not is_admin_event(rt, event):
        if not command:
            return
        # Sessizce yok saymak yerine net bir hata ver: kullanıcı ID'sini
        # gösterip nasıl yetki vereceğini de söylüyoruz.
        rt.bot.STATS["commands"] += 1
        log.warning("Yetkisiz komut denemesi: %s (chat=%s, sender=%s)",
                    command, event.chat_id, event.sender_id)
        await event.reply(
            "⛔ Bu komutu kullanmaya yetkin yok.\n"
            f"• Komut: {command}\n"
            f"• Kontrol sohbeti: {event.chat_id}\n"
            f"• Senin kullanıcı ID'n: {event.sender_id}\n"
            f"• Yetkili ID'ler: {', '.join(str(i) for i in sorted(rt.bot.ADMIN_IDS)) or 'tanımsız'}\n"
            "Yetki için hesap sahibinden admin_user_id değerini config.json'da güncellemesini iste."
        )
        return

    # Owner-only optional feature. Existing config drafts always retain priority.
    if rt.tracker and isinstance(event, PrivateControlEvent) and event.sender_id == rt.bot.SELF_ID:
        handled = await rt.tracker.handle(event, raw, existing_pending=peek_pending(pending_key(event)) is not None)
        if handled:
            pending = peek_pending(pending_key(event))
            if pending and pending.get("stage") == "webdetakip":
                # Ürün takibi kendi dosyasını anında günceller; /ekle
                # taslağına devam edilmemesi için kapatılır.
                drop_pending(pending_key(event))
                if command in {"/iptal", "/webtakipiptal"}:
                    await control_reply(rt, event, "↩️ Ürün takip taslağı iptal edildi; ayar düzenleme taslağı da kapatıldı.")
                elif command == COMMAND_SETTINGS_SAVE:
                    await control_reply(rt, event, "ℹ️ Ürün takip kayıtları product_watches.json'da; ayar düzenleme taslağı kapatıldı. Takip menüsü: /webtakip")
                else:
                    await control_reply(rt, event, "ℹ️ Ürün takip işlemi işlendi; ayar düzenleme taslağı kapatıldı. Takip menüsü: /webtakip")
            return

    # Komut değil: çok adımlı akışta beklenen değer olabilir.
    if not command:
        await handle_pending_message(rt, event, raw)
        return

    rt.bot.STATS["commands"] += 1
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
        await control_reply(rt, event, "👋 Komutlarını artık burada verebilirsin. Fırsatlar aynı hedef gruba gider.\n"
                            "Grup ayarları: /ayar · Durum: /durum · Tüm komutlar: /komutlar\n"
                            "Kişisel fırsatlar: /dmfiltre · /ekle /cikar → dmdahil / dmharic / dmfiyat / ürün adı < fiyat\n"
                            "Özel sohbet geçmişi silinmez. Kayıtlı Mesajlar yedek kontrol olarak açık.")
    elif command in CMD_DM_COMMANDS:
        await handle_dm_command(rt, event, command, rest)
    elif command == COMMAND_STATUS:
        text = rt.bot.build_status_text(rt.store.config, connected=rt.client.is_connected())
        dm_on = dm_filters.has_active_rules(rt.store.config)
        text += f"\n• 📩 Kişisel DM fırsat motoru: {'ETKİN' if dm_on else 'KAPALI'}"
        if rt.tracker is not None:
            wt_on = bool(rt.tracker.state.get("enabled", False))
            text += f"\n• 🌐 Web ürün takibi: {'AÇIK' if wt_on else 'KAPALI'}"
        if isinstance(event, PrivateControlEvent):
            text += "\n\n" + dm_status(rt)
        await control_reply(rt, event, text)
    elif command == COMMAND_TEST:
        text = (
            f"🧪 Deneme mesajı – {timefmt.local().strftime('%Y-%m-%d %H:%M:%S')} TSİ\n"
            f"Hedef: {rt.bot.DESTINATION_LABEL}\n"
            f"Kaynaklar: {len(rt.bot.SOURCE_IDS)} | Görülen: {rt.bot.STATS['seen']} | Eşleşen: {rt.bot.STATS['matched']}"
        )
        try:
            await rt.client.send_message(rt.bot.DESTINATION, text)
            reply = f"✅ Deneme mesajı gönderildi: {rt.bot.DESTINATION_LABEL}"
        except Exception as exc:  # noqa: BLE001
            await control_reply(rt, event, f"❌ Deneme mesajı gönderilemedi: {type(exc).__name__}: {exc}")
            return
        if rt.bot.NOTIFY_BOT_TOKEN and rt.bot.DESTINATION_ID is not None:
            ok, detail = await rt.bot.send_bot_ping(
                rt.bot.NOTIFY_BOT_TOKEN, rt.bot.DESTINATION_ID,
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
        reply += "\n\n" + await private_channel_health(rt)
        await control_reply(rt, event, reply)
    elif command == COMMAND_SOURCES:
        await control_reply(rt, event, rt.bot.build_source_text())
    elif command in CMD_SOURCE_TEST:
        await test_source_access(rt, event, rest)
    elif command == COMMAND_ID:
        if isinstance(event, PrivateControlEvent):
            await control_reply(rt, event,
                f"🆔 Özel sohbet / kullanıcı ID'n: {event.sender_id}\n"
                f"🎯 Fırsat hedefi değişmedi: {rt.bot.DESTINATION_LABEL}\n"
                "Bu özel sohbet ID'sini destination olarak yazmana gerek yok."
            )
            return
        try:
            chat = await event.get_chat()
            chat_kind = type(chat).__name__
        except Exception:  # noqa: BLE001 - sohbet cache'te olmayabilir
            chat_kind = "bilinmiyor"
        await control_reply(rt, event,
            f"🆔 Bu sohbetin ID'si: {event.chat_id}\n"
            f"• Tür: {chat_kind}\n"
            f"• Senin kullanıcı ID'n: {event.sender_id}\n"
            f"config.json için:\n"
            f'  "control_chat": {event.chat_id},\n'
            f'  "admin_user_id": {event.sender_id}'
        )
    elif command == COMMAND_RESTART:
        # Yalnızca bilinçli /restart’ta cursor yedeği — otomatik restart değil
        rt.bot.RESTARTING = True
        # 1) Uçuş drain — yeni mesajlar RESTARTING bayrağıyla zaten bekletiliyor
        drain_deadline = rt.bot.time.time() + 6
        while rt.bot.time.time() < drain_deadline and (rt.bot.SOURCE_IN_FLIGHT or rt.poll_delivery_tasks):
            await asyncio.sleep(0.2)
        if rt.bot.SOURCE_IN_FLIGHT or rt.poll_delivery_tasks:
            log.warning("Restart drain %ss doldu — %s uçuş + %s poll görevi kaldı; mevcut ardışık cursor ile yedek alınıyor.",
                        6, len(rt.bot.SOURCE_IN_FLIGHT), len(rt.poll_delivery_tasks))
        # 2) Tek toplu persist (mevcut atomic_write_json + GIT_WRITE_LOCK yolunu kullan)
        ok_save, save_note = await rt.bot.cursor_persist_for_restart(rt.store)
        if not ok_save:
            rt.bot.RESTARTING = False
            await control_reply(rt, event, "✗ Restart iptal — cursor kalıcılaştırılamadı: " + save_note)
            return
        # 3) Dispatch yalnızca persist başarıyla bittiyse
        ok, message = await rt.bot.dispatch_next_run(rt.gh_pat)
        if ok:
            await control_reply(rt, event, "🔄 " + message + "\n" + save_note)
        else:
            rt.bot.RESTARTING = False
            await control_reply(rt, event, "⚠️ " + message + "\n" + save_note + "\nCursor yedeği alındı ama yeni çalışma başlatılamadı; mevcut süreç çalışmaya devam ediyor.")
    elif command == COMMAND_HELP:
        await control_reply(rt, event, HELP_TEXT)
    elif command == COMMAND_WEB_TRACK:
        await control_reply(rt, event, "Ürün takip menüsü için bildirim botunun özel sohbetinde /webtakip yaz.")
    elif command in CMD_ANALYZE:
        await analyze_history(rt, event, rest)
    elif command in CMD_TG_FILTER_OPEN:
        await handle_filter_command(rt, event, "open", rest)
    elif command in CMD_TG_FILTER_CLOSE:
        await handle_filter_command(rt, event, "close", rest)
    elif command in SETTINGS_COMMANDS:
        await handle_settings_command(rt, event, command, rest)
    else:
        await control_reply(rt, event, f"Bilinmeyen komut: {raw}\n\n{HELP_TEXT}")
