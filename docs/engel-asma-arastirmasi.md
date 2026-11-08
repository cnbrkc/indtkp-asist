# Deep search: Cimri/Epey engelini sunucu/proxy/ücret olmadan aşmanın yolu var mı?

Tarih: 2026-11-08. Amaç: kullanıcının sorusu — *"github ve internette bu engeli aşmanın sunucu
bilgisayar ya da proxy olmadan başka hiç mi yolu yok"*. GitHub, bloglar, ürün sayfaları ve
Türkçe kaynaklar tarandı.

## 1. Önce doğru teşhis: engel ne tür?

Cloudflare üç ayrı sebeple 403 verir ve **çözümleri farklıdır**:

| 403 türü | Sayfada görünen | Ne işe yarar |
|---|---|---|
| **Blocked** (IP itibarı / WAF kuralı) | "Attention Required / Why have I been blocked" | **Yalnız** IP değişimi. Tarayıcı, TLS taklidi, başlık — **hiçbiri yaramaz** |
| **Challenge** (tarayıcı doğrulaması) | "Just a moment…" / Turnstile | Gerçek tarayıcı (nodriver, Camoufox) veya çerez |
| **TLS/JA3 uyuşmazlığı** | Genelde "Blocked" | `curl_cffi` / `curl-impersonate` (Chrome'un BoringSSL'i) |

**Bizim ölçümümüz:** 6 varyant (4 başlık kümesi × urllib + 2 × curl) → **hepsi 403**.
Kaynaklara göre TLS uyuşmazlığı olsaydı `curl_cffi`/curl-impersonate çözerdi — ama bizim
curl testi **Chrome taklidi değildi**, düz curl'dü. Yani **bu ihtimal henüz test edilmedi.**

## 2. Taranan yöntemler ve durumları

| # | Yöntem | Ücret | Sunucu/proxy | Durum |
|---|---|---|---|---|
| 1 | **`curl_cffi` / `curl-impersonate`** (Chrome TLS+HTTP/2 taklidi) | Ücretsiz (pip) | Gerekmez | ⚠️ **TEST EDİLMEDİ — en güçlü ücretsiz aday.** Kaynaklar: "TLS-only korumada 403→200" |
| 2 | Tam tarayıcı başlık seti | Ücretsiz | Gerekmez | ❌ Ölçüldü, işe yaramadı |
| 3 | `nodriver` / `SeleniumBase UC` / `Camoufox` (gerçek tarayıcı) | Ücretsiz | Gerekmez | ⚠️ Yalnız **challenge** varsa işe yarar; "blocked" ise yaramaz. GitHub Actions'ta ağır (indirme + RAM) ve Telegram döngüsüne risk |
| 4 | GitHub Actions'ı **kullanıcının kendi runner'ı** olarak çalıştırmak (self-hosted runner) | Ücretsiz | Kullanıcının bilgisayarı | ⚠️ Teknik olarak "sunucu" değil ama **bilgisayarı açık tutmayı** gerektirir — zaten yerel okuyucu ile aynı işi yapar, daha karmaşık |
| 5 | **Cloudflare Workers** ücretsiz planı (100.000 istek/gün, sunucu yok) | Ücretsiz | Gerekmez (edge) | ⚠️ Denenebilir ama Cloudflare'in **kendi IP'leri** de sık sık engellenir; ayrıca hesap açmayı gerektirir |
| 6 | Hazır ücretsiz CORS proxy'leri (allorigins, codetabs, corsproxy, cors.lol, cors.workers.dev) | Ücretsiz | Var | ❌ **Ölçüldü:** 500 / 522 / API anahtarı / hız sınırı / 403 |
| 7 | `r.jina.ai` (ücretsiz okuma servisi) | Ücretsiz | Var | ❌ **Ölçüldü:** 403 (onun IP'si de engelli) |
| 8 | Google Translate proxy (`*.translate.goog`) | Ücretsiz | Var | ❌ **Ölçüldü:** "Just a moment…" |
| 9 | Wayback Machine canlı kayıt zorlama | Ücretsiz | Var | ❌ **Ölçüldü:** "Job failed"; mevcut kopya Eylül 2025 |
| 10 | Arama motoru önbelleği | Ücretsiz | Var | ❌ Önbellek kaldırıldı; fiyat taşımıyor |
| 11 | **Epey/Cimri mobil uygulama API'si** ("gizli API") | Ücretsiz | Gerekmez | ⚠️ Kaynaklarda doğrulanmadı; `api.epey.com` açılmadı. Uygulama trafiği incelenmeden iddia edilemez — ayrıca "gayriresmî API yok" kuralına girer |
| 12 | Apify hazır Epey/Cimri scraper'ları | **Ücretli** ($1.50/1000) | Var | ❌ Ücret kuralı |
| 13 | Ev interneti / cep hotspot (yerel okuyucu) | Ücretsiz | Kullanıcının ağı | ✅ **Çalışıyor** — mevcut çözüm |
| 14 | Ücretsiz VPS/kredi (Oracle, Fly, Render) | "Ücretsiz" | Var | ❌ Kayıt + kart doğrulaması; kural ruhuna aykırı |

## 3. Dürüst cevap: "hiç mi yolu yok?"

**Var, tek bir tanesi denenmedi: `curl_cffi` (Chrome TLS taklidi).**

Gerekçe: bugüne kadar denediğimiz her şey aynı IP'den **farklı başlıklar** veya **farklı ama
yine "bot" görünen** bir TLS istemcisiydi (curl). `curl_cffi` ise Chrome'un **gerçek BoringSSL
parmak izini ve HTTP/2 ayarlarını** kullanır. Kaynaklardaki tablo:

> indeed.com: requests 403 → curl_cffi 200 · glassdoor 403 → 200 · capterra 403 → 200
> stockx / crunchbase / stackoverflow: 403 challenge (o zaman tarayıcı şart)

Yani **eğer bizim 403'ümüz TLS kaynaklıysa**, bu yöntem çözer. Eğer **IP itibarı** kaynaklıysa
çözmez — ve bunu ancak deneyerek anlarız.

## 4. ÖLÇÜM SONUÇLARI (2026-11-08, GitHub Actions, engelli IP)

Teşhis aracı 8 varyanta çıkarıldı ve ölçüm engelli IP'den (GitHub runner) koşuldu.

### 4.1 HTTP istemcileri — hepsi 403

| Varyant | Epey | Cimri |
|---|---|---|
| urllib · mevcut UA | 403 | 403 |
| urllib · tarayıcı UA | 403 | 403 |
| urllib · tarayıcı UA + tam başlık | 403 | 403 |
| urllib · mevcut UA + tam başlık | 403 | 403 |
| curl · mevcut UA | 403 | 403 |
| curl · tarayıcı UA + tam başlık | 403 | 403 |
| **curl_cffi · Chrome TLS** | **403** | **403** |
| **curl_cffi · Safari TLS** | **403** | **403** |

→ TLS parmak izi engel **değil**. Chrome'un BoringSSL'i bile aynı cevabı aldı.

### 4.2 403'ün kimliği — IP engeli değil, **JS challenge**

| Site | Başlık | WAF imzası | Karar |
|---|---|---|---|
| Epey | `Just a moment...` | Cloudflare | **challenge** |
| Cimri | `Güvenlik doğrulaması \| Cimri` | kendi doğrulaması | **challenge** |

`cf-mitigated: challenge` tarzı cevap: sunucu "IP'ni beğenmedim" demiyor,
"**önce JavaScript'ini çalıştır**" diyor.

### 4.3 Gerçek tarayıcı — son ve kesin test

Runner'da Chrome kurulu (`Chrome kontrolü` adımı geçti: example.com açıldı),
`nodriver` ile başsız Chrome, bot sinyalini azaltan argümanlarla, **48 saniye**
challenge'ın çözülmesini bekledi:

```
başlık="Just a moment..." · bekleme=48.0 sn · JSON-LD=yok
→ TARAYICI DA GEÇEMEDİ
```

Bu, sorunun son halkası: **veri merkezi IP'sinden gelen gerçek Chrome bile
geçemiyor.** Challenge IP itibarına bağlı olarak etkileşimli doğrulama
istiyor (Cloudflare "managed challenge" / Turnstile). Ücretsiz ve otomatik
hiçbir yol bunu geçemez — geçen tek şey, **o sitede daha önce normal
davranmış bir tarayıcı profili** (çerez) olur; o da bizde yok.

## 5. Neden eski öneri

Eski plan "curl_cffi deneyelim, 200 gelirse çalışır" idi. **Denendi: 403.**
Ardından "challenge ise gerçek tarayıcı çözer" dendi. **Denendi: o da geçemedi.**

Yani artık tahmin kalmadı; üç katman da (başlık, TLS, JavaScript) ölçüldü ve
hepsi aynı yerde kapandı.

## 6. Kesin cevap: "başka hiç mi yolu yok?"

**Sunucu/proxy/ücret olmadan, tamamen otomatik: YOK.** Kanıt yukarıda —
üç ayrı katman, üç ayrı ölçüm, hepsi 403, sonuncusu gerçek Chrome ile.

Kalan tek ücretsiz yol, engelin **mantığına** uyan yol: istek engelli veri
merkezi IP'sinden değil, sitelerin **normal gördüğü** bir ağdan çıksın.

| Yol | Ücret | Sunucu/proxy | Durum |
|---|---|---|---|
| **Yerel okuyucu** (`yerel_okuyucu.py`, kullanıcının kendi interneti) | 0 | Gerekmez | ✅ **Mevcut çözüm** |
| Telefon hotspot'u + aynı betik (`--gap 60`) | 0 | Gerekmez | ✅ Yedek |
| GitHub Actions + Chrome + challenge | 0 | Gerekmez | ❌ **Ölçüldü: geçmiyor** |
| Ücretsiz VPS/proxy | 0 | Var | ❌ Kayıt/kart + IP yine veri merkezi |
| Ücretli konut proxy / Apify | Ücretli | Var | ❌ Kural dışı |

Not: bu araştırma **Akakçe hattına hiç dokunmadı** (Akakçe zaten okunuyor) ve
sisteme yeni bir çalışma zamanı bağımlılığı eklemedi — `curl_cffi`/`nodriver`
yalnız teşhis workflow'unda, sadece teşhis dosyaları değişince koşuyor.
