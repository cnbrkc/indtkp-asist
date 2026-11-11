"""Altın referans: fiyatlı örnek mesajların BUGÜNKÜ çıktıları (davranış sabitleme).

Amaç: davranışı koruması gereken bir düzenlemede tek bir mesajın bile fiyatı,
başlığı, bildirim metni, biçimi (entity), düğmeleri ya da tekrar kimliği
değişirse bunu göstermek. Burada "doğrusu budur" iddiası YOKTUR; kayıt bugünkü
davranışın kendisidir (bilinen kusurlar dâhil). Fark çıkarsa: dur ve farkı incele.

Girdi   : fixtures/golden/corpus.json — test dosyalarındaki fiyat içeren 563
          farklı dize sabiti (hasat kuralı: en az 12 karakter ve TL/TRY/₺/lira
          geçen her sabit). Dondurulmuştur; özeti CORPUS_SHA256 ile sabitlenir.
Beklenen: fixtures/golden/expected.json — her girdi için aşağıdaki katmanlar.

Katmanlar
  price   : price_filter'ın saf çıktıları (adaylar, seçilen fiyat, başlık).
  prod    : canlı config.json'daki düzenle (smart_header + fast_notify açık)
            main() içinden GERÇEK akış. Bot API'ye giden istek ağ sınırında
            (urllib.request.urlopen) yakalanır. Her mesaj ikinci kez de gelir
            ("again": 📌 tekrar notunun işlenmesi).
  botdown : aynı düzen, Bot API reddediyor → kullanıcı hesabıyla yedek iletim.
  classic : iki bayrak kapalı (kodun varsayılanı): hesap kopyası + bildirim +
            kopyanın silinmesi. Üretimde kullanılmadığı için 4 kayıtta bir.
  preload : prod bildirimleri hedef geçmişi olarak geri okunur (açılış taraması;
            bildirim metni aynı zamanda kalıcı biçimdir).

Her mesaj iki biçimde denenir: "plain" (sabit olduğu gibi, fotoğraflı) ve "rich"
(aynı metne belirlenimci biçim, düğme ve temizlenecek ekler giydirilmiş, metin
olarak). 16 kayıtta bir "long" (sınırı aşan fotoğraflı ileti: kırpma) eklenir.

Okuma kuralı: bir hücrede "=" değeri "botun bu hücrede gönderdiği ilk istekteki
(bot[0]) karşılığıyla aynı" demektir; {"=": n, "+": kalan} ise "ilk n karakteri
(ya da öğesi) onunla aynı, sonrası şu". Yinelenen metinler böylece bir kez yazılır.

Beklenen dosyası testler çalışırken ASLA yazılmaz; yalnızca iki adımla değişir:

    python tests/test_golden_reference.py --review          # farklar + onay kodu; yazmaz
    python tests/test_golden_reference.py --regenerate KOD  # yalnızca o kodla yazar

--review bugünkü kodun çıktısını dosyadakiyle karşılaştırır (katman özeti, fark
türleri, değişen kayıtlar; --full ile her farkın tamamı) ve o fark kümesine özgü
bir onay kodu verir. --regenerate kodsuz ya da başka bir fark kümesinin koduyla
yazmayı reddeder: yazılan, incelenenle aynı olmak zorundadır.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import copy
import dataclasses
import hashlib
import io
import json
import logging
import os
import re
import sys
import tempfile
import textwrap
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Awaitable, Callable
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bot  # noqa: E402
import price_filter  # noqa: E402
from telethon.tl import types  # noqa: E402
from test_integration import (  # noqa: E402
    ADMIN_ID, BASE_ENV, GROUP_ID, FakeButton, FakeClient, FakeEvent, FakeMarkup, FakeRow,
    reset_state,
)

GOLDEN_DIR = Path(__file__).resolve().parent / "fixtures" / "golden"
CORPUS_PATH = GOLDEN_DIR / "corpus.json"
EXPECTED_PATH = GOLDEN_DIR / "expected.json"
CORPUS_COUNT = 563
CORPUS_SHA256 = "e7865854b4f331c42b0f024d9175d8d6f4f4a79c104dfe8fb49141730e385e37"

BOT_TOKEN = "123:GOLDEN"          # bot kimliği 123: açılış taraması göndereni bununla tanır
BOT_MESSAGE_ID = 9001             # sahte Bot API her gönderime bu kimliği verir
EVENT_ID = 7                      # her örnek aynı kaynak mesaj kimliğiyle gelir

# Canlı config.json OKUNMAZ (bot onu çalışırken değiştirir). Aşağıdakiler oradaki
# ayarların dondurulmuş kopyasıdır. Bilinçli farklar: grup kelime filtreleri
# kapalıdır ki her örnek teslim aşamasına ulaşsın (filtre kararını matches()
# testleri korur); arka plan işleri (hızlı tarama, özel komut kanalı, otomatik
# yenileme) ve süreye bağlı "yavaş bildirim" raporu kapalıdır.
BASE_CONFIG: dict[str, Any] = {
    "source_chats": ["@firsatz"],
    "destination": GROUP_ID,
    "control_chat": GROUP_ID,
    "admin_user_id": ADMIN_ID,
    "include_keywords": [], "exclude_keywords": [],
    "include_enabled": False, "exclude_enabled": False,
    "match_mode": "any", "copy_mode": "copy",
    "delivery_modes": ["forward", "copy", "media", "text", "link"],
    "max_media_mb": 25, "link_appendix": "smart",
    "message_link": True, "source_footer": True,
    "notify_media": True, "single_message": True, "clean_commands": True,
    "dedup_enabled": True, "dedup_window_hours": 12, "dedup_scan_limit": 100,
    "dm_enabled": True, "dm_keywords": ["airfryer", "kahve"],
    "dm_exclude_keywords": ["kupon"], "dm_price_max": ["10"],
    "auto_restart": False, "notify_on_start": False,
    "fast_poll": False, "private_control": False, "slow_report_seconds": 0,
}
PROFILES: dict[str, dict[str, Any]] = {
    "prod": {"smart_header": True, "fast_notify": True},
    "botdown": {"smart_header": True, "fast_notify": True},
    "classic": {"smart_header": False, "fast_notify": False},
}
SAMPLE_EVERY = {"prod": 1, "botdown": 1, "classic": 4}   # profil kaç kayıtta bir denenir
LONG_EVERY = 16                                          # "long" biçimi kaç kayıtta bir
PRELOAD_BATCH = 100                                      # dedup_scan_limit üst sınırı


# --- Girdiler ----------------------------------------------------------------

def load_corpus() -> list[dict[str, str]]:
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


def corpus_digest(corpus: list[dict[str, str]]) -> str:
    return hashlib.sha256("\x00".join(item["text"] for item in corpus).encode("utf-8")).hexdigest()


def utf16(text: str) -> int:
    """Telegram entity konumları UTF-16 birimidir (üretim kodundan bağımsız hesap)."""
    return len(text.encode("utf-16-le")) // 2


PRODUCT_URLS = (
    "https://www.amazon.com.tr/dp/B0GOLDEN01?tag=golden-21&linkCode=ogi",
    "https://www.hepsiburada.com/golden-urun-p-HBCV00000GOLD1?magaza=Hepsiburada",
    "https://www.trendyol.com/golden/urun-p-123456789?boutiqueId=61&merchantId=968",
    "https://amzn.to/4gOLDeN",
    "https://ty.gl/golden123",
)
_AMOUNT_RE = re.compile(r"\d[\d.,]*\s?(?:TL|₺)")


def rich_parts(index: int, text: str) -> tuple[str, list[Any], Any, list[tuple[int, int]]]:
    """Örneğe belirlenimci biçim giydir: (metin, entity'ler, düğmeler, üstü çizili aralıklar).

    Kanallardaki gerçek iletiye benzer: ilk satır kalın; iki tutar varsa ilki üstü
    çizili (eski fiyat); bir satır gizli ürün bağlantısı (tek sıralarda ürün adı,
    çift sıralarda son satır); altında ürün düğmesi ve tek sıralarda WhatsApp düğmesi.
    Üç örnekten ikisine temizliğin SİLMESİ gereken ekler konur (başa etiket ya da
    sona etiket + WhatsApp bağlantısı); böylece silme sonrası entity kaydırma da
    sabitlenir.
    """
    url = PRODUCT_URLS[index % len(PRODUCT_URLS)]
    prefix, suffix = (
        ("", "\n\n#işbirliği #reklam\nhttps://wa.me/905550000000"),
        ("#reklam\n", "\n\n💚Whatsapp Önemli Fırsatlar"),
        ("", ""),
    )[index % 3]
    full = prefix + text + suffix

    def entity(kind: Any, start: int, end: int, **extra: Any) -> Any:
        return kind(offset=utf16(prefix + text[:start]), length=utf16(text[start:end]), **extra)

    lines: list[tuple[int, int]] = []          # boş olmayan satırların (başlangıç, bitiş) konumu
    cursor = 0
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped:
            start = cursor + line.index(stripped)
            lines.append((start, start + len(stripped)))
        cursor += len(line) + 1

    entities: list[Any] = []
    strike: list[tuple[int, int]] = []
    if lines:
        entities.append(entity(types.MessageEntityBold, *lines[0]))
        linked = lines[0] if index % 2 or len(lines) == 1 else lines[-1]
        if "http" not in text[linked[0]:linked[1]].lower():
            entities.append(entity(types.MessageEntityTextUrl, *linked, url=url))
    amounts = list(_AMOUNT_RE.finditer(text))
    if len(amounts) >= 2:
        strike.append(amounts[0].span())
        entities.append(entity(types.MessageEntityStrike, *amounts[0].span()))
    entities.sort(key=lambda item: (item.offset, -item.length))

    # Düğme, dört örnekten ikisinde gövdedeki gizli bağlantıyla aynı adrese gider.
    button_url = url if index % 4 < 2 else PRODUCT_URLS[(index + 1) % len(PRODUCT_URLS)]
    rows = [FakeRow([FakeButton("Ürüne Git 🛒", url=button_url)])]
    if index % 2:
        rows.append(FakeRow([FakeButton("WhatsApp grubumuz", url="https://chat.whatsapp.com/GOLDEN")]))
    return full, entities, FakeMarkup(rows), strike


def variants_for(index: int, profile: str) -> tuple[str, ...]:
    return ("plain", "rich", "long") if profile == "prod" and index % LONG_EVERY == 0 else ("plain", "rich")


def build_event(index: int, text: str, variant: str, message_id: int = EVENT_ID) -> FakeEvent:
    chat_id = next(iter(bot.SOURCE_IDS))
    if variant == "plain":
        return FakeEvent(chat_id, 999, text, message_id=message_id, media=True)
    if variant == "long":
        # Fotoğraf açıklaması sınırını (1024) aşacak kadar yinelenmiş metin.
        text = "\n".join([text] * (1300 // max(1, utf16(text)) + 1))
    full, entities, markup, _ = rich_parts(index, text)
    return FakeEvent(chat_id, 999, full, message_id=message_id, media=variant == "long",
                     entities=entities, reply_markup=markup)


# --- Çıktıyı sıradan veriye çevirme ------------------------------------------

def without_unset(dump: Any) -> Any:
    """Telethon dökümünden verilmemiş (None) alanları, iç içe nesneler dâhil, çıkar."""
    if isinstance(dump, dict):
        return {key: without_unset(item) for key, item in dump.items() if item is not None}
    if isinstance(dump, list):
        return [without_unset(item) for item in dump]
    return dump


def plain(value: Any) -> Any:
    """Çıktıyı JSON'a yazılabilir, küme sırasından bağımsız sıradan veriye çevir."""
    if hasattr(value, "to_dict"):
        # Telethon nesnesi: verilmemiş seçmeli alanlar yazılmaz. Kütüphane yeni sürümlerde
        # şemaya alan ekler (1.36'da olmayan MessageMediaPhoto.video gibi); kayıt botun
        # davranışına bağlı kalsın, kurulu Telethon sürümüne değil.
        value = without_unset(value.to_dict())
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = dataclasses.asdict(value)
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(plain(item) for item in value)
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if isinstance(value, (bytes, bytearray)):
        return f"<bytes {len(value)} sha256:{hashlib.sha256(bytes(value)).hexdigest()[:12]}>"
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return f"<{type(value).__name__}>"


def price_info(info: Any) -> dict[str, Any]:
    return {"value": info.value, "shown": info.shown, "confidence": info.confidence,
            "reason": info.reason, "text": info.text}


def price_layer(index: int, text: str) -> dict[str, Any]:
    """price_filter'ın saf çıktıları; "struck" alanları eski fiyatı üstü çizili aynı metindir."""
    _, _, _, strike = rich_parts(index, text)
    record: dict[str, Any] = {
        "candidates": plain(price_filter.find_candidates(text)),
        "price": price_info(price_filter.extract_price(text)),
        "title": price_filter.extract_title(text),
        "generic_title": price_filter.is_generic_title(text),
        "campaign": price_filter.has_campaign_marker(text),
    }
    if strike:
        record["strike"] = plain(strike)
        record["candidates_struck"] = plain(price_filter.find_candidates(text, strike))
        record["price_struck"] = price_info(price_filter.extract_price(text, strike))
    return record


# --- Sahte Bot API (yalnızca ağ sınırı) --------------------------------------

class BotApiWire:
    """``urllib.request.urlopen`` yerine geçer; üretimin kurduğu isteği olduğu gibi saklar."""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request, timeout: float | None = None) -> io.BytesIO:
        self.requests.append(request)
        if not self.ok:
            body = {"ok": False, "error_code": 403,
                    "description": "Forbidden: bot was kicked from the group chat"}
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {},  # type: ignore[arg-type]
                                         io.BytesIO(json.dumps(body).encode()))
        return io.BytesIO(json.dumps({"ok": True, "result": {"message_id": BOT_MESSAGE_ID}}).encode())


_JSON_FIELDS = frozenset({"entities", "caption_entities", "reply_markup", "link_preview_options"})


def wire_field(name: str, value: Any) -> Any:
    """Bot API'nin JSON dizesi olarak taşıdığı alanları (entity, klavye) okunur hâle getir."""
    return json.loads(value) if name in _JSON_FIELDS and isinstance(value, str) else value


def wire_request(request: urllib.request.Request, *, full: bool = True) -> dict[str, Any]:
    """Giden Bot API isteğini çöz: yöntem, alanlar, dosyalar ve gövdenin özeti.

    ``wire`` gövdenin bayt düzeyindeki özetidir: alanlar aynı kalıp yalnızca tel
    üstündeki yazım değişirse onu da gösterir. ``full=False`` iken (reddedilen
    denemeler) yalnızca yöntem ve özet tutulur.
    """
    prefix = f"https://api.telegram.org/bot{BOT_TOKEN}/"
    assert request.full_url.startswith(prefix), request.full_url
    content_type = request.get_header("Content-type") or ""
    body = bytes(request.data or b"")
    record: dict[str, Any] = {"method": request.full_url[len(prefix):]}
    if content_type == "application/json":
        record["wire"] = hashlib.sha256(body).hexdigest()[:16]
        if full:
            payload = json.loads(body.decode("utf-8"))
            record["fields"] = {key: wire_field(key, item) for key, item in payload.items()}
        return record
    marker = "multipart/form-data; boundary="
    assert content_type.startswith(marker), content_type
    boundary = content_type[len(marker):].encode()
    # Sınır dizgisi her istekte rastgeledir; özet ondan arındırılmış gövdeden alınır.
    record["wire"] = hashlib.sha256(body.replace(boundary, b"BOUNDARY")).hexdigest()[:16]
    if not full:
        return record
    fields: dict[str, Any] = {}
    files: list[dict[str, Any]] = []
    for part in body.split(b"--" + boundary)[1:-1]:
        head, _, data = part.partition(b"\r\n\r\n")
        assert data.endswith(b"\r\n"), part[:80]
        headers = head.decode("utf-8").strip().split("\r\n")
        name = re.search(r' name="([^"]*)"', headers[0]).group(1)
        filename = re.search(r' filename="([^"]*)"', headers[0])
        if filename:
            files.append({"field": name, "filename": filename.group(1),
                          "mime": headers[1].split(": ", 1)[1], "data": plain(data[:-2])})
        else:
            fields[name] = wire_field(name, data[:-2].decode("utf-8"))
    record["fields"] = fields
    record["files"] = files
    return record


# --- Gerçek akışı çalıştırma -------------------------------------------------

class CapturingQueue(bot.PrivateOfferQueue):
    """Özel kopya kuyruğu: öğeyi 1 sn aralıklı gönderim döngüsüne bırakmadan yakalar."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.captured: list[dict[str, Any]] = []

    def submit(self, item: dict[str, Any]) -> bool:
        self.captured.append(copy.deepcopy(item))
        return True


@contextlib.contextmanager
def bot_module_restored():
    """Koşudan sonra ``bot`` modülünü bulunduğu gibi bırak; koşu boyunca çıktıyı sustur.

    main() modül düzeyindeki ayarları (SMART_HEADER, NOTIFY_BOT_TOKEN, ADMIN_IDS...)
    değiştirir. Bunlar sonraki test modüllerine sızmasın diye BÜYÜK HARFLİ her ad
    eski nesnesine ve eski içeriğine döndürülür, koşunun yarattığı adlar silinir.
    Günlük ve yapılandırma raporu yalnızca gürültü olduğu için susturulur.
    """
    saved = {name: value for name, value in vars(bot).items() if name.isupper()}
    contents: dict[str, Any] = {}
    for name, value in saved.items():
        if isinstance(value, (dict, list, set)):
            try:
                contents[name] = copy.deepcopy(value)
            except Exception:  # noqa: BLE001 - kilit/olay nesnesi taşıyan kayıtlar: yüzeysel kopya yeter
                contents[name] = copy.copy(value)
    disabled = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            yield
    finally:
        logging.disable(disabled)
        for name in [name for name in vars(bot) if name.isupper() and name not in saved]:
            delattr(bot, name)
        for name, value in saved.items():
            if name in contents and value != contents[name]:
                value.clear()
                (value.extend if isinstance(value, list) else value.update)(contents[name])
            setattr(bot, name, value)


Scenario = Callable[[FakeClient, CapturingQueue], Awaitable[None]]

# Koşu boyunca ortamdan yalnızca bunlar geçer. config.json'ın üzerine yazan değişkenler
# (DESTINATION, MATCH_MODE, SOURCE_CHATS...) ve Actions'ın GITHUB_* değişkenleri
# çıktıyı makineye bağlamasın diye geri kalan her şey koşu süresince gizlenir.
_PASSTHROUGH_ENV = ("PATH", "SYSTEMROOT", "HOME", "TMPDIR", "TEMP", "TMP", "TZ", "LANG", "LC_ALL")


def run_bot(profile: dict[str, Any], scenario: Scenario, *, wire: Callable[..., Any],
            history: list[tuple] | None = None, env: dict[str, str] | None = None) -> None:
    """Üretimin ``main()``'ini başlat; ``scenario`` bot ayaktayken koşar.

    Sahte olan yalnızca iki ağ sınırıdır: Telethon istemcisi (test_integration.FakeClient)
    ve ``urllib.request.urlopen`` (``wire``). Aradaki her şey üretim kodudur.
    ``profile`` BASE_CONFIG'in üzerine yazılır. Ortam yalıtılmıştır: _PASSTHROUGH_ENV,
    BASE_ENV, bot anahtarı ve ``env`` dışında hiçbir değişken görünmez.
    """
    clients: list[FakeClient] = []
    queues: list[CapturingQueue] = []

    def client_factory(*args: Any, **kwargs: Any) -> FakeClient:
        client = FakeClient(*args, **kwargs)
        client.history = list(history or [])

        async def alive() -> None:
            await scenario(client, queues[0])

        client.run_until_disconnected = alive  # type: ignore[method-assign]
        clients.append(client)
        return client

    def queue_factory(*args: Any, **kwargs: Any) -> CapturingQueue:
        queues.append(CapturingQueue(*args, **kwargs))
        return queues[-1]

    with tempfile.TemporaryDirectory() as folder, bot_module_restored():
        reset_state()
        path = Path(folder) / "config.json"
        path.write_text(json.dumps({**BASE_CONFIG, **profile}), encoding="utf-8")
        environment = {name: os.environ[name] for name in _PASSTHROUGH_ENV if name in os.environ}
        environment.update({**BASE_ENV, "NOTIFY_BOT_TOKEN": BOT_TOKEN, **(env or {})})
        with mock.patch.dict(os.environ, environment, clear=True), \
             mock.patch.object(bot, "TelegramClient", client_factory), \
             mock.patch.object(bot, "StringSession", lambda *args, **kwargs: object()), \
             mock.patch.object(bot, "PrivateOfferQueue", queue_factory), \
             mock.patch.object(urllib.request, "urlopen", wire):
            asyncio.run(bot.main(["--config", str(path)]))
    assert len(clients) == 1 and len(queues) == 1, (len(clients), len(queues))


_STAT_KEYS = ("seen", "matched", "forwarded", "failed", "deduped", "dedup_edits", "cleaned")
_DEDUP_KEYS = ("title", "count", "sources", "footer_name", "offer", "plain_offer", "product_asins",
               "pending", "failed", "editable", "kind", "bot_message_id", "account_message_id", "text")


def dedup_snapshot() -> dict[str, Any]:
    """Tekrar önbelleğinin kimlik alanları (zaman damgaları ve kilit nesneleri hariç)."""
    return {key: {name: plain(entry.get(name)) for name in _DEDUP_KEYS if name in entry}
            for key, entry in sorted(bot.DEDUP_CACHE.items())}


def account_actions(client: FakeClient) -> dict[str, Any]:
    """Kullanıcı hesabının (Telethon) hedefe yaptığı her şey."""
    actions: dict[str, Any] = {}
    if client.forwarded:
        actions["forward"] = [[entity, plain(getattr(message, "id", None)), from_peer]
                              for entity, message, from_peer in client.forwarded]
    if client.sent:
        actions["send_message"] = [
            {"to": entity, "text": plain(message), **plain(kwargs)}
            for (entity, message), kwargs in zip(client.sent, client.sent_kwargs, strict=True)
        ]
    if client.files:
        actions["send_file"] = [
            {"to": entity, "file": plain(payload), "name": name, "caption": caption, **plain(kwargs)}
            for (entity, payload, caption), name, kwargs
            in zip(client.files, client.file_names, client.file_kwargs, strict=True)
        ]
    if client.deleted:
        actions["delete"] = plain(client.deleted)
    if client.edited:
        actions["edit"] = plain(client.edited)
    return actions


async def private_copy(queue: CapturingQueue, item: dict[str, Any]) -> dict[str, Any]:
    """Kuyruğa giren özel (DM) kopya ve üretimin onun için hazırlayacağı Bot API isteği."""
    summary: dict[str, Any] = {"strike": plain(item.get("strike"))}
    if not item.get("bot"):
        # Bildirim bottan gitmediyse istek hesap kopyası okunarak kurulur (Telethon);
        # burada yalnızca kuyruğa giren öğe sabitlenir.
        summary["account_ids"] = plain(item.get("account_ids"))
        summary["keyboard"] = plain(item.get("keyboard"))
        return summary
    try:
        request = await queue.prepare(item)
    except bot.BotAPIError as exc:
        summary["refused"] = str(exc)
    else:
        summary["request"] = request and {"method": request[0], "payload": plain(request[1])}
    return summary


def deliver_all(profile_name: str, corpus: list[dict[str, str]]) -> list[dict[str, Any] | None]:
    """Her örneği, birbirinden yalıtılmış olarak, gerçek kaynak işleyicisinden geçir."""
    wire = BotApiWire(ok=profile_name != "botdown")
    records: list[dict[str, Any] | None] = []

    async def scenario(client: FakeClient, queue: CapturingQueue) -> None:
        handler = client.handlers[1][1]          # on_new_message (kaynak işleyicisi)
        assert not wire.requests, "açılışta beklenmeyen Bot API isteği"
        logs = (client.sent, client.sent_kwargs, client.sent_ids, client.files, client.file_names,
                client.file_kwargs, client.forwarded, client.deleted, client.edited,
                wire.requests, queue.captured)

        async def observe(event: FakeEvent) -> dict[str, Any]:
            """Tek bir kaynak mesajının dışarıdan görülen bütün etkileri."""
            for entries in logs:
                entries.clear()
            client._next_id = 100
            stats = {key: bot.STATS[key] for key in _STAT_KEYS}
            modes = dict(bot.STATS["modes"])
            await handler(event)
            return {
                "bot": [wire_request(request, full=wire.ok) for request in wire.requests],
                "account": account_actions(client),
                "stats": {key: bot.STATS[key] - stats[key] for key in _STAT_KEYS
                          if bot.STATS[key] != stats[key]},
                "modes": {mode: count - modes.get(mode, 0)
                          for mode, count in sorted(bot.STATS["modes"].items())
                          if count != modes.get(mode, 0)},
                "dedup": dedup_snapshot(),
                "dm": [await private_copy(queue, item) for item in list(queue.captured)],
            }

        for index, item in enumerate(corpus):
            if index % SAMPLE_EVERY[profile_name]:
                records.append(None)
                continue
            record: dict[str, Any] = {}
            for variant in variants_for(index, profile_name):
                # Yalıtım: her örnek boş tekrar önbelleğiyle başlar.
                for registry in (bot.DEDUP_CACHE, bot.LIVE_SEEN, bot.POLL_DISPATCHED, bot.SOURCE_IN_FLIGHT, bot.CURSOR_RAM):
                    registry.clear()
                cell = await observe(build_event(index, item["text"], variant))
                if profile_name == "prod":
                    # Aynı fırsat ikinci kez gelir: tekrar birleştirme ve 📌 notu.
                    cell["again"] = await observe(build_event(index, item["text"], variant, EVENT_ID + 1))
                record[variant] = fold_cell(cell)
            records.append(record)

    run_bot(PROFILES[profile_name], scenario, wire=wire)
    return records


# --- Yinelenen metni bir kez yazma --------------------------------------------

SAME = "="
_SENT_KIND = {"text": "text", "caption": "text", "entities": "entities",
              "caption_entities": "entities", "reply_markup": "reply_markup"}


def same_as(base: Any, value: Any) -> Any:
    """``value`` botun gönderdiğiyle aynıysa "=", onunla başlıyorsa {"=": n, "+": kalan}."""
    if base is None:
        return value
    if value == base:
        return SAME
    if type(value) is type(base) and isinstance(base, (str, list)):
        shared = next((position for position, (left, right) in enumerate(zip(base, value))
                       if left != right), min(len(base), len(value)))
        if shared * 2 >= len(base):
            return {SAME: shared, "+": value[shared:]}
    return value


def fold_cell(cell: dict[str, Any]) -> dict[str, Any]:
    """Botun ilk isteğindeki metin/biçim/düğmeler hücrede yineleniyorsa yerine "=" yaz."""
    first = cell["bot"][0].get("fields", {}) if cell["bot"] else {}
    sent = {"text": first.get("text", first.get("caption")),
            "entities": first.get("entities", first.get("caption_entities")),
            "reply_markup": first.get("reply_markup")}

    def fold(holder: dict[str, Any] | None, *names: str) -> None:
        for name in names:
            if holder and name in holder:
                holder[name] = same_as(sent[_SENT_KIND[name]], holder[name])

    def fold_part(part: dict[str, Any], requests: list[dict[str, Any]]) -> None:
        for request in requests:
            fold(request.get("fields"), *_SENT_KIND)
        for action in part["account"].get("send_message", []):
            fold(action, "text")
        for action in part["account"].get("send_file", []):
            fold(action, "caption")
        for entry in part["dedup"].values():
            fold(entry, "text")
        for queued in part["dm"]:
            fold((queued.get("request") or {}).get("payload"), *_SENT_KIND)

    fold_part(cell, cell["bot"][1:])
    again = cell.get("again")
    if again is not None:
        fold_part(again, again["bot"])
        again["dedup"] = changed_fields(cell["dedup"], again["dedup"])
    return cell


def changed_fields(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """İkinci gözlemde değişen alanlar (kaybolan anahtar "<silindi>" olarak görünür)."""
    changed: dict[str, Any] = {key: "<silindi>" for key in before if key not in after}
    for key, value in after.items():
        if key not in before:
            changed[key] = value
        elif isinstance(value, dict) and isinstance(before[key], dict):
            inner = changed_fields(before[key], value)
            if inner:
                changed[key] = inner
        elif value != before[key]:
            changed[key] = value
    return changed


# --- Açılış taraması -----------------------------------------------------------

_TL_ENTITY_TYPES = {
    "bold": types.MessageEntityBold, "italic": types.MessageEntityItalic,
    "underline": types.MessageEntityUnderline, "strikethrough": types.MessageEntityStrike,
    "code": types.MessageEntityCode, "spoiler": types.MessageEntitySpoiler,
    "blockquote": types.MessageEntityBlockquote,
}


def tl_entity(entity: dict[str, Any]) -> Any:
    """Bot API entity sözlüğünü, Telegram'ın geçmişte geri vereceği Telethon nesnesine çevir."""
    place = {"offset": entity["offset"], "length": entity["length"]}
    if entity["type"] == "text_link":
        return types.MessageEntityTextUrl(url=entity["url"], **place)
    if entity["type"] == "expandable_blockquote":
        return types.MessageEntityBlockquote(collapsed=True, **place)
    return _TL_ENTITY_TYPES[entity["type"]](**place)


def preload_all(prod_records: list[dict[str, Any] | None]) -> list[dict[str, Any]]:
    """Botun kendi bildirimlerini hedef geçmişi olarak ver; açılışta kurulan önbelleği oku.

    Çift sıradaki kayıtların fotoğraflı ("plain"), tek sıradakilerin metin ("rich")
    bildirimi kullanılır; her açılış en fazla PRELOAD_BATCH ileti tarar.
    """
    notifications: list[tuple[str, list[Any], bool]] = []
    for index, record in enumerate(prod_records):
        fields = record["rich" if index % 2 else "plain"]["bot"][0]["fields"]
        if "caption" in fields:
            notifications.append((fields["caption"], fields.get("caption_entities", []), True))
        else:
            notifications.append((fields["text"], fields.get("entities", []), False))
    bot_id = int(BOT_TOKEN.split(":", 1)[0])
    results: list[dict[str, Any]] = []
    for first in range(0, len(notifications), PRELOAD_BATCH):
        batch = notifications[first:first + PRELOAD_BATCH]
        # iter_messages yeniden eskiye döner: en yeni ileti listenin başındadır.
        history = [
            (GROUP_ID, text, bot_id, None, media, [tl_entity(item) for item in entities], first + position + 1)
            for position, (text, entities, media) in reversed(list(enumerate(batch)))
        ]
        cache: dict[str, Any] = {}

        async def scenario(client: FakeClient, queue: CapturingQueue) -> None:
            cache.update(dedup_snapshot())

        run_bot(PROFILES["prod"], scenario, wire=BotApiWire(), history=history)
        texts = [text for text, _, _ in batch]
        for entry in cache.values():
            # Kaydın metni yerine, hangi kayıttan (corpus sırası) kurulduğu yazılır.
            text = entry.pop("text", None)
            entry["from"] = first + texts.index(text) if text in texts else text
        results.append({"first": first, "count": len(batch), "cache": cache})
    return results


# --- Üretim / karşılaştırma ---------------------------------------------------

def build_reference(corpus: list[dict[str, str]]) -> dict[str, Any]:
    reference: dict[str, Any] = {
        "price": [price_layer(index, item["text"]) for index, item in enumerate(corpus)],
    }
    for name in PROFILES:
        reference[name] = deliver_all(name, corpus)
    reference["preload"] = preload_all(reference["prod"])
    return reference


def differences(expected: Any, actual: Any, path: str = "") -> list[tuple[str, Any, Any]]:
    """İki sıradan veri ağacı arasındaki farklar: (yol, beklenen, gerçekleşen)."""
    if type(expected) is not type(actual):
        return [(path, expected, actual)]
    if isinstance(expected, dict):
        found: list[tuple[str, Any, Any]] = []
        for key in sorted(set(expected) | set(actual)):
            if key not in expected or key not in actual:
                found.append((f"{path}.{key}", expected.get(key, "<yok>"), actual.get(key, "<yok>")))
            else:
                found.extend(differences(expected[key], actual[key], f"{path}.{key}"))
        return found
    if isinstance(expected, list):
        found = []
        if len(expected) != len(actual):
            found.append((f"{path}.length", len(expected), len(actual)))
        for position, (left, right) in enumerate(zip(expected, actual)):
            found.extend(differences(left, right, f"{path}[{position}]"))
        return found
    return [] if expected == actual else [(path, expected, actual)]


def describe(name: str, found: list[tuple[str, Any, Any]], total: int,
             corpus: list[dict[str, str]] | None, limit: int = 12) -> str:
    """Farkları okunur biçimde özetle: kaç kayıt, hangi alan, hangi kaynak satırı."""
    record_of = re.compile(rf"{re.escape(name)}\[(\d+)\]")
    touched = sorted({int(match.group(1)) for path, _, _ in found if (match := record_of.match(path))})
    lines = [f"'{name}' katmanında {len(found)} fark, {len(touched)} kayıtta (toplam {total} kayıt)."]
    for path, left, right in found[:limit]:
        match = record_of.match(path)
        source = f"   ← {corpus[int(match.group(1))]['src']}" if match and corpus else ""
        lines += [f"  {path}{source}", f"      beklenen    : {left!r}", f"      gerçekleşen : {right!r}"]
    if len(found) > limit:
        lines.append(f"  ... ve {len(found) - limit} fark daha")
    return "\n".join(lines)


def render_expected(reference: dict[str, Any]) -> str:
    """Geçerli JSON; her kayıt kendi satırında (fark incelemesi kayıt kayıt yapılabilsin)."""
    blocks = []
    for name, rows in reference.items():
        lines = ",\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows)
        blocks.append(f"{json.dumps(name)}: [\n{lines}\n]")
    text = "{\n" + ",\n".join(blocks) + "\n}\n"
    assert json.loads(text) == reference, "beklenen çıktı JSON gidiş-dönüşünde değişiyor"
    return text


class GoldenReferenceTest(unittest.TestCase):
    """Bugünkü çıktılar kayıtlı referansla birebir aynı olmalı."""

    corpus: list[dict[str, str]]
    expected: dict[str, Any]
    delivered: dict[str, list[dict[str, Any] | None]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.corpus = load_corpus()
        cls.expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
        cls.delivered = {}

    @classmethod
    def tearDownClass(cls) -> None:
        cls.delivered = {}

    def deliver(self, name: str) -> list[dict[str, Any] | None]:
        if name not in self.delivered:
            self.delivered[name] = deliver_all(name, self.corpus)
        return self.delivered[name]

    def assert_layer(self, name: str, actual: list[Any]) -> None:
        found = differences(self.expected[name], actual, name)
        if found:
            corpus = None if name == "preload" else self.corpus
            self.fail(describe(name, found, len(self.expected[name]), corpus) + (
                "\nDavranış değişmemeliydi: dur ve farkı incele. Değişiklik bilinçliyse farkın tamamı "
                "`python tests/test_golden_reference.py --review` ile incelenir; dosya yalnızca oradaki "
                "onay koduyla (--regenerate KOD) yeniden yazılır."))

    def test_corpus_is_frozen(self) -> None:
        self.assertEqual(len(self.corpus), CORPUS_COUNT)
        self.assertEqual(len({item["text"] for item in self.corpus}), CORPUS_COUNT)
        self.assertEqual(corpus_digest(self.corpus), CORPUS_SHA256,
                         "corpus.json değişmiş: girdi kümesi dondurulmuştur")

    def test_expected_file_is_canonical(self) -> None:
        self.assertEqual(render_expected(self.expected), EXPECTED_PATH.read_text(encoding="utf-8"),
                         "expected.json elle düzenlenmiş görünüyor; "
                         "yalnızca --review ve ardından --regenerate KOD ile yazılır")

    def test_price_layer(self) -> None:
        self.assert_layer("price", [price_layer(index, item["text"])
                                    for index, item in enumerate(self.corpus)])

    def test_prod_profile_delivery(self) -> None:
        self.assert_layer("prod", self.deliver("prod"))

    def test_botdown_fallback_delivery(self) -> None:
        self.assert_layer("botdown", self.deliver("botdown"))

    def test_classic_profile_delivery(self) -> None:
        self.assert_layer("classic", self.deliver("classic"))

    def test_preload_reads_back_own_notifications(self) -> None:
        self.assert_layer("preload", preload_all(self.deliver("prod")))


# --- İnceleme ve onaylı yeniden üretim -----------------------------------------
#
# expected.json yalnızca aşağıdaki iki adımla değişir. Testler ve --review hiçbir şey
# yazmaz; --regenerate, incelenmiş fark kümesinin onay kodu olmadan yazmaz.

USAGE = """\
Kullanım:
  python tests/test_golden_reference.py                    testleri koşar (hiçbir şey yazmaz)
  python tests/test_golden_reference.py --review [--full]  farkları ve onay kodunu gösterir (hiçbir şey yazmaz)
  python tests/test_golden_reference.py --regenerate KOD   yalnızca --review'ın verdiği onay koduyla yazar"""

_INDEX = re.compile(r"\[\d+\]")
_RECORD = re.compile(r"\w+\[(\d+)\]")


def approval_code(old_text: str, new_text: str) -> str:
    """İncelenen değişikliğin kimliği: dosyadaki içerik ile yazılacak içeriğin ortak özeti."""
    digest = hashlib.sha256()
    for part in (old_text, new_text):
        digest.update(hashlib.sha256(part.encode("utf-8")).digest())
    return digest.hexdigest()[:12]


def pending_change() -> tuple[list[dict[str, str]], str, str]:
    """(corpus, dosyadaki metin, bugünkü kodun üreteceği metin). Hiçbir şey yazmaz."""
    corpus = load_corpus()
    if corpus_digest(corpus) != CORPUS_SHA256:
        raise SystemExit("corpus.json değişmiş: girdi kümesi dondurulmuştur; işlem reddedildi.")
    old_text = EXPECTED_PATH.read_text(encoding="utf-8") if EXPECTED_PATH.exists() else ""
    return corpus, old_text, render_expected(build_reference(corpus))


def record_index(path: str) -> int | None:
    match = _RECORD.match(path)
    return int(match.group(1)) if match else None


def spot(left: Any, right: Any, context: int = 30) -> tuple[str, str]:
    """İki değerin yazımı; uzun dizelerde yalnızca farklılaşan bölge, çevresiyle gösterilir."""
    if not (isinstance(left, str) and isinstance(right, str)) or max(len(left), len(right)) <= 4 * context:
        return repr(left), repr(right)
    shortest = min(len(left), len(right))
    head = next((position for position in range(shortest) if left[position] != right[position]), shortest)
    tail = 0
    while tail < shortest - head and left[-1 - tail] == right[-1 - tail]:
        tail += 1
    start, keep = max(0, head - context), max(0, tail - context)

    def cut(value: str) -> str:
        return ("…" if start else "") + repr(value[start:len(value) - keep]) + ("…" if keep else "")

    return cut(left), cut(right)


def review_report(corpus: list[dict[str, str]], old_text: str, new_text: str, *,
                  full: bool = False) -> str:
    """Dosyadaki referans ile bugünkü çıktının farkı: katman özeti, fark türleri, değişen kayıtlar."""

    def stamp(text: str) -> str:
        return f"{len(text.encode('utf-8'))} bayt, sha256 {hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]}"

    def short(text: str, limit: int = 110) -> str:
        return text if full or len(text) <= limit else text[:limit - 1] + "…"

    lines = ["ALTIN REFERANS İNCELEMESİ (hiçbir dosya yazılmadı)",
             f"  dosyadaki : {stamp(old_text)}   {EXPECTED_PATH}",
             f"  bugünkü   : {stamp(new_text)}"]
    if new_text == old_text:
        return "\n".join(lines + ["", "Fark yok: bugünkü çıktı dosyadakiyle birebir aynı."])
    old = json.loads(old_text) if old_text else {}
    new = json.loads(new_text)
    layers = {name: differences(old.get(name, []), new.get(name, []), name)
              for name in [*new, *(name for name in old if name not in new)]}

    lines += ["", "Katman özeti"]
    for name, found in layers.items():
        total = len(new.get(name, []))
        records = {record_index(path) for path, _, _ in found}
        lines.append(f"  {name:8s}: {len(found)} fark, {len(records)} kayıtta (toplam {total} kayıt)"
                     if found else f"  {name:8s}: değişiklik yok ({total} kayıt)")

    lines += ["", "Fark türleri (kayıt ve sıra numaraları [*] ile birleştirilmiştir; ×n = kaç kez)"]
    for found in layers.values():
        kinds: dict[str, list[tuple[str, Any, Any]]] = {}
        for item in found:
            kinds.setdefault(_INDEX.sub("[*]", item[0]), []).append(item)
        for kind, items in sorted(kinds.items()):
            records = {record_index(path) for path, _, _ in items}
            lines.append(f"  {kind}: {len(items)} fark, {len(records)} kayıt")
            moves = collections.Counter(spot(left, right) for _, left, right in items)
            for (left, right), count in moves.most_common(None if full else 3):
                lines.append(f"      ×{count:<4d} {short(left)}  →  {short(right)}")
            if not full and len(moves) > 3:
                lines.append(f"      … ve {len(moves) - 3} farklı geçiş daha")

    lines += ["", "Değişen kayıtlar" + ("" if full else " (her farkın tamamı için: --review --full)")]
    for name, found in layers.items():
        per_record: dict[int | None, list[tuple[str, Any, Any]]] = {}
        for item in found:
            per_record.setdefault(record_index(item[0]), []).append(item)
        if not per_record:
            continue
        if not full:
            numbers = ", ".join("kayıt dışı" if index is None else str(index) for index in per_record)
            lines.append(f"  {name} ({len(per_record)} kayıt):")
            lines.append(textwrap.fill(numbers, width=100, initial_indent="      ", subsequent_indent="      "))
            continue
        for index, items in per_record.items():
            known = index is not None and name != "preload" and index < len(corpus)
            source = f"   ← {corpus[index]['src']}" if known else ""
            lines.append(f"  {name}[{index}]: {len(items)} fark{source}")
            for path, left, right in items:
                before, after = spot(left, right)
                lines.append(f"      {path}: {before}  →  {after}")

    code = approval_code(old_text, new_text)
    return "\n".join(lines + [
        "", f"Onay kodu: {code}",
        "Bu fark kümesi incelendi ve onaylandıysa (yalnızca o zaman):",
        f"    python tests/test_golden_reference.py --regenerate {code}",
        "Kod yalnızca bu fark kümesi için geçerlidir: üretim kodu, corpus ya da dosya değişirse",
        "geçersiz olur ve yeniden inceleme gerekir."])


def command_line(argv: list[str]) -> int:
    """``--review [--full]`` ve ``--regenerate KOD``; dönüş değeri sürecin çıkış kodudur."""
    if argv[:1] == ["--review"] and set(argv[1:]) <= {"--full"}:
        print(review_report(*pending_change(), full="--full" in argv))
        return 0
    if argv[:1] != ["--regenerate"] or len(argv) > 2:
        print(USAGE)
        return 2
    if len(argv) == 1:
        print("REDDEDİLDİ: --regenerate onay kodu olmadan yazmaz. Önce farkları incele:\n"
              "    python tests/test_golden_reference.py --review\n" + USAGE)
        return 2
    _, old_text, new_text = pending_change()
    if new_text == old_text:
        print("Fark yok: bugünkü çıktı dosyadakiyle birebir aynı. Hiçbir şey yazılmadı.")
        return 0
    if argv[1] != approval_code(old_text, new_text):
        print("REDDEDİLDİ: onay kodu şu anki fark kümesiyle eşleşmiyor. Hiçbir şey yazılmadı.\n"
              "Kod incelemeden sonra üretim kodu ya da dosya değiştiyse geçersizdir; yeniden incele:\n"
              "    python tests/test_golden_reference.py --review")
        return 1
    EXPECTED_PATH.write_text(new_text, encoding="utf-8", newline="\n")
    print(f"{EXPECTED_PATH} yazıldı: {len(new_text.encode('utf-8'))} bayt, "
          f"sha256 {hashlib.sha256(new_text.encode('utf-8')).hexdigest()[:16]} (onay kodu {argv[1]}).")
    return 0


class RegenerationGuardTest(unittest.TestCase):
    """expected.json denetimsiz yazılamaz. Hızlıdır: bot koşmaz, gerçek dosyaya dokunulmaz."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "expected.json"
        self.before = render_expected({"price": [{"value": 1}, {"value": 2}], "preload": [{"first": 0}]})
        self.path.write_text(self.before, encoding="utf-8", newline="\n")
        self.produced: dict[str, Any] = {"price": [{"value": 1}, {"value": 3}], "preload": [{"first": 0}]}
        module = sys.modules[__name__]
        for name, value in (("EXPECTED_PATH", self.path),
                            ("build_reference", lambda corpus: copy.deepcopy(self.produced))):
            patcher = mock.patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_cli(self, *argv: str) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = command_line(list(argv))
        return status, output.getvalue()

    def on_disk(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def test_review_shows_the_change_and_writes_nothing(self) -> None:
        code = approval_code(self.before, render_expected(self.produced))
        outputs = {}
        for arguments in (("--review",), ("--review", "--full")):
            with self.subTest(arguments=arguments):
                status, outputs[arguments] = self.run_cli(*arguments)
                self.assertEqual(status, 0)
                self.assertIn("price[*].value: 1 fark, 1 kayıt", outputs[arguments])
                self.assertIn("2  →  3", outputs[arguments])
                self.assertIn(f"--regenerate {code}", outputs[arguments])
                self.assertEqual(self.on_disk(), self.before)
        # --full her farkı kaydıyla ve o kaydın geldiği kaynak satırıyla yazar.
        full = outputs["--review", "--full"]
        self.assertIn("price[1]: 1 fark   ← " + load_corpus()[1]["src"], full)
        self.assertIn("price[1].value: 2  →  3", full)

    def test_regenerate_refuses_without_the_code_of_the_reviewed_change(self) -> None:
        code = approval_code(self.before, render_expected(self.produced))
        for arguments in (("--regenerate",), ("--regenerate", "0" * 12), ("--regenerate", code, "fazla"),
                          ("--regenerate", "--full"), ("--yaz",), ()):
            with self.subTest(arguments=arguments):
                status, output = self.run_cli(*arguments)
                self.assertNotEqual(status, 0)
                self.assertNotIn(code, output)       # ret mesajı geçerli kodu sızdırmaz
                self.assertEqual(self.on_disk(), self.before)
        # İncelemeden sonra çıktı değişirse eski kod artık geçmez.
        self.produced["price"][1]["value"] = 4
        status, output = self.run_cli("--regenerate", code)
        self.assertEqual((status, self.on_disk()), (1, self.before))
        self.assertIn("eşleşmiyor", output)

    def test_regenerate_with_the_reviewed_code_writes_exactly_that_change(self) -> None:
        after = render_expected(self.produced)
        code = approval_code(self.before, after)
        status, output = self.run_cli("--regenerate", code)
        self.assertEqual((status, self.on_disk()), (0, after))
        self.assertIn("yazıldı", output)
        # Artık fark yok: inceleme bunu söyler; aynı kodla ikinci çağrı hiçbir şey yazmaz.
        self.assertIn("Fark yok", self.run_cli("--review")[1])
        stamp = self.path.stat().st_mtime_ns
        status, output = self.run_cli("--regenerate", code)
        self.assertEqual((status, self.on_disk(), self.path.stat().st_mtime_ns), (0, after, stamp))
        self.assertIn("Hiçbir şey yazılmadı", output)

    def test_long_texts_are_shown_around_the_changed_spot(self) -> None:
        left, right = "A" * 100 + "eski" + "B" * 100, "A" * 100 + "yeni!" + "B" * 100
        self.assertEqual(spot(left, right), ("…" + repr("A" * 30 + "eski" + "B" * 30) + "…",
                                             "…" + repr("A" * 30 + "yeni!" + "B" * 30) + "…"))
        self.assertEqual(spot("kısa", "kisa"), ("'kısa'", "'kisa'"))
        self.assertEqual(spot(1, 0), ("1", "0"))


if __name__ == "__main__":
    if sys.argv[1:2] in (["--review"], ["--regenerate"]):
        sys.exit(command_line(sys.argv[1:]))
    unittest.main()
