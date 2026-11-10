# Bildirilen üç örnek: kök neden ve sınırlar

Bu inceleme kullanıcının sağladığı mesaj metinleriyle ve sahte Telegram nesneleriyle
çevrimdışı yapılmıştır; gerçek kanallara veya aboneliğe erişim denenmemiştir.

* **Clear:** Fiyat doğru (224 TL). Ürün satırı 120 karakter üzeri olduğu için
  uzunluk puanı almaz; `📂 Kozmetik, Kişisel Bakım` kategori satırı kısa ve çok
  kelimeli olduğundan onu geçer. Kategori işaretli satır sadece başlık/arama
  adaylarından çıkarılır; gövde ve ürün adındaki kategori kelimeleri korunur.
* **Trust:** 512,05 TL ve `Yeni Fiyatı: 699 TL` güçlü fiyat adayı sayılır;
  farklı adaylar belirsiz olduğundan üst satır ❓ olur. Açık ikinci el/yenilenmiş
  kondisyon, ayrıca tek güvenilir teklif tutarı ve tek `Yeni Fiyatı` referansı
  birlikte varsa referans yeni ürün fiyatı olarak ayrılır. Kondisyon yoksa veya
  tutarlar belirsizse eski temkinli davranış korunur. Üstü çizili fiyat teklif
  kanıtı değildir. Başlıkta 512 TL gösterilmesi eski kuruş gösterim kuralıdır;
  çıkarılan gerçek fiyat 512,05 TL'dir.
* **OTL:** İsim ve 953,42 TL ayrıştırması doğru; `₺953` üst satırda kuruşu
  kesen mevcut gösterim tercihi, fiyat hesaplaması hatası değil. Kaynak linki
  inline URL butonunda; Bot API tesliminde smart mod önceden yalnız butonu
  koruyor ve metne yazmıyordu. Artık URL butonu metne de eklenir; zaten gövdede
  görünen URL tekrarlanmaz. Buton korunur, `off` tercihi değişmez. Telegram
  uzunluk sınırı nedeniyle ekin sığmadığı uç durumda ek düşürülebilir.

Davet linki, `CheckChatInvite` ile yalnızca mevcut üyeliği sorgular; bekleyen,
ücretli veya önizleme erişimi üyelik sayılmaz. Katılım/ödeme yapılmaz; geçerli
üyelik bulunursa yönetim akışı link yerine ID saklar. Canlı Telegram'da abonelik,
iptal edilmiş link, yeniden başlatma ve mesaj erişimi gerçek hesapla ayrıca
sınanmalıdır. `/kaynaktest` erişim kontrolü için kullanılabilir.
