"""config.json okuma ve değer ayrıştırma yardımcıları (saf, ağ kullanmaz)."""

from __future__ import annotations

import logging
import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

log = logging.getLogger("telegram-filter")


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
