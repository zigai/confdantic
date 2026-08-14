import pytest
from pydantic import BaseModel, Field

from confdantic import Confdantic
from tests.models import ExampleModel


def test_load_nonexistent_file():
    with pytest.raises(FileNotFoundError):
        ExampleModel.load("nonexistent.json")


def test_load_unsupported_format(tmp_path):
    filepath = tmp_path / "test.txt"
    filepath.touch()
    with pytest.raises(ValueError, match=r"Unknown file extension: txt"):
        ExampleModel.load(str(filepath))


def test_save_existing_file_no_overwrite(tmp_path, sample_data):
    model = ExampleModel(**sample_data)
    filepath = tmp_path / "test.json"
    filepath.touch()

    with pytest.raises(FileExistsError):
        model.save(str(filepath), overwrite=False)


def test_save_unsupported_format(tmp_path, sample_data):
    model = ExampleModel(**sample_data)
    filepath = tmp_path / "test.txt"

    with pytest.raises(ValueError, match=r"Unknown file extension: txt"):
        model.save(str(filepath))


def test_nested_model_save_load(tmp_path):
    class Address(BaseModel):
        street: str
        city: str

    class Person(Confdantic):
        name: str
        address: Address

    data = {"name": "Jane Doe", "address": {"street": "456 Elm St", "city": "Anytown"}}

    model = Person(**data)
    filepath = tmp_path / "nested.yaml"
    model.save(str(filepath))

    loaded_model = Person.load(str(filepath))
    assert loaded_model.model_dump() == data
    assert isinstance(loaded_model.address, Address)


@pytest.mark.parametrize(
    ("extension", "expected"),
    [
        ("json", '  "name": "x"'),
        ("jsonc", '  "name": "x" // Name desc'),
    ],
)
def test_save_json_indent_forwarded(tmp_path, extension, expected):
    class IndentModel(Confdantic):
        name: str = Field("x", description="Name desc")

    model = IndentModel()
    filepath = tmp_path / f"indent.{extension}"
    model.save(str(filepath), comments=True, indent=2)
    content = filepath.read_text()
    assert expected in content


@pytest.mark.parametrize(
    ("extension", "content"),
    [
        ("json", b'\xef\xbb\xbf{"name": "x"}'),
        ("jsonc", b'\xef\xbb\xbf{\n  // comment\n  "name": "x"\n}'),
        ("toml", b'\xef\xbb\xbfname = "x"'),
    ],
)
def test_load_with_bom(tmp_path, extension, content):
    class BomModel(Confdantic):
        name: str

    filepath = tmp_path / f"bom.{extension}"
    filepath.write_bytes(content)
    model = BomModel.load(str(filepath))
    assert model.name == "x"
