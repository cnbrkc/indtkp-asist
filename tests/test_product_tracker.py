"""Offline synthetic fixtures: these do not certify live Akakçe access."""
import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from akakce_worker import cents, parse_page, product_url, target_cents, page_diagnostics
from product_tracker import ProductTracker, load_state

URL = 'https://www.akakce.com/fritoz/en-ucuz-ornek-fiyati,123.html'


def page(offers, **kwargs):
    return '<script type="application/ld+json">' + json.dumps({
        '@type': 'Product', 'name': 'Örnek ürün', 'offers': offers, **kwargs}) + '</script>'


def offer(price, seller='Satıcı', **kwargs):
    return {'@type': 'Offer', 'priceCurrency': 'TRY', 'price': price,
            'seller': {'name': seller}, 'availability': 'https://schema.org/InStock', **kwargs}


class ReaderTests(unittest.TestCase):
    def test_canonical_url(self):
        self.assertEqual(product_url(URL + '?ref=abc#x'), URL)
        self.assertEqual(product_url(URL.replace('www.', '')), URL)

    def test_reject_unsafe_urls(self):
        for url in ['http://www.akakce.com/x-fiyati,1.html',
                    'https://www.akakce.com.evil.test/x-fiyati,1.html',
                    'https://localhost/x-fiyati,1.html', 'https://127.0.0.1/x-fiyati,1.html',
                    'https://user@www.akakce.com/x-fiyati,1.html',
                    'https://www.akakce.com:444/x-fiyati,1.html',
                    'https://www.akakce.com/arama/?q=test']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                product_url(url)

    def test_prices(self):
        self.assertEqual(target_cents('6.500,50 TL'), 650050)
        self.assertEqual(target_cents('6500'), 650000)
        self.assertEqual(cents('6499.99'), 649999)
        for bad in ['0', '0.001', '-1', 'nan', 'inf', True]:
            with self.subTest(bad=bad), self.assertRaises((ValueError, TypeError)):
                cents(bad)

    def test_reject_ambiguous_targets(self):
        for bad in ['6.50', '6500.50', '6,500.50', 'nan', '1,2,3']:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                target_cents(bad)

    def test_all_sellers_no_allowlist(self):
        result = parse_page(page([offer('10000', 'Teknosa'), offer('7000', 'Yeni satıcı')]))
        self.assertEqual(result['price'], 700000)
        self.assertEqual(result['seller'], 'Yeni satıcı')
        self.assertEqual(result['offers'], 2)
        self.assertEqual(result['sample_offers'][0]['seller'], 'Yeni satıcı')

    def test_order_independent_and_nested(self):
        raw = '<script type="application/ld+json">' + json.dumps({'@graph': [
            {'@type': 'Product', 'name': 'Ürün', 'offers': {
                '@type': 'AggregateOffer', 'offers': [offer('6000')]}}]}) + '</script>'
        self.assertEqual(parse_page(raw)['price'], 600000)

    def test_stock_currency_and_invalid_prices(self):
        result = parse_page(page([offer('1', availability='https://schema.org/OutOfStock'),
                                  offer('2', priceCurrency='USD'), offer('0'),
                                  offer('NaN'), offer('9000')]))
        self.assertEqual(result['price'], 900000)

    def test_fail_closed(self):
        for raw in ['<html>captcha challenge</html>', page({'@type': 'AggregateOffer', 'lowPrice': '10'}),
                    '<script type="application/ld+json">broken</script>', '<h1>100 TL</h1>',
                    page([]), page([offer('0')])]:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_page(raw)


    def test_diagnostics_are_inert_bounded_outline(self):
        raw = ('<div class="offers" onclick="SECRET_HANDLER">17 teklif'
               '<a href="https://example.com/SECRET_URL">6.839,05 TL</a>'
               '<script>SECRET_SCRIPT</script><form>SECRET_FORM</form></div>')
        result = page_diagnostics(raw)
        outline = result['dom_outline']
        self.assertIn('17 teklif', outline)
        self.assertIn('6.839,05 TL', outline)
        self.assertNotIn('SECRET', outline)
        self.assertLessEqual(len(page_diagnostics('<div>' + 'x' * 200_000)['dom_outline']), 100_000)

    def test_diagnostics_keep_jsonld_separate(self):
        result = page_diagnostics(page([offer('6839.05')]))
        self.assertEqual(result['jsonld_blocks'], 1)
        self.assertIn('6839.05', result['jsonld_preview'][0])
        self.assertNotIn('6839.05', result['dom_outline'])


    def html_offer(self, price, seller):
        return ('<li><span class="pb_v8"><span class="pt_v8">' + price +
                '</span></span><span class="v_v8"><img alt="Mağaza">/' + seller +
                '</span><span class="stock_v8">Stokta</span></li>')

    def test_html_main_list_not_recommendations(self):
        raw = page({'@type': 'AggregateOffer', 'offerCount': '2', 'offers': [offer('9000')]})
        raw += '<ul id="PL_22"><li><span class="pt_v8">1 TL</span></li></ul>'
        raw += '<ul class="pl_v9" id="PL">' + self.html_offer('9.000,00 TL', 'A') + self.html_offer('6.000,00 TL', 'B') + '</ul>'
        result = parse_page(raw)
        self.assertEqual(result['price'], 600000)
        self.assertEqual(result['seller'], 'Mağaza/B')
        self.assertEqual(result['offers'], 2)
        self.assertTrue(result['coverage_complete'])
        self.assertEqual(result['source'], 'html_main_list')

    def test_partial_jsonld_is_explicit(self):
        result = parse_page(page({'offerCount': '20', 'offers': [offer('6839.05')]}))
        self.assertFalse(result['coverage_complete'])
        self.assertEqual(result['reported_offer_count'], 20)

    def test_partial_html_not_declared_complete(self):
        raw = page({'offerCount': '20', 'offers': [offer('6839.05')]})
        raw += '<ul id="PL">' + ''.join(self.html_offer('6.839,05 TL', str(i)) for i in range(16)) + '</ul>'
        result = parse_page(raw)
        self.assertEqual(result['html_rows'], 16)
        self.assertEqual(result['offers'], 16)
        self.assertFalse(result['coverage_complete'])

    def test_invalid_html_row_not_silently_dropped(self):
        raw = page({'offerCount': '2', 'offers': [offer('6839.05')]})
        raw += '<ul id="PL">' + self.html_offer('6.839,05 TL', 'A') + '<li>bozuk satır</li></ul>'
        result = parse_page(raw)
        self.assertEqual(result['invalid_html_rows'], 1)
        self.assertFalse(result['coverage_complete'])

    def test_html_price_handles_nonbreaking_space(self):
        raw = page([offer('6839.05')])
        raw += '<ul id="PL">' + self.html_offer('6.839,05&nbsp;TL', 'A') + '</ul>'
        self.assertEqual(parse_page(raw)['price'], 683905)


    def test_actual_report_jsonld_is_only_ten_of_twenty(self):
        path = Path(__file__).parent / 'fixtures' / 'akakce' / 'partial-jsonld.html'
        result = parse_page(path.read_text())
        self.assertEqual(result['price'], 683905)
        self.assertEqual(result['seller'], 'Pttavm/Promo')
        self.assertEqual(result['jsonld_offers'], 10)
        self.assertEqual(result['reported_offer_count'], 20)
        self.assertFalse(result['coverage_complete'])


    def test_actual_report_minimum_verified_without_all_sellers(self):
        path = Path(__file__).parent / 'fixtures' / 'akakce' / 'current-minimum.html'
        result = parse_page(path.read_text())
        self.assertEqual(result['price'], 683905)
        self.assertEqual(result['seller'], 'Pttavm/Promo')
        self.assertEqual(result['offers'], 16)
        self.assertFalse(result['coverage_complete'])
        self.assertTrue(result['minimum_verified'])
        self.assertEqual(result['declared_minimum'], 683905)

    def test_minimum_mismatch_not_verified(self):
        result = parse_page(page({'priceCurrency': 'TRY', 'lowPrice': '6000',
                                  'offers': [offer('6839.05')]}))
        self.assertFalse(result['minimum_verified'])

    def test_missing_or_foreign_currency_minimum_not_verified(self):
        for aggregate in [{'offers': [offer('6839.05')]},
                          {'priceCurrency': 'USD', 'lowPrice': '6839.05', 'offers': [offer('6839.05')]}]:
            self.assertFalse(parse_page(page(aggregate))['minimum_verified'])


class TrackerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'watches.json'
        self.sent = []
        self.saved = []

        async def call(method, payload):
            self.sent.append(payload)
            return {'message_id': len(self.sent)}

        async def reader(url):
            return {'title': 'Örnek', 'price': 600000, 'seller': 'Herhangi biri', 'offers': 2}

        def persist(path, data):
            self.saved.append(copy.deepcopy(data))
            path.write_text(json.dumps(data))
            return True, 'test'

        self.tracker = ProductTracker(self.path, SimpleNamespace(call=call), persist, reader)
        self.replies = []

        async def reply(text):
            self.replies.append(text)

        self.event = SimpleNamespace(chat_id=123, reply=reply)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def item(self, target=700000):
        item = {'url': URL, 'target': target, 'chat_id': 123, 'title': 'Örnek', 'alert_price': None}
        self.tracker.state['items'] = [item]
        return item

    async def test_disabled_by_default(self):
        self.assertFalse(self.tracker.state['enabled'])
        await self.tracker.handle(self.event, URL)
        self.assertTrue(self.tracker.queue.empty())

    async def test_commands_and_nonblocking_link_queue(self):
        self.tracker.state['enabled'] = True
        async def fail_if_called(url):
            raise AssertionError('handler must not fetch')
        self.tracker.reader = fail_if_called
        self.assertTrue(await self.tracker.handle(self.event, URL))
        self.assertEqual(self.tracker.queue.get_nowait(), ('link', 123, URL))

    async def test_existing_draft_priority(self):
        self.tracker.state['enabled'] = True
        self.assertFalse(await self.tracker.handle(self.event, URL, existing_pending=True))
        self.assertFalse(await self.tracker.handle(self.event, '6500', existing_pending=True))
        self.assertFalse(await self.tracker.handle(self.event, '/kaydet', existing_pending=True))
        self.assertFalse(await self.tracker.handle(self.event, '/durum'))

    async def test_link_target_save_and_restart(self):
        await self.tracker.job('link', 123, URL)
        self.assertIn(123, self.tracker.drafts)
        await self.tracker.handle(self.event, '7000')
        job = self.tracker.queue.get_nowait()
        await self.tracker.job(*job)
        restored = load_state(self.path)
        self.assertEqual(restored['items'][0]['target'], 700000)
        self.assertEqual(restored['items'][0]['url'], URL)

    async def test_cancel_draft(self):
        self.tracker.drafts[123] = {'until': float('inf')}
        self.assertTrue(await self.tracker.handle(self.event, '/iptal'))
        self.assertFalse(self.tracker.drafts)

    async def test_queue_full(self):
        for _ in range(32):
            self.tracker.queue.put_nowait(('x', 123, ''))
        await self.tracker.handle(self.event, '/takipler')
        self.assertIn('dolu', self.replies[-1])

    async def test_target_equality_no_alert(self):
        await self.tracker.check(self.item(600000))
        self.assertFalse(self.sent)

    async def test_alert_and_dedup(self):
        item = self.item()
        await self.tracker.check(item)
        self.assertEqual(len(self.sent), 1)
        self.tracker.next_host = 0
        await self.tracker.check(item)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(load_state(self.path)['items'][0]['alert_price'], 600000)

    async def test_failed_fetch_not_zero(self):
        async def fail(url):
            raise ValueError('blocked')
        self.tracker.reader = fail
        item = self.item()
        for _ in range(3):
            self.tracker.next_host = 0
            await self.tracker.check(item)
        self.assertEqual(len(self.sent), 1)
        self.assertIn('okunamıyor', self.sent[0]['text'])
        self.assertIsNone(item['alert_price'])

    async def test_save_failure_rolls_back(self):
        self.tracker.persist = lambda *args: (False, 'failure')
        await self.tracker.job('/takipac', 123, '')
        self.assertFalse(self.tracker.state['enabled'])
        self.assertIn('başarısız', self.sent[-1]['text'])

    async def test_delete_bad_index_does_not_mutate(self):
        self.item()
        with self.assertRaises(ValueError):
            await self.tracker.job('/takipsil', 123, '/takipsil 0')
        self.assertEqual(len(self.tracker.state['items']), 1)

    async def test_corrupt_file_isolated(self):
        self.path.write_text('{broken')
        other = ProductTracker(self.path, self.tracker.api, self.tracker.persist)
        self.assertTrue(other.disabled_error)
        self.assertEqual(self.path.read_text(), '{broken')

    async def test_cancellation_propagates(self):
        async def cancel(url):
            raise asyncio.CancelledError()
        self.tracker.reader = cancel
        with self.assertRaises(asyncio.CancelledError):
            await self.tracker.check(self.item())

    async def test_hung_reader_does_not_block_command_routing(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow(url):
            started.set()
            await release.wait()
            return {'title': 'Örnek', 'price': 600000, 'seller': '', 'offers': 1}

        self.tracker.reader = slow
        self.item()
        self.tracker.state['enabled'] = True
        task = asyncio.create_task(self.tracker.run())
        try:
            await asyncio.wait_for(started.wait(), 2)
            self.assertFalse(await asyncio.wait_for(self.tracker.handle(self.event, '/durum'), .1))
            self.assertTrue(await asyncio.wait_for(self.tracker.handle(self.event, '/takipler'), .1))
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_full_list_not_truncated(self):
        self.tracker.state['items'] = [
            {'url': URL.replace('123', str(i)), 'title': 'Ürün adı ' * 20,
             'target': 700000, 'chat_id': 123, 'alert_price': None} for i in range(1, 31)]
        await self.tracker.job('/takipler', 123, '')
        all_text = '\n'.join(p['text'] for p in self.sent)
        self.assertIn('30. ', all_text)
        self.assertTrue(all(len(p['text'].encode('utf-16-le')) // 2 <= 4096 for p in self.sent))

    async def test_lower_new_price_alerts(self):
        item = self.item()
        await self.tracker.check(item)
        self.tracker.next_host = 0

        async def cheaper(url):
            return {'title': 'Örnek', 'price': 550000, 'seller': 'Yeni satıcı', 'offers': 1}

        self.tracker.reader = cheaper
        await self.tracker.check(item)
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(item['alert_price'], 550000)


    async def test_unverified_minimum_cannot_trigger_tracking(self):
        from unittest.mock import AsyncMock, patch
        from product_tracker import read_product
        process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(
            json.dumps({'ok': True, 'result': {'price': 683905, 'coverage_complete': False, 'minimum_verified': False}}).encode(), b'')))
        with patch('product_tracker.asyncio.create_subprocess_exec', new=AsyncMock(return_value=process)):
            with self.assertRaisesRegex(ValueError, 'Güncel en düşük fiyat doğrulanamadı'):
                await read_product(URL)


    async def test_verified_minimum_allowed_with_partial_seller_list(self):
        from unittest.mock import AsyncMock, patch
        from product_tracker import read_product
        result = {'price': 683905, 'coverage_complete': False, 'minimum_verified': True}
        process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(
            json.dumps({'ok': True, 'result': result}).encode(), b'')))
        with patch('product_tracker.asyncio.create_subprocess_exec', new=AsyncMock(return_value=process)):
            self.assertEqual(await read_product(URL), result)
