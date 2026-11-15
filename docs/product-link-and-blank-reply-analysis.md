# Ürün linki, içeriksiz yanıt ve iki başlık/fiyat hatası: kök neden incelemesi

Bildirilen dört bildirim tek tek incelendi. Kaynak mesajların **gerçek yapısı**
(kalın yazı, yazının altına gizlenmiş hyperlink, yanıt başlığı, kanal
mesajlarındaki inline buton) Telegram'ın herkese açık önizlemesinden
(`t.me/<kanal>/<id>?embed=1`, `t.me/s/<kanal>/<id>`) okundu ve sahte Telegram
nesneleriyle yeniden kuruldu. Hesapla hiçbir kanala bağlanılmadı; testlerin
tamamı çevrimdışıdır (`tests/test_reported_link_and_blank_cases.py`).

Önizlemenin göstermediği iki ayrıntı varsayımdır: `amazonplatinum` butonunun
etiketi bildirimdeki satırdan (`🔗 ✅ Satın Al ✅: …`) alındı; `indirimfirsattr`
bir grup olduğu için butonu önizlemede görünmez (gövde "Aşağıdaki butona tıkla"
der), testlerde aynı adrese giden bir buton olarak modellendi. Sonuç bu
varsayıma bağlı değildir: buton hiç yokken, farklı bir adrese giderken ya da
ürün dışı bir adrese giderken de tek `👉 Ürün Linki 👈` satırı çıkar (testli).

## Örnek örnek sonuç

| # | Kaynak | Görülen | Kök neden | Şimdi |
|---|---|---|---|---|
| 1 | `amazonplatinum/8537` | Altta `🔗 ✅ Satın Al ✅: https://www.amazon.com.tr/dp/…?tag=…&smid=…&th=1&psc=1` | Ürün adresi yalnız inline butonda. `build_link_appendix` buton linkini **etiketi ve ham adresiyle** metne yazıyordu. | `👉 Ürün Linki 👈` (adres yazıya gömülü). Üst satır ve fiyat değişmedi. |
| 2 | `indirimfirsattr/295982` | Üst satır `uzun sürmez ❓` | İki ayrı hata: fiyat ve başlık (aşağıda). | Üst satır `₺99`; slogan başlık olmuyor. |
| 3 | `amazonindirimtr/47090` | Yalnız `Mesajı Gör` + kaynak adından oluşan boş bildirim | İleti, iki mesaj önceki fırsata (`47088`, iPhone Air) **yanıt** olarak atılmış, kendi metni olmayan bir medya. Dahili filtre kapalıyken (`include_enabled: false`) her ileti eşleşir; içerik denetimi yoktu. | Bildirim üretilmez. |
| 4 | `indirimfirsattr/295987` | Üst satır `₺224 — Prime Üyelerine Özel: 1000TL seçili ürün …` | Kampanya satırındaki bitişik `1000TL` **model kodu** sayılıp +3 puan alıyordu (5,5 > ürün adı 4,5). | `₺224 — ONLY Kadın Onlpoptrash-Sally Easy Mw Rib PNT Noos` |

### 2. örnek ayrıntısı

Mesajın ilk satırının tamamı kalın ve ürün adresine gizli hyperlink'tir:

```text
⚡ Sepete 4 adet ekleyip, ödeme ekranında promosyonlar kısmından GUZELL200 kodunu
girin. Kodla adeti 99 TL. Farklı varyantlar için de kodu kullanabilirsiniz.
Link
💥 Bu fiyat uzun sürmez!
#işbirliği #yeniindirim
👇👇 Aşağıdaki butona tıkla, fırsatı kaçırma! 👇👇
```

* **Fiyat (`❓`):** `99 TL.` tutarından sonraki kelime `Farklı` idi. Tutarın hemen
  ardındaki kelime "bu tutar fiyat değil" listesine (`_AFTER_NEG`) **önekle**
  bakılır; `farklı`, listedeki `fark` ile eşleşti ve 99 TL "fark tutarı" diye
  elendi. Oysa arada cümle bitiyor. Düzeltme: tutardan sonra cümle biter
  (`. ! ? …`) ve yeni cümle **büyük harfle** başlarsa sonraki kelime tutarı
  nitelemez. Aynı kök nedenle `💰 349 TL. Kargo ücretsiz!`, `1.299 TL! Taksit
  imkanı var` gibi satırlar da fiyatsız kalıyordu; onlar da düzeldi.
  Temkinli sınırlar: `99 TL Farklı satıcıda` (cümle sonu yok), `500 TL. üzeri`
  (küçük harf), `99 TL.Farklı` (boşluk yok) eskisi gibi elenir; tek başına cümle
  açamayan edatlar noktadan sonra da tutara bağlı sayılır
  (`100 TL. Üzeri alışverişlerde …`, `500 TL. Değerinde hediye çeki` eşik/çek
  tutarıdır).
* **Başlık (`uzun sürmez`):** Mesajda **ürün adı yok**. Kupon cümlesi başlık
  adayı değildir; geriye kalan tek jenerik olmayan satır slogandı. `Bu fiyat
  uzun sürmez`, `Stoklar tükenmeden …`, `Aşağıdaki butona …` şablon sloganları
  jenerik başlık listesine eklendi. Ürün adı olmadığı için üst satır yalnız
  fiyatı taşır (`₺99`), arama düğmesi üretilmez ve tekrar anahtarı boş kalır:
  aynı şablonu taşıyan farklı ürünler slogan yüzünden tek mesajda
  birleştirilmez. Ürün adı yalnız görselde ve linkin ardında olduğundan
  çevrimdışı çıkarılamaz; uydurma ad üretilmez.

### 4. örnek ayrıntısı

Ürün adı ilk satırdadır (kalın + gizli hyperlink) ve doğru adaydır; sorun
puanlamadaydı. `_MODEL_RE` harf-rakam karışımını model kodu sayar (`X1504VA`).
`1000TL` de bu kalıba uyduğu için kampanya satırı ürün adını geçti. Düzeltme:
model kodu aranmadan önce para tutarları satırdan çıkarılır. Gerçek model
kodları etkilenmez; `Steam 100TL Cüzdan Kartı` gibi adı tutar içeren ürünler de
başlık olmaya devam eder (yalnız +3 "model" primi verilmez).

## Ürün linki: her grupta aynı görünüm

Kaynaklar ürün adresini dört farklı yere koyuyor (bildirilen örnekler,
önizlemeleri okunan kanallar ve `tests/fixtures/golden/corpus.json` içindeki
dondurulmuş örnek mesajlar):

| Düzen | Örnek | Adres nerede |
|---|---|---|
| Inline buton | `amazonplatinum` (`✅ Satın Al ✅`), `amazonindirimtr` (`👉 👉 FIRSATA GİT 👈 👈`) | yalnız butonda |
| Gizli hyperlink | `indirimfirsattr` (ilk satırın tamamı); corpus'ta `🔗 FIRSATA GİT!`, `🔗 Amazon'da gör` | yazının altında |
| Açık URL | corpus'ta `🛍 https://app.hb.biz/…`, `🛒 https://link.amazon/…` | metinde |
| Önizleme | link kartı olan düz paylaşımlar | yalnız önizlemede |

Bu yüzden tek bir kaynağa özel kural yerine her düzeni kapsayan tek bir kural
yazıldı. Eski düzende buton satırı kaynağın etiketini de taşıyordu; örneğin
`amazonindirimtr` için `🔗 👉 👉 FIRSATA GİT 👈 👈: https://fenom.io/…` yazılıyordu.
Adres hem butonda hem gizli hyperlink'te olsa da tek satır üretilir.

Kural (`product_links`, `is_product_link`, `is_search_link`):

1. Ürüne gidebilen **her buton** bir satırdır (tek ise `👉 Ürün Linki 👈`, birden
   fazlaysa `👉 Ürün Linki 1 👈`, `👉 Ürün Linki 2 👈` …, en çok 4).
2. Buton yoksa mesajdaki **ilk** ürün bağlantısı (gizli hyperlink → önizleme →
   açık URL) tek satır olur.
3. Ürün linki sayılmayanlar: Google (web ve Alışveriş), Akakçe, Cimri aramaları;
   sohbet/sosyal ağ/topluluk adresleri ve çekiliş formları (`NON_PRODUCT_HOSTS`);
   yolu olmayan mağaza ana sayfası (yalnız izleme etiketi taşısa da); doğrudan
   görsel/video dosyası. Mağaza/kısaltıcı alan adları sınırsız olduğu için beyaz
   liste tutulmaz. Ayrıntı: aşağıdaki "QA" bölümü.
4. Kaynağın arama butonları bot bildiriminde link satırı olmaz (kaynakların
   `Akakçe'de ara` butonları eskiden `🔗 …: https://…` satırı oluyordu); klavyede
   arama düğmesi olarak kalırlar. Klavyesi olmayan hesap kopyasında satır olarak
   yazılırlar. Gövdedeki gizli arama linki (1. örnekteki `🔎 Google`) `smart`
   modunda zaten listelenmez, alıntıda tıklanabilir kalır.

Güvenlik payları:

* **Alıntı yedektir.** Gövdedeki özgün hyperlink/URL'ye dokunulmaz; ürün linki
  yanlış seçilse bile doğru adres alıntıda ve `🔗 Mesajı Gör` altında durur.
* **Bot bildiriminde buton kaybolmaz.** Kaynak butonu klavyeden yalnız adresi
  metinde (görünür ya da gömülü) taşındıysa kaldırılır. Ek satırı uzunluk sınırında
  düşerse ya da `link_appendix: "off"` ise buton klavyede kalır.
* **`all` ve `off` aynen korunur.** `all` ham URL dökümünü (`🔗 etiket: adres`),
  `off` eksiz düzeni sürdürür; ikisinde de ürün satırı üretilmez.
* **Biçimsiz son çare iletisi** (`link` yolu, entity gönderilmez) adresi açık
  yazar: `👉 Ürün Linki 👈: https://…`.
* Ürün olmayan buton linki (ör. kanal daveti) eski `🔗 etiket: adres` biçimiyle
  yazılır.
* **Satırın kendisi ürün adı değildir.** Yeniden başlatmada hedef gruptaki eski
  bildirimler taranırken `👉 Ürün Linki 👈` satırı (`Mesajı Gör` gibi) başlık
  adayı sayılmaz; yalnız görsel + link satırından oluşan bildirim tekrar kaydı
  açmaz, gerçek başlıklı bildirim eskisi gibi birleşir.

### Bu çalışmada bulunan ilgili hata: 📌 notu ve UTF-16

Tekrar notu (`📌 N kere paylaşıldı`) işlenirken entity konumları yeniden
hesaplanır. Ekleme noktası **karakter** sayısıyla, entity konumları ise
Telegram'ın birimi olan **UTF-16** ile ölçülüyordu. Emoji iki birim olduğu için
çok emojili mesajlarda gövdenin sonundaki entity'ler not kadar kayıyordu: alıntı
`Mesajı Gör` satırını yutuyor, kalın kaynak adı nota taşıyor, yeni ürün linki ise
etiketinden kopup yandaki metne geçiyordu (mesajdaki emoji sayısı, etiketten
mesaj sonuna kalan uzunluğu aştığında; pratikte onlarca emojili mesajlarda).
Hata bu değişiklikten önce de vardı (alıntı ve kalın yazı için), ürün linkiyle
görünür hâle geldi. Ekleme noktası artık UTF-16 ile verilir
(`_dedup_apply_badge`); bağlantı her zaman etiketinde kalır.

## QA: alan adı kuralı, çok linkli mesajlar, Bot API gövdesi

PR'ın ilk hâli (`94de953`) üç başlıkta yeniden sınandı. Karşılaştırma ölçütü
"erişilebilirlik"tir: temizlenmiş kaynak mesajın her bağlantısı bildirimde ya
görünür URL, ya gizli hyperlink (`text_link`), ya da (yalnız bot bildiriminde)
inline klavye düğmesi olarak açılabilmelidir.

### 1. `is_product_link` fazla izin veriyordu

120 adreslik tabloda 41 yanlış sınıflandırma çıktı; hepsi "ürün değil"in ürün
sayılmasıydı. Alan adı **eşleştirmesi** doğruydu (büyük harf, sondaki nokta, port,
kullanıcı bilgisi, alt alan adı eleniyor; `netflix.com`, `mytiktok.com`,
`x.com.magaza.example`, `x.com@magaza.example` gibi benzerler ürün kalıyordu).
Kök neden iki eksikti:

* **Liste dardı.** Discord, LinkedIn, Pinterest, Reddit, Threads, Twitch, Snapchat,
  Tumblr, VK, Bluesky, BiP, Linktree, `fb.com`/`fb.me`/`m.me` ve çekiliş formları
  (`forms.gle`, `docs.google.com`) listede yoktu; tek butonu topluluk daveti olan
  mesajda davet `👉 Ürün Linki 👈` oluyordu.
* **"Sorgusu olan her adres ürün olabilir" kuralı.** `https://www.amazon.com.tr/?tag=kanal-21`
  gibi iş ortaklığı etiketli ana sayfa ürün sayılıyordu. Artık yolu olmayan adres
  ancak izleme dışı bir sorgu anahtarı taşıyorsa ürüne gidebilir sayılır
  (`TRACKING_QUERY_KEYS`; fail-open: tanınmayan anahtar ya da değeri adres olan
  parametre içerik sayılır).

Ek bulgu: buton yokken "ilk bağlantı" seçildiği için, kaynak botlarının mesajın
başındaki görünmez karaktere gömdüğü **önizleme görseli** (`…/71abc.jpg`) ürün
linki oluyordu. Doğrudan görsel/video dosyaları artık ürün sayılmaz.

Bilerek dokunulmayanlar: genel kısaltıcılar (`t.co`, `bit.ly`; ürüne de gider) ve
yolu olmayıp tanınmayan anahtar taşıyan adresler (`/?pfx=abc&gclid=…`). Ürün
sayılmayan bağlantı kaybolmaz; `🔗 etiket: adres` satırı olur ya da klavyede kalır.

### 2. Çok linkli mesajlarda kaybolan bağlantılar

İki kök neden bulundu:

* **Arama linkleri hedefe bakılmadan atlanıyordu.** "Arama linki satır olmaz"
  kuralı bot bildirimi için doğruydu (düğme klavyede durur) ama aynı kod klavyesi
  olmayan hesap kopyasında da çalışıyordu: kaynağın arama butonları orada hiçbir
  yerde kalmıyordu. PR öncesinde satır olarak yazılıyorlardı; bu PR'ın getirdiği
  bir gerilemeydi. Etiketi silinen gizli arama linki de aynı nedenle düşüyordu.
* **Sınır elemeden önce uygulanıyordu.** `missing_links` ilk 4 bağlantıyı alıyor,
  ürün satırına taşınanlar ve arama linkleri ondan sonra eleniyordu. Elenen
  bağlantı satır hakkını tüketiyor, arkasındaki kupon/kanal/önizleme linki
  (hesap kopyasında) düşüyordu; örn. 4 ürün butonu + kupon butonunda kupon.

Düzeltme (`link_appendix_lines`, `_compose_build_appendix`, `_compose_fit_appendix`):

* Bir bağlantı yalnız **başka yerde taşınıyorsa** satır olmaz: ürün satırındaki
  adres; bot bildiriminde klavyede duran arama butonu. Eleme sınırdan önce yapılır.
* En çok 4 ürün satırı + 4 `🔗` satırı (+ etiketi silinen linkler için 4). Satır
  alamayan buton bot bildiriminde klavyede kalır; hesap kopyasında 5. ve sonraki
  ürün butonları da 4 `🔗` satırını kullanır (eskiden toplam sınır 4 satırdı).
* Karakter sınırında satırların önceliği vardır. Mesajın ilk 4 gizli bağlantısı
  (PR öncesinde de yazılanlar) gövdeden önce gelir. Eleme sayesinde yer bulan
  sonraki satırlar ve klavyede de durabilen buton satırları **yedeklidir**:
  yalnız gövdeyi kırpmıyorsa yazılır. `🔗` satırları hiç sığmıyorsa onlar düşer,
  ürün satırı kalır (eskiden ek tümüyle düşerdi).
* `all` modu PR öncesindeki ham dökümüne döndü (gövdedeki gizli arama linki de
  listelenir). Hesap kopyasında `all` çıktısı ve her iki hedefte `off` çıktısı
  PR öncesiyle aynıdır (aşağıdaki rastgele kümelerin tamamında birebir). Bot
  bildiriminde `all` modunun farkı: kaynağın arama butonları satır yerine
  klavyede durur ve buton satırları gövdeyi kırpmaz.

Ölçüm (PR öncesi `13ad981` → bu sürüm; "yeni kayıp" = önce açılabilen, şimdi
açılamayan bağlantı):

| Küme | PR'ın ilk hâli | Bu sürüm |
|---|---|---|
| 17 elle kurulmuş çok linkli senaryo × 3 mod × bot/hesap | 32 yeni kayıp (hepsi hesap kopyasında arama butonu) | 0 |
| 3.000 rastgele çok linkli mesaj × 3 mod × bot/hesap × metin/fotoğraf sınırı (36.000 ileti) | 6.572 yeni kayıp | 0 |
| Aynı üreteç, 40–400 karakterlik sorgu ekli adreslerle 1.500 mesaj (18.000 ileti) | 2.966 yeni kayıp | 53 |

Rastgele üreteç bilerek zorlayıcıdır (12 butona kadar, her beş bağlantıdan biri
arama linki, etiketi silinen linkler). Son satırdaki 53 kaybın 49'u, PR öncesi
kodun eki **tümüyle** düşürüp gövdeyi koruduğu iletilerdedir (yeni kod satırları
korur, gövde kısalır); 4'ü `👉 Ürün Linki 👈` satırının kapladığı 18 birim yüzünden
kesme noktasının bir satır öne gelmesidir. Aynı kümede PR öncesine göre 3.107
bağlantı yeniden açılabilir hâle geldi; hiçbir ileti sınırı aşmadı.

### 3. Bot API gövdesi

Kod incelemesinde hata bulunmadı; gövde bu PR'dan önce de aynı yoldan kuruluyordu
(gövdedeki gizli hyperlink'ler de aynı `text_link` yoluyla gider).
Eksik olan, ürün satırının **ağ sınırında** sınanmasıydı. Yeni testler üretimin
`main()` → `notify_offer` → `send_bot_*` yolunu çalıştırıp `urlopen`'a giden
isteği çözer ve şunları doğrular: `sendMessage`/`editMessageText` için `entities`,
`sendPhoto`/`editMessageCaption` için `caption_entities`; ürün satırını tam
kapsayan tek bir `{"type": "text_link", "offset", "length", "url"}`; konumların
UTF-16 birimi olduğu (emojili gövdeden sonra karakter sırasından farklı) ve
yardımcı çift ortasına düşmediği; `parse_mode` gönderilmediği;
`link_preview_options.is_disabled`; klavyede ürün düğmesinin yinelenmediği; 📌
düzenlemesinden sonra bağlantının etiketinde kaldığı.

**Canlı Telegram denemesi yapılmadı** (bu ortamda bot anahtarı, oturum ve deneme
sohbeti yok; `api.telegram.org` erişimi de kapalı). Doğrulanan, Telegram'a giden
istek gövdesidir; Telegram'ın onu nasıl çizdiği değil.

## İçeriksiz yanıt kuralı

`is_blank_reply` yalnız şu üç koşul **birlikte** sağlanınca iletiyi eler:

1. ileti başka bir mesaja **yanıttır** (forum konusunun kök bağlantısı yanıt
   sayılmaz),
2. temizlenmiş metninde tek bir harf ya da rakam yoktur,
3. hiçbir türde bağlantı taşımaz (gizli hyperlink, buton, önizleme, açık URL).

Ayrıca `Yoğun ilgi görüyor` duyurusu kaynağın gerçek yazımıyla
(`Yoğun ilgi görüyor 📣`) da tanınır. `amazonindirimtr` bu sözü önceki bir
fırsata **metinli yanıt** olarak atar (ör. `47061` → `47058`); eski denetim
sondaki emojiyi kaldıramadığı ve biçim entity'si (kalın yazı, özel emoji)
görünce vazgeçtiği için ileti filtreden kaçıyordu. Şimdi kelimeler birebir bu
üçü ise ve ileti görsel, buton ya da herhangi bir bağlantı taşımıyorsa elenir.

Bilerek dar tutuldu (gerçek fırsat kaçmasın):

* Yanıt **olmayan** açıklamasız görsel eskisi gibi iletilir (afiş bir fırsat
  olabilir).
* Tek kelime, fiyat, bağlantı ya da buton taşıyan her yanıt iletilir.
* `GUZELL200 kodu tükendi.` gibi **metinli** bilgi iletileri de iletilir; bunlar
  fırsatın durumunu bildirir ve metinden güvenle "fırsat değil" denemez.

Elenen ileti günlüğe `İçeriksiz mesaj elendi (chat=… id=…)` satırıyla yazılır
ve işlenmiş sayılır (yeniden denenmez).

## Doğrulama

* Yeni testler: `tests/test_reported_link_and_blank_cases.py` (dört örnek, link
  sınıflandırması, UTF-16 konumları, `all`/`off`, sınırda düşen ek, 📌 notu,
  yeniden başlatma taraması, uçtan uca eleme ve fail-open durumları). 23 testin
  22'si düzeltmeler geri alındığında kırılır; kalan biri (ek sınırda düşünce
  kaynak butonunun korunması) zaten var olan güvenceyi sabitler.
* QA testleri: `tests/test_product_link_qa.py` (27 test: alan adı tabloları,
  çok linkli senaryolar, karakter sınırı, rastgele mesajlarda değişmezler, ağ
  sınırında Bot API gövdesi, bot reddedince hesap kopyası). 27 testin 21'i PR'ın
  ilk hâlinde kırılır; kalan 6'sı zaten doğru çalışan davranışı sabitler (benzer
  alan adları, bozuk adresler, kısaltıcılar, bot bildiriminde butonların
  korunması, hesap kopyasında ilk 4 bağlantı, çok butonlu bildirimin Bot API
  gövdesi). Düzeltmelere yapılan 18 ayrı bozma denemesinin (mutasyon) tamamı bu
  testlerce yakalanır.
* Altın referans (testlerden derlenmiş 563 dondurulmuş örnek mesaj):
  **fiyat/başlık katmanında tek değişiklik yok**. İletim katmanlarındaki
  farklar yalnız şunlardır: ek satırının biçimi, ona ait `text_link` entity'si,
  klavyeden kalkan kaynak butonu ve bunlara bağlı özetler. Eski ve yeni çıktı
  ileti ileti karşılaştırıldı (bot bildirimi, 📌 düzenlemesi ve hesap kopyası):
  üst satır, tekrar anahtarı ve istatistikler aynı; eski çıktıdaki her adres
  yeni çıktıda da erişilebilir; her `👉 Ürün Linki 👈` satırı tam olarak bir
  bağlantı taşıyor ve bağlantı tam olarak o satırı kapsıyor; üretilen ham
  `🔗 etiket: adres` ürün satırı kalmadı. Bu karşılaştırma yalnız altın
  referanstaki mesajları kapsıyordu; orada kaynak arama butonu taşıyan hesap
  kopyası yoktur, yukarıdaki gerileme bu yüzden görünmedi. QA düzeltmeleri altın
  referansta **tek bir bayt değiştirmedi**.
* Fiyat/başlık motoru (`price_filter`), değişiklikten önceki depo metinleri
  üzerinde eski sürümle karşılaştırıldı: 563 dondurulmuş örnekte **0 fark**,
  testlerdeki ve belgelerdeki ~4.400 metin parçasında **1 fark** (yalnız slogan
  içeren bir parçada `uzun sürmez` artık başlık olmuyor).

## Bilinen sınırlar

* Ürün adı metinde yoksa (2. örnek) üst satır yalnız fiyattır; ad, görselden ya
  da mağaza sayfasından okunmaz.
* Buton yokken "ilk ürün bağlantısı" seçilir. Mesajın ilk bağlantısı ürün
  dışında bir sayfaysa (ör. mağaza vitrini) ürün linki o olur; doğru adres
  alıntıda yedek olarak durur.
* Bağlantı taşıma sınırları (PR öncesinde de vardı): `smart` modunda bot
  bildirimi önizleme kartının adresini yalnız ürün linki seçildiğinde taşır;
  klavyesiz hesap kopyası en çok 4 ürün + 4 `🔗` satırı taşır ve `off` modunda
  buton linki taşımaz; sınırı aşan gövde kırpılırsa kırpılan kısımdaki gizli ve
  açık bağlantılar yalnız `🔗 Mesajı Gör` üzerinden açılır.
* Canlı Telegram'da tıklanabilirlik denenmedi (yukarıda, QA 3. başlık).
* Çok uzun medya açıklamalarında ürün satırına yer açmak için alıntı 18 birim
  daha erken kesilir (alıntı zaten kesilmiştir; tamamı `Mesajı Gör` altındadır).
* Cümle sonu kuralının bedeli: niteleyicinin tutardan **sonraki ayrı cümlede**
  geldiği yazımlar (`250 TL. İndirim!`, `49 TL. Kargo ücreti`) mesajdaki tek
  tutarsa artık fiyat sayılır. Aynı cümledeki yazım (`250 TL indirim`,
  `49 TL kargo`) ve eşik edatları (`üzeri`, `üstü`, `değerinde`) eskisi gibi
  elenir; 563 dondurulmuş örneğin hiçbirinde fiyat değişmedi.
* Metinli bilgi yanıtları (`GUZELL200 kodu tükendi.`) ve yanıt olmayan
  açıklamasız görseller bilerek iletilmeye devam eder (yukarıda).
