#!/usr/bin/env bash
set -euo pipefail
shopt -s nullglob dotglob inherit_errexit

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
CONFIG="$SCRIPT_DIR/config.yaml"
SELECTED=()
while (( $# )); do
    case "$1" in
        --config)
            CONFIG=${2:?--config needs a file}
            shift 2
            ;;
        -*)
            echo "usage: backuphelper.sh [--config <file>] [job ...]" >&2
            exit 2
            ;;
        *)
            SELECTED+=("$1")
            shift
            ;;
    esac
done
# archives contain secrets such as keys and database dumps
umask 077

log() {
    printf '%s %s\n' "$(date '+%F %T')" "$*"
}

fail() {
    log "❌ $*"
    exit 1
}

contains() {
    local item
    for item in "${@:2}"; do
        if [[ "$item" = "$1" ]]; then return 0; fi
    done
    return 1
}

for tool in yq jq tar gzip git flock; do
    command -v "$tool" > /dev/null || fail "$tool is missing"
done
[[ -f "$CONFIG" ]] || fail "config $CONFIG not found"
SETTINGS=$(yq -c . "$CONFIG") || fail "config $CONFIG is no valid yaml"
mapfile -t JOBS < <(jq -r '. // {} | keys_unsorted[]' <<< "$SETTINGS")
(( ${#JOBS[@]} )) || fail "config $CONFIG has no jobs"
for job in "${SELECTED[@]}"; do
    contains "$job" "${JOBS[@]}" || fail "unknown job $job"
done

setting() {
    jq -r --arg job "$job" --arg key "$1" --arg default "$2" '.[$job][$key] // $default' <<< "$SETTINGS"
}

# field of the current source; lists are printed one entry per line
field() {
    jq -r --arg key "$1" '.[$key] // empty | if type == "array" then .[] else . end' <<< "$source"
}

# archives of the current job, newest first; the date pattern keeps jobs with a common name prefix apart
archives() {
    local file
    for file in "$target/$job"-*.tar.gz; do
        if [[ "${file##*/}" =~ ^$job-[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{6}\.tar\.gz$ ]]; then printf '%s\n' "$file"; fi
    done | sort -r
}

# git checkouts below the path contribute untracked, ignored and modified files, other folders everything
collect_git() {
    local path folder skip
    path=$(field path)
    [[ "$path" = /* ]] || fail "$job: git source needs an absolute path"
    mapfile -t skip < <(field skip)
    for folder in "$path"/*/; do
        folder=${folder%/}
        if contains "${folder##*/}" "${skip[@]}"; then continue; fi
        if [[ ! -e "$folder/.git" ]]; then
            printf '%s\0' "$folder" >> "$list"
            continue
        fi
        {
            git -C "$folder" ls-files -z --others --modified --exclude-standard
            git -C "$folder" ls-files -z --others --ignored --exclude-standard --directory
        } < /dev/null | while IFS= read -r -d '' file; do printf '%s\0' "$folder/$file"; done >> "$list"
    done
}

# the output is named after the position of the source, so no part of the command (e.g. a password) ends up in a file name
collect_command() {
    local name="command-$1" command
    command=$(field command)
    [[ -n "$command" ]] || fail "$job: command source $1 needs a command"
    bash -c "$command" < /dev/null > "$staging/$name" || fail "$job: $name failed with exit status $?"
    printf '%s\0' "$staging/$name" >> "$list"
}

cleanup() {
    rm -rf -- "$staging"
}

run_job() {
    local target keep interval newest index source sources exclude archive status=0
    target=$(setting target "")
    keep=$(setting keep "")
    interval=$(setting interval 0)
    [[ "$job" =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]] || fail "job names may only contain letters, digits, _ and -"
    [[ "$target" = /* ]] || fail "$job needs an absolute target"
    [[ "$keep" =~ ^[1-9][0-9]*$ && "$interval" =~ ^[0-9]+$ ]] || fail "$job needs keep >= 1 and interval >= 0 (hours)"
    if [[ ! -d "$target" ]]; then
        log "⚠️ $job: target $target is missing; skipped"
        return 0
    fi
    exec 9> "/tmp/backuphelper-$(printf '%s' "$target/$job" | md5sum | cut -c1-32).lock"
    flock -n 9 || return 0
    rm -f -- "$target/$job"-*.partial
    newest=$(archives | head -n 1)
    if [[ -n "$newest" ]] && (( $(date +%s) - $(stat -c %Y "$newest") < interval * 3600 )); then return 0; fi

    log "$job: started"
    staging=$(mktemp -d /tmp/backuphelper.XXXXXX)
    list="$staging/.files"
    trap cleanup EXIT
    : > "$list"
    mapfile -t sources < <(jq -c --arg job "$job" '.[$job].sources // [] | .[]' <<< "$SETTINGS")
    (( ${#sources[@]} )) || fail "$job has no sources"
    for index in "${!sources[@]}"; do
        source=${sources[$index]}
        case "$(field type)" in
            path)
                [[ "$(field path)" = /* ]] || fail "$job: path source needs an absolute path"
                printf '%s\0' "$(field path)" >> "$list"
                ;;
            git) collect_git ;;
            command) collect_command "$((index + 1))" ;;
            *) fail "$job: source type must be path, git or command" ;;
        esac
    done

    archive="$target/$job-$(date +%Y-%m-%d-%H%M%S).tar.gz"
    mapfile -t exclude < <(jq -r --arg job "$job" '.[$job].exclude // [] | .[]' <<< "$SETTINGS")
    # exit status 1 only reports files that changed while they were read (open sqlite databases)
    tar -czf "$archive.partial" --ignore-failed-read --warning=no-file-changed "${exclude[@]/#/--exclude=}" \
        --transform "s|^${staging#/}/||" -C / --null -T <(sed -z 's|^/||' "$list") || status=$?
    (( status <= 1 )) || fail "$job: tar failed with exit status $status"
    mv -- "$archive.partial" "$archive"
    archives | tail -n +$((keep + 1)) | while IFS= read -r outdated; do rm -f -- "$outdated"; done
    log "✅ $job: $archive ($(du -h "$archive" | cut -f1))"
}

# every job runs in its own subshell, so a failing job neither stops the others nor leaks its state
failed=0
for job in "${JOBS[@]}"; do
    if (( ${#SELECTED[@]} )) && ! contains "$job" "${SELECTED[@]}"; then continue; fi
    set +e
    (
        set -eE
        trap 'log "❌ $job: \"$BASH_COMMAND\" failed in line $LINENO"' ERR
        run_job
    )
    (( $? == 0 )) || failed=1
    set -e
done
exit "$failed"
