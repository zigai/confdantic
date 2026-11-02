from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum, auto

from confdantic.errors import ConfigError, UnknownKeyError, unknown_key_message
from confdantic.keypath import KeyPath, to_key_path

REDACTED = "[REDACTED]"


class SourceKind(StrEnum):
    """Configuration layers, in ascending precedence."""

    DEFAULT = auto()
    SYSTEM = auto()
    USER = auto()
    FILE = auto()
    STDIN = auto()
    ENV = auto()
    FLAG = auto()


@dataclass(frozen=True, slots=True)
class Origin:
    key: KeyPath
    source: SourceKind
    path: str | None
    """The file, environment variable, or flag that set the value."""
    line: int | None
    raw_value: str | None
    """The value as written; None when it was not recorded, as for unknown keys."""

    def location(self) -> str:
        """Short layer and location, such as `user /p/config.yaml:3`, `env APP_PORT`, `default`."""
        if self.line is not None:
            return f"{self.source} {self.path}:{self.line}"

        if self.path is not None:
            return f"{self.source} {self.path}"

        return str(self.source)

    def describe(self) -> str:
        """Long form of the layer for error text, such as `environment variable: APP_PORT`."""
        match self.source:
            case SourceKind.DEFAULT:
                return "defaults"
            case SourceKind.STDIN:
                return "standard input"
            case SourceKind.ENV:
                return f"environment variable: {self.path}"
            case SourceKind.FLAG:
                return f"CLI flag: {self.path}" if self.path is not None else "CLI flags"
            case _:
                path = self.path or ""
                return f"{path}:{self.line}" if self.line is not None else path


@dataclass(frozen=True, slots=True)
class UnknownKey:
    """A key a file or contribution set that the model does not declare; its value is not kept."""

    origin: Origin
    suggestion: KeyPath | None

    def __str__(self) -> str:
        return unknown_key_message(self.origin.key, self.suggestion)

    def error(self) -> UnknownKeyError:
        return UnknownKeyError(self.origin.key, suggestion=self.suggestion, origin=self.origin)


class Metadata(Mapping[KeyPath, Origin]):
    """Winning origins in model declaration order, with file and unknown-key reports."""

    def __init__(
        self,
        origins: Mapping[KeyPath, Origin] | None = None,
        active_files: Sequence[str] = (),
        unknown_keys: Sequence[UnknownKey] = (),
        keys: Sequence[KeyPath] = (),
    ) -> None:
        origins = origins or {}
        rank = {key: index for index, key in enumerate(keys)}
        unranked = len(rank)

        def order(key: KeyPath) -> int:
            # Keys the model does not declare (list items, extras) follow their nearest parent.
            ranked = next((key[:end] for end in range(len(key), 0, -1) if key[:end] in rank), None)
            return unranked if ranked is None else rank[ranked]

        self._origins = {key: origins[key] for key in sorted(origins, key=order)}
        self.active_files = tuple(dict.fromkeys(active_files))
        self.unknown_keys = tuple(unknown_keys)

    def where(self, key: str | Sequence[str]) -> Origin | None:
        """Look up the origin of a dotted key, such as `database.port`, or a key path."""
        return self._origins.get(to_key_path(key))

    def origins(self) -> tuple[Origin, ...]:
        return tuple(self._origins.values())

    def error(self, key: str | Sequence[str], message: str) -> ConfigError:
        """Build an error attributed to `key` and the layer that set it."""
        path = to_key_path(key)
        origin = self._origins.get(path)
        if origin is not None and origin.raw_value == REDACTED:
            message = "Invalid secret configuration value"

        return ConfigError(message, key=path, origin=origin)

    def __getitem__(self, key: KeyPath) -> Origin:
        return self._origins[key]

    def __iter__(self) -> Iterator[KeyPath]:
        return iter(self._origins)

    def __len__(self) -> int:
        return len(self._origins)

    def __repr__(self) -> str:
        return (
            f"Metadata(active_files={self.active_files!r}, origins={self._origins!r}, "
            f"unknown_keys={self.unknown_keys!r})"
        )


__all__ = ["REDACTED", "Metadata", "Origin", "SourceKind", "UnknownKey"]
