# Bildirilen fırsatlar: kök nedenler ve güvenlik sınırları

İnceleme kullanıcının aktardığı metinler ve sahte Telegram nesneleriyle yapılmıştır;
kaynak kanalların gerçek link entity'leri, fiyatları veya ödeme koşulları canlı
olarak doğrulanmadı. Bütün fiyatlar kanalın iddiasıdır, hesaplama yapılmaz.

* Hayat ve Revani: aynı ürün ve normal fiyat ile çoklu alım birim fiyatı,
  önbellekte farklı tutar sayılıp her düşük tutarda yeni kayıt açılıyordu. Sadece
  kısa sürede gelen, aynı normalize ada ve açık eski normal fiyat + daha düşük
  çoklu alım tutarına sahip mesaj ilişkilendirilir. Eski kayıt daha önce çoklu
  alım/kupon/kondisyonluysa, restart geçmişindense, tutar/kimlik belirsizse veya
  kimliği bilinmiyorsa veya iki tarafta bilinen Amazon ürün kimlikleri farklıysa
  eski davranış sürer. Yeni bildirim ve rozet başarılı
  olmadan eski bildirim silinmez; silme hatası iki mesaj bırakır. Güncel fiyatın
  kaç adet koşuluyla geçerli olduğu kaynak gövdesinde açıkça kalır.
* GIGABYTE: uzun fiyat/taksit/özellik satırı uzunluk ve metadata cezalarıyla
  kaybeder; kısa CTA kazanır. Ürün adını taşıyan tek model kodlu hyperlink,
  UTF-16 sınırlarıyla okunur. Entity yoksa yalnızca başta fiyat ve parantezli
  taksit açıklamasından sonra gelen model kodlu katalog satırı ayrıca tanınır;
  CTA ve belirsiz birden çok link varsayılan ayrıştırmaya bırakılır.
* Fropie: `alımda adet 101` kuralın beklediği yalın `alımda 101` biçimiyle
  eşleşmiyordu; uzun kupon satırı fiyat etiketini de zayıflatıp iki adayı
  belirsizleştiriyordu. Açık çoklu alım birim fiyatı tanınır ve 101 TL seçilir;
  %100 bileşim ibaresi ürün adında korunur. İndirim yüzdeleri ürün adı yapılmaz.
* Servet: `(137 TL/Adet)` ikinci bağımsız fiyat sanılıyordu. Parantezli birim
  dökümü 413 TL paket toplamından ayrılır; 137 TL toplamın yerine konmaz.
* `Yoğun ilgi görüyor`: fırsat ismi gibi kabul edilip bildirim ve dedup kimliği
  üretiliyordu. Yalnızca tam bu içerik, medya/bağlantı/biçim entity'si/buton
  taşımıyorsa atılır. İçerik taşıyan eşdeğer mesajlar gönderilir ve bu ifade
  ürün kimliği olarak kullanılmaz.
* NIVEA: kaynak URL butonu ile aynı URL metne eklendikten sonra buton da
  korunuyordu. Teslim edilecek metinde görünür URL veya tıklanabilir entity varsa
  kaynak ürün butonu kaldırılır. URL kırpıldıysa veya metne alınmadıysa buton
  korunur; arama düğmeleri mevcut biçimde kalır. `off` link kaybına yol açmaz.
