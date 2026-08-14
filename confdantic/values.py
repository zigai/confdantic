from __future__ import annotations

from pathlib import PurePath
from typing import Any, TypeAlias

from pydantic_core import to_jsonable_python

ConfigScalar: TypeAlias = "bool | int | float | str | PurePath | None"
ConfigValue: TypeAlias = "ConfigScalar | list[ConfigValue] | dict[str, ConfigValue]"


def serialize_value(value: ConfigValue, serialize_unsupported: bool) -> ConfigValue:
    if not serialize_unsupported:
        return value

    return to_jsonable_python(value, fallback=stringify_unsupported)


def stringify_unsupported(value: ConfigValue) -> str:
    if isinstance(value, PurePath):
        return value.as_posix()

    return str(value)


def json_default(value: Any) -> str:  # noqa: ANN401 - json passes arbitrary unsupported values.
    """Encode supported path values and reject every other unsupported JSON value."""
    if isinstance(value, PurePath):
        return value.as_posix()

    raise TypeError(
        f"Object of type {type(value).__name__} is not JSON serializable; "
        "pass serialize_unsupported=True to save it as a string"
    )


__all__ = ["ConfigScalar", "ConfigValue", "json_default", "serialize_value"]
