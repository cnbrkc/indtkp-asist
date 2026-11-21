"""Süreç yaşam döngüsü: özel kanal durumu ve zamanlanmış yeniden başlatma."""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from collections.abc import Callable
from typing import Any

from telethon import TelegramClient

import appstate
import gitops
import pollcursor
from runtime import Runtime
from telegram_retry import gate_stats
from textutil import humanize

log = logging.getLogger("telegram-filter")


async def private_status_changed(rt: Runtime, status: str, detail: str) -> None:
    """Rutin açılışı sessiz tut; gerçek özel komut arızalarını bildirmeyi sürdür."""
    if status == "running":
        log.info("Özel komutlar hazır: %s", detail)
        return
    if status == "recovered":
        text = "✅ Özel komut kanalı toparlandı; botun özel sohbetinden komut verebilirsin."
    elif status == "webhook":
        text = ("⚠️ Özel komutlar ÇALIŞMIYOR: bildirim botunda bir webhook tanımlı; "
                "getUpdates ile çelişiyor. Webhook'u kaldırıp takipçiyi yeniden başlat "
                "(webhook otomatik silinmez). Komutlar şimdilik Kayıtlı Mesajlar'dan işlenir.")
    elif status == "unauthorized":
        text = ("⚠️ Özel komutlar ÇALIŞMIYOR: bot token'ı geçersiz (401). "
                "NOTIFY_BOT_TOKEN secret'ını BotFather'dan kontrol edip güncelle. "
                "Komutlar şimdilik Kayıtlı Mesajlar'dan işlenir.")
    elif status == "conflict":
        text = ("⚠️ Özel komutlar ÇALIŞMIYOR: aynı bot token'ıyla ikinci bir getUpdates "
                f"tüketicisi var ({detail}). Eski bir çalışma/VM/başka bir proje aynı botu "
                "kullanıyor olabilir; onu kapat. Bu arada komutlar Kayıtlı Mesajlar'dan işlenir.")
    else:
        return
    try:
        await rt.client.send_message(appstate.DESTINATION, text)
    except Exception as exc:  # noqa: BLE001
        log.warning("Özel komut durum uyarısı gruba gönderilemedi: %s", type(exc).__name__)


async def prepare_scheduled_restart(rt: Runtime) -> bool:
    """Planlı yenilemeden önce yeni mesaj almayı durdurur (RESTARTING), uçuştaki işleri kısa süre bekler ve imleçleri kaydeder."""
    appstate.RESTARTING = True
    drain_deadline = time.time() + 6
    while time.time() < drain_deadline and (appstate.SOURCE_IN_FLIGHT or rt.poll_delivery_tasks):
        await asyncio.sleep(0.2)
    if appstate.SOURCE_IN_FLIGHT or rt.poll_delivery_tasks:
        log.warning("Otomatik restart drain %ss doldu — %s uçuş + %s poll görevi kaldı; güvenli cursor kaydedilip yenileme sürüyor.",
                    6, len(appstate.SOURCE_IN_FLIGHT), len(rt.poll_delivery_tasks))
    ok_save, save_note = await pollcursor.cursor_persist_for_restart(rt.store)
    if not ok_save:
        appstate.RESTARTING = False
        log.error("Otomatik restart cursor kaydı başarısız; mevcut süreç dinlemeye devam edecek: %s", save_note)
        return False
    log.info("Otomatik restart cursor'ı kaydedildi: %s", save_note)
    return True


async def resume_after_scheduled_restart_failure(rt: Runtime) -> None:
    """Yeni çalışma başlatılamazsa mevcut süreçte dinlemeye devam eder (RESTARTING kapatılır)."""
    appstate.RESTARTING = False
    log.warning("Yeni çalışma başlatılamadı; mevcut süreç kaynakları dinlemeye devam edecek.")


async def auto_restart_scheduler(
    client: TelegramClient, destination: Any, pat: str, delay_seconds: int, *,
    prepare_restart: Callable[[], Any] | None = None,
    resume_after_failure: Callable[[], Any] | None = None,
) -> None:  # noqa: ARG001 - dış API uyumu
    """Eski Actions çalışmasını güvenli biçimde devret, sonra yenisini başlat.

    ``prepare_restart`` cursor'ı kalıcılaştırıp devam eden teslimatları
    boşaltmalıdır. Yeni workflow başlatılamazsa ``resume_after_failure`` eski
    sürecin kaynakları dinlemeye devam etmesini sağlar.
    """
    await asyncio.sleep(delay_seconds)
    log.info("Takipçi oturumu yenileniyor (%s doldu).", humanize(delay_seconds))
    if prepare_restart is not None:
        prepared = await prepare_restart()
        if not prepared:
            log.error("Otomatik yenileme iptal: kaynak cursor'ı güvenle kaydedilemedi.")
            return
    ok, detail = await gitops.dispatch_next_run(pat)
    if not ok:
        log.error("Otomatik yenileme başlatılamadı: %s", detail)
        if resume_after_failure is not None:
            await resume_after_failure()


async def heartbeat() -> None:
    """Actions log'unda 'hâlâ dinliyor mu?' sorusunu cevaplayan periyodik satır."""
    while True:
        await asyncio.sleep(1800)
        log.info(
            "Kalp atışı: %s çalışıyor | görülen=%d eşleşen=%d iletilen=%d"
            " | tarama aralığı=%.0f sn (son yoklama %.1f sn, FloodWait=%d)"
            " | Bot API 429=%d (retry bekleme %.1f sn, kilit bekleme %.1f sn)",
            humanize(time.time() - appstate.STARTED_AT), appstate.STATS["seen"], appstate.STATS["matched"], appstate.STATS["forwarded"],
            appstate.POLL_TELEMETRY["interval"], appstate.POLL_TELEMETRY["last_elapsed"], int(appstate.POLL_TELEMETRY["flood_count"]),
            gate_stats(appstate.NOTIFY_BOT_TOKEN)["rate_limit_count"],
            gate_stats(appstate.NOTIFY_BOT_TOKEN)["retry_wait_seconds"],
            gate_stats(appstate.NOTIFY_BOT_TOKEN)["lock_wait_seconds"],
        )


def start_background_tasks(rt: Runtime, auto_restart: bool, restart_minutes: int) -> None:
    """Otomatik yenileme zamanlayıcısını (GH_PAT varsa) ve nabız görevini başlatır."""
    if auto_restart and rt.gh_pat:
        asyncio.create_task(auto_restart_scheduler(
            rt.client, appstate.DESTINATION, rt.gh_pat, max(60, restart_minutes * 60),
            prepare_restart=functools.partial(prepare_scheduled_restart, rt),
            resume_after_failure=functools.partial(resume_after_scheduled_restart_failure, rt),
        ))
    elif auto_restart:
        log.warning("auto_restart açık ama GH_PAT yok; oturum Actions süresi bitince kapanacak.")
    asyncio.create_task(heartbeat())
