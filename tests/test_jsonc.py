import json
from typing import Literal

from pydantic import BaseModel, Field

from confdantic import Confdantic
from confdantic.formats.jsonc import insert_jsonc_comments
from tests.models import ExampleModel


def test_load_jsonc(tmp_path, sample_data):
    filepath = tmp_path / "test.jsonc"
    content = """{
        // This is a comment
        "name": "John Doe",
        "age": 30, /* inline comment */
        "hobbies": ["reading", "cycling"],
        "address": "123 Main St"
    }"""
    filepath.write_text(content)

    model = ExampleModel.load(str(filepath))
    assert model.model_dump() == sample_data


def test_load_json5(tmp_path, sample_data):
    filepath = tmp_path / "test.json5"
    content = """{
        "name": "John Doe",
        "age": 30,
        "hobbies": ["reading", "cycling"],
        "address": "123 Main St"
    }"""
    filepath.write_text(content)

    model = ExampleModel.load(str(filepath))
    assert model.model_dump() == sample_data


def test_save_jsonc_with_comments(tmp_path, sample_data):
    name_description = "The person's name"
    age_description = "The person's age"

    class CommentedModel(Confdantic):
        name: str = Field(..., description=name_description)
        age: int = Field(..., description=age_description)

    model = CommentedModel(**sample_data)
    filepath = tmp_path / "test.jsonc"
    model.save(str(filepath), comments=True)

    content = filepath.read_text()
    assert name_description in content
    assert age_description in content
    assert "//" in content


def test_save_jsonc_without_comments(tmp_path, sample_data):
    class CommentedModel(Confdantic):
        name: str = Field(..., description="A description")
        age: int = Field(..., description="Another description")

    model = CommentedModel(**sample_data)
    filepath = tmp_path / "test.jsonc"
    model.save(str(filepath), comments=False)

    content = filepath.read_text()
    assert "//" not in content
    json.loads(content)


def test_save_jsonc_comment_position_above(tmp_path, sample_data):
    class CommentedModel(Confdantic):
        name: str = Field(..., description="Name field")
        age: int = Field(..., description="Age field")

    model = CommentedModel(**sample_data)
    filepath = tmp_path / "test.jsonc"
    model.save(str(filepath), comments=True, comment_position="above_field")

    content = filepath.read_text()
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if "// Name field" in line:
            assert '"name"' in lines[i + 1]
            break


def test_save_jsonc_with_nested_comments(tmp_path):
    class Nested(BaseModel):
        enabled: bool = Field(..., description="Enable feature")
        retries: int = Field(..., description="Number of retries")

    class NestedModel(Confdantic):
        title: str = Field(..., description="Title")
        config: Nested = Field(..., description="Configuration")

    model = NestedModel(title="Test", config={"enabled": True, "retries": 3})
    filepath = tmp_path / "nested.jsonc"
    model.save(str(filepath), comments=True)

    content = filepath.read_text()
    assert "// Title" in content
    assert "// Configuration" in content
    assert "// Enable feature" in content
    assert "// Number of retries" in content


def test_save_jsonc_with_literal_choices(tmp_path):
    class ChoicesModel(Confdantic):
        mode: Literal["dev", "prod"] = Field(..., description="Operating mode")

    model = ChoicesModel(mode="dev")
    filepath = tmp_path / "choices.jsonc"
    model.save(str(filepath), comments=True)

    content = filepath.read_text()
    assert "Operating mode | choices: dev, prod" in content


def test_jsonc_round_trip(tmp_path):
    class RoundTripModel(Confdantic):
        name: str = Field(..., description="Name")
        value: int

    original = RoundTripModel(name="test", value=42)
    filepath = tmp_path / "roundtrip.jsonc"
    original.save(str(filepath), comments=True)

    loaded = RoundTripModel.load(str(filepath))
    assert loaded.name == original.name
    assert loaded.value == original.value


def test_save_json5_extension(tmp_path, sample_data):
    class SimpleModel(Confdantic):
        name: str = Field(..., description="Name field")
        age: int

    model = SimpleModel(**sample_data)
    filepath = tmp_path / "test.json5"
    model.save(str(filepath), comments=True)

    content = filepath.read_text()
    assert "// Name field" in content

    loaded = SimpleModel.load(str(filepath))
    assert loaded.name == sample_data["name"]
    assert loaded.age == sample_data["age"]


def test_save_jsonc_with_braces_in_string_values(tmp_path):
    class BraceModel(Confdantic):
        template: str = Field("hello {world", description="A template with a brace")
        other: int = Field(1, description="Other field")
        more: str = Field("x", description="More field")

    model = BraceModel()
    filepath = tmp_path / "brace.jsonc"
    model.save(str(filepath), comments=True)

    content = filepath.read_text()
    assert '"template": "hello {world", // A template with a brace' in content
    assert '"other": 1, // Other field' in content
    assert '"more": "x" // More field' in content

    loaded = BraceModel.load(str(filepath))
    assert loaded.model_dump() == model.model_dump()


def test_save_jsonc_with_deeply_nested_comments(tmp_path):
    class Level3(BaseModel):
        deep: str = Field("d", description="Deep field desc")

    class Level2(BaseModel):
        mid: str = Field("m", description="Mid field desc")
        l3: Level3 = Field(default_factory=Level3, description="Level3 desc")

    class Level1(Confdantic):
        top: str = Field("t", description="Top field desc")
        l2: Level2 = Field(default_factory=Level2, description="Level2 desc")

    model = Level1()
    filepath = tmp_path / "deep.jsonc"
    model.save(str(filepath), comments=True)

    content = filepath.read_text()
    assert '"top": "t", // Top field desc' in content
    assert '"mid": "m", // Mid field desc' in content
    assert '"deep": "d" // Deep field desc' in content


def test_save_jsonc_with_nested_list_comments(tmp_path):
    class Item(BaseModel):
        name: str = Field(..., description="Item name")
        qty: int = Field(..., description="Item quantity")

    class Outer(Confdantic):
        items: list[Item] = Field(..., description="List of items")

    model = Outer(items=[{"name": "a", "qty": 2}])
    filepath = tmp_path / "items.jsonc"
    model.save(str(filepath), comments=True)

    content = filepath.read_text()
    assert '"items": [ // List of items' in content
    assert '"name": "a", // Item name' in content
    assert '"qty": 2 // Item quantity' in content


def test_save_jsonc_with_doubly_nested_list_comments(tmp_path):
    class Item(BaseModel):
        name: str = Field(..., description="Item name")

    class Outer(Confdantic):
        groups: list[list[Item]] = Field(..., description="Item groups")

    model = Outer(groups=[[Item(name="a")]])
    filepath = tmp_path / "groups.jsonc"
    model.save(str(filepath), comments=True)

    content = filepath.read_text()
    assert '"groups": [ // Item groups' in content
    assert '"name": "a" // Item name' in content


def test_save_jsonc_literal_none_choice_shown_as_null(tmp_path):
    class NoneLit(Confdantic):
        mode: Literal["a"] | None = Field("a", description="Mode")

    model = NoneLit()
    filepath = tmp_path / "none_lit.jsonc"
    model.save(str(filepath), comments=True)
    content = filepath.read_text()
    assert "choices: a, null" in content


def test_save_jsonc_literal_union_of_literals_choices(tmp_path):
    # Composed at runtime: two literal aliases may legitimately be unioned elsewhere.
    Mode = Literal["a"] | Literal["b"]  # noqa: PYI030

    class UnionLit(Confdantic):
        mode: Mode = Field("a", description="Mode")

    model = UnionLit()
    filepath = tmp_path / "union_lit.jsonc"
    model.save(str(filepath), comments=True)
    content = filepath.read_text()
    assert "Mode | choices: a, b" in content


def test_save_jsonc_with_optional_nested_comments(tmp_path):
    class Nested(BaseModel):
        retries: int = Field(3, description="Number of retries")

    class Outer(Confdantic):
        nested: Nested | None = Field(None, description="Optional nested config")

    model = Outer(nested={"retries": 5})
    filepath = tmp_path / "optional.jsonc"
    model.save(str(filepath), comments=True)

    content = filepath.read_text()
    assert '"nested": { // Optional nested config' in content
    assert '"retries": 5 // Number of retries' in content


def test_insert_jsonc_comments_legacy_nested_fields(tmp_path):
    class LegacyModel(Confdantic):
        sub: dict = Field(default_factory=dict, description="Sub desc")

    nested_fields = {"sub": {"x": Field(1, description="X desc")}}
    json_string = json.dumps({"sub": {"x": 1}}, indent=4)

    out = insert_jsonc_comments(json_string, LegacyModel.model_fields, nested_fields)
    assert '"x": 1 // X desc' in out

    # Original positional argument order must keep working.
    out = insert_jsonc_comments(json_string, LegacyModel.model_fields, nested_fields, "above_field")
    assert "// X desc" in out
    assert '"x": 1' in out

    # Without nested_fields the fill-in tree is not present.
    out = insert_jsonc_comments(json_string, LegacyModel.model_fields)
    assert "X desc" not in out


def test_save_jsonc_with_recursive_model_comments(tmp_path):
    class Node(Confdantic):
        name: str = Field(description="Node name")
        children: list["Node"] = Field(default_factory=list, description="Child nodes")

    model = Node(name="root", children=[Node(name="leaf")])
    filepath = tmp_path / "recursive.jsonc"
    model.save(str(filepath), comments=True)

    content = filepath.read_text()
    assert content.count("// Node name") == 2
    assert content.count("// Child nodes") == 2


def test_save_jsonc_does_not_use_wrong_union_model_comments(tmp_path):
    class Cat(BaseModel):
        name: str = Field(description="Cat name")
        kind: Literal["cat"] = "cat"

    class Dog(BaseModel):
        name: str = Field(description="Dog name")
        kind: Literal["dog"] = "dog"

    class PetConfig(Confdantic):
        pet: Cat | Dog

    filepath = tmp_path / "union.jsonc"
    PetConfig(pet=Dog(name="Fido")).save(str(filepath), comments=True)

    content = filepath.read_text()
    assert "Cat name" not in content
    assert "choices: cat" not in content
