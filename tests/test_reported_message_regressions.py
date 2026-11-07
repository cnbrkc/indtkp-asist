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

SONGMICS = (
    "SONGMICS 2 Elbise Askısı, Uzatılabilir Gardırop Askısı, Tekerlekli Gardırop Askısı, "
    "Yüksekliği 113 ila 180 cm Arasında Yüksekliği Ayarlanabilir, Alt Çubuk Ayarlanabilir, "
    "Siyah LLR401B01\n\n🏷️ 1489 TL\n\n👉 FIRSATA GİT\n\n"
    "🛍️Amazon tanıtım @ozelfirsat GOOGLE 🔍"
)
TEFAL = (
    "Tefal Ingenio Harmony Okaliptus Büyük Tava Tencere Seti - 10 Parça 🔎\n\n"
    "💳 5.649 TL - 2 set alımda\n\n⚡ Ürün sayfasındaki 2700 TL kuponu kullanın\n\n"
    "🛍️ n11 (Satıcı : n11) Tanıtım\n\n👉 FIRSATA GİT 👈\n\n"
    "📣 Diğer kanallarımız için TAZEFIRSAT.com"
)
KARCHER = (
    "Karcher Wd 1 Classic 1.628-411.0\n\n"
    "🛍️SOFRAM300 koduyla 1.690 TL\n\nhttps://publicis.link/amzn-37441-taxlg5a"
)
SAMSUNG = (
    "Samsung Galaxy S26 FE 256 GB 8 GB Ram (Samsung Türkiye Garantili)\n\n"
    "https://app.hb.biz/RjzSABNqoXN9  siyah renk\n\n"
    "https://app.hb.biz/dv6XvY8dsk8R yeşil renk\n\n"
    "43.728 tl sepette\n\nSatıcı hepsiburada-teknosa\n\n🔥🔥🔥"
)
PHILIPS_DAILY = (
    "Philips Daily Collection Çelik Su Isıtıcı - 1.7L, 2200W, Işıklı Gösterge, "
    "360° Döner Taban, Mikro Gözenekli Filtre, Yassı Isıtma Yüzeyi, Yaylı Kapak, "
    "Enerji Tasarruflu, Güvenli Tasarım (HD9350/90)\n\n🏷️ 1.463 TL\n\n"
    "SOFRAM300 Kodunu sepette kullanın\n\n👉 FIRSATA GİT\n\n"
    "🛍️Amazon tanıtım @ozelfirsat GOOGLE 🔍"
)
PHILIPS_5000 = (
    "PHILIPS 5000 Serisi Buharlı Ütü – 2400W, 40g/dk Sürekli Buhar, 160g Şok, "
    "SteamGlide Plus Taban, Dahili Kireç Temizleme, Damlatmaz Sistem, Dikey Kullanım "
    "Özelliği (DST5010/10)\n\n🏷️ 1.474 TL\n\nSOFRAM300 Kodunu sepette kullanın\n\n"
    "👉 FIRSATA GİT\n\n🛍️Amazon tanıtım @ozelfirsat GOOGLE 🔍"
)
HAUSTIER = (
    "🛍️ Haustier WF007L Otomatik Kedi Su Sebili,Kedi Su Çeşmesi, Su Şelalesi, "
    "Su Pınarı 3.5Lt Geniş Hazne, Bpa Içermez, Ultra Sessiz (Siyah)\n\n"
    "💰 Fiyat : 1344 TL / ödeme aşamasında 134 TL indirimle 1209 TL\n\n"
    "🗓️ 365 Günün En Düşük Fiyatı\n\n🛒 https://link.amazon/B0ewaZard"
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
        fallback = bot._search_query(AMAZON_PROMO)
        self.assertTrue(pf.is_generic_title(fallback), fallback)
        event = SimpleNamespace(message=AMAZON_PROMO, raw_text=AMAZON_PROMO, entities=[], media=None)
        with patch.object(bot, "SMART_HEADER", True):
            self.assertEqual(bot.compose_message(event)["header"], "")
        self.assertEqual(bot.smart_dedup_title(event), "")

    def test_previous_high_price_shipping_and_ten_pack_are_separated(self):
        info = pf.extract_price(SHIPPING_PACK)
        self.assertEqual((info.value, info.confidence), (199, "medium"))
        self.assertEqual(pf.extract_title(SHIPPING_PACK), "")
        self.assertEqual([c.role for c in pf.find_candidates(SHIPPING_PACK)],
                         ["old", "normal", "shipping", "shipping"])
        self.assertTrue(pf.price_matches(SHIPPING_PACK, 199)[0])
        self.assertFalse(pf.price_matches(SHIPPING_PACK, 198)[0])

    def test_store_and_coupon_metadata_never_replaces_product_titles(self):
        title_songmics = (
            "SONGMICS 2 Elbise Askısı, Uzatılabilir Gardırop Askısı, Tekerlekli Gardırop Askısı, "
            "Yüksekliği 113 ila 180 cm Arasında Yüksekliği Ayarlanabilir, Alt Çubuk Ayarlanabilir, "
            "Siyah LLR401B01"
        )
        title_daily = (
            "Philips Daily Collection Çelik Su Isıtıcı - 1.7L, 2200W, Işıklı Gösterge, "
            "360° Döner Taban, Mikro Gözenekli Filtre, Yassı Isıtma Yüzeyi, Yaylı Kapak, "
            "Enerji Tasarruflu, Güvenli Tasarım (HD9350/90)"
        )
        title_5000 = (
            "PHILIPS 5000 Serisi Buharlı Ütü – 2400W, 40g/dk Sürekli Buhar, 160g Şok, "
            "SteamGlide Plus Taban, Dahili Kireç Temizleme, Damlatmaz Sistem, Dikey Kullanım "
            "Özelliği (DST5010/10)"
        )
        cases = [
            (SONGMICS, title_songmics, 1489, "₺1.489"),
            (TEFAL, "Tefal Ingenio Harmony Okaliptus Büyük Tava Tencere Seti - 10 Parça", 5649, "₺5.649"),
            (KARCHER, "Karcher Wd 1 Classic 1.628-411.0", 1690, "₺1.690"),
            (PHILIPS_DAILY, title_daily, 1463, "₺1.463"),
            (PHILIPS_5000, title_5000, 1474, "₺1.474"),
        ]
        for text, title, price, shown in cases:
            with self.subTest(title=title):
                self.assertEqual(pf.extract_title(text), title)
                self.assertEqual(pf.extract_price(text).value, price)
                event = SimpleNamespace(message=text, raw_text=text, entities=[], media=None)
                self.assertEqual(bot.smart_dedup_title(event), pf.extract_title(text))
                with patch.object(bot, "SMART_HEADER", True):
                    self.assertEqual(bot.compose_message(event)["header"], f"{shown} — {title}")
        with patch.object(bot, "SMART_HEADER", True):
            event = SimpleNamespace(message=TEFAL, raw_text=TEFAL, entities=[], media=None)
            self.assertIn("5.649 TL - 2 set alımda", bot.compose_message(event)["text"])
        shortened = pf.extract_title(SONGMICS, limit=80)
        self.assertLessEqual(len(shortened), 81, "açıkça istenen dar limit model koduyla aşılmamalı")
        self.assertTrue(shortened.endswith("…"))

    def test_price_followed_by_sepette_is_a_basket_price(self):
        title = "Samsung Galaxy S26 FE 256 GB 8 GB Ram (Samsung Türkiye Garantili)"
        info = pf.extract_price(SAMSUNG)
        self.assertEqual((info.value, info.confidence, info.reason), (43728, "high", "discounted"))
        self.assertEqual([(c.value, c.cls, c.role) for c in pf.find_candidates(SAMSUNG)],
                         [(43728, "price", "discounted")])
        self.assertEqual(pf.extract_title(SAMSUNG), title)
        self.assertIsNone(pf.extract_price("Sepette 750 TL indirim").value)
        self.assertIsNone(pf.extract_price("750 TL sepette indirim").value)
        self.assertEqual(pf.extract_price("43.728 TL sepette").value, 43728)
        event = SimpleNamespace(message=SAMSUNG, raw_text=SAMSUNG, entities=[], media=None)
        with patch.object(bot, "SMART_HEADER", True):
            self.assertEqual(bot.compose_message(event)["header"], f"₺43.728 — {title}")

    def test_haustier_explicit_payment_discount_selects_final_price(self):
        title = (
            "Haustier WF007L Otomatik Kedi Su Sebili,Kedi Su Çeşmesi, Su Şelalesi, "
            "Su Pınarı 3.5Lt Geniş Hazne, Bpa Içermez, Ultra Sessiz (Siyah)"
        )
        info = pf.extract_price(HAUSTIER)
        self.assertEqual((info.value, info.confidence, info.reason), (1209, "high", "discounted"))
        self.assertEqual([(c.value, c.role) for c in pf.find_candidates(HAUSTIER)],
                         [(1344, "normal"), (134, "discount_amount"), (1209, "discounted")])
        event = SimpleNamespace(message=HAUSTIER, raw_text=HAUSTIER, entities=[], media=None)
        with patch.object(bot, "SMART_HEADER", True):
            self.assertEqual(bot.compose_message(event)["header"], f"₺1.209 — {title}")

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
