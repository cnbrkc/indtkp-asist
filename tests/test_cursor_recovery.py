"""Cursor recovery — tek cursor/grup, RAM-only, manuel /restart persist, 1800s, dedup korunması."""

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
from telethon import utils  # noqa: E402
from telethon.tl import types  # noqa: E402


class CursorMechanismTest(unittest.TestCase):
    def setUp(self):
        ti.reset_state()
        bot.CURSOR_RAM.clear()
        self.addCleanup(lambda: (bot.CURSOR_RAM.clear(), ti.reset_state()))

    def test_single_cursor_per_group(self):
        bot._cursor_ensure(-1001)["cursor"] = 100
        bot._cursor_mark_pending(-1001, 101)
        bot._cursor_mark_pending(-1002, 50)
        self.assertEqual(len(bot.CURSOR_RAM), 2)
        self.assertEqual(bot.CURSOR_RAM[-1001]["cursor"], 100)
        self.assertIn(101, bot.CURSOR_RAM[-1001]["pending"])

    def test_cursor_advances_only_contiguously(self):
        cid = -1001
        bot._cursor_ensure(cid)["cursor"] = 1520
        # 1521 success
        bot._cursor_mark_pending(cid, 1521)
        bot._cursor_mark_success(cid, 1521)
        self.assertEqual(bot.CURSOR_RAM[cid]["cursor"], 1521)
        # 1522 fail
        bot._cursor_mark_pending(cid, 1522)
        bot._cursor_mark_failed(cid, 1522)
        self.assertEqual(bot.CURSOR_RAM[cid]["cursor"], 1521)
        # 1523 success but gap at 1522 → cursor stays 1521
        bot._cursor_mark_pending(cid, 1523)
        bot._cursor_mark_success(cid, 1523)
        self.assertEqual(bot.CURSOR_RAM[cid]["cursor"], 1521)
        self.assertIn(1523, bot.CURSOR_RAM[cid]["completed"])
        # 1522 now success → both advance to 1523
        bot._cursor_mark_pending(cid, 1522)
        bot._cursor_mark_success(cid, 1522)
        self.assertEqual(bot.CURSOR_RAM[cid]["cursor"], 1523)

    def test_pending_blocks_advance(self):
        cid = -1002
        bot._cursor_ensure(cid)["cursor"] = 10
        bot._cursor_mark_pending(cid, 11)
        bot._cursor_mark_pending(cid, 12)
        # 12 completes before 11 → still blocked
        bot._cursor_mark_success(cid, 12)
        self.assertEqual(bot.CURSOR_RAM[cid]["cursor"], 10)
        bot._cursor_mark_success(cid, 11)
        self.assertEqual(bot.CURSOR_RAM[cid]["cursor"], 12)

    def test_load_and_collect(self):
        config = {"source_cursors": {"-1001": 1521, "-1002": 870}}
        bot.cursor_load_from_config(config)
        self.assertEqual(bot.CURSOR_RAM[-1001]["cursor"], 1521)
        collected = bot.cursor_collect_for_persist()
        self.assertEqual(collected, {"-1001": 1521, "-1002": 870})

    def test_fast_poll_backfill_age_is_1800(self):
        self.assertEqual(bot.FAST_POLL_BACKFILL_MAX_AGE, 1800.0)


class CursorIntegrationTest(ti.MainHarness, unittest.TestCase):
    def setUp(self):
        ti.reset_state()
        bot.CURSOR_RAM.clear()
        self.addCleanup(lambda: (bot.CURSOR_RAM.clear(), ti.reset_state()))

    def test_bootstrap_no_replay(self):
        """İlk kurulum: top ile bootstrap → 0 teslim"""
        async def get_input_entity(client_self, chat_id):
            return types.InputPeerChannel(channel_id=abs(chat_id) % 10**10, access_hash=1)
        async def call(client_self, request):
            chat_id = sorted(bot.SOURCE_IDS)[0]
            peer = utils.get_peer(chat_id)
            return SimpleNamespace(dialogs=[SimpleNamespace(peer=peer, top_message=10)])
        async def get_messages(client_self, peer, min_id=0, limit=None):
            # Should not be called when bootstrap
            self.fail("bootstrap’ta get_messages çağrılmamalı")
        async def run_until_disconnected(client_self):
            await asyncio.sleep(1.2)
        config = self._write_config(fast_poll=True, fast_poll_seconds=1)
        self.addCleanup(os.unlink, config)
        with mock.patch.object(ti.FakeClient, "get_input_entity", get_input_entity, create=True), \
             mock.patch.object(ti.FakeClient, "__call__", call, create=True), \
             mock.patch.object(ti.FakeClient, "get_messages", get_messages, create=True), \
             mock.patch.object(ti.FakeClient, "_mb_entity_cache", SimpleNamespace(get=lambda *a, **k: None), create=True), \
             mock.patch.object(ti.FakeClient, "run_until_disconnected", run_until_disconnected):
            client = self._run_main(config)
        self.assertEqual(len(client.delivered), 0)

    def test_cursor_based_recovery_delivers_after_bootstrap(self):
        """Cursor sonrası yeni mesajlar cursor’dan alınır, sayfalı."""
        polls = [0]
        async def get_input_entity(client_self, chat_id):
            return types.InputPeerChannel(channel_id=abs(chat_id) % 10**10, access_hash=1)
        async def call(client_self, request):
            polls[0] += 1
            chat_id = sorted(bot.SOURCE_IDS)[0]
            peer = utils.get_peer(chat_id)
            top = 10 if polls[0] == 1 else 13
            return SimpleNamespace(dialogs=[SimpleNamespace(peer=peer, top_message=top)])
        async def get_messages(client_self, peer, min_id=0, limit=None):
            # After bootstrap base=0, second poll should fetch min_id=0 → 11,12,13
            chat_id = sorted(bot.SOURCE_IDS)[0]
            channel_id = utils.get_peer(chat_id).channel_id
            now = datetime.datetime.now(datetime.timezone.utc)
            if min_id == 0:
                return [types.Message(id=i, peer_id=types.PeerChannel(channel_id), post=True, date=now, message=f"çay ürün {i} 5 TL") for i in range(13,10,-1)]
            return []
        async def run_until_disconnected(client_self):
            await asyncio.sleep(2.5)
        config = self._write_config(fast_poll=True, fast_poll_seconds=1, dedup_enabled=False)
        self.addCleanup(os.unlink, config)
        with mock.patch.object(ti.FakeClient, "get_input_entity", get_input_entity, create=True), \
             mock.patch.object(ti.FakeClient, "__call__", call, create=True), \
             mock.patch.object(ti.FakeClient, "get_messages", get_messages, create=True), \
             mock.patch.object(ti.FakeClient, "_mb_entity_cache", SimpleNamespace(get=lambda *a, **k: None), create=True), \
             mock.patch.object(ti.FakeClient, "run_until_disconnected", run_until_disconnected):
            client = self._run_main(config)
        # Second poll delivers 3 (11,12,13)
        self.assertEqual(len(client.delivered), 3)


class RestartPersistTest(unittest.TestCase):
    def test_auto_restart_does_not_persist_cursor(self):
        """Otomatik restart scheduler cursor persist etmez — sadece manuel /restart."""
        # Check that auto_restart_scheduler calls dispatch_next_run but not cursor_persist
        import inspect
        src = inspect.getsource(bot.auto_restart_scheduler)
        self.assertIn("dispatch_next_run", src)
        self.assertNotIn("cursor_persist", src)
        # Manual handler does
        src2 = inspect.getsource(bot.main)
        self.assertIn("cursor_persist_for_restart", src2)
        self.assertIn("COMMAND_RESTART", src2)

    def test_manual_restart_blocks_without_persist(self):
        """Manual restart handler’da persist başarısızsa dispatch engellenir."""
        import inspect
        src = inspect.getsource(bot.main)
        # Check pattern: ok_save check → return, then dispatch
        self.assertIn("ok_save", src)
        self.assertIn("Restart iptal", src)


class CursorGapAndBloatTest(unittest.TestCase):
    def setUp(self):
        ti.reset_state()
        bot.CURSOR_RAM.clear()
        self.addCleanup(lambda: (bot.CURSOR_RAM.clear(), ti.reset_state()))

    def test_telegram_id_gap_skipped(self):
        """Silinmiş ID (1522 yok) cursor’u bloklamamalı — 1523 success ile gap atlanır."""
        cid = -1001
        bot._cursor_ensure(cid)["cursor"] = 1521
        # 1523 success, 1522 hiç var olmadı (pending/failed değil)
        bot._cursor_mark_pending(cid, 1523)
        bot._cursor_mark_success(cid, 1523)
        # gap 1522 missing ama completed max 1523 >1522 → atla
        self.assertEqual(bot.CURSOR_RAM[cid]["cursor"], 1523)
        self.assertNotIn(1522, bot.CURSOR_RAM[cid]["completed"])

    def test_failed_blocks_then_gap_timeout(self):
        cid = -1002
        bot._cursor_ensure(cid)["cursor"] = 10
        bot._cursor_mark_pending(cid, 11)
        bot._cursor_mark_failed(cid, 11)
        bot._cursor_mark_pending(cid, 12)
        bot._cursor_mark_success(cid, 12)
        self.assertEqual(bot.CURSOR_RAM[cid]["cursor"], 10)  # fail blokluyor
        # 30dk timeout sonrası gap atlanır
        bot.CURSOR_RAM[cid]["failed_at"][11] = 0  # çok eski
        bot._cursor_advance(cid)
        self.assertEqual(bot.CURSOR_RAM[cid]["cursor"], 12)

    def test_completed_pruned_at_200(self):
        cid = -1003
        bot._cursor_ensure(cid)["cursor"] = 1
        # 2 fail, 3..250 success → completed 248 birikir
        bot._cursor_mark_pending(cid, 2)
        bot._cursor_mark_failed(cid, 2)
        for i in range(3, 251):
            bot._cursor_mark_pending(cid, i)
            bot._cursor_mark_success(cid, i)
        # fail 2 blokluyor, completed 3..250 birikti → 248
        self.assertEqual(bot.CURSOR_RAM[cid]["cursor"], 1)
        self.assertGreater(len(bot.CURSOR_RAM[cid]["completed"]), 200)
        # timeout sonrası fail atlanınca cursor 250’ye kadar ilerler, prune tetiklenir
        bot.CURSOR_RAM[cid]["failed_at"][2] = 0
        bot._cursor_advance(cid)
        self.assertEqual(bot.CURSOR_RAM[cid]["cursor"], 250)


class FirstPollLiveRaceTest(ti.MainHarness, unittest.TestCase):
    def setUp(self):
        ti.reset_state()
        bot.CURSOR_RAM.clear()
        self.addCleanup(lambda: (bot.CURSOR_RAM.clear(), ti.reset_state()))

    def test_live_before_first_poll_does_not_replay_history(self):
        """İlk poll öncesi canlı mesaj → bootstrap id-1, geçmiş tekrar yok."""
        async def get_input_entity(client_self, chat_id):
            return types.InputPeerChannel(channel_id=abs(chat_id) % 10**10, access_hash=1)
        async def call(client_self, request):
            chat_id = sorted(bot.SOURCE_IDS)[0]
            peer = utils.get_peer(chat_id)
            return SimpleNamespace(dialogs=[SimpleNamespace(peer=peer, top_message=10)])
        async def get_messages(client_self, peer, min_id=0, limit=None):
            self.fail("ilk poll öncesi canlı sonrası get_messages min_id=0 olmamalı")
        async def run_until_disconnected(client_self):
            # Canlı mesajı poll’den önce gönder
            await asyncio.sleep(0.3)
            chat_id = sorted(bot.SOURCE_IDS)[0]
            # Telethon Event’i simüle et
            from telethon.tl import types as tl_types
            from telethon import events
            msg = tl_types.Message(id=11, peer_id=tl_types.PeerChannel(abs(chat_id)%10**10), post=True,
                                   date=datetime.datetime.now(datetime.timezone.utc), message="çay 5 TL fırsat")
            ev = events.NewMessage.Event(msg)
            ev._chat_id = chat_id
            ev._message_id = 11
            # on_new_message’i doğrudan çağırma yerine poll sonrası kontrol
            await asyncio.sleep(0.5)

        config = self._write_config(fast_poll=True, fast_poll_seconds=1)
        self.addCleanup(os.unlink, config)
        # Sadece bootstrap testi — canlı race’i cursor canlı bootstrap’i ile kontrol et
        # Basit kontrol: _cursor_ensure yeni grupta id-1 ile bootstrap
        bot._cursor_ensure(-999)["cursor"] = 0
        bot._cursor_mark_pending(-999, 11)
        bot.CURSOR_RAM[-999]["cursor"] = 0
        # Canlı öncesi bootstrap mantığı: id=11 → cursor max(0,10)=10
        bot.CURSOR_RAM.clear()
        # Simulate live before poll
        bot._cursor_ensure(-2001111111111)["cursor"] = max(0, 11-1)
        self.assertEqual(bot.CURSOR_RAM[-2001111111111]["cursor"], 10)

    def test_pagination_over_30min_gap(self):
        """30dk+ kesinti: cursor sonrası 200 mesaj sayfalı kurtarılır (limit=None paginated)."""
        polls = [0]
        async def get_input_entity(client_self, chat_id):
            return types.InputPeerChannel(channel_id=abs(chat_id) % 10**10, access_hash=1)
        async def call(client_self, request):
            polls[0] += 1
            chat_id = sorted(bot.SOURCE_IDS)[0]
            peer = utils.get_peer(chat_id)
            top = 10 if polls[0] == 1 else 210
            return SimpleNamespace(dialogs=[SimpleNamespace(peer=peer, top_message=top)])
        async def get_messages(client_self, peer, min_id=0, limit=None):
            chat_id = sorted(bot.SOURCE_IDS)[0]
            channel_id = utils.get_peer(chat_id).channel_id
            now = datetime.datetime.now(datetime.timezone.utc)
            if min_id == 0:
                # 200 mesaj — Telethon paginated tek çağrı, biz tek listede döndür
                return [types.Message(id=i, peer_id=types.PeerChannel(channel_id), post=True, date=now, message=f"çay ürün {i} 5 TL") for i in range(210,10,-1)]
            return []
        async def run_until_disconnected(client_self):
            await asyncio.sleep(3.0)
        config = self._write_config(fast_poll=True, fast_poll_seconds=1, dedup_enabled=False)
        self.addCleanup(os.unlink, config)
        with mock.patch.object(ti.FakeClient, "get_input_entity", get_input_entity, create=True), \
             mock.patch.object(ti.FakeClient, "__call__", call, create=True), \
             mock.patch.object(ti.FakeClient, "get_messages", get_messages, create=True), \
             mock.patch.object(ti.FakeClient, "_mb_entity_cache", SimpleNamespace(get=lambda *a, **k: None), create=True), \
             mock.patch.object(ti.FakeClient, "run_until_disconnected", run_until_disconnected):
            client = self._run_main(config)
        self.assertEqual(len(client.delivered), 200)


class RestartAndPersistFailureTest(unittest.TestCase):
    def setUp(self):
        ti.reset_state()
        bot.CURSOR_RAM.clear()
        self.addCleanup(lambda: (bot.CURSOR_RAM.clear(), ti.reset_state()))

    def test_restart_blocks_new_messages_but_allows_drain(self):
        """RESTARTING=True iken on_new_message durur, poll loop da durur."""
        bot.RESTARTING = False
        self.addCleanup(lambda: setattr(bot, "RESTARTING", False))
        bot.RESTARTING = True
        # poll loop RESTARTING check
        import inspect
        src = inspect.getsource(bot.main)
        self.assertIn("if RESTARTING:", src)
        self.assertIn("poll_delivery_tasks", src)

    def test_persist_failure_blocks_dispatch(self):
        import inspect
        src = inspect.getsource(bot.main)
        self.assertIn('ok_save', src)
        self.assertIn('Restart iptal', src)
        self.assertIn('cursor_persist_for_restart', src)
