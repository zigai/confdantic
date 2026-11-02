from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from confdantic.errors import MalformedDocumentError
from confdantic.formats.base import RenderOptions, ValueLocation
from confdantic.formats.json_edit import edit_jsonc_bytes
from confdantic.io import decode_utf8_sig
from confdantic.values import ConfigValue, json_default, serialize_value

if TYPE_CHECKING:
    from confdantic.plan import ModelPlan


def reject_duplicate_keys(pairs: list[tuple[Any, Any]]) -> dict[str, Any]:
    """`object_pairs_hook` that refuses duplicate members instead of keeping the last one."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        name = str(key)
        if name in result:
            raise MalformedDocumentError(f"Duplicate key '{name}' found in JSON object")

        result[name] = value

    return result


def json_location(decoded: Mapping[str, Any], path: Sequence[str]) -> ValueLocation | None:
    if not path:
        return None

    current: Any = decoded
    for segment in path:
        if isinstance(current, Mapping) and segment in current:
            current = current[segment]
        elif isinstance(current, list) and segment.isdigit() and int(segment) < len(current):
            current = current[int(segment)]
        else:
            return None

    raw = current if isinstance(current, str) else json.dumps(current, ensure_ascii=False)
    return ValueLocation(None, raw)


def reject_nonfinite(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise MalformedDocumentError("JSON numbers must be finite")

    return number


class JSON:
    name = "json"
    extensions = (".json",)

    def __init__(self, encoding: str = "utf-8-sig") -> None:
        self.encoding = encoding

    def decode(self, data: bytes) -> Mapping[str, Any]:
        text = decode_utf8_sig(data) if self.encoding == "utf-8-sig" else data.decode(self.encoding)
        if not text.strip():
            raise MalformedDocumentError("JSON document cannot be empty")

        try:
            document = json.loads(
                text,
                object_pairs_hook=reject_duplicate_keys,
                parse_constant=reject_nonfinite,
                parse_float=reject_nonfinite,
            )
        except MalformedDocumentError:
            raise
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        else:
            if not isinstance(document, Mapping):
                raise MalformedDocumentError("JSON document root must be a mapping")

            return document

        raise MalformedDocumentError("Invalid JSON document")

    def encode(
        self, document: Mapping[str, Any], *, plan: ModelPlan | None, options: RenderOptions
    ) -> bytes:
        _ = plan
        serialized = serialize_value(dict(document), options.serialize_unsupported)
        return json.dumps(
            serialized, indent=options.indent, default=json_default, allow_nan=False
        ).encode("utf-8")

    def locate(self, decoded: Mapping[str, Any], path: Sequence[str]) -> ValueLocation | None:
        return json_location(decoded, path)

    def edit(self, data: bytes, path: Sequence[str], value: ConfigValue) -> bytes:
        self.decode(data)
        result = edit_jsonc_bytes(data, path, value)
        self.decode(result)

        return result


__all__ = ["JSON", "json_location", "reject_duplicate_keys", "reject_nonfinite"]
