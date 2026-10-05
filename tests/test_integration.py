"""bot.main()'in açılış akışını sahte bir Telethon istemcisiyle uçtan uca dener.

Bu test, gerçek hatanın (control_chat string verildiğinde tüm update akışının
ölmesi) bir daha geri gelmemesini garanti eder.

Çalıştırma:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import asyncio
import io
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


def make_channel(name="FırsatZ", username=None):
    return types.Channel(id=abs(CHANNEL_ID), title=name, photo=types.ChatPhotoEmpty(), date=0,
                         username=username)


class FakeButton:
    """Inline buton yerine geçer; hem eski (``url=``) hem yeni (``type=``) şema."""

    def __init__(self, text, url=None, inner_url=None):
        self.text = text
        self.url = url
        self.type = mock.Mock(url=inner_url) if inner_url else None


class FakeRow:
    def __init__(self, buttons):
        self.buttons = list(buttons)


class FakeMarkup:
    def __init__(self, rows):
        self.rows = list(rows)


class FakeFile:
    """Telethon ``Message.file`` yerine geçen basit taşıyıcı."""

    def __init__(self, name=None, ext=".jpg", mime="image/jpeg", size=1024):
        self.name = name
        self.ext = ext
        self.mime_type = mime
        self.size = size


class FakeMessage:
    """Gerçek Telethon Message'ın yerine geçer; str OLMAMASI önemli,
    aksi halde send_message(copy) senaryosu yanlışlıkla başarılı sayılır."""

    def __init__(self, message_id, media=True, text=None, entities=None, reply_markup=None, file=None):
        self.id = message_id
        self.media = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=1)) if media else None
        self.file = file if file is not None else FakeFile()
        self.video = None
        self.message = text
        self.entities = list(entities or [])
        self.reply_markup = reply_markup

    def __str__(self):
        return f"<mesaj:{self.id}>"


class FakeEvent:
    def __init__(self, chat_id, sender_id, text, message_id=1, media=True,
                 entities=None, reply_markup=None, file=None):
        self.chat_id = chat_id
        self.sender_id = sender_id
        self.raw_text = text
        self.id = message_id
        self.message = FakeMessage(message_id, media, text=text, entities=entities,
                                   reply_markup=reply_markup, file=file)
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
        self.file_names = []
        self.unresolved = []
        self.fail_modes = set()   # test senaryosu için kapatılacak yollar

    @property
    def delivered(self) -> list:
        """Metin/mesaj olarak giden + dosya olarak giden her şey."""
        return list(self.sent) + [(entity, message) for entity, message, _ in self.files]

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
            return make_channel(value.lstrip("@"), username=value.lstrip("@"))
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
        reupload = isinstance(file, (bytes, bytearray, io.BytesIO))
        if reupload and ("media" in self.fail_modes or "download" in self.fail_modes):
            raise self._protected_error("media")
        if not reupload and "copy" in self.fail_modes:
            # Korumalı kanalda medyayı referansla yeniden göndermek de engellenir.
            raise self._protected_error("copy")
        payload = file.getvalue() if isinstance(file, io.BytesIO) else file
        self.files.append((entity, payload, caption))
        self.file_names.append(getattr(file, "name", None))

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
        self.assertEqual(len(self.client.delivered), 1)
        self.assertEqual(self.client.delivered[0][0], GROUP_ID, "mesaj gruba gitmeli")

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




class NotificationTest(unittest.TestCase):
    """Bildirim: fırsatın kopyası + gizli linkler + "Fırsatı Gönderen" altbilgisi."""

    def setUp(self):
        reset_state()
        self.calls: list[dict] = []        # send_bot_ping çağrıları
        self.media_calls: list[dict] = []  # send_bot_media çağrıları
        self.media_ok = True
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

        async def fake_ping(token, chat_id, text, **kwargs):
            self.calls.append({"token": token, "chat_id": chat_id, "text": text, **kwargs})
            return True, "bildirim gönderildi"

        async def fake_media(token, chat_id, **kwargs):
            if not self.media_ok:
                return False, "HTTP 400: bad request"
            self.media_calls.append({"token": token, "chat_id": chat_id, **kwargs})
            return True, "bildirim medyası gönderildi"

        def factory(*args, **kwargs):
            client = FakeClient(*args, **kwargs)
            created.append(client)
            return client

        self._patch("send_bot_ping", fake_ping)
        self._patch("send_bot_media", fake_media)
        with mock.patch.dict(os.environ, BASE_ENV, clear=False), \
             mock.patch.object(bot, "TelegramClient", factory), \
             mock.patch.object(bot, "StringSession", lambda *a, **k: object()):
            asyncio.run(bot.main(["--config", handle.name]))
        return created[0]

    def _send(self, client, text, **kwargs):
        asyncio.run(client.handlers[1][1](FakeEvent(next(iter(bot.SOURCE_IDS)), 7, text, **kwargs)))

    @staticmethod
    def _links(entity_list, kind="text_link"):
        return [e for e in (entity_list or []) if e.get("type") == kind]

    # --- yeni biçim ---------------------------------------------------------

    def test_notification_is_the_message_itself_plus_footer(self):
        """Kırpılmış/küçültülmüş özet değil, mesajın kendisi + altbilgi gitmeli."""
        client = self._run({"notify_bot_token": "123:ABC"})
        text = "Sıcak ÇAY 5 TL\nKaçırılmayacak fırsat!"
        self._send(client, text)
        self.assertEqual(len(self.media_calls), 1, "medya bildirimi denenmeli")
        call = self.media_calls[0]
        self.assertEqual(call["token"], "123:ABC")
        self.assertEqual(call["chat_id"], GROUP_ID)
        self.assertIn(text, call["caption"], "mesajın tamamı gitmeli")
        self.assertTrue(call["caption"].endswith("Fırsatı Gönderen: firsatz"), call["caption"])
        self.assertEqual(call["kind"], "photo")
        self.assertEqual(call["filename"], "firsat_1.jpg")

    def test_footer_name_hides_the_source_message_link(self):
        client = self._run({"notify_bot_token": "123:ABC"})
        self._send(client, "ÇAY fırsatı")
        links = self._links(self.media_calls[0]["entities"])
        self.assertEqual(len(links), 1, self.media_calls[0]["entities"])
        footer = links[0]
        self.assertEqual(footer["url"], "https://t.me/firsatz/1")
        self.assertEqual(footer["type"], "text_link")
        self.assertIn("Fırsatı Gönderen: firsatz", self.media_calls[0]["caption"])

    def test_hidden_entity_link_is_carried_in_text_and_entity(self):
        """'Fırsata Git' yazısının altına gizlenmiş link kaybolmamalı."""
        client = self._run({"notify_bot_token": "123:ABC"})
        text = "Fırsata Git 👉 çay"
        entity = types.MessageEntityTextUrl(
            offset=0, length=len("Fırsata Git"), url="https://amzn.to/3xyz",
        )
        self._send(client, text, entities=[entity])
        caption = self.media_calls[0]["caption"]
        self.assertIn("https://amzn.to/3xyz", caption, "gizli link metin olarak da yazılmalı")
        self.assertIn("🔗", caption)
        hidden = self._links(self.media_calls[0]["entities"])
        urls = {item["url"] for item in hidden}
        self.assertIn("https://amzn.to/3xyz", urls, "entity korunmalı (tıklanabilir)")
        self.assertIn("https://t.me/firsatz/1", urls, "altbilgi linki")

    def test_button_links_become_inline_keyboard(self):
        client = self._run({"notify_bot_token": "123:ABC", "notify_media": False})
        markup = FakeMarkup([FakeRow([FakeButton("Fırsata Git", inner_url="https://amzn.to/btn")])])
        self._send(client, "Fırsata git 👇 çay", reply_markup=markup)
        self.assertEqual(self.media_calls, [])
        self.assertEqual(len(self.calls), 1)
        call = self.calls[0]
        self.assertIn("https://amzn.to/btn", call["text"], "buton linki metinde de olmalı")
        self.assertEqual(call["keyboard"], {"inline_keyboard": [[
            {"text": "Fırsata Git", "url": "https://amzn.to/btn"},
        ]]})

    def test_legacy_button_schema_is_supported(self):
        """Eski Telethon sürümlerindeki ``KeyboardButtonUrl(url=...)`` şeması."""
        client = self._run({"notify_bot_token": "123:ABC", "notify_media": False})
        markup = FakeMarkup([FakeRow([FakeButton("Fırsat", url="https://amzn.to/eski")])])
        self._send(client, "çay", reply_markup=markup)
        self.assertEqual(self.calls[0]["keyboard"]["inline_keyboard"][0][0]["url"], "https://amzn.to/eski")

    def test_text_only_message_notification(self):
        client = self._run({"notify_bot_token": "123:ABC"})
        self._send(client, "ÇAY 5 TL", media=False)
        self.assertEqual(self.media_calls, [])
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.calls[0]["text"].endswith("Fırsatı Gönderen: firsatz"))

    def test_media_failure_falls_back_to_text_notification(self):
        self.media_ok = False
        client = self._run({"notify_bot_token": "123:ABC"})
        self._send(client, "ÇAY 5 TL")
        self.assertEqual(len(self.calls), 1, "medya gönderilemezse metin bildirimi gitmeli")
        self.assertIn("ÇAY 5 TL", self.calls[0]["text"])
        self.assertTrue(self.calls[0]["text"].endswith("Fırsatı Gönderen: firsatz"))

    def test_flags_can_disable_appendix_and_footer(self):
        client = self._run({"notify_bot_token": "123:ABC", "notify_media": False,
                            "append_links": False, "source_footer": False})
        entity = types.MessageEntityTextUrl(offset=0, length=3, url="https://amzn.to/yok")
        self._send(client, "çay", entities=[entity])
        text = self.calls[0]["text"]
        self.assertEqual(text, "çay")
        self.assertNotIn("Fırsatı Gönderen", text)
        self.assertNotIn("https://amzn.to/yok", text)

    # --- eski davranışların korunması --------------------------------------

    def test_no_token_means_no_ping(self):
        client = self._run({"notify_bot_token": None})
        self._send(client, "ÇAY")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.media_calls, [])
        self.assertEqual(bot.STATS["matched"], 1, "mesaj yine de iletilmeli")

    def test_null_string_token_is_treated_as_empty(self):
        client = self._run({"notify_bot_token": "none"})
        self._send(client, "ÇAY")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.media_calls, [])

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
        self.assertEqual(len(self.client.delivered), 1)
        self.assertEqual(bot.STATS["modes"].get("copy"), 1)

    def test_forward_and_copy_blocked_falls_back_to_media_reupload(self):
        """En kritik senaryo: ikisi de korumalıysa medya indirilip yeniden yüklenir."""
        self.client.fail_modes.update({"forward", "copy"})
        self._send()
        self.assertEqual(len(self.client.files), 1, "medya yeniden yüklenmeli")
        self.assertEqual(bot.STATS["modes"].get("media"), 1)

    def test_reuploaded_photo_keeps_its_name_and_type(self):
        """Eski hata: bytes olarak yüklenen fotoğraf 'unnamed' adlı belgeye dönüşüyordu."""
        self.client.fail_modes.update({"forward", "copy"})
        self._send()
        self.assertEqual(self.client.file_names, ["firsat_1.jpg"], "uzantılı ad şart")
        self.assertNotIn("unnamed", self.client.file_names)

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
        self.assertEqual(len(client.delivered), 1)

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


class HiddenLinkDeliveryTest(unittest.TestCase):
    """Gizli linkler (metin altı / buton) iletilen mesajla birlikte hedefe gitmeli."""

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

    def _send(self, text, **kwargs):
        asyncio.run(self.client.handlers[1][1](FakeEvent(self.source_id, 42, text, **kwargs)))

    @staticmethod
    def _texts(client) -> str:
        chunks = [str(message) for _, message in client.sent]
        chunks += [str(caption or "") for *_, caption in client.files]
        return "\n".join(chunks)

    def test_hidden_entity_link_survives_text_fallback(self):
        self.client.fail_modes.update({"forward", "copy", "media", "download"})
        text = "çay fırsatı – Fırsata Git"
        entity = types.MessageEntityTextUrl(
            offset=text.index("Fırsata Git"), length=len("Fırsata Git"), url="https://amzn.to/gizli",
        )
        self._send(text, entities=[entity])
        delivered = self._texts(self.client)
        self.assertIn("https://amzn.to/gizli", delivered, "gizli link iletide olmalı")
        self.assertIn("Fırsata Git", delivered)
        self.assertEqual(bot.STATS["modes"].get("text"), 1)

    def test_hidden_entity_link_survives_media_reupload(self):
        """Fotoğraf yeniden yüklenirken açıklamadaki gizli link kaybolmamalı."""
        self.client.fail_modes.update({"forward", "copy"})
        text = "çay 5 TL"
        entity = types.MessageEntityTextUrl(offset=0, length=3, url="https://amzn.to/kapak")
        self._send(text, entities=[entity])
        self.assertEqual(len(self.client.files), 1)
        caption = self.client.files[0][2]
        self.assertIn("https://amzn.to/kapak", caption)
        self.assertIn("çay 5 TL", caption)

    def test_button_link_is_written_into_copy(self):
        """Kullanıcı hesabı inline klavye gönderemez; link metne yazılmalı."""
        self.client.fail_modes.add("forward")
        self._send("çay fırsatı", media=False, reply_markup=FakeMarkup([
            FakeRow([FakeButton("Fırsata Git", inner_url="https://amzn.to/buton")]),
        ]))
        delivered = self._texts(self.client)
        self.assertIn("https://amzn.to/buton", delivered)
        self.assertIn("🔗 Fırsata Git", delivered)

    def test_text_fallback_lists_hidden_links_and_source(self):
        """Son çare metin: hem buton linki hem orijinal mesaja giden link eklenir."""
        self.client.fail_modes.update({"forward", "copy", "media", "download"})
        self._send("çay fırsatı", reply_markup=FakeMarkup([
            FakeRow([FakeButton("Fırsata Git", url="https://amzn.to/kart")]),
        ]))
        delivered = self._texts(self.client)
        self.assertIn("https://amzn.to/kart", delivered)
        self.assertIn("https://t.me/firsatz/1", delivered, "kaynak mesaj linki de olmalı")
        self.assertEqual(bot.STATS["modes"].get("text"), 1)

    def test_caption_limit_is_respected(self):
        """Uzun açıklamada bile ek + link korunur, Telegram sınırı aşılmaz."""
        self.client.fail_modes.update({"forward", "copy"})
        entity = types.MessageEntityTextUrl(offset=0, length=3, url="https://amzn.to/uzun")
        self._send("ç" * 8 + " " + ("çay " * 400), entities=[entity])
        caption = self.client.files[0][2]
        self.assertLessEqual(len(caption), bot.CAPTION_LIMIT)
        self.assertIn("https://amzn.to/uzun", caption)


if __name__ == "__main__":
    unittest.main()
