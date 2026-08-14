from pathlib import Path
from typing import Literal

import pytest
import tomlkit
from pydantic import BaseModel, Field

from confdantic import Confdantic
from tests.models import ExampleModel


def test_load_toml(tmp_path, sample_data):
    filepath = tmp_path / "test.toml"
    with filepath.open("w") as file:
        tomlkit.dump(sample_data, file)

    model = ExampleModel.load(str(filepath))
    assert model.model_dump() == sample_data


def test_save_toml(tmp_path, sample_data):
    model = ExampleModel(**sample_data)
    filepath = tmp_path / "test.toml"
    model.save(str(filepath))

    with filepath.open() as file:
        loaded_data = tomlkit.load(file)
    assert loaded_data == sample_data


def test_save_toml_with_comments(tmp_path, sample_data):
    name_description = "The person's name"
    age_description = "The person's age"

    class CommentedModel(Confdantic):
        name: str = Field(..., description=name_description)
        age: int = Field(..., description=age_description)

    model = CommentedModel(**sample_data)
    filepath = tmp_path / "test.toml"
    model.save(str(filepath), comments=True)

    with filepath.open() as file:
        content = file.read()

    assert name_description in content
    assert age_description in content


def test_save_toml_with_nested_comments_and_literals(tmp_path):
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
    filepath = tmp_path / "commented.toml"
    model.save(str(filepath), comments=True)

    content = filepath.read_text()
    assert "# Title description" in content
    assert "Operating mode | choices: dev, prod" in content
    assert "[nested] # Nested configuration" in content
    assert "Logging level | choices: info, debug" in content


def test_save_toml_without_comments(tmp_path):
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
    filepath = tmp_path / "plain.toml"
    model.save(str(filepath), comments=False)

    content = filepath.read_text()
    assert "#" not in content


def test_save_toml_with_arbitrary_type(tmp_path):
    class ArbitraryTomlModel(Confdantic):
        base_path: Path

    base_path = tmp_path / "config"
    model = ArbitraryTomlModel(base_path=base_path)
    filepath = tmp_path / "arbitrary.toml"

    with pytest.raises(tomlkit.exceptions.ConvertError):
        model.save(str(filepath), comments=False)

    model.save(str(filepath), comments=False, serialize_unsupported=True)
    content = filepath.read_text()
    parsed = tomlkit.loads(content)
    assert Path(parsed["base_path"]) == base_path


def test_save_toml_with_nested_boolean_comments(tmp_path):
    class Nested(BaseModel):
        enabled: bool = Field(..., description="Enable feature toggle")
        retries: int = Field(..., description="Number of retries")

    class BooleanCommentModel(Confdantic):
        nested: Nested = Field(..., description="Nested settings")

    model = BooleanCommentModel(nested={"enabled": True, "retries": 5})
    filepath = tmp_path / "bool_comments.toml"

    model.save(str(filepath), comments=True)
    content = filepath.read_text()
    assert "Enable feature toggle" in content


def test_save_toml_with_none_values(tmp_path):
    model = ExampleModel(name="John Doe", age=30, hobbies=["reading"], address=None)
    filepath = tmp_path / "test_none.toml"
    model.save(str(filepath))

    with filepath.open() as file:
        content = file.read()
    assert "address" not in content

    loaded_model = ExampleModel.load(str(filepath))
    assert loaded_model.name == "John Doe"
    assert loaded_model.age == 30
    assert loaded_model.hobbies == ["reading"]
    assert loaded_model.address is None


def test_save_toml_round_trip_with_none_values(tmp_path):
    model = ExampleModel(name="Jane Doe", age=25, address=None)
    filepath = tmp_path / "round_trip.toml"
    model.save(str(filepath))

    loaded_model = ExampleModel.load(str(filepath))
    assert loaded_model.model_dump() == {
        "name": "Jane Doe",
        "age": 25,
        "hobbies": [],
        "address": None,
    }


def test_save_toml_with_deeply_nested_comments(tmp_path):
    class Level3(BaseModel):
        deep: str = Field("d", description="Deep field desc")

    class Level2(BaseModel):
        mid: str = Field("m", description="Mid field desc")
        l3: Level3 = Field(default_factory=Level3, description="Level3 desc")

    class Level1(Confdantic):
        top: str = Field("t", description="Top field desc")
        l2: Level2 = Field(default_factory=Level2, description="Level2 desc")

    model = Level1()
    filepath = tmp_path / "deep.toml"
    model.save(str(filepath), comments=True)

    content = filepath.read_text()
    assert 'top = "t" # Top field desc' in content
    assert 'mid = "m" # Mid field desc' in content
    assert 'deep = "d" # Deep field desc' in content


def test_save_toml_with_optional_nested_comments(tmp_path):
    class Nested(BaseModel):
        retries: int = Field(3, description="Number of retries")

    class Outer(Confdantic):
        nested: Nested | None = Field(None, description="Optional nested config")

    model = Outer(nested={"retries": 5})
    filepath = tmp_path / "optional.toml"
    model.save(str(filepath), comments=True)

    content = filepath.read_text()
    assert "[nested] # Optional nested config" in content
    assert "retries = 5 # Number of retries" in content


def test_save_toml_with_array_of_tables_comments(tmp_path):
    class Db(BaseModel):
        host: str = Field("localhost", description="DB host")
        port: int = Field(5432, description="DB port")

    class Outer(Confdantic):
        dbs: list[Db] = Field(default_factory=lambda: [Db(), Db(port=1)], description="DBs desc")

    model = Outer()
    filepath = tmp_path / "aot.toml"
    model.save(str(filepath), comments=True)

    content = filepath.read_text()
    assert "[[dbs]] # DBs desc" in content
    assert content.count("# DB host") == 2
    assert content.count("# DB port") == 2

    loaded = Outer.load(str(filepath))
    assert loaded.model_dump() == model.model_dump()


def test_save_toml_literal_none_choice_omitted(tmp_path):
    class NoneLit(Confdantic):
        mode: Literal["a"] | None = Field("a", description="Mode")

    model = NoneLit()
    filepath = tmp_path / "none_lit.toml"
    model.save(str(filepath), comments=True)
    content = filepath.read_text()
    assert "choices: a" in content
    assert "''" not in content
