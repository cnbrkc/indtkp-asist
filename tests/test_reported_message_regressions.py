"""Regressions from user-supplied notification examples; no Telegram export used."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import bot
import price_filter as pf


SLEEPY = (
    "Sleepy Easy Clean Orkide Bahçesi Yüzey Temizlik Havlusu Mendili 100 Yaprak 🔎\n\n"
    "💳 75 TL\n\n⚠️ 4 adet ekleyin\n\n🛍️ Amazon Tanıtım\n\n"
    "👉 FIRSATA GİT 👈\n\n📣 Diğer kanallarımız için TAZEFIRSAT.com"
)
PHILIPS = (
    "🛍️ Philips 7000 Serisi Aqua 25.2 V Kablosuz Dik Süpürge\n\n"
    "💰 Fiyat : 14250 TL / ödeme aşamasında 750 TL indirimle 13500 TL\n\n"
    "🗓️ 365 Günün En Düşük Fiyatı\n\n"
    "https://www.amazon.com.tr/dp/B0GQV9ZHGZ?psc=1&tag=indirimalarmi-21"
)
ADIDAS = (
    "📉 FİYAT DÜŞÜŞÜ -%33,6\n\n"
    "🛍️ Adidas ESS BTU BP Unisex Yetişkin Sırt Çantası BLACK/WHITE S\n\n"
    "💸 İndirimli Fiyat: 1.577,00 TL\n\n🔻 Fark: -%33,6\n\n"
    "❌ Eski Fiyat: 2.375,01 TL\n\n🛒 Amazon: 30 günün en düşük fiyatı\n\n"
    "🏆 TÜM ZAMANLARIN DİBİ (78 günün)\n\n🔗 Amazon'da gör\n\n#Moda@amazonfirsatalarmi"
)
KOTON = (
    '"KOTON300" indirim kodunu uygulayın. Linkteki diğer montları da inceleyebilirsiniz.\n\n'
    "✅ Kapüşonlu Uzun Kollu Cepli Fermuarlı Şişme\n\n"
    '🏷 889 TL — sepette "KOTON300" indirim kodunu uygulayın. '
    "Linkteki diğer montları da inceleyebilirsiniz.\n\n"
    "👉 FIRSATA GİT !\n\n🛍 Hepsiburada"
)

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
    def test_sleepy_quantity_instruction_never_replaces_product_name(self):
        title = "Sleepy Easy Clean Orkide Bahçesi Yüzey Temizlik Havlusu Mendili 100 Yaprak"
        self.assertTrue(pf.is_generic_title("⚠️ 4 adet ekleyin"))
        self.assertTrue(pf.is_generic_title("Sepete 4 adet ekleyiniz"))
        self.assertEqual(pf.extract_title(SLEEPY), title)
        self.assertEqual(pf.extract_price(SLEEPY).value, 75)
        self.assertEqual(bot._search_query(SLEEPY), title)
        event = SimpleNamespace(message=SLEEPY, raw_text=SLEEPY, entities=[], media=None)
        self.assertEqual(bot.smart_dedup_title(event), title)
        with patch.object(bot, "SMART_HEADER", True):
            self.assertEqual(bot.compose_message(event)["header"], f"₺75 — {title}")

    def test_payment_stage_discount_selects_explicit_final_price(self):
        title = "Philips 7000 Serisi Aqua 25.2 V Kablosuz Dik Süpürge"
        info = pf.extract_price(PHILIPS)
        self.assertEqual((info.value, info.confidence, info.reason), (13500, "high", "discounted"))
        self.assertEqual(
            [(c.value, c.role) for c in pf.find_candidates(PHILIPS)],
            [(14250, "normal"), (750, "discount_amount"), (13500, "discounted")],
        )
        self.assertTrue(pf.price_matches(PHILIPS, 13500)[0])
        self.assertFalse(pf.price_matches(PHILIPS, 13499)[0])
        self.assertEqual(bot.offer_terms(PHILIPS)[0], 13500)
        event = SimpleNamespace(message=PHILIPS, raw_text=PHILIPS, entities=[], media=None)
        with patch.object(bot, "SMART_HEADER", True):
            self.assertEqual(bot.compose_message(event)["header"], f"₺13.500 — {title}")
        variant = "Fiyat: 1000 TL / ödeme aşamasında 100 TL indirim ile 900 TL"
        self.assertEqual(pf.extract_price(variant).value, 900)
        markdown_variant = "Fiyat: 1000 TL / ödeme aşamasında 100 TL indirimle **900 TL**"
        self.assertEqual(pf.extract_price(markdown_variant).value, 900)
        # Son tutar başka bir giderse genel kural gevşetilmez.
        shipping = variant + " kargo"
        self.assertNotEqual(pf.extract_price(shipping).value, 900)
        self.assertIsNone(pf.extract_price("Sepette 750 TL indirim").value)

    def test_koton_product_and_labeled_price_survive_coupon_instruction(self):
        title = "Kapüşonlu Uzun Kollu Cepli Fermuarlı Şişme"
        self.assertEqual(pf.extract_title(KOTON), title)
        self.assertEqual(pf.extract_price(KOTON).value, 889)
        self.assertEqual(pf.extract_price(KOTON).confidence, "high")
        self.assertEqual(bot._search_query(KOTON), title)
        event = SimpleNamespace(message=KOTON, raw_text=KOTON, entities=[], media=None)
        self.assertEqual(bot.smart_dedup_title(event), title)
        with patch.object(bot, "SMART_HEADER", True):
            self.assertEqual(bot.compose_message(event)["header"], f"₺889 — {title}")
        markdown = KOTON.replace(
            "✅ Kapüşonlu Uzun Kollu Cepli Fermuarlı Şişme",
            "✅ **Kapüşonlu Uzun Kollu Cepli Fermuarlı Şişme**",
        ).replace("🏷 889 TL", "🏷 **889 TL**")
        self.assertEqual(pf.extract_title(markdown), title)
        self.assertEqual(pf.extract_price(markdown).value, 889)

    def test_adidas_existing_extraction_remains_unchanged(self):
        title = "Adidas ESS BTU BP Unisex Yetişkin Sırt Çantası BLACK/WHITE S"
        self.assertEqual(pf.extract_title(ADIDAS), title)
        self.assertEqual(pf.extract_price(ADIDAS).value, 1577)
        event = SimpleNamespace(message=ADIDAS, raw_text=ADIDAS, entities=[], media=None)
        with patch.object(bot, "SMART_HEADER", True):
            self.assertEqual(bot.compose_message(event)["header"], f"₺1.577 — {title}")

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
