"""bot.main()'in açılış akışını sahte bir Telethon istemcisiyle uçtan uca dener.

Bu test, gerçek hatanın (control_chat string verildiğinde tüm update akışının
ölmesi) bir daha geri gelmemesini garanti eder.

Çalıştırma:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bot  # noqa: E402
from telethon.tl import types  # noqa: E402

ADMIN_ID = 1143378073
GROUP_ID = -5092968106
CHANNEL_ID = -1001111111111


def make_user():
    return types.User(id=ADMIN_ID, first_name="Ben")


def make_group():
    return types.Chat(id=abs(GROUP_ID), title="Benim Grup", photo=types.ChatPhotoEmpty(),
                      participants_count=1, date=0, version=0)


def make_channel(name="FırsatZ"):
    return types.Channel(id=abs(CHANNEL_ID), title=name, photo=types.ChatPhotoEmpty(), date=0)


class FakeMessage:
    """Gerçek Telethon Message'ın yerine geçer; str OLMAMASI önemli,
    aksi halde send_message(copy) senaryosu yanlışlıkla başarılı sayılır."""

    def __init__(self, message_id, media=True):
        self.id = message_id
        self.media = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=1)) if media else None
        self.file = None
        self.video = None

    def __str__(self):
        return f"<mesaj:{self.id}>"


class FakeEvent:
    def __init__(self, chat_id, sender_id, text, message_id=1, media=True):
        self.chat_id = chat_id
        self.sender_id = sender_id
        self.raw_text = text
        self.id = message_id
        self.message = FakeMessage(message_id, media)
        self.replies: list[str] = []

    async def reply(self, text):
        self.replies.append(text)

    async def get_chat(self):
        return make_group() if self.chat_id == GROUP_ID else make_channel("kaynak")


class FakeClient:
    """Telethon yerine geçen, çağrıları kaydeden sahte istemci."""

    def __init__(self, *args, **kwargs):
        self.handlers = []
        self.sent = []
        self.forwarded = []
        self.files = []
        self.unresolved = []
        self.fail_modes = set()   # test senaryosu için kapatılacak yollar

    def on(self, builder):
        def decorator(callback):
            self.handlers.append((builder, callback))
            return callback
        return decorator

    async def connect(self):
        return True

    async def is_user_authorized(self):
        return True

    async def get_me(self):
        return make_user()

    async def get_entity(self, value):
        if value == "me" or value == ADMIN_ID:
            return make_user()
        if value == GROUP_ID or value == str(GROUP_ID):
            return make_group()
        if value == "@olmayan":
            self.unresolved.append(value)
            raise ValueError(f'No user has "{value}" as username')
        if isinstance(value, str) and value.startswith("@"):
            return make_channel(value.lstrip("@"))
        if isinstance(value, int) and value < 0:
            return types.Chat(id=abs(value), title=f"chat{value}", photo=types.ChatPhotoEmpty(),
                              participants_count=1, date=0, version=0)
        raise ValueError(f"Cannot find any entity corresponding to {value!r}")

    async def send_message(self, entity, message, **kwargs):
        if "copy" in self.fail_modes and not isinstance(message, str):
            raise self._protected_error("copy")
        self.sent.append((entity, message))

    async def forward_messages(self, entity, message, from_peer=None):
        if "forward" in self.fail_modes:
            raise self._protected_error("forward")
        self.forwarded.append((entity, message, from_peer))

    async def download_media(self, message, file=None):
        if "download" in self.fail_modes:
            raise ValueError("medya indirilemedi (koruma)")
        return b"\xff\xd8sahte-jpeg-verisi"

    async def send_file(self, entity, file, caption=None, **kwargs):
        if "media" in self.fail_modes:
            raise self._protected_error("media")
        self.files.append((entity, file, caption))

    def _protected_error(self, what):
        """Korumalı kanalda Telegram'in verdiği gerçek hata."""
        from telethon import errors
        return errors.ChatForwardsRestrictedError(request=None)

    async def run_until_disconnected(self):
        return None


BASE_ENV = {
    "API_ID": "123456",
    "API_HASH": "a" * 32,
    "SESSION_STRING": "1BVtsOKAB...",
    "GH_PAT": "",
    "RESTART_AFTER_MINUTES": "330",
}


def reset_state():
    bot.SOURCES.clear()
    bot.SOURCE_FAILURES.clear()
    bot.CONTROL_NAMES.clear()
    bot.SOURCE_IDS.clear()
    bot.CONTROL_IDS.clear()
    bot.DESTINATION_ID = None
    bot.STATS.update({"seen": 0, "matched": 0, "forwarded": 0, "failed": 0,
                      "commands": 0, "modes": {}, "last_match": None,
                      "last_match_source": None})


class IntegrationTest(unittest.TestCase):
    def setUp(self):
        reset_state()
        self.config_path = self._write_config()
        self.client = self._run_main(self.config_path)

    def tearDown(self):
        os.unlink(self.config_path)
        reset_state()

    def _write_config(self, **overrides) -> str:
        config = {
            "source_chats": ["@firsatz", "@olmayan"],
            "destination": GROUP_ID,
            "include_keywords": ["çay"],
            "exclude_keywords": ["çekiliş"],
            "match_mode": "any",
            "copy_mode": "copy",
            "control_chat": GROUP_ID,
            "admin_user_id": ADMIN_ID,
            "auto_restart": False,
            "notify_on_start": False,
        }
        config.update(overrides)
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(config, handle)
        handle.close()
        return handle.name

    def _run_main(self, config_path: str) -> FakeClient:
        created = []

        def factory(*args, **kwargs):
            client = FakeClient(*args, **kwargs)
            created.append(client)
            return client

        with mock.patch.dict(os.environ, BASE_ENV, clear=False), \
             mock.patch.object(bot, "TelegramClient", factory), \
             mock.patch.object(bot, "StringSession", lambda *a, **k: object()):
            asyncio.run(bot.main(["--config", config_path]))
        self.assertTrue(created, "TelegramClient hiç oluşturulmadı")
        return created[0]

    def _call(self, handler, event):
        asyncio.run(handler(event))
        return event

    # --- açılış -------------------------------------------------------------

    def test_unresolvable_source_does_not_kill_the_rest(self):
        self.assertEqual(len(bot.SOURCE_IDS), 1, bot.SOURCES)
        self.assertEqual([value for value, _ in bot.SOURCE_FAILURES], ["@olmayan"])

    def test_control_group_is_resolved_to_int(self):
        self.assertIn(GROUP_ID, bot.CONTROL_IDS)
        self.assertIn(ADMIN_ID, bot.CONTROL_IDS, "Kayıtlı Mesajlar her zaman kontrol edilebilmeli")

    def test_handlers_are_registered_without_chats_filter(self):
        """chats= filtresi Telethon tarafından ilk mesajda çözülür ve hata verirse tüm
        update akışını öldürür; bu yüzden hiç kullanılmamalı."""
        self.assertEqual(len(self.client.handlers), 2)
        for builder, _ in self.client.handlers:
            self.assertIsNone(builder.chats)

    def test_startup_does_not_crash_on_string_ids(self):
        """Eski hata: control_chat/destination string verilince ValueError."""
        reset_state()
        path = self._write_config(control_chat=str(GROUP_ID), destination=str(GROUP_ID))
        self.addCleanup(os.unlink, path)
        client = self._run_main(path)
        self.assertIn(GROUP_ID, bot.CONTROL_IDS)
        self.assertEqual(client.sent, [], "notify_on_start=False iken mesaj gitmemeli")

    # --- komutlar -----------------------------------------------------------

    def test_status_command_answers_admin_in_group(self):
        event = self._call(self.client.handlers[0][1], FakeEvent(GROUP_ID, ADMIN_ID, "/status"))
        self.assertTrue(event.replies, "/status yanıt üretmedi")
        self.assertIn("Takipçi aktif", event.replies[0])

    def test_turkish_status_alias_works(self):
        event = self._call(self.client.handlers[0][1], FakeEvent(GROUP_ID, ADMIN_ID, "/durum"))
        self.assertIn("Takipçi aktif", event.replies[0])

    def test_help_command_works(self):
        event = self._call(self.client.handlers[0][1], FakeEvent(GROUP_ID, ADMIN_ID, "/yardim"))
        self.assertIn("Komutlar", event.replies[0])

    def test_source_command_lists_failures(self):
        event = self._call(self.client.handlers[0][1], FakeEvent(GROUP_ID, ADMIN_ID, "/kaynak"))
        self.assertIn("çözülemedi", event.replies[0])

    def test_command_from_stranger_is_ignored(self):
        event = self._call(self.client.handlers[0][1], FakeEvent(GROUP_ID, 424242, "/status"))
        self.assertEqual(event.replies, [])

    def test_command_from_unrelated_chat_is_ignored(self):
        event = self._call(self.client.handlers[0][1], FakeEvent(-1009999999999, ADMIN_ID, "/status"))
        self.assertEqual(event.replies, [])

    def test_saved_messages_command_works(self):
        event = self._call(self.client.handlers[0][1], FakeEvent(ADMIN_ID, ADMIN_ID, "/durum"))
        self.assertTrue(event.replies)

    def test_test_command_sends_to_destination(self):
        event = self._call(self.client.handlers[0][1], FakeEvent(GROUP_ID, ADMIN_ID, "/test"))
        self.assertTrue(any("Deneme mesajı" in str(m) for _, m in self.client.sent))
        self.assertIn("✅", event.replies[0])

    def test_plain_text_is_not_a_command(self):
        event = self._call(self.client.handlers[0][1], FakeEvent(GROUP_ID, ADMIN_ID, "selam"))
        self.assertEqual(event.replies, [])

    # --- mesaj dinleme ------------------------------------------------------

    def test_matching_message_is_delivered_to_group(self):
        source_id = next(iter(bot.SOURCE_IDS))
        self._call(self.client.handlers[1][1], FakeEvent(source_id, 999, "Sıcak ÇAY 5 TL"))
        self.assertEqual(bot.STATS["matched"], 1)
        self.assertEqual(len(self.client.sent), 1)
        self.assertEqual(self.client.sent[0][0], GROUP_ID, "mesaj gruba gitmeli")

    def test_capital_turkish_keyword_matches(self):
        source_id = next(iter(bot.SOURCE_IDS))
        self._call(self.client.handlers[1][1], FakeEvent(source_id, 1001, "ÇAY KAMPANYASI"))
        self.assertEqual(bot.STATS["matched"], 1)

    def test_exclude_keyword_blocks(self):
        source_id = next(iter(bot.SOURCE_IDS))
        self._call(self.client.handlers[1][1], FakeEvent(source_id, 1002, "Çay çekilişi"))
        self.assertEqual(bot.STATS["matched"], 0)

    def test_non_matching_message_is_not_delivered(self):
        source_id = next(iter(bot.SOURCE_IDS))
        self._call(self.client.handlers[1][1], FakeEvent(source_id, 1000, "iPhone 17 geldi"))
        self.assertEqual(bot.STATS["matched"], 0)
        self.assertEqual(self.client.sent, [])
        self.assertEqual(bot.STATS["seen"], 1, "görülen mesaj sayacı artmalı")

    def test_unknown_chat_is_ignored(self):
        self._call(self.client.handlers[1][1], FakeEvent(-1007777777777, 1, "çay"))
        self.assertEqual(bot.STATS["seen"], 0)
        self.assertEqual(self.client.sent, [])

    def test_control_chat_message_is_not_forwarded(self):
        self._call(self.client.handlers[1][1], FakeEvent(GROUP_ID, ADMIN_ID, "çay"))
        self.assertEqual(bot.STATS["seen"], 0)
        self.assertEqual(self.client.sent, [])

    def test_forward_mode_uses_forward_messages(self):
        reset_state()
        path = self._write_config(copy_mode="forward", source_chats=["@firsatz"])
        self.addCleanup(os.unlink, path)
        client = self._run_main(path)
        source_id = next(iter(bot.SOURCE_IDS))
        asyncio.run(client.handlers[1][1](FakeEvent(source_id, 5, "çay")))
        self.assertEqual(len(client.forwarded), 1)
        self.assertEqual(client.sent, [])


class CheckModeTest(unittest.TestCase):
    def setUp(self):
        reset_state()

    def test_check_returns_zero_for_valid_setup(self):
        config = {
            "source_chats": ["@firsatz"],
            "destination": GROUP_ID,
            "control_chat": GROUP_ID,
            "admin_user_id": ADMIN_ID,
            "match_mode": "any",
            "copy_mode": "copy",
        }
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(config, handle)
        handle.close()
        self.addCleanup(os.unlink, handle.name)

        created = []
        with mock.patch.dict(os.environ, BASE_ENV, clear=False), \
             mock.patch.object(bot, "TelegramClient", lambda *a, **k: created.append(1)):
            code = asyncio.run(bot.main(["--check", "--config", handle.name]))
        self.assertEqual(code, 0)
        self.assertEqual(created, [], "--check modunda istemci oluşturulmamalı")

    def test_check_returns_one_when_secret_missing(self):
        config = {"source_chats": ["@firsatz"], "control_chat": "me"}
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(config, handle)
        handle.close()
        self.addCleanup(os.unlink, handle.name)

        env = dict(BASE_ENV, API_HASH="", SESSION_STRING="")
        with mock.patch.dict(os.environ, env, clear=False):
            code = asyncio.run(bot.main(["--check", "--config", handle.name]))
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()


class NotificationTest(unittest.TestCase):
    """notify_bot_token verildiğinde bildirim ping'i atılmalı."""

    def setUp(self):
        reset_state()
        self.calls = []
        self._patchers = []

    def tearDown(self):
        for patcher in self._patchers:
            patcher.stop()
        reset_state()

    def _patch(self, target, replacement):
        """Handler'lar main() döndükten SONRA çağrıldığı için yamalar açık kalmalı."""
        patcher = mock.patch.object(bot, target, replacement)
        patcher.start()
        self._patchers.append(patcher)

    def _run(self, config_extra):
        config = {
            "source_chats": ["@firsatz"],
            "destination": GROUP_ID,
            "include_keywords": ["çay"],
            "exclude_keywords": [],
            "match_mode": "any",
            "copy_mode": "copy",
            "control_chat": GROUP_ID,
            "admin_user_id": ADMIN_ID,
            "auto_restart": False,
            "notify_on_start": False,
        }
        config.update(config_extra)
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(config, handle)
        handle.close()
        self.addCleanup(os.unlink, handle.name)

        created = []

        async def fake_ping(token, chat_id, text):
            self.calls.append((token, chat_id, text))
            return True, "bildirim gönderildi"

        def factory(*args, **kwargs):
            client = FakeClient(*args, **kwargs)
            created.append(client)
            return client

        self._patch("send_bot_ping", fake_ping)
        with mock.patch.dict(os.environ, BASE_ENV, clear=False), \
             mock.patch.object(bot, "TelegramClient", factory), \
             mock.patch.object(bot, "StringSession", lambda *a, **k: object()):
            asyncio.run(bot.main(["--config", handle.name]))
        return created[0]

    def test_match_triggers_bot_ping(self):
        client = self._run({"notify_bot_token": "123:ABC"})
        source_id = next(iter(bot.SOURCE_IDS))
        asyncio.run(client.handlers[1][1](FakeEvent(source_id, 7, "Sıcak ÇAY 5 TL")))
        self.assertEqual(len(self.calls), 1, "eşleşmede bildirim atılmalı")
        token, chat_id, text = self.calls[0]
        self.assertEqual(token, "123:ABC")
        self.assertEqual(chat_id, GROUP_ID)
        self.assertIn("🔔", text)

    def test_no_token_means_no_ping(self):
        client = self._run({"notify_bot_token": None})
        source_id = next(iter(bot.SOURCE_IDS))
        asyncio.run(client.handlers[1][1](FakeEvent(source_id, 8, "ÇAY")))
        self.assertEqual(self.calls, [])
        self.assertEqual(bot.STATS["matched"], 1, "mesaj yine de iletilmeli")

    def test_null_string_token_is_treated_as_empty(self):
        client = self._run({"notify_bot_token": "none"})
        source_id = next(iter(bot.SOURCE_IDS))
        asyncio.run(client.handlers[1][1](FakeEvent(source_id, 9, "ÇAY")))
        self.assertEqual(self.calls, [])

    def test_test_command_warns_when_no_token(self):
        client = self._run({"notify_bot_token": None})
        event = FakeEvent(GROUP_ID, ADMIN_ID, "/test")
        asyncio.run(client.handlers[0][1](event))
        self.assertIn("notify_bot_token yok", event.replies[0])

    def test_test_command_confirms_ping(self):
        client = self._run({"notify_bot_token": "123:ABC"})
        event = FakeEvent(GROUP_ID, ADMIN_ID, "/test")
        asyncio.run(client.handlers[0][1](event))
        self.assertIn("Bot bildirimi de gönderildi", event.replies[0])
        self.assertEqual(len(self.calls), 1)


class DeliveryChainTest(unittest.TestCase):
    """Korumalı (noforwards) kanallarda alternatifli iletim zinciri."""

    def setUp(self):
        reset_state()
        self.config_path = self._write_config()
        self.client = self._run_main(self.config_path)
        self.source_id = next(iter(bot.SOURCE_IDS))

    def tearDown(self):
        os.unlink(self.config_path)
        reset_state()

    def _write_config(self, **overrides) -> str:
        config = {
            "source_chats": ["@firsatz"],
            "destination": GROUP_ID,
            "include_keywords": ["çay"],
            "exclude_keywords": [],
            "match_mode": "any",
            "delivery_modes": ["forward", "copy", "media", "text", "link"],
            "control_chat": GROUP_ID,
            "admin_user_id": ADMIN_ID,
            "auto_restart": False,
            "notify_on_start": False,
        }
        config.update(overrides)
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(config, handle)
        handle.close()
        return handle.name

    def _run_main(self, config_path: str) -> FakeClient:
        created = []

        def factory(*args, **kwargs):
            client = FakeClient(*args, **kwargs)
            created.append(client)
            return client

        with mock.patch.dict(os.environ, BASE_ENV, clear=False), \
             mock.patch.object(bot, "TelegramClient", factory), \
             mock.patch.object(bot, "StringSession", lambda *a, **k: object()):
            asyncio.run(bot.main(["--config", config_path]))
        return created[0]

    def _send(self, text="Sıcak ÇAY 5 TL"):
        asyncio.run(self.client.handlers[1][1](FakeEvent(self.source_id, 1234, text)))

    def test_chain_is_built_in_order(self):
        self.assertEqual(bot.DELIVERY_CHAIN, ["forward", "copy", "media", "text", "link"])

    def test_happy_path_uses_forward(self):
        self._send()
        self.assertEqual(len(self.client.forwarded), 1)
        self.assertEqual(bot.STATS["modes"].get("forward"), 1)

    def test_forward_blocked_falls_back_to_copy(self):
        """Korumalı kanal: forward CHAT_FORWARDS_RESTRICTED verir, copy denenir."""
        self.client.fail_modes.add("forward")
        self._send()
        self.assertEqual(self.client.forwarded, [])
        self.assertEqual(len(self.client.sent), 1)
        self.assertEqual(bot.STATS["modes"].get("copy"), 1)

    def test_forward_and_copy_blocked_falls_back_to_media_reupload(self):
        """En kritik senaryo: ikisi de korumalıysa medya indirilip yeniden yüklenir."""
        self.client.fail_modes.update({"forward", "copy"})
        self._send()
        self.assertEqual(len(self.client.files), 1, "medya yeniden yüklenmeli")
        self.assertEqual(bot.STATS["modes"].get("media"), 1)

    def test_media_unavailable_falls_back_to_text(self):
        self.client.fail_modes.update({"forward", "copy", "media", "download"})
        self._send("ÇAY 5 TL kampanya")
        self.assertTrue(any("ÇAY 5 TL" in str(m) for _, m in self.client.sent))
        self.assertEqual(bot.STATS["modes"].get("text"), 1)

    def test_everything_blocked_falls_back_to_link_card(self):
        """include_keywords boşken metinsiz (yalnızca medya) mesajlar da akar;
        metin olmadığı için son çare t.me bağlantısıdır."""
        reset_state()
        path = self._write_config(include_keywords=[])
        self.addCleanup(os.unlink, path)
        self.client = self._run_main(path)
        self.source_id = next(iter(bot.SOURCE_IDS))
        self.client.fail_modes.update({"forward", "copy", "media", "download"})
        self._send("")
        link_messages = [m for _, m in self.client.sent if "t.me" in str(m)]
        self.assertEqual(len(link_messages), 1, "son çare olarak t.me bağlantısı gönderilmeli")
        self.assertEqual(bot.STATS["modes"].get("link"), 1)

    def test_all_paths_failing_counts_as_failure(self):
        """Metin yok + bağlantı üretilemiyor -> hiçbir iletim yolu kalmaz."""
        reset_state()
        path = self._write_config(include_keywords=[])
        self.addCleanup(os.unlink, path)
        self.client = self._run_main(path)
        self.source_id = next(iter(bot.SOURCE_IDS))
        self.client.fail_modes.update({"forward", "copy", "media", "download"})
        with mock.patch.object(bot, "build_message_link", lambda *a, **k: None):
            self._send("")
        self.assertEqual(bot.STATS["forwarded"], 0)
        self.assertEqual(bot.STATS["failed"], 1)

    def test_custom_chain_order_is_respected(self):
        reset_state()
        path = self._write_config(delivery_modes=["copy", "forward"])
        self.addCleanup(os.unlink, path)
        client = self._run_main(path)
        self.assertEqual(bot.DELIVERY_CHAIN[:2], ["copy", "forward"])
        asyncio.run(client.handlers[1][1](FakeEvent(next(iter(bot.SOURCE_IDS)), 5, "çay")))
        self.assertEqual(len(client.sent), 1)

    def test_legacy_copy_mode_still_works(self):
        reset_state()
        path = self._write_config(delivery_modes=None, copy_mode="copy")
        self.addCleanup(os.unlink, path)
        client = self._run_main(path)
        self.assertEqual(bot.DELIVERY_CHAIN[0], "copy", "eski copy_mode alanı ilk sıraya konmalı")

    def test_unknown_mode_is_rejected_by_check(self):
        config = {"source_chats": ["@firsatz"], "control_chat": "me",
                  "delivery_modes": ["forward", "ekrangoruntusu"]}
        with mock.patch.dict(os.environ, BASE_ENV, clear=False):
            problems = bot.check_environment(config)
        self.assertTrue(any("delivery_modes" in p for p in problems), problems)


class BuildDeliveryChainTest(unittest.TestCase):
    def test_defaults_to_full_chain(self):
        self.assertEqual(
            bot.build_delivery_chain({}),
            ["forward", "copy", "media", "text", "link"],
        )

    def test_copy_mode_copy_puts_copy_first(self):
        self.assertEqual(bot.build_delivery_chain({"copy_mode": "copy"})[0], "copy")

    def test_explicit_list_is_completed_with_fallbacks(self):
        self.assertEqual(
            bot.build_delivery_chain({"delivery_modes": ["media"]}),
            ["media", "forward", "copy", "text", "link"],
        )

    def test_duplicates_and_junk_are_cleaned(self):
        with self.assertLogs("telegram-filter", level="WARNING"):
            chain = bot.build_delivery_chain({"delivery_modes": ["copy", "copy", "saçma", "text"]})
        self.assertEqual(chain, ["copy", "text", "forward", "media", "link"])


class MessageLinkTest(unittest.TestCase):
    def test_public_channel_link(self):
        event = FakeEvent(-1001234567890, 1, "x", message_id=42)
        link = bot.build_message_link(event, {"username": "firsatz"})
        self.assertEqual(link, "https://t.me/firsatz/42")

    def test_private_channel_link(self):
        event = FakeEvent(-1001234567890, 1, "x", message_id=7)
        link = bot.build_message_link(event, {"username": None})
        self.assertEqual(link, "https://t.me/c/1234567890/7")

    def test_basic_group_has_no_link(self):
        event = FakeEvent(-5092968106, 1, "x", message_id=9)
        self.assertIsNone(bot.build_message_link(event, {"username": None}))


class IdCommandTest(unittest.TestCase):
    def setUp(self):
        reset_state()
        config = {
            "source_chats": ["@firsatz"],
            "destination": GROUP_ID,
            "include_keywords": ["çay"],
            "control_chat": GROUP_ID,
            "admin_user_id": ADMIN_ID,
            "auto_restart": False,
            "notify_on_start": False,
        }
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(config, handle)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        created = []

        def factory(*args, **kwargs):
            client = FakeClient(*args, **kwargs)
            created.append(client)
            return client

        with mock.patch.dict(os.environ, BASE_ENV, clear=False), \
             mock.patch.object(bot, "TelegramClient", factory), \
             mock.patch.object(bot, "StringSession", lambda *a, **k: object()):
            asyncio.run(bot.main(["--config", handle.name]))
        self.client = created[0]

    def tearDown(self):
        reset_state()

    def test_id_command_reports_ids_for_config(self):
        event = FakeEvent(GROUP_ID, ADMIN_ID, "/id")
        asyncio.run(self.client.handlers[0][1](event))
        reply = event.replies[0]
        self.assertIn(str(GROUP_ID), reply)
        self.assertIn(str(ADMIN_ID), reply)
        self.assertIn('"control_chat"', reply)
        self.assertIn('"admin_user_id"', reply)
