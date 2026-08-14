from __future__ import annotations

from typing import Literal

from pydantic.fields import FieldInfo

from confdantic.annotations import literal_choices

CommentFormat = Literal["json", "yaml", "toml"]


def sanitize_comment(comment: str) -> str:
    return comment.replace("\n", " ").replace("\r", " ")


def get_comment(
    field: FieldInfo,
    fmt: CommentFormat,
    add_choices: bool = True,
) -> str | None:
    """Build a field comment from its description and documented literal choices."""
    choices = literal_choices(field.annotation) if add_choices else None
    formatted_choices: list[str] = []
    if choices:
        for choice in choices:
            if choice is None:
                if fmt in ("json", "yaml"):
                    formatted_choices.append("null")
            else:
                formatted_choices.append(str(choice))

    choices_comment = "choices: " + ", ".join(formatted_choices) if formatted_choices else None
    if not field.description:
        return choices_comment

    comment = sanitize_comment(field.description)
    if choices_comment:
        comment += " | " + choices_comment

    return comment


__all__ = ["CommentFormat", "get_comment", "sanitize_comment"]
