"""Fiyat/başlık tespiti, DM fiyat kuralı ve yeni bildirim düzeni testleri."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bot  # noqa: E402
import price_filter as pf  # noqa: E402
from telethon.tl import types  # noqa: E402

# (mesaj, beklenen başlık | None=kontrol etme, beklenen fiyat | None=fiyat yok)
CASES = [
    ("Philips Hue Ampul\n0 TL\nhttps://x.com", "Philips Hue Ampul", 0),
    ("TCL 65C7K 4K QD-Mini LED Google TV\n🏷 29.999 TL\n500 TL kupon\nhttps://a.co/x",
     "TCL 65C7K 4K QD-Mini LED Google TV", 29999),
    ("🔥 FIRSAT 🔥\nSamsung Galaxy S24 256 GB Akıllı Telefon\n💰 Fiyat: 41.999 TL\n✅ Taksit: 12 x 3.500 TL\nSatıcı: Amazon",
     "Samsung Galaxy S24 256 GB Akıllı Telefon", 41999),
    ("Ürün X\nSepette 150 TL indirim\n1.299 TL", "Ürün X", 1299),
    ("LG 65UR781C 65 inç 4K TV\nEski: 44.999 TL\nYeni: 31.866 TL\nTaksit 2.655 TL",
     "LG 65UR781C 65 inç 4K TV", 31866),
    ("Xiaomi Redmi Buds 5 Pro 1.199 TL'ye düştü! Hepsiburada", "Xiaomi Redmi Buds 5 Pro", 1199),
    ("Dyson V12 Detect Slim Süpürge\n2.999 TL'den 1.999 TL'ye düştü", "Dyson V12 Detect Slim Süpürge", 1999),
    ("1.000 TL ve üzeri alışverişlerde 150 TL indirim\nTrendyol", None, None),
    ("Apple AirPods Pro 2\n6.499 TL + kargo bedava", "Apple AirPods Pro 2", 6499),
    ("📦 Ürün: Philips 3000 Serisi Airfryer HD9200/90\n💸 Fiyat: 2.899,90 TL\n📉 En düşük fiyat: 2.799 TL (60 gün)",
     "Philips 3000 Serisi Airfryer HD9200/90", 2899.9),
    ("[Amazon] Anker 20.000 mAh Powerbank\n599 TL\nKupon: 100 TL (otomatik uygulanır)",
     "Anker 20.000 mAh Powerbank", 599),
    ("Kupon ile 899 TL! Lego Classic 11011\nhttps://x", "Lego Classic 11011", 899),
    ("DIKKAT FİYAT HATASI\nLogitech MX Master 3S\n₺749", "Logitech MX Master 3S", 749),
    ("Sony WH-1000XM5 Kulaklık\n8.999 TL\n(Önceki fiyat 11.499 TL)", "Sony WH-1000XM5 Kulaklık", 8999),
    ("Nutella 750 gr\n129,90 TL\nMigros", "Nutella 750 gr", 129.9),
    ("Samsung\n55 inç Crystal UHD TV\n15 bin TL", "Samsung 55 inç Crystal UHD TV", 15000),
    ("Fiyat düştü!\nRoborock Q Revo\nSepette 17.999 TL\nTaksit 12 x 1.500 TL", "Roborock Q Revo", 17999),
    ("Sepette %20 indirim\nOral-B iO 5 Şarjlı Diş Fırçası\n3.299 TL", "Oral-B iO 5 Şarjlı Diş Fırçası", 3299),
    ("Xiaomi Robot Süpürge S10+\nFiyat: 9.499 TL\nKargo: 49,90 TL", "Xiaomi Robot Süpürge S10+", 9499),
    ("Apple iPhone 15 128 GB\n39.999 TL\n(2 gün önce 41.499 TL idi)", "Apple iPhone 15 128 GB", 39999),
    ("Adidas Erkek Ayakkabı\n1.250 TL\n750 TL sepette indirim", "Adidas Erkek Ayakkabı", 1250),
    ("Hepsiburada\n500 TL kupon kodu: HB500\nXiaomi 14T 12/256", "Xiaomi 14T 12/256", None),
    ("Kingston 1TB NV2 NVMe SSD 2.099 TL", "Kingston 1TB NV2 NVMe SSD", 2099),
    ("🔥 Samsung 980 Pro 2TB 5.399 TL 🔥 ➡️ link", "Samsung 980 Pro 2TB", 5399),
    ("Ücretsiz Kargo!\nPhilips Sonicare 3100\n1.899 TL", "Philips Sonicare 3100", 1899),
    ("Hepsipay ile 1.000 TL ve üzeri alışverişe 100 TL indirim\nSony PS5 Slim\n21.999 TL", "Sony PS5 Slim", 21999),
    ("Dikkat! Stoklarla sınırlı\nBaseus 65W GaN Şarj Aleti\n429 TL\nKupon ile 349 TL", "Baseus 65W GaN Şarj Aleti", 349),
    ("Lg 55 inç TV\n17.499 TL (Normal fiyatı 21.999 TL)", "Lg 55 inç TV", 17499),
    ("Netflix 3 ay ücretsiz deneme", None, None),
    ("Çok iyi fiyat gibi duruyor, bence kaçmaz", None, None),
    ("Epic Games'te bu hafta ücretsiz oyun: Control", None, 0),
]


class ExtractionTests(unittest.TestCase):
    def test_title_and_price_cases(self):
        for text, want_title, want_price in CASES:
            with self.subTest(text=text[:50]):
                title, price = pf.analyze(text)
                if want_title is not None:
                    self.assertEqual(title.lower(), want_title.lower())
                if want_price is None:
                    self.assertIsNone(price.value)
                else:
                    self.assertIsNotNone(price.value)
                    self.assertAlmostEqual(price.value, want_price, places=2)

    def test_struck_through_price_is_ignored(self):
        text = "Ürün Adı Pro\n1.999 TL 1.499 TL"
        start = text.index("1.999")
        info = pf.extract_price(text, [(start, start + len("1.999 TL"))])
        self.assertEqual(info.value, 1499)

    def test_amount_formats(self):
        for raw, value in [("1.299", 1299), ("1.299,90", 1299.9), ("1,299.90", 1299.9),
                           ("129,90", 129.9), ("1299", 1299), ("12.345.678", 12345678)]:
            self.assertAlmostEqual(pf.parse_amount(raw), value)

    def test_threshold_parser(self):
        for raw, value in [("10000", 10000), ("10.000 ₺", 10000), ("50 tl", 50), ("10 bin", 10000), ("0", 0)]:
            self.assertEqual(pf.parse_threshold(raw), value)
        self.assertIsNone(pf.parse_threshold("abc"))

    def test_price_format(self):
        self.assertEqual(pf.format_price(1299), "1.299 TL")
        self.assertEqual(pf.format_price(159.9), "159,90 TL")
        self.assertEqual(pf.format_price(0), "0 TL")

    def test_threshold_is_numeric_not_substring(self):
        self.assertTrue(pf.price_matches("Ampul\n0 TL", 0)[0])
        self.assertFalse(pf.price_matches("Ampul\n150 TL", 0)[0])
        self.assertTrue(pf.price_matches("Ampul\n50 TL", 50)[0])
        self.assertFalse(pf.price_matches("Ampul\n51 TL", 50)[0])

    def test_coupon_never_counts_as_price(self):
        self.assertFalse(pf.price_matches("TCL 65 TV 29.999 TL\n500 TL kupon", 1000)[0])
        self.assertFalse(pf.price_matches("Sepette 150 TL indirim\nTV 20.000 TL", 1000)[0])
        self.assertFalse(pf.price_matches("Sadece kupon: 100 TL kupon kodu", 1000)[0])

    def test_low_confidence_never_triggers(self):
        info = pf.extract_price("Eski fiyat 900 TL")
        self.assertEqual(info.confidence, "low")
        self.assertFalse(pf.price_matches("Eski fiyat 900 TL", 1000)[0])


class DmPriceRuleTests(unittest.TestCase):
    def test_independent_from_keywords(self):
        cfg = {"dm_enabled": True, "dm_keywords": ["tcl 65"], "dm_exclude_keywords": [], "dm_price_max": ["50"]}
        self.assertTrue(bot.dm_should_send(cfg, "TCL 65C7K TV 29.999 TL"))      # kelime kuralı
        self.assertTrue(bot.dm_should_send(cfg, "Ampul 0 TL"))                  # fiyat kuralı
        self.assertFalse(bot.dm_should_send(cfg, "Ampul 150 TL"))               # ikisi de değil

    def test_price_rule_works_when_keyword_dm_is_off(self):
        cfg = {"dm_enabled": False, "dm_keywords": [], "dm_price_max": ["10000"]}
        self.assertTrue(bot.dm_should_send(cfg, "Telefon\n9.999 TL"))
        self.assertFalse(bot.dm_should_send(cfg, "Telefon\n10.001 TL"))

    def test_rule_off_without_threshold(self):
        cfg = {"dm_enabled": False, "dm_keywords": []}
        self.assertFalse(bot.dm_should_send(cfg, "Ampul 0 TL"))

    def test_keyword_exclude_does_not_block_price_rule(self):
        cfg = {"dm_enabled": True, "dm_keywords": ["tv"], "dm_exclude_keywords": ["tv"], "dm_price_max": ["100"]}
        self.assertTrue(bot.dm_should_send(cfg, "TV kablosu 49 TL"))

    def test_staging(self):
        ok, cand, values, msg = bot.stage_telegram_list_change({}, "dm_price_max", "add", "10.000 ₺")
        self.assertTrue(ok)
        self.assertEqual(cand["dm_price_max"], ["10000"])
        ok, cand2, _, _ = bot.stage_telegram_list_change(cand, "dm_price_max", "add", "50")
        self.assertEqual(cand2["dm_price_max"], ["50"])         # yeni değer eskinin yerine geçer
        ok, cand3, _, _ = bot.stage_telegram_list_change(cand2, "dm_price_max", "remove", "1")
        self.assertTrue(ok)
        self.assertEqual(cand3["dm_price_max"], [])
        self.assertFalse(bot.stage_telegram_list_change({}, "dm_price_max", "add", "abc")[0])
        self.assertFalse(bot.stage_telegram_list_change({}, "dm_price_max", "add", "10, 20")[0])
        self.assertFalse(bot.stage_telegram_list_change({}, "dm_price_max", "remove", "1")[0])

    def test_alias(self):
        for name in ("dmfiyat", "dm_fiyat", "6"):
            self.assertEqual(bot.resolve_telegram_list(name), "dm_price_max")


class SmartHeaderLayoutTests(unittest.TestCase):
    TEXT = "🔥 FIRSAT 🔥\nSamsung Galaxy S24 256 GB\nEski: 49.999 TL\nYeni: 41.999 TL\n500 TL kupon"

    def setUp(self):
        self.addCleanup(setattr, bot, "SMART_HEADER", bot.SMART_HEADER)

    def event(self):
        start = self.TEXT.index("49.999")
        return SimpleNamespace(
            message=self.TEXT, raw_text=self.TEXT, reply_markup=None, media=None,
            entities=[types.MessageEntityStrike(offset=start, length=len("49.999 TL"))],
        )

    def test_default_layout_is_unchanged(self):
        bot.SMART_HEADER = False
        text = bot.compose_message(self.event(), message_link="https://t.me/c/1/2", source_name="Grup")["text"]
        self.assertNotIn("💰", text)
        self.assertTrue(text.startswith("Samsung Galaxy S24 256 GB\n\n🔥 FIRSAT"))

    def test_smart_layout_order(self):
        bot.SMART_HEADER = True
        composed = bot.compose_message(self.event(), message_link="https://t.me/c/1/2", source_name="Grup")
        text = composed["text"]
        self.assertTrue(text.startswith("Samsung Galaxy S24 256 GB\n\n💰 41.999 TL\n\n🔥 FIRSAT"))
        self.assertLess(text.index("💰"), text.index("Eski:"))
        self.assertLess(text.index("500 TL kupon"), text.index("🔗 Mesajı Gör"))
        self.assertTrue(text.endswith("Grup"))
        kinds = [type(e).__name__ for e in composed["entities"]]
        self.assertIn("MessageEntityBlockquote", kinds)
        quote = next(e for e in composed["entities"] if type(e).__name__ == "MessageEntityBlockquote")
        raw = text.encode("utf-16-le")[quote.offset * 2:(quote.offset + quote.length) * 2].decode("utf-16-le")
        self.assertEqual(raw, self.TEXT)   # Telegram offsetleri UTF-16'dır

    def test_price_not_found_is_visible(self):
        bot.SMART_HEADER = True
        event = SimpleNamespace(message="Güzel bir ürün var", raw_text="Güzel bir ürün var",
                                reply_markup=None, media=None, entities=[])
        text = bot.compose_message(event, message_link=None, source_name=None)["text"]
        self.assertIn(bot.PRICE_NOT_FOUND_TEXT, text)


if __name__ == "__main__":
    unittest.main()
