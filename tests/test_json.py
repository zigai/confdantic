import json

import pytest
from pydantic import Field

from confdantic import Confdantic
from tests.models import ExampleModel


def test_load_json(tmp_path, sample_data):
    filepath = tmp_path / "test.json"
    with filepath.open("w") as file:
        json.dump(sample_data, file)

    model = ExampleModel.load(str(filepath))
    assert model.model_dump() == sample_data


def test_save_json(tmp_path, sample_data):
    model = ExampleModel(**sample_data)
    filepath = tmp_path / "test.json"
    model.save(str(filepath))

    with filepath.open() as file:
        loaded_data = json.load(file)
    assert loaded_data == sample_data


def test_load_json_encoding_forwarded(tmp_path):
    class EncModel(Confdantic):
        name: str

    filepath = tmp_path / "latin1.json"
    filepath.write_bytes('{"name": "Jos\xe9"}'.encode("latin-1"))
    model = EncModel.load(str(filepath), encoding="latin-1")
    assert model.name == "José"


@pytest.mark.parametrize("extension", ["json", "jsonc"])
def test_save_json_formats_reject_unsupported_type_without_opt_in(tmp_path, extension):
    class SetModel(Confdantic):
        tags: set[str] = Field(default_factory=lambda: {"a", "b"})

    model = SetModel()
    filepath = tmp_path / f"set.{extension}"
    with pytest.raises(TypeError):
        model.save(str(filepath))

    model.save(str(filepath), serialize_unsupported=True)
    loaded = SetModel.load(str(filepath))
    assert loaded.model_dump() == model.model_dump()


def test_save_json_serialization_error_does_not_truncate_existing_file(tmp_path):
    class SetModel(Confdantic):
        tags: set[str]

    filepath = tmp_path / "set.json"
    filepath.write_text("existing content")

    with pytest.raises(TypeError):
        SetModel(tags={"a"}).save(str(filepath))

    assert filepath.read_text() == "existing content"
