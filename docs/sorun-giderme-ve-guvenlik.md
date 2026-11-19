# ID doğrulama, sorun giderme ve güvenlik

## 10. ID'leri doğrulama

En hızlı yol: **gruba `/id` yaz.**

```text
🆔 Bu sohbetin ID'si: -5092968106
• Tür: Chat
• Senin kullanıcı ID'n: 1143378073
config.json için:
  "control_chat": -5092968106,
  "admin_user_id": 1143378073
```

`/id` da bir kontrol komutu olduğu için, komut hiç yanıt vermiyorsa mevcut `control_chat`
yanlış demektir: geçici olarak `"control_chat": "me"` yapıp Kayıtlı Mesajlar'dan `/id` ile
doğru ID'yi al.

> **Dikkat:** temel grup (`-5092968106` gibi) sonradan süpergruba dönüşürse ID `-100...`
> biçimine değişir ve config'i güncellemen gerekir.

---

## 11. Sorun giderme

| Belirti | Muhtemel neden | Çözüm |
|---|---|---|
| Workflow görünmüyor | PR `main`'e merge edilmedi veya Actions kapalı | PR'ı merge et; Settings → Actions'tan workflow'ları etkinleştir |
| Run 10-15 saniyede kırmızı | Bir secret boş (genelde `API_HASH`) | **Ayarları doğrula** adımının log'una bak |
| `Your API ID or Hash cannot be empty` | `API_ID`/`API_HASH` boş | İki secret'ı da ekle |
| `SESSION_STRING` yetkisiz | Session kesilmiş/iptal edilmiş | Colab notebook ile yeniden üret |
| `Cannot find any entity corresponding to "-5092968106"` | ID metin olarak verilmiş | Sayı olarak yaz (güncel sürüm otomatik çeviriyor) |
| Grup komutları çalışmıyor | `admin_user_id` boş veya `control_chat` yanlış | `/id` ile ID'leri doğrula; config'i güncelle |
| Mesaj geliyor ama bildirim yok | Kendi hesabın gönderiyor; Telegram bildirim üretmez | [6. bölüm](bildirimler.md#6-bildirim-kurulumu-telefona-uyarı-gelsin) |
| Mesaj hiç gelmiyor | Kanal listede değil / hesap üye değil / kelime eşleşmiyor | `/kaynaklar` ve `/durum`a bak; log'daki `ÜYE DEĞİLSİN` uyarılarını kontrol et |
| "Fırsata Git" var ama ham link yok | Link yazının altına gizlenmiş; tıklanabilir | `👉 Ürün Linki 👈` yazısına ya da alıntıdaki yazıya dokun. Ham URL istersen `link_appendix: "all"` |
| Fotoğraf "unnamed" dosya olarak geliyor | Eski sürüm hatası | Bot'u güncelle |
| `⚠️ ... yalnızca bu oturumda geçerli` | Ayar değişikliği depoya yazılamadı | [bakım rehberi](bakim.md) |
| `GH_PAT` ile yenileme olmuyor | Token izni yok veya süresi dolmuş | Token'da **Actions: Read and write** olduğunu ve expiration tarihini kontrol et |
| Aynı mesaj iki kez geliyor | İki job aynı anda çalışmış | Actions concurrency ayarını ve açık run'ları kontrol et |
| Aynı fırsat iki kez gruba düştü | Başlıklar birebir aynı değil, pencere doldu veya açılış taraması dışında kaldı | `/durum` satırındaki birleştirme sayacına bak; gerekirse `dedup_window_hours` değerini artır |
| `FloodWait` / rate limit | Çok fazla forward | Kaynak sayısını ve kelime filtresini daralt |

---

## 12. Güvenlik

- `SESSION_STRING`, API hash'i, bot token'ı ve telefon kodunu kimseye gösterme; `config.json`a
  secret yazma veya credential içeren değişikliği commit etme. Bildirim botu token'ı için
  `NOTIFY_BOT_TOKEN` GitHub secret'ını kullan.
- Bot token'ı daha önce GitHub'a push edildiyse BotFather'da `/revoke` ile iptal edip yenisini
  oluştur; Git geçmişinden silmek tek başına token'ı geçersiz kılmaz.
- `SESSION_STRING`/API hash geçmişte GitHub'a push edildiyse [my.telegram.org](https://my.telegram.org)
  üzerinden API uygulamasını yenile. Session geçersiz olursa yeni session üretip
  `SESSION_STRING` secret'ını güncelle.
- Kaynak kanalların kurallarına uy; yüksek hacimli otomatik iletim Telegram rate-limit'ine
  takılabilir.
