from __future__ import annotations

from collections.abc import Sequence
from types import UnionType
from typing import Annotated, ForwardRef, TypeAlias, Union, get_args, get_origin

from objinspect.typing import get_literal_choices, is_direct_literal
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from confdantic.values import ConfigValue

AnnotationValue: TypeAlias = "ConfigValue | type | None"
ModelFields: TypeAlias = dict[str, FieldInfo]


def base_model_annotation(annotation: AnnotationValue) -> type[BaseModel] | None:
    try:
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return annotation
    except TypeError:
        return None

    return None


def unwrapped_model(
    annotation: AnnotationValue,
    owner_model: type[BaseModel] | None = None,
) -> type[BaseModel] | None:
    """Resolve a model class from a direct, optional, annotated, or collection annotation."""
    if isinstance(annotation, str):
        model = (
            owner_model if owner_model is not None and annotation == owner_model.__name__ else None
        )
    elif isinstance(annotation, ForwardRef):
        model = unwrapped_model(annotation.__forward_arg__, owner_model)
    elif get_origin(annotation) is Annotated:
        args = get_args(annotation)
        model = unwrapped_model(args[0], owner_model) if args else None
    elif get_origin(annotation) in (Union, UnionType):
        args = tuple(arg for arg in get_args(annotation) if arg is not type(None))
        model = unwrapped_model(args[0], owner_model) if len(args) == 1 else None
    elif get_origin(annotation) in (list, set, frozenset, Sequence):
        args = get_args(annotation)
        model = unwrapped_model(args[0], owner_model) if len(args) == 1 else None
    elif get_origin(annotation) is tuple:
        args = get_args(annotation)
        if len(args) == 1 or (len(args) == 2 and args[1] is Ellipsis):
            model = unwrapped_model(args[0], owner_model)
        else:
            model = None
    else:
        model = base_model_annotation(annotation)

    return model


def nested_model_fields(
    annotation: AnnotationValue,
    owner_model: type[BaseModel] | None = None,
) -> ModelFields | None:
    model = unwrapped_model(annotation, owner_model)
    return model.model_fields if model is not None else None


def literal_choices(annotation: AnnotationValue) -> tuple[object, ...] | None:
    """Return choices from a literal or union of literals, including an optional null choice."""
    if is_direct_literal(annotation):
        return tuple(get_literal_choices(annotation))

    origin = get_origin(annotation)
    if origin is not Union and origin is not UnionType:
        return None

    args = get_args(annotation)
    has_none = any(arg is type(None) for arg in args)
    choices: list[object] = []
    for arg in args:
        if arg is type(None):
            continue

        if not is_direct_literal(arg):
            return None

        choices.extend(get_literal_choices(arg))

    if has_none:
        choices.append(None)

    return tuple(choices)


__all__ = [
    "AnnotationValue",
    "ModelFields",
    "base_model_annotation",
    "literal_choices",
    "nested_model_fields",
    "unwrapped_model",
]
