from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

import tomlkit
import tomlkit.exceptions
from tomlkit.container import Container
from tomlkit.items import AbstractTable, AoT, Array, Item, Table

from confdantic.errors import InvalidKeyPathError, MalformedDocumentError
from confdantic.formats.base import RenderOptions, ValueLocation
from confdantic.io import decode_utf8_sig
from confdantic.plan import get_model_plan
from confdantic.values import ConfigValue, serialize_value

if TYPE_CHECKING:
    from confdantic.plan import ModelPlan


def set_comment(item: Item, comment: str) -> None:
    """Attach a comment, using the first table header for arrays of tables."""
    if isinstance(item, (Array, AoT)) and item and isinstance(item[0], Table):
        item[0].comment(comment)
    else:
        item.comment(comment)


def _add_plan_comments(document: Any, plan: ModelPlan) -> None:  # noqa: ANN401
    for key, spec in plan.by_key.items():
        try:
            item = document.item(key)
        except KeyError:
            continue

        comment = spec.get_comment(fmt="toml")
        if comment:
            set_comment(item, comment)

        if len(spec.nested_models) == 1:
            child_plan = get_model_plan(spec.nested_models[0])
            child = document[key]
            if isinstance(child, (Array, AoT)):
                for entry in child:
                    if isinstance(entry, Table):
                        _add_plan_comments(entry, child_plan)
            elif isinstance(child, Table):
                _add_plan_comments(child, child_plan)


def _get_child(mapping: Any, key: str) -> Any:  # noqa: ANN401
    if isinstance(mapping, (Container, AbstractTable)):
        return mapping.item(key) if key in mapping else None

    if isinstance(mapping, Mapping):
        return mapping.get(key)
    if isinstance(mapping, (list, Array, AoT)) and key.isdigit() and int(key) < len(mapping):
        return mapping[int(key)]
    return None


class TOML:
    name = "toml"
    extensions = (".toml", ".tml")

    def decode(self, data: bytes) -> Mapping[str, Any]:
        try:
            doc = tomlkit.loads(decode_utf8_sig(data))
        except (tomlkit.exceptions.TOMLKitError, TypeError, ValueError, IndexError):
            pass
        else:
            return doc

        raise MalformedDocumentError("Invalid TOML document")

    def encode(
        self, document: Mapping[str, Any], *, plan: ModelPlan | None, options: RenderOptions
    ) -> bytes:
        serialized = _without_none(serialize_value(dict(document), options.serialize_unsupported))
        if not isinstance(serialized, dict):
            raise TypeError("TOML encoding requires a mapping")

        output = tomlkit.loads(tomlkit.dumps(serialized))
        if options.comments and plan is not None:
            _add_plan_comments(output, plan)

        return tomlkit.dumps(output).encode("utf-8")

    def locate(self, decoded: Mapping[str, Any], path: Sequence[str]) -> ValueLocation | None:
        if not path:
            return None

        current: Any = decoded
        for segment in path:
            value = _get_child(current, segment)
            if value is None:
                return None

            current = value
        raw = (
            current.as_string().strip()
            if isinstance(current, Item) and not isinstance(current, str)
            else str(current)
        )

        return ValueLocation(None, raw)

    def edit(self, data: bytes, path: Sequence[str], value: ConfigValue) -> bytes:
        if not path:
            raise InvalidKeyPathError("Invalid empty key path")

        doc = self.decode(data)
        current: Any = doc
        for segment in path[:-1]:
            if segment not in current:
                current[segment] = tomlkit.table()
            elif not isinstance(current[segment], Mapping):
                raise InvalidKeyPathError(
                    f"Cannot traverse non-object at segment '{segment}' in key path"
                )

            current = current[segment]

        current[path[-1]] = value

        return tomlkit.dumps(doc).encode("utf-8")


def _without_none(value: Any) -> Any:  # noqa: ANN401
    # TOML has no null, so unset optional values are left out.
    if isinstance(value, dict):
        return {key: _without_none(child) for key, child in value.items() if child is not None}

    if isinstance(value, list):
        return [_without_none(child) for child in value]

    return value


__all__ = ["TOML"]
