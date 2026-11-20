"""Bildirilen dört örnek ve ürün linki altyapısı; tümü çevrimdışıdır.

Kaynak mesajların yapısı (kalın/gizli hyperlink, buton, yanıt başlığı) t.me
önizlemelerinden okunup sahte Telegram nesneleriyle yeniden kurulmuştur:

* ``amazonplatinum/8537``   – ürün adresi ``✅ Satın Al ✅`` butonunda,
* ``indirimfirsattr/295982`` – ilk satırın tamamı ürün adresine gizli hyperlink; ürün adı yok,
* ``amazonindirimtr/47090`` – bir fırsata yanıt olarak atılmış açıklamasız görsel,
* ``indirimfirsattr/295987`` – ürün adı gizli hyperlink; altında ``1000TL`` içeren kampanya satırı.

Önizlemenin göstermediği iki ayrıntı varsayımdır: ``amazonplatinum`` butonunun etiketi
bildirimdeki satırdan alınmıştır; ``indirimfirsattr`` (grup) butonu ise aynı adrese giden
bir buton olarak modellenmiştir. Butonsuz ve farklı adresli buton durumları da sınanır.
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bot  # noqa: E402
import composer  # noqa: E402
import botapi  # noqa: E402
import appstate  # noqa: E402
import botentities  # noqa: E402
import confutil  # noqa: E402
import dedupbadge  # noqa: E402
import producttitle  # noqa: E402
import sanitize  # noqa: E402
import searchbuttons  # noqa: E402
import smarttitle  # noqa: E402
import price_filter as pf  # noqa: E402
import test_cross_channel_dedup as cc  # noqa: E402
import test_integration as ti  # noqa: E402
from telethon.tl import types  # noqa: E402

LABEL = "👉 Ürün Linki 👈"
MESSAGE_LINK = "https://t.me/kaynak/1"

PLATINUM_URL = ("https://www.amazon.com.tr/dp/B0F8QG135W?tag=36a74e2d-52a7-4421-9e34-306431265d33-21"
                "&smid=A1UNQM1SR2CHM&th=1&psc=1")
PLATINUM_TITLE = "Ateşler Kuruyemiş Badem İçi Kavrulmuş 150 gr"
PLATINUM_GOOGLE = ("https://www.google.com/search?q=Ate%C5%9Fler+Kuruyemi%C5%9F+Badem+%C4%B0%C3%A7i"
                   "+Kavrulmu%C5%9F+150+gr")
PLATINUM_TEXT = (f"📦 {PLATINUM_TITLE}\n\n💰 106,26 TL\n\n🔻 %54.87\n\n🔎 Google\n\n"
                 "📢 @amazonplatinum #tanıtım\n\n"
                 "ℹ️ Amazon tarafından sağlandı içerik reklam / işbirliği içerebilir")

COUPON_URL = "https://www.amazon.com.tr/dp/B0DXKHM7M3?tag=yeninidirim-21&linkCode=ll1&ref_=as_li_ss_tl"
COUPON_LINE = ("⚡ Sepete 4 adet ekleyip, ödeme ekranında promosyonlar kısmından GUZELL200 kodunu girin. "
               "Kodla adeti 99 TL. Farklı varyantlar için de kodu kullanabilirsiniz.")
COUPON_TEXT = (f"{COUPON_LINE}\n\nLink\n\n💥 Bu fiyat uzun sürmez!\n\n#işbirliği #yeniindirim\n\n"
               "👇👇 Aşağıdaki butona tıkla, fırsatı kaçırma! 👇👇")

ONLY_URL = "https://www.amazon.com.tr/dp/B0CNH414W1?tag=yeninidirim-21&linkCode=ll1&ref_=as_li_ss_tl"
ONLY_TITLE = "ONLY Kadın Onlpoptrash-Sally Easy Mw Rib PNT Noos"
ONLY_TEXT = (f"⚡ {ONLY_TITLE}\n\n💰 Fiyat: 224,31 ₺\n\n🎯 Stokta var\n\n🏷️ Kampanyalar:\n\n"
             "🎉 Prime Üyelerine Özel: 1000TL seçili ürün satın aldığınızda %10 tasarruf edin\n\n"
             "⏰ Stoklar tükenmeden yakala!\n\n#işbirliği #yeniindirim\n\n"
             "👇👇 Aşağıdaki butona tıkla, fırsatı kaçırma! 👇👇")


def linked(text: str, label: str, url: str) -> types.MessageEntityTextUrl:
    """``label`` yazısının altına gizlenmiş bağlantı (konumlar UTF-16)."""
    return types.MessageEntityTextUrl(
        offset=sanitize.utf16_length(text[:text.index(label)]), length=sanitize.utf16_length(label), url=url)


def markup(*buttons: tuple[str, str]):
    return ti.FakeMarkup([ti.FakeRow([ti.FakeButton(text, url=url) for text, url in buttons])]) if buttons else None


def message(text: str, *, links=(), buttons=(), media=None) -> SimpleNamespace:
    return SimpleNamespace(id=1, message=text, raw_text=text, media=media, reply_markup=markup(*buttons),
                           entities=[linked(text, label, url) for label, url in links])


def platinum() -> SimpleNamespace:
    return message(PLATINUM_TEXT, links=[("Google", PLATINUM_GOOGLE)],
                   buttons=[("✅ Satın Al ✅", PLATINUM_URL)])


def coupon(url: str = COUPON_URL) -> SimpleNamespace:
    return message(COUPON_TEXT, links=[(COUPON_LINE, url)], buttons=[("🛒 Ürüne Git", url)])


def only() -> SimpleNamespace:
    return message(ONLY_TEXT, links=[(f"⚡ {ONLY_TITLE}", ONLY_URL)], buttons=[("🛒 Ürüne Git", ONLY_URL)])


def compose(obj, mode: str = "smart", *, as_bot: bool = True, **extra):
    """Üretimdeki iki çağrı: bot bildirimi (inline klavyeli) ve hesap kopyası (klavyesiz)."""
    extra.setdefault("has_keyboard", as_bot)
    with mock.patch.object(appstate, "SMART_HEADER", True):
        return composer.compose_message(obj, link_kinds=confutil.link_kinds_for(mode, bot=as_bot),
                                   message_link=MESSAGE_LINK, source_name="Kaynak", **extra)


def product_links(composed) -> list[tuple[str, str]]:
    """Bot API'ye giden ``(görünen yazı, gömülü adres)`` çiftleri; yalnız ürün satırları."""
    entities = botentities.bot_api_entities(composed["message"], composed["content"])
    found = [(sanitize.utf16_slice(composed["text"], item["offset"], item["length"]), item["url"])
             for item in entities if item["type"] == "text_link"]
    return [pair for pair in found if pair[0].startswith("👉 Ürün Linki")]


def keyboard_labels(obj, composed) -> list[str]:
    entities = botentities.bot_api_entities(composed["message"], composed["content"]) + botentities.source_name_entity(composed)
    keyboard = searchbuttons.build_inline_keyboard(obj, body_text=composed["text"], body_entities=entities)
    return [button["text"] for row in (keyboard or {}).get("inline_keyboard", []) for button in row]


SEARCH_ROW = ["Google Alışveriş", "Akakçe'de ara", "Cimri'de ara"]


class ProductLinkTest(unittest.TestCase):
    """Ürün adresi her kaynak düzeninde ``👉 Ürün Linki 👈`` yazısına gömülür."""

    def test_reported_button_link_becomes_hidden_product_link(self):
        obj = platinum()
        composed = compose(obj)
        # Sıra: fiyat-ad → ürün linki → alıntı → (mesaj adresini taşıyan) grup adı.
        self.assertEqual(composed["text"].split("\n\n")[1], LABEL)
        self.assertEqual(composed["text"].split("\n\n")[-1], "Kaynak")
        self.assertTrue(composed["text"].startswith(f"₺106 — {PLATINUM_TITLE}\n\n{LABEL}\n\n📦 {PLATINUM_TITLE}"))
        self.assertNotIn("Mesajı Gör", composed["text"], "adres grup adının içinde")
        self.assertEqual(composed["source_name_url"], MESSAGE_LINK)
        self.assertNotIn("amazon.com.tr", composed["text"], "ürün adresi metne yazılmaz")
        self.assertNotIn("Satın Al", composed["text"], "buton etiketi link satırı olmaz")
        self.assertEqual(product_links(composed), [(LABEL, PLATINUM_URL)])
        # Alıntı yedek olarak aynen durur; gövdedeki Google araması ayrı satır olmaz.
        self.assertEqual(composed["body"], sanitize.message_text(sanitize.sanitize_message(obj)))
        self.assertEqual(composed["text"].count("Google"), 1)
        # Adres metinde tıklanabilir kaldığı için kaynak butonu yinelenmez.
        self.assertEqual(keyboard_labels(obj, composed), SEARCH_ROW)
        # Hesap kopyası (bot gönderemezse) aynı düzeni ve aynı gömülü adresi taşır.
        account = compose(obj, as_bot=False)
        self.assertEqual(account["text"], composed["text"])
        self.assertEqual([entity.url for entity in account["entities"] if getattr(entity, "url", None)],
                         [PLATINUM_GOOGLE, PLATINUM_URL, MESSAGE_LINK],
                         "hesap kopyası da mesaj linkini grup adına gömer")

    def test_every_source_layout_gets_exactly_one_product_line(self):
        cta = "🔗 FIRSATA GİT!"
        layouts = {
            "buton (amazonindirimtr)": (
                message("📦 Blade Green Dream Deodorant 200 ML\n\n💰 70,81 ₺",
                        buttons=[("👉 👉 FIRSATA GİT 👈 👈", "https://fenom.io/amzn-mdz4p")]),
                "https://fenom.io/amzn-mdz4p"),
            "gizli CTA satırı": (
                message(f"Palmolive Naturals Duş Jeli 500 ml\n\n💰 79,00 TL\n\n{cta}",
                        links=[("FIRSATA GİT!", "https://www.amazon.com.tr/dp/B0DBVT6DJV?tag=firsatztg-21")]),
                "https://www.amazon.com.tr/dp/B0DBVT6DJV?tag=firsatztg-21"),
            "gizli başlık + aynı adresli buton": (only(), ONLY_URL),
            "görünür adres": (
                message("Xiaomi G27Qi Monitör\n\n💰 6.499 TL\n\nLink: https://ty.gl/dpvho5ux5sxuj"),
                "https://ty.gl/dpvho5ux5sxuj"),
        }
        for name, (obj, url) in layouts.items():
            with self.subTest(name):
                composed = compose(obj)
                self.assertEqual(product_links(composed), [(LABEL, url)])
                self.assertEqual(composed["text"].count(LABEL), 1)
                self.assertEqual(composed["body"], sanitize.message_text(sanitize.sanitize_message(obj)),
                                 "alıntı (yedek) değişmez")
                self.assertEqual(keyboard_labels(obj, composed), SEARCH_ROW)

    def test_reported_group_posts_get_one_line_whatever_the_unseen_button_is(self):
        """Grup butonu önizlemede görünmez: buton olsa da olmasa da tek ürün satırı çıkar."""
        short = "https://amzn.to/3xYzAbC"
        title_link, coupon_link = [(f"⚡ {ONLY_TITLE}", ONLY_URL)], [(COUPON_LINE, COUPON_URL)]
        cases = {
            "ürün adı gizli link, buton yok": (message(ONLY_TEXT, links=title_link), ONLY_URL, f"₺224 — {ONLY_TITLE}"),
            "aynı adresli buton": (only(), ONLY_URL, f"₺224 — {ONLY_TITLE}"),
            "farklı adresli buton": (
                message(ONLY_TEXT, links=title_link, buttons=[("🛒 Ürüne Git", short)]), short, f"₺224 — {ONLY_TITLE}"),
            "ürün olmayan buton": (
                message(ONLY_TEXT, links=title_link, buttons=[("Gruba katıl", "https://t.me/indirimfirsattr")]),
                ONLY_URL, f"₺224 — {ONLY_TITLE}"),
            "kupon cümlesi gizli link, buton yok": (message(COUPON_TEXT, links=coupon_link), COUPON_URL, "₺99"),
            "kupon, aynı adresli buton": (coupon(), COUPON_URL, "₺99"),
        }
        for name, (obj, url, header) in cases.items():
            with self.subTest(name):
                composed = compose(obj)
                self.assertEqual(composed["header"], header)
                self.assertEqual(product_links(composed), [(LABEL, url)])
                self.assertEqual(composed["text"].count(LABEL), 1)
                for raw in ("amazon.com.tr", "amzn.to"):
                    self.assertNotIn(raw, composed["text"], "ürün adresi metne açık yazılmaz")

    def test_search_chat_and_homepage_links_are_not_product_links(self):
        for url in (PLATINUM_GOOGLE, "https://www.google.com/search?udm=28&q=cay",
                    "https://www.akakce.com/arama/?q=cay", "https://www.cimri.com/arama?q=cay"):
            self.assertTrue(sanitize.is_search_link(url), url)
            self.assertFalse(producttitle.is_product_link(url), url)
        for url in ("https://t.me/kanal", "https://t.me/kanal/42", "https://wa.me/905550000000",
                    "https://instagram.com/kanal", "https://youtu.be/xyz", "https://www.amazon.com.tr",
                    "https://www.trendyol.com/", "tg://resolve?domain=kanal", ""):
            self.assertFalse(producttitle.is_product_link(url), url)
        for url in (PLATINUM_URL, "https://fenom.io/amzn-mdz4p", "https://app.hb.biz/m6WukNoP939I",
                    "https://ty.gl/qm2qlv0davzbx", "https://link.amazon/B01xNall7", "https://amzn.to/4gOLDeN",
                    "https://play.google.com/store/apps/details?id=a.b"):
            self.assertTrue(producttitle.is_product_link(url), url)
            self.assertFalse(sanitize.is_search_link(url), url)

    def test_search_buttons_stay_on_the_bot_keyboard_and_channel_button_keeps_old_line(self):
        akakce, google = "https://www.akakce.com/arama/?q=nivea", "https://www.google.com/search?q=nivea"
        obj = message("NIVEA Kadın Roll On 50ml\n268 TL", buttons=[
            ("Akakçe'de ara", akakce), ("Google", google), ("Kanalımız", "https://t.me/kanal"),
        ])
        for mode in ("smart", "all"):
            composed = compose(obj, mode)
            self.assertEqual(composed["appendix"], "🔗 Kanalımız: https://t.me/kanal", mode)
            self.assertEqual(product_links(composed), [])
            # Arama butonları kaybolmaz: bot bildiriminde düğme olarak kalır.
            self.assertEqual(keyboard_labels(obj, composed),
                             ["Google", "Google Alışveriş", "Akakçe'de ara", "Cimri'de ara"], mode)
        # Hesap kopyasında klavye yoktur; arama butonunun tek taşıyıcısı bu satırdır.
        for mode in ("smart", "all"):
            account = compose(obj, mode, as_bot=False)
            self.assertEqual(account["appendix"], (f"🔗 Akakçe'de ara: {akakce}\n🔗 {google}\n"
                                                   "🔗 Kanalımız: https://t.me/kanal"), mode)
            self.assertEqual(product_links(account), [])

    def test_several_product_buttons_are_numbered_in_source_order(self):
        first, second = "https://www.amazon.com.tr/dp/B0AAAAAAA1", "https://ty.gl/abc123"
        obj = message("NIVEA Kadın Roll On 50ml\n268 TL",
                      buttons=[("Amazon'da Gör", first), ("Trendyol'da Gör", second)])
        composed = compose(obj)
        self.assertEqual(composed["appendix"], "👉 Ürün Linki 1 👈\n👉 Ürün Linki 2 👈")
        self.assertEqual(product_links(composed), [("👉 Ürün Linki 1 👈", first), ("👉 Ürün Linki 2 👈", second)])
        self.assertEqual(keyboard_labels(obj, composed), SEARCH_ROW)

    def test_button_is_preferred_over_earlier_hidden_links(self):
        store, product = "https://www.amazon.com.tr/stores/page/ABC", "https://www.amazon.com.tr/dp/B0AAAAAAA1"
        text = "🛍 Amazon\nClear Şampuan 350 ml\n🏷 224 TL\n🔗 FIRSATA GİT"
        both = message(text, links=[("Amazon", store), ("FIRSATA GİT", product)], buttons=[("Satın Al", product)])
        self.assertEqual(product_links(compose(both)), [(LABEL, product)],
                         "buton adresi gövdede gizli link olsa da ürün linki odur")
        homepage = message(text, links=[("Amazon", "https://www.amazon.com.tr"), ("FIRSATA GİT", product)])
        self.assertEqual(product_links(compose(homepage)), [(LABEL, product)], "mağaza ana sayfası ürün linki olmaz")

    def test_off_and_all_modes_keep_their_layouts(self):
        obj = platinum()
        off = compose(obj, "off")
        self.assertEqual(off["appendix"], "")
        self.assertEqual(keyboard_labels(obj, off), ["✅ Satın Al ✅", *SEARCH_ROW], "off: buton yedekte kalır")
        everything = compose(obj, "all")
        self.assertEqual(everything["appendix"],
                         f"🔗 {PLATINUM_GOOGLE}\n🔗 ✅ Satın Al ✅: {PLATINUM_URL}",
                         "all: ham URL dökümü değişmez; gövdedeki gizli arama linki de listelenir")
        self.assertEqual(product_links(everything), [])

    def test_dropped_appendix_keeps_the_source_button(self):
        # ``Mesajı Gör`` satırı kalktığı için düzen bir satır kısaldı: ürün
        # satırının sığmadığı sınır artık 80 değil 70 birim.
        obj = platinum()
        composed = compose(obj, limit=70)
        self.assertEqual(composed["appendix"], "")
        self.assertEqual(product_links(composed), [])
        self.assertIn("✅ Satın Al ✅", keyboard_labels(obj, composed))

    def test_unformatted_fallback_writes_the_address_visibly(self):
        composed = compose(platinum(), as_bot=False, hidden_links=False)
        self.assertIn(f"\n\n{LABEL}: {PLATINUM_URL}\n\n", composed["text"])
        self.assertEqual(product_links(composed), [])

    def test_link_under_removed_disclosure_tag_is_carried_as_product_link(self):
        text = "Clear Şampuan 350 ml 224 TL #reklam"
        obj = message(text, links=[("#reklam", ONLY_URL)])
        self.assertEqual(product_links(compose(obj)), [(LABEL, ONLY_URL)])
        self.assertEqual(compose(obj, "off")["appendix"], f"🔗 {ONLY_URL}", "off iken de kaybolmaz")


class ReportedTitleAndPriceTest(unittest.TestCase):
    """Başlık/fiyat: gizli hyperlink'li ilk satır, kupon cümlesi ve kampanya satırı."""

    def test_coupon_sentence_price_and_no_slogan_title(self):
        obj = coupon()
        body = sanitize.message_text(sanitize.sanitize_message(obj))
        info = pf.extract_price(body)
        self.assertEqual((info.value, info.confidence, info.reason), (99.0, "high", "discounted"))
        composed = compose(obj)
        # Mesajda ürün adı yok: slogan başlık yapılmaz, üst satır yalnız fiyattır.
        self.assertEqual(composed["header"], "₺99")
        self.assertEqual(composed["text"].split("\n\n")[:3],
                         ["₺99", LABEL, COUPON_LINE])
        self.assertEqual(smarttitle.smart_dedup_title(obj), "", "slogan ürün kimliği olamaz")
        self.assertEqual(producttitle._search_query(sanitize.sanitize_message(obj)), "")
        self.assertEqual(product_links(composed), [(LABEL, COUPON_URL)])
        self.assertEqual(keyboard_labels(obj, composed), [], "aranacak ad yokken arama düğmesi üretilmez")

    def test_template_slogans_are_generic_but_do_not_hide_real_titles(self):
        for line in ("💥 Bu fiyat uzun sürmez!", "Bu uzun sürmez", "⏰ Stoklar tükenmeden yakala!",
                     "👇👇 Aşağıdaki butona tıkla, fırsatı kaçırma! 👇👇", "Aşağıdaki butona"):
            self.assertTrue(pf.is_generic_title(line), line)
        for line in ("Uzun Kollu Erkek Gömlek", "Stoklar için Raf Sistemi 5 Katlı", "Buton Pil CR2032 5'li"):
            self.assertFalse(pf.is_generic_title(line), line)
        title, info = pf.analyze("GIGABYTE G5 Oyun Laptopu\n\n📌 Satıcı Amazon.\n\n💥 Bu fiyat uzun sürmez!\n87.500 TL")
        self.assertEqual((title, info.value), ("GIGABYTE G5 Oyun Laptopu", 87500.0))

    def test_sentence_end_separates_amount_from_following_words(self):
        price = lambda text: pf.extract_price(text).value  # noqa: E731
        self.assertEqual(price("Ürün\n99 TL. Farklı varyantlar için de geçerli."), 99.0)
        self.assertEqual(price("Ürün\n🏷 229 TL. Kupon kodu: ABC"), 229.0)
        self.assertEqual(price("Ürün\n💰 1.299 TL! Taksit imkanı var"), 1299.0)
        self.assertEqual(price("Ürün\n💰 349 TL. Kargo ücretsiz!"), 349.0)
        self.assertEqual(price("Ürün\n1.499 TL… Liste fiyatı 1.999 TL"), 1499.0)
        # Aynı cümledeki niteleyici ve küçük harfle süren özensiz yazım eskisi gibi elenir;
        # tek başına cümle açamayan edatlar (üzeri/üstü/değerinde) noktadan sonra da eşiktir.
        for text in ("Ürün\n100 TL fark var", "Ürün\n99 TL Farklı satıcıda", "Ürün\n250 TL indirim",
                     "Ürün\n250 TL İndirim!", "Ürün\n500 TL ve üzeri alışverişte", "Ürün\n49 TL kargo",
                     "Ürün\n100 TL kupon", "Ürün\n500 TL. üzeri alışverişte", "Ürün\n99 TL.Farklı varyant",
                     "Ürün\n100 TL. Üzeri alışverişlerde 20 TL indirim", "Ürün\n500 TL. Değerinde hediye çeki",
                     "Ürün\n1.000 TL. Üstü siparişlerde kargo bedava"):
            self.assertIsNone(price(text), text)

    def test_linked_product_name_beats_campaign_line_with_glued_amount(self):
        obj = only()
        body = sanitize.message_text(sanitize.sanitize_message(obj))
        title, info = pf.analyze(body)
        self.assertEqual((title, info.value, info.confidence), (ONLY_TITLE, 224.31, "high"))
        composed = compose(obj)
        self.assertEqual(composed["header"], f"₺224 — {ONLY_TITLE}")
        self.assertEqual(smarttitle.smart_dedup_title(obj), ONLY_TITLE)
        self.assertEqual(producttitle._search_query(sanitize.sanitize_message(obj)), ONLY_TITLE)
        # Bağlantı olmasa da sonuç aynıdır: neden gömülü link değil, puanlamadır.
        self.assertEqual(pf.extract_title(ONLY_TEXT), ONLY_TITLE)

    def test_glued_amount_is_not_a_model_code_but_model_codes_still_count(self):
        score = lambda line: pf._score_line(line, pf._trim_edges(pf._remove_noise(line)), 3, set())  # noqa: E731
        self.assertEqual(score("Prime Üyelerine Özel: 1000TL seçili ürün satın aldığınızda tasarruf edin"),
                         score("Prime Üyelerine Özel: bin lira seçili ürün satın aldığınızda tasarruf edin"))
        self.assertEqual(score("Asus Vivobook X1504VA Laptop") - score("Asus Vivobook Laptop Bilgisayar"), 3)
        self.assertEqual(pf.extract_title("Steam 100TL Cüzdan Kartı\n💰 95 TL"), "Steam 100TL Cüzdan Kartı")


class BlankReplyUnitTest(unittest.TestCase):
    """Yalnız YANIT olan ve hiçbir içerik taşımayan ileti içeriksiz sayılır."""

    @staticmethod
    def reply(text="", **kwargs):
        obj = message(text, **kwargs)
        obj.reply_to = types.MessageReplyHeader(reply_to_msg_id=47088)
        return obj

    def test_only_content_free_replies_are_blank(self):
        photo = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=1))
        for name, obj in {
            "açıklamasız görsel": self.reply(media=photo),
            "salt emoji": self.reply("🔥🔥"),
            "yalnız temizlenen etiket": self.reply("#reklam", media=photo),
            "yalnız WhatsApp tanıtım linki": self.reply("https://wa.me/905550000000"),
        }.items():
            self.assertTrue(sanitize.is_blank_reply(obj), name)
        topic_root = message("", media=photo)
        topic_root.reply_to = types.MessageReplyHeader(reply_to_msg_id=5, forum_topic=True)
        for name, obj in {
            "yanıt değil: açıklamasız görsel": message("", media=photo),
            "yanıt değil: salt emoji": message("🔥"),
            "forum konusunun kökü": topic_root,
            "tek kelime": self.reply("Bitti", media=photo),
            "yalnız fiyat": self.reply("79 TL"),
            "görünür adres": self.reply("https://ty.gl/abc123"),
            "gizli hyperlink": self.reply("👉", links=[("👉", ONLY_URL)]),
            "buton": self.reply(buttons=[("Fırsata Git", ONLY_URL)]),
        }.items():
            self.assertFalse(sanitize.is_blank_reply(obj), name)
        in_topic = message("", media=photo)
        in_topic.reply_to = types.MessageReplyHeader(reply_to_msg_id=9, reply_to_top_id=5, forum_topic=True)
        self.assertTrue(sanitize.is_blank_reply(in_topic), "forum konusu içindeki gerçek yanıt")


class DeliveryTest(cc.Harness, unittest.TestCase):
    """Uçtan uca: gerçek ``on_new_message`` yolu, sahte Telegram ve Bot API ile."""

    def send(self, client, chat_id, message_id, text, *, reply_to=None, media=False, links=(), buttons=()):
        event = ti.FakeEvent(chat_id, 7, text, message_id=message_id, media=media,
                             entities=[linked(text, label, url) for label, url in links],
                             reply_markup=markup(*buttons))
        if reply_to is not None:
            event.message.reply_to = types.MessageReplyHeader(reply_to_msg_id=reply_to)

        async def run():
            with mock.patch.object(appstate, "DEDUP_EDIT_MIN_INTERVAL", 0):
                await client.handlers[1][1](event)
                await asyncio.sleep(0.02)
        asyncio.run(run())

    @property
    def notifications(self) -> int:
        return len(self.ping_calls) + len(self.media_calls)

    def test_blank_reply_is_dropped_and_real_offers_still_flow(self):
        client = self.start()
        deal = "📦 Apple iPhone Air 1 TB - Uçuk Altın Rengi\n\n💰 89.274,42 ₺"
        self.send(client, self.first_id, 47088, deal, buttons=[("FIRSATA GİT", "https://fenom.io/amzn-nwei1")])
        self.assertEqual(self.notifications, 1)
        # Bildirilen örnek: aynı fırsata açıklamasız görselle yanıt.
        self.send(client, self.first_id, 47090, "", reply_to=47088, media=True)
        self.assertEqual(self.notifications, 1, "boş bildirim üretilmemeli")
        self.assertEqual(appstate.STATS["matched"], 1)
        self.assertIn(47090, appstate.CURSOR_RAM[self.first_id]["completed"] | {appstate.CURSOR_RAM[self.first_id]["cursor"]},
                      "elenen ileti işlenmiş sayılır; yeniden denenmez")
        # Kaynağın gerçek yazımı (emoji ile) ve salt emoji yanıtı da bildirim üretmez.
        self.send(client, self.first_id, 47061, "Yoğun ilgi görüyor 📣", reply_to=47088)
        self.send(client, self.first_id, 47062, "👆👆", reply_to=47088)
        self.assertEqual(self.notifications, 1)
        # Biçim entity'si (kalın yazı, özel emoji) içerik eklemez; duyuru yine elenir.
        styled = ti.FakeEvent(self.first_id, 7, "Yoğun ilgi görüyor 📣", message_id=47063, media=False, entities=[
            types.MessageEntityBold(offset=0, length=18),
            types.MessageEntityCustomEmoji(offset=19, length=2, document_id=5),
        ])
        asyncio.run(client.handlers[1][1](styled))
        self.assertEqual(self.notifications, 1)
        # Aynı söz bir bağlantı taşıyorsa (gizli hyperlink) fırsat olabilir: iletilir.
        self.send(client, self.first_id, 47064, "Yoğun ilgi görüyor 📣",
                  links=[("Yoğun ilgi görüyor", "https://fenom.io/amzn-abc")])
        self.assertEqual(self.notifications, 2)
        # Fail-open: metin, bağlantı ya da buton taşıyan yanıt ve yanıt olmayan görsel iletilir.
        self.send(client, self.first_id, 47091, "GUZELL200 kodu tükendi.", reply_to=47088)
        self.send(client, self.first_id, 47092, "", reply_to=47088, media=True,
                  buttons=[("FIRSATA GİT", "https://fenom.io/amzn-zzz")])
        self.send(client, self.first_id, 47093, "", media=True)
        self.assertEqual(self.notifications, 5)

    def test_reported_button_offer_end_to_end_and_badge_keeps_hidden_link(self):
        client = self.start()
        self.send(client, self.first_id, 8537, PLATINUM_TEXT, links=[("Google", PLATINUM_GOOGLE)],
                  buttons=[("✅ Satın Al ✅", PLATINUM_URL)])
        call = self.ping_calls[0]
        blocks = call["text"].split("\n\n")
        self.assertEqual((blocks[0], blocks[1], blocks[-1]),
                         (f"₺106 — {PLATINUM_TITLE}", LABEL, cc.FIRST_CHANNEL))
        self.assertNotIn("Mesajı Gör", call["text"], "adres grup adına gömülü")
        self.assertNotIn("amazon.com.tr", call["text"])
        hidden = [(sanitize.utf16_slice(call["text"], item["offset"], item["length"]), item["url"])
                  for item in call["entities"] if item["type"] == "text_link"]
        self.assertEqual(hidden[:2], [("Google", PLATINUM_GOOGLE), (LABEL, PLATINUM_URL)])
        # Grup adı orijinal mesaja bağlanır (kimlik çözülmüş sohbetten kurulur).
        self.assertEqual(hidden[2][0], cc.FIRST_CHANNEL)
        self.assertTrue(hidden[2][1].startswith("https://t.me/"), hidden[2][1])
        self.assertTrue(hidden[2][1].endswith("/8537"), hidden[2][1])
        self.assertEqual([button["text"] for row in call["keyboard"]["inline_keyboard"] for button in row],
                         SEARCH_ROW)
        # Aynı fırsat ikinci kanaldan gelir: not işlenirken gömülü adres yerinde kalmalı.
        self.send(client, cc.SECOND_CHANNEL_ID, 99, PLATINUM_TEXT, buttons=[("Satın Al", PLATINUM_URL)])
        self.assertEqual(self.notifications, 1)
        edit = self.edit_text_calls[-1]
        self.assertIn("2 kere paylaşıldı", edit["text"])
        self.assertIn((LABEL, PLATINUM_URL), [
            (sanitize.utf16_slice(edit["text"], item["offset"], item["length"]), item.get("url"))
            for item in edit["entities"]])

    def test_badge_edit_keeps_link_on_its_label_in_emoji_heavy_messages(self):
        """Entity konumları UTF-16 birimidir: emojili mesajda 📌 notu bağlantıyı etiketten koparmamalı."""
        client = self.start()
        text = "Deneme Havlu Seti 6 Parça\n💰 200 TL\n" + "\n".join(["🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥"] * 8)
        url = "https://www.amazon.com.tr/dp/B0EMOJI0001"
        self.send(client, self.first_id, 500, text, buttons=[("Satın Al", url)])
        self.send(client, cc.SECOND_CHANNEL_ID, 501, text, buttons=[("Satın Al", url)])
        edit = self.edit_text_calls[-1]
        self.assertIn("2 kere paylaşıldı", edit["text"])
        spans = [(item["type"], sanitize.utf16_slice(edit["text"], item["offset"], item["length"]), item.get("url"))
                 for item in edit["entities"]]
        self.assertIn(("text_link", LABEL, url), spans)
        quote = next(piece for kind, piece, _ in spans if kind == "blockquote")
        self.assertTrue(quote.endswith("🔥"), "alıntı gövdede biter; notu yutmaz")
        self.assertEqual([piece for kind, piece, _ in spans if kind == "bold"][-1],
                         edit["text"].split("\n\n")[-1])

    def test_restart_scan_reads_the_new_layout_and_ignores_the_link_label(self):
        """Açılış taraması: ``👉 Ürün Linki 👈`` satırı ürün adı sayılmaz; gerçek başlık birleşir."""
        self.assertTrue(pf.is_generic_title(LABEL))
        self.assertTrue(pf.is_generic_title(LABEL.replace("Linki", "Linki 2")))
        self.assertIsNone(dedupbadge.dedup_key(LABEL))
        self.assertFalse(pf.is_generic_title("Ürün Linkin Park Tişört"), "ürün adı elenmez")
        destination = -1001234567890   # süper grup: eski bot bildirimi düzenlenebilir
        full = (f"₺106 — {PLATINUM_TITLE}\n\n📦 {PLATINUM_TITLE}\n\n💰 106,26 TL\n\n{LABEL}\n\n"
                f"🔗 Mesajı Gör: https://t.me/amazonplatinum/8537\n\n{cc.FIRST_CHANNEL}")
        bare = f"{LABEL}\n\n🔗 Mesajı Gör: https://t.me/amazonplatinum/8540\n\n{cc.FIRST_CHANNEL}"
        ti.FakeClient.default_history = [
            (destination, bare, 123, None, True, [linked(bare, LABEL, "https://amzn.to/afis")], 901),
            (destination, full, 123, None, None, [linked(full, LABEL, PLATINUM_URL)], 900),
        ]
        client = self.start(destination=destination)
        self.assertEqual([entry["title"] for entry in appstate.DEDUP_CACHE.values()], [PLATINUM_TITLE],
                         "başlıksız (yalnız link satırı) bildirim tekrar kaydı açmaz")
        self.send(client, cc.SECOND_CHANNEL_ID, 201, PLATINUM_TEXT, links=[("Google", PLATINUM_GOOGLE)],
                  buttons=[("✅ Satın Al ✅", PLATINUM_URL)])
        self.assertEqual(self.notifications, 0, "geçmişteki bildirim aynı fırsatı yutar")
        edit = self.edit_text_calls[-1]
        self.assertEqual(edit["message_id"], 900)
        self.assertIn("2 kere paylaşıldı", edit["text"])
        self.assertIn(("text_link", LABEL, PLATINUM_URL), [
            (item["type"], sanitize.utf16_slice(edit["text"], item["offset"], item["length"]), item.get("url"))
            for item in edit["entities"]
        ], "yeniden başlatmadan sonra da bağlantı etiketinde kalır")

    def test_titleless_coupon_posts_are_not_merged_by_their_slogan(self):
        client = self.start()
        other = "https://www.amazon.com.tr/dp/B0OTHER0001?tag=yeninidirim-21"
        self.send(client, self.first_id, 295982, COUPON_TEXT, links=[(COUPON_LINE, COUPON_URL)],
                  buttons=[("🛒 Ürüne Git", COUPON_URL)])
        self.send(client, self.first_id, 295990, COUPON_TEXT, links=[(COUPON_LINE, other)],
                  buttons=[("🛒 Ürüne Git", other)])
        self.assertEqual(len(self.ping_calls), 2, "ürün adı yokken slogan yüzünden birleştirilmemeli")
        self.assertEqual(appstate.STATS["deduped"], 0)
        self.assertTrue(all(call["text"].startswith("₺99\n\n") for call in self.ping_calls))

    def test_last_resort_link_card_shows_the_product_address(self):
        """Bot ve biçimli yollar kapalıyken giden düz metin iletide adres görünür kalır."""
        client = self.start()

        async def refuse(*args, **kwargs):
            return botapi.BotSendResult(False, "Forbidden")

        client.fail_modes.update({"forward", "copy", "media", "download"})
        with mock.patch.object(botapi, "send_bot_ping", refuse), \
                mock.patch.object(appstate, "DELIVERY_CHAIN", ["link"]):
            self.send(client, self.first_id, 8537, PLATINUM_TEXT, buttons=[("✅ Satın Al ✅", PLATINUM_URL)])
        delivered = str(client.sent[-1][1])
        self.assertIn(f"{LABEL}: {PLATINUM_URL}", delivered)
        self.assertIsNone(client.sent_kwargs[-1].get("formatting_entities"))


if __name__ == "__main__":
    unittest.main()
