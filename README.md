[![GitHub Tag](https://img.shields.io/github/v/tag/vielhuber/backuphelper)](https://github.com/vielhuber/backuphelper/tags)
[![License](https://img.shields.io/github/license/vielhuber/backuphelper)](https://github.com/vielhuber/backuphelper/blob/main/LICENSE.md)
[![Last Commit](https://img.shields.io/github/last-commit/vielhuber/backuphelper)](https://github.com/vielhuber/backuphelper/commits)

# 💾 backuphelper 💾

backuphelper is a bash script that writes rotating `tar.gz` backups from one yaml config: local paths, the files of git checkouts that are not in git and the output of any bash command (database dumps, remote files).

## installation / update

```sh
git clone https://github.com/vielhuber/backuphelper.git
cd backuphelper
cp config.yaml.example config.yaml
./backuphelper.sh
```

requirements: `bash`, `yq` (jq wrapper), `jq`, `tar`, `gzip`, `git`, `flock`

## configuration

```yaml
target: /mnt/h/backuphelper
keep: 14
interval: 20
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

    remote-host:
        target: /mnt/o/backups/remote-host
        keep: 7
        interval: 160
        sources:
            - type: command
              name: uploads.tar
              command: ssh user@example.com 'tar -C www -cf - uploads'
```

**settings** (top level as default, overridable per job):

- `target`: folder of the archives `<job>-YYYY-MM-DD-HHMMSS.tar.gz`; a missing folder skips the job
- `keep`: archives kept per job (default `7`)
- `interval`: minimum hours between two archives of a job (default `0`)
- `exclude`: tar exclude patterns (top level and job)

**sources:**

- `path`: absolute file or folder
- `git`: every folder below `path` except `skip`; git checkouts only with untracked, ignored and modified files
- `command`: output of a bash command, stored as `name`; `check` is an extended regex the output must match

## usage

```sh
./backuphelper.sh
./backuphelper.sh local remote-host
./backuphelper.sh --config /path/to/other.yaml
```

## cron

```sh
0 * * * * /path/to/backuphelper/backuphelper.sh >> /var/log/backuphelper.log 2>&1
```
