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
import functools
import json
import logging
import os
from collections.abc import Sequence

from telethon import events, TelegramClient
from telethon.sessions import StringSession

import appstate
import controlcmds
import lifecycle
import listener
import preflight
import runtimecfg
import startup
import timefmt
from commandtext import ConfigStore
from confutil import config_flag, config_path, load_config
from delivery import send_copy, send_forward, send_link_card, send_media, send_text_only
from dmcontrol import prepare_private_offer
from lifecycle import private_status_changed
from listener import fast_poll_loop
from private_bot import BotAPI, poll_health, poll_private_commands, PrivateOfferQueue
from runtime import Runtime
from textutil import display_name

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
    """Komut satırı bağımsız değişkenleri: --check ve --config."""
    parser = argparse.ArgumentParser(description="Telegram indirim mesajı filtresi")
    parser.add_argument(
        "--check", action="store_true",
        help="Telegram'a bağlanmadan secret/config kontrolü yap ve raporu yazdır",
    )
    parser.add_argument("--config", default=None, help="config.json yolu (varsayılan: $CONFIG_FILE veya config.json)")
    return parser


def run_check(config_path: str | None) -> int:
    """Telegram'a bağlanmadan secret/config doğrulaması yapar; sorun varsa 1, yoksa 0 döner."""
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
    """Botu kurar ve çalıştırır: ayarları yükle → Telegram'a bağlan → sohbetleri çöz → servisleri,
    teslim yollarını ve dinleyicileri kur → bağlantı kopana kadar dinle. Ayrıntılar ``startup`` ve akış modüllerinde."""
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

    startup.log_active_features()
    await startup.resolve_chats(rt, config)

    rt.settings_lock = asyncio.Lock()
    rt.private_api = BotAPI(appstate.NOTIFY_BOT_TOKEN) if appstate.NOTIFY_BOT_TOKEN else None
    startup.start_product_tracker(rt)
    rt.private_offers = PrivateOfferQueue(rt.private_api, functools.partial(prepare_private_offer, rt))

    # Teslim yolları: korumalı (noforwards) kanallarda forward ve copy patlar; zincir sırayla
    # denenir: forward -> copy -> medyayı indirip yeniden yükle -> sadece metin -> link.
    rt.senders = {
        "forward": functools.partial(send_forward, rt),
        "copy": functools.partial(send_copy, rt),
        "media": functools.partial(send_media, rt),
        "text": functools.partial(send_text_only, rt),
        "link": functools.partial(send_link_card, rt),
    }
    rt.account_send_lock = asyncio.Lock()

    await startup.prepare_caches(rt)

    # Handler'lara `chats=` VERMİYORUZ: Telethon o filtreyi ilk mesajda çözer ve
    # çözümleme hatası tüm update akışını öldürür. Filtreyi burada kendimiz yapıyoruz.
    @rt.client.on(events.NewMessage())
    async def on_control_message(event: events.NewMessage.Event) -> None:
        return await controlcmds.on_control_message(rt, event)

    @rt.client.on(events.NewMessage())
    async def on_new_message(event: events.NewMessage.Event) -> None:
        return await listener.on_new_message(rt, event)

    rt.poll_delivery_tasks = set()

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

    lifecycle.start_background_tasks(rt, auto_restart, restart_minutes)

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
