"""Süreç yaşam döngüsü: özel kanal durumu ve zamanlanmış yeniden başlatma."""

from __future__ import annotations

import asyncio
import logging

import appstate
import time
from runtime import Runtime

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
    appstate.RESTARTING = True
    drain_deadline = time.time() + 6
    while time.time() < drain_deadline and (appstate.SOURCE_IN_FLIGHT or rt.poll_delivery_tasks):
        await asyncio.sleep(0.2)
    if appstate.SOURCE_IN_FLIGHT or rt.poll_delivery_tasks:
        log.warning("Otomatik restart drain %ss doldu — %s uçuş + %s poll görevi kaldı; güvenli cursor kaydedilip yenileme sürüyor.",
                    6, len(appstate.SOURCE_IN_FLIGHT), len(rt.poll_delivery_tasks))
    ok_save, save_note = await rt.bot.cursor_persist_for_restart(rt.store)
    if not ok_save:
        appstate.RESTARTING = False
        log.error("Otomatik restart cursor kaydı başarısız; mevcut süreç dinlemeye devam edecek: %s", save_note)
        return False
    log.info("Otomatik restart cursor'ı kaydedildi: %s", save_note)
    return True


async def resume_after_scheduled_restart_failure(rt: Runtime) -> None:
    appstate.RESTARTING = False
    log.warning("Yeni çalışma başlatılamadı; mevcut süreç kaynakları dinlemeye devam edecek.")
