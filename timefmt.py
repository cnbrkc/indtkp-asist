"""Tutarlı saat biçimlendirme: her yerde Türkiye saati (TSİ) ve açık etiket.

Sorunun kaynağı: GitHub Actions runner'ları UTC çalışır. Ham ``time.strftime``
ile yazılan "Son kontrol: 10:44" değeri aslında 13:44 TSİ idi; etiket de
olmadığı için "3 saat geride kalmış" gibi görünüyordu. Bu modül Türkiye
saatini (UTC+3, 2016'dan beri yaz saati yok) tek yerden üretir ve yanına
"TSİ" etiketi koyar.

``zoneinfo``/tzdata bulunamazsa sabit UTC+3'e düşer; Türkiye için aynı sonucu
verir, yani çıktı ortama göre değişmez.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo

    TZ = ZoneInfo('Europe/Istanbul')
except Exception:  # noqa: BLE001 - tzdata yoksa Türkiye sabit UTC+3
    TZ = timezone(timedelta(hours=3), 'TSİ')

LABEL = 'TSİ'


def local(ts: float | None = None) -> datetime:
    """Epoch saniyesini (varsayılan: şimdi) TSİ datetime'a çevir."""
    return datetime.fromtimestamp(time.time() if ts is None else float(ts), TZ)


def clock(ts: float | None = None) -> str:
    """Saat: ``13:44``."""
    return local(ts).strftime('%H:%M')


def stamp(ts: float | None = None) -> str:
    """Gün ve saat: ``06.11 13:44`` (yıl yok; gün ışığına göre karışmaz)."""
    return local(ts).strftime('%d.%m %H:%M')


def age_text(seconds: float) -> str:
    """İnsan diliyle yaş: ``az önce``, ``6 dk önce``, ``2 sa 5 dk önce``."""
    seconds = int(max(0, seconds))
    if seconds < 45:
        return 'az önce'
    if seconds < 90:
        return '1 dk önce'
    minutes = seconds // 60
    if minutes < 60:
        return f'{minutes} dk önce'
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f'{hours} sa {minutes} dk önce' if minutes else f'{hours} sa önce'
    days, hours = divmod(hours, 24)
    return f'{days} gün {hours} sa önce' if hours else f'{days} gün önce'


def since(ts: float, now: float | None = None) -> str:
    """``06.11 13:44 TSİ (6 dk önce)`` — saat dilimi belirsizliği bırakmaz."""
    return f'{stamp(ts)} {LABEL} ({age_text((time.time() if now is None else now) - ts)})'


def in_seconds(seconds: float, now: float | None = None) -> str:
    """Gelecekteki bir an için ``13:49 TSİ`` biçimi."""
    return f'{clock((time.time() if now is None else now) + max(0.0, seconds))} {LABEL}'
