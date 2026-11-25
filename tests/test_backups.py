from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
from threading import Thread
import time
import unittest
from urllib.parse import parse_qs, urlsplit


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

    def test_git_sources_select_the_same_files_locally_and_over_ssh(self):
        parent = self.root / "projects with 'quotes'"
        repository = parent / 'project'
        repository.mkdir(parents=True)
        subprocess.run(['git', 'init', '-q', str(repository)], check=True)
        for name in ('unchanged', 'modified', 'staged', 'deleted'):
            (repository / name).write_text('committed\n')
        for directory in ('cache', 'cacheable', 'backwpup', 'backwpup-backups', 'backwpup-restore'):
            nested = repository / 'deep' / directory
            nested.mkdir(parents=True)
            (nested / 'data').write_text('committed\n')
        (repository / '.gitignore').write_text('.env\nvendor/\n')
        subprocess.run(['git', 'add', '.'], cwd=repository, check=True)
        subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
                        'commit', '-qm', 'fixture'], cwd=repository, check=True)
        committed = (repository / 'unchanged').stat()
        os.utime(repository / 'unchanged', ns=(committed.st_atime_ns, committed.st_mtime_ns + 2_000_000_000))
        (repository / 'modified').write_text('working tree\n')
        (repository / 'staged').write_text('staged change\n')
        (repository / 'new staged').write_text('new staged file\n')
        subprocess.run(['git', 'add', 'staged', 'new staged'], cwd=repository, check=True)
        (repository / 'deleted').unlink()
        for directory in (repository / 'deep').iterdir():
            (directory / 'data').write_text('modified\n')
        (repository / 'untracked\nfile').write_text('untracked\n')
        (repository / '.env').write_text('fixture\n')
        (repository / 'vendor').mkdir()
        (repository / 'vendor' / 'ignored').write_text('excluded\n')
        (parent / 'plain').mkdir()
        (parent / 'plain' / 'data').write_text('plain directory\n')
        for directory in ('cache', 'cacheable', 'backwpup-backups'):
            nested = parent / 'plain' / 'deep' / directory
            nested.mkdir(parents=True)
            (nested / 'data').write_text('plain directory\n')
        (parent / 'skip').mkdir()
        (parent / 'skip' / 'data').write_text('skipped\n')
        binary = self.root / 'bin'
        binary.mkdir()
        ssh = binary / 'ssh'
        ssh.write_text('#!/usr/bin/env python3\nimport os, sys\n'
                       'assert sys.argv[1:6] == ["-o", "BatchMode=yes", "-p", "2222", "fixture"]\n'
                       'os.execvp("bash", ["bash", "-c", sys.argv[6]])\n')
        ssh.chmod(0o755)
        expected = {str(repository / name)[1:] for name in
                    ('modified', 'staged', 'new staged', 'untracked\nfile', '.env')}
        expected.add(str(parent / 'plain' / 'data')[1:])
        expected.add(str(repository / 'deep' / 'cacheable' / 'data')[1:])
        expected.add(str(parent / 'plain' / 'deep' / 'cacheable' / 'data')[1:])
        for remote, exclusions in ((False, ['vendor', 'backwpup*', 'cache']),
                                   (True, ['vendor', 'backwpup*', 'cache']), (True, [])):
            with self.subTest(remote=remote, exclusions=exclusions):
                expected_files = expected.copy()
                if not exclusions:
                    expected_files.add(str(repository / 'vendor' / 'ignored')[1:])
                    for directory in ('cache', 'backwpup', 'backwpup-backups', 'backwpup-restore'):
                        expected_files.add(str(repository / 'deep' / directory / 'data')[1:])
                    for directory in ('cache', 'backwpup-backups'):
                        expected_files.add(str(parent / 'plain' / 'deep' / directory / 'data')[1:])
                source = {'type': 'git', 'path': str(parent), 'skip': ['skip']}
                if remote:
                    source.update(ssh='fixture', ssh_options=['-p', '2222'])
                self.write_config({'local': {'target': str(self.target), 'keep': 2,
                                            'exclude': exclusions, 'sources': [source]}})
                result = subprocess.run([str(ROOT / 'backuphelper.sh'), '--config', str(self.config), 'local'],
                                        capture_output=True, text=True, timeout=15,
                                        env={**os.environ, 'PATH': f'{binary}:{os.environ["PATH"]}'})
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                archive = next(self.target.glob('local-*.tar.gz'))
                with tarfile.open(archive) as outer:
                    if remote:
                        self.assertEqual(['git-1.tar'], outer.getnames())
                        with tarfile.open(fileobj=outer.extractfile('git-1.tar')) as contents:
                            self.assertEqual(expected_files, {m.name for m in contents if m.isfile()})
                    else:
                        self.assertEqual(expected_files, {m.name for m in outer if m.isfile()})
                archive.unlink()
        (parent / 'broken' / '.git').mkdir(parents=True)
        result = subprocess.run([str(ROOT / 'backuphelper.sh'), '--config', str(self.config), 'local'],
                                capture_output=True, text=True, timeout=15,
                                env={**os.environ, 'PATH': f'{binary}:{os.environ["PATH"]}'})
        self.assertNotEqual(0, result.returncode)
        self.assertEqual([], list(self.target.iterdir()))

    def test_failed_ssh_git_source_does_not_publish_an_archive(self):
        binary = self.root / 'bin'
        binary.mkdir()
        ssh = binary / 'ssh'
        ssh.write_text('#!/bin/sh\nprintf partial\nexit 255\n')
        ssh.chmod(0o755)
        self.write_config({'remote': {'target': str(self.target), 'keep': 2,
                                     'sources': [{'type': 'git', 'path': '/projects', 'ssh': 'fixture'}]}})
        result = subprocess.run([str(ROOT / 'backuphelper.sh'), '--config', str(self.config), 'remote'],
                                capture_output=True, text=True, timeout=15,
                                env={**os.environ, 'PATH': f'{binary}:{os.environ["PATH"]}'})
        self.assertNotEqual(0, result.returncode)
        self.assertEqual([], list(self.target.iterdir()))

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

    def test_success_url_notifies_after_publishing_an_archive(self):
        requests = []
        target = self.target

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, sorted(file.name for file in target.glob('*.tar.gz'))))
                self.send_response(503 if self.path.startswith('/unavailable') else 204)
                self.end_headers()

            def log_message(self, *_):
                pass

        with HTTPServer(('127.0.0.1', 0), Handler) as server:
            listener = Thread(target=server.serve_forever)
            listener.start()
            try:
                url = f'http://127.0.0.1:{server.server_port}/'
                job = {**self.daily_job(), 'on_success_url': url + '?name=$BACKUP_NAME$&date=$BACKUP_DATE$'}
                broken = {**job, 'sources': [{'type': 'command', 'command': 'exit 5'}]}
                unavailable = {**job, 'on_success_url': url + 'unavailable?token=private-marker'}
                del broken['schedule'], unavailable['schedule']
                self.write_config({'fachlehrer': job, 'broken': broken, 'unavailable': unavailable})
                self.assertEqual(0, self.run_backup().returncode)
                self.assertNotEqual(0, self.run_backup('broken').returncode)
                self.assertEqual([], requests)

                old = time.time() - 48 * 3600
                os.utime(self.config, (old, old))
                result = self.run_backup()
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                self.assertEqual(1, len(requests))
                requested_url, archives = requests[0]
                archive = next(self.target.glob('fachlehrer-*.tar.gz'))
                self.assertEqual([archive.name], archives)
                self.assertEqual({'name': ['fachlehrer'], 'date': [archive.name[len('fachlehrer-'):][:10]]},
                                 parse_qs(urlsplit(requested_url).query))
                self.assertEqual(0, self.run_backup().returncode)
                self.assertEqual(1, len(requests))

                result = self.run_backup('unavailable')
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                self.assertEqual(2, len(requests))
                self.assertIn('HTTP 503', result.stdout)
                self.assertNotIn('private-marker', result.stdout + result.stderr)
                self.assertEqual(1, len(list(self.target.glob('unavailable-*.tar.gz'))))
            finally:
                server.shutdown()
                listener.join()

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
