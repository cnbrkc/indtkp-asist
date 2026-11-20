"""/komut sohbeti temelleri: olay doğrulama, yanıt gönderme ve komut temizliği."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from telethon import events

import appstate
from analysis import chunk_text
from confutil import config_flag
from private_bot import PrivateControlEvent
from runtime import Runtime
from sanitize import sent_message_ids

log = logging.getLogger("telegram-filter")


def is_control_event(rt: Runtime, event: events.NewMessage.Event) -> bool:
    """Komut yalnızca kontrol sohbetinden gelirse işlenir."""
    if isinstance(event, PrivateControlEvent):
        return config_flag(rt.store.config.get("private_control"), False)
    if config_flag(rt.store.config.get("private_control"), False):
        return event.chat_id == appstate.SELF_ID  # emergency fallback, not the offer group
    return event.chat_id in appstate.CONTROL_IDS


def is_admin_event(rt: Runtime, event: events.NewMessage.Event) -> bool:
    """Kayıtlı Mesajlar'a yazan hesabın sahibi; gruptaysa admin listesi geçerli."""
    if isinstance(event, PrivateControlEvent):
        return event.sender_id in (appstate.ADMIN_IDS | {appstate.SELF_ID}) and event.chat_id == event.sender_id
    if event.chat_id == appstate.SELF_ID:
        return True
    return event.sender_id in appstate.ADMIN_IDS


async def delete_command_messages(rt: Runtime, chat_id: int, message_ids: Sequence[int]) -> None:
    """Komut sohbetindeki eski komut/yanıt mesajlarını sil.

    Yalnızca ``COMMAND_MESSAGES`` kaydından gelen ID'ler silinir; indirim
    bildirimleri bu kayda girmediği için silinmeleri mümkün değildir.
    Silme başarısız olsa bile (ör. yetki yok) komut akışı devam eder.
    """
    ids = [int(item) for item in message_ids if item]
    if not ids:
        return
    try:
        await rt.client.delete_messages(int(chat_id or 0), ids, revoke=True)
    except Exception as exc:  # noqa: BLE001 - silinemezse komut yine çalışsın
        log.warning("Eski komut mesajları silinemedi (chat=%s, adet=%d): %s: %s",
                    chat_id, len(ids), type(exc).__name__, exc)
        return
    appstate.STATS["cleaned_commands"] += len(ids)
    log.debug("Komut sohbeti temizlendi: chat=%s, %d mesaj.", chat_id, len(ids))


async def control_reply(rt: Runtime, event: events.NewMessage.Event, text: str, *,
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
    if isinstance(event, PrivateControlEvent) or not appstate.CLEAN_COMMANDS:
        return sent_ids
    chat_id = int(getattr(event, "chat_id", 0) or 0)
    current = [int(getattr(event, "id", 0) or 0), *sent_ids]
    outdated = rt.bot.note_command_messages(chat_id, current)
    outdated += [int(item) for item in stale if item]
    await delete_command_messages(rt, chat_id, outdated)
    return sent_ids
