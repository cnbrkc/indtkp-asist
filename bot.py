"""Telegram indirim mesajı filtresi.

Kişisel Telegram hesabı (user client) ile çalışır; bot hesabı kaynak kanalları
okuyamaz. Yapılandırma config.json dosyasından gelir, sırlar ortam
değişkenlerinden gelir.

Önemli tasarım notu: Telethon'daki `events.NewMessage(chats=...)` filtresi chat
listesini *ilk mesaj geldiğinde* çözer ve tek bir chat bile çözülemezse tüm
handler devre dışı kalır (üstelik hata yalnızca "Task exception was never
retrieved" olarak loglanır). Bu yüzden burada bütün chat'ler açılışta tek tek
çözülür, çözülemeyenler atlanır ve handler'lara hazır int ID listesi verilir.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import functools
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence

from telethon import events, TelegramClient
from telethon.sessions import StringSession

import appstate
import chatresolve
import controlcmds
import gitops
import lifecycle
import listener
import pollcursor
import preflight
import runtimecfg
import timefmt
from commandtext import ConfigStore
from confutil import chat_values, config_flag, config_path, load_config
from constants import MESSAGE_LINK_LABEL, PRODUCT_LINK_TEXT
from dedupflow import dedup_preload
from delivery import send_copy, send_forward, send_link_card, send_media, send_text_only
from dmcontrol import prepare_private_offer
from header import build_header_line, header_price_text
from lifecycle import (
    prepare_scheduled_restart,
    private_status_changed,
    resume_after_scheduled_restart_failure,
)
from listener import fast_poll_loop
from private_bot import BotAPI, poll_health, poll_private_commands, PrivateOfferQueue
from runtime import Runtime
from textutil import display_name, humanize

# Runner UTC çalışır; log saatlerini TSİ'ye sabitle ki kullanıcı saatle
# karşılaştırabilsin (Actions log'unda "10:44" görünen satırlar TSİ 13:44'tü).
# Sınıf özniteliği olarak atandığı için staticmethod sarmalayıcı gerekir;
# aksi hâlde Python bunu bağlı metot sanıp iki argüman geçirir.
logging.Formatter.converter = staticmethod(
    lambda seconds: timefmt.local(seconds).timetuple()
)
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("telegram-filter")


# ---------------------------------------------------------------------------
# Giriş noktası: argüman ayrıştırma, --check ve ana akış (main)
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Telegram indirim mesajı filtresi")
    parser.add_argument(
        "--check", action="store_true",
        help="Telegram'a bağlanmadan secret/config kontrolü yap ve raporu yazdır",
    )
    parser.add_argument("--config", default=None, help="config.json yolu (varsayılan: $CONFIG_FILE veya config.json)")
    return parser


def run_check(config_path: str | None) -> int:
    try:
        config = load_config(config_path)
    except FileNotFoundError:
        print(f"✗ config.json bulunamadı (bakılan yol: {config_path or os.getenv('CONFIG_FILE', 'config.json')})", flush=True)
        return 1
    except json.JSONDecodeError as exc:
        print(f"✗ config.json geçerli bir JSON değil: {exc}", flush=True)
        return 1

    problems = preflight.check_environment(config)
    preflight.print_report(config, problems)
    return 1 if problems else 0


async def main(argv: Sequence[str] | None = None) -> int:
    # Filtre/link bayraklarını apply_runtime_config yazar; burada yalnızca
    # kimlik atanır (diğer adlar yalnızca okunur, `global` gerekmez).
    rt = Runtime()

    args = build_parser().parse_args(argv)
    if args.check:
        return run_check(args.config)

    config = load_config(args.config)
    problems = preflight.check_environment(config)
    preflight.print_report(config, problems)
    if problems:
        for problem in problems:
            log.error("Yapılandırma sorunu: %s", problem)
        raise SystemExit(1)

    # Config nesnesi çalışan filtre ve geçici liste taslakları için kullanılır.
    rt.store = ConfigStore(config_path(args.config), config)

    rt.gh_pat = os.getenv("GH_PAT", "").strip()
    auto_restart = bool(config.get("auto_restart", True))
    restart_minutes = max(1, int(os.getenv("RESTART_AFTER_MINUTES", "330")))

    # Telegram bağlantısı gerektirmeyen ayarlar (filtre, zincir, bayraklar).
    for note in runtimecfg.apply_runtime_config(config):
        log.info("%s", note)

    rt.client = TelegramClient(
        StringSession(os.environ["SESSION_STRING"].strip()),
        int(os.environ["API_ID"].strip()),
        os.environ["API_HASH"].strip(),
    )

    await rt.client.connect()
    if not await rt.client.is_user_authorized():
        raise SystemExit(
            "SESSION_STRING geçersiz veya oturumun süresi dolmuş. "
            "generate_session.py ile yeni bir session üretip GitHub secret'ını güncelle."
        )

    me = await rt.client.get_me()
    appstate.SELF_ID = me.id
    log.info("Bağlanıldı: %s (id=%s)", display_name(me), me.id)

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

    # --- Kaynakları, kontrol sohbetini ve hedefi çöz.
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


    rt.settings_lock = asyncio.Lock()
    rt.private_api = BotAPI(appstate.NOTIFY_BOT_TOKEN) if appstate.NOTIFY_BOT_TOKEN else None
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


    rt.private_offers = PrivateOfferQueue(rt.private_api, functools.partial(prepare_private_offer, rt))


    # --- İletim yolları -----------------------------------------------------
    # Korumalı (noforwards) kanallarda forward ve copy patlar; o yüzden sırayla
    # denenir: forward -> copy -> medyayı indirip yeniden yükle -> sadece metin -> link.


    rt.senders = {
        "forward": functools.partial(send_forward, rt),
        "copy": functools.partial(send_copy, rt),
        "media": functools.partial(send_media, rt),
        "text": functools.partial(send_text_only, rt),
        "link": functools.partial(send_link_card, rt),
    }

    rt.account_send_lock = asyncio.Lock()


    # --- Tekrar birleştirme --------------------------------------------------
    # Aynı başlık aynı anda 3-5 kanaldan gelebilir; gönderen rezervasyon koyar,
    # diğerleri bekleyip tekrara düşer. Rozet aynı ürünün kilidiyle birleştirilir;
    # kayıp hedefte bu olayın kendisi yeni bildirim gönderebilir.

                # State may have changed just as the timeout fired; re-read it.


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

    # Handler'lara `chats=` VERMİYORUZ: Telethon o filtreyi ilk mesajda çözer ve
    # çözümleme hatası tüm update akışını öldürür. Filtreyi burada kendimiz yapıyoruz.
    @rt.client.on(events.NewMessage())
    async def on_control_message(event: events.NewMessage.Event) -> None:
        return await controlcmds.on_control_message(rt, event)

    @rt.client.on(events.NewMessage())
    async def on_new_message(event: events.NewMessage.Event) -> None:
        return await listener.on_new_message(rt, event)


    rt.poll_delivery_tasks: set[asyncio.Task] = set()


    # Özel komutlar ve fırsat kuyruğu ayrı görevlerde çalışır; açılış mesajı gönderilmez.
    private_tasks = []
    if rt.tracker:
        private_tasks.append(asyncio.create_task(rt.tracker.run()))
    if rt.private_api:
        private_tasks.append(asyncio.create_task(rt.private_offers.run()))
        if config_flag(rt.store.config.get("private_control"), False):
            private_tasks.append(asyncio.create_task(poll_private_commands(
                rt.private_api, lambda: appstate.ADMIN_IDS | {appstate.SELF_ID}, on_control_message,
                functools.partial(private_status_changed, rt),
            )))
            await asyncio.sleep(0)  # poller görevi ilk adımını atsın
            for _ in range(40):  # poller başlangıcına en fazla ~10 sn tanı
                if poll_health()["status"] not in {"off", "starting"}:
                    break
                await asyncio.sleep(0.25)


    if auto_restart and rt.gh_pat:
        asyncio.create_task(lifecycle.auto_restart_scheduler(
            rt.client, appstate.DESTINATION, rt.gh_pat, max(60, restart_minutes * 60),
            prepare_restart=functools.partial(prepare_scheduled_restart, rt),
            resume_after_failure=functools.partial(resume_after_scheduled_restart_failure, rt),
        ))
    elif auto_restart:
        log.warning("auto_restart açık ama GH_PAT yok; oturum Actions süresi bitince kapanacak.")
    asyncio.create_task(lifecycle.heartbeat())

    if appstate.FAST_POLL:
        private_tasks.append(asyncio.create_task(fast_poll_loop(rt)))

    log.info("Dinleniyor... (kaynak=%d, kontrol=%s, hedef=%s)", len(appstate.SOURCE_IDS), sorted(appstate.CONTROL_IDS), appstate.DESTINATION_LABEL)
    try:
        await rt.client.run_until_disconnected()
    finally:
        shutdown_tasks = [*private_tasks, *rt.poll_delivery_tasks]
        for task in shutdown_tasks:
            task.cancel()
        await asyncio.gather(*shutdown_tasks, return_exceptions=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:  # noqa: BLE001 - Actions log'unda net bir iz bırak
        log.exception("Takipçi beklenmeyen bir hatayla durdu.")
        raise
