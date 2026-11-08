"""Yerel okuyucu (B yolu): Cimri/Epey sayfalarını **senin ağından** okur.

Neden var: 2026-11-08 ölçümü — bu iki site GitHub Actions'ın IP aralığını tanıyıp
**HTTP 403** döndürüyor. 6 varyant denendi (4 başlık kümesi + curl'ün farklı TLS
yığını), hepsi 403. Yani engel IP'ye bakıyor; kodla aşılamaz.

B yolunun çalışan tek biçimi bu: okuma **kullanıcının kendi ağından** yapılır
(evdeki internet, cep telefonu, ofis — engellenmeyen herhangi bir bağlantı).
Sonuç diske yazılır; GitHub'daki bot yalnızca **dosya** okur. Bot dosyayı okuduğu
için ağa hiç çıkmaz: Telegram bildirimleri **hiç yavaşlamaz**.

Kurallar:
  * Ücret yok: yalnız standart kütüphane, tek dosya, pip gerekmez.
  * Proxy/VPN kullanılmaz: doğrudan istek atılır, araya kimse girmez.
  * robots.txt'e uyulur: arama/karşılaştırma yolları reddedilir (aggregator_worker
    ile aynı kural).
  * Yavaş çalışır: istekler arasında `--gap` (varsayılan 45 sn) bekler.
  * Ağa çıkmazsa **hiçbir şey yazmaz**, eski veriyi bozmaz.

Kullanım
--------
Tek seferlik (elle deneme):

    python yerel_okuyucu.py --url https://www.epey.com/televizyon/tcl-65c6k.html

Sürekli (kendi bilgisayarında açık bırak):

    python yerel_okuyucu.py --watch product_watches.json

Çıktı dosyası `aggregator_feed.json` (varsayılan):

    {"version": 1, "updated_at": 1762..., "items": {
       "https://www.epey.com/televizyon/tcl-65c6k.html": {
          "title": "...", "price": 6092775, "seller": "Trendyol",
          "offer_count": 5, "verified": true, "read_at": 1762..., "source": "local"}}}

Bot bu dosyayı `aggregator_feed.json` adıyla depoda görürse Cimri/Epey
kalemlerini oradan okur; dosya yoksa eskisi gibi "403" der.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import aggregator_worker as worker  # aynı doğrulama kuralları (fail-closed)

FEED_VERSION = 1
# Veri bu kadar eskirse bot onu "bayat" sayar ve fiyat göstermez.
STALE_SECONDS = 6 * 3600


def read_one(url):
    """URL'yi oku ve feed'e yazılacak sade kaydı döndür."""
    result = worker.fetch(url)
    return {
        'title': result['title'],
        'price': result['price'],
        'currency': result.get('currency', 'TRY'),
        'seller': result.get('seller'),
        'offer_count': result.get('offer_count'),
        'stores': [offer.get('seller') for offer in result.get('offers', [])
                   if offer.get('seller')][:10],
        'verified': True,
        'read_at': int(time.time()),
        'source': 'local',
    }


def load_feed(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'version': FEED_VERSION, 'updated_at': 0, 'items': {}}
    if not isinstance(data, dict) or data.get('version') != FEED_VERSION:
        return {'version': FEED_VERSION, 'updated_at': 0, 'items': {}}
    if not isinstance(data.get('items'), dict):
        data['items'] = {}
    return data


def save_feed(path, feed):
    """Atomik yaz: yarım kalan dosya botu bozmasın."""
    feed = dict(feed)
    feed['version'] = FEED_VERSION
    feed['updated_at'] = int(time.time())
    target = Path(path)
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps(feed, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temp, target)


def urls_from_watches(path):
    """product_watches.json içindeki Cimri/Epey ürünlerini seç."""
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    urls = []
    for item in data.get('items') or []:
        url = item.get('url') if isinstance(item, dict) else None
        if not isinstance(url, str):
            continue
        try:
            worker.product_url(url)          # allowlist + yol kuralı
        except ValueError:
            continue                         # Akakçe kayıtları buraya girmez
        if url not in urls:
            urls.append(url)
    return urls


def run_once(urls, feed_path, *, gap=45.0, verbose=True):
    """Verilen URL'leri sırayla oku; başarılı olanları feed'e yaz."""
    feed = load_feed(feed_path)
    results = {}
    for index, raw in enumerate(urls):
        try:
            url = worker.product_url(raw)
        except ValueError as exc:
            results[raw] = ('atlandı', str(exc))
            if verbose:
                print(f'  atlandı: {raw} ({exc})')
            continue
        try:
            entry = read_one(url)
        except worker.Blocked as exc:
            results[raw] = ('engel', str(exc))
            if verbose:
                print(f'  engel: {url} ({exc})')
        except Exception as exc:            # noqa: BLE001 - tek URL diğerlerini durdurmaz
            results[raw] = ('hata', f'{type(exc).__name__}: {str(exc)[:120]}')
            if verbose:
                print(f'  hata: {url} ({type(exc).__name__})')
        else:
            feed['items'][url] = entry
            results[raw] = ('ok', f"{entry['price'] / 100:.2f} TL")
            if verbose:
                print(f"  ✅ {url} → {entry['price'] / 100:.2f} TL "
                      f"({entry['seller'] or '—'}, {entry['offer_count']} mağaza)")
        if index + 1 < len(urls):
            time.sleep(gap)                  # siteleri yormamak için
    save_feed(feed_path, feed)
    return results


def watch(path, feed_path, *, gap=45.0, period=600.0):
    print(f'İzleme başladı: {path} → {feed_path} '
          f'(her {int(period)} sn, istekler arası {int(gap)} sn)')
    while True:
        urls = urls_from_watches(path)
        if urls:
            print(f'[{time.strftime("%H:%M:%S")}] {len(urls)} ürün okunuyor')
            run_once(urls, feed_path, gap=gap)
        else:
            print(f'[{time.strftime("%H:%M:%S")}] takip dosyasında Cimri/Epey ürünü yok')
        time.sleep(period)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Cimri/Epey sayfalarını kendi ağından okuyup aggregator_feed.json üretir.')
    parser.add_argument('--url', action='append', default=[],
                        help='Tek ürün URL (birden fazla için tekrarla)')
    parser.add_argument('--watch', metavar='product_watches.json',
                        help='Bu dosyadaki Cimri/Epey ürünlerini sürekli oku')
    parser.add_argument('--out', default='aggregator_feed.json',
                        help='Çıktı dosyası (varsayılan aggregator_feed.json)')
    parser.add_argument('--gap', type=float, default=45.0,
                        help='İstekler arası bekleme, sn (varsayılan 45)')
    parser.add_argument('--period', type=float, default=600.0,
                        help='--watch tur aralığı, sn (varsayılan 600 = 10 dk)')
    args = parser.parse_args(argv)

    if args.watch:
        try:
            watch(args.watch, args.out, gap=max(15.0, args.gap),
                  period=max(120.0, args.period))
        except KeyboardInterrupt:
            print('\nDurduruldu. Son yazılan veri korunuyor.')
        return 0
    if not args.url:
        parser.error('--url veya --watch gerekli')
    results = run_once(args.url, args.out, gap=max(0.0, args.gap))
    ok = sum(1 for state, _ in results.values() if state == 'ok')
    print(f'\n{ok}/{len(results)} okundu → {args.out}')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
