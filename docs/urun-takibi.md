# Akakçe ürün takibi

## Akakçe ürün takibi — aynı botun özel sohbetinde

Varsayılan **kapalıdır**. Aynı Actions job'unda çalışır; yeni bot/token gerekmez.
Mevcut kanal filtreleri, iletim zinciri ve dedup akışı değişmez. Yalnız hesap sahibi
botun **özel sohbetinden** yönetir ve alarmı yine orada alır; hedef gruba gönderilmez.

### Bildirim biçimi

```text
₺6.839 — Philips Airfryer 3000 Serisi HD9243/90 L 4.1 lt Yağsız Fritöz

Akakçe Takip Bildirimi

https://www.pttavm.com/…

https://www.akakce.com/fritoz/…-fiyati,248390291.html
```

İlk satır mevcut kanal bildirimlerinin aynı başlık fonksiyonunu kullanır: kuruş
**yalnız başlıkta**, yuvarlanmadan atılır. Hedef karşılaştırması kuruşlu gerçek
fiyatla yapılır. Bloklar arasında bir boş satır vardır. Önizleme kapalıdır; alarm
tek, düz metin mesajdır. Hedef, satıcı açıklaması veya ek footer normal alarma
ilave edilmez. Teknik/kayıt hatası varsa ayrıca bir uyarı mesajı gelebilir.

Satın alma linki yalnız doğrulanmış en düşük **fiyat + aynı satıcı** ile JSON-LD
teklifinde eşleşirse eklenir. Merchant/varyant/kupon query parametreleri korunur.
Link bulunamaz veya güvenlik denetimini geçmezse o blok atlanır; başka satıcının
linki veya tahmini link kullanılmaz. Akakçe takip linki her zaman en sondadır.
Satıcı sitesine ek HTTP isteği yapılmaz.

### İlk kontrollü deneme

Bu komutlar ancak PR birleştirilip **güncel ana bot başladıktan sonra** kullanılabilir.
PR kontrolü tek başına çalışan botu güncellemez. Merge sonrasında mevcut botun
özel sohbetine `/restart` gönderip yenilemenin tamamlanmasını bekleyebilirsin
(mevcut GH_PAT gerekir); bu komutun mevcut workflow yenileme yolu değiştirilmedi.
Yenilenen bot hazır olunca `/webtakip` menüyü göstermelidir. Aynı bot token'ıyla
ikinci bir bağımsız Telegram dinleyicisi başlatma.

Botla özel sohbetinde sırayla:

1. `/durum` — mevcut dinleyicinin yanıt verdiğini kontrol et.
2. `/webtakip` — alt komutların kısa menüsünü açar.
3. `/webtakipac` — **Kalıcı kaydedildi** yanıtını bekle.
4. Takip edeceğin Akakçe **ürün detay linkini** gönder.
5. Bot ürünü ve okuduğu fiyatı gösterip hedefi sorunca, test için gördüğün güncel
   fiyatın biraz **üstünde** hedef yaz. Örneğin fiyat hâlâ 6.839,05 TL ise `7000`.
6. Kalıcı kayıt onayını bekle. İlk başarılı periyodik okumada yukarıdaki biçimde
   özel mesaj beklenir. Boş kuyrukta genellikle sonraki kontrolde gelir; host
   beklemesi, ağ hatası veya yoğunlukta gecikebilir. Saniyesi garanti değildir.
7. `/webtakipler` — hedefi ve son kontrol durumunu gör. `/durum` ile eski dinleyiciyi
   tekrar kontrol et. Mevcut kanal fırsatları aynı hedefe gelmeye devam etmeli.
8. Test bitince **aynı Akakçe linkini tekrar gönder**, sorulduğunda gerçek hedefini
   yaz (örneğin `6500`). Bu işlem o ürünün hedefini günceller ve alarm kaydını sıfırlar.

Aynı fiyat her kontrolde yeniden gönderilmez. Alarm yalnız fiyat **hedefin kesin
altında** ise gelir; eşit fiyat gönderilmez. Daha düşük yeni fiyat tekrar bildirilebilir.
Fiyat yukarı çıkıp eski alarm fiyatına geri gelirse yeniden bildirilmez.

Mevcut `/test` komutunu bu Akakçe denemesi için kullanma: o komut eskisi gibi
hedef gruba test gönderir, Akakçe alarmını sınamaz.

| Komut | İşlev |
|---|---|
| `/webtakip` | Kısa menü ve komut kopyalama düğmeleri |
| `/webtakipac` | Ürün kontrollerini açar, GitHub'a kaydeder |
| `/webtakipkapat` | Takip listesini koruyarak kontrolleri kapatır |
| `/webtakipler` | Ürün numarası, hedef ve bu oturumun kontrol durumu |
| `/webtakipsil 1` | Listedeki numarayla ürün siler |
| `/webtakipiptal` | Hedef fiyat taslağını iptal eder |
| `/webtakipyardim` | Kısa kullanım |

Yalnız **`/webtakip`** komutunu hatırlaman yeterli. Menüde her alt komutun yanında
2–4 kelimelik açıklama ve komutu panoya kopyalayan düğmeler vardır. Düğmeye basmak
komutu çalıştırmaz; kopyalananı yazı alanına yapıştırıp gönderirsin. Telegram bot
mesajları yazı alanını doğrudan dolduramaz. `/webtakipsil` kopyalanınca ürün numarasını
sen eklersin. Kopyalama düğmelerini desteklemeyen istemcide düz komutları kullan.
Yeni komutlar tek ve kanonik kaynaktır; eski `/takip*` isimleri alias kalmadan kaldırıldı. `/webtakipyardim` ayrıntılı yardım olarak kalır.
Menüyü açmak bekleyen hedef/filtre taslağını bozmaz veya takibi açıp kapatmaz.

Mevcut filtre düzenleme taslağı açıksa önce `/kaydet` veya `/iptal` ile bitir.
Hedef taslağı 10 dakika sonra geçersiz olur. Bir ürün okuması sıradaysa komut sonucu
ayrıca bildirilir; yalnız "sıraya alındı" yanıtı kalıcı kayıt onayı değildir.
`/webtakipkapat` için de tamamlanma onayını bekle.

### Saatler ve "son kontrol" ne zaman güncellenir

Tüm saatler **TSİ** etiketiyle yazılır. GitHub Actions runner'ları UTC çalıştığı için
eski sürümde `/webtakipler` "Son kontrol: 10:44" diyordu; bu aslında **13:44 TSİ** idi ve
üç saat geride kalmış gibi görünüyordu (saat dilimi etiketi yoktu). Artık zaman damgası
epoch olarak tutulur ve `06.11 13:44 TSİ (6 dk önce)` biçiminde yazılır; satır ayrıca
hedefe olan farkı (`hedefin 10.928,75 TL üzerinde`) ve sıradaki kontrolün saatini
gösterir. Log satırlarının saati de TSİ'ye sabitlenmiştir.

Kontroller yalnız **runner açıkken** çalışır ve her kontrol zaman damgasını yalnız o
tarayıcı oturumunun belleğinde tutar (`product_watches.json` içine yazılmaz). Bu yüzden:

- Yeni bir runner açıldığında ilk satır bir süre
  `Son kontrol: bu çalışmada henüz yok` der — bu bir arıza değil, henüz ilk okumanın
  yapılmadığını söyler (uydurma bir saat yazılmaz).
- Saat **donmaz**: her başarılı okumada tazelenir ve parantezdeki yaş canlı artar.
  Başarısız okumada yaş büyümeye devam eder, durum `Okunamadı` olur ve artan bekleme
  `sıradaki kontrol` saatinde görünür.
- Runner kapanınca hiçbir saat ilerlemez; ekrandaki son saat, son gerçek kontrolün
  saatidir. Takip "açık" görünse bile o saatten sonra okuma yapılmamıştır.

**Nereden anlarım: kontrol ediyor mu, yoksa saat dondu mu?**

- `/webtakipler` satırındaki `(6 dk önce)` ifadesi canlıdır. Bekleyip tekrar
  baktığında büyüyorsa okuma olmuyor; küçülüp tazeleniyorsa kontrol çalışıyor.
- `sıradaki kontrol: ~14:13 TSİ` süresi geçtiği hâlde okuma yoksa satır
  `⏳ gecikti` yazar (kuyrukta iş var, ağ/engel geciktiriyor ya da runner kapandı).
- Okuma başarılı ama alarm gönderilemediyse satır "Okunamadı" demez:
  `60.927,75 TL · alarm iletilemedi (TimeoutError, 2. deneme)` yazar; fiyat ve saat korunur.
- Telegram gönderimi yanıt vermezse kontrol **45 saniyede** kopar ve döngü devam eder;
  bir gönderim takılıp takibi kilitleyemez.
- Actions log'unda her okuma tek satır bırakır:
  `Ürün kontrolü: TCL 65C6K → 60.927,75 TL (hedef 49.999,00 TL)` ve 30 dakikada bir
  `Takip kalp atışı: açık=True ürün=1 | … (az önce)`. Log saati TSİ'dir.

### Fiyat doğrulaması ve sınırlar

- Tüm satıcı sayfalarını indirmek gerekli değildir. Okunan somut geçerli TRY
  teklifinin minimumu, aynı ürünün `AggregateOffer.lowPrice` alanıyla eşleşmeli
  (`minimum_verified: true`). Eksik/uyuşmayan minimumla takip okuması reddedilir.
- Satıcı izin listesi yok. Eksik satıcı sayısı (`coverage_complete: false`), minimum
  eşleşiyorsa takibe engel değildir. Yıllık dip otomatik çekilmez; hedefi sen girersin.
- Gerçek kullanıcı raporunda 6.839,05 TL / Pttavm/Promo doğrulandı; JSON-LD 10 teklif,
  HTML ana liste 16 satır, bildirilen toplam 20 idi. Gerçek rapor parçaları offline
  regresyon fixture'larında tutulur. Bu ölçüm her ürüne/gelecek güne garanti değildir.
- Kupon, üyelik, kargo, stok ve fiyatın güncelliği satın almadan kontrol edilmeli.
  Aynı Akakçe yanıtını doğrulamak mağaza kasasındaki fiyatı doğrulamaz.
- İlk sürüm en fazla **30 ürün**; ürün başına **5 dakika hedef aralık**, host başına
  en az **10 saniye** boşluk kullanır. Hatalarda bekleme artar; üç başarısız
  kontrolde özel sohbetine uyarı gelir. Bunlar Akakçe'nin onaylı limitleri değildir.

### Kalıcılık ve izolasyon

`product_watches.json` ürünleri, hedefleri, açık/kapalı ve küçük alarm durumunu tutar.
Her kontrolde commit/HTML arşivi üretilmez. GitHub Contents API kullanılır; mevcut
config yazarıyla ortak kilit vardır, checkout kirletilmez. Başarısız kalıcı kayıtta
ayar geri alınır ve uyarı verilir. Ürün/hedefler public repo ve Git geçmişinde görünür;
secret yazılmaz. Mevcut Actions'ın repository/branch ve yazma token'ı gerekir.

Okuma ayrı, sırları almayan alt süreçtedir; süre, gövde boyutu ve kuyruk sınırlıdır.
Kanal dinleyicisi ağ işçisini beklemez. Aynı runner kaynakları paylaşıldığı için
mutlak izolasyon/kesintisiz çalışma garantisi yoktur. Actions geçişinde fırsat
kaçabilir. Telegram gönderimi ile GitHub kaydı atomik değildir; ani kesintide
tekrar alarm mümkündür. Bozuk takip dosyası yalnız bu özelliği devre dışı bırakır.

Kalıcı kayıt testi için doğal runner yenilenmesinden sonra `/webtakipler` gönder:
ürün/hedef korunmalı, aynı fiyattan gönderilmiş alarm normalde tekrar etmemeli.
Geri dönüş için `/webtakipkapat` yazıp kalıcı kayıt onayını bekle; mevcut kanal takibi sürer.

### Geçici erişim testi kaldırıldı

Geliştirme sırasında kullanılan `Akakçe erişim testi` workflow'u son PR'dan
çıkarıldı. PR güncellemelerinde ayrıca Akakçe sayfası açan veya rapor artifact'i
üreten bir iş artık yok. Önceden tamamlanan çalışmalar Actions geçmişinde
görünebilir; bu, yeniden çalıştıkları anlamına gelmez. Normal `Testler` workflow'u
kod regresyonlarını, `Telegram indirim takipçisi` ise asıl botu çalıştırmaya devam eder.
Akakçe takibi ayrıca bir workflow açmaz; ana botta `/webtakipac` ile açılır.

Actions ekranındaki eski `Gecici push testi` de ayrı bir tarihsel teşhis kaydıdır:
6 Ekim 2026'da eski `arena/61a972b6-indtkp-asist` dalında push kimlik doğrulamasını
sınamak için çalışmıştır. `tmp-push-test.yml` hem mevcut `main`de hem bu PR'da yoktur;
Asıl bot bu denemeyi çağırmaz. Gereksiz test betiği, sonuç dosyası veya çağrı kalıntısı
bulunmadı. Eski Actions kaydı PR ile silinmez; repo yöneticisi Actions → Gecici push
testi → ⋯ → Disable workflow ile kaydı devre dışı bırakabilir. Geçmiş çalışmalar
listede kalabilir. Botun `commit_and_push` / `push_branch` kodu gerçek ayar kaydı için
kullanılır, bu tarihsel testin parçası sayılıp kaldırılmamalıdır.

Deneme raporu: bildirim ekran görüntüsü, `/webtakipler` çıktısı, kayıt onayı/uyarıları ve
mevcut kanal dinleyicisinin çalışıp çalışmadığını paylaş. Cookie/token/secret paylaşma.
