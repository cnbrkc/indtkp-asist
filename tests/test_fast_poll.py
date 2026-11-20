"""Hızlı tarama (getPeerDialogs yoklaması) testleri — sahte Telegram istemcisiyle."""
from __future__ import annotations

import asyncio
import datetime
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import bot  # noqa: E402
import time  # noqa: E402
import runtimecfg  # noqa: E402
import pollcursor  # noqa: E402
import appstate  # noqa: E402
import listener  # noqa: E402
import dedupmatch  # noqa: E402
import test_integration as ti  # noqa: E402
from telethon import events, utils  # noqa: E402
from telethon.tl import types  # noqa: E402


class FastPollEndToEndTest(ti.MainHarness, unittest.TestCase):
    def setUp(self):
        ti.reset_state()
        self.addCleanup(ti.reset_state)
        self.polls = 0

    def _make_message(self, channel_id, message_id, text):
        return types.Message(
            id=message_id, peer_id=types.PeerChannel(channel_id), post=True,
            date=datetime.datetime.now(datetime.timezone.utc), message=text,
        )

    def test_poll_catches_new_message_once(self):
        test = self

        async def get_input_entity(client_self, chat_id):
            return types.InputPeerChannel(channel_id=abs(chat_id) % 10**10, access_hash=1)

        async def call(client_self, request):
            test.polls += 1
            chat_id = sorted(appstate.SOURCE_IDS)[0]
            top = 10 if test.polls == 1 else 11      # ilk tur başlangıç, ikinci turda yeni mesaj
            peer = utils.get_peer(chat_id)
            return SimpleNamespace(dialogs=[SimpleNamespace(peer=peer, top_message=top)])

        async def get_messages(client_self, peer, min_id=0, limit=30, max_id=0, reverse=False):
            chat_id = sorted(appstate.SOURCE_IDS)[0]
            channel_id = utils.get_peer(chat_id).channel_id
            return [test._make_message(channel_id, 11, "çay 5 TL fırsat")]

        async def run_until_disconnected(client_self):
            await asyncio.sleep(3.3)       # ~3 yoklama turu (fast_poll_seconds=1)

        config = self._write_config(fast_poll=True, fast_poll_seconds=1)
        self.addCleanup(os.unlink, config)
        with mock.patch.object(ti.FakeClient, "get_input_entity", get_input_entity, create=True), \
             mock.patch.object(ti.FakeClient, "__call__", call, create=True), \
             mock.patch.object(ti.FakeClient, "get_messages", get_messages, create=True), \
             mock.patch.object(ti.FakeClient, "_mb_entity_cache",
                               SimpleNamespace(get=lambda *a, **k: None), create=True), \
             mock.patch.object(ti.FakeClient, "run_until_disconnected", run_until_disconnected):
            client = self._run_main(config)

        self.assertGreaterEqual(self.polls, 2)
        self.assertEqual(len(client.delivered), 1, "tarama mesajı tam bir kez işlemeli")
        self.assertEqual(appstate.STATS["matched"], 1)
        chat_id = sorted(appstate.SOURCE_IDS)[0]
        # Aynı mesaj sonradan canlı olayla gelirse tekrar işlenmemeli
        late_live = SimpleNamespace(chat_id=chat_id, id=11)
        self.assertTrue(pollcursor.live_event_should_skip(late_live))
        self.assertFalse(pollcursor.live_event_should_skip(SimpleNamespace(chat_id=chat_id, id=12)))

    def test_source_entity_flood_wait_isolated_and_retried_after_deadline(self):
        from telethon import errors
        test = self
        channel_ids = (2000, 2001)
        peer_ids = tuple(utils.get_peer_id(types.PeerChannel(cid)) for cid in channel_ids)
        first_source = min(peer_ids)
        entity_attempts = {}
        history_sources = set()
        real_get_entity = ti.FakeClient.get_entity

        async def get_entity(client_self, value):
            if isinstance(value, str) and value.startswith("@src"):
                index = int(value[4:])
                cid = channel_ids[index]
                return types.Channel(id=cid, title=value, username=value[1:],
                                     photo=types.ChatPhotoEmpty(), date=0)
            return await real_get_entity(client_self, value)

        async def get_input_entity(client_self, chat_id):
            entity_attempts[chat_id] = entity_attempts.get(chat_id, 0) + 1
            if chat_id == first_source and entity_attempts[chat_id] == 1:
                raise errors.FloodWaitError(request=mock.Mock(), capture=1)
            return types.InputPeerChannel(channel_id=abs(chat_id) % 10**10, access_hash=1)

        async def call(client_self, request):
            dialogs = [SimpleNamespace(peer=utils.get_peer(cid), top_message=1)
                       for cid in peer_ids]
            return SimpleNamespace(dialogs=dialogs)

        async def get_messages(client_self, peer, min_id=0, limit=30, max_id=0, reverse=False):
            history_sources.add(peer.channel_id)
            now = datetime.datetime.now(datetime.timezone.utc)
            return [types.Message(id=1, peer_id=types.PeerChannel(peer.channel_id), post=True,
                                  date=now, message=f"çay ürün {peer.channel_id}")]

        async def run_until_disconnected(client_self):
            await asyncio.sleep(3.5)

        config = self._write_config(
            fast_poll=True, fast_poll_seconds=1,
            source_chats=["@src0", "@src1"],
            source_cursors={str(peer_ids[0]): 0, str(peer_ids[1]): 0},
            dedup_enabled=False,
        )
        self.addCleanup(os.unlink, config)
        with mock.patch.object(ti.FakeClient, "get_entity", get_entity, create=True), \
             mock.patch.object(ti.FakeClient, "get_input_entity", get_input_entity, create=True), \
             mock.patch.object(ti.FakeClient, "__call__", call, create=True), \
             mock.patch.object(ti.FakeClient, "get_messages", get_messages, create=True), \
             mock.patch.object(ti.FakeClient, "_mb_entity_cache",
                               SimpleNamespace(get=lambda *a, **k: None), create=True), \
             mock.patch.object(ti.FakeClient, "run_until_disconnected", run_until_disconnected), \
             self.assertLogs("telegram-filter", level="WARNING") as logs:
            client = self._run_main(config)
        self.assertGreaterEqual(entity_attempts[first_source], 2, "geçici hata sonraki turda tekrar denenmeli")
        self.assertEqual(history_sources, set(channel_ids), "bir kaynak hatası diğerinin poll'unu engellememeli")
        self.assertEqual(len([item for item in client.delivered if item[0] != "me"]), 2,
                         f"poll teslimi yok; stats={appstate.STATS}; logs={logs.output}")
        self.assertTrue(any("FloodWait" in line and "diğer kaynaklar sürüyor" in line
                            for line in logs.output))

    def test_thirty_message_batch_does_not_starve_another_source(self):
        source_ids = (2000, 2001)
        chat_ids = tuple(utils.get_peer_id(types.PeerChannel(cid)) for cid in source_ids)
        backlog_chat = min(chat_ids)
        backlog_channel = source_ids[chat_ids.index(backlog_chat)]
        other_channel = source_ids[chat_ids.index(max(chat_ids))]
        fetches = []
        real_get_entity = ti.FakeClient.get_entity

        async def get_entity(client_self, value):
            if isinstance(value, str) and value.startswith("@src"):
                index = int(value[4:])
                cid = source_ids[index]
                return types.Channel(id=cid, title=value, username=value[1:],
                                     photo=types.ChatPhotoEmpty(), date=0)
            return await real_get_entity(client_self, value)

        async def get_input_entity(client_self, chat_id):
            return types.InputPeerChannel(channel_id=abs(chat_id) % 10**10, access_hash=1)

        polls = {"n": 0}

        async def call(client_self, request):
            polls["n"] += 1
            # İlk tur: backlog kaynağı kayıtlı cursor'ına (1) eşit → geçmiş istenmez.
            # Sonraki turlarda 1000'e sıçrar → normal (30'luk, eskiden-yeniye) yol.
            return SimpleNamespace(dialogs=[
                SimpleNamespace(peer=utils.get_peer(chat_id),
                                top_message=(1000 if polls["n"] > 1 else 1) if chat_id == backlog_chat else 1)
                for chat_id in chat_ids
            ])

        async def get_messages(client_self, peer, min_id=0, limit=30, max_id=0, reverse=False):
            fetches.append((peer.channel_id, limit, reverse))
            now = datetime.datetime.now(datetime.timezone.utc)
            if peer.channel_id == backlog_channel:
                return [types.Message(id=i, peer_id=types.PeerChannel(peer.channel_id), post=True,
                                      date=now, message=f"çay backlog item {i}")
                        for i in range(min_id + 1, min_id + limit + 1)]
            return [types.Message(id=1, peer_id=types.PeerChannel(peer.channel_id), post=True,
                                  date=now, message="çay other source unique")]

        async def run_until_disconnected(client_self):
            await asyncio.sleep(2.4)
            for _ in range(200):
                if appstate.STATS["forwarded"] >= 31:
                    break
                await asyncio.sleep(0)

        config = self._write_config(
            fast_poll=True, fast_poll_seconds=1, dedup_enabled=False,
            source_chats=["@src0", "@src1"],
            source_cursors={str(backlog_chat): 1, str(max(chat_ids)): 0},
        )
        self.addCleanup(os.unlink, config)
        with mock.patch.object(ti.FakeClient, "get_entity", get_entity, create=True), \
             mock.patch.object(ti.FakeClient, "get_input_entity", get_input_entity, create=True), \
             mock.patch.object(ti.FakeClient, "__call__", call, create=True), \
             mock.patch.object(ti.FakeClient, "get_messages", get_messages, create=True), \
             mock.patch.object(ti.FakeClient, "_mb_entity_cache",
                               SimpleNamespace(get=lambda *a, **k: None), create=True), \
             mock.patch.object(ti.FakeClient, "run_until_disconnected", run_until_disconnected):
            client = self._run_main(config)
        # Diğer kaynak (cursor 0, kayıtlı) yaş sınırlı pencereyle, backlog kaynağı 30'luk dilimle taranır.
        self.assertEqual(fetches, [(other_channel, appstate.FAST_POLL_BACKFILL_LIMIT, False),
                                   (backlog_channel, appstate.FAST_POLL_BATCH_LIMIT, True)])
        self.assertEqual(appstate.STATS["forwarded"], 31, "30-item batch + diğer kaynağın iletisi tamamlanmalı")
        self.assertEqual(len([item for item in client.delivered if item[0] != "me"]), 31)

    def test_flood_wait_is_not_capped_and_poll_interval_backs_off(self):
        from telethon import errors

        done = asyncio.Event()
        calls, sleeps = [], []
        real_sleep = asyncio.sleep

        async def sleep(delay):
            if delay >= 600:  # heartbeat, unrelated to polling
                await asyncio.Event().wait()
            sleeps.append(delay)
            await real_sleep(0)

        async def get_input_entity(client_self, chat_id):
            return types.InputPeerChannel(channel_id=1, access_hash=1)

        async def call(client_self, request):
            calls.append(request)
            if len(calls) == 1:
                raise errors.FloodWaitError(request=request, capture=180)
            done.set()
            return SimpleNamespace(dialogs=[])

        async def run_until_disconnected(client_self):
            await asyncio.wait_for(done.wait(), 1)

        config = self._write_config(fast_poll=True, fast_poll_seconds=3)
        self.addCleanup(os.unlink, config)
        with mock.patch.object(ti.FakeClient, "get_input_entity", get_input_entity, create=True), \
             mock.patch.object(ti.FakeClient, "__call__", call, create=True), \
             mock.patch.object(ti.FakeClient, "run_until_disconnected", run_until_disconnected), \
             mock.patch.object(bot.asyncio, "sleep", sleep):
            self._run_main(config)
        self.assertEqual(sleeps[:3], [3, 181, 181])
        self.assertGreaterEqual(len(calls), 2)

    def test_backfill_delivers_recent_missed_but_not_old_messages(self):
        # P6: açılıştaki geriye tarama, kesinti sırasında gelen (taze) mesajı
        # yakalar ama yaş sınırından eski mesajı topluca yeniden bildirmez.
        test = self
        old_date = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)

        def make(channel_id, message_id, text, date):
            return types.Message(
                id=message_id, peer_id=types.PeerChannel(channel_id), post=True,
                date=date, message=text,
            )

        async def get_input_entity(client_self, chat_id):
            return types.InputPeerChannel(channel_id=abs(chat_id) % 10**10, access_hash=1)

        async def call(client_self, request):
            test.polls += 1
            chat_id = sorted(appstate.SOURCE_IDS)[0]
            peer = utils.get_peer(chat_id)
            return SimpleNamespace(dialogs=[SimpleNamespace(peer=peer, top_message=10)])

        async def get_messages(client_self, peer, min_id=0, limit=30, max_id=0, reverse=False):
            chat_id = sorted(appstate.SOURCE_IDS)[0]
            channel_id = utils.get_peer(chat_id).channel_id
            now = datetime.datetime.now(datetime.timezone.utc)
            return [make(channel_id, 10, "çay 5 TL fırsat", now),
                    make(channel_id, 9, "çay 5 TL fırsat", old_date),
                    make(channel_id, 8, "çay 5 TL fırsat",
                         now - datetime.timedelta(seconds=800))]

        async def run_until_disconnected(client_self):
            await asyncio.sleep(2.2)

        config = self._write_config(fast_poll=True, fast_poll_seconds=1)
        self.addCleanup(os.unlink, config)
        with mock.patch.object(ti.FakeClient, "get_input_entity", get_input_entity, create=True), \
             mock.patch.object(ti.FakeClient, "__call__", call, create=True), \
             mock.patch.object(ti.FakeClient, "get_messages", get_messages, create=True), \
             mock.patch.object(ti.FakeClient, "_mb_entity_cache",
                               SimpleNamespace(get=lambda *a, **k: None), create=True), \
             mock.patch.object(ti.FakeClient, "run_until_disconnected", run_until_disconnected):
            client = self._run_main(config)
        # Yalnızca 30 dk içindeki mesajlar kalır; aynı ürün dedup ile tek teslim olur.
        destination_deliveries = [item for item in client.delivered if item[0] != "me"]
        self.assertEqual(len(destination_deliveries), 1, "taze backlog yakalanmalı, aynı ürün birleşmeli")
        self.assertEqual(appstate.STATS["deduped"], 1)
        chat_id = sorted(appstate.SOURCE_IDS)[0]
        self.assertIn(chat_id, [int(k) for k in appstate.CURSOR_RAM])

    def test_backfill_same_product_burst_merges_to_one_delivery(self):
        # Aynı fırsatın birçok kopyası geriye taramayla aynı anda gelse bile
        # dedup rezervasyonu tek bildirim üretir (toplu patlama olmaz).
        test = self

        async def get_input_entity(client_self, chat_id):
            return types.InputPeerChannel(channel_id=abs(chat_id) % 10**10, access_hash=1)

        async def call(client_self, request):
            chat_id = sorted(appstate.SOURCE_IDS)[0]
            peer = utils.get_peer(chat_id)
            return SimpleNamespace(dialogs=[SimpleNamespace(peer=peer, top_message=10)])

        async def get_messages(client_self, peer, min_id=0, limit=30, max_id=0, reverse=False):
            chat_id = sorted(appstate.SOURCE_IDS)[0]
            channel_id = utils.get_peer(chat_id).channel_id
            now = datetime.datetime.now(datetime.timezone.utc)
            return [types.Message(id=i, peer_id=types.PeerChannel(channel_id), post=True,
                                  date=now, message="çay 5 TL fırsat") for i in range(10, 5, -1)]

        async def run_until_disconnected(client_self):
            await asyncio.sleep(4.5)

        config = self._write_config(fast_poll=True, fast_poll_seconds=1)
        self.addCleanup(os.unlink, config)
        with mock.patch.object(ti.FakeClient, "get_input_entity", get_input_entity, create=True), \
             mock.patch.object(ti.FakeClient, "__call__", call, create=True), \
             mock.patch.object(ti.FakeClient, "get_messages", get_messages, create=True), \
             mock.patch.object(ti.FakeClient, "_mb_entity_cache",
                               SimpleNamespace(get=lambda *a, **k: None), create=True), \
             mock.patch.object(ti.FakeClient, "run_until_disconnected", run_until_disconnected), \
             mock.patch.object(appstate, "DEDUP_EDIT_MIN_INTERVAL", 0):
            client = self._run_main(config)
        # Aynı ürün 5 kaynak iletisinde görünse de tek bildirim üretilmeli.
        self.assertEqual(len([item for item in client.delivered if item[0] != "me"]), 1)
        self.assertEqual(appstate.STATS["deduped"], 4)

    def test_backfill_unique_opportunities_are_delivered_serially(self):
        # Açılışta bulunan benzersiz fırsatların hepsi iletilir ama geriye
        # tarama bunları eşzamanlı görev patlamasına çevirmez (sıralı yol).
        async def get_input_entity(client_self, chat_id):
            return types.InputPeerChannel(channel_id=abs(chat_id) % 10**10, access_hash=1)

        async def call(client_self, request):
            chat_id = sorted(appstate.SOURCE_IDS)[0]
            peer = utils.get_peer(chat_id)
            return SimpleNamespace(dialogs=[SimpleNamespace(peer=peer, top_message=10)])

        async def get_messages(client_self, peer, min_id=0, limit=30, max_id=0, reverse=False):
            chat_id = sorted(appstate.SOURCE_IDS)[0]
            channel_id = utils.get_peer(chat_id).channel_id
            now = datetime.datetime.now(datetime.timezone.utc)
            return [types.Message(id=i, peer_id=types.PeerChannel(channel_id), post=True,
                                  date=now, message=f"çay ürün {i} 5 TL") for i in range(10, 5, -1)]

        async def run_until_disconnected(client_self):
            await asyncio.sleep(3.5)

        config = self._write_config(fast_poll=True, fast_poll_seconds=1)
        self.addCleanup(os.unlink, config)
        with mock.patch.object(ti.FakeClient, "get_input_entity", get_input_entity, create=True), \
             mock.patch.object(ti.FakeClient, "__call__", call, create=True), \
             mock.patch.object(ti.FakeClient, "get_messages", get_messages, create=True), \
             mock.patch.object(ti.FakeClient, "_mb_entity_cache",
                               SimpleNamespace(get=lambda *a, **k: None), create=True), \
             mock.patch.object(ti.FakeClient, "run_until_disconnected", run_until_disconnected):
            client = self._run_main(config)
        self.assertEqual(len([item for item in client.delivered if item[0] != "me"]), 5)

    def test_backfill_requests_are_bounded_for_24_sources(self):
        # 24 kaynakta açılış: tek GetPeerDialogs + her kaynağa tek sınırlı
        # bootstrap geçmiş çağrısı; sayfa sayısı topaktan bağımsızdır.
        fetches = []
        real_get_entity = ti.FakeClient.get_entity

        async def get_entity(client_self, value):
            if isinstance(value, str) and value.startswith("@src"):
                index = int(value[4:])
                return types.Channel(id=2000 + index, title=value, username=value[1:],
                                     photo=types.ChatPhotoEmpty(), date=0)
            return await real_get_entity(client_self, value)

        async def get_input_entity(client_self, chat_id):
            return types.InputPeerChannel(channel_id=abs(chat_id) % 10**10, access_hash=1)

        async def call(client_self, request):
            dialogs = [SimpleNamespace(peer=utils.get_peer(chat_id), top_message=250)
                       for chat_id in sorted(appstate.SOURCE_IDS)]
            return SimpleNamespace(dialogs=dialogs)

        async def get_messages(client_self, peer, min_id=0, limit=30, max_id=0, reverse=False):
            fetches.append((min_id, limit, max_id, reverse))
            return []

        async def run_until_disconnected(client_self):
            await asyncio.sleep(1.2)

        config = self._write_config(
            fast_poll=True, fast_poll_seconds=1,
            source_chats=[f"@src{i}" for i in range(24)],
        )
        self.addCleanup(os.unlink, config)
        with mock.patch.object(ti.FakeClient, "get_entity", get_entity, create=True), \
             mock.patch.object(ti.FakeClient, "get_input_entity", get_input_entity, create=True), \
             mock.patch.object(ti.FakeClient, "__call__", call, create=True), \
             mock.patch.object(ti.FakeClient, "get_messages", get_messages, create=True), \
             mock.patch.object(ti.FakeClient, "_mb_entity_cache",
                               SimpleNamespace(get=lambda *a, **k: None), create=True), \
             mock.patch.object(ti.FakeClient, "run_until_disconnected", run_until_disconnected):
            client = self._run_main(config)
        self.assertEqual(len(appstate.SOURCE_IDS), 24)
        self.assertEqual(len(fetches), 24)
        self.assertTrue(all(limit == appstate.FAST_POLL_BACKFILL_LIMIT and max_id == 251
                            for _, limit, max_id, _ in fetches))
        self.assertEqual(client.delivered, [])

    def test_poll_interval_recovers_gradually_after_flood_wait(self):
        from telethon import errors

        done = asyncio.Event()
        calls, sleeps = [], []
        real_sleep = asyncio.sleep

        async def sleep(delay):
            if delay >= 600:  # heartbeat, unrelated to polling
                await asyncio.Event().wait()
            sleeps.append(delay)
            await real_sleep(0)

        async def get_input_entity(client_self, chat_id):
            return types.InputPeerChannel(channel_id=1, access_hash=1)

        async def call(client_self, request):
            calls.append(request)
            if len(calls) == 1:
                raise errors.FloodWaitError(request=request, capture=180)
            if len(calls) >= 3:
                done.set()
            return SimpleNamespace(dialogs=[])

        async def run_until_disconnected(client_self):
            await asyncio.wait_for(done.wait(), 1)

        config = self._write_config(fast_poll=True, fast_poll_seconds=3)
        self.addCleanup(os.unlink, config)
        with mock.patch.object(ti.FakeClient, "get_input_entity", get_input_entity, create=True), \
             mock.patch.object(ti.FakeClient, "__call__", call, create=True), \
             mock.patch.object(ti.FakeClient, "run_until_disconnected", run_until_disconnected), \
             mock.patch.object(bot.asyncio, "sleep", sleep):
            self._run_main(config)
        # FloodWait aralığı korur; sonraki HIZLI başarılı yoklamalar aralığı
        # yarıya indirerek gevşetir (300 sn'de takılı kalmaz, birden de açılmaz).
        self.assertEqual(sleeps[:4], [3, 181, 181, 90.5])
        self.assertLessEqual(appstate.POLL_TELEMETRY["interval"], 45.25)

    def test_telethon_internal_wait_also_slows_polling(self):
        done = asyncio.Event()
        real_sleep = asyncio.sleep
        sleeps, calls = [], []
        now = [100.0]
        clock = mock.Mock(wraps=time)
        clock.monotonic.side_effect = lambda: now[0]

        async def sleep(delay):
            if delay >= 600:
                await asyncio.Event().wait()
            sleeps.append(delay)
            await real_sleep(0)

        async def get_input_entity(client_self, chat_id):
            return types.InputPeerChannel(channel_id=1, access_hash=1)

        async def call(client_self, request):
            calls.append(request)
            if len(calls) == 1:
                now[0] += 4  # Telethon slept internally, no exception escaped
            else:
                done.set()
            return SimpleNamespace(dialogs=[])

        async def run_until_disconnected(client_self):
            await asyncio.wait_for(done.wait(), 1)

        config = self._write_config(fast_poll=True, fast_poll_seconds=3)
        self.addCleanup(os.unlink, config)
        with mock.patch.object(ti.FakeClient, "get_input_entity", get_input_entity, create=True), \
             mock.patch.object(ti.FakeClient, "__call__", call, create=True), \
             mock.patch.object(ti.FakeClient, "run_until_disconnected", run_until_disconnected), \
             mock.patch.object(listener, "time", clock), \
             mock.patch.object(bot.asyncio, "sleep", sleep):
            self._run_main(config)
        self.assertEqual(sleeps[:2], [3, 10])

    def test_history_failure_retries_cursor_and_delivers_more_than_thirty_messages(self):
        done = asyncio.Event()
        real_sleep = asyncio.sleep
        fetches = []
        test = self

        async def sleep(delay):
            if delay >= 600:
                await asyncio.Event().wait()
            await real_sleep(0)

        async def get_input_entity(client_self, chat_id):
            return utils.get_peer(chat_id)

        async def call(client_self, request):
            test.polls += 1
            top = 10 if test.polls == 1 else 50
            chat_id = next(iter(appstate.SOURCE_IDS))
            return SimpleNamespace(dialogs=[SimpleNamespace(peer=utils.get_peer(chat_id), top_message=top)])

        async def get_messages(client_self, peer, min_id=0, limit=30, max_id=0, reverse=False):
            fetches.append((min_id, limit))
            if len(fetches) == 1:
                raise OSError("temporary history fetch failure")
            done.set()
            end = 30 if min_id == 0 else 40
            return [test._make_message(peer.channel_id, i, f"çay ürün {i}")
                    for i in range(min_id + 1, end + 1)]

        async def run_until_disconnected(client_self):
            await asyncio.wait_for(done.wait(), 2)
            # The bounded page leaves another 10-message page for the next poll.
            await real_sleep(1.2)
            for _ in range(100):
                if appstate.STATS["forwarded"] >= 40:
                    break
                await real_sleep(0.01)

        config = self._write_config(fast_poll=True, fast_poll_seconds=1, dedup_enabled=False, source_cursors={"-2001111111111": 0})
        self.addCleanup(os.unlink, config)
        with mock.patch.object(ti.FakeClient, "get_input_entity", get_input_entity, create=True), \
             mock.patch.object(ti.FakeClient, "__call__", call, create=True), \
             mock.patch.object(ti.FakeClient, "get_messages", get_messages, create=True), \
             mock.patch.object(ti.FakeClient, "_mb_entity_cache", SimpleNamespace(get=lambda *a: None), create=True), \
             mock.patch.object(ti.FakeClient, "run_until_disconnected", run_until_disconnected), \
             mock.patch.object(bot.asyncio, "sleep", sleep):
            client = self._run_main(config)
        # Geçici history hatası cursor'ı ilerletmez; ikinci deneme (kayıtlı cursor için
        # yaş sınırlı pencere) ilk 30 mesajı, sonraki tur kalan 10 mesajı alır.
        self.assertEqual(fetches[:3], [(0, 100), (0, 100), (30, 30)])
        self.assertEqual(len([item for item in client.delivered if item[0] != "me"]), 40)
        self.assertFalse(appstate.SOURCE_IN_FLIGHT)

    # --- Restart / duraklama sonrası geriye tarama: yaş sınırı (varsayılan 15 dk) ---

    def _run_resume_scenario(self, *, saved_cursor, tops, ages, expect_deliveries, run_seconds=3.4,
                             extra_config=None):
        """Kayıtlı cursor ile açılış. tops: tur başına top_message (son değer tekrar edilir);
        ages: {mesaj_id: yaş_saniye}. Döner: (client, get_messages çağrıları)."""
        test = self
        calls = []
        real_sleep = asyncio.sleep
        chat_id = -2001111111111

        def messages_at(now):
            return [types.Message(id=mid, peer_id=types.PeerChannel(channel_id), post=True,
                                  date=now - datetime.timedelta(seconds=age), message=f"çay ürün {mid}")
                    for mid, age in ages.items()]

        channel_id = utils.get_peer(chat_id).channel_id

        async def get_input_entity(client_self, cid):
            return types.InputPeerChannel(channel_id=channel_id, access_hash=1)

        async def call(client_self, request):
            top = tops[min(test.polls, len(tops) - 1)]
            test.polls += 1
            return SimpleNamespace(dialogs=[SimpleNamespace(peer=utils.get_peer(chat_id), top_message=top)])

        async def get_messages(client_self, peer, min_id=0, limit=30, max_id=0, reverse=False):
            calls.append({"min_id": min_id, "limit": limit, "max_id": max_id, "reverse": reverse})
            msgs = messages_at(datetime.datetime.now(datetime.timezone.utc))
            top = tops[min(max(test.polls - 1, 0), len(tops) - 1)]
            msgs = [m for m in msgs if m.id <= top]
            if max_id:
                return sorted([m for m in msgs if m.id < max_id], key=lambda m: -m.id)[:limit]
            return sorted([m for m in msgs if m.id > min_id], key=lambda m: m.id)[:limit]

        async def run_until_disconnected(client_self):
            waited = 0.0
            while waited < run_seconds:
                await real_sleep(0.05)
                waited += 0.05
                if len(client_self.delivered) >= expect_deliveries and waited > 1.2:
                    break

        config = self._write_config(fast_poll=True, fast_poll_seconds=1, dedup_enabled=False,
                                    source_cursors={str(chat_id): saved_cursor}, **(extra_config or {}))
        self.addCleanup(os.unlink, config)
        with mock.patch.object(ti.FakeClient, "get_input_entity", get_input_entity, create=True), \
             mock.patch.object(ti.FakeClient, "__call__", call, create=True), \
             mock.patch.object(ti.FakeClient, "get_messages", get_messages, create=True), \
             mock.patch.object(ti.FakeClient, "_mb_entity_cache", SimpleNamespace(get=lambda *a, **k: None), create=True), \
             mock.patch.object(ti.FakeClient, "run_until_disconnected", run_until_disconnected):
            client = self._run_main(config)
        return client, calls

    def test_stale_saved_cursor_replays_only_recent_window(self):
        # Kayıtlı cursor (5) çok eski: 6-7 numaralı mesajlar 2 saat önceki. Yalnızca son
        # 15 dk içindeki 8-10 bildirilir; cursor'dan itibaren tam geçmiş çekilmez.
        client, calls = self._run_resume_scenario(
            saved_cursor=5, tops=[10],
            ages={10: 5, 9: 200, 8: 600, 7: 7200, 6: 7500}, expect_deliveries=3)
        delivered = list(client.delivered)
        self.assertEqual(len(delivered), 3, "yalnızca 15 dk içindeki 3 mesaj bildirilmeli")
        self.assertEqual(calls[0]["max_id"], 11, "kayıtlı cursor önce yaş sınırlı pencere yolundan geçmeli")
        self.assertEqual(calls[0]["limit"], appstate.FAST_POLL_BACKFILL_LIMIT)
        self.assertFalse(any(c["reverse"] and c["min_id"] == 5 for c in calls),
                         "eski cursor'dan başlayıp tüm birikimi çekmemeli")

    def test_recent_saved_cursor_is_kept_and_not_replayed(self):
        # Kopukluk kısa: cursor (8) pencere içinde. 8 zaten bildirilmişti; yalnızca 9-10 gelir.
        client, calls = self._run_resume_scenario(
            saved_cursor=8, tops=[10], ages={10: 5, 9: 60, 8: 120, 7: 180}, expect_deliveries=2)
        delivered = list(client.delivered)
        self.assertEqual(len(delivered), 2, "cursor'dan sonraki 2 mesaj bildirilmeli, 8 tekrar bildirilmemeli")

    def test_saved_cursor_without_new_messages_makes_no_history_request(self):
        client, calls = self._run_resume_scenario(
            saved_cursor=10, tops=[10], ages={10: 5}, expect_deliveries=0, run_seconds=1.6)
        self.assertEqual(calls, [], "kaçırılan mesaj yoksa geçmiş istenmemeli")
        self.assertEqual(list(client.delivered), [])

    def test_pause_gap_skips_old_backlog_and_delivers_recent_only(self):
        # Çalışırken duraklama: cursor 10 iken top 20'ye sıçradı; 11-13 saatler önceki,
        # 14-20 taze. Eski birikim toplu bildirilmez; yalnızca taze 7 mesaj gider.
        ages = {20: 3, 19: 8, 18: 20, 17: 40, 16: 60, 15: 90, 14: 120, 13: 9000, 12: 9100, 11: 9200, 10: 9300}
        client, calls = self._run_resume_scenario(
            saved_cursor=10, tops=[10, 20], ages=ages, expect_deliveries=7, run_seconds=4.5)
        delivered = list(client.delivered)
        self.assertEqual(len(delivered), 7, "yalnızca 15 dk içindeki mesajlar bildirilmeli")

    def test_backfill_window_is_configurable_and_defaults_to_fifteen_minutes(self):
        original = appstate.FAST_POLL_BACKFILL_MAX_AGE
        self.addCleanup(setattr, appstate, "FAST_POLL_BACKFILL_MAX_AGE", original)
        self.assertEqual(original, 900.0)
        runtimecfg.apply_runtime_config({})
        self.assertEqual(appstate.FAST_POLL_BACKFILL_MAX_AGE, 900.0)
        runtimecfg.apply_runtime_config({"fast_poll_backfill_minutes": 5})
        self.assertEqual(appstate.FAST_POLL_BACKFILL_MAX_AGE, 300.0)
        runtimecfg.apply_runtime_config({"fast_poll_backfill_minutes": 9999})
        self.assertEqual(appstate.FAST_POLL_BACKFILL_MAX_AGE, 7200.0)
        runtimecfg.apply_runtime_config({"fast_poll_backfill_minutes": "bozuk"})
        self.assertEqual(appstate.FAST_POLL_BACKFILL_MAX_AGE, 900.0)

    def test_fast_poll_can_be_disabled(self):
        config = self._write_config(fast_poll=False)
        self.addCleanup(os.unlink, config)
        client = self._run_main(config)
        self.assertFalse(appstate.FAST_POLL)
        self.assertEqual(client.delivered, [])


class PollIntervalRecoveryTest(unittest.TestCase):
    def test_recovery_halves_but_never_below_base(self):
        self.assertEqual(dedupmatch.recover_poll_interval(300, 3), 150)
        self.assertEqual(dedupmatch.recover_poll_interval(181, 3), 90.5)
        self.assertEqual(dedupmatch.recover_poll_interval(4, 3), 3)
        self.assertEqual(dedupmatch.recover_poll_interval(3, 3), 3)
        self.assertEqual(dedupmatch.recover_poll_interval(2, 3), 2)

    def test_backoff_still_grows_and_caps(self):
        self.assertEqual(dedupmatch.next_poll_interval(3, 180), 180)
        self.assertEqual(dedupmatch.next_poll_interval(300, 1000), 300)


class LiveVsPollDedupTest(unittest.TestCase):
    def setUp(self):
        appstate.LIVE_SEEN.clear()
        appstate.POLL_DISPATCHED.clear()

    def test_live_first_then_poll_is_skipped_by_poller_check(self):
        event = SimpleNamespace(chat_id=-1001, id=5)
        self.assertFalse(pollcursor.live_event_should_skip(event))
        self.assertIn((-1001, 5), appstate.LIVE_SEEN)       # tarama bunu görüp atlayacak

    def test_poll_first_then_live_is_skipped(self):
        pollcursor._remember(appstate.POLL_DISPATCHED, -1001, 7)
        self.assertTrue(pollcursor.live_event_should_skip(SimpleNamespace(chat_id=-1001, id=7)))

    def test_poll_event_itself_is_not_skipped(self):
        pollcursor._remember(appstate.POLL_DISPATCHED, -1001, 9)
        self.assertFalse(pollcursor.live_event_should_skip(SimpleNamespace(chat_id=-1001, id=9, _fast_poll=True)))

    def test_real_event_object_accepts_fast_poll_flag(self):
        message = types.Message(id=3, peer_id=types.PeerChannel(1001), post=True,
                                date=datetime.datetime.now(datetime.timezone.utc), message="çay")
        event = events.NewMessage.Event(message)
        event._fast_poll = True
        self.assertTrue(getattr(event, "_fast_poll", False))
        self.assertEqual(event.id, 3)
        self.assertEqual(event.raw_text, "çay")
        self.assertEqual(event.chat_id, -1000000001001)


if __name__ == "__main__":
    unittest.main()
