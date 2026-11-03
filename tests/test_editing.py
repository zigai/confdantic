from __future__ import annotations

import json
import multiprocessing
import os
from datetime import timedelta
from multiprocessing.synchronize import Barrier
from pathlib import Path
from typing import Annotated

import pytest
from pydantic import BaseModel, BeforeValidator, ConfigDict, SecretStr, create_model

from confdantic import Duration, config_field, edit, load, save
from confdantic.editing import set_bytes, set_key, set_keys
from confdantic.errors import (
    ConfigValidationError,
    InvalidKeyPathError,
    MultipleDocumentsError,
    UnknownKeyError,
    UnsupportedFormatError,
)
from confdantic.formats.base import RenderOptions
from confdantic.formats.registry import format_for_path, select_formats


def test_set_bytes_jsonc_preserves_comments_and_braces():
    jsonc_input = b"""// Top header
{
  /* block comment */
  "title": "hello {with braces}",
  "database": {
    "port": 5432 // port comment
  }
}"""
    output = set_bytes("jsonc", jsonc_input, "database.port", 5433)
    text = output.decode("utf-8")
    assert "// Top header" in text
    assert "/* block comment */" in text
    assert '"title": "hello {with braces}"' in text
    assert '"port": 5433 // port comment' in text


def test_set_bytes_jsonc_nested_insertion():
    jsonc_input = b"""{
  "database": {
    "host": "localhost"
  }
}"""
    output = set_bytes("jsonc", jsonc_input, "database.port", 5432)
    text = output.decode("utf-8")
    assert '"host": "localhost",' in text
    assert '"port": 5432' in text


def test_set_bytes_jsonc_create_absent_intermediate_mappings():
    jsonc_input = b"""{
  "app": "demo"
}"""
    output = set_bytes("jsonc", jsonc_input, "database.redis.port", 6379)
    text = output.decode("utf-8")
    assert '"database": {' in text
    assert '"redis": {' in text
    assert '"port": 6379' in text


@pytest.mark.parametrize(
    ("fmt", "source"),
    [
        ("json", b"{}"),
        ("json", b'{\n  "existing": 1\n}'),
        ("jsonc", b'{\n  "existing": 1, // preserved\n}'),
    ],
)
@pytest.mark.parametrize(
    ("dotted", "key"),
    [
        ('double"quote', 'double"quote'),
        ("back\\\\slash", "back\\slash"),
        ("line\nbreak", "line\nbreak"),
        ("café", "café"),
    ],
)
def test_set_bytes_json_inserted_key_is_encoded(fmt: str, source: bytes, dotted: str, key: str):
    output = set_bytes(fmt, source, dotted, 2)

    assert json.dumps(key, ensure_ascii=False).encode("utf-8") + b": 2" in output
    assert format_for_path(f"test.{fmt}", select_formats((fmt,))).decode(output)[key] == 2

    if fmt == "jsonc":
        assert b"// preserved" in output


def test_set_bytes_toml_preserves_comments_and_order():
    toml_input = b"""# File header
[server]
host = "localhost" # inline host comment
port = 5432 # inline port comment
"""
    output = set_bytes("toml", toml_input, "server.port", 5433)
    text = output.decode("utf-8")
    assert "# File header" in text
    assert 'host = "localhost" # inline host comment' in text
    assert "port = 5433 # inline port comment" in text


def test_set_bytes_yaml_preserves_comments():
    yaml_input = b"""# YAML header
database:
  host: localhost # host comment
  port: 5432 # port comment
"""
    output = set_bytes("yaml", yaml_input, "database.port", 5433)
    text = output.decode("utf-8")
    assert "# YAML header" in text
    assert "host: localhost # host comment" in text
    assert "port: 5433 # port comment" in text


def test_set_bytes_yaml_rejects_multi_doc():
    multi = b"a: 1\n---\nb: 2\n"
    with pytest.raises(MultipleDocumentsError):
        set_bytes("yaml", multi, "a", 3)


@pytest.mark.parametrize(
    ("fmt", "doc"),
    [
        ("json", b'{"port": 5432}'),
        ("jsonc", b'{"port": 5432}'),
        ("toml", b"port = 5432\n"),
        ("yaml", b"port: 5432\n"),
    ],
)
def test_set_bytes_rejects_non_object_traversal(fmt: str, doc: bytes):
    with pytest.raises(InvalidKeyPathError, match="Cannot traverse non-object"):
        set_bytes(fmt, doc, "port.nested", 1)


@pytest.mark.parametrize("key", ["", "port\\", "port\\\\\\", "port\\x", "a\\/b"])
def test_set_bytes_rejects_malformed_keys(key: str):
    with pytest.raises(InvalidKeyPathError):
        set_bytes("json", b'{"port": 5432}', key, 1)


def test_set_bytes_rejects_unsupported_custom_format():
    with pytest.raises(UnsupportedFormatError, match="does not support"):
        set_bytes("xml", b"<xml></xml>", "a.b", 1)


def test_set_bytes_type_coercion_and_duration_inference():
    # Int coercion from string
    doc_int = b'{"port": 5432}'
    out_int = set_bytes("json", doc_int, "port", "5433")
    assert b'"port": 5433' in out_int

    # Bool coercion from string
    doc_bool = b'{"debug": true}'
    out_bool = set_bytes("json", doc_bool, "debug", "false")
    assert b'"debug": false' in out_bool

    # Duration inference on existing duration string
    doc_dur = b'{"timeout": "1h"}'
    out_dur = set_bytes("json", doc_dur, "timeout", "90m")
    assert b'"timeout": "1h30m"' in out_dur

    # Duration inference with timedelta object
    out_td = set_bytes("json", doc_dur, "timeout", timedelta(minutes=45))
    assert b'"timeout": "45m"' in out_td


@pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX permission bits")
def test_set_file_in_place_preserves_mode(tmp_path: Path):
    target = tmp_path / "app.toml"
    target.write_bytes(b'port = 5432\nhost = "localhost"\n')
    target.chmod(0o600)

    set_key(target, "port", 5433)
    assert b"port = 5433" in target.read_bytes()
    assert (target.stat().st_mode & 0o777) == 0o600


def test_set_key_follows_symlink_to_the_linked_file(tmp_path: Path):
    real = tmp_path / "real.toml"
    real.write_bytes(b"port = 8080\n")
    link = tmp_path / "link.toml"
    link.symlink_to("real.toml")

    set_key(link, "port", 8081)

    assert link.is_symlink()
    assert real.read_bytes() == b"port = 8081\n"


def test_set_key_and_set_keys(tmp_path: Path):
    target = tmp_path / "app.yaml"
    target.write_text("# header\nhost: localhost\nport: 5432\n", encoding="utf-8")

    set_key(target, "port", 5433)
    assert "port: 5433" in target.read_text()
    assert "# header" in target.read_text()

    set_keys(target, {"host": "api.internal", "port": 9000})
    content = target.read_text()
    assert "host: api.internal" in content
    assert "port: 9000" in content
    assert "# header" in content


def test_set_key_with_model_validation(tmp_path: Path):
    from confdantic import Confdantic

    class DB(BaseModel):
        port: int

    class ServerConfig(Confdantic):
        database: DB

    target = tmp_path / "server.toml"
    target.write_text("[database]\n# DB port\nport = 5432\n", encoding="utf-8")

    # 1. Valid update with model parameter
    set_key(target, "database.port", 5433, model=ServerConfig)
    assert "port = 5433" in target.read_text()
    assert "# DB port" in target.read_text()

    # 2. Unknown config key reports a suggestion.
    with pytest.raises(UnknownKeyError, match=r'did you mean "database\.port"'):
        set_key(target, "database.prt", 5435, model=ServerConfig)

    # 3. Invalid values use Pydantic's message and the config key.
    with pytest.raises(ConfigValidationError, match=r"valid integer.*for database\.port"):
        set_key(target, "database.port", "not_a_number", model=ServerConfig)


def test_set_key_model_coerces_csv_duration_and_map_entry(tmp_path: Path):
    class Config(BaseModel):
        tags: list[str] = []
        timeout: Duration = timedelta(minutes=1)
        labels: dict[str, str] = {"env": "dev"}

    target = tmp_path / "config.json"
    save(Config(), target)
    set_keys(
        target, {"tags": 'red,"blue,green"', "timeout": "90m", "labels.env": "prod"}, model=Config
    )
    document = json.loads(target.read_text())
    assert document == {
        "tags": ["red", "blue,green"],
        "timeout": "1h30m",
        "labels": {"env": "prod"},
    }


@pytest.mark.parametrize("suffix", ["json", "jsonc"])
def test_set_map_entry_in_empty_json_object(tmp_path: Path, suffix: str):
    class Config(BaseModel):
        labels: dict[str, str] = {}

    target = tmp_path / f"config.{suffix}"
    save(Config(), target)
    set_key(target, "labels.env", "prod", model=Config)
    assert load(Config, target, formats=(suffix,), environment={}).labels == {"env": "prod"}


def test_set_map_entry_in_yaml(tmp_path: Path):
    class Config(BaseModel):
        labels: dict[str, str] = {}

    target = tmp_path / "config.yaml"
    save(Config(), target)
    set_key(target, "labels.env", "prod", model=Config)
    assert "env: prod" in target.read_text()


@pytest.mark.parametrize("suffix", ["toml", "yaml", "json", "jsonc"])
def test_save_set_load_uses_config_key(tmp_path: Path, suffix: str):
    class Config(BaseModel):
        port: int = config_field(8080, key="http_port")

    target = tmp_path / f"config.{suffix}"
    save(Config(), target)
    set_key(target, "http_port", "9000", model=Config)
    assert load(Config, target, formats=(suffix,), environment={}).port == 9000
    assert "http_port" in target.read_text()


def test_set_key_writes_secret_values_not_their_mask(tmp_path: Path):
    from pydantic import SecretStr

    class Credentials(BaseModel):
        password: SecretStr = SecretStr("")

    path = tmp_path / "config.toml"
    path.write_text('password = ""\n')
    set_key(path, "password", "hunter2", model=Credentials)

    assert path.read_text() == 'password = "hunter2"\n'


@pytest.mark.parametrize("key", ["token", "db", "private", "item"])
def test_typed_edit_redacts_secret_validator_errors(tmp_path: Path, key: str):
    def reject(value: str) -> str:
        if value.startswith("reject-"):
            raise ValueError(f"Rejected {value}")

        return value

    checked = Annotated[str, BeforeValidator(reject)]

    class Database(BaseModel):
        credential: checked = config_field("", key="password", secret=True)
        label: checked = ""

    first = create_model(
        "Choice",
        __config__=ConfigDict(loc_by_alias=False),
        credential=(checked, config_field(key="password", secret=True)),
    )
    second = create_model(
        "Choice",
        __config__=ConfigDict(loc_by_alias=False),
        credential=(str, config_field(key="public_password")),
    )

    class Config(BaseModel):
        credential: checked = config_field("", key="token", secret=True)
        database: Database = config_field(default_factory=Database, key="db")
        private: Database = config_field(default_factory=Database, secret=True)
        item: first | second | None = None

    path = tmp_path / "config.json"
    save(Config(), path)
    before = path.read_bytes()
    value = (
        "reject-credential"
        if key == "token"
        else {"password": "reject-credential", "label": "reject-label"}
    )
    with pytest.raises(ConfigValidationError) as failure:
        set_key(path, key, value, model=Config)

    error = failure.value
    assert "reject-credential" not in str(error)
    assert "reject-credential" not in repr(error.errors)
    assert error.__cause__ is None
    assert error.__context__ is None
    if key == "db":
        assert "Rejected reject-label" in str(error)
        assert {entry.key for entry in error.errors} == {("db", "password"), ("db", "label")}
    elif key == "private":
        assert "reject-label" not in str(error)

    assert path.read_bytes() == before


def test_set_key_splits_csv_for_optional_list(tmp_path: Path):
    class Tags(BaseModel):
        tags: list[str] | None = None

    path = tmp_path / "config.toml"
    path.write_text("")
    set_key(path, "tags", "a,b", model=Tags)

    assert load(Tags, path, formats=("toml",), environment={}).tags == ["a", "b"]


def test_set_key_writes_nested_model_under_config_keys(tmp_path: Path):
    class Database(BaseModel):
        port: int = config_field(5432, key="db_port")

    class Config(BaseModel):
        database: Database = Database()

    path = tmp_path / "config.toml"
    path.write_text("")
    set_key(path, "database", {"db_port": "6000"}, model=Config)

    assert path.read_text() == "[database]\ndb_port = 6000\n"


@pytest.mark.parametrize(
    ("document", "value", "expected"),
    [
        (b"a = true\n", " off ", b"a = false\n"),
        (b"a = false\n", "t", b"a = true\n"),
        (b"a = 3\n", "1.0", b"a = 1\n"),
        (b"a = 1.5\n", "nope", b'a = "nope"\n'),
    ],
)
def test_set_bytes_coerces_scalars_like_pydantic(document: bytes, value: str, expected: bytes):
    assert set_bytes("toml", document, "a", value) == expected


def test_jsonc_insertion_preserves_comment_after_last_value():
    source = b'{\n  "count": 1 // keep this comment\n}\n'
    output = set_bytes("jsonc", source, "name", "sample")
    assert b"// keep this comment" in output
    assert format_for_path("config.jsonc", select_formats(("jsonc",))).decode(output) == {
        "count": 1,
        "name": "sample",
    }


def test_yaml_edit_preserves_untouched_quotes_and_indentation():
    source = b'other: "yes" # keep\nchild:\n    name: "sample"\ncount: 1\n'
    output = set_bytes("yaml", source, "count", 2)
    assert output == source.replace(b"count: 1", b"count: 2")


def _edit_from_process(path: str, key: str, value: int, ready: Barrier) -> None:
    ready.wait()
    set_key(path, key, value)


@pytest.mark.parametrize("format_name", ["json", "yaml"])
def test_concurrent_process_edits_keep_both_updates(tmp_path: Path, format_name: str):
    target = tmp_path / f"config.{format_name}"
    writer = format_for_path(target, select_formats((format_name,)))
    document = {
        "left": 0,
        "right": 0,
        "padding": {f"item{index}": "sample" for index in range(256)},
    }
    target.write_bytes(writer.encode(document, plan=None, options=RenderOptions()))
    context = multiprocessing.get_context("spawn")
    ready = context.Barrier(2)
    processes = [
        context.Process(target=_edit_from_process, args=(str(target), key, value, ready))
        for key, value in [("left", 11), ("right", 22)]
    ]
    for process in processes:
        process.start()

    for process in processes:
        process.join()

    assert [process.exitcode for process in processes] == [0, 0]

    assert writer.decode(target.read_bytes()) == {**document, "left": 11, "right": 22}


def test_failed_edit_releases_writer_and_preserves_file(tmp_path: Path):
    target = tmp_path / "config.json"
    original = b'{"count": 1, "child": 7}'
    target.write_bytes(original)
    with pytest.raises(InvalidKeyPathError, match="non-object"):
        set_keys(target, {"count": 2, "child.name": "sample"})

    assert target.read_bytes() == original
    set_key(target, "count", 3)
    assert json.loads(target.read_bytes()) == {"count": 3, "child": 7}


def _replace_from_process(path: str, operation: str, ready: Barrier) -> None:
    from confdantic import init

    class Config(BaseModel):
        left: int = 10
        right: int = 20

    ready.wait()
    if operation == "save":
        save(Config(), path)
    else:
        init(Config, path, overwrite=True)


@pytest.mark.parametrize("operation", ["save", "init"])
def test_edits_do_not_restore_data_replaced_by_other_writers(tmp_path: Path, operation: str):
    target = tmp_path / "config.yaml"
    writer = format_for_path(target, select_formats(("yaml",)))
    target.write_bytes(
        writer.encode(
            {"left": 0, "right": 0, "padding": {f"item{index}": "sample" for index in range(256)}},
            plan=None,
            options=RenderOptions(),
        )
    )
    context = multiprocessing.get_context("spawn")
    ready = context.Barrier(2)
    processes = [
        context.Process(target=_edit_from_process, args=(str(target), "left", 11, ready)),
        context.Process(target=_replace_from_process, args=(str(target), operation, ready)),
    ]
    for process in processes:
        process.start()

    for process in processes:
        process.join()

    assert [process.exitcode for process in processes] == [0, 0]
    result = writer.decode(target.read_bytes())
    assert result["right"] == 20
    assert result["left"] in (10, 11)
    assert "padding" not in result


@pytest.mark.parametrize(
    ("fmt", "source"),
    [
        ("toml", b"[labels]\na = 1\n"),
        ("yaml", b"labels:\n  a: 1\n"),
        ("json", b'{"labels": {"a": 1}}'),
        ("jsonc", b'{"labels": {"a": 1}}'),
    ],
)
@pytest.mark.parametrize(
    ("dotted", "entry"),
    [("labels.a\\.b", "a.b"), ("labels.a\\\\b", "a\\b"), ("labels.", ""), ("labels.a/b", "a/b")],
)
def test_set_bytes_escaped_key_names_one_literal_entry(
    fmt: str, source: bytes, dotted: str, entry: str
):
    output = set_bytes(fmt, source, dotted, 2)

    decoded = format_for_path(f"test.{fmt}", select_formats((fmt,))).decode(output)
    assert decoded == {"labels": {"a": 1, entry: 2}}


def test_set_key_with_model_edits_a_dotted_map_entry(tmp_path: Path):
    class Config(BaseModel):
        labels: dict[str, int] = {}

    path = tmp_path / "config.yaml"
    path.write_text("labels:\n  c: 1\n", encoding="utf-8")

    set_key(path, "labels.a\\.b", "2", model=Config)
    set_key(path, ("labels", "d.e"), "3", model=Config)

    assert load(Config, path, formats=("yaml",)).labels == {"c": 1, "a.b": 2, "d.e": 3}


class TypedExtras(BaseModel):
    model_config = ConfigDict(extra="allow")
    __pydantic_extra__: dict[str, int]

    name: str = ""


def test_set_key_checks_typed_extra_values(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('name = "n"\n', encoding="utf-8")

    with pytest.raises(ConfigValidationError):
        set_key(path, "alpha", "nope", model=TypedExtras)

    set_key(path, "beta", "5", model=TypedExtras)

    assert path.read_text(encoding="utf-8") == 'name = "n"\nbeta = 5\n'


def test_set_key_with_model_accepts_duration_units(tmp_path: Path):
    class Config(BaseModel):
        timeout: Duration = timedelta(seconds=30)

    path = tmp_path / "config.toml"
    path.write_text('timeout = "30s"\n', encoding="utf-8")

    set_key(path, "timeout", "1w2d", model=Config)

    assert path.read_text(encoding="utf-8") == 'timeout = "1w2d"\n'


@pytest.mark.parametrize(
    ("suffix", "content"),
    [
        (".toml", "# keep\n[database]\nport = 5432 # port\n"),
        (".yaml", "# keep\ndatabase:\n  port: 5432 # port\n"),
        (".json", '{\n  "database": {"port": 5432}\n}\n'),
    ],
)
def test_rejected_model_edit_leaves_the_file_unchanged(tmp_path: Path, suffix: str, content: str):
    class Database(BaseModel):
        port: int = 0

    class Config(BaseModel):
        database: Database = Database()

    target = tmp_path / f"config{suffix}"
    target.write_text(content, encoding="utf-8")
    before = target.read_bytes()

    with pytest.raises(UnknownKeyError):
        set_keys(target, {"database.port": 1, "database.prt": 2}, model=Config)
    with pytest.raises(ConfigValidationError):
        set_keys(target, {"database.port": "not-a-number"}, model=Config)

    assert target.read_bytes() == before


def test_editing_a_missing_file_creates_nothing(tmp_path: Path):
    target = tmp_path / "missing.toml"

    with pytest.raises(FileNotFoundError):
        set_key(target, "port", 1)

    assert list(tmp_path.iterdir()) == []


class EditDatabase(BaseModel):
    host: str = "localhost"
    port: int = config_field(5432, key="db_port")


class EditConfig(BaseModel):
    name: str = "app"
    retries: int = 3
    token: SecretStr = config_field(SecretStr(""), env="EDIT_TOKEN")
    database: EditDatabase = EditDatabase()
    labels: dict[str, str] = {}


EDIT_FILE = """# service settings
name = "demo" # shown in logs

[database]
db_port = 5432 # primary

[labels]
env = "dev"
team = "core"
"""


@pytest.fixture
def edit_target(tmp_path: Path) -> Path:
    target = tmp_path / "config.toml"
    target.write_text(EDIT_FILE, encoding="utf-8")
    return target


def test_edit_writes_only_the_changed_key_under_its_config_name(edit_target: Path):
    with edit(edit_target, EditConfig) as config:
        assert config.name == "demo"
        config.database.port = 6000

    assert edit_target.read_text() == EDIT_FILE.replace("db_port = 5432", "db_port = 6000")


def test_edit_does_not_write_environment_values(edit_target: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EDIT_TOKEN", "from-environment")

    with edit(edit_target, EditConfig) as config:
        assert config.token.get_secret_value() == ""
        config.retries = 5

    text = edit_target.read_text()
    assert "retries = 5" in text
    assert "from-environment" not in text


def test_edit_writes_a_changed_secret_in_clear(edit_target: Path):
    with edit(edit_target, EditConfig) as config:
        config.token = SecretStr("sample-token")

    assert 'token = "sample-token"' in edit_target.read_text()


def test_edit_removes_a_deleted_map_entry(edit_target: Path):
    with edit(edit_target, EditConfig) as config:
        del config.labels["team"]

    assert load(EditConfig, edit_target, formats=("toml",)).labels == {"env": "dev"}


def test_edit_writes_nothing_when_the_block_raises(edit_target: Path):
    def change_then_fail() -> None:
        with edit(edit_target, EditConfig) as config:
            config.retries = 5
            raise RuntimeError

    with pytest.raises(RuntimeError):
        change_then_fail()

    assert edit_target.read_text() == EDIT_FILE


def test_edit_rejects_an_invalid_value_without_writing(edit_target: Path):
    with (
        pytest.raises(ConfigValidationError, match="retries"),
        edit(edit_target, EditConfig) as config,
    ):
        config.retries = "many"  # pyrefly: ignore[bad-assignment]

    assert edit_target.read_text() == EDIT_FILE


def test_edit_keeps_a_change_made_to_another_key_meanwhile(edit_target: Path):
    with edit(edit_target, EditConfig) as config:
        set_key(edit_target, "name", "renamed")
        config.retries = 5

    loaded = load(EditConfig, edit_target, formats=("toml",))
    assert (loaded.name, loaded.retries) == ("renamed", 5)


def test_edit_rejects_a_format_that_cannot_be_edited(tmp_path: Path):
    target = tmp_path / "config.ini"
    target.write_text("name = demo\n", encoding="utf-8")

    with pytest.raises(UnsupportedFormatError), edit(target, EditConfig):
        pass
