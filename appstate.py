"""Çalışma zamanı durumu: ayarlar, sayaçlar ve önbellekler — tek sahibi burasıdır.

Eskiden ``bot.py`` içinde modül düzeyi global değişkenlerdi. Artık her yerden
``appstate.AD`` olarak okunur/yazılır (``global`` bildirimi gerekmez). ``bot.main()`` ve
``bot.apply_runtime_config`` doldurur; işlevler ve testler aynı nesneleri paylaşır.
"""

from __future__ import annotations

import asyncio
import collections
import contextvars
import time
from typing import Any


# Yalnızca çalışma sırasında (apply_runtime_config) atanan ayarlar; başlangıç değeri yok:
#   LINK_KINDS, BOT_LINK_KINDS

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

# Alıntı temizliği (kullanıcı isteği, "ilk adım"): üst satırda GÜVENLE tespit
# edilen ürün adı satırı ile tespit edilen tek başına kupon kodu satırı alıntıdan
# silinir. Emin olunmayan satır ASLA silinmez. config: clean_quote_duplicates.
CLEAN_QUOTE_DUPLICATES = False

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

# P6: geriye dönük tarama sınırları (açılış, restart ve duraklama sonrası).
# Kesinti sırasında gelen mesajlar yalnızca bu pencere içinde yakalanır; daha eski
# mesajlar topluca yeniden bildirilmez. Kayıtlı (restart öncesi) imleç varsa önce o
# kullanılır, ama yaş sınırı yine uygulanır: imleç ne kadar eski olursa olsun pencere
# dışındaki mesajlar atlanır.
# config.json → fast_poll_backfill_minutes (varsayılan 15; 1-120 arası).
FAST_POLL_BACKFILL_LIMIT = 100       # kaynak başına en fazla bu kadar geçmiş ileti taranır

FAST_POLL_BACKFILL_MAX_AGE = 900.0   # yalnızca son 15 dk içindeki iletiler

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


STARTED_AT = time.time()
