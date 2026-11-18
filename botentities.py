"""Bot API entity dönüşümleri ve medya tanımlayıcıları (saf)."""

from __future__ import annotations

import io
import mimetypes
from sanitize import _as_message, message_entities, utf16_length
from telethon.tl import types
from typing import Any


def entities_for_text(obj: Any, body: str) -> list[Any]:
    """Gövde kırpıldıysa sınırı aşan entity'leri at (Telegram hata verir)."""
    limit = utf16_length(body)
    kept: list[Any] = []
    for entity in message_entities(obj):
        if isinstance(entity, dict):
            offset = int(entity.get("offset", 0) or 0)
            length = int(entity.get("length", 0) or 0)
        else:
            offset = int(getattr(entity, "offset", 0) or 0)
            length = int(getattr(entity, "length", 0) or 0)
        if length <= 0 or offset < 0 or offset + length > limit:
            continue
        kept.append(entity)
    return kept


BOT_ENTITY_TYPES = {
    "MessageEntityBold": "bold",
    "MessageEntityItalic": "italic",
    "MessageEntityUnderline": "underline",
    "MessageEntityStrike": "strikethrough",
    "MessageEntitySpoiler": "spoiler",
    "MessageEntityCode": "code",
    "MessageEntityPre": "pre",
    "MessageEntityBlockquote": "blockquote",
    "MessageEntityTextUrl": "text_link",
    "MessageEntityUrl": "url",
    "MessageEntityEmail": "email",
    "MessageEntityPhone": "phone_number",
    "MessageEntityMention": "mention",
    "MessageEntityHashtag": "hashtag",
    "MessageEntityCashtag": "cashtag",
    "MessageEntityBotCommand": "bot_command",
    "MessageEntityBankCard": "bank_card",
}


def bot_api_entity(entity: Any, text_length: int | None = None) -> dict[str, Any] | None:
    """Telethon entity'sini Bot API biçimine çevir.

    Offsets are already UTF-16 in both worlds, so they can be passed through.
    Desteklenmeyen türler (custom emoji, text_mention...) sessizce atlanır.
    """
    kind = BOT_ENTITY_TYPES.get(type(entity).__name__)
    if not kind:
        return None
    if kind == "blockquote" and getattr(entity, "collapsed", False):
        kind = "expandable_blockquote"
    offset = int(getattr(entity, "offset", 0) or 0)
    length = int(getattr(entity, "length", 0) or 0)
    if text_length is not None and offset + length > text_length:
        length = text_length - offset
    if length <= 0 or offset < 0:
        return None
    data: dict[str, Any] = {"type": kind, "offset": offset, "length": length}
    if kind == "text_link":
        url = getattr(entity, "url", None)
        if not url:
            return None
        data["url"] = str(url)
    if kind == "pre":
        language = getattr(entity, "language", None)
        if language:
            data["language"] = str(language)
    return data


def bot_api_entities(obj: Any, body: str) -> list[dict[str, Any]]:
    """Gövdeye sığan entity'leri Bot API sözlüklerine çevir."""
    text_length = utf16_length(body)
    result: list[dict[str, Any]] = []
    for entity in entities_for_text(obj, body):
        data = bot_api_entity(entity, text_length)
        if data:
            result.append(data)
    return result


def composed_has_content(composed: dict[str, Any]) -> bool:
    """İletilecek gerçek içerik var mı?

    ``content`` kaynak adını (ya da ad yazılamadıysa "Mesajı Gör" satırını) da
    taşır; yalnızca onların bulunması metin sayılmaz. Kaynakta metin yoksa iletim zincirinin son çaresi
    (t.me bağlantı kartı) devreye girebilsin diye bu ayrım gerekir.
    """
    return any(
        str(composed.get(key) or "").strip()
        for key in ("header", "title", "body", "appendix")
    )


def source_name_entity(composed: dict[str, Any]) -> list[dict[str, Any]]:
    """En alttaki kaynak grup adını KALIN yap ve adresini adın içine göm.

    Kullanıcı isteği: "Fırsatı Gönderen" gibi bir etiket yazılmasın; ad kalın
    kalsın, ama orijinal mesajın adresi artık ayrı ``🔗 Mesajı Gör`` satırı
    olarak değil, **bu adın içinde** dursun (dokununca açılır, URL görünmez).
    """
    offset = composed.get("source_name_offset", -1)
    length = composed.get("source_name_length", 0)
    if offset is None or length is None or offset < 0 or length <= 0:
        return []
    entities: list[dict[str, Any]] = [
        {"type": "bold", "offset": int(offset), "length": int(length)},
    ]
    url = composed.get("source_name_url")
    if url:
        entities.append({"type": "text_link", "offset": int(offset),
                         "length": int(length), "url": str(url)})
    return entities


def media_upload_name(obj: Any) -> str:
    """Yeniden yüklemede kullanılacak dosya adı.

    Telethon, adı olmayan ``bytes``/``BytesIO`` nesnelerini ``"unnamed"`` dosyası
    olarak gönderir (utils.get_attributes). Bu yüzden uzantılı bir ad şart;
    aksi halde fotoğraf, adı "unnamed" olan bir belgeye dönüşür.
    """
    message = _as_message(obj)
    file = getattr(message, "file", None)
    name = getattr(file, "name", None)
    if name:
        return str(name)
    ext = getattr(file, "ext", None)
    mime = getattr(file, "mime_type", None)
    if not ext and mime:
        ext = mimetypes.guess_extension(str(mime))
    if ext in (".jpe", ".jpeg"):
        ext = ".jpg"
    return f"firsat_{getattr(message, 'id', 'medya')}{ext or '.jpg'}"


def media_buffer(data: bytes, name: str) -> io.BytesIO:
    """İndirilen medyayı, türünü koruyan isimli bir akışa çevir."""
    buffer = io.BytesIO(data)
    buffer.name = name  # Telethon uzantıyı buradan okur (fotoğraf/video/dosya)
    return buffer


def reupload_attributes(obj: Any) -> list[Any] | None:
    """Yeniden yüklemede videonun en-boy oranını koru.

    Telethon, dosya adından video algılayıp metadata bulamazsa 1:1 oranlı
    ``DocumentAttributeVideo`` üretir; video kare görünür. Orijinal attribute'u
    vererek süre/ölçü bilgisini koruyoruz.
    """
    media = getattr(_as_message(obj), "media", None)
    document = getattr(media, "document", None)
    if not isinstance(document, types.Document):
        return None
    for attribute in getattr(document, "attributes", None) or []:
        if type(attribute).__name__ == "DocumentAttributeVideo":
            return [types.DocumentAttributeVideo(
                duration=float(getattr(attribute, "duration", 0) or 0),
                w=int(getattr(attribute, "w", 1) or 1),
                h=int(getattr(attribute, "h", 1) or 1),
                round_message=bool(getattr(attribute, "round_message", False)),
                supports_streaming=True,
            )]
    return None


def bot_media_descriptor(obj: Any) -> dict[str, Any] | None:
    """Bildirim botuyla gönderilebilecek medyanın türünü/ boyutunu belirle."""
    message = _as_message(obj)
    media = getattr(message, "media", None)
    info: dict[str, Any] = {"kind": "document", "filename": "", "mime": "application/octet-stream"}
    if isinstance(media, types.MessageMediaPhoto):
        info.update(kind="photo", filename=f"firsat_{getattr(message, 'id', 'foto')}.jpg", mime="image/jpeg")
    elif isinstance(media, types.MessageMediaDocument):
        document = getattr(media, "document", None)
        if not isinstance(document, types.Document):
            return None
        attributes = list(getattr(document, "attributes", None) or [])
        names = {type(a).__name__ for a in attributes}
        filename = next(
            (getattr(a, "file_name", None) for a in attributes
             if type(a).__name__ == "DocumentAttributeFilename" and getattr(a, "file_name", None)),
            None,
        )
        mime = str(getattr(document, "mime_type", "") or "")
        if mime.startswith("video/") or "DocumentAttributeVideo" in names:
            kind = "video"
            filename = filename or f"firsat_{getattr(message, 'id', 'video')}.mp4"
        elif mime in {"image/jpeg", "image/jpg", "image/png"} or mime.startswith("image/jp"):
            kind = "photo"
            filename = filename or f"firsat_{getattr(message, 'id', 'foto')}.jpg"
        else:
            kind = "document"
            filename = filename or f"firsat_{getattr(message, 'id', 'dosya')}.bin"
        info.update(kind=kind, filename=filename, mime=mime or mimetypes.guess_type(filename)[0]
                    or "application/octet-stream")
    else:
        return None
    info["size"] = int(getattr(getattr(message, "file", None), "size", 0) or 0)
    return info
