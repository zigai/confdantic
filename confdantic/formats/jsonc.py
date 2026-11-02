from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal, TypeAlias

import jsonc
from pydantic import BaseModel

from confdantic.errors import MalformedDocumentError
from confdantic.formats.base import RenderOptions, ValueLocation
from confdantic.formats.json import json_location, reject_duplicate_keys, reject_nonfinite
from confdantic.formats.json_edit import edit_jsonc_bytes
from confdantic.io import decode_utf8_sig
from confdantic.plan import get_model_plan
from confdantic.values import ConfigValue, json_default, serialize_value

if TYPE_CHECKING:
    from confdantic.plan import ModelPlan

CommentPosition = Literal["end_of_line", "above_field"]
JsonContext: TypeAlias = tuple[
    Literal["object", "array"],
    type[BaseModel] | None,
]
KEY_PATTERN = re.compile(r'^(\s*)"((?:[^"\\]|\\.)*)"\s*:')


def insert_jsonc_comments(
    json_string: str,
    root_model: type[BaseModel],
    position: CommentPosition = "end_of_line",
) -> str:
    """Insert field comments into a pretty-printed JSON document."""
    lines = json_string.split("\n")
    result_lines: list[str] = []
    stack: list[JsonContext] = [("object", root_model)]

    for line in lines:
        match = KEY_PATTERN.match(line)
        if match:
            indentation = match.group(1)
            key = json.loads(f'"{match.group(2)}"')
            current_model = stack[-1][1] if stack else None

            comment, child_model = _resolve_jsonc_comment(key, current_model)
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
                    child_model,
                    not seen_value_open,
                )
                seen_value_open = True
        else:
            for char in structural_chars(line):
                apply_structural_char(stack, char, None, is_value_open=False)

            result_lines.append(line)

    return "\n".join(result_lines)


def _resolve_jsonc_comment(
    key: str,
    current_model: type[BaseModel] | None,
) -> tuple[str | None, type[BaseModel] | None]:
    if current_model is None:
        return None, None

    spec = get_model_plan(current_model).by_key.get(key)
    if spec is None:
        return None, None

    child_model = spec.nested_models[0] if len(spec.nested_models) == 1 else None
    return spec.get_comment(fmt="json"), child_model


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
    open_model: type[BaseModel] | None,
    is_value_open: bool,
) -> None:
    """Update JSON context while walking a structural character."""
    if char == "{":
        if is_value_open:
            stack.append(("object", open_model))
        elif stack and stack[-1][0] == "array":
            stack.append(("object", stack[-1][1]))
    elif char == "[":
        array_model = open_model if is_value_open else (stack[-1][1] if stack else None)
        stack.append(("array", array_model))
    elif char in "}]" and stack:
        stack.pop()


class JSONC:
    name = "jsonc"
    extensions = (".jsonc",)

    def __init__(self, encoding: str = "utf-8-sig") -> None:
        self.encoding = encoding

    def decode(self, data: bytes) -> Mapping[str, Any]:
        text = decode_utf8_sig(data) if self.encoding == "utf-8-sig" else data.decode(self.encoding)
        if not text.strip():
            raise MalformedDocumentError("JSONC document cannot be empty")

        try:
            document = jsonc.loads(
                text,
                object_pairs_hook=reject_duplicate_keys,
                parse_constant=reject_nonfinite,
                parse_float=reject_nonfinite,
            )
        except MalformedDocumentError:
            raise
        except (IndexError, json.JSONDecodeError, ValueError, TypeError):
            pass
        else:
            if not isinstance(document, Mapping):
                raise MalformedDocumentError("JSONC document root must be a mapping")

            return document

        raise MalformedDocumentError("Invalid JSONC document")

    def encode(
        self, document: Mapping[str, Any], *, plan: ModelPlan | None, options: RenderOptions
    ) -> bytes:
        serialized = serialize_value(dict(document), options.serialize_unsupported)
        text = json.dumps(serialized, indent=options.indent, default=json_default, allow_nan=False)
        if options.comments and plan is not None:
            text = insert_jsonc_comments(
                text,
                plan.model,
                position=options.comment_position,
            )

        return text.encode("utf-8")

    def locate(self, decoded: Mapping[str, Any], path: Sequence[str]) -> ValueLocation | None:
        return json_location(decoded, path)

    def edit(self, data: bytes, path: Sequence[str], value: ConfigValue) -> bytes:
        self.decode(data)
        result = edit_jsonc_bytes(data, path, value)
        self.decode(result)

        return result


__all__ = ["JSONC", "CommentPosition", "insert_jsonc_comments"]
