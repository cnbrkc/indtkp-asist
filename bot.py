"""Telegram indirim mesajı filtresi.

Kişisel Telegram hesabı (user client) ile çalışır; bot hesabı kaynak kanalları
okuyamaz. Yapılandırma ortam değişkenlerinden gelir, böylece sırlar repoya girmez.
"""

import asyncio
import logging
import os
from typing import Iterable

from telethon import TelegramClient, events
from telethon.sessions import StringSession

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("telegram-filter")


def csv_env(name: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


def chat_values(values: Iterable[str]) -> list[object]:
    """Chat ID'lerini int'e çevir, @kullaniciadi ve me gibi değerleri koru."""
    result: list[object] = []
    for value in values:
        try:
            result.append(int(value))
        except ValueError:
            result.append(value)
    return result


API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STRING = os.environ.get("SESSION_STRING", "").strip()
SOURCE_CHATS = chat_values(csv_env("SOURCE_CHATS"))
DESTINATION = os.getenv("DESTINATION", "me").strip()
INCLUDE_KEYWORDS = [x.casefold() for x in csv_env("INCLUDE_KEYWORDS")]
EXCLUDE_KEYWORDS = [x.casefold() for x in csv_env("EXCLUDE_KEYWORDS")]
MATCH_MODE = os.getenv("MATCH_MODE", "any").lower()
COPY_MODE = os.getenv("COPY_MODE", "forward").lower()

if not SOURCE_CHATS:
    raise RuntimeError("SOURCE_CHATS boş olamaz; izlenecek kanal ID'lerini girin.")
if MATCH_MODE not in {"any", "all"}:
    raise RuntimeError("MATCH_MODE yalnızca any veya all olabilir.")
if COPY_MODE not in {"forward", "copy"}:
    raise RuntimeError("COPY_MODE yalnızca forward veya copy olabilir.")

# StringSession kullanmak GitHub Actions gibi geçici makinelerde .session dosyası
# saklama ihtiyacını ortadan kaldırır.
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)


def matches(text: str) -> bool:
    normalized = text.casefold()
    if EXCLUDE_KEYWORDS and any(word in normalized for word in EXCLUDE_KEYWORDS):
        return False
    if not INCLUDE_KEYWORDS:
        return True
    found = [word in normalized for word in INCLUDE_KEYWORDS]
    return all(found) if MATCH_MODE == "all" else any(found)


@client.on(events.NewMessage(chats=SOURCE_CHATS))
async def on_new_message(event: events.NewMessage.Event) -> None:
    text = event.raw_text or ""
    if not matches(text):
        return

    source = await event.get_chat()
    source_name = getattr(source, "title", None) or getattr(source, "username", None) or str(event.chat_id)
    log.info("Eşleşti: %s / %s", source_name, event.id)

    # Mesajı doğrudan ileri göndermek, medya ve albümlerde en güvenilir yoldur.
    # copy seçeneği kaynak kanal etiketini kaldırır; Telegram bunu yeniden yükleme
    # yapmadan sunucu tarafında kopyalar.
    if COPY_MODE == "copy":
        await client.send_message(DESTINATION, event.message)
    else:
        await client.forward_messages(DESTINATION, event.message, from_peer=event.chat_id)


async def main() -> None:
    if SESSION_STRING:
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError("SESSION_STRING geçersiz veya oturumun süresi dolmuş.")
    else:
        # Bu yol yalnızca yerel ilk kurulum içindir; Actions'ta etkileşimli giriş yoktur.
        await client.start()

    me = await client.get_me()
    log.info("%s olarak çalışıyor; %d kaynak dinleniyor; hedef=%s", getattr(me, "username", me.id), len(SOURCE_CHATS), DESTINATION)
    await client.run_until_disconnected()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
