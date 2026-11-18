"""Bildirim üst satırı: fiyat + ürün adı biçimlendirme ve sığdırma (saf)."""

from __future__ import annotations

import math
import re
from producttitle import _SEARCH_AMOUNT, _SEARCH_CURRENCY
from sanitize import utf16_length


# --- Bildirim üst satırı (kullanıcı isteği: fiyat EN BAŞTA, emojisiz) --------
# Tasarım (tek satır, kalın):
#     ₺56 — Penti Kız Çocuk Basic Colorful 4lü Patik Çorap
# Fiyat güvenle bulunamayınca:
#     ❓ — Penti Kız Çocuk Basic Colorful 4lü Patik Çorap
# Eskiden 1. satır ürün başlığı, 2. satır fiyat (boş satırla ayrı) idi; şimdi
# fiyat ile ad AYNI satırda ve fiyat solda, böylece kesilen telefon
# bildiriminde fiyat + adın ilk kelimeleri birlikte okunur.
# Üst satır YALNIZCA görünen metindir: arama sorguları (Google Alışveriş,
# Akakçe, Cimri) ve tekrar (dedup) anahtarı daima fiyatsız addan kurulur
# (bkz. ``is_header_price``, ``split_header_line``, ``header_search_title``).
HEADER_SEPARATOR = " — "
# Önceki sürümün üst satır ayracı (``56₺ - Ad``); yalnızca geriye dönük
# okumada (geçmiş taraması/rozet) kabul edilir, yeni bildirimde üretilmez.
PREV_HEADER_SEPARATOR = " - "
# Fiyat yazımı: para birimi işareti + kuruşsuz tutar, boşluksuz (``₺56``).
# Gövdedeki orijinal metne dokunulmaz; bu yazım yalnızca üst satırdadır.
PRICE_CURRENCY_MARK = "₺"
# Fiyat bulunamadığında uzun uyarı yazmak yerine yalnızca bu işaret konur
# (kullanıcı isteği: "fiyat bulunamadı diyor, artık sadece soru işareti koysun").
PRICE_MISSING_MARK = "❓"
# YALNIZCA geriye dönük okuma: daha önceki sürümlerin üst satırı
# "💰 12.299 TL — Ad" (emoji önekli, uzun tireli) biçimindeydi, en eski sabit
# düzen ise gövdede "💰Fiyat: …" satırı taşırdı. Yeni bildirimde 💰 üretilmez;
# bu iki sabit geçmiş bildirimleri doğru okumak için tutulur.
PRICE_LINE_PREFIX = "💰"
LEGACY_HEADER_SEPARATOR = " — "
# Üst satırın FİYAT kısmı olarak tanınan biçimler. _SEARCH_AMOUNT/_SEARCH_CURRENCY
# arama ayıklayıcısıyla AYNI tutar/para birimi tanımını kullanır (tek kaynak).
_HEADER_PRICE_RE = re.compile(
    rf"(?:{_SEARCH_CURRENCY}\s*{_SEARCH_AMOUNT}|{_SEARCH_AMOUNT}\s*{_SEARCH_CURRENCY})",
    re.IGNORECASE,
)


def is_header_price(text: str) -> bool:
    """Metin üst satırın fiyat kısmı mı? ``56₺``, ``12.299,90 TL``, ``₺1.299``, ``❓``.

    Yalnızca TAM eşleşme kabul edilir: "12.299 TL — Philips Airfryer" gibi bir
    ürün adı fiyat diye bölünmez; "2024 - Yeni Sezon" başlığında "2024" tek
    başına para birimi taşımadığı için fiyat sayılmaz.
    """
    value = " ".join((text or "").split())
    if not value:
        return False
    if value == PRICE_MISSING_MARK:
        return True
    return bool(_HEADER_PRICE_RE.fullmatch(value))


def header_price_text(value: float | None) -> str:
    """Üst satır fiyat yazısı: ``₺56``, ``₺12.299``; yoksa ``❓``.

    Yalnızca GÖSTERİM biçimlendirilir (kullanıcı isteği): kuruş, yuvarlanmadan
    atılır (``199,90`` → ``₺199``; ``12.299,99`` → ``₺12.299``) ve tutar binlik
    ayraçlarıyla yazılır. Gerçek fiyat değerine dokunulmaz: ayrıştırıcı,
    `format_price` (gövde/DM/raporlar "TL" yazımıyla devam eder), fiyat düşüşü
    ve dedup karşılaştırmaları kuruşlu gerçek değerle çalışmayı sürdürür.
    """
    if value is None or not math.isfinite(value):
        return PRICE_MISSING_MARK
    whole = math.trunc(value)  # kuruşu at; asla yuvarlama
    text = f"{whole:,}".replace(",", ".")
    return f"{PRICE_CURRENCY_MARK}{text}"


def build_header_line(price_text: str, title: str) -> str:
    """Üst satırı kur: ``<fiyat> - <ürün adı>`` ya da ``<ürün adı> ❓``.

    Fiyat varsa başta, sonra ayraç, sonra ürün adı. Fiyat yoksa (``❓``) ürün
    adı başa, ``❓`` sona yazılır (kullanıcı isteği: ``❓`` öne gelmesin).
    Parçalardan biri yoksa satır ona göre kısalır; ikisi de yoksa boş döner.
    """
    price = (price_text or "").strip()
    name = " ".join((title or "").split())
    if price == PRICE_MISSING_MARK and name:
        return f"{name} {PRICE_MISSING_MARK}"
    if price and name:
        return f"{price}{HEADER_SEPARATOR}{name}"
    return price or name


def utf16_budget(text: str, units: int) -> str:
    """Metni en fazla ``units`` UTF-16 kod birimi olacak şekilde baştan keser.

    Telegram uzunlukları UTF-16 birimiyle sayar; ``𝐆𝐔𝐂𝐂𝐈`` gibi süslü yazı ve
    astral emoji bir Python karakteri ama İKİ UTF-16 birimidir. Karakter sayısıyla
    kesmek bu yüzden sınırı aşabiliyordu (bkz. ``fit_header_line``). Kesim kod
    noktası sınırında kalır; yarım karakter üretilmez.
    """
    if units <= 0 or not text:
        return ""
    used = 0
    kept = []
    for char in text:
        cost = 2 if ord(char) > 0xFFFF else 1
        if used + cost > units:
            break
        kept.append(char)
        used += cost
    return "".join(kept)


def fit_header_line(price_text: str, title: str, room: int) -> str:
    """Üst satırı ``room`` UTF-16 birimine sığdır: fiyat korunur, adın kuyruğu düşer.

    Kapasite yetmiyorsa önce ürün adının kuyruğu "…" ile düşer; ad için hiç yer
    kalmamışsa yalnızca fiyat yazılır (fiyat, bildirimin kesilen yerinde solda
    durduğu için her koşulda en değerli parçadır). Hiç sığmıyorsa boş döner.
    Uzunluk Telegram birimiyle (UTF-16) ölçülür: ürün adı astral karakter
    içeriyorsa (süslü font marka adı, emoji) karakter sayısıyla ölçmek sınırı
    aşırıp gönderilen metni ``message is too long`` hatasına düşürebiliyordu.
    """
    price = (price_text or "").strip()
    name = " ".join((title or "").split())
    # Fiyat yoksa (❓) ürün adı başa, ❓ sona: "Ad ❓"
    if price == PRICE_MISSING_MARK:
        suffix = f" {PRICE_MISSING_MARK}"
        full = f"{name}{suffix}" if name else price
        if room <= 0 or utf16_length(full) <= room:
            return full if room > 0 else ""
        if not name:
            return price if utf16_length(price) <= room else ""
        if room - utf16_length(suffix) < 8:
            return price if utf16_length(price) <= room else ""
        tail = utf16_budget(name, room - utf16_length(suffix) - 1).rstrip()
        return f"{tail}…{suffix}"
    full = build_header_line(price, name)
    if room <= 0 or utf16_length(full) <= room:
        return full if room > 0 else ""
    if not name:
        return price if utf16_length(price) <= room else ""
    prefix = f"{price}{HEADER_SEPARATOR}" if price else ""
    if room - utf16_length(prefix) < 8:
        # Ad için ayrılacak yer yok: en azından fiyat görünsün.
        return price if utf16_length(price) <= room else ""
    tail = utf16_budget(name, room - utf16_length(prefix) - 1).rstrip()
    return f"{prefix}{tail}…"


def split_header_line(line: str | None) -> tuple[str, str]:
    """Bildirim üst satırını ``(fiyat, ürün adı)`` diye ayırır.

    Ayracın solu fiyat gibi görünmüyorsa satırın TAMAMI ürün adıdır; böylece
    içinde " - " veya "—" geçen, fiyat benzeri sayı/para birimi barındıran
    adlar yanlış bölünmez. Başta ``💰`` ile başlayan satırlar eski/ara sürüm
    bildirimlerdir: önek düşülüp aynı mantık yürütülür, fiyat okunamıyorsa
    satır "adı olmayan fiyat satırı" sayılır (geçmiş taraması o durumda
    ``_search_query``'ye düşer — eski davranışın aynısı).
    """
    text = " ".join((line or "").split())
    if not text:
        return "", ""
    legacy = text.startswith(PRICE_LINE_PREFIX)
    body = text[len(PRICE_LINE_PREFIX):].strip() if legacy else text
    # Yeni biçim: "Ad ❓" (fiyat sonda) — önce onu tanı, yoksa eski "❓ — Ad" ayraçları.
    if body == PRICE_MISSING_MARK:
        return PRICE_MISSING_MARK, ""
    if body.endswith(f" {PRICE_MISSING_MARK}"):
        title_part = body[:-len(PRICE_MISSING_MARK)].strip().rstrip(" -—")
        if title_part:
            return PRICE_MISSING_MARK, title_part
    # Güncel ayraç + önceki sürümlerin ayraçları: geçmişteki bildirimler
    # (``56₺ - Ad``, ``💰 12.299 TL — Ad``) de doğru bölünmeye devam eder.
    separators = tuple(dict.fromkeys(
        (HEADER_SEPARATOR, LEGACY_HEADER_SEPARATOR, PREV_HEADER_SEPARATOR)))
    for separator in separators:
        head, found, tail = body.partition(separator)
        head = head.strip()
        if is_header_price(head):
            return head, tail.strip() if found else ""
    return (text, "") if legacy else ("", text)


def header_search_title(text: str | None) -> str:
    """Metin bildirim üst satırıysa fiyatını düşür, arama için salt adı bırak.

    Arama sorguları normalde kaynak mesajdan (``_search_query``) ayıklanır ve
    fiyat zaten içeride değildir. Bu fonksiyon, üst satırın bir yere yanlışlıkla
    olduğu gibi taşınmasına karşı tek noktadaki sigortadır: ``₺56 — Ad`` gelir,
    ``Ad`` çıkar; tutar, para birimi, ayraç ve ``❓`` arama kutusuna girmez.
    Ayraçsız salt fiyat/``❓`` satırı aranacak bir ad taşımaz, boş döner.
    """
    text = " ".join((text or "").split())
    if not text:
        return ""
    price, title = split_header_line(text)
    return title if price else text
