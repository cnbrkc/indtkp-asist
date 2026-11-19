# Özel sohbetten yönetim ve kişisel fırsatlar (DM)

## Yeni: önce özel sohbetten yönetim, sonra isteğe bağlı kişisel fırsatlar

Bu depoda `private_control: true`: **komutları gruba değil, mevcut bildirim botunun
özel sohbetine yaz.** Fırsatların hedefi (`destination`), kaynaklar, grup filtreleri,
iletim zinciri ve tekrar birleştirme ayarları değişmedi. `control_chat` eski değeriyle
korunur ama bu modda gruptan komut işlenmez. **Kayıtlı Mesajlar** yedek kontrol yoludur.
Eski kurulumlarda yeni alan yoksa önceki grup kontrol davranışı korunur.

### 1. aşama — yalnızca yönetimi taşı

1. Bu değişikliği birleştir ve takipçiyi yeni sürümle yeniden başlat. Aynı bot token'ını
   kullanan eski çalışmayı kapat; tek bir `getUpdates` tüketicisi çalışmalı.
2. Mevcut `NOTIFY_BOT_TOKEN` secret'ının tanımlı olduğundan emin ol; yeni bot veya yeni
   token gerekmez. Botun gruptaki üyeliğini/yetkilerini değiştirme.
3. Botun özel sohbetini aç. Takipçi hazır olduktan sonra `/start`, ardından `/durum` gönder.
4. `/ayar`, `/ekle`, `/cikar`, `/kaydet`, `/iptal`, `/tgfiltreac`, `/tgfiltrekapat`, `/kaynaklar`,
   `/kaynaktest`, `/analiz`, `/restart` aynı işlevleri özel sohbetten yerine getirir.
   `/test` denemeyi hâlâ **hedef gruba** gönderir; sonucu özelden yanıtlar.
5. Botla özel sohbet geçmişi silinmez. Eski `clean_commands` davranışı yalnızca
   kullanıcı hesabının kontrol sohbetlerinde (ör. Kayıtlı Mesajlar) geçerlidir.

Komutları sadece hesap sahibi ve `admin_user_id` listesindeki kişiler kullanabilir.
Bot, yetkisiz kişilerin mesajlarını ve gruptan gelen Bot API mesajlarını yok sayar.
Bot API long polling ayrı bir arka plan görevidir; kaynak kanallar hâlâ aynı Telethon
bağlantısıyla dinlenir. Açılıştan önce bekleyen bot komutları güvenlik için atlanır:
cevap gelmediyse bot hazır olduktan sonra tekrar `/start` gönder.

### 2. aşama — kişisel fırsatları istediğin zaman aç

Yeni kurulumlarda varsayılan `dm_enabled: false`, `dm_keywords: []`,
`dm_exclude_keywords: []` ve `dm_product_price_rules: []` olur. **Mevcut DM ayarları
korunur; yeni alanı olmayan eski config dosyaları da çalışmaya devam eder.** Hesap sahibi
botun özel sohbetinde, diğer listelerle aynı taslak/kaydet akışını kullanır:

```text
Sen: /ekle
Bot: 1. Dahili · 2. Harici · 3. Grup · 4. dmdahil · 5. dmharic · 6. dmfiyat · 7. webdetakip · 8. ürün adı < fiyat
Sen: 8
Bot: Ürün başlığındaki ifade < maksimum gerçek ürün fiyatı biçiminde kural gönder.
Sen: telefon < 100; TCL < 30000; LG 75 < 45000
Bot: Değişiklik taslağı... /kaydet veya /iptal
Sen: /kaydet
Bot: Kaydedildi ve çalışan ayarlara uygulandı.

Sen: /ekle
Sen: webdetakip
Bot: Akakçe ürün detay linkini gönder (…akakce.com/…-fiyati,ID.html).
Sen: https://www.akakce.com/…-fiyati,123456789.html
Bot: …Hangi fiyatın altına düşerse haber vereyim? Örnek: 6500…
Sen: 6500
Bot: ✅ Kalıcı kaydedildi. (product_watches.json; ayrı /kaydet adımı yok)
```

Kural silmek için `/cikar` → `ürün adı < fiyat` → kayıt numarası veya tam kural →
`/kaydet`. Mevcut menü numaraları korunmuştur: `4` dmdahil, `5` dmharic, `6` dmfiyat,
`7` webdetakip; yeni ürün adı/fiyat grubu `8`'dir. `7` (webdetakip) seçimi `/webtakip`
ile aynı akışı başlatır (ekle: link → hedef fiyat; çıkar: /webtakipler numarasıyla silme).
Ürün adı/fiyat grubunda birden çok kuralı satır veya noktalı virgülle ayırabilirsin;
ondalık virgüllü eşikler (örn. `telefon < 1.299,90 TL`) korunur. Kurallar Türkçe/büyük-küçük
harf ve boşluk normalizasyonuyla tekilleştirilir; aynı ürün ifadesi ve aynı eşik ikinci kez
eklenmez. Her kural numarasıyla veya tam ifadeyle ayrı silinir. Bu grupta en fazla 100 kural vardır.

Değişiklik **`/kaydet` öncesinde aktif değildir**; `/iptal` taslağı siler. Yeni bir
`dmdahil` kaydı kaydedilince dmdahil açılır; son dahil kaydı çıkarılırsa kapanır.
`/dmfiltreac` ve `/dmfiltrekapat` yalnız dmdahil grubunu açıp kapatır; `dmfiyat` ve
ürün adı/fiyat grubu bağımsızdır. `dmharic` listesi boş değilse dmdahil ile dmfiyat
fırsatlarını ortak biçimde engeller; yeni ürün adı/fiyat kurallarını etkilemez.
`/dmfiltre` dört grubu ayrı ayrı açık/kapalı durum, kayıt sayısı, kayıtlar ve fiyat
eşikleriyle gösterir; sayaçlar bu oturum içindir.

- **dmdahil:** `dm_enabled` açıksa, dahil ifadelerinden biri mesajda bitişik blok olarak
  bulununca DM gönderir; fiyat eşiği aranmaz. Mevcut geriye uyumlu davranış gereği eşleşme
  ürün başlığıyla sınırlı değil, tüm temizlenmiş mesaj metnindedir. Bu nedenle kısa ifadeler
  veya kupon/açıklama satırları geniş eşleşebilir; başlık VE fiyat koşulu istiyorsan aşağıdaki
  bağımsız grubu kullan.
- **dmfiyat:** Ürün adından bağımsız tek maksimum gerçek ürün fiyatıdır; eşik ve altı eşleşir.
  Fiyatı `price_filter` belirler; kupon/indirim, eski veya üstü çizili tutar, taksit, kargo ve
  alışveriş koşulu fiyat sayılmaz. `dmharic` bu grubu da engeller.
- **dmharic:** Doluysa dmdahil VE dmfiyat için ortak engeldir; sadece özel kopyayı etkiler.
- **ürün adı < fiyat:** Her kuralda ürün başlığındaki ifade VE gerçek ürün fiyatı birlikte
  eşleşmelidir. Örn. `telefon < 100`, `TCL < 30000`, `LG 75 < 45000`. Herhangi bir kayıt
  eşleşince DM gönderir; dmdahil, dmfiyat ve dmharic ayarlarından bağımsızdır.

Ayarlar `config.json`'a ve mevcut GitHub kayıt yöntemiyle depoya yazılır; gönderilen
kayıt raporunu kontrol et. Yerel dosyaya yazma başarısızsa eski ayar geri yüklenir ve taslak
tekrar denenmek üzere korunur. Çakışan taslak güncel config'i ezmez. GitHub'a gönderim
başarısızsa yereldeki ayar geçerlidir ama yeni Actions runner'ına aktarılmayabilir.
**Özel kelimeler ve ürün/fiyat kuralları gizli değildir; config ve Git geçmişinde görünür.**

| Komut | Etki |
|---|---|
| `/dmfiltre` | Dört grubun açık/kapalı durumu, listeleri/eşikleri ve bu oturumun gönderim/hata/kuyruk sayaçları; salt okunur |
| `/ekle` → `dmdahil` / `dmharic` / `dmfiyat` / `ürün adı < fiyat` | Ekleme taslağı; `/kaydet` ile uygulanır |
| `/cikar` → aynı filtreler | Numara/tam kural ile çıkarma taslağı; `/kaydet` ile uygulanır |
| `/dmfiltrekapat` | Yalnız dmdahil'i kapatır; bağımsız dmfiyat ve ürün adı/fiyat kuralları sürer |
| `/dmfiltreac` | Kayıtlı dmdahil listesini tekrar açar; boş listeyi açmaz |
| `/komutlar` | Her desteklenen komutu ve açıklamasını tek kez listeler |

`/start` yanıtında `/komutlar` hatırlatılır. `/komutlar` yedi başlıkla kategorik
olarak üretilir: Temel komutlar, Liste yönetimi, Telegram indirim filtreleri,
Kişisel bildirim filtreleri (DM), Web ürün takibi, Gelişmiş araçlar ve Yönetim.

Bu kişisel ayarlar ve alıcı **yalnızca hesabın sahibine** aittir; başka bir admin
kişisel alıcıyı kendisine çeviremez. Çok kullanıcılı abonelik sistemi değildir.

- **DM dahil** listesinden herhangi bir ifade metinde **blok halinde** geçmelidir.
  `tcl 65`, `TCL 65T6D` içinde eşleşir; `TCL televizyon 65` veya `65 TCL` eşleşmez.
  Kelimeler ayrı ayrı aranmaz; kelime sınırı şartı yoktur. Bu yüzden kısa filtreler
  geniş eşleşebilir (`lg`, `bilgi` içinde de geçer); gerekirse `lg 65` gibi ifade kullan.
  Büyük/küçük harf, Unicode genişlik ve fazla boşluk farkları yok sayılır;
  `IPHONE`/`İPHONE` eşleşir. Her ifade en fazla 100 karakterdir.
- **DM hariç (`dmharic`)** listesindeki bir ifade aynı blok eşleşmesiyle geçerse
  `dmdahil` ve `dmfiyat` sonuçlarını engeller; grup mesajı bundan etkilenmez. Yeni
  ürün adı/fiyat kuralları bu engelden bağımsızdır. Hariç listesi boşsa ek engel yoktur.
  Kuyrukta bekleyen kopyalar gönderilmeden önce güncel ayarlarla tekrar kontrol edilir.
- **DM maksimum fiyat (`dmfiyat`)** ürün adından bağımsızdır; `dmharic` ile engellenebilir.
  **Ürün adı < fiyat** kayıtlarında ise ürün başlığı ve gerçek fiyat ikisi birden eşleşir;
  bu grup dmdahil/dmfiyat/dmharic ayarlarından bağımsızdır.
- Önce mevcut grup filtresi uygulanır. Hariç tutulan, gruba gönderilemeyen veya
  mevcut tekrar birleştirmesiyle yeni grup mesajı oluşturulmayan fırsata özel kopya
  gönderilmez. Bu aşama **gruba yeni gönderilen fırsatların aynasıdır**; geçmiş tarama,
  fiyat düşüşü analizi veya gruptaki rozet düzenlemelerini DM'e eşitleme yapmaz.
- Metin ve biçimlendirme entity'leri, gizli linkler, URL düğmeleri korunur. Medya
  gruptaki iletiden Telegram'ın `copyMessage` yöntemiyle kopyalanır; tekrar indirilmez.
  Sonuna yalnızca **`🎯 SANA ÖZEL`** eklenir. Bu sabit metin iPhone Kestirmeler filtresi
  için kullanılabilir; telefon otomasyonu bu PR'ın kapsamında değildir.
- Gruba gönderim/hesap kopyası temizliği tamamlandıktan sonra özel kopya ayrı,
  en fazla 100 kayıtlık kuyruğa alınır. İşçi en fazla saniyede bir gönderim yapar;
  **bu, tekrarlı alarm değildir: bir yeni fırsat = bir özel kopya.**
- Bot engellenirse, özel sohbet başlatılmamışsa veya kopyalama başarısızsa grup
  etkilenmez. Hata `/dmfiltre` sayacına ve loga yazılır. 429 yanıtlarında sınırlı bekleme
  uygulanır. Kuyruk dolarsa özel kopya atlanır ve sayılır; sınırsız bellek büyümez.
- Grup bot mesajı başarısızken kullanıcı hesabının grup kopyası kalmışsa o ileti
  okunarak özel kopya denenir. Telegram'ın kopyalayamadığı içerikler veya ek etiketle
  uzunluk sınırını aşan yedek iletiler **sessizce kesilmez**, özel gönderim hatası sayılır.
- Kuyruk ve sayaçlar bellektedir: yeniden başlatmada henüz gönderilmemiş özel
  kopyalar kaybolabilir. Yapılandırma kalıcıdır. Garantili teslim/kalıcı outbox ve
  tekrarlı alarm bu aşamada eklenmedi.

### Sorun giderme ve geri dönüş

**Açılış ve otomatik oturum yenileme mesajları artık gönderilmez** (gruba da DM'e de).
Eski `notify_on_start: true` ayarı da rutin açılış mesajını geri açmaz. Dinlemeyi kontrol
etmek için botun özel sohbetinde `/durum` yaz: bağlantı/dinleme durumu, kaynak sayısı,
çalışma süresi ve sayaçlar görünür; gruba deneme mesajı atılmaz. Bağlantı yoksa bunu
belirtir. Bot tamamen kapalıysa komuta yanıt veremez; Actions/servis logunu kontrol et.

Gerçek özel komut arızaları (webhook / 401 / kalıcı 409) ve arıza sonrası toparlanma
uyarıları korunur; bunlar rutin yeniden başlatma bildirimi değildir. Ayrıntılı özel
komut teşhisi için `/test` kullanılabilir, ancak **bu komut hedef gruba deneme mesajı
atar** ve alarma neden olabilir; sessiz kontrol için `/durum` tercih et.

Takipçi hazır olmadan önce yazdığın komut işlenmez (eski bir `/restart`'ın tekrar
oynamaması için) ama artık sessiz kalmaz: son 15 dakika içinde yazılmışsa bot
`⏳ ... komutu tekrar gönder` diye uyarır.

Botun özel sohbetinde `/start` yanıtlanmıyorsa, sırayla şunları kontrol et (token'ı
asla sohbete, config'e veya loglara yazma):

1. **Secret eksik veya yanlış:** GitHub → Actions → son çalıştırmanın **"Ayarları
   doğrula"** adımındaki raporda `NOTIFY_BOT_TOKEN=YOK` yazıyorsa secret tanımlı değil.
   `Settings → Secrets and variables → Actions` üzerinden ekle/güncelle ve workflow'u
   yeniden başlat. **Bu, en sık görülen nedendir.**
2. **Botta webhook var:** Logda `Özel komutlar başlatılamadı: bu botta webhook var`
   yazıyorsa botta webhook var; `getUpdates` ile çelişir ve özel komutlar hiç
   başlamaz. Webhook'u kaldır (`https://api.telegram.org/bot<TOKEN>/deleteWebhook`)
   veya webhook kullanan entegrasyonu kapat; tek alıcı olsun. Uygulama webhook'u
   kendisi silmez.
3. **İkinci bir tüketici var:** Logda `hata 409` (çakışma) yazıyorsa aynı token'la
   başka bir `getUpdates` uygulaması (eski runner, yerel servis) çalışıyor; mesajlar
   iki tüketici arasında bölüşülür. Tek örneğe indir.
4. **Token geçersiz:** Logda `hata 401` / `token geçersiz` yazıyorsa token bozuk veya
   yanlış botun token'ı; BotFather'da `/revoke` ile yenisini alıp secret'a koy.
5. **Yanlış bot:** Kayıtlı Mesajlar'dan `/test` yaz; yanıttaki `🔎 Özel komut kanalı`
   bölümünde botun kullanıcı adı (`@...`) görünür. Özel sohbette konuştuğun bot
   **aynı** olmalı.
6. **Grupta yazıyorsan:** Bu kanal grup mesajlarını tasarım gereği yok sayar (yalnızca
   özel sohbet kabul eder). BotFather'daki gizlilik (privacy) modu grupta geçerlidir;
   özel sohbette gerekmez. Komutları botun **özel sohbetine** yaz.
7. **Bot ilk mesajı atamaz:** Telegram kuralı gereği bot sana ilk özel mesajı kendisi
   atamaz. Botun özel sohbetini aç, **/start**'a bas ve botu engellemediğinden emin ol.
   Bot engellenmişse veya hiç başlatılmamışsa ne komut yanıtı ne de kişisel fırsat
   kopyası düşer.
8. **Bekleyen mesajlar atlanır:** Takipçi kapalıyken gönderdiğin komutlar açılışta
   güvenlik için atılır; takipçi hazır olduktan sonra komutu **yeniden** gönder.

BotFather'dan yapman gerekenler: yeni bot açmana gerek yok (mevcut bildirim botu yeterli);
botun kullanıcı adını yukarıdaki `/test` çıktısından doğrula. Eski grup komut düzenine
dönmek için `private_control: false` yapıp yeniden başlat; korunan `control_chat` yeniden
geçerli olur. Özel fırsatları da kapatmak istersen `dm_enabled: false` yap. `destination`
veya grup filtrelerini değiştirme.

## DM fırsat filtreleri ve yeni bildirim düzeni

Kişisel DM kararı dört ayrı grup olarak değerlendirilir:

| Grup | Eşleşme koşulu | dmharic etkisi |
|---|---|---|
| `dmdahil` | `dm_enabled` açık + dahil ifadelerinden biri mesajın herhangi bir yerinde bitişik blok olarak geçer; fiyat sınırı yoktur | Engeller |
| `dmfiyat` | `dm_price_max` içindeki tek eşik ve altındaki gerçek ürün fiyatı; ürün adı aranmaz | Engeller |
| `dmharic` | Dolu liste, iki eski grupta ortak engel olur | Yalnız dmdahil/dmfiyat'ı durdurur |
| `ürün adı < fiyat` | Her kural için ürün başlığında ifade VE güvenilir gerçek fiyat ≤ eşik | Etkilemez |

- `dmdahil` mevcut davranışla geriye uyum için bütün temizlenmiş mesaj metnini tarar; bu nedenle
  çok kısa ifadeler (`lg` gibi) açıklama/kupon satırında da eşleşebilir. Ürün başlığıyla sınırlı
  bir koşul için yeni grubu kullanın.
- `dmfiyat` için `/ekle` → `dmfiyat` → tutar (örn. `10000`) → `/kaydet`; eşik ve altı gönderilir.
  Kapatmak için `/cikar` → `dmfiyat` → `1` → `/kaydet`. Bu kural dmdahil'in açık/kapalı
  durumundan bağımsızdır, ancak `dmharic` tarafından engellenir.
- Yeni grup için `/ekle` → `ürün adı < fiyat` (veya menü `8`) → `telefon < 100` → `/kaydet`.
  Örnekler: `TCL < 30000`, `LG 75 < 45000`. Birden çok kural eklenebilir; tekrar ekleme
  normalizasyonla tekilleştirilir, `/cikar` ile kayıt numarası/tam kural üzerinden ayrı silinir.
- Bu iki fiyat grubunda fiyat `price_filter` ile bir kez ayrıştırılır. Kupon/indirim tutarı,
  eski ya da üstü çizili fiyat, taksit, kargo ve alışveriş koşulu gerçek ürün fiyatı sayılmaz.
  Güvenilir ürün fiyatı veya başlık yoksa yeni kural eşleşmez.
- Yeni config anahtarı `dm_product_price_rules` (string listesi); eksik eski config alanı veya `[]`
  boş liste kabul edilir. Bozuk ya da yinelenen kurallar başlangıç doğrulamasında reddedilir;
  `/ekle` akışı yinelenen girdileri ikinci kez kaydetmez. `dm_price_max` en çok bir değerli eski biçimini korur.

**Yeni bildirim düzeni (`smart_header`)** — `config.json` içine `"smart_header": true` eklenince açılır; anahtar yoksa eski düzen aynen sürer.

**Yeni bildirim düzeni (`smart_header`)** — `config.json` içine `"smart_header": true` eklenince açılır; anahtar yoksa eski düzen aynen sürer.

```
₺56 — Ürün başlığı        ← kalın, TEK satır: fiyat önce, " — " ayracı, sonra ad
❓ — Ürün başlığı          ← fiyat güvenle bulunamadığında
₺1.299 — Ürün başlığı     ← kuruş atılır (1.299,90 → ₺1.299), yuvarlanmaz

> orijinal mesaj (alıntı bloğu)

Grup adı / 📌 N kere paylaşıldı: …   ← ad(lar) mesajın adresine gömülü
```

- Fiyat yazısı **yalnızca görüntüleme için** `header_price_text` ile üretilir:
  tutarın kuruşu yuvarlanmadan atılır ve `₺` tutarın önüne yazılır. Gövde, DM eşiği,
  `offer_terms` ve fiyat karşılaştırmaları gerçek (kuruşlu) değerle ve "TL"
  yazımıyla çalışmaya devam eder (`price_filter.format_price` değişmedi).
  Low/none güvenli tutar üst satıra **taşınmaz**; o durumda satır `❓` ile açılır
  (kupon/kampanya duyurusunda hiç açılmaz — bkz. 13. madde).
- Fiyatı üst satıra taşımak aramaları **bozmaz**: `₺56 — ` / `❓ — ` ön eki yalnızca
  görüntüdür. Google Alışveriş / Akakçe / Cimri sorguları ve tekrar (dedup) anahtarı,
  kaynak mesajdan ayıklanan fiyatsız adla kurulur; gönderilmiş bildirim okunurken de
  üst satır `split_header_line` ile fiyat ve ad diye ayrılır, aramaya ad gider.
- Üst satır sığmazsa (medya açıklaması gibi dar kalıplar) fiyat korunur, ürün adının
  kuyruğu `…` ile kırpılır. Bu sığdırma ve gövde kırpması UTF-16 birimiyle ölçer;
  emoji/süslü font ağırlıklı iletilerde gönderilen metin 4096/1024 sınırını aşmaz.
- Üst satır salt görünümdür: ekrandaki `₺12.299` yazısı gösterim için
  kuruşsuzdur; karşılaştırma, arama, dedup ve DM eşiği mesajdan çıkarılan gerçek
  sayıyı (örn. `12299.9`) kullanır; `format_price`ın `TL` yazısı diğer yollarda
  aynen durur.
- **Geçmiş biçimler (yalnız okuma):** bir önceki sürüm `Ürün başlığı` + alt satırda
  `💰 1.299 TL`, en eski sabit düzen gövdede `💰Fiyat: …` üretiyordu. Bunlar yeni
  çıktının parçası değildir; açılış taraması bu iletileri de aynı fırsat anahtarına
  bağlar, yeni bildirim ise daima `56₺ - Ad` biçiminde kurulur.

Fiyat ve başlık tespiti `price_filter.py` içindedir: kupon, indirim tutarı, taksit, kargo, "… TL ve üzeri", üstü çizili ve "eski/önceki/normal fiyat" tutarları fiyat sayılmaz; "X TL'den Y TL'ye düştü" için Y alınır.
