import json
from pathlib import Path
from typing import Literal

import pytest
import toml
import tomlkit
import yaml
from pydantic import BaseModel, Field

from confdantic import Confdantic


class ExampleModel(Confdantic):
    name: str
    age: int
    hobbies: list[str] = Field(default_factory=list)
    address: str | None = None


@pytest.fixture
def temp_dir(tmp_path):
    return tmp_path


@pytest.fixture
def sample_data():
    return {
        "name": "John Doe",
        "age": 30,
        "hobbies": ["reading", "cycling"],
        "address": "123 Main St",
    }


def test_load_json(temp_dir, sample_data):
    filepath = temp_dir / "test.json"
    with open(filepath, "w") as f:
        json.dump(sample_data, f)

    model = ExampleModel.load(str(filepath))
    assert model.model_dump() == sample_data


def test_load_yaml(temp_dir, sample_data):
    filepath = temp_dir / "test.yaml"
    with open(filepath, "w") as f:
        yaml.dump(sample_data, f)

    model = ExampleModel.load(str(filepath))
    assert model.model_dump() == sample_data


def test_load_toml(temp_dir, sample_data):
    filepath = temp_dir / "test.toml"
    with open(filepath, "w") as f:
        toml.dump(sample_data, f)

    model = ExampleModel.load(str(filepath))
    assert model.model_dump() == sample_data


def test_load_nonexistent_file():
    with pytest.raises(FileNotFoundError):
        ExampleModel.load("nonexistent.json")


def test_load_unsupported_format(temp_dir):
    filepath = temp_dir / "test.txt"
    filepath.touch()
    with pytest.raises(ValueError):
        ExampleModel.load(str(filepath))


def test_save_json(temp_dir, sample_data):
    model = ExampleModel(**sample_data)
    filepath = temp_dir / "test.json"
    model.save(str(filepath))

    with open(filepath, "r") as f:
        loaded_data = json.load(f)
    assert loaded_data == sample_data


def test_save_yaml(temp_dir, sample_data):
    model = ExampleModel(**sample_data)
    filepath = temp_dir / "test.yaml"
    model.save(str(filepath))

    with open(filepath, "r") as f:
        loaded_data = yaml.safe_load(f)
    assert loaded_data == sample_data


def test_save_toml(temp_dir, sample_data):
    model = ExampleModel(**sample_data)
    filepath = temp_dir / "test.toml"
    model.save(str(filepath))

    with open(filepath, "r") as f:
        loaded_data = toml.load(f)
    assert loaded_data == sample_data


def test_save_existing_file_no_overwrite(temp_dir, sample_data):
    model = ExampleModel(**sample_data)
    filepath = temp_dir / "test.json"
    filepath.touch()

    with pytest.raises(FileExistsError):
        model.save(str(filepath), overwrite=False)


def test_save_unsupported_format(temp_dir, sample_data):
    model = ExampleModel(**sample_data)
    filepath = temp_dir / "test.txt"

    with pytest.raises(ValueError):
        model.save(str(filepath))


def test_save_yaml_with_comments(temp_dir, sample_data):
    name_description = "The person's name"
    age_description = "The person's age"

    class CommentedModel(Confdantic):
        name: str = Field(..., description=name_description)
        age: int = Field(..., description=age_description)

    model = CommentedModel(**sample_data)
    filepath = temp_dir / "test.yaml"
    model.save(str(filepath), comments=True)

    with open(filepath, "r") as f:
        content = f.read()

    assert name_description in content
    assert age_description in content


def test_save_toml_with_comments(temp_dir, sample_data):
    name_description = "The person's name"
    age_description = "The person's age"

    class CommentedModel(Confdantic):
        name: str = Field(..., description=name_description)
        age: int = Field(..., description=age_description)

    model = CommentedModel(**sample_data)
    filepath = temp_dir / "test.toml"
    model.save(str(filepath), comments=True)

    with open(filepath, "r") as f:
        content = f.read()

    assert name_description in content
    assert age_description in content


def test_save_toml_with_nested_comments_and_literals(temp_dir):
    class Nested(BaseModel):
        retries: int = Field(..., description="Number of retries")
        level: Literal["info", "debug"] = Field(..., description="Logging level")

    class CommentedTomlModel(Confdantic):
        title: str = Field(..., description="Title description")
        mode: Literal["dev", "prod"] = Field(..., description="Operating mode")
        nested: Nested = Field(..., description="Nested configuration")

    model = CommentedTomlModel(
        title="Demo",
        mode="dev",
        nested={"retries": 3, "level": "info"},
    )
    filepath = temp_dir / "commented.toml"
    model.save(str(filepath), comments=True)

    content = filepath.read_text()
    assert "# Title description" in content
    assert "Operating mode | choices: dev, prod" in content
    assert "[nested] # Nested configuration" in content
    assert "Logging level | choices: info, debug" in content


def test_save_toml_without_comments(temp_dir):
    class Nested(BaseModel):
        retries: int = Field(..., description="Number of retries")
        level: Literal["info", "debug"] = Field(..., description="Logging level")

    class PlainTomlModel(Confdantic):
        title: str = Field(..., description="Title description")
        mode: Literal["dev", "prod"] = Field(..., description="Operating mode")
        nested: Nested = Field(..., description="Nested configuration")

    model = PlainTomlModel(
        title="Demo",
        mode="dev",
        nested={"retries": 3, "level": "info"},
    )
    filepath = temp_dir / "plain.toml"
    model.save(str(filepath), comments=False)

    content = filepath.read_text()
    assert "#" not in content


def test_save_toml_with_arbitrary_type(temp_dir):
    class ArbitraryTomlModel(Confdantic):
        base_path: Path

    base_path = temp_dir / "config"
    model = ArbitraryTomlModel(base_path=base_path)
    filepath = temp_dir / "arbitrary.toml"

    with pytest.raises(tomlkit.exceptions.ConvertError):
        model.save(str(filepath), comments=False)

    model.save(str(filepath), comments=False, serialize_unsupported=True)
    content = filepath.read_text()
    parsed = tomlkit.loads(content)
    assert Path(parsed["base_path"]) == base_path


def test_save_toml_with_nested_boolean_comments(temp_dir):
    class Nested(BaseModel):
        enabled: bool = Field(..., description="Enable feature toggle")
        retries: int = Field(..., description="Number of retries")

    class BooleanCommentModel(Confdantic):
        nested: Nested = Field(..., description="Nested settings")

    model = BooleanCommentModel(nested={"enabled": True, "retries": 5})
    filepath = temp_dir / "bool_comments.toml"

    model.save(str(filepath), comments=True)
    content = filepath.read_text()
    assert "Enable feature toggle" in content


def test_nested_model_save_load(temp_dir):
    class Address(BaseModel):
        street: str
        city: str

    class Person(Confdantic):
        name: str
        address: Address

    data = {"name": "Jane Doe", "address": {"street": "456 Elm St", "city": "Anytown"}}

    model = Person(**data)
    filepath = temp_dir / "nested.yaml"
    model.save(str(filepath))

    loaded_model = Person.load(str(filepath))
    assert loaded_model.model_dump() == data
    assert isinstance(loaded_model.address, Address)


def test_save_toml_with_none_values(temp_dir):
    model = ExampleModel(name="John Doe", age=30, hobbies=["reading"], address=None)
    filepath = temp_dir / "test_none.toml"
    model.save(str(filepath))

    with open(filepath) as f:
        content = f.read()
    assert "address" not in content

    loaded_model = ExampleModel.load(str(filepath))
    assert loaded_model.name == "John Doe"
    assert loaded_model.age == 30
    assert loaded_model.hobbies == ["reading"]
    assert loaded_model.address is None


def test_save_toml_round_trip_with_none_values(temp_dir):
    model = ExampleModel(name="Jane Doe", age=25, address=None)
    filepath = temp_dir / "round_trip.toml"
    model.save(str(filepath))

    loaded_model = ExampleModel.load(str(filepath))
    assert loaded_model.model_dump() == {
        "name": "Jane Doe",
        "age": 25,
        "hobbies": [],
        "address": None,
    }


def test_load_jsonc(temp_dir, sample_data):
    filepath = temp_dir / "test.jsonc"
    content = """{
        // This is a comment
        "name": "John Doe",
        "age": 30, /* inline comment */
        "hobbies": ["reading", "cycling"],
        "address": "123 Main St"
    }"""
    filepath.write_text(content)

    model = ExampleModel.load(str(filepath))
    assert model.model_dump() == sample_data


def test_load_json5(temp_dir, sample_data):
    filepath = temp_dir / "test.json5"
    content = """{
        "name": "John Doe",
        "age": 30,
        "hobbies": ["reading", "cycling"],
        "address": "123 Main St"
    }"""
    filepath.write_text(content)

    model = ExampleModel.load(str(filepath))
    assert model.model_dump() == sample_data


def test_save_jsonc_with_comments(temp_dir, sample_data):
    name_description = "The person's name"
    age_description = "The person's age"

    class CommentedModel(Confdantic):
        name: str = Field(..., description=name_description)
        age: int = Field(..., description=age_description)

    model = CommentedModel(**sample_data)
    filepath = temp_dir / "test.jsonc"
    model.save(str(filepath), comments=True)

    content = filepath.read_text()
    assert name_description in content
    assert age_description in content
    assert "//" in content


def test_save_jsonc_without_comments(temp_dir, sample_data):
    class CommentedModel(Confdantic):
        name: str = Field(..., description="A description")
        age: int = Field(..., description="Another description")

    model = CommentedModel(**sample_data)
    filepath = temp_dir / "test.jsonc"
    model.save(str(filepath), comments=False)

    content = filepath.read_text()
    assert "//" not in content
    json.loads(content)


def test_save_jsonc_comment_position_above(temp_dir, sample_data):
    class CommentedModel(Confdantic):
        name: str = Field(..., description="Name field")
        age: int = Field(..., description="Age field")

    model = CommentedModel(**sample_data)
    filepath = temp_dir / "test.jsonc"
    model.save(str(filepath), comments=True, comment_position="above_field")

    content = filepath.read_text()
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if "// Name field" in line:
            assert '"name"' in lines[i + 1]
            break


def test_save_jsonc_with_nested_comments(temp_dir):
    class Nested(BaseModel):
        enabled: bool = Field(..., description="Enable feature")
        retries: int = Field(..., description="Number of retries")

    class NestedModel(Confdantic):
        title: str = Field(..., description="Title")
        config: Nested = Field(..., description="Configuration")

    model = NestedModel(title="Test", config={"enabled": True, "retries": 3})
    filepath = temp_dir / "nested.jsonc"
    model.save(str(filepath), comments=True)

    content = filepath.read_text()
    assert "// Title" in content
    assert "// Configuration" in content
    assert "// Enable feature" in content
    assert "// Number of retries" in content


def test_save_jsonc_with_literal_choices(temp_dir):
    class ChoicesModel(Confdantic):
        mode: Literal["dev", "prod"] = Field(..., description="Operating mode")

    model = ChoicesModel(mode="dev")
    filepath = temp_dir / "choices.jsonc"
    model.save(str(filepath), comments=True)

    content = filepath.read_text()
    assert "Operating mode | choices: dev, prod" in content


def test_jsonc_round_trip(temp_dir):
    class RoundTripModel(Confdantic):
        name: str = Field(..., description="Name")
        value: int

    original = RoundTripModel(name="test", value=42)
    filepath = temp_dir / "roundtrip.jsonc"
    original.save(str(filepath), comments=True)

    loaded = RoundTripModel.load(str(filepath))
    assert loaded.name == original.name
    assert loaded.value == original.value


def test_save_json5_extension(temp_dir, sample_data):
    class SimpleModel(Confdantic):
        name: str = Field(..., description="Name field")
        age: int

    model = SimpleModel(**sample_data)
    filepath = temp_dir / "test.json5"
    model.save(str(filepath), comments=True)

    content = filepath.read_text()
    assert "// Name field" in content

    loaded = SimpleModel.load(str(filepath))
    assert loaded.name == sample_data["name"]
    assert loaded.age == sample_data["age"]
