"""Regressions from user-supplied notification examples; no Telegram export used."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import bot
import price_filter as pf


DREAME = (
    "Dreame D20 Ultra Robot Süpürge Beyaz, 13000Pa Emiş Gücü\n\n"
    "🏷️ 20.999 TL (Son 6 Ayın En Düşük Fiyatı)\n\n"
    "💬 Sepette 1 yıllık süpürge bakım paketi hediye"
)
AUHMA = (
    "🔥AUHMA AMG38 Boyun ve Omuz Masaj Aleti\n\n"
    "Sepette 7GO120 kodunu girin. Kod ile en düşük fiyatına iner."
)
AMAZON_PROMO = (
    '🎯 Amazon\'da "Seçili ürünlerde Sepette 2.ye %50 İndirim"\n\n'
    "📅 Başlangıç: 06 Kasım 2026 - Bitiş: 30 Kasım 2026\n"
    "https://www.amazon.com.tr/promotion/psp/AKF3SOFKUFNNK"
)
SHIPPING_PACK = (
    "422 TL’ye çıkmıştı tekrar 199 TL⬇️ Plus üyeliklerde kargo bedava, "
    "59 TL kargo ödemek yerine sepette 10’lu kargo paketini "
    "75 TL’ye alabilirsiniz😉"
)


class ReportedMessageRegressions(unittest.TestCase):
    def test_dreame_care_bundle_quantity_is_not_price(self):
        self.assertEqual(pf.extract_price(DREAME).value, 20999)
        self.assertEqual(pf.extract_title(DREAME),
                         "Dreame D20 Ultra Robot Süpürge Beyaz, 13000Pa Emiş Gücü")
        self.assertEqual([c.value for c in pf.find_candidates(DREAME)], [20999])

    def test_coupon_code_digits_do_not_become_price(self):
        self.assertIsNone(pf.extract_price(AUHMA).value)
        self.assertEqual(pf.extract_title(AUHMA), "AUHMA AMG38 Boyun ve Omuz Masaj Aleti")
        self.assertEqual(pf.find_candidates(AUHMA), [])
        self.assertFalse(pf.price_matches(AUHMA, 1000)[0])

    def test_second_item_percent_campaign_and_dates_have_no_product_price_or_title(self):
        self.assertIsNone(pf.extract_price(AMAZON_PROMO).value)
        self.assertEqual(pf.extract_title(AMAZON_PROMO), "")
        self.assertEqual(pf.find_candidates(AMAZON_PROMO), [])
        self.assertFalse(bot.dm_should_send({"dm_price_max": ["20000"]}, AMAZON_PROMO))

    def test_previous_high_price_shipping_and_ten_pack_are_separated(self):
        info = pf.extract_price(SHIPPING_PACK)
        self.assertEqual((info.value, info.confidence), (199, "medium"))
        self.assertEqual(pf.extract_title(SHIPPING_PACK), "")
        self.assertEqual([c.role for c in pf.find_candidates(SHIPPING_PACK)],
                         ["old", "normal", "shipping", "shipping"])
        self.assertTrue(pf.price_matches(SHIPPING_PACK, 199)[0])
        self.assertFalse(pf.price_matches(SHIPPING_PACK, 198)[0])

    def test_notification_header_uses_correct_or_unknown_amount(self):
        expected = [
            (DREAME, "₺20.999 — Dreame D20 Ultra Robot Süpürge Beyaz, 13000Pa Emiş Gücü"),
            (AUHMA, "❓ — AUHMA AMG38 Boyun ve Omuz Masaj Aleti"),
            (SHIPPING_PACK, "₺199"),
        ]
        with patch.object(bot, "SMART_HEADER", True):
            for text, wanted in expected:
                event = SimpleNamespace(message=text, raw_text=text, entities=[], media=None)
                with self.subTest(wanted=wanted):
                    self.assertEqual(bot.compose_message(event)["header"], wanted)


if __name__ == "__main__":
    unittest.main()
