from __future__ import annotations

import csv
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, TypeAdapter, ValidationError
from pydantic_core import to_jsonable_python

from confdantic.duration import format_duration, parse_duration
from confdantic.errors import (
    ConfigError,
    ConfigValidationError,
    InvalidKeyPathError,
    UnknownKeyError,
    UnsupportedFormatError,
)
from confdantic.formats.base import EditableFormat
from confdantic.formats.registry import select_formats
from confdantic.keypath import KeyPath, to_key_path
from confdantic.merge import collect_origins
from confdantic.persistence import ConfigFile
from confdantic.plan import ModelPlan, get_model_plan
from confdantic.provenance import REDACTED, SourceKind
from confdantic.values import reveal_secrets

# bool comes before int because bool is an int subclass.
SCALAR_ADAPTERS: dict[type, TypeAdapter[Any]] = {
    bool: TypeAdapter(bool),
    int: TypeAdapter(int),
    float: TypeAdapter(float),
}


def set_key(
    path: Path | str,
    key: str | KeyPath,
    value: Any,  # noqa: ANN401
    *,
    model: type[BaseModel] | None = None,
) -> None:
    """Edit one key in a file, keeping its comments and layout. See `set_keys`."""
    set_keys(path, {key: value}, model=model)


def set_keys(
    path: Path | str,
    updates: Mapping[str | KeyPath, Any],
    *,
    model: type[BaseModel] | None = None,
) -> None:
    """Edit several keys in one atomic write, keeping comments, layout, and permissions.

    Keys are dotted, such as `database.port`, or key paths such as `("labels", "a.b")`.
    With `model`, each key must be declared and each value must validate against its
    field; strings for list fields are split as CSV. Without it, a string is coerced
    to the type of the value it replaces. A symlink is followed. Writers wait on a
    shared sidecar lock.
    """
    paths = {to_key_path(key): value for key, value in updates.items()}
    values = _checked_updates(paths, model) if model is not None else paths
    if not values:
        return

    config_file = ConfigFile(path)
    if not config_file.path.is_file():
        raise FileNotFoundError(f"Configuration file not found: '{path}'")

    editor = _editor(config_file.path.suffix)
    with config_file:
        data = config_file.read()
        for key, value in values.items():
            data = _edit(editor, data, key, value, coerce=model is None)

        config_file.write(data)


def set_bytes(
    format: str,  # noqa: A002
    data: bytes,
    key: str | KeyPath,
    value: Any,  # noqa: ANN401
) -> bytes:
    """Edit one key in a built-in format's bytes; `format` is a name or an extension."""
    return _edit(_editor(format), data, to_key_path(key), value, coerce=True)


def _checked_updates(updates: Mapping[KeyPath, Any], model: type[BaseModel]) -> dict[KeyPath, Any]:
    """Validate each value against its field and return what should be written."""
    plan = get_model_plan(model)
    result: dict[KeyPath, Any] = {}
    for key, value in updates.items():
        target = plan.target(key)
        if not target.known:
            raise UnknownKeyError(key, suggestion=plan.suggest(key))

        adapter = plan.value_adapter(key)
        if adapter is None:
            # Extra keys on `extra="allow"` models and deep map entries have no fixed type.
            result[key] = value
            continue

        # The whole field, as opposed to one entry of a mapping or sequence field.
        field = target.spec if target.depth == 0 else None
        candidate = value
        if field is not None and field.is_sequence and isinstance(value, str):
            text = value.strip()
            candidate = next(csv.reader([text], skipinitialspace=True)) if text else []

        try:
            validated = plan.validate_value(key, candidate)
        except ValidationError as e:
            errors = _validation_errors(plan, key, candidate, e)
        else:
            # Python mode keeps secrets readable; JSON mode would mask them as "**********".
            dumped = reveal_secrets(adapter.dump_python(validated, mode="python"))
            dumped = plan.to_document_value_at(key, dumped, original=validated)
            result[key] = to_jsonable_python(dumped)

            continue

        # Raised outside the handler so the input-bearing ValidationError is not chained.
        raise ConfigValidationError(errors)

    return result


def _validation_errors(
    plan: ModelPlan, key: KeyPath, value: object, error: ValidationError
) -> list[ConfigError]:
    document: dict[str, object] = {key[-1]: value}
    for segment in reversed(key[:-1]):
        document = {segment: document}

    origins = collect_origins(document, SourceKind.DEFAULT, None, plan)
    secret_keys = [name for name, origin in origins.items() if origin.raw_value == REDACTED]
    errors = []
    for detail in error.errors():
        error_key = plan.key_for_loc(detail["loc"], prefix=key)
        secret = plan.is_secret_path(error_key) or any(
            name[: len(error_key)] == error_key for name in secret_keys
        )
        message = "Invalid secret configuration value" if secret else detail["msg"]
        errors.append(ConfigError(message, key=error_key))

    return errors


def _editor(format_name: str) -> EditableFormat:
    extension = format_name if format_name.startswith(".") else f".{format_name}"
    try:
        editor = select_formats((extension,))[0].format
    except UnsupportedFormatError:
        editor = None

    if not isinstance(editor, EditableFormat):
        raise UnsupportedFormatError(
            f"Format '{format_name}' does not support comment-preserving in-place editing"
        )

    return editor


def _edit(
    editor: EditableFormat,
    data: bytes,
    key: KeyPath,
    value: Any,  # noqa: ANN401
    *,
    coerce: bool,
) -> bytes:
    if coerce:
        value = _coerce_value(_existing_value(editor.decode(data), key), value)

    result = editor.edit(data, key, value)
    editor.decode(result)  # The rewrite must still parse.

    return result


def _existing_value(document: Mapping[str, Any], segments: KeyPath) -> Any:  # noqa: ANN401
    current: Any = document
    for index, segment in enumerate(segments):
        if not isinstance(current, Mapping):
            raise InvalidKeyPathError(
                f"Cannot traverse non-object at segment '{segments[index - 1]}' in key path"
            )

        if segment not in current:
            return None

        current = current[segment]

    return current


def _coerce_value(existing: Any, value: Any) -> Any:  # noqa: ANN401, PLR0911
    """Coerce a string to the type of the value it replaces; format durations canonically."""
    if isinstance(value, timedelta):
        return format_duration(value)

    if not isinstance(value, str):
        return value

    scalar = next((kind for kind in SCALAR_ADAPTERS if isinstance(existing, kind)), None)
    if scalar is not None:
        try:
            return SCALAR_ADAPTERS[scalar].validate_python(value.strip())
        except ValidationError:
            return value

    if isinstance(existing, str):
        try:
            parse_duration(existing)
            return format_duration(parse_duration(value))
        except ValueError:
            return value

    return value


__all__ = ["set_bytes", "set_key", "set_keys"]
