# Telegram indirim takipçisi

Kişisel Telegram hesabının üye olduğu kanalları dinler, filtreden geçen mesajları
istediğin sohbete iletir. Polling yapmaz: bağlantıyı açık tutar, mesaj gelir gelmez işler.
Dahili/harici kelime ve takip edilen grup listelerini Telegram'dan yönetebilirsin
([komutlar](docs/komutlar.md)); diğer teknik ayarlar `config.json` üzerinden yapılır.

## Hızlı başlangıç

1. [Kurulum](docs/kurulum.md): Telegram API bilgisi, session string, GitHub secret'ları, `config.json`.
2. Botun özel sohbetinde `/start`, ardından `/durum` yaz.
3. Listeleri `/ekle`, `/cikar`, `/kaydet` ile yönet ([komutlar](docs/komutlar.md)).

## Belgeler

| Konu | Dosya |
|---|---|
| Sıfırdan kurulum | [docs/kurulum.md](docs/kurulum.md) |
| Nasıl çalışır, `config.json` ayarları | [docs/calisma-ve-ayarlar.md](docs/calisma-ve-ayarlar.md) |
| Telegram komutları, liste düzenleme, `/analiz` | [docs/komutlar.md](docs/komutlar.md) |
| Özel sohbetten yönetim, kişisel fırsatlar (DM) | [docs/ozel-sohbet-ve-dm.md](docs/ozel-sohbet-ve-dm.md) |
| Bildirim düzeni, hızlı tarama, güvenilirlik | [docs/bildirimler.md](docs/bildirimler.md) |
| İletim zinciri, gizli bağlantılar, fiyat arama düğmeleri | [docs/iletim-ve-linkler.md](docs/iletim-ve-linkler.md) |
| Tekrar birleştirme | [docs/tekrar-birlestirme.md](docs/tekrar-birlestirme.md) |
| Akakçe ürün takibi | [docs/urun-takibi.md](docs/urun-takibi.md) |
| ID doğrulama, sorun giderme, güvenlik | [docs/sorun-giderme-ve-guvenlik.md](docs/sorun-giderme-ve-guvenlik.md) |
| Yeni sürümü devreye alma, `GH_PAT`, doğrulama listesi | [docs/bakim.md](docs/bakim.md) |
| Geliştirici notları, kod mimarisi | [docs/gelistirici.md](docs/gelistirici.md), [docs/mimari.md](docs/mimari.md) |
| Geçmiş analizler ve değişiklik geçmişi | [docs/archive/](docs/archive/README.md) |

## Geliştirme

```bash
python -m unittest discover -s tests -v   # birim ve uçtan uca testler
python bot.py --check                     # secret + config doğrulaması
```
