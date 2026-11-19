"""/kaynaktest: kaynak kanalların erişim smoke testi."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from telethon import events

from botentities import bot_media_descriptor
from constants import CAPTION_LIMIT, MESSAGE_LIMIT
from controlbase import control_reply
from producttitle import _search_query
from runtime import Runtime
from sanitize import (
    _as_message,
    extract_links,
    message_entities,
    message_text,
    sanitize_message,
    utf16_length,
)
from textutil import humanize

log = logging.getLogger("telegram-filter")


async def test_source_access(rt: Runtime, event: events.NewMessage.Event, rest: str) -> None:
    """Geçmiş erişimini, ham mesaj bütünlüğünü ve yerel kullanım yolunu sınar.

    Her kaynakta son tek mesaj okunur; mesaj gönderilmez/değiştirilmez ve
    toplu test sırasında medya dosyaları indirilmez. Seçili kaynakta ham
    metin uzunluğu/hash'i, entity aralıkları, medya/link metadatası ve
    compose_message kuru çalıştırması ayrıca raporlanır.
    """
    requested = rest.strip().strip("'\"")
    all_sources = list(rt.bot.SOURCES)
    targets = all_sources
    unresolved: list[tuple[Any, Exception]] = list(rt.bot.SOURCE_FAILURES)
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
            failed = next((entry for entry in rt.bot.SOURCE_FAILURES
                           if needle in {str(entry[0]).strip().lstrip("@").casefold(),
                                         str(entry[0]).strip().casefold()}), None)
            if failed:
                targets = []
                unresolved = [failed]
            else:
                await control_reply(rt, 
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
    live_counts = rt.bot.STATS.get("source_seen", {})
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
            async for message in rt.client.iter_messages(item["id"], limit=1):
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
                age = f" · {humanize(rt.bot.time.time() - timestamp)} önce"
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
            dry_run = rt.bot.compose_message(
                latest,
                limit=CAPTION_LIMIT - 24 if media else MESSAGE_LIMIT - 100,
                link_kinds=rt.bot.LINK_KINDS,
                source_name=name if rt.bot.SOURCE_FOOTER else None,
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
            if rt.bot.SMART_HEADER:
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
    await control_reply(rt, event, "\n".join(lines))
