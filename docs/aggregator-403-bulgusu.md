# Cimri + Epey 403 bulgusu (2026-11-08, canlı ölçüm)

## Ne oldu

Gerçek linklerle ilk canlı deneme **başarısız** oldu:

> ⚠️ Ürün takip işlemi tamamlanamadı: Epey okunamadı; erişim engeli veya sayfa değişikliği olabilir

## Ölçüm (GitHub Actions, teşhis işi)

| Site | HTTP | Süre | Bayt | JSON-LD |
|---|---|---|---|---|
| www.cimri.com | **403 / 403 / 403** | 234 ms | —

| www.epey.com | **403 / 403 / 403** | 140 ms | —

## Neyi kanıtlıyor

1. **Sayfa değişmedi.** 140 ms'de yanıt gelmiş; sayfa hiç indirilmemiş. Ayrıştırıcı hiç çalışmadı.
2. **Link biçimi doğru.** Üç denemede de aynı sonuç; ayrıca aynı URL'ler tarayıcıdan açılıyor.
3. **Engel kimlik/konum bazlı.** Aynı gün `fetch_page` (farklı ağ) ile iki site de sorunsuz okundu.
   Yani site içeriği bizim için açık; **GitHub Actions'ın IP aralığı** reddediliyor.

## Neden böyle: F1 ölçümüyle aynı desen

F1'de 7 mağazanın 5'i (n11, MediaMarkt, Teknosa, Pazarama + Amazon captcha) Actions'tan **403**
döndü, tarayıcıdan açıldı. Cimri ve Epey aynı davranışı gösteriyor: **veri merkezi IP'leri
engelleniyor**. Bu, "siteyi bozduk" değil, "site bu adresi tanıyor" demek.

## Bu yüzden yapılanlar

| Değişiklik | Gerekçe |
|---|---|
| `aggregator_worker.error_code()` | 403 → `BLOCKED`, 429 → `RATE_LIMITED`, ağ → `NETWORK`, ayrıştırma → `PARSE`. Böylece kullanıcıya **doğru** cümle yazılıyor. |
| `product_tracker.read_failure_text()` | "sayfa değişikliği olabilir" yanlış teşhisi kaldırıldı. Artık: *"sunucu bu bağlantıyı reddetti (HTTP 403). Sayfa değişmedi; site veri merkezi IP'lerini engelliyor."* |
| `tools/aggregator_diag.py` | Aynı URL'yi **4 farklı başlık kümesiyle** dener (mevcut UA / tarayıcı UA / tam başlıklar / ikisi). Sonuç "başlık farkı" ise düzeltilebilir; "dördü de 403" ise IP engeli kesinleşir. |
| `.github/workflows/aggregator-probe.yml` | Teşhis işine bu test eklendi; sonuç doğrudan Actions özetinde tablo olur. |

## Yapılmayacaklar (kural)

- ❌ Proxy / VPN / veri merkezi dışı tünel — "proxy yok" kuralı.
- ❌ Çerez/oturum, hesap açma — C2 kuralı.
- ❌ CAPTCHA/JS çözen araçlar (Playwright, curl_cffi, Botasaurus) — reddedildi.
- ❌ Header taklidiyle engel aşmaya çalışmak: sonuç "kimlik gizleme" olur, kural dışı.

## Sıradaki karar (kullanıcıya iki seçenek)

**A. Akakçe hattı gibi kalsın, Cimri/Epey "kapalı" dursun.**
   Aynı kod tarayıcıdan/başka ağdan çalışır; GitHub Actions'tan çalışmaz. Ürün eklerken
   Cimri/Epey linki gönderilirse artık **dürüst** hata mesajı görülür (403).

**B. Cimri/Epey'i başka bir okuma yolundan besle.**
   Örn. `fetch_page` benzeri bir araç (bizim botumuz değil, dışarıdan okuma) ile periyodik
   veri almak. Bu, "proxy yok" kuralının sınırında; ayrıca yeni bir bağımlılık demek.
   Ayrıntılı konuşulmalı; tek başına karar verilmez.

Not: Akakçe hattı bu bulgudan **etkilenmedi** — Akakçe Actions'tan okunabiliyor (ölçüldü,
200/200/200).
