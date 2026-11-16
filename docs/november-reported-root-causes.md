# Bildirilen 13 örnek: kök neden analizi ve düzeltmeler (2026-11)

Kullanıcı 13 örnek bildirdi; **hepsi aynı tip hata değil**. Aşağıda her örnek için
ölçülen kök neden ve uygulanan minimum düzeltme var. Değişiklikler yalnız
`price_filter.py`, `bot.py` ve testlerde; **çalışan işleyiş bozulmadı** (altın
referans 563 kayıtta yalnız 2 kupon kaydı değişti, gerekçesi §14).

Kural: doğru tespitleri bozmadan ilerlemek. Bu yüzden her düzeltme **dar bir
kalıba** bağlandı; geniş "genel başlık" kuralları denendi ve **geri alındı**
(§13, denial listesi).

---

## 1. `36 adet askı 274 TL (7,6 TL adet)` → başlık `36 adet askı ❓`

**Kök neden.** Parantez içindeki `7,6 TL` birim fiyatı, `274 TL` toplamla aynı
aday havuzuna **eşit güçte** giriyordu. `_extract_select_best` iki farklı değer
görünce `ambiguous=True` üretip güveni `low` yapıyor; `low` tutar üst satıra
taşınmadığı için başlık `❓` kalıyordu. Yani fiyat **bulunmuştu**, güven
düşürülmüştü.

**Düzeltme.** `_relate_candidates` içinde dar kalıp: tutarın **önünde açık
parantez** var ve tutardan sonra yalnız `adet|tane|birim` (+ `)`) geliyorsa rol
`unit` olur. Toplam `pack_total` olarak kalır, birim fiyat havuzdan düşer.
Parantezsiz `274 TL 7,6 TL` yazımı ve parantez içindeki öteki tutarlar
etkilenmez.

**Sonuç.** `₺274 — 36 adet askı`.

---

## 2. Asus postunda başlık doğru, fiyat **başka ürünün**

**Kök neden.** Kaynak şablon üstte bir ürünü, altında **başka ürünleri**
listeliyor. `🔻Rampage Oyuncu Koltuğu … 6.881TL` satırı `sepette` nedeniyle
`discounted` (en güçlü) role sahipti; `_extract_build_pools` "finals" havuzunu
önceliklendirdiği için **oyuncu koltuğunun** fiyatı başlıktaki Asus'un fiyatı
sanıldı. Üstelik `_extract_select_best` iki farklı final görünce `low` üretti,
yani Asus'un kendi `24.999TL` fiyatı ekrana hiç gelmedi.

**Düzeltme.** `_heading_price_pool`: ileti ilk 6 satırında **iki veya daha fazla
fiyatlı ürün satırı** taşıyorsa (ve bunlar `Fiyat:` etiketli değilse) yalnız
**başlık bloğunun** tutarları havuza girer.

İki koruma:
- Başlık satırı **çıplak tutar** ise (`100 TL`) kural devre dışı: `Pamuk Havlu /
  100 TL / Cam Bardak / 200 TL` gibi eş yapılı listelerde hangi fiyatın hangi
  ürüne ait olduğu metinden çıkmaz, **belirsiz kalmalı** (mevcut test korundu).
- `Fiyat: 519 TL / … 419 TL` gibi **tek ürünün** fiyat bloğu bu sayıma girmez.

**Sonuç.** `24.999 TL` (medium). Oyuncu koltuğunun fiyatı artık başlığa sızmıyor.

**Ek kök neden (bağımsız doğrulamada bulundu).** Üstteki düzeltmeden sonra bile
başlık `❓` kalıyordu: `✅Prime Üyelere Özel 24.999TL - Piyasası 27.897TL+`
satırında **eski/geçmiş fiyat** (`27.897TL`, `cls="old"`) da başlık havuzuna
giriyordu. Havuz iki farklı değer görünce `_extract_select_best` sonucu yine
`ambiguous` sayıp güveni `low`a düşürüyor, doğru fiyat (24.999) üst satıra
taşınmıyordu. `_heading_price_pool` artık **yalnız `cls == "price"`** adaylarını
toplar; eski fiyat havuz dışında kalır. "Piyasası" bilgisi üst satırda
istenmiyor, ürünün fiyatı isteniyor.

---

## 3. `SPORTİF150 kodu ile … 600/150TL` → başlık kupon kodu gibi

**Kök neden.** `_score_line` içindeki model-kodu bonusu (`+3`) `SPORTİF150`
gibi kupon kodlarını da yakalıyordu. Kupon kodu "model kodu" sayılınca satır
gerçek ürün adından yüksek puan alıyor.

**Düzeltme.** `_COUPON_CODE_CONTEXT_RE`: kod `koduyla/kodla/kodu ile` ya da
`kupon kodu <KOD>` bağlamında geçiyorsa bonus **verilmez**. Gerçek model kodları
(`RTX 5050`, `NH.QZ7EY.001`, `512GB`) bu bağlama girmediği için etkilenmez.

---

## 4. `📢 Migros Sanal Market'te` başlık seçildi

**Kök neden.** Satır yalnız **mağaza + konum eki**. Kısa olduğu için puanı
gerçek ürün satırıyla **eşit** (4.50) çıktı ve `_title_pick_best` eşitlikte
**en erken satırı** seçtiği için başlık oldu.

**Düzeltme.** `_is_bare_store_mention`: satır mağaza adıyla başlıyor, **konum
ekiyle bitiyor** ve arada en çok **iki** sözcük var (sayı yok) ise genel
başlıktır. `Migros'ta Demlik ve Bardak Siyah Çay …` gibi ürün anlatan satırlar
bu kalıba girmez.

> Not: ilk denenen "mağaza adıyla başlıyorsa genel başlıktır" kuralı **çok
> genişti** — `Amazon Alışverişlerinizde Geçerli 12.000 TL'ye …` satırını da
> eliyor ve §8'i bozuyordu. Konum eki koşulu bu yüzden zorunlu.

**Sonuç.** `Demlik ve Bardak Siyah Çaylarda …` başlık olur.

---

## 5. `Yeni indirim kodu KASIM150 geldi` başlık oldu

**Kök neden.** §3 ile aynı: `KASIM150` model kodu bonusu alıyor, kod duyurusu
satırı gerçek ürün adını geçiyor.

**Düzeltme.** §3'teki kupon-kodu bağlamı + `_CAMPAIGN_CONDITION_LINE_RE` (§6).

---

## 6. `600 TL Üzerine 150 TL İndirim sağlar` başlık oldu

**Kök neden.** Kampanya **koşulu** cümlesi (iki tutar + `üzerine` + `indirim`)
3.50 puan alırken gerçek ürün adı (uzun olduğu için uzunluk cezasıyla) 2.50'de
kalıyordu.

**Düzeltme.** `_score_line` içinde bu kalıp **4 puan** kaybeder.

> Önemli ayrım: bu satırı `is_generic_title` ile **genel başlık yapmak
> denendi ve geri alındı**. Aynı cümle kupon kampanyasının **dedup kimliği**
> olarak da kullanılıyor; genel başlık sayılınca `dedup_key` `None` dönüyor,
> tekrar birleştirme ve DM eşleşmesi bozuluyordu (golden'da 214 fark). Doğru
> katman **başlık puanı**, kimlik değil.

---

## 7. `400 TL'ye 100 TL Indirim` → `₺100` fiyat yazıldı

**Kök neden — Türkçe büyük `I`.** `norm` Türkçe küçültme yapar (`I` → `ı`), bu
yüzden büyük harfle yazılmış `INDIRIM` tokeni `ındırım` olur ve `AFTER_NEG`
içindeki `indirim` köküyle **eşleşmez**. Sonuç: indirim tutarı ürün fiyatı
sayıldı.

**Düzeltme.** `norm_stem` (ı→i, ş→s, ğ→g, ç→c, ö→o, ü→u) **karşılaştırmanın iki
tarafına** uygulanır. `norm`'un kendisi (küçültme davranışı) değiştirilmedi;
aramalar ve dedup anahtarları aynı kalır.

**Sonuç.** Fiyat yok — doğru, çünkü iletide ürün fiyatı yok.

---

## 8. `Amazon … 12.000 TL'ye 1.000 TL İndirim Kodu` → `❓`, sonra başlık **gövdeden rastgele satır**

**İki ayrı kök neden.**

(a) `_coupon_preview_message` yalnız **ilk satıra** bakıyordu ve kart biçimi
(`KOD (kopyalamak için dokun)`) arıyordu. Kupon kodu **ayrı satırda** (`FIRSAT1000`)
durduğunda ileti "kupon duyurusu" sayılmıyor, `_campaign_title_if_clipped`
devreye girmiyor ve başlığa gövdeden bir satır düşüyordu.

(b) §4'teki geniş mağaza kuralı ilk satırı eliyordu.

**Düzeltme.** Tek başına duran **kod satırı** (yalnız büyük harf/rakam, uzunluk
≥4) ve o kod `_coupon_codes` ile doğrulanıyorsa ileti kupon duyurusudur.

**Sonuç.** `Amazon Alışverişlerinizde Geçerli 12.000 TL'ye 1.000 TL İndirim Kodu — FIRSAT1000`.

---

## 9. `Fiyat: 519 TL / ödeme aşamasında TEMIZLIK200 koduyla 2 adet alımda adet başı 419 TL`

**Kök neden.** §2'deki yeni kuralın **yan etkisi** yakalandı ve kural
daraltıldı: tek ürünlü gönderide iki fiyat satırı vardı ve bu "çok ürünlü"
sanılıp `519` seçildi. Oysa burada asıl teklif `419` (kuponlu adet başı).

**Düzeltme.** `Fiyat:` **etiketli** satırlar çok-ürün sayımına girmez; ayrıca
`_relate_refine_roles` zaten `multi_unit` rolünü `419`'a veriyor.

**Sonuç.** `₺419 — Fairy Platinum …`.

---

## 10. `₺647 — her an değişebilir`

**Kök neden.** Kaynak şablonun sabit **uyarı cümlesi** (`🚀 Fiyat her an
değişebilir, kaçırma!`) ürün adı değil; ama uzun Amazon başlığı uzunluk
cezası alırken bu kısa satır 2.50 puanla **başlık** oldu.

**Düzeltme.** `_GENERIC_TITLE_RE`'ye dar CTA kalıpları (`her an değişebilir`,
`… kaçırma!`).

**Sonuç.** `₺647 — Bosch Home and Garden X/Line 40 Parça Aksesuar Seti, …`.

---

## 11. `₺53.240 — her an değişebilir` (Acer)

**Kök neden.** §10 düzeltildikten sonra ortaya çıkan **ikinci** neden:
`priced_catalog_title` (fiyat + taksit parantezi + katalog adı kalıbı) marka
için **en az iki ardışık büyük harf** istiyordu. `GIGABYTE`, `ASUS` geçiyor;
`Acer` geçmiyordu. Katalog yolu kapandığı için başlık, parantez içindeki taksit
metninden türetiliyordu.

**Düzeltme.** Ölçüt "**başlık harfi büyük** + gerçek model izi (`_MODEL_RE`)".
Uzunluk ve model koşulları aynen korundu.

**Sonuç.** `₺53.240 — Acer Nitro ANV15-52 NH.QZ7EY.001 Intel Core i5 13420H …`.

---

## 12. `₺129.999 — GIGABYTE …` (doğruydu) + "mükerrer bildirim"

Bu örnek **zaten doğruydu**; §11 düzeltmesinden sonra da doğru kaldı
(regresyon testiyle kilitlendi). "Mükerrer bildirim" şikayeti fiyat/başlık
tespiti değil, **tekrar birleştirme** konusudur ve bu değişikliklerin kapsamı
dışında; başlık kimliği değişmediği için davranış aynı kalır.

---

## 13. Çoklu paylaşım notunda **ikinci grubun linki yok**

**Kök neden.** `dedup_badge` grup adlarını **virgülle** birleştiriyordu:
`", ".join(names)`. Grup adlarının **kendisi virgül içerebiliyor**
(`… Kupon Haberleri, İndirimde Al`). `dedup_badge_sources` de virgülden
bölünce:
- adlar ikiye bölünüyordu,
- `dedup_badge_name_spans` bölünmüş parçalara link oturttuğu için linkler
  **kayıyor**, ikinci grubun adresi **kayboluyordu**.

**Düzeltme.** `BADGE_SOURCE_SEPARATOR = " | "`. Adlar ` | ` ile ayrılır; ad
içindeki virgül artık bölmez. Eski **virgülle** yazılmış rozetler hâlâ okunur
(geriye dönük uyum: `_badge_source_parts` tek parça ve virgül görürse eski
bölmeye düşer). "· tek grup" ve "— KAÇIRMA!" ekleri ayrıştırmadan önce
söküldüğü için tek gruplu rozet değişmedi.

**Sonuç.** İki ad, iki ayrı `text_link`, **doğru aralıklarda**.

**Ek kök neden (bağımsız doğrulamada bulundu).** İlk düzeltme **tek gruplu**
rozeti kapsamıyordu. Tek gruplu yeni biçim rozeti (`… · tek grup`) yalnız bir
parça üretir; virgül içeren adda "tek parça kaldı → eski biçim" varsayımı
devreye girip adı ikiye bölüyor ve `text_link` **hiç üretilmiyordu**. Yani
kullanıcının "ikinci bildirimin linki yok" şikâyeti tek gruplu rozetlerde
sürüyordu. Ayırt edici ölçüt yalnız `· tek grup` ekidir.

`— KAÇIRMA!` eki bu iş için **kullanılamaz**: aynı ek yeni biçimde 5+ paylaşımda
da eklenir (`🚨 5 kere paylaşıldı: A, B, C — KAÇIRMA!`). Onu "eski biçim"
saymak, yeni biçim 5+ paylaşımlı rozetlerde adları bölünmez hâle getiriyordu.
Bu yüzden ekin **karar** rolü kaldırıldı, yalnız adlardan **temizlenme** rolü
kaldı (`_BADGE_TRAILING_DECOR_RE`).

---

## 14. `❓` kararı: kupon duyurusunda soru işareti yok

Kullanıcı notu: *"fiyat bulamadıysak artık soru işareti koymayalım … bir de
indirim kuponu bildirimlerinde de koymayalım … gidip kupon sanıp sırf fiyat
bulamadık diye fiyat olan bi gönderiyi çöpe atmamalıyız."*

**Düzeltme.** `_is_coupon_announcement`: ileti **tam bir kampanya cümlesi**
kuruyorsa (eşik + indirim tutarı — `_coupon_campaign_title` bunu zorunlu
kılar) üst satıra `❓` yazılmaz.

**Ek kök nedenler (bağımsız doğrulamada bulundu).** `_coupon_campaign_title`
iki dar kalıbı kaçırıyordu ve bu iletilerde `❓` haksız yere kalıyordu:

| Örnek | Kaçan kalıp | Düzeltme |
|---|---|---|
| 4 | `2 Öde 1'i Money Hediye` — ileti **kupon/kod sözcüğü hiç içermiyor**; `campaign_marker` boş dönüyordu | `_NON_COUPON_CAMPAIGN_RE`: `N öde M`, `money`, `biri tutarında` |
| 5 | gövdede yalnız `Money hesabına iade` var | aynı kalıp |
| 7 | `400 TL'ye 100 TL **I**ndirim` — noktasız büyük `I`, `normalize` ile `ındirim` olur, `indirim` deseni kaçar | `_campaign_norm`: **yalnız** kampanya sözcüğü tanımada `ı → i`; `normalize` davranışı değişmedi |

`normalize` bilerek değiştirilmedi: arama sorgusu, eşleştirme ve dedup ondan
besleniyor. Katlama yalnız `_campaign_norm` içinde yapılır.

`2 alana 1 TL` kalıbı **bilerek dışarıda**: o iletilerde mevcut davranış başlığı
`❓` ile bırakıyor ve o davranış korunuyor (genişletme denendi, 3 alt-testi
kırdı, geri alındı).

**Neden "fiyatlı gönderiyi çöpe atma" riski yok:** bu dal, **güvenli tutar
dalından sonra** gelir. `high`/`medium` güvenli fiyat varsa üst satıra o yazılır
ve bu fonksiyona hiç gelinmez. Yani istisna yalnız "fiyatı olmayan duyuru"
durumunda devreye girer.

**Altın referans etkisi.** 563 kayıtta **2 kayıt** değişti; ikisi de kupon
duyurusu (`Hopi 200 TL ve üzeri …` ve `SNEAKER500 …`). Her iki kayıtta
değişiklik yalnız `❓` + boşluğun (2 UTF-16 birimi) kalkması; `price` katmanı,
`classic` ve `preload` profilleri **hiç değişmedi**. Onay kodu
`0b2e49683427` ile yeniden yazıldı.

---

## Geri alınan (denenmiş, yanlış) yaklaşımlar

Bunlar **uygulanmadı**; ileride tekrar denenmemesi için kayıtlıdır:

| Deneme | Neden geri alındı |
|---|---|
| "Eşik + indirim cümlesi" ni `is_generic_title` ile genel başlık saymak | Kupon **dedup kimliğini** öldürüyor (`dedup_key` → `None`), tekrar birleştirme ve DM bozuluyor. Golden'da 214 fark. Doğru katman: başlık puanı. |
| "Mağaza adıyla başlayan satır genel başlıktır" | `Amazon Alışverişlerinizde Geçerli …` gibi gerçek kampanya başlıklarını da eliyordu. Konum eki koşulu eklendi. |
| Kupon kodu kalıbını `_GENERIC_TITLE_RE`'ye eklemek | `KASIM150 koduyla 2477 TL` gibi **kupon sonrası ödeme** tutarını taşıyan satırları da eliyor, fiyat kaybına yol açıyordu. Bonus kaldırma yolu seçildi. |

---

## Korunan davranışlar (regresyon)

`tests/test_user_reported_november_batch.py` 30 testle yukarıdaki 13 örneği ve
şu değişmezleri kilitler (10'u bağımsız doğrulama turunda eklenen ek kök nedenler):

- eş yapılı çok ürün listesi (`100 TL` / `200 TL`) **belirsiz** kalır (`low`);
- `413 TL (137 TL/Adet)` → `413` (mevcut `pack_total` kuralı);
- `KASIM250 koduyla 2477 TL` → **`2477`** (kupon sonrası ödeme hâlâ fiyat);
- `Sepette 50 TL indirim` → fiyat yok; `Fiyat: 1.299 TL` → `1299`;
- tek gruplu rozet `· tek grup` ekiyle aynı; eski virgüllü rozetler okunur.

## Doğrulama

| Kontrol | Sonuç |
|---|---|
| Tam test paketi | **1228 passed / 2421 subtests** (taban 1198; ilk tur +20, doğrulama turu +10) |
| `python bot.py --check` | Ortam secret'ları yok (değişiklik öncesi de aynı) |
| `git diff --check` | temiz |
| Altın referans | 563 kayıt; **yalnız 2 kupon kaydı** değişti (§14), onay kodu `0b2e49683427`. Doğrulama turundaki düzeltmeler golden'da **hiçbir** değişiklik yapmadı (sha `55658a87b0582db9`, tabanda da aynı 2 kayıt). |
| 563 kayıt uçtan uca tarama | `❓` üreten başlık **98 → 96**; değişen başlık **yalnız 2** (ikisi de istenen kupon `❓` kaldırması) — gizli davranış değişikliği yok |
| `price` / `classic` / `preload` katmanları | **değişmedi** |

### Doğrulama turunda eklenen testler

| Test | Kilitlediği davranış |
|---|---|
| `test_old_price_beside_the_real_price_does_not_make_it_unknown` | `Piyasası 27.897TL` eski fiyatı başlık havuzuna girmez; başlık `₺24.999 — Asus …` |
| `test_prime_line_alone_yields_its_price` | 2. örnek Asus'tan **bağımsız** ölçülür |
| `test_non_coupon_campaign_announcement_gets_no_question_mark` | `2 Öde 1'i Money Hediye` → `❓` yok |
| `test_money_refund_wording_gets_no_question_mark` | `Money hesabına iade` → `❓` yok |
| `test_dotless_capital_i_campaign_word_is_recognized` | `Indirim` (noktasız I) kampanya sözcüğü sayılır |
| `test_price_bearing_post_is_never_treated_as_coupon` | fiyatlı gönderi kupon sayılmaz; `2477` korunur |
| `test_product_price_wins_over_campaign_sentence_in_same_post` | kampanya cümlesi + ürün fiyatı: `₺200` **fiyat olarak yazılmaz** |
| `test_single_group_name_with_comma_keeps_its_link` | tek gruplu, virgüllü ad → **1** `text_link`, doğru aralık |
| `test_legacy_badge_keeps_comma_split_with_group_count_suffix` | `— KAÇIRMA!` eki eski bölmeyi **engellemez** |
| `test_new_format_multi_group_names_survive_round_trip` | 3 gruplu, hepsi virgüllü → 3 ad, 3 link |
