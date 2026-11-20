"""Sohbet çözümleme: kaynak/kontrol/hedef kanalları ID'ye çevirme ve gruplar."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Iterable
from typing import Any

from telethon import errors, TelegramClient, utils
from telethon.tl import functions, types

import appstate
from confutil import chat_values, parse_chat_value
from textutil import display_name, humanize

log = logging.getLogger("telegram-filter")


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
