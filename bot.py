"""Telegram indirim mesajı filtresi.

Kişisel Telegram hesabı (user client) ile çalışır; bot hesabı kaynak kanalları
okuyamaz. Yapılandırma ortam değişkenlerinden gelir, böylece sırlar repoya girmez.
"""

import asyncio
import json
import logging
import os
import urllib.request
from pathlib import Path
from typing import Iterable

from telethon import TelegramClient, events
from telethon.sessions import StringSession

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("telegram-filter")


def load_config() -> dict:
    """Public config dosyasını oku; ortam değişkenleri geriye dönük desteklenir."""
    path = Path(os.getenv("CONFIG_FILE", "config.json"))
    with path.open(encoding="utf-8") as file:
        config = json.load(file)

    # Eski Variables kurulumu kullananların geçişini kolaylaştır.
    for key in ("source_chats", "include_keywords", "exclude_keywords"):
        env_name = key.upper()
        if os.getenv(env_name):
            config[key] = [x.strip() for x in os.environ[env_name].split(",") if x.strip()]
    for key in ("destination", "match_mode", "copy_mode"):
        if os.getenv(key.upper()):
            config[key] = os.environ[key.upper()]
    return config


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
CONFIG = load_config()
SOURCE_CHATS = chat_values(CONFIG.get("source_chats", []))
DESTINATION = str(CONFIG.get("destination", "me")).strip()
INCLUDE_KEYWORDS = [str(x).casefold() for x in CONFIG.get("include_keywords", [])]
EXCLUDE_KEYWORDS = [str(x).casefold() for x in CONFIG.get("exclude_keywords", [])]
MATCH_MODE = str(CONFIG.get("match_mode", "any")).lower()
COPY_MODE = str(CONFIG.get("copy_mode", "forward")).lower()
CONTROL_CHAT = CONFIG.get("control_chat", "me")
AUTO_RESTART = bool(CONFIG.get("auto_restart", True))
RESTART_AFTER_MINUTES = int(os.getenv("RESTART_AFTER_MINUTES", "330"))
GH_PAT = os.getenv("GH_PAT", "").strip()

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


def is_allowed_control(event: events.NewMessage.Event) -> bool:
    """Kontrol komutlarını yalnızca Saved Messages'tan veya admin ID'den kabul et."""
    admin_id = CONFIG.get("admin_user_id")
    return CONTROL_CHAT == "me" or (admin_id is not None and event.sender_id == int(admin_id))


async def dispatch_next_run() -> bool:
    """Yeni Actions job'u başlatır. GH_PAT yoksa sessizce manuel moda düşer."""
    if not GH_PAT:
        return False
    repository = os.getenv("GITHUB_REPOSITORY", "")
    workflow = os.getenv("GITHUB_WORKFLOW_FILE", "telegram-monitor.yml")
    ref = os.getenv("GITHUB_REF_NAME", "main")
    if not repository:
        return False
    url = f"https://api.github.com/repos/{repository}/actions/workflows/{workflow}/dispatches"
    payload = (f'{{"ref":"{ref}"}}').encode()
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {GH_PAT}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "telegram-indirim-takipci",
        },
    )
    try:
        await asyncio.to_thread(urllib.request.urlopen, request, timeout=20)
        log.info("Sonraki GitHub Actions çalışması başlatıldı.")
        return True
    except Exception:
        log.exception("Sonraki Actions çalışması başlatılamadı.")
        return False


@client.on(events.NewMessage(chats=CONTROL_CHAT))
async def on_control_message(event: events.NewMessage.Event) -> None:
    if not is_allowed_control(event):
        return
    command = (event.raw_text or "").strip().lower()
    if command == "/status":
        await event.reply("✅ Takipçi aktif. Mesajlar Telegram bağlantısı üzerinden anlık dinleniyor.")
    elif command == "/restart":
        if await dispatch_next_run():
            await event.reply("🔄 Yeni GitHub Actions çalışması başlatıldı; bu çalışma birazdan kapanabilir.")
        else:
            await event.reply("⚠️ Otomatik yeniden başlatma ayarlı değil. Actions sayfasından Run workflow kullan.")


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


async def auto_restart_scheduler() -> None:
    """Actions job bitmeden bir sonraki job'u zincirleme başlatır."""
    if not AUTO_RESTART or not GH_PAT:
        return
    delay = max(60, RESTART_AFTER_MINUTES * 60)
    await asyncio.sleep(delay)
    await client.send_message(DESTINATION, "⏳ Takipçi oturumu yenileniyor; mesaj dinleme kısa süre içinde devam edecek.")
    await dispatch_next_run()


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
    if AUTO_RESTART and GH_PAT:
        asyncio.create_task(auto_restart_scheduler())
    await client.run_until_disconnected()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
