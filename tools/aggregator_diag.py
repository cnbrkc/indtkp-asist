"""Cimri/Epey 403 teşhisi: engel **istek başlığından** mı, **IP'den** mi?

2026-11-08 ölçümü: iki toplayıcı da GitHub Actions'tan **HTTP 403** döndürdü,
hem de 140–234 ms'de (sayfa hiç inmedi). Bu, "sayfa değişti" değil, sunucunun
isteği reddetmesi. İki olası neden var ve çözümleri tamamen farklı:

    (A) başlıklar   → düzeltilebilir: düzgün Accept/Accept-Encoding/UA gönder.
    (B) veri merkezi IP'si → düzeltilemez: bu makineden o site okunamaz.

Bu araç aynı URL'yi 4 farklı başlık kümesiyle dener ve farkı tabloya yazar:

    1. mevcut worker başlıkları      (Mozilla/5.0 (compatible; indtkp-asist/1.0))
    2. tarayıcı UA                   (Chrome/Windows)
    3. tarayıcı UA + tam başlıklar   (Accept-Encoding, Accept-Language, …)
    4. mevcut UA + tam başlıklar

Yorum: 1→403 ama 3→200 ise neden (A); dördü de 403 ise neden (B).

Kullanım:
    python -I tools/aggregator_diag.py --url <urun-url> [--url ...] [--gap 12]

Yalnız ürün sayfaları, host allowlist'i içinde, tek tek ve aralıklı istek atar.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from akakce_worker import NoRedirect  # noqa: E402  (repo root module)

ALLOWED_HOSTS = {'www.cimri.com', 'www.epey.com'}
MAX_BODY = 3_000_000

CURRENT_UA = 'Mozilla/5.0 (compatible; indtkp-asist/1.0)'
BROWSER_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')

CURRENT_HEADERS = {'Accept-Language': 'tr-TR,tr;q=0.9',
                   'Accept': 'text/html,application/xhtml+xml'}
FULL_HEADERS = {
    'Accept': ('text/html,application/xhtml+xml,application/xml;q=0.9,'
               'image/avif,image/webp,*/*;q=0.8'),
    'Accept-Encoding': 'gzip, deflate, br',
    'Accept-Language': 'tr-TR,tr;q=0.9,en;q=0.8',
    'Cache-Control': 'no-cache',
    'Pragma': 'no-cache',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
    'Upgrade-Insecure-Requests': '1',
    'Connection': 'keep-alive',
}

VARIANTS = (
    ('1 mevcut worker', CURRENT_UA, CURRENT_HEADERS),
    ('2 tarayıcı UA', BROWSER_UA, CURRENT_HEADERS),
    ('3 tarayıcı UA + tam başlık', BROWSER_UA, FULL_HEADERS),
    ('4 mevcut UA + tam başlık', CURRENT_UA, FULL_HEADERS),
)

INTERESTING = ('server', 'cf-ray', 'cf-mitigated', 'x-served-by', 'x-cache',
               'via', 'x-azure-ref', 'x-amzn-waf-action', 'content-type',
               'content-length', 'set-cookie', 'retry-after')


def check_url(raw):
    parts = urlsplit((raw or '').strip())
    if parts.scheme != 'https' or (parts.hostname or '').lower() not in ALLOWED_HOSTS:
        raise ValueError(f'Yalnız Cimri/Epey ürün linkleri: {raw}')
    if parts.username or parts.password or parts.port not in (None, 443):
        raise ValueError(f'Geçersiz link: {raw}')
    return f'https://{parts.hostname}{parts.path}'


def decode(data, encoding):
    encoding = (encoding or '').lower()
    try:
        if 'gzip' in encoding:
            return gzip.decompress(data)
        if 'deflate' in encoding:
            return zlib.decompress(data)
    except (OSError, zlib.error):
        pass
    return data


def attempt(url, user_agent, headers):
    """Tek istek. Yönlendirme takip edilmez; gövde sınırlı okunur."""
    started = time.monotonic()
    request = urllib.request.Request(url, headers={'User-Agent': user_agent, **headers})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
            data = response.read(MAX_BODY + 1)
            body = decode(data, response.headers.get('Content-Encoding'))
            text = body.decode('utf-8', errors='replace')
            return {
                'status': response.status,
                'ms': int((time.monotonic() - started) * 1000),
                'bytes': len(data),
                'title': (text.split('<title>', 1)[1].split('</title>', 1)[0].strip()
                          if '<title>' in text else ''),
                'jsonld': text.count('application/ld+json'),
                'headers': {k: v for k, v in response.headers.items()
                            if k.lower() in INTERESTING},
            }
    except urllib.error.HTTPError as exc:
        return {'status': exc.code,
                'ms': int((time.monotonic() - started) * 1000),
                'headers': {k: v for k, v in (exc.headers or {}).items()
                            if k.lower() in INTERESTING}}
    except ValueError as exc:                      # NoRedirect 3xx
        return {'status': '3xx', 'error': 'yönlendirme reddedildi',
                'ms': int((time.monotonic() - started) * 1000)}
    except Exception as exc:                       # noqa: BLE001 - teşhis aracı
        return {'status': None, 'error': type(exc).__name__,
                'ms': int((time.monotonic() - started) * 1000)}


def verdict(rows):
    """Tablodan tek cümlelik sonuç."""
    by_name = {name: row.get('status') for name, row in rows}
    ok = [name for name, status in by_name.items() if status == 200]
    if len(ok) == len(by_name):
        return 'HEPSİ OKUNDU — engel yok.'
    if ok:
        return (f'BAŞLIK FARKI: {", ".join(ok)} okundu → engel başlıktan kaynaklanıyor, '
                'düzeltilebilir.')
    if all(status == 403 for status in by_name.values()):
        return ('DÖRDÜ DE 403 → engel IP/veri merkezi kaynaklı görünüyor; bu makineden '
                'okunamaz (başlıkla düzeltilemez).')
    return 'KARMA SONUÇ — ayrıntı tabloda.'


def main(argv=None):
    parser = argparse.ArgumentParser(description='Cimri/Epey 403 teşhisi')
    parser.add_argument('--url', action='append', required=True)
    parser.add_argument('--gap', type=float, default=12.0,
                        help='istekler arası bekleme (sn), varsayılan 12')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)

    report = {'generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'results': []}
    for raw in args.url:
        url = check_url(raw)
        print(f'\n## {url}')
        rows = []
        for name, agent, headers in VARIANTS:
            row = attempt(url, agent, headers)
            rows.append((name, row))
            print(f'  {name:<28} {str(row.get("status")):<5} '
                  f'{row.get("ms", 0):>5} ms  {row.get("bytes", "—")} bayt  '
                  f'{row.get("error", "")}')
            if row.get('headers'):
                print(f'      {" · ".join(f"{k}: {v}" for k, v in row["headers"].items())[:300]}')
            if row.get('title'):
                print(f'      başlık: {row["title"][:80]} · JSON-LD blok: {row["jsonld"]}')
            time.sleep(args.gap)
        summary = verdict(rows)
        print(f'  → {summary}')
        report['results'].append({'url': url, 'verdict': summary,
                                  'variants': {name: row for name, row in rows}})

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
