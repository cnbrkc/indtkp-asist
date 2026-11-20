# Kod mimarisi

Tüm mantık eskiden tek `bot.py` dosyasındaydı (10.000+ satır, `main()` tek başına 2.600 satır).
Şimdi her sorumluluğun kendi modülü var; `bot.py` yalnızca giriş noktasıdır.

## Katmanlar

1. **Saf modüller** (`keywords`, `sanitize`, `header`, `dedupmatch`, `commandtext`, `analysis`,
   `producttitle` ...): ağ ve global durum olmadan çalışan metin/mantık işleri. Testleri doğrudan
   bunları içe aktarır.
2. **`appstate.py`**: çalışma zamanı durumunun tek sahibi: ayarlar, sayaçlar, önbellekler
   (`appstate.STATS`, `appstate.DESTINATION_ID`, `appstate.CURSOR_RAM` ...). Her yerden
   `appstate.AD` olarak okunur/yazılır; `global` bildirimi gerekmez.
3. **Altyapı modülleri**: `botapi` (Bot API), `gitops` (config.json'u depoya yazma, sonraki
   çalıştırmayı tetikleme), `composer` (mesaj kurma), `chatresolve` (kanalları ID'ye çevirme),
   `runtimecfg` (config → ayarlar), `pollcursor` (hızlı tarama imleci), `statusreport` (/durum),
   `preflight` (`--check`).
4. **Akış modülleri**: `controlbase`, `dmcontrol`, `sourcetest`, `controlcmds`, `delivery`,
   `dedupflow`, `listener`, `lifecycle`. Hepsi modül düzeyinde işlevlerdir ve ilk parametre olarak
   **`rt`** (`runtime.Runtime`) alır.
5. **`bot.py`**: yalnızca giriş: argüman ayrıştırma, `--check` ve `main()` (kurulum + Telethon
   işleyicilerini bağlama). Hiçbir modül `bot.py`'yi içe aktarmaz (döngüsel bağımlılık ve
   `python bot.py` çift yükleme sorunu olmaz).

Kural: bir modül yalnızca kendinden önceki katmandan içe aktarır. Testlerin yamaladığı işlevler
çağıranlarda `modül.ad(...)` biçiminde kullanılır (ör. `botapi.send_bot_media`), bu yüzden yama
her çağıranda etkili olur.

## `rt` (Runtime) nedir?

`main()` içinde kurulan paylaşılan nesneler: `rt.client` (Telethon), `rt.store` (config),
`rt.tracker`, `rt.private_api`, `rt.private_offers`, `rt.senders`, kilitler ve `rt.gh_pat`. Eski
kapanış değişkenlerinin açık hâlidir; bir işlevin neye dayandığı imzasından ve gövdesindeki `rt.`
kullanımından görülür.

## Dosyalar

| Dosya | Satır | Görevi |
|---|---:|---|
| `price_filter.py` | 1666 | Fiyat ve başlık tespiti + DM fiyat kuralı. |
| `commandtext.py` | 976 | Komut metinleri, liste/filtre düzenleme akışı ve onay mantığı (saf). |
| `sanitize.py` | 813 | Mesaj temizleme, gizli bağlantılar ve bağlantı eki (saf metin/entity işleri). |
| `controlcmds.py` | 761 | Kontrol sohbeti komutları: ayar, filtre, taslak, analiz ve ana komut dağıtıcısı. |
| `dedupbadge.py` | 660 | Tekrar birleştirme rozeti: üretim, ayrıştırma ve entity kaydırma (saf). |
| `dedupflow.py` | 647 | Tekrar birleştirme akışı: gönderim öncesi/sonrası kontrol, rozet işleme ve açılış yüklemesi. |
| `dedupmatch.py` | 605 | Tekrar birleştirme eşleştirme: ürün başlığı karşılaştırma ve fırsat anahtarı (saf). |
| `producttitle.py` | 527 | Ürün bağlantısı tespiti, kupon kampanya başlığı ve arama sorgusu üretimi (saf). |
| `delivery.py` | 496 | Fırsat teslimi: hedefe kopyalama/iletme, bot bildirimi ve yeniden deneme. |
| `product_tracker.py` | 450 | Opt-in price tracking. No Telethon imports or channel pipeline mutations. |
| `analysis.py` | 438 | /analiz: mesaj geçmişi kelime analizi, başlık kelime ayıklama ve sorgu ayrıştırma (saf). |
| `composer.py` | 407 | Mesaj kurma: iletim zinciri, üst satır, ek bölümü ve nihai metin/entity üretimi. |
| `listener.py` | 402 | Kaynak mesaj dinleme: olay işleme ve hızlı tarama döngüsü. |
| `bot.py` | 391 | Telegram indirim mesajı filtresi. |
| `akakce_worker.py` | 383 | Bounded, token-free Akakçe reader. Run in a disposable subprocess. |
| `private_bot.py` | 380 | Private command transport and exact offer copies; no channel monitoring here. |
| `chatresolve.py` | 367 | Sohbet çözümleme: kaynak/kontrol/hedef kanalları ID'ye çevirme ve gruplar. |
| `dm_filters.py` | 321 | Pure decision engine for the personal Telegram offer filters. |
| `botapi.py` | 273 | Bot API istemcisi: çok parçalı gövde, bildirim gönderme/düzenleme/silme (Telegram bot hesabı). |
| `gitops.py` | 271 | Depoya yazma: atomik JSON, git/GitHub API ile config.json kaydı ve sonraki çalıştırmayı tetikleme. |
| `composeparts.py` | 254 | Mesaj kurma yardımcıları: gövde/ek/başlık kesme ve entity üretimi (saf). |
| `sourcetest.py` | 230 | /kaynaktest: kaynak kanalların erişim smoke testi. |
| `dmcontrol.py` | 221 | Kişisel fırsat (DM) kuyruğu ve DM komutları. |
| `preflight.py` | 216 | --check: secret, ayar ve kaynak doğrulama raporu. |
| `botentities.py` | 210 | Bot API entity dönüşümleri ve medya tanımlayıcıları (saf). |
| `header.py` | 199 | Bildirim üst satırı: fiyat + ürün adı biçimlendirme ve sığdırma (saf). |
| `appstate.py` | 192 | Çalışma zamanı durumu: ayarlar, sayaçlar ve önbellekler — tek sahibi burasıdır. |
| `confutil.py` | 184 | config.json okuma ve değer ayrıştırma yardımcıları (saf, ağ kullanmaz). |
| `searchbuttons.py` | 147 | Fiyat satırı etiketi, arama düğmeleri ve satır içi klavye (saf). |
| `keywords.py` | 136 | Anahtar kelime eşleştirme (Türkçe büyük/küçük harf normalizasyonu dahil). |
| `smarttitle.py` | 136 | Akıllı başlık: web sayfası ipuçları ve çoklu ürün başlığı (saf). |
| `telegram_retry.py` | 123 | Bot API 429 backpressure shared by notifications, edits and private replies. |
| `quotes.py` | 122 | Alıntıda tespit edilen satırları temizleme (saf). |
| `pollcursor.py` | 117 | Hızlı tarama imleci (cursor): RAM durumu, restart yedeği ve canlı olay tekilleştirme. |
| `lifecycle.py` | 111 | Süreç yaşam döngüsü: özel kanal durumu ve zamanlanmış yeniden başlatma. |
| `statusreport.py` | 96 | /durum ve kaynak durum metinleri. |
| `controlbase.py` | 94 | /komut sohbeti temelleri: olay doğrulama, yanıt gönderme ve komut temizliği. |
| `runtimecfg.py` | 87 | config.json değerlerini çalışma zamanı ayarlarına (appstate) uygulama. |
| `timefmt.py` | 67 | Tutarlı saat biçimlendirme: her yerde Türkiye saati (TSİ) ve açık etiket. |
| `constants.py` | 63 | Telegram sınırları, ürün bağlantısı ve arama düğmesi sabitleri. |
| `textutil.py` | 32 | Küçük metin yardımcıları (görünen ad, süre biçimleme). |
| `runtime.py` | 30 | main() içinde kurulan ve ayrı modüllerdeki işlevlere geçirilen çalışma zamanı bağlamı. |

## Yeni kod eklerken

- Saf mantığı ilgili saf modüle yaz; testi modülü doğrudan içe aktarsın (`import header`).
- Paylaşılan durum gerekiyorsa `appstate`'e ekle; Telegram çağrısı veya `rt` gerekiyorsa ilgili
  akış modülüne `rt` alan bir işlev ekle.
- Testler: `python -m unittest discover -s tests` (veya `python -m pytest tests`).
