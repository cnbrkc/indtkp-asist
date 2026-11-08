# F1 ölçüm sonuçları (2026-11-08, GitHub Actions IP'sinden)

Bu belge **gerçek ölçümün** sonucudur. Çalıştırma: `store-probe` workflow'u, GitHub Actions
(`ubuntu-latest`) IP'sinden, anonim tek GET, `NoRedirect`, 3 istek/URL.

Ürün: **TCL 65C6K** (65" QD-Mini LED) — her sitede aynı ürün.

---

## 1. Sonuç tablosu (ölçüm, tahmin değil)

| Host | Durum | HTTP | Gecikme | JSON-LD Product | Görünür fiyat | Sonuç |
|---|---|---|---|---|---|---|
| **www.hepsiburada.com** | **okunabilir** | 200/200/200 | **845 ms** | **1** (`TCL 65C6K`, `66444.03 TRY`, `InStock`, satıcı `TCL`) | 13 | ✅ **ÇALIŞIYOR** |
| www.amazon.com.tr | engel/captcha | 200/200/200 | 420 ms | 0 | 0 | ❌ 4 KB captcha sayfası |
| www.n11.com | engel | 403/403/403 | — | 0 | 0 | ❌ |
| www.mediamarkt.com.tr | engel | 403/403/403 | — | 0 | 0 | ❌ |
| www.teknosa.com | engel | 403/403/403 | — | 0 | 0 | ❌ |
| www.pazarama.com | engel | 403/403/403 | — | 0 | 0 | ❌ |
| www.trendyol.com | engel | **yönlendirme** | — | — | — | ❌ `/en/select-country` (coğrafya duvarı) |

**Özet: 7 sitenin 1'i okunabiliyor (Hepsiburada), 5'i 403, 1'i coğrafya duvarı.**

---

## 2. Bunun anlamı

Beklenti (plan v4): MediaMarkt "[AI crawler'ları açıkça serbest bırakmış](docs/magaza-takip-plani.md)"
→ okunur. **Gerçek: 403.** Sebep robots değil: robots *kural* söyler, **WAF/Akamai** ise
istek sahibine bakar. GitHub Actions IP havuzu bilinen bir veri merkezi aralığı olduğu için
**User-Agent ve robots ne olursa olsun** bu siteler 403 döndürüyor.

Yani planın 13.6 tablosundaki `min_gap` / eşzamanlılık ayarları **ikinci dereceden** mesele:
o sitelere **hiç istek geçmiyor**. Hız sınırı değil, **erişim** sorunu var.

---

## 3. Üç seçenek (karar senin)

### A) Yalnız Hepsiburada ile başla (mevcut altyapı, bugün)
- Hepsiburada **çalışıyor**: JSON-LD 1 Product, fiyat `66.444,03 TRY`, stok `InStock`, satıcı `TCL`.
- Akakçe hattı + Hepsiburada hattı: iki bağımsız kaynak.
- Diğerleri 403 dönerse **sessizce yutulmaz**, kullanıcıya "bu mağaza okunamıyor, Akakçe satırıyla
  takip edilecek" denir (F6/F7 mantığı).
- **Maliyet: sıfır risk, sıfır yeni altyapı.**
- Eksik: "8 mağaza birlikte" hedefinin yalnız 1'i doğrudan okunur.

### B) Ev bilgisayarı runner'ı (self-hosted)
- GitHub Actions'a **kendi makinemizi** runner olarak bağlarız (`runs-on: self-hosted`).
- Ev/mobil IP'si olduğu için 403'ler büyük olasılıkla kalkar (bu IP'ler normal kullanıcı gibi görünür).
- **Maliyet:** makine 7/24 açık kalmalı; Telegram oturumu ve token'lar o makinede durur;
  GitHub'ın public repo'da self-hosted runner önerisi "fork PR'ları tehlikeli" uyarısı taşır —
  ama bizim workflow'lar yalnız `workflow_dispatch`/`schedule` ile çalışıyor, PR tetiklemiyor.
- Ayrıntı: yalnız **mağaza hattı** self-hosted'da, Telegram/Akakçe hattı Actions'ta kalabilir
  (iki ayrı workflow, tek `store_watches.json`).

### C) Kiralık proxy / ticari scraping API
- **Reddediyorum.** Senin kuralın: "proxy, hesap/oturum, gayriresmî API yok". Ayrıca ücretli.

---

## 4. Önerim

**A ile başla, B'yi Hepsiburada dışındaki 5 mağaza için sonra aç.**

Gerekçe:
1. Hepsiburada JSON-LD'si **temiz** (tek Product, TRY, stok, satıcı) → F2'nin `verify()` kuralı
   gerçek sayfayla test edilebilir. Altyapı (worker + tracker + bot bağlantısı) bu tek siteyle
   **uçtan uca** doğrulanır.
2. B (self-hosted runner) altyapıyı değiştirmez; yalnız `runs-on` ve ağ konumu değişir.
   Yani A'yı bitirmek B'yi **kolaylaştırır**, engellemez.
3. 403'lerin UA/Referer ile aşılması **beklenmiyor** (denenebilir ama plana bağımlı hâle
   getirmemek gerekir). Bu ölçüm bir sonraki probe sürümünde test edilecek.

---

## 5. Kanıt: Hepsiburada gerçek verisi

```
JSON-LD (1 blok parse edildi):
  @type   : Product
  name    : TCL 65C6K 65'' 165 Ekran Uydu Alıcılı 4K Ultra HD MiniLED Google TV
  offers  : price=66444.03  currency=TRY  availability=InStock  seller=TCL
Görünen fiyat adayları (13):
  66.444,03 TL  ← JSON-LD ile EŞLEŞTİ ✅
  30.499,00 / 55.736,83 / 82.799,00 / 103.321,22 / 265.806,83 TL  ← diğer ürün/satıcı blokları
  14,90 TL (kargo/abonelik satırı), 5 TL
Sayfa boyutu: 178.793 bayt · gecikme: 148–1454 ms (ort. 845)
```

## 6. Kanıt: engeller
- **Amazon:** HTTP 200 ama **4.204 bayt** → captcha sayfası. `captcha` işareti yakalandı.
  Görünen fiyat 0. (Beklenen: `robots.txt` AI botlarını ismen yasaklıyor.)
- **Trendyol:** GET → `/en/select-country` **yönlendirmesi**; `NoRedirect` reddetti (doğru davranış).
- **n11 / MediaMarkt / Teknosa / Pazarama:** düz **403** (1669–9691 bayt engel sayfası).

---

## 7. Probun kendisinde düzeltilecek 3 şey (bu ölçümde görüldü)

1. **Engellenen sayfada analiz yapılmıyor gibi görünüyor ama yapılıyor:** Amazon captcha
   sayfasında `"price"` deseni **42 kez** eşleşti. Bunlar anlamsız (captcha script'i).
   → Engel işareti varsa **fiyat analizi atlanmalı**, satır `engel/captcha` kalmalı.
2. **403 gövdesi saklanmıyor:** hangi WAF'ın engellediğini görmek için engel sayfasının
   ilk ~2 KB'ı artefakta eklenmeli (Akamai mi, Cloudflare mi, özel 403 mü).
3. **Boş `snippet`:** engellenen sitede snippet 0 bayt. Engel gövdesi snippet'e girmeli.

Bu üçü bir sonraki küçük PR'da düzeltilecek; sonra **A yolu** (Hepsiburada ile F2) başlıyor.

---

## 8. Plan üzerindeki etkisi

| Plan maddesi | Ölçüm sonrası |
|---|---|
| F2: Hepsiburada + MediaMarkt + Teknosa | **F2: yalnız Hepsiburada** (diğerleri 403) |
| F5: n11 + Pazarama | **B (self-hosted) veya Akakçe yedeği** olmadan yapılamaz |
| F6/F7: Trendyol | Coğrafya duvarı **doğrulandı** → Akakçe Trendyol satırı yedeği zaten planda ✅ |
| F9: Amazon | Captcha **doğrulandı** → fiyat takibi yok; hatırlatıcı modu zaten planda ✅ |
| 13.6 `min_gap` tablosu | Hepsiburada için geçerli; diğerleri **erişim** engeli yüzünden devre dışı |
