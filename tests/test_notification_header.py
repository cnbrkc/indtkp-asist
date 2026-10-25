"""Bildirim üst satırı: ``56₺ - Ürün Adı`` / ``❓ - Ürün Adı`` tasarımı.

Kullanıcı isteğinin kesin biçimi:

    56₺ - Penti Kız Çocuk Basic Colorful 4lü Patik Çorap
    ❓ - Penti Kız Çocuk Basic Colorful 4lü Patik Çorap     ← fiyat bulunamayınca

Yani: fiyatın önünde 💰 YOK, ``56 TL`` değil ``56₺``, ayraç ``-`` (em-dash
değil), fiyat yokken tek başına ``❓``. İlk satır fiyat + adı birlikte taşır;
alt gövde, kampanya açıklaması, linkler ve kaynak adı değişmeden kalır.

Bu dosya üç şeyi kilitler:
1. tam metin eşitliğiyle üst satır tasarımı,
2. gerçek inline-buton zincirinde (build_inline_keyboard → _price_search_url)
    sorgunun DECODE edilmiş ``q`` parametresinin birebir fiyatsız ad olması,
3. gönderilmiş bildirimlerin (yeni + iki eski biçim) açılış taramasında aynı
    fırsat anahtarına dönmesi; ayrıca entity/uzunluk sınırları.
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
import test_integration as ti  # noqa: E402
from telethon.tl import types  # noqa: E402
from test_integration import (  # noqa: E402
    ADMIN_ID, GROUP_ID, FakeClient, FakeEvent, MainHarness, reset_state,
)

TITLE = "Penti Kız Çocuk Basic Colorful 4lü Patik Çorap"
SOURCE = f"🔥 FIRSAT 🔥\n\n{TITLE}\n\n🎯 56₺\n%28 indirim · kargo bedava\n\n🛒 https://amazon.com.tr/dp/B0X"


def event(text=SOURCE, entities=None, **extra):
    data = {"message": text, "raw_text": text, "entities": entities or [],
            "media": None, "reply_markup": None, "chat_id": 1, "id": 1}
    data.update(extra)
    return SimpleNamespace(**data)


def utf16_slice(text: str, offset: int, length: int) -> str:
    raw = text.encode("utf-16-le")
    return raw[offset * 2:(offset + length) * 2].decode("utf-16-le")


def decoded_queries(markup: dict | None) -> dict[str, str]:
    """İnceleme isteği üzerine: URL metninde substring aramak yerine ``q``yu çöz."""
    out: dict[str, str] = {}
    for row in (markup or {}).get("inline_keyboard", []):
        for button in row:
            params = urllib.parse.parse_qs(urllib.parse.urlsplit(button["url"]).query)
            out[button["text"]] = params.get("q", [""])[0]
    return out


class HeaderDesignTests(unittest.TestCase):
    """1) Tam metin eşitliğiyle üst satır tasarımı."""

    def setUp(self):
        self.addCleanup(setattr, bot, "SMART_HEADER", bot.SMART_HEADER)
        bot.SMART_HEADER = True

    def test_exact_requested_line(self):
        composed = bot.compose_message(event(), message_link="https://t.me/firsatz/1",
                                       source_name="FırsatZ")
        self.assertEqual(composed["header"], f"56₺ - {TITLE}")
        self.assertEqual(composed["text"].splitlines()[0], f"56₺ - {TITLE}")
        self.assertEqual(composed["price"], "56₺")
        self.assertEqual(composed["title"], TITLE)
        # 💰 öneki yok, "TL" yazımı yok, em-dash yok.
        self.assertNotIn("💰", composed["header"])
        self.assertNotIn("TL", composed["header"])
        self.assertNotIn("—", composed["header"])

    def test_missing_price_is_only_the_mark(self):
        text = f"{TITLE}\n\n🎯 Sepete özel indirim başladı, linkte"
        composed = bot.compose_message(event(text))
        self.assertEqual(composed["header"], f"❓ - {TITLE}")
        self.assertEqual(composed["price"], "❓")
        self.assertNotIn("Fiyat tespit edilemedi", composed["text"])
        self.assertNotIn("bulunamadı", composed["text"])
        self.assertNotIn("💰", composed["text"].splitlines()[0])

    def test_lower_cases_keep_the_body_untouched(self):
        composed = bot.compose_message(event(), message_link="https://t.me/firsatz/1",
                                       source_name="FırsatZ")
        body = bot.message_text(bot.sanitize_message(event()))
        self.assertIn(body, composed["text"], "gövde olduğu gibi (kampanya açıklaması dahil)")
        self.assertIn("🛒 https://amazon.com.tr/dp/B0X", composed["text"])
        self.assertTrue(composed["text"].endswith("FırsatZ"), "kaynak adı en altta")
        self.assertIn("🔗 Mesajı Gör: https://t.me/firsatz/1", composed["text"])

    def test_price_number_formats_reach_the_header(self):
        for source, expected in [
            ("Philips Airfryer XXL 6.2L\nFiyat: 12.299 TL", "12.299₺"),
            ("Philips Airfryer XXL 6.2L\nFiyat: 12.299,90 TL", "12.299,90₺"),
            ("Philips Airfryer XXL 6.2L\nFiyat: 56 TL", "56₺"),
            ("Philips Airfryer XXL 6.2L\nFiyat: 56₺", "56₺"),
            ("Philips Airfryer XXL 6.2L\nFiyat: ₺56", "56₺"),
        ]:
            with self.subTest(source=source):
                composed = bot.compose_message(event(source))
                self.assertTrue(composed["header"].startswith(expected + " - "), composed["header"])
                self.assertEqual(composed["price"], expected)

    def test_low_confidence_price_is_never_carried_up(self):
        # Ayrıştırıcı güven kararına dokunulmadı: low/none → başlıkta ❓.
        for source, confidence in [("Cam Bardak 50 TL\nPamuk Havlu 100 TL", None),
                                   ("Deneme Havlu\nEski fiyat: 200 TL", "low"),
                                   ("Deneme Havlu\nSepette 50 TL indirim", "none")]:
            with self.subTest(source=source):
                info = pf.extract_price(source)
                if confidence:
                    self.assertEqual(info.confidence, confidence)
                self.assertNotIn(info.confidence, {"medium", "high"})
                composed = bot.compose_message(event(source))
                self.assertEqual(composed["price"], "❓")
                for number in ("50", "100", "200"):
                    self.assertNotIn(number + "₺", composed["header"], "düşük güven tutar üst satıra taşınmaz")

    def test_header_price_agrees_with_body_offer_terms(self):
        # Başlıkta gösterilen fiyat ile gövdeden seçilen fiyat çelişmemeli.
        for source in [SOURCE,
                       "Xiaomi Redmi Note 13 Pro 256GB\nFiyat: 18.499 TL\nEski: 24.999 TL",
                       "Philips Airfryer XXL 6.2L\n💰 Fiyat: 12.299 TL / 2 adet alımda 11.999 TL"]:
            with self.subTest(source=source.splitlines()[0]):
                composed = bot.compose_message(event(source))
                terms_price, _ = bot.offer_terms(event(source))
                if terms_price is None:
                    self.assertEqual(composed["price"], "❓")
                else:
                    self.assertEqual(composed["price"], bot.header_price_text(terms_price))

    def test_smart_header_off_is_unchanged(self):
        bot.SMART_HEADER = False
        composed = bot.compose_message(event())
        self.assertEqual(composed["header"], TITLE.replace("4lü Patik Çorap", "4lü Patik Çorap"))
        self.assertEqual(composed["price"], "")
        self.assertNotIn("❓", composed["text"])
        self.assertNotIn("56₺", composed["text"].splitlines()[0])

    def test_lone_mark_without_title_is_not_printed(self):
        # Ne fiyat ne ad: havada kalan tek ❓ satırı kurulmaz, gövde yine gider.
        composed = bot.compose_message(event("🎯 İŞBİRLİĞİ"))
        self.assertEqual(composed["header"], "")
        self.assertEqual(composed["price"], "")
        self.assertNotIn("❓", composed["text"])

    def test_price_only_header_without_title(self):
        composed = bot.compose_message(event("Fiyat: 1.299 TL\n\n#firsat #amazon"))
        self.assertEqual(composed["header"], "1.299₺")
        self.assertIsNone(composed["product_title"])
        self.assertTrue(bot.composed_has_content(composed), "salt fiyatlı üst satır içeriktir")


class HeaderHelperTests(unittest.TestCase):
    """2a) Merkezi sabitler ve yardımcılar."""

    def test_central_constants(self):
        self.assertEqual(bot.HEADER_SEPARATOR, " - ")
        self.assertEqual(bot.PRICE_CURRENCY_MARK, "₺")
        self.assertEqual(bot.PRICE_MISSING_MARK, "❓")

    def test_header_price_text(self):
        self.assertEqual(bot.header_price_text(56), "56₺")
        self.assertEqual(bot.header_price_text(12299), "12.299₺")
        self.assertEqual(bot.header_price_text(12299.9), "12.299,90₺")
        self.assertEqual(bot.header_price_text(0), "0₺")
        self.assertEqual(bot.header_price_text(None), "❓")
        # Ayrıştırıcı ve gövde yazımı ("TL") değişmedi:
        self.assertEqual(pf.format_price(56), "56 TL")

    def test_is_header_price(self):
        for value in ["56₺", "₺56", "12.299₺", "12.299,90 TL", "₺1.299", "56 TL", "❓"]:
            with self.subTest(value=value):
                self.assertTrue(bot.is_header_price(value))
        for value in ["", "Penti", "2024", "56 TL - Penti", "Fiyat tespit edilemedi",
                      "4lü Patik", "12.299 TL ve üzeri"]:
            with self.subTest(value=value):
                self.assertFalse(bot.is_header_price(value))

    def test_split_roundtrip_and_no_mis_split(self):
        lines = [
            f"56₺ - {TITLE}",
            f"❓ - {TITLE}",
            "19.999₺ - Beko B3RFS280DXF - No Frost 56 cm Buzdolabı",
            "12.299,90₺ - Xiaomi Redmi — Pro 256GB",
        ]
        for line in lines:
            with self.subTest(line=line):
                price, title = bot.split_header_line(line)
                self.assertTrue(bot.is_header_price(price), line)
                self.assertNotIn("₺", title)
                self.assertNotIn("❓", title)
                self.assertEqual(bot.build_header_line(price, title), line)

    def test_titles_are_not_split_when_head_is_not_a_price(self):
        for line in [TITLE, "Samsung 2024 - Yeni Sezon Kablo", "Fiat Linea - 2015",
                     "Beko B3RFS280DXF - No Frost 56 cm Genişlik Buzdolabı"]:
            with self.subTest(line=line):
                self.assertEqual(bot.split_header_line(line), ("", line))

    def test_legacy_header_shapes_still_read(self):
        # Bu dalda üretilen ara sürüm ve ondan önceki iki düzen (yalnız OKUMA).
        self.assertEqual(bot.split_header_line("💰 12.299 TL — Philips Airfryer XXL"),
                         ("12.299 TL", "Philips Airfryer XXL"))
        self.assertEqual(bot.split_header_line("💰 Fiyat tespit edilemedi"),
                         ("💰 Fiyat tespit edilemedi", ""))
        self.assertEqual(bot.split_header_line("💰Fiyat: 5 TL"), ("💰Fiyat: 5 TL", ""))
        self.assertEqual(bot.split_header_line("Philips Airfryer XXL"), ("", "Philips Airfryer XXL"))

    def test_fit_and_search_helpers(self):
        self.assertEqual(bot.fit_header_line("56₺", "", 90), "56₺")
        fitted = bot.fit_header_line("56₺", "A" * 80, 30)
        self.assertTrue(fitted.startswith("56₺ - " + "A" * 20), fitted)
        self.assertLessEqual(len(fitted), 30)
        self.assertEqual(bot.fit_header_line("56₺", "A" * 80, 0), "")
        self.assertEqual(bot.header_search_title("56₺ - Penti Patik"), "Penti Patik")
        self.assertEqual(bot.header_search_title("❓ - Penti Patik"), "Penti Patik")
        self.assertEqual(bot.header_search_title("56₺"), "")      # aranacak ad yok
        self.assertEqual(bot.header_search_title("❓"), "")        # yalnız işaret
        self.assertEqual(bot.header_search_title(TITLE), TITLE)
        self.assertEqual(bot.header_search_title(""), "")


class SearchChainTests(unittest.TestCase):
    """2b) Üç servisin GERÇEK buton üretim zinciri (yardımcı fonksiyon değil)."""

    def setUp(self):
        self.addCleanup(setattr, bot, "SMART_HEADER", bot.SMART_HEADER)
        bot.SMART_HEADER = True

    def test_three_services_search_exactly_the_price_free_name(self):
        queries = decoded_queries(bot.build_inline_keyboard(event()))
        self.assertEqual(set(queries), {"Google Alışveriş", "Akakçe'de ara", "Cimri'de ara"})
        for label, query in queries.items():
            with self.subTest(service=label):
                self.assertEqual(query, TITLE)
                for leak in ("56", "₺", "TL", "❓", "💰", "%28", " - "):
                    self.assertNotIn(leak, query)

    def test_query_identical_before_and_after_the_header_change(self):
        # Kaynak mesajdan temizleme davranışı bozulmadı: sorgu SMART_HEADER'dan bağımsız.
        with patch.object(bot, "SMART_HEADER", False):
            off = decoded_queries(bot.build_inline_keyboard(event()))
        on = decoded_queries(bot.build_inline_keyboard(event()))
        self.assertEqual(off, on)

    def test_price_first_source_still_searches_the_name(self):
        text = "💰 3.999₺ - Arzum ARZ0652 Twist Copper Izgara\n\n🔥 Kaçmaz!"
        queries = decoded_queries(bot.build_inline_keyboard(event(text)))
        for label, query in queries.items():
            with self.subTest(service=label):
                self.assertNotIn("3.999", query)
                self.assertNotIn("₺", query)
                self.assertIn("Arzum", query)

    def test_composed_notification_as_input_never_leaks_price_or_mark(self):
        # Kablolama hatası senaryosu: bildirim metni arama zincirine girse bile
        # fiyat/para birimi/ayraç/❓ sorguya taşınmaz.
        header = bot.compose_message(event())["header"]
        self.assertIn("56₺", header)
        expected = bot._search_query(event())
        for service, _ in bot.PRICE_SEARCH_BUTTONS:
            with self.subTest(service=service):
                url = bot._price_search_url(service, header)
                self.assertEqual(decoded_queries({"inline_keyboard": [[{"text": service, "url": url}]]})[service],
                                 expected)
                self.assertNotIn("56", urllib.parse.unquote_plus(url))
                self.assertNotIn("❓", urllib.parse.unquote_plus(url))

    def test_no_bogus_buttons_for_empty_or_price_only_titles(self):
        for text in ["", "Fiyat: 1.299 TL", "1.299₺", "🎯", "%50 indirim", "❓"]:
            with self.subTest(text=text):
                markup = bot.build_inline_keyboard(event(text))
                self.assertIsNone(markup, "yapay/boş arama düğmesi üretilmez")

    def test_existing_source_search_button_is_preserved_not_duplicated(self):
        # Kaynakta Akakçe linki varsa o buton korunur, ikincisi üretilmez.
        kb = {"rows": [[SimpleNamespace(url="https://www.akakce.com/arama/?q=Philips+Airfryer",
                                       text=SimpleNamespace(text="Akakçe"))]]}
        markup = bot.build_inline_keyboard(event(reply_markup=kb))
        rows = (markup or {}).get("inline_keyboard", [])
        akakce = [b for row in rows for b in row if "akakce.com" in b["url"]]
        self.assertEqual(len(akakce), 1, akakce)


class EntityAndLimitTests(unittest.TestCase):
    """4) Gerçek gönderim parametreleri: entity offset'leri ve sınırlar."""

    def setUp(self):
        self.addCleanup(setattr, bot, "SMART_HEADER", bot.SMART_HEADER)
        bot.SMART_HEADER = True

    def bot_payload_entities(self, composed):
        """notify_offer'ın Bot API'ye verdiği entity listesi (hesap yolu da aynı offset)."""
        return (bot.bot_api_entities(composed["message"], composed["content"])
                + bot.source_name_entity(composed))

    def test_bold_covers_first_line_in_utf16(self):
        # Türkçe karakter + sayı + tire + kesme işareti: UTF-16 birimiyle karakter
        # sayısı farklı olsaydı bu test patlardı.
        text = "Pentı Gi̇yim 4’lü - Patik Çorap 56cm\nFiyat: 56₺"
        composed = bot.compose_message(event(text), source_name="Grup")
        bold = next(e for e in self.bot_payload_entities(composed) if e["type"] == "bold")
        self.assertEqual(utf16_slice(composed["text"], bold["offset"], bold["length"]),
                         composed["header"])
        self.assertEqual(utf16_slice(composed["text"], bold["offset"], bold["length"]).count("\n"), 0,
                         "kalın alan yalnız ilk satır")

    def test_body_entities_shift_with_the_new_header(self):
        body = f"{TITLE}\n🔥 Fırsata Git 🔥"
        word = "Fırsata"
        start = len(body[:body.index(word)].encode("utf-16-le")) // 2
        entities = [types.MessageEntityTextUrl(offset=start, length=len(word),
                                               url="https://t.me/go")]
        composed = bot.compose_message(event(body, entities=entities))
        links = [e for e in self.bot_payload_entities(composed) if e["type"] == "text_link"]
        self.assertEqual(len(links), 1)
        sliced = utf16_slice(composed["text"], links[0]["offset"], links[0]["length"])
        self.assertEqual(sliced, word, "gövde entity'si yeni başlık uzunluğuna göre kaymalı")

    def test_astral_emoji_in_the_body_still_offsets_correctly(self):
        body = f"{TITLE}\n🛍️🔥 Alışveriş başladı ✅"
        word = "Alışveriş"
        start = len(body[:body.index(word)].encode("utf-16-le")) // 2
        entities = [types.MessageEntityBold(offset=start, length=len(word))]
        composed = bot.compose_message(event(body, entities=entities))
        text_links = [e for e in self.bot_payload_entities(composed) if e["type"] == "bold"]
        # 0. index kaynak gövdedeki bold; kaydırılmış hâli sonradan gelir.
        sliced = [utf16_slice(composed["text"], e["offset"], e["length"]) for e in text_links]
        self.assertIn(word, sliced, sliced)

    def test_text_and_caption_limits_hold_in_utf16(self):
        long_title = "Ürün " * 60
        for limit in (bot.CAPTION_LIMIT - 24, bot.MESSAGE_LIMIT - 200, 2000, 120, 40, 12):
            with self.subTest(limit=limit):
                composed = bot.compose_message(event(long_title + "\nFiyat: 56₺"), limit=limit)
                self.assertLessEqual(bot.utf16_length(composed["text"]), limit)
                self.assertTrue(composed["header"] in composed["text"] or not composed["header"])

    def test_truncation_keeps_price_and_the_distinguishing_name_prefix(self):
        composed = bot.compose_message(event(SOURCE), limit=45)
        self.assertTrue(composed["header"].startswith("56₺ - "), composed["header"])
        self.assertLessEqual(len(composed["header"]), 45)
        self.assertTrue(composed["header"].endswith("…"), composed["header"])
        self.assertIn(TITLE[:8], composed["header"])
        self.assertEqual(bot.split_header_line(composed["header"])[0], "56₺")

    def test_only_mark_when_nothing_fits(self):
        # Fiyat yoksa ve ad sığmıyorsa satır ❓ ile açılır/kapanır.
        composed = bot.compose_message(event(f"{TITLE}\n🎯 kupon var"), limit=18)
        self.assertTrue(composed["header"].startswith("❓"), composed["header"])
        self.assertLessEqual(len(composed["header"]), 18)


class DedupCompatibilityTests(unittest.TestCase):
    """3) Eski/yeni bildirim biçimleri gerçek dedup akışında."""

    def test_history_key_equals_live_key(self):
        live = bot.smart_dedup_title(event())
        header = bot.compose_message(event())["header"]
        from_header = bot.split_header_line(header)[1]
        terms = bot.offer_terms(event())
        self.assertEqual(bot.dedup_offer_key(from_header, terms), bot.dedup_offer_key(live, terms))
        # Eski biçim (başlık üstte, fiyat ayrı satırda) de aynı anahtarı verir.
        legacy_line = bot.split_header_line(live)[1] or live
        self.assertEqual(bot.dedup_offer_key(legacy_line, terms), bot.dedup_offer_key(live, terms))

    def test_price_change_and_name_change_are_not_confused(self):
        terms_56 = bot.offer_terms(event(SOURCE))
        other_price = event(SOURCE.replace("🎯 56₺", "🎯 49₺"))
        other_name = event(SOURCE.replace(TITLE, TITLE + " Siyah"))
        self.assertNotEqual(terms_56[0], bot.offer_terms(other_price)[0])
        self.assertEqual(bot.split_header_line(bot.compose_message(event())["header"])[1],
                         bot.split_header_line(bot.compose_message(other_price)["header"])[1],
                         "fiyat değişince ad değişmez")
        self.assertNotEqual(bot.split_header_line(bot.compose_message(other_name)["header"])[1],
                            TITLE, "ad değişince anahtar değişir")
        self.assertNotEqual(bot.dedup_offer_key(TITLE + " Siyah", terms_56),
                            bot.dedup_offer_key(TITLE, terms_56))


class NotificationHistoryFlowTests(MainHarness, unittest.TestCase):
    """3-b) Restart + açılış taraması: yeni ve eski bildirim metinleriyle."""

    def start(self, history=()):
        FakeClient.default_history = list(history)
        path = self._write_config(source_chats=["@firsatz"], include_keywords=[], smart_header=True)
        self.addCleanup(os.unlink, path)
        return self._run_main(path)

    def dispatch(self, client, offers):
        async def run():
            with mock.patch.object(bot, "DEDUP_EDIT_MIN_INTERVAL", 0):
                for source_id, message_id, text, polled in offers:
                    ev = FakeEvent(source_id, 7, text, message_id=message_id, media=False)
                    ev._fast_poll = polled
                    await client.handlers[1][1](ev)
                    await asyncio.sleep(0.02)
        asyncio.run(run())

    def delivered_text(self, client):
        return client.delivered[-1][1]

    def test_new_format_notification_merges_after_restart(self):
        reset_state()
        self.addCleanup(reset_state)
        self.addCleanup(setattr, FakeClient, "default_history", [])
        client = self.start()
        source = next(iter(bot.SOURCE_IDS))
        offer = f"{TITLE}\nFiyat: 56₺"
        self.dispatch(client, [(source, 101, offer, False)])
        delivered = self.delivered_text(client)
        self.assertTrue(delivered.startswith(f"56₺ - {TITLE}"), delivered)

        reset_state()
        restarted = self.start([(GROUP_ID, delivered, ADMIN_ID, None, None, [], 777)])
        entry = next(iter(bot.DEDUP_CACHE.values()))
        self.assertEqual(entry["title"], TITLE, "anahtar fiyatsız ad olmalı")
        self.dispatch(restarted, [(source, 102, offer, False)])
        self.assertEqual(len(restarted.delivered), 0, "aynı fırsat yeniden bildirilmemeli")
        self.assertEqual(bot.STATS["deduped"], 1)

    def test_previous_title_plus_price_line_history_still_merges(self):
        reset_state()
        self.addCleanup(reset_state)
        self.addCleanup(setattr, FakeClient, "default_history", [])
        old = (f"{TITLE}\n\n💰 56 TL\n\n🔗 Mesajı Gör: https://t.me/firsatz/1\n\nfirsatz")
        client = self.start([(GROUP_ID, old, ADMIN_ID, None, None, [], 888)])
        self.assertEqual(len(bot.DEDUP_CACHE), 1)
        self.assertEqual(next(iter(bot.DEDUP_CACHE.values()))["title"], TITLE)
        source = next(iter(bot.SOURCE_IDS))
        self.dispatch(client, [(source, 103, f"{TITLE}\nFiyat: 56₺", False)])
        self.assertEqual(len(client.delivered), 0, "eski biçimden gelen anahtar yeniyle eşleşmeli")
        self.assertEqual(bot.STATS["deduped"], 1)

    def test_oldest_fixed_layout_price_line_still_merges(self):
        reset_state()
        self.addCleanup(reset_state)
        self.addCleanup(setattr, FakeClient, "default_history", [])
        old = (f"{TITLE}\n\n💰Fiyat: 56 TL\n\n🔗 Mesajı Gör: https://t.me/firsatz/2\n\nfirsatz")
        client = self.start([(GROUP_ID, old, ADMIN_ID, None, None, [], 999)])
        self.assertEqual(len(bot.DEDUP_CACHE), 1)
        self.assertEqual(next(iter(bot.DEDUP_CACHE.values()))["title"], TITLE)
        source = next(iter(bot.SOURCE_IDS))
        self.dispatch(client, [(source, 104, f"{TITLE}\nFiyat: 56 TL", False)])
        self.assertEqual(len(client.delivered), 0)
        self.assertEqual(bot.STATS["deduped"], 1)

    def test_truncated_or_mark_only_history_is_not_indexed_with_wrong_key(self):
        reset_state()
        self.addCleanup(reset_state)
        self.addCleanup(setattr, FakeClient, "default_history", [])
        cases = {
            "kırpılmış ad": f"{TITLE[:40]}…\n\n🔗 Mesajı Gör: https://t.me/firsatz/3\n\nfirsatz",
            "salt ❓ üst satırı": f"❓\n\n🔗 Mesajı Gör: https://t.me/firsatz/4\n\nfirsatz",
            "eski salt uyarı satırı": ("💰 Fiyat tespit edilemedi\n\n🔗 Mesajı Gör: "
                                       "https://t.me/firsatz/5\n\nfirsatz"),
        }
        for name, text in cases.items():
            with self.subTest(case=name):
                reset_state()
                self.start([(GROUP_ID, text, ADMIN_ID, None, None, [], 1200)])
                # Güvenli ürün kimliği yok: gövdeden toplanan artıklanahtar
                # ("Mesajı Gör" gibi) üretilmemeli.
                self.assertEqual(bot.DEDUP_CACHE, {}, f"{name} yanlış anahtar üretti")


class RealNotificationPayloadTests(unittest.TestCase):
    """2/4) Gerçek handler zinciri: ``notify_offer`` → ``send_bot_ping`` payload'ı.

    Bu testler bildirim botuna GERÇEKTE giden metni, entity listesini ve inline
    klavyeyi yakalar; "kalınlık doğru satırda mı, arama sorgusunda fiyat var mı,
    caption sınıra uyuyor mu" soruları üretimin kendi yoluyla cevaplanır.
    """

    setUp = ti.DedupBotBadgeTest.setUp
    tearDown = ti.DedupBotBadgeTest.tearDown
    _patch = ti.DedupBotBadgeTest._patch
    _run = ti.DedupBotBadgeTest._run
    _send = ti.DedupBotBadgeTest._send

    def test_text_notification_payload(self):
        client = self._run(fast_notify=True, include_keywords=[], smart_header=True)
        self._send(client, SOURCE, media=False)
        self.assertEqual(len(self.ping_calls), 1, self.ping_calls)
        call = self.ping_calls[0]
        text = call["text"]
        self.assertEqual(text.splitlines()[0], f"56₺ - {TITLE}")
        self.assertNotIn("💰", text.splitlines()[0])
        bold = [item for item in call["entities"] if item["type"] == "bold"]
        self.assertEqual(utf16_slice(text, bold[0]["offset"], bold[0]["length"]),
                         f"56₺ - {TITLE}", "kalınlık tam olarak üst satırı kapsar")
        queries = decoded_queries(call["keyboard"])
        for label in ("Google Alışveriş", "Akakçe'de ara", "Cimri'de ara"):
            self.assertEqual(queries[label], TITLE, label)
        self.assertLessEqual(bot.utf16_length(text), bot.MESSAGE_LIMIT)

    def test_missing_price_notification_payload(self):
        client = self._run(fast_notify=True, include_keywords=[], smart_header=True)
        self._send(client, f"{TITLE}\n🎯 Sepete özel indirim başladı", media=False)
        call = self.ping_calls[-1]
        self.assertEqual(call["text"].splitlines()[0], f"❓ - {TITLE}")
        bold = [item for item in call["entities"] if item["type"] == "bold"]
        self.assertEqual(utf16_slice(call["text"], bold[0]["offset"], bold[0]["length"]),
                         f"❓ - {TITLE}")
        self.assertNotIn("Fiyat tespit edilemedi", call["text"])
        for label, query in decoded_queries(call["keyboard"]).items():
            self.assertNotIn("❓", query, label)
            self.assertEqual(query, TITLE, label)

    def test_media_caption_stays_within_limit_and_keeps_the_header(self):
        client = self._run(fast_notify=True, include_keywords=[], smart_header=True)
        long_body = SOURCE + "\n" + ("Açıklama satırı uzun tutuldu. " * 90)
        self._send(client, long_body)
        self.assertTrue(self.media_calls, "medya bildirimi denenmeli")
        call = self.media_calls[-1]
        caption = call["caption"]
        self.assertTrue(caption.startswith(f"56₺ - {TITLE}"), caption[:60])
        self.assertLessEqual(bot.utf16_length(caption), bot.CAPTION_LIMIT)
        bold = [item for item in call["entities"] if item["type"] == "bold"]
        self.assertEqual(utf16_slice(caption, bold[0]["offset"], bold[0]["length"]),
                         f"56₺ - {TITLE}", "kırpma sonrası bile kalınlık ilk satırda")
        for label, query in decoded_queries(call["keyboard"]).items():
            self.assertEqual(query, TITLE, label)


if __name__ == "__main__":
    unittest.main()
