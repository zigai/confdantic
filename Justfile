@_:
   just --list

_require-uv:
   @uv --version > /dev/null || (echo "Please install uv: https://docs.astral.sh/uv/" && exit 1)

_require-hatch:
  @hatch --version > /dev/null || (echo "Please install hatch: uv tool install hatch" && exit 1)

# check code style and potential issues
lint:
   ruff check

# fix automatically fixable linting issues
fix:
   ruff check --fix

# format code
format:
   ruff format

# run tests across all supported Python versions
test: _require-hatch
   hatch run test:test

# run all quality checks
check: format lint test

# build the package
build: _require-uv
   uv build

# setup development environment
dev: _require-uv
   uv sync --extra dev
   uv run pre-commit install

# clean build artifacts and caches
clean:
   rm -rf .venv .pytest_cache .mypy_cache .ruff_cache
   find . -type d -name "__pycache__" -exec rm -r {} +
