# Değişiklik geçmişi (arşiv — güncel davranış için docs/ altındaki konu dosyalarına bak)

## 14. Bu güncellemeden sonra yapılacaklar

Bu sürüm beş liste düzenlemesini ve iki filtre komutunu sunar: `/ekle` veya `/cikar` →
seçim → değer (virgülle birden çok) → `/kaydet` ya da `/iptal`; filtreler için `/tgfiltreac` ve
`/tgfiltrekapat`; geçmiş istatistiği için `/analiz`.
Diğer teknik ayarlar `config.json` üzerinden yönetilir. Ayrıntılar [4. bölümde](../komutlar.md#4-telegram-komutları)
ve [5. bölümde](../komutlar.md#5-ayarları-telegramdan-düzenleme).

Bu repodaki güncel davranış değişiklikleri:

1. **"Fırsatı Gönderen" etiketi kaldırıldı; kaynak adı artık sade ve kalın.**
   Bildirimin en altında yalnızca hangi gruptan geldiği yazılır — "Fırsatı Gönderen"
   gibi bir açıklama ve hiçbir link yok, sadece grup adı kalın olarak durur
   (`source_footer`, varsayılan `true`).
2. **Komut temizliği eklendi** (`clean_commands`, varsayılan `true`). Komut sohbetinde
   yeni bir komut yazıldığında bir önceki komut ve yanıtı silinir; ekranda yalnızca son
   mesaj kalır. İndirim bildirimleri bu temizliğin dışındadır, asla silinmez.
   Ayrıntı: [4. bölüm → Komut temizliği](../komutlar.md#komut-temizliği-ekranda-yalnızca-son-mesaj).
3. **İşbirliği/reklam etiketleri ve WhatsApp linkleri temizlenir; eksik arama hizmetleri
   inline düğme olarak eklenir.** Hashtag'ler ve tam kelimeler hassas sınırlarla eşleşir;
   arama düğmeleri ileti gövdesini uzatmaz. Ayrıntı: [6. bölüm](../bildirimler.md#6-bildirim-kurulumu-telefona-uyarı-gelsin) ve [8. bölüm](../iletim-ve-linkler.md#8-gizli-bağlantılar-ve-fiyat-arama-düğmeleri).
4. **Bildirim botu token'ı yalnızca `NOTIFY_BOT_TOKEN` secret'ından okunur.**
   Eski config alanı ve güncel `config.json` değeri kaldırıldı; geçmişteki token'ı iptal et.
5. **Tekrar rozeti tek satıra indi.** İkinci ve sonraki kopyalarda ilk mesaja işlenen rozet
   `✅ 2 kaynakta paylaşıldı · teyitli fırsat` biçiminde **tek satırdır**; kaynak adları tek
   tek yazılmaz (5 kaynak alt alta yazılınca bildirim karışıyordu). Daha önce gönderilmiş
   mesajlardaki `📌 Kaynaklar: ...` satırı okunurken yine atlanır.
   Ayrıntı: [9. bölüm](../tekrar-birlestirme.md#9-tekrar-birleştirme-aynı-fırsat-tek-mesaj).
6. **Kanal tanıtımı ve hashtag satırları temizlenir.** `💚Whatsapp Önemli Fırsatlar`,
   `📲 WhatsApp grubumuz`, `#amazon #indirimalarmi` gibi satırlar bildirime hiç girmez;
   fiyat/ürün satırları ve içerik taşıyan satırlar korunur (dar kural — veri kaybı yok).
   Ayrıntı: [6. bölüm](../bildirimler.md#6-bildirim-kurulumu-telefona-uyarı-gelsin).
7. **Arama düğmeleri artık farklı kaynak formatlarında doğru ürün adını arar.** Fiyat/
   indirim satırları sorgudan ayıklanır; sıralama Google Alışveriş → Akakçe → Cimri'dir.
   Cimri araması `/arama?sort=price,asc&q=...` URL'sini kullanır (sorgu başlığın
   tamamıdır; ilk iki kelimeye kısaltma 12. maddeyle kaldırıldı). İşlem yereldir,
   bildirim gecikmesi eklemez; mesaj metnine dokunmaz.
8. **Kaynak erişim smoke testi eklendi.** `/kaynaktest` her çözülmüş kanaldan tek son mesaj
   okuyarak erişimi, üyeliği ve o oturumdaki canlı event sayısını raporlar; tüm arşivi
   indirmez ve kaynaklara test mesajı göndermez.
   Ayrıntı: [4. bölüm](../komutlar.md#4-telegram-komutları) ve [8. bölüm](../iletim-ve-linkler.md#8-gizli-bağlantılar-ve-fiyat-arama-düğmeleri).
9. **Bildirim sabit düzene geçti: başlık → `💰Fiyat: …` → `🔗 <ürün linki>` → kalan
   satırlar → `Mesajı Gör` → kaynak adı.** Başlık, fiyat ve ürün linki kaynak mesajdan
   alınır ve **alındıkları satırlardan silinir**; biçim gereği alınmayan satırlar
   altta aynen korunur. Böylece aynı bilgi iki kez
   görünmez, veri kaybı olmaz. `🔗 Ürün fırsat linki:` etiketi kaldırıldı; link satırı
   yalnızca ataç + adrestir.
   Ayrıntı: [6. bölüm](../bildirimler.md#6-bildirim-kurulumu-telefona-uyarı-gelsin).
10. **Kupon/duyuru istisnası ve fiyat satırı birleşmesi.** Sabit düzen artık yalnızca
    **ürün başlığı ve fiyat birlikte** varsa kuruluyor; kupon ve duyuru paylaşımları
    (başlık/fiyat yok) olduğu gibi iletilir. Kupon satırlarındaki tutarlar ve
    `200 TL üzeri` gibi eşikler fiyat sayılmaz. Fiyatla ilgili ek veri
    (`🏷️ 33 TL (3 Adet Alımda 22 TL)`) üst fiyat satırına taşınır, altta tekrar etmez.
    Cimri arama düğmesinin sorgusu ilk iki kelimeye indi (Cloudflare WAF bloğunu azaltmak
    için). Ayrıntı: [6. bölüm](../bildirimler.md#6-bildirim-kurulumu-telefona-uyarı-gelsin) ve
    [8. bölüm](../iletim-ve-linkler.md#8-gizli-bağlantılar-ve-fiyat-arama-düğmeleri).
11. **Blok sırası son hâli: fiyat bloğu, altına inen ürün linki ve çoklu paylaşım
    notu.** Bildirim artık blok blok okunur: başlık → `💰Fiyat` (fiyatla ilgili tüm veri
    ve `💬/📂/🛍️/🗓️` gibi fiyata bağlı satırlar) → `🔗 <ürün linki>` →
    `🔗 Mesajı Gör` → bloklara girmeyen satırlar ve gizli linkler → en altta kaynak
    grup adı. Ürün linki bir **satır değil üçüncü bloktur**: fiyat bloğunun altına
    iner, "hemen fiyat satırının altında" olmak zorunda değildir. Çoklu paylaşım notu
    (`📌 N kere paylaşıldı: <gruplar>`) fiyat satırının altına değil, **bu son bloğa**
    eklenip mesajı kapatır; sayı ve grup adları
    kaynaklardan toplanır. Eski `✅/🔥/🚨 … kaynakta paylaşıldı` rozetleri okunup
    yeni biçime taşınır (geriye dönük uyumluluk).
    Ayrıntı: [6. bölüm](../bildirimler.md#6-bildirim-kurulumu-telefona-uyarı-gelsin) ve
    [9. bölüm](../tekrar-birlestirme.md#9-tekrar-birleştirme-aynı-fırsat-tek-mesaj).
12. **Sabit düzen kaldırıldı; mesajlar artık OLDUĞU GİBİ iletiliyor.** 9-11.
    maddelerdeki başlık/fiyat/link ayıklama blokları silindi. Yeni düzen:
    **ürün başlığı en üstte → altında orijinal mesaj aynen → `🔗 Mesajı Gör`
    → en altta kaynak grup adı.** Kaynak mesajdan hiçbir satır silinmez.
    Tekrarlı paylaşımda kaynak grup adı satırı kaldırılır ve yerine
    `📌 N kere paylaşıldı: <gruplar>` notu yazılır.
    Ayrıca **Cimri arama düğmesi artık ürün başlığının tamamıyla arıyor**:
    iki kelimeye kısaltmanın sebebi sanılan WAF blokunun aslında Telegram'ın
    dahili tarayıcısından kaynaklandığı, adres dış tarayıcıda açılınca tam
    sorguyla çalıştığı görüldü (7 ve 10. maddedeki kısaltma kaldırıldı).
    Ayrıntı: [6. bölüm](../bildirimler.md#6-bildirim-kurulumu-telefona-uyarı-gelsin) ve
    [9. bölüm](../tekrar-birlestirme.md#9-tekrar-birleştirme-aynı-fırsat-tek-mesaj).
13. **Fiyat, ürün adıyla birlikte üst satıra taşındı: `56₺ - Ürün adı`**
    (`smart_header` açıkken). Eskiden 1. satır başlık, 2. satır fiyat idi; şimdi
    tek satırda **fiyat en başta**, sonra düz tire ayracı ` - `, sonra ad gelir —
    kesilen telefon bildiriminde fiyat ve adın ilk kelimeleri birlikte görünür.
    Kullanıcının kesin tasarımı: fiyatın önünde `💰` **yok**, yazım `56 TL` değil
    `56₺`, ayraç `—` değil `-`. Fiyat bulunamazsa "💰 Fiyat tespit edilemedi"
    yazısı yerine yalnızca **`❓`** konur (`❓ - Ürün adı`); ad da bulunamadıysa
    üst satır hiç kurulur. Aramalar bu değişiklikten **etkilenmez**: Google
    Alışveriş, Akakçe ve Cimri sorguları ile dedup anahtarı kaynak mesajdan
    ayıklanan fiyatsız addan kurulur; gönderilmiş bildirimin üst satırı okunurken
    de tutar/para birimi/ayraç/`❓` düşürülür (`split_header_line`,
    `header_search_title`, `_price_search_url`). Fiyatın kendisi ve güven kuralları
    (high/medium) değiştirilmedi; düşük güvenli tutar üst satıra taşınmaz. Dar
    kalıplarda fiyat korunur, adın kuyruğu `…` ile kırpılır — kalıp/uzunluk
    ölçümü Telegram gibi **UTF-16 kod birimiyle** yapılır (`𝐆𝐔𝐂𝐂𝐈`, `🔥` bir
    karakter ama iki birimdir; karakterle sayan kırpma sınırı aşırıyordu).
    Sıfır bir hata değildir: `Fiyat: 0 TL` yüksek, `ücretsiz/bedava` orta
    güvenlidir ve üst satırda `0₺` olur; `kargo bedava`/`deneme` fiyat sayılmaz
    (`❓`). Boş/bozuk tutarlar (`₺`, `1.2.99 TL`, `%50`) da `❓` bırakır.
    **İstisna — kupon/kampanya duyurusu:** ileti tam bir kampanya cümlesi
    kuruyorsa (eşik + indirim tutarı) ürün fiyatı **yoktur**; bu bir eksiklik
    değil iletinin türüdür, üst satıra `❓` yazılmaz. Fiyatı olan gönderi bu
    istisnaya girmez: güvenli tutar dalı önce çalışır, yani "kupon sanıp fiyatlı
    gönderiyi eleme" olmaz.
    `smart_header`
    kapalıysa düzen değişmeden kalır (yalnız başlık). Ayrıntı:
    [6. bölüm](../bildirimler.md#6-bildirim-kurulumu-telefona-uyarı-gelsin),
    [8. bölüm](../iletim-ve-linkler.md#8-gizli-bağlantılar-ve-fiyat-arama-düğmeleri) ve
    [9. bölüm](../tekrar-birlestirme.md#9-tekrar-birleştirme-aynı-fırsat-tek-mesaj).
14. **Ürün linki `👉 Ürün Linki 👈` yazısına gömüldü; içeriksiz yanıtlar eleniyor.**
    Alttaki `🔗 ✅ Satın Al ✅: https://…` gibi ham link satırı kalktı: ürün adresi
    her kaynakta (buton, gizli hyperlink, açık URL) aynı yazının içine gömülür,
    alıntı yedek olarak aynen kalır; kaynağın Google/Akakçe/Cimri arama butonları
    bot bildiriminde link satırı olmaz (klavyede durur; klavyesiz hesap kopyasında
    satır olarak yazılır). Çok linkli mesajlarda satır sınırı (4 ürün + 4 bağlantı)
    başka yerde taşınan adresi saymaz; sığmayan buton satırı gövdeyi kırpmak yerine
    klavyede kalır. Karakter sınırında hiçbir ek satırı, önceki düzende açılabilen
    bir bağlantıyı kapatmaz: fazladan `🔗` satırı gövdeyi kırpmaz, ürün satırı
    gövdedeki bağlantıyı kesmez. Bir fırsata açıklamasız görselle verilen yanıtlar ve
    `Yoğun ilgi görüyor 📣` duyurusu artık bildirim üretmez. Tekrar (📌) notu çok
    emojili mesajlarda bağlantıyı etiketinden kaydırmaz. Başlık/fiyat tarafında:
    cümle sonundan sonraki kelime tutarı nitelemez (`… adeti 99 TL. Farklı …` →
    99 TL), `1000TL` gibi bitişik tutar model kodu sayılmaz (kampanya satırı ürün
    adını geçemez) ve `Bu fiyat uzun sürmez!` gibi şablon sloganları ürün adı
    yapılmaz. Kök neden incelemesi:
    [docs/product-link-and-blank-reply-analysis.md](product-link-and-blank-reply-analysis.md).
    Ayrıntı: [6. bölüm](../bildirimler.md#6-bildirim-kurulumu-telefona-uyarı-gelsin) ve
    [8. bölüm](../iletim-ve-linkler.md#8-gizli-bağlantılar-ve-fiyat-arama-düğmeleri).
15. **Satır sırası değişti, `🔗 Mesajı Gör` satırı kaldırıldı (kullanıcı isteği).**
    Eski sıra: fiyat — ürün adı → alıntı mesaj → `👉 Ürün Linki 👈` →
    `🔗 Mesajı Gör: <t.me>` → kaynak grup adı. Yeni sıra: **fiyat — ürün adı →
    boşluk → `👉 Ürün Linki 👈` → boşluk → alıntı mesaj → boşluk → alt blok
    (kaynak grup adı ya da `📌 N kere paylaşıldı:` notu).** `🔗 Mesajı Gör` satırı
    tamamen kalktı; o adres **kaynak grup adının içine** gömüldü (ada dokunmak
    orijinal mesajı açar). Tekrar notunda **her grup adı kendi mesajına** bağlanır
    (`firsatz` → o grubun mesajı, `İndirimde Al` → o grubun mesajı …); eskiden
    yalnız ilk gelen kopyanın adresi taşınıyordu. Yer daraldığında rozet önce
    süsü atar ama **adları ve linklerini korur**; ad hiç yazılamıyorsa link
    `🔗 Mesajı Gör: <url>` satırı olarak geri gelir — hiçbir durumda adres kaybolmaz.
    Karakter/limit ölçümünde **bütçe bilerek sabit tutuldu**: kalkan satırın yeri
    ek bütçesinde ayrılmış kalır, böylece kırpma noktası ve `🔗` satırı seçimi
    değişmez; ileti yalnız bir satır kısalır, gövde hiçbir zaman eskisinden kısa
    olmaz (istek: yalnız kısalsın). Ürün satırı eskiden olduğu gibi **ürün satırı
    olmayan** çıktının açtığı hiçbir bağlantıyı kapatmaz. Altın referans
    (`tests/fixtures/golden/expected.json`)
    `--review` → `--regenerate` akışıyla yenilendi; farkın tamamı düzen
    değişikliğidir (kaybolan adres yok, kaybolan ürün satırı yok).
    Ayrıntı: [6. bölüm](../bildirimler.md#6-bildirim-kurulumu-telefona-uyarı-gelsin) ve
    [9. bölüm](../tekrar-birlestirme.md#9-tekrar-birleştirme-aynı-fırsat-tek-mesaj).

### Hızlı özet

- [ ] Güncel PR'ı `main`'e merge et
- [ ] Actions'tan workflow'u bir kez elle başlat
- [ ] `/ayar` → sade menü geliyor mu?
- [ ] `/tgfiltreac` ve `/tgfiltrekapat` → menü soruyor mu? Dahili/harici filtre ayrı ayrı kapanıyor mu?
- [ ] `/ekle` → kategori seç → `a, b, c` gönder → `/kaydet` ile GitHub'a yaz
- [ ] `/cikar` → listeyi gösteriyor mu? `1, 3` gibi çoklu çıkarma çalışıyor mu?
- [ ] `/analiz` → "🔎 Analiz başladı" mesajı ve iki istatistik geliyor mu? Kelimeleri
      harici listeye ekleyebiliyor musun?
- [ ] `/analiz 1000 tcl 65` → satırlar `ürün adı (tam) - fiyat` mı? Model kodu (ör.
      `65C7K`) görünüyor mu; eski/taksitli fiyat yerine indirimli fiyat mı yazıyor?
- [ ] Bildirim düzeni: başlık en üstte → `👉 Ürün Linki 👈` → mesaj OLDUĞU GİBİ →
      en altta kaynak grup adı (adres **ada gömülü**, ayrı `Mesajı Gör` satırı yok).
      Tekrarlı paylaşım gelince grup adı satırı kalkıp `📌 N kere paylaşıldı: <gruplar>`
      yazılıyor mu; nottaki **her ad kendi mesajını** açıyor mu? Cimri düğmesi tam başlıkla arıyor mu?
- [ ] Tek mesaj modu: gruba tek mesaj düşüyor mu (bot bildirimi), kopya siliniyor mu?
- [ ] Komut temizliği: `/durum` yaz → eski komut/yanıt silindi mi? Bildirimler duruyor mu?
- [ ] `/iptal` taslağı değiştirmeden siliyor mu?
- [ ] `GH_PAT`: süresi dolmadıysa dokunma; dolmuşsa *Actions: Read and write* ile yenile
