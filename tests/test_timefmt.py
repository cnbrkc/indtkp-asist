"""Saat gösterimi: sunucu saat dilimi ne olursa olsun TSİ ve etiketli olmalı.

Regresyon: GitHub Actions runner'ları UTC çalıştığı için ``time.strftime``
ile yazılan "Son kontrol: 10:44" aslında TSİ 13:44'ü gösteriyordu ve üç saat
geride kalmış gibi okunuyordu.
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

import timefmt

ROOT = Path(__file__).resolve().parents[1]
# 2026-11-06 13:44 TSİ == 10:44 UTC
EPOCH = 1793961840


class TimeFormatTests(unittest.TestCase):
    def test_tsi_clock_and_stamp(self):
        self.assertEqual(timefmt.clock(EPOCH), '13:44')
        self.assertEqual(timefmt.stamp(EPOCH), '06.11 13:44')
        self.assertEqual(timefmt.LABEL, 'TSİ')

    def test_since_is_unambiguous(self):
        text = timefmt.since(EPOCH, now=EPOCH + 6 * 60)
        self.assertEqual(text, '06.11 13:44 TSİ (6 dk önce)')

    def test_age_text_boundaries(self):
        self.assertEqual(timefmt.age_text(0), 'az önce')
        self.assertEqual(timefmt.age_text(44), 'az önce')
        self.assertEqual(timefmt.age_text(60), '1 dk önce')
        self.assertEqual(timefmt.age_text(89), '1 dk önce')
        self.assertEqual(timefmt.age_text(90), '1 dk önce')
        self.assertEqual(timefmt.age_text(3600), '1 sa önce')
        self.assertEqual(timefmt.age_text(3600 + 300), '1 sa 5 dk önce')
        self.assertEqual(timefmt.age_text(86400 + 7200), '1 gün 2 sa önce')
        self.assertEqual(timefmt.age_text(-10), 'az önce')

    def test_in_seconds_points_forward(self):
        self.assertEqual(timefmt.in_seconds(300, now=EPOCH), '13:49 TSİ')

    def test_result_does_not_depend_on_host_timezone(self):
        """TZ=UTC ve TZ=America/New_York aynı TSİ çıktısını vermeli."""
        script = (
            'import sys; sys.path.insert(0, %r)\n'
            'import timefmt\n'
            'print(timefmt.clock(%d), timefmt.stamp(%d))\n' % (str(ROOT), EPOCH, EPOCH)
        )
        outputs = set()
        for tz in ('UTC', 'America/New_York', 'Asia/Tokyo'):
            env = {**os.environ, 'TZ': tz}
            result = subprocess.run([sys.executable, '-c', script], env=env,
                                    capture_output=True, text=True, check=True)
            outputs.add(result.stdout.strip())
        self.assertEqual(outputs, {'13:44 06.11 13:44'})


class LogTimezoneTests(unittest.TestCase):
    def test_log_timestamps_are_tsi_in_every_host_zone(self):
        """Regresyon: Actions log'u UTC yazıyordu; kullanıcı saatle eşleştiremiyordu."""
        script = (
            'import logging, sys\n'
            'sys.path.insert(0, %r)\n'
            'import bot  # noqa: F401 - converter ataması modül yüklenirken yapılır\n'
            'logging.getLogger("probe").warning("prob")\n' % str(ROOT)
        )
        outputs = set()
        for tz in ('UTC', 'Asia/Tokyo', 'America/New_York'):
            env = {**os.environ, 'TZ': tz}
            result = subprocess.run([sys.executable, '-c', script], env=env,
                                    capture_output=True, text=True, check=True)
            lines = [line for line in result.stderr.splitlines() if 'prob' in line]
            self.assertTrue(lines, result.stderr)
            outputs.add(lines[0][:13])  # "2026-11-06 14"
        self.assertEqual(len(outputs), 1)
        self.assertRegex(outputs.pop(), r'^\d{4}-\d{2}-\d{2} \d{2}$')


class StatusTextTimezoneTests(unittest.TestCase):
    def test_status_shows_start_time_and_now_in_tsi(self):
        import bot

        bot.SOURCES.clear()
        bot.SOURCE_FAILURES.clear()
        bot.SOURCE_IDS.clear()
        bot.CONTROL_NAMES.clear()
        bot.CONTROL_IDS.clear()
        status = bot.build_status_text({'include_keywords': ['çay']})
        line = next(text for text in status.splitlines() if 'Çalışma süresi' in text)
        self.assertIn('başlangıç', line)
        self.assertIn('TSİ', line)
        self.assertIn('şimdi', line)


if __name__ == '__main__':
    unittest.main()
