"""Private commands/copying: no Telegram credentials or network required."""
import asyncio
import contextlib
import copy
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot
import private_bot as private
from test_integration import (
    MainHarness, FakeClient, FakeEvent, FakeMessage, BASE_ENV,
    ADMIN_ID, GROUP_ID, reset_state,
)


def dm_message(text, sender=ADMIN_ID, **extra):
    return {"chat": {"id": sender, "type": "private"},
            "from": {"id": sender}, "message_id": 500, "text": text, **extra}


def reset_poll_state():
    private.POLL_STATE.update({"status": "off", "bot": None, "last_error": ""})


class FakeAPI:
    def __init__(self):
        self.calls = []
        self.fail_offers = False
        self.webhook_url = ""

    async def call(self, method, payload):
        self.calls.append((method, copy.deepcopy(payload)))
        if method == "getMe":
            return {"id": 424242, "username": "takipci_bot", "is_bot": True}
        if method == "getWebhookInfo":
            return {"url": self.webhook_url}
        if self.fail_offers and private.PRIVATE_SUFFIX in (payload.get("text", "") + payload.get("caption", "")):
            raise private.BotAPIError("403")
        return {"message_id": len(self.calls) + 900}

    @property
    def offers(self):
        return [(m, p) for m, p in self.calls
                if private.PRIVATE_SUFFIX in (p.get("text", "") + p.get("caption", ""))]


class PrivatePureTests(unittest.TestCase):
    def test_keywords_normalized_unique_any_match(self):
        self.assertEqual(private.parse_dm_keywords("TCL, LG, İPHONE, tcl\nLG"), ["tcl", "lg", "iphone"])
        for text in ["TCL televizyon", "LG OLED", "İPHONE 16", "iPhone 16", "IPHONE 16"]:
            self.assertTrue(private.dm_matches(text, ["tcl", "lg", "iphone"]))
        # Literal substring semantics also apply to short single-word filters.
        self.assertTrue(private.dm_matches("bilgi gölge", ["lg"]))
        self.assertFalse(private.dm_matches("TCL", []))
        self.assertTrue(private.dm_matches("55C805 kampanya", ["55c805"]))
        self.assertFalse(private.dm_matches("TCL", ["tcl tv"]))

    def test_dm_phrase_is_contiguous_substring_and_exclusion_wins(self):
        for text in ["TCL 65T6D", "tcl 65c805 fırsat", "TCL  65T6D", "ＴＣＬ ６５T6D"]:
            with self.subTest(text=text):
                self.assertTrue(private.dm_matches(text, ["tcl 65"]))
                self.assertFalse(private.dm_matches(text, ["tcl 65"], ["tcl 65"]))
        for text in ["TCL televizyon 65", "65 TCL", "TCL-65", "TCL 55T6D", "TCL65"]:
            self.assertFalse(private.dm_matches(text, ["tcl 65"]), text)
        self.assertTrue(private.dm_matches("TCL 65T6D", ["tcl 65"], ["65 TCL"]))
        self.assertFalse(private.dm_matches("TCL 65T6D", [], ["other"]))
        self.assertFalse(private.dm_matches("anything", ["", "  ", None]))
        self.assertTrue(private.dm_matches("İPHONE 18PRO", ["iphone 18"], []))
        self.assertTrue(private.dm_matches("TCL 65T6D", ["TCL 65"], ["", None]))

    def test_dm_list_staging_limits_normalization_and_exact_removal(self):
        for field in bot.DM_LIST_FIELDS:
            config = {field: ["tcl 65", "iphone"]}
            ok, candidate, values, _ = bot.stage_telegram_list_change(
                config, field, "add", "TCL  65, IPHONE, ＬＧ ７５",
            )
            self.assertTrue(ok)
            self.assertEqual(values, ["lg 75"])
            self.assertEqual(candidate[field], ["tcl 65", "iphone", "lg 75"])
            ok, _, _, _ = bot.stage_telegram_list_change(config, field, "remove", "tcl")
            self.assertFalse(ok)  # removing an item still requires its full phrase
            ok, candidate, _, _ = bot.stage_telegram_list_change(config, field, "remove", "TCL 65")
            self.assertTrue(ok)
            self.assertEqual(candidate[field], ["iphone"])
            for raw in ["a" * 101, "ß" * 100, ",".join(f"w{i}" for i in range(51))]:
                self.assertFalse(bot.stage_telegram_list_change(config, field, "add", raw)[0])
            full = {field: [f"w{i}" for i in range(100)]}
            ok, candidate, _, _ = bot.stage_telegram_list_change(full, field, "add", "new")
            self.assertFalse(ok)
            self.assertEqual(candidate, full)

    def test_keyword_limits(self):
        for value in [", ,", "a" * 101, ",".join(str(i) for i in range(101))]:
            with self.assertRaises(ValueError):
                private.parse_dm_keywords(value)

    def test_only_authorized_private_humans(self):
        allowed = {ADMIN_ID}
        self.assertTrue(private.authorized_private_message(dm_message("/start"), allowed))
        for message in [None, {}, dm_message("/start", sender=1),
                        dm_message("/start", chat={"id": GROUP_ID, "type": "group"}),
                        dm_message("/start", chat={"id": 1, "type": "private"}),
                        dm_message("/start", **{"from": {"id": ADMIN_ID, "is_bot": True}})]:
            self.assertFalse(private.authorized_private_message(message, allowed))

    def test_pending_dialogues_do_not_collide_with_saved_messages(self):
        private_event = private.PrivateControlEvent(FakeAPI(), dm_message("/dmfiltreekle"))
        saved_event = FakeEvent(ADMIN_ID, ADMIN_ID, "/ekle")
        self.assertNotEqual(bot.pending_key(private_event), bot.pending_key(saved_event))

    def test_text_copy_preserves_entities_keyboard_and_original(self):
        info = {"kind": "text", "text": "🔥 TCL fırsat", "entities": [
            {"type": "bold", "offset": 3, "length": 3},
            {"type": "text_link", "offset": 7, "length": 6, "url": "https://example.com"}],
            "keyboard": {"inline_keyboard": [[{"text": "Git", "url": "https://example.com"}]]}}
        original = copy.deepcopy(info)
        method, payload = private.private_copy_request(ADMIN_ID, GROUP_ID, info)
        self.assertEqual(method, "sendMessage")
        self.assertEqual(payload["text"], info["text"] + private.PRIVATE_SUFFIX)
        self.assertEqual(payload["entities"], info["entities"])
        self.assertEqual(payload["reply_markup"], info["keyboard"])
        payload["entities"].clear()
        self.assertEqual(info, original)

    def test_media_copy_is_server_side_with_same_caption(self):
        info = {"kind": "media", "message_id": 42, "text": "TCL", "entities": []}
        method, payload = private.private_copy_request(ADMIN_ID, GROUP_ID, info)
        self.assertEqual(method, "copyMessage")
        self.assertEqual(payload["from_chat_id"], GROUP_ID)
        self.assertEqual(payload["message_id"], 42)
        self.assertEqual(payload["caption"], "TCL" + private.PRIVATE_SUFFIX)
        self.assertNotIn("photo", payload)

    def test_no_silent_truncation_or_missing_media_id(self):
        for info in [{"kind": "media", "text": "x" * 1024, "message_id": 1},
                     {"kind": "media", "text": "TCL"},
                     {"kind": "text", "text": "🔥" * 2048}]:
            with self.assertRaises(private.BotAPIError):
                private.private_copy_request(ADMIN_ID, GROUP_ID, info)

    def test_config_validates_new_fields(self):
        for extra in [{"dm_keywords": "tcl"}, {"dm_keywords": [1]}, {"dm_keywords": [""]},
                      {"private_control": "yes"}, {"dm_enabled": "no"},
                      {"dm_exclude_keywords": "tcl"}, {"dm_exclude_keywords": [None]},
                      {"dm_exclude_keywords": [""]}, {"dm_exclude_keywords": ["a" * 101]},
                      {"dm_exclude_keywords": ["a"] * 101}]:
            config = {"source_chats": ["@firsatz"], "destination": GROUP_ID, **extra}
            with mock.patch.dict(os.environ, BASE_ENV):
                self.assertTrue(bot.check_environment(config))

    def test_api_error_carries_code(self):
        self.assertEqual(private.BotAPIError("hata", code=403).code, 403)
        self.assertIsNone(private.BotAPIError("hata").code)

    def test_check_report_shows_notify_bot_token_without_leaking_it(self):
        config = {"source_chats": ["@firsatz"], "destination": GROUP_ID}
        out = io.StringIO()
        with mock.patch.dict(os.environ, {**BASE_ENV, "NOTIFY_BOT_TOKEN": "123:ABC"}):
            with contextlib.redirect_stdout(out):
                bot.print_report(config, [])
        self.assertIn("NOTIFY_BOT_TOKEN=var", out.getvalue())
        self.assertNotIn("123:ABC", out.getvalue())
        out = io.StringIO()
        with mock.patch.dict(os.environ, {**BASE_ENV, "NOTIFY_BOT_TOKEN": ""}):
            with contextlib.redirect_stdout(out):
                bot.print_report(config, [])
        self.assertIn("NOTIFY_BOT_TOKEN=YOK", out.getvalue())


class PrivateAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_retries_429_respecting_retry_after(self):
        api = private.BotAPI("never-log-this")
        with mock.patch.object(api, "_request", side_effect=[
            {"ok": False, "error_code": 429, "parameters": {"retry_after": 2}},
            {"ok": True, "result": {"message_id": 1}},
        ]), mock.patch.object(private.asyncio, "sleep", new_callable=mock.AsyncMock) as sleep:
            result = await api.call("sendMessage", {})
        self.assertEqual(result, {"message_id": 1})
        sleep.assert_awaited_once_with(2)

    async def test_no_retry_on_forbidden_or_excessive_wait(self):
        for code, seconds in [(403, 1), (429, 1000)]:
            api = private.BotAPI("secret")
            with mock.patch.object(api, "_request", return_value={
                "ok": False, "error_code": code, "parameters": {"retry_after": seconds},
                "description": "secret must not leak",
            }) as request:
                with self.assertRaises(private.BotAPIError) as caught:
                    await api.call("sendMessage", {})
                self.assertNotIn("secret", str(caught.exception))
                self.assertEqual(request.call_count, 1)

    async def test_poll_skips_backlog_and_unauthorized_messages(self):
        reset_poll_state()
        calls, received = [], []
        class API:
            async def call(self, method, payload):
                calls.append((method, payload))
                if method == "getMe":
                    return {"id": 424242, "username": "takipci_bot"}
                if method == "getWebhookInfo":
                    return {"url": ""}
                if payload.get("offset") == -1:
                    return [{"update_id": 40, "message": dm_message("/restart")}]
                if payload.get("offset") == 41:
                    return [{"update_id": 41, "message": dm_message("/start", sender=1)},
                            {"update_id": 42, "message": dm_message("/durum")},
                            {"update_id": 43, "message": dm_message("/start", chat={"id": GROUP_ID, "type": "group"})}]
                raise asyncio.CancelledError
        async def handle(event):
            received.append(event.raw_text)
        with self.assertRaises(asyncio.CancelledError):
            await private.poll_private_commands(API(), lambda: {ADMIN_ID}, handle)
        self.assertEqual(received, ["/durum"])
        self.assertEqual(calls[-1][1]["offset"], 44)
        health = private.poll_health()
        self.assertEqual(health["status"], "running")
        self.assertEqual(health["bot"], {"id": 424242, "username": "takipci_bot"})

    async def test_fresh_backlog_command_gets_resend_notice_but_is_not_run(self):
        reset_poll_state()
        calls = []
        class API:
            async def call(self, method, payload):
                calls.append((method, payload))
                if method == "getMe":
                    return {"id": 1, "username": "takipci_bot"}
                if method == "getWebhookInfo":
                    return {"url": ""}
                if payload.get("offset") == -1:
                    return [{"update_id": 40, "message": dm_message("/restart", date=1000)}]
                if method == "sendMessage":
                    return {"message_id": 1}
                raise asyncio.CancelledError
        handler = mock.AsyncMock()
        statuses = []
        async def on_status(status, detail):
            statuses.append((status, detail))
        with self.assertRaises(asyncio.CancelledError):
            await private.poll_private_commands(API(), lambda: {ADMIN_ID}, handler, on_status,
                                                clock=lambda: 1300)
        handler.assert_not_awaited()
        notices = [p for m, p in calls if m == "sendMessage"]
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["chat_id"], ADMIN_ID)
        self.assertIn("tekrar gönder", notices[0]["text"])
        self.assertEqual(statuses, [("running", "takipci_bot")])

    async def test_old_or_unauthorized_backlog_is_silent(self):
        for message in (dm_message("/restart", date=1000), dm_message("/durum", sender=1, date=9990)):
            reset_poll_state()
            calls = []
            class API:
                async def call(self, method, payload):
                    calls.append((method, payload))
                    if method == "getMe":
                        return {"id": 1, "username": "b"}
                    if method == "getWebhookInfo":
                        return {"url": ""}
                    if payload.get("offset") == -1:
                        return [{"update_id": 40, "message": message}]
                    raise asyncio.CancelledError
            with self.assertRaises(asyncio.CancelledError):
                await private.poll_private_commands(API(), lambda: {ADMIN_ID}, mock.AsyncMock(),
                                                    clock=lambda: 10000)
            self.assertFalse([m for m, _ in calls if m == "sendMessage"])

    async def test_persistent_conflict_is_reported_once_and_recovery_announced(self):
        reset_poll_state()
        class API:
            def __init__(self):
                self.polls = 0
            async def call(self, method, payload):
                if method == "getMe":
                    return {"id": 7, "username": "takipci_bot"}
                if method == "getWebhookInfo":
                    return {"url": ""}
                if payload.get("offset") == -1:
                    return []
                self.polls += 1
                if self.polls <= 4:
                    raise private.BotAPIError("Telegram API getUpdates: hata 409", code=409)
                if self.polls == 5:
                    return []
                raise asyncio.CancelledError
        statuses = []
        async def on_status(status, detail):
            statuses.append(status)
            if status == "conflict":
                self.assertEqual(private.poll_health()["status"], "conflict")
        with mock.patch.object(private.asyncio, "sleep", new_callable=mock.AsyncMock):
            with self.assertRaises(asyncio.CancelledError):
                await private.poll_private_commands(API(), lambda: {ADMIN_ID}, mock.AsyncMock(), on_status)
        self.assertEqual(statuses, ["running", "conflict", "recovered"])
        self.assertEqual(private.poll_health()["status"], "running")

    async def test_status_callback_errors_never_stop_poller(self):
        reset_poll_state()
        class API:
            async def call(self, method, payload):
                if method == "getMe":
                    return {"id": 7, "username": "x"}
                if method == "getWebhookInfo":
                    return {"url": ""}
                if payload.get("offset") == -1:
                    return []
                raise asyncio.CancelledError
        async def boom(status, detail):
            raise RuntimeError("callback broke")
        with self.assertRaises(asyncio.CancelledError):
            await private.poll_private_commands(API(), lambda: {ADMIN_ID}, mock.AsyncMock(), boom)
        self.assertEqual(private.poll_health()["status"], "running")

    async def test_existing_webhook_is_not_deleted(self):
        reset_poll_state()
        async def call(method, payload):
            if method == "getMe":
                return {"id": 1, "username": "bot"}
            if method == "getWebhookInfo":
                return {"url": "https://existing.invalid"}
            raise AssertionError(method)
        api = mock.Mock(call=mock.AsyncMock(side_effect=call))
        handler = mock.AsyncMock()
        await private.poll_private_commands(api, lambda: {ADMIN_ID}, handler)
        api.call.assert_any_await("getMe", {})
        api.call.assert_any_await("getWebhookInfo", {})
        handler.assert_not_awaited()
        self.assertEqual(private.poll_health()["status"], "webhook")

    async def test_poll_stops_on_unauthorized_token(self):
        reset_poll_state()
        attempts = []
        class API:
            async def call(self, method, payload):
                attempts.append(method)
                raise private.BotAPIError("Telegram API getMe: hata 401", code=401)
        handler = mock.AsyncMock()
        # 401'de sonsuza kadar yeniden denemez; poller geri döner.
        await private.poll_private_commands(API(), lambda: {ADMIN_ID}, handler)
        handler.assert_not_awaited()
        self.assertEqual(attempts, ["getMe"])
        self.assertEqual(private.poll_health()["status"], "unauthorized")

    async def test_poll_records_last_transport_error(self):
        reset_poll_state()
        class API:
            def __init__(self):
                self.polls = 0
            async def call(self, method, payload):
                if method == "getMe":
                    return {"id": 7, "username": "takipci_bot"}
                if method == "getWebhookInfo":
                    return {"url": ""}
                if payload.get("offset") == -1:
                    return []
                self.polls += 1
                if self.polls == 1:
                    raise private.BotAPIError("Telegram API getUpdates: hata 409", code=409)
                raise asyncio.CancelledError
        with mock.patch.object(private.asyncio, "sleep", new_callable=mock.AsyncMock):
            with self.assertRaises(asyncio.CancelledError):
                await private.poll_private_commands(API(), lambda: {ADMIN_ID}, mock.AsyncMock())
        health = private.poll_health()
        self.assertEqual(health["status"], "running")
        self.assertIn("409", health["last_error"])

    async def test_queue_is_bounded_and_failure_does_not_stop_worker(self):
        api = FakeAPI()
        api.fail_offers = True
        async def prepare(item):
            return "sendMessage", {"chat_id": ADMIN_ID, "text": item["text"] + private.PRIVATE_SUFFIX}
        queue = private.PrivateOfferQueue(api, prepare, maxsize=1)
        item = {"text": "TCL"}
        self.assertTrue(queue.submit(item))
        item["text"] = "changed"
        self.assertFalse(queue.submit(item))
        worker = asyncio.create_task(queue.run())
        try:
            await asyncio.wait_for(queue.queue.join(), 1)
            self.assertEqual(queue.failed, 1)
            self.assertFalse(worker.done())
            self.assertEqual(api.offers[0][1]["text"], "TCL" + private.PRIVATE_SUFFIX)
            self.assertEqual(queue.dropped, 1)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)


class PrivateIntegrationTests(MainHarness, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        reset_state()
        reset_poll_state()
        self.api = FakeAPI()
        self.queues = []
        self.group_text = []
        self.group_media = []
        self.persist_ok = True
        self.poll_status = "running"
        self.poll_on_status = None
        self.path = self._write_config(
            private_control=True, include_enabled=False, dm_enabled=False,
            dm_keywords=[], dedup_enabled=False, source_chats=["@firsatz"],
        )
        self.addCleanup(os.unlink, self.path)

    async def asyncTearDown(self):
        reset_state()

    async def run_scenario(self, scenario, **config):
        data = json.loads(Path(self.path).read_text())
        data.update(config)
        Path(self.path).write_text(json.dumps(data))
        self.client = FakeClient()
        real_queue = private.PrivateOfferQueue
        def queue_factory(*args, **kwargs):
            queue = real_queue(*args, **kwargs)
            self.queues.append(queue)
            return queue
        async def save(store, note=""):
            if self.persist_ok:
                bot.atomic_write_json(store.path, store.config)
            return self.persist_ok, "test persistence"
        async def ping(token, chat_id, text, **kwargs):
            self.group_text.append({"chat_id": chat_id, "text": text, **kwargs})
            return bot.BotSendResult(True, "ok", 700)
        async def media(token, chat_id, **kwargs):
            self.group_media.append({"chat_id": chat_id, **kwargs})
            return bot.BotSendResult(True, "ok", 701)
        async def poll(api, allowed, handler, on_status=None, **kwargs):
            # Gerçek poller gibi: hazır olduğunu bildirir, sonra bekler.
            self.poll_on_status = on_status
            private.POLL_STATE.update({"status": self.poll_status,
                                       "bot": {"id": 424242, "username": "takipci_bot"}})
            if on_status is not None:
                await on_status(self.poll_status, "takipci_bot")
            await asyncio.Event().wait()
        async def run():
            await scenario()
        with mock.patch.dict(os.environ, {**BASE_ENV, "NOTIFY_BOT_TOKEN": "test-token"}), \
             mock.patch.object(bot, "TelegramClient", return_value=self.client), \
             mock.patch.object(bot, "StringSession", return_value=object()), \
             mock.patch.object(bot, "BotAPI", return_value=self.api), \
             mock.patch.object(bot, "PrivateOfferQueue", side_effect=queue_factory), \
             mock.patch.object(bot, "save_config", side_effect=save), \
             mock.patch.object(bot, "send_bot_ping", side_effect=ping), \
             mock.patch.object(bot, "send_bot_media", side_effect=media), \
             mock.patch.object(bot, "poll_private_commands", side_effect=poll), \
             mock.patch.object(self.client, "run_until_disconnected", side_effect=run):
            await bot.main(["--config", self.path])

    async def command(self, text, sender=ADMIN_ID):
        event = private.PrivateControlEvent(self.api, dm_message(text, sender))
        await self.client.handlers[0][1](event)

    async def offer(self, text, **kwargs):
        event = FakeEvent(next(iter(bot.SOURCE_IDS)), 7, text, **kwargs)
        await self.client.handlers[1][1](event)
        await asyncio.wait_for(self.queues[0].queue.join(), 3)

    def replies(self):
        return "\n".join(p.get("text", "") for _, p in self.api.calls)

    async def test_stage_one_private_commands_group_unchanged(self):
        async def scenario():
            event = FakeEvent(GROUP_ID, ADMIN_ID, "/close harici")
            await self.client.handlers[0][1](event)
            self.assertFalse(event.replies)
            await self.command("/start")
            await self.command("/durum")
            self.assertIn("Bot özel sohbeti", self.replies())
            await self.command("/close harici")
            saved = json.loads(Path(self.path).read_text())
            self.assertFalse(saved["exclude_enabled"])
            self.assertEqual(saved["destination"], GROUP_ID)
            await self.offer("TCL fırsatı", media=False)
            self.assertEqual(self.group_text[-1]["chat_id"], GROUP_ID)
            self.assertFalse(self.api.offers)
            # Private replies never run the account's cleanup against this DM.
            self.assertFalse(any(chat == ADMIN_ID for chat, *_ in self.client.deleted))
        await self.run_scenario(scenario)

    async def test_existing_multistep_settings_work_in_private(self):
        async def scenario():
            await self.command("/ekle")
            await self.command("1")
            await self.command("kahve")
            await self.command("/kaydet")
            saved = json.loads(Path(self.path).read_text())
            self.assertIn("kahve", saved["include_keywords"])
            self.assertEqual(saved["destination"], GROUP_ID)
        await self.run_scenario(scenario)

    async def test_saved_messages_fallback_and_unauthorized_private(self):
        async def scenario():
            fallback = FakeEvent(ADMIN_ID, ADMIN_ID, "/durum")
            await self.client.handlers[0][1](fallback)
            self.assertTrue(fallback.replies)
            await self.command("/dmfiltreekle tcl", sender=77)
            self.assertEqual(json.loads(Path(self.path).read_text())["dm_keywords"], [])
        await self.run_scenario(scenario)

    async def test_dm_dialogue_immediate_save_exact_text_and_filters(self):
        async def scenario():
            await self.command("/dmfiltreekle")
            await self.command("TCL, LG, iPhone")
            saved = json.loads(Path(self.path).read_text())
            self.assertTrue(saved["dm_enabled"])
            self.assertEqual(saved["dm_keywords"], ["tcl", "lg", "iphone"])
            self.assertEqual(saved["include_keywords"], ["çay"])
            await self.offer("TCL televizyon https://example.com", media=False)
            self.assertEqual(len(self.api.offers), 1)
            method, payload = self.api.offers[0]
            self.assertEqual(method, "sendMessage")
            self.assertEqual(payload["text"], self.group_text[0]["text"] + private.PRIVATE_SUFFIX)
            self.assertEqual(payload["entities"], self.group_text[0]["entities"])
            self.assertEqual(payload["reply_markup"], self.group_text[0]["keyboard"])
            self.assertEqual(payload["chat_id"], ADMIN_ID)
            await self.offer("kahve", media=False)
            await self.offer("TCL çekiliş", media=False)
            self.assertEqual(len(self.api.offers), 1)
            self.assertEqual(len(self.group_text), 2)
        await self.run_scenario(scenario)

    async def test_media_and_keyboard_match_group(self):
        async def scenario():
            await self.command("/dmfiltreekle tcl")
            await self.offer("TCL televizyon")
            method, payload = self.api.offers[0]
            self.assertEqual(method, "copyMessage")
            self.assertEqual(payload["message_id"], 701)
            self.assertEqual(payload["from_chat_id"], GROUP_ID)
            self.assertEqual(payload["caption"], self.group_media[0]["caption"] + private.PRIVATE_SUFFIX)
            self.assertEqual(payload["caption_entities"], self.group_media[0]["entities"])
            self.assertEqual(payload["reply_markup"], self.group_media[0]["keyboard"])
        await self.run_scenario(scenario)

    async def test_cancel_disable_and_failed_save(self):
        async def scenario():
            await self.command("/dmfiltreekle")
            await self.command("/iptal")
            await self.command("tcl")
            self.assertEqual(json.loads(Path(self.path).read_text())["dm_keywords"], [])
            self.persist_ok = False
            await self.command("/dmfiltreekle tcl")
            await self.offer("TCL", media=False)
            self.assertFalse(self.api.offers)
            self.persist_ok = True
            await self.command("/dmfiltreekle tcl")
            await self.command("/dmkapat")
            await self.offer("TCL", media=False)
            self.assertFalse(self.api.offers)
            await self.command("/dmac")
            await self.offer("TCL", media=False)
            self.assertEqual(len(self.api.offers), 1)
        await self.run_scenario(scenario)

    async def test_group_dedup_does_not_create_extra_dm(self):
        async def scenario():
            await self.command("/dmfiltreekle tcl")
            await self.offer("TCL fırsat", media=False)
            await self.offer("TCL fırsat", media=False, message_id=2)
            self.assertEqual(len(self.api.offers), 1)
            self.assertEqual(bot.STATS["deduped"], 1)
        with mock.patch.object(bot, "edit_bot_text", new_callable=mock.AsyncMock, return_value=(True, "ok")):
            await self.run_scenario(scenario, dedup_enabled=True, dedup_scan_limit=0)

    async def test_dm_failure_never_removes_or_blocks_group(self):
        async def scenario():
            self.api.fail_offers = True
            await self.command("/dmfiltreekle tcl")
            await self.offer("TCL", media=False)
            self.assertEqual(self.queues[0].failed, 1)
            self.assertEqual(bot.STATS["forwarded"], 1)
            self.assertEqual(bot.STATS["failed"], 0)
            await self.offer("LG", media=False)
            self.assertEqual(bot.STATS["forwarded"], 2)
        await self.run_scenario(scenario)

    async def test_group_account_fallback_is_copied_without_deleting_group(self):
        async def scenario():
            await self.command("/dmfiltreekle tcl")
            group_message = FakeMessage(101, media=False, text="TCL fallback")
            with mock.patch.object(bot, "send_bot_ping", new_callable=mock.AsyncMock, return_value=(False, "error")), \
                 mock.patch.object(self.client, "get_messages", new_callable=mock.AsyncMock,
                                   return_value=group_message, create=True):
                await self.offer("TCL fallback", media=False)
            self.assertEqual(self.api.offers[0][1]["text"], "TCL fallback" + private.PRIVATE_SUFFIX)
            self.assertFalse(self.client.deleted)
        await self.run_scenario(scenario, single_message=True)

    async def test_pending_group_draft_is_not_overwritten_by_dm_filter(self):
        async def scenario():
            await self.command("/ekle")
            await self.command("/dmfiltreekle tcl")
            self.assertIn("bekleyen işlemi", self.replies())
            await self.command("1")
            await self.command("kahve")
            await self.command("/kaydet")
            self.assertIn("kahve", json.loads(Path(self.path).read_text())["include_keywords"])
            self.assertEqual(json.loads(Path(self.path).read_text())["dm_keywords"], [])
        await self.run_scenario(scenario)

    async def test_private_test_command_keeps_group_destination(self):
        async def scenario():
            await self.command("/test")
            self.assertEqual(self.group_text[-1]["chat_id"], GROUP_ID)
            self.assertTrue(any(chat == GROUP_ID for chat, _ in self.client.sent))
            self.assertIn("Deneme mesajı gönderildi", self.replies())
            await self.command("/id")
            self.assertIn("destination olarak yazmana gerek yok", self.replies())
        await self.run_scenario(scenario)

    async def test_private_test_reports_channel_health(self):
        async def scenario():
            private.POLL_STATE.update({"status": "running",
                                       "bot": {"id": 424242, "username": "takipci_bot"},
                                       "last_error": ""})
            await self.command("/test")
            self.assertIn("Özel komut kanalı", self.replies())
            self.assertIn("@takipci_bot", self.replies())
            self.assertIn("Webhook: yok", self.replies())
            self.assertIn("Poller: çalışıyor", self.replies())
        await self.run_scenario(scenario)

    async def test_private_test_warns_about_webhook(self):
        async def scenario():
            self.api.webhook_url = "https://ornek.invalid/hook"
            await self.command("/test")
            self.assertIn("Webhook: VAR", self.replies())
        await self.run_scenario(scenario)

    async def test_private_test_reports_missing_token(self):
        async def scenario():
            with mock.patch.object(bot, "NOTIFY_BOT_TOKEN", ""):
                await self.command("/test")
            self.assertIn("NOTIFY_BOT_TOKEN tanımlı değil", self.replies())
        await self.run_scenario(scenario)

    async def test_startup_is_silent_even_with_legacy_notify_flag(self):
        async def scenario():
            self.assertEqual(self.client.sent, [])
            self.assertFalse([p for m, p in self.api.calls if m == "sendMessage"])
            await self.command("/durum")
            self.assertIn("Dinleme: AKTİF", self.replies())
            self.assertEqual(self.client.sent, [])  # /durum does not ping the group
            with mock.patch.object(self.client, "is_connected", return_value=False):
                await self.command("/durum")
            self.assertIn("Dinleme: BAĞLANTI YOK", self.replies())
        await self.run_scenario(scenario, notify_on_start=True)

    async def test_startup_note_reports_broken_private_channel(self):
        self.poll_status = "webhook"
        async def scenario():
            startup = [message for _, message in self.client.sent]
            self.assertTrue(any("Özel komutlar ÇALIŞMIYOR" in message and "webhook" in message
                                for message in startup), startup)
            self.assertFalse([p for m, p in self.api.calls if m == "sendMessage"])
        await self.run_scenario(scenario, notify_on_start=True)

    async def test_real_private_channel_faults_still_warn_group(self):
        async def scenario():
            before = len(self.client.sent)
            await self.poll_on_status("conflict", "3x 409")
            await self.poll_on_status("unauthorized", "401")
            await self.poll_on_status("recovered", "")
            texts = [message for _, message in self.client.sent[before:]]
            self.assertEqual(len(texts), 3)
            self.assertIn("409", texts[0])
            self.assertIn("401", texts[1])
            self.assertIn("toparlandı", texts[2])
            # Arıza uyarıları hesaptan gider; rutin açılış DM'i yoktur.
            self.assertFalse([p for m, p in self.api.calls if m == "sendMessage"])
        await self.run_scenario(scenario, notify_on_start=True)

    async def test_disabling_cancels_not_yet_sent_queue_items(self):
        async def scenario():
            await self.command("/dmfiltreekle tcl")
            # Queue a copy before the worker gets scheduled, then disable DM.
            event = FakeEvent(next(iter(bot.SOURCE_IDS)), 7, "TCL fırsat", media=False)
            await self.client.handlers[1][1](event)
            await self.command("/dmkapat")
            await asyncio.wait_for(self.queues[0].queue.join(), 2)
            self.assertFalse(self.api.offers)
            self.assertEqual(bot.STATS["forwarded"], 1)
        await self.run_scenario(scenario)

    async def test_authorized_other_admin_cannot_change_owners_dm(self):
        async def scenario():
            await self.command("/durum", sender=88)
            self.assertIn("Takipçi aktif", self.replies())
            await self.command("/dmfiltreekle tcl", sender=88)
            self.assertIn("yalnızca hesap sahibinin", self.replies())
            self.assertEqual(json.loads(Path(self.path).read_text())["dm_keywords"], [])
        await self.run_scenario(scenario, admin_user_id=[ADMIN_ID, 88])

    async def test_persisted_dm_config_works_after_restart_without_new_command(self):
        async def scenario():
            await self.offer("TCL yeni ilan", media=False)
            self.assertEqual(len(self.api.offers), 1)
            self.assertEqual(self.api.offers[0][1]["chat_id"], ADMIN_ID)
        await self.run_scenario(scenario, dm_keywords=["tcl"], dm_enabled=True)

    async def test_dmfiltre_is_read_only_and_never_prompts_for_keywords(self):
        async def scenario():
            before = Path(self.path).read_text()
            await self.command("/dmfiltre")
            self.assertIn("Kelimeler: tcl, lg", self.replies())
            self.assertIn("Kişisel bildirim: KAPALI", self.replies())
            self.assertFalse(bot.PENDING)
            self.assertEqual(Path(self.path).read_text(), before)
            await self.command("/dmfiltre iphone")
            await self.command("iphone")
            self.assertIn("yalnızca bilgi gösterir", self.replies())
            self.assertEqual(Path(self.path).read_text(), before)
        await self.run_scenario(scenario, dm_keywords=["tcl", "lg"], dm_enabled=False)

    async def test_add_is_incremental_and_duplicate_does_not_enable_paused_filter(self):
        async def scenario():
            await self.command("/dmfiltreekle TCL, lg, iphone, LG")
            saved = json.loads(Path(self.path).read_text())
            self.assertEqual(saved["dm_keywords"], ["tcl", "lg", "iphone"])
            self.assertTrue(saved["dm_enabled"])
            await self.command("/dmkapat")
            await self.command("/dmfiltreekle TCL")
            saved = json.loads(Path(self.path).read_text())
            self.assertFalse(saved["dm_enabled"])
            self.assertEqual(saved["dm_keywords"], ["tcl", "lg", "iphone"])
        await self.run_scenario(scenario, dm_keywords=["tcl"], dm_enabled=False)

    async def test_remove_dialogue_preserves_others_and_group_settings(self):
        async def scenario():
            await self.command("/dmfiltrecikar")
            self.assertIn("Çıkarılacak kelimeleri", self.replies())
            await self.command("LG, iphone")
            saved = json.loads(Path(self.path).read_text())
            self.assertEqual(saved["dm_keywords"], ["tcl"])
            self.assertTrue(saved["dm_enabled"])
            self.assertEqual(saved["include_keywords"], ["çay"])
            self.assertEqual(saved["exclude_keywords"], ["çekiliş"])
            self.assertEqual(saved["destination"], GROUP_ID)
            await self.command("/dmfiltrecikar TCL")
            saved = json.loads(Path(self.path).read_text())
            self.assertEqual(saved["dm_keywords"], [])
            self.assertFalse(saved["dm_enabled"])
            await self.command("/dmfiltrecikar")
            self.assertFalse(bot.PENDING)
        await self.run_scenario(scenario, dm_keywords=["tcl", "lg", "iphone"], dm_enabled=True)

    async def test_remove_keeps_filter_paused_and_rejects_unknown_atomically(self):
        async def scenario():
            await self.command("/dmfiltrecikar TCL, samsung")
            self.assertEqual(json.loads(Path(self.path).read_text())["dm_keywords"], ["tcl", "lg"])
            self.assertIn("Hiçbir kayıt çıkarılmadı", self.replies())
            await self.command("/dmfiltrecikar lg")
            saved = json.loads(Path(self.path).read_text())
            self.assertEqual(saved["dm_keywords"], ["tcl"])
            self.assertFalse(saved["dm_enabled"])
        await self.run_scenario(scenario, dm_keywords=["tcl", "lg"], dm_enabled=False)

    async def test_remove_cancel_and_failed_save_preserve_list(self):
        async def scenario():
            await self.command("/dmfiltrecikar")
            await self.command("/iptal")
            await self.command("lg")
            self.assertEqual(json.loads(Path(self.path).read_text())["dm_keywords"], ["tcl", "lg"])
            await self.command("/dmfiltrecikar")
            self.persist_ok = False
            await self.command("lg")
            self.assertEqual(json.loads(Path(self.path).read_text())["dm_keywords"], ["tcl", "lg"])
            self.assertTrue(bot.PENDING)
            self.persist_ok = True
            await self.command("lg")
            self.assertEqual(json.loads(Path(self.path).read_text())["dm_keywords"], ["tcl"])
            self.assertFalse(bot.PENDING)
        await self.run_scenario(scenario, dm_keywords=["tcl", "lg"], dm_enabled=True)

    async def test_dmfiltre_and_komutlar_preserve_pending_removal(self):
        async def scenario():
            await self.command("/dmfiltrecikar")
            await self.command("/dmfiltre")
            await self.command("/komutlar")
            await self.command("lg")
            self.assertEqual(json.loads(Path(self.path).read_text())["dm_keywords"], ["tcl"])
        await self.run_scenario(scenario, dm_keywords=["tcl", "lg"])

    async def edit_list(self, action, field, value, *, save=True):
        await self.command("/ekle" if action == "add" else "/çıkar")
        await self.command(field)
        await self.command(value)
        if save:
            await self.command("/kaydet")

    async def test_dm_lists_share_staged_menu_and_preserve_existing_settings(self):
        async def scenario():
            before = json.loads(Path(self.path).read_text())
            await self.command("/ekle")
            self.assertIn("4. 🎯  dmdahil", self.replies())
            self.assertIn("5. 🔕  dmharic", self.replies())
            await self.command("4")
            self.assertIn("01. iphone", self.replies())
            await self.command("TCL 65; LG 75, tcl 65\nİPHONE")
            self.assertEqual(json.loads(Path(self.path).read_text()), before)
            self.assertIn("DEĞİŞİKLİK TASLAĞI", self.replies())
            await self.command("/dmfiltre")
            self.assertTrue(bot.PENDING)  # status does not discard the draft
            await self.command("/kaydet")
            saved = json.loads(Path(self.path).read_text())
            self.assertEqual(saved["dm_keywords"], ["iphone", "tcl 65", "lg 75"])
            self.assertTrue(saved["dm_enabled"])
            await self.edit_list("add", "5", "TCL 65T6D, yenilenmiş")
            saved = json.loads(Path(self.path).read_text())
            self.assertEqual(saved["dm_exclude_keywords"], ["tcl 65t6d", "yenilenmiş"])
            for field in before.keys() - {"dm_keywords", "dm_enabled"}:
                self.assertEqual(saved[field], before[field], field)
            await self.command("/dmfiltre")
            self.assertIn("DM hariç: tcl 65t6d, yenilenmiş", self.replies())
        await self.run_scenario(scenario, dm_keywords=["iphone"])

    async def test_model_prefix_matches_as_one_block_for_text_and_media(self):
        async def scenario():
            await self.offer("TCL televizyon 65T6D fırsat", media=False)
            await self.offer("65T6D TCL fırsat", media=False)
            self.assertFalse(self.api.offers)
            await self.offer("TCL 65T6D fırsat", media=False)
            await self.offer("TCL 65C805 fırsat", media=True)
            self.assertEqual(len(self.api.offers), 2)
            self.assertEqual(self.api.offers[0][0], "sendMessage")
            self.assertEqual(self.api.offers[1][0], "copyMessage")
            self.assertEqual(bot.STATS["forwarded"], 4)
        await self.run_scenario(scenario, dm_keywords=["tcl 65"], dm_enabled=True)

    async def test_dm_exclusion_wins_without_affecting_group_delivery(self):
        async def scenario():
            await self.offer("TCL 65T6D fırsat", media=False)
            self.assertEqual(bot.STATS["forwarded"], 1)
            self.assertEqual(self.queues[0].queue.qsize(), 0)
            self.assertFalse(self.api.offers)
            await self.offer("TCL 65C805 fırsat", media=False)
            self.assertEqual(len(self.api.offers), 1)
            self.assertEqual(bot.STATS["forwarded"], 2)
        await self.run_scenario(scenario, dm_keywords=["tcl 65"], dm_enabled=True,
                                dm_exclude_keywords=["tcl 65t6"])

    async def test_new_exclusion_cancels_queued_copy_and_removal_allows_future_copy(self):
        async def scenario():
            event = FakeEvent(next(iter(bot.SOURCE_IDS)), 7, "TCL 65T6D fırsat", media=False)
            await self.client.handlers[1][1](event)
            self.assertEqual(self.queues[0].queue.qsize(), 1)
            await self.edit_list("add", "dmharic", "tcl 65t6")
            await asyncio.wait_for(self.queues[0].queue.join(), 2)
            self.assertFalse(self.api.offers)
            self.assertEqual(bot.STATS["forwarded"], 1)
            await self.edit_list("remove", "dmharic", "1")
            self.assertEqual(json.loads(Path(self.path).read_text())["dm_exclude_keywords"], [])
            await self.offer("TCL 65T6D yeni fırsat", media=False)
            self.assertEqual(len(self.api.offers), 1)
        await self.run_scenario(scenario, dm_keywords=["tcl 65"], dm_enabled=True)

    async def test_last_include_removal_disables_dm_and_keeps_exclusions(self):
        async def scenario():
            await self.edit_list("remove", "dmdahil", "1, LG 75", save=False)
            self.assertEqual(json.loads(Path(self.path).read_text())["dm_keywords"], ["tcl 65", "lg 75"])
            await self.command("/kaydet")
            saved = json.loads(Path(self.path).read_text())
            self.assertEqual(saved["dm_keywords"], [])
            self.assertFalse(saved["dm_enabled"])
            self.assertEqual(saved["dm_exclude_keywords"], ["yenilenmiş"])
            await self.command("/dmac")
            self.assertFalse(json.loads(Path(self.path).read_text())["dm_enabled"])
            await self.offer("TCL 65T6D", media=False)
            self.assertFalse(self.api.offers)
        await self.run_scenario(scenario, dm_keywords=["tcl 65", "lg 75"], dm_enabled=True,
                                dm_exclude_keywords=["yenilenmiş"])

    async def test_exclusion_edits_and_include_removal_do_not_enable_paused_dm(self):
        async def scenario():
            await self.edit_list("add", "dmharic", "yenilenmiş")
            await self.edit_list("remove", "dmharic", "1")
            await self.edit_list("remove", "dmdahil", "LG")
            saved = json.loads(Path(self.path).read_text())
            self.assertFalse(saved["dm_enabled"])
            self.assertEqual(saved["dm_keywords"], ["tcl"])
            await self.edit_list("add", "dmdahil", "TCL", save=False)
            self.assertFalse(json.loads(Path(self.path).read_text())["dm_enabled"])
            self.assertIn("Eklenecek yeni kayıt yok", self.replies())
        await self.run_scenario(scenario, dm_keywords=["tcl", "lg"], dm_enabled=False)

    async def test_dm_draft_cancel_save_failure_and_retry(self):
        async def scenario():
            before = Path(self.path).read_text()
            await self.edit_list("add", "dmdahil", "tcl 65", save=False)
            await self.command("/iptal")
            self.assertEqual(Path(self.path).read_text(), before)
            await self.edit_list("add", "dmdahil", "tcl 65", save=False)
            self.persist_ok = False
            await self.command("/kaydet")
            self.assertEqual(Path(self.path).read_text(), before)
            self.assertTrue(bot.PENDING)
            await self.offer("TCL 65T6D", media=False)
            self.assertFalse(self.api.offers)  # failed save rolled back runtime too
            self.persist_ok = True
            await self.command("/kaydet")
            self.assertFalse(bot.PENDING)
            await self.offer("TCL 65T6D", media=False)
            self.assertEqual(len(self.api.offers), 1)
        await self.run_scenario(scenario)

    async def test_exclusion_save_failure_does_not_block_offers(self):
        async def scenario():
            await self.edit_list("add", "dmharic", "tcl 65", save=False)
            self.persist_ok = False
            await self.command("/kaydet")
            await self.offer("TCL 65T6D", media=False)
            self.assertEqual(len(self.api.offers), 1)
            self.assertNotIn("dm_exclude_keywords", json.loads(Path(self.path).read_text()))
        await self.run_scenario(scenario, dm_keywords=["tcl 65"], dm_enabled=True)

    async def test_other_admin_and_saved_messages_cannot_edit_dm_lists(self):
        async def scenario():
            before = Path(self.path).read_text()
            for field in ("dmdahil", "dmharic"):
                await self.command("/ekle", sender=88)
                await self.command(field, sender=88)
                self.assertIn("yalnızca hesap sahibinin", self.api.calls[-1][1]["text"])
                await self.command("/iptal", sender=88)
                for text in ("/ekle", field):
                    event = FakeEvent(ADMIN_ID, ADMIN_ID, text)
                    await self.client.handlers[0][1](event)
                self.assertIn("yalnızca hesap sahibinin", event.replies[-1])
                await self.client.handlers[0][1](FakeEvent(ADMIN_ID, ADMIN_ID, "/iptal"))
            self.assertEqual(Path(self.path).read_text(), before)
        await self.run_scenario(scenario, admin_user_id=[ADMIN_ID, 88])

    async def test_stale_dm_draft_does_not_overwrite_current_list(self):
        async def scenario():
            await self.edit_list("add", "dmharic", "yenilenmiş", save=False)
            # Simulate a base/list conflict at save time.
            pending = next(iter(bot.PENDING.values()))
            pending["base_config"]["dm_exclude_keywords"] = ["old"]
            await self.command("/kaydet")
            self.assertIn("başka bir değişiklik", self.replies())
            self.assertEqual(json.loads(Path(self.path).read_text())["dm_exclude_keywords"], ["tcl"])
            self.assertFalse(bot.PENDING)
        await self.run_scenario(scenario, dm_exclude_keywords=["tcl"])

    async def test_auto_restart_dispatches_without_any_telegram_message(self):
        client = mock.Mock(send_message=mock.AsyncMock())
        with mock.patch.object(bot.asyncio, "sleep", new_callable=mock.AsyncMock) as sleep, \
             mock.patch.object(bot, "dispatch_next_run", new_callable=mock.AsyncMock) as dispatch:
            await bot.auto_restart_scheduler(client, GROUP_ID, "test-pat", 3600)
        sleep.assert_awaited_once_with(3600)
        dispatch.assert_awaited_once_with("test-pat")
        client.send_message.assert_not_awaited()

    async def test_master_command_lists_all_canonical_commands(self):
        async def scenario():
            await self.command("/komutlar")
            self.assertEqual(self.api.calls[-1][1]["text"], bot.HELP_TEXT)
            for line in bot.HELP_TEXT.splitlines():
                self.assertRegex(line, r"^/\S+ - \S.*$")
            self.assertLess(len(bot.HELP_TEXT), 3500)
            commands = [line.split(" - ", 1)[0] for line in bot.HELP_TEXT.splitlines()]
            self.assertEqual(commands, [name for name, _ in bot.COMMAND_DESCRIPTIONS])
            self.assertIn("/kaynaktest", commands)
            self.assertIn("/start", commands)
        await self.run_scenario(scenario)

    async def test_dmdurum_removed_from_dispatch_and_help(self):
        async def scenario():
            await self.command("/dmdurum")
            self.assertIn("Bilinmeyen komut: /dmdurum", self.replies())
            self.assertNotIn("/dmdurum", bot.HELP_TEXT)
            self.assertFalse(bot.PENDING)
        await self.run_scenario(scenario)


class DMEditPureTests(unittest.TestCase):
    def test_limit_applies_to_combined_list(self):
        existing = [f"word{i}" for i in range(100)]
        with self.assertRaises(ValueError):
            private.edit_dm_keywords(existing, "new", "add")
        self.assertEqual(len(existing), 100)
        self.assertEqual(private.edit_dm_keywords(existing, "word1", "add"), existing)

    def test_remove_is_exact_phrase_not_substring(self):
        existing = ["tcl tv", "lg"]
        with self.assertRaises(ValueError):
            private.edit_dm_keywords(existing, "tcl", "remove")
        self.assertEqual(private.edit_dm_keywords(existing, "TCL TV", "remove"), ["lg"])
        self.assertEqual(existing, ["tcl tv", "lg"])

    def test_invalid_action_rejected(self):
        with self.assertRaises(ValueError):
            private.edit_dm_keywords(["tcl"], "lg", "replace")
