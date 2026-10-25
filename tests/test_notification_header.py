"""Bildirim üst satırı: ``💰 fiyat — ürün adı`` düzeni ve arama/dedup izolasyonu.

Kullanıcı isteği iki parçalıydı:

1. Fiyat, ürün adıyla birlikte ve ONDA ÖNCE tek satırda dursun (bildirim
   kesildiğinde fiyat + adın ilk kelimeleri görünsün),
2. Google / Akakçe / Cimri aramaları BOZULMASIN — arama daima fiyatsız adla
   yapılsın; fiyat bulunamayınca da uzun uyarı yerine yalnızca ``?`` yazılsın.

Bu dosya ikinci maddeyi hem birim hem uçtan uca (gerçek gönderim → açılış
taraması) kilitler.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bot  # noqa: E402
import price_filter as pf  # noqa: E402
from test_integration import (  # noqa: E402
    ADMIN_ID, GROUP_ID, FakeClient, FakeEvent, MainHarness, reset_state,
)

SOURCE = (
    "🔥 FIRSAT 🔥\n\n"
    "Philips Airfryer XXL 6.2L Siyah Fritöz HD9880/90\n\n"
    "Eski fiyat: 19.999 TL\n"
    "💰 Fiyat: 12.299 TL\n"
    "%38 indirim · kargo bedava\n\n"
    "🛒 https://www.amazon.com.tr/dp/B0XXXX"
)


def event(text=SOURCE):
    return SimpleNamespace(message=text, raw_text=text, entities=[], media=None,
                           reply_markup=None, chat_id=1, id=1)


class HeaderLineTests(unittest.TestCase):
    """Üst satırın kurulum/ayrıştırma yardımcıları."""

    def test_build_header_line_order(self):
        self.assertEqual(
            bot.build_header_line("💰 1.299 TL", "Philips Airfryer XXL 6.2L"),
            "💰 1.299 TL — Philips Airfryer XXL 6.2L",
        )
        self.assertEqual(bot.build_header_line("💰 1.299 TL", ""), "💰 1.299 TL")
        self.assertEqual(bot.build_header_line("", "Philips Airfryer"), "Philips Airfryer")
        self.assertEqual(bot.build_header_line("", ""), "")

    def test_split_header_roundtrip(self):
        for line in [
            "💰 1.299 TL — Philips Airfryer XXL 6.2L",
            "💰 ? — Philips Airfryer XXL 6.2L",
            "💰 12.299,50 TL — TCL 65 inç — QLED",   # adın içindeki ayraç bölünmez
        ]:
            with self.subTest(line=line):
                price, title = bot.split_header_line(line)
                self.assertTrue(price.startswith("💰"), line)
                self.assertNotIn("💰", title)
                self.assertEqual(bot.build_header_line(price, title), line)

    def test_plain_and_legacy_lines_are_not_mistaken_for_headers(self):
        # Başlık-önce düzen: ayraç yok → satırın tamamı addır.
        self.assertEqual(bot.split_header_line("Philips Airfryer XXL"), ("", "Philips Airfryer XXL"))
        # Eski tek başına fiyat satırı: ad taşımaz.
        self.assertEqual(bot.split_header_line("💰 Fiyat tespit edilemedi"),
                         ("💰 Fiyat tespit edilemedi", ""))
        self.assertEqual(bot.split_header_line(""), ("", ""))
        self.assertEqual(bot.split_header_line(None), ("", ""))

    def test_fit_header_keeps_price_first(self):
        title = "Samsung Galaxy S24 Ultra 256 GB Titan Siyah Kılıflı model"
        fitted = bot.fit_header_line("💰 41.999 TL", title, 40)
        self.assertLessEqual(len(fitted), 40)
        self.assertTrue(fitted.startswith("💰 41.999 TL — "), fitted)
        self.assertTrue(fitted.endswith("…"), fitted)
        self.assertIn(title[:8], fitted)
        self.assertEqual(bot.fit_header_line("💰 41.999 TL", title, 500),
                         bot.build_header_line("💰 41.999 TL", title))
        self.assertEqual(bot.fit_header_line("💰 41.999 TL", title, 0), "")

    def test_header_search_title_strips_price(self):
        self.assertEqual(
            bot.header_search_title("💰 1.299 TL — Philips Airfryer XXL 6.2L"),
            "Philips Airfryer XXL 6.2L",
        )
        self.assertEqual(bot.header_search_title("💰 ?"), "")      # aranacak ad yok
        self.assertEqual(bot.header_search_title("Philips Airfryer XXL"), "Philips Airfryer XXL")
        self.assertEqual(bot.header_search_title(""), "")


class NotificationLayoutTests(unittest.TestCase):
    """compose_message'ın ürettiği üst satır."""

    def setUp(self):
        self.addCleanup(setattr, bot, "SMART_HEADER", bot.SMART_HEADER)
        bot.SMART_HEADER = True

    def test_price_first_then_name_on_one_line(self):
        composed = bot.compose_message(event(), message_link="https://t.me/firsatz/9",
                                       source_name="FırsatZ")
        header = composed["header"]
        self.assertEqual(header, "💰 12.299 TL — Philips Airfryer XXL 6.2L Siyah Fritöz HD9880/90")
        self.assertEqual(composed["text"].splitlines()[0], header)
        self.assertNotIn("\n\n💰 ", composed["text"], "ayrı fiyat bloğu artık yok")
        # Fiyat, kesilen ön izlemenin en solunda kalır.
        self.assertLess(header.index("12.299"), header.index("Philips"))
        self.assertEqual(composed["title"], "Philips Airfryer XXL 6.2L Siyah Fritöz HD9880/90")
        self.assertNotIn("12.299", composed["title"])

    def test_missing_price_is_just_a_question_mark(self):
        text = "Philips Airfryer XXL 6.2L Siyah Fritöz\n🎯 Kampanya başladı, detaylar linkte"
        composed = bot.compose_message(event(text))
        self.assertEqual(composed["header"], "💰 ? — Philips Airfryer XXL 6.2L Siyah Fritöz")
        self.assertNotIn("Fiyat tespit edilemedi", composed["text"])
        self.assertNotIn("bulunamadı", composed["text"])

    def test_price_only_header_when_title_is_missing(self):
        text = "Fiyat: 1.299 TL\n\n#firsat #amazon"
        composed = bot.compose_message(event(text))
        self.assertEqual(composed["header"], "💰 1.299 TL")
        self.assertEqual(bot.split_header_line(composed["header"])[1], "")
        self.assertIsNone(composed["product_title"], "üst satırda uydurma bir ad yok")

    def test_bold_covers_whole_header(self):
        composed = bot.compose_message(event())
        bold = next(e for e in composed["entities"] if type(e).__name__ == "MessageEntityBold")
        text = composed["text"]
        self.assertEqual(
            text.encode("utf-16-le")[bold.offset * 2:(bold.offset + bold.length) * 2].decode("utf-16-le"),
            composed["header"],
        )

    def test_header_survives_caption_limit(self):
        long_body = SOURCE + "\n\n" + ("Açıklama satırı. " * 120)
        composed = bot.compose_message(event(long_body), limit=bot.CAPTION_LIMIT - 24)
        self.assertEqual(composed["text"].splitlines()[0], composed["header"])
        self.assertTrue(composed["header"].startswith("💰 12.299 TL — "))
        self.assertLessEqual(len(composed["text"]), bot.CAPTION_LIMIT - 24)

    def test_smart_header_off_keeps_old_layout(self):
        bot.SMART_HEADER = False
        composed = bot.compose_message(event())
        self.assertEqual(composed["header"], "Philips Airfryer XXL 6.2L Siyah Fritöz HD9880/90")
        self.assertEqual(composed["price"], "")
        self.assertNotIn("💰 ?", composed["text"])


class SearchIsolationTests(unittest.TestCase):
    """Arama sorguları fiyatlı üst satırdan ETKİLENMEZ."""

    def setUp(self):
        self.addCleanup(setattr, bot, "SMART_HEADER", bot.SMART_HEADER)
        bot.SMART_HEADER = True

    def queries(self, obj):
        markup = bot.build_inline_keyboard(obj) or {"inline_keyboard": []}
        result = {}
        for row in markup["inline_keyboard"]:
            for button in row:
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(button["url"]).query).get("q", [""])[0]
                result[button["text"]] = query
        return result

    def test_all_three_services_search_the_price_free_name(self):
        expected = bot._search_query(event())
        self.assertTrue(expected)
        self.assertNotIn("12.299", expected)
        self.assertNotIn("19.999", expected)
        queries = self.queries(event())
        self.assertEqual(set(queries), {"Google Alışveriş", "Akakçe'de ara", "Cimri'de ara"})
        for label, query in queries.items():
            with self.subTest(service=label):
                self.assertEqual(query, expected)
                for price in ("12.299", "19.999", "1.299", "%38"):
                    self.assertNotIn(price, query)
                self.assertNotIn("💰", query)

    def test_notification_header_never_reaches_a_search_url(self):
        """Üst satırın tamamı yanlışlıkla sorgu olursa bile fiyat düşer."""
        header = bot.compose_message(event())["header"]
        self.assertIn("12.299 TL", header)
        expected = bot._search_query(event())
        for service, _label in bot.PRICE_SEARCH_BUTTONS:
            with self.subTest(service=service):
                url = bot._price_search_url(service, header)
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("q", [""])[0]
                self.assertEqual(query, expected)
                self.assertNotIn("12.299", urllib.parse.unquote_plus(url))

    def test_dedup_key_is_built_from_the_price_free_name(self):
        offer = (pf.extract_price(SOURCE).value, "")
        live_title = bot.smart_dedup_title(event())
        header_title = bot.split_header_line(bot.compose_message(event())["header"])[1]
        self.assertEqual(bot.dedup_offer_key(header_title, offer),
                         bot.dedup_offer_key(live_title, offer))
        # Eski (başlık üstte, fiyat ayrı satırda) bildirim de aynı anahtarı verir.
        legacy_title = bot.split_header_line(live_title)[1] or live_title
        self.assertEqual(bot.dedup_offer_key(legacy_title, offer),
                         bot.dedup_offer_key(live_title, offer))


class HeaderHistoryDedupTests(MainHarness, unittest.TestCase):
    """Gönderilen yeni üst satır, açılış taramasında aynı fırsata bağlar."""

    def start(self, history=()):
        FakeClient.default_history = list(history)
        path = self._write_config(source_chats=["@firsatz"], include_keywords=[], smart_header=True)
        self.addCleanup(os.unlink, path)
        return self._run_main(path)

    def dispatch(self, client, offers):
        async def run():
            with mock.patch.object(bot, "DEDUP_EDIT_MIN_INTERVAL", 0):
                for source_id, message_id, text, polled in offers:
                    event = FakeEvent(source_id, 7, text, message_id=message_id, media=False)
                    event._fast_poll = polled
                    await client.handlers[1][1](event)
                    await asyncio.sleep(0.02)
        asyncio.run(run())

    def test_delivered_header_is_reused_after_restart(self):
        reset_state()
        self.addCleanup(reset_state)
        self.addCleanup(setattr, FakeClient, "default_history", [])
        client = self.start()
        source = next(iter(bot.SOURCE_IDS))
        self.dispatch(client, [(source, 101, "Philips Airfryer XXL 6.2L\nFiyat: 12.299 TL", False)])
        self.assertEqual(len(client.delivered), 1)
        delivered = client.delivered[0][1]
        self.assertTrue(delivered.startswith("💰 12.299 TL — Philips Airfryer XXL 6.2L"), delivered)

        reset_state()
        restarted = self.start([(GROUP_ID, delivered, ADMIN_ID, None, None, [], 777)])
        self.assertEqual(len(bot.DEDUP_CACHE), 1, "yeni üst satır önbelleğe alınmalı")
        entry = next(iter(bot.DEDUP_CACHE.values()))
        self.assertEqual(entry["title"], "Philips Airfryer XXL 6.2L")
        self.assertNotIn("💰", entry["title"])
        self.assertNotIn("12.299 TL —", entry["title"])
        self.dispatch(restarted, [(source, 102, "Philips Airfryer XXL 6.2L\nFiyat: 12.299 TL", False)])
        self.assertEqual(len(restarted.delivered), 0, "aynı ürün yeniden bildirilmemeli")
        self.assertEqual(bot.STATS["deduped"], 1)


if __name__ == "__main__":
    unittest.main()
