from __future__ import annotations

import json
import os
import tomllib
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

import jsonschema
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import BaseModel, ConfigDict, Field, SecretStr, create_model, field_validator

from confdantic.duration import Duration
from confdantic.errors import (
    ConfdanticError,
    ConfigValidationError,
    UnsupportedFormatError,
)
from confdantic.fields import config_field
from confdantic.formats.base import RenderOptions
from confdantic.loader import Contribution, load
from confdantic.persistence import atomic_write_bytes, init, save
from confdantic.schema import schema


def test_atomic_write_bytes(tmp_path: Path):
    target = tmp_path / "atomic.txt"
    atomic_write_bytes(target, b"initial content", overwrite=True)
    assert target.read_bytes() == b"initial content"

    # Non-overwrite fails when target exists
    with pytest.raises(FileExistsError):
        atomic_write_bytes(target, b"new content", overwrite=False)

    # File remains identical
    assert target.read_bytes() == b"initial content"

    # Overwrite works
    atomic_write_bytes(target, b"updated content", overwrite=True)
    assert target.read_bytes() == b"updated content"


def test_atomic_write_bytes_preserves_target_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    target = tmp_path / "atomic.txt"
    target.write_bytes(b"original content")

    def fail_replace(source: Path, destination: Path) -> Path:
        raise OSError("injected replace failure")

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(OSError, match="injected replace failure"):
        atomic_write_bytes(target, b"new content", overwrite=True)

    assert target.read_bytes() == b"original content"
    assert list(tmp_path.glob(".tmp_confdantic_*")) == []


class Port(BaseModel):
    port: int = 1


@pytest.mark.parametrize(
    "write",
    [
        lambda path: save(Port(port=2), path),
        lambda path: init(Port, path, overwrite=True),
    ],
    ids=["save", "init"],
)
def test_writes_follow_symlinks_to_the_linked_file(tmp_path: Path, write):
    real = tmp_path / "real.toml"
    real.write_text("port = 9\n", encoding="utf-8")
    link = tmp_path / "link.toml"
    link.symlink_to("real.toml")

    write(link)

    assert link.is_symlink()
    assert "port = 9" not in real.read_text(encoding="utf-8")
    # Windows keeps the writer lock file; only the link and its target should be left otherwise.
    left = sorted(item.name for item in tmp_path.iterdir() if item.suffix != ".lock")
    assert left == ["link.toml", "real.toml"]


def test_write_through_dangling_symlink_creates_its_target(tmp_path: Path):
    link = tmp_path / "link.toml"
    link.symlink_to("missing/real.toml")

    save(Port(port=2), link)

    assert link.is_symlink()
    assert (tmp_path / "missing" / "real.toml").read_text(encoding="utf-8") == "port = 2\n"


def test_write_through_symlink_loop_fails(tmp_path: Path):
    first = tmp_path / "first.toml"
    second = tmp_path / "second.toml"
    first.symlink_to(second)
    second.symlink_to(first)

    with pytest.raises(OSError, match=r"first\.toml"):
        save(Port(), first)


@pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX permission bits")
def test_existing_file_keeps_its_permission_bits(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text("port = 1\n", encoding="utf-8")
    path.chmod(0o600)

    save(Port(port=2), path, mode=0o644)
    init(Port, path, overwrite=True, mode=0o644)

    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX permission bits")
@pytest.mark.parametrize("mode", [0o640, 0])
def test_new_file_uses_the_requested_mode(tmp_path: Path, mode: int):
    path = tmp_path / "config.toml"

    save(Port(), path, mode=mode)

    assert path.stat().st_mode & 0o777 == mode


def test_save_and_init(tmp_path: Path):
    class Service(BaseModel):
        name: str = "svc"
        port: int = 3000

    inst = Service(port=3001)
    p1 = tmp_path / "svc1.toml"
    save(inst, p1)
    assert "port = 3001" in p1.read_text()

    p2 = tmp_path / "svc2.toml"
    init(Service, p2)
    assert "port = 3000" in p2.read_text()


def test_init_creates_schema_directives(tmp_path: Path):
    class Server(BaseModel):
        host: str = "localhost"
        port: int = 8080

    schema_url = "https://example.com/schema.json"

    # TOML
    toml_path = tmp_path / "template.toml"
    init(Server, toml_path, schema_url=schema_url)
    toml_text = toml_path.read_text(encoding="utf-8")
    assert toml_text.startswith(f"#:schema {schema_url}\n")
    assert "port = 8080" in toml_text

    # YAML
    yaml_path = tmp_path / "template.yaml"
    init(Server, yaml_path, schema_url=schema_url)
    yaml_text = yaml_path.read_text(encoding="utf-8")
    assert yaml_text.startswith(f"# yaml-language-server: $schema={schema_url}\n")
    assert "port: 8080" in yaml_text

    # JSON
    json_path = tmp_path / "template.json"
    init(Server, json_path, schema_url=schema_url)
    json_dict = json.loads(json_path.read_text(encoding="utf-8"))
    assert json_dict["$schema"] == schema_url
    assert json_dict["port"] == 8080


def test_init_rejects_missing_required_fields(tmp_path: Path):
    class StrictConfig(BaseModel):
        required_field: str

    with pytest.raises(ConfigValidationError, match="Field required for required_field"):
        init(StrictConfig, tmp_path / "strict.toml")


def test_init_omits_secrets(tmp_path: Path):
    class Nested(BaseModel):
        password: SecretStr = config_field(SecretStr("nested_default"))
        enabled: bool = True

    class Secrets(BaseModel):
        token: SecretStr = config_field(SecretStr("leaked_default"))
        nested: Nested = Nested()

    for suffix in ("toml", "yaml", "json", "jsonc"):
        target = tmp_path / f"template.{suffix}"
        init(Secrets, target)
        content = target.read_text()
        assert "leaked_default" not in content
        assert "nested_default" not in content
        assert "token" not in content
        assert "password" not in content
        assert "enabled" in content


def test_init_omits_required_secret_but_reports_required_public_field(tmp_path: Path):
    class SecretOnly(BaseModel):
        credential: SecretStr = config_field(secret=True)
        port: int = 8080

    target = tmp_path / "secret.json"
    init(SecretOnly, target)
    assert json.loads(target.read_text()) == {"port": 8080}

    class MissingPublic(SecretOnly):
        host: str

    with pytest.raises(ConfigValidationError, match="Field required for host"):
        init(MissingPublic, tmp_path / "missing.json")


def test_save_custom_encoder_requires_default_render_options(tmp_path: Path):
    class TXT:
        name = "txt"
        extensions = (".txt",)

        def decode(self, data: bytes) -> dict[str, object]:
            return {"port": int(data)}

        def encode(
            self, document: dict[str, object], *, plan: object, options: RenderOptions
        ) -> bytes:
            assert options == RenderOptions()
            assert plan is not None

            return str(document["http_port"]).encode()

    class Config(BaseModel):
        port: int = config_field(8080, key="http_port")

    target = tmp_path / "config.txt"
    with pytest.raises(UnsupportedFormatError, match="non-default render options"):
        save(Config(), target, formats=(TXT(),), comments=True)

    assert not target.exists()

    save(Config(), target, formats=(TXT(),))
    assert target.read_bytes() == b"8080"


def test_init_rejects_newline_in_schema_url(tmp_path: Path):
    class Safe(BaseModel):
        x: int = 1

    with pytest.raises(ValueError, match="newline"):
        init(Safe, tmp_path / "safe.toml", schema_url="https://evil.com\r\ninjected")


def test_init_rejects_model_with_existing_schema_property(tmp_path: Path):
    class Conflict(BaseModel):
        schema_: str = config_field("custom", key="$schema")

    with pytest.raises(ConfdanticError, match="already defines a '\\$schema' field"):
        init(Conflict, tmp_path / "conflict.json", schema_url="https://example.com/schema.json")


def test_schema_generation():
    class SubModel(BaseModel):
        sub_key: str = config_field("val", key="sk")

    class RootModel(BaseModel):
        sub: SubModel
        count: int = config_field(1, key="count_key")

    res = schema(
        RootModel,
        schema_id="https://example.com/root.json",
        title="RootSchema",
    )
    assert res["$id"] == "https://example.com/root.json"
    assert res["title"] == "RootSchema"
    assert "count_key" in res["properties"]
    assert "sk" in res["$defs"]["SubModel"]["properties"]


@pytest.mark.parametrize("fmt", ["toml", "yaml", "json", "jsonc"])
def test_save_writes_secret_values_that_load_back(tmp_path: Path, fmt: str):
    from confdantic.loader import load

    class Credentials(BaseModel):
        token: SecretStr = SecretStr("")

    path = tmp_path / f"config.{fmt}"
    save(Credentials(token=SecretStr("s3cret")), path)

    loaded = load(Credentials, path, formats=(fmt,), environment={})
    assert loaded.token.get_secret_value() == "s3cret"


@pytest.mark.parametrize("fmt", ["toml", "yaml", "json", "jsonc"])
def test_init_omits_secrets_in_model_collections(tmp_path: Path, fmt: str):
    class Child(BaseModel):
        password: str = config_field("sample-secret", secret=True)
        name: str = "public"

    class Config(BaseModel):
        mapping: dict[str, Child] = {"first": Child()}
        items: list[Child] = [Child()]

    path = tmp_path / f"config.{fmt}"
    init(Config, path)
    content = path.read_text()
    assert "sample-secret" not in content
    assert "password" not in content
    assert "public" in content


def test_schema_describes_sparse_config_keys_and_redacts_defaults():
    class Child(BaseModel):
        token: str = config_field("sample-secret", secret=True)
        count: int = config_field(key="n")

    class Config(BaseModel):
        child: Child
        mapping: dict[str, Child] = {"sample": Child(n=1)}

    result = schema(Config)
    assert "sample-secret" not in json.dumps(result)
    assert "required" not in result
    assert "required" not in result["$defs"]["Child"]
    assert "n" in result["$defs"]["Child"]["properties"]


@pytest.mark.parametrize("fmt", ["json", "jsonc"])
def test_init_schema_directive_loads_in_strict_mode(tmp_path: Path, fmt: str):
    from confdantic import load

    class Config(BaseModel):
        port: int = 8080

    path = tmp_path / f"config.{fmt}"
    init(Config, path, schema_url="https://example.com/config.json")
    assert load(Config, path, formats=(fmt,), strict=True, environment={}).port == 8080


def test_recursive_schema_allows_generated_template_directive(tmp_path: Path):
    class Node(BaseModel):
        model_config = ConfigDict(extra="forbid")

        count: int = config_field(1, key="n")
        child: Node | None = None

    path = tmp_path / "config.json"
    init(Node, path, schema_url="https://example.com/config.schema.json")
    document = json.loads(path.read_text())
    result = schema(Node)
    root = result["$defs"][result["$ref"].split("/")[-1]] if "$ref" in result else result

    assert root["additionalProperties"] is False
    assert set(document) <= set(root["properties"])
    assert root["properties"]["$schema"] == {"type": "string"}
    child_ref = root["properties"]["child"]["anyOf"][0]["$ref"]
    assert "$schema" not in result["$defs"][child_ref.split("/")[-1]]["properties"]


def test_dynamic_defaults_omit_runtime_secret_models(tmp_path: Path):
    class Child(BaseModel):
        token: str = config_field("sample-secret", secret=True)
        count: int = 1

    class Config(BaseModel):
        value: Any = {
            "item": Child(),
            "token": SecretStr("sample-wrapped"),
            "items": [SecretStr("sample-list"), "public"],
        }

    path = tmp_path / "config.json"
    init(Config, path)
    assert json.loads(path.read_text()) == {"value": {"item": {"count": 1}, "items": ["public"]}}


def test_schema_handles_models_with_the_same_name():
    first = create_model("Item", count=(int, config_field(1, key="n")))
    second = create_model("Item", name=(str, config_field("sample", key="label")))
    root = create_model("Config", first=(first, ...), second=(second, ...))
    result = schema(root)
    for field, key in [("first", "n"), ("second", "label")]:
        definition = result["properties"][field]["$ref"].split("/")[-1]
        assert key in result["$defs"][definition]["properties"]


def test_save_preserves_union_identity_without_running_validators(tmp_path: Path):
    class First(BaseModel):
        count: int = config_field(1, key="first_count")

    class Second(BaseModel):
        count: int = config_field(1, key="second_count")

        @field_validator("count")
        @classmethod
        def increment(cls, value: int) -> int:
            return value + 1

    class Config(BaseModel):
        item: First | Second

    config = Config(item=Second(second_count=3))
    path = tmp_path / "config.json"
    save(config, path)
    assert json.loads(path.read_text()) == {"item": {"second_count": 4}}


def test_init_includes_defaults_derived_from_other_fields(tmp_path: Path):
    class Config(BaseModel):
        count: int = 7
        doubled: int = Field(default_factory=lambda data: data["count"] * 2)

    path = tmp_path / "config.json"
    init(Config, path)
    assert json.loads(path.read_text()) == {"count": 7, "doubled": 14}


@pytest.mark.parametrize("ext", ["toml", "yaml", "json", "jsonc"])
def test_timedeltas_save_as_text_that_loads_back(tmp_path: Path, ext: str):
    class Config(BaseModel):
        timeout: timedelta = timedelta(seconds=30)
        retries: list[timedelta] = []
        interval: Duration = timedelta(seconds=30)

    path = tmp_path / f"config.{ext}"
    config = Config(
        timeout=timedelta(days=9),
        retries=[timedelta(milliseconds=1500)],
        interval=timedelta(days=9),
    )

    save(config, path)

    text = path.read_text(encoding="utf-8")
    assert "P9D" in text
    assert "PT1.5S" in text
    assert "1w2d" in text
    assert load(Config, path, formats=(ext,)) == config


def test_init_writes_plain_timedelta_default_as_iso_text(tmp_path: Path):
    class Config(BaseModel):
        timeout: timedelta = timedelta(minutes=5)

    path = tmp_path / "config.yaml"
    init(Config, path)

    assert path.read_text(encoding="utf-8").startswith("timeout: PT5M")


def test_init_and_save_refuse_to_replace_an_existing_file(tmp_path: Path):
    class Config(BaseModel):
        port: int = 1

    target = tmp_path / "config.toml"
    target.write_text("# hand written\nport = 2\n", encoding="utf-8")
    before = target.read_bytes()

    with pytest.raises(FileExistsError):
        init(Config, target)
    with pytest.raises(FileExistsError):
        save(Config(), target, overwrite=False)

    assert target.read_bytes() == before

    init(Config, target, overwrite=True)
    assert load(Config, target, formats=("toml",)).port == 1


@pytest.mark.parametrize(
    ("suffix", "marker"),
    [
        (".toml", "# Port to listen on"),
        (".yaml", "# Port to listen on"),
        (".jsonc", "// Port to listen on"),
    ],
)
def test_init_writes_descriptions_as_comments(tmp_path: Path, suffix: str, marker: str):
    class Service(BaseModel):
        port: int = Field(3000, description="Port to listen on")
        token: SecretStr = config_field(description="Access token")

    target = tmp_path / f"starter{suffix}"
    init(Service, target)

    text = target.read_text(encoding="utf-8")
    assert marker in text
    assert "token" not in text


def test_init_requires_defaults_for_non_secret_fields(tmp_path: Path):
    class Service(BaseModel):
        name: str

    target = tmp_path / "starter.toml"
    with pytest.raises(ConfdanticError):
        init(Service, target)

    assert not target.exists()


@pytest.mark.parametrize("suffix", [".toml", ".yaml", ".jsonc"])
def test_multi_line_descriptions_with_comment_markers_load_back(tmp_path: Path, suffix: str):
    description = "first line\nsecond # not a key\nthird // */ /* end"

    class Service(BaseModel):
        name: str = Field("svc", description=description)
        port: int = Field(1, description=description)

    target = tmp_path / f"config{suffix}"
    save(Service(name="other", port=2), target, comments=True)

    assert load(Service, target, formats=(suffix[1:],), environment={}) == Service(
        name="other", port=2
    )


ROUND_TRIP_TEXT = st.text(st.characters(exclude_categories=("Cs",)), max_size=20)


class RoundTrip(BaseModel):
    name: str = ""
    count: int = 0
    ratio: float = 0.0
    enabled: bool = False
    items: list[str] = []
    labels: dict[str, str] = {}


ROUND_TRIP_MODELS = st.builds(
    RoundTrip,
    name=ROUND_TRIP_TEXT,
    count=st.integers(min_value=-(2**63), max_value=2**63 - 1),
    ratio=st.floats(allow_nan=False, allow_infinity=False),
    enabled=st.booleans(),
    items=st.lists(ROUND_TRIP_TEXT, max_size=4),
    labels=st.dictionaries(ROUND_TRIP_TEXT, ROUND_TRIP_TEXT, max_size=4),
)


@pytest.mark.parametrize("suffix", [".toml", ".yaml", ".json", ".jsonc"])
def test_any_model_value_survives_save_and_load(
    tmp_path_factory: pytest.TempPathFactory, suffix: str
):
    target = tmp_path_factory.mktemp("round-trip") / f"config{suffix}"

    @settings(max_examples=150, deadline=None)
    @given(ROUND_TRIP_MODELS)
    def check(config: RoundTrip) -> None:
        save(config, target)
        assert load(RoundTrip, target, formats=(suffix[1:],), environment={}) == config

    check()


def test_save_writes_configuration_keys(tmp_path: Path):
    class Config(BaseModel):
        port: int = config_field(8080, key="http_port")

    path = tmp_path / "config.toml"
    save(Config(), path)
    assert path.read_text().splitlines() == ["http_port = 8080"]


class StarterDatabase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field("localhost", description="Database host")
    port: int = config_field(5432, key="db_port")
    password: SecretStr | None = None


class Starter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["dev", "prod"] = Field("dev", description="Run mode")
    timeout: Duration = Duration(seconds=90)
    retry: timedelta = timedelta(minutes=2)
    ratio: float = 0.5
    tags: list[str] = ["a", "b"]
    limits: dict[str, int] = {"x.y": 1}
    note: str | None = None
    token: SecretStr = config_field(description="API token")
    database: StarterDatabase = StarterDatabase()


def _starter_document(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".toml":
        return tomllib.loads(text)
    if path.suffix == ".yaml":
        from ruamel.yaml import YAML

        return YAML(typ="safe").load(text)
    return json.loads("\n".join(line.split(" //")[0] for line in text.splitlines()))


@pytest.mark.parametrize("suffix", [".toml", ".yaml", ".json", ".jsonc"])
def test_init_output_fits_the_schema_and_loads_strictly(tmp_path: Path, suffix: str):
    target = tmp_path / f"starter{suffix}"
    init(Starter, target, schema_url="https://example.com/starter.schema.json")

    jsonschema.validate(_starter_document(target), schema(Starter))
    config = load(
        Starter,
        target,
        formats=(suffix[1:],),
        strict=True,
        environment={},
        contributions=[Contribution({"token": "sample-token"})],
    )
    assert config.database == StarterDatabase()
    assert config.limits == {"x.y": 1}
