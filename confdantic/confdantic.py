import json
import re
from pathlib import Path, PurePath
from typing import Literal

import jsonc
import toml
import tomlkit
from objinspect.typing import get_literal_choices, is_direct_literal
from pydantic import BaseModel, ConfigDict
from pydantic.fields import FieldInfo
from pydantic_core import to_jsonable_python
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from tomlkit.items import Table
from typing_extensions import Self

CommentPosition = Literal["end_of_line", "above_field"]


def sanitize_comment(comment: str) -> str:
    sanitized = comment.replace("\n", " ").replace("\r", " ")
    return sanitized


def file_ext(filepath: str) -> str:
    return Path(filepath).suffix.lower().removeprefix(".")


def _base_model_annotation(annotation: object) -> type[BaseModel] | None:
    try:
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return annotation
    except TypeError:
        return None

    return None


def get_comment(
    field: FieldInfo, fmt: Literal["json", "yaml", "toml"], add_choices: bool = True
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
                    if fmt in ["json", "yaml"]:
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


def _resolve_json_comment(
    *,
    key: str,
    depth: int,
    current_parent: str | None,
    model_fields: dict[str, FieldInfo],
    nested_fields: dict[str, dict[str, FieldInfo]],
) -> tuple[str | None, str | None]:
    if depth == 1:
        field = model_fields.get(key)
        if field is None:
            return None, None

        annotation = _base_model_annotation(field.annotation)
        next_parent = key if annotation is not None else None
        return get_comment(field, fmt="json"), next_parent

    if depth == 2 and current_parent:
        nested = nested_fields.get(current_parent)
        if nested is None:
            return None, None

        field = nested.get(key)
        if field is None:
            return None, None

        return get_comment(field, fmt="json"), current_parent

    return None, None


def insert_jsonc_comments(
    json_string: str,
    model_fields: dict[str, FieldInfo],
    nested_fields: dict[str, dict[str, FieldInfo]],
    position: CommentPosition = "end_of_line",
) -> str:
    """
    Insert JSONC-style comments into a pretty-printed JSON string.

    Args:
        json_string: Pretty-printed JSON string from json.dumps(indent=4).
        model_fields: The model_fields dict from the Pydantic model.
        nested_fields: Mapping of field names to their nested model_fields (for BaseModel fields).
        position: Where to place comments - "end_of_line" or "above_field".

    Returns:
        JSON string with // comments inserted.
    """
    lines = json_string.split("\n")
    result_lines: list[str] = []
    key_pattern = re.compile(r'^(\s*)"([^"]+)"\s*:')
    depth = 0
    current_parent: str | None = None

    for line in lines:
        open_braces = line.count("{")
        close_braces = line.count("}")

        match = key_pattern.match(line)
        if match:
            indentation = match.group(1)
            key = match.group(2)
            comment, next_parent = _resolve_json_comment(
                key=key,
                depth=depth,
                current_parent=current_parent,
                model_fields=model_fields,
                nested_fields=nested_fields,
            )
            if next_parent is not None:
                current_parent = next_parent

            if comment:
                if position == "end_of_line":
                    line_stripped = line.rstrip()
                    result_lines.append(f"{line_stripped} // {comment}")
                else:
                    result_lines.append(f"{indentation}// {comment}")
                    result_lines.append(line)
                depth += open_braces - close_braces

                continue

        depth += open_braces - close_braces
        if depth <= 1:
            current_parent = None

        result_lines.append(line)

    return "\n".join(result_lines)


def _build_nested_field_map(
    model_fields: dict[str, FieldInfo],
) -> dict[str, dict[str, FieldInfo]]:
    """Build a mapping of field names to their nested model_fields for BaseModel fields."""
    nested: dict[str, dict[str, FieldInfo]] = {}
    for name, field in model_fields.items():
        annotation = _base_model_annotation(field.annotation)
        if annotation is not None:
            nested[name] = annotation.model_fields

    return nested


def _add_nested_toml_comments(field: FieldInfo, table: Table) -> None:
    annotation = _base_model_annotation(field.annotation)
    if annotation is None:
        return

    for subfield_name, subfield in annotation.model_fields.items():
        try:
            subitem = table.item(subfield_name)
        except KeyError:
            continue

        comment = get_comment(subfield, fmt="toml")
        if comment and hasattr(subitem, "comment"):
            subitem.comment(comment)


def _write_toml_document(filepath: Path, toml_doc: tomlkit.TOMLDocument) -> None:
    with filepath.open("w") as file:
        tomlkit.dump(toml_doc, file)


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
    def _json_fallback(value: object) -> str:
        if isinstance(value, PurePath):
            return value.as_posix()

        return str(value)

    def to_commented_yaml(self) -> CommentedMap | CommentedSeq:
        """Converts the Confdantic instance to a CommentedMap or CommentedSeq for YAML serialization."""
        data = self.model_dump()
        return self._to_commented_yaml(data)

    def _to_commented_yaml(self, obj: object) -> object:
        if isinstance(obj, dict):
            cm = CommentedMap()
            for key, value in obj.items():
                cm[key] = self._to_commented_yaml(value)

                if issubclass(self.__class__, BaseModel) and key in self.__class__.model_fields:
                    field = self.__class__.model_fields[key]
                    comment = get_comment(field, fmt="yaml")
                    if comment:
                        cm.yaml_add_eol_comment(comment, key)

            return cm

        if isinstance(obj, list):
            cs = CommentedSeq()
            for item in obj:
                cs.append(self._to_commented_yaml(item))

            return cs

        return obj

    @classmethod
    def load(cls, filepath: str) -> Self:
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
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(filepath)
        ext = file_ext(filepath)
        match ext:
            case "toml" | "tml":
                return cls.load_toml(filepath)
            case "yaml" | "yml":
                return cls.load_yaml(filepath)
            case "json":
                return cls.load_json(filepath)
            case "jsonc" | "json5":
                return cls.load_jsonc(filepath)
            case _:
                raise ValueError(f"Unknown file extension: {ext}")

    def save(
        self,
        filepath: str,
        overwrite: bool = True,
        comments: bool = True,
        serialize_unsupported: bool = False,
        comment_position: CommentPosition = "end_of_line",
    ) -> None:
        """
        Save the configuration to a file.

        This method automatically detects the file format based on the file extension
        and uses the appropriate saving method.

        Args:
            filepath (str): The path where the configuration file will be saved.
            overwrite (bool, optional): Whether to overwrite the file if it already exists. Defaults to True.
            comments (bool, optional): Whether to include comments in the saved file. Defaults to True.
            serialize_unsupported (bool, optional): Whether to serialize unsupported types as strings. Defaults to False.
            comment_position (CommentPosition, optional): Where to place comments in JSONC files. Defaults to "end_of_line".

        Raises:
            FileExistsError: If the file already exists and overwrite is False.
            ValueError: If the file extension is not recognized.
        """
        path = Path(filepath)
        if path.exists() and not overwrite:
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
                    comments=comments,
                    serialize_unsupported=serialize_unsupported,
                )
            case "json":
                return self.save_json(
                    filepath, overwrite=overwrite, serialize_unsupported=serialize_unsupported
                )
            case "jsonc" | "json5":
                return self.save_jsonc(
                    filepath,
                    overwrite=overwrite,
                    comments=comments,
                    serialize_unsupported=serialize_unsupported,
                    comment_position=comment_position,
                )
            case _:
                raise ValueError(f"Unknown file extension: {ext}")

    @classmethod
    def load_yaml(cls, filepath: str) -> Self:
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(filepath)

        yaml = YAML()
        with path.open() as file:
            data = yaml.load(file)

        return cls.model_validate(data)

    @classmethod
    def load_toml(cls, filepath: str) -> Self:
        with Path(filepath).open() as file:
            return cls.model_validate(toml.load(file))

    @classmethod
    def load_json(cls, filepath: str, encoding: str = "utf-8") -> Self:
        with Path(filepath).open(encoding=encoding) as file:
            return cls.model_validate(json.load(file))

    @classmethod
    def load_jsonc(cls, filepath: str, encoding: str = "utf-8") -> Self:
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(filepath)

        with path.open(encoding=encoding) as file:
            data = jsonc.load(file)
        return cls.model_validate(data)

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
        data = self.model_dump(exclude_none=True)
        if serialize_unsupported:
            data = to_jsonable_python(data, fallback=self._json_fallback)
        toml_string = tomlkit.dumps(data)
        toml_doc = tomlkit.loads(toml_string)

        if not comments:
            _write_toml_document(path, toml_doc)
            return

        for name, field in self.__class__.model_fields.items():
            try:
                item = toml_doc.item(name)
            except KeyError:
                continue

            comment = get_comment(field, fmt="toml")
            if comment:
                item.comment(comment)

            _add_nested_toml_comments(field, toml_doc[name])

        _write_toml_document(path, toml_doc)

    def save_json(
        self,
        filepath: str,
        overwrite: bool = True,
        serialize_unsupported: bool = False,
    ) -> None:
        path = Path(filepath)
        if path.exists() and not overwrite:
            raise FileExistsError(filepath)
        data = self.model_dump()
        if serialize_unsupported:
            data = to_jsonable_python(data, fallback=self._json_fallback)

        with path.open("w") as file:
            json.dump(data, file, indent=4, default=str)

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

        yaml = YAML()
        yaml.indent(mapping=2, sequence=4, offset=2)

        yaml.preserve_quotes = True

        data = self.model_dump()
        if serialize_unsupported:
            data = to_jsonable_python(data, fallback=self._json_fallback)

        if comments:
            data = self._to_commented_yaml(data)

        with path.open("w") as file:
            yaml.dump(data, file)

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

        data = self.model_dump()
        if serialize_unsupported:
            data = to_jsonable_python(data, fallback=self._json_fallback)

        json_string = json.dumps(data, indent=indent, default=str)

        if comments:
            nested_fields = _build_nested_field_map(self.__class__.model_fields)
            json_string = insert_jsonc_comments(
                json_string=json_string,
                model_fields=self.__class__.model_fields,
                nested_fields=nested_fields,
                position=comment_position,
            )

        with path.open("w") as file:
            file.write(json_string)


__all__ = ["Confdantic"]
