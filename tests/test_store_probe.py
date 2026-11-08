"""F1 probe tests: allowlist, redirect refusal, embedded-JSON and price-label parsing.

No network is used. Every fetch is replaced by a fake opener, so a leak in the
guard rails shows up as a failing test rather than as a real request.
"""
import email.message
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

import store_probe as probe


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, body, status=200, url='', encoding=None):
        self._body = body.encode('utf-8') if isinstance(body, str) else body
        self.status = status
        self._url = url
        headers = email.message.Message()
        if encoding:
            headers['Content-Encoding'] = encoding
        self.headers = headers

    def read(self, _limit=None):
        return self._body

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class FakeOpener:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def open(self, request, timeout=None):
        self.calls.append((request.full_url, timeout))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def fetcher(body, **kwargs):
    opener = FakeOpener(FakeResponse(body, **kwargs))
    return opener, (lambda url, opener=opener: probe.fetch_once(url, opener=opener))


JSONLD_PAGE = """<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"TCL 65C7K Mini LED TV",
 "gtin13":"8691234567890",
 "offers":{"@type":"Offer","price":"89999.00","priceCurrency":"TRY",
           "availability":"https://schema.org/InStock","seller":{"name":"MediaMarkt"}}}
</script></head><body>
<div>₺ 89.999,–</div>
<div>Taksit Aylık ₺22.499,75</div>
<div>+18.000 puan</div>
<div>Ek garanti 3.639 TL</div>
<div>İkinci El 45.000,00 TL</div>
</body></html>"""

# Mirrors the shape measured on n11: one price per seller inside embedded JSON.
N11_PAGE = """<html><body>
<script>window.__INITIAL_STATE__ = {"variants":[
 {"outOfStock":false,"price":"49.750 TL"},
 {"outOfStock":false,"price":"50.049,09 TL"},
 {"outOfStock":true,"price":"12.000 TL"}]};</script>
<div>49.750 TL</div>
</body></html>"""

COUNTRY_WALL_PAGE = """<html><head><title>Welcome to Trendyol</title></head>
<body>Please select your country for shopping</body></html>"""


class ValidateUrlTests(unittest.TestCase):
    def test_accepts_allowlisted_https(self):
        for url in ('https://www.mediamarkt.com.tr/tr/product/_tv-1246642.html',
                    'https://www.hepsiburada.com/x-p-HBCV1',
                    'https://www.amazon.com.tr/dp/B0FBWJTCV6',
                    'https://www.trendyol.com/tcl/x-p-855342638?merchantId=106394'):
            self.assertEqual(probe.validate_url(url), url.split('#')[0])

    def test_rejects_unsafe_shapes(self):
        bad = [
            'http://www.mediamarkt.com.tr/tr/product/_tv-1.html',      # http
            'https://evil.test/tr/product/_tv-1.html',                 # unknown host
            'https://www.mediamarkt.com.tr.evil.test/x.html',          # suffix spoof
            'https://user:pw@www.mediamarkt.com.tr/x.html',            # userinfo
            'https://www.mediamarkt.com.tr:8443/x.html',               # port
            'https://localhost/x.html',
            'https://127.0.0.1/x.html',
            'ftp://www.teknosa.com/x',
            'https://www.teknosa.com/',                                # bare root
            'https://www.teknosa.com/x.html y',                        # whitespace
            '',
        ]
        for url in bad:
            with self.assertRaises(ValueError, msg=url):
                probe.validate_url(url)

    def test_fragment_is_dropped(self):
        self.assertEqual(
            probe.validate_url('https://www.teknosa.com/tv-p-1#urun-ozellikleri'),
            'https://www.teknosa.com/tv-p-1')


class FetchGuardTests(unittest.TestCase):
    def test_redirect_is_refused_not_followed(self):
        # NoRedirect raises ValueError; the probe must report, never follow.
        exc = ValueError('Yönlendirme reddedildi')
        opener = FakeOpener(exc)
        result = probe.fetch_once('https://www.trendyol.com/x-p-1', opener=opener)
        self.assertTrue(result.redirected)
        self.assertEqual(result.status, None)
        self.assertEqual(len(opener.calls), 1)

    def test_http_error_is_a_measurement_not_a_crash(self):
        error = urllib.error.HTTPError(
            'https://www.n11.com/x', 403, 'Forbidden', {}, io.BytesIO(b'Access Denied'))
        opener = FakeOpener(error)
        result = probe.fetch_once('https://www.n11.com/x', opener=opener)
        self.assertEqual(result.status, 403)
        self.assertIn('403', result.error)
        state = probe.block_state(result.body, result.status)
        self.assertIn('HTTP 403', state['captcha_or_block'])

    def test_body_size_limit(self):
        opener = FakeOpener(FakeResponse('x' * (probe.MAX_BODY + 10)))
        result = probe.fetch_once('https://www.pazarama.com/x', opener=opener)
        self.assertIn('3 MB', result.error)
        self.assertEqual(result.body, '')

    def test_gzip_body_is_decoded(self):
        import gzip
        payload = gzip.compress(b'<html><body>89.999 TL</body></html>')
        opener = FakeOpener(FakeResponse(payload, encoding='gzip'))
        result = probe.fetch_once('https://www.teknosa.com/x', opener=opener)
        self.assertIn('89.999 TL', result.body)

    def test_request_is_anonymous(self):
        opener, fetch = fetcher(JSONLD_PAGE)
        fetch('https://www.mediamarkt.com.tr/x')
        # No cookie jar is ever attached by urllib's default opener.
        self.assertEqual(len(opener.calls), 1)


class AnalysisTests(unittest.TestCase):
    def test_jsonld_product_and_offer(self):
        analysis = probe.analyze(JSONLD_PAGE, 200)
        self.assertEqual(analysis['jsonld_blocks'], 1)
        self.assertEqual(analysis['jsonld_products'], 1)
        self.assertEqual(analysis['jsonld_currencies'], ['TRY'])
        self.assertEqual(analysis['jsonld_prices'][0]['price'], '89999.00')

    def test_installment_points_and_warranty_are_labelled_not_main_price(self):
        analysis = probe.analyze(JSONLD_PAGE, 200)
        labels = {(item['text'], item['label']) for item in analysis['visible_prices']}
        self.assertIn(('22.499,75', 'taksit'), labels)
        self.assertIn(('3.639', 'garanti'), labels)
        # Turkish dotted İ must not break the second-hand rule.
        self.assertIn(('45.000,00', 'ikinci_el'), labels)
        # "+18.000 puan" has no TL/₺ suffix: it must never enter the price pool.
        self.assertNotIn('18.000', {item['text'] for item in analysis['visible_prices']})

    def test_installment_multiplier_row_is_labelled_taksit(self):
        analysis = probe.analyze('<div>4 x 22.499,75 TL</div>', 200)
        self.assertEqual(analysis['visible_prices'][0]['label'], 'taksit')

    def test_jsonld_price_matches_the_main_visible_price(self):
        analysis = probe.analyze(JSONLD_PAGE, 200)
        matched = analysis['jsonld_price_matches_visible']
        self.assertTrue(matched, 'JSON-LD fiyatı görünen fiyatla eşleşmeli')
        self.assertEqual(matched[0]['label'], 'ana_fiyat')

    def test_ean_is_detected(self):
        self.assertEqual(probe.analyze(JSONLD_PAGE, 200)['ean'], '8691234567890')

    def test_embedded_seller_prices_only_when_in_stock(self):
        analysis = probe.analyze(N11_PAGE, 200)
        embedded = analysis['embedded']
        self.assertIn('__INITIAL_STATE__', embedded['markers'])
        self.assertEqual(embedded['seller_price_samples'], ['49.750', '50.049,09'])
        self.assertNotIn('12.000', embedded['seller_price_samples'])

    def test_country_wall_is_detected(self):
        analysis = probe.analyze(COUNTRY_WALL_PAGE, 200)
        self.assertTrue(analysis['block']['country_wall'])

    def test_block_marker_is_detected(self):
        analysis = probe.analyze('<html><body>Akamai: Access Denied</body></html>', 403)
        self.assertTrue(analysis['block']['captcha_or_block'])

    def test_broken_jsonld_does_not_crash(self):
        page = '<script type="application/ld+json">{"@type":"Product",</script>'
        analysis = probe.analyze(page, 200)
        self.assertEqual(analysis['jsonld_products'], 0)

    def test_tr_trap_price_is_right(self):
        # "89.999,-" is a Turkish integer price, not 89,999.
        analysis = probe.analyze('<div>₺ 89.999,–</div>', 200)
        self.assertEqual(analysis['visible_prices'][0]['cents'], 8_999_900)


AMAZON_CHALLENGE = """<html><head><title>Amazon.com.tr</title>
<script>var a={"price":"1.00","price":"2.00","price":"3.00"};</script>
</head><body>Enter the characters you see below
Type the characters you see in this image:</body></html>"""


class BlockedPageTests(unittest.TestCase):
    def test_challenge_page_is_not_analysed_for_prices(self):
        # Amazon returns HTTP 200 with a challenge page: 42 bogus "price" keys.
        analysis = probe.analyze(AMAZON_CHALLENGE, 200)
        self.assertEqual(analysis['visible_price_count'], 0)
        self.assertEqual(analysis['visible_prices'], [])
        self.assertEqual(analysis['jsonld_prices'], [])
        self.assertEqual(analysis['embedded']['price_fields'], 0)
        self.assertEqual(analysis['analysis_skipped'], 'engel sayfası')

    def test_block_body_is_kept_in_the_snippet(self):
        snippet = probe._snippet(AMAZON_CHALLENGE)
        self.assertIn('BLOCK_BODY', snippet)
        self.assertIn('Enter the characters', snippet)

    def test_403_body_is_kept_in_the_snippet(self):
        snippet = probe._snippet('<html><body>Access Denied (Akamai)</body></html>')
        self.assertIn('BLOCK_BODY', snippet)


# Shape of a Cimri product page (measured 2026-11-08): one row per merchant with
# a logo, a price, and "N dk önce güncellendi".
CIMRI_PAGE = """<html><body>
<h1>TCL T6D Televizyon</h1>
<div>26 Fiyat Arasında En Ucuz</div><div>29.999 TL</div>
<div>Mağazalar</div><div>Amazon (6)Hepsiburada (2)n11 (1)PttAVM (5)</div>
<div><img src="https://cdn.cimri.io/pictures/merchant-logos/10725.png?v=1">Amazon-prime</div>
<div>29.999 TL</div><div>12 dk önce güncellendi</div>
<div><img src="https://cdn.cimri.io/pictures/merchant-logos/10370.png?v=1">Amazon</div>
<div>30.045,02 TL</div><div>34 dk önce güncellendi</div>
<div><img src="https://cdn.cimri.io/pictures/merchant-logos/1497.png?v=1">Hepsiburada</div>
<div>31.200 TL</div><div>2 saat önce güncellendi</div>
</body></html>"""


class AggregatorTests(unittest.TestCase):
    def test_merchant_rows_and_freshness(self):
        agg = probe.aggregator_analysis(CIMRI_PAGE, 'www.cimri.com')
        self.assertEqual(agg['distinct_merchant_logos'], 3)
        self.assertEqual(agg['merchant_logo_rows'], 3)
        self.assertIn('Amazon', agg['merchant_counts'])
        self.assertEqual(agg['merchant_counts']['Amazon'], 6)
        self.assertIn('12 dk', agg['freshness_samples'])
        self.assertEqual(agg['min_price_hint'], '29.999')

    def test_aggregator_section_only_for_aggregator_hosts(self):
        store = probe.analyze('<div>₺ 89.999,–</div>', 200, 'www.mediamarkt.com.tr')
        self.assertIsNone(store['aggregator'])
        agg = probe.analyze(CIMRI_PAGE, 200, 'www.cimri.com')
        self.assertEqual(agg['aggregator']['kind'], 'aggregator')

    def test_cimri_url_is_allowed_and_classified(self):
        url = 'https://www.cimri.com/televizyonlar/en-ucuz-tcl-t6d-televizyon-fiyatlari,a2582673366'
        self.assertEqual(probe.validate_url(url), url)
        self.assertEqual(probe.SITE_KIND['www.cimri.com'], 'aggregator')

    def test_summary_has_aggregator_table(self):
        record = {'host': 'www.cimri.com', 'status': 200, 'statuses': [200],
                  'latency_ms': {'avg': 300}, 'visible_prices': [], 'block': {},
                  'aggregator': probe.aggregator_analysis(CIMRI_PAGE, 'www.cimri.com')}
        summary = probe.summary_markdown([record])
        self.assertIn('Toplayıcı siteler', summary)
        self.assertIn('Amazon (6)', summary)


class RowStateTests(unittest.TestCase):
    def test_states(self):
        cases = [
            ({'status': 200, 'jsonld_prices': [{'price': '1'}], 'block': {}}, 'okunabilir'),
            ({'status': 200, 'jsonld_prices': [], 'block': {}}, 'okundu, fiyat yok'),
            ({'status': 200, 'block': {'country_wall': ['x']}}, 'coğrafya duvarı'),
            ({'status': 200, 'block': {'captcha_or_block': ['x']}}, 'engel/captcha'),
            ({'status': 200, 'redirected': True, 'block': {}}, 'yönlendirme'),
            ({'error': 'bağlantı: URLError', 'block': {}}, 'okunamadı'),
        ]
        for record, expected in cases:
            self.assertEqual(probe._row(record), expected, record)


class SplitEntriesTests(unittest.TestCase):
    def test_one_url_per_line(self):
        text = 'https://www.teknosa.com/a-p-1\nhttps://www.n11.com/urun/b?magaza=c\n'
        self.assertEqual(probe.split_entries(text),
                         ['https://www.teknosa.com/a-p-1',
                          'https://www.n11.com/urun/b?magaza=c'])

    def test_github_single_line_input_merged_with_spaces(self):
        # This is exactly what the first real probe run produced.
        text = ('https://www.n11.com/urun/tcl-65c6k-91634106 '
                'https://www.hepsiburada.com/tcl-65c6k-p-HBCV1')
        self.assertEqual(probe.split_entries(text),
                         ['https://www.n11.com/urun/tcl-65c6k-91634106',
                          'https://www.hepsiburada.com/tcl-65c6k-p-HBCV1'])

    def test_commas_and_newlines_and_blank_lines(self):
        text = '\n\nhttps://www.teknosa.com/a, https://www.n11.com/b ;\n'
        self.assertEqual(probe.split_entries(text),
                         ['https://www.teknosa.com/a', 'https://www.n11.com/b'])

    def test_glued_urls_without_separator(self):
        text = 'https://www.teknosa.com/ahttps://www.n11.com/b'
        self.assertEqual(probe.split_entries(text),
                         ['https://www.teknosa.com/a', 'https://www.n11.com/b'])

    def test_truncated_url_is_kept_so_the_run_reports_it(self):
        # A cut-off path is still a well-formed URL shape: it must reach the
        # fetch (and show up as HTTP 404), not vanish from the report.
        tokens = probe.split_entries('https://www.hepsiburada.com/tcl-65c6k-')
        self.assertEqual(tokens, ['https://www.hepsiburada.com/tcl-65c6k-'])
        self.assertEqual(probe.validate_url(tokens[0]),
                         'https://www.hepsiburada.com/tcl-65c6k-')
        with self.assertRaises(ValueError):
            probe.validate_url('https://www.hepsiburada.com')


class ProbeRunTests(unittest.TestCase):
    def test_end_to_end_writes_outputs_and_rejects_unknown_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            urls = Path(tmp) / 'urls.txt'
            urls.write_text(
                'https://www.mediamarkt.com.tr/tr/product/_tv-1.html\n'
                'https://evil.test/x.html\n'
                'https://www.teknosa.com/ORNEK-yapistir\n'
                '# yorum\n', encoding='utf-8')
            original = probe.fetch_once
            probe.fetch_once = lambda url, opener=None: probe.FetchResult(
                status=200, body=JSONLD_PAGE, final_url=url, size=len(JSONLD_PAGE),
                latency_ms=120)
            try:
                code = probe.main(['--urls', str(urls), '--out', tmp,
                                   '--rounds', '1', '--no-wait'])
            finally:
                probe.fetch_once = original
            self.assertEqual(code, 0)
            data = json.loads((Path(tmp) / 'probe.json').read_text(encoding='utf-8'))
            self.assertEqual(len(data['records']), 1)
            self.assertEqual(data['records'][0]['host'], 'www.mediamarkt.com.tr')
            reasons = {item['reason'] for item in data['skipped']}
            self.assertIn('İzin listesinde olmayan host', reasons)
            self.assertIn('örnek satır, URL girilmedi', reasons)
            summary = (Path(tmp) / 'summary.md').read_text(encoding='utf-8')
            self.assertIn('mediamarkt.com.tr', summary)
            self.assertIn('okunabilir', summary)
            self.assertTrue((Path(tmp) / 'snippets' / 'www.mediamarkt.com.tr.json').exists())

    def test_empty_input_reports_clearly_instead_of_silent_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            urls = Path(tmp) / 'urls.txt'
            urls.write_text('', encoding='utf-8')
            code = probe.main(['--urls', str(urls), '--out', tmp, '--rounds', '1', '--no-wait'])
            self.assertEqual(code, 0)
            summary = (Path(tmp) / 'summary.md').read_text(encoding='utf-8')
            self.assertIn('Hiçbir URL ölçülmedi', summary)
            data = json.loads((Path(tmp) / 'probe.json').read_text(encoding='utf-8'))
            self.assertEqual(data['records'], [])

    def test_space_separated_single_line_input_is_split(self):
        with tempfile.TemporaryDirectory() as tmp:
            urls = Path(tmp) / 'urls.txt'
            urls.write_text('https://www.teknosa.com/a-p-1 https://www.n11.com/b?magaza=c',
                            encoding='utf-8')
            seen = []
            original = probe.fetch_once

            def fake(url, opener=None):
                seen.append(url)
                return probe.FetchResult(status=200, body=JSONLD_PAGE, final_url=url,
                                         size=len(JSONLD_PAGE), latency_ms=100)
            probe.fetch_once = fake
            try:
                probe.main(['--urls', str(urls), '--out', tmp, '--rounds', '1', '--no-wait'])
            finally:
                probe.fetch_once = original
            self.assertEqual(seen, ['https://www.teknosa.com/a-p-1',
                                    'https://www.n11.com/b?magaza=c'])

    def test_failed_fetch_is_reported_without_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            urls = Path(tmp) / 'urls.txt'
            urls.write_text('https://www.n11.com/urun/x?magaza=y\n', encoding='utf-8')
            original = probe.fetch_once
            probe.fetch_once = lambda url, opener=None: probe.FetchResult(
                status=403, error='HTTP 403', latency_ms=90)
            try:
                probe.main(['--urls', str(urls), '--out', tmp, '--rounds', '1', '--no-wait'])
            finally:
                probe.fetch_once = original
            data = json.loads((Path(tmp) / 'probe.json').read_text(encoding='utf-8'))
            record = data['records'][0]
            self.assertEqual(record['status'], 403)
            self.assertNotIn('snippet', record)


if __name__ == '__main__':
    unittest.main()
