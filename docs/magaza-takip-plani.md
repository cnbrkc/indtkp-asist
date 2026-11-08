# MAĞAZA TAKİP PLANI — FİNAL (v4)

Repo: `cnbrkc/indtkp-asist` · Dal: `arena/4f0b6386-indtkp-asist` · Tarih: 2026-11-08
Bu belge **tek plan**. Önceki tüm plan/denetim dosyaları silindi; değerli bilgiler buraya taşındı.
**v4 farkı:** (1) Akakçe bulma işi en sona alındı, (2) "tüm sitelerdeki en uygun fiyat, tek alarm"
özelliği ayrı faz oldu, (3) derin GitHub taramasından gelen **gömülü JSON** yöntemleri eklendi.

---

## 0. Tek cümlede plan

> Akakçe hattına dokunmadan, **her mağazanın kendi ürün sayfasını** okuyan ikinci bir hat
> kurulur. Sen linkleri verirsin, bot o linkleri **aynı anda ve sürekli** izler, **sayfadaki
> satıcıların en uygun fiyatını** bulur ve **tek alarmla** haber verir. Akakçe'den otomatik
> bulma (eski F6/F8) **en sonda** — istenirse.

**Senin son sözün:** "Şu an nasıl Akakçe linki takip ettirebiliyorsam, bu sitelerin linklerini
kendim verdiğimde **tüm satıcılardaki en uygun fiyatı** sürekli takip edebileyim, hepsini aynı
anda." → Planın çekirdeği bu. Akakçe bulma = kolaylık, sonra.

---

## 1. Değişmez sözleşmeler (her fazda aynı)

### 1.1 Dokunulmazlar
- `product_tracker.py`, `akakce_worker.py`, `product_watches.json` → **DEĞİŞMEZ** (yalnız import).
- `config.json`'a **yeni anahtar yok**. `COMMAND_DESCRIPTIONS`/`HELP_TEXT`'e **yeni komut yok**
  (`tests/test_bot.py:259` kanonik komut demetini birebir karşılaştırıyor).
- Stdlib dışı bağımlılık yok. Fiyat geçmişi **commit edilmez** (repo public).
- Yeni özellik **varsayılan KAPALI**. Yeni kodun hatası kanal dinlemeyi ve Akakçe takibini
  **asla** durdurmaz (`try/except` + log).

### 1.2 C1–C7 okuma sözleşmeleri
- **C1 İzolasyon:** okuma ayrı süreçte (`-I`), sırlar miras alınmaz (yalnız `PATH/LANG/SYSTEMROOT`).
  Not: izole modda `sys.path[0]` **stdlib zip** olur, script klasörü eklenmez (`-E -s` devrede) →
  worker kendi klasörünü `sys.path.insert(0, ...)` ile ekler.
- **C2 Kimliksiz tek istek:** giriş yok, çerez yok, oturum yok, **yönlendirme reddi**
  (`NoRedirect`), tek `GET`, 3 MB gövde / 15 sn HTTP / 20 sn süreç.
- **C3 Yapısal veri önceliği:** JSON-LD → **gömülü JSON** (`__NEXT_DATA__`,
  `__PRODUCT_DETAIL_APP_INITIAL_STATE__`, `__INITIAL_STATE__`) → anlamlı DOM.
  Otomatik üretilen CSS sınıf adlarına güvenilmez.
- **C4 Çapraz doğrulama:** iki bağımsız kaynak tutmazsa okuma **başarısız** (fail-closed).
- **C5 Fail-closed + nezaket:** hata/engelde bildirim yok; host bazlı min aralık; üstel geri çekilme.
- **C6 Idempotent alarm:** `alert_price` ile tekrar bastırma; kalıcı yazım başarısızsa RAM'de geri al.
- **C7 Test disiplini:** gerçek sayfadan fixture (sentetik değil) + kısmi/bozuk JSON +
  "okundu ama doğrulanamadı" senaryosu.

### 1.3 İş kararları (senin verdiğin)
| Konu | Karar |
|---|---|
| Fiyat | **Liste + sepette birlikte** okunur, bildirimde ikisi yazılır; **alarm sepette fiyata göre** |
| Sepette okunamazsa | Liste fiyatı + "(sepette fiyat okunamadı)" — sessizce değiştirme yok |
| **Satıcı** | **Sayfadaki satıcıların en düşük fiyatı.** Satıcı kilidi yok; bildirimde satıcı adı yazılır |
| Birden çok mağaza | **Aynı ürün, tüm mağazalar, tek alarm** (en uygun fiyat) — bkz. F8 |
| n11 / Pazarama `?magaza=` | **KORUNUR** (fiyat satıcıya bağlı; atmak yanlış satıcıyı takip ettirir) |
| MediaMarkt / Teknosa `?shopId=` | **ATILIR** (robots'ta yasak; yalnız mağazanın kendi satıcısı) |
| Trendyol `?merchantId=` | **KORUNUR** (satıcıyı belirler) |
| Otomatik kabul | **Yok.** Bot aday gösterir, **sen onaylarsın** |
| Kargo | Mağaza hattı **kargo hariç**; bildirimde "kargo hariç" ibaresi **zorunlu** |
| Ürün kimliği | Akakçe ürün ID + seri kodu; **EAN/GTIN bonus** (MediaMarkt veriyor — F1'de ölçülür) |
| Fiyat geçmişi | Tutulmaz (Akakçe kendi geçmişini veriyor; repo public) |
| Akakçe sınırı | 30 ürün / 300 sn (mevcut, değişmez) |
| Mağaza sınırı | **30 ürün**; ürün başına `speed (auto/acil/normal/ekonomik)`; 5 dk–1 saat; host başına 1 istek/20 sn |

---

## 2. Mağaza × yetenek matrisi (ölçülmüş, 2026-11-08)

| Mağaza | Doğrudan takip | Fiyat kaynağı (öncelik sırası) | Not |
|---|---|---|---|
| **MediaMarkt** | ✅ | JSON-LD `offers.price` → `[data-testid="price-current"]` / `.price-box` | Taksit `₺22.499,75` ve `+18.000 puan` **aynı sayfada** (D6 tuzağı). `?brand=TCL` kategori sayfası robots'ta serbest. **EAN değeri sayfada var** (kaynak: `2scraper/mediamarkt-scraper`) |
| **Teknosa** | ✅ | JSON-LD → görünen `89.999 TL` | `?shopId=` yasak; fiyat gün içinde oynak |
| **Hepsiburada** | ✅ (riskli) | **JSON-LD → `__INITIAL_STATE__` → meta** | **Önemli:** `www.` **olmadan** + `Referer: https://hepsiburada.com/` Akamai korumasını atlatıyor (kaynak: `kaanvp/fiyat-takip`) → F2'de böyle uygulanacak. `SEPETTE` + liste birlikte geliyor |
| **n11** | ✅ | **JSON-LD → gömülü varyant JSON → meta → CSS** | Gömülü JSON'da **satıcı başına fiyat** var: `"outOfStock":false … "price":"… TL"` (kaynak: `kaanvp/fiyat-takip`) → **en düşük satıcı buradan çıkar** |
| **Pazarama** | ✅ | JSON-LD → `[data-testid="price-current"]` / `.price-current` | robots `Allow: /*?magaza` |
| **Trendyol** | ✅ (duvar riski) | **`__PRODUCT_DETAIL_APP_INITIAL_STATE__` gömülü JSON** (`product.price.discountedPrice.value` / `sellingPrice.value`) → `__NEXT_DATA__` → JSON-LD → CSS | Coğrafya duvarı (`/en/select-country?cb=…`) görülürse Akakçe Trendyol satırına düşülür. Kaynak: `kaanvp/fiyat-takip`, `msakirc/kutai` |
| **Amazon TR** | ✅ (korumalı) | `#corePrice_feature_div .a-offscreen` → `#corePriceDisplay…` → JSON-LD `offers.price` | `.com.tr` fiyat veriyor (bugün ölçtüm). **2. el tuzağı:** görünen fiyat "İkinci El" idi → yeni/2. el filtresi şart. robots: `User-agent: *` için `/dp/` serbest, AI botları ismen yasak |
| Pttavm / idefix / Evkur | 🟡 Sonra | JSON-LD | Akakçe'de zaten listeliler; adaptör kolay ama öncelik değil |

**Kritik kapsam gerçeği:** TCL 65C7K'da Akakçe **"4 satıcı içinde kargo dahil en ucuz"** diyor →
n11, Pttavm, Hepsiburada, Pazarama. Trendyol/Amazon/idefix/Teknosa/MediaMarkt **o üründe yok**.
Yani "Akakçe'den 8 mağaza linki" 4/8 çıkar; ama **senin verdiğin linkte 8/8** mümkün.

---

## 3. Yol haritası — 10 faz (Akakçe bulma en sonda)

| Faz | Ad | Ne yapar | Telegram'da fark | Risk |
|---|---|---|---|---|
| **F1** | Probe | 7 mağazayı ölçer: JSON-LD, **gömülü JSON**, **kaç satıcı fiyatı var**, 2. el var mı → gerçek fixture | yok | **sıfır** |
| **F2** | Okuyucu | `store_worker.py`: Hepsiburada (non-www+Referer), MediaMarkt, Teknosa. 4 kademeli fiyat merdiveni | yok | düşük |
| **F3** | Takipçi | `store_tracker.py` + `store_watches.json` (boş dosya commit'li) | yok | orta |
| **F4** | Bağlantı | `bot.py` ~20 satır | **LİNK YAPIŞTIR → TAKİP** | **yüksek** |
| **F5** | n11 + Pazarama | n11'de **satıcı başına fiyat** → en düşük satıcı; sepette fiyat | 5 mağaza | orta |
| **F6** | Trendyol | Gömülü state + `__NEXT_DATA__`; duvar → Akakçe yedeği | Trendyol takipte | orta |
| **F7** | Amazon TR | `.a-offscreen` + **yeni/2. el filtresi** + sıkı hız + kill switch | Amazon takipte | **orta-yüksek** |
| **F8** | **En iyi fiyat motoru** | Aynı ürün N mağazada → **tek alarm, en ucuz mağaza+satıcı** | **"hepsini birden"** | orta |
| **F9** | Akakçe satıcı linkleri | `/magazatakipbul <akakçe linki>` → satıcı linkleri listesi | kolaylık | düşük |
| **F10** | Çoklu seçim | `1,3,7` → sırayla hedef, `hepsi 55000` | kolaylık | düşük |

**Kural:** her faz **ayrı commit**, kendi testleriyle. Testler geçmeden sonraki faza geçilmez.
**Bekleme yok** — fazlar sırayla tek akışta ilerler.

---

## 4. Faz faz detay

### F1 — Probe (ölçüm)
`.github/workflows/store-probe.yml` (yalnız `workflow_dispatch`, `permissions: {}`, secret yok,
Telegram yok) + `tools/store_probe.py` (tek dosya, stdlib).
**Güvenlik:** host allowlist (`www.mediamarkt.com.tr`, `www.teknosa.com`, `www.pazarama.com`,
`www.n11.com`, `www.hepsiburada.com`, `www.trendyol.com`, `www.amazon.com.tr`) + **https zorunlu**
+ `NoRedirect`; allowlist dışı satır **reddedilir**. Siteler arası ≥5 sn, URL başına tek istek.
**Her URL için çıktı:** HTTP, yönlendirme, boyut, captcha/engel işareti, **JSON-LD blok sayısı +
`offers.price`/`priceCurrency`**, **gömülü JSON var mı** (`__NEXT_DATA__`,
`__PRODUCT_DETAIL_APP_INITIAL_STATE__`, `__INITIAL_STATE__`, `"price":"… TL"` deseni),
**gömülü JSON'dan çıkan satıcı sayısı + fiyat listesi**, görünen tüm fiyat adayları
(ana fiyat / taksit / puan / garanti / **2. el**), hangisi JSON-LD ile eşleşiyor, **EAN var mı**.
**Ek deneme:** Hepsiburada için `www.`li ve `www.`siz URL **ayrı ayrı** ölçülür (Akamai farkı).
**Hız ölçümü:** her siteye **3 istek, aralarında 5 sn** → yanıt süreleri (ms) + `429/403` oranı
kaydedilir. Bu veriyle **13.6.2'deki `min_gap` tablosu tahminden ölçüme** çevrilir.
**Çıktı artefaktı:** `GITHUB_STEP_SUMMARY` tablosu + site başına ≤20 KB kırpılmış JSON/HTML →
**F2/F5/F6/F7 fixture'larının tek kaynağı**.
**Kabul:** 7 site tablosu dolu; Trendyol ya fiyat ya `country_wall`; Amazon ya **yeni** fiyat ya
`no_new_offer`; **her satırda "kaç satıcı fiyatı bulundu" sayısı** yazılı.
**Geri alma:** workflow dosyasını sil.

### F2 — Okuyucu (`store_worker.py`)
`sys.path.insert(0, <kendi klasörü>)` → `akakce_worker`'dan `cents, target_cents, purchase_url,
Scripts, NoRedirect, MAX_BODY` import.
Registry `{host: adaptör}`; sözleşme: `QUERY`, `product_url(raw)`, `parse(raw_html)`, `verify(parsed)`.
**Fiyat merdiveni (her adaptörde aynı sıra):** ① JSON-LD `offers` ② gömülü JSON
③ `data-testid`/anlamlı DOM ④ meta. **Satıcı listesi** çıkarılabiliyorsa (n11, Hepsiburada)
`offers[]` içinde döndürülür, en düşüğü seçilir.
**verify:** JSON-LD kuruş değeri **ana fiyat bloğunda aynen geçmeli**; taksit (`22.499,75`),
puan (`+18.000 puan`), ek garanti (`3.639 TL`), `4 x …` **fiyat adayı değil**. Para birimi TRY,
tek Product, stok var. Hepsiburada'da liste ≥ sepette.
**CLI:** `python -I store_worker.py <url>` → `{"ok":true,"result":…}` / `{"ok":false,"reason":…}`.
**Test:** `tests/test_store_worker.py` + `tests/fixtures/stores/**` (**F1 artefaktından**; yoksa
faz **BLOKE**, sentetikle devam yok). Senaryolar: fiyat yok, iki Product, TRY değil, stok yok,
JSON-LD ≠ görünen, **taksit/puan karışmıyor**, engel sayfası, güvensiz URL'ler (`http`, farklı
host, userinfo, port, localhost, `host.evil.test`), query politikası.
**Geri alma:** dosyayı sil.

### F3 — Takipçi (`store_tracker.py`) + başlangıç dosyası
`store_watches.json` **bu fazda commit edilir**: `{"version":1,"enabled":false,"items":[]}`
(yoksa `persist_products`'ın `GET`+`sha` adımı ilk kayıtta hep başarısız olur).
`StoreTracker(path, api, persist, reader=read_store, *, owner_id, header_formatter)` —
`product_tracker.py` örnek alınır, **ona bağlanılmaz**; `money`, `clean_title` import edilir.
**En fazla 30 ürün** (Akakçe ile aynı; bkz. 13.5 — sınır ürün sayısı değil, **host bazlı istek aralığı**).
Kayıt: `url, store, title, target, chat_id, alert_price, group, speed, interval, fail_streak, next_check`.
**Zamanlama:** sabit tur yerine **her ürün kendi `next_check` saatinde** okunur;
`speed=auto` → hedefe yakınlığa göre 5 dk–1 saat arası; host başına **1 istek / 20 sn** token bucket;
`403/429/CAPTCHA` → aralık iki katı (max 6 saat) + tek uyarı.
**`handle(event, raw, *, existing_pending=False)`** — `existing_pending and komut değilse → False`.
Allowlist dışı link / bilinmeyen komut → `False`.
**Komutlar:** `/magazatakip`, `/magazatakipac`, `/magazatakipkapat`, `/magazatakipler`,
`/magazatakipsil N`, `/magazatakipiptal`, `/magazatakipyardim`.
**Akış:** link kuyruğa (komut işleyicide **ağ beklemesi yok**) → hedef fiyat → kaydet.
İdempotent alarm, kayıt hatasında RAM'de geri al, okuma hatası ≠ bildirim hatası, 3 ardışık
hatada tek uyarı, üstel geri çekilme.
**Bildirim:** `build_header_line` + boş satır + `{Mağaza} Takip Bildirimi · kargo hariç` +
boş satır + link. Tek düz metin, önizleme kapalı, 4096 UTF-16 kontrolü.
**Test:** `tests/test_store_tracker.py` (TrackerTests deseni): kuyruk, taslak, alarm + bastırma,
geri alma, bozuk dosya izolasyonu, hung reader yönlendirmeyi bloklamaz, iptal, 10 sınırı,
kapalıyken okuma yok, `existing_pending` kuralı.

### F4 — `bot.py` bağlantısı (en riskli, en küçük diff)
Mevcut tracker kurulumundan **sonra**, **kendi `try/except`'iyle** `StoreTracker(...)`.
Hata olursa **yalnız log**.
Yönlendirme (~7213, owner-only blok): `pending_state = peek_pending(pending_key(event))`
**bir kez** hesaplanır, **her iki takipçiye aynı** `existing_pending` geçirilir. Sıra: mağaza
taslağı → `tracker.handle` → `handled` değilse `store_tracker.handle`.
**Pending temizliği:** `handled` sonrası `stage == "webdetakip"` → `drop_pending` mantığı mağaza
takipçisi handled olduğunda **da** çalışır. `/iptal` + `/magazatakipiptal`. `private_tasks`'a
`store_tracker.run()` — **ayrı workflow YOK**, aynı süreç (aksi halde Telegram 409 çakışması).
**Test:** Akakçe linki eskisi gibi; mağaza linki `store_tracker`'a; bilinmeyen komut eskisi gibi;
kurulum patlarsa tracker + kanal dinleme çalışıyor; iki taslak varken düz metin doğru yere;
`/ekle` taslağı açık kalmıyor. **Ön koşul:** `handled=False` sonrası "bilinmeyen komut" cevabı bozulmuyor.

### F5 — n11 + Pazarama
n11: **gömülü varyant JSON** → `"outOfStock":false … "price":"… TL"` deseni ile **satıcı başına
fiyat**; en düşük satıcı seçilir, adı bildirime yazılır. `QUERY="keep"` (`?magaza=`).
Pazarama: JSON-LD → `[data-testid="price-current"]` → `.price-current`/`.price-box`; `QUERY="keep"`.
**verify:** liste ≥ sepette, ikisi de > 0, aynı para birimi; sepette yoksa liste +
"(sepette fiyat okunamadı)".
**Test:** fixture + query politikası + "sepette yok" + **en düşük satıcı seçimi** (iki farklı
satıcı fiyatıyla).

### F6 — Trendyol
**Fiyat merdiveni:** `window.__PRODUCT_DETAIL_APP_INITIAL_STATE__` gömülü JSON
(regex: `window\.__PRODUCT_DETAIL_APP_INITIAL_STATE__\s*=\s*(\{.+?\});`, sonra
`product.price.discountedPrice.value` → `sellingPrice.value`) → `__NEXT_DATA__` → JSON-LD → CSS.
**Duvar tespiti:** final URL `select-country` içeriyorsa veya gövdede `Welcome to Trendyol` /
`Please select your country` varsa → `ok:false, reason:"country_wall"`, **alarm yok**.
**Otomatik yedek:** duvar sürerse Akakçe'nin **Trendyol satırı** kullanılır (15 dk gecikme).
**Mesaj:** duvar sürerse "/magazatakipac Trendyol kapatıldı, Akakçe satırından devam" (sessiz düşüş yok).
**Kabul:** F1'de Trendyol ya "fiyat okundu" ya `country_wall` — belirsizlik kabul değil.

### F7 — Amazon TR (korumalı)
**ASIN doğrulama:** `/dp/<ASIN>` veya `/gp/product/<ASIN>`; https, host `www.amazon.com.tr`,
`NoRedirect`, tek `GET`, 3 MB.
**Fiyat:** `#corePrice_feature_div .a-offscreen` → `#corePriceDisplay_desktop_feature_div` →
JSON-LD; **aynı kuruşta** olmalı (C4).
**🔴 Yeni/2. el filtresi (kritik):** yalnız **"Yeni"** fiyatı kabul. `İkinci El`, `Amazon Depo`,
`Yenilenmiş`, `Kullanılmış`, `Yeni Gibi` etiketli fiyatlar **reddedilir**. Yeni yoksa
`ok:false, reason:"no_new_offer"`, alarm yok. *(Bugün ölçtüğüm tek fiyat "İkinci El" idi — tuzak gerçek.)*
**Koruma:** ürün başına **6 saatte 1** okuma, günde ≤6 istek; `403/503/CAPTCHA` → adaptör
**kendini kapatır** (`enabled=false` + tek uyarı), diğerleri etkilenmez.
**Yedek:** okunamazsa `🔔 Amazon: <ürün> — fiyat okunamadı (yalnız 2. el teklif var). Kontrol: <link>`

### F8 — En iyi fiyat motoru (**senin çekirdek isteğin**)
**Amaç:** Aynı ürünün N mağazadaki linkini ver → bot hepsini **aynı anda** izlesin,
**en uygun fiyatı** bulsun, **tek alarm** göndersin.
**Tasarım (yeni dosya `store_group.py`; `product_tracker.py`'ye dokunulmaz):**
- `store_watches.json` item'larına `group` alanı eklenir (şema bizim dosyamız, `config.json` değil).
  `{"url": ..., "store": "n11", "group": "tcl-65c7k", ...}`
- Grup alarm kuralı: her turda grubun **tüm üyeleri** okunur → `min(price)` → koşul:
  `min < target` **ve** `min < group_alert_price` → **tek bildirim**:
  ```
  ₺148.999 — TCL 65C7K Premium 4K Ultra HD 65" …
  En uygun: n11 · satıcı: novvo · sepette 148.999,00 TL
  Diğerleri: Hepsiburada 152.400 (Teknolojiankara) · Trendyol 155.000 (NOVVO) · Amazon okunamadı
  Mağaza Takip Bildirimi · kargo hariç
  https://www.n11.com/urun/…
  ```
- Tek mağaza okunamazsa **grup alarmı durmaz**; bildirimde "okunamadı" satırı görünür (fail-closed
  yalnız o mağaza için; diğer mağazalar geçerli).
- Aynı ürünün birden fazla linki **elle** gruplanır (`/magazatakipgrup <grup> <link>` ya da
  `/magazatakip` sırasında "bu ürünün diğer mağaza linki var mı?" sorusu). Otomatik eşleştirme
  **yok** (yanlış ürün riski); F9 geldikten sonra Akakçe ürün ID'si grup anahtarı olabilir.
- **Senin kontrolün:** grup kurulurken bot her linki tek tek okur ve **onay ister**:
  "3 link okundu, hepsi TCL 65C7K görünüyor — grup kurulsun mu?"
- **Test:** 3 mağaza fixture'ı, en ucuz seçimi, bir mağaza `ok:false` iken alarmın yine gelmesi,
  aynı fiyata ikinci alarm yok, 10 ürün/grup sınırı.

### F9 — `/magazatakipbul` (Akakçe'den satıcı linkleri) — **kolaylık, sonda**
`akakce_worker.parse_page` teklifleri `{price, seller}` döndürüyor, `url` **atılıyor**;
`akakce_worker.py` değişmez → **yeni** `seller_links_worker.py` sayfayı **bir kez** indirir →
(a) `parse_page` çağırır (kanıtlı doğrulama bozulmaz), (b) kendi JSON-LD yürüyücüsüyle
`price + seller + url` çıkarır.
**Ölçüm:** TCL 65C7K → **4/4** teklif linkli; fritöz fixture'ı → JSON-LD **10/10**, HTML listesi
**10/16**. Çıktıda **dürüst kapsam etiketi** (`4/4`, `10/16`).
```
Sen: /magazatakipbul https://www.akakce.com/televizyon/…-fiyati,1050078653.html
Bot: TCL 65C7K … Akakçe'de 4 satıcı, 4'ünde de link bulundu:
     1) n11 162.890,00 (lacasedegrup)  2) Pttavm 163.333,33  3) Hepsiburada 169.200,00  4) Pazarama 179.000,00
     Trendyol/Amazon/idefix/Teknosa/MediaMarkt listelenmiyor — varsa linki /magazatakip ile yapıştır.
     Hangilerini takip edeyim? (1,2,3)
```
**Ek keşif kanalı:** MediaMarkt `category/…?brand=TCL` (robots serbest) — F1'de ölçülür.

### F10 — Çoklu seçim kuyruğu
**Kanıt:** `product_tracker.py:291` `self.drafts[chat] = {...}` **tek yuva**, üzerine yazar;
300-306 `add` işi **tek ürün + tek hedef**. "Her seçim için `tracker.handle(url)`" önceki taslağı ezer.
**Çözüm:** yeni dosya kendi kuyruğunu tutar `{'urls': [...], 'i': 0}`; hedef mesajında sırayla
`tracker.handle(url, existing_pending=False)` + `tracker.handle("<hedef>")`, `i` artar,
"2/3 · 65C7L için hedef?" sorulur. Hedef biçimleri: `hepsi 55000`, `55000, 70000`, `65C6K: 55000`.
**Test:** 3 seçim + `hepsi 55000` → **3 kayıt**.

---

## 5. Ekranda ne görürsün (F8 sonrası = asıl hedef)

```
Sen: /magazatakip https://www.hepsiburada.com/tcl-65c7k-…-p-HBCV…
Bot: ✅ Okundu: TCL 65C7K Premium 4K Ultra HD 65" 165 Ekran Mini LED TV
     Hepsiburada · liste 169.200,00 TL · sepette 165.000,00 TL · satıcı: lacase de
     Bu ürünün başka mağazada linki var mı? Varsa yapıştır, yoksa "yok" yaz. (kargo hariç)
Sen: https://www.n11.com/urun/tcl-65c7k-…-103250733?magaza=lacasedegrup
Bot: ✅ n11 · liste 162.890,00 TL · satıcı: lacasedegrup
     Grup: tcl-65c7k (2 mağaza). Hangi fiyatın altına düşerse haber vereyim? (ör. 148000)
Sen: 148000
Bot: ✅ Takibe alındı · grup tcl-65c7k · 2 mağaza · hedef 148.000 TL
     Şu an en uygun: n11 · lacasedegrup · 162.890,00 TL

… 15 dk sonra, n11 düştü …
Bot: ₺147.499 — TCL 65C7K Premium 4K Ultra HD 65" …
     En uygun: n11 · satıcı lacasedegrup · sepette 147.499,00 TL
     Diğerleri: Hepsiburada 165.000 (sepette) · Trendyol okunamadı (coğrafya duvarı)
     Mağaza Takip Bildirimi · kargo hariç
     https://www.n11.com/urun/…
```

---

## 6. Risk tablosu

| Risk | Etki | Önlem |
|---|---|---|
| `bot.py` değişikliği Akakçe takibini bozar | **yüksek** | Ayrı `try/except`, Akakçe yönlendirmesi değişmez, F4 testinde "Akakçe linki eskisi gibi" maddesi |
| Hepsiburada Akamai 403/503 | orta | `www.`siz URL + `Referer`; 3 ardışık hatada tek uyarı; üstel geri çekilme |
| Mağaza sayfası yapısı değişir | orta | C4 → tutmazsa "okuma başarısız", yanlış fiyat gösterilmez |
| Taksit/puan fiyat sanılır | **yüksek** | JSON-LD kuruş değeri ana blokta aynen geçmeli (gerçek tuzak: 22.499,75) |
| Amazon 2. el fiyatını yeni sanmak | **yüksek** | `İkinci El/Amazon Depo/Yenilenmiş/Yeni Gibi` reddedilir |
| Amazon CAPTCHA/403 | **yüksek** | 6 saatte 1, günde ≤6; engelde adaptör kendini kapatır |
| Trendyol coğrafya duvarı | orta | Duvar tespiti → Akakçe Trendyol satırı yedeği |
| n11'de yanlış satıcı | orta | `?magaza=` korunur; gömülü JSON'dan satıcı başına fiyat |
| Grup yanlış eşleşme (farklı ürün) | orta | Otomatik eşleştirme yok; 3 link okunup **onay** istenir |
| Public repo'da fiyat geçmişi | orta | Geçmiş commit edilmez |
| Actions maliyeti | düşük | Repo public → standart runner ücretsiz; probe yalnız elle |
| Test tabanı kayar | orta | Taban **904 test OK**; her fazda düşerse faz kapanmaz |

---

## 7. Test disiplini

```
/tmp/venv/bin/python -m unittest discover -s tests
```
(Bu ortamda `telethon` gerekiyor; sistem python'unda yok → `/tmp/venv --system-site-packages`.)
Taban: **904 test OK**. Faz başında ve sonunda koşulur; sayı düşerse sonraki faza geçilmez.
Yeni testler: `test_store_worker.py`, `test_store_tracker.py`, `test_store_group.py`,
`test_store_links.py`, `test_find_queue.py` + `tests/fixtures/stores/**` (gerçek sayfadan).

---

## 8. Geri alma (her faz için)

1. O fazın **tek commit**'ini revert et.
2. Yeni dosyalar silinir (`store_worker.py`, `store_tracker.py`, `store_group.py`,
   `seller_links_worker.py`…).
3. `store_watches.json` → `{"version":1,"enabled":false,"items":[]}` → takip durur.
4. Akakçe hattı hiçbir fazda etkilenmez.

---

## 9. Derin tarama — ne bulundu, ne alınacak (2026-11-08)

**Aranan:** GitHub repo araması + kod araması (17 sorgu): `trendyol/n11/hepsiburada/pazarama/
mediamarkt/teknosa + scraper`, `turkish ecommerce scraper`, `price tracker turkey telegram`,
`json-ld product extractor`, `__NEXT_DATA__ trendyol`, `__INITIAL_STATE__ hepsiburada`,
`botasaurus`, `scrapling`, `curl_cffi`, `self-hosted price monitor`, `akakce`.

| Kaynak | Ne alıyoruz | Durum |
|---|---|---|
| **`kaanvp/fiyat-takip`** (en değerli) | **20+ TR mağaza için ayrı ayrıştırıcı**: hepsiburada, n11, pazarama, trendyol, mediamarkt, teknosa… | 4 kademeli fiyat merdiveni, **Hepsiburada non-www+Referer**, **n11 varyant JSON'undan satıcı başına fiyat**, **Trendyol `__PRODUCT_DETAIL_APP_INITIAL_STATE__`** → plana işlendi |
| **`msakirc/kutai`** | Trendyol ürün sayfalarında **`__NEXT_DATA__` önce**, sonra yapısal veri; `public.trendyol.com` API'sinin 2026-03'te DNS sorunu yaşadığı notu | Alındı. **Resmî olmayan API kullanılmayacak** (senin kuralın) |
| `ismailcakmak/e-commerce-price-tracker` | Trendyol `prc-dsc`/`pr-new-br`, Amazon `a-offscreen` | Yedek seçici |
| `furkandlkdr/telegram-trendyol-bot` | `data-testid="price"` + `price-view-discounted`; "Sepete Ekle" ile stok kontrolü | Yedek seçici + stok kuralı |
| `2scraper/mediamarkt-scraper` | MediaMarkt'ta **EAN + 30 günlük dip fiyat** alanları | **F1'de ölçülecek**; EAN çıkarsa gruplama anahtarı olur (F8) |
| `nsozturk/akakce-mcp` | Akakçe için MCP sunucusu + `API_REFERENCE.md` + `llms.txt` | **F9** için referans (kod alınmayacak) |
| `clucraft/PriceGhost` (1.1k★) | Self-hosted fiyat takip mimarisi | Mimari fikir (kayıt modeli, bildirim) |
| `D4Vinci/Scrapling` (99k★) | Anti-bot fetch katmanı, TLS parmak izi | **Reddedildi** — bağımlılık; stdlib kuralını bozar |
| `lexiforest/curl_cffi` (6.8k★) | Chrome TLS/HTTP2 taklidi | **Reddedildi** — bağımlılık. Yalnız "Trendyol duvarı aşılamazsa son çare" olarak not edildi, plana girmedi |
| `omkarcloud/botasaurus` (5.8k★) | Anti-detect tarayıcı çerçevesi | **Reddedildi** — tarayıcı gerekir |
| `metehanulusoy/price-tracker-bot` | Amazon `.a-price-whole`, Playwright | Seçici alındı, **Playwright reddedildi** |
| `omkarcloud/trendyol-scraper` | RapidAPI ücretli servis | **Reddedildi** — proxy/3. taraf |
| `piloterr`, `luminati/ Bright Data` | Ticari scraping API'leri | **Reddedildi** — 3. taraf/proxy |

**Yasal/etik çizgi:** hiçbir depodan kod kopyalanmadı (lisans riski yok). Yalnız
"**hangi HTML sınıfı / hangi gömülü JSON alanı fiyat taşıyor**" bilgisi alındı; kod kendi
C1–C7 sözleşmelerimizle sıfırdan yazılacak. Resmî olmayan site API'leri (`public.trendyol.com`,
`discovery-web-*`) **kullanılmayacak**. Tarayıcı (Playwright/Selenium) **kullanılmayacak**.

---

## 10. Claude'a not (kopyala-yapıştır)

```
FINAL PLAN v4 — onaylandı. Sıra: F1 probe → F2 store_worker(HB+MediaMarkt+Teknosa)
→ F3 store_tracker+store_watches.json → F4 bot.py → F5 n11+Pazarama → F6 Trendyol
→ F7 Amazon → F8 EN İYİ FİYAT MOTORU (grup) → F9 /magazatakipbul → F10 çoklu seçim.
Her faz ayrı commit; test kapısı taban 904 OK.

Dokunulmazlar: product_tracker.py, akakce_worker.py, product_watches.json, config.json,
COMMAND_DESCRIPTIONS/HELP_TEXT. C1–C7 aynen.

D1 -I: izole modda sys.path[0] stdlib zip olur, -E -s devrede → worker kendi klasörünü
   sys.path.insert(0, ...) ile ekler.
D2 QUERY adaptör başına: Teknosa/MediaMarkt "drop", n11/Pazarama/Trendyol "keep".
D3 store_watches.json repoda yok → F3'te {"version":1,"enabled":false,"items":[]} commit.
D4 handle(event, raw, *, existing_pending=False); handled sonrası stage=="webdetakip"
   → drop_pending mağaza takipçisi için de çalışır.
D5 /magazatakip*, /magazatakipbul COMMAND_DESCRIPTIONS/HELP_TEXT'e EKLENMEZ.
D6 verify: JSON-LD kuruş değeri ana fiyat bloğunda aynen geçmeli; taksit (22.499,75),
   puan (+18.000), ek garanti (3.639 TL), "4 x …" fiyat adayı değil.
D7 store_probe.py: host allowlist (7 host) + https + NoRedirect; allowlist dışı reddedilir.
D8 Her fazda README: komutlar, açma/kapama, "kargo hariç", geri alma.
D9 Kapsam: Akakçe 65C7K'da yalnız 4 satıcı (n11, Pttavm, Hepsiburada, Pazarama).

YENİ — derin tarama bulguları (plana işlendi):
E1 Fiyat merdiveni her adaptörde: JSON-LD → GÖMÜLÜ JSON → data-testid/DOM → meta.
E2 Hepsiburada: www. OLMADAN url + Referer: https://hepsiburada.com/ → Akamai daha az engelliyor
   (kaynak: kaanvp/fiyat-takip). F1'de iki URL ayrı ayrı ölçülecek.
E3 n11: gömülü varyant JSON'unda "outOfStock":false … "price":"… TL" deseni → SATICI BAŞINA
   fiyat; en düşük satıcı buradan seçilir, adı bildirime yazılır.
E4 Trendyol: window.__PRODUCT_DETAIL_APP_INITIAL_STATE__ gömülü JSON
   (product.price.discountedPrice.value / sellingPrice.value), sonra __NEXT_DATA__, sonra JSON-LD.
   Duvar tespiti: select-country / "Welcome to Trendyol" → reason:"country_wall" + Akakçe yedeği.
E5 Amazon: #corePrice_feature_div .a-offscreen → #corePriceDisplay… → JSON-LD; YENİ/2.EL FİLTRESİ
   (İkinci El, Amazon Depo, Yenilenmiş, Yeni Gibi → reddet); 6 saatte 1; engelde kendini kapatır.
E6 F8 grup motoru: store_watches.json item'larına "group" alanı; grup min fiyatı → TEK alarm;
   bir mağaza okunamazsa grup alarmı durmaz, "okunamadı" satırı görünür. Otomatik eşleştirme YOK,
   grup kurulurken her link okunup onay istenir.
E7 MediaMarkt EAN alanı F1'de ölçülecek; çıkarsa grup anahtarı olarak kullanılabilir.

Reddedilenler: Playwright/Selenium (bağımlılık), curl_cffi/Scrapling/Botasaurus (bağımlılık),
public.trendyol.com ve discovery-web-* resmî olmayan API'ler, RapidAPI/Bright Data/proxy.
```

---

## 11. Komut listesi (yeni, hiçbiri yardım menüsüne eklenmez)

| Komut | İş |
|---|---|
| `/magazatakip <link>` | Mağaza linkini takibe al (hedef fiyat sorar; başka mağaza linki eklemeyi önerir) |
| `/magazatakipgrup <grup> <link>` | Linki var olan gruba ekle (F8) |
| `/magazatakipac` · `/magazatakipkapat` | Mağaza hattını aç / kapat (varsayılan **kapalı**) |
| `/magazatakipler` | Takip listesi + grup bazlı en uygun fiyatlar |
| `/magazatakipsil N` | N. kaydı sil |
| `/magazatakipiptal` | Taslağı iptal |
| `/magazatakipyardim` | Kendi kısa yardımı |
| `/magazatakipbul <akakçe linki>` | Akakçe ürünündeki satıcı linklerini listele (F9) |

---


## 13. Kapasite ve limitler — ölçülmüş (2026-11-08)

**Kısa cevap:** Evet. Ama bu **yeni bir workflow/runner açmadan**, mevcut `bot.py` sürecinin
içine bir `asyncio` görevi olarak eklenerek yapılır. Yeni süreç = yeni Actions dakikası,
yeni Telegram poller'ı (= 409 çakışması) demek olurdu; planda **öyle değil**.

### 13.1 Mevcut durum (koddan)
`telegram-monitor.yml`: tek job, `timeout-minutes: 350`, `RESTART_AFTER_MINUTES: 330`,
cron `7 */6 * * *`. Bot kendi kendini yeniden başlatıyor → **günde ~24 saat tek runner**.
`bot.py:7558` → `private_tasks.append(asyncio.create_task(tracker.run()))`.
Bizim `store_tracker.run()` de **aynı listeye** eklenecek (F4).

### 13.2 Limit tablosu

| Kaynak | Mevcut kullanım | Yeni plan sonrası | Limit | Durum |
|---|---|---|---|---|
| **Actions dakikası** | ~1.440 dk/gün (tek runner, 7/24) | **Değişmez** (yeni job yok) | Public repo → standart runner **ücretsiz/sınırsız** | ✅ |
| **Telegram poller** | 1 süreç (`bot.py`) + `private_bot` | Değişmez | 409 çakışması yok | ✅ |
| **Akakçe döngüsü** | 30 ürün × `HOST_GAP 10 sn` = **300 sn** / `INTERVAL 300` | **Değişmez** (dosyaya dokunulmuyor) | %100 dolu — **zaten sınırda** | ⚠️ mevcut |
| **Mağaza döngüsü** | yok | **30 ürün**, ürün başına 5 dk–1 saat (`auto`); host başına 1 istek/20 sn | ~2.880 istek/gün, host başına ≤4.320 | ✅ |
| **Amazon alt sınırı** | — | `auto`: ürün başına **4–24 istek/gün** | kill switch | ✅ |
| **Günlük HTTP isteği** | 30 × 288 = 8.640 | +2.880 (30 ürün × 96) = **~11.520/gün** ≈ 0,13 istek/sn | site nezaket sınırları | ✅ |
| **GitHub Contents API** | yalnız durum değişince (2 istek) | aynı | 5.000 istek/saat | ✅ |
| **Disk/commit** | `product_watches.json` | +`store_watches.json` (~1-2 KB) | — | ✅ |
| **Bellek** | — | +1 asyncio görevi, +10 kayıt | — | ✅ |
| **Telegram mesaj** | yalnız alarm | +grup alarmı (F8: **tek** mesaj) | ~30 mesaj/sn | ✅ |

### 13.3 Dört şart (bunlar sağlanmazsa "sınırsız" olmaz)

1. **Repo public kalmalı.** Public → standart runner dakikaları ücretsiz ve sınırsız.
   Repo private'a dönerse: ~1.440 dk/gün × 30 = **43.200 dk/ay**, Free planın 2.000 dk'lik
   kotasını ~20 kat aşar (Linux $0.006/dk → ~$260/ay). **Bu planın gizli ön koşulu budur.**
2. **Akakçe döngüsü dolu (%100).** 30 ürün × 10 sn = 300 sn, aralık da 300 sn. Yani Akakçe'yi
   hızlandırmak mümkün değil; **yeni ürünler mağaza hattına gitmeli**, Akakçe'ye yığılmamalı.
   Not: mağaza hattı 15 dk aralıkla çalıştığı için **Akakçe'den daha yavaş** olacak (5 dk değil).
3. **Tek süreç, tek poller.** `store_tracker.run()` mevcut `private_tasks` listesine eklenir;
   **ayrı workflow açılmaz**. Ayrı workflow açılırsa `private_bot`/`bot.py` ile 409 çakışır.
4. **Tek dispatcher, iki taslak.** Aynı anda hem `/ekle` hem `/magazatakip` taslağı açık olabilir;
   F4'teki öncelik kuralı (`existing_pending` aynı değeri iki takipçiye) bunu çözer.
   Çözülmezse "mağaza linki Akakçe taslağına gider" hatası olur.

### 13.4 Dürüst sınırlar (abartmıyoruz)

- **Mağaza hattı 30 ürün** (Akakçe ile aynı). Ürün artınca engellenme değil, **kontrol sıklığı** düşer
  (host limiter). Dolarsa `/magazatakipsil` ile yer açılır.
- **Akakçe hattı 30 ürün ve 5 dakika** — mevcut sınır, değişmiyor.
- **Runner 5,5 saatte bir yeniden başlıyor** → yarıda kalan taslaklar (hedef fiyat sorulmuş ama
  cevaplanmamış) kaybolur. Bugün de böyle; yeni bir risk değil.
- **Yavaşlık:** `auto` hızda mağaza hattı **5 dk (hedefe yakınken) – 1 saat (uzakken)** arasında okur.
  Akakçe sabit 5 dk. Yani mağaza alarmı Akakçe'den genelde daha geç; `acil` seçilirse eşitlenir.
- **Amazon** en kısıtlı: varsayılan 6 saat (günde 4), `acil` ile 1 saat (günde 24). Daha hızlısı riskli.

### 13.5 Bu tablo nereden geliyor (kanıt)
`telegram-monitor.yml` (timeout 350, restart 330, cron 6 saat), `bot.py:7558`, `bot.py:5515`
(`persist_products` → Contents API GET+PUT), `product_tracker.py:18-23`
(`MAX_PRODUCTS 30`, `INTERVAL 300`, `SEND_TIMEOUT 45`, `HOST_GAP 10`),
`product_tracker.py:156` (`save` yalnız durum değişiminde), `product_tracker.py:413` (`run` döngüsü).


## 13.5 Sınırlar neden var? Hangisi gerçek, hangisi keyfi (2026-11-08 taraması)

### 13.5.1 Üç tür sınır var — karıştırılmamalı

| Sınır | Tür | Gerçek mi? |
|---|---|---|
| Akakçe 30 ürün / 300 sn | **Teknik (gerçek)** | 30 × `HOST_GAP 10 sn` = 300 sn = aralık → **döngü %100 dolu**, ürün eklenemez |
| Mağaza `INTERVAL 900 sn` | **Bizim seçimimiz (keyfi)** | Teknik zorunluluk yok. Gerçek uygulamalar **1–3 saat** kullanıyor → bizim 15 dk zaten 4–12 kat agresif |
| `HOST_GAP 20 sn` | **Nezaket (gerekli)** | Bir siteye saniyede 1 istek = engellenme. robots'ta **Crawl-delay yok** (kontrol edildi) → kendi kuralımız |
| Mağaza 10 ürün | **Bizim seçimimiz (keyfi)** | `bot.py` 7/24 çalıştığı için ürün sayısı Actions dakikası **artırmıyor**; tek kısıt host başına istek aralığı |
| Amazon 6 saatte 1 | **Yasal/risk (gerekli)** | `robots.txt` AI botlarını ismen yasaklıyor; ToS otomatik erişimi sınırlıyor |
| Actions dakikası | **Gerçek değil** (public repo) | Public → ücretsiz sınırsız; tek runner zaten 7/24 çalışıyor |

### 13.5.2 Sanayi karşılaştırması (ölçüldü)
- **changedetection.io** (35,6k★): varsayılan kontrol **3 saat**; fiyat takibi için önerilen
  **1–3 saat**; "**5–10 dakika yalnız çok acil birkaç ürün için**" — kaynak: resmî rehberler.
- **urlwatch** (3,1k★): saatlik/günlük periyot varsayılanı.
- Yani **15 dakika "çok sık" tarafında**, 5 dakika ise Akakçe'nin hattı (mevcut, çalışıyor).
- **robots.txt kontrolü (bugün):** MediaMarkt, n11, Akakçe, Teknosa, Hepsiburada, Pazarama →
  hiçbirinde **`Crawl-delay` yok**. MediaMarkt AI botlarını **açıkça serbest bırakmış**
  (`User-agent: ClaudeBot / Allow: /`). Yani yavaşlığımızın sebebi sitelerin kuralı değil,
  **bizim temkinli seçimimiz**.

### 13.5.3 Çözüm: sabit süre yerine **akıllı hız**

**Kaldırılan:** tek `INTERVAL 900` ve tek `MAX 10`.
**Gelen (F3/F5'e işlendi):** ürün başına `hız` alanı + host bazlı limiter + otomatik geri çekilme.
**Ayrıntı ve site bazlı dip sınırlar → 13.6.**

| Hız | Aralık | Ne zaman |
|---|---|---|
| `acil` | **5 dk** | Kampanya dönemi, hedefin çok yakınındasın |
| `normal` (varsayılan) | **15 dk** | Genel kullanım |
| `ekonomik` | **1 saat** | Uzak hedef, "acelesi yok" |
| `auto` ✅ önerilen | **5 dk – 1 saat** (site dip sınırına göre sınırlanır) | 13.6.3 formülü |

**`otomatik` nasıl çalışır:**
1. `fiyat, hedefin %5 yakınında` → **5 dk** (alarm anı yakın, hızlan)
2. `hedefin %5–20 üstünde` → **15 dk**
3. `hedefin %20+ üstünde` → **1 saat** (boşuna istek atma)
4. **Tek yönlü kural:** fiyat hedefin altına indiyse (alarm gitti) → 15 dk'ya döner, tekrar
   yükselirse yavaşlar.
5. **Geri çekilme:** `403/429/CAPTCHA` → aralık **iki katına** (max 6 saat) + **tek uyarı**;
   3 temiz okumadan sonra kademeli olarak hızlanır.
6. **Host bazlı token bucket:** aynı siteye **en sık 1 istek / 20 sn** — bu kural ürün sayısından
   **bağımsız** çalışır. 30 ürün aynı sitedeyse limiter etkin aralığı otomatik
   `30 × 20 sn = 10 dk`ya çeker. **Yani ürün sayısı artınca engellenme riski artmaz, sadece
   kontrol aralığı uzar.** Bu, "sabit ürün sınırı"ndan daha doğru bir tasarım.

### 13.5.4 Yeni kapasite (ölçülmüş hesap)

| Senaryo | İstek/gün | Host başına | Durum |
|---|---|---|---|
| 30 ürün, 5 siteye dağılmış, `normal` (15 dk) | 30 × 96 = 2.880 | 576 | ✅ rahat |
| 30 ürün, tek sitede, `normal` | 2.880 | 2.880 → limiter 10 dk'ya çeker | ✅ engellenmez |
| 30 ürün, `acil` (5 dk) | 8.640 | 1.728/site | ✅ (limit 4.320/gün/host) |
| Akakçe hattı (mevcut) | 8.640 | 8.640 | ⚠️ **dolu** — yeni ürün almaz |
| Amazon, `normal` | ürün başına 4 | 4 | ✅ |
| Amazon, `acil` (1 saat) | ürün başına 24 | 24 | ⚠️ sınırda, kill switch devrede |

**Sonuç:** Mağaza hattı ürün sınırı **10 → 30**'a çıkıyor (Akakçe ile aynı). Ürün sayısı artınca
engellenme değil, **kontrol sıklığı** düşüyor — doğru davranış bu. Actions maliyeti yine **artmıyor**
(`bot.py` zaten 7/24 çalışıyor).

### 13.5.5 Değişen dosya alanları
- `store_watches.json` item: `{"url", "store", "title", "target", "chat_id", "alert_price",
  "group", "speed": "auto|acil|normal|ekonomik", "interval", "fail_streak", "next_check"}`
- `store_tracker.py`: `next_check` tabanlı zamanlayıcı (sabit tur yerine **her ürün kendi saatinde**),
  host token bucket, `speed` çözümleyici, geri çekilme.
- **`product_tracker.py` değişmiyor** (Akakçe 5 dk / 30 ürün sabit kalıyor).

### 13.5.6 Dürüst kalan sınırlar
- **Akakçe hattı** gerçekten dolu: 30 ürün / 5 dk. Hızlandırmak için dosyasına dokunmak gerekir →
  **yapmıyoruz**. Akakçe'de daha fazla ürün istersen mağaza hattına taşı.
- **Amazon** her modda en yavaş (1 saat en hızlısı, varsayılan 6 saat) — engellenmemek için.
- **`acil` modu senin sorumluluğunda:** 30 ürün × 5 dk × tek site = günde 8.640 istek. Limiter
  korur ama "nezaket" sınırına yakın. Varsayılan `auto`.


## 13.6 Eşzamanlı istek ve site bazlı dip sınır (2026-11-08 taraması)

### 13.6.1 "Eşzamanlı istek atamıyor muyuz?" — Atabiliyoruz, ama mesele o değil

**Gerçek referans değerler (Scrapy 2.19 dokümantasyonu):**

| Ayar | Çerçeve varsayılanı | Proje şablonu (2.13.3+) | "Kibar" öneri | "Çok kibar" öneri | **Bizim mevcut** |
|---|---|---|---|---|---|
| Eşzamanlı istek / domain | **8** | 1 | 4 | 2 | **1** |
| İstekler arası gecikme | 0 | 1 sn | 2 sn | 5 sn | **10 sn (Akakçe)** |

Yani bizim ayarımız **"çok kibar" ayardan bile 2 kat yavaş**, çerçeve varsayılanından **8 kat az
eşzamanlı**. Scrapy'nin kendi önerisi: *"Sorumlu tarama kutudan çıktığı gibi"* = 1 eşzamanlı + 1 sn.

**İnsan analojisi — haklısın:** tarayıcı aynı siteye **6 paralel bağlantı** açar; insan 5–10 sn'de
bir ürün sayfasına geçer. Yani gerçek bir insanın trafiği ≈ **birkaç saniyede birkaç istek**.
Bizim 20 saniyede 1 istek insan davranışının çok altında.

**Ama asıl teknik nokta:** eşzamanlılık, site zaten hız sınırlıysa **hız kazandırmaz**.
- 10 paralel istek atarsın, site 5 isteği kabul edip 5'ini 429/403 ile reddeder → **net hız aynı,
  risk daha yüksek** (burst = bot sinyali).
- Doğru tasarım "her şeyi paralel yap" değil: **host başına token bucket (dip sınır) + sınırlı
  eşzamanlılık**. Eşzamanlılık yalnız bucket'ın izin verdiği kadar açılır.

### 13.6.2 Site bazlı **dip sınır** tablosu (senin dediğin gibi, her site kendi içinde)

| Host | `min_gap` (dip) | Eşzamanlı | Gerekçe |
|---|---|---|---|
| `akakce.com` | **10 sn** | 1 | Mevcut, çalışıyor — **dokunmuyoruz** |
| `www.mediamarkt.com.tr` | **5 sn** | 2 | robots AI botlarını **açıkça serbest bırakmış** (`ClaudeBot → Allow: /`) |
| `www.teknosa.com` | **8 sn** | 1 | Fiyat oynak; F1 ölçümüyle kesinleşir |
| `www.pazarama.com` | **8 sn** | 1 | Hafif site |
| `www.trendyol.com` | **6 sn** | 1 | Coğrafya duvarı riski |
| `www.n11.com` | **10 sn** | 1 | Geniş robots kısıtı + satıcı bazlı sayfa |
| `www.hepsiburada.com` | **12 sn** | 1 | Akamai koruması |
| `www.amazon.com.tr` | **60 sn** | 1 | `robots.txt` AI botlarını **ismen yasaklıyor**, ToS riski |

Bu değerler **başlangıç**tır; F1'de ölçülür ve **adaptif olarak** güncellenir (13.6.4).

### 13.6.3 Ürün sayısı artınca otomatik seyrelme — senin formülün

Site başına: **`etkin_aralık = max(istenen_aralık, (N − 1) × min_gap)`**

| Site | `min_gap` | 1 ürün | 10 ürün | 30 ürün | 60 ürün | 200 ürün |
|---|---|---|---|---|---|---|
| MediaMarkt | 5 sn | 5 dk | 5 dk | 5 dk | **5 dk** (59×5=295 sn) | **17 dk** |
| n11 | 10 sn | 5 dk | 5 dk | **~5 dk** (29×10=290 sn) | **10 dk** | **33 dk** |
| Amazon | 60 sn | 5 dk | **10 dk** | **30 dk** | **1 saat** | 3,3 saat |
| **Akakçe (mevcut)** | 10 sn | 5 dk | 5 dk | **5 dk — TAM DOLU** | — | — |

**Okunuşu:** 1 ürün varken istediğin hızda kontrol edilir; ürün sayısı artınca **site kendi dip
sınırından dolayı** aralık kendiliğinden uzar. Ayrı bir "50 ürün sınırı" koymaya **gerek yok** —
fizik onu kendisi dayatıyor. Akakçe satırı bunun kanıtı: 30 ürün × 10 sn = 300 sn = 5 dk aralık →
döngü **tam dolu**, bu yüzden Akakçe'ye yeni ürün eklenemiyor.

### 13.6.4 Adaptif hız — tahmin yerine sitenin kendi cevabı (AutoThrottle mantığı)

1. **Yanıt süresi ölçülür:** `gap = max(min_gap, yanıt_süresi × 2)`.
   Site yavaşladıysa biz de yavaşlarız (Scrapy AutoThrottle mantığı: delay = latency × hedef eşzamanlılık).
2. **429 / 403 / CAPTCHA görülürse:** o host için `min_gap` **iki katına** (max 300 sn) +
   **tek uyarı**; eşzamanlılık **1'e** düşer. **3 temiz okumadan sonra** çeyrek adım geri hızlanır.
3. **F1 probe bunu ölçer:** her siteye **3 istek, aralarında 5 sn** → yanıt süreleri + 429/403 oranı.
   Tablo **tahmin değil ölçüm** olur.

### 13.6.5 Eşzamanlılığı neden 1–2'de tutuyoruz (dürüst gerekçe)

- **Kazanç yok:** 30 ürün × 5–10 sn = 2,5–5 dk. İstediğimiz kontrol aralığı zaten 5–60 dk.
  Yani seri okuma **fazlasıyla** yetiyor.
- **Risk var:** paralel istek = ani burst = bot algılama sinyali (Scrapy dokümanı da bunu söylüyor).
- **Karmaşıklık:** her okuma ayrı izole süreç. 30 ürün = 30 süreç açılışı → **tek süreçte batch**
  modu ekliyoruz (`store_worker.py --batch`, satır başına 1 URL). Hız aynı, CPU daha az.
- **Kapı açık:** `concurrency` alanı şemada duruyor; MediaMarkt'ta 2'ye çıkarmak **tek satır**.

### 13.6.6 Şemaya eklenen alanlar

`store_watches.json` item:
```json
{"url": "...", "store": "hepsiburada", "group": "tcl-65c7k",
 "speed": "auto", "interval": 300, "next_check": 0,
 "min_gap": 12, "concurrency": 1, "latency_ms": 840,
 "fail_streak": 0, "throttle_until": 0}
```
**Politika tablosu kod içinde sabit** (`SITE_POLICY = {"hepsiburada": {...}}`) — `config.json`'a
**dokunulmaz** (mevcut doğrulama bozulmasın).

### 13.6.7 Yeni günlük istek hesabı (30 ürün, `auto`)

| Site | 30 ürün için etkin aralık | İstek/gün |
|---|---|---|
| MediaMarkt (5 sn) | 5 dk | 30 × 288 = 8.640 |
| n11 (10 sn) | ~5 dk (sınırda) | 8.640 |
| Hepsiburada (12 sn) | ~6 dk | 7.200 |
| Amazon (60 sn) | 30 dk | 1.440 |
| **En kötü tek site senaryosu** | | **~8.640/gün ≈ 0,1 istek/sn** |

Akakçe hattı zaten 8.640/gün. Toplam ≈ 17.000 istek/gün ≈ **0,2 istek/sn** — bir sitenin
gördüğü **gerçek insan trafiğinin milyonda biri**. Yani sayı korkutucu değil; sorun sadece
**aynı saniyeye yığmamak**, o da token bucket ile çözülüyor.

## 14. Onay

**"F1'e başla"** → probeyi yazarım (7 mağaza, host allowlist, `NoRedirect`, gömülü JSON ölçümü),
testleri koştururum (**904 + yeni**), sonucu raporlarım (değişen dosyalar, test sonucu, geri alma),
sonra F2'ye geçerim. Arada bekleme yok; tek kapı test.

---

## 15. F2 sonucu — Cimri + Epey entegre edildi (2026-11-08)

Karar: **önce karşılaştırma siteleri**. Sebebi ölçüm: Akakçe'de Amazon/MediaMarkt/Vatan/A101
**hiç yok**; Cimri ve Epey bu mağazaları zaten gezmiş olarak geliyor. Yani tek bir Cimri
linki, ayrı ayrı mağaza entegrasyonundan daha çok mağaza kapsıyor.

### 15.1 Ne yapıldı

| Dosya | Durum | Ne yapar |
|---|---|---|
| `aggregator_worker.py` | **yeni** | Cimri/Epey ürün sayfasını okur, Akakçe ile **aynı JSON sözleşmesini** döndürür |
| `product_tracker.py` | **değişti (davranış korunarak)** | Link hangi siteye aitse **doğru işçiye** yönlendirir; Akakçe yolu bire bir aynı kaldı |
| `bot.py` | **değişti (2 satır)** | Link bekleme metni Akakçe+Cimri+Epey oldu; **yeni komut yok** |
| `tests/test_aggregator_worker.py` | **yeni, 16 test** | Ayrıştırma, binlik-nokta tuzağı, fail-closed |
| `tests/test_tracker_routing.py` | **yeni, 18 test** | Akakçe hattı bozulmadı mı, Cimri/Epey doğru işçiye mi gidiyor |

### 15.2 Ürün linki biçimi (kullanıcı bu üçünü gönderebilir)

| Site | Link biçimi | Örnek |
|---|---|---|
| Akakçe | `akakce.com/…-fiyati,ID.html` | (mevcut, değişmedi) |
| Cimri | `cimri.com/<kategori>/en-ucuz-<ürün>-fiyatlari,a<ID>` | `…/televizyonlar/en-ucuz-tcl-65c6k-…-fiyatlari,a2582673366` |
| Epey | `epey.com/<kategori>/<model>.html` | `epey.com/televizyon/tcl-65c6k.html` |

`.html` **şart** (Epey'de uzantısız adres 404 veriyor — ölçüldü). Cimri'de `,a<ID>` eki şart;
eksiz adres ürün değil **kategori** sayfası döndürüyor (ölçüldü).

### 15.3 Fiyat güvenliği — neden yanlış fiyat gösteremez

Üç kapı var, biri tutmazsa okuma **başarısız** olur ve bildirim gitmez:

1. **Yapısal fiyat**: sayfanın JSON-LD `lowPrice` alanı.
2. **Gözle görünen satırlar**: "Trendyol fiyatı 60.927,75 TL" gibi mağaza satırları
   (Cimri'de mağaza adı logo satırından okunur).
3. **Eşitlik**: (1) ile (2)'nin en düşüğü **aynı kuruş** olmalı. Tutmazsa `ValueError`.

Ek olarak **Türkçe binlik nokta tuzağı** kapatıldı: `29.999` naif ayrıştırıcıda 29,999 TL
olur; doğrusu 29.999 TL. Test `tests/test_aggregator_worker.py::PriceTrapTests`.

Engel sayfası (`captcha`, `access denied`, …) gövdesi **hiç ayrıştırılmaz**.

### 15.4 robots.txt durumu (ölçüldü, 2026-11-08)

| Site | Durum |
|---|---|
| Epey | `Claude-User / ChatGPT-User / OAI-SearchBot / PerplexityBot → Allow: /`; **Crawl-delay yok** |
| Cimri | Ürün sayfaları serbest; `?q=`, `?magaza=`, `/api/` yasak |

Bu yüzden link doğrulaması `?magaza=…` gibi sorguları **atıyor** — hem robots uyumu hem de
"tüm mağazalar görünsün" kuralı aynı anda sağlanıyor. Arama/karşılaştırma sayfaları
(`/ara/`, `/karsilastir/…/farklari/`) reddediliyor.

### 15.5 Kullanıcıya görünen fark

Aynı komutlar, aynı menü (`/webtakipac`, `/webtakipler`, …). Sadece link üç siteden biri
olabiliyor. Bildirimde en düşük fiyatın **hangi mağazada** olduğu ve kaç mağazanın
karşılaştırıldığı yazıyor:

```
₺60.927 — TCL 65C6K Ultra HD (4K) TV
Epey Takip Bildirimi
En düşük fiyat: Trendyol
5 mağaza fiyatı karşılaştırıldı
https://www.epey.com/televizyon/tcl-65c6k.html
```

Akakçe bildirimi **bire bir aynı** kaldı (test: `test_akakce_notification_is_unchanged`).

### 15.6 Test kapısı

`python -m unittest discover -s tests` → **973 OK** (F1 sonu 955; +18 yönlendirme testi).

### 15.7 Geri alma

Tek komut: `git revert <commit>` — Akakçe hattı, `config.json`, `product_watches.json`
biçimi ve komut listesi değişmediği için geri alma veri kaybı bırakmaz.

### 15.8 Sırada ne var

1. **Canlı doğrulama**: gerçek Cimri ve Epey linkleriyle tek tur (kullanıcı link verecek).
2. Sonra mağaza fazları (Hepsiburada → …) — F2 bunlara bağlı değil.
