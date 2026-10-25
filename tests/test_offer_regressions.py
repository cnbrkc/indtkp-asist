"""Yeni kanal kalıpları: fiyat rolleri dedup'tan bağımsız sınanır."""
import unittest
import urllib.parse
from types import SimpleNamespace
from unittest.mock import patch

import bot
import price_filter as pf


CASES = [
    ('OGX Biotin & Kolajen Dolgunlaştırıcı Sülfatsız Şampuan 385 ml + Saç Bakım Kremi 385 ml',
     '519 TL\nTakip Kuponu + GUZELLIK200 kodu ile (2 Adet Alımda) 1 adet 369 TL', 369, 'medium'),
    ('Bargello No.122 Kadın 210 ml Body Mist + No.384 Kadın 210 ml Body Mist',
     'GUZELLIK200 koduyla 3 adet alımda 250 TL', 250, 'low'),
    ('Uni Baby Uni Wipes Comfort Zeytinyağlı Islak Mendil 12X90 1080 Yaprak',
     '2 Adet Alımda 100₺ Kuponla Adedi 373₺', 373, 'medium'),
    ('Fropie Proteinli Granola - Yer Fıstıklı & Kinoa Patlaklı 240gr',
     'Fiyat : 189 TL / 2 al 1 öde ile 94 TL', 94, 'medium'),
    ('Busstier 25 Parça Tornavida Tamir Seti, Deri Cüzdan Kılıflı, Bilgisayar, Telefon ve Saat Tamir Kiti',
     'Fiyat: 179,00 TL\nKampanyalar 450TL seçili ürün satın aldığınızda %5 tasarruf edin\n'
     'KAMPANYA: 170,05 TL (3 adet) // 450TL seçili ürün satın aldığınızda %5 tasarruf edin', 179, 'high'),
    ('E-Garaj Tesla Model Y ile Uyumlu Bagaj Yazısı (TESLA) Gümüş',
     'Fiyat: 560,28 TL\nÖdeme esnasında %15 tutarında tasarruf edin\nKAMPANYA: 504,25 TL (2 adet)\n'
     '1000 TL seçili ürün satın aldığınızda %10 tasarruf edin', 560.28, 'high'),
]


class NewPricePatterns(unittest.TestCase):
    def test_new_examples_and_production_consumers(self):
        for title, offer, value, confidence in CASES:
            for currency in ['TL', '₺']:
                text = title + '\n\n' + offer.replace('TL', currency)
                with self.subTest(title=title, currency=currency):
                    info = pf.extract_price(text)
                    self.assertEqual((info.value, info.confidence), (value, confidence))
                    event = SimpleNamespace(message=text, raw_text=text, entities=[], media=None)
                    reliable = confidence in {'high', 'medium'}
                    with patch.object(bot, 'SMART_HEADER', True):
                        composed = bot.compose_message(event)
                    output = composed['text']
                    first_line = output.splitlines()[0]
                    if reliable:
                        # Fiyat en başta, " - " ayracıyla ürün adına bağlanır; emoji yok.
                        self.assertEqual(
                            first_line,
                            bot.build_header_line(bot.header_price_text(value), composed['title']),
                        )
                        self.assertNotIn('💰', first_line)
                    else:
                        self.assertTrue(first_line.startswith('❓'), first_line)
                        self.assertNotIn('Fiyat tespit', first_line)
                    # Üst satıra yazılan fiyat arama sorgusuna karışmaz:
                    # Google/Akakçe/Cimri salt fiyatsız ürün adını arar.
                    markup = bot.build_inline_keyboard(event) or {'inline_keyboard': []}
                    search_urls = [button['url'] for row in markup['inline_keyboard'] for button in row]
                    self.assertEqual(len(search_urls), 3, search_urls)
                    expected_query = bot._search_query(event)
                    for url in search_urls:
                        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get('q', [''])[0]
                        self.assertEqual(query, expected_query, url)
                        self.assertNotIn('💰', query)
                        self.assertNotIn('₺', query)
                        self.assertNotIn('❓', query)
                        if reliable:
                            self.assertNotIn(pf.format_price(value), query)
                    self.assertEqual(bot.offer_terms(event)[0], value if reliable else None)
                    for threshold, hit in [(value, reliable), (value - 1, False)]:
                        self.assertEqual(pf.price_matches(text, threshold)[0], hit)
                        self.assertEqual(bot.dm_should_send({'dm_price_max': [str(threshold)]}, text), hit)
                    self.assertIn(title.split()[0], bot.smart_dedup_title(event))

    def test_explicit_units_not_hidden_by_coupon(self):
        for phrase in ['1 adet', 'adedi', 'tanesi', 'adet başı']:
            for space in [' ', '\u00a0', '\u202f']:
                text = ('2 adet alımda 100 TL kuponla ' + phrase + ' 373 TL').replace(' ', space)
                with self.subTest(phrase=phrase, space=space):
                    cs = pf.find_candidates(text)
                    self.assertEqual([c.role for c in cs], ['discount_amount', 'multi_unit'])
                    self.assertEqual(pf.extract_price(text).value, 373)
                    self.assertEqual(pf.extract_price(text).confidence, 'medium')

    def test_ambiguous_quantity_and_campaigns_remain_untrusted(self):
        for text in ['3 adet alımda 250 TL', 'KOD200 koduyla 3 adet alımda 250 TL',
                     'KAMPANYA: 170,05 TL (3 adet)', 'KAMPANYA: 504,25 TL (2 adet)',
                     '2 al 1 öde ile 94 TL',
                     'KAMPANYA: 170,05 TL (3 adet)\nKAMPANYA: 160 TL (4 adet)']:
            with self.subTest(text=text):
                self.assertEqual(pf.extract_price(text).confidence, 'low')
                self.assertFalse(pf.price_matches(text, 10000)[0])
                self.assertIsNone(bot.offer_terms(text)[0])

    def test_threshold_and_campaign_roles(self):
        cs = pf.find_candidates(CASES[4][1])
        self.assertEqual([c.role for c in cs], ['normal', 'threshold', 'campaign_quantity', 'threshold'])
        self.assertEqual([c.role for c in pf.find_candidates(CASES[5][1])],
                         ['normal', 'campaign_quantity', 'threshold'])
        # Fiyat etiketi olmaması kampanyayı genel fiyata dönüştürmemeli.
        self.assertEqual(pf.extract_price(CASES[4][1].replace('Fiyat: ', '')).value, 179)
        self.assertEqual(pf.extract_price(CASES[5][1].replace('Fiyat: ', '')).value, 560.28)

    def test_generic_buy_pay_rule_and_non_unit_totals(self):
        for count, pay, normal, effective in [(2, 1, 189, 94), (3, 2, 120, 80), (4, 3, 200, 150)]:
            text = f'Fiyat: {normal} TL / {count} al {pay} öde ile {effective} TL'
            self.assertEqual(pf.extract_price(text).value, effective)
            self.assertEqual(pf.extract_price(text).confidence, 'medium')
        self.assertEqual(pf.extract_price('519 TL / 2 adet alımda toplam 369 TL').value, 519)
        for text in ['189 TL / 2 al 1 öde ile toplam 94 TL', '189 TL / 2 al 1 öde ile 94 TL toplam']:
            self.assertEqual(pf.extract_price(text).value, 189)
            self.assertEqual(pf.find_candidates(text)[-1].role, 'multi_total')

    def test_old_shipping_and_discount_are_not_promoted(self):
        for text in ['Eski adedi 373 TL', 'Kargo adedi 373 TL', '100 TL kuponla adedi 373 TL kupon',
                     'Taksit adedi 373 TL', 'Kargo tanesi 373 TL', 'Taksit 1 adet 373 TL',
                     '100 TL kuponla kargo adedi 373 TL']:
            with self.subTest(text=text):
                self.assertFalse(pf.price_matches(text, 1000)[0])
        text = 'Eski 80 TL\nKargo: 10 TL\n519 TL\n2 adet alımda 100 TL kuponla adedi 369 TL'
        self.assertEqual(pf.extract_price(text).value, 369)


class DedupIdentityRegressions(unittest.TestCase):
    TITLE = 'Next YE-55GFSG8-4K 55" 140 Ekran UHD 4K Google TV'

    def test_rounding_not_a_new_offer_but_meaningful_drop_is(self):
        for a, b in [(13485.92, 13485), (13485, 13485.92)]:
            self.assertFalse(bot.better_or_different_offer((a, ''), (b, '')))
        for a, b in [(13485, 13400), (10, 9.1), (200, 199), (13485.92, 13485.5)]:
            self.assertTrue(bot.better_or_different_offer((a, ''), (b, '')))
        self.assertTrue(bot.better_or_different_offer((13485, ''), (13485, 'prime')))

    def test_metadata_and_punctuation_do_not_change_title(self):
        base = self.TITLE + '\nFiyat: 13.485,92 TL'
        for extra in ['\nSatıcı: Amazon', '\nSatıcı: Amazon Depo', '\nKargo Bedava']:
            self.assertEqual(bot.dedup_key(bot.smart_dedup_title(base)),
                             bot.dedup_key(bot.smart_dedup_title(base + extra)))
        self.assertEqual(bot.dedup_key(self.TITLE), bot.dedup_key(self.TITLE.replace('-', ' ')))

    def test_variants_and_explicit_product_conditions_remain_distinct(self):
        for title in [self.TITLE.replace('55GFSG8', '55GFSG9'), self.TITLE.replace('55" 140', '65" 165'),
                      self.TITLE + ' Beyaz', self.TITLE + ' Siyah', self.TITLE + ' 128 GB', self.TITLE + ' 256 GB']:
            self.assertNotEqual(bot.dedup_key(self.TITLE), bot.dedup_key(title))
        base = self.TITLE + '\nFiyat: 13.485 TL'
        keys = [bot.dedup_offer_key(self.TITLE, bot.offer_terms(base + extra)) for extra in
                ['', '\nÜrün durumu: İkinci el', '\nÜrün durumu: İkinci el yeni gibi', '\nÜrün durumu: Yenilenmiş']]
        self.assertEqual(len(set(keys)), 4)

    def test_history_source_name_is_not_product_condition(self):
        title = self.TITLE
        raw = title + '\nFiyat: 13.485 TL'
        notice = bot.compose_message(raw, message_link='https://t.me/c/1/2', source_name='Amazon Depo - İkinci El Fırsatlar')['text']
        self.assertEqual(bot.offer_terms(raw), bot.offer_terms(notice))
        badge_notice = notice.rsplit('\n\n', 1)[0] + '\n\n📌 4 kere paylaşıldı: Amazon Depo - İkinci El Fırsatlar'
        self.assertEqual(bot.offer_terms(raw), bot.offer_terms(badge_notice))

    def test_exact_rounding_limits_and_dm_are_independent(self):
        cases = [
            (13485.92, 13485, False),
            (13485.92, 13484, True),
            (13486, 13485, False),  # tam 1 TL: sınır dahil
            (13486.01, 13485, True),
            (1000.09, 1000, False),  # %0,01 daha dar üst sınır
            (1000.11, 1000, True),
            (1000.09, 1000.01, True),  # iki kesirli fiyat: eski 0,01 TL
            (200, 199, True),
        ]
        for old, new, different in cases:
            with self.subTest(old=old, new=new):
                self.assertEqual(bot.better_or_different_offer((old, ''), (new, '')), different)
        # Dedup toleransı fiyat değerini ve DM fiyat eşiğini yuvarlamaz.
        text = self.TITLE + '\nFiyat: 13.485,92 TL'
        self.assertEqual(pf.extract_price(text).value, 13485.92)
        self.assertFalse(pf.price_matches(text, 13485)[0])
        self.assertFalse(bot.dm_should_send({'dm_price_max': ['13485']}, text))
        self.assertTrue(bot.dm_should_send({'dm_price_max': ['13485,92']}, text))

    def test_unknown_status_is_absence_of_evidence_not_new(self):
        base = self.TITLE + '\nFiyat: 13.485 TL'
        unknown = bot.offer_terms(base)
        like_new = bot.offer_terms(base + '\nİkinci El: Yeni Gibi')
        refurbished = bot.offer_terms(base + '\nÜrün durumu: Yenilenmiş')
        self.assertNotIn('durum:', unknown[1])
        self.assertIn('durum:ikinci el yeni gibi', like_new[1])
        self.assertIn('durum:yenilenmiş', refurbished[1])
        self.assertNotEqual(like_new[1], refurbished[1])
        # Satıcı adı tek başına ürün durumunun kanıtı değildir.
        self.assertEqual(bot.offer_terms(base + '\nSatıcı: Amazon Depo'), unknown)
        # Aynı başlık/fiyat iki farklı satış ilanı da olabilir: wildcard eşleşmesi yok.
        self.assertNotEqual(bot.dedup_offer_key(self.TITLE, unknown),
                            bot.dedup_offer_key(self.TITLE, like_new))

    def test_seller_metadata_policy_and_state_match(self):
        base = self.TITLE + '\nFiyat: 13.485 TL'
        a = bot.offer_terms(base + '\nSatıcı: Mağaza A\nİkinci El: Yeni Gibi')
        b = bot.offer_terms(base + '\nSatıcı: Mağaza B\nİkinci El: Yeni Gibi')
        self.assertEqual(a, b)  # mevcut politika: satıcı adı bağımsız teklif anahtarı değil
        self.assertFalse(bot.better_or_different_offer(a, b))
        self.assertNotEqual(bot.dedup_offer_key(self.TITLE, a),
                            bot.dedup_offer_key(self.TITLE.replace('55GFSG8', '55GFSG9'), b))
