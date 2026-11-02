from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from confdantic.keypath import KeyPath
    from confdantic.provenance import Origin


class ConfdanticError(Exception):
    """Base exception for configuration operations."""


class MalformedDocumentError(ConfdanticError):
    """A configuration document cannot be parsed."""


class UnsupportedFormatError(ConfdanticError):
    """A format or extension is unknown, not selected, or lacks the needed capability."""


class NoFormatsError(ConfdanticError):
    """A file would be read, but no formats were selected."""


class FileTooLargeError(ConfdanticError):
    """A file or stream exceeds the permitted size."""


class InvalidKeyPathError(ConfdanticError):
    """A dotted key path is empty, malformed, or crosses a non-mapping value."""


class MultipleDocumentsError(ConfdanticError):
    """A multi-document stream was given where one document is required."""


class ConfigError(ConfdanticError):
    """A configuration failure, attributed to a key and the layer that supplied its value."""

    message: str
    key: KeyPath | None
    origin: Origin | None

    def __init__(
        self, message: str, *, key: KeyPath | None = None, origin: Origin | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.key = key
        self.origin = origin

    def __str__(self) -> str:
        text = f"config error: {self.message}"
        if self.key:
            text += f" for {format_key(self.key)}"

        if self.origin is not None:
            if self.origin.raw_value is not None:
                text += f": {quote(self.origin.raw_value)}"

            text += f"\n  --> set by {self.origin.describe()}"

        return text


class ConfigValidationError(ConfigError):
    """One or more configuration failures; `key` and `origin` are the first failure's."""

    errors: tuple[ConfigError, ...]

    def __init__(self, errors: Sequence[ConfigError]) -> None:
        if not errors:
            raise ValueError("ConfigValidationError requires at least one error")

        self.errors = tuple(errors)
        first = self.errors[0]
        super().__init__(first.message, key=first.key, origin=first.origin)

    def __str__(self) -> str:
        return "\n\n".join(str(error) for error in self.errors)


class UnknownKeyError(ConfigError):
    """A key the model does not declare, with the closest declared key when one is near."""

    suggestion: KeyPath | None

    def __init__(
        self, key: KeyPath, *, suggestion: KeyPath | None = None, origin: Origin | None = None
    ) -> None:
        super().__init__(unknown_key_message(key, suggestion), key=key, origin=origin)
        self.suggestion = suggestion

    def __str__(self) -> str:
        # The message already names the key, and an unknown key's value is never kept.
        text = f"config error: {self.message}"
        if self.origin is not None:
            text += f"\n  --> set by {self.origin.describe()}"

        return text


class InvalidEnvironmentValueError(ConfigError):
    """An environment variable's value does not fit its field."""

    variable_name: str

    def __init__(self, message: str, *, key: KeyPath, origin: Origin, variable_name: str) -> None:
        super().__init__(message, key=key, origin=origin)
        self.variable_name = variable_name


def unknown_key_message(key: KeyPath, suggestion: KeyPath | None) -> str:
    message = f"unknown key {quote(format_key(key))}"
    if suggestion is not None:
        message += f" (did you mean {quote(format_key(suggestion))}?)"

    return message


def format_key(key: KeyPath) -> str:
    """Join segments into a dotted key, escaping each dot and backslash inside one."""
    return ".".join(segment.replace("\\", "\\\\").replace(".", "\\.") for segment in key)


def quote(text: str) -> str:
    """Quote text for diagnostics, escaping quotes, backslashes, and control characters."""
    return json.dumps(text, ensure_ascii=False)


__all__ = [
    "ConfdanticError",
    "ConfigError",
    "ConfigValidationError",
    "FileTooLargeError",
    "InvalidEnvironmentValueError",
    "InvalidKeyPathError",
    "MalformedDocumentError",
    "MultipleDocumentsError",
    "NoFormatsError",
    "UnknownKeyError",
    "UnsupportedFormatError",
    "format_key",
    "quote",
    "unknown_key_message",
]
