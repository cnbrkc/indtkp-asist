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

DREAME_N11 = (
    "n11'den Dreame L50S Pro Ultra Robot Süpürge Beyaz\n\n"
    "https://sl.n11.com/n/vAjGnql\n\nTık hızında 2000 kuponla 50499"
)
THRUSTMASTER_USED = (
    "🛍️ Thrustmaster T128 Yarış Direksiyonu – Hibrit Force Feedback Teknolojisi – "
    "Manyetik Vites ve Pedal Seti – LED Hız Göstergesi – Xbox Series X|S, Xbox One ve PC Uyumlu\n\n"
    "💰 Fiyat : 13199 TL\n🗓️ 60 Günün En Düşük Fiyatı\n"
    "♻️ İkinci El (Yeni Gibi) : 12539 TL\n\n"
    "https://www.amazon.com.tr/dp/B0B3RJNZXG?psc=1&tag=indirimalarmi-21"
)
PHILIPS_HAIR_COUPON = (
    "✅ Philips 3000 Serisi 3'ü 1 Arada Saç Şekillendirici, Seramik Argan Yağı Katkılı Plakalar\n\n"
    "🏷 1.649 TL — sepette 250 TL kupon ile. Listedeki diğer Philips ürünlerini de "
    "inceleyebilirsiniz 1500/250 kupon var."
)
PHILIPS_SHAVER_COUPON = (
    "✅ Philips 3000 Serisi Islak&Kuru Tıraş Makinesi, 5 Yönde Dönen Başlıklar, "
    "Hassas Düzeltici, S3144/00\n\n🏷 1.749 TL — sepette 250 TL kupon ile."
)
LOGITECH_CHAT = (
    "Logitech BRIO 100 Full HD Web Kamerası, Toplantı ve Yayın Amaçlı, Otomatik Işık Dengesi, "
    "Dahili Mikrofon, Gizlilik Kapağı, USB-A, Microsoft Teams, Google Meet, Zoom ile Uyumlu, Siyah\n\n"
    "💰2.300,00TL\n\n📦 Durum: İkinci El: Yeni Gibi\n\n"
    "💬 Yorum: Yorum: Ürün orijinal ambalajında gönderilir.\n\n🏪 Satıcı: Amazon Depo\n\n"
    "🔗FIRSATA GİT!\n\n📦 Stok: Stokta sadece 1 adet kaldı.\n\n"
    "📣 Sohbet Grubumuz İçin @firsatzsohbet"
)
HOMEND_COUPON = (
    "Homend Dustroyer 1272H 2 in 1 Metal Borulu Kablolu Dikey Süpürge\n\n1376 TL kupon ile"
)
EVEREST_MULTI = (
    "🔥Everest Gümüş Işıklandırmalı Oyuncu Mouse\n\n"
    "✅249TL - 2’li Alımda 2 Al 1 Öde İle Tanesi 124TL’ye Geliyor"
)
TRIOMOVE_SECOND = "TrioMove Protein Bar Ürünlerinde Sepette 2.si 1 TL"
BOYNER_BOGO = (
    "Boyner'de 1 Alana 1 Bedava + BOYNER1000 kodu ile birleşiyor\n\n"
    "https://app.hb.biz/qbRcRZ2m9A5G\n\n"
    "🔗 Mesajı Gör: https://t.me/indirimalarmigrup/528059"
)
LENOVO_TITLE = "Lenovo Thinkplus Live LP80 PRO Tws Bluetooth 5.3 Kablosuz Kulakiçi Kulaklık"
LENOVO_LP80 = (
    f"🛒 {LENOVO_TITLE}\n\n"
    "🎯 499 TL / Tık hızında 175 TL kuponla 3 adet alımda tanesi 440 TL 🚚 Kargo Bedava\n\n"
    "🛍️ https://sl.n11.com/n/vAk2s0n"
)
LENOVO_LP80_NOTIFICATION = (
    f"₺440 — {LENOVO_TITLE}\n\n{LENOVO_LP80}\n\n"
    "🔗 Mesajı Gör: https://t.me/indirimalarmigrup/528179\n\n"
    "indirimalarmi Sıcak Fırsatlar"
)
MAYI_PAYMENT = (
    "🛍️ Mayi Tuz Mayi İyotlu Öğütülmüş Sofra Tuzu 2 kg – 2'li Fırsat Paket\n\n"
    "💰 Fiyat : 600 TL / ödeme aşamasında 454  TL\n"
    "🗓️ 60 Günün En Düşük Fiyatı\n\n"
    "🛒 https://link.amazon/B00MrDi3m"
)
COLINS_PLUS = (
    "🛒 Colin's Yeşil Erkek Sweatshirt\n\n"
    "🎯 222 TL / Plus Üyelere Özel 211 TL\n\n"
    "🛍️ https://publicis.link/pazarama-37360-9bsitwt"
)
SUDOCREM_SHIPPING = (
    "Sudocrem Bebek Bakım Pişik Kremi 250 G\n\n"
    "🏷️ 217 TL (Kargo Bedava)\n"
    "💬 2.000+ yorum\n"
    "📂 Kozmetik, Kişisel Bakım\n"
    "🛍️ n11\n"
    "🔗 https://onu.al/fixu"
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

    def test_n11_store_prefix_and_currency_less_coupon_final_price(self):
        title = "Dreame L50S Pro Ultra Robot Süpürge Beyaz"
        info = pf.extract_price(DREAME_N11)
        self.assertEqual((info.value, info.confidence, info.reason), (50499, "high", "discounted"))
        self.assertEqual([(c.value, c.role) for c in pf.find_candidates(DREAME_N11)],
                         [(50499, "discounted")])
        self.assertEqual(pf.extract_title(DREAME_N11), title)
        self.assertEqual(bot._search_query(DREAME_N11), title)
        event = SimpleNamespace(message=DREAME_N11, raw_text=DREAME_N11, entities=[], media=None)
        self.assertEqual(bot.smart_dedup_title(event), title)
        with patch.object(bot, "SMART_HEADER", True):
            self.assertEqual(bot.compose_message(event)["header"], f"₺50.499 — {title}")
        self.assertIsNone(pf.extract_price("Kuponla 2 al 1 öde").value)

    def test_explicit_used_condition_price_wins_over_new_reference_price(self):
        title = "Thrustmaster T128 Yarış Direksiyonu"
        info = pf.extract_price(THRUSTMASTER_USED)
        self.assertEqual((info.value, info.confidence, info.reason), (12539, "high", "condition_price"))
        self.assertEqual([c.role for c in pf.find_candidates(THRUSTMASTER_USED)],
                         ["normal", "condition_price"])
        self.assertEqual(bot.offer_terms(THRUSTMASTER_USED)[0], 12539)
        event = SimpleNamespace(message=THRUSTMASTER_USED, raw_text=THRUSTMASTER_USED, entities=[], media=None)
        with patch.object(bot, "SMART_HEADER", True):
            self.assertEqual(bot.compose_message(event)["header"], f"₺12.539 — {title}")
        ambiguous = THRUSTMASTER_USED + "\n♻️ İkinci El (Çok İyi) : 12499 TL"
        self.assertEqual(pf.extract_price(ambiguous).confidence, "low")
        self.assertIsNone(bot.offer_terms(ambiguous)[0])

    def test_price_followed_by_coupon_ile_is_not_discount_amount(self):
        title_homend = "Homend Dustroyer 1272H 2 in 1 Metal Borulu Kablolu Dikey Süpürge"
        title_hair = "Philips 3000 Serisi 3'ü 1 Arada Saç Şekillendirici, Seramik Argan Yağı Katkılı Plakalar"
        title_shaver = "Philips 3000 Serisi Islak&Kuru Tıraş Makinesi, 5 Yönde Dönen Başlıklar, Hassas Düzeltici, S3144/00"
        cases = [
            (HOMEND_COUPON, title_homend, 1376, "discounted", ["discounted"], "₺1.376"),
            (PHILIPS_HAIR_COUPON, title_hair, 1649, "normal", ["normal", "discount_amount"], "₺1.649"),
            (PHILIPS_SHAVER_COUPON, title_shaver, 1749, "normal", ["normal", "discount_amount"], "₺1.749"),
        ]
        for text, title, value, reason, roles, shown in cases:
            with self.subTest(title=title):
                info = pf.extract_price(text)
                self.assertEqual((info.value, info.confidence, info.reason), (value, "high", reason))
                self.assertEqual([c.role for c in pf.find_candidates(text)], roles)
                self.assertEqual(pf.extract_title(text), title)
                event = SimpleNamespace(message=text, raw_text=text, entities=[], media=None)
                with patch.object(bot, "SMART_HEADER", True):
                    self.assertEqual(bot.compose_message(event)["header"], f"{shown} — {title}")
        self.assertIsNone(pf.extract_price("Sepette 250 TL kupon").value)
        self.assertIsNone(pf.extract_price("Sepette 250 TL kupon ile").value)
        self.assertIsNone(pf.extract_price("250 TL kupon ile").value)
        threshold = pf.extract_price("100 TL kupon ile 90 TL")
        self.assertEqual((threshold.value, threshold.reason), (90, "discounted"))
        self.assertEqual([c.role for c in pf.find_candidates("100 TL kupon ile 90 TL")],
                         ["discount_amount", "discounted"])

    def test_chat_promotion_never_replaces_logitech_product_name(self):
        title = (
            "Logitech BRIO 100 Full HD Web Kamerası, Toplantı ve Yayın Amaçlı, Otomatik Işık Dengesi, "
            "Dahili Mikrofon, Gizlilik Kapağı, USB-A, Microsoft Teams, Google Meet, Zoom ile Uyumlu, Siyah"
        )
        self.assertEqual(pf.extract_title(LOGITECH_CHAT), title)
        self.assertEqual(bot._search_query(LOGITECH_CHAT), title)
        self.assertEqual(pf.extract_price(LOGITECH_CHAT).value, 2300)
        event = SimpleNamespace(message=LOGITECH_CHAT, raw_text=LOGITECH_CHAT, entities=[], media=None)
        self.assertEqual(bot.smart_dedup_title(event), title)
        with patch.object(bot, "SMART_HEADER", True):
            self.assertEqual(bot.compose_message(event)["header"], f"₺2.300 — {title}")

    def test_buy_pay_unit_price_overrides_pack_total_only_with_explicit_campaign(self):
        title = "Everest Gümüş Işıklandırmalı Oyuncu Mouse"
        info = pf.extract_price(EVEREST_MULTI)
        self.assertEqual((info.value, info.confidence, info.reason), (124, "medium", "multi_unit"))
        self.assertEqual([c.role for c in pf.find_candidates(EVEREST_MULTI)], ["pack_total", "multi_unit"])
        self.assertTrue(pf.price_matches(EVEREST_MULTI, 124)[0])
        self.assertFalse(pf.price_matches(EVEREST_MULTI, 123)[0])
        event = SimpleNamespace(message=EVEREST_MULTI, raw_text=EVEREST_MULTI, entities=[], media=None)
        with patch.object(bot, "SMART_HEADER", True):
            self.assertEqual(bot.compose_message(event)["header"], f"₺124 — {title}")
        plain_breakdown = "Everest Mouse\n249 TL (Tanesi 124 TL)"
        self.assertEqual(pf.extract_price(plain_breakdown).value, 249)

    def test_ordinal_second_item_amount_is_campaign_not_main_price(self):
        info = pf.extract_price(TRIOMOVE_SECOND)
        self.assertEqual((info.value, info.confidence, info.reason),
                         (None, "none", "yalnız kampanya koşulu tutarı"))
        self.assertEqual([c.role for c in pf.find_candidates(TRIOMOVE_SECOND)], ["campaign"])
        self.assertIsNone(bot.offer_terms(TRIOMOVE_SECOND)[0])
        self.assertFalse(pf.price_matches(TRIOMOVE_SECOND, 1000)[0])
        with_base_price = "TrioMove Protein Bar\n🏷 49 TL\n" + TRIOMOVE_SECOND
        self.assertEqual(pf.extract_price(with_base_price).value, 49)
        self.assertEqual([c.role for c in pf.find_candidates(with_base_price)], ["normal", "campaign"])
        event = SimpleNamespace(message=TRIOMOVE_SECOND, raw_text=TRIOMOVE_SECOND, entities=[], media=None)
        with patch.object(bot, "SMART_HEADER", True):
            self.assertTrue("❓" in bot.compose_message(event)["header"])

    def test_bogo_free_campaign_is_not_zero_product_price(self):
        info = pf.extract_price(BOYNER_BOGO)
        self.assertEqual((info.value, info.confidence), (None, "none"))
        self.assertEqual(pf.extract_title(BOYNER_BOGO), "")
        self.assertIsNone(bot.offer_terms(BOYNER_BOGO)[0])
        self.assertFalse(pf.price_matches(BOYNER_BOGO, 1000)[0])
        event = SimpleNamespace(message=BOYNER_BOGO, raw_text=BOYNER_BOGO, entities=[], media=None)
        with patch.object(bot, "SMART_HEADER", True):
            self.assertEqual(bot.compose_message(event)["header"], "")
        self.assertEqual(bot.smart_dedup_title(event), "")
        real_free = pf.extract_price("Kalem Seti bedava")
        self.assertEqual((real_free.value, real_free.confidence), (0, "medium"))

    def test_lenovo_composed_history_keeps_the_same_offer_for_dedup(self):
        live = SimpleNamespace(message=LENOVO_LP80, raw_text=LENOVO_LP80, entities=[], media=None)
        history = SimpleNamespace(
            message=LENOVO_LP80_NOTIFICATION, raw_text=LENOVO_LP80_NOTIFICATION, entities=[], media=None,
        )
        info = pf.extract_price(LENOVO_LP80)
        self.assertEqual((info.value, info.confidence, info.reason), (440, "medium", "multi_unit"))
        self.assertEqual(bot.offer_terms(live), (440, ""))
        history_terms = bot.offer_terms(history, prefer_header=True)
        self.assertEqual(history_terms, (440, ""))
        self.assertEqual(bot.smart_dedup_title(history), LENOVO_TITLE)
        self.assertEqual(
            bot.dedup_offer_key(bot.smart_dedup_title(live), bot.offer_terms(live)),
            bot.dedup_offer_key(bot.smart_dedup_title(history), history_terms),
        )
        self.assertFalse(bot.better_or_different_offer(history_terms, bot.offer_terms(live)))
        with patch.object(bot, "SMART_HEADER", True):
            self.assertEqual(bot.compose_message(live)["header"], f"₺440 — {LENOVO_TITLE}")

    def test_payment_stage_price_without_discount_amount_selects_final_price(self):
        title = "Mayi Tuz Mayi İyotlu Öğütülmüş Sofra Tuzu 2 kg"
        info = pf.extract_price(MAYI_PAYMENT)
        self.assertEqual((info.value, info.confidence, info.reason), (454, "high", "discounted"))
        self.assertEqual([c.role for c in pf.find_candidates(MAYI_PAYMENT)], ["normal", "discounted"])
        self.assertTrue(pf.price_matches(MAYI_PAYMENT, 454)[0])
        self.assertFalse(pf.price_matches(MAYI_PAYMENT, 453)[0])
        self.assertNotEqual(pf.extract_price("600 TL / ödeme aşamasında 454 TL kargo").value, 454)
        event = SimpleNamespace(message=MAYI_PAYMENT, raw_text=MAYI_PAYMENT, entities=[], media=None)
        with patch.object(bot, "SMART_HEADER", True):
            self.assertEqual(bot.compose_message(event)["header"], f"₺454 — {title}")

    def test_explicit_plus_member_price_wins_without_relaxing_plain_ambiguity(self):
        title = "Colin's Yeşil Erkek Sweatshirt"
        info = pf.extract_price(COLINS_PLUS)
        self.assertEqual((info.value, info.confidence, info.reason), (211, "high", "member_price"))
        self.assertEqual([c.role for c in pf.find_candidates(COLINS_PLUS)], ["normal", "member_price"])
        self.assertTrue(pf.price_matches(COLINS_PLUS, 211)[0])
        self.assertFalse(pf.price_matches(COLINS_PLUS, 210)[0])
        self.assertNotEqual(pf.extract_price("222 TL / Plus Üyelere Özel 211 TL kargo").value, 211)
        event = SimpleNamespace(message=COLINS_PLUS, raw_text=COLINS_PLUS, entities=[], media=None)
        with patch.object(bot, "SMART_HEADER", True):
            self.assertEqual(bot.compose_message(event)["header"], f"₺211 — {title}")
        plain = "Colin's Yeşil Erkek Sweatshirt\n222 TL / 211 TL"
        self.assertEqual(pf.extract_price(plain).confidence, "low")
        conflicting = COLINS_PLUS + " / Plus Üyelere Özel 209 TL"
        self.assertEqual(pf.extract_price(conflicting).confidence, "low")
        self.assertIsNone(bot.offer_terms(conflicting)[0])

    def test_parenthesized_free_shipping_does_not_turn_price_into_shipping_fee(self):
        title = "Sudocrem Bebek Bakım Pişik Kremi 250 G"
        info = pf.extract_price(SUDOCREM_SHIPPING)
        self.assertEqual((info.value, info.confidence, info.reason), (217, "high", "normal"))
        self.assertEqual([c.role for c in pf.find_candidates(SUDOCREM_SHIPPING)], ["normal"])
        self.assertTrue(pf.price_matches(SUDOCREM_SHIPPING, 217)[0])
        self.assertFalse(pf.price_matches(SUDOCREM_SHIPPING, 216)[0])
        event = SimpleNamespace(message=SUDOCREM_SHIPPING, raw_text=SUDOCREM_SHIPPING, entities=[], media=None)
        with patch.object(bot, "SMART_HEADER", True):
            self.assertEqual(bot.compose_message(event)["header"], f"₺217 — {title}")
        self.assertIsNone(pf.extract_price("Sudocrem Bebek Bakım Kremi\nKargo: 30 TL").value)

    def test_notification_header_uses_correct_or_unknown_amount(self):
        expected = [
            (DREAME, "₺20.999 — Dreame D20 Ultra Robot Süpürge Beyaz, 13000Pa Emiş Gücü"),
            (AUHMA, "AUHMA AMG38 Boyun ve Omuz Masaj Aleti ❓"),
            (SHIPPING_PACK, "₺199"),
        ]
        with patch.object(bot, "SMART_HEADER", True):
            for text, wanted in expected:
                event = SimpleNamespace(message=text, raw_text=text, entities=[], media=None)
                with self.subTest(wanted=wanted):
                    self.assertEqual(bot.compose_message(event)["header"], wanted)


if __name__ == "__main__":
    unittest.main()
