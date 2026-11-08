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
import subprocess
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

# (ad, istemci, UA, başlıklar). "istemci" önemli: Python/urllib'in TLS parmak
# izi birçok WAF tarafından ayrıca işaretlenir. curl farklı bir TLS yığını
# kullanır; ikisi arasındaki fark "IP engeli" ile "istemci parmak izi"ni ayırır.
VARIANTS = (
    ('1 urllib · mevcut UA', 'urllib', CURRENT_UA, CURRENT_HEADERS),
    ('2 urllib · tarayıcı UA', 'urllib', BROWSER_UA, CURRENT_HEADERS),
    ('3 urllib · tarayıcı UA + tam başlık', 'urllib', BROWSER_UA, FULL_HEADERS),
    ('4 urllib · mevcut UA + tam başlık', 'urllib', CURRENT_UA, FULL_HEADERS),
    ('5 curl · mevcut UA', 'curl', CURRENT_UA, CURRENT_HEADERS),
    ('6 curl · tarayıcı UA + tam başlık', 'curl', BROWSER_UA, FULL_HEADERS),
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


def attempt_curl(url, user_agent, headers, workdir='/tmp'):
    """curl ile tek istek. Yönlendirme TAKİP EDİLMEZ (-L yok).

    Neden ayrı istemci: Python/urllib'in TLS parmak izi bilinen bir WAF
    imzasıdır. curl farklı bir yığın kullanır. İkisinin farklı cevap vermesi
    "IP engeli" değil "istemci parmak izi" demektir.
    """
    import os
    import subprocess
    body_path = os.path.join(workdir, 'diag_body.txt')
    command = ['curl', '-sS', '--max-time', '20', '--max-filesize', str(MAX_BODY),
               '-o', body_path, '-w', '%{http_code} %{size_download} %{time_total}',
               '-A', user_agent]
    for key, value in headers.items():
        command += ['-H', f'{key}: {value}']
    command.append(url)
    started = time.monotonic()
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=40)
    except (OSError, subprocess.SubprocessError) as exc:
        return {'status': None, 'error': type(exc).__name__,
                'ms': int((time.monotonic() - started) * 1000)}
    if proc.returncode != 0:
        return {'status': None, 'error': f'curl {proc.returncode}: {proc.stderr.strip()[:120]}',
                'ms': int((time.monotonic() - started) * 1000)}
    parts = (proc.stdout or '').split()
    status = int(parts[0]) if parts and parts[0].isdigit() else None
    row = {'status': status,
           'ms': int((time.monotonic() - started) * 1000),
           'bytes': int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0,
           'client': 'curl'}
    if status == 200:
        try:
            text = Path(body_path).read_text(encoding='utf-8', errors='replace')
        except OSError:
            text = ''
        row['title'] = (text.split('<title>', 1)[1].split('</title>', 1)[0].strip()
                        if '<title>' in text else '')
        row['jsonld'] = text.count('application/ld+json')
    return row


def run_variant(url, client, user_agent, headers):
    if client == 'curl':
        return attempt_curl(url, user_agent, headers)
    return attempt(url, user_agent, headers)


def verdict(rows):
    """Tablodan tek cümlelik sonuç."""
    by_name = {name: row.get('status') for name, row in rows}
    ok = [name for name, status in by_name.items() if status == 200]
    curl_ok = [name for name in ok if name.startswith('5') or name.startswith('6')]
    urllib_ok = [name for name in ok if name[:1] in '1234']
    if len(ok) == len(by_name):
        return 'HEPSİ OKUNDU — engel yok.'
    if ok and curl_ok and not urllib_ok:
        return ('İSTEMCİ FARKI: curl okudu, urllib okumadı → engel IP değil, '
                'HTTP istemcisinin parmak izi. Worker curl ile okumaya geçirilebilir.')
    if ok:
        return (f'BAŞLIK FARKI: {", ".join(ok)} okundu → engel başlıktan kaynaklanıyor, '
                'düzeltilebilir.')
    if all(status == 403 for status in by_name.values()):
        return ('HEPSİ 403 (iki istemci, dört başlık kümesi) → engel IP/veri merkezi '
                'kaynaklı; başlıkla veya istemciyle düzeltilemez.')
    statuses = [row.get('status') for _, row in rows]
    return ('KARMA SONUÇ — dördü de okunamadı ama 403 de değil: '
            f'{statuses}. Ağ/başlık kombinasyonu denendi.')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Cimri/Epey 403 teşhisi')
    parser.add_argument('--url', action='append', required=True)
    parser.add_argument('--gap', type=float, default=12.0,
                        help='istekler arası bekleme (sn), varsayılan 12')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--annotate', action='store_true',
                        help='Sonucu GitHub annotation olarak **stdout**a yaz. '
                             'Bu satırlar dosyaya yönlendirilirse GitHub görmez.')
    parser.add_argument('--summary-out', metavar='PATH',
                        help='İnsan okur tabloyu bu dosyaya yaz (GITHUB_STEP_SUMMARY)')
    parser.add_argument('--json-out', metavar='PATH',
                        help='Makine okur sonucu bu dosyaya yaz')
    args = parser.parse_args(argv)

    report = {'generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'results': []}
    # `--summary-out` varsa tablo oraya, annotation satırları stdout'a gider.
    import contextlib
    import io
    table = io.StringIO()
    sink = contextlib.redirect_stdout(table) if args.summary_out else contextlib.nullcontext()
    with sink:
        for raw in args.url:
            url = check_url(raw)
            print(f'\n## {url}')
            rows = []
            for name, client, agent, headers in VARIANTS:
                row = run_variant(url, client, agent, headers)
                rows.append((name, row))
                print(f'  {name:<36} {str(row.get("status")):<5} '
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

    if args.summary_out:
        Path(args.summary_out).write_text(table.getvalue(), encoding='utf-8')
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                      encoding='utf-8')
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.annotate:
        # Log arşivi bazen API'den okunamıyor; annotation check-run API'sinde kalır.
        for entry in report['results']:
            for name, row in entry['variants'].items():
                print(f'::notice title={name}::HTTP {row.get("status")} · '
                      f'{row.get("ms")} ms · {row.get("bytes", "—")} bayt · '
                      f'{entry["url"].split("/")[2]}')
            print(f'::warning title={entry["url"].split("/")[2]}::{entry["verdict"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
