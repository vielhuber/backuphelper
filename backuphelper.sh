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

# live status that overwrites itself; only in a terminal, so cron logs stay clean
progress() {
    if [[ -t 2 ]]; then printf '\r\033[K%s' "${1:+   $1}" >&2; fi
}

contains() {
    local item
    for item in "${@:2}"; do
        if [[ "$item" = "$1" ]]; then return 0; fi
    done
    return 1
}

for tool in yq jq tar gzip git flock pv; do
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

# tar drops these anyway; leaving them out of the list keeps the size estimate honest
excluded() {
    local pattern name=${1%/}
    name=${name##*/}
    for pattern in "${exclude[@]}"; do
        # unquoted, so the pattern matches like a tar exclude glob
        if [[ "$name" == $pattern ]]; then return 0; fi
    done
    return 1
}

# git checkouts below the path contribute untracked, ignored and modified files, other folders everything
collect_git() {
    local path folder skip folders position=0
    path=$(field path)
    [[ "$path" = /* ]] || fail "$job: git source needs an absolute path"
    mapfile -t skip < <(field skip)
    folders=("$path"/*/)
    log "📂 $job $1: git $path (${#folders[@]} folders)"
    for folder in "${folders[@]}"; do
        folder=${folder%/}
        position=$((position + 1))
        progress "$position/${#folders[@]} ${folder##*/}"
        if contains "${folder##*/}" "${skip[@]}"; then continue; fi
        if [[ ! -e "$folder/.git" ]]; then
            printf '%s\0' "$folder" >> "$list"
            continue
        fi
        {
            git -C "$folder" ls-files -z --others --modified --exclude-standard
            git -C "$folder" ls-files -z --others --ignored --exclude-standard --directory
        } < /dev/null | while IFS= read -r -d '' file; do
            # --modified also lists deleted files
            if [[ -e "$folder/$file" || -L "$folder/$file" ]] && ! excluded "$file"; then printf '%s\0' "$folder/$file"; fi
        done >> "$list"
    done
    progress
}

# the output is named after the position of the source, so no part of the command (e.g. a password) ends up in a file name
collect_command() {
    local name="command-$1" command
    command=$(field command)
    [[ -n "$command" ]] || fail "$job: command source $1 needs a command"
    log "⚙️ $job $2: $name"
    bash -c "$command" < /dev/null | pv -N "$name" > "$staging/$name" || fail "$job: $name failed with exit status $?"
    printf '%s\0' "$staging/$name" >> "$list"
}

cleanup() {
    rm -rf -- "$staging"
}

run_job() {
    local target keep interval newest index step source sources exclude archive size outdated status=0
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
    if ! flock -n 9; then
        if [[ -t 1 ]]; then log "🔒 $job: already running; skipped"; fi
        return 0
    fi
    rm -f -- "$target/$job"-*.partial
    newest=$(archives | head -n 1)
    if [[ -n "$newest" ]] && (( $(date +%s) - $(stat -c %Y "$newest") < interval * 3600 )); then
        if [[ -t 1 ]]; then log "⏭️ $job: last archive is younger than $interval hours; skipped"; fi
        return 0
    fi

    log "🚀 $job: started"
    staging=$(mktemp -d /tmp/backuphelper.XXXXXX)
    list="$staging/.files"
    trap cleanup EXIT
    : > "$list"
    mapfile -t exclude < <(jq -r --arg job "$job" '.[$job].exclude // [] | .[]' <<< "$SETTINGS")
    mapfile -t sources < <(jq -c --arg job "$job" '.[$job].sources // [] | .[]' <<< "$SETTINGS")
    (( ${#sources[@]} )) || fail "$job has no sources"
    for index in "${!sources[@]}"; do
        source=${sources[$index]}
        step="[$((index + 1))/${#sources[@]}]"
        case "$(field type)" in
            path)
                [[ "$(field path)" = /* ]] || fail "$job: path source needs an absolute path"
                log "📄 $job $step: path $(field path)"
                printf '%s\0' "$(field path)" >> "$list"
                ;;
            git) collect_git "$step" ;;
            command) collect_command "$((index + 1))" "$step" ;;
            *) fail "$job: source type must be path, git or command" ;;
        esac
    done

    archive="$target/$job-$(date +%Y-%m-%d-%H%M%S).tar.gz"
    # only drives the progress estimate; du applies name patterns like tar, path patterns are ignored
    size=$({ du -cb "${exclude[@]/#/--exclude=}" --files0-from="$list" 2> /dev/null || true; } | tail -n 1 | cut -f1)
    log "📦 $job: packing about $(numfmt --to=iec-i --suffix=B "${size:-0}")"
    # exit status 1 only reports files that changed while they were read (open sqlite databases)
    {
        tar -cf - --ignore-failed-read --warning=no-file-changed "${exclude[@]/#/--exclude=}" \
            --transform "s|^${staging#/}/||" -C / --null -T <(sed -z 's|^/||' "$list") \
            || echo "$?" > "$staging/.tar-status"
    } | pv -s "${size:-0}" | gzip > "$archive.partial"
    if [[ -f "$staging/.tar-status" ]]; then status=$(< "$staging/.tar-status"); fi
    (( status <= 1 )) || fail "$job: tar failed with exit status $status"
    mv -- "$archive.partial" "$archive"
    mapfile -t outdated < <(archives | tail -n +$((keep + 1)))
    if (( ${#outdated[@]} )); then
        rm -f -- "${outdated[@]}"
        log "🧹 $job: removed ${#outdated[@]} old archive(s)"
    fi
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
