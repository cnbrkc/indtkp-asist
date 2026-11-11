"""Regressions for full campaign-title selection in coupon-only posts."""
from types import SimpleNamespace
import unittest

import bot
import price_filter


def event(text):
    return SimpleNamespace(message=text, raw_text=text, entities=[], media=None)


class CouponCampaignTitleTests(unittest.TestCase):
    def test_mobile_amazon_campaign_uses_complete_explanation_not_clipped_teaser(self):
        text = ("Mobil uygulamada geçerli 1.000 TL alışverişe ❓\nAmazon İndirim Kodu. 📣\n"
                "Mobil uygulamada geçerli 1.000 TL alışverişe 100 TL indirim!\n🎟 Kupon: KASIM100")
        self.assertEqual(bot._search_query(event(text)),
                         "Amazon Mobil uygulamada geçerli 1.000 TL alışverişe 100 TL indirim")
        self.assertIsNone(price_filter.extract_price(text).value)  # threshold/coupon is not item price

    def test_boyner_provider_footer_loses_to_actual_campaign_heading(self):
        text = ("Boyner tarafından sağlandı. ❓\n📣 Boyner 2.000 TL'ye 500 TL İndirim Kodu\n"
                "İndirim Kodu: MODA500\nKampanyalı ürünleri görüntüleyin")
        self.assertEqual(bot._search_query(event(text)), "Boyner 2.000 TL'ye 500 TL İndirim Kodu")
        self.assertIsNone(price_filter.extract_price(text).value)

    def test_amazon_threshold_and_discount_sentence_beats_incomplete_heading(self):
        text = ("üzeri alışverişte ❓\n🔥 Amazon İndirim Kodu\n"
                "🛒 4.000 TL ve üzeri alışverişte 1.000 TL indirim\n🎟️ Kod: KASIM1000")
        self.assertEqual(bot._search_query(event(text)), "Amazon 4.000 TL ve üzeri alışverişte 1.000 TL indirim")
        self.assertIsNone(price_filter.extract_price(text).value)

    def test_coupon_code_is_not_campaign_title_when_full_campaign_sentence_exists(self):
        text = ("₺250 — MODADA250 (kopyalamak için dokun)\n🎟️ İNDİRİM KUPONU\n"
                "🔑 MODADA250 (kopyalamak için dokun)\n💸 1250 TL VE ÜZERİ 250 TL INDIRIM\n🛒 AMAZON’A GİT")
        self.assertEqual(bot._search_query(event(text)), "1250 TL VE ÜZERİ 250 TL INDIRIM")

    def test_product_line_remains_authoritative_in_post_with_coupon(self):
        title = "Imza Siyah Kirlangic Yaka 1001265163"
        text = f"{title}\n2727 TL / KASIM250 koduyla 2477 TL / altindaki 200 TL kuponla 2527 TL"
        self.assertIn("1001265163", bot._search_query(event(text)))
        self.assertEqual(price_filter.extract_price(text).value, 2477)

    def test_one_unambiguous_product_price_is_unchanged(self):
        text = "PUMA Jada Classic DayINight Spor Ayakkabı Kadın\n3516.75 TL → 1352.79 TL"
        self.assertEqual(price_filter.extract_price(text).value, 1352.79)
        self.assertIn("PUMA Jada", bot._search_query(event(text)))


if __name__ == "__main__":
    unittest.main()
