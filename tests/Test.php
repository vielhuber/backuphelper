<?php
declare(strict_types=1);

use PHPUnit\Framework\TestCase;
use vielhuber\backuphelper\backuphelper;
use vielhuber\backuphelper\BackupException;

final class Test extends TestCase
{
    private string $root;

    protected function setUp(): void
    {
        $this->root = sys_get_temp_dir() . '/backuphelper-test-' . bin2hex(random_bytes(4));
        foreach (['target', 'projects', 'remote'] as $folder) {
            mkdir($this->root . '/' . $folder, 0777, true);
        }
    }

    protected function tearDown(): void
    {
        exec('rm -rf ' . escapeshellarg($this->root));
    }

    public function test_git_path_and_command_sources_end_up_in_one_archive(): void
    {
        $repository = $this->root . '/projects/app';
        mkdir($repository . '/node_modules/lib', 0777, true);
        file_put_contents($repository . '/.gitignore', ".env\nnode_modules/\n");
        file_put_contents($repository . '/tracked.txt', 'tracked');
        exec('git -C ' . escapeshellarg($repository) . ' init -q && git -C ' . escapeshellarg($repository) . ' add . && git -C ' .
            escapeshellarg($repository) . ' -c user.name=test -c user.email=test@example.test commit -qm init');
        file_put_contents($repository . '/.env', 'SECRET=1');
        file_put_contents($repository . '/untracked.txt', 'new');
        file_put_contents($repository . '/node_modules/lib/index.js', 'dependency');
        mkdir($this->root . '/projects/plain');
        file_put_contents($this->root . '/projects/plain/data.txt', 'data');
        mkdir($this->root . '/projects/skipped');
        file_put_contents($this->root . '/projects/skipped/big.bin', 'big');
        file_put_contents($this->root . '/single.conf', 'conf');
        $this->config(<<<YAML
        target: {$this->root}/target
        exclude: [node_modules]
        jobs:
            local:
                sources:
                    - type: git
                      path: {$this->root}/projects
                      skip: [skipped]
                    - type: path
                      path: {$this->root}/single.conf
                    - type: command
                      name: dump.sql
                      command: printf 'data\\n-- Dump completed on today\\n'
                      check: '^-- Dump completed on '
        YAML);
        $this->assertTrue($this->backup());
        $archives = glob($this->root . '/target/local-*.tar.gz');
        $this->assertCount(1, $archives);
        $members = $this->members($archives[0]);
        $projects = ltrim($this->root, '/') . '/projects';
        $this->assertContains('dump.sql', $members);
        $this->assertContains($projects . '/app/.env', $members);
        $this->assertContains($projects . '/app/untracked.txt', $members);
        $this->assertContains($projects . '/plain/data.txt', $members);
        $this->assertContains(ltrim($this->root, '/') . '/single.conf', $members);
        $this->assertNotContains($projects . '/app/tracked.txt', $members);
        $this->assertEmpty(preg_grep('/node_modules|skipped/', $members));
    }

    public function test_failed_check_writes_no_archive_and_reports_failure(): void
    {
        $this->config(<<<YAML
        target: {$this->root}/target
        jobs:
            broken:
                sources:
                    - type: command
                      name: dump.sql
                      command: printf 'incomplete'
                      check: '^-- Dump completed on '
        YAML);
        $this->assertFalse($this->backup());
        $this->assertSame([], glob($this->root . '/target/*'));
    }

    public function test_rotation_keeps_the_newest_archives_and_interval_skips_early_runs(): void
    {
        foreach (['2020-01-01-000000', '2020-01-02-000000', '2020-01-03-000000'] as $stamp) {
            touch($this->root . '/target/job-' . $stamp . '.tar.gz', strtotime('2020-01-03'));
        }
        touch($this->root . '/target/job-extra-2020-01-01-000000.tar.gz');
        $this->config(<<<YAML
        target: {$this->root}/target
        keep: 2
        interval: 1
        jobs:
            job:
                sources:
                    - type: command
                      name: note.txt
                      command: echo note
        YAML);
        $this->assertTrue($this->backup());
        $archives = array_map('basename', glob($this->root . '/target/job-2*.tar.gz'));
        $this->assertCount(2, $archives);
        $this->assertContains('job-2020-01-03-000000.tar.gz', $archives);
        $this->assertFileExists($this->root . '/target/job-extra-2020-01-01-000000.tar.gz');
        $this->assertTrue($this->backup());
        $this->assertSame($archives, array_map('basename', glob($this->root . '/target/job-2*.tar.gz')));
    }

    public function test_ftpsh_source_downloads_a_remote_tar_and_removes_it_remotely(): void
    {
        mkdir($this->root . '/remote/site/cache', 0777, true);
        file_put_contents($this->root . '/remote/site/index.php', 'site');
        file_put_contents($this->root . '/remote/site/cache/page.html', 'cache');
        $ftpsh = $this->root . '/ftpsh';
        file_put_contents($ftpsh, <<<'BASH'
        #!/usr/bin/env bash
        set -e
        [[ "$1" = --env ]] || exit 9
        cd "$(dirname "$2")/remote"
        shift 2
        if [[ "$1" = --download ]]; then cat "$2"; exit; fi
        "$@"
        BASH);
        chmod($ftpsh, 0755);
        $this->config(<<<YAML
        target: {$this->root}/target
        ftpsh: {$ftpsh}
        jobs:
            site:
                sources:
                    - type: ftpsh
                      name: files.tar
                      env: {$this->root}/site.env
                      path: site
                      exclude: [cache]
        YAML);
        $this->assertTrue($this->backup());
        $this->assertSame(['files.tar'], $this->members(glob($this->root . '/target/site-*.tar.gz')[0]));
        exec('tar -xzOf ' . escapeshellarg(glob($this->root . '/target/site-*.tar.gz')[0]) . ' files.tar | tar -t', $inner);
        $this->assertContains('site/index.php', $inner);
        $this->assertEmpty(preg_grep('/cache/', $inner));
        $this->assertSame([], glob($this->root . '/remote/backuphelper_*'));
    }

    public function test_missing_target_is_skipped_and_invalid_config_is_rejected(): void
    {
        $this->config(<<<YAML
        target: {$this->root}/unplugged
        jobs:
            job:
                sources:
                    - type: command
                      name: note.txt
                      command: echo note
        YAML);
        $this->assertTrue($this->backup());
        $this->config("jobs:\n    job:\n        sources:\n            - type: git\n              path: relative\n");
        $this->expectException(BackupException::class);
        $this->backup();
    }

    private function config(string $yaml): void
    {
        file_put_contents($this->root . '/config.yaml', $yaml);
    }

    private function backup(): bool
    {
        ob_start();
        try {
            return (new backuphelper())->config($this->root . '/config.yaml')->run();
        } finally {
            ob_end_clean();
        }
    }

    /**
     * @return list<string>
     */
    private function members(string $archive): array
    {
        exec('tar -tzf ' . escapeshellarg($archive), $members);
        return array_map(fn(string $member): string => rtrim($member, '/'), $members);
    }
}
