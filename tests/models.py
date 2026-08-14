from pydantic import Field

from confdantic import Confdantic


class ExampleModel(Confdantic):
    name: str
    age: int
    hobbies: list[str] = Field(default_factory=list)
    address: str | None = None


__all__ = ["ExampleModel"]
