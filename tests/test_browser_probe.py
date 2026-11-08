"""Gerçek tarayıcı ölçümü: challenge gerçekten geçilebiliyor mu?

Bulgu: Epey 'Just a moment...', Cimri 'Güvenlik doğrulaması' döndürüyor.
Bu IP engeli değil, JS doğrulaması. Testler, aracın sonucu ASLA abartmadığını
garanti eder: challenge kalırsa 'geçti' demez.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parent.parent / 'tools'
sys.path.insert(0, str(TOOLS))

import browser_probe as probe  # noqa: E402


class ClassifyTests(unittest.TestCase):
    def test_cloudflare_challenge_is_not_readable(self):
        row = probe.classify('Just a moment...', '<html>checking your browser</html>')
        self.assertTrue(row['challenged'])
        self.assertFalse(row['readable'])

    def test_cimri_security_check_is_not_readable(self):
        row = probe.classify('Güvenlik doğrulaması | Cimri', '<html></html>')
        self.assertTrue(row['challenged'])
        self.assertFalse(row['readable'])

    def test_attention_required_is_not_readable(self):
        self.assertFalse(probe.classify('Attention Required! | Cloudflare',
                                        '<html></html>')['readable'])

    def test_real_product_page_with_jsonld_is_readable(self):
        body = ('<script type="application/ld+json">{"price": "42999.00"}</script>')
        row = probe.classify('TCL 65C6K Fiyatı | Epey', body)
        self.assertFalse(row['challenged'])
        self.assertTrue(row['readable'])
        self.assertIn('application/ld+json', row['price_hints'])

    def test_page_without_jsonld_is_not_readable(self):
        self.assertFalse(probe.classify('TCL 65C6K', '<html><body>fiyat yok</body></html>')
                         ['readable'])


class ExtractPriceTests(unittest.TestCase):
    def test_reads_numeric_price(self):
        self.assertEqual(probe.extract_price('{"price": 42999.0}'), 42999.0)

    def test_reads_string_price(self):
        self.assertEqual(probe.extract_price('{"price": "42999.00"}'), 42999.0)

    def test_reads_turkish_formatted_price(self):
        self.assertEqual(probe.extract_price('{"price": "42.999,00"}'), 42999.0)

    def test_plain_integer_price(self):
        self.assertEqual(probe.extract_price('{"price": 42999}'), 42999.0)

    def test_thousands_separator_is_understood(self):
        self.assertEqual(probe.extract_price('{"price": "1.299"}'), 1299.0)
        self.assertEqual(probe.extract_price('{"price": "1,299"}'), 1299.0)

    def test_garbage_is_none_not_crash(self):
        self.assertIsNone(probe._to_number('abc'))
        self.assertIsNone(probe._to_number(''))

    def test_missing_price_is_none_not_invented(self):
        self.assertIsNone(probe.extract_price('<html>fiyat yok</html>'))
        self.assertIsNone(probe.extract_price(''))
        self.assertIsNone(probe.extract_price('{"price": "0"}'))


class SafetyTests(unittest.TestCase):
    def test_foreign_host_is_refused(self):
        row = probe.probe('https://www.trendyol.com/x', timeout=1)
        self.assertIn('izin verilmeyen host', row['error'])

    def test_missing_browser_is_reported_honestly(self):
        with mock.patch.dict(sys.modules, {'nodriver': None}):
            row = probe.probe('https://www.epey.com/x.html', timeout=1)
        self.assertIn('error', row)

    def test_cli_never_crashes_on_missing_browser(self):
        code = probe.main(['--url', 'https://www.epey.com/x.html', '--timeout', '1'])
        self.assertEqual(code, 0)


if __name__ == '__main__':
    unittest.main()
