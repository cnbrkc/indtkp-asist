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
    value = re.sub(r'\s+', '', raw.strip().upper().replace('TRY', '').replace('TL', '').replace('₺', ''))
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


class OfferList(HTMLParser):
    """Read only the observed main ul#PL, never recommendations/history prices."""
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self):
        super().__init__()
        self.root = None
        self.stack = []
        self.markup = []
        self.markup_size = 0

    def capture(self, value):
        if self.markup_size + len(value) <= 80_000:
            self.markup.append(value)
            self.markup_size += len(value)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if not self.stack:
            if self.root is not None or tag != 'ul' or attrs.get('id') != 'PL':
                return
            self.root = {'tag': tag, 'attrs': attrs, 'children': []}
            self.stack = [self.root]
        else:
            node = {'tag': tag, 'attrs': attrs, 'children': []}
            self.stack[-1]['children'].append(node)
            if tag not in self.VOID:
                self.stack.append(node)
        self.capture(self.get_starttag_text())

    def handle_endtag(self, tag):
        if self.stack:
            self.capture(f'</{tag}>')
            for index in range(len(self.stack) - 1, -1, -1):
                if self.stack[index]['tag'] == tag:
                    self.stack = self.stack[:index]
                    break

    def handle_data(self, data):
        if self.stack:
            self.stack[-1]['children'].append(data)
            self.capture(data)


def node_text(node):
    if isinstance(node, str):
        return node
    if node['tag'] == 'img':
        return node['attrs'].get('alt', '')
    return ''.join(node_text(child) for child in node['children'])


def class_nodes(node, name):
    found = []
    if isinstance(node, dict):
        if name in node['attrs'].get('class', '').split():
            found.append(node)
        for child in node['children']:
            found.extend(class_nodes(child, name))
    return found


def visible_offers(raw):
    parser = OfferList()
    parser.feed(raw)
    if parser.root is None:
        return [], 0, 0, ''
    rows = [child for child in parser.root['children'] if isinstance(child, dict) and child['tag'] == 'li']
    offers, invalid = [], 0
    for row in rows:
        prices = class_nodes(row, 'pt_v8')
        sellers = class_nodes(row, 'v_v8')
        stock = ' '.join(node_text(n) for n in class_nodes(row, 'stock_v8')).lower()
        if 'stokta yok' in stock or 'tükendi' in stock:
            continue
        try:
            if len(prices) != 1 or len(sellers) != 1:
                raise ValueError('Belirsiz teklif satırı')
            price = target_cents(node_text(prices[0]))
            seller = ' '.join(node_text(sellers[0]).split())
            if not seller:
                raise ValueError('Satıcı okunamadı')
            offers.append({'price': price, 'seller': seller[:200]})
        except (ValueError, TypeError):
            invalid += 1
    return offers, len(rows), invalid, ''.join(parser.markup)


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
    jsonld_count = len(offers)
    aggregate = product.get('offers')
    reported = None
    if isinstance(aggregate, dict):
        try:
            reported = int(aggregate.get('offerCount', ''))
            if reported <= 0:
                reported = None
        except (ValueError, TypeError):
            pass
    html_offers, html_rows, invalid_rows, _ = visible_offers(raw)
    source = 'jsonld'
    if html_offers and not invalid_rows:
        offers = html_offers
        source = 'html_main_list'
    # Do not silently call a partial first page complete. Additional offers may
    # be loaded by Akakçe's own "Daha fazla fiyat gör" control.
    if html_rows:
        complete = False if invalid_rows or not html_offers else (html_rows == reported if reported else None)
    else:
        complete = jsonld_count == reported if reported else None
    best = min(offers, key=lambda offer: offer['price'])
    return {'title': str(product.get('name') or 'Akakçe ürünü')[:250],
            'price': best['price'], 'seller': best['seller'], 'offers': len(offers),
            'source': source, 'jsonld_offers': jsonld_count,
            'html_rows': html_rows, 'invalid_html_rows': invalid_rows,
            'reported_offer_count': reported, 'coverage_complete': complete,
            'sample_offers': sorted(offers, key=lambda offer: offer['price'])[:20]}


class Outline(HTMLParser):
    """Bounded inert DOM outline; no URLs, script bodies, forms or arbitrary attrs."""
    def __init__(self):
        super().__init__()
        self.hidden = None
        self.lines = []
        self.size = 0
        self.script_types = []

    def add(self, text):
        if self.size + len(text) <= 100_000:
            self.lines.append(text)
            self.size += len(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script' and len(self.script_types) < 50:
            self.script_types.append(attrs.get('type', 'javascript')[:100])
        if tag in {'script', 'style', 'head', 'noscript', 'form'}:
            if self.hidden is None:
                self.hidden = tag
            return
        if self.hidden:
            return
        if tag in {'div', 'li', 'ul', 'span', 'b', 'strong', 'p', 'h1', 'h2', 'section', 'a', 'img'}:
            # Only structural labels. Never serialize event handlers or URLs.
            labels = ' '.join(f'{key}={attrs[key][:150]}' for key in ('id', 'class') if attrs.get(key))
            self.add(f'<{tag} {labels}>')
            if tag == 'img' and attrs.get('alt'):
                self.add(attrs['alt'][:150])

    def handle_endtag(self, tag):
        if self.hidden == tag:
            self.hidden = None
        elif not self.hidden and tag in {'li', 'ul', 'div', 'section'}:
            self.add(f'</{tag}>')

    def handle_data(self, data):
        if not self.hidden:
            text = ' '.join(data.split())
            if text:
                self.add(text[:1000])


def page_diagnostics(raw):
    outline = Outline()
    outline.feed(raw)
    scripts = Scripts()
    scripts.feed(raw)
    # This is an anonymous public fetch, not a browser session. Keep only
    # bounded structural text, not executable HTML or cookie/header values.
    return {'html_characters': len(raw), 'script_types': outline.script_types,
            'jsonld_blocks': len(scripts.scripts),
            'dom_outline': '\n'.join(outline.lines),
            'outline_limit': 100_000,
            'jsonld_preview': [script[:30_000] for script in scripts.scripts[:3]],
            'main_offer_markup': visible_offers(raw)[3],
            'load_more_context': [raw[max(0, match.start()-1500):match.end()+1000]
                                  for match in list(re.finditer('Daha fazla fiyat gör', raw))[:2]]}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Yönlendirme reddedildi')


def fetch(url, *, diagnostics=False):
    url = product_url(url)
    request = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'tr-TR,tr;q=0.9'})
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
        data = response.read(MAX_BODY + 1)
        if len(data) > MAX_BODY:
            raise ValueError('Sayfa boyutu sınırı aşıldı')
    raw = data.decode('utf-8', errors='replace')
    result = parse_page(raw)
    if diagnostics:
        result['diagnostics'] = page_diagnostics(raw)
    return result


if __name__ == '__main__':
    try:
        print(json.dumps({'ok': True, 'result': fetch(sys.argv[1], diagnostics='--diagnostics' in sys.argv[2:])}, ensure_ascii=False))
    except Exception as exc:
        # No arbitrary remote body or URL is echoed.
        report = {'ok': False, 'error': type(exc).__name__}
        if isinstance(exc, urllib.error.HTTPError):
            report['http_status'] = exc.code
        elif isinstance(exc, ValueError):
            report['reason'] = str(exc)[:200]
        print(json.dumps(report, ensure_ascii=False))
        sys.exit(1)
