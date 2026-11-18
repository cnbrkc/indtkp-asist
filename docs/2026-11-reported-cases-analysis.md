# Kasım 2026 bildirilen vakalar: ayrı kök nedenler ve dar düzeltmeler

Kullanıcı notu: **"HEPSİ AYNI TİP HATA DEĞİL"**. Aşağıda her bildirim kendi
sınıfına ayrılmıştır. Derin web aramasına gerek kalmadı: bütün belirtiler depodaki
`price_filter.py`, `bot.py`, `config.json`, mevcut testler ve 563 kayıtlık altın
referans ile çevrimdışı yeniden üretildi. Dış kaynağa başvurulmadı.

Yeniden üretim araçları: `price_filter.analyze` / `find_candidates`,
`bot.compose_message`, `bot.dedup_badge*`, `tests/test_golden_reference.py --review`
ve `python3 -m unittest discover -s tests -q`.

## Vaka tablosu

| # | Bildirilen örnek | Görünen belirti | Kök neden (ayrı) | Sınıf | Uygulanan dar çözüm |
|---|---|---|---|---|---|
| 1 | Dyson V8 Origin | `₺12.999 — Dyson V8 Origin Kablosuz Süpürge`; 11 kaynaklı rozette `FırsatlarCo` mesaj linki yok | Rozet adları `BADGE_SOURCE_SEPARATOR = " | "` ile birleştiriliyor; kaynak grubunun **kendi adında** da `" | "` geçince `dedup_badge_sources` / `dedup_badge_name_spans` tam adı parçalara bölüyor. `entry["links"]` ise TAM adla anahtarlı: parça için URL bulunamıyor. İkinci katman: `dedup_badge_links` `entry["links"]` içinde tek bir eşleşme bulunca hemen dönüp kalan adların `link_map` adreslerini atlıyordu. Üçüncü katman: ID ile çözülen özel süpergrupta `entity.username` boşsa `build_message_link` `requested` değerini kullanmıyordu. | Çapraz grup dedup / link | Canlı rozette tam ad listesi (`entry["sources"]`) `dedup_badge_entities`'e verilir; açılış taraması link ETİKETLERİNDEN tam adı `dedup_badge_names` ile geri kurar; `links` + `link_map` ad bazında birleştirilir; `requested` `@kullanıcıadı` yedek olarak kullanılır. Ürün linki zaten yakalanmıştı; eksik olan **kaynak mesaj** linkiydi. |
| 2 | Pella 3'lü Mikrofiber Temizlik Bezi | Üst satırda `❓`; gövdede `199TL … Set Fiyatı 162TL / Adet: 54TL` | `price_filter._relate_candidates` üç tutarı da `normal` sanıyor: `162TL` ve `54TL` ikisi de güçlü `normal` olunca `_heading_price_pool` belirsiz, güven `low`, başlık `❓`. Bu bir başlık/ürün adı hatası değil, **fiyat rolü** hatası. | Fiyat tespiti | Yalnız kupon/kod bağlamındaki `Set Fiyatı X` → `discounted` nihai fiyat; aynı satırda önünde başka tutar olan `/ Adet: X` → `unit`. `162/3` hesaplanmaz, `54` yükseltilmez. Sade `Adet: 54TL` iletisi eskisi gibi `54` ürün fiyatı olarak kalır. |
| 3 | High Peak Talos 3 Kişilik Çadır | Başlık `₺5.902`, gövde `💰 ₺5.902,26`; `Depo Fırsatları` linki yok | `extract_price` **doğru**: `5902.26 / ₺5.902,26 / high`. Başlıktaki kuruşsuz gösterim mevcut `build_header_line` davranışıdır (bk. `docs/november-reported-regressions.md`); tespit hatası değildir. Link kaybı #1'deki rozet/link haritası kök nedenine dayanır. | Görüntüleme (değiştirilmedi) + dedup link | Fiyat/başlık koduna dokunulmadı; link düzeltmesi #1 ile ortak. `₺5.902` bilinçli olarak korundu. |
| 4 | Solo Ultra Tuvalet Kağıdı 40'lı | `₺234`; `TERTEMİZ75`, `sepette kodu aktif edin`, `💰 234,00 ₺`; `Yarı Fiyatına Ürünler | Amazon Depo` linki yok | Fiyat ve başlık **doğru**. Eksik link: kaynak adının ta kendisi rozet ayracı `" | "` içeriyor; tam ad parçalanıp link anahtarı kayboluyor. Ayrıca `TERTEMİZ75` tek başına durmasına rağmen `_coupon_codes` tarafından doğrulanamıyor; bonus temizlik için "emin değiliz" durumudur. | Dedup link + bonus failsafe | #1 link düzeltmesi. Bonus: ürün adı satırı güvenle silinir, **doğrulanamayan** `TERTEMİZ75` satırı alıntıda AYNEN kalır. |
| 5 | Hepsiburada Kozmetik `KOZMODA150` | Kupon/kampanya duyurusu; görünür `https://app.hb.biz/...`; `🎯indirimalarmi | Önemli Fırsatlar 💥` linki yok | `extract_price` `None` döndürür: ürün fiyatı olmayan bir kupon duyurusudur; `❓` yazılmaması önceki düzeltmeyle zaten doğru. Eksik link yine `" | "` içeren tam kaynak adının bölünmesi. Bonus için `KOZMODA150`, önündeki `150 TL indirim kodu:` ile `_coupon_codes` tarafından kesin doğrulanır. | Kupon duyurusu (doğru) + dedup link + bonus | #1 link düzeltmesi. Bonus: tek başına `KOZMODA150` satırı alıntıdan çıkarılır; üst başlık, kampanya kimliği ve ürün linki ORİJİNAL gövdeden hesaplanmaya devam eder. |
| 6 | Calvin Klein LOW PRO CUPS WT SUEDE MG | `₺1.562`; gövdede `MODADA250`; aynı `Yarı Fiyatına Ürünler | Amazon Depo` linki yok | Fiyat/başlık **doğru**. Link kök nedeni #4 ile aynı (ad içinde ayraç). `MODADA250` satır içinde `"Kodunu sepette kullanın."` ile bitişik olduğu için tek başına kod satırı değildir. | Dedup link + bonus failsafe | #1 link düzeltmesi. Bonus: ürün adı satırı silinir; **bitişik** kod satırı korunur. |
| 7 | Tommy Hilfiger Essential Nautical Sneaker | `₺1.064`; `MODADA250`, `🔗 https://t.me/priceraybot`; aynı kaynak linki yok | Fiyat/başlık **doğru**. `t.me/priceraybot` bir bot/kanal adresidir, ürün linki değildir; ürün linki ekinin boş olması bu yüzdendir. Eksik kaynak linki #1/#4 kök nedeni. | Dedup link | #1 link düzeltmesi; görünür bot linkine dokunulmadı. Bonus: ürün adı satırı silinir, tek başına kod satırı olmadığı için kod temizliği yapılmaz. |

Özetle üç ayrı hata sınıfı vardır: **(A)** Pella'da fiyat rolü/belirsizlik,
**(B)** çoklu paylaşım rozetinde tam kaynak adının bölünmesi ve kısmi link
haritasının atlanması, **(C)** High Peak kuruş gösterimi gibi *kasıtlı* davranışın
hata sanılması. Buna ek olarak **(D)** bonus "ilk adım" isteği vardır; o bir hata
düzeltmesi değil, yeni ve fail-safe bir görüntüleme kuralıdır.

## Bonus / ilk adım: alıntıdan SATIR silme

Kullanıcı şartı: *"İSMİ TESPİT ETTİK YADA KOD SATIRINI TESPİT ETTİK …
SİLEBİLİRİZ. TESPİT EDEMEDİYSEK EMİN OLAMADIYSAK SİLMEYELİM KALSIN.
GARANTİ OLACAK YANİ."*

Yeni `clean_quote_duplicates` bayrağı (kodda varsayılan **kapalı**, bu deponun
`config.json`'unda **açık**) yalnızca `smart_header` açıkken ve görünen alıntı
üzerinde çalışır:

1. **Ürün adı:** `price_filter.title_source_line` yeni, dar bir doğrulayıcıdır.
   `extract_title` başlığı TEK bir satırdan, en yüksek ve rakipsiz puanla,
   kırpma/komşu genişletme olmadan çıkardıysa o satırı verir. Satırda fiyat,
   URL, etiket, yüzde, kupon kodu, metadata, eski rozet sözleri
   (`kaynakta/paylaşıldı`), kanal tanıtımı, açıklama/placeholder veya çıplak
   alan adı varsa `None` döner. İletide birden çok katı ürün adayı varsa da
   `None` döner.
2. **Kupon kodu:** yalnızca `_coupon_codes` tarafından DOĞRULANMIŞ ve tek başına
   duran `KOD` satırları `quoted_code_lines` ile seçilir. Solo'nun bağlamsız
   `TERTEMİZ75` satırı doğrulanamadığı için **kalır**.
3. **Link güvenliği:** silinecek satır URL/`@`/`#` veya herhangi bir link entity'si
   taşıyorsa silinmez. Satır silinince alıntı boşalacaksa hiçbir satır silinmez.
4. **Kimlik korunur:** başlık, fiyat, tekrar (dedup) anahtarı, arama sorgusu,
   ürün linkleri ve appendix kararı ORİJİNAL `sanitize_message` çıktısından
   hesaplanır. Yalnızca görünen `body`, `body_entities` ve bunların UTF-16
   konumları `strip_quoted_detected_lines` ile güncellenir
   (`_remove_spans` / `_remap_entities` / `_blank_line_spans`). Kaynak olay
   nesnesi değiştirilmez.

Böylece "eminsek sil, emin değilsek kal" kuralı kodda testlerle sabitlenmiştir:
`tests/test_reported_november_2026_regressions.py` içinde tespit edilen ad/kod
satırı → silinir; belirsiz kod, iki ürün adayı, linkli satır, bitişik kod,
alıntıyı boşaltacak silme → **değişmez**.

## Doğru tespitleri bozmama / minimum kod

* Pella kuralı yalnızca `kod/kupon … Set Fiyatı` ve `… / Adet:` kalıplarına
  uygulanır; parantezli birim, `adet başı`, `2 alana 1`, eski/indirim/history
  rolleri aynen korunur.
* Rozet düzeltmesi metin ayracını değiştirmez; `BADGE_SOURCE_SEPARATOR`, eski
  virgüllü rozetler ve `· tek grup` davranışı sürer. Canlı yolda adlar zaten
  `entry["sources"]` listesinde tam durduğu için ekstra ayrıştırma yapılmaz.
* `dedup_badge_links` artık iki haritayı **ad bazında** birleştirir; eski tek
  ad + `Mesajı Gör` yedeği korunur.
* `build_message_link` yalnızca `entity.username` boşken `requested` alanındaki
  açık `@kullanıcıadı` değerini yedek kullanır; temel grup için link üretmeyen
  kural değişmez.
* Alıntı temizliği varsayılan kapalıdır; açılması `config.json`, README ve altın
  referansın `BASE_CONFIG` kopyasıyla birlikte yapıldı.

## Doğrulama

* `python3 -m unittest discover -s tests -q` → 1251 test (1232 mevcut + 19 yeni) OK.
* `python3 tests/test_golden_reference.py --review` → `price` ve `preload`
  katmanlarında **değişiklik yok**; `prod`/`botdown` farkları yalnızca alıntıdan
  silinen ürün adı satırı, ona bağlı entity offset/kalınlık kaymaları ve rozet
  sığdırma değişimleridir. URL çoklu kümeleri korunur. Onay kodu
  `5f28625f81f8` ile `tests/fixtures/golden/expected.json` yenilendi.
* `test_characterization.py` rozet/açılış senaryoları alıntı temizliğinden
  bağımsız çalıştırılacak şekilde `clean_quote_duplicates: false` override'ı aldı;
  eski davranışı sabitlemeye devam eder.
* `API_ID/API_HASH/SESSION_STRING` dummy değerleriyle `python3 -u bot.py --check`
  → `yapılandırma geçerli ✅`; raporda `alıntı temizliği: açık` görünür.
* Regenerasyon sonrası `python3 tests/test_golden_reference.py --review`
  → `Fark yok: bugünkü çıktı dosyadakiyle birebir aynı.`

## Bilinen sınır

Geçmişte **hiç link entity'si üretilmemiş** eski bir rozet açılış taramasında
okunursa, o adın adresi iletinin içinde bulunmadığı için geri getirilemez. Yeni
paylaşımlarda adres `entry["links"]` / `requested` yedeğiyle yazıldığı için rozet
bundan sonra tam adın üzerinde link taşır. Bu, "kayıp veriyi uydurma" yapmama
kararıdır.
