from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from confdantic import (
    Confdantic,
    ConfigValidationError,
    Contribution,
    UnknownKeyError,
    UnsupportedFormatError,
)
from tests.models import ExampleModel


def test_load_nonexistent_file():
    with pytest.raises(FileNotFoundError):
        ExampleModel.load("nonexistent.json")


def test_load_unsupported_format(tmp_path):
    filepath = tmp_path / "test.txt"
    filepath.touch()
    with pytest.raises(UnsupportedFormatError):
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

    with pytest.raises(UnsupportedFormatError):
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


def test_confdantic_init_and_layered_features(tmp_path):
    from pathlib import Path

    class App(Confdantic):
        host: str = "127.0.0.1"
        port: int = 8080

    tpl_path = Path(tmp_path) / "app.toml"
    App.init(tpl_path)
    assert tpl_path.exists()
    assert "port = 8080" in tpl_path.read_text()

    # Classmethod in-place update
    App.set_key(tpl_path, "port", 9090)
    assert "port = 9090" in tpl_path.read_text()

    # Classmethod load with a Path object and a flag contribution
    loaded = App.load(tpl_path, contributions=[Contribution({"host": "0.0.0.0"})])
    assert loaded.port == 9090
    assert loaded.host == "0.0.0.0"


class Server(Confdantic):
    host: str = "localhost"
    port: int = 8080


@pytest.mark.parametrize(
    ("save_name", "load_name", "suffix", "marker"),
    [
        ("save_toml", "load_toml", ".toml", 'host = "example"'),
        ("save_yaml", "load_yaml", ".yaml", "host: example"),
        ("save_json", "load_json", ".json", '"host": "example"'),
        ("save_jsonc", "load_jsonc", ".jsonc", '"host": "example"'),
    ],
)
def test_format_methods_use_their_format_whatever_the_suffix(
    tmp_path: Path, save_name: str, load_name: str, suffix: str, marker: str
):
    config = Server(host="example", port=1)
    other = ".yaml" if suffix != ".yaml" else ".toml"
    for name in ("config.conf", f"config{other}", "config"):
        target = tmp_path / name
        getattr(config, save_name)(target)
        assert marker in target.read_text(encoding="utf-8")
        assert getattr(Server, load_name)(target) == config


def test_class_set_key_validates_against_the_class(tmp_path: Path):
    target = tmp_path / "config.toml"
    target.write_text("# server\nport = 8080\n", encoding="utf-8")
    before = target.read_bytes()

    with pytest.raises(ConfigValidationError):
        Server.set_key(target, "port", "not-a-number")
    with pytest.raises(UnknownKeyError):
        Server.set_keys(target, {"port": 1, "prot": 2})

    assert target.read_bytes() == before
