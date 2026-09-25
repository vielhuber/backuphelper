<?php
declare(strict_types=1);

namespace vielhuber\backuphelper;

final class JobRunner
{
    private string $ftpsh = ConfigReader::DEFAULT_FTPSH;

    /**
     * Binary used for ftpsh sources (a PATH name or an absolute path).
     */
    public function ftpsh(string $ftpsh): self
    {
        $this->ftpsh = $ftpsh;
        return $this;
    }

    /**
     * Write one archive when the job is due; a failing source aborts before any existing archive is touched.
     *
     * @throws BackupException
     */
    public function run(Job $job): JobStatus
    {
        if (!is_dir($job->target)) {
            $this->log('⚠️ ' . $job->name . ': target ' . $job->target . ' is missing; skipped');
            return JobStatus::Skipped;
        }
        $lock = fopen(sys_get_temp_dir() . '/backuphelper-' . md5($job->target . '/' . $job->name) . '.lock', 'c');
        if ($lock === false || !flock($lock, LOCK_EX | LOCK_NB)) {
            return JobStatus::Skipped;
        }
        array_map('unlink', glob($job->target . '/' . $job->name . '-*.partial') ?: []);
        $archives = $this->archives(job: $job);
        if ($archives !== [] && time() - (int) filemtime($archives[0]) < $job->interval * 3600) {
            return JobStatus::Skipped;
        }
        $this->log($job->name . ': started');
        $staging = sys_get_temp_dir() . '/backuphelper-' . bin2hex(random_bytes(8));
        mkdir($staging, 0700);
        $archive = $job->target . '/' . $job->name . '-' . date('Y-m-d-His') . '.tar.gz';
        try {
            $files = [];
            foreach ($job->sources as $source) {
                array_push($files, ...$this->collect(source: $source, staging: $staging));
            }
            $this->archive(files: $files, staging: $staging, exclude: $job->exclude, archive: $archive);
        } finally {
            $this->execute(command: ['rm', '-rf', '--', $staging], label: 'staging cleanup');
        }
        foreach (array_slice($this->archives(job: $job), $job->keep) as $outdated) {
            unlink($outdated);
        }
        $this->log('✅ ' . $job->name . ': ' . $archive . ' (' . round((int) filesize($archive) / 1048576) . ' MB)');
        return JobStatus::Created;
    }

    /**
     * Absolute paths the archive takes from one source; command and ftpsh sources are written into staging first.
     *
     * @return list<string>
     * @throws BackupException
     */
    private function collect(Source $source, string $staging): array
    {
        return match ($source->type) {
            SourceType::Path => [$source->path],
            SourceType::Git => $this->gitLeftovers(source: $source),
            SourceType::Command => [$this->commandOutput(source: $source, staging: $staging)],
            SourceType::Ftpsh => [$this->ftpshArchive(source: $source, staging: $staging)]
        };
    }

    /**
     * Folders below the source path: git repositories contribute untracked, ignored and modified files, other folders everything.
     *
     * @return list<string>
     * @throws BackupException
     */
    private function gitLeftovers(Source $source): array
    {
        $paths = [];
        foreach (array_diff(scandir($source->path) ?: [], ['.', '..', ...$source->skip]) as $name) {
            $folder = $source->path . '/' . $name;
            if (!is_dir($folder)) {
                continue;
            }
            if (!file_exists($folder . '/.git')) {
                $paths[] = $folder;
                continue;
            }
            $listing =
                $this->execute(
                    command: ['git', '-C', $folder, 'ls-files', '-z', '--others', '--modified', '--exclude-standard'],
                    label: 'git listing of ' . $folder
                ) .
                $this->execute(
                    command: ['git', '-C', $folder, 'ls-files', '-z', '--others', '--ignored', '--exclude-standard', '--directory'],
                    label: 'git listing of ' . $folder
                );
            foreach (array_filter(explode("\0", $listing)) as $file) {
                $paths[] = $folder . '/' . $file;
            }
        }
        return array_values(array_unique($paths));
    }

    /**
     * Run the command with bash and keep its standard output as staging file.
     *
     * @throws BackupException
     */
    private function commandOutput(Source $source, string $staging): string
    {
        $file = $staging . '/' . $source->name;
        $this->execute(command: ['bash', '-c', $source->command], label: $source->name, output: $file);
        if ($source->check === null) {
            return $file;
        }
        try {
            $this->execute(command: ['grep', '-Eq', '--', $source->check, $file], label: 'check');
        } catch (BackupException) {
            throw BackupException::checkFailed($source->name, $source->check);
        }
        return $file;
    }

    /**
     * Pack the remote path into a randomly named tar, download it and always remove it remotely.
     *
     * @throws BackupException
     */
    private function ftpshArchive(Source $source, string $staging): string
    {
        $file = $staging . '/' . $source->name;
        $remote = 'backuphelper_' . bin2hex(random_bytes(16)) . '.tar';
        $ftpsh = [$this->ftpsh, '--env', $source->env];
        try {
            $this->execute(
                command: [
                    ...$ftpsh,
                    'tar',
                    '-cf',
                    $remote,
                    '--exclude=' . $remote,
                    ...array_map(fn(string $pattern): string => '--exclude=' . $pattern, $source->exclude),
                    '--warning=no-file-changed',
                    $source->path
                ],
                label: 'ftpsh tar',
                allowed: [0, 1]
            );
            $this->execute(command: [...$ftpsh, '--download', $remote], label: 'ftpsh download', output: $file);
            $this->execute(command: ['tar', '-tf', $file], label: 'verification of ' . $source->name, output: '/dev/null');
        } finally {
            $this->execute(command: [...$ftpsh, 'rm', '-f', '--', $remote], label: 'ftpsh cleanup');
        }
        return $file;
    }

    /**
     * Write the tar.gz under a partial name first, so an interrupted run never counts as backup.
     *
     * @param list<string> $files
     * @param list<string> $exclude
     * @throws BackupException
     */
    private function archive(array $files, string $staging, array $exclude, string $archive): void
    {
        $list = $staging . '/.files';
        file_put_contents($list, implode("\0", array_map(fn(string $file): string => ltrim($file, '/'), $files)));
        // exit status 1 only reports files that changed while they were read (open sqlite databases)
        $this->execute(
            command: [
                'tar',
                '-czf',
                $archive . '.partial',
                '--ignore-failed-read',
                '--warning=no-file-changed',
                ...array_map(fn(string $pattern): string => '--exclude=' . $pattern, $exclude),
                '--transform=s|^' . ltrim($staging, '/') . '/||',
                '-C',
                '/',
                '--null',
                '-T',
                $list
            ],
            label: 'tar',
            allowed: [0, 1]
        );
        rename($archive . '.partial', $archive);
    }

    /**
     * Newest first; the date pattern keeps archives of jobs with a common name prefix apart.
     *
     * @return list<string>
     */
    private function archives(Job $job): array
    {
        $archives = array_values(
            array_filter(
                glob($job->target . '/' . $job->name . '-*.tar.gz') ?: [],
                fn(string $archive): bool => (bool) preg_match(
                    '/^' . preg_quote($job->name, '/') . '-\d{4}-\d{2}-\d{2}-\d{6}\.tar\.gz$/',
                    basename($archive)
                )
            )
        );
        rsort($archives);
        return $archives;
    }

    /**
     * Run a command without a shell; standard error goes straight to the caller's standard error.
     *
     * @param list<string> $command
     * @param list<int> $allowed
     * @throws BackupException
     */
    private function execute(array $command, string $label, ?string $output = null, array $allowed = [0]): string
    {
        $process = proc_open(
            $command,
            [0 => ['file', '/dev/null', 'r'], 1 => $output === null ? ['pipe', 'w'] : ['file', $output, 'w'], 2 => STDERR],
            $pipes
        );
        if ($process === false) {
            throw BackupException::commandFailed($label, -1);
        }
        $stdout = $output === null ? (string) stream_get_contents($pipes[1]) : '';
        $status = proc_close($process);
        if (!in_array($status, $allowed, true)) {
            throw BackupException::commandFailed($label, $status);
        }
        return $stdout;
    }

    /**
     * Timestamped line on standard output, which cron redirects into the log.
     */
    private function log(string $message): void
    {
        echo date('Y-m-d H:i:s') . ' ' . $message . PHP_EOL;
    }
}
