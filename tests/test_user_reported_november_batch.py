"""Kullanıcının 2026-11 bildirdiği 13 örneğin kök neden regresyonları.

Her örnek AYRI bir kök nedendir; testler tek tek o nedeni kilitler:

1. ``(7,6 TL adet)`` — parantez içi BİRİM fiyatı ile toplam aynı havuzda eşit
   güçteydi; toplam "belirsiz" sayılıp başlık ``❓`` kalıyordu.
2. ``Prime Üyelere Özel 24.999TL`` + altında BAŞKA ürünler — havuzdaki en
   güçlü indirimli tutar (6.881 TL, oyuncu koltuğu) başlıktaki ürünün fiyatı
   sanılıyordu.
3. ``SPORTİF150 kodu ile … 600/150TL`` — kupon kodu "model kodu" gibi puan
   alıp başlık oluyordu.
4. ``Migros Sanal Market'te`` — yalnız mağaza + konum eki olan satır, ürün
   satırıyla eşit puanda en erken satır kuralıyla başlık seçiliyordu.
5. ``Yeni indirim kodu KASIM150 geldi`` — kod duyurusu satırı model kodu
   bonusuyla gerçek ürün adını geçiyordu.
6. ``600 TL Üzerine 150 TL İndirim sağlar`` — kampanya KOŞULU cümlesi ürün
   adından yüksek puan alıyordu (genel başlık saymak dedup kimliğini
   öldürdüğü için yalnız başlık puanı düşürülür).
7-8. ``Kodu ile 400 TL'ye 100 TL Indirim`` / ``koduyla 200₺ indirim`` —
   indirim/eşik tutarı ürün fiyatı sayılıyordu. Türkçe büyük harf ``INDIRIM``
   ``norm`` ile ``ındırım`` olduğu için ``indirim`` köküyle eşleşmiyordu.
9. ``Fiyat: 519 TL / … 419 TL`` — tek ürünlü gönderide iki fiyat satırı
   "çok ürünlü" sanılıp 519'a düşülüyordu.
10. ``her an değişebilir`` — kaynak şablonunun sabit uyarı satırı, uzun ürün
    adını uzunluk cezasıyla geçip başlık oluyordu.
11. ``53240₺ (World Karta …) Acer Nitro …`` — katalog başlığı marka için iki
    ARDIŞIK büyük harf istiyordu; "Acer" geçemeyince taksit metni başlık oldu.
12. Aynı düzen, GIGABYTE (tamamı büyük) — zaten doğruydu, korunuyor.
13. Çoklu paylaşım notunda grup adları virgülle birleştiriliyordu; adın kendisi
    virgül içerdiğinde adlar bölünüp İKİNCİ grubun mesaj adresi kayboluyordu.
"""
from types import SimpleNamespace
import unittest

import bot
import price_filter as pf


_SMART_HEADER = bot.SMART_HEADER


def setUpModule():
    # Üretim düzeni: config.json'da smart_header açık. Başlık ("₺274 — …")
    # yalnız bu bayrakla kurulur; testler gerçek üretim düzenini ölçer.
    bot.SMART_HEADER = True


def tearDownModule():
    bot.SMART_HEADER = _SMART_HEADER


def event(text):
    return SimpleNamespace(message=text, raw_text=text, entities=[], media=None)


def composed(text, name=None):
    return bot.compose_message(event(text), source_name=name)


ASK = """36 adet askı 274 TL (7,6 TL adet)

Sepete 3 adet ekleyin ve ASKI100 kodunu uygulayın.

FIRSATA GİT

Hepsiburada"""

ASUS = """⚡ Asus Vivabook Go AMD Ryzen 5 8GB RAM 512GB SSD Laptop

✅Prime Üyelere Özel 24.999TL - Piyasası 27.897TL+
🔻Rampage Oyuncu Koltuğu Kumal Sepette 6.881TL - Peşin Fiyatına 6 Taksit İmkanı
▪️Lumberjack Erkek Siyah Outdoor Ayakkabı Ürün Altındaki 300TL Kupon İle 2.416TL / Linkte Bulunan Ürünlerde 2500/300TL Kupon Aktif

🚀 Fiyat her an değişebilir, kaçırma!"""

SPORTIF = """🚴🏻‍♀️SPORTİF150 kodu ile Spor & Outdoor Ürünlerinde 600/150TL İndirim!

👉Ürünlere Git: https://app.hb.biz/7BOjbzEjo2Bz

🚨Kod sınırlı sayıda, kullanılmıyorsa tükenmiştir."""

MIGROS_SANAL = """📢 Migros Sanal Market'te

🛒 Demlik ve Bardak Siyah Çaylarda , Meyveli Maden Sularında geçerli
Aynı Üründen 2 Tane alana
biri tutarında Money hesabına iade

📌 https://firsatatik.la/migros

Migros"""

KASIM150 = """Yeni indirim kodu KASIM150 geldi
600 TL Üzerine 150 TL İndirim sağlar🔥
https://app.hb.biz/hX0hhHNgWJz0

KASIM150 kodu ile 453 TL🔥

Altınyıldız Classics Erkek Haki Anti-pilling Tüylenme Yapmayan Standart Fit Dik Yaka Soğuk Geçirmez Polar Sweatshirt
https://app.hb.biz/bdj750zT3wxz"""

EVTEKSTILI = """EVTEKSTİLİ100 Kodu ile Ev Tekstili, Aydınlatma ve
Dekorasyonda 400 TL'ye 100 TL Indirim!

FIRSATA GİT

Hepsiburada"""

FIRSAT1000 = """⚡ Amazon Alışverişlerinizde Geçerli 12.000 TL'ye 1.000 TL İndirim Kodu

FIRSAT1000

Kodu hesabınızda aktif etmek ve kullanmak için aşağıdaki bağlantıya tıklayın.

🎯 İndirim bitmeden sepete at!"""

FAIRY = """🛍️ Fairy Platinum Bulaşık Makinesi Deterjanı Kapsülü/Tableti 72 Yıkama Limon Kokulu

💰 Fiyat : 519 TL / ödeme aşamasında TEMIZLIK200 koduyla 2 adet alımda adet başı 419 TL
🗓️ 60 Günün En Düşük Fiyatı

🛒 https://link.amazon/B0c8owY8d"""

BOSCH = """⚡ Bosch Home and Garden X/Line 40 Parça Aksesuar Seti, Matkap uçları, Vidalama uçları, Üniversal Tutucu, Havşa açıcı, Uzun Ömürlü, Yeşil

✅ 647,90 ₺
📌 (60 günün en düşük fiyatı)
🎯 %2 anlık indirim

🚀 Fiyat her an değişebilir, kaçırma!"""

ACER = ("⚡ 53240₺ ( 🎉World Karta Vade Farksız 6 taksit❗️ Acer teknik servisi yükseltme "
        "için laptopu bize yollayın diyor) Acer Nitro ANV15-52 NH.QZ7EY.001 Intel Core "
        "i5 13420H (8C/12T, 4.6GHz’e kadar, 12MB Intel Smart Cache) RTX 5050 (8GB, 75W), "
        "16/512GB (1x16GB 3200MHz DDR4 RAM, 512GB Gen4 M.2 SSD takılı, boşta M.2 yuvası "
        "yok), 15.6” FHD IPS 300nit %45NTSC 165Hz ekran, sarı arka aydınlatmalı klavye, "
        "76Wh pil, 2.11kg, 135W adaptör, FDOS :  Trendyol’da 56616₺ (🎉Bonus Kartlara "
        "Vade Farksız 12 taksit) :\n\n🚀 Fiyat her an değişebilir, kaçırma!")

GIGABYTE = ("⚡ 129999₺  (🎉 Seçili kartlara vade farksız 12 taksit - ❗️115W ekran kartı var "
            "ve RAM yükseltme imkanı yok) GIGABYTE GAMING A18 PRO Slim  Intel Core 7 240H "
            "(10C/16T, 5.2GHz Turbo, 24MB L3 cache) RTX 5070Ti (12GB, 115W + Mux) "
            "32GB/1TB (32GB LPDDR5X lehimli RAM, 1x Gen4 M.2 SSD yuvası boşta) 18” 165Hz "
            "3ms %100sRGB 300nit QHD IPS ekran, 76Wh pil, 2TB SSD, FDOS :\n\n"
            "🚀 Fiyat her an değişebilir, kaçırma!")

CK_PARFUM = """Calvin Klein parfümlerde %30 indirim + EFSANEKOZMO200 koduyla 200₺ indirim birleşiyor

Satıcı Calvin Klein

https://app.hb.biz/tQUZ7BQe3alG"""


class ReportedPriceTests(unittest.TestCase):
    def test_bracket_unit_price_keeps_the_total(self):
        """1) (7,6 TL adet) birim fiyattır; toplam 274 TL korunur."""
        info = pf.extract_price(ASK)
        self.assertEqual(info.value, 274)
        self.assertIn(info.confidence, {"medium", "high"})
        self.assertEqual(composed(ASK)["header"], "₺274 — 36 adet askı")

    def test_heading_product_price_beats_other_products_in_the_same_post(self):
        """2) Alt satırlardaki BAŞKA ürünlerin fiyatı başlığın fiyatı değildir."""
        self.assertEqual(pf.extract_price(ASUS).value, 24999)
        self.assertIn("Prime Üyelere", _first_price_line(ASUS))
        self.assertNotIn("6.881", composed(ASUS, "İNDİRİM & FIRSAT TR")["header"])

    def test_coupon_code_is_not_a_model_code(self):
        """3) Kupon kodu model kodu bonusu almaz."""
        title = pf.extract_title(SPORTIF)
        self.assertNotEqual(title, "SPORTIF150")
        self.assertIn("SPORT", pf.extract_title(SPORTIF).upper())

    def test_bare_store_mention_is_not_the_title(self):
        """4) "Migros Sanal Market'te" ürün adı değildir."""
        self.assertTrue(pf.is_generic_title("📢 Migros Sanal Market'te"))
        title = pf.extract_title(MIGROS_SANAL)
        self.assertNotIn("Sanal Market'te", title)
        self.assertIn("Demlik", title)

    def test_coupon_announcement_line_is_not_the_title(self):
        """5) "Yeni indirim kodu KASIM150 geldi" başlık değildir."""
        title = pf.extract_title(KASIM150)
        self.assertNotIn("KASIM150 geldi", title)
        self.assertIn("Altınyıldız", title)

    def test_threshold_sentence_loses_title_score_only(self):
        """6) Kampanya koşulu cümlesi başlık puanı kaybeder, kimlik olarak kalır."""
        self.assertLess(
            pf._score_line("600 TL Üzerine 150 TL İndirim sağlar",
                           "600 TL Üzerine 150 TL İndirim sağlar", 1, set()),
            pf._score_line(KASIM150.splitlines()[6],
                           pf._trim_edges(pf._remove_noise(KASIM150.splitlines()[6])), 6, set()),
        )
        self.assertIn("Altınyıldız", pf.extract_title(KASIM150))

    def test_dotless_capital_discount_word_is_recognised(self):
        """7-8) "Indirim" (büyük I) da indirimdir: tutar fiyat değildir."""
        self.assertTrue(pf._stem_in("ındırım", pf._AFTER_NEG))
        self.assertIsNone(pf.extract_price(EVTEKSTILI).value)
        self.assertIsNone(pf.extract_price(CK_PARFUM).value)
        self.assertFalse(composed(EVTEKSTILI)["header"].startswith("₺"))

    def test_coupon_discount_amount_is_not_the_item_price(self):
        """8) "EFSANEKOZMO200 koduyla 200₺ indirim" indirim tutarıdır."""
        info = pf.extract_price(CK_PARFUM)
        self.assertIsNone(info.value)
        # Kupon duyurusu: fiyat yok, `❓` de yazılmaz (kullanıcı isteği).
        # Üst satır gövde cümlesini taşır; orada "200₺" geçmesi doğaldır
        # (indirim tutarı kaynağın kendi metnidir), önemli olan FİYAT olarak
        # yorumlanmaması: ayrı fiyat alanı boş kalır.
        composed = bot.compose_message(event(CK_PARFUM), source_name="İndirim Kılavuzu")
        self.assertEqual(composed["price"], "")
        self.assertNotIn("❓", composed["header"])

    def test_single_product_two_price_lines_is_not_multi_product(self):
        """9) Tek üründeki "Fiyat: 519 / … 419" çok ürünlü sayılmamalı."""
        info = pf.extract_price(FAIRY)
        self.assertEqual(info.value, 419)
        self.assertIn(info.confidence, {"medium", "high"})

    def test_cta_warning_line_is_not_the_title(self):
        """10) "her an değişebilir" sabit uyarı satırı başlık olmamalı."""
        self.assertTrue(pf.is_generic_title("her an değişebilir"))
        self.assertTrue(pf.is_generic_title("🚀 Fiyat her an değişebilir, kaçırma!"))
        self.assertTrue(composed(BOSCH)["header"].startswith("₺647 — Bosch"))

    def test_catalog_title_accepts_mixed_case_brand(self):
        """11) "Acer Nitro …" katalog yolundan geçer (tamamı büyük olması gerekmez)."""
        self.assertTrue(pf.priced_catalog_title(ACER).startswith("Acer Nitro"))
        header = composed(ACER, "İNDİRİM & FIRSAT TR")["header"]
        self.assertTrue(header.startswith("₺53.240 — Acer Nitro"), header)
        self.assertNotIn("her an değişebilir", header)

    def test_catalog_title_still_accepts_uppercase_brand(self):
        """12) GIGABYTE düzeni değişmedi."""
        self.assertTrue(pf.priced_catalog_title(GIGABYTE).startswith("GIGABYTE GAMING"))
        self.assertTrue(composed(GIGABYTE, "İNDİRİM & FIRSAT TR")["header"]
                        .startswith("₺129.999 — GIGABYTE"))


def _utf16_slice(text, offset, length):
    """UTF-16 birimiyle dilimle (emoji/astral karakterler iki birimdir)."""
    units = 0
    start = end = None
    for index, char in enumerate(text):
        if start is None and units >= offset:
            start = index
        units += 2 if ord(char) > 0xFFFF else 1
        if start is not None and units >= offset + length:
            end = index + 1
            break
    if start is None:
        return ""
    return text[start:end if end is not None else len(text)]


def _first_price_line(text):
    return next(line for line in text.splitlines() if "24.999" in line)


class BadgeNameTests(unittest.TestCase):
    """13) Virgül içeren grup adları rozette bölünmemeli; linkleri kaymamalı."""

    def setUp(self):
        self.name_a = "İndirim Varsa Anlık Fırsat Alışveriş Kampanya Kupon Haberleri, İndirimde Al"
        self.name_b = "İNDİRİM & FIRSAT TR 🛒 Sıcak Fırsatlar, Alışveriş & Kupon"

    def _entry(self):
        entry = bot.new_dedup_entry("t", object(), self.name_a, self.name_a)
        bot.note_dedup_source(entry, self.name_a, "https://t.me/g1/100")
        bot.note_dedup_source(entry, self.name_b, "https://t.me/g2/200")
        return entry

    def test_names_with_commas_survive_the_round_trip(self):
        entry = self._entry()
        badge = bot.dedup_badge(2, sources=entry["sources"])
        self.assertEqual(bot.dedup_badge_sources(badge), [self.name_a, self.name_b])
        self.assertEqual(len(bot.dedup_badge_name_spans(badge)), 2)

    def test_each_name_carries_its_own_message_link(self):
        entry = self._entry()
        badge = bot.dedup_badge(2, sources=entry["sources"])
        links = bot.dedup_badge_links(entry, entry["sources"])
        entities = bot.dedup_badge_entities(badge, bot_api=True, offset=0, links=links)
        # Entity konumları UTF-16 birimidir; metni aynı birimle dilimle.
        labelled = {entity["url"]: _utf16_slice(badge, entity["offset"], entity["length"])
                    for entity in entities if entity["type"] == "text_link"}
        self.assertEqual(labelled, {
            "https://t.me/g1/100": self.name_a,
            "https://t.me/g2/200": self.name_b,
        })

    def test_single_group_still_prints_the_explicit_note(self):
        badge = bot.dedup_badge(2, sources=["Fırsatlar (Sadece Paylaşım)"])
        self.assertEqual(badge, "📌 2 kere paylaşıldı: Fırsatlar (Sadece Paylaşım) · tek grup")
        self.assertEqual(bot.dedup_badge_sources(badge), ["Fırsatlar (Sadece Paylaşım)"])

    def test_legacy_comma_separated_badges_still_parse(self):
        legacy = "📌 2 kere paylaşıldı: FirsatZ, İndirimde Al"
        self.assertEqual(bot.dedup_badge_sources(legacy), ["FirsatZ", "İndirimde Al"])

    def test_single_group_name_with_comma_keeps_its_link(self):
        """TEK gruplu rozet: ad virgül içeriyorsa eski-format sanılıp BÖLÜNMEMELİ.

        Virgülden bölme "tek parça kaldıysa eski biçimdir" varsayımına dayanıyordu;
        yeni biçimde de tek parça olur. Sonuç: ad ikiye bölünüyor ve mesaj linki
        (``text_link``) hiç üretilmiyordu — kullanıcının "link yok" şikâyeti.
        Ayırt edici tek ölçüt eski biçimin son eki (``· tek grup`` / ``— KAÇIRMA!``).
        """
        name = "İNDİRİM & FIRSAT TR 🛒 Sıcak Fırsatlar, Alışveriş & Kupon"
        entry = bot.new_dedup_entry("t", object(), name, name)
        bot.note_dedup_source(entry, name, "https://t.me/g1/100")
        badge = bot.dedup_badge(2, sources=entry["sources"])
        self.assertEqual(bot.dedup_badge_sources(badge), [name])
        links = bot.dedup_badge_links(entry, entry["sources"])
        entities = bot.dedup_badge_entities(badge, bot_api=True, offset=0, links=links)
        links_found = [e for e in entities if e["type"] == "text_link"]
        self.assertEqual(len(links_found), 1, badge)
        self.assertEqual(links_found[0]["url"], "https://t.me/g1/100")
        self.assertEqual(_utf16_slice(badge, links_found[0]["offset"], links_found[0]["length"]), name)

    def test_legacy_badge_keeps_comma_split_with_group_count_suffix(self):
        """Eski biçim son eki (``— KAÇIRMA!``) varsa virgülden bölme SÜRMELİ."""
        legacy = "🚨 3 kere paylaşıldı: FirsatZ, İndirimde Al, Fırsat Kupon — KAÇIRMA!"
        self.assertEqual(
            bot.dedup_badge_sources(legacy), ["FirsatZ", "İndirimde Al", "Fırsat Kupon"],
        )

    def test_new_format_multi_group_names_survive_round_trip(self):
        names = [
            "İndirim & Fırsat, Alışveriş",
            "Kupon, Kampanya TR",
            "Sıcak Fırsatlar, İndirimde Al",
        ]
        entry = bot.new_dedup_entry("t", object(), names[0], names[0])
        for index, name in enumerate(names):
            bot.note_dedup_source(entry, name, f"https://t.me/g{index}/1")
        badge = bot.dedup_badge(3, sources=entry["sources"])
        self.assertEqual(bot.dedup_badge_sources(badge), names)


class UnchangedBehaviourTests(unittest.TestCase):
    """Doğru tespitler korunuyor: bu örnekler değişmedi."""

    def test_multi_product_list_without_distinct_prices_stays_ambiguous(self):
        text = "Pamuk Havlu\n100 TL\nCam Bardak\n200 TL"
        self.assertEqual(len(pf.find_candidates(text)), 2)
        self.assertEqual(pf.extract_price(text).value, 100)
        self.assertEqual(pf.extract_price(text).confidence, "low")

    def test_explicit_unit_price_is_unchanged(self):
        self.assertEqual(pf.extract_price("Ürün\n🏷 413 TL (137 TL/Adet)").value, 413)

    def test_plain_discount_amount_wording_still_works(self):
        self.assertEqual(pf.extract_price("Ürün\nSepette 50 TL indirim").value, None)
        self.assertEqual(pf.extract_price("Ürün\nFiyat: 1.299 TL").value, 1299)

    def test_coupon_discounted_price_is_still_a_price(self):
        """Kod sonrası ÖDENECEK tutar hâlâ fiyattır (indirim tutarı değil)."""
        text = "Filtre Kahve 1 kg\n2727 TL / KASIM250 koduyla 2477 TL"
        self.assertEqual(pf.extract_price(text).value, 2477)


class HeaderPriceAccuracyTests(unittest.TestCase):
    """Doğrulama sırasında bulunan iki ek kök neden (PR incelemesi)."""

    def setUp(self):
        self._smart = bot.SMART_HEADER
        bot.SMART_HEADER = True

    def tearDown(self):
        bot.SMART_HEADER = self._smart

    def _header(self, text, **kwargs):
        obj = SimpleNamespace(
            message=text, raw_text=text, entities=[], media=None, reply_markup=None,
        )
        return bot.compose_message(obj, source_name="Test Grubu", has_keyboard=False, **kwargs)

    def test_old_price_beside_the_real_price_does_not_make_it_unknown(self):
        """"Piyasası 27.897TL" eski fiyat, başlık havuzuna girmemeli.

        Havuz hem ``price`` hem ``old`` sınıfını topladığı için 24.999 ile 27.897
        "belirsiz" sayılıyor, güven ``low``a düşüyor ve üst satır ``❓`` kalıyordu.
        Doğru fiyat bulunduğu hâlde gösterilmiyordu (kullanıcı örneği 2).
        """
        text = (
            "⚡ Asus Vivabook Go AMD Ryzen 5 8GB RAM 512GB SSD Laptop\n\n"
            "✅Prime Üyelere Özel 24.999TL - Piyasası 27.897TL+\n"
            "🔻Rampage Oyuncu Koltuğu Sepette 6.881TL\n\n"
            "🚀 Fiyat her an değişebilir, kaçırma!"
        )
        info = pf.extract_price(text)
        self.assertEqual(info.value, 24999)
        self.assertIn(info.confidence, {"high", "medium"})
        self.assertEqual(
            self._header(text)["header"],
            "₺24.999 — Asus Vivabook Go AMD Ryzen 5 8GB RAM 512GB SSD Laptop",
        )

    def test_prime_line_alone_yields_its_price(self):
        """2. örnek Asus'tan BAĞIMSIZ da doğru olmalı (tek satırlık ölçüm)."""
        self.assertEqual(
            pf.extract_price("✅Prime Üyelere Özel 24.999TL - Piyasası 27.897TL+").value,
            24999,
        )

    def test_non_coupon_campaign_announcement_gets_no_question_mark(self):
        """Kupon sözcüğü geçmeyen kampanya duyurusu da ❓ almamalı (örnek 4)."""
        text = (
            "🛒 Migros'ta Demlik ve Bardak Siyah Çay Ürünlerinde 2 Öde 1'i Money Hediye\n"
            "📅 16 – 18 Kasım 2026"
        )
        result = self._header(text)
        self.assertNotIn("❓", result["header"])
        self.assertIn("Money Hediye", result["header"])

    def test_money_refund_wording_gets_no_question_mark(self):
        """Örnek 5'in gövdesi: "Money iade" kalıbı da duyurudur."""
        text = (
            "🛒 Demlik ve Bardak Siyah Çaylarda geçerli\n"
            "Aynı Üründen 2 Tane alana\nbiri tutarında Money hesabına iade"
        )
        self.assertNotIn("❓", self._header(text)["header"])

    def test_dotless_capital_i_campaign_word_is_recognized(self):
        """``Indirim`` (noktasız I) kampanya sözcüğü sayılmalı — örnek 7.

        ``normalize`` Türkçe küçültme yaptığı için ``Indirim`` → ``ındirim`` olur
        ve ``indirim`` deseni kaçar; ``normalize`` davranışı değiştirilmeden yalnız
        kampanya sözcüğü tanımasında katlanır.
        """
        self.assertTrue(bot._is_coupon_announcement("EVTEKSTİLİ100 Kodu ile\nDekorasyonda 400 TL'ye 100 TL Indirim!"))
        self.assertNotIn(
            "❓",
            self._header("EVTEKSTİLİ100 Kodu ile Ev Tekstili\nDekorasyonda 400 TL'ye 100 TL Indirim!")["header"],
        )

    def test_price_bearing_post_is_never_treated_as_coupon(self):
        """Kullanıcı şartı: "fiyat olan gönderiyi kupon sanıp çöpe atma"."""
        priced = "⚡ Bosch Home and Garden X/Line 40 Parça Aksesuar Seti\n\n✅ 647,90 ₺"
        self.assertFalse(bot._is_coupon_announcement(priced))
        couponed = "Filtre Kahve 1 kg\n2727 TL / KASIM250 koduyla 2477 TL"
        self.assertEqual(pf.extract_price(couponed).value, 2477)
        self.assertIn("₺2.477", self._header(couponed)["header"])

    def test_safe_price_is_detected_independently_of_coupon_classification(self):
        """Fiyat ölçümü kupon sınıflandırmasından BAĞIMSIZ olmalı.

        ``_has_safe_price`` doğrudan ``price_filter.extract_price``a bakar; bu
        test ikisinin aynı gerçeği söylediğini kilitler. Aksi hâlde "fiyat var mı"
        kararı iki ayrı yerde yorumlanır ve sessizce ayrışabilir.
        """
        priced = "Philips Ütü 1.499 TL\n2 Öde 1'i Money Hediye"
        self.assertTrue(bot._has_safe_price(priced))
        self.assertFalse(bot._is_coupon_announcement(priced))
        announcement = "Migros'ta 2 Öde 1'i Money Hediye"
        self.assertFalse(bot._has_safe_price(announcement))
        self.assertTrue(bot._is_coupon_announcement(announcement))

    def test_priced_money_post_keeps_its_price_without_branch_order(self):
        """Fiyatlı Money gönderisinde fiyat DAİMA korunur.

        Dal sırası ne olursa olsun: ``_is_coupon_announcement`` güvenli fiyat
        gördüğünde False döner, böylece fiyatı silen kupon dalı devreye girmez.
        """
        variants = [
            "Philips Ütü 1.499 TL\n2 Öde 1'i Money Hediye",
            "Philips Ütü\n\n1.499 TL\n\n2 Öde 1'i Money Hediye",
            "Philips Ütü\n2 alana 1 TL kampanyası\n💰 3.250 TL",
            "Dyson Süpürge 12.499 TL\nMoney hesabına iade",
        ]
        for text in variants:
            self.assertFalse(bot._is_coupon_announcement(text), text)
            header = self._header(text)["header"]
            self.assertIn("₺", header, text)
            self.assertNotIn("❓", header, text)

    def test_announcement_without_price_never_shows_question_mark(self):
        """Fiyatsız Money/kampanya duyurusunda ❓ GÖSTERİLMEZ (dal sırasından bağımsız)."""
        variants = [
            "Migros'ta Demlik ve Bardak Siyah Çay Ürünlerinde 2 Öde 1'i Money Hediye",
            "Migros Sanal Market'te\n2 Öde 1'i Money Hediye",
            "Aynı Üründen 2 Tane alana biri tutarında Money hesabına iade",
            "Hopi 200 TL ve üzeri alışverişlerde 50 TL indirim kuponu",
            "SNEAKER500 koduyla n11'de Sneaker Ürünlerinde 3000 TL ve üzeri 500 TL indirim",
        ]
        for text in variants:
            self.assertTrue(bot._is_coupon_announcement(text), text)
            self.assertNotIn("❓", self._header(text)["header"], text)

    def test_uncertain_multi_product_keeps_question_mark(self):
        """Normal ürün gönderisindeki belirsizlik mekanizması DEĞİŞMEZ."""
        text = "Pamuk Havlu\n100 TL\nCam Bardak\n200 TL"
        self.assertEqual(pf.extract_price(text).confidence, "low")
        self.assertFalse(bot._is_coupon_announcement(text))
        self.assertFalse(bot._has_safe_price(text))

    def test_product_price_wins_over_campaign_sentence_in_same_post(self):
        """Kampanya cümlesi + ürün fiyatı bir arada: fiyat korunur (örnek 13)."""
        text = (
            "Calvin Klein parfümlerde %30 indirim + EFSANEKOZMO200 koduyla 200₺ indirim birleşiyor\n\n"
            "Satıcı Calvin Klein"
        )
        result = self._header(text)
        self.assertNotIn("❓", result["header"])
        # 200₺ indirim tutarıdır; ürün fiyatı değildir.
        self.assertNotIn("₺200 —", result["header"])


if __name__ == "__main__":
    unittest.main()
