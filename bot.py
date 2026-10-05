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
import io
import json
import logging
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Iterable, Sequence

from telethon import TelegramClient, errors, events, utils
from telethon.sessions import StringSession
from telethon.tl import types

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("telegram-filter")

STARTED_AT = time.time()
STATS = {
    "seen": 0,        # kaynaklarda görülen mesaj
    "matched": 0,     # anahtar kelimeye uyan mesaj
    "forwarded": 0,   # hedefe iletilen mesaj
    "failed": 0,      # iletimi başarısız mesaj
    "commands": 0,    # çalıştırılan komut
    "modes": {},      # hangi iletim yolu kaç kez işe yaradı
    "last_match": None,
    "last_match_source": None,
}

# Kaynak/kontrol listelerinin açılışta çözülmüş hâli (main() doldurur).
SOURCES: list[dict[str, Any]] = []
SOURCE_IDS: set[int] = set()
SOURCE_FAILURES: list[tuple[Any, Exception]] = []
CONTROL_IDS: set[int] = set()
CONTROL_NAMES: list[str] = []
DESTINATION_LABEL = "me"
DESTINATION_ID: int | None = None
NOTIFY_BOT_TOKEN = ""
DELIVERY_CHAIN: list[str] = []
MAX_MEDIA_MB = 0
SELF_ID: int | None = None
APPEND_LINKS = True   # (eski anahtar) gizli/buton bağlantılarını iletinin sonuna ekle
SOURCE_FOOTER = True  # bildirime "Fırsatı Gönderen: <kaynak>" satırı ekle
NOTIFY_MEDIA = True   # bildirim botu medyayı da göndersin
MESSAGE_LINK_LINE = True      # iletinin sonuna "🔗 Mesajı Gör: <t.me linki>" ekle
LINK_APPENDIX_MODE = "smart"  # smart | all | off (bkz. link_appendix_mode)

# Telegram sınırları (Bot API ve kullanıcı hesabı için ortak olanlar).
MESSAGE_LIMIT = 4096          # normal mesaj metni
CAPTION_LIMIT = 1024          # medya açıklaması
LINK_APPENDIX_LIMIT = 4       # ileti sonuna en fazla kaç gizli bağlantı yazılsın
FOOTER_LABEL = "Fırsatı Gönderen: "
MESSAGE_LINK_LABEL = "Mesajı Gör"
BOT_API_MEDIA_LIMIT_MB = {"photo": 10, "video": 50, "document": 50}

# Hangi link türleri metne yazılsın?
#   entity  = yazının altına gizlenmiş hyperlink (metinde tıklanabilir kalır)
#   button  = inline buton linki (kullanıcı hesabı buton gönderemez)
#   webpage = link önizlemesindeki hedef
LINK_KIND_GROUPS = {
    # "smart": taşınamayan linkler yazılır. Gizli hyperlink'ler mesajın içinde
    # tıklanabilir kaldığı için tekrar yazılmaz (kullanıcı isteği: "Fırsata Git:
    # https://..." satırı yerine mesajın linki yeter).
    "smart": ("button", "webpage"),
    "all": ("entity", "button", "webpage"),
    "off": (),
}
# Bildirim botunda butonlar gerçek inline buton olarak gider; orada yalnızca
# önizleme linki metne yazılır.
BOT_LINK_KIND_GROUPS = {
    "smart": ("webpage",),
    "all": ("entity", "button", "webpage"),
    "off": (),
}


# ---------------------------------------------------------------------------
# Yapılandırma
# ---------------------------------------------------------------------------

def load_config(path: str | os.PathLike[str] | None = None) -> dict:
    """config.json'ı oku; ortam değişkenleriyle (eski kurulumlar için) üzerine yaz."""
    file_path = Path(path or os.getenv("CONFIG_FILE", "config.json"))
    with file_path.open(encoding="utf-8") as handle:
        config = json.load(handle)

    for key in ("source_chats", "include_keywords", "exclude_keywords"):
        env_name = key.upper()
        if os.getenv(env_name):
            config[key] = [x.strip() for x in os.environ[env_name].split(",") if x.strip()]
    if os.getenv("DELIVERY_MODES"):
        config["delivery_modes"] = [x.strip() for x in os.environ["DELIVERY_MODES"].split(",") if x.strip()]
    if os.getenv("MAX_MEDIA_MB"):
        config["max_media_mb"] = os.environ["MAX_MEDIA_MB"]
    for key in ("destination", "match_mode", "copy_mode", "control_chat"):
        if os.getenv(key.upper()):
            config[key] = os.environ[key.upper()]
    if os.getenv("ADMIN_USER_ID"):
        config["admin_user_id"] = os.environ["ADMIN_USER_ID"]
    if os.getenv("AUTO_RESTART"):
        config["auto_restart"] = os.environ["AUTO_RESTART"].strip().lower() in {
            "1", "true", "yes", "evet", "on",
        }
    for key in ("append_links", "source_footer", "notify_media", "message_link"):
        env_value = os.getenv(key.upper())
        if env_value is not None and env_value.strip():
            config[key] = env_value
    if os.getenv("LINK_APPENDIX", "").strip():
        config["link_appendix"] = os.environ["LINK_APPENDIX"].strip()
    return config


def link_appendix_mode(config: dict) -> str:
    """``link_appendix`` ayarını ``smart`` / ``all`` / ``off`` olarak çöz.

    ``smart`` (varsayılan): yalnızca metne yazılmadığı sürece kaybolacak linkler
    eklenir (buton, link önizlemesi). Gizli hyperlink'ler zaten mesajın içinde
    tıklanabilir olduğu için tekrar yazılmaz.

    Eski ``append_links`` anahtarı hâlâ çalışır: ``true`` → ``all``, ``false`` → ``off``.
    """
    raw = config.get("link_appendix")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        if "append_links" in config:
            return "all" if config_flag(config.get("append_links"), True) else "off"
        return "smart"
    text = str(raw).strip().lower()
    if text in {"off", "none", "no", "hayir", "hayır", "false", "0", "kapali", "kapalı", "kapalı."}:
        return "off"
    if text in {"all", "hepsi", "tum", "tüm", "true", "1", "evet", "open", "açık", "acik"}:
        return "all"
    if text in {"smart", "akilli", "akıllı", "safe", "varsayilan", "varsayılan"}:
        return "smart"
    log.warning("Bilinmeyen link_appendix değeri %r; 'smart' kabul edildi (geçerli: smart, all, off).", raw)
    return "smart"


def link_kinds_for(mode: str, bot: bool = False) -> tuple[str, ...]:
    """Moda göre metne yazılacak link türlerini döndür."""
    table = BOT_LINK_KIND_GROUPS if bot else LINK_KIND_GROUPS
    return tuple(table.get(mode, table["smart"]))


def config_flag(value: Any, default: bool = True) -> bool:
    """``true``/``"evet"``/``1`` gibi değerleri bool'a çevir; boş/``null`` varsayılana döner."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"", "none", "null", "yok", "yoksa"}:
        return default
    return text in {"1", "true", "yes", "evet", "on", "açık", "acik", "aktif"}


def parse_chat_value(value: Any) -> int | str:
    """Tek bir chat değerini Telethon'ın beklediği tipe çevir.

    Telethon string bir değeri *kullanıcı adı* olarak çözmeye çalışır:
    ``get_input_entity("-5092968106")`` -> ``ValueError: Cannot find any entity``.
    Bu yüzden sayısal ID'ler (JSON'da tırnak içinde yazılsalar bile) int'e
    çevrilmelidir; ``me`` ve ``@kullaniciadi`` ise string olarak korunur.
    """
    if isinstance(value, bool):  # bool bir int alt sınıfıdır, ayrıca yakala
        raise ValueError(f"geçersiz chat değeri: {value!r}")
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        raise ValueError("boş chat değeri")
    if text.lower() in {"me", "self"}:
        return "me"
    try:
        return int(text)
    except ValueError:
        return text


def chat_values(values: Iterable[Any] | None) -> list[int | str]:
    """Chat listesini dönüştür; bozuk girdileri atla ama programı durdurma."""
    result: list[int | str] = []
    for value in values or []:
        try:
            result.append(parse_chat_value(value))
        except ValueError as exc:
            log.warning("config: %r atlandı (%s)", value, exc)
    return result


def parse_admin_ids(value: Any) -> set[int]:
    """``admin_user_id`` alanını esnek biçimde oku.

    Kabul edilen biçimler: ``null`` / ``123`` / ``"123"`` / ``"123,456"`` /
    ``[123, 456]`` / ``"none"``. Sayıya çevrilemeyen değerler loglanır ve
    yok sayılır (eski sürümde ``int("none")`` bot'u çökertiyordu).
    """
    if value is None:
        return set()
    items: Iterable[Any] = value if isinstance(value, (list, tuple, set)) else str(value).replace(";", ",").split(",")
    ids: set[int] = set()
    for item in items:
        text = str(item).strip()
        if not text or text.lower() in {"null", "none", "yok", "-"}:
            continue
        try:
            ids.add(int(text))
        except ValueError:
            log.warning("admin_user_id içindeki %r bir sayı değil, yok sayıldı.", text)
    return ids


# ---------------------------------------------------------------------------
# Anahtar kelime eşleştirme
# ---------------------------------------------------------------------------

# Python'un casefold()'u "İ" harfini "i" + birleşik nokta (U+0307) yapar, yani
# "İNDİRİM".casefold() == "i̇ndi̇ri̇m" olur ve "indirim" ile EŞLEŞMEZ.
# Türkçe büyük harfleri önce kendimiz indirgiyoruz.
_TURKISH_CASE_MAP = str.maketrans({"İ": "i", "I": "ı"})


def normalize(text: str | None) -> str:
    """Türkçe duyarlı küçük harf normalizasyonu."""
    return (text or "").translate(_TURKISH_CASE_MAP).casefold()


def matches(
    text: str | None,
    include: Sequence[str],
    exclude: Sequence[str],
    mode: str = "any",
) -> bool:
    """Mesaj metnini anahtar kelimelere göre değerlendir."""
    normalized = normalize(text)
    if exclude and any(word in normalized for word in exclude):
        return False
    if not include:
        return True
    found = [word in normalized for word in include]
    return all(found) if mode == "all" else any(found)


# ---------------------------------------------------------------------------
# Ortam / yapılandırma doğrulama
# ---------------------------------------------------------------------------

def check_environment(config: dict | None = None) -> list[str]:
    """Eksik/hatalı ayarları toplar. Boş liste = her şey hazır."""
    problems: list[str] = []

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

    if config is None:
        return problems

    sources = chat_values(config.get("source_chats"))
    if not sources:
        problems.append("config.json → source_chats boş. En az bir kanal/grup eklenmeli.")
    if str(config.get("match_mode", "any")).lower() not in {"any", "all"}:
        problems.append("config.json → match_mode yalnızca 'any' veya 'all' olabilir.")
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
    try:
        if int(config.get("max_media_mb", 25)) < 0:
            problems.append("config.json → max_media_mb negatif olamaz.")
    except (TypeError, ValueError):
        problems.append("config.json → max_media_mb bir sayı olmalı.")
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
          f"SESSION_STRING={mask('SESSION_STRING')} GH_PAT={mask('GH_PAT')}", flush=True)
    print(f"Kaynaklar          : {len(config.get('source_chats') or [])} adet", flush=True)
    print(f"Hedef              : {config.get('destination', 'me')}", flush=True)
    print(f"Kontrol sohbeti    : {config.get('control_chat', 'me')}", flush=True)
    print(f"Admin ID'leri      : {sorted(parse_admin_ids(config.get('admin_user_id'))) or 'tanımsız'}", flush=True)
    print(f"Anahtar kelimeler  : {config.get('include_keywords') or '(hepsi)'} "
          f"({config.get('match_mode', 'any')})", flush=True)
    print(f"Hariç kelimeler    : {config.get('exclude_keywords') or '(yok)'}", flush=True)
    print(f"İletim sırası      : {' → '.join(build_delivery_chain(config))}", flush=True)
    print(f"Medya sınırı       : {config.get('max_media_mb', 25)} MB", flush=True)
    mode = link_appendix_mode(config)
    mode_label = {"smart": "akıllı (buton/önizleme)", "all": "tüm gizli linkler", "off": "kapalı"}[mode]
    print(f"Bağlantı ekleri     : {mode_label} "
          f"| mesaj linki: {'açık' if config_flag(config.get('message_link')) else 'kapalı'} "
          f"| kaynak altbilgisi: {'açık' if config_flag(config.get('source_footer')) else 'kapalı'} "
          f"| bildirim medyası: {'açık' if config_flag(config.get('notify_media')) else 'kapalı'}", flush=True)
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


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------

def display_name(entity: Any) -> str:
    title = getattr(entity, "title", None)
    if title:
        return str(title)
    name = " ".join(x for x in (getattr(entity, "first_name", None), getattr(entity, "last_name", None)) if x)
    if name:
        return name
    username = getattr(entity, "username", None)
    return f"@{username}" if username else str(getattr(entity, "id", entity))


def humanize(seconds: float) -> str:
    seconds = int(max(0, seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}s {minutes}dk"
    if minutes:
        return f"{minutes}dk {secs}sn"
    return f"{secs}sn"


async def resolve_chat(client: TelegramClient, value: int | str) -> tuple[int, str, Any]:
    """Chat'i int ID'ye çevir; çözülemezse ValueError/TypeError fırlatır."""
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


async def _bot_api_request(
    token: str, method: str, *, payload: bytes, content_type: str, what: str,
) -> tuple[bool, str]:
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=payload,
        method="POST",
        headers={"Content-Type": content_type},
    )
    try:
        with await asyncio.to_thread(urllib.request.urlopen, request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8", "replace") or "{}")
        if body.get("ok"):
            return True, f"{what} gönderildi"
        return False, f"Bot API ok=false: {body.get('description')}"
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        log.error("Bildirim bot'u hata verdi: HTTP %s %s", exc.code, detail)
        return False, f"HTTP {exc.code}: {detail}"
    except Exception as exc:  # noqa: BLE001 - bildirim başarısızlığı akışı durdurmaz
        log.warning("Bildirim gönderilemedi: %s", type(exc).__name__)
        return False, f"{type(exc).__name__}"


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
    """
    if not token:
        return False, "notify_bot_token tanımlı değil."
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text[:MESSAGE_LIMIT]}
    if entities:
        payload["entities"] = json.dumps(entities)
    if keyboard:
        payload["reply_markup"] = json.dumps(keyboard)
    if link_preview is not None:
        payload["link_preview_options"] = json.dumps({"is_disabled": not link_preview})
    return await _bot_api_request(
        token, "sendMessage",
        payload=json.dumps(payload).encode(), content_type="application/json", what="bildirim",
    )


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
    """Bildirim botuyla fotoğraf/video/dosya gönder (medya da bildirim üretsin)."""
    if not token:
        return False, "notify_bot_token tanımlı değil."
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
    return await _bot_api_request(
        token, method, payload=body, content_type=content_type, what=f"bildirim medyası ({kind})",
    )


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
    if username:
        return f"https://t.me/{str(username).lstrip('@')}/{message_id}"
    chat_id = getattr(event, "chat_id", None)
    # t.me/c/<id>/<mesaj> yalnızca kanal/süpergrup için çalışır (-100... ile başlar).
    if isinstance(chat_id, int) and chat_id < -1000000000000:
        return f"https://t.me/c/{abs(chat_id) - 1000000000000}/{message_id}"
    return None


# ---------------------------------------------------------------------------
# Gizli bağlantılar (metin altına gizlenmiş linkler, buton linkleri)
# ---------------------------------------------------------------------------
#
# İndirim kanalları ürün linkini çoğu zaman "Fırsata Git" yazısının ALTINA
# gizler (MessageEntityTextUrl) ya da mesajın altındaki inline butona koyar.
# Bu bağlantılar düz metinde görünmediği için eski sürüm yalnızca "Fırsata Git"
# yazıyordu. Aşağıdaki yardımcılar bağlantıyı nerede olursa olsun bulur,
# iletiyle birlikte gönderir ve bildirimde tıklanabilir tutar.

URL_RE = re.compile(r"(?:https?://|t\.me/|telegram\.me/|www\.)[^\s<>\"')\]}]+", re.IGNORECASE)
_URL_TAIL_TRIM = ".,;:!?…\"'”’)]}>»"


def utf16_length(text: str) -> int:
    """Telegram offset/length değerleri UTF-16 kod birimi sayar (emoji 2 birim)."""
    return len((text or "").encode("utf-16-le")) // 2


def utf16_slice(text: str, offset: int, length: int) -> str:
    """UTF-16 offset'leriyle güvenli metin dilimi (emoji içeren mesajlarda düz dilim kayar)."""
    if not text or offset is None or length is None or offset < 0 or length <= 0:
        return ""
    data = text.encode("utf-16-le")
    return data[offset * 2:(offset + length) * 2].decode("utf-16-le", "replace")


def clean_url(url: Any) -> str:
    """Bağlantıyı kırp, sonundaki noktalama işaretlerini at, şema ekle."""
    text = str(url or "").strip()
    while text and text[-1] in _URL_TAIL_TRIM:
        text = text[:-1]
    lowered = text.lower()
    if lowered.startswith(("t.me/", "telegram.me/", "www.")):
        text = "https://" + text
    return text


def _as_message(obj: Any) -> Any:
    """Olay (Event) veya mesaj nesnesi verildiğinde mesajı döndür."""
    inner = getattr(obj, "message", None)
    if inner is not None and not isinstance(inner, str):
        return inner
    return obj


def message_text(obj: Any) -> str:
    """Mesajın ham metni (biçimlendirmeden bağımsız)."""
    message = _as_message(obj)
    text = getattr(message, "message", None)
    if isinstance(text, str):
        return text
    raw = getattr(obj, "raw_text", None)
    return raw if isinstance(raw, str) else ""


def message_entities(obj: Any) -> list[Any]:
    message = _as_message(obj)
    entities = getattr(message, "entities", None)
    return list(entities) if entities else []


def button_link(button: Any) -> tuple[str, str] | None:
    """Bir inline butonun (url, etiket) bilgisini döndür.

    Telethon iki farklı şema kullanabiliyor: eski sürümlerde ``KeyboardButtonUrl``
    doğrudan ``.url`` taşır; yeni sürümlerde ``KeyboardInlineButton`` içindeki
    ``.type`` (``InlineButtonTypeUrl``/``InlineButtonTypeWebView``) URL'yi tutar.
    """
    label = str(getattr(button, "text", "") or "").strip()
    url = getattr(button, "url", None)
    if not url:
        inner = getattr(button, "type", None)
        url = getattr(inner, "url", None)
        if not url:
            # "Kopyala" butonu bazen bağlantının kendisini kopyalatır.
            copy_text = getattr(inner, "copy_text", None)
            if isinstance(copy_text, str) and URL_RE.match(copy_text.strip()):
                url = copy_text
    if not url:
        return None
    cleaned = clean_url(url)
    if not cleaned:
        return None
    return cleaned, label


def extract_links(obj: Any) -> list[dict[str, str]]:
    """Mesajdaki tüm bağlantıları bul: gizli hyperlink, buton, önizleme, düz URL.

    Her kayıt ``{"url", "label", "kind"}`` sözlüğüdür; ``kind`` şunlardan biri:
    ``entity`` (yazının altına gizlenmiş), ``button`` (inline buton),
    ``webpage`` (link önizlemesi), ``text`` (metinde açıkça görünen).
    """
    message = _as_message(obj)
    text = message_text(obj)
    found: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(url: Any, label: Any = None, kind: str = "text") -> None:
        cleaned = clean_url(url)
        if not cleaned or not URL_RE.match(cleaned):
            return
        key = cleaned.rstrip("/").lower()
        if key in seen:
            return
        seen.add(key)
        found.append({"url": cleaned, "label": str(label or "").strip() or None, "kind": kind})

    for entity in message_entities(message):
        url = getattr(entity, "url", None)
        if url:
            label = utf16_slice(text, getattr(entity, "offset", 0), getattr(entity, "length", 0))
            add(url, label, "entity")

    markup = getattr(message, "reply_markup", None)
    for row in getattr(markup, "rows", None) or []:
        for button in getattr(row, "buttons", None) or []:
            info = button_link(button)
            if info:
                add(info[0], info[1], "button")

    webpage = getattr(getattr(message, "media", None), "webpage", None)
    if webpage is not None:
        add(getattr(webpage, "url", None), getattr(webpage, "title", None), "webpage")

    for url in URL_RE.findall(text):
        add(url, None, "text")

    return found


def visible_urls(text: str) -> set[str]:
    """Metinde gözle görülen bağlantılar (bunları tekrar yazmaya gerek yok)."""
    return {clean_url(url).rstrip("/").lower() for url in URL_RE.findall(text or "")}


def missing_links(
    obj: Any,
    limit: int = LINK_APPENDIX_LIMIT,
    kinds: Sequence[str] | None = ("button", "webpage"),
) -> list[dict[str, str]]:
    """Metinde görünmeyen bağlantılar: gizli hyperlink, buton, link önizlemesi.

    ``kinds`` verilirse yalnızca o türler döner (bkz. ``LINK_KIND_GROUPS``).
    """
    visible = visible_urls(message_text(obj))
    allowed = None if kinds is None else set(kinds)
    result: list[dict[str, str]] = []
    for item in extract_links(obj):
        if item["kind"] == "text":
            continue
        if allowed is not None and item["kind"] not in allowed:
            continue
        if item["url"].rstrip("/").lower() in visible:
            continue
        result.append(item)
        if len(result) >= limit:
            break
    return result


def build_link_appendix(obj: Any, kinds: Sequence[str] | None = ("button", "webpage")) -> str:
    """Gizli bağlantıları iletinin sonuna eklenecek metne çevir."""
    lines: list[str] = []
    for item in missing_links(obj, kinds=kinds):
        label = (item.get("label") or "").strip()
        if label and label.lower() not in item["url"].lower():
            lines.append(f"🔗 {label[:40]}: {item['url']}")
        else:
            lines.append(f"🔗 {item['url']}")
    return "\n".join(lines)


def build_inline_keyboard(obj: Any) -> dict | None:
    """Buton linklerini Bot API inline klavyesi olarak yeniden kur."""
    message = _as_message(obj)
    markup = getattr(message, "reply_markup", None)
    rows: list[list[dict[str, str]]] = []
    for row in getattr(markup, "rows", None) or []:
        buttons: list[dict[str, str]] = []
        for button in getattr(row, "buttons", None) or []:
            info = button_link(button)
            if not info:
                continue
            url, label = info
            buttons.append({"text": (label or url)[:64], "url": url})
        if buttons:
            rows.append(buttons)
    return {"inline_keyboard": rows} if rows else None


def compose_message(
    obj: Any,
    *,
    limit: int = MESSAGE_LIMIT,
    link_kinds: Sequence[str] | None = ("button", "webpage"),
    message_link: str | None = None,
    message_link_label: str = MESSAGE_LINK_LABEL,
    footer_label: str | None = None,
    footer_name: str | None = None,
    footer_url: str | None = None,
) -> dict[str, Any]:
    """İletilecek metni kur: gövde + bağlantı ekleri + mesaj linki + kaynak altbilgisi.

    Sıra: ``gövde`` → ``🔗 <link>`` satırları → ``🔗 Mesajı Gör: <t.me>`` →
    ``Fırsatı Gönderen: <kaynak>``. Ek satırlar kısa ama kritiktir; bu yüzden
    önce onlara yer ayrılır, gövde gerekiyorsa kırpılır (fotoğraf açıklaması
    1024 karakterle sınırlıdır). Sığmazsa önce link listesi, sonra mesaj linki
    düşer; nihai güvence olan ``Mesajı Gör`` satırı en sona bırakılır.

    Dönen sözlükte ``body`` (kırpılmış olabilecek gövde), ``footer_offset``
    (UTF-16 offset) ve ``source_url`` bulunur; entity'ler bunlara göre kurulur.
    """
    body = message_text(obj)
    appendix_text = build_link_appendix(obj, kinds=link_kinds) if link_kinds else ""
    source_line = f"🔗 {message_link_label}: {message_link}" if message_link else ""
    footer_text = f"{footer_label}{footer_name}" if (footer_label and footer_name) else ""

    sep = "\n\n"
    appendix_block = f"{sep}{appendix_text}" if appendix_text else ""
    source_block = f"{sep}{source_line}" if source_line else ""
    footer_block = f"{sep}{footer_text}" if footer_text else ""
    if len(appendix_block) + len(source_block) + len(footer_block) >= limit:
        appendix_block, appendix_text = "", ""
    if len(source_block) + len(footer_block) >= limit:
        source_block, source_line = "", ""

    reserved = len(appendix_block) + len(source_block) + len(footer_block)
    room = max(1, limit - reserved)
    if len(body) > room:
        body = body[: max(0, room - 1)].rstrip() + "…"

    text = body + appendix_block + source_block
    footer_offset = -1
    if footer_text:
        text += sep
        footer_offset = utf16_length(text)
        text += footer_text

    return {
        "text": text,
        "body": body,
        "appendix": appendix_text,
        "source_line": source_line,
        "source_url": message_link if source_line else None,
        "footer_text": footer_text,
        "footer_offset": footer_offset,
        "footer_length": utf16_length(footer_name) if footer_text else 0,
        "footer_url": footer_url if footer_text and footer_url else None,
    }


def entities_for_text(obj: Any, body: str) -> list[Any]:
    """Gövde kırpıldıysa sınırı aşan entity'leri at (Telegram hata verir)."""
    limit = utf16_length(body)
    kept: list[Any] = []
    for entity in message_entities(obj):
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


def footer_entity(composed: dict[str, Any], message_link: str | None) -> list[dict[str, Any]]:
    """Altbilgideki kaynak adını tıklanabilir yap: ad t.me mesaj linkini gizler."""
    if not composed.get("footer_url") or composed.get("footer_offset", -1) < 0 or not message_link:
        return []
    return [{
        "type": "text_link",
        "offset": composed["footer_offset"],
        "length": composed["footer_length"],
        "url": message_link,
    }]


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


# ---------------------------------------------------------------------------
# Komutlar
# ---------------------------------------------------------------------------

HELP_TEXT = (
    "Komutlar:\n"
    "/status (/durum)  – çalışma durumu ve sayaçlar\n"
    "/test  (/deneme)  – hedefe deneme mesajı gönderir\n"
    "/source (/kaynak) – izlenen kanallar ve çözümleme durumu\n"
    "/id               – bu sohbetin ve senin ID'ni gösterir (config için)\n"
    "/restart (/yenile) – yeni GitHub Actions çalışması başlatır\n"
    "/help  (/yardim)  – bu mesaj"
)


def build_status_text(config: dict) -> str:
    last = STATS["last_match"]
    last_line = "henüz eşleşme yok"
    if last:
        last_line = f"{humanize(time.time() - last)} önce ({STATS['last_match_source']})"
    keywords = config.get("include_keywords") or "(hepsi)"
    return (
        "✅ Takipçi aktif\n"
        f"• Çalışma süresi: {humanize(time.time() - STARTED_AT)}\n"
        f"• Dinlenen kaynak: {len(SOURCE_IDS)}/{len(config.get('source_chats') or [])}"
        + (f" (çözülemeyen {len(SOURCE_FAILURES)})" if SOURCE_FAILURES else "")
        + "\n"
        f"• Görülen: {STATS['seen']} | Eşleşen: {STATS['matched']} | İletilen: {STATS['forwarded']}"
        + (f" | Hatalı: {STATS['failed']}" if STATS["failed"] else "")
        + "\n"
        f"• Son eşleşme: {last_line}\n"
        f"• Hedef: {DESTINATION_LABEL}\n"
        f"• Kontrol sohbeti: {', '.join(CONTROL_NAMES) or 'me'}\n"
        f"• Anahtar kelimeler: {', '.join(keywords) if isinstance(keywords, list) else keywords} "
        f"({config.get('match_mode', 'any')})\n"
        f"• İletim sırası: {' → '.join(DELIVERY_CHAIN) or 'yok'}"
        + (f" | kullanılan: {', '.join(f'{k}×{v}' for k, v in STATS['modes'].items())}"
           if STATS["modes"] else "")
    )


def build_source_text() -> str:
    lines = [f"İzlenen {len(SOURCES)} kaynak:"]
    lines += [f"• {item['name']}  [{item['id']}]" + ("" if item["joined"] else "  ⚠️ ÜYE DEĞİLSİN")
              for item in SOURCES]
    for value, exc in SOURCE_FAILURES:
        lines.append(f"✗ {value} çözülemedi: {exc}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Ana akış
# ---------------------------------------------------------------------------

async def auto_restart_scheduler(client: TelegramClient, destination: Any, pat: str, delay_seconds: int) -> None:
    """Actions job bitmeden bir sonraki job'u zincirleme başlatır."""
    await asyncio.sleep(delay_seconds)
    try:
        await client.send_message(
            destination,
            f"⏳ Takipçi oturumu yenileniyor ({humanize(delay_seconds)} doldu); dinleme birazdan devam edecek.",
        )
    except Exception:  # noqa: BLE001
        log.exception("Yenileme bildirimi gönderilemedi.")
    await dispatch_next_run(pat)


async def heartbeat() -> None:
    """Actions log'unda 'hâlâ dinliyor mu?' sorusunu cevaplayan periyodik satır."""
    while True:
        await asyncio.sleep(1800)
        log.info(
            "Kalp atışı: %s çalışıyor | görülen=%d eşleşen=%d iletilen=%d",
            humanize(time.time() - STARTED_AT), STATS["seen"], STATS["matched"], STATS["forwarded"],
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
    global SOURCES, SOURCE_IDS, SOURCE_FAILURES, CONTROL_IDS, CONTROL_NAMES
    global DESTINATION_LABEL, DESTINATION_ID, NOTIFY_BOT_TOKEN
    global DELIVERY_CHAIN, MAX_MEDIA_MB, SELF_ID
    global APPEND_LINKS, SOURCE_FOOTER, NOTIFY_MEDIA
    global MESSAGE_LINK_LINE, LINK_APPENDIX_MODE, LINK_KINDS, BOT_LINK_KINDS

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

    include_keywords = [normalize(x) for x in config.get("include_keywords", [])]
    exclude_keywords = [normalize(x) for x in config.get("exclude_keywords", [])]
    match_mode = str(config.get("match_mode", "any")).lower()
    DELIVERY_CHAIN = build_delivery_chain(config)
    try:
        MAX_MEDIA_MB = int(config.get("max_media_mb", 25))
    except (TypeError, ValueError):
        log.warning("max_media_mb sayı değil, 25 kabul edildi.")
        MAX_MEDIA_MB = 25
    admin_ids = parse_admin_ids(config.get("admin_user_id"))
    gh_pat = os.getenv("GH_PAT", "").strip()
    auto_restart = bool(config.get("auto_restart", True))
    restart_minutes = max(1, int(os.getenv("RESTART_AFTER_MINUTES", "330")))
    notify_on_start = bool(config.get("notify_on_start", True))
    NOTIFY_BOT_TOKEN = str(config.get("notify_bot_token") or os.getenv("NOTIFY_BOT_TOKEN", "") or "").strip()
    if NOTIFY_BOT_TOKEN.lower() in {"null", "none", "yok"}:
        NOTIFY_BOT_TOKEN = ""
    APPEND_LINKS = link_appendix_mode(config) != "off"
    LINK_APPENDIX_MODE = link_appendix_mode(config)
    LINK_KINDS = link_kinds_for(LINK_APPENDIX_MODE, bot=False)
    BOT_LINK_KINDS = link_kinds_for(LINK_APPENDIX_MODE, bot=True)
    MESSAGE_LINK_LINE = config_flag(config.get("message_link"), True)
    SOURCE_FOOTER = config_flag(config.get("source_footer"), True)
    NOTIFY_MEDIA = config_flag(config.get("notify_media"), True)
    if LINK_APPENDIX_MODE == "smart":
        log.info("Bağlantı ekleri: akıllı mod — gizli hyperlink'ler mesajda tıklanabilir kalır, "
                 "yalnızca buton/önizleme linkleri metne yazılır.")
    elif LINK_APPENDIX_MODE == "all":
        log.info("Bağlantı ekleri: tüm gizli linkler iletinin sonuna yazılacak.")
    if MESSAGE_LINK_LINE:
        log.info("Her iletinin sonuna '🔗 %s: <t.me mesaj linki>' satırı eklenecek.", MESSAGE_LINK_LABEL)
    if NOTIFY_BOT_TOKEN:
        log.info("Bildirim biçimi: mesajın kopyası + %s (t.me linki gizli)%s",
                 FOOTER_LABEL.strip() + " <kaynak>" if SOURCE_FOOTER else "altbilgi yok",
                 " + medya" if NOTIFY_MEDIA else "")

    client = TelegramClient(
        StringSession(os.environ["SESSION_STRING"].strip()),
        int(os.environ["API_ID"].strip()),
        os.environ["API_HASH"].strip(),
    )

    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit(
            "SESSION_STRING geçersiz veya oturumun süresi dolmuş. "
            "generate_session.py ile yeni bir session üretip GitHub secret'ını güncelle."
        )

    me = await client.get_me()
    SELF_ID = me.id
    log.info("Bağlanıldı: %s (id=%s)", display_name(me), me.id)

    # --- Kaynakları tek tek çöz: bir tanesi bozuksa diğerleri çalışmaya devam eder.
    for value in chat_values(config.get("source_chats")):
        try:
            peer_id, name, entity = await resolve_chat(client, value)
        except Exception as exc:  # noqa: BLE001 - tek kanal tüm bot'u düşürmemeli
            SOURCE_FAILURES.append((value, exc))
            log.error("Kaynak çözülemedi: %r -> %s: %s", value, type(exc).__name__, exc)
            continue
        SOURCES.append({
            "id": peer_id,
            "name": name,
            "requested": value,
            "username": getattr(entity, "username", None),
            "joined": not bool(getattr(entity, "left", False)),
        })
        SOURCE_IDS.add(peer_id)

    for item in SOURCES:
        flag = "" if item["joined"] else "  <-- ÜYE DEĞİLSİN, mesaj gelmez!"
        log.info("Kaynak hazır: %-28s id=%-15s istenen=%s%s", item["name"], item["id"], item["requested"], flag)
    for value, exc in SOURCE_FAILURES:
        log.warning("Kaynak atlandı: %r (%s)", value, exc)
    if not SOURCE_IDS:
        raise SystemExit("Hiçbir kaynak çözülemedi; source_chats listesini ve hesap üyeliğini kontrol et.")
    not_joined = [item["name"] for item in SOURCES if not item["joined"]]
    if not_joined:
        log.warning(
            "Bu hesaptan üye olunmayan kaynaklar var, bunlardan mesaj GELMEZ: %s", ", ".join(not_joined)
        )

    # --- Kontrol sohbeti ve hedef.
    control_config = config.get("control_chat", "me")
    control_values = control_config if isinstance(control_config, (list, tuple)) else [control_config]
    for value in chat_values(control_values):
        try:
            peer_id, name, _ = await resolve_chat(client, value)
        except Exception as exc:  # noqa: BLE001
            log.error("control_chat çözülemedi: %r -> %s. Kayıtlı Mesajlar (me) kullanılıyor.", value, exc)
            continue
        CONTROL_IDS.add(peer_id)
        CONTROL_NAMES.append(f"{name} [{peer_id}]")
    if SELF_ID is not None:
        CONTROL_IDS.add(SELF_ID)  # Kayıtlı Mesajlar her zaman kontrol edilebilir
    if not CONTROL_NAMES:
        CONTROL_NAMES.append("me (Kayıtlı Mesajlar)")
    log.info("Komutlar şu sohbetlerde dinleniyor: %s", ", ".join(CONTROL_NAMES))
    if admin_ids:
        log.info("Komut kullanabilecek admin ID'leri: %s", sorted(admin_ids))
    elif len(CONTROL_IDS) > 1:
        log.warning("admin_user_id boş: grup komutları kimse tarafından kullanılamaz.")

    destination_value = parse_chat_value(config.get("destination", "me"))
    try:
        peer_id, name, _ = await resolve_chat(client, destination_value)
        destination: Any = peer_id
        DESTINATION_LABEL = f"{name} [{peer_id}]"
        DESTINATION_ID = peer_id
    except Exception as exc:  # noqa: BLE001
        log.error("destination çözülemedi: %r -> %s. Kayıtlı Mesajlar'a düşülüyor.", destination_value, exc)
        destination = "me"
        DESTINATION_LABEL = "me (Kayıtlı Mesajlar)"
        DESTINATION_ID = None
    log.info("Hedef: %s", DESTINATION_LABEL)
    if not NOTIFY_BOT_TOKEN:
        log.warning(
            "notify_bot_token tanımlı değil: mesajları kendi hesabın gönderdiği için Telegram "
            "BİLDİRİM ÜRETMEZ (Kayıtlı Mesajlar'da da grupta da). Sesli bildirim istiyorsan "
            "@BotFather'dan bir bot oluşturup gruba ekle ve notify_bot_token alanına token'ı yaz."
        )

    def is_control_event(event: events.NewMessage.Event) -> bool:
        """Komutu yalnızca kontrol sohbetinden VE admin'den kabul et."""
        if event.chat_id not in CONTROL_IDS:
            return False
        if event.chat_id == SELF_ID:  # Kayıtlı Mesajlar'a yazan zaten hesabın sahibi
            return True
        return event.sender_id in admin_ids

    # --- İletim yolları -----------------------------------------------------
    # Korumalı (noforwards) kanallarda forward ve copy patlar; o yüzden sırayla
    # denenir: forward -> copy -> medyayı indirip yeniden yükle -> sadece metin -> link.

    def source_of(event: events.NewMessage.Event) -> dict[str, Any] | None:
        """Kaynak kaydını bul (kaynak adı, kullanıcı adı, t.me linki için)."""
        return next((item for item in SOURCES if item["id"] == event.chat_id), None)

    def offer_link(event: events.NewMessage.Event) -> str | None:
        return build_message_link(event, source_of(event)) if MESSAGE_LINK_LINE else None

    async def send_forward(event: events.NewMessage.Event) -> None:
        await client.forward_messages(destination, event.message, from_peer=event.chat_id)

    async def send_copy(event: events.NewMessage.Event) -> None:
        """Mesajı biçimiyle birlikte yeniden gönder.

        Gizli hyperlink'ler entity olarak korunur. Kaynaktaki ``reply_markup``
        kullanıcı hesabından gönderilemediği için (inline klavyeler yalnızca
        botlara açıktır) buton linkleri metne yazılır; ayrıca en alta
        ``🔗 Mesajı Gör: <t.me>`` satırı eklenir.
        """
        message = event.message
        media = getattr(message, "media", None)
        is_webpage = isinstance(media, types.MessageMediaWebPage)
        link = offer_link(event)
        if media and not is_webpage:
            composed = compose_message(event, limit=CAPTION_LIMIT - 24,
                                       link_kinds=LINK_KINDS, message_link=link)
            await client.send_file(
                destination,
                media,
                caption=composed["text"],
                formatting_entities=entities_for_text(event, composed["body"]),
                force_document=False,
            )
            return
        composed = compose_message(event, limit=MESSAGE_LIMIT - 100,
                                   link_kinds=LINK_KINDS, message_link=link)
        await client.send_message(
            destination,
            composed["text"],
            formatting_entities=entities_for_text(event, composed["body"]),
            link_preview=True,
        )

    async def send_media(event: events.NewMessage.Event) -> None:
        """Medyayı indirip hedefe SIFIRDAN yükle (forward kısıtını atlar).

        Önemli: indirilen veriyi düz ``bytes`` olarak ``send_file``a vermek
        fotoğrafın "unnamed" adlı bir belgeye dönüşmesine yol açar (Telethon
        dosya adını ``getattr(file, 'name', 'unnamed')`` ile tahmin eder).
        Bu yüzden uzantılı isimli bir akış kullanılır.
        """
        message = event.message
        if not getattr(message, "media", None) or isinstance(message.media, types.MessageMediaWebPage):
            raise ValueError("mesajda indirilebilir medya yok")
        size = getattr(getattr(message, "file", None), "size", None) or 0
        if MAX_MEDIA_MB and size and size > MAX_MEDIA_MB * 1024 * 1024:
            raise ValueError(f"medya {size // (1024 * 1024)} MB, sınır {MAX_MEDIA_MB} MB")
        data = await client.download_media(message, bytes)
        if not data:
            raise ValueError("medya indirilemedi")
        composed = compose_message(event, limit=CAPTION_LIMIT - 24,
                                   link_kinds=LINK_KINDS, message_link=offer_link(event))
        await client.send_file(
            destination,
            media_buffer(data, media_upload_name(message)),
            caption=composed["text"] or None,
            formatting_entities=entities_for_text(event, composed["body"]),
            attributes=reupload_attributes(message),
            force_document=False,
        )

    async def send_text_only(event: events.NewMessage.Event) -> None:
        # Gövdeye ek olarak gizli linkler (varsa) ve "Mesajı Gör" satırı eklenir;
        # ürün linki bir şekilde kaçsa bile tek dokunuşla fırsata ulaşılır.
        composed = compose_message(event, limit=MESSAGE_LIMIT - 400,
                                   link_kinds=LINK_KINDS, message_link=offer_link(event))
        if not composed["body"].strip() and not composed["appendix"]:
            raise ValueError("mesajda metin yok")
        has_media = bool(getattr(event.message, "media", None))
        note = "\n\n⚠️ Kaynak medyayı korumalı işaretlediği için medya iletilemedi." if has_media else ""
        await client.send_message(
            destination,
            composed["text"] + note,
            formatting_entities=entities_for_text(event, composed["body"]),
            link_preview=True,
        )

    async def send_link_card(event: events.NewMessage.Event) -> None:
        """Son çare: kaynak adı + t.me bağlantısı. Ekranda görülebilir tek şey budur."""
        link = build_message_link(event, source_of(event))
        if not link:
            raise ValueError("bu sohbet türü için t.me bağlantısı üretilemiyor")
        composed = compose_message(event, limit=2000, link_kinds=LINK_KINDS, message_link=link)
        if composed["body"].strip():
            body = composed["text"]
        else:
            body = f"🔗 {STATS['last_match_source'] or 'kaynak'} kanalındaki mesaj:\n{link}"
            if composed["appendix"]:
                body += f"\n{composed['appendix']}"
            body += "\n(medya korumalı olduğu için iletilemedi, bağlantıdan açabilirsin)"
        await client.send_message(destination, body[:MESSAGE_LIMIT], link_preview=True)

    SENDERS = {
        "forward": send_forward,
        "copy": send_copy,
        "media": send_media,
        "text": send_text_only,
        "link": send_link_card,
    }

    async def notify_offer(event: events.NewMessage.Event, source_name: str) -> None:
        """Bildirim botuyla fırsatın kopyasını at.

        Tasarım: mesajın kendisi (biçimi ve gizli linkleriyle) → altına
        "🔗 <gizli linkler>" (varsa) → en alta "Fırsatı Gönderen: <kaynak>".
        Kaynak adı, orijinal mesajın t.me bağlantısını gizli hyperlink olarak
        taşır; ürün linki kaçırılsa bile tek dokunuşla mesaja ulaşılır.
        """
        if not NOTIFY_BOT_TOKEN or DESTINATION_ID is None:
            return
        message_link = offer_link(event)
        keyboard = build_inline_keyboard(event)
        footer_name = source_name if SOURCE_FOOTER else None

        descriptor = bot_media_descriptor(event) if NOTIFY_MEDIA else None
        if descriptor is not None:
            limit_mb = min(BOT_API_MEDIA_LIMIT_MB.get(descriptor["kind"], 50),
                           MAX_MEDIA_MB or BOT_API_MEDIA_LIMIT_MB.get(descriptor["kind"], 50))
            if descriptor["size"] and descriptor["size"] > limit_mb * 1024 * 1024:
                log.info("Bildirim medyası %s MB, sınır %s MB → metin olarak gönderilecek.",
                         descriptor["size"] // (1024 * 1024), limit_mb)
                descriptor = None

        if descriptor is not None:
            composed = compose_message(
                event, limit=CAPTION_LIMIT - 24, link_kinds=BOT_LINK_KINDS, message_link=message_link,
                footer_label=FOOTER_LABEL, footer_name=footer_name, footer_url=message_link,
            )
            entities = bot_api_entities(event, composed["body"]) + footer_entity(composed, message_link)
            try:
                data = await client.download_media(event.message, bytes)
            except Exception as exc:  # noqa: BLE001 - medya inmezse bildirim yine gitsin
                log.warning("Bildirim medyası indirilemedi (%s): %s", type(exc).__name__, exc)
                data = None
            if data:
                ok, detail = await send_bot_media(
                    NOTIFY_BOT_TOKEN, DESTINATION_ID,
                    kind=descriptor["kind"], filename=descriptor["filename"],
                    mime_type=descriptor["mime"], data=data,
                    caption=composed["text"], entities=entities, keyboard=keyboard,
                )
                if ok:
                    log.info("Bildirim gönderildi (medya: %s, kaynak: %s).", descriptor["kind"], source_name)
                    return
                log.warning("Bildirim medyası gönderilemedi (%s) → metne düşülüyor.", detail)

        composed = compose_message(
            event, limit=MESSAGE_LIMIT - 200, link_kinds=BOT_LINK_KINDS, message_link=message_link,
            footer_label=FOOTER_LABEL, footer_name=footer_name, footer_url=message_link,
        )
        entities = bot_api_entities(event, composed["body"]) + footer_entity(composed, message_link)
        text = composed["text"] or f"🔔 Yeni fırsat – {source_name}"
        ok, detail = await send_bot_ping(
            NOTIFY_BOT_TOKEN, DESTINATION_ID, text,
            entities=entities, keyboard=keyboard,
        )
        if ok:
            log.info("Bildirim gönderildi (metin, kaynak: %s).", source_name)
        else:
            log.warning("Bildirim gönderilemedi: %s", detail)

    async def deliver(event: events.NewMessage.Event, source_name: str) -> tuple[bool, str]:
        """Sırayla iletim yollarını dene; ilk başarılı olanı kullan."""
        last_error = "denenmedi"
        for mode in DELIVERY_CHAIN:
            try:
                await SENDERS[mode](event)
            except errors.FloodWaitError as exc:
                STATS["failed"] += 1
                log.warning("FloodWait (%s sn) – %s bekleniyor, mesaj atlandı.", exc.seconds, mode)
                await asyncio.sleep(min(exc.seconds, 30))
                return False, f"floodwait:{exc.seconds}"
            except Exception as exc:  # noqa: BLE001 - bir yol patlarsa sıradakini dene
                last_error = f"{type(exc).__name__}: {exc}"
                log.info("İletim yolu '%s' başarısız (%s) → sıradaki deneniyor.", mode, last_error)
                continue
            STATS["forwarded"] += 1
            STATS["modes"][mode] = STATS["modes"].get(mode, 0) + 1
            if mode not in ("forward", "copy"):
                log.info("Mesaj '%s' yedeğiyle iletildi (kaynak: %s).", mode, source_name)
            await notify_offer(event, source_name)
            return True, mode

        STATS["failed"] += 1
        log.error("Hiçbir iletim yolu çalışmadı (kaynak=%s, mesaj=%s). Son hata: %s",
                  source_name, getattr(event, "id", "?"), last_error)
        return False, last_error

    # Handler'lara `chats=` VERMİYORUZ: Telethon o filtreyi ilk mesajda çözer ve
    # çözümleme hatası tüm update akışını öldürür. Filtreyi burada kendimiz yapıyoruz.
    @client.on(events.NewMessage())
    async def on_control_message(event: events.NewMessage.Event) -> None:
        if not is_control_event(event):
            return
        raw = (event.raw_text or "").strip()
        if not raw.startswith("/"):
            return
        command = normalize(raw.split()[0])
        STATS["commands"] += 1
        log.info("Komut alındı: %s (chat=%s, sender=%s)", command, event.chat_id, event.sender_id)

        if command in {"/status", "/durum"}:
            await event.reply(build_status_text(config))
        elif command in {"/test", "/deneme"}:
            text = (
                f"🧪 Deneme mesajı – {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"Hedef: {DESTINATION_LABEL}\n"
                f"Kaynaklar: {len(SOURCE_IDS)} | Görülen: {STATS['seen']} | Eşleşen: {STATS['matched']}"
            )
            try:
                await client.send_message(destination, text)
                reply = f"✅ Deneme mesajı gönderildi: {DESTINATION_LABEL}"
            except Exception as exc:  # noqa: BLE001
                await event.reply(f"❌ Deneme mesajı gönderilemedi: {type(exc).__name__}: {exc}")
                return
            if NOTIFY_BOT_TOKEN and DESTINATION_ID is not None:
                ok, detail = await send_bot_ping(
                    NOTIFY_BOT_TOKEN, DESTINATION_ID,
                    "🔔 Bildirim denemesi\n\nFırsatı Gönderen: (bildirimlerde buraya kaynak adı gelir)",
                )
                reply += "\n" + ("🔔 Bot bildirimi de gönderildi (telefonuna düşmeli)." if ok
                                 else f"⚠️ Bot bildirimi gönderilemedi: {detail}")
                reply += ("\nGizli linkler ve buton linkleri de bildirime eklenir; "
                          "medya varsa bot onu da gönderir.")
            else:
                reply += ("\n⚠️ notify_bot_token yok: mesajı kendi hesabın gönderdiği için "
                          "bildirim almazsın. @BotFather'dan bot oluşturup gruba ekle.")
            await event.reply(reply)
        elif command in {"/source", "/sources", "/kaynak", "/kaynaklar"}:
            await event.reply(build_source_text())
        elif command == "/id":
            try:
                chat = await event.get_chat()
                chat_kind = type(chat).__name__
            except Exception:  # noqa: BLE001 - sohbet cache'te olmayabilir
                chat_kind = "bilinmiyor"
            await event.reply(
                f"🆔 Bu sohbetin ID'si: {event.chat_id}\n"
                f"• Tür: {chat_kind}\n"
                f"• Senin kullanıcı ID'n: {event.sender_id}\n"
                f"config.json için:\n"
                f'  "control_chat": {event.chat_id},\n'
                f'  "admin_user_id": {event.sender_id}'
            )
        elif command in {"/restart", "/yenile", "/yeniden"}:
            ok, message = await dispatch_next_run(gh_pat)
            await event.reply(("🔄 " if ok else "⚠️ ") + message)
        elif command in {"/help", "/yardim", "/yardım"}:
            await event.reply(HELP_TEXT)
        else:
            await event.reply(f"Bilinmeyen komut: {raw}\n\n{HELP_TEXT}")

    @client.on(events.NewMessage())
    async def on_new_message(event: events.NewMessage.Event) -> None:
        if event.chat_id not in SOURCE_IDS or event.chat_id in CONTROL_IDS:
            return
        if DESTINATION_ID is not None and event.chat_id == DESTINATION_ID:
            return  # hedefe kendi gönderdiğimiz mesajı tekrar iletmeyelim
        STATS["seen"] += 1
        text = event.raw_text or ""
        if not matches(text, include_keywords, exclude_keywords, match_mode):
            log.debug("Eşleşmedi (chat=%s id=%s): %.80s", event.chat_id, event.id, text)
            return

        STATS["matched"] += 1
        STATS["last_match"] = time.time()
        source_name = next((item["name"] for item in SOURCES if item["id"] == event.chat_id), str(event.chat_id))
        STATS["last_match_source"] = source_name
        log.info("Eşleşti: %s / mesaj %s / %.80s", source_name, event.id, text)
        await deliver(event, source_name)

    if notify_on_start:
        try:
            await client.send_message(
                destination,
                f"🟢 Takipçi başladı: {len(SOURCE_IDS)} kaynak dinleniyor"
                + (f", {len(SOURCE_FAILURES)} kaynak çözülemedi" if SOURCE_FAILURES else "")
                + f".\nHedef: {DESTINATION_LABEL}",
            )
        except Exception:  # noqa: BLE001
            log.exception("Başlangıç bildirimi gönderilemedi.")

    if auto_restart and gh_pat:
        asyncio.create_task(auto_restart_scheduler(client, destination, gh_pat, max(60, restart_minutes * 60)))
    elif auto_restart:
        log.warning("auto_restart açık ama GH_PAT yok; oturum Actions süresi bitince kapanacak.")
    asyncio.create_task(heartbeat())

    log.info("Dinleniyor... (kaynak=%d, kontrol=%s, hedef=%s)", len(SOURCE_IDS), sorted(CONTROL_IDS), DESTINATION_LABEL)
    await client.run_until_disconnected()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:  # noqa: BLE001 - Actions log'unda net bir iz bırak
        log.exception("Takipçi beklenmeyen bir hatayla durdu.")
        raise
