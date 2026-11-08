"""Opt-in price tracking. No Telethon imports or channel pipeline mutations."""
from __future__ import annotations

import asyncio
import copy
import html
import json
import logging
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import timefmt
from akakce_worker import purchase_url, target_cents
from akakce_worker import product_url as akakce_product_url
from aggregator_worker import LABEL as AGGREGATOR_NAMES
from aggregator_worker import product_url as aggregator_product_url

log = logging.getLogger(__name__)


def aggregator_site(raw):
    """Link Cimri/Epey ürün sayfasıysa site adını döndürür, değilse None.

    Akakçe hattı bu dosyada *tek* yol olmaktan çıkıyor. Cimri ve Epey
    kendi okuyucusuyla (``aggregator_worker``) okunur; geri kalan her şey
    eskisi gibi Akakçe kuralından geçer, böylece Akakçe davranışı bire bir
    korunur.
    """
    try:
        return AGGREGATOR_NAMES[urlsplit((raw or '').strip()).hostname]
    except (KeyError, ValueError, AttributeError, TypeError):
        return None


def is_product_link(raw):
    return aggregator_site(raw) is not None or raw.startswith(
        ('https://www.akakce.com/', 'https://akakce.com/'))


def normalize_product_url(raw):
    """Akakçe ise Akakçe kuralı, Cimri/Epey ise toplayıcı kuralı."""
    if aggregator_site(raw):
        return aggregator_product_url(raw)
    return akakce_product_url(raw)


def tracker_kind(host):
    """Takip dosyasındaki ``kind`` alanı: okuma yöntemini belirler."""
    return 'aggregator' if host in AGGREGATOR_NAMES else 'akakce'


MAX_PRODUCTS = 30
# Geriye dönük: eski ad. Yeni kod SITE_POLICY kullanır.
INTERVAL = 300
# Tek bir Telegram gönderimi döngüyü kilitlemesin (Bot API'nin kendi 30 sn
# zaman aşımı vardır; 429 yeniden denemeleri bunun üstüne biner).
SEND_TIMEOUT = 45
HOST_GAP = 10

# --- Yenileme aralığı politikası (F3) -------------------------------------
# Her sınırın bir gerekçesi var; hiçbiri keyfi değil (plan §13.6 + §16).
#   min_gap    : aynı siteye iki istek arası dip sınır. robots.txt'te
#                Crawl-delay YOK (üçünde de kontrol edildi), sayfa ağırlığı
#                (Akakçe ~1-3 MB) ve "ban yemeyelim" kuralı belirliyor.
#   single     : tek ürün varken hedef aralık — kullanıcı isteği: az link = sık kontrol.
#   max        : otomatik seyreltmenin üst sınırı.
#   backoff    : engel/hata görülünce min_gap'in çarpılacağı tavan.
SITE_POLICY = {
    # Akakçe: 30 ürün × 10 sn = 300 sn => bugünkü döngü tam dolu. Bu yüzden
    # tek ürünü hızlandırmak EK yük getirmez, sadece 30 üründe eski hâline döner.
    'akakce': {'min_gap': 10, 'single': 180, 'max': 1800, 'backoff': 120},
    # Cimri: robots'ta Crawl-delay yok; sayfa toplayıcı listesi kadar ağır.
    'cimri': {'min_gap': 10, 'single': 180, 'max': 1800, 'backoff': 120},
    # Epey: robots AI botlarına açıkça Allow: /, Crawl-delay yok. Yine de en
    # ağır sayfa (~9 chunk menü çöpü) → dip sınır 12 sn.
    'epey': {'min_gap': 12, 'single': 240, 'max': 1800, 'backoff': 120},
}
DEFAULT_POLICY = {'min_gap': 10, 'single': 300, 'max': 1800, 'backoff': 120}
# Hata ağırlığı: geri çekilme çarpanı 2**ağırlık, max 3600 sn tavan.
BACKOFF_WEIGHT = {2: 2, 3: 3, 4: 4}

# Sepet için ADIM ARALIĞI (kullanıcıya gösterilen değer).
INTERVAL_CHOICES = (2, 5, 10, 15, 30)
DEFAULT_INTERVAL_MINUTES = 5
# One source for the menu, copy buttons and recognized subcommands.
TRACK_MENU = (
    ('/webtakipac', 'Fiyat takibini aç'),
    ('/webtakipkapat', 'Fiyat takibini durdur'),
    ('/webtakipler', 'Ürünleri ve hedefleri göster'),
    ('/webtakipsil', 'Numarayla ürün sil'),
    ('/webtakipiptal', 'Hedef girişini iptal'),
    ('/webtakipyardim', 'Ayrıntılı kullanımı göster'),
)
COMMANDS = frozenset({'/webtakip', *(command for command, _ in TRACK_MENU)})


def site_key(url):
    """Politika anahtarı: akakce / cimri / epey."""
    site = aggregator_site(url)
    return site.lower() if site else 'akakce'


def policy_for(url):
    return SITE_POLICY.get(site_key(url), DEFAULT_POLICY)


def effective_interval(bucket, wanted):
    """İstenen aralık ile sitenin dip sınırının büyüğü.

    Plan §13.6.3: tek ürün hızlı, ürün çoğalınca aralık kendiliğinden uzar.

    Formül **N × min_gap** (planda yazan `(N−1) × min_gap` değil): `due` bir
    *ürünün* kendi son okumasından sayılır, turun başlangıcından değil. 30 ürün
    tek turda ancak 30 × 10 sn = 300 sn'de bitiyor; `(N−1)` kullanılsaydı son
    ürünün süresi dolduğu hâlde sıra gelmediği için liste sürekli
    "⏳ gecikti" gösterirdi. N × min_gap tam olarak eski 300 sn'ye oturur.
    """
    count = max(1, int(bucket.get('count') or 1))
    return max(int(wanted), count * int(bucket.get('min_gap') or 10))


def bucket_target(bucket, now=None):
    """Kovaya yazılacak hedef aralık (sn)."""
    now = time.monotonic() if now is None else now
    cap = int(bucket.get('cap') or DEFAULT_POLICY['max'])
    if bucket.get('boost_until', 0) > now:
        return min(effective_interval(bucket, bucket['wanted']), cap)
    # Temiz okuma sayısı arttıkça tavana doğru iki adımda gevşe.
    factor = 1 if bucket.get('clean', 0) < 3 else 2
    return min(effective_interval(bucket, bucket['wanted']) * factor, cap)


def buckets_from_items(items):
    """Her site için tek kova: sayaç, istenen aralık, dip sınır (ölçülür)."""
    buckets = {}
    for item in items:
        key = site_key(item['url'])
        policy = SITE_POLICY.get(key, DEFAULT_POLICY)
        bucket = buckets.setdefault(key, {
            'site': key, 'count': 0, 'wanted': policy['single'],
            'min_gap': policy['min_gap'], 'cap': policy['max'],
            'backoff': policy['backoff'], 'clean': 0, 'boost_until': 0,
            'latency_ms': None, 'fail_streak': 0, 'last_check': None,
            'auto': True, 'interval': policy['single'],
        })
        bucket['count'] += 1
        # `interval_minutes` yoksa "otomatik": site kendi hızında (tek ürün hızlı).
        # Elle seçim (dosyaya `interval_minutes` yazılmışsa) o değeri geçerli kılar.
        minutes = item.get('interval_minutes')
        if isinstance(minutes, int) and minutes in INTERVAL_CHOICES:
            bucket['wanted'] = max(bucket['wanted'], minutes * 60)
            bucket['auto'] = False
    for bucket in buckets.values():
        bucket['interval'] = bucket_target(bucket)
    return buckets


def tracking_menu(enabled):
    lines = ['Ürün Takip Menüsü · ' + ('Açık' if enabled else 'Kapalı'), '']
    lines.extend(f'{command} — {description}' for command, description in TRACK_MENU)
    lines.extend(['', 'Ürün eklemek/hedef değiştirmek için Akakçe, Cimri veya Epey linkini gönder.',
                  'Silme: /webtakipsil NUMARA',
                  'Düğmeler komutu panoya kopyalar; yapıştırıp gönder.'])
    buttons = [
        {'text': command, 'copy_text': {'text': command + (' ' if command == '/webtakipsil' else '')}}
        for command, _ in TRACK_MENU
    ]
    return '\n'.join(lines), {'inline_keyboard': [buttons[i:i + 2] for i in range(0, len(buttons), 2)]}



def money(value):
    return f'{value / 100:,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.') + ' TL'


def len_minutes(seconds):
    """540 → '9 dk', 1800 → '30 dk', 45 → '1 dk'dan kısa'."""
    seconds = int(round(seconds))
    if seconds < 60:
        return '1 dk\'dan kısa'
    if seconds < 3600:
        return f'{max(1, round(seconds / 60))} dk'
    hours, rest = divmod(round(seconds / 60), 60)
    return f'{hours} sa' + (f' {rest} dk' if rest else '')


def clean_title(value):
    """JSON-LD başlığı bazen HTML kaçışlı gelir (``&quot;``); metinde aç."""
    return ' '.join(html.unescape(str(value or '')).split())[:250]


def notification_text(result, tracked_url, header_formatter=None):
    title = ' '.join(result['title'].split())[:250]
    if header_formatter:
        header = header_formatter(result['price'], title)
    else:
        header = f"₺{result['price'] // 100:,}".replace(',', '.') + f" — {title}"
    site = aggregator_site(tracked_url)
    blocks = [header, (f'{site} Takip Bildirimi' if site else 'Akakçe Takip Bildirimi')]
    # Toplayıcı siteler tüm mağazaları gezdiği için en düşük fiyatın hangi
    # mağazada olduğunu söylemek zorundayız (satıcı kilidi yok — kural: lowest).
    # Akakçe sözleşmesinde `offers` sayıdır; toplayıcılarda liste. Karıştırma yok.
    offers = result.get('offers')
    sellers = offers if isinstance(offers, list) else []
    cheapest = sellers[0] if sellers else {}
    if site and cheapest.get('seller') and cheapest.get('seller') != '(mağaza adı okunamadı)':
        blocks.append(f"En düşük fiyat: {cheapest['seller']}")
    if site and result.get('offer_count'):
        blocks.append(f"{result['offer_count']} mağaza fiyatı karşılaştırıldı")
    direct = purchase_url(result.get('purchase_url'))
    if direct:
        blocks.append(direct)
    blocks.append(normalize_product_url(tracked_url))
    return '\n\n'.join(blocks)


def load_state(path):
    if not path.exists():
        return {'version': 1, 'enabled': False, 'items': []}
    if path.stat().st_size > 150_000:
        raise ValueError('Takip dosyası çok büyük')
    data = json.loads(path.read_text(encoding='utf-8'))
    if (data.get('version') != 1 or type(data.get('enabled')) is not bool
            or not isinstance(data.get('items'), list) or len(data['items']) > MAX_PRODUCTS):
        raise ValueError('Geçersiz takip dosyası')
    urls = set()
    for item in data['items']:
        url = normalize_product_url(item['url'])
        if url in urls or url != item['url']:
            raise ValueError('Tekrarlı veya geçersiz ürün')
        urls.add(url)
        # Eski kayıtlarda "kind" yok; URL'den anlaşılır (geriye dönük uyumlu).
        if item.get('kind') not in (None, 'akakce', 'aggregator', 'cimri', 'epey'):
            raise ValueError('Geçersiz takip türü')
        for key in ('target', 'chat_id'):
            if type(item[key]) is not int or item[key] <= 0:
                raise ValueError('Geçersiz takip kaydı')
        minutes = item.get('interval_minutes')
        if minutes is not None and minutes not in INTERVAL_CHOICES:
            raise ValueError('Geçersiz kontrol sıklığı')
        alert = item.get('alert_price')
        if alert is not None and (type(alert) is not int or alert <= 0):
            raise ValueError('Geçersiz alarm kaydı')
        if not isinstance(item.get('title'), str) or len(item['title']) > 250:
            raise ValueError('Geçersiz başlık')
    return data


async def read_product(url):
    """One disposable process, no Telegram/GitHub credentials inherited."""
    site = aggregator_site(url)
    worker = str(Path(__file__).with_name(
        'aggregator_worker.py' if site else 'akakce_worker.py').resolve())
    env = {key: os.environ[key] for key in ('PATH', 'LANG', 'SYSTEMROOT') if key in os.environ}
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-I', worker, normalize_product_url(url), env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        limit=65536,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=20)
        if len(output) > 16000:
            raise ValueError('İşçi yanıtı çok büyük')
        body = json.loads(output)
        if process.returncode or not body.get('ok'):
            name = site or 'Akakçe'
            detail = body.get('reason') if isinstance(body, dict) else None
            raise ValueError(f'{name} okunamadı; erişim engeli veya sayfa değişikliği olabilir'
                             + (f' ({detail})' if site and isinstance(detail, str) else ''))
        result = body['result']
        # Akakçe "minimum_verified", Cimri/Epey "jsonld_verified" alanını yazar:
        # ikisi de aynı soruyu yanıtlar — okunan fiyat doğrulandı mı?
        verified = (result.get('jsonld_verified') if site else result.get('minimum_verified'))
        if verified is not True:
            raise ValueError('Güncel en düşük fiyat doğrulanamadı; okunan teklifler birbirini tutmalı')
        if type(result.get('price')) is not int or result['price'] <= 0:
            raise ValueError('Geçersiz fiyat')
        return result
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


class ProductTracker:
    def __init__(self, path, api, persist, reader=read_product, *, owner_id=None, header_formatter=None):
        self.path, self.api, self.persist, self.reader = Path(path), api, persist, reader
        self.owner_id = owner_id
        self.header_formatter = header_formatter
        self.queue = asyncio.Queue(maxsize=32)
        self.drafts = {}
        self.health = {}
        self.next_host = 0.0
        self.next_site = {}
        self.started_at = time.time()
        self.disabled_error = False
        try:
            self.state = load_state(self.path)
        except Exception:
            self.state = {'version': 1, 'enabled': False, 'items': []}
            self.disabled_error = True
            log.error('Ürün takip dosyası okunamadı; yalnız ürün takibi kapalı.')
        # Geriye dönük: eski kayıtlarda tür yok. Dosyayı yazmadan bellekte tamamla.
        for item in self.state['items']:
            item.setdefault('kind', tracker_kind(urlsplit(item['url']).hostname))
            item.setdefault('site', aggregator_site(item['url']) or 'Akakçe')
        self.buckets = buckets_from_items(self.state['items'])

    # --- site kovası (yenileme aralığı) ----------------------------------
    def reset_gaps(self):
        """Bekleme kapılarını sıfırla (testler ve elle tetikleme için)."""
        self.next_host = 0.0
        self.next_site = {}

    def rebuild_buckets(self):
        """Ürün listesi değişti: sayaçlar korunarak kovaları yeniden kur."""
        previous = getattr(self, 'buckets', {})
        self.buckets = buckets_from_items(self.state['items'])
        for key, bucket in self.buckets.items():
            old = previous.get(key) or {}
            for field in ('clean', 'boost_until', 'latency_ms', 'fail_streak', 'last_check'):
                if field in old:
                    bucket[field] = old[field]
            bucket['interval'] = bucket_target(bucket)

    def interval_text(self):
        """Menüde görünen tek satırlık özet."""
        if not self.buckets:
            return ('Henüz ürün yok. Eklenen ilk ürün 3 dakikada bir kontrol edilir; '
                    'ürün çoğaldıkça aralık kendiliğinden uzar.')
        rows = []
        for key, bucket in sorted(self.buckets.items()):
            name = {'akakce': 'Akakçe', 'cimri': 'Cimri', 'epey': 'Epey'}.get(key, key)
            rows.append(f"{name}: {len_minutes(bucket['interval'])} "
                        f"({bucket['count']} ürün, dip sınır {bucket['min_gap']} sn)")
        return 'Kontrol aralığı — ' + ' · '.join(rows)

    def next_interval_options(self, key):
        """Bu site için seçilebilecek aralıklar; dip sınırın altı gösterilmez."""
        floor = int((self.buckets.get(key) or DEFAULT_POLICY)['min_gap'])
        return tuple(minutes for minutes in INTERVAL_CHOICES if minutes * 60 >= floor)

    async def tell(self, chat, text):
        # Keep every product number visible, including a full 30-product list.
        # 1800 code points also fit Telegram's UTF-16 limit with emoji.
        while text:
            split = len(text) if len(text) <= 1800 else text.rfind('\n', 0, 1800)
            if split <= 0:
                split = 1800
            chunk, text = text[:split], text[split:].lstrip('\n')
            await self.api.call('sendMessage', {'chat_id': chat, 'text': chunk,
                                              'link_preview_options': {'is_disabled': True}})

    async def save(self, before):
        try:
            ok, detail = await asyncio.to_thread(self.persist, self.path, copy.deepcopy(self.state))
        except Exception:
            ok, detail = False, 'Kayıt işlemi tamamlanamadı.'
        if not ok:
            self.state = before
        return ok, detail

    async def handle(self, event, raw, *, existing_pending=False):
        """Only owner-private messages reach here. Never await Akakçe I/O here."""
        if self.owner_id is not None and event.chat_id != self.owner_id:
            return False
        command = raw.split()[0].lower() if raw.startswith('/') else ''
        if existing_pending and command not in COMMANDS:
            return False
        draft = self.drafts.get(event.chat_id)
        if draft and draft['until'] < time.monotonic():
            self.drafts.pop(event.chat_id, None)
            draft = None
        if command == '/webtakipiptal' or (command == '/iptal' and draft):
            self.drafts.pop(event.chat_id, None)
            await event.reply('Ürün takip taslağı iptal edildi.')
            return True
        is_link = is_product_link(raw)
        if command not in COMMANDS and not is_link and not (draft and not command):
            return False
        if command == '/webtakip':
            text, keyboard = tracking_menu(self.state['enabled'])
            text += '\n\n' + self.interval_text()
            try:
                await self.api.call('sendMessage', {
                    'chat_id': event.chat_id, 'text': text, 'reply_markup': keyboard,
                })
            except Exception as exc:
                # Copy buttons are optional; never retry an uncertain send.
                # Only explicit API 400 rejection can fall back to plain text.
                if getattr(exc, 'code', None) != 400:
                    raise
                await event.reply(text)
            return True
        if self.disabled_error:
            await event.reply('Takip dosyası okunamıyor. Mevcut Telegram dinleme devam ediyor; dosya düzeltilmeli.')
            return True
        if command == '/webtakipyardim':
            await event.reply('Akakçe, Cimri veya Epey ürün linkini gönder, ardından hedef fiyatı yaz.\n'
                              '/webtakipac · /webtakipkapat · /webtakipler · /webtakipsil NUMARA · /webtakipiptal\n'
                              'En düşük fiyat iki kaynaktan doğrulanarak izlenir; tüm teklifleri indirmek gerekmez.\n'
                              'Hedefi sen girersin. Ürünler/hedefler public repoya kaydedilir.\n'
                              'Alarm: fiyat < hedef. Aynı veya daha yüksek fiyat tekrar bildirilmez.\n'
                              'Sıklık: 1 ürün 3 dk (Epey 4 dk), ürün çoğaldıkça aralık kendiliğinden uzar;\n'
                              'engel/hata görülürse site kendi kendine yavaşlar.')
            return True
        try:
            if is_link:
                if not self.state['enabled']:
                    await event.reply('Ürün takibi kapalı. Açmak için /webtakipac; menü: /webtakip')
                    return True
                job = ('link', event.chat_id, normalize_product_url(raw))
            elif draft and not command:
                target = target_cents(raw)
                job = ('add', event.chat_id, {**draft, 'target': target})
            else:
                job = (command, event.chat_id, raw)
            self.queue.put_nowait(job)
            if job[0] == 'add':
                self.drafts.pop(event.chat_id, None)
            await event.reply('İşlem sıraya alındı; sonucu burada bildireceğim.')
        except (ValueError, OverflowError) as exc:
            await event.reply(str(exc))
        except asyncio.QueueFull:
            await event.reply('Ürün takip kuyruğu dolu; biraz sonra tekrar dene.')
        return True

    async def fetch(self, url):
        key = site_key(url)
        started = time.monotonic()
        await asyncio.sleep(max(0, self.next_site.get(key, 0.0) - started))
        try:
            return await self.reader(url)
        finally:
            # Dip sınır ÖLÇÜLEN yanıt süresine göre de büyür: site yavaşladıysa
            # (AutoThrottle mantığı) gap = max(dip, yanıt × 2).
            elapsed = time.monotonic() - started
            bucket = self.buckets.get(key) or {}
            floor = max(float(bucket.get('min_gap') or HOST_GAP), elapsed * 2)
            self.next_site[key] = time.monotonic() + floor
            self.next_host = max(self.next_host, self.next_site[key])

    def health_line(self, health, now, mono):
        """Saat dilimi belirsizliği bırakmayan tek satırlık kontrol özeti.

        Eski sürüm ham `time.strftime` kullanıyordu; GitHub runner'ları UTC
        olduğu için saat Türkiye saatinden 3 saat geride görünüyordu. Artık
        zaman damgası epoch olarak tutulur, TSİ etiketiyle ve canlı yaşla
        yazılır.
        """
        checked_at = health.get('checked_at')
        due = health.get('due')
        next_text = ''
        if isinstance(due, (int, float)):
            remaining = due - mono
            if remaining <= 0:
                # Süre doldu ama okuma yapılmadı: kuyruk/ağ/engel geciktiriyor.
                next_text = ' · sıradaki kontrol: ⏳ gecikti'
            else:
                next_text = f" · sıradaki kontrol: ~{timefmt.in_seconds(remaining, now)}"
        if checked_at is None:
            # Bu çalışmada henüz kontrol yok: zamanı uydurmak yerine söyle.
            return (f"Son kontrol: bu çalışmada henüz yok | Durum: {health.get('status', 'bekliyor')}"
                    f"{next_text}")
        line = (f"Son kontrol: {timefmt.since(checked_at, now)} | "
                f"Durum: {health.get('status', 'bekliyor')}"
                f"{self.target_gap(health.get('price'), health.get('target'))}")
        failures = health.get('failures') or 0
        if failures:
            line += f" ({failures} başarısız deneme)"
        return line + next_text

    @staticmethod
    def target_gap(price, target):
        """Hedefe ne kadar kaldığını söyler; alarm koşulu görünür olsun."""
        if type(price) is not int or type(target) is not int:
            return ''
        if price < target:
            return f" · hedefin {money(target - price)} altında (alarm eşiği)"
        return f" · hedefin {money(price - target)} üzerinde"

    async def job(self, kind, chat, value):
        if kind == '/webtakipler':
            rows = []
            now = time.time()
            mono = time.monotonic()
            for index, item in enumerate(self.state['items'], 1):
                h = self.health.get(item['url'], {})
                rows.append(f"{index}. {clean_title(item['title'])}\nHedef altı: {money(item['target'])}\n"
                            f"{self.health_line(h, now, mono)}\n{item['url']}")
            header = 'Takip ' + ('açık' if self.state['enabled'] else 'kapalı')
            header += f' · çalışma başlangıcı {timefmt.stamp(self.started_at)} TSİ'
            await self.tell(chat, header + '\n' +
                            ('\n\n'.join(rows) if rows else 'Ürün yok.') + '\nMenü: /webtakip')
            return
        if kind == 'link':
            result = await self.fetch(value)
            # A second link replaces only this feature's own draft.
            self.drafts[chat] = {'url': value, 'title': result['title'],
                                 'until': time.monotonic() + 600}
            site = aggregator_site(value)
            if site:
                offers = result.get('offers') if isinstance(result.get('offers'), list) else []
                cheapest = offers[0] if offers else {}
                store = cheapest.get('seller') or '(mağaza adı okunamadı)'
                source = (f"{result.get('offer_count') or len(offers)} mağaza fiyatı gezildi; "
                          f"en düşük {store}")
            else:
                source = 'Akakçe güncel minimumuyla doğrulandı'
            await self.tell(chat, f"{result['title']}\nOkunan en düşük teklif: {money(result['price'])}\n"
                            f"{source}.\nHangi fiyatın altına düşerse haber vereyim?\n"
                            'Örnek: 6500 veya 6.500,50 TL. İptal: /webtakipiptal')
            return
        before = copy.deepcopy(self.state)
        if kind in {'/webtakipac', '/webtakipkapat'}:
            self.state['enabled'] = kind == '/webtakipac'
            # Yalnız açma/kapama anahtarı değişiyor: kayıtlar ve kovalar
            # yeniden kurulmamalı (testte elle konan kayıtlar bozulmasın).
            ok, detail = await self.save(before)
            await self.tell(chat, ('✅ Kalıcı kaydedildi. ' if ok else '⚠️ Kalıcı kayıt başarısız; değişiklik geri alındı. ') + detail)
            return
        if kind == 'add':
            item = next((i for i in self.state['items'] if i['url'] == value['url']), None)
            if item is None:
                if len(self.state['items']) >= MAX_PRODUCTS:
                    raise ValueError(f'İlk sürüm en fazla {MAX_PRODUCTS} ürün destekler.')
                item = {'url': value['url'], 'kind': tracker_kind(urlsplit(value['url']).hostname),
                        'site': aggregator_site(value['url']) or 'Akakçe'}
                self.state['items'].append(item)
            item.update(title=value['title'], target=value['target'], chat_id=chat, alert_price=None)
            self.health.pop(item['url'], None)
        elif kind == '/webtakipsil':
            try:
                index = int(value.split()[1]) - 1
                if index < 0 or index >= len(self.state['items']):
                    raise ValueError()
            except (IndexError, ValueError):
                raise ValueError('/webtakipler listesindeki numarayı kullan: /webtakipsil 1') from None
            self.state['items'].pop(index)
        else:
            return
        self.rebuild_buckets()
        ok, detail = await self.save(before)
        await self.tell(chat, ('✅ Kalıcı kaydedildi. ' if ok else '⚠️ Kalıcı kayıt başarısız; değişiklik geri alındı. ') + detail
                        + ('\n' + self.interval_text() if len(self.state['items']) else ''))

    async def check(self, item):
        """Tek kontrol: okuma, bildirim ve kalıcı kayıt ayrı ayrı ele alınır.

        Regresyon: eskiden okuma başarılı olsa bile bildirim/kayıt hatası aynı
        ``except`` bloğuna düşüyor, satır "Okunamadı" oluyor ve okunan fiyat
        kayboluyordu. Artık okuma ile teslimat ayrı değerlendirilir; ayrıca her
        denemenin saati tutulur, böylece "en son ne zaman denendi" görünür.
        """
        url = item['url']
        health = self.health.setdefault(url, {})
        key = site_key(url)
        bucket = self.buckets.setdefault(key, dict(DEFAULT_POLICY, site=key, count=1,
                                                   wanted=DEFAULT_POLICY['single'],
                                                   interval=DEFAULT_POLICY['single'],
                                                   clean=0, boost_until=0, fail_streak=0))
        ping = self.owner_id or item['chat_id']
        read_ok = False
        price = None
        try:
            result = await self.fetch(url)
            read_ok = True
            bucket['last_check'] = time.time()
            price = result['price']
            now = time.time()
            health.update(checked_at=now, last_attempt_at=now, status=money(price),
                          price=price, target=item['target'], failures=0, last_error=None)
            log.info('Ürün kontrolü: %s → %s (hedef %s)', clean_title(item['title'])[:60],
                     money(price), money(item['target']))
            bucket['fail_streak'] = 0
            bucket['clean'] = int(bucket.get('clean', 0)) + 1
            if price >= item['target'] or (item.get('alert_price') is not None and price >= item['alert_price']):
                # Alarm gerekmiyor: geçmiş bir gönderim hatası geri çekilmeyi
                # kalıcı olarak şişirmesin.
                health['notify_failures'] = 0
                return
            text = notification_text(result, url, self.header_formatter)
            if len(text.encode('utf-16-le')) // 2 > 4096:
                raise ValueError('Takip bildirimi Telegram uzunluk sınırını aşıyor')
            # Asılı kalan bir gönderim döngüyü süresiz kilitlemesin.
            await asyncio.wait_for(self.api.call('sendMessage', {
                'chat_id': ping, 'text': text,
                'link_preview_options': {'is_disabled': True},
            }), timeout=SEND_TIMEOUT)
            health['notify_failures'] = 0
            before = copy.deepcopy(self.state)
            item['alert_price'] = price
            ok, _ = await self.save(before)
            if not ok:
                # Suppress repeats in this run even if remote checkpoint failed.
                current = next(i for i in self.state['items'] if i['url'] == url)
                current['alert_price'] = price
                health['status'] += ' | alarm kaydı kalıcılaştırılamadı'
                await self.tell(ping, '⚠️ Alarm gönderildi ancak GitHub kaydı başarısız; yeniden başlatmada tekrarlanabilir.')
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - tek ürün hatası döngüyü durdurmaz
            health['last_attempt_at'] = time.time()
            health['last_error'] = type(exc).__name__
            if read_ok:
                # Fiyat okundu; yalnız bildirim/kayıt tamamlanamadı. "Okunamadı"
                # demek yanlış olurdu ve fiyatı gizlerdi. Zaman aşımında mesaj
                # gitmiş de olabilir; bu yüzden "doğrulanamadı" denir.
                health['notify_failures'] = health.get('notify_failures', 0) + 1
                outcome = ('alarm gönderimi doğrulanamadı' if isinstance(exc, asyncio.TimeoutError)
                           else 'alarm iletilemedi')
                health['status'] = (f"{money(price)} · {outcome} "
                                    f"({type(exc).__name__}, {health['notify_failures']}. deneme)")
                log.warning('Takip alarmı iletilemedi: %s (%s)', url, type(exc).__name__)
            else:
                health['failures'] = health.get('failures', 0) + 1
                health['status'] = 'Okunamadı; otomatik bekleme'
                # Engel/yavaşlama sinyali: bu siteyi bir süre gevşet (plan §13.6.4).
                bucket['fail_streak'] = int(bucket.get('fail_streak', 0)) + 1
                bucket['clean'] = 0
                bucket['boost_until'] = time.monotonic() + int(bucket.get('backoff', 120))
                if bucket['fail_streak'] >= 2:
                    bucket['min_gap'] = min(300, int(bucket.get('min_gap', 10)) * 2)
                log.warning('Ürün okunamadı: %s — %s (%d. başarısız deneme)',
                            url, type(exc).__name__, health['failures'])
                if health['failures'] == 3:
                    await self.tell(ping, f"⚠️ Bu ürün 3 kontroldür okunamıyor. Telegram kanal dinleme devam ediyor.\n{url}")
        finally:
            # Bildirim hatası da geri çekilmeyi büyütsün ki kopuk bir hâlde
            # 5 dakikada bir aynı mesajı denemeye çalışmasın.
            failures = health.get('failures', 0) + health.get('notify_failures', 0)
            # Taban aralık artık sabit 300 sn değil: sitenin kendi kovası.
            bucket['interval'] = bucket_target(bucket)
            base = int(bucket['interval'])
            weight = BACKOFF_WEIGHT.get(failures, 0)
            health['due'] = time.monotonic() + min(3600, base * 2 ** weight)
            if health.get('failures'):
                self.next_site[key] = max(self.next_site.get(key, 0.0),
                                          time.monotonic() + int(bucket.get('min_gap', 10)) * 6)

    def telemetry(self):
        """Kalp atışı: her ürünün son durumu tek satırda, sonraki teşhis için.

        Kullanıcı "takip ediyor muydu, yoksa saat mi dondu?" diye sorduğunda
        cevap Actions log'unda bulunabilsin diye 30 dakikada bir yazılır.
        """
        rows = []
        now = time.time()
        for item in self.state['items']:
            health = self.health.get(item['url'], {})
            checked = health.get('checked_at')
            age = timefmt.age_text(now - checked) if checked else 'bu çalışmada yok'
            rows.append(f"{clean_title(item['title'])[:40]}={health.get('status', 'bekliyor')} ({age})")
        log.info('Takip kalp atışı: açık=%s ürün=%d | %s',
                 self.state['enabled'], len(self.state['items']), ' ; '.join(rows) or 'ürün yok')

    async def run(self):
        log.info('Ürün takibi başladı: açık=%s ürün=%d aralık=%ss',
                 self.state['enabled'], len(self.state['items']), INTERVAL)
        last_beat = time.monotonic()
        while True:
            if time.monotonic() - last_beat >= 1800:
                last_beat = time.monotonic()
                self.telemetry()
            try:
                try:
                    job = await asyncio.wait_for(self.queue.get(), timeout=1)
                except asyncio.TimeoutError:
                    job = None
                if job:
                    try:
                        await self.job(*job)
                    except Exception as exc:  # noqa: BLE001 - tek iş hatası takibi durdurmaz
                        log.warning('Ürün takip işlemi tamamlanamadı: %s: %s',
                                    type(exc).__name__, exc)
                        try:
                            await self.tell(job[1], '⚠️ Ürün takip işlemi tamamlanamadı: ' +
                                            (str(exc) if isinstance(exc, ValueError)
                                             else type(exc).__name__))
                        except Exception:  # noqa: BLE001 - yanıt da gitmezse sessiz geç
                            log.warning('Takip hata yanıtı gönderilemedi.')
                    finally:
                        self.queue.task_done()
                elif self.state['enabled'] and not self.disabled_error:
                    item = next((i for i in self.state['items']
                                 if self.health.get(i['url'], {}).get('due', 0) <= time.monotonic()), None)
                    if item:
                        await self.check(item)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - son savunma hattı
                log.exception('Ürün takip görevi hata aldı; Telegram dinleyicisi etkilenmedi.')
                await asyncio.sleep(10)
