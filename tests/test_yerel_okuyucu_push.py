"""Modem/sunucu senaryosu: besini depoya kendiliğinden işle.

Kullanıcının isteği: "bilgisayar sürekli açık kalamaz, ya modemden çıkarsak?"
Bu yüzden yerel okuyucu modemde (Entware) çalışabilmeli ve ürettiği besini
depoya kendisi göndermeli. Kısıtlar:
  - Modemde git kurulu olmayabilir → Contents API (tek HTTPS çağrısı).
  - Token .git/config'e yazılmaz.
  - Hata olduğunda veri kaybolmaz, eski besin depoda kalır (fail-closed).
"""
import base64
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yerel_okuyucu as reader  # noqa: E402


class ParseRepoTests(unittest.TestCase):
    def test_https_with_git_suffix(self):
        self.assertEqual(reader.parse_repo('https://github.com/cnbrkc/indtkp-asist.git'),
                         ('cnbrkc', 'indtkp-asist'))

    def test_https_without_suffix_and_trailing_slash(self):
        self.assertEqual(reader.parse_repo('https://github.com/cnbrkc/indtkp-asist/'),
                         ('cnbrkc', 'indtkp-asist'))

    def test_ssh_style(self):
        self.assertEqual(reader.parse_repo('git@github.com:cnbrkc/indtkp-asist.git'),
                         ('cnbrkc', 'indtkp-asist'))

    def test_garbage_is_refused(self):
        for bad in ('', 'https://gitlab.com/a/b.git', 'https://github.com/only-user'):
            with self.assertRaises(ValueError):
                reader.parse_repo(bad)


class PushTokenTests(unittest.TestCase):
    def test_prefers_github_token(self):
        env = {'GITHUB_TOKEN': 'g', 'FEED_PUSH_TOKEN': 'f', 'GH_TOKEN': 'h'}
        with mock.patch.dict('os.environ', env, clear=True):
            self.assertEqual(reader.push_token(), 'g')

    def test_falls_back_and_reports_missing(self):
        with mock.patch.dict('os.environ', {}, clear=True):
            self.assertEqual(reader.push_token(), '')
        with mock.patch.dict('os.environ', {'GH_TOKEN': 'h'}, clear=True):
            self.assertEqual(reader.push_token(), 'h')


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class PushFeedTests(unittest.TestCase):
    def setUp(self):
        self.feed = Path(self.id().replace('.', '_') + '.json')
        self.feed.write_text(json.dumps({'version': 1, 'items': {}}), encoding='utf-8')
        self.env = mock.patch.dict('os.environ', {'GITHUB_TOKEN': 't'}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(lambda: self.feed.exists() and self.feed.unlink())

    def _patch(self, get=None, put=None):
        get = get or (lambda request, timeout: _FakeResponse(
            json.dumps({'sha': 'abc123'}).encode()))
        put = put or (lambda request, timeout: _FakeResponse(
            json.dumps({'commit': {'sha': 'deadbeef1'}}).encode()))
        return mock.patch.multiple(reader.urllib.request, urlopen=mock.DEFAULT), get, put

    def test_missing_token_does_not_touch_network(self):
        with mock.patch.dict('os.environ', {}, clear=True), \
                mock.patch.object(reader.urllib.request, 'urlopen') as opener:
            self.assertEqual(reader.push_feed('https://github.com/o/r.git', self.feed),
                             ('no-token', 'GITHUB_TOKEN yok'))
        opener.assert_not_called()

    def test_successful_push_sends_existing_sha(self):
        calls = []

        class Resp(_FakeResponse):
            pass

        def fake_urlopen(request, timeout=30):
            calls.append(request)
            if request.get_method() == 'PUT':
                return _FakeResponse(json.dumps({'commit': {'sha': 'deadbeef1'}}).encode())
            return _FakeResponse(json.dumps({'sha': 'abc123'}).encode())

        with mock.patch.object(reader.urllib.request, 'urlopen', side_effect=fake_urlopen):
            state, detail = reader.push_feed('https://github.com/o/r.git', self.feed,
                                             path='aggregator_feed.json', branch='main')
        self.assertEqual((state, detail), ('pushed', 'deadbee'))
        payload = json.loads(calls[-1].data.decode())
        self.assertEqual(payload['sha'], 'abc123')       # güncelleme, oluşturma değil
        self.assertEqual(payload['branch'], 'main')
        self.assertEqual(base64.b64decode(payload['content']).decode(),
                         self.feed.read_text(encoding='utf-8'))
        self.assertIn('Bearer t', calls[0].headers.get('Authorization', ''))

    def test_first_push_has_no_sha(self):
        calls = []

        def fake_urlopen(request, timeout=30):
            calls.append(request)
            if request.get_method() == 'PUT':
                return _FakeResponse(json.dumps({'commit': {'sha': 'aaa1111'}}).encode())
            raise HTTPError(request.full_url, 404, 'Not Found', {}, None)

        with mock.patch.object(reader.urllib.request, 'urlopen', side_effect=fake_urlopen):
            state, _ = reader.push_feed('https://github.com/o/r.git', self.feed)
        self.assertEqual(state, 'pushed')
        self.assertNotIn('sha', json.loads(calls[-1].data.decode()))

    def test_network_error_is_reported_not_raised(self):
        with mock.patch.object(reader.urllib.request, 'urlopen',
                               side_effect=URLError('offline')):
            state, detail = reader.push_feed('https://github.com/o/r.git', self.feed)
        self.assertEqual(state, 'error')
        self.assertIn('ağ', detail)

    def test_conflict_is_not_an_error(self):
        def fake_urlopen(request, timeout=30):
            if request.get_method() == 'PUT':
                raise HTTPError(request.full_url, 409, 'Conflict', {}, None)
            return _FakeResponse(json.dumps({'sha': 'x'}).encode())

        with mock.patch.object(reader.urllib.request, 'urlopen', side_effect=fake_urlopen):
            state, _ = reader.push_feed('https://github.com/o/r.git', self.feed)
        self.assertEqual(state, 'unchanged')

    def test_missing_feed_file_is_reported(self):
        state, detail = reader.push_feed('https://github.com/o/r.git', '/nonexistent.json')
        self.assertEqual(state, 'error')

    def test_token_is_never_written_into_repo_files(self):
        """Token yalnız HTTP başlığında; diskte iz bırakmamalı."""
        source = (ROOT / 'yerel_okuyucu.py').read_text(encoding='utf-8')
        self.assertNotIn('git config', source)
        self.assertNotIn('credential.helper', source)


class CliPushTests(unittest.TestCase):
    def test_push_without_token_is_refused_before_network(self):
        argv = ['--url', 'https://www.epey.com/televizyon/x.html',
                '--push', 'https://github.com/cnbrkc/indtkp-asist.git']
        with mock.patch.dict('os.environ', {}, clear=True), \
                mock.patch.object(reader, 'run_once') as run_once:
            with self.assertRaises(SystemExit):
                reader.main(argv)
        run_once.assert_not_called()

    def test_bad_push_url_is_refused(self):
        with mock.patch.dict('os.environ', {'GITHUB_TOKEN': 't'}, clear=True):
            with self.assertRaises(SystemExit):
                reader.main(['--url', 'https://www.epey.com/x.html', '--push', 'nope'])


if __name__ == '__main__':
    unittest.main()


class DocumentationTests(unittest.TestCase):
    """Belge, ölçülen gerçeklerle çelişmemeli."""

    def test_modem_guide_exists_and_is_honest(self):
        text = (ROOT / 'docs' / 'modem-uzerinde-okuyucu.md').read_text(encoding='utf-8')
        self.assertIn('--push', text)
        self.assertIn('Entware', text)
        # Önce ölçüm: modemde kurmadan önce curl ile denenmeli.
        self.assertIn('curl', text)
        # Yanlış yönlendirmeye karşı uyarı var mı?
        self.assertIn('bypass-cloudflare', text)
        self.assertIn('Cloudflare **hesabınızda**', text)

    def test_guide_matches_real_cli_flags(self):
        """Belgede geçen her bayrak gerçekten var olmalı (kopyala-çalıştır)."""
        import re
        text = (ROOT / 'docs' / 'modem-uzerinde-okuyucu.md').read_text(encoding='utf-8')
        code = (ROOT / 'yerel_okuyucu.py').read_text(encoding='utf-8')
        for flag in set(re.findall(r'--[a-z-]{3,}', text)):
            self.assertIn(f"'{flag}'", code, f'{flag} kodda yok')
