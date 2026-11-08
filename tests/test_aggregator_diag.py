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

    def test_variant_set_covers_both_user_agents_and_both_clients(self):
        agents = {agent for _, _, agent, _ in diag.VARIANTS}
        clients = {client for _, client, _, _ in diag.VARIANTS}
        self.assertEqual(agents, {diag.CURRENT_UA, diag.BROWSER_UA})
        self.assertEqual(clients, {'urllib', 'curl', 'cffi', 'cffi-safari'})
        self.assertEqual(len(diag.VARIANTS), 8)

    def test_client_difference_is_reported_as_such(self):
        """curl okur, urllib 403 alırsa engel IP değil istemci parmak izidir."""
        rows = [('1 urllib · mevcut UA', {'status': 403}),
                ('2 urllib · tarayıcı UA', {'status': 403}),
                ('3 urllib · tarayıcı UA + tam başlık', {'status': 403}),
                ('4 urllib · mevcut UA + tam başlık', {'status': 403}),
                ('5 curl · mevcut UA', {'status': 200}),
                ('6 curl · tarayıcı UA + tam başlık', {'status': 200})]
        text = diag.verdict(rows)
        self.assertIn('İSTEMCİ FARKI', text)
        self.assertIn('curl', text)


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
        rows = [{'status': 403, 'ms': 140, 'bytes': 0}] * 6
        buffer = io.StringIO()
        with mock.patch.object(diag, 'attempt', side_effect=rows), \
             mock.patch.object(diag, 'attempt_curl', side_effect=rows), \
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


class CurlTests(unittest.TestCase):
    def test_curl_never_follows_redirects(self):
        """-L kullanılmamalı: yönlendirme takibi tracker kuralına da aykırı."""
        import inspect
        source = inspect.getsource(diag.attempt_curl)
        self.assertNotIn("'-L'", source)
        self.assertIn('--max-time', source)

    def test_curl_missing_binary_is_reported_not_raised(self):
        with mock.patch.object(diag.subprocess, 'run', side_effect=FileNotFoundError()):
            row = diag.attempt_curl('https://www.epey.com/x.html', diag.CURRENT_UA, {})
        self.assertIsNone(row['status'])
        self.assertEqual(row['error'], 'FileNotFoundError')

    def test_curl_parses_status_and_size(self):
        fake = SimpleNamespace(returncode=0, stdout='403 0 0.12', stderr='')
        with mock.patch.object(diag.subprocess, 'run', return_value=fake):
            row = diag.attempt_curl('https://www.epey.com/x.html', diag.CURRENT_UA, {})
        self.assertEqual(row['status'], 403)
        self.assertEqual(row['bytes'], 0)


class OutputRoutingTests(unittest.TestCase):
    """Regresyon: annotation satırları dosyaya yönlendirilince GitHub hiç görmüyordu."""

    def run_main(self, tmpdir, extra):
        rows = [{'status': 403, 'ms': 140, 'bytes': 0}] * 6
        out = tmpdir / 'summary.md'
        js = tmpdir / 'diag.json'
        import contextlib
        import io
        buffer = io.StringIO()
        argv = ['--url', 'https://www.epey.com/televizyon/x.html',
                '--summary-out', str(out), '--json-out', str(js)] + extra
        with mock.patch.object(diag, 'attempt', side_effect=rows), \
             mock.patch.object(diag, 'attempt_curl', side_effect=rows), \
             mock.patch.object(diag.time, 'sleep'), \
             contextlib.redirect_stdout(buffer):
            diag.main(argv)
        return buffer.getvalue(), out, js

    def test_annotations_go_to_stdout_while_table_goes_to_the_file(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            stdout, summary, js = self.run_main(tmpdir, ['--annotate'])
            # Annotation STDOUT'ta olmalı.
            self.assertIn('::warning title=www.epey.com::', stdout)
            # Tablo dosyada olmalı, stdout'ta tablo satırı olmamalı.
            self.assertIn('403', summary.read_text(encoding='utf-8'))
            self.assertNotIn('403 ms', stdout)
            self.assertTrue(js.exists())
            self.assertIn('results', js.read_text(encoding='utf-8'))

    def test_json_out_is_valid_json(self):
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            _stdout, _summary, js = self.run_main(Path(tmp), [])
            data = json.loads(js.read_text(encoding='utf-8'))
            self.assertEqual(len(data['results']), 1)
            self.assertIn('403', data['results'][0]['verdict'])


class WafSignatureTests(unittest.TestCase):
    """403'ün kimliği: 'Blocked' (IP itibarı) mi, 'challenge' (tarayıcı) mı?

    Çözüm seçimi buna bağlı: challenge ise gerçek tarayıcı işe yarar,
    blocked ise tarayıcı da yaramaz — IP değişmedikçe.
    """

    def test_cloudflare_blocked_page(self):
        html = ('<html><head><title>Attention Required! | Cloudflare</title></head>'
                '<body>Why have I been blocked?</body></html>')
        sig = diag.waf_signature(html)
        self.assertIn('cloudflare_blocked', sig['waf'])
        self.assertEqual(sig['blocked_vs_challenge'], 'blocked')

    def test_cloudflare_challenge_page(self):
        html = '<html><head><title>Just a moment...</title></head></html>'
        sig = diag.waf_signature(html)
        self.assertIn('cloudflare_challenge', sig['waf'])
        self.assertEqual(sig['blocked_vs_challenge'], 'challenge')

    def test_other_wafs_are_recognised(self):
        self.assertIn('akamai', diag.waf_signature('Reference #18.abcd')['waf'])
        self.assertIn('incapsula', diag.waf_signature('_Incapsula_Resource')['waf'])
        self.assertIn('datadome', diag.waf_signature('geo.captcha-delivery.com')['waf'])
        self.assertIn('perimeterx', diag.waf_signature('px-captcha')['waf'])

    def test_unknown_body_is_honest(self):
        sig = diag.waf_signature('<html><body>403</body></html>')
        self.assertEqual(sig['waf'], ['bilinmiyor'])
        self.assertEqual(sig['blocked_vs_challenge'], 'unknown')

    def test_no_secrets_are_extracted(self):
        """Ray ID gibi kimlikler yalnızca 'var mı' diye bakılır, değeri taşınmaz."""
        sig = diag.waf_signature('Cloudflare Ray ID: 8f3a1b2c9d')
        self.assertTrue(sig['ray_present'])
        self.assertNotIn('8f3a1b2c9d', str(sig))


class ChromeImpersonationVariantTests(unittest.TestCase):
    """7-8. varyant: Chrome/Safari TLS parmak izi taklidi.

    Bu, "403 IP kaynaklı mı, TLS parmak izi kaynaklı mı" ayrımını kesinleştiren
    tek ücretsiz araçtır. Paket yoksa araç çökmemeli, dürüstçe söylemeli.
    """

    def test_two_impersonation_variants_exist(self):
        names = [variant[0] for variant in diag.VARIANTS]
        self.assertEqual(len(diag.VARIANTS), 8)
        self.assertTrue(any('curl_cffi' in name and 'chrome' in name for name in names))
        self.assertTrue(any('curl_cffi' in name and 'safari' in name for name in names))

    def test_missing_package_is_reported_not_raised(self):
        with mock.patch.dict(sys.modules, {'curl_cffi': None}):
            row = diag.attempt_cffi('https://www.epey.com/x.html', 'chrome',
                                    diag.BROWSER_UA, diag.FULL_HEADERS)
        self.assertIsNone(row['status'])
        self.assertIn('curl_cffi', row['error'])

    def test_impersonation_success_verdict(self):
        rows = [('1 urllib · mevcut UA', {'status': 403}),
                ('7 curl_cffi · chrome', {'status': 200, 'jsonld': 1})]
        self.assertIn('TLS FARKI', diag.verdict(rows))

    def test_all_blocked_verdict_mentions_tls_attempt(self):
        rows = [(variant[0], {'status': 403,
                              'signature': {'blocked_vs_challenge': 'blocked'}})
                for variant in diag.VARIANTS]
        verdict = diag.verdict(rows)
        self.assertIn('TLS taklidi', verdict)

    def test_challenge_verdict_points_to_real_browser(self):
        rows = [(variant[0], {'status': 403,
                              'signature': {'blocked_vs_challenge': 'challenge'}})
                for variant in diag.VARIANTS]
        self.assertIn('tarayıcı', diag.verdict(rows))
        self.assertIn('challenge', diag.verdict(rows))


class TlsRefusalTests(unittest.TestCase):
    """WAF TLS el sıkışmasını kapatırsa status None döner — bu da engildir."""

    def test_tls_refusal_is_not_reported_as_mixed_result(self):
        rows = [(variant[0], {'status': 403, 'signature': {'blocked_vs_challenge': 'blocked'}})
                for variant in diag.VARIANTS[:6]]
        rows += [('7 curl_cffi · chrome', {'status': None, 'error': 'SSLError'}),
                 ('8 curl_cffi · safari', {'status': None, 'error': 'SSLError'})]
        verdict = diag.verdict(rows)
        self.assertNotIn('KARMA', verdict)
        self.assertIn('IP', verdict)

    def test_all_connections_refused_is_honest(self):
        rows = [(variant[0], {'status': None, 'error': 'timeout'}) for variant in diag.VARIANTS]
        self.assertTrue(diag.verdict(rows))
