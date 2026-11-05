"""Bounded, token-free Akakçe reader. Run in a disposable subprocess."""
from __future__ import annotations

import json
import re
import sys
import urllib.request
import urllib.error
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit

MAX_BODY = 3_000_000


def product_url(raw):
    parts = urlsplit(raw.strip())
    if (parts.scheme != 'https' or parts.hostname not in {'akakce.com', 'www.akakce.com'}
            or parts.username or parts.password or parts.port not in (None, 443)
            or not re.fullmatch(r'/[a-zA-Z0-9_/,.-]+-fiyati,\d+\.html', parts.path)):
        raise ValueError('HTTPS Akakçe ürün detay linki gönder.')
    return urlunsplit(('https', 'www.akakce.com', parts.path, '', ''))


def cents(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0 or number > 1_000_000_000:
            raise ValueError('Geçersiz fiyat')
        result = int((number * 100).quantize(Decimal('1')))
        if result <= 0:
            raise ValueError('Geçersiz fiyat')
        return result
    except InvalidOperation:
        raise ValueError('Geçersiz fiyat') from None


def target_cents(raw):
    value = raw.strip().upper().replace('TRY', '').replace('TL', '').replace('₺', '').replace(' ', '')
    if not re.fullmatch(r'(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d{1,2})?', value):
        raise ValueError('Örnek: 6500 veya 6.500,50 TL')
    return cents(value.replace('.', '').replace(',', '.'))


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inside = False
        self.current = []
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.inside = dict(attrs).get('type', '').lower() == 'application/ld+json'
            self.current = []

    def handle_data(self, data):
        if self.inside:
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.inside:
            self.scripts.append(''.join(self.current))
            self.inside = False


def parse_page(raw):
    if any(marker in raw.lower() for marker in ('are you a human', 'cf-chl-', 'captcha challenge')):
        raise ValueError('Erişim doğrulaması istendi')
    parser = Scripts()
    parser.feed(raw)
    products = []

    def find(node):
        if isinstance(node, list):
            for item in node:
                find(item)
        elif isinstance(node, dict):
            kinds = node.get('@type', [])
            if isinstance(kinds, str):
                kinds = [kinds]
            if 'Product' in kinds:
                products.append(node)
            else:
                for item in node.values():
                    if isinstance(item, (list, dict)):
                        find(item)

    for script in parser.scripts:
        try:
            find(json.loads(script))
        except (ValueError, TypeError):
            continue
    if len(products) != 1:
        raise ValueError('Tek ürün için yapılandırılmış veri bulunamadı')
    product = products[0]
    offers = []

    def collect(node):
        if isinstance(node, list):
            for item in node:
                collect(item)
        elif isinstance(node, dict):
            # AggregateOffer.lowPrice alone lacks a specific offer; fail closed.
            if 'price' in node:
                currency = node.get('priceCurrency')
                if currency != 'TRY':
                    return
                availability = str(node.get('availability', ''))
                if availability and availability.rsplit('/', 1)[-1] not in {'InStock', 'LimitedAvailability', 'OnlineOnly'}:
                    return
                try:
                    price = cents(node['price'])
                except (ValueError, TypeError):
                    return
                seller = node.get('seller') or {}
                name = seller.get('name', '') if isinstance(seller, dict) else str(seller)
                offers.append({'price': price, 'seller': str(name)[:200]})
            if 'offers' in node:
                collect(node['offers'])

    collect(product.get('offers'))
    if not offers:
        raise ValueError('Geçerli TRY teklifi okunamadı')
    best = min(offers, key=lambda offer: offer['price'])
    return {'title': str(product.get('name') or 'Akakçe ürünü')[:250],
            'price': best['price'], 'seller': best['seller'], 'offers': len(offers),
            'sample_offers': sorted(offers, key=lambda offer: offer['price'])[:20]}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Yönlendirme reddedildi')


def fetch(url):
    url = product_url(url)
    request = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'tr-TR,tr;q=0.9'})
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
        data = response.read(MAX_BODY + 1)
        if len(data) > MAX_BODY:
            raise ValueError('Sayfa boyutu sınırı aşıldı')
    return parse_page(data.decode('utf-8', errors='replace'))


if __name__ == '__main__':
    try:
        print(json.dumps({'ok': True, 'result': fetch(sys.argv[1])}, ensure_ascii=False))
    except Exception as exc:
        # No arbitrary remote body or URL is echoed.
        report = {'ok': False, 'error': type(exc).__name__}
        if isinstance(exc, urllib.error.HTTPError):
            report['http_status'] = exc.code
        elif isinstance(exc, ValueError):
            report['reason'] = str(exc)[:200]
        print(json.dumps(report, ensure_ascii=False))
        sys.exit(1)
