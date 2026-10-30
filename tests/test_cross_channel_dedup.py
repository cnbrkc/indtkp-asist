"""Issue #26: aynı ürün/fiyat/kampanya farklı Telegram kanallarından gelince tek bildirim.

Kapsam (sade tutuldu):
- Ürün eşleşmesi: başlık yazımı, kaynak (t.me), WhatsApp tanıtımı ve arama düğmeleri
  ürün farkı sayılmaz; bilinen mağazalarda izleme parametreleri kimliği değiştirmez.
- Gerçek farklar birleşmez: varyant, fiyat düşüşü, kampanya, kupon, kondisyon (depo,
  açık kutu, kutusuz, outlet, 2. el), kargo, farklı ürün bağlantısı (P4).
- Fast-poll: aynı turdaki mesajlar tarihe göre sıralanır; hata olursa mesaj kaybolmaz.

Harness: gerçek ``on_new_message`` ve ``fast_poll_loop`` yolu, ``test_integration``
içindeki sahte I/O ile. Ağ erişimi yoktur.
"""
from __future__ import annotations

import asyncio
import datetime
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bot  # noqa: E402
import test_integration as ti  # noqa: E402
from telethon import utils  # noqa: E402
from telethon.tl import functions, types  # noqa: E402

FIRST_CHANNEL = "FırsatZ"
SECOND_CHANNEL = "Fırsat Tayfa"
SECOND_CHANNEL_ID = -1001111111112

BASE_TITLE = "Nivea Fresh Cherry Kadın Sprey Deodorant, 150 ml"
PRODUCT_LINK = "https://www.amazon.com.tr/dp/B0ABC12345"
NIVEA_LINK = "https://www.nivea.com.tr/urun/cherry-150ml"
WHATSAPP_FIRST = "💚Whatsapp Önemli Fırsatlar https://whatsapp.com/channel/firsatzKANAL"
WHATSAPP_SECOND = "💚Whatsapp Fırsat Tayfa https://whatsapp.com/channel/tayfaKANAL"
GOOGLE_FIRST = "https://www.google.com/search?q=nivea+cherry&tbm=shop"
GOOGLE_SECOND = "https://www.google.com.tr/search?q=nivea+fresh+cherry&tbm=shop"


def offer(title: str = BASE_TITLE, price: str = "86,67", campaign: str = "3 al 2 öde") -> str:
    """Issue'daki biçimde teklif gövdesi (kaynak satırı yok)."""
    return (
        f"₺86 — {title}\n\n"
        f"💰{price} TL\n\n"
        "🧮 Sepete 3 adet ekleyin, indirim ödeme ekranında uygulanır.\n\n"
        f"🎯 KAMPANYA: {campaign}"
    )


def with_lines(*lines: str, body: str | None = None) -> str:
    return "\n\n".join([body or offer(), *lines])


def amazon(tag: str | None = None, extra: str = "") -> str:
    query = "&".join(part for part in ([f"tag={tag}"] if tag else []) + ([extra] if extra else []))
    return f"https://www.amazon.com.tr/dp/B0ABC12345" + (f"?{query}" if query else "")


def _markup(urls: list[str] | None):
    if not urls:
        return None
    return SimpleNamespace(rows=[SimpleNamespace(buttons=[
        SimpleNamespace(text="Ara", url=url) for url in urls
    ])])


def _event(text: str, *, buttons: list[str] | None = None):
    return SimpleNamespace(message=text, raw_text=text, entities=[], media=None,
                           reply_markup=_markup(buttons))


class Harness:
    """Canlı yol ve fast-poll için ortak kurulum (``DedupBotBadgeTest`` harness'i ödünç alınır)."""

    setUp = ti.DedupBotBadgeTest.setUp
    tearDown = ti.DedupBotBadgeTest.tearDown
    _patch = ti.DedupBotBadgeTest._patch
    _run = ti.DedupBotBadgeTest._run

    def start(self, **config_extra):
        client = self._run(fast_notify=True, include_keywords=[], smart_header=True,
                           source_footer=True, **config_extra)
        first = next(iter(bot.SOURCE_IDS))
        bot.SOURCE_IDS.add(SECOND_CHANNEL_ID)
        bot.SOURCES[:] = [{"id": first, "name": FIRST_CHANNEL},
                          {"id": SECOND_CHANNEL_ID, "name": SECOND_CHANNEL}]
        self.first_id = first
        return client

    def offers(self, client, items):
        """``items``: (kanal_id, mesaj_id, metin[, buton_url'leri]) sırasıyla işlenir."""
        async def run():
            with mock.patch.object(bot, "DEDUP_EDIT_MIN_INTERVAL", 0):
                for item in items:
                    chat_id, message_id, text = item[:3]
                    buttons = item[3] if len(item) > 3 else None
                    event = ti.FakeEvent(chat_id, 7, text, message_id=message_id, media=False,
                                         reply_markup=_markup(buttons))
                    await client.handlers[1][1](event)
                    await asyncio.sleep(0.02)
        asyncio.run(run())

    def pings(self, first: str, second: str, first_buttons=None, second_buttons=None) -> int:
        """İki kaynak olayını taze bir çalıştırmada işle; gönderilen bildirim sayısı."""
        self.tearDown()
        self.setUp()
        client = self.start()
        self.offers(client, [
            (self.first_id, 101, first, first_buttons),
            (SECOND_CHANNEL_ID, 201, second, second_buttons),
        ])
        return len(self.ping_calls)

    def assertPings(self, first: str, second: str, expected: int, msg: str = "", **buttons) -> None:
        self.assertEqual(self.pings(first, second, **buttons), expected, msg)


class SameOfferTest(Harness, unittest.TestCase):
    """Aynı teklif, farklı kaynak/takip bağlantısı: tek bildirim."""

    def test_issue_example_two_channels_one_notification(self):
        client = self.start()
        self.offers(client, [
            (self.first_id, 101, with_lines("Kaynak mesaj: https://t.me/firsatz/130652")),
            (SECOND_CHANNEL_ID, 201, with_lines("Kaynak mesaj: https://t.me/firsattayfa/28513")),
        ])
        self.assertEqual(len(self.ping_calls), 1, "ikinci kanal yeni bildirim üretmemeli")
        self.assertEqual(bot.STATS["deduped"], 1)
        text = self.edit_text_calls[-1]["text"]
        self.assertIn("2 kere paylaşıldı", text)
        self.assertIn(FIRST_CHANNEL, text)
        self.assertIn(SECOND_CHANNEL, text)

    def test_homend_same_asin_psc_and_button_different_channels(self):
        # Kaynakların tam Telegram entity/önizleme verisi yok; metinler paylaşılan
        # bildirimlerden yeniden oluşturuldu, iki ürün URL'si kullanıcıdan alındı.
        first_url = "https://www.amazon.com.tr/dp/B0DGLLJP1M?psc=1&tag=indirimalarmi-21"
        second_url = "https://www.amazon.com.tr/dp/B0DGLLJP1M?tag=indirimgeldi-21"
        first = ("🛍️ Homend Profashion 3023H Stand Mikser Antrasit\n\n"
                 "💰 Fiyat : 6619 TL\n🗓️ 365 Günün En Düşük Fiyatı\n\n" + first_url)
        second = ("✅ Homend Profashion 3023H Stand Mikser Antrasit\n\n"
                  "🏷 6.619 TL\n\n👉 FIRSATA GİT!\n\n🛍 Amazon")
        self.assertPings(first, second, 1, second_buttons=[second_url])
        self.assertEqual(bot.STATS["deduped"], 1)

    def test_source_promo_search_and_utm_differences_one_notification(self):
        cases = {
            "kanal tanıtımı (WhatsApp) farklı": (with_lines(WHATSAPP_FIRST), with_lines(WHATSAPP_SECOND), {}),
            "arama düğmesi farklı": (offer(), offer(), dict(first_buttons=[GOOGLE_FIRST],
                                                          second_buttons=[GOOGLE_SECOND])),
            "yalnız utm farklı": (with_lines(NIVEA_LINK + "?utm_source=firsatz"),
                                  with_lines(NIVEA_LINK + "?utm_source=tayfa&utm_medium=kanal"), {}),
            "ürün linki yalnız bir tarafta": (with_lines(NIVEA_LINK), offer(), {}),
        }
        for name, (first, second, buttons) in cases.items():
            with self.subTest(name):
                self.assertPings(first, second, 1, **buttons)

    def test_title_spelling_variants_one_notification(self):
        for title in [
            "NİVEA Fresh-Cherry kadın sprey deodorant 150 ml",
            "Kadın Sprey Deodorant Nivea Fresh Cherry, 150 ml!",
            "Nivea Fresh Cherry Kadın Sprey Deodorant 150ml",
        ]:
            with self.subTest(title=title):
                self.assertPings(offer(), offer(title), 1)

    def test_amazon_same_asin_tag_and_path_variants_one_notification(self):
        self.assertPings(with_lines(amazon("firsatz-21")), with_lines(amazon("tayfa-21")), 1)
        self.assertPings(
            with_lines("https://www.amazon.com.tr/dp/B0ABC12345?utm_source=firsatz"),
            with_lines("https://www.amazon.com.tr/gp/product/B0ABC12345?linkCode=ll1&tag=a-21"), 1)
        # Belirsiz arama/gezinme parametreleri (qid, sr, pd_rd_*) kimlikte kalır: resmî
        # kaynakta ürün bilgisi taşımadığı doğrulanmadı. Bu bağlantı ayrı kalır (P4).
        self.assertPings(
            with_lines("https://www.amazon.com.tr/dp/B0ABC12345/ref=sr_1_1?qid=1&sr=8-1&pd_rd_r=x"),
            with_lines(amazon()), 2, "belirsiz gezinme parametresi kimlikte kalır")

    def test_restart_history_and_second_channel_share_one_entry(self):
        # Yeniden başlatmada önceki bildirim geçmişten yüklenir; ikinci kanal aynı kaydı kullanmalı.
        history = (
            f"₺86 — {BASE_TITLE}\n\n"
            "💰86,67 TL\n\n"
            "🧮 Sepete 3 adet ekleyin, indirim ödeme ekranında uygulanır.\n\n"
            "🎯 KAMPANYA: 3 al 2 öde\n\n"
            "🔗 Mesajı Gör: https://t.me/firsatz/130652\n\n"
            f"{FIRST_CHANNEL}"
        )
        ti.FakeClient.default_history = [(ti.GROUP_ID, history, 123, None, None, [], 900)]
        client = self.start()
        self.offers(client, [(SECOND_CHANNEL_ID, 201, with_lines(
            "Kaynak mesaj: https://t.me/firsattayfa/28513"))])
        self.assertEqual(len(self.ping_calls), 0, "geçmiş kaydı ikinci kanalı yutmalı")
        self.assertEqual(bot.STATS["deduped"], 1)


class DifferentOfferTest(Harness, unittest.TestCase):
    """Gerçek fark: varyant, fiyat, kampanya, kondisyon, kargo, ürün bağlantısı. Birleşmez."""

    def test_variant_or_different_asin_is_new_notification(self):
        self.assertPings(offer(), offer("Nivea Fresh Cherry Kadın Sprey Deodorant, 250 ml"), 2)
        self.assertPings(with_lines("https://www.amazon.com.tr/dp/B0ABC12345"),
                         with_lines("https://www.amazon.com.tr/dp/B0ZZZ99999"), 2)
        self.assertPings(with_lines(amazon(extra="th=1")), with_lines(amazon()), 2,
                         "varyant parametresi kimliktedir")
        self.assertPings(with_lines(amazon(extra="smid=A1XYZ")), with_lines(amazon()), 2,
                         "satıcı parametresi kimliktedir")

    def test_psc_difference_does_not_hide_real_offer_differences(self):
        first = with_lines(amazon(extra="psc=1"))
        plain_link = amazon()
        self.assertPings(first, with_lines(plain_link, body=offer(
            "Nivea Fresh Cherry Kadın Sprey Deodorant, 250 ml")), 2)
        self.assertPings(first, with_lines("Açık kutu ürün", plain_link), 2)
        self.assertPings(first, with_lines(plain_link, body=offer(price="79,90")), 2)
        self.assertPings(first, with_lines(
            "https://www.amazon.com.tr/dp/B0ZZZ99999"), 2)

    def test_campaign_and_coupon_difference_is_new_notification(self):
        self.assertPings(offer(), offer(campaign="2 al 1 öde"), 2)
        self.assertPings(offer(), offer() + "\n\nKupon: CAY10", 2)

    def test_real_price_drop_is_new_notification(self):
        self.assertPings(offer(), offer(price="79,90"), 2)
        self.assertPings(with_lines(amazon("firsatz-21")),
                         with_lines(amazon("tayfa-21"), body=offer(price="79,90")), 2,
                         "tag farklıyken gerçek düşüş")

    def test_condition_one_side_is_new_notification(self):
        # Kondisyon yalnız bir mesajda: aynı link olsa da (tag farklı olsa da) ayrı bildirim.
        for line in ["Amazon Depo", "Amazon Warehouse", "Açık kutu ürün", "Kutusuz",
                     "Outlet fiyatı", "2. el", "İkinci el"]:
            with self.subTest(condition=line):
                self.assertPings(with_lines(line, PRODUCT_LINK), with_lines(PRODUCT_LINK), 2)
        self.assertPings(with_lines("Amazon Depo", amazon("firsatz-21")),
                         with_lines(amazon("tayfa-21")), 2, "tag farklı + kondisyon tek taraf")

    def test_same_condition_on_both_sides_one_notification(self):
        self.assertPings(with_lines("Amazon Depo", amazon("firsatz-21")),
                         with_lines("Amazon Depo", amazon("tayfa-21")), 1)

    def test_shipping_difference_is_new_notification_same_shipping_merges(self):
        self.assertPings(with_lines("Kargo ücretsiz", PRODUCT_LINK),
                         with_lines("Kargo 29,90 TL", PRODUCT_LINK), 2)
        self.assertPings(with_lines("Kargo bedava", NIVEA_LINK),
                         with_lines("Ücretsiz kargo", NIVEA_LINK), 1, "aynı kargo, farklı yazım")

    def test_different_product_links_stay_separate_p4(self):
        cases = {
            "ref farklı (genel parametre)": (NIVEA_LINK + "?ref=firsatz", NIVEA_LINK + "?ref=tayfa"),
            "parametre yalnız bir tarafta": (NIVEA_LINK, NIVEA_LINK + "?ref=tayfa"),
            "farklı kısa linkler": ("https://amzn.to/3abcXYZ", "https://amzn.to/3defUVW"),
            "farklı mağaza": (NIVEA_LINK, "https://www.trendyol.com/nivea/cherry-150ml-p-123456"),
            "aynı ASIN, farklı Amazon alan adı": (PRODUCT_LINK, "https://www.amazon.de/dp/B0ABC12345"),
        }
        for name, (first, second) in cases.items():
            with self.subTest(name):
                self.assertPings(with_lines(first), with_lines(second), 2)

    def test_cross_store_with_promo_and_search_buttons_stays_separate_p4(self):
        self.assertPings(with_lines(PRODUCT_LINK + " " + WHATSAPP_FIRST.replace("💚", "")),
                         with_lines("https://www.trendyol.com/nivea/x-p-123456 "
                                    + WHATSAPP_SECOND.replace("💚", "")),
                         2, first_buttons=[GOOGLE_FIRST], second_buttons=[GOOGLE_SECOND])

    def test_amazon_tag_whatsapp_and_google_together_one_notification(self):
        self.assertPings(with_lines(amazon("firsatz-21"), WHATSAPP_FIRST),
                         with_lines(amazon("tayfa-21"), WHATSAPP_SECOND), 1,
                         first_buttons=[GOOGLE_FIRST], second_buttons=[GOOGLE_SECOND])

    def test_seller_name_is_policy_not_identity(self):
        # Politika (kullanıcı onayı bekliyor): satıcı adı kimlik/kondisyon sayılmaz.
        self.assertPings(with_lines("Satıcı: Amazon", PRODUCT_LINK),
                         with_lines(PRODUCT_LINK), 1)
        self.assertPings(with_lines("Satıcı: Amazon", amazon("firsatz-21")),
                         with_lines("Satıcı: XYZ", amazon("tayfa-21")), 1,
                         "bilinen risk: satıcı + tag farkı birleşir")



class CampaignPriceDedupTest(Harness, unittest.TestCase):
    """Kampanya tutarı (ürün fiyatı değil) + WhatsApp/Google düğmeleri birlikte.

    Kampanya satırı fiyatı değiştirmez: aynı ürün ve gerçek fiyat tek bildirim olur;
    gerçek fiyat farkı ve belirsiz (❓) fiyat yeni bildirim kalır.
    """

    @staticmethod
    def body(price: str, campaign: str = "🎯 2. ürün 1 TL") -> str:
        return f"{BASE_TITLE}\n🏷 {price} TL\n{campaign}"

    def test_campaign_line_with_whatsapp_and_google_one_notification(self):
        self.assertPings(with_lines(WHATSAPP_FIRST, body=self.body("86,67")),
                         with_lines(WHATSAPP_SECOND, body=self.body("86,67", "🎯 2. ürün 1₺")), 1,
                         first_buttons=[GOOGLE_FIRST], second_buttons=[GOOGLE_SECOND])

    def test_real_price_drop_with_campaign_and_buttons_stays_separate(self):
        self.assertPings(with_lines(WHATSAPP_FIRST, body=self.body("86,67")),
                         with_lines(WHATSAPP_SECOND, body=self.body("49,90")), 2,
                         first_buttons=[GOOGLE_FIRST], second_buttons=[GOOGLE_SECOND])

    def test_unknown_price_first_then_real_price_stays_separate(self):
        # Belirsiz (❓) ilk mesaj, sonra kesin fiyat: kesin fiyat yeni bildirim olmalı.
        self.assertPings(with_lines(WHATSAPP_FIRST, body=f"{BASE_TITLE}\n1 TL 2. ürün 49,90 TL"),
                         with_lines(WHATSAPP_SECOND, body=self.body("86,67")), 2,
                         first_buttons=[GOOGLE_FIRST], second_buttons=[GOOGLE_SECOND])


class ConditionMarkerTest(unittest.TestCase):
    """Kondisyon/kargo işaretleri yalnızca kısa, ilgili satırlardan çıkarılır."""

    def test_short_lines_yield_condition_markers(self):
        for line, marker in {
            "📦 Amazon Depo ürünü": "durum:depo",
            "Amazon Warehouse": "durum:depo",
            "Açık kutu ürün": "durum:açık kutu",
            "Kutusuz": "durum:kutusuz",
            "Outlet fiyatı": "durum:outlet",
            "2. el": "durum:ikinci el",
        }.items():
            with self.subTest(line=line):
                self.assertIn(marker, bot._offer_marker_terms(line))

    def test_long_sentences_urls_and_seller_lines_are_not_sources(self):
        for text in [
            "Bu ürünü depo stoklarımızdan hızlıca gönderiyoruz ve kargo ücretsizdir",
            "https://www.depo.example/urun?kutusuz=1",
            "Satıcı: Amazon Depo",
        ]:
            with self.subTest(text=text):
                self.assertEqual([m for m in bot._offer_marker_terms(text) if m.startswith("durum:")], [])

    def test_shipping_markers_and_plain_offer(self):
        self.assertEqual(bot._offer_marker_terms("Kargo bedava"), bot._offer_marker_terms("Ücretsiz kargo"))
        self.assertNotEqual(bot._offer_marker_terms("Kargo 29,90 TL"), bot._offer_marker_terms("Kargo bedava"))
        self.assertEqual(bot._offer_marker_terms(offer()), [])


class LinkIdentityTest(unittest.TestCase):
    """Ürün kimliği yalnızca ürün bağlantılarını taşır (P4 güvenliği dahil)."""

    def keys(self, text: str) -> frozenset[str]:
        return bot.product_link_keys(_event(text))

    def test_source_whatsapp_and_search_links_are_not_identity(self):
        for url in ["https://t.me/firsatz/130652", "https://whatsapp.com/channel/firsatzKANAL",
                    "https://chat.whatsapp.com/abc", "https://wa.link/firsatz",
                    "https://www.google.com.tr/search?q=nivea", "https://tr.shopping.google.com/search?q=nivea",
                    "https://www.google.com./search?q=nivea", "https://www.akakce.com/ara/?q=cay",
                    "https://www.cimri.com/arama?q=cay"]:
            with self.subTest(url=url):
                self.assertEqual(self.keys(f"Çay 5 TL {url}"), frozenset())

    def test_lookalike_hosts_are_product_links(self):
        for url in ["https://google.evil.example/urun", "https://google.com.attacker.net/urun",
                    "https://mygoogle.com/urun", "https://notakakce.com/urun",
                    "https://whatsapp.com.evil.example/x", "https://notwhatsapp.com/x"]:
            with self.subTest(url=url):
                self.assertTrue(self.keys(f"Çay 5 TL {url}"))

    def test_amazon_identity_needs_exact_host_and_uppercase_asin(self):
        self.assertEqual(self.keys("Çay 5 TL https://m.amazon.com.tr/dp/B0ABC12345?tag=x"),
                         frozenset({"amazon.com.tr#B0ABC12345"}))
        for url in ["https://amazon.com.tr.evil.example/dp/B0ABC12345",
                    "https://notamazon.com.tr/dp/B0ABC12345",
                    "https://smile.amazon.com.tr/dp/B0ABC12345",
                    "https://www.amazon.de/dp/B0ABC12345",
                    "https://amazon.com.tr/dp/B0ABC1234",     # geçersiz ASIN uzunluğu
                    "https://amazon.com.tr/dp/b0abc12345"]:   # küçük harf: kural yok (fail-open)
            with self.subTest(url=url):
                self.assertFalse(any("#" in key for key in self.keys(f"Çay 5 TL {url}")))

    def test_amazon_only_psc_one_is_ignored_other_parameters_remain(self):
        base = "https://www.amazon.com.tr/dp/B0DGLLJP1M"
        key = self.keys(base)
        self.assertEqual(self.keys(base + "?psc=1&tag=indirimalarmi-21"), key)
        for query in ("psc=0", "psc=2", "psc=", "psc=1&th=1", "psc=1&smid=A1"):
            with self.subTest(query=query):
                self.assertTrue(bot._link_conflict(key, self.keys(base + "?" + query)))
        self.assertTrue(bot._link_conflict(key, self.keys(
            "https://www.amazon.com.tr/dp/B0ZZZ99999?psc=1")))

    def test_real_product_links_keep_their_params(self):
        keys = self.keys("Çay 5 TL https://www.nivea.com.tr/urun?sku=1 https://t.me/firsatz/1")
        self.assertEqual(keys, frozenset({"www.nivea.com.tr/urun?sku=1"}))


class FastPollTest(Harness, unittest.TestCase):
    """Hızlı tarama (getPeerDialogs) canlı yolla aynı sonucu verir; sıra ve kayıp korunur."""

    CHANNELS = {"@firsatz": 1111111111, "@firsattayfa": 1111111112}

    def poll_pair(self, first: str, second: str, first_age: float = 2.0, second_age: float = 1.0) -> int:
        """İki mesajı fast-poll ile işle (ilk tur başlangıç, ikinci turda yeni mesajlar).

        ``first_age``/``second_age``: saniye cinsinden yaş (büyük = daha eski).
        """
        self.tearDown()
        self.setUp()
        test = self
        self.polls = 0
        texts = {self.CHANNELS["@firsatz"]: first, self.CHANNELS["@firsattayfa"]: second}
        original_get_entity = ti.FakeClient.get_entity

        async def get_entity(client_self, value):
            if value in self.CHANNELS:
                cid = self.CHANNELS[value]
                return types.Channel(id=cid, title=value.lstrip("@"), photo=types.ChatPhotoEmpty(),
                                     date=0, username=value.lstrip("@"))
            return await original_get_entity(client_self, value)

        async def get_input_entity(client_self, chat_id):
            return types.InputPeerChannel(channel_id=abs(chat_id) % 10**10, access_hash=1)

        async def call(client_self, request):
            if not isinstance(request, functions.messages.GetPeerDialogsRequest):
                return SimpleNamespace(dialogs=[])
            test.polls += 1
            top = 10 if test.polls == 1 else 11
            dialogs = [SimpleNamespace(peer=utils.get_peer(-1000000000000 - cid), top_message=top)
                       for cid in self.CHANNELS.values()]
            return SimpleNamespace(dialogs=dialogs)

        now = datetime.datetime.now(datetime.timezone.utc)
        dates = {self.CHANNELS["@firsatz"]: now - datetime.timedelta(seconds=first_age),
                 self.CHANNELS["@firsattayfa"]: now - datetime.timedelta(seconds=second_age)}

        async def get_messages(client_self, peer, min_id=0, limit=None):
            if test.polls < 2:
                return []
            return [types.Message(id=11, peer_id=types.PeerChannel(peer.channel_id), post=True,
                                  date=dates[peer.channel_id], message=texts[peer.channel_id])]

        async def run_until_disconnected(client_self):
            await asyncio.sleep(3.0)  # ~1 sn aralıkla iki yoklama turu

        patches = [
            mock.patch.object(ti.FakeClient, "get_entity", get_entity),
            mock.patch.object(ti.FakeClient, "get_input_entity", get_input_entity, create=True),
            mock.patch.object(ti.FakeClient, "__call__", call, create=True),
            mock.patch.object(ti.FakeClient, "get_messages", get_messages, create=True),
            mock.patch.object(ti.FakeClient, "_mb_entity_cache",
                              SimpleNamespace(get=lambda *a, **k: None), create=True),
            mock.patch.object(ti.FakeClient, "run_until_disconnected", run_until_disconnected),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self._run(fast_poll=True, fast_poll_seconds=1,
                  source_chats=["@firsatz", "@firsattayfa"], include_keywords=[],
                  smart_header=True, source_footer=True, fast_notify=True)
        self.assertGreaterEqual(self.polls, 2, "iki yoklama turu tamamlanmalı")
        return len(self.ping_calls)

    def test_poll_and_live_paths_agree(self):
        # Yalnız sıra-bağımsız durumlar. Aynı turda gelen fiyat düşüşü (sıraya bağlı)
        # ayrı değişiklik olarak değerlendirilecek; bu dosyada yok.
        cases = {
            "aynı teklif, farklı kaynak": (with_lines("Kaynak mesaj: https://t.me/firsatz/1"),
                                           with_lines("Kaynak mesaj: https://t.me/firsattayfa/2"), 1),
            "farklı ürün bağlantısı": (with_lines(NIVEA_LINK), with_lines("https://www.trendyol.com/x-p-1"), 2),
        }
        for name, (first, second, expected) in cases.items():
            with self.subTest(name):
                live = Harness.pings(self, first, second)
                self.assertEqual(live, expected, f"canlı yol: {name}")
                self.assertEqual(self.poll_pair(first, second), expected, f"fast-poll: {name}")


if __name__ == "__main__":
    unittest.main()
