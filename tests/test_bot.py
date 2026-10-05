"""bot.py içindeki saf (ağ gerektirmeyen) fonksiyonların testleri.

Çalıştırma:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import asyncio
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bot  # noqa: E402


class ParseChatValueTest(unittest.TestCase):
    def test_string_id_becomes_int(self):
        """JSON'da tırnak içindeki negatif ID int olmalı, yoksa Telethon kullanıcı adı sanır."""
        self.assertEqual(bot.parse_chat_value("-5092968106"), -5092968106)
        self.assertEqual(bot.parse_chat_value("-1001234567890"), -1001234567890)
        self.assertIsInstance(bot.parse_chat_value("-5092968106"), int)

    def test_int_passthrough(self):
        self.assertEqual(bot.parse_chat_value(-5092968106), -5092968106)

    def test_me_and_username_stay_strings(self):
        self.assertEqual(bot.parse_chat_value("me"), "me")
        self.assertEqual(bot.parse_chat_value("ME"), "me")
        self.assertEqual(bot.parse_chat_value("@firsatz"), "@firsatz")

    def test_whitespace_is_tolerated(self):
        self.assertEqual(bot.parse_chat_value("  -1001234567890  "), -1001234567890)

    def test_bool_and_empty_rejected(self):
        with self.assertRaises(ValueError):
            bot.parse_chat_value(True)
        with self.assertRaises(ValueError):
            bot.parse_chat_value("   ")

    def test_chat_values_skips_bad_entries(self):
        with self.assertLogs("telegram-filter", level="WARNING"):
            result = bot.chat_values(["@firsatz", "", "-5092968106", True])
        self.assertEqual(result, ["@firsatz", -5092968106])


class ParseAdminIdsTest(unittest.TestCase):
    def test_accepts_every_documented_shape(self):
        for value in (1143378073, "1143378073", " 1143378073 ", [1143378073]):
            self.assertEqual(bot.parse_admin_ids(value), {1143378073}, value)

    def test_multiple_ids(self):
        self.assertEqual(bot.parse_admin_ids("111,222;333"), {111, 222, 333})

    def test_null_like_values_are_empty(self):
        for value in (None, "", "none", "null", "yok"):
            self.assertEqual(bot.parse_admin_ids(value), set(), repr(value))

    def test_garbage_does_not_crash(self):
        """Eski sürüm int('none') ile çöküyordu; artık yalnızca uyarı vermeli."""
        with self.assertLogs("telegram-filter", level="WARNING"):
            self.assertEqual(bot.parse_admin_ids("none,abc"), set())


class NormalizeTest(unittest.TestCase):
    def test_turkish_capital_i_matches_lowercase_keyword(self):
        """"İNDİRİM".casefold() birleşik nokta üretir; normalize() bunu düzeltmeli."""
        self.assertNotIn("indirim", "İNDİRİM".casefold())  # eski davranışın kanıtı
        self.assertIn("indirim", bot.normalize("İNDİRİM"))

    def test_dotless_i_is_preserved(self):
        self.assertIn("ıspanak", bot.normalize("ISPANAK"))

    def test_plain_text_unchanged(self):
        self.assertEqual(bot.normalize("Çay 5 TL"), "çay 5 tl")

    def test_none_is_safe(self):
        self.assertEqual(bot.normalize(None), "")


class MatchesTest(unittest.TestCase):
    include = [bot.normalize(x) for x in ("çay", "kahve", "şeker")]
    exclude = [bot.normalize(x) for x in ("çekiliş", "hediye")]

    def test_any_mode(self):
        self.assertTrue(bot.matches("Sıcak KAHVE 250 TL", self.include, self.exclude, "any"))
        self.assertFalse(bot.matches("iPhone 17 satışta", self.include, self.exclude, "any"))

    def test_all_mode(self):
        self.assertFalse(bot.matches("çay ve şeker", self.include, self.exclude, "all"))
        self.assertTrue(bot.matches("çay kahve şeker", self.include, self.exclude, "all"))

    def test_exclude_wins(self):
        self.assertFalse(bot.matches("Kahve çekilişi", self.include, self.exclude, "any"))

    def test_capital_turkish_text_matches(self):
        self.assertTrue(bot.matches("ÇAY VE ŞEKER KAMPANYASI", self.include, self.exclude, "any"))

    def test_empty_include_accepts_everything(self):
        self.assertTrue(bot.matches("her şey", [], self.exclude, "any"))

    def test_empty_text_rejected_when_keywords_present(self):
        self.assertFalse(bot.matches("", self.include, self.exclude, "any"))


class CheckEnvironmentTest(unittest.TestCase):
    base_config = {
        "source_chats": ["@firsatz"],
        "destination": "me",
        "match_mode": "any",
        "copy_mode": "copy",
        "control_chat": "me",
        "admin_user_id": None,
    }
    good_env = {
        "API_ID": "123456",
        "API_HASH": "a" * 32,
        "SESSION_STRING": "1BVtsOKAB...",
        "GH_PAT": "github_pat_x",
    }

    def test_healthy_setup_has_no_problems(self):
        with mock.patch.dict(os.environ, self.good_env, clear=False):
            self.assertEqual(bot.check_environment(dict(self.base_config)), [])

    def test_missing_secrets_are_reported(self):
        env = {"API_ID": "", "API_HASH": "", "SESSION_STRING": "", "GH_PAT": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            problems = bot.check_environment(dict(self.base_config))
        joined = " ".join(problems)
        self.assertIn("API_ID", joined)
        self.assertIn("API_HASH", joined)
        self.assertIn("SESSION_STRING", joined)

    def test_bad_api_hash_length_reported(self):
        env = dict(self.good_env, API_HASH="kisa")
        with mock.patch.dict(os.environ, env, clear=False):
            problems = bot.check_environment(dict(self.base_config))
        self.assertTrue(any("32 karakter" in p for p in problems), problems)

    def test_group_control_without_admin_is_a_problem(self):
        config = dict(self.base_config, control_chat=-5092968106, admin_user_id=None)
        with mock.patch.dict(os.environ, self.good_env, clear=False):
            problems = bot.check_environment(config)
        self.assertTrue(any("admin_user_id" in p for p in problems), problems)

    def test_group_control_with_admin_is_fine(self):
        config = dict(self.base_config, control_chat="-5092968106", admin_user_id="1143378073")
        with mock.patch.dict(os.environ, self.good_env, clear=False):
            self.assertEqual(bot.check_environment(config), [])

    def test_empty_sources_reported(self):
        config = dict(self.base_config, source_chats=[])
        with mock.patch.dict(os.environ, self.good_env, clear=False):
            problems = bot.check_environment(config)
        self.assertTrue(any("source_chats" in p for p in problems), problems)

    def test_invalid_modes_reported(self):
        config = dict(self.base_config, match_mode="bazen", copy_mode="ucur")
        with mock.patch.dict(os.environ, self.good_env, clear=False):
            problems = bot.check_environment(config)
        self.assertTrue(any("match_mode" in p for p in problems), problems)
        self.assertTrue(any("copy_mode" in p for p in problems), problems)


class RunCheckTest(unittest.TestCase):
    def _write(self, config: dict) -> str:
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(config, handle)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_valid_config_exits_zero(self):
        path = self._write({
            "source_chats": ["@firsatz"],
            "destination": -5092968106,
            "control_chat": -5092968106,
            "admin_user_id": 1143378073,
            "match_mode": "any",
            "copy_mode": "copy",
        })
        with mock.patch.dict(os.environ, CheckEnvironmentTest.good_env, clear=False):
            with redirect_stdout(io.StringIO()) as out:
                code = bot.run_check(path)
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("yapılandırma geçerli", out.getvalue())

    def test_missing_secret_exits_one_and_names_it(self):
        path = self._write({"source_chats": ["@firsatz"], "control_chat": "me"})
        env = {"API_ID": "123456", "API_HASH": "", "SESSION_STRING": "", "GH_PAT": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            with redirect_stdout(io.StringIO()) as out:
                code = bot.run_check(path)
        self.assertEqual(code, 1)
        self.assertIn("API_HASH", out.getvalue())

    def test_broken_json_exits_one(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        handle.write("{ bozuk json")
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(bot.run_check(handle.name), 1)
        self.assertIn("JSON", out.getvalue())

    def test_missing_file_exits_one(self):
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(bot.run_check("/yok/boyle/config.json"), 1)
        self.assertIn("bulunamadı", out.getvalue())


class StatusTextTest(unittest.TestCase):
    def test_status_and_source_text_render(self):
        bot.SOURCES.clear()
        bot.SOURCE_FAILURES.clear()
        bot.SOURCES.append({"id": -1001, "name": "FırsatZ", "requested": "@firsatz", "joined": True})
        bot.SOURCE_IDS.clear()
        bot.SOURCE_IDS.add(-1001)
        bot.SOURCE_FAILURES.append(("@olmayan", ValueError("yok")))
        bot.STATS.update({"seen": 42, "matched": 3, "forwarded": 3, "failed": 0})
        bot.CONTROL_IDS.clear()
        bot.CONTROL_IDS.update({-5092968106})
        bot.CONTROL_NAMES.clear()
        bot.CONTROL_NAMES.append("Benim Grup [-5092968106]")
        bot.DESTINATION_LABEL = "Benim Grup [-5092968106]"

        status = bot.build_status_text({"include_keywords": ["çay"], "match_mode": "any", "source_chats": ["@firsatz"]})
        self.assertIn("Takipçi aktif", status)
        self.assertIn("Görülen: 42", status)
        self.assertIn("Benim Grup", status)

        sources = bot.build_source_text()
        self.assertIn("FırsatZ", sources)
        self.assertIn("çözülemedi", sources)

    def test_help_text_lists_every_command(self):
        for command in ("/status", "/test", "/source", "/restart", "/help"):
            self.assertIn(command, bot.HELP_TEXT)


class RealConfigTest(unittest.TestCase):
    def test_repository_config_is_usable(self):
        """Depodaki config.json gerçekten yüklenip doğrulanabilmeli."""
        path = Path(__file__).resolve().parents[1] / "config.json"
        config = bot.load_config(path)
        self.assertEqual(len(config["source_chats"]), 16)
        self.assertIsInstance(config["control_chat"], int)
        self.assertIsInstance(config["destination"], int)
        self.assertEqual(bot.parse_admin_ids(config["admin_user_id"]), {1143378073})
        self.assertIn("notify_bot_token", config)
        with mock.patch.dict(os.environ, CheckEnvironmentTest.good_env, clear=False):
            self.assertEqual(bot.check_environment(config), [])


if __name__ == "__main__":
    unittest.main()


class BotPingTest(unittest.TestCase):
    """notify_bot_token ile gönderilen bildirim ping'i."""

    def test_no_token_returns_false_without_calling_api(self):
        with mock.patch("bot.urllib.request.urlopen") as urlopen:
            ok, detail = asyncio.run(bot.send_bot_ping("", -5092968106, "selam"))
        self.assertFalse(ok)
        self.assertIn("tanımlı değil", detail)
        urlopen.assert_not_called()

    def test_successful_ping_posts_to_bot_api(self):
        fake_response = io.BytesIO(json.dumps({"ok": True, "result": {}}).encode())
        fake_response.__enter__ = lambda self: self
        fake_response.__exit__ = lambda self, *a: False
        with mock.patch("bot.urllib.request.urlopen", return_value=fake_response) as urlopen:
            ok, detail = asyncio.run(bot.send_bot_ping("123:ABC", -5092968106, "🔔 deneme"))
        self.assertTrue(ok, detail)
        request = urlopen.call_args[0][0]
        self.assertEqual(request.full_url, "https://api.telegram.org/bot123:ABC/sendMessage")
        self.assertEqual(json.loads(request.data.decode())["chat_id"], -5092968106)

    def test_api_error_is_reported_not_raised(self):
        with mock.patch("bot.urllib.request.urlopen",
                        side_effect=urllib.error.HTTPError(
                            "u", 400, "Bad Request", {}, io.BytesIO(b'{"description":"chat not found"}'))):
            with self.assertLogs("telegram-filter", level="ERROR"):
                ok, detail = asyncio.run(bot.send_bot_ping("123:ABC", -1, "x"))
        self.assertFalse(ok)
        self.assertIn("400", detail)


class EnvOverrideTest(unittest.TestCase):
    def _config_file(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump({"source_chats": ["@firsatz"], "control_chat": "me"}, handle)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_delivery_modes_env_overrides_config(self):
        env = {"DELIVERY_MODES": "copy, forward ,media", "MAX_MEDIA_MB": "10"}
        with mock.patch.dict(os.environ, env, clear=False):
            config = bot.load_config(self._config_file())
        self.assertEqual(config["delivery_modes"], ["copy", "forward", "media"])
        self.assertEqual(config["max_media_mb"], "10")
        self.assertEqual(bot.build_delivery_chain(config)[:3], ["copy", "forward", "media"])

    def test_absent_env_leaves_config_alone(self):
        env = {"DELIVERY_MODES": "", "MAX_MEDIA_MB": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            config = bot.load_config(self._config_file())
        self.assertNotIn("delivery_modes", config)


# ---------------------------------------------------------------------------
# Gizli bağlantılar, mesaj birleştirme ve Bot API yüklemeleri
# ---------------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402

from telethon.tl import types as tl_types  # noqa: E402


def make_message(text="", entities=None, buttons=None, webpage=None, media=True,
                 file=None, message_id=1, reply_markup=None):
    """``extract_links``/``compose_message`` için hafif mesaj taklidi."""
    if reply_markup is None and buttons:
        reply_markup = SimpleNamespace(rows=[SimpleNamespace(buttons=list(buttons))])
    if webpage is not None:
        media = SimpleNamespace(webpage=SimpleNamespace(url=webpage, title=None))
    elif media is True:
        media = tl_types.MessageMediaPhoto(photo=tl_types.PhotoEmpty(id=1))
    elif not media:
        media = None
    return SimpleNamespace(
        id=message_id, message=text, entities=list(entities or []),
        reply_markup=reply_markup, media=media, file=file,
    )


class Utf16Test(unittest.TestCase):
    """Telegram offset'leri UTF-16 kod birimi sayar; emoji düz dilimi kaydırır."""

    def test_emoji_counts_as_two_units(self):
        text = "🔥çay"
        self.assertEqual(bot.utf16_length(text), 5)
        self.assertEqual(bot.utf16_slice(text, 2, 3), "çay")

    def test_bad_ranges_are_safe(self):
        self.assertEqual(bot.utf16_slice("abc", -1, 2), "")
        self.assertEqual(bot.utf16_slice("abc", 0, 0), "")
        self.assertEqual(bot.utf16_slice("", 0, 2), "")

    def test_plain_text_length_matches(self):
        self.assertEqual(bot.utf16_length("çay"), 3)


class ExtractLinksTest(unittest.TestCase):
    def test_hidden_text_link_is_found(self):
        """'Fırsata Git' yazısının altına gizlenmiş link en kritik senaryo."""
        text = "Fırsata Git"
        message = make_message(text, entities=[
            tl_types.MessageEntityTextUrl(offset=0, length=len(text), url="https://amzn.to/1"),
        ])
        self.assertEqual(bot.extract_links(message), [
            {"url": "https://amzn.to/1", "label": "Fırsata Git", "kind": "entity"},
        ])
        self.assertEqual(bot.build_link_appendix(message), "🔗 Fırsata Git: https://amzn.to/1")

    def test_visible_url_is_not_repeated_in_appendix(self):
        text = "https://amzn.to/2 çay kampanyası"
        message = make_message(text, entities=[
            tl_types.MessageEntityUrl(offset=0, length=len("https://amzn.to/2")),
        ])
        self.assertEqual(bot.missing_links(message), [])
        self.assertEqual(bot.build_link_appendix(message), "")

    def test_button_link_old_and_new_schema(self):
        eski = SimpleNamespace(text="Fırsata Git", url="https://amzn.to/eski", type=None)
        yeni = SimpleNamespace(text="Fırsata Git", url=None,
                               type=SimpleNamespace(url="https://amzn.to/yeni"))
        message = make_message("çay", buttons=[eski, yeni])
        urls = [item["url"] for item in bot.extract_links(message)]
        self.assertEqual(urls, ["https://amzn.to/eski", "https://amzn.to/yeni"])
        appendix = bot.build_link_appendix(message)
        self.assertIn("🔗 Fırsata Git: https://amzn.to/eski", appendix)
        self.assertIn("https://amzn.to/yeni", appendix)

    def test_copy_button_with_url_is_found(self):
        kopyala = SimpleNamespace(text="Linki kopyala", url=None,
                                  type=SimpleNamespace(url=None, copy_text="https://amzn.to/kopya"))
        message = make_message("çay", buttons=[kopyala])
        self.assertEqual([item["url"] for item in bot.extract_links(message)], ["https://amzn.to/kopya"])

    def test_webpage_preview_is_found(self):
        message = make_message("çay fırsatı", webpage="https://amzn.to/onizleme")
        self.assertEqual([item["url"] for item in bot.extract_links(message)], ["https://amzn.to/onizleme"])

    def test_plain_text_url_is_found_and_deduplicated(self):
        message = make_message("çay https://amzn.to/3 ve tekrar https://amzn.to/3")
        links = bot.extract_links(message)
        self.assertEqual([item["url"] for item in links], ["https://amzn.to/3"])
        self.assertEqual(links[0]["kind"], "text")

    def test_trailing_punctuation_is_trimmed(self):
        message = make_message("çay https://amzn.to/4, hemen al")
        self.assertEqual([item["url"] for item in bot.extract_links(message)], ["https://amzn.to/4"])

    def test_entity_label_with_emoji_uses_utf16_offsets(self):
        """Emoji'den sonra gelen entity offset'i UTF-16'dır; düz dilim kayardı."""
        text = "🔥 FIRSAT – Fırsata Git 👉"
        offset = bot.utf16_length(text[:text.index("Fırsata Git")])
        message = make_message(text, entities=[
            tl_types.MessageEntityTextUrl(offset=offset, length=bot.utf16_length("Fırsata Git"),
                                          url="https://amzn.to/emoji"),
        ])
        self.assertEqual(bot.extract_links(message)[0]["label"], "Fırsata Git")

    def test_scheme_less_and_www_links_are_normalised(self):
        message = make_message("çay www.amazon.com.tr/urun ve t.me/firsatz/9")
        self.assertEqual(
            [item["url"] for item in bot.extract_links(message)],
            ["https://www.amazon.com.tr/urun", "https://t.me/firsatz/9"],
        )

    def test_missing_links_respects_limit(self):
        buttons = [SimpleNamespace(text=f"b{i}", url=f"https://amzn.to/{i}", type=None) for i in range(6)]
        message = make_message("çay", buttons=buttons)
        self.assertEqual(len(bot.missing_links(message)), bot.LINK_APPENDIX_LIMIT)

    def test_inline_keyboard_is_rebuilt_for_bot_api(self):
        buttons = [SimpleNamespace(text="Fırsata Git", url="https://amzn.to/btn", type=None)]
        message = make_message("çay", buttons=buttons)
        self.assertEqual(bot.build_inline_keyboard(message), {"inline_keyboard": [[
            {"text": "Fırsata Git", "url": "https://amzn.to/btn"},
        ]]})

    def test_non_url_buttons_are_ignored(self):
        callback = SimpleNamespace(text="Onayla", url=None, type=SimpleNamespace(data=b"1"))
        message = make_message("çay", buttons=[callback])
        self.assertIsNone(bot.build_inline_keyboard(message))
        self.assertEqual(bot.extract_links(message), [])


class ComposeMessageTest(unittest.TestCase):
    def test_message_appendix_and_footer_together(self):
        text = "ÇAY 5 TL"
        entity = tl_types.MessageEntityTextUrl(offset=4, length=2, url="https://amzn.to/5")
        composed = bot.compose_message(
            make_message(text, entities=[entity]),
            footer_label=bot.FOOTER_LABEL, footer_name="FırsatZ",
            footer_url="https://t.me/firsatz/9",
        )
        self.assertTrue(composed["text"].startswith(text))
        self.assertIn("🔗 https://amzn.to/5", composed["text"])
        self.assertTrue(composed["text"].endswith("Fırsatı Gönderen: FırsatZ"))
        self.assertEqual(bot.footer_entity(composed, "https://t.me/firsatz/9"), [{
            "type": "text_link", "offset": composed["footer_offset"],
            "length": bot.utf16_length("FırsatZ"), "url": "https://t.me/firsatz/9",
        }])

    def test_long_body_is_truncated_but_footer_survives(self):
        composed = bot.compose_message(
            make_message("a" * 5000), limit=200,
            footer_label=bot.FOOTER_LABEL, footer_name="FırsatZ", footer_url="https://t.me/x/1",
        )
        self.assertLessEqual(len(composed["text"]), 200)
        self.assertTrue(composed["text"].endswith("Fırsatı Gönderen: FırsatZ"))
        self.assertEqual(len(composed["body"]), 200 - len("\n\nFırsatı Gönderen: FırsatZ"))

    def test_entities_outside_truncated_body_are_dropped(self):
        entity = tl_types.MessageEntityBold(offset=0, length=4000)
        message = make_message("b" * 4000, entities=[entity])
        body = "kısa gövde"
        self.assertEqual(bot.entities_for_text(message, body), [])
        self.assertEqual(bot.entities_for_text(message, "b" * 4000), [entity])

    def test_bot_api_entities_map_types_and_skip_unknown(self):
        entities = [
            tl_types.MessageEntityBold(offset=0, length=3),
            tl_types.MessageEntityTextUrl(offset=4, length=11, url="https://amzn.to/z"),
            tl_types.MessageEntityUnknown(offset=0, length=1),
            tl_types.MessageEntityCode(offset=20, length=2),
        ]
        message = make_message("x" * 30, entities=entities)
        converted = bot.bot_api_entities(message, "x" * 30)
        self.assertEqual([item["type"] for item in converted], ["bold", "text_link", "code"])
        self.assertEqual(converted[1]["url"], "https://amzn.to/z")
        self.assertNotIn("date_time", [item["type"] for item in converted])

    def test_bot_api_entity_clamps_offsets(self):
        entity = tl_types.MessageEntityBold(offset=2, length=10)
        self.assertEqual(bot.bot_api_entity(entity, 5), {"type": "bold", "offset": 2, "length": 3})
        self.assertIsNone(bot.bot_api_entity(entity, 2))


class MediaNamingTest(unittest.TestCase):
    """Eski hata: bytes olarak yeniden yüklenen medya 'unnamed' adıyla gidiyordu."""

    def test_photo_gets_real_extension(self):
        message = make_message("çay", file=SimpleNamespace(name=None, ext=".jpg", mime_type="image/jpeg"))
        self.assertEqual(bot.media_upload_name(message), "firsat_1.jpg")

    def test_document_keeps_original_name(self):
        file = SimpleNamespace(name="kupon.pdf", ext=".pdf", mime_type="application/pdf")
        self.assertEqual(bot.media_upload_name(make_message("çay", file=file, message_id=7)), "kupon.pdf")

    def test_extension_is_guessed_from_mime_when_missing(self):
        file = SimpleNamespace(name=None, ext=None, mime_type="video/mp4")
        self.assertEqual(bot.media_upload_name(make_message("çay", file=file)), "firsat_1.mp4")

    def test_jpeg_extension_is_normalised(self):
        file = SimpleNamespace(name=None, ext=".jpeg", mime_type=None)
        self.assertEqual(bot.media_upload_name(make_message("çay", file=file)), "firsat_1.jpg")

    def test_media_buffer_carries_the_name(self):
        buffer = bot.media_buffer(b"veri", "firsat_9.jpg")
        self.assertEqual(buffer.name, "firsat_9.jpg")
        self.assertEqual(buffer.getvalue(), b"veri")

    def test_photo_descriptor(self):
        message = make_message("çay", file=SimpleNamespace(size=1234))
        descriptor = bot.bot_media_descriptor(message)
        self.assertEqual(descriptor["kind"], "photo")
        self.assertEqual(descriptor["mime"], "image/jpeg")
        self.assertEqual(descriptor["filename"], "firsat_1.jpg")
        self.assertEqual(descriptor["size"], 1234)

    def test_document_descriptor_uses_original_name(self):
        doc = tl_types.Document(
            id=1, access_hash=1, file_reference=b"", date=None, mime_type="video/mp4", size=999,
            dc_id=1, attributes=[tl_types.DocumentAttributeFilename(file_name="urun.mp4")],
        )
        message = make_message("çay", media=tl_types.MessageMediaDocument(document=doc),
                               file=SimpleNamespace(size=999))
        descriptor = bot.bot_media_descriptor(message)
        self.assertEqual((descriptor["kind"], descriptor["filename"], descriptor["size"]),
                         ("video", "urun.mp4", 999))

    def test_webpage_only_message_has_no_media(self):
        message = make_message("çay fırsatı", webpage="https://amzn.to/x")
        self.assertIsNone(bot.bot_media_descriptor(message))

    def test_video_attributes_are_reused_on_reupload(self):
        """Telethon metadata bulamazsa 1:1 video üretir; oran korunmalı."""
        video = tl_types.DocumentAttributeVideo(duration=12.5, w=1920, h=1080)
        doc = tl_types.Document(
            id=1, access_hash=1, file_reference=b"", date=None, mime_type="video/mp4", size=10,
            dc_id=1, attributes=[tl_types.DocumentAttributeFilename(file_name="klip.mp4"), video],
        )
        message = make_message("çay", media=tl_types.MessageMediaDocument(document=doc))
        attributes = bot.reupload_attributes(message)
        self.assertEqual((attributes[0].w, attributes[0].h, attributes[0].duration), (1920, 1080, 12.5))

    def test_photos_have_no_attribute_override(self):
        self.assertIsNone(bot.reupload_attributes(make_message("çay")))


class MultipartTest(unittest.TestCase):
    def test_body_contains_fields_and_file(self):
        body, content_type = bot.encode_multipart(
            {"chat_id": -100, "caption": "çay"},
            [("photo", "firsat.jpg", "image/jpeg", b"JPEGVERISI")],
        )
        self.assertIn("multipart/form-data; boundary=", content_type)
        boundary = content_type.split("boundary=")[1].encode()
        self.assertIn(b'name="chat_id"', body)
        self.assertIn(b"-100", body)
        self.assertIn(b'name="photo"; filename="firsat.jpg"', body)
        self.assertIn(b"Content-Type: image/jpeg", body)
        self.assertIn(b"JPEGVERISI", body)
        self.assertTrue(body.endswith(b"--" + boundary + b"--\r\n"))

    def test_none_and_empty_fields_are_skipped(self):
        body, _ = bot.encode_multipart({"a": None, "b": "", "c": "x"}, [])
        self.assertNotIn(b'name="a"', body)
        self.assertNotIn(b'name="b"', body)
        self.assertIn(b'name="c"', body)


class BotApiSendTest(unittest.TestCase):
    """send_bot_ping ve send_bot_media gerçekten doğru gövdeyi POST etmeli."""

    @staticmethod
    def _fake_response():
        response = io.BytesIO(json.dumps({"ok": True, "result": {}}).encode())
        response.__enter__ = lambda self: self
        response.__exit__ = lambda self, *a: False
        return response

    def test_ping_sends_entities_and_keyboard(self):
        with mock.patch("bot.urllib.request.urlopen", return_value=self._fake_response()) as urlopen:
            ok, _ = asyncio.run(bot.send_bot_ping(
                "123:ABC", -100, "ÇAY\n\nFırsatı Gönderen: FırsatZ",
                entities=[{"type": "text_link", "offset": 5, "length": 3, "url": "https://t.me/x/1"}],
                keyboard={"inline_keyboard": [[{"text": "Fırsata Git", "url": "https://amzn.to/b"}]]},
            ))
        self.assertTrue(ok)
        request = urlopen.call_args[0][0]
        self.assertTrue(request.full_url.endswith("/bot123:ABC/sendMessage"))
        payload = json.loads(request.data.decode())
        entities = json.loads(payload["entities"])
        self.assertEqual(entities[0]["type"], "text_link")
        self.assertEqual(entities[0]["url"], "https://t.me/x/1")
        keyboard = json.loads(payload["reply_markup"])
        self.assertEqual(keyboard["inline_keyboard"][0][0]["text"], "Fırsata Git")
        self.assertEqual(keyboard["inline_keyboard"][0][0]["url"], "https://amzn.to/b")

    def test_media_posts_multipart_to_sendphoto(self):
        with mock.patch("bot.urllib.request.urlopen", return_value=self._fake_response()) as urlopen:
            ok, detail = asyncio.run(bot.send_bot_media(
                "123:ABC", -100, kind="photo", filename="firsat_1.jpg", mime_type="image/jpeg",
                data=b"JPEG", caption="çay", entities=[{"type": "bold", "offset": 0, "length": 3}],
            ))
        self.assertTrue(ok, detail)
        request = urlopen.call_args[0][0]
        self.assertTrue(request.full_url.endswith("/bot123:ABC/sendPhoto"))
        self.assertTrue(request.headers["Content-type"].startswith("multipart/form-data; boundary="))
        self.assertIn(b'name="photo"; filename="firsat_1.jpg"', request.data)
        self.assertIn(b'name="caption_entities"', request.data)

    def test_document_kind_uses_senddocument(self):
        with mock.patch("bot.urllib.request.urlopen", return_value=self._fake_response()) as urlopen:
            asyncio.run(bot.send_bot_media(
                "123:ABC", -100, kind="document", filename="kupon.pdf",
                mime_type="application/pdf", data=b"PDF",
            ))
        self.assertTrue(urlopen.call_args[0][0].full_url.endswith("/sendDocument"))


class ConfigFlagTest(unittest.TestCase):
    def test_common_true_and_false_spellings(self):
        for value in (True, "true", "1", "evet", "açık", 1):
            self.assertTrue(bot.config_flag(value), repr(value))
        for value in (False, "false", "0", "hayır", "kapalı", 0):
            self.assertFalse(bot.config_flag(value), repr(value))

    def test_missing_values_fall_back_to_default(self):
        self.assertTrue(bot.config_flag(None))
        self.assertFalse(bot.config_flag(None, False))
        self.assertFalse(bot.config_flag("yok", False))
        self.assertTrue(bot.config_flag("", True))
