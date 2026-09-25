[![GitHub Tag](https://img.shields.io/github/v/tag/vielhuber/backuphelper)](https://github.com/vielhuber/backuphelper/tags)
[![License](https://img.shields.io/github/license/vielhuber/backuphelper)](https://github.com/vielhuber/backuphelper/blob/main/LICENSE.md)
[![Last Commit](https://img.shields.io/github/last-commit/vielhuber/backuphelper)](https://github.com/vielhuber/backuphelper/commits)

# 💾 backuphelper 💾

backuphelper is a bash script that writes rotating `tar.gz` backups from one yaml config: local paths, the files of git checkouts that are not in git and the output of any bash command (database dumps, remote files).

## installation

```sh
git clone https://github.com/vielhuber/backuphelper.git
cd backuphelper
cp config.yaml.example config.yaml
```

requirements: `bash`, `yq` (jq wrapper), `jq`, `tar`, `gzip`, `git`, `flock`

## configuration

```yaml
# every top level key is a job that writes <target>/<job>-YYYY-MM-DD-HHMMSS.tar.gz
local:
    # folder of the archives; the job is skipped while it is missing (e.g. an unplugged usb drive)
    target: /mnt/h/backuphelper
    # number of archives to keep
    keep: 14
    # optional: minimum hours between two archives, so an hourly cron writes about one per day
    interval: 20
    # optional: tar exclude patterns
    exclude: [node_modules, vendor, .cache, syncdb/cache]
    sources:
        # every folder below path except skip; git checkouts only with untracked, ignored and modified files
        - type: git
          path: /var/www
          skip: [_environments, big-uploads]
        # a file or folder
        - type: path
          path: /var/lib/lamp
        # output of a bash command, stored as command-<position in this list> (here command-3)
        - type: command
          command: mysqldump --all-databases --single-transaction --routines --events --triggers

remote-host:
    target: /mnt/o/backups/remote-host
    keep: 7
    interval: 160
    sources:
        # the password is passed via stdin, so it neither shows up in the remote command line nor in a process list
        - type: command
          command: |
              printf '%s\n' 'secret' | ssh -o BatchMode=yes user@example.com 'IFS= read -r MYSQL_PWD; export MYSQL_PWD; mysqldump --single-transaction -h localhost -u user database'
        - type: command
          command: ssh -o BatchMode=yes user@example.com 'tar -C www -cf - uploads || [ $? -eq 1 ]'
```

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
