import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]


class BackupTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.target = self.root / 'archives'
        self.target.mkdir()
        self.source = self.root / 'source'
        self.source.write_text('backup content\n')
        self.config = self.root / 'config.yaml'

    def write_config(self, jobs):
        self.config.write_text(json.dumps(jobs))

    def run_backup(self, *jobs):
        return subprocess.run([str(ROOT / 'backuphelper.sh'), '--config', str(self.config), *jobs],
                              capture_output=True, text=True, timeout=15)

    def daily_job(self):
        return {'target': str(self.target), 'keep': 2,
                'schedule': f'0 {(time.localtime().tm_hour + 2) % 24} * * *',
                'sources': [{'type': 'path', 'path': str(self.source)}]}

    def test_manual_backup_runs_with_or_without_a_schedule(self):
        for schedule in (None, '0 1 * * *'):
            with self.subTest(schedule=schedule):
                job = {'target': str(self.target), 'keep': 2,
                       'sources': [{'type': 'path', 'path': str(self.source)}]}
                if schedule:
                    job['schedule'] = schedule
                self.write_config({'local': job})
                result = self.run_backup('local')
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                archive = next(self.target.glob('local-*.tar.gz'))
                with tarfile.open(archive) as contents:
                    self.assertEqual(b'backup content\n', contents.extractfile(str(self.source)[1:]).read())
                archive.unlink()

    def test_old_interval_is_rejected_instead_of_silently_running_every_time(self):
        self.write_config({'local': {'target': str(self.target), 'keep': 2, 'interval': 20,
                                    'sources': [{'type': 'path', 'path': str(self.source)}]}})
        result = self.run_backup()
        self.assertNotEqual(0, result.returncode)
        self.assertIn('schedule', result.stdout + result.stderr)
        self.assertEqual([], list(self.target.iterdir()))

    def test_manual_calls_also_reject_non_cron_schedules(self):
        for schedule in ['daily', '*-*-* 01:00:00', '60 1 * * *', None, False]:
            with self.subTest(schedule=schedule):
                self.write_config({'local': {'target': str(self.target), 'keep': 2, 'schedule': schedule,
                                            'sources': [{'type': 'path', 'path': str(self.source)}]}})
                result = self.run_backup()
                self.assertNotEqual(0, result.returncode)
                self.assertEqual([], list(self.target.iterdir()))

    def test_missed_backup_is_caught_up_once(self):
        self.write_config({'local': self.daily_job()})
        previous = self.target / 'local-2020-01-01-000000.tar.gz'
        with tarfile.open(previous, 'w:gz'):
            pass
        old = time.time() - 48 * 3600
        os.utime(previous, (old, old))
        result = self.run_backup()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        archives = {p.name: p.stat().st_mtime_ns for p in self.target.iterdir()}
        self.assertEqual(2, len(archives))
        result = self.run_backup()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(archives, {p.name: p.stat().st_mtime_ns for p in self.target.iterdir()})

    def test_new_job_waits_for_a_term_after_config_creation(self):
        self.write_config({'local': self.daily_job()})
        result = self.run_backup()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual([], list(self.target.iterdir()))
        old = time.time() - 48 * 3600
        os.utime(self.config, (old, old))
        result = self.run_backup()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(1, len(list(self.target.glob('*.tar.gz'))))

    def test_manual_jobs_run_only_when_selected(self):
        job = self.daily_job()
        del job['schedule']
        self.write_config({'local': job})
        result = self.run_backup()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual([], list(self.target.iterdir()))
        result = self.run_backup('local')
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(1, len(list(self.target.glob('*.tar.gz'))))

    def test_failed_due_job_is_retried_on_next_poll(self):
        marker = self.root / 'ready'
        job = self.daily_job()
        job['sources'] = [{'type': 'command', 'command': f'test -e "{marker}" && cat "{self.source}"'}]
        self.write_config({'local': job})
        old = time.time() - 48 * 3600
        os.utime(self.config, (old, old))
        self.assertNotEqual(0, self.run_backup().returncode)
        self.assertEqual([], list(self.target.iterdir()))
        marker.touch()
        result = self.run_backup()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(1, len(list(self.target.glob('*.tar.gz'))))

    def test_cron_poll_does_not_queue_behind_a_running_backup(self):
        started, release = self.root / 'started', self.root / 'release'
        job = self.daily_job()
        job['sources'] = [{'type': 'command', 'command':
                          f'touch "{started}"; while [ ! -e "{release}" ]; do sleep .05; done; printf fixture'}]
        self.write_config({'local': job})
        first = subprocess.Popen([str(ROOT / 'backuphelper.sh'), '--config', str(self.config), 'local'],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while not started.exists() and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertTrue(started.exists())
            result = self.run_backup()
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIsNone(first.poll())
        finally:
            release.touch()
            first.communicate(timeout=10)

    def test_different_jobs_from_concurrent_calls_wait_for_each_other(self):
        events = self.root / 'events'
        release = self.root / 'release'
        jobs = {}
        for name in ('first', 'second'):
            wait = f'while [ ! -e "{release}" ]; do sleep .05; done; ' if name == 'first' else ''
            jobs[name] = {'target': str(self.target), 'keep': 2, 'sources': [{'type': 'command', 'command':
                f'printf "{name}-start\\n" >> "{events}"; {wait}'
                f'printf "{name}-end\\n" >> "{events}"; printf payload'}]}
        self.write_config(jobs)
        command = [str(ROOT / 'backuphelper.sh'), '--config', str(self.config)]
        first = subprocess.Popen([*command, 'first'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        second = None
        try:
            deadline = time.monotonic() + 5
            while not events.exists() and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertTrue(events.exists(), 'First backup did not start')
            second = subprocess.Popen([*command, 'second'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            time.sleep(.5)
            self.assertEqual('first-start\n', events.read_text())
            release.touch()
            for process in (first, second):
                output, error = process.communicate(timeout=10)
                self.assertEqual(0, process.returncode, output + error)
            self.assertEqual(['first-start', 'first-end', 'second-start', 'second-end'], events.read_text().splitlines())
        finally:
            release.touch()
            for process in (first, second):
                if process:
                    if process.poll() is None:
                        process.kill()
                    process.communicate()


if __name__ == '__main__':
    unittest.main()
