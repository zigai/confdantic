from __future__ import annotations

from pathlib import Path, PurePath
from typing import TYPE_CHECKING, Any, Self

from pydantic import BaseModel, ConfigDict
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from confdantic.formats import json as json_format
from confdantic.formats import jsonc as jsonc_format
from confdantic.formats import toml as toml_format
from confdantic.formats import yaml as yaml_format
from confdantic.formats.base import RenderOptions
from confdantic.loader import load as load_layers
from confdantic.persistence import atomic_write_bytes, builtin_writer, encode_model
from confdantic.persistence import init as init_file
from confdantic.persistence import save as save_file
from confdantic.plan import get_model_plan

if TYPE_CHECKING:
    from confdantic.formats.jsonc import CommentPosition


class Confdantic(BaseModel):
    """A Pydantic model with commented JSON, TOML, and YAML file serialization."""

    model_config = ConfigDict(
        validate_assignment=True,
        json_encoders={PurePath: lambda path: path.as_posix()},
    )

    def to_commented_yaml(self) -> CommentedMap | CommentedSeq:
        """Convert this model to a comment-aware YAML value."""
        plan = get_model_plan(type(self))
        document = plan.to_document(self.model_dump(mode="python"), model=self)

        return yaml_format.commented_value(document, plan)

    @classmethod
    def load(cls, filepath: Path | str | None = None, **options: Any) -> Self:  # noqa: ANN401
        """Load through `confdantic.load`, taking `formats` from the file suffix when omitted."""
        if filepath is not None and "formats" not in options:
            extension = Path(filepath).suffix.lower()
            if extension:
                options["formats"] = (extension,)

        return load_layers(cls, filepath, **options)

    def save(
        self,
        filepath: Path | str,
        overwrite: bool = True,
        comments: bool = True,
        serialize_unsupported: bool = False,
        comment_position: CommentPosition = "end_of_line",
        indent: int = 4,
    ) -> None:
        """Save this model using the format identified by the file extension."""
        save_file(
            self,
            filepath,
            overwrite=overwrite,
            comments=comments,
            serialize_unsupported=serialize_unsupported,
            comment_position=comment_position,
            indent=indent,
        )

    @classmethod
    def load_yaml(cls, filepath: Path | str) -> Self:
        return load_layers(cls, filepath, formats=(yaml_format.YAML(),))

    @classmethod
    def load_toml(cls, filepath: Path | str) -> Self:
        return load_layers(cls, filepath, formats=(toml_format.TOML(),))

    @classmethod
    def load_json(cls, filepath: Path | str, encoding: str = "utf-8-sig") -> Self:
        return load_layers(cls, filepath, formats=(json_format.JSON(encoding),))

    @classmethod
    def load_jsonc(cls, filepath: Path | str, encoding: str = "utf-8-sig") -> Self:
        return load_layers(cls, filepath, formats=(jsonc_format.JSONC(encoding),))

    def save_toml(
        self,
        filepath: Path | str,
        overwrite: bool = True,
        comments: bool = True,
        serialize_unsupported: bool = False,
    ) -> None:
        options = RenderOptions(comments=comments, serialize_unsupported=serialize_unsupported)
        self._write("toml", filepath, overwrite, options)

    def save_json(
        self,
        filepath: Path | str,
        overwrite: bool = True,
        serialize_unsupported: bool = False,
        indent: int = 4,
    ) -> None:
        options = RenderOptions(serialize_unsupported=serialize_unsupported, indent=indent)
        self._write("json", filepath, overwrite, options)

    def save_yaml(
        self,
        filepath: Path | str,
        overwrite: bool = True,
        comments: bool = True,
        serialize_unsupported: bool = False,
    ) -> None:
        options = RenderOptions(comments=comments, serialize_unsupported=serialize_unsupported)
        self._write("yaml", filepath, overwrite, options)

    def save_jsonc(
        self,
        filepath: Path | str,
        overwrite: bool = True,
        comments: bool = True,
        serialize_unsupported: bool = False,
        comment_position: CommentPosition = "end_of_line",
        indent: int = 4,
    ) -> None:
        options = RenderOptions(comments, serialize_unsupported, comment_position, indent)
        self._write("jsonc", filepath, overwrite, options)

    @classmethod
    def init(
        cls,
        filepath: Path | str,
        *,
        overwrite: bool = False,
        schema_url: str | None = None,
        comments: bool = True,
        mode: int | None = None,
    ) -> None:
        """Generate and atomically save an initial configuration template for this model."""
        init_file(
            cls,
            filepath,
            overwrite=overwrite,
            schema_url=schema_url,
            comments=comments,
            mode=mode,
        )

    def _write(
        self, format_name: str, filepath: Path | str, overwrite: bool, options: RenderOptions
    ) -> None:
        path = Path(filepath)
        if path.exists() and not overwrite:
            raise FileExistsError(filepath)

        encoded = encode_model(self, builtin_writer(format_name), options)
        atomic_write_bytes(path, encoded, overwrite=overwrite)


__all__ = ["Confdantic"]
