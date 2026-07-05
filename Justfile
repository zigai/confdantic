set positional-arguments
set script-interpreter := ['uv', 'run', '--no-project', '--', 'python']
_:
    @just help

_require-uv:
  @uv --version > /dev/null || (echo "Please install uv: https://docs.astral.sh/uv/" && exit 1)

# Check code for lint issues
lint: _require-uv
  uv run --group dev ruff check .

# format code
format: _require-uv
  uv run --group dev ruff format .

# fix automatically fixable linting issues
fix: _require-uv
  uv run --group dev ruff check --fix .

# run tests across all supported Python versions
[script]
test *args: _require-uv
  from pathlib import Path
  import os
  import shutil
  import subprocess
  import sys

  versions = [
      line.strip()
      for line in Path(".python-versions").read_text(encoding="utf-8").splitlines()
      if line.strip() and not line.lstrip().startswith("#")
  ]
  args = sys.argv[1:]
  if args[:1] == ["--"]:
      args = args[1:]

  def colorize(text):
      if os.environ.get("NO_COLOR"):
          return text
      if os.environ.get("FORCE_COLOR") or (sys.stdout.isatty() and os.environ.get("TERM") != "dumb"):
          return f"\033[1;36m{text}\033[0m"
      return text

  def print_separator(label):
      width = shutil.get_terminal_size(fallback=(120, 24)).columns
      text = f" {label} "
      if len(text) >= width:
          print(colorize(label), flush=True)
          return

      left = (width - len(text)) // 2
      right = width - len(text) - left
      print(colorize(f"{'─' * left}{text}{'─' * right}"), flush=True)

  for py in versions:
      print_separator(f"Python {py}")
      result = subprocess.run([
          "uv",
          "run",
          "--python",
          py,
          "--isolated",
          "--group",
          "test",
          "--",
          "pytest",
          *args,
      ])
      if result.returncode:
          raise SystemExit(result.returncode)
# Build the project
build: _require-uv
  uv build

# setup or update local dev environment, keeps previously installed packages
sync: _require-uv
  uv sync --inexact --group dev
  uv run --group dev pre-commit install

# run tests with coverage and show a coverage report
coverage: _require-uv
  uv run coverage run -m pytest
  uv run coverage report

# clean build artifacts and caches
clean:
  rm -rf .venv .pytest_cache .mypy_cache .ruff_cache
  find . -type d -name "__pycache__" -exec rm -r {} +

# static type check with pyrefly
typecheck: _require-uv
  uv run --group dev pyrefly check --min-severity warn

# check code for common misspellings
spell: _require-uv
  uv run --group dev codespell

# run all quality checks
check: lint coverage typecheck spell
  uv run --group dev ruff format --check .

# list available recipes
help:
  @just --list

alias fmt := format
alias cov := coverage
alias mypy := typecheck

alias dev := sync
alias pyrefly := typecheck
