# Akakçe takip entegrasyonu — uygulama öncesi güvenlik planı

Tarih: 2026-11-05. Durum: kapalı varsayılanlı ilk entegrasyon uygulandı; canlı Akakçe/Actions doğrulaması henüz tamamlanmadı.

Uygulama notu: takip kalıcılığı yerel checkout dosyasına yazmak yerine GitHub Contents API kullanır; mevcut config Git işlemleriyle ortak RLock altında sıralanır. Sonraki runner güncel takip JSON’unu checkout eder. Sentetik/offline testler canlı erişim yerine geçmez. Kullanım ve gerçek ilk sürüm sınırları README’nin Akakçe bölümündedir.

## Onaylanan kapsam

- Aynı repo, aynı Actions job'u, aynı Telegram botunun özel sohbeti.
- Kullanıcı Akakçe ürün detay linkini gönderir; bot hedef fiyatı sorar.
- Satıcı izin/engelleme listesi yok. Okunabilen tüm teklifler değerlendirilir; üyelik, kupon, kargo gibi koşullar varsa bildirimde belirtilir. Eksik koşullar uydurulmaz.
- Karşılaştırma kesin olarak fiyat < hedef şeklindedir; eşit fiyat alarm üretmez.
- Takipler ve hedefler GitHub'da küçük bir JSON dosyasıyla kalıcı tutulur. Dosya ve Git geçmişi public olacaktır; sır içermez.
- İlk sürüm yıllık dip fiyatın otomatik çıkarılmasına bağımlı değildir. Cimri ve doğrudan mağazalar kapsam dışıdır.

## Mevcut sistemde doğrulananlar

- telegram-monitor.yml: tek monitor job, timeout 350 dakika, yeniden başlatma hedefi 330 dakika.
- private_bot.poll_private_commands komut handler'ını sıralı await eder. Akakçe HTTP isteği bu handler içinde beklenmemelidir.
- bot.py on_control_message komut olmayan mesajları mevcut handle_pending_message akışına yollar. Yeni link/hedef akışı, mevcut filtre düzenleme taslaklarını ele geçirmemelidir.
- save_config, commit_and_push işlemini thread'e taşır. Yeni bir kalıcılık yazarı eklenirken ortak Git yazma kilidi olmadan paralel commit/rebase/push yapılmamalıdır.
- Yenileme workflow_dispatch ile yeni run başlatır; eski run iptal edilebilir. Sadece kapanışta kayıt yapmak yeterli değildir.
- Yerel Python 3.11 ortamında mevcut 781 unittest geçti. CI Python 3.12 kullanıyor. Bu sonuç henüz olmayan entegrasyonu veya canlı Akakçe erişimini doğrulamaz.

## İzolasyon tasarımı

1. Akakçe okuyucusu Telegram/Telethon ve bot.py import etmez.
2. Ağ işçisi aynı runner'da ayrı alt süreç olarak çalışır; kısa zaman aşımı, sınırlı yanıt boyutu, sınırlı iş kuyruğu, tek eşzamanlı istek ve artan bekleme uygular. Kontrolsüz thread/process üretimi yoktur.
3. Ana bot işçinin sayfa yanıtını komut işleyicisinde beklemez. Yerel mesajlaşma yalnızca sınırlı ve doğrulanan veri taşır; işçi sır ve bot token'ı almaz.
4. İşçi bozulursa yalnızca ürün takibi durur. Yeniden başlatma sınırlıdır; sürekli çöküşte devre kesici açılır, kanal dinleyicisi devam eder.
5. Tek getUpdates tüketicisi korunur. Akakçe komutları yalnızca mevcut yetkili özel sohbet yönlendirmesine eklenir.
6. Akakçe bildirim kuyruğu sınırlı ve kanal/DM kopya kuyruğundan ayrıdır. Aynı Telegram token'ının hız sınırları ortaktır; alarm yığılması sınırlandırılır ve mevcut gönderim kapısı kullanılır.
7. Açılış varsayılanı kapalıdır. Tek kapatma seçeneği ağ işçisini durdurur; mevcut kanal ayarlarını değiştirmez.
8. Mevcut dedup, kanal filtreleme, kaynak çözümleme ve teslim zinciri davranışları değiştirilmez.

Alt süreç, paylaşılan runner'ın bellek/CPU/ağ veya GitHub arızasına karşı mutlak izolasyon sağlamaz. Sıfır risk/kaçırmama garantisi verilmez.

## Kalıcılık

- Ayrı takip dosyası, şema sürümü, doğrulama ve atomik dosya yazımı.
- Ürün ekleme/hedef değiştirme/silme, kalıcı kayıt başarısızsa 'kalıcı kaydedildi' diye yanıtlanmaz. Başarısızlık açıkça belirtilir ve yeniden deneme imkânı sunulur.
- Mevcut config yazarı dahil tüm Git mutasyonları ortak kilitle seri hale getirilir. Yalnızca hedef dosyalar stage edilir.
- Aynı fiyat her sorguda Git'e yazılmaz. Küçük alarm durumu kontrollü sıklıkta saklanır; sınırsız HTML/ölçüm arşivi tutulmaz.
- Bozuk takip dosyası yalnızca yeni özelliği devre dışı bırakır; mevcut bot açılışını engellemez ve dosya sessizce boşaltılmaz.
- Yeni runner kayıtlı takipleri yükler. Kalıcılaştırılmamış son gözlemler kaybolabilir.
- Telegram gönderimi ile GitHub yazımı atomik değildir. Ani iptalde tekrar alarm/kaçırma arasındaki politika test edilip belgelenmelidir; exactly-once iddiası yoktur.

## Okuyucu güvenliği ve doğruluk

- Yalnızca HTTPS Akakçe ürün detay URL'leri; host tam eşleşmesi. Kullanıcı URL'si üzerinden localhost, özel IP, yabancı host veya kontrolsüz redirect okunmaz.
- Kaynak yapısı görülmeden CSS seçicisi/endpoint uydurulmaz. Ham yanıt fixture'ı ve runner erişim ölçümü gerekir.
- CAPTCHA, 403, 429, zaman aşımı ve eksik fiyat ayrı sonuçlardır; 0 TL'ye çevrilmez.
- TRY tutarları kuruş tamsayısıyla karşılaştırılır. Taksit, eski fiyat ve indirim tutarı satış fiyatı değildir.
- Satıcı filtresi yoktur. Koşullu tekliflerin koşulları korunur. Tüm teklifleri kapsama, sayfadan gerçekten elde edilen listeyle sınırlıdır.
- Akakçe güncelleme zamanı ile bizim okuma zamanımız ayrı tutulur. Okuma, mağazadaki anlık fiyat garantisi değildir.
- İlk öneri az sayıda üründe 5 dakika hedef aralıktır; doğrulanmış servis limiti değildir. Toplam host bütçesi, hatalar ve ürün sayısıyla aralık uzar. Engelde daha fazla yük bindirilmez.

## Yayına geçiş kapıları

- [ ] Kullanıcının gerçek örnek linklerinde ham HTTP yanıtı ve fiyat/teklif yapısı doğrulandı.
- [ ] Aynı URL'ler gerçek Actions runner'ında, sırları loglamadan denendi. Bu test geçmiş sonucu gösterir, gelecek erişimi garanti etmez.
- [ ] Fiyat, koşul, stok, bozuk yanıt, üyelik ve eksik satıcı fixture testleri geçti.
- [ ] İşçi askıda/çökmüşken kanal olayı ve /durum yanıtı çalışmaya devam etti.
- [ ] Mevcut /ekle, /çıkar, /kaydet, /iptal taslakları link/hedef mesajlarından etkilenmedi.
- [ ] Config kaydı ile takip kaydı eşzamanlı test edildi; değişiklikler kaybolmadı.
- [ ] Runner iptali, yeniden açılış, bozuk kayıt, GitHub kayıt hatası ve tekrar alarm senaryoları test edildi.
- [ ] 781 mevcut test ve yeni regresyonlar CI Python 3.12'de geçti.
- [ ] Önce kapalı entegrasyon, sonra bildirimsiz gölge test, ardından az ürünle alarm açılışı yapıldı.

Bu kapılar tamamlanmadan 'mevcut sistemi bozmadan çalışıyor' sonucu ilan edilmez.
