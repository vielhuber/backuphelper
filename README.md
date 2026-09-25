[![build status](https://github.com/vielhuber/backuphelper/actions/workflows/ci.yml/badge.svg)](https://github.com/vielhuber/backuphelper/actions)
[![GitHub Tag](https://img.shields.io/github/v/tag/vielhuber/backuphelper)](https://github.com/vielhuber/backuphelper/tags)
[![Code Style](https://img.shields.io/badge/code_style-psr--12-ff69b4.svg)](https://www.php-fig.org/psr/psr-12/)
[![License](https://img.shields.io/github/license/vielhuber/backuphelper)](https://github.com/vielhuber/backuphelper/blob/main/LICENSE.md)
[![Last Commit](https://img.shields.io/github/last-commit/vielhuber/backuphelper)](https://github.com/vielhuber/backuphelper/commits)
[![PHP Version Support](https://img.shields.io/packagist/php-v/vielhuber/backuphelper)](https://packagist.org/packages/vielhuber/backuphelper)
[![Packagist Downloads](https://img.shields.io/packagist/dt/vielhuber/backuphelper)](https://packagist.org/packages/vielhuber/backuphelper)

# 💾 backuphelper 💾

backuphelper writes rotating `tar.gz` backups from one yaml config. every job collects its sources into a single archive `<job>-YYYY-MM-DD-HHMMSS.tar.gz` in its target folder and keeps only the newest ones. sources are local paths, the files of git checkouts that are not in git, the output of any command (database dumps, remote files via ssh) and remote folders of hosts that only offer ftp/sftp via [ftpsh](https://github.com/vielhuber/ftpsh).

## installation

```bash
composer require vielhuber/backuphelper
```

requirements: php, `tar`, `gzip`, `git`, `bash` and, for ftpsh sources, [ftpsh](https://github.com/vielhuber/ftpsh).

## configuration

copy `config.yaml.example` to `config.yaml` (ignored by git, keep it private since it may contain passwords):

```yaml
target: /mnt/h/backuphelper
keep: 14
interval: 20
ftpsh: /var/www/ftpsh/ftpsh.sh
exclude: [node_modules, vendor, .cache]

jobs:
    local:
        sources:
            - type: git
              path: /var/www
              skip: [big-uploads]
            - type: path
              path: /var/lib/lamp
            - type: command
              name: mysql.sql
              command: mysqldump --all-databases --single-transaction
              check: '^-- Dump completed on '

    ftp-only-host:
        target: /mnt/o/backups/ftp-only-host
        keep: 2
        interval: 160
        sources:
            - type: ftpsh
              name: files.tar
              env: ftp-only-host.env
              path: .
              exclude: [.git, cache]
```

**settings** (top level as default, overridable per job):

- `target`: folder of the archives; it must exist, otherwise the job is skipped (e.g. an unplugged usb drive)
- `keep`: number of archives kept per job (default `7`)
- `interval`: minimum hours between two archives of a job (default `0`); lets an hourly cron write roughly one archive per day even when the machine is off at a fixed time
- `exclude`: tar exclude patterns; top level and job patterns both apply
- `ftpsh`: ftpsh binary (default `ftpsh` from the `PATH`)

**sources:**

- `path`: an absolute file or folder
- `git`: every folder directly below `path` (except `skip`); git checkouts contribute only untracked, ignored and modified files (`.env`, `.data`, uploads, uncommitted work), other folders are taken completely
- `command`: runs `command` with bash and stores its output as `name`; `check` is an extended regex the output must contain, otherwise the job fails
- `ftpsh`: packs `path` on the remote host into a randomly named tar (without `exclude`), downloads it as `name`, verifies it and always removes the remote file; `env` is passed to `ftpsh --env`

a failing source aborts its job before any existing archive is touched; the other jobs still run.

## usage

```bash
vendor/bin/backuphelper config.yaml
vendor/bin/backuphelper config.yaml local ftp-only-host
```

the command exits with `0` when all jobs succeeded or were skipped and with `1` otherwise.

```php
use vielhuber\backuphelper\backuphelper;
(new backuphelper())->config('config.yaml')->run();
```

## cron

```bash
0 * * * * /path/to/vendor/bin/backuphelper /path/to/config.yaml >> /var/log/backuphelper.log 2>&1
```

## restore

```bash
tar -xzf local-2026-01-01-030000.tar.gz -C /restore
```

paths are stored without the leading `/`; command and ftpsh outputs are stored at the archive root.

## tests

```bash
vendor/bin/phpunit
```
