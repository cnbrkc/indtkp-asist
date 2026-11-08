# MAĞAZA TAKİP PLANI — FİNAL (v3 son)

Repo: `cnbrkc/indtkp-asist` · Dal: `arena/4f0b6386-indtkp-asist` · Tarih: 2026-11-08
Bu belge **tek plan**. Bu sohbette üretilen diğer tüm plan/denetim dosyaları silindi;
içlerindeki değerli bilgiler (ölçümler, kararlar, düzeltmeler) buraya taşındı.

---

## 0. Tek cümlede plan

> **Akakçe hattına dokunmadan**, mağazaların kendi ürün sayfalarını okuyan **ikinci bir takip
> hattı** kurulur. Linkleri **sen verirsin** (motor önce), sonra Akakçe'nin satıcı listesinden
> **link önerisi** gelir (kolaylık sonra). Her adım testlerle kapanır, varsayılan **kapalı** başlar,
> patlarsa Akakçe takibi çalışmaya devam eder.

**Sıra mantığı:** motor → bağlantı → genişleme → bulucu. Sebep ölçümle sabit: Akakçe'den
otomatik link çıkarma ürüne göre 4/4 ile 10/16 arasında değişiyor; senin verdiğin link **%100**.

---

## 1. Değişmez sözleşmeler (her fazda aynı)

### 1.1 Dokunulmazlar
- `product_tracker.py`, `akakce_worker.py`, `product_watches.json` → **DEĞİŞMEZ** (yalnız import).
- `config.json`'a **yeni anahtar yok** (mevcut doğrulama bozulmasın).
- `COMMAND_DESCRIPTIONS` / `HELP_TEXT` → yeni komutlar **EKLENMEZ**
  (`tests/test_bot.py:259` kanonik komut demetini birebir karşılaştırıyor).
- Stdlib dışı bağımlılık yok. Fiyat geçmişi **commit edilmez** (repo public).
- Yeni özellik **varsayılan KAPALI**. Yeni kodun hatası kanal dinlemeyi ve Akakçe takibini
  **asla** durdurmaz (`try/except` + log).

### 1.2 C1–C7 okuma sözleşmeleri (Akakçe'den miras, her mağazada aynı)
- **C1 İzolasyon:** okuma ayrı süreçte (`-I`), sırlar miras alınmaz (yalnız `PATH/LANG/SYSTEMROOT`).
- **C2 Kimliksiz tek istek:** giriş yok, çerez yok, oturum yok, **yönlendirme reddi**
  (`NoRedirect`), tek `GET`, 3 MB gövde / 15 sn HTTP / 20 sn süreç.
- **C3 Yapısal veri önceliği:** JSON-LD (schema.org) birinci kaynak, anlamlı DOM ikinci.
  Otomatik üretilen CSS sınıflarına güvenilmez.
- **C4 Çapraz doğrulama:** iki bağımsız kaynak tutmazsa okuma **başarısız** (fail-closed).
- **C5 Fail-closed + nezaket:** hata/engelde bildirim yok; host bazlı min aralık; üstel geri çekilme.
- **C6 Idempotent alarm:** `alert_price` ile tekrar bastırma; kalıcı yazım başarısızsa RAM'de geri al.
- **C7 Test disiplini:** gerçek sayfadan fixture (sentetik değil) + kısmi/bozuk JSON-LD +
  "okundu ama doğrulanamadı" senaryosu.

### 1.3 İş kararları (senin verdiğin)
| Konu | Karar |
|---|---|
| Fiyat | **Liste + sepette birlikte okunur**, bildirimde ikisi yazılır; **alarm sepette fiyata göre** |
| Sepette okunamazsa | Liste fiyatı + "(sepette fiyat okunamadı)" notu (sessizce değiştirme yok) |
| Pazaryeri satıcısı | Sayfadaki satıcılar içinde **en düşük fiyat**; satıcı kilidi yok |
| n11 / Pazarama `?magaza=` | **KORUNUR** (fiyat satıcıya bağlı; atmak yanlış satıcıyı takip ettirir) |
| MediaMarkt / Teknosa `?shopId=` | **ATILIR** (robots'ta yasak; yalnız mağazanın kendi satıcısı) |
| Otomatik kabul | **Yok.** Bot aday gösterir, **sen onaylarsın** |
| Kargo | Mağaza hattı **kargo hariç** okur; bildirimde "kargo hariç" ibaresi **zorunlu** |
| Ürün kimliği | Akakçe ürün ID + seri kodu jetonu; GTIN **zorunlu değil** (Akakçe'de yok) |
| Fiyat geçmişi | Tutulmaz (Akakçe kendi geçmişini/alarmını veriyor; repo public) |
| Aynı ürün iki hatta | İki ayrı kayıt (şema sade kalsın) |
| Akakçe sınırı | 30 ürün / 300 sn (mevcut, değişmez) |
| Mağaza sınırı | 10 ürün / 900 sn taban, ±45 sn jitter, `HOST_GAP 20 sn` |

---

## 2. Mağaza × yetenek matrisi (ölçülmüş, 2026-11-08)

| Mağaza | Doğrudan takip | Not (kanıt) |
|---|---|---|
| **MediaMarkt** | ✅ **Evet** | JSON-LD `offers.price` + görünen `₺89.999,–`; taksit `Aylık ₺22.499,75` ve `+18.000 puan` **aynı sayfada** (D6 tuzağı). `?brand=TCL` kategori sayfası robots'ta serbest → keşif kanalı |
| **Teknosa** | ✅ Evet | `89.999 TL` + JSON-LD; `?shopId=` yasak → yalnız Teknosa'nın kendi satıcısı; fiyat gün içinde oynak |
| **Hepsiburada** | ✅ Evet, ama riskli | `SEPETTE 63.121,83` + liste `66.444,03` birlikte geliyor. Akamai: datacenter IP'de 403/503 riski |
| **n11** | ✅ Evet (`?magaza=` korunur) | `?magaza=hepsiphone` 49.750 ↔ `?magaza=markauygun` "SEPETTE 50.049,09" ↔ query'siz **fiyat yok** |
| **Pazarama** | ✅ Evet | `15.499,00 TL` sunucu tarafında; robots `Allow: /*?magaza`; yapısal veri doğrulanmalı (probe) |
| **Pttavm / idefix / Evkur** | 🟡 Sonra | Akakçe'de zaten listeleniyorlar; adaptör eklemek kolay ama öncelik değil |
| **Trendyol** | ❌ **Doğrudan okunamıyor** | Bugün ürün sayfası `/en/select-country?cb=...` sayfasına düştü → ülke/çerez kapısı. Çerez kuralı gereği (C2) zorlanmaz. **Akakçe'nin Trendyol satırlarıyla takip edilir** |
| **Amazon TR** | ❌ **Okunmuyor** | `robots.txt` AI botlarını **ismen yasaklıyor** (ChatGPT-User, ClaudeBot, PerplexityBot, GrokBot, Scrapy); satın alma kutusu yok, fiyat `a-offscreen`/JS'te, Prime/sepette indirim farkı. **Fiyat takibi yok** → yalnız hatırlatıcı modu (F9, opsiyonel) |

**Kritik gerçek:** TCL 65C7K'da Akakçe **"4 satıcı içinde kargo dahil en ucuz"** diyor →
n11, Pttavm, Hepsiburada, Pazarama. Trendyol/Amazon/idefix/Teknosa/MediaMarkt **o üründe yok**.
Yani "Akakçe'den 8 mağaza linki" hayal; **4/8** gerçek. Ama senin verdiğin linkte **8/8** mümkün
(Amazon hariç fiyat okuması).

---

## 3. Yol haritası — 9 faz

| Faz | Ad | Ne yapar | Telegram'da fark | Risk |
|---|---|---|---|---|
| **F1** | Probe | 6 mağazayı + MediaMarkt `?brand=` kategori sayfasını ölçer, **gerçek fixture üretir** | yok | **sıfır** |
| **F2** | Okuyucu | `store_worker.py`: Hepsiburada + MediaMarkt + Teknosa | yok | düşük |
| **F3** | Takipçi | `store_tracker.py` + `store_watches.json` (boş dosya **commit'li**) | yok | orta |
| **F4** | Bağlantı | `bot.py` ~20 satır | **LİNK YAPIŞTIR → TAKİP** | **yüksek** |
| **F5** | Genişleme | n11 + Pazarama (`?magaza=` korunur, sepette fiyat, en düşük satıcı) | 5 mağaza | orta |
| **F6** | Satıcı linkleri | `/magazatakipbul <akakçe linki>` → **satıcı linkleri listesi** | "bu üründeki linkler" | düşük |
| **F7** | Trendyol | Ülke duvarı **tespiti** + Akakçe Trendyol satırından takip + net mesaj | Trendyol görünür | düşük |
| **F8** | Çoklu seçim | `1,3,7` → sırayla hedef sor, `hepsi 55000` | 3 mağaza tek seferde | düşük |
| **F9** | (opsiyonel) Amazon | Hatırlatıcı modu: link kaydı + haftalık "kontrol et" | **fiyat okumaz** | düşük |

**Kural:** her faz **ayrı commit**, kendi testleriyle. Testler geçmeden sonraki faza geçilmez.
**Bekleme yok** — "gün" ifadesi yok, fazlar sırayla tek akışta ilerler.

---

## 4. Faz faz detay

### F1 — Probe (ölçüm)
**Yapılır:** `.github/workflows/store-probe.yml` (yalnız `workflow_dispatch`, `permissions: {}`,
secret yok, Telegram yok) + `tools/store_probe.py` (tek dosya, stdlib).
**Güvenlik:** host allowlist (`www.mediamarkt.com.tr`, `www.teknosa.com`, `www.pazarama.com`,
`www.n11.com`, `www.hepsiburada.com`, `www.amazon.com.tr`) + **https zorunlu** + `NoRedirect`;
allowlist dışı satır **reddedilir**. Siteler arası ≥5 sn, URL başına tek istek.
**Çıktı (her URL için):** HTTP kodu, yönlendirme var mı, gövde boyutu, captcha/engel işareti,
JSON-LD blok sayısı, Product sayısı, `offers.price` + `priceCurrency`, **görünen tüm fiyat
adayları** (ana fiyat / taksit / puan / garanti) ve **hangisinin JSON-LD ile eşleştiği**.
Ayrıca çıktı: `GITHUB_STEP_SUMMARY` tablosu + artifact (site başına ≤20 KB kırpılmış JSON-LD ve
fiyat çevresi HTML). **Bu artifact F2 fixture'larının tek kaynağı.**
**Kabul kriteri:** 6 site için tablo dolu; Trendyol/Amazon satırı "engelli/okunamadı" olarak
**kanıtlı** görünüyor; MediaMarkt `?brand=TCL` kategori sayfası da ölçülmüş.
**Geri alma:** workflow dosyasını sil.

### F2 — Okuyucu (`store_worker.py`)
**Yapılır:** `sys.path.insert(0, <kendi klasörü>)` (−I notu: izole modda `sys.path[0]` stdlib zip
olur, script klasörü eklenmez; `-E -s` de devrede) → `akakce_worker`'dan `cents, target_cents,
purchase_url, Scripts, NoRedirect, MAX_BODY` import.
Registry `{host: adaptör}`; adaptör sözleşmesi: `QUERY = "drop"|"keep"`, `product_url(raw)`,
`parse(raw_html)`, `verify(parsed)` (tutmazsa `ValueError`).
**Başlangıç adaptörleri:** Hepsiburada, MediaMarkt (`/tr/product/_<slug>-<id>.html`),
Teknosa (`/<slug>-p-<id>`).
**verify (tek satıcılı):** JSON-LD `offers.price` **ve** sayfada görünen fiyat. Kural:
**JSON-LD kuruş değeri ana fiyat bloğunda aynen geçmeli**; taksit (`22.499,75`), puan
(`+18.000 puan`), ek garanti (`3.639 TL`), `4 x …` satırları **fiyat adayı sayılmaz**.
Para birimi TRY, tek Product, stok var. Hepsiburada'da ek olarak liste ≥ sepette.
**CLI:** `python -I store_worker.py <url>` → `{"ok":true,"result":…}` / `{"ok":false,…}`
(akakce_worker ile aynı hata biçimi, iç ayrıntı sızdırmaz).
**Test:** `tests/test_store_worker.py` + `tests/fixtures/stores/**` — **fixture F1 artifact'inden**;
artifact yoksa faz **BLOKE**, sentetikle devam edilmez. Senaryolar: fiyat yok, iki Product, TRY
değil, stok yok, JSON-LD ≠ görünen fiyat, **taksit/puan satırı fiyatla karışmıyor**, engel
sayfası, güvensiz URL'ler (`http`, farklı host, userinfo, port, localhost, `host.evil.test`),
query politikası (Teknosa `shopId` atılır, n11 `magaza` korunur).
**Geri alma:** dosyayı sil.

### F3 — Takipçi (`store_tracker.py`) + başlangıç dosyası
**Yapılır:** `store_watches.json` **bu fazda commit edilir**:
`{"version":1,"enabled":false,"items":[]}` — yoksa `persist_products`'ın GitHub `GET`+`sha`
adımı ilk kayıtta hep "Takip dosyasının GitHub sürümü okunamadı." verir.
`StoreTracker(path, api, persist, reader=read_store, *, owner_id, header_formatter)` —
`product_tracker.py` örnek alınır, ona **bağlanılmaz**; `money`, `clean_title` import edilir.
En fazla **10** ürün. Kayıt: `url, store, title, target, chat_id, alert_price`.
**`handle(event, raw, *, existing_pending=False)`** imzası product_tracker ile aynı kural:
`existing_pending and komut değilse → False`. Allowlist dışı link / bilinmeyen komut → `False`.
**Komutlar:** `/magazatakip`, `/magazatakipac`, `/magazatakipkapat`, `/magazatakipler`,
`/magazatakipsil N`, `/magazatakipiptal`, `/magazatakipyardim`.
**Akış:** link kuyruğa (komut işleyicide **ağ beklemesi yok**) → hedef fiyat iste → kaydet.
İdempotent alarm, kayıt başarısızsa RAM'de geri al, okuma hatası ≠ bildirim hatası,
3 ardışık okuma hatasında tek uyarı, üstel geri çekilme.
**Bildirim:** `build_header_line` başlığı + boş satır + `{Mağaza} Takip Bildirimi · kargo hariç`
+ boş satır + ürün linki. Tek düz metin, link önizleme kapalı, 4096 UTF-16 kontrolü.
**Test:** `tests/test_store_tracker.py` (TrackerTests deseni): kuyruk, taslak, alarm + bastırma,
kayıt hatasında geri alma, bozuk dosya izolasyonu, hung reader yönlendirmeyi bloklamaz, iptal,
10 ürün sınırı, kapalıyken okuma yok, `existing_pending` kuralı.
**Geri alma:** dosyaları sil + `store_watches.json`'ı boşalt.

### F4 — `bot.py` bağlantısı (en riskli, en küçük diff)
**Yapılır:** `persist_products`'ın bulunduğu `try` bloğunda, mevcut tracker kurulumundan
**sonra**, **kendi `try/except`'iyle** `StoreTracker(...)` kurulur (aynı `persist_products`,
`owner_id`, `header_formatter`). Hata olursa **yalnız log**.
**Yönlendirme (~7213, owner-only blok):** `pending_state = peek_pending(pending_key(event))`
**bir kez** hesaplanır, **her iki takipçiye aynı `existing_pending`** geçirilir
(`existing_pending=pending_state is not None`). Sıra: mağaza taslağı varsa ona → sonra
`tracker.handle` → `handled` değilse `store_tracker.handle`.
**Pending temizliği:** `handled` sonrası mevcut `stage == "webdetakip"` → `drop_pending` mantığı
mağaza takipçisi handled olduğunda **da** çalışır (yoksa `/ekle` taslağı açık kalır).
`/iptal` ve `/magazatakipiptal` için iptal mesajı. `private_tasks`'a `store_tracker.run()`.
**Test:** Akakçe linki eskisi gibi `tracker`'a; mağaza linki `store_tracker`'a; bilinmeyen komut
eskisi gibi; kurulum patlarsa tracker + kanal dinleme çalışıyor; iki taslak varken düz metin
doğru yere; `/ekle` taslağı mağaza komutundan sonra açık kalmıyor.
**Ön koşul kontrolü:** `handled=False` sonrası "bilinmeyen komut" cevabının bozulmadığı doğrulanır.
**Geri alma:** tek commit revert → `bot.py` eski hâline döner.

### F5 — n11 + Pazarama
`QUERY="keep"` (n11 `?magaza=`, Pazarama `?magaza`). n11'de **satıcı sabitlenir**; sayfadaki
satıcılar içinde **en düşük fiyat** kuralı geçerli; bildirimde en düşük satıcının adı yazılır.
`verify`: liste ≥ sepette, ikisi de > 0, aynı para birimi. Sepette yoksa liste +
"(sepette fiyat okunamadı)".
**Test:** fixture + query politikası + "sepette yok" senaryosu + en düşük satıcı seçimi.

### F6 — `/magazatakipbul` (7. aşama: Akakçe'den satıcı linkleri)
**Neden ayrı dosya:** `akakce_worker.parse_page` teklifleri `{price, seller}` olarak döndürüyor,
`url` alanı atılıyor; `akakce_worker.py` değişmez olduğu için **yeni** `seller_links_worker.py`
sayfayı **bir kez** indirir → (a) `akakce_worker.parse_page` çağırır (kanıtlı doğrulama bozulmaz),
(b) kendi küçük JSON-LD yürüyücüsüyle `price + seller + url` çıkarır.
**Ölçüm (kanıt):** TCL 65C7K → 4/4 teklif linkli; hava fritözü fixture'ı → JSON-LD 10/10,
HTML listesi 16 teklif → **10/16**. Bu yüzden çıktıda **dürüst kapsam etiketi**: `4/4`, `10/16`.
**Ekran:**
```
Sen: /magazatakipbul https://www.akakce.com/televizyon/…-fiyati,1050078653.html
Bot: TCL 65C7K … Akakçe'de 4 satıcı, 4'ünde de link bulundu:
     1) n11         162.890,00 TL   lacasedegrup
     2) Pttavm      163.333,33 TL   La casede tasarım
     3) Hepsiburada 169.200,00 TL   lacase de
     4) Pazarama    179.000,00 TL   LACASEDE GRUP
     Trendyol / Amazon / idefix / Teknosa / MediaMarkt: Akakçe bu üründe listelemiyor.
     Bunlarda varsa linki /magazatakip ile yapıştır.
     Hangilerini takip edeyim? (1,2,3 — ya da "hepsi 150000")
```
**Ayrıca keşif kanalı (B):** MediaMarkt `category/…?brand=TCL` sayfası (robots serbest) →
"TCL 65 inc TV (9 ürün)" listesi; F1'de ölçüldü, F6'da ikinci kaynak olarak kullanılır.
**Test:** fixture × 2 (65C7K satıcı listesi + gürültülü örnek), linki olmayan satır "link yok"
etiketi, aynı satıcının farklı pazaryerleri uyarısı.

### F7 — Trendyol (en uygun yer: burada)
**Gerçek:** ürün sayfası bugün `/en/select-country?cb=…` sayfasına düştü (ülke/çerez kapısı).
Çerez kullanmadığımız için (C2) bu kapı zorlanmaz. Trendyol `robots.txt` zaten `?q=`, `/sr/`,
`/pd?` yollarını yasaklıyor.
**Yapılan (3 parça, kod maliyeti küçük):**
1. **Ülke duvarı tespiti:** `store_worker` çıktısında `ok:false, reason:"country_wall"`.
2. **Yönlendirme yok:** Trendyol linki `/magazatakip`'e verilirse bot **net** der:
   > ❌ Trendyol'u doğrudan okuyamıyoruz (ülke seçim sayfasına yönlendiriyor).
   > Trendyol'da fiyat Akakçe'nin Trendyol satırından takip edilir. Akakçe ürün linkini
   > `/webtakip` ile gönder, ya da `/magazatakipbul <akakçe linki>` ile Trendyol satırını seç.
3. **Akakçe Trendyol satırı:** `Akakçe'de Trendyol satırı varsa (ör. 65C6K'da 2 satıcı),
   `/magazatakipbul` listesinde **[Trendyol · Akakçe üzerinden]** etiketiyle görünür** —
   seçilirse **Akakçe ürün linki** takibe girer (mağaza adaptörü değil). Yani Trendyol
   bilgisi tamamen kaybolmuyor.

### F8 — Çoklu seçim kuyruğu (`1,3,7` / `hepsi 55000`)
**Kanıt (kod):** `product_tracker.py:291` → `self.drafts[chat] = {...}` **tek yuva**, üzerine
yazar; satır 300-306 `add` işi **tek ürün + tek hedef** alır. Yani "her seçim için
`tracker.handle(url)` çağır" yöntemi önceki taslağı ezer → yalnız son seçim yaşar.
**Çözüm:** yeni dosya kendi kuyruğunu tutar: `{'urls': [...], 'i': 0, 'titles': {...}}`.
Hedef fiyat mesajında sırayla `tracker.handle(url, existing_pending=False)` +
`tracker.handle("<hedef>")` çağrılır, `i` artar, "2/3 · 65C7L için hedef?" sorulur.
`product_tracker.py`'ye **dokunulmaz**.
**Hedef biçimleri:** `hepsi 55000` · `55000, 70000, 40000` · ürüne özel `65C6K: 55000`.
**Yarıda kalırsa:** taslak daimi değil (yeniden başlatmada kaybolur) → "kalanları tekrar seç".
Bu bilinçli: kalıcı yazmak `product_watches.json` şemasını bozardı.
**Test:** 3 seçim + `hepsi 55000` → **3 kayıt**, 3 farklı hedef değil, tek oturum.

### F9 — Amazon (opsiyonel, isteğe bağlı, fiyat okumaz)
**Gerçek:** `robots.txt` AI botlarını ismen yasaklıyor (ChatGPT-User, ClaudeBot, PerplexityBot,
GrokBot, Scrapy); satın alma kutusu yok; fiyat JS'te; Prime/sepette indirim farklı.
**Yapılan:** `/magazatakipamazon <link>` → link **doğrulanıp** kaydedilir (ASIN regex), ürün adı
**yazdığın metinden** alınır; fiyat okunmaz. Haftalık tek hatırlatma:
> 🔔 Amazon kontrol: TCL 65C7K — istediğin ürün. Fiyatı sen kontrol et: <link>
**Kabul:** Amazon'dan **hiç fiyat okunmaz**, alarm üretilmez, robots zorlanmaz.
Karar senin: bu faz istenmezse plan F8'de biter.

---

## 5. Ekranda ne görürsün (F4 sonrası, asıl hedef)

```
Sen: /magazatakip https://www.hepsiburada.com/tcl-65c7k-…-p-HBCV…
Bot: ✅ Okundu: TCL 65C7K Premium 4K Ultra HD 65" 165 Ekran Mini LED TV
     Hepsiburada · liste 169.200,00 TL · sepette 165.000,00 TL (kargo hariç)
     Hangi fiyatın altına düşerse haber vereyim? (ör. 150000)   İptal: /magazatakipiptal
Sen: 150000
Bot: ✅ Takibe alındı (Hepsiburada, kargo hariç)
     Hedef 150.000 TL · şu an sepette 165.000,00 TL

… 15 dk sonra …
Bot: ₺149.499 — TCL 65C7K Premium 4K Ultra HD 65" …
     Liste 169.200,00 TL · Sepette 149.499,00 TL
     Hepsiburada Takip Bildirimi · kargo hariç
     https://www.hepsiburada.com/tcl-65c7k-…-p-HBCV…
```
Desteklenmeyen/okunamayan mağaza **sessizce yutulmaz**, açıkça söylenir.

---

## 6. Risk tablosu

| Risk | Etki | Önlem |
|---|---|---|
| `bot.py` değişikliği Akakçe takibini bozar | **yüksek** | Ayrı `try/except`, Akakçe yönlendirmesi **değişmez**, F4 testlerinde "Akakçe linki eskisi gibi" maddesi |
| Hepsiburada Akamai 403/503 | orta | 3 ardışık hatada tek uyarı + üstel geri çekilme; alarm yok (fail-closed) |
| Mağaza sayfası yapısı değişir | orta | C4 çapraz doğrulama: tutmazsa "okuma başarısız", yanlış fiyat **gösterilmez** |
| Taksit/puan fiyat sanılır | **yüksek** | JSON-LD kuruş değeri ana blokta aynen geçmeli (MediaMarkt'ta gerçek tuzak: 22.499,75) |
| n11'de yanlış satıcı | orta | `?magaza=` **korunur** |
| Akakçe'nin listelemediği mağaza | düşük | Senin verdiğin link + F6 dürüst kapsam etiketi (`4/4`, `10/16`) |
| Trendyol | — | Ülke duvarı tespiti + Akakçe satırı (F7) |
| Amazon | — | Fiyat takibi yok; robots'a saygı (F9) |
| Public repo'da fiyat geçmişi | orta | Geçmiş commit **edilmez**; `store_watches.json` yalnız hedef+url |
| Actions maliyeti | düşük | Repo **public** → standart runner dakikaları ücretsiz; probe yalnız elle |
| Test tabanı kayar | orta | Taban **904 test OK**; her fazda sayı düşerse faz kapanmaz |

---

## 7. Test disiplini (her fazda aynı)

```
/tmp/venv/bin/python -m unittest discover -s tests
```
(Bu ortamda `telethon` gerekiyor; sistem python'unda yok → `/tmp/venv` `--system-site-packages`.)
- Bugünkü taban: **904 test OK**.
- Faz başında ve sonunda koşulur. **Sayı düşerse veya hata çıkarsa sonraki faza geçilmez.**
- Her faz kendi test dosyasını ekler: `test_store_worker.py`, `test_store_tracker.py`,
  `test_store_links.py`, `test_find_queue.py` + `tests/fixtures/stores/**` (gerçek sayfadan).

---

## 8. Geri alma (her faz için)

1. O fazın **tek commit**'ini revert et.
2. Yeni dosyalar silinir (`store_worker.py`, `store_tracker.py`, `seller_links_worker.py`…).
3. `store_watches.json` → `{"version":1,"enabled":false,"items":[]}` → takip durur.
4. Akakçe hattı hiçbir fazda etkilenmez (dosyalarına dokunulmuyor).

---

## 9. Claude'a not (kopyala-yapıştır)

```
FINAL PLAN v3 — onaylandı. Aşağıdaki 9 faz sırayla, her faz ayrı commit + test kapısı (taban 904 OK).
Dokunulmazlar: product_tracker.py, akakce_worker.py, product_watches.json, config.json,
COMMAND_DESCRIPTIONS/HELP_TEXT. C1–C7 aynen korunur.

Sıra: F1 probe → F2 store_worker(HB+MM+Teknosa) → F3 store_tracker+store_watches.json
→ F4 bot.py → F5 n11+Pazarama → F6 /magazatakipbul (satıcı linkleri) → F7 Trendyol
→ F8 çoklu seçim kuyruğu → F9 (opsiyonel) Amazon hatırlatıcı.

Trendyol: doğrudan okunamıyor (ülke seçim sayfası: /en/select-country?cb=…; çerez yasak).
  Yapılacak: (1) store_worker'da reason:"country_wall", (2) kullanıcıya net mesaj + Akakçe
  yönlendirmesi, (3) Akakçe'nin Trendyol satırı /magazatakipbul listesinde
  "[Trendyol · Akakçe üzerinden]" etiketiyle → seçilirse Akakçe ürün linki takibe girer.
Amazon: fiyat takibi YOK (robots AI botlarını ismen yasaklıyor; fiyat JS'te; Prime farkı).
  Yalnız F9: link doğrula + kaydet + haftalık hatırlatma. F9 istenmezse plan F8'de biter.

Düzeltmeler (v1'den, hepsi hâlâ geçerli):
D1 -I gerekçesi: izole modda sys.path[0] stdlib zip olur; -E -s de devrede → worker kendi
   klasörünü sys.path.insert(0, ...) ile ekler.
D2 QUERY adaptör başına: Teknosa/MediaMarkt "drop", n11/Pazarama "keep" (?magaza).
D3 store_watches.json repoda yok → F3'te {"version":1,"enabled":false,"items":[]} commit edilir.
D4 handle(event, raw, *, existing_pending=False); handled sonrası stage=="webdetakip"
   → drop_pending mağaza takipçisi için de çalışır.
D5 /magazatakip* ve /webtakipbul COMMAND_DESCRIPTIONS/HELP_TEXT'e EKLENMEZ
   (test_bot.py:259 kanonik demeti birebir karşılaştırıyor).
D6 verify: JSON-LD offers.price'ın kuruş değeri ana fiyat bloğunda aynen geçmeli; taksit
   (22.499,75), puan (+18.000), ek garanti (3.639 TL), "4 x …" satırları fiyat adayı değil.
D7 store_probe.py: host allowlist + https zorunlu + NoRedirect, allowlist dışı satır reddedilir.
D8 Her fazda README: komutlar, açma/kapama, "kargo hariç", geri alma.
D9 Kapsam gerçeği: Akakçe 65C7K'da yalnız 4 satıcı (n11, Pttavm, Hepsiburada, Pazarama);
   Trendyol/Amazon/idefix/Teknosa/MediaMarkt yok. Adaptörler "taze + yedek" değer katar.

Ek düzeltmeler:
N1 Çoklu seçim: product_tracker.py:291 self.drafts[chat] TEK yuva, 300-306 tek ürün+tek hedef.
   F8'de kendi kuyruğu tutulacak, product_tracker.py'ye dokunulmayacak.
N2 /magazatakipbul: akakce_worker.parse_page url alanını ATIYOR ({price,seller}).
   akakce_worker.py değişmez → yeni seller_links_worker.py: sayfayı 1 kez indir,
   parse_page'i çağır + kendi JSON-LD yürüyücüsüyle price+seller+url çıkar.
   Kapsam etiketi dürüst: 65C7K 4/4, fritöz fixture 10/16.
N3 F6/F8 yönlendirmesinde existing_pending şartı açıkça uygulanır (ayar taslağı varken
   /magazatakipbul araya girmez).
N4 "28/28 TV kabul" yalnız televizyon/tcl-65-inc.html fixture'ı için; ?q= fixture'ında askı
   aparatı reddedilmeli (iki ayrı test).
N5 Aday satırı: "1) 65C6K · 60.990,24 TL · 28 satıcı" + ad 45 karaktere kırpılır.
N6 verify: liste ve sepette ikisi de > 0, aynı para birimi; sepette yoksa liste +
   "(sepette fiyat okunamadı)".
```

---

## 10. Komut listesi (yeni, hiçbiri yardım menüsüne eklenmez)

| Komut | İş |
|---|---|
| `/magazatakip <link>` | Mağaza linkini takibe al (hedef fiyat sorar) |
| `/magazatakipac` · `/magazatakipkapat` | Mağaza hattını aç / kapat (varsayılan **kapalı**) |
| `/magazatakipler` | Takip listesi + sağlık |
| `/magazatakipsil N` | N. kaydı sil |
| `/magazatakipiptal` | Taslağı iptal |
| `/magazatakipyardim` | Kendi kısa yardımı |
| `/magazatakipbul <akakçe linki>` | **Akakçe ürünündeki satıcı linklerini listele** (F6) |
| `/magazatakipamazon <link>` | Amazon hatırlatıcı (F9, opsiyonel) |

---

## 11. Onay

**"F1'e başla"** → probeyi yazarım, testleri koştururum (**904 + yeni**), sonucu raporlarım
(değişen dosyalar, test sonucu, geri alma), sonra F2'ye geçerim. Arada bekleme yok; tek kapı test.
