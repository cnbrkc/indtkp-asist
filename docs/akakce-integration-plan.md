# Akakçe takibi — uygulanan tasarım ve kabul kontrolü

## Kapsam

- Aynı Telegram botu, mevcut Actions job'u ve hesap sahibinin özel sohbeti.
- Akakçe ürün detay linki + kullanıcının elle girdiği hedef fiyat.
- Somut geçerli teklif minimumu ile ürünün TRY `AggregateOffer.lowPrice` tutarı eşleşir.
- Tüm satıcıları sayfalama, yıllık dip otomasyonu ve doğrudan mağaza okuması yok.
- Satıcı filtresi yok. Takip varsayılan kapalı.

## Bildirim sözleşmesi

1. Mevcut `build_header_line(header_price_text(...), title)` ile fiyat–ürün başlığı.
2. Boş satır, `Akakçe Takip Bildirimi`.
3. Bulunabiliyorsa boş satır ve fiyat/satıcıyla eşleşmiş satın alma linki.
4. Boş satır ve takip edilen Akakçe ürün linki.

Tek düz metin mesaj, link önizlemesi kapalı. Kuruş yalnız mevcut başlık davranışıyla
atılır; karşılaştırma kuruş tamsayısıyla yapılır. Teknik hatalar ayrı uyarılardır.
Üretimde alıcı doğrudan `SELF_ID` ile sabitlenir; kayıttaki başka bir chat_id alarmı
başka sohbete yönlendiremez. Mevcut `/test` hâlâ grup testidir, değiştirilmedi.

## Yalıtım

- Ana Telethon kanal handler'ları, filtre/dedup/teslim kodları değiştirilmedi.
- Mevcut özel bot poller'ına yalnız hesap sahibi için dar bir yönlendirme eklendi.
- Mevcut filtre taslakları komut olmayan mesajlarda öncelikli kalır.
- Komut handler'ında Akakçe HTTP isteği beklenmez; ayrı sınırlı iş kuyruğu kullanılır.
- Ağ okuması ayrı, sırları devralmayan süreçte; 15 sn HTTP ve 20 sn dış zaman aşımı.
- 3 MB sayfa sınırı, tek eşzamanlı okuma, host aralığı ve hata beklemesi vardır.
- Aynı bot token'ının mevcut hız sınırı kapısı kullanılır. İkinci getUpdates yok.
- Runner tamamen kapanması/bellek/ağ sorunu için mutlak izolasyon iddiası yoktur.

## Kalıcılık

Küçük takip JSON'u GitHub Contents API ile kaydedilir. Config Git işlemleri ve ürün
API yazımları ortak RLock altında sıralanır; ürün yazımı checkout'u kirletmez.
Kayıt başarısızsa ayar RAM'de geri alınır; yanlış kalıcı kayıt onayı verilmez.
Yeni runner güncel takip JSON'unu checkout eder. Dosya ve geçmiş public'tir.
Alarm gönderimi/GitHub yazımı atomik değildir; kesintide tekrar riski belgelenmiştir.
Bozuk takip JSON'u mevcut kanal botunun açılışını engellemez.

## Doğrulamalar ve kalan kontrollü deneme

- Önceki canlı Actions probe: güncel minimum/satıcı kullanıcı gözlemiyle eşleşti.
- Gerçek raporun JSON-LD ve ana HTML parçaları offline fixture olarak eklendi.
- Fiyat eşitliği, daha düşük yeni fiyat, tekrar bastırma, farklı satıcıdan link
  ödünç almama, query parametrelerinin korunması ve güvensiz link testleri vardır.
- Başlığın mevcut kanal fonksiyonuyla aynı olması, tam bildirim metni, boş satırlar,
  linksiz fallback ve sabit özel sohbet alıcısı test edilir.
- Eski regresyonlar, askıda okuyucu sırasında komut yönlendirme, bozuk kayıt,
  kuyruk doluluğu ve kalıcı kayıt başarısızlığı testleri vardır.
- Canlı Telegram'da hedef girme/alarm gönderimi ve doğal runner geçişi sonrası
  kalıcılık kullanıcıyla kontrollü olarak doğrulanmalıdır. README tam adımları içerir.

PR'ı ready yapmak otomatik merge/aktivasyon değildir. Aktivasyon varsayılan kapalı
kalır; merge sonrası güncel bot başladığında kullanıcı `/takipac` ile açar.
