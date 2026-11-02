from __future__ import annotations

from collections.abc import Sequence
from typing import TypeAlias

from confdantic.errors import InvalidKeyPathError, quote

KeyPath: TypeAlias = tuple[str, ...]
"""The mapping keys from the document root to a value; list indexes are decimal strings."""


def parse_key(key: str) -> KeyPath:
    r"""Split a dotted key into its segments.

    Inside one segment, `\.` is a literal dot and `\\` a literal backslash; no other
    escape exists. `labels.` names the empty key under `labels`.
    """
    if not key:
        raise InvalidKeyPathError("Invalid empty key path")

    segments: list[str] = []
    token: list[str] = []
    index = 0
    while index < len(key):
        char = key[index]
        if char == ".":
            segments.append("".join(token))
            token.clear()
        elif char == "\\":
            index += 1
            if index == len(key):
                raise InvalidKeyPathError(
                    f"Invalid configuration key {quote(key)}: it ends in a backslash; "
                    "write \\\\ for a literal one"
                )

            escaped = key[index]
            if escaped not in ".\\":
                raise InvalidKeyPathError(
                    f"Invalid configuration key {quote(key)}: \\{escaped} is not an escape; "
                    "write \\. for a literal dot and \\\\ for a backslash"
                )

            token.append(escaped)
        else:
            token.append(char)

        index += 1

    segments.append("".join(token))

    return tuple(segments)


def to_key_path(key: str | Sequence[str]) -> KeyPath:
    """Accept a dotted key or a sequence of segments."""
    if isinstance(key, str):
        return parse_key(key)

    if not key or not all(isinstance(segment, str) for segment in key):
        raise InvalidKeyPathError(f"Invalid key path: {key!r}")

    return tuple(key)


__all__ = ["KeyPath", "parse_key", "to_key_path"]
