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

import timefmt
from akakce_worker import product_url, purchase_url, target_cents

log = logging.getLogger(__name__)
MAX_PRODUCTS = 30
INTERVAL = 300
# Tek bir Telegram gönderimi döngüyü kilitlemesin (Bot API'nin kendi 30 sn
# zaman aşımı vardır; 429 yeniden denemeleri bunun üstüne biner).
SEND_TIMEOUT = 45
HOST_GAP = 10
# One source for the menu, copy buttons and recognized subcommands.
TRACK_MENU = (
    ('/takipac', 'Fiyat takibini aç'),
    ('/takipkapat', 'Fiyat takibini durdur'),
    ('/takipler', 'Ürünleri ve hedefleri göster'),
    ('/takipsil', 'Numarayla ürün sil'),
    ('/takipiptal', 'Hedef girişini iptal'),
    ('/takipyardim', 'Ayrıntılı kullanımı göster'),
)
COMMANDS = frozenset({'/takip', *(command for command, _ in TRACK_MENU)})


def tracking_menu(enabled):
    lines = ['Akakçe Takip Menüsü · ' + ('Açık' if enabled else 'Kapalı'), '']
    lines.extend(f'{command} — {description}' for command, description in TRACK_MENU)
    lines.extend(['', 'Ürün eklemek/hedef değiştirmek için Akakçe linkini gönder.',
                  'Silme: /takipsil NUMARA',
                  'Düğmeler komutu panoya kopyalar; yapıştırıp gönder.'])
    buttons = [
        {'text': command, 'copy_text': {'text': command + (' ' if command == '/takipsil' else '')}}
        for command, _ in TRACK_MENU
    ]
    return '\n'.join(lines), {'inline_keyboard': [buttons[i:i + 2] for i in range(0, len(buttons), 2)]}



def money(value):
    return f'{value / 100:,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.') + ' TL'


def clean_title(value):
    """JSON-LD başlığı bazen HTML kaçışlı gelir (``&quot;``); metinde aç."""
    return ' '.join(html.unescape(str(value or '')).split())[:250]


def notification_text(result, tracked_url, header_formatter=None):
    title = ' '.join(result['title'].split())[:250]
    if header_formatter:
        header = header_formatter(result['price'], title)
    else:
        header = f"₺{result['price'] // 100:,}".replace(',', '.') + f" — {title}"
    blocks = [header, 'Akakçe Takip Bildirimi']
    direct = purchase_url(result.get('purchase_url'))
    if direct:
        blocks.append(direct)
    blocks.append(product_url(tracked_url))
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
        url = product_url(item['url'])
        if url in urls or url != item['url']:
            raise ValueError('Tekrarlı veya geçersiz ürün')
        urls.add(url)
        for key in ('target', 'chat_id'):
            if type(item[key]) is not int or item[key] <= 0:
                raise ValueError('Geçersiz takip kaydı')
        alert = item.get('alert_price')
        if alert is not None and (type(alert) is not int or alert <= 0):
            raise ValueError('Geçersiz alarm kaydı')
        if not isinstance(item.get('title'), str) or len(item['title']) > 250:
            raise ValueError('Geçersiz başlık')
    return data


async def read_product(url):
    """One disposable process, no Telegram/GitHub credentials inherited."""
    worker = str(Path(__file__).with_name('akakce_worker.py').resolve())
    env = {key: os.environ[key] for key in ('PATH', 'LANG', 'SYSTEMROOT') if key in os.environ}
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-I', worker, product_url(url), env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        limit=65536,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=20)
        if len(output) > 16000:
            raise ValueError('İşçi yanıtı çok büyük')
        body = json.loads(output)
        if process.returncode or not body.get('ok'):
            raise ValueError('Akakçe okunamadı; erişim engeli veya sayfa değişikliği olabilir')
        result = body['result']
        if result.get('minimum_verified') is not True:
            raise ValueError('Güncel en düşük fiyat doğrulanamadı; Akakçe minimumu ve okunan teklif uyuşmalı')
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
        self.started_at = time.time()
        self.disabled_error = False
        try:
            self.state = load_state(self.path)
        except Exception:
            self.state = {'version': 1, 'enabled': False, 'items': []}
            self.disabled_error = True
            log.error('Ürün takip dosyası okunamadı; yalnız ürün takibi kapalı.')

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
        if command == '/takipiptal' or (command == '/iptal' and draft):
            self.drafts.pop(event.chat_id, None)
            await event.reply('Ürün takip taslağı iptal edildi.')
            return True
        is_link = raw.startswith(('https://www.akakce.com/', 'https://akakce.com/'))
        if command not in COMMANDS and not is_link and not (draft and not command):
            return False
        if command == '/takip':
            text, keyboard = tracking_menu(self.state['enabled'])
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
        if command == '/takipyardim':
            await event.reply('Akakçe ürün linkini gönder, ardından hedef fiyatı yaz.\n'
                              '/takipac · /takipkapat · /takipler · /takipsil NUMARA · /takipiptal\n'
                              'Akakçe güncel minimumu doğrulanarak izlenir; tüm teklifleri indirmek gerekmez.\n'
                              'Hedefi sen girersin. Ürünler/hedefler public repoya kaydedilir.\n'
                              'Alarm: fiyat < hedef. Aynı veya daha yüksek fiyat tekrar bildirilmez.\n'
                              'Kontroller hedef olarak 5 dk aralıklı; engelde ve yoğunlukta uzar.')
            return True
        try:
            if is_link:
                if not self.state['enabled']:
                    await event.reply('Ürün takibi kapalı. Açmak için /takipac; menü: /takip')
                    return True
                job = ('link', event.chat_id, product_url(raw))
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
        await asyncio.sleep(max(0, self.next_host - time.monotonic()))
        try:
            return await self.reader(url)
        finally:
            self.next_host = time.monotonic() + HOST_GAP

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
        if kind == '/takipler':
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
                            ('\n\n'.join(rows) if rows else 'Ürün yok.') + '\nMenü: /takip')
            return
        if kind == 'link':
            result = await self.fetch(value)
            # A second link replaces only this feature's own draft.
            self.drafts[chat] = {'url': value, 'title': result['title'],
                                 'until': time.monotonic() + 600}
            await self.tell(chat, f"{result['title']}\nOkunan en düşük teklif: {money(result['price'])}\n"
                            "Akakçe güncel minimumuyla doğrulandı.\nHangi fiyatın altına düşerse haber vereyim?\n"
                            'Örnek: 6500 veya 6.500,50 TL. İptal: /takipiptal')
            return
        before = copy.deepcopy(self.state)
        if kind in {'/takipac', '/takipkapat'}:
            self.state['enabled'] = kind == '/takipac'
        elif kind == 'add':
            item = next((i for i in self.state['items'] if i['url'] == value['url']), None)
            if item is None:
                if len(self.state['items']) >= MAX_PRODUCTS:
                    raise ValueError(f'İlk sürüm en fazla {MAX_PRODUCTS} ürün destekler.')
                item = {'url': value['url']}
                self.state['items'].append(item)
            item.update(title=value['title'], target=value['target'], chat_id=chat, alert_price=None)
            self.health.pop(item['url'], None)
        elif kind == '/takipsil':
            try:
                index = int(value.split()[1]) - 1
                if index < 0 or index >= len(self.state['items']):
                    raise ValueError()
            except (IndexError, ValueError):
                raise ValueError('/takipler listesindeki numarayı kullan: /takipsil 1') from None
            self.state['items'].pop(index)
        else:
            return
        ok, detail = await self.save(before)
        await self.tell(chat, ('✅ Kalıcı kaydedildi. ' if ok else '⚠️ Kalıcı kayıt başarısız; değişiklik geri alındı. ') + detail)

    async def check(self, item):
        """Tek kontrol: okuma, bildirim ve kalıcı kayıt ayrı ayrı ele alınır.

        Regresyon: eskiden okuma başarılı olsa bile bildirim/kayıt hatası aynı
        ``except`` bloğuna düşüyor, satır "Okunamadı" oluyor ve okunan fiyat
        kayboluyordu. Artık okuma ile teslimat ayrı değerlendirilir; ayrıca her
        denemenin saati tutulur, böylece "en son ne zaman denendi" görünür.
        """
        url = item['url']
        health = self.health.setdefault(url, {})
        ping = self.owner_id or item['chat_id']
        read_ok = False
        price = None
        try:
            result = await self.fetch(url)
            read_ok = True
            price = result['price']
            now = time.time()
            health.update(checked_at=now, last_attempt_at=now, status=money(price),
                          price=price, target=item['target'], failures=0, last_error=None)
            log.info('Ürün kontrolü: %s → %s (hedef %s)', clean_title(item['title'])[:60],
                     money(price), money(item['target']))
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
                log.warning('Ürün okunamadı: %s — %s (%d. başarısız deneme)',
                            url, type(exc).__name__, health['failures'])
                if health['failures'] == 3:
                    await self.tell(ping, f"⚠️ Bu ürün 3 kontroldür okunamıyor. Telegram kanal dinleme devam ediyor.\n{url}")
        finally:
            # Bildirim hatası da geri çekilmeyi büyütsün ki kopuk bir hâlde
            # 5 dakikada bir aynı mesajı denemeye çalışmasın.
            failures = health.get('failures', 0) + health.get('notify_failures', 0)
            health['due'] = time.monotonic() + min(3600, INTERVAL * 2 ** min(failures, 4))
            if health.get('failures'):
                self.next_host = max(self.next_host, time.monotonic() + 60)

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
