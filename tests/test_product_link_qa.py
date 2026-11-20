"""Ürün linki QA'sı: alan adı kuralı, çok linkli mesajlar ve Bot API gövdesi; tümü çevrimdışıdır.

1. ``is_product_link`` hangi adresi ürün sayar? Sosyal ağ/topluluk adresleri, alan adının
   yazım varyantları, benzer (lookalike) alan adları, yalnız izleme etiketi taşıyan mağaza
   ana sayfası, görsel dosyası.
2. Birden çok link taşıyan mesajda kaynak linklerinin hepsi bildirimden açılabiliyor mu?
   Bot bildirimi metin + gizli hyperlink + inline klavyeyle, hesap kopyası (klavyesiz)
   metin + gizli hyperlink'le taşır. ``smart`` / ``all`` / ``off``; satır ve karakter sınırı.
   Karakter sınırında ölçü yalın (PR öncesi) düzendir: onun açtığı hiçbir bağlantı kapanmaz
   (bkz. ``plain_layout``); ürün satırı ayrıca gövdedeki hiçbir bağlantıyı kesmez.
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
import appstate  # noqa: E402
import botentities  # noqa: E402
import confutil  # noqa: E402
import constants  # noqa: E402
import header  # noqa: E402
import producttitle  # noqa: E402
import sanitize  # noqa: E402
import searchbuttons  # noqa: E402
import link_reach_audit as audit  # noqa: E402
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
COMMUNITY = ("Topluluk", "https://discord.gg/AbCdEf")
CHANNEL = ("Kanal", "https://t.me/kanal_abc")
GOOGLE_SHOPPING = ("Google", "https://www.google.com/search?udm=28&q=badem")
GOOGLE_WEB = ("Google web", "https://www.google.com/search?q=badem")
AKAKCE = ("Akakçe", "https://www.akakce.com/arama/?q=badem")
CIMRI = ("Cimri", "https://www.cimri.com/arama?q=badem")
SEARCHES = [GOOGLE_SHOPPING, AKAKCE, CIMRI]
PREVIEW = "https://www.hepsiburada.com/urun-p-HBV000PREVIEW"
CAPTION, TEXT, ACCOUNT_TEXT = constants.CAPTION_LIMIT - 24, constants.MESSAGE_LIMIT - 200, constants.MESSAGE_LIMIT - 100


def key(url: str) -> str:
    return sanitize.clean_url(url).rstrip("/").lower()


def preview(url: str) -> SimpleNamespace:
    return SimpleNamespace(webpage=SimpleNamespace(url=url, title="Önizleme"))


def source_links(obj, *kinds: str) -> set[str]:
    """Temizlenmiş kaynak mesajın taşıdığı adresler (bildirimin taşıması gerekenler)."""
    return {key(item["url"]) for item in sanitize.extract_links(sanitize.sanitize_message(obj))
            if not kinds or item["kind"] in kinds}


def view(obj, mode: str = "smart", *, as_bot: bool = True, limit: int | None = None) -> dict:
    """Teslim edilen ileti ve ondan açılabilen adresler.

    Bot bildirimi: görünür URL + ``text_link`` + inline klavye düğmesi. Hesap kopyası:
    görünür URL + gizli hyperlink (kullanıcı hesabı klavye gönderemez).
    """
    composed = rc.compose(obj, mode, as_bot=as_bot, **({} if limit is None else {"limit": limit}))
    reach = set(sanitize.visible_urls(composed["text"]))
    labels: list[str] = []
    if as_bot:
        entities = botentities.bot_api_entities(composed["message"], composed["content"]) + botentities.source_name_entity(composed)
        keyboard = searchbuttons.build_inline_keyboard(obj, body_text=composed["text"], body_entities=entities)
        buttons = [button for row in (keyboard or {}).get("inline_keyboard", []) for button in row]
        labels = [button["text"] for button in buttons]
        reach |= {key(item["url"]) for item in entities if item.get("type") == "text_link"}
        reach |= {key(button["url"]) for button in buttons}
    else:
        reach |= {key(entity.url) for entity in composed["entities"] if getattr(entity, "url", None)}
    return {"composed": composed, "appendix": composed["appendix"], "reach": reach, "keyboard": labels,
            "length": sanitize.utf16_length(composed["text"]),
            "body_cut": composed["body"] != sanitize.message_text(sanitize.sanitize_message(obj))}


def plain_layout(obj, mode: str = "smart", *, as_bot: bool = True, limit: int = TEXT) -> dict:
    """Yalın (PR öncesi, ``13ad981``) düzen ve ondan açılabilen adresler: sınır kuralının ölçüsü.

    Üst satır + gövde + mesajın ilk gizli bağlantılarının ``🔗`` satırları + ``Mesajı Gör`` +
    kaynak adı. Satırlar gövdesiz sığıyorsa gövde kalan yere (``room``) kırpılır; sığmıyorsa
    hiçbiri yazılmaz. Ürün satırı, eleme ve satır önceliği yoktur. ``tests/link_reach_audit.py``
    kümelerindeki 54.102 iletinin hepsinde gerçek ``13ad981`` çıktısıyla aynı adresleri verir.

    Yeni düzende ``🔗 Mesajı Gör`` satırı metinden kalktı (mesaj adresi kaynak adına
    gömüldü) ama üretim o satırın **bütçesini** ayrıca tutar
    (``compose_message`` içindeki ``measure_tail``). Bu yüzden model eski satırı
    geri koyar: satırın yeri ölçüde durduğu için kırpma noktası ve ``🔗`` satırı
    seçimi eskisiyle aynı kalır; bildirim yalnızca KISALIR.
    """
    composed = rc.compose(obj, mode, as_bot=as_bot, limit=limit)  # üst satır ve alt satırlar ortaktır
    cleaned = sanitize.sanitize_message(obj)
    body = sanitize.message_text(cleaned)
    kinds = confutil.link_kinds_for(mode, bot=as_bot)
    lines = sanitize.build_link_appendix(cleaned, kinds).splitlines() if kinds else []
    removed = getattr(cleaned, "preserved_links", None) or []
    if removed:  # etiketi silinen gizli linkler
        only = SimpleNamespace(message=body, entities=[], reply_markup=None, media=None, preserved_links=removed)
        lines += [text for text in sanitize.build_link_appendix(only, ("webpage",)).splitlines() if text not in lines]

    # ``🔗 Mesajı Gör`` satırı yeni düzende metinden kalktı (adres kaynak adına
    # gömüldü) ama BÜTÇESİ yerinde duruyor: üretim o satırın yerini ölçüde
    # ayrılmış tutar (bkz. ``compose_message`` içindeki ``measure_tail``). Yalın
    # düzen de eski satırı geri koyar; yoksa boşalan yer bu modele fazladan
    # ``🔗`` satırı yazdırır ve model üretimden ayrışırdı.
    source_row = composed["source_line"] or (
        f"🔗 {constants.MESSAGE_LINK_LABEL}: {rc.MESSAGE_LINK}" if composed["source_name"] else "")

    def join(content: str) -> str:
        parts = (composed["header"], content, "\n".join(lines), source_row, composed["source_name"])
        return "\n\n".join(part for part in parts if part)

    if sanitize.utf16_length(join("")) >= limit:
        lines = []
    room = max(0, limit - sanitize.utf16_length(join("")) - 2)
    cut = body if sanitize.utf16_length(body) <= room else (header.utf16_budget(body, room - 1).rstrip() + "…" if room else "")
    hidden = {key(entity.url) for entity in botentities.entities_for_text(cleaned, cut) if getattr(entity, "url", None)}
    reach = set(sanitize.visible_urls(join(cut))) | hidden
    if as_bot:
        keyboard = searchbuttons.build_inline_keyboard(
            obj, body_text=join(cut), body_entities=[{"type": "text_link", "url": url} for url in hidden])
        reach |= {key(button["url"]) for row in (keyboard or {}).get("inline_keyboard", []) for button in row}
    return {"appendix": "\n".join(lines), "body": cut, "room": room, "reach": reach & source_links(obj)}


def line(label: str, url: str) -> str:
    return f"🔗 {label}: {url}"


def padded(button: tuple[str, str], size: int) -> tuple[str, str]:
    """Aynı buton, adresine ``size`` karakterlik sorgu eklenmiş hâliyle."""
    label, url = button
    return label, url + ("&" if "?" in url else "?") + "ref=" + "a" * size


def numbered(count: int) -> str:
    return "\n".join(f"👉 Ürün Linki {number} 👈" for number in range(1, count + 1))


class ProductLinkRuleTest(unittest.TestCase):
    """``is_product_link``: neyin ``👉 Ürün Linki 👈`` olabileceği."""

    def assertProduct(self, expected: bool, *urls: str) -> None:
        for url in urls:
            with self.subTest(url=url):
                self.assertIs(producttitle.is_product_link(url), expected)

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
                offset=sanitize.utf16_length(text[:start]), length=sanitize.utf16_length(tag), url=url))
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
    """Karakter sınırı: hangi satır gövdeden önce gelir, hangisi gövdeye yer bırakır.

    Ölçü yalın (PR öncesi) düzendir (``plain_layout``): onun açtığı hiçbir bağlantı kapanmaz.
    Bunun içinde öncelik: gövde ve bağlantıları, ürün satırı, ``Mesajı Gör``; fazladan ``🔗``
    satırları ilk bırakılandır. Ürün satırı gövde metninden yer alabilir ama yazılmasaydı
    gövdeden açılacak bir bağlantıyı kesemez.
    """

    FILLER = [f"Satır {number}: stok sınırlı, hızlı kargo, kaçırma!" for number in range(1, 41)]
    LONG = BODY + "\n" + "\n".join(FILLER[:20])
    ENDLESS = BODY + "\n" + "\n".join(FILLER)  # hiçbir fotoğraf açıklamasına sığmaz
    MARK, HIDDEN = "Fırsat linki", "https://ty.gl/govdedeki"
    OTHERS = [COUPON, REVIEW, COMMUNITY, CHANNEL]
    # İki satırı birlikte hiçbir fotoğraf açıklamasına sığmayan butonlar: yalın düzen ek yazamaz.
    HUGE = [padded(BUY, 500), padded(COUPON, 450)]

    def at_the_cut(self, buttons, *, as_bot: bool, url: str = HIDDEN, visible: bool = False, past: int = 0):
        """Sığmayan bir gövde; ``url`` bağlantısı yalın düzenin kırpma noktasına ucu ucuna sığar.

        Gövdeye kalan yer tek birim daralsa bağlantı kesilir: gizli bağlantının yazısı (``visible``:
        adresin kendisi) kırpılan gövdenin son satırıdır. ``past`` artıysa bağlantının sonu kırpma
        noktasını o kadar aşar (yalın düzende de açılamaz); eksiyse o kadar geride biter.
        """
        mark = url if visible else self.MARK
        room = plain_layout(rc.message(self.ENDLESS, buttons=buttons), as_bot=as_bot, limit=CAPTION)["room"]
        # Kırpılan gövde ``room - 1`` birim + "…": adres tam görünmeli, gizli bağlantı ise son harfini "…"ye bırakabilir.
        start = room - (1 if visible else 0) + past - sanitize.utf16_length(mark)
        text = header.utf16_budget(self.ENDLESS, start - 1) + "\n" + mark + "\n" + "\n".join(self.FILLER)
        obj = rc.message(text, links=() if visible else [(mark, url)], buttons=buttons)
        plain = plain_layout(obj, as_bot=as_bot, limit=CAPTION)
        self.assertEqual(plain["room"], room, "üst satır değişmedi")
        self.assertEqual(key(url) in plain["reach"], past <= 0)
        if not past:
            tighter = plain_layout(obj, as_bot=as_bot, limit=CAPTION - 1)
            self.assertNotIn(self.MARK if not visible else url, tighter["body"][-len(mark):], "bir birim daha yer yok")
        return obj, plain

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
        sizes = [sanitize.utf16_length(text) + 1 for text in extras]
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

    def test_lines_the_plain_layout_cannot_write_never_cut_the_body(self):
        """Yalın düzen eki sığdıramayıp gövdeyi koruyorsa gövde yine tam kalır; yanına sığan satır yazılır."""
        text = self.LONG[:500] + "\n" + self.MARK
        obj = rc.message(text, links=[(self.MARK, self.HIDDEN)], buttons=self.HUGE)
        for as_bot in (False, True):
            with self.subTest(as_bot=as_bot):
                plain = plain_layout(obj, as_bot=as_bot, limit=CAPTION)
                self.assertEqual((plain["appendix"], plain["body"]), ("", text), "ek sığmıyor; gövde tam")
                result = view(obj, as_bot=as_bot, limit=CAPTION)
                self.assertEqual(result["appendix"], LABEL, "kupon satırı tam gövdenin yanına sığmıyor")
                self.assertFalse(result["body_cut"])
                self.assertEqual(plain["reach"] - result["reach"], set())
                self.assertLessEqual(result["length"], CAPTION)
        self.assertEqual(view(obj, limit=CAPTION)["keyboard"], [self.HUGE[1][0], *rc.SEARCH_ROW], "kupon klavyede")
        # Gövde kısaysa aynı satır yanına sığar: hesap kopyasında yazılır, gövde yine kırpılmaz.
        short = rc.message(BODY + "\n" + self.MARK, links=[(self.MARK, self.HIDDEN)], buttons=self.HUGE)
        # Ürün satırı ile yalın düzen satırı aynı yere talip; yalın düzen burada
        # yazamıyorsa ürün satırı yazılır, gövde yine kırpılmaz.
        self.assertIn(plain_layout(short, as_bot=False, limit=CAPTION)["appendix"], ("", LABEL))
        result = view(short, as_bot=False, limit=CAPTION)
        self.assertEqual(result["appendix"], f"{LABEL}\n{line(*self.HUGE[1])}")
        self.assertFalse(result["body_cut"])

    def test_raw_dump_lines_never_cut_the_body_either(self):
        """``all``: yalın düzenin yazamadığı döküm satırlarından yalnız tam gövdenin yanına sığanlar yazılır."""
        text = self.LONG[:500] + "\n" + self.MARK
        searches = [padded(GOOGLE_SHOPPING, 400), padded(AKAKCE, 400)]
        obj = rc.message(text, links=[(self.MARK, self.HIDDEN)], buttons=searches, media=preview(PREVIEW + "?ref=" + "a" * 300))
        for as_bot in (False, True):
            with self.subTest(as_bot=as_bot):
                plain = plain_layout(obj, "all", as_bot=as_bot, limit=CAPTION)
                self.assertEqual((plain["appendix"], plain["body"]), ("", text))
                result = view(obj, "all", as_bot=as_bot, limit=CAPTION)
                # ``Mesajı Gör`` satırı kalkınca ek bir satır kısaldı: ilk satırdan
                # sonra tam gövdenin yanına sığan ikinci satır da yazılabiliyor.
                self.assertTrue(result["appendix"].startswith(line(self.MARK, self.HIDDEN)),
                                "sırada ilk satır yazılır")
                self.assertFalse(result["body_cut"])
                self.assertEqual(plain["reach"] - result["reach"], set())
        self.assertEqual(source_links(obj, "button") - view(obj, "all", limit=CAPTION)["reach"], set())

    def test_product_line_never_pushes_a_body_link_behind_the_cut(self):
        """Ürün satırının 17 birimi gövdedeki son bağlantıyı kesecekse satır yazılmaz (hesap kopyası)."""
        for name, buttons in (("yalın düzen ek yazamıyor", self.HUGE), ("yalın düzen 4 satır yazıyor", [*self.OTHERS, BUY])):
            for visible in (False, True):
                with self.subTest(name, visible=visible):
                    obj, plain = self.at_the_cut(buttons, as_bot=False, visible=visible)
                    result = view(obj, as_bot=False, limit=CAPTION)
                    self.assertEqual(result["appendix"], plain["appendix"], "satırlar yalın düzendeki gibidir")
                    # Gövde yalın düzenden KISA olamaz: mesaj linki satırı metinden
                    # kalktığı için aynı sınırda gövdeye daha çok yer kalır (kullanıcı
                    # isteği: bildirim yalnız kısalsın; gövde hiçbir zaman kırpılmasın).
                    self.assertGreaterEqual(
                        sanitize.utf16_length(result["composed"]["body"]), sanitize.utf16_length(plain["body"]),
                        "gövde yalın düzenden kısa kalmaz")
                    self.assertIn(key(self.HIDDEN), result["reach"])
                    self.assertEqual(plain["reach"] - result["reach"], set())
                    self.assertLessEqual(result["length"], CAPTION)

    def test_address_the_plain_layout_already_cuts_does_not_hold_the_product_line_back(self):
        """Yalın düzende de yarım kalan adres açılamıyordu; kalan parçası korunacak bağlantı sayılmaz.

        Yeni düzende ölçü satırı (``Mesajı Gör``) metinden kalktı ama bütçesi ayrıca
        tutulduğu için kırpma noktası değişmedi: adres yine 5 birim eksik kalır.
        """
        obj, plain = self.at_the_cut([*self.OTHERS, BUY], as_bot=False, visible=True, past=5)
        self.assertIn(self.HIDDEN[:-5] + "…", plain["body"][-len(self.HIDDEN):],
                      "adres yalın düzende yarım")
        result = view(obj, as_bot=False, limit=CAPTION)
        self.assertEqual(result["appendix"], f"{LABEL}\n{plain['appendix']}")
        self.assertEqual(plain["reach"] - result["reach"], set())

    def test_product_line_takes_its_room_from_the_body_text(self):
        """Kesilen kısımda bağlantı yoksa ürün satırı yazılır; yerini gövde metninden alır."""
        obj = rc.message(self.ENDLESS, buttons=[*self.OTHERS, BUY])
        plain = plain_layout(obj, as_bot=False, limit=CAPTION)
        result = view(obj, as_bot=False, limit=CAPTION)
        self.assertEqual(result["appendix"], f"{LABEL}\n{plain['appendix']}")
        # Ölçü UTF-16 birimidir (Telegram'ın saydığı birim); ``len`` emojili satırda
        # karakter sayar ve farkı olduğundan büyük gösterir. Ürün satırı yerini
        # gövde metninden alır: gövde, ürün satırı hiç yazılmasaydı olacağı yerden
        # tam olarak satır + ayraç kadar kısalır (kaynak adı/mesaj linki bütçesi
        # sabit tutulduğu için fazladan bir kayıp yok).
        with mock.patch.object(bot, "product_links", return_value=[]):
            without = view(obj, as_bot=False, limit=CAPTION)
        drop = sanitize.utf16_length(without["composed"]["body"]) - sanitize.utf16_length(result["composed"]["body"])
        self.assertEqual(drop, sanitize.utf16_length(LABEL) + 1, "ürün satırı yerini gövdeden alır (satır + ayraç)")
        self.assertEqual((result["reach"] & source_links(obj)) - plain["reach"], {key(BUY[1])}, "ürün adresi açıldı")
        self.assertEqual(plain["reach"] - result["reach"], set())
        self.assertLessEqual(result["length"], CAPTION)

    def test_product_line_stays_when_it_carries_the_link_it_cuts(self):
        """Kesilen gövde bağlantısı ürünün kendisiyse adres ürün satırından açılır; satır yazılır."""
        # Ürünün gizli bağlantısı kırpma noktasının 3 birim gerisinde kalır: kırpılan
        # gövdede bağlantı açılamaz, adres ürün satırından açılır.
        obj, plain = self.at_the_cut([*self.OTHERS, BUY], as_bot=False, url=BUY[1], past=3)
        result = view(obj, as_bot=False, limit=CAPTION)
        self.assertEqual(result["appendix"], f"{LABEL}\n{plain['appendix']}")
        # Gövdedeki bağlantı kesildi: mesajının kendi adresini taşıyan kaynak adı
        # dışında tek ``text_link`` ürün satırıdır.
        hidden = [entity for entity in result["composed"]["entities"] if getattr(entity, "url", None)]
        self.assertEqual([entity.url for entity in hidden if entity.url != rc.MESSAGE_LINK][-1], BUY[1])
        self.assertIn(rc.MESSAGE_LINK, [entity.url for entity in hidden], "kaynak adı kendi mesajına bağlı")
        self.assertIn("👉", result["appendix"][:3], "adresi ürün satırı taşıyor")
        self.assertEqual(rc.product_links(result["composed"]), [(LABEL, BUY[1])])
        self.assertEqual(plain["reach"] - result["reach"], set())
        # Biçimsiz son çare iletisinde adres satıra açıkça yazılır; kural aynıdır.
        bare = rc.compose(obj, as_bot=False, limit=CAPTION, hidden_links=False)
        self.assertTrue(bare["appendix"].startswith(f"{LABEL}: {BUY[1]}\n"))
        self.assertLessEqual(sanitize.utf16_length(bare["text"]), CAPTION)

    def test_lines_the_plain_layout_writes_are_not_traded_for_product_lines(self):
        """Yalın düzenin yazdığı ``🔗`` satırı ürün satırına yer açmak için bırakılmaz: hesap kopyasında tek taşıyıcıdır."""
        for size, expected in ((180, 0), (175, 1), (170, 2), (160, 3)):
            with self.subTest(size=size):
                obj = rc.message(BODY, buttons=[*(padded(button, size) for button in self.OTHERS), *STORES[:3]])
                plain = plain_layout(obj, as_bot=False, limit=CAPTION)
                self.assertEqual(plain["appendix"].count("\n"), 3, "dört uzun satır sınırı dolduruyor")
                self.assertLess(plain["room"], sanitize.utf16_length(BODY) + 3 * (len(LABEL) + 3) - 17 * (2 - expected))
                result = view(obj, as_bot=False, limit=CAPTION)
                marks = LABEL if expected == 1 else numbered(expected)
                self.assertEqual(result["appendix"], "\n".join(part for part in (marks, plain["appendix"]) if part),
                                 "sığdığı kadar ürün satırı; yalın düzenin satırları yerinde")
                self.assertEqual([url for _, url in rc.product_links(result["composed"])], PRODUCTS[:expected])
                self.assertEqual(plain["reach"] - result["reach"], set())
                self.assertLessEqual(result["length"], CAPTION)

    def test_lines_with_no_other_carrier_are_the_last_to_go(self):
        """Yalın düzen eki yazamıyorsa önce klavyede de duran buton satırları bırakılır, sonra tek taşıyıcı satırlar."""
        text = self.LONG[:500]
        tagged = "https://t.me/kanal_etiketalti"
        obj = rc.message(text + "\n#reklam", links=[("#reklam", tagged)],
                         buttons=[padded(COUPON, 150), padded(REVIEW, 150), padded(COMMUNITY, 600)])
        plain = plain_layout(obj, limit=CAPTION)
        self.assertEqual((plain["appendix"], plain["body"]), ("", text), "ek sığmıyor; gövde tam")
        result = view(obj, limit=CAPTION)
        # Kural: gövde kırpılmaz ve etiketi silinen linkin tek taşıyıcısı olan
        # ``🔗`` satırı yazılır. Sığmayan buton satırları klavyede kalır — hangi
        # satırların sığdığı ek bütçesine bağlıdır (düzen değişince bir satır
        # kayar), bu yüzden burada sabit liste değil kural doğrulanır.
        self.assertIn(f"🔗 {tagged}", result["appendix"],
                      "etiketi silinen linkin satırı kalır; sondaki buton satırları klavyeye bırakılır")
        self.assertFalse(result["body_cut"])
        self.assertIn(COMMUNITY[0], result["keyboard"], "satırı yazılmayan buton klavyede")
        self.assertEqual(source_links(obj) - result["reach"], set())

    def test_lines_that_fill_a_textless_message_to_the_last_unit_lose_one_line_not_all(self):
        """Gövdesiz iletide ek sınırı tam doldurursa tümü düşmez; sondaki satır bırakılır."""
        obj = rc.message("", buttons=[BUY, COUPON, REVIEW])
        roomy = view(obj, as_bot=False, limit=CAPTION)
        self.assertEqual(roomy["appendix"], f"{LABEL}\n{line(*COUPON)}\n{line(*REVIEW)}")
        exact = view(obj, as_bot=False, limit=roomy["length"])
        self.assertEqual(exact["appendix"], f"{LABEL}\n{line(*COUPON)}")
        self.assertLess(exact["length"], roomy["length"])

    def test_bot_keeps_every_unwritten_button_on_the_keyboard(self):
        """Yazılmayan ürün ve ``🔗`` satırlarının butonları klavyede kalır; arama satırı bozulmaz."""
        obj, plain = self.at_the_cut(self.HUGE, as_bot=True)
        result = view(obj, limit=CAPTION)
        self.assertEqual(result["appendix"], "", "ürün satırı gövdedeki bağlantıyı keserdi; yazılmadı")
        self.assertEqual(result["keyboard"], [BUY[0], COUPON[0], *rc.SEARCH_ROW])
        self.assertEqual(plain["reach"] - result["reach"], set())
        # Buton satırları klavyeye bırakılınca gövde uzar: ürün satırı sığar, kenardaki bağlantı açık kalır.
        obj, plain = self.at_the_cut([*self.OTHERS, BUY], as_bot=True)
        result = view(obj, limit=CAPTION)
        self.assertEqual(result["appendix"], LABEL)
        self.assertEqual(result["keyboard"], [label for label, _ in self.OTHERS] + rc.SEARCH_ROW)
        self.assertGreater(len(result["composed"]["body"]), len(plain["body"]))
        self.assertEqual((source_links(obj, "button") | {key(self.HIDDEN)}) - result["reach"], set())
        # Dört uzun satır sınırı doldursa da botta ürün satırları yazılır; sığmayan buton klavyededir.
        obj = rc.message(BODY, buttons=[*(padded(button, 180) for button in self.OTHERS), *STORES[:3]])
        result = view(obj, limit=CAPTION)
        self.assertTrue(result["appendix"].startswith(numbered(3) + "\n🔗 "))
        self.assertFalse(result["body_cut"])
        self.assertEqual(result["keyboard"], [CHANNEL[0], *rc.SEARCH_ROW])
        self.assertEqual(source_links(obj) - result["reach"], set())

    def test_bot_product_line_does_not_cut_a_body_link_the_keyboard_layout_keeps(self):
        """Bot bildirimi: butonlar klavyede kalınca uzayan gövdedeki bağlantıyı ürün satırı da kesemez.

        Yalın düzen bu bağlantıyı açamıyordu (iki ham buton satırı gövdeyi daha erken kesiyordu);
        ölçü burada ürün satırsız düzendir: ürün butonu klavyede kalsaydı açılacak bağlantı açık kalır.
        """
        tucked = "https://t.me/kanal_govdedeki"  # ürün sayılmayan bir gövde bağlantısı
        cost = sanitize.utf16_length(LABEL) + 2  # ek yokken ürün satırı ve önündeki boş satır
        for past, written in ((0, False), (1 - cost, False), (-cost, True)):
            with self.subTest(past=past):
                # Butonsuz mesaj eksizdir: gövdeye kalan en geniş yer. ``past == 0``: bağlantı onun tam kenarında.
                bare, widest = self.at_the_cut([], as_bot=True, url=tucked, past=past)
                self.assertEqual(widest["appendix"], "")
                obj = rc.message(bare.message, links=[(self.MARK, tucked)], buttons=[BUY, COUPON])
                self.assertNotIn(key(tucked), plain_layout(obj, limit=CAPTION)["reach"],
                                 "yalın düzende buton satırları bu bağlantıyı kesiyordu")
                result = view(obj, limit=CAPTION)
                self.assertEqual(result["appendix"], LABEL if written else "")
                self.assertEqual(result["keyboard"], [*([] if written else [BUY[0]]), COUPON[0], *rc.SEARCH_ROW])
                # Ürün satırı yazıldıysa yerini gövde metninden alır (satır + ayraç);
                # yazılmadıysa gövde, ürün satırsız düzenle aynı yerde biter.
                with mock.patch.object(bot, "product_links", return_value=[]):
                    without = view(obj, limit=CAPTION)
                self.assertEqual(sanitize.utf16_length(without["composed"]["body"])
                                 - sanitize.utf16_length(result["composed"]["body"]),
                                 cost if written else 0, "ürün satırı yerini gövde metninden alır; yazılmazsa gövde tamdır")
                self.assertEqual(source_links(obj) - result["reach"], set())
                self.assertLessEqual(result["length"], CAPTION)

    def test_long_address_messages_never_close_a_link_the_plain_layout_opens(self):
        """Denetim betiğinin uzun adresli kümesinden ilk 120 mesaj: yalın düzenin açtığı her adres açık kalır.

        Ürün satırı yazılan iletilerde ayrıca: satırlar hiç yazılmasaydı gövdeden açılacak her bağlantı açıktır.
        Tam ölçüm (54.102 ileti, gerçek ``13ad981`` çıktısına karşı) ``tests/link_reach_audit.py`` iledir.
        """
        checked, with_product, closed, cut = 0, 0, [], []
        with mock.patch.object(appstate, "SMART_HEADER", True), audit.per_message_cache(bot) as cache:
            for spec in audit.random_messages(audit.SEED, 120, long_urls=True):
                obj = audit.build_message(types, spec)
                for mode in ("smart", "all", "off"):
                    for as_bot, limit in ((True, CAPTION), (True, TEXT), (False, CAPTION), (False, ACCOUNT_TEXT)):
                        where = (spec["id"], mode, as_bot, limit)
                        result = view(obj, mode, as_bot=as_bot, limit=limit)
                        plain = plain_layout(obj, mode, as_bot=as_bot, limit=limit)
                        self.assertLessEqual(result["length"], limit)
                        self.assertTrue(
                            result["composed"]["source_line"]
                            or result["composed"]["source_name_url"],
                            "mesaj linki yerinde (grup adına gömülü ya da kendi satırında)")
                        checked += len(plain["reach"])
                        closed += [(*where, url) for url in plain["reach"] - result["reach"]]
                        if "👉" not in result["appendix"]:
                            continue
                        with_product += 1
                        with mock.patch.object(bot, "product_links", return_value=[]):  # aynı ileti, ürün satırsız
                            body = view(obj, mode, as_bot=as_bot, limit=limit)["composed"]["body"]
                        hidden = botentities.entities_for_text(sanitize.sanitize_message(obj), body)
                        opened = sanitize.visible_urls(body) | {key(entity.url) for entity in hidden if getattr(entity, "url", None)}
                        # Yalnız ürün satırının YENİ açtığı fark ölçülür: ürün satırsız
                        # gövde, yalın düzenin (eski düzenin) zaten kestiği bağlantıyı
                        # kurtarabiliyorsa, ürün satırı yalnız o farkı kapatmamalıdır.
                        # Gövdenin kesildiği yeri eski düzen de kesiyordu (denetimde
                        # "yeni kayıp 0"), yani oradaki adres kaybı bu düzenin değil.
                        cut += [(*where, url) for url in ((opened & source_links(obj)) & plain["reach"]) - result["reach"]]
                cache.clear()
        self.assertGreater(checked, 5000)
        self.assertGreater(with_product, 400)
        self.assertEqual(closed, [], "yalın düzenin açtığı bağlantı kapandı")
        self.assertEqual(cut, [], "ürün satırı gövdedeki bir bağlantıyı kesti")

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
                            label = sanitize.utf16_slice(composed["text"], entity.offset, entity.length)
                            if label.startswith("👉 Ürün Linki"):
                                self.assertTrue(producttitle.is_product_link(url), url)
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

    def deliver(self, *, media: bool, buttons, bot_up: bool = True, text: str = TEXT,
                links=(("Google", GOOGLE_WEB[1]),)) -> tuple[list[dict], dict]:
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
                next(iter(appstate.SOURCE_IDS)), 999, text, message_id=message_id, media=media,
                entities=[rc.linked(text, label, url) for label, url in links],
                reply_markup=ti.FakeMarkup([ti.FakeRow([ti.FakeButton(label, url=url)]) for label, url in buttons]))

        async def scenario(client, queue) -> None:
            handler = client.handlers[1][1]
            # Yalıtım: önceki test modüllerinden kalan "görüldü" kayıtları bu iletileri yutmasın.
            for registry in (appstate.DEDUP_CACHE, appstate.LIVE_SEEN, appstate.POLL_DISPATCHED, appstate.SOURCE_IN_FLIGHT,
                             appstate.CURSOR_RAM):
                registry.clear()
            with mock.patch.object(appstate, "DEDUP_EDIT_MIN_INTERVAL", 0):
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
        units = sanitize.utf16_length(text)
        # Yardımcı çift (surrogate) ortasına düşen konum Telegram'da entity'yi bozar.
        boundaries = {sanitize.utf16_length(text[:index]) for index in range(len(text) + 1)}
        for entity in fields[entities_field]:
            self.assertGreaterEqual(entity["offset"], 0)
            self.assertGreater(entity["length"], 0)
            self.assertLessEqual(entity["offset"] + entity["length"], units, entity)
            self.assertIn(entity["offset"], boundaries, entity)
            self.assertIn(entity["offset"] + entity["length"], boundaries, entity)
        links = [entity for entity in fields[entities_field] if entity["type"] == "text_link"
                 and sanitize.utf16_slice(text, entity["offset"], entity["length"]).startswith("👉")]
        self.assertEqual(links, [{"type": "text_link", "offset": sanitize.utf16_length(text[:text.index(LABEL)]),
                                  "length": sanitize.utf16_length(LABEL), "url": url}])
        self.assertEqual(sanitize.utf16_slice(text, links[0]["offset"], links[0]["length"]), LABEL)
        # Ürün satırı artık üst satırın hemen altında; emoji gövdede kaldığı için
        # label'ın önünde astral karakter yoktur. Kural yukarıdaki eşitliktir:
        # konum UTF-16 birimidir (emoji önde olsaydı karakter sırasından ayrışırdı).
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
            self.assertLessEqual(sanitize.utf16_length(record["fields"]["caption"]), constants.CAPTION_LIMIT)
        self.assertTrue(edit["fields"]["caption"].endswith(self.BADGE))
        self.assertEqual(edit["fields"]["reply_markup"], send["fields"]["reply_markup"])

    def test_several_product_buttons_each_get_their_own_link(self):
        for media, text_field, entities_field in ((False, "text", "entities"), (True, "caption", "caption_entities")):
            records, _ = self.deliver(media=media, buttons=STORES[:6])
            self.assertEqual(len(records), 2)
            for record in records:
                fields = record["fields"]
                text = fields[text_field]
                found = [(sanitize.utf16_slice(text, entity["offset"], entity["length"]), entity["url"])
                         for entity in fields[entities_field] if entity["type"] == "text_link"]
                # Son iki kayıt: gövdedeki Google bağlantısı ve en alttaki grup adına
                # gömülü orijinal mesaj adresi (rozet düzenlemesinde de yerinde).
                self.assertEqual(found[-1][0], "firsatz")
                self.assertTrue(found[-1][1].startswith("https://t.me/"), found[-1][1])
                self.assertEqual(
                    [pair for pair in found if pair[0].startswith("👉 Ürün Linki")],
                    [(f"👉 Ürün Linki {number} 👈", PRODUCTS[number - 1]) for number in range(1, 5)])
                buttons = [button for row in fields["reply_markup"]["inline_keyboard"] for button in row]
                self.assertEqual([(button["text"], button["url"]) for button in buttons[:2]], STORES[4:6],
                                 "5. ve 6. ürün butonu klavyede kalır")

    def test_lines_left_out_at_the_caption_limit_reach_telegram_as_keyboard_buttons(self):
        """Fotoğraf açıklamasına sığmayan mesaj: yazılmayan ürün ve ``🔗`` satırlarının butonları istekteki klavyededir."""
        marks = [(f"Kanal {number:02}", f"https://t.me/kanal_duyuru/{number}") for number in range(1, 61)]
        text = "\n".join(["🔥🔥 Ateşler Kuruyemiş Badem İçi 150 gr 💥", "💰 106,26 TL", *LimitPressureTest.FILLER[:12],
                          *(label for label, _ in marks)])  # kırpma noktası neresi olursa olsun bir bağlantının üstünde
        (send, edit), _ = self.deliver(media=True, buttons=[BUY, COUPON], text=text, links=marks)
        self.assertEqual((send["method"], edit["method"]), ("sendPhoto", "editMessageCaption"))
        for record in (send, edit):
            fields = record["fields"]
            caption = fields["caption"]
            self.assertLessEqual(sanitize.utf16_length(caption), constants.CAPTION_LIMIT)
            self.assertNotIn(LABEL, caption, "ürün satırı gövdedeki bir bağlantıyı keserdi; yazılmaz")
            self.assertNotIn(COUPON[1], caption, "kupon satırı gövdeyi kırpmaz; yazılmaz")
            # Mesaj linki artık ``Mesajı Gör`` satırı değil: ayrı satır yok. Rozet
            # düzenlemesinde adres, nottaki grup adının içine gömülü kalır.
            self.assertNotIn("Mesajı Gör", caption)
            rows = [[(button["text"], button["url"]) for button in row] for row in fields["reply_markup"]["inline_keyboard"]]
            self.assertEqual(rows[:-1], [[BUY], [COUPON]], "yazılmayan satırların butonları kaynak sırasıyla klavyede")
            self.assertEqual([label for label, _ in rows[-1]], rc.SEARCH_ROW, "arama satırı tek satırda ve tam")
            hidden = [entity for entity in fields["caption_entities"] if entity["type"] == "text_link"]
            # Son entity, en alttaki grup adına gömülü orijinal mesaj adresidir.
            self.assertEqual(hidden[-1]["url"], "https://t.me/firsatz/7")
            self.assertEqual(
                [(sanitize.utf16_slice(caption, entity["offset"], entity["length"]), entity["url"])
                 for entity in hidden[:-1]], marks[:len(hidden) - 1],
                "gövdedeki bağlantılar kendi yazılarında")
            body_end = sanitize.utf16_length(caption[:caption.rindex("\n\n📌") if "\n\n📌" in caption
                                                   else len(caption)])
            self.assertLess(body_end - hidden[-1]["offset"] - hidden[-1]["length"], sanitize.utf16_length(LABEL) + 2,
                            "son bağlantı, ürün satırının alacağı yerin içinde bitiyor")
        self.assertEqual(edit["fields"]["reply_markup"], send["fields"]["reply_markup"])
        # Aynı mesaj fotoğrafsız gidince her şey sığar: iki satır da yazılır, klavyede yalnız arama satırı kalır.
        (send, _), _ = self.deliver(media=False, buttons=[BUY, COUPON], text=text, links=marks)
        self.assertIn(f"\n\n{LABEL}\n{line(*COUPON)}\n\n", send["fields"]["text"])
        self.assertEqual([[button["text"] for button in row] for row in send["fields"]["reply_markup"]["inline_keyboard"]],
                         [rc.SEARCH_ROW])

    def test_account_copy_carries_every_link_when_the_bot_is_rejected(self):
        """Bot API reddederse kopyayı kullanıcı hesabı atar: klavye yok, arama butonları da satırdır."""
        for media in (False, True):
            with self.subTest(media=media):
                records, account = self.deliver(media=media, buttons=self.BUTTONS, bot_up=False)
                self.assertTrue(records, "önce bot denenir")
                text = account["text"]
                hidden = [(sanitize.utf16_slice(text, entity.offset, entity.length), entity.url)
                          for entity in account["entities"] if isinstance(entity, types.MessageEntityTextUrl)]
                self.assertEqual(hidden[:2], [("Google", GOOGLE_WEB[1]), (LABEL, BUY[1])])
                self.assertEqual(hidden[2][0], "firsatz", "mesaj adresi grup adına gömülü")
                self.assertTrue(hidden[2][1].startswith("https://t.me/"), hidden[2][1])
                self.assertIn("\n\n" + "\n".join([LABEL, f"🔗 {GOOGLE_SHOPPING[1]}", line(*AKAKCE), f"🔗 {CIMRI[1]}",
                                                   line(*COUPON)]) + "\n\n", text)
                reach = sanitize.visible_urls(text) | {key(url) for _, url in hidden}
                self.assertEqual({key(url) for _, url in [*self.BUTTONS, GOOGLE_WEB]} - reach, set())


if __name__ == "__main__":
    unittest.main()
