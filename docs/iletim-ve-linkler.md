# İletim zinciri, gizli bağlantılar ve fiyat arama düğmeleri

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
- `/durum` komutu hangi yolun kaç kez işe yaradığını gösterir:
  `İletim sırası: forward → copy → media → text → link | kullanılan: forward×120, media×7`

---

## 8. Gizli bağlantılar ve fiyat arama düğmeleri

İndirim kanalları ürün linkini çoğu zaman açıkça yazmaz; üç yere gizler:

| Gizleme yolu | Örnek | Bot ne yapar |
|---|---|---|
| Metin altına gizlenmiş hyperlink | "**Fırsata Git**" yazısı ya da ürün adının kendisi görünür, link altındadır | Mesaj olduğu gibi iletildiği için link gövdede tıklanabilir kalır (yedek); adres ayrıca `👉 Ürün Linki 👈` satırına gömülür |
| Inline buton | Yazıda link yok, butondadır (`✅ Satın Al ✅`) | Adres `👉 Ürün Linki 👈` yazısına gömülür; bot, adres metinde taşındıysa fazladan ürün düğmesini kaldırır, taşınamadıysa korur |
| Açık URL | `Link: https://ty.gl/…` | Alıntıda aynen kalır; aynı adres `👉 Ürün Linki 👈` satırına da gömülür |
| Link önizlemesi | Metinde link yok, önizleme kartı var | Mesajda başka ürün bağlantısı yoksa önizleme hedefi `👉 Ürün Linki 👈` olur |

### Ürün linki her grupta aynı görünür: `👉 Ürün Linki 👈`

Eskiden buton linki alta `🔗 ✅ Satın Al ✅: https://www.amazon.com.tr/dp/…?tag=…` diye,
etiketi ve upuzun adresiyle yazılırdı. Artık ürün adresi **tek ve sabit bir yazının
içine gömülür**; URL metinde görünmez, yazıya dokununca açılır:

- **Hangi bağlantı?** Buton kaynağın bilinçli "ürüne git" çağrısıdır: ürüne gidebilen
  her buton listelenir (tek ise `👉 Ürün Linki 👈`, birden fazlaysa kaynak sırasıyla
  `👉 Ürün Linki 1 👈`, `👉 Ürün Linki 2 👈` …; en çok 4). Buton yoksa mesajdaki **ilk**
  ürün bağlantısı (gizli hyperlink → önizleme → açık URL) tek satır olur.
- **Ne ürün linki sayılmaz?**
  - Google (web/Alışveriş), Akakçe ve Cimri aramaları;
  - sohbet, sosyal ağ ve topluluk adresleri: Telegram, WhatsApp, Instagram, YouTube,
    X/Twitter, Facebook (fb.com, fb.me, m.me dahil), TikTok, Discord, LinkedIn, Pinterest
    (ülke alan adlarıyla), Reddit, Threads, Twitch, Snapchat, Tumblr, VK, Bluesky, BiP,
    Linktree; çekiliş/anket formları (forms.gle, docs.google.com);
  - yolu olmayan mağaza ana sayfası — yalnız iş ortaklığı/izleme etiketi taşısa da
    (`https://www.amazon.com.tr/?tag=kanal-21`, `…/?utm_source=telegram`). Tanınmayan
    tek bir sorgu anahtarı (`?p=123`, `?url=https://…`) varsa adres ürüne gidebilir
    sayılır;
  - doğrudan görsel/video dosyası (`…/urun.jpg`): kaynak botlarının mesajın başındaki
    görünmez karaktere gömdüğü önizleme görseli "ilk bağlantı" olup ürün linki sanılmaz.

  Alan adı tam ya da alt alan adı olarak eşleşir; büyük/küçük harf, sondaki nokta, port
  fark etmez: `m.facebook.com` elenir, `netflix.com` (x.com'la biter) ya da
  `x.com.magaza.example` elenmez. Mağaza/kısaltıcı alan adları sınırsız olduğu için
  (amzn.to, ty.gl, app.hb.biz, fenom.io, t.co, bit.ly …) beyaz liste tutulmaz; yalnız bu
  kesin "ürün değil" adresler elenir. Ürün sayılmayan bağlantı **kaybolmaz**: eski
  `🔗 etiket: adres` biçimiyle yazılır ya da klavyede düğme olarak kalır.
- **Kaynağın arama butonları.** Kaynaktaki Google/Akakçe/Cimri butonu bot bildiriminde
  link satırı olmaz; klavyede arama düğmesi olarak durur ve satır hakkı tüketmez.
  Kullanıcı hesabı klavye gönderemediği için **hesap kopyasında** (bot gönderemediğinde
  atılan yedek) bu butonlar `🔗 etiket: adres` satırı olarak yazılır. Gövdedeki gizli
  arama linki alıntıda tıklanabilir kalır; `all` modunda eskisi gibi ayrıca listelenir.
- **Kaç satır?** En çok 4 ürün satırı ve ayrıca en çok 4 `🔗` satırı yazılır (etiketi
  silinen gizli linkler için 4 satır daha). Başka yerde taşınan adres — ürün satırı,
  klavyedeki arama düğmesi — bu hakkı tüketmez; arkasındaki kupon/kanal linki düşmez.
  Satır alamayan buton bot bildiriminde klavyede kalır (5. ve sonraki ürün butonu da).
  Klavyesiz hesap kopyasında 5. ve sonraki ürün butonları da bu 4 `🔗` satırını
  kullanır; daha fazlası taşınamaz (eskiden toplam sınır 4 satırdı).
- **Karakter sınırı** (fotoğraf açıklaması 1024, metin 4096). Ölçü, **ürün satırı
  olmayan** düzendir (üst satır + gövde + mesajın ilk 4 gizli bağlantısının `🔗`
  satırı + grup adı): **o düzende açılabilen hiçbir bağlantı kapanmaz.** Mesaj
  adresi artık ada gömülü olduğu için `🔗 Mesajı Gör` satırı metinden kalktı, ama
  **bütçesi sabit tutulur**: boşalan yer fazladan `🔗` satırlarına verilseydi o
  satırlar gövdenin sonundan yer alıp gövdedeki bağlantıyı kapatırdı (istek:
  bildirim yalnız **kısalsın**, gerisi değişmesin). Bu yüzden kırpma noktası ve
  satır seçimi eskisiyle aynıdır; ileti bir satır kısalır, gövde hiçbir zaman
  eskisinden kısa olmaz. Yalnız ileti **her şeyiyle sığıyorsa** hiçbir satır
  bırakılmaz (ayrılmış yer sığan iletiyi kısaltmaz). Bunun içinde öncelik sırası:
  1. Kaynak mesajın metni ve içindeki bağlantılar. Gövdenin tamamı sığmıyorsa fazladan
     `🔗` satırları sondan başlayarak yazılmaz; gövdeyi hiç kırpmazlar. Fazladan satır:
     ilk 4 bağlantıdan sonrakiler, bot bildiriminde klavyede de duran buton satırları
     ve yalın düzen eki hiç sığdıramıyorsa satırların tamamı.
  2. `👉 Ürün Linki 👈` satırları. Yer yoksa gövde metninin sonundan yer alırlar; ama
     yazılmasalardı gövdeden açılacak bir bağlantıyı kesecekse ya da yalın düzenin
     satırlarına yer bırakmayacaksa sondan başlayarak yazılmazlar (bot bildiriminde
     butonları klavyede kalır).
  3. Grup adı (ve ona gömülü mesaj adresi); ek uğruna bırakılmaz. Ad yazılamazsa
     adres `🔗 Mesajı Gör: <url>` satırı olarak geri gelir — link kaybolmaz.
  4. Diğer `🔗` satırları ilk bırakılanlardır. Tek istisna, yalın düzenin yazabildiği
     ilk 4 bağlantıdan başka taşıyıcısı olmayanların satırıdır (klavyesiz hesap
     kopyasında hepsi; bot bildiriminde klavyede durmayanlar): eskisi gibi gövdeden
     önce gelirler, yoksa önceki düzende açılan bağlantı kapanırdı.

  Bot bildiriminde satırı yazılmayan her buton klavyede kalır. Ölçüm ve yöntemi:
  [analiz belgesi](archive/product-link-and-blank-reply-analysis.md),
  `python tests/link_reach_audit.py --base 370b5be` (54.102 ileti: 3 kümede de
  `yeni kayıp 0`, `sınırı aşan 0`, `botta kayıp buton 0`, `adres kaybı 0`,
  `gövdesi kısalan 0`; açılabilen bağlantı 179.983 → 180.280 ve 83.715 → 83.951).
  Değişiklik commit edildikten sonra `--base HEAD` **kullanılmaz**: yeni sürümün
  kendisiyle karşılaştırır ve zorunlu olarak 0 verir.
- **Alıntı yedektir.** Gövdedeki özgün hyperlink/URL değişmez. Ürün linki yanlış
  seçilse bile doğru adres alıntıda ve grup adına gömülü mesaj linkinde durur.
- **Bot bildiriminde hiçbir buton kaybolmaz** (üç modda da): adresi metinde görünür ya
  da gömülü taşınmayan her kaynak butonu klavyede kalır — ek satırı sınıra sığmayıp
  düşse ya da `link_appendix: "off"` olsa bile. Bilinen sınırlar: `smart` modunda bot
  bildirimi önizleme kartının adresini yalnız ürün linki seçildiğinde taşır (hesap
  kopyası onu `🔗 başlık: adres` satırı olarak yazar); hesap kopyası `off` modunda
  buton linki taşımaz; sınırı aşan gövde kırpılırsa kırpılan kısımdaki bağlantılar
  grup adına gömülü mesaj linki üzerinden açılır.
- Yazıya gömülü adres bot bildiriminde, hesap kopyasında, tekrar (📌) notu işlenirken
  ve özel (DM) kopyada aynı konumda korunur. Yalnız biçimsiz giden son çare iletisinde
  (`link` yolu) adres açıkça yazılır: `👉 Ürün Linki 👈: https://…`.

Bildirim botu, kaynakta aynı arama hizmetine ait bir URL/buton yoksa eksik düğmeleri şu
sırayla ekler:

1. **Google Alışveriş** — `https://www.google.com/search?udm=28&q=<ürün>&hl=tr&gl=tr`
2. **Akakçe'de ara** — `https://www.akakce.com/arama/?q=<ürün>`
3. **Cimri'de ara** — `https://www.cimri.com/arama?sort=price,asc&q=<ürün başlığının tamamı>`

Cimri arama sonuçlarında gözlenen kalıp `/arama` yolu, `q` sorgu parametresi ve
`sort=price,asc` fiyat sıralamasıdır. Örnek:
`https://www.cimri.com/arama?sort=price,asc&q=Arzum+AR5106+Volume+Pro`. Sorgudaki Türkçe
karakterler ve özel karakterler URL-encode edilir.

**Cimri sorgusu ürün başlığının tamamıdır.** Daha önce uzun sorguların Cloudflare WAF
blokunu tetiklediği sanılıyor ve sorgu ilk iki kelimeye indiriliyordu. Denemede sorunun
**Telegram'ın uygulama içi tarayıcısında** olduğu, aynı adres dış tarayıcıda açılınca
tam sorguyla arama yaptığı görüldü (kullanıcı isteği: "ilk iki kelimeyi aramayı bıraksın,
tüm ürün başlığını arasın"). Kısaltma kaldırıldı; üç düğme de tam sorgu kullanır.
Sayfa yine açılmazsa bağlantıyı Telegram'ın içi tarayıcısı yerine normal tarayıcıda açmak
en pratik çözümdür.

### Kaynak formatları farklı olsa da sorgu ürün adından üretilir

Kaynakların mesaj düzeni standart değil; başta fiyat, indirim oranı veya başlık bulunabilir.
Arama sorgusu için bot mesajı satır satır, **yerel ve anlık** olarak temizler:

- Tek başına fiyat/para birimi satırları (`1.299 TL`, `₺7.499`) ve indirim yüzdesi/
  kampanya satırları (`%50 indirim`, `İndirim oranı %30`) sorgu başlığı sayılmaz.
- Ürünle aynı satırdaki fiyat ve indirim bilgileri de sorgudan çıkarılır.
- İlk kullanılabilir ürün adı satırı seçilir; örneğin
  `1.299 TL` → `%50 indirim` → `🛍️ Philips Airfryer XXL 6.2L` → ürün linki
  düzeninde üç arama da `Philips Airfryer XXL 6.2L` ile açılır.
- Baştaki emoji/semboller ve görünür ürün URL'leri sorguya katılmaz. Ürün adı bulunamazsa
  yapay/boş arama düğmesi eklenmez.
- Bildirim üst satırındaki `₺56 — ` (veya `❓ — `) ön eki **asla sorguya girmez**:
  arama kaynak mesajdan üretilir; ayrıca `_price_search_url` tek nokta sigortası
  olarak kendisine üst satır verilirerse tutarı, para birimini, ayracı ve `❓`
  işaretini düşürür (`header_search_title`), geriye salt ürün adı kalır. Ayraç
  düz tire olduğu için (` - `) içinde tire geçen ürün adları da doğru ayrıştırılır;
  ayracın solu fiyat gibi görünmüyorsa satırın tamamı ad sayılır. Böylece fiyatı
  üste almak Google/Akakçe/Cimri aramalarını bozmaz.

Bu ayıklama **yalnızca arama düğmelerindeki sorguyu** etkiler: gönderilen mesaj gövdesi,
fiyat, indirim oranı, biçimlendirme ve kaynak linkleri olduğu gibi kalır. Algoritma ağ
isteği yapmadığı için bildirim yoluna ek gecikme getirmez.

Kaynakta zaten Google Shopping/Akakçe/Cimri linki varsa aynı hizmet için yeni düğme
çoğaltılmaz. Kaynaktaki ürün URL'si gövdede taşınamazsa butonu korunur; tanınan arama butonları da URL'leri
bozulmadan standart Google → Akakçe → Cimri sırasına alınır. Eski bir kaynaktan gelen
`marketfiyati.org.tr` linki de normal kaynak linki olarak korunur, fakat artık Market Fiyatı
arama düğmesi oluşturulmaz. Düğmeler inline klavyededir; mesaj gövdesini uzatmaz ve
4096/1024 karakter sınırına yeni karakter eklemez. Bu düğmeler bildirim botu gerektirir;
Telegram kullanıcı hesabı inline klavye gönderemez.

Temizleme gereken iletide `forward` atlanır (özgün mesaj değişmeden iletileceği için);
kopyalama/yedek zinciri temizlenmiş metin ve entity offset'leriyle çalışır. Böylece
reklam/işbirliği etiketleri, doğrudan WhatsApp URL'leri ve WhatsApp URL butonları
forward ile geri eklenmez.

Üst satırın hemen altına `👉 Ürün Linki 👈` satırı, onun altına orijinal mesaj, en alta
da kaynak grup adı eklenir; orijinal mesajın `t.me` adresi grup adının içine gömülüdür
(ayrı `🔗 Mesajı Gör` satırı yoktur). `link_appendix: "smart"` (varsayılan) ürün adresini bu yazıya
gömer; ham URL ikinci kez yazılmaz, gövdedeki hyperlink/URL yedek olarak aynen kalır.
Ürün düğmesi yalnız adres metinde (görünür ya da gömülü) korunmuşsa kaldırılır; kesilme
veya `off` gibi durumlarda düğme yedekte kalır. `"all"` ürün satırı üretmez, eskisi gibi
gizli hyperlink/buton/önizleme adreslerini `🔗 etiket: adres` biçiminde ham URL olarak
döker; `"off"` ek bağlantı satırı yazmaz. Kaynağın arama butonları bot bildiriminde üç
modda da satır olmaz (klavyede durur); klavyesiz hesap kopyasında `smart` ve `all`
modlarında satır olarak yazılır.

`clean_quote_duplicates: true` (bu depoda açık) alıntıdaki tekrarın yalnızca **ilk
adımını** temizler: üst satırda yazan ürün adı, kaynak mesajda TEK ve kesin bir
ürün adı satırı olarak tespit edildiyse o satır alıntıdan çıkarılır; ayrıca
`_coupon_codes` ile doğrulanmış, tek başına duran kupon kodu satırı da çıkarılır.
"Kesin" değilse hiç dokunulmaz: birden çok ürün adayı, satır içinde fiyat/URL/
kod/metadata, kırpılmış başlık, linkli satır, tespit edilemeyen kod ya da alıntıyı
boşaltacak silme durumlarının hiçbirinde satır silinmez. Üst başlık, fiyat, tekrar
(dedup) anahtarı, arama sorgusu ve ürün linkleri daima **orijinal** mesajdan
hesaplanır; temizlik yalnızca görünen alıntı metnine ve onun entity offset'lerine
uygulanır.
