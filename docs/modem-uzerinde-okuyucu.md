# Modemden çıkmak: Keenetic (Entware) üzerinde yerel okuyucu

Soru: *"Bilgisayar sürekli açık kalamaz ama ya peki modemimden çıkarsak?"*

Kısa cevap: **evet, bu doğru fikir — ve önerilen yolların en iyisi.** Modem
zaten 7/24 açık, zaten ev IP'sinden çıkıyor, ek donanım/sunucu/ücret yok.

Bu belge üç şeyi ayırır: **neyin gerçekten işe yaradığı**, **neyin yaramadığı**,
ve **adımlar**.

---

## 1. Dürüst uyarı: "önce modemde dene" (5 dakika, bedava)

Modemde kurulum yapmadan önce **tek bir komutla** o ağın engellenip
engellenmediğini gör. Çünkü bazı ISS'lerde CGNAT (ortak IP) vardır ve o IP
havuzu da veri merkezi kadar kirli olabilir.

Telefonun modem Wi-Fi'sine bağlıyken bilgisayarda:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://www.epey.com/televizyon/tcl-65c6k.html
```

- `200` → ev ağı temiz, modem planı çalışır. Devam et.
- `403` + `Just a moment...` → bu ağ da challenge'a düşüyor; modemde kurulum
  yapmanın anlamı olmaz. (Bu durumda tek yol cep hotspot'u ya da beklemek.)

Not: 403 alırsan bu **kesin** sonuçtur; modemde Python çalıştırmak bunu
değiştirmez — engel kodla değil, IP/JS doğrulamasıyla ilgili (bkz.
`docs/engel-asma-arastirmasi.md`).

## 2. Neden önerilen diğer yollar yaramaz (ölçüldü / mantık)

| Öneri | Değerlendirme |
|---|---|
| **Tailscale exit node** | ❌ Evet, "trafik evden çıkar" ama **evdeki cihaz açık kalmak zorunda**. O cihaz zaten bir bilgisayar — yani "bilgisayar açık kalamaz" kısıtını çözmüyor, sadece yerini değiştiriyor. Ayrıca GitHub Actions'ta her koşuya Tailscale bağlantısı + auth key gerekir; süre kısıtlı işlerde kırılgan. |
| **`bypass-cloudflare-for-github-action` benzeri Action'lar** | ❌ **Yanlış yönlendirme.** Bunlar sizin Cloudflare **hesabınızda** zone/firewall ayarı değiştirir. Cimri/Epey sizin hesabınız değil — onların Cloudflare'ine erişemezsiniz. Bu Action'lar **kendi sitenizi** test ederken işe yarar, başkasının sitesinde hiçbir şey yapmaz. |
| **`HTTP_PROXY` / `HTTPS_PROXY`** | ❌ Ücretsiz proxy'lerin hepsi ölçüldü (r.jina.ai, allorigins, codetabs, corsproxy, cors.lol…) → 403/500/522/hız sınırı. Ücretli konut proxy'si kural dışı. |
| **Botu modeme taşımak** | ✅ **Tek gerçek çözüm.** Aşağıda. |
| **Oracle Cloud Always Free** | ⚠️ Teknik olarak çalışabilir ama **kart doğrulaması** ister ve IP'si **veri merkezi**dir — yani bugün engellenen IP türünün aynısı. "Ücretsiz VPS" engeli çözmez, sadece yerini değiştirir. Ayrıca 7/24 sizin bakımınızda bir sunucu olur. |

Modem fikrinin diğerlerinden farkı: **çıkış IP'si gerçekten ev IP'si** olur.
Sorun da tam olarak buydu.

## 3. Adımlar

### 3.1 Modeme Entware kur

1. Keenetic arayüzü → **Uygulamalar / OPKG** (Opkg disk kurulumu). Bir USB bellek
   tak (ext4 önerilir), Entware'i oraya kur.
2. SSH aç: **Yönetim → Kullanıcılar ve erişim**, bir kullanıcıya SSH izni ver.
3. Bağlan: `ssh root@192.168.1.1` (Keenetic'te kullanıcı adı `root`).

```sh
opkg update
opkg install python3 python3-urllib python3-json python3-ssl
```

> `python3-ssl` şart: yoksa HTTPS isteği atılamaz. Kurulumda hata alırsan
> `python3-light` yerine tam `python3` kurulu olduğundan emin ol.

### 3.2 Dosyayı ve ayarları koy

```sh
mkdir -p /opt/etc/indtkp
cd /opt/etc/indtkp
# yerel_okuyucu.py, aggregator_worker.py ve product_watches.json buraya
```

Modemde `git` gerekmez; **tek dosya + standart kütüphane** yeterli.

### 3.3 Token'ı ortam değişkenine koy

GitHub → Settings → Developer settings → **Fine-grained token**:
- Repository access: yalnız `indtkp-asist`
- Permissions: **Contents: Read and write** (başka hiçbir izin yok)

```sh
cat > /opt/etc/indtkp/env.sh <<'SH'
export GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxx
SH
chmod 600 /opt/etc/indtkp/env.sh
```

> Bu token **yalnız** besin dosyasını yazabilir. `.git/config`e yazılmaz;
> yalnız HTTPS başlığında kullanılır (test: `tests/test_yerel_okuyucu_push.py`).

### 3.4 Elle bir kez dene

```sh
cd /opt/etc/indtkp
. ./env.sh
python3 yerel_okuyucu.py --watch product_watches.json \
  --push https://github.com/cnbrkc/indtkp-asist.git \
  --gap 60 --period 900
```

Beklenen çıktı:

```
İzleme başladı: product_watches.json → aggregator_feed.json → depo:aggregator_feed.json@main
[12:00:00] 2 ürün okunuyor
  ✅ https://www.epey.com/... → 42999.00 TL (Trendyol, 5 mağaza)
  depo: pushed (a1b2c3d)
```

Depoda `aggregator_feed.json` göründüyse iş tamam. **Bilgisayar kapalı olabilir.**

### 3.5 7/24 çalıştır (modem yeniden başlasa da)

```sh
cat > /opt/etc/init.d/S99indtkp <<'SH'
#!/bin/sh
ENABLED=yes
PROCS=yerel_okuyucu.py
ARGS="--watch product_watches.json --push https://github.com/cnbrkc/indtkp-asist.git --gap 60 --period 900"
PREARGS=""
DESC="indtkp yerel okuyucu"
PATH=/opt/sbin:/opt/bin:/opt/usr/bin:/usr/sbin:/usr/bin:/sbin:/bin

. /opt/etc/init.d/rc.func
SH
chmod +x /opt/etc/init.d/S99indtkp
/opt/etc/init.d/S99indtkp start
```

Kontrol: `/opt/etc/init.d/S99indtkp status`

## 4. Ne kadar kaynak yer?

Ölçüm (dev makinede, gerçek sayfa ayrıştırması ile):

| Ölçüm | Değer |
|---|---|
| Besin okuma (bot tarafı) | ort. **0,014 ms** |
| Tek sayfa okuma + ayrıştırma | ~0,3–1,5 s (ağ baskın) |
| Tur aralığı | 900 sn (15 dk) → günde 96 istek/ürün |
| RAM (Python + 1 süreç) | ~35–60 MB |
| Disk | < 5 MB (kod + besin) |

Modem için önemli olan: **istekler arası 60 sn boşluk** korunur, paralel istek
yok → ban riski yok.

## 5. Sınırlar (dürüst)

- Modem yeniden başlarsa servis `S99` ile kendiliğinden geri gelir.
- İnternet kesilirse o tur atlanır; **depodaki eski besin korunur**, bot
  "bayat" diyip fiyat göstermez (fail-closed).
- Besin 6 saatten eskiyse bot onu kullanmaz — yani 900 sn'lik tur rahatça yeter.
- Modemde Python yoksa/kuramıyorsan alternatif aynı komutu bir **Raspberry Pi**,
  eski bir Android telefon (Termux) veya NAS üzerinde çalıştırmak: kod aynı.
