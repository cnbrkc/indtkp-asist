"""bot.py içindeki saf (ağ gerektirmeyen) fonksiyonların testleri.

Çalıştırma:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
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
        with mock.patch.dict(os.environ, CheckEnvironmentTest.good_env, clear=False):
            self.assertEqual(bot.check_environment(config), [])


if __name__ == "__main__":
    unittest.main()
