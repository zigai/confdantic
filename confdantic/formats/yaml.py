from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from confdantic.annotations import ModelFields, unwrapped_model
from confdantic.comments import get_comment
from confdantic.values import ConfigValue, serialize_value


def load(path: Path) -> Any:  # noqa: ANN401 - parsed YAML is a dynamic boundary.
    return YAML().load(path.read_text(encoding="utf-8"))


def render(
    data: ConfigValue,
    model: type[BaseModel],
    comments: bool,
    serialize_unsupported: bool,
) -> str:
    serialized = serialize_value(data, serialize_unsupported)
    if comments:
        serialized = build_commented_value(serialized, model.model_fields, model)

    yaml = YAML()
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.preserve_quotes = True
    stream = StringIO()
    yaml.dump(serialized, stream)

    return stream.getvalue()


def build_commented_value(
    value: ConfigValue,
    model_fields: ModelFields | None,
    owner_model: type[BaseModel] | None = None,
) -> ConfigValue | CommentedMap | CommentedSeq:
    if isinstance(value, dict):
        commented_mapping = CommentedMap()
        for key, child_value in value.items():
            field = model_fields.get(key) if model_fields is not None else None
            child_model = (
                unwrapped_model(field.annotation, owner_model) if field is not None else None
            )
            child_fields = child_model.model_fields if child_model is not None else None
            commented_mapping[key] = build_commented_value(
                child_value,
                child_fields,
                child_model,
            )

            if field is not None:
                comment = get_comment(field, fmt="yaml")
                if comment:
                    commented_mapping.yaml_add_eol_comment(comment, key)

        return commented_mapping

    if isinstance(value, list):
        commented_sequence = CommentedSeq()
        for item in value:
            commented_sequence.append(build_commented_value(item, model_fields, owner_model))

        return commented_sequence

    return value


__all__ = ["build_commented_value", "load", "render"]
