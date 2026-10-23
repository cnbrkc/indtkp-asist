"""Regression scenarios reconstructed from the 23 October delivery logs.

No Telegram access: real HTTP adapter and main handlers run with fake I/O.
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bot
import price_filter
import private_bot
import telegram_retry
import test_integration as ti


class ProductIdentityRegressionTest(unittest.TestCase):
    GENERIC = [
        "2 al, 1'ine ile (2 adet alımda)",
        "Anlık indirimleri kaçırmamak için bildirimlerinizi açabilirsiniz",
        "(3 Adet Alımda", "2 adet alımda", "8 adet alımda",
        "30g medyan: (fiyat medyana yakın)", "N11 İndirim Kuponu",
        "(Tekli 2 Adet Alımda", "1 adet kaldı.", "Durum: İkinci El: Yeni Gibi",
    ]

    def test_generic_fragments_can_never_be_dedup_keys(self):
        for fragment in self.GENERIC:
            with self.subTest(fragment=fragment):
                self.assertIsNone(bot.dedup_key(fragment))
                self.assertEqual(price_filter.extract_title(fragment), "")
                self.assertEqual(bot.smart_dedup_title(fragment), "")

    def test_product_beats_conditions_before_or_after_it(self):
        products = [
            "Old Spice Captain Erkek Deodorant 150 ml",
            "Luis Bien Diş Bakım Seti", "Kellogg's Coco Pops Mısır Gevreği 450 g",
            "SUNMAN Oyuncak Araba", "VODASEAL Tamir Bandı 50 mm",
            "Cricut EasyPress Mini Pres", "PROCSIN Cilt Bakım Seti",
            "Karaca Multichef Rose Gold Mutfak Şefi",
        ]
        for product in products:
            for fragment in self.GENERIC:
                for text in (f"{fragment}\n{product}\n159 TL", f"{product}\n159 TL\n{fragment}"):
                    with self.subTest(product=product, fragment=fragment):
                        self.assertEqual(bot.smart_dedup_title(text), product)

    def test_model_and_size_suffixes_are_not_truncated_for_dedup(self):
        prefix = "Old Spice " + "Erkek Deodorant Vücut Spreyi " * 8
        a = bot.smart_dedup_title(prefix + "Captain 150 ml\n159 TL")
        b = bot.smart_dedup_title(prefix + "Whitewater 250 ml\n159 TL")
        self.assertNotEqual(bot.dedup_key(a), bot.dedup_key(b))

    def test_punctuation_variants_merge_but_sizes_do_not(self):
        self.assertEqual(bot.dedup_key("De’Longhi Kahve Makinesi"), bot.dedup_key("Delonghi Kahve Makinesi"))
        self.assertNotEqual(bot.dedup_key("Old Spice 150 ml"), bot.dedup_key("Old Spice 250 ml"))

    def test_decimal_volumes_and_plus_models_are_different_products(self):
        for left, right in [
            ("Philips Airfryer 6.2 L", "Philips Airfryer 2.6 L"),
            ("Duru Sıvı Sabun 1,5 L", "Duru Sıvı Sabun 5,1 L"),
            ("Roborock S8", "Roborock S8+"),
        ]:
            with self.subTest(left=left, right=right):
                self.assertNotEqual(bot.dedup_key(left), bot.dedup_key(right))
        self.assertEqual(bot.dedup_key("Philips 6.2L"), bot.dedup_key("Philips 6,2 L"))

    def test_scheduled_monitor_does_not_cancel_an_active_run(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/telegram-monitor.yml").read_text()
        self.assertIn("cancel-in-progress: ${{ github.event_name == 'workflow_dispatch' }}", workflow)
        self.assertIn("group: telegram-monitor", workflow)

    def test_loreal_exclusions_normalize_both_sides(self):
        for term in ["l’oreal", "l'oreal", "l’oréal"]:
            for spelling in ["L'Oréal", "Loreal", "L’ORÉAL"]:
                with self.subTest(term=term, spelling=spelling):
                    self.assertFalse(bot.matches(spelling + " şampuan", [], [term], include_enabled=False))
                    self.assertTrue(bot.matches(spelling, [], [term], exclude_enabled=False))
        self.assertTrue(bot.matches("Kahve makinesi", [], ["l’oréal"]))

    def test_price_drop_and_purchase_conditions_are_distinct(self):
        terms = bot.offer_terms
        self.assertTrue(bot.better_or_different_offer(terms("Stanley IceFlow 1322 TL"), terms("Stanley IceFlow 1135 TL")))
        self.assertFalse(bot.better_or_different_offer(terms("Stanley IceFlow 1135 TL"), terms("Stanley IceFlow 1322 TL")))
        self.assertTrue(bot.better_or_different_offer(terms("Bingo 2000 ml 285 TL"), terms("Bingo 2000 ml 159 TL")))
        self.assertTrue(bot.better_or_different_offer(terms("Bingo 2000 ml 285 TL"), terms("Bingo 2000 ml\n3 adet alımda 159 TL")))
        a = terms("Bref WC\nFiyat: 97 TL / 2 adet alımda 48 TL")
        b = terms("Bref WC\nFiyat: 97 TL / 2 adet alımda 40 TL")
        self.assertEqual(a[0], 48)
        self.assertTrue(bot.better_or_different_offer(a, b))
        self.assertFalse(bot.better_or_different_offer(a, a))
        self.assertIsNone(terms("Ürün\nEski fiyat: 100 TL")[0])

    def test_history_id_namespaces_and_poll_backoff(self):
        self.assertFalse(bot.bot_history_ids_shared(-5569562901))
        self.assertFalse(bot.bot_history_ids_shared(1143378073))
        self.assertTrue(bot.bot_history_ids_shared(-1001234567890))
        interval = bot.next_poll_interval(3, 5)
        self.assertGreaterEqual(interval, 10)
        self.assertGreater(bot.next_poll_interval(interval, 5), interval)
        self.assertEqual(bot.next_poll_interval(300, 1000), 300)


class RateLimitRegressionTest(unittest.IsolatedAsyncioTestCase):
    async def test_long_and_repeated_429_retries_identical_media_request(self):
        responses = [
            {"ok": False, "error_code": 429, "parameters": {"retry_after": 1000}},
            {"ok": False, "error_code": 429, "parameters": {"retry_after": 16}},
            {"ok": True, "result": {"message_id": 36892}},
        ]
        requests = []

        def urlopen(request, **kwargs):
            requests.append((request.full_url, request.data))
            body = responses.pop(0)
            raw = json.dumps(body).encode()
            if not body["ok"]:
                raise urllib.error.HTTPError("redacted", 429, "Too Many Requests", {}, io.BytesIO(raw))
            return io.BytesIO(raw)

        with mock.patch.object(bot.urllib.request, "urlopen", side_effect=urlopen), \
             mock.patch.object(telegram_retry.asyncio, "sleep", new_callable=mock.AsyncMock) as sleep:
            result = await bot.send_bot_media("123:ABC", -5569562901, kind="photo",
                                              filename="deal.jpg", mime_type="image/jpeg", data=b"image")
        self.assertTrue(result.ok)
        self.assertEqual(result.message_id, 36892)
        self.assertEqual(len(requests), 3)
        self.assertTrue(all(request == requests[0] for request in requests))
        self.assertGreaterEqual(sleep.await_args_list[0].args[0], 1000)
        self.assertGreaterEqual(sleep.await_args_list[1].args[0], 16)

    async def test_notification_edit_and_private_reply_share_cooldown(self):
        api = private_bot.BotAPI("123:shared")
        gate = telegram_retry.gate_for(api.token)
        gate.until = telegram_retry.time.monotonic() + 30
        response = io.BytesIO(b'{"ok":true,"result":{"message_id":10}}')
        with mock.patch.object(telegram_retry.asyncio, "sleep", new_callable=mock.AsyncMock) as sleep, \
             mock.patch.object(api, "_request", return_value={"ok": True, "result": {}}), \
             mock.patch.object(bot.urllib.request, "urlopen", return_value=response):
            await api.call("sendMessage", {"chat_id": 1, "text": "hello"})
            await bot.edit_bot_text(api.token, -5569562901, 10, "updated")
        self.assertEqual(sleep.await_count, 2)
        self.assertTrue(all(call.args[0] >= 29 for call in sleep.await_args_list))

    async def test_extended_cooldown_is_rechecked_after_waking(self):
        now = [100.0]
        gate = telegram_retry.RetryGate(until=110)
        clock = mock.Mock(wraps=telegram_retry.time)
        clock.monotonic.side_effect = lambda: now[0]
        waits = []

        async def sleep(delay):
            waits.append(delay)
            now[0] += delay
            if len(waits) == 1:
                gate.until = 125  # concurrent long-poll 429 extended the deadline

        with mock.patch.object(telegram_retry, "time", clock), \
             mock.patch.object(telegram_retry.asyncio, "sleep", sleep):
            await gate.wait()
        self.assertEqual(waits, [10, 15])
        self.assertEqual(now[0], 125)

    async def test_invalid_retry_parameters_use_safe_finite_delay(self):
        for parameters in [None, "bad", {"retry_after": float("nan")},
                           {"retry_after": float("inf")}, {"retry_after": True}]:
            with self.subTest(parameters=parameters):
                gate = telegram_retry.RetryGate()
                gate.defer({"parameters": parameters})
                delay = gate.until - telegram_retry.time.monotonic()
                self.assertGreater(delay, 1)
                self.assertLessEqual(delay, 1.25)

    async def test_cancelled_cooldown_releases_lock(self):
        started = asyncio.Event()

        async def rejected():
            started.set()
            return {"ok": False, "error_code": 429, "parameters": {"retry_after": 1000}}

        task = asyncio.create_task(telegram_retry.request_with_retry("cancel", "sendMessage", rejected))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(telegram_retry.gate_for("cancel").lock.locked())

    async def test_get_updates_does_not_block_notifications(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def long_poll():
            started.set()
            await release.wait()
            return {"ok": True, "result": []}

        task = asyncio.create_task(telegram_retry.request_with_retry("poll", "getUpdates", long_poll))
        await started.wait()
        request = mock.AsyncMock(return_value={"ok": True})
        await asyncio.wait_for(telegram_retry.request_with_retry("poll", "sendMessage", request), 1)
        release.set()
        await task

    async def test_not_modified_is_success_without_error_log(self):
        exc = urllib.error.HTTPError("redacted", 400, "Bad Request", {}, io.BytesIO(
            b'{"description":"Bad Request: message is not modified"}'))
        with mock.patch.object(bot.urllib.request, "urlopen", side_effect=exc) as request, \
             mock.patch.object(bot.log, "error") as error:
            ok, _ = await bot.edit_bot_text("123:ABC", -1, 1, "unchanged")
        self.assertTrue(ok)
        self.assertEqual(request.call_count, 1)
        error.assert_not_called()

    async def test_ambiguous_transport_failure_is_not_replayed(self):
        with mock.patch.object(bot.urllib.request, "urlopen", side_effect=TimeoutError) as request:
            ok, _ = await bot.send_bot_ping("123:ABC", -1, "offer")
        self.assertFalse(ok)
        self.assertEqual(request.call_count, 1)


class DeliveryRegressionTest(unittest.TestCase):
    # Reuse the existing main() harness without inheriting/rerunning its tests.
    setUp = ti.DedupBotBadgeTest.setUp
    tearDown = ti.DedupBotBadgeTest.tearDown
    _patch = ti.DedupBotBadgeTest._patch
    _run = ti.DedupBotBadgeTest._run
    _send = ti.DedupBotBadgeTest._send

    def test_basic_group_history_never_uses_account_id_as_bot_id(self):
        ti.FakeClient.default_history = [
            (ti.GROUP_ID, "Çay\n\nÇay 5 TL\n\n🔗 Mesajı Gör: https://t.me/firsatz/9", 123, None, None, [], 83375),
        ]
        client = self._run(fast_notify=True)
        self.assertFalse(bot.DEDUP_CACHE)
        self._send(client, "Çay 5 TL", media=False)
        self.assertEqual(len(self.ping_calls), 1)
        self.assertFalse(self.edit_text_calls)

    def test_account_only_history_does_not_suppress_notification(self):
        ti.FakeClient.default_history = [
            (ti.GROUP_ID, "Çay 5 TL\n\n🔗 Mesajı Gör: https://t.me/firsatz/9", ti.ADMIN_ID, None, None, [], 83375),
        ]
        client = self._run(fast_notify=True)
        self.assertFalse(bot.DEDUP_CACHE)
        self._send(client, "Çay 5 TL", media=False)
        self.assertEqual(len(self.ping_calls), 1)

    def test_missing_bot_message_is_replaced_on_current_occurrence(self):
        client = self._run(fast_notify=True)
        self._send(client, "Çay 5 TL", media=False)
        with mock.patch.object(bot, "edit_bot_text", new=mock.AsyncMock(return_value=(False, "Bad Request: message to edit not found"))) as edit:
            self._send(client, "Çay 5 TL", media=False)
        self.assertEqual(edit.await_count, 1, "shorter badge cannot repair a missing message")
        self.assertEqual(len(self.ping_calls), 2)
        self.assertEqual(bot.STATS["deduped"], 0)
        self._send(client, "Çay 5 TL", media=False)
        self.assertEqual(len(self.ping_calls), 2)
        self.assertEqual(self.edit_text_calls[-1]["message_id"], self.ping_calls[-1]["message_id"])

    def test_failed_bot_fallback_is_not_a_successful_dedup_entry(self):
        client = self._run(fast_notify=True)
        with mock.patch.object(bot, "send_bot_ping", new=mock.AsyncMock(return_value=(False, "Forbidden"))):
            self._send(client, "Çay 5 TL", media=False)
        self.assertTrue(client.delivered, "account fallback remains available")
        self.assertFalse(bot.DEDUP_CACHE, "account copy is not a bot notification")
        self._send(client, "Çay 5 TL", media=False)
        self.assertEqual(len(self.ping_calls), 1)

    def test_price_drop_sends_new_notification_not_just_badge(self):
        client = self._run(fast_notify=True, include_keywords=[])
        self._send(client, "Stanley IceFlow Termos\n1322 TL", media=False)
        self._send(client, "Stanley IceFlow Termos\n1135 TL", media=False)
        self._send(client, "Stanley IceFlow Termos\n1135 TL", media=False)
        self.assertEqual(len(self.ping_calls), 2)
        self.assertEqual(len(self.edit_text_calls), 1)
        self.assertEqual(self.edit_text_calls[0]["message_id"], self.ping_calls[-1]["message_id"])

    def test_live_and_poll_same_id_are_claimed_before_delivery(self):
        client = self._run(fast_notify=True)

        async def scenario():
            handler = client.handlers[1][1]
            chat = next(iter(bot.SOURCE_IDS))
            live = ti.FakeEvent(chat, 7, "Çay 5 TL", message_id=127346, media=False)
            poll = ti.FakeEvent(chat, 7, "Çay 5 TL", message_id=127346, media=False)
            poll._fast_poll = True
            await asyncio.gather(handler(live), handler(poll), handler(poll), handler(live))

        asyncio.run(scenario())
        self.assertEqual(len(self.ping_calls), 1)
        self.assertEqual(bot.STATS["seen"], 1)
        self.assertEqual(bot.STATS["deduped"], 0)
        self.assertEqual(next(iter(bot.DEDUP_CACHE.values()))["count"], 1)
        self.assertFalse(bot.SOURCE_IN_FLIGHT)

    def test_active_send_is_not_stolen_during_long_cooldown(self):
        client = self._run(fast_notify=True)
        sent = mock.AsyncMock()

        async def scenario():
            started, release = asyncio.Event(), asyncio.Event()

            async def slow_send(*args, **kwargs):
                await sent()
                started.set()
                await release.wait()
                return bot.BotSendResult(True, "sent", 900)

            handler = client.handlers[1][1]
            chat = next(iter(bot.SOURCE_IDS))
            with mock.patch.object(bot, "send_bot_ping", slow_send), \
                 mock.patch.object(bot, "DEDUP_RESERVE_TIMEOUT", 0.001), \
                 mock.patch.object(bot, "DEDUP_RESERVE_TTL", 0):
                first = asyncio.create_task(handler(ti.FakeEvent(chat, 7, "Çay 5 TL", message_id=1, media=False)))
                await started.wait()
                second = asyncio.create_task(handler(ti.FakeEvent(chat, 7, "Çay 5 TL", message_id=2, media=False)))
                await asyncio.sleep(0.02)
                self.assertEqual(sent.await_count, 1)
                release.set()
                await asyncio.gather(first, second)

        asyncio.run(scenario())
        self.assertEqual(sent.await_count, 1)
        self.assertEqual(bot.STATS["deduped"], 1)

    def test_simultaneous_badges_are_coalesced(self):
        client = self._run(fast_notify=True)

        async def scenario():
            handler = client.handlers[1][1]
            chat = next(iter(bot.SOURCE_IDS))
            with mock.patch.object(bot, "DEDUP_EDIT_MIN_INTERVAL", 0.01):
                await handler(ti.FakeEvent(chat, 7, "Çay 5 TL", message_id=1, media=False))
                next(iter(bot.DEDUP_CACHE.values()))["last_edit"] = bot.time.time()
                await asyncio.gather(*(handler(ti.FakeEvent(chat, 7, "Çay 5 TL", message_id=i, media=False)) for i in range(2, 12)))

        asyncio.run(scenario())
        self.assertEqual(len(self.edit_text_calls), 1)
        self.assertIn("11 kere paylaşıldı: firsatz", self.edit_text_calls[0]["text"])
        self.assertEqual(next(iter(bot.DEDUP_CACHE.values()))["sources"], ["firsatz"])

    def test_onual_429_keeps_media_notification_instead_of_account_fallback(self):
        real_media = bot.send_bot_media
        client = self._run(fast_notify=True, include_keywords=[])
        responses = [
            {"ok": False, "error_code": 429, "parameters": {"retry_after": 16}},
            {"ok": True, "result": {"message_id": 36892}},
        ]
        methods = []

        def urlopen(request, **kwargs):
            methods.append(request.full_url.rsplit("/", 1)[-1])
            body = responses.pop(0)
            if not body["ok"]:
                raise urllib.error.HTTPError("redacted", 429, "Too Many Requests", {}, io.BytesIO(json.dumps(body).encode()))
            return io.BytesIO(json.dumps(body).encode())

        with mock.patch.object(bot, "send_bot_media", real_media), \
             mock.patch.object(bot.urllib.request, "urlopen", side_effect=urlopen), \
             mock.patch.object(telegram_retry.RetryGate, "wait", new_callable=mock.AsyncMock):
            self._send(client, "Karaca Multichef Rose Gold Mutfak Şefi\n2999 TL", message_id=127346)
        self.assertEqual(methods, ["sendPhoto", "sendPhoto"])
        self.assertFalse(self.ping_calls, "429 is not a reason to drop to text")
        self.assertFalse(client.delivered, "429 is not an account-only success")
        entry = next(iter(bot.DEDUP_CACHE.values()))
        self.assertEqual(entry["bot_message_id"], 36892)
        self.assertEqual(entry["editable"], "bot")

    def test_cancelled_delivery_releases_reservation_and_source_claim(self):
        client = self._run(fast_notify=True)

        async def scenario():
            started = asyncio.Event()

            async def waiting(*args, **kwargs):
                started.set()
                await asyncio.Event().wait()

            chat = next(iter(bot.SOURCE_IDS))
            with mock.patch.object(bot, "send_bot_ping", waiting):
                task = asyncio.create_task(client.handlers[1][1](ti.FakeEvent(chat, 7, "Çay 5 TL", media=False)))
                await started.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertFalse(bot.DEDUP_CACHE)
            self.assertNotIn((chat, 1), bot.LIVE_SEEN)
            self.assertFalse(bot.SOURCE_IN_FLIGHT)

        asyncio.run(scenario())


    def test_account_floodwait_retries_instead_of_dropping_offer(self):
        from telethon import errors

        client = self._run(fast_notify=False)
        original = client.send_message
        requests = []

        async def send(*args, **kwargs):
            requests.append((args, kwargs))
            if len(requests) == 1:
                raise errors.FloodWaitError(request=None, capture=80)
            return await original(*args, **kwargs)

        with mock.patch.object(client, "send_message", send), \
             mock.patch.object(bot.asyncio, "sleep", new_callable=mock.AsyncMock) as sleep:
            self._send(client, "Çay 5 TL", media=False)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0], requests[1])
        self.assertIn(mock.call(81), sleep.await_args_list)
        self.assertEqual(len(self.ping_calls), 1)
        self.assertEqual(bot.STATS["forwarded"], 1)
        self.assertEqual(bot.STATS["failed"], 0)

    def test_failed_cleanup_is_not_reported_as_deleted(self):
        client = self._run(fast_notify=False, dm_enabled=True, dm_keywords=["çay"])
        client.fail_modes.add("delete")
        with mock.patch.object(bot.PrivateOfferQueue, "submit", return_value=True) as submit:
            self._send(client, "Çay 5 TL", media=False)
            submit.assert_called_once()
        self.assertEqual(bot.STATS["cleaned"], 0)
        self.assertTrue(client.delivered)
        self.assertEqual(len(self.ping_calls), 1)
        # Bot delivery still wins; deletion failure must not cause a second send.
        self._send(client, "Çay 5 TL", media=False)
        self.assertEqual(len(self.ping_calls), 1)

    def test_alternating_conditions_do_not_repeat_notifications(self):
        client = self._run(fast_notify=True)
        for condition in ["2 adet alımda", "3 adet alımda", "2 adet alımda", "3 adet alımda"]:
            self._send(client, f"Çay\n{condition} 50 TL", media=False)
        self.assertEqual(len(self.ping_calls), 2)
        self.assertEqual(bot.STATS["deduped"], 2)
        self.assertEqual(len(bot.DEDUP_CACHE), 2)


    def test_supergroup_preload_restores_lower_price_notification(self):
        destination = -1001234567890
        ti.FakeClient.default_history = [
            (destination, "Stanley IceFlow\n\nStanley IceFlow 1135 TL\n\n🔗 Mesajı Gör: https://t.me/firsatz/2", 123, None, None, [], 200),
            (destination, "Stanley IceFlow\n\nStanley IceFlow 1322 TL\n\n🔗 Mesajı Gör: https://t.me/firsatz/1", 123, None, None, [], 100),
        ]
        client = self._run(destination=destination, fast_notify=True, include_keywords=[], smart_header=True)
        self._send(client, "Stanley IceFlow\n1135 TL", media=False)
        self.assertFalse(self.ping_calls)
        self.assertEqual(self.edit_text_calls[-1]["message_id"], 200)

    def test_another_bots_history_cannot_suppress_our_notification(self):
        destination = -1001234567890
        ti.FakeClient.default_history = [
            (destination, "Çay\n\nÇay 5 TL\n\n🔗 Mesajı Gör: https://t.me/firsatz/9", 999, None, None, [], 200),
        ]
        client = self._run(destination=destination, fast_notify=True)
        self._send(client, "Çay 5 TL", media=False)
        self.assertEqual(len(self.ping_calls), 1)
        self.assertFalse(self.edit_text_calls)
