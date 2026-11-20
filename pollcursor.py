"""Hızlı tarama imleci (cursor): RAM durumu, restart yedeği ve canlı olay tekilleştirme."""

from __future__ import annotations

import collections
import time
from typing import Any

import appstate
import gitops
from commandtext import ConfigStore

# Budama yok: cursor önünde poll ile henüz doğrulanmamış bir boşluk varken bile pending/failed
# boş olabilir. 500+ kayıtlı tamamlanmış zinciri silmek cursor'u kilitleyebilir; bu yüzden
# completed kayıtları güvenle tüketilene kadar korunur.


def _remember(store_: "collections.OrderedDict[tuple[int, int], float]", chat_id: Any, message_id: Any) -> None:
    store_[(int(chat_id), int(message_id))] = time.time()
    while len(store_) > 5000:
        store_.popitem(last=False)


def live_event_should_skip(event: Any) -> bool:
    """Atomically claim a source/message pair before the first await.

    Both live and polled events use the same registry, including in-flight
    deliveries. POLL_DISPATCHED only marks scheduled work, not completed work.
    """
    key = (int(event.chat_id), int(event.id))
    if key in appstate.LIVE_SEEN or key in appstate.SOURCE_IN_FLIGHT:
        return True
    if key in appstate.POLL_DISPATCHED and not getattr(event, "_fast_poll", False):
        return True
    _remember(appstate.LIVE_SEEN, *key)
    return False


def _cursor_ensure(chat_id: int) -> dict[str, Any]:
    cid = int(chat_id)
    if cid not in appstate.CURSOR_RAM:
        appstate.CURSOR_RAM[cid] = {"cursor": 0, "pending": set(), "completed": set(), "failed": set(), "failed_at": {}, "initialized": True}
    return appstate.CURSOR_RAM[cid]


def _cursor_advance(chat_id: int) -> None:
    """Sade ardışık ilerleme: yalnızca completed zinciri ilerler; gap’ler poll’de doğrulanır."""
    st = _cursor_ensure(chat_id)
    while True:
        nxt = int(st["cursor"]) + 1
        if nxt in st["pending"]:
            break
        if nxt in st["failed"]:
            break  # başarısız — sonraki poll’de retry edilecek, kaybetme yok
        if nxt in st["completed"]:
            st["completed"].remove(nxt)
            st["cursor"] = nxt
            continue
        break


def _cursor_mark_pending(chat_id: int, msg_id: int) -> None:
    st = _cursor_ensure(chat_id)
    mid = int(msg_id)
    st["pending"].add(mid)
    # önceki failed aynı ID yeniden deneniyorsa temizle
    st["failed"].discard(mid)
    st["failed_at"].pop(mid, None)


def _cursor_mark_success(chat_id: int, msg_id: int) -> None:
    st = _cursor_ensure(chat_id)
    mid = int(msg_id)
    st["pending"].discard(mid)
    st["failed"].discard(mid)
    st["failed_at"].pop(mid, None)
    st["completed"].add(mid)
    _cursor_advance(chat_id)


def _cursor_mark_failed(chat_id: int, msg_id: int) -> None:
    st = _cursor_ensure(chat_id)
    mid = int(msg_id)
    st["pending"].discard(mid)
    st["failed"].add(mid)
    st["failed_at"][mid] = time.time()


def cursor_load_from_config(config: dict) -> None:
    """config.json → source_cursors RAM’e yükle (main başlangıcında)."""
    appstate.CURSOR_RAM.clear()
    raw = config.get("source_cursors")
    if not isinstance(raw, dict):
        return
    for k, v in raw.items():
        try:
            cid = int(k)
            cur = int(v)
            # resume_check: bu imleç bir önceki oturumdan kalma; ilk taramada yaş sınırı uygulanır
            appstate.CURSOR_RAM[cid] = {"cursor": cur, "pending": set(), "completed": set(), "failed": set(), "failed_at": {},
                               "initialized": True, "resume_check": True}
        except Exception:
            continue


def cursor_collect_for_persist() -> dict[str, int]:
    """Yalnızca başlangıç noktası belirlenmiş RAM cursor’larını kalıcılaştır."""
    return {
        str(int(cid)): int(st["cursor"])
        for cid, st in appstate.CURSOR_RAM.items()
        if st.get("initialized", True)
    }


async def cursor_persist_for_restart(store: ConfigStore) -> tuple[bool, str]:
    """Bilinçli /restart öncesi tek toplu kalıcı kayıt. Başarı → restart serbest."""
    # Mevcut atomic_write_json + commit_and_push + GIT_WRITE_LOCK yolunu kullan
    store.config["source_cursors"] = cursor_collect_for_persist()
    ok, note = await gitops.save_config(store, "cursor: restart öncesi yedek")
    return ok, note
