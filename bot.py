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
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Sequence

from telethon import TelegramClient, errors, events, utils
from telethon.sessions import StringSession

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
    return config


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


async def send_bot_ping(token: str, chat_id: int | str, text: str) -> tuple[bool, str]:
    """Bot API üzerinden kısa bir bildirim mesajı atar.

    Takipçi *kendi hesabınla* gönderdiği için Telegram o mesajları senin kendi
    mesajın sayar ve bildirim üretmez. Bildirim isteyenler @BotFather'dan bir bot
    oluşturup hedef gruba ekler; bu fonksiyon o bot adına kısa bir "ping" atar.
    """
    if not token:
        return False, "notify_bot_token tanımlı değil."
    payload = json.dumps({"chat_id": chat_id, "text": text[:500]}).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with await asyncio.to_thread(urllib.request.urlopen, request, timeout=15) as response:
            body = json.loads(response.read().decode("utf-8", "replace") or "{}")
        if body.get("ok"):
            return True, "bildirim gönderildi"
        return False, f"Bot API ok=false: {body.get('description')}"
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        log.error("Bildirim bot'u hata verdi: HTTP %s %s", exc.code, detail)
        return False, f"HTTP {exc.code}: {detail}"
    except Exception as exc:  # noqa: BLE001 - bildirim başarısızlığı akışı durdurmaz
        log.warning("Bildirim gönderilemedi: %s", type(exc).__name__)
        return False, f"{type(exc).__name__}"


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


def build_message_link(event: Any, source: dict | None) -> str | None:
    """Mesajın t.me bağlantısını kur; kurulamıyorsa None döner."""
    message_id = getattr(event, "id", None)
    if not message_id:
        return None
    username = (source or {}).get("username")
    if username:
        return f"https://t.me/{username}/{message_id}"
    chat_id = getattr(event, "chat_id", None)
    # t.me/c/<id>/<mesaj> yalnızca kanal/süpergrup için çalışır (-100... ile başlar).
    if isinstance(chat_id, int) and chat_id < -1000000000000:
        return f"https://t.me/c/{abs(chat_id) - 1000000000000}/{message_id}"
    return None


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

    async def send_forward(event: events.NewMessage.Event) -> None:
        await client.forward_messages(destination, event.message, from_peer=event.chat_id)

    async def send_copy(event: events.NewMessage.Event) -> None:
        await client.send_message(destination, event.message)

    async def send_media(event: events.NewMessage.Event) -> None:
        """Medyayı indirip hedefe SIFIRDAN yükle (forward kısıtını atlar)."""
        message = event.message
        if not getattr(message, "media", None):
            raise ValueError("mesajda medya yok")
        size = getattr(getattr(message, "file", None), "size", None) or 0
        if MAX_MEDIA_MB and size and size > MAX_MEDIA_MB * 1024 * 1024:
            raise ValueError(f"medya {size // (1024 * 1024)} MB, sınır {MAX_MEDIA_MB} MB")
        data = await client.download_media(message, bytes)
        if not data:
            raise ValueError("medya indirilemedi")
        caption = (event.raw_text or "")[:1000] or None
        await client.send_file(destination, data, caption=caption)

    async def send_text_only(event: events.NewMessage.Event) -> None:
        text = (event.raw_text or "").strip()
        if not text:
            raise ValueError("mesajda metin yok")
        has_media = bool(getattr(event.message, "media", None))
        note = "\n\n⚠️ Kaynak medyayı korumalı işaretlediği için medya iletilemedi." if has_media else ""
        await client.send_message(destination, f"{text[:3800]}{note}")

    async def send_link_card(event: events.NewMessage.Event) -> None:
        """Son çare: kaynak adı + t.me bağlantısı. Ekranda görülebilir tek şey budur."""
        link = build_message_link(event, next((s for s in SOURCES if s["id"] == event.chat_id), None))
        if not link:
            raise ValueError("bu sohbet türü için t.me bağlantısı üretilemiyor")
        text = (event.raw_text or "").strip()
        body = f"🔗 {STATS['last_match_source'] or 'kaynak'} kanalındaki mesaj:\n{link}"
        if text:
            body = f"{text[:1500]}\n\n🔗 Kaynak: {link}"
        else:
            body += "\n(medya korumalı olduğu için iletilemedi, bağlantıdan açabilirsin)"
        await client.send_message(destination, body, link_preview=True)

    SENDERS = {
        "forward": send_forward,
        "copy": send_copy,
        "media": send_media,
        "text": send_text_only,
        "link": send_link_card,
    }

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
            if NOTIFY_BOT_TOKEN and DESTINATION_ID is not None:
                ok, detail = await send_bot_ping(
                    NOTIFY_BOT_TOKEN, DESTINATION_ID,
                    f"🔔 Yeni fırsat ({source_name}) – {normalize(event.raw_text or '')[:120]}",
                )
                if not ok:
                    log.warning("Bildirim ping'i gönderilemedi: %s", detail)
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
                ok, detail = await send_bot_ping(NOTIFY_BOT_TOKEN, DESTINATION_ID, "🔔 Bildirim denemesi")
                reply += "\n" + ("🔔 Bot bildirimi de gönderildi (telefonuna düşmeli)." if ok
                                 else f"⚠️ Bot bildirimi gönderilemedi: {detail}")
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
