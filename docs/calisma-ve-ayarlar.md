# Nasıl çalışır ve ayarlar

## 2. Nasıl çalışır ve sınırları

- `bot.py` çalıştığı ve kişisel hesap kaynaklara üye olduğu sürece, yapılandırılmış tüm
  kaynakların yeni Telegram mesajları aynı canlı event handler ile anlık alınır.
- **Geçmişe dönük mesajlar otomatik iletilmez.** Bot kapalıyken veya yeniden başlatılırken kaçanlar
  sonradan bildirim olarak backfill edilmez; `/analiz` ve `/kaynaktest` geçmişi yalnızca
  manuel kontrol/analiz için okur.
- Aynı başlıklı fırsatlar tek mesajda birleşir; tekrarlar gruba atılmaz, ilk mesajın en alt
  bloğuna `📌 N kere paylaşıldı: <gruplar>` notu işlenir
  ([9. bölüm](tekrar-birlestirme.md#9-tekrar-birleştirme-aynı-fırsat-tek-mesaj)).
- **GitHub Actions kalıcı sunucu değildir.** Job yaklaşık 5 saat 50 dakika çalışır, sonra
  yeniden başlatılır. Başlatmalar arasında kısa boşluklar olabilir.
- GitHub'ın scheduled workflow için resmi en kısa aralığı 5 dakikadır ve zamanlama
  yoğunlukta gecikebilir
  ([GitHub Docs](https://docs.github.com/actions/using-workflows/workflow-syntax-for-github-actions)).
  Bu yüzden "her dakika garanti tarama" Actions ile mümkün değildir.
- `GH_PAT` varsa bot, süre dolmadan ~30 dakika önce yeni bir Actions çalışması başlatır;
  `concurrency` ayarı eski job'u kapatıp yenisini devralır. Yine de token iptali, GitHub
  yoğunluğu veya hesap limitleri nedeniyle **mutlak 7/24 garanti yoktur**.
- Gerçekten kesintisiz çalışma istersen `bot.py`'yi ücretsiz bir **Oracle Cloud Free Tier
  VM**'de `systemd` servisi olarak çalıştır; Actions'ı yedek olarak bırak.

**Oracle VM ile kurulum (özet)**

1. Oracle Cloud Free Tier hesabı aç, Ubuntu VM kur.
2. Repo'yu clone et, `pip install -r requirements.txt`.
3. Secret değerlerini ortam değişkeni veya `.env` olarak tanımla (**`.env`yi GitHub'a gönderme**).
4. `deploy/telegram-filter.service` dosyasını `systemd`'ye tanıtıp servisi başlat.
5. VM yeniden başlarsa servis otomatik kalkar.

---

## 3. Ayarlar: `config.json`

| Alan | Tip | Açıklama |
|---|---|---|
| `source_chats` | liste | Dinlenen kanal/grup listesi: `@kullaniciadi` veya `-100...` ID. **Hesabın üye olmadığı kanaldan canlı mesaj gelmeyebilir; takipçi kapalıyken kaçan mesajlar geri oynatılmaz.** Erişimi `/kaynaktest` ile sınayabilirsin. |
| `destination` | sayı / `"me"` | Filtrelenen mesajların gideceği sohbet. `me` = Kayıtlı Mesajlar (**bildirim gelmez**); grup ID'si yazarsan oraya düşer. |
| `include_keywords` | liste | Aranacak kelimeler. Büyük/küçük harf farkı yoktur; `İ`/`I` doğru indirgenir. |
| `exclude_keywords` | liste | Bunlardan biri geçerse mesaj atlanır (harici filtre **açıkken**). |
| `include_enabled` | `true` / `false` | 🔎 **Dahili kelime filtresi açık mı?** `false` ise `include_keywords` yok sayılır. `/tgfiltreac` ve `/tgfiltrekapat` ile değiştirilir. Varsayılan `true`. |
| `exclude_enabled` | `true` / `false` | 🚫 **Harici kelime filtresi açık mı?** `false` ise hiçbir mesaj harici kelimelerle engellenmez. `/tgfiltreac` ve `/tgfiltrekapat` ile değiştirilir. Varsayılan `true`. |
| `match_mode` | `any` / `all` | Dahili kelimeler nasıl eşleşsin: `any` = biri yeterli, `all` = hepsi aynı mesajda. (Eski `forward_all` değeri hâlâ okunur: dahili filtre kapalı demektir; `/tgfiltreac dahili` yazınca `any`'ye döner.) |
| `copy_mode` | `forward` / `copy` | Eski alan. `delivery_modes` yoksa ilk denenecek yolu belirler. |
| `delivery_modes` | liste | Korumalı kanallarda sırayla denenecek iletim yolları: `forward → copy → media → text → link` ([7. bölüm](iletim-ve-linkler.md#7-iletim-zinciri-korumalı-kanallar)). |
| `max_media_mb` | sayı | `media` yolunda indirilecek en büyük medya (varsayılan 25, `0` = sınırsız). |
| `link_appendix` | `smart` / `all` / `off` | Gizli linklerin eklenme biçimi ([8. bölüm](iletim-ve-linkler.md#8-gizli-bağlantılar-ve-fiyat-arama-düğmeleri)). |
| `message_link` | `true` / `false` | Orijinal mesajın `t.me` adresini bildirime katar (nihai güvence; kapatman önerilmez). Adres **kaynak grup adının içine gömülür** (ayrı `🔗 Mesajı Gör` satırı yoktur); ad yazılamayan durumda `🔗 Mesajı Gör: <t.me linki>` satırı olarak geri döner. |
| `source_footer` | `true` / `false` | Bildirimin **en altına kaynak grup adını kalın** yazar. "Fırsatı Gönderen" gibi bir etiket yazılmaz; ad, orijinal mesajın adresine **gizli link** olarak bağlanır (ada dokunmak mesajı açar). Varsayılan `true`. |
| `notify_media` | `true` / `false` | Bildirim botu fotoğraf/videoyu da göndersin. |
| `clean_commands` | `true` / `false` | **Komut temizliği.** `control_chat`'te yeni bir komut yazıldığında bir önceki komut ve bot yanıtı silinir; ekranda yalnızca son mesaj kalır. İndirim bildirimleri bu temizliğin **dışındadır, asla silinmez.** Varsayılan `true`. |
| `clean_quote_duplicates` | `true` / `false` | **Alıntı temizliği (ilk adım).** `smart_header` açıkken üst satırda güvenle tespit edilen ürün adı satırı ile `_coupon_codes` tarafından doğrulanan tek başına kupon kodu satırı alıntıdan çıkarılır. Tespit düşük/şüpheliyse, satır link/etiket taşıyorsa veya satır silinince alıntı boşalacaksa **hiçbir satır silinmez** ([8. bölüm](iletim-ve-linkler.md#8-gizli-bağlantılar-ve-fiyat-arama-düğmeleri)). Başlık, fiyat, tekrar kimliği ve ürün linkleri daima ORİJİNAL mesajdan hesaplanır. Bu depoda `true`; alan yoksa kapalı. |
| `single_message` | `true` / `false` | **Tek mesaj modu.** Bildirim botu mesajı gruba attıysa, hesabın attığı kopya gruptan silinir; böylece her fırsat tek mesaj olarak kalır. Bildirim gidemezse kopya **silinmez**. Varsayılan `true`. |
| `dedup_enabled` | `true` / `false` | **Tekrar birleştirme.** Normalize ürün sorgusu aynı olan fırsatlar tek mesajda toplanır; tekrarlar gruba atılmaz, ilk mesajın son bloğuna `📌 2 kere paylaşıldı: <gruplar>` gibi **tek satırlık** not işlenir ([9. bölüm](tekrar-birlestirme.md#9-tekrar-birleştirme-aynı-fırsat-tek-mesaj)). Varsayılan `true`. |
| `dedup_window_hours` | sayı | Normalize ürün sorgusu kaç saat boyunca "aynı fırsat" sayılsın (1–72, varsayılan 12). |
| `dedup_scan_limit` | sayı | Açılışta önbelleğe alınacak son ileti sayısı (0–100, varsayılan 100; `0` = tarama yapma). |
| `control_chat` | sayı / `"me"` | Eski kontrol sohbeti; `private_control: true` iken yalnızca Kayıtlı Mesajlar yedeği aktiftir. |
| `private_control` | `true` / `false` | Komutları bildirim botunun özel sohbetinden al; grup komutlarını kapat. Bu depoda `true`; alan yoksa eski davranış. `NOTIFY_BOT_TOKEN` gerekir. |
| `dm_enabled` | `true` / `false` | Yalnızca dmdahil grubunu açıp kapatır; başlangıçta `false`. |
| `dm_keywords` | metin listesi | Dmdahil ifadeleri; mevcut uyumluluk için temizlenmiş mesajın tamamında blok eşleşmesi aranır. `/ekle` / `/cikar` → `dmdahil` → `/kaydet`. |
| `dm_exclude_keywords` | metin listesi | Ortak engel listesi: dmdahil ve dmfiyat'ı durdurur, yeni ürün adı/fiyat grubunu etkilemez. `/ekle` / `/cikar` → `dmharic` → `/kaydet`. |
| `dm_price_max` | sayı listesi (en çok 1) | `dmfiyat` maksimum gerçek ürün fiyatı; eşik ve altı gönderilir, dmdahil'den bağımsız fakat dmharic'e tabidir. |
| `dm_product_price_rules` | `ürün adı < fiyat` listesi | Yeni bağımsız grup. Ürün başlığı eşleşmesi ve güvenilir gerçek fiyat koşulu birlikte gerekir; varsayılan/eksik eski config alanı `[]` kabul edilir. `/ekle` / `/cikar` → menü 8 → `/kaydet`. |
| `admin_user_id` | sayı / liste | `control_chat` bir grupsa **zorunlu**: komutları yalnızca bu ID'ler çalıştırabilir. |
| `auto_restart` | `true` / `false` | `GH_PAT` varsa yenileme zincirini açar (bir sonraki açılışta geçerli). |
| `notify_on_start` | `true` / `false` | Eski uyumluluk alanı; artık dikkate alınmaz. Açılış/otomatik yenileme sessizdir; `/durum` ile kontrol et. |

Bildirim botu token'ı config'e yazılmaz; tek kaynağı `NOTIFY_BOT_TOKEN` ortam değişkeni /
GitHub Actions secret'ıdır ([6. bölüm](bildirimler.md#6-bildirim-kurulumu-telefona-uyarı-gelsin)).
`notify_bot_token` eski config anahtarı yok sayılır.

**Ortam değişkeni ile geçersiz kılma:** Aynı adların büyük harflisi (`SOURCE_CHATS`,
`DESTINATION`, `MATCH_MODE`, `LINK_APPENDIX`…) config.json'ın üzerine yazar. Detaylar
`.env.example` dosyasında. (Eski `APPEND_LINKS` anahtarı da çalışır: `true` → `all`,
`false` → `off`.)
