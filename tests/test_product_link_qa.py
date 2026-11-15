"""Ürün linki QA'sı: alan adı kuralı, çok linkli mesajlar ve Bot API gövdesi; tümü çevrimdışıdır.

1. ``is_product_link`` hangi adresi ürün sayar? Sosyal ağ/topluluk adresleri, alan adının
   yazım varyantları, benzer (lookalike) alan adları, yalnız izleme etiketi taşıyan mağaza
   ana sayfası, görsel dosyası.
2. Birden çok link taşıyan mesajda kaynak linklerinin hepsi bildirimden açılabiliyor mu?
   Bot bildirimi metin + gizli hyperlink + inline klavyeyle, hesap kopyası (klavyesiz)
   metin + gizli hyperlink'le taşır. ``smart`` / ``all`` / ``off``; satır ve karakter sınırı.
3. ``👉 Ürün Linki 👈`` Bot API'ye tıklanabilir ``text_link`` olarak mı gidiyor? İstek ağ
   sınırında (``urllib.request.urlopen``) yakalanır: alan adları, UTF-16 konumları, klavye,
   önizleme ayarı ve 📌 düzenlemesinden sonraki durum.

Canlı Telegram çağrısı yoktur; 3. bölüm üretimin kurduğu istek gövdesini doğrular,
Telegram'ın onu nasıl çizdiğini değil.
"""
from __future__ import annotations

import asyncio
import random
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bot  # noqa: E402
import test_golden_reference as golden  # noqa: E402
import test_integration as ti  # noqa: E402
import test_reported_link_and_blank_cases as rc  # noqa: E402
from telethon.tl import types  # noqa: E402

LABEL = rc.LABEL
BODY = "Ateşler Kuruyemiş Badem İçi Kavrulmuş 150 gr\n💰 106,26 TL"
PRODUCTS = [f"https://www.amazon.com.tr/dp/B0PRODUCT{number}?tag=kanal-21" for number in range(1, 10)]
STORES = [(f"Mağaza {number}", url) for number, url in enumerate(PRODUCTS, 1)]
BUY = ("✅ Satın Al ✅", PRODUCTS[0])
COUPON = ("🎟 Kupon", "https://t.me/kanal_kupon/55")
REVIEW = ("İnceleme", "https://www.youtube.com/watch?v=abc")
GOOGLE_SHOPPING = ("Google", "https://www.google.com/search?udm=28&q=badem")
GOOGLE_WEB = ("Google web", "https://www.google.com/search?q=badem")
AKAKCE = ("Akakçe", "https://www.akakce.com/arama/?q=badem")
CIMRI = ("Cimri", "https://www.cimri.com/arama?q=badem")
SEARCHES = [GOOGLE_SHOPPING, AKAKCE, CIMRI]
PREVIEW = "https://www.hepsiburada.com/urun-p-HBV000PREVIEW"
CAPTION, TEXT, ACCOUNT_TEXT = bot.CAPTION_LIMIT - 24, bot.MESSAGE_LIMIT - 200, bot.MESSAGE_LIMIT - 100


def key(url: str) -> str:
    return bot.clean_url(url).rstrip("/").lower()


def preview(url: str) -> SimpleNamespace:
    return SimpleNamespace(webpage=SimpleNamespace(url=url, title="Önizleme"))


def source_links(obj, *kinds: str) -> set[str]:
    """Temizlenmiş kaynak mesajın taşıdığı adresler (bildirimin taşıması gerekenler)."""
    return {key(item["url"]) for item in bot.extract_links(bot.sanitize_message(obj))
            if not kinds or item["kind"] in kinds}


def view(obj, mode: str = "smart", *, as_bot: bool = True, limit: int | None = None) -> dict:
    """Teslim edilen ileti ve ondan açılabilen adresler.

    Bot bildirimi: görünür URL + ``text_link`` + inline klavye düğmesi. Hesap kopyası:
    görünür URL + gizli hyperlink (kullanıcı hesabı klavye gönderemez).
    """
    composed = rc.compose(obj, mode, as_bot=as_bot, **({} if limit is None else {"limit": limit}))
    reach = set(bot.visible_urls(composed["text"]))
    labels: list[str] = []
    if as_bot:
        entities = bot.bot_api_entities(composed["message"], composed["content"]) + bot.source_name_entity(composed)
        keyboard = bot.build_inline_keyboard(obj, body_text=composed["text"], body_entities=entities)
        buttons = [button for row in (keyboard or {}).get("inline_keyboard", []) for button in row]
        labels = [button["text"] for button in buttons]
        reach |= {key(item["url"]) for item in entities if item.get("type") == "text_link"}
        reach |= {key(button["url"]) for button in buttons}
    else:
        reach |= {key(entity.url) for entity in composed["entities"] if getattr(entity, "url", None)}
    return {"composed": composed, "appendix": composed["appendix"], "reach": reach, "keyboard": labels,
            "length": bot.utf16_length(composed["text"]),
            "body_cut": composed["body"] != bot.message_text(bot.sanitize_message(obj))}


def line(label: str, url: str) -> str:
    return f"🔗 {label}: {url}"


def numbered(count: int) -> str:
    return "\n".join(f"👉 Ürün Linki {number} 👈" for number in range(1, count + 1))


class ProductLinkRuleTest(unittest.TestCase):
    """``is_product_link``: neyin ``👉 Ürün Linki 👈`` olabileceği."""

    def assertProduct(self, expected: bool, *urls: str) -> None:
        for url in urls:
            with self.subTest(url=url):
                self.assertIs(bot.is_product_link(url), expected)

    def test_social_and_community_addresses_are_not_product_links(self):
        self.assertProduct(
            False,
            "https://discord.gg/AbCdEf", "https://discord.com/invite/AbCdEf", "https://discordapp.com/invite/AbCdEf",
            "https://www.linkedin.com/company/kanal", "https://lnkd.in/abc", "https://tr.pinterest.com/pin/1",
            "https://pin.it/abc", "https://www.reddit.com/r/indirim/comments/1", "https://redd.it/abc",
            "https://www.threads.net/@kanal", "https://www.threads.com/@kanal", "https://fb.com/kanal",
            "https://fb.me/kanal", "https://m.me/kanal", "https://www.twitch.tv/kanal",
            "https://www.snapchat.com/add/kanal", "https://kanal.tumblr.com/post/1", "https://vk.com/kanal",
            "https://bsky.app/profile/kanal",
            "https://channels.bip.ai/join/kanal", "https://linktr.ee/kanal", "https://forms.gle/AbCdEf",
            "https://docs.google.com/forms/d/e/1FAIp/viewform",
            # PR'ın ilk hâlinde de elenenler.
            "https://t.me/kanal/42", "https://telegram.me/kanal", "https://www.youtube.com/watch?v=abc",
            "https://youtu.be/abc", "https://x.com/kanal/status/1", "https://twitter.com/kanal",
            "https://www.facebook.com/kanal", "https://www.tiktok.com/@kanal", "https://wa.me/905550000000",
            "https://www.instagram.com/kanal/",
        )

    def test_host_is_matched_whatever_its_spelling(self):
        self.assertProduct(
            False,
            "https://DISCORD.GG/AbCdEf", "https://Www.YouTube.Com/watch?v=abc", "https://x.com./kanal",
            "https://discord.gg:443/AbCdEf", "https://kullanici:sifre@x.com/kanal", "https://m.facebook.com/kanal",
            "https://vm.tiktok.com/ZS1/", "https://old.reddit.com/r/indirim", "http://t.me/kanal",
            "https://music.youtube.com/watch?v=abc", "https://mobile.twitter.com/kanal",
            # Pinterest ülke alan adları.
            "https://pinterest.co.uk/pin/1", "https://www.pinterest.de/pin/1", "https://pinterest.com.tr/pin/1",
            "https://PINTEREST.FR/pin/1",
        )

    def test_lookalike_hosts_stay_product_links(self):
        self.assertProduct(
            True,
            "https://www.netflix.com/tr/title/1", "https://notx.com/urun/1", "https://box.com/urun/1",
            "https://mytiktok.com/urun/1", "https://t.me.magaza.example/urun/1", "https://x.com.magaza.example/urun/1",
            "https://x.com@magaza.example/urun/1", "https://discord.gg@magaza.example/urun/1",
            "https://discordapp.shop/urun/1", "https://mypinterest.de/urun/1", "https://pinterest.shop/urun/1",
            "https://pinterest.de.magaza.example/urun/1", "https://bip.ai.magaza.example/urun/1",
            "https://myforms.gle/urun/1", "https://play.google.com/store/apps/details?id=a.b",
            "https://store.google.com/product/pixel",
        )

    def test_store_homepage_with_only_tracking_parameters_is_not_a_product_link(self):
        self.assertProduct(
            False,
            "https://www.amazon.com.tr", "https://www.amazon.com.tr/", "https://www.amazon.com.tr/?tag=kanal-21",
            "https://www.amazon.com.tr?tag=kanal-21&linkCode=ll2&ref_=as_li_ss_tl",
            "https://www.trendyol.com/?utm_source=telegram&utm_medium=kanal&utm_campaign=x",
            "https://www.hepsiburada.com/?wt_af=kanal", "https://www.n11.com/?gclid=XyZ&fbclid=AbC",
            "https://www.trendyol.com/?adjust_t=abc&adjust_campaign=kanal", "https://www.amazon.com.tr/?TAG=kanal-21",
            "https://www.amazon.com.tr/?", "https://www.amazon.com.tr/#nav",
        )
        # Fail-open: tanınmayan tek bir anahtar ya da hedef adres taşıyan parametre ürüne gidebilir.
        self.assertProduct(
            True,
            "https://shop.example/?p=1234", "https://shop.example/?product_id=77&utm_source=telegram",
            "https://shop.example/?q=airfryer", "https://go.example/?url=https%3A%2F%2Fwww.amazon.com.tr%2Fdp%2FB0X",
            "https://www.trendyol.com/?adjust_deeplink=ty%3A%2F%2F%3FPage%3DProductDetail%26ContentId%3D1",
            "https://www.amazon.com.tr/dp/B0X?tag=kanal-21", "https://www.amazon.com.tr/gp/goldbox",
        )

    def test_media_files_are_not_product_links(self):
        self.assertProduct(
            False,
            "https://m.media-amazon.com/images/I/71abcDEF._AC_SL1500_.jpg",
            "https://cdn.dsmcdn.com/ty1/product/media/images/x/1_org_zoom.JPG",
            "https://productimages.hepsiburada.net/s/777/375-375/110000860880938.jpg/format:webp",
            "https://i.ibb.co/abc/urun.png", "https://telegra.ph/file/abc123.webp", "https://shop.example/video.mp4",
        )
        self.assertProduct(True, "https://shop.example/urun.html", "https://shop.example/jpg-cerceve-p-1",
                           "https://shop.example/urun.jpg.html", "https://shop.example/png")

    def test_shorteners_and_affiliate_redirects_stay_product_links(self):
        self.assertProduct(
            True,
            "https://amzn.to/4gOLDeN", "https://amzn.eu/d/abc", "https://a.co/d/abc", "https://link.amazon/B01xNall7",
            "https://ty.gl/qm2qlv0davzbx", "https://tyml.gl/abc", "https://fenom.io/amzn-mdz4p",
            "https://app.hb.biz/m6WukNoP939I", "https://sl.n11.com/n/vAk2s0n", "https://onu.al/feMF",
            "https://publicis.link/pazarama-37360", "https://go.gelirortaklari.com/click?aid=11",
            "https://paylaskazan.hepsiburada.com/abc", "https://t.co/AbCdEf", "https://bit.ly/3abc",
        )

    def test_malformed_addresses_are_never_product_links(self):
        self.assertProduct(False, "", None, "ftp://shop.example/p/1", "https://localhost/p/1", "https://[::1/p",
                           "javascript:alert(1)", "tg://resolve?domain=kanal", "shop.example/p/1")

    def test_community_button_is_carried_but_never_labelled_as_product(self):
        discord = ("Topluluk", "https://discord.gg/AbCdEf")
        obj = rc.message(BODY, buttons=[discord, BUY])
        for as_bot in (True, False):
            result = view(obj, as_bot=as_bot)
            self.assertEqual(result["appendix"], f"{LABEL}\n{line(*discord)}", as_bot)
            self.assertEqual(rc.product_links(result["composed"]), [(LABEL, BUY[1])], as_bot)
            self.assertEqual(source_links(obj) - result["reach"], set())
        alone = rc.message(BODY, buttons=[discord])
        self.assertEqual(view(alone)["appendix"], line(*discord), "tek buton topluluk daveti: ürün satırı olmaz")

    def test_tagged_homepage_button_does_not_take_the_product_line(self):
        home = ("🛍 Amazon", "https://www.amazon.com.tr/?tag=kanal-21")
        text = BODY + "\n\n🔗 FIRSATA GİT"
        obj = rc.message(text, links=[("FIRSATA GİT", PRODUCTS[0])], buttons=[home])
        result = view(obj)
        self.assertEqual(rc.product_links(result["composed"]), [(LABEL, PRODUCTS[0])])
        self.assertEqual(result["appendix"], f"{LABEL}\n{line(*home)}")

    def test_hidden_preview_image_is_not_chosen_as_the_product_link(self):
        """Kaynak botları önizleme görselini mesajın başındaki görünmez karaktere gömer."""
        image = "https://m.media-amazon.com/images/I/71abcDEF._AC_SL1500_.jpg"
        text = "\u200b" + BODY + "\n\n🔗 FIRSATA GİT"
        obj = rc.message(text, links=[("\u200b", image), ("FIRSATA GİT", PRODUCTS[0])], media=preview(image))
        self.assertEqual(rc.product_links(rc.compose(obj)), [(LABEL, PRODUCTS[0])])
        self.assertEqual(rc.product_links(rc.compose(rc.message(text, links=[("\u200b", image)]))), [],
                         "yalnız görsel varsa ürün satırı uydurulmaz")


class MultiLinkReachabilityTest(unittest.TestCase):
    """Çok linkli mesaj: satır sınırı hiçbir linki sessizce düşürmez."""

    def assertNothingLost(self, obj, result: dict, *kinds: str, msg: str = "") -> None:
        self.assertEqual(source_links(obj, *kinds) - result["reach"], set(), msg)

    def test_search_buttons_do_not_use_up_the_line_limit(self):
        """Arama butonları 4 satırlık hakkı tüketiyor, arkalarındaki kupon düşüyordu."""
        obj = rc.message(BODY, buttons=[*SEARCHES, BUY, COUPON])
        for mode, product_line in (("smart", LABEL), ("all", line(*BUY))):
            result = view(obj, mode)
            self.assertEqual(result["appendix"], f"{product_line}\n{line(*COUPON)}", mode)
            self.assertEqual(result["keyboard"], ["Google", "Akakçe", "Cimri"], "arama butonları klavyede kalır")
            self.assertNothingLost(obj, result, msg=mode)

    def test_account_copy_lists_the_search_buttons_it_cannot_send_as_keyboard(self):
        """Hesap kopyasında klavye yoktur; arama butonları da satır olarak taşınır."""
        obj = rc.message(BODY, buttons=[*SEARCHES, BUY])
        searches = f"🔗 {GOOGLE_SHOPPING[1]}\n{line(*AKAKCE)}\n🔗 {CIMRI[1]}"
        smart = view(obj, as_bot=False)
        self.assertEqual(smart["appendix"], f"{LABEL}\n{searches}")
        self.assertNothingLost(obj, smart)
        everything = view(obj, "all", as_bot=False)
        self.assertEqual(everything["appendix"], f"{searches}\n{line(*BUY)}")
        self.assertNothingLost(obj, everything)

    def test_line_behind_four_product_buttons_is_not_dropped(self):
        obj = rc.message(BODY, buttons=[*STORES[:4], COUPON])
        for as_bot in (True, False):
            result = view(obj, as_bot=as_bot)
            self.assertEqual(result["appendix"], f"{numbered(4)}\n{line(*COUPON)}", as_bot)
            self.assertNothingLost(obj, result)
        with_preview = rc.message(BODY, buttons=STORES[:4], media=preview(PREVIEW))
        account = view(with_preview, as_bot=False)
        self.assertEqual(account["appendix"], f"{numbered(4)}\n{line('Önizleme', PREVIEW)}")
        self.assertNothingLost(with_preview, account)

    def test_more_than_four_product_buttons(self):
        """İlk dördü ürün satırı olur; fazlası botta düğme, hesap kopyasında ``🔗`` satırıdır."""
        obj = rc.message(BODY, buttons=[*STORES[:6], COUPON])
        notification = view(obj)
        self.assertEqual(notification["appendix"], f"{numbered(4)}\n{line(*COUPON)}")
        self.assertEqual(notification["keyboard"][:2], ["Mağaza 5", "Mağaza 6"])
        self.assertNothingLost(obj, notification)
        account = view(obj, as_bot=False)
        self.assertEqual(account["appendix"],
                         f"{numbered(4)}\n{line(*STORES[4])}\n{line(*STORES[5])}\n{line(*COUPON)}")
        self.assertNothingLost(obj, account)

    def test_product_link_under_a_removed_tag_does_not_cost_another_link_its_line(self):
        """Etiketi silinen gizli linkler: ürün linki olan, diğerlerinin satır hakkını tüketmez."""
        tags = ["#reklam", "#işbirliği", "reklam", "işbirliği", "#reklam"]
        text = BODY + "\n\nbir #reklam iki #işbirliği üç reklam dört işbirliği beş #reklam"
        entities, cursor = [], 0
        for tag, url in zip(tags, PRODUCTS):
            start = text.index(tag, cursor)
            entities.append(types.MessageEntityTextUrl(
                offset=bot.utf16_length(text[:start]), length=bot.utf16_length(tag), url=url))
            cursor = start + len(tag)
        obj = SimpleNamespace(id=1, message=text, raw_text=text, media=None, entities=entities, reply_markup=None)
        self.assertEqual(len(source_links(obj)), 5, "beş link de etiketle birlikte silinmeden korunur")
        for as_bot in (True, False):
            result = view(obj, as_bot=as_bot)
            self.assertEqual(result["appendix"], "\n".join([LABEL, *(f"🔗 {url}" for url in PRODUCTS[1:5])]), as_bot)
            self.assertNothingLost(obj, result)

    def test_no_button_is_lost_in_the_bot_notification_in_any_mode(self):
        cases = {
            "5 ürün butonu": rc.message(BODY, buttons=STORES[:5]),
            "9 ürün butonu": rc.message(BODY, buttons=STORES),
            "arama + inceleme + ürün + kupon": rc.message(BODY, buttons=[*SEARCHES, REVIEW, BUY, COUPON]),
            "4 kupon + ürün": rc.message(BODY, buttons=[
                *[(f"Kupon {number}", f"https://t.me/kanal_kupon/{number}") for number in range(1, 5)], BUY]),
            "4 arama + 2 kupon": rc.message(BODY, buttons=[
                *SEARCHES, GOOGLE_WEB, COUPON, ("Kupon 2", "https://t.me/kanal_kupon/56")]),
            "gizli linkler + butonlar": rc.message(
                BODY + "\n\n🔎 Google\nFIRSATA GİT", links=[("Google", GOOGLE_WEB[1]), ("FIRSATA GİT", PRODUCTS[7])],
                buttons=[AKAKCE, *STORES[:3], COUPON]),
            "görünür adres + aynı adresli buton": rc.message(BODY + "\n" + PRODUCTS[0], buttons=[BUY, COUPON]),
        }
        for name, obj in cases.items():
            for mode in ("smart", "all", "off"):
                for limit in (TEXT, CAPTION):
                    with self.subTest(name, mode=mode, limit=limit):
                        result = view(obj, mode, limit=limit)
                        self.assertNothingLost(obj, result, "button")
                        self.assertNothingLost(obj, result, "entity", "text")
                        self.assertLessEqual(result["length"], limit)

    def test_account_copy_carries_four_product_lines_and_four_link_lines(self):
        """Klavyesiz kopyanın sınırı: 4 ürün satırı + 4 ``🔗`` satırı (eskiden toplam 4)."""
        obj = rc.message(BODY, buttons=STORES)
        account = view(obj, as_bot=False)
        self.assertEqual(account["appendix"], "\n".join([numbered(4), *(line(*item) for item in STORES[4:8])]))
        self.assertEqual(source_links(obj) - account["reach"], {key(PRODUCTS[8])}, "9. buton sınırın dışında kalır")
        self.assertEqual(view(obj, "off", as_bot=False)["appendix"], "", "off: eksiz düzen değişmez")


class LimitPressureTest(unittest.TestCase):
    """Karakter sınırı: hangi satır gövdeden önce gelir, hangisi gövdeye yer bırakır."""

    LONG = BODY + "\n" + "\n".join(f"Satır {number}: stok sınırlı, hızlı kargo, kaçırma!" for number in range(1, 21))

    def test_bot_link_line_never_shortens_the_body(self):
        """Sığmayan buton satırı yazılmaz; buton klavyede kalır, gövde onun yüzünden kırpılmaz."""
        obj = rc.message(self.LONG, buttons=[BUY, COUPON])
        roomy = view(obj, limit=TEXT)
        self.assertEqual(roomy["appendix"], f"{LABEL}\n{line(*COUPON)}")
        self.assertNotIn(COUPON[0], roomy["keyboard"])
        tight = view(obj, limit=CAPTION)
        self.assertEqual(tight["appendix"], LABEL, "ürün satırı kalır, kupon satırı yazılmaz")
        self.assertEqual(tight["keyboard"][0], COUPON[0], "kupon düğme olarak kalır")
        self.assertEqual(source_links(obj) - tight["reach"], set())
        without_coupon = view(rc.message(self.LONG, buttons=[BUY]), limit=CAPTION)
        self.assertEqual(tight["composed"]["body"], without_coupon["composed"]["body"],
                         "gövde, kupon butonu hiç yokmuş gibi aynı yerden kırpılır")
        self.assertLessEqual(tight["length"], CAPTION)

    def test_account_copy_keeps_the_first_four_links_and_adds_the_rest_while_they_fit(self):
        obj = rc.message(BODY, buttons=[*STORES[:6], COUPON])
        extras = [line(*STORES[4]), line(*STORES[5]), line(*COUPON)]
        full = view(obj, as_bot=False, limit=ACCOUNT_TEXT)
        self.assertEqual(full["appendix"], "\n".join([numbered(4), *extras]))
        # Tam sığan ileti: sınır bir birim daralınca son satır bırakılır, gövde kırpılmaz.
        sizes = [bot.utf16_length(text) + 1 for text in extras]
        for dropped, limit in ((0, full["length"]), (1, full["length"] - 1),
                               (2, full["length"] - sizes[2] - 1), (3, full["length"] - sizes[2] - sizes[1] - 1)):
            with self.subTest(dropped=dropped):
                result = view(obj, as_bot=False, limit=limit)
                self.assertEqual(result["appendix"], "\n".join([numbered(4), *extras[:3 - dropped]]))
                self.assertFalse(result["body_cut"], "fazladan satır gövdeyi kırpmaz")
                self.assertLessEqual(result["length"], limit)

    def test_account_copy_writes_its_first_four_links_even_if_the_body_is_cut(self):
        """Mesajın ilk 4 gizli bağlantısı eskisi gibi gövdeden önce gelir (tek taşıyıcı bu satırdır)."""
        obj = rc.message(self.LONG, buttons=[BUY, COUPON, REVIEW])
        result = view(obj, as_bot=False, limit=CAPTION)
        self.assertEqual(result["appendix"], f"{LABEL}\n{line(*COUPON)}\n{line(*REVIEW)}")
        self.assertTrue(result["body_cut"])
        self.assertEqual(source_links(obj) - result["reach"], set())
        self.assertLessEqual(result["length"], CAPTION)

    def test_product_line_survives_when_the_link_lines_cannot_fit_at_all(self):
        """Eskiden ek tümüyle düşerdi; artık yalnız sığmayan ``🔗`` satırları düşer."""
        huge = ("🎟 Kupon", "https://t.me/kanal_kupon/55?start=" + "a" * 990)
        obj = rc.message(BODY, buttons=[BUY, huge])
        account = view(obj, as_bot=False, limit=CAPTION)
        self.assertEqual(account["appendix"], LABEL)
        self.assertFalse(account["body_cut"])
        self.assertIn(key(BUY[1]), account["reach"])
        notification = view(obj, limit=CAPTION)
        self.assertEqual(notification["appendix"], LABEL)
        self.assertEqual(source_links(obj) - notification["reach"], set(), "botta kupon düğme olarak kalır")

    def test_link_under_a_removed_tag_keeps_its_line_under_pressure(self):
        """Etiketi silinen linkin tek taşıyıcısı kendi satırıdır; klavyeye bırakılabilen satırlarla düşmez."""
        hidden = "https://ty.gl/etiketalti"
        text = self.LONG + "\n#reklam"
        obj = rc.message(text, links=[("#reklam", hidden)], buttons=[GOOGLE_SHOPPING, *STORES[:3]])
        for mode in ("smart", "all"):
            result = view(obj, mode, limit=CAPTION)
            self.assertIn(key(hidden), result["reach"], mode)
            self.assertEqual(source_links(obj, "button") - result["reach"], set(), mode)
            self.assertLessEqual(result["length"], CAPTION)
        everything = view(obj, "all", limit=CAPTION)
        self.assertEqual(everything["appendix"], f"🔗 {hidden}", "buton satırları klavyeye bırakılır, bu satır kalır")
        self.assertEqual(everything["keyboard"][:3], [label for label, _ in STORES[:3]])

    def test_random_messages_keep_their_links(self):
        """Rastgele çok linkli mesajlar: sınır aşılmaz, etiket doğru adrese bağlıdır, buton kaybolmaz."""
        rnd = random.Random(20261114)
        others = [COUPON, REVIEW, GOOGLE_SHOPPING, AKAKCE, CIMRI, GOOGLE_WEB, ("Topluluk", "https://discord.gg/AbCdEf"),
                  ("🛍 Amazon", "https://www.amazon.com.tr/?tag=kanal-21")]
        words = ["Philips", "Airfryer", "XXL", "6.2L", "Bosch", "Matkap", "Nivea", "Krem", "🔥", "💥", "Stok", "kargo"]
        for seed in range(120):
            product_buttons = rnd.sample(STORES[:6], rnd.randint(0, 4))
            other_buttons = rnd.sample(others, rnd.randint(0, 4))
            buttons = product_buttons + other_buttons
            rnd.shuffle(buttons)
            lines = [" ".join(rnd.sample(words, 4)), f"💰 {rnd.randint(20, 9000)},90 TL"]
            links = []
            for number in range(rnd.randint(0, 3)):
                label = f"Link{seed}x{number}"
                lines.append(f"{rnd.choice(['🔗', '👉', '🛍'])} {label}")
                links.append((label, f"https://ty.gl/gizli{seed}x{number}"))
            if rnd.random() < 0.3:
                lines.append(f"Adres: https://sl.n11.com/n/acik{seed}")
            lines.extend(" ".join(rnd.sample(words, 5)) * rnd.choice([1, 1, 8]) for _ in range(rnd.randint(0, 3)))
            obj = rc.message("\n".join(lines), links=links, buttons=buttons)
            for mode in ("smart", "all"):
                for as_bot, limit in ((True, CAPTION), (True, TEXT), (False, CAPTION), (False, ACCOUNT_TEXT)):
                    with self.subTest(seed=seed, mode=mode, as_bot=as_bot, limit=limit):
                        result = view(obj, mode, as_bot=as_bot, limit=limit)
                        composed = result["composed"]
                        self.assertLessEqual(result["length"], limit)
                        for entity in composed["entities"]:
                            url = getattr(entity, "url", None)
                            label = bot.utf16_slice(composed["text"], entity.offset, entity.length)
                            if label.startswith("👉 Ürün Linki"):
                                self.assertTrue(bot.is_product_link(url), url)
                        marks = [text for text in composed["appendix"].split("\n") if text.startswith("👉 Ürün Linki")]
                        self.assertEqual([label for label, _ in rc.product_links(composed)], marks,
                                         "her ürün satırı tek bir gizli bağlantı taşır")
                        if as_bot:
                            self.assertEqual(source_links(obj, "button") - result["reach"], set())
                        if not result["body_cut"]:
                            self.assertEqual(source_links(obj, "entity", "text") - result["reach"], set())
                        if limit == ACCOUNT_TEXT and mode == "smart":
                            # En çok 4 ürün + 4 başka buton: klavyesiz kopya hepsini taşır.
                            self.assertEqual(source_links(obj) - result["reach"], set())


class BotApiWireTest(unittest.TestCase):
    """Bot API'ye giden gerçek istek: ``👉 Ürün Linki 👈`` tıklanabilir ``text_link`` olarak gider."""

    TEXT = "🔥🔥 Ateşler Kuruyemiş Badem İçi 150 gr 💥\n\n💰 106,26 TL\n\n🔎 Google"
    BUTTONS = [*SEARCHES, BUY, COUPON]
    BADGE = "📌 2 kere paylaşıldı: firsatz · tek grup"

    def deliver(self, *, media: bool, buttons, bot_up: bool = True) -> tuple[list[dict], dict]:
        """Aynı fırsatı iki kez işle (ikincisi 📌 düzenlemesi); giden istekleri ve hesap kopyasını çöz.

        Sahte olan yalnız iki ağ sınırıdır (Telethon istemcisi ve ``urlopen``); aradaki
        ``main()`` → ``on_new_message`` → ``notify_offer`` → ``send_bot_*`` üretim kodudur.
        """
        for name in ("send_bot_ping", "send_bot_media", "edit_bot_text", "edit_bot_caption"):
            self.assertEqual(getattr(bot, name).__qualname__, name, "önceki bir test sahte göndericiyi geri almamış")
        wire = golden.BotApiWire(ok=bot_up)
        records: list[dict] = []
        account: dict = {}

        def event(message_id: int) -> ti.FakeEvent:
            return ti.FakeEvent(
                next(iter(bot.SOURCE_IDS)), 999, self.TEXT, message_id=message_id, media=media,
                entities=[rc.linked(self.TEXT, "Google", GOOGLE_WEB[1])],
                reply_markup=ti.FakeMarkup([ti.FakeRow([ti.FakeButton(label, url=url)]) for label, url in buttons]))

        async def scenario(client, queue) -> None:
            handler = client.handlers[1][1]
            # Yalıtım: önceki test modüllerinden kalan "görüldü" kayıtları bu iletileri yutmasın.
            for registry in (bot.DEDUP_CACHE, bot.LIVE_SEEN, bot.POLL_DISPATCHED, bot.SOURCE_IN_FLIGHT,
                             bot.CURSOR_RAM):
                registry.clear()
            with mock.patch.object(bot, "DEDUP_EDIT_MIN_INTERVAL", 0):
                for message_id in (7, 8) if bot_up else (7,):
                    await handler(event(message_id))
                    await asyncio.sleep(0.05)
            records.extend(golden.wire_request(request, full=bot_up) for request in wire.requests)
            if client.sent:
                account.update(text=client.sent[-1][1], entities=client.sent_kwargs[-1]["formatting_entities"])
            if client.files:
                account.update(text=client.files[-1][2], entities=client.file_kwargs[-1]["formatting_entities"])

        golden.run_bot(golden.PROFILES["prod"], scenario, wire=wire)
        return records, account

    def assertClickableProductLink(self, record: dict, text_field: str, entities_field: str, url: str) -> None:
        fields = record["fields"]
        text = fields[text_field]
        self.assertNotIn("parse_mode", fields, "entity listesi parse_mode ile birlikte gönderilmez")
        self.assertIsInstance(fields[entities_field], list)
        units = bot.utf16_length(text)
        # Yardımcı çift (surrogate) ortasına düşen konum Telegram'da entity'yi bozar.
        boundaries = {bot.utf16_length(text[:index]) for index in range(len(text) + 1)}
        for entity in fields[entities_field]:
            self.assertGreaterEqual(entity["offset"], 0)
            self.assertGreater(entity["length"], 0)
            self.assertLessEqual(entity["offset"] + entity["length"], units, entity)
            self.assertIn(entity["offset"], boundaries, entity)
            self.assertIn(entity["offset"] + entity["length"], boundaries, entity)
        links = [entity for entity in fields[entities_field] if entity["type"] == "text_link"
                 and bot.utf16_slice(text, entity["offset"], entity["length"]).startswith("👉")]
        self.assertEqual(links, [{"type": "text_link", "offset": bot.utf16_length(text[:text.index(LABEL)]),
                                  "length": bot.utf16_length(LABEL), "url": url}])
        self.assertEqual(bot.utf16_slice(text, links[0]["offset"], links[0]["length"]), LABEL)
        self.assertNotEqual(links[0]["offset"], text.index(LABEL),
                            "önünde emoji var: UTF-16 konumu karakter sırasından farklıdır")
        self.assertEqual(text.count(LABEL), 1)
        self.assertNotIn(url, text, "ürün adresi metne açık yazılmaz")
        # Kupon butonu satır olur; kaynağın arama butonları metne yazılmaz, klavyede kalır.
        self.assertIn(f"\n\n{LABEL}\n{line(*COUPON)}\n\n", text)
        for _, search in SEARCHES:
            self.assertNotIn(search, text)
        buttons = [button for row in fields["reply_markup"]["inline_keyboard"] for button in row]
        self.assertEqual([(button["text"], button["url"]) for button in buttons], SEARCHES,
                         "ürün ve kupon metinde tıklanabilir, düğmesi yinelenmez; arama düğmeleri kalır")
        for button in buttons:
            self.assertEqual(sorted(button), ["text", "url"])

    def test_text_notification_and_its_badge_edit(self):
        (send, edit), _ = self.deliver(media=False, buttons=self.BUTTONS)
        self.assertEqual((send["method"], edit["method"]), ("sendMessage", "editMessageText"))
        for record in (send, edit):
            self.assertClickableProductLink(record, "text", "entities", BUY[1])
            self.assertEqual(record["fields"]["link_preview_options"], {"is_disabled": True},
                             "gizli adres ikinci bir ürün kartı (önizleme) açmaz")
        self.assertTrue(edit["fields"]["text"].endswith(self.BADGE))
        self.assertEqual(edit["fields"]["reply_markup"], send["fields"]["reply_markup"])
        self.assertEqual(edit["fields"]["message_id"], golden.BOT_MESSAGE_ID)

    def test_photo_notification_and_its_badge_edit(self):
        (send, edit), _ = self.deliver(media=True, buttons=self.BUTTONS)
        self.assertEqual((send["method"], edit["method"]), ("sendPhoto", "editMessageCaption"))
        self.assertEqual([item["field"] for item in send["files"]], ["photo"])
        for record in (send, edit):
            self.assertNotIn("entities", record["fields"], "medyada alan adı caption_entities'tir")
            self.assertClickableProductLink(record, "caption", "caption_entities", BUY[1])
            self.assertLessEqual(bot.utf16_length(record["fields"]["caption"]), bot.CAPTION_LIMIT)
        self.assertTrue(edit["fields"]["caption"].endswith(self.BADGE))
        self.assertEqual(edit["fields"]["reply_markup"], send["fields"]["reply_markup"])

    def test_several_product_buttons_each_get_their_own_link(self):
        for media, text_field, entities_field in ((False, "text", "entities"), (True, "caption", "caption_entities")):
            records, _ = self.deliver(media=media, buttons=STORES[:6])
            self.assertEqual(len(records), 2)
            for record in records:
                fields = record["fields"]
                text = fields[text_field]
                found = [(bot.utf16_slice(text, entity["offset"], entity["length"]), entity["url"])
                         for entity in fields[entities_field] if entity["type"] == "text_link"]
                self.assertEqual(found[1:],
                                 [(f"👉 Ürün Linki {number} 👈", PRODUCTS[number - 1]) for number in range(1, 5)])
                buttons = [button for row in fields["reply_markup"]["inline_keyboard"] for button in row]
                self.assertEqual([(button["text"], button["url"]) for button in buttons[:2]], STORES[4:6],
                                 "5. ve 6. ürün butonu klavyede kalır")

    def test_account_copy_carries_every_link_when_the_bot_is_rejected(self):
        """Bot API reddederse kopyayı kullanıcı hesabı atar: klavye yok, arama butonları da satırdır."""
        for media in (False, True):
            with self.subTest(media=media):
                records, account = self.deliver(media=media, buttons=self.BUTTONS, bot_up=False)
                self.assertTrue(records, "önce bot denenir")
                text = account["text"]
                hidden = [(bot.utf16_slice(text, entity.offset, entity.length), entity.url)
                          for entity in account["entities"] if isinstance(entity, types.MessageEntityTextUrl)]
                self.assertEqual(hidden, [("Google", GOOGLE_WEB[1]), (LABEL, BUY[1])])
                self.assertIn("\n\n" + "\n".join([LABEL, f"🔗 {GOOGLE_SHOPPING[1]}", line(*AKAKCE), f"🔗 {CIMRI[1]}",
                                                   line(*COUPON)]) + "\n\n", text)
                reach = bot.visible_urls(text) | {key(url) for _, url in hidden}
                self.assertEqual({key(url) for _, url in [*self.BUTTONS, GOOGLE_WEB]} - reach, set())


if __name__ == "__main__":
    unittest.main()
