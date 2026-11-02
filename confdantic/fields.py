from __future__ import annotations

from typing import Any, Literal

from pydantic import Field
from pydantic_core import PydanticUndefined


def config_field(
    default: Any = PydanticUndefined,  # noqa: ANN401 - default can be any type
    *,
    key: str | None = None,
    env: str | Literal[False] | None = None,
    secret: bool = False,
    description: str | None = None,
    **field_options: Any,  # noqa: ANN401
) -> Any:  # noqa: ANN401 - aligns with pydantic.Field typing to allow field assignment
    """Create a Pydantic field; `key` sets its native validation alias and file key.

    `env` names the field's environment variable; `False` stops environment binding.
    """
    if key is not None:
        for option in ("validation_alias", "alias"):
            if option in field_options and field_options[option] != key:
                raise ValueError(f"{option} must match the configuration key")

        field_options["validation_alias"] = key

    confdantic_meta: dict[str, object] = {"key": key, "env": env, "secret": secret}

    extra = field_options.get("json_schema_extra")
    if callable(extra):
        raise TypeError("Cannot merge confdantic metadata into callable json_schema_extra")

    if isinstance(extra, dict):
        if "confdantic" in extra:
            raise ValueError(
                "Preexisting 'confdantic' namespace in json_schema_extra is not allowed"
            )

        merged_extra = dict(extra)
        merged_extra["confdantic"] = confdantic_meta
    elif extra is None:
        merged_extra = {"confdantic": confdantic_meta}
    else:
        raise TypeError(f"Unsupported json_schema_extra type: {type(extra).__name__}")

    field_options["json_schema_extra"] = merged_extra

    if description is not None:
        field_options["description"] = description

    if default is PydanticUndefined or ("default_factory" in field_options and default is ...):
        return Field(**field_options)

    return Field(default, **field_options)


__all__ = ["config_field"]
