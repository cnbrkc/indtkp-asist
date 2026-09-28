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
  "include_keywords": ["5070ti", "ekran kartı", "laptop"],
  "exclude_keywords": ["çekiliş", "sponsorlu"],
  "match_mode": "any",
  "copy_mode": "forward"
}
```

- `source_chats`: Virgül yerine JSON listesi kullanılır. Kanal adı veya `-100...` chat ID olabilir.
- `destination`: `me` = Kayıtlı Mesajlar; özel sohbet/grup ID'si de kullanılabilir.
- `include_keywords`: Bunlardan biri eşleşsin (`any`) veya hepsi eşleşsin (`all`).
- `exclude_keywords`: Bunlardan biri varsa mesaj gönderilmez.
- `copy_mode`: `forward` kaynak bilgisini korur, `copy` kaynak etiketini kaldırır.

GitHub web arayüzünde `config.json` dosyasını düzenleyip commit etmen yeterlidir. Secret'ları tekrar girmen gerekmez.

### 5. Başlat ve komutlar

**Actions → Telegram indirim takipçisi → Run workflow** seç. İlk çalıştırmada log'larda hesabın bağlandığını görmelisin.

Varsayılan olarak kendi **Kayıtlı Mesajlar** sohbetine şunları yazabilirsin:

```text
/status
/restart
```

`/restart`, `GH_PAT` eklenmişse yeni Actions çalışmasını hemen başlatır. `control_chat` değerini grup ID'si yapıp `admin_user_id` değerine kendi Telegram kullanıcı ID'ni yazarsan aynı komutları sadece o grupta ve sadece sen çalıştırabilirsin. Herkese açık gruplarda `admin_user_id` ayarlamadan kullanma.

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
