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
        self.assertFalse(private.dm_matches("bilgi gölge", ["lg"]))
        self.assertFalse(private.dm_matches("TCL", []))
        self.assertTrue(private.dm_matches("55C805 kampanya", ["55c805"]))
        self.assertFalse(private.dm_matches("TCL", ["tcl tv"]))

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
                      {"private_control": "yes"}, {"dm_enabled": "no"}]:
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
        async def poll(*args):
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

    async def test_startup_note_announces_private_control(self):
        async def scenario():
            startup = [message for _, message in self.client.sent]
            self.assertTrue(any("Özel komutlar: bildirim botunun özel sohbetinden /start"
                                in message for message in startup))
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

    async def test_master_command_and_aliases_share_one_line_per_command_format(self):
        async def scenario():
            for command in ("/komutlar", "/help", "/yardim", "/yardım"):
                await self.command(command)
                self.assertEqual(self.api.calls[-1][1]["text"], bot.HELP_TEXT)
            for line in bot.HELP_TEXT.splitlines():
                self.assertRegex(line, r"^/\S+ - \S.*$")
            self.assertLess(len(bot.HELP_TEXT), 3500)
            for command in ("/dmfiltre", "/dmfiltreekle", "/dmfiltrecikar", "/open", "/close",
                            "/ekle", "/çıkar", "/kaydet", "/iptal", "/analiz", "/restart"):
                self.assertTrue(any(line.startswith(command + " - ") for line in bot.HELP_TEXT.splitlines()))
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
