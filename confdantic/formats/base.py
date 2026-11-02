from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from confdantic.formats.jsonc import CommentPosition
    from confdantic.plan import ModelPlan
    from confdantic.values import ConfigValue


@dataclass(frozen=True, slots=True)
class ValueLocation:
    line: int | None
    raw_value: str


@dataclass(frozen=True, slots=True)
class RenderOptions:
    comments: bool = False
    serialize_unsupported: bool = False
    comment_position: CommentPosition = "end_of_line"
    indent: int = 4


@runtime_checkable
class Format(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def extensions(self) -> tuple[str, ...]: ...

    def decode(self, data: bytes) -> Mapping[str, Any]: ...


@runtime_checkable
class SupportsEncode(Protocol):
    def encode(
        self,
        document: Mapping[str, Any],
        *,
        plan: ModelPlan | None,
        options: RenderOptions,
    ) -> bytes: ...


@runtime_checkable
class SupportsLocate(Protocol):
    def locate(self, decoded: Mapping[str, Any], path: Sequence[str]) -> ValueLocation | None: ...


@runtime_checkable
class SupportsEdit(Protocol):
    def edit(self, data: bytes, path: Sequence[str], value: ConfigValue) -> bytes: ...


@runtime_checkable
class EditableFormat(Format, SupportsEdit, Protocol):
    """A format that can rewrite one key while keeping the rest of the document."""


__all__ = [
    "EditableFormat",
    "Format",
    "RenderOptions",
    "SupportsEdit",
    "SupportsEncode",
    "SupportsLocate",
    "ValueLocation",
]
