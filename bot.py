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
import collections
import contextvars
import base64
import copy
import hashlib
import io
import json
import logging
import math
import mimetypes
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
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from telethon import TelegramClient, errors, events, utils
from telethon.sessions import StringSession
from telethon.tl import functions, types

import price_filter
import timefmt
from telegram_retry import gate_stats, request_with_retry
from private_bot import (
    BotAPI, BotAPIError, PrivateControlEvent, PrivateOfferQueue,
    dm_matches, edit_dm_keywords, normalize_keyword, poll_health, poll_private_commands,
    private_copy_request,
)

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
STATS = {
    "seen": 0,        # kaynaklarda görülen mesaj
    "matched": 0,     # anahtar kelimeye uyan mesaj
    "forwarded": 0,   # hedefe iletilen mesaj
    "failed": 0,      # hedefe hiçbir teslim yolu çalışmayan kaynak mesajı
    "notification_failed": 0,  # içerik iletilse bile Bot API telefon bildirimi başarısız
    "commands": 0,    # çalıştırılan komut
    "modes": {},      # hangi iletim yolu kaç kez işe yaradı
    "cleaned": 0,     # bildirim gittikten sonra silinen hesap kopyası
    "cleaned_commands": 0,  # komut sohbetinde silinen eski komut/yanıt mesajı
    "deduped": 0,     # aynı ürün sorgusuyla birleştirilen (yeniden gönderilmeyen) tekrar
    "dedup_edits": 0,  # tekrarda ilk mesaja işlenen çoklu paylaşım notu güncellemesi
    "last_match": None,
    "last_match_source": None,
    "source_seen": {},  # kaynak chat_id -> bu oturumda alınan canlı NewMessage sayısı
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
# Aktif filtre/komut durumları (apply_runtime_config yazar).
FILTER_INCLUDE: list[str] = []
FILTER_EXCLUDE: list[str] = []
FILTER_MODE = "any"           # any | all (dahili kelimeler nasıl eşleşsin)
FILTER_INCLUDE_ENABLED = True  # 🔎 dahili kelime filtresi açık mı?
FILTER_EXCLUDE_ENABLED = True  # 🚫 harici kelime engeli açık mı?
ADMIN_IDS: set[int] = set()
SOURCE_FOOTER = True  # bildirimin en altına kaynak grup adını KALIN yaz (etiket yok; link: mesajın kendisi)
NOTIFY_MEDIA = True   # bildirim botu medyayı da göndersin
MESSAGE_LINK_LINE = True      # mesajın t.me adresini kaynak adına göm (ad yoksa "🔗 Mesajı Gör" satırı)
LINK_APPENDIX_MODE = "smart"  # smart | all | off (bkz. link_appendix_mode)
# Tek mesaj modu: bildirim botu mesajı gruba attıysa, hesabın attığı kopya silinir.
# Böylece her fırsat grupta tek mesaj olarak kalır (botun bildirimi = uyarı düşen mesaj).
SINGLE_MESSAGE = True
# Komut sohbeti temizliği: yeni bir komut/yanıt geldiğinde bir önceki komut
# alışverişi (senin komutun + botun yanıtı) silinir; ekranda yalnızca son mesaj
# kalır. İndirim bildirimleri bu kayda girmediği için ASLA silinmez.
CLEAN_COMMANDS = True
# chat_id -> son komut alışverişinin mesaj ID'leri (yalnızca komut diyaloğu).
COMMAND_MESSAGES: dict[int, list[int]] = {}
# Tekrar birleştirme (dedup): aynı normalize ürün sorgulu fırsat tek mesaja toplanır.
# Ayrıntı: aşağıdaki "Tekrar birleştirme" bölümü.
DEDUP_ENABLED = True       # aynı ürün başlıklı tekrarlar birleştirilsin mi?
DEDUP_WINDOW_SECONDS = 12 * 3600  # normalize ürün başlığı kaç saniye "aynı fırsat" sayılsın
DEDUP_SCAN_LIMIT = 100     # açılışta hedefteki son kaç mesaj önbelleğe alınsın
DEDUP_MAX_ENTRIES = 1000   # bellekte tutulacak en fazla başlık (en eskiler düşer)
# normalize ürün sorgusu -> kayıt sözlüğü (bkz. new_dedup_entry).
DEDUP_CACHE: dict[str, dict[str, Any]] = {}
DEDUP_LOCK = asyncio.Lock()  # kayıt/rezervasyon kısa kritik bölümü

# Telegram sınırları (Bot API ve kullanıcı hesabı için ortak olanlar).
MESSAGE_LIMIT = 4096          # normal mesaj metni
CAPTION_LIMIT = 1024          # medya açıklaması
LINK_APPENDIX_LIMIT = 4       # ileti sonuna en fazla kaç ürün satırı ve (ayrıca) kaç 🔗 satırı yazılsın
MESSAGE_LINK_LABEL = "Mesajı Gör"
# Ürün bağlantısı her kaynakta aynı görünür: URL bu yazının içine gömülür
# (kullanıcı isteği: "🔗 ✅ Satın Al ✅: https://…" yerine "👉 Ürün Linki 👈").
PRODUCT_LINK_TEXT = "👉 Ürün Linki 👈"
# "Ürün linki" OLAMAYACAK adresler. Mağaza ve kısaltıcı alan adları sınırsızdır
# (amzn.to, ty.gl, app.hb.biz, fenom.io …); bu yüzden beyaz liste tutulmaz, yalnız
# kesin ürün olmayanlar elenir: sohbet/sosyal ağ adresleri ve fiyat arama servisleri
# (bkz. ``is_search_link``). Alan adı TAM ya da alt alan adı olarak eşleşir:
# ``m.facebook.com`` elenir; ``netflix.com`` (x.com) ve ``x.com.magaza.example`` elenmez.
# Genel kısaltıcılar (t.co, bit.ly …) ürüne de gidebildiği için listede yoktur.
NON_PRODUCT_HOSTS = (
    "t.me", "telegram.me", "telegram.dog", "youtube.com", "youtu.be",
    "x.com", "twitter.com", "facebook.com", "tiktok.com",
    # Topluluk/sosyal ağ davet, profil ve gönderi adresleri.
    "discord.gg", "discord.com", "discordapp.com", "linkedin.com", "lnkd.in",
    "pinterest.com", "pin.it", "reddit.com", "redd.it", "threads.net", "threads.com",
    "fb.com", "fb.me", "m.me", "twitch.tv", "snapchat.com", "tumblr.com", "vk.com", "bsky.app",
    "bip.ai", "linktr.ee",
    # Çekiliş/anket formları ve paylaşılan belgeler.
    "forms.gle", "docs.google.com",
)
# Pinterest ülke alan adlarını da kullanır (pinterest.co.uk, pinterest.de, pinterest.com.tr).
# Benzer adlar (mypinterest.de, pinterest.shop, pinterest.de.magaza.example) eşleşmez.
NON_PRODUCT_HOST_RE = re.compile(r"(?:^|\.)pinterest\.(?:[a-z]{2,3}|com?\.[a-z]{2})$")
# Yolu olmayan adres mağazanın ana sayfasıdır; aşağıdaki sorgu anahtarları onu ürün
# sayfasına çevirmez (iş ortaklığı etiketi, kampanya/tıklama izleme). Bunların dışında
# tek bir anahtar (``?p=123``, ``?url=https://…``) varsa adres ürüne gidebilir sayılır.
TRACKING_QUERY_KEYS = frozenset({
    "tag", "linkcode", "linkid", "camp", "creative", "creativeasin", "ascsubtag", "ref", "ref_",
    "language", "_encoding", "ie",
    "gclid", "gbraid", "wbraid", "dclid", "fbclid", "msclkid", "yclid", "ttclid", "twclid",
    "igshid", "srsltid", "aff", "affid", "affiliate", "source", "src",
})
TRACKING_QUERY_PREFIXES = ("utm_", "adjust_", "wt_", "aff_", "pf_rd_", "pd_rd_")
# Doğrudan görsel/video dosyası ürün sayfası değildir. Kaynak botları önizleme görselini
# çoğu zaman mesajın başındaki görünmez karaktere gömer; "ilk bağlantı" o olmamalı.
MEDIA_FILE_PATH_RE = re.compile(r"\.(?:jpe?g|png|webp|gif|bmp|avif|mp4|mov|webm)(?:/|$)", re.IGNORECASE)
# Ek satırlarının karakter sınırındaki önceliği (bkz. ``_compose_fit_appendix``):
# yedekli ``🔗`` satırı gövdeye yer bırakır; vazgeçilmez ``🔗`` satırı, yalın düzende de
# yazılabiliyorsa gövdeden önce gelir; ürün satırı gövde metninden yer alabilir ama
# gövdedeki bir bağlantıyı kesemez.
APPENDIX_SPARE, APPENDIX_REQUIRED, APPENDIX_PRODUCT = range(3)
# Arama bağlantıları mesaj metnine yazılmaz; yalnızca Bot API inline klavyesine
# eklenir. Böylece arama düğmeleri Telegram'ın 4096/1024 karakter sınırını tüketmez.
# Arama düğmelerinin kullanıcı isteğindeki sabit sırası.
PRICE_SEARCH_BUTTONS = (
    ("google_shopping", "Google Alışveriş"),
    ("akakce", "Akakçe'de ara"),
    ("cimri", "Cimri'de ara"),
)
MAX_INLINE_KEYBOARD_BUTTONS = 100
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
# Bot bildiriminde buton URL'si de metne taşınır; görünür URL ve metin
# hyperlink'i tekrarlanmaz. Önizleme için eski smart davranışı korunur.
BOT_LINK_KIND_GROUPS = {
    "smart": ("button",),
    "all": ("entity", "button", "webpage"),
    "off": (),
}


# ---------------------------------------------------------------------------
# Yapılandırma
# ---------------------------------------------------------------------------

def config_path(path: str | os.PathLike[str] | None = None) -> Path:
    """config.json'ın yolu (--config > CONFIG_FILE > config.json)."""
    return Path(path or os.getenv("CONFIG_FILE", "config.json"))


def load_config(path: str | os.PathLike[str] | None = None) -> dict:
    """config.json'ı oku; ortam değişkenleriyle (eski kurulumlar için) üzerine yaz."""
    file_path = config_path(path)
    with file_path.open(encoding="utf-8") as handle:
        config = json.load(handle)
    if isinstance(config, dict):
        # Legacy installs may still have a committed token; never carry it into
        # ConfigStore or write it back during unrelated settings updates.
        config.pop("notify_bot_token", None)
    for key in ("source_chats", "include_keywords", "exclude_keywords"):
        env_name = key.upper()
        if os.getenv(env_name):
            config[key] = [x.strip() for x in os.environ[env_name].split(",") if x.strip()]
    if os.getenv("DELIVERY_MODES"):
        config["delivery_modes"] = [x.strip() for x in os.environ["DELIVERY_MODES"].split(",") if x.strip()]
    if os.getenv("MAX_MEDIA_MB"):
        config["max_media_mb"] = os.environ["MAX_MEDIA_MB"]
    if os.getenv("DEDUP_WINDOW_HOURS"):
        config["dedup_window_hours"] = os.environ["DEDUP_WINDOW_HOURS"]
    if os.getenv("DEDUP_SCAN_LIMIT"):
        config["dedup_scan_limit"] = os.environ["DEDUP_SCAN_LIMIT"]
    for key in ("destination", "match_mode", "copy_mode", "control_chat"):
        if os.getenv(key.upper()):
            config[key] = os.environ[key.upper()]
    if os.getenv("ADMIN_USER_ID"):
        config["admin_user_id"] = os.environ["ADMIN_USER_ID"]
    if os.getenv("AUTO_RESTART"):
        config["auto_restart"] = os.environ["AUTO_RESTART"].strip().lower() in {
            "1", "true", "yes", "evet", "on",
        }
    for key in ("append_links", "clean_commands", "source_footer", "notify_media", "message_link", "single_message",
                "dedup_enabled"):
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


# Eşleştirme modları.
#   any         = include_keywords'ten en az biri geçsin
#   all         = include_keywords'ün hepsi geçsin
#   forward_all = filtre KAPALI; kaynaklardaki her mesaj iletilir
#                 (exclude_keywords bu modda da engellemeye devam eder)
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


def exclusion_normalize(text: str | None) -> str:
    """Brand spelling variants: L’Oréal / L'Oreal / Loreal, without joining words."""
    text = unicodedata.normalize("NFKD", normalize(text))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(re.sub(r"['’‘`ʼ]", "", text).split())


def matches(
    text: str | None,
    include: Sequence[str],
    exclude: Sequence[str],
    mode: str = "any",
    *,
    include_enabled: bool = True,
    exclude_enabled: bool = True,
) -> bool:
    """Mesaj metnini anahtar kelimelere göre değerlendir.

    İki filtre birbirinden bağımsızdır (/tgfiltreac ve /tgfiltrekapat ile yönetilir):
      * ``exclude_enabled`` açıkken ``exclude`` kelimelerinden biri geçen mesaj
        engellenir (varsayılan davranış).
      * ``include_enabled`` kapalıysa ``include`` listesi tamamen yok sayılır;
        yalnızca harici filtre uygulanır.
      * İkisi de kapalıysa kaynaklardaki her mesaj iletilir.

    Eski ``match_mode: forward_all`` ayarı da dahili filtreyi kapatır (geriye
    dönük uyumluluk).
    """
    normalized = normalize(text)
    excluded_text = exclusion_normalize(text)
    if exclude_enabled and any(
        (word := exclusion_normalize(term)) and word in excluded_text for term in exclude
    ):
        return False
    if not include_enabled:
        return True
    resolved = canonical_match_mode(mode) or "any"
    if resolved == "forward_all":
        return True
    if not include:
        return True
    found = [word in normalized for word in include]
    return all(found) if resolved == "all" else any(found)


def filter_state_of(config: dict) -> dict[str, bool]:
    """İki filtrenin açık/kapalı durumunu config'ten oku.

    ``include_enabled`` / ``exclude_enabled`` asıl anahtarlardır; eski
    ``match_mode: forward_all`` ayarı dahili filtreyi kapalı sayar.
    """
    include_enabled = config_flag(config.get("include_enabled"), True)
    if match_mode_of(config) == "forward_all":
        include_enabled = False
    return {
        "include": include_enabled,
        "exclude": config_flag(config.get("exclude_enabled"), True),
    }


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
          f"| komut temizliği: {'açık' if config_flag(config.get('clean_commands')) else 'kapalı'}", flush=True)
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

URL_RE = re.compile(
    r"(?:"
    r"(?:https?://|whatsapp://|t\.me/|telegram\.me/|www\.|wa\.me/|wa\.link/)"
    r"[^\s<>\"')\]}]+"
    r"|(?<![\w.-])(?:[\w-]+\.)*(?:wa\.me|wa\.link|whatsapp\.(?:com|net)|instagram\.com|instagr\.am|"
    r"akakce\.com|marketfiyati\.org\.tr|cimri\.com|google\.com(?:\.tr)?)(?:[/?#][^\s<>\"')\]}]*)?"
    r"(?=$|[\s<>\"')\]},;:!?…])"
    r")",
    re.IGNORECASE,
)
_URL_TAIL_TRIM = ".,;:!?…\"'”’)]}>»"
DISCLOSURE_TOKEN_RE = re.compile(r"(?<![\w#])#?(?:işbirliği|reklam)(?!\w)", re.IGNORECASE)
# Bir "satır sonu bloğu": ardışık satır sonları ve aralarındaki yatay boşluklar.
# Temizlikten arta kalan fazladan boş satırları bulmak için kullanılır.
_BLANK_RUN_RE = re.compile(r"[ \t\r]*(?:\n[ \t\r]*)+")
_EDGE_BLANK_RE = re.compile(r"\A[ \t\r\n]+|[ \t\r\n]+\Z")
# Arama sorgusunun başındaki emoji/sembol ("🛍️", "-", "|") sorguya girmemeli.
_LEADING_NOISE_RE = re.compile(r"\A[^\w]+", re.UNICODE)

# --- Kanal tanıtımı ve hashtag satırları ------------------------------------
# Kaynaklar mesajın altına kendi kanallarını ve etiket yığınını ekler:
#   💚Whatsapp Önemli Fırsatlar
#   #amazon #indirimalarmi
# Kullanıcı isteği: bunlar bildirime hiç girmesin ("tertemiz görünüm"). İki
# kural bilerek dar tutulur; böylece fiyat/ürün bilgisi taşıyan satır asla
# silinmez (veri kaybı yok):
#   1) Satırdaki TÜM kelimeler hashtag'lerden geliyorsa (salt etiket satırı).
#   2) Satır bir mesajlaşma kanalını (WhatsApp/Telegram) tanıtıyor ve
#      kelimelerinin TAMAMI tanıtım sözlüğünden geliyorsa.
# Sözlükte olmayan tek bir kelime satırı korur: "WhatsApp'tan bilgi ...
# 9 TL" içerik satırı olduğu gibi kalır.
# Kelime = harf/sayı dizisi (emoji ve noktalama atlanır). Türkçe ek, kesme
# işaretiyle ayrıldığında ("WhatsApp'ın", "kanalımıza") kelimenin parçası
# sayılır; böylece ek yüzünden tanıtım satırı gözden kaçmaz.
WORD_TOKEN_RE = re.compile(r"([^\W_]+)(?:['’][^\W_]+)?", re.UNICODE)
HASHTAG_WORD_RE = re.compile(r"#([^\W_]+)", re.UNICODE)
# Kök sözlüğü: 4+ harfli kökler önek eşleşir (fırsat→fırsatlar, katıl→katılın),
# kısa kelimeler birebir eşleşir (ve, ile, mi…). Türkçe küçük harfle yazılır.
PROMO_WORD_ROOTS = frozenset("""
abone aile amazon bize bizi biz bildirim bu burada buradan burdan da davet de diğer diger
duyuru edin ekle ekleyin fırsat gel gelin grup grub gün güncel haber hemen her hesap
herkes hepsiburada ile için indirim instagram ınstagram istersen kampanya kanal katıl kaçır link
olun önemli paylaş sayfa sitemiz şimdi takip telegram topluluk trendyol tüm üye
ve whatsapp watsap web wp yazın
""".split())
# Tanıtım satırı sayılmak için satırda bu köklerden biri geçmelidir: satır bir
# sosyal/mesajlaşma kanalını (WhatsApp/Telegram/Instagram) tanıtıyorsa tanıtımdır.
CHANNEL_WORD_ROOTS = (
    "whatsapp", "watsap", "whats", "wp", "telegram",
    "instagram", "ınstagram",
)


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
    scheme_less_service = re.match(
        r"(?:[\w-]+\.)*(?:wa\.me|wa\.link|whatsapp\.(?:com|net)|instagram\.com|instagr\.am|"
        r"akakce\.com|marketfiyati\.org\.tr|cimri\.com|google\.com(?:\.tr)?)(?:$|[/?#])",
        lowered,
    )
    if lowered.startswith(("t.me/", "telegram.me/", "www.")) or scheme_less_service:
        text = "https://" + text
    return text


def _as_message(obj: Any) -> Any:
    """Olay (Event) veya mesaj nesnesi verildiğinde mesajı döndür."""
    inner = getattr(obj, "message", None)
    if inner is not None and not isinstance(inner, str):
        return inner
    return obj


def message_text(obj: Any) -> str:
    """Mesajın ham metni (biçimlendirmeden bağımsız); metin girdisini de kabul et."""
    if isinstance(obj, str):
        return obj
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


def sent_message_ids(result: Any) -> list[int]:
    """``send_message``/``forward_messages`` çıktısından mesaj ID'lerini topla.

    Tek mesaj da liste de dönebilir; ID yoksa boş liste verir (silme atlanır).
    """
    items = result if isinstance(result, (list, tuple, set)) else [result]
    ids: list[int] = []
    for item in items:
        message_id = getattr(item, "id", None)
        if isinstance(message_id, int) and not isinstance(message_id, bool):
            ids.append(message_id)
    return ids


def first_sent_item(result: Any) -> Any | None:
    """Gönderim çıktısının ilk mesaj nesnesi (ID'lisi tercih edilir)."""
    items = result if isinstance(result, (list, tuple, set)) else [result]
    for item in items:
        message_id = getattr(item, "id", None)
        if isinstance(message_id, int) and not isinstance(message_id, bool):
            return item
    for item in items:
        if item is not None:
            return item
    return None


def first_sent_text(result: Any) -> str | None:
    """Gönderilen iletinin metni/açıklaması; okunamazsa ``None``."""
    text = getattr(first_sent_item(result), "message", None)
    return text if isinstance(text, str) and text else None


def first_sent_entities(result: Any) -> list[Any] | None:
    """Gönderilen iletinin biçim entity'leri; yoksa ``None``."""
    entities = getattr(first_sent_item(result), "entities", None)
    return list(entities) if entities else None


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


def is_whatsapp_url(url: Any) -> bool:
    """WhatsApp bağlantı alan adlarını tanı; benzer adlı yabancı alanları eşleştirme."""
    text = clean_url(url)
    lowered = text.lower()
    if lowered.startswith("whatsapp://"):
        return True
    if "://" not in lowered:
        text = "https://" + text
    try:
        host = (urllib.parse.urlsplit(text).hostname or "").rstrip(".").lower()
    except ValueError:
        return False
    return (
        host == "wa.me" or host.endswith(".wa.me")
        or host == "wa.link" or host.endswith(".wa.link")
        or host == "whatsapp.com" or host.endswith(".whatsapp.com")
        or host == "whatsapp.net" or host.endswith(".whatsapp.net")
    )


def is_instagram_url(url: Any) -> bool:
    """Instagram tanıtım bağlantısını alan adıyla tanı; benzer alanları eleme."""
    text = clean_url(url)
    if "://" not in text.lower():
        text = "https://" + text
    try:
        host = (urllib.parse.urlsplit(text).hostname or "").rstrip(".").lower()
    except ValueError:
        return False
    return (
        host == "instagram.com" or host.endswith(".instagram.com")
        or host == "instagr.am" or host.endswith(".instagr.am")
    )


def _utf16_to_index(text: str, offset: int) -> int:
    """Telegram UTF-16 offset'ini Python karakter indeksine çevir."""
    target = max(0, int(offset or 0))
    if target == 0:
        # Offset 0 metnin başıdır; aşağıdaki döngü onu ilk karakterin içi
        # sayıp 1 döndürürdü ve baştaki biçim ilk karakterini kaybederdi.
        return 0
    units = 0
    for index, char in enumerate(text):
        width = 2 if ord(char) > 0xFFFF else 1
        if units + width > target:
            # Telegram entity'leri normalde bir Unicode karakterinin ortasına
            # düşmez; bozuk girdide karakteri korumak için sağa yuvarla.
            return index + 1
        units += width
        if units == target:
            return index + 1
    return len(text)


def _merge_spans(spans: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """Örtüşen/bitişik silme aralıklarını tek aralığa indir."""
    ordered = sorted((start, end) for start, end in spans if end > start)
    merged: list[list[int]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _with_adjacent_space(text: str, start: int, end: int) -> tuple[int, int]:
    """Yalnızca silinen parçanın yanındaki boşluk/tabı al; başka metne dokunma."""
    left, right = start, end
    while left > 0 and text[left - 1] in " \t":
        left -= 1
    if left == start:
        while right < len(text) and text[right] in " \t":
            right += 1
    return left, right


def _blank_line_spans(text: str, *, keep: int = 2) -> list[tuple[int, int]]:
    """Fazla boş satırları ``keep`` satır sonuna indirecek silme aralıkları.

    WhatsApp linki/``#reklam`` etiketi silinince geriye çoklu boş satır kalır;
    bu aralıklar onları tek boş satıra indirir (``keep=2`` → bölümler arasında
    bir boş satır). Ayrıca satır sonu bloklarının içindeki yatay boşluklar ile
    baştaki/sondaki boş satırlar atılır. **Yalnızca** boşluk/tab/``\\r``/satır
    sonu karakterleri silinir; metin içeriğine dokunulmaz, veri kaybolmaz.
    """
    drop: list[int] = []
    for match in _BLANK_RUN_RE.finditer(text):
        newlines = [i for i in range(match.start(), match.end()) if text[i] == "\n"]
        kept = set(newlines[:keep])
        drop.extend(i for i in range(match.start(), match.end()) if i not in kept)
    # Kenardaki boş blok satır sonu içeriyorsa (yani boş satırsa) tümüyle atılır;
    # ilk satırın girintisi gibi satır içi boşluklar korunur.
    for edge in _EDGE_BLANK_RE.finditer(text):
        if "\n" in edge.group(0):
            drop.extend(range(edge.start(), edge.end()))
    return _merge_spans([(index, index + 1) for index in sorted(set(drop))])


def _remove_spans(text: str, spans: Sequence[tuple[int, int]]) -> str:
    for start, end in reversed(spans):
        text = text[:start] + text[end:]
    return text


def _index_after_removals(index: int, removed_spans: Sequence[tuple[int, int]]) -> int:
    removed = sum(
        max(0, min(index, end) - start)
        for start, end in removed_spans if index > start
    )
    return max(0, index - removed)


def _disclosure_spans(
    text: str,
    *,
    protected: Sequence[tuple[int, int]] = (),
) -> list[tuple[int, int]]:
    """Yalnızca bağımsız ``işbirliği``/``reklam`` kelimelerini ve hashtag'lerini bul."""
    spans: list[tuple[int, int]] = []
    for match in DISCLOSURE_TOKEN_RE.finditer(text or ""):
        if any(match.start() < end and start < match.end() for start, end in protected):
            continue
        spans.append(_with_adjacent_space(text, match.start(), match.end()))
    return _merge_spans(spans)


def clean_disclosure_tokens(text: str) -> str:
    """Disclosure etiketlerini kaldır; URL'lerin içindeki parçaları değiştirme."""
    text = text or ""
    url_spans: list[tuple[int, int]] = []
    for match in URL_RE.finditer(text):
        raw_url = match.group(0).rstrip(_URL_TAIL_TRIM)
        if raw_url:
            url_spans.append((match.start(), match.start() + len(raw_url)))
    return _remove_spans(text, _disclosure_spans(text, protected=url_spans))


def _word_matches(token: str, roots: Iterable[str]) -> bool:
    """Kelime kök sözlüğüyle eşleşiyor mu? (4+ harfli kökler önek, kısalar tam)."""
    word = normalize(token)
    for root in roots:
        if word == root or (len(root) >= 4 and word.startswith(root)):
            return True
    return False


def _promo_line_spans(text: str, entities: Sequence[Any] = ()) -> list[tuple[int, int]]:
    """Kanal tanıtımı ve salt hashtag satırlarının silinecek aralıkları.

    Kullanıcı isteği: bildirimde "💚Whatsapp Önemli Fırsatlar" ve
    "#amazon #indirimalarmi" gibi satırlar hiç görünmesin. Kurallar dardır;
    fiyat/ürün satırı taşıyan bir satır silinmez (bkz. PROMO_WORD_ROOTS).
    Silinecek aralık satır sonunu İÇERMEZ; artakalan boş satırlar
    ``_blank_line_spans`` ile sıkıştırılır. Görünen etikette Instagram yazmasa
    bile gerçek Instagram entity'si, genel tanıtım etiketini hedefli şekilde
    tanır (ör. Instagram'a bağlı ``Kanalımıza katılın``).
    """
    instagram_entity_spans: list[tuple[int, int]] = []
    for entity in entities:
        if not is_instagram_url(getattr(entity, "url", None)):
            continue
        try:
            offset = int(getattr(entity, "offset", 0) or 0)
            length = int(getattr(entity, "length", 0) or 0)
            start = _utf16_to_index(text, offset)
            end = _utf16_to_index(text, offset + length)
        except (TypeError, ValueError, AttributeError):
            continue
        if end > start:
            instagram_entity_spans.append((start, end))
    spans: list[tuple[int, int]] = []
    for match in re.finditer(r"[^\n]+", text or ""):
        raw_line = match.group(0)
        masked = URL_RE.sub(" ", raw_line)  # URL harfleri kelime sayılmasın
        words = WORD_TOKEN_RE.findall(masked)
        if not words:
            continue
        tagged = HASHTAG_WORD_RE.findall(masked)
        if tagged and len(tagged) == len(words):
            spans.append((match.start(), match.end()))  # yalnızca etiketler
            continue
        has_instagram_link = any(
            is_instagram_url(url.group(0)) for url in URL_RE.finditer(raw_line)
        ) or any(
            start < match.end() and end > match.start()
            for start, end in instagram_entity_spans
        )
        if not has_instagram_link \
                and not any(_word_matches(word, CHANNEL_WORD_ROOTS) for word in words):
            continue
        if all(_word_matches(word, PROMO_WORD_ROOTS) for word in words):
            spans.append((match.start(), match.end()))
    # Tüm içerik silinecekse (örn. mesaj yalnızca tanıtım satırlarından oluşuyorsa)
    # hiçbir şey silme: boş bildirim göndermektense satır kalsın.
    if spans and not any(char.isalnum() for char in _remove_spans(text, spans)):
        return []
    return spans


def _remap_entities(
    text: str,
    new_text: str,
    entities: Sequence[Any],
    removed_spans: Sequence[tuple[int, int]],
) -> list[Any]:
    """Metin temizlenince UTF-16 entity aralıklarını doğru yere kaydır."""
    result: list[Any] = []

    for entity in entities:
        old_offset = int(getattr(entity, "offset", 0) or 0)
        old_length = int(getattr(entity, "length", 0) or 0)
        if old_offset < 0 or old_length <= 0:
            continue
        old_start = _utf16_to_index(text, old_offset)
        old_end = _utf16_to_index(text, old_offset + old_length)
        start = _index_after_removals(old_start, removed_spans)
        end = max(start, _index_after_removals(old_end, removed_spans))
        if end <= start:
            continue
        try:
            cloned = copy.copy(entity)
            cloned.offset = utf16_length(new_text[:start])
            cloned.length = utf16_length(new_text[start:end])
        except Exception:  # noqa: BLE001 - entity bilinmiyorsa içeriği koruyup atla
            log.debug("Mesaj entity'si temizlenen metne taşınamadı: %s", type(entity).__name__)
            continue
        if cloned.length > 0:
            result.append(cloned)
    return result


def _sanitize_collect_removed_spans(text: str, entities: Sequence[Any]) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """WhatsApp/Instagram URL'leri, disclosure ve promo satırları için silinecek aralıkları topla."""
    removed: list[tuple[int, int]] = []
    visible_urls: list[tuple[int, int]] = []
    for match in URL_RE.finditer(text):
        raw_url = match.group(0).rstrip(_URL_TAIL_TRIM)
        if not raw_url:
            continue
        span = (match.start(), match.start() + len(raw_url))
        visible_urls.append(span)
        if is_whatsapp_url(raw_url) or is_instagram_url(raw_url):
            removed.append(_with_adjacent_space(text, *span))
    disclosure_spans = _disclosure_spans(text, protected=visible_urls)
    removed.extend(disclosure_spans)
    removed.extend(_promo_line_spans(text, entities))
    return removed, visible_urls


def _sanitize_apply_text_cleaning(text: str, entities: Sequence[Any], removed: list[tuple[int, int]]) -> tuple[str, list[Any], list[tuple[int, int]], list[tuple[int, int]]]:
    """Silinecek aralıkları metinden çıkar ve entity offset'lerini UTF-16 bazında kaydır."""
    removed_spans = _merge_spans(removed)
    cleaned_text = _remove_spans(text, removed_spans)
    remapped_entities = _remap_entities(text, cleaned_text, entities, removed_spans) if removed_spans else list(entities)
    blank_spans = _blank_line_spans(cleaned_text)
    if blank_spans:
        compact_text = _remove_spans(cleaned_text, blank_spans)
        remapped_entities = _remap_entities(cleaned_text, compact_text, remapped_entities, blank_spans)
        cleaned_text = compact_text
    return cleaned_text, remapped_entities, removed_spans, blank_spans


def _sanitize_filter_entities(remapped_entities: Sequence[Any]) -> list[Any]:
    """Gizli WhatsApp/Instagram entity'lerini temizlenmiş listeden çıkar."""
    return [
        entity for entity in remapped_entities
        if not is_whatsapp_url(getattr(entity, "url", None))
        and not is_instagram_url(getattr(entity, "url", None))
    ]


def _sanitize_collect_preserved_links(
    message: Any, text: str, entities: Sequence[Any],
    removed_spans: Sequence[tuple[int, int]], blank_spans: Sequence[tuple[int, int]],
) -> list[dict[str, str]]:
    """Silinen disclosure etiketi altında kalan gizli URL'leri preserved_links'e taşı."""
    preserved_links = list(getattr(message, "preserved_links", None) or [])
    for entity in entities:
        url = getattr(entity, "url", None)
        if not url or is_whatsapp_url(url) or is_instagram_url(url):
            continue
        old_start = _utf16_to_index(text, int(getattr(entity, "offset", 0) or 0))
        old_end = _utf16_to_index(text, int(getattr(entity, "offset", 0) or 0) + int(getattr(entity, "length", 0) or 0))
        new_start = _index_after_removals(old_start, removed_spans)
        new_end = _index_after_removals(old_end, removed_spans)
        if new_end - new_start < old_end - old_start:
            new_start = _index_after_removals(new_start, blank_spans)
            new_end = _index_after_removals(new_end, blank_spans)
        if old_end > old_start and new_end <= new_start:
            preserved_links.append({"url": clean_url(url), "label": "", "kind": "webpage"})
    return preserved_links


def _sanitize_dedup_preserved_links(preserved_links: list[dict[str, str]]) -> list[dict[str, str]]:
    """Aynı URL'nin tekrar taşınmasını ve Instagram hedeflerini engelle."""
    unique: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in preserved_links:
        if not isinstance(item, dict):
            continue
        url = clean_url(item.get("url"))
        key = url.rstrip("/").lower()
        if not url or is_instagram_url(url) or key in seen:
            continue
        seen.add(key)
        unique.append({"url": url, "label": str(item.get("label") or "").strip(), "kind": str(item.get("kind") or "webpage")})
    return unique


def _sanitize_clean_markup(markup: Any) -> tuple[Any | None, bool]:
    """Inline butonlardaki WhatsApp/Instagram URL'lerini ve disclosure etiketlerini temizle."""
    if markup is None:
        return None, False
    cleaned_rows: list[SimpleNamespace] = []
    markup_changed = False
    for row in getattr(markup, "rows", None) or []:
        cleaned_buttons: list[Any] = []
        for button in getattr(row, "buttons", None) or []:
            info = button_link(button)
            if info and (is_whatsapp_url(info[0]) or is_instagram_url(info[0])):
                markup_changed = True
                continue
            if not info:
                cleaned_buttons.append(button)
                continue
            label = str(getattr(button, "text", "") or "")
            new_label = clean_disclosure_tokens(label)
            if new_label != label:
                markup_changed = True
                clone = copy.copy(button)
                clone.text = new_label.strip() or "Bağlantı"
                cleaned_buttons.append(clone)
            else:
                cleaned_buttons.append(button)
        if cleaned_buttons:
            cleaned_rows.append(SimpleNamespace(buttons=cleaned_buttons))
        elif getattr(row, "buttons", None):
            markup_changed = True
    return SimpleNamespace(rows=cleaned_rows), markup_changed


def _sanitize_clean_media(media: Any) -> Any | None:
    """WhatsApp/Instagram webpage önizlemesini temizle; diğer medyayı koru."""
    webpage = getattr(media, "webpage", None)
    if webpage is not None and (is_whatsapp_url(getattr(webpage, "url", None)) or is_instagram_url(getattr(webpage, "url", None))):
        return None
    return media


def sanitize_message(obj: Any) -> SimpleNamespace:
    """İletilecek kopyayı temizle; kaynak Telethon mesajına hiçbir zaman dokunma.

    Kaldırılanlar: bağımsız ``#işbirliği``/``işbirliği``/``#reklam``/``reklam``
    ifadeleri, doğrudan WhatsApp/Instagram URL'leri, bu hedeflere ait gizli
    entity'ler/inline butonlar/önizlemeler ve kanal tanıtımı/salt hashtag
    satırları (``_promo_line_spans``). Instagram tanıtım satırları kaldırılır;
    diğer kanal ekleri, ürün metni, entity, medya ve linkler korunur.
    Silme işleminin artığında oluşan çoklu boş satırlar tek boş satıra indirilir
    (``_blank_line_spans``); bu adım yalnızca boşluk karakterlerini alır, metin
    içeriğini değiştirmez.
    """
    message = _as_message(obj)
    text = message_text(obj)
    entities = message_entities(message)

    removed, _ = _sanitize_collect_removed_spans(text, entities)
    cleaned_text, remapped_entities, removed_spans, blank_spans = _sanitize_apply_text_cleaning(text, entities, removed)
    cleaned_entities = _sanitize_filter_entities(remapped_entities)

    preserved_links = _sanitize_collect_preserved_links(message, text, entities, removed_spans, blank_spans)
    preserved_links = _sanitize_dedup_preserved_links(preserved_links)

    markup = getattr(message, "reply_markup", None)
    cleaned_markup, markup_changed = _sanitize_clean_markup(markup)

    media = _sanitize_clean_media(getattr(message, "media", None))

    changed = (
        cleaned_text != text
        or len(cleaned_entities) != len(entities)
        or markup_changed
        or media is not getattr(message, "media", None)
    )
    return SimpleNamespace(
        id=getattr(message, "id", None),
        message=cleaned_text,
        raw_text=cleaned_text,
        entities=cleaned_entities,
        reply_markup=cleaned_markup,
        media=media,
        preserved_links=preserved_links,
        file=getattr(message, "file", None),
        video=getattr(message, "video", None),
        changed=changed,
    )


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

    for item in getattr(message, "preserved_links", None) or []:
        if isinstance(item, dict):
            add(item.get("url"), item.get("label"), str(item.get("kind") or "webpage"))

    return found


def is_blank_reply(obj: Any) -> bool:
    """Başka bir mesaja YANIT olan ve ürün/fiyat/bağlantı taşıyamayan ileti mi?

    Kaynaklar kendi fırsatına açıklamasız görsel ya da çıkartmayla yanıt verir; bu
    ileti yalnız "Mesajı Gör" satırı ile kaynak adından oluşan boş bir bildirim
    üretiyordu. Fail-open: temizlenmiş metinde tek bir harf/rakam ya da herhangi
    türde bağlantı (gizli hyperlink, buton, önizleme, görünür URL) varsa ileti
    korunur; yanıt OLMAYAN açıklamasız görsel de (afiş bir fırsat olabilir) eskisi
    gibi iletilir. Forum konusunun kök bağlantısı yanıt sayılmaz.
    """
    header = getattr(_as_message(obj), "reply_to", None)
    if header is None or (getattr(header, "forum_topic", False)
                          and getattr(header, "reply_to_top_id", None) is None):
        return False
    cleaned = sanitize_message(obj)
    return not any(char.isalnum() for char in message_text(cleaned)) and not extract_links(cleaned)


def visible_urls(text: str) -> set[str]:
    """Metinde gözle görülen bağlantılar (bunları tekrar yazmaya gerek yok)."""
    return {clean_url(url).rstrip("/").lower() for url in URL_RE.findall(text or "")}


def missing_links(
    obj: Any,
    limit: int = LINK_APPENDIX_LIMIT,
    kinds: Sequence[str] | None = ("button", "webpage"),
    skip: Callable[[dict[str, str]], bool] | None = None,
) -> list[dict[str, str]]:
    """Metinde görünmeyen bağlantılar: gizli hyperlink, buton, link önizlemesi.

    ``kinds`` verilirse yalnızca o türler döner (bkz. ``LINK_KIND_GROUPS``).
    ``skip`` çağıranın zaten başka yerde taşıdığı bağlantıları eler. Eleme sınırdan
    ÖNCE yapılır: elenen bağlantı ``limit`` hakkı tüketmez, arkasındaki gerçek
    bağlantı düşmez.
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
        if skip is not None and skip(item):
            continue
        result.append(item)
        if len(result) >= limit:
            break
    return result


def link_appendix_lines(
    obj: Any,
    kinds: Sequence[str] | None = ("button", "webpage"),
    *,
    exclude_urls: Iterable[str] = (),
    has_keyboard: bool = False,
) -> list[tuple[str, bool]]:
    """Gizli bağlantıların ``🔗`` satırları: ``(satır, vazgeçilmez mi?)``.

    Başka yerde açılabilen adres satır olmaz: ``exclude_urls`` çağıranın kendi
    taşıdığı adreslerdir (``👉 Ürün Linki 👈`` satırı); kaynağın Google/Akakçe/Cimri
    arama butonu ise ileti inline klavyeyle gidiyorsa (``has_keyboard``; bot bildirimi)
    orada düğme olarak kalır. Klavyesi olmayan hesap kopyasında arama butonunun tek
    taşıyıcısı bu satırdır; o yüzden orada yazılır.

    Bu elemeler sınırdan önce yapıldığı için elenen bağlantı satır hakkı tüketmez.
    Mesajın ilk ``LINK_APPENDIX_LIMIT`` gizli bağlantısı (hiç eleme olmasaydı
    yazılacak olanlar) vazgeçilmezdir. Eleme sayesinde yer bulan sonraki bağlantılar
    ve klavyede de durabilen buton satırları yedeklidir: ``compose_message`` bunları
    yalnız gövdeyi kırpmayacaksa yazar.
    """
    excluded = {clean_url(url).rstrip("/").lower() for url in exclude_urls if url}

    def carried_elsewhere(item: dict[str, str]) -> bool:
        if item["url"].rstrip("/").lower() in excluded:
            return True
        return has_keyboard and item["kind"] == "button" and is_search_link(item["url"])

    first = {item["url"].rstrip("/").lower() for item in missing_links(obj, kinds=kinds)}
    lines: list[tuple[str, bool]] = []
    for item in missing_links(obj, kinds=kinds, skip=carried_elsewhere):
        label = (item.get("label") or "").strip()
        text = (f"🔗 {label[:40]}: {item['url']}" if label and label.lower() not in item["url"].lower()
                else f"🔗 {item['url']}")
        required = item["url"].rstrip("/").lower() in first and not (has_keyboard and item["kind"] == "button")
        lines.append((text, required))
    return lines


def build_link_appendix(
    obj: Any,
    kinds: Sequence[str] | None = ("button", "webpage"),
    *,
    exclude_urls: Iterable[str] = (),
    has_keyboard: bool = False,
) -> str:
    """Gizli bağlantıları iletinin sonuna ekle; başka yerde açılabilen adresi yineleme.

    Satır kuralları için bkz. ``link_appendix_lines``.
    """
    return "\n".join(
        text for text, _ in link_appendix_lines(obj, kinds, exclude_urls=exclude_urls, has_keyboard=has_keyboard))


def is_search_link(url: Any) -> bool:
    """Adres bir fiyat/web araması mı (Google, Akakçe, Cimri)? Etikete bakılmaz."""
    cleaned = clean_url(url)
    if _price_search_service(cleaned):
        return True
    try:
        parsed = urllib.parse.urlsplit(cleaned)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    # Gövdedeki "🔎 Google" web aramasıdır; play.google.com gibi mağazalar ürün kalır.
    return host.removeprefix("www.") in {"google.com", "google.com.tr"} and parsed.path.startswith("/search")


def _is_tracking_query(query: str) -> bool:
    """Sorgu yalnız izleme/iş ortaklığı etiketi mi taşıyor? (bkz. ``TRACKING_QUERY_KEYS``)

    Fail-open: tanınmayan tek bir anahtar ya da değeri adres olan bir parametre
    (yönlendirme hedefi) varsa sorgu içerik sayılır.
    """
    return all(
        "://" not in value
        and (key.lower() in TRACKING_QUERY_KEYS or key.lower().startswith(TRACKING_QUERY_PREFIXES))
        for key, value in urllib.parse.parse_qsl(query, keep_blank_values=True)
    )


def is_product_link(url: Any) -> bool:
    """Bağlantı ürün/fırsat sayfasına götürebilir mi? (bkz. ``NON_PRODUCT_HOSTS``)

    Yolu olmayan adres (``https://www.amazon.com.tr``) mağazanın ana sayfasıdır; "🛍 Amazon"
    gibi mağaza adına gömülü böyle bir link ürün linki sayılmaz. Ana sayfaya eklenen
    iş ortaklığı/izleme etiketi (``/?tag=kanal-21``, ``/?utm_source=…``) bunu değiştirmez.
    Doğrudan görsel/video dosyası da (``…/urun.jpg``) ürün sayfası değildir.
    """
    cleaned = clean_url(url)
    if not cleaned or is_search_link(cleaned) or is_whatsapp_url(cleaned) or is_instagram_url(cleaned):
        return False
    try:
        parsed = urllib.parse.urlsplit(cleaned)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    return (parsed.scheme in {"http", "https"} and "." in host
            and bool(parsed.path.strip("/") or not _is_tracking_query(parsed.query))
            and not MEDIA_FILE_PATH_RE.search(parsed.path)
            and not NON_PRODUCT_HOST_RE.search(host)
            and not any(host == item or host.endswith("." + item) for item in NON_PRODUCT_HOSTS))


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


# Fiyatlar ve indirim yüzdeleri ürün adı değildir; bazı kaynaklar bunları
# başlığın önüne, bazıları arkasına yazar. Arama sorgusunda yalnızca satırdaki
# ürün metni kalır. Bu işlem tamamen yereldir (ağ isteği yoktur).
_SEARCH_AMOUNT = r"(?:\d{1,3}(?:[.,\s]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
_SEARCH_CURRENCY = r"(?:YTL|TRY|TL|USD|EUR|₺|[$€£])"
_SEARCH_PRICE_RE = re.compile(
    rf"(?<![\w])(?:{_SEARCH_CURRENCY}\s*{_SEARCH_AMOUNT}|"
    rf"{_SEARCH_AMOUNT}\s*{_SEARCH_CURRENCY})(?![\w])",
    re.IGNORECASE,
)
_SEARCH_DISCOUNT_RE = re.compile(
    r"(?<![\w])(?:(?:%|yüzde|yuzde)\s*\d{1,3}(?:[.,]\d+)?"
    r"(?:['’]?(?:ya|ye|a|e|ı|i|u|ü))?|"
    r"\d{1,3}(?:[.,]\d+)?\s*%(?:['’]?(?:ya|ye|a|e|ı|i|u|ü))?)"
    r"(?:\s*(?:indirim(?:li)?|discount|off|tasarruf))?",
    re.IGNORECASE,
)
_SEARCH_NOISE_WORDS = frozenset(normalize(word) for word in """
    fiyat fiyatı fiyati fiyatlar price pricing tl try ytl usd eur
    indirim indirimli indirimde indirimden discount discounts off tasarruf savings
    yüzde yuzde oran oranı orani percent
    kampanya kampanyası kampanyasi kampanyalı kampanyali campaign fırsat firsat
    fırsatı firsati fırsata firsata fırsatın firsatin deal
    sepette sepete kupon kuponu kuponla coupon voucher kod kodu promo
    bugün bugun şimdi simdi güncel guncel son eski yeni düşen dusen düştü dustu
    düşüş dusus varan kadar yerine daha ucuza ucuzladı ucuzladi
    stok stokta stoklarla sınırlı sinirli geçerli gecerli
    taksit taksitle peşin pesin kargo ücretsiz ucretsiz bedava
    ürün ürünü urun urunu ürüne urune başlık baslik özellik özelliği ozellik detay
    link linki linkten product ürünler urunler
    hemen kaçmaz kacmaz kaçırma kacirma tıkla tikla satın satin
    alırken alirken oranında oraninda
    whatsapp whatsapptan whatsapp'tan reklamcı reklamci reklam bilgi gizli bağlantı baglanti
    bağlantısı baglantisi burada
    den ye
""".split())
_SEARCH_CTA_ONLY_WORDS = frozenset({"al", "bak", "git", "gor", "gör", "incele", "tikla", "tıkla"})


def _search_line_candidate(raw_line: str) -> str:
    """Fiyat/indirim/CTA satırlarını ayıkla, ürün adı olabilecek metni döndür."""
    if re.match(r"^\s*📂\ufe0f?", raw_line or ""):
        return ""
    line = URL_RE.sub(" ", raw_line or "")
    line = clean_disclosure_tokens(line)
    line = price_filter.strip_store_prefix(line)
    # Hashtag-only tanıtım satırları sanitize_message tarafından kaldırılır;
    # başlıkta gerçek bir ürün adı varsa kelimeyi koruyup # işaretini sil.
    line = re.sub(r"(?<!\w)#(?=\w)", "", line)
    line = _SEARCH_PRICE_RE.sub(" ", line)
    line = _SEARCH_DISCOUNT_RE.sub(" ", line)
    def remove_search_noise(match: re.Match[str]) -> str:
        token = normalize(match.group())
        # "link" is CTA noise as a standalone word, but part of the brand
        # TP-Link when hyphenated. Preserve that one compound brand token.
        hyphenated_link = (
            token == "link" and match.start() > 1 and line[match.start() - 1] == "-"
            and line[match.start() - 2].isalpha()
        )
        return " " if not hyphenated_link and (
            token in _SEARCH_NOISE_WORDS or token in _SEARCH_CTA_ONLY_WORDS
        ) else match.group()

    line = WORD_TOKEN_RE.sub(remove_search_noise, line)
    line = _LEADING_NOISE_RE.sub("", line)
    line = re.sub(r"[^\w\s&+.,;:'’/–—-]+", " ", line, flags=re.UNICODE)
    line = re.sub(r"[()\[\]{}]+", " ", line)
    line = re.sub(r"\s+", " ", line).strip(" \t\r\n,;:.|/\\-–—·•")
    if not any(char.isalpha() for char in line):
        return ""
    tokens = [normalize(token) for token in WORD_TOKEN_RE.findall(line)]
    if tokens and set(tokens) <= _SEARCH_CTA_ONLY_WORDS:
        return ""
    return line


# Arama sorgusu ve bildirim başlığı en fazla bu uzunlukta tutulur.
SEARCH_QUERY_LIMIT = 200


# Fiyat/indirim/tanıtım başlığı kelimeleri: yalnızca bunlardan oluşan satır
# ürün adı değildir (ör. "DÜŞÜŞÜ", "FİYAT DÜŞÜŞÜ", "Fark", "En düşük fiyat").
_SEARCH_DEAL_STEMS = ("düş", "dus", "indir", "fiyat", "fark", "dibi", "zirve",
                      "tasarruf", "ucuz", "en düşük", "en dusuk")


def _search_is_deal_word(word: str) -> bool:
    normalized = normalize(word)
    return any(normalized.startswith(normalize(stem)) for stem in _SEARCH_DEAL_STEMS)


def _search_candidate_score(candidate: str) -> int:
    """Bir aday satırın ürün adı olma olasılığını yerel olarak puanlar.

    Ağ isteği ya da ek ayrıştırma yoktur; yalnızca satır metni incelenir.
    """
    words = [word for word in WORD_TOKEN_RE.findall(candidate)]
    score = 0
    if len(words) >= 2:
        score += 1
    else:
        # Tek kelimelik satırlar çoğunlukla mağaza/kategori etiketidir
        # ("🛍️ Amazon", "📂 Süpermarket"); ürün adı ise genelde çok kelimelidir.
        score -= 1
    if ":" in candidate:
        # "Amazon: 60 günün en düşük" gibi etiketli/istatistik satırları.
        score -= 2
    if words and all(_search_is_deal_word(word) for word in words):
        # Yalnızca fiyat/indirim kelimelerinden oluşan başlık ("DÜŞÜŞÜ").
        score -= 3
    return score


def linked_product_title(obj: Any, limit: int = 160) -> str:
    """Recognize a product-name hyperlink, not CTA or search links.

    Entity offsets are UTF-16. Only a single unambiguous, model-bearing label
    spanning at least four words is trusted; otherwise keep normal extraction.
    """
    text = message_text(obj)
    candidates = []
    for entity in message_entities(_as_message(obj)):
        url = getattr(entity, "url", None)
        if not url or not URL_RE.match(str(url)) or _price_search_service(str(url), ""):
            continue
        label = utf16_slice(text, getattr(entity, "offset", 0), getattr(entity, "length", 0)).strip()
        if (len(label.split()) < 4 or not price_filter._MODEL_RE.search(label)
                or price_filter.PRICE_RE.search(label) or "\n" in label
                or price_filter.is_generic_title(label)):
            continue
        title = price_filter.extract_title(label, limit=limit)
        if title:
            candidates.append(title)
    return candidates[0] if len(candidates) == 1 else ""


def _coupon_codes(text: str) -> list[str]:
    """Return explicit uppercase promo codes, excluding ordinary words near "kod"."""
    code_pattern = r"[A-Z0-9İĞÜŞÖÇ]{4,}"
    patterns = (
        rf"\b({code_pattern})\s+(?i:kod(?:u|uyla|unu)?|kuponu)\b",
        rf"(?i:\b(?:kod(?:u|uyla|unu)?|kupon(?:u)?)\s*[:：]?\s*)({code_pattern})\b",
        rf"\b({code_pattern})\s*\(\s*kopyalamak\s+için\s+dokun\s*\)",
    )
    codes = [match.group(1) for pattern in patterns for match in re.finditer(pattern, text or "")]
    generic_labels = {"INDIRIM", "İNDİRİM", "KUPO", "KUPON", "COUPON", "KOD", "KODU"}
    codes = [code for code in codes if code.upper() not in generic_labels]
    return list(dict.fromkeys(codes))


def _coupon_campaign_title(
    text: str, limit: int, *, include_code: bool = False,
    allow_code_only_marker: bool = False,
) -> str:
    """Return a complete coupon/campaign heading, not a clipped teaser.

    Coupon-only announcements often contain only eligibility and discount
    amounts, not a product price. Product/model-bearing messages remain on the
    normal title path so coupon metadata cannot replace a real product name.
    """
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    campaign_marker = r"\b(?:kupon\w*|indirim\s*kodu|kampanya\s*kodu)\b"
    if allow_code_only_marker:
        campaign_marker = r"\b(?:kupon\w*|kod\w*|indirim\s*kodu|kampanya\s*kodu)\b"
    if not re.search(campaign_marker, _campaign_norm(text)):
        return ""
    product_line = False
    for line in lines:
        title_markers = [match.start() for pattern in (
            price_filter._UNIT_RE, price_filter._MODEL_RE,
        ) if (match := pattern.search(line))]
        title_markers.extend(match.start() for match in re.finditer(r"\d{7,}|\d+\.\d+-\d+", line))
        marker = min(title_markers, default=len(line))
        title_prefix = line[:marker]
        if (
            len(WORD_TOKEN_RE.findall(_search_line_candidate(line))) >= 5
            and (
                (not re.search(r"kopyalamak|alışveriş|alisveris|üzeri|indirim|kupon|kod", normalize(line))
                 and bool(title_markers))
                or (bool(price_filter._UNIT_RE.search(line))
                    and len(WORD_TOKEN_RE.findall(_search_line_candidate(title_prefix))) >= 3
                    and not re.search(r"indirim|kupon|kod", normalize(title_prefix)))
            )
        ):
            product_line = True
            break
    if product_line:
        return ""

    candidates: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        # Preserve currency/percent/coupon words: generic product-title cleanup
        # correctly removes these from product titles but must not clip offers.
        candidate = clean_disclosure_tokens(line)
        candidate = re.sub(r"[^\w\s%₺.,:'’/-]+", " ", candidate, flags=re.UNICODE)
        candidate = re.sub(r"\s+", " ", candidate).strip(" \t,;:.|/-")
        plain = normalize(candidate)
        has_amount = bool(re.search(r"(?:\d[\d.,]*\s*(?:tl|₺|try)|%\s*\d)", normalize(line)))
        has_campaign_word = bool(re.search(r"\b(?:indirim|ındırım)\w*\b", _campaign_norm(candidate)))
        category_coupon_line = bool(
            re.search(r"\b(?:kupon|coupon)\w*\b", plain)
            and index > 0
            and re.search(r"linkteki|kategori", normalize(lines[index - 1]))
        )
        percent_coupon_line = bool(
            re.search(r"\bkupon\w*\b", plain)
            and re.search(r"%\s*\d", normalize(line))
            and re.search(r"\bek\b", plain)
        )
        if candidate and has_amount and (has_campaign_word or category_coupon_line or percent_coupon_line) and len(WORD_TOKEN_RE.findall(candidate)) >= 3:
            candidates.append((index, candidate))

    if not candidates:
        return ""

    # Prefer the most self-contained threshold/discount sentence.
    selected_index, selected = max(
        candidates,
        key=lambda item: (len(WORD_TOKEN_RE.findall(item[1])), len(item[1])),
    )
    # A category context immediately before a short coupon line is part of the
    # offer, not a product name (e.g. “Sofra Mutfak / 75 TL kupon”).
    if selected_index > 0 and re.search(r"linkteki|kategori", normalize(lines[selected_index - 1])):
        prefix = _search_line_candidate(lines[selected_index - 1])
        if prefix and normalize(prefix) not in normalize(selected):
            selected = f"{prefix} {selected}"

    category_line = next((line for line in lines if re.search(r"\bkategori\s*[:：]", line, re.I)), "")
    category_match = re.search(r"\bkategori\s*[:：]\s*(.+)$", category_line, re.I)
    category = _search_line_candidate(category_match.group(1)) if category_match else ""
    if category and normalize(category) not in normalize(selected):
        selected = f"{category} {selected}"
    if include_code:
        codes = _coupon_codes(text)
        missing_codes = [code for code in codes if normalize(code) not in normalize(selected)]
        if missing_codes:
            selected += " — " + ", ".join(missing_codes)
    if not re.search(r"\bamazon\b", normalize(selected)) and any(
        re.search(r"\bamazon\b", normalize(line))
        and re.search(r"\b(?:indirim|kupon|kod)\b", normalize(line))
        for line in lines
    ):
        selected = "Amazon " + selected
    return selected[:limit].strip()


def _coupon_preview_message(text: str, campaign_title: str) -> bool:
    """True when the source starts with a code card or a genuinely clipped teaser."""
    first = next((line.strip() for line in (text or "").splitlines() if line.strip()), "")
    if re.fullmatch(r"[A-Z0-9İĞÜŞÖÇ]{4,}\s*\(\s*kopyalamak\s+için\s+dokun\s*\)\s*❓?", first):
        return True
    # Kupon kodu ÇIPLAK bir satırda durabilir ("FIRSAT1000"), kampanya cümlesi
    # ise ilk satırdadır. Kart biçimi (kopyalamak için dokun) olmasa da tek
    # başına duran kod satırı, iletinin kupon duyurusu olduğunu gösterir;
    # eskiden yalnız ilk satır incelendiği için bu düzen kaçıyor ve başlığa
    # gövdeden rastgele bir satır ("hesabınızda aktif etmek …") düşüyordu.
    first_title_tokens = {normalize(token) for token in WORD_TOKEN_RE.findall(first)}
    campaign_tokens_all = {normalize(token) for token in WORD_TOKEN_RE.findall(campaign_title)}
    if campaign_title and first_title_tokens and first_title_tokens <= campaign_tokens_all:
        for line in (text or "").splitlines():
            token = line.strip()
            if not re.fullmatch(r"[A-Z0-9İĞÜŞÖÇ]{4,}", token):
                continue
            if normalize(token) in {normalize(code) for code in _coupon_codes(text)}:
                return True
    if "❓" not in first or not campaign_title:
        return False
    first_tokens = {normalize(token) for token in WORD_TOKEN_RE.findall(first)}
    campaign_tokens = {normalize(token) for token in WORD_TOKEN_RE.findall(campaign_title)}
    shared = first_tokens & campaign_tokens
    clipped_to_fragment = len(first_tokens) <= 2 and len(campaign_tokens) >= 6
    return bool((len(shared) >= 4 and len(campaign_tokens) >= len(shared) + 2)
                or clipped_to_fragment)


# Noktasız büyük ``I`` Türkçe küçültmede ``ı`` olur (``Indirim`` → ``ındirim``)
# ve ``indirim`` deseni kaçar. ``normalize`` davranışı BİLEREK değiştirilmiyor
# (arama sorgusu, eşleştirme, dedup ondan besleniyor); yalnız kampanya
# sözcüklerini tanıyan bu yardımcıda katlama yapılıyor.
_DOTLESS_I_FOLD = str.maketrans({"ı": "i"})


def _campaign_norm(text: str) -> str:
    """``normalize`` + noktasız ``ı`` katlaması (yalnız kampanya sözcükleri)."""
    return normalize(text).translate(_DOTLESS_I_FOLD)


# Kupon/kod olmadan da yayınlanan kampanya kalıpları: "2 Öde 1'i Money
# Hediye", "Aynı üründen 2 alana biri tutarında Money iade". Kullanıcı
# bildirimi: bu iletiler kupon duyurusudur, ürün fiyatı beklenmez.
# Not: "2 alana 1 TL" gibi kalıplar BİLEREK dışarıda — mevcut davranış o
# iletilerde başlığı `❓` ile bırakıyor ve o davranış korunuyor.
_NON_COUPON_CAMPAIGN_RE = re.compile(
    r"\b\d+\s*öde\s*\d+['’]?[iı]?\b|\bmoney\b|"
    r"\b(?:biri|bir)\s*tutar[ıi]nda\b",
    re.UNICODE,
)


def _has_safe_price(text: str) -> bool:
    """Metinde üst satıra yazılabilecek GÜVENLİ bir ürün fiyatı var mı?

    Bu ölçüm kupon sınıflandırmasından **bağımsızdır**: doğrudan
    ``price_filter.extract_price`` sonucuna bakar. Amaç, "fiyat var mı" kararının
    iki ayrı yerde (dal sırası ve kupon tespiti) yeniden yorumlanmasını önlemek.
    ``_is_coupon_announcement`` bu yardımcıyı kullanır; böylece fiyatlı gönderi,
    dalların sırası değişse bile kupon sayılmaz.
    """
    info = price_filter.extract_price(text)
    return info.value is not None and info.confidence in {"high", "medium"}


def _is_coupon_announcement(text: str) -> bool:
    """İleti bir kupon/kampanya duyurusu mu (ürün fiyatı beklenmez)?

    Üç ölçüt: (a) güvenli ürün fiyatı YOK, (b) kupon/kod sözcüğü geçmeyen
    kampanya kalıbı (``2 Öde 1'i Money Hediye``) VAR ya da tam bir kampanya
    cümlesi kurulabiliyor (eşik + indirim tutarı).

    Fiyat koşulu burada, fonksiyonun İÇİNDE aranır — çağıranın dal sırasına
    güvenilmez. Kullanıcı şartı: "fiyat olan bir gönderiyi kupon sanıp çöpe
    atmamalıyız". Üst satırdaki ``❓``, fiyatı OLMAYAN duyurularda yanlış bilgi
    olduğu için kaldırılır; fiyatı olan gönderide ise fiyat her koşulda korunur.
    """
    if _has_safe_price(text):
        return False
    # Kampanya sözcüğü ("kupon", "indirim kodu") hiç geçmeyen duyurular da var:
    # "Migros'ta … 2 Öde 1'i Money Hediye". Bunlar da ürün fiyatı taşımaz;
    # aksi hâlde başlıkta haksız bir ``❓`` kalıyordu.
    if _NON_COUPON_CAMPAIGN_RE.search(_campaign_norm(text)):
        return True
    campaign = _coupon_campaign_title(
        text, MESSAGE_LIMIT, include_code=True, allow_code_only_marker=True,
    )
    if not campaign:
        return False
    # Kampanya cümlesi VARSA ve güvenli ürün fiyatı YOKSA ileti duyurudur.
    # "Fiyat yok" koşulu yukarıda bir kez, bu fonksiyonun içinde ölçülür; çağıran
    # dalın önce çalışmasına bağlı DEĞİLDİR (eskiden öyleydi ve sessiz bir
    # kırılganlıktı: dal sırası değişirse fiyat kaybolabilirdi).
    return True


def _is_coupon_teaser_title(title: str) -> bool:
    """Recognize non-product previews such as promo-code cards/category coupons."""
    plain = normalize(title)
    first = next((line.strip() for line in (title or "").splitlines() if line.strip()), "")
    code_preview = bool(re.fullmatch(
        r"[A-Z0-9İĞÜŞÖÇ]{4,}\s*\(\s*kopyalamak\s+için\s+dokun\s*\)\s*❓?", first,
    ))
    category_coupon = bool(re.search(r"\blinkteki\b.*\b(?:ürünlerinde|urunlerinde)\b", plain))
    return code_preview or category_coupon


def _campaign_title_if_clipped(
    text: str, title: str, limit: int, *, include_code: bool = False,
    allow_code_only_marker: bool = False,
) -> str:
    """Use a complete coupon sentence only when the chosen title is its prefix fragment."""
    campaign = _coupon_campaign_title(
        text, limit, include_code=include_code,
        allow_code_only_marker=allow_code_only_marker,
    )
    if not campaign:
        return ""
    # A bare product price followed by generic coupon instructions is not a
    # complete campaign title. Require an eligibility threshold or both the
    # basket minimum and discount amounts before replacing a teaser.
    amounts = re.findall(r"\d[\d.,]*\s*(?:tl|₺|try)(?![a-z])", normalize(campaign))
    has_threshold = bool(re.search(
        r"\b(?:ve\s+)?(?:uzeri|üstü|alisveris|harcama|kategorisinde|kategorilerinde)\b",
        normalize(campaign),
    ))
    if not has_threshold and len(amounts) < 2:
        return ""
    title_tokens = {normalize(token) for token in WORD_TOKEN_RE.findall(title or "")}
    campaign_tokens = {normalize(token) for token in WORD_TOKEN_RE.findall(campaign)}
    # Don't replace a distinct product name. A clipped preview title is a strict
    # subset of the complete sentence and is materially shorter.
    if title_tokens and title_tokens <= campaign_tokens and len(campaign_tokens) >= len(title_tokens) + 2:
        return campaign
    if include_code and title_tokens:
        base_campaign = _coupon_campaign_title(
            text, limit, allow_code_only_marker=allow_code_only_marker,
        )
        base_tokens = {normalize(token) for token in WORD_TOKEN_RE.findall(base_campaign)}
        if title_tokens == base_tokens and campaign_tokens > base_tokens:
            return campaign
    return ""


def _coupon_dedup_identity(text: str) -> str:
    """Canonical identity for coupon-only campaigns; stable across copy variants."""
    campaign = _coupon_campaign_title(
        text, MESSAGE_LIMIT, include_code=True, allow_code_only_marker=True,
    )
    codes = _coupon_codes(text)
    if not campaign or not codes:
        return ""
    merchant = next((store for store in sorted(price_filter.STORES, key=len, reverse=True)
                     if re.search(rf"\b{re.escape(normalize(store))}\b", normalize(text))), "")
    category_line = next((line for line in (text or "").splitlines()
                          if re.search(r"\bkategori\s*[:：]", line, re.I)), "")
    category_match = re.search(r"\bkategori\s*[:：]\s*(.+)$", category_line, re.I)
    category = _search_line_candidate(category_match.group(1)) if category_match else ""
    amounts = []
    for shown in re.findall(r"\d[\d.,]*\s*(?:tl|try|₺)", normalize(campaign)):
        value = price_filter.parse_threshold(shown)
        if value is not None:
            amounts.append(f"{value:.2f}")
    return "Kampanya " + " ".join(
        part for part in (merchant, category, *codes, *amounts)
        if part and normalize(part) not in {"indirim", "kupon", "coupon", "kod", "kodu"}
    )


def _search_query(obj: Any, limit: int = SEARCH_QUERY_LIMIT) -> str:
    """Farklı kaynak şablonlarından ürün adını bulup arama sorgusu kur.

    Fiyat/indirim satırları atlanır. Adaylar arasından en yüksek puanlı olan
    seçilir (eşitlikte en üstteki kazanır; tüm adaylar zayıfsa ilk aday
    korunur, yani eski davranış bozulmaz). Metin değiştirilmez, servise istek
    atılmaz ve bildirim yoluna ek gecikme getirmez.
    """
    linked = linked_product_title(obj, limit=limit)
    if linked:
        return linked
    catalog = price_filter.priced_catalog_title(message_text(obj))
    if catalog:
        return price_filter.extract_title(catalog, limit=limit)
    raw_text = message_text(obj)
    campaign_title = _coupon_campaign_title(raw_text, limit)
    if campaign_title:
        return campaign_title
    text = URL_RE.sub(" ", raw_text)
    best_candidate = ""
    best_score = None
    for raw_line in text.splitlines():
        candidate = _search_line_candidate(raw_line)
        if not candidate or price_filter.is_generic_title(candidate):
            continue
        score = _search_candidate_score(candidate)
        # ``✅`` ile işaretlenen, sayısal metadata taşımayan açıklayıcı ürün
        # satırı kupon talimatına karşı eşitlikte kalmasın. Başlık ayıklayıcıdaki
        # dar kuralın arama sorgusundaki karşılığıdır.
        if raw_line.lstrip().startswith("✅") \
                and len(WORD_TOKEN_RE.findall(candidate)) >= 4 \
                and not any(char.isdigit() for char in candidate):
            score += 2
        if best_score is None or score > best_score:
            best_candidate, best_score = candidate, score
    best_candidate = best_candidate[:limit].strip()
    complete_campaign = _campaign_title_if_clipped(
        raw_text, best_candidate, limit, allow_code_only_marker=True,
    )
    return complete_campaign or best_candidate


PRICE_LINE_LABEL = "Fiyat:"
# Eski biçimin link etiketi; yalnızca geriye dönük tanıma için tutulur
# (eski bildirimlerin tekrar önbelleği hâlâ okunabilir).
PRODUCT_LINK_LABEL = "🔗 Ürün fırsat linki:"


def is_price_line(line: str | None) -> bool:
    """Satır bir fiyat satırı mı? Baştaki emoji/sembol (``💰``) hoş görülür.

    Yalnızca geriye dönük tanıma için tutulur: eski bildirimlerdeki
    ``💰Fiyat: …`` satırları ve rozet konumları bu sayede okunabilir kalır.
    Yeni bildirimlerde ayrı bir fiyat satırı yoktur; fiyat, ürün adıyla birlikte
    üst satıra emojisiz yazılır (``₺56 — Ürün adı``; bkz. ``build_header_line``)
    ve mesajın gövdesi olduğu gibi iletilir.
    """
    content = _LEADING_NOISE_RE.sub("", (line or "").strip())
    return content.startswith(PRICE_LINE_LABEL)


def _price_search_service(url: str, label: str = "") -> str | None:
    """Bir URL'nin Google Shopping, Akakçe veya Cimri olduğunu belirle."""
    try:
        parsed = urllib.parse.urlsplit(clean_url(url))
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().rstrip(".")
    path = parsed.path.lower()
    query = urllib.parse.parse_qs(parsed.query)
    label_lower = (label or "").casefold()
    if (
        host == "akakce.com" or host.endswith(".akakce.com")
        or "akakçe" in label_lower or "akakce" in label_lower
    ):
        return "akakce"
    if (
        host == "cimri.com" or host.endswith(".cimri.com")
        or "cimri" in label_lower
    ):
        return "cimri"
    if (
        host == "google.com" or host.endswith(".google.com")
        or host == "google.com.tr" or host.endswith(".google.com.tr")
    ):
        shopping_url = (
            path.startswith("/shopping")
            or "shop" in query.get("tbm", [])
            or "28" in query.get("udm", [])
            or "shopping" in label_lower
            or "alışveriş" in label_lower
        )
        if shopping_url:
            return "google_shopping"
    if "google shopping" in label_lower or "google alışveriş" in label_lower:
        return "google_shopping"
    return None


# Cimri sorgusu KISALTILMAZ: ürün başlığının tamamı aranır. Daha önce uzun
# sorguların Cloudflare WAF'ına takıldığı sanılıyordu ve sorgu ilk iki
# kelimeye iniyordu; denemede sorunun Telegram'ın dahili tarayıcısında olduğu,
# aynı adres dış tarayıcıda açılınca tam sorguyla arama yaptığı görüldü
# (kullanıcı isteği: "ilk iki kelimeyi aramayı bıraksın, tüm ürün başlığını
# arasın"). Google Alışveriş ve Akakçe zaten tam sorgu kullanıyordu.


def _cimri_search_query(query: str) -> str:
    """Cimri sorgusu: ürün başlığının tamamı (yalnızca boşluklar sıkıştırılır)."""
    return " ".join((query or "").split())


def _price_search_url(service: str, query: str) -> str:
    """Ürün arama adresini kur (query başlık ayıklayıcısından gelir).

    ``header_search_title`` çağrısının tek nedeni bildirim ÜST SATIRININ
    (``₺56 — Ad``, ``❓ — Ad``) arama sorgusuna karışmasını imkânsız
    kılmaktır: tutar, para birimi, ayraç ve ``❓`` burada her koşulda düşer,
    servise salt ürün adı gider.
    """
    query = header_search_title(query)
    if service == "akakce":
        return "https://www.akakce.com/arama/?" + urllib.parse.urlencode({"q": query})
    if service == "google_shopping":
        params = urllib.parse.urlencode((
            ("udm", "28"), ("q", query), ("hl", "tr"), ("gl", "tr"),
        ))
        return "https://www.google.com/search?" + params
    if service == "cimri":
        # Cimri'nin arama sonuç sayfası bu yapıyı kullanıyor. Fiyat artan
        # sıralaması en ucuz teklifi üste taşır; virgül URL'de sabit bırakılır.
        short = _cimri_search_query(query) or query
        return "https://www.cimri.com/arama?sort=price,asc&q=" + urllib.parse.quote_plus(short)
    raise ValueError(f"Bilinmeyen fiyat arama hizmeti: {service}")


def build_inline_keyboard(obj: Any, *, body_text: str | None = None,
                          body_entities: Sequence[dict[str, Any]] = ()) -> dict | None:
    """Remove a source button only when its URL survives in the delivered body."""
    body_urls = visible_urls(body_text or "")
    body_urls.update(clean_url(e.get("url")).rstrip("/").lower()
                     for e in body_entities if e.get("type") == "text_link" and e.get("url"))
    message = sanitize_message(obj)
    markup = getattr(message, "reply_markup", None)
    rows: list[list[dict[str, str]]] = []
    search_buttons: dict[str, list[dict[str, str]]] = {
        service: [] for service, _ in PRICE_SEARCH_BUTTONS
    }
    existing_button_count = 0
    for row in getattr(markup, "rows", None) or []:
        buttons: list[dict[str, str]] = []
        for button in getattr(row, "buttons", None) or []:
            info = button_link(button)
            if not info:
                continue
            url, label = info
            item = {"text": (label or url)[:64], "url": url}
            existing_button_count += 1
            service = _price_search_service(url, label or "")
            if not service and body_text is not None and clean_url(url).rstrip("/").lower() in body_urls:
                existing_button_count -= 1
                continue
            if service in search_buttons:
                search_buttons[service].append(item)
            else:
                buttons.append(item)
        # Kaynak butonları kendi sıraları ve satır gruplarıyla korunur.
        if buttons:
            rows.append(buttons)

    present_services = {
        service
        for link in extract_links(message)
        if (service := _price_search_service(link["url"], link.get("label") or ""))
    }
    query = header_search_title(_search_query(message))
    available_slots = max(0, MAX_INLINE_KEYBOARD_BUTTONS - existing_button_count)
    generated = 0
    search_row: list[dict[str, str]] = []
    for service, label in PRICE_SEARCH_BUTTONS:
        # Kaynaktaki bilinen arama butonu korunur, ancak standart sıraya alınır.
        if search_buttons[service]:
            search_row.extend(search_buttons[service])
            continue
        # Gövdedeki/gizli bağlantı aynı hizmeti zaten sunuyorsa çoğaltma.
        if service in present_services or not query or generated >= available_slots:
            continue
        search_row.append({"text": label, "url": _price_search_url(service, query)})
        generated += 1

    if search_row:
        rows.append(search_row)
    return {"inline_keyboard": rows} if rows else None


def _entities_within(entities: Sequence[Any] | None, limit: int) -> list[Any]:
    """UTF-16 sınırını aşan entity'leri at (Telegram taşan entity'yi reddeder)."""
    kept: list[Any] = []
    for entity in entities or []:
        offset = int(getattr(entity, "offset", 0) or 0)
        length = int(getattr(entity, "length", 0) or 0)
        if length <= 0 or offset < 0 or offset + length > limit:
            continue
        kept.append(entity)
    return kept


# Yeni bildirim üst bloğu (TEK ÜST SATIR: "₺56 — ürün adı" ya da "❓ — ürün adı";
# alıntılanmış orijinal mesajın üstünde).
# config.json → smart_header; anahtar yoksa KAPALI (eski düzen aynen sürer).
SMART_HEADER = False
# Hızlı bildirim: bot bildirimi ÖNCE gider; hesap kopyası yalnızca bot başarısız olursa atılır.
# config.json → fast_notify; anahtar yoksa KAPALI (eski sıra aynen sürer).
FAST_NOTIFY = False
# Hızlı tarama: Telegram bazı yoğun kanalların güncellemelerini geç (10-60 sn) iletiyor.
# Bu görev kaynakları birkaç saniyede bir tek istekle yoklar, yeni mesajı hemen işler.
# config.json → fast_poll (varsayılan AÇIK; kapatmak için false), fast_poll_seconds (varsayılan 3).
FAST_POLL = True
FAST_POLL_SECONDS = 3.0
# P6: açılışta her kaynak için geriye dönük tarama sınırları. Kesinti
# sırasında gelen mesajlar yalnızca bu pencere içinde yakalanır; daha eski
# mesajlar topluca yeniden bildirilmez. Kalıcı imleç tutulmaz.
FAST_POLL_BACKFILL_LIMIT = 100       # yeni/yeniden başlayan kaynak başına başlangıç penceresi
FAST_POLL_BACKFILL_MAX_AGE = 1800.0  # başlangıçta yalnızca son 30 dk içindeki iletiler
FAST_POLL_BATCH_LIMIT = 30           # kaynak başına bir turda en fazla 30 yeni ileti
# P0 ölçümleme: hızlı taramanın anlık durumu (/durum ve kalp atışında görünür).
# Yalnızca sayaç/aralık bilgisi tutar; davranış değişmez.
POLL_TELEMETRY: dict[str, float] = {
    "interval": FAST_POLL_SECONDS,   # o an kullanılan yoklama aralığı (sn)
    "last_elapsed": 0.0,             # son tam tarama turunun süresi (sn)
    "last_dialog_elapsed": 0.0,      # son GetPeerDialogs isteğinin süresi (sn)
    "flood_count": 0,                # bu oturumdaki tarama FloodWait sayısı
    "last_flood_wait": 0.0,          # son tarama FloodWait beklemesi (sn)
}
LIVE_SEEN: "collections.OrderedDict[tuple[int, int], float]" = collections.OrderedDict()
POLL_DISPATCHED: "collections.OrderedDict[tuple[int, int], float]" = collections.OrderedDict()
SOURCE_IN_FLIGHT: set[tuple[int, int]] = set()
# Kaynak bazlı kalıcı cursor (yalnızca RAM — bilinçli /restart’ta tek commit)
CURSOR_RAM: dict[int, dict[str, Any]] = {}
RESTARTING = False  # /restart drain sırasında yeni mesajlar bekletilsin
# Yavaş bildirim raporu: toplam süre bu eşiği (sn) aşarsa Kayıtlı Mesajlar'a döküm gelir. 0 = kapalı.
SLOW_REPORT_SECONDS = 8.0
_SLOW_REPORT_LAST = 0.0
MEDIA_TIMING: "contextvars.ContextVar[dict | None]" = contextvars.ContextVar("media_timing", default=None)
# P5: tek bir deliver() çağrısı içinde indirilen medyanın bildirime taşınması;
# (chat_id, message_id) -> bytes. deliver() bitince bağlam sıfırlanır, kalıcı
# önbellek değildir; eşzamanlı teslimler birbirinin bağlamını görmez.
MEDIA_DOWNLOAD_CACHE: "contextvars.ContextVar[dict | None]" = contextvars.ContextVar(
    "media_download_cache", default=None)
# --- Bildirim üst satırı (kullanıcı isteği: fiyat EN BAŞTA, emojisiz) --------
# Tasarım (tek satır, kalın):
#     ₺56 — Penti Kız Çocuk Basic Colorful 4lü Patik Çorap
# Fiyat güvenle bulunamayınca:
#     ❓ — Penti Kız Çocuk Basic Colorful 4lü Patik Çorap
# Eskiden 1. satır ürün başlığı, 2. satır fiyat (boş satırla ayrı) idi; şimdi
# fiyat ile ad AYNI satırda ve fiyat solda, böylece kesilen telefon
# bildiriminde fiyat + adın ilk kelimeleri birlikte okunur.
# Üst satır YALNIZCA görünen metindir: arama sorguları (Google Alışveriş,
# Akakçe, Cimri) ve tekrar (dedup) anahtarı daima fiyatsız addan kurulur
# (bkz. ``is_header_price``, ``split_header_line``, ``header_search_title``).
HEADER_SEPARATOR = " — "
# Önceki sürümün üst satır ayracı (``56₺ - Ad``); yalnızca geriye dönük
# okumada (geçmiş taraması/rozet) kabul edilir, yeni bildirimde üretilmez.
PREV_HEADER_SEPARATOR = " - "
# Fiyat yazımı: para birimi işareti + kuruşsuz tutar, boşluksuz (``₺56``).
# Gövdedeki orijinal metne dokunulmaz; bu yazım yalnızca üst satırdadır.
PRICE_CURRENCY_MARK = "₺"
# Fiyat bulunamadığında uzun uyarı yazmak yerine yalnızca bu işaret konur
# (kullanıcı isteği: "fiyat bulunamadı diyor, artık sadece soru işareti koysun").
PRICE_MISSING_MARK = "❓"
# YALNIZCA geriye dönük okuma: daha önceki sürümlerin üst satırı
# "💰 12.299 TL — Ad" (emoji önekli, uzun tireli) biçimindeydi, en eski sabit
# düzen ise gövdede "💰Fiyat: …" satırı taşırdı. Yeni bildirimde 💰 üretilmez;
# bu iki sabit geçmiş bildirimleri doğru okumak için tutulur.
PRICE_LINE_PREFIX = "💰"
LEGACY_HEADER_SEPARATOR = " — "
# Üst satırın FİYAT kısmı olarak tanınan biçimler. _SEARCH_AMOUNT/_SEARCH_CURRENCY
# arama ayıklayıcısıyla AYNI tutar/para birimi tanımını kullanır (tek kaynak).
_HEADER_PRICE_RE = re.compile(
    rf"(?:{_SEARCH_CURRENCY}\s*{_SEARCH_AMOUNT}|{_SEARCH_AMOUNT}\s*{_SEARCH_CURRENCY})",
    re.IGNORECASE,
)


def is_header_price(text: str) -> bool:
    """Metin üst satırın fiyat kısmı mı? ``56₺``, ``12.299,90 TL``, ``₺1.299``, ``❓``.

    Yalnızca TAM eşleşme kabul edilir: "12.299 TL — Philips Airfryer" gibi bir
    ürün adı fiyat diye bölünmez; "2024 - Yeni Sezon" başlığında "2024" tek
    başına para birimi taşımadığı için fiyat sayılmaz.
    """
    value = " ".join((text or "").split())
    if not value:
        return False
    if value == PRICE_MISSING_MARK:
        return True
    return bool(_HEADER_PRICE_RE.fullmatch(value))


def header_price_text(value: float | None) -> str:
    """Üst satır fiyat yazısı: ``₺56``, ``₺12.299``; yoksa ``❓``.

    Yalnızca GÖSTERİM biçimlendirilir (kullanıcı isteği): kuruş, yuvarlanmadan
    atılır (``199,90`` → ``₺199``; ``12.299,99`` → ``₺12.299``) ve tutar binlik
    ayraçlarıyla yazılır. Gerçek fiyat değerine dokunulmaz: ayrıştırıcı,
    `format_price` (gövde/DM/raporlar "TL" yazımıyla devam eder), fiyat düşüşü
    ve dedup karşılaştırmaları kuruşlu gerçek değerle çalışmayı sürdürür.
    """
    if value is None or not math.isfinite(value):
        return PRICE_MISSING_MARK
    whole = math.trunc(value)  # kuruşu at; asla yuvarlama
    text = f"{whole:,}".replace(",", ".")
    return f"{PRICE_CURRENCY_MARK}{text}"


def build_header_line(price_text: str, title: str) -> str:
    """Üst satırı kur: ``<fiyat> - <ürün adı>`` ya da ``<ürün adı> ❓``.

    Fiyat varsa başta, sonra ayraç, sonra ürün adı. Fiyat yoksa (``❓``) ürün
    adı başa, ``❓`` sona yazılır (kullanıcı isteği: ``❓`` öne gelmesin).
    Parçalardan biri yoksa satır ona göre kısalır; ikisi de yoksa boş döner.
    """
    price = (price_text or "").strip()
    name = " ".join((title or "").split())
    if price == PRICE_MISSING_MARK and name:
        return f"{name} {PRICE_MISSING_MARK}"
    if price and name:
        return f"{price}{HEADER_SEPARATOR}{name}"
    return price or name


def utf16_budget(text: str, units: int) -> str:
    """Metni en fazla ``units`` UTF-16 kod birimi olacak şekilde baştan keser.

    Telegram uzunlukları UTF-16 birimiyle sayar; ``𝐆𝐔𝐂𝐂𝐈`` gibi süslü yazı ve
    astral emoji bir Python karakteri ama İKİ UTF-16 birimidir. Karakter sayısıyla
    kesmek bu yüzden sınırı aşabiliyordu (bkz. ``fit_header_line``). Kesim kod
    noktası sınırında kalır; yarım karakter üretilmez.
    """
    if units <= 0 or not text:
        return ""
    used = 0
    kept = []
    for char in text:
        cost = 2 if ord(char) > 0xFFFF else 1
        if used + cost > units:
            break
        kept.append(char)
        used += cost
    return "".join(kept)


def fit_header_line(price_text: str, title: str, room: int) -> str:
    """Üst satırı ``room`` UTF-16 birimine sığdır: fiyat korunur, adın kuyruğu düşer.

    Kapasite yetmiyorsa önce ürün adının kuyruğu "…" ile düşer; ad için hiç yer
    kalmamışsa yalnızca fiyat yazılır (fiyat, bildirimin kesilen yerinde solda
    durduğu için her koşulda en değerli parçadır). Hiç sığmıyorsa boş döner.
    Uzunluk Telegram birimiyle (UTF-16) ölçülür: ürün adı astral karakter
    içeriyorsa (süslü font marka adı, emoji) karakter sayısıyla ölçmek sınırı
    aşırıp gönderilen metni ``message is too long`` hatasına düşürebiliyordu.
    """
    price = (price_text or "").strip()
    name = " ".join((title or "").split())
    # Fiyat yoksa (❓) ürün adı başa, ❓ sona: "Ad ❓"
    if price == PRICE_MISSING_MARK:
        suffix = f" {PRICE_MISSING_MARK}"
        full = f"{name}{suffix}" if name else price
        if room <= 0 or utf16_length(full) <= room:
            return full if room > 0 else ""
        if not name:
            return price if utf16_length(price) <= room else ""
        if room - utf16_length(suffix) < 8:
            return price if utf16_length(price) <= room else ""
        tail = utf16_budget(name, room - utf16_length(suffix) - 1).rstrip()
        return f"{tail}…{suffix}"
    full = build_header_line(price, name)
    if room <= 0 or utf16_length(full) <= room:
        return full if room > 0 else ""
    if not name:
        return price if utf16_length(price) <= room else ""
    prefix = f"{price}{HEADER_SEPARATOR}" if price else ""
    if room - utf16_length(prefix) < 8:
        # Ad için ayrılacak yer yok: en azından fiyat görünsün.
        return price if utf16_length(price) <= room else ""
    tail = utf16_budget(name, room - utf16_length(prefix) - 1).rstrip()
    return f"{prefix}{tail}…"


def split_header_line(line: str | None) -> tuple[str, str]:
    """Bildirim üst satırını ``(fiyat, ürün adı)`` diye ayırır.

    Ayracın solu fiyat gibi görünmüyorsa satırın TAMAMI ürün adıdır; böylece
    içinde " - " veya "—" geçen, fiyat benzeri sayı/para birimi barındıran
    adlar yanlış bölünmez. Başta ``💰`` ile başlayan satırlar eski/ara sürüm
    bildirimlerdir: önek düşülüp aynı mantık yürütülür, fiyat okunamıyorsa
    satır "adı olmayan fiyat satırı" sayılır (geçmiş taraması o durumda
    ``_search_query``'ye düşer — eski davranışın aynısı).
    """
    text = " ".join((line or "").split())
    if not text:
        return "", ""
    legacy = text.startswith(PRICE_LINE_PREFIX)
    body = text[len(PRICE_LINE_PREFIX):].strip() if legacy else text
    # Yeni biçim: "Ad ❓" (fiyat sonda) — önce onu tanı, yoksa eski "❓ — Ad" ayraçları.
    if body == PRICE_MISSING_MARK:
        return PRICE_MISSING_MARK, ""
    if body.endswith(f" {PRICE_MISSING_MARK}"):
        title_part = body[:-len(PRICE_MISSING_MARK)].strip().rstrip(" -—")
        if title_part:
            return PRICE_MISSING_MARK, title_part
    # Güncel ayraç + önceki sürümlerin ayraçları: geçmişteki bildirimler
    # (``56₺ - Ad``, ``💰 12.299 TL — Ad``) de doğru bölünmeye devam eder.
    separators = tuple(dict.fromkeys(
        (HEADER_SEPARATOR, LEGACY_HEADER_SEPARATOR, PREV_HEADER_SEPARATOR)))
    for separator in separators:
        head, found, tail = body.partition(separator)
        head = head.strip()
        if is_header_price(head):
            return head, tail.strip() if found else ""
    return (text, "") if legacy else ("", text)


def header_search_title(text: str | None) -> str:
    """Metin bildirim üst satırıysa fiyatını düşür, arama için salt adı bırak.

    Arama sorguları normalde kaynak mesajdan (``_search_query``) ayıklanır ve
    fiyat zaten içeride değildir. Bu fonksiyon, üst satırın bir yere yanlışlıkla
    olduğu gibi taşınmasına karşı tek noktadaki sigortadır: ``₺56 — Ad`` gelir,
    ``Ad`` çıkar; tutar, para birimi, ayraç ve ``❓`` arama kutusuna girmez.
    Ayraçsız salt fiyat/``❓`` satırı aranacak bir ad taşımaz, boş döner.
    """
    text = " ".join((text or "").split())
    if not text:
        return ""
    price, title = split_header_line(text)
    return title if price else text


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
    if key in LIVE_SEEN or key in SOURCE_IN_FLIGHT:
        return True
    if key in POLL_DISPATCHED and not getattr(event, "_fast_poll", False):
        return True
    _remember(LIVE_SEEN, *key)
    return False


# --- Cursor (tek cursor / grup) — yalnızca RAM, bilinçli /restart’ta tek persist

def _cursor_ensure(chat_id: int) -> dict[str, Any]:
    cid = int(chat_id)
    if cid not in CURSOR_RAM:
        CURSOR_RAM[cid] = {"cursor": 0, "pending": set(), "completed": set(), "failed": set(), "failed_at": {}, "initialized": True}
    return CURSOR_RAM[cid]


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
    CURSOR_RAM.clear()
    raw = config.get("source_cursors")
    if not isinstance(raw, dict):
        return
    for k, v in raw.items():
        try:
            cid = int(k)
            cur = int(v)
            CURSOR_RAM[cid] = {"cursor": cur, "pending": set(), "completed": set(), "failed": set(), "failed_at": {}, "initialized": True}
        except Exception:
            continue


def cursor_collect_for_persist() -> dict[str, int]:
    """Yalnızca başlangıç noktası belirlenmiş RAM cursor’larını kalıcılaştır."""
    return {
        str(int(cid)): int(st["cursor"])
        for cid, st in CURSOR_RAM.items()
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
    global _SLOW_REPORT_LAST
    processing = sum(seconds for _, seconds in steps)
    total = processing + max(lag, 0.0)
    if SLOW_REPORT_SECONDS <= 0 or total < SLOW_REPORT_SECONDS:
        return
    now = time.time()
    if now - _SLOW_REPORT_LAST < 60:
        return
    _SLOW_REPORT_LAST = now
    lines = [f"⏱ Yavaş bildirim ({total:.0f} sn) — {source_name}"]
    if lag >= 0:
        lines.append(f"• Telegram → bot: {lag:.1f} sn")
    lines += [f"• {name}: {seconds:.1f} sn" for name, seconds in steps]
    media = MEDIA_TIMING.get()
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


def _webpage_hints(obj: Any) -> list[str]:
    """Mesajdaki link önizlemesinin başlığı (varsa) başlık seçimine ipucu olur."""
    hints: list[str] = []
    try:
        media = getattr(obj, "media", None) or getattr(getattr(obj, "message", None), "media", None)
        webpage = getattr(media, "webpage", None)
        for attr in ("title", "description"):
            value = getattr(webpage, attr, None)
            if isinstance(value, str) and value.strip():
                hints.append(value.strip())
    except Exception:  # noqa: BLE001 - ipucu yoksa sorun değil
        return []
    return hints


def _multi_product_titles(text: str) -> list[str]:
    """Explicitly numbered/listed product blocks yield separate title identities."""
    blocks = price_filter._numbered_product_blocks(text or "")
    if len(blocks) < 2:
        return []
    titles = []
    for _, block in blocks:
        title = price_filter.extract_title(block, limit=MESSAGE_LIMIT)
        if (title and not price_filter.is_generic_title(title)
                and len(WORD_TOKEN_RE.findall(title)) >= 3
                and not re.search(r"\b(?:ürün|urun)\b", normalize(title))):
            titles.append(title)
    return titles if len(titles) >= 2 else []


def _multi_product_display_title(titles: Sequence[str]) -> str:
    """Keep a compact, readable five-word label for each product in the header."""
    return " — ".join(" ".join(title.split()[:5]) for title in titles)


def smart_header_info(obj: Any, cleaned_obj: Any, body: str) -> tuple[str, "price_filter.PriceInfo"]:
    """Temizlenmiş mesajdan (başlık, fiyat) üret; hata olursa boş döner."""
    try:
        entities = entities_for_text(cleaned_obj, body)
        strike = price_filter.strike_spans_from_entities(body, entities)
        title, info = price_filter.analyze(body, strike, _webpage_hints(obj))
        linked = linked_product_title(cleaned_obj)
        if info.reason == "birden fazla ürün bloğu: seçim belirsiz":
            product_titles = _multi_product_titles(body)
            if product_titles:
                return _multi_product_display_title(product_titles), info
        campaign_title = _campaign_title_if_clipped(
            body, title, MESSAGE_LIMIT, allow_code_only_marker=True,
        )
        full_campaign_title = _coupon_campaign_title(
            body, MESSAGE_LIMIT, include_code=True,
            allow_code_only_marker=True,
        )
        # A card-code/category teaser is not a product name. Multi-tier coupon
        # tables likewise need the campaign heading, never one arbitrary tier.
        coupon_preview = bool(
            full_campaign_title and (
                _is_coupon_teaser_title(title)
                or _coupon_preview_message(body, full_campaign_title)
            )
        )
        return linked or (full_campaign_title if coupon_preview else campaign_title) or title, info
    except Exception as exc:  # noqa: BLE001 - başlık/fiyat hatası bildirimi engellemesin
        log.warning("Başlık/fiyat tespiti başarısız (%s): %s", type(exc).__name__, exc)
        return "", price_filter.PriceInfo(None, "", "none", "hata")


def smart_dedup_title(event: Any) -> str:
    """Yeni düzende tekrar anahtarı için kullanılan başlık (FİYATSIZ).

    Kaynak mesajdan ayıklanır; bildirim üst satırına yazılan fiyat buraya
    giremez, çünkü üst satır yalnızca çıktı metnidir. Böylece "fiyat - ad"
    düzenine geçmek tekrar (dedup) anahtarlarını değiştirmez.
    """
    cleaned = sanitize_message(event)
    text = message_text(cleaned)
    linked = linked_product_title(cleaned, limit=MESSAGE_LIMIT)
    if linked:
        return linked
    campaign_identity = _coupon_dedup_identity(text)
    if campaign_identity:
        return campaign_identity
    product_titles = _multi_product_titles(text)
    if product_titles:
        return " / ".join(product_titles)[:MESSAGE_LIMIT]
    campaign_title = _campaign_title_if_clipped(
        text,
        price_filter.extract_title(text, _webpage_hints(event), limit=MESSAGE_LIMIT),
        MESSAGE_LIMIT,
        include_code=True,
        allow_code_only_marker=True,
    )
    if campaign_title:
        return campaign_title
    # If the source's first line already contains "price - product name",
    # keep that product name; otherwise a following Prime/shipping line can be
    # mistaken for the title by generic title extraction.
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    header_price, header_title = split_header_line(first_line)
    if header_price and header_title and not price_filter.is_generic_title(header_title):
        return header_title
    first_title, first_price = price_filter.analyze(first_line)
    if first_price.value is not None and first_price.confidence in {"high", "medium"} and first_title:
        return first_title
    # Display headers may be truncated; dedup must retain model/size suffixes.
    title = price_filter.extract_title(text, _webpage_hints(event), limit=MESSAGE_LIMIT)
    fallback = _search_query(cleaned, limit=MESSAGE_LIMIT)
    return title or (fallback if not price_filter.is_generic_title(fallback) else "")


def _compose_resolve_header(
    obj: Any, cleaned_obj: Any, source_body: str, fallback_title: str,
) -> tuple[str, str, str, bool]:
    """Üst satır (fiyat + ürün adı) ve alıntı işaretini SMART_HEADER moduna göre kur."""
    product_title = fallback_title
    price_text = ""
    quote_body = False
    if SMART_HEADER:
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


def _compose_cut_body(body: str, room: int) -> str:
    """Gövdeyi ``room`` UTF-16 birimine sığdır; kırpılan gövde ``…`` ile biter, yer yoksa boş kalır."""
    if utf16_length(body) <= room:
        return body
    return utf16_budget(body, room - 1).rstrip() + "…" if room > 0 else ""


def _compose_body_links(cleaned_obj: Any, body: str, room: int) -> set[str]:
    """``room`` birime sığdırılan gövdeden açılabilen adresler: görünür URL'ler ve gizli hyperlink'ler.

    Kırpma noktasının gerisinde kalan bağlantı açılamaz. Yarım kalan bir URL'nin baştaki
    parçası da adres gibi görünür; çağıran bunu gövdenin tamamındaki adreslerle eler.
    """
    text = _compose_cut_body(body, room)
    return visible_urls(text) | {
        clean_url(entity.url).rstrip("/").lower()
        for entity in entities_for_text(cleaned_obj, text) if getattr(entity, "url", None)
    }


def _compose_fit_appendix(
    build: Callable[[int], tuple[list[tuple[str, int]], list[tuple[int, int, str]], list[str]]],
    cleaned_obj: Any, header: str, body: str, tail: Sequence[str], limit: int, sep: str,
    real_tail: Sequence[str] | None = None,
) -> tuple[str, list[tuple[int, int, str]]]:
    """Eki karakter sınırına sığdır: ``(ek metni, ürün satırı konumları)``.

    ``build(n)`` en çok ``n`` ürün satırlı eki kurar (bkz. ``_compose_build_appendix``).

    Ölçü yalın düzendir: gövde + mesajın ilk gizli bağlantılarının ``🔗`` satırları (hepsi
    birlikte gövdesiz sığmıyorsa hiçbiri) + kaynak adı. O düzenin açtığı hiçbir bağlantı
    kapanmaz (mesaj adresi adın içinde olduğu için metinde yer kaplamaz). Bunun içinde
    öncelik sırası:

    1. Kaynak mesajın metni ve içindeki bağlantılar. Gövdenin tamamı sığmıyorsa ``🔗``
       satırları sondan başlayarak bırakılır: önce yedekliler (``APPENDIX_SPARE``; botta
       butonu klavyede kalır), yalın düzen hiç satır yazamıyorsa vazgeçilmezler de (o düzende
       de yoklardı). Bu satırlar gövdeyi hiç kırpmaz. Yalın düzenin yazdığı vazgeçilmez
       satırlar yerinde kalır: klavyesiz kopyada bağlantının tek taşıyıcısıdır.
    2. ``👉 Ürün Linki 👈`` satırları. Yer yoksa gövde metninin sonundan yer alırlar; ama
       **yalın düzende (PR öncesi düzen) açılabilen hiçbir bağlantıyı kapatmazlar.** Güvence
       bu kadardır, daha güçlü bir iddia değildir: ölçü bütçesi sabit tutulduğu için kırpma
       noktası eski düzenle aynı yerde kalır, dolayısıyla yalın düzenin açtığı her bağlantı
       açık kalır. Buna karşılık "ürün satırı hiç yazılmasaydı gövdeden açılacak **her**
       bağlantı açık kalır" iddiası geçerli DEĞİLDİR: ürün satırı, ürün satırsız varyanta
       göre gövdenin sonundaki bir bağlantıyı kesebilir — o bağlantı eski düzende de
       kesiliyordu (ölçüm: 1430 ürün satırlı hücrenin 10'unda, %0,70; kapanan adreslerin
       tamamı yalın düzende de kapalı, dolayısıyla kullanıcıya dönük erişim kaybı yoktur).
       Ek ayrıca gövdesiz de sığmalıdır (yalın düzenin satırları dışarıda kalmaz). Buna
       uymayan ürün satırı sondan başlayarak yazılmaz; bağlantısı botta klavye düğmesi
       olarak kalır, hesap kopyasında ilk gizli bağlantılardansa ``🔗`` satırı olur.
    3. Kaynak adı (ve içine gömülü mesaj adresi) ek uğruna bırakılmaz; ad hiç
       yazılamıyorsa adres ``🔗 Mesajı Gör: <t.me>`` satırı olarak geri gelir.

    ``real_tail``: metne GERÇEKTEN giren kuyruk (mesaj linki artık ayrı satır
    değil, kaynak adının içinde). ``tail`` bilerek eski, geniş bütçeyi korur:
    satır kalktı diye boşalan yer fazladan ``🔗`` satırlarına verilirse onlar
    gövdenin sonundan yer alıp gövdedeki bağlantıyı kapatırdı (kullanıcı isteği:
    bildirim yalnız KISALSIN). Ama ileti her şeyiyle SİĞİYORSA hiçbir satır
    bırakılmaz: bütçedeki ayrılmış yer yalnız baskı altında satır seçimini
    belirler, sığan iletiyi kısaltmaz.
    """
    def size(texts: Sequence[str], units: int) -> int:
        """Gövde ``units`` birim olsaydı iletinin uzunluğu."""
        parts = (header, "x" * units, "\n".join(texts), *tail)
        return utf16_length(sep.join(part for part in parts if part))

    def everything_fits(texts: Sequence[str]) -> bool:
        """Gövdenin tamamı ve bütün satırlar GERÇEK uzunlukla sığıyor mu?"""
        parts = (header, body, "\n".join(texts), *(tail if real_tail is None else real_tail))
        actual = utf16_length(sep.join(part for part in parts if part))
        if actual > limit:
            return False
        parts = (header, "\n".join(texts), *(tail if real_tail is None else real_tail))
        return utf16_length(sep.join(part for part in parts if part)) < limit

    if real_tail is not None:
        everything = build(LINK_APPENDIX_LIMIT)
        if everything_fits([text for text, _ in everything[0]]):
            return "\n".join(text for text, _ in everything[0]), everything[1]

    def whole(texts: Sequence[str]) -> bool:
        """Gövdenin tamamı sığıyor mu? (Ek, gövde dışındaki yerin hepsini de doldurmamalı.)"""
        return size(texts, utf16_length(body)) <= limit and size(texts, 0) < limit

    def body_links(texts: Sequence[str]) -> set[str]:
        """Bu ekle birlikte gövdeye kalan yerden açılabilen adresler."""
        return _compose_body_links(cleaned_obj, body, max(0, limit - size(texts, 1) + 1))

    def fit(count: int) -> tuple[list[str], list[tuple[int, int, str]], int]:
        """En çok ``count`` ürün satırlı ek; ``🔗`` satırları 1. maddeye göre bırakılmış hâliyle."""
        lines, spans, plain = build(count)
        if size(plain, 0) >= limit:  # yalın düzen satırlarının hiçbirini yazamıyor
            plain = []
        texts = [text for text, _ in lines]
        ranks = [rank for _, rank in lines]
        for spare in (APPENDIX_SPARE, APPENDIX_REQUIRED)[:1 if plain else 2]:
            while spare in ranks and not whole(texts):
                last = len(ranks) - 1 - ranks[::-1].index(spare)
                del texts[last], ranks[last]
        return texts, spans, ranks.count(APPENDIX_PRODUCT)

    wanted: set[str] | None = None
    count = LINK_APPENDIX_LIMIT
    while True:
        texts, spans, count = fit(count)
        if not count or whole(texts):
            break
        # Gövde kırpılacak: ürün satırları, hiç yazılmasalardı gövdeden açılacak bağlantıyı kapatmamalı.
        if wanted is None:
            wanted = body_links(fit(0)[0]) & _compose_body_links(cleaned_obj, body, utf16_length(body))
        carried = visible_urls("\n".join(texts)) | {clean_url(url).rstrip("/").lower() for _, _, url in spans}
        if size(texts, 0) < limit and wanted <= body_links(texts) | carried:
            break
        count -= 1
    return "\n".join(texts), spans


def _compose_join(segments: Sequence[tuple[str, str]], sep: str) -> str:
    """Dolu segmentleri ayırıcı ile birleştir."""
    return sep.join(value for _, value in segments if value)


def _compose_other_than_body(segments: Sequence[tuple[str, str]], sep: str) -> str:
    """Gövde dışındaki parçaların birleşimini ver (limit kontrolü için)."""
    return _compose_join([part for part in segments if part[0] != "body" and part[1]], sep)


def _compose_drop_overflow_parts(
    segments: list[tuple[str, str]], appendix_text: str, name: str | None, source_line: str, limit: int, sep: str,
) -> tuple[list[tuple[str, str]], str, str | None, str]:
    """Gövde hariç sığmama durumunda appendix/name/source sırasıyla düşer."""
    if utf16_length(_compose_other_than_body(segments, sep)) >= limit and appendix_text:
        segments = [(kind, "" if kind == "appendix" else value) for kind, value in segments]
        appendix_text = ""
    if utf16_length(_compose_other_than_body(segments, sep)) >= limit and name:
        segments = [(kind, "" if kind == "name" else value) for kind, value in segments]
        name = None
    if utf16_length(_compose_other_than_body(segments, sep)) >= limit and source_line:
        segments = [(kind, "" if kind == "source" else value) for kind, value in segments]
        source_line = ""
    return segments, appendix_text, name, source_line


def _compose_truncate_header(
    segments: list[tuple[str, str]], header_text: str, price_text: str, product_title: str, limit: int, sep: str,
) -> tuple[list[tuple[str, str]], str, str, str]:
    """Üst satır hâlâ sığmıyorsa kalan oda ile kırp; fiyat korunur, ad kısalır."""
    if header_text and utf16_length(_compose_other_than_body(segments, sep)) >= limit:
        rest = _compose_join([part for part in segments if part[0] not in ("body", "header") and part[1]], sep)
        room = limit - utf16_length(rest) - (utf16_length(sep) if rest else 0) - utf16_length(sep)
        header_text = fit_header_line(price_text, product_title, room) if room >= 16 else ""
        price_text, product_title = split_header_line(header_text) if header_text else ("", "")
        segments = [(kind, header_text if kind == "header" else value) for kind, value in segments]
    return segments, header_text, price_text, product_title


def _compose_truncate_body(
    segments: list[tuple[str, str]], limit: int, sep: str,
) -> tuple[list[tuple[str, str]], str]:
    """Gövde için kalan odaya göre kırp; yer yoksa boş bırak."""
    others_text = _compose_other_than_body(segments, sep)
    current_body = next((value for kind, value in segments if kind == "body"), "")
    gap = utf16_length(sep) if (current_body and others_text) else 0
    body_room = max(0, limit - utf16_length(others_text) - gap)
    body = _compose_cut_body(current_body, body_room)
    segments = [(kind, body if kind == "body" else value) for kind, value in segments]
    return segments, body


def _compose_assemble_text(segments: Sequence[tuple[str, str]], sep: str) -> tuple[str, str, dict[str, int], int, int]:
    """Segmentleri UTF-16 offset'lerle nihai metin ve content'e dönüştür."""
    offsets: dict[str, int] = {}
    parts: list[str] = []
    cursor = 0
    for kind, value in segments:
        if not value:
            continue
        if parts:
            parts.append(sep)
            cursor += utf16_length(sep)
        offsets.setdefault(kind, cursor)
        parts.append(value)
        cursor += utf16_length(value)
    text = "".join(parts)
    content = _compose_join([(kind, value) for kind, value in segments if kind != "name"], sep)
    name_offset = offsets["name"] if "name" in offsets else -1
    body_offset = offsets.get("body", 0)
    return text, content, offsets, body_offset, name_offset


def _compose_build_entities(
    header_text: str, body: str, body_entities: Sequence[Any], name: str | None,
    quote_body: bool, offsets: dict[str, int], body_offset: int, name_offset: int,
    appendix_links: Sequence[tuple[int, int, str]] = (),
    name_url: str | None = None,
) -> tuple[list[Any], list[Any]]:
    """Üst satır, alıntı, gövde ve ek linklerini + kaynak adı biçimini topla.

    İki liste döner: ``(içerik, kaynak adı)``. Kaynak adı bloğu Bot API'ye ayrı
    eklenir (``source_name_entity``); ayırmak, alanın metin dışına taşmadığı
    sürece iki listede de aynı biçimde durmasını sağlar.
    """
    content_entities: list[Any] = []
    if header_text and "header" in offsets:
        content_entities.append(types.MessageEntityBold(
            offset=offsets["header"], length=utf16_length(header_text),
        ))
    if quote_body and body and "body" in offsets:
        content_entities.append(types.MessageEntityBlockquote(
            offset=body_offset, length=utf16_length(body),
        ))
    content_entities.extend(_entities_within(
        shift_telethon_entities(body_entities, body_offset),
        body_offset + utf16_length(body),
    ))
    if "appendix" in offsets:
        content_entities.extend(
            types.MessageEntityTextUrl(offset=offsets["appendix"] + start, length=length, url=url)
            for start, length, url in appendix_links
        )
    name_entities: list[Any] = []
    if name_offset >= 0 and name:
        name_entities.append(types.MessageEntityBold(
            offset=name_offset, length=utf16_length(name),
        ))
        if name_url:
            # Kullanıcı isteği: "Mesajı Gör" satırı kalkar; orijinal mesajın
            # adresi en alttaki grup adının İÇİNE gömülür.
            name_entities.append(types.MessageEntityTextUrl(
                offset=name_offset, length=utf16_length(name), url=name_url,
            ))
    return content_entities, name_entities


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
        cleaned_obj, header_text, body, measure_tail, limit, sep,
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


# ---------------------------------------------------------------------------
# Tekrar birleştirme (aynı fırsat, tek mesaj)
# ---------------------------------------------------------------------------
#
# Aynı indirim 3-5 kanal tarafından dakikalar içinde, farklı fiyat/CTA
# yerleşimleriyle paylaşılabilir. Her kopyayı gruba atmak mesaj kalabalığı yapar;
# ürün adı sorgusu normalize edilerek ilk kopya bellekte kanonik kayıt olur.
#
#   * İlk kopya her zamanki gibi gönderilir ve ürün sorgusu ``DEDUP_CACHE``
#     sözlüğüne yazılır (mesaj başına EK API çağrısı YOKTUR;
#     her tekrarda geçmiş taramak hem bildirimi geciktirir hem FloodWait/429
#     riskini artırırdı).
#   * Sonraki aynı ürün sorgusuna sahip kopyalar gruba ATILMAZ; ilk mesajdaki
#     kaynak grup adı satırı KALDIRILIR ve yerine çoklu paylaşım notu işlenip
#     bildirim orada kapanır:
#     "📌 2 kere paylaşıldı: FırsatZ, İndirimde Al" (kullanıcı isteği:
#     "hangi gruptan geldiğini kaldırıp N kere paylaşıldı şu şu gruplar
#     diye yazsın"). Grup adı satırı yoksa not en sona eklenir.
#   * Not TEK SATIRDIR; grup adları virgülle yazılır. Sayı 5'i geçince vurgu
#     artar: "🚨 5 kere paylaşıldı: ... — KAÇIRMA!". Kalın + emoji,
#     Telegram'ın sunduğu en güçlü vurgu kombinasyonudur.
#   * Eşleşme penceresi ``dedup_window_hours`` ile sınırlıdır (varsayılan 12):
#     iki hafta sonra aynı ürün yine indirime girerse YENİ fırsat sayılır.
#   * Bot yeniden başlayınca bellek boşalır; açılışta hedeften SON
#     ``dedup_scan_limit`` mesaj (varsayılan 100, tek API çağrısı) okunup
#     önbellek yeniden kurulur. İşlem başına tarama yapılmaz.
#
# Yarış durumu: aynı anda gelen kopyalar için gönderen "rezervasyon" koyar;
# diğerleri onun bitmesini bekleyip tekrara düşer (bkz. main() içindeki
# dedup_before_send / dedup_after_send).

# Çoklu paylaşım notu (kullanıcı isteği): bildirimi KAPATAN son bloktur,
# "📌 2 kere paylaşıldı: FırsatZ, İndirimde Al" biçiminde yazılır; 5+ kaynakta
# baştaki emoji 🚨 olur ve sonuna "— KAÇIRMA!" eklenir.
# (Emoji grubu yakalanmaz; sayı her iki biçimde de 1. gruptur.)
DEDUP_BADGE_RE = re.compile(r"^(?:📌|🚨)\s*(\d+)\s+kere\s+paylaşıldı", re.IGNORECASE)
# Eski rozet biçimi: "✅ 2 kaynakta paylaşıldı · teyitli fırsat" / "🔥🔥 4 kaynakta ..."
# (üst satırda ya da fiyat satırının hemen altında dururdu). Yalnızca ESKİ
# gönderilmiş bildirimlerin rozeti sökülürken tanınır — geriye dönük uyumluluk.
DEDUP_BADGE_LEGACY_RE = re.compile(
    r"^(?:✅|🔥+|🚨)\s*(\d+)\s+kaynakta\s+paylaşıldı", re.IGNORECASE,
)
# Çok daha eski sürüm rozeti ikinci satırda kaynak adlarını listelerdi
# ("📌 Kaynaklar: ..."); açılış taramasında o satırı atlamak için korunur.
DEDUP_SOURCES_PREFIX = "📌"
# Rozette birden çok grup adı bu ayraçla yazılır. Virgül KULLANILMAZ: kaynak
# grup adları virgül içerebiliyor ve o durumda adlar bölünüp linkleri kayıyordu.
BADGE_SOURCE_SEPARATOR = " | "
# YENİ biçimin TEK GRUPLU rozete eklediği sabit süs. Yalnız bu ek, rozetin yeni
# biçimde yazıldığını ve ad içindeki virgülün AYRAÇ OLMADIĞINI kanıtlar.
# ``— KAÇIRMA!`` buraya GİREMEZ: aynı ek yeni biçimde de (5+ paylaşımda) çıkıyor,
# dolayısıyla eski/yeni ayrımında hiçbir şey kanıtlamaz. Nitekim onu "eski biçim"
# sanmak, ``🚨 5 kere paylaşıldı: A, B, C — KAÇIRMA!`` rozetindeki adları
# bölünmez hâle getiriyordu.
_BADGE_NEW_SINGLE_SUFFIX_RE = re.compile(r"·\s*tek\s+grup")
# Rozetin sonuna eklenen süsler. Yalnız AD'dan temizlenirler (ad sayılmazlar);
# eski/yeni biçim KARARINDA kullanılmazlar — ``— KAÇIRMA!`` yeni biçimde de var.
_BADGE_TRAILING_DECOR_RE = re.compile(r"\s*(?:—\s*KAÇIRMA!|·\s*tek\s+grup)\s*$")
# Aynı sohbete Bot API ~1/sn sınırı uygular; not güncellemeleri bunun
# altında kalmak için mesaj başına bu kadar aralık bırakır. Aralık içinde
# gelen tekrarlar tek düzenlemede birleşir (düzenleme kilidi altında sayaç
# yeniden okunur); bekleme Bot API kilidi alınmadan yapılır, bu yüzden yeni
# bildirimler rozet beklemesinin arkasında kilitlenmez.
DEDUP_EDIT_MIN_INTERVAL = 5.0
# Rezervasyon koyup bitirmeyen göndereni bekleme süresi (sonrası devralma).
DEDUP_RESERVE_TIMEOUT = 60.0
# Yarım kalmış rezervasyonun önbellekten atılma süresi (temizlik).
DEDUP_RESERVE_TTL = 300.0


DEDUP_TOKEN_RE = re.compile(r"[^\W\d_]+|\d+(?:[.,]\d+)*|\+", re.UNICODE)

_TITLE_STORE_PREFIX_RE = re.compile(
    r"^(?:amazon|hepsiburada|trendyol|n11|mediamarkt|teknosa|vatan|pazarama|"
    r"ciceksepeti|itopya|incehesap|boyner|defacto|lcw|migros|carrefour|"
    r"a101|bim|sok|ikea|koctas|decathlon)\s*:\s+",
)


def _fold_title(title: str | None) -> str:
    """Başlık karşılaştırması için Türkçe/ASCII harf, kesme ve mağaza öneki normalizasyonu.

    ``exclusion_normalize`` NFKD ile ş/ğ/ü/ö/ç harflerini sadeleştirir ancak
    noktasız ``ı`` harfini korur; ayrıca büyük ``I`` harfi ``normalize`` içinde
    ``ı`` olduğundan ``PROFASHION`` ile ``Profashion`` ayrışabiliyordu. Başlık
    kimliğinde ``ı -> i`` dönüşümü hem Türkçe ASCII yazımları (``Kirmizi`` /
    ``Kırmızı``) hem büyük harfli İngilizce marka/model adlarını eşitler.
    """
    folded = exclusion_normalize(title).replace("ı", "i")
    stripped = _TITLE_STORE_PREFIX_RE.sub("", folded)
    if stripped != folded and len(re.findall(r"[a-z0-9]+", stripped)) >= 2:
        folded = stripped
    return folded


def dedup_key(title: str | None) -> str | None:
    """Ürün adını kaynak biçimlerinden bağımsız, noktalama duyarsız anahtara çevir.

    Çağıran, fiyat/indirim/CTA satırlarını ayıklayan ``_search_query`` sonucunu
    verir. Harf ve sayı token'larını sıralamak; baştaki emoji, tire, noktalama,
    ``6.2L``/``6,2 L`` gibi yazım ve kelime sırası farklarını aynı üründe toplar.
    Token tekrarları korunur; benzer ama farklı model numaraları eşleşmez.
    """
    if not title or price_filter.is_generic_title(title):
        return None
    tokens = [token.replace(",", ".") for token in DEDUP_TOKEN_RE.findall(_fold_title(title))]
    key = " ".join(sorted(tokens))
    return key or None


def dedup_badge(
    count: int, *, sources: Sequence[str] | None = None, short: bool = False,
    compact: bool = False,
) -> str:
    """Çoklu paylaşım notunun metni; ilk gönderimde (``count < 2``) boş döner.

    Kullanıcı isteği: "2-3-4 kere paylaşıldı şu şu şu gruplarda" — not, en
    altta kaynak grup adı satırının YERİNE yazılıp mesajı kapatır. 5 ve üzeri
    sayıda vurgu artar (🚨 ... — KAÇIRMA!). ``compact=True`` adları korur ama
    süsü ("· tek grup", "— KAÇIRMA!") atar: dar açıklamada adlar sığsın diye
    ilk küçültme basamağı budur. ``short=True`` grup adlarını tümden yazmaz;
    yalnız adı olmayan (dolayısıyla link kaybı yaratmayan) durumlarda kullanılır.
    """
    if count < 2:
        return ""
    unique_names = list(
        dict.fromkeys(str(name).strip() for name in (sources or []) if str(name).strip())
    )
    # Ayraç VİRGÜL değil: grup adlarının kendisi virgül içerebiliyor ("… Kupon
    # Haberleri, İndirimde Al") ve virgülle birleştirmek rozeti yanlış yerden
    # bölüyor, ikinci grubun mesaj adresini kaybettiriyordu (kullanıcı bildirimi:
    # "ilk sayfanın linkini gömmüşüz ama ikinci bildirimin linki yok").
    names = BADGE_SOURCE_SEPARATOR.join(unique_names)
    if short:
        names = ""
    head = "📌" if count < 5 else "🚨"
    if compact:
        head = "📌"
    line = f"{head} {count} kere paylaşıldı"
    if names:
        line += f": {names}"
        if len(unique_names) == 1 and not compact:
            # Sayaç paylaşım adedini, liste kaynak adlarını tekil tutar. Aynı
            # gruptaki ikinci paylaşımda ikinci bir grup adı uydurmak yerine
            # tek grup olduğunu açıkça belirt.
            line += " · tek grup"
    if count >= 5 and not compact:
        line += " — KAÇIRMA!"
    return line


def dedup_badge_entities(
    badge: str, *, bot_api: bool = False, offset: int = 0,
    links: dict[str, str] | None = None,
) -> list[Any]:
    """Rozeti biçimlendir: başlık kalın, HER grup adı kendi mesajına bağlı.

    Kullanıcı isteği: nota yazılan her grup adı, o grubun kendi mesajına
    gömülü olsun. Adlar virgülle ayrıldığı için bağlantılar adın tam üzerine
    (UTF-16 aralığına) oturur; adı bilinmeyen kaynak link taşımaz.
    ``offset`` rozetin iletideki başlangıcıdır (bildirim metninin sonunda).
    """
    if not badge or not DEDUP_BADGE_RE.match(badge.strip()):
        return []
    entities: list[Any] = []
    headline = badge.split("\n")[0]
    start = int(offset)
    if bot_api:
        entities.append({"type": "bold", "offset": start,
                         "length": utf16_length(headline)})
    else:
        entities.append(types.MessageEntityBold(offset=start,
                                                length=utf16_length(headline)))
    for name, name_start, name_length in dedup_badge_name_spans(badge):
        url = (links or {}).get(name)
        if not url:
            continue
        if bot_api:
            entities.append({"type": "text_link", "offset": start + name_start,
                             "length": name_length, "url": str(url)})
        else:
            entities.append(types.MessageEntityTextUrl(
                offset=start + name_start, length=name_length, url=str(url)))
    return entities


def _dedup_badge_match(content: str) -> re.Match[str] | None:
    """Satır çoklu paylaşım notu mu? (yeni biçim ya da eski rozet)"""
    stripped = (content or "").strip()
    return DEDUP_BADGE_RE.match(stripped) or DEDUP_BADGE_LEGACY_RE.match(stripped)


def _badge_source_parts(line: str) -> list[str]:
    """Rozet satırındaki ad listesini ``|`` sınırlarıyla böl.

    Grup adlarının KENDİSİ virgül içerebilir ("İndirim Varsa Anlık Fırsat
    Alışveriş Kampanya Kupon Haberleri, İndirimde Al"). Virgülle birleştirme
    adları ikiye bölüyor, rozetteki link aralıkları yanlış ada oturuyor ve
    grubun/b grupların mesaj adresi kayboluyordu. Bu yüzden ayraç ``|``'dir.

    Yeni biçimde TEK ad yazıldığında rozet "· tek grup" eki alır ve o adın
    kendisi virgül içerebilir ("… Sıcak Fırsatlar, Alışveriş & Kupon"). Bu eki
    yok sayıp virgülden bölmek, tek grupta bile adı ikiye bölüp mesaj linkini
    kaybettiriyordu. Bu yüzden "· tek grup" eki varsa virgülden BÖLÜNMEZ; eki
    olmayan ayraçsız rozet eski (virgülle ayrılmış) biçim sayılıp bölünür.
    """
    payload = line.partition(":")[2]
    new_single = bool(_BADGE_NEW_SINGLE_SUFFIX_RE.search(payload))
    payload = _BADGE_TRAILING_DECOR_RE.sub("", payload)
    payload = payload.replace(BADGE_SOURCE_SEPARATOR, "\x00")
    parts = [part.strip() for part in payload.split("\x00")]
    if new_single:
        # "· tek grup": ad tektir ve virgül içerebilir; BÖLÜNMEZ.
        return [part for part in parts if part]
    # Ayraç yok: virgülle ayrılmış ESKİ biçim rozeti. ``— KAÇIRMA!`` eki burada
    # karar vermez (yeni biçim 5+ paylaşımda da yazar); tek gruplu yeni rozet ise
    # yukarıdaki ekle zaten yakalandı. Kalan belirsizlik yalnız "compact" (süssüz)
    # yeni rozette virgüllü tek addır; orada eski biçim lehine bölmek korunuyor,
    # çünkü eski rozetler geçmişte yazılmış ve hâlâ okunmak zorunda.
    if len(parts) == 1 and "," in parts[0]:
        parts = [part.strip() for part in parts[0].split(",")]
    return [part for part in parts if part]


def dedup_badge_sources(content: str | None) -> list[str]:
    """Rozet satırındaki grup adlarını ayıkla (güncelleme ve açılış taraması).

    Adlar ``|`` ile ayrılır; böylece ad içindeki virgül adı bölmez ve rozetteki
    her ad kendi mesaj adresini korur. Eski (virgülle ayrılmış) rozetler de
    okunabilir.
    """
    line = (content or "").strip()
    if not DEDUP_BADGE_RE.match(line):
        return []
    return _badge_source_parts(line)


def dedup_badge_name_spans(badge: str | None) -> list[tuple[str, int, int]]:
    """Rozetteki her grup adının ``(ad, UTF-16 başlangıç, uzunluk)`` konumları.

    Adlar virgülle ayrıldığı için sırayla aranır: aynı ad iki kez yazılmaz
    (``dedup_badge`` tekrarları ayıklar), böylece imleç her adı doğru yerde
    bulur. ``👉 Ürün Linki 👈`` gibi satırlarda kullanılan UTF-16 birimi
    burada da geçerlidir (emoji rozetten önce gövdede olabilir; konumlar
    rozetin KENDİSİNE göre döner, çağıran rozetin metindeki başlangıcını ekler).
    """
    text = badge or ""
    if not DEDUP_BADGE_RE.match(text.strip()):
        return []
    spans: list[tuple[str, int, int]] = []
    cursor = 0
    for name in dedup_badge_sources(text):
        index = text.find(name, cursor)
        if index < 0:
            continue  # "— KAÇIRMA!" / "· tek grup" kırpması adı bölmüş olabilir
        start = utf16_length(text[:index])
        spans.append((name, start, utf16_length(name)))
        cursor = index + len(name)
    return spans


def _entity_url_and_span(entity: Any) -> tuple[str, int, int] | None:
    """Entity'den ``(url, UTF-16 offset, uzunluk)``; bağlantı taşımıyorsa ``None``."""
    if isinstance(entity, dict):
        url = entity.get("url")
        offset, length = entity.get("offset", 0), entity.get("length", 0)
    else:
        url = getattr(entity, "url", None)
        offset, length = getattr(entity, "offset", 0), getattr(entity, "length", 0)
    if not url:
        return None
    try:
        offset, length = int(offset), int(length)
    except (TypeError, ValueError):
        return None
    if offset < 0 or length <= 0:
        return None
    return str(url), offset, length


def _is_telegram_link(url: str) -> bool:
    """Adres bir Telegram mesajı mı? (yayın/grup iletisinin kendi bağlantısı)"""
    lowered = str(url or "").strip().lower()
    return lowered.startswith(("https://t.me/", "http://t.me/", "https://telegram.me/",
                               "http://telegram.me/", "https://telegram.dog/",
                               "http://telegram.dog/"))


def dedup_source_links(text: str | None, entities: Sequence[Any] | None = None) -> dict[str, str]:
    """Metindeki Telegram bağlantılarını kapsadıkları yazıyla eşle (ad → adres).

    Hem yeni düzenin kaynak adı (adres adın İÇİNE gömülü) hem de eski biçimin
    ``🔗 Mesajı Gör: <adres>`` satırı okunur; açılış taraması bir bildirimin
    grup adlarını adresleriyle birlikte geri kurabilsin diye kullanılır.
    """
    body = text or ""
    mapping: dict[str, str] = {}
    for entity in entities or []:
        info = _entity_url_and_span(entity)
        if not info:
            continue
        url, offset, length = info
        if not _is_telegram_link(url):
            continue
        label = utf16_slice(body, offset, length).strip()
        if label and "t.me/" not in label.casefold():
            mapping.setdefault(label, url)
    # Eski biçim: adres görünür bir satırdadır, entity yoktur.
    for line in body.splitlines():
        stripped = line.strip()
        if MESSAGE_LINK_LABEL in stripped and "t.me/" in stripped.casefold():
            tail = stripped.split(MESSAGE_LINK_LABEL, 1)[1].lstrip(": ").strip()
            candidate = clean_url(tail)
            if _is_telegram_link(candidate):
                mapping.setdefault(DEDUP_MESSAGE_LINK_KEY, candidate)
    return mapping


# Görünür ``Mesajı Gör`` satırından okunan adresin geçici anahtarı: satır bir
# grup adı taşımaz, yalnız o bildirimin kendi adresidir.
DEDUP_MESSAGE_LINK_KEY = "\x00mesaj-link"


def _dedup_badge_location(text: str) -> tuple[int, int, int] | None:
    """Rozetin konumunu bul: not artık mesajı kapatan son satırdır.

    Öncelik yeni biçimdedir (en son anlamlı satır). Eski gönderilmiş
    bildirimlerin rozeti ya en üstte ya fiyat satırının altındaydı; o konumlar
    yalnızca geriye dönük olarak taranır ki geçmiş mesajlar da düzeltilebilsin.
    """
    lines = text.splitlines(keepends=True)
    offset = len(text)
    for line in reversed(lines):
        content = line.rstrip("\r\n")
        offset -= len(line)
        if not content.strip():
            continue
        match = _dedup_badge_match(content)
        if not match:
            break  # son anlamlı satır rozet değil → yeni biçim yok
        count = _dedup_badge_count(match)
        return count, offset, offset + len(content)

    offset = 0
    for index, line in enumerate(lines[:8]):
        content = line.rstrip("\r\n")
        match = _dedup_badge_match(content)
        if not match:
            offset += len(line)
            continue
        previous_nonempty = next(
            (prior.strip() for prior in reversed(lines[:index]) if prior.strip()),
            "",
        )
        has_price_before = any(is_price_line(prior) for prior in lines[:index])
        if index != 0 and not is_price_line(previous_nonempty) \
                and (has_price_before or index > 2):
            offset += len(line)
            continue
        return _dedup_badge_count(match), offset, offset + len(content)
    return None


def _dedup_badge_count(match: re.Match[str]) -> int:
    """Eşleşmeden paylaşım sayısını çıkar; okunamazsa 1 say."""
    try:
        return max(1, int(match.group(1)))
    except (ValueError, IndexError):
        return 1


def _strip_dedup_badge_details(
    text: str | None,
) -> tuple[int, str, tuple[int, int, int]]:
    """Rozeti çıkar ve (eski başlangıç, eski bitiş, kalan ayraç) UTF-16 aralığını ver."""
    text = text or ""
    location = _dedup_badge_location(text)
    if location is None:
        return 1, text, (0, 0, 0)
    count, line_start, line_end = location

    if line_start == 0:
        tail = text[line_end:].lstrip("\r\n")
        if tail and tail.splitlines()[0].strip().startswith(DEDUP_SOURCES_PREFIX):
            tail = tail.split("\n", 1)[1] if "\n" in tail else ""
            tail = tail.lstrip("\r\n")
        removed_end = len(text) - len(tail)
        return count, tail, (0, utf16_length(text[:removed_end]), 0)

    before = text[:line_start]
    after = text[line_end:]
    prefix = before.rstrip("\r\n")
    suffix = after.lstrip("\r\n")
    replacement = "\n\n" if prefix and suffix else ""
    removed_start = len(prefix)
    removed_end = len(text) - len(suffix)
    base = prefix + replacement + suffix
    return count, base, (
        utf16_length(text[:removed_start]),
        utf16_length(text[:removed_end]),
        utf16_length(replacement),
    )


def strip_dedup_badge(text: str | None) -> tuple[int, str]:
    """Çoklu paylaşım notunu (ya da eski rozeti) metinden güvenle söker."""
    count, base, _ = _strip_dedup_badge_details(text)
    return count, base


def shift_bot_entities(
    entities: Sequence[dict[str, Any]] | None, delta: int,
) -> list[dict[str, Any]]:
    """Bot API entity sözlüklerini ``delta`` UTF-16 birimi sağa kaydır (kopyalayarak)."""
    shifted: list[dict[str, Any]] = []
    for entity in entities or []:
        if not isinstance(entity, dict):
            continue
        clone = dict(entity)
        try:
            clone["offset"] = int(clone.get("offset", 0)) + delta
        except (TypeError, ValueError):
            continue
        shifted.append(clone)
    return shifted


def shift_telethon_entities(entities: Sequence[Any] | None, delta: int) -> list[Any]:
    """Telethon entity'lerini ``delta`` UTF-16 birimi sağa kaydır (kopyalayarak)."""
    shifted: list[Any] = []
    for entity in entities or []:
        try:
            clone = copy.copy(entity)
            clone.offset = int(getattr(entity, "offset", 0) or 0) + delta
            clone.length = int(getattr(entity, "length", 0) or 0)
        except Exception:  # noqa: BLE001 - bilinmeyen entity taşınamıyorsa atlanır
            log.debug("Rozet kaydırma entity'yi taşıyamadı: %s", type(entity).__name__)
            continue
        if clone.length > 0 and clone.offset >= 0:
            shifted.append(clone)
    return shifted


def dedup_current_prefix(text: str | None) -> str:
    """Yalnızca geriye dönük uyumluluk için eski üst-rozet önekini döndür."""
    full = text or ""
    location = _dedup_badge_location(full)
    if location is None or location[1] != 0:
        return ""
    _, base, _ = _strip_dedup_badge_details(full)
    return full[:len(full) - len(base)] if base and full.endswith(base) else full


def dedup_badge_insertion(text: str, badge: str) -> tuple[str, int, int, int]:
    """Notu mesajın en sonuna KENDİ BLOĞU olarak ekle ve bildirimi orada kapat.

    Çağıran taraf kaynak grup adı satırını zaten metinden çıkarmıştır
    (bkz. ``_dedup_apply_badge``); böylece not, grup adının yerini alır.
    """
    if not badge:
        return text, 0, 0, 0
    body = text.rstrip(" \t\r\n")
    prefix = "\n\n" if body else ""
    new_text = f"{body}{prefix}{badge}"
    insert_at = len(body)
    insert_length = len(prefix) + len(badge)
    badge_offset = utf16_length(body + prefix)
    return new_text, insert_at, insert_length, badge_offset


def _rebase_entities_for_dedup_badge(
    entities: Sequence[Any] | None,
    removed: tuple[int, int, int],
    insert_at: int,
    insert_length: int,
    *,
    bot_api: bool = False,
) -> list[Any]:
    """Entity offset'lerini notun kaldırılıp sona eklenmesine göre taşı.

    Bütün konumlar (``removed``, ``insert_at``, ``insert_length``) entity'lerle aynı
    birimde, yani UTF-16 kod birimi olarak verilir.
    """
    old_start, old_end, replacement_length = removed
    removed_length = max(0, old_end - old_start)
    after_delta = replacement_length - removed_length
    result: list[Any] = []
    for entity in entities or []:
        if bot_api:
            if not isinstance(entity, dict):
                continue
            clone: Any = dict(entity)
            try:
                offset = int(clone.get("offset", 0))
                length = int(clone.get("length", 0))
            except (TypeError, ValueError):
                continue
        else:
            try:
                clone = copy.copy(entity)
                offset = int(getattr(entity, "offset", 0) or 0)
                length = int(getattr(entity, "length", 0) or 0)
            except Exception:  # noqa: BLE001 - bilinmeyen entity taşınamıyorsa atla
                continue
        if offset < 0 or length <= 0:
            continue
        end = offset + length
        if removed_length and offset < old_end and end > old_start:
            continue  # eski rozetin kendi biçimlendirmesi / çakışan entity
        if removed_length and offset >= old_end:
            offset += after_delta
        if offset < insert_at < offset + length:
            length += insert_length
        elif offset >= insert_at:
            offset += insert_length
        if bot_api:
            clone["offset"] = offset
            clone["length"] = length
        else:
            try:
                clone.offset = offset
                clone.length = length
            except Exception:  # noqa: BLE001 - entity kopyalanamazsa atla
                continue
        result.append(clone)
    return result


def dedup_rebased_bot_entities(
    entities: Sequence[dict[str, Any]] | None, old_prefix_len: int, new_prefix_len: int,
) -> list[dict[str, Any]]:
    """Rozet değişiminde Bot API entity'lerini taşı: eski rozet içindekiler atılır."""
    kept: list[dict[str, Any]] = []
    for entity in entities or []:
        if not isinstance(entity, dict):
            continue
        try:
            offset = int(entity.get("offset", 0))
        except (TypeError, ValueError):
            continue
        if offset >= old_prefix_len:
            kept.append(entity)
    return shift_bot_entities(kept, new_prefix_len - old_prefix_len)


def dedup_rebased_tl_entities(
    entities: Sequence[Any] | None, old_prefix_len: int, new_prefix_len: int,
) -> list[Any]:
    """Rozet değişiminde Telethon entity'lerini taşı: eski rozet içindekiler atılır."""
    kept: list[Any] = []
    for entity in entities or []:
        try:
            offset = int(getattr(entity, "offset", 0) or 0)
        except (TypeError, ValueError):
            continue
        if offset >= old_prefix_len:
            kept.append(entity)
    return shift_telethon_entities(kept, new_prefix_len - old_prefix_len)


def note_dedup_source(
    entry: dict[str, Any], source_name: str | None, link: str | None = None,
) -> None:
    """Tekrarı paylaşan grubun adını (ve varsa mesaj adresini) kayda ekle.

    ``links`` sözlüğü ``{grup adı: o grubun mesajı}`` tutar. Rozette yazan HER
    adres, o adın kendi grubunun mesajına gömülür (kullanıcı isteği): iki grup
    paylaştığında yalnız ilk gelen mesajın linki değil, ikisinin linki de
    bulunur. Eski kayıtlarda/önbellek taramasında link bilinmiyorsa ad yine
    yazılır, yalnız o ad link taşımaz.
    """
    name = (source_name or "").strip()
    if not name:
        return
    names = entry.setdefault("sources", [])
    if not isinstance(names, list):
        names = entry["sources"] = []
    if name not in names:
        names.append(name)
    url = str(link or "").strip()
    if url:
        links = entry.setdefault("links", {})
        if not isinstance(links, dict):
            links = entry["links"] = {}
        # Aynı grup bir fırsatı iki kez paylaşırsa ad tek kez yazılır; adres de
        # ilk (bildirimin kendisini taşıyan) paylaşımda kalır. Böylece rozetteki
        # ad, bildirimde gösterilen mesajı açar.
        links.setdefault(name, url)


def dedup_badge_links(entry: dict[str, Any], names: Sequence[str]) -> dict[str, str]:
    """Rozette yazılacak adların adresleri: ``{ad: t.me linki}``.

    ``entry["links"]`` varsa oradan gelir. Açılış taramasıyla kurulmuş eski
    kayıtlarda adres, metne gömülü grup adından ya da ``Mesajı Gör`` satırından
    okunur; hiçbiri yoksa o ad link olmadan yazılır (metin bozulmaz).
    """
    links = entry.get("links")
    result: dict[str, str] = {}
    if isinstance(links, dict):
        for name in names:
            url = links.get(name)
            if url:
                result[name] = str(url)
    if result:
        return result
    stored = entry.get("link_map")
    if isinstance(stored, dict):
        for name in names:
            url = stored.get(name)
            if url and url != DEDUP_MESSAGE_LINK_KEY:
                result[name] = str(url)
        if result:
            return result
        fallback = stored.get(DEDUP_MESSAGE_LINK_KEY)
        if fallback and len(list(dict.fromkeys(names))) == 1:
            # Yalnız tek ad ve yalnız ``Mesajı Gör`` satırı biliniyorsa o adres
            # o adın kendisidir (eski biçim: ad + ayrı link satırı).
            result[list(dict.fromkeys(names))[0]] = str(fallback)
    return result


def dedup_footer_name(text: str | None) -> str:
    """Gönderilmiş bir bildirimin en altındaki kaynak grup adını bul.

    Açılış taramasında (``dedup_preload``) eski bildirimlerin tekrar kaydına
    yazılır; tekrar yakalandığında bu satır kaldırılıp yerine çoklu paylaşım
    notu konur. Son blok yalnızca şu koşullarda grup adı sayılır: bildirim
    düzenine ait bir iz vardır (yeni düzende ``👉 Ürün Linki 👈``; eski düzende
    ``Mesajı Gör`` satırı — ham forward'larda ikisi de yoktur, yani ad bloğu da
    yoktur), tek satırdır, makul uzunluktadır ve link/etiket taşımaz.
    """
    blocks = [block.strip() for block in (text or "").split("\n\n") if block.strip()]
    if len(blocks) < 2:
        return ""
    last = blocks[-1]
    if "\n" in last or len(last) > 80 or last.startswith(("🔗", "📌", "🚨")):
        return ""
    if MESSAGE_LINK_LABEL in last or "t.me/" in last.casefold():
        return ""
    has_layout_mark = any(
        MESSAGE_LINK_LABEL in block
        or PRODUCT_LINK_TEXT in block
        or PRODUCT_LINK_LABEL in block
        or block.startswith("👉")
        for block in blocks[:-1]
    )
    if not has_layout_mark:
        return ""
    return last


def bot_history_ids_shared(chat_id: int) -> bool:
    """Only channels/supergroups have a shared MTProto/Bot API ID namespace."""
    return utils.resolve_id(chat_id)[1] is types.PeerChannel


def next_poll_interval(current: float, flood_wait: float) -> float:
    # Do not immediately return to the rate that Telegram just rejected.
    return max(current, min(300.0, max(current * 2, flood_wait, 10.0)))


def recover_poll_interval(current: float, base: float) -> float:
    """Başarılı (ve hızlı) bir yoklamadan sonra aralığı kademeli gevşet.

    Her başarılı yoklama aralığı yarıya indirir (taban ``base``). Yoklamanın
    kendisi geçerli aralıkla beklediği için istek hızı bir anda artmaz;
    300 sn'den 3 sn'ye dönüş bile art arda başarılı yoklamalar gerektirir.
    """
    if current <= base:
        return current
    return max(base, current / 2)


def offer_terms(obj: Any, *, prefer_header: bool = False) -> tuple[float | None, str]:
    """Güvenilir ürün fiyatını döndür; koşul, mağaza ve bağlantılar kimlik değildir.

    ``prefer_header`` yalnız kendi composed bildirimlerimizi geçmişten yüklerken
    kullanılır. Canlı kaynakta eski bir başlık ile daha düşük gövde fiyatı birlikte
    bulunabilir; bu nedenle varsayılan davranış gövdeyi ayrıştırmaya devam eder.
    """
    text = message_text(obj)
    if prefer_header:
        first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
        header_price, header_title = split_header_line(first_line)
        if header_price and header_title and not price_filter.is_generic_title(header_title):
            # Composed bildirimin fiyatı zaten canlı yolda doğrulanmıştır. Restart
            # sonrası gövdedeki eski/kupon/kampanya tutarlarıyla yeniden belirsizlik
            # üretmek, aynı teklifi ``None -> fiyat`` gibi gösterip ikinci bildirim
            # attırıyordu. Başlık yoluyla aynı sözleşmeyi fiyat için de kullan.
            header_info = price_filter.extract_price(header_price)
            if header_info.value is not None and header_info.confidence in {"high", "medium"}:
                return header_info.value, ""
    strike = price_filter.strike_spans_from_entities(text, entities_for_text(obj, text))
    info = price_filter.extract_price(text, strike)
    value = info.value if info.confidence in {"high", "medium"} else None
    return value, ""


_UPGRADE_CONDITION_RE = re.compile(
    r"\b(?:ikinci\s+el|2\s*\.\s*el|yenilenmiş|yenilenmis|refurbished|renewed|"
    r"açık\s+kutu|acik\s+kutu|depo\s+ürünü|depo\s+urunu)\b", re.IGNORECASE,
)
_ASIN_RE = re.compile(r"/(?:dp|gp/product)/([a-z0-9]{10})(?:[/?#]|$)", re.IGNORECASE)


def product_asins(obj: Any) -> set[str]:
    return {match.group(1).upper() for link in extract_links(obj)
            if (match := _ASIN_RE.search(link["url"]))}


def product_link_ids(obj: Any) -> set[str]:
    """Stable explicit product references used only as a conservative dedup bridge."""
    identities: set[str] = set()
    for link in extract_links(obj):
        try:
            parsed = urllib.parse.urlsplit(clean_url(link["url"]))
        except ValueError:
            continue
        host = (parsed.hostname or "").lower().rstrip(".")
        path = parsed.path.rstrip("/")
        # Hepsiburada's short links are stable product references; the t.me
        # source permalink and generated search buttons are not product IDs.
        if host == "app.hb.biz" and path:
            identities.add(f"hb:{path.lower()}")
    return identities


def plain_offer_for_upgrade(obj: Any, offer: tuple | None) -> bool:
    """Only a clearly standalone normal offer may be replaced automatically."""
    if not offer or offer[0] is None:
        return False
    text = message_text(obj)
    if _UPGRADE_CONDITION_RE.search(text):
        return False
    strike = price_filter.strike_spans_from_entities(text, entities_for_text(obj, text))
    candidates = price_filter.find_candidates(text, strike)
    prices = [c for c in candidates if c.cls == "price" and c.role in {"normal", "pack_total"}]
    return (len({c.value for c in prices}) == 1 and bool(prices)
            and _dedup_whole_tl(prices[0].value) == _dedup_whole_tl(offer[0])
            and not any(c.role in {"multi_unit", "condition_price", "member_price", "discounted", "basket_coupon", "product_coupon"}
                        for c in candidates))


def _simple_linked_offer_price(obj: Any, offer: tuple | None) -> bool:
    """Accept only one clear, unconditional final price for a linked product."""
    if not offer or offer[0] is None:
        return False
    text = message_text(obj)
    if _UPGRADE_CONDITION_RE.search(text):
        return False
    strike = price_filter.strike_spans_from_entities(text, entities_for_text(obj, text))
    info = price_filter.extract_price(text, strike)
    if info.value is None or info.confidence not in {"high", "medium"} or info.value != offer[0]:
        return False
    candidates = price_filter.find_candidates(text, strike)
    blocked_roles = {"multi_unit", "unit", "condition_price", "member_price", "basket_coupon", "product_coupon"}
    if any(candidate.role in blocked_roles for candidate in candidates):
        return False
    values = {candidate.value for candidate in candidates if candidate.cls == "price"}
    return values == {offer[0]}


def is_referenced_product_price_drop(
    entry: dict[str, Any], title: str, obj: Any,
    offer: tuple | None, now: float,
) -> bool:
    """Allow a safe replacement only for the same explicit item link and a lower price.

    Generic matching still announces genuine price drops separately. This narrow
    bridge is reserved for repeated listings whose product URL/ASIN and title
    both agree, so a cheaper copy can replace the old notice and carry the share
    badge without merging similarly named variants.
    """
    old = entry.get("offer")
    old_links = set(entry.get("product_link_ids") or ())
    old_links.update(f"asin:{asin}" for asin in entry.get("product_asins") or ())
    new_links = product_link_ids(obj)
    new_links.update(f"asin:{asin}" for asin in product_asins(obj))
    if (entry.get("failed") or entry.get("pending")
            or not _multibuy_editable_valid(entry)
            or not old_links or old_links != new_links
            or not titles_match_product(entry.get("title"), title)
            or not old or old[0] is None or not offer or offer[0] is None
            or not better_or_different_offer(old, offer)):
        return False
    first_seen = float(entry.get("first_seen", 0) or 0)
    if now < first_seen or now - first_seen > 180:
        return False
    if not _simple_linked_offer_price(entry.get("text") or "", old):
        return False
    return _simple_linked_offer_price(obj, offer)


def _multibuy_entry_eligible(entry: dict[str, Any], title: str, offer: tuple | None, now: float) -> bool:
    """Entry'nin 3 dakikalık bridge için uygun olup olmadığını kontrol et."""
    old = entry.get("offer")
    if (entry.get("preload") or not entry.get("plain_offer") or entry.get("failed")
            or entry.get("pending") or now - float(entry.get("first_seen", 0)) > 180
            or now < float(entry.get("first_seen", 0))
            or dedup_key(entry.get("title")) != dedup_key(title)
            or not old or old[0] is None or not offer or offer[0] is None
            or not better_or_different_offer(old, offer)):
        return False
    return True


def _multibuy_editable_valid(entry: dict[str, Any]) -> bool:
    """Kaydın düzenlenebilir (bot/account) mesaj ID'si geçerli mi?"""
    if entry.get("editable") == "bot":
        return isinstance(entry.get("bot_message_id"), int)
    if entry.get("editable") == "account":
        return isinstance(entry.get("account_message_id"), int)
    return False


def _multibuy_blocked_by_condition_or_asin(entry: dict[str, Any], obj: Any) -> bool:
    """Upgrade bloklayıcı koşul (ikinci el vb.) veya ASIN uyuşmazlığı var mı?"""
    text = message_text(obj)
    if _UPGRADE_CONDITION_RE.search(text):
        return True
    old_asins, new_asins = entry.get("product_asins") or set(), product_asins(obj)
    if old_asins and new_asins and old_asins != new_asins:
        return True
    return False


def _multibuy_price_structure_valid(entry: dict[str, Any], obj: Any, offer: tuple | None) -> bool:
    """Mesajda eski taban fiyat + daha ucuz multi-buy yapısı doğru mu?"""
    old = entry.get("offer")
    text = message_text(obj)
    strike = price_filter.strike_spans_from_entities(text, entities_for_text(obj, text))
    candidates = price_filter.find_candidates(text, strike)
    base = [c for c in candidates if c.cls == "price" and c.role in {"normal", "pack_total"}
            and c.strong and _dedup_whole_tl(c.value) == _dedup_whole_tl(old[0])]
    cheaper = [c for c in candidates if c.role == "multi_unit" and c.value == offer[0]]
    normal_values = {c.value for c in candidates if c.cls == "price" and c.role in {"normal", "pack_total"}}
    multi_values = {c.value for c in candidates if c.role == "multi_unit"}
    return (len(base) == 1 and len(cheaper) == 1 and base[0].line == cheaper[0].line
            and len(normal_values) == 1 and len(multi_values) == 1
            and not any(c.role in {"condition_price", "member_price", "discounted", "basket_coupon", "product_coupon"}
                        for c in candidates))


def is_referenced_multibuy_upgrade(entry: dict[str, Any], title: str, obj: Any,
                                    offer: tuple | None, now: float) -> bool:
    """Conservative 3-minute bridge: same exact title, old base + lower multi-buy."""
    if not _multibuy_entry_eligible(entry, title, offer, now):
        return False
    if not _multibuy_editable_valid(entry):
        return False
    if _multibuy_blocked_by_condition_or_asin(entry, obj):
        return False
    return _multibuy_price_structure_valid(entry, obj, offer)

def dedup_offer_key(title: str, offer: tuple[float | None, str] | None = None) -> str | None:  # noqa: ARG001 - dış API uyumu
    """Tekrar anahtarı ürün adıdır; fiyat yalnızca aynı ürünün fiyat değişimini denetler.

    `offer` geriye dönük uyumluluk için korunur; anahtar yalnızca başlığa
    bağlıdır. Çağıranlar fiyatı ayrıca `better_or_different_offer` ile
    karşılaştırır.
    """
    return dedup_key(title)


_TITLE_STOPWORDS = frozenset({"ve", "ile", "icin", "olan"})

_TITLE_BENIGN_EXTRA_WORDS = frozenset({
    "orijinal", "yeni", "profesyonel", "otomatik", "dijital", "elektrikli",
    "sarjli", "tasinabilir", "cok", "amacli", "fonksiyonlu",
})

_TITLE_COLOR_WORDS = frozenset({
    "siyah", "beyaz", "gri", "antrasit", "kirmizi", "mavi", "lacivert",
    "yesil", "sari", "turuncu", "mor", "lila", "pembe", "kahverengi", "bej",
    "krem", "altin", "gumus", "bronz", "bakir", "bordo", "turkuaz", "haki",
    "vizon", "ekru", "pudra", "mercan", "fuşya", "fusya", "hardal", "kiremit",
    "indigo", "uzay", "gece", "yildiz", "titanyum", "grafit",
    "black", "white", "grey", "gray", "red", "blue", "green", "yellow",
    "pink", "purple", "silver", "gold", "rose", "midnight", "starlight",
    "graphite", "titanium", "navy", "beige", "brown", "orange",
})

_TITLE_VARIANT_WORDS = frozenset({
    "pro", "max", "plus", "ultra", "mini", "lite", "slim", "fe", "se", "ti",
    "super", "oled", "qled", "wifi", "lte", "cellular", "kablolu", "kablosuz",
    "wireless", "bluetooth", "xs", "s", "m", "l", "xl", "xxl", "xxxl",
    "erkek", "kadin", "cocuk", "bebek", "unisex",
    "kilif", "koruyucu", "kordon", "kayis", "adaptor", "sarj", "kablo",
    "yedek", "filtre", "baslik", "kartus", "torba", "standi", "tutucu",
    "jakarli", "pamuklu", "keten", "kadife", "saten", "mat", "parlak",
    "simli", "desenli", "cizgili",
})

_TITLE_UNIT_MAP = {
    "ml": "ml", "cl": "cl",
    "l": "l", "lt": "l", "litre": "l", "liter": "l",
    "kg": "kg", "kilogram": "kg",
    "g": "g", "gr": "g", "gram": "g", "mg": "mg",
    "gb": "gb", "tb": "tb", "mb": "mb",
    "mah": "mah", "wh": "wh",
    "kw": "kw", "kilowatt": "kw", "w": "w", "watt": "w",
    "v": "v", "volt": "v",
    "hz": "hz", "ghz": "ghz", "mhz": "mhz",
    "mp": "mp", "bar": "bar", "rpm": "rpm",
    "cm": "cm", "santim": "cm", "santimetre": "cm",
    "mm": "mm", "milimetre": "mm",
    "m": "m", "metre": "m",
    "inc": "inc", "inch": "inc",
    "adet": "adet", "ad": "adet", "tane": "adet",
    "li": "li", "lu": "li", "parca": "parca",
}

_TITLE_DIM_RE = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)(?:\s*[x×]\s*(\d+(?:\.\d+)?))?"
    r"(?:\s*(cm|mm|m))?\b",
)
_TITLE_INCH_RE = re.compile(r'(\d+(?:\.\d+)?)\s*(?:"|\'\'|”|″|\binc\b|\binch\b)')
_TITLE_NUM_UNIT_RE = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*("
    + "|".join(sorted(_TITLE_UNIT_MAP, key=len, reverse=True))
    + r")\b",
)
_TITLE_HYPHEN_MODEL_RE = re.compile(r"\b(\d+)-([a-z])\b")
_TITLE_WORD_RE = re.compile(r"[a-z0-9]+(?:\.\d+[a-z0-9]*)?|\+")
_MEASUREMENT_TOKEN_RE = re.compile(
    r"^\d+(?:\.\d+)?(?:x\d+(?:\.\d+)?)*(?:ml|cl|l|kg|g|mg|gb|tb|mb|mah|wh|"
    r"kw|w|v|hz|ghz|mhz|mp|bar|rpm|cm|mm|m|inc|adet|li|parca|k|d)$",
)


def _canonicalize_title_units(text: str) -> str:
    text = re.sub(r"(\d),(\d)", r"\1.\2", text)

    def _dim_repl(m: re.Match[str]) -> str:
        parts = [m.group(1), m.group(2)]
        if m.group(3):
            parts.append(m.group(3))
        return "x".join(parts) + (m.group(4) or "")

    text = _TITLE_DIM_RE.sub(_dim_repl, text)
    text = _TITLE_INCH_RE.sub(r"\1inc", text)
    text = _TITLE_NUM_UNIT_RE.sub(
        lambda m: f"{m.group(1)}{_TITLE_UNIT_MAP[m.group(2)]}", text,
    )
    text = _TITLE_HYPHEN_MODEL_RE.sub(r"\1\2", text)
    return text


def _stem_title_word(word: str) -> str:
    if (
        word == "+"
        or any(ch.isdigit() for ch in word)
        or word in _TITLE_COLOR_WORDS
        or word in _TITLE_VARIANT_WORDS
    ):
        return word
    if word == "makinasi":
        word = "makinesi"
    if len(word) >= 6 and word.isalpha():
        if word.endswith(("si", "su")) and len(word) - 2 >= 4:
            return word[:-2]
        if word.endswith(("i", "u")) and len(word) - 1 >= 4:
            return word[:-1]
    return word


def _title_profile(title: str | None) -> dict[str, Any] | None:
    """Başlıktan anlamlı kelimeler, ilk-3-kelime sinyali ve ayırt edici nitelikleri çıkar."""
    if not title or price_filter.is_generic_title(title):
        return None
    canonical = _canonicalize_title_units(_fold_title(title))
    raw_tokens = _TITLE_WORD_RE.findall(canonical)
    words = [t for t in raw_tokens if t not in _TITLE_STOPWORDS]
    if not words:
        return None
    specs = tuple(sorted(w for w in words if w == "+" or any(ch.isdigit() for ch in w)))
    has_model_code = any(
        len(w) >= 4
        and any(ch.isalpha() for ch in w)
        and any(ch.isdigit() for ch in w)
        and not _MEASUREMENT_TOKEN_RE.fullmatch(w)
        for w in words
    )
    colors = frozenset(w for w in words if w in _TITLE_COLOR_WORDS)
    variants = frozenset(w for w in words if w in _TITLE_VARIANT_WORDS)
    stems = [_stem_title_word(w) for w in words]
    return {
        "words": words,
        "stems": stems,
        "stem_set": frozenset(stems),
        "specs": specs,
        "has_model_code": has_model_code,
        "colors": colors,
        "variants": variants,
    }


def _title_has_strong_identity(title: str | None) -> bool:
    prof = _title_profile(title)
    return bool(prof and len(prof["stems"]) >= 3)


def _title_model_codes(profile: dict[str, Any]) -> set[str]:
    """Model kodları ölçü/kapasite token'larından ayır."""
    return {
        word for word in profile["words"]
        if len(word) >= 4 and any(ch.isalpha() for ch in word) and any(ch.isdigit() for ch in word)
        and not _MEASUREMENT_TOKEN_RE.fullmatch(word)
    }


def _title_capacities(profile: dict[str, Any]) -> set[str]:
    """Açık kapasite farkları (128 GB/256 GB, 150 ml/250 ml) ürün varyantıdır."""
    return {
        word for word in profile["words"]
        if re.fullmatch(r"\d+(?:\.\d+)?(?:gb|tb|ml|cl|mah|wh|kg|g|adet)", word)
    }


def _ordered_title_prefix_match(pa: dict[str, Any], pb: dict[str, Any]) -> bool:
    """Uzun katalog adının, kırılmış kısa adın güvenli devamı olup olmadığını sına.

    Sıralı en az 10 kelimelik önek ve sayısal/model kimliği şarttır. Son kelime
    ``Kapan`` → ``Kapanma`` gibi kırılmış olabilir. Uzun taraftaki ek sayılar
    yalnız açık model koduna (``DST7030/20``) ait olabilir; yeni kapasite,
    ölçü, yıl veya başka sayısal varyantlar bu yolla birleştirilmez.
    """
    words_a, words_b = pa["words"], pb["words"]
    if len(words_a) == len(words_b):
        return False
    short, long = (words_a, words_b) if len(words_a) < len(words_b) else (words_b, words_a)
    short_profile = pa if len(words_a) < len(words_b) else pb
    if len(short) < 10 or not (short_profile["specs"] or short_profile["has_model_code"]):
        return False
    boundary = len(short) - 1
    if short[:boundary] != long[:boundary]:
        return False
    short_last, long_last = short[-1], long[boundary]
    if short_last != long_last and (
        len(short_last) < 4 or not short_last.isalpha()
        or not long_last.startswith(short_last)
    ):
        return False
    suffix = long[len(short):]
    if not suffix:
        return False
    model_codes = _title_model_codes({"words": suffix})
    for index, word in enumerate(suffix):
        if not any(char.isdigit() for char in word):
            continue
        if word in model_codes:
            continue
        if word.isdigit() and index > 0 and suffix[index - 1] in model_codes:
            continue  # ``DST7030/20`` kodunun ``20`` parçası
        return False
    return True


def titles_match_product(title_a: str | None, title_b: str | None) -> bool:
    """Başlıkları karşılaştır: aynı model kodu yeterli güçlü eşleşmedir.

    Mağaza, bağlantı, Prime/kargo/kupon koşulları ürün adını geçersiz kılamaz.
    Açık renk, kapasite ve ürün-varyant çelişkileri ise korunur.
    """
    key_a, key_b = dedup_key(title_a), dedup_key(title_b)
    if not key_a or not key_b:
        return False
    if key_a == key_b:
        return True
    pa, pb = _title_profile(title_a), _title_profile(title_b)
    if not pa or not pb or pa["words"][0] != pb["words"][0]:
        return False

    # A shared explicit model code bridges editorial title variations such as
    # inch/cm descriptions and optional feature lists.
    shared_codes = _title_model_codes(pa) & _title_model_codes(pb)
    if shared_codes:
        critical_variants = _TITLE_VARIANT_WORDS - {"ultra"}
        variants_a = pa["variants"] & critical_variants
        variants_b = pb["variants"] & critical_variants
        return (pa["colors"] == pb["colors"]
                and variants_a == variants_b
                and _title_capacities(pa) == _title_capacities(pb))

    codes_a, codes_b = _title_model_codes(pa), _title_model_codes(pb)
    if codes_a and codes_b and codes_a.isdisjoint(codes_b):
        return False

    # With no shared model code, allow only close same-brand title variants. Exact
    # model/spec, color and product-type conflicts still prevent a merge.
    if (pa["colors"] != pb["colors"] or pa["variants"] != pb["variants"]
            or (("+" in pa["words"]) != ("+" in pb["words"]))):
        return False
    capacities_a, capacities_b = _title_capacities(pa), _title_capacities(pb)
    if capacities_a and capacities_b and capacities_a != capacities_b:
        return False
    if _ordered_title_prefix_match(pa, pb):
        # Genel spec-set karşılaştırması, uzun addaki ``DST7030/20`` kodunu
        # ürün farkı sanıyordu. Yukarıdaki sıralı önek ve varyant kontrolleri
        # geçildiyse model koduna ait bu ek değerler artık çelişki değildir.
        return True
    specs_a = set(pa["specs"])
    specs_b = set(pb["specs"])
    if specs_a and specs_b and specs_a != specs_b:
        return False
    words_a, words_b = pa["stem_set"], pb["stem_set"]
    overlap = len(words_a & words_b)
    return overlap >= 4 and overlap / min(len(words_a), len(words_b)) >= 0.7


def _find_matching_dedup_key(
    cache: dict[str, dict[str, Any]], title: str | None,
    asins: Iterable[str] = (), link_ids: Iterable[str] = (),
) -> str | None:
    """Find an earlier item by strong title match or identical explicit product ID."""
    current_asins = set(asins or ())
    current_links = set(link_ids or ())
    can_match_title = _title_has_strong_identity(title)
    if not can_match_title and not (current_asins or current_links):
        return None
    best_key: str | None = None
    best_rank: tuple[int, float] = (-1, -1.0)
    for cand_key, cand_entry in cache.items():
        if cand_entry.get("failed"):
            continue
        same_asins = bool(current_asins) and current_asins == set(cand_entry.get("product_asins") or ())
        same_links = bool(current_links) and current_links == set(cand_entry.get("product_link_ids") or ())
        same_title = can_match_title and titles_match_product(cand_entry.get("title"), title)
        if not (same_asins or same_links or same_title):
            continue
        rank = (0 if cand_entry.get("pending") else 1, float(cand_entry.get("last_seen", 0.0)))
        if rank > best_rank:
            best_rank, best_key = rank, cand_key
    return best_key


def _keep_detailed_prefix_title(entry: dict[str, Any], candidate: str) -> None:
    """Güvenli uzun-adevamı eşleşmesinde model kodlu zengin adı kayda taşı.

    Kısa ad kayıtta bırakılırsa iki farklı uzun model, aynı kısa ad üzerinden
    zincirleme biçimde tek fırsata birleşebilir. Yalnız doğrulanmış sıralı
    önek eşleşmesinde ve aday gerçekten daha ayrıntılıysa başlığı zenginleştir;
    dedup anahtarını, sayacı veya gönderilmiş bildirimi değiştirme.
    """
    existing = entry.get("title")
    if not isinstance(existing, str) or not candidate or existing == candidate:
        return
    pa, pb = _title_profile(existing), _title_profile(candidate)
    if not pa or not pb or len(pb["words"]) <= len(pa["words"]):
        return
    if titles_match_product(existing, candidate) and _ordered_title_prefix_match(pa, pb):
        entry["title"] = candidate


def _dedup_whole_tl(value: Any) -> int | None:
    """Yalnızca dedup karşılaştırmasında kullanılan tam-TL değeri.

    Gerçek fiyatın kendisi değildir: hiçbir yere saklanmaz, formatlanmaz ve
    gösterime/DM eşiklerine girmez. ``None``, negatif ya da sonlu olmayan
    girdiler asla 0'a çevrilmez; bilinmeyen fiyat bilinmeyen kalır (``None``).
    Fiyatlar negatif olamadığı için ``int()`` kesmesi taban (floor) ile aynıdır.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    return int(number)


def better_or_different_offer(old: tuple | None, new: tuple | None) -> bool:
    if old is None or new is None:
        return False
    if new[0] is None:
        return False
    if old[0] is None:
        return True
    # Kuruş farkı tek başına yeni fırsat değildir: aynı tam TL içindeki
    # fiyatlar (örn. 1.299,90 ↔ 1.299) karşılaştırmada aynı kabul edilir.
    # Gerçek fiyatlar (old[0]/new[0]) aynen saklanır ve gösterilir; burada
    # yalnızca geçici tam-TL karşılaştırma değeri türetilir.
    old_whole = _dedup_whole_tl(old[0])
    new_whole = _dedup_whole_tl(new[0])
    if old_whole is not None and new_whole is not None and old_whole == new_whole:
        return False
    # Yalnız tam TL'ye kırpma/yuvarlama ölçeğindeki farkları tolere et.
    # Hem 1 TL hem %0,01 üst sınırı: küçük fiyatlı ürünün gerçek düşüşünü yutma.
    rounded = (float(old[0]).is_integer() or float(new[0]).is_integer())
    tolerance = min(1.0, min(old[0], new[0]) * 0.0001) if rounded else 0.01
    return new[0] < old[0] - max(0.01, tolerance)


def new_dedup_entry(title: str, token: Any, source_name: str | None = None,
                    footer_name: str | None = None,
                    preload: bool = False) -> dict[str, Any]:
    """Gönderim rezervasyonu konmuş yeni önbellek kaydı (``pending=True``).

    ``sources`` ilk kopyayı paylaşan grupları tutar; çoklu paylaşım notu
    ("📌 N kere paylaşıldı: <gruplar>") bu adlarla kurulur (bkz. ``dedup_badge``).
    ``links`` ise ``{grup adı: o grubun mesaj adresi}`` eşlemesidir: rozette
    yazan her ad kendi mesajına gömülür (kullanıcı isteği — şimdiye kadar yalnız
    ilk gelen kopyanın adresi kullanılıyordu). ``footer_name`` bildirimin en
    altındaki kaynak grup adı satırıdır; tekrar yakalandığında bu satır
    kaldırılıp yerine çoklu paylaşım notu yazılır (kullanıcı isteği: "hangi
    gruptan geldiğini kaldırıp N kere paylaşıldı şu şu gruplar diye yazsın").
    """
    now = time.time()
    name = (source_name or "").strip()
    return {
        "title": title,
        "preload": bool(preload),     # kayıt açılış taramasından mı geldi?
        "sources": [name] if name else [],
        # "links" ({grup adı: t.me linki}) yalnızca bir paylaşımın adresi
        # bilindiğinde kurulur (bkz. note_dedup_source); açılış taraması kendi
        # eşlemesini "link_map" altında tutar.
        "footer_name": (footer_name or "").strip(),
        "count": 1,
        "first_seen": now,
        "last_seen": now,
        "pending": True,     # ilk mesaj henüz gönderilmedi
        "failed": False,     # gönderim yarım kaldı / devralındı
        "ready": asyncio.Event(),
        "token": token,      # rezervasyon sahibi (devralmayı ayırt eder)
        "editable": None,    # "bot" | "account" | None
        "bot_message_id": None,
        "account_message_id": None,
        "kind": "text",      # "text" | "media" (bot düzenlemesinin yöntemini seçer)
        "text": "",          # ilk mesajın güncel tam metni (not işlenirken güncellenir)
        "entities_bot": None,  # Bot API entity sözlükleri (güncel metne göre)
        "entities_tl": None,   # Telethon entity'leri (güncel metne göre)
        "keyboard": None,
        "edit_lock": asyncio.Lock(),
        "last_edit": 0.0,
        "edit_fails": 0,     # üst üste rozet hatası (2'de düzenleme bırakılır)
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
            if age > DEDUP_RESERVE_TTL and (owner is None or owner.done()):
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


# ---------------------------------------------------------------------------
# Komutlar
# ---------------------------------------------------------------------------

# Canonical command names are shared by the help catalog and dispatch paths.
COMMAND_HELP = "/komutlar"
COMMAND_START = "/start"
COMMAND_STATUS = "/durum"
COMMAND_DM_STATUS = "/dmfiltre"
COMMAND_DM_FILTER_OPEN = "/dmfiltreac"
COMMAND_DM_FILTER_CLOSE = "/dmfiltrekapat"
COMMAND_SETTINGS_MENU = "/ayar"
COMMAND_SETTINGS_ADD = "/ekle"
COMMAND_SETTINGS_REMOVE = "/cikar"
COMMAND_SETTINGS_SAVE = "/kaydet"
COMMAND_SETTINGS_REVERT = "/iptal"
COMMAND_TG_FILTER_OPEN = "/tgfiltreac"
COMMAND_TG_FILTER_CLOSE = "/tgfiltrekapat"
COMMAND_SOURCES = "/kaynaklar"
COMMAND_SOURCE_TEST = "/kaynaktest"
COMMAND_ANALYZE = "/analiz"
COMMAND_TEST = "/test"
COMMAND_ID = "/id"
COMMAND_RESTART = "/restart"
COMMAND_WEB_TRACK = "/webtakip"

# Bu katalog tek kaynak olduğu için /komutlar, README tablosu ve BotFather
# virgülle ayrılmış listesi aynı sırayla üretilir. Komutlar işleve göre
# gruplandırılmış, üstten alta okunış sırasıyla:
#   1) Temel komutlar
#   2) Liste yönetimi
#   3) Telegram indirim filtreleri
#   4) Kişisel bildirim filtreleri (DM)
#   5) Web ürün takibi (/webtakip menüsü içinde gömülü alt komutlar da görünür)
#   6) Gelişmiş araçlar
#   7) Yönetim komutu
COMMAND_DESCRIPTIONS = (
    # --- 1) Temel komutlar ---
    (COMMAND_HELP, "Tüm desteklenen komutları ve açıklamalarını kategoriler halinde listeler."),
    (COMMAND_START, "Özel sohbet kullanımını açıklar; fırsat hedefini değiştirmez."),
    (COMMAND_STATUS, "Takipçinin dinleme durumunu, hedefini, sayaçlarını ve filtre durumunu gösterir."),
    (COMMAND_SETTINGS_MENU, "Bot yönetim merkezi: liste, TG filtre, DM filtre ve web ürün takibi bölümlerini gösterir."),
    # --- 2) Liste yönetimi ---
    (COMMAND_SETTINGS_ADD, "Liste seçip kayıt ekleme taslağı başlatır (dahil/hariç/grup/dmdahil/dmharic/dmfiyat/web ürün takibi)."),
    (COMMAND_SETTINGS_REMOVE, "Liste seçip kayıt çıkarma taslağı başlatır (dahil/hariç/grup/dmdahil/dmharic/dmfiyat/web ürün takibi)."),
    (COMMAND_SETTINGS_SAVE, "Bekleyen liste taslağını kaydeder (DM listeleri dahil)."),
    (COMMAND_SETTINGS_REVERT, "Bekleyen ekleme veya çıkarma işlemini iptal eder."),
    # --- 3) Telegram indirim filtreleri ---
    (COMMAND_TG_FILTER_OPEN, "Telegram indirim akışında dahili, harici veya iki filtreyi açar."),
    (COMMAND_TG_FILTER_CLOSE, "Telegram indirim akışında dahili, harici veya iki filtreyi kapatır."),
    # --- 4) Kişisel bildirim filtreleri (DM) ---
    (COMMAND_DM_STATUS, "Kişisel bildirim filtresinin durumunu, dahil/hariç listesini ve fiyat sınırını gösterir."),
    (COMMAND_DM_FILTER_OPEN, "Kişisel bildirim filtresini açar."),
    (COMMAND_DM_FILTER_CLOSE, "Kişisel bildirim filtresini kapatır; kelime listeleri korunur."),
    # --- 5) Web ürün takibi (Akakçe ProductTracker) ---
    (COMMAND_WEB_TRACK, "Akakçe ürün takip menüsünü açar; Akakçe linkini gönderip hedef fiyatı belirlersin."),
    ("/webtakipac", "Web ürün fiyat takibini etkinleştirir."),
    ("/webtakipkapat", "Web ürün fiyat takibini durdurur; ana Telegram indirim akışını durdurmaz."),
    ("/webtakipler", "Takip edilen web ürünlerini, hedeflerini ve son kontrol durumunu listeler."),
    ("/webtakipsil", "Numarayla takip edilen web ürününü siler."),
    ("/webtakipiptal", "Bekleyen web ürün takip işlemini iptal eder."),
    ("/webtakipyardim", "Web ürün takibinin ayrıntılı kullanımını gösterir."),
    # --- 6) Gelişmiş araçlar ---
    (COMMAND_SOURCES, "İzlenen kaynakları ve çözülemeyenleri listeler."),
    (COMMAND_SOURCE_TEST, "Her kaynağın son ham mesajını ve yerel işlem yolunu denetler; canlı event sayısını raporlar."),
    (COMMAND_ANALYZE, "Kaynak geçmişindeki başlıkları analiz eder; örnek: /analiz 100 tümü veya "
                       "/analiz 1000 tcl 65 (eşleşen ürünleri 'ürün adı - fiyat' olarak listeler)."),
    (COMMAND_TEST, "Hedef gruba deneme mesajı gönderir; sonucu komut sohbetinde bildirir."),
    (COMMAND_ID, "Bulunduğun sohbetin ve kullanıcının kimliğini gösterir."),
    # --- 7) Yönetim ---
    (COMMAND_RESTART, "Yeni takipçi çalışması başlatır; GH_PAT gerekir."),
)
_HELP_CATEGORIES = (
    ("**📋 Temel komutlar**", {COMMAND_HELP, COMMAND_START, COMMAND_STATUS, COMMAND_SETTINGS_MENU}),
    ("**📝 Liste yönetimi**", {COMMAND_SETTINGS_ADD, COMMAND_SETTINGS_REMOVE, COMMAND_SETTINGS_SAVE, COMMAND_SETTINGS_REVERT}),
    ("**🔎 Telegram indirim filtreleri**", {COMMAND_TG_FILTER_OPEN, COMMAND_TG_FILTER_CLOSE}),
    ("**📩 Kişisel bildirim filtreleri (DM)**", {COMMAND_DM_STATUS, COMMAND_DM_FILTER_OPEN, COMMAND_DM_FILTER_CLOSE}),
    ("**🌐 Web ürün takibi**", {COMMAND_WEB_TRACK, "/webtakipac", "/webtakipkapat", "/webtakipler", "/webtakipsil", "/webtakipiptal", "/webtakipyardim"}),
    ("**🛠️ Gelişmiş araçlar**", {COMMAND_SOURCES, COMMAND_SOURCE_TEST, COMMAND_ANALYZE, COMMAND_TEST, COMMAND_ID}),
    ("**⚙️ Yönetim**", {COMMAND_RESTART}),
)

_HELP_LINES: list[str] = []
for _category_title, _category_commands in _HELP_CATEGORIES:
    _HELP_LINES.append("")
    _HELP_LINES.append(_category_title)
    for _command, _description in COMMAND_DESCRIPTIONS:
        if _command in _category_commands:
            _HELP_LINES.append(f"{_command} - {_description}")
HELP_TEXT = "\n".join(_HELP_LINES)


def _bot_api_stats_line() -> str:
    """P0 ölçümleme özeti: Bot API 429 sayaçları (token/içerik yazılmaz)."""
    if not NOTIFY_BOT_TOKEN:
        return "• Bot API hız sınırı: token yok (ölçüm kapalı)"
    stats = gate_stats(NOTIFY_BOT_TOKEN)
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
    last = STATS["last_match"]
    last_line = "henüz eşleşme yok"
    if last:
        last_line = f"{humanize(time.time() - last)} önce ({STATS['last_match_source']})"
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
    elif not SOURCE_IDS:
        listening = "• Dinleme: KAYNAK YOK — kaynak listesini kontrol et."
    else:
        listening = "• Dinleme: AKTİF — kaynak mesajları bekleniyor."
    return (
        "✅ Takipçi aktif\n"
        f"{listening}\n"
        f"• Çalışma süresi: {humanize(time.time() - STARTED_AT)}"
        f" · başlangıç {timefmt.stamp(STARTED_AT)} TSİ (şimdi {timefmt.clock()} TSİ)\n"
        f"• Dinlenen kaynak: {len(SOURCE_IDS)}/{len(config.get('source_chats') or [])}"
        + (f" (çözülemeyen {len(SOURCE_FAILURES)})" if SOURCE_FAILURES else "")
        + "\n"
        f"• Görülen: {STATS['seen']} | Eşleşen: {STATS['matched']} | İletilen: {STATS['forwarded']}"
        + (f" | Hedefe iletilemeyen: {STATS['failed']}" if STATS["failed"] else "")
        + (f" | Telefon bildirimi başarısız: {STATS['notification_failed']}"
           if STATS.get("notification_failed") else "")
        + "\n"
        f"• Son eşleşme: {last_line}\n"
        f"• Hedef: {DESTINATION_LABEL}\n"
        f"• Kontrol sohbeti: {('Bot özel sohbeti + Kayıtlı Mesajlar' if config_flag(config.get('private_control'), False) else ', '.join(CONTROL_NAMES) or 'me')}\n"
        f"{filter_line}\n"
        f"• İletim sırası: {' → '.join(DELIVERY_CHAIN) or 'yok'}"
        + (f" | kullanılan: {', '.join(f'{k}×{v}' for k, v in STATS['modes'].items())}"
           if STATS["modes"] else "")
        + "\n"
        f"• Tek mesaj modu: {'açık' if SINGLE_MESSAGE else 'kapalı'}"
        + (f" · bildirim sonrası silinen hesap kopyası: {STATS.get('cleaned', 0)}"
           if SINGLE_MESSAGE and STATS.get("cleaned") else "")
        + "\n"
        f"• Komut temizliği: {'açık' if CLEAN_COMMANDS else 'kapalı'}"
        + (f" · silinen eski komut mesajı: {STATS.get('cleaned_commands', 0)}"
           if CLEAN_COMMANDS and STATS.get("cleaned_commands") else "")
        + "\n"
        + (
            f"• Tekrar birleştirme: açık · birleştirilen {STATS.get('deduped', 0)}"
            f" · rozet {STATS.get('dedup_edits', 0)} · izlenen {len(DEDUP_CACHE)} başlık"
            if DEDUP_ENABLED else "• Tekrar birleştirme: kapalı"
        )
        + "\n"
        + (
            f"• Hızlı tarama: açık · aralık {POLL_TELEMETRY['interval']:.0f} sn"
            f" · son yoklama {POLL_TELEMETRY['last_elapsed']:.1f} sn"
            f" · FloodWait {int(POLL_TELEMETRY['flood_count'])}"
            + (f" (son {POLL_TELEMETRY['last_flood_wait']:.0f} sn)" if POLL_TELEMETRY["flood_count"] else "")
            if FAST_POLL else "• Hızlı tarama: kapalı"
        )
        + "\n"
        + _bot_api_stats_line()
    )


def build_source_text() -> str:
    lines = [f"İzlenen {len(SOURCES)} kaynak:"]
    lines += [f"• {item['name']}  [{item['id']}]" + ("" if item["joined"] else "  ⚠️ ÜYE DEĞİLSİN")
              for item in SOURCES]
    for value, exc in SOURCE_FAILURES:
        lines.append(f"✗ {value} çözülemedi: {exc}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Telegram liste düzenleme akışı
# ---------------------------------------------------------------------------
#
# Telegram'da beş liste taslak akışıyla düzenlenir; iki filtre ise
# /tgfiltreac ve /tgfiltrekapat ile bağımsız açılıp kapatılır. Diğer ayarlar config/kod üzerinden.

def build_main_menu_text() -> str:
    """/ayar çıktısı: botun ana yönetim merkezi."""
    return "\n".join([
        "⚙️  BOT YÖNETİM MERKEZİ",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "📝  LİSTE YÖNETİMİ",
        "➕  /ekle   · Listelere kayıt ekleme taslağı başlatır",
        "➖  /cikar  · Listelerden kayıt çıkarma taslağı başlatır",
        "   (/kaydet ile taslağı kaydet, /iptal ile vazgeç)",
        "",
        "🔎  TELEGRAM İNDİRİM FİLTRELERİ",
        "🟢  /tgfiltreac   · Dahili, harici veya iki filtreyi açar",
        "🔴  /tgfiltrekapat · Dahili, harici veya iki filtreyi kapatır",
        "   İkisi bağımsızdır: kelime listeleri silinmez, sadece etkinlik",
        "   durumu değişir; /durum'dan durumları görebilirsin.",
        "",
        "📩  KİŞİSEL BİLDİRİM FİLTRELERİ (DM)",
        "ℹ️  /dmfiltre    · DM filtrelerinin durumunu ve listelerini gösterir",
        "🟢  /dmfiltreac  · Kişisel bildirim filtresini açar",
        "🔴  /dmfiltrekapat · Kişisel bildirim filtresini kapatır",
        "   DM dahil/hariç kelimeler ve fiyat sınırı korunur;",
        "   sadece hesap sahibinin özel sohbetinden düzenlenir.",
        "",
        "🌐  WEB ÜRÜN TAKİBİ (Akakçe)",
        "🛒  /webtakip    · Ürün takip menüsünü açar",
        "▶️  /webtakipac  · Ürün fiyat takibini etkinleştirir",
        "⏸️  /webtakipkapat · Ürün fiyat takibini durdurur",
        "📋  /webtakipler · Takip edilen ürünleri listeler",
        "🗑️  /webtakipsil · Takip edilen ürünü siler",
        "↩️  /webtakipiptal · Bekleyen ürün takip işlemini iptal eder",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "Detaylı komut rehberi: /komutlar",
        "Gelişmiş araçlar: /analiz, /kaynaklar, /kaynaktest, /test, /id",
        "Yeniden başlatma: /restart",
    ])


# Telegram'dan değiştirilebilen tek ayarlar; her yeni alan buraya bilinçli eklenmeli.
DM_LIST_FIELDS = ("dm_keywords", "dm_exclude_keywords")
DM_PRICE_FIELD = "dm_price_max"   # tek değerli; kelime listelerinden ayrı işlenir
TELEGRAM_LIST_FIELDS = ("include_keywords", "exclude_keywords", "source_chats", *DM_LIST_FIELDS, DM_PRICE_FIELD)
# /ekle ve /cikar menüsünün 7. seçeneği: Akakçe ürün fiyat takibi.
# Bu config listesi değildir; seçilince product_tracker (link → hedef fiyat →
# product_watches.json) akışına yönlendirilir.
WEBTAKIP_FIELD = "__webtakip__"
WEBTAKIP_CHOICES = {"7", "webdetakip", "web_takip", "akakcetakip", "akakce_takip"}
TELEGRAM_LIST_META: dict[str, dict[str, str]] = {
    "dm_keywords": {
        "title": "dmdahil — DM dahil kelimeler",
        "icon": "🎯",
        "description": "Blok halinde geçen ifadeler özel kopya gönderir; eklemek DM'i açar",
    },
    "dm_exclude_keywords": {
        "title": "dmharic — DM hariç kelimeler",
        "icon": "🔕",
        "description": "Blok halinde geçen ifadeler yalnızca özel kopyayı engeller",
    },
    "dm_price_max": {
        "title": "dmfiyat — DM maksimum fiyat",
        "icon": "💰",
        "description": "Bu tutar ve altındaki fiyatlar özel kopya gönderir (kelimelerden bağımsız)",
    },
    "include_keywords": {
        "title": "Dahili kelimeler",
        "icon": "🔎",
        "description": "Mesajlarda eşleşmesi aranan kelimeler",
    },
    "exclude_keywords": {
        "title": "Harici kelimeler",
        "icon": "🚫",
        "description": "Mesajda geçerse iletim yapılmaz",
    },
    "source_chats": {
        "title": "Grup isimleri",
        "icon": "📣",
        "description": "İzlenecek kanal ve gruplar",
    },
}
TELEGRAM_LIST_ALIASES = {
    "dm_keywords": {"dmdahil", "dmdahili", "dm_dahil", "dm_dahili"},
    "dm_exclude_keywords": {"dmharic", "dmhariç", "dm_haric", "dm_hariç"},
    "dm_price_max": {"dmfiyat", "dm_fiyat", "dmmaxfiyat", "dm_max_fiyat", "dm_price_max", "dmucret", "fiyatfiltresi"},
    "include_keywords": {"dahil", "dahili", "dahili_kelimeler", "kelime", "kelimeler", "include_keywords"},
    "exclude_keywords": {"harici", "harici_kelimeler", "hariç", "haric", "exclude_keywords"},
    "source_chats": {"grup", "gruplar", "grup_isimleri", "kanal", "kanallar", "kaynak", "kaynaklar", "source_chats"},
}
LIST_ITEM_MAX_LENGTH = 100
LIST_BATCH_MAX = 50       # tek mesajda işlenebilecek en fazla kayıt
LIST_PREVIEW_MAX = 12     # onay/taslak mesajında listelenen en fazla kayıt


def telegram_list_items(config: dict, field: str) -> list[Any]:
    """İzinli Telegram listesini güvenli biçimde liste olarak döndür."""
    value = config.get(field)
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def dm_price_threshold(config: dict) -> float | None:
    """DM maksimum fiyat eşiği (yoksa/okunamazsa None → kural kapalı)."""
    items = telegram_list_items(config, "dm_price_max")
    if not items:
        return None
    return price_filter.parse_threshold(items[0])


def dm_should_send(config: dict, text: str, strike: Sequence[tuple[int, int]] = ()) -> bool:
    """Özel kopya kararı: kelime kuralı VEYA fiyat kuralı. İkisi birbirinden bağımsızdır."""
    if config_flag(config.get("dm_enabled"), False) and dm_matches(
        text, config.get("dm_keywords", []), config.get("dm_exclude_keywords", []),
    ):
        return True
    threshold = dm_price_threshold(config)
    if threshold is None:
        return False
    hit, info = price_filter.price_matches(text, threshold, strike)
    if hit:
        log.info("DM fiyat kuralı eşleşti: %s ≤ %s (%s).", info.text, price_filter.format_price(threshold), info.reason)
    return hit


def resolve_telegram_list(text: Any) -> str | None:
    """Menüdeki 1–6 seçimini, Türkçe liste adını veya webdetakip'i kurallı alana çevir."""
    raw = normalize(str(text or "")).strip().lstrip("/")
    if raw.isdigit():
        index = int(raw)
        if 1 <= index <= len(TELEGRAM_LIST_FIELDS):
            return TELEGRAM_LIST_FIELDS[index - 1]
        if index == 7:
            return WEBTAKIP_FIELD
    key = re.sub(r"[\s-]+", "_", raw)
    if key in WEBTAKIP_CHOICES:
        return WEBTAKIP_FIELD
    for field, aliases in TELEGRAM_LIST_ALIASES.items():
        if key == field or key in aliases:
            return field
    return None


def _webtakip_count(store: Any) -> int | None:
    """product_watches.json'daki takip edilen ürün sayısı (okunamazsa None)."""
    try:
        from product_tracker import load_state

        return len(load_state(store.path.with_name("product_watches.json")).get("items", []))
    except Exception as exc:  # noqa: BLE001 - menü yine de gösterilir; neden log'a düşer
        log.warning("Web takip kayıt sayısı okunamadı: %s: %s", type(exc).__name__, exc)
        return None


def build_list_category_prompt(
    action: str, config: dict, webtakip_count: int | None = None,
) -> str:
    """/ekle veya /cikar sonrası gösterilen sade, numaralı liste seçimi."""
    is_add = action == "add"
    title = "➕  LİSTEYE EKLE" if is_add else "➖  LİSTEDEN ÇIKAR"
    instruction = "Hangi listeye ekleyelim?" if is_add else "Hangi listeden kayıt çıkaralım?"
    lines = [title, "━━━━━━━━━━━━━━━━━━━━", instruction, ""]
    for index, field in enumerate(TELEGRAM_LIST_FIELDS, start=1):
        meta = TELEGRAM_LIST_META[field]
        count = len(telegram_list_items(config, field))
        lines.append(f"{index}. {meta['icon']}  {meta['title']}  ·  {count} kayıt")
        lines.append(f"   {meta['description']}")
    count_text = "–" if webtakip_count is None else str(webtakip_count)
    lines += [
        f"7. 🌐  webdetakip  ·  Akakçe ürün fiyat takibi  ·  {count_text} kayıt",
        "   Akakçe ürün linkiyle fiyat/hedef takibi: link → hedef fiyat → kalıcı kayıt",
        "",
        "Seçmek için 1, 2, 3, 4, 5, 6 ya da 7 (webdetakip) yazabilir; listedeki adı da geçer.",
        "Vazgeçmek için /iptal.",
    ]
    return "\n".join(lines)


def telegram_list_item_key(field: str, value: Any) -> str:
    """Kayıt karşılaştırması: Türkçe kelimeler ve ASCII Telegram kullanıcı adları."""
    text = str(value)
    if field in DM_LIST_FIELDS:
        return normalize_keyword(text)
    # Telegram kullanıcı adları ASCII ve büyük/küçük harfe duyarsızdır; burada
    # Türkçe I/İ eşlemesi uygulanmamalı (I ile i farklı dönüşür).
    return text.casefold() if field == "source_chats" else normalize(text)


def build_list_value_prompt(action: str, field: str, config: dict) -> str:
    """Liste seçildikten sonra mevcut kayıtları ve tek adımlık yönergeyi göster."""
    meta = TELEGRAM_LIST_META[field]
    items = telegram_list_items(config, field)
    lines = [f"{meta['icon']}  {meta['title']}", meta["description"], "━━━━━━━━━━━━━━━━━━━━"]
    if items:
        lines.append(f"Mevcut liste · {len(items)} kayıt")
        lines.append("")
        lines.extend(
            f"{index:02}. {str(item).replace(chr(10), ' ').replace(chr(13), ' ')}"
            for index, item in enumerate(items, start=1)
        )
    else:
        lines.append("Mevcut liste boş.")
    lines.append("")
    if field == "dm_price_max":
        if action == "add":
            lines += ["Maksimum fiyatı gönder (TL). Bu tutar ve altındaki fiyatlar DM'e düşer.",
                      "Örnek: 10000 · 50 · 0   (yeni değer eskisinin yerine geçer)"]
        else:
            lines += ["Fiyat eşiğini kaldırmak için 1 yaz."]
        lines += ["", "Bu adımda ayar henüz kaydedilmez.", "İptal etmek için /iptal."]
        return "\n".join(lines)
    if action == "add":
        if field == "source_chats":
            lines += ["Eklenecek kanal/grup kullanıcı adını, ID'sini veya davet linkini gönder.",
                      "Davet linki için önce takip hesabıyla katıl (gerekirse onay/abonelik tamamlanmalı).",
                      "Örnek: @kanaladi, -1001234567890, https://t.me/+DAVET"]
        else:
            lines.append("Eklenecek kelimeleri gönder.")
        lines += ["Birden çok kaydı virgül, noktalı virgül veya satır ile ayırabilirsin.",
                  "Örnek: kahve, şeker, süt"]
    else:
        lines += ["Çıkarmak istediğin kaydın numarasını veya listedeki tam değerini gönder;",
                  "birden çok kayıt için virgülle ayır. Örnek: 1, 3, çekiliş"]
    lines += ["", "Bu adımda ayar henüz kaydedilmez.", "İptal etmek için /iptal."]
    return "\n".join(lines)


def format_value_preview(values: Sequence[Any], limit: int = LIST_PREVIEW_MAX) -> str:
    """Kayıtları tek satırda özetle; uzunsa '… ve N kayıt daha' ekle."""
    texts = [str(value) for value in values]
    if len(texts) > limit:
        return ", ".join(texts[:limit]) + f" … ve {len(texts) - limit} kayıt daha"
    return ", ".join(texts)


def stage_price_threshold_change(
    config: dict, action: str, raw_value: Any,
) -> tuple[bool, dict, list[Any], str]:
    """DM maksimum fiyat eşiği: tek değer; ekleme eskisinin yerine geçer, çıkarma kapatır."""
    title = TELEGRAM_LIST_META["dm_price_max"]["title"]
    current = telegram_list_items(config, "dm_price_max")
    candidate = copy.deepcopy(config)
    if action == "remove":
        if not current:
            return False, copy.deepcopy(config), [], "ℹ️ DM fiyat eşiği zaten boş; çıkarılacak kayıt yok."
        candidate["dm_price_max"] = []
        return True, candidate, [current[0]], (
            f"{title} kaldırılacak (şu an: {price_filter.format_price(price_filter.parse_threshold(current[0]) or 0)}). "
            "Fiyat kuralı kapanır."
        )
    values = split_values(raw_value)
    if len(values) != 1:
        return False, copy.deepcopy(config), [], "ℹ️ Tek bir tutar gönder. Örnek: 10000"
    amount = price_filter.parse_threshold(values[0])
    if amount is None or amount < 0 or amount > 100_000_000:
        return False, copy.deepcopy(config), [], "❌ Tutarı anlayamadım. Örnek: 10000 veya 50 veya 0"
    stored = str(int(amount)) if abs(amount - round(amount)) < 0.005 else f"{amount:.2f}"
    candidate["dm_price_max"] = [stored]
    note = f"{title}: {price_filter.format_price(amount)} ve altı DM'e düşecek"
    if current:
        note += f" (eski: {price_filter.format_price(price_filter.parse_threshold(current[0]) or 0)})"
    return True, candidate, [stored], note


def _stage_list_add(
    config: dict, field: str, current: list[Any], values: list[str], title: str,
) -> tuple[bool, dict, list[Any], str]:
    added: list[Any] = []
    duplicates: list[str] = []
    invalid: list[str] = []
    keys = [telegram_list_item_key(field, item) for item in current]
    for text in values:
        if len(text) > LIST_ITEM_MAX_LENGTH:
            invalid.append(f"{text[:24]}… (en fazla {LIST_ITEM_MAX_LENGTH} karakter)")
            continue
        if field == "source_chats":
            try:
                value: Any = parse_chat_value(text)
            except ValueError as exc:
                invalid.append(f"{text} ({exc})")
                continue
        else:
            value = telegram_list_item_key(field, text)
        key = telegram_list_item_key(field, value)
        if field in DM_LIST_FIELDS and len(key) > LIST_ITEM_MAX_LENGTH:
            invalid.append(f"{text[:24]}… (normalizasyon sonrası en fazla {LIST_ITEM_MAX_LENGTH} karakter)")
            continue
        if key in keys:
            duplicates.append(str(value))
            continue
        keys.append(key)
        added.append(value)
    if not added:
        lines = ["ℹ️ Eklenecek yeni kayıt yok."]
        if duplicates:
            lines.append(f"zaten bu listede: {format_value_preview(duplicates)}")
        if invalid:
            lines.append(f"Geçersiz kayıt: {format_value_preview(invalid)}")
        return False, copy.deepcopy(config), [], "\n".join(lines)
    if field in DM_LIST_FIELDS and len(current) + len(added) > 100:
        return False, copy.deepcopy(config), [], "DM listesinde en fazla 100 kayıt olabilir; hiçbir kayıt eklenmedi."
    if len(added) == 1:
        message = f"{title} listesine eklenecek: {added[0]}"
    else:
        message = f"{title} listesine eklenecek {len(added)} kayıt: {format_value_preview(added)}"
    if duplicates:
        message += f"\n⚠️ {len(duplicates)} kayıt zaten listede, atlandı: {format_value_preview(duplicates)}"
    if invalid:
        message += f"\n⚠️ {len(invalid)} kayıt alınamadı, atlandı: {format_value_preview(invalid)}"
    candidate = copy.deepcopy(config)
    candidate[field] = [*current, *added]
    return True, candidate, added, message


def _stage_list_remove(
    config: dict, field: str, current: list[Any], values: list[str], title: str,
) -> tuple[bool, dict, list[Any], str]:
    # Numaralar kullanıcının gördüğü listenin sırasıdır; kayıtlar çıkarıldıkça
    # sıra kaymasın diye numara baştan çözülür, kayıt sonra yerinden bulunur.
    remaining = list(current)
    removed: list[Any] = []
    missing: list[str] = []
    for text in values:
        index: int | None = None
        if text.isdigit():
            selected = int(text)
            if 1 <= selected <= len(current):
                index = selected - 1
        if index is None:
            wanted = telegram_list_item_key(field, text)
            index = next((i for i, item in enumerate(current)
                          if telegram_list_item_key(field, item) == wanted), None)
        if index is None:
            missing.append(text)
            continue
        target_key = telegram_list_item_key(field, current[index])
        position = next((i for i, item in enumerate(remaining)
                         if telegram_list_item_key(field, item) == target_key), None)
        if position is None:
            missing.append(text)
            continue
        removed.append(remaining.pop(position))
    if not removed:
        detail = f"Bu kayıtlar listede bulunamadı: {format_value_preview(missing)}" if missing \
            else "Çıkarılacak kayıt bulunamadı."
        return False, copy.deepcopy(config), [], f"ℹ️ {detail}"
    if field == "source_chats" and not remaining:
        return False, copy.deepcopy(config), [], (
            "En az bir takip edilen grup/kanal kalmalı; son kaydı da çıkaramazsın."
        )
    if len(removed) == 1:
        message = f"{title} listesinden çıkarılacak: {removed[0]}"
    else:
        message = f"{title} listesinden çıkarılacak {len(removed)} kayıt: {format_value_preview(removed)}"
    if missing:
        message += f"\n⚠️ {len(missing)} kayıt listede bulunamadı, atlandı: {format_value_preview(missing)}"
    candidate = copy.deepcopy(config)
    candidate[field] = remaining
    return True, candidate, removed, message


def stage_telegram_list_change(
    config: dict, field: str, action: str, raw_value: Any,
) -> tuple[bool, dict, list[Any], str]:
    """İzinli bir liste değişikliğini config'e dokunmadan taslak olarak hazırla.

    Tek mesajda virgül, noktalı virgül veya satır ile ayrılmış **birden çok**
    kayıt gönderilebilir. Dönen üçüncü değer işlenen kayıtların listesidir
    (eklenen ya da çıkarılan); hiçbiri işlenemezse ``ok=False`` döner.
    """
    if field not in TELEGRAM_LIST_FIELDS:
        return False, copy.deepcopy(config), [], "Bu liste Telegram'dan düzenlenemez."
    if action not in {"add", "remove"}:
        return False, copy.deepcopy(config), [], "Geçersiz liste işlemi."
    if field == "dm_price_max":
        return stage_price_threshold_change(config, action, raw_value)
    current = telegram_list_items(config, field)
    values = split_values(raw_value)
    if not values:
        return False, copy.deepcopy(config), [], "Boş kayıt eklenemez veya çıkarılamaz."
    if len(values) > LIST_BATCH_MAX:
        return False, copy.deepcopy(config), [], (
            f"Tek mesajda en fazla {LIST_BATCH_MAX} kayıt gönderebilirsin."
        )
    title = TELEGRAM_LIST_META[field]["title"]
    if action == "add":
        return _stage_list_add(config, field, current, values, title)
    return _stage_list_remove(config, field, current, values, title)


def resolve_confirmation_choice(text: Any) -> str | None:
    """Onay mesajındaki etiket veya kısa yanıtı eyleme çevir."""
    choice = re.sub(r"[^\w]+", " ", normalize(str(text or ""))).strip()
    choice = " ".join(choice.split())
    if choice in {
        "kaydet", "kaydet ve gonder", "kaydet ve gönder", "kaydet ve githuba gonder",
        "kaydet ve githuba gönder", "kaydet ve github a gönder", "onayla", "evet",
    }:
        return "save"
    if choice in {
        "iptal", "ıptal", "iptal et", "ıptal et", "iptal et ve taslagi sil",
        "iptal et ve taslağı sil", "vazgec", "vazgeç", "vazgec et", "vazgeç et",
        "hayir", "hayır", "cancel",
    }:
        return "cancel"
    return None


# ---------------------------------------------------------------------------
# Filtre aç/kapat (/tgfiltreac, /tgfiltrekapat)
# ---------------------------------------------------------------------------
#
# Dahili (🔎 include_keywords) ve harici (🚫 exclude_keywords) filtreler
# birbirinden BAĞIMSIZ açılıp kapatılır; böylece hangi filtrenin çalıştığı tam
# olarak kontrol edilebilir. Durum config.json'daki ``include_enabled`` ve
# ``exclude_enabled`` alanlarında tutulur (varsayılan: ikisi de açık).

FILTER_TARGET_META: dict[str, dict[str, str]] = {
    "include": {
        "icon": "🔎",
        "title": "Dahili kelimeler",
        "field": "include_enabled",
        "keywords": "include_keywords",
        "description": "Mesajda aranan kelimeler (kapalıysa kelimeler yok sayılır)",
    },
    "exclude": {
        "icon": "🚫",
        "title": "Harici kelimeler",
        "field": "exclude_enabled",
        "keywords": "exclude_keywords",
        "description": "Geçen mesajı engelleyen kelimeler (kapalıysa engelleme yapılmaz)",
    },
}
FILTER_TARGET_ORDER = ("include", "exclude")
FILTER_TARGET_NUMBER: dict[str, str] = {"1": "include", "2": "exclude", "3": "both"}
FILTER_TARGET_ALIASES: dict[str, set[str]] = {
    "include": {
        "dahili", "dahil", "dahili_kelimeler", "dahil_kelimeler", "include",
        "include_keywords", "icerik", "içerik", "kelime", "kelimeler", "aranan",
        "yesil", "yeşil",
    },
    "exclude": {
        "harici", "hariç", "haric", "harici_kelimeler", "hariç_kelimeler", "haric_kelimeler",
        "exclude", "exclude_keywords", "engel", "engeller", "engelleme", "yasak",
        "yasakli", "yasaklı", "kara",
    },
    "both": {
        "ikisi", "ikisi_birlikte", "her_ikisi", "herikisi", "hepsi", "tumu", "tümü",
        "both", "all",
    },
}


def resolve_filter_target(text: Any) -> str | None:
    """``/tgfiltreac`` / ``/tgfiltrekapat`` yanıtını hedefe çevir: include | exclude | both.

    Kabul edilenler: ``1``/``2``/``3``, ``dahili``, ``harici``, ``ikisi``,
    ``hariç kelimeler`` gibi yazımlar (boşluk/tire/büyük-küçük farkı yok sayılır).
    """
    raw = normalize(str(text or "")).strip().lstrip("/").strip(".,;:!?")
    if not raw:
        return None
    if raw in FILTER_TARGET_NUMBER:
        return FILTER_TARGET_NUMBER[raw]
    key = re.sub(r"[\s\-]+", "_", raw)
    flat = key.replace("_", "")
    for target, aliases in FILTER_TARGET_ALIASES.items():
        if key == target or key in aliases:
            return target
        if flat in {alias.replace("_", "") for alias in aliases}:
            return target
    return None


def filter_state_lines(config: dict) -> list[str]:
    """İki filtrenin güncel durumunu okunaklı satırlar hâlinde ver."""
    state = filter_state_of(config)
    lines: list[str] = []
    for target in FILTER_TARGET_ORDER:
        meta = FILTER_TARGET_META[target]
        count = len(telegram_list_items(config, meta["keywords"]))
        label = "AÇIK" if state[target] else "KAPALI"
        lines.append(f"• {meta['icon']} {meta['title']}: {label} · {count} kayıt")
    return lines


def build_filter_toggle_prompt(action: str, config: dict) -> str:
    """`/tgfiltreac` veya `/tgfiltrekapat` argümansız yazıldığında hangi filtreyi sorar."""
    state = filter_state_of(config)
    is_open = action == "open"
    lines = [
        "🔓  FİLTRE AÇ" if is_open else "🔒  FİLTRE KAPAT",
        "━━━━━━━━━━━━━━━━━━━━",
        "Hangi filtreyi açalım?" if is_open else "Hangi filtreyi kapatalım?",
        "",
    ]
    for index, target in enumerate(FILTER_TARGET_ORDER, start=1):
        meta = FILTER_TARGET_META[target]
        count = len(telegram_list_items(config, meta["keywords"]))
        label = "açık" if state[target] else "kapalı"
        lines.append(f"{index}. {meta['icon']}  {meta['title']}  ·  şu an {label}  ·  {count} kayıt")
        lines.append(f"   {meta['description']}")
    lines += [
        f"3. 🔁  İkisi birlikte  ·  şu an "
        f"{'açık' if state['include'] and state['exclude'] else 'kapalı'}",
        "",
        "1, 2, 3 yaz ya da filtre adını yaz (dahili / harici / ikisi).",
        "Doğrudan da yazabilirsin: /tgfiltreac dahili · /tgfiltrekapat harici · /tgfiltreac ikisi",
        "Vazgeçmek için /iptal.",
    ]
    return "\n".join(lines)


def stage_filter_toggle(config: dict, action: str, target: str) -> tuple[bool, dict, str]:
    """Filtre anahtarını açar/kapatır; config'e dokunmadan taslak döndürür."""
    if action not in {"open", "close"}:
        return False, copy.deepcopy(config), "Geçersiz filtre işlemi."
    if target not in {"include", "exclude", "both"}:
        return False, copy.deepcopy(config), (
            "Filtre seçilemedi. 1 (dahili), 2 (harici) veya 3 (ikisi) yaz."
        )
    enabled = action == "open"
    targets = FILTER_TARGET_ORDER if target == "both" else (target,)
    what = "Filtreler" if target == "both" else FILTER_TARGET_META[target]["title"] + " filtresi"
    state = filter_state_of(config)
    if all(state[item] is enabled for item in targets):
        return False, copy.deepcopy(config), (
            f"ℹ️ {what} zaten {'açık' if enabled else 'kapalı'}; değişiklik yok."
        )

    candidate = copy.deepcopy(config)
    for item in targets:
        candidate[FILTER_TARGET_META[item]["field"]] = enabled
    # Dahili filtre açılırken eski "tümünü al" modu bırakılır; yoksa include
    # kelimeleri yine yok sayılırdı.
    if enabled and "include" in targets and match_mode_of(candidate) == "forward_all":
        candidate["match_mode"] = "any"
    return True, candidate, build_filter_result_text(action, target, candidate)


def build_filter_result_text(action: str, target: str, config: dict) -> str:
    """Filtre değişikliğinden sonra gösterilen özet."""
    state = filter_state_of(config)
    if target == "both":
        what = "Dahili ve harici filtreler"
    else:
        what = FILTER_TARGET_META[target]["title"] + " filtresi"
    icon = "🔓" if action == "open" else "🔒"
    verb = "AÇILDI" if action == "open" else "KAPATILDI"
    lines = [f"{icon} {what} {verb}.", "", *filter_state_lines(config)]
    if not state["include"] and not state["exclude"]:
        lines += ["", "⚠️ İki filtre de kapalı: kaynaklardaki HER mesaj iletilir."]
    elif not state["include"]:
        lines += ["", "🔓 Dahili filtre kapalı: anahtar kelime aranmaz; yalnızca harici kelimeler engeller."]
    elif not state["exclude"]:
        lines += ["", "ℹ️ Harici filtre kapalı: engelleme yapılmaz; yalnızca dahili kelimeler aranır."]
    else:
        lines += ["", "🔎 Dahili: en az bir kelime eşleşmeli · 🚫 Harici: eşleşen mesaj engellenir."]
    return "\n".join(lines)


def new_filter_edit(action: str) -> dict[str, Any]:
    """`/tgfiltreac` ve `/tgfiltrekapat` için bekleyen seçim oturumu (config değişmez)."""
    return {"stage": "filter", "action": action}


def build_list_change_confirmation(action: str, field: str, value: Any, config: dict,
                                   message: str = "") -> str:
    """Değişiklik taslağı için Telegram'da okunaklı onay mesajı kur.

    ``message`` içindeki ``⚠️`` ile başlayan satırlar (atlanan kayıtlar) onaya
    aynen taşınır; böylece kullanıcı neyin işlenmediğini kaydetmeden önce görür.
    """
    meta = TELEGRAM_LIST_META[field]
    verb = "Eklenecek" if action == "add" else "Çıkarılacak"
    icon = "➕" if action == "add" else "➖"
    values = list(value) if isinstance(value, (list, tuple, set)) else [value]
    count = len(telegram_list_items(config, field))
    if len(values) == 1:
        heading = f"{icon} {verb}: {values[0]}"
    else:
        lines = [f"{icon} {verb} {len(values)} kayıt:"]
        lines += [f"   {index:02d}. {item}"
                  for index, item in enumerate(values[:LIST_PREVIEW_MAX], start=1)]
        if len(values) > LIST_PREVIEW_MAX:
            lines.append(f"   … ve {len(values) - LIST_PREVIEW_MAX} kayıt daha")
        heading = "\n".join(lines)
    warnings = [line for line in str(message or "").splitlines() if line.startswith("⚠️")]
    return "\n".join([
        "📝  DEĞİŞİKLİK TASLAĞI",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📂 {meta['title']}",
        heading,
        *warnings,
        f"📊 Kaydedilince listedeki kayıt sayısı: {count}",
        "",
        "Bu değişiklik henüz aktif değil ve config.json'a yazılmadı.",
        "",
        "✅ Kaydet ve GitHub'a gönder  →  /kaydet",
        "↩️ İptal et ve taslağı sil     →  /iptal",
        "",
        "Komut gönderebilir veya sadece “kaydet” / “iptal” yazabilirsin.",
    ])


# Komut yeniden adlandırma tamamlandı: /open→/tgfiltreac, /close→/tgfiltrekapat,
# /dmac→/dmfiltreac, /dmkapat→/dmfiltrekapat, /takip*→/webtakip*,
# /dmfiltreekle→/ekle+/cikar, /dmfiltrecikar→/ekle+/cikar; eski isimler alias
# kalmadan tamamen kaldırıldı, yeni isimler tek ve kanonik kaynaktır.
# Turkish uppercase I/İ is normalized at parse time; it is not a second command.
CMD_DM_COMMANDS = frozenset({
    COMMAND_DM_STATUS, COMMAND_DM_FILTER_OPEN, COMMAND_DM_FILTER_CLOSE,
})
CMD_SETTINGS_MENU = frozenset({COMMAND_SETTINGS_MENU})
CMD_SETTINGS_ADD = frozenset({COMMAND_SETTINGS_ADD})
CMD_SETTINGS_REMOVE = frozenset({COMMAND_SETTINGS_REMOVE})
CMD_SETTINGS_SAVE = frozenset({COMMAND_SETTINGS_SAVE})
CMD_SETTINGS_REVERT = frozenset({COMMAND_SETTINGS_REVERT})
CMD_TG_FILTER_OPEN = frozenset({COMMAND_TG_FILTER_OPEN})
CMD_TG_FILTER_CLOSE = frozenset({COMMAND_TG_FILTER_CLOSE})
CMD_ANALYZE = frozenset({COMMAND_ANALYZE})
CMD_SOURCE_TEST = frozenset({COMMAND_SOURCE_TEST})
# /webtakip menüsünün içindeki Akakçe takip alt komutları product_tracker.handle
# üzerinden yönlendirilir (bildirim botu özel sohbeti); /komutlar'da görünür tutulur.
CMD_WEB_TRACK_MENU = frozenset({"/webtakipac", "/webtakipkapat", "/webtakipler",
                                "/webtakipsil", "/webtakipiptal", "/webtakipyardim"})
SETTINGS_COMMANDS = frozenset().union(
    CMD_SETTINGS_MENU, CMD_SETTINGS_ADD, CMD_SETTINGS_REMOVE,
    CMD_SETTINGS_SAVE, CMD_SETTINGS_REVERT,
)

PendingKey = tuple[int, int] | tuple[str, int, int]
PENDING: dict[PendingKey, dict[str, Any]] = {}
PENDING_TTL_SECONDS = 600  # 10 dakika sonra bekleyen işlem düşer

SAVE_STATUS_TEXT = {
    "pushed": "✅ config.json yazıldı ve repo'ya işlendi ({detail}) — yeniden başlasa da kalıcı.",
    "clean": "💾 config.json yazıldı; depoda ayrıca işlenecek değişiklik yoktu ({detail}).",
    "local": ("⚠️ config.json yazıldı ve commit edildi ama gönderilemedi ({detail}). "
              "Bot yeniden başlarsa bu değişiklik kaybolur; GitHub erişimini kontrol et."),
    "no-repo": ("⚠️ config.json yazıldı ama depoya işlenemedi ({detail}). "
                "Değişiklik yalnızca bu oturumda geçerli."),
    "error": ("⚠️ config.json yazıldı ama repo'ya işlenemedi ({detail}). "
              "GitHub erişim/izinlerini kontrol et; değişiklik bu oturumda aktif."),
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


def pending_key(event: Any) -> PendingKey:
    # Bot DM and Saved Messages can have the same numeric chat/sender IDs.
    chat_id = int(getattr(event, "chat_id", 0) or 0)
    sender_id = int(getattr(event, "sender_id", 0) or 0)
    if isinstance(event, PrivateControlEvent):
        return ("bot", chat_id, sender_id)
    return (chat_id, sender_id)


def set_pending(key: PendingKey, **data: Any) -> None:
    data["at"] = time.time()
    PENDING[key] = data


def peek_pending(key: PendingKey) -> dict[str, Any] | None:
    """Süresi dolmamış işlemi al; süresi dolduysa bellekten temizle."""
    item = PENDING.get(key)
    if item is None:
        return None
    if time.time() - float(item.get("at", 0)) > PENDING_TTL_SECONDS:
        PENDING.pop(key, None)
        return None
    return item


def drop_pending(key: PendingKey) -> None:
    PENDING.pop(key, None)


def new_telegram_edit(action: str, config: dict) -> dict[str, Any]:
    """Bir kişi için geçici düzenleme oturumu oluştur; asıl config değişmez."""
    snapshot = copy.deepcopy(config)
    return {
        "stage": "category",
        "action": action,
        "base_config": snapshot,
        "draft_config": copy.deepcopy(snapshot),
    }


class ConfigStore:
    """Config dosyasını ve liste taslağı sırasında gereken anlık görüntüyü tut."""

    def __init__(self, path: str | os.PathLike[str], config: dict) -> None:
        self.path = Path(path)
        self.config: dict = config

    def snapshot(self) -> dict:
        return copy.deepcopy(self.config)

    def restore(self, snapshot: dict) -> None:
        self.config = copy.deepcopy(snapshot)


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
    global SMART_HEADER, FAST_NOTIFY, SLOW_REPORT_SECONDS, FAST_POLL, FAST_POLL_SECONDS
    global FILTER_INCLUDE, FILTER_EXCLUDE, FILTER_MODE, ADMIN_IDS
    global FILTER_INCLUDE_ENABLED, FILTER_EXCLUDE_ENABLED
    global DELIVERY_CHAIN, MAX_MEDIA_MB
    global LINK_APPENDIX_MODE, LINK_KINDS, BOT_LINK_KINDS
    global MESSAGE_LINK_LINE, SOURCE_FOOTER, NOTIFY_MEDIA, NOTIFY_BOT_TOKEN
    global SINGLE_MESSAGE, CLEAN_COMMANDS
    global DEDUP_ENABLED, DEDUP_WINDOW_SECONDS, DEDUP_SCAN_LIMIT

    notes: list[str] = []
    FILTER_INCLUDE = [normalize(x) for x in config.get("include_keywords") or []]
    FILTER_EXCLUDE = [normalize(x) for x in config.get("exclude_keywords") or []]
    FILTER_MODE = match_mode_of(config)
    filter_state = filter_state_of(config)
    FILTER_INCLUDE_ENABLED = filter_state["include"]
    FILTER_EXCLUDE_ENABLED = filter_state["exclude"]
    ADMIN_IDS = parse_admin_ids(config.get("admin_user_id"))
    DELIVERY_CHAIN = build_delivery_chain(config)
    try:
        MAX_MEDIA_MB = int(config.get("max_media_mb", 25))
    except (TypeError, ValueError):
        log.warning("max_media_mb sayı değil, 25 kabul edildi.")
        MAX_MEDIA_MB = 25
    LINK_APPENDIX_MODE = link_appendix_mode(config)
    LINK_KINDS = link_kinds_for(LINK_APPENDIX_MODE, bot=False)
    BOT_LINK_KINDS = link_kinds_for(LINK_APPENDIX_MODE, bot=True)
    MESSAGE_LINK_LINE = config_flag(config.get("message_link"), True)
    SOURCE_FOOTER = config_flag(config.get("source_footer"), True)
    NOTIFY_MEDIA = config_flag(config.get("notify_media"), True)
    SINGLE_MESSAGE = config_flag(config.get("single_message"), True)
    CLEAN_COMMANDS = config_flag(config.get("clean_commands"), True)
    SMART_HEADER = config_flag(config.get("smart_header"), False)
    FAST_NOTIFY = config_flag(config.get("fast_notify"), False)
    FAST_POLL = config_flag(config.get("fast_poll"), True)
    try:
        FAST_POLL_SECONDS = min(30.0, max(1.0, float(config.get("fast_poll_seconds", 3))))
    except (TypeError, ValueError):
        FAST_POLL_SECONDS = 3.0
    try:
        SLOW_REPORT_SECONDS = max(0.0, float(config.get("slow_report_seconds", 8)))
    except (TypeError, ValueError):
        SLOW_REPORT_SECONDS = 8.0
    DEDUP_ENABLED = config_flag(config.get("dedup_enabled"), True)
    try:
        DEDUP_WINDOW_SECONDS = max(1, min(72, int(config.get("dedup_window_hours", 12)))) * 3600
    except (TypeError, ValueError):
        log.warning("dedup_window_hours sayı değil, 12 saat kabul edildi.")
        DEDUP_WINDOW_SECONDS = 12 * 3600
    try:
        DEDUP_SCAN_LIMIT = max(0, min(100, int(config.get("dedup_scan_limit", 100))))
    except (TypeError, ValueError):
        log.warning("dedup_scan_limit sayı değil, 100 kabul edildi.")
        DEDUP_SCAN_LIMIT = 100
    # Bildirim token'ının tek kaynağı environment/Actions secret'ı olsun;
    # config.json içindeki eski notify_bot_token alanı artık kullanılmaz.
    token = str(os.getenv("NOTIFY_BOT_TOKEN", "") or "").strip()
    NOTIFY_BOT_TOKEN = "" if token.lower() in {"null", "none", "yok"} else token

    if not FILTER_INCLUDE_ENABLED:
        notes.append("🔓 Dahili kelime filtresi kapalı; anahtar kelimeler yok sayılıyor"
                     + (" (harici kelimeler yine engeller)." if FILTER_EXCLUDE_ENABLED
                        else " ve harici engelleme de kapalı: her mesaj iletilir."))
    if not FILTER_EXCLUDE_ENABLED:
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
    global SOURCES, SOURCE_IDS, SOURCE_FAILURES
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


async def resolve_destination(client: TelegramClient, config: dict, quiet: bool = False) -> list[str]:  # noqa: ARG001 - dış API uyumu
    """Hedef sohbeti çöz; olmazsa Kayıtlı Mesajlar'a düş.

    `quiet` ileriye dönük sessiz mod için ayrılmıştır.
    """
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
    previous = COMMAND_MESSAGES.get(chat, [])
    COMMAND_MESSAGES[chat] = current
    return [item for item in previous if item not in current]


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
# Mesaj geçmişi kelime analizi (/analiz)
# ---------------------------------------------------------------------------
#
# Amaç: kaynaklarda geçmişe dönük mesajları okuyup **başlıkta** (mesajın ilk
# anlamlı satırı; marka/ürün adının yazdığı yer) en çok geçen kelimeleri
# göstermek. Böylece hiç almak istemediğin ürün türlerini bulup harici
# kelimeler listesine ekleyebilirsin. Yalnızca başlık taranır; açıklama, fiyat
# ve link satırları istatistiğe girmez.

ANALYSIS_DEFAULT_LIMIT = 300     # kaynak başına taranan mesaj sayısı
ANALYSIS_MIN_LIMIT = 20
ANALYSIS_MAX_LIMIT = 2000
ANALYSIS_TOP = 25                # her listede gösterilen kelime sayısı
ANALYSIS_QUERY_MAX_LINES = 200   # sorgu listesinde en fazla satır (spam'i önler)
# `/analiz tümü` yazıldığında kabul edilen kelimeler.
ANALYSIS_ALL_TOKENS = frozenset({
    "tumu", "tümü", "hepsi", "hepsini", "all", "hersey", "herşey", "filtresiz",
    "sinirsiz", "sınırsız",
})
# Harf dizileri ve saf sayı dizileri (fiyat/adet sayıları ayrı yakalanır).
ANALYSIS_TOKEN_RE = re.compile(r"[0-9]+|[^\W\d_]+", re.UNICODE)

# İçerik taşımayan bağlaç/edat/zamirler: istatistikte anlamlı kelimeler kalsın.
STOP_WORDS = frozenset("""
acaba ama ancak artık asla ayrıca bana bazı belki ben beni benim beri bile bir
biraz birçok biri birkaç biz bize bizim bu buna bunda bundan bunu bunun burada
çok çünkü da daha değil diğer diye eğer en fakat falan filan gibi göre hangi
hatta hem hep hepsi her herhangi hiç için ile ise işte kadar kez ki kim kimi mı
mi mu mü nasıl ne neden nerede nereye niçin niye o olan olarak oldu olduğu olup
olsun öyle sadece sanki sen siz sonra şey şeyi şimdi şu şuna şunu tüm tümü üzere
var ve veya ya yani yine zaten
""".split())
# İlan/indirim kalıpları: neredeyse her mesajda geçtiği için varsayılan
# istatistikte gizlenir. Hepsini görmek için `/analiz tümü`.
DEAL_FILLER_WORDS = frozenset("""
adet adetli bedava bugün dahil fırsat fırsatı fırsatlar firsat güncel günün
hemen indirim indirimli kampanya kampanyası kargo kaçırma kaçmaz kupon link
links mevcut saat sepet sepette sipariş sınırlı stok stoğu stoklar tıkla tl try
ücretsiz
""".split())
ANALYSIS_STOP_WORDS = STOP_WORDS | DEAL_FILLER_WORDS

ANALYSIS_USAGE = "\n".join([
    "🔎  BAŞLIK KELİME ANALİZİ",
    "━━━━━━━━━━━━━━━━━━━━",
    "Kaynaklardaki geçmiş mesajların yalnızca başlığını (ilk anlamlı satırını)",
    "tarar; en çok geçen 25 kelimeyi ve en çok geçen 25 ilk kelimeyi verir.",
    "",
    "Kullanım:",
    f"  /analiz            → kaynak başına son {ANALYSIS_DEFAULT_LIMIT} mesaj",
    f"  /analiz 1000       → kaynak başına son 1000 mesaj ({ANALYSIS_MIN_LIMIT}–{ANALYSIS_MAX_LIMIT})",
    "  /analiz350         → aynısı, bitişik yazılabilir",
    "  /analiz 1000 tcl 65 → tüm kaynakların son 1000 mesajında 'tcl 65' geçenleri",
    "                        listeler: ürün adı (tam) - fiyat",
    "  /analiz tümü       → işlevsiz kelimeler ve saf sayılar da sayılsın",
    "  /analiz 500 tümü   → ikisi birlikte",
    "",
    "Sonuçlara bakıp istemediğin kelimeleri harici listeye ekle:",
    "/cikar → 2 (Harici kelimeler) → örnek: bebek, oyuncak, kitap",
])


def message_title(text: str | None) -> str:
    """Mesajın başlığı: ilk boş olmayan satır (marka/ürün adının yazdığı yer)."""
    for line in (text or "").splitlines():
        title = line.strip()
        if title:
            return title
    return ""


def title_tokens(title: str, *, keep_everything: bool = False) -> list[str]:
    """Başlığı Türkçe duyarlı küçük harfe indirip kelimelere ayır.

    ``keep_everything=False`` iken saf sayılar (fiyat, adet, model kodu) atılır.
    """
    tokens: list[str] = []
    for raw in ANALYSIS_TOKEN_RE.findall(normalize(title)):
        if not keep_everything and raw.isdigit():
            continue
        tokens.append(raw)
    return tokens


def content_tokens(tokens: Sequence[str], *, keep_everything: bool = False) -> list[str]:
    """İşlevsiz kelimeleri, saf sayıları ve tek harfli gürültüyü ele.

    ``keep_everything=True`` iken hiçbiri elenmez (``/analiz tümü``).
    """
    if keep_everything:
        return list(tokens)
    return [token for token in tokens
            if len(token) > 1 and not token.isdigit() and token not in ANALYSIS_STOP_WORDS]


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


def list_contains_token(items: Iterable[Any], token: str) -> bool:
    """Kelime, verilen listedeki bir kaydın (ya da kaydın bir parçasının) içinde mi?"""
    for item in items:
        if token in normalize(str(item)).split():
            return True
    return False


def format_ranking(counter: Any, config: dict, top: int = ANALYSIS_TOP) -> list[str]:
    """Frekans tablosunu numaralı satırlara çevir; listede olanları işaretle."""
    exclude = telegram_list_items(config, "exclude_keywords")
    include = telegram_list_items(config, "include_keywords")
    ranked = sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:top]
    lines: list[str] = []
    for index, (word, count) in enumerate(ranked, start=1):
        mark = ""
        if list_contains_token(exclude, word):
            mark = " 🚫"
        elif list_contains_token(include, word):
            mark = " 🔎"
        lines.append(f"{index:02d}. {count:>4}×  {word}{mark}")
    return lines or ["(kelime bulunamadı)"]


def build_analysis_report(
    analysis: dict[str, Any],
    *,
    config: dict,
    sources: int,
    per_source: int,
    keep_everything: bool = False,
    elapsed: float = 0.0,
    failures: Sequence[str] = (),
) -> str:
    """Analiz sonucunu Telegram'da okunaklı iki istatistiğe çevir."""
    counts = analysis["counts"]
    lines = [
        "📊  BAŞLIK KELİME ANALİZİ",
        "━━━━━━━━━━━━━━━━━━━━",
        f"• Kaynak: {sources} grup · kaynak başına son {per_source} mesaj",
        f"• Taranan başlık: {counts['titles']} · tekil: {counts['unique']}"
        + (f" (tekrar birleştirildi: {counts['duplicates']})" if counts["duplicates"] else ""),
        f"• Süre: {humanize(elapsed)}",
    ]
    if keep_everything:
        lines.append("• Kapsam: TÜM kelimeler ve sayılar (filtre kapalı)")
    else:
        filtered = f" ({counts['filtered']} kelime)" if counts["filtered"] else ""
        lines.append(f"• Kapsam: işlevsiz kelimeler ve saf sayılar elendi{filtered}"
                     " · hepsi için: /analiz tümü")
    if failures:
        lines.append(f"• ⚠️ Okunamayan kaynak: {', '.join(failures)}")

    lines += ["", f"1️⃣ EN ÇOK GEÇEN {ANALYSIS_TOP} KELİME (başlıktaki tüm kelimeler)"]
    lines += format_ranking(analysis["words"], config)
    lines += ["", f"2️⃣ EN ÇOK GEÇEN {ANALYSIS_TOP} İLK KELİME (başlığın ilk kelimesi)"]
    lines += format_ranking(analysis["first_words"], config)
    lines += [
        "",
        "🚫 = harici listende · 🔎 = dahili listende",
        "İstemediğin kelimeleri harici listeye ekle: /cikar → 2 (Harici kelimeler)",
        "Örnek: bebek, oyuncak, kitap — birden çok kelimeyi virgülle ayırabilirsin.",
    ]
    return "\n".join(lines)


def parse_analysis_request(raw: str) -> tuple[int, bool, str]:
    """``/analiz [adet] [tümü] [sorgu]`` argümanlarını çöz.

    Döner: (mesaj sayısı, filtre kapalı mı, sorgu metni; yoksa "").
    Mesaj sayısı yalnızca argümanların BAŞINDA olabilir; ilk kelimeden sonra
    gelen sayılar sorgunun parçasıdır (``/analiz 1000 tcl 65`` → sorgu
    ``tcl 65``). ``tümü`` nerede geçerse geçsin filtreyi kapatır.
    """
    limit = ANALYSIS_DEFAULT_LIMIT
    keep_everything = False
    limit_set = False
    query_words: list[str] = []
    for token in str(raw or "").split():
        word = token.strip(",;")
        key = normalize(word)
        if not key:
            continue
        if key in ANALYSIS_ALL_TOKENS:
            keep_everything = True
            continue
        if not query_words and not limit_set and key.isdigit():
            limit = max(ANALYSIS_MIN_LIMIT, min(ANALYSIS_MAX_LIMIT, int(key)))
            limit_set = True
            continue
        query_words.append(word)
    return limit, keep_everything, " ".join(query_words)


def parse_analysis_args(raw: str) -> tuple[int, bool]:
    """``/analiz [adet] [tümü]`` argümanlarını çöz: (mesaj sayısı, filtre kapalı mı).

    Sorgu kelimesi varsa ``ValueError`` yükseltir (sorgulu kullanım için
    ``parse_analysis_request`` kullanılır).
    """
    limit, keep_everything, query = parse_analysis_request(raw)
    if query:
        raise ValueError(query)
    return limit, keep_everything


def analysis_query_pattern(query: str) -> Any:
    """Sorguyu Türkçe duyarlı, kelime sınırlı bir desene çevir (``None``: boş)."""
    words = normalize(query).split()
    if not words:
        return None
    body = r"\s+".join(re.escape(word) for word in words)
    # Sınır: sorgudan hemen önce/sonra harf veya rakam gelmemeli
    # ("tcl 65" → "tcl 655" eşleşmez).
    return re.compile(rf"(?<![^\W_]){body}(?![^\W_])", re.UNICODE)


# Ürün adı tam gösterilir (kullanıcı isteği: "tam isim alsın, orayı kesti burayı
# kesti derdi olmaz"); yalnızca aşırı uzun tek satırlar bu sınırda kesilir.
ANALYSIS_TITLE_LIMIT = 300
# Bir satırın "fiyat satırı" sayılması için fiyat dışında kalan en fazla kelime.
# ("🏷 3.558 TL" evet · "Ürün sayfasındaki 100 TL kupon ..." hayır.)
ANALYSIS_PRICE_LINE_MAX_WORDS = 4


def tidy_product_title(title: str) -> str:
    """Ürün adındaki fiyat/ayraç artıklarını toparla (``için . `` → ``için.``)."""
    cleaned = re.sub(r"\s+", " ", title or "")
    cleaned = re.sub(r"\s+([,.;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"([,;:])\s*(?=[,;:])", r"\1", cleaned)
    return cleaned.strip(" \t,;:.|/\\-–—·•")


def query_product_title(text: str) -> str:
    """Sorgu listesi için ürün adı: başlık adayı, tam metniyle (kısaltılmaz).

    "TCL 65 inç Akıllı TV 65C7K 4K QD-Mini LED …" gibi model kodlu uzun
    başlıklar kesilmez; yalnızca fiyat/noise temizliğinden kalan boşluk ve
    noktalama artıkları toparlanır.
    """
    return tidy_product_title(_search_query(text, limit=ANALYSIS_TITLE_LIMIT)
                              or message_title(text))


# Fiyat satırı sayılan ama ürün fiyatı olmayan satırlar: taksit tutarı, "200 TL
# üzeri" eşiği, kupon/çekiliş tutarı.
_ANALYSIS_PRICE_LINE_SKIP_RE = re.compile(
    r"(?i)\btaksit\w*\b|\büzeri\b|\buzeri\b|\bkupon\w*\b|\bcoupon\b|\bçekiliş\w*\b",
)


def parse_price_value(raw: str) -> float | None:
    """``31.866 TL`` / ``5.999,90`` / ``99,90`` → sayı (Türkçe yazım)."""
    digits = "".join(char for char in str(raw or "") if char.isdigit() or char in ".,")
    if not digits:
        return None
    if "," in digits and "." in digits:
        if digits.rfind(",") > digits.rfind("."):   # 1.299,90
            digits = digits.replace(".", "").replace(",", ".")
        else:                                       # 1,299.90
            digits = digits.replace(",", "")
    elif "." in digits:
        tail = digits.rpartition(".")[2]
        if digits.count(".") > 1 or len(tail) == 3:  # 1.299 / 31.866 → binlik ayraç
            digits = digits.replace(".", "")
    elif "," in digits:
        tail = digits.rpartition(",")[2]
        if digits.count(",") > 1 or len(tail) == 3:
            digits = digits.replace(",", "")
        else:                                       # 99,90 → ondalık
            digits = digits.replace(",", ".")
    try:
        return float(digits)
    except ValueError:
        return None


def _price_line_extra_words(line: str) -> int:
    """Fiyatı çıkarınca satırda kalan (sayı olmayan) kelime sayısı."""
    rest = _SEARCH_PRICE_RE.sub(" ", line or "")
    return len([token for token in WORD_TOKEN_RE.findall(rest) if not token.isdigit()])


def query_price(text: str) -> str:
    """Mesajdan ürün fiyatını seç.

    Öncelik fiyat satırlarındadır (``🏷 3.558 TL``, ``💰 Fiyat : 225 TL``); bunlar
    arasından **en düşük** değer alınır, böylece "eski 44.999 TL / yeni 31.866 TL"
    ikilisinde indirimli fiyat görünür. Taksit tutarı, "200 TL üzeri" eşiği ve
    kupon/çekiliş tutarı ürün fiyatı sayılmaz. Fiyat satırı yoksa mesajdaki
    fiyatların en düşüğü, hiç fiyat yoksa ``fiyat yok`` döner.
    """
    if price_filter.has_campaign_marker(text):
        # Kampanya izi varsa analiz, bildirimdeki fiyatla aynı kararı kullanır: kampanya
        # tutarı ya da belirsiz çoklu tutar analizde farklı bir fiyat olarak görünmez.
        info = price_filter.extract_price(text)
        if info.value is not None:
            return info.shown
        if info.reason.startswith("kampanya"):
            return "fiyat yok"
    listed: list[tuple[float, str]] = []
    plain: list[tuple[float, str]] = []
    fallback: list[str] = []
    lines = (text or "").splitlines()
    for index, line in enumerate(lines):
        prev_line = lines[index - 1] if index else None
        if price_filter.ambiguous_campaign_line(line):
            continue  # aynı satırda kampanya + etiketsiz tutar: hangisi ürün fiyatı belirsiz
        skipped = bool(_ANALYSIS_PRICE_LINE_SKIP_RE.search(line))
        on_price_line = _price_line_extra_words(line) <= ANALYSIS_PRICE_LINE_MAX_WORDS
        for match in _SEARCH_PRICE_RE.finditer(line):
            if price_filter.is_campaign_extra_amount(line, match.start(), match.end(), prev_line):
                continue  # "2. ürün 1 TL" gibi kampanya koşulu: ürün fiyatı değil
            shown = " ".join(match.group().split())
            value = parse_price_value(match.group())
            if value is None or skipped:
                fallback.append(shown)  # taksit/kupon tutarı: yalnızca son çare
                continue
            (listed if on_price_line else plain).append((value, shown))
    if listed:
        return min(listed, key=lambda item: item[0])[1]
    if plain:
        return min(plain, key=lambda item: item[0])[1]
    return fallback[0] if fallback else "fiyat yok"


ANALYSIS_SCAN_START = "🔎 Analiz başladı"
ANALYSIS_SCAN_RUNNING = "🔎 Geçmiş taranıyor"


async def start_analysis_scan(event: Any, targets: Sequence[Any], per_source: int,
                              *, purpose: str = "", query: str = "") -> Any:
    """``Analiz başladı`` ilerleme mesajını gönder (gönderilemezse ``None``).

    Mesaj ``update_analysis_scan`` ile kaynak kaynak güncellenir; rapor gelince
    ``control_reply(..., stale=[...])`` ile silinir, böylece ekranda yalnızca
    sonuç kalır. Uzun taramalarda kullanıcı çalıştığını bu mesajdan görür.
    """
    lines = [f"{ANALYSIS_SCAN_START}: {len(targets)} kaynak × son {per_source} mesaj taranıyor…"]
    if query:
        lines.append(f"• Sorgu: '{query}'")
    if purpose:
        lines.append(purpose)
    try:
        return await event.reply("\n".join(lines))
    except Exception:  # noqa: BLE001 - yanıt gitmese de tarama sürsün
        log.debug("Analiz başlangıç yanıtı gönderilemedi.")
        return None


def analysis_status_id(status: Any) -> int:
    """İlerleme mesajının ID'si (yoksa 0 → silinecek mesaj yok)."""
    return next(iter(sent_message_ids(status)), 0)


async def update_analysis_scan(status: Any, done: int, total: int, detail: str) -> None:
    """İlerleme mesajını güncelle; düzenleme başarısızsa tarama devam eder."""
    if status is None:
        return
    try:
        await status.edit(f"{ANALYSIS_SCAN_RUNNING}... {done}/{total} kaynak · {detail}")
    except Exception:  # noqa: BLE001 - düzenleme başarısızsa tarama sürsün
        pass


def build_query_lines(texts: Iterable[str], pattern: Any) -> list[str]:
    """Desenle eşleşen mesajları ``ürün adı - fiyat`` satırlarına çevir.

    Ürün adı, ürün başlığı seçicisiyle (``_search_query``) bulunur ve tam
    gösterilir ("TCL 65 inç Akıllı TV 65C7K …" gibi sonunda model kodu olan
    başlıklar kırpılmaz). Fiyat ``query_price`` ile seçilir. Aynı satır birden
    çok kaynakta varsa bir kez yazılır.
    """
    lines: list[str] = []
    seen: set[str] = set()
    for text in texts:
        if not text or not pattern.search(normalize(text)):
            continue
        title = " ".join(query_product_title(text).split()) or "(başlıksız)"
        line = f"{title} - {query_price(text)}"
        if line not in seen:
            seen.add(line)
            lines.append(line)
    return lines


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
            humanize(time.time() - STARTED_AT), STATS["seen"], STATS["matched"], STATS["forwarded"],
            POLL_TELEMETRY["interval"], POLL_TELEMETRY["last_elapsed"], int(POLL_TELEMETRY["flood_count"]),
            gate_stats(NOTIFY_BOT_TOKEN)["rate_limit_count"],
            gate_stats(NOTIFY_BOT_TOKEN)["retry_wait_seconds"],
            gate_stats(NOTIFY_BOT_TOKEN)["lock_wait_seconds"],
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
    global SELF_ID

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
    store = ConfigStore(config_path(args.config), config)

    gh_pat = os.getenv("GH_PAT", "").strip()
    auto_restart = bool(config.get("auto_restart", True))
    restart_minutes = max(1, int(os.getenv("RESTART_AFTER_MINUTES", "330")))

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
        log.info("Bağlantı ekleri: akıllı mod — ürün adresi '%s' yazısına gömülür "
                 "(buton, gizli hyperlink ya da görünür URL); alıntıdaki bağlantılar yedek kalır.",
                 PRODUCT_LINK_TEXT)
    elif LINK_APPENDIX_MODE == "all":
        log.info("Bağlantı ekleri: tüm gizli linkler iletinin sonuna yazılacak.")
    if MESSAGE_LINK_LINE:
        log.info("Orijinal mesajın t.me adresi kaynak grup adının içine gömülecek "
                 "(ayrı '🔗 %s' satırı yok).", MESSAGE_LINK_LABEL)
    if NOTIFY_BOT_TOKEN:
        log.info("Bildirim biçimi (basit düzen): en üstte ürün başlığı → '%s' → altında mesaj "
                 "OLDUĞU GİBİ → en altta kalın kaynak adı%s; mesajın adresi o adın içine "
                 "gömülüdür. Tekrarlı paylaşımlarda kaynak adı satırı kalkar, yerine çoklu "
                 "paylaşım notu yazılır (📌 N kere paylaşıldı: <gruplar>; her ad kendi "
                 "mesajına bağlı).",
                 PRODUCT_LINK_TEXT, " + medya" if NOTIFY_MEDIA else "")
    if CLEAN_COMMANDS:
        log.info("Komut temizliği açık: yeni komutta önceki komut/yanıt silinir, "
                 "bildirimlere dokunulmaz.")
    if DEDUP_ENABLED:
        log.info("Tekrar birleştirme açık: aynı ürün sorgusu %s boyunca tek mesajda toplanır, "
                 "açılışta hedefteki son %d ileti taranır.",
                 humanize(DEDUP_WINDOW_SECONDS), DEDUP_SCAN_LIMIT)
    else:
        log.info("Tekrar birleştirme kapalı: her eşleşme ayrı mesaj olarak iletilir.")

    # --- Kaynakları, kontrol sohbetini ve hedefi çöz.
    _orig_ids = dict(store.config.get("source_chat_ids") or {}) if isinstance(store.config.get("source_chat_ids"), dict) else {}
    notes, fatal = await resolve_chat_groups(
        client, config, {"sources", "destination", "control"},
    )
    for note in notes:
        log.info("%s", note)
    if fatal:
        raise SystemExit(f"{fatal}; source_chats listesini ve hesap üyeliğini kontrol et.")
    # Kaynak ID'lerini kalıcılaştır — sonraki açılışlarda username FloodWait'ini önler
    _new_ids = store.config.get("source_chat_ids")
    if isinstance(_new_ids, dict) and _new_ids != _orig_ids:
        try:
            await asyncio.to_thread(atomic_write_json, store.path, store.config)
            log.info("Kaynak ID'leri kalıcılaştırıldı: %s/%s", len(_new_ids), len(chat_values(store.config.get("source_chats"))))
            # GitHub kalıcılığı: bir sonraki runner aynı ID'leri görsün; sessiz, hatası startup'ı öldürmez
            # Yalnızca gerçek repo checkout'unda push et — testteki /tmp dosyası repo dışı
            if push_token() and git_repo_root(store.path.parent) is not None:
                _st, _dt = await asyncio.to_thread(commit_and_push, store.path, "kaynak ID'leri güncellendi")
                log.info("Kaynak ID push: %s %s", _st, _dt)
        except Exception as exc:  # noqa: BLE001 - kalıcılık hatası bot'u durdurmasın
            log.warning("Kaynak ID'leri yazılamadı: %s: %s", type(exc).__name__, exc)
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
            "NOTIFY_BOT_TOKEN secret/ortam değişkeni tanımlı değil: mesajları kendi hesabın "
            "gönderdiği için Telegram BİLDİRİM ÜRETMEZ. Sesli bildirim istiyorsan @BotFather'dan "
            "bir bot oluşturup gruba ekle ve token'ı NOTIFY_BOT_TOKEN secret'ında sakla."
        )

    def is_control_event(event: events.NewMessage.Event) -> bool:
        """Komut yalnızca kontrol sohbetinden gelirse işlenir."""
        if isinstance(event, PrivateControlEvent):
            return config_flag(store.config.get("private_control"), False)
        if config_flag(store.config.get("private_control"), False):
            return event.chat_id == SELF_ID  # emergency fallback, not the offer group
        return event.chat_id in CONTROL_IDS

    def is_admin_event(event: events.NewMessage.Event) -> bool:
        """Kayıtlı Mesajlar'a yazan hesabın sahibi; gruptaysa admin listesi geçerli."""
        if isinstance(event, PrivateControlEvent):
            return event.sender_id in (ADMIN_IDS | {SELF_ID}) and event.chat_id == event.sender_id
        if event.chat_id == SELF_ID:
            return True
        return event.sender_id in ADMIN_IDS

    async def delete_command_messages(chat_id: int, message_ids: Sequence[int]) -> None:
        """Komut sohbetindeki eski komut/yanıt mesajlarını sil.

        Yalnızca ``COMMAND_MESSAGES`` kaydından gelen ID'ler silinir; indirim
        bildirimleri bu kayda girmediği için silinmeleri mümkün değildir.
        Silme başarısız olsa bile (ör. yetki yok) komut akışı devam eder.
        """
        ids = [int(item) for item in message_ids if item]
        if not ids:
            return
        try:
            await client.delete_messages(int(chat_id or 0), ids, revoke=True)
        except Exception as exc:  # noqa: BLE001 - silinemezse komut yine çalışsın
            log.warning("Eski komut mesajları silinemedi (chat=%s, adet=%d): %s: %s",
                        chat_id, len(ids), type(exc).__name__, exc)
            return
        STATS["cleaned_commands"] += len(ids)
        log.debug("Komut sohbeti temizlendi: chat=%s, %d mesaj.", chat_id, len(ids))

    async def control_reply(event: events.NewMessage.Event, text: str, *,
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
        if isinstance(event, PrivateControlEvent) or not CLEAN_COMMANDS:
            return sent_ids
        chat_id = int(getattr(event, "chat_id", 0) or 0)
        current = [int(getattr(event, "id", 0) or 0), *sent_ids]
        outdated = note_command_messages(chat_id, current)
        outdated += [int(item) for item in stale if item]
        await delete_command_messages(chat_id, outdated)
        return sent_ids

    async def delete_account_copy(sent_ids: Sequence[int], source_name: str) -> bool:
        """Bildirim botu gönderdikten sonra hedefteki hesap kopyasını sil.

        Böylece grupta her fırsat tek mesaj olarak kalır: botun bildirimi
        (telefonuna uyarı düşen mesaj). Bildirim gidemezse kopya SİLİNMEZ;
        mesajsız kalmaktansa iki kopya iyidir. Silme başarısız olursa yalnızca
        uyarı loglanır, ileti yine başarılı sayılır.
        """
        if not sent_ids or DESTINATION_ID is None:
            return False
        try:
            await client.delete_messages(DESTINATION, list(sent_ids), revoke=True)
        except Exception as exc:  # noqa: BLE001 - silinemezse mesaj kalsın
            log.warning("Hesap kopyası silinemedi (kaynak=%s): %s: %s",
                        source_name, type(exc).__name__, exc)
            return False
        STATS["cleaned"] += 1
        log.info("Hesap kopyası silindi (%d mesaj); grupta bot bildirimi kaldı (kaynak=%s).",
                 len(sent_ids), source_name)
        return True

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

    settings_lock = asyncio.Lock()
    private_api = BotAPI(NOTIFY_BOT_TOKEN) if NOTIFY_BOT_TOKEN else None
    # Optional feature: failure to initialize must not prevent channel listening.
    tracker = None
    if private_api and config_flag(store.config.get("private_control"), False):
        try:
            from product_tracker import ProductTracker

            def persist_products(path, data):
                # Contents API avoids dirtying the shared checkout while config
                # commits/rebases use it. The next runner checks out this snapshot.
                with GIT_WRITE_LOCK:
                    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
                    token = push_token()
                    repo = git_repo_root(store.path.parent)
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

            tracker = ProductTracker(
                store.path.with_name("product_watches.json"), private_api, persist_products,
                owner_id=SELF_ID,
                header_formatter=lambda cents, title: build_header_line(header_price_text(cents / 100), title),
            )
        except Exception:
            log.exception("Ürün takibi başlatılamadı; kanal dinleme devam ediyor.")

    async def prepare_private_offer(item):
        # Turning off/changing the filter also cancels queued, unsent copies.
        if not dm_should_send(store.config, item["match_text"], item.get("strike") or ()):
            return None
        info = item.get("bot")
        if not info:
            ids = item.get("account_ids") or []
            if not ids:
                raise BotAPIError("Grup kopyası bulunamadı.")
            message = await client.get_messages(item["destination"], ids=ids[0])
            if message is None:
                raise BotAPIError("Grup kopyası artık yok.")
            text = getattr(message, "message", "") or ""
            media = getattr(message, "media", None)
            info = {
                "message_id": message.id,
                "kind": "media" if media and not isinstance(media, types.MessageMediaWebPage) else "text",
                "text": text,
                "entities": bot_api_entities(message, text),
                "keyboard": item.get("keyboard"),
            }
            if info["kind"] == "media" and not bot_history_ids_shared(item["destination"]):
                log.warning("Normal gruptaki hesap medya ID'si Bot API'ye taşınamaz; özel bildirim metin olarak gönderilecek.")
                info["kind"] = "text"
                info["message_id"] = None
                info["text"] = text or "📎 Medyalı fırsat: gruptaki hesap kopyasına bak."
        return private_copy_request(SELF_ID, item["destination"], info)

    private_offers = PrivateOfferQueue(private_api, prepare_private_offer)

    def enqueue_private_offer(event, info):
        if not private_api:
            return
        if not config_flag(store.config.get("dm_enabled"), False) and dm_price_threshold(store.config) is None:
            return
        sanitized = sanitize_message(event)
        text = message_text(sanitized)
        strike = price_filter.strike_spans_from_entities(text, entities_for_text(sanitized, text))
        if dm_should_send(store.config, text, strike):
            private_offers.submit({
                "destination": DESTINATION_ID, "match_text": text, "strike": strike,
                "bot": info.get("bot"), "account_ids": info.get("account_ids"),
                "keyboard": build_inline_keyboard(event),
            })

    def dm_status():
        words = store.config.get("dm_keywords", [])
        enabled = config_flag(store.config.get("dm_enabled"), False)
        return (
            f"🎯 Kişisel bildirim: {'AÇIK' if enabled else 'KAPALI'}\n"
            f"DM dahil · Kelimeler: {', '.join(words) or '(boş)'}\n"
            f"DM hariç: {', '.join(store.config.get('dm_exclude_keywords', [])) or '(boş)'}\n"
            f"DM fiyat: {(price_filter.format_price(dm_price_threshold(store.config)) + ' ve altı') if dm_price_threshold(store.config) is not None else 'KAPALI'}"
            " (kelimelerden bağımsız)\n"
            "Dahil ifadelerden biri blok halinde geçerse özel kopya gelir; hariç eşleşmesi engeller.\n"
            "Grup filtreleri ve tekrar birleştirmesi geçerlidir. Tekrarlı alarm yok.\n"
            f"Bu oturum: gönderilen {private_offers.sent}, hata {private_offers.failed}, "
            f"kuyrukta {private_offers.queue.qsize()}, taşma {private_offers.dropped}\n"
            "/ekle → dmdahil / dmharic / dmfiyat → değer → /kaydet\n"
            "/cikar → dmdahil / dmharic / dmfiyat → numara veya değer → /kaydet\n"
            "/dmfiltreac - Aç\n"
            "/dmfiltrekapat - Kapat"
        )

    async def private_channel_health() -> str:
        """Özel komut kanalının teşhisi; /test ile görünür (token asla yazılmaz)."""
        if not NOTIFY_BOT_TOKEN:
            return ("🔎 Özel komut kanalı: KAPALI — NOTIFY_BOT_TOKEN tanımlı değil.\n"
                    "Komutlar Kayıtlı Mesajlar'dan işlenir; bot sana özel mesaj atamaz.")
        lines = ["🔎 Özel komut kanalı (bildirim botu):"]
        try:
            me = await private_api.call("getMe", {}) or {}
            username = me.get("username")
            lines.append(f"• Bot: @{username}" if username
                         else "• Bot: kullanıcı adı alınamadı (token geçersiz olabilir).")
        except BotAPIError as exc:
            lines.append(f"• Bot: alınamadı — token geçersiz olabilir (hata {exc.code}).")
        except Exception as exc:
            lines.append(f"• Bot: alınamadı ({type(exc).__name__}).")
        try:
            webhook = await private_api.call("getWebhookInfo", {}) or {}
            if webhook.get("url"):
                lines.append("• Webhook: VAR — getUpdates ile çelişir; özel komutlar "
                             "çalışmaz. Webhook'u kaldırıp takipçiyi yeniden başlat.")
            else:
                lines.append("• Webhook: yok")
        except Exception as exc:
            lines.append(f"• Webhook: alınamadı ({type(exc).__name__}).")
        health = poll_health()
        status = health.get("status")
        label = {
            "off": "başlatılmadı (private_control kapalı)",
            "starting": "başlatılıyor",
            "running": "çalışıyor",
            "webhook": "webhook var — kapalı",
            "unauthorized": "token geçersiz (401) — kapalı",
            "conflict": "409 çakışma — aynı token'ı başka bir süreç kullanıyor",
        }.get(status, str(status))
        lines.append(f"• Poller: {label}")
        if health.get("last_error"):
            lines.append(f"• Son hata: {health['last_error']}")
        lines.append("Bot ilk özel mesajı kendisi atamaz: botun özel sohbetinde /start yaz "
                     "ve botu engellemediğinden emin ol.")
        return "\n".join(lines)

    async def save_dm_setting(event, *, action=None, raw="", enabled=True):
        async with settings_lock:
            before = store.snapshot()
            if action is not None:
                existing = store.config.get("dm_keywords", [])
                try:
                    words = edit_dm_keywords(existing, raw, action)
                except ValueError as exc:
                    await control_reply(event, str(exc))
                    return
                if words == existing:
                    pending = peek_pending(pending_key(event))
                    if pending and pending.get("stage") == "dm_words":
                        drop_pending(pending_key(event))
                    await control_reply(event, "ℹ️ Bu kelimeler zaten listede; ayarlar değişmedi.\n" + dm_status())
                    return
                store.config["dm_keywords"] = words
                # Adding a new interest opts in; removal preserves a paused
                # filter and switches it off when the last keyword is removed.
                enabled = bool(words) and (action == "add" or config_flag(before.get("dm_enabled"), False))
            store.config["dm_enabled"] = enabled
            ok, note = await save_config(store, "Kişisel bildirim ayarı güncellendi")
            if not ok:
                store.restore(before)
                await control_reply(event, "❌ Kaydedilemedi; önceki ayar korundu.\n" + note)
                return
            pending = peek_pending(pending_key(event))
            if pending and pending.get("stage") == "dm_words":
                drop_pending(pending_key(event))
            await control_reply(event, "✅ Anında uygulandı. Grup ayarları değişmedi.\n" + dm_status() + "\n" + note)

    async def handle_dm_command(event, command, rest):
        if not isinstance(event, PrivateControlEvent) or event.sender_id != SELF_ID:
            await control_reply(event, "🎯 Kişisel ayarlar yalnızca hesap sahibinin botla özel sohbetinden yönetilir.")
            return
        if command == COMMAND_DM_STATUS:
            await control_reply(event, dm_status() + (
                "\n\nBu komut yalnızca bilgi gösterir. Kelime eklemek / çıkarmak için /ekle veya "
                "/cikar → dmdahil/dmharic seçip /kaydet kullanın." if rest else ""
            ))
        elif command == COMMAND_DM_FILTER_OPEN:
            enabled = True
            if enabled and not store.config.get("dm_keywords"):
                await control_reply(event, "Önce /ekle → dmdahil ile kelimelerini ekleyip /kaydet. Boş liste hiçbir şeyi göndermez.")
                return
            await save_dm_setting(event, enabled=enabled)
        elif command == COMMAND_DM_FILTER_CLOSE:
            await save_dm_setting(event, enabled=False)
        else:
            await control_reply(event, f"Bilinmeyen komut: {command}\n\n{HELP_TEXT}")

    async def can_edit_list(event, field):
        if (field in DM_LIST_FIELDS or field == DM_PRICE_FIELD) and (
            not isinstance(event, PrivateControlEvent) or event.sender_id != SELF_ID
        ):
            await control_reply(event, "🎯 Kişisel ayarlar yalnızca hesap sahibinin botla özel sohbetinden yönetilir.")
            return False
        return True

    async def save_pending_change(event: events.NewMessage.Event,
                                 key: PendingKey) -> None:
        """Taslağı çakışma kontrolünden geçir, uygula ve sadece şimdi GitHub'a yaz."""
        async with settings_lock:
            pending = peek_pending(key)
            if pending is None:
                await control_reply(event, "ℹ️ Kaydedilecek bekleyen bir değişiklik yok. Önce /ekle veya /cikar.")
                return
            if pending.get("stage") == "webdetakip":
                # product_tracker kayıtlarını product_watches.json'da anında
                # kalıcı tutar; /ekle akışındaki ayrı /kaydet adımı yoktur.
                drop_pending(key)
                await control_reply(event, "ℹ️ Ürün takip kayıtları product_watches.json'da; bu akışta kayıt anında kalıcılaşır, ayrı /kaydet adımı yok. Ayar taslağı kapatıldı. Takip menüsü: /webtakip")
                return
            if pending.get("stage") != "confirm":
                await control_reply(event, "ℹ️ İşlem henüz tamamlanmadı. Önce istenen liste/değer seçimini yap veya /iptal.")
                return

            field = str(pending.get("field") or "")
            base = pending.get("base_config") or {}
            draft = pending.get("draft_config") or {}
            if field not in TELEGRAM_LIST_FIELDS or field not in draft:
                drop_pending(key)
                await control_reply(event, "❌ Taslak geçersiz olduğu için iptal edildi. Yeniden /ekle veya /cikar ile başla.")
                return

            if not await can_edit_list(event, field):
                return
            before = store.snapshot()
            if before.get(field) != base.get(field):
                drop_pending(key)
                await control_reply(event,
                    f"⚠️ {TELEGRAM_LIST_META[field]['title']} taslağı hazırlanırken başka bir değişiklik yapılmış.\n"
                    "Güncel listeyi korudum; lütfen /ekle veya /cikar ile yeniden başla."
                )
                return

            candidate = copy.deepcopy(before)
            candidate[field] = copy.deepcopy(draft[field])
            if field == "dm_keywords":
                candidate["dm_enabled"] = bool(candidate[field]) and (
                    pending.get("action") == "add" or config_flag(before.get("dm_enabled"), False)
                )
            groups = changed_groups(before, candidate)
            store.config = candidate
            notes, fatal = await apply_setting_change(before)
            if fatal:
                store.restore(before)
                apply_runtime_config(store.config)
                if groups:
                    await resolve_chat_groups(client, store.config, groups, strict=False)
                drop_pending(key)
                await control_reply(event,
                    f"❌ Değişiklik uygulanamadı: {fatal}\n"
                    "Önceki ayarlar korundu; taslağı iptal ettim. Grup/kanal bilgisini kontrol edip yeniden dene."
                )
                return

            ok, save_note = await save_config(store, f"{TELEGRAM_LIST_META[field]['title']} güncellendi")
            if not ok:
                store.restore(before)
                apply_runtime_config(store.config)
                if groups:
                    await resolve_chat_groups(client, store.config, groups, strict=False)
                set_pending(key, **pending)
                await control_reply(event, "\n".join([
                    "❌ Değişiklik dosyaya yazılamadı; çalışan ayarlar geri yüklendi.",
                    save_note,
                    "Taslak korundu. Dosya erişimini düzelttikten sonra /kaydet ile tekrar deneyebilir veya /iptal edebilirsin.",
                ]))
                return

            drop_pending(key)
            log.info("Telegram liste ayarı kaydedildi: %s (%s)", field, pending.get("action"))
            await control_reply(event, "\n".join([
                "✅ Değişiklik kaydedildi ve çalışan ayarlara uygulandı.",
                f"📂 {TELEGRAM_LIST_META[field]['title']}",
                f"📊 Güncel kayıt sayısı: {len(telegram_list_items(store.config, field))}",
                *notes,
                save_note,
            ]))

    async def apply_filter_toggle(event: events.NewMessage.Event, action: str, target: str) -> None:
        """Filtreyi aç/kapat, config'e yaz ve GitHub'a gönder (anında uygulanır).

        Dahili ve harici filtre ayrı ayrı yönetilir; ikisi de kapatılabilir.
        Dosyaya yazma başarısız olursa önceki durum geri yüklenir.
        """
        async with settings_lock:
            before = store.snapshot()
            ok, candidate, note = stage_filter_toggle(before, action, target)
            if not ok:
                drop_pending(pending_key(event))
                await control_reply(event, note)
                return

            store.config = candidate
            apply_runtime_config(store.config)
            what = "Filtreler" if target == "both" else FILTER_TARGET_META[target]["title"] + " filtresi"
            ok_save, save_note = await save_config(
                store, f"{what} {'açıldı' if action == 'open' else 'kapatıldı'}",
            )
            if not ok_save:
                store.restore(before)
                apply_runtime_config(store.config)
                await control_reply(event, "❌ Filtre değişikliği kaydedilemedi; önceki durum geri yüklendi.\n" + save_note)
                return

            drop_pending(pending_key(event))
            log.info("Filtre %s: %s (hedef=%s)", "açıldı" if action == "open" else "kapatıldı",
                     what, target)
            await control_reply(event, note + "\n" + save_note)

    async def handle_filter_command(event: events.NewMessage.Event, action: str, rest: str) -> None:
        """`/tgfiltreac` ve `/tgfiltrekapat`: argümanla hemen uygula, argüman yoksa sor."""
        key = pending_key(event)
        pending = peek_pending(key)
        if pending and pending.get("stage") == "confirm":
            await control_reply(event,
                "📝 Önce bekleyen liste taslağını sonuçlandır.\n"
                "✅ /kaydet ile kaydet veya ↩️ /iptal ile vazgeç."
            )
            return

        raw = rest.strip()
        if not raw:
            set_pending(key, **new_filter_edit(action))
            await control_reply(event, build_filter_toggle_prompt(action, store.config))
            return

        target = resolve_filter_target(raw)
        if target is None:
            # Soru açık kalsın: kullanıcı 1/2/3 yazabilir ya da /iptal edebilir.
            set_pending(key, **new_filter_edit(action))
            await control_reply(event, "\n".join([
                f"❌ '{raw}' anlaşılmadı.",
                "1 (dahili), 2 (harici) veya 3 (ikisi) yazabilirsin.",
                "",
                build_filter_toggle_prompt(action, store.config),
            ]))
            return

        await apply_filter_toggle(event, action, target)

    async def list_query_matches(event: events.NewMessage.Event, targets: list[Any],
                                 per_source: int, query: str) -> None:
        """Tüm kaynakların son ``per_source`` mesajında ``query`` geçenleri listele.

        Tarama, istatistik analiziyle aynı okuma yolunu kullanır (salt okuma;
        hiçbir mesaj gönderilmez/değiştirilmez). Tarama boyunca "Analiz
        başladı" ilerleme mesajı kaynak kaynak güncellenir, rapor gelince
        silinir. Sonuç satırları ``ürün adı (tam) - fiyat`` biçimindedir; uzun
        listeler ``control_reply`` tarafından birden çok mesaja bölünür.
        """
        pattern = analysis_query_pattern(query)
        started = time.time()
        status = await start_analysis_scan(
            event, targets, per_source,
            purpose="Bitince eşleşen ürünleri (ürün adı - fiyat) göndereceğim.",
            query=query,
        )
        status_id = analysis_status_id(status)
        texts: list[str] = []
        failures: list[str] = []
        matches = 0
        for index, item in enumerate(targets, start=1):
            try:
                async for message in client.iter_messages(item["id"], limit=per_source):
                    text = message_text(message)
                    texts.append(text)
                    if text and pattern.search(normalize(text)):
                        matches += 1
            except Exception as exc:  # noqa: BLE001 - tek kaynak taramayı durdurmasın
                failures.append(f"{item['name']} ({type(exc).__name__})")
                log.warning("Geçmiş okunamadı (%s): %s: %s", item["name"], type(exc).__name__, exc)
            await update_analysis_scan(status, index, len(targets),
                                       f"{matches} eşleşme · sorgu: '{query}'")
        lines = build_query_lines(texts, pattern)
        log.info("Sorgu listesi: '%s' → %d eşleşme (%d mesaj, %d kaynak).",
                 query, len(lines), len(texts), len(targets))
        header = [
            f"🔎 '{query}' içeren mesajlar: {len(lines)} ürün",
            f"• Kaynak: {len(targets)} grup · kaynak başına son {per_source} mesaj"
            f" · süre: {humanize(time.time() - started)}",
        ]
        if failures:
            header.append(f"• ⚠️ Okunamayan kaynak: {', '.join(failures)}")
        if not lines:
            await control_reply(event, "\n".join(header + ["", "Eşleşen mesaj bulunamadı."]),
                                stale=[status_id])
            return
        shown = lines[:ANALYSIS_QUERY_MAX_LINES]
        body = header + ["", *shown]
        if len(lines) > len(shown):
            body.append(f"… ilk {len(shown)} gösteriliyor; daha az sonuç için sayı/sorguyu daralt.")
        body.append("ℹ️ Satır biçimi: ürün adı (tam) - fiyat · fiyat satırı varsa en düşük fiyat.")
        await control_reply(event, "\n".join(body), stale=[status_id])

    async def analyze_history(event: events.NewMessage.Event, rest: str) -> None:
        """Kaynak geçmişinin başlıklarını tara; kelime istatistiklerini gönder.

        Yalnızca mesajın ilk anlamlı satırı (başlık) okunur; her kaynaktan en
        fazla ``per_source`` mesaj alınır. Tek bir kaynak okunamazsa tarama
        durmaz, raporda "okunamayan kaynak" olarak listelenir.
        Sorgu verilirse (``/analiz 1000 tcl 65``) istatistik yerine eşleşen
        ürünlerin listesi döner (bkz. ``list_query_matches``).
        """
        per_source, keep_everything, query = parse_analysis_request(rest)

        targets = [item for item in SOURCES if item.get("joined")]
        if not targets:
            await control_reply(event, "ℹ️ Taranacak kaynak yok. Önce /kaynaklar ile listeyi kontrol et.")
            return

        if query:
            await list_query_matches(event, targets, per_source, query)
            return

        started = time.time()
        # İlerleme mesajı bilinçli olarak temizlik kaydına girmez: rapor gelince
        # "stale" listesiyle silinir, böylece ekranda yalnızca istatistik raporu
        # kalır. Uzun taramaların çalıştığı bu mesajdan görülür.
        status = await start_analysis_scan(
            event, targets, per_source,
            purpose="Uzun sürebilir; bitince iki istatistiği göndereceğim.",
        )
        status_id = analysis_status_id(status)

        titles: list[str] = []
        failures: list[str] = []
        for index, item in enumerate(targets, start=1):
            try:
                async for message in client.iter_messages(item["id"], limit=per_source):
                    title = message_title(message_text(message))
                    if title:
                        titles.append(title)
            except Exception as exc:  # noqa: BLE001 - tek kaynak taramayı durdurmasın
                failures.append(f"{item['name']} ({type(exc).__name__})")
                log.warning("Geçmiş okunamadı (%s): %s: %s", item["name"], type(exc).__name__, exc)
            await update_analysis_scan(status, index, len(targets), f"{len(titles)} başlık")

        analysis = analyze_titles(titles, keep_everything=keep_everything)
        log.info("Kelime analizi: %d başlık, %d tekil, %d kaynak, %s.",
                 analysis["counts"]["titles"], analysis["counts"]["unique"], len(targets),
                 "tüm kelimeler" if keep_everything else "filtreli")
        report = build_analysis_report(
            analysis,
            config=store.config,
            sources=len(targets),
            per_source=per_source,
            keep_everything=keep_everything,
            elapsed=time.time() - started,
            failures=failures,
        )
        await control_reply(event, report, stale=[status_id])

    async def test_source_access(event: events.NewMessage.Event, rest: str) -> None:
        """Geçmiş erişimini, ham mesaj bütünlüğünü ve yerel kullanım yolunu sınar.

        Her kaynakta son tek mesaj okunur; mesaj gönderilmez/değiştirilmez ve
        toplu test sırasında medya dosyaları indirilmez. Seçili kaynakta ham
        metin uzunluğu/hash'i, entity aralıkları, medya/link metadatası ve
        compose_message kuru çalıştırması ayrıca raporlanır.
        """
        requested = rest.strip().strip("'\"")
        all_sources = list(SOURCES)
        targets = all_sources
        unresolved: list[tuple[Any, Exception]] = list(SOURCE_FAILURES)
        if requested:
            needle = requested.casefold()
            target = None
            if needle.isdigit():
                index = int(needle)
                if 1 <= index <= len(all_sources):
                    target = all_sources[index - 1]
            for item in all_sources:
                aliases = {
                    str(item.get("requested", "")).strip().lstrip("@").casefold(),
                    str(item.get("username", "") or "").strip().lstrip("@").casefold(),
                    str(item.get("name", "")).strip().casefold(),
                    str(item.get("id", "")).strip().casefold(),
                }
                if needle in aliases or requested.casefold() in {
                    str(item.get("requested", "")).strip().casefold(),
                    str(item.get("username", "") or "").strip().casefold(),
                }:
                    target = item
                    break
            if target is not None:
                targets = [target]
                unresolved = []
            else:
                failed = next((entry for entry in SOURCE_FAILURES
                               if needle in {str(entry[0]).strip().lstrip("@").casefold(),
                                             str(entry[0]).strip().casefold()}), None)
                if failed:
                    targets = []
                    unresolved = [failed]
                else:
                    await control_reply(
                        event,
                        "❌ Kaynak bulunamadı. /kaynaklar ile sırayı gör; kullanım: "
                        "/kaynaktest [sıra | @kullanıcı_adı | -100... ID].",
                    )
                    return

        tested = 0
        readable = 0
        joined = 0
        raw_checked = 0
        usable = 0
        show_full_excerpt = bool(requested)
        lines = [
            "🧪 KAYNAK MESAJ ERİŞİM TESTİ",
            "━━━━━━━━━━━━━━━━━━━━",
            f"• Denenen çözülmüş kaynak: {len(targets)}",
            "• Her kaynakta yalnızca en yeni 1 mesaj okunur; tüm geçmiş taranmaz.",
            "• Ham mesaj değiştirilmeden denetlenir; kullanım testi yalnızca yerel kuru çalıştırmadır.",
            "",
        ]
        live_counts = STATS.get("source_seen", {})
        if not isinstance(live_counts, dict):
            live_counts = {}
        for item in targets:
            tested += 1
            name = " ".join(str(item.get("name") or item.get("id") or "kaynak").split())
            configured_as = " ".join(str(item.get("requested") or item.get("id") or "").split())
            source_label = f"{name} [{configured_as}]" if configured_as else name
            live_count = int(live_counts.get(item["id"], 0) or 0)
            is_joined = bool(item.get("joined"))
            if is_joined:
                joined += 1
            try:
                latest = None
                async for message in client.iter_messages(item["id"], limit=1):
                    latest = message
                    break
                readable += 1
                if latest is None:
                    lines.append(
                        f"✅ {source_label} — geçmiş okunuyor, henüz mesaj yok · canlı event: {live_count}"
                    )
                    continue

                message_id = getattr(latest, "id", None)
                date = getattr(latest, "date", None)
                age = ""
                try:
                    timestamp = date.timestamp() if hasattr(date, "timestamp") else float(date)
                    age = f" · {humanize(time.time() - timestamp)} önce"
                except (TypeError, ValueError, OverflowError, OSError):
                    pass

                raw_field = getattr(_as_message(latest), "message", None)
                raw_text = message_text(latest)
                empty_media_message = raw_field is None and not raw_text
                raw_exact = isinstance(raw_field, str) and raw_text == raw_field
                raw_integrity_ok = raw_exact or empty_media_message
                media = getattr(_as_message(latest), "media", None)
                media_descriptor = bot_media_descriptor(latest)
                if media is None:
                    media_label = "yok"
                elif media_descriptor:
                    size = int(media_descriptor.get("size", 0) or 0)
                    media_label = f"{media_descriptor['kind']} · {size} B"
                else:
                    media_label = type(media).__name__

                entities = message_entities(latest)
                text_length = utf16_length(raw_text)
                valid_entities = sum(
                    1 for entity in entities
                    if int(getattr(entity, "offset", 0) or 0) >= 0
                    and int(getattr(entity, "length", 0) or 0) > 0
                    and int(getattr(entity, "offset", 0) or 0)
                    + int(getattr(entity, "length", 0) or 0) <= text_length
                )
                links = extract_links(latest)
                product_title = _search_query(latest)
                dry_run = compose_message(
                    latest,
                    limit=CAPTION_LIMIT - 24 if media else MESSAGE_LIMIT - 100,
                    link_kinds=LINK_KINDS,
                    source_name=name if SOURCE_FOOTER else None,
                )
                layout_ok = bool(dry_run["text"] or media or links)
                if dry_run["header"]:
                    # Basit düzen: üst satır ("₺56 — ürün adı") en üstte,
                    # mesajın aslı hemen altında.
                    layout_ok = (
                        layout_ok
                        and dry_run["content"].startswith(dry_run["header"] + "\n\n")
                        and dry_run["body"] == message_text(sanitize_message(latest))
                    )
                can_use = (
                    raw_integrity_ok and valid_entities == len(entities) and layout_ok
                    and bool(raw_text.strip() or media or links)
                )
                if can_use:
                    usable += 1
                raw_checked += int(raw_integrity_ok)

                mark = "✅" if is_joined else "⚠️ ÜYE DEĞİL"
                membership_note = "" if is_joined else " · yeni mesajlar gelmeyebilir"
                suffix = f" · #{message_id}" if message_id is not None else ""
                lines.append(
                    f"{mark} {source_label} — son mesaj{suffix}{age} · canlı event: {live_count}{membership_note}"
                )
                digest = hashlib.sha256(raw_text.encode("utf-8", "replace")).hexdigest()[:12]
                raw_status = (
                    "birebir OK" if raw_exact
                    else "metin yok (medya mesajı)" if empty_media_message
                    else "ham metin alanı yok/uyuşmuyor"
                )
                lines.append(
                    f"   Ham: {raw_status} · {len(raw_text)} karakter · sha256 {digest}"
                    f" · entity {valid_entities}/{len(entities)} · link {len(links)} · medya {media_label}"
                )
                layout_note = f" · başlık {product_title or 'bulunamadı'}"
                if SMART_HEADER:
                    # Üst satır = "fiyat - ürün adı"; `başlık` fiyatsız hâlidir
                    # ve Google/Akakçe/Cimri aramaları ile dedup o değeri kullanır.
                    layout_note += f" · üst satır {(dry_run['header'] or 'yok')[:70]}"
                lines.append(
                    "   Kuru kullanım: " + ("✅ kullanılabilir" if can_use else "⚠️ kontrol gerekli")
                    + layout_note
                )
                if show_full_excerpt and raw_text:
                    excerpt_limit = 800
                    excerpt = raw_text[:excerpt_limit]
                    if len(raw_text) > excerpt_limit:
                        excerpt += "\n… (rapor için kısaltıldı; tam metin hash/uzunlukla kontrol edildi)"
                    indented = excerpt.replace("\n", "\n      ")
                    lines.append(f"   Ham mesaj örneği:\n      {indented}")
            except Exception as exc:  # noqa: BLE001 - bir kaynak diğerlerini engellemesin
                log.warning("Kaynak erişim testi başarısız (%s): %s: %s",
                            name, type(exc).__name__, exc)
                lines.append(
                    f"❌ {source_label} — geçmiş/ham mesaj kullanımı kontrol edilemedi"
                    f" ({type(exc).__name__}) · canlı event: {live_count}"
                )

        for value, exc in unresolved:
            lines.append(f"❌ {value} — kaynak çözülemedi ({type(exc).__name__})")
        lines += [
            "",
            f"• Geçmiş okuma: {readable}/{tested} · ham içerik kontrolü: {raw_checked}/{readable}"
            f" · kullanılabilir kuru deneme: {usable}/{readable}",
            f"• Hesaptan üye olunan: {joined}/{tested} · canlı event sayacı bot bu çalışmaya başladığından beri tutulur.",
            "ℹ️ Bu test kaynak mesajını göndermez/değiştirmez ve medya dosyasını indirmez; ham alanı,"
            " entity/link/medya metadatasını ve mesajın yerel hazırlanma yolunu kontrol eder.",
            "ℹ️ Canlı iletim bot çalışırken ve hesap kanala üyeyken yapılır; bot kapalıyken kaçan mesajlar otomatik geri alınmaz.",
        ]
        await control_reply(event, "\n".join(lines))

    async def handle_settings_command(event: events.NewMessage.Event,
                                      command: str, rest: str) -> None:
        """Üç izinli liste için taslak akışını yönet."""
        key = pending_key(event)
        pending = peek_pending(key)

        if command in CMD_SETTINGS_MENU:
            await control_reply(event, build_main_menu_text())
            return

        if command in CMD_SETTINGS_ADD or command in CMD_SETTINGS_REMOVE:
            if pending and pending.get("stage") == "confirm":
                await control_reply(event,
                    "📝 Önce bekleyen taslağı sonuçlandır.\n"
                    "✅ /kaydet ile kaydet veya ↩️ /iptal ile vazgeç."
                )
                return
            if rest.strip():
                await control_reply(event,
                    "Komutu tek başına gönder: /ekle veya /cikar.\n"
                    "Ardından listelerden birini seçip değeri ayrı mesaj olarak yaz."
                )
                return

            action = "add" if command in CMD_SETTINGS_ADD else "remove"
            set_pending(key, **new_telegram_edit(action, store.config))
            webtakip_count = _webtakip_count(store)
            await control_reply(event, build_list_category_prompt(action, store.config, webtakip_count))
            return

        if command in CMD_SETTINGS_SAVE:
            if pending is None:
                await control_reply(event, "ℹ️ Kaydedilecek bekleyen bir değişiklik yok. Başlamak için /ekle veya /cikar.")
            else:
                await save_pending_change(event, key)
            return

        if command in CMD_SETTINGS_REVERT:
            if pending is None:
                await control_reply(event, "ℹ️ İptal edilecek bekleyen bir işlem yok.")
            else:
                drop_pending(key)
                await control_reply(event, "↩️ İşlem iptal edildi. Taslak silindi; ayarlar ve GitHub değişmedi.")
            return

        return

    async def handle_pending_message(event: events.NewMessage.Event,
                                     raw: str) -> bool:
        """Liste menüsü/değer bekleniyorsa bu mesajı taslak akışına uygula."""
        key = pending_key(event)
        item = peek_pending(key)
        if item is None:
            return False

        text = raw.strip()
        if item.get("stage") == "dm_words":
            if not isinstance(event, PrivateControlEvent) or event.sender_id != SELF_ID:
                return False
            await save_dm_setting(event, action=item["action"], raw=text)
            return True
        if item.get("stage") == "filter":
            action = str(item.get("action") or "open")
            target = resolve_filter_target(text)
            if target is None:
                set_pending(key, **item)
                await control_reply(event, "\n".join([
                    "❌ Seçimi anlayamadım. 1, 2, 3 yazabilir ya da filtre adını yazabilirsin.",
                    "",
                    build_filter_toggle_prompt(action, store.config),
                ]))
                return True
            await apply_filter_toggle(event, action, target)
            return True

        if item.get("stage") == "category":
            field = resolve_telegram_list(text)
            if field is None:
                set_pending(key, **item)
                await control_reply(event, "❌ Seçimi anlayamadım. 1, 2, 3, 4, 5, 6 ya da 7 (webdetakip) yazabilir; listedeki adı da geçer.\n\n"
                                    + build_list_category_prompt(str(item.get("action")), store.config, _webtakip_count(store)))
                return True

            if field == WEBTAKIP_FIELD:
                if not isinstance(event, PrivateControlEvent) or event.sender_id != SELF_ID:
                    await control_reply(event, "🎯 Kişisel ayarlar yalnızca hesap sahibinin botla özel sohbetinden yönetilir.")
                    return True
                if tracker is None:
                    drop_pending(key)
                    await control_reply(event, "❌ Ürün takibi şu an kullanılamıyor (bildirim botu yapılandırması eksik).")
                    return True
                item["stage"] = "webdetakip"
                item["field"] = WEBTAKIP_FIELD
                set_pending(key, **item)
                if str(item.get("action")) == "remove":
                    await tracker.job("/webtakipler", event.chat_id, "")
                    await control_reply(event, "Çıkarılacak ürünün /webtakipler listesindeki numarasını gönder (ör. 3). İptal: /iptal")
                else:
                    await control_reply(event, "Akakçe ürün detay linkini gönder (…akakce.com/…-fiyati,ID.html). Hedef fiyatı bir sonraki mesajda istenecek. İptal: /iptal")
                return True

            if not await can_edit_list(event, field):
                return True
            config = item.get("draft_config") or store.config
            items = telegram_list_items(config, field)
            if item.get("action") == "remove" and not items:
                drop_pending(key)
                await control_reply(event, f"ℹ️ {TELEGRAM_LIST_META[field]['title']} listesi boş; çıkarılacak kayıt yok.")
                return True

            item["stage"] = "value"
            item["field"] = field
            set_pending(key, **item)
            await control_reply(event, build_list_value_prompt(str(item.get("action")), field, config))
            return True

        if item.get("stage") == "webdetakip":
            if tracker is None:
                drop_pending(key)
                await control_reply(event, "❌ Ürün takibi şu an çalışmıyor (bildirim botu yapılandırması eksik).")
                return True
            if not isinstance(event, PrivateControlEvent) or event.sender_id != SELF_ID:
                await control_reply(event, "🎯 Kişisel ayarlar yalnızca hesap sahibinin botla özel sohbetinden yönetilir.")
                return True
            action = str(item.get("action") or "add")
            ok = True
            if action == "add":
                if not text.startswith(("https://www.akakce.com/", "https://akakce.com/")):
                    await control_reply(event, "Akakçe ürün detay linki bekleniyor (…akakce.com/…-fiyati,ID.html). İptal: /iptal")
                    return True
                ok = await tracker.handle(event, text, existing_pending=False)
            else:
                try:
                    number = int(text.split()[0])
                except (ValueError, IndexError):
                    await control_reply(event, "Çıkarılacak ürünün /webtakipler listesindeki numarasını gönder (ör. 3). İptal: /iptal")
                    return True
                ok = await tracker.handle(event, f"/webtakipsil {number}", existing_pending=False)
            # product_tracker kendi kayıtlarını product_watches.json'da anında
            # kalıcı tutar; bu nedenle ayar taslağına ayrı bir /kaydet adımı yok.
            drop_pending(key)
            if not ok:
                await control_reply(event, "⚠️ İşlem anlaşılamadı; lütfen Akakçe linki veya geçerli bir ürün numarası gönder.")
            return True

        if item.get("stage") == "value":
            action = str(item.get("action") or "")
            field = str(item.get("field") or "")
            if not await can_edit_list(event, field):
                return True
            draft = item.get("draft_config") or store.config
            ok, candidate, values, message = stage_telegram_list_change(draft, field, action, text)
            if not ok:
                set_pending(key, **item)
                prompt = build_list_value_prompt(action, field, draft) if field in TELEGRAM_LIST_FIELDS else ""
                await control_reply(event, "\n".join(part for part in [message, prompt] if part))
                return True

            if action == "add" and field == "source_chats":
                resolved_values = []
                for value in values:
                    try:
                        peer_id, _, _ = await resolve_chat(client, value)
                        # Never persist a private invite token in config/Git history.
                        resolved_values.append(peer_id if isinstance(value, str) and _INVITE_LINK_RE.fullmatch(value.strip()) else value)
                    except Exception as exc:  # noqa: BLE001 - geçersiz kaynak taslağa alınmasın
                        set_pending(key, **item)
                        await control_reply(event, "\n".join([
                            f"❌ Kaynak çözülemedi ({type(exc).__name__}).",
                            "Hiçbir kayıt taslağa alınmadı. Daveti takip hesabıyla açıp önce katıl; "
                            "onay veya ücretli abonelik gerekiyorsa tamamlanmasını bekle.",
                            "Linkin geçerli ve hesabın erişiminin açık olduğunu kontrol et.",
                            "",
                            build_list_value_prompt(action, field, draft),
                        ]))
                        return True
                if resolved_values != values:
                    # Re-stage with IDs so duplicate handling and confirmation use
                    # exactly the values that will be saved, never the invite.
                    ok, candidate, values, message = stage_telegram_list_change(
                        draft, field, action, "\n".join(map(str, resolved_values)),
                    )
                    if not ok:
                        set_pending(key, **item)
                        await control_reply(event, message)
                        return True

            item["stage"] = "confirm"
            item["draft_config"] = candidate
            item["field"] = field
            item["value"] = values
            set_pending(key, **item)
            await control_reply(event, build_list_change_confirmation(action, field, values, candidate, message))
            return True

        if item.get("stage") == "confirm":
            choice = resolve_confirmation_choice(text)
            if choice == "save":
                await save_pending_change(event, key)
                return True
            if choice == "cancel":
                drop_pending(key)
                await control_reply(event, "↩️ İşlem iptal edildi. Taslak silindi; ayarlar ve GitHub değişmedi.")
                return True
            set_pending(key, **item)
            await control_reply(event, "📝 Taslak hazır. ✅ /kaydet (veya kaydet) ile kaydet; ↩️ /iptal (veya iptal) ile vazgeç.")
            return True

        drop_pending(key)
        return False

    # --- İletim yolları -----------------------------------------------------
    # Korumalı (noforwards) kanallarda forward ve copy patlar; o yüzden sırayla
    # denenir: forward -> copy -> medyayı indirip yeniden yükle -> sadece metin -> link.

    def source_of(event: events.NewMessage.Event) -> dict[str, Any] | None:
        """Kaynak kaydını bul (kaynak adı, kullanıcı adı, t.me linki için)."""
        return next((item for item in SOURCES if item["id"] == event.chat_id), None)

    def offer_link(event: events.NewMessage.Event) -> str | None:
        return build_message_link(event, source_of(event)) if MESSAGE_LINK_LINE else None

    async def send_forward(event: events.NewMessage.Event) -> Any:
        return await client.forward_messages(DESTINATION, event.message, from_peer=event.chat_id)

    async def send_copy(event: events.NewMessage.Event) -> Any:
        """Mesajı biçimiyle birlikte yeniden gönder.

        Gizli hyperlink'ler entity olarak korunur. Kaynaktaki ``reply_markup``
        kullanıcı hesabından gönderilemediği için (inline klavyeler yalnızca
        botlara açıktır) buton linkleri metne yazılır. Orijinal mesajın adresi
        en alttaki kaynak adının içine gömülür (ad yazılamazsa
        ``🔗 Mesajı Gör: <t.me>`` satırı geri gelir).
        """
        message = event.message
        media = getattr(message, "media", None)
        is_webpage = isinstance(media, types.MessageMediaWebPage)
        link = offer_link(event)
        source_record = source_of(event)
        footer_name = (source_record or {}).get("name") if SOURCE_FOOTER else None
        if media and not is_webpage:
            composed = compose_message(
                event, limit=CAPTION_LIMIT - 24, link_kinds=LINK_KINDS,
                message_link=link, source_name=footer_name,
            )
            return await client.send_file(
                DESTINATION,
                media,
                caption=composed["text"],
                formatting_entities=composed["entities"],
                force_document=False,
            )
        composed = compose_message(
            event, limit=MESSAGE_LIMIT - 100, link_kinds=LINK_KINDS,
            message_link=link, source_name=footer_name,
        )
        return await client.send_message(
            DESTINATION,
            composed["text"],
            formatting_entities=composed["entities"],
            link_preview=False,
        )

    async def send_media(event: events.NewMessage.Event) -> Any:
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
        # Aynı teslim içinde bildirim botu da aynı medyayı kullanacaksa
        # ikinci kez indirilmesin (bkz. notify_offer).
        _media_cache = MEDIA_DOWNLOAD_CACHE.get()
        if _media_cache is not None:
            try:
                _media_cache[(int(event.chat_id), int(event.id))] = data
            except (TypeError, ValueError):
                pass
        source_record = source_of(event)
        footer_name = (source_record or {}).get("name") if SOURCE_FOOTER else None
        composed = compose_message(
            event, limit=CAPTION_LIMIT - 24, link_kinds=LINK_KINDS,
            message_link=offer_link(event), source_name=footer_name,
        )
        return await client.send_file(
            DESTINATION,
            media_buffer(data, media_upload_name(message)),
            caption=composed["text"] or None,
            formatting_entities=composed["entities"],
            attributes=reupload_attributes(message),
            force_document=False,
        )

    async def send_text_only(event: events.NewMessage.Event) -> Any:
        # Gövdeye ek olarak gizli linkler (varsa) ve kaynak adına gömülü mesaj
        # adresi eklenir; ürün linki bir şekilde kaçsa bile tek dokunuşla fırsata
        # ulaşılır (ad yazılamazsa "🔗 Mesajı Gör" satırı geri gelir).
        source_record = source_of(event)
        footer_name = (source_record or {}).get("name") if SOURCE_FOOTER else None
        composed = compose_message(
            event, limit=MESSAGE_LIMIT - 400, link_kinds=LINK_KINDS,
            message_link=offer_link(event), source_name=footer_name,
        )
        if not composed_has_content(composed):
            raise ValueError("mesajda metin yok")
        has_media = bool(getattr(event.message, "media", None))
        note = "\n\n⚠️ Kaynak medyayı korumalı işaretlediği için medya iletilemedi." if has_media else ""
        return await client.send_message(
            DESTINATION,
            composed["text"] + note,
            formatting_entities=composed["entities"],
            link_preview=False,
        )

    async def send_link_card(event: events.NewMessage.Event) -> Any:
        """Son çare: kaynak adı + t.me bağlantısı. Ekranda görülebilir tek şey budur."""
        link = build_message_link(event, source_of(event))
        if not link:
            raise ValueError("bu sohbet türü için t.me bağlantısı üretilemiyor")
        source_record = source_of(event)
        footer_name = (source_record or {}).get("name") if SOURCE_FOOTER else None
        composed = compose_message(
            event, limit=2000, link_kinds=LINK_KINDS,
            message_link=link, source_name=footer_name,
            hidden_links=False,  # bu ileti biçimsiz gider; URL metinde görünmeli
        )
        if composed_has_content(composed):
            body = composed["text"]
        else:
            body = f"🔗 {STATS['last_match_source'] or 'kaynak'} kanalındaki mesaj:\n{link}"
            if composed["appendix"]:
                body += f"\n{composed['appendix']}"
            body += "\n(medya korumalı olduğu için iletilemedi, bağlantıdan açabilirsin)"
        return await client.send_message(DESTINATION, body[:MESSAGE_LIMIT], link_preview=False)

    SENDERS = {
        "forward": send_forward,
        "copy": send_copy,
        "media": send_media,
        "text": send_text_only,
        "link": send_link_card,
    }

    account_send_lock = asyncio.Lock()

    async def send_account_with_retry(mode: str, event: Any) -> Any:
        # Account fallback must not silently drop an offer on FloodWait either.
        # Keep other offer sends behind this wait rather than stampeding Telegram.
        async with account_send_lock:
            while True:
                try:
                    return await SENDERS[mode](event)
                except (errors.FloodWaitError, errors.SlowModeWaitError) as exc:
                    wait = max(1, exc.seconds) + 1
                    log.warning("Hesap gönderimi hız sınırı (%s): %s sn beklenip aynı fırsat yeniden denenecek.",
                                mode, wait)
                    await asyncio.sleep(wait)

    # --- Tekrar birleştirme --------------------------------------------------
    # Aynı başlık aynı anda 3-5 kanaldan gelebilir; gönderen rezervasyon koyar,
    # diğerleri bekleyip tekrara düşer. Rozet aynı ürünün kilidiyle birleştirilir;
    # kayıp hedefte bu olayın kendisi yeni bildirim gönderebilir.

    async def dedup_before_send(
        key: str, title: str, source_name: str | None = None,
        footer_name: str | None = None,
        offer: tuple[float | None, str] | None = None,
        event: Any = None,
        source_link: str | None = None,
    ) -> tuple[str, Any, str]:
        """Karar ver: ``("send", token, hedef_anahtar)`` yeni gönderim, ``("dup", None, hedef_anahtar)`` tekrar.

        Rezervasyon sahibi takılırsa bekleyen devralır; fırsatın kaybolmasındansa
        fazladan bir mesaj iyidir (fail-open). ``source_name``, tekrarlarda son
        bloğa yazılacak grup adı listesine eklenir. ``footer_name`` ilk
        bildirimin en altındaki kaynak grup adı satırıdır; tekrar yakalandığında
        bu satır kaldırılıp yerine çoklu paylaşım notu yazılır. ``source_link``
        o paylaşımın t.me adresidir: nota yazılan grup adı bu adrese gömülür
        (kullanıcı isteği — her grubun kendi mesajı).
        """
        token: Any = object()
        while True:
            async with DEDUP_LOCK:
                prune_dedup_cache(DEDUP_CACHE, time.time(), DEDUP_WINDOW_SECONDS, DEDUP_MAX_ENTRIES)
                active_key = (
                    key if key in DEDUP_CACHE
                    else (_find_matching_dedup_key(
                        DEDUP_CACHE, title,
                        product_asins(event) if event is not None else (),
                        product_link_ids(event) if event is not None else (),
                    ) or key)
                )
                entry = DEDUP_CACHE.get(active_key)
                if entry is None or entry.get("failed") or (
                    not entry.get("pending")
                    and better_or_different_offer(entry.get("offer"), offer)
                ):
                    target_key = active_key
                    previous = entry if entry and event is not None and (
                        is_referenced_multibuy_upgrade(entry, title, event, offer, time.time())
                        or is_referenced_product_price_drop(entry, title, event, offer, time.time())
                    ) else None
                    entry = new_dedup_entry(title, token, source_name, footer_name)
                    note_dedup_source(entry, source_name, source_link)
                    entry["owner"] = asyncio.current_task()
                    entry["offer"] = offer
                    entry["plain_offer"] = plain_offer_for_upgrade(event, offer) if event is not None else False
                    entry["product_asins"] = product_asins(event) if event is not None else set()
                    link_ids = product_link_ids(event) if event is not None else set()
                    if link_ids:
                        entry["product_link_ids"] = link_ids
                    if previous:
                        entry["upgrade_previous"] = previous
                        entry["count"] = int(previous.get("count", 1)) + 1
                        entry["sources"] = list(previous.get("sources") or [])
                        old_links = previous.get("links")
                        if isinstance(old_links, dict) and old_links:
                            entry["links"] = dict(old_links)
                        note_dedup_source(entry, source_name, source_link)
                    DEDUP_CACHE[target_key] = entry
                    return "send", token, target_key
                if not entry.get("pending"):
                    _keep_detailed_prefix_title(entry, title)
                    if event is not None:
                        entry.setdefault("product_asins", set()).update(product_asins(event))
                        link_ids = product_link_ids(event)
                        if link_ids:
                            entry.setdefault("product_link_ids", set()).update(link_ids)
                    entry["count"] += 1
                    entry["last_seen"] = time.time()
                    note_dedup_source(entry, source_name, source_link)
                    waiter_event = None
                else:
                    waiter_event = entry["ready"]
            if waiter_event is None:
                await dedup_apply_badge(active_key)
                # Deleted/wrong-ID messages must not swallow this occurrence.
                if entry.get("failed"):
                    continue
                return "dup", None, active_key
            try:
                await asyncio.wait_for(waiter_event.wait(), timeout=DEDUP_RESERVE_TIMEOUT)
            except TimeoutError:
                async with DEDUP_LOCK:
                    current = DEDUP_CACHE.get(active_key)
                    if current is entry and current.get("pending"):
                        owner = current.get("owner")
                        if owner is not None and not owner.done():
                            continue  # still sending / waiting for retry_after
                        log.warning("Tekrar rezervasyonu %ss'de bitmedi, devralınıyor (başlık: %.40s).",
                                    int(DEDUP_RESERVE_TIMEOUT), title)
                        current["failed"] = True
                        current["ready"].set()
                        token = object()
                        DEDUP_CACHE[active_key] = new_dedup_entry(
                            title, token, source_name, footer_name,
                        )
                        note_dedup_source(DEDUP_CACHE[active_key], source_name, source_link)
                        DEDUP_CACHE[active_key]["owner"] = asyncio.current_task()
                        DEDUP_CACHE[active_key]["offer"] = offer
                        DEDUP_CACHE[active_key]["plain_offer"] = plain_offer_for_upgrade(event, offer) if event is not None else False
                        return "send", token, active_key
                # State may have changed just as the timeout fired; re-read it.

    async def dedup_after_send(
        key: str, token: Any, success: bool, info: dict[str, Any] | None,
    ) -> None:
        """Rezervasyonu sonuçlandır: başarılıysa rozet bilgisini yaz, değilse düşür."""
        async with DEDUP_LOCK:
            entry = DEDUP_CACHE.get(key)
            if entry is None or entry.get("token") is not token:
                return  # devralınmış ya da budanmış; güncel kayda dokunma
            if not success or (NOTIFY_BOT_TOKEN and not (info or {}).get("bot")):
                entry["failed"] = True
                entry["ready"].set()
                if DEDUP_CACHE.get(key) is entry:
                    previous = entry.get("upgrade_previous")
                    if previous:
                        DEDUP_CACHE[key] = previous
                    else:
                        del DEDUP_CACHE[key]
                return
            info = info or {}
            bot_info = info.get("bot") or {}
            bot_id = bot_info.get("message_id")
            account_ids = list(info.get("account_ids") or [])
            if isinstance(bot_id, int) and not isinstance(bot_id, bool):
                entry["editable"] = "bot"
                entry["bot_message_id"] = bot_id
                entry["kind"] = "media" if bot_info.get("kind") == "media" else "text"
                entry["text"] = bot_info.get("text") or ""
                entry["entities_bot"] = list(bot_info.get("entities") or [])
                entry["keyboard"] = bot_info.get("keyboard")
            elif account_ids and not info.get("account_deleted"):
                entry["editable"] = "account"
                entry["account_message_id"] = account_ids[0]
                entry["text"] = info.get("account_text") or ""
                entry["entities_tl"] = list(info.get("account_entities") or [])
            else:
                # Rozet işlenemez (örn. bot ID'si alınamadı) ama tekrar
                # kaydı durur: sonraki kopyalar yine birleştirilir.
                entry["editable"] = None
            previous = entry.pop("upgrade_previous", None)
            if previous and not entry.get("editable"):
                # Unknown new message ID: never remove the known good old one.
                entry["count"] = 1
                entry["sources"] = [name for name in [entry.get("footer_name")] if name]
                previous = None
            entry["pending"] = False
            entry.pop("owner", None)
            entry["ready"].set()
        if previous:
            # Send first, then badge; only delete the old message after both
            # operations succeeded. A failure leaves two recoverable messages.
            await dedup_apply_badge(key)
            async with DEDUP_LOCK:
                can_delete = (DEDUP_CACHE.get(key) is entry
                              and entry.get("badge_count") == entry.get("count")
                              and not entry.get("failed"))
            if can_delete:
                async with previous["edit_lock"]:
                    try:
                        if previous.get("editable") == "bot" and DESTINATION_ID is not None:
                            deleted, detail = await delete_bot_message(
                                NOTIFY_BOT_TOKEN, DESTINATION_ID, previous["bot_message_id"])
                            if not deleted:
                                log.warning("Eski fırsat silinemedi: %s", detail)
                        elif previous.get("editable") == "account":
                            await delete_account_copy([previous["account_message_id"]], "indirimli fırsat")
                    except Exception as exc:
                        log.warning("Eski fırsat korunuyor: %s", type(exc).__name__)

    async def dedup_apply_badge(key: str) -> None:
        """İlk mesaja rozet işle; geçici hata akışı durdurmaz."""
        try:
            await _dedup_apply_badge(key)
        except Exception as exc:  # noqa: BLE001 - rozet görevi asla çökmemeli
            log.warning("Rozet görevi beklenmedik hatayla bitti: %s: %s", type(exc).__name__, exc)

    async def _dedup_apply_badge(key: str) -> None:
        async with DEDUP_LOCK:
            entry = DEDUP_CACHE.get(key)
            if entry is None or entry.get("pending") or not entry.get("editable"):
                return
            edit_lock = entry["edit_lock"]
        async with edit_lock:
            async with DEDUP_LOCK:
                if DEDUP_CACHE.get(key) is not entry or entry.get("pending") or not entry.get("editable"):
                    return
                if entry.get("badge_count") == entry.get("count"):
                    return
                count = int(entry.get("count", 0))
                editable = entry["editable"]
                kind = entry.get("kind", "text")
                current_text = entry.get("text") or ""
                bot_entities = list(entry.get("entities_bot") or [])
                tl_entities = list(entry.get("entities_tl") or [])
                keyboard = entry.get("keyboard")
                bot_message_id = entry.get("bot_message_id")
                account_message_id = entry.get("account_message_id")
                footer_name = (entry.get("footer_name") or "").strip()
                wait = DEDUP_EDIT_MIN_INTERVAL - (time.time() - float(entry.get("last_edit", 0.0)))
            if count < 2 or not current_text:
                return
            if wait > 0:
                await asyncio.sleep(wait)
            count = int(entry.get("count", count))
            # Eski not (varsa) çıkarılır; kaynak grup adı satırı KALDIRILIR ve
            # yerine çoklu paylaşım notu yazılıp mesaj kapatılır (kullanıcı
            # isteği: "hangi gruptan geldiğini kaldırıp N kere paylaşıldı şu şu
            # gruplar diye yazsın"). Grup adı bilinmiyorsa/sonda değilse not
            # eskisi gibi en sona eklenir.
            _, base, removed_badge = _strip_dedup_badge_details(current_text)
            badge_at_end = removed_badge != (0, 0, 0) \
                and removed_badge[1] == utf16_length(current_text) and removed_badge[2] == 0
            if footer_name and base.endswith(footer_name):
                # Rozet yokken ad kendi bloğuyla ("\n\n" + ad) durur; sondaki
                # rozet söküldüyse ayraç zaten rozetle birlikte gitmiştir
                # (ayracın kalan "\n" işareti adın hemen önünde durur).
                if removed_badge == (0, 0, 0):
                    cut = len(base) - len(footer_name) - 2 \
                        if base.endswith("\n\n" + footer_name) else -1
                elif badge_at_end:
                    cut = len(base) - len(footer_name)
                    if cut > 0 and base[cut - 1] != "\n":
                        cut = -1
                else:
                    cut = -1  # rozet ortada bir yerde: ad bloğuna dokunma
                if cut >= 0:
                    base = base[:cut]
                    removed_badge = (
                        utf16_length(current_text[:cut]), utf16_length(current_text), 0,
                    )
            if not base.strip():
                log.warning("Rozet gövdesi boş, güncelleme atlandı (başlık: %.40s).",
                            entry.get("title", ""))
                return
            names = list(entry.get("sources") or [])
            if not names:
                # Önbellek açılış taramasıyla kurulduysa adlar eski nottan okunur.
                names = dedup_badge_sources(current_text.splitlines()[-1] if current_text else "")
            # Kullanıcı isteği: nota yazılan HER grup adı kendi mesajına
            # bağlanır (grup adı adresin içine gömülür).
            name_links = dedup_badge_links(entry, names)
            badges = [
                dedup_badge(count, sources=names),
                # Yer daraldığında ilk küçültme adları KORUR (süsü atar): ad
                # giderse adın taşıdığı mesaj linki de giderdi. Adları tümden
                # atan kısa biçim yalnız hiçbir ad link taşımıyorsa denenir.
                dedup_badge(count, sources=names, compact=True),
            ]
            if not name_links:
                badges.append(dedup_badge(count, sources=names, short=True))
            tried: set[str] = set()
            for badge in badges:
                if not badge or badge in tried:
                    continue
                tried.add(badge)
                new_text, insert_at, insert_length, badge_offset = dedup_badge_insertion(base, badge)
                # Entity konumları UTF-16 birimidir; ekleme noktası da aynı birimle
                # verilmelidir. Karakter sayısıyla verildiğinde emojili mesajlarda
                # gövdenin sonundaki entity'ler (alıntı, ``👉 Ürün Linki 👈`` bağlantısı)
                # rozet kadar kayıyor, bağlantı etiketinden kopuyordu.
                insert_at, insert_length = (utf16_length(new_text[:insert_at]),
                                            utf16_length(new_text[insert_at:]))
                if editable == "bot":
                    limit = CAPTION_LIMIT if kind == "media" else MESSAGE_LIMIT
                    if len(new_text) > limit:
                        continue  # sığmadı, kısa rozeti dene
                    shifted = _rebase_entities_for_dedup_badge(
                        bot_entities, removed_badge, insert_at, insert_length, bot_api=True,
                    )
                    new_entities: list[dict[str, Any]] | None = sorted([
                        *shifted,
                        *dedup_badge_entities(badge, bot_api=True, offset=badge_offset,
                                              links=name_links),
                    ], key=lambda entity: int(entity.get("offset", 0)))
                    if DESTINATION_ID is None or not isinstance(bot_message_id, int):
                        break
                    if kind == "media":
                        ok, detail = await edit_bot_caption(
                            NOTIFY_BOT_TOKEN, DESTINATION_ID, bot_message_id, new_text,
                            entities=new_entities, keyboard=keyboard,
                        )
                    else:
                        ok, detail = await edit_bot_text(
                            NOTIFY_BOT_TOKEN, DESTINATION_ID, bot_message_id, new_text,
                            entities=new_entities, keyboard=keyboard, link_preview=False,
                        )
                    if ok:
                        async with DEDUP_LOCK:
                            live = DEDUP_CACHE.get(key)
                            if live is entry:
                                live["text"] = new_text
                                live["entities_bot"] = new_entities
                                live["last_edit"] = time.time()
                                live["edit_fails"] = 0
                                live["badge_count"] = count
                        STATS["dedup_edits"] += 1
                        log.info("Rozet güncellendi (%s paylaşım, %s farklı kaynak, bot iletisi %s).",
                                 count, len(entry.get("sources") or []), bot_message_id)
                        return
                    if "message to edit not found" in detail.lower() or "message can't be edited" in detail.lower():
                        entry["failed"] = True
                        entry["editable"] = None
                        log.warning("Eski bot iletisi düzenlenemiyor; fırsat yeni bildirim olarak gönderilecek.")
                        return
                    log.info("Rozet bot iletisine işlenemedi (%s); kısa rozet deneniyor.", detail)
                else:
                    if not isinstance(account_message_id, int):
                        break
                    shifted_tl = _rebase_entities_for_dedup_badge(
                        tl_entities, removed_badge, insert_at, insert_length,
                    )
                    new_tl = sorted([
                        *shifted_tl,
                        *dedup_badge_entities(badge, bot_api=False, offset=badge_offset,
                                              links=name_links),
                    ], key=lambda entity: int(getattr(entity, "offset", 0) or 0))
                    try:
                        await client.edit_message(
                            DESTINATION, account_message_id, new_text,
                            formatting_entities=new_tl, link_preview=False,
                        )
                    except errors.FloodWaitError as exc:
                        log.warning("Rozet FloodWait (%s sn); kısa rozet denenmeden bırakılıyor.", exc.seconds)
                        entry["last_edit"] = time.time() + exc.seconds
                        return
                    except (errors.MessageIdInvalidError, errors.MessageAuthorRequiredError):
                        entry["failed"] = True
                        entry["editable"] = None
                        return
                    except errors.MessageNotModifiedError:
                        async with DEDUP_LOCK:
                            live = DEDUP_CACHE.get(key)
                            if live is entry:
                                live["text"] = new_text
                                live["entities_tl"] = new_tl
                                live["last_edit"] = time.time()
                                live["badge_count"] = count
                        return
                    except Exception as exc:  # noqa: BLE001 - rozet hatası fırsatı öldürmez
                        log.info("Rozet hesap iletisine işlenemedi (%s: %s); kısa rozet deneniyor.",
                                 type(exc).__name__, exc)
                        continue
                    async with DEDUP_LOCK:
                        live = DEDUP_CACHE.get(key)
                        if live is entry:
                            live["text"] = new_text
                            live["entities_tl"] = new_tl
                            live["last_edit"] = time.time()
                            live["edit_fails"] = 0
                            live["badge_count"] = count
                    STATS["dedup_edits"] += 1
                    log.info("Rozet güncellendi (%s paylaşım, %s farklı kaynak, hesap iletisi %s).",
                             count, len(entry.get("sources") or []), account_message_id)
                    return
            # Buraya düşüldüyse rozet işlenemedi; iki üst üste hatada bu
            # başlık için düzenlemeyi bırak (birleştirme sürer).
            async with DEDUP_LOCK:
                live = DEDUP_CACHE.get(key)
                if live is entry:
                    live["edit_fails"] = int(live.get("edit_fails", 0)) + 1
                    if live["edit_fails"] >= 2:
                        live["editable"] = None
                        log.info("Rozet iki kez başarısız oldu; bu başlık rozetsiz birleşecek.")

    async def dedup_preload() -> None:
        """Açılışta hedefin son iletilerini okuyup önbelleği ısıt (tek tarama).

        Güvenle eşlenebilen geçmişte yeniden bildirimi önler. Normal gruptaki
        bot mesajlarının hesap ID'leri Bot API ID'si olarak kullanılamaz.
        İnsan sohbeti ASLA kayda alınmaz: yalnızca hesabın gönderdikleri ve
        bildirim biçimindeki (rozetli/"Mesajı Gör"lü) iletiler işlenir.
        Kayda alınmayan bir ileti en fazla fazladan bir bildirime yol açar;
        yanlış kayıt ise fırsat kaybettirirdi — temkinli taraf seçildi.
        """
        if not DEDUP_ENABLED or DEDUP_SCAN_LIMIT <= 0 or DESTINATION_ID is None:
            return
        try:
            recent: list[Any] = []
            async for message in client.iter_messages(DESTINATION, limit=DEDUP_SCAN_LIMIT):
                recent.append(message)
        except Exception as exc:  # noqa: BLE001 - tarama açılışı öldürmemeli
            log.warning("Tekrar önbelleği ısıtılamadı (%s: %s); boş başlanıyor.",
                        type(exc).__name__, exc)
            return
        if NOTIFY_BOT_TOKEN and not bot_history_ids_shared(DESTINATION_ID):
            # Temel grupta MTProto ve Bot API mesaj ID uzayları ayrıdır: eski
            # bot iletisi düzenlenemez ama kimliği (başlık+fiyat/koşul) yine de
            # güvenle kayda alınabilir. Kayıt editable=None olur; böylece
            # restart sonrası aynı fırsat yeniden bildirilmez, yanlış ID ile
            # düzenleme de denenmez. (P4)
            log.info("Normal grupta eski bot mesajları düzenlenemez; "
                     "yalnızca tekrar bastırma için kayda alınacak.")
        now = time.time()
        added = 0
        failed = 0
        first_error = ""
        for message in reversed(recent):  # eskiden yeniye: ilk kopya kanonik olur
            try:
                text = getattr(message, "message", None) or ""
                if not isinstance(text, str) or not text.strip():
                    continue
                sender = getattr(message, "sender_id", None)
                if sender is None:
                    sender = getattr(getattr(message, "sender", None), "id", None)
                badge_count, base = strip_dedup_badge(text)
                looks_like_deal = (
                    badge_count > 1
                    or MESSAGE_LINK_LABEL in text
                    or PRODUCT_LINK_TEXT in text    # yeni düzen: 👉 Ürün Linki 👈
                    or PRODUCT_LINK_LABEL in text  # eski biçim
                    or any(is_price_line(line)      # eski sabit düzen (💰Fiyat: …)
                           for line in text.splitlines())
                )
                if sender == SELF_ID:
                    if NOTIFY_BOT_TOKEN:
                        continue  # account-only history is NOT a delivered notification
                    editable: str | None = "account"
                elif looks_like_deal:
                    if NOTIFY_BOT_TOKEN:
                        bot_id = NOTIFY_BOT_TOKEN.split(":", 1)[0]
                        if str(sender) != bot_id:
                            continue  # another bot/human is not editable by this bot
                        # Temel grupta ID uzayları ayrı: kayıt bastırma amaçlı
                        # tutulur (editable=None), bot ID'si düzenleme için
                        # KESİNLİKLE kullanılmaz.
                        editable = "bot" if bot_history_ids_shared(DESTINATION_ID) else None
                    else:
                        editable = None
                else:
                    continue  # insan sohbeti: kayda alma
                title = _search_query(base) or message_title(base)
                if SMART_HEADER:
                    first_line = next((ln.strip() for ln in base.splitlines() if ln.strip()), "")
                    if first_line.endswith("…"):
                        continue  # truncated history header is not a safe product identity
                    history_price, history_title = split_header_line(first_line)
                    if history_price:
                        # Üst satır "fiyat - ürün adı" (ya da eski "💰 …" satırı).
                        # Ad yoksa (salt fiyat/❓) güvenli ürün kimliği yoktur:
                        # canlı yol da başlıksız ileti için anahtar kurmaz. Body'den
                        # rastgele satır toplamak yanlış anahtara yol açabilir.
                        if not history_title:
                            continue
                        title = _campaign_title_if_clipped(
                            base, history_title, MESSAGE_LIMIT, include_code=True,
                            allow_code_only_marker=True,
                        ) or history_title
                    elif first_line:
                        title = _campaign_title_if_clipped(
                            base, first_line, MESSAGE_LIMIT, include_code=True,
                            allow_code_only_marker=True,
                        ) or first_line   # başlığın tek başına üstte olduğu düzen
                terms = offer_terms(message, prefer_header=True)
                key = dedup_offer_key(title, terms)
                if not key:
                    continue
                seen_at = now
                sent_date = getattr(message, "date", None)
                if sent_date is not None:
                    try:
                        seen_at = float(sent_date.timestamp()) if hasattr(sent_date, "timestamp") \
                            else float(sent_date)
                    except (TypeError, ValueError):
                        seen_at = now
                if now - seen_at > DEDUP_WINDOW_SECONDS:
                    continue
                message_id = getattr(message, "id", None)
                if not isinstance(message_id, int) or isinstance(message_id, bool):
                    continue
                media = getattr(message, "media", None)
                is_media = bool(media) and not isinstance(media, types.MessageMediaWebPage)
                raw_entities = list(getattr(message, "entities", None) or [])
                async with DEDUP_LOCK:
                    target_key = (
                        key if key in DEDUP_CACHE
                        else (_find_matching_dedup_key(DEDUP_CACHE, title) or key)
                    )
                    existing = DEDUP_CACHE.get(target_key)
                    if existing is None or better_or_different_offer(existing.get("offer"), terms):
                        # A newer/lower-price notification supersedes the old one;
                        # do not restore the expensive message after a restart.
                        record = new_dedup_entry(title, object(), preload=True)
                        record["pending"] = False
                        record["failed"] = False
                        record["ready"].set()
                        record["count"] = max(1, badge_count)
                        # Tekrarda kaldırılacak kaynak grup adı satırı (varsa).
                        record["footer_name"] = dedup_footer_name(base)
                        # Bildirimdeki her Telegram bağlantısı: grup adına gömülü
                        # adresler (yeni düzen) ve eski ``Mesajı Gör`` satırı.
                        link_map = dedup_source_links(text, raw_entities)
                        if link_map:
                            record["link_map"] = link_map
                        if badge_count > 1:
                            names = dedup_badge_sources(text.splitlines()[-1] if text else "")
                            if names:
                                record["sources"] = names
                        record["first_seen"] = seen_at
                        record["last_seen"] = seen_at
                        record["editable"] = editable
                        record["kind"] = "media" if is_media else "text"
                        record["text"] = text
                        record["offer"] = terms
                        record["entities_tl"] = raw_entities
                        if editable == "bot":
                            record["bot_message_id"] = message_id
                            record["entities_bot"] = [
                                converted for entity in raw_entities
                                if (converted := bot_api_entity(entity)) is not None
                            ]
                        elif editable == "account":
                            record["account_message_id"] = message_id
                        DEDUP_CACHE[target_key] = record
                        added += 1
                    elif not existing.get("pending"):
                        if badge_count > 1:
                            existing["count"] = max(int(existing.get("count", 1)), badge_count)
                        else:
                            existing["count"] = int(existing.get("count", 1)) + 1
                        existing["last_seen"] = max(float(existing.get("last_seen", seen_at)), seen_at)
            except Exception as exc:  # noqa: BLE001 - tek ileti taramayı durdurmaz
                failed += 1
                first_error = first_error or f"{type(exc).__name__}: {exc}"
                log.debug("Önbelleğe alınamayan ileti: %s: %s", type(exc).__name__, exc)
        if failed:
            # İletiler tek tek debug düzeyinde kalır; toplamı görünür olmalı: buradaki bir
            # kod hatası taramanın bir dalını sessizce devre dışı bırakabilir.
            log.warning("Tekrar önbelleği: %d/%d ileti işlenemedi (ilk hata: %s).",
                        failed, len(recent), first_error)
        if added:
            log.info("Tekrar önbelleği ısıtıldı: %d başlık (son %d ileti tarandı).",
                     added, len(recent))

    async def notify_offer(
        event: events.NewMessage.Event, source_name: str,
    ) -> tuple[bool, dict[str, Any]]:
        """Bildirim botuyla fırsatın kopyasını at; (gönderildi_mi, bilgi) döner.

        Tasarım (basit düzen): en üstte ÜST SATIR — ``smart_header`` açıksa
        ``₺56 — ürün adı`` (fiyat en başta; fiyat yoksa ``❓ — ürün adı``),
        kapalıysa ürün başlığı →
        altında mesaj OLDUĞU GİBİ → gizli link ekleri (varsa) →
        "🔗 Mesajı Gör: <t.me linki>" → en altta kaynak grup adı
        (etiketsiz, linksiz, yalnızca kalın).
        Tekrarlı paylaşımlarda kaynak grup adı satırı kaldırılır ve yerine
        çoklu paylaşım notu yazılır (bkz. ``_dedup_apply_badge``).

        Arama düğmeleri (Google Alışveriş / Akakçe / Cimri) kaynak mesajdan
        ayıklanan FİYATSIZ adla kurulur; üst satıra yazılan fiyat arama
        sorgusuna taşınmaz. Metin gönderiminde Telegram bağlantı önizlemesi
        kapalıdır; ürün bağlantısı ve tıklanabilirliği korunur, mesajın altında
        ikinci bir Amazon ürün kartı oluşmaz.

        Dönen ilk değer, tek mesaj modunda hesap kopyasının silinip
        silinmeyeceğini belirler (bkz. delete_account_copy). İkinci değer,
        tekrar birleştirmenin rozet işleyebilmesi için gönderilen bot
        mesajının kimlik/bilgi sözlüğüdür (boş olabilir).
        """
        no_info: dict[str, Any] = {}
        if not NOTIFY_BOT_TOKEN or DESTINATION_ID is None:
            return False, no_info, ""
        message_link = offer_link(event)
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
                event, limit=CAPTION_LIMIT - 24, link_kinds=BOT_LINK_KINDS,
                message_link=message_link, source_name=footer_name, has_keyboard=True,
                source_name_url=message_link,
            )
            entities = bot_api_entities(composed["message"], composed["content"]) + source_name_entity(composed)
            keyboard = build_inline_keyboard(event, body_text=composed["text"], body_entities=entities)
            t_media = time.perf_counter()
            # Yedek zincirde (send_media) aynı teslim içinde indirildiyse
            # yeniden indirme; önbellek yalnızca bu deliver() bağlamında yaşar.
            data = None
            _media_cache = MEDIA_DOWNLOAD_CACHE.get()
            if _media_cache is not None:
                try:
                    data = _media_cache.get((int(event.chat_id), int(event.id)))
                except (TypeError, ValueError):
                    data = None
            if data is None:
                try:
                    data = await client.download_media(event.message, bytes)
                except Exception as exc:  # noqa: BLE001 - medya inmezse bildirim yine gitsin
                    log.warning("Bildirim medyası indirilemedi (%s): %s", type(exc).__name__, exc)
                    data = None
            t_downloaded = time.perf_counter()
            if data:
                log.info("⏱ Bildirim medyası: %s, %.1f MB, indirme %.1f sn.",
                         descriptor["kind"], len(data) / (1024 * 1024), t_downloaded - t_media)
                MEDIA_TIMING.set({"kind": descriptor["kind"], "mb": len(data) / (1024 * 1024),
                                  "download": t_downloaded - t_media, "upload": 0.0})
                send_result = await send_bot_media(
                    NOTIFY_BOT_TOKEN, DESTINATION_ID,
                    kind=descriptor["kind"], filename=descriptor["filename"],
                    mime_type=descriptor["mime"], data=data,
                    caption=composed["text"], entities=entities, keyboard=keyboard,
                )
                ok, detail = send_result
                if ok:
                    upload_seconds = time.perf_counter() - t_downloaded
                    log.info("Bildirim gönderildi (medya: %s, kaynak: %s, yükleme %.1f sn).",
                             descriptor["kind"], source_name, upload_seconds)
                    timing = MEDIA_TIMING.get()
                    if timing:
                        timing["upload"] = upload_seconds
                    return True, {
                        "message_id": getattr(send_result, "message_id", None),
                        "kind": "media",
                        "text": composed["text"],
                        "entities": entities,
                        "keyboard": keyboard,
                    }, ""
                log.warning("Bildirim medyası gönderilemedi (%s) → metne düşülüyor.", detail)

        composed = compose_message(
            event, limit=MESSAGE_LIMIT - 200, link_kinds=BOT_LINK_KINDS,
            message_link=message_link, source_name=footer_name, has_keyboard=True,
            source_name_url=message_link,
        )
        entities = bot_api_entities(composed["message"], composed["content"]) + source_name_entity(composed)
        text = composed["text"] or f"🔔 Yeni fırsat – {source_name}"
        keyboard = build_inline_keyboard(event, body_text=text, body_entities=entities)
        send_result = await send_bot_ping(
            NOTIFY_BOT_TOKEN, DESTINATION_ID, text,
            entities=entities, keyboard=keyboard, link_preview=False,
        )
        ok, detail = send_result
        if ok:
            log.info("Bildirim gönderildi (metin, kaynak: %s).", source_name)
            return True, {
                "message_id": getattr(send_result, "message_id", None),
                "kind": "text",
                "text": text,
                "entities": entities,
                "keyboard": keyboard,
            }, ""
        log.warning("Bildirim gönderilemedi: %s", detail)
        return False, no_info, str(detail)

    async def notify_offer_with_retry(
        event: events.NewMessage.Event, source_name: str,
    ) -> tuple[bool, dict[str, Any], str]:
        """Explicit gateway failures are retryable; ambiguous network errors are not replayed."""
        retry_delays = (0.5, 2.0)
        for attempt in range(len(retry_delays) + 1):
            sent, info, detail = await notify_offer(event, source_name)
            if sent:
                return True, info, ""
            # Telegram's own request_with_retry already handles 429. A definite
            # 502/503/504 can be retried without retrying timeouts/connection
            # failures whose first send may have been accepted.
            retryable = any(f"HTTP {status}:" in detail for status in (502, 503, 504))
            if not retryable or attempt >= len(retry_delays):
                return False, info, detail
            delay = retry_delays[attempt]
            log.warning("Bildirim geçici HTTP ağ geçidi hatası; aynı bildirim %.1f sn sonra yeniden denenecek (%d/%d).",
                        delay, attempt + 1, len(retry_delays))
            await asyncio.sleep(delay)
        return False, {}, "yeniden deneme sınırı"

    async def deliver(
        event: events.NewMessage.Event, source_name: str,
    ) -> tuple[bool, str, dict[str, Any]]:
        """Sırayla iletim yollarını dene; ilk başarılı olanı kullan.

        Üçüncü değer, tekrar birleştirmenin ilk mesaja rozet işleyebilmesi
        için gönderim bilgisidir: ``bot`` (bot mesajı sözlüğü ya da ``None``),
        ``account_ids`` (hesabın gönderdiği mesaj ID'leri),
        ``account_deleted`` (tek mesaj modunda silinip silinmediği),
        ``account_text``/``account_entities`` (hesap iletisinin metni/biçimi).
        """
        last_error = "denenmedi"
        # P5: bu teslim boyunca geçerli medya aktarım bağlamı (kalıcı değil).
        MEDIA_DOWNLOAD_CACHE.set({})
        t_begin = time.perf_counter()
        try:
            received_lag = max(0.0, time.time() - event.message.date.timestamp())
        except Exception:  # noqa: BLE001 - tarih yoksa ölçüm atlanır
            received_lag = -1.0
        skip_bot = False

        def note_notification_failure(detail: str = "") -> None:
            if NOTIFY_BOT_TOKEN and DESTINATION_ID is not None:
                STATS["notification_failed"] = int(STATS.get("notification_failed", 0) or 0) + 1
                log.error("Hedef içeriği iletilmiş olsa da telefon bildirimi başarısız (kaynak=%s, mesaj=%s, neden=%s); toplam=%d.",
                          source_name, getattr(event, "id", "?"), detail or "bilinmeyen hata",
                          STATS["notification_failed"])

        if FAST_NOTIFY and NOTIFY_BOT_TOKEN and DESTINATION_ID is not None:
            # Hızlı yol: önce bildirim botu. Başarılıysa hesap kopyası hiç atılmaz
            # (silinecek bir şey kalmaz); başarısızsa normal zincir aynen çalışır.
            bot_sent, bot_info, bot_error = await notify_offer_with_retry(event, source_name)
            if bot_sent:
                STATS["forwarded"] += 1
                STATS["modes"]["bot"] = STATS["modes"].get("bot", 0) + 1
                info = {
                    "mode": "bot", "bot": bot_info, "account_ids": [], "account_deleted": False,
                    "account_text": None, "account_entities": None,
                }
                try:
                    enqueue_private_offer(event, info)
                except Exception as exc:
                    log.warning("Özel kopya kuyruğa alınamadı; grup korunuyor: %s", type(exc).__name__)
                log.info("⏱ %s | telegram→bot %.1f sn | bildirim (hızlı yol) %.1f sn",
                         source_name, received_lag, time.perf_counter() - t_begin)
                maybe_report_slow(client, source_name, received_lag, [("Bildirim (hızlı yol)", time.perf_counter() - t_begin)])
                return True, "bot", info
            note_notification_failure(bot_error)
            skip_bot = True
            log.info("Hızlı bildirim gönderilemedi → normal iletim zincirine dönülüyor (kaynak: %s).", source_name)
        sanitized = sanitize_message(event)
        modes = DELIVERY_CHAIN
        product_title = _search_query(sanitized)
        if sanitized.changed or product_title:
            # Forward özgün iletiyi değiştirmeden taşır; temizleme gereken veya
            # üstüne ürün başlığı eklenecek iletilerde kopyalama zincirine geç
            # ki hem çıktı düzeni (başlık en üstte) korunsun hem de yasaklı
            # içerik kaçmasın. Zincirde yalnızca forward varsa yine forward
            # denenir: hiç iletmemektense başlıksız iletmek iyidir.
            filtered = [mode for mode in DELIVERY_CHAIN if mode != "forward"]
            modes = filtered or list(DELIVERY_CHAIN)
            reason = "temizleme" if sanitized.changed else "ürün başlığı"
            log.info("Mesaj %s gerektiriyor; 'forward' atlanacak (kaynak: %s).", reason, source_name)
        for mode in modes:
            try:
                result = await send_account_with_retry(mode, event)
            except Exception as exc:  # noqa: BLE001 - bir yol patlarsa sıradakini dene
                last_error = f"{type(exc).__name__}: {exc}"
                log.info("İletim yolu '%s' başarısız (%s) → sıradaki deneniyor.", mode, last_error)
                continue
            STATS["forwarded"] += 1
            STATS["modes"][mode] = STATS["modes"].get(mode, 0) + 1
            if mode not in ("forward", "copy"):
                log.info("Mesaj '%s' yedeğiyle iletildi (kaynak: %s).", mode, source_name)
            account_ids = sent_message_ids(result)
            t_account = time.perf_counter()
            # Tek mesaj modu: bildirim botu gönderdiyse hesabın attığı kopyayı sil.
            if skip_bot:
                bot_sent, bot_info = False, {}
            else:
                bot_sent, bot_info, bot_error = await notify_offer_with_retry(event, source_name)
                if not bot_sent:
                    note_notification_failure(bot_error)
            t_notified = time.perf_counter()
            account_deleted = False
            if bot_sent and SINGLE_MESSAGE:
                account_deleted = await delete_account_copy(account_ids, source_name)
            log.info("⏱ %s | telegram→bot %.1f sn | hesap kopyası %.1f sn | bildirim %.1f sn | silme %.1f sn | toplam %.1f sn",
                     source_name, received_lag, t_account - t_begin, t_notified - t_account,
                     time.perf_counter() - t_notified, time.perf_counter() - t_begin)
            maybe_report_slow(client, source_name, received_lag, [
                ("Hesap kopyası", t_account - t_begin), ("Bildirim", t_notified - t_account),
                ("Silme", time.perf_counter() - t_notified),
            ])
            info = {
                "mode": mode,
                "bot": bot_info if bot_sent else None,
                "account_ids": account_ids,
                "account_deleted": account_deleted,
                "account_text": first_sent_text(result),
                "account_entities": first_sent_entities(result),
            }
            try:
                enqueue_private_offer(event, info)
            except Exception as exc:
                log.warning("Özel kopya kuyruğa alınamadı; grup korunuyor: %s", type(exc).__name__)
            return True, mode, info

        STATS["failed"] += 1
        log.error("Hiçbir iletim yolu çalışmadı (kaynak=%s, mesaj=%s). Son hata: %s",
                  source_name, getattr(event, "id", "?"), last_error)
        return False, last_error, {}

    # Tekrar önbelleğini sıfırla ve hedefin son iletileriyle ısıt: yeniden
    # başlamalarda yalnızca güvenle eşlenebilen geçmişi kullan (tek tarama).
    DEDUP_CACHE.clear()
    LIVE_SEEN.clear()
    POLL_DISPATCHED.clear()
    SOURCE_IN_FLIGHT.clear()
    await dedup_preload()
    # Cursor: RAM sadece, bilinçli /restart’ta tek persist
    cursor_load_from_config(store.config)
    # İlk kurulum: source_cursors yoksa bootstrap ilk poll’de top ile yapılır (patlama yok)
    if not store.config.get("source_cursors"):
        log.info("Cursor: kalıcı kayıt yok — ilk poll’de sınırlı geriye tarama yapılacak; başlangıçtan önceki geçmiş bastırılacak.")

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
                "Yetki için hesap sahibinden admin_user_id değerini config.json'da güncellemesini iste."
            )
            return

        # Owner-only optional feature. Existing config drafts always retain priority.
        if tracker and isinstance(event, PrivateControlEvent) and event.sender_id == SELF_ID:
            handled = await tracker.handle(event, raw, existing_pending=peek_pending(pending_key(event)) is not None)
            if handled:
                pending = peek_pending(pending_key(event))
                if pending and pending.get("stage") == "webdetakip":
                    # Ürün takibi kendi dosyasını anında günceller; /ekle
                    # taslağına devam edilmemesi için kapatılır.
                    drop_pending(pending_key(event))
                    if command in {"/iptal", "/webtakipiptal"}:
                        await control_reply(event, "↩️ Ürün takip taslağı iptal edildi; ayar düzenleme taslağı da kapatıldı.")
                    elif command == COMMAND_SETTINGS_SAVE:
                        await control_reply(event, "ℹ️ Ürün takip kayıtları product_watches.json'da; ayar düzenleme taslağı kapatıldı. Takip menüsü: /webtakip")
                    else:
                        await control_reply(event, "ℹ️ Ürün takip işlemi işlendi; ayar düzenleme taslağı kapatıldı. Takip menüsü: /webtakip")
                return

        # Komut değil: çok adımlı akışta beklenen değer olabilir.
        if not command:
            await handle_pending_message(event, raw)
            return

        STATS["commands"] += 1
        # Bekleyen liste düzenleme taslağını yalnızca /kaydet veya /iptal kapatır;
        # /durum gibi başka komutlar taslağı sessizce düşürmez.
        rest = raw[len(raw.split()[0]):].strip()
        # `/analiz350` → `/analiz 350`: bitişik yazılan mesaj sayısı.
        shorthand = re.fullmatch(re.escape(COMMAND_ANALYZE) + r"(\d+)", command)
        if shorthand:
            rest = f"{shorthand.group(1)} {rest}".strip()
            command = COMMAND_ANALYZE
        log.info("Komut alındı: %s (chat=%s, sender=%s)", command, event.chat_id, event.sender_id)

        # Sabit komutlar önce: alan değerleriyle çakışabilecek komutları çöz.
        if command == COMMAND_START:
            await control_reply(event, "👋 Komutlarını artık burada verebilirsin. Fırsatlar aynı hedef gruba gider.\n"
                                "Grup ayarları: /ayar · Durum: /durum · Tüm komutlar: /komutlar\n"
                                "Kişisel fırsatlar: /dmfiltre · /ekle /cikar → dmdahil / dmharic\n"
                                "Özel sohbet geçmişi silinmez. Kayıtlı Mesajlar yedek kontrol olarak açık.")
        elif command in CMD_DM_COMMANDS:
            await handle_dm_command(event, command, rest)
        elif command == COMMAND_STATUS:
            text = build_status_text(store.config, connected=client.is_connected())
            dm_on = config_flag(store.config.get("dm_enabled"), False)
            text += f"\n• 📩 Kişisel bildirim filtresi: {'AÇIK' if dm_on else 'KAPALI'}"
            if tracker is not None:
                wt_on = bool(tracker.state.get("enabled", False))
                text += f"\n• 🌐 Web ürün takibi: {'AÇIK' if wt_on else 'KAPALI'}"
            if isinstance(event, PrivateControlEvent):
                text += "\n\n" + dm_status()
            await control_reply(event, text)
        elif command == COMMAND_TEST:
            text = (
                f"🧪 Deneme mesajı – {timefmt.local().strftime('%Y-%m-%d %H:%M:%S')} TSİ\n"
                f"Hedef: {DESTINATION_LABEL}\n"
                f"Kaynaklar: {len(SOURCE_IDS)} | Görülen: {STATS['seen']} | Eşleşen: {STATS['matched']}"
            )
            try:
                await client.send_message(DESTINATION, text)
                reply = f"✅ Deneme mesajı gönderildi: {DESTINATION_LABEL}"
            except Exception as exc:  # noqa: BLE001
                await control_reply(event, f"❌ Deneme mesajı gönderilemedi: {type(exc).__name__}: {exc}")
                return
            if NOTIFY_BOT_TOKEN and DESTINATION_ID is not None:
                ok, detail = await send_bot_ping(
                    NOTIFY_BOT_TOKEN, DESTINATION_ID,
                    "🔔 Bildirim denemesi\n\n(Bildirimlerde mesajın kopyası + '🔗 Mesajı Gör' linki gelir.)",
                )
                reply += "\n" + ("🔔 Bot bildirimi de gönderildi (telefonuna düşmeli)." if ok
                                 else f"⚠️ Bot bildirimi gönderilemedi: {detail}")
                reply += ("\nGizli linkler ve buton linkleri de bildirime eklenir; "
                          "medya varsa bot onu da gönderir.")
            else:
                reply += ("\n⚠️ NOTIFY_BOT_TOKEN yok: mesajı kendi hesabın gönderdiği için "
                          "bildirim almazsın. BotFather'dan bot oluşturup gruba ekle ve token'ı "
                          "GitHub Actions secret'ı olarak tanımla.")
            reply += "\n\n" + await private_channel_health()
            await control_reply(event, reply)
        elif command == COMMAND_SOURCES:
            await control_reply(event, build_source_text())
        elif command in CMD_SOURCE_TEST:
            await test_source_access(event, rest)
        elif command == COMMAND_ID:
            if isinstance(event, PrivateControlEvent):
                await control_reply(event,
                    f"🆔 Özel sohbet / kullanıcı ID'n: {event.sender_id}\n"
                    f"🎯 Fırsat hedefi değişmedi: {DESTINATION_LABEL}\n"
                    "Bu özel sohbet ID'sini destination olarak yazmana gerek yok."
                )
                return
            try:
                chat = await event.get_chat()
                chat_kind = type(chat).__name__
            except Exception:  # noqa: BLE001 - sohbet cache'te olmayabilir
                chat_kind = "bilinmiyor"
            await control_reply(event,
                f"🆔 Bu sohbetin ID'si: {event.chat_id}\n"
                f"• Tür: {chat_kind}\n"
                f"• Senin kullanıcı ID'n: {event.sender_id}\n"
                f"config.json için:\n"
                f'  "control_chat": {event.chat_id},\n'
                f'  "admin_user_id": {event.sender_id}'
            )
        elif command == COMMAND_RESTART:
            # Yalnızca bilinçli /restart’ta cursor yedeği — otomatik restart değil
            global RESTARTING
            RESTARTING = True
            # 1) Uçuş drain — yeni mesajlar RESTARTING bayrağıyla zaten bekletiliyor
            drain_deadline = time.time() + 6
            while time.time() < drain_deadline and (SOURCE_IN_FLIGHT or poll_delivery_tasks):
                await asyncio.sleep(0.2)
            if SOURCE_IN_FLIGHT or poll_delivery_tasks:
                log.warning("Restart drain %ss doldu — %s uçuş + %s poll görevi kaldı; mevcut ardışık cursor ile yedek alınıyor.",
                            6, len(SOURCE_IN_FLIGHT), len(poll_delivery_tasks))
            # 2) Tek toplu persist (mevcut atomic_write_json + GIT_WRITE_LOCK yolunu kullan)
            ok_save, save_note = await cursor_persist_for_restart(store)
            if not ok_save:
                RESTARTING = False
                await control_reply(event, "✗ Restart iptal — cursor kalıcılaştırılamadı: " + save_note)
                return
            # 3) Dispatch yalnızca persist başarıyla bittiyse
            ok, message = await dispatch_next_run(gh_pat)
            if ok:
                await control_reply(event, "🔄 " + message + "\n" + save_note)
            else:
                RESTARTING = False
                await control_reply(event, "⚠️ " + message + "\n" + save_note + "\nCursor yedeği alındı ama yeni çalışma başlatılamadı; mevcut süreç çalışmaya devam ediyor.")
        elif command == COMMAND_HELP:
            await control_reply(event, HELP_TEXT)
        elif command == COMMAND_WEB_TRACK:
            await control_reply(event, "Ürün takip menüsü için bildirim botunun özel sohbetinde /webtakip yaz.")
        elif command in CMD_ANALYZE:
            await analyze_history(event, rest)
        elif command in CMD_TG_FILTER_OPEN:
            await handle_filter_command(event, "open", rest)
        elif command in CMD_TG_FILTER_CLOSE:
            await handle_filter_command(event, "close", rest)
        elif command in SETTINGS_COMMANDS:
            await handle_settings_command(event, command, rest)
        else:
            await control_reply(event, f"Bilinmeyen komut: {raw}\n\n{HELP_TEXT}")

    @client.on(events.NewMessage())
    async def on_new_message(event: events.NewMessage.Event) -> None:
        if event.chat_id not in SOURCE_IDS or event.chat_id in CONTROL_IDS:
            return
        if DESTINATION_ID is not None and event.chat_id == DESTINATION_ID:
            return  # hedefe kendi gönderdiğimiz mesajı tekrar iletmeyelim
        if RESTARTING:
            return  # bilinçli restart drain — mesaj cursor ile kurtarılır
        if live_event_should_skip(event):
            return  # aynı mesaj hızlı taramayla zaten işlendi
        # cursor ilk katman — ID bazlı tekrar engeli (yalnızca RAM, persist’siz)
        # Yeni grup & ilk poll öncesi race: RAM’de yoksa bootstrap (patlama yok)
        try:
            _cid = int(event.chat_id); _mid = int(event.id)
            if _cid not in CURSOR_RAM:
                # İlk live update mesajı tek başına güvenli bir başlangıç noktası
                # değildir: update'ler eşzamanlı işlenirken daha eski ID önce
                # gelebilir. Poll, sınırlı/yaş filtreli bootstrap'ı belirleyecek.
                CURSOR_RAM[_cid] = {"cursor": 0, "pending": set(), "completed": set(),
                                    "failed": set(), "failed_at": {}, "initialized": False}
                log.info("Yeni kaynak canlı olayla görüldü; güvenli cursor poll'de belirlenecek: %s", _cid)
            _cur = int(CURSOR_RAM.get(_cid, {}).get("cursor", 0) or 0)
            if _mid <= _cur:
                return
        except Exception:
            pass
        key = (event.chat_id, event.id)
        SOURCE_IN_FLIGHT.add(key)
        _cursor_mark_pending(int(event.chat_id), int(event.id))
        try:
            await process_source_event(event)
        except BaseException:
            LIVE_SEEN.pop(key, None)
            POLL_DISPATCHED.pop(key, None)
            _cursor_mark_failed(int(event.chat_id), int(event.id))
            raise
        finally:
            SOURCE_IN_FLIGHT.discard(key)

    async def process_source_event(event: Any) -> None:
        STATS["seen"] += 1
        source_seen = STATS.setdefault("source_seen", {})
        if isinstance(source_seen, dict):
            source_seen[event.chat_id] = int(source_seen.get(event.chat_id, 0) or 0) + 1
        text = event.raw_text or ""
        # Exact, content-free teaser only. Any media, URL, hidden link, or button may
        # carry a real deal; fail open rather than suppressing it. Kaynak bu ifadeyi
        # "Yoğun ilgi görüyor 📣" diye yazar: emoji/noktalama karşılaştırmaya girmez.
        # Kelimeler birebir bu üçü olduğundan biçim entity'si (kalın, özel emoji)
        # içerik ekleyemez; bağlantı taşıyan entity'yi ``extract_links`` yakalar.
        msg = _as_message(event)
        teaser = (WORD_TOKEN_RE.findall(normalize(text)) == ["yoğun", "ilgi", "görüyor"]
                  and getattr(msg, "media", None) is None
                  and not getattr(msg, "reply_markup", None)
                  and not extract_links(event))
        if teaser or is_blank_reply(event):
            log.info("İçeriksiz mesaj elendi (chat=%s id=%s): ürün, fiyat ya da bağlantı yok.",
                     event.chat_id, event.id)
            _cursor_mark_success(int(event.chat_id), int(event.id))
            return
        if not matches(text, FILTER_INCLUDE, FILTER_EXCLUDE, FILTER_MODE,
                       include_enabled=FILTER_INCLUDE_ENABLED,
                       exclude_enabled=FILTER_EXCLUDE_ENABLED):
            log.debug("Eşleşmedi (chat=%s id=%s): %.80s", event.chat_id, event.id, text)
            _cursor_mark_success(int(event.chat_id), int(event.id))
            return

        STATS["matched"] += 1
        STATS["last_match"] = time.time()
        source_name = next((item["name"] for item in SOURCES if item["id"] == event.chat_id), str(event.chat_id))
        STATS["last_match_source"] = source_name
        log.info("Eşleşti: %s / mesaj %s / %.80s", source_name, event.id, text)

        # Tekrar birleştirme: aynı ürün sorgusu pencere içindeyse gruba yeni mesaj
        # ATILMAZ; ilk mesaja rozet işlenir. Kayıp hedefte yeni bildirim gönderilir.
        # Ürün sorgusu aynı temizlenmiş/gönderilecek içerikten ayıklanır.
        # Product identity must not vary with the optional display-header setting.
        dedup_title = smart_dedup_title(event)
        terms = offer_terms(event) if DEDUP_ENABLED else (None, "")
        dedup_id = dedup_offer_key(dedup_title, terms) if DEDUP_ENABLED else None
        if not dedup_id:
            ok, _, _ = await deliver(event, source_name)
            if not ok:
                LIVE_SEEN.pop((event.chat_id, event.id), None)
                POLL_DISPATCHED.pop((event.chat_id, event.id), None)
                _cursor_mark_failed(int(event.chat_id), int(event.id))
            else:
                _cursor_mark_success(int(event.chat_id), int(event.id))
            return
        decision, token, effective_dedup_id = await dedup_before_send(
            dedup_id, dedup_title, source_name,
            footer_name=source_name if SOURCE_FOOTER else None,
            offer=terms, event=event,
            source_link=build_message_link(
                event, next((item for item in SOURCES if item["id"] == event.chat_id), None)),
        )
        if decision == "dup":
            STATS["deduped"] += 1
            log.info("Tekrar birleştirildi (kaynak=%s): %.60s", source_name, dedup_title)
            _cursor_mark_success(int(event.chat_id), int(event.id))
            return
        ok, info = False, None
        try:
            ok, _, info = await deliver(event, source_name)
        finally:
            await dedup_after_send(effective_dedup_id, token, ok, info)
            if not ok:
                LIVE_SEEN.pop((event.chat_id, event.id), None)
                POLL_DISPATCHED.pop((event.chat_id, event.id), None)
                _cursor_mark_failed(int(event.chat_id), int(event.id))
            else:
                _cursor_mark_success(int(event.chat_id), int(event.id))

    async def private_status_changed(status: str, detail: str) -> None:
        """Rutin açılışı sessiz tut; gerçek özel komut arızalarını bildirmeyi sürdür."""
        if status == "running":
            log.info("Özel komutlar hazır: %s", detail)
            return
        if status == "recovered":
            text = "✅ Özel komut kanalı toparlandı; botun özel sohbetinden komut verebilirsin."
        elif status == "webhook":
            text = ("⚠️ Özel komutlar ÇALIŞMIYOR: bildirim botunda bir webhook tanımlı; "
                    "getUpdates ile çelişiyor. Webhook'u kaldırıp takipçiyi yeniden başlat "
                    "(webhook otomatik silinmez). Komutlar şimdilik Kayıtlı Mesajlar'dan işlenir.")
        elif status == "unauthorized":
            text = ("⚠️ Özel komutlar ÇALIŞMIYOR: bot token'ı geçersiz (401). "
                    "NOTIFY_BOT_TOKEN secret'ını BotFather'dan kontrol edip güncelle. "
                    "Komutlar şimdilik Kayıtlı Mesajlar'dan işlenir.")
        elif status == "conflict":
            text = ("⚠️ Özel komutlar ÇALIŞMIYOR: aynı bot token'ıyla ikinci bir getUpdates "
                    f"tüketicisi var ({detail}). Eski bir çalışma/VM/başka bir proje aynı botu "
                    "kullanıyor olabilir; onu kapat. Bu arada komutlar Kayıtlı Mesajlar'dan işlenir.")
        else:
            return
        try:
            await client.send_message(DESTINATION, text)
        except Exception as exc:  # noqa: BLE001
            log.warning("Özel komut durum uyarısı gruba gönderilemedi: %s", type(exc).__name__)

    poll_delivery_tasks: set[asyncio.Task] = set()

    async def fast_poll_loop() -> None:
        """Kaynakları tek istekle (getPeerDialogs) yoklar; yeni mesajı canlı olayla aynı yoldan işler."""
        input_peers: dict[int, Any] = {}
        failures = 0
        poll_interval = FAST_POLL_SECONDS
        POLL_TELEMETRY["interval"] = poll_interval
        log.info("⚡ Hızlı tarama açık: kaynaklar %.0f sn'de bir yoklanacak.", FAST_POLL_SECONDS)

        delivery_slots = asyncio.Semaphore(4)

        async def run_event(event: Any) -> None:
            try:
                async with delivery_slots:
                    await on_new_message(event)
            except Exception as exc:  # noqa: BLE001 - tek mesaj hatası taramayı durdurmasın
                log.warning("Hızlı tarama mesajı işlenemedi (%s): %s", type(exc).__name__, exc)

        source_retry_log_at: dict[tuple[int, str], float] = {}
        source_not_before: dict[int, float] = {}

        def defer_source_after_flood(chat_id: int, exc: BaseException, stage: str) -> None:
            wait = max(1, int(getattr(exc, "seconds", 1) or 1)) + 1
            source_not_before[int(chat_id)] = max(
                source_not_before.get(int(chat_id), 0.0), time.monotonic() + wait,
            )
            log.warning("Kaynak taraması FloodWait (%s, kaynak=%s): %s sn ertelendi; diğer kaynaklar sürüyor.",
                        stage, chat_id, wait)

        def should_log_source_retry(chat_id: int, stage: str) -> bool:
            key = (int(chat_id), stage)
            now = time.monotonic()
            if now - source_retry_log_at.get(key, 0.0) >= 300:
                source_retry_log_at[key] = now
                return True
            return False

        while True:
            try:
                await asyncio.sleep(max(FAST_POLL_SECONDS, poll_interval))
                if not FAST_POLL:
                    continue
                poll_started = time.monotonic()
                targets: list[tuple[int, Any]] = []
                for chat_id in sorted(SOURCE_IDS - CONTROL_IDS):
                    if source_not_before.get(chat_id, 0.0) > time.monotonic():
                        continue
                    peer = input_peers.get(chat_id)
                    if peer is None:
                        try:
                            peer = await client.get_input_entity(chat_id)
                        except errors.FloodWaitError as exc:
                            defer_source_after_flood(chat_id, exc, "entity")
                            continue
                        except Exception as exc:  # noqa: BLE001 - source isolated; retry next cycle
                            if should_log_source_retry(chat_id, "entity"):
                                log.warning("Hızlı taramada kaynak çözümlenemedi (kaynak=%s): %s: %s; sonraki turda yeniden denenecek.",
                                            chat_id, type(exc).__name__, exc)
                            continue
                        input_peers[chat_id] = peer
                    targets.append((chat_id, peer))
                if not targets:
                    POLL_TELEMETRY["last_elapsed"] = time.monotonic() - poll_started
                    continue
                dialog_started = time.monotonic()
                result = await client(functions.messages.GetPeerDialogsRequest(
                    peers=[types.InputDialogPeer(peer) for _, peer in targets],
                ))
                dialog_elapsed = time.monotonic() - dialog_started
                POLL_TELEMETRY["last_dialog_elapsed"] = dialog_elapsed
                if RESTARTING:
                    POLL_TELEMETRY["last_elapsed"] = time.monotonic() - poll_started
                    await asyncio.sleep(0.5)
                    continue
                tops = {utils.get_peer_id(d.peer): int(d.top_message or 0) for d in result.dialogs}
                events_by_source: dict[int, list[Any]] = {}
                for chat_id, peer in targets:
                    if RESTARTING:
                        break
                    top = tops.get(chat_id)
                    if top is None:
                        if should_log_source_retry(chat_id, "dialog"):
                            log.warning("Hızlı tarama yanıtında kaynak diyaloğu yok (kaynak=%s); sonraki turda yeniden denenecek.", chat_id)
                        continue
                    cur_entry = CURSOR_RAM.get(chat_id)
                    initializing = cur_entry is None or not cur_entry.get("initialized", True)
                    if initializing:
                        # Cursor yoksa/toparlanmış kaynaksa sınırlı bir start-up
                        # pencere tara. Eski geçmişi patlatma; son 30 dk ve en
                        # fazla 100 ileti. Geçmiş sorgusu hata verirse cursor'ı
                        # ilerletme, sonraki turda güvenle tekrar dene.
                        try:
                            messages = await client.get_messages(
                                peer, limit=FAST_POLL_BACKFILL_LIMIT, max_id=int(top) + 1,
                            )
                        except errors.FloodWaitError as exc:
                            defer_source_after_flood(chat_id, exc, "bootstrap history")
                            continue
                        except Exception as exc:  # noqa: BLE001 - retry without changing cursor
                            if should_log_source_retry(chat_id, "bootstrap-history"):
                                log.warning("Başlangıç geçmişi çekilemedi (kaynak=%s): %s: %s; sonraki turda yeniden denenecek.",
                                            chat_id, type(exc).__name__, exc)
                            continue
                        cutoff = time.time() - FAST_POLL_BACKFILL_MAX_AGE
                        recent = []
                        for message in messages:
                            try:
                                stamp = message.date.timestamp()
                            except Exception:  # noqa: BLE001 - malformed/missing date is not safe to replay
                                continue
                            if stamp >= cutoff:
                                recent.append(message)
                        recent.sort(key=lambda message: int(message.id))
                        st = _cursor_ensure(chat_id)
                        known_ids = set(st["pending"]) | set(st["failed"]) | set(st["completed"])
                        known_ids = {int(mid) for mid in known_ids if int(mid) > 0}
                        if not recent:
                            st["cursor"] = min(int(top), min(known_ids) - 1) if known_ids else int(top)
                            st["initialized"] = True
                            _cursor_advance(chat_id)
                            log.info("Yeni kaynak için cursor bootstrap: %s → %s (son %d ileti içinde son %.0f dk'da ileti yok).",
                                     chat_id, st["cursor"], FAST_POLL_BACKFILL_LIMIT, FAST_POLL_BACKFILL_MAX_AGE / 60)
                            continue
                        baseline_ids = [int(recent[0].id), *known_ids]
                        st["cursor"] = max(0, min(baseline_ids) - 1)
                        st["initialized"] = True
                        # Mark stale messages that fall *inside* the recent ID
                        # range as intentionally skipped; otherwise a later
                        # min_id poll could rediscover and deliver them.
                        recent_ids = {int(message.id) for message in recent}
                        for stale_message in messages:
                            stale_id = int(getattr(stale_message, "id", 0) or 0)
                            if stale_id > st["cursor"] and stale_id not in recent_ids:
                                _cursor_mark_success(chat_id, stale_id)
                        messages = recent
                        log.info("Yeni kaynak için sınırlı kurtarma: %s → cursor=%s, %d ileti (son %.0f dk).",
                                 chat_id, st["cursor"], len(messages), FAST_POLL_BACKFILL_MAX_AGE / 60)
                        cur = int(st["cursor"])
                    else:
                        cur = int(cur_entry.get("cursor", 0) or 0)
                        if top <= cur:
                            continue
                        try:
                            # oldest-first, capped batch: a noisy source cannot
                            # monopolize this pass; remaining IDs stay beyond cursor.
                            messages = await client.get_messages(
                                peer, min_id=cur, limit=FAST_POLL_BATCH_LIMIT, reverse=True,
                            )
                        except errors.FloodWaitError as exc:
                            defer_source_after_flood(chat_id, exc, "history")
                            continue
                        except Exception as exc:  # noqa: BLE001 - don't move cursor; retry next cycle
                            if should_log_source_retry(chat_id, "history"):
                                log.warning("Geçmiş çekilemedi (kaynak=%s, cursor=%s): %s: %s; sonraki turda yeniden denenecek.",
                                            chat_id, cur, type(exc).__name__, exc)
                            continue
                        messages = sorted(messages, key=lambda message: int(getattr(message, "id", 0) or 0))
                    # Poll-confirmed leading ID gaps are deleted messages; do not
                    # skip an ID currently being delivered or awaiting retry.
                    fetched_ids = {int(getattr(m, "id", 0) or 0) for m in messages if getattr(m, "id", None)}
                    if fetched_ids:
                        max_fetched = max(fetched_ids)
                        st = _cursor_ensure(chat_id)
                        for gid in range(int(st["cursor"]) + 1, max_fetched + 1):
                            if gid in fetched_ids:
                                break
                            if gid in st["pending"] or gid in st["failed"]:
                                break
                            st["cursor"] = gid
                        _cursor_advance(chat_id)
                        cur = int(st["cursor"])
                    for message in messages:
                        mid = int(getattr(message, "id", 0) or 0)
                        if mid <= cur or (chat_id, mid) in LIVE_SEEN:
                            continue
                        _remember(POLL_DISPATCHED, chat_id, mid)
                        event = events.NewMessage.Event(message)
                        try:
                            event._set_client(client)
                        except Exception:  # noqa: BLE001 - attach client even without entity cache
                            event._client = client
                        event._fast_poll = True
                        log.info("⚡ Hızlı tarama yeni mesajı yakaladı (kaynak=%s, mesaj=%s).", chat_id, mid)
                        events_by_source.setdefault(chat_id, []).append(event)
                # Interleave dispatch by source: a backlog in the first channel
                # must not put every other source behind it in the delivery lock.
                if not RESTARTING:
                    source_order = sorted(events_by_source)
                    while any(events_by_source.values()):
                        for chat_id in source_order:
                            pending_events = events_by_source[chat_id]
                            if not pending_events:
                                continue
                            task = asyncio.create_task(run_event(pending_events.pop(0)))
                            poll_delivery_tasks.add(task)
                            task.add_done_callback(poll_delivery_tasks.discard)
                # Measure the whole scan pass (entity resolution, dialog request,
                # and all per-source history fetches), not just GetPeerDialogs.
                poll_elapsed = time.monotonic() - poll_started
                POLL_TELEMETRY["last_elapsed"] = poll_elapsed
                if poll_elapsed >= max(2.0, FAST_POLL_SECONDS):
                    poll_interval = next_poll_interval(poll_interval, poll_elapsed)
                    POLL_TELEMETRY["interval"] = poll_interval
                    log.info("Tam hızlı tarama turu %.1f sn sürdü (diyalog isteği %.1f sn); aralık %.0f sn'ye çıkarıldı.",
                             poll_elapsed, dialog_elapsed, poll_interval)
                else:
                    recovered = recover_poll_interval(poll_interval, FAST_POLL_SECONDS)
                    if recovered != poll_interval:
                        poll_interval = recovered
                        POLL_TELEMETRY["interval"] = poll_interval
                failures = 0
            except asyncio.CancelledError:
                raise
            except errors.FloodWaitError as exc:
                wait = max(1, int(getattr(exc, "seconds", 5))) + 1
                poll_interval = next_poll_interval(poll_interval, wait)
                POLL_TELEMETRY["interval"] = poll_interval
                POLL_TELEMETRY["flood_count"] += 1
                POLL_TELEMETRY["last_flood_wait"] = float(wait)
                log.warning("Hızlı tarama FloodWait: %s sn bekleniyor; yeni yoklama aralığı %.0f sn. "
                            "Canlı mesaj dinleme devam ediyor. [bu oturumda tarama FloodWait=%d]",
                            wait, poll_interval, int(POLL_TELEMETRY["flood_count"]))
                await asyncio.sleep(wait)
            except Exception as exc:  # noqa: BLE001 - geçici hata; canlı olaylar çalışmaya devam eder
                failures += 1
                if failures in {1, 5, 20}:
                    log.warning("Hızlı tarama hatası (%s): %s", type(exc).__name__, exc)
                await asyncio.sleep(min(30, 2 * failures))

    # Özel komutlar ve fırsat kuyruğu ayrı görevlerde çalışır; açılış mesajı gönderilmez.
    private_tasks = []
    if tracker:
        private_tasks.append(asyncio.create_task(tracker.run()))
    if private_api:
        private_tasks.append(asyncio.create_task(private_offers.run()))
        if config_flag(store.config.get("private_control"), False):
            private_tasks.append(asyncio.create_task(poll_private_commands(
                private_api, lambda: ADMIN_IDS | {SELF_ID}, on_control_message,
                private_status_changed,
            )))
            await asyncio.sleep(0)  # poller görevi ilk adımını atsın
            for _ in range(40):  # poller başlangıcına en fazla ~10 sn tanı
                if poll_health()["status"] not in {"off", "starting"}:
                    break
                await asyncio.sleep(0.25)

    async def prepare_scheduled_restart() -> bool:
        global RESTARTING
        RESTARTING = True
        drain_deadline = time.time() + 6
        while time.time() < drain_deadline and (SOURCE_IN_FLIGHT or poll_delivery_tasks):
            await asyncio.sleep(0.2)
        if SOURCE_IN_FLIGHT or poll_delivery_tasks:
            log.warning("Otomatik restart drain %ss doldu — %s uçuş + %s poll görevi kaldı; güvenli cursor kaydedilip yenileme sürüyor.",
                        6, len(SOURCE_IN_FLIGHT), len(poll_delivery_tasks))
        ok_save, save_note = await cursor_persist_for_restart(store)
        if not ok_save:
            RESTARTING = False
            log.error("Otomatik restart cursor kaydı başarısız; mevcut süreç dinlemeye devam edecek: %s", save_note)
            return False
        log.info("Otomatik restart cursor'ı kaydedildi: %s", save_note)
        return True

    async def resume_after_scheduled_restart_failure() -> None:
        global RESTARTING
        RESTARTING = False
        log.warning("Yeni çalışma başlatılamadı; mevcut süreç kaynakları dinlemeye devam edecek.")

    if auto_restart and gh_pat:
        asyncio.create_task(auto_restart_scheduler(
            client, DESTINATION, gh_pat, max(60, restart_minutes * 60),
            prepare_restart=prepare_scheduled_restart,
            resume_after_failure=resume_after_scheduled_restart_failure,
        ))
    elif auto_restart:
        log.warning("auto_restart açık ama GH_PAT yok; oturum Actions süresi bitince kapanacak.")
    asyncio.create_task(heartbeat())

    if FAST_POLL:
        private_tasks.append(asyncio.create_task(fast_poll_loop()))

    log.info("Dinleniyor... (kaynak=%d, kontrol=%s, hedef=%s)", len(SOURCE_IDS), sorted(CONTROL_IDS), DESTINATION_LABEL)
    try:
        await client.run_until_disconnected()
    finally:
        shutdown_tasks = [*private_tasks, *poll_delivery_tasks]
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
