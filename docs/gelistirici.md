# Geliştirici notları

```bash
python -m unittest discover -s tests -v   # birim ve uçtan uca testler
python bot.py --check                     # secret + config doğrulaması
```

`push` ve `pull_request` olaylarında **Testler** workflow'u aynı iki komutu GitHub'da da
çalıştırır. Ek bağımlılık yok, sadece `telethon` gerekir.

Kod yapısı ve modül haritası için [mimari.md](mimari.md). Diğer dosyalar: `config.json` (ayarlar),
`generate_session.py` ve `session_generator_colab.ipynb` (session üretimi),
`deploy/telegram-filter.service` (systemd örneği), `tests/` (birim ve uçtan uca testler).
