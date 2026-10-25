# Fiyat ayrıştırıcı regresyon incelemesi

## Değiştirilen eski beklentiler

Bu değişiklikler bağımsız mağaza fiyat doğrulaması değildir: mesajda açıkça yazılan
koşullu fiyatın, yeni kabul kriterleri uyarınca bildirim fiyatına alınmasıdır.

- Bref: `Fiyat : 97 TL / 2 adet alımda 48 TL`. Önceki 97 beklentisi,
  normal fiyat varsa `cond` adaylarının değerlendirilmemesini sabitliyordu.
  Yayla'nın `114 TL (2 Adet Alımda 57 TL)` kabul kriteriyle aynı dil yapısıdır;
  slash ile parantez anlamsal bir fark yaratmaz. Yeni beklenti 48, orta güven.
- Nivea: `Fiyat : 199 TL / 6 adet alımda 179 TL`. Aynı yapıdaki miktarın
  2 yerine 6 olması açık çoklu alım teklifini geçersiz kılmaz. 199 yerine 179;
  orta güven. Metin toplam demiyor; birim fiyat yorumu kanal kalıbına dayanır.
- Köyceğiz: `512 TL / 2. ürün 1 TL ile 2 adet alımda 256 TL`.
  1 TL ikinci ürüne özgü promosyondur, genel ürün fiyatı değildir. 256 TL ise
  açık iki-adet-alım teklifidir. 512 yerine 256; orta güven. Mesajın yuvarlama
  veya hesabını doğrulamıyoruz (512+1 toplamının yarısı 256,50 eder);
  bildirilen 256 tutarını yeniden hesaplayıp değiştirmiyoruz.

Eski beklentiler eski öncelik politikasını doğru test ediyordu; yeni istenen
politika açısından yanlışlar. Gerçek satın alma koşullarının bağımsız kanıtı yok.

## Güven politikası

- high: etiketli/bağlamlı tek normal veya açık nihai fiyat.
- medium: tek normal fiyat (güçlü etiket yok); açık adet başı/tanesi;
  normal/nihai fiyatla karşılaştırılabilen açık çoklu alım alternatifi.
- low: tek başına `2 adet alımda X TL` (birim/toplam kanıtı yok),
  `2 adet X TL`, açık çoklu toplam, birbiriyle çelişen nihai fiyatlar,
  yalnız eski/geçmiş fiyatlar. low değerler DM veya bildirim fiyatı olarak
  kullanılmaz. Teşhis için value alanında bir aday bulunabilir.
- none: fiyat yok veya yalnız kupon/eşik/kargo/ilgisiz tutarlar.

Rol filtresinden sonra daha avantajlı birim alternatif karşılaştırılabilir;
metindeki bütün sayılar üzerinde minimum alınmaz. İki ayrı açık nihai kupon
fiyatının hangisinin geçerli olduğu bilinmiyorsa minimum seçmek yerine low.

## Test kapsamı ve sonuç

Komut: `.venv/bin/python -m unittest discover -s tests`
Sonuç: 588 test OK. Fiyat/başlık/DM/düzen grubu 20 test OK; yeni bağlam grubu
9 test OK (yedi örnek × iki para birimi × üç boşluk = 42 alt durum dahil).
Kalan 559 test de tam paket içinde geçti. `git diff --check` temiz.

Yeni bağlam testleri: yedi kabul örneği ve rol nedenleri; ham Telegram yolu;
Chakra son compose_message çıktısı; toplam ve çelişen fiyatlar; beş ayrı satın
alma ifadesi; eski/güncel/kupon/eşik/kargo/çoklu fiyatı birleştiren mesaj;
birden fazla nihai kupon fiyatı; yalnız kupon ve fiyat bulunamadı; eski fiyat,
kargo ve birim rolleri. Mevcut sayı biçimi, üstü çizili fiyat, başlık, DM ve
bildirim düzeni testleri değiştirilmeden geçti. Önceki test dosyasında yalnız
Bref/Nivea/Köyceğiz beklentileri değişti.

## Kalan riskler

- Normal fiyatla birlikte yazılan yalın `N adet alımda X TL` kanalın birim
  fiyat kalıbı kabul edilir, orta güven taşır. Gönderen toplam kasteder fakat
  belirtmezse hâlâ yanlış yorumlanabilir: `300 TL / 2 adet alımda 200 TL`.
- Birden çok ürünün aynı mesajdaki fiyatları henüz ürün bazında gruplanmaz.
  Teklif ilişkilerinin satırlara bölünmesi veya desteklenmeyen ayraçlar da
  sınıflandırmayı bozabilir; genel Türkçe dil çözümleyicisi değildir.
- Kupon uygunluğu/üyelik, stok ve gerçek kasa fiyatı doğrulanmaz. Yazılmamış
  nihai tutar hesaplanmaz. Promosyon aritmetiği kontrol edilmez.
- Testler yerel Telegram nesnesi benzetimleri ve gerçek üretim fonksiyonlarıyla
  çalışır; canlı Telegram'a mesaj gönderilmedi.

PR açılmadı. Bu sınırlar kabul edildiğinde regresyon kontrolleri açısından PR
incelemesine hazır; üretimde hatasız fiyat garantisi değildir.

## Son çağrı noktası/güven denetimi

Son çalıştırma: 591 test OK (bağlam grubu 12, mevcut fiyat grubu 20,
559 diğer test). Önceki 588 sayısı yukarıdaki ilk incelemeye aittir.
Üç yeni test metodu eklendi; bu denetimde mevcut beklenti değiştirilmedi.

Üretim çağrı haritası:

- `compose_message -> smart_header_info -> analyze -> extract_price`:
  yalnız medium/high üretilmiş fiyat başlığına dönüşür. low/none için
  PRICE_MISSING_MARK (`❓`) yazılır; "Fiyat tespit edilemedi" uyarısı kaldırıldı.
  Fiyat, ürün adıyla birlikte TEK üst satıra yazılır
  (`56₺ - Ürün adı`; `format_price`ın "56 TL" çıktısı yalnız görüntüleme için
  `header_price_text` ile `₺` biçimine çevrilir — ayrıştırıcıya dokunulmadı),
  ad da fiyat da yoksa üst satır kurulmaz. Bu satır yalnızca görünen metindir ve
  arama sorgularına/dedup anahtarına taşınmaz. Gövde orijinal tutarlarıyla
  korunur; gövdede bir tutarın bulunması, ayrıştırıcının bunu fiyat olarak
  onayladığı anlamına gelmez.
  Teslimat, geçmiş bildirimin yeniden düzenlenmesi ve kuru çalışma bu ortak
  compose yolunu kullanır. SMART_HEADER kapalıyken yeni fiyat satırı üretilmez.
- `enqueue_private_offer -> sanitize_message -> dm_should_send ->
  price_matches -> extract_price`: yalnız medium/high eşikle karşılaştırılır.
  Kuyruktan gönderimde `prepare_private_offer` aynı dm_should_send kararını
  yeniden kontrol eder. Bağımsız kelime kuralı low fiyatlı mesajı da gönderebilir;
  bu fiyat eşiğinin tetiklenmesi değildir.
- `offer_terms -> extract_price`: canlı dedup ve geçmiş taramasının ortak yolu.
  Önceki kod güven kontrolünden sonra cond adaylarının minimumunu tekrar
  hesaplıyordu; low sonucu yeniden fiyat haline getirebiliyordu. Bu ikinci
  hesap kaldırıldı. Güvenilir değer seçimi artık yalnız extract_price'tan gelir.
  Bu son denetimde değişen tek üretim çağrı noktası offer_terms'tür.
- `find_candidates` artık üretimde yalnız ayrıştırıcının içinden tüketilir;
  filtrelenmemiş adaylardan fiyat çıkaran ikinci bir bot yolu kalmadı.

Üç yeni test:

1. `test_confidence_through_notification_dm_and_dedup`: altı satın alma
   ifadesi, high/medium/low/none, eski fiyat, kupon/eşik, çelişen nihai fiyat,
   düşük koşullu aday; ham olaydan sanitize/entity/compose, DM ve offer_terms.
   Gerçek UTF-16 strikethrough entity'si; bağımsız DM kelime kuralı da kontrol edilir.
2. `test_original_full_context_and_total_counterexamples`: mevcut REAL_WORLD
   listesindeki Bref, Nivea ve Köyceğiz tam mesajları doğrudan kullanılır.
   URL, paket boyutu, emoji ve başlıkla birlikte fiyat/rol/güven doğrulanır.
   Aynı mesajlara genel olarak `toplam` eklendiğinde koşullu tutarın birim
   fiyata terfi etmediği de doğrulanır.
3. `test_seven_examples_through_production_consumers`: tüm yedi örnek,
   compose_message çıktısı, DM eşik sınırı ve offer_terms üzerinden doğrulanır.

Normal fiyat, birim/toplam belirsizliğini mantıksal olarak ÇÖZMEZ; karşılaştırma
bağlamı sağlar. `300 TL / 2 adet alımda 200 TL` orta güven kararı, bu kanalın
"normal fiyat / koşullu birim fiyat" sözleşmesine dayanır, matematiksel kanıta
veya genel Türkçe garantisine değil. Yayla ve diğer kabul örnekleri de aynı
varsayımı gerektirdiğinden davranışları değiştirilmedi. Bağlamsız aynı kalıp low;
"toplam" açıkça yazılmışsa normal fiyat olsa bile toplam aday seçime alınmaz.

Bref/Nivea/Köyceğiz tam bağlamları, birim teklif okumasını destekliyor; hiçbirinde
"toplam" yazmıyor. Bununla birlikte metinler bağımsız kasa fiyatı kanıtı değildir.
Karşı örnek testleri bilinen hata sınıflarını sınırlar; başka hiçbir mesajda
hata olmayacağını kanıtlayamaz. Önceki kalan riskler geçerlidir.

684 test yerel üretim fonksiyonlarını çalıştırır; canlı Telegram gönderimi veya
canlı DM kuyruğu uçtan uca ağ testi yapılmadı. PR açılmadı; kullanıcı onayı beklenir.

## Son denetim: bildirim üst satırı (sıfır fiyat, UTF-16 sınırları, sunum yalıtımı)

Kullanıcının dört risk başlığı `main` ile diferansiyel ölçümle denetlendi.

1. **Sıfır/geçersiz fiyat.** Politika değiştirilmedi: `Fiyat: 0 TL` → high/0.0,
   `ücretsiz|bedava` → medium/0.0 (`_FREE_RE`), yani sıfır GÖSTERİLEN bir fiyattır
   ve `main`de `💰 0 TL` idi; yeni üst satırda `0₺`. `kargo bedava`/`deneme`
   `_FREE_BLOCK_RE` ile fiyat sayılmaz → `❓`. Negatif fiyat üretilemiyor (testteki dokuz
   negatif/ters biçimde de ayrıştırıcı işareti düşürüyor, `value < 0` hiç görülmedi), boş/`₺`/`TL`/`1.2.99 TL`/`12,29,90 TL`/kupon/`%50`/low → `❓`.
   `0₺`, `0,50₺`, `12,50₺`, `1.234.567,89₺` gibi her `format_price` biçimi
   `is_header_price`dan geçer; bölünme/arama/sayı geri-dönüşümü turu-geri doğrulandı.
2. **Uzun adlar ve sınırlar.** `extract_title` emoji/sembolleri düşürüyor AMA süslü
   font astral harfleri (`𝐆𝐔𝐂𝐂𝐈`, `𝕏`) düşürmüyor: karakter sayısı UTF-16
   biriminden küçük kalıyor. Karakterle ölçen `fit_header_line` + gövde kırpması bu
   yüzden odayı aşabiliyordu; ölçüm UTF-16 birimine çevrildi (`utf16_budget`).
   Ölçülen etki (astral yoğun, karakterce yasal kaynak ileti): mesaj yolu
   `main` 4161 birim → **4096 aşıyordu**, yeni 3895; medya caption yolu
   `main` 1622 birim → **1024 aşıyordu (598 fazla)**, yeni 1000. Gövde kırpması
   artık kalıbı TAM dolduruyor (≤2 birim pay), gereksiz içerik atmıyor;
   kırpma sonrası tüm entity'ler gönderilen metnin içinde kalıyor ve kalınlık
   ilk satırı (üst satırı) kapsıyor.
3. **Sunum yalıtımı.** Üst satırla seçilen fiyat aynı kaynaktan geliyor
   (`smart_header_info`), `parse_threshold("12.299,90₺") == 12299.9`; gövde,
   kampanya/koşul satırları, link bloğu ve kaynak adı bayt bayt `main` ile aynı
   (37 gerçek mesajda `body/appendix/source_line/title` farkı 0). low/none tutar
   üst satıra çıkmıyor; `❓` hiçbir tüketicide fiyat sayılmıyor.
4. **Eski/yeni ayrımı.** `PRICE_LINE_PREFIX`/`LEGACY_HEADER_SEPARATOR` yalnız
   `split_header_line`ın okuma yolunda geçiyor (yazma yolunda hiçbir kullanım yok);
   `is_price_line` kaynak/eski-düzen tanımaya ayrılmış. Ayraç, `₺` veya fiyat
   benzeri sayı içeren adlar (`Samsung 2024 - Yeni Sezon`, `Beko X - No Frost 56 cm`)
   bölünmüyor; `build(split(x)) == x`.

`send_bot_ping`/`send_bot_media`/`edit_bot_text`/`edit_bot_caption` içindeki son
savunma dilimleri (`text[:MESSAGE_LIMIT]`, `caption[:CAPTION_LIMIT]`) karakter
sayar ve `str[:N]` dilimi birim sınırını garanti ETMEZ (ölçüldü: 4096 karakterlik
astral bir dilim 7680 birim). Çağrı yollarının tamamı denetlendi: dört Bot API
çağrısı ve üç hesap göndericisi (`send_copy`, `send_media`, `send_text_only`,
`send_link_card`) ile `/kaynaktest` kurusu, compose'un UTF-16 garantisiyle
dilime HİÇ ulaşmıyor (`text[:limit] == text`, entity aralıkları gönderilen metnin
içinde) — bu `SendBoundaryTests` ile kilitlendi; tek compose-dışı çağrı (`/test`
deneme bildirimi) sabit kısa metindir ve entity taşımıyor. `str[:N]` kod noktası
kestiği için karakter bozulması/tek başına surrogate üretmesi de mümkün değil.
Kod bu yüzden değiştirilmedi.

Bilinen, bu değişiklikten bağımsız kalan davranış: çoklu fiyat/`sepete özel`
kalıplarında low kararı `❓` üretir (gövdede sayı görünmeye devam eder) ve
rozet düzenleme yolundaki sığma kontrolü (`len(new_text) > limit`) karakterle
ölçer — orada taşma durumu düzenlemenin reddedilip kısa rozete düşmesiyle
sonuçlanır, bildirim kaybı üretmez (önbellekteki `text` gönderilenle uyumlu
kalır); ayrı bir iş olarak ele alınabilir. Yönetici yanıtı bölücüsü
`chunk_text(limit=3500)` da karakter sayar; durum/komut çıktılarında 1750+
astral karakter gerekirdi (bildirim yollarıyla ilgisi yok).
