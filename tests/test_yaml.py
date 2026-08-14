import yaml
from pydantic import BaseModel, Field

from confdantic import Confdantic
from tests.models import ExampleModel


def test_load_yaml(tmp_path, sample_data):
    filepath = tmp_path / "test.yaml"
    with filepath.open("w") as file:
        yaml.dump(sample_data, file)

    model = ExampleModel.load(str(filepath))
    assert model.model_dump() == sample_data


def test_save_yaml(tmp_path, sample_data):
    model = ExampleModel(**sample_data)
    filepath = tmp_path / "test.yaml"
    model.save(str(filepath))

    with filepath.open() as file:
        loaded_data = yaml.safe_load(file)
    assert loaded_data == sample_data


def test_save_yaml_with_comments(tmp_path, sample_data):
    name_description = "The person's name"
    age_description = "The person's age"

    class CommentedModel(Confdantic):
        name: str = Field(..., description=name_description)
        age: int = Field(..., description=age_description)

    model = CommentedModel(**sample_data)
    filepath = tmp_path / "test.yaml"
    model.save(str(filepath), comments=True)

    with filepath.open() as file:
        content = file.read()

    assert name_description in content
    assert age_description in content


def test_save_yaml_with_nested_comments(tmp_path):
    class Nested(BaseModel):
        host: str = Field(
            "localhost", description="The hostname or IP address of the database server"
        )
        port: int = Field(
            5432, description="The port number on which the database server is listening"
        )

    class Outer(Confdantic):
        debug: bool = Field(default=False, description="Enable debug mode")
        database: Nested = Field(default_factory=Nested, description="Database configuration")

    model = Outer()
    filepath = tmp_path / "nested.yaml"
    model.save(str(filepath), comments=True)

    content = filepath.read_text()
    assert "debug: false  # Enable debug mode" in content
    assert "host: localhost  # The hostname or IP address of the database server" in content
    assert "port: 5432 # The port number on which the database server is listening" in content


def test_save_yaml_with_nested_list_comments(tmp_path):
    class Item(BaseModel):
        name: str = Field(..., description="Item name")
        qty: int = Field(..., description="Item quantity")

    class Outer(Confdantic):
        items: list[Item] = Field(..., description="List of items")

    model = Outer(items=[{"name": "a", "qty": 2}])
    filepath = tmp_path / "items.yaml"
    model.save(str(filepath), comments=True)

    content = filepath.read_text()
    assert "items:  # List of items" in content
    assert "name: a  # Item name" in content
    assert "qty: 2 # Item quantity" in content


def test_save_yaml_with_optional_nested_comments(tmp_path):
    class Nested(BaseModel):
        retries: int = Field(3, description="Number of retries")

    class Outer(Confdantic):
        nested: Nested | None = Field(None, description="Optional nested config")

    model = Outer(nested={"retries": 5})
    filepath = tmp_path / "optional.yaml"
    model.save(str(filepath), comments=True)

    content = filepath.read_text()
    assert "nested:  # Optional nested config" in content
    assert "retries: 5" in content
    assert "# Number of retries" in content
