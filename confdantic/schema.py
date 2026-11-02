from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaValue
from pydantic_core import core_schema

from confdantic.plan import get_model_plan


class ConfigSchema(GenerateJsonSchema):
    def tagged_union_schema(self, schema: core_schema.TaggedUnionSchema) -> JsonSchemaValue:
        result = super().tagged_union_schema(schema)
        if "oneOf" in result:
            result["anyOf"] = result.pop("oneOf")

        result.pop("discriminator", None)
        return result

    def model_schema(self, schema: core_schema.ModelSchema) -> JsonSchemaValue:
        result = super().model_schema(schema)
        model = schema["cls"]
        if not issubclass(model, BaseModel):
            return result

        plan = get_model_plan(model)
        properties = result.get("properties", {})
        renamed = {}
        for name, node in properties.items():
            spec = plan.fields.get(name)
            if spec is None:
                renamed[name] = node
                continue

            if isinstance(node, dict):
                node.pop("confdantic", None)
                if (
                    spec.is_secret
                    or _contains_secret_model(spec.nested_models, frozenset())
                    or plan.contains_secret(spec.field_info.default, (spec.key,))
                ):
                    node.pop("default", None)

            renamed[spec.key] = node

        result["properties"] = renamed
        result.pop("required", None)
        return result


def schema(
    model_type: type[BaseModel],
    *,
    schema_id: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """Generate a JSON Schema for a sparse configuration layer, using config keys."""
    model_type.model_rebuild()
    result = model_type.model_json_schema(
        by_alias=False, mode="validation", schema_generator=ConfigSchema
    )
    if "$schema" not in get_model_plan(model_type).by_key:
        if "$ref" in result:
            definition = result["$defs"][result.pop("$ref").split("/")[-1]]
            result = {**definition, **result}

        result["properties"] = {**result.get("properties", {}), "$schema": {"type": "string"}}

    if schema_id is not None:
        result["$id"] = schema_id

    if title is not None:
        result["title"] = title

    return result


def _contains_secret_model(
    models: tuple[type[BaseModel], ...], active: frozenset[type[BaseModel]]
) -> bool:
    for model in models:
        if model in active:
            continue

        plan = get_model_plan(model)
        if any(
            spec.is_secret or _contains_secret_model(spec.nested_models, active | {model})
            for spec in plan.fields.values()
        ):
            return True

    return False


__all__ = ["schema"]
