# Cimri + Epey: "B yolu" — kendi ağından okuma (ücret yok)

## Neden böyle bir şey var

Ölçüm (2026-11-08, 6 varyant): Cimri ve Epey, **GitHub Actions'un IP aralığını** tanıyıp
**HTTP 403** döndürüyor. Başlık değiştirmek (tarayıcı UA, tam başlık seti) ve istemci
değiştirmek (curl'ün farklı TLS yığını) işe yaramadı. Yani engel **nereden** bağlandığımıza bakıyor.

Senin ağından (ev/telefon/ofis) aynı sayfalar **açılıyor**. O yüzden okuma işini oraya taşıyoruz:

```
   Senin bilgisayarın                      GitHub (bot)
   ┌────────────────────┐                  ┌──────────────────────┐
   │ yerel_okuyucu.py   │  aggregator_    │ bot.py               │
   │ Cimri/Epey → oku   │  feed.json      │ yalnız DOSYA okur    │
   │ 45 sn arayla       │ ──────────────► │ ağa hiç çıkmaz       │
   └────────────────────┘                  └──────────────────────┘
```

**Telegram bildirimlerin neden yavaşlamıyor:** Bot bu siteler için ağa çıkmıyor, sadece küçük
bir JSON dosyası okuyor. Ölçüm: dosya okuma **0,014 ms** (ortalama), tam kontrol **1,2 ms**.
Akakçe hattı hiç değişmedi.

## Ücret

**Sıfır.** Tek dosya, standart Python, `pip` bile gerekmez. Proxy/VPN yok, hesap yok, API
anahtarı yok.

## Kurulum (3 adım)

### 1. Dosyayı indir

`yerel_okuyucu.py` — depoda, kök dizinde. Bilgisayarına kopyala.

### 2. Bir kez dene

```bash
python yerel_okuyucu.py --url "https://www.epey.com/televizyon/tcl-65c6k.html"
```

Beklenen çıktı:

```
  ✅ https://www.epey.com/televizyon/tcl-65c6k.html → 60.927,75 TL (Trendyol, 5 mağaza)

1/1 okundu → aggregator_feed.json
```

❌ `engel: ...` derse o ağ da engellenmiş demektir (ör. bazı okul/ofis ağları). Evdeki
internet veya telefon hotspot'u ile dene.

### 3. Açık bırak

```bash
python yerel_okuyucu.py --watch product_watches.json
```

- `product_watches.json` depodaki takip listen (botun kullandığı dosya).
- Her 10 dakikada bir tur atar, istekler arasında 45 sn bekler (siteyi yormamak için).
- Bilgisayarı kapattığında okuma durur; bot elindeki son veriyi **6 saate kadar** kullanır,
  sonrasında "veri eski" der ve fiyat göstermez (yanlış fiyat göstermektense söylememek).

## Veriyi GitHub'a taşıma

İki yol var; ikisi de ücretsiz:

| Yol | Nasıl | Ne zaman |
|---|---|---|
| **Elle** | `aggregator_feed.json` dosyasını depoya yükle (GitHub web arayüzü) | Günde 1–2 kez bakıyorsan |
| **Otomatik** | Aynı dosyayı commit'leyip push eden küçük bir betik (`git add aggregator_feed.json && git commit -m feed && git push`) | Sürekli takip istiyorsan |

Dosya yoksa veya 6 saatten eskiyse bot dürüst davranır:

> Epey okunamadı: bu site veri merkezi IP'sini engelliyor (HTTP 403). Yerel okuyucu verisi
> yok veya 6 saatten eski. Çözüm: bilgisayarında `python yerel_okuyucu.py --watch
> product_watches.json` çalıştır.

## Güvenlik kuralları (korunuyor)

| Kural | Durum |
|---|---|
| Proxy / VPN / tünel | ❌ kullanılmıyor, doğrudan istek |
| Çerez / oturum / hesap | ❌ yok |
| CAPTCHA çözen araç | ❌ yok |
| robots.txt | ✅ arama/karşılaştırma yolları reddedilir |
| Ücret | ✅ sıfır |
| Telegram gecikmesi | ✅ ölçüldü: 0,014 ms |

## Sınırlar (dürüst)

- Bilgisayar kapalıysa veri tazelenmez → bot "veri eski" der, **uydurmaz**.
- Elle yükleme yapıyorsan veri en fazla o kadar taze olur.
- Fiyat doğrulaması yine iki kapıdan geçer (JSON-LD + mağaza satırlarının en düşüğü aynı kuruş).
