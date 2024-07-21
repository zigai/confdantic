# Confen

## Installation
#### From PyPi
```
pip install 
```
#### From source
```
pip install git+https://github.com/zigai/.git
```

```python
from typing import Literal
from pydantic import Field
from confen import Confen

class DatabaseConfig(Confen):
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

class ApplicationConfig(Confen):
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
