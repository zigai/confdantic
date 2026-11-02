from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from confdantic.formats.base import SupportsLocate
from confdantic.keypath import KeyPath
from confdantic.plan import ModelPlan
from confdantic.provenance import REDACTED, Origin, SourceKind


def collect_origins(
    document: Mapping[str, Any],
    source: SourceKind,
    path: str | None,
    plan: ModelPlan,
    *,
    locator: SupportsLocate | None = None,
    decoded: Mapping[str, Any] | None = None,
) -> dict[KeyPath, Origin]:
    """Record an origin for every leaf of a config-keyed layer, and for each list item.

    `locator` and `decoded` supply file line numbers and literal text when the layer
    came from a file; otherwise the rendered Python value is used.
    """
    origins: dict[KeyPath, Origin] = {}

    def visit(value: Any, key: KeyPath) -> None:  # noqa: ANN401
        if isinstance(value, Mapping):
            for name, child in value.items():
                visit(child, (*key, str(name)))

            return

        secret = plan.contains_secret(value, key)
        location = locator.locate(decoded, key) if locator and decoded else None
        line = location.line if location is not None else None
        raw = location.raw_value if location is not None else str(value)
        origins[key] = Origin(key, source, path, line, REDACTED if secret else raw)

        if isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                visit(item, (*key, str(index)))

    visit(document, ())

    return origins


def merge_overlay(
    accumulated: dict[str, Any],
    overlay: Mapping[str, Any],
    origins: dict[KeyPath, Origin],
    layer_origins: Mapping[KeyPath, Origin],
    plan: ModelPlan,
    prefix: KeyPath = (),
) -> None:
    """Overlay one layer: mappings merge, every other value (lists included) replaces."""
    for name, value in overlay.items():
        key = (*prefix, name)
        if key in layer_origins:
            origins[key] = layer_origins[key]

        if isinstance(value, Mapping):
            if not isinstance(accumulated.get(name), dict):
                accumulated[name] = {}
                origins.pop(key, None)
                _drop_descendants(key, origins)

            merge_overlay(accumulated[name], value, origins, layer_origins, plan, key)

            continue

        accumulated[name] = list(value) if isinstance(value, list) else value
        _drop_descendants(key, origins)

        if isinstance(value, list):
            origins.update(
                {
                    item: origin
                    for item, origin in layer_origins.items()
                    if _is_descendant(item, key)
                }
            )


def _drop_descendants(key: KeyPath, origins: dict[KeyPath, Origin]) -> None:
    for descendant in [name for name in origins if _is_descendant(name, key)]:
        del origins[descendant]


def _is_descendant(key: KeyPath, ancestor: KeyPath) -> bool:
    return len(key) > len(ancestor) and key[: len(ancestor)] == ancestor


__all__ = ["collect_origins", "merge_overlay"]
