from __future__ import annotations

import io
import json
import os
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, Never

import pytest
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    create_model,
    field_validator,
    model_validator,
)

from confdantic import (
    ConfdanticError,
    ConfigValidationError,
    Contribution,
    Duration,
    FileTooLargeError,
    InvalidEnvironmentValueError,
    InvalidKeyPathError,
    MalformedDocumentError,
    NoFormatsError,
    SourceKind,
    UnknownKeyError,
    config_edit_path,
    config_field,
    env_vars,
    load,
    load_with_metadata,
    save,
)
from confdantic.plan import get_model_plan


def config_dirs(system: Path, user: Path) -> dict[str, str]:
    """Point system and user config discovery at these folders on every platform."""
    return {
        "XDG_CONFIG_DIRS": str(system),
        "XDG_CONFIG_HOME": str(user),
        "ProgramData": str(system),
        "APPDATA": str(user),
    }


class Database(BaseModel):
    port: int = 5432


class Settings(BaseModel):
    host: str = "localhost"
    port: int = config_field(8080, key="http_port")
    database: Database = Database()


def test_config_key_uses_native_validation_alias(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text("http_port = 9000\n")
    assert Settings(http_port=1).port == 1
    assert load(Settings, path, formats=("toml",), environment={}).port == 9000
    path.write_text("port = 1\n")
    config, meta = load_with_metadata(Settings, path, formats=("toml",), environment={})
    assert config.port == 8080
    assert meta.unknown_keys[0].origin.key == ("port",)


def test_config_field_rejects_conflicting_validation_aliases():
    with pytest.raises(ValueError, match="validation_alias must match"):
        config_field(key="external", validation_alias="other")

    with pytest.raises(ValueError, match="alias must match"):
        config_field(key="external", alias="other")


def test_native_alias_and_python_name_are_available_when_enabled():
    class Config(BaseModel):
        model_config = ConfigDict(validate_by_name=True)
        count: int = config_field(key="number")

    assert Config(number=2).count == 2
    assert Config(count=3).count == 3


def test_secret_error_location_uses_the_models_native_location_policy(tmp_path: Path):
    class Config(BaseModel):
        model_config = ConfigDict(loc_by_alias=False)
        credential: int = config_field(key="protected", secret=True)
        protected: str = config_field("public", key="credential")

        @field_validator("credential", mode="before")
        @classmethod
        def reject(cls, value) -> Never:
            raise ValueError(f"rejected {value}")

    source = tmp_path / "config.json"
    source.write_text('{"protected":"sample-secret"}')
    with pytest.raises(ConfigValidationError) as failure:
        load(Config, source, formats=("json",), environment={})
    assert "sample-secret" not in str(failure.value)
    assert failure.value.errors[0].key == ("protected",)


@pytest.mark.parametrize(
    "fields",
    [
        {"port": (int, config_field(1, key="a.b"))},
        {"port": (int, config_field(1, key=""))},
        {
            "port": (int, config_field(1, key="http_port")),
            "other": (int, config_field(2, key="http_port")),
        },
    ],
    ids=["dotted", "empty", "duplicate"],
)
def test_bad_configuration_keys_are_rejected_on_use(tmp_path: Path, fields: dict[str, Any]):
    model = create_model("Bad", **fields)
    path = tmp_path / "config.toml"
    path.write_text("")

    with pytest.raises(ConfdanticError):
        load(model, path, formats=("toml",), environment={})
    with pytest.raises(ConfdanticError):
        save(model(), tmp_path / "out.toml")

    assert not (tmp_path / "out.toml").exists()


def test_plan_converts_nested_sequences_maps_and_keys():
    class Item(BaseModel):
        value: int = config_field(1, key="v")

    class Complex(BaseModel):
        items: list[Item] = Field(default_factory=list)
        labels: dict[str, str] = Field(default_factory=dict)

    plan = get_model_plan(Complex)
    assert plan.unknown_keys({"items": [{"v": 2}], "labels": {"env": "prod"}}) == []
    assert plan.to_document({"items": [{"value": 2}]})["items"] == [{"v": 2}]
    assert plan.resolve(("items", "0", "v")).name == "value"
    assert plan.known(("labels", "env"))
    assert plan.key_for_loc(("items", 0, "value")) == ("items", "0", "v")


def test_nested_list_keys_validate_from_file(tmp_path: Path):
    class Item(BaseModel):
        value: int = config_field(1, key="v")

    class Config(BaseModel):
        items: list[Item] = []

    path = tmp_path / "config.json"
    path.write_text('{"items": [{"v": 2}]}')
    assert load(Config, path, formats=("json",), environment={}).items[0].value == 2


@pytest.mark.parametrize("shape", ["lists", "maps", "mixed", "tuple"])
def test_nested_container_keys_round_trip_and_keep_secret_origins(tmp_path: Path, shape: str):
    class Item(BaseModel):
        value: int = config_field(0, key="external")
        token: str = config_field("hidden", secret=True)

    entry = {"external": 5, "token": "sample-secret"}
    shapes = {
        "lists": (list[list[Item]], [[entry]], ("values", "0", "0")),
        "maps": (dict[str, dict[str, Item]], {"a": {"b": entry}}, ("values", "a", "b")),
        "mixed": (dict[str, list[Item]], {"a": [entry]}, ("values", "a", "0")),
        "tuple": (
            tuple[list[Item], dict[str, Item]],
            [[entry], {"a": entry}],
            ("values", "1", "a"),
        ),
    }
    annotation, values, prefix = shapes[shape]
    config_type = create_model("Config", values=(annotation, ...))
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"values": values}))
    config, meta = load_with_metadata(
        config_type, source, formats=("json",), environment={}, strict=True
    )
    output = tmp_path / "output.json"
    save(config, output)
    assert json.loads(output.read_text())["values"] == values
    assert meta.where((*prefix, "token")).raw_value == "[REDACTED]"
    adapter = get_model_plan(config_type).value_adapter((*prefix, "external"))
    assert adapter is not None
    assert adapter.validate_python("7") == 7


def test_nested_container_unknown_keys_report_the_full_path(tmp_path: Path):
    class Item(BaseModel):
        value: int = config_field(0, key="external")

    class Config(BaseModel):
        values: list[dict[str, Item]]

    source = tmp_path / "config.json"
    source.write_text('{"values":[{"entry":{"externla":5}}]}')
    with pytest.raises(ConfigValidationError, match=r"values\.0\.entry\.externla"):
        load(Config, source, formats=("json",), environment={}, strict=True)


def test_unknown_python_field_names_in_lists_do_not_override_config_keys(tmp_path: Path):
    class Item(BaseModel):
        value: int = config_field(0, key="external")

    class Config(BaseModel):
        values: list[Item]

    source = tmp_path / "config.json"
    source.write_text('{"values":[{"value":5}]}')
    config, meta = load_with_metadata(Config, source, formats=("json",), environment={})
    assert config.values[0].value == 0
    assert meta.unknown_keys[0].origin.key == ("values", "0", "value")


@pytest.mark.parametrize("discriminated", [False, True])
def test_union_model_validators_run_once(tmp_path: Path, discriminated: bool):
    class First(BaseModel):
        kind: Literal["first"]
        count: int

        @model_validator(mode="after")
        def increment(self):
            self.count += 1
            return self

    class Second(BaseModel):
        kind: Literal["second"]
        count: int

    annotation = (
        Annotated[First | Second, Field(discriminator="kind")] if discriminated else First | Second
    )
    config_type = create_model("Config", value=(annotation, ...))
    source = tmp_path / "config.json"
    source.write_text('{"value":{"kind":"first","count":1}}')
    assert load(config_type, source, formats=("json",), environment={}).value.count == 2


def test_discriminated_union_models_can_share_their_name(tmp_path: Path):
    first = create_model(
        "Choice",
        kind=(Literal["first"], config_field(key="type")),
        count=(int, config_field(key="number")),
    )
    second = create_model(
        "Choice",
        kind=(Literal["second"], config_field(key="type")),
        name=(str, ...),
    )
    config_type = create_model(
        "Config", value=(Annotated[first | second, Field(discriminator="kind")], ...)
    )
    source = tmp_path / "config.json"
    for value, expected_type in [
        ({"type": "first", "number": 7}, first),
        ({"type": "second", "name": "text"}, second),
    ]:
        source.write_text(json.dumps({"value": value}))
        loaded = load(config_type, source, formats=("json",), environment={}, strict=True)
        assert isinstance(loaded.value, expected_type)


def test_defaults_have_origins_and_do_not_change_model_equality():
    config, meta = load_with_metadata(Settings, environment={})
    assert config == Settings()
    assert meta.where("host").source is SourceKind.DEFAULT
    assert meta.where("http_port").source is SourceKind.DEFAULT
    assert meta.where("database.port").source is SourceKind.DEFAULT
    assert [origin.key for origin in meta.origins()] == [
        ("host",),
        ("http_port",),
        ("database", "port"),
    ]


def test_sequence_origins_follow_parent_in_declaration_order():
    class Config(BaseModel):
        tags: list[str] = ["a", "b"]
        host: str = "local"

    _, meta = load_with_metadata(Config, environment={})
    assert tuple(meta) == (("tags",), ("tags", "0"), ("tags", "1"), ("host",))


def test_default_factory_runs_once_per_load():
    calls: list[int] = []

    def factory() -> list[str]:
        calls.append(1)
        return ["ready"]

    class Factory(BaseModel):
        tags: list[str] = Field(default_factory=factory)

    config = load(Factory, environment={})
    assert config.tags == ["ready"]
    assert calls == [1]


def test_file_layer_precedence_and_metadata(tmp_path: Path):
    system = tmp_path / "system" / "app"
    user = tmp_path / "user" / "app"
    system.mkdir(parents=True)
    user.mkdir(parents=True)
    (system / "config.toml").write_text('host = "system"\nhttp_port = 1\n')
    (user / "config.toml").write_text('host = "user"\n')
    explicit = tmp_path / "explicit.toml"
    explicit.write_text("http_port = 2\n")
    environment = {
        **config_dirs(tmp_path / "system", tmp_path / "user"),
        "APP_DATABASE__PORT": "3",
    }
    config, meta = load_with_metadata(
        Settings,
        explicit,
        app_name="app",
        formats=("toml", "yaml"),
        env_prefix="APP",
        environment=environment,
        contributions=(Contribution({"http_port": 4}, paths={"http_port": "--port"}),),
    )
    assert (config.host, config.port, config.database.port) == ("user", 4, 3)
    assert [meta.where(key).source for key in ("host", "http_port", "database.port")] == [
        SourceKind.USER,
        SourceKind.FLAG,
        SourceKind.ENV,
    ]
    assert meta.where("http_port").location() == "flag --port"
    assert meta.active_files == (
        str(system / "config.toml"),
        str(user / "config.toml"),
        str(explicit),
    )


def test_missing_ok_skips_missing_file_but_malformed_file_fails(tmp_path: Path):
    path = tmp_path / "missing.toml"
    assert load(Settings, path, missing_ok=True, formats=("toml",), environment={}) == Settings()

    with pytest.raises(FileNotFoundError):
        load(Settings, path, formats=("toml",), environment={})

    path.write_text("invalid = [\n")
    with pytest.raises(MalformedDocumentError):
        load(Settings, path, missing_ok=True, formats=("toml",), environment={})

    with pytest.raises(NoFormatsError):
        load(Settings, path, environment={})


def test_unknown_keys_suggestions_strict_and_unrelated(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text("http_prot = 1\nzxyq = 2\n")
    _, meta = load_with_metadata(Settings, path, formats=("toml",), environment={})
    assert [(item.origin.key, item.suggestion) for item in meta.unknown_keys] == [
        (("http_prot",), ("http_port",)),
        (("zxyq",), None),
    ]

    with pytest.raises(ConfigValidationError) as e:
        load(Settings, path, formats=("toml",), strict=True, environment={})

    assert len(e.value.errors) == 2


def test_extra_allow_accepts_unknown_and_forbid_rejects(tmp_path: Path):
    class Allow(BaseModel):
        model_config = ConfigDict(extra="allow")
        port: int = 1

    class Forbid(BaseModel):
        model_config = ConfigDict(extra="forbid")
        port: int = 1

    path = tmp_path / "config.toml"
    path.write_text("extra = 2\n")
    instance, meta = load_with_metadata(Allow, path, formats=("toml",), environment={})
    assert instance.extra == 2
    assert not meta.unknown_keys

    with pytest.raises(UnknownKeyError):
        load(Forbid, path, formats=("toml",), environment={})


def test_yaml_merge_anchor_is_not_unknown(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("base: &b\n  port: 5433\ndatabase:\n  <<: *b\n")
    config, meta = load_with_metadata(Settings, path, formats=("yaml",), environment={})
    assert config.database.port == 5433
    assert not meta.unknown_keys


def test_yaml_mappings_no_merge_uses_are_unknown(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("plain:\n  port: 1\nunused: &u\n  port: 2\n")
    _, meta = load_with_metadata(Settings, path, formats=("yaml",), environment={})
    assert [item.origin.key for item in meta.unknown_keys] == [("plain",), ("unused",)]


def test_environment_is_prefix_gated_and_explicit_names_take_priority():
    class Env(BaseModel):
        port: int = config_field(1, key="http_port")
        token: SecretStr | None = config_field(None, env="API_KEY")
        database: Database = Database()

    environment = {
        "HTTP_PORT": "2",
        "DB_PORT": "3",
        "API_KEY": "hidden",
        "APP_HTTP_PORT": "4",
        "APP_DATABASE__PORT": "5",
    }
    unprefixed = load(Env, environment=environment)
    assert unprefixed.port == 1
    assert unprefixed.database.port == 5432
    assert unprefixed.token.get_secret_value() == "hidden"
    prefixed = load(Env, env_prefix="APP", environment=environment)
    assert prefixed.port == 4
    assert prefixed.database.port == 5
    assert [(var.key, var.name, var.secret) for var in env_vars(Env, "APP")] == [
        (("http_port",), "APP_HTTP_PORT", False),
        (("token",), "APP_API_KEY", True),
        (("database", "port"), "APP_DATABASE_PORT", False),
    ]
    assert [var.name for var in env_vars(Env)] == ["API_KEY"]

    prefixed = load(
        Env,
        env_prefix="APP",
        environment={"APP_API_KEY": "preferred", "API_KEY": "exact", "APP_TOKEN": "derived"},
    )
    assert prefixed.token.get_secret_value() == "preferred"
    derived = load(Env, env_prefix="APP", environment={"APP_TOKEN": "derived"})
    assert derived.token.get_secret_value() == "derived"


def test_disabled_environment_subtree():
    class Disabled(BaseModel):
        database: Database = config_field(default_factory=Database, env=False)

    assert (
        load(Disabled, env_prefix="APP", environment={"APP_DATABASE__PORT": "1"}).database.port
        == 5432
    )


def test_invalid_env_value_reports_source_without_secret():
    class Secret(BaseModel):
        password: int = config_field(1, env="PASSWORD", secret=True)

    with pytest.raises(InvalidEnvironmentValueError) as e:
        load(Secret, environment={"PASSWORD": "hidden-value"})

    assert "PASSWORD" in str(e.value)
    assert "hidden-value" not in str(e.value)


def test_validation_attributes_each_error_and_missing_has_no_origin(tmp_path: Path):
    class Required(BaseModel):
        port: int
        count: int
        host: str

    path = tmp_path / "config.toml"
    path.write_text('port = "bad"\ncount = "bad"\n')
    with pytest.raises(ConfigValidationError) as e:
        load(Required, path, formats=("toml",), environment={})

    assert e.value.errors[0].key == ("port",)
    assert str(path) in str(e.value)
    path.write_text("port = 1\ncount = 2\n")
    with pytest.raises(ConfigValidationError) as e:
        load(Required, path, formats=("toml",), environment={})

    assert e.value.errors[0].key == ("host",)
    assert e.value.errors[0].origin is None


def test_validate_config_error_and_exception_group():
    class Limited(BaseModel):
        port: int = 8080

        def validate_config(self, meta):
            raise meta.error("port", "too high")

    with pytest.raises(ConfigValidationError) as e:
        load(Limited, environment={"APP_PORT": "9090"}, env_prefix="APP")

    assert "too high for port" in str(e.value)
    assert "APP_PORT" in str(e.value)

    class Several(BaseModel):
        port: int = 1

        def validate_config(self, meta):
            raise ExceptionGroup("invalid", [meta.error("port", "one"), meta.error("port", "two")])

    with pytest.raises(ConfigValidationError) as e:
        load(Several, environment={})

    assert len(e.value.errors) == 2


def test_contributions_reject_unknown_and_colliding_keys():
    with pytest.raises(UnknownKeyError):
        load(Settings, contributions=[Contribution({"prot": 2})], environment={})

    with pytest.raises(InvalidKeyPathError, match="sets both"):
        load(
            Settings,
            contributions=[Contribution({"database": {}, "database.port": 1})],
            environment={},
        )


def test_nested_contributions_track_leaf_sources():
    config, meta = load_with_metadata(
        Settings,
        contributions=[Contribution({"database": {"port": 7}}, paths={"database": "--database"})],
        environment={},
    )
    assert config.database.port == 7
    origin = meta.where("database.port")
    assert (origin.source, origin.path) == (SourceKind.FLAG, "--database")

    with pytest.raises(UnknownKeyError):
        load(Settings, contributions=[Contribution({"database": {"prot": 7}})], environment={})


@pytest.mark.parametrize("unset", [{}, {"XDG_CONFIG_HOME": "", "APPDATA": ""}])
def test_user_file_defaults_to_the_home_config_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unset: dict[str, str]
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    base = tmp_path / "AppData" / "Roaming" if os.name == "nt" else tmp_path / ".config"
    (base / "app").mkdir(parents=True)
    (base / "app" / "config.toml").write_text('host = "home"\n')

    config = load(Settings, app_name="app", formats=("toml",), environment=unset)

    assert config.host == "home"


def test_config_edit_path_selection(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    expected = tmp_path / "app" / "config.yaml"
    assert config_edit_path(app_name="app", formats=("yaml", "toml")) == expected
    expected.parent.mkdir()
    expected.write_text("host: local\n")
    assert config_edit_path(app_name="app", formats=("yaml", "toml")) == expected
    assert config_edit_path(tmp_path / "other.toml") == tmp_path / "other.toml"

    with pytest.raises(ValueError, match="standard input cannot be edited"):
        config_edit_path("-")


class Tagged(BaseModel):
    tags: list[str] | None = None
    labels: dict[str, int] = Field(default_factory=dict)
    items: list[Database] = Field(default_factory=list)


def test_optional_list_is_parsed_from_csv_environment():
    config = load(Tagged, env_prefix="APP", environment={"APP_TAGS": "a, b"})

    assert config.tags == ["a", "b"]


def test_env_vars_skip_fields_one_string_cannot_hold():
    assert [variable.name for variable in env_vars(Tagged, "APP")] == ["APP_TAGS"]


def test_default_factory_receives_earlier_defaults():
    class Derived(BaseModel):
        base: int = 1
        doubled: int = Field(default_factory=lambda data: data["base"] * 2)

    assert load(Derived, environment={}).doubled == 2


def test_default_factory_receives_resolved_validated_fields():
    class Config(BaseModel):
        count: int = 1
        doubled: int = Field(default_factory=lambda data: data["count"] * 2)

    config, meta = load_with_metadata(Config, env_prefix="APP", environment={"APP_COUNT": "7"})
    assert config.doubled == 14
    assert meta.where("doubled").source is SourceKind.DEFAULT


def test_union_error_key_skips_the_variant_name(tmp_path: Path):
    class First(BaseModel):
        count: int = config_field(key="n")

    class Second(BaseModel):
        name: str = config_field(key="label")

    class Holder(BaseModel):
        part: First | Second

    path = tmp_path / "config.toml"
    path.write_text("[part]\nn = 'x'\n")
    with pytest.raises(ConfigValidationError) as failure:
        load(Holder, path, formats=("toml",), environment={})

    assert ("part", "n") in [error.key for error in failure.value.errors]


def test_origin_location_and_error_source_forms(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("host: file\n")
    _, meta = load_with_metadata(
        Settings,
        path,
        formats=("yaml",),
        env_prefix="APP",
        environment={"APP_HTTP_PORT": "1"},
        contributions=[Contribution({"database.port": 2}, paths={"database.port": "--db-port"})],
    )

    assert meta.where("host").location() == f"file {path}:1"
    assert meta.where("http_port").location() == "env APP_HTTP_PORT"
    assert meta.where("database.port").location() == "flag --db-port"
    assert str(meta.error("http_port", "too high")).endswith(
        "\n  --> set by environment variable: APP_HTTP_PORT"
    )


def test_environment_parsing_follows_pydantic_and_trims_like_strata():
    class Flags(BaseModel):
        debug: bool = False
        count: int = 0
        name: str = ""

    environment = {"APP_DEBUG": " Y ", "APP_COUNT": " 7 ", "APP_NAME": "  padded  "}
    config = load(Flags, env_prefix="APP", environment=environment)

    assert (config.debug, config.count, config.name) == (True, 7, "  padded  ")


@pytest.mark.parametrize("source", ["json", "env", "flag"])
def test_loading_runs_pydantic_validators_on_raw_values(tmp_path: Path, source: str):
    class Config(BaseModel):
        count: int = 0

        @field_validator("count", mode="before")
        @classmethod
        def parse_count(cls, value: int | str) -> int | str:
            return 7 if value == "seven" else value

    path = tmp_path / "config.json"
    path.write_text('{"count": "seven"}')
    options = {
        "json": {"path": path, "formats": ("json",)},
        "env": {"env_prefix": "APP", "environment": {"APP_COUNT": "seven"}},
        "flag": {"contributions": [Contribution({"count": "seven"})]},
    }
    assert load(Config, **options[source]).count == 7


@pytest.mark.parametrize("discriminated", [False, True])
def test_union_selection_uses_model_validation(tmp_path: Path, discriminated: bool):
    class First(BaseModel):
        model_config = ConfigDict(extra="forbid")
        kind: Literal["first"]
        first: int = config_field(key="value")

    class Second(BaseModel):
        model_config = ConfigDict(extra="forbid")
        kind: Literal["second"]
        second: int = config_field(key="value")

    annotation = (
        Annotated[First | Second, Field(discriminator="kind")] if discriminated else First | Second
    )

    class Config(BaseModel):
        item: annotation

    path = tmp_path / "config.json"
    path.write_text('{"item": {"kind": "second", "value": 7}}')
    config = load(Config, path, formats=("json",), environment={})
    assert isinstance(config.item, Second)
    assert config.item.second == 7


def test_model_validator_can_transform_document_before_field_validation(tmp_path: Path):
    class Config(BaseModel):
        count: int

        @model_validator(mode="before")
        @classmethod
        def parse_count(cls, value: dict[str, int | str]) -> dict[str, int | str]:
            return {**value, "count": 7} if value["count"] == "seven" else value

    path = tmp_path / "config.json"
    path.write_text('{"count": "seven"}')
    assert load(Config, path, formats=("json",), environment={}).count == 7


def test_secret_origins_cover_collections_and_inherited_env(tmp_path: Path):
    class Child(BaseModel):
        password: str = config_field("sample-default", secret=True)

    class Public(BaseModel):
        value: str = ""

    class Config(BaseModel):
        mapping: dict[str, Child] = {"first": Child()}
        items: list[Child] = [Child()]
        parent: Public = config_field(default_factory=Public, secret=True)

    path = tmp_path / "config.json"
    path.write_text(
        '{"mapping": {"first": {"password": "sample-file"}}, "items": [{"password": "sample-file"}]}'
    )
    _, meta = load_with_metadata(
        Config,
        path,
        formats=("json",),
        env_prefix="APP",
        environment={"APP_PARENT__VALUE": "sample-env"},
    )
    assert "sample-" not in repr(meta)
    assert meta.where("mapping.first.password").raw_value == "[REDACTED]"
    assert meta.where("items.0.password").raw_value == "[REDACTED]"
    assert meta.where("parent.value").raw_value == "[REDACTED]"
    assert next(
        variable for variable in env_vars(Config, "APP") if variable.key == ("parent", "value")
    ).secret


def test_secret_validator_messages_are_not_exposed(tmp_path: Path):
    class Config(BaseModel):
        token: int = config_field(secret=True)

        @field_validator("token")
        @classmethod
        def reject(cls, value: int) -> Never:
            raise ValueError(f"rejected sample-secret-{value}")

    path = tmp_path / "config.json"
    path.write_text('{"token": 7}')
    with pytest.raises(ConfigValidationError) as failure:
        load(Config, path, formats=("json",), environment={})

    assert "sample-secret" not in str(failure.value)
    assert failure.value.__context__ is None


def test_dynamic_map_keys_keep_exact_origins(tmp_path: Path):
    class Config(BaseModel):
        labels: dict[str, str]

    path = tmp_path / "config.yaml"
    path.write_text("labels:\n  foo_bar: one\n  foobar: two\n  Env: three\n  env: four\n")
    config, meta = load_with_metadata(Config, path, formats=("yaml",), environment={})
    for key, value in config.labels.items():
        assert meta.where(f"labels.{key}").raw_value == value


def test_secret_validation_hook_does_not_chain_rejected_values():
    class Config(BaseModel):
        token: SecretStr = SecretStr("sample-secret")

        def validate_config(self, meta):
            raise ValueError(f"rejected {self.token.get_secret_value()}")

    with pytest.raises(ConfigValidationError) as failure:
        load(Config, environment={})

    assert "sample-secret" not in str(failure.value)
    assert failure.value.__cause__ is None
    assert failure.value.__context__ is None


def test_discriminated_union_requires_its_config_discriminator(tmp_path: Path):
    class First(BaseModel):
        kind: Literal["first"] = config_field("first", key="type")
        count: int = 1

    class Second(BaseModel):
        kind: Literal["second"] = config_field("second", key="type")
        count: int = 2

    class Config(BaseModel):
        item: Annotated[First | Second, Field(discriminator="kind")]

    path = tmp_path / "config.json"
    path.write_text('{"item": {"count": 7}}')
    with pytest.raises(ConfigValidationError):
        load(Config, path, formats=("json",), environment={})

    path.write_text('{"item": {"type": "second", "count": 7}}')
    assert isinstance(load(Config, path, formats=("json",), environment={}).item, Second)


DOTTED_KEY_DOCUMENTS = {
    "json": '{"labels": {"a.b": 1, "a": {"b": 2}}}',
    "yaml": 'labels:\n  "a.b": 1\n  a:\n    b: 2\n',
    "toml": '[labels]\n"a.b" = 1\n\n[labels.a]\nb = 2\n',
}


@pytest.mark.parametrize("fmt", DOTTED_KEY_DOCUMENTS)
def test_literal_dot_in_map_key_is_kept_apart_from_nesting(tmp_path: Path, fmt: str):
    class Config(BaseModel):
        labels: dict[str, Any] = {}

    path = tmp_path / f"config.{fmt}"
    path.write_text(DOTTED_KEY_DOCUMENTS[fmt], encoding="utf-8")

    config, meta = load_with_metadata(Config, path, formats=(fmt,))

    assert config.labels == {"a.b": 1, "a": {"b": 2}}
    assert meta["labels", "a.b"].raw_value == "1"
    assert meta["labels", "a", "b"].raw_value == "2"
    assert meta.where("labels.a\\.b") is meta["labels", "a.b"]


def test_secret_under_dotted_map_key_is_redacted(tmp_path: Path):
    class Entry(BaseModel):
        password: int = config_field(0, secret=True)

    class Config(BaseModel):
        entries: dict[str, Entry] = {}

    path = tmp_path / "config.yaml"
    path.write_text('entries:\n  "a.b":\n    password: abc123\n', encoding="utf-8")

    with pytest.raises(ConfigValidationError) as caught:
        load(Config, path, formats=("yaml",))

    assert "abc123" not in str(caught.value)
    assert caught.value.key == ("entries", "a.b", "password")


def test_contribution_keys_use_escaped_dots():
    class Config(BaseModel):
        labels: dict[str, str] = {}

    contribution = Contribution(
        {"labels.a\\.b": "x", ("labels", "c.d"): "y"}, paths={("labels", "c.d"): "--label"}
    )

    config, meta = load_with_metadata(Config, contributions=[contribution])

    assert config.labels == {"a.b": "x", "c.d": "y"}
    assert meta["labels", "c.d"].path == "--label"


def test_unknown_key_message_escapes_the_key(tmp_path: Path):
    class Config(BaseModel):
        port: int = 0

    path = tmp_path / "config.json"
    path.write_text('{"po\\"rt": 1}', encoding="utf-8")

    with pytest.raises(
        ConfigValidationError, match=r'unknown key "po\\"rt" \(did you mean "port"\?\)'
    ):
        load(Config, path, formats=("json",), strict=True)


@pytest.mark.parametrize(
    ("fmt", "document"),
    [("toml", 'timeout = "1w2d"\n'), ("yaml", "timeout: 1w2d\n"), ("json", '{"timeout": "1w2d"}')],
)
def test_duration_loads_day_and_week_units(tmp_path: Path, fmt: str, document: str):
    class Config(BaseModel):
        timeout: Duration = timedelta(seconds=30)

    path = tmp_path / f"config.{fmt}"
    path.write_text(document, encoding="utf-8")

    assert load(Config, path, formats=(fmt,)).timeout == timedelta(days=9)


def test_duration_reads_units_from_environment():
    class Nested(BaseModel):
        retry: Duration | None = None

    class Config(BaseModel):
        timeout: Duration = timedelta(seconds=30)
        nested: Nested = Nested()

    config = load(
        Config, env_prefix="APP_", environment={"APP_TIMEOUT": "2w", "APP_NESTED__RETRY": "90m"}
    )

    assert config.timeout == timedelta(weeks=2)
    assert config.nested.retry == timedelta(minutes=90)


def test_plain_timedelta_keeps_pydantic_parsing(tmp_path: Path):
    class Config(BaseModel):
        timeout: timedelta = timedelta(seconds=30)

    path = tmp_path / "config.toml"
    path.write_text('timeout = "P9D"\n', encoding="utf-8")
    assert load(Config, path, formats=("toml",)).timeout == timedelta(days=9)

    path.write_text('timeout = "1w2d"\n', encoding="utf-8")
    with pytest.raises(ConfigValidationError) as caught:
        load(Config, path, formats=("toml",))

    assert caught.value.key == ("timeout",)


@pytest.mark.parametrize(
    ("layers", "expected"),
    [
        ((), "localhost"),
        (("system",), "system"),
        (("system", "user"), "user"),
        (("system", "user", "explicit"), "explicit"),
        (("system", "user", "explicit", "env"), "env"),
        (("system", "user", "explicit", "env", "first-flag"), "first-flag"),
        (("system", "user", "explicit", "env", "first-flag", "second-flag"), "second-flag"),
    ],
)
def test_each_layer_overrides_the_layers_below(
    tmp_path: Path, layers: tuple[str, ...], expected: str
):
    system = tmp_path / "system" / "app"
    user = tmp_path / "user" / "app"
    system.mkdir(parents=True)
    user.mkdir(parents=True)
    explicit = tmp_path / "explicit.toml"
    if "system" in layers:
        (system / "config.toml").write_text('host = "system"\n')
    if "user" in layers:
        (user / "config.toml").write_text('host = "user"\n')
    if "explicit" in layers:
        explicit.write_text('host = "explicit"\n')
    environment = {
        **config_dirs(tmp_path / "system", tmp_path / "user"),
    }
    if "env" in layers:
        environment["APP_HOST"] = "env"
    contributions = [
        Contribution({"host": name}) for name in ("first-flag", "second-flag") if name in layers
    ]

    config = load(
        Settings,
        explicit,
        missing_ok=True,
        app_name="app",
        formats=("toml",),
        env_prefix="APP",
        environment=environment,
        contributions=contributions,
    )

    assert config.host == expected


def test_nested_tables_merge_across_files_and_lists_replace(tmp_path: Path):
    class Server(BaseModel):
        host: str = "default-host"
        port: int = 0
        tags: list[str] = []

    class Config(BaseModel):
        server: Server = Server()

    system = tmp_path / "system" / "app"
    user = tmp_path / "user" / "app"
    system.mkdir(parents=True)
    user.mkdir(parents=True)
    (system / "config.toml").write_text('[server]\nhost = "system-host"\ntags = ["a", "b"]\n')
    (user / "config.toml").write_text('[server]\nport = 2\ntags = ["c"]\n')
    environment = {
        **config_dirs(tmp_path / "system", tmp_path / "user"),
    }

    config, meta = load_with_metadata(
        Config, app_name="app", formats=("toml",), environment=environment
    )

    assert config.server == Server(host="system-host", port=2, tags=["c"])
    assert meta.where("server.tags.0").source is SourceKind.USER
    assert meta.where("server.tags.1") is None


@pytest.mark.parametrize(
    ("formats", "expected"),
    [(("yaml", "toml"), "from-yaml"), (("toml", "yaml"), "from-toml")],
)
def test_discovery_picks_the_first_selected_format(
    tmp_path: Path, formats: tuple[str, ...], expected: str
):
    user = tmp_path / "user" / "app"
    user.mkdir(parents=True)
    (user / "config.toml").write_text('host = "from-toml"\n')
    (user / "config.yaml").write_text("host: from-yaml\n")

    config = load(
        Settings,
        app_name="app",
        formats=formats,
        environment=config_dirs(tmp_path / "system", tmp_path / "user"),
    )

    assert config.host == expected


def test_max_file_size_limits_files_and_stdin(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_bytes(b'host = "x"\n')
    size = path.stat().st_size

    assert load(Settings, path, formats=("toml",), max_file_size=size, environment={}).host == "x"

    with pytest.raises(FileTooLargeError):
        load(Settings, path, formats=("toml",), max_file_size=size - 1, environment={})

    with pytest.raises(FileTooLargeError):
        load(
            Settings,
            "-",
            formats=("toml",),
            max_file_size=size - 1,
            stdin=io.StringIO('host = "x"\n'),
            environment={},
        )


@pytest.mark.parametrize(
    ("set_names", "expected"),
    [
        (("APP_DATABASE__PORT",), 1),
        (("APP_DATABASE_PORT",), 2),
        (("APP_DATABASE__PORT", "APP_DATABASE_PORT"), 1),
    ],
)
def test_prefixed_names_accept_single_underscore_and_prefer_double(
    set_names: tuple[str, ...], expected: int
):
    values = {"APP_DATABASE__PORT": "1", "APP_DATABASE_PORT": "2"}
    environment = {name: values[name] for name in set_names}

    config, meta = load_with_metadata(Settings, env_prefix="APP", environment=environment)

    assert config.database.port == expected
    origin = meta.where("database.port")
    assert origin is not None
    assert origin.path == next(name for name, value in values.items() if value == str(expected))


@pytest.mark.parametrize(
    ("annotation", "raw"),
    [(SecretStr | int, "[1]"), (int | bool, '"x"'), (list[int] | int, '"x"')],
    ids=["secret-or-int", "int-or-bool", "list-or-int"],
)
def test_scalar_union_errors_use_the_field_key(tmp_path: Path, annotation: Any, raw: str):
    model = create_model("Holder", value=(annotation, 0))
    path = tmp_path / "config.toml"
    path.write_text(f"value = {raw}\n")

    with pytest.raises(ConfigValidationError) as failure:
        load(model, path, formats=("toml",), environment={})

    assert {error.key for error in failure.value.errors} == {("value",)}
    assert "lax-or-strict" not in str(failure.value)


def test_validation_message_matches_the_documented_layout(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('[database]\nport = "abc"\n')

    with pytest.raises(ConfigValidationError) as failure:
        load(Settings, path, formats=("toml",), environment={})

    assert str(failure.value) == (
        "config error: Input should be a valid integer, unable to parse string as an integer "
        f'for database.port: "abc"\n  --> set by {path}'
    )


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("config.toml", '[database]\nport = "a\\"b\\nc"\n'),
        ("config.yaml", 'database:\n  port: "a\\"b\\nc"\n'),
        ("config.json", '{"database": {"port": "a\\"b\\nc"}}'),
        ("config.jsonc", '{"database": {"port": "a\\"b\\nc"}}'),
    ],
)
def test_validation_message_shows_the_raw_value_escaped_once(
    tmp_path: Path, name: str, content: str
):
    path = tmp_path / name
    path.write_text(content)

    with pytest.raises(ConfigValidationError) as failure:
        load(Settings, path, formats=("toml", "yaml", "json", "jsonc"), environment={})

    first_line = str(failure.value).splitlines()[0]
    assert first_line.endswith('for database.port: "a\\"b\\nc"')


@pytest.mark.parametrize(
    ("name", "content", "raw"),
    [
        ("config.toml", "[database]\nport = [1, 2]\n", "[1, 2]"),
        ("config.json", '{"database": {"port": [1, 2]}}', "[1, 2]"),
        ("config.toml", "[database]\nport = 1.5\n", "1.5"),
        ("config.json", '{"database": {"port": 1.5}}', "1.5"),
    ],
)
def test_validation_message_shows_non_string_raw_values_as_written(
    tmp_path: Path, name: str, content: str, raw: str
):
    path = tmp_path / name
    path.write_text(content)

    with pytest.raises(ConfigValidationError) as failure:
        load(Settings, path, formats=("toml", "json"), environment={})

    assert str(failure.value).splitlines()[0].endswith(f'for database.port: "{raw}"')


def test_derived_names_are_ignored_without_a_prefix():
    config = load(Settings, environment={"HOST": "other", "DATABASE_PORT": "1", "HTTP_PORT": "2"})
    assert config == Settings()


def test_metadata_is_keyed_by_configuration_keys(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text("http_port = 1\n")
    _, meta = load_with_metadata(Settings, path, formats=("toml",), environment={})
    assert meta.where("http_port").source is SourceKind.FILE
    assert meta.where("port") is None


def test_validate_config_keeps_a_plain_exception_message():
    class Limited(BaseModel):
        port: int = 8080

        def validate_config(self, meta: object) -> None:
            raise ValueError("port too high")

    with pytest.raises(ConfigValidationError, match="port too high"):
        load(Limited, environment={})
