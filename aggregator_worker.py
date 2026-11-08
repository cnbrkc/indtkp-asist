"""F2: Cimri ve Epey okuyucusu — Akakçe hattının yanına, ona dokunmadan.

Neden ayrı dosya: `akakce_worker.product_url()` yalnız `akakce.com` kabul ediyor ve
`product_tracker.py` **değişmez** listesinde. Bu yüzden karşılaştırma siteleri kendi
okuyucusuyla geliyor; aynı JSON sözleşmesini döndürüyor ki mevcut hat onu tanısın.

Fiyat merdiveni (C3):
    ① JSON-LD `AggregateOffer.lowPrice` / `Offer.price`
    ② sayfadaki mağaza satırları (mağaza adı + fiyat + tazelik)

Doğrulama (C4, fail-closed): JSON-LD fiyatı **mağaza satırlarındaki en düşük fiyatla**
aynı kuruşta olmalı. Tutmazsa okuma **başarısız** — yanlış fiyat gösterilmez.

Kullanım:
    python -I aggregator_worker.py <url>
    {"ok": true, "result": {"title": ..., "price": 5999000, "store": "Epey",
     "offers": [{"seller": "Hepsiburada", "price": 6118900}, ...], ...}}
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

# İzole modda (-I) sys.path[0] stdlib zip olur; komşu modülleri kendimiz ekliyoruz.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from akakce_worker import MAX_BODY, NoRedirect, cents  # noqa: E402

LABEL = {'www.cimri.com': 'Cimri', 'www.epey.com': 'Epey'}

# Cimri:   /televizyonlar/en-ucuz-tcl-t6d-televizyon-fiyatlari,a2582673366
# Epey:    /televizyon/tcl-65c6k.html   (karşılaştırma ve arama yolları robots'ta yasak)
PATHS = {
    'www.cimri.com': re.compile(r'^/[a-z0-9-]+/en-ucuz-[a-z0-9-]+-fiyatlari(?:,[a-z0-9]+)?$'),
    'www.epey.com': re.compile(r'^/[a-z0-9-]+/[a-z0-9-]+\.html$'),
}

# Epey robots.txt: Claude-User/ChatGPT-User/OAI-SearchBot'a `Allow: /`.
# Cimri robots.txt: urun sayfalari serbest, ?q=/magaza=/api/ yasak.
USER_AGENT = 'Mozilla/5.0 (compatible; indtkp-asist/1.0)'
HEADERS = {'User-Agent': USER_AGENT, 'Accept-Language': 'tr-TR,tr;q=0.9',
           'Accept': 'text/html,application/xhtml+xml'}

TR_PRICE = re.compile(r'(?<![\d.,])(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:,\d{1,2})?)(?![\d])\s*(?:TL|₺)')
FRESHNESS = re.compile(r'(\d{1,3})\s*(dk|dakika|saat|gün)\s*önce', re.I)
STORE_LINE = re.compile(
    r'([A-ZÇĞİÖŞÜ][\wÇĞİÖŞÜçğıöşü.&\- ]{1,24}?)\s*(?:fiyatı|fiyatları)\s*([\d.,]+)\s*(?:TL|₺)',
    re.I,
)
BLOCK_MARKERS = ('captcha', 'access denied', 'erişim engellendi', 'unusual traffic',
                 'are you a robot', 'too many requests', 'request denied')


class Blocked(Exception):
    """Engel sayfası: fiyat raporlanmaz (fail-closed)."""


def product_url(raw):
    """Allowlist + https + yol kuralı. Akakçe'ninkiyle aynı sıkılıkta."""
    parts = urlsplit((raw or '').strip())
    host = (parts.hostname or '').lower()
    if parts.scheme != 'https':
        raise ValueError('HTTPS link gönder.')
    if host not in PATHS:
        raise ValueError('Yalnız Cimri ve Epey ürün linkleri destekleniyor.')
    if parts.username or parts.password or parts.port not in (None, 443):
        raise ValueError('Geçersiz link.')
    if not PATHS[host].match(parts.path):
        raise ValueError('Ürün sayfası linki gönder (arama/karşılaştırma sayfası değil).')
    return urlunsplit(('https', host, parts.path, '', ''))


THOUSANDS_ONLY = re.compile(r'^\d{1,3}(?:\.\d{3})+$')


def _to_cents(text):
    """'66.444,03' · '66444.03' · '29.999' → kuruş.

    Tuzak: Türkçe binlik ayırıcı nokta. "29.999" **yirmi dokuz bin** dokuz yüz
    doksan dokuz TL'dir; "29.999"u ondalık sanan naif ayrıştırıcı 29,999 TL
    üretir ve tüm doğrulama kayar. Bu yüzden virgül yoksa ve desen
    `1-3 hane + .ddd` ise nokta binlik ayırıcıdır.
    """
    value = str(text).strip().replace('TL', '').replace('₺', '').strip()
    if not value:
        return None
    if ',' in value:                       # 66.444,03 → 66444.03
        value = value.replace('.', '').replace(',', '.')
    elif THOUSANDS_ONLY.match(value):      # 29.999 → 29999
        value = value.replace('.', '')
    try:
        return cents(value)
    except ValueError:
        return None


def _walk(node):
    if isinstance(node, list):
        for item in node:
            yield from _walk(item)
    elif isinstance(node, dict):
        yield node
        for value in node.values():
            if isinstance(value, (list, dict)):
                yield from _walk(value)


def jsonld_blocks(raw):
    blocks, inside, current = [], False, []
    for chunk in re.split(r'(<script[^>]*>|</script>)', raw, flags=re.I):
        opening = re.match(r'<script[^>]*>', chunk, re.I)
        if opening:
            inside = 'ld+json' in chunk.lower()
            current = []
            continue
        if re.fullmatch(r'</script>', chunk, re.I):
            if inside and ''.join(current).strip():
                blocks.append(''.join(current).strip())
            inside = False
            continue
        if inside:
            current.append(chunk)
    return blocks


def jsonld_offers(raw):
    """JSON-LD içindeki satıcı fiyatları (AggregateOffer.offers)."""
    found = []
    for block in jsonld_blocks(raw):
        try:
            data = json.loads(block)
        except (ValueError, TypeError):
            continue
        for node in _walk(data):
            kind = node.get('@type')
            kinds = {str(k).lower() for k in (kind if isinstance(kind, list) else [kind])}
            if 'offer' not in kinds or node.get('price') is None:
                continue
            price = _to_cents(node['price'])
            if price is None:
                continue
            seller = node.get('seller')
            name = seller.get('name') if isinstance(seller, dict) else seller
            found.append({'seller': str(name)[:40] if name else None, 'price': price})
    return found


def jsonld_price(raw):
    """AggregateOffer.lowPrice birinci tercih; tekil Offer.price ikinci."""
    for block in jsonld_blocks(raw):
        try:
            data = json.loads(block)
        except (ValueError, TypeError):
            continue
        for node in _walk(data):
            kind = node.get('@type')
            kinds = {str(k).lower() for k in (kind if isinstance(kind, list) else [kind])}
            if 'aggregateoffer' in kinds and node.get('lowPrice') is not None:
                price = _to_cents(node['lowPrice'])
                if price:
                    return price, 'aggregate_lowprice', node.get('priceCurrency')
            if 'offer' in kinds and node.get('price') is not None:
                price = _to_cents(node['price'])
                if price:
                    return price, 'offer_price', node.get('priceCurrency')
    return None, None, None


def merchant_rows(raw):
    """Mağaza satırları: 'Hepsiburada fiyatı 61.189,00 TL' ve çıplak '61.189,00 TL'.

    İki site de satır başına mağaza adı, fiyat ve '3 saat önce' tazeliği basıyor.
    Mağaza adı bulunamayan satır `seller=None` ile yine fiyat havuzuna girer; bu
    yüzden doğrulama "en düşük satır == JSON-LD" eşitliğine dayanır.
    """
    rows = []
    for match in STORE_LINE.finditer(raw):
        price = _to_cents(match.group(2))
        if price:
            rows.append({'seller': ' '.join(match.group(1).split())[:40], 'price': price})
    known = {row['price'] for row in rows}
    for match in TR_PRICE.finditer(raw):
        price = _to_cents(match.group(1))
        if price and price not in known:
            rows.append({'seller': None, 'price': price})
            known.add(price)
    return rows


LOGO_ROW = re.compile(r'merchant-logos/\d+\.png[^>]*>\s*([^<]{1,30})<', re.I)
CLEAN_NAME = re.compile(r'[^\wÇĞİÖŞÜçğıöşü.&\- ]')


def logo_rows(raw):
    """Cimri'de mağaza adı fiyatın hemen üstünde logo ile geliyor.

    Sıra: logo → 1. metin = mağaza adı → sonraki 2-4 metinden ilki fiyat.
    Ad temizlendikten sonra 2-28 karakter arası olmalı; aksi halde satır atlanır.
    """
    rows = []
    texts = [(m.end(), m.group(1)) for m in re.finditer(r'>([^<>]{1,160})<', raw)]
    for match in LOGO_ROW.finditer(raw):
        candidate = ' '.join(CLEAN_NAME.sub(' ', match.group(1)).split())
        if not 2 <= len(candidate) <= 28:
            continue
        price = None
        for _pos, text in (item for item in texts if item[0] > match.end()):
            amount = re.search(r'([\d][\d.,]*)\s*(?:TL|₺)', text)
            if amount:
                price = _to_cents(amount.group(1))
                break
        if price:
            rows.append({'seller': candidate, 'price': price})
    return rows


def page_title(raw):
    match = re.search(r'<title[^>]*>(.*?)</title>', raw, re.S | re.I)
    if not match:
        return ''
    title = re.sub(r'\s+', ' ', match.group(1)).strip()
    return re.split(r'\s+[|·-]\s+', title)[0][:200]


def parse(raw, url):
    """Sayfayı çöz; tutmazsa ValueError (fail-closed)."""
    low = raw.lower()
    if any(marker in low for marker in BLOCK_MARKERS):
        raise Blocked('engel sayfası')
    price, source, currency = jsonld_price(raw)
    if price is None:
        raise ValueError('Yapısal fiyat (JSON-LD) bulunamadı; doğrulama yapılamaz.')
    if currency not in (None, 'TRY'):
        raise ValueError('Para birimi TRY değil.')

    structured = jsonld_offers(raw)
    rows = logo_rows(raw) or merchant_rows(raw)
    pool = [row['price'] for row in (structured or rows)]
    if not pool:
        raise ValueError('Mağaza satırı bulunamadı; çift doğrulama yapılamaz.')
    cheapest = min(pool)
    if cheapest != price:
        raise ValueError('JSON-LD fiyatı mağaza satırlarındaki en düşükle uyuşmuyor.')

    offers = structured or rows
    cheapest_row = min(offers, key=lambda row: row['price'])
    sellers = [row['seller'] for row in offers if row.get('seller')]
    return {
        'title': page_title(raw) or '(başlık okunamadı)',
        'price': price,
        'currency': 'TRY',
        'store': LABEL[urlsplit(url).hostname],
        'seller': cheapest_row['seller'] or (sellers[0] if sellers else '(mağaza adı okunamadı)'),
        'offers': sorted(offers, key=lambda row: row['price'])[:40],
        'offer_count': len(offers),
        'seller_count': len(sellers),
        'price_source': source,
        'jsonld_verified': True,
        'url': url,
    }


def fetch(url):
    url = product_url(url)
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
        data = response.read(MAX_BODY + 1)
        if len(data) > MAX_BODY:
            raise ValueError('Sayfa boyutu sınırı aşıldı')
    return parse(data.decode('utf-8', errors='replace'), url)


def error_code(exc):
    """Hatanın makine-okur sınıfı — kullanıcıya doğru cümleyi yazabilmek için.

    2026-11-08 canlı doğrulama: iki toplayıcı da HTTP 403 döndürdü ve kullanıcıya
    "sayfa değişikliği olabilir" dendi. Yanlış teşhis. Artık sınıf ayrı taşınır.
    """
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            return 'BLOCKED'
        if exc.code == 429:
            return 'RATE_LIMITED'
        return f'HTTP_{exc.code}'
    if isinstance(exc, Blocked):
        return 'BLOCKED'
    if isinstance(exc, urllib.error.URLError):
        return 'NETWORK'
    if isinstance(exc, TimeoutError):
        return 'TIMEOUT'
    return 'PARSE'


if __name__ == '__main__':
    try:
        print(json.dumps({'ok': True, 'result': fetch(sys.argv[1])}, ensure_ascii=False))
    except Exception as exc:  # uzak içerik veya iç ayrıntı sızdırılmaz
        report = {'ok': False, 'error': type(exc).__name__, 'code': error_code(exc)}
        if isinstance(exc, urllib.error.HTTPError):
            report['http_status'] = exc.code
        elif isinstance(exc, (ValueError, Blocked)):
            report['reason'] = str(exc)[:200]
        print(json.dumps(report, ensure_ascii=False))
        sys.exit(1)
