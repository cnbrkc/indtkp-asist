# Bildirim düzeni, hız ve güvenilirlik

## 6. Bildirim kurulumu (telefona uyarı gelsin)

Takipçi **kendi Telegram hesabınla** gönderir; Telegram kendi gönderdiğin mesajlar için
bildirim üretmez. Bu yüzden fırsat gruba düşse bile telefonuna uyarı gelmez. Çözüm: gruba
ikinci bir gönderici olarak küçük bir bot eklemek — bildirimi onun attığı mesaj üretir.

**Bildirim biçimi (basit düzen):** mesajlar üzerinde oynama yapılmaz; kaynak mesaj
**olduğu gibi** iletilir. Düzen sabittir (kullanıcı isteği):

1. **Üst satır** en üstte (kalın) — `smart_header` açıksa **önce fiyat, sonra ` — `,
   sonra ürün adı** tek satırda: `₺56 — Penti Kız Çocuk Basic Colorful 4lü Patik Çorap`.
   Fiyatın önünde emoji **yok**, yazım `56 TL` değil **`₺56`** (kuruş gösterilmez;
   `199,90` → `₺199`, yuvarlama yapılmaz). Fiyat güvenle bulunamazsa fiyat yerine
   yalnızca **`❓`** gelir: `❓ — Penti Kız Çocuk Basic Colorful 4lü Patik Çorap`. `smart_header` kapalıysa
   üst satırda yalnızca ürün başlığı yazılır (eski hâli). Fiyatın başta durması,
   telefonun bildiriminde kesilen metinde **fiyat + ürün adının ilk kelimelerinin**
   birlikte görünmesini sağlar.
2. Altına **`👉 Ürün Linki 👈`** — ürün adresi bu yazının içine gömülüdür (dokununca
   açılır, URL metinde görünmez). Kaynak adresi butona, yazının altına ya da açık
   URL olarak koysa da satır her grupta aynıdır; alıntıdaki özgün bağlantı yedek
   olarak yerinde kalır ([8. bölüm](iletim-ve-linkler.md#8-gizli-bağlantılar-ve-fiyat-arama-düğmeleri)).
3. Altına **orijinal mesaj** — satırları, fiyatı, linkleri ve emojileriyle aynen,
   ürün linki satırının altında.
4. En alta **kaynak grup adı** (kalın). Orijinal mesajın adresi (`t.me/…`) bu adın
   içine gömülüdür: ada dokunmak mesajı açar. Ayrı `🔗 Mesajı Gör` satırı **yoktur**
   (kullanıcı isteği: satır kaldırıldı, adres ada gömüldü).

```text
₺225 — Palmolive Moments Lavanta Yağları ve Böğürtlen ile Nemlendirici Banyo ve Duş Jeli 500ml x 4 Adet

🛍️ Palmolive Moments Lavanta Yağları ve Böğürtlen ile Nemlendirici Banyo ve Duş Jeli 500ml x 4 Adet

💰 Fiyat : 225 TL

🗓️ 365 Günün En Düşük Fiyatı

🛒 https://link.amazon/B02W5SjPe

👉 Ürün Linki 👈         ← ürün adresi bu yazıya gömülü (tıklanabilir)

🛍️ Palmolive Moments …  ← kaynak mesaj aynen, ürün satırının altında
…

İndirimde Al            ← kalın; MESAJIN ADRESİ bu ada gömülü (tıklanabilir)
```

Satır sırası sabittir: **üst satır → boşluk → `👉 Ürün Linki 👈` → boşluk → alıntı
mesaj → boşluk → alt not (kaynak grup adı ya da çoklu paylaşım notu).**

Tekrarlı paylaşımda (bkz. 9. bölüm) **hangi gruptan geldiği satırı kaldırılır** ve
yerine çoklu paylaşım notu yazılır: `📌 3 kere paylaşıldı: FırsatZ, İndirimde Al, OnuAl`.
Nottaki **her grup adı kendi mesajına** bağlıdır (virgülle ayrılmış adların her biri
ayrı bir gizli link taşır); böylece fırsatı paylaşan bütün grupların mesajı ada
dokunularak açılır. Yer daraldığında rozet önce süsü atar (`· tek grup`, `— KAÇIRMA!`)
ama **adları korur** — ad giderse adın taşıdığı link de giderdi. Kaynak adı hiç
yazılamıyorsa (yer yetmezse) adres `🔗 Mesajı Gör: <url>` satırı olarak geri gelir:
link hiçbir durumda kaybolmaz.

- **İçeriksiz yanıtlar bildirilmez.** Kaynaklar kendi fırsatına açıklamasız bir
  görsel ya da çıkartmayla yanıt verir; böyle bir ileti yalnızca `Mesajı Gör` satırı
  ve kaynak adından oluşan boş bir bildirim üretirdi. Kural bilerek dardır (gerçek
  fırsat kaçmasın): ileti **başka bir mesaja yanıt** olmalı, temizlenmiş metninde
  **tek bir harf/rakam** ve **hiçbir bağlantı** (gizli hyperlink, buton, önizleme,
  görünür URL) bulunmamalıdır. Metin, fiyat, bağlantı ya da buton taşıyan her yanıt
  ve yanıt olmayan açıklamasız görsel eskisi gibi iletilir. Bunun dışında yalnızca
  metni `Yoğun ilgi görüyor` olan duyuru elenir (emoji, noktalama ve kalın yazı gibi
  biçimler sayılmaz; görsel, bağlantı ya da buton taşıyorsa iletilir). Elenen her
  ileti günlüğe `İçeriksiz mesaj elendi (chat=… id=…)` diye yazılır.
- Ürün başlığı bulunamayan mesajlarda (kupon/duyuru gibi) üst satır yalnızca fiyatı
  (`₺56`) ya da fiyat yoksa yalnızca `❓` taşır; **ikisi de bulunamazsa üst satır hiç
  kurulmaz**, mesaj ve alt satırlar aynen iletilir.
- Üst satıra yazılan fiyat **yalnızca görünen metindir**: Google Alışveriş, Akakçe ve
  Cimri arama sorguları ile tekrar (dedup) anahtarı, kaynak mesajdan ayıklanan
  **fiyatsız** üründen kurulur; tutar, `₺`, ayraç ve `❓` hiçbir arama URL'sine
  sızmaz (`split_header_line` / `header_search_title` / `_price_search_url`).
- Başlık, fiyat veya link kaynak mesajdan **silinmez**: gövde her zaman orijinal
  metnin kendisidir. Fiyat/indirim satırlarının ayıklanması yalnızca başlık
  ayıklayıcısında ve arama düğmelerinin sorgusunda kullanılır.
- `Amazon tanıtım …`, `n11 (Satıcı: …) Tanıtım` ve yalnız kupon/fiyat içeren
  `SOFRAM300 koduyla 1.690 TL` gibi metadata satırları ürün adı seçilmez. Uzun
  katalog adlarında sondaki açık model kodu kısaltma sırasında korunur.
- `43.728 TL sepette`, `Sepette 43.728 TL` ile aynı açık sepet fiyatıdır. Buna karşılık
  `750 TL sepette indirim` bir indirim tutarıdır ve ürün fiyatı olarak gösterilmez.
- Bölümler arasında **tam bir boş satır** olur. Reklam/işbirliği etiketi veya
  WhatsApp bağlantısı silinince geriye kalan çoklu boş satırlar tek boş satıra
  indirilir; bu sıkıştırma yalnızca boşluk karakterlerini alır, içerik kaybolmaz.
- Metinden yalnızca bağımsız `#işbirliği`, `işbirliği`, `#reklam`, `reklam` ifadeleri
  ile doğrudan WhatsApp/Instagram URL'leri çıkarılır. WhatsApp'a gizlenmiş hyperlink'in
  görünen etiketi düz metin olarak korunur; başka kelime ve domain'ler silinmez.
- **Instagram tanıtım ekleri temizlenir:** `Instagram kanalımıza katılın` gibi tanıtım
  satırları ile Instagram'a giden gizli bağlantılar, inline butonlar ve önizlemeler
  bildirime taşınmaz. Bu, Instagram'a özeldir; diğer kanal/mağaza bağlantıları ve aynı
  satırdaki ürün-fiyat içeriği korunur.
- Yeniden oluşturulan metin bildirimlerinde Telegram bağlantı önizlemesi kapalıdır.
  Amazon ürün bağlantısı ve tıklanabilirliği korunur; mesajın altında ayrıca Amazon
  ürün önizleme kartı oluşmaz.
- **Kanal tanıtımı ve hashtag satırları temizlenir:** `💚Whatsapp Önemli Fırsatlar` gibi
  mesajlaşma kanalı tanıtımları ve `#amazon #indirimalarmi` gibi yalnızca etiketlerden
  oluşan satırlar bildirime hiç girmez (kullanıcı isteği: "tertemiz görünüm"). Kurallar
  dar tutulur: satırda hem bir kanal adı (WhatsApp/Telegram/Instagram) geçmeli hem de satırdaki
  **tüm kelimeler** tanıtım sözlüğünden gelmelidir. Sözlükte olmayan tek bir kelime
  satırı korur; bu yüzden `WhatsApp'tan bilgi ... 9 TL` gibi içerik satırları,
  fiyat/ürün satırları ve tüm mesaj silinmesini gerektiren durumlar olduğu gibi kalır.
- Kaynak adı "Fırsatı Gönderen" gibi bir **etiketle yazılmaz** ve **hiçbir linke
  bağlanmaz**; yalnızca hangi gruptan geldiği kalın olarak görünür
  (`source_footer: false` ile tamamen kapatılabilir).
- Arama düğmeleri gövdeye karakter eklemez; ek özellik uğruna mevcut metin kısaltılmaz.

**Adımlar (2 dakika)**

1. Telegram'da **@BotFather** → `/newbot` → görünen ad → `_bot` ile biten kullanıcı adı.
   BotFather sana `7123456789:AAHx...` biçiminde bir token verir (kimseyle paylaşma).
2. Fırsatların düştüğü grubu aç → **Üyeler → Üye ekle** → botun kullanıcı adını yaz ve ekle.
   Yönetici yapmana gerek yok.
3. GitHub deposunda **Settings → Secrets and variables → Actions → New repository
   secret** bölümünden `NOTIFY_BOT_TOKEN` adlı secret oluştur ve token'ı değer olarak
   gir. Yerel çalıştırmada `NOTIFY_BOT_TOKEN` ortam değişkenini kullan. Token'ı
   `config.json`a yazma veya commit etme; kod yalnızca secret/ortam değerini kullanır.
4. Daha önce bir token `config.json` veya Git geçmişine eklendiyse BotFather'da
   `/revoke` ile iptal edip yenisini secret'a koy. Bu değişiklik güncel config'ten eski
   alanı kaldırır; Git geçmişindeki eski blob ayrıca kalabilir. Workflow'u yeniden
   başlatıp gruba `/test` yaz.
   `🔔 Bot bildirimi de gönderildi
   (telefonuna düşmeli).` yazıyorsa tamamdır.

**Sorun giderme**

| `/test` yanıtı | Anlamı | Çözüm |
|---|---|---|
| `HTTP 403: bot is not a member` | Bot gruba eklenmemiş | 2. adımı tekrarla |
| `HTTP 400: chat not found` | `destination` ID'si yanlış | Gruba `/id` yaz, çıkan ID'yi kullan |
| `HTTP 401: Unauthorized` | Token bozuk/yanlış | BotFather'dan `/revoke` ile yenisini al |
| `HTTP 429` | Çok sık mesaj | Bot eşleşme başına 1 mesaj atar; kaynak sayısını azalt |

`NOTIFY_BOT_TOKEN` secret'ı tanımlı değilse takipçi çalışmaya devam eder; yalnızca bildirim
ve arama düğmeleri gönderilmez.

**Tek mesaj modu (`single_message`):** Bildirim botu mesajı gruba attıktan sonra
hesabın attığı kopya gruptan silinir; grupta yalnızca botun mesajı (telefonuna
uyarı düşen mesaj) kalır. Bildirim gönderilemezse kopya **silinmez** — mesajsız
kalmaktansa iki kopya iyidir. Kapatmak için `config.json` → `"single_message": false`.

## Gecikme: hızlı bildirim (`fast_notify`) ve süre kayıtları

- `config.json` içine `"fast_notify": true` eklenirse bildirim botu **önce** gönderir; hesap kopyası yalnızca bot başarısız olursa atılır (silinecek kopya kalmaz). Anahtar yoksa eski sıra aynen sürer.
- Her bildirim için logda tek satır süre özeti çıkar: `⏱ kaynak | telegram→bot X sn | hesap kopyası Y sn | bildirim Z sn | silme W sn | toplam T sn`. Medyalı bildirimlerde ayrıca `⏱ Bildirim medyası: … MB, indirme X sn` ve yükleme süresi yazılır.
- `telegram→bot` yüksekse gecikme Telegram'ın güncellemeyi geç iletmesindendir; `bildirim` yüksekse medya indirme/yüklemedir.
- **Yavaş bildirim raporu:** toplam süre `slow_report_seconds` (varsayılan 8 sn) üstüne çıkarsa bot Kayıtlı Mesajlar'a adım adım süre dökümü yollar (dakikada en fazla 1). `"slow_report_seconds": 0` ile kapanır.

## Hızlı tarama (`fast_poll`) — Telegram'ın geç güncellemesine karşı

Loglar gösterdi ki bazı yoğun kanallarda mesaj, Telegram tarafından hesaba **5–60 sn geç** iletiliyor (`⏱ … telegram→bot` değeri); bizim işleme süremiz ise ~1 sn. Hızlı tarama bunu aşar: tüm kaynaklar birkaç saniyede bir **tek istekle** yoklanır, yeni mesaj bulununca canlı olayla **aynı yoldan** işlenir.

- Varsayılan **açık**. Kapatmak: `"fast_poll": false`. Aralık: `"fast_poll_seconds": 3` (1–30).
- Aynı kaynak/mesaj ID'si canlı olaydan, taramadan veya taramadan tekrar gelirse tek kez işlenir; devam eden gönderim de bu korumaya dahildir.
- `FloodWait` süresi kısaltılmaz. Hata ya da Telethon'un kısa beklemeleri nedeniyle yavaşlayan yoklama sonrası tarama aralığı kademeli artırılır (en fazla 300 sn); canlı dinleme devam eder.
- Geçmiş sorgusu başarısızsa tarama imleci ilerletilmez; bir turda 30'dan fazla yeni mesaj olması kalanları düşürmez.
- **Kopukluk sonrası geriye tarama (restart, yeniden başlatma, duraklama):** bot yalnızca
  **son 15 dakikadaki** mesajları (en fazla 100) toplar; daha eski birikim bildirilmez.
  Önce restart sırasında yedeklenen kaynak imleci (`source_cursors`) kullanılır; imleç pencereden
  yeniyse kaldığı yerden devam edilir, pencereden eskiyse (ör. yedek saatler önce alınmışsa)
  pencerenin başına atlanır. Aynı sınır çalışırken uzun bir duraklamadan (ağ kopması, uzun
  `FloodWait`) sonra da uygulanır. Süre: `"fast_poll_backfill_minutes": 15` (1–120 dk).
- Logda `⚡ Hızlı tarama yeni mesajı yakaladı` satırı, Telegram'ın geciktirdiği ve taramanın kurtardığı mesajları gösterir.


## Bildirim güvenilirliği — 23 Ekim 2026 düzeltmeleri

- **429 medya hatası değildir.** Açık bir `429` reddinde aynı Bot API isteği,
  belirtilen süre beklendikten sonra yeniden denenir; sırf hız sınırı nedeniyle
  medya → metin → yalnız hesap kopyası zincirine düşülmez. Gönderim devam ederken
  başka kaynak aynı ürünün rezervasyonunu devralmaz. Eşzamanlı bir istek bekleme
  süresini uzatırsa yeni süre de dikkate alınır.
- **Hesap tarafındaki FloodWait:** hesap gönderimi de süre kısaltılmadan aynı
  fırsatla yeniden denenir; bekleme sırasında diğer hesap gönderimleri sırada kalır.
- **Hesap kopyası ≠ bot bildirimi.** Bot gönderimi başka bir nedenle başarısızsa
  hesap yedeği kullanılabilir, fakat bu kayıt sonraki kaynaklardan gelecek bot
  bildiriminin önünü kapatmaz. Bağlantı zaman aşımı gibi sonucu belirsiz HTTP
  istekleri retry katmanında otomatik tekrarlanmaz. Mevcut yedek zinciri çalışabilir;
  Telegram ilk isteği kabul etmişse çift ileti olasılığı tamamen ortadan kalkmaz.
- **Güvenilir ürün kimliği:** `3 adet alımda`, `30g medyan`, stok/durum ve
  “bildirimlerinizi açın” gibi genel satırlar birleştirme anahtarı olamaz.
  Uzun başlığın model/hacim son eki dedup için kesilmez. Apostrof/aksan farkları
  normalize edilir; belirsiz varyantlar yaklaşık benzerlikle birleştirilmez.
  Ondalık sayılar bütün tutulur: `6,2 L` ile `2,6 L`, `S8` ile `S8+` farklıdır.
- **Fiyat ve koşullar:** güvenilir şekilde tespit edilen fiyat düşerse veya
  tanınan alım/kupon koşulu değişirse yeni bildirim gider. Bu metinsel bir
  ayrıştırıcıdır; mağazada kupon uygulanmasını doğrulamaz veya bilinmeyen
  paket/birim fiyatlarını birbirine çevirmeye çalışmaz. Farklı alım koşulları ayrı
  kayıtlarda tutulur; A/B/A sırasıyla gelen teklifler gereksiz yeni bildirim üretmez.
  Güvenli geçmiş yüklemesinde daha düşük fiyatlı bildirim tercih edilir.
- **Kayıp hedef mesaj:** `message to edit not found` durumunda kısa rozetle
  tekrar denemek yerine mevcut fırsat için yeni bildirim gönderilir.
  `message is not modified` başarıdır, hata olarak loglanmaz.
- **Harici filtre:** `L'Oréal`, `L’Oreal`, `Loreal` yazımları aynı engel ifadesiyle
  yakalanır. Bir mesajda “kedi maması” ifadesi geçmiyorsa bu ifade tek başına tüm
  kedi ürünlerini anlamsal olarak engellemez.

**Yeniden başlatma sınırı:** önceki workflow'da `cancel-in-progress: true` olduğu
için zamanlanmış yeni çalışma da çalışan süreci kesebiliyordu. Artık cron mevcut
çalışmayı iptal etmez; sıraya girer. Yalnızca açık `/restart` / manuel
`workflow_dispatch` yeni çalışmayla mevcut süreci değiştirebilir. Tek concurrency
grubu korunur, iki takipçi aynı anda açılmaz. Logdaki “canceled” tek başına
uygulama çökmesi değildir.
Bekleyen gönderimler ve olay kimlikleri bellektedir; ani Actions iptali sırasında
kalıcı kuyruk veya otomatik geçmiş telafisi garantisi yoktur. Bu değişiklikler
çalışma devam ederkenki hataları düzeltir; kesintisiz teslimat garantisi vermez.
`Got difference` senkronizasyonu ve medya için DC değişimi de tek başına kayıp
bildirim kanıtı değildir.

**Özel medya yedeği:** botun kendi gönderdiği medyanın Bot API ID'siyle özel kopya
alınmaya devam edilir. Ancak normal grupta yalnızca kullanıcı hesabı medyayı
kaydetmişse hesabın mesaj ID'si `copyMessage` için kullanılmaz; başlık/açıklama ve
bağlantılarla metin bildirimi gönderilir. Bu yedekte medyanın kendisi DM'ye taşınmaz.
Hesap kopyası silinemediğinde de silinmiş gibi işaretlenmez.

Regresyonları çalıştırmak için: `python -m unittest discover -s tests -v`.
Testler gerçek Telegram gönderimi yapmadan HTTP 429, OnuAl medya bildirimi,
geçmiş ID alanları, fiyat düşüşü, eşzamanlı olaylar ve iptal senaryolarını sınar.
