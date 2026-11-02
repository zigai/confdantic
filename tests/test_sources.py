from __future__ import annotations

import io
from pathlib import Path

import pytest
from pydantic import BaseModel

from confdantic import ConfdanticError, NoFormatsError, load
from confdantic.errors import (
    FileTooLargeError,
    MalformedDocumentError,
    MultipleDocumentsError,
    UnsupportedFormatError,
)
from confdantic.formats.base import RenderOptions
from confdantic.formats.json import JSON
from confdantic.formats.jsonc import JSONC
from confdantic.formats.registry import discovery_extensions, format_for_path, select_formats
from confdantic.formats.toml import TOML
from confdantic.formats.yaml import YAML
from confdantic.io import decode_utf8_sig, read_bounded_bytes


def test_read_bounded_bytes(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_bytes(b"port = 8080\n")
    assert read_bounded_bytes(path, max_file_size=1024) == b"port = 8080\n"

    with pytest.raises(FileTooLargeError):
        read_bounded_bytes(path, max_file_size=3)

    with pytest.raises(FileTooLargeError):
        read_bounded_bytes(io.BytesIO(b"123456789"), max_file_size=3)

    assert read_bounded_bytes("-", max_file_size=100, stdin=io.BytesIO(b"stdin")) == b"stdin"

    with pytest.raises(FileNotFoundError):
        read_bounded_bytes(tmp_path / "missing.toml")

    with pytest.raises(ValueError, match="directory"):
        read_bounded_bytes(tmp_path)


def test_decode_utf8_sig():
    assert decode_utf8_sig(b"hello") == "hello"
    assert decode_utf8_sig(b"\xef\xbb\xbfhello") == "hello"

    with pytest.raises(MalformedDocumentError, match="Invalid UTF-8"):
        decode_utf8_sig(b"\xff\xfe")


def test_select_formats_order_and_extension():
    selected = select_formats(("toml", ".yml", "jsonc"))
    assert discovery_extensions(selected) == (".toml", ".tml", ".yml", ".jsonc")
    assert isinstance(format_for_path("settings.yml", selected), YAML)

    with pytest.raises(UnsupportedFormatError):
        format_for_path("settings.yaml", selected)

    with pytest.raises(UnsupportedFormatError):
        format_for_path("settings", selected)

    with pytest.raises(UnsupportedFormatError):
        select_formats(("json5",))

    with pytest.raises(ConfdanticError):
        select_formats(("toml", ".toml"))


@pytest.mark.parametrize(
    ("fmt", "data", "error"),
    [
        (YAML(), b"a: 1\n---\nb: 2\n", MultipleDocumentsError),
        (YAML(), b"a: 1\na: 2\n", MalformedDocumentError),
        (JSON(), b'{"a":1,"a":2}', MalformedDocumentError),
        (JSONC(), b'// c\n{"a":1,"a":2}', MalformedDocumentError),
    ],
)
def test_invalid_documents(fmt, data, error):
    with pytest.raises(error):
        fmt.decode(data)


def test_origin_locations():
    yaml = YAML()
    document = yaml.decode(b"db:\n  port: 5432\n")
    location = yaml.locate(document, ["db", "port"])
    assert location is not None
    assert location.line == 2
    assert location.raw_value == "5432"
    toml = TOML()
    document = toml.decode(b"port = 1_000\n")
    location = toml.locate(document, ["port"])
    assert location is not None
    assert location.raw_value == "1_000"


def test_custom_format_receives_default_render_options(tmp_path: Path):
    from confdantic import save

    class Custom:
        name = "ini"
        extensions = (".ini",)

        def decode(self, data: bytes):
            return {"port": int(data.decode().split("=")[1])}

        def encode(self, document, *, plan, options):
            assert options == RenderOptions()
            return f"port={document['port']}".encode()

    class Config(BaseModel):
        port: int = 2

    path = tmp_path / "config.ini"
    save(Config(), path, formats=(Custom(),))
    assert load(Config, path, formats=(Custom(),), environment={}).port == 2


def test_explicit_file_formats_and_stdin(tmp_path: Path):
    class Config(BaseModel):
        port: int = 0

    path = tmp_path / "config.toml"
    path.write_text("port = 42\n")
    with pytest.raises(NoFormatsError):
        load(Config, path)

    with pytest.raises(UnsupportedFormatError):
        load(Config, path, formats=("yaml", "json"))

    conf = tmp_path / "app.conf"
    conf.write_text("port = 7\n")
    assert load(Config, conf, formats=("toml",), environment={}).port == 7
    with pytest.raises(MalformedDocumentError):
        load(Config, path, formats=("json",), environment={})

    assert (
        load(
            Config, "-", formats=("toml", "yaml"), stdin=io.StringIO("port = 2\n"), environment={}
        ).port
        == 2
    )


@pytest.mark.parametrize("fmt", [JSON(), JSONC()])
@pytest.mark.parametrize("value", [b"NaN", b"Infinity", b"-Infinity"])
def test_json_formats_reject_nonfinite_numbers(fmt, value):
    with pytest.raises(MalformedDocumentError):
        fmt.decode(b'{"value":' + value + b"}")

    with pytest.raises(ValueError, match="Out of range float values"):
        fmt.encode({"value": float(value)}, plan=None, options=RenderOptions())


@pytest.mark.parametrize("source", [b"123: value\n", b"child: &a {loop: *a}\n"])
def test_yaml_rejects_invalid_configuration_shapes(source):
    with pytest.raises(MalformedDocumentError):
        YAML().decode(source)


@pytest.mark.parametrize(
    ("fmt", "source"), [(YAML(), b'token: "sample-secret\n'), (TOML(), b'token = "sample-secret\n')]
)
def test_parser_failures_do_not_expose_document_values(fmt, source):
    with pytest.raises(MalformedDocumentError) as failure:
        fmt.decode(source)

    assert "sample-secret" not in str(failure.value)
    assert failure.value.__context__ is None


def test_json5_is_not_a_format(tmp_path: Path):
    class Config(BaseModel):
        host: str = "localhost"

    path = tmp_path / "config.json5"
    path.write_text("{host: 'x',}")
    with pytest.raises(UnsupportedFormatError):
        load(Config, path, formats=("json5",))
