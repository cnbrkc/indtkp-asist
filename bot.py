"""Telegram indirim mesajı filtresi.

Kişisel Telegram hesabı (user client) ile çalışır; bot hesabı kaynak kanalları
okuyamaz. Yapılandırma config.json dosyasından gelir, sırlar ortam
değişkenlerinden gelir.

Önemli tasarım notu: Telethon'daki `events.NewMessage(chats=...)` filtresi chat
listesini *ilk mesaj geldiğinde* çözer ve tek bir chat bile çözülemezse tüm
handler devre dışı kalır (üstelik hata yalnızca "Task exception was never
retrieved" olarak loglanır). Bu yüzden burada bütün chat'ler açılışta tek tek
çözülür, çözülemeyenler atlanır ve handler'lara hazır int ID listesi verilir.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from telethon import TelegramClient, errors, events, utils
from telethon.sessions import StringSession
from telethon.tl import functions, types

import appstate
import price_filter
import dm_filters
import timefmt
from telegram_retry import gate_stats, request_with_retry
from private_bot import BotAPI, BotAPIError, PrivateOfferQueue, poll_health, poll_private_commands

from confutil import (
    chat_values,
    config_flag,
    config_path,
    link_appendix_mode,
    link_kinds_for,
    load_config,
    parse_admin_ids,
    parse_chat_value,
)
from keywords import canonical_match_mode, filter_state_of, match_mode_of, normalize
from sanitize import (
    LINK_APPENDIX_LIMIT,
    _as_message,
    button_link,
    extract_links,
    link_appendix_lines,
    message_text,
    sanitize_message,
    utf16_length,
)

from constants import (
    APPENDIX_PRODUCT,
    APPENDIX_REQUIRED,
    APPENDIX_SPARE,
    CAPTION_LIMIT,
    MAX_INLINE_KEYBOARD_BUTTONS,
    MESSAGE_LIMIT,
    MESSAGE_LINK_LABEL,
    PRODUCT_LINK_TEXT,
)
from textutil import (
    display_name,
    humanize,
)
from analysis import content_tokens, title_tokens
from producttitle import _is_coupon_announcement, _search_query, is_product_link
from searchbuttons import build_inline_keyboard
from header import PRICE_MISSING_MARK, build_header_line, header_price_text
from smarttitle import _webpage_hints, smart_header_info
from quotes import (
    strip_quoted_detected_lines,
)
from composeparts import (
    _compose_assemble_text,
    _compose_build_entities,
    _compose_drop_overflow_parts,
    _compose_fit_appendix,
    _compose_truncate_body,
    _compose_truncate_header,
)
from botentities import bot_api_entities, entities_for_text, source_name_entity
from commandtext import (
    ConfigStore,
    SAVE_STATUS_TEXT,
    stage_telegram_list_change,
    telegram_list_items,
)
import controlcmds
import listener
from runtime import Runtime
from dedupflow import dedup_preload
from delivery import send_copy, send_forward, send_link_card, send_media, send_text_only
from dmcontrol import prepare_private_offer
from lifecycle import prepare_scheduled_restart, private_status_changed, resume_after_scheduled_restart_failure
from listener import fast_poll_loop
import functools
import sys

# Runner UTC çalışır; log saatlerini TSİ'ye sabitle ki kullanıcı saatle
# karşılaştırabilsin (Actions log'unda "10:44" görünen satırlar TSİ 13:44'tü).
# Sınıf özniteliği olarak atandığı için staticmethod sarmalayıcı gerekir;
# aksi hâlde Python bunu bağlı metot sanıp iki argüman geçirir.
logging.Formatter.converter = staticmethod(
    lambda seconds: timefmt.local(seconds).timetuple()
)
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("telegram-filter")

STARTED_AT = time.time()


# ---------------------------------------------------------------------------
# Ortam / yapılandırma doğrulama
# ---------------------------------------------------------------------------

def _check_env_secrets(problems: list[str]) -> None:
    api_id = os.getenv("API_ID", "").strip()
    api_hash = os.getenv("API_HASH", "").strip()
    session_string = os.getenv("SESSION_STRING", "").strip()
    if not api_id:
        problems.append("API_ID secret'ı boş. Settings → Secrets and variables → Actions → Secrets sekmesine ekle.")
    else:
        try:
            int(api_id)
        except ValueError:
            problems.append(f"API_ID sayı olmalı, gelen değer okunamadı (uzunluk {len(api_id)}).")
    if not api_hash:
        problems.append("API_HASH secret'ı boş. my.telegram.org → API development tools'dan alıp secret olarak ekle.")
    elif len(api_hash) != 32:
        problems.append(f"API_HASH 32 karakter olmalı, gelen değer {len(api_hash)} karakter.")
    if not session_string:
        problems.append("SESSION_STRING secret'ı boş. generate_session.py ile üretip secret olarak ekle.")
    if not os.getenv("GH_PAT", "").strip():
        log.warning("GH_PAT boş: otomatik yenileme zinciri çalışmaz, workflow'u elle başlatman gerekir.")


def _check_config_flags_and_dm(config: dict, problems: list[str]) -> None:
    if not isinstance(config.get("private_control", False), bool):
        problems.append("config.json → private_control true/false olmalı.")
    if not isinstance(config.get("dm_enabled", False), bool):
        problems.append("config.json → dm_enabled true/false olmalı.")
    for field in ("dm_keywords", "dm_exclude_keywords"):
        dm_words = config.get(field, [])
        if (not isinstance(dm_words, list) or len(dm_words) > 100
                or any(not isinstance(w, str) or not w.strip() or len(w) > 100 for w in dm_words)):
            problems.append(f"config.json → {field} en fazla 100 dolu metinden oluşmalı (her biri ≤100 karakter).")
    price_items = config.get("dm_price_max", [])
    if not isinstance(price_items, list) or len(price_items) > 1 or any(
        price_filter.parse_threshold(item) is None for item in price_items
    ):
        problems.append("config.json → dm_price_max en fazla 1 sayı içermeli (örn. [\"10000\"]).")
    product_price_items = config.get(dm_filters.DM_PRODUCT_PRICE_FIELD, [])
    invalid_product_price_items = not isinstance(product_price_items, list) or len(product_price_items) > 100
    seen_product_price_rules: set[tuple[str, float]] = set()
    if isinstance(product_price_items, list):
        for item in product_price_items:
            try:
                rule = dm_filters.parse_product_price_rule(item)
            except (TypeError, ValueError):
                invalid_product_price_items = True
                break
            if rule.identity in seen_product_price_rules:
                invalid_product_price_items = True
                break
            seen_product_price_rules.add(rule.identity)
    if invalid_product_price_items:
        problems.append(
            "config.json → dm_product_price_rules en fazla 100 geçerli, yinelenmeyen "
            "'ürün adı < fiyat' kuralı içermeli."
        )
    for flag in ("smart_header", "fast_notify", "fast_poll"):
        if not isinstance(config.get(flag, False), bool):
            problems.append(f"config.json → {flag} true/false olmalı.")
    if config.get("private_control") and not os.getenv("NOTIFY_BOT_TOKEN", "").strip():
        log.warning("Özel komutlar için NOTIFY_BOT_TOKEN gerekli; Kayıtlı Mesajlar yedek kontrol olarak açık.")


def _check_sources_and_modes(config: dict, problems: list[str]) -> None:
    sources = chat_values(config.get("source_chats"))
    if not sources:
        problems.append("config.json → source_chats boş. En az bir kanal/grup eklenmeli.")
    if canonical_match_mode(config.get("match_mode", "any")) is None:
        problems.append(
            "config.json → match_mode yalnızca 'any', 'all' veya 'forward_all' olabilir "
            "(forward_all, dahili kelimeleri yok sayar; hariç kelimeler yine engeller)."
        )
    if str(config.get("copy_mode", "forward")).lower() not in {"forward", "copy"}:
        problems.append("config.json → copy_mode yalnızca 'forward' veya 'copy' olabilir.")
    explicit_modes = config.get("delivery_modes")
    if explicit_modes is not None:
        if not isinstance(explicit_modes, (list, tuple)) or not explicit_modes:
            problems.append("config.json → delivery_modes boş olmayan bir liste olmalı.")
        else:
            unknown = [str(m) for m in explicit_modes if str(m).strip().lower() not in DELIVERY_MODES]
            if unknown:
                problems.append(
                    "config.json → delivery_modes içinde bilinmeyen değer var: "
                    f"{unknown} (geçerli: {', '.join(DELIVERY_MODES)})"
                )


def _check_limits_and_destination(config: dict, problems: list[str]) -> bool:
    try:
        if int(config.get("max_media_mb", 25)) < 0:
            problems.append("config.json → max_media_mb negatif olamaz.")
    except (TypeError, ValueError):
        problems.append("config.json → max_media_mb bir sayı olmalı.")
    try:
        window = int(config.get("dedup_window_hours", 12))
        if not 1 <= window <= 72:
            problems.append("config.json → dedup_window_hours 1–72 saat arasında olmalı.")
    except (TypeError, ValueError):
        problems.append("config.json → dedup_window_hours bir sayı olmalı.")
    try:
        scan = int(config.get("dedup_scan_limit", 100))
        if not 0 <= scan <= 100:
            problems.append("config.json → dedup_scan_limit 0–100 arasında olmalı.")
    except (TypeError, ValueError):
        problems.append("config.json → dedup_scan_limit bir sayı olmalı.")
    try:
        parse_chat_value(config.get("destination", "me"))
    except ValueError as exc:
        problems.append(f"config.json → destination geçersiz: {exc}")
    control = config.get("control_chat", "me")
    control_values = list(control) if isinstance(control, (list, tuple)) else [control]
    group_control = False
    for value in control_values:
        try:
            if parse_chat_value(value) != "me":
                group_control = True
        except ValueError as exc:
            problems.append(f"config.json → control_chat geçersiz: {exc}")
    return group_control


def check_environment(config: dict | None = None) -> list[str]:
    """Eksik/hatalı ayarları toplar. Boş liste = her şey hazır."""
    problems: list[str] = []
    _check_env_secrets(problems)
    if config is None:
        return problems
    _check_config_flags_and_dm(config, problems)
    _check_sources_and_modes(config, problems)
    group_control = _check_limits_and_destination(config, problems)
    if group_control and not parse_admin_ids(config.get("admin_user_id")):
        problems.append(
            "config.json → control_chat bir grup ama admin_user_id boş. "
            "Grup komutları kimse tarafından kullanılamaz; kendi kullanıcı ID'ni yaz."
        )
    return problems


def print_report(config: dict, problems: list[str]) -> None:
    """Actions log'unda okunabilir bir açılış raporu bırak (sır değeri basmaz)."""
    def mask(name: str) -> str:
        return "var" if os.getenv(name, "").strip() else "YOK"

    print("=" * 62, flush=True)
    print("Telegram indirim takipçisi - yapılandırma raporu", flush=True)
    print("=" * 62, flush=True)
    print(f"Ortam değişkenleri : API_ID={mask('API_ID')} API_HASH={mask('API_HASH')} "
          f"SESSION_STRING={mask('SESSION_STRING')} GH_PAT={mask('GH_PAT')} "
          f"NOTIFY_BOT_TOKEN={mask('NOTIFY_BOT_TOKEN')}", flush=True)
    print(f"Kaynaklar          : {len(config.get('source_chats') or [])} adet", flush=True)
    print(f"Hedef              : {config.get('destination', 'me')}", flush=True)
    control_label = ("Bot özel sohbeti + Kayıtlı Mesajlar"
                     if config_flag(config.get("private_control"), False)
                     else config.get("control_chat", "me"))
    print(f"Kontrol sohbeti    : {control_label}", flush=True)
    print(f"Admin ID'leri      : {sorted(parse_admin_ids(config.get('admin_user_id'))) or 'tanımsız'}", flush=True)
    filter_mode = match_mode_of(config)
    filter_label = {
        "any": "any (biri yeterli)",
        "all": "all (hepsi zorunlu)",
        "forward_all": "forward_all (dahili kelimeler yok sayılır)",
    }[filter_mode]
    print(f"Anahtar kelimeler  : {config.get('include_keywords') or '(hepsi)'} ({filter_label})", flush=True)
    print(f"Hariç kelimeler    : {config.get('exclude_keywords') or '(yok)'}", flush=True)
    filter_state = filter_state_of(config)
    print(f"Filtre durumu      : dahili {'AÇIK' if filter_state['include'] else 'KAPALI'} | "
          f"harici {'AÇIK' if filter_state['exclude'] else 'KAPALI'} "
          f"(/tgfiltreac ve /tgfiltrekapat ile değiştirilir)", flush=True)
    print(f"İletim sırası      : {' → '.join(build_delivery_chain(config))}", flush=True)
    print(f"Medya sınırı       : {config.get('max_media_mb', 25)} MB", flush=True)
    mode = link_appendix_mode(config)
    mode_label = {"smart": f"akıllı ({PRODUCT_LINK_TEXT})", "all": "tüm gizli linkler", "off": "kapalı"}[mode]
    print(f"Bağlantı ekleri     : {mode_label} "
          f"| mesaj linki (grup adına gömülü): {'açık' if config_flag(config.get('message_link')) else 'kapalı'} "
          f"| bildirim medyası: {'açık' if config_flag(config.get('notify_media')) else 'kapalı'} "
          f"| tek mesaj: {'açık' if config_flag(config.get('single_message')) else 'kapalı'} "
          f"| kaynak adı (kalın, mesaja bağlı): {'açık' if config_flag(config.get('source_footer')) else 'kapalı'} "
          f"| komut temizliği: {'açık' if config_flag(config.get('clean_commands')) else 'kapalı'} "
          f"| alıntı temizliği: {'açık' if config_flag(config.get('clean_quote_duplicates'), False) else 'kapalı'}", flush=True)
    try:
        window_hours = int(config.get("dedup_window_hours", 12))
    except (TypeError, ValueError):
        window_hours = 12
    try:
        scan_limit = int(config.get("dedup_scan_limit", 100))
    except (TypeError, ValueError):
        scan_limit = 100
    print(f"Tekrar birleştirme : {'açık' if config_flag(config.get('dedup_enabled'), True) else 'kapalı'} "
          f"(pencere: {window_hours} sa · açılış taraması: son {scan_limit} mesaj)", flush=True)
    print(f"Otomatik yenileme  : {config.get('auto_restart', True)} "
          f"({os.getenv('RESTART_AFTER_MINUTES', '330')} dk sonra)", flush=True)
    if problems:
        print("-" * 62, flush=True)
        print("SORUNLAR:", flush=True)
        for problem in problems:
            print(f"  ✗ {problem}", flush=True)
    else:
        print("Sonuç              : yapılandırma geçerli ✅", flush=True)
    print("=" * 62, flush=True)


_INVITE_LINK_RE = re.compile(
    r"^https?://(?:t\.me|telegram\.me)/(?:\+|joinchat/)([A-Za-z0-9_-]+)/*$",
    re.IGNORECASE,
)


async def resolve_chat(client: TelegramClient, value: int | str) -> tuple[int, str, Any]:
    """Resolve joined invite links without joining or paying on the user's behalf."""
    invite = _INVITE_LINK_RE.fullmatch(value.strip()) if isinstance(value, str) else None
    if invite:
        checked = await client(functions.messages.CheckChatInviteRequest(invite.group(1)))
        if not isinstance(checked, types.ChatInviteAlready) or getattr(checked.chat, "left", False):
            raise ValueError("Önce takip hesabıyla daveti açıp üyeliği tamamla; onay/ücret gerekebilir.")
        entity = checked.chat
    else:
        try:
            entity = await client.get_entity(value)
        except ValueError:
            # StringSession does not persist Telethon's entity cache. Refresh
            # dialogs for a private numeric ID after a fresh process starts.
            if not isinstance(value, int):
                raise
            await client.get_dialogs()
            entity = await client.get_entity(value)
    return utils.get_peer_id(entity), display_name(entity), entity


async def dispatch_next_run(pat: str) -> tuple[bool, str]:
    """Yeni bir Actions çalışması başlatır. GH_PAT yoksa manuel moda düşer."""
    if not pat:
        return False, "GH_PAT tanımlı değil; Actions sayfasından 'Run workflow' ile başlat."
    repository = os.getenv("GITHUB_REPOSITORY", "")
    workflow = os.getenv("GITHUB_WORKFLOW_FILE", "telegram-monitor.yml")
    ref = os.getenv("GITHUB_REF_NAME", "main")
    if not repository:
        return False, "GITHUB_REPOSITORY bulunamadı (Actions dışında mı çalışıyor?)."
    url = f"https://api.github.com/repos/{repository}/actions/workflows/{workflow}/dispatches"
    request = urllib.request.Request(
        url,
        data=json.dumps({"ref": ref}).encode(),
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {pat}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "telegram-indirim-takipci",
        },
    )
    try:
        with await asyncio.to_thread(urllib.request.urlopen, request, timeout=20) as response:
            response.read()
        log.info("Sonraki GitHub Actions çalışması başlatıldı (%s/%s @ %s).", repository, workflow, ref)
        return True, f"Yeni çalışma başlatıldı ({repository}@{ref})."
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        log.error("Actions başlatılamadı: HTTP %s %s", exc.code, body)
        return False, f"GitHub isteği başarısız: HTTP {exc.code}. GH_PAT'in 'Actions: Read and write' yetkisi var mı?"
    except Exception as exc:  # noqa: BLE001 - ağ hatası bot'u öldürmemeli
        log.exception("Actions başlatılamadı.")
        return False, f"GitHub isteği başarısız: {type(exc).__name__}."


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


# ---------------------------------------------------------------------------
# İletim zinciri (korumalı içerik için alternatifli yol)
# ---------------------------------------------------------------------------

# Birçok indirim kanalı "içeriği koru" (noforwards) ayarını açar. O kanallarda
# forward CHAT_FORWARDS_RESTRICTED hatası verir, copy de aynı şekilde patlayabilir.
# Bu yüzden tek bir yol denemiyoruz: sırayla dene, hata alırsan bir sonrakine geç.
DELIVERY_MODES = ("forward", "copy", "media", "text", "link")


def build_delivery_chain(config: dict) -> list[str]:
    """Denenecek iletim yollarının sırasını kur.

    `delivery_modes` açıkça verilmişse o sırayı kullanır; verilmediyse eski
    `copy_mode` alanından türetir ve geri kalan yolları yedek olarak ekler.
    """
    explicit = config.get("delivery_modes")
    if explicit:
        chain = [str(m).strip().lower() for m in explicit if str(m).strip()]
    else:
        preferred = str(config.get("copy_mode", "forward")).strip().lower()
        chain = [preferred] if preferred in DELIVERY_MODES else ["forward"]
    # Bilinmeyen isimleri at, tekrar edenleri koruyarak temizle, yedekleri ekle.
    seen: list[str] = []
    for mode in chain:
        if mode in DELIVERY_MODES and mode not in seen:
            seen.append(mode)
        elif mode not in DELIVERY_MODES:
            log.warning("Bilinmeyen delivery_mode %r yok sayıldı (geçerli: %s).", mode, ", ".join(DELIVERY_MODES))
    for fallback in DELIVERY_MODES:
        if fallback not in seen:
            seen.append(fallback)
    return seen


def build_message_link(event: Any, source: dict | None = None) -> str | None:
    """Mesajın t.me bağlantısını kur; kurulamıyorsa None döner."""
    message_id = getattr(event, "id", None) or getattr(getattr(event, "message", None), "id", None)
    if not message_id:
        return None
    username = (source or {}).get("username") or getattr(getattr(event, "chat", None), "username", None)
    if not username and source:
        # ID ile çözülen özel süpergruplarda entity.username boş olabilir; ama
        # config'de istenen değer hâlâ herkese açık @kullanıcıadı'dır. Link
        # üretilemezse note_dedup_source adı linksiz yazar ve rozette o kaynak
        # tıklanamaz (kullanıcı bildirimi: "orijinal link gömülmemiş").
        requested = str(source.get("requested") or "").strip()
        if requested.startswith("@"):
            username = requested.lstrip("@")
    if username:
        return f"https://t.me/{str(username).lstrip('@')}/{message_id}"
    chat_id = getattr(event, "chat_id", None)
    # t.me/c/<id>/<mesaj> yalnızca kanal/süpergrup için çalışır (-100... ile başlar).
    if isinstance(chat_id, int) and chat_id < -1000000000000:
        return f"https://t.me/c/{abs(chat_id) - 1000000000000}/{message_id}"
    return None


def product_links(obj: Any, limit: int = LINK_APPENDIX_LIMIT) -> list[dict[str, str]]:
    """Bildirimde ``👉 Ürün Linki 👈`` olarak verilecek bağlantılar (kaynak sırasıyla).

    Kaynak şablonları farklıdır; ürün adresi butonda, yazının altına gizli
    hyperlink'te, görünür URL'de ya da yalnız önizlemede durabilir. Buton,
    kaynağın bilinçli "ürüne git" çağrısıdır: ürüne gidebilen HER buton listelenir
    (aynı adres gövdede gizli link olsa da). Buton yoksa mesajdaki İLK ürün
    bağlantısı tek satır olur; alıntıdaki diğer bağlantılar yerinde, yedek olarak
    kalır. Arama servisleri ve sohbet/sosyal ağ adresleri ürün linki sayılmaz.
    """
    links = [item for item in extract_links(obj) if is_product_link(item["url"])]
    markup = getattr(_as_message(obj), "reply_markup", None)
    button_urls = {
        info[0].rstrip("/").lower()
        for row in getattr(markup, "rows", None) or []
        for button in getattr(row, "buttons", None) or []
        if (info := button_link(button))
    }
    buttons = [item for item in links if item["url"].rstrip("/").lower() in button_urls]
    return buttons[:limit] if buttons else links[:1]


def _remember(store_: "collections.OrderedDict[tuple[int, int], float]", chat_id: Any, message_id: Any) -> None:
    store_[(int(chat_id), int(message_id))] = time.time()
    while len(store_) > 5000:
        store_.popitem(last=False)


def live_event_should_skip(event: Any) -> bool:
    """Atomically claim a source/message pair before the first await.

    Both live and polled events use the same registry, including in-flight
    deliveries. POLL_DISPATCHED only marks scheduled work, not completed work.
    """
    key = (int(event.chat_id), int(event.id))
    if key in appstate.LIVE_SEEN or key in appstate.SOURCE_IN_FLIGHT:
        return True
    if key in appstate.POLL_DISPATCHED and not getattr(event, "_fast_poll", False):
        return True
    _remember(appstate.LIVE_SEEN, *key)
    return False


# --- Cursor (tek cursor / grup) — yalnızca RAM, bilinçli /restart’ta tek persist

def _cursor_ensure(chat_id: int) -> dict[str, Any]:
    cid = int(chat_id)
    if cid not in appstate.CURSOR_RAM:
        appstate.CURSOR_RAM[cid] = {"cursor": 0, "pending": set(), "completed": set(), "failed": set(), "failed_at": {}, "initialized": True}
    return appstate.CURSOR_RAM[cid]


def _cursor_advance(chat_id: int) -> None:
    """Sade ardışık ilerleme: yalnızca completed zinciri ilerler; gap’ler poll’de doğrulanır."""
    st = _cursor_ensure(chat_id)
    while True:
        nxt = int(st["cursor"]) + 1
        if nxt in st["pending"]:
            break
        if nxt in st["failed"]:
            break  # başarısız — sonraki poll’de retry edilecek, kaybetme yok
        if nxt in st["completed"]:
            st["completed"].remove(nxt)
            st["cursor"] = nxt
            continue
        break
    # Budama kaldırıldı: cursor önünde poll ile henüz doğrulanmamış
    # bir boşluk varken bile pending/failed boş olabilir. 500+ kayıtlı
    # tamamlanmış zinciri silmek cursor'u kilitleyebilir; bu yüzden
    # completed kayıtları güvenle tüketilene kadar korunur.


def _cursor_mark_pending(chat_id: int, msg_id: int) -> None:
    st = _cursor_ensure(chat_id)
    mid = int(msg_id)
    st["pending"].add(mid)
    # önceki failed aynı ID yeniden deneniyorsa temizle
    st["failed"].discard(mid)
    st["failed_at"].pop(mid, None)


def _cursor_mark_success(chat_id: int, msg_id: int) -> None:
    st = _cursor_ensure(chat_id)
    mid = int(msg_id)
    st["pending"].discard(mid)
    st["failed"].discard(mid)
    st["failed_at"].pop(mid, None)
    st["completed"].add(mid)
    _cursor_advance(chat_id)


def _cursor_mark_failed(chat_id: int, msg_id: int) -> None:
    st = _cursor_ensure(chat_id)
    mid = int(msg_id)
    st["pending"].discard(mid)
    st["failed"].add(mid)
    st["failed_at"][mid] = time.time()


def cursor_load_from_config(config: dict) -> None:
    """config.json → source_cursors RAM’e yükle (main başlangıcında)."""
    appstate.CURSOR_RAM.clear()
    raw = config.get("source_cursors")
    if not isinstance(raw, dict):
        return
    for k, v in raw.items():
        try:
            cid = int(k)
            cur = int(v)
            # resume_check: bu imleç bir önceki oturumdan kalma; ilk taramada yaş sınırı uygulanır
            appstate.CURSOR_RAM[cid] = {"cursor": cur, "pending": set(), "completed": set(), "failed": set(), "failed_at": {},
                               "initialized": True, "resume_check": True}
        except Exception:
            continue


def cursor_collect_for_persist() -> dict[str, int]:
    """Yalnızca başlangıç noktası belirlenmiş RAM cursor’larını kalıcılaştır."""
    return {
        str(int(cid)): int(st["cursor"])
        for cid, st in appstate.CURSOR_RAM.items()
        if st.get("initialized", True)
    }


async def cursor_persist_for_restart(store: ConfigStore) -> tuple[bool, str]:
    """Bilinçli /restart öncesi tek toplu kalıcı kayıt. Başarı → restart serbest."""
    # Mevcut atomic_write_json + commit_and_push + GIT_WRITE_LOCK yolunu kullan
    store.config["source_cursors"] = cursor_collect_for_persist()
    ok, note = await save_config(store, "cursor: restart öncesi yedek")
    return ok, note


def maybe_report_slow(tg_client: Any, source_name: str, lag: float, steps: list[tuple[str, float]]) -> None:
    """Toplam süre eşiği aşarsa Kayıtlı Mesajlar'a süre dökümü gönder (arka planda; en fazla dakikada 1)."""
    processing = sum(seconds for _, seconds in steps)
    total = processing + max(lag, 0.0)
    if appstate.SLOW_REPORT_SECONDS <= 0 or total < appstate.SLOW_REPORT_SECONDS:
        return
    now = time.time()
    if now - appstate._SLOW_REPORT_LAST < 60:
        return
    appstate._SLOW_REPORT_LAST = now
    lines = [f"⏱ Yavaş bildirim ({total:.0f} sn) — {source_name}"]
    if lag >= 0:
        lines.append(f"• Telegram → bot: {lag:.1f} sn")
    lines += [f"• {name}: {seconds:.1f} sn" for name, seconds in steps]
    media = appstate.MEDIA_TIMING.get()
    if media:
        lines.append(
            f"• Medya: {media.get('kind')}, {media.get('mb', 0):.1f} MB, "
            f"indirme {media.get('download', 0):.1f} sn, yükleme {media.get('upload', 0):.1f} sn"
        )
    text = "\n".join(lines)

    async def _send() -> None:
        try:
            await tg_client.send_message("me", text)
        except Exception as exc:  # noqa: BLE001 - rapor gönderilemese de akış sürsün
            log.warning("Yavaş bildirim raporu gönderilemedi: %s", type(exc).__name__)

    try:
        asyncio.get_running_loop().create_task(_send())
    except RuntimeError:
        pass


def _compose_resolve_header(
    obj: Any, cleaned_obj: Any, source_body: str, fallback_title: str,
) -> tuple[str, str, str, bool]:
    """Üst satır (fiyat + ürün adı) ve alıntı işaretini SMART_HEADER moduna göre kur."""
    product_title = fallback_title
    price_text = ""
    quote_body = False
    if appstate.SMART_HEADER:
        smart_title, smart_price = smart_header_info(obj, cleaned_obj, source_body)
        multiple_products = smart_price.reason == "birden fazla ürün bloğu: seçim belirsiz"
        product_title = smart_title or (
            ("Birden fazla ürün" if multiple_products else "")
            or (product_title if not price_filter.is_generic_title(product_title) else "")
        )
        if smart_price.value is not None and smart_price.confidence in {"high", "medium"}:
            price_text = header_price_text(smart_price.value)
        elif multiple_products and product_title != "Birden fazla ürün":
            price_text = ""  # reliable per-item titles are enough; there is no single bundle price
        elif multiple_products:
            price_text = PRICE_MISSING_MARK
        elif product_title and _is_coupon_announcement(source_body):
            # Kullanıcı isteği: "indirim kuponu bildirimlerinde de [soru işareti]
            # koymayalım". Kupon/kampanya duyurusunda ürün fiyatı YOKTUR; bu bir
            # eksiklik değil, iletinin türüdür. `❓` yazmak bilgi vermiyordu.
            # "Fiyatı olan gönderiyi kupon sanıp çöpe atma" koruması artık dal
            # sırasına bağlı değil: `_is_coupon_announcement` güvenli fiyat varsa
            # kendi içinde False döner (bkz. `_has_safe_price`). Yani buraya
            # yalnız gerçekten fiyatsız duyurular gelir.
            price_text = ""
        else:
            price_text = PRICE_MISSING_MARK
        quote_body = bool(source_body.strip())
    if price_text == PRICE_MISSING_MARK and not product_title:
        price_text = ""
    header_text = build_header_line(price_text, product_title)
    return header_text, price_text, product_title, quote_body


def _compose_build_appendix(
    cleaned_obj: Any, source_body: str, link_kinds: Sequence[str] | None, hidden_links: bool = True,
    has_keyboard: bool = False, product_limit: int = LINK_APPENDIX_LIMIT,
) -> tuple[list[tuple[str, int]], list[tuple[int, int, str]], list[str]]:
    """Ek satırlar: önce ``👉 Ürün Linki 👈``, sonra başka türlü taşınamayan linkler.

    İlk değer ``(satır, öncelik)`` listesidir (bkz. ``APPENDIX_PRODUCT`` …); sınıra
    sığdırma ``_compose_fit_appendix`` içindedir. İkinci değer ürün satırlarının ek
    içindeki ``(UTF-16 başlangıç, uzunluk, URL)`` konumlarıdır; URL metne yazılmaz, bu
    aralığa gizli hyperlink olarak gömülür. ``hidden_links=False`` (biçimsiz son çare
    iletisi) URL'yi satıra açıkça yazar. ``link_appendix: all`` (gizli hyperlink'ler de
    listelenir) ham URL dökümünü, ``off`` ise eksiz düzeni aynen korur; ikisinde de
    ürün satırı üretilmez.

    En çok ``product_limit`` ürün satırı ve ayrıca en çok ``LINK_APPENDIX_LIMIT`` ``🔗``
    satırı yazılır. Satır alamayan ürün butonu ``has_keyboard`` (bot bildirimi) ise klavyede
    düğme olarak kalır; klavyesiz hesap kopyasında ``🔗 etiket: adres`` satırı olur.

    Üçüncü değer yalın ektir: ürün satırı ve eleme olmasaydı yazılacak ``🔗`` satırları
    (mesajın ilk ``LINK_APPENDIX_LIMIT`` gizli bağlantısı ve etiketi silinen linkler).
    Karakter sınırında ölçü olarak kullanılır (bkz. ``_compose_fit_appendix``).
    """
    candidates = (product_links(cleaned_obj, limit=MAX_INLINE_KEYBOARD_BUTTONS)
                  if link_kinds and "entity" not in link_kinds else [])
    products = candidates[:max(0, min(LINK_APPENDIX_LIMIT, product_limit))]
    lines: list[tuple[str, int]] = []
    spans: list[tuple[int, int, str]] = []
    cursor = 0
    for number, item in enumerate(products, 1):
        label = PRODUCT_LINK_TEXT if len(products) == 1 else PRODUCT_LINK_TEXT.replace("Linki", f"Linki {number}")
        if hidden_links:
            spans.append((cursor, utf16_length(label), item["url"]))
        else:
            label = f"{label}: {item['url']}"
        lines.append((label, APPENDIX_PRODUCT))
        cursor += utf16_length(label) + 1

    def extend(found: Sequence[tuple[str, bool]]) -> None:
        # Aynı satır iki gruptan da gelebilir (korunan link ``all`` modunda ana grupta
        # da görünür); tek kez yazılır ve en yüksek önceliği alır.
        written = {text: index for index, (text, _) in enumerate(lines)}
        for text, required in found:
            rank = APPENDIX_REQUIRED if required else APPENDIX_SPARE
            if text not in written:
                written[text] = len(lines)
                lines.append((text, rank))
            elif lines[written[text]][1] < rank:
                lines[written[text]] = (text, rank)

    plain: list[str] = []
    if link_kinds:
        extend(link_appendix_lines(
            cleaned_obj, link_kinds, has_keyboard=has_keyboard,
            exclude_urls=[item["url"] for item in (candidates if has_keyboard else products)],
        ))
        plain.extend(text for text, _ in link_appendix_lines(cleaned_obj, link_kinds))
    carried = getattr(cleaned_obj, "preserved_links", None) or []
    if carried:
        carried_only = SimpleNamespace(
            message=source_body, entities=[], reply_markup=None, media=None, preserved_links=carried,
        )
        extend(link_appendix_lines(
            carried_only, ("webpage",), exclude_urls=[item["url"] for item in products],
        ))
        plain.extend(text for text, _ in link_appendix_lines(carried_only, ("webpage",)) if text not in plain)
    return lines, spans, plain


def compose_message(
    obj: Any,
    *,
    limit: int = MESSAGE_LIMIT,
    link_kinds: Sequence[str] | None = ("button", "webpage"),
    message_link: str | None = None,
    message_link_label: str = MESSAGE_LINK_LABEL,
    source_name: str | None = None,
    hidden_links: bool = True,
    has_keyboard: bool = False,
    source_name_url: str | None = None,
) -> dict[str, Any]:
    """Basit bildirim düzenini kur (kullanıcı istekleri):

    1. en üstte TEK SATIR üst başlık (kalın) — ``smart_header`` açıksa önce
       fiyat, sonra ``—``, sonra ürün adı (``₺56 — Penti Kız Çocuk ...``);
       fiyat güvenle bulunamadığında fiyat yerine ``❓`` yazılır
       (``❓ — Penti Kız Çocuk ...``). Kapalıysa yalnız ürün adı gelir.
       Fiyatın başta olması, bildirimin kesilen ön izlemesinde fiyat ile ürün
       adının ilk kelimelerinin birlikte görülmesini sağlar,
    2. ``👉 Ürün Linki 👈`` — ürün adresi bu yazıya gömülüdür (bkz. ``product_links``);
       alıntıdaki özgün bağlantılar yedek olarak yerinde kalır. Altında, başka türlü
       taşınamayan bağlantılar ``🔗 etiket: adres`` satırı olur (bkz. ``link_appendix_lines``),
    3. altında kaynak mesaj **olduğu gibi** (yalnızca güvenlik temizliği
       uygulanır: reklam/işbirliği etiketleri, WhatsApp/Instagram linkleri,
       Instagram tanıtımları, kanal tanıtımı ve salt hashtag satırları),
    4. en altta kaynak grup adı (kalın) — **orijinal mesajın adresi bu adın
       İÇİNE gömülüdür** (dokununca açılır, URL metinde görünmez). Kullanıcı
       isteği: ayrı ``🔗 Mesajı Gör`` satırı kaldırıldı; mesaj kısaldı ve
       bağlantı yine tek dokunuşla ulaşılabilir.

    Ürün satırı (2) bilerek alıntının ÜSTÜNDEDİR (kullanıcı isteği): fiyat-ad
    satırının hemen altında ürün adresi durur, kaynak mesaj onun altına iner.
    Aynı gerekçe, bildirimin kesilen ön izlemesinde ürün bağlantısının da
    görünmesini sağlar.

    Üst satır YALNIZCA görünen metindir: arama sorguları (Google Alışveriş,
    Akakçe, Cimri) ve tekrar (dedup) anahtarı kaynak mesajdan ayıklanan
    fiyatsız ad üzerinden kurulur; başlığa eklenen fiyat hiçbir aramaya
    taşınmaz (bkz. ``split_header_line`` / ``header_search_title``).

    Tekrarlı paylaşımlarda kaynak grup adı satırı kaldırılır ve yerine
    çoklu paylaşım notu yazılır (``📌 N kere paylaşıldı: <gruplar>``;
    bkz. ``_dedup_apply_badge``). Notta yazan HER grup adı, o grubun kendi
    mesajına bağlanır. Ürün başlığı bulunamayan mesajlarda üst satır yalnızca
    fiyatı taşır; başlık da bulunamadıysa üst satır olmaz, gövde ve alt
    satırlar aynen iletilir.

    Kaynak adı kapanmışsa (``source_footer: false`` ya da ad hiç yoksa)
    orijinal mesajın adresi kaybolmaz: yerine eski ``🔗 Mesajı Gör: <t.me>``
    satırı yazılır (tek başına bu satır, adresin son taşıyıcısıdır).

    ``has_keyboard`` iletinin inline klavyeyle gideceğini söyler (yalnız bot bildirimi;
    kullanıcı hesabı klavye gönderemez). Klavyede duran kaynak butonu satır olmak
    zorunda değildir: arama butonları hiç yazılmaz, diğer buton satırları da yalnız
    gövdeyi kırpmıyorsa yazılır (bkz. ``_compose_fit_appendix``). Klavyesiz kopyada
    bu satırlar butonun tek taşıyıcısıdır.

    Kaynak mesaj nesnesine hiçbir zaman dokunulmaz; yalnızca kopya yeniden
    kurulur. ``body`` orijinal mesajı, ``content`` en alt blok (kaynak adı)
    hariç her şeyi; ``entities`` ise tüm çıktı için doğru UTF-16 konumlarını
    taşır.
    """
    cleaned_obj = sanitize_message(obj)
    source_body = message_text(cleaned_obj)
    product_title = _search_query(cleaned_obj)

    header_text, price_text, product_title, quote_body = _compose_resolve_header(
        obj, cleaned_obj, source_body, product_title,
    )

    body = source_body
    body_entities = entities_for_text(cleaned_obj, source_body)
    body_obj = cleaned_obj
    if appstate.CLEAN_QUOTE_DUPLICATES and appstate.SMART_HEADER and quote_body:
        # Yalnızca GÖRÜNEN alıntı temizlenir. Başlık, fiyat, tekrar kimliği,
        # arama sorgusu ve ürün linkleri ORİJİNAL (kırpılmamış) mesajdan
        # hesaplanmaya devam eder; doğru tespitler değişmez.
        new_body, new_body_entities = strip_quoted_detected_lines(
            source_body, body_entities, product_title, _webpage_hints(obj),
        )
        if new_body != source_body:
            body, body_entities = new_body, new_body_entities
            body_obj = copy.copy(cleaned_obj)
            body_obj.message = body
            body_obj.raw_text = body
            body_obj.entities = body_entities

    name = (source_name or "").strip() or None
    # Kaynak adı varsa orijinal mesajın adresi ADIN İÇİNE gömülür; ayrı satır
    # yazılmaz (kullanıcı isteği). Ad yoksa adresin tek taşıyıcısı eski
    # ``🔗 Mesajı Gör`` satırıdır, o yüzden yalnız o durumda yazılır.
    # Çoklu paylaşım notunda adlar kendi paylaşımlarına bağlandığı için ilk
    # bildirimin adı da ona gönderilen adrese bağlanır (``source_name_url``).
    name_url = (source_name_url or message_link) if name else None
    source_line = (
        f"🔗 {message_link_label}: {message_link}" if (message_link and not name_url) else ""
    )
    sep = "\n\n"
    # ÖLÇÜ BÜTÇESİ SABİT: ek satırları, eskiden ``🔗 Mesajı Gör`` satırının durduğu
    # yeri de hesaba katar. Mesaj linki artık ada gömülü olduğu için o satır
    # metinden kalktı; bütçe de birlikte büyüseydi fazladan ``🔗`` satırları
    # gövdenin sonundan yer yerdi (gövde bir satır uzun kalabildiği için ürün
    # satırı artık "sığmıyor" görünüp düşerdi). Bütçeyi sabit tutmak iki şeyi
    # garanti eder: hiçbir bağlantı yeni yer işgali yüzünden kapanmaz ve ürün
    # satırları eskisi gibi yazılmaya devam eder. Kullanıcı isteği: bildirim
    # yalnız KISALSIN, başka hiçbir şey değişmesin.
    # ``real_tail`` gerçek metindir (mesaj linki adın içinde, ayrı satır yok);
    # ``measure_tail`` ise o satırın bütçesini de taşır.
    measure_tail = (source_line or (f"🔗 {message_link_label}: {message_link}" if message_link else ""),
                    name or "")
    appendix_text, appendix_links = _compose_fit_appendix(
        lambda product_limit: _compose_build_appendix(
            cleaned_obj, source_body, link_kinds, hidden_links, has_keyboard, product_limit),
        body_obj, header_text, body, measure_tail, limit, sep,
        real_tail=(source_line, name or ""),
    )

    segments: list[tuple[str, str]] = [
        ("header", header_text),
        ("appendix", appendix_text),
        ("body", body),
        ("source", source_line),
        ("name", name or ""),
    ]

    segments, appendix_text, name, source_line = _compose_drop_overflow_parts(
        segments, appendix_text, name, source_line, limit, sep,
    )
    if not name:
        # Kaynak adı düştüyse gömülü adres de düşerdi: adresin son taşıyıcısı
        # olarak ``Mesajı Gör`` satırını geri getir (metin ve entity tutarlı).
        source_line = f"🔗 {message_link_label}: {message_link}" if message_link else ""
        name_url = None
        segments = [(kind, source_line if kind == "source" else value) for kind, value in segments]
        segments, appendix_text, name, source_line = _compose_drop_overflow_parts(
            segments, appendix_text, name, source_line, limit, sep,
        )
        if not source_line:
            name_url = None
    segments, header_text, price_text, product_title = _compose_truncate_header(
        segments, header_text, price_text, product_title, limit, sep,
    )
    segments, body = _compose_truncate_body(segments, limit, sep)

    text, content, offsets, body_offset, name_offset = _compose_assemble_text(segments, sep)
    content_entities, name_entities = _compose_build_entities(
        header_text, body, body_entities, name, quote_body, offsets, body_offset, name_offset,
        appendix_links if appendix_text else (), name_url,
    )

    composed_message = SimpleNamespace(
        message=content,
        raw_text=content,
        entities=list(content_entities),
    )
    return {
        "text": text,
        "body": body,
        "content": content,
        "appendix": appendix_text,
        "source_line": source_line,
        "source_url": message_link if (source_line or name_url) else None,
        "source_name": name,
        "source_name_offset": name_offset,
        "source_name_length": utf16_length(name) if (name_offset >= 0 and name) else 0,
        "source_name_url": name_url if name_offset >= 0 else None,
        "source_name_entities": name_entities,
        "header": header_text,
        "price": price_text,
        "product_title": product_title or None,
        "title": product_title,
        "body_offset": body_offset,
        "entities": [*content_entities, *name_entities],
        "message": composed_message,
    }


def prune_dedup_cache(
    cache: dict[str, dict[str, Any]], now: float, window_seconds: float, max_entries: int,
) -> int:
    """Süresi dolmuş ve yarım kalmış kayıtları at; fazlaysa en eskileri düşür.

    Dönen değer atılan kayıt sayısıdır. Bekleyen (``pending``) kayıtlar
    ``DEDUP_RESERVE_TTL`` dolmadan atılmaz; kapasite aşımında önce hazır
    kayıtlar elenir.
    """
    dropped = 0
    for key in list(cache):
        entry = cache.get(key) or {}
        age = now - float(entry.get("first_seen", now))
        if entry.get("pending"):
            owner = entry.get("owner")
            if age > appstate.DEDUP_RESERVE_TTL and (owner is None or owner.done()):
                entry["failed"] = True
                try:
                    entry["ready"].set()
                except Exception:  # noqa: BLE001 - temizlik akışı durdurmaz
                    pass
                del cache[key]
                dropped += 1
        elif age > window_seconds:
            del cache[key]
            dropped += 1
    overflow = len(cache) - max(1, int(max_entries))
    if overflow > 0:
        ordered = sorted(cache.items(), key=lambda item: float(item[1].get("first_seen", now)))
        for key, entry in ordered:
            if overflow <= 0:
                break
            if entry.get("pending"):
                continue
            del cache[key]
            overflow -= 1
            dropped += 1
        for key, entry in ordered:
            if overflow <= 0:
                break
            owner = entry.get("owner")
            if owner is not None and not owner.done():
                continue
            if key in cache:
                del cache[key]
                overflow -= 1
                dropped += 1
    return dropped


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


def build_status_text(config: dict, *, connected: bool = True) -> str:
    last = appstate.STATS["last_match"]
    last_line = "henüz eşleşme yok"
    if last:
        last_line = f"{humanize(time.time() - last)} önce ({appstate.STATS['last_match_source']})"
    keywords = telegram_list_items(config, "include_keywords")
    exclude_words = telegram_list_items(config, "exclude_keywords")
    mode = match_mode_of(config)
    state = filter_state_of(config)
    keyword_text = ", ".join(str(item) for item in keywords) or "(kelime yok)"
    exclude_text = ", ".join(str(item) for item in exclude_words) or "(kelime yok)"
    if state["include"]:
        filter_line = f"• 🔎 Telegram dahili filtre: AÇIK · {keyword_text} ({mode})"
    else:
        filter_line = "• 🔎 Telegram dahili filtre: KAPALI · kelimeler yok sayılıyor"
    if state["exclude"]:
        filter_line += f"\n• 🚫 Telegram harici filtre: AÇIK · {exclude_text}"
    else:
        filter_line += "\n• 🚫 Telegram harici filtre: KAPALI · engelleme yapılmıyor"
    filter_line += "\n• Değiştirmek için /tgfiltreac ve /tgfiltrekapat"
    if not connected:
        listening = "• Dinleme: BAĞLANTI YOK — Telegram bağlantısı bekleniyor."
    elif not appstate.SOURCE_IDS:
        listening = "• Dinleme: KAYNAK YOK — kaynak listesini kontrol et."
    else:
        listening = "• Dinleme: AKTİF — kaynak mesajları bekleniyor."
    return (
        "✅ Takipçi aktif\n"
        f"{listening}\n"
        f"• Çalışma süresi: {humanize(time.time() - STARTED_AT)}"
        f" · başlangıç {timefmt.stamp(STARTED_AT)} TSİ (şimdi {timefmt.clock()} TSİ)\n"
        f"• Dinlenen kaynak: {len(appstate.SOURCE_IDS)}/{len(config.get('source_chats') or [])}"
        + (f" (çözülemeyen {len(appstate.SOURCE_FAILURES)})" if appstate.SOURCE_FAILURES else "")
        + "\n"
        f"• Görülen: {appstate.STATS['seen']} | Eşleşen: {appstate.STATS['matched']} | İletilen: {appstate.STATS['forwarded']}"
        + (f" | Hedefe iletilemeyen: {appstate.STATS['failed']}" if appstate.STATS["failed"] else "")
        + (f" | Telefon bildirimi başarısız: {appstate.STATS['notification_failed']}"
           if appstate.STATS.get("notification_failed") else "")
        + "\n"
        f"• Son eşleşme: {last_line}\n"
        f"• Hedef: {appstate.DESTINATION_LABEL}\n"
        f"• Kontrol sohbeti: {('Bot özel sohbeti + Kayıtlı Mesajlar' if config_flag(config.get('private_control'), False) else ', '.join(appstate.CONTROL_NAMES) or 'me')}\n"
        f"{filter_line}\n"
        f"• İletim sırası: {' → '.join(appstate.DELIVERY_CHAIN) or 'yok'}"
        + (f" | kullanılan: {', '.join(f'{k}×{v}' for k, v in appstate.STATS['modes'].items())}"
           if appstate.STATS["modes"] else "")
        + "\n"
        f"• Tek mesaj modu: {'açık' if appstate.SINGLE_MESSAGE else 'kapalı'}"
        + (f" · bildirim sonrası silinen hesap kopyası: {appstate.STATS.get('cleaned', 0)}"
           if appstate.SINGLE_MESSAGE and appstate.STATS.get("cleaned") else "")
        + "\n"
        f"• Komut temizliği: {'açık' if appstate.CLEAN_COMMANDS else 'kapalı'}"
        + (f" · silinen eski komut mesajı: {appstate.STATS.get('cleaned_commands', 0)}"
           if appstate.CLEAN_COMMANDS and appstate.STATS.get("cleaned_commands") else "")
        + "\n"
        + (
            f"• Tekrar birleştirme: açık · birleştirilen {appstate.STATS.get('deduped', 0)}"
            f" · rozet {appstate.STATS.get('dedup_edits', 0)} · izlenen {len(appstate.DEDUP_CACHE)} başlık"
            if appstate.DEDUP_ENABLED else "• Tekrar birleştirme: kapalı"
        )
        + "\n"
        + (
            f"• Hızlı tarama: açık · aralık {appstate.POLL_TELEMETRY['interval']:.0f} sn"
            f" · son yoklama {appstate.POLL_TELEMETRY['last_elapsed']:.1f} sn"
            f" · FloodWait {int(appstate.POLL_TELEMETRY['flood_count'])}"
            + (f" (son {appstate.POLL_TELEMETRY['last_flood_wait']:.0f} sn)" if appstate.POLL_TELEMETRY["flood_count"] else "")
            if appstate.FAST_POLL else "• Hızlı tarama: kapalı"
        )
        + "\n"
        + _bot_api_stats_line()
    )


def build_source_text() -> str:
    lines = [f"İzlenen {len(appstate.SOURCES)} kaynak:"]
    lines += [f"• {item['name']}  [{item['id']}]" + ("" if item["joined"] else "  ⚠️ ÜYE DEĞİLSİN")
              for item in appstate.SOURCES]
    for value, exc in appstate.SOURCE_FAILURES:
        lines.append(f"✗ {value} çözülemedi: {exc}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Kalıcılık: atomik dosya yazımı + repo'ya işleme
# ---------------------------------------------------------------------------

def atomic_write_json(path: Path, data: dict) -> None:
    """JSON'u yarıda kalmayacak şekilde yaz: geçici dosya → fsync → os.replace.

    Yarım yazılmış bir config.json bot'u açılışta çökertir; bu yüzden doğrudan
    hedef dosyaya yazmıyoruz.
    """
    path = Path(path)
    persisted_data = dict(data)
    persisted_data.pop("notify_bot_token", None)
    payload = json.dumps(persisted_data, ensure_ascii=False, indent=2) + "\n"
    directory = str(path.parent) if str(path.parent) else "."
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=directory,
        prefix=f".{path.name}.", suffix=".tmp", delete=False,
    )
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise
    try:  # dizin girdisinin de diske inmesini garantile
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


def _run_git(args: Sequence[str], cwd: str, timeout: int = 30) -> tuple[int, str]:
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except FileNotFoundError:
        return 127, "git komutu bulunamadı"
    except subprocess.TimeoutExpired:
        return 124, f"git {args[0]} zaman aşımına uğradı"
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def git_repo_root(path: str | os.PathLike[str]) -> Path | None:
    """Verilen yolun bağlı olduğu git deposunun kökünü bul; yoksa None."""
    code, out = _run_git(["rev-parse", "--show-toplevel"], cwd=str(path))
    if code == 0 and out:
        return Path(out.splitlines()[0].strip())
    return None


def push_token() -> str:
    """Depoya yazmak için kullanılabilecek token (ilk dolu olan)."""
    for name in ("CONFIG_PUSH_TOKEN", "GITHUB_TOKEN", "GH_PAT"):
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def current_branch(repo: Path) -> str:
    code, out = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=str(repo))
    branch = out.splitlines()[0].strip() if code == 0 and out else ""
    if not branch or branch == "HEAD":  # detached HEAD
        branch = os.getenv("GITHUB_REF_NAME", "").strip()
    return branch


GIT_WRITE_LOCK = threading.RLock()


def commit_and_push(path: Path, message: str) -> tuple[str, str]:
    with GIT_WRITE_LOCK:
        return _commit_and_push_locked(path, message)


def _commit_and_push_locked(path: Path, message: str) -> tuple[str, str]:
    """config.json'ı commit'leyip origin'e gönder: (durum, detay).

    durum: pushed | clean | local | no-repo | error
    Hiçbir durumda istisna fırlatmaz; çağıran yalnızca raporlar.
    """
    path = Path(path)
    repo = git_repo_root(path.parent if path.parent != Path("") else ".")
    commit_message = f"config: {message}" if message else "config: güncelleme"
    if repo is None:
        return _push_via_api(path, commit_message)
    try:
        relative = str(path.resolve().relative_to(repo.resolve()))
    except (ValueError, OSError):
        return "no-repo", "config.json depo dışında"

    token = push_token()
    code, out = _run_git(["status", "--porcelain", "--", relative], cwd=str(repo))
    if code != 0:
        return "error", f"git status başarısız: {out[:160]}"
    if not out.strip():
        return "clean", "depoda commit edilecek değişiklik yok"

    code, out = _run_git(["add", "--", relative], cwd=str(repo))
    if code != 0:
        return "error", f"git add başarısız: {out[:160]}"
    code, out = _run_git(
        ["-c", "user.name=telegram-indirim-takipci", "-c",
         "user.email=actions@github.invalid", "commit", "-m", commit_message, "--", relative],
        cwd=str(repo),
    )
    if code != 0:
        return "error", f"git commit başarısız: {out[:160]}"

    # Sıra önemli: Actions'ın GITHUB_REF_NAME'i pull_request olayında dal adı
    # değil "4/merge" gibi bir birleştirme referansıdır. Onu doğrudan refspec
    # olarak kullanmak depoda gereksiz bir dal açar; yerel dal daha güvenilir.
    branch = (os.getenv("CONFIG_PUSH_BRANCH", "").strip()
              or current_branch(repo)
              or os.getenv("GITHUB_REF_NAME", "").strip())
    if not branch:
        return "local", "dal adı bulunamadı; commit yerelde kaldı"

    code, out = push_branch(repo, branch, token)
    if code != 0 and ("non-fast-forward" in out or "fetch first" in out or "rejected" in out):
        _run_git(["pull", "--rebase", "--no-edit", "origin", branch], cwd=str(repo), timeout=60)
        code, out = push_branch(repo, branch, token)
    if code != 0:
        if not token:
            return "local", (f"depoya gönderilemedi ve push token'ı yok; commit yerelde kaldı "
                             f"(git push origin {branch}). Hata: {out[:160]}")
        return "error", f"git push başarısız: {out[:200]}"
    return "pushed", f"origin/{branch}"


def push_branch(repo: Path, branch: str, token: str = "") -> tuple[int, str]:
    """Dalı ``origin``e gönder.

    Önce ortamda hazır kimlik bilgisi varmış gibi düz ``git push`` dener;
    GitHub Actions'ta ``actions/checkout`` kimliği ``.git/config``e yazar ve
    job'a ``contents: write`` verildiği için bu tek başına yeterlidir.
    Başarısız olursa token'ı ``http.extraheader`` ile enjekte ederek tekrar
    dener (VM'de çalıştırma senaryosu).

    Sıra bilinçli: Actions'ta üstüne bir de ``http.extraheader`` eklemek
    çift ``Authorization`` başlığı üretip push'u bozabilir.
    """
    plain = ["push", "origin", f"HEAD:{branch}"]
    code, out = _run_git(plain, cwd=str(repo), timeout=60)
    if code == 0 or not token:
        return code, out
    auth = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return _run_git(
        ["-c", f"http.extraheader=AUTHORIZATION: basic {auth}", *plain],
        cwd=str(repo), timeout=60,
    )


def _github_api(method: str, url: str, token: str, payload: dict | None = None) -> tuple[bool, Any, str]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "telegram-indirim-takipci",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8", "replace")
        return True, (json.loads(body) if body else {}), ""
    except urllib.error.HTTPError as exc:
        return False, None, f"HTTP {exc.code} {exc.read().decode('utf-8', 'replace')[:160]}"
    except Exception as exc:  # noqa: BLE001 - ağ hatası ayar komutunu öldürmemeli
        return False, None, f"{type(exc).__name__}: {exc}"


def _push_via_api(path: Path, message: str) -> tuple[str, str]:
    """git yoksa/config depo dışındaysa GitHub Contents API ile güncelle."""
    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
    token = push_token()
    if not repository or not token:
        return "no-repo", "GITHUB_REPOSITORY veya push token'ı yok"
    branch = (os.getenv("CONFIG_PUSH_BRANCH") or os.getenv("GITHUB_REF_NAME") or "main").strip()
    url = f"https://api.github.com/repos/{repository}/contents/{urllib.parse.quote(path.name)}"
    ok, current, _ = _github_api("GET", f"{url}?ref={branch}", token)
    payload = {
        "message": message,
        "content": base64.b64encode(path.read_bytes()).decode(),
        "branch": branch,
    }
    if ok and isinstance(current, dict) and current.get("sha"):
        payload["sha"] = current["sha"]
    ok, _, detail = _github_api("PUT", url, token, payload)
    if not ok:
        return "error", detail or "Contents API isteği başarısız"
    return "pushed", f"{repository}@{branch} (API)"


async def save_config(store: ConfigStore, note: str = "") -> tuple[bool, str]:
    """config.json'ı atomik yaz ve mümkse repo'ya işle: (yazıldı_mı, rapor)."""
    try:
        await asyncio.to_thread(atomic_write_json, store.path, store.config)
    except OSError as exc:
        return False, (f"⚠️ Ayar çalışan botta aktif ama config.json yazılamadı "
                       f"({type(exc).__name__}: {exc}) — yeniden başlatınca kaybolur.")
    status, detail = await asyncio.to_thread(commit_and_push, store.path, note)
    return True, SAVE_STATUS_TEXT.get(status, SAVE_STATUS_TEXT["error"]).format(detail=detail)


# ---------------------------------------------------------------------------
# Çalışan bot'a ayar uygulama
# ---------------------------------------------------------------------------

def apply_runtime_config(config: dict) -> list[str]:
    """Global çalışma ayarlarını config'ten yenile (Telegram bağlantısı gerekmez)."""

    notes: list[str] = []
    appstate.FILTER_INCLUDE = [normalize(x) for x in config.get("include_keywords") or []]
    appstate.FILTER_EXCLUDE = [normalize(x) for x in config.get("exclude_keywords") or []]
    appstate.FILTER_MODE = match_mode_of(config)
    filter_state = filter_state_of(config)
    appstate.FILTER_INCLUDE_ENABLED = filter_state["include"]
    appstate.FILTER_EXCLUDE_ENABLED = filter_state["exclude"]
    appstate.ADMIN_IDS = parse_admin_ids(config.get("admin_user_id"))
    appstate.DELIVERY_CHAIN = build_delivery_chain(config)
    try:
        appstate.MAX_MEDIA_MB = int(config.get("max_media_mb", 25))
    except (TypeError, ValueError):
        log.warning("max_media_mb sayı değil, 25 kabul edildi.")
        appstate.MAX_MEDIA_MB = 25
    appstate.LINK_APPENDIX_MODE = link_appendix_mode(config)
    appstate.LINK_KINDS = link_kinds_for(appstate.LINK_APPENDIX_MODE, bot=False)
    appstate.BOT_LINK_KINDS = link_kinds_for(appstate.LINK_APPENDIX_MODE, bot=True)
    appstate.MESSAGE_LINK_LINE = config_flag(config.get("message_link"), True)
    appstate.SOURCE_FOOTER = config_flag(config.get("source_footer"), True)
    appstate.NOTIFY_MEDIA = config_flag(config.get("notify_media"), True)
    appstate.SINGLE_MESSAGE = config_flag(config.get("single_message"), True)
    appstate.CLEAN_COMMANDS = config_flag(config.get("clean_commands"), True)
    # Varsayılan kapalı: davranış değişikliği yalnız config.json açtığında
    # üretimde görülür; mevcut testler/altın referans eski düzeni sınamaya devam eder.
    appstate.CLEAN_QUOTE_DUPLICATES = config_flag(config.get("clean_quote_duplicates"), False)
    appstate.SMART_HEADER = config_flag(config.get("smart_header"), False)
    appstate.FAST_NOTIFY = config_flag(config.get("fast_notify"), False)
    appstate.FAST_POLL = config_flag(config.get("fast_poll"), True)
    try:
        appstate.FAST_POLL_SECONDS = min(30.0, max(1.0, float(config.get("fast_poll_seconds", 3))))
    except (TypeError, ValueError):
        appstate.FAST_POLL_SECONDS = 3.0
    try:
        appstate.FAST_POLL_BACKFILL_MAX_AGE = min(120.0, max(1.0, float(config.get("fast_poll_backfill_minutes", 15)))) * 60.0
    except (TypeError, ValueError):
        appstate.FAST_POLL_BACKFILL_MAX_AGE = 900.0
    try:
        appstate.SLOW_REPORT_SECONDS = max(0.0, float(config.get("slow_report_seconds", 8)))
    except (TypeError, ValueError):
        appstate.SLOW_REPORT_SECONDS = 8.0
    appstate.DEDUP_ENABLED = config_flag(config.get("dedup_enabled"), True)
    try:
        appstate.DEDUP_WINDOW_SECONDS = max(1, min(72, int(config.get("dedup_window_hours", 12)))) * 3600
    except (TypeError, ValueError):
        log.warning("dedup_window_hours sayı değil, 12 saat kabul edildi.")
        appstate.DEDUP_WINDOW_SECONDS = 12 * 3600
    try:
        appstate.DEDUP_SCAN_LIMIT = max(0, min(100, int(config.get("dedup_scan_limit", 100))))
    except (TypeError, ValueError):
        log.warning("dedup_scan_limit sayı değil, 100 kabul edildi.")
        appstate.DEDUP_SCAN_LIMIT = 100
    # Bildirim token'ının tek kaynağı environment/Actions secret'ı olsun;
    # config.json içindeki eski notify_bot_token alanı artık kullanılmaz.
    token = str(os.getenv("NOTIFY_BOT_TOKEN", "") or "").strip()
    appstate.NOTIFY_BOT_TOKEN = "" if token.lower() in {"null", "none", "yok"} else token

    if not appstate.FILTER_INCLUDE_ENABLED:
        notes.append("🔓 Dahili kelime filtresi kapalı; anahtar kelimeler yok sayılıyor"
                     + (" (harici kelimeler yine engeller)." if appstate.FILTER_EXCLUDE_ENABLED
                        else " ve harici engelleme de kapalı: her mesaj iletilir."))
    if not appstate.FILTER_EXCLUDE_ENABLED:
        notes.append("🚫 Harici kelime filtresi kapalı; engelleme yapılmıyor.")
    return notes


def changed_groups(before: dict, after: dict) -> set[str]:
    """Liste akışında kaynak listesi değiştiyse kaynakları yeniden çöz."""
    return {"sources"} if before.get("source_chats") != after.get("source_chats") else set()


def _lookup_source_id(source_id_map: dict[str, Any], value: Any) -> Any | None:
    key = str(value).strip()
    if key in source_id_map:
        return source_id_map[key]
    if isinstance(value, str) and value.startswith("@"):
        alt = value.lstrip("@")
        if alt in source_id_map:
            return source_id_map[alt]
        low = alt.lower()
        for k, v in source_id_map.items():
            if isinstance(k, str) and k.lstrip("@").lower() == low:
                return v
    return None


def _is_transient_id_error(exc: Exception) -> bool:
    """Ağ/timeout/ServerError gibi geçici hatalar — ID korunmalı."""
    if isinstance(exc, (OSError, ConnectionError, TimeoutError, asyncio.TimeoutError, asyncio.CancelledError)):
        return True
    try:
        from telethon.errors.rpcbaseerrors import InvalidDCError, ServerError, TimedOutError

        if isinstance(exc, (ServerError, TimedOutError, InvalidDCError)):
            return True
    except Exception:
        pass
    return False


def _is_stale_id_error(exc: Exception) -> bool:
    """Kesin geçersiz/erişilemez ID — temizlenip username fallback yapılabilir."""
    if isinstance(exc, ValueError):
        return True
    # Telethon RPC kalıcı hataları: varlık yok / özel / geçersiz
    stale_candidates = (
        getattr(errors, "ChannelInvalidError", None),
        getattr(errors, "ChannelIdInvalidError", None),
        getattr(errors, "ChatInvalidError", None),
        getattr(errors, "ChatIdInvalidError", None),
        getattr(errors, "PeerIdInvalidError", None),
        getattr(errors, "UsernameInvalidError", None),
        getattr(errors, "UsernameNotOccupiedError", None),
        getattr(errors, "InviteHashInvalidError", None),
        getattr(errors, "InviteHashExpiredError", None),
        getattr(errors, "ChatForbiddenError", None),
    )
    stale_types = tuple(t for t in stale_candidates if t is not None)
    if stale_types and isinstance(exc, stale_types):
        return True
    return False


def _is_invite_link(value: Any) -> bool:
    return isinstance(value, str) and bool(_INVITE_LINK_RE.fullmatch(value.strip()))


async def _prefill_missing_ids_from_dialogs(client: TelegramClient, values: list[int | str],
                                            source_id_map: dict[str, Any]) -> int:
    """Eksik ID'leri tek bir get_dialogs çağrısıyla doldur — 47x ResolveUsername'i önler.

    Telegram `get_dialogs` bir RPC'de tüm üye olunan sohbetleri döndürür; username
    eşleştirmesi yerel yapılır, FloodWait tetiklenmez. Sadece @username kaynakları
    için geçerli, invite linkler ayrı kalır.
    """
    missing = [v for v in values if _lookup_source_id(source_id_map, v) is None and not _is_invite_link(v)]
    if not missing:
        return 0
    # Sadece username tabanlı eksikler varsa dene
    try:
        dialogs = await client.get_dialogs()
    except Exception as exc:
        log.warning("Dialog listesi alınamadı (%s: %s) — tek tek username çözümüne düşülüyor",
                    type(exc).__name__, exc)
        return 0
    # username -> peer_id haritası
    username_to_id: dict[str, int] = {}
    for dlg in dialogs or []:
        ent = getattr(dlg, "entity", None)
        if ent is None:
            # Bazı Telethon sürümlerinde dialog direkt entity olabilir
            ent = dlg
        uname = getattr(ent, "username", None)
        if not uname:
            continue
        try:
            pid = utils.get_peer_id(ent)
        except Exception:
            continue
        username_to_id[str(uname).strip().lower()] = pid

    filled = 0
    for v in missing:
        key = str(v).strip()
        # @xxx -> xxx lower
        lookup = key.lstrip("@").lower() if isinstance(v, str) else ""
        pid = username_to_id.get(lookup)
        if pid is not None and _lookup_source_id(source_id_map, v) is None:
            source_id_map[key] = pid
            filled += 1
            log.info("Kaynak ID dialoglardan bulundu: %s -> %s", v, pid)
    if filled:
        log.info("Dialoglardan %s/%s eksik kaynak ID'si dolduruldu", filled, len(missing))
    return filled


async def resolve_sources(client: TelegramClient, config: dict, quiet: bool = False) -> list[str]:
    """Kaynakları tek tek çöz; biri bozuksa diğerleri çalışmaya devam eder."""
    resolved: list[dict[str, Any]] = []
    failures: list[tuple[Any, Exception]] = []
    stored_ids = config.get("source_chat_ids")
    source_id_map: dict[str, Any] = dict(stored_ids) if isinstance(stored_ids, dict) else {}
    values = chat_values(config.get("source_chats"))
    # FloodWait'i tetiklemeden önce dialoglardan toplu ID doldurmayı dene
    if values:
        try:
            await _prefill_missing_ids_from_dialogs(client, values, source_id_map)
        except Exception as exc:
            log.warning("Dialog ön-doldurma atlandı: %s: %s", type(exc).__name__, exc)
    flood_wait_seconds: int | None = None
    flood_wait_exc: Exception | None = None
    for idx, value in enumerate(values):
        key = str(value).strip()
        cached_id = _lookup_source_id(source_id_map, value)
        if cached_id is not None:
            try:
                peer_id, name, entity = await resolve_chat(client, int(cached_id))
                resolved.append({
                    "id": peer_id,
                    "name": name,
                    "requested": value,
                    "username": getattr(entity, "username", None),
                    "joined": not bool(getattr(entity, "left", False)),
                })
                if str(source_id_map.get(key)) != str(peer_id):
                    source_id_map[key] = peer_id
                continue
            except errors.FloodWaitError as exc:
                flood_wait_seconds = max(flood_wait_seconds or 0, int(getattr(exc, "seconds", 0) or 0))
                flood_wait_exc = exc
                failures.append((value, exc))
                log.error("Kaynak çözülemedi (ID ile): %r -> %s: %s", value, type(exc).__name__, exc)
                continue
            except Exception as exc:  # noqa: BLE001 - geçici vs stale ayrımı
                if _is_transient_id_error(exc):
                    log.warning("Kaynak ID %s (%r) geçici hata: %s: %s — ID korunuyor, username denenmeyecek",
                                cached_id, value, type(exc).__name__, exc)
                    failures.append((value, exc))
                    continue
                if _is_stale_id_error(exc):
                    log.warning("Kaynak ID %s (%r) artık erişilemiyor: %s: %s -> kullanıcı adıyla yeniden denenecek",
                                cached_id, value, type(exc).__name__, exc)
                    # Stale ID'yi temizle — username başarılı olursa yeni ID eklenecek
                    for k in list(source_id_map.keys()):
                        if str(source_id_map[k]) == str(cached_id) and str(k).strip().lstrip("@").lower() == key.lstrip("@").lower():
                            source_id_map.pop(k, None)
                    if key in source_id_map and str(source_id_map.get(key)) == str(cached_id):
                        source_id_map.pop(key, None)
                else:
                    # Kesin stale değil ve transient değil — güvenli tarafta kal, ID'yi silme
                    log.warning("Kaynak ID %s (%r) geçici/bilinmeyen hata: %s: %s — ID korunuyor",
                                cached_id, value, type(exc).__name__, exc)
                    failures.append((value, exc))
                    continue
        # FloodWait aktifken yeni username denemesi yapma — ID'li kaynaklar zaten yukarıda denendi
        # Invite linkler ResolveUsername değil, ayrı limitte; park etme
        if flood_wait_seconds is not None and not _is_invite_link(value):
            failures.append((value, flood_wait_exc))
            log.warning("Kaynak atlandı (FloodWait park %ss): %r", flood_wait_seconds, value)
            continue
        try:
            peer_id, name, entity = await resolve_chat(client, value)
        except errors.FloodWaitError as exc:
            flood_wait_seconds = int(getattr(exc, "seconds", 0) or 0)
            flood_wait_exc = exc
            failures.append((value, exc))
            log.error("Kaynak çözülemedi: %r -> %s: %s", value, type(exc).__name__, exc)
            log.warning("FloodWait %ss: kalan bilinmeyen kaynaklar bu oturumda park edildi; "
                        "bilinen ID'lerle devam ediliyor (sonraki açılışta tekrar denenecek)",
                        flood_wait_seconds)
            continue
        except Exception as exc:  # noqa: BLE001 - tek kanal tüm bot'u düşürmemeli
            failures.append((value, exc))
            log.error("Kaynak çözülemedi: %r -> %s: %s", value, type(exc).__name__, exc)
            continue
        resolved.append({
            "id": peer_id,
            "name": name,
            "requested": value,
            "username": getattr(entity, "username", None),
            "joined": not bool(getattr(entity, "left", False)),
        })
        if source_id_map.get(key) != peer_id:
            source_id_map[key] = peer_id
    # Kalıcı haritayı yalnızca mevcut source_chats anahtarlarıyla sınırla (stale temizlenir)
    # Fakat FloodWait'te park edilenler için eski ID varsa koru — yoksa sonraki restart'ta tekrar username dener
    if source_id_map:
        allowed_keys = {str(v).strip() for v in values}
        pruned: dict[str, Any] = {}
        for k, v in source_id_map.items():
            if k in allowed_keys:
                pruned[k] = v
        # Park edilenlerin eski ID'sini kaybetmemek için pruned üzerine başarılıları ekle
        # Zaten pruned içinde var, resolved ile güncellendi; parked'ler pruned'da kalır
        config["source_chat_ids"] = pruned
        # Başarılıları kesin ekle (pruned sonrası override)
        for item in resolved:
            rk = str(item["requested"]).strip()
            if config["source_chat_ids"].get(rk) != item["id"]:
                config["source_chat_ids"][rk] = item["id"]
    elif resolved:
        config["source_chat_ids"] = {str(item["requested"]).strip(): item["id"] for item in resolved}
    appstate.SOURCES, appstate.SOURCE_FAILURES = resolved, failures
    appstate.SOURCE_IDS = {item["id"] for item in resolved}
    notes: list[str] = []
    for item in resolved:
        flag = "" if item["joined"] else "  <-- ÜYE DEĞİLSİN, mesaj gelmez!"
        if not quiet:
            log.info("Kaynak hazır: %-28s id=%-15s istenen=%s%s",
                     item["name"], item["id"], item["requested"], flag)
        elif not item["joined"]:
            notes.append(f"⚠️ {item['name']} kanalına üye değilsin; mesaj gelmez.")
    # Ayırt edici rapor: tanımlı / çözülen / park edilen
    defined = len(values)
    resolved_cnt = len(resolved)
    parked_cnt = sum(1 for _, exc in failures if isinstance(exc, errors.FloodWaitError))
    other_fail = len(failures) - parked_cnt
    if failures:
        for value, exc in failures:
            log.warning("Kaynak atlandı: %r (%s)", value, exc)
        if parked_cnt:
            wait_h = f" ({humanize(flood_wait_seconds)} bekle)" if flood_wait_seconds else ""
            notes.append(f"⚠️ {parked_cnt} kaynak FloodWait nedeniyle park edildi{wait_h}: "
                         + ", ".join(str(v) for v, e in failures if isinstance(e, errors.FloodWaitError))[:200])
        if other_fail:
            notes.append(f"⚠️ {other_fail} kaynak çözülemedi: "
                         + ", ".join(str(value) for value, exc in failures if not isinstance(exc, errors.FloodWaitError))[:200])
        if resolved_cnt < defined:
            # Kısmi başlatmayı normal gibi gösterme — açıkça raporla
            log.warning("⚠️ KISMI BAŞLATMA: %s/%s kaynak dinleniyor, %s park edildi, %s hatalı",
                        resolved_cnt, defined, parked_cnt, other_fail)
            notes.append(f"ℹ️ Tanımlı {defined}, çözülen {resolved_cnt}, park edilen {parked_cnt}, hatalı {other_fail}")
    else:
        if not quiet:
            log.info("Kaynaklar tamam: %s/%s çözüldü", resolved_cnt, defined)
    return notes


async def resolve_control(client: TelegramClient, config: dict,
                          quiet: bool = False) -> tuple[list[str], bool]:  # noqa: ARG001 - dış API uyumu
    """Kontrol sohbetlerini çöz; Kayıtlı Mesajlar her zaman açık kalır.

    Dönen ikinci değer: istenen sohbetlerden en az biri çözülebildi mi?
    `quiet` ileriye dönük sessiz mod için ayrılmıştır.
    """
    ids: set[int] = set()
    names: list[str] = []
    control_config = config.get("control_chat", "me")
    values = control_config if isinstance(control_config, (list, tuple)) else [control_config]
    for value in chat_values(values):
        try:
            peer_id, name, _ = await resolve_chat(client, value)
        except Exception as exc:  # noqa: BLE001
            log.error("control_chat çözülemedi: %r -> %s. Kayıtlı Mesajlar (me) kullanılıyor.", value, exc)
            continue
        ids.add(peer_id)
        names.append(f"{name} [{peer_id}]")
    if appstate.SELF_ID is not None:
        ids.add(appstate.SELF_ID)  # Kayıtlı Mesajlar her zaman kontrol edilebilir
    resolved = bool(names)
    if not names:
        names.append("me (Kayıtlı Mesajlar)")
    appstate.CONTROL_IDS, appstate.CONTROL_NAMES = ids, names
    # Not: açılışta çağıran taraf loglar; burada tekrar loglamıyoruz.
    return [f"🎛 Komut sohbeti: {', '.join(names)}"], resolved


async def resolve_destination(client: TelegramClient, config: dict, quiet: bool = False) -> list[str]:  # noqa: ARG001 - dış API uyumu
    """Hedef sohbeti çöz; olmazsa Kayıtlı Mesajlar'a düş.

    `quiet` ileriye dönük sessiz mod için ayrılmıştır.
    """
    value = parse_chat_value(config.get("destination", "me"))
    try:
        peer_id, name, _ = await resolve_chat(client, value)
        appstate.DESTINATION, appstate.DESTINATION_ID = peer_id, peer_id
        appstate.DESTINATION_LABEL = f"{name} [{peer_id}]"
    except Exception as exc:  # noqa: BLE001
        log.error("destination çözülemedi: %r -> %s. Kayıtlı Mesajlar'a düşülüyor.", value, exc)
        appstate.DESTINATION, appstate.DESTINATION_ID = "me", None
        appstate.DESTINATION_LABEL = "me (Kayıtlı Mesajlar)"
        return [f"⚠️ destination çözülemedi ({value}); Kayıtlı Mesajlar'a düşüldü."]
    return [f"🎯 Hedef: {appstate.DESTINATION_LABEL}"]


async def resolve_chat_groups(
    client: TelegramClient, config: dict, groups: Iterable[str], strict: bool = False,
) -> tuple[list[str], str | None]:
    """Sohbet gerektiren ayarları (yeniden) çöz.

    ``strict=True`` iken (Telegram'dan gelen ayar değişikliği) çözülemeyen
    destination/control_chat ölümcül sayılır ve değişiklik geri alınır; açılışta
    ise uyarı verip Kayıtlı Mesajlar'a düşmek daha güvenlidir.
    """
    notes: list[str] = []
    if "sources" in groups:
        notes += await resolve_sources(client, config)
        if not appstate.SOURCE_IDS:
            return notes, ("hiçbir kaynak çözülemedi; source_chats listesini ve "
                           "hesabın kanallara üyeliğini kontrol et")
    if "destination" in groups:
        notes += await resolve_destination(client, config)
        if strict and appstate.DESTINATION_ID is None:
            return notes, "destination çözülemedi; sohbet ID'sini/kullanıcı adını kontrol et"
    if "control" in groups:
        control_notes, resolved = await resolve_control(client, config)
        notes += control_notes
        if strict and not resolved:
            return notes, "control_chat çözülemedi; komutları kullanamazsın, değer geri alındı"
    return notes, None


def note_command_messages(chat_id: int, message_ids: Sequence[int]) -> list[int]:
    """Son komut alışverişinin mesaj ID'lerini kaydet; silinecek eskileri döndür.

    Yalnızca komut diyaloğuna ait mesajlar (kullanıcının komutu + botun yanıtı)
    buraya yazılır. İndirim bildirimleri bu kayda hiç girmediği için temizlikten
    etkilenmez; böylece yanlışlıkla fırsat mesajı silinmez.
    """
    chat = int(chat_id or 0)
    current = [int(item) for item in message_ids if item]
    previous = appstate.COMMAND_MESSAGES.get(chat, [])
    appstate.COMMAND_MESSAGES[chat] = current
    return [item for item in previous if item not in current]


def analyze_titles(titles: Iterable[str], *, keep_everything: bool = False) -> dict[str, Any]:
    """Başlıkları tara; kelime ve ilk kelime frekanslarını çıkar.

    Aynı başlık birden çok kanaldan gelmişse bir kez sayılır (tekrar
    birleştirme); böylece tek bir kampanya tüm istatistiği bozmaz.
    """
    words: Counter[str] = Counter()
    first_words: Counter[str] = Counter()
    seen: set[str] = set()
    counts = {"titles": 0, "unique": 0, "duplicates": 0, "filtered": 0}
    for title in titles:
        text = (title or "").strip()
        if not text:
            continue
        counts["titles"] += 1
        key = normalize(text)
        if key in seen:
            counts["duplicates"] += 1
            continue
        seen.add(key)
        counts["unique"] += 1
        # Sayılar ve işlevsiz kelimeler "elenen" sayılır: kapsam notu bunu gösterir.
        tokens = title_tokens(text, keep_everything=True)
        kept = content_tokens(tokens, keep_everything=keep_everything)
        counts["filtered"] += len(tokens) - len(kept)
        words.update(kept)
        if kept:
            first_words[kept[0]] += 1
    return {"counts": counts, "words": words, "first_words": first_words}


# ---------------------------------------------------------------------------
# Ana akış
# ---------------------------------------------------------------------------

async def auto_restart_scheduler(
    client: TelegramClient, destination: Any, pat: str, delay_seconds: int, *,
    prepare_restart: Callable[[], Any] | None = None,
    resume_after_failure: Callable[[], Any] | None = None,
) -> None:  # noqa: ARG001 - dış API uyumu
    """Eski Actions çalışmasını güvenli biçimde devret, sonra yenisini başlat.

    ``prepare_restart`` cursor'ı kalıcılaştırıp devam eden teslimatları
    boşaltmalıdır. Yeni workflow başlatılamazsa ``resume_after_failure`` eski
    sürecin kaynakları dinlemeye devam etmesini sağlar.
    """
    await asyncio.sleep(delay_seconds)
    log.info("Takipçi oturumu yenileniyor (%s doldu).", humanize(delay_seconds))
    if prepare_restart is not None:
        prepared = await prepare_restart()
        if not prepared:
            log.error("Otomatik yenileme iptal: kaynak cursor'ı güvenle kaydedilemedi.")
            return
    ok, detail = await dispatch_next_run(pat)
    if not ok:
        log.error("Otomatik yenileme başlatılamadı: %s", detail)
        if resume_after_failure is not None:
            await resume_after_failure()


async def heartbeat() -> None:
    """Actions log'unda 'hâlâ dinliyor mu?' sorusunu cevaplayan periyodik satır."""
    while True:
        await asyncio.sleep(1800)
        log.info(
            "Kalp atışı: %s çalışıyor | görülen=%d eşleşen=%d iletilen=%d"
            " | tarama aralığı=%.0f sn (son yoklama %.1f sn, FloodWait=%d)"
            " | Bot API 429=%d (retry bekleme %.1f sn, kilit bekleme %.1f sn)",
            humanize(time.time() - STARTED_AT), appstate.STATS["seen"], appstate.STATS["matched"], appstate.STATS["forwarded"],
            appstate.POLL_TELEMETRY["interval"], appstate.POLL_TELEMETRY["last_elapsed"], int(appstate.POLL_TELEMETRY["flood_count"]),
            gate_stats(appstate.NOTIFY_BOT_TOKEN)["rate_limit_count"],
            gate_stats(appstate.NOTIFY_BOT_TOKEN)["retry_wait_seconds"],
            gate_stats(appstate.NOTIFY_BOT_TOKEN)["lock_wait_seconds"],
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Telegram indirim mesajı filtresi")
    parser.add_argument(
        "--check", action="store_true",
        help="Telegram'a bağlanmadan secret/config kontrolü yap ve raporu yazdır",
    )
    parser.add_argument("--config", default=None, help="config.json yolu (varsayılan: $CONFIG_FILE veya config.json)")
    return parser


def run_check(config_path: str | None) -> int:
    try:
        config = load_config(config_path)
    except FileNotFoundError:
        print(f"✗ config.json bulunamadı (bakılan yol: {config_path or os.getenv('CONFIG_FILE', 'config.json')})", flush=True)
        return 1
    except json.JSONDecodeError as exc:
        print(f"✗ config.json geçerli bir JSON değil: {exc}", flush=True)
        return 1

    problems = check_environment(config)
    print_report(config, problems)
    return 1 if problems else 0


async def main(argv: Sequence[str] | None = None) -> int:
    # Filtre/link bayraklarını apply_runtime_config yazar; burada yalnızca
    # kimlik atanır (diğer adlar yalnızca okunur, `global` gerekmez).
    rt = Runtime(sys.modules[__name__])

    args = build_parser().parse_args(argv)
    if args.check:
        return run_check(args.config)

    config = load_config(args.config)
    problems = check_environment(config)
    print_report(config, problems)
    if problems:
        for problem in problems:
            log.error("Yapılandırma sorunu: %s", problem)
        raise SystemExit(1)

    # Config nesnesi çalışan filtre ve geçici liste taslakları için kullanılır.
    rt.store = ConfigStore(config_path(args.config), config)

    rt.gh_pat = os.getenv("GH_PAT", "").strip()
    auto_restart = bool(config.get("auto_restart", True))
    restart_minutes = max(1, int(os.getenv("RESTART_AFTER_MINUTES", "330")))

    # Telegram bağlantısı gerektirmeyen ayarlar (filtre, zincir, bayraklar).
    for note in apply_runtime_config(config):
        log.info("%s", note)

    rt.client = TelegramClient(
        StringSession(os.environ["SESSION_STRING"].strip()),
        int(os.environ["API_ID"].strip()),
        os.environ["API_HASH"].strip(),
    )

    await rt.client.connect()
    if not await rt.client.is_user_authorized():
        raise SystemExit(
            "SESSION_STRING geçersiz veya oturumun süresi dolmuş. "
            "generate_session.py ile yeni bir session üretip GitHub secret'ını güncelle."
        )

    me = await rt.client.get_me()
    appstate.SELF_ID = me.id
    log.info("Bağlanıldı: %s (id=%s)", display_name(me), me.id)

    if appstate.LINK_APPENDIX_MODE == "smart":
        log.info("Bağlantı ekleri: akıllı mod — ürün adresi '%s' yazısına gömülür "
                 "(buton, gizli hyperlink ya da görünür URL); alıntıdaki bağlantılar yedek kalır.",
                 PRODUCT_LINK_TEXT)
    elif appstate.LINK_APPENDIX_MODE == "all":
        log.info("Bağlantı ekleri: tüm gizli linkler iletinin sonuna yazılacak.")
    if appstate.MESSAGE_LINK_LINE:
        log.info("Orijinal mesajın t.me adresi kaynak grup adının içine gömülecek "
                 "(ayrı '🔗 %s' satırı yok).", MESSAGE_LINK_LABEL)
    if appstate.NOTIFY_BOT_TOKEN:
        log.info("Bildirim biçimi (basit düzen): en üstte ürün başlığı → '%s' → altında mesaj "
                 "OLDUĞU GİBİ → en altta kalın kaynak adı%s; mesajın adresi o adın içine "
                 "gömülüdür. Tekrarlı paylaşımlarda kaynak adı satırı kalkar, yerine çoklu "
                 "paylaşım notu yazılır (📌 N kere paylaşıldı: <gruplar>; her ad kendi "
                 "mesajına bağlı).",
                 PRODUCT_LINK_TEXT, " + medya" if appstate.NOTIFY_MEDIA else "")
    if appstate.CLEAN_COMMANDS:
        log.info("Komut temizliği açık: yeni komutta önceki komut/yanıt silinir, "
                 "bildirimlere dokunulmaz.")
    if appstate.DEDUP_ENABLED:
        log.info("Tekrar birleştirme açık: aynı ürün sorgusu %s boyunca tek mesajda toplanır, "
                 "açılışta hedefteki son %d ileti taranır.",
                 humanize(appstate.DEDUP_WINDOW_SECONDS), appstate.DEDUP_SCAN_LIMIT)
    else:
        log.info("Tekrar birleştirme kapalı: her eşleşme ayrı mesaj olarak iletilir.")

    # --- Kaynakları, kontrol sohbetini ve hedefi çöz.
    _orig_ids = dict(rt.store.config.get("source_chat_ids") or {}) if isinstance(rt.store.config.get("source_chat_ids"), dict) else {}
    notes, fatal = await resolve_chat_groups(
        rt.client, config, {"sources", "destination", "control"},
    )
    for note in notes:
        log.info("%s", note)
    if fatal:
        raise SystemExit(f"{fatal}; source_chats listesini ve hesap üyeliğini kontrol et.")
    # Kaynak ID'lerini kalıcılaştır — sonraki açılışlarda username FloodWait'ini önler
    _new_ids = rt.store.config.get("source_chat_ids")
    if isinstance(_new_ids, dict) and _new_ids != _orig_ids:
        try:
            await asyncio.to_thread(atomic_write_json, rt.store.path, rt.store.config)
            log.info("Kaynak ID'leri kalıcılaştırıldı: %s/%s", len(_new_ids), len(chat_values(rt.store.config.get("source_chats"))))
            # GitHub kalıcılığı: bir sonraki runner aynı ID'leri görsün; sessiz, hatası startup'ı öldürmez
            # Yalnızca gerçek repo checkout'unda push et — testteki /tmp dosyası repo dışı
            if push_token() and git_repo_root(rt.store.path.parent) is not None:
                _st, _dt = await asyncio.to_thread(commit_and_push, rt.store.path, "kaynak ID'leri güncellendi")
                log.info("Kaynak ID push: %s %s", _st, _dt)
        except Exception as exc:  # noqa: BLE001 - kalıcılık hatası bot'u durdurmasın
            log.warning("Kaynak ID'leri yazılamadı: %s: %s", type(exc).__name__, exc)
    not_joined = [item["name"] for item in appstate.SOURCES if not item["joined"]]
    if not_joined:
        log.warning(
            "Bu hesaptan üye olunmayan kaynaklar var, bunlardan mesaj GELMEZ: %s", ", ".join(not_joined)
        )
    if appstate.ADMIN_IDS:
        log.info("Komut kullanabilecek admin ID'leri: %s", sorted(appstate.ADMIN_IDS))
    elif len(appstate.CONTROL_IDS) > 1:
        log.warning("admin_user_id boş: grup komutları kimse tarafından kullanılamaz.")
    if not appstate.NOTIFY_BOT_TOKEN:
        log.warning(
            "NOTIFY_BOT_TOKEN secret/ortam değişkeni tanımlı değil: mesajları kendi hesabın "
            "gönderdiği için Telegram BİLDİRİM ÜRETMEZ. Sesli bildirim istiyorsan @BotFather'dan "
            "bir bot oluşturup gruba ekle ve token'ı NOTIFY_BOT_TOKEN secret'ında sakla."
        )


    rt.settings_lock = asyncio.Lock()
    rt.private_api = BotAPI(appstate.NOTIFY_BOT_TOKEN) if appstate.NOTIFY_BOT_TOKEN else None
    # Optional feature: failure to initialize must not prevent channel listening.
    rt.tracker = None
    if rt.private_api and config_flag(rt.store.config.get("private_control"), False):
        try:
            from product_tracker import ProductTracker

            def persist_products(path, data):
                # Contents API avoids dirtying the shared checkout while config
                # commits/rebases use it. The next runner checks out this snapshot.
                with GIT_WRITE_LOCK:
                    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
                    token = push_token()
                    repo = git_repo_root(rt.store.path.parent)
                    branch = (os.getenv("CONFIG_PUSH_BRANCH", "").strip()
                              or (current_branch(repo) if repo else "")
                              or os.getenv("GITHUB_REF_NAME", "").strip())
                    if not repository or not token or not branch:
                        return False, "GitHub kalıcı kayıt ortamı eksik."
                    url = f"https://api.github.com/repos/{repository}/contents/{path.name}"
                    ok, remote, detail = _github_api("GET", f"{url}?ref={urllib.parse.quote(branch, safe='')}", token)
                    if not ok or not isinstance(remote, dict) or not remote.get("sha"):
                        log.warning("Ürün takip dosyasının GitHub sürümü okunamadı: %s",
                                    detail or "beklenmeyen yanıt")
                        return False, "Takip dosyasının GitHub sürümü okunamadı."
                    payload = {
                        "message": "config: ürün takip güncellemesi",
                        "branch": branch, "sha": remote["sha"],
                        "content": base64.b64encode((json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()).decode(),
                    }
                    ok, _, detail = _github_api("PUT", url, token, payload)
                    if not ok:
                        log.warning("Ürün takip ayarı GitHub'a yazılamadı: %s", detail)
                    return ok, "Ürün takip ayarı GitHub'a yazıldı." if ok else "GitHub yazımı başarısız."

            rt.tracker = ProductTracker(
                rt.store.path.with_name("product_watches.json"), rt.private_api, persist_products,
                owner_id=appstate.SELF_ID,
                header_formatter=lambda cents, title: build_header_line(header_price_text(cents / 100), title),
            )
        except Exception:
            log.exception("Ürün takibi başlatılamadı; kanal dinleme devam ediyor.")


    rt.private_offers = PrivateOfferQueue(rt.private_api, functools.partial(prepare_private_offer, rt))


    # --- İletim yolları -----------------------------------------------------
    # Korumalı (noforwards) kanallarda forward ve copy patlar; o yüzden sırayla
    # denenir: forward -> copy -> medyayı indirip yeniden yükle -> sadece metin -> link.


    rt.senders = {
        "forward": functools.partial(send_forward, rt),
        "copy": functools.partial(send_copy, rt),
        "media": functools.partial(send_media, rt),
        "text": functools.partial(send_text_only, rt),
        "link": functools.partial(send_link_card, rt),
    }

    rt.account_send_lock = asyncio.Lock()


    # --- Tekrar birleştirme --------------------------------------------------
    # Aynı başlık aynı anda 3-5 kanaldan gelebilir; gönderen rezervasyon koyar,
    # diğerleri bekleyip tekrara düşer. Rozet aynı ürünün kilidiyle birleştirilir;
    # kayıp hedefte bu olayın kendisi yeni bildirim gönderebilir.

                # State may have changed just as the timeout fired; re-read it.


    # Tekrar önbelleğini sıfırla ve hedefin son iletileriyle ısıt: yeniden
    # başlamalarda yalnızca güvenle eşlenebilen geçmişi kullan (tek tarama).
    appstate.DEDUP_CACHE.clear()
    appstate.LIVE_SEEN.clear()
    appstate.POLL_DISPATCHED.clear()
    appstate.SOURCE_IN_FLIGHT.clear()
    await dedup_preload(rt)
    # Cursor: RAM sadece, bilinçli /restart’ta tek persist
    cursor_load_from_config(rt.store.config)
    # İlk kurulum: source_cursors yoksa bootstrap ilk poll’de top ile yapılır (patlama yok)
    if not rt.store.config.get("source_cursors"):
        log.info("Cursor: kalıcı kayıt yok — ilk poll’de sınırlı geriye tarama yapılacak; başlangıçtan önceki geçmiş bastırılacak.")

    # Handler'lara `chats=` VERMİYORUZ: Telethon o filtreyi ilk mesajda çözer ve
    # çözümleme hatası tüm update akışını öldürür. Filtreyi burada kendimiz yapıyoruz.
    @rt.client.on(events.NewMessage())
    async def on_control_message(event: events.NewMessage.Event) -> None:
        return await controlcmds.on_control_message(rt, event)

    @rt.client.on(events.NewMessage())
    async def on_new_message(event: events.NewMessage.Event) -> None:
        return await listener.on_new_message(rt, event)


    rt.poll_delivery_tasks: set[asyncio.Task] = set()


    # Özel komutlar ve fırsat kuyruğu ayrı görevlerde çalışır; açılış mesajı gönderilmez.
    private_tasks = []
    if rt.tracker:
        private_tasks.append(asyncio.create_task(rt.tracker.run()))
    if rt.private_api:
        private_tasks.append(asyncio.create_task(rt.private_offers.run()))
        if config_flag(rt.store.config.get("private_control"), False):
            private_tasks.append(asyncio.create_task(poll_private_commands(
                rt.private_api, lambda: appstate.ADMIN_IDS | {appstate.SELF_ID}, on_control_message,
                functools.partial(private_status_changed, rt),
            )))
            await asyncio.sleep(0)  # poller görevi ilk adımını atsın
            for _ in range(40):  # poller başlangıcına en fazla ~10 sn tanı
                if poll_health()["status"] not in {"off", "starting"}:
                    break
                await asyncio.sleep(0.25)


    if auto_restart and rt.gh_pat:
        asyncio.create_task(auto_restart_scheduler(
            rt.client, appstate.DESTINATION, rt.gh_pat, max(60, restart_minutes * 60),
            prepare_restart=functools.partial(prepare_scheduled_restart, rt),
            resume_after_failure=functools.partial(resume_after_scheduled_restart_failure, rt),
        ))
    elif auto_restart:
        log.warning("auto_restart açık ama GH_PAT yok; oturum Actions süresi bitince kapanacak.")
    asyncio.create_task(heartbeat())

    if appstate.FAST_POLL:
        private_tasks.append(asyncio.create_task(fast_poll_loop(rt)))

    log.info("Dinleniyor... (kaynak=%d, kontrol=%s, hedef=%s)", len(appstate.SOURCE_IDS), sorted(appstate.CONTROL_IDS), appstate.DESTINATION_LABEL)
    try:
        await rt.client.run_until_disconnected()
    finally:
        shutdown_tasks = [*private_tasks, *rt.poll_delivery_tasks]
        for task in shutdown_tasks:
            task.cancel()
        await asyncio.gather(*shutdown_tasks, return_exceptions=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:  # noqa: BLE001 - Actions log'unda net bir iz bırak
        log.exception("Takipçi beklenmeyen bir hatayla durdu.")
        raise
