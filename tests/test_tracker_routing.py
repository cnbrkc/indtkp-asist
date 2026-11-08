"""Cimri/Epey linkleri Akakçe hattından geçerken Akakçe davranışı bozulmuyor mu?

Ağ yok: alt süreç (`asyncio.create_subprocess_exec`) taklit edilir.
"""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import product_tracker as pt
from product_tracker import (ProductTracker, load_state, notification_text,
                             aggregator_site, is_product_link, normalize_product_url, read_product)

AKAKCE = 'https://www.akakce.com/fritoz/en-ucuz-ornek-fiyati,123.html'
CIMRI = ('https://www.cimri.com/televizyonlar/'
         'en-ucuz-tcl-t6d-televizyon-fiyatlari,a2582673366')
EPEY = 'https://www.epey.com/televizyon/tcl-65c6k.html'


def aggregator_result(**overrides):
    result = {
        'title': 'TCL 65C6K', 'price': 6_092_775, 'currency': 'TRY', 'store': 'Epey',
        'seller': 'Trendyol', 'offers': [{'seller': 'Trendyol', 'price': 6_092_775}],
        'offer_count': 5, 'price_source': 'aggregate_lowprice',
        'jsonld_verified': True, 'url': EPEY,
    }
    result.update(overrides)
    return result


class RoutingTests(unittest.TestCase):
    def test_site_detection(self):
        self.assertEqual(aggregator_site(CIMRI), 'Cimri')
        self.assertEqual(aggregator_site(EPEY), 'Epey')
        self.assertIsNone(aggregator_site(AKAKCE))
        self.assertIsNone(aggregator_site('https://www.hepsiburada.com/x'))

    def test_link_detection_is_additive(self):
        for url in (AKAKCE, CIMRI, EPEY):
            self.assertTrue(is_product_link(url), url)
        for text in ('merhaba', '/webtakipler', 'https://example.test/x'):
            self.assertFalse(is_product_link(text), text)

    def test_akakce_urls_are_normalized_exactly_as_before(self):
        messy = AKAKCE + '?gclid=abc#yorumlar'
        self.assertEqual(normalize_product_url(messy),
                         'https://www.akakce.com/fritoz/en-ucuz-ornek-fiyati,123.html')

    def test_aggregator_urls_use_their_own_rule(self):
        self.assertEqual(normalize_product_url(CIMRI + '?magaza=Amazon'), CIMRI)
        self.assertEqual(normalize_product_url(EPEY + '?utm_source=x'), EPEY)
        with self.assertRaises(ValueError):
            normalize_product_url('https://www.epey.com/televizyon/karsilastir/1-2/x_y/')


class WorkerChoiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []

    def fake_process(self, body, returncode=0):
        class Process:
            def __init__(self):
                self.returncode = returncode

            async def communicate(self):
                return json.dumps(body, ensure_ascii=False).encode(), b''

        async def spawn(*args, **kwargs):
            self.calls.append((args, kwargs))
            return Process()

        return mock.patch.object(pt.asyncio, 'create_subprocess_exec', spawn)

    async def test_cimri_and_epey_go_to_the_aggregator_worker(self):
        with self.fake_process({'ok': True, 'result': aggregator_result()}):
            for url in (CIMRI, EPEY):
                result = await read_product(url)
                self.assertEqual(result['price'], 6_092_775)
        workers = [Path(args[2]).name for args, _ in self.calls]
        self.assertEqual(workers, ['aggregator_worker.py', 'aggregator_worker.py'])
        self.assertTrue(all(args[3].startswith('https://www.') for args, _ in self.calls))

    async def test_akakce_still_goes_to_the_akakce_worker(self):
        akakce_body = {'ok': True, 'result': {'title': 'Örnek', 'price': 600_000,
                                             'minimum_verified': True}}
        with self.fake_process(akakce_body):
            await read_product(AKAKCE)
        self.assertEqual(Path(self.calls[0][0][2]).name, 'akakce_worker.py')

    async def test_aggregator_requires_jsonld_verification(self):
        with self.fake_process({'ok': True, 'result': aggregator_result(jsonld_verified=False)}):
            with self.assertRaises(ValueError) as ctx:
                await read_product(EPEY)
        self.assertIn('doğrulanamadı', str(ctx.exception))

    async def test_aggregator_failure_names_the_site_and_reason(self):
        body = {'ok': False, 'error': 'ValueError', 'code': 'PARSE',
                'reason': 'JSON-LD fiyatı uyuşmuyor.'}
        with self.fake_process(body, returncode=1):
            with self.assertRaises(ValueError) as ctx:
                await read_product(EPEY)
        self.assertIn('Epey okunamadı', str(ctx.exception))
        self.assertIn('uyuşmuyor', str(ctx.exception))

    async def test_blocked_failure_does_not_blame_the_page(self):
        body = {'ok': False, 'error': 'HTTPError', 'code': 'BLOCKED', 'http_status': 403}
        with self.fake_process(body, returncode=1):
            with self.assertRaises(ValueError) as ctx:
                await read_product(EPEY)
        message = str(ctx.exception)
        self.assertIn('403', message)
        self.assertNotIn('sayfa değişikliği', message.lower())

    async def test_aggregator_worker_body_is_not_trusted_for_negative_prices(self):
        with self.fake_process({'ok': True, 'result': aggregator_result(price=-1)}):
            with self.assertRaises(ValueError):
                await read_product(EPEY)


class NotificationTests(unittest.TestCase):
    def test_aggregator_notification_names_site_store_and_count(self):
        text = notification_text(aggregator_result(), EPEY)
        self.assertIn('Epey Takip Bildirimi', text)
        self.assertIn('En düşük fiyat: Trendyol', text)
        self.assertIn('5 mağaza fiyatı karşılaştırıldı', text)
        self.assertIn(EPEY, text)

    def test_akakce_notification_is_unchanged(self):
        result = {'title': 'Örnek', 'price': 600_000, 'offers': 2}
        self.assertEqual(notification_text(result, AKAKCE),
                         '₺6.000 — Örnek\n\nAkakçe Takip Bildirimi\n\n' + AKAKCE)

    def test_unknown_seller_is_not_printed_as_a_name(self):
        result = aggregator_result(seller=None,
                                  offers=[{'seller': None, 'price': 6_092_775}])
        self.assertNotIn('En düşük fiyat', notification_text(result, EPEY))


class TrackerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'watches.json'
        self.sent, self.saved = [], []

        async def call(method, payload):
            self.sent.append(payload)
            return {'message_id': len(self.sent)}

        async def reader(url):
            return aggregator_result(url=url)

        def persist(path, data):
            self.saved.append(data)
            path.write_text(json.dumps(data))
            return True, 'test'

        self.feed = Path(self.tmp.name) / pt.FEED_FILENAME
        self.feed.write_text(json.dumps({'version': 1, 'updated_at': 1, 'items': {
            EPEY: {'title': 'TCL 65C6K', 'price': 6_092_775, 'seller': 'Trendyol',
                   'offer_count': 5, 'verified': True, 'read_at': int(__import__('time').time())},
            CIMRI: {'title': 'TCL T6D', 'price': 2_999_900, 'seller': 'Amazon-prime',
                    'offer_count': 3, 'verified': True, 'read_at': int(__import__('time').time())},
        }}), encoding='utf-8')
        self.tracker = ProductTracker(self.path, SimpleNamespace(call=call), persist, reader,
                                      feed_path=self.feed)
        self.replies = []

        async def reply(text):
            self.replies.append(text)

        self.event = SimpleNamespace(chat_id=123, reply=reply)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_off_by_default_refuses_before_anything_else(self):
        await self.tracker.handle(self.event, CIMRI)
        self.assertTrue(self.tracker.queue.empty())
        self.assertIn('kapalı', self.replies[-1].lower())

    async def test_cimri_link_goes_to_the_feed_not_the_network(self):
        """B yolu: besin varsa link kabul edilir, ağa hiç çıkılmaz."""
        self.tracker.state['enabled'] = True

        async def fail_if_called(url):
            raise AssertionError('Cimri ağdan okunmamalı; besin kullanılmalı')

        self.tracker.reader = fail_if_called
        self.assertTrue(await self.tracker.handle(self.event, CIMRI + '?magaza=Amazon'))
        self.assertEqual(self.tracker.queue.get_nowait()[0], 'link')

    async def test_add_stores_kind_and_site(self):
        await self.tracker.job('link', 123, EPEY)
        self.assertIn('en düşük Trendyol', self.sent[-1]['text'])
        await self.tracker.handle(self.event, '60000')
        await self.tracker.job(*self.tracker.queue.get_nowait())
        item = load_state(self.path)['items'][0]
        self.assertEqual((item['kind'], item['site']), ('aggregator', 'Epey'))
        self.assertEqual(item['target'], 6_000_000)

    async def test_mixed_list_survives_restart(self):
        await self.tracker.job('link', 123, EPEY)
        await self.tracker.handle(self.event, '60000')
        await self.tracker.job(*self.tracker.queue.get_nowait())
        # Akakçe kaydı eski biçimde (kind/site yok) duruyor: yine de okunmalı.
        data = json.loads(self.path.read_text())
        data['items'].append({'url': AKAKCE, 'target': 700_000, 'chat_id': 123,
                              'title': 'Örnek', 'alert_price': None})
        self.path.write_text(json.dumps(data))
        restored = load_state(self.path)
        self.assertEqual([i['url'] for i in restored['items']], [EPEY, AKAKCE])
        tracker = ProductTracker(self.path, SimpleNamespace(call=None), None)
        self.assertEqual([i['kind'] for i in tracker.state['items']], ['aggregator', 'akakce'])
        self.assertEqual([i['site'] for i in tracker.state['items']], ['Epey', 'Akakçe'])

    async def test_aggregator_alert_is_sent_once_then_deduped(self):
        item = {'url': EPEY, 'target': 7_000_000, 'chat_id': 123,
                'title': 'TCL 65C6K', 'alert_price': None}
        self.tracker.state['items'] = [item]
        await self.tracker.check(item)
        self.assertEqual(len(self.sent), 1)
        self.assertIn('Epey Takip Bildirimi', self.sent[0]['text'])
        await self.tracker.check(item)
        self.assertEqual(len(self.sent), 1)

    async def test_bad_site_in_file_is_rejected(self):
        self.path.write_text(json.dumps({'version': 1, 'enabled': False, 'items': [
            {'url': 'https://www.cimri.com/arama?q=tcl', 'target': 1, 'chat_id': 1,
             'title': 'x'}]}))
        with self.assertRaises(ValueError):
            load_state(self.path)


if __name__ == '__main__':
    unittest.main()


class PlatformBlockedSiteTests(unittest.IsolatedAsyncioTestCase):
    """2026-11-08 ölçümü: Cimri ve Epey bu platformdan 403 (6/6 varyant).
    Bu yüzden kuyruğa hiç girmemeli; anında ve doğru sebeple cevap verilmeli."""

    async def asyncSetUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'watches.json'
        self.sent = []

        async def call(method, payload):
            self.sent.append(payload)
            return {'message_id': 1}

        def persist(path, data):
            path.write_text(json.dumps(data))
            return True, 'test'

        self.tracker = ProductTracker(self.path, SimpleNamespace(call=call), persist)
        self.tracker.state['enabled'] = True
        self.replies = []

        async def reply(text):
            self.replies.append(text)

        self.event = SimpleNamespace(chat_id=123, reply=reply)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def test_policy_names_the_blocked_sites(self):
        self.assertEqual(pt.PLATFORM_AGGREGATOR_BLOCKED, frozenset({'cimri', 'epey'}))

    async def test_epey_link_is_refused_immediately_with_the_right_reason(self):
        self.assertTrue(await self.tracker.handle(self.event, EPEY))
        self.assertTrue(self.tracker.queue.empty(), 'kuyruğa girmemeli')
        message = self.replies[-1]
        self.assertIn('Epey okunamadı', message)
        self.assertIn('403', message)
        self.assertIn('kaydedilmedi', message)
        self.assertIn('Akakçe', message)          # çözüm yolu gösteriliyor
        self.assertNotIn('sayfa değişikliği', message.lower())

    async def test_cimri_link_is_refused_immediately_too(self):
        self.assertTrue(await self.tracker.handle(self.event, CIMRI))
        self.assertTrue(self.tracker.queue.empty())
        self.assertIn('Cimri okunamadı', self.replies[-1])

    async def test_akakce_is_completely_unaffected(self):
        self.assertTrue(await self.tracker.handle(self.event, AKAKCE))
        self.assertEqual(self.tracker.queue.get_nowait(), ('link', 123, AKAKCE))

    async def test_refusal_does_not_require_the_feature_to_be_enabled(self):
        """Kapalıyken de doğru sebep söylenir: 'kapalı' demek yanıltıcı olurdu."""
        self.tracker.state['enabled'] = False
        self.assertTrue(await self.tracker.handle(self.event, EPEY))
        self.assertIn('403', self.replies[-1])

    async def test_existing_records_still_load_and_keep_their_urls(self):
        """Daha önce kaydedilmiş Cimri/Epey kaydı varsa dosya bozulmasın."""
        self.path.write_text(json.dumps({'version': 1, 'enabled': True, 'items': [
            {'url': EPEY, 'target': 1000, 'chat_id': 1, 'title': 'TCL', 'kind': 'aggregator',
             'site': 'Epey'}]}))
        restored = load_state(self.path)
        self.assertEqual(restored['items'][0]['url'], EPEY)
