"""Focused regressions from the reported coupon/title/price examples."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import bot
import price_filter as pf


def event(text: str):
    return SimpleNamespace(message=text, raw_text=text, entities=[], media=None)


class UserSuppliedCouponRegressions(unittest.TestCase):
    def test_clipped_coupon_teaser_uses_complete_campaign_and_code(self):
        text = (
            "üzerine ❓\n📢 Hepsiburada\n"
            "300 TL ve üzerine 50 TL indirim kodu\nEFSOCUMA50"
        )
        expected = "300 TL ve üzerine 50 TL indirim kodu — EFSOCUMA50"
        self.assertEqual(bot._coupon_campaign_title(text, 200, include_code=True), expected)
        self.assertEqual(bot.smart_dedup_title(event(text)), "Kampanya hepsiburada EFSOCUMA50 300.00 50.00")
        with patch.object(bot, "SMART_HEADER", True):
            header = bot.compose_message(event(text))["text"].splitlines()[0]
        self.assertEqual(header, "300 TL ve üzerine 50 TL indirim kodu ❓")
        self.assertIsNone(pf.extract_price(text).value)

    def test_coupon_title_is_distinguished_by_code_for_dedup(self):
        first = "Hepsiburada Temel tüketim kategorilerinde, 500 TL ve üzeri alışverişleriniz için 100 TL indirim kodu: MARKETİM100"
        second = "Hepsiburada İndirim Kodu. Temel tüketim kategorisinde geçerli 500 TL alışverişe 100 TL indirim! Kupon: MARKETİM100"
        other_code = second.replace("MARKETİM100", "MARKETİM101")
        first_title = bot.smart_dedup_title(event(first))
        second_title = bot.smart_dedup_title(event(second))
        other_title = bot.smart_dedup_title(event(other_code))
        other_merchant = bot.smart_dedup_title(event(
            second.replace("Hepsiburada", "Amazon").replace("MARKETİM100", "MARKETİM100")
        ))
        self.assertEqual(first_title, second_title)
        self.assertIn("MARKETİM100", first_title)
        self.assertNotEqual(bot.dedup_key(second_title), bot.dedup_key(other_title))
        self.assertNotEqual(bot.dedup_key(first_title), bot.dedup_key(other_merchant))
        self.assertFalse(bot.titles_match_product(second_title, other_title))

    def test_coupon_category_is_retained_when_teaser_is_clipped(self):
        text = (
            "üzeri alışverişte ❓\n🔥 Amazon İndirim Kodu\n"
            "🏷️ Geçerli Kategori: Temizlik\n"
            "🛒 750 TL ve üzeri alışverişte 200 TL indirim\nKod: TEMIZLIK200"
        )
        title = bot.smart_dedup_title(event(text))
        self.assertIn("temizlik", title.casefold())
        self.assertIn("TEMIZLIK200", title)
        self.assertIsNone(pf.extract_price(text).value)

    def test_polo_coupon_code_heading_does_not_end_at_threshold(self):
        text = (
            "POLO500 Kodu ile n11'de U.S. Polo Ürünlerinde 1000TL ve üzeri ❓\n"
            "🔑POLO500 Kodu ile n11'de U.S. Polo Ürünlerinde 1000TL ve üzeri 500TL indirim"
        )
        title = bot.smart_dedup_title(event(text))
        self.assertIn("POLO500", title)
        self.assertIn("500TL indirim", bot._search_query(event(text)))
        self.assertIsNone(pf.extract_price(text).value)

    def test_boyner_basket_minimum_and_coupon_discount_are_not_item_price(self):
        text = (
            "Boyner Satıcılı Kozmetik Ürünlerinde Tüm İndirimlere Ek KOZMO500 Kodu ile "
            "2000 TL'ye 500 TL Daha İndirim Oluyor"
        )
        candidates = pf.find_candidates(text)
        self.assertEqual([c.role for c in candidates], ["threshold", "discount_amount"])
        self.assertIsNone(pf.extract_price(text).value)
        self.assertIn("KOZMO500", bot.smart_dedup_title(event(text)))
        with patch.object(bot, "SMART_HEADER", True):
            header = bot.compose_message(event(text))["text"].splitlines()[0]
        self.assertTrue(header.endswith("❓"), header)
        self.assertNotIn("₺2.000", header)

    def test_tp_link_is_preserved_in_search_title(self):
        text = (
            "TP-Link Deco M4 2-pack, AC1200 Mbps, Dual-Band, Gigabit Portları, "
            "100 Cihaza Kadar Bağlanılabilirlik, 260 m²'ye Kadar Kapsama, "
            "Mobil Uygulama ile Kolay Kurulum, Wi-Fi 5 Mesh Sistemi\n🏷️ 2.344 TL"
        )
        title = bot._search_query(event(text))
        self.assertTrue(title.startswith("TP-Link Deco M4"), title)
        self.assertEqual(pf.extract_price(text).value, 2344)

    def test_existing_product_price_and_multi_buy_stay_authoritative(self):
        text = (
            "ABC DETERJAN Sıvı Sabun Mor 400 Ml\nFiyat: 42.90 TL\n"
            "3 Al 2 Öde İle: 28.60 TL"
        )
        self.assertEqual(bot.smart_dedup_title(event(text)), "ABC DETERJAN Sıvı Sabun Mor 400 Ml")
        self.assertEqual(pf.extract_price(text).value, 42.9)

        appliance = (
            "Delonghi Magnifica Plus Otomatik Kahve Makinesi, 18 İçecek, ECAM322.70.SB\n"
            "Fiyat: 32999 TL / ödeme aşamasında 2000 TL indirimle 30999 TL\n"
            "KASIM800 koduyla 30199 TL"
        )
        self.assertIn("ECAM322.70.SB", bot.smart_dedup_title(event(appliance)))
        self.assertEqual(pf.extract_price(appliance).value, 30199)
        self.assertEqual(pf.extract_price(appliance).confidence, "high")
        with patch.object(bot, "SMART_HEADER", True):
            header = bot.compose_message(event(appliance))["text"].splitlines()[0]
        self.assertTrue(header.startswith("₺30.199 — Delonghi Magnifica Plus"), header)


if __name__ == "__main__":
    unittest.main()
