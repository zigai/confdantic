from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from confdantic.errors import (
    InvalidKeyPathError,
    MalformedDocumentError,
)
from confdantic.io import decode_utf8_sig


def edit_jsonc_bytes(data: bytes, path: Sequence[str], value: Any) -> bytes:  # noqa: ANN401
    """Edit the value at `path` in JSON or JSONC bytes, preserving comments and formatting."""
    text = decode_utf8_sig(data)
    if not path:
        raise InvalidKeyPathError("Invalid empty key path")

    tokens = tokenize_jsonc(text)
    root_obj_idx = _find_root_object_token(tokens)

    new_text = _edit_object_at_path(text, tokens, root_obj_idx, list(path), value)

    return new_text.encode("utf-8")


def tokenize_jsonc(text: str) -> list[tuple[str, str, int, int]]:
    """Tokenize JSONC into (kind, value, start_char, end_char)."""
    tokens: list[tuple[str, str, int, int]] = []
    i = 0
    n = len(text)

    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue

        if c == "/" and i + 1 < n and text[i + 1] in ("/", "*"):
            tok, i = _consume_comment(text, i, n)
            tokens.append(tok)
            continue

        if c == '"':
            tok, i = _consume_string(text, i, n)
            tokens.append(tok)
            continue

        if c in "{}[]:,":
            tokens.append(("punct", c, i, i + 1))
            i += 1
            continue

        start = i

        while (
            i < n
            and not text[i].isspace()
            and text[i] not in "{}[]:,"
            and not (text[i] == "/" and i + 1 < n and text[i + 1] in "/*")
        ):
            i += 1

        raw = text[start:i]
        tokens.append(("primitive", raw, start, i))

    return tokens


def _consume_comment(
    text: str,
    start: int,
    n: int,
) -> tuple[tuple[str, str, int, int], int]:
    if text[start + 1] == "/":
        i = start + 2
        while i < n and text[i] != "\n":
            i += 1

        return ("comment", text[start:i], start, i), i

    i = start + 2
    while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
        i += 1

    i = min(n, i + 2)

    return ("comment", text[start:i], start, i), i


def _consume_string(
    text: str,
    start: int,
    n: int,
) -> tuple[tuple[str, str, int, int], int]:
    i = start + 1
    while i < n:
        if text[i] == "\\":
            i += 2
        elif text[i] == '"':
            i += 1
            break
        else:
            i += 1

    raw_str = text[start:i]
    try:
        val_str = json.loads(raw_str)
    except (json.JSONDecodeError, ValueError):
        raise MalformedDocumentError("Invalid JSON string") from None

    return ("string", val_str, start, i), i


def _find_root_object_token(tokens: list[tuple[str, str, int, int]]) -> int:
    for idx, tok in enumerate(tokens):
        if tok[0] == "comment":
            continue

        if tok[0] == "punct" and tok[1] == "{":
            return idx

        raise MalformedDocumentError(f"Expected root object in JSON document, got {tok[0]}")

    raise MalformedDocumentError("Empty JSON document, expected root object")


def _skip_balanced_container(
    tokens: list[tuple[str, str, int, int]],
    idx: int,
    open_c: str,
    close_c: str,
) -> int:
    depth = 1
    curr = idx + 1
    while curr < len(tokens) and depth > 0:
        if tokens[curr][0] == "punct":
            if tokens[curr][1] == open_c:
                depth += 1
            elif tokens[curr][1] == close_c:
                depth -= 1

        curr += 1

    return curr


def _parse_value_span(
    tokens: list[tuple[str, str, int, int]],
    idx: int,
) -> tuple[int, int, int]:
    tok = tokens[idx]
    kind, _, start, _ = tok

    if kind in ("string", "primitive"):
        return start, tok[3], idx + 1

    if kind == "punct" and tok[1] == "[":
        end_idx = _skip_balanced_container(tokens, idx, "[", "]")
        return start, tokens[end_idx - 1][3], end_idx

    if kind == "punct" and tok[1] == "{":
        end_idx = _skip_balanced_container(tokens, idx, "{", "}")
        return start, tokens[end_idx - 1][3], end_idx

    raise MalformedDocumentError(f"Unexpected token in JSON value: {tok}")


def _skip_comments(tokens: list[tuple[str, str, int, int]], idx: int) -> int:
    while idx < len(tokens) and tokens[idx][0] == "comment":
        idx += 1

    return idx


def _find_members(
    tokens: list[tuple[str, str, int, int]],
    obj_start_idx: int,
) -> tuple[
    dict[str, tuple[tuple[str, str, int, int], tuple[str, str, int, int], int, int, bool, int]],
    tuple[str, str, int, int],
    bool,
]:
    idx = obj_start_idx + 1
    members: dict[
        str,
        tuple[tuple[str, str, int, int], tuple[str, str, int, int], int, int, bool, int],
    ] = {}
    has_trailing_comma = False

    while idx < len(tokens):
        idx = _skip_comments(tokens, idx)
        if idx >= len(tokens) or (tokens[idx][0] == "punct" and tokens[idx][1] == "}"):
            break

        if tokens[idx][0] != "string":
            raise MalformedDocumentError(f"Expected string key in object, got {tokens[idx][0]}")

        key_tok = tokens[idx]
        key = key_tok[1]
        if key in members:
            raise MalformedDocumentError(f"Duplicate key '{key}' found in JSON object")

        idx = _skip_comments(tokens, idx + 1)
        if idx >= len(tokens) or tokens[idx][0] != "punct" or tokens[idx][1] != ":":
            raise MalformedDocumentError(f"Expected ':' after key '{key}'")

        colon_tok = tokens[idx]
        idx = _skip_comments(tokens, idx + 1)

        val_tok_idx = idx
        val_is_obj = tokens[idx][0] == "punct" and tokens[idx][1] == "{"
        v_start, v_end, next_idx = _parse_value_span(tokens, idx)
        members[key] = (key_tok, colon_tok, v_start, v_end, val_is_obj, val_tok_idx)
        idx = _skip_comments(tokens, next_idx)

        if idx < len(tokens) and tokens[idx][0] == "punct" and tokens[idx][1] == ",":
            idx = _skip_comments(tokens, idx + 1)
            if idx < len(tokens) and tokens[idx][0] == "punct" and tokens[idx][1] == "}":
                has_trailing_comma = True
                break

    closing_brace = tokens[idx]

    return members, closing_brace, has_trailing_comma


def _edit_object_at_path(
    text: str,
    tokens: list[tuple[str, str, int, int]],
    obj_idx: int,
    segments: list[str],
    value: Any,  # noqa: ANN401
) -> str:
    members, closing_brace, has_trailing = _find_members(tokens, obj_idx)
    seg = segments[0]

    if len(segments) == 1:
        val_json = json.dumps(value, indent=2, allow_nan=False)
        if seg in members:
            _, _, v_start, v_end, _, _ = members[seg]
            return text[:v_start] + val_json + text[v_end:]

        return _insert_member_into_object(
            text,
            closing_brace,
            members,
            seg,
            val_json,
            has_trailing,
        )

    if seg in members:
        _, _, _, _, val_is_obj, child_obj_idx = members[seg]
        if not val_is_obj:
            raise InvalidKeyPathError(f"Cannot traverse non-object at segment '{seg}' in key path")

        return _edit_object_at_path(text, tokens, child_obj_idx, segments[1:], value)

    nested_dict = _build_nested_dict(segments[1:], value)
    nested_json = json.dumps(nested_dict, indent=2, allow_nan=False)
    return _insert_member_into_object(
        text,
        closing_brace,
        members,
        seg,
        nested_json,
        has_trailing,
    )


def _build_nested_dict(segments: list[str], leaf_val: Any) -> dict[str, Any]:  # noqa: ANN401
    curr: dict[str, Any] = {}
    node = curr
    for i, s in enumerate(segments):
        if i == len(segments) - 1:
            node[s] = leaf_val
        else:
            node[s] = {}
            node = node[s]

    return curr


def _insert_member_into_object(
    text: str,
    closing_brace_tok: tuple[str, str, int, int],
    members: Mapping[str, Any],
    key: str,
    val_json: str,
    has_trailing_comma: bool,
) -> str:
    brace_start = closing_brace_tok[2]
    line_start = text.rfind("\n", 0, brace_start)
    line_prefix = text[line_start + 1 : brace_start]
    base_indent = line_prefix[: len(line_prefix) - len(line_prefix.lstrip(" \t"))]
    child_indent = base_indent + "  "

    indented_val = _indent_multiline_string(val_json, child_indent)
    encoded_key = json.dumps(key, ensure_ascii=False)

    if not members:
        insertion = f"\n{child_indent}{encoded_key}: {indented_val}\n{base_indent}"
        return text[:brace_start] + insertion + text[brace_start:]

    _, last_info = list(members.items())[-1]
    last_v_end = last_info[3]

    if not has_trailing_comma:
        prefix = text[:last_v_end] + "," + text[last_v_end:brace_start]
        if not prefix.endswith("\n"):
            prefix += "\n"

        return (
            prefix
            + f"{child_indent}{encoded_key}: {indented_val}\n{base_indent}"
            + text[brace_start:]
        )

    return (
        text[:brace_start]
        + f"{child_indent}{encoded_key}: {indented_val},\n{base_indent}"
        + text[brace_start:]
    )


def _indent_multiline_string(val_str: str, indent: str) -> str:
    lines = val_str.split("\n")
    if len(lines) <= 1:
        return val_str

    result = [lines[0]]
    result.extend(f"{indent}{line}" if line else "" for line in lines[1:])

    return "\n".join(result)


__all__ = ["edit_jsonc_bytes", "tokenize_jsonc"]
