# Kod mimarisi

Tüm mantık eskiden tek `bot.py` dosyasındaydı (10.000+ satır). Şimdi sorumluluklar modüllere ayrıldı.
Kural: **saf modüller** (ağ, global durum ve `bot.py` içe aktarmadan bağımsız) yalnızca yardımcı
modüllerden içe aktarır; **`bot.py`** Telegram bağlantısını, çalışma zamanı durumunu ve ana akışı
(`main()`) taşır. Böylece bir modül `bot.py`'yi içe aktarmaz ve döngüsel bağımlılık oluşmaz.

| Dosya | Satır | Görevi |
|---|---:|---|
| `bot.py` | 4957 | Telegram indirim mesajı filtresi. |
| `price_filter.py` | 1666 | Fiyat ve başlık tespiti + DM fiyat kuralı. |
| `commandtext.py` | 976 | Komut metinleri, liste/filtre düzenleme akışı ve onay mantığı (saf). |
| `sanitize.py` | 813 | Mesaj temizleme, gizli bağlantılar ve bağlantı eki (saf metin/entity işleri). |
| `dedupbadge.py` | 660 | Tekrar birleştirme rozeti: üretim, ayrıştırma ve entity kaydırma (saf). |
| `dedupmatch.py` | 605 | Tekrar birleştirme eşleştirme: ürün başlığı karşılaştırma ve fırsat anahtarı (saf). |
| `producttitle.py` | 527 | Ürün bağlantısı tespiti, kupon kampanya başlığı ve arama sorgusu üretimi (saf). |
| `product_tracker.py` | 450 | Opt-in price tracking. No Telethon imports or channel pipeline mutations. |
| `analysis.py` | 405 | /analiz: mesaj geçmişi kelime analizi, başlık kelime ayıklama ve sorgu ayrıştırma (saf). |
| `akakce_worker.py` | 383 | Bounded, token-free Akakçe reader. Run in a disposable subprocess. |
| `private_bot.py` | 380 | Private command transport and exact offer copies; no channel monitoring here. |
| `dm_filters.py` | 321 | Pure decision engine for the personal Telegram offer filters. |
| `composeparts.py` | 254 | Mesaj kurma yardımcıları: gövde/ek/başlık kesme ve entity üretimi (saf). |
| `botentities.py` | 210 | Bot API entity dönüşümleri ve medya tanımlayıcıları (saf). |
| `header.py` | 199 | Bildirim üst satırı: fiyat + ürün adı biçimlendirme ve sığdırma (saf). |
| `confutil.py` | 184 | config.json okuma ve değer ayrıştırma yardımcıları (saf, ağ kullanmaz). |
| `searchbuttons.py` | 147 | Fiyat satırı etiketi, arama düğmeleri ve satır içi klavye (saf). |
| `keywords.py` | 136 | Anahtar kelime eşleştirme (Türkçe büyük/küçük harf normalizasyonu dahil). |
| `smarttitle.py` | 136 | Akıllı başlık: web sayfası ipuçları ve çoklu ürün başlığı (saf). |
| `telegram_retry.py` | 123 | Bot API 429 backpressure shared by notifications, edits and private replies. |
| `quotes.py` | 122 | Alıntıda tespit edilen satırları temizleme (saf). |
| `timefmt.py` | 67 | Tutarlı saat biçimlendirme: her yerde Türkiye saati (TSİ) ve açık etiket. |
| `constants.py` | 63 | Telegram sınırları, ürün bağlantısı ve arama düğmesi sabitleri. |
| `textutil.py` | 32 | Küçük metin yardımcıları (görünen ad, süre biçimleme). |

Testler: `python -m unittest discover -s tests` (veya `python -m pytest tests`). Yeni kod eklerken
saf mantığı ilgili modüle, durum veya Telegram çağrısı gerektireni `bot.py`'ye koy; testler
modülü doğrudan içe aktarır (`import header`, `import sanitize` ...).
