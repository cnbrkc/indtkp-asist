"""Yenileme aralığı: Akakçe / Cimri / Epey için dip sınır, otomatik seyrelme, geri çekilme.

Ağ yok. Tüm sayılar plan §13.6 ve §16'daki ölçümlere dayanır.
"""
import asyncio
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import product_tracker as pt
from product_tracker import (DEFAULT_INTERVAL_MINUTES, DEFAULT_POLICY, INTERVAL_CHOICES,
                             ProductTracker, SITE_POLICY, SITE_POLICY as POLICY,
                             bucket_target, buckets_from_items, effective_interval,
                             len_minutes, policy_for, site_key)

AKAKCE = 'https://www.akakce.com/fritoz/en-ucuz-ornek-fiyati,{n}.html'
CIMRI = ('https://www.cimri.com/televizyonlar/'
         'en-ucuz-ornek-{n}-televizyon-fiyatlari,a{n}0')
EPEY = 'https://www.epey.com/televizyon/ornek-{n}.html'


def item(url, minutes=None):
    data = {'url': url, 'target': 700_000, 'chat_id': 1, 'title': 'Örnek',
            'alert_price': None}
    if minutes is not None:                 # yoksa "otomatik"
        data['interval_minutes'] = minutes
    return data


class PolicyTests(unittest.TestCase):
    def test_every_supported_site_has_a_policy(self):
        for url in (AKAKCE.format(n=1), CIMRI.format(n=1), EPEY.format(n=1)):
            self.assertIn(site_key(url), SITE_POLICY)
            self.assertEqual(policy_for(url), POLICY[site_key(url)])

    def test_no_crawl_delay_and_floors_are_measured_not_guessed(self):
        # Üç robots.txt de kontrol edildi: Crawl-delay YOK. Dip sınırlar bu yüzden
        # sayfa ağırlığı + "ban yemeyelim" kuralıyla konuldu, robots'tan gelmiyor.
        for key, policy in SITE_POLICY.items():
            self.assertGreaterEqual(policy['min_gap'], 10, key)
            self.assertLessEqual(policy['min_gap'], 30, key)
            self.assertGreater(policy['single'], policy['min_gap'] * 5, key)
            self.assertLessEqual(policy['single'], 300, key)
            self.assertEqual(policy['max'], 1800, key)

    def test_single_product_is_faster_than_the_old_five_minutes(self):
        """Kullanıcı isteği: link azken daha sık kontrol."""
        for key in ('akakce', 'cimri', 'epey'):
            self.assertLessEqual(SITE_POLICY[key]['single'], 240, key)

    def test_epey_is_slightly_more_conservative_than_the_others(self):
        # Epey sayfası en ağırı (~9 chunk menü çöpü ölçüldü).
        self.assertGreater(SITE_POLICY['epey']['min_gap'], SITE_POLICY['akakce']['min_gap'])
        self.assertGreater(SITE_POLICY['epey']['single'], SITE_POLICY['akakce']['single'])

    def test_akakce_30_products_still_land_on_the_old_300_seconds(self):
        """Akakçe hattı bozulmadı: 30 ürün × 10 sn = 300 sn (eski INTERVAL)."""
        bucket = buckets_from_items([item(AKAKCE.format(n=n)) for n in range(30)])['akakce']
        self.assertEqual(bucket['interval'], pt.INTERVAL)

    def test_old_interval_constant_still_exists(self):
        self.assertEqual(pt.INTERVAL, 300)


class DilutionTests(unittest.TestCase):
    def test_formula(self):
        self.assertEqual(effective_interval({'count': 1, 'min_gap': 10}, 180), 180)
        self.assertEqual(effective_interval({'count': 30, 'min_gap': 10}, 180), 300)
        self.assertEqual(effective_interval({'count': 2, 'min_gap': 10}, 180), 180)

    def test_more_products_never_mean_faster_checks(self):
        previous = 0
        for count in (1, 2, 3, 10, 30):
            bucket = buckets_from_items(
                [item(AKAKCE.format(n=n)) for n in range(count)])['akakce']
            self.assertGreaterEqual(bucket['interval'], previous)
            previous = bucket['interval']

    def test_per_site_counting_is_independent(self):
        items = ([item(CIMRI.format(n=n)) for n in range(3)] +
                 [item(EPEY.format(n=n)) for n in range(20)] +
                 [item(AKAKCE.format(n=n)) for n in range(2)])
        buckets = buckets_from_items(items)
        self.assertEqual(sum(b['count'] for b in buckets.values()), 25)
        self.assertEqual(buckets['epey']['count'], 20)
        self.assertEqual(buckets['cimri']['count'], 3)
        # 20 Epey ürünü × 12 sn = 240 sn = tek ürün hedefi → 240 kalır.
        self.assertEqual(buckets['epey']['interval'], 240)
        self.assertEqual(buckets['akakce']['interval'], 180)

    def test_cap_is_respected(self):
        bucket = {'count': 500, 'min_gap': 12, 'wanted': 240, 'cap': 1800, 'clean': 0}
        self.assertEqual(bucket_target(bucket), 1800)

    def test_user_choice_can_only_slow_down_not_speed_past_floor(self):
        bucket = buckets_from_items([item(CIMRI.format(n=1), minutes=30)])['cimri']
        self.assertEqual(bucket['interval'], 1800)
        bucket = buckets_from_items([item(EPEY.format(n=1), minutes=2)])['epey']
        self.assertEqual(bucket['interval'], 240)   # seçim dibin altına inemez

    def test_interval_choices_are_offered_and_bounded(self):
        self.assertEqual(INTERVAL_CHOICES, (2, 5, 10, 15, 30))
        self.assertIn(DEFAULT_INTERVAL_MINUTES, INTERVAL_CHOICES)

    def test_cli_text(self):
        self.assertEqual(len_minutes(180), '3 dk')
        self.assertEqual(len_minutes(1800), '30 dk')
        self.assertEqual(len_minutes(3600), '1 sa')
        self.assertEqual(len_minutes(5400), '1 sa 30 dk')
        self.assertEqual(len_minutes(30), '1 dk\'dan kısa')


class BackoffTests(unittest.TestCase):
    def test_error_grows_the_interval_and_stays_capped(self):
        bucket = {'count': 1, 'min_gap': 10, 'wanted': 180, 'cap': 1800, 'slowdown': 1}
        self.assertEqual(bucket_target(bucket), 180)          # sağlıklı
        bucket['slowdown'] = 2
        self.assertEqual(bucket_target(bucket), 360)          # 1 hata
        bucket['slowdown'] = 8
        self.assertEqual(bucket_target(bucket), 1440)         # 3. hata
        bucket.update(count=30, slowdown=8)
        self.assertEqual(bucket_target(bucket), 1800)         # tavan 30 dk
        bucket.update(slowdown=999)
        self.assertEqual(bucket_target(bucket), 1800)         # slowdown 8'de kırpılır

    def test_thirty_minutes_is_never_exceeded(self):
        """Engel ne kadar sürerse sürsün kontrol 30 dk'dan seyrek olmaz."""
        for count in (1, 10, 30):
            bucket = {'count': count, 'min_gap': 12, 'wanted': 240, 'cap': 1800,
                      'slowdown': 8}
            self.assertLessEqual(bucket_target(bucket), 1800)

    def test_slowdown_is_not_applied_to_a_healthy_site(self):
        """Regresyon: eskiden 3 temiz okumadan sonra kalıcı 2× yavaşlama vardı."""
        bucket = buckets_from_items([item(CIMRI.format(n=1))])['cimri']
        for _ in range(10):
            bucket['clean'] = bucket.get('clean', 0) + 1
        self.assertEqual(bucket_target(bucket), 180)

    def test_backoff_weight_table(self):
        self.assertEqual(pt.BACKOFF_WEIGHT, {2: 2, 3: 3, 4: 4})
        # 1 hata: çarpan yok (yeniden dene), 2 hata: 4x, 4 hata: 16x → 3600 tavanı
        base = 180
        self.assertEqual(min(3600, base * 2 ** pt.BACKOFF_WEIGHT.get(1, 0)), base)
        self.assertEqual(min(3600, base * 2 ** pt.BACKOFF_WEIGHT[2]), 720)
        self.assertEqual(min(3600, base * 2 ** pt.BACKOFF_WEIGHT[4]), 2880)


class TrackerIntervalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'watches.json'
        self.sent, self.saved = [], []

        async def call(method, payload):
            self.sent.append(payload)
            return {'message_id': len(self.sent)}

        async def reader(url):
            return {'title': 'Örnek', 'price': 600_000, 'seller': 'x', 'offers': 1}

        def persist(path, data):
            self.saved.append(data)
            path.write_text(__import__('json').dumps(data))
            return True, 'test'

        # Cimri/Epey artık ağdan değil yerel okuyucu besininden okunuyor (B yolu).
        self.feed = Path(self.tmp.name) / pt.FEED_FILENAME
        self.write_feed(EPEY.format(n=1), CIMRI.format(n=1))
        self.tracker = ProductTracker(self.path, SimpleNamespace(call=call), persist, reader,
                                      feed_path=self.feed)
        self.replies = []

        async def reply(text):
            self.replies.append(text)

        self.event = SimpleNamespace(chat_id=1, reply=reply)
        self.tracker.state['enabled'] = True

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def write_feed(self, *urls, price=600_000):
        import json
        items = {url: {'title': 'Örnek', 'price': price, 'seller': 'x', 'offer_count': 1,
                       'verified': True, 'read_at': int(time.time())} for url in urls}
        self.feed.write_text(json.dumps({'version': 1, 'updated_at': int(time.time()),
                                         'items': items}), encoding='utf-8')

    async def test_first_check_uses_the_site_interval_not_five_minutes(self):
        self.tracker.state['items'] = [item(EPEY.format(n=1), minutes=5)]
        self.tracker.rebuild_buckets()
        await self.tracker.check(self.tracker.state['items'][0])
        due = self.tracker.health[EPEY.format(n=1)]['due']
        import time
        remaining = due - time.monotonic()
        self.assertLess(remaining, 300)
        self.assertGreater(remaining, 200)

    async def test_menu_shows_interval_summary(self):
        self.tracker.state['items'] = [item(CIMRI.format(n=1))]
        self.tracker.rebuild_buckets()
        await self.tracker.handle(self.event, '/webtakip')
        text = self.sent[-1]['text']
        self.assertIn('Kontrol aralığı', text)
        self.assertIn('Cimri: 3 dk', text)

    async def test_no_new_command_is_added(self):
        """Yardım kataloğu büyümesin: aralık otomatik, komut yok (tests/test_bot.py:259)."""
        self.assertFalse(any(command == '/webtakiparalik' for command, _ in pt.TRACK_MENU))
        self.assertNotIn('/webtakiparalik', pt.COMMANDS)
        self.assertFalse(await self.tracker.handle(self.event, '/webtakiparalik'))

    async def test_file_level_override_is_respected_and_load_validated(self):
        self.tracker.state['items'] = [item(EPEY.format(n=1), minutes=10)]
        self.tracker.rebuild_buckets()
        self.assertEqual(self.tracker.buckets['epey']['interval'], 600)
        # Dosyadan okurken geçersiz değer sessizce kabul edilmez.
        import json as _json
        self.path.write_text(_json.dumps({'version': 1, 'enabled': True, 'items': [
            {'url': EPEY.format(n=1), 'target': 1000, 'chat_id': 1, 'title': 'x',
             'interval_minutes': 7}]}))
        with self.assertRaises(ValueError):
            pt.load_state(self.path)

    async def test_adding_products_slows_the_site_down_automatically(self):
        # 15 ürün × 10 sn = 150 sn < tek ürün hedefi (180) → henüz seyrelme yok.
        urls = [CIMRI.format(n=n) for n in range(15)]
        self.tracker.state['items'] = [item(url) for url in urls]
        self.tracker.rebuild_buckets()
        self.assertEqual(self.tracker.buckets['cimri']['interval'], 180)
        # 30 ürün × 10 sn = 300 sn → seyrelme devreye girer.
        urls = [CIMRI.format(n=n) for n in range(30)]
        self.tracker.state['items'] = [item(url) for url in urls]
        self.tracker.rebuild_buckets()
        bucket = self.tracker.buckets['cimri']
        self.assertEqual(bucket['count'], 30)
        self.assertEqual(bucket['interval'], 300)

    async def test_failure_relaxes_the_site_gap(self):
        self.tracker.state['items'] = [item(EPEY.format(n=1))]
        self.tracker.rebuild_buckets()
        # Besini bayatlat: okuma başarısız olsun (B yolunda taze veri yok = hata).
        self.write_feed()          # boş besin

        async def broken(url):
            raise ValueError('blocked')

        self.tracker.reader = broken
        await self.tracker.check(self.tracker.state['items'][0])
        bucket = self.tracker.buckets['epey']
        self.assertEqual(bucket['fail_streak'], 1)
        self.assertEqual(bucket['slowdown'], 2)      # 1. hatada hemen 2 katı
        self.assertEqual(bucket['interval'], 480)    # 240 × 2
        first_gap = self.tracker.next_site['epey']
        self.tracker.reset_gaps()
        await self.tracker.check(self.tracker.state['items'][0])
        self.assertEqual(bucket['min_gap'], 24)      # 2. hatada dip iki katına
        self.assertGreater(self.tracker.next_site['epey'], first_gap)
        self.assertLessEqual(bucket['min_gap'], 300)  # tavan
        for _ in range(4):                           # 8 kat tavanı aşılmaz
            self.tracker.reset_gaps()
            await self.tracker.check(self.tracker.state['items'][0])
        self.assertEqual(bucket['slowdown'], 8)

    async def test_slow_throttle_is_not_counted_as_a_slow_site(self):
        """Regresyon: bekleme süresi ölçülen yanıt süresine ekleniyordu →
        her turda dip sınır ikiye katlanıp site kendi kendini boğuyordu."""
        self.tracker.state['items'] = [item(EPEY.format(n=1))]
        self.tracker.rebuild_buckets()
        self.tracker.buckets['epey']['min_gap'] = 1        # ölçüm için küçült
        self.tracker.next_site['epey'] = time.monotonic() + 0.5   # 0,5 sn bekleme

        async def slow_but_ok(url):
            await asyncio.sleep(0.02)
            return {'title': 'Örnek', 'price': 600_000, 'seller': 'x', 'offers': 1}

        self.tracker.reader = slow_but_ok
        await self.tracker.check(self.tracker.state['items'][0])
        gap = self.tracker.next_site['epey'] - time.monotonic()
        # Bekleme sayılsaydı gap ≈ 2 × 0,52 = 1,04 sn olurdu; doğrusu dip sınır.
        self.assertLess(gap, 1.0, 'bekleme, ölçülen yanıt süresine eklenmemeli')

    async def test_clean_read_resets_the_streak(self):
        self.tracker.state['items'] = [item(EPEY.format(n=1))]
        self.tracker.rebuild_buckets()
        bucket = self.tracker.buckets['epey']
        bucket['fail_streak'] = 3
        bucket['clean'] = 0
        await self.tracker.check(self.tracker.state['items'][0])
        self.assertEqual(bucket['fail_streak'], 0)
        self.assertEqual(bucket['clean'], 1)

    async def test_sites_do_not_block_each_other(self):
        """Bir site yavaşlarken diğerinin aralığı etkilenmemeli."""
        self.tracker.state['items'] = [item(CIMRI.format(n=1)), item(EPEY.format(n=1))]
        self.tracker.rebuild_buckets()
        before = self.tracker.buckets['cimri']['min_gap']

        async def broken(url):
            raise ValueError('blocked')

        self.tracker.reader = broken
        await self.tracker.check(self.tracker.state['items'][1])
        self.assertEqual(self.tracker.buckets['cimri']['min_gap'], before)


if __name__ == '__main__':
    unittest.main()
