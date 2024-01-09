from pydantic import BaseModel, Field
import tomlkit
from tomlkit.items import Table
from pydantic.fields import FieldInfo
from objinspect.util import get_literal_choices, is_literal
import os
import toml
import json
import yaml


def sanitize_comment(comment: str) -> str:
    sanitized = comment.replace("\n", " ").replace("\r", " ")
    return sanitized


def get_comment(field: FieldInfo, add_choices: bool = True):
    if is_literal(field.annotation) and add_choices:
        choices = list(get_literal_choices(field.annotation))
        if not choices:
            choices_str = None
        else:
            choices_str = "choices: " + ", ".join(choices)
    else:
        choices_str = None
    if not field.description:
        return choices_str
    comment = sanitize_comment(field.description)
    if choices_str:
        comment += " | " + choices_str
    return comment


class Confen(BaseModel):
    class Config:
        validate_assignment = True

    @classmethod
    def load(cls, filepath: str):
        if not os.path.exists(filepath):
            raise FileNotFoundError(filepath)
        _, ext = os.path.splitext(filepath)
        match ext:
            case "toml" | "tml":
                return cls.load_toml(filepath)
            case _:
                ...

    @classmethod
    def load_toml(cls, filepath: str):
        with open(filepath, "r") as f:
            return cls.model_validate(toml.load(f))

    @classmethod
    def load_json(cls, filepath: str):
        with open(filepath, "r") as f:
            return cls.model_validate(json.load(f))

    @classmethod
    def load_yaml(cls, filepath: str):
        with open(filepath, "r") as f:
            return cls.model_validate(yaml.safe_load(f))

    def save(self, filepath: str, overwrite: bool = True, comments: bool = True):
        if os.path.exists(filepath) and not overwrite:
            raise FileExistsError(filepath)
        _, ext = os.path.splitext(filepath)
        match ext:
            case "toml" | "tml":
                return self.save_toml(filepath, overwrite=overwrite, comments=comments)
            case "yaml" | "yml":
                return self.save_yaml(filepath=filepath, overwrite=overwrite)
            case "json":
                return self.save_json(filepath, overwrite=overwrite)
            case _:
                raise ValueError(f"Unknown file extension: {ext}")

    def save_toml(self, filepath: str, overwrite: bool = True, comments: bool = True):
        if os.path.exists(filepath) and not overwrite:
            raise FileExistsError(filepath)
        data = self.model_dump()
        toml_string = tomlkit.dumps(data)
        toml_document = tomlkit.loads(toml_string)

        if not comments:
            with open(filepath, "w") as f:
                tomlkit.dump(toml_document, f)
                return

        for name, field in self.model_fields.items():
            item = toml_document.item(name)
            comment = get_comment(field)
            if comment:
                item.comment(comment)

            try:
                is_base_model = issubclass(field.annotation, BaseModel)
            except TypeError:
                is_base_model = False

            if is_base_model:
                subfield = field.annotation
                for subfname, f in subfield.model_fields.items():
                    table: Table = toml_document[name]
                    subitem = table.get(subfname)
                    comment = get_comment(f)
                    if comment:
                        subitem.comment(comment)

        with open(filepath, "w") as f:
            tomlkit.dump(toml_document, f)

    def save_json(self, filepath: str, overwrite: bool = True):
        if os.path.exists(filepath) and not overwrite:
            raise FileExistsError(filepath)
        data = self.model_dump()
        with open(filepath, "w") as f:
            json.dump(data, f)

    def save_yaml(self, filepath: str, overwrite: bool = True):
        if os.path.exists(filepath) and not overwrite:
            raise FileExistsError(filepath)
        data = self.model_dump()
        with open(filepath, "w") as f:
            yaml.safe_dump(data, f)
