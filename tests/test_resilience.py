"""Son dayanıklılık denetimi: üretim tüketicileri ve gerçek handler zinciri."""
import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import bot
import price_filter as pf
from test_integration import FakeClient, FakeEvent, MainHarness, reset_state


class IncompletePriceTests(unittest.TestCase):
    def assert_untrusted(self, text):
        event = SimpleNamespace(message=text, raw_text=text, entities=[], media=None)
        info = pf.extract_price(text)
        self.assertNotIn(info.confidence, {'medium', 'high'})
        self.assertFalse(pf.price_matches(text, 99999)[0])
        self.assertFalse(bot.dm_should_send({'dm_price_max': ['99999']}, text))
        self.assertIsNone(bot.offer_terms(event)[0])
        with patch.object(bot, 'SMART_HEADER', True):
            self.assertIn(bot.PRICE_NOT_FOUND_TEXT, bot.compose_message(event)['text'])

    def test_malformed_number_prefix_is_not_a_price(self):
        for text in ['Fiyat: 1..299 TL', 'Fiyat: 1,2,3 TL', '₺1..299',
                     'TL 1,2,3', 'Fiyat: ₺1..299']:
            with self.subTest(text=text):
                self.assertIsNone(pf.extract_price(text).value)
                self.assert_untrusted('Pamuk Havlu 30x40 Cm\n' + text)

    def test_additional_invalid_number_boundaries(self):
        for number in ['1,,299', '1.,299', '1,.299', '12.34.56', '12,34,56', '1299..90']:
            for template in ['Fiyat: {} TL', 'Fiyat: {}', '₺{}', 'TL {}', '{} TL']:
                text = template.format(number)
                with self.subTest(text=text):
                    self.assertIsNone(pf.extract_price(text).value)
                    self.assert_untrusted('Pamuk Havlu\n' + text)

    def test_valid_turkish_formats_still_reach_consumers(self):
        for number, value in [('99,90', 99.9), ('1.299', 1299), ('1.299,90', 1299.9),
                              ('1.234.567,90', 1234567.9), ('1\u00a0299,90', 1299.9),
                              ('1\u202f299,90', 1299.9)]:
            for template in ['Fiyat: {} TL', '₺{}', '{}₺', 'Fiyat: {}']:
                text = 'Pamuk Havlu\n' + template.format(number)
                with self.subTest(text=text):
                    info = pf.extract_price(text)
                    self.assertEqual(info.value, value)
                    self.assertIn(info.confidence, {'medium', 'high'})
                    self.assertTrue(pf.price_matches(text, value)[0])
                    self.assertFalse(pf.price_matches(text, value - 0.01)[0])
                    self.assertEqual(bot.offer_terms(text)[0], value)

    def test_coupon_newlines_indentation_and_explanations(self):
        for newline in ['\n', '\r\n', '\n\n', '\r\n\r\n']:
            for explanation in ['', ' (ürün altından etkinleştirin)', ' — son gün',
                                newline + 'Açıklama: Sepette uygulayın']:
                terms = []
                for code in ['KOD10', 'KOD20']:
                    text = ('Pamuk Havlu' + newline + 'Fiyat: 300 TL' + newline +
                            '\t Kupon: ' + code + explanation)
                    with self.subTest(newline=newline, explanation=explanation, code=code):
                        offer = bot.offer_terms(text)
                        self.assertEqual(offer[0], 300)
                        self.assertIn('kupon: ' + code.lower(), offer[1])
                        self.assertNotIn('300 tl kupon', offer[1])
                        terms.append(offer)
                self.assertNotEqual(bot.dedup_offer_key('Pamuk Havlu', terms[0]),
                                    bot.dedup_offer_key('Pamuk Havlu', terms[1]))

    def test_missing_threshold_and_percent_only(self):
        for text in ['', 'Fiyat: TL', 'Yalnız %15 indirim',
                     '500 TL ve üzeri alışverişlerde %5 indirim', 'Sepette 150 TL indirim',
                     'KOD200 koduyla 3 adet alımda 250 TL']:
            with self.subTest(text=text):
                self.assert_untrusted('Pamuk Havlu 30x40 Cm\n' + text)

    def test_valid_formats_and_trailing_punctuation(self):
        for text, value in [('Fiyat: 1.299,90 TL', 1299.9), ('₺1.299,90', 1299.9),
                            ('Fiyat: 1\u00a0299,90 TL', 1299.9), ('Fiyat: 1\u202f299,90 TL', 1299.9),
                            ('₺99, stokta', 99), ('Fiyat: 99. Kaçırmayın', 99),
                            ('Fiyat: 99, stokta', 99), ('Fiyat: 1299', 1299)]:
            with self.subTest(text=text):
                self.assertEqual(pf.extract_price(text).value, value)
                self.assertIn(pf.extract_price(text).confidence, {'medium', 'high'})

    def test_coupon_code_is_not_consumed_by_previous_price_line(self):
        for space in [' ', '\u00a0', '\u202f']:
            for code in ['KOD10', 'KOD20']:
                text = ('Pamuk Havlu\nFiyat: 300 TL\nKupon: ' + code).replace(' ', space)
                terms = bot.offer_terms(text)
                self.assertIn('kupon: ' + code.lower(), terms[1])
                self.assertNotIn('300 tl kupon', terms[1])
        self.assertIn('100 tl kupon', bot.offer_terms('Pamuk Havlu\n300 TL / 100 TL kupon')[1])

    def test_multiple_products_are_not_split_and_can_be_ambiguous(self):
        text = 'Pamuk Havlu\n100 TL\nCam Bardak\n200 TL'
        self.assertEqual(len(pf.find_candidates(text)), 2)
        self.assertEqual(pf.extract_price(text).value, 100)
        self.assert_untrusted(text)
        # Belgelenen sınır: güçlü etiket diğer ürünün yalın fiyatını bastırabilir.
        text = 'Pamuk Havlu\nFiyat: 100 TL\nCam Bardak 50 TL'
        self.assertEqual(pf.extract_price(text).value, 100)
        self.assertEqual(pf.extract_price(text).confidence, 'high')
        # Bu fiyatın hangi ürüne ait olduğu API tarafından ayrıca modellenmez.


class FlowResilienceTests(MainHarness, unittest.TestCase):
    TITLE = 'Deneme Pamuk Havlu 30x40 Cm Bej'

    def setUp(self):
        reset_state()
        FakeClient.default_history = []
        self.addCleanup(reset_state)
        self.addCleanup(setattr, FakeClient, 'default_history', [])
        path = self._write_config(source_chats=['@firsatz'], include_keywords=[], smart_header=True)
        self.addCleanup(os.unlink, path)
        self.client = self._run_main(path)
        self.source = next(iter(bot.SOURCE_IDS))

    def event(self, text, mid, poll=False, source=None):
        event = FakeEvent(source or self.source, 7, text, message_id=mid, media=False)
        event._fast_poll = poll
        return event

    async def send(self, event):
        await self.client.handlers[1][1](event)

    def test_metadata_changes_preserve_canonical_price_body_and_sources(self):
        text = self.TITLE + '\nFiyat: 300 TL / 2 adet alımda 200 TL\nSatıcı: Mağaza A'
        second = -1001111111112
        bot.SOURCE_IDS.add(second)
        bot.SOURCES.append({'id': second, 'name': 'Diğer Kaynak'})
        async def run():
            with patch.object(bot, 'DEDUP_EDIT_MIN_INTERVAL', 0):
                await self.send(self.event(text, 1))
                key, first = next(iter(bot.DEDUP_CACHE.items()))
                original_offer = first['offer']
                original_body = first['text']
                copy = self.TITLE.replace(' ', '  ') + '\n💰 Fiyat: 300₺ / 2 adet alımda 200₺\nSatıcı: Mağaza B'
                await self.send(self.event(copy, 2, True, second))
                entry = bot.DEDUP_CACHE[key]
                self.assertEqual(entry['offer'], original_offer)
                self.assertEqual(entry['count'], 2)
                self.assertIn('Diğer Kaynak', entry['sources'])
                self.assertIn('💰 200 TL', entry['text'])
                self.assertIn('Satıcı: Mağaza A', entry['text'])
                self.assertNotIn('Satıcı: Mağaza B', entry['text'])
                # Birleştirme gövdeyi sentezlemez; yalnız rozet/source bloğu değişir.
                self.assertIn(text, original_body)
                self.assertIn(text, entry['text'])
        asyncio.run(run())
        self.assertEqual(len(self.client.delivered), 1)

    def test_changed_coupon_condition_is_a_separate_offer(self):
        async def run():
            await self.send(self.event(self.TITLE + '\nFiyat: 300 TL\nKupon: KOD10', 1))
            await self.send(self.event(self.TITLE + '\nFiyat: 300 TL\nKupon: KOD20', 2))
        asyncio.run(run())
        self.assertEqual(len(self.client.delivered), 2)
        self.assertEqual(len(bot.DEDUP_CACHE), 2)

    def test_poll_live_order_and_concurrent_same_source_claim(self):
        async def run():
            for i, first_poll in enumerate([True, False]):
                mid = 20 + i
                events = [self.event(self.TITLE + '\nFiyat: 300 TL', mid, first_poll),
                          self.event(self.TITLE + '\nFiyat: 300 TL', mid, not first_poll)]
                await asyncio.gather(*(self.send(e) for e in events))
        with patch.object(bot, 'DEDUP_EDIT_MIN_INTERVAL', 0):
            asyncio.run(run())
        self.assertEqual(len(self.client.delivered), 1)
        self.assertEqual(bot.STATS['seen'], 2)
        self.assertEqual(next(iter(bot.DEDUP_CACHE.values()))['count'], 2)
        self.assertFalse(bot.SOURCE_IN_FLIGHT)

    def test_distinct_sources_wait_for_pending_send(self):
        async def run():
            second_source = -1001111111112
            bot.SOURCE_IDS.add(second_source)
            started, release = asyncio.Event(), asyncio.Event()
            original = self.client.send_message
            calls = 0
            async def slow_send(*args, **kwargs):
                nonlocal calls
                calls += 1
                started.set()
                await release.wait()
                return await original(*args, **kwargs)
            with patch.object(self.client, 'send_message', slow_send), patch.object(bot, 'DEDUP_EDIT_MIN_INTERVAL', 0):
                first = asyncio.create_task(self.send(self.event(self.TITLE + '\n300 TL', 1)))
                try:
                    await asyncio.wait_for(started.wait(), 2)
                    second = asyncio.create_task(self.send(self.event(self.TITLE + '\n300 TL', 2, True, second_source)))
                    await asyncio.sleep(0.01)
                    self.assertEqual(calls, 1)
                    self.assertTrue(next(iter(bot.DEDUP_CACHE.values()))['pending'])
                finally:
                    release.set()
                await asyncio.wait_for(asyncio.gather(first, second), 2)
                self.assertEqual(calls, 1)
        asyncio.run(run())
        self.assertEqual(len(self.client.delivered), 1)
        self.assertEqual(next(iter(bot.DEDUP_CACHE.values()))['count'], 2)

    def test_new_lower_price_after_pending_send_is_not_lost(self):
        async def run():
            started, release = asyncio.Event(), asyncio.Event()
            original = self.client.send_message
            async def slow_send(*args, **kwargs):
                started.set()
                await release.wait()
                return await original(*args, **kwargs)
            with patch.object(self.client, 'send_message', slow_send):
                first = asyncio.create_task(self.send(self.event(self.TITLE + '\n300 TL', 1)))
                try:
                    await asyncio.wait_for(started.wait(), 2)
                    second = asyncio.create_task(self.send(self.event(self.TITLE + '\n250 TL', 2)))
                    await asyncio.sleep(0.01)
                finally:
                    release.set()
                await asyncio.wait_for(asyncio.gather(first, second), 2)
        asyncio.run(run())
        self.assertEqual(len(self.client.delivered), 2)
        self.assertEqual(next(iter(bot.DEDUP_CACHE.values()))['offer'][0], 250)

    def test_cache_loss_can_renotify_without_history_reload(self):
        async def run():
            await self.send(self.event(self.TITLE + '\n300 TL', 1))
            bot.DEDUP_CACHE.clear()
            await self.send(self.event(self.TITLE + '\n300 TL', 2))
        asyncio.run(run())
        self.assertEqual(len(self.client.delivered), 2)

    def test_pack_size_and_color_are_distinct_offers(self):
        async def run():
            for i, suffix in enumerate([' 2 Adet', ' 4 Adet', ' Siyah', ' Beyaz']):
                await self.send(self.event(self.TITLE + suffix + '\n300 TL', i + 1))
        asyncio.run(run())
        self.assertEqual(len(self.client.delivered), 4)

    def test_late_low_confidence_copy_cannot_overwrite_reliable_price(self):
        async def run():
            with patch.object(bot, 'DEDUP_EDIT_MIN_INTERVAL', 0):
                await self.send(self.event(self.TITLE + '\nFiyat: 300 TL', 1))
                offer = next(iter(bot.DEDUP_CACHE.values()))['offer']
                low = self.TITLE + '\nEski fiyat: 100 TL'
                self.assertEqual(pf.extract_price(low).confidence, 'low')
                await self.send(self.event(low, 2))
                entry = next(iter(bot.DEDUP_CACHE.values()))
                self.assertEqual(entry['offer'], offer)
                self.assertIn('💰 300 TL', entry['text'])
                self.assertNotIn('Eski fiyat: 100 TL', entry['text'])
        asyncio.run(run())
        self.assertEqual(len(self.client.delivered), 1)

    def test_original_condition_and_coupon_details_survive_badging(self):
        text = (self.TITLE + '\nFiyat: 300 TL\nKupon: KOD10\nİkinci El: Yeni Gibi'
                '\nAçıklama: Kutusu açılmış; aksesuarları tam')
        copy = self.TITLE + '\n300 TL\nKupon: KOD10\nİkinci El: Yeni Gibi'
        async def run():
            with patch.object(bot, 'DEDUP_EDIT_MIN_INTERVAL', 0):
                await self.send(self.event(text, 1))
                original_offer = next(iter(bot.DEDUP_CACHE.values()))['offer']
                await self.send(self.event(copy, 2))
                entry = next(iter(bot.DEDUP_CACHE.values()))
                self.assertEqual(entry['offer'], original_offer)
                self.assertIn('durum:ikinci el yeni gibi', entry['offer'][1])
                self.assertIn('kupon: kod10', entry['offer'][1])
                self.assertIn(text, entry['text'])
                self.assertEqual(entry['count'], 2)
        asyncio.run(run())
        self.assertEqual(len(self.client.delivered), 1)


    def test_coupon_code_changes_with_crlf_and_details_send_separately(self):
        async def run():
            for mid, code, ending in [(1, 'KOD10', ' (ürün altından etkinleştirin)'),
                                      (2, 'KOD20', '\r\nAçıklama: Sepette uygulayın')]:
                text = self.TITLE + '\r\nFiyat: 300 TL\r\n\r\n\tKupon: ' + code + ending
                await self.send(self.event(text, mid, mid == 1))
        asyncio.run(run())
        self.assertEqual(len(self.client.delivered), 2)
        self.assertEqual(len(bot.DEDUP_CACHE), 2)
        self.assertEqual({entry['offer'][1] for entry in bot.DEDUP_CACHE.values()},
                         {'kupon: kod10', 'kupon: kod20'})
