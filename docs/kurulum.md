# Kurulum

## 1. Sıfırdan kurulum (hatırlatma listesi)

Daha önce kurduysan bu bölüm hatırlatma niyetine; toplam 5 adım, ~10 dakika.

### Adım 1 — Telegram API bilgisini al

[my.telegram.org](https://my.telegram.org) → **API development tools** → `API_ID` ve `API_HASH`.
Kaynak kanallara **kişisel hesabınla** katıl (bot hesabı kanalları okuyamaz).

### Adım 2 — Session string üret

Bilgisayar şart değil. Depodaki `session_generator_colab.ipynb` dosyasını GitHub'da açıp
**Open in Colab** ile çalıştır (notebook'u public paylaşma):

1. API ID gir → 2. API Hash gir → 3. Telefonu `+90...` biçiminde gir →
4. Gelen kodu gir → 5. İki aşamalı doğrulama varsa parolayı gir →
6. Çıkan `SESSION_STRING` değerini kopyala.

Python olan bir ortamda alternatif:

```bash
pip install -r requirements.txt
API_ID=... API_HASH=... python generate_session.py
```

> Bu değer Telegram hesabının giriş anahtarıdır. Kimseyle paylaşma.

### Adım 3 — GitHub secret'larını ekle

**Settings → Secrets and variables → Actions → Secrets → New repository secret**

| Secret | Zorunlu | Ne için |
|---|---|---|
| `API_ID` | ✅ | Telegram API kimliği |
| `API_HASH` | ✅ | 32 karakterlik API hash'i |
| `SESSION_STRING` | ✅ | Adım 2'de ürettiğin oturum anahtarı |
| `GH_PAT` | ➖ | Sadece otomatik yenileme zinciri için (aşağıda) |

Biri eksikse workflow açılışta durur ve **Ayarları doğrula** adımı hangi secret'ın boş
olduğunu Türkçe olarak log'a yazar. Aynı kontrolü yerelde de çalıştırabilirsin:

```bash
python bot.py --check
```

**GH_PAT** (opsiyonel): Actions job'u en fazla ~6 saat yaşar. Bu token, job bitmeden yeni
bir çalışma başlatarak zinciri sürer. Fine-grained token oluştururken bu repo için
**Actions: Read and write** iznini ver. Eklemezsen bot yine çalışır, sadece 6 saatte bir
elle (veya schedule ile) başlatman gerekir.

### Adım 4 — `config.json`'ı düzenle

Deponun kökündeki [`config.json`](../config.json) dosyasını GitHub'dan düzenle (kalem simgesi):

```json
{
  "source_chats": ["@indirimkanali", "-1001234567890"],
  "destination": -5092968106,
  "include_keywords": ["çay", "kahve", "şeker"],
  "exclude_keywords": ["çekiliş", "hediye"],
  "match_mode": "any",
  "copy_mode": "copy",
  "delivery_modes": ["forward", "copy", "media", "text", "link"],
  "max_media_mb": 25,
  "link_appendix": "smart",
  "message_link": true,
  "source_footer": true,
  "notify_media": true,
  "clean_commands": true,
  "clean_quote_duplicates": true,
  "single_message": true,
  "dedup_enabled": true,
  "dedup_window_hours": 12,
  "dedup_scan_limit": 100,
  "control_chat": -5092968106,
  "admin_user_id": 1143378073,
  "auto_restart": true,
  "notify_on_start": false
}
```

Alanların hepsi [3. bölümde](calisma-ve-ayarlar.md#3-ayarlar-configjson) tek tek anlatılıyor. ID bilmiyorsan
[10. bölüm](sorun-giderme-ve-guvenlik.md#10-idleri-doğrulama)deki `/id` komutunu kullan.

> **ID'ler tırnak içinde de yazılabilir** (`"-5092968106"`); bot sayıya çevirir.

### Adım 5 — Çalıştır ve test et

1. **Actions → Telegram indirim takipçisi → Run workflow → `main`** → çalıştır.
2. **Ayarları doğrula** adımının log'unda yeşil "yapılandırma geçerli ✅" görmelisin.
3. **Mesajları dinle** adımında `Bağlanıldı: ...` ve `Dinleniyor...` satırları gelmeli.
4. Kontrol sohbetine `/durum` yaz → bot yanıt veriyorsa komut yolu çalışıyor.
5. `/test` yaz → hedefe deneme mesajı düşmeli.
6. Telefonuna bildirim gelsin istiyorsan [6. bölümdeki](bildirimler.md#6-bildirim-kurulumu-telefona-uyarı-gelsin) bildirim botunu mutlaka kur.

**Kurulum kontrol listesi**

- [ ] `API_ID`, `API_HASH`, `SESSION_STRING` secret'ları dolu
- [ ] `config.json` geçerli (`python bot.py --check` temiz geçiyor)
- [ ] Kaynak kanallara kişisel hesapla üye olunmuş (log'da `ÜYE DEĞİLSİN` yok)
- [ ] `/kaynaktest` her çözülmüş kaynaktan son mesajı okuyabiliyor
- [ ] `/durum` ve `/test` yanıt veriyor
- [ ] (İsteğe bağlı) Bildirim botu kurulu, `/test` bildirim gönderiyor
