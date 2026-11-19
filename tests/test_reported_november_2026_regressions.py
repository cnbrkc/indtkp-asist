"""Kasım 2026 kullanıcı bildirimleri: her vakanın AYRI kök nedeni için dar testler.

Bu dosyada üç ayrı hata sınıfı sabitlenir:

1. Pella: "Set Fiyatı / Adet:" dökümünde birim fiyatı ``normal`` sanıp güveni
   düşürmek (``❓``). Fiyat/ürün adı tespiti hatası.
2. Çoklu paylaşım rozeti: grup adının İÇİNDE ``" | "`` geçince tam adın
   parçalanması ve/veya kısmi ``links`` yüzünden ``link_map`` adreslerinin
   atlanması (kaynak mesaj linkinin gömülmemesi). Çapraz grup dedup hatası.
3.Bonus/"ilk adım": alıntıda GÜVENLE tespit edilen ürün adı / tek başına kupon
   kodu satırının silinmesi; emin olunmayan satırın AYNEN kalması.

Diğer bildirilen başlıklar/fiyatlar (Dyson, High Peak, Solo, Kozmoda, Calvin,
Tommy) bu testlerde de "doğru tespit bozulmadı" iddiasıyla korunur.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bot  # noqa: E402
import confutil  # noqa: E402
import constants  # noqa: E402
import dedupbadge  # noqa: E402
import quotes  # noqa: E402
import sanitize  # noqa: E402
import smarttitle  # noqa: E402
import price_filter  # noqa: E402
from telethon.tl import types  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

PELLA = (
    "🔥Pella 3'lü Mikrofiber Temizlik Bezi\n\n"
    "✅199TL - 2'li Alımda TERTEMİZ75 Kodu İle Set Fiyatı 162TL / Adet: 54TL\n\n"
    "➡️ https://app.hb.biz/K28UyzyvGkNL\n\n"
    "İndirim Kanalı"
)
DYSON = (
    "Dyson V8 Origin Kablosuz Süpürge\n\n"
    "🏷️ 12.999 TL (Son 6 Ayın En Düşük Fiyatı)\n"
    "💬 Ortalama fiyatın %35 altında\n"
    "📂 Elektronik\n"
    "🛍️ Hepsiburada\n"
    "🔗 https://onu.al/fj1h"
)
SOLO = (
    "📦 Solo Ultra Tuvalet Kağıdı 40'lı\n\n"
    "TERTEMİZ75\n\n"
    "sepette kodu aktif edin\n\n"
    "💰 234,00 ₺\n\n"
    "🔥 %25 indirim"
)
KOZMODA = (
    "Hepsiburada Kozmetik kategorilerinde, 600 TL ve üzeri alışverişleriniz için "
    "150 TL indirim kodu:\n\n"
    "KOZMODA150\n\n"
    "Kodu kullanmak için hemen tıklayın:\n"
    "👉 https://app.hb.biz/hlwb6jgdTQ2y\n\n"
    "Tanıtım"
)
CALVIN = (
    "Calvin Klein LOW PRO CUPS WT SUEDE MG Spor AyakkabıKadın 37\n\n"
    "🏷 1.562 TL\n"
    "MODADA250 Kodunu sepette kullanın.\n\n"
    "👉 FIRSATA GİT\n\n"
    "🛍Amazon tanıtım @ozelfirsat GOOGLE 🔍"
)
TWO_PRODUCTS = "Pamuk Havlu\n100 TL\nCam Bardak\n200 TL"

PIPE_NAMES = [
    "Amzn İndirim Fırsatları",
    "Yarı Fiyatına Ürünler | Amazon Depo",
    "🎯indirimalarmi | Önemli Fırsatlar 💥",
    "FırsatlarCo",
    "Depo Fırsatları",
]


def _message(text: str, entities=None) -> SimpleNamespace:
    return SimpleNamespace(id=101, message=text, raw_text=text, entities=list(entities or []),
                           media=None, reply_markup=None)


def _compose_obj(obj: SimpleNamespace, *, clean: bool) -> dict:
    with mock.patch.object(bot, "SMART_HEADER", True), \
         mock.patch.object(bot, "CLEAN_QUOTE_DUPLICATES", clean):
        return bot.compose_message(
            obj,
            link_kinds=confutil.link_kinds_for("smart", bot=True),
            message_link="https://t.me/kaynak/101",
            source_name="Kaynak",
            has_keyboard=True,
        )


def _compose(text: str, *, clean: bool, entities=None) -> dict:
    return _compose_obj(_message(text, entities), clean=clean)


def _text_link(offset: int, length: int, url: str) -> types.MessageEntityTextUrl:
    return types.MessageEntityTextUrl(offset=offset, length=length, url=url)


class PellaPriceRoleTest(unittest.TestCase):
    """1) Set/Adet dökümü: tek dar rol düzeltmesi, genel birim kuralı DEĞİL."""

    def test_set_price_is_final_and_adet_is_unit(self):
        info = price_filter.extract_price(PELLA)
        self.assertEqual(info.value, 162.0)
        self.assertEqual(info.shown, "162TL")
        self.assertEqual(info.confidence, "high")
        candidates = price_filter.find_candidates(PELLA)
        roles = {c.shown: c.role for c in candidates}
        self.assertEqual(roles.get("162TL"), "discounted")
        self.assertEqual(roles.get("54TL"), "unit")
        title, analyzed = price_filter.analyze(PELLA)
        self.assertEqual(title, "Pella 3'lü Mikrofiber Temizlik Bezi")
        self.assertEqual(analyzed.value, 162.0)
        self.assertEqual(_compose(PELLA, clean=False)["header"],
                         "₺162 — Pella 3'lü Mikrofiber Temizlik Bezi")

    def test_plain_adet_price_is_not_rewritten(self):
        """Yalnız 'Adet: 54TL' yazan iletide 54 hâlâ ürün fiyatıdır."""
        info = price_filter.extract_price("Masa Örtüsü\n\nAdet: 54TL")
        self.assertEqual(info.value, 54.0)
        self.assertNotEqual(info.reason, "unit")

    def test_other_reported_prices_still_correct(self):
        expected = {
            DYSON: 12999.0,
            "High Peak Talos 3 Kişilik Çadır\n\n💰 ₺5.902,26": 5902.26,
            SOLO: 234.0,
            CALVIN: 1562.0,
        }
        for text, value in expected.items():
            with self.subTest(text=text[:30]):
                self.assertEqual(price_filter.extract_price(text).value, value)
        # Kupon duyurusunda ürün fiyatı YOKTUR; bu bir tespit hatası değildir.
        self.assertIsNone(price_filter.extract_price(KOZMODA).value)


class BadgeExactNameLinkTest(unittest.TestCase):
    """2) Rozet: tam grup adları bölünmesin; kısmi link haritası tamamlansın."""

    def setUp(self):
        self.links = {name: f"https://t.me/k/{i}" for i, name in enumerate(PIPE_NAMES)}

    def test_live_badge_links_every_whole_name(self):
        badge = dedupbadge.dedup_badge(6, sources=PIPE_NAMES)
        # Eski ayrıştırıcı ad içindeki iki " | " yüzünden 7 parça görüyordu.
        self.assertEqual(len(dedupbadge.dedup_badge_sources(badge)), 7)
        entities = dedupbadge.dedup_badge_entities(
            badge, bot_api=True, offset=0, links=self.links, names=PIPE_NAMES,
        )
        labelled = {
            entity["url"]: sanitize.utf16_slice(badge, entity["offset"], entity["length"])
            for entity in entities if entity["type"] == "text_link"
        }
        self.assertEqual(labelled, {url: name for name, url in self.links.items()})

    def test_preload_reconstructs_whole_names_from_link_labels(self):
        badge = dedupbadge.dedup_badge(6, sources=PIPE_NAMES)
        names = dedupbadge.dedup_badge_names(badge, self.links)
        self.assertEqual(names, PIPE_NAMES)
        self.assertEqual(dedupbadge.dedup_badge_links({"link_map": self.links}, names), self.links)

    def test_partial_live_links_are_completed_from_preload_map(self):
        entry = {
            "links": {PIPE_NAMES[0]: self.links[PIPE_NAMES[0]]},
            "link_map": {name: url for name, url in self.links.items() if name != PIPE_NAMES[0]},
        }
        self.assertEqual(dedupbadge.dedup_badge_links(entry, PIPE_NAMES), self.links)

    def test_legacy_comma_and_single_group_parsing_preserved(self):
        self.assertEqual(
            dedupbadge.dedup_badge_sources("📌 2 kere paylaşıldı: FirsatZ, İndirimde Al"),
            ["FirsatZ", "İndirimde Al"],
        )
        name = "İNDİRİM & FIRSAT TR 🛒 Sıcak Fırsatlar, Alışveriş & Kupon"
        single = dedupbadge.dedup_badge(2, sources=[name])
        self.assertTrue(single.endswith("· tek grup"))
        self.assertEqual(dedupbadge.dedup_badge_sources(single), [name])

    def test_requested_username_fallback_builds_message_link(self):
        event = _message("x")
        event.chat_id = -1002343434094
        event.chat = SimpleNamespace(username=None)
        source = {"id": event.chat_id, "name": "FırsatlarCo",
                  "username": None, "requested": "@firsatlarco"}
        self.assertEqual(bot.build_message_link(event, source),
                         "https://t.me/firsatlarco/101")


class QuotedDetectedLineCleanupTest(unittest.TestCase):
    """3) Bonus/ilk adım: kesin SATIR silinir; şüpheli satır AYNEN kalır."""

    def test_confident_product_title_line_is_removed(self):
        composed = _compose(DYSON, clean=True)
        self.assertEqual(composed["header"],
                         "₺12.999 — Dyson V8 Origin Kablosuz Süpürge")
        self.assertNotIn("Dyson V8 Origin Kablosuz Süpürge", composed["body"])
        self.assertIn("🏷️ 12.999 TL", composed["body"])
        self.assertIn(constants.PRODUCT_LINK_TEXT, composed["appendix"])
        # Başlık/fiyat/link kimliği ORİJİNAL mesajdan hesaplanmaya devam eder.
        obj = _message(DYSON)
        before = smarttitle.smart_dedup_title(obj)
        _compose_obj(obj, clean=True)
        self.assertEqual(smarttitle.smart_dedup_title(obj), before)
        self.assertEqual(obj.message, DYSON)

    def test_confident_standalone_coupon_code_line_is_removed(self):
        composed = _compose(KOZMODA, clean=True)
        self.assertIn("KOZMODA150", composed["header"])
        self.assertNotIn("\nKOZMODA150\n", "\n" + composed["body"] + "\n")
        self.assertIn("150 TL indirim kodu:", composed["body"])
        self.assertIn(constants.PRODUCT_LINK_TEXT, composed["appendix"])

    def test_uncertain_standalone_code_line_stays(self):
        composed = _compose(SOLO, clean=True)
        self.assertIn("TERTEMİZ75", composed["body"],
                      "_coupon_codes bu kodu doğrulayamıyorsa satır SİLİNMEZ")
        self.assertEqual(composed["header"],
                         "₺234 — Solo Ultra Tuvalet Kağıdı 40'lı")

    def test_uncertain_product_title_stays(self):
        composed = _compose(TWO_PRODUCTS, clean=True)
        self.assertEqual(composed["body"], TWO_PRODUCTS)

    def test_inline_coupon_code_line_stays(self):
        composed = _compose(CALVIN, clean=True)
        self.assertIn("MODADA250 Kodunu sepette kullanın.", composed["body"])
        self.assertEqual(composed["header"],
                         "₺1.562 — Calvin Klein LOW PRO CUPS WT SUEDE MG Spor AyakkabıKadın 37")

    def test_linked_title_line_stays(self):
        title = "Dyson V8 Origin Kablosuz Süpürge"
        entity = _text_link(0, sanitize.utf16_length(title), "https://www.hepsiburada.com/p")
        composed = _compose(DYSON, clean=False)
        self.assertIn(title, composed["body"])
        linked = _compose(DYSON, clean=True, entities=[entity])
        self.assertEqual(linked["body"], composed["body"],
                         "linkli ürün adı satırı alıntının tek taşıyıcısıdır, silinmez")

    def test_linked_code_line_stays(self):
        start = sanitize.utf16_length(KOZMODA[:KOZMODA.index("KOZMODA150")])
        entity = _text_link(start, sanitize.utf16_length("KOZMODA150"), "https://app.hb.biz/kod")
        composed = _compose(KOZMODA, clean=True, entities=[entity])
        self.assertIn("KOZMODA150", composed["body"])

    def test_cleanup_can_be_turned_off(self):
        composed = _compose(DYSON, clean=False)
        self.assertEqual(composed["body"], DYSON)

    def test_detected_lines_do_not_empty_the_quote(self):
        text = "Dyson V8 Origin Kablosuz Süpürge"
        composed = _compose(text, clean=True)
        self.assertEqual(composed["body"], text)

    def test_coupon_code_detection_is_narrow(self):
        self.assertEqual(quotes.quoted_code_lines(KOZMODA), {"KOZMODA150"})
        self.assertEqual(quotes.quoted_code_lines(SOLO), set())
        self.assertEqual(quotes.quoted_code_lines(PELLA), set())
        self.assertEqual(quotes.quoted_code_lines(CALVIN), set())


class RuntimeConfigTest(unittest.TestCase):
    """Yeni bayrak config.json'dan okunur; alan yoksa kapalı kalır."""

    GLOBALS = (
        "SMART_HEADER", "FAST_NOTIFY", "SLOW_REPORT_SECONDS", "FAST_POLL", "FAST_POLL_SECONDS",
        "FILTER_INCLUDE", "FILTER_EXCLUDE", "FILTER_MODE", "ADMIN_IDS",
        "FILTER_INCLUDE_ENABLED", "FILTER_EXCLUDE_ENABLED", "DELIVERY_CHAIN", "MAX_MEDIA_MB",
        "LINK_APPENDIX_MODE", "LINK_KINDS", "BOT_LINK_KINDS", "MESSAGE_LINK_LINE",
        "SOURCE_FOOTER", "NOTIFY_MEDIA", "NOTIFY_BOT_TOKEN", "SINGLE_MESSAGE",
        "CLEAN_COMMANDS", "CLEAN_QUOTE_DUPLICATES", "DEDUP_ENABLED",
        "DEDUP_WINDOW_SECONDS", "DEDUP_SCAN_LIMIT",
    )

    def test_flag_defaults_off_and_live_config_enables_it(self):
        config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        self.assertTrue(config["clean_quote_duplicates"])
        snapshot = {name: getattr(bot, name, None) for name in self.GLOBALS}
        with mock.patch.multiple(bot, create=True, **snapshot):
            bot.apply_runtime_config({"clean_quote_duplicates": True})
            self.assertTrue(bot.CLEAN_QUOTE_DUPLICATES)
            bot.apply_runtime_config({})
            self.assertFalse(bot.CLEAN_QUOTE_DUPLICATES)


if __name__ == "__main__":
    unittest.main()
