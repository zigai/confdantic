@_:
  just --list

_require-uv:
  @uv --version > /dev/null || (echo "Please install uv: https://docs.astral.sh/uv/" && exit 1)

_require-hatch:
  @hatch --version > /dev/null || (echo "Please install hatch: uv tool install hatch" && exit 1)

# check code style and potential issues
lint:
  ruff check

# format code
format:
  ruff format

# fix automatically fixable linting issues
fix:
  ruff check --fix

# run tests across all supported Python versions
test: _require-hatch
  hatch run test:test

# build the package
build: _require-uv
  uv build

# setup or update local dev environment, keeps previously installed extras
dev: _require-uv
  uv sync --inexact --extra dev
  uv run pre-commit install

# run tests with coverage and show a coverage report
coverage:
  coverage run -m pytest
  coverage report

# clean build artifacts and caches
clean:
  rm -rf .venv .pytest_cache .mypy_cache .ruff_cache
  find . -type d -name "__pycache__" -exec rm -r {} +

# static type check with mypy
typecheck: _require-uv
    uv run mypy

# check code for common misspellings
spell:
    codespell

# run all quality checks
check: format lint coverage typecheck spell

# list available recipes
help:
    just --list

alias fmt := format
alias cov := coverage
alias mypy := typecheck
