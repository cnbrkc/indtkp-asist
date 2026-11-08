"""Teşhis aracı: ağ yok, yalnız karar mantığı test edilir."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

import aggregator_diag as diag


class UrlTests(unittest.TestCase):
    def test_only_aggregator_product_pages_are_accepted(self):
        self.assertEqual(diag.check_url('https://www.epey.com/televizyon/x.html?utm=1'),
                         'https://www.epey.com/televizyon/x.html')
        for url in ('https://www.hepsiburada.com/x-p-1', 'http://www.epey.com/a.html',
                    'https://www.epey.com:8080/a.html', 'https://user:p@www.epey.com/a.html',
                    'ftp://www.cimri.com/a'):
            with self.assertRaises(ValueError, msg=url):
                diag.check_url(url)


class VerdictTests(unittest.TestCase):
    def rows(self, *statuses):
        return [(f'v{i}', {'status': s}) for i, s in enumerate(statuses)]

    def test_all_ok(self):
        self.assertIn('engel yok', diag.verdict(self.rows(200, 200, 200, 200)))

    def test_header_difference_is_named_when_some_pass(self):
        text = diag.verdict(self.rows(403, 403, 200, 403))
        self.assertIn('BAŞLIK FARKI', text)

    def test_all_forbidden_points_to_ip(self):
        text = diag.verdict(self.rows(403, 403, 403, 403))
        self.assertIn('IP', text)
        self.assertIn('403', text)

    def test_mixed_is_honest(self):
        # Biri okundu ama hepsi değil → yine "başlık farkı" (düzeltilebilir) der.
        self.assertIn('BAŞLIK FARKI', diag.verdict(self.rows(403, None, 429, 200)))
        # Hiçbiri okunmadı ve hepsi 403 değil → karma, dürüstçe söylenir.
        self.assertIn('KARMA', diag.verdict(self.rows(403, None, 0, 500)))

    def test_variant_set_covers_both_user_agents(self):
        agents = {agent for _, agent, _ in diag.VARIANTS}
        self.assertEqual(agents, {diag.CURRENT_UA, diag.BROWSER_UA})
        self.assertEqual(len(diag.VARIANTS), 4)


class AttemptTests(unittest.TestCase):
    def test_403_is_captured_without_raising(self):
        import io
        import urllib.error
        error = urllib.error.HTTPError('https://www.epey.com/x.html', 403, 'Forbidden',
                                       {'server': 'cloudflare'}, io.BytesIO(b''))
        with mock.patch.object(diag.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = error
            row = diag.attempt('https://www.epey.com/x.html', diag.CURRENT_UA, {})
        self.assertEqual(row['status'], 403)
        self.assertEqual(row['headers'].get('server'), 'cloudflare')

    def test_redirect_is_reported_not_followed(self):
        with mock.patch.object(diag.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = ValueError('Yönlendirme reddedildi')
            row = diag.attempt('https://www.epey.com/x.html', diag.CURRENT_UA, {})
        self.assertEqual(row['status'], '3xx')

    def test_gzip_body_is_decoded(self):
        import gzip
        raw = gzip.compress('<title>Test</title>'.encode())
        self.assertIn(b'Test', diag.decode(raw, 'gzip'))


if __name__ == '__main__':
    unittest.main()


class AnnotateTests(unittest.TestCase):
    """Log arşivi API'den okunamadığında sonuç annotation'dan görünebilmeli."""

    def test_annotate_prints_a_warning_with_the_verdict(self):
        import contextlib
        import io
        rows = [{'status': 403, 'ms': 140, 'bytes': 0}] * 4
        buffer = io.StringIO()
        with mock.patch.object(diag, 'attempt', side_effect=rows), \
             mock.patch.object(diag.time, 'sleep'), \
             contextlib.redirect_stdout(buffer):
            diag.main(['--url', 'https://www.epey.com/televizyon/x.html', '--annotate'])
        output = buffer.getvalue()
        self.assertIn('::warning title=www.epey.com::', output)
        self.assertIn('::notice title=', output)
        self.assertIn('HTTP 403', output)
        self.assertIn('IP', output)

    def test_verdict_names_the_mixed_statuses_instead_of_hiding_them(self):
        text = diag.verdict([('a', {'status': 403}), ('b', {'status': 500}),
                             ('c', {'status': None}), ('d', {'status': 0})])
        self.assertIn('KARMA', text)
        self.assertIn('500', text)
