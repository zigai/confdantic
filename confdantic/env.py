from __future__ import annotations

import csv
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from confdantic.annotations import unwrap_optional
from confdantic.keypath import KeyPath
from confdantic.plan import FieldSpec, ModelPlan, get_model_plan
from confdantic.provenance import REDACTED, Origin, SourceKind


@dataclass(frozen=True, slots=True)
class EnvVar:
    """One environment variable a model reads."""

    key: KeyPath
    name: str
    """The variable to document; `names` lists every spelling tried, first match wins."""
    names: tuple[str, ...]
    secret: bool


def env_vars(model: type[BaseModel], prefix: str = "") -> tuple[EnvVar, ...]:
    """List the variables `load(model, env_prefix=prefix)` reads, in declaration order."""
    normalized = _normalize_prefix(prefix)
    result: list[EnvVar] = []
    plan = get_model_plan(model)
    for key, spec in _bindable_fields(plan):
        names = _candidate_names(spec, key, normalized)
        if names:
            result.append(
                EnvVar(
                    key,
                    names[0] if spec.env is not None else names[-1],
                    names,
                    plan.is_secret_path(key),
                )
            )

    return tuple(result)


def bind_environment(
    plan: ModelPlan, environment: Mapping[str, str], prefix: str
) -> tuple[dict[str, Any], dict[KeyPath, Origin]]:
    """Read bound variables into a config-keyed layer and its origins."""
    normalized = _normalize_prefix(prefix)
    values: dict[str, Any] = {}
    origins: dict[KeyPath, Origin] = {}

    for key, spec in _bindable_fields(plan):
        name = next((n for n in _candidate_names(spec, key, normalized) if n in environment), None)
        if name is None:
            continue

        raw = environment[name]
        secret = plan.is_secret_path(key)
        origin = Origin(key, SourceKind.ENV, name, None, REDACTED if secret else raw)
        if spec.is_sequence:
            value = next(csv.reader([raw.strip()], skipinitialspace=True)) if raw.strip() else []
        else:
            value = raw.strip() if unwrap_optional(spec.field_info.annotation) is bool else raw
        *parents, leaf = key
        cursor = values
        for segment in parents:
            cursor = cursor.setdefault(segment, {})

        cursor[leaf] = value
        origins[key] = origin

        if isinstance(value, list):
            for index, item in enumerate(value):
                item_key = (*key, str(index))
                item_raw = REDACTED if secret else item
                origins[item_key] = Origin(item_key, SourceKind.ENV, name, None, item_raw)

    return values, origins


def _normalize_prefix(prefix: str) -> str:
    normalized = prefix.strip().upper()
    if normalized and not normalized.endswith("_"):
        normalized += "_"

    return normalized


def _candidate_names(spec: FieldSpec, key: KeyPath, prefix: str) -> tuple[str, ...]:
    names: list[str] = []
    if spec.env is not None:
        if prefix:
            names.append(prefix + spec.env)

        names.append(spec.env)

    if not prefix or not _env_decodable(spec):
        return tuple(names)

    segments = [segment.upper() for segment in key]
    nested = f"{prefix}{'__'.join(segments)}"
    flat = f"{prefix}{'_'.join(segments)}"
    return tuple(dict.fromkeys((*names, nested, flat)))


def _env_decodable(spec: FieldSpec) -> bool:
    """Whether one string can hold the field: mappings and lists of models cannot."""
    return not spec.is_mapping and not (spec.is_sequence and spec.nested_models)


def _bindable_fields(
    plan: ModelPlan, prefix: KeyPath = (), active: frozenset[type[BaseModel]] = frozenset()
) -> Iterator[tuple[KeyPath, FieldSpec]]:
    """Yield `(key, field)` pairs, descending into single nested models."""
    if plan.model in active:
        return

    active |= {plan.model}
    for spec in plan.fields.values():
        if spec.env_disabled:
            continue

        key = (*prefix, spec.key)
        is_model = len(spec.nested_models) == 1 and not (spec.is_sequence or spec.is_mapping)
        if is_model:
            yield from _bindable_fields(get_model_plan(spec.nested_models[0]), key, active)

        if not is_model or spec.env is not None:
            yield key, spec


__all__ = ["EnvVar", "bind_environment", "env_vars"]
