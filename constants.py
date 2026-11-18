"""Telegram sınırları, ürün bağlantısı ve arama düğmesi sabitleri."""

from __future__ import annotations

import re


# Telegram sınırları (Bot API ve kullanıcı hesabı için ortak olanlar).
MESSAGE_LIMIT = 4096          # normal mesaj metni
CAPTION_LIMIT = 1024          # medya açıklaması
MESSAGE_LINK_LABEL = "Mesajı Gör"
# Ürün bağlantısı her kaynakta aynı görünür: URL bu yazının içine gömülür
# (kullanıcı isteği: "🔗 ✅ Satın Al ✅: https://…" yerine "👉 Ürün Linki 👈").
PRODUCT_LINK_TEXT = "👉 Ürün Linki 👈"
# "Ürün linki" OLAMAYACAK adresler. Mağaza ve kısaltıcı alan adları sınırsızdır
# (amzn.to, ty.gl, app.hb.biz, fenom.io …); bu yüzden beyaz liste tutulmaz, yalnız
# kesin ürün olmayanlar elenir: sohbet/sosyal ağ adresleri ve fiyat arama servisleri
# (bkz. ``is_search_link``). Alan adı TAM ya da alt alan adı olarak eşleşir:
# ``m.facebook.com`` elenir; ``netflix.com`` (x.com) ve ``x.com.magaza.example`` elenmez.
# Genel kısaltıcılar (t.co, bit.ly …) ürüne de gidebildiği için listede yoktur.
NON_PRODUCT_HOSTS = (
    "t.me", "telegram.me", "telegram.dog", "youtube.com", "youtu.be",
    "x.com", "twitter.com", "facebook.com", "tiktok.com",
    # Topluluk/sosyal ağ davet, profil ve gönderi adresleri.
    "discord.gg", "discord.com", "discordapp.com", "linkedin.com", "lnkd.in",
    "pinterest.com", "pin.it", "reddit.com", "redd.it", "threads.net", "threads.com",
    "fb.com", "fb.me", "m.me", "twitch.tv", "snapchat.com", "tumblr.com", "vk.com", "bsky.app",
    "bip.ai", "linktr.ee",
    # Çekiliş/anket formları ve paylaşılan belgeler.
    "forms.gle", "docs.google.com",
)
# Pinterest ülke alan adlarını da kullanır (pinterest.co.uk, pinterest.de, pinterest.com.tr).
# Benzer adlar (mypinterest.de, pinterest.shop, pinterest.de.magaza.example) eşleşmez.
NON_PRODUCT_HOST_RE = re.compile(r"(?:^|\.)pinterest\.(?:[a-z]{2,3}|com?\.[a-z]{2})$")
# Yolu olmayan adres mağazanın ana sayfasıdır; aşağıdaki sorgu anahtarları onu ürün
# sayfasına çevirmez (iş ortaklığı etiketi, kampanya/tıklama izleme). Bunların dışında
# tek bir anahtar (``?p=123``, ``?url=https://…``) varsa adres ürüne gidebilir sayılır.
TRACKING_QUERY_KEYS = frozenset({
    "tag", "linkcode", "linkid", "camp", "creative", "creativeasin", "ascsubtag", "ref", "ref_",
    "language", "_encoding", "ie",
    "gclid", "gbraid", "wbraid", "dclid", "fbclid", "msclkid", "yclid", "ttclid", "twclid",
    "igshid", "srsltid", "aff", "affid", "affiliate", "source", "src",
})
TRACKING_QUERY_PREFIXES = ("utm_", "adjust_", "wt_", "aff_", "pf_rd_", "pd_rd_")
# Doğrudan görsel/video dosyası ürün sayfası değildir. Kaynak botları önizleme görselini
# çoğu zaman mesajın başındaki görünmez karaktere gömer; "ilk bağlantı" o olmamalı.
MEDIA_FILE_PATH_RE = re.compile(r"\.(?:jpe?g|png|webp|gif|bmp|avif|mp4|mov|webm)(?:/|$)", re.IGNORECASE)
# Ek satırlarının karakter sınırındaki önceliği (bkz. ``_compose_fit_appendix``):
# yedekli ``🔗`` satırı gövdeye yer bırakır; vazgeçilmez ``🔗`` satırı, yalın düzende de
# yazılabiliyorsa gövdeden önce gelir; ürün satırı gövde metninden yer alabilir ama
# gövdedeki bir bağlantıyı kesemez.
APPENDIX_SPARE, APPENDIX_REQUIRED, APPENDIX_PRODUCT = range(3)
# Arama bağlantıları mesaj metnine yazılmaz; yalnızca Bot API inline klavyesine
# eklenir. Böylece arama düğmeleri Telegram'ın 4096/1024 karakter sınırını tüketmez.
# Arama düğmelerinin kullanıcı isteğindeki sabit sırası.
PRICE_SEARCH_BUTTONS = (
    ("google_shopping", "Google Alışveriş"),
    ("akakce", "Akakçe'de ara"),
    ("cimri", "Cimri'de ara"),
)
MAX_INLINE_KEYBOARD_BUTTONS = 100
BOT_API_MEDIA_LIMIT_MB = {"photo": 10, "video": 50, "document": 50}
