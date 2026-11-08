"""F1 probe: measure whether store product pages can be read anonymously.

This tool fetches nothing but product pages on an explicit host allowlist, one
request at a time, with redirects refused. It extracts *measurements* (JSON-LD,
embedded state, visible price candidates, latency, block markers) and never
decides a price. The output is the source material for later adapters and for
their test fixtures.

Run standalone:
    python -I tools/store_probe.py --urls urls.txt --out out/
    python -I tools/store_probe.py --url https://www.amazon.com.tr/dp/B0FBWJTCV6

Writes:
    out/probe.json            full machine-readable result
    out/summary.md            Markdown table for GITHUB_STEP_SUMMARY
    out/snippets/<host>.json  trimmed evidence per host (artefact)
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import time
import urllib.error
import urllib.request
import zlib
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from akakce_worker import NoRedirect, cents  # noqa: E402  (repo root module)

# ---------------------------------------------------------------------------
# Policy (mirrors plan 13.6.2 — starting values, corrected by measurement)
# ---------------------------------------------------------------------------

ALLOWED_HOSTS = {
    'www.mediamarkt.com.tr': {'min_gap': 5, 'concurrency': 2},
    'www.teknosa.com': {'min_gap': 8, 'concurrency': 1},
    'www.pazarama.com': {'min_gap': 8, 'concurrency': 1},
    'www.n11.com': {'min_gap': 10, 'concurrency': 1},
    'www.hepsiburada.com': {'min_gap': 12, 'concurrency': 1},
    'www.trendyol.com': {'min_gap': 6, 'concurrency': 1},
    'www.amazon.com.tr': {'min_gap': 60, 'concurrency': 1},
    # Reference only: the Akakçe line already works and is not part of F2.
    'www.akakce.com': {'min_gap': 10, 'concurrency': 1},
    # Aggregators. Stores behind them are reached by the aggregator, not by us.
    'www.cimri.com': {'min_gap': 10, 'concurrency': 1},
    # Epey: robots.txt AI botlarına açıkça Allow: /, Crawl-delay yok (2026-11-08).
    'www.epey.com': {'min_gap': 12, 'concurrency': 1},
}

# "store" pages are read directly; "aggregator" pages carry per-merchant rows and
# are the only realistic way to see stores that refuse datacenter IPs (F1 result:
# 5 of 7 stores answered 403 from GitHub Actions).
SITE_KIND = {'www.cimri.com': 'aggregator', 'www.akakce.com': 'aggregator',
             'www.epey.com': 'aggregator'}

MAX_BODY = 3_000_000
HTTP_TIMEOUT = 15
GAP_BETWEEN_URLS = 5          # seconds, cross-host politeness
ROUNDS = 3                    # latency measurement rounds per URL
SNIPPET_LIMIT = 20_000        # bytes of evidence kept per host

USER_AGENT = 'Mozilla/5.0 (compatible; indtkp-asist-probe/1.0)'
HEADERS = {
    'User-Agent': USER_AGENT,
    'Accept': 'text/html,application/xhtml+xml',
    'Accept-Language': 'tr-TR,tr;q=0.9',
    'Accept-Encoding': 'gzip, deflate',
}

_NUMBER = r'\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:,\d{1,2})?'

# Two shapes are used on Turkish store pages: "₺ 89.999,–" / "₺22.499,75"
# (symbol first) and "89.999 TL" / "3.639 TL" (symbol last). The en-dash and
# ",-" suffixes are integer markers, not decimals.
TURKISH_PRICE = re.compile(
    r'₺\s*(?P<pre>' + _NUMBER + r')'
    r'|(?<![\d.,])(?P<post>' + _NUMBER + r')\s*(?:TL|₺|,-|–|—)'
)

# Context keywords used only to *label* a candidate, never to accept a price.
POINTS = ('puan', 'para puan', 'chip', 'bonus')
WARRANTY = ('garanti', 'ek garanti', 'sigorta')
SECOND_HAND = ('ikinci el', '2. el', '2.el', 'yenilenmis', 'yenilenmiş', 'teşhir', 'teshir',
               'amazon depo', 'yeni gibi')
# "4 x 22.499,75" / "Aylık 22.499,75" / "Peşin fiyatına 4 taksit"
INSTALLMENT = re.compile(r'\btaksit|\baylık|\baylik|peşin fiyatına|\b\d{1,2}\s*x\b')
TR_FOLD = str.maketrans({'İ': 'i', 'I': 'i', 'ı': 'i', 'Ş': 's', 'ş': 's', 'Ğ': 'g',
                         'ğ': 'g', 'Ü': 'u', 'ü': 'u', 'Ö': 'o', 'ö': 'o', 'Ç': 'c', 'ç': 'c'})
BLOCK_MARKERS = ('captcha', 'access denied', 'erişim engellendi', 'unusual traffic',
                 'request unsuccessful', 'are you a robot', 'bot kontrolü',
                 'too many requests', 'rate limit',
                 # Amazon challenge page phrasing (measured: HTTP 200, 4 KB body)
                 'enter the characters you see', 'type the characters you see',
                 'characters you see in this image')
COUNTRY_WALL = ('select your country', 'ülkenizi seçin', 'select-country')

EMBEDDED_PATTERNS = {
    '__NEXT_DATA__': re.compile(r'<script[^>]+id="__NEXT_DATA__"[^>]*>', re.I),
    '__PRODUCT_DETAIL_APP_INITIAL_STATE__':
        re.compile(r'window\.__PRODUCT_DETAIL_APP_INITIAL_STATE__\s*=', re.I),
    '__INITIAL_STATE__': re.compile(r'window\.__INITIAL_STATE__\s*=', re.I),
    '__NUXT__': re.compile(r'window\.__NUXT__\s*=', re.I),
    'application_state': re.compile(r'\bapplication-state\b', re.I),
}
EMBEDDED_PRICE = re.compile(r'"price"\s*:\s*"?' + _NUMBER + r'"?', re.I)
# n11 publishes one price per seller variant. Only in-stock variants count.
EMBEDDED_SELLER_PRICE = re.compile(
    r'"outOfStock"\s*:\s*(?:false|null)[^}]{0,240}?"price"\s*:\s*"?(' + _NUMBER + r')"?',
    re.I,
)
EAN_PATTERNS = (
    re.compile(r'"gtin(?:8|12|13|14)"\s*:\s*"?(\d{8,14})"?', re.I),
    re.compile(r'\b(?:EAN|Barkod)\b[^0-9]{0,40}(\d{13})', re.I),
    re.compile(r'"ean"\s*:\s*"?(\d{8,14})"?', re.I),
)


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------

def validate_url(raw):
    """Return a normalized https URL on an allowlisted host, else ValueError."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError('Boş URL')
    if any(ch.isspace() for ch in raw.strip()):
        raise ValueError('URL boşluk içeriyor')
    parts = urlsplit(raw.strip())
    host = (parts.hostname or '').lower()
    if parts.scheme != 'https':
        raise ValueError('Yalnız https')
    if host not in ALLOWED_HOSTS:
        raise ValueError('İzin listesinde olmayan host')
    if parts.username or parts.password:
        raise ValueError('userinfo kabul edilmez')
    if parts.port not in (None, 443):
        raise ValueError('Özel port kabul edilmez')
    if not parts.path or parts.path == '/':
        raise ValueError('Ürün yolu gerekli')
    return urlunsplit(('https', host, parts.path, parts.query, ''))



URL_TOKEN = re.compile(r'https?://\S+')


def split_entries(text):
    """Recover URLs from a single line that GitHub's one-line input merged.

    GitHub Actions `inputs` for a string input is a *single* line, so pasting
    one URL per line collapses into "url1 url2 url3". Newlines, commas and
    whitespace are all accepted as separators.
    """
    tokens = []
    for chunk in re.split(r'[\s,;]+', text or ''):
        chunk = chunk.strip().strip('<>"\'')
        if chunk:
            tokens.append(chunk)
    # If a chunk is obviously several URLs glued together, split on the scheme.
    recovered = []
    for token in tokens:
        pieces = [piece for piece in re.split(r'(?=https?://)', token) if piece]
        recovered.extend(pieces if len(pieces) > 1 else [token])
    return recovered


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

class FetchResult:
    __slots__ = ('status', 'body', 'final_url', 'redirected', 'bytes', 'latency_ms', 'error')

    def __init__(self, status=None, body='', final_url='', redirected=False,
                 size=0, latency_ms=0, error=None):
        self.status, self.body, self.final_url = status, body, final_url
        self.redirected, self.bytes = redirected, size
        self.latency_ms, self.error = latency_ms, error


def _decode(data, encoding):
    if (encoding or '').lower() == 'gzip':
        try:
            data = gzip.decompress(data)
        except OSError:
            pass
    elif (encoding or '').lower() == 'deflate':
        try:
            data = zlib.decompress(data, -zlib.MAX_WBITS)
        except zlib.error:
            try:
                data = zlib.decompress(data)
            except zlib.error:
                pass
    return data.decode('utf-8', errors='replace')


def fetch_once(url, opener=None):
    """One anonymous GET. Redirects raise and are reported, never followed."""
    opener = opener or urllib.request.build_opener(NoRedirect())
    started = time.monotonic()
    try:
        request = urllib.request.Request(url, headers=HEADERS)
        with opener.open(request, timeout=HTTP_TIMEOUT) as response:
            data = response.read(MAX_BODY + 1)
            latency = int((time.monotonic() - started) * 1000)
            if len(data) > MAX_BODY:
                return FetchResult(status=response.status, latency_ms=latency,
                                   error='gövde 3 MB sınırını aştı')
            body = _decode(data, response.headers.get('Content-Encoding'))
            return FetchResult(status=response.status, body=body,
                               final_url=response.geturl(), size=len(data),
                               latency_ms=latency)
    except ValueError as exc:
        # NoRedirect.redirect_request raises ValueError on any 3xx.
        return FetchResult(latency_ms=int((time.monotonic() - started) * 1000),
                           error='yönlendirme reddedildi', redirected='Yönlendirme' in str(exc))
    except urllib.error.HTTPError as exc:
        body = ''
        try:
            body = exc.read(200_000).decode('utf-8', errors='replace')
        except Exception:
            pass
        return FetchResult(status=exc.code, body=body, size=len(body),
                           latency_ms=int((time.monotonic() - started) * 1000),
                           error=f'HTTP {exc.code}')
    except urllib.error.URLError as exc:
        return FetchResult(latency_ms=int((time.monotonic() - started) * 1000),
                           error=f'bağlantı: {type(exc.reason).__name__}')
    except TimeoutError:
        return FetchResult(latency_ms=int((time.monotonic() - started) * 1000),
                           error='zaman aşımı')
    except Exception as exc:  # never leak remote content or internals
        return FetchResult(latency_ms=int((time.monotonic() - started) * 1000),
                           error=type(exc).__name__)


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

class Scripts(HTMLParser):
    """Collect application/ld+json blocks exactly like akakce_worker does."""

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
            text = ''.join(self.current).strip()
            if text:
                self.scripts.append(text)
            self.inside = False


def jsonld_blocks(raw):
    parser = Scripts()
    try:
        parser.feed(raw)
    except Exception:
        return []
    return parser.scripts


def _walk(node):
    if isinstance(node, list):
        for item in node:
            yield from _walk(item)
    elif isinstance(node, dict):
        yield node
        for value in node.values():
            if isinstance(value, (list, dict)):
                yield from _walk(value)


def jsonld_products(raw):
    """Every Product node with its offers, flattened."""
    products = []
    for block in jsonld_blocks(raw):
        try:
            data = json.loads(block)
        except (ValueError, TypeError):
            continue
        for node in _walk(data):
            kind = node.get('@type')
            kinds = kind if isinstance(kind, list) else [kind]
            if not any(str(k).lower() == 'product' for k in kinds):
                continue
            offers = node.get('offers')
            flat = []
            for offer in _walk(offers) if offers else []:
                if 'price' in offer or 'lowPrice' in offer:
                    flat.append({
                        'price': offer.get('price', offer.get('lowPrice')),
                        'currency': offer.get('priceCurrency'),
                        'availability': offer.get('availability'),
                        'seller': (offer.get('seller') or {}).get('name')
                        if isinstance(offer.get('seller'), dict) else offer.get('seller'),
                        'url': offer.get('url'),
                    })
            products.append({'name': str(node.get('name') or '')[:200],
                             'gtin': node.get('gtin13') or node.get('gtin'),
                             'offers': flat[:40]})
    return products


def embedded_state(raw):
    found = {name: len(pattern.findall(raw)) for name, pattern in EMBEDDED_PATTERNS.items()}
    prices = EMBEDDED_PRICE.findall(raw)[:60]
    seller_prices = EMBEDDED_SELLER_PRICE.findall(raw)[:40]
    return {'markers': {k: v for k, v in found.items() if v},
            'price_fields': len(prices),
            'price_samples': prices[:10],
            'seller_prices': len(seller_prices),
            'seller_price_samples': seller_prices[:10]}


def fold(text):
    """Turkish-aware lowercase so "İkinci El" matches "ikinci el"."""
    return (text or '').translate(TR_FOLD).lower()


def _label(context):
    low = fold(context)
    if any(word in low for word in SECOND_HAND):
        return 'ikinci_el'
    if any(word in low for word in POINTS):
        return 'puan'
    if any(word in low for word in WARRANTY):
        return 'garanti'
    if INSTALLMENT.search(low):
        return 'taksit'
    return 'ana_fiyat'


SEGMENT = re.compile(r'>([^<>]{1,400})<')


def _segments(raw):
    """Visible text runs between tags, with their offsets."""
    return [(match.start(1), match.end(1), ' '.join(match.group(1).split()))
            for match in SEGMENT.finditer(raw)]


def _context_for(segments, position):
    """Text of the tag holding the price plus its immediate neighbour.

    Labelling from a fixed character window mixes rows ("+18.000 puan" inside
    the width of "₺22.499,75"); a tag boundary is the row boundary.
    """
    for index, (start, end, text) in enumerate(segments):
        if start <= position < end:
            neighbour = ''
            if index + 1 < len(segments) and len(text) < 40:
                neighbour = ' ' + segments[index + 1][2]
            elif index and len(text) < 40:
                neighbour = ' ' + segments[index - 1][2]
            return (text + neighbour).strip()
    return ''


def visible_prices(raw):
    """Every Turkish price-looking string with a guess at what row it belongs to."""
    segments = _segments(raw)
    candidates, seen = [], set()
    for match in TURKISH_PRICE.finditer(raw):
        digits = match.group('pre') or match.group('post')
        if not digits:
            continue
        text = match.group(0).strip()
        key = (match.start(), digits)
        if key in seen:
            continue
        seen.add(key)
        try:
            value = cents(digits.replace('.', '').replace(',', '.'))
        except ValueError:
            continue
        context = _context_for(segments, match.start())
        candidates.append({'text': digits, 'raw': text[:40], 'cents': value,
                           'label': _label(context or match.group(0)),
                           'context': context[:140]})
        if len(candidates) >= 80:
            break
    return candidates



# ---------------------------------------------------------------------------
# Aggregator pages (Cimri / Akakçe style): per-merchant rows
# ---------------------------------------------------------------------------

MERCHANT_LOGO = re.compile(r'/merchant-logos/(\d+)\.png', re.I)
MERCHANT_COUNT = re.compile(r'\b([A-ZÇĞİÖŞÜ][\wÇĞİÖŞÜçğıöşü.&\- ]{1,24})\s*\((\d{1,3})\)')
FRESHNESS = re.compile(r'(\d{1,3})\s*(dk|dakika|saat|gün)\s*önce', re.I)
OFFER_AROUND = re.compile(r'([A-ZÇĞİÖŞÜa-zçğıöşü0-9.&\- ]{2,28})\s*\(')
MIN_HINT = re.compile(r'En Ucuz[^0-9]{0,40}(' + _NUMBER + r')', re.I)


def aggregator_analysis(raw, host=None):
    """What an aggregator page tells us about *other* stores.

    A Cimri product page lists one row per merchant with its price and how long
    ago it was refreshed, plus a "N fiyat arasında en ucuz" summary. That is the
    same information Akakçe gives, but Cimri also lists stores Akakçe does not
    (Amazon measured on 2026-11-08).
    """
    logos = MERCHANT_LOGO.findall(raw)
    counts = {}
    for name, number in MERCHANT_COUNT.findall(raw):
        name = ' '.join(name.split())
        if 1 <= len(name) <= 28 and not name.lower().startswith(('fiyat', 'taksit')):
            counts[name] = max(int(number), counts.get(name, 0))
    freshness = [(int(n), unit.lower()) for n, unit in FRESHNESS.findall(raw)]
    hint = MIN_HINT.search(raw)
    return {
        'kind': 'aggregator',
        'merchant_logo_rows': len(logos),
        'distinct_merchant_logos': len(set(logos)),
        'merchant_counts': dict(sorted(counts.items(), key=lambda kv: -kv[1])[:12]),
        'freshness_samples': sorted({f'{n} {u}' for n, u in freshness})[:8],
        'min_price_hint': hint.group(1) if hint else None,
    }


def _to_cents(value):
    """JSON-LD prices are usually "66444.03"; some sites emit "66.444,03"."""
    text = str(value).strip().replace('TL', '').replace('₺', '').strip()
    if re.search(r',\d{1,2}$', text):
        text = text.replace('.', '').replace(',', '.')
    try:
        return cents(text)
    except ValueError:
        return None


def block_state(raw, status):
    low = raw.lower()
    markers = [m for m in BLOCK_MARKERS if m in low]
    walls = [m for m in COUNTRY_WALL if m in low]
    if status is not None and status in (403, 429, 503):
        markers.append(f'HTTP {status}')
    return {'captcha_or_block': markers, 'country_wall': walls}


def ean_values(raw):
    for pattern in EAN_PATTERNS:
        for match in pattern.finditer(raw):
            return match.group(1)
    return None


def analyze(raw, status=None, host=None):
    products = jsonld_products(raw)
    prices = visible_prices(raw)
    jsonld_prices = [offer for product in products for offer in product['offers']
                     if offer.get('price') not in (None, '')]
    matches = []
    for offer in jsonld_prices:
        price = _to_cents(offer['price'])
        if price is None:
            continue
        for candidate in prices:
            if candidate['cents'] == price:
                matches.append({'jsonld': offer['price'], 'text': candidate['text'],
                                'label': candidate['label']})
                break
    block = block_state(raw, status)
    blocked = bool(block['captcha_or_block'] or block['country_wall'])
    if blocked:
        # A captcha/challenge page carries arbitrary JS with "price" keys
        # (Amazon's challenge matched 42 of them). Analysing it would invent
        # numbers, so only the block evidence is reported.
        return {
            'jsonld_blocks': len(jsonld_blocks(raw)),
            'jsonld_products': len(products),
            'jsonld_product_names': [],
            'jsonld_prices': [],
            'jsonld_currencies': [],
            'jsonld_stock': [],
            'jsonld_price_matches_visible': [],
            'embedded': {'markers': {}, 'price_fields': 0, 'price_samples': [],
                         'seller_prices': 0, 'seller_price_samples': []},
            'visible_price_count': 0,
            'visible_prices': [],
            'label_counts': {},
            'block': block,
            'ean': None,
            'analysis_skipped': 'engel sayfası',
        }
    return {
        'jsonld_blocks': len(jsonld_blocks(raw)),
        'jsonld_products': len(products),
        'jsonld_product_names': [product['name'][:80] for product in products[:3]],
        'jsonld_prices': jsonld_prices[:20],
        'jsonld_currencies': sorted({str(o.get('currency')) for o in jsonld_prices}),
        'jsonld_stock': [str(o.get('availability')) for o in jsonld_prices[:5]],
        'jsonld_price_matches_visible': matches[:10],
        'embedded': embedded_state(raw),
        'visible_price_count': len(prices),
        'visible_prices': prices[:25],
        'label_counts': _counts(prices),
        'block': block,
        'ean': ean_values(raw),
        'analysis_skipped': None,
        'aggregator': (aggregator_analysis(raw, host)
                       if SITE_KIND.get(host) == 'aggregator' else None),
    }


def _counts(prices):
    counts = {}
    for item in prices:
        counts[item['label']] = counts.get(item['label'], 0) + 1
    return counts


def probe_url(url, rounds=ROUNDS, gap=GAP_BETWEEN_URLS, sleep=time.sleep):
    """Fetch several times to measure latency and stability, analyse the last body."""
    record = {'url': url, 'host': urlsplit(url).hostname,
              'kind': SITE_KIND.get(urlsplit(url).hostname, 'store'),
              'policy': ALLOWED_HOSTS.get(urlsplit(url).hostname, {}),
              'attempts': [], 'error': None}
    last = None
    for index in range(rounds):
        if index:
            sleep(gap)
        result = fetch_once(url)
        last = result
        record['attempts'].append({
            'status': result.status, 'bytes': result.bytes,
            'latency_ms': result.latency_ms, 'redirected': result.redirected,
            'error': result.error,
        })
    latencies = [a['latency_ms'] for a in record['attempts'] if a['status'] == 200]
    statuses = [a['status'] for a in record['attempts']]
    record['status'] = statuses[-1]
    record['statuses'] = statuses
    record['up'] = bool(latencies) and len(latencies) == rounds
    record['latency_ms'] = {'min': min(latencies) if latencies else None,
                            'max': max(latencies) if latencies else None,
                            'avg': int(sum(latencies) / len(latencies)) if latencies else None}
    record['bytes'] = last.bytes if last else 0
    record['redirected'] = any(a['redirected'] for a in record['attempts'])
    if last and last.body:
        record.update(analyze(last.body, last.status, record['host']))
        record['snippet'] = _snippet(last.body)
    else:
        record['error'] = last.error if last else 'okunamadı'
    return record


def _snippet(raw):
    """Bounded evidence for fixture making: JSON-LD + embedded price contexts."""
    parts = []
    blocks = jsonld_blocks(raw)
    if blocks:
        parts.append('<!--JSONLD-->')
        parts.append(blocks[0][:SNIPPET_LIMIT // 2])
    for name in ('__PRODUCT_DETAIL_APP_INITIAL_STATE__', '__INITIAL_STATE__', '__NEXT_DATA__'):
        match = EMBEDDED_PATTERNS[name].search(raw)
        if not match:
            continue
        window = raw[match.start():match.start() + SNIPPET_LIMIT // 3]
        parts.append(f'<!--{name}-->')
        parts.append(window)
        break
    price_matches = list(TURKISH_PRICE.finditer(raw))[:6]
    for match in price_matches:
        parts.append('<!--PRICE_CONTEXT-->')
        parts.append(raw[max(0, match.start() - 600):match.end() + 600])
    if not parts:
        # Blocked pages have no JSON-LD and no price context; keep the first
        # bytes so the WAF that refused us can be identified later.
        return '<!--BLOCK_BODY-->\n' + raw[:2000]
    text = '\n'.join(parts)
    return text[:SNIPPET_LIMIT]


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _row(record):
    if record.get('error') and not record.get('status'):
        state = 'okunamadı'
    elif record.get('block', {}).get('country_wall'):
        state = 'coğrafya duvarı'
    elif record.get('block', {}).get('captcha_or_block'):
        state = 'engel/captcha'
    elif record.get('redirected'):
        state = 'yönlendirme'
    elif record.get('status') == 200 and record.get('jsonld_prices'):
        state = 'okunabilir'
    elif record.get('status') == 200:
        state = 'okundu, fiyat yok'
    else:
        state = 'hata'
    return state


def summary_markdown(records):
    if not records:
        return ('# F1 probe sonucu\n\n'
                '**Hiçbir URL ölçülmedi.** `urls` alanı boş, örnek metin ya da izin '
                'listesinde olmayan host olabilir. Birden fazla linki boşluk veya '
                'virgülle ayırarak tek satırda yapıştır.\n')
    lines = ['# F1 probe sonucu', '',
             '| Host | Durum | HTTP | Gecikme (ms) | JSON-LD | Product | Satıcı fiyatı | '
             'Görünür fiyat | EAN | Engel |',
             '|---|---|---|---|---|---|---|---|---|---|']
    for record in records:
        embedded = record.get('embedded') or {}
        delay = (record.get('latency_ms') or {}).get('avg')
        lines.append('| {host} | {state} | {status} | {delay} | {ld} | {prod} | {seller} | '
                     '{vis} | {ean} | {block} |'.format(
                         host=record.get('host', '?'),
                         state=_row(record),
                         status='/'.join(str(s) for s in record.get('statuses', [])) or '—',
                         delay=delay if delay is not None else '—',
                         ld=record.get('jsonld_blocks', '—'),
                         prod=record.get('jsonld_products', '—'),
                         seller=embedded.get('seller_prices', '—'),
                         vis=record.get('visible_price_count', '—'),
                         ean=record.get('ean') or '—',
                         block=', '.join(record.get('block', {}).get('captcha_or_block', [])
                                         + record.get('block', {}).get('country_wall', [])) or 'yok'))
    lines += ['', '## Fiyat adayları (taksit/puan/2. el tuzağı)', '']
    for record in records:
        if not record.get('visible_prices'):
            continue
        lines.append(f"**{record['host']}** — etiket sayımı: {record.get('label_counts')}")
        lines.append('')
        lines.append('| Metin | Krş | Etiket | JSON-LD eşleşmesi |')
        lines.append('|---|---|---|---|')
        matched = {m['text'] for m in record.get('jsonld_price_matches_visible', [])}
        for item in record['visible_prices'][:10]:
            mark = '✅' if item['text'] in matched else ''
            lines.append(f"| `{item.get('raw', item['text'])}` | {item['cents']} | {item['label']} | {mark} |")
        lines.append('')
    aggregators = [r for r in records if (r.get('aggregator') or None)]
    if aggregators:
        lines += ['## Toplayıcı siteler — mağaza dağılımı', '']
        lines.append('| Host | Mağaza sayısı | Mağaza (fiyat adedi) | En ucuz ipucu | Tazelik |')
        lines.append('|---|---|---|---|---|')
        for record in aggregators:
            agg = record['aggregator']
            pairs = ', '.join(f'{k} ({v})' for k, v in
                              list(agg['merchant_counts'].items())[:8]) or '—'
            lines.append('| {} | {} | {} | {} | {} |'.format(
                record.get('host', '?'), agg['distinct_merchant_logos'], pairs,
                agg.get('min_price_hint') or '—',
                ', '.join(agg['freshness_samples'][:4]) or '—'))
        lines.append('')
    lines += ['## Gömülü JSON (fiyat merdiveni 2. kademe)', '']
    lines.append('| Host | İşaretler | `price` alanı | Satıcı fiyatı |')
    lines.append('|---|---|---|---|')
    for record in records:
        embedded = record.get('embedded') or {}
        lines.append('| {} | {} | {} | {} |'.format(
            record.get('host', '?'),
            ', '.join(embedded.get('markers', {})) or 'yok',
            embedded.get('price_fields', '—'),
            embedded.get('seller_prices', '—')))
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description='F1 store probe (allowlist only)')
    parser.add_argument('--url', action='append', default=[])
    parser.add_argument('--urls', help='file with one URL per line')
    parser.add_argument('--out', default='probe-out')
    parser.add_argument('--rounds', type=int, default=ROUNDS)
    parser.add_argument('--gap', type=int, default=GAP_BETWEEN_URLS)
    parser.add_argument('--no-wait', action='store_true', help='for tests only')
    args = parser.parse_args(argv)

    entries = list(args.url)
    if args.urls:
        entries.append(Path(args.urls).read_text(encoding='utf-8'))
    raw_urls = []
    for entry in entries:
        raw_urls.extend(split_entries(entry))

    out = Path(args.out)
    (out / 'snippets').mkdir(parents=True, exist_ok=True)

    records, skipped = [], []
    sleep = (lambda _seconds: None) if args.no_wait else time.sleep
    seen_hosts = {}
    for raw in raw_urls:
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if 'ORNEK' in line.upper():
            skipped.append({'url': line, 'reason': 'örnek satır, URL girilmedi'})
            continue
        try:
            url = validate_url(line)
        except ValueError as exc:
            skipped.append({'url': line[:120], 'reason': str(exc)})
            continue
        host = urlsplit(url).hostname
        gap = ALLOWED_HOSTS[host]['min_gap']
        if host in seen_hosts:
            wait = gap - (time.monotonic() - seen_hosts[host])
            if wait > 0:
                sleep(wait)
        seen_hosts[host] = time.monotonic()
        record = probe_url(url, rounds=args.rounds, gap=max(gap, args.gap), sleep=sleep)
        records.append(record)
        seen_hosts[host] = time.monotonic()
        (out / 'snippets' / f'{host}.json').write_text(
            json.dumps({'url': url, 'snippet': record.get('snippet', '')},
                       ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"{host}: {_row(record)} (HTTP {record.get('status')})", flush=True)

    if not records:
        print('UYARI: hiçbir URL ölçülmedi. "urls" alanına ürün linki yapıştırdığından emin ol; '
              'birden fazla link boşluk veya virgülle ayrılabilir.', file=sys.stderr)

    payload = {'generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
               'policy': ALLOWED_HOSTS, 'records': records, 'skipped': skipped}
    (out / 'probe.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                    encoding='utf-8')
    summary = summary_markdown(records)
    (out / 'summary.md').write_text(summary, encoding='utf-8')
    print(summary)
    return 0


if __name__ == '__main__':
    sys.exit(main())
