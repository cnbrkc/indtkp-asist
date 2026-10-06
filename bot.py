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
import io
import json
import logging
import mimetypes
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
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
DESTINATION: Any = "me"       # hedefin çözülmüş hâli (int ID veya "me")
DESTINATION_LABEL = "me"
DESTINATION_ID: int | None = None
NOTIFY_BOT_TOKEN = ""
DELIVERY_CHAIN: list[str] = []
MAX_MEDIA_MB = 0
SELF_ID: int | None = None
# Telegram'dan canlı değiştirilen filtre/komut durumları (apply_runtime_config yazar).
FILTER_INCLUDE: list[str] = []
FILTER_EXCLUDE: list[str] = []
FILTER_MODE = "any"
ADMIN_IDS: set[int] = set()
CONFIG_STORE: Any = None      # ConfigStore (main doldurur)
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

def config_path(path: str | os.PathLike[str] | None = None) -> Path:
    """config.json'ın yolu (--config > CONFIG_FILE > config.json)."""
    return Path(path or os.getenv("CONFIG_FILE", "config.json"))


# Ortam değişkeninin üzerine yazdığı config alanları (yalnızca bilgi amaçlı).
# Telegram'dan kaydederken bu değerler dosyaya da işlenir; kullanıcı görebilsin.
ENV_OVERRIDES: set[str] = set()


def load_config(path: str | os.PathLike[str] | None = None) -> dict:
    """config.json'ı oku; ortam değişkenleriyle (eski kurulumlar için) üzerine yaz."""
    file_path = config_path(path)
    with file_path.open(encoding="utf-8") as handle:
        config = json.load(handle)
    original = copy.deepcopy(config)

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
    ENV_OVERRIDES.clear()
    ENV_OVERRIDES.update(
        key for key in set(original) | set(config) if original.get(key) != config.get(key)
    )
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


# Eşleştirme modları.
#   any         = include_keywords'ten en az biri geçsin
#   all         = include_keywords'ün hepsi geçsin
#   forward_all = filtre KAPALI; kaynaklardaki her mesaj iletilir
#                 (exclude_keywords bu modda da engellemeye devam eder)
MATCH_MODES = ("any", "all", "forward_all")

# Kullanıcının yazabileceği alternatif yazımlar → kurallı mod adı.
MATCH_MODE_ALIASES = {
    # --- any: kelimelerden biri yeterli
    "any": "any", "or": "any", "veya": "any", "herhangi": "any",
    "herhangibiri": "any", "biri": "any", "birisi": "any", "yada": "any",
    # --- all: kelimelerin hepsi zorunlu
    "all": "all", "and": "all", "ve": "all", "hepsi": "all", "tumu": "all",
    "tümü": "all", "tumu_var": "all", "hepside": "all", "hepsi_olsun": "all",
    # --- forward_all: tüm mesajları ilet
    "forward_all": "forward_all", "forwardall": "forward_all",
    "all_messages": "forward_all", "allmessages": "forward_all",
    "allmsgs": "forward_all", "all_msgs": "forward_all",
    "tum_mesajlar": "forward_all", "tüm_mesajlar": "forward_all",
    "tummesajlar": "forward_all", "tum_mesaj": "forward_all",
    "hepsini_gonder": "forward_all", "hepsini_gönder": "forward_all",
    "hepsini_ilet": "forward_all", "hepsiniyolla": "forward_all",
    "tumunu_gonder": "forward_all", "tümünü_gönder": "forward_all",
    "hepsi_gelsin": "forward_all", "hersini_gonder": "forward_all",
    "passthrough": "forward_all", "nofilter": "forward_all",
    "filtresiz": "forward_all", "filtre_yok": "forward_all",
    "filtreyok": "forward_all", "filtreleme": "forward_all",
    "*": "forward_all", "hepsi_gonder": "forward_all",
}


def choice_keys(text: Any) -> tuple[str, ...]:
    """Seçim değerinin olası yazımları: boşluk/tire/büyük-küçük farkını yok sayar."""
    base = normalize(str(text or "")).strip()
    return (base.replace(" ", "_").replace("-", "_"),
            base.replace(" ", "").replace("-", ""))


def canonical_match_mode(value: Any) -> str | None:
    """``match_mode`` yazımını kurallı hâle getir; tanınmıyorsa ``None``."""
    for key in choice_keys(value):
        mode = MATCH_MODE_ALIASES.get(key)
        if mode:
            return mode
    return None


def match_mode_of(config: dict) -> str:
    """config'ten kurallı eşleştirme modunu oku (varsayılan ``any``)."""
    return canonical_match_mode(config.get("match_mode")) or "any"


def matches(
    text: str | None,
    include: Sequence[str],
    exclude: Sequence[str],
    mode: str = "any",
) -> bool:
    """Mesaj metnini anahtar kelimelere göre değerlendir.

    ``forward_all`` modunda ``include`` tamamen yok sayılır; ``exclude`` her
    zaman önce uygulanır (istenmeyen içerik hiçbir modda geçmez).
    """
    normalized = normalize(text)
    if exclude and any(word in normalized for word in exclude):
        return False
    resolved = canonical_match_mode(mode) or "any"
    if resolved == "forward_all":
        return True
    if not include:
        return True
    found = [word in normalized for word in include]
    return all(found) if resolved == "all" else any(found)


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
    if canonical_match_mode(config.get("match_mode", "any")) is None:
        problems.append(
            "config.json → match_mode yalnızca 'any', 'all' veya 'forward_all' olabilir "
            "(forward_all = tüm mesajları ilet)."
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
    filter_mode = match_mode_of(config)
    filter_label = {
        "any": "any (biri yeterli)",
        "all": "all (hepsi zorunlu)",
        "forward_all": "forward_all (TÜM mesajlar iletilir, filtre kapalı)",
    }[filter_mode]
    print(f"Anahtar kelimeler  : {config.get('include_keywords') or '(hepsi)'} ({filter_label})", flush=True)
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
    "/ayar             – ayar menüsü: gruplar (/filtre, /iletim…) ve işlemler\n"
    "                    (/ekle, /sil, /set, /goster). Alan adı ezberlemek gerekmez.\n"
    "/help  (/yardim)  – bu mesaj"
)


def build_status_text(config: dict) -> str:
    last = STATS["last_match"]
    last_line = "henüz eşleşme yok"
    if last:
        last_line = f"{humanize(time.time() - last)} önce ({STATS['last_match_source']})"
    keywords = config.get("include_keywords") or "(hepsi)"
    mode = match_mode_of(config)
    if mode == "forward_all":
        filter_line = "🔓 Tüm mesajlar iletiliyor (filtre kapalı)"
    else:
        filter_line = (f"• Anahtar kelimeler: "
                       f"{', '.join(keywords) if isinstance(keywords, list) else keywords} ({mode})")
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
        f"{filter_line}\n"
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
# Ayar yönetimi (Telegram'dan canlı düzenleme)
# ---------------------------------------------------------------------------
#
# Amaç: config.json'ı Telegram kontrol sohbetinden değiştirebilmek.
#   • Değişiklik ÇALIŞAN botta anında aktif olur (restart beklemez).
#   • Değişiklik config.json'a atomik yazılır ve mümkse git ile repo'ya
#     işlenir; böylece GitHub Actions yeniden başlasa da kaybolmaz.
#
# Risk yönetimi: her alan için tip/aralık doğrulaması var; bilinmeyen alan
# yazılamaz. Sohbet gerektiren alanlar (source_chats, destination,
# control_chat) değişince Telegram'da yeniden çözülür, çözülemezse değişiklik
# geri alınır.

def build_main_menu_text() -> str:
    """/ayar çıktısı: grupları ve işlem komutlarını özetler."""
    lines = [
        "⚙️ Ayar menüsü",
        "",
        "Değişiklik anında aktif olur ve config.json'a yazılır "
        "(repo'ya da işlenirse kalıcı olur).",
        "",
        "Ayar grupları — birine dokun, o gruptaki ayarları ve komutları gör:",
        "",
    ]
    for group in SETTING_GROUPS:
        lines.append(f"{group['icon']} /{group['key']} – {group['desc']}")
    lines += [
        "",
        "İşlem komutları — alan adı ezberlemen gerekmez, bot sorar:",
        "",
        " /ekle    – bir listeye değer ekle (hangi liste? menü çıkar)",
        " /sil     – bir listeden değer çıkar",
        " /set     – bir ayarın değerini değiştir",
        " /goster  – bir ayarı göster (/ayar_goster hepsi için)",
        " /kaydet  – dosyayı tekrar yaz / repo'ya gönder",
        " /iptal   – bekleyen işlemi iptal et / son değişikliği geri al",
        "",
        "Nasıl kullanılıyor? Komuta dokun → alanı seç → değeri yaz.",
        " Örnek: /mod → 3                    (match_mode = forward_all)",
        " Örnek: /kelime_ekle çay            (tek mesajda biter)",
        " Örnek: /ekle → 1 → çay, kahve      (menüden seçerek)",
        "",
        "İpucu: her ayarın kendi komutu da var — /kelime_ekle, /kanal_sil,",
        "/hedef, /token … Bir grubun tüm komutlarını görmek için /filtre, /iletim…",
    ]
    return "\n".join(lines)

# Telegram'dan değiştirilebilecek alanlar.
#   kind     : doğrulama biçimi
#   resolve  : değişince Telegram'da yeniden çözülmesi gereken grup
#   help     : /ayar_goster çıktısında gösterilen açıklama
SETTING_FIELDS: dict[str, dict[str, Any]] = {
    "source_chats": {
        "kind": "chat_list", "resolve": "sources",
        "help": "Dinlenen kanal/grup listesi (@kullanici veya -100... ID)",
    },
    "destination": {
        "kind": "chat", "resolve": "destination",
        "help": "Fırsatların iletildiği sohbet",
    },
    "control_chat": {
        "kind": "chat", "resolve": "control",
        "help": "Komutların dinlendiği sohbet (me = Kayıtlı Mesajlar)",
    },
    "admin_user_id": {
        "kind": "id_list",
        "help": "Komut kullanabilecek kullanıcı ID'leri",
    },
    "include_keywords": {
        "kind": "str_list", "fold": True,
        "help": "Aranan kelimeler (match_mode any/all ile kullanılır)",
    },
    "exclude_keywords": {
        "kind": "str_list", "fold": True,
        "help": "Görüldüğünde iletilmeyecek kelimeler (her modda engeller)",
    },
    "match_mode": {
        "kind": "enum", "choices": MATCH_MODES, "aliases": MATCH_MODE_ALIASES,
        "help": "any (biri yeterli) | all (hepsi zorunlu) | forward_all (tüm mesajlar)",
    },
    "delivery_modes": {
        "kind": "enum_list", "choices": DELIVERY_MODES,
        "help": "İletim yollarının deneme sırası",
    },
    "copy_mode": {
        "kind": "enum", "choices": ("forward", "copy"),
        "help": "delivery_modes boşsa tercih edilen ilk yol",
    },
    "max_media_mb": {
        "kind": "int", "min": 0, "max": 2000,
        "help": "Yeniden yüklenecek medyanın üst sınırı (MB)",
    },
    "link_appendix": {
        "kind": "enum", "choices": ("smart", "all", "off"),
        "help": "Gizli linklerin iletiye eklenme biçimi",
    },
    "message_link": {
        "kind": "bool",
        "help": "İletinin sonuna 'Mesajı Gör' bağlantısı",
    },
    "source_footer": {
        "kind": "bool",
        "help": "Bildirimde 'Fırsatı Gönderen: <kaynak>' satırı",
    },
    "notify_media": {
        "kind": "bool",
        "help": "Bildirim botu fotoğraf/videoyu da göndersin",
    },
    "notify_on_start": {
        "kind": "bool",
        "help": "Açılışta bilgi mesajı gönder (bir sonraki açılışta geçerli)",
    },
    "auto_restart": {
        "kind": "bool",
        "help": "Actions zinciriyle otomatik yenileme (bir sonraki açılışta geçerli)",
    },
    "notify_bot_token": {
        "kind": "secret",
        "help": "Bildirim botu token'ı (boş = bildirim kapalı)",
    },
}

# Kullanıcıların yazması muhtemel kısa/Türkçe alan adları.
FIELD_ALIASES = {
    "kaynak": "source_chats", "kaynaklar": "source_chats", "kanallar": "source_chats",
    "kanal": "source_chats", "source": "source_chats", "sources": "source_chats",
    "hedef": "destination", "hedefsohbet": "destination", "hedef_sohbet": "destination",
    "kontrol": "control_chat", "kontrolsohbeti": "control_chat", "control": "control_chat",
    "admin": "admin_user_id", "adminid": "admin_user_id", "adminler": "admin_user_id",
    "kelime": "include_keywords", "kelimeler": "include_keywords",
    "anahtar": "include_keywords", "anahtarkelime": "include_keywords",
    "aranan": "include_keywords", "include": "include_keywords",
    "dahil": "include_keywords", "dahil_liste": "include_keywords",
    "dahil_listesi": "include_keywords", "dahil_kelime": "include_keywords",
    "dahilkelime": "include_keywords", "dahil_kelimeler": "include_keywords",
    "haric": "exclude_keywords", "hariç": "exclude_keywords",
    "haric_liste": "exclude_keywords", "haric_listesi": "exclude_keywords",
    "haric_kelime": "exclude_keywords", "harickkelime": "exclude_keywords",
    "haric_kelimeler": "exclude_keywords",
    "yasak": "exclude_keywords", "yasakli": "exclude_keywords", "yasaklı": "exclude_keywords",
    "exclude": "exclude_keywords", "engelli": "exclude_keywords", "engellenen": "exclude_keywords",
    "mod": "match_mode", "modu": "match_mode", "eslesme": "match_mode", "eşleşme": "match_mode",
    "filtre": "match_mode", "filtremodu": "match_mode", "matchmode": "match_mode",
    "iletim": "delivery_modes", "yollar": "delivery_modes", "iletimyollari": "delivery_modes",
    "medya": "max_media_mb", "medyasınırı": "max_media_mb", "medyasiniri": "max_media_mb",
    "boyut": "max_media_mb", "maxmedya": "max_media_mb",
    "link": "link_appendix", "linkeki": "link_appendix", "baglanti": "link_appendix",
    "mesajlinki": "message_link", "mesaj_linki": "message_link",
    "altbilgi": "source_footer", "kaynakadi": "source_footer",
    "bildirimmedya": "notify_media", "bildirim_medya": "notify_media",
    "acilisbildirimi": "notify_on_start", "yenileme": "auto_restart",
    "otomatikyenileme": "auto_restart", "otoyenileme": "auto_restart",
    "token": "notify_bot_token", "bottoken": "notify_bot_token", "bot_token": "notify_bot_token",
}

LIST_KINDS = {"str_list", "chat_list", "id_list", "enum_list"}

BOOL_TRUE = {"1", "true", "yes", "evet", "on", "açık", "acik", "aktif", "var",
             "a", "enable", "enabled", "open"}
BOOL_FALSE = {"0", "false", "no", "hayir", "hayır", "off", "kapali", "kapalı",
              "pasif", "yok", "kapat", "disable", "disabled", "none", "null", "k"}

def _expand_commands(names: set[str]) -> frozenset[str]:
    """Türkçe büyük harf farkını da kabul et: /SİL yazımı '/sıl' olarak gelir."""
    expanded = set(names)
    for name in names:
        expanded.add(name.replace("i", "ı"))
    return frozenset(expanded)


# Komut adları → işlev. normalize() edilmiş hâlde karşılaştırılır.
CMD_SETTINGS_MENU = _expand_commands({"/ayar", "/ayarlar", "/settings", "/konfig", "/configur"})
CMD_SETTINGS_SHOW = _expand_commands({"/ayar_goster", "/ayargoster", "/ayargor", "/ayar_gör",
                                      "/goster", "/göster", "/gör", "/gor", "/ayarlarim", "/ayarlarım"})
CMD_SETTINGS_SET = _expand_commands({"/ayar_set", "/ayarset", "/set", "/ayarla", "/degistir", "/değiştir"})
CMD_SETTINGS_ADD = _expand_commands({"/ayar_ekle", "/ayarekle", "/ekle", "/add"})
CMD_SETTINGS_REMOVE = _expand_commands({"/ayar_sil", "/ayarsil", "/sil", "/cikar", "/çıkar", "/remove", "/delete"})
CMD_SETTINGS_SAVE = _expand_commands({"/ayar_kaydet", "/ayarkaydet", "/kaydet", "/save"})
CMD_SETTINGS_REVERT = _expand_commands({"/ayar_iptal", "/ayariptal", "/iptal", "/geri", "/geri_al", "/undo"})
SETTINGS_COMMANDS = (CMD_SETTINGS_MENU | CMD_SETTINGS_SHOW | CMD_SETTINGS_SET
                     | CMD_SETTINGS_ADD | CMD_SETTINGS_REMOVE | CMD_SETTINGS_SAVE
                     | CMD_SETTINGS_REVERT)

# Ayar grupları: kullanıcı alan adlarını ezberlemek zorunda kalmasın diye
# ayarlar birkaç akılda kalıcı gruba bölündü. Her grubun kendi komutu var
# (/filtre, /bildirim …) ve komut yazıldığında o gruptaki ayarlar, güncel
# değerleri ve "dokunulmaya hazır" kısa komutlarıyla listelenir.
SETTING_GROUPS: tuple[dict[str, Any], ...] = (
    {"key": "filtre", "icon": "🔎", "title": "Filtre",
     "desc": "aranan/hariç kelimeler ve eşleşme modu",
     "fields": ("match_mode", "include_keywords", "exclude_keywords")},
    {"key": "kanallar", "icon": "📥", "title": "Kaynaklar",
     "desc": "dinlenen kanal ve gruplar (liste: /kaynaklar)",
     "fields": ("source_chats",)},
    {"key": "hedef", "icon": "📤", "title": "Hedef",
     "desc": "fırsatların iletildiği sohbet",
     "fields": ("destination",)},
    {"key": "bildirim", "icon": "🔔", "title": "Bildirim",
     "desc": "bildirim botu, medya ve altbilgi",
     "fields": ("notify_bot_token", "notify_media", "source_footer", "notify_on_start")},
    {"key": "iletim", "icon": "🚚", "title": "İletim",
     "desc": "iletim yolları ve medya boyutu",
     "fields": ("delivery_modes", "copy_mode", "max_media_mb")},
    {"key": "linkler", "icon": "🔗", "title": "Bağlantılar",
     "desc": "gizli linkler ve mesaj linki",
     "fields": ("link_appendix", "message_link")},
    {"key": "yetki", "icon": "🛡", "title": "Yetki",
     "desc": "komutların dinlendiği sohbet ve yetkili ID'ler",
     "fields": ("control_chat", "admin_user_id")},
    {"key": "sistem", "icon": "⚙️", "title": "Sistem",
     "desc": "otomatik yenileme",
     "fields": ("auto_restart",)},
)

# Grup komutlarının alternatif yazımları → grup anahtarı.
GROUP_ALIASES = {
    "filtre": "filtre", "filter": "filtre", "kelimeler": "filtre", "kelime": "filtre",
    "kanallar": "kanallar", "kanal": "kanallar", "kanallarim": "kanallar",
    "kanallarım": "kanallar", "grup": "kanallar",
    "hedef": "hedef", "hedefsohbet": "hedef", "hedef_sohbet": "hedef", "gonderim": "hedef",
    "bildirim": "bildirim", "bildirimler": "bildirim", "notification": "bildirim",
    "iletim": "iletim", "yollar": "iletim", "teslimat": "iletim",
    "linkler": "linkler", "baglantilar": "linkler", "bağlantılar": "linkler",
    "yetki": "yetki", "yetkiler": "yetki", "izin": "yetki", "admin": "yetki",
    "sistem": "sistem", "system": "sistem",
}

# Menülerde gösterilen kısa komut adı: /kelime_ekle, /mod, /kanal_sil gibi
# komutlar otomatik olarak üretiliyor (alan adı + eylem).
FIELD_SHORT_NAMES = {
    "source_chats": "kanal", "destination": "hedef", "control_chat": "kontrol",
    "admin_user_id": "admin", "include_keywords": "kelime",
    "exclude_keywords": "haric", "match_mode": "mod", "delivery_modes": "yol",
    "copy_mode": "kopya", "max_media_mb": "medya", "link_appendix": "linkeki",
    "message_link": "mesajlinki", "source_footer": "altbilgi",
    "notify_media": "bildirimmedya", "notify_on_start": "acilisbildirimi",
    "auto_restart": "yenileme", "notify_bot_token": "token",
}

# "/kelime_ekle" gibi alan+eylem komutlarındaki eylem ekleri.
FIELD_ACTION_SUFFIXES = {"ekle": "add", "sil": "remove", "set": "set",
                         "goster": "show", "göster": "show", "gor": "show",
                         "gör": "show", "degistir": "set", "değiştir": "set"}

# Grup anahtarı → grup sözlüğü (hızlı erişim).
GROUP_BY_KEY = {group["key"]: group for group in SETTING_GROUPS}

# Çok adımlı akış: "/ekle" yazıldığında bot hangi alana ekleneceğini sorar,
# kullanıcı cevap yazınca işlem tamamlanır. Bekleyen işlem şu sözlükte tutulur;
# yalnızca bellekte olduğu için bot yeniden başlarsa kaybolur (zararsız).
PENDING: dict[tuple[int, int], dict[str, Any]] = {}
PENDING_TTL_SECONDS = 600  # 10 dakika sonra bekleyen işlem düşer

# Eski sabit korunuyor: /ayar çıktısı artık dinamik üretiliyor.
SETTINGS_HELP_TEXT = build_main_menu_text()

# config.json'a yazma sonucuna göre kullanıcıya gösterilen satırlar.
SAVE_STATUS_TEXT = {
    "pushed": "✅ config.json yazıldı ve repo'ya işlendi ({detail}) — yeniden başlasa da kalıcı.",
    "clean": "💾 config.json yazıldı; depoda ayrıca işlenecek değişiklik yoktu ({detail}).",
    "local": ("⚠️ config.json yazıldı ve commit edildi ama gönderilemedi ({detail}). "
              "Bot yeniden başlarsa bu değişiklik kaybolur; /ayar_kaydet ile tekrar dene."),
    "no-repo": ("⚠️ config.json yazıldı ama depoya işlenemedi ({detail}). "
                "Değişiklik yalnızca bu oturumda geçerli."),
    "error": ("⚠️ config.json yazıldı ama repo'ya işlenemedi ({detail}). "
              "/ayar_kaydet ile tekrar deneyebilirsin."),
}


def split_values(raw: Any) -> list[str]:
    """Virgül/noktalı virgül/satır ile ayrılmış girdiyi parçalara ayır."""
    text = str(raw or "").replace("\n", ",").replace(";", ",")
    values: list[str] = []
    for chunk in text.split(","):
        item = chunk.strip().strip("'\"")
        if item:
            values.append(item)
    return values


def coerce_item(spec: dict, raw: Any) -> tuple[bool, Any]:
    """Liste öğesini alanın tipine çevir: (ok, değer | hata)."""
    kind = spec["kind"]
    text = str(raw).strip()
    if kind == "str_list":
        value = normalize(text) if spec.get("fold") else text
        return (True, value) if value else (False, "boş değer")
    if kind == "enum_list":
        return _coerce_choice(text, spec["choices"], spec.get("aliases"))
    if kind == "chat_list":
        try:
            return True, parse_chat_value(text)
        except ValueError as exc:
            return False, str(exc)
    if kind == "id_list":
        try:
            return True, int(text)
        except ValueError:
            return False, "bir kullanıcı ID'si (sayı) olmalı"
    return False, f"{kind} listesi desteklenmiyor"


def _coerce_choice(text: Any, choices: Sequence[str], aliases: dict | None = None) -> tuple[bool, Any]:
    """Sabit listeden bir değer seç; yazım farklarını ve takma adları kabul et."""
    for key in choice_keys(text):
        value = (aliases or {}).get(key)
        if value is None and key in choices:
            value = key
        if value is not None:
            return True, value
    return False, f"geçersiz değer {str(text)!r} (geçerli: {', '.join(choices)})"


def coerce_scalar(spec: dict, raw: Any) -> tuple[bool, Any, str]:
    """Tek değerli alanı çevir: (ok, değer, hata)."""
    kind = spec["kind"]
    text = str(raw).strip()
    if kind == "enum":
        ok, value = _coerce_choice(text, spec["choices"], spec.get("aliases"))
        return (True, value, "") if ok else (False, None, str(value))
    if kind == "int":
        match = re.search(r"-?\d+", text)
        if not match:
            return False, None, f"{text!r} bir sayı değil"
        number = int(match.group())
        low, high = spec.get("min"), spec.get("max")
        if low is not None and number < low:
            return False, None, f"en az {low} olmalı"
        if high is not None and number > high:
            return False, None, f"en fazla {high} olmalı"
        return True, number, ""
    if kind == "bool":
        key = normalize(text)
        if key in BOOL_TRUE:
            return True, True, ""
        if key in BOOL_FALSE:
            return True, False, ""
        return False, None, f"geçersiz değer {text!r} (açık/kapalı, true/false, 1/0)"
    if kind == "chat":
        try:
            return True, parse_chat_value(text), ""
        except ValueError as exc:
            return False, None, str(exc)
    if kind == "secret":
        return True, ("" if normalize(text) in {"null", "none", "yok", "-"} else text), ""
    return False, None, f"{kind} alanı Telegram'dan değiştirilemez"


def coerce_list(spec: dict, raw: Any) -> tuple[bool, Any, str]:
    """Liste alanını çevir: (ok, liste, hata)."""
    values = split_values(raw)
    if not values:
        return False, None, "en az bir değer ver"
    result: list[Any] = []
    for item in values:
        ok, value = coerce_item(spec, item)
        if not ok:
            return False, None, f"{item!r} geçersiz: {value}"
        result.append(value)
    return True, result, ""


def field_help() -> str:
    """Değiştirilebilir alanların listesi (hata mesajlarında kullanılır)."""
    names = sorted(SETTING_FIELDS)
    return "Değiştirilebilir alanlar: " + ", ".join(names) + "\nAyrıntı: /ayar"


def alias_keys(text: Any) -> tuple[str, ...]:
    """Komut/alan adının olası yazımları: "/FILTRE" → filtre, fıltre, filtra…

    Türkçe büyük harf indirgemesi "I"yı "ı" yapar; kullanıcı klavyeden büyük
    harfle yazdığında "filtre" yerine "fıltre" gelir. İki yönü de deneyelim.
    """
    base = normalize(str(text or "")).strip().lstrip("/")
    return (base, base.replace("ı", "i"), base.replace("i", "ı"))


def resolve_setting_field(raw: str) -> str | None:
    """Yazılan alan adını kurallı isme çevir (kısa/Türkçe adlar dahil)."""
    for key in alias_keys(raw):
        if key in SETTING_FIELDS:
            return key
        alias = FIELD_ALIASES.get(key)
        if alias:
            return alias
    return None


def short_command(field: str, action: str = "") -> str:
    """Alan için üretilen kısa komut: /kelime_ekle, /mod, /kanal_sil…"""
    name = FIELD_SHORT_NAMES.get(field, field)
    return f"/{name}" + (f"_{action}" if action else "")


def group_of(field: str) -> dict[str, Any] | None:
    """Alanın bağlı olduğu grup (menülerde gezinmek için)."""
    for group in SETTING_GROUPS:
        if field in group["fields"]:
            return group
    return None


# Alan menüsünde gösterilme sırası: en sık değiştirilenler üstte, böylece
# "/ekle → 1" gibi bir kısayol tahmin edilebilir olur.
FIELD_MENU_ORDER = (
    "match_mode", "include_keywords", "exclude_keywords", "source_chats",
    "destination", "control_chat", "admin_user_id", "delivery_modes",
    "copy_mode", "max_media_mb", "link_appendix", "message_link",
    "source_footer", "notify_media", "notify_on_start", "auto_restart",
    "notify_bot_token",
)


def fields_for_action(action: str) -> tuple[str, ...]:
    """Bir eylem için seçilebilir alanlar (liste eylemleri yalnızca listeleri sunar)."""
    if action in ("add", "remove"):
        names = [name for name, spec in SETTING_FIELDS.items()
                 if spec["kind"] in LIST_KINDS]
    else:
        names = list(SETTING_FIELDS)
    return tuple(sorted(names, key=lambda name: (FIELD_MENU_ORDER.index(name)
                                                 if name in FIELD_MENU_ORDER else 99)))


def parse_field_command(command: str) -> tuple[str, str] | None:
    """/kelime_ekle → (alan, eylem). /mod gibi eylemsiz yazım 'set' sayılır.

    Böylece her ayarın ayrı bir komutu varmış gibi davranır: istenen alan adı
    + istenen eylem tek bir komutta birleşir (``/dahil_liste_ekle`` gibi).
    """
    raw = normalize(str(command or "")).strip().lstrip("/")
    if not raw:
        return None
    if "_" in raw:
        head, _, tail = raw.rpartition("_")
        action = next((FIELD_ACTION_SUFFIXES[key] for key in alias_keys(tail)
                       if key in FIELD_ACTION_SUFFIXES), None)
        if action and head:
            field = resolve_setting_field(head)
            if field:
                return field, action
    field = resolve_setting_field(raw)
    return (field, "set") if field else None


def resolve_group_command(command: str) -> dict[str, Any] | None:
    """/filtre, /bildirim … gibi grup komutunu gruba çevir; değilse None."""
    for key in alias_keys(command):
        group = GROUP_ALIASES.get(key)
        if group:
            return GROUP_BY_KEY.get(group)
    return None


# Ayar komutu olmayan sabit komutlar: /source gibi adlar alan takma adıyla
# çakıştığı için bunlar ayar işleyicisine girmez.
RESERVED_COMMANDS = frozenset({
    "/status", "/durum", "/test", "/deneme", "/source", "/sources", "/kaynak",
    "/kaynaklar", "/id", "/restart", "/yenile", "/yeniden", "/help", "/yardim",
    "/yardım",
})


def is_settings_command(command: str) -> bool:
    """Bu komut ayar işleyicisine ait mi? (grup ve alan komutları dahil)"""
    if command in RESERVED_COMMANDS:
        return False
    return (command in SETTINGS_COMMANDS
            or resolve_group_command(command) is not None
            or parse_field_command(command) is not None)


def pick_from_menu(text: str, options: Sequence[Any]) -> str:
    """Menü seçimini çöz: önce birebir eşleşme, sonra satır numarası."""
    raw = str(text or "").strip()
    for option in options:
        if normalize(raw) == normalize(str(option)):
            return str(option)
    if raw.isdigit():
        index = int(raw)
        if 1 <= index <= len(options):
            return str(options[index - 1])
    return raw


def value_options_for(field: str, action: str, config: dict) -> list[str]:
    """Bir alan için numarayla seçilebilecek değerler.

    Hem çok adımlı akışta hem de tek mesajda biten komutlarda aynı davranış:
    enum alanlarda ve listeden silerken numara yazmak çalışır.
    """
    spec = SETTING_FIELDS.get(field, {})
    if spec.get("kind") == "enum":
        return [str(choice) for choice in spec["choices"]]
    if action == "remove" and spec.get("kind") in LIST_KINDS:
        return [str(item) for item in (config.get(field) or [])]
    if action == "set" and spec.get("kind") == "bool":
        return ["açık", "kapalı"]
    return []


# --- bekleyen (çok adımlı) işlem ------------------------------------------

def pending_key(event: Any) -> tuple[int, int]:
    return (int(getattr(event, "chat_id", 0) or 0),
            int(getattr(event, "sender_id", 0) or 0))


def set_pending(key: tuple[int, int], **data: Any) -> None:
    data["at"] = time.time()
    PENDING[key] = data


def take_pending(key: tuple[int, int]) -> dict[str, Any] | None:
    """Bekleyen işlemi al ve sil; süresi dolduysa yok say."""
    item = PENDING.pop(key, None)
    if item is None:
        return None
    if time.time() - float(item.get("at", 0)) > PENDING_TTL_SECONDS:
        return None
    return item


def drop_pending(key: tuple[int, int]) -> None:
    PENDING.pop(key, None)


def build_group_text(group: dict[str, Any], config: dict) -> str:
    """Bir grubun ayarlarını, değerleri ve kısa komutlarıyla listeler."""
    lines = [f"{group['icon']} {group['title']} — {group['desc']}", ""]
    for index, field in enumerate(group["fields"], start=1):
        spec = SETTING_FIELDS[field]
        lines.append(f"{index}. {field} — {spec['help']}")
        lines.append(f"   değer: {format_value(field, config.get(field))}")
        if spec["kind"] in LIST_KINDS:
            lines.append(f"   ➕ {short_command(field, 'ekle')} <değer>"
                         f"   ➖ {short_command(field, 'sil')} <değer>"
                         f"   👁 {short_command(field, 'goster')}")
        else:
            lines.append(f"   ✏️ {short_command(field)} <değer>"
                         f"   👁 {short_command(field, 'goster')}")
        lines.append("")
    lines.append("Komuta dokunup değeri yaz ya da yalnızca komutu gönder: "
                 "bot değeri sana sorar.")
    lines.append("Diğer gruplar: /ayar · Bekleyen işlemi iptal: /iptal")
    return "\n".join(lines)


def build_field_menu(action: str, config: dict) -> tuple[str, list[str]]:
    """'/ekle' gibi argümansız bir eylemde gösterilecek alan menüsü.

    Dönen ikinci değer menüdeki alanların sırası: kullanıcı numarayla da
    seçebilsin diye kaydediliyor.
    """
    titles = {"add": ("➕", "Hangi listeye ekleyelim?"),
              "remove": ("➖", "Hangi listeden çıkaralım?"),
              "set": ("✏️", "Hangi ayarı değiştirelim?"),
              "show": ("👁", "Hangi ayarı gösterelim?")}
    icon, title = titles.get(action, titles["set"])
    options = list(fields_for_action(action))
    example = FIELD_SHORT_NAMES.get(options[0], options[0]) if options else "kelime"
    # Menüde gösterilen komut eyleme göre: /kelime_ekle, /kelime_sil, /kelime…
    suffix = {"add": "ekle", "remove": "sil", "show": "goster"}.get(action, "")
    lines = [f"{icon} {title}", ""]
    for index, field in enumerate(options, start=1):
        lines.append(f"{index}. {field} — {SETTING_FIELDS[field]['help']}")
        lines.append(f"   şu an: {format_value(field, config.get(field))}"
                     f"   ·   {short_command(field, suffix)}")
    lines += ["", f"Numarayı yaz ya da kısa adı yaz (örn. 2 veya {example}).",
              "İptal: /iptal"]
    return "\n".join(lines), options


def build_value_prompt(field: str, action: str, config: dict) -> tuple[str, list[str]]:
    """Alan seçildikten sonra gösterilen 'değeri yaz' sorusu.

    Dönen ikinci değer numarayla seçilebilecek seçenekler (enum değerleri ya da
    listedeki mevcut kayıtlar); boşsa kullanıcı serbest metin yazar.
    """
    spec = SETTING_FIELDS[field]
    kind = spec["kind"]
    lines = [f"✍️ {field} — {spec['help']}", ""]

    if action == "remove" and kind in LIST_KINDS:
        value = config.get(field) or []
        if not value:
            return (f"ℹ️ {field} zaten boş; silinecek bir şey yok.", [])
        lines.append("Hangisini silelim?")
        lines.append("")
        for index, item in enumerate(value[:40], start=1):
            lines.append(f" {index}. {item}")
        if len(value) > 40:
            lines.append(f" … (+{len(value) - 40} kayıt daha; değerini yaz)")
        lines += ["", "Numara veya değer yaz · hepsi için: hepsi · İptal: /iptal"]
        return "\n".join(lines), [str(item) for item in value]

    if kind == "enum":
        choices = list(spec["choices"])
        lines.append("Seçenekler:")
        lines.append("")
        for index, choice in enumerate(choices, start=1):
            lines.append(f" {index}. {choice}")
        lines += ["", f"Şu an: {format_value(field, config.get(field))}",
                  "Numarayı yaz ya da değerin kendisini yaz · İptal: /iptal"]
        return "\n".join(lines), [str(choice) for choice in choices]

    if kind == "enum_list":
        choices = list(spec["choices"])
        lines.append(f"Geçerli değerler: {', '.join(choices)}")
        lines.append("Virgülle sırala (örn. copy,forward) · İptal: /iptal")
        return "\n".join(lines), [str(choice) for choice in choices]

    if kind == "bool":
        lines.append("açık / kapalı yaz (true/false, 1/0 da olur)")
        lines.append(f"Şu an: {format_value(field, config.get(field))} · İptal: /iptal")
        return "\n".join(lines), ["açık", "kapalı"]

    if kind in ("chat", "chat_list"):
        lines.append("@kullaniciadi veya -100... ID yaz"
                     + (" (virgülle çoklu)" if kind == "chat_list" else ""))
        lines.append("İptal: /iptal")
        return "\n".join(lines), []

    if kind == "id_list":
        lines.append("Kullanıcı ID'si (sayı) yaz · İptal: /iptal")
        return "\n".join(lines), []

    if kind == "int":
        lines.append(f"Sayı yaz (min {spec.get('min', 0)}"
                     f"{', max ' + str(spec['max']) if spec.get('max') is not None else ''})"
                     " · İptal: /iptal")
        return "\n".join(lines), []

    if kind == "secret":
        lines.append("Token'ı yapıştır (silmek için: yok) · İptal: /iptal")
        return "\n".join(lines), []

    if kind in LIST_KINDS:
        lines.append(("Şimdi değeri yaz" if action == "add" else "Yeni listeyi yaz")
                     + " (virgülle çoklu: çay, kahve) · İptal: /iptal")
        return "\n".join(lines), []

    lines.append("Şimdi yeni değeri yaz · İptal: /iptal")
    return "\n".join(lines), []


def format_value(field: str, value: Any) -> str:
    """Ayar değerini okunabilir tek satıra indir (sırları gizler)."""
    spec = SETTING_FIELDS.get(field, {})
    if spec.get("kind") == "secret":
        return "var" if str(value or "").strip() else "yok"
    if isinstance(value, bool):
        return "açık" if value else "kapalı"
    if value is None:
        return "(boş)"
    if isinstance(value, (list, tuple)):
        if not value:
            return "(boş liste)"
        items = [str(item) for item in value]
        shown = ", ".join(items[:8])
        return f"{len(items)} kayıt: {shown}" + (f" … (+{len(items) - 8})" if len(items) > 8 else "")
    return str(value)


class ConfigStore:
    """config.json'ı tutar: doğrular, çalışan bot'a uygular, kalıcı yazar.

    Değişiklik akışı: doğrula → config'i güncelle → çalışan bot'a uygula →
    dosyaya/repo'ya yaz. Herhangi bir adım kalıcı yazmadan önce başarısız
    olursa çağıran taraf ``restore()`` ile eski hâle döner.
    """

    def __init__(self, path: str | os.PathLike[str], config: dict) -> None:
        self.path = Path(path)
        self.config: dict = config
        self.undo: dict | None = None
        self.last_saved_at = 0.0
        self.last_save_note = "bu oturumda henüz kaydedilmedi"

    # --- alan adı -------------------------------------------------------
    def resolve_field(self, raw: str) -> str | None:
        """Yazılan alan adını kurallı isme çevir (kısa/Türkçe adlar dahil)."""
        key = normalize(str(raw or "")).strip().lstrip("/")
        if key in SETTING_FIELDS:
            return key
        return FIELD_ALIASES.get(key)

    # --- anlık görüntü --------------------------------------------------
    def snapshot(self) -> dict:
        return copy.deepcopy(self.config)

    def restore(self, snapshot: dict) -> None:
        """Config'i verilen görüntüye döndür (geri alma için)."""
        self.config = copy.deepcopy(snapshot)
        self.undo = None

    def _begin(self) -> None:
        """Değişiklikten önceki hâli geri alma tamponuna yaz."""
        self.undo = copy.deepcopy(self.config)

    def revert(self) -> str:
        """Son değişikliği geri al; geri alınacak şey yoksa bilgi döner."""
        if self.undo is None:
            return "ℹ️ Geri alınacak bir değişiklik yok."
        before, after = self.undo, self.snapshot()
        self.config = before
        self.undo = None
        changed = [name for name in SETTING_FIELDS
                   if before.get(name) != after.get(name)]
        return "↩️ Geri alındı: " + (", ".join(changed) if changed else "değişiklik yok")

    def mark_saved(self) -> None:
        self.last_saved_at = time.time()

    def _as_list(self, field: str) -> list[Any]:
        """Alanın liste hâli.

        ``admin_user_id`` config'te çoğu zaman tek sayı olarak durur
        (``1143378073``); liste komutlarının bunu da liste gibi görmesi gerekir,
        aksi halde ``list(1143378073)`` TypeError verir.
        """
        value = self.config.get(field)
        if value is None:
            return []
        if isinstance(value, (list, tuple, set)):
            return list(value)
        return [value]

    # --- değiştirme -----------------------------------------------------
    def set_field(self, raw_field: str, raw_value: Any) -> tuple[bool, str, str | None]:
        """Alanı doğrula ve değiştir: (ok, mesaj, alan)."""
        field = self.resolve_field(raw_field)
        if field is None:
            return False, f"❌ Bilinmeyen alan: {raw_field}\n\n{field_help()}", None
        spec = SETTING_FIELDS[field]
        if spec["kind"] in LIST_KINDS:
            ok, value, error = coerce_list(spec, raw_value)
        else:
            ok, value, error = coerce_scalar(spec, raw_value)
        if not ok:
            return False, f"❌ {field}: {error}\n{spec['help']}", None
        old = self.config.get(field)
        if old == value:
            return False, f"ℹ️ {field} zaten {format_value(field, value)}", None
        self._begin()
        self.config[field] = value
        return True, f"✅ {field}: {format_value(field, old)} → {format_value(field, value)}", field

    def add_to_field(self, raw_field: str, raw_value: Any) -> tuple[bool, str, str | None]:
        """Liste alanına öğe ekle: (ok, mesaj, alan)."""
        field = self.resolve_field(raw_field)
        if field is None:
            return False, f"❌ Bilinmeyen alan: {raw_field}\n\n{field_help()}", None
        spec = SETTING_FIELDS[field]
        if spec["kind"] not in LIST_KINDS:
            return False, (f"❌ {field} bir liste değil; eklemek yerine şunu kullan: "
                           f"/ayar_set {field} <deger>"), None
        ok, items, error = coerce_list(spec, raw_value)
        if not ok:
            return False, f"❌ {field}: {error}", None
        existing = self._as_list(field)
        known = {self._item_key(spec, item) for item in existing}
        added: list[Any] = []
        for item in items:
            key = self._item_key(spec, item)
            if key in known:
                continue
            existing.append(item)
            known.add(key)
            added.append(item)
        if not added:
            return False, f"ℹ️ {', '.join(str(x) for x in items)} zaten {field} içinde.", None
        self._begin()
        self.config[field] = existing
        return True, (f"➕ {field}: {', '.join(format_value(field, x) for x in added)} eklendi "
                      f"(toplam {len(existing)})"), field

    def remove_from_field(self, raw_field: str, raw_value: Any) -> tuple[bool, str, str | None]:
        """Liste alanından öğe çıkar: değer, satır numarası veya 'hepsi'."""
        field = self.resolve_field(raw_field)
        if field is None:
            return False, f"❌ Bilinmeyen alan: {raw_field}\n\n{field_help()}", None
        spec = SETTING_FIELDS[field]
        if spec["kind"] not in LIST_KINDS:
            return False, f"❌ {field} bir liste değil; silmek için /ayar_set {field} <deger>", None
        current = self._as_list(field)
        if not current:
            return False, f"ℹ️ {field} zaten boş.", None
        targets = split_values(raw_value)
        if not targets:
            return False, (f"❌ Silmek için bir değer veya satır numarası ver. "
                           f"Örnek: /ayar_sil {field} 1"), None
        if len(targets) == 1 and normalize(targets[0]) in {"hepsi", "tumu", "tümü", "all",
                                                           "*", "clear", "bos", "boş", "hepsini"}:
            self._begin()
            self.config[field] = []
            return True, f"🧹 {field} temizlendi ({len(current)} kayıt silindi).", field

        doomed: set[int] = set()
        unknown: list[str] = []
        for target in targets:
            digits = str(target).strip()
            if digits.isdigit():
                index = int(digits)
                if 1 <= index <= len(current):
                    doomed.add(index - 1)
                    continue
            ok, typed = coerce_item(spec, target)
            if not ok:
                unknown.append(str(target))
                continue
            key = self._item_key(spec, typed)
            for index, item in enumerate(current):
                if self._item_key(spec, item) == key:
                    doomed.add(index)
        if not doomed:
            hint = f" (bilinmeyen: {', '.join(unknown)})" if unknown else ""
            return False, f"ℹ️ {', '.join(targets)} {field} içinde bulunamadı{hint}.", None
        removed = [current[index] for index in sorted(doomed)]
        remaining = [item for index, item in enumerate(current) if index not in doomed]
        self._begin()
        self.config[field] = remaining
        return True, (f"➖ {field}: {', '.join(format_value(field, x) for x in removed)} silindi "
                      f"(kalan {len(remaining)})"), field

    @staticmethod
    def _item_key(spec: dict, value: Any) -> str:
        """Liste öğelerini karşılaştırma anahtarı (yazım farkını yok sayar)."""
        return normalize(str(value)) if spec.get("fold") else str(value)

    # --- gösterim -------------------------------------------------------
    def status_line(self) -> str:
        if not self.last_saved_at:
            line = "📄 Kaynak: config.json (bu oturumda değişiklik yapılmadı)"
        else:
            line = (f"📄 config.json · son kayıt: {humanize(time.time() - self.last_saved_at)} önce "
                    f"· {self.last_save_note}")
        if ENV_OVERRIDES:
            line += (f"\n⚠️ Ortam değişkenleri şu alanları eziyor: {', '.join(sorted(ENV_OVERRIDES))}. "
                     "Kaydedilen dosyada ortam değerleri yazılı olur.")
        return line


def build_settings_text(store: ConfigStore, only: str | None = None) -> str:
    """/ayar_goster çıktısını kur."""
    config = store.config
    if only:
        spec = SETTING_FIELDS[only]
        value = config.get(only)
        lines = [f"⚙️ {only}", "", f"• değer: {format_value(only, value)}",
                 f"• açıklama: {spec['help']}"]
        if spec["kind"] in LIST_KINDS and isinstance(value, (list, tuple)) and value:
            lines.append("")
            for index, item in enumerate(value, start=1):
                if index > 40:
                    lines.append(f"  … (+{len(value) - 40} kayıt daha)")
                    break
                lines.append(f"  {index}. {item}")
            lines += ["", f"Silmek için: /ayar_sil {only} <numara veya değer>",
                      f"Eklemek için: /ayar_ekle {only} <deger>"]
        elif spec["kind"] == "enum":
            lines += ["", f"Geçerli değerler: {', '.join(spec['choices'])}"]
        lines += ["", store.status_line()]
        return "\n".join(lines)

    mode = match_mode_of(config)
    mode_label = {
        "any": "any (kelimelerden biri yeterli)",
        "all": "all (kelimelerin hepsi zorunlu)",
        "forward_all": "forward_all → 🔓 TÜM mesajlar iletiliyor",
    }[mode]
    lines = [
        "⚙️ Aktif ayarlar (çalışan bot)",
        "",
        "🔎 Filtre",
        f"• match_mode: {mode_label}",
        f"• include_keywords: {format_value('include_keywords', config.get('include_keywords'))}",
        f"• exclude_keywords: {format_value('exclude_keywords', config.get('exclude_keywords'))}",
        "",
        "📤 İletim",
        f"• destination: {DESTINATION_LABEL}",
        f"• delivery_modes: {' → '.join(DELIVERY_CHAIN) or 'yok'}",
        f"• copy_mode: {config.get('copy_mode', 'forward')}",
        f"• max_media_mb: {MAX_MEDIA_MB}",
        f"• link_appendix: {LINK_APPENDIX_MODE} | mesaj linki: "
        f"{'açık' if MESSAGE_LINK_LINE else 'kapalı'}",
        "",
        "🔔 Bildirim",
        f"• notify_bot_token: {format_value('notify_bot_token', config.get('notify_bot_token'))}",
        f"• kaynak altbilgisi: {'açık' if SOURCE_FOOTER else 'kapalı'} | "
        f"bildirim medyası: {'açık' if NOTIFY_MEDIA else 'kapalı'}",
        "",
        "🛠 Çalışma",
        f"• source_chats: {format_value('source_chats', config.get('source_chats'))} "
        f"(çözülen {len(SOURCE_IDS)})",
        f"• control_chat: {', '.join(CONTROL_NAMES) or 'me'}",
        f"• admin_user_id: {', '.join(str(i) for i in sorted(ADMIN_IDS)) or 'tanımsız'}",
        f"• auto_restart: {format_value('auto_restart', config.get('auto_restart'))} | "
        f"notify_on_start: {format_value('notify_on_start', config.get('notify_on_start'))}",
        "",
        store.status_line(),
        "Ayrıntı için: /ayar_goster <alan> · Menü: /ayar",
    ]
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
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
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


def commit_and_push(path: Path, message: str) -> tuple[str, str]:
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
    store.mark_saved()
    status, detail = await asyncio.to_thread(commit_and_push, store.path, note)
    store.last_save_note = {"pushed": "repo'ya işlendi", "clean": "depo zaten güncel",
                            "local": "yerelde kaldı", "no-repo": "depoya işlenemedi",
                            "error": "depoya işlenemedi"}.get(status, "kaydedildi")
    return True, SAVE_STATUS_TEXT.get(status, SAVE_STATUS_TEXT["error"]).format(detail=detail)


# ---------------------------------------------------------------------------
# Çalışan bot'a ayar uygulama
# ---------------------------------------------------------------------------

def apply_runtime_config(config: dict) -> list[str]:
    """Global çalışma ayarlarını config'ten yenile (Telegram bağlantısı gerekmez)."""
    global FILTER_INCLUDE, FILTER_EXCLUDE, FILTER_MODE, ADMIN_IDS
    global DELIVERY_CHAIN, MAX_MEDIA_MB
    global LINK_APPENDIX_MODE, LINK_KINDS, BOT_LINK_KINDS, APPEND_LINKS
    global MESSAGE_LINK_LINE, SOURCE_FOOTER, NOTIFY_MEDIA, NOTIFY_BOT_TOKEN

    notes: list[str] = []
    FILTER_INCLUDE = [normalize(x) for x in config.get("include_keywords") or []]
    FILTER_EXCLUDE = [normalize(x) for x in config.get("exclude_keywords") or []]
    FILTER_MODE = match_mode_of(config)
    ADMIN_IDS = parse_admin_ids(config.get("admin_user_id"))
    DELIVERY_CHAIN = build_delivery_chain(config)
    try:
        MAX_MEDIA_MB = int(config.get("max_media_mb", 25))
    except (TypeError, ValueError):
        log.warning("max_media_mb sayı değil, 25 kabul edildi.")
        MAX_MEDIA_MB = 25
    LINK_APPENDIX_MODE = link_appendix_mode(config)
    APPEND_LINKS = LINK_APPENDIX_MODE != "off"
    LINK_KINDS = link_kinds_for(LINK_APPENDIX_MODE, bot=False)
    BOT_LINK_KINDS = link_kinds_for(LINK_APPENDIX_MODE, bot=True)
    MESSAGE_LINK_LINE = config_flag(config.get("message_link"), True)
    SOURCE_FOOTER = config_flag(config.get("source_footer"), True)
    NOTIFY_MEDIA = config_flag(config.get("notify_media"), True)
    token = str(config.get("notify_bot_token") or os.getenv("NOTIFY_BOT_TOKEN", "") or "").strip()
    NOTIFY_BOT_TOKEN = "" if token.lower() in {"null", "none", "yok"} else token

    if FILTER_MODE == "forward_all":
        notes.append("🔓 Filtre kapalı: kaynaklardaki TÜM mesajlar iletiliyor"
                     + (" (exclude_keywords yine de engeller)." if FILTER_EXCLUDE else "."))
    return notes


def changed_groups(before: dict, after: dict) -> set[str]:
    """İki config arasında Telegram'da yeniden çözülmesi gereken gruplar."""
    groups: set[str] = set()
    for field, spec in SETTING_FIELDS.items():
        group = spec.get("resolve")
        if group and before.get(field) != after.get(field):
            groups.add(group)
    return groups


async def resolve_sources(client: TelegramClient, config: dict, quiet: bool = False) -> list[str]:
    """Kaynakları tek tek çöz; biri bozuksa diğerleri çalışmaya devam eder."""
    global SOURCES, SOURCE_IDS, SOURCE_FAILURES
    resolved: list[dict[str, Any]] = []
    failures: list[tuple[Any, Exception]] = []
    for value in chat_values(config.get("source_chats")):
        try:
            peer_id, name, entity = await resolve_chat(client, value)
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
    SOURCES, SOURCE_FAILURES = resolved, failures
    SOURCE_IDS = {item["id"] for item in resolved}
    notes: list[str] = []
    for item in resolved:
        flag = "" if item["joined"] else "  <-- ÜYE DEĞİLSİN, mesaj gelmez!"
        if not quiet:
            log.info("Kaynak hazır: %-28s id=%-15s istenen=%s%s",
                     item["name"], item["id"], item["requested"], flag)
        elif not item["joined"]:
            notes.append(f"⚠️ {item['name']} kanalına üye değilsin; mesaj gelmez.")
    for value, exc in failures:
        log.warning("Kaynak atlandı: %r (%s)", value, exc)
    if failures:
        notes.append(f"⚠️ {len(failures)} kaynak çözülemedi: "
                     + ", ".join(str(value) for value, _ in failures[:5]))
    return notes


async def resolve_control(client: TelegramClient, config: dict,
                          quiet: bool = False) -> tuple[list[str], bool]:
    """Kontrol sohbetlerini çöz; Kayıtlı Mesajlar her zaman açık kalır.

    Dönen ikinci değer: istenen sohbetlerden en az biri çözülebildi mi?
    """
    global CONTROL_IDS, CONTROL_NAMES
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
    if SELF_ID is not None:
        ids.add(SELF_ID)  # Kayıtlı Mesajlar her zaman kontrol edilebilir
    resolved = bool(names)
    if not names:
        names.append("me (Kayıtlı Mesajlar)")
    CONTROL_IDS, CONTROL_NAMES = ids, names
    # Not: açılışta çağıran taraf loglar; burada tekrar loglamıyoruz.
    return [f"🎛 Komut sohbeti: {', '.join(names)}"], resolved


async def resolve_destination(client: TelegramClient, config: dict, quiet: bool = False) -> list[str]:
    """Hedef sohbeti çöz; olmazsa Kayıtlı Mesajlar'a düş."""
    global DESTINATION, DESTINATION_ID, DESTINATION_LABEL
    value = parse_chat_value(config.get("destination", "me"))
    try:
        peer_id, name, _ = await resolve_chat(client, value)
        DESTINATION, DESTINATION_ID = peer_id, peer_id
        DESTINATION_LABEL = f"{name} [{peer_id}]"
    except Exception as exc:  # noqa: BLE001
        log.error("destination çözülemedi: %r -> %s. Kayıtlı Mesajlar'a düşülüyor.", value, exc)
        DESTINATION, DESTINATION_ID = "me", None
        DESTINATION_LABEL = "me (Kayıtlı Mesajlar)"
        return [f"⚠️ destination çözülemedi ({value}); Kayıtlı Mesajlar'a düşüldü."]
    return [f"🎯 Hedef: {DESTINATION_LABEL}"]


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
        if not SOURCE_IDS:
            return notes, ("hiçbir kaynak çözülemedi; source_chats listesini ve "
                           "hesabın kanallara üyeliğini kontrol et")
    if "destination" in groups:
        notes += await resolve_destination(client, config)
        if strict and DESTINATION_ID is None:
            return notes, "destination çözülemedi; sohbet ID'sini/​kullanıcı adını kontrol et"
    if "control" in groups:
        control_notes, resolved = await resolve_control(client, config)
        notes += control_notes
        if strict and not resolved:
            return notes, "control_chat çözülemedi; komutları kullanamazsın, değer geri alındı"
    return notes, None


def parse_setting_args(rest: str) -> tuple[str, str]:
    """/ayar_set alan=deger ve /ayar_set alan deger yazımlarının ikisini de kabul et."""
    text = (rest or "").strip()
    if "=" in text:
        field, _, value = text.partition("=")
        if field.strip():
            return field.strip(), value.strip()
    field, _, value = text.partition(" ")
    return field.strip(), value.strip()


def strip_prefix(text: str) -> str:
    """'✅ match_mode: any → all' → 'match_mode: any → all' (baştaki işareti at)."""
    first_line = text.splitlines()[0] if text else ""
    return re.sub(r"^[\W_]+", "", first_line) or first_line


async def reply_chunked(event: Any, text: str, limit: int = 3500) -> None:
    """Uzun yanıtı Telegram'ın 4096 karakter sınırına göre parçalara böl."""
    chunks = chunk_text(text, limit)
    if not chunks:
        return
    for chunk in chunks:
        await event.reply(chunk)


def chunk_text(text: str, limit: int = 3500) -> list[str]:
    """Metni satır sınırlarını bozmadan parçalara ayır."""
    if len(text) <= limit:
        return [text] if text.strip() else []
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:  # tek satır sınırdan uzunsa zorla böl
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


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
    global SELF_ID, CONFIG_STORE
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

    # Ayar mağazası: Telegram'dan gelen değişiklikler burada tutulur ve hem
    # çalışan bot'a uygulanır hem de config.json'a geri yazılır.
    store = ConfigStore(config_path(args.config), config)
    CONFIG_STORE = store

    gh_pat = os.getenv("GH_PAT", "").strip()
    auto_restart = bool(config.get("auto_restart", True))
    restart_minutes = max(1, int(os.getenv("RESTART_AFTER_MINUTES", "330")))
    notify_on_start = bool(config.get("notify_on_start", True))

    # Telegram bağlantısı gerektirmeyen ayarlar (filtre, zincir, bayraklar).
    for note in apply_runtime_config(config):
        log.info("%s", note)

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

    # --- Kaynakları, kontrol sohbetini ve hedefi çöz.
    notes, fatal = await resolve_chat_groups(
        client, config, {"sources", "destination", "control"},
    )
    for note in notes:
        log.info("%s", note)
    if fatal:
        raise SystemExit(f"{fatal}; source_chats listesini ve hesap üyeliğini kontrol et.")
    not_joined = [item["name"] for item in SOURCES if not item["joined"]]
    if not_joined:
        log.warning(
            "Bu hesaptan üye olunmayan kaynaklar var, bunlardan mesaj GELMEZ: %s", ", ".join(not_joined)
        )
    if ADMIN_IDS:
        log.info("Komut kullanabilecek admin ID'leri: %s", sorted(ADMIN_IDS))
    elif len(CONTROL_IDS) > 1:
        log.warning("admin_user_id boş: grup komutları kimse tarafından kullanılamaz.")
    if not NOTIFY_BOT_TOKEN:
        log.warning(
            "notify_bot_token tanımlı değil: mesajları kendi hesabın gönderdiği için Telegram "
            "BİLDİRİM ÜRETMEZ (Kayıtlı Mesajlar'da da grupta da). Sesli bildirim istiyorsan "
            "@BotFather'dan bir bot oluşturup gruba ekle ve notify_bot_token alanına token'ı yaz."
        )

    def is_control_event(event: events.NewMessage.Event) -> bool:
        """Komut yalnızca kontrol sohbetinden gelirse işlenir."""
        return event.chat_id in CONTROL_IDS

    def is_admin_event(event: events.NewMessage.Event) -> bool:
        """Kayıtlı Mesajlar'a yazan hesabın sahibi; gruptaysa admin listesi geçerli."""
        if event.chat_id == SELF_ID:
            return True
        return event.sender_id in ADMIN_IDS

    async def apply_setting_change(before: dict,
                                   groups: set[str] | None = None) -> tuple[list[str], str | None]:
        """Ayar değişikliğini çalışan bot'a uygula.

        ``groups`` verilmezse hangi sohbet gruplarının değiştiği ``before`` ile
        karşılaştırılarak bulunur. Dönen ikinci değer doluysa değişiklik
        geçersiz sayılır ve çağıran taraf geri alır.
        """
        notes = apply_runtime_config(store.config)
        targets = groups if groups is not None else changed_groups(before, store.config)
        resolved, fatal = await resolve_chat_groups(client, store.config, targets, strict=bool(targets))
        return notes + resolved, fatal

    async def apply_settings_change(event: events.NewMessage.Event, field: str,
                                    value: str, action: str) -> None:
        """Doğrulanmış bir ayar değişikliğini uygula, kaydet ve bildir.

        Hem tek mesajda biten komutlar (/kelime_ekle çay) hem de çok adımlı
        akış (/ekle → menü → değer) sonunda buraya gelir.
        """

        async def finish(lines: list[str]) -> None:
            await reply_chunked(event, "\n".join(line for line in lines if line))

        # Numarayla seçim: hem "/mod 3" hem de "/mod → 3" aynı sonucu verir.
        options = value_options_for(field, action, store.config)
        if options:
            value = pick_from_menu(str(value), options)

        before = store.snapshot()
        if action == "set":
            ok, message, field = store.set_field(field, value)
        elif action == "add":
            ok, message, field = store.add_to_field(field, value)
        else:  # remove
            ok, message, field = store.remove_from_field(field, value)
        if not ok:
            await finish([message])
            return

        notes, fatal = await apply_setting_change(before)
        if fatal:
            groups = changed_groups(before, store.config)
            store.restore(before)
            await apply_setting_change(store.config, groups)
            await finish([f"❌ {strip_prefix(message)} — uygulanamadı: {fatal}",
                          "↩️ Eski ayar geri yüklendi, dosyaya yazılmadı."])
            return

        _, save_note = await save_config(store, f"{field} güncellendi")
        log.info("Ayar değişti: %s", message)
        await finish([message, *notes, save_note])

    async def handle_settings_command(event: events.NewMessage.Event,
                                      command: str, rest: str) -> None:
        """/ayar* komutlarını çalıştır (yetki kontrolü çağıran tarafta yapıldı).

        Üç kullanım biçimi desteklenir:
          • doğrudan:  /kelime_ekle çay      → alan+eylem tek komutta
          • dallı:     /ekle                 → menü → alan seç → değer yaz
          • gruplu:    /filtre               → grubun tüm ayarları ve komutları
        """

        async def finish(lines: list[str]) -> None:
            await reply_chunked(event, "\n".join(line for line in lines if line))

        async def ask_value(field: str, action: str) -> None:
            """Alan belli, değer yok: kullanıcıya sor ve cevabı bekle."""
            text, options = build_value_prompt(field, action, store.config)
            set_pending(pending_key(event), stage="value", action=action,
                        field=field, options=options)
            await finish([text])

        async def ask_field(action: str) -> None:
            """Hiç argüman yok: alan menüsünü göster ve seçimi bekle."""
            text, options = build_field_menu(action, store.config)
            set_pending(pending_key(event), stage="field", action=action,
                        options=options)
            await finish([text])

        # --- ana menü ----------------------------------------------------
        if command in CMD_SETTINGS_MENU:
            await finish([build_main_menu_text(), "", store.status_line()])
            return

        # --- grup menüsü (/filtre, /bildirim …) --------------------------
        group = resolve_group_command(command)
        if group is not None:
            await finish([build_group_text(group, store.config), "",
                          store.status_line()])
            return

        # --- göster / kaydet / geri al -----------------------------------
        if command in CMD_SETTINGS_SHOW:
            wanted = rest.strip()
            if not wanted:
                await ask_field("show")
                return
            field = store.resolve_field(wanted)
            if field is None:
                await finish([f"❌ Bilinmeyen alan: {wanted}", "", field_help()])
                return
            await finish([build_settings_text(store, field)])
            return

        if command in CMD_SETTINGS_SAVE:
            _, note = await save_config(store, "ayarlar Telegram üzerinden kaydedildi")
            await finish([note, store.status_line()])
            return

        if command in CMD_SETTINGS_REVERT:
            key = pending_key(event)
            if key in PENDING:
                drop_pending(key)
                await finish(["↩️ Bekleyen işlem iptal edildi."])
                return
            if store.undo is None:
                await finish(["ℹ️ Geri alınacak bir değişiklik yok.",
                              "İpucu: /ayar_kaydet ile mevcut ayarları yeniden yazabilirsin."])
                return
            before = store.snapshot()
            message = store.revert()
            notes, fatal = await apply_setting_change(before, None)
            if fatal:
                await finish([f"⚠️ {message}", fatal])
                return
            _, note = await save_config(store, "ayar değişikliği geri alındı")
            await finish([message, *notes, note])
            return

        # Yeni bir komut geldi: yarım kalmış bekleyen işlem varsa düşür.
        drop_pending(pending_key(event))

        # --- eylem: ekle / sil / değiştir --------------------------------
        if command in CMD_SETTINGS_ADD:
            action = "add"
        elif command in CMD_SETTINGS_REMOVE:
            action = "remove"
        elif command in CMD_SETTINGS_SET:
            action = "set"
        else:
            parsed = parse_field_command(command)
            if parsed is None:
                await finish([f"❌ Bilinmeyen komut: {command}", "", build_main_menu_text()])
                return
            action = parsed[1]
            # /kelime_ekle gibi komutlarda alan baştan belli; kalan metin değer.
            field_raw, value = parsed[0], rest.strip()
            if action == "show":
                await finish([build_settings_text(store, parsed[0])])
                return
            if value:
                await apply_settings_change(event, field_raw, value, action)
            else:
                await ask_value(field_raw, action)
            return

        # --- /ekle, /sil, /set: alan adı verilmiş mi? ---------------------
        field_raw, value = parse_setting_args(rest)
        if not field_raw:
            await ask_field(action)
            return

        field = store.resolve_field(field_raw)
        if field is None:
            await finish([f"❌ Bilinmeyen alan: {field_raw}", "", field_help()])
            return

        if not value:
            await ask_value(field, action)
            return

        await apply_settings_change(event, field, value, action)

    async def handle_pending_message(event: events.NewMessage.Event,
                                     raw: str) -> bool:
        """Bekleyen çok adımlı işlem varsa bu mesajı o işlemin girdisi say.

        Dönen değer mesajın işlendiğini gösterir; ``False`` ise çağıran taraf
        mesajı yok sayar (komut değil, sadece sohbette yazılmış bir şey).
        """
        key = pending_key(event)
        item = take_pending(key)
        if item is None:
            return False

        action = item.get("action", "set")
        text = raw.strip()

        if item.get("stage") == "field":
            options = item.get("options") or []
            resolved = store.resolve_field(pick_from_menu(text, options))
            if resolved is None:
                await event.reply(
                    f"❌ Geçerli bir seçim değil: {text}\n"
                    "Numarayı ya da listede görünen adı yaz. İptal: /iptal")
                set_pending(key, **item)
                return True
            prompt, value_options = build_value_prompt(resolved, action, store.config)
            set_pending(key, stage="value", action=action, field=resolved,
                        options=value_options)
            if not value_options and prompt.startswith("ℹ️"):
                await event.reply(prompt)
                drop_pending(key)
                return True
            await event.reply(prompt)
            return True

        field = item.get("field") or ""
        options = item.get("options") or []
        value = pick_from_menu(text, options) if options else text
        if action == "show":
            await event.reply(build_settings_text(store, field))
            return True
        await apply_settings_change(event, field, value, action)
        return True

    # --- İletim yolları -----------------------------------------------------
    # Korumalı (noforwards) kanallarda forward ve copy patlar; o yüzden sırayla
    # denenir: forward -> copy -> medyayı indirip yeniden yükle -> sadece metin -> link.

    def source_of(event: events.NewMessage.Event) -> dict[str, Any] | None:
        """Kaynak kaydını bul (kaynak adı, kullanıcı adı, t.me linki için)."""
        return next((item for item in SOURCES if item["id"] == event.chat_id), None)

    def offer_link(event: events.NewMessage.Event) -> str | None:
        return build_message_link(event, source_of(event)) if MESSAGE_LINK_LINE else None

    async def send_forward(event: events.NewMessage.Event) -> None:
        await client.forward_messages(DESTINATION, event.message, from_peer=event.chat_id)

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
                DESTINATION,
                media,
                caption=composed["text"],
                formatting_entities=entities_for_text(event, composed["body"]),
                force_document=False,
            )
            return
        composed = compose_message(event, limit=MESSAGE_LIMIT - 100,
                                   link_kinds=LINK_KINDS, message_link=link)
        await client.send_message(
            DESTINATION,
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
            DESTINATION,
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
            DESTINATION,
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
        await client.send_message(DESTINATION, body[:MESSAGE_LIMIT], link_preview=True)

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
        if not raw:
            return
        command = normalize(raw.split()[0]) if raw.startswith("/") else ""

        if not is_admin_event(event):
            if not command:
                return
            # Sessizce yok saymak yerine net bir hata ver: kullanıcı ID'sini
            # gösterip nasıl yetki vereceğini de söylüyoruz.
            STATS["commands"] += 1
            log.warning("Yetkisiz komut denemesi: %s (chat=%s, sender=%s)",
                        command, event.chat_id, event.sender_id)
            await event.reply(
                "⛔ Bu komutu kullanmaya yetkin yok.\n"
                f"• Komut: {command}\n"
                f"• Kontrol sohbeti: {event.chat_id}\n"
                f"• Senin kullanıcı ID'n: {event.sender_id}\n"
                f"• Yetkili ID'ler: {', '.join(str(i) for i in sorted(ADMIN_IDS)) or 'tanımsız'}\n"
                "Yetki almak için hesabın sahibine şunu yazdır:\n"
                f"/admin_ekle {event.sender_id}"
            )
            return

        # Komut değil: çok adımlı akışta beklenen değer olabilir.
        if not command:
            await handle_pending_message(event, raw)
            return

        STATS["commands"] += 1
        # Yeni bir komut yarım kalmış akışı düşürür (/iptal hariç: o akışı
        # görmek için PENDING'e bakıyor).
        if command not in CMD_SETTINGS_REVERT:
            drop_pending(pending_key(event))
        rest = raw[len(raw.split()[0]):].strip()
        log.info("Komut alındı: %s (chat=%s, sender=%s)", command, event.chat_id, event.sender_id)

        # Sabit komutlar önce: /source gibi adlar alan takma adıyla çakışabilir.
        if command in {"/status", "/durum"}:
            await event.reply(build_status_text(store.config))
        elif command in {"/test", "/deneme"}:
            text = (
                f"🧪 Deneme mesajı – {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"Hedef: {DESTINATION_LABEL}\n"
                f"Kaynaklar: {len(SOURCE_IDS)} | Görülen: {STATS['seen']} | Eşleşen: {STATS['matched']}"
            )
            try:
                await client.send_message(DESTINATION, text)
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
        elif is_settings_command(command):
            # Ayar komutları en sonda: /ayar*, grup komutları (/filtre …) ve
            # alan komutları (/kelime_ekle, /mod …) burada işlenir.
            await handle_settings_command(event, command, rest)
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
        if not matches(text, FILTER_INCLUDE, FILTER_EXCLUDE, FILTER_MODE):
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
                DESTINATION,
                f"🟢 Takipçi başladı: {len(SOURCE_IDS)} kaynak dinleniyor"
                + (f", {len(SOURCE_FAILURES)} kaynak çözülemedi" if SOURCE_FAILURES else "")
                + f".\nHedef: {DESTINATION_LABEL}",
            )
        except Exception:  # noqa: BLE001
            log.exception("Başlangıç bildirimi gönderilemedi.")

    if auto_restart and gh_pat:
        asyncio.create_task(auto_restart_scheduler(client, DESTINATION, gh_pat, max(60, restart_minutes * 60)))
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
