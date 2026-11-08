"""Cimri + Epey okuyucusu (F2). Ağ kullanmayız: fixture'lar F1 ölçümünden gelir."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aggregator_worker as aw

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'aggregators'
EPEY_URL = 'https://www.epey.com/televizyon/tcl-65c6k.html'
CIMRI_URL = ('https://www.cimri.com/televizyonlar/'
             'en-ucuz-tcl-t6d-televizyon-fiyatlari,a2582673366')


def page(name):
    return (FIXTURES / name).read_text(encoding='utf-8')


class UrlTests(unittest.TestCase):
    def test_accepts_both_product_shapes(self):
        self.assertEqual(aw.product_url(EPEY_URL), EPEY_URL)
        self.assertEqual(aw.product_url(CIMRI_URL), CIMRI_URL)

    def test_rejects_search_and_comparison_pages(self):
        # Her ikisi de robots.txt'te yasak.
        for url in ('https://www.epey.com/televizyon/karsilastir/1011770-1017424/tcl-65c6k_tcl-65c7k/',
                    'https://www.epey.com/ara/?q=tcl',
                    'https://www.cimri.com/arama?q=tcl',
                    'https://www.cimri.com/televizyonlar'):
            with self.assertRaises(ValueError, msg=url):
                aw.product_url(url)

    def test_query_string_is_stripped_so_all_stores_stay_visible(self):
        # ?magaza= filtresi atılır: tek mağaza değil, tüm mağazalar görülsün.
        for url in ('https://www.cimri.com/televizyonlar/en-ucuz-tcl-fiyatlari?magaza=Amazon',
                    'https://www.epey.com/televizyon/tcl-65c6k.html?utm_source=x'):
            cleaned = aw.product_url(url)
            self.assertNotIn('?', cleaned)
            self.assertNotIn('magaza', cleaned)

    def test_rejects_other_hosts_and_unsafe_shapes(self):
        for url in ('https://www.hepsiburada.com/x-p-HBCV1',
                    'http://www.epey.com/televizyon/tcl-65c6k.html',
                    'https://user:pw@www.epey.com/televizyon/tcl-65c6k.html',
                    'https://www.epey.com:8443/televizyon/tcl-65c6k.html',
                    'https://www.epey.com.evil.test/televizyon/tcl-65c6k.html',
                    'ftp://www.epey.com/televizyon/tcl-65c6k.html',
                    ''):
            with self.assertRaises(ValueError, msg=url):
                aw.product_url(url)


class PriceTrapTests(unittest.TestCase):
    """Türkçe binlik nokta tuzağı: '29.999' 29,999 TL değil 29.999 TL'dir."""

    def test_thousands_dot_is_not_a_decimal_point(self):
        self.assertEqual(aw._to_cents('29.999'), 2_999_900)
        self.assertEqual(aw._to_cents('60.927,75'), 6_092_775)
        self.assertEqual(aw._to_cents('60927.75'), 6_092_775)
        self.assertEqual(aw._to_cents('92.999,00'), 9_299_900)

    def test_rejects_non_prices(self):
        for value in ('', 'abc', '0', '-5'):
            self.assertIsNone(aw._to_cents(value), value)


class EpeyTests(unittest.TestCase):
    def test_low_price_and_sellers(self):
        result = aw.parse(page('epey-tcl-65c6k.html'), EPEY_URL)
        self.assertEqual(result['store'], 'Epey')
        self.assertEqual(result['price'], 6_092_775)
        self.assertEqual(result['price_source'], 'aggregate_lowprice')
        self.assertTrue(result['jsonld_verified'])
        self.assertEqual(result['seller'], 'Trendyol')
        sellers = [offer['seller'] for offer in result['offers']]
        self.assertIn('Hepsiburada', sellers)
        self.assertIn('Media Markt', sellers)
        self.assertEqual(result['offers'][0]['price'], min(o['price'] for o in result['offers']))

    def test_title_is_clean(self):
        result = aw.parse(page('epey-tcl-65c6k.html'), EPEY_URL)
        self.assertTrue(result['title'].startswith('TCL 65C6K'))
        self.assertNotIn('Epey', result['title'])


class CimriTests(unittest.TestCase):
    def test_merchant_names_come_from_logo_rows(self):
        result = aw.parse(page('cimri-tcl-t6d.html'), CIMRI_URL)
        self.assertEqual(result['store'], 'Cimri')
        self.assertEqual(result['price'], 2_999_900)
        self.assertEqual(result['seller'], 'Amazon-prime')
        self.assertEqual([offer['seller'] for offer in result['offers']],
                         ['Amazon-prime', 'Amazon', 'Hepsiburada'])

    def test_free_text_rows_without_logos_still_parse(self):
        result = aw.parse(page('cimri-merchant-rows.html'), CIMRI_URL)
        self.assertEqual(result['price'], 2_999_900)
        self.assertEqual(result['offer_count'], 2)


class FailClosedTests(unittest.TestCase):
    def test_mismatch_between_jsonld_and_rows_is_a_failure(self):
        with self.assertRaises(ValueError) as ctx:
            aw.parse(page('mismatch.html'), EPEY_URL)
        self.assertIn('uyuşmuyor', str(ctx.exception))

    def test_missing_jsonld_is_a_failure(self):
        with self.assertRaises(ValueError):
            aw.parse('<html><body>Hepsiburada fiyatı 61.189,00 TL</body></html>', EPEY_URL)

    def test_no_rows_is_a_failure(self):
        raw = ('<script type="application/ld+json">{"@type":"Product","offers":'
               '{"@type":"AggregateOffer","lowPrice":"29999","priceCurrency":"TRY"}}'
               '</script><body>boş</body>')
        with self.assertRaises(ValueError):
            aw.parse(raw, EPEY_URL)

    def test_wrong_currency_is_a_failure(self):
        raw = ('<script type="application/ld+json">{"@type":"Product","offers":'
               '{"@type":"AggregateOffer","lowPrice":"29999","priceCurrency":"USD",'
               '"offers":[{"@type":"Offer","price":"29999"}]}}'
               '</script><body>29.999 TL</body>')
        with self.assertRaises(ValueError):
            aw.parse(raw, EPEY_URL)

    def test_block_page_is_refused_not_parsed(self):
        with self.assertRaises(aw.Blocked):
            aw.parse('<html><body>Access Denied</body></html>', EPEY_URL)

    def test_no_remote_body_is_echoed_by_the_cli_contract(self):
        # parse() yalnız ValueError/Blocked fırlatır; gövde asla hataya gömülmez.
        try:
            aw.parse(page('mismatch.html'), EPEY_URL)
        except ValueError as exc:
            self.assertNotIn('<script', str(exc))


if __name__ == '__main__':
    unittest.main()
