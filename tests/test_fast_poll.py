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

    def test_fast_poll_can_be_disabled(self):
        config = self._write_config(fast_poll=False)
        self.addCleanup(os.unlink, config)
        client = self._run_main(config)
        self.assertFalse(bot.FAST_POLL)
        self.assertEqual(client.delivered, [])


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
