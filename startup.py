"""main() için açılış adımları: etkin özellik günlükleri, kanal çözümleme, ürün takibi ve önbellek hazırlığı.

Sıra ve davranış eskiden ``bot.main()`` içindeydi; burada yalnızca okunabilirlik için ayrıldı.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import urllib.parse

import appstate
import chatresolve
import gitops
import pollcursor
from confutil import chat_values, config_flag
from constants import MESSAGE_LINK_LABEL, PRODUCT_LINK_TEXT
from dedupflow import dedup_preload
from header import build_header_line, header_price_text
from runtime import Runtime
from textutil import humanize

log = logging.getLogger("telegram-filter")


def log_active_features() -> None:
    """Etkin bağlantı eki, bildirim, komut temizliği ve tekrar birleştirme ayarlarını günlüğe yazar."""
    if appstate.LINK_APPENDIX_MODE == "smart":
        log.info("Bağlantı ekleri: akıllı mod — ürün adresi '%s' yazısına gömülür "
                 "(buton, gizli hyperlink ya da görünür URL); alıntıdaki bağlantılar yedek kalır.",
                 PRODUCT_LINK_TEXT)
    elif appstate.LINK_APPENDIX_MODE == "all":
        log.info("Bağlantı ekleri: tüm gizli linkler iletinin sonuna yazılacak.")
    if appstate.MESSAGE_LINK_LINE:
        log.info("Orijinal mesajın t.me adresi kaynak grup adının içine gömülecek "
                 "(ayrı '🔗 %s' satırı yok).", MESSAGE_LINK_LABEL)
    if appstate.NOTIFY_BOT_TOKEN:
        log.info("Bildirim biçimi (basit düzen): en üstte ürün başlığı → '%s' → altında mesaj "
                 "OLDUĞU GİBİ → en altta kalın kaynak adı%s; mesajın adresi o adın içine "
                 "gömülüdür. Tekrarlı paylaşımlarda kaynak adı satırı kalkar, yerine çoklu "
                 "paylaşım notu yazılır (📌 N kere paylaşıldı: <gruplar>; her ad kendi "
                 "mesajına bağlı).",
                 PRODUCT_LINK_TEXT, " + medya" if appstate.NOTIFY_MEDIA else "")
    if appstate.CLEAN_COMMANDS:
        log.info("Komut temizliği açık: yeni komutta önceki komut/yanıt silinir, "
                 "bildirimlere dokunulmaz.")
    if appstate.DEDUP_ENABLED:
        log.info("Tekrar birleştirme açık: aynı ürün sorgusu %s boyunca tek mesajda toplanır, "
                 "açılışta hedefteki son %d ileti taranır.",
                 humanize(appstate.DEDUP_WINDOW_SECONDS), appstate.DEDUP_SCAN_LIMIT)
    else:
        log.info("Tekrar birleştirme kapalı: her eşleşme ayrı mesaj olarak iletilir.")


async def resolve_chats(rt: Runtime, config: dict) -> None:
    """Kaynak, kontrol ve hedef sohbetleri çözer; kaynak ID'lerini kalıcılaştırır ve eksikleri uyarır.

    Kaynakların hiçbiri çözülemezse (fatal) süreci durdurur.
    """
    _orig_ids = dict(rt.store.config.get("source_chat_ids") or {}) if isinstance(rt.store.config.get("source_chat_ids"), dict) else {}
    notes, fatal = await chatresolve.resolve_chat_groups(
        rt.client, config, {"sources", "destination", "control"},
    )
    for note in notes:
        log.info("%s", note)
    if fatal:
        raise SystemExit(f"{fatal}; source_chats listesini ve hesap üyeliğini kontrol et.")
    # Kaynak ID'lerini kalıcılaştır — sonraki açılışlarda username FloodWait'ini önler
    _new_ids = rt.store.config.get("source_chat_ids")
    if isinstance(_new_ids, dict) and _new_ids != _orig_ids:
        try:
            await asyncio.to_thread(gitops.atomic_write_json, rt.store.path, rt.store.config)
            log.info("Kaynak ID'leri kalıcılaştırıldı: %s/%s", len(_new_ids), len(chat_values(rt.store.config.get("source_chats"))))
            # GitHub kalıcılığı: bir sonraki runner aynı ID'leri görsün; sessiz, hatası startup'ı öldürmez
            # Yalnızca gerçek repo checkout'unda push et — testteki /tmp dosyası repo dışı
            if gitops.push_token() and gitops.git_repo_root(rt.store.path.parent) is not None:
                _st, _dt = await asyncio.to_thread(gitops.commit_and_push, rt.store.path, "kaynak ID'leri güncellendi")
                log.info("Kaynak ID push: %s %s", _st, _dt)
        except Exception as exc:  # noqa: BLE001 - kalıcılık hatası bot'u durdurmasın
            log.warning("Kaynak ID'leri yazılamadı: %s: %s", type(exc).__name__, exc)
    not_joined = [item["name"] for item in appstate.SOURCES if not item["joined"]]
    if not_joined:
        log.warning(
            "Bu hesaptan üye olunmayan kaynaklar var, bunlardan mesaj GELMEZ: %s", ", ".join(not_joined)
        )
    if appstate.ADMIN_IDS:
        log.info("Komut kullanabilecek admin ID'leri: %s", sorted(appstate.ADMIN_IDS))
    elif len(appstate.CONTROL_IDS) > 1:
        log.warning("admin_user_id boş: grup komutları kimse tarafından kullanılamaz.")
    if not appstate.NOTIFY_BOT_TOKEN:
        log.warning(
            "NOTIFY_BOT_TOKEN secret/ortam değişkeni tanımlı değil: mesajları kendi hesabın "
            "gönderdiği için Telegram BİLDİRİM ÜRETMEZ. Sesli bildirim istiyorsan @BotFather'dan "
            "bir bot oluşturup gruba ekle ve token'ı NOTIFY_BOT_TOKEN secret'ında sakla."
        )


def start_product_tracker(rt: Runtime) -> None:
    """Özel kontrol açıksa Akakçe ürün izleyicisini kurar (`rt.tracker`); başarısızlık dinlemeyi durdurmaz."""
    # Optional feature: failure to initialize must not prevent channel listening.
    rt.tracker = None
    if rt.private_api and config_flag(rt.store.config.get("private_control"), False):
        try:
            from product_tracker import ProductTracker

            def persist_products(path, data):
                # Contents API avoids dirtying the shared checkout while config
                # commits/rebases use it. The next runner checks out this snapshot.
                with gitops.GIT_WRITE_LOCK:
                    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
                    token = gitops.push_token()
                    repo = gitops.git_repo_root(rt.store.path.parent)
                    branch = (os.getenv("CONFIG_PUSH_BRANCH", "").strip()
                              or (gitops.current_branch(repo) if repo else "")
                              or os.getenv("GITHUB_REF_NAME", "").strip())
                    if not repository or not token or not branch:
                        return False, "GitHub kalıcı kayıt ortamı eksik."
                    url = f"https://api.github.com/repos/{repository}/contents/{path.name}"
                    ok, remote, detail = gitops._github_api("GET", f"{url}?ref={urllib.parse.quote(branch, safe='')}", token)
                    if not ok or not isinstance(remote, dict) or not remote.get("sha"):
                        log.warning("Ürün takip dosyasının GitHub sürümü okunamadı: %s",
                                    detail or "beklenmeyen yanıt")
                        return False, "Takip dosyasının GitHub sürümü okunamadı."
                    payload = {
                        "message": "config: ürün takip güncellemesi",
                        "branch": branch, "sha": remote["sha"],
                        "content": base64.b64encode((json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()).decode(),
                    }
                    ok, _, detail = gitops._github_api("PUT", url, token, payload)
                    if not ok:
                        log.warning("Ürün takip ayarı GitHub'a yazılamadı: %s", detail)
                    return ok, "Ürün takip ayarı GitHub'a yazıldı." if ok else "GitHub yazımı başarısız."

            rt.tracker = ProductTracker(
                rt.store.path.with_name("product_watches.json"), rt.private_api, persist_products,
                owner_id=appstate.SELF_ID,
                header_formatter=lambda cents, title: build_header_line(header_price_text(cents / 100), title),
            )
        except Exception:
            log.exception("Ürün takibi başlatılamadı; kanal dinleme devam ediyor.")


async def prepare_caches(rt: Runtime) -> None:
    """Açılışta tekrar/canlı önbellekleri sıfırlar, hedefin son iletileriyle ısıtır ve imleci yükler."""
    # Tekrar önbelleğini sıfırla ve hedefin son iletileriyle ısıt: yeniden
    # başlamalarda yalnızca güvenle eşlenebilen geçmişi kullan (tek tarama).
    appstate.DEDUP_CACHE.clear()
    appstate.LIVE_SEEN.clear()
    appstate.POLL_DISPATCHED.clear()
    appstate.SOURCE_IN_FLIGHT.clear()
    await dedup_preload(rt)
    # Cursor: RAM sadece, bilinçli /restart’ta tek persist
    pollcursor.cursor_load_from_config(rt.store.config)
    # İlk kurulum: source_cursors yoksa bootstrap ilk poll’de top ile yapılır (patlama yok)
    if not rt.store.config.get("source_cursors"):
        log.info("Cursor: kalıcı kayıt yok — ilk poll’de sınırlı geriye tarama yapılacak; başlangıçtan önceki geçmiş bastırılacak.")
