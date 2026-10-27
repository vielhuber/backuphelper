import json
import os
from pathlib import Path
import shutil
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

    def test_manual_backup_runs_with_or_without_a_schedule(self):
        for schedule in (None, '*-*-* 01:00:00'):
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


class SchedulerTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.units = self.root / 'units'
        self.units.mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.calls = self.root / 'calls'
        self.config = self.root / 'config.yaml'
        self.installer = self.root / 'install-systemd.sh'
        self.installer.write_text((ROOT / 'install-systemd.sh').read_text().replace(
            '/etc/systemd/system', str(self.units)))
        control = self.bin / 'systemctl'
        control.write_text('#!/usr/bin/env python3\nimport json, os, sys\n'
                           'with open(os.environ["TEST_CALLS"], "a") as f: f.write(json.dumps(sys.argv[1:]) + "\\n")\n')
        control.chmod(0o755)
        self.environment = {**os.environ, 'PATH': str(self.bin) + ':' + os.environ['PATH'],
                            'TEST_CALLS': str(self.calls)}

    def install(self, config, runner='/usr/bin/true', arguments=()):
        self.config.write_text(json.dumps(config))
        return subprocess.run(['bash', str(self.installer), '--config', str(self.config),
                               '--user', subprocess.check_output(['id', '-un'], text=True).strip(), '--', runner, *arguments],
                              env=self.environment, capture_output=True, text=True, timeout=15)

    def test_calendar_timers_are_persistent_and_manual_jobs_have_no_timer(self):
        result = self.install({'daily': {'schedule': '*-*-* 01:00:00'},
                               'weekly': {'schedule': 'Sun *-*-* 00:30:00'}, 'manual': {}})
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        for name, calendar in [('daily', '*-*-* 01:00:00'), ('weekly', 'Sun *-*-* 00:30:00')]:
            timer = self.units / f'backuphelper-{name}.timer'
            text = timer.read_text()
            self.assertIn(f'OnCalendar={calendar}\n', text)
            self.assertIn('Persistent=true\n', text)
            self.assertIn(f'Unit=backuphelper@{name}.service\n', text)
        self.assertFalse((self.units / 'backuphelper-manual.timer').exists())
        service = (self.units / 'backuphelper@.service').read_text()
        self.assertIn('"--config"', service)
        self.assertIn(' %i\n', service)
        verify = subprocess.run(['systemd-analyze', 'verify', *map(str, self.units.iterdir())],
                                capture_output=True, text=True)
        self.assertEqual(0, verify.returncode, verify.stderr)

    def test_invalid_schedule_or_legacy_interval_changes_nothing(self):
        for invalid in [{'schedule': 'not a calendar'}, {'schedule': 'daily\nUnit=other.service'}, {'interval': 20}]:
            with self.subTest(invalid=invalid):
                result = self.install({'valid': {'schedule': 'daily'}, 'invalid': invalid})
                self.assertNotEqual(0, result.returncode)
                self.assertEqual([], list(self.units.iterdir()))
                self.assertFalse(self.calls.exists())

    def test_removed_jobs_are_disabled_but_unrelated_timers_remain(self):
        result = self.install({'old': {'schedule': 'daily'}})
        self.assertEqual(0, result.returncode, result.stderr)
        custom = self.units / 'backuphelper-personal.timer'
        custom.write_text('[Timer]\nOnCalendar=weekly\n')
        self.calls.write_text('')
        result = self.install({'new': {'schedule': 'daily'}})
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse((self.units / 'backuphelper-old.timer').exists())
        self.assertTrue(custom.exists())
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertIn(['disable', '--now', 'backuphelper-old.timer'], calls)
        self.assertFalse(any('backuphelper-personal.timer' in call for call in calls))

    def test_runner_arguments_keep_systemd_metacharacters_literal(self):
        result = self.install({'daily': {'schedule': 'daily'}}, arguments=('100%', '$HOME " \\ test'))
        self.assertEqual(0, result.returncode, result.stderr)
        service = (self.units / 'backuphelper@.service').read_text()
        self.assertIn('%%', service)
        self.assertIn('$$', service)
        self.assertIn('\\"', service)
        self.assertIn('\\\\', service)

    def test_invalid_service_definition_changes_nothing(self):
        runner = self.root / 'unsupported " executable'
        shutil.copy('/usr/bin/true', runner)
        result = self.install({'daily': {'schedule': 'daily'}}, str(runner))
        self.assertNotEqual(0, result.returncode)
        self.assertEqual([], list(self.units.iterdir()))
        self.assertFalse(self.calls.exists())

    def test_config_path_keeps_the_alias_used_inside_a_container(self):
        alias = self.root / 'mounted-alias'
        alias.symlink_to(self.root, target_is_directory=True)
        self.config = alias / 'config.yaml'
        result = self.install({'daily': {'schedule': 'daily'}})
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(f'"{self.config}"', (self.units / 'backuphelper@.service').read_text())


if __name__ == '__main__':
    unittest.main()
