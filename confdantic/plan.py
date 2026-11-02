from __future__ import annotations

import sys
from collections.abc import Mapping
from copy import copy
from dataclasses import dataclass
from datetime import timedelta
from functools import cache
from typing import Annotated, Any, Generic, TypeVar, get_args, get_origin

from pydantic import BaseModel, TypeAdapter
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined, to_jsonable_python
from typing_inspection.introspection import is_union_origin

from confdantic.annotations import (
    MAPPING_ORIGINS,
    SEQUENCE_ORIGINS,
    entry_type,
    extract_nested_models,
    is_mapping_type,
    is_secret_type,
    is_sequence_type,
    literal_choices,
    unwrap_optional,
)
from confdantic.comments import CommentFormat, get_comment
from confdantic.errors import ConfdanticError, format_key
from confdantic.keypath import KeyPath

ALLOWED_METADATA_KEYS = frozenset({"key", "env", "secret"})
M = TypeVar("M", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class FieldSpec:
    name: str
    key: str
    env: str | None
    env_disabled: bool
    is_secret: bool
    is_sequence: bool
    is_mapping: bool
    nested_models: tuple[type[BaseModel], ...]
    adapter: TypeAdapter[Any]
    entry_type: Any
    field_info: FieldInfo

    def get_comment(self, fmt: CommentFormat, add_choices: bool = True) -> str | None:
        return get_comment(self.field_info, fmt=fmt, add_choices=add_choices)


@dataclass(frozen=True, slots=True)
class KeyTarget:
    """What a config key path addresses within a model."""

    known: bool
    spec: FieldSpec | None = None
    depth: int = 0
    """Segments below `spec`: a mapping key or sequence index, and anything under it."""
    secret: bool = False
    forbidden: bool = False
    """An unknown key inside a model configured with `extra="forbid"`."""
    annotation: object = None


@dataclass(frozen=True, slots=True)
class ModelPlan(Generic[M]):
    model: type[M]
    fields: dict[str, FieldSpec]
    by_key: dict[str, FieldSpec]
    extra: str
    extra_type: object
    """The value type of keys an `extra="allow"` model does not declare."""

    def target(self, key: KeyPath) -> KeyTarget:
        if not key:
            return KeyTarget(known=False)

        return _key_target(self.model, key, None, 0, secret=False)

    def resolve(self, key: KeyPath) -> FieldSpec | None:
        """Return the field that owns a key, including keys below mapping and sequence fields."""
        return self.target(key).spec

    def known(self, key: KeyPath) -> bool:
        return self.target(key).known

    def is_secret_path(self, key: KeyPath) -> bool:
        return self.target(key).secret

    def contains_secret(self, value: Any, key: KeyPath = ()) -> bool:  # noqa: ANN401
        if key and self.is_secret_path(key):
            return True

        if is_secret_type(type(value)):
            return True

        if isinstance(value, BaseModel):
            plan = get_model_plan(type(value))
            return any(
                spec.is_secret or plan.contains_secret(getattr(value, spec.name), (spec.key,))
                for spec in plan.fields.values()
            )

        if isinstance(value, Mapping):
            return any(
                self.contains_secret(child, (*key, str(name))) for name, child in value.items()
            )

        if isinstance(value, (list, tuple, set, frozenset)):
            return any(
                self.contains_secret(child, (*key, str(index))) for index, child in enumerate(value)
            )

        return False

    def value_adapter(self, key: KeyPath) -> TypeAdapter[Any] | None:
        """Adapter for the value stored at a key, or None when the key has no fixed type."""
        target = self.target(key)
        if target.spec is None:
            return (
                _adapter(target.annotation)
                if target.known and target.annotation is not Any
                else None
            )

        if target.depth == 0:
            return target.spec.adapter

        return _adapter(target.annotation) if target.annotation is not Any else None

    def validate_document(self, document: Mapping[str, Any]) -> M:
        return self.model.model_validate(document, by_alias=True, by_name=True)

    def validate_value(self, key: KeyPath, value: object) -> object:
        adapter = self.value_adapter(key)
        return adapter.validate_python(value, by_alias=True, by_name=True) if adapter else value

    def keys(self) -> tuple[KeyPath, ...]:
        """Every declared key in declaration order."""
        return _declared_keys(self.model)

    def suggest(self, key: KeyPath) -> KeyPath | None:
        """Return the declared key closest to a likely typo, comparing dotted spellings."""
        target = format_key(key).casefold()
        limit = 1 + len(target) // 5
        best: KeyPath | None = None
        best_distance = limit + 1
        for candidate in self.keys():
            distance = _edit_distance(target, format_key(candidate).casefold())
            if distance < best_distance:
                best, best_distance = candidate, distance

        return best

    def unknown_keys(self, document: Mapping[str, Any]) -> list[KeyPath]:
        return _unknown_keys(self.model, document, ())

    def to_document_value_at(
        self, key: KeyPath, value: object, *, original: object = None
    ) -> object:
        target = self.target(key)
        return _document_value(target.annotation, value, original)

    def to_document(
        self, dump: Mapping[str, Any], *, model: BaseModel | None = None
    ) -> dict[str, Any]:
        """Rename field names in a `model_dump(mode="python")` result to config keys."""
        document: dict[str, Any] = {}
        for name, value in dump.items():
            spec = self.fields.get(name)
            if spec is None:
                document[name] = value
            else:
                document[spec.key] = _document_value(
                    spec.field_info.annotation,
                    value,
                    getattr(model, name) if model is not None else None,
                )

        return document

    def key_for_loc(self, loc: tuple[int | str, ...], *, prefix: KeyPath = ()) -> KeyPath:
        """Map a Pydantic error location (field names) to a config key path."""
        annotation = self.target(prefix).annotation if prefix else self.model
        return _location_key(annotation, loc, prefix)

    def default_document(self) -> dict[str, Any]:
        """Config-keyed defaults, including nested models whose own defaults validate."""
        return self._defaults(frozenset())

    def _defaults(self, active: frozenset[type[BaseModel]]) -> dict[str, Any]:
        if self.model in active:
            return {}

        active |= {self.model}
        document: dict[str, Any] = {}
        validated: dict[str, Any] = {}
        for spec in self.fields.values():
            info = spec.field_info
            if info.default_factory_takes_validated_data:
                continue

            if not info.is_required():
                value = info.get_default(call_default_factory=True, validated_data=validated)
            elif len(spec.nested_models) == 1 and not (spec.is_sequence or spec.is_mapping):
                child = get_model_plan(spec.nested_models[0])
                document[spec.key] = child._defaults(active)  # noqa: SLF001
                continue
            else:
                continue

            validated[spec.name] = value
            dumped = (
                value
                if info.annotation is Any or spec.entry_type is Any
                else spec.adapter.dump_python(value, mode="python", by_alias=False, warnings=False)
            )
            document[spec.key] = _document_value(spec.field_info.annotation, dumped, value)

        return document


def _key_target(
    annotation: object,
    segments: KeyPath,
    spec: FieldSpec | None,
    depth: int,
    secret: bool,
) -> KeyTarget:
    if not segments:
        return KeyTarget(known=True, spec=spec, depth=depth, secret=secret, annotation=annotation)

    inner = unwrap_optional(annotation)
    origin = get_origin(inner)
    if is_union_origin(origin):
        targets = [_key_target(arg, segments, spec, depth, secret) for arg in get_args(inner)]
        known = next((target for target in targets if target.known), None)
        return KeyTarget(
            known=known is not None,
            spec=known.spec if known else spec,
            depth=known.depth if known else depth,
            secret=any(target.secret for target in targets),
            forbidden=any(target.forbidden for target in targets),
            annotation=known.annotation if known else None,
        )

    if inner is Any:
        return KeyTarget(
            known=True, spec=spec, depth=depth + len(segments), secret=secret, annotation=Any
        )

    if isinstance(inner, type) and issubclass(inner, BaseModel):
        return _model_key_target(get_model_plan(inner), segments, secret)

    return _container_key_target(inner, segments, spec, depth, secret)


def _model_key_target(plan: ModelPlan, segments: KeyPath, secret: bool) -> KeyTarget:
    field = plan.by_key.get(segments[0])
    if field is None and plan.extra == "allow":
        return _key_target(plan.extra_type, segments[1:], None, 0, secret)

    if field is None:
        return KeyTarget(known=False, secret=secret, forbidden=plan.extra == "forbid")

    return _key_target(
        field.field_info.annotation, segments[1:], field, 0, secret or field.is_secret
    )


def _container_key_target(
    annotation: object,
    segments: KeyPath,
    spec: FieldSpec | None,
    depth: int,
    secret: bool,
) -> KeyTarget:
    origin = get_origin(annotation)
    if origin in MAPPING_ORIGINS:
        return _key_target(entry_type(annotation) or Any, segments[1:], spec, depth + 1, secret)

    if origin in SEQUENCE_ORIGINS and segments[0].isdigit():
        item = _sequence_type(annotation, int(segments[0]))
        if item is not None:
            return _key_target(item, segments[1:], spec, depth + 1, secret)

    return KeyTarget(known=False, spec=spec, depth=depth, secret=secret)


def _sequence_type(annotation: object, index: int) -> object:
    args = get_args(annotation)
    if not args:
        return Any

    if get_origin(annotation) is tuple and not (len(args) == 2 and args[1] is Ellipsis):
        return args[index] if index < len(args) else None

    return args[0] if args else Any


def _unknown_keys(annotation: object, value: object, prefix: KeyPath) -> list[KeyPath]:
    inner = unwrap_optional(annotation)
    origin = get_origin(inner)
    if is_union_origin(origin):
        variants = [set(_unknown_keys(arg, value, prefix)) for arg in get_args(inner)]
        return sorted(set.intersection(*variants))

    if isinstance(inner, type) and issubclass(inner, BaseModel) and isinstance(value, Mapping):
        return _unknown_model_keys(get_model_plan(inner), value, prefix)

    if origin in MAPPING_ORIGINS and isinstance(value, Mapping):
        return [
            key
            for name, item in value.items()
            for key in _unknown_keys(entry_type(inner), item, (*prefix, str(name)))
        ]

    if origin in SEQUENCE_ORIGINS and isinstance(value, (list, tuple)):
        return [
            key
            for index, item in enumerate(value)
            for key in _unknown_keys(_sequence_type(inner, index), item, (*prefix, str(index)))
        ]

    return []


def _unknown_model_keys(
    plan: ModelPlan, document: Mapping[str, Any], prefix: KeyPath
) -> list[KeyPath]:
    unknown: list[KeyPath] = []
    for name, value in document.items():
        path = (*prefix, name)
        if spec := plan.by_key.get(name):
            unknown.extend(_unknown_keys(spec.field_info.annotation, value, path))
        elif plan.extra != "allow":
            unknown.append(path)

    return unknown


def _document_value(annotation: object, value: object, original: object) -> object:
    if value is None:
        return None

    if isinstance(value, timedelta):
        # Pydantic's own text form, ISO 8601, since formats such as TOML have no duration type.
        return to_jsonable_python(value)

    inner = unwrap_optional(annotation)
    origin = get_origin(inner)
    if isinstance(original, BaseModel) and isinstance(value, Mapping):
        return get_model_plan(type(original)).to_document(value, model=original)

    if is_union_origin(origin):
        return _union_document_value(get_args(inner), value, original)

    if isinstance(inner, type) and issubclass(inner, BaseModel) and isinstance(value, Mapping):
        return get_model_plan(inner).to_document(value)

    return _container_document_value(inner, value, original)


def _container_document_value(annotation: object, value: object, original: object) -> object:
    origin = get_origin(annotation)
    if origin in MAPPING_ORIGINS and isinstance(value, Mapping):
        return {
            key: _document_value(
                entry_type(annotation),
                item,
                original.get(key) if isinstance(original, Mapping) else None,
            )
            for key, item in value.items()
        }

    if origin in SEQUENCE_ORIGINS and isinstance(value, (list, tuple)):
        return [
            _document_value(
                _sequence_type(annotation, index),
                item,
                original[index]
                if isinstance(original, (list, tuple)) and index < len(original)
                else None,
            )
            for index, item in enumerate(value)
        ]

    return value


def _union_document_value(
    annotations: tuple[object, ...], value: object, original: object
) -> object:
    models = [arg for arg in annotations if isinstance(arg, type) and issubclass(arg, BaseModel)]
    if len(models) == len(annotations) and isinstance(value, Mapping):
        return _union_document([get_model_plan(model) for model in models], value)

    for annotation in annotations:
        origin = get_origin(unwrap_optional(annotation))
        if (origin in MAPPING_ORIGINS and isinstance(value, Mapping)) or (
            origin in SEQUENCE_ORIGINS and isinstance(value, (list, tuple))
        ):
            return _document_value(annotation, value, original)

    return value


def _location_key(annotation: object, loc: tuple[int | str, ...], prefix: KeyPath) -> KeyPath:
    if not loc:
        return prefix

    inner = unwrap_optional(annotation)
    origin = get_origin(inner)
    if is_union_origin(origin):
        return _union_location_key(get_args(inner), loc, prefix)

    if isinstance(inner, type) and issubclass(inner, BaseModel):
        plan = get_model_plan(inner)
        if inner.model_config.get("loc_by_alias", True):
            field = plan.by_key.get(str(loc[0])) or plan.fields.get(str(loc[0]))
        else:
            field = plan.fields.get(str(loc[0])) or plan.by_key.get(str(loc[0]))
        if field is not None:
            path = (*prefix, field.key)
            return _location_key(field.field_info.annotation, loc[1:], path)

    return _container_location_key(inner, loc, prefix)


def _union_location_key(
    annotations: tuple[object, ...], loc: tuple[int | str, ...], prefix: KeyPath
) -> KeyPath:
    models = [arg for arg in annotations if isinstance(arg, type) and issubclass(arg, BaseModel)]
    tagged = [
        model
        for model in models
        if model.__name__ == loc[0]
        or any(
            loc[0] in (literal_choices(spec.field_info.annotation) or ())
            for spec in get_model_plan(model).fields.values()
        )
    ]
    # Pydantic puts the matched tag, or else the variant's label, before the inner location.
    candidates = tagged or annotations
    tail = loc[1:]
    paths = {_location_key(candidate, tail, prefix) for candidate in candidates}
    return paths.pop() if len(paths) == 1 else prefix


def _container_location_key(
    annotation: object, loc: tuple[int | str, ...], prefix: KeyPath
) -> KeyPath:
    segment, *rest = loc
    path = (*prefix, str(segment))
    origin = get_origin(annotation)
    if origin in MAPPING_ORIGINS:
        return _location_key(entry_type(annotation), tuple(rest), path)

    if origin in SEQUENCE_ORIGINS and isinstance(segment, int):
        return _location_key(_sequence_type(annotation, segment), tuple(rest), path)

    return (*path, *(str(item) for item in rest))


def _union_document(plans: list[ModelPlan], item: Mapping[str, Any]) -> dict[str, Any]:
    names: dict[str, str] = {}
    for plan in plans:
        for name, spec in plan.fields.items():
            if name not in item:
                continue

            if name in names and names[name] != spec.key:
                raise ConfdanticError(
                    "A union value needs its model instance to select configuration keys"
                )

            names[name] = spec.key

    return {names.get(name, name): value for name, value in item.items()}


@cache
def _adapter(annotation: Any) -> TypeAdapter[Any]:  # noqa: ANN401
    return TypeAdapter(annotation)


@cache
def _declared_keys(model: type[BaseModel]) -> tuple[KeyPath, ...]:
    keys: list[KeyPath] = []

    def walk(plan: ModelPlan, prefix: KeyPath, active: frozenset[type[BaseModel]]) -> None:
        if plan.model in active:
            return

        active |= {plan.model}
        for spec in plan.fields.values():
            path = (*prefix, spec.key)
            keys.append(path)

            if not spec.is_mapping:
                for child in spec.nested_models:
                    walk(get_model_plan(child), path, active)

    walk(get_model_plan(model), (), frozenset())

    return tuple(dict.fromkeys(keys))


def _edit_distance(a: str, b: str) -> int:
    """Optimal string alignment distance, counting an adjacent transposition as one edit."""
    rows = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        rows[i][0] = i

    for j in range(len(b) + 1):
        rows[0][j] = j

    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            substitution = rows[i - 1][j - 1] + (a[i - 1] != b[j - 1])
            rows[i][j] = min(rows[i - 1][j] + 1, rows[i][j - 1] + 1, substitution)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                rows[i][j] = min(rows[i][j], rows[i - 2][j - 2] + 1)

    return rows[-1][-1]


_PLAN_CACHE: dict[type[BaseModel], ModelPlan[Any]] = {}


def get_model_plan(model_cls: type[M]) -> ModelPlan[M]:
    cached = _PLAN_CACHE.get(model_cls)
    if cached is not None:
        return cached

    model_cls.model_rebuild()

    plan = compile_model_plan(model_cls)
    _PLAN_CACHE[model_cls] = plan

    return plan


def compile_model_plan(model_cls: type[M]) -> ModelPlan[M]:
    fields: dict[str, FieldSpec] = {}
    by_key: dict[str, FieldSpec] = {}
    for name, info in model_cls.model_fields.items():
        spec = compile_field_spec(name, info, model_cls)
        if not spec.key or "." in spec.key:
            raise ConfdanticError(f"Invalid config key '{spec.key}' for field '{name}'")

        if spec.key in by_key:
            raise ConfdanticError(
                f"Fields '{by_key[spec.key].name}' and '{name}' share config key '{spec.key}'"
            )

        fields[name] = spec
        by_key[spec.key] = spec

    extra = model_cls.model_config.get("extra", "ignore")

    return ModelPlan(
        model_cls,
        fields,
        by_key,
        extra if isinstance(extra, str) else "ignore",
        _extra_type(model_cls),
    )


def _extra_type(model_cls: type[BaseModel]) -> object:
    """The value type of a `__pydantic_extra__: dict[str, T]` annotation, else Any."""
    for owner in model_cls.__mro__[:-1]:
        annotation = getattr(owner, "__annotations__", {}).get("__pydantic_extra__")
        if annotation is None:
            continue

        if isinstance(annotation, str):
            # Resolved the way Pydantic resolves it: in the declaring class's module.
            namespace = vars(sys.modules[owner.__module__])
            annotation = eval(annotation, namespace, dict(vars(owner)))  # noqa: S307

        return entry_type(annotation) or Any

    return Any


def compile_field_spec(name: str, field_info: FieldInfo, owner_model: type[BaseModel]) -> FieldSpec:
    extra = field_info.json_schema_extra
    raw = extra.get("confdantic") if isinstance(extra, dict) else None
    meta: dict[str, Any] = raw if isinstance(raw, dict) else {}
    for item in meta:
        if item not in ALLOWED_METADATA_KEYS:
            raise ConfdanticError(f"Unknown confdantic metadata key '{item}' on field '{name}'")

    key = meta.get("key")
    if key is not None and not isinstance(key, str):
        raise ConfdanticError(f"Field '{name}' key must be a string")

    if key is not None and field_info.validation_alias != key:
        raise ConfdanticError(f"Field '{name}' configuration key must match its validation alias")

    env = meta.get("env")
    if env is not None and env is not False and not (isinstance(env, str) and env):
        raise ConfdanticError(f"Field '{name}' env must be a variable name or False")

    annotation = field_info.annotation

    return FieldSpec(
        name=name,
        key=key if key is not None else name,
        env=env or None,
        env_disabled=env is False,
        is_secret=bool(meta.get("secret")) or is_secret_type(annotation),
        is_sequence=is_sequence_type(annotation),
        is_mapping=is_mapping_type(annotation),
        nested_models=extract_nested_models(annotation, owner_model),
        adapter=_field_adapter(field_info),
        entry_type=entry_type(annotation),
        field_info=field_info,
    )


def _field_adapter(field_info: FieldInfo) -> TypeAdapter[Any]:
    """Validate one field's value with its constraints.

    The copy drops the default: the adapter never supplies one, and Pydantic warns
    about default factories that take validated data.
    """
    if field_info.annotation is None:
        return TypeAdapter(Any)

    constraints = copy(field_info)
    constraints.default = PydanticUndefined
    constraints.default_factory = None
    constraints.alias = None
    constraints.validation_alias = None
    constraints.serialization_alias = None

    return TypeAdapter(Annotated[field_info.annotation, constraints])


__all__ = [
    "FieldSpec",
    "KeyTarget",
    "ModelPlan",
    "compile_field_spec",
    "compile_model_plan",
    "get_model_plan",
]
