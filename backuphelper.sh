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

[[ -f "$CONFIG" ]] || fail "config $CONFIG not found"
SETTINGS=$(yq -c . "$CONFIG") || fail "config $CONFIG is no valid yaml"
FTPSH=$(jq -r '.ftpsh // "ftpsh"' <<< "$SETTINGS")
mapfile -t JOBS < <(jq -r '.jobs // {} | keys_unsorted[]' <<< "$SETTINGS")
(( ${#JOBS[@]} )) || fail "config $CONFIG has no jobs"
for job in "${SELECTED[@]}"; do
    contains "$job" "${JOBS[@]}" || fail "unknown job $job"
done

# job setting with fallback to the top level and a default
setting() {
    jq -r --arg job "$job" --arg key "$1" --arg default "$2" '.jobs[$job][$key] // .[$key] // $default' <<< "$SETTINGS"
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

collect_command() {
    local name command check
    name=$(field name)
    command=$(field command)
    check=$(field check)
    [[ -n "$name" && "$name" != */* && -n "$command" ]] || fail "$job: command source needs name and command"
    bash -c "$command" < /dev/null > "$staging/$name" || fail "$job: $name failed with exit status $?"
    if [[ -n "$check" ]] && ! grep -Eq -- "$check" "$staging/$name"; then fail "$job: $name does not match the check $check"; fi
    printf '%s\0' "$staging/$name" >> "$list"
}

# the remote tar gets a random name and is removed again, also when the job fails (see cleanup)
collect_ftpsh() {
    local name env path exclude status=0
    name=$(field name)
    env=$(field env)
    path=$(field path)
    [[ -n "$name" && "$name" != */* && -n "$env" && -n "$path" ]] || fail "$job: ftpsh source needs name, env and path"
    mapfile -t exclude < <(field exclude)
    remote_env=$env
    remote="backuphelper_$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n').tar"
    "$FTPSH" --env "$env" tar -cf "$remote" --exclude="$remote" "${exclude[@]/#/--exclude=}" --warning=no-file-changed "$path" \
        < /dev/null > /dev/null || status=$?
    (( status <= 1 )) || fail "$job: remote tar of $path failed with exit status $status"
    "$FTPSH" --env "$env" --download "$remote" < /dev/null > "$staging/$name" || fail "$job: download of $name failed"
    tar -tf "$staging/$name" > /dev/null || fail "$job: $name is no valid tar"
    "$FTPSH" --env "$env" rm -f -- "$remote" < /dev/null > /dev/null
    remote=
    printf '%s\0' "$staging/$name" >> "$list"
}

cleanup() {
    if [[ -n "$remote" ]]; then "$FTPSH" --env "$remote_env" rm -f -- "$remote" < /dev/null > /dev/null || true; fi
    rm -rf -- "$staging"
}

run_job() {
    local target keep interval newest source sources exclude archive status=0
    target=$(setting target "")
    keep=$(setting keep 7)
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
    remote=
    remote_env=
    trap cleanup EXIT
    : > "$list"
    mapfile -t sources < <(jq -c --arg job "$job" '.jobs[$job].sources // [] | .[]' <<< "$SETTINGS")
    (( ${#sources[@]} )) || fail "$job has no sources"
    for source in "${sources[@]}"; do
        case "$(field type)" in
            path)
                [[ "$(field path)" = /* ]] || fail "$job: path source needs an absolute path"
                printf '%s\0' "$(field path)" >> "$list"
                ;;
            git) collect_git ;;
            command) collect_command ;;
            ftpsh) collect_ftpsh ;;
            *) fail "$job: source type must be path, git, command or ftpsh" ;;
        esac
    done

    archive="$target/$job-$(date +%Y-%m-%d-%H%M%S).tar.gz"
    mapfile -t exclude < <(jq -r --arg job "$job" '(.exclude // []) + (.jobs[$job].exclude // []) | .[]' <<< "$SETTINGS")
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
