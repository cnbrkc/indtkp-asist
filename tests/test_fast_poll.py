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
            chat_id = sorted(bot.SOURCE_IDS)[0]
            top = 10 if test.polls == 1 else 11      # ilk tur başlangıç, ikinci turda yeni mesaj
            peer = utils.get_peer(chat_id)
            return SimpleNamespace(dialogs=[SimpleNamespace(peer=peer, top_message=top)])

        async def get_messages(client_self, peer, min_id=0, limit=30):
            chat_id = sorted(bot.SOURCE_IDS)[0]
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
        self.assertEqual(bot.STATS["matched"], 1)
        chat_id = sorted(bot.SOURCE_IDS)[0]
        # Aynı mesaj sonradan canlı olayla gelirse tekrar işlenmemeli
        late_live = SimpleNamespace(chat_id=chat_id, id=11)
        self.assertTrue(bot.live_event_should_skip(late_live))
        self.assertFalse(bot.live_event_should_skip(SimpleNamespace(chat_id=chat_id, id=12)))

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
        old_date = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)

        def make(channel_id, message_id, text, date):
            return types.Message(
                id=message_id, peer_id=types.PeerChannel(channel_id), post=True,
                date=date, message=text,
            )

        async def get_input_entity(client_self, chat_id):
            return types.InputPeerChannel(channel_id=abs(chat_id) % 10**10, access_hash=1)

        async def call(client_self, request):
            test.polls += 1
            chat_id = sorted(bot.SOURCE_IDS)[0]
            peer = utils.get_peer(chat_id)
            return SimpleNamespace(dialogs=[SimpleNamespace(peer=peer, top_message=10)])

        async def get_messages(client_self, peer, min_id=0, limit=30):
            chat_id = sorted(bot.SOURCE_IDS)[0]
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
        # Yaş sınırı (900 sn): 800 sn önceki mesaj gelir, 1 saat önceki gelmez.
        self.assertEqual(len(client.delivered), 2, "yalnızca yaş sınırının içindeki mesajlar iletilmeli")
        # Aynı mesaj canlı olayla da gelse ikinci kez işlenmez.
        chat_id = sorted(bot.SOURCE_IDS)[0]
        self.assertTrue(bot.live_event_should_skip(SimpleNamespace(chat_id=chat_id, id=10)))

    def test_backfill_same_product_burst_merges_to_one_delivery(self):
        # Aynı fırsatın birçok kopyası geriye taramayla aynı anda gelse bile
        # dedup rezervasyonu tek bildirim üretir (toplu patlama olmaz).
        test = self

        async def get_input_entity(client_self, chat_id):
            return types.InputPeerChannel(channel_id=abs(chat_id) % 10**10, access_hash=1)

        async def call(client_self, request):
            chat_id = sorted(bot.SOURCE_IDS)[0]
            peer = utils.get_peer(chat_id)
            return SimpleNamespace(dialogs=[SimpleNamespace(peer=peer, top_message=10)])

        async def get_messages(client_self, peer, min_id=0, limit=30):
            chat_id = sorted(bot.SOURCE_IDS)[0]
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
             mock.patch.object(bot, "DEDUP_EDIT_MIN_INTERVAL", 0):
            client = self._run_main(config)
        self.assertEqual(len(client.delivered), 1)
        self.assertEqual(bot.STATS["deduped"], 4)

    def test_backfill_unique_opportunities_are_delivered_serially(self):
        # Açılışta bulunan benzersiz fırsatların hepsi iletilir ama geriye
        # tarama bunları eşzamanlı görev patlamasına çevirmez (sıralı yol).
        async def get_input_entity(client_self, chat_id):
            return types.InputPeerChannel(channel_id=abs(chat_id) % 10**10, access_hash=1)

        async def call(client_self, request):
            chat_id = sorted(bot.SOURCE_IDS)[0]
            peer = utils.get_peer(chat_id)
            return SimpleNamespace(dialogs=[SimpleNamespace(peer=peer, top_message=10)])

        async def get_messages(client_self, peer, min_id=0, limit=30):
            chat_id = sorted(bot.SOURCE_IDS)[0]
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
        self.assertEqual(len(client.delivered), 5)

    def test_backfill_requests_are_bounded_for_24_sources(self):
        # 24 kaynakta açılış: 1 toplu GetPeerDialogs + kaynak başına en fazla
        # 1 get_messages; mesaj başına geçmiş sorgusu yok. Sayı sınırı
        # min_id = top - 100 olarak uygulanır.
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
                       for chat_id in sorted(bot.SOURCE_IDS)]
            return SimpleNamespace(dialogs=dialogs)

        async def get_messages(client_self, peer, min_id=0, limit=30):
            fetches.append(min_id)
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
        self.assertEqual(len(bot.SOURCE_IDS), 24)
        self.assertEqual(len(fetches), 24)
        self.assertTrue(all(min_id == 150 for min_id in fetches))
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
        self.assertLessEqual(bot.POLL_TELEMETRY["interval"], 45.25)

    def test_telethon_internal_wait_also_slows_polling(self):
        done = asyncio.Event()
        real_sleep = asyncio.sleep
        sleeps, calls = [], []
        now = [100.0]
        clock = mock.Mock(wraps=bot.time)
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
             mock.patch.object(bot, "time", clock), \
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
            chat_id = next(iter(bot.SOURCE_IDS))
            return SimpleNamespace(dialogs=[SimpleNamespace(peer=utils.get_peer(chat_id), top_message=top)])

        async def get_messages(client_self, peer, min_id=0, limit=30):
            fetches.append((min_id, limit))
            if len(fetches) == 1:
                raise OSError("temporary history fetch failure")
            done.set()
            return [test._make_message(peer.channel_id, i, f"çay ürün {i}") for i in range(50, 10, -1)]

        async def run_until_disconnected(client_self):
            await asyncio.wait_for(done.wait(), 1)
            # Let the newly scheduled handlers finish before normal shutdown.
            for _ in range(100):
                if bot.STATS["forwarded"] == 40:
                    break
                await real_sleep(0)

        config = self._write_config(fast_poll=True, fast_poll_seconds=1, dedup_enabled=False)
        self.addCleanup(os.unlink, config)
        with mock.patch.object(ti.FakeClient, "get_input_entity", get_input_entity, create=True), \
             mock.patch.object(ti.FakeClient, "__call__", call, create=True), \
             mock.patch.object(ti.FakeClient, "get_messages", get_messages, create=True), \
             mock.patch.object(ti.FakeClient, "_mb_entity_cache", SimpleNamespace(get=lambda *a: None), create=True), \
             mock.patch.object(ti.FakeClient, "run_until_disconnected", run_until_disconnected), \
             mock.patch.object(bot.asyncio, "sleep", sleep):
            client = self._run_main(config)
        # P6: ilk tur artık başlangıç işareti koymakla kalmıyor; sınırlı
        # geriye tarama tabanından (top - 100, burada 0) çekiyor. Çekme
        # başarısız olursa aynı tabandan yeniden deneniyor (imleç ilerlemez).
        self.assertEqual(fetches, [(0, None), (0, None)])
        self.assertEqual(len(client.delivered), 40)
        self.assertFalse(bot.SOURCE_IN_FLIGHT)

    def test_fast_poll_can_be_disabled(self):
        config = self._write_config(fast_poll=False)
        self.addCleanup(os.unlink, config)
        client = self._run_main(config)
        self.assertFalse(bot.FAST_POLL)
        self.assertEqual(client.delivered, [])


class PollIntervalRecoveryTest(unittest.TestCase):
    def test_recovery_halves_but_never_below_base(self):
        self.assertEqual(bot.recover_poll_interval(300, 3), 150)
        self.assertEqual(bot.recover_poll_interval(181, 3), 90.5)
        self.assertEqual(bot.recover_poll_interval(4, 3), 3)
        self.assertEqual(bot.recover_poll_interval(3, 3), 3)
        self.assertEqual(bot.recover_poll_interval(2, 3), 2)

    def test_backoff_still_grows_and_caps(self):
        self.assertEqual(bot.next_poll_interval(3, 180), 180)
        self.assertEqual(bot.next_poll_interval(300, 1000), 300)


class LiveVsPollDedupTest(unittest.TestCase):
    def setUp(self):
        bot.LIVE_SEEN.clear()
        bot.POLL_DISPATCHED.clear()

    def test_live_first_then_poll_is_skipped_by_poller_check(self):
        event = SimpleNamespace(chat_id=-1001, id=5)
        self.assertFalse(bot.live_event_should_skip(event))
        self.assertIn((-1001, 5), bot.LIVE_SEEN)       # tarama bunu görüp atlayacak

    def test_poll_first_then_live_is_skipped(self):
        bot._remember(bot.POLL_DISPATCHED, -1001, 7)
        self.assertTrue(bot.live_event_should_skip(SimpleNamespace(chat_id=-1001, id=7)))

    def test_poll_event_itself_is_not_skipped(self):
        bot._remember(bot.POLL_DISPATCHED, -1001, 9)
        self.assertFalse(bot.live_event_should_skip(SimpleNamespace(chat_id=-1001, id=9, _fast_poll=True)))

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
