# PR #58 Sonrası Canlı FloodWait — Kök Neden Analizi (Read-Only)

**Tarih:** 2026-11-11 22:19 canlı log — `149.154.167.92`  
**Branch:** `arena/dca938c3-indtkp-asist` @ `28e824e` (PR #58 merge sonrası `main`)  
**Config:** 47 `source_chats` (46 `@kullanıcıadı` + 1 invite link) — `config.json:2-44`  
**Log kanıtı:**
```
ERROR Kaynak çözülemedi: '@indirimfirsat3' -> FloodWaitError: A wait of 41509 seconds
... 37 kez aynı 41509s
INFO Kaynak hazır: 10 adet (FırsatZ, TAZEFIRSATmini, onual_ekstra …)
WARNING Kaynak atlandı: '@indirimfirsat3' (...) — 37 kaynak
INFO Dinleniyor... (kaynak=10, kontrol=[-5569562901,1143378073])
```

---

## 1) Yönetici Özeti
Canlıda 47 kaynaktan 37’si `ResolveUsernameRequest` kaynaklı `FloodWait(41509s ~11.5sa)` ile düşüyor. Bot `source_chats`’i **her açılışta** 47 kez `get_entity(value)` ile sırasıyla çözmeye çalışıyor (`bot.py:5044-5046`, `resolve_chat:670`). İlk ~10 resolve sonrası Telegram `ResolveUsernameRequest` için global flood koyuyor; sonraki tüm username resolve’ları aynı `41509s` ile fail ediyor. Sonuç: kaynakların 2/3’ü dinlenmiyor, sadece 3 bildirim gitmiş.

Kök neden **sayısal chat ID’lerinin kalıcı saklanmaması**. Bir kez çözülmüş `peer_id` (`-1001…`/`-155…`) bir sonraki açılışta tekrar username üzerinden çözülüyor. Numeric ID ile `get_entity(int)` invite/username request’ini tetiklemez; StringSession entity cache de temizlendiği için (`bot.py:670-676` — int için `get_dialogs` fallback var ama username için yok). PR #58 cursor (`source_cursors`) için kalıcılık getirdi ama **kaynak ID’leri için ayrı kalıcılık yok**.

## 2) Depo Haritası (ilgili dosyalar)

| Dosya | Satır | Rol |
|---|---|---|
| `bot.py:5039-5073` | `resolve_sources` | Kaynakları tek tek `resolve_chat` → `SOURCES/SOURCE_IDS` doldurur |
| `bot.py:660-678` | `resolve_chat` | `CheckChatInvite` / `get_entity(value)` |
| `bot.py:5125-5149` | `resolve_chat_groups` | `sources/destination/control` grupları |
| `bot.py:2318-2340` | `cursor_load/collect` | `source_cursors` kalıcılığı (var) |
| `bot.py:4729-4950` | `ConfigStore/atomic_write/commit_and_push` | `config.json` kalıcı yazım yolu |
| `bot.py:5700-5730` | `main` açılış | `resolve_chat_groups({sources,…})` çağrısı |
| `config.json` | `source_chats` + `source_cursors` yok | Şu an sadece username listesi |
| `tests/test_integration.py:135-340` | `FakeClient` | `get_entity`→Channel; `resolve_chat` test harness |

## 3) Mevcut Resolve Akışı (sıra ve yük)
`main():5727` → `resolve_chat_groups({sources})` → `resolve_sources` döngüsü:
```py
for value in chat_values(config.get("source_chats")):  # 47
    peer_id, name, entity = await resolve_chat(client, value) # get_entity(value)
```
`resolve_chat` string için doğrudan `await client.get_entity(value)` → `ResolveUsernameRequest`. Invite link için `CheckChatInviteRequest`. Numeric int için istisna yolunda `get_dialogs()` + retry (değerli).

Döngü **ardı ardına** 47 istek; aralık yok, batch yok, FloodWait yakalayıp kırma yok. `FloodWaitError` yakalanıp `failures` listesine ekleniyor ve `continue` (`bot.py:5047-5050`) — ama aynı oturumda flood sürerken kalan 36 isteğe de **aynı 41509s** ile devam ediyor, bekleme süresini uzatıyor.

## 4) Kalıcılık — Kaynak ID’leri
`source_cursors` (cursor recovery) kalıcı: `bot.py:2340` `cursor_collect_for_persist()` → `atomic_write_json` + `commit_and_push` (sadece manuel `/restart`). Sonraki açılış `cursor_load_from_config:2322` ile RAM’e yükleniyor.

Kaynak ID’leri için **eşdeğer kalıcılık yok**:
- `config.json` canlıda `source_chat_ids`/`peer_ids` içermiyor; sadece `source_chats` kullanıcı adları.
- `SOURCE_IDS` RAM set (`bot.py:90`) — her process restart (Actions 330dk `auto_restart` + `StringSession` yenileme) ile sıfırlanıyor.
- Telethon `StringSession` entity cache kalıcı değil (`bot.py:671-676` yorum: “StringSession does not persist … cache”).
- Sonuç: her 5.5 saatte bir 47 resolve tekrarlanıyor → flood kaçınılmaz.

## 5) FloodWait Yönetimi (mevcut)
- `resolve_sources` FloodWait’i **failure** olarak loglar (`ERROR` + `WARNING`) ve devam eder; `FloodWaitError.seconds` **dikkate alınmaz**.
- Bir kaynakta `41509s` gelince kalan 36 kaynak da **peş peşe aynı hatayı** üretir (log kanıtı: 37× aynı `41509`).
- Aynı oturumda tekrar `get_entity("@...")` denemesi flood süresini **sıfırlamaz ama uzatabilir**; Telegram per-user limit.
- Bilinen ID’ler de flood nedeniyle **atlanıyor** (`bot.py:5044-5050` — tüm 37 atlandı, 10 iyi olan da şans eseri ilk sırada olanlar).
- 41.5sn bloklayıcı bekleme yok; ama **tüm resolve’ları denemeye devam etmek** 37 gereksiz istek + log gürültüsü + kalan iyi kaynakları da riske atıyor. Doğru: flood’da username resolve’u durdur, bilinen ID’leri kullan, bilinmeyenleri beklemeye al.

## 6) Kaynakların Eksiksiz Başlatılması (mevcut raporlama)
Başlangıç logu eksik:
```
INFO Kaynak hazır: FırsatZ … (10)
WARNING Kaynak atlandı: '@indirimfirsat3' (FloodWait…) (37)
INFO 🎯 Hedef: IndTkp Bildirimler
INFO Dinleniyor... (kaynak=10, ...)
```
- Toplam `47` vs `10` oranı loglanmıyor; `resolve_sources:5071` sadece “⚠️ N kaynak çözülemedi: a,b,c” (en fazla 5) ekliyor.
- Bot `kaynak=10` ile **normal** dinliyor gibi gösteriyor; kısmi başlatma açıkça raporlanmıyor.
- FloodWait bekleyen kaynaklar için **yeniden deneme** yok; bir sonraki açılışa kadar (5.5h sonra yine 47 resolve) kayıp.

## 7) Entity Önbelleği ve Config Yapısı
- Telethon `client.get_entity(value)` önce `_mb_entity_cache` (memory) bakar; yoksa API çağrısı. `StringSession` sonrası cache boş → ilk açılışta cache miss.
- Int fallback (`bot.py:672-676`) `get_dialogs()` ile tüm diyalogları çeker (1 istek) sonra int id bulunur — bu **ucuz** (1 call) ve username flood’unu tetiklemez; ama int’ler hiç saklanmadığı için bu yoldan faydalanılmıyor.
- Mevcut `config.json` yapısı: `source_chats` (insan okunur) + `source_cursors` (cursor). İkinci bir kayıt mekanizması **gereksiz**; aynı dosyada `source_chat_ids: { "@kullaniciadi": -100… }` veya `resolved_sources` eşleşmesi eklenebilir; mevcut `atomic_write` + `commit_and_push` yolu yeniden kullanılır.

## 8) Cursor/Restart Etkileşimi
- `auto_restart` (`bot.py:5727-`, `auto_restart_scheduler:5606`) 330dk sonra `dispatch_next_run` ile yeni Actions çalışması başlatır; `source_cursors` yalnızca **manuel `/restart`** (`COMMAND_RESTART:773…`) sırasında `cursor_persist_for_restart:2337` → `save_config` ile pushlanır; auto restart cursor kaydetmez — bu doğru (görev tanımı).
- Kaynak ID kalıcılığı da **aynı noktada** yapılmalı: başarılı `resolve_chat` sonrası `config["source_chat_ids"][username]=peer_id` + `atomic_write` + (tercihen) `commit_and_push`? Ama `main()` bölünmemeli, mesaj başı/periyodik push yasak (sınırlar). En küçük güvenli yer: `resolve_sources` başarılı döngü sonrası **bir kez** `source_chat_ids` doldurup sessizce `atomic_write_json` (push’suz, local kalıcı; sonraki `git` push manuel `/restart` veya bir sonraki başarılı resolve’da birikecek). Veya `resolve_sources` içinde FloodWait yoksa hemen `*_persist`? Ayrıntı tasarım aşamasında netleşmeli — ikinci kayıt mekanizması oluşturulmamalı.

## 9) Test Kapsamı ve Riskler
Mevcut testler (`test_integration.py:FakeClient.get_entity` → Channel) tek kaynak üzerinden `resolve_chat_groups` akışını sınar; FloodWait, 47 kaynak, ID kalıcılığı, int-username eşleşmesi, bozuk ID, invite link senaryoları **yok**. Golden fixture (`corpus.json`/`expected.json`) mesaj filtreleme için, resolve için değil.

**Riskler:**
- Username değişirse (`@firsatz` → `@yeniad`) eski ID artık geçersiz; int ile `get_entity` `ValueError` verir — `get_dialogs` fallback veya username fallback gerekir.
- Kullanıcı kanalından ayrılırsa `entity.left=True` (`bot.py:5056`) — `not_joined` uyarısı verilir ama ID saklanmaya devam etmemeli.
- Numeric ID atlandıktan sonra tekrar eklenen yeni kaynak: ilk kurulumda ID bilinmiyor → bir kez username resolve gerekir; FloodWait’te beklemeye alınmalı, diğer 46 ID ile devam etmeli.
- Invite link (`https://t.me/+...`) sayısal ID’ye dönüşür; saklanabilir.
- 47 kaynaklı `get_entity` yerine `GetPeerDialogs` batch (fast_poll gibi) kullanılabilir ama `main()` bölünmeden eklenmeli.

**Öneri (kod aşaması için):**
- `config.json`’a `source_chat_ids: dict[str,int]` (veya `resolved_sources: [{requested,id,name}]`) ekle; `resolve_sources` önce `int` denesin, sonra username; başarılı olunca dict güncellenip `atomic_write_json` ile saklansın.
- `FloodWaitError` yakalayınca `41509s` logla, kalan **username** çözümlemelerini durdur (`break` veya `pending` listesi), bilinen ID’lerden devam et; `pending`’ler bir sonraki FloodWait süresi sonunda veya bir sonraki `main` açılışında (ID cache hazır) denenecek.
- Başlangıç raporu `len(SOURCES)==len(source_chats)` değilse `⚠️ KISMİ BAŞLATMA: 10/47, 37 beklemede (FloodWait 41509s)` şeklinde logla; `SOURCE_FAILURES` ayrı gösterilmeli.
- Yeni kaynak/username değişimi/bozuk ID için int→entity başarısız olursa `get_entity(original_username)` fallback’i dene ve dict’i güncelle.
- Test: 7 senaryo (47 ID’li restart 0 ResolveUsername, ilk kurulum batch, 41509 FloodWait, restart ID korunması, yeni kaynak ekleme, kullanıcı adı değişimi, bozuk ID) — mevcut CI içinde `test_integration` altına ekle; `product_tracker`/`akakce_worker` dokunma.

**Sınırlar uyumu:** `main()` bölme yok, filtreleme/fiyat/dedup değişmiyor, cursor sistemi korunuyor, per-message push yok, yeni dosya/servis yok, mevcut `ConfigStore`/`atomic_write` yolu kullanılacak.

