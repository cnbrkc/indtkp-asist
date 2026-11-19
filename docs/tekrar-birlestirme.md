# Tekrar birleştirme

## 9. Tekrar birleştirme (aynı fırsat, tek mesaj)

İlk tekli fiyat ile daha sonra paylaşılan çoklu alım indirimi, yalnızca **üç dakika**
içinde ve birebir normalize ürün adı eşleştiğinde, yeni mesaj eski tekli fiyatı
ve daha düşük çoklu alım fiyatını aynı satırda açıkça içeriyorsa ilişkilendirilir.
Yeni bildirim önce gönderilir, kaynak sayacı üzerine işlenir; ancak bu ikisi
başarılı ve eski mesajın kimliği biliniyorsa eski bildirim silinir. Silme/düzenleme
başarısız olursa mesajlar korunur. Gerçek fiyat düşüşleri ve belirsiz eşleşmeler
eskisi gibi ayrı bildirimdir. Yeniden başlatma öncesi kayıtlar bu güvenli yükseltme
için kullanılmaz; üyelik/koşul farklılıkları ve eksik referans fiyatı otomatik
ilişkilendirilmez. Çoklu alım koşulu ve fiyatın bağlamı kaynak mesajda kalır.

Aynı indirim 3-5 kanal tarafından dakikalar içinde, çoğu zaman birebir aynı
başlıkla paylaşılır. Hepsini gruba atmak mesaj kalabalığı yapar; oysa tekrarlar
indirimin "gerçek ve teyitli" olduğunun işaretidir. Bu yüzden tekrar birleştirme
açıktır (varsayılan):

- **İlk kopya** her zamanki gibi gönderilir ve başlığı bellekteki listeye yazılır.
- **Sonraki aynı fırsatın kopyaları gruba ATILMAZ.** Onun yerine ilk mesajdaki
  **kaynak grup adı satırı kaldırılır** ve yerine çoklu paylaşım notu yazılıp mesaj
  orada kapanır (kullanıcı isteği: "hangi gruptan geldiğini kaldırıp 3 kez/2 kez
  paylaşıldı şu şu gruplar diye yazsın"). Not **tek satırdır**; kaç kez paylaşıldığı
  ve hangi gruplardan geldiği yazılır:

```text
Sıcak ÇAY

Sıcak ÇAY 5 TL
Kaçırılmayacak fırsat!

📌 3 kere paylaşıldı: firsatz, İndirimde Al, OnuAl   ← grup adının yerinde, son blok
```

Nottaki **her ad kendi mesajına gizli link taşır** (`firsatz` → o grubun mesajı,
`İndirimde Al` → o grubun mesajı …). Eski düzende fırsatın linki ayrı bir
`🔗 Mesajı Gör` satırındaydı; o satır kaldırıldı, adres adlara paylaştırıldı.

- Sayı 5'i geçince vurgu artar: `📌 N kere paylaşıldı: …` → `🚨 N kere paylaşıldı: … —
  KAÇIRMA!`. Telegram'da mesaj rengine müdahale edilemez; kalın + emoji,
  platformun sunduğu en güçlü vurgu kombinasyonudur. Çok dar medya açıklamalarında
  önce süs atılır (`· tek grup`, `— KAÇIRMA!`), **adlar korunur**; adlar hiç
  sığmıyorsa ve hiçbir ad link taşımıyorsa yalnızca sayı yazılır (adresi bilinen bir
  ad asla düşürülmez — düşerse o grubun mesaj linki kaybolurdu).
- Grup adları kaynaklardan toplanır (ilk kopyayı gönderen grup + sonraki kopyaları
  yakalayan gruplar); aynı grup birden fazla kez paylaşırsa adı bir kez yazılır. Sayaç
  paylaşım adedini göstermeye devam eder; listede yalnız bir grup varsa rozet
  `📌 2 kere paylaşıldı: FırsatZ · tek grup` diyerek ikinci bir grubun eksik olmadığını açıklar.
- **Geriye dönük uyumluluk:** eski `✅ 2 kaynakta paylaşıldı · teyitli fırsat` /
  `🔥 3 kaynakta ...` / `🚨 N KAYNAKTA PAYLAŞILDI` rozetleri hâlâ tanınır ve sökülür;
  daha eski sürümün ikinci satırdaki `📌 Kaynaklar: ...` listesi de atlanır. Böylece
  restart sonrası açılış taraması eski bildirimleri sayar ve not yeni biçimle yazılır.
- Eşleşme mesajın ilk satırına değil, fiyat/indirim/CTA satırlarını yerel olarak
  ayıklayan ürün başlığı sorgusuna göre yapılır. Harf büyüklüğü, noktalama, kelime sırası
  ve `6.2L` / `6,2 L` gibi sayı biçimi farkları normalize edilir; böylece fiyatı başta
  yazan kaynakla ürünü başta yazan kaynak eşleşebilir. Başlıksız (salt medya) iletiler
  birleştirilmez.
- Uzun/kısa katalog adları ancak sıralı en az 10 kelimelik güçlü bir önek ve sayısal/model
  kimliği varsa, son kelime kırılması açıklanabiliyorsa ve renk/kapasite/varyant çelişkisi
  yoksa birleşir. Uzun addaki ek sayılar yalnız `DST7030/20` gibi model koduna ait olabilir;
  farklı model, ölçü, kapasite, renk ve aksesuarlar ayrı fırsat olarak kalır.
- Üst satır artık `fiyat - ürün adı` taşırken de açılış taraması aynı anahtarı
  bulur: gönderilmiş bildirimin ilk satırı `split_header_line` ile ayrıştırılır,
  anahtar **fiyatsız** addan üretilir (canlı yoldaki `smart_dedup_title` ile birebir
  aynı imza). **Geçmiş biçimler yalnız okunur ve ayrı tutulmuştur:** (a) `56₺ - Ad`
  (bugünkü üretim), (b) `Ad` + alt satırda `💰 56 TL` (bir önceki sürüm),
  (c) gövdede `💰Fiyat: 56 TL` (en eski sabit düzen) — üçü de aynı fırsata bağlanır.
  Üst satırı `…` ile kırpılmış, yalnız `❓` ya da yalnız fiyat taşıyan (ürün adı
  içermeyen) iletiler **kimlik sayılmaz ve önbelleğe alınmaz** — gövdeden toplanan
  artık satırlar (ör. `👉 Ürün Linki 👈`, eski düzende `Mesajı Gör`) yanlış anahtar olurdu;
  canlı yol da başlıksız
  ileti için anahtar kurmaz. Restart sonrası tekrarlar yine tek mesajda birleşir.

**Hız ve limitler:** eşleşme bellekte yapılır; her iletide geçmiş taranmaz.
Rozet düzenlemeleri API isteğidir ve Telegram hız sınırlarına tabidir. Eşzamanlı
rozet işleri birleştirilir; `429` gelirse Telegram'ın `retry_after` süresi beklenir.
Bot API gönderimleri, düzenlemeleri ve özel yanıtlar aynı bekleme kapısını paylaşır.
Uzun `getUpdates` isteği bu kapıyı kilitlemez. GitHub'a ek istek atılmaz.

Açılışta hedeften son `dedup_scan_limit` ileti (varsayılan 100) okunur; **12 saatlik
pencere, 12 saatin tamamının geçmişten yüklendiği anlamına gelmez.** Süpergruplarda
kendi botumuzun mesaj ID'leri kullanılabilir. **Normal gruplarda** kullanıcı hesabının
MTProto mesaj ID'si ile Bot API mesaj ID'si aynı değildir; eski bot mesajları bu yüzden
önbelleğe alınmaz. Yeniden açılış sonrası tekrar bildirim olabilir; yanlış bir ID'ye
rozet yazıp yeni fırsatı susturmaktan güvenlidir. Bot bildirimleri açıkken yalnızca
hesabın gönderdiği eski kopyalar da başarılı bildirim sayılmaz.

**Ayarlar** (`config.json`): `dedup_enabled` (varsayılan `true`),
`dedup_window_hours` (normalize ürün sorgusu kaç saat "aynı fırsat" sayılsın, 1–72,
varsayılan 12; haftalar sonra aynı ürün yine indirime girerse YENİ fırsat sayılır),
`dedup_scan_limit` (açılış taraması, 0–100, varsayılan 100; `0` = tarama yapma).
`/durum` birleştirilen tekrar ve rozet sayılarını gösterir.
