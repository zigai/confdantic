from __future__ import annotations

from pathlib import Path
from typing import Any

import tomlkit
from pydantic import BaseModel
from pydantic.fields import FieldInfo
from tomlkit.items import AoT, Array, Item, Table

from confdantic.annotations import unwrapped_model
from confdantic.comments import get_comment
from confdantic.values import ConfigValue, serialize_value


def load(path: Path) -> Any:  # noqa: ANN401 - parsed TOML is a dynamic boundary.
    return tomlkit.loads(path.read_text(encoding="utf-8-sig"))


def render(
    data: ConfigValue,
    model: type[BaseModel],
    comments: bool,
    serialize_unsupported: bool,
) -> str:
    serialized = serialize_value(data, serialize_unsupported)
    if not isinstance(serialized, dict):  # pragma: no cover - render input is always a model dump.
        raise TypeError("TOML rendering requires a mapping")

    document = tomlkit.loads(tomlkit.dumps(serialized))
    if not comments:
        return tomlkit.dumps(document)

    for name, field in model.model_fields.items():
        try:
            item = document.item(name)
        except KeyError:
            continue

        comment = get_comment(field, fmt="toml")
        if comment:
            set_comment(item, comment)

        subitem = document[name]
        if isinstance(subitem, Item):
            add_nested_comments(field, subitem, model)

    return tomlkit.dumps(document)


def set_comment(item: Item, comment: str) -> None:
    """Attach a comment, using the first table header for arrays of tables."""
    if isinstance(item, (Array, AoT)) and item and isinstance(item[0], Table):
        item[0].comment(comment)
    else:
        item.comment(comment)


def add_nested_comments(
    field: FieldInfo,
    item: Item,
    owner_model: type[BaseModel],
) -> None:
    nested_model = unwrapped_model(field.annotation, owner_model)
    if nested_model is None:
        return

    if isinstance(item, (Array, AoT)):
        for subitem in item:
            if isinstance(subitem, Table):
                add_nested_comments(field, subitem, owner_model)

        return

    if not isinstance(item, Table):
        return

    for subfield_name, subfield in nested_model.model_fields.items():
        try:
            subitem = item.item(subfield_name)
        except KeyError:
            continue

        comment = get_comment(subfield, fmt="toml")
        if comment:
            set_comment(subitem, comment)

        add_nested_comments(subfield, subitem, nested_model)


__all__ = ["load", "render"]
