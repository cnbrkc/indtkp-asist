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

---

## Güncelleme 2 — 6 varyantlı teşhis (2026-11-08 16:33 koşusu)

İkinci koşu (YAML düzeltildikten sonra) sonucu:

| Site | HTTP | Kod | Süre |
|---|---|---|---|
| Cimri | 403/403/403 | `BLOCKED` | 260 ms |
| Epey | 403/403/403 | `BLOCKED` | 154 ms |

Mesaj artık doğru: *"sunucu bu bağlantıyı reddetti (HTTP 403)"* — "sayfa değişti" demiyor.

### Kalan tek soru: IP mi, istemci parmak izi mi?

Python/urllib'in TLS parmak izi bilinen bir WAF imzasıdır. Bu yüzden teşhis aracı artık
**6 varyant** deniyor:

| # | İstemci | Başlıklar |
|---|---|---|
| 1 | urllib | mevcut worker başlıkları |
| 2 | urllib | tarayıcı UA |
| 3 | urllib | tarayıcı UA + tam başlık seti |
| 4 | urllib | mevcut UA + tam başlık seti |
| 5 | **curl** | mevcut UA |
| 6 | **curl** | tarayıcı UA + tam başlık seti |

Karar mantığı:

- curl 200, urllib 403 → **istemci parmak izi** → düzeltilebilir (worker curl'e geçer).
- Hepsi 403 → **IP engeli** → bu makineden okunamaz; başlıkla/istemciyle aşılamaz.
- Bazı urllib varyantları 200 → **başlık farkı** → tek satırlık düzeltme.

---

## KESİN SONUÇ — 2026-11-08 17:07 koşusu (6 varyant, iki istemci)

| Site | urllib × 4 | curl × 2 | Karar |
|---|---|---|---|
| Cimri | 403, 86 ms | 403, 87 ms (14.591 bayt), 73 ms (9.241 bayt) | **engel** |
| Epey | 403, 65–112 ms | 403, 51 ms (5.531 bayt), 103 ms (3.319 bayt) | **engel** |

**Hepsi 403.** Başlıklar değişti (tarayıcı UA, tam başlık seti), istemci değişti (curl, farklı
TLS yığını) — sonuç aynı. Yani engel **istek içeriğinden bağımsız**, **IP/veri merkezi**
kaynaklı. Kodla aşılamaz.

Not: 403 ile birlikte 3–15 KB'lık bir **engel sayfası** dönüyor (WAF'ın standart davranışı).
Bu yüzden "403 ama gövde var" yanıltmasın; `code: BLOCKED` doğru sınıflandırma.

### Kodda yapılan son değişiklik

`PLATFORM_AGGREGATOR_BLOCKED = {'cimri', 'epey'}`: bu platformdan okunamadığı **ölçülmüş**
siteler için link kuyruğa **hiç girmiyor**. Kullanıcı anında, doğru sebeple cevap alıyor:

> Epey okunamadı: sunucu bu bağlantıyı reddetti (HTTP 403). Sayfa değişmedi; site veri
> merkezi IP'lerini engelliyor.
>
> Epey linki kaydedilmedi; bu hâliyle takip edilemez. Akakçe linki kullanabilirsin.

Akakçe hattı **hiç etkilenmiyor** (test: `test_akakce_is_completely_unaffected`).
Daha önce kaydedilmiş Cimri/Epey kayıtları dosyada bozulmadan durur.

### Ne yapılabilir, ne yapılamaz

| Yaklaşım | Durum |
|---|---|
| Daha iyi başlıklar | ❌ ölçüldü, işe yaramadı |
| curl / farklı TLS istemcisi | ❌ ölçüldü, işe yaramadı |
| Daha yavaş/yavaş istek | ⛔ anlamsız: engel 403, hız sınırı 429 değil |
| Proxy / VPN / tünel | ⛔ kural: "proxy yok" |
| Çerez/oturum, CAPTCHA çözücü | ⛔ kural: C2 |
| Farklı ağdan okuma (kullanıcının makinesi ya da dışarıdan bir okuyucu) | ⚠️ sınırda; kullanıcı kararı |
