"""Komut metinleri, liste/filtre düzenleme akışı ve onay mantığı (saf)."""

from __future__ import annotations

import logging
import copy
import dm_filters
import os
import price_filter
import re
import time
from collections.abc import Sequence
from confutil import parse_chat_value
from keywords import filter_state_of, match_mode_of, normalize
from pathlib import Path
from private_bot import PrivateControlEvent, normalize_keyword
from typing import Any

log = logging.getLogger("telegram-filter")


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
    (COMMAND_SETTINGS_ADD, "Liste seçip kayıt ekleme taslağı başlatır (dahil/hariç/grup/DM filtreleri/ürün adı < fiyat/web ürün takibi)."),
    (COMMAND_SETTINGS_REMOVE, "Liste seçip kayıt çıkarma taslağı başlatır (dahil/hariç/grup/DM filtreleri/ürün adı < fiyat/web ürün takibi)."),
    (COMMAND_SETTINGS_SAVE, "Bekleyen liste taslağını kaydeder (DM listeleri dahil)."),
    (COMMAND_SETTINGS_REVERT, "Bekleyen ekleme veya çıkarma işlemini iptal eder."),
    # --- 3) Telegram indirim filtreleri ---
    (COMMAND_TG_FILTER_OPEN, "Telegram indirim akışında dahili, harici veya iki filtreyi açar."),
    (COMMAND_TG_FILTER_CLOSE, "Telegram indirim akışında dahili, harici veya iki filtreyi kapatır."),
    # --- 4) Kişisel bildirim filtreleri (DM) ---
    (COMMAND_DM_STATUS, "Dört kişisel DM filtresinin durumunu, listelerini ve fiyat eşiklerini gösterir."),
    (COMMAND_DM_FILTER_OPEN, "dmdahil grubunu açar; dmfiyat ve ürün adı/fiyat kuralları bağımsızdır."),
    (COMMAND_DM_FILTER_CLOSE, "dmdahil grubunu kapatır; diğer DM filtre grupları bağımsız çalışır."),
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


# ---------------------------------------------------------------------------
# Telegram liste düzenleme akışı
# ---------------------------------------------------------------------------
#
# DM ve grup listeleri ortak taslak/kaydet akışını kullanır; iki Telegram grup filtresi
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
        "🟢  /dmfiltreac  · dmdahil grubunu açar",
        "🔴  /dmfiltrekapat · dmdahil grubunu kapatır",
        "   dmdahil/dmfiyat için dmharic ortak engeldir; ürün adı < fiyat grubu bağımsızdır.",
        "   DM ayarları yalnızca hesap sahibinin özel sohbetinden düzenlenir.",
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
DM_PRICE_FIELD = dm_filters.DM_PRICE_FIELD   # tek değerli; kelime listelerinden ayrı işlenir
DM_PRODUCT_PRICE_FIELD = dm_filters.DM_PRODUCT_PRICE_FIELD
TELEGRAM_LIST_FIELDS = (
    "include_keywords", "exclude_keywords", "source_chats", *DM_LIST_FIELDS,
    DM_PRICE_FIELD, DM_PRODUCT_PRICE_FIELD,
)
# Sayısal seçimleri geriye uyumlu tut: web takip 7 olmaya devam eder; yeni
# bağımsız ürün-adı/fiyat listesi 8'dir.
WEBTAKIP_FIELD = "__webtakip__"
TELEGRAM_LIST_MENU = (*TELEGRAM_LIST_FIELDS[:-1], WEBTAKIP_FIELD, DM_PRODUCT_PRICE_FIELD)
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
    DM_PRODUCT_PRICE_FIELD: {
        "title": "ürün adı < fiyat — DM ürün/fiyat kuralları",
        "icon": "🏷️",
        "description": "Ürün başlığındaki ifade ve gerçek ürün fiyatı birlikte eşleşmeli",
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
    DM_PRODUCT_PRICE_FIELD: {
        "ürün_adı_<_fiyat", "urun_adi_<_fiyat", "dm_urun_fiyat", "dm_ürün_fiyat",
        "dm_product_price_rules", "urun_fiyat", "ürün_fiyat", "urunadifiyat",
    },
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
    return dm_filters.dm_price_threshold(config)


def dm_decision(
    config: dict,
    text: str,
    strike: Sequence[tuple[int, int]] = (),
    *,
    facts: dm_filters.OfferFacts | None = None,
) -> dm_filters.DMDecision:
    """DM'in saf ve açıklanabilir kararını üret; gönderim/queue yan etkisi yoktur."""
    return dm_filters.evaluate_dm_offer(config, text, strike, facts=facts)


def _log_dm_decision(decision: dm_filters.DMDecision) -> None:
    for result in decision.results:
        log.debug(
            "DM filtresi %s: %s (%s)%s",
            result.group, "eşleşti" if result.matched else "reddedildi", result.reason,
            f" [kural: {result.rule}]" if result.rule else "",
        )
    if decision.should_send:
        log.info("DM gönderim kararı: %s", ", ".join(decision.matched_groups))


def dm_should_send(config: dict, text: str, strike: Sequence[tuple[int, int]] = ()) -> bool:
    """Geriye uyumlu bool API; kararın tüm grup nedenleri ``dm_decision`` ile alınır."""
    decision = dm_decision(config, text, strike)
    _log_dm_decision(decision)
    return decision.should_send


def resolve_telegram_list(text: Any) -> str | None:
    """Menü seçimini, Türkçe liste adını veya webdetakip'i kurallı alana çevir."""
    raw = normalize(str(text or "")).strip().lstrip("/")
    if raw.isdigit():
        index = int(raw)
        if 1 <= index <= len(TELEGRAM_LIST_MENU):
            return TELEGRAM_LIST_MENU[index - 1]
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
    for index, field in enumerate(TELEGRAM_LIST_MENU, start=1):
        if field == WEBTAKIP_FIELD:
            count_text = "–" if webtakip_count is None else str(webtakip_count)
            lines += [
                f"{index}. 🌐  webdetakip  ·  Akakçe ürün fiyat takibi  ·  {count_text} kayıt",
                "   Akakçe ürün linkiyle fiyat/hedef takibi: link → hedef fiyat → kalıcı kayıt",
            ]
            continue
        meta = TELEGRAM_LIST_META[field]
        count = len(telegram_list_items(config, field))
        lines.append(f"{index}. {meta['icon']}  {meta['title']}  ·  {count} kayıt")
        lines.append(f"   {meta['description']}")
    lines += [
        "",
        "Seçmek için 1–8 yazabilir; listedeki adı da geçer. 7 (webdetakip) eski seçimi korur.",
        "Vazgeçmek için /iptal.",
    ]
    return "\n".join(lines)


def telegram_list_item_key(field: str, value: Any) -> str:
    """Kayıt karşılaştırması: DM kuralları Türkçe uyumlu, kaynak adları ASCII uyumlu."""
    text = str(value)
    if field in DM_LIST_FIELDS:
        return normalize_keyword(text)
    if field == DM_PRODUCT_PRICE_FIELD:
        try:
            return dm_filters.parse_product_price_rule(text).canonical
        except (TypeError, ValueError):
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
    if field == DM_PRODUCT_PRICE_FIELD:
        if action == "add":
            lines += [
                "Ürün başlığında aranacak ifade ile en yüksek gerçek ürün fiyatını '<' ile yaz.",
                "Örnek: telefon < 100 · TCL < 30000 · LG 75 < 45000",
                "Birden çok kuralı yeni satır veya noktalı virgülle ayır; aynı kural tekrar eklenmez.",
                "Kupon, eski fiyat, taksit ve kargo tutarları ürün fiyatı sayılmaz.",
            ]
        else:
            lines += ["Tek bir kuralı listedeki kayıt numarası veya tam 'ürün adı < fiyat' ifadesiyle çıkar."]
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
    raw_text = str(raw_value or "").strip()
    values = (
        split_values(raw_text)
        if not raw_text or ";" in raw_text or "\n" in raw_text or re.search(r",\s+", raw_text)
        else [raw_text]
    )
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


def split_product_price_rule_values(raw: Any) -> list[str]:
    """Split rule batches without mistaking a decimal comma for a separator.

    Newlines and semicolons are unambiguous. Commas may separate multiple
    complete rules, but a comma is accepted as a rule boundary only after the
    preceding threshold parses and before a following ``name < price`` pair.
    """
    text = str(raw or "").strip()
    if not text:
        return []
    values: list[str] = []
    for line in re.split(r"[\r\n;]+", text):
        line = line.strip()
        if not line:
            continue
        if "<" not in line:
            values.extend(split_values(line))
            continue
        if line.count("<") < 2:
            values.append(line)
            continue
        start = 0
        for match in re.finditer(",", line):
            prefix = line[start:match.start()].strip()
            suffix = line[match.end():]
            if prefix.count("<") != 1 or price_filter.parse_threshold(prefix.split("<", 1)[1]) is None:
                continue
            next_less_than = suffix.find("<")
            if next_less_than < 0:
                continue
            next_name = suffix[:next_less_than].strip()
            if not next_name or "," in next_name or not any(char.isalpha() for char in next_name):
                continue
            values.append(prefix)
            start = match.end()
        remainder = line[start:].strip()
        if remainder:
            values.append(remainder)
    return values


def stage_product_price_rule_change(
    config: dict, action: str, raw_value: Any,
) -> tuple[bool, dict, list[Any], str]:
    """Stage one or more independent title-and-price rules."""
    field = DM_PRODUCT_PRICE_FIELD
    title = TELEGRAM_LIST_META[field]["title"]
    current = telegram_list_items(config, field)
    values = split_product_price_rule_values(raw_value)
    if not values:
        return False, copy.deepcopy(config), [], "Boş ürün adı/fiyat kuralı eklenemez veya çıkarılamaz."
    if len(values) > LIST_BATCH_MAX:
        return False, copy.deepcopy(config), [], f"Tek mesajda en fazla {LIST_BATCH_MAX} kural gönderebilirsin."
    if action == "remove":
        return _stage_list_remove(config, field, current, values, title)

    added: list[str] = []
    duplicates: list[str] = []
    invalid: list[str] = []
    keys = [telegram_list_item_key(field, item) for item in current]
    for raw_rule in values:
        try:
            rule = dm_filters.parse_product_price_rule(raw_rule)
        except (TypeError, ValueError) as exc:
            invalid.append(f"{raw_rule[:32]}… ({exc})" if len(raw_rule) > 32 else f"{raw_rule} ({exc})")
            continue
        if rule.canonical in keys:
            duplicates.append(rule.canonical)
            continue
        keys.append(rule.canonical)
        added.append(rule.canonical)
    if not added:
        lines = ["ℹ️ Eklenecek yeni kural yok."]
        if duplicates:
            lines.append(f"zaten listede: {format_value_preview(duplicates)}")
        if invalid:
            lines.append(f"Geçersiz kural: {format_value_preview(invalid)}")
        return False, copy.deepcopy(config), [], "\n".join(lines)
    if len(current) + len(added) > 100:
        return False, copy.deepcopy(config), [], "Ürün adı/fiyat listesinde en fazla 100 kural olabilir; hiçbir kural eklenmedi."

    message = (
        f"{title} listesine eklenecek: {added[0]}" if len(added) == 1 else
        f"{title} listesine eklenecek {len(added)} kural: {format_value_preview(added)}"
    )
    if duplicates:
        message += f"\n⚠️ {len(duplicates)} kural zaten listede, atlandı: {format_value_preview(duplicates)}"
    if invalid:
        message += f"\n⚠️ {len(invalid)} kural geçersiz, atlandı: {format_value_preview(invalid)}"
    candidate = copy.deepcopy(config)
    candidate[field] = [*current, *added]
    return True, candidate, added, message


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
    if field == DM_PRODUCT_PRICE_FIELD:
        return stage_product_price_rule_change(config, action, raw_value)
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
