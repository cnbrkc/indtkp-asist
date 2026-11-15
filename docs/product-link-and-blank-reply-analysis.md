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
   Telegram, WhatsApp, Instagram, YouTube, X, Facebook, TikTok adresleri; yolu ve
   sorgusu olmayan mağaza ana sayfası. Mağaza/kısaltıcı alan adları sınırsız
   olduğu için beyaz liste tutulmaz.
4. Arama linkleri hiçbir modda link satırı olmaz (1. örnekteki `🔎 Google` ve
   kaynakların `Akakçe'de ara` butonları eskiden `🔗 …: https://…` satırı
   olabiliyordu); bot bildiriminde arama düğmesi olarak kalırlar.

Güvenlik payları:

* **Alıntı yedektir.** Gövdedeki özgün hyperlink/URL'ye dokunulmaz; ürün linki
  yanlış seçilse bile doğru adres alıntıda ve `🔗 Mesajı Gör` altında durur.
* **Link kaybolmaz.** Kaynak butonu klavyeden yalnız adresi metinde (görünür ya
  da gömülü) taşındıysa kaldırılır. Ek satırı uzunluk sınırında düşerse ya da
  `link_appendix: "off"` ise buton klavyede kalır.
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
* Altın referans (testlerden derlenmiş 563 dondurulmuş örnek mesaj):
  **fiyat/başlık katmanında tek değişiklik yok**. İletim katmanlarındaki
  farklar yalnız şunlardır: ek satırının biçimi, ona ait `text_link` entity'si,
  klavyeden kalkan kaynak butonu ve bunlara bağlı özetler. Eski ve yeni çıktı
  ileti ileti karşılaştırıldı (bot bildirimi, 📌 düzenlemesi ve hesap kopyası):
  üst satır, tekrar anahtarı ve istatistikler aynı; eski çıktıdaki her adres
  yeni çıktıda da erişilebilir; her `👉 Ürün Linki 👈` satırı tam olarak bir
  bağlantı taşıyor ve bağlantı tam olarak o satırı kapsıyor; üretilen ham
  `🔗 etiket: adres` ürün satırı kalmadı.
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
* Çok uzun medya açıklamalarında ürün satırına yer açmak için alıntı 18 birim
  daha erken kesilir (alıntı zaten kesilmiştir; tamamı `Mesajı Gör` altındadır).
* Cümle sonu kuralının bedeli: niteleyicinin tutardan **sonraki ayrı cümlede**
  geldiği yazımlar (`250 TL. İndirim!`, `49 TL. Kargo ücreti`) mesajdaki tek
  tutarsa artık fiyat sayılır. Aynı cümledeki yazım (`250 TL indirim`,
  `49 TL kargo`) ve eşik edatları (`üzeri`, `üstü`, `değerinde`) eskisi gibi
  elenir; 563 dondurulmuş örneğin hiçbirinde fiyat değişmedi.
* Metinli bilgi yanıtları (`GUZELL200 kodu tükendi.`) ve yanıt olmayan
  açıklamasız görseller bilerek iletilmeye devam eder (yukarıda).
