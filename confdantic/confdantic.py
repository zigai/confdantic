import json
import os
from pathlib import PurePath
from typing import Any, Literal

import toml
import tomlkit
from objinspect.typing import get_literal_choices, is_direct_literal
from pydantic import BaseModel, ConfigDict
from pydantic.fields import FieldInfo
from pydantic_core import to_jsonable_python
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from tomlkit.items import Table


def sanitize_comment(comment: str) -> str:
    sanitized = comment.replace("\n", " ").replace("\r", " ")
    return sanitized


def file_ext(filepath: str):
    ext = os.path.splitext(filepath)[1].lower()
    if ext.startswith("."):
        ext = ext[1:]
    return ext


def get_comment(
    field: FieldInfo, format: Literal["json", "yaml", "toml"], add_choices: bool = True
) -> str | None:
    """
    Generate a comment string for a Pydantic field, including description and choices if they exist.
    This function creates a comment string based on the field's description and, if the field
    is a Literal type, its possible choices.
    """
    if is_direct_literal(field.annotation) and add_choices:
        choices = list(get_literal_choices(field.annotation))
        if not choices:
            choices_str = None
        else:
            formated_choices = []
            for i in choices:
                if i is None:
                    if format in ["json", "yaml"]:
                        formated_choices.append("null")
                    else:
                        formated_choices.append("''")
                else:
                    formated_choices.append(str(i))
            choices_str = "choices: " + ", ".join(formated_choices)
    else:
        choices_str = None
    if not field.description:
        return choices_str
    comment = sanitize_comment(field.description)
    if choices_str:
        comment += " | " + choices_str
    return comment


class Confdantic(BaseModel):
    """
    A class for serializing and deserializing Pydantic models to and from files.
    This class extends Pydantic's BaseModel to provide enhanced file I/O capabilities,
    supporting TOML, YAML, and JSON formats with optional comment preservation.
    """

    model_config = ConfigDict(
        validate_assignment=True,
        json_encoders={PurePath: lambda path: path.as_posix()},
    )

    @staticmethod
    def _json_fallback(value: Any) -> Any:
        if isinstance(value, PurePath):
            return value.as_posix()
        return str(value)

    def to_commented_yaml(self) -> CommentedMap | CommentedSeq:
        """Converts the Confdantic instance to a CommentedMap or CommentedSeq for YAML serialization."""
        data = self.model_dump()
        return self._to_commented_yaml(data)

    def _to_commented_yaml(self, obj: Any) -> CommentedMap | CommentedSeq | Any:
        if isinstance(obj, dict):
            cm = CommentedMap()
            for key, value in obj.items():
                cm[key] = self._to_commented_yaml(value)

                if issubclass(self.__class__, BaseModel) and key in self.__class__.model_fields:
                    field = self.__class__.model_fields[key]
                    comment = get_comment(field, format="yaml")
                    if comment:
                        cm.yaml_add_eol_comment(comment, key)
            return cm
        elif isinstance(obj, list):
            cs = CommentedSeq()
            for item in obj:
                cs.append(self._to_commented_yaml(item))
            return cs
        else:
            return obj

    @classmethod
    def load(cls, filepath: str):
        """
        Load a Pydantic model from a file.

        This method automatically detects the file format based on the file extension
        and uses the appropriate loading method.

        Args:
            filepath (str): The path to the file containing the serialized model.

        Returns:
            Confen: An instance of the Confen class with the loaded data.

        Raises:
            FileNotFoundError: If the specified file does not exist.
            ValueError: If the file extension is not recognized.
        """
        if not os.path.exists(filepath):
            raise FileNotFoundError(filepath)
        ext = file_ext(filepath)
        match ext:
            case "toml" | "tml":
                return cls.load_toml(filepath)
            case "yaml" | "yml":
                return cls.load_yaml(filepath)
            case "json":
                return cls.load_json(filepath)
            case _:
                raise ValueError(f"Unknown file extension: {ext}")

    def save(
        self,
        filepath: str,
        overwrite: bool = True,
        comments: bool = True,
        serialize_unsupported: bool = False,
    ):
        """
        Save the configuration to a file.

        This method automatically detects the file format based on the file extension
        and uses the appropriate saving method.

        Args:
            filepath (str): The path where the configuration file will be saved.
            overwrite (bool, optional): Whether to overwrite the file if it already exists. Defaults to True.
            comments (bool, optional): Whether to include comments in the saved file. Defaults to True.
            serialize_unsupported (bool, optional): Whether to serialize unsupported types as strings. Defaults to False.

        Raises:
            FileExistsError: If the file already exists and overwrite is False.
            ValueError: If the file extension is not recognized.
        """
        if os.path.exists(filepath) and not overwrite:
            raise FileExistsError(filepath)

        ext = file_ext(filepath)
        match ext:
            case "toml" | "tml":
                return self.save_toml(
                    filepath,
                    overwrite=overwrite,
                    comments=comments,
                    serialize_unsupported=serialize_unsupported,
                )
            case "yaml" | "yml":
                return self.save_yaml(
                    filepath=filepath,
                    overwrite=overwrite,
                    serialize_unsupported=serialize_unsupported,
                )
            case "json":
                return self.save_json(
                    filepath, overwrite=overwrite, serialize_unsupported=serialize_unsupported
                )
            case _:
                raise ValueError(f"Unknown file extension: {ext}")

    @classmethod
    def load_yaml(cls, filepath: str):
        if not os.path.exists(filepath):
            raise FileNotFoundError(filepath)

        yaml = YAML()
        with open(filepath) as f:
            data = yaml.load(f)

        return cls.model_validate(data)

    @classmethod
    def load_toml(cls, filepath: str):
        with open(filepath) as f:
            return cls.model_validate(toml.load(f))

    @classmethod
    def load_json(cls, filepath: str, encoding: str = "utf-8"):
        with open(filepath, encoding=encoding) as f:
            return cls.model_validate(json.load(f))

    def save_toml(
        self,
        filepath: str,
        overwrite: bool = True,
        comments: bool = True,
        serialize_unsupported: bool = False,
    ) -> None:
        if os.path.exists(filepath) and not overwrite:
            raise FileExistsError(filepath)
        data = self.model_dump()
        if serialize_unsupported:
            data = to_jsonable_python(data, fallback=self._json_fallback)
        toml_string = tomlkit.dumps(data)
        toml_doc = tomlkit.loads(toml_string)

        if not comments:
            with open(filepath, "w") as f:
                tomlkit.dump(toml_doc, f)
                return

        for name, field in self.__class__.model_fields.items():
            item = toml_doc.item(name)
            comment = get_comment(field, format="toml")
            if comment:
                item.comment(comment)

            try:
                is_base_model = is_base_model = isinstance(field.annotation, type) and issubclass(
                    field.annotation, BaseModel
                )
            except TypeError:
                is_base_model = False

            if is_base_model and field.annotation:
                subfield = field.annotation
                for subfname, f in subfield.model_fields.items():
                    table: Table = toml_doc[name]
                    try:
                        subitem = table.item(subfname)
                    except KeyError:
                        subitem = None
                    if subitem is None:
                        continue
                    comment = get_comment(f, format="toml")
                    if comment and hasattr(subitem, "comment"):
                        subitem.comment(comment)

        with open(filepath, "w") as f:
            tomlkit.dump(toml_doc, f)

    def save_json(
        self,
        filepath: str,
        overwrite: bool = True,
        serialize_unsupported: bool = False,
    ) -> None:
        if os.path.exists(filepath) and not overwrite:
            raise FileExistsError(filepath)
        data = self.model_dump()
        if serialize_unsupported:
            data = to_jsonable_python(data, fallback=self._json_fallback)
        with open(filepath, "w") as f:
            json.dump(data, f, indent=4, default=str)

    def save_yaml(
        self,
        filepath: str,
        overwrite: bool = True,
        comments: bool = True,
        serialize_unsupported: bool = False,
    ) -> None:
        if os.path.exists(filepath) and not overwrite:
            raise FileExistsError(filepath)

        yaml = YAML()
        yaml.indent(mapping=2, sequence=4, offset=2)
        yaml.preserve_quotes = True

        data = self.model_dump()
        if serialize_unsupported:
            data = to_jsonable_python(data, fallback=self._json_fallback)
        if comments:
            data = self._to_commented_yaml(data)
        with open(filepath, "w") as f:
            yaml.dump(data, f)


__all__ = ["Confdantic"]
