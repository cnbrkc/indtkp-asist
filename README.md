# Telegram indirim takipçisi

Kişisel Telegram hesabının üye olduğu kanalları dinler, kelime filtresinden geçen mesajları başka bir sohbete gönderir. Mesaj dinleme başladıktan sonra polling yapmaz; Telegram bağlantısını açık tutar ve yeni mesajı geldiği anda işler.

## Gerçek çalışma süresi

- `bot.py` çalıştığı sürece mesajlar anlık olarak dinlenir; 1 dakika bekleyip tarama yapmaz.
- GitHub Actions ise kalıcı sunucu değildir. Workflow yaklaşık 5 saat 50 dakika çalışır, sonra yeniden başlatılır. Yeniden başlatma arasında boşluk olabilir.
- GitHub'ın scheduled workflow için resmi en kısa aralığı 5 dakikadır; ayrıca zamanlama yoğunlukta gecikebilir [GitHub Docs](https://docs.github.com/actions/using-workflows/workflow-syntax-for-github-actions). Bu nedenle her dakikada garanti tarama GitHub Actions ile yapılamaz.
- Gerçekten 7/24 ve 1 dakikadan kısa tepki isteniyorsa `bot.py`yi ücretsiz bir Oracle Cloud Free Tier VM üzerinde sürekli çalıştırmak daha doğru çözümdür. GitHub Actions yedeği olarak bırakılabilir.

GitHub Actions kurulumu ücretsiz ve kolay başlangıç içindir; kritik indirim kaçırmama hedefinde VM yolunu tercih etmek gerekir.

## En kolay kurulum

### 1. Telegram API bilgisi

[my.telegram.org](https://my.telegram.org) üzerinden `API_ID` ve `API_HASH` al. Kaynak kanallara kişisel hesabınla katıl.

### 2. Telefonda session üret

Bilgisayar şart değil. Depodaki `session_generator_colab.ipynb` dosyasını GitHub'da açıp **Open in Colab** seç veya dosyayı Google Colab'a yükle. Notebook'u public paylaşma.

Tek hücreyi çalıştır:

1. API ID gir.
2. API Hash gir.
3. Telefon numarasını uluslararası formatta gir (`+90...`).
4. Telegram'a gelen kodu gir.
5. İki aşamalı doğrulama varsa parolayı gir.
6. Çıkan `SESSION_STRING` değerini kopyala.

Bu, Telegram hesabının giriş anahtarıdır; kimseyle paylaşma ve notebook çıktısını kaydetme.

Alternatif olarak Python olan bir ortamda:

```bash
pip install -r requirements.txt
API_ID=... API_HASH=... python generate_session.py
```

### 3. GitHub Secret'larını ekle

Repository → **Settings → Secrets and variables → Actions → New repository secret**:

```text
API_ID
API_HASH
SESSION_STRING
```

**Üçü de dolu olmalı.** Biri eksikse workflow açılışta durur; workflow'daki
**Ayarları doğrula** adımı hangi secret'ın boş olduğunu log'a Türkçe olarak yazar.
Aynı kontrolü yerelde `python bot.py --check` ile de çalıştırabilirsin.

Kesintisiz Actions zinciri istiyorsan ayrıca bir GitHub fine-grained token oluşturup yalnızca bu repository için **Actions: Read and write** izni vererek şunu ekle:

```text
GH_PAT
```

Bu token, job bitmeden yaklaşık 30 dakika önce yeni workflow çalışmasını başlatır. Böylece Actions 6 saatlik pencereyle sınırlı kalsa da yeni job otomatik devralır. Token eklemezsen sistem normal manuel/scheduled moda devam eder. Artık `Variables` eklemek gerekmiyor.

### 4. Tek ayar dosyası

Kanal, hedef ve kelime ayarları kökteki [`config.json`](config.json) dosyasındadır:

```json
{
  "source_chats": ["@indirimkanali", "-1001234567890"],
  "destination": "me",
  "include_keywords": ["çay", "kahve", "şeker"],
  "exclude_keywords": ["çekiliş", "hediye"],
  "match_mode": "any",
  "copy_mode": "forward",
  "control_chat": "me",
  "admin_user_id": null,
  "auto_restart": true,
  "notify_on_start": true
}
```

- `source_chats`: Virgül yerine JSON listesi kullanılır. Kanal adı veya `-100...` chat ID olabilir.
- `destination`: `me` = Kayıtlı Mesajlar; özel sohbet/grup ID'si de kullanılabilir.
- `include_keywords`: Bunlardan biri eşleşsin (`any`) veya hepsi eşleşsin (`all`).
- `exclude_keywords`: Bunlardan biri varsa mesaj gönderilmez.
- `copy_mode`: `forward` kaynak bilgisini korur, `copy` kaynak etiketini kaldırır. (Eski alan; `delivery_modes` varsa o geçerli.)
- `delivery_modes`: Korumalı kanallar için denenecek iletim yollarının sırası (bkz. **M** bölümü). Varsayılan: `["forward", "copy", "media", "text", "link"]`.
- `max_media_mb`: `media` yolunda indirilecek en büyük medya (varsayılan 25).
- `link_appendix`: Bağlantı ekleri. `"smart"` (varsayılan): gizli hyperlink'ler mesajda **tıklanabilir** kalır (tekrar yazılmaz), yalnızca başka türlü taşınamayan bağlantılar (inline buton, link önizlemesi) metne yazılır. `"all"`: tüm gizli bağlantılar `🔗 ...` satırı olarak da yazılır. `"off"`: hiçbiri (bkz. **O** bölümü).
- `message_link`: Her iletinin sonuna `🔗 Mesajı Gör: <t.me mesaj linki>` satırını ekler. Varsayılan `true`; ürün linki kaçarsa bu satırdan mesaja, mesajdan fırsata gidilir.
- `source_footer`: Bildirime en alta `Fırsatı Gönderen: <kaynak>` satırını ekler; kaynak adı **orijinal mesajın t.me bağlantısını gizler**. Varsayılan `true`.
- `notify_media`: Bildirim botu medyayı (fotoğraf/video) da göndersin. Varsayılan `true`; `false` yaparsan bildirim yalnızca metin olur.
- `control_chat`: Komutların (`/status`, `/restart`...) dinleneceği sohbet. `"me"` veya grup ID'si.
- `admin_user_id`: `control_chat` bir grup ise **zorunlu**; komutları yalnızca bu ID kullanabilir.
- `notify_on_start`: Her açılışta hedefe "takipçi başladı" mesajı gönderir.
- `notify_bot_token`: **Bildirim almak için gerekli.** @BotFather bot token'ı; boş/`null` ise bildirim gelmez (bkz. **L** bölümü).

> **ID'ler tırnak içinde de yazılabilir** (`"-5092968106"`); bot bunları sayıya çevirir.
> Çevirmese Telethon string'i *kullanıcı adı* sanıp `Cannot find any entity` hatası verir.

GitHub web arayüzünde `config.json` dosyasını düzenleyip commit etmen yeterlidir. Secret'ları tekrar girmen gerekmez.

### 5. Başlat ve komutlar

**Actions → Telegram indirim takipçisi → Run workflow** seç. İlk çalıştırmada log'larda hesabın bağlandığını görmelisin.

Varsayılan olarak kendi **Kayıtlı Mesajlar** sohbetine şunları yazabilirsin:

```text
/status   (veya /durum)
/test     (veya /deneme)
/restart  (veya /yenile)
/source   (veya /kaynak)
/help     (veya /yardim)
```

`/restart`, `GH_PAT` eklenmişse yeni Actions çalışmasını hemen başlatır. `control_chat` değerini grup ID'si yapıp `admin_user_id` değerine kendi Telegram kullanıcı ID'ni yazarsan aynı komutları sadece o grupta ve sadece sen çalıştırabilirsin. Herkese açık gruplarda `admin_user_id` ayarlamadan kullanma.

> **Bildirim notu (önemli):** Takipçi **kendi Telegram hesabınla** gönderir. Telegram kendi
> gönderdiğin mesajlar için bildirim üretmez — bu yüzden ne Kayıtlı Mesajlar'da ne de grupta
> sesli bildirim alırsın. Bildirim istiyorsan `notify_bot_token` ayarını kur (aşağıda anlatılıyor).

`GH_PAT` varsa takipçi, süre dolmadan yaklaşık 30 dakika önce hedef sohbete yenilenme mesajı yollar ve yeni Actions job'unu kendi başına başlatır. GitHub Actions concurrency ayarı eski job'u kapatıp yenisini devralır. Bu, Actions'ı kalıcı servis gibi zincirler; yine de GitHub yoğunluğu, token iptali veya hesap limitleri nedeniyle mutlak 7/24 garantisi değildir.

## 1 dakikalık sürekli çalışma için Oracle VM

GitHub Actions'ı tamamen ücretsiz ve sürekli sunucuya çevirmek mümkün değil. Daha sağlam ücretsiz plan:

1. Oracle Cloud Free Tier hesabı oluştur.
2. Ubuntu ARM/AMD Free Tier VM kur.
3. Repo'yu VM'ye clone et.
4. Secret değerlerini VM'de environment veya `.env` olarak tanımla; `.env`yi GitHub'a gönderme.
5. `python bot.py`yi `systemd` servisi olarak çalıştır.
6. VM yeniden başlarsa servis otomatik kalkar.

Bu modelde `bot.py` Telegram'a sürekli bağlı kalır ve yeni mesajları anlık işler. Oracle hesap/kapasite uygunluğu bölgeye göre değişebilir; ücretsiz kota garanti edilemez. VM kurulumu için ayrıca `deploy/systemd.service` eklenebilir.

## Güvenlik

- `SESSION_STRING`, API Hash ve telefon kodunu kimseye gönderme.
- Eski sürümde API bilgileri açıkça yazılmıştı. GitHub'a push edildiyse [my.telegram.org](https://my.telegram.org) üzerinden API uygulamasını yenile.
- Session geçersiz olursa Colab notebook ile yeni session üretip `SESSION_STRING` Secret'ını güncelle.
- Kaynak kanalların kurallarına uy; yüksek hacimli otomatik forward işlemleri Telegram rate-limit uygulamasına neden olabilir.

## Baştan sona kurulum kontrol listesi

Aşağıdaki sıra, bu projeyi hiç kurmamış biri için önerilen sıradır.

### A. PR'ı ana branch'e al

1. Bu PR'ı GitHub'da aç.
2. Dosyaların değişikliklerini incele.
3. **Merge pull request** ile `main` branch'e birleştir.
4. GitHub Actions schedule yalnızca varsayılan branch'teki workflow dosyasını çalıştırdığı için bu adım önemlidir.
5. Birleştirmeden sonra repository'de **Actions** sekmesinin açık olduğundan emin ol.

### B. Telegram API ID ve API Hash al

1. `https://my.telegram.org` adresine gir.
2. Telegram telefon numaranla giriş yap.
3. **API development tools** bölümünü aç.
4. Yeni bir uygulama oluştur.
5. Ekrandaki `api_id` sayısını ve `api_hash` değerini not al.
6. API Hash'i Telegram mesajı, GitHub issue'su veya herkese açık dosyada paylaşma.

> Önceki sürümde API bilgileri kaynak koduna yazılmıştı. Bu bilgiler eski GitHub commit'lerinde görünüyorsa yeni bir API uygulaması/hash oluşturup eskisini kullanımdan kaldır.

### C. Session String üret

Bu proje kişisel Telegram hesabını dinlediği için bot tokenı değil, kullanıcı oturumu gerekir. Session String Telegram hesabına giriş anahtarıdır.

Telefondan en kolay yöntem:

1. GitHub'da `session_generator_colab.ipynb` dosyasını aç.
2. **Open in Colab** seçeneğine bas. Seçenek görünmezse dosyayı indirip `https://colab.research.google.com` üzerinde **File → Upload notebook** ile yükle.
3. Notebook'un üst kısmından **Runtime → Run all** seç.
4. API ID'yi gir.
5. API Hash'i gir.
6. Telefon numaranı ülke koduyla gir: `+905xxxxxxxxx`.
7. Telegram uygulamasına gelen doğrulama kodunu gir.
8. İki aşamalı doğrulama etkinse Telegram parolanı gir.
9. Ekrana basılan `SESSION_STRING` değerinin tamamını kopyala.
10. Notebook'u paylaşma, çıktıyı kaydetme ve değeri kimseye gönderme.

Colab'da kodu çalıştırmak istemezsen aynı işlem Python kurulu bir bilgisayarda şöyle yapılır:

```bash
pip install -r requirements.txt
API_ID=12345678 API_HASH=your_hash python generate_session.py
```

### D. GitHub Secret'larını oluştur

Repository sayfasında:

**Settings → Secrets and variables → Actions → New repository secret**

Şu üç secret'ı ayrı ayrı oluştur:

| İsim | Değer |
|---|---|
| `API_ID` | API development tools içindeki sayı |
| `API_HASH` | API development tools içindeki hash |
| `SESSION_STRING` | Colab veya `generate_session.py` çıktısı |

Değerler kaydedildikten sonra GitHub bunları tekrar düz metin olarak göstermez. Yanlış girersen aynı secret için **Update secret** kullan.

### E. Otomatik yenileme için GH_PAT oluştur

Actions job'u yaklaşık 5 saat 30 dakika sonra kendi devamını başlatabilsin diye ek bir GitHub token gerekir. Bu token Telegram tokenı değildir; yalnızca GitHub workflow başlatır.

1. GitHub profil fotoğrafına bas.
2. **Settings → Developer settings → Fine-grained personal access tokens → Generate new token** seç.
3. Token için bir isim yaz: `telegram-monitor-restart`.
4. Mümkünse kısa/uygun bir expiration tarihi seç. Süresi dolunca yenilemek gerekir.
5. **Repository access → Only select repositories** seç.
6. Yalnızca bu repository'yi seç (**Only select repositories** → kendi repo'n).
7. **Repository permissions → Actions → Read and write** seç.
8. Token'ı oluştur ve ekranda bir kez gösterilen değeri kopyala.
9. Repository'de **Settings → Secrets and variables → Actions → New repository secret** aç.
10. İsim olarak `GH_PAT`, değer olarak token'ı gir.

Bu token'ı kaynak koda yazma. Token'ın süresi dolarsa otomatik yenileme çalışmaz; workflow manuel olarak başlatılabilir ve token güncellenebilir.

### F. config.json dosyasını düzenle

GitHub'da `config.json` dosyasını açıp kalem simgesiyle düzenle. Örnek:

```json
{
  "source_chats": [
    "@firsatkanali",
    "-1001234567890"
  ],
  "destination": -5092968106,
  "include_keywords": [
    "çay",
    "kahve",
    "şeker"
  ],
  "exclude_keywords": [
    "çekiliş",
    "hediye"
  ],
  "match_mode": "any",
  "copy_mode": "copy",
  "link_appendix": "smart",
  "message_link": true,
  "source_footer": true,
  "notify_media": true,
  "control_chat": -5092968106,
  "admin_user_id": 1143378073,
  "auto_restart": true,
  "notify_on_start": true
}
```

Ayarların anlamı:

- `source_chats`: Hesabının zaten katıldığı kanal/grup kullanıcı adları veya ID'leri. Kullanıcı adı için `@` kullan.
- `destination`: `me` filtrelenenleri Kayıtlı Mesajlar'a gönderir (**bildirim gelmez**). Grup/kanal ID'si yazarsan oraya düşer ve bildirim alırsın.
- `include_keywords`: Mesajın metninde aranacak kelimeler. Küçük/büyük harf farkı yoktur; `İ`/`I` gibi Türkçe büyük harfler de doğru indirgenir.
- `exclude_keywords`: Eşleşen kelimelerden biri varsa mesaj atlanır. Hariç tutma kuralı önce çalışır.
- `match_mode: any`: Dahil kelimelerden en az biri yeterli. `all`: hepsi aynı mesajda bulunmalı.
- `copy_mode: forward`: Telegram forward etiketi korunur. `copy`: kaynak etiketi kaldırılır.
- `delivery_modes`: Korumalı (`noforwards`) kanallarda sırayla denenecek yollar: `forward → copy → media → text → link`. Biri hata verirse sıradaki denenir (bkz. **M**).
- `max_media_mb: 25`: `media` yolunda indirilecek en büyük medya boyutu.
- `link_appendix: "smart"`: Mesajda **"Fırsata Git" gibi yazıların altına gizlenmiş linkler** mesajın içinde tıklanabilir kalır (tekrar yazılmaz). Inline buton ve önizleme linkleri metne yazılır, çünkü bunlar başka türlü taşınamaz. `"all"` yazarsan tüm gizli linkler `🔗 ...` satırı olarak da eklenir; `"off"` hiçbirini eklemez. Bkz. **O. Gizli bağlantılar**.
- `message_link: true`: Her iletinin sonuna `🔗 Mesajı Gör: https://t.me/<kanal>/<mesaj>` satırı eklenir. En kötü senaryoda (link bir şekilde alınamazsa) bu satırdan orijinal fırsat mesajına gidilir.
- `source_footer: true`: Bildirim mesajının en altına `Fırsatı Gönderen: <kaynak adı>` ekler. Kaynak adı tıklanabilirdir ve **orijinal fırsat mesajının Telegram linkini gizler**; ürün linki alınamazsa tek dokunuşla mesaja gidilir.
- `notify_media: true`: Bildirim botu fotoğraf/videoyu da gönderir (gönderemezse otomatik olarak metin bildirimine düşer).
- `control_chat: me`: Komutlar Kayıtlı Mesajlar'dan alınır. Grup ID'si verilirse komutlar o gruptan alınır.
- `admin_user_id`: Grup kullanıyorsan kendi Telegram kullanıcı ID'n. `null` bırakma; aksi halde gruptan komut çalışmaz. Sayı, `"123"` metni veya `[123, 456]` listesi kabul edilir.
- `auto_restart: true`: GH_PAT varsa yenileme zincirini açar.
- `notify_on_start: true`: Her açılışta hedefe kısa bir "başladım" mesajı gönderir.
- `notify_bot_token`: Bildirim bot'unun token'ı. `null` ise takipçi çalışır ama bildirim gelmez.

Chat ID bilmiyorsan önce kanalın kullanıcı adıyla (`@kanaladi`) deneyebilirsin. Özel gruplar için `-100...` formatındaki ID gerekir. Hesabın kaynak kanala katılmamışsa kullanıcı hesabı mesajları göremez — bot açılışta üye olmadığın kaynakları log'da `ÜYE DEĞİLSİN` diye işaretler.

> **ID yazım biçimi:** `"destination": -5092968106` (sayı) ile `"destination": "-5092968106"` (metin) aynıdır;
> bot metinleri sayıya çevirir. Eski sürümde çevrilmiyordu ve bu yüzden grup hedefi/komutları sessizce çalışmıyordu.

### G. İlk çalıştırma

1. Repository'de **Actions** sekmesini aç.
2. Soldan **Telegram indirim takipçisi** workflow'unu seç.
3. **Run workflow** düğmesine bas.
4. Branch olarak `main` seç.
5. Yeşil işaret oluşmasını bekle.
6. Run'a girip önce **Ayarları doğrula**, sonra **Mesajları dinle** adımının log'unu aç.
7. Şu tip satırlar görmelisin:

```text
Ortam değişkenleri : API_ID=var API_HASH=var SESSION_STRING=var GH_PAT=var
...
Bağlanıldı: kullaniciadi (id=1143378073)
Kaynak hazır: FırsatZ   id=-1001234567890   istenen=@firsatz
Komutlar şu sohbetlerde dinleniyor: Benim Grup [-5092968106]
Hedef: Benim Grup [-5092968106]
Dinleniyor... (kaynak=16, kontrol=[...], hedef=...)
```

8. Gruba `/status` yaz — bot yanıt veriyorsa hem bağlantı hem komut yolu çalışıyor demektir.
9. `/test` yaz — hedefe deneme mesajı düşmeli.
10. Kaynak kanallardan test mesajı gönder veya yeni bir indirim mesajı bekle.
11. Kelime eşleşirse hedef sohbete iletilir; log'da `Eşleşti: ...` satırı görünür.

Session hatası alırsan yeni bir session üretip `SESSION_STRING` secret'ını güncelle. `config.json` hatası alırsan **Ayarları doğrula** adımı hangi alanın bozuk olduğunu yazar.

### H. Yenileme zinciri nasıl çalışır?

GitHub Actions job'u tek başına sonsuza kadar yaşayamaz. Workflow'da 350 dakikalık üst sınır vardır. `bot.py` bu sınıra gelmeden, varsayılan olarak 330. dakikada:

1. Hedef sohbete yenileme uyarısı gönderir.
2. GitHub Actions API'ye `workflow_dispatch` isteği yapar.
3. `GH_PAT` ile aynı workflow'un yeni bir çalışmasını başlatır.
4. Workflow'daki `concurrency` ayarı eski job'u iptal edip yeni job'u devralmasını sağlar.

Yeni job'un başlatılması yoğunluk nedeniyle gecikebilir. GH_PAT yoksa bu otomatik zincir devreye girmez ve workflow yaklaşık 350. dakikada kapanır. Bu durumda Actions sayfasından tekrar **Run workflow** yapılır.

GitHub Actions'ın scheduled workflow minimumu 5 dakikadır ve zamanlama kesin değildir. Bu proje scheduled workflow ile her dakika tarama yapmaz; bot çalışırken Telegram bağlantısını açık tuttuğu için mesajları geliş anında işler. Job'lar arasında GitHub kaynaklı boşluk olabileceği için kritik, kesintisiz 7/24 hizmet garantisi verilemez.

### I. Telegram komutları

Varsayılan `control_chat: me` ayarıyla Kayıtlı Mesajlar'a yaz:

| Komut | Türkçe takma adı | Ne yapar |
|---|---|---|
| `/status` | `/durum` | Çalışma süresi, dinlenen kaynak sayısı, görülen/eşleşen/iletilen sayaçları, son eşleşme, hedef ve kelimeler |
| `/test` | `/deneme` | Hedefe deneme mesajı gönderir; iletim yolunu doğrular |
| `/source` | `/kaynak` | İzlenen kanalları çözülen ID'leriyle listeler, çözülemeyenleri işaretler |
| `/id` | — | Bu sohbetin ve senin kullanıcı ID'ni gösterir; config'e kopyalayabilirsin |
| `/restart` | `/yenile` | `GH_PAT` varsa yeni Actions çalışmasını hemen başlatır |
| `/help` | `/yardim` | Komut listesi |

Komutlar yalnızca `control_chat` olarak ayarlanan sohbetten **ve** `admin_user_id` ile eşleşen kullanıcıdan kabul edilir. Kayıtlı Mesajlar her zaman açıktır (oraya yazan zaten hesabın sahibidir).

Grup komutu için `control_chat` grup ID'si ve `admin_user_id` kendi ID'n olacak şekilde `config.json`ı değiştir. Sonra config değişikliğini commit et. Grup herkese açıksa yalnızca kendi ID'n kabul edilir.

### J. Sorun giderme

| Belirti | Muhtemel neden | Çözüm |
|---|---|---|
| Workflow görünmüyor | PR main'e merge edilmedi veya Actions kapalı | PR'ı merge et, Settings → Actions'tan workflow'ları etkinleştir |
| Run 10-15 saniyede kırmızı oluyor | Bir secret boş (genelde `API_HASH` veya `SESSION_STRING`) | **Ayarları doğrula** adımının log'una bak; eksik secret'ı ekle |
| `ValueError: Your API ID or Hash cannot be empty` | `API_ID`/`API_HASH` secret'ı boş | İki secret'ı da Settings → Secrets'a ekle |
| `SESSION_STRING` yetkisiz | Session yanlış/kesik veya Telegram oturumu iptal edildi | Colab notebook ile yeniden üret |
| `Cannot find any entity corresponding to "-5092968106"` | Grup ID'si **metin** olarak verilmiş, Telethon onu kullanıcı adı sanıyor | ID'yi sayı olarak yaz (`-5092968106`) veya bot'u güncelle (artık otomatik çevriliyor) |
| Grup komutları hiç çalışmıyor | `admin_user_id` boş/`null` ya da `control_chat` yanlış | `admin_user_id`'ye kendi ID'ni, `control_chat`'e grup ID'sini yaz |
| Mesajlar geliyor ama bildirim almıyorum | Takipçi **kendi hesabınla** gönderiyor; Telegram kendi mesajın için bildirim üretmez | `notify_bot_token` kur: @BotFather → `/newbot`, botu gruba ekle, token'ı config'e yaz (bkz. **L. Bildirim kurulumu**) |
| Mesaj gelmiyor | Kanal kaynak listesinde değil, kullanıcı hesapla kanala katılmamış veya kelime eşleşmiyor | `/source` ile çözümü, `/status` ile sayaçları kontrol et; log'daki `ÜYE DEĞİLSİN` uyarılarına bak |
| Mesajda "Fırsata Git" yazıyor ama ham link görünmüyor | Link, yazının **altına gizlenmiş** (`MessageEntityTextUrl`) olduğu için düz metinde yok; tıklanabilir olarak taşınır | Yazının üstüne dokun → link açılır. Ham URL de görünsün istersen `link_appendix: "all"`. Her durumda `🔗 Mesajı Gör:` satırı mesaja götürür (bkz. **O**) |
| Fotoğraf "unnamed" adlı dosya olarak geliyor | Eski sürüm medyayı adsız `bytes` olarak yeniden yüklüyordu; Telethon adı `unnamed` varsayıp belge olarak gönderiyordu | Bot'u güncelle: yeniden yükleme uzantılı isimle yapılır, fotoğraf fotoğraf olarak gider (bkz. **M**) |
| Bildirim, mesajın kırpılmış/küçük harfli hâlini gösteriyor | Eski bildirim biçimi `🔔 Yeni fırsat (...) – ilk 120 karakter` | Bot'u güncelle: bildirim artık mesajın tamamı + `Fırsatı Gönderen` satırıdır (bkz. **L**) |
| Log'da `Task exception was never retrieved` | Bir chat çözülemiyor (eski sürümde tüm dinlemeyi öldürürdü) | Bot'u güncelle; artık çözülemeyen chat atlanır ve loglanır |
| `GH_PAT` ile yenileme olmuyor | Token Actions yazma iznine sahip değil veya süresi doldu | Token izinlerini ve expiration tarihini kontrol et |
| Aynı mesaj iki kez geliyor | Aynı anda iki job çalışmış olabilir | Actions concurrency ayarını ve açık workflow run'larını kontrol et |
| `FloodWait`/rate limit | Çok fazla forward işlemi | Kaynak sayısını ve kelime filtresini daralt, Telegram bekleme süresine uy |
| Grup komutu çalışmıyor | Grup ID veya admin kullanıcı ID yanlış | ID'leri kontrol edip config commit et |

### K. Testler

Değişiklik yapınca şunları çalıştır (yalnızca `telethon` gerekir, ek bağımlılık yok):

```bash
python -m unittest discover -s tests -v   # 147 test: eşleştirme, config, açılış akışı, komutlar, bildirim, iletim zinciri, gizli linkler
python bot.py --check                     # secret + config doğrulaması
```

`push` ve `pull_request` olaylarında **Testler** workflow'u aynı iki komutu GitHub'da da çalıştırır.

### L. Bildirim botu kurulumu (adım adım)

Takipçi **kendi Telegram hesabınla** çalışır. Telegram kendi gönderdiğin mesajlar için
bildirim üretmez; bu yüzden fırsat mesajı gruba düşse bile telefonuna uyarı gelmez.
Çözüm: gruba ikinci bir gönderici olarak küçük bir bot eklemek. Bildirimi üreten,
botun attığı mesajdır.

**Bildirim biçimi (varsayılan):**

```text
<fırsat mesajının TAMAMI – biçimi, emoji'leri ve gizli linkleriyle>
   (ör. "Fırsata Git" yazısı hâlâ tıklanabilir: linki yazının altında)

🔗 Mesajı Gör: https://t.me/FirsatZ/31543    ← orijinal mesajın linki (her zaman eklenir)
🔗 https://amzn.to/yyyyy                     ← yalnızca buton/önizleme linki varsa (link_appendix)

Fırsatı Gönderen: FırsatZ                    ← "FırsatZ" de aynı mesaja giden gizli linki taşır
```

- Mesaj **kırpılmaz, küçük harfe çevrilmez, yeniden yazılmaz**; kaynaktaki hâliyle gider.
- Metnin altına gizlenmiş linkler **tıklanabilir kalır**; ayrıca ham URL olarak tekrar yazılmaz (kullanıcı isteği). Ham hâlini de görmek istersen `link_appendix: "all"` yap.
- Inline butonlar (`Fırsata Git` butonu gibi) bildirimde **aynı buton olarak** yeniden kurulur.
- Mesajda fotoğraf/video varsa bot onu da gönderir (`notify_media`).
- `🔗 Mesajı Gör:` satırı, link ne olursa olsun orijinal fırsat mesajına giden **nihai güvencedir**; `message_link: false` ile kapatılabilir (önerilmez).
- `source_footer: false` yaparsan altbilgi eklenmez.
- Bu mesaj, hesabının sessizce gönderdiği kopyanın **yanına** eklenir; amacı bildirim üretmektir.

**1. Bot oluştur (2 dakika)**

1. Telegram'da **@BotFather** sohbetini aç.
2. `/newbot` yaz.
3. Görünen isim sorulur → örn. `İndirim Bildirim`.
4. Kullanıcı adı sorulur → `_bot` ile bitmeli, örn. `benim_indirim_uyari_bot`.
5. BotFather sana bir token verir:

```text
7123456789:AAHx... (46 karakter)
```

Bu token'ı kimseyle paylaşma; botunu tamamen kontrol eder.

**2. Botu gruba ekle**

1. Fırsatların düşeceği grubu aç (ör. `-5092968106`).
2. Grubun adına bas → **Üyeler / Members** → **Üye ekle / Add members**.
3. Botunun kullanıcı adını (`@benim_indirim_uyari_bot`) yaz ve ekle.
4. Yönetici yapmana **gerek yok**; sadece "mesaj gönderme" yetkisi yeterli.

**3. Token'ı config'e yaz**

GitHub'da `config.json`'ı düzenle:

```json
"notify_bot_token": "7123456789:AAHx..."
```

Commit et. (Alternatif: `NOTIFY_BOT_TOKEN` adında bir GitHub secret/variable da kullanabilirsin.)

**4. Test et**

1. Workflow'u yeniden başlat (**Actions → Run workflow**).
2. Gruba `/test` yaz.
3. Yanıt şunu söylüyorsa tamamdır: `🔔 Bot bildirimi de gönderildi (telefonuna düşmeli).`
   Gelen deneme mesajında `Fırsatı Gönderen:` satırını da görmelisin.
4. Telefona bildirim gelmiyorsa: Telegram → Ayarlar → Bildirimler → grup bildirimlerinin
   açık olduğundan ve botun gruptan atılmadığından emin ol.

**Sorun giderme**

| `/test` yanıtı | Anlamı | Çözüm |
|---|---|---|
| `HTTP 403: bot is not a member of the group chat` | Bot gruba eklenmemiş | 2. adımı tekrarla |
| `HTTP 400: chat not found` | `destination` ID'si yanlış | Gruba `/id` yaz, çıkan ID'yi config'e koy |
| `HTTP 401: Unauthorized` | Token yanlış/kopyalanırken bozulmuş | BotFather'dan `/revoke` ile yenisini al |
| `HTTP 429` | Çok sık mesaj | Bot yalnızca eşleşme başına 1 mesaj atar; kaynak sayısını azalt |

Token'ı `null` bırakırsan takipçi aynı şekilde çalışır, sadece bildirim gelmez.

> Bot API'si grup ID'sini Telethon ile aynı biçimde kullanır (`-5092968106` gibi),
> bu yüzden `destination` için ayrı bir ID gerekmez.

### M. Korumalı içerik: alternatifli iletim zinciri

Birçok indirim kanalı **"içeriği koru / Restrict saving content"** ayarını açar
(`noforwards`). O kanallarda:

- **İlet (forward)** → `CHAT_FORWARDS_RESTRICTED` hatası verir.
- **Kopyala (copy)** → aynı şekilde reddedilebilir.

Bu yüzden tek bir yol denenmez. Bot sırayla dener, hata alırsa bir sonrakine geçer
ve log'a hangi yolda patladığını yazar:

| Sıra | Yol | Ne yapar | Ne zaman devreye girer |
|---|---|---|---|
| 1 | `forward` | Sunucu tarafında iletir; kaynak etiketi ve albüm yapısı korunur | Varsayılan, en ucuz yol |
| 2 | `copy` | Mesaj nesnesini hedefe yeniden gönderir | forward koruma nedeniyle reddedilirse |
| 3 | `media` | **Medyayı indirip hedefe sıfırdan yükler** | forward ve copy ikisi de reddedilirse |
| 4 | `text` | Yalnızca metni/başlığı gönderir, "medya iletilemedi" notu ekler | medya indirilemiyorsa veya `max_media_mb` üstündeyse |
| 5 | `link` | `t.me` bağlantısı kartı gönderir | metin de yoksa |

```json
"delivery_modes": ["forward", "copy", "media", "text", "link"],
"max_media_mb": 25
```

- `delivery_modes`: sırayı kendin belirleyebilirsin. Yazmadığın yollar yine yedek olarak
  sona eklenir; bilinmeyen bir isim yazarsan `--check` uyarır.
- Eski `copy_mode: "copy"` alanı hâlâ çalışır: o yolu **ilk sıraya** koyar, gerisi yedek kalır.
- `max_media_mb`: `media` yolunda indirilecek en büyük medya. GitHub Actions runner'ı geçici
  olduğu için varsayılan 25 MB; büyük videolar doğrudan `text` yoluna düşer. `0` = sınırsız.

**Gerçek ekran görüntüsü alınabilir mi?** Hayır — sunucu tarafında çalışan bir istemci ekran
görüntüsü üretemez. Bunun yerine 3. yol aynı işi görür: fotoğraf/video indirilip hedefe
yeniden yüklendiği için görsel olarak orijinaliyle aynı mesajı görürsün. Hiçbiri olmazsa
5. yol tıklanabilir bağlantı bırakır.

> `/status` komutu hangi yolun kaç kez işe yaradığını gösterir:
> `İletim sırası: forward → copy → media → text → link | kullanılan: forward×120, media×7`

### N. Grup ve kullanıcı ID'sini doğrulama

`config.json`'daki ID'lerin doğru olduğundan emin olmanın en hızlı yolu: **gruba `/id` yaz.**

```text
🆔 Bu sohbetin ID'si: -5092968106
• Tür: Chat
• Senin kullanıcı ID'n: 1143378073
config.json için:
  "control_chat": -5092968106,
  "admin_user_id": 1143378073
```

Çıkan değerleri doğrudan config'e kopyalayabilirsin. `/id` komutu da `control_chat`
içinde çalıştığı için, komut hiç yanıt vermiyorsa mevcut ayarlar yanlış demektir — o zaman
geçici olarak `"control_chat": "me"` yapıp Kayıtlı Mesajlar'dan `/id` ile grup ID'sini al.

> **Dikkat:** temel grup (ID `-5092968106` gibi) sonradan süpergruba dönüştürülürse ID
> `-100...` biçiminde değişir ve config güncellenmesi gerekir.

### O. Gizli bağlantılar (en kritik ayar)

İndirim kanalları ürün linkini çoğu zaman açıkça yazmaz; şu üç yere gizler:

| Gizleme yolu | Örnek | Bot ne yapar |
|---|---|---|
| Metnin altına gizlenmiş hyperlink (`MessageEntityTextUrl`) | "**Fırsata Git**" yazısı görünür, link yazının altındadır | Link mesajın içinde tıklanabilir kalır; ayrıca sonuna `🔗 Mesajı Gör: https://t.me/...` eklenir (`link_appendix: "all"` ise ham URL de yazılır) |
| Inline buton (`Fırsata Git` butonu) | Yazıda hiç link yok, link mesaj altındaki butondadır | Bildirimde buton aynen yeniden kurulur; hesap kopyasında link `🔗 ...` satırı olarak yazılır (kullanıcı hesapları buton gönderemez) |
| Link önizlemesi (`webpage`) | Metinde link görünmez, önizleme kartı vardır | Önizlemedeki hedef URL de link listesine girer |

Ek olarak, her iletinin sonuna `🔗 Mesajı Gör: https://t.me/...` satırı eklenir: orijinal
fırsat mesajının Telegram linkidir.

**Çift taraflı garanti:** bildirimde `Fırsatı Gönderen: <kaynak>` satırındaki kaynak adı
da orijinal mesajın t.me linkini gizler. Ürün linki herhangi bir nedenle alınamazsa bile
bildirime dokunup doğrudan fırsat mesajına gidebilirsin.

**Ayarlar**

```json
"link_appendix": "smart",
"message_link": true,
"source_footer": true,
"notify_media": true
```

- `link_appendix: "smart"` (varsayılan) → gizli hyperlink'ler mesajda tıklanabilir kalır, tekrar yazılmaz; buton/önizleme linkleri metne yazılır.
- `link_appendix: "all"` → ek güvence: tüm gizli linkler `🔗 <etiket>: <url>` satırı olarak da yazılır.
- `link_appendix: "off"` → hiçbir bağlantı eki yazılmaz (önerilmez).
- `message_link: false` → `🔗 Mesajı Gör:` satırı eklenmez (nihai güvence kapanır; önerilmez).
- `source_footer: false` → `Fırsatı Gönderen:` satırı ve gizli mesaj linki olmaz.
- `notify_media: false` → bildirim yalnızca metin olur; medya gönderilmez.

Aynı ayarlar ortam değişkeniyle de verilebilir: `LINK_APPENDIX`, `MESSAGE_LINK`, `SOURCE_FOOTER`, `NOTIFY_MEDIA`.
(Eski `APPEND_LINKS` anahtarı da çalışır: `true` → `all`, `false` → `off`.)

**Not:** Gizli linklerin entity olarak korunması, kaynak mesajın biçimlendirmesine bağlıdır;
Telegram'ın metin gönderiminde entity'ler yalnızca `formatting_entities` ile korunur. Bu bot
bunu yapar; nihai güvence olarak `🔗 Mesajı Gör:` satırı her iletide bulunur.
