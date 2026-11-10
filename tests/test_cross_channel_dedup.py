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
        # Farklı Amazon URL parametreleri aynı başlık/fiyattaki ürünü bölmez.
        self.assertPings(
            with_lines("https://www.amazon.com.tr/dp/B0ABC12345/ref=sr_1_1?qid=1&sr=8-1&pd_rd_r=x"),
            with_lines(amazon()), 1, "URL parametreleri kimlik değildir")

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
    """Başlık ve fiyat belirler; koşul ve URL farkları ürün kimliği değildir."""

    def test_title_variant_and_real_price_drop_are_new_notifications(self):
        self.assertPings(offer(), offer("Nivea Fresh Cherry Kadın Sprey Deodorant, 250 ml"), 2)
        self.assertPings(offer(), offer(price="79,90"), 2)

    def test_campaign_coupon_shipping_and_condition_do_not_change_identity(self):
        variants = [
            offer(campaign="2 al 1 öde"), offer() + "\n\nKupon: CAY10",
            with_lines("Kargo ücretsiz"), with_lines("Kargo 29,90 TL"),
            with_lines("Amazon Depo"), with_lines("Açık kutu ürün"),
        ]
        for second in variants:
            with self.subTest(second=second):
                self.assertPings(offer(), second, 1)

    def test_different_product_links_do_not_split_same_title(self):
        links = [PRODUCT_LINK, "https://www.trendyol.com/nivea/cherry-150ml-p-123456",
                 "https://www.amazon.de/dp/B0ABC12345", NIVEA_LINK + "?sku=150A",
                 NIVEA_LINK + "?sku=150B", "https://amzn.to/3abcXYZ", "https://amzn.to/3defUVW"]
        for url in links:
            with self.subTest(url=url):
                self.assertPings(with_lines(PRODUCT_LINK), with_lines(url), 1)

    def test_link_difference_is_ignored_in_preload_and_fast_poll(self):
        self.assertPings(with_lines(PRODUCT_LINK), with_lines("https://ty.gl/other"), 1)

    def test_source_promo_and_search_buttons_do_not_split_product(self):
        self.assertPings(
            with_lines(PRODUCT_LINK + " " + WHATSAPP_FIRST.replace("💚", "")),
            with_lines("https://www.trendyol.com/nivea/x-p-123456 " + WHATSAPP_SECOND.replace("💚", "")),
            1, first_buttons=[GOOGLE_FIRST], second_buttons=[GOOGLE_SECOND],
        )

    def test_unknown_price_then_known_price_remains_a_new_notification(self):
        self.assertPings(
            with_lines(WHATSAPP_FIRST, body=f"{BASE_TITLE}\n1 TL 2. ürün 49,90 TL"),
            with_lines(WHATSAPP_SECOND, body=offer()), 2,
            first_buttons=[GOOGLE_FIRST], second_buttons=[GOOGLE_SECOND],
        )

    def test_same_seller_metadata_merges(self):
        self.assertPings(with_lines("Satıcı: Amazon", PRODUCT_LINK), with_lines(PRODUCT_LINK), 1)
        self.assertPings(with_lines("Satıcı: Amazon", amazon("firsatz-21")),
                         with_lines("Satıcı: XYZ", amazon("tayfa-21")), 1)


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


class TitlePriceOnlyIdentityTest(Harness, unittest.TestCase):
    def test_same_title_and_price_merge_across_conditions_and_links(self):
        self.assertPings(
            with_lines("Kargo ücretsiz", PRODUCT_LINK),
            with_lines("İkinci El: Yeni Gibi", "Kupon: TEST", "https://magaza.example/urun"),
            1,
        )

    def test_different_title_or_price_still_matters(self):
        self.assertPings(offer(), offer("Nivea Fresh Cherry 250 ml"), 2)
        self.assertPings(offer(), offer(price="79,90"), 2)


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
            "farklı ürün bağlantısı": (with_lines(NIVEA_LINK), with_lines("https://www.trendyol.com/x-p-1"), 1),
        }
        for name, (first, second, expected) in cases.items():
            with self.subTest(name):
                live = Harness.pings(self, first, second)
                self.assertEqual(live, expected, f"canlı yol: {name}")
                self.assertEqual(self.poll_pair(first, second), expected, f"fast-poll: {name}")


if __name__ == "__main__":
    unittest.main()


class ReportedMultibuyUpgradeTest(Harness, unittest.TestCase):
    def test_promotes_recent_same_product_and_moves_badge_before_deleting_old(self):
        client = self.start()
        deletes = []
        async def delete(token, chat, message_id):
            self.assertTrue(self.edit_text_calls, 'must badge new message before deleting')
            deletes.append(message_id)
            return True, 'ok'
        self._patch('delete_bot_message', delete)
        title = 'Dr. Oetker Revani 3 Kg'
        self.offers(client, [
            (self.first_id, 101, title + '\n₺291,90'),
            (SECOND_CHANNEL_ID, 201, title + '\n₺291,90'),
            (self.first_id, 102, '🛍️ Dr. Oetker Revani 3 kg\n💰 Fiyat : 291 TL / 4 adet alımda 262 TL'),
        ])
        self.assertEqual(len(self.ping_calls), 2)
        self.assertEqual(deletes, [self.ping_calls[0]['message_id']])
        self.assertEqual(self.edit_text_calls[-1]['message_id'], self.ping_calls[1]['message_id'])
        self.assertIn('3 kere paylaşıldı', self.edit_text_calls[-1]['text'])
        self.assertTrue(self.ping_calls[-1]['text'].startswith('₺262 —'))

    def test_water_same_base_discount_is_promoted(self):
        client = self.start()
        deletes = []
        async def delete(token, chat, message_id):
            deletes.append(message_id)
            return True, 'ok'
        self._patch('delete_bot_message', delete)
        title = 'Hayat Doğal Mineralli Su 6x1,5 L'
        self.offers(client, [(self.first_id, 101, title + '\n🏷️ 79 TL'),
                             (SECOND_CHANNEL_ID, 201, f'🛍️ {title}\n💰 Fiyat : 79 TL / 13 adet alımda 71 TL')])
        self.assertEqual(len(self.ping_calls), 2)
        self.assertEqual(deletes, [self.ping_calls[0]['message_id']])
        self.assertIn('2 kere paylaşıldı', self.edit_text_calls[-1]['text'])

    def test_real_drop_without_base_stays_separate(self):
        client = self.start()
        deletes = []
        async def delete(*args):
            deletes.append(args)
            return True, 'ok'
        self._patch('delete_bot_message', delete)
        self.offers(client, [(self.first_id, 101, 'Dr. Oetker Revani 3 Kg\n291 TL'),
                             (SECOND_CHANNEL_ID, 201, 'Dr. Oetker Revani 3 Kg\nFiyat: 260 TL')])
        self.assertEqual(len(self.ping_calls), 2)
        self.assertFalse(deletes)

    def test_badge_failure_keeps_old_message(self):
        client = self.start()
        deletes = []
        async def delete(*args):
            deletes.append(args)
            return True, 'ok'
        async def fail_edit(*args, **kwargs):
            return False, 'unavailable'
        self._patch('delete_bot_message', delete)
        title = 'Hayat Doğal Mineralli Su 6x1,5 L'
        with mock.patch.object(bot, 'edit_bot_text', fail_edit):
            self.offers(client, [(self.first_id, 101, title + '\n🏷 79 TL'),
                                 (SECOND_CHANNEL_ID, 201, f'{title}\nFiyat: 79 TL / 13 adet alımda 71 TL')])
        self.assertEqual(len(self.ping_calls), 2)
        self.assertFalse(deletes)

    def test_stale_first_and_wrong_base_do_not_delete(self):
        client = self.start()
        deletes = []
        async def delete(*args):
            deletes.append(args)
            return True, 'ok'
        self._patch('delete_bot_message', delete)
        title = 'Hayat Doğal Mineralli Su 6x1,5 L'
        self.offers(client, [(self.first_id, 101, title + '\n🏷 79 TL')])
        entry = next(iter(bot.DEDUP_CACHE.values()))
        entry['first_seen'] -= 181
        self.offers(client, [(SECOND_CHANNEL_ID, 201, f'{title}\nFiyat: 79 TL / 13 adet alımda 71 TL')])
        self.assertEqual(len(self.ping_calls), 2)
        self.assertFalse(deletes)

    def test_send_failure_preserves_previous_cache_and_message(self):
        client = self.start()
        title = 'Hayat Doğal Mineralli Su 6x1,5 L'
        self.offers(client, [(self.first_id, 101, title + '\n🏷 79 TL')])
        old = next(iter(bot.DEDUP_CACHE.values()))
        async def fail_send(*args, **kwargs):
            return bot.BotSendResult(False, 'failed')
        deletes = []
        async def delete(*args):
            deletes.append(args)
            return True, 'ok'
        self._patch('delete_bot_message', delete)
        with mock.patch.object(bot, 'send_bot_ping', fail_send):
            self.offers(client, [(SECOND_CHANNEL_ID, 201, f'{title}\nFiyat: 79 TL / 13 adet alımda 71 TL')])
        self.assertIs(next(iter(bot.DEDUP_CACHE.values())), old)
        self.assertFalse(deletes)

    def test_non_content_teaser_skipped_but_media_and_links_retained(self):
        client = self.start()
        self.offers(client, [(self.first_id, 101, 'Yoğun ilgi görüyor'),
                             (SECOND_CHANNEL_ID, 201, 'Yoğun ilgi görüyor\nhttps://example.com/urun')])
        self.assertEqual(len(self.ping_calls), 1)
        async def run_photo():
            event = ti.FakeEvent(self.first_id, 7, 'Yoğun ilgi görüyor', message_id=102, media=True)
            await client.handlers[1][1](event)
        asyncio.run(run_photo())
        self.assertEqual(len(self.media_calls), 1)

    def test_different_asin_or_used_condition_are_not_upgraded(self):
        client = self.start()
        deletes = []
        async def delete(*args):
            deletes.append(args)
            return True, 'ok'
        self._patch('delete_bot_message', delete)
        title = 'Hayat Doğal Mineralli Su 6x1,5 L'
        self.offers(client, [(self.first_id, 101, f'{title}\n🏷 79 TL\nhttps://amazon.com/dp/B09RMSLVS8'),
                             (SECOND_CHANNEL_ID, 201, f'{title}\nFiyat: 79 TL / 13 adet alımda 71 TL\nhttps://amazon.com/dp/B0ABC12345')])
        self.assertFalse(deletes)
        self.assertEqual(len(self.ping_calls), 2)
