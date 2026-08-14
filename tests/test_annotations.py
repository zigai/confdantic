from typing import Annotated, Literal

from pydantic import BaseModel

from confdantic.annotations import literal_choices, nested_model_fields, unwrapped_model


class NestedModel(BaseModel):
    value: str


def test_unwrapped_model_resolves_supported_containers():
    assert unwrapped_model(NestedModel) is NestedModel
    assert unwrapped_model(NestedModel | None) is NestedModel
    assert unwrapped_model(Annotated[NestedModel, "metadata"]) is NestedModel
    assert unwrapped_model(list[NestedModel]) is NestedModel
    assert unwrapped_model(tuple[NestedModel, ...]) is NestedModel


def test_unwrapped_model_rejects_ambiguous_unions():
    class OtherModel(BaseModel):
        value: int

    assert unwrapped_model(NestedModel | OtherModel) is None


def test_unwrapped_model_resolves_a_string_self_reference():
    assert unwrapped_model(list["NestedModel"], NestedModel) is NestedModel


def test_nested_model_fields_returns_resolved_fields():
    assert nested_model_fields(list[NestedModel]) == NestedModel.model_fields


def test_literal_choices_supports_optional_and_composed_literals():
    annotation = Literal["a"] | Literal["b"] | None  # noqa: PYI030

    assert literal_choices(annotation) == ("a", "b", None)


def test_literal_choices_rejects_mixed_unions():
    assert literal_choices(Literal["a"] | str) is None
