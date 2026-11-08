"""Gerçek tarayıcı ölçümü: JS challenge'ı geçebiliyor mu?

2026-11-08 bulgusu: Epey `Just a moment...` (Cloudflare JS challenge), Cimri
`Güvenlik doğrulaması` döndürüyor. Bu, IP itibarı engeli DEĞİL: sunucu "önce
JavaScript'ini çalıştır, sonra konuşalım" diyor. Düz HTTP istemcisi (urllib,
curl, curl_cffi) bunu **asla** geçemez — JS motoru yok.

Bu araç tek soruyu yanıtlar: **gerçek bir Chrome, veri merkezi IP'sinden bu
challenge'ı geçebiliyor mu?** Geçebiliyorsa GitHub Actions içinde ücretsiz bir
okuma yolu var demektir. Geçemiyorsa (Turnstile etkileşim istiyorsa) tek yol
kullanıcının kendi ağıdır (yerel okuyucu).

Dürüstlük kuralları:
  - Sonuç uydurulmaz: challenge sayfası kalırsa "geçilemedi" yazar.
  - Fiyat okunursa JSON-LD'den okunur; yoksa "fiyat yok" denir.
  - Tarayıcı yoksa paketten bağımsız olarak "tarayıcı yok" denir, çökmez.

Kullanım:
    python -I tools/browser_probe.py --url <ürün-url> [--timeout 45]

Yalnız ürün sayfaları, allowlist içinde, tek istek.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

ALLOWED_HOSTS = {'www.cimri.com', 'www.epey.com'}

# Challenge sayfalarının başlıkları. Bunlardan biri kalırsa geçilemedi.
CHALLENGE_MARKERS = (
    'just a moment', 'attention required', 'güvenlik doğrulaması',
    'checking your browser', 'bir dakika', 'doğrulama',
)
# Fiyat ipuçları: JSON-LD ya da sayfada görünen fiyat alanı.
PRICE_HINTS = ('application/ld+json', '"price"', 'itemprop="price"', 'fiyat')


def classify(title, body):
    """Sayfanın challenge mı, gerçek ürün sayfası mı olduğunu söyle."""
    low_title = (title or '').strip().lower()
    challenged = any(marker in low_title for marker in CHALLENGE_MARKERS)
    has_jsonld = 'application/ld+json' in (body or '')
    return {
        'title': (title or '').strip()[:80],
        'challenged': challenged,
        'jsonld': has_jsonld,
        'price_hints': [hint for hint in PRICE_HINTS if hint in (body or '')][:4],
        'readable': (not challenged) and has_jsonld,
    }


def extract_price(body):
    """JSON-LD'den ilk TL fiyatını çıkar. Bulunamazsa None (uydurma yok)."""
    if not body:
        return None
    for match in re.finditer(r'"price"\s*:\s*"?([0-9][0-9.,]*)"?', body):
        value = _to_number(match.group(1))
        if value is not None and value > 0:
            return value
    return None


def _to_number(text):
    """'42.999,00' (TR), '42999.00' (EN), '1.299' belirsizliğini doğru çöz.

    Kural: iki ayırıcı varsa sonuncusu ondalıktır. Tek ayırıcı varsa, arkasında
    tam 3 hane ve başka ondalık yoksa binliktir; değilse ondalıktır.
    """
    text = text.strip()
    if ',' in text and '.' in text:
        if text.rfind(',') > text.rfind('.'):        # 42.999,00 → TR
            text = text.replace('.', '').replace(',', '.')
        else:                                        # 42,999.00 → EN
            text = text.replace(',', '')
    elif ',' in text:
        head, _, tail = text.rpartition(',')
        text = text.replace(',', '') if len(tail) == 3 else f'{head}.{tail}'
    elif '.' in text:
        head, _, tail = text.rpartition('.')
        text = text.replace('.', '') if len(tail) == 3 else text
    try:
        return float(text)
    except ValueError:
        return None


async def _probe_nodriver(url, timeout):
    """nodriver (Chrome DevTools) ile sayfayı aç ve challenge'ın çözülmesini bekle."""
    try:
        import nodriver  # noqa: PLC0415
    except ImportError:
        return {'error': 'nodriver kurulu değil'}

    browser = None
    try:
        browser = await nodriver.start(
            headless=True, browser_args=['--no-sandbox', '--disable-dev-shm-usage'])
    except Exception as exc:                       # noqa: BLE001 - teşhis aracı
        return {'error': f'tarayıcı başlatılamadı: {type(exc).__name__}: {str(exc)[:120]}'}

    try:
        page = await browser.get(url, new_tab=False)
        result = {}
        waited = 0.0
        step = 3.0
        while waited <= timeout:
            await asyncio.sleep(step)
            waited += step
            try:
                title = await page.evaluate('document.title')
                body = await page.evaluate('document.documentElement.outerHTML')
            except Exception:                      # noqa: BLE001 - sayfa değişiyor
                continue
            result = classify(title, body)
            result['waited_s'] = round(waited, 1)
            if result['readable']:
                result['price'] = extract_price(body)
                return result
        result.setdefault('waited_s', round(waited, 1))
        return result or {'error': 'sayfa okunamadı'}
    finally:
        try:
            browser.stop()
        except Exception:                          # noqa: BLE001 - kapanış
            pass


def probe(url, timeout=45):
    host = urlsplit(url).hostname or ''
    if host not in ALLOWED_HOSTS:
        return {'error': f'izin verilmeyen host: {host}'}
    try:
        return asyncio.run(_probe_nodriver(url, timeout))
    except RuntimeError as exc:                    # zaten bir döngü var
        return {'error': f'olay döngüsü: {str(exc)[:100]}'}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Gerçek tarayıcı challenge ölçümü')
    parser.add_argument('--url', required=True)
    parser.add_argument('--timeout', type=float, default=45.0)
    parser.add_argument('--summary-out', default='')
    args = parser.parse_args(argv)

    row = probe(args.url, args.timeout)
    row['url'] = args.url
    print(json.dumps(row, ensure_ascii=False))

    if row.get('error'):
        print(f'::warning title=tarayıcı::Başlatılamadı: {row["error"][:150]}')
        verdict = f'Tarayıcı ölçümü yapılamadı: {row["error"][:120]}'
    elif row.get('readable'):
        verdict = (f'GERÇEK TARAYICI GEÇTİ — challenge çözüldü, fiyat: '
                   f'{row.get("price", "okunamadı")}. GitHub Actions içinde ücretsiz yol var.')
        print(f'::notice title={urlsplit(args.url).hostname}::{verdict}')
    else:
        verdict = ('TARAYICI DA GEÇEMEDİ — challenge kalıcı (muhtemelen etkileşimli '
                   'doğrulama). Tek ücretsiz yol kullanıcının kendi ağı.')
        print(f'::warning title={urlsplit(args.url).hostname}::{verdict}')

    if args.summary_out:
        Path(args.summary_out).write_text(
            f'### {args.url}\n\n{verdict}\n\n```json\n'
            f'{json.dumps(row, ensure_ascii=False, indent=2)}\n```\n', encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main())
