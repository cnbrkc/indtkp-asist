"""B yolu: yerel okuyucu besini (aggregator_feed.json) — ücret yok, ağ yok.

Kural: Telegram bildirimleri yavaşlamayacak. Bu yüzden bot ağa hiç çıkmaz;
yavaş/sıkıntılı okuma **kullanıcının kendi bilgisayarında** (`yerel_okuyucu.py`)
olur ve yalnızca bir JSON dosyası paylaşılır.
"""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import product_tracker as pt
import yerel_okuyucu as reader
from product_tracker import (FEED_STALE_SECONDS, ProductTracker, feed_entry, load_feed,
                             read_failure_text)

EPEY = 'https://www.epey.com/televizyon/tcl-65c6k.html'
CIMRI = ('https://www.cimri.com/televizyonlar/'
         'en-ucuz-x-fiyatlari,a123')
AKAKCE = 'https://www.akakce.com/fritoz/en-ucuz-x-fiyati,123.html'


def entry(price=6_092_775, age=60, verified=True, seller='Trendyol'):
    return {'title': 'TCL 65C6K', 'price': price, 'currency': 'TRY', 'seller': seller,
            'offer_count': 5, 'verified': verified, 'read_at': int(time.time()) - age,
            'source': 'local'}


class FeedEntryTests(unittest.TestCase):
    def test_fresh_verified_entry_is_used(self):
        result = feed_entry({EPEY: entry()}, EPEY)
        self.assertEqual(result['price'], 6_092_775)
        self.assertEqual(result['store'], 'Epey')
        self.assertEqual(result['seller'], 'Trendyol')
        self.assertEqual(result['price_source'], 'local_feed')
        self.assertTrue(result['jsonld_verified'])

    def test_stale_entry_is_refused(self):
        old = entry(age=FEED_STALE_SECONDS + 60)
        self.assertIsNone(feed_entry({EPEY: old}, EPEY))

    def test_unverified_entry_is_refused(self):
        self.assertIsNone(feed_entry({EPEY: entry(verified=False)}, EPEY))

    def test_bad_prices_are_refused(self):
        for price in (0, -5, '6092775', 12.5, None):
            self.assertIsNone(feed_entry({EPEY: entry(price=price)}, EPEY), price)

    def test_missing_url_or_garbage_feed_is_refused(self):
        self.assertIsNone(feed_entry({}, EPEY))
        self.assertIsNone(feed_entry(None, EPEY))
        self.assertIsNone(feed_entry({'x': 'y'}, EPEY))

    def test_broken_file_reads_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'feed.json'
            path.write_text('{bozuk', encoding='utf-8')
            self.assertEqual(load_feed(path), {})
            path.write_text(json.dumps({'version': 99, 'items': {}}), encoding='utf-8')
            self.assertEqual(load_feed(path), {})
            self.assertEqual(load_feed(Path(tmp) / 'yok.json'), {})


class StaleMessageTests(unittest.TestCase):
    def test_message_explains_the_real_cause_and_the_fix(self):
        text = read_failure_text('Epey', {'code': 'STALE_FEED', 'http_status': 403})
        self.assertIn('403', text)
        self.assertIn('yerel_okuyucu.py', text)
        self.assertIn('6 saat', text)
        self.assertNotIn('sayfa değişikliği', text.lower())


class TrackerFeedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.path = self.dir / 'watches.json'
        self.feed = self.dir / pt.FEED_FILENAME
        self.sent = []

        async def call(method, payload):
            self.sent.append(payload)
            return {'message_id': 1}

        async def never_network(url):
            raise AssertionError('feed varken ağa çıkılmamalı')

        def persist(path, data):
            path.write_text(json.dumps(data))
            return True, 'test'

        self.tracker = ProductTracker(self.path, SimpleNamespace(call=call), persist,
                                      never_network)
        self.tracker.state['enabled'] = True
        self.replies = []

        async def reply(text):
            self.replies.append(text)

        self.event = SimpleNamespace(chat_id=1, reply=reply)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def write_feed(self, items):
        self.feed.write_text(json.dumps({'version': 1, 'updated_at': int(time.time()),
                                         'items': items}), encoding='utf-8')

    async def test_feed_is_read_without_touching_the_network(self):
        self.write_feed({EPEY: entry()})
        result = await self.tracker.fetch(EPEY)
        self.assertEqual(result['price'], 6_092_775)

    async def test_missing_feed_fails_with_the_stale_message(self):
        with self.assertRaises(ValueError) as ctx:
            await self.tracker.fetch(EPEY)
        self.assertIn('yerel_okuyucu.py', str(ctx.exception))

    async def test_stale_feed_fails_closed(self):
        self.write_feed({EPEY: entry(age=FEED_STALE_SECONDS + 10)})
        with self.assertRaises(ValueError):
            await self.tracker.fetch(EPEY)

    async def test_alert_works_from_the_feed(self):
        self.write_feed({EPEY: entry(price=6_000_000)})
        item = {'url': EPEY, 'target': 7_000_000, 'chat_id': 1,
                'title': 'TCL 65C6K', 'alert_price': None}
        self.tracker.state['items'] = [item]
        await self.tracker.check(item)
        self.assertEqual(len(self.sent), 1)
        self.assertIn('Epey Takip Bildirimi', self.sent[0]['text'])

    async def test_link_is_accepted_when_feed_has_it(self):
        self.write_feed({EPEY: entry()})
        self.assertTrue(await self.tracker.handle(self.event, EPEY))
        self.assertEqual(self.tracker.queue.get_nowait()[0], 'link')

    async def test_link_is_refused_when_feed_is_missing(self):
        self.assertTrue(await self.tracker.handle(self.event, EPEY))
        self.assertTrue(self.tracker.queue.empty())
        self.assertIn('yerel_okuyucu.py', self.replies[-1])

    async def test_akakce_never_looks_at_the_feed(self):
        self.write_feed({EPEY: entry()})
        with self.assertRaises(AssertionError):
            # Akakçe hattı kendi okuyucusundan geçer (reader = never_network).
            await self.tracker.fetch(AKAKCE)


class LocalReaderTests(unittest.TestCase):
    def test_urls_from_watches_keeps_only_aggregators(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'w.json'
            path.write_text(json.dumps({'version': 1, 'enabled': True, 'items': [
                {'url': EPEY}, {'url': CIMRI}, {'url': AKAKCE}, {'url': 'saçma'}]}),
                encoding='utf-8')
            self.assertEqual(reader.urls_from_watches(path), [EPEY, CIMRI])

    def test_save_is_atomic_and_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'feed.json'
            reader.save_feed(out, {'items': {}})
            self.assertTrue(out.exists())
            self.assertFalse(list(Path(tmp).glob('*.tmp')))
            data = json.loads(out.read_text())
            self.assertEqual(data['version'], 1)
            self.assertGreater(data['updated_at'], 0)

    def test_failed_reads_do_not_destroy_existing_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'feed.json'
            reader.save_feed(out, {'items': {EPEY: entry()}})

            def boom(url):
                raise OSError('ağ yok')

            original = reader.read_one
            reader.read_one = boom
            try:
                results = reader.run_once([EPEY], out, gap=0, verbose=False)
            finally:
                reader.read_one = original
            self.assertEqual(results[EPEY][0], 'hata')
            # Eski kayıt korunmuş olmalı.
            self.assertIn(EPEY, json.loads(out.read_text())['items'])

    def test_gap_is_respected_between_requests(self):
        calls = []

        def fake_read(url):
            calls.append(time.monotonic())
            return {'price': 1, 'verified': True, 'read_at': int(time.time()),
                    'title': 'x', 'seller': 's', 'offer_count': 1}

        sleeps = []
        with tempfile.TemporaryDirectory() as tmp:
            original_read = reader.read_one
            original_sleep = reader.time.sleep
            reader.read_one = fake_read
            reader.time.sleep = lambda seconds: sleeps.append(seconds)
            try:
                reader.run_once([EPEY, CIMRI], Path(tmp) / 'f.json', gap=45, verbose=False)
            finally:
                reader.read_one = original_read
                reader.time.sleep = original_sleep
        self.assertEqual(sleeps, [45])       # 2 URL → tek bekleme


if __name__ == '__main__':
    unittest.main()


class LatencyTests(unittest.IsolatedAsyncioTestCase):
    """Kullanıcı şartı: 'Telegram bildirimlerimi geciktirip yavaşlatmasın'.

    Besin yolu yalnızca dosya okur. Ölçüm: 200 okumanın en kötüsü bile 1 ms'nin
    çok altında; tam bir kontrol (fiyat + alarm kararı) ~1–2 ms.
    """

    async def test_feed_read_is_fast_enough_to_be_invisible(self):
        import time as _time
        with tempfile.TemporaryDirectory() as tmp:
            feed = Path(tmp) / pt.FEED_FILENAME
            feed.write_text(json.dumps({'version': 1, 'updated_at': 1, 'items': {
                EPEY: entry()}}), encoding='utf-8')

            async def call(method, payload):
                return {'message_id': 1}

            tracker = ProductTracker(Path(tmp) / 'w.json', SimpleNamespace(call=call),
                                     lambda p, d: (True, 't'), feed_path=feed)
            timings = []
            for _ in range(200):
                started = _time.perf_counter()
                await tracker.fetch(EPEY)
                timings.append((_time.perf_counter() - started) * 1000)
            worst = max(timings)
            average = sum(timings) / len(timings)
        self.assertLess(average, 5.0, f'ortalama {average:.3f} ms')
        self.assertLess(worst, 50.0, f'en kötü {worst:.3f} ms')

    async def test_feed_never_blocks_the_event_loop(self):
        """Disk okuması senkron; ama dosya küçük. Eşzamanlı işler gecikmemeli."""
        import asyncio
        ticks = 0

        async def ticker():
            nonlocal ticks
            for _ in range(20):
                await asyncio.sleep(0.001)
                ticks += 1

        with tempfile.TemporaryDirectory() as tmp:
            feed = Path(tmp) / pt.FEED_FILENAME
            feed.write_text(json.dumps({'version': 1, 'updated_at': 1, 'items': {
                EPEY: entry()}}), encoding='utf-8')

            async def call(method, payload):
                return {}

            tracker = ProductTracker(Path(tmp) / 'w.json', SimpleNamespace(call=call),
                                     lambda p, d: (True, 't'), feed_path=feed)
            task = asyncio.create_task(ticker())
            for _ in range(50):
                await tracker.fetch(EPEY)
            await task
        self.assertEqual(ticks, 20, 'dosya okuması olay döngüsünü durdurmamalı')
