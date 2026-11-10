"""Davranış sabitleme: bugüne dek hiçbir testin geçmediği yollar ve iki yapısal bekçi.

Altın referans (test_golden_reference.py) fiyatlı mesajların çıktısını sabitler. Bu
dosya onun ulaşmadığı yan yolları sabitler:

  GitHub        : dispatch_next_run, _github_api, _push_via_api, push_token ve ürün
                  takibinin Contents API yazımı (main() içindeki persist_products).
  webdetakip    : /ekle veya /cikar → 7 akışının ürün takibine devri.
  Tekrar rozeti : düzenleme reddedildiğinde izlenen dallar ve sahipsiz kalmış
                  rezervasyonun devralınması.
  Benzer çiftler: aynı işi yapar görünen yardımcıların bugünkü farkları.
  Bekçiler      : tanımsız ad taraması; Akakçe işçisinin yalıtılmış kipte açılması.

Buradaki her iddia "bugün böyle çalışıyor" demektir; "doğrusu budur" demek DEĞİLDİR.
Düzeltilmemiş kusurların (2 ve 3 numara) bugünkü görünümü KnownDefectPins sınıfında,
kusur başına tek testle durur. Bir kusur düzeltildiğinde bu dosyada yalnızca o test
(ve tanımsız ad kusurlarında KNOWN_UNDEFINED listesi) bilinçli olarak güncellenir;
buradaki başka bir testin oynaması düzeltmenin beklenmeyen bir yan etkisidir. Altın
referansta 1 ve 2 numaralı kusurların dokunduğu kayıtlar ayrıca yeniden üretilir
(3 numara dokunmaz). 1 numaralı kusur düzeltildi: iğnesi kaldırıldı, doğru davranışı
test_bot.py'deki MessageSanitizationTest sabitler.

Ağ yoktur: Telethon yerine test_integration.FakeClient, HTTP yerine
urllib.request.urlopen'ın sahtesi çalışır; aradaki her şey üretim kodudur.
"""

from __future__ import annotations

import asyncio
import base64
import builtins
import contextlib
import io
import json
import logging
import os
import subprocess
import symtable
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Awaitable, Callable
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bot  # noqa: E402
import price_filter  # noqa: E402
import private_bot as private  # noqa: E402
import product_tracker  # noqa: E402
import test_golden_reference as golden  # noqa: E402
import test_integration as ti  # noqa: E402
import test_private_bot as tpb  # noqa: E402
from telethon import errors  # noqa: E402

OWNER = ti.ADMIN_ID   # sahte istemcide hesap sahibi (SELF_ID) aynı zamanda yöneticidir


# --- Ağ sınırı -----------------------------------------------------------------

class Wire:
    """``urllib.request.urlopen`` yerine geçer: isteği saklar, sıradaki hazır yanıtı verir.

    Yanıt sözlük ya da bayt ise 200 gövdesidir; istisna ise fırlatılır. Hazır yanıt
    kalmadıysa istek yine kaydedilir ve reddedilir (testler ``calls`` listesini sayar).
    """

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, request: urllib.request.Request, timeout: float | None = None) -> io.BytesIO:
        self.calls.append({
            "method": request.get_method(),
            "url": request.full_url,
            # Başlık adları tel üstünde büyük/küçük harfe duyarsızdır.
            "headers": {name.lower(): value for name, value in request.header_items()},
            "body": json.loads(request.data) if request.data else None,
            "timeout": timeout,
        })
        if not self.answers:
            raise AssertionError(f"hazır yanıtı olmayan istek: {request.get_method()} {request.full_url}")
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return io.BytesIO(answer if isinstance(answer, bytes) else json.dumps(answer).encode())

    def sent(self) -> list[tuple[str, str]]:
        return [(call["method"], call["url"]) for call in self.calls]


def http_error(code: int, body: str = "") -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://api.github.com/", code, "hata", {},  # type: ignore[arg-type]
                                  io.BytesIO(body.encode()))


def github_headers(token: str) -> dict[str, str]:
    return {
        "accept": "application/vnd.github+json",
        "authorization": f"Bearer {token}",
        "x-github-api-version": "2022-11-28",
        "content-type": "application/json",
        "user-agent": "telegram-indirim-takipci",
    }


_GITHUB_ENV = ("CONFIG_PUSH_TOKEN", "GITHUB_TOKEN", "GH_PAT", "GITHUB_REPOSITORY",
               "CONFIG_PUSH_BRANCH", "GITHUB_REF_NAME", "GITHUB_WORKFLOW_FILE")


def github_env(**values: str) -> Any:
    """Ortamı GitHub değişkenlerinden arındır; yalnızca verilenler tanımlı olsun.

    Actions'ta GITHUB_REPOSITORY ve GITHUB_REF_NAME hep tanımlıdır; "tanımsızken ne
    olur" sorusu ancak böyle sorulabilir.
    """
    env = {name: value for name, value in os.environ.items() if name not in _GITHUB_ENV}
    env.update(values)
    return mock.patch.dict(os.environ, env, clear=True)


@contextlib.contextmanager
def quiet_logs() -> Any:
    """Günlük satırları sözleşme değildir; test çıktısını kirletmesinler."""
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(previous)


# --- GitHub: yeni çalışma başlatma -----------------------------------------------

class DispatchNextRunTest(unittest.TestCase):
    """/yenile ve otomatik yenileme: yeni bir Actions çalışması isteği."""

    def dispatch(self, pat: str, *answers: Any, **env: str) -> tuple[tuple[bool, str], Wire]:
        wire = Wire(*answers)
        with github_env(**env), quiet_logs(), mock.patch.object(urllib.request, "urlopen", wire):
            return asyncio.run(bot.dispatch_next_run(pat)), wire

    def test_without_pat_nothing_is_requested(self) -> None:
        result, wire = self.dispatch("", GITHUB_REPOSITORY="cnbrkc/ornek")
        self.assertEqual(result, (False, "GH_PAT tanımlı değil; Actions sayfasından 'Run workflow' ile başlat."))
        self.assertEqual(wire.calls, [])

    def test_without_repository_nothing_is_requested(self) -> None:
        for env in ({}, {"GITHUB_REPOSITORY": ""}):
            with self.subTest(env=env):
                result, wire = self.dispatch("pat", **env)
                self.assertEqual(result, (False, "GITHUB_REPOSITORY bulunamadı (Actions dışında mı çalışıyor?)."))
                self.assertEqual(wire.calls, [])

    def test_request_with_default_workflow_and_branch(self) -> None:
        result, wire = self.dispatch("gizli-pat", b"", GITHUB_REPOSITORY="cnbrkc/ornek")
        self.assertEqual(result, (True, "Yeni çalışma başlatıldı (cnbrkc/ornek@main)."))
        self.assertEqual(wire.calls, [{
            "method": "POST",
            "url": "https://api.github.com/repos/cnbrkc/ornek/actions/workflows/telegram-monitor.yml/dispatches",
            "headers": github_headers("gizli-pat"),
            "body": {"ref": "main"},
            "timeout": 20,
        }])

    def test_workflow_and_branch_follow_the_environment(self) -> None:
        result, wire = self.dispatch("pat", b"", GITHUB_REPOSITORY="cnbrkc/ornek",
                                     GITHUB_WORKFLOW_FILE="baska.yml", GITHUB_REF_NAME="deneme")
        self.assertEqual(result, (True, "Yeni çalışma başlatıldı (cnbrkc/ornek@deneme)."))
        self.assertEqual(wire.sent(), [
            ("POST", "https://api.github.com/repos/cnbrkc/ornek/actions/workflows/baska.yml/dispatches"),
        ])
        self.assertEqual(wire.calls[0]["body"], {"ref": "deneme"})

    def test_http_error_is_reported_with_its_status(self) -> None:
        result, wire = self.dispatch("pat", http_error(403, "Resource not accessible"),
                                     GITHUB_REPOSITORY="cnbrkc/ornek")
        self.assertEqual(result, (False, "GitHub isteği başarısız: HTTP 403. "
                                         "GH_PAT'in 'Actions: Read and write' yetkisi var mı?"))
        self.assertEqual(len(wire.calls), 1, "istek yinelenmez")

    def test_other_errors_are_reported_by_type(self) -> None:
        result, wire = self.dispatch("pat", urllib.error.URLError("ağ yok"),
                                     GITHUB_REPOSITORY="cnbrkc/ornek")
        self.assertEqual(result, (False, "GitHub isteği başarısız: URLError."))
        self.assertEqual(len(wire.calls), 1, "istek yinelenmez")


# --- GitHub: Contents API ile kalıcı kayıt ---------------------------------------

class GithubApiTest(unittest.TestCase):
    """_github_api: tek bir GitHub REST çağrısı → (başarı, yanıt, ayrıntı)."""

    def call(self, *args: Any, answer: Any) -> tuple[tuple[bool, Any, str], Wire]:
        wire = Wire(answer)
        with mock.patch.object(urllib.request, "urlopen", wire):
            return bot._github_api(*args), wire

    def test_request_shape_and_json_answer(self) -> None:
        result, wire = self.call("PUT", "https://api.github.com/x", "gizli", {"mesaj": "çay"},
                                 answer={"content": {"sha": "yeni"}})
        self.assertEqual(result, (True, {"content": {"sha": "yeni"}}, ""))
        self.assertEqual(wire.calls, [{
            "method": "PUT", "url": "https://api.github.com/x", "headers": github_headers("gizli"),
            "body": {"mesaj": "çay"}, "timeout": 20,
        }])

    def test_request_without_payload_has_no_body_and_empty_answer_is_an_empty_dict(self) -> None:
        result, wire = self.call("GET", "https://api.github.com/x", "gizli", answer=b"")
        self.assertEqual(result, (True, {}, ""))
        self.assertIsNone(wire.calls[0]["body"])
        self.assertEqual(wire.calls[0]["method"], "GET")

    def test_http_error_detail_is_the_status_and_first_160_characters(self) -> None:
        result, _ = self.call("GET", "https://api.github.com/x", "gizli", answer=http_error(422, "ş" * 500))
        self.assertEqual(result, (False, None, "HTTP 422 " + "ş" * 160))

    def test_other_errors_are_named(self) -> None:
        result, _ = self.call("GET", "https://api.github.com/x", "gizli", answer=urllib.error.URLError("ağ yok"))
        self.assertEqual(result, (False, None, "URLError: <urlopen error ağ yok>"))

    def test_unreadable_answer_is_a_failure_not_an_exception(self) -> None:
        ok, data, detail = self.call("GET", "https://api.github.com/x", "gizli", answer=b"<html>")[0]
        self.assertEqual((ok, data), (False, None))
        self.assertTrue(detail.startswith("JSONDecodeError: "), detail)


class ContentsApiPushTest(unittest.TestCase):
    """git deposu yokken config.json'ın Contents API ile yazılması (_push_via_api)."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.path = self.folder / "config.json"
        self.path.write_bytes('{"kelime": "çay"}\n'.encode())
        self.content = base64.b64encode(self.path.read_bytes()).decode()

    def push(self, *answers: Any, path: Path | None = None, **env: str) -> tuple[tuple[str, str], Wire]:
        wire = Wire(*answers)
        with github_env(**env), mock.patch.object(urllib.request, "urlopen", wire):
            return bot._push_via_api(path or self.path, "config: deneme"), wire

    def test_without_repository_or_token_nothing_is_requested(self) -> None:
        for env in ({"GH_PAT": "pat"}, {"GITHUB_REPOSITORY": "cnbrkc/ornek"},
                    {"GITHUB_REPOSITORY": "  ", "GH_PAT": "pat"}):
            with self.subTest(env=env):
                result, wire = self.push(**env)
                self.assertEqual(result, ("no-repo", "GITHUB_REPOSITORY veya push token'ı yok"))
                self.assertEqual(wire.calls, [])

    def test_existing_file_is_updated_with_its_sha(self) -> None:
        result, wire = self.push({"sha": "eski-sha"}, {}, GITHUB_REPOSITORY="cnbrkc/ornek", GH_PAT="pat")
        self.assertEqual(result, ("pushed", "cnbrkc/ornek@main (API)"))
        url = "https://api.github.com/repos/cnbrkc/ornek/contents/config.json"
        self.assertEqual(wire.calls, [
            {"method": "GET", "url": url + "?ref=main", "headers": github_headers("pat"),
             "body": None, "timeout": 20},
            {"method": "PUT", "url": url, "headers": github_headers("pat"), "timeout": 20,
             "body": {"message": "config: deneme", "content": self.content, "branch": "main",
                      "sha": "eski-sha"}},
        ])

    def test_file_unknown_to_github_is_created_without_sha(self) -> None:
        for answer in (http_error(404, "Not Found"), {"name": "config.json"}, []):
            with self.subTest(answer=answer):
                result, wire = self.push(answer, {}, GITHUB_REPOSITORY="cnbrkc/ornek", GH_PAT="pat")
                self.assertEqual(result, ("pushed", "cnbrkc/ornek@main (API)"))
                self.assertEqual(wire.calls[1]["body"],
                                 {"message": "config: deneme", "content": self.content, "branch": "main"})

    def test_refused_write_reports_the_api_detail(self) -> None:
        result, wire = self.push({"sha": "eski-sha"}, http_error(409, "sha uyuşmuyor"),
                                 GITHUB_REPOSITORY="cnbrkc/ornek", GH_PAT="pat")
        self.assertEqual(result, ("error", "HTTP 409 sha uyuşmuyor"))
        self.assertEqual(len(wire.calls), 2, "yazma yinelenmez")

    def test_branch_is_config_push_branch_then_ref_name_then_main(self) -> None:
        cases = [
            ({"CONFIG_PUSH_BRANCH": "ayar/dal", "GITHUB_REF_NAME": "4/merge"}, "ayar/dal"),
            ({"GITHUB_REF_NAME": "surum"}, "surum"),
            ({"CONFIG_PUSH_BRANCH": "", "GITHUB_REF_NAME": ""}, "main"),
        ]
        for env, branch in cases:
            with self.subTest(env=env):
                result, wire = self.push({"sha": "s"}, {}, GITHUB_REPOSITORY="cnbrkc/ornek", GH_PAT="pat", **env)
                self.assertEqual(result, ("pushed", f"cnbrkc/ornek@{branch} (API)"))
                # Dal adı okuma adresine olduğu gibi (kodlanmadan) yazılır.
                self.assertEqual(wire.calls[0]["url"],
                                 f"https://api.github.com/repos/cnbrkc/ornek/contents/config.json?ref={branch}")
                self.assertEqual(wire.calls[1]["body"]["branch"], branch)

    def test_file_name_is_quoted_in_the_address(self) -> None:
        path = self.folder / "ayar dosyası.json"
        path.write_bytes(b"{}\n")
        _, wire = self.push({}, {}, path=path, GITHUB_REPOSITORY="cnbrkc/ornek", GH_PAT="pat")
        url = "https://api.github.com/repos/cnbrkc/ornek/contents/ayar%20dosyas%C4%B1.json"
        self.assertEqual(wire.sent(), [("GET", url + "?ref=main"), ("PUT", url)])

    def test_push_token_is_the_first_non_blank_of_three(self) -> None:
        cases = [
            ({"CONFIG_PUSH_TOKEN": "bir", "GITHUB_TOKEN": "iki", "GH_PAT": "üç"}, "bir"),
            ({"CONFIG_PUSH_TOKEN": "  ", "GITHUB_TOKEN": " iki ", "GH_PAT": "üç"}, "iki"),
            ({"GH_PAT": "üç"}, "üç"),
            ({}, ""),
        ]
        for env, expected in cases:
            with self.subTest(env=env), github_env(**env):
                self.assertEqual(bot.push_token(), expected)

    def test_commit_and_push_outside_a_repository_goes_through_the_contents_api(self) -> None:
        for note, message in (("match_mode değişti", "config: match_mode değişti"), ("", "config: güncelleme")):
            with self.subTest(note=note):
                wire = Wire({"sha": "eski-sha"}, {})
                with github_env(GITHUB_REPOSITORY="cnbrkc/ornek", CONFIG_PUSH_TOKEN="pat"), \
                     mock.patch.object(urllib.request, "urlopen", wire), \
                     mock.patch.object(bot, "_run_git", return_value=(128, "fatal: not a git repository")):
                    result = bot.commit_and_push(self.path, note)
                self.assertEqual(result, ("pushed", "cnbrkc/ornek@main (API)"))
                self.assertEqual([call["method"] for call in wire.calls], ["GET", "PUT"])
                self.assertEqual(wire.calls[1]["body"]["message"], message)


# --- main() içinden koşan senaryolar ---------------------------------------------

class ParkedTracker(product_tracker.ProductTracker):
    """Gerçek ürün izleyicisi; yalnızca arka plan döngüsü durur.

    Kuyruğa giren iş kuyrukta kalır: Akakçe'ye gidilmez. Testler bot.py'nin
    izleyiciye NE verdiğini kuyruktan okur.
    """

    async def run(self) -> None:
        await asyncio.Event().wait()


class PrivateChat:
    """Senaryonun eli: botun özel sohbetine yazar, o mesaja verilen yanıtları okur."""

    def __init__(self, client: ti.FakeClient, api: tpb.FakeAPI, tracker: Any) -> None:
        self.client, self.api, self.tracker = client, api, tracker

    async def say(self, text: str, sender: int = OWNER) -> list[str]:
        before = len(self.api.calls)
        await self.client.handlers[0][1](private.PrivateControlEvent(self.api, tpb.dm_message(text, sender)))
        return [payload.get("text", "") for _, payload in self.api.calls[before:]]

    async def say_in_saved_messages(self, text: str) -> list[str]:
        """Hesabın Kayıtlı Mesajlar'ı: özel komut kanalı açıkken acil durum yolu."""
        event = ti.FakeEvent(OWNER, OWNER, text, media=False)
        await self.client.handlers[0][1](event)
        return list(event.replies)

    def queued_jobs(self) -> list[tuple]:
        jobs = []
        while not self.tracker.queue.empty():
            jobs.append(self.tracker.queue.get_nowait())
        return jobs


def run_private(scenario: Callable[[PrivateChat], Awaitable[None]], *,
                tracker_factory: Callable[..., Any] = ParkedTracker,
                wire: Wire | None = None, **config: Any) -> None:
    """Özel komut kanalı açık bir bot başlat (golden.run_bot); ``scenario`` bot ayaktayken koşar.

    Ek sahteler: özel sohbetin Bot API'si (test_private_bot.FakeAPI), getUpdates
    yoklayıcısı ve ürün izleyicisinin arka plan döngüsü.
    """
    api = tpb.FakeAPI()
    trackers: list[Any] = []
    finished: list[bool] = []
    network = wire if wire is not None else Wire()

    def make_tracker(*args: Any, **kwargs: Any) -> Any:
        trackers.append(tracker_factory(*args, **kwargs))
        return trackers[-1]

    async def poller(_api: Any, _allowed: Any, _handler: Any, on_status: Any = None, **_kwargs: Any) -> None:
        # Gerçek yoklayıcı gibi hazır olduğunu bildirir, sonra bekler.
        private.POLL_STATE.update({"status": "running", "bot": {"id": 424242, "username": "takipci_bot"}})
        if on_status is not None:
            await on_status("running", "takipci_bot")
        await asyncio.Event().wait()

    async def alive(client: ti.FakeClient, _queue: Any) -> None:
        await scenario(PrivateChat(client, api, trackers[0] if trackers else None))
        finished.append(True)

    tpb.reset_poll_state()
    try:
        with mock.patch.object(bot, "BotAPI", return_value=api), \
             mock.patch.object(bot, "poll_private_commands", poller), \
             mock.patch.object(product_tracker, "ProductTracker", make_tracker):
            golden.run_bot({"private_control": True, **config}, alive, wire=network)
    finally:
        tpb.reset_poll_state()
    assert finished, "senaryo sonuna kadar koşmadı"
    if wire is None:
        assert not network.calls, f"beklenmeyen HTTP isteği: {network.sent()}"


def run_prod(scenario: Callable[[ti.FakeClient], Awaitable[None]], **options: Any) -> None:
    """Canlı düzendeki (smart_header + fast_notify) botu başlat; ``scenario`` bot ayaktayken koşar."""
    finished: list[bool] = []

    async def alive(client: ti.FakeClient, _queue: Any) -> None:
        await scenario(client)
        finished.append(True)

    golden.run_bot(golden.PROFILES["prod"], alive, **options)
    assert finished, "senaryo sonuna kadar koşmadı"


# --- Ürün takibinin GitHub'a yazımı (persist_products) -----------------------------

WATCHES = {"version": 1, "enabled": True, "items": [{
    "url": "https://www.akakce.com/kahve-makinesi/en-ucuz-ornek-fiyati,123456.html",
    "title": "Örnek Kahve Makinesi", "target": 650000, "chat_id": OWNER, "alert_price": None,
}]}


class PersistProductsTest(unittest.TestCase):
    """main() ürün izleyicisine verdiği kayıt işlevi: yalnızca Contents API, yerel git'e dokunmaz."""

    def persist(self, *answers: Any, git: Any = None, **env: str) -> tuple[tuple[bool, str], Wire]:
        wire = Wire(*answers)
        outcome: list[tuple[bool, str]] = []
        no_repository = (128, "fatal: not a git repository")

        async def scenario(chat: PrivateChat) -> None:
            with github_env(**env), mock.patch.object(bot, "_run_git", git or (lambda *a, **k: no_repository)):
                outcome.append(chat.tracker.persist(chat.tracker.path, WATCHES))

        run_private(scenario, wire=wire)
        return outcome[0], wire

    def test_tracker_is_wired_to_the_watch_file_beside_config(self) -> None:
        seen: dict[str, Any] = {}

        async def scenario(chat: PrivateChat) -> None:
            seen.update(path=chat.tracker.path, api=chat.tracker.api, owner=chat.tracker.owner_id,
                        header=chat.tracker.header_formatter(129900, "Örnek Ürün"), expected_api=chat.api,
                        beside_config=chat.tracker.path.with_name("config.json").exists())

        run_private(scenario)
        self.assertEqual(seen["path"].name, "product_watches.json")
        self.assertTrue(seen["beside_config"], "config.json ile aynı klasör")
        self.assertIs(seen["api"], seen["expected_api"])
        self.assertEqual(seen["owner"], OWNER)
        self.assertEqual(seen["header"], "₺1.299 — Örnek Ürün")

    def test_incomplete_environment_is_refused_without_requests(self) -> None:
        for env in ({"GH_PAT": "pat", "GITHUB_REF_NAME": "main"},
                    {"GITHUB_REPOSITORY": "cnbrkc/ornek", "GITHUB_REF_NAME": "main"},
                    {"GITHUB_REPOSITORY": "cnbrkc/ornek", "GH_PAT": "pat"}):
            with self.subTest(env=env):
                result, wire = self.persist(**env)
                self.assertEqual(result, (False, "GitHub kalıcı kayıt ortamı eksik."))
                self.assertEqual(wire.calls, [])

    def test_write_request(self) -> None:
        result, wire = self.persist({"sha": "eski-sha"}, {}, GITHUB_REPOSITORY="cnbrkc/ornek",
                                    GH_PAT="pat", CONFIG_PUSH_BRANCH="ayar/dal")
        self.assertEqual(result, (True, "Ürün takip ayarı GitHub'a yazıldı."))
        url = "https://api.github.com/repos/cnbrkc/ornek/contents/product_watches.json"
        content = (json.dumps(WATCHES, ensure_ascii=False, indent=2) + "\n").encode()
        self.assertEqual(wire.calls, [
            # config.json yazımından farklı olarak dal adı burada kodlanır.
            {"method": "GET", "url": url + "?ref=ayar%2Fdal", "headers": github_headers("pat"),
             "body": None, "timeout": 20},
            {"method": "PUT", "url": url, "headers": github_headers("pat"), "timeout": 20,
             "body": {"message": "config: ürün takip güncellemesi", "branch": "ayar/dal",
                      "sha": "eski-sha", "content": base64.b64encode(content).decode()}},
        ])

    def test_unknown_remote_version_is_refused_before_writing(self) -> None:
        for answer in (http_error(404, "Not Found"), {"name": "product_watches.json"}, []):
            with self.subTest(answer=answer):
                result, wire = self.persist(answer, GITHUB_REPOSITORY="cnbrkc/ornek", GH_PAT="pat",
                                            GITHUB_REF_NAME="main")
                self.assertEqual(result, (False, "Takip dosyasının GitHub sürümü okunamadı."))
                self.assertEqual([call["method"] for call in wire.calls], ["GET"])

    def test_refused_write_is_reported(self) -> None:
        result, wire = self.persist({"sha": "eski-sha"}, http_error(409, "sha uyuşmuyor"),
                                    GITHUB_REPOSITORY="cnbrkc/ornek", GH_PAT="pat", GITHUB_REF_NAME="main")
        self.assertEqual(result, (False, "GitHub yazımı başarısız."))
        self.assertEqual([call["method"] for call in wire.calls], ["GET", "PUT"])

    def test_branch_is_config_push_branch_then_local_branch_then_ref_name(self) -> None:
        def checkout(args: list[str], cwd: str, timeout: int = 30) -> tuple[int, str]:
            return (0, cwd) if "--show-toplevel" in args else (0, "yerel-dal")

        cases = [
            ({"CONFIG_PUSH_BRANCH": "ayar", "GITHUB_REF_NAME": "4/merge"}, checkout, "ayar"),
            ({"GITHUB_REF_NAME": "4/merge"}, checkout, "yerel-dal"),
            ({"GITHUB_REF_NAME": "surum"}, None, "surum"),
        ]
        for env, git, branch in cases:
            with self.subTest(env=env, repository=bool(git)):
                result, wire = self.persist({"sha": "s"}, {}, git=git, GITHUB_REPOSITORY="cnbrkc/ornek",
                                            GH_PAT="pat", **env)
                self.assertTrue(result[0], result)
                self.assertEqual(wire.calls[1]["body"]["branch"], branch)


# --- /ekle | /cikar → 7 (webdetakip) ----------------------------------------------

LINK = "https://www.akakce.com/kahve-makinesi/en-ucuz-ornek-fiyati,123456.html"
ASK_LINK = ("Akakçe ürün detay linkini gönder (…akakce.com/…-fiyati,ID.html). "
            "Hedef fiyatı bir sonraki mesajda istenecek. İptal: /iptal")
ASK_NUMBER = "Çıkarılacak ürünün /webtakipler listesindeki numarasını gönder (ör. 3). İptal: /iptal"
QUEUED = "İşlem sıraya alındı; sonucu burada bildireceğim."
OWNER_ONLY = "🎯 Kişisel ayarlar yalnızca hesap sahibinin botla özel sohbetinden yönetilir."
MENU_LINE = "7. 🌐  webdetakip  ·  Akakçe ürün fiyat takibi  ·  "


class WebdetakipFlowTest(unittest.TestCase):
    """Liste menüsündeki 7. seçenek ayar taslağını ürün izleyicisine devreder."""

    def test_add_flow_hands_the_link_to_the_tracker(self) -> None:
        async def scenario(chat: PrivateChat) -> None:
            chat.tracker.state["enabled"] = True
            (menu,) = await chat.say("/ekle")
            self.assertTrue(menu.startswith("➕  LİSTEYE EKLE\n"), menu)
            self.assertIn(MENU_LINE, menu)
            self.assertEqual(await chat.say("7"), [ASK_LINK])
            # Link olmayan yanıt akışı bozmaz; taslak bekler.
            self.assertEqual(await chat.say("bu bir link değil"),
                             ["Akakçe ürün detay linki bekleniyor (…akakce.com/…-fiyati,ID.html). İptal: /iptal"])
            self.assertEqual(chat.queued_jobs(), [])
            self.assertEqual(await chat.say(LINK + "?utm_source=kanal"), [QUEUED])
            self.assertEqual(chat.queued_jobs(), [("link", OWNER, LINK)])
            # Taslak kapanmıştır: sıradaki düz mesaj hiçbir akışa girmez.
            self.assertEqual(await chat.say("6500"), [])
            self.assertEqual(await chat.say("/iptal"), ["ℹ️ İptal edilecek bekleyen bir işlem yok."])

        run_private(scenario)

    def test_list_name_selects_it_and_disabled_tracking_answers_instead_of_queueing(self) -> None:
        async def scenario(chat: PrivateChat) -> None:
            self.assertFalse(chat.tracker.state["enabled"])
            await chat.say("/ekle")
            self.assertEqual(await chat.say("webdetakip"), [ASK_LINK])
            self.assertEqual(await chat.say(LINK), ["Ürün takibi kapalı. Açmak için /webtakipac; menü: /webtakip"])
            self.assertEqual(chat.queued_jobs(), [])
            self.assertEqual(await chat.say("/iptal"), ["ℹ️ İptal edilecek bekleyen bir işlem yok."])

        run_private(scenario)

    def test_remove_flow_shows_the_list_then_queues_the_removal(self) -> None:
        async def scenario(chat: PrivateChat) -> None:
            chat.tracker.state = json.loads(json.dumps(WATCHES))
            (menu,) = await chat.say("/cikar")
            self.assertTrue(menu.startswith("➖  LİSTEDEN ÇIKAR\n"), menu)
            self.assertIn(MENU_LINE, menu)
            listing, prompt = await chat.say("7")
            self.assertTrue(listing.startswith("Takip açık · çalışma başlangıcı "), listing)
            self.assertTrue(listing.endswith(
                " TSİ\n1. Örnek Kahve Makinesi\nHedef altı: 6.500,00 TL\n"
                f"Son kontrol: bu çalışmada henüz yok | Durum: bekliyor\n{LINK}\nMenü: /webtakip"), listing)
            self.assertEqual(prompt, ASK_NUMBER)
            self.assertEqual(await chat.say("hangisiydi"), [ASK_NUMBER])
            self.assertEqual(chat.queued_jobs(), [])
            self.assertEqual(await chat.say("1 numara"), [QUEUED])
            self.assertEqual(chat.queued_jobs(), [("/webtakipsil", OWNER, "/webtakipsil 1")])
            self.assertEqual(await chat.say("/iptal"), ["ℹ️ İptal edilecek bekleyen bir işlem yok."])

        run_private(scenario)

    def test_tracker_commands_inside_the_draft_close_it(self) -> None:
        async def scenario(chat: PrivateChat) -> None:
            await chat.say("/ekle")
            await chat.say("7")
            self.assertEqual(await chat.say("/webtakipiptal"), [
                "Ürün takip taslağı iptal edildi.",
                "↩️ Ürün takip taslağı iptal edildi; ayar düzenleme taslağı da kapatıldı.",
            ])
            await chat.say("/ekle")
            await chat.say("7")
            menu, note = await chat.say("/webtakip")
            self.assertTrue(menu.startswith("Akakçe Takip Menüsü · Kapalı"), menu)
            self.assertEqual(note, "ℹ️ Ürün takip işlemi işlendi; ayar düzenleme taslağı kapatıldı. "
                                   "Takip menüsü: /webtakip")
            self.assertEqual(await chat.say("/iptal"), ["ℹ️ İptal edilecek bekleyen bir işlem yok."])

        run_private(scenario)

    def test_cancel_and_save_inside_the_draft(self) -> None:
        async def scenario(chat: PrivateChat) -> None:
            await chat.say("/ekle")
            await chat.say("7")
            self.assertEqual(await chat.say("/iptal"),
                             ["↩️ İşlem iptal edildi. Taslak silindi; ayarlar ve GitHub değişmedi."])
            await chat.say("/cikar")
            await chat.say("7")
            self.assertEqual(await chat.say("/kaydet"), [
                "ℹ️ Ürün takip kayıtları product_watches.json'da; bu akışta kayıt anında kalıcılaşır, "
                "ayrı /kaydet adımı yok. Ayar taslağı kapatıldı. Takip menüsü: /webtakip",
            ])
            self.assertEqual(await chat.say("/iptal"), ["ℹ️ İptal edilecek bekleyen bir işlem yok."])
            self.assertEqual(chat.queued_jobs(), [])

        run_private(scenario)

    def test_only_the_owner_in_the_private_chat_may_choose_it(self) -> None:
        other_admin = 222333444

        async def scenario(chat: PrivateChat) -> None:
            await chat.say("/ekle", sender=other_admin)
            self.assertEqual(await chat.say("7", sender=other_admin), [OWNER_ONLY])
            # Taslak liste seçiminde bekler: başka bir liste hâlâ seçilebilir.
            (prompt,) = await chat.say("1", sender=other_admin)
            self.assertTrue(prompt.startswith("🔎  Dahili kelimeler\n"), prompt)

            await chat.say_in_saved_messages("/ekle")
            self.assertEqual(await chat.say_in_saved_messages("7"), [OWNER_ONLY])
            self.assertEqual(chat.queued_jobs(), [])

        run_private(scenario, admin_user_id=[OWNER, other_admin])

    def test_unavailable_tracker_closes_the_draft(self) -> None:
        def broken(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("takip dosyası açılamadı")

        async def scenario(chat: PrivateChat) -> None:
            self.assertIsNone(chat.tracker)
            (menu,) = await chat.say("/ekle")
            self.assertIn(MENU_LINE, menu)
            self.assertEqual(await chat.say("7"),
                             ["❌ Ürün takibi şu an kullanılamıyor (bildirim botu yapılandırması eksik)."])
            self.assertEqual(await chat.say("/iptal"), ["ℹ️ İptal edilecek bekleyen bir işlem yok."])

        run_private(scenario, tracker_factory=broken)


# --- Tekrar rozeti: düzenlemenin reddedildiği dallar -------------------------------

OFFER = "Çaykur Rize Turist Çay 1 kg\n199 TL"
NOTIFICATION = ("₺199 — Çaykur Rize Turist Çay 1 kg\n\nÇaykur Rize Turist Çay 1 kg\n199 TL\n\n"
                "🔗 Mesajı Gör: https://t.me/firsatz/1\n\n")
FULL_BADGE = "📌 {} kere paylaşıldı: firsatz · tek grup"
SHORT_BADGE = "📌 {} kere paylaşıldı"


async def offer(client: ti.FakeClient, message_id: int) -> None:
    """Aynı fırsat kaynak kanalda (yeni bir mesaj olarak) bir kez daha paylaşılır."""
    chat = next(iter(bot.SOURCE_IDS))
    await client.handlers[1][1](ti.FakeEvent(chat, 999, OFFER, message_id=message_id, media=False))


def cached() -> dict[str, Any]:
    (entry,) = bot.DEDUP_CACHE.values()
    return entry


def stats(*names: str) -> tuple[int, ...]:
    return tuple(bot.STATS[name] for name in names)


class EditRefusingBotApi(golden.BotApiWire):
    """Gönderimleri kabul eden, düzenlemeleri verilen açıklamayla (HTTP 400) reddeden Bot API."""

    def __init__(self, description: str) -> None:
        super().__init__()
        self.description = description

    def __call__(self, request: urllib.request.Request, timeout: float | None = None) -> io.BytesIO:
        if "/editMessage" not in request.full_url:
            return super().__call__(request, timeout)
        self.requests.append(request)
        body = {"ok": False, "error_code": 400, "description": self.description}
        raise urllib.error.HTTPError(request.full_url, 400, "Bad Request", {},  # type: ignore[arg-type]
                                     io.BytesIO(json.dumps(body).encode()))

    def log(self) -> list[tuple[str, str]]:
        """(yöntem, metnin son satırı) çiftleri."""
        records = [golden.wire_request(request) for request in self.requests]
        return [(record["method"], record["fields"]["text"].rsplit("\n", 1)[-1]) for record in records]


class BotBadgeEditRefusedTest(unittest.TestCase):
    """Bildirim bottan gitmişse (canlı düzen) rozet Bot API ile işlenir."""

    def test_vanished_or_frozen_message_is_replaced_by_a_new_notification(self) -> None:
        for description in ("Bad Request: message to edit not found",
                            "Bad Request: message can't be edited"):
            with self.subTest(description=description):
                wire = EditRefusingBotApi(description)
                seen: dict[str, Any] = {}

                async def scenario(client: ti.FakeClient) -> None:
                    await offer(client, 1)
                    first = cached()
                    await offer(client, 2)
                    seen.update(first=first, entry=cached(), stats=stats("forwarded", "deduped", "dedup_edits"))

                run_prod(scenario, wire=wire)
                # Tek düzenleme denemesi (kısa rozet denenmez), ardından aynı fırsat yeniden bildirilir.
                self.assertEqual(wire.log(), [("sendMessage", "firsatz"),
                                              ("editMessageText", FULL_BADGE.format(2)),
                                              ("sendMessage", "firsatz")])
                self.assertEqual(seen["stats"], (2, 0, 0))
                self.assertEqual((seen["first"]["failed"], seen["first"]["editable"]), (True, None))
                self.assertIsNot(seen["entry"], seen["first"])
                self.assertEqual((seen["entry"]["count"], seen["entry"]["editable"], seen["entry"]["failed"]),
                                 (1, "bot", False))

    def test_other_refusals_try_the_short_badge_and_give_up_after_two_rounds(self) -> None:
        wire = EditRefusingBotApi("Bad Request: can't parse entities")
        seen: dict[str, Any] = {}

        async def scenario(client: ti.FakeClient) -> None:
            await offer(client, 1)
            await offer(client, 2)
            seen["after_one_round"] = (cached()["editable"], cached()["edit_fails"])
            await offer(client, 3)
            seen["after_two_rounds"] = (cached()["editable"], cached()["edit_fails"])
            await offer(client, 4)
            seen.update(entry=dict(cached()), stats=stats("forwarded", "deduped", "dedup_edits"))

        run_prod(scenario, wire=wire)
        self.assertEqual(wire.log(), [
            ("sendMessage", "firsatz"),
            ("editMessageText", FULL_BADGE.format(2)), ("editMessageText", SHORT_BADGE.format(2)),
            ("editMessageText", FULL_BADGE.format(3)), ("editMessageText", SHORT_BADGE.format(3)),
            # Dördüncü paylaşımda artık deneme yok: fırsat rozetsiz birleşir.
        ])
        self.assertEqual(seen["after_one_round"], ("bot", 1))
        self.assertEqual(seen["after_two_rounds"], (None, 2))
        self.assertEqual(seen["stats"], (1, 3, 0), "yeni bildirim yok; üç tekrar da birleşti")
        self.assertEqual((seen["entry"]["count"], seen["entry"]["failed"]), (4, False))
        self.assertEqual(seen["entry"]["text"], NOTIFICATION + "firsatz", "kayıtlı metin rozetsiz kalır")


class AccountBadgeEditRefusedTest(unittest.TestCase):
    """Bildirim botu yokken ilk mesaj hesaptan gider; rozet Telethon ile işlenir."""

    def run_offers(self, error: Callable[[], Exception], count: int) -> dict[str, Any]:
        seen: dict[str, Any] = {"attempts": []}
        wire = Wire()

        async def refuse(_entity: Any, message: Any, text: str, **_kwargs: Any) -> None:
            seen["attempts"].append((message, text.rsplit("\n", 1)[-1]))
            raise error()

        async def scenario(client: ti.FakeClient) -> None:
            client.edit_message = refuse  # type: ignore[method-assign]
            await offer(client, 1)
            seen["first"] = cached()
            seen["started"] = time.time()
            for message_id in range(2, count + 1):
                await offer(client, message_id)
            seen.update(finished=time.time(), entry=cached(), sent=[text for _, text in client.sent],
                        stats=stats("forwarded", "deduped", "dedup_edits"))

        run_prod(scenario, wire=wire, env={"NOTIFY_BOT_TOKEN": ""})
        self.assertEqual(wire.calls, [], "bot yokken HTTP isteği olmaz")
        self.assertEqual(seen["first"]["account_message_id"], 101)
        return seen

    def test_flood_wait_postpones_the_badge_without_a_second_attempt(self) -> None:
        seen = self.run_offers(lambda: errors.FloodWaitError(request=None, capture=30), count=2)
        self.assertEqual(seen["attempts"], [(101, FULL_BADGE.format(2))])
        self.assertEqual(seen["sent"], [NOTIFICATION + "firsatz"])
        self.assertEqual(seen["stats"], (1, 1, 0))
        entry = seen["entry"]
        self.assertIs(entry, seen["first"])
        self.assertEqual((entry["count"], entry["failed"], entry["editable"]), (2, False, "account"))
        self.assertEqual(entry["text"], NOTIFICATION + "firsatz")
        # Sonraki rozet denemesi Telegram'ın istediği süre kadar ileri atılır.
        self.assertGreaterEqual(entry["last_edit"], seen["started"] + 30)
        self.assertLessEqual(entry["last_edit"], seen["finished"] + 30)

    def test_invalid_or_foreign_message_is_replaced_by_a_new_one(self) -> None:
        for error in (errors.MessageIdInvalidError, errors.MessageAuthorRequiredError):
            with self.subTest(error=error.__name__):
                seen = self.run_offers(lambda: error(request=None), count=2)
                self.assertEqual(seen["attempts"], [(101, FULL_BADGE.format(2))])
                # Yeni mesaj ikinci paylaşımın kendi bildirimidir (bağlantısı /2).
                self.assertEqual(seen["sent"], [NOTIFICATION + "firsatz",
                                                NOTIFICATION.replace("firsatz/1", "firsatz/2") + "firsatz"])
                self.assertEqual(seen["stats"], (2, 0, 0))
                self.assertEqual((seen["first"]["failed"], seen["first"]["editable"]), (True, None))
                entry = seen["entry"]
                self.assertIsNot(entry, seen["first"])
                self.assertEqual((entry["count"], entry["editable"], entry["account_message_id"]),
                                 (1, "account", 102))

    def test_not_modified_counts_as_applied(self) -> None:
        seen = self.run_offers(lambda: errors.MessageNotModifiedError(request=None), count=2)
        self.assertEqual(seen["attempts"], [(101, FULL_BADGE.format(2))])
        self.assertEqual(seen["stats"], (1, 1, 0), "düzenleme sayacı artmaz")
        entry = seen["entry"]
        self.assertEqual((entry["count"], entry["badge_count"], entry["editable"]), (2, 2, "account"))
        self.assertEqual(entry["text"], NOTIFICATION + FULL_BADGE.format(2))

    def test_other_errors_try_the_short_badge_and_give_up_after_two_rounds(self) -> None:
        seen = self.run_offers(lambda: RuntimeError("bağlantı koptu"), count=4)
        self.assertEqual(seen["attempts"], [
            (101, FULL_BADGE.format(2)), (101, SHORT_BADGE.format(2)),
            (101, FULL_BADGE.format(3)), (101, SHORT_BADGE.format(3)),
            # Dördüncü paylaşımda artık deneme yok: fırsat rozetsiz birleşir.
        ])
        self.assertEqual(seen["sent"], [NOTIFICATION + "firsatz"])
        self.assertEqual(seen["stats"], (1, 3, 0))
        entry = seen["entry"]
        self.assertEqual((entry["count"], entry["failed"], entry["editable"], entry["edit_fails"]),
                         (4, False, None, 2))
        self.assertEqual(entry["text"], NOTIFICATION + "firsatz")


class ReservationTakeoverTest(unittest.TestCase):
    """Gönderimi bitmemiş görünen ama sahibi kalmamış rezervasyon devralınır (fail-open)."""

    def take_over(self, owner_state: str) -> dict[str, Any]:
        wire = golden.BotApiWire()
        seen: dict[str, Any] = {}

        async def scenario(client: ti.FakeClient) -> None:
            await offer(client, 1)
            (key,) = bot.DEDUP_CACHE
            # İlk gönderimi yarıda kalmış gibi göster: kayıt "pending", sahibi yok ya da bitmiş.
            stale = bot.new_dedup_entry(cached()["title"], object(), "firsatz", "firsatz")
            stale["offer"] = cached()["offer"]
            if owner_state == "finished":
                stale["owner"] = asyncio.ensure_future(asyncio.sleep(0))
                await stale["owner"]
            bot.DEDUP_CACHE[key] = stale
            with mock.patch.object(bot, "DEDUP_RESERVE_TIMEOUT", 0.01):
                await offer(client, 2)
            seen.update(stale=stale, entry=cached(), stats=stats("forwarded", "deduped", "dedup_edits"))

        run_prod(scenario, wire=wire)
        seen["methods"] = [golden.wire_request(request)["method"] for request in wire.requests]
        return seen

    def test_reservation_without_a_living_owner_is_taken_over(self) -> None:
        for owner_state in ("missing", "finished"):
            with self.subTest(owner=owner_state):
                seen = self.take_over(owner_state)
                self.assertEqual(seen["methods"], ["sendMessage", "sendMessage"], "bekleyen kendisi gönderir")
                self.assertEqual(seen["stats"], (2, 0, 0))
                stale, entry = seen["stale"], seen["entry"]
                self.assertTrue(stale["failed"])
                self.assertTrue(stale["ready"].is_set(), "diğer bekleyenler de uyandırılır")
                self.assertIsNot(entry, stale)
                self.assertEqual((entry["pending"], entry["failed"], entry["count"], entry["editable"]),
                                 (False, False, 1, "bot"))


# --- Bilinen kusurların bugünkü görünümü -------------------------------------------

class KnownDefectPins(unittest.TestCase):
    """Denetimde bulunan gizli kusurlardan düzeltilmemiş ikisi BUGÜN böyle davranıyor.

    Bunlar hedef değil, ölçüm noktasıdır: ilgili kusur düzeltildiğinde buradaki tek
    test bilinçli olarak güncellenir. Düzeltmenin ne üretmesi gerektiği o aşamanın
    kararıdır; burada yalnızca bugünkü sonuç yazılıdır.

    1 numaralı kusur (_utf16_to_index 0 ofsetini 1 sayıyordu) düzeltildi ve iğnesi
    kaldırıldı; doğru davranışı test_bot.py'deki MessageSanitizationTest sabitler.
    """

    def test_defect2_second_copy_in_history_is_not_counted_at_startup(self) -> None:
        # bot.py dedup_preload: "existing_conflict" tanımsız; aynı fırsatın geçmişteki ikinci
        # bildirimi NameError'a düşer ve sessizce atlanır: sayaç da son görülme de ilerlemez.
        now = time.time()
        older, newer = now - 3600, now - 60
        bot_id = int(golden.BOT_TOKEN.split(":", 1)[0])
        history = [  # yeniden eskiye
            (ti.GROUP_ID, NOTIFICATION + "firsatz", bot_id, newer, None, [], 12),
            (ti.GROUP_ID, NOTIFICATION + "firsatz", bot_id, older, None, [], 11),
        ]
        seen: dict[str, Any] = {}

        async def scenario(_client: ti.FakeClient) -> None:
            seen.update(cached())

        run_prod(scenario, wire=Wire(), history=history)
        self.assertEqual(seen["count"], 1)
        self.assertEqual(seen["last_seen"], older)

    def test_defect3_list_menu_cannot_count_the_watched_products(self) -> None:
        # bot.py _webtakip_count: "load_state" modül düzeyinde tanımsız; sayı hep bilinmiyor ("–").
        async def scenario(chat: PrivateChat) -> None:
            chat.tracker.path.write_text(json.dumps(WATCHES), encoding="utf-8")
            self.assertEqual(len(product_tracker.load_state(chat.tracker.path)["items"]), 1)
            (menu,) = await chat.say("/ekle")
            self.assertIn(MENU_LINE + "– kayıt\n", menu)

        run_private(scenario)


# --- Benzer görünen yardımcılar ------------------------------------------------------

class LookAlikeHelpersTest(unittest.TestCase):
    """Aynı işi yapar görünen yardımcı çiftleri aynı sonucu VERMEZ.

    "Tekrarı kaldırmak" için birini ötekinin yerine koymak davranışı değiştirir. Her
    satır aynı girdinin iki işlevdeki bugünkü sonucudur; sütunlardan biri oynarsa bir
    işlev ötekine benzetilmiş demektir.

    Üçüncü bir çift (bot._utf16_to_index / price_filter.utf16_to_char_index) yalnızca 0
    ofsetinde ayrışıyordu; o fark 1 numaralı kusurdu ve düzeltildi. İkisi artık her
    tamsayı ofsette aynı sonucu verdiği için burada satırı yoktur.
    """

    def test_normalize_keeps_whitespace_and_casefolds_while_norm_collapses_and_lowers(self) -> None:
        cases = [
            # girdi                  bot.normalize            price_filter.norm
            ("ÇAY  Bardağı",         "çay  bardağı",          "çay bardağı"),
            ("çay\u00a0bardağı",     "çay\u00a0bardağı",      "çay bardağı"),
            (" kahve \n makinesi\t", " kahve \n makinesi\t", "kahve makinesi"),
            ("Straße",               "strasse",               "straße"),
            ("IŞIK İNDİRİM",         "ışık indirim",          "ışık indirim"),   # ortak zemin
        ]
        for text, in_bot, in_price_filter in cases:
            with self.subTest(text=text):
                self.assertEqual(bot.normalize(text), in_bot)
                self.assertEqual(price_filter.norm(text), in_price_filter)

    def test_parse_price_value_reads_loose_text_while_parse_amount_is_strict(self) -> None:
        cases = [
            # girdi        bot.parse_price_value   price_filter.parse_amount
            ("31.866 TL",  31866.0,                None),
            ("₺ 45",       45.0,                   None),
            ("0,999",      999.0,                  0.999),
            ("1234,567",   1234567.0,              1234.567),
            ("12 bin",     12.0,                   12000.0),
            ("5.999,90",   5999.9,                 5999.9),    # ortak zemin
            ("1.299",      1299.0,                 1299.0),    # ortak zemin
        ]
        for raw, in_bot, in_price_filter in cases:
            with self.subTest(raw=raw):
                self.assertEqual(bot.parse_price_value(raw), in_bot)
                self.assertEqual(price_filter.parse_amount(raw), in_price_filter)


# --- Yapısal bekçiler ----------------------------------------------------------------

# Bugün tanımsız olduğu bilinen iki ad (KnownDefectPins 2 ve 3). Liste yalnızca küçülebilir.
KNOWN_UNDEFINED = {("bot.py", "existing_conflict"), ("bot.py", "load_state")}

_IMPLICIT_GLOBALS = frozenset({"__name__", "__file__", "__doc__", "__spec__", "__loader__",
                               "__package__", "__builtins__", "__debug__"})


def undefined_globals(source: str, filename: str) -> dict[str, list[str]]:
    """İşlev ve sınıf gövdelerinde okunan ama hiçbir yerde tanımlanmayan genel adlar.

    Döndürülen sözlük: ad → adı kullanan kapsamlar ("işlev:satır"). Bir ad; modül
    düzeyinde atanmış/içe aktarılmışsa, bir işlevde ``global`` ile atanıyorsa ya da
    yerleşikse tanımlıdır. Kaynak yalnızca derlenir, çalıştırılmaz. ``from x import *``
    ile gelen adlar görülemez: yıldızlı içe aktarma kullanan modül burada tanımsız ad
    bildirir (bugün hiçbir modül kullanmıyor).
    """
    top = symtable.symtable(source, filename, "exec")

    def scopes(table: symtable.SymbolTable) -> list[symtable.SymbolTable]:
        found = []
        for child in table.get_children():
            found.append(child)
            found.extend(scopes(child))
        return found

    nested = scopes(top)
    defined = {symbol.get_name() for symbol in top.get_symbols()
               if symbol.is_assigned() or symbol.is_imported()}
    defined |= {symbol.get_name() for table in nested for symbol in table.get_symbols()
                if symbol.is_declared_global() and (symbol.is_assigned() or symbol.is_imported())}
    defined |= set(dir(builtins)) | _IMPLICIT_GLOBALS
    missing: dict[str, list[str]] = {}
    for table in nested:
        for symbol in table.get_symbols():
            if symbol.is_global() and symbol.is_referenced() and symbol.get_name() not in defined:
                missing.setdefault(symbol.get_name(), []).append(f"{table.get_name()}:{table.get_lineno()}")
    return missing


class UndefinedNameGuard(unittest.TestCase):
    """Bir işlev başka yere taşınırken geride kalan ad, o satır çalışana dek fark edilmez.

    main() içindeki iç işlevler kapanış değişkenlerine (store, client, tracker...)
    dayanır; modül düzeyine çıkarılan bir işlevde bunlar tanımsız genel ada dönüşür
    ve hata ancak o dal ilk kez çalıştığında (çoğu kez üretimde) görülür.
    """

    def test_detector_recognises_what_it_must(self) -> None:
        sample = (
            "import os\n"
            "LIMIT = 3\n"
            "def setup():\n"
            "    global LATE\n"
            "    LATE = 1\n"
            "def fine(items):\n"
            "    return [os.sep, LIMIT, LATE, len(items), [item for item in items], __name__]\n"
            "def outer():\n"
            "    inner_value = 1\n"
            "    def uses_closure():\n"
            "        return inner_value\n"
            "    return uses_closure\n"
            "def moved_out():\n"
            "    return inner_value\n"
            "class Holder:\n"
            "    size = LIMIT\n"
            "    def method(self):\n"
            "        return self.size + typo_name\n"
        )
        self.assertEqual(undefined_globals(sample, "<örnek>"),
                         {"inner_value": ["moved_out:13"], "typo_name": ["method:17"]})

    def test_production_modules_have_no_new_undefined_names(self) -> None:
        modules = sorted(ROOT.glob("*.py"))
        self.assertIn(ROOT / "bot.py", modules)
        found: dict[tuple[str, str], list[str]] = {}
        for path in modules:
            for name, places in undefined_globals(path.read_text(encoding="utf-8"), str(path)).items():
                found[(path.name, name)] = places
        self.assertEqual(set(found), KNOWN_UNDEFINED, found)


class AkakceWorkerIsolationTest(unittest.TestCase):
    """product_tracker.read_product işçiyi ``python -I`` ile, soyulmuş ortamda başlatır.

    Yalıtılmış kipte betiğin klasörü sys.path'e girmez: işçi depodaki başka bir
    modülü içe aktarırsa testler (süreç içi içe aktarma) yeşil kalır ama üretimde
    işçi hiç açılamaz. Bu test işçiyi üretimdeki gibi başlatır; ağa çıkılmaz
    (adres Akakçe olmadığı için istekten önce reddedilir).
    """

    def test_worker_starts_isolated_and_refuses_a_foreign_address(self) -> None:
        env = {name: os.environ[name] for name in ("PATH", "LANG", "SYSTEMROOT") if name in os.environ}
        process = subprocess.run(
            [sys.executable, "-I", str(ROOT / "akakce_worker.py"), "https://example.com/urun-fiyati,1.html"],
            env=env, capture_output=True, timeout=60, check=False,
        )
        self.assertEqual(process.returncode, 1, process.stderr.decode("utf-8", "replace"))
        report = json.loads(process.stdout.decode("utf-8", "replace"))
        self.assertEqual((report["ok"], report["error"]), (False, "ValueError"))
        self.assertTrue(report["reason"].startswith("HTTPS Akak"), report)


if __name__ == "__main__":
    unittest.main()
