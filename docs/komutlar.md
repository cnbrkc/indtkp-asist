# Telegram komutları ve ayar düzenleme

## 4. Telegram komutları

Komutlar yalnızca `control_chat` sohbetinden **ve** `admin_user_id` listesindeki
kullanıcılardan kabul edilir. Kayıtlı Mesajlar her zaman açıktır. Yetkisiz biri komut
yazarsa bot nedenini, o kişinin kullanıcı ID'sini ve nasıl yetki alacağını yazar.

### Telegram açıklamasına kopyalanacak kısa metin

```text
Kaynak mesajlarını hedef gruba iletir.
/tgfiltreac — Filtre aç (sorar: dahili, harici veya ikisi).
/tgfiltrekapat — Filtre kapat (sorar: dahili, harici veya ikisi).
/ayar, /ekle, /cikar — kelime ve kaynak listelerini yönetir.
/kaydet, /iptal — taslağı kaydet / iptal et.
/analiz — Geçmiş başlıklarını tarar; en çok geçen kelimeleri istatistik olarak verir.
/kaynaktest — Kaynaklardan son mesajı okuyarak erişimi sınar.
/durum, /kaynaklar, /test, /id, /restart, /komutlar — durum ve araçlar.
```

`/tgfiltreac` ve `/tgfiltrekapat` anında uygulanır ve kaydedilir; tüm liste düzenlemeleri (DM dahil/hariç
dahil) `/kaydet` seçilene kadar taslakta kalır. `/restart` için
`GH_PAT` gerekir. Türkçe `I`/`İ` büyük harf yazımları (`/ANALİZ`, `/Iptal` gibi) otomatik eşleşir.

### Komut temizliği (ekranda yalnızca son mesaj)

`clean_commands` açıkken (varsayılan) **eski kullanıcı-hesabı kontrol sohbeti** kendini temizler.
**Botla özel sohbette bu temizlik uygulanmaz; geçmiş korunur.**

- Yeni bir komut yazdığında **bir önceki komutun** ve **botun ona verdiği yanıtın**
  tamamı silinir; ekranda yalnızca son komut ve yanıtı kalır.
- Çok adımlı akışlarda (`/ekle` → liste seç → değer → `/kaydet`) her adım bir öncekini
  siler; `/analiz` ilerleme mesajı rapor gelince kaybolur.
- Silme yalnızca komut diyaloğuna ait mesajları kapsar. **İndirim/ilan bildirimleri bu
  kayda hiç girmez, bu yüzden asla silinmez** — tek mesaj modunun sildiği hesap kopyası
  da bu kuralın dışındadır (o, bildirim başarılı olduğunda silinir).
- Silme için hesabın o mesajları silebilmesi gerekir (kendi mesajların her zaman
  silinebilir; başka bir admin kullanıcıdan gelen komutlar için grupta yönetici olman
  gerekir). Silinemezse bot uyarı loglar, komut çalışmaya devam eder.
- Kapatmak için `config.json` → `"clean_commands": false` (veya `CLEAN_COMMANDS=false`).

### Tüm desteklenen komutlar

| Komut | Açıklama |
|---|---|
| `/komutlar` | Tüm desteklenen komutları ve açıklamalarını kategoriler halinde listeler. |
| `/start` | Özel sohbet kullanımını açıklar; fırsat hedefini değiştirmez. |
| `/durum` | Takipçinin dinleme durumunu, hedefini, sayaçlarını ve filtre durumunu gösterir. |
| `/ayar` | Bot yönetim merkezi: liste, TG filtre, DM filtre ve web ürün takibi bölümlerini gösterir. |
| `/ekle` | Liste seçip kayıt ekleme taslağı başlatır (dahil/hariç/grup/DM filtreleri/ürün adı < fiyat/web ürün takibi). |
| `/cikar` | Liste seçip kayıt çıkarma taslağı başlatır (dahil/hariç/grup/DM filtreleri/ürün adı < fiyat/web ürün takibi). |
| `/kaydet` | Bekleyen liste taslağını kaydeder (DM listeleri dahil). |
| `/iptal` | Bekleyen ekleme veya çıkarma işlemini iptal eder. |
| `/tgfiltreac` | Telegram indirim akışında dahili, harici veya iki filtreyi açar. |
| `/tgfiltrekapat` | Telegram indirim akışında dahili, harici veya iki filtreyi kapatır. |
| `/dmfiltre` | Dört DM grubunun durumunu, kayıtlarını, eşiklerini ve oturum sayaçlarını gösterir. |
| `/dmfiltreac` | dmdahil grubunu açar; diğer gruplar bağımsızdır. |
| `/dmfiltrekapat` | dmdahil grubunu kapatır; dmfiyat ve ürün adı/fiyat kuralları bağımsız kalır. |
| `/webtakip` | Akakçe ürün takip menüsünü açar; Akakçe linkini gönderip hedef fiyatı belirlersin. |
| `/webtakipac` | Web ürün fiyat takibini etkinleştirir. |
| `/webtakipkapat` | Web ürün fiyat takibini durdurur; ana Telegram indirim akışını durdurmaz. |
| `/webtakipler` | Takip edilen web ürünlerini, hedeflerini ve son kontrol durumunu listeler. |
| `/webtakipsil` | Numarayla takip edilen web ürününü siler. |
| `/webtakipiptal` | Bekleyen web ürün takip işlemini iptal eder. |
| `/webtakipyardim` | Web ürün takibinin ayrıntılı kullanımını gösterir. |
| `/kaynaklar` | İzlenen kaynakları ve çözülemeyenleri listeler. |
| `/kaynaktest` | Her kaynağın son ham mesajını ve yerel işlem yolunu denetler; canlı event sayısını raporlar. |
| `/analiz` | Kaynak geçmişindeki başlıkları analiz eder; örnek: /analiz 100 tümü veya /analiz 1000 tcl 65 (eşleşen ürünleri 'ürün adı - fiyat' olarak listeler). |
| `/test` | Hedef gruba deneme mesajı gönderir; sonucu komut sohbetinde bildirir. |
| `/id` | Bulunduğun sohbetin ve kullanıcının kimliğini gösterir. |
| `/restart` | Yeni takipçi çalışması başlatır; GH_PAT gerekir. |

**Tamamı virgülle ayrılmış hâli** (BotFather açıklamasına, sabitlenmiş mesaja ya da
grubun komut menüsüne doğrudan yapıştırabilirsin):

```text
/komutlar, /start, /durum, /ayar, /ekle, /cikar, /kaydet, /iptal, /tgfiltreac, /tgfiltrekapat, /dmfiltre, /dmfiltreac, /dmfiltrekapat, /webtakip, /webtakipac, /webtakipkapat, /webtakipler, /webtakipsil, /webtakipiptal, /webtakipyardim, /kaynaklar, /kaynaktest, /analiz, /test, /id, /restart
```

#### Kaynak mesaj erişimini test etme

- `/kaynaktest` çözülmüş **tüm** kaynaklardan yalnızca en yeni mesajı okuyup erişim
  sonucunu, üyelik uyarısını, ham metin alanının birebir bütünlüğünü (uzunluk/hash),
  entity/link/medya bilgisini, yerel compose kuru denemesini ve bu çalışmada canlı
  alınan mesaj sayısını verir. Tek kaynak seçildiğinde ham mesajdan kısa bir örnek de
  gösterilir. `/kaynaktest 3`, `/kaynaktest @kanal` veya
  `/kaynaktest -100...` ile tek kaynak da seçilebilir. Çözülemeyen kaynaklar rapora
  ayrıca girer; bir kaynağın hatası diğer testleri durdurmaz.
- Bu bir duman (smoke) testidir: geçmişten bir mesaj okunması hesabın **şu anki erişimini**
  doğrular; kanala sahte mesaj atmaz. Bildirimler, kullanıcı hesabı üye olduğu ve takipçi
  çalıştığı sırada gelen yeni Telegram event'lerinden alınır. Kaynakta o çalışmada yeni
  mesaj yoksa canlı sayaç `0` kalması normaldir.
- Takipçi kapalıyken veya Actions yeniden başlarken oluşan mesajlar otomatik olarak
  sonradan iletilmez. `/analiz` geçmişi ayrıca okur ama geriye dönük bildirim göndermez.

---

## 5. Ayarları Telegram'dan düzenleme

Telegram akışını bilerek sade tuttuk: mevcut listeler ile dört DM grubu `/ekle` ve
`/cikar` taslak akışından düzenlenir; iki grup filtresi de `/tgfiltreac` ve
`/tgfiltrekapat` ile bağımsız açılıp kapatılır. Diğer teknik ayarlar config/kod üzerinden yönetilir.

| Menüde görünen ad | `config.json` alanı | Ne işe yarar |
|---|---|---|
| 🔎 Dahili kelimeler | `include_keywords` | Mesajda aranacak kelimeler |
| 🚫 Harici kelimeler | `exclude_keywords` | Bunlardan biri geçerse mesajı engeller |
| 📣 Grup isimleri | `source_chats` | Takip edilecek kanal/grup kullanıcı adı veya ID'si |
| 🎯 dmdahil | `dm_keywords` | Özel kopyada blok halinde aranan ifadeler; dmdahil etkinliği `dm_enabled` ile yönetilir |
| 🔕 dmharic | `dm_exclude_keywords` | dmdahil ve dmfiyat için ortak engel listesi |
| 💰 dmfiyat | `dm_price_max` | Ürün adından bağımsız tek maksimum gerçek ürün fiyatı |
| 🏷️ ürün adı < fiyat | `dm_product_price_rules` | Başlık ifadesi + gerçek ürün fiyatı; çoklu bağımsız kurallar |

DM listeleri yalnızca hesap sahibinin botla özel sohbetinden düzenlenir. `/tgfiltreac` ve
`/tgfiltrekapat` grup filtrelerine aittir. `/dmfiltreac` / `/dmfiltrekapat` yalnızca dmdahil'i
yönetir; dmfiyat ve ürün adı/fiyat kuralları kayıtlı olduklarında bağımsız çalışır.

#### Filtreler: `/tgfiltreac` ve `/tgfiltrekapat`

İki filtre **birbirinden bağımsızdır**; hangisinin çalışacağına tam olarak sen karar verirsin:

| Filtre | Config alanı | Açıkken | Kapalıyken |
|---|---|---|---|
| 🔎 Dahili kelimeler | `include_enabled` | Mesajda kelimelerden en az biri geçmeli (`any`) / hepsi geçmeli (`all`) | Anahtar kelimeler yok sayılır |
| 🚫 Harici kelimeler | `exclude_enabled` | Bu kelimelerden biri geçen mesaj engellenir | Hiç engelleme yapılmaz |

```text
/tgfiltreac              → "Hangi filtreyi açalım?" diye sorar
/tgfiltreac dahili       → yalnızca dahili kelime filtresini açar
/tgfiltreac harici       → yalnızca harici engeli açar
/tgfiltreac ikisi        → ikisini birlikte açar
/tgfiltrekapat dahili      → dahili filtreyi kapatır (harici engel çalışmaya devam eder)
/tgfiltrekapat harici      → engellemeyi kapatır (dahili kelimeler aranmaya devam eder)
/tgfiltrekapat ikesi       → ikisini de kapatır: kaynaklardaki HER mesaj iletilir
```

Menüden seçim yaparken `1` (dahili), `2` (harici), `3` (ikisi) yazabilirsin; filtre
adını yazmak da yeterli. Her iki komut değişikliği **anında** uygular, `config.json`'a
yazar ve GitHub'a gönderir; yanıtta iki filtrenin güncel durumu görünür. Yanlış bir şey
yazarsan menü açık kalır, `/iptal` ile vazgeçebilirsin. Bekleyen bir liste taslağı
(`/ekle` → `/kaydet` bekleyen) varken filtre komutları çalışmaz, önce taslağı
sonuçlandırman istenir.

Diğer ayarlar (`destination`, iletim yolları, bildirimler, token'lar vb.) Telegram'dan
değiştirilemez; bunları `config.json`/kod üzerinden düzenle.

### Ekleme

1. Kontrol sohbetinde **`/ekle`** yaz.
2. Gelen menüden `1`, `2` veya `3` gönder (istersen kategori adını da yazabilirsin).
3. Bot seçtiğin listenin mevcut kayıtlarını gösterir.
4. Eklenecek kelimeleri **tek mesajda** gönder. Birden çok kaydı virgül (`,`),
   noktalı virgül (`;`) veya yeni satırla ayır:
   `kahve, şeker, süt` ya da `@kanal1, @kanal2, -1001234567890`
5. Son onayda **`/kaydet`** ile GitHub'a gönder veya **`/iptal`** ile taslağı sil.

Tek mesajda en fazla 50 kayıt işlenir. Listede zaten olan veya geçersiz kayıtlar
atlanır; onay ekranında `⚠️` satırıyla kaç kaydın neden atlandığı yazar.

```text
➕  LİSTEYE EKLE
━━━━━━━━━━━━━━━━━━━━
1. 🔎  Dahili kelimeler  ·  3 kayıt
2. 🚫  Harici kelimeler  ·  2 kayıt
3. 📣  Grup isimleri     ·  23 kayıt

Seçmek için 1, 2 veya 3 yaz.
Vazgeçmek için /iptal.
```

Grup/kanal eklerken `@kullaniciadi`, `-1001234567890` biçiminde ID veya özel
davet linki (`https://t.me/+...` / `https://t.me/joinchat/...`) kullan. Özel link
önce **takipçinin kişisel Telegram hesabında** açılıp üyelik tamamlanmalıdır; bot
otomatik katılmaz, katılma isteği göndermez ve ücretli abonelik/ödeme yapmaz.
Onay veya abonelik bekliyorsa tamamlanmasını bekle. Sonra `/ekle` → `grup` → davet
linki → `/kaydet` kullan. Bot linki yalnızca üyeliği doğrulamak için kontrol eder,
taslağa ve config/Git geçmişine davet kodu yerine sohbet ID'si kaydeder. Davet kodunu
başkalarıyla paylaşma; komutu yalnızca güvenilir özel kontrol sohbetinde gönder.
Yeniden başlatmada özel ID çözülemezse sohbet listesi yenilenir. Üyelik bitmişse,
link iptal edilmişse veya Telegram erişimi engelliyorsa takip çalışmaz. Özel
kanalın `t.me/c/...` mesaj linkleri sadece erişimi olan hesaplarda açılır; normal
özel gruplarda mesaj linki üretilemeyebilir. Canlı takip sadece takipçi çalışırken
gelen mesajları alır. Bot
yeni kaynağı doğrulamaya çalışır; çözülemeyen kaynak taslağa alınmaz. **Kaydettikten sonra
`/kaynaktest @kullaniciadi`** (veya sadece `/kaynaktest` ile tüm kaynaklar) yazarak
hesabın kanaldan son mesajı okuyabildiğini ve üyelik durumunu kontrol et.

### Çıkarma

1. **`/cikar`** yaz.
2. Kategoriyi seç; bot güncel listeyi numaralı olarak gösterir.
3. Çıkarmak istediğin kayıtların numaralarını veya listedeki tam değerlerini gönder;
   birden çok kayıt için virgülle ayır: `1, 3, çekiliş`
4. Onay mesajında **`/kaydet`** ya da **`/iptal`** seç.

Takip edilen kaynak listesinden son grup/kanal silinemez; botun çalışması için en az
bir kaynak kalmalıdır (bu yüzden hepsini birden çıkaran istek reddedilir).

### Taslak ve kayıt davranışı

Değer mesajını göndermek tek başına ayarı değiştirmez. Bot önce bir taslak ve iki net
seçenek gösterir:

```text
📝  DEĞİŞİKLİK TASLAĞI
━━━━━━━━━━━━━━━━━━━━
📂 Dahili kelimeler
➕ Eklenecek: kahve

Bu değişiklik henüz aktif değil ve config.json'a yazılmadı.

✅ Kaydet ve GitHub'a gönder  →  /kaydet
↩️ İptal et ve taslağı sil     →  /iptal
```

- **`/kaydet`** (veya sadece `kaydet`): değişikliği çalışan ayara uygular, `config.json`'ı
  atomik biçimde yazar ve GitHub deposuna commit/push etmeyi dener. Yanıtta GitHub sonucu görünür.
- **`/iptal`** (veya sadece `iptal`): bekleyen adımı/taslağı siler; config dosyası ve çalışan ayar değişmez.
- Bekleyen işlem 10 dakika kullanılmazsa zaman aşımına uğrar. Bot yeniden başlarsa
  tamamlanmamış taslak bellekte tutulduğu için silinir.
- Menü sırasında yanlış bir seçim yaparsan doğru seçimi tekrar gönder. Taslak onay
  aşamasındayken önce `/kaydet` veya `/iptal` ile sonuçlandır.

### Başlık kelime analizi (`/analiz`)

Hangi ürün türlerini hiç görmek istemediğini bulmak için geçmiş mesajların
**başlığı** (mesajın ilk anlamlı satırı; marka/ürün adının yazdığı yer) taranır ve
iki istatistik verilir:

1. **En çok geçen 25 kelime** (başlıkta geçen tüm kelimeler),
2. **En çok geçen 25 ilk kelime** (her başlığın ilk kelimesi).

```text
/analiz            → kaynak başına son 300 mesaj
/analiz 1000       → kaynak başına son 1000 mesaj (20–2000 arası)
/analiz tümü       → işlevsiz kelimeler ve saf sayılar da sayılsın
/analiz 500 tümü   → ikisi birlikte
/analiz 1000 tcl 65→ sorgu modu: son 1000 mesajda 'tcl 65' geçen ürünler
```

- Yalnızca başlık okunur; açıklama, fiyat ve link satırları sayılmaz.
- Aynı başlık birden çok kanaldan gelmişse bir kez sayılır (tekrar birleştirme);
  böylece tek bir kampanya istatistiği bozmaz. Raporda kaç tekrarın
  birleştirildiği yazar.
- Varsayılan listede "ve/ile/için" gibi işlevsiz kelimeler ve saf sayılar elenir;
  hepsini görmek için `/analiz tümü` kullan.
- Harici listende olan kelimeler `🚫`, dahili listende olanlar `🔎` ile işaretlenir.
- Çıkan listeden istemediğin kelimeleri doğrudan harici listeye ekle:
  `/cikar` → `2` (Harici kelimeler) → `bebek, oyuncak, kitap`.
- Tarama uzun sürebilir; **"🔎 Analiz başladı"** mesajı gelir ve her kaynak
  bitince aynı mesaj güncellenir (`12/23 kaynak · 4 eşleşme`). Rapor gelince bu
  mesaj silinir, ekranda yalnızca sonuç kalır. Okunamayan kaynak olursa tarama
  durmaz, raporda "Okunamayan kaynak" satırı çıkar.

#### Sorgu modu (`/analiz <adet> <kelime…>`)

- Tüm kaynakların son *adet* mesajında sorgu geçen mesajlar listelenir; her satır
  `ürün adı - fiyat` biçimindedir ve **ürün adı tam yazılır** (kesilmez), böylece
  model kodlu başlıklar olduğu gibi görünür:
  `TCL 65 inç Akıllı TV 65C7K 4K QD-Mini LED … - 31.866 TL`
- Fiyat, fiyat satırlarındaki (`💰`, `🏷`, `Fiyat:`) **en düşük** değerdir;
  "eski 44.999 TL / yeni 31.866 TL" gibi ikililerde indirimli fiyat görünür.
  Taksit tutarı, "200 TL üzeri" eşiği ve kupon tutarı ürün fiyatı sayılmaz.
- Sayı yalnızca argümanların başında mesaj limiti sayılır; `tcl 65` içindeki 65
  sorguda kalır. Sorgu kelime sınırlı eşleşir (`tcl 65` ≠ `tcl 655`) ve Türkçe
  büyük/küçük harf duyarsızdır. Bitişik yazım: `/analiz350`.
- Aynı satır birden çok kaynakta varsa bir kez yazılır; en fazla 200 satır
  gösterilir, uzun liste birden çok mesaja bölünür.
