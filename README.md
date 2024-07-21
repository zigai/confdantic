# Confdantic
[![Tests](https://github.com/zigai/confdantic/actions/workflows/tests.yml/badge.svg)](https://github.com/zigai/confdantic/actions/workflows/tests.yml)
[![PyPI version](https://badge.fury.io/py/confdantic.svg)](https://badge.fury.io/py/confdantic)
![Supported versions](https://img.shields.io/badge/python-3.10+-blue.svg)
[![Downloads](https://static.pepy.tech/badge/confdantic)](https://pepy.tech/project/confdantic)
[![license](https://img.shields.io/github/license/zigai/confdantic.svg)](https://github.com/zigai/confdantic/blob/master/LICENSE)

`Confdantic` is a Python library that enhances Pydantic's capabilities for working with JSON, YAML, and TOML formats.
It preserves field descriptions as comments when serializing to YAML or TOML, making it great for generating user-friendly configuration files.

## Installation
#### From PyPi
```
pip install confdantic
```
#### From source
```
pip install git+https://github.com/zigai/confdantic.git
```
## Example
```python
from typing import Literal
from pydantic import Field
from confdantic import Confdantic

class DatabaseConfig(Confdantic):
    host: str = Field(
        "localhost",
        description="The hostname or IP address of the database server",
    )
    port: int = Field(
        5432,
        description="The port number on which the database server is listening.",
    )
    username: str
    password: str

class ApplicationConfig(Confdantic):
    debug: bool = Field(False, description="Enable debug mode")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        "INFO", description="Logging level"
    )
    database: DatabaseConfig
    allowed_hosts: list[str] = Field(
        default_factory=list,
        description="A list of host/domain names that this application can serve.",
    )

config = ApplicationConfig(
    database=DatabaseConfig(username="admin", password="secret"),
    allowed_hosts=["example.com"],
)
config.save("config.yaml", comments=True)
config.save("config.toml", comments=True)
```
`config.yaml`
```yaml
debug: false  # Enable debug mode
log_level: INFO # Logging level | choices: DEBUG, INFO, WARNING, ERROR
database:
  host: localhost  # The hostname or IP address of the database server
  port: 5432 # The port number on which the database server is listening.
  username: admin
  password: secret
allowed_hosts:  # A list of host/domain names that this application can serve.
  - example.com
```
`config.toml`
```toml
debug = false # Enable debug mode
log_level = "INFO" # Logging level | choices: DEBUG, INFO, WARNING, ERROR
allowed_hosts = ["example.com"] # A list of host/domain names that this application can serve.

[database]
host = "localhost" # The hostname or IP address of the database server
port = 5432 # The port number on which the database server is listening.
username = "admin"
password = "secret"
```
## License
[MIT License](https://github.com/zigai/confdantic/blob/master/LICENSE)
