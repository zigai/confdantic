from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal, TypeAlias

import jsonc
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from confdantic.annotations import ModelFields, unwrapped_model
from confdantic.comments import get_comment
from confdantic.values import ConfigValue, json_default, serialize_value

CommentPosition = Literal["end_of_line", "above_field"]
JsonContext: TypeAlias = tuple[
    Literal["object", "array"],
    ModelFields | None,
    type[BaseModel] | None,
]
KEY_PATTERN = re.compile(r'^(\s*)"((?:[^"\\]|\\.)*)"\s*:')


def load(path: Path, encoding: str) -> Any:  # noqa: ANN401 - parsed JSONC is a dynamic boundary.
    return jsonc.loads(path.read_text(encoding=encoding))


def render(
    data: ConfigValue,
    model: type[BaseModel],
    comments: bool,
    serialize_unsupported: bool,
    position: CommentPosition,
    indent: int,
) -> str:
    serialized = serialize_value(data, serialize_unsupported)
    json_string = json.dumps(serialized, indent=indent, default=json_default)
    if not comments:
        return json_string

    return insert_jsonc_comments(
        json_string,
        model.model_fields,
        position=position,
        root_model=model,
    )


def insert_jsonc_comments(
    json_string: str,
    model_fields: ModelFields,
    nested_fields: dict[str, dict[str, FieldInfo]] | None = None,
    position: CommentPosition = "end_of_line",
    root_model: type[BaseModel] | None = None,
) -> str:
    """Insert field comments into a pretty-printed JSON document."""
    lines = json_string.split("\n")
    result_lines: list[str] = []
    stack: list[JsonContext] = [("object", model_fields, root_model)]

    for line in lines:
        match = KEY_PATTERN.match(line)
        if match:
            indentation = match.group(1)
            key = json.loads(f'"{match.group(2)}"')

            field = None
            child_fields = None
            child_model = None
            current_fields = stack[-1][1] if stack else None
            current_model = stack[-1][2] if stack else None
            if stack and stack[-1][0] == "object" and current_fields is not None:
                field = current_fields.get(key)
                if field is not None:
                    child_model = unwrapped_model(field.annotation, current_model)
                    child_fields = child_model.model_fields if child_model is not None else None
                    if child_fields is None and len(stack) == 1 and nested_fields is not None:
                        child_fields = nested_fields.get(key)

            comment = get_comment(field, fmt="json") if field is not None else None
            if comment:
                if position == "end_of_line":
                    result_lines.append(f"{line.rstrip()} // {comment}")
                else:
                    result_lines.append(f"{indentation}// {comment}")
                    result_lines.append(line)
            else:
                result_lines.append(line)

            seen_value_open = False
            for char in structural_chars(line, match.end()):
                apply_structural_char(
                    stack,
                    char,
                    child_fields,
                    child_model,
                    not seen_value_open,
                )
                seen_value_open = True
        else:
            for char in structural_chars(line):
                apply_structural_char(stack, char, None, None, is_value_open=False)

            result_lines.append(line)

    return "\n".join(result_lines)


def structural_chars(line: str, start: int = 0) -> list[str]:
    """Return JSON structural characters outside double-quoted strings in order."""
    chars: list[str] = []
    in_string = False
    i = start
    while i < len(line):
        char = line[i]
        if in_string:
            if char == "\\":
                i += 2
                continue

            if char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "{[]}":
            chars.append(char)
        i += 1

    return chars


def apply_structural_char(
    stack: list[JsonContext],
    char: str,
    open_fields: ModelFields | None,
    open_model: type[BaseModel] | None,
    is_value_open: bool,
) -> None:
    """Update JSON context while walking a structural character."""
    if char == "{":
        if is_value_open:
            stack.append(("object", open_fields, open_model))
        elif stack and stack[-1][0] == "array":
            stack.append(("object", stack[-1][1], stack[-1][2]))
    elif char == "[":
        if is_value_open:
            array_fields = open_fields
        elif stack and stack[-1][0] == "array":
            array_fields = stack[-1][1]
        else:
            array_fields = None
        array_model = open_model if is_value_open else (stack[-1][2] if stack else None)
        stack.append(("array", array_fields, array_model))
    elif char in "}]" and stack:
        stack.pop()


__all__ = ["CommentPosition", "insert_jsonc_comments", "load", "render"]
