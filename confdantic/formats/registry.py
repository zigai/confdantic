from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from confdantic.errors import ConfdanticError, UnsupportedFormatError
from confdantic.formats.base import Format
from confdantic.formats.json import JSON
from confdantic.formats.jsonc import JSONC
from confdantic.formats.toml import TOML
from confdantic.formats.yaml import YAML


@dataclass(frozen=True, slots=True)
class Selected:
    format: Format
    extension: str


BUILTIN_FORMATS: tuple[Format, ...] = (TOML(), YAML(), JSON(), JSONC())


def select_formats(selection: Sequence[str | Format]) -> tuple[Selected, ...]:
    selected: list[Selected] = []
    extensions: set[str] = set()
    for choice in selection:
        if isinstance(choice, str):
            choice = choice.strip().lower()
            matches = (
                [(fmt, choice) for fmt in BUILTIN_FORMATS if choice in fmt.extensions]
                if choice.startswith(".")
                else [
                    (fmt, ext)
                    for fmt in BUILTIN_FORMATS
                    if fmt.name == choice
                    for ext in fmt.extensions
                ]
            )
            if not matches:
                raise UnsupportedFormatError(f"Unsupported format or extension: '{choice}'")
        elif isinstance(choice, Format):
            matches = [(choice, ext) for ext in choice.extensions]
        else:
            raise UnsupportedFormatError(f"Unsupported format: {choice!r}")

        for fmt, ext in matches:
            if not ext.startswith(".") or ext == ".":
                raise ConfdanticError(f"Invalid format extension: '{ext}'")

            if ext in extensions:
                raise ConfdanticError(f"Format extension '{ext}' is already selected")

            extensions.add(ext)
            selected.append(Selected(fmt, ext))

    return tuple(selected)


def format_for_path(path: str | Path, selected: Sequence[Selected]) -> Format:
    suffix = Path(path).suffix.lower()
    for item in selected:
        if item.extension == suffix:
            return item.format

    raise UnsupportedFormatError(f"Unsupported format or extension: '{suffix or path}'")


def discovery_extensions(selected: Sequence[Selected]) -> tuple[str, ...]:
    return tuple(item.extension for item in selected)


__all__ = [
    "BUILTIN_FORMATS",
    "Selected",
    "discovery_extensions",
    "format_for_path",
    "select_formats",
]
