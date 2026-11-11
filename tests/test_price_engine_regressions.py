"""Sentetik kabul/regresyon senaryoları: fiyat güvenliği ve ürün başlığı."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import bot
import price_filter as pf


class PriceEngineRegressions(unittest.TestCase):
    def test_space_thousands_and_bin_amounts_never_drop_leading_digits(self):
        cases = [
            ("Samsung Galaxy A55\n12 999 TL", 12999),
            ("Samsung Galaxy A55\n15 bin 500 TL", 15500),
            ("Samsung Galaxy A55\n15 bin TL", 15000),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(pf.extract_price(text).value, expected)
                self.assertEqual(pf.extract_title(text), "Samsung Galaxy A55")

    def test_coupon_after_price_is_used_only_when_same_product_is_explicit(self):
        info = pf.extract_price("Philips Airfryer\n12.999 TL / Kupon uygulayınca 11.999 TL")
        self.assertEqual((info.value, info.confidence, info.reason), (11999, "high", "discounted"))
        self.assertEqual(pf.extract_title("Philips Airfryer\n12.999 TL / Kupon uygulayınca 11.999 TL"),
                         "Philips Airfryer")

    def test_coupon_amount_is_not_the_product_price(self):
        text = "Philips Airfryer\n12.999 TL ürün fiyatı, 1.000 TL kupon"
        self.assertEqual(pf.extract_price(text).value, 12999)
        self.assertEqual([c.role for c in pf.find_candidates(text)], ["normal", "discount_amount"])

    def test_basket_savings_are_not_subtracted_from_an_unclear_amount(self):
        text = "Philips Airfryer\nSepette 150 TL indirim / 1.299 TL"
        self.assertIsNone(pf.extract_price(text).value)
        self.assertFalse(pf.price_matches(text, 5000)[0])

    def test_pack_total_wins_over_parenthetical_unit_price(self):
        text = "8'li Kahve Kapsülü Paketi\n240 TL (tanesi 30 TL)"
        self.assertEqual(pf.extract_price(text).value, 240)
        self.assertEqual([c.role for c in pf.find_candidates(text)], ["pack_total", "unit"])
        self.assertEqual(pf.extract_title(text), "8'li Kahve Kapsülü Paketi")

    def test_explicit_single_unit_price_behavior_is_preserved(self):
        text = "Filtre Kahve\nAdet başı 80 TL"
        self.assertEqual(pf.extract_price(text).value, 80)
        self.assertEqual(pf.extract_title(text), "Filtre Kahve")

    def test_multi_product_prices_and_titles_are_not_cross_paired(self):
        text = ("1. ürün: Televizyon 13.999 TL\n"
                "2. ürün: Kulaklık kuponla 8.499 TL")
        info = pf.extract_price(text)
        self.assertIsNone(info.value)
        self.assertEqual(info.confidence, "none")
        self.assertEqual(pf.extract_title(text), "")
        self.assertFalse(pf.price_matches(text, 20000)[0])

    def test_product_block_selection_requires_unique_price_or_explicit_featured_marker(self):
        unique = "1. ürün: Televizyon\n2. ürün: Kulaklık 8.499 TL"
        self.assertEqual(pf.extract_price(unique).value, 8499)
        self.assertEqual(pf.extract_title(unique), "Kulaklık")
        featured = ("1. ürün: Televizyon 13.999 TL\n"
                    "2. ürün: Öne çıkan ürün: Kulaklık 8.499 TL")
        self.assertEqual(pf.extract_price(featured).value, 8499)
        self.assertEqual(pf.extract_title(featured), "Kulaklık")

    def test_old_price_is_excluded_and_multiple_discount_candidates_stay_unknown(self):
        old_new = "Bosch Blender\n15.999 TL yerine 12.999 TL"
        self.assertEqual(pf.extract_price(old_new).value, 12999)
        stacked = "Bosch Blender\n14.999 TL, sepette 13.999 TL, kuponla 12.999 TL"
        info = pf.extract_price(stacked)
        self.assertEqual(info.confidence, "low")
        self.assertFalse(pf.price_matches(stacked, 20000)[0])

    def test_number_formats_decimals_currency_position_and_punctuation(self):
        cases = [
            ("Fiyat: 12.999 TL", 12999),
            ("Fiyat: 12,999 TL", 12999),
            ("Fiyat: 12 999 TL", 12999),
            ("Fiyat: 1.299,90 TL", 1299.90),
            ("Fiyat: 1,299.90 TL", 1299.90),
            ("Fiyat: 1299,90 TL", 1299.90),
            ("₺12.999!", 12999),
            ("🔥 12.999 TL ✅", 12999),
        ]
        for offer, expected in cases:
            text = f"Sony WH-1000XM5\n{offer}"
            with self.subTest(offer=offer):
                self.assertAlmostEqual(pf.extract_price(text).value, expected)
                self.assertEqual(pf.extract_title(text), "Sony WH-1000XM5")

    def test_three_or_more_prices_and_product_numbers_are_role_filtered(self):
        text = ("Sony WH-1000XM5 Model WH1000XM5, 40 mm, 2026/11/07\n"
                "Eski fiyat 19.999 TL\nYeni fiyat: 17.999 TL\n"
                "12 taksit x 1.500 TL; 500 TL kupon tutarı")
        self.assertEqual(pf.extract_price(text).value, 17999)
        self.assertEqual(pf.extract_title(text), "Sony WH-1000XM5 Model WH1000XM5, 40 mm, 2026/11/07")

    def test_product_title_descriptors_and_model_variants_are_preserved(self):
        cases = [
            ("Yeni Doğan Bebek Bezi\n🏷 299 TL", "Yeni Doğan Bebek Bezi"),
            ("Super Mario Odyssey\n1.299 TL", "Super Mario Odyssey"),
            ("İlk Yardım Çantası\n249 TL", "İlk Yardım Çantası"),
            ("%0 Yağlı Yoğurt\n80 TL", "%0 Yağlı Yoğurt"),
            ("iPhone 15 – 128 GB Siyah\n12.999 TL", "iPhone 15 – 128 GB Siyah"),
            ("Samsung\n+ Galaxy Watch 6 44mm LTE\n12.999 TL", "Samsung Galaxy Watch 6 44mm LTE"),
            ("Dyson V15 Detect Absolute 394451-01\n24.999 TL", "Dyson V15 Detect Absolute 394451-01"),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(pf.extract_title(text), expected)

    def test_shipping_cashback_and_installment_are_distinct_non_product_roles(self):
        text = "Kargo: 30 TL\nCashback: 100 TL\n12 taksit x 1.250 TL"
        self.assertEqual([c.role for c in pf.find_candidates(text)],
                         ["shipping", "cashback", "installment"])
        self.assertIsNone(pf.extract_price(text).value)

    def test_malformed_or_missing_product_headings_do_not_create_fake_price(self):
        for text in ["Ürün adı yakında açıklanacak", "Fiyat: -- TL", "Model: ???\nSepette indirim"]:
            with self.subTest(text=text):
                self.assertIsNone(pf.extract_price(text).value)

    def test_notification_and_dm_use_unknown_for_ambiguous_multi_product_message(self):
        text = "1. ürün: Televizyon 13.999 TL\n2. ürün: Kulaklık kuponla 8.499 TL"
        event = SimpleNamespace(message=text, raw_text=text, entities=[], media=None)
        with patch.object(bot, "SMART_HEADER", True):
            header = bot.compose_message(event)["header"]
        self.assertEqual(header, "Birden fazla ürün ❓", header)
        self.assertFalse(pf.price_matches(text, 20000)[0])
        self.assertFalse(bot.dm_should_send({"dm_price_max": ["20000"]}, text))


if __name__ == "__main__":
    unittest.main()
