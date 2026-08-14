from typing import Literal

from pydantic import BaseModel, Field

from confdantic.comments import get_comment, sanitize_comment


class CommentedModel(BaseModel):
    mode: Literal["dev", "prod"] | None = Field(
        description="Execution\nmode",
    )
    undocumented: Literal["a", "b"]


def test_sanitize_comment_flattens_line_breaks():
    assert sanitize_comment("first\nsecond\rthird") == "first second third"


def test_get_comment_combines_description_and_choices():
    field = CommentedModel.model_fields["mode"]

    assert get_comment(field, "json") == "Execution mode | choices: dev, prod, null"
    assert get_comment(field, "toml") == "Execution mode | choices: dev, prod"


def test_get_comment_can_return_choices_without_description():
    field = CommentedModel.model_fields["undocumented"]

    assert get_comment(field, "yaml") == "choices: a, b"


def test_get_comment_can_omit_choices():
    field = CommentedModel.model_fields["mode"]

    assert get_comment(field, "json", add_choices=False) == "Execution mode"
