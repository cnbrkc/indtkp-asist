# Telegram indirim takipçisi

Kişisel Telegram hesabının üye olduğu indirim kanallarını dinler, seçtiğin kelimelere uyan gönderileri ayrı bir sohbete yollar. **Bot hesabı değil, Telethon ile kişisel hesap oturumu kullanır**; bu yüzden kaynak kanallara hesabınla üye olman gerekir.

## Önce güvenlik

Eski `bot.py` içinde API ID/API Hash açıkta kalmıştı. Bu değerler daha önce GitHub'a gönderildiyse artık gizli kabul edilmez: [my.telegram.org](https://my.telegram.org) üzerinden API uygulamasını yenile veya hash'i değiştir. Git geçmişinde görünen sırları temizlemek tek başına yeterli değildir.

`SESSION_STRING` ve API bilgilerini asla kod içine, issue'ya veya herkese açık log'a yazma. String session, hesabına giriş yapmaya yarayan parolaya eşdeğerdir.

## GitHub Actions ile ücretsiz kurulum

Bu yöntem bilgisayarın sürekli açık kalmasını gerektirmez. GitHub'ın public repository runner'ı yaklaşık 6 saatlik oturumlar halinde çalışır. Her oturumun arasında birkaç dakikalık boşluk olabilir; bu nedenle **gerçek zamanlı ve kesintisiz 7/24 garanti değildir**. GitHub cron gecikebilir, Actions da uzun süre hiç commit/activity olmayan depolarda zamanlamayı durdurabilir.

1. Telegram'da hedef bir sohbet oluştur veya gönderileri **Kayıtlı Mesajlar**'a göndermek istiyorsan hedefi `me` bırak.
2. Kaynak kanal kullanıcı adlarını (`@kanaladi`) veya chat ID'lerini ekle. Özel kanallar için ID genellikle `-100...` biçimindedir. Hesabın bu kanallara katılmış olmalı.
3. [my.telegram.org](https://my.telegram.org) üzerinden API ID ve API Hash al.
4. Bir bilgisayar/telefon üzerindeki Python ortamında bir kez oturum string'i üret:

   ```bash
   pip install -r requirements.txt
   API_ID=... API_HASH=... python generate_session.py
   ```

   Telefonuna gelen Telegram kodunu ve varsa iki aşamalı doğrulama parolasını gir. Çıktıdaki `SESSION_STRING`'ı sakla. Bu adım yalnızca ilk kurulumdur; sonra bilgisayar gerekmez.

5. GitHub'da **Settings → Secrets and variables → Actions** ekranında:
   - **Secrets**: `API_ID`, `API_HASH`, `SESSION_STRING`
   - **Variables**: `SOURCE_CHATS`, `DESTINATION`, `INCLUDE_KEYWORDS`, `EXCLUDE_KEYWORDS`, `MATCH_MODE`, `COPY_MODE`

   Örnek değerler:

   | Değişken | Örnek | Açıklama |
   |---|---|---|
   | `SOURCE_CHATS` | `@kanal1,-1001234567890` | Virgülle ayrılmış kaynaklar |
   | `DESTINATION` | `me` veya `-1009876543210` | Filtrelenen mesajların hedefi |
   | `INCLUDE_KEYWORDS` | `5070ti,ekran kartı,laptop` | Varsayılan: bunlardan biri eşleşsin |
   | `EXCLUDE_KEYWORDS` | `çekiliş,sponsorlu` | Bunlardan biri varsa gönderme |
   | `MATCH_MODE` | `any` | `any` veya bütün kelimeler için `all` |
   | `COPY_MODE` | `forward` | `copy` kaynak etiketini kaldırır |

6. **Actions → Telegram indirim takipçisi → Run workflow** ile elle başlatıp log'u kontrol et. Sonrasında workflow yaklaşık her 6 saatte bir yeniden başlar. `workflow_dispatch` elle yeniden başlatmak içindir.

> Actions log'unda mesaj içeriği yazdırılmıyor. Yine de repo erişimini sınırlı tut ve `SESSION_STRING`'ı kimseye gönderme.

## Yerelde test

```bash
cp .env.example .env
# .env değerlerini doldur
set -a; . ./.env; set +a
python bot.py
```

İlk yerel çalıştırmada `SESSION_STRING` boşsa Telethon interaktif giriş ister. Actions'ta interaktif terminal olmadığı için oraya mutlaka üretilmiş StringSession koyulmalıdır.

## Çalışma mantığı

- Yalnızca `SOURCE_CHATS` içindeki yeni mesajlar izlenir; tüm hesabı dinleyip yanlışlıkla spam üretmez.
- `EXCLUDE_KEYWORDS` önce uygulanır.
- `INCLUDE_KEYWORDS` boşsa kaynak mesajlarının hepsi, doluysa `MATCH_MODE` kuralına uyanlar gönderilir.
- `forward`, Telegram'ın orijinal iletisini ileri gönderir; `copy`, kaynak etiketi olmadan kopyalar.
- Workflow'daki `concurrency` aynı anda iki runner'ın çalışıp çift mesaj göndermesini engeller.

## Ücretsiz seçeneklerin sınırı

GitHub Actions sürekli servis (daemon) değildir; ücretsiz ve bilgisayarsız başlangıç için en pratik çözümdür ancak kesintisiz çalışma gerekiyorsa kalıcı diskli bir ücretsiz/ucuz VM gerekir. `tgcf` ve `tg-focus` gibi açık kaynak projeler de benzer filtreleme yaklaşımı kullanıyor; bu repo küçük ve yalnızca indirim filtresi için tutuldu.

### Lisans ve kullanım notu

Telegram kanal kurallarına, telif haklarına ve GitHub Actions kullanım koşullarına uy. Çok yüksek hacimli otomatik forward işlemleri hesabına rate-limit veya kısıtlama getirebilir.
