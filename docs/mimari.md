# Kod mimarisi

Tüm mantık eskiden tek `bot.py` dosyasındaydı (10.000+ satır, `main()` tek başına 2.600 satır).
Şimdi sorumluluklar modüllere ayrıldı.

## Katmanlar

1. **Saf modüller** (`keywords`, `sanitize`, `header`, `dedupmatch`, `commandtext`, `analysis` ...):
   ağ, global durum ve `bot.py` içe aktarma olmadan çalışan metin/mantık işleri. Birbirlerinden ve
   standart kütüphaneden başka bir şeye bağımlı değildir; testleri doğrudan bunları içe aktarır.
2. **Akış modülleri** (`controlbase`, `dmcontrol`, `sourcetest`, `controlcmds`, `delivery`,
   `dedupflow`, `listener`, `lifecycle`): eskiden `main()` içindeki iç işlevlerdi. Hepsi
   modül düzeyinde durur ve ilk parametre olarak **`rt`** (`runtime.Runtime`) alır.
3. **`appstate.py`**: çalışma zamanı durumunun tek sahibi: ayarlar, sayaçlar ve önbellekler
   (`appstate.STATS`, `appstate.DESTINATION_ID`, `appstate.CURSOR_RAM` ...). Her yerden `appstate.AD`
   olarak okunur/yazılır; `global` bildirimi gerekmez. Testler de aynı nesneleri yamalar.
4. **`bot.py`**: Telegram bağlantısı, Bot API/git yardımcıları, mesaj kurma ve `main()` (kurulum +
   işleyicileri bağlama). Akış modülleri `bot.py`'yi içe aktarmaz; testlerin yamaladığı `bot.py`
   işlevlerine (ör. `compose_message`, `send_bot_media`) `rt.bot.AD` ile erişir (döngüsel bağımlılık
   ve `python bot.py` çift yükleme sorunu olmaz).

## `rt` (Runtime) nedir?

`main()` içinde kurulan paylaşılan nesneler: `rt.client` (Telethon), `rt.store` (config),
`rt.tracker`, `rt.private_api`, `rt.private_offers`, `rt.senders`, kilitler ve `rt.bot` (çalışan
`bot` modülü; yalnızca `bot.py` işlevleri için). Eski kapanış değişkenlerinin açık hâlidir; bir işlevin neye dayandığı imzasından
ve gövdesindeki `rt.` kullanımından görülür.

## Dosyalar

| Dosya | Satır | Görevi |
|---|---:|---|
| `bot.py` | 2288 | Telegram indirim mesajı filtresi. |
| `price_filter.py` | 1666 | Fiyat ve başlık tespiti + DM fiyat kuralı. |
| `commandtext.py` | 976 | Komut metinleri, liste/filtre düzenleme akışı ve onay mantığı (saf). |
| `sanitize.py` | 813 | Mesaj temizleme, gizli bağlantılar ve bağlantı eki (saf metin/entity işleri). |
| `controlcmds.py` | 754 | Kontrol sohbeti komutları: ayar, filtre, taslak, analiz ve ana komut dağıtıcısı. |
| `dedupbadge.py` | 660 | Tekrar birleştirme rozeti: üretim, ayrıştırma ve entity kaydırma (saf). |
| `dedupmatch.py` | 605 | Tekrar birleştirme eşleştirme: ürün başlığı karşılaştırma ve fırsat anahtarı (saf). |
| `dedupflow.py` | 596 | Tekrar birleştirme akışı: gönderim öncesi/sonrası kontrol, rozet işleme ve açılış yüklemesi. |
| `producttitle.py` | 527 | Ürün bağlantısı tespiti, kupon kampanya başlığı ve arama sorgusu üretimi (saf). |
| `delivery.py` | 460 | Fırsat teslimi: hedefe kopyalama/iletme, bot bildirimi ve yeniden deneme. |
| `product_tracker.py` | 450 | Opt-in price tracking. No Telethon imports or channel pipeline mutations. |
| `analysis.py` | 405 | /analiz: mesaj geçmişi kelime analizi, başlık kelime ayıklama ve sorgu ayrıştırma (saf). |
| `listener.py` | 400 | Kaynak mesaj dinleme: olay işleme ve hızlı tarama döngüsü. |
| `akakce_worker.py` | 383 | Bounded, token-free Akakçe reader. Run in a disposable subprocess. |
| `private_bot.py` | 380 | Private command transport and exact offer copies; no channel monitoring here. |
| `dm_filters.py` | 321 | Pure decision engine for the personal Telegram offer filters. |
| `composeparts.py` | 254 | Mesaj kurma yardımcıları: gövde/ek/başlık kesme ve entity üretimi (saf). |
| `sourcetest.py` | 229 | /kaynaktest: kaynak kanalların erişim smoke testi. |
| `dmcontrol.py` | 220 | Kişisel fırsat (DM) kuyruğu ve DM komutları. |
| `botentities.py` | 210 | Bot API entity dönüşümleri ve medya tanımlayıcıları (saf). |
| `header.py` | 199 | Bildirim üst satırı: fiyat + ürün adı biçimlendirme ve sığdırma (saf). |
| `appstate.py` | 187 | Çalışma zamanı durumu: ayarlar, sayaçlar ve önbellekler — tek sahibi burasıdır. |
| `confutil.py` | 184 | config.json okuma ve değer ayrıştırma yardımcıları (saf, ağ kullanmaz). |
| `searchbuttons.py` | 147 | Fiyat satırı etiketi, arama düğmeleri ve satır içi klavye (saf). |
| `keywords.py` | 136 | Anahtar kelime eşleştirme (Türkçe büyük/küçük harf normalizasyonu dahil). |
| `smarttitle.py` | 136 | Akıllı başlık: web sayfası ipuçları ve çoklu ürün başlığı (saf). |
| `telegram_retry.py` | 123 | Bot API 429 backpressure shared by notifications, edits and private replies. |
| `quotes.py` | 122 | Alıntıda tespit edilen satırları temizleme (saf). |
| `controlbase.py` | 80 | /komut sohbeti temelleri: olay doğrulama, yanıt gönderme ve komut temizliği. |
| `timefmt.py` | 67 | Tutarlı saat biçimlendirme: her yerde Türkiye saati (TSİ) ve açık etiket. |
| `constants.py` | 63 | Telegram sınırları, ürün bağlantısı ve arama düğmesi sabitleri. |
| `lifecycle.py` | 62 | Süreç yaşam döngüsü: özel kanal durumu ve zamanlanmış yeniden başlatma. |
| `runtime.py` | 32 | main() içinde kurulan ve ayrı modüllerdeki işlevlere geçirilen çalışma zamanı bağlamı. |
| `textutil.py` | 32 | Küçük metin yardımcıları (görünen ad, süre biçimleme). |

## Yeni kod eklerken

- Saf mantığı ilgili saf modüle yaz; testi modülü doğrudan içe aktarsın (`import header`).
- Telegram çağrısı veya paylaşılan durum gerekiyorsa ilgili akış modülüne `rt` alan bir işlev ekle.
- Testler: `python -m unittest discover -s tests` (veya `python -m pytest tests`).
