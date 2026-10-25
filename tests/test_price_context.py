"""Teklif rollerinin ve Telegram ham metin yolunun regresyonları."""
import unittest
from types import SimpleNamespace

import bot
import price_filter as pf


class OfferContextTests(unittest.TestCase):
    CASES = [
        ('Chakra Waved Pamuk El Havlusu 30x40 Cm Bej', '99 TL 🚚 Kargo Bedava', 99,
         ['normal']),
        ('Akmina B Vitaminli Karadut & Frenk Üzümü Aromalı Maden Suyu 6x1 L',
         "225 TL / 300 TL'ye 50 TL kuponla 2 adet alımda 200 TL", 200,
         ['normal', 'threshold', 'discount_amount', 'multi_unit']),
        ('Siyah Kapüşonlu Fermuarlı Oversize Kadın Eşofman Takım',
         '956 TL / Ürün altındaki 300 TL kuponla 656 TL', 656,
         ['normal', 'discount_amount', 'product_coupon']),
        ('Bingo Tek Fısta Ultra Güçlü Yağ Çözücü Sprey 900 ml',
         "100 TL / 500 TL'ye 100 TL kuponla 5 adet alımda 80 TL", 80,
         ['normal', 'threshold', 'discount_amount', 'multi_unit']),
        ('Yayla Yemek Hazır Salsa Soslu Tavuk 200 Gram',
         '114 TL (2 Adet Alımda 57 TL)', 57, ['normal', 'multi_unit']),
        ('DeFacto Su İtici Slim Fit Mont',
         '600 TL / Sepette 150 TL kuponla 450 TL / Ürün altındaki 250 TL kuponla 2 adet alımda 475 TL',
         450, ['normal', 'discount_amount', 'basket_coupon', 'discount_amount', 'multi_unit']),
        ('Uni Baby Uni Wipes Comfort Zeytinyağlı Islak Mendil 12X90 1080 Yaprak',
         "423 TL / 500 TL'ye 100 TL kuponla 2 adet alımda 373 TL", 373,
         ['normal', 'threshold', 'discount_amount', 'multi_unit']),
    ]

    def test_examples_and_variants(self):
        for title, offer, expected, roles in self.CASES:
            for currency in ['TL', '₺']:
                for space in [' ', '\u00a0', '\u202f']:
                    text = 'Ürün: ' + title + '\n\n🎯 ' + offer.replace('TL', currency).replace(' ', space)
                    with self.subTest(title=title, currency=currency, space=space):
                        candidates = pf.find_candidates(text)
                        self.assertEqual([c.role for c in candidates], roles)
                        self.assertTrue(all(c.reason for c in candidates))
                        info = pf.extract_price(text)
                        self.assertEqual(info.value, expected)
                        self.assertIn(info.confidence, {'medium', 'high'})
                        self.assertTrue(pf.price_matches(text, expected)[0])
                        self.assertFalse(pf.price_matches(text, expected - 1)[0])

    def test_raw_telegram_path(self):
        text = 'Ürün: Chakra Waved Pamuk El Havlusu 30x40 Cm Bej\n\n🎯 99 TL 🚚 Kargo Bedava'
        message = SimpleNamespace(message=text, entities=[], media=None)
        event = SimpleNamespace(message=message, raw_text=text)
        self.assertEqual(bot.message_text(event), text)
        cleaned = bot.sanitize_message(event)
        body = bot.message_text(cleaned)
        self.assertIn('99 TL', body)
        title, info = bot.smart_header_info(event, cleaned, body)
        self.assertIn('Chakra', title)
        self.assertEqual(info.value, 99)

    def test_total_and_ambiguous_prices_do_not_trigger_dm(self):
        for text in ['2 adet alımda toplam 100 TL', '2 adet alımda 100 TL toplam',
                     'Ürün 100 TL / 90 TL']:
            with self.subTest(text=text):
                self.assertEqual(pf.extract_price(text).confidence, 'low')
                self.assertFalse(pf.price_matches(text, 1000)[0])
        self.assertEqual(pf.find_candidates('2 adet alımda toplam 100 TL')[0].role, 'multi_total')

    def test_old_shipping_and_unit_roles(self):
        text = 'Eski 900 TL / Yeni 700 TL\nKargo: 30 TL\nAdet başı 80 TL'
        cs = pf.find_candidates(text)
        self.assertEqual([c.role for c in cs], ['old', 'discounted', 'unrelated', 'unit'])
        self.assertEqual(pf.extract_price(text).value, 80)
        text = '🎯 900 TL / 700 TL'
        self.assertEqual(pf.find_candidates(text, [(2, 8)])[0].role, 'old')

    def test_purchase_phrases(self):
        for text, role, confidence in [
            ('2 adet alımda 200 TL', 'multi_unit', 'low'),
            ('2 adet alımda toplam 200 TL', 'multi_total', 'low'),
            ('2 adet alımda tanesi 200 TL', 'multi_unit', 'medium'),
            ('adet başı 200 TL', 'unit', 'medium'),
            ('2 adet 200 TL', 'conditional', 'low'),
        ]:
            with self.subTest(text=text):
                self.assertEqual(pf.extract_price(text).value, 200)
                self.assertEqual(pf.extract_price(text).confidence, confidence)
                self.assertEqual(pf.find_candidates(text)[0].role, role)
                self.assertEqual(pf.price_matches(text, 200)[0], confidence == 'medium')
                cfg = {'dm_enabled': False, 'dm_keywords': [], 'dm_price_max': ['200']}
                self.assertEqual(bot.dm_should_send(cfg, text), confidence == 'medium')
                self.assertFalse(pf.price_matches(text, 199)[0])

    def test_mixed_roles_not_numeric_minimum(self):
        text = ("Eski 90 TL\nGüncel fiyat: 600 TL / Sepette 150 TL kuponla 450 TL / "
                "Ürün altındaki 250 TL kuponla 2 adet alımda 475 TL\n"
                "500 TL'ye 100 TL kupon\nKargo: 20 TL")
        self.assertEqual(pf.extract_price(text).value, 450)
        self.assertEqual([c.role for c in pf.find_candidates(text)],
                         ['old', 'normal', 'discount_amount', 'basket_coupon',
                          'discount_amount', 'multi_unit', 'threshold', 'discount_amount', 'unrelated'])
        self.assertFalse(pf.price_matches(text, 449)[0])
        self.assertTrue(pf.price_matches(text, 450)[0])

    def test_multiple_final_prices_are_not_silently_minimized(self):
        for text in ['600 TL / Sepette 100 TL kuponla 500 TL / Ürün altındaki 200 TL kuponla 400 TL',
                     '600 TL / Ürün altındaki 200 TL kuponla 400 TL / Sepette 100 TL kuponla 500 TL']:
            self.assertEqual(pf.extract_price(text).confidence, 'low')
            self.assertFalse(pf.price_matches(text, 1000)[0])

    def test_coupon_only_and_missing_price(self):
        cfg = {'dm_enabled': False, 'dm_keywords': [], 'dm_price_max': ['1000']}
        for text in ["500 TL'ye 100 TL kupon", 'Sepette 150 TL indirim', 'Kupon: 50 TL',
                     '🎯 Fiyat yakında 🚚 Kargo Bedava', 'Kargo: 30 TL']:
            with self.subTest(text=text):
                self.assertIsNone(pf.extract_price(text).value)
                self.assertFalse(pf.price_matches(text, 1000)[0])
                self.assertFalse(bot.dm_should_send(cfg, text))

    def test_chakra_final_notification(self):
        from unittest.mock import patch
        text = 'Ürün: Chakra Waved Pamuk El Havlusu 30x40 Cm Bej\n\n🎯 99 TL 🚚 Kargo Bedava'
        event = SimpleNamespace(message=SimpleNamespace(message=text, entities=[], media=None), raw_text=text)
        with patch.object(bot, 'SMART_HEADER', True):
            result = bot.compose_message(event, message_link='https://t.me/c/1/2', source_name='Kaynak')
        output = result['text']
        self.assertIn(text, output)
        # Fiyat en başta, ayraçtan sonra ürün adı geliyor (tek satır).
        self.assertTrue(output.startswith('💰 99 TL — Chakra Waved Pamuk El Havlusu 30x40 Cm Bej'), output)
        self.assertEqual(result['price'], '💰 99 TL')
        self.assertEqual(result['title'], 'Chakra Waved Pamuk El Havlusu 30x40 Cm Bej')
        self.assertNotIn('💰 ?', output)
        self.assertTrue(output.endswith('Kaynak'))
        self.assertEqual(event.message.message, text)

    def test_confidence_through_notification_dm_and_dedup(self):
        """Raw event -> sanitize/entities -> real notification + DM + dedup."""
        from unittest.mock import patch
        from telethon.tl import types
        cases = [
            ('2 adet alımda 200 TL', 'low', 200),
            ('300 TL / 2 adet alımda 200 TL', 'medium', 200),
            ('2 adet alımda toplam 200 TL', 'low', 200),
            ('2 adet alımda tanesi 200 TL', 'medium', 200),
            ('adet başı 200 TL', 'medium', 200),
            ('2 adet 200 TL', 'low', 200),
            ('Eski fiyat: 200 TL', 'low', 200),
            ('Fiyat: 200 TL', 'high', 200),
            ('200 TL', 'high', 200),
            ('Sepette 50 TL indirim', 'none', None),
            ("500 TL'ye 50 TL kupon", 'none', None),
            # Daha düşük cond tutar, low nihai çelişkiyi aşamaz.
            ('600 TL / Sepette 100 TL kuponla 500 TL / Ürün altındaki 200 TL kuponla 400 TL / 2 adet alımda 300 TL',
             'low', 300),
        ]
        for offer, confidence, value in cases:
            text = '🛍 Deneme Pamuk Havlu 30x40 Cm\n\n🎯 ' + offer
            with self.subTest(offer=offer):
                event = SimpleNamespace(message=SimpleNamespace(message=text, entities=[], media=None), raw_text=text)
                cleaned = bot.sanitize_message(event)
                body = bot.message_text(cleaned)
                strike = pf.strike_spans_from_entities(body, bot.entities_for_text(cleaned, body))
                _, info = bot.smart_header_info(event, cleaned, body)
                self.assertEqual((info.confidence, info.value), (confidence, value))
                reliable = confidence in {'high', 'medium'}
                with patch.object(bot, 'SMART_HEADER', True):
                    composed = bot.compose_message(event)
                output = composed['text']
                header = composed['header']
                if reliable:
                    # Güvenilir fiyat üst satırın EN BAŞINA yazılır.
                    self.assertEqual(header, bot.build_header_line('💰 ' + pf.format_price(value), composed['title']))
                    self.assertNotIn('💰 ?', output)
                else:
                    # Güven yok: üst satırda yalnızca soru işareti, uzun uyarı yok.
                    self.assertTrue(header.startswith('💰 ?'), header)
                    self.assertNotIn('Fiyat tespit', output)
                    # Kaynak gövdedeki tutarlar silinmez; üretilmiş fiyat başlığı olmamalı.
                    self.assertNotIn('💰 ' + pf.format_price(value or 0) + ' — ', output)
                self.assertEqual(bot.offer_terms(event)[0], value if reliable else None)
                for threshold in [1000, (value - 1) if value else 0]:
                    cfg = {'dm_enabled': False, 'dm_keywords': [], 'dm_price_max': [str(threshold)]}
                    self.assertEqual(bot.dm_should_send(cfg, body, strike),
                                     reliable and value <= threshold)
                if not reliable:
                    # Bağımsız kelime kuralı yine DM gönderebilir; fiyat tetikleyicisi değildir.
                    cfg = {'dm_enabled': True, 'dm_keywords': ['havlu'], 'dm_price_max': ['0']}
                    self.assertTrue(bot.dm_should_send(cfg, body, strike))

        text = '🛍 Deneme Havlu\n🎯 200 TL'
        start = len(text[:text.index('200')].encode('utf-16-le')) // 2
        event = SimpleNamespace(message=text, raw_text=text, entities=[types.MessageEntityStrike(offset=start, length=6)], media=None)
        with patch.object(bot, 'SMART_HEADER', True):
            struck_header = bot.compose_message(event)['header']
        self.assertTrue(struck_header.startswith('💰 ?'), struck_header)
        cleaned = bot.sanitize_message(event)
        body = bot.message_text(cleaned)
        strike = pf.strike_spans_from_entities(body, bot.entities_for_text(cleaned, body))
        self.assertFalse(bot.dm_should_send({'dm_price_max': ['1000']}, body, strike))
        self.assertIsNone(bot.offer_terms(event)[0])

    def test_original_full_context_and_total_counterexamples(self):
        # Aynı tam gerçek mesajlar: URL, paket boyutu, başlık ve emoji korunur.
        from test_price_filter import REAL_WORLD
        for marker, expected in [('Bref WC Power', 48), ('NIVEA Cellular', 179), ('Köyceğiz Yöresel', 256)]:
            text = next(text for text, _, _ in REAL_WORLD if marker in text)
            with self.subTest(marker=marker):
                self.assertEqual(pf.extract_price(text).value, expected)
                self.assertEqual(pf.extract_price(text).confidence, 'medium')
                self.assertEqual(bot.offer_terms(text)[0], expected)
                # Açık toplam değişikliği birim fiyat diye seçilmemeli.
                import re
                total_text = re.sub(r'(adet alımda)\s+(\d+ TL)', r'\1 toplam \2', text, flags=re.IGNORECASE)
                self.assertNotEqual(total_text, text)
                cs = pf.find_candidates(total_text)
                self.assertTrue(any(c.role == 'multi_total' and c.value == expected for c in cs))
                self.assertNotEqual(pf.extract_price(total_text).value, expected)

    def test_seven_examples_through_production_consumers(self):
        from unittest.mock import patch
        for title, offer, expected, _ in self.CASES:
            text = 'Ürün: ' + title + '\n\n🎯 ' + offer
            event = SimpleNamespace(message=text, raw_text=text, media=None, entities=[])
            with self.subTest(title=title), patch.object(bot, 'SMART_HEADER', True):
                composed = bot.compose_message(event)
                output = composed['text']
                # Üst satır: fiyat — ürün adı (tek satır, fiyat en başta).
                self.assertEqual(
                    composed['header'],
                    bot.build_header_line('💰 ' + pf.format_price(expected), composed['title']),
                )
                self.assertTrue(output.startswith(composed['header'] + '\n\n'), output)
                self.assertNotIn('💰 ?', output)
                self.assertEqual(bot.offer_terms(event)[0], expected)
                cleaned = bot.sanitize_message(event)
                body = bot.message_text(cleaned)
                for threshold, hit in [(expected, True), (expected - 1, False)]:
                    self.assertEqual(bot.dm_should_send({'dm_price_max': [str(threshold)]}, body), hit)
