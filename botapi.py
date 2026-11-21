"""Bot API istemcisi: çok parçalı gövde, bildirim gönderme/düzenleme/silme (Telegram bot hesabı)."""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
import uuid
from collections.abc import Sequence
from typing import Any

import appstate
from constants import CAPTION_LIMIT, MESSAGE_LIMIT
from telegram_retry import gate_stats, request_with_retry

log = logging.getLogger("telegram-filter")


def encode_multipart(fields: dict[str, Any], files: Sequence[tuple[str, str, str, bytes]]) -> tuple[bytes, str]:
    """Basit multipart/form-data gövdesi kur (Bot API dosya yüklemeleri için).

    ``files`` üçlüleri: (alan adı, dosya adı, MIME türü, içerik).
    """
    boundary = "----IndirimTakipci" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for name, value in fields.items():
        if value is None or value == "":
            continue
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8")
        )
    for field, filename, mime, data in files:
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
            f"Content-Type: {mime}\r\n\r\n".encode("utf-8") + bytes(data) + b"\r\n"
        )
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class BotSendResult(tuple):
    """``(ok, detail)`` gibi açılan ama gönderilen mesajın ID'sini de taşıyan sonuç.

    Var olan ``ok, detail = await send_bot_ping(...)`` çağrıları ve testlerdeki
    düz 2'li fake'ler bozulmadan çalışmaya devam eder; yeni kod mesaj ID'sine
    ``getattr(sonuç, "message_id", None)`` ile erişir (fake'lerde ``None`` döner).
    """

    def __new__(cls, ok: bool, detail: str, message_id: int | None = None) -> BotSendResult:
        self = super().__new__(cls, (bool(ok), str(detail)))
        self.ok = bool(ok)
        self.detail = str(detail)
        self.message_id = message_id
        return self


def message_id_from_result(result: Any) -> int | None:
    """Bot API ``result`` sözlüğünden mesaj ID'sini al; yoksa ``None``."""
    message_id = result.get("message_id") if isinstance(result, dict) else None
    if isinstance(message_id, bool):
        return None
    return int(message_id) if isinstance(message_id, int) else None


async def _bot_api_request_full(
    token: str, method: str, *, payload: bytes, content_type: str, what: str,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Bot API çağrısı; başarılı sonucun ``result`` sözlüğünü de döndürür."""
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=payload,
        method="POST",
        headers={"Content-Type": content_type},
    )
    async def request_once() -> dict[str, Any]:
        try:
            with await asyncio.to_thread(urllib.request.urlopen, request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8", "replace") or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            try:
                body = json.loads(detail)
            except ValueError:
                body = {"description": f"HTTP {exc.code}"}
            body["error_code"] = exc.code
            body["ok"] = False
            return body

    try:
        body = await request_with_retry(token, method, request_once)
        if body.get("ok"):
            result = body.get("result")
            return True, f"{what} gönderildi", result if isinstance(result, dict) else None
        detail = str(body.get("description", "Bot API ok=false"))
        if method.startswith("editMessage") and "message is not modified" in detail.lower():
            return True, "değişiklik yok (zaten güncel)", None
        log.error("Bildirim bot'u hata verdi: %s %s", body.get("error_code", "?"), detail[:200])
        return False, f"HTTP {body.get('error_code', '?')}: {detail}", None
    except Exception as exc:  # noqa: BLE001 - belirsiz ağ hatasını tekrar yollama
        log.warning("Bildirim gönderilemedi: %s", type(exc).__name__)
        return False, f"{type(exc).__name__}", None


async def _bot_api_request(
    token: str, method: str, *, payload: bytes, content_type: str, what: str,
) -> tuple[bool, str]:
    """Bot API çağrısı yapar; yalnızca (başarılı_mı, ayrıntı) döner."""
    ok, detail, _ = await _bot_api_request_full(
        token, method, payload=payload, content_type=content_type, what=what,
    )
    return ok, detail


async def send_bot_ping(
    token: str,
    chat_id: int | str,
    text: str,
    *,
    entities: list[dict[str, Any]] | None = None,
    keyboard: dict[str, Any] | None = None,
    link_preview: bool | None = None,
) -> tuple[bool, str]:
    """Bot API üzerinden bildirim mesajı atar.

    Takipçi *kendi hesabınla* gönderdiği için Telegram o mesajları senin kendi
    mesajın sayar ve bildirim üretmez. Bildirim isteyenler @BotFather'dan bir bot
    oluşturup hedef gruba ekler; bu fonksiyon o bot adına mesajı atar.

    ``entities`` ve ``keyboard`` verilirse mesaj, kaynaktaki biçimlendirmeyi
    (gizli bağlantılar dâhil) ve buton linklerini korur.

    Dönen değer ``(ok, detail)`` gibi açılır; ``.message_id`` özniteliği
    gönderilen Bot API mesajının ID'sini verir (başarısızlıkta ``None``).
    """
    if not token:
        return BotSendResult(False, "NOTIFY_BOT_TOKEN secret/ortam değişkeni tanımlı değil.")
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text[:MESSAGE_LIMIT]}
    if entities:
        payload["entities"] = json.dumps(entities)
    if keyboard:
        payload["reply_markup"] = json.dumps(keyboard)
    if link_preview is not None:
        payload["link_preview_options"] = json.dumps({"is_disabled": not link_preview})
    ok, detail, result = await _bot_api_request_full(
        token, "sendMessage",
        payload=json.dumps(payload).encode(), content_type="application/json", what="bildirim",
    )
    return BotSendResult(ok, detail, message_id_from_result(result))


async def send_bot_media(
    token: str,
    chat_id: int | str,
    *,
    kind: str,
    filename: str,
    mime_type: str,
    data: bytes,
    caption: str | None = None,
    entities: list[dict[str, Any]] | None = None,
    keyboard: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Bildirim botuyla fotoğraf/video/dosya gönder (medya da bildirim üretsin).

    Dönen değer ``(ok, detail)`` gibi açılır; ``.message_id`` özniteliği
    gönderilen Bot API mesajının ID'sini verir (başarısızlıkta ``None``).
    """
    if not token:
        return BotSendResult(False, "NOTIFY_BOT_TOKEN secret/ortam değişkeni tanımlı değil.")
    method = {"photo": "sendPhoto", "video": "sendVideo"}.get(kind, "sendDocument")
    field = {"photo": "photo", "video": "video"}.get(kind, "document")
    fields: dict[str, Any] = {"chat_id": chat_id}
    if caption:
        fields["caption"] = caption[:CAPTION_LIMIT]
    if entities:
        fields["caption_entities"] = json.dumps(entities)
    if keyboard:
        fields["reply_markup"] = json.dumps(keyboard)
    body, content_type = encode_multipart(fields, [(field, filename, mime_type, data)])
    ok, detail, result = await _bot_api_request_full(
        token, method, payload=body, content_type=content_type, what=f"bildirim medyası ({kind})",
    )
    return BotSendResult(ok, detail, message_id_from_result(result))


async def delete_bot_message(token: str, chat_id: int | str, message_id: int) -> tuple[bool, str]:
    """Bot hesabıyla gönderilmiş eski bildirimi siler."""
    payload = {"chat_id": chat_id, "message_id": message_id}
    return await _bot_api_request(token, "deleteMessage", payload=json.dumps(payload).encode(),
                                  content_type="application/json", what="eski bildirim")


async def edit_bot_text(
    token: str,
    chat_id: int | str,
    message_id: int,
    text: str,
    *,
    entities: list[dict[str, Any]] | None = None,
    keyboard: dict[str, Any] | None = None,
    link_preview: bool | None = None,
) -> tuple[bool, str]:
    """Bildirim botunun gönderdiği bir metin mesajını yerinde günceller.

    Tekrar birleştirmede ilk mesaja çoklu paylaşım notu ("3 kere paylaşıldı: ...")
    işlerken kullanılır. Bot kendi mesajını düzenleyebilir; içerik değişmediyse
    Telegram "message is not modified" der, bu başarı sayılır.
    """
    if not token:
        return False, "NOTIFY_BOT_TOKEN secret/ortam değişkeni tanımlı değil."
    payload: dict[str, Any] = {
        "chat_id": chat_id, "message_id": message_id, "text": text[:MESSAGE_LIMIT],
    }
    if entities:
        payload["entities"] = json.dumps(entities)
    if keyboard:
        payload["reply_markup"] = json.dumps(keyboard)
    if link_preview is not None:
        payload["link_preview_options"] = json.dumps({"is_disabled": not link_preview})
    ok, detail = await _bot_api_request(
        token, "editMessageText",
        payload=json.dumps(payload).encode(), content_type="application/json", what="bildirim güncellemesi",
    )
    if not ok and "message is not modified" in detail:
        return True, "değişiklik yok (zaten güncel)"
    return ok, detail


async def edit_bot_caption(
    token: str,
    chat_id: int | str,
    message_id: int,
    caption: str,
    *,
    entities: list[dict[str, Any]] | None = None,
    keyboard: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Bildirim botunun gönderdiği bir medyanın açıklamasını yerinde günceller."""
    if not token:
        return False, "NOTIFY_BOT_TOKEN secret/ortam değişkeni tanımlı değil."
    payload: dict[str, Any] = {
        "chat_id": chat_id, "message_id": message_id, "caption": caption[:CAPTION_LIMIT],
    }
    if entities:
        payload["caption_entities"] = json.dumps(entities)
    if keyboard:
        payload["reply_markup"] = json.dumps(keyboard)
    ok, detail = await _bot_api_request(
        token, "editMessageCaption",
        payload=json.dumps(payload).encode(), content_type="application/json", what="bildirim güncellemesi",
    )
    if not ok and "message is not modified" in detail:
        return True, "değişiklik yok (zaten güncel)"
    return ok, detail


def _bot_api_stats_line() -> str:
    """P0 ölçümleme özeti: Bot API 429 sayaçları (token/içerik yazılmaz)."""
    if not appstate.NOTIFY_BOT_TOKEN:
        return "• Bot API hız sınırı: token yok (ölçüm kapalı)"
    stats = gate_stats(appstate.NOTIFY_BOT_TOKEN)
    line = (
        f"• Bot API hız sınırı: 429={stats['rate_limit_count']}"
        f" · retry bekleme {stats['retry_wait_seconds']:.1f} sn"
        f" · kilit bekleme {stats['lock_wait_seconds']:.1f} sn"
        f" · soğuma bekleme {stats['cooldown_wait_seconds']:.1f} sn"
    )
    by_method = stats.get("rate_limits_by_method") or {}
    if by_method:
        line += " · metot: " + ", ".join(
            f"{name}×{count}" for name, count in sorted(by_method.items())
        )
    return line
