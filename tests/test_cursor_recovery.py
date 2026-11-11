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
            # After bootstrap (10), second poll should fetch min_id=10 → 11,12,13
            chat_id = sorted(bot.SOURCE_IDS)[0]
            channel_id = utils.get_peer(chat_id).channel_id
            now = datetime.datetime.now(datetime.timezone.utc)
            if min_id == 10:
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
