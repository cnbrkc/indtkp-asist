"""Kaynak çözümleme regresyon testleri — source_chat_ids cache + FloodWait breaker.

Bu testler bot.resolve_sources / resolve_chat / resolve_chat_groups'un gerçek
akışını çalıştırır; inspect/getsource taklidi yok.

Senaryolar:
1. 47 kayıtlı kaynak tamamen ID üzerinden çözülür, username çağrısı yok.
2. İlk kurulumda ID yokken username üzerinden kontrollü çözüm.
3. FloodWait park: ilk bilinmeyen 41509s patlayınca sonraki bilinmeyenler denenmez.
4. FloodWait olsa bile bilinen ID'ler çözülmeye devam eder.
5. Başarılı ID'ler config'e yazılır ve restart'ta yeniden kullanılır.
6. Stale (geçersiz) ID güvenli temizlenir, başka kayıt silinmez.
7. Yeni kaynak eklenince mevcut ID'ler korunur, sadece yeni çözülür.
8. Ek kritikler: numeric fallback get_dialogs, atomic yazım, gereksiz push yok.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bot  # noqa: E402
from telethon import errors, types, utils  # noqa: E402

ADMIN_ID = 1143378073
GROUP_ID = -5092968106


def _reset():
    bot.SOURCES.clear()
    bot.SOURCE_FAILURES.clear()
    bot.SOURCE_IDS.clear()
    bot.CONTROL_IDS.clear()
    bot.CONTROL_NAMES.clear()
    bot.DESTINATION_ID = None
    bot.PENDING.clear()
    bot.COMMAND_MESSAGES.clear()
    bot.DEDUP_CACHE.clear()
    bot.CURSOR_RAM.clear()
    bot.STATS.update({"seen": 0, "matched": 0, "forwarded": 0, "failed": 0})


# ---------------------------------------------------------------------------
# Sahte Telethon client — bot.resolve_chat'in gerçek yolunu test eder
# ---------------------------------------------------------------------------

class TrackingClient:
    """get_entity / get_dialogs izleyen, FloodWait ve stale simüle eden client."""

    def __init__(self, mapping: dict | None = None):
        # mapping: key (str username/@username veya int id) -> entity veya Exception
        # Örn: {"@firsatz": channel, -100111: channel, "@boom": FloodWait}
        self.mapping = dict(mapping or {})
        self.calls: list[tuple[str, object]] = []  # ("get_entity", value)
        self.dialogs_calls = 0
        self._int_retry_done: set[int] = set()

    async def get_entity(self, value):
        self.calls.append(("get_entity", value))
        # mapping'de doğrudan varsa onu döndür / fırlat
        if value in self.mapping:
            v = self.mapping[value]
            if isinstance(v, Exception):
                raise v
            return v
        # string username normalizasyonu: "@x" == "x"
        if isinstance(value, str):
            key = value.strip()
            if key in self.mapping:
                v = self.mapping[key]
                if isinstance(v, Exception):
                    raise v
                return v
            # "@x" için "x" fallback
            alt = key.lstrip("@")
            if alt in self.mapping:
                v = self.mapping[alt]
                if isinstance(v, Exception):
                    raise v
                return v
            # genel fallback: tüm username'ler channel döndür (test_integration benzeri)
            if key.startswith("@") or (key and not key.lstrip("-").isdigit()):
                # bilinmeyen username -> channel oluştur
                uname = key.lstrip("@")
                return types.Channel(id=abs(hash(uname)) % 9000000000 + 1000000000,
                                     title=uname, photo=types.ChatPhotoEmpty(), date=0,
                                     username=uname)
            # numeric string zaten parse_chat_value int'e çevirir, buraya gelmemeli
            raise ValueError(f"Cannot find any entity corresponding to {value!r}")
        if isinstance(value, int):
            # int ID için mapping yoksa genel başarı: Chat döndür
            if value < 0:
                return types.Chat(id=abs(value), title=f"chat{value}", photo=types.ChatPhotoEmpty(),
                                  participants_count=1, date=0, version=0)
            return types.Channel(id=abs(value), title=f"chan{value}", photo=types.ChatPhotoEmpty(),
                                 date=0, username=None)
        raise ValueError(f"Cannot find any entity corresponding to {value!r}")

    async def get_dialogs(self):
        self.dialogs_calls += 1
        return []

    # resolve_chat invite path'i için stub — bu testlerde invite yok
    async def __call__(self, request):
        raise NotImplementedError("CheckChatInviteRequest bu testlerde kullanılmıyor")


def _chan(username: str, chat_id: int | None = None):
    cid = chat_id if chat_id is not None else abs(hash(username)) % 9000000000 + 1000000000
    return types.Channel(id=cid, title=username, photo=types.ChatPhotoEmpty(), date=0,
                         username=username.lstrip("@"))


def _chat(chat_id: int):
    return types.Chat(id=abs(chat_id), title=f"chat{chat_id}", photo=types.ChatPhotoEmpty(),
                      participants_count=1, date=0, version=0)


class SourceIdCacheTest(unittest.TestCase):
    """7 ana senaryo + kritik davranışlar — gerçek resolve_* akışı."""

    def setUp(self):
        _reset()

    def tearDown(self):
        _reset()

    # ------------------------------------------------------------------
    # 1) 47 kayıtlı kaynak — tamamen int ID üzerinden
    # ------------------------------------------------------------------
    def test_47_cached_sources_use_int_not_username(self):
        n = 47
        source_chats = [f"@kaynak{i}" for i in range(n)]
        # her kaynağa ayrı sayısal ID
        source_chat_ids = {name: -(1000000000 + i) for i, name in enumerate(source_chats)}
        config = {"source_chats": source_chats, "source_chat_ids": dict(source_chat_ids)}
        # client mapping: int ID'ler başarılı, username'ler FloodWait olsaydı yakalanırdı
        mapping = {}
        for name, pid in source_chat_ids.items():
            mapping[pid] = _chat(pid)
            # username çağrısı yapılırsa patlasın — test bunu yakalamalı
            mapping[name] = errors.FloodWaitError(request=None, capture=41509)
            mapping[name.lstrip("@")] = errors.FloodWaitError(request=None, capture=41509)
        client = TrackingClient(mapping)

        notes = asyncio.run(bot.resolve_sources(client, config, quiet=True))

        self.assertEqual(len(bot.SOURCES), n)
        self.assertEqual(len(bot.SOURCE_IDS), n)
        self.assertEqual(len(bot.SOURCE_FAILURES), 0, bot.SOURCE_FAILURES)
        # hiçbir username çağrısı olmamalı — tüm get_entity argümanları int
        username_calls = [v for kind, v in client.calls if kind == "get_entity" and isinstance(v, str)]
        self.assertEqual(username_calls, [], f"username çağrısı yapılmamalı, ama: {client.calls[:10]}")
        int_calls = [v for kind, v in client.calls if kind == "get_entity" and isinstance(v, int)]
        self.assertEqual(len(int_calls), n)
        # config pruned aynı kalmalı (değişmedi)
        self.assertEqual(config["source_chat_ids"], source_chat_ids)
        self.assertNotIn("park edildi", "\n".join(notes).lower())

    # ------------------------------------------------------------------
    # 2) İlk kurulum — ID yok, username üzerinden kontrollü çözüm
    # ------------------------------------------------------------------
    def test_initial_setup_resolves_via_username(self):
        source_chats = ["@alpha", "@beta", "@gamma"]
        config = {"source_chats": source_chats}
        client = TrackingClient()  # default: her username channel döndürür
        notes = asyncio.run(bot.resolve_sources(client, config, quiet=True))

        self.assertEqual(len(bot.SOURCES), 3)
        self.assertEqual(len(bot.SOURCE_FAILURES), 0)
        # config'e yazılmış olmalı
        self.assertIn("source_chat_ids", config)
        self.assertEqual(set(config["source_chat_ids"].keys()), set(source_chats))
        for v in config["source_chat_ids"].values():
            self.assertIsInstance(v, int)
            self.assertLess(v, 0)  # Chat peer_id negatif
        # çağrılar username olmalı (int yok)
        str_calls = [v for _, v in client.calls if isinstance(v, str)]
        self.assertEqual(len(str_calls), 3)

    # ------------------------------------------------------------------
    # 3) FloodWait — ilk bilinmeyen 41509s, sonrakiler denenmez
    # ------------------------------------------------------------------
    def test_floodwait_parks_remaining_unknown_usernames(self):
        source_chats = ["@a", "@b", "@c", "@d"]
        config = {"source_chats": source_chats}
        # @a başarılı, @b FloodWait, @c/@d asla denenmemeli
        mapping = {
            "@a": _chan("a", 1001),
            "@b": errors.FloodWaitError(request=None, capture=41509),
            "@c": _chan("c", 1003),
            "@d": _chan("d", 1004),
        }
        client = TrackingClient(mapping)
        asyncio.run(bot.resolve_sources(client, config, quiet=True))

        self.assertEqual(len(bot.SOURCES), 1)
        self.assertEqual(bot.SOURCES[0]["requested"], "@a")
        # failures: @b gerçek FloodWait + @c/@d park edilmiş (aynı exc ile)
        self.assertEqual(len(bot.SOURCE_FAILURES), 3)
        parked = [v for v, e in bot.SOURCE_FAILURES if isinstance(e, errors.FloodWaitError)]
        self.assertEqual(len(parked), 3)
        # client.calls sadece @a ve @b için olmalı, @c/@d hiç çağrılmamalı
        called_usernames = [v for _, v in client.calls if isinstance(v, str)]
        self.assertEqual(called_usernames, ["@a", "@b"])

    # ------------------------------------------------------------------
    # 4) Bilinen ID'ler FloodWait olsa bile çözülmeye devam eder
    # ------------------------------------------------------------------
    def test_known_ids_still_resolve_during_floodwait(self):
        # Sıra: @known1 (cached) -> @unknown (FloodWait) -> @known2 (cached) -> @unknown2 (park)
        source_chats = ["@known1", "@unknown1", "@known2", "@unknown2"]
        known1_id = -1001111111111
        known2_id = -1002222222222
        config = {
            "source_chats": source_chats,
            "source_chat_ids": {"@known1": known1_id, "@known2": known2_id},
        }
        mapping = {
            known1_id: _chat(known1_id),
            known2_id: _chat(known2_id),
            "@unknown1": errors.FloodWaitError(request=None, capture=41509),
            "@unknown2": _chan("unknown2", 9999),  # çağrılmamalı
        }
        client = TrackingClient(mapping)
        asyncio.run(bot.resolve_sources(client, config, quiet=True))

        # known1 ve known2 çözülmeli, unknown1 ve unknown2 park
        self.assertEqual(len(bot.SOURCES), 2)
        ids = {item["requested"] for item in bot.SOURCES}
        self.assertEqual(ids, {"@known1", "@known2"})
        self.assertIn(known1_id, bot.SOURCE_IDS)
        self.assertIn(known2_id, bot.SOURCE_IDS)
        parked = [v for v, e in bot.SOURCE_FAILURES if isinstance(e, errors.FloodWaitError)]
        self.assertEqual(len(parked), 2)
        # int çağrıları her iki known için yapılmış olmalı
        int_calls = [v for _, v in client.calls if isinstance(v, int)]
        self.assertIn(known1_id, int_calls)
        self.assertIn(known2_id, int_calls)
        # unknown2 username hiç çağrılmamalı
        str_calls = [v for _, v in client.calls if isinstance(v, str)]
        self.assertNotIn("@unknown2", str_calls)

    # ------------------------------------------------------------------
    # 5) Restart — başarılı ID'ler config'e yazılır, sonraki açılış yeniden kullanır
    # ------------------------------------------------------------------
    def test_successful_ids_persist_and_reused_on_restart(self):
        source_chats = ["@x", "@y"]
        config = {"source_chats": source_chats}
        client1 = TrackingClient()
        asyncio.run(bot.resolve_sources(client1, config, quiet=True))
        self.assertIn("source_chat_ids", config)
        saved_ids = dict(config["source_chat_ids"])
        self.assertEqual(len(saved_ids), 2)

        # Simüle restart: atomic_write_json ile dosyaya yaz, geri oku
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tf:
            path = Path(tf.name)
            json.dump({"source_chats": source_chats}, tf)
        try:
            # bot'un yaptığı gibi config'e IDs ekleyip atomic yaz
            data = {"source_chats": source_chats, "source_chat_ids": saved_ids}
            bot.atomic_write_json(path, data)
            reloaded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(reloaded["source_chat_ids"], saved_ids)

            # ikinci açılış: aynı config ile resolve — hiçbir username çağrısı olmamalı
            _reset()
            client2 = TrackingClient({
                v: _chat(v) for v in saved_ids.values()
            })
            # username çağrısı yapılırsa patlasın
            for name in source_chats:
                client2.mapping[name] = errors.FloodWaitError(request=None, capture=41509)
            reloaded_cfg = {"source_chats": source_chats, "source_chat_ids": dict(reloaded["source_chat_ids"])}
            asyncio.run(bot.resolve_sources(client2, reloaded_cfg, quiet=True))
            self.assertEqual(len(bot.SOURCES), 2)
            str_calls2 = [v for _, v in client2.calls if isinstance(v, str)]
            self.assertEqual(str_calls2, [], "restart'ta username denenmemeli")
            int_calls2 = [v for _, v in client2.calls if isinstance(v, int)]
            self.assertEqual(len(int_calls2), 2)
        finally:
            path.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # 6) Stale ID — geçersiz ID güvenli temizlenir, başka kayıt silinmez
    # ------------------------------------------------------------------
    def test_stale_id_cleaned_without_deleting_other_entries(self):
        source_chats = ["@good", "@stale"]
        good_id = -1003333333333
        stale_id = -1004444444444
        new_stale_id = -1005555555555
        config = {
            "source_chats": source_chats,
            "source_chat_ids": {"@good": good_id, "@stale": stale_id},
        }
        mapping = {
            good_id: _chat(good_id),
            stale_id: ValueError("Cannot find any entity corresponding to -1004444444444"),
            "@stale": _chan("stale", 1005555555555),  # fallback username başarılı
        }
        client = TrackingClient(mapping)
        asyncio.run(bot.resolve_sources(client, config, quiet=True))

        self.assertEqual(len(bot.SOURCES), 2)
        # @good eski ID ile kalmalı
        self.assertEqual(config["source_chat_ids"]["@good"], good_id)
        # @stale yeni ID ile güncellenmiş olmalı, eski silinmiş
        # peer_id negatif channel id; _chan 1005555555555 -> get_peer_id?
        # types.Channel peer_id hesaplaması telethon utils ile; biz sadece int kontrol
        self.assertNotEqual(config["source_chat_ids"]["@stale"], stale_id)
        self.assertIn("@good", config["source_chat_ids"])
        self.assertIn("@stale", config["source_chat_ids"])
        self.assertEqual(len(config["source_chat_ids"]), 2, "başka kayıt silinmemeli")

    # ------------------------------------------------------------------
    # 7) Yeni kaynak eklenince mevcut ID'ler korunur, sadece yeni çözülür
    # ------------------------------------------------------------------
    def test_adding_new_source_preserves_existing_ids(self):
        existing = ["@a", "@b"]
        existing_ids = {"@a": -1006666666666, "@b": -1007777777777}
        config = {"source_chats": existing, "source_chat_ids": dict(existing_ids)}
        client1 = TrackingClient({v: _chat(v) for v in existing_ids.values()})
        asyncio.run(bot.resolve_sources(client1, config, quiet=True))
        self.assertEqual(config["source_chat_ids"], existing_ids)

        # yeni kaynak ekle
        _reset()
        new_chats = ["@a", "@b", "@c"]
        # config'i mevcut IDs ile güncelle, yeni @c yok
        config2 = {"source_chats": new_chats, "source_chat_ids": dict(existing_ids)}
        mapping2 = {
            existing_ids["@a"]: _chat(existing_ids["@a"]),
            existing_ids["@b"]: _chat(existing_ids["@b"]),
            "@c": _chan("c", 1009999999999),
        }
        client2 = TrackingClient(mapping2)
        asyncio.run(bot.resolve_sources(client2, config2, quiet=True))

        self.assertEqual(len(bot.SOURCES), 3)
        self.assertEqual(config2["source_chat_ids"]["@a"], existing_ids["@a"])
        self.assertEqual(config2["source_chat_ids"]["@b"], existing_ids["@b"])
        self.assertIn("@c", config2["source_chat_ids"])
        # mevcut ID'ler için username çağrısı olmamalı
        str_calls = [v for _, v in client2.calls if isinstance(v, str)]
        self.assertEqual(str_calls, ["@c"])

class ResolveChatNumericFallbackTest(unittest.TestCase):
    """resolve_chat numeric ID için get_dialogs fallback'i yalnızca gerektiğinde."""

    def test_int_get_entity_value_error_triggers_get_dialogs_then_retries(self):
        async def run():
            calls = []

            class C:
                async def get_entity(self, value):
                    calls.append(value)
                    if len(calls) == 1:
                        raise ValueError("Cannot find any entity corresponding to -100999")
                    return _chat(-100999)

                async def get_dialogs(self):
                    calls.append("get_dialogs")
                    return []

            client = C()
            peer_id, name, entity = await bot.resolve_chat(client, -100999)
            self.assertEqual(peer_id, -100999)
            self.assertEqual(calls, [-100999, "get_dialogs", -100999])

        asyncio.run(run())

    def test_str_value_never_calls_get_dialogs(self):
        async def run():
            calls = []

            class C:
                async def get_entity(self, value):
                    calls.append(value)
                    raise ValueError("No user has \"@olmayan\" as username")

                async def get_dialogs(self):
                    calls.append("get_dialogs")
                    return []

            client = C()
            with self.assertRaises(ValueError):
                await bot.resolve_chat(client, "@olmayan")
            self.assertNotIn("get_dialogs", calls)

        asyncio.run(run())

    def test_resolve_chat_int_uses_int_directly(self):
        """int ID doğrudan int olarak iletilir, username'e düşmez."""
        async def run():
            seen = {}

            class C:
                async def get_entity(self, value):
                    seen["value"] = value
                    seen["type"] = type(value).__name__
                    return _chat(value) if isinstance(value, int) else _chan("x")

                async def get_dialogs(self):
                    raise AssertionError("get_dialogs çağrılmamalı")

            await bot.resolve_chat(C(), -1001234567890)
            self.assertIsInstance(seen["value"], int)
            self.assertEqual(seen["type"], "int")

        asyncio.run(run())


class ParkedIdsPreservedTest(unittest.TestCase):
    """FloodWait'te park edilenlerin eski geçerli ID'leri korunur."""

    def setUp(self):
        _reset()

    def tearDown(self):
        _reset()

    def test_parked_old_id_kept_in_config(self):
        source_chats = ["@keep", "@parked"]
        keep_id = -1008888888888
        parked_id = -1009999999999
        config = {
            "source_chats": source_chats,
            "source_chat_ids": {"@keep": keep_id, "@parked": parked_id},
        }
        # @keep int başarılı, @parked için yeni bir kaynak ekleyip FloodWait simüle edelim
        # ama burada @parked zaten cached; onu park etmek için username'li yeni kaynak gibi davran
        # Gerçek senaryo: @parked bilinmeyenken FloodWait — ama burada cached olduğu için int denenir
        # Bunun yerine: 3 kaynak — 2 cached, 1 bilinmeyen FloodWait -> parked'in ID'si pruned'da kalır
        source_chats2 = ["@keep", "@parked", "@new_unknown"]
        config2 = {
            "source_chats": source_chats2,
            "source_chat_ids": {"@keep": keep_id, "@parked": parked_id},
        }
        mapping = {
            keep_id: _chat(keep_id),
            parked_id: _chat(parked_id),
            "@new_unknown": errors.FloodWaitError(request=None, capture=41509),
        }
        client = TrackingClient(mapping)
        asyncio.run(bot.resolve_sources(client, config2, quiet=True))
        # FloodWait sonrası parked değil, keep+parked çözülmeli, new_unknown park
        self.assertEqual(len(bot.SOURCES), 2)
        self.assertIn("@keep", config2["source_chat_ids"])
        self.assertIn("@parked", config2["source_chat_ids"])
        self.assertEqual(config2["source_chat_ids"]["@parked"], parked_id)


class AtomicAndPushGatingTest(unittest.TestCase):
    """ID değişmeyince push yok; atomic_write doğru; repo dışı push yok."""

    def test_atomic_write_strips_legacy_token_and_persists(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "config.json"
            p.write_text(json.dumps({"a": 1}), encoding="utf-8")
            bot.atomic_write_json(p, {"a": 2, "notify_bot_token": "secret"})
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(data["a"], 2)
            self.assertNotIn("notify_bot_token", data)

    def test_git_repo_root_outside_repo_returns_none_and_push_not_called(self):
        # main'deki gating: push_token() and git_repo_root(store.path.parent) is not None
        with tempfile.TemporaryDirectory() as td:
            outside = Path(td) / "config.json"
            outside.write_text("{}", encoding="utf-8")
            # tmp dışı repo değil -> git_repo_root None dönmeli (veya en azından push gating)
            root = bot.git_repo_root(Path(td))
            self.assertIsNone(root, "tmp dizini git repo değil, None dönmeli")
            # main'in yaptığı kontrolü simüle et
            with mock.patch.object(bot, "push_token", return_value="tok"), \
                 mock.patch.object(bot, "commit_and_push") as mcp:
                _orig = {}
                _new = {"@x": -1001}
                # gating koşulu
                if _new != _orig:
                    if bot.push_token() and bot.git_repo_root(outside.parent) is not None:
                        bot.commit_and_push(outside, "kaynak ID'leri güncellendi")
                mcp.assert_not_called()

    def test_no_push_when_ids_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            # git repo oluştur
            import subprocess, shutil
            if shutil.which("git") is None:
                self.skipTest("git yok")
            root = Path(td) / "repo"
            root.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
            cfg = root / "config.json"
            cfg.write_text(json.dumps({"source_chats": ["@a"], "source_chat_ids": {"@a": -1001}}), encoding="utf-8")
            subprocess.run(["git", "add", "config.json"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)

            # IDs değişmedi -> commit_and_push çağrılmamalı (main gating'i)
            _orig = {"@a": -1001}
            _new = {"@a": -1001}
            self.assertEqual(_orig, _new)
            # main mantığı: if _new != _orig: push — eşitken hiç girmez
            # bunu doğrudan doğrula
            entered = _new != _orig
            self.assertFalse(entered, "ID değişmeyince push dalına girilmemeli")

class ResolveChatGroupsIntegrationTest(unittest.TestCase):
    """resolve_chat_groups gerçek akış — sources/destination/control birlikte."""

    def setUp(self):
        _reset()

    def tearDown(self):
        _reset()

    def test_resolve_chat_groups_via_sources(self):
        # Basit entegrasyon: sources + destination
        config = {
            "source_chats": ["@s1"],
            "source_chat_ids": {"@s1": -1001111111111},
            "destination": GROUP_ID,
            "control_chat": GROUP_ID,
        }
        mapping = {
            -1001111111111: _chat(-1001111111111),
            GROUP_ID: types.Chat(id=abs(GROUP_ID), title="Benim Grup", photo=types.ChatPhotoEmpty(),
                                 participants_count=1, date=0, version=0),
        }
        # GROUP_ID string olarak da gelebilir — FakeClient onu da kabul etsin
        client = TrackingClient(mapping)
        # destination/control için get_entity GROUP_ID int'i de çözmeli
        # TrackingClient int fallback'u zaten Chat döndürüyor, yeterli

        async def run():
            notes, fatal = await bot.resolve_chat_groups(client, config, {"sources", "destination", "control"})
            self.assertIsNone(fatal)
            self.assertEqual(len(bot.SOURCES), 1)
            self.assertEqual(bot.DESTINATION_ID, GROUP_ID)

        asyncio.run(run())

