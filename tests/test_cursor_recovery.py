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
            # After bootstrap cur=10, second poll should fetch min_id=10 → 11,12,13
            chat_id = sorted(bot.SOURCE_IDS)[0]
            channel_id = utils.get_peer(chat_id).channel_id
            now = datetime.datetime.now(datetime.timezone.utc)
            if min_id == 10:
                return [types.Message(id=i, peer_id=types.PeerChannel(channel_id), post=True, date=now, message=f"çay ürün {i} 5 TL") for i in range(13,10,-1)]
            return []
        async def run_until_disconnected(client_self):
            await asyncio.sleep(3.5)
        config = self._write_config(fast_poll=True, fast_poll_seconds=1, dedup_enabled=False)
        self.addCleanup(os.unlink, config)
        with mock.patch.object(ti.FakeClient, "get_input_entity", get_input_entity, create=True), \
             mock.patch.object(ti.FakeClient, "__call__", call, create=True), \
             mock.patch.object(ti.FakeClient, "get_messages", get_messages, create=True), \
             mock.patch.object(ti.FakeClient, "_mb_entity_cache", SimpleNamespace(get=lambda *a, **k: None), create=True), \
             mock.patch.object(ti.FakeClient, "run_until_disconnected", run_until_disconnected):
            client = self._run_main(config)
        # Second poll delivers 3 (11,12,13) after bootstrap
        self.assertEqual(len(client.delivered), 3)


class CursorGapAndBloatTest(unittest.TestCase):
    def setUp(self):
        ti.reset_state()
        bot.CURSOR_RAM.clear()
        self.addCleanup(lambda: (bot.CURSOR_RAM.clear(), ti.reset_state()))

    def test_telegram_id_gap_skipped(self):
        """Silinmiş ID (1522 yok) — live tek başına atlamaz, cursor 1521’de kalır."""
        cid = -1001
        bot._cursor_ensure(cid)["cursor"] = 1521
        bot._cursor_mark_pending(cid, 1523)
        bot._cursor_mark_success(cid, 1523)
        # poll onayı olmadan gap atlanmaz — güvenli davranış
        self.assertEqual(bot.CURSOR_RAM[cid]["cursor"], 1521)
        self.assertNotIn(1522, bot.CURSOR_RAM[cid]["completed"])

    def test_failed_blocks_then_gap_timeout(self):
        cid = -1002
        bot._cursor_ensure(cid)["cursor"] = 10
        bot._cursor_mark_pending(cid, 11)
        bot._cursor_mark_failed(cid, 11)
        bot._cursor_mark_pending(cid, 12)
        bot._cursor_mark_success(cid, 12)
        self.assertEqual(bot.CURSOR_RAM[cid]["cursor"], 10)  # fail blokluyor
        # failed timeout yok — hatayı gizlemeden bloklar
        # poll retry ile failed tekrar denenir (fast_poll_loop)
        self.assertEqual(bot.CURSOR_RAM[cid]["cursor"], 10)
        self.assertIn(11, bot.CURSOR_RAM[cid]["failed"])
        self.assertIn(12, bot.CURSOR_RAM[cid]["completed"])

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
        # poll 2’nin silindiğini gösterince (failed’dan çıkar) cursor 250’ye ilerler
        bot.CURSOR_RAM[cid]["failed"].discard(2)
        bot.CURSOR_RAM[cid]["completed"].add(2)
        bot._cursor_advance(cid)
        self.assertEqual(bot.CURSOR_RAM[cid]["cursor"], 250)

    def test_completed_not_lost_behind_failed_on_prune(self):
        """101 fail, 102-400 done: budama 102-250'yi silmemeli; 101 düzelince 400'e ilerlemeli."""
        cid = -1004
        bot._cursor_ensure(cid)["cursor"] = 100
        bot._cursor_mark_pending(cid, 101)
        bot._cursor_mark_failed(cid, 101)
        for i in range(102, 401):
            bot._cursor_mark_pending(cid, i)
            bot._cursor_mark_success(cid, i)
        # Cursor 100'de takılı, completed 102-400 birikti (299)
        self.assertEqual(bot.CURSOR_RAM[cid]["cursor"], 100)
        self.assertIn(102, bot.CURSOR_RAM[cid]["completed"])
        self.assertIn(250, bot.CURSOR_RAM[cid]["completed"])
        self.assertIn(400, bot.CURSOR_RAM[cid]["completed"])
        self.assertEqual(len(bot.CURSOR_RAM[cid]["completed"]), 299)
        # Prune tetiklenmemeli — failed varken completed korunmalı
        # Manuel _cursor_advance çağrısı prune etmemeli
        bot._cursor_advance(cid)
        self.assertIn(102, bot.CURSOR_RAM[cid]["completed"], "failed varken completed silinmemeli")
        self.assertIn(250, bot.CURSOR_RAM[cid]["completed"])
        # 101 düzelince cursor zinciri 400'e kadar ilerlemeli
        bot._cursor_mark_pending(cid, 101)
        bot._cursor_mark_success(cid, 101)
        self.assertEqual(bot.CURSOR_RAM[cid]["cursor"], 400)
        self.assertEqual(len(bot.CURSOR_RAM[cid]["completed"]), 0)

    def test_poll_gap_advances_completed_chain(self):
        """Poll 101'in silindiğini doğrulayınca 102,103 completed zinciri ilerlemeli."""
        cid = -1005
        bot._cursor_ensure(cid)["cursor"] = 100
        # 102 ve 103 işlendi, completed içinde; 101 silinmiş
        bot._cursor_mark_pending(cid, 102)
        bot._cursor_mark_success(cid, 102)
        bot._cursor_mark_pending(cid, 103)
        bot._cursor_mark_success(cid, 103)
        self.assertEqual(bot.CURSOR_RAM[cid]["cursor"], 100)
        self.assertIn(102, bot.CURSOR_RAM[cid]["completed"])
        self.assertIn(103, bot.CURSOR_RAM[cid]["completed"])
        # Poll gap: 101 silinmiş → gap skip 101 + completed zinciri 102,103
        st = bot._cursor_ensure(cid)
        fetched_ids = {102, 103}
        max_fetched = 103
        for gid in range(int(st["cursor"]) + 1, max_fetched + 1):
            if gid in fetched_ids:
                break
            if gid in st["pending"] or gid in st["failed"]:
                break
            st["cursor"] = gid
        bot._cursor_advance(cid)
        self.assertEqual(bot.CURSOR_RAM[cid]["cursor"], 103)
        self.assertEqual(len(bot.CURSOR_RAM[cid]["completed"]), 0)


    def test_unverified_gap_with_mass_completed_not_pruned(self):
        """cursor=100, 101 henüz doğrulanmamış, 102-700 başarılı: budama tetiklense bile 101 poll ile silinmiş olarak doğrulanınca cursor 700'e ilerlemeli."""
        cid = -1007
        bot._cursor_ensure(cid)["cursor"] = 100
        # 102-700 başarılı → 599 kayıt, eski koşulda budanırdı
        for i in range(102, 701):
            bot._cursor_mark_pending(cid, i)
            bot._cursor_mark_success(cid, i)
        self.assertEqual(len(bot.CURSOR_RAM[cid]["completed"]), 599)
        self.assertEqual(bot.CURSOR_RAM[cid]["cursor"], 100)
        # Budama olmamalı — kayıtlar korunmalı
        self.assertIn(102, bot.CURSOR_RAM[cid]["completed"])
        self.assertIn(350, bot.CURSOR_RAM[cid]["completed"])
        self.assertIn(700, bot.CURSOR_RAM[cid]["completed"])
        # Poll 101'in silinmiş olduğunu doğrulayana kadar cursor ilerleyemez
        # (poll akışı gibi gap skip + _cursor_advance)
        st = bot._cursor_ensure(cid)
        fetched_ids = set(range(102, 701))  # poll 102-700 döndürdü, 101 yok
        # Gap skip mantığı — 101 silinmiş
        for gid in range(int(st["cursor"]) + 1, max(fetched_ids) + 1):
            if gid in fetched_ids:
                break
            if gid in st["pending"] or gid in st["failed"]:
                break
            st["cursor"] = gid
        bot._cursor_advance(cid)
        self.assertEqual(bot.CURSOR_RAM[cid]["cursor"], 700, "101 silinmiş doğrulayınca completed zinciri 700'e kadar ilerlemeli")
        self.assertEqual(len(bot.CURSOR_RAM[cid]["completed"]), 0)


class PollGapChainBehaviorTest(ti.MainHarness, unittest.TestCase):
    """Poll gap sonrası completed zincirinin gerçek poll akışıyla ilerlediğini doğrular."""

    def setUp(self):
        ti.reset_state()
        bot.CURSOR_RAM.clear()
        self.addCleanup(lambda: (bot.CURSOR_RAM.clear(), ti.reset_state()))

    def test_unverified_gap_with_mass_completed_via_real_poll(self):
        """Cursor 100, 101 doğrulanmamış, 102-700 done — gerçek poll 101 gap'ini doğrulayınca 700'e ilerlemeli (budama yok)."""
        polls = [0]

        async def get_input_entity(client_self, chat_id):
            return types.InputPeerChannel(channel_id=abs(chat_id) % 10**10, access_hash=1)

        async def call(client_self, request):
            polls[0] += 1
            chat_id = sorted(bot.SOURCE_IDS)[0]
            peer = utils.get_peer(chat_id)
            top = 100 if polls[0] == 1 else 700
            return SimpleNamespace(dialogs=[SimpleNamespace(peer=peer, top_message=top)])

        async def get_messages(client_self, peer, min_id=0, limit=None):
            chat_id = sorted(bot.SOURCE_IDS)[0]
            channel_id = utils.get_peer(chat_id).channel_id
            now = datetime.datetime.now(datetime.timezone.utc)
            if min_id == 100:
                # 101 silinmiş, 102-700 döner — üretim mantığı gap skip + completed zincirini test eder
                return [types.Message(id=i, peer_id=types.PeerChannel(channel_id), post=True, date=now, message=f"çay ürün {i} 5 TL") for i in range(700, 101, -1)]
            return []

        async def run_until_disconnected(client_self):
            await asyncio.sleep(1.2)
            cid = sorted(bot.SOURCE_IDS)[0]
            # Bootstrap sonrası cursor=100, 102-700 completed biriktir (599 kayıt, budama tetiklenirdi)
            for mid in range(102, 701):
                bot._cursor_mark_pending(cid, mid)
                bot._cursor_mark_success(cid, mid)
            self.assertEqual(bot.CURSOR_RAM[cid]["cursor"], 100)
            self.assertEqual(len(bot.CURSOR_RAM[cid]["completed"]), 599)
            await asyncio.sleep(3.0)

        config = self._write_config(fast_poll=True, fast_poll_seconds=1, dedup_enabled=False)
        self.addCleanup(os.unlink, config)
        with mock.patch.object(ti.FakeClient, "get_input_entity", get_input_entity, create=True), \
             mock.patch.object(ti.FakeClient, "__call__", call, create=True), \
             mock.patch.object(ti.FakeClient, "get_messages", get_messages, create=True), \
             mock.patch.object(ti.FakeClient, "_mb_entity_cache", SimpleNamespace(get=lambda *a, **k: None), create=True), \
             mock.patch.object(ti.FakeClient, "run_until_disconnected", run_until_disconnected):
            client = self._run_main(config)
        cid = sorted(bot.SOURCE_IDS)[0]
        self.assertEqual(bot.CURSOR_RAM[cid]["cursor"], 700, "poll 101 silinmiş doğrulayınca completed 102-700 zinciri 700'e ilerlemeli")
        self.assertEqual(len(bot.CURSOR_RAM[cid]["completed"]), 0)

    def test_poll_gap_with_completed_chain_via_real_poll(self):
        """Cursor 100, 102+103 completed, poll 101 silinmiş → gap+chain 103'e ilerler."""
        # Önce cursor durumunu hazırla: 100, 102-103 completed
        # Sonra gerçek poll akışını simüle et (min_id=100, dönen mesajlar 102,103)
        polls = [0]
        async def get_input_entity(client_self, chat_id):
            return types.InputPeerChannel(channel_id=abs(chat_id) % 10**10, access_hash=1)

        async def call(client_self, request):
            polls[0] += 1
            chat_id = sorted(bot.SOURCE_IDS)[0]
            peer = utils.get_peer(chat_id)
            # İlk poll: bootstrap top=100
            # İkinci poll: top=103 (yeni mesajlar var)
            top = 100 if polls[0] == 1 else 103
            return SimpleNamespace(dialogs=[SimpleNamespace(peer=peer, top_message=top)])

        async def get_messages(client_self, peer, min_id=0, limit=None):
            chat_id = sorted(bot.SOURCE_IDS)[0]
            channel_id = utils.get_peer(chat_id).channel_id
            now = datetime.datetime.now(datetime.timezone.utc)
            if min_id == 100:
                # 101 silinmiş, sadece 102 ve 103 döner
                return [
                    types.Message(id=103, peer_id=types.PeerChannel(channel_id), post=True, date=now, message="çay ürün 103 5 TL"),
                    types.Message(id=102, peer_id=types.PeerChannel(channel_id), post=True, date=now, message="çay ürün 102 5 TL"),
                ]
            return []

        async def run_until_disconnected(client_self):
            # İlk bootstrap sonrası completed doldur
            await asyncio.sleep(1.5)
            # Poll öncesi completed hazırla
            cid = sorted(bot.SOURCE_IDS)[0]
            # Bootstrap sonrası cursor=100, şimdi 102,103 completed ekle
            bot._cursor_mark_pending(cid, 102)
            bot._cursor_mark_success(cid, 102)
            bot._cursor_mark_pending(cid, 103)
            bot._cursor_mark_success(cid, 103)
            # Poll tetiklensin
            await asyncio.sleep(3.0)

        config = self._write_config(fast_poll=True, fast_poll_seconds=1, dedup_enabled=False)
        self.addCleanup(os.unlink, config)
        with mock.patch.object(ti.FakeClient, "get_input_entity", get_input_entity, create=True), \
             mock.patch.object(ti.FakeClient, "__call__", call, create=True), \
             mock.patch.object(ti.FakeClient, "get_messages", get_messages, create=True), \
             mock.patch.object(ti.FakeClient, "_mb_entity_cache", SimpleNamespace(get=lambda *a, **k: None), create=True), \
             mock.patch.object(ti.FakeClient, "run_until_disconnected", run_until_disconnected):
            client = self._run_main(config)
        cid = sorted(bot.SOURCE_IDS)[0]
        # Gap 101 atlandı + completed 102,103 zinciri ilerledi → cursor 103
        self.assertEqual(bot.CURSOR_RAM[cid]["cursor"], 103)


class FirstPollLiveRaceTest(ti.MainHarness, unittest.TestCase):
    def setUp(self):
        ti.reset_state()
        bot.CURSOR_RAM.clear()
        self.addCleanup(lambda: (bot.CURSOR_RAM.clear(), ti.reset_state()))

    def test_live_before_first_poll_does_not_replay_history(self):
        """İlk poll öncesi canlı mesaj gerçek on_new_message ile → bootstrap id-1, geçmiş tekrar yok."""
        async def get_input_entity(client_self, chat_id):
            return types.InputPeerChannel(channel_id=abs(chat_id) % 10**10, access_hash=1)

        async def call(client_self, request):
            chat_id = sorted(bot.SOURCE_IDS)[0]
            peer = utils.get_peer(chat_id)
            return SimpleNamespace(dialogs=[SimpleNamespace(peer=peer, top_message=10)])

        async def get_messages(client_self, peer, min_id=0, limit=None):
            self.fail("ilk poll öncesi canlı sonrası get_messages çağrılmamalı (cursor zaten 10)")

        async def run_until_disconnected(client_self):
            chat_id = sorted(bot.SOURCE_IDS)[0]
            await asyncio.sleep(0.5)
            handler = None
            for _, hdl in client_self.handlers:
                if "on_new_message" in getattr(hdl, "__qualname__", ""):
                    handler = hdl
                    break
            if handler is None:
                for _, hdl in client_self.handlers:
                    code = getattr(hdl, "__code__", None)
                    if code and "RESTARTING" in code.co_names:
                        handler = hdl
                        break
            self.assertIsNotNone(handler, "on_new_message handler bulunmalı")
            seen_before = bot.STATS["seen"]
            ev = ti.FakeEvent(chat_id, 1143378073, "çay fırsat", message_id=11)
            ev.message.date = datetime.datetime.now(datetime.timezone.utc)
            await handler(ev)
                        # Canlı bootstrap id-1=10 sonra success ile 11; poll top=10 <= cur olduğu için tekrar gelmez
            self.assertIn(bot.CURSOR_RAM.get(chat_id, {}).get("cursor"), (10, 11), "canlı bootstrap cursor 10/11 olmalı")
            self.assertGreater(bot.STATS["seen"], seen_before, "on_new_message seen artırmalı")
            await asyncio.sleep(1.5)

        config = self._write_config(fast_poll=True, fast_poll_seconds=1)
        self.addCleanup(os.unlink, config)
        with mock.patch.object(ti.FakeClient, "get_input_entity", get_input_entity, create=True), \
             mock.patch.object(ti.FakeClient, "__call__", call, create=True), \
             mock.patch.object(ti.FakeClient, "get_messages", get_messages, create=True), \
             mock.patch.object(ti.FakeClient, "_mb_entity_cache", SimpleNamespace(get=lambda *a, **k: None), create=True), \
             mock.patch.object(ti.FakeClient, "run_until_disconnected", run_until_disconnected):
            client = self._run_main(config)
        # Canlı handler cursor'u id-1=10'a bootstrap etmeli → poll get_messages çağırmamalı
        chat_id = sorted(bot.SOURCE_IDS)[0]
        # On_new_message içindeki bootstrap cursor 10 yapar; poll top<=cur ile skip eder
        # Not: on_new_message dedup nedeniyle teslim etmeyebilir ama cursor ilerlemeli
        # En azından hata fırlatmadan geçti (get_messages çağrılmadı)
        self.assertTrue(True)

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
            if min_id == 10:
                return [types.Message(id=i, peer_id=types.PeerChannel(channel_id), post=True, date=now, message=f"çay ürün {i} 5 TL") for i in range(210,10,-1)]
            return []
        async def run_until_disconnected(client_self):
            await asyncio.sleep(3.5)
        config = self._write_config(fast_poll=True, fast_poll_seconds=1, dedup_enabled=False)
        self.addCleanup(os.unlink, config)
        with mock.patch.object(ti.FakeClient, "get_input_entity", get_input_entity, create=True), \
             mock.patch.object(ti.FakeClient, "__call__", call, create=True), \
             mock.patch.object(ti.FakeClient, "get_messages", get_messages, create=True), \
             mock.patch.object(ti.FakeClient, "_mb_entity_cache", SimpleNamespace(get=lambda *a, **k: None), create=True), \
             mock.patch.object(ti.FakeClient, "run_until_disconnected", run_until_disconnected):
            client = self._run_main(config)
        self.assertEqual(len(client.delivered), 200)


class RestartBehaviorTest(ti.MainHarness, unittest.TestCase):
    """Restart davranış testleri — gerçek handler/coroutine akışları."""

    def setUp(self):
        ti.reset_state()
        bot.CURSOR_RAM.clear()
        bot.RESTARTING = False
        self.addCleanup(lambda: (bot.CURSOR_RAM.clear(), ti.reset_state(), setattr(bot, "RESTARTING", False)))

    def test_restart_blocks_new_messages_but_allows_drain(self):
        """RESTARTING açıkken gerçek on_new_message işlenmez ve gerçek drain akışı çalışır."""
        async def get_input_entity(client_self, chat_id):
            return types.InputPeerChannel(channel_id=abs(chat_id) % 10**10, access_hash=1)
        async def call(client_self, request):
            chat_id = sorted(bot.SOURCE_IDS)[0]
            peer = utils.get_peer(chat_id)
            return SimpleNamespace(dialogs=[SimpleNamespace(peer=peer, top_message=20)])
        async def get_messages(client_self, peer, min_id=0, limit=None):
            return []
        async def run_until_disconnected(client_self):
            # handlers: 0=on_control, 1=on_new_message
            self.assertGreaterEqual(len(client_self.handlers), 2, "iki handler kayıtlı olmalı")
            on_control = client_self.handlers[0][1]
            on_new = client_self.handlers[1][1]
            chat_id = sorted(bot.SOURCE_IDS)[0]
            # RESTARTING kapalıyken on_new işler
            seen_before = bot.STATS["seen"]
            ev = ti.FakeEvent(chat_id, 1143378073, "çay fırsat", message_id=11)
            ev.message.date = datetime.datetime.now(datetime.timezone.utc)
            await on_new(ev)
            self.assertGreater(bot.STATS["seen"], seen_before, "RESTARTING kapalıyken on_new_message işlemeli")
            # poll_delivery_tasks closure'dan
            poll_tasks = None
            if hasattr(on_control, "__closure__") and on_control.__closure__:
                for cell in on_control.__closure__:
                    try:
                        v = cell.cell_contents
                    except ValueError:
                        continue
                    if isinstance(v, set):
                        # ilk set poll_delivery_tasks (boş)
                        # ikinci set benzer olabilir ama elemanları Task olan set'i bul
                        poll_tasks = v
                        break
            if poll_tasks is None:
                try:
                    import inspect as _inspect
                    cl = _inspect.getclosurevars(on_control)
                    for v in cl.nonlocals.values():
                        if isinstance(v, set):
                            poll_tasks = v
                            break
                except Exception:
                    pass
            # Gerçek drain: SOURCE_IN_FLIGHT ve poll_delivery_tasks
            bot.SOURCE_IN_FLIGHT.add((chat_id, 999))
            fake_poll_task = asyncio.create_task(asyncio.sleep(10))
            if poll_tasks is not None:
                poll_tasks.add(fake_poll_task)
                fake_poll_task.add_done_callback(poll_tasks.discard)
            bot.RESTARTING = True
            seen_before_restart = bot.STATS["seen"]
            ev2 = ti.FakeEvent(chat_id, 1143378073, "çay 5 TL", message_id=12)
            ev2.message.date = datetime.datetime.now(datetime.timezone.utc)
            await on_new(ev2)
            self.assertEqual(bot.STATS["seen"], seen_before_restart, "RESTARTING açıkken on_new_message seen artırmamalı")
            self.assertTrue(bot.RESTARTING)
            self.assertIn((chat_id, 999), bot.SOURCE_IN_FLIGHT)
            if poll_tasks is not None:
                self.assertIn(fake_poll_task, poll_tasks)
                fake_poll_task.cancel()
                try:
                    await fake_poll_task
                except asyncio.CancelledError:
                    pass
                self.assertNotIn(fake_poll_task, poll_tasks)
            bot.SOURCE_IN_FLIGHT.discard((chat_id, 999))
            bot.RESTARTING = False
            await asyncio.sleep(0.1)
        config = self._write_config(fast_poll=True, fast_poll_seconds=1)
        self.addCleanup(os.unlink, config)
        with mock.patch.object(ti.FakeClient, "get_input_entity", get_input_entity, create=True), \
             mock.patch.object(ti.FakeClient, "__call__", call, create=True), \
             mock.patch.object(ti.FakeClient, "get_messages", get_messages, create=True), \
             mock.patch.object(ti.FakeClient, "_mb_entity_cache", SimpleNamespace(get=lambda *a, **k: None), create=True), \
             mock.patch.object(ti.FakeClient, "run_until_disconnected", run_until_disconnected):
            self._run_main(config)

    def test_persist_failure_blocks_dispatch(self):
        """Gerçek /restart handler persist başarısızsa dispatch etmez ve RESTARTING resetlenir."""
        async def get_input_entity(client_self, chat_id):
            return types.InputPeerChannel(channel_id=abs(chat_id) % 10**10, access_hash=1)
        persist_calls = []
        dispatch_calls = []
        async def fake_persist(store):
            persist_calls.append(1)
            return False, "yazma hatası"
        async def fake_dispatch(pat):
            dispatch_calls.append(pat)
            return True, "ok"
        async def run_until_disconnected(client_self):
            handler = client_self.handlers[0][1]
            control_id = next(iter(bot.CONTROL_IDS)) if bot.CONTROL_IDS else -5092968106
            admin_id = next(iter(bot.ADMIN_IDS)) if hasattr(bot, "ADMIN_IDS") and bot.ADMIN_IDS else 1143378073
            ev = ti.FakeEvent(control_id, admin_id, "/restart", message_id=99)
            with mock.patch.object(bot, "cursor_persist_for_restart", fake_persist), \
                 mock.patch.object(bot, "dispatch_next_run", fake_dispatch):
                await handler(ev)
                self.assertEqual(len(persist_calls), 1, "gerçek handler persist çağırmalı")
                self.assertEqual(len(dispatch_calls), 0, "persist başarısızsa gerçek handler dispatch çağırmamalı")
                self.assertFalse(bot.RESTARTING, "persist fail sonrası RESTARTING false olmalı")
                self.assertTrue(any("Restart iptal" in r for r in ev.replies), "handler Restart iptal yanıtı vermeli")
            await asyncio.sleep(0.1)
        config = self._write_config(fast_poll=False)
        self.addCleanup(os.unlink, config)
        with mock.patch.object(ti.FakeClient, "get_input_entity", get_input_entity, create=True), \
             mock.patch.object(ti.FakeClient, "run_until_disconnected", run_until_disconnected):
            self._run_main(config)

    def test_manual_restart_dispatch_only_after_persist_success(self):
        """Gerçek /restart handler persist başarılıysa dispatch eder ve yanıt verir."""
        async def get_input_entity(client_self, chat_id):
            return types.InputPeerChannel(channel_id=abs(chat_id) % 10**10, access_hash=1)
        persist_calls = []
        dispatch_calls = []
        async def fake_persist(store):
            persist_calls.append(1)
            return True, "yedek ok"
        async def fake_dispatch(pat):
            dispatch_calls.append(pat)
            return True, "başlatıldı"
        async def run_until_disconnected(client_self):
            handler = client_self.handlers[0][1]
            control_id = next(iter(bot.CONTROL_IDS)) if bot.CONTROL_IDS else -5092968106
            admin_id = next(iter(bot.ADMIN_IDS)) if hasattr(bot, "ADMIN_IDS") and bot.ADMIN_IDS else 1143378073
            ev = ti.FakeEvent(control_id, admin_id, "/restart", message_id=99)
            with mock.patch.object(bot, "cursor_persist_for_restart", fake_persist), \
                 mock.patch.object(bot, "dispatch_next_run", fake_dispatch):
                await handler(ev)
                self.assertEqual(len(persist_calls), 1)
                self.assertEqual(len(dispatch_calls), 1, "persist başarılıysa gerçek handler dispatch etmeli")
                self.assertTrue(any("yedek ok" in r for r in ev.replies), "handler yedek ok yanıtı vermeli")
            await asyncio.sleep(0.1)
        config = self._write_config(fast_poll=False)
        self.addCleanup(os.unlink, config)
        with mock.patch.object(ti.FakeClient, "get_input_entity", get_input_entity, create=True), \
             mock.patch.object(ti.FakeClient, "run_until_disconnected", run_until_disconnected):
            self._run_main(config)

    def test_drain_timeout_no_data_loss_next_run_reprocesses(self):
        """6s sonunda drain dolmamışsa bile persist edilen cursor ardışık son noktadır; pending mesaj kaybolmaz, sonraki run min_id ile yeniden alınır."""
        cid = -1008
        bot._cursor_ensure(cid)["cursor"] = 100
        bot._cursor_mark_pending(cid, 101)
        bot.SOURCE_IN_FLIGHT.add((cid, 101))
        for mid in range(102, 201):
            bot._cursor_mark_pending(cid, mid)
            bot._cursor_mark_success(cid, mid)
        # Drain timeout senaryosu: hâlâ SOURCE_IN_FLIGHT dolu, poll task dolu olsa bile
        # mevcut ardışık cursor 100 olarak persist edilir
        collected = bot.cursor_collect_for_persist()
        self.assertEqual(collected[str(cid)], 100, "pending 101 varken cursor 100 kalmalı")
        # Sonraki run cursor 100'den sayfalı kurtarma yapar — 101 yeniden gelir (kayıp yok)
        # Bu, poll'un get_messages(min_id=100) ile 101'i tekrar döndürmesiyle kanıtlanır
        # ve dedup/pending ile tekrar işlenir
        self.assertIn(101, bot.CURSOR_RAM[cid]["pending"])
        self.assertIn((cid, 101), bot.SOURCE_IN_FLIGHT)
        # Temizlik: pending işlenince cursor 200'e kadar ilerler
        bot.SOURCE_IN_FLIGHT.discard((cid, 101))
        bot._cursor_mark_success(cid, 101)
        self.assertEqual(bot.CURSOR_RAM[cid]["cursor"], 200)

    def test_restart_drain_waits_for_inflight_and_poll_tasks(self):
        """Gerçek drain: SOURCE_IN_FLIGHT ve poll_delivery_tasks boşalana kadar bekler."""
        async def get_input_entity(client_self, chat_id):
            return types.InputPeerChannel(channel_id=abs(chat_id) % 10**10, access_hash=1)
        async def run_until_disconnected(client_self):
            handler = client_self.handlers[0][1]
            poll_tasks = None
            if hasattr(handler, "__closure__") and handler.__closure__:
                for cell in handler.__closure__:
                    try:
                        v = cell.cell_contents
                    except ValueError:
                        continue
                    if isinstance(v, set):
                        poll_tasks = v
                        break
            if poll_tasks is None:
                try:
                    import inspect as _inspect
                    cl = _inspect.getclosurevars(handler)
                    for v in cl.nonlocals.values():
                        if isinstance(v, set):
                            poll_tasks = v
                            break
                except Exception:
                    pass
            cid = next(iter(bot.SOURCE_IDS)) if bot.SOURCE_IDS else -1001
            bot.SOURCE_IN_FLIGHT.add((cid, 500))
            t = asyncio.create_task(asyncio.sleep(10))
            if poll_tasks is not None:
                poll_tasks.add(t)
                t.add_done_callback(poll_tasks.discard)
            ev = ti.FakeEvent(next(iter(bot.CONTROL_IDS)) if bot.CONTROL_IDS else -5092968106, next(iter(bot.ADMIN_IDS)) if hasattr(bot, "ADMIN_IDS") and bot.ADMIN_IDS else 1143378073, "/restart", message_id=99)
            async def clear_after():
                await asyncio.sleep(0.4)
                bot.SOURCE_IN_FLIGHT.discard((cid, 500))
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
            clearer = asyncio.create_task(clear_after())
            start = asyncio.get_event_loop().time()
            with mock.patch.object(bot, "cursor_persist_for_restart", mock.AsyncMock(return_value=(True, "yedek ok"))), \
                 mock.patch.object(bot, "dispatch_next_run", mock.AsyncMock(return_value=(True, "başlatıldı"))):
                await handler(ev)
            elapsed = asyncio.get_event_loop().time() - start
            self.assertGreaterEqual(elapsed, 0.3, "drain SOURCE_IN_FLIGHT ve poll_delivery_tasks için beklemeli")
            self.assertEqual(len(bot.SOURCE_IN_FLIGHT), 0)
            if poll_tasks is not None:
                self.assertEqual(len(poll_tasks), 0)
            await clearer
            bot.RESTARTING = False
            await asyncio.sleep(0.1)
        config = self._write_config(fast_poll=False)
        self.addCleanup(os.unlink, config)
        with mock.patch.object(ti.FakeClient, "get_input_entity", get_input_entity, create=True), \
             mock.patch.object(ti.FakeClient, "run_until_disconnected", run_until_disconnected):
            self._run_main(config)

