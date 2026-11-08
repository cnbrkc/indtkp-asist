"""Hata mesajları doğru teşhis vermeli: engel ≠ sayfa değişikliği."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from product_tracker import READ_FAILURES, read_failure_text


class FailureTextTests(unittest.TestCase):
    def test_block_says_the_server_refused_and_names_403(self):
        text = read_failure_text('Epey', {'code': 'BLOCKED', 'http_status': 403})
        self.assertIn('Epey okunamadı', text)
        self.assertIn('403', text)
        self.assertIn('Sayfa değişmedi', text)
        self.assertNotIn('sayfa değişikliği', text.lower())

    def test_rate_limit_mentions_retry(self):
        self.assertIn('429', read_failure_text('Cimri', {'code': 'RATE_LIMITED'}))

    def test_parse_error_includes_reason(self):
        text = read_failure_text('Epey', {'code': 'PARSE', 'reason': 'JSON-LD fiyatı uyuşmuyor.'})
        self.assertIn('uyuşmuyor', text)

    def test_unknown_code_falls_back_without_crashing(self):
        for body in ({}, None, {'code': 'HTTP_503'}, {'code': 'WAT'}):
            self.assertIn('okunamadı', read_failure_text('Epey', body))
        self.assertIn('503', read_failure_text('Epey', {'code': 'HTTP_503'}))

    def test_every_known_code_has_a_sentence(self):
        for code, sentence in READ_FAILURES.items():
            self.assertGreater(len(sentence), 20, code)


class WorkerEndToEndTests(unittest.IsolatedAsyncioTestCase):
    """read_product, worker'ın `code` alanını kullanıcı cümlesine çevirmeli."""

    async def test_blocked_worker_output_becomes_a_clear_message(self):
        import json
        from types import SimpleNamespace
        from unittest import mock
        import product_tracker as pt

        process = SimpleNamespace(returncode=1, communicate=mock.AsyncMock(return_value=(
            json.dumps({'ok': False, 'error': 'HTTPError', 'code': 'BLOCKED',
                        'http_status': 403}).encode(), b'')))
        with mock.patch.object(pt.asyncio, 'create_subprocess_exec',
                               mock.AsyncMock(return_value=process)):
            with self.assertRaises(ValueError) as ctx:
                await pt.read_product('https://www.epey.com/televizyon/tcl-65c6k.html')
        message = str(ctx.exception)
        self.assertIn('Epey okunamadı', message)
        self.assertIn('403', message)
        self.assertNotIn('sayfa değişikliği', message.lower())


if __name__ == '__main__':
    unittest.main()
