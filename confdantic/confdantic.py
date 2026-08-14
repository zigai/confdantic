from __future__ import annotations

from pathlib import Path, PurePath
from typing import Self

from pydantic import BaseModel, ConfigDict
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from confdantic.comments import get_comment, sanitize_comment  # noqa: F401 - compatibility exports
from confdantic.formats import json as json_format
from confdantic.formats import jsonc as jsonc_format
from confdantic.formats import toml as toml_format
from confdantic.formats import yaml as yaml_format
from confdantic.formats.jsonc import (  # noqa: F401 - compatibility export
    CommentPosition,
    insert_jsonc_comments,
)
from confdantic.values import ConfigValue, stringify_unsupported


def file_ext(filepath: str) -> str:
    """Return the normalized suffix retained for compatibility with earlier releases."""
    return Path(filepath).suffix.lower().removeprefix(".")


class Confdantic(BaseModel):
    """A Pydantic model with commented JSON, TOML, and YAML file serialization."""

    model_config = ConfigDict(
        validate_assignment=True,
        json_encoders={PurePath: lambda path: path.as_posix()},
    )

    def to_commented_yaml(self) -> CommentedMap | CommentedSeq:
        """Convert this model to a comment-aware YAML value."""
        return yaml_format.build_commented_value(
            self.model_dump(),
            self.__class__.model_fields,
            self.__class__,
        )

    @classmethod
    def load(cls, filepath: str, encoding: str = "utf-8-sig") -> Self:
        """Load and validate a model using the format identified by the file extension."""
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(filepath)

        extension = path.suffix.lower().removeprefix(".")
        match extension:
            case "toml" | "tml":
                return cls.load_toml(filepath)
            case "yaml" | "yml":
                return cls.load_yaml(filepath)
            case "json":
                return cls.load_json(filepath, encoding=encoding)
            case "jsonc" | "json5":
                return cls.load_jsonc(filepath, encoding=encoding)
            case _:
                raise ValueError(f"Unknown file extension: {extension}")

    def save(
        self,
        filepath: str,
        overwrite: bool = True,
        comments: bool = True,
        serialize_unsupported: bool = False,
        comment_position: CommentPosition = "end_of_line",
        indent: int = 4,
    ) -> None:
        """Save this model using the format identified by the file extension."""
        path = Path(filepath)
        if path.exists() and not overwrite:
            raise FileExistsError(filepath)

        extension = path.suffix.lower().removeprefix(".")
        match extension:
            case "toml" | "tml":
                self.save_toml(
                    filepath,
                    overwrite=overwrite,
                    comments=comments,
                    serialize_unsupported=serialize_unsupported,
                )
            case "yaml" | "yml":
                self.save_yaml(
                    filepath=filepath,
                    overwrite=overwrite,
                    comments=comments,
                    serialize_unsupported=serialize_unsupported,
                )
            case "json":
                self.save_json(
                    filepath,
                    overwrite=overwrite,
                    serialize_unsupported=serialize_unsupported,
                    indent=indent,
                )
            case "jsonc" | "json5":
                self.save_jsonc(
                    filepath,
                    overwrite=overwrite,
                    comments=comments,
                    serialize_unsupported=serialize_unsupported,
                    comment_position=comment_position,
                    indent=indent,
                )
            case _:
                raise ValueError(f"Unknown file extension: {extension}")

    @classmethod
    def load_yaml(cls, filepath: str) -> Self:
        return cls.model_validate(yaml_format.load(Path(filepath)))

    @classmethod
    def load_toml(cls, filepath: str) -> Self:
        return cls.model_validate(toml_format.load(Path(filepath)))

    @classmethod
    def load_json(cls, filepath: str, encoding: str = "utf-8-sig") -> Self:
        return cls.model_validate(json_format.load(Path(filepath), encoding))

    @classmethod
    def load_jsonc(cls, filepath: str, encoding: str = "utf-8-sig") -> Self:
        return cls.model_validate(jsonc_format.load(Path(filepath), encoding))

    def save_toml(
        self,
        filepath: str,
        overwrite: bool = True,
        comments: bool = True,
        serialize_unsupported: bool = False,
    ) -> None:
        path = Path(filepath)
        if path.exists() and not overwrite:
            raise FileExistsError(filepath)

        content = toml_format.render(
            self.model_dump(exclude_none=True),
            self.__class__,
            comments,
            serialize_unsupported,
        )
        path.write_text(content, encoding="utf-8")

    def save_json(
        self,
        filepath: str,
        overwrite: bool = True,
        serialize_unsupported: bool = False,
        indent: int = 4,
    ) -> None:
        path = Path(filepath)
        if path.exists() and not overwrite:
            raise FileExistsError(filepath)

        content = json_format.render(self.model_dump(), serialize_unsupported, indent)
        path.write_text(content, encoding="utf-8")

    def save_yaml(
        self,
        filepath: str,
        overwrite: bool = True,
        comments: bool = True,
        serialize_unsupported: bool = False,
    ) -> None:
        path = Path(filepath)
        if path.exists() and not overwrite:
            raise FileExistsError(filepath)

        content = yaml_format.render(
            self.model_dump(),
            self.__class__,
            comments,
            serialize_unsupported,
        )
        path.write_text(content, encoding="utf-8")

    def save_jsonc(
        self,
        filepath: str,
        overwrite: bool = True,
        comments: bool = True,
        serialize_unsupported: bool = False,
        comment_position: CommentPosition = "end_of_line",
        indent: int = 4,
    ) -> None:
        path = Path(filepath)
        if path.exists() and not overwrite:
            raise FileExistsError(filepath)

        content = jsonc_format.render(
            self.model_dump(),
            self.__class__,
            comments,
            serialize_unsupported,
            comment_position,
            indent,
        )
        path.write_text(content, encoding="utf-8")

    @staticmethod
    def _json_fallback(value: ConfigValue) -> str:
        """Retain the earlier unsupported-value fallback for subclasses using it directly."""
        return stringify_unsupported(value)


__all__ = ["Confdantic"]
