# Telegram indirim takipçisi

Kişisel Telegram hesabının üye olduğu kanalları dinler, filtreden geçen mesajları
istediğin sohbete iletir. Polling yapmaz: bağlantıyı açık tutar, mesaj gelir gelmez işler.
Ayarları elle düzenlemek zorunda kalmamak için her şeyi Telegram'dan da
değiştirebilirsin ([5. bölüm](#5-ayarları-telegramdan-değiştirme)).

---

## İçindekiler

1. [Sıfırdan kurulum](#1-sıfırdan-kurulum-hatırlatma-listesi)
2. [Nasıl çalışır ve sınırları](#2-nasıl-çalışır-ve-sınırları)
3. [Ayarlar: `config.json`](#3-ayarlar-configjson)
4. [Telegram komutları](#4-telegram-komutları)
5. [Ayarları Telegram'dan değiştirme](#5-ayarları-telegramdan-değiştirme)
6. [Bildirim kurulumu](#6-bildirim-kurulumu-telefona-uyarı-gelsin)
7. [İletim zinciri: korumalı kanallar](#7-iletim-zinciri-korumalı-kanallar)
8. [Gizli bağlantılar](#8-gizli-bağlantılar)
9. [ID'leri doğrulama](#9-idleri-doğrulama)
10. [Sorun giderme](#10-sorun-giderme)
11. [Güvenlik](#11-güvenlik)
12. [Geliştirici notları](#12-geliştirici-notları)
13. [Bu güncellemeden sonra yapılacaklar](#13-bu-güncellemeden-sonra-yapılacaklar)

---

## 1. Sıfırdan kurulum (hatırlatma listesi)

Daha önce kurduysan bu bölüm hatırlatma niyetine; toplam 5 adım, ~10 dakika.

### Adım 1 — Telegram API bilgisini al

[my.telegram.org](https://my.telegram.org) → **API development tools** → `API_ID` ve `API_HASH`.
Kaynak kanallara **kişisel hesabınla** katıl (bot hesabı kanalları okuyamaz).

### Adım 2 — Session string üret

Bilgisayar şart değil. Depodaki `session_generator_colab.ipynb` dosyasını GitHub'da açıp
**Open in Colab** ile çalıştır (notebook'u public paylaşma):

1. API ID gir → 2. API Hash gir → 3. Telefonu `+90...` biçiminde gir →
4. Gelen kodu gir → 5. İki aşamalı doğrulama varsa parolayı gir →
6. Çıkan `SESSION_STRING` değerini kopyala.

Python olan bir ortamda alternatif:

```bash
pip install -r requirements.txt
API_ID=... API_HASH=... python generate_session.py
```

> Bu değer Telegram hesabının giriş anahtarıdır. Kimseyle paylaşma.

### Adım 3 — GitHub secret'larını ekle

**Settings → Secrets and variables → Actions → Secrets → New repository secret**

| Secret | Zorunlu | Ne için |
|---|---|---|
| `API_ID` | ✅ | Telegram API kimliği |
| `API_HASH` | ✅ | 32 karakterlik API hash'i |
| `SESSION_STRING` | ✅ | Adım 2'de ürettiğin oturum anahtarı |
| `GH_PAT` | ➖ | Sadece otomatik yenileme zinciri için (aşağıda) |

Biri eksikse workflow açılışta durur ve **Ayarları doğrula** adımı hangi secret'ın boş
olduğunu Türkçe olarak log'a yazar. Aynı kontrolü yerelde de çalıştırabilirsin:

```bash
python bot.py --check
```

**GH_PAT** (opsiyonel): Actions job'u en fazla ~6 saat yaşar. Bu token, job bitmeden yeni
bir çalışma başlatarak zinciri sürer. Fine-grained token oluştururken bu repo için
**Actions: Read and write** iznini ver. Eklemezsen bot yine çalışır, sadece 6 saatte bir
elle (veya schedule ile) başlatman gerekir.

### Adım 4 — `config.json`'ı düzenle

Deponun kökündeki [`config.json`](config.json) dosyasını GitHub'dan düzenle (kalem simgesi):

```json
{
  "source_chats": ["@indirimkanali", "-1001234567890"],
  "destination": -5092968106,
  "include_keywords": ["çay", "kahve", "şeker"],
  "exclude_keywords": ["çekiliş", "hediye"],
  "match_mode": "any",
  "copy_mode": "copy",
  "delivery_modes": ["forward", "copy", "media", "text", "link"],
  "max_media_mb": 25,
  "link_appendix": "smart",
  "message_link": true,
  "source_footer": true,
  "notify_media": true,
  "control_chat": -5092968106,
  "admin_user_id": 1143378073,
  "auto_restart": true,
  "notify_on_start": true,
  "notify_bot_token": null
}
```

Alanların hepsi [3. bölümde](#3-ayarlar-configjson) tek tek anlatılıyor. ID bilmiyorsan
[9. bölüm](#9-idleri-doğrulama)deki `/id` komutunu kullan.

> **ID'ler tırnak içinde de yazılabilir** (`"-5092968106"`); bot sayıya çevirir.

### Adım 5 — Çalıştır ve test et

1. **Actions → Telegram indirim takipçisi → Run workflow → `main`** → çalıştır.
2. **Ayarları doğrula** adımının log'unda yeşil "yapılandırma geçerli ✅" görmelisin.
3. **Mesajları dinle** adımında `Bağlanıldı: ...` ve `Dinleniyor...` satırları gelmeli.
4. Kontrol sohbetine `/status` yaz → bot yanıt veriyorsa komut yolu çalışıyor.
5. `/test` yaz → hedefe deneme mesajı düşmeli.
6. Telefonuna bildirim gelsin istiyorsan [6. bölümdeki](#6-bildirim-kurulumu-telefona-uyarı-gelsin) bildirim botunu mutlaka kur.

**Kurulum kontrol listesi**

- [ ] `API_ID`, `API_HASH`, `SESSION_STRING` secret'ları dolu
- [ ] `config.json` geçerli (`python bot.py --check` temiz geçiyor)
- [ ] Kaynak kanallara kişisel hesapla üye olunmuş (log'da `ÜYE DEĞİLSİN` yok)
- [ ] `/status` ve `/test` yanıt veriyor
- [ ] (İsteğe bağlı) Bildirim botu kurulu, `/test` bildirim gönderiyor

---

## 2. Nasıl çalışır ve sınırları

- `bot.py` çalıştığı sürece mesajlar **anlık** işlenir; 1 dakika bekleyip tarama yapmaz.
- **GitHub Actions kalıcı sunucu değildir.** Job yaklaşık 5 saat 50 dakika çalışır, sonra
  yeniden başlatılır. Başlatmalar arasında kısa boşluklar olabilir.
- GitHub'ın scheduled workflow için resmi en kısa aralığı 5 dakikadır ve zamanlama
  yoğunlukta gecikebilir
  ([GitHub Docs](https://docs.github.com/actions/using-workflows/workflow-syntax-for-github-actions)).
  Bu yüzden "her dakika garanti tarama" Actions ile mümkün değildir.
- `GH_PAT` varsa bot, süre dolmadan ~30 dakika önce yeni bir Actions çalışması başlatır;
  `concurrency` ayarı eski job'u kapatıp yenisini devralır. Yine de token iptali, GitHub
  yoğunluğu veya hesap limitleri nedeniyle **mutlak 7/24 garanti yoktur**.
- Gerçekten kesintisiz çalışma istersen `bot.py`'yi ücretsiz bir **Oracle Cloud Free Tier
  VM**'de `systemd` servisi olarak çalıştır; Actions'ı yedek olarak bırak.

**Oracle VM ile kurulum (özet)**

1. Oracle Cloud Free Tier hesabı aç, Ubuntu VM kur.
2. Repo'yu clone et, `pip install -r requirements.txt`.
3. Secret değerlerini ortam değişkeni veya `.env` olarak tanımla (**`.env`yi GitHub'a gönderme**).
4. `deploy/telegram-filter.service` dosyasını `systemd`'ye tanıtıp servisi başlat.
5. VM yeniden başlarsa servis otomatik kalkar.

---

## 3. Ayarlar: `config.json`

| Alan | Tip | Açıklama |
|---|---|---|
| `source_chats` | liste | Dinlenen kanal/grup listesi: `@kullaniciadi` veya `-100...` ID. **Hesabın üye olmadığı kanaldan mesaj gelmez.** |
| `destination` | sayı / `"me"` | Filtrelenen mesajların gideceği sohbet. `me` = Kayıtlı Mesajlar (**bildirim gelmez**); grup ID'si yazarsan oraya düşer. |
| `include_keywords` | liste | Aranacak kelimeler. Büyük/küçük harf farkı yoktur; `İ`/`I` doğru indirgenir. |
| `exclude_keywords` | liste | Bunlardan biri geçerse mesaj atlanır. **Her modda önce bu kural çalışır.** |
| `match_mode` | `any` / `all` / `forward_all` | `any`: kelimelerden biri yeterli. `all`: hepsi aynı mesajda olmalı. `forward_all`: **filtre kapalı, tüm mesajlar iletilir**. |
| `copy_mode` | `forward` / `copy` | Eski alan. `delivery_modes` yoksa ilk denenecek yolu belirler. |
| `delivery_modes` | liste | Korumalı kanallarda sırayla denenecek iletim yolları: `forward → copy → media → text → link` ([7. bölüm](#7-iletim-zinciri-korumalı-kanallar)). |
| `max_media_mb` | sayı | `media` yolunda indirilecek en büyük medya (varsayılan 25, `0` = sınırsız). |
| `link_appendix` | `smart` / `all` / `off` | Gizli linklerin eklenme biçimi ([8. bölüm](#8-gizli-bağlantılar)). |
| `message_link` | `true` / `false` | Her iletinin sonuna `🔗 Mesajı Gör: <t.me linki>` ekler (nihai güvence; kapatman önerilmez). |
| `source_footer` | `true` / `false` | Bildirime `Fırsatı Gönderen: <kaynak>` satırı ekler; kaynak adı orijinal mesajın linkini gizler. |
| `notify_media` | `true` / `false` | Bildirim botu fotoğraf/videoyu da göndersin. |
| `control_chat` | sayı / `"me"` | Komutların dinleneceği sohbet. `me` = Kayıtlı Mesajlar. |
| `admin_user_id` | sayı / liste | `control_chat` bir grupsa **zorunlu**: komutları yalnızca bu ID'ler çalıştırabilir. |
| `auto_restart` | `true` / `false` | `GH_PAT` varsa yenileme zincirini açar (bir sonraki açılışta geçerli). |
| `notify_on_start` | `true` / `false` | Her açılışta hedefe kısa bir "başladım" mesajı gönderir. |
| `notify_bot_token` | metin / `null` | Bildirim botu token'ı. `null` ise takipçi çalışır ama **bildirim gelmez** ([6. bölüm](#6-bildirim-kurulumu-telefona-uyarı-gelsin)). |

**Ortam değişkeni ile geçersiz kılma:** Aynı adların büyük harflisi (`SOURCE_CHATS`,
`DESTINATION`, `MATCH_MODE`, `LINK_APPENDIX`…) config.json'ın üzerine yazar. Detaylar
`.env.example` dosyasında. (Eski `APPEND_LINKS` anahtarı da çalışır: `true` → `all`,
`false` → `off`.)

---

## 4. Telegram komutları

Komutlar yalnızca `control_chat` sohbetinden **ve** `admin_user_id` listesindeki
kullanıcılardan kabul edilir. Kayıtlı Mesajlar her zaman açıktır. Yetkisiz biri komut
yazarsa bot nedenini, o kişinin kullanıcı ID'sini ve nasıl yetki alacağını yazar.

| Komut | Türkçe | Ne yapar |
|---|---|---|
| `/status` | `/durum` | Çalışma süresi, kaynak sayısı, görülen/eşleşen/iletilen sayaçları, son eşleşme, hedef |
| `/test` | `/deneme` | Hedefe deneme mesajı gönderir; iletim yolunu ve bildirimi doğrular |
| `/source` | `/kaynak` | İzlenen kanalları ve çözülemeyenleri listeler |
| `/id` | — | Bu sohbetin ve senin kullanıcı ID'ni verir; config'e kopyalayabilirsin |
| `/restart` | `/yenile` | `GH_PAT` varsa yeni Actions çalışmasını hemen başlatır |
| `/ayar` | — | Ayar menüsü: gruplar (`/filtre`, `/iletim`…) ve işlemler (`/ekle`, `/sil`, `/set`) — [5. bölüm](#5-ayarları-telegramdan-değiştirme) |
| `/help` | `/yardim` | Komut listesi |

---

## 5. Ayarları Telegram'dan değiştirme

`config.json`'ı elle düzenleyip commit etmene gerek yok. Üç yol var; hangisi
kolayına geliyorsa onu kullan. **Her değişiklik anında aktif olur ve kalıcı olarak
kaydedilir.**

### A) Grup komutları — "hangi ayar nerede?" diye bakmak için

| Komut | İçindeki ayarlar |
|---|---|
| `/filtre` | aranan/hariç kelimeler, eşleşme modu |
| `/kanallar` | dinlenen kanal ve gruplar |
| `/hedef` | fırsatların gittiği sohbet |
| `/bildirim` | bildirim botu, medya, kaynak altbilgisi |
| `/iletim` | iletim yolları, medya boyutu |
| `/linkler` | gizli linkler, mesaj linki |
| `/yetki` | komut sohbeti, yetkili ID'ler |
| `/sistem` | otomatik yenileme |

Bunlar "yardım sayfası" gibi çalışır: yazınca o gruptaki ayarların **güncel değerleri**
ve her biri için hazır komut gelir. Telegram `/komut` yazılarını tıklanabilir yapar;
komuta dokun, değeri yaz, gönder.

```text
🔎 Filtre — aranan/hariç kelimeler ve eşleşme modu

1. match_mode — any (biri yeterli) | all (hepsi zorunlu) | forward_all (tüm mesajlar)
   değer: any
   ✏️ /mod <değer>   👁 /mod_goster

2. include_keywords — Aranan kelimeler
   değer: 3 kayıt: çay, kahve, şeker
   ➕ /kelime_ekle <değer>   ➖ /kelime_sil <değer>   👁 /kelime_goster
...
```

### B) Her ayarın kendi komutu — en hızlı yol

Kalıp basit: **kısa ad + isteğe bağlı eylem eki.**

| Komut | Ne yapar |
|---|---|
| `/kelime_ekle çay` | aranan kelimelere "çay" ekler |
| `/kelime_sil 2` | 2 numaralı kelimeyi siler |
| `/mod 3` | eşleşme modunu 3 numaralı seçenek yapar (= `forward_all`) |
| `/hedef -1001234567890` | hedef sohbeti değiştirir |
| `/token 7123...` | bildirim botu token'ını yazar |
| `/admin_ekle 424242` | başka birine komut yetkisi verir |
| `/medya 40` | medya boyutu sınırını 40 MB yapar |
| `/mesajlinki kapalı` | açık/kapalı ayarını kapatır |

Eylem ekleri: `_ekle` (listeye ekle), `_sil` (listeden çıkar), `_goster` (göster).
Eksiz yazarsan "değiştir" anlamına gelir: `/mod any`, `/hedef me`.

**Değeri yazmazsan bot sana sorar.** `/mod` yazıp gönderdiğinde seçenekleri
numaralandırır; cevap olarak sadece `3` yazman yeterli.

Kısa adların tam listesi: `mod`, `kelime`, `haric`, `kanal`, `hedef`, `kontrol`,
`admin`, `yol`, `kopya`, `medya`, `linkeki`, `mesajlinki`, `altbilgi`,
`bildirimmedya`, `acilisbildirimi`, `yenileme`, `token`. Uzun adları da
(`include_keywords` gibi) ve kendi uyduracağın adları da kabul eder
(`/dahil_liste_ekle` gibi).

### C) Menüden seçmeli — hiçbir adı ezberlemeden

Alan adı aklında değilse işlem komutunu yaz, bot sana seçenekleri sunsun:

| Komut | Ne yapar |
|---|---|
| `/ekle` | "Hangi listeye ekleyelim?" → menü → alan seç → değer yaz |
| `/sil` | "Hangi listeden çıkaralım?" → aynı şekilde |
| `/set` | "Hangi ayarı değiştirelim?" → aynı şekilde |
| `/goster` | Bir ayarı gösterir |
| `/ayar_goster` | Tüm ayarları özetler |
| `/kaydet` | `config.json`'ı tekrar yazar ve depoya göndermeyi dener |
| `/iptal` | Bekleyen soruyu iptal eder; yoksa son değişikliği geri alır |

Menüde hem numara hem kısa ad geçerli: `2` ya da `haric` aynı sonucu verir.

```text
👤 /ekle
   ➕ Hangi listeye ekleyelim?
      1. include_keywords …  /kelime_ekle
      2. exclude_keywords …  /haric_ekle
      …
👤 kelime          (veya "1")
   ✍️ include_keywords — Şimdi değeri yaz (virgülle çoklu: çay, kahve)
👤 kahve, şeker
   ➕ include_keywords: kahve, şeker eklendi (toplam 3)
```

Enum ve liste alanlarında **numara her yerde çalışır**: `/mod 3`, `/mod` → `3`,
`/kelime_sil 2` aynı işi yapar.

> Bekleyen soru 10 dakika sonra düşer; yeni bir komut yazdığında da iptal olur.
> Yarım kalan akış telefonunda değil botun belleğinde tutulur, yani bot yeniden
> başlarsa kaybolur (zararı yok, sadece soruyu tekrar sorarsın).

### Değiştirilebilen alanlar

`source_chats`, `destination`, `control_chat`, `admin_user_id`, `include_keywords`,
`exclude_keywords`, `match_mode`, `delivery_modes`, `copy_mode`, `max_media_mb`,
`link_appendix`, `message_link`, `source_footer`, `notify_media`, `notify_on_start`,
`auto_restart`, `notify_bot_token`.

Bilinmeyen bir alan yazarsan bot kabul etmez ve seçenekleri gösterir.

### Kalıcılık nasıl çalışır?

1. Değişiklik doğrulanır ve **çalışan bot'a uygulanır** (anında aktif).
2. `config.json` **atomik** yazılır: geçici dosya → `fsync` → `os.replace`. Yarım kalmış bir
   config dosyası bot'u açılışta çökertmez.
3. Dosya git ile commit edilip `origin`e gönderilir. Böylece Actions job'u yeniden başlasa
   bile değişiklik kaybolmaz.

Yanıtta sonucu görürsün: `✅ config.json yazıldı ve repo'ya işlendi (origin/main)`.
Depoya yazılamazsa `⚠️ ... yalnızca bu oturumda geçerli` uyarısı alırsın
([13. bölüm](#13-bu-güncellemeden-sonra-yapılacaklar) ve [10. bölüm](#10-sorun-giderme)).

Sohbet gerektiren alanlar (`destination`, `control_chat`, `source_chats`) değişince
Telegram'da yeniden çözülür. Yeni değer çözülemezse değişiklik **geri alınır** — yanlış bir
ID yüzünden bot komutlarını duyamaz hâle gelmezsin. Tek bir kaynak kanal çözülemezse sadece
uyarı verilir, diğerleri çalışmaya devam eder.

---

## 6. Bildirim kurulumu (telefona uyarı gelsin)

Takipçi **kendi Telegram hesabınla** gönderir; Telegram kendi gönderdiğin mesajlar için
bildirim üretmez. Bu yüzden fırsat gruba düşse bile telefonuna uyarı gelmez. Çözüm: gruba
ikinci bir gönderici olarak küçük bir bot eklemek — bildirimi onun attığı mesaj üretir.

**Bildirim biçimi:** fırsat mesajının tamamı (biçimi, emojileri ve gizli linkleriyle) +
`🔗 Mesajı Gör: <orijinal mesaj linki>` + `Fırsatı Gönderen: <kaynak>` satırı.
Mesaj kırpılmaz, küçük harfe çevrilmez.

**Adımlar (2 dakika)**

1. Telegram'da **@BotFather** → `/newbot` → görünen ad → `_bot` ile biten kullanıcı adı.
   BotFather sana `7123456789:AAHx...` biçiminde bir token verir (kimseyle paylaşma).
2. Fırsatların düştüğü grubu aç → **Üyeler → Üye ekle** → botun kullanıcı adını yaz ve ekle.
   Yönetici yapmana gerek yok.
3. `config.json`a token'ı yaz ve commit et:

   ```json
   "notify_bot_token": "7123456789:AAHx..."
   ```

   (Alternatif: `NOTIFY_BOT_TOKEN` adında bir GitHub secret/variable.)
4. Workflow'u yeniden başlat, gruba `/test` yaz. `🔔 Bot bildirimi de gönderildi
   (telefonuna düşmeli).` yazıyorsa tamamdır.

**Sorun giderme**

| `/test` yanıtı | Anlamı | Çözüm |
|---|---|---|
| `HTTP 403: bot is not a member` | Bot gruba eklenmemiş | 2. adımı tekrarla |
| `HTTP 400: chat not found` | `destination` ID'si yanlış | Gruba `/id` yaz, çıkan ID'yi kullan |
| `HTTP 401: Unauthorized` | Token bozuk/yanlış | BotFather'dan `/revoke` ile yenisini al |
| `HTTP 429` | Çok sık mesaj | Bot eşleşme başına 1 mesaj atar; kaynak sayısını azalt |

Token'ı `null` bırakırsan takipçi aynı şekilde çalışır, sadece bildirim gelmez.

---

## 7. İletim zinciri: korumalı kanallar

Birçok indirim kanalı **"içeriği koru"** (`noforwards`) ayarını açar. Orada `forward`
`CHAT_FORWARDS_RESTRICTED` hatası verir, `copy` de reddedilebilir. Bu yüzden tek yol
denenmez; sırayla deneyip ilk başarılı olanı kullanır:

| Sıra | Yol | Ne yapar |
|---|---|---|
| 1 | `forward` | Sunucu tarafında iletir; kaynak etiketi korunur (en ucuz yol) |
| 2 | `copy` | Mesajı hedefe yeniden gönderir |
| 3 | `media` | **Medyayı indirip sıfırdan yükler** — görsel olarak orijinaliyle aynı |
| 4 | `text` | Sadece metni gönderir, "medya iletilemedi" notu ekler |
| 5 | `link` | `t.me` bağlantı kartı gönderir |

- `delivery_modes` ile sırayı kendin belirleyebilirsin; yazmadığın yollar yedek olarak sona
  eklenir, bilinmeyen isim olursa `--check` uyarır.
- `/status` komutu hangi yolun kaç kez işe yaradığını gösterir:
  `İletim sırası: forward → copy → media → text → link | kullanılan: forward×120, media×7`

---

## 8. Gizli bağlantılar

İndirim kanalları ürün linkini çoğu zaman açıkça yazmaz; üç yere gizler:

| Gizleme yolu | Örnek | Bot ne yapar |
|---|---|---|
| Metin altına gizlenmiş hyperlink | "**Fırsata Git**" yazısı görünür, link altındadır | Link mesajın içinde tıklanabilir kalır |
| Inline buton | Yazıda link yok, butondadır | Bildirimde buton aynen kurulur; hesap kopyasında `🔗 ...` olarak yazılır |
| Link önizlemesi | Metinde link yok, önizleme kartı var | Önizleme hedefi link listesine girer |

Ek olarak her iletinin sonuna `🔗 Mesajı Gör: <t.me linki>` eklenir.
`link_appendix: "smart"` (varsayılan) gizli hyperlink'leri tekrar yazmaz, yalnızca başka
türlü taşınamayan linkleri metne ekler. `"all"` hepsini ham URL olarak da yazar;
`"off"` hiçbirini yazmaz (önerilmez).

---

## 9. ID'leri doğrulama

En hızlı yol: **gruba `/id` yaz.**

```text
🆔 Bu sohbetin ID'si: -5092968106
• Tür: Chat
• Senin kullanıcı ID'n: 1143378073
config.json için:
  "control_chat": -5092968106,
  "admin_user_id": 1143378073
```

`/id` da bir kontrol komutu olduğu için, komut hiç yanıt vermiyorsa mevcut `control_chat`
yanlış demektir: geçici olarak `"control_chat": "me"` yapıp Kayıtlı Mesajlar'dan `/id` ile
doğru ID'yi al.

> **Dikkat:** temel grup (`-5092968106` gibi) sonradan süpergruba dönüşürse ID `-100...`
> biçimine değişir ve config'i güncellemen gerekir.

---

## 10. Sorun giderme

| Belirti | Muhtemel neden | Çözüm |
|---|---|---|
| Workflow görünmüyor | PR `main`'e merge edilmedi veya Actions kapalı | PR'ı merge et; Settings → Actions'tan workflow'ları etkinleştir |
| Run 10-15 saniyede kırmızı | Bir secret boş (genelde `API_HASH`) | **Ayarları doğrula** adımının log'una bak |
| `Your API ID or Hash cannot be empty` | `API_ID`/`API_HASH` boş | İki secret'ı da ekle |
| `SESSION_STRING` yetkisiz | Session kesilmiş/iptal edilmiş | Colab notebook ile yeniden üret |
| `Cannot find any entity corresponding to "-5092968106"` | ID metin olarak verilmiş | Sayı olarak yaz (güncel sürüm otomatik çeviriyor) |
| Grup komutları çalışmıyor | `admin_user_id` boş veya `control_chat` yanlış | `/id` ile ID'leri doğrula; config'i güncelle |
| Mesaj geliyor ama bildirim yok | Kendi hesabın gönderiyor; Telegram bildirim üretmez | [6. bölüm](#6-bildirim-kurulumu-telefona-uyarı-gelsin) |
| Mesaj hiç gelmiyor | Kanal listede değil / hesap üye değil / kelime eşleşmiyor | `/source` ve `/status`a bak; log'daki `ÜYE DEĞİLSİN` uyarılarını kontrol et |
| "Fırsata Git" var ama ham link yok | Link yazının altına gizlenmiş; tıklanabilir | Yazıya dokun. Ham URL istersen `link_appendix: "all"` |
| Fotoğraf "unnamed" dosya olarak geliyor | Eski sürüm hatası | Bot'u güncelle |
| `⚠️ ... yalnızca bu oturumda geçerli` | Ayar değişikliği depoya yazılamadı | [13. bölüm](#13-bu-güncellemeden-sonra-yapılacaklar) |
| `GH_PAT` ile yenileme olmuyor | Token izni yok veya süresi dolmuş | Token'da **Actions: Read and write** olduğunu ve expiration tarihini kontrol et |
| Aynı mesaj iki kez geliyor | İki job aynı anda çalışmış | Actions concurrency ayarını ve açık run'ları kontrol et |
| `FloodWait` / rate limit | Çok fazla forward | Kaynak sayısını ve kelime filtresini daralt |

---

## 11. Güvenlik

- `SESSION_STRING`, API hash'i ve telefon kodunu kimseye gösterme.
- Bunlar geçmişte GitHub'a push edildiyse [my.telegram.org](https://my.telegram.org)
  üzerinden API uygulamasını yenile.
- Session geçersiz olursa yeni session üretip `SESSION_STRING` secret'ını güncelle.
- Kaynak kanalların kurallarına uy; yüksek hacimli otomatik iletim Telegram rate-limit'ine
  takılabilir.

---

## 12. Geliştirici notları

```bash
python -m unittest discover -s tests -v   # 241 test
python bot.py --check                     # secret + config doğrulaması
```

`push` ve `pull_request` olaylarında **Testler** workflow'u aynı iki komutu GitHub'da da
çalıştırır. Ek bağımlılık yok, sadece `telethon` gerekir.

Dosyalar: `bot.py` (tüm mantık), `config.json` (ayarlar), `generate_session.py` ve
`session_generator_colab.ipynb` (session üretimi), `deploy/telegram-filter.service`
(systemd örneği), `tests/` (birim ve uçtan uca testler).

---

## 13. Bu güncellemeden sonra yapılacaklar

Bu sürüm Telegram'dan ayar yönetimini (grup menüleri `/filtre`…, her ayarın kendi
komutu `/kelime_ekle`… ve seçmeli akış `/ekle` → menü → değer) ve `match_mode:
forward_all` modunu getiriyor. Ayrıntılar [5. bölümde](#5-ayarları-telegramdan-değiştirme).
Aktif olması için üç şey yapman yeterli:

### 1. PR'ı `main`'e merge et

Actions workflow'ları **varsayılan daldan** (`main`) çalışır; PR açıkken yeni kod devreye
girmez. PR #4'ü merge ettikten sonra:

**Actions → Telegram indirim takipçisi → Run workflow → `main`** ile yeni kodu bir kez elle
başlat. (Bunu yapmazsan mevcut job eski kodla 6 saate kadar devam eder.)

### 2. `GH_PAT` — sadece süresi dolduysa yenile

- **Otomatik yenileme zinciri** için `GH_PAT`'a *Actions: Read and write* izni yeterli;
  mevcut token'ın çalışması için dokunmana gerek yok.
- **Ayarların depoya yazılması** GitHub Actions'ta **ekstra bir şey gerektirmez**: workflow
  artık `GITHUB_TOKEN` kullanıyor ve job'a `contents: write` izni verilmiş durumda. Yani
  `GH_PAT`'ı sırf bu özellik için yenilemene gerek yok.
- Token'ın süresi zaten dolmuşsa yenilerken izin listesine dikkat et:
  - Actions zinciri → **Actions: Read and write**
  - VM'de çalıştırıyorsan depoya yazmak için → **Contents: Read and write**

### 3. Doğrula

Kontrol sohbetine şunları yaz:

```text
/ayar        → menü gelmeli (ayar grupları + işlem komutları)
/filtre      → filtre ayarlarının güncel değerleri ve kısa komutları
/ekle        → "Hangi listeye ekleyelim?" menüsü gelmeli
/mod         → seçenekleri numaralandırmalı: 1 any · 2 all · 3 forward_all
3            → "✅ match_mode: any → forward_all" + "repo'ya işlendi (origin/main)"
/mod_goster  → forward_all olduğunu teyit et
/iptal       → istersen son değişikliği geri alır
```

Yanıtta **`✅ repo'ya işlendi`** yazıyorsa kalıcılık çalışıyor demektir: repo'da
`config.json` güncellenmiş olur ve Actions yeniden başlasa da ayarın kalır.

`⚠️ ... yalnızca bu oturumda geçerli` uyarısı alırsan önce **PR'ın merge edildiğinden ve
yeni bir run başlattığından** emin ol (eski kodda bu özellik yoktur); hâlâ uyarı varsa
[10. bölümdeki](#10-sorun-giderme) ilgili satıra bak.

### Hızlı özet

- [ ] PR #4'ü `main`'e merge et
- [ ] Actions'tan workflow'u bir kez elle başlat
- [ ] `/ayar` → menü geliyor mu? `/ekle` → alan menüsü çıkıyor mu?
- [ ] `/mod` → `3` → "repo'ya işlendi" diyor mu?
- [ ] `GH_PAT`: süresi dolmadıysa dokunma; dolmuşsa *Actions: Read and write* ile yenile
