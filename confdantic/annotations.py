from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import NoneType
from typing import Any, TypeAlias, get_args, get_origin

from pydantic import BaseModel, SecretBytes, SecretStr
from typing_inspection import typing_objects
from typing_inspection.introspection import get_literal_values, is_union_origin

from confdantic.values import ConfigValue

AnnotationValue: TypeAlias = "ConfigValue | type | None"

SEQUENCE_ORIGINS: tuple[object, ...] = (list, Sequence, set, frozenset, tuple)
MAPPING_ORIGINS: tuple[object, ...] = (dict, Mapping)


def unwrap_optional(annotation: object) -> object:
    """Strip `Annotated` and `X | None` wrappers that do not change a field's shape."""
    origin = get_origin(annotation)
    if typing_objects.is_annotated(origin):
        return unwrap_optional(get_args(annotation)[0])

    if is_union_origin(origin):
        args = tuple(arg for arg in get_args(annotation) if arg is not NoneType)
        return unwrap_optional(args[0]) if len(args) == 1 else annotation

    return annotation


def extract_nested_models(  # noqa: C901
    annotation: Any,  # noqa: ANN401
    owner_model: type[BaseModel] | None = None,
) -> tuple[type[BaseModel], ...]:
    """Return every model reachable through unions, sequences, and mapping values."""
    models: list[type[BaseModel]] = []

    def collect(value: Any) -> None:  # noqa: ANN401, C901
        if isinstance(value, str):
            if owner_model is not None and value == owner_model.__name__:
                models.append(owner_model)

            return

        if typing_objects.is_forwardref(value):
            collect(value.__forward_arg__)
            return

        origin = get_origin(value)
        if typing_objects.is_annotated(origin):
            collect(get_args(value)[0])
        elif is_union_origin(origin) or origin in SEQUENCE_ORIGINS:
            for arg in get_args(value):
                if arg is not NoneType and arg is not Ellipsis:
                    collect(arg)
        elif origin in MAPPING_ORIGINS:
            args = get_args(value)
            if len(args) > 1:
                collect(args[1])
        elif isinstance(value, type) and issubclass(value, BaseModel):
            models.append(value)

    collect(annotation)

    return tuple(dict.fromkeys(models))


def is_sequence_type(annotation: object) -> bool:
    return get_origin(unwrap_optional(annotation)) in SEQUENCE_ORIGINS


def is_mapping_type(annotation: object) -> bool:
    return get_origin(unwrap_optional(annotation)) in MAPPING_ORIGINS


def entry_type(annotation: object) -> Any:  # noqa: ANN401
    """Return the value type of a mapping or homogeneous sequence annotation."""
    inner = unwrap_optional(annotation)
    origin = get_origin(inner)
    args = get_args(inner)
    if origin in MAPPING_ORIGINS:
        return args[1] if len(args) == 2 else None

    if origin is tuple:
        return args[0] if len(args) == 2 and args[1] is Ellipsis else None

    if origin in SEQUENCE_ORIGINS:
        return args[0] if len(args) == 1 else None

    return None


def is_secret_type(annotation: object) -> bool:
    if get_origin(annotation) is not None:
        return any(is_secret_type(arg) for arg in get_args(annotation))

    return isinstance(annotation, type) and issubclass(annotation, (SecretStr, SecretBytes))


def literal_choices(annotation: object) -> tuple[object, ...] | None:
    """Return choices from a literal or union of literals, including an optional null choice."""
    origin = get_origin(annotation)
    if typing_objects.is_literal(origin):
        return tuple(get_literal_values(annotation))

    if not is_union_origin(origin):
        return None

    args = get_args(annotation)
    choices: list[object] = []
    for arg in args:
        if arg is NoneType:
            continue

        if not typing_objects.is_literal(get_origin(arg)):
            return None

        choices.extend(get_literal_values(arg))

    if NoneType in args:
        choices.append(None)

    return tuple(choices)


__all__ = [
    "AnnotationValue",
    "entry_type",
    "extract_nested_models",
    "is_mapping_type",
    "is_secret_type",
    "is_sequence_type",
    "literal_choices",
    "unwrap_optional",
]
