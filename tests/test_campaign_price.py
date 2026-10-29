"""Kampanya koşulu tutarları (ör. "2. ürün 1 TL") ürün fiyatı sayılmaz.

Senaryolar: (1) yalnızca kampanya tutarı var → fiyat bilinmez (❓, dedup'ta fiyat yok,
DM kuralı tetiklenmez); (2) gerçek ürün fiyatı + kampanya tutarı → gerçek fiyat;
(3) normal 1 TL ürünler ve kampanya gibi görünen ama ürün bağlamında olan tutarlar
→ filtrelenmez. Her vaka fiyat katmanında (price_filter), bildirim başlığında
(compose_message), dedup fiyatında (offer_terms), DM kuralında (price_matches) ve
analiz raporunda (query_price) birlikte doğrulanır.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import bot
import price_filter as pf


def _event(text: str):
    return SimpleNamespace(message=text, raw_text=text, entities=[], media=None)


def _first_line(text: str) -> str:
    with patch.object(bot, "SMART_HEADER", True):
        return bot.compose_message(_event(text))["text"].splitlines()[0]


class CampaignOnlyPriceTest(unittest.TestCase):
    """Mesajda ürün fiyatı yok; yalnızca kampanya koşulu tutarı var → bilinmeyen fiyat."""

    CASES = [
        "Kalem Set 10'lu\n2 al 2. ürün 1 TL",
        "Kalem Set 10'lu\n2 al, 2. ürüne 1 TL",
        "Kalem Set 10'lu\n2 al 2. 1₺",
        "Kalem Set 10'lu\n2. ürüne 1₺",
        "Kalem Set 10'lu\nİkinci ürüne 1₺",
        "Kalem Set 10'lu\nİkinci ürün 1 TL",
        "Kalem Set 10'lu\n2. ürünü 1 TL'ye al",
        "Kalem Set 10'lu\n2. ürün: 1 TL",
        "Kalem Set 10'lu\n2. ürün fiyatı: 1 TL",
        "Kalem Set 10'lu\n🏷 2. ürün 1₺",
        "Kalem Set 10'lu\n2 alana 1 TL kampanyası",
        "Kalem Set 10'lu\n2 alana 2. ürün 1₺",
        "Kalem Set 10'lu\nKampanya: 2. ürün 1 TL",
        "Kalem Set 10'lu\nKampanya: 1 TL ek ürün",
        "Kalem Set 10'lu\n2. ürün 1 TL kampanyası geçerli",
        # çok satırlı: kampanya koşulu bir satırda, tutar hemen altında
        "Kalem Set 10'lu\n2. ürün\n1 TL",
        "Kalem Set 10'lu\n2. ürün:\n1 TL",
        "Kalem Set 10'lu\n2. ürün:\n1₺",
        "Kalem Set 10'lu\n2. ürün\n1₺",
        "Kalem Set 10'lu\n2 al 2. ürün\n1₺",
        "Kalem Set 10'lu\n2 alana\n1 TL",
        "Kalem Set 10'lu\nKampanya:\n2. ürün\n1 TL",
        "Kalem Set 10'lu\nEk ürün\n1 TL",
    ]

    def test_campaign_amount_is_not_a_product_price(self):
        for text in self.CASES:
            with self.subTest(text=text):
                info = pf.extract_price(text)
                self.assertIsNone(info.value)
                self.assertEqual(info.confidence, "none")
                self.assertEqual(info.reason, "yalnız kampanya koşulu tutarı")

    def test_notification_header_stays_unknown(self):
        for text in self.CASES:
            with self.subTest(text=text):
                self.assertTrue(_first_line(text).startswith(bot.PRICE_MISSING_MARK),
                                _first_line(text))

    def test_dedup_and_dm_never_use_campaign_amount(self):
        for text in self.CASES:
            with self.subTest(text=text):
                self.assertIsNone(bot.offer_terms(_event(text))[0])
                self.assertFalse(pf.price_matches(text, 5)[0])
                self.assertFalse(pf.price_matches(text, 1000)[0])

    def test_analysis_report_does_not_quote_campaign_amount(self):
        for text in self.CASES:
            with self.subTest(text=text):
                self.assertEqual(bot.query_price(text), "fiyat yok")

    def test_campaign_role_is_recorded(self):
        roles = [c.role for c in pf.find_candidates("Kalem Set 10'lu\n2 al 2. ürün 1 TL")]
        self.assertEqual(roles, ["campaign"])


class RealPriceWithCampaignTest(unittest.TestCase):
    """Gerçek ürün fiyatı mesajda açıkça var; kampanya tutarı onu ezmez."""

    CASES = [
        ("Kalem Set 10'lu\n🏷 49,90 TL\n2. ürün 1 TL", 49.9),
        ("🏷 49,90 TL 2. ürün 1 TL", 49.9),
        # kampanya tutarından önce güçlü işaretli (🏷 / Fiyat:) gerçek fiyat: korunur
        ("Kalem Set 10'lu\nFiyat: 49,90 TL 2. ürün 1 TL", 49.9),
        ("Kalem Set 10'lu\n🏷 1 TL 2. ürün 49,90 TL", 1.0),
        # etiketli fiyat + ayrı satırdaki kampanya: etiket fiyatı korur
        ("Kalem Set 10'lu\n🏷 49,90 TL\n2. ürün:\n1₺", 49.9),
        ("Kalem Set 10'lu\n2. ürün:\n🏷 49,90 TL\n1 TL", 49.9),
        ("Kalem Set 10'lu\nFiyat: 49,90 TL\n2 al 2. 1₺", 49.9),
        ("Kalem Set 10'lu\n🏷 49,90 TL\n2 alana 1 TL", 49.9),
        ("Kalem Set 10'lu\nKampanya fiyatı: 49,90 TL ek ürün", 49.9),
        # kampanya satırı gerçek fiyattan önce
        ("Kalem Set 10'lu\n2. ürün 1 TL\n🏷 49,90 TL", 49.9),
        ("Kalem Set 10'lu\nKampanya: 2. ürün 1 TL\nFiyat: 49,90 TL", 49.9),
        # çok satırlı kampanya, araya giren gerçek fiyat
        ("Kalem Set 10'lu\n🏷 49,90 TL\n2. ürün\n1 TL", 49.9),
        ("Kalem Set 10'lu\n🏷 49,90 TL\n2. ürün:\n1₺", 49.9),
        # kampanya etiketinden sonra emojili gerçek fiyat satırı: fiyat korunur
        ("Kalem Set 10'lu\n2. ürün\n🏷 49,90 TL", 49.9),
    ]

    def test_real_price_wins_over_campaign_amount(self):
        for text, value in self.CASES:
            with self.subTest(text=text):
                info = pf.extract_price(text)
                self.assertEqual(info.value, value)
                self.assertIn(info.confidence, {"high", "medium"})
                self.assertEqual(bot.offer_terms(_event(text))[0], value)
                self.assertTrue(_first_line(text).startswith(bot.header_price_text(value)),
                                _first_line(text))
                self.assertTrue(pf.price_matches(text, value)[0])
                self.assertFalse(pf.price_matches(text, value - 1)[0])

    def test_analysis_price_matches_notification_with_campaign_marker(self):
        for text, shown in [
            ("Kalem Set 10'lu\n🏷 49,90 TL\n2. ürün 1 TL", "49,90 TL"),
            ("Kalem Set 10'lu\n2. ürün:\n🏷 49,90 TL\n1 TL", "49,90 TL"),
            ("Kalem Set 10'lu\n2. ürün 1 TL\n🏷 49,90 TL", "49,90 TL"),
            ("Kalem Set 10'lu\n🏷 1 TL\n2. ürün\n1 TL", "1 TL"),
        ]:
            with self.subTest(text=text):
                self.assertEqual(bot.query_price(text), shown)
                self.assertEqual(bot.query_price(text), pf.extract_price(text).shown)

    def test_campaign_amount_is_kept_as_campaign_role(self):
        roles = [c.role for c in pf.find_candidates("🏷 49,90 TL\n2. ürün 1 TL")]
        self.assertEqual(roles, ["normal", "campaign"])


class NormalOneLiraPriceTest(unittest.TestCase):
    """Gerçekten 1 TL olan ürünler ve kampanya kelimesine benzeyen ama ürün fiyatı olan tutarlar."""

    CASES = [
        ("Kalem Set 10'lu\n🏷 1 TL", 1.0),
        ("Kalem Set 10'lu\nFiyat: 1 TL", 1.0),
        ("Kalem Set 10'lu\n💰 1₺", 1.0),
        ("Kalem Set 10'lu 1 TL", 1.0),
        ("Kalem Set 10'lu\n🏷 1 TL\nKargo bedava", 1.0),
        ("Kalem Set 10'lu\nFiyat:\n1 TL", 1.0),
        ("Kalem Set 10'lu\n🏷 1 TL\n2. ürün\n1 TL", 1.0),
        ("Kalem Set 10'lu\n1 TL", 1.0),
        ("Kalem 1 TL'ye al", 1.0),
        ("Kalem Set\n🏷 1 TL'ye al", 1.0),
        # 1 TL ürün fiyatıdır; aynı mesajdaki kampanya tutarı yok sayılır.
        ("Kalem Set 10'lu\nFiyat: 1 TL\n2. ürün 1 TL", 1.0),
        ("Kalem Set 10'lu\n🏷 1 TL 2. ürün 1 TL", 1.0),
        # "2" ile başlayan ama kampanya olmayan ifadeler.
        ("Kutu 2 alüminyum 150 TL", 150.0),
        ("Ek ürün kılıfı 150 TL", 150.0),
        ("2 al 100 TL", 100.0),
        ("2. ürün: Kulaklık 1.299 TL", 1299.0),
        ("Kalem Set 2 ürün 1 TL", 1.0),
    ]

    def test_normal_prices_are_not_filtered(self):
        for text, value in self.CASES:
            with self.subTest(text=text):
                info = pf.extract_price(text)
                self.assertEqual(info.value, value)
                self.assertIn(info.confidence, {"high", "medium"})
                self.assertEqual(bot.offer_terms(_event(text))[0], value)
                self.assertTrue(_first_line(text).startswith(bot.header_price_text(value)),
                                _first_line(text))
                self.assertTrue(pf.price_matches(text, value)[0])
                self.assertFalse(pf.price_matches(text, value - 1)[0])

    def test_one_lira_threshold_matches_real_price(self):
        self.assertTrue(pf.price_matches("Kalem\n🏷 1 TL", 1)[0])
        self.assertEqual(bot.query_price("Kalem Set 10'lu\n🏷 1 TL"), "1 TL")


class AmbiguousSameLineTest(unittest.TestCase):
    """Aynı satırda kampanya tutarı + etiketsiz başka tutar: hangisi ürün fiyatı belli değil.

    Beklenen: fiyat yayımlanmaz (❓, dedup fiyatı yok, DM eşleşmez, analiz "fiyat yok").
    Güçlü işaretli tutar (🏷/Fiyat:) kampanyadan önce geliyorsa RealPriceWithCampaignTest'te
    gerçek fiyat olarak kalır.
    """

    CASES = [
        "Kalem Set 10'lu\n1 TL 2. ürün 49,90 TL",
        "Kalem Set 10'lu\n49,90 TL 2. ürün 1 TL",
        "Kalem Set 10'lu\n1 TL (2. ürün 49,90 TL)",
        "Kalem Set 10'lu\n49,90 TL (2. ürün 1 TL)",
        "Kalem Set 10'lu\n1 TL / 2. ürün 49,90 TL",
        "Kalem Set 10'lu\n1 TL, 2. ürün 1 TL",
        "Kalem Set 10'lu\n2. ürün 49,90 TL 1 TL",
        "Kalem Set 10'lu\n2 al 1 TL 2. ürün 49,90 TL",
        "Kalem Set 10'lu\n49,90 TL 2 al 2. 1₺",
        "Kalem Set 10'lu\n🏷 2. ürün 1 TL 49,90 TL",
        # Başlık aynı satırdaysa emoji "güçlü işaret" sayılmaz: ürün adı + iki tutar belirsiz.
        "Kalem Set 10'lu 🏷 49,90 TL 2. ürün 1 TL",
        "Kalem Set 10'lu\n🏷 2 al 2. 1₺ 49,90 TL",
    ]

    def test_ambiguous_same_line_publishes_no_price(self):
        for text in self.CASES:
            with self.subTest(text=text):
                info = pf.extract_price(text)
                self.assertIsNone(info.value)
                self.assertEqual(info.confidence, "none")
                self.assertTrue(_first_line(text).startswith(bot.PRICE_MISSING_MARK),
                                _first_line(text))
                self.assertIsNone(bot.offer_terms(_event(text))[0])
                self.assertFalse(pf.price_matches(text, 1000)[0])
                self.assertEqual(bot.query_price(text), "fiyat yok")


class AmbiguousLayoutTest(unittest.TestCase):
    """Kesin çözülemeyen düzenler: yanlış fiyat üretmek yerine fiyat bilinmez (❓)."""

    CASES = [
        # Ürün adı gibi biten satırın altında tek tutar: kampanya olarak alınır (güvenli taraf).
        "Kalem Set 2. ürün\n1 TL",
        # Etiketsiz fiyat + ayrı satırdaki kampanya tutarı: hangisi ürün fiyatı belirsiz.
        "Kalem Set 10'lu\n49,90 TL\n2. ürün 1 TL",
        "Kalem Set 10'lu\n1 TL\n2. ürün 49,90 TL",
        "Kalem Set 10'lu\n1 TL\n2. ürün:\n49,90 TL",
        "Kalem Set 10'lu\n2. ürün 1 TL\n49,90 TL",
    ]

    def test_ambiguous_layout_stays_unknown(self):
        for text in self.CASES:
            with self.subTest(text=text):
                info = pf.extract_price(text)
                self.assertIsNone(info.value)
                self.assertEqual(info.confidence, "none")
                self.assertTrue(_first_line(text).startswith(bot.PRICE_MISSING_MARK))
                self.assertIsNone(bot.offer_terms(_event(text))[0])
                self.assertFalse(pf.price_matches(text, 1000)[0])
                self.assertEqual(bot.query_price(text), "fiyat yok")


if __name__ == "__main__":
    unittest.main()
