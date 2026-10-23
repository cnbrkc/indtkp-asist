# Yeni fırsat kalıpları ve dedup denetimi

## Kapsam ve başlangıç

Son birleştirilmiş fiyat değişiklikleri bu checkout'ta mevcut. Başlangıçta tam
paket 591 test OK. Önceki test beklentilerinin hiçbiri değiştirilmedi.
PR açılmadı; bu çalışma yerel dalda inceleme içindir.

## Fiyatlar: yeniden üretilen önceki sonuç ve yeni sonuç

| Mesaj | Önce | Sonra | Somut neden |
|---|---|---|---|
| OGX | 519 / medium | 369 / medium | Parantez sonrası `1 adet` eski belirsiz cond sınıfında kalıyor, seçim havuzuna girmiyordu. Açık tek-adet birim rolü eklendi. |
| Bargello | 250 / low | 250 / low | Aday zaten bulunuyordu; bildirim güven kapısı nedeniyle fiyat bulunamadı diyordu. Birim/toplam kanıtı yok, sessizce birim varsayılmadı. |
| Uni Baby | None / none | 373 / medium | `adedi` açık birim kalıbı eksikti; yakındaki kupon sözcüğü adayı other yapıyordu. Kupon tutarını koruyup açık birim sonucu terfi ettiriyoruz. |
| Fropie | 189 / low | 94 / medium | `2 al 1 öde ile` sonucu normal fiyat sayılıyor ve iki normal fiyat çelişiyordu. Açık kampanya sonucu koşullu efektif fiyat sınıfına alındı. |
| Busstier | 179 / high | 179 / high | Önce doğru seçim güçlü Fiyat etiketinden geliyordu; 450 eşikleri ve 170,05 kampanya tutarı hâlâ normal sınıfındaydı. Eşik ve sonda `(N adet)` bulunan KAMPANYA tutarı ayrıldı. |
| E-Garaj | 560,28 / high | 560,28 / high | Genel fiyat korunur; 504,25 `(2 adet)` kampanyasının birim/toplam niteliği ve hangi indirime bağlı olduğu kanıtlanamaz. 1000 eşiktir. %15 veya %10'dan yeni fiyat hesaplanmaz. |

Bu önce/sonra gözlemleri verilen metinlerin yerel üretim fonksiyonunda yeniden
üretimidir; orijinal Telegram entity/kaynak kayıtları elimizde yoktur.

Açık toplam ifadeleri birim kampanyaya yükseltilmez. Bağlamsız buy/pay fiyatı,
yalın N-adet-alımda ve `(N adet)` kampanya etiketi low kalır. Normal fiyatla
karşılaştırılan buy/pay sonucu medium; aritmetik doğrulama yapılmaz.
Kupon/indirim, eski, kargo ve taksit tutarları birim kalıbıyla terfi ettirilmez.
Yedi eski kabul örneği ve DeFacto 450 sonucu değişmedi.

Uzun OGX ve Busstier başlıklarında yeni testler ayrıca mevcut başlık seçicisinin
Takip Kuponu/Kampanyalar açıklamasını seçebildiğini gösterdi. Yalnız bu genel
meta satır başlangıçları başlık adayı olmaktan çıkarıldı; ürün adı puanlama
mimarisi değiştirilmedi.

## Dedup akışı ve somut çoğalma yolu

1. Hızlı yoklama `_fast_poll` işaretli olayı normal `on_new_message` işleyicisine
   verir. `(chat_id, message_id)` LIVE_SEEN / SOURCE_IN_FLIGHT / POLL_DISPATCHED
   kontrolleri aynı kaynak mesajın yeniden işlenmesini sınırlar.
2. `process_source_event` filtrelerden sonra temizlenmiş başlığı ve offer_terms
   çıktısını hesaplar. SMART_HEADER açıkken smart_dedup_title, kapalıyken
   _search_query kullanılır.
3. dedup_offer_key = sıralı tam başlık token'ları + koşul imzası. Kaynak/grup adı
   ve akış türü anahtara dahil değildir. Model, ölçü ve renk token'ları silinmez;
   bulanık benzerlik eklenmedi.
4. dedup_before_send ortak kilit/cache içinde rezervasyon koyar. Aynı anahtarın
   yeni teklifi better_or_different_offer ile daha iyi/farklı bulunursa yeni
   bildirim oluşur; aksi halde ilk bildirimde sayı ve kaynak rozeti güncellenir.
   Fast notify yalnız deliver içindeki gönderim sırasını değiştirir, ayrı bir
   ürün dedup havuzu oluşturmaz. Başarısız rezervasyon/silinmiş bildirim
   fail-open davranışı korunur.
5. Açılışta hedef geçmişi taranır, düzenlenebilir bot/hesap bildirimleri aynı
   anahtar ve teklif karşılaştırmasıyla cache'e alınır. Varsayılan pencere
   12 saat, ayarlanabilir 1–72 saat; cache/zaman sınırları değiştirilmedi.

`better_or_different_offer((13485.92,''),(13485,''))` önceden True idi:
0,92 TL fark 0,01 TL'den büyük olduğundan yeni fırsat sayılıyordu.
İki kez 13485,92 ardından iki kez 13485 dizisi böylece iki ikili grubu
oluşturabiliyor. Bu doğrudan yeniden üretildi; dört gerçek olayın tek kesin
nedeninin bu olduğunu iddia etmiyoruz. Tam ham mesajlar/zamanlar olmadan
başlık, koşul imzası, geçmiş sahipliği veya pencere ayrımını dışlayamayız.

Yeni tolerans: en az bir fiyat tam TL ise, hem 1 TL hem fiyatın %0,01'iyle
sınırlı fark yeni fiyat düşüşü sayılmaz. İki kesirli fiyatta eski 0,01 TL
karşılaştırması korunur. Küçük fiyatlı ürünün anlamlı indirimi ve gerçek büyük
fiyat düşüşleri bildirim üretmeye devam eder.

Satıcı adı ve Amazon Depo kaynak adı kendi başına ürün durumu değildir.
Ancak metinde açık ikinci el / ikinci el yeni gibi / yenilenmiş durumu varsa
bunlar ayrı koşul imzalarıdır. Geçmiş bildirimdeki kaynak footer'ı ve kaynak
rozeti bu durum çıkarımından önce kaldırılır. Başlığın kendisinde anlamlı ek
kelimeler varsa geniş silme/fuzzy birleştirme yapılmaz.

**Sınır:** Kullanıcının dört gerçek paylaşımından biri gerçekten ikinci el
"yeni gibi", diğerleri yeni/durumu bilinmeyen ürünse bu çalışma hepsini
otomatik tekleştirmez. Fiyat/kaynak yuvarlama çoğalması çözülür; anlamlı ürün
durumu farkı temkinli biçimde korunur. Olayın tümünü kesin teşhis etmek için
anonimleştirilmiş dört ham metin ve paylaşım zamanları gerekir.

## Testler

Eklenen bağımsız dosya tests/test_offer_regressions.py: 10 test metodu;
alt durumlarla altı gerçek örnek, TL/₺, Unicode boşluklar, açık birimler,
kupon/kargo/eski fiyat koruması, kampanya ve eşik rolleri, kampanya çelişkileri,
açık toplam karşı örnekleri, fiyat/DM/bildirim/offer_terms tüketicileri;
model/ebat/renk/kapasite/durum ayrımı, kaynak metadatası ve yuvarlama toleransı.

Eklenen RoundedOfferFlowTest: 4 entegrasyon testi, mevcut bot.main sahte Telegram
istemcisiyle gerçek handler/rezervasyon/deliver/rozet/geçmiş akışı çalışır:
- üç kaynaktan dört paylaşım, poll/live karışımı ve aynı source mesajın tekrarı:
  tek bildirim, 4 sayısı ve üç kaynak adı;
- açılış geçmişindeki 13485,92 fiyatına 13485 canlı kopya: yeni bildirim yok;
- açık ikinci el-yeni gibi ile durumu belirtilmeyen ürün: iki bildirim;
- farklı model ve anlamlı fiyat düşüşü: üç ayrı bildirim.

Mevcut testlerde beklenti değişikliği yok; yalnız yeni sınıf/metotlar eklendi.
Canlı Telegram/DM ağına bağlanılmadı. DM karar fonksiyonu ve bildirim üretimi
üretim kodudur; taşıma testleri sahte istemci kullanır.

## Kalan riskler

- Birim/toplam açık değilse kanal kalıplarının yorumu mutlak kanıt değildir.
- Kupon uygunluğu, stok, ödeme esnası %15, sepet eşiklerinin sağlanması veya
  promosyon aritmetiği doğrulanmaz; yazılmayan fiyat hesaplanmaz.
- Çok ürünlü mesajlar ve keyfi farklı başlıklar ürün bazında çözülmez; yakın
  kelimelere dayalı agresif birleştirme özellikle eklenmedi.
- 1 TL / %0,01 sınırındaki gerçek küçük fiyat düşüşü yuvarlama sanılabilir.
- Anlamlı durum farkını korumak bazı görünürde benzer fırsatları ayrı bırakır.

## Son doğrulama

- Başlangıç: 591 test, OK.
- Son tam paket: `.venv/bin/python -m unittest discover -s tests` — **605 test, OK**.
- Yeni bağımsız grup: 10 test, OK; entegrasyon dosyası: 165 test, OK
  (4 yeni test dahil).
- `git diff --check`: temiz.
- Değişen üretim dosyaları yalnız `price_filter.py` ve `bot.py`.
- Önceki test beklentilerinde değişiklik yok. Eklenen test metodu toplamı 14.

## Dar kapsamlı son denetim: tolerans ve bilinmeyen durum

Bu aşamada üretim davranışı değiştirilmedi; offer_terms içinde bilinmeyen
ürün durumunun sıfır ürün anlamına gelmediği açıklayan yorum eklendi.
Mevcut test beklentileri değiştirilmedi. Yanıltıcı `...as_new` adlı test,
`...without_listing_evidence` olarak yeniden adlandırıldı; beklentisi aynı.

Toleransın bütün üretim kullanım yerleri tarandı: better_or_different_offer
canlı dedup_before_send kararında ve açılış geçmişi indeksinde kullanılır.
Başlık/model eşleştirmesinde, fiyat ayrıştırmada, bildirim fiyatında ve DM
fiyat eşiğinde KULLANILMAZ. Farklı koşul imzaları fiyat toleransından önce
ayrılır; başlık/koşul anahtarı farklı teklifler aynı cache kaydına zaten girmez.

Kesin karşılaştırma (koşullar aynı, iki fiyat biliniyorsa):
- En az bir fiyat tam TL: t = max(0,01; min(1 TL; min(eski,yeni) * 0,0001)).
- İki fiyat da kesirli: t = 0,01 TL (mevcut davranış).
- Yeni fiyat < eski fiyat - t ise yeni fırsat; eşit sınır yeni düşüş sayılmaz.
- Eski güvenilir fiyat yokken yeni güvenilir fiyat gelirse yeni fırsat.
- Yeni güvenilir fiyat yoksa sırf fiyattan yeni fırsat üretilmez.
- Fonksiyon yönlüdür: fiyat yükselişini yeni indirim diye bildirmez.

Testler: 13485,92 -> 13485 tekrar; 13485,92 -> 13484 gerçek düşüş;
13486 -> 13485 tam 1 TL sınırı; 13486,01 -> 13485 gerçek düşüş;
1000,09 -> 1000 tekrar; 1000,11 -> 1000 gerçek düşüş;
iki kesirli fiyat ve küçük fiyatlı gerçek indirimler de korunur.
DM 13485 eşiği, 13485,92 fiyatını hâlâ reddeder; dedup toleransı DM'ye sızmaz.
Canlı ve geçmişten yükleme yollarında istenen 13485,92/13485/13484 dizisi
üretim handler'larıyla sınandı.

Durum politikası:
- İki aynı açık durum aynı kimlikte birleşir. Farklı satıcı adları mevcut
  politikada tek başına farklı teklif değildir; durum aynıysa birleşebilir.
- İkinci El: Yeni Gibi ile Yenilenmiş ayrı kalır.
- Durum yokluğu ne sıfır ürün etiketi ne de kanıtlanmış uyuşmazlıktır.
  Yalnız mevcut başlık/fiyat/koşul özeti farklı kaynakların aynı satış ilanı
  olduğunu kanıtlamaz. Bu yüzden açık/bilinmeyen durum farklı kaynak
  mesajlarında temkinli olarak ayrı kalır. Tüm bilinmeyenleri wildcard yapıp
  birbirinden farklı durumları dolaylı birleştirme riski alınmadı.
- Kesin (chat_id,message_id) kimliği aynıysa poll/live tekrar zaten tek
  kaynak olayıdır; sonraki temsilde durum satırı eksik olsa da ikinci bildirim
  veya yeni paylaşım sayısı oluşmaz. Bu ayrı entegrasyon testiyle doğrulandı.
- Ortak satış ilanı ID'sine dayalı yeni bir kaynaklar arası köprü eklenmedi;
  bu aşamada böyle bir kanıt akışı yoktur. Bu bir sınırlamadır, tüm
  açık/bilinmeyen çiftlerin aynı fırsat olduğu iddiası değildir.
- Aynı durum/fiyat olsa da farklı model, ebat, renk ve kapasite anahtarları
  birleştirilmez.

Yeni testler: 3 bağımsız karşılaştırma/kimlik testi + 3 gerçek handler
entegrasyon testi. Tam paket **611 test OK**, `git diff --check` temiz.
Gerçek dört Next paylaşımının tek bildirime indiği iddia edilmiyor; bunlar
sentetik regresyon sonuçlarıdır. Satıcı kimliğinin anahtara dahil olmaması,
mevcut yuvarlama sınırında gerçek küçük indirimlerin bastırılabilmesi ve
bilinmeyen duruma sahip farklı kaynakların ayrı kalabilmesi kalan risklerdir.

## Son regresyon ve dayanıklılık turu

Başlangıç: 611 test OK. Son: **625 test OK**.
Komut: `.venv/bin/python -m unittest discover -s tests`.
Yeni `tests/test_resilience.py`: 14 test OK (5 ayrıştırıcı/tüketici, 9 handler
akışı). `git diff --check`: temiz. Önceki beklentiler değiştirilmedi.

### Doğrulanmış iki hata ve minimal düzeltmeleri

1. `Fiyat: 1..299 TL` etiketli sayı regex'inin sayı başında durması sonucu
   1 TL/high dönüyordu. Para birimi önde olan bozuk sayılarda benzeri vardı.
   Etiketli/para-birimi-önde sayıdan sonra sayısal devamı reddeden ortak
   bitiş kontrolü eklendi. `1..299` ve `1,2,3` artık güvenilir fiyat değil;
   `₺99, stokta`, `Fiyat: 99. Kaçırmayın` gibi normal son noktalama,
   ondalıklar, binlikler ve Unicode boşluklar korunur.
2. `Fiyat: 300 TL\nKupon: KOD10` koşul çıkarımında `300 TL\nKupon`
   eşleşmesi kupon sözcüğünü tüketiyor, kod koşulu kayboluyordu. KOD10/KOD20
   mesajları tek teklif olabiliyordu. Sayısal kupon tutarı kalıbında TL ile
   kupon arasında yalnız yatay boşluğa izin verildi. Aynı satırdaki
   `100 TL kupon` değişmedi; yeni satırdaki kod artık ayrı koşul olarak kalır.
   Kupon kodu koşulu değişimi ayrı teklif kimliğidir; eski mesaj ezilmez.

Bu turda üretim değişiklikleri yalnız bu iki doğrulanmış hata içindir.
Dedup rezervasyonu, tolerans veya ürün durumu davranışı değiştirilmedi.

### Yeni testler

- malformed_number_prefix_is_not_a_price: bozuk sayıların bildirim/DM/
  offer_terms dahil bütün tüketicilerde reddedilmesi.
- missing_threshold_and_percent_only: eksik, yalnız eşik/yüzde/indirim ve
  low koşullu mesajların güven kapıları.
- valid_formats_and_trailing_punctuation: Türkçe sayı, TL/₺, NBSP/narrow-NBSP,
  geçerli etiketli yalın sayılar ve son noktalama.
- coupon_code_is_not_consumed_by_previous_price_line: satır sınırı hatası,
  iki farklı kod ve yatay Unicode boşlukları.
- multiple_products_are_not_split_and_can_be_ambiguous: iki eşdeğer aday low;
  güçlü etiketli aday ise high kalabilir. Ürün bazlı ilişki çözümü yoktur.
- metadata_changes_preserve_canonical_price_body_and_sources: başlık boşluğu,
  emoji, TL/₺, satıcı ve farklı kaynak değişikliklerinde tek bildirim;
  300 genel / 200 koşullu fiyat ayrımı, ilk gövde ve kaynak sayısı korunur.
- changed_coupon_condition_is_a_separate_offer: kod değişirse iki bildirim.
- poll_live_order_and_concurrent_same_source_claim: iki sıra ve aynı kaynak
  kimliğine eşzamanlı olaylar; her özgün kaynak mesajı yalnız bir kez sayılır.
- distinct_sources_wait_for_pending_send: gerçek gönderim async kapıyla
  bekletilir; diğer kaynağın aynı ürünü rezervasyonu paylaşır, tek gönderim.
- new_lower_price_after_pending_send_is_not_lost: pending sırasında 300->250
  gerçek indirim ayrı yeni bildirime dönüşür; yeni kayıt 250 olur.
- cache_loss_can_renotify_without_history_reload: cache silinirse yeni kaynak
  olayı yeniden bildirilebilir; kalıcı altyapı eklenmedi.
- pack_size_and_color_are_distinct_offers: paket/renk farkı ayrı kalır.
- late_low_confidence_copy_cannot_overwrite_reliable_price: low kopya mevcut
  güvenilir fiyatı/bildirim gövdesini ezmez.
- original_condition_and_coupon_details_survive_badging: ilk mesajın durum,
  kupon ve daha kapsamlı açıklamaları sade kopyayla silinmez.

### Hata değil, mevcut sınırlamalar

- Cache bellektedir. Açılış geçmişi yeniden yükleme mevcut korumadır; yalnız
  taranan pencere/limit içindeki tanınan ve düzenlenebilir bildirimleri kapsar.
  Cache temizliği, pencere aşımı veya geçmişin yüklenememesi tekrar bildirim
  oluşturabilir. Başarısız/silinmiş bildirimde fail-open davranış da korunur.
- Birleştirme bilgi sentezi değildir: ilk gövde korunur, kaynaklar/sayı rozette
  güncellenir. Sonraki kopyanın yeni açıklamaları gövdeye eklenmez. İlk kopya
  sade, sonraki kopya daha kapsamlıysa sonraki açıklamalar görünmeyebilir.
  Cache fiyat/koşul özetini taşır; confidence ayrıca saklanmaz. Üretilmiş fiyat
  kapısı compose_message içinde, DM kapısı price_matches içinde uygulanır.
- Çok ürünlü mesajlar ayrılmıyor. Örneğin `Pamuk Havlu / Fiyat:100 TL` ve
  `Cam Bardak 50 TL` aynı mesajdaysa güçlü etiketli 100 high seçilebilir;
  hangi ürüne ait olduğunu modelleyen ayrı veri yapısı yoktur. Eşdeğer iki
  farklı fiyat low olur, ama bu tüm çok ürünlü mesajları saptamaz.
- Mevcut koşul imzası bütün doğal dil kupon anlatımlarını çözmez. Etiketli
  kupon/kod, mevcut miktar ve üyelik kalıpları korunur; desteklenmeyen kod
  anlatımlarındaki değişiklikler ayırt edilemeyebilir. Yeni NLP kapsamı eklenmedi.
- Aynı başlığın yalnız boşluk/emoji/noktalama/kelime sırası gibi normalizasyon
  farkları birleşebilir. Yeni anlamlı kelimeler veya değişen model/varyantlar
  agresif fuzzy eşleştirmeyle silinmez; bazı gerçek tekrarlar ayrı kalabilir.
- Testler yerel gerçek üretim fonksiyonları/handler'larıyla çalışır; canlı
  Telegram/DM ağı veya tüm olası yarış sıralamaları için garanti değildir.

Önceki yedi kabul örneği, OGX/Uni Baby/Fropie/Busstier/E-Garaj/Bargello,
tolerans, durum ve mükerrerlik regresyonlarının tamamı tam pakette geçti.
PR açılmadı; kullanıcı onayı bekleniyor.

## Ek dar sınır kontrolü

Bu turda üretim kodu değiştirilmedi. Mevcut beklentilere dokunulmadan yalnız
`tests/test_resilience.py` genişletildi (4 yeni test metodu):

- `1,,299`, `1.,299`, `1,.299`, `12.34.56`, `12,34,56`, `1299..90`:
  etiketli, yalın etiketli, TL/₺ önde ve para birimi sonda biçimleriyle 30
  alt durum; hiçbiri güvenilir fiyat olarak tüketilmiyor.
- Altı geçerli Türkçe sayı (ondalık virgül, tek/çok binlik, NBSP/narrow NBSP)
  dört para birimi/etiket biçimiyle 24 alt durum; fiyat, eşik ve offer_terms
  doğrulanıyor. Önceden mevcut para birimsiz etiketli tutar üst sınırı
  10.000.000 korunuyor; bu limit kaldırılmadı.
- LF, CRLF, boş satır, girinti ve dört açıklama biçimi × iki kupon kodu:
  32 alt durumda kod korunuyor, önceki fiyat satırı kodu tüketmiyor,
  KOD10/KOD20 farklı teklif anahtarı üretiyor.
- CRLF/girinti/ek açıklamalarla KOD10 ve KOD20 gerçek handler yolunda
  iki ayrı bildirim oluşturuyor.

Başlangıç 625 test OK; son tam paket **629 test OK**; dayanıklılık dosyası
**18 test OK**. `git diff --check` temiz. Önceki kabul örnekleri ve dedup
regresyonları değişmeden geçti. PR açılmadı.
