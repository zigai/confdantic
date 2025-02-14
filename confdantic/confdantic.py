import json
import os
from typing import Any, Literal

import toml
import tomlkit
from objinspect.typing import get_literal_choices, is_direct_literal
from pydantic import BaseModel, ConfigDict
from pydantic.fields import FieldInfo
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

    model_config = ConfigDict(validate_assignment=True)

    def to_commented_yaml(self) -> CommentedMap | CommentedSeq:
        """
        Converts the Confdantic instance to a CommentedMap or CommentedSeq for YAML serialization.
        """
        return self._to_commented_yaml(self)

    def _to_commented_yaml(self, obj: Any) -> CommentedMap | CommentedSeq | Any:
        if issubclass(obj.__class__, BaseModel):
            cm = CommentedMap()
            for field_name, field in obj.model_fields.items():
                value = getattr(obj, field_name)
                cm[field_name] = self._to_commented_yaml(value)

                comment = get_comment(field, format="yaml")
                if comment:
                    cm.yaml_add_eol_comment(comment, field_name)

            return cm
        elif isinstance(obj, list):
            cs = CommentedSeq()
            for item in obj:
                cs.append(self._to_commented_yaml(item))
            return cs
        elif isinstance(obj, dict):
            cm = CommentedMap()
            for key, value in obj.items():
                cm[key] = self._to_commented_yaml(value)
            return cm
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

    def save(self, filepath: str, overwrite: bool = True, comments: bool = True):
        """
        Save the configuration to a file.

        This method automatically detects the file format based on the file extension
        and uses the appropriate saving method.

        Args:
            filepath (str): The path where the configuration file will be saved.
            overwrite (bool, optional): Whether to overwrite the file if it already exists. Defaults to True.
            comments (bool, optional): Whether to include comments in the saved file. Defaults to True.

        Raises:
            FileExistsError: If the file already exists and overwrite is False.
            ValueError: If the file extension is not recognized.
        """

        if os.path.exists(filepath) and not overwrite:
            raise FileExistsError(filepath)

        ext = file_ext(filepath)
        match ext:
            case "toml" | "tml":
                return self.save_toml(filepath, overwrite=overwrite, comments=comments)
            case "yaml" | "yml":
                return self.save_yaml(filepath=filepath, overwrite=overwrite)
            case "json":
                return self.save_json(filepath, overwrite=overwrite)
            case _:
                raise ValueError(f"Unknown file extension: {ext}")

    @classmethod
    def load_yaml(cls, filepath: str):
        if not os.path.exists(filepath):
            raise FileNotFoundError(filepath)

        yaml = YAML()
        with open(filepath, "r") as f:
            data = yaml.load(f)

        return cls.model_validate(data)

    @classmethod
    def load_toml(cls, filepath: str):
        with open(filepath, "r") as f:
            return cls.model_validate(toml.load(f))

    @classmethod
    def load_json(cls, filepath: str):
        with open(filepath, "r") as f:
            return cls.model_validate(json.load(f))

    def save_toml(self, filepath: str, overwrite: bool = True, comments: bool = True) -> None:
        if os.path.exists(filepath) and not overwrite:
            raise FileExistsError(filepath)
        data = self.model_dump()
        toml_string = tomlkit.dumps(data)
        toml_doc = tomlkit.loads(toml_string)

        if not comments:
            with open(filepath, "w") as f:
                tomlkit.dump(toml_doc, f)
                return

        for name, field in self.model_fields.items():
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
                    subitem = table.get(subfname)
                    comment = get_comment(f, format="toml")
                    if comment:
                        subitem.comment(comment)

        with open(filepath, "w") as f:
            tomlkit.dump(toml_doc, f)

    def save_json(self, filepath: str, overwrite: bool = True) -> None:
        if os.path.exists(filepath) and not overwrite:
            raise FileExistsError(filepath)
        data = self.model_dump()
        with open(filepath, "w") as f:
            json.dump(data, f)

    def save_yaml(self, filepath: str, overwrite: bool = True, comments: bool = True) -> None:
        if os.path.exists(filepath) and not overwrite:
            raise FileExistsError(filepath)

        yaml = YAML()
        yaml.indent(mapping=2, sequence=4, offset=2)
        yaml.preserve_quotes = True

        data = self.model_dump()
        if comments:
            data = self.to_commented_yaml()
        else:
            data = self.model_dump()
        with open(filepath, "w") as f:
            yaml.dump(data, f)


__all__ = ["Confdantic"]
