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
  yanlış seçilse bile doğru adres alıntıda ve kaynak adına gömülü mesaj linkinde durur.
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
  bildirimler taranırken `👉 Ürün Linki 👈` satırı (eski düzende `Mesajı Gör`) başlık
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
* Karakter sınırında ölçü **ürün satırı olmayan düzendir** (gövde + mesajın ilk 4
  gizli bağlantısının `🔗` satırı + kaynak adı; mesajın adresi artık adın içine
  gömülü olduğu için ayrı `Mesajı Gör` satırı yoktur): o düzende
  açılabilen hiçbir bağlantı kapanmaz. Ayrıntısı aşağıda, "Karakter sınırı"
  başlığındadır.
* `all` modu PR öncesindeki ham dökümüne döndü (gövdedeki gizli arama linki de
  listelenir). Her iki hedefte `off` çıktısı PR öncesiyle birebir aynıdır
  (aşağıdaki kümelerin tamamında). Hesap kopyasında `all` çıktısı da aynıdır; tek
  fark, dört satırın hiçbir şekilde sığmadığı iletilerde artık tam gövdenin
  yanına sığan satırların yazılmasıdır (eskiden hiçbiri yazılmazdı). Bot
  bildiriminde `all` modunun farkı: kaynağın arama butonları satır yerine
  klavyede durur ve buton satırları gövdeyi kırpmaz.

#### Karakter sınırı: yalın düzenin açtığı bağlantı kapanmaz

İkinci commit (`cd33f46`) uzun adresli mesajlarda hâlâ bağlantı kaybediyordu.
Kayıpların dört biçimi vardı; hepsi "PR öncesinde açılabilen bağlantı, yeni
satırlar yüzünden açılamıyor" demektir:

| Biçim | PR öncesi | `cd33f46` |
|---|---|---|
| Ek sığmıyordu | Dört `🔗` satırı gövdesiz de sığmaz; ek tümüyle düşer, gövde kalır | Ürün satırı ve eleme sayesinde kısalan ek sığar, gövdeden önce gelir; gövde kısalır, sonundaki bağlantılar kapanır |
| Yalnız ürün satırı | Ek düşmüş, gövde kalan yere kırpılmış | 16–18 birimlik ürün satırı kırpma noktasını öne çeker; tam orada biten bağlantı kapanır |
| Ürün satırı + `🔗` satırları | Dört `🔗` satırı yazılır, gövde kırpılır | İlk dört bağlantı arasında olmayan ürün butonlarının satırları eklenir; kırpma noktası 2–29 birim öne gelir |
| Sınırı dolduran satırlar | Dört uzun `🔗` satırı 14–25 birimlik gövdeyle sığar | Ürün satırlarıyla birlikte gövdesiz de sığmaz; `🔗` satırlarının hepsi düşer (klavyesiz kopyada tek taşıyıcıydılar) |

Kural (`_compose_fit_appendix`): yalın düzenin açtığı hiçbir bağlantı kapanmaz.
Bunun içinde öncelik sırası şudur:

1. **Kaynak mesajın metni ve içindeki bağlantılar.** Gövdenin tamamı sığmıyorsa
   fazladan `🔗` satırları sondan başlayarak bırakılır; bunlar gövdeyi hiç
   kırpmaz. Fazladan satır şunlardır: yalın düzende olmayan satırlar (ilk dört
   bağlantıdan sonrakiler), bot bildiriminde klavyede de duran buton satırları
   ve yalın düzen eki hiç yazamıyorsa satırların tamamı. Önce klavyede de duran
   ya da sonradan eklenen satırlar, en son tek taşıyıcı satırlar bırakılır.
2. **`👉 Ürün Linki 👈` satırları.** Yer yoksa gövde *metninin* sonundan yer
   alırlar; gövdedeki bir bağlantıyı ise kesemezler. Ölçü ürün satırsız düzendir:
   ürün satırları hiç yazılmasaydı gövdeden açılacak her bağlantı açık kalır
   (adresini ekin kendisi taşımıyorsa). Bu düzen yalın düzenden dar değildir (bot
   bildiriminde buton satırları klavyeye bırakıldığı için daha geniştir);
   dolayısıyla yalın düzenin açtığı bağlantı da kapanmaz. Bir bağlantıyı kesecek
   ya da yalın düzenin satırlarına yer bırakmayacak ürün satırı sondan başlayarak
   yazılmaz. Bot bildiriminde o ürünün butonu klavyede kalır; hesap kopyasında
   ilk dört bağlantıdan biriyse `🔗` satırı olur.
3. **Kaynak adı** (ve içine gömülü mesaj adresi) ek uğruna bırakılmaz. Ad ve üst
   satır kısadır (ürün adı en çok 200 karakter); en dar sınırda bile ürün
   satırlarıyla aynı iletiye sığarlar, aralarında seçim yapmak gerekmez. Ad hiç
   yazılamazsa mesajın adresi `🔗 Mesajı Gör: <t.me>` satırı olarak geri gelir;
   ölçümde bu satıra hiç düşülmedi.
4. **Diğer `🔗` satırları** en önce bırakılanlardır (1. madde). Tek istisna, yalın
   düzenin *yazabildiği* satırlardan başka taşıyıcısı olmayanlardır: klavyesiz
   hesap kopyasında hepsi; bot bildiriminde klavyede durmayanlar (`all` modunun
   gizli linkleri ve önizleme adresi, etiketi silinen linkler). Bunlar eskisi gibi
   gövdeden önce gelir; bırakılsalardı PR öncesinde açılan bağlantı kapanırdı.

Ölçüm `tests/link_reach_audit.py` ile yapılır: iki sürümü aynı tohumlu mesajlarla
çalıştırıp ileti ileti karşılaştırır ("yeni kayıp" = PR öncesi `13ad981`
çıktısında açılabilen, karşılaştırılan sürümde açılamayan bağlantı).

```
python tests/link_reach_audit.py --cells            # PR öncesi ile çalışma ağacı, ≈2 dk
python tests/link_reach_audit.py --base cd33f46     # başka bir sürümle karşılaştır
python tests/link_reach_audit.py --seed 7 --show 3  # başka tohum; kayıp örneklerini yazdır
```

| Küme | PR'ın ilk hâli `94de953` | İkinci commit `cd33f46` | Bu sürüm |
|---|---|---|---|
| 17 elle kurulmuş çok linkli senaryo × 3 mod × bot/hesap (102 ileti) | 28 | 0 | **0** |
| 3.000 rastgele çok linkli mesaj × 3 mod × bot/hesap × metin/fotoğraf sınırı (36.000 ileti) | 8.284 | 5 | **0** |
| Aynı üreteç, 40–400 karakterlik sorgu ekli adreslerle 1.500 mesaj (18.000 ileti) | 3.804 | 550 | **0** |

Bu sürümde üç kümenin hiçbir iletisi sınırı aşmaz, bot bildiriminde hiçbir buton
kaybolmaz, mesajın adresi her iletide kaynak adının içinde durur. Kural geliştirilirken hiç
kullanılmayan iki tohumla (`--seed 7`, `--seed 424242`; 108.204 ileti daha) sonuç
yine 0 / 0 / 0'dır. PR öncesine göre yeniden açılabilir hâle gelen bağlantı: 15 /
4.617 / 4.481 (açılabilen bağlantı 353 → 368, 175.366 → 179.983, 79.234 → 83.715).

Bu tablo ikinci commit'le birlikte bildirilen 0 / 0 / 53 ile **doğrudan
karşılaştırılamaz**: o ölçümün üreteci saklanmamıştı. Betik yeniden yazıldı ve
depoya eklendi; üreteci eskisinden zorlayıcıdır (gizli linkler gövdenin her
yerine dağılır, görünür `Link: …` satırları, 90 satıra varan gövdeler, 12 butona
kadar). Aynı ikinci commit yeni betikte 0 / 5 / 550 verir.

Bedel, ikinci commit'e göre (aynı kümeler):

* 17 senaryonun 102 iletisi ve rastgele kümenin 36.000 iletisinden 35.938'i
  ikinci commit'le aynıdır (ek satırları, gövde uzunluğu, klavye, açılabilen
  adresler). Farklı 62 iletinin 5'i yukarıdaki 5 kaybın düzeldiği iletilerdir
  (hepsi hesap kopyası, fotoğraf sınırı): gövdedeki bağlantı geri gelir,
  karşılığında ikinci commit'in ürün satırıyla açtığı 4 bağlantı açılmaz (PR
  öncesinde de açılmıyorlardı). Kalan 57'si bot bildirimidir: ürün satırı
  gövdedeki bir bağlantıyı keseceği için yazılmaz, butonu klavyede kalır; 56
  bağlantı açılır, hiçbiri kapanmaz.
* Uzun adresli kümede 18.000 iletinin 16.667'si aynıdır. İkinci commit'in açtığı
  1.086 bağlantı (645 ileti) artık açılmaz: 1.025'i gövdeyi kırparak yazılan
  `🔗` satırı, 61'i ürün satırıdır; hiçbiri PR öncesinde de açılamıyordu.
  Karşılığında 861 bağlantı açılır (550'si yukarıdaki kayıplar), 830 iletide
  gövde uzar (3 iletide kısalır; son maddedeki çatışma). Açılabilen bağlantı
  toplamı 83.940 → 83.715 olur (PR öncesi 79.234): sıra gereği gövde, fazladan
  satırdan önce gelir.
* Ürün satırı uzun kümenin 6.000 `smart` iletisinin 68'inde (40'ında hiç
  kalmaz), rastgele kümenin 12.000 `smart` iletisinin 62'sinde (25'inde hiç
  kalmaz) azalır; toplam 10.844 → 10.713 ve 20.840 → 20.727 satır. 127'sinde
  neden, satırın gövdedeki bir bağlantıyı kesecek olmasıdır: 96'sı bot
  bildirimi, 31'i hesap kopyasıdır. Kalan 3'ünde (hepsi uzun kümede, hesap
  kopyası, fotoğraf sınırı) yalın düzenin dört uzun `🔗` satırı ürün satırına
  yer bırakmaz; burada "sıfır yeni kayıp" hedefi ile "ürün satırı `🔗`
  satırından önce gelir" sırası çatışır ve hedef kazanır (ileti PR öncesindeki
  gibidir). Bot bildiriminde yazılmayan ürün satırının bağlantısı 96 iletinin
  95'inde açık kalır (butonu klavyededir ya da bağlantı gövdededir); kalan 1
  iletide ürün bağlantısı buton değil, gövdenin kırpılan kısmındaki gizli
  bağlantıdır. O adres ve hesap kopyasında yazılmayan ürün satırlarının
  adresleri PR öncesinde de açılamıyordu.

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
* QA testleri: `tests/test_product_link_qa.py` (40 test: alan adı tabloları,
  çok linkli senaryolar, karakter sınırı, rastgele mesajlarda değişmezler, ağ
  sınırında Bot API gövdesi, bot reddedince hesap kopyası). İlk 27 testin 21'i
  PR'ın ilk hâlinde kırılır; kalan 6'sı zaten doğru çalışan davranışı sabitler
  (benzer alan adları, bozuk adresler, kısaltıcılar, bot bildiriminde butonların
  korunması, hesap kopyasında ilk 4 bağlantı, çok butonlu bildirimin Bot API
  gövdesi). Düzeltmelere yapılan 18 ayrı bozma denemesinin (mutasyon) tamamı bu
  testlerce yakalanır.
* Karakter sınırı kuralının 13 testi aynı dosyadadır: 12'si `LimitPressureTest`
  içindedir; biri (`BotApiWireTest`) sınırda yazılmayan ürün ve `🔗` satırlarının
  butonlarını Telegram'a giden isteğin klavyesinde arar. Yalın düzen testte
  bağımsız olarak yeniden kurulur (`plain_layout`); bu model, denetim betiğinin
  54.102 iletisinin tamamında gerçek `13ad981` çıktısıyla aynı adresleri verir.
  Böylece "yalın düzenin açtığı bağlantı kapanmaz" değişmezi git geçmişi
  gerekmeden CI'da da sınanır (uzun adresli kümenin ilk 120 mesajı, 1.440
  ileti). 13 testin 9'u ikinci commit'te kırılır; kalan 4'ü aşırı düzeltmeye
  karşıdır (ürün satırı gövde metninden yer alabilmeli, kendi taşıdığı ya da
  zaten yarım kalan adres yüzünden geri çekilmemeli, tek taşıyıcı satır en son
  bırakılmalı). Kuralın 22 ayrı bozma denemesinin tamamı yakalanır.
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
  açık bağlantılar yalnız kaynak adına gömülü mesaj linki üzerinden açılır.
* Canlı Telegram'da tıklanabilirlik denenmedi (yukarıda, QA 3. başlık).
* Çok uzun medya açıklamalarında ürün satırına yer açmak için alıntı satır
  başına 17–18 birim daha erken kesilir (alıntı zaten kesilmiştir; tamamı
  gömülü mesaj linki altındadır). Kesilecek kısımda bir bağlantı varsa ürün satırı
  yazılmaz (bot bildiriminde butonu klavyede kalır); yalnız metin kısalır,
  bağlantı kapanmaz. Ölçümde gövde, PR öncesine göre rastgele kümenin 36.000
  iletisinin 636'sında ve uzun adresli kümenin 18.000 iletisinin 927'sinde
  kısadır (ortanca 17–19, en çok 79 birim); 3.492 ve 1.543 iletide ise uzundur
  (ham `🔗 etiket: adres` satırının yerini 16 birimlik ürün satırı aldığı için).
* "Sıfır yeni kayıp" bağlantı sayar, metin saymaz: PR öncesi düzen gövdeyi dört
  uzun `🔗` satırı uğruna birkaç birime indiriyorsa bu sürüm de öyle yapar
  (o satırlar klavyesiz kopyada tek taşıyıcıdır) ve kalan yer ürün satırına
  yetmiyorsa ürün satırı yazılmaz. Ölçümde 54.000 iletinin 3'ü.
* Bot bildiriminde, kesilecek gövde bağlantısı aynı zamanda klavyede duran bir
  butonun adresiyse ürün satırı yine de geri çekilir (kural, gövde bağlantıları
  için klavyeye güvenmez). Bağlantı kaybı yoktur; yalnız o iletide ürün satırı
  eksik kalır, ürünün butonu klavyededir (ölçümde 54.102 iletinin 5'i, 7 satır).
* Cümle sonu kuralının bedeli: niteleyicinin tutardan **sonraki ayrı cümlede**
  geldiği yazımlar (`250 TL. İndirim!`, `49 TL. Kargo ücreti`) mesajdaki tek
  tutarsa artık fiyat sayılır. Aynı cümledeki yazım (`250 TL indirim`,
  `49 TL kargo`) ve eşik edatları (`üzeri`, `üstü`, `değerinde`) eskisi gibi
  elenir; 563 dondurulmuş örneğin hiçbirinde fiyat değişmedi.
* Metinli bilgi yanıtları (`GUZELL200 kodu tükendi.`) ve yanıt olmayan
  açıklamasız görseller bilerek iletilmeye devam eder (yukarıda).
