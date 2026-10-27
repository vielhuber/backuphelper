import importlib.util
from pathlib import Path
import os
import subprocess
import time
from datetime import datetime, timezone
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('cron_schedule', ROOT / 'cron_schedule.py')
cron = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cron)


class CronScheduleTest(unittest.TestCase):
    def setUp(self):
        previous = os.environ.get('TZ')
        os.environ['TZ'] = 'UTC'
        time.tzset()
        self.addCleanup(self.restore_timezone, previous)

    def restore_timezone(self, previous):
        if previous is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = previous
        time.tzset()

    def test_next_appointments_follow_cron_rules(self):
        cases = [
            ('0 1 * * *', '2026-01-01 00:00:00', '2026-01-01 01:00:00'),
            ('30 0 * * 0', '2026-01-01 00:00:00', '2026-01-04 00:30:00'),
            ('30 0 * * 7', '2026-01-01 00:00:00', '2026-01-04 00:30:00'),
            ('30 0 * * SUN', '2026-01-01 00:00:00', '2026-01-04 00:30:00'),
            ('30 0 * * sat-sun', '2026-01-03 01:00:00', '2026-01-04 00:30:00'),
            ('30 0 * * 5-0', '2026-01-03 01:00:00', '2026-01-04 00:30:00'),
            ('*/15 9-17 * * mon-fri', '2026-01-02 17:46:00', '2026-01-05 09:00:00'),
            ('5,35 1-5/2 * JAN,MAR MON', '2026-01-01 00:00:00', '2026-01-05 01:05:00'),
            ('0 */23 * * *', '2026-01-01 00:01:00', '2026-01-01 23:00:00'),
            ('0 1 1,15 * mon', '2026-01-01 01:01:00', '2026-01-05 01:00:00'),
            ('0 1 1,15 * mon', '2026-01-13 00:00:00', '2026-01-15 01:00:00'),
            ('0 1 */2 * mon', '2026-01-05 02:00:00', '2026-01-19 01:00:00'),
            ('0 1 1-31 * mon', '2026-01-06 00:00:00', '2026-01-06 01:00:00'),
            ('0 1 1,15 * */2', '2026-01-01 02:00:00', '2026-01-15 01:00:00'),
            ('0 0 29 2 *', '2026-01-01 00:00:00', '2028-02-29 00:00:00'),
            ('0 0 31 2 mon', '2026-01-01 00:00:00', '2026-02-02 00:00:00'),
            ('  0\t1 * * *  ', '2026-01-01 00:00:00', '2026-01-01 01:00:00'),
        ]
        for expression, start, expected in cases:
            with self.subTest(expression=expression, start=start):
                since = datetime.fromisoformat(start).timestamp()
                scheduled = datetime.fromisoformat(expected).timestamp()
                self.assertFalse(cron.due(expression, since, scheduled - 1))
                self.assertTrue(cron.due(expression, since, scheduled))
                self.assertFalse(cron.due(expression, scheduled, scheduled + 30))

    def test_invalid_or_non_five_field_expressions_are_rejected(self):
        invalid = ['daily', '@daily', '*-*-* 01:00:00', 'Sun *-*-* 00:30:00',
                   '* * * *', '* * * * * *', '60 * * * *', '* 24 * * *', '* * 0 * *',
                   '* * 32 * *', '* * * 0 *', '* * * 13 *', '* * * * 8',
                   '*/0 * * * *', '5-2 * * * *', '0,,1 * * * *', 'x * * * *',
                   '5/15 * * * *', '0 1 * * mon/2',
                   '0 1 ? * mon', '0 1 L * *', '0 1 * * mon#2', '~ * * * *',
                   '0 1 * * *\nUnit=other.service', '0 1 * * *\r', '0 1 * * * UTC']
        for expression in invalid:
            with self.subTest(expression=expression):
                with self.assertRaises(ValueError):
                    cron.parse(expression)

    def test_cli_errors_have_no_partial_output_or_traceback(self):
        result = subprocess.run(['python3', str(ROOT / 'cron_schedule.py'), 'daily'], capture_output=True, text=True)
        self.assertEqual(2, result.returncode)
        self.assertEqual('', result.stdout)
        self.assertIn('five', result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_impossible_date_and_reversed_clock_are_not_due(self):
        since = datetime(2026, 1, 1).timestamp()
        now = datetime(2030, 1, 1).timestamp()
        self.assertFalse(cron.due('0 0 31 2 *', since, now))
        self.assertFalse(cron.due('* * * * *', now, since))

    def test_daylight_saving_missing_and_repeated_hours(self):
        os.environ['TZ'] = 'Europe/Berlin'
        time.tzset()
        spring_start = datetime(2026, 3, 29, 0, tzinfo=timezone.utc).timestamp()
        spring_end = datetime(2026, 3, 29, 2, tzinfo=timezone.utc).timestamp()
        self.assertFalse(cron.due('30 2 * * *', spring_start, spring_end))
        fall_start = datetime(2026, 10, 25, 0, tzinfo=timezone.utc).timestamp()
        first = datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc).timestamp()
        second = datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc).timestamp()
        self.assertTrue(cron.due('30 2 * * *', fall_start, first))
        self.assertFalse(cron.due('30 2 * * *', first, second - 1))
        self.assertTrue(cron.due('30 2 * * *', first, second))


if __name__ == '__main__':
    unittest.main()
