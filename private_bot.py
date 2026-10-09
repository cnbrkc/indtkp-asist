"""Private command transport and exact offer copies; no channel monitoring here."""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
import unicodedata
import urllib.error
import urllib.request
from types import SimpleNamespace

log = logging.getLogger("telegram-filter.private")
PRIVATE_SUFFIX = "\n\n🎯 SANA ÖZEL"


class BotAPIError(Exception):
    pass


class BotAPI:
    def __init__(self, token):
        self.token = token

    def _request(self, method, payload):
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self.token}/{method}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            # Never log the request URL (it contains the bot token).
            try:
                return json.loads(exc.read())
            except (ValueError, OSError):
                raise BotAPIError(f"Telegram HTTP {exc.code}") from None
        except Exception as exc:
            raise BotAPIError(f"Telegram bağlantı hatası: {type(exc).__name__}") from None

    async def call(self, method, payload):
        for attempt in range(3):
            body = await asyncio.to_thread(self._request, method, payload)
            if body.get("ok"):
                return body.get("result")
            code = body.get("error_code", "?")
            if code == 429 and attempt < 2:
                delay = body.get("parameters", {}).get("retry_after", 1)
                if isinstance(delay, (int, float)) and 0 <= delay <= 60:
                    await asyncio.sleep(delay)
                    continue
            # Do not echo arbitrary remote text or credentials to logs/users.
            raise BotAPIError(f"Telegram API {method}: hata {code}")


class PrivateReply:
    def __init__(self, api, chat_id, message_id):
        self.api, self.chat_id, self.id = api, chat_id, message_id

    async def edit(self, text):
        await self.api.call("editMessageText", {
            "chat_id": self.chat_id, "message_id": self.id, "text": text,
        })


class PrivateControlEvent:
    """Minimal adapter for the existing command handlers (plain-text replies)."""
    def __init__(self, api, message):
        self.api = api
        self.chat_id = message["chat"]["id"]
        self.sender_id = message["from"]["id"]
        self.id = message["message_id"]
        self.raw_text = message.get("text", "")

    async def reply(self, text):
        result = await self.api.call("sendMessage", {"chat_id": self.chat_id, "text": text})
        return PrivateReply(self.api, self.chat_id, result["message_id"])

    async def get_chat(self):
        return SimpleNamespace(id=self.chat_id, kind="private")


def authorized_private_message(message, allowed_ids):
    return (
        isinstance(message, dict)
        and message.get("chat", {}).get("type") == "private"
        and message.get("from", {}).get("id") in allowed_ids
        and message.get("chat", {}).get("id") == message.get("from", {}).get("id")
        and not message.get("from", {}).get("is_bot", False)
        and isinstance(message.get("text"), str)
    )


async def poll_private_commands(api, allowed_ids, handler):
    """One consumer per token. Never delete an existing webhook implicitly.

    Updates queued before startup are intentionally skipped, so an old /restart
    or half-finished settings dialogue cannot replay after a runner replacement.
    """
    offset = None
    initialized = False
    while True:
        try:
            if not initialized:
                webhook = await api.call("getWebhookInfo", {})
                if webhook.get("url"):
                    log.error("Özel komutlar başlatılamadı: bu botta webhook var. "
                              "Mevcut entegrasyonu kontrol et; webhook silinmedi.")
                    return
                old = await api.call("getUpdates", {
                    "offset": -1, "limit": 1, "timeout": 0,
                    "allowed_updates": ["message"],
                })
                if old:
                    offset = old[-1]["update_id"] + 1
                initialized = True
                log.info("Bot özel komutları hazır; özel sohbetten /start gönder.")
            payload = {"timeout": 20, "allowed_updates": ["message"]}
            if offset is not None:
                payload["offset"] = offset
            updates = await api.call("getUpdates", payload)
            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message")
                if not authorized_private_message(message, allowed_ids()):
                    continue
                try:
                    await handler(PrivateControlEvent(api, message))
                except Exception as exc:
                    log.error("Özel komut işlenemedi: %s", type(exc).__name__)
                    try:
                        await api.call("sendMessage", {
                            "chat_id": message["chat"]["id"],
                            "text": "⚠️ Komut tamamlanamadı. /durum ile kontrol edip yeniden dene.",
                        })
                    except BotAPIError:
                        pass
        except BotAPIError as exc:
            log.warning("Özel komut bağlantısı: %s; yeniden denenecek.", exc)
            await asyncio.sleep(5)
        except Exception as exc:
            log.error("Özel komut bağlantısı: %s; yeniden denenecek.", type(exc).__name__)
            await asyncio.sleep(5)


def normalize_keyword(text):
    return unicodedata.normalize("NFKC", text).casefold().replace("i\u0307", "i")


def parse_dm_keywords(text):
    words = []
    for part in re.split(r"[,\n]", text):
        word = normalize_keyword(part.strip())
        if word and word not in words:
            if len(word) > 100:
                raise ValueError("Her kelime/ifade en fazla 100 karakter olabilir.")
            words.append(word)
    if not words:
        raise ValueError("En az bir kelime yaz; kapatmak için /dmkapat kullan.")
    if len(words) > 100:
        raise ValueError("En fazla 100 kelime/ifade kaydedebilirsin.")
    return words


def dm_matches(text, keywords):
    normalized = normalize_keyword(text)
    return any(re.search(r"(?<!\w)" + re.escape(normalize_keyword(word)) + r"(?!\w)", normalized)
               for word in keywords if isinstance(word, str) and word.strip())


def private_copy_request(chat_id, from_chat_id, info):
    """Preserve text, UTF-16 entities, caption and URL keyboard; append only a suffix.

    Media is copied server-side from the delivered group message, never downloaded
    again. Oversized fallback content is refused rather than silently truncated.
    """
    text = info.get("text", "") + PRIVATE_SUFFIX
    media = info.get("kind") == "media"
    if len(text.encode("utf-16-le")) // 2 > (1024 if media else 4096):
        raise BotAPIError("Özel kopya Telegram uzunluk sınırını aşıyor; içerik kesilmedi.")
    payload = {"chat_id": chat_id}
    if media:
        if not info.get("message_id"):
            raise BotAPIError("Grup mesajının kimliği yok; medya kopyalanamadı.")
        method = "copyMessage"
        payload.update(from_chat_id=from_chat_id, message_id=info["message_id"], caption=text,
                       caption_entities=copy.deepcopy(info.get("entities") or []))
    else:
        method = "sendMessage"
        payload.update(text=text, entities=copy.deepcopy(info.get("entities") or []))
    if info.get("keyboard"):
        payload["reply_markup"] = copy.deepcopy(info["keyboard"])
    return method, payload


class PrivateOfferQueue:
    """A bounded, best-effort mirror queue: a DM failure never rolls back the group."""
    def __init__(self, api, prepare, maxsize=100):
        self.api, self.prepare = api, prepare
        self.queue = asyncio.Queue(maxsize=maxsize)
        self.sent = 0
        self.failed = 0
        self.dropped = 0

    def submit(self, item):
        try:
            self.queue.put_nowait(copy.deepcopy(item))
            return True
        except asyncio.QueueFull:
            self.dropped += 1
            log.error("Özel fırsat kuyruğu dolu; grup iletildi, özel kopya atlandı.")
            return False

    async def run(self):
        while True:
            item = await self.queue.get()
            try:
                request = await self.prepare(item)
                if request is not None:
                    method, payload = request
                    await self.api.call(method, payload)
                    self.sent += 1
            except Exception as exc:
                self.failed += 1
                log.warning("Özel fırsat gönderilemedi; grup korunuyor: %s", type(exc).__name__)
            finally:
                self.queue.task_done()
            await asyncio.sleep(1)  # private-chat flood protection; no repeat alerts
