from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from confdantic.values import ConfigValue, json_default, serialize_value


def load(path: Path, encoding: str) -> Any:  # noqa: ANN401 - parsed JSON is a dynamic boundary.
    return json.loads(path.read_text(encoding=encoding))


def render(data: ConfigValue, serialize_unsupported: bool, indent: int) -> str:
    serialized = serialize_value(data, serialize_unsupported)
    return json.dumps(serialized, indent=indent, default=json_default)


__all__ = ["load", "render"]
