"""Bir kez yerelde çalıştırıp GitHub Secret olarak kullanacağın StringSession üretir."""
import asyncio
import os
from telethon import TelegramClient
from telethon.sessions import StringSession


async def main() -> None:
    api_id = int(os.environ["API_ID"])
    api_hash = os.environ["API_HASH"]
    phone = os.environ.get("PHONE") or input("Telefon (+90...): ").strip()
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.start(phone=phone)
    print("\nSESSION_STRING (bunu GitHub Secret'a koy, kimseyle paylaşma):\n")
    print(client.session.save())
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
