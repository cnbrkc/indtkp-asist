"""Başlık temelli, mağaza/yönlendirme URL'sinden bağımsız mükerrer ürün testleri.

Kapsam:
- Aynı/çok yakın başlık + aynı fiyat + aynı mağaza + farklı Amazon / Hepsiburada /
  Trendyol / Paylaş Kazan / yönlendirme URL'leri -> tek bildirim.
- İlk üç anlamlı kelime aynı olsa bile farklı model numarası, ürün kodu, kapasite,
  boyut, paket adedi, renk veya gerçek ürün varyantı -> ayrı bildirim.
- Başlık ve fiyat değişimi değerlendirilir; link/mağaza/koşul bilgileri kimliği etkilemez.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
ROOT_DIR = TESTS_DIR.parent
for _p in (str(ROOT_DIR), str(TESTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bot  # noqa: E402
import test_integration as ti  # noqa: E402
from test_cross_channel_dedup import FIRST_CHANNEL, Harness, SECOND_CHANNEL_ID  # noqa: E402

HOMEND_TITLE = "Homend Profashion 3023H Stand Mikser Antrasit"


def deal(title: str = HOMEND_TITLE, price: str = "6.619", *extra_lines: str) -> str:
    lines = [f"🛍️ {title}", f"💰 Fiyat: {price} TL", *extra_lines]
    return "\n\n".join(line for line in lines if line)


class TitleBasedProductDedupTest(Harness, unittest.TestCase):
    """Başlık temelli eşleştirme ve kritik koruma kuralları (uçtan uca handler yolu)."""

    def test_same_title_different_amazon_affiliate_urls_one_notification(self):
        cases = {
            "farklı amzn.to kısa affiliate linkleri": (
                deal(HOMEND_TITLE, "6.619", "https://amzn.to/3homendA"),
                deal(HOMEND_TITLE, "6.619", "https://amzn.to/3homendB"),
                {},
            ),
            "amzn.eu uygulama paylaşım linki + tam dp affiliate butonu": (
                deal(HOMEND_TITLE, "6.619", "https://amzn.eu/d/8xYzAbc"),
                f"✅ {HOMEND_TITLE}\n\n🏷 6.619 TL\n\n👉 FIRSATA GİT!\n\n🛍 Amazon",
                {"second_buttons": [
                    "https://www.amazon.com.tr/Homend-Profashion-3023H-Stand-Mikser"
                    "/dp/B0DGLLJP1M/ref=nosim?psc=1&tag=tayfa-21&linkCode=ll1"
                ]},
            ),
            "büyük harf I/İ farkı + amzn.to ve dp linki": (
                deal("HOMEND PROFASHION 3023H Stand Mikser Antrasit", "6.619",
                     "https://amzn.to/3homendUpper"),
                deal("Homend Profashion 3023H Stand Mikser, Antrasit", "6.619",
                     "https://www.amazon.com.tr/dp/B0DGLLJP1M?tag=indirimgeldi-21"),
                {},
            ),
        }
        for name, (first, second, buttons) in cases.items():
            with self.subTest(name):
                self.assertPings(first, second, 1, msg=name, **buttons)
                self.assertEqual(bot.STATS["deduped"], 1, msg=name)

    def test_same_title_different_hepsiburada_redirect_urls_one_notification(self):
        cases = {
            "iki farklı app.hb.biz yönlendirme linki": (
                deal(HOMEND_TITLE, "6.619", "https://app.hb.biz/K9xL1m2N3p"),
                deal(HOMEND_TITLE, "6.619", "https://app.hb.biz/P7qR8s9T0u"),
                {},
            ),
            "hb.biz kısa link + tam ürün URL ve kampanya parametreleri": (
                deal(HOMEND_TITLE, "6.619", "https://hb.biz/AbCdEfGh12"),
                f"✅ {HOMEND_TITLE}\n\n🏷 6619 TL\n\n🛍 Hepsiburada",
                {"second_buttons": [
                    "https://www.hepsiburada.com/homend-profashion-3023h-stand-mikser-"
                    "antrasit-p-HBCV00003XYZ12?magaza=Hepsiburada&wt_pc=akakce.c.k.pc"
                ]},
            ),
            "aynı HB ürün kodu + farklı ftid/aff_id parametreleri": (
                deal(HOMEND_TITLE, "6.619",
                     "https://www.hepsiburada.com/homend-profashion-3023h-p-HBCV00003XYZ12"
                     "?ftid=111&url_src=ios-product-detail"),
                deal(HOMEND_TITLE, "6.619",
                     "https://www.hepsiburada.com/homend-profashion-3023h-stand-mikser-"
                     "antrasit-p-HBCV00003XYZ12?aff_id=222&magaza=Homend"),
                {},
            ),
        }
        for name, (first, second, buttons) in cases.items():
            with self.subTest(name):
                self.assertPings(first, second, 1, msg=name, **buttons)
                self.assertEqual(bot.STATS["deduped"], 1, msg=name)

    def test_same_title_different_trendyol_affiliate_urls_one_notification(self):
        cases = {
            "ty.gl ve tyml.gl farklı kısa affiliate linkleri": (
                deal(HOMEND_TITLE, "6.619", "https://ty.gl/homend111"),
                deal(HOMEND_TITLE, "6.619", "https://tyml.gl/homend222"),
                {},
            ),
            "ty.gl kısa link + tam Trendyol affiliate URL": (
                deal(HOMEND_TITLE, "6.619", "https://ty.gl/homend111"),
                deal(HOMEND_TITLE, "6.619",
                     "https://www.trendyol.com/homend/profashion-3023h-stand-mikser-"
                     "antrasit-p-123456789?boutiqueId=61&merchantId=106740"
                     "&adjust_t=mk12x3o_m4o2h83&utm_campaign=affiliate"),
                {},
            ),
            "aynı -p- ürün kimliği + farklı boutique/merchant/adjust parametreleri": (
                deal(HOMEND_TITLE, "6.619",
                     "https://www.trendyol.com/homend/profashion-3023h-p-123456789"
                     "?boutiqueId=61&merchantId=106740"),
                deal(HOMEND_TITLE, "6.619",
                     "https://m.trendyol.com/homend/stand-mikser-p-123456789"
                     "?boutiqueId=99&merchantId=888&adjust_tracker=xyz&pid=42"),
                {},
            ),
        }
        for name, (first, second, buttons) in cases.items():
            with self.subTest(name):
                self.assertPings(first, second, 1, msg=name, **buttons)
                self.assertEqual(bot.STATS["deduped"], 1, msg=name)

    def test_same_title_different_paylas_kazan_urls_one_notification(self):
        cases = {
            "Hepsiburada Paylaş Kazan linkleri": (
                deal(HOMEND_TITLE, "6.619", "https://paylaskazan.hepsiburada.com/paylas/k111"),
                deal(HOMEND_TITLE, "6.619", "https://app.hb.biz/pk222"),
            ),
            "Trendyol Paylaş Kazan linkleri": (
                deal(HOMEND_TITLE, "6.619", "https://ty.gl/paylaskazanA"),
                deal(HOMEND_TITLE, "6.619", "https://ty.gl/paylaskazanB"),
            ),
            "GelirOrtakları / Paylaş Kazan yönlendirme servisleri": (
                deal(HOMEND_TITLE, "6.619",
                     "https://go.gelirortaklari.com/click?aid=11"
                     "&url=https%3A%2F%2Fwww.hepsiburada.com%2Fhomend-p-HBCV00003XYZ12"),
                deal(HOMEND_TITLE, "6.619", "https://paylaskazan.ornek.tr/r/998877"),
            ),
        }
        for name, (first, second) in cases.items():
            with self.subTest(name):
                self.assertPings(first, second, 1, msg=name)
                self.assertEqual(bot.STATS["deduped"], 1, msg=name)

    def test_completely_different_urls_same_title_and_product_identity_one_notification(self):
        cases = {
            "tamamen farklı yönlendirme URL'leri + aynı başlık": (
                deal(HOMEND_TITLE, "6.619", "https://firsata-git.kanal1.example/go/10293"),
                deal(HOMEND_TITLE, "6.619", "https://indirim-linki.kanal2.example/out/88412"),
            ),
            "çok yakın başlık (ek kategori/iyelik eki) + farklı HB URL'leri": (
                deal("Homend Profashion 3023H Stand Mikser Antrasit", "6.619",
                     "https://app.hb.biz/K9xL1m2N3p"),
                deal("Homend Profashion 3023H Mutfak Şefi Stand Mikseri, Antrasit", "6.619",
                     "https://www.hepsiburada.com/homend-profashion-3023h-stand-mikser-"
                     "antrasit-p-HBCV00003XYZ12?wt_pc=aff"),
            ),
            "Türkçe karakter / ASCII farkı + yakın başlık + farklı Trendyol URL'leri": (
                deal("Philips Airfryer HD9252/90 Sıcak Hava Fritözü Siyah", "3.499",
                     "https://ty.gl/philipsA"),
                deal("PHILIPS Airfryer HD9252-90 Fritoz Siyah", "3.499",
                     "https://www.trendyol.com/philips/airfryer-hd9252-90-p-77665544?boutiqueId=61"),
            ),
            "bağlantısız çok yakın başlık (ilk 3 kelime + model + renk aynı)": (
                deal("Homend Profashion 3023H Stand Mikser Antrasit", "6.619"),
                deal("Homend Profashion 3023H Profesyonel Stand Mikser Antrasit", "6.619"),
            ),
        }
        for name, (first, second) in cases.items():
            with self.subTest(name):
                self.assertPings(first, second, 1, msg=name)
                self.assertEqual(bot.STATS["deduped"], 1, msg=name)

    def test_same_first_three_words_different_model_number_not_merged(self):
        cases = {
            "3. kelimedeki model numarası farklı (3023H vs 3024H)": (
                deal("Homend Profashion 3023H Stand Mikser Antrasit", "6.619", "https://app.hb.biz/a1"),
                deal("Homend Profashion 3024H Stand Mikser Antrasit", "6.619", "https://app.hb.biz/a2"),
            ),
            "ilk 3 kelime birebir aynı, 4. kelimedeki model numarası farklı": (
                deal("Homend Stand Mikser 3023H Antrasit", "6.619", "https://ty.gl/m1"),
                deal("Homend Stand Mikser 3024H Antrasit", "6.619", "https://ty.gl/m2"),
            ),
            "ilk 3 kelime aynı, model son eki harfi farklı (3023H vs 3023R)": (
                deal("Homend Stand Mikser 3023H Antrasit", "6.619", "https://ty.gl/m3"),
                deal("Homend Stand Mikser 3023R Antrasit", "6.619", "https://ty.gl/m4"),
            ),
            "ilk 3 kelime aynı (Philips Airfryer XXL), ürün kodu farklı": (
                deal("Philips Airfryer XXL HD9650/90 Fritöz Siyah", "7.999", "https://app.hb.biz/p1"),
                deal("Philips Airfryer XXL HD9285/96 Fritöz Siyah", "7.999", "https://app.hb.biz/p2"),
            ),
        }
        for name, (first, second) in cases.items():
            with self.subTest(name):
                self.assertPings(first, second, 2, msg=name)

    def test_same_product_price_drop_still_notifies(self):
        # Gerçek fiyat düşüşü farklı yönlendirme URL'lerinde de bildirilmeye devam etmeli.
        self.assertPings(
            deal(HOMEND_TITLE, "6.619", "https://app.hb.biz/K9xL1m2N3p"),
            deal(HOMEND_TITLE, "5.999", "https://app.hb.biz/P7qR8s9T0u"),
            2,
            msg="aynı başlık + gerçek fiyat düşüşü",
        )
        self.assertPings(
            deal("Homend Profashion 3023H Stand Mikser Antrasit", "6.619", "https://ty.gl/t1"),
            deal("Homend Profashion 3023H Profesyonel Stand Mikser Antrasit", "5.999", "https://ty.gl/t2"),
            2,
            msg="çok yakın başlık + gerçek fiyat düşüşü",
        )
        # Mevcut tam-TL kuruş politikası (better_or_different_offer) bozulmamalı.
        self.assertPings(
            deal(HOMEND_TITLE, "6.619,90", "https://app.hb.biz/K9xL1m2N3p"),
            deal(HOMEND_TITLE, "6.619", "https://app.hb.biz/P7qR8s9T0u"),
            1,
            msg="aynı tam TL kuruş farkı birleşmeye devam etmeli",
        )

    def test_same_product_conditions_do_not_split_title_price_identity(self):
        for cond in ("Amazon Depo", "Açık kutu ürün", "Outlet fiyatı", "Kutusuz",
                     "2. el", "İkinci El: Yeni Gibi", "Ürün durumu: Yenilenmiş",
                     "Kargo ücretsiz", "Kupon: HB100", "Prime"):
            with self.subTest(condition=cond):
                self.assertPings(
                    deal(HOMEND_TITLE, "6.619", cond, "https://app.hb.biz/c1"),
                    deal(HOMEND_TITLE, "6.619", "https://app.hb.biz/c2"),
                    1,
                    msg=cond,
                )

    def test_same_product_different_real_variant_not_merged(self):
        cases = {
            "farklı renk (Antrasit vs Kırmızı)": (
                deal("Homend Profashion 3023H Stand Mikser Antrasit", "6.619", "https://app.hb.biz/v1"),
                deal("Homend Profashion 3023H Stand Mikser Kırmızı", "6.619", "https://app.hb.biz/v2"),
            ),
            "renk yalnız bir tarafta": (
                deal("Homend Profashion 3023H Stand Mikser Antrasit", "6.619", "https://app.hb.biz/v3"),
                deal("Homend Profashion 3023H Stand Mikser", "6.619", "https://app.hb.biz/v4"),
            ),
            "farklı kapasite (128 GB vs 256 GB)": (
                deal("Samsung Galaxy S24 128 GB Siyah", "32.999", "https://ty.gl/s1"),
                deal("Samsung Galaxy S24 256 GB Siyah", "32.999", "https://ty.gl/s2"),
            ),
            "farklı hacim (150 ml vs 250 ml)": (
                deal("Nivea Fresh Cherry Kadın Sprey Deodorant 150 ml", "86,67", "https://ty.gl/n1"),
                deal("Nivea Fresh Cherry Kadın Sprey Deodorant 250 ml", "86,67", "https://ty.gl/n2"),
            ),
            "farklı paket adedi (2 Adet vs 4 Adet)": (
                deal("Deneme Pamuk Havlu 30x40 Cm Bej 2 Adet", "300", "https://ty.gl/h1"),
                deal("Deneme Pamuk Havlu 30x40 Cm Bej 4 Adet", "300", "https://ty.gl/h2"),
            ),
            "farklı model varyantı (Plus / + / FE)": (
                deal("Samsung Galaxy S24 256 GB Siyah", "39.999", "https://app.hb.biz/g1"),
                deal("Samsung Galaxy S24 Plus 256 GB Siyah", "39.999", "https://app.hb.biz/g2"),
            ),
            "cihaz ile aksesuarı (iPhone 15 vs iPhone 15 Kılıf)": (
                deal("Apple iPhone 15 128 GB Siyah", "499", "https://ty.gl/ip1"),
                deal("Apple iPhone 15 128 GB Siyah Kılıf", "499", "https://ty.gl/ip2"),
            ),
            "ilk 3 kelime aynı ama ürün türü farklı (Biberon vs Emzik)": (
                deal("Philips Avent Doğal Biberon", "349", "https://app.hb.biz/av1"),
                deal("Philips Avent Doğal Emzik", "349", "https://app.hb.biz/av2"),
            ),
        }
        for name, (first, second) in cases.items():
            with self.subTest(name):
                self.assertPings(first, second, 2, msg=name)

    def test_links_store_and_conditions_are_not_identity(self):
        cases = [
            (deal(HOMEND_TITLE, "6.619", "https://app.hb.biz/K9xL1m2N3p"),
             deal(HOMEND_TITLE, "6.619", "https://ty.gl/homend111")),
            (deal(HOMEND_TITLE, "6.619", "https://amzn.to/3homendA"),
             deal(HOMEND_TITLE, "6.619", "https://app.hb.biz/K9xL1m2N3p")),
            (deal(HOMEND_TITLE, "6.619", "Kargo ücretsiz", "https://app.hb.biz/k1"),
             deal(HOMEND_TITLE, "6.619", "Kargo 49,90 TL", "https://app.hb.biz/k2")),
            (deal(HOMEND_TITLE, "6.619", "Kupon: HB100", "https://app.hb.biz/kp1"),
             deal(HOMEND_TITLE, "6.619", "Kupon: HB250", "https://app.hb.biz/kp2")),
            (deal(HOMEND_TITLE, "6.619",
                  "https://www.amazon.com.tr/dp/B0DGLLJP1M?psc=1&tag=indirimalarmi-21"),
             deal(HOMEND_TITLE, "6.619",
                  "https://www.amazon.com.tr/dp/B0ZZZ99999?tag=indirimgeldi-21")),
        ]
        for first, second in cases:
            with self.subTest(first=first, second=second):
                self.assertPings(first, second, 1)

    def test_restart_preload_with_redirect_link_suppresses_second_channel_different_url(self):
        history = (
            f"₺6.619 — {HOMEND_TITLE}\n\n"
            f"🛍️ {HOMEND_TITLE}\n\n"
            "💰 Fiyat: 6.619 TL\n\n"
            "https://app.hb.biz/K9xL1m2N3p\n\n"
            "🔗 Mesajı Gör: https://t.me/firsatz/140001\n\n"
            f"{FIRST_CHANNEL}"
        )
        ti.FakeClient.default_history = [(ti.GROUP_ID, history, 123, None, None, [], 900)]
        client = self.start()
        self.offers(client, [(
            SECOND_CHANNEL_ID, 201,
            deal(HOMEND_TITLE, "6.619",
                 "https://www.hepsiburada.com/homend-profashion-3023h-stand-mikser-"
                 "antrasit-p-HBCV00003XYZ12?magaza=Hepsiburada&wt_pc=aff"),
        )])
        self.assertEqual(len(self.ping_calls), 0, "geçmişteki HB bildirimi ikinci kanalı bastırmalı")
        self.assertEqual(bot.STATS["deduped"], 1)


    def test_iffalcon_50u65_editorial_titles_at_same_price_merge(self):
        titles = [
            "iFFALCON 50U65 50 İnç Smart TV 4K UHD HDR 10 Gaming Google TV, "
            "Dinamik Renk Geliştirme, Dolby Audio, MEMC, İnce Tasarımlı Game Master (2026 Modeli)",
            "iFFALCON 50U65 4K Ultra HD 50” 127 Ekran Uydu Alıcılı Google Smart LED TV",
            "iFFALCON 50U65 50 inch 4K UHD HDR10 Google Smart TV Game Master",
            "iFFALCON 50U65 UHD Smart TV 50 inç, Google TV ve Dolby Audio",
        ]
        for title in titles[1:]:
            with self.subTest(title=title):
                self.assertTrue(bot.titles_match_product(titles[0], title))

        client = self.start()
        items = []
        extra = ["Prime", "Ücretsiz kargo", "Kupon: TEST", "2. el"]
        links = ["https://www.amazon.com.tr/dp/B0AAAA0001", "https://ty.gl/iffalcon1",
                 "https://magaza.example/urun-a", "https://magaza.example/urun-b"]
        for index, title in enumerate(titles):
            items.append((self.first_id if index % 2 == 0 else SECOND_CHANNEL_ID,
                          701 + index,
                          deal(title, "14.999", extra[index], links[index])))
        self.offers(client, items)
        self.assertEqual(len(self.ping_calls), 1)
        self.assertEqual(bot.STATS["deduped"], 3)



class TitleMatcherUnitTests(unittest.TestCase):
    """Başlık normalizasyonu ve model kodu birim testleri."""

    def test_unit_and_turkish_character_normalization_matches(self):
        self.assertTrue(bot.titles_match_product(
            "Philips Airfryer XXL 6,2 L Sıcak Hava Fritözü HD9280 Siyah",
            "PHILIPS Airfryer XXL 6.2 Litre Fritoz HD9280 Siyah",
        ))
        self.assertTrue(bot.titles_match_product(
            'Next YE-55GFSG8 55" 4K Google TV',
            "Next YE-55GFSG8 55 inç 4K Google TV",
        ))
        self.assertTrue(bot.titles_match_product(
            "Hepsiburada: Homend Profashion 3023H Stand Mikser Antrasit",
            "Homend Profashion 3023-H Stand Mikser, Antrasit",
        ))

    def test_plus_duplicate_numbers_and_accessories_do_not_merge(self):
        self.assertFalse(bot.titles_match_product(
            "Roborock S8 Robot Süpürge Beyaz",
            "Roborock S8+ Robot Süpürge Beyaz",
        ))
        self.assertFalse(bot.titles_match_product(
            "Lenovo IdeaPad Slim 3 16GB 512GB Dizüstü Bilgisayar",
            "Lenovo IdeaPad Slim 3 16GB 16GB Dizüstü Bilgisayar",
        ))
        self.assertFalse(bot.titles_match_product(
            "Samsung Galaxy S24 256 GB Siyah",
            "Samsung Galaxy S24 256 GB Siyah Ekran Koruyucu",
        ))



if __name__ == "__main__":
    unittest.main()
