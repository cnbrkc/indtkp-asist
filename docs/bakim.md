# Bakım: yeni sürümü devreye alma, GH_PAT ve doğrulama

### 1. PR'ı `main`'e merge et

Actions workflow'ları **varsayılan daldan** (`main`) çalışır; güncel PR `main`'e merge
edilmeden yeni kod devreye girmez. Merge sonrasında:

**Actions → Telegram indirim takipçisi → Run workflow → `main`** ile yeni kodu bir kez elle
başlat. (Bunu yapmazsan mevcut job eski kodla 6 saate kadar devam eder.)

### 2. `GH_PAT` — sadece süresi dolduysa yenile

- **Otomatik yenileme zinciri** için `GH_PAT`'a *Actions: Read and write* izni yeterli;
  mevcut token'ın çalışması için dokunmana gerek yok.
- **Ayarların depoya yazılması** GitHub Actions'ta **ekstra bir şey gerektirmez**: workflow
  artık `GITHUB_TOKEN` kullanıyor ve job'a `contents: write` izni verilmiş durumda. Yani
  `GH_PAT`'ı sırf bu özellik için yenilemene gerek yok.
- Token'ın süresi zaten dolmuşsa yenilerken izin listesine dikkat et:
  - Actions zinciri → **Actions: Read and write**
  - VM'de çalıştırıyorsan depoya yazmak için → **Contents: Read and write**

### 3. Doğrula

Kontrol sohbetinde yeni akışı doğrula:

```text
/ayar       → beş liste ve filtre kısayolları gösterilmeli
/tgfiltrekapat      → menü sormalı: 1 dahili · 2 harici · 3 ikisi
1           → dahili filtre kapanmalı, harici engel sürmeli
/tgfiltreac dahili→ dahili filtre geri açılmalı (anında kaydeder)
/tgfiltrekapat ikisi→ iki filtre de kapanmalı; her mesaj iletilmeli
/tgfiltreac ikisi → ikisi birden açılmalı
/ekle       → beş liste seçimi, ardından mevcut liste
kahve, şeker, süt → taslakta üçü görünmeli; henüz kaydedilmemeli
/kaydet     → config.json yazılmalı ve GitHub sonucu görünmeli
/cikar      → liste seçimi ve mevcut kayıtlar gösterilmeli
1, 3        → iki kayıt birden çıkarılmalı
/iptal      → bekleyen taslak silinmeli; ayar değişmemeli
/analiz     → "🔎 Analiz başladı" mesajı gelmeli ve kaynak kaynak güncellenmeli
            → iki istatistik (25 kelime + 25 ilk kelime) gelmeli
            → beğenmediğin kelimeyi /cikar → 2 ile harici listeye ekle
/analiz 1000 tcl 65 → her satır `ürün adı (tam) - fiyat` olmalı; model kodu
            görünmeli ve eski/taksitli fiyat değil indirimli fiyat yazılmalı
```

Yanıtta **`✅ repo'ya işlendi`** yazıyorsa kalıcılık çalışıyor demektir: repo'da
`config.json` güncellenmiş olur ve Actions yeniden başlasa da ayarın kalır.

`⚠️ ... yalnızca bu oturumda geçerli` uyarısı alırsan önce **PR'ın merge edildiğinden ve
yeni bir run başlattığından** emin ol (eski kodda bu özellik yoktur); hâlâ uyarı varsa
[11. bölümdeki](sorun-giderme-ve-guvenlik.md#11-sorun-giderme) ilgili satıra bak.

## Runner beklenmedik şekilde ölürse ("The hosted runner lost communication with the server")

Bu, Python hatası değil, GitHub'ın çalıştırıcı makinesinin (VM) bağlantıyı kaybetmesidir. İş kırmızı
biter ve bot durur; 330 dakikalık otomatik yenileme zinciri kopar (zinciri çalışan süreç kurar).

- **Eski log silinmez.** Çalıştırmayı yeniden başlatınca (Re-run) önceki deneme "Attempt #1" olarak
  saklanır: çalıştırma sayfasının sağ üstündeki **Attempt** açılır listesinden seçilir. Loglar ~90 gün durur.
- **Nedene bakmak için:** Attempt #1 → `monitor` → "Mesajları dinle" adımının son satırlarına bak. Python
  hatası/Traceback yoksa ve log aniden kesildiyse VM düşmüştür (bot kaynaklı değildir).
- **Otomatik toparlanma:** `telegram-monitor.yml` içindeki cron her 30 dakikada tetiklenir ve çalışanı
  kesmeden **sırada bekler**; çalışan süreç ölürse bekleyen yedek hemen başlar. Yine de bir iş kırmızıysa
  ve yenisi başlamadıysa Actions → Run workflow ile elle başlat.

