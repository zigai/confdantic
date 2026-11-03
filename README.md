# confdantic

**Configuration for Pydantic that remembers where every value came from.**

confdantic fills a Pydantic model from config files, environment variables, and CLI flags, and tells you where each value came from.

[![Tests](https://img.shields.io/github/actions/workflow/status/zigai/confdantic/tests.yml?branch=master&label=Tests)](https://github.com/zigai/confdantic/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/confdantic?color=blue)](https://pypi.org/project/confdantic/)
[![License](https://img.shields.io/github/license/zigai/confdantic)](LICENSE)

- **One model:** Pydantic validates every layer.
- **Layers:** files, environment variables, and flags each override the one below.
- **Provenance:** ask which file set any value.
- **Safe edits:** change a key and keep the file's comments.

## Installation

```sh
uv add confdantic
```

Requires Python 3.11 or newer.

## Quick start

```python
from pydantic import BaseModel

from confdantic import load


class Config(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8080


config = load(Config, "config.toml", formats=("toml",))
print(f"listening on {config.host}:{config.port}")
```

With `port = 9000` in `config.toml`:

```text
listening on 127.0.0.1:9000
```

## How the layers stack

```text
  CLI flags        contributions=[...]   ← wins
  Environment      MYAPP_PORT=9050
  Config file      ./config.toml
  User file        ~/.config/myapp/config.toml
  System file      /etc/xdg/myapp/config.toml
  Defaults         the model's defaults  ← fallback
```

Each layer is one argument:

```python
from confdantic import Contribution

flags = Contribution({"port": 9100}, paths={"port": "--port"})
config = load(
    Config, app_name="myapp", formats=("toml",), env_prefix="MYAPP", contributions=[flags]
)
```

## Where did this value come from?

```python
from confdantic import load_with_metadata

config, meta = load_with_metadata(Config, app_name="myapp", formats=("toml",))
print(meta.where("port").location())
# User /home/you/.config/myapp/config.toml

for unknown in meta.unknown_keys:
    print(unknown)
# unknown key "prot" (did you mean "port"?)
```

Pass `strict=True` to make unknown keys an error.

## Checking values

For checks across fields, add `validate_config`. Errors point at the layer that set the key:

```python
from confdantic import Metadata


class Config(BaseModel):
    port: int = 8080

    def validate_config(self, meta: Metadata) -> None:
        if self.port > 9000:
            raise meta.error("port", "above the privileged ceiling")
```

```text
config error: above the privileged ceiling for port: "9090"
  --> set by /home/you/project/config.toml
```

## Editing config files

`edit` loads one file into your model and writes back only what you change, keeping comments:

```python
from confdantic import edit

with edit("config.toml", Config) as config:
    config.database.port = 5433
```

`save(config, "state.json")` writes a whole model, and `init(Config, "config.toml")` writes a starter file with your defaults. For a `config set` command, `set_key("config.toml", "database.port", "5433", model=Config)` takes the key and value as typed.

## Fields

```python
from pydantic import SecretStr

from confdantic import Duration, config_field


class Config(BaseModel):
    database_url: str = config_field("sqlite://", key="db_url")
    api_key: SecretStr = config_field(env="API_KEY")
    timeout: Duration = Duration(seconds=30)  # "30s", "1h30m", "2w"
```

Secrets show as `[REDACTED]` in origins and errors.

## License

[MIT](LICENSE)
