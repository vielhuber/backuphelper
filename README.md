[![GitHub Tag](https://img.shields.io/github/v/tag/vielhuber/backuphelper)](https://github.com/vielhuber/backuphelper/tags)
[![License](https://img.shields.io/github/license/vielhuber/backuphelper)](https://github.com/vielhuber/backuphelper/blob/main/LICENSE.md)
[![Last Commit](https://img.shields.io/github/last-commit/vielhuber/backuphelper)](https://github.com/vielhuber/backuphelper/commits)

# 💾 backuphelper 💾

backuphelper is a bash script that writes rotating `tar.gz` backups from one yaml config. every job collects its sources into a single archive `<job>-YYYY-MM-DD-HHMMSS.tar.gz` in its target folder and keeps only the newest ones. sources are local paths, the files of git checkouts that are not in git, the output of any command (database dumps, remote files via ssh) and remote folders of hosts that only offer ftp/sftp via [ftpsh](https://github.com/vielhuber/ftpsh).

## installation / update

```sh
git clone https://github.com/vielhuber/backuphelper.git
cd backuphelper
cp config.yaml.example config.yaml
nano config.yaml
./backuphelper.sh
```

update with `git pull`. to use `backuphelper` from anywhere, create a symlink in your path:

```sh
sudo ln -s $(pwd)/backuphelper.sh /usr/local/bin/backuphelper
```

**requirements:** `bash`, `yq` (the jq wrapper, `apt install yq`), `jq`, `tar`, `gzip`, `git`, `flock` and, for ftpsh sources, [ftpsh](https://github.com/vielhuber/ftpsh).

## configuration

`config.yaml` is ignored by git; keep it private since it may contain passwords.

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
- `ftpsh`: ftpsh binary (top level only, default `ftpsh` from the `PATH`)

**sources:**

- `path`: an absolute file or folder
- `git`: every folder directly below `path` (except `skip`); git checkouts contribute only untracked, ignored and modified files (`.env`, `.data`, uploads, uncommitted work), other folders are taken completely
- `command`: runs `command` with bash and stores its output as `name`; `check` is an extended regex the output must contain, otherwise the job fails
- `ftpsh`: packs `path` on the remote host into a randomly named tar (without `exclude`), downloads it as `name`, verifies it and always removes the remote file; `env` is passed to `ftpsh --env`

a failing source aborts its job before any existing archive is touched; the other jobs still run.

## usage

```sh
./backuphelper.sh
./backuphelper.sh local ftp-only-host
./backuphelper.sh --config /path/to/other.yaml
```

the script exits with `0` when all jobs succeeded or were skipped and with `1` otherwise.

## cron

```sh
0 * * * * /path/to/backuphelper/backuphelper.sh >> /var/log/backuphelper.log 2>&1
```

## restore

```sh
tar -xzf local-2026-01-01-030000.tar.gz -C /restore
```

paths are stored without the leading `/`; command and ftpsh outputs are stored at the archive root.
