set positional-arguments
set script-interpreter := ['uv', 'run', '--no-project', '--', 'python']

# Run every configured linter on the whole project, stopping on failure
lint: require-uv
    @uv run --group dev ruff check .

# Format every supported first-party file type
format:
    @uv run --group dev ruff format .
    @oxfmt '!**/*.md'
    @git ls-files -co --exclude-standard -z | while IFS= read -r -d '' file; do if [[ -f $file ]]; then printf '%s\0' "$file"; fi; done | xargs -0 -r shfmt -f | xargs -r -d '\n' shfmt -w
    @git ls-files -co --exclude-standard -- Justfile justfile "*.just" | while IFS= read -r file; do just --justfile "$file" --fmt; done
    @just markdown fmt

# Apply safe lint autofixes, then format every supported file type
fix: require-uv && format
    uv run --group dev ruff check --fix .

# Run tests on every supported Python version
[positional-arguments]
test *args: require-uv
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ "${1:-}" == "--" ]]; then shift; fi
    separator() {
        local text=" $1 " width=120 left right fill
        if [[ -n "${COLUMNS:-}" ]]; then width=$COLUMNS; elif [[ -t 1 ]]; then width=$(tput cols); fi
        if (( ${#text} >= width )); then text=$1; else
            left=$(( (width - ${#text}) / 2 )); right=$(( width - ${#text} - left ))
            printf -v fill '%*s' "$left" ''; text="${fill// /─}$text"
            printf -v fill '%*s' "$right" ''; text="$text${fill// /─}"
        fi
        if [[ -z "${NO_COLOR:-}" ]] && { [[ -n "${FORCE_COLOR:-}" ]] || { [[ -t 1 ]] && [[ "${TERM:-}" != dumb ]]; }; }; then
            printf '\033[1;36m%s\033[0m\n' "$text"
        else
            printf '%s\n' "$text"
        fi
    }
    while read -r version; do
        [[ -z "$version" || "$version" == \#* ]] && continue
        separator "Python $version"
        uv run --python "$version" --isolated --group dev -- pytest "$@" || [[ $? -eq 5 ]]
    done < .python-versions

# Build the project
build: require-uv
    uv build

# Install repository dependencies
setup: require-uv
    @uv sync --inexact --group dev

# Measure all local test suites and print coverage
coverage: require-uv
    #!/usr/bin/env bash
    set -euo pipefail
    export COVERAGE_FILE="$PWD/.coverage"
    uv run --group dev coverage erase
    uv run --group dev coverage run -m pytest || [[ $? -eq 5 ]]
    uv run --group dev coverage combine
    uv run --group dev coverage report

# Remove generated output, caches, and dependencies
clean: (remove ".venv" "dist" "build" ".pytest_cache" ".pyrefly" ".ruff_cache" ".coverage" "htmlcov" ".hypothesis" "**/__pycache__" ".coverage.*")

# Static type check with pyrefly
typecheck: require-uv
    uv run --group dev pyrefly check

# Run the commit checks
check: (run-checks "formatcheck" "lockcheck" "lint" "typecheck" "test")

# Check the same files and tools without writing
formatcheck:
    @uv run --group dev ruff format --check .
    @oxfmt --check '!**/*.md'
    @git ls-files -co --exclude-standard -z | while IFS= read -r -d '' file; do if [[ -f $file ]]; then printf '%s\0' "$file"; fi; done | xargs -0 -r shfmt -f | xargs -r -d '\n' shfmt -d
    @git ls-files -co --exclude-standard -- Justfile justfile "*.just" | while IFS= read -r file; do just --justfile "$file" --fmt --check; done
    @just markdown check

# Synchronize dependency locks without upgrading dependencies
[no-exit-message]
lock:
    @uv lock

# Verify dependency manifests and locks without changing files
[no-exit-message]
lockcheck:
    @uv lock --check

# List available recipes
[default]
help:
    @just --list

alias fmt := format
alias cov := coverage

[private]
require-uv:
    @uv --version > /dev/null || (echo "Please install uv: https://docs.astral.sh/uv/" && exit 1)

[positional-arguments]
[private]
remove +paths:
    #!/usr/bin/env bash
    set -euo pipefail
    shopt -s nullglob globstar
    size() { LC_ALL=C numfmt --from-unit=1024 --to=iec-i --suffix=B --format=%.1f "$1"; }
    rows=() total=0
    for pattern in "$@"; do
        found=()
        for path in $pattern; do
            if [[ -e $path || -L $path ]]; then found+=("$path"); fi
        done
        if (( ${#found[@]} == 0 )); then continue; fi
        kib=$(du -sck -- "${found[@]}" | tail -n1 | cut -f1)
        rm -rf -- "${found[@]}"
        label=$pattern
        if (( ${#found[@]} > 1 )); then label+=" (${#found[@]})"; fi
        rows+=("removed  $label|$(size "$kib")")
        total=$(( total + kib ))
    done
    if [[ -n ${CLEAN_TOTAL_FILE:-} ]]; then echo "$total" >> "$CLEAN_TOTAL_FILE"; fi
    if (( ${#rows[@]} == 0 )); then echo "nothing to clean"; exit 0; fi
    printf '%s\n' "${rows[@]}" "total freed|$(size "$total")" | column -t -s '|' -R 2

[positional-arguments]
[private]
run-checks +steps:
    #!/usr/bin/env bash
    set -uo pipefail
    LC_NUMERIC=C
    log_dir="${XDG_CACHE_HOME:-$HOME/.cache}/just-check"
    mkdir -p "$log_dir"
    log=$(mktemp "$log_dir/step.XXXXXX")
    trap 'rm -f "$log"' EXIT
    width=0 failed=()
    for step in "$@"; do (( ${#step} > width )) && width=${#step}; done
    for step in "$@"; do
        read -ra command <<< "$step"
        printf 'check  %-*s  ' "$width" "$step"
        start=$EPOCHREALTIME
        run=(just --justfile {{ quote(justfile()) }} "${command[@]}")
        if [[ -t 1 ]]; then run=(script -qefc "${run[*]@Q}" /dev/null); fi
        if "${run[@]}" < /dev/null > "$log" 2>&1; then status=ok; else status=FAIL; failed+=("$step"); fi
        printf '%-4s  %7.1fs\n' "$status" "$(LC_NUMERIC=C awk -v a="$start" -v b="$EPOCHREALTIME" 'BEGIN { print b - a }')"
        if [[ "$status" == FAIL ]]; then sed 's/\r$//; s/^/    /' "$log"; fi
    done
    if (( ${#failed[@]} )); then printf '%d of %d failed: %s\n' "${#failed[@]}" "$#" "${failed[*]}"; exit 1; fi
    printf 'all %d passed\n' "$#"

[private]
markdown *args:
    rumdl {{ args }} --exclude '.git/**' --disable MD001,MD013,MD024,MD025,MD026,MD031,MD032,MD033,MD034,MD036,MD041,MD058 --config 'MD060.enabled=true' --config 'MD060.style="aligned"' --config 'MD003.style="atx"' --config 'MD004.style="dash"' --config 'MD029.style="ordered"' --config 'MD046.style="fenced"' --config 'MD048.style="backtick"' --config 'MD049.style="asterisk"' --config 'MD050.style="asterisk"' --config 'MD055.style="leading_and_trailing"'
