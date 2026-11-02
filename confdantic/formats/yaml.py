from __future__ import annotations

from collections.abc import Mapping, Sequence
from io import StringIO
from typing import TYPE_CHECKING, Any

from ruamel.yaml import YAML as RuamelYAML  # noqa: N811
from ruamel.yaml import YAMLError
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.constructor import DuplicateKeyError
from ruamel.yaml.nodes import MappingNode, Node, SequenceNode
from ruamel.yaml.scalarstring import DoubleQuotedScalarString
from ruamel.yaml.util import load_yaml_guess_indent

from confdantic.errors import InvalidKeyPathError, MalformedDocumentError, MultipleDocumentsError
from confdantic.formats.base import RenderOptions, ValueLocation
from confdantic.io import decode_utf8_sig
from confdantic.plan import get_model_plan
from confdantic.values import ConfigValue, serialize_value

if TYPE_CHECKING:
    from confdantic.plan import ModelPlan


def commented_value(value: Any, plan: ModelPlan | None) -> Any:  # noqa: ANN401
    """Wrap a config-keyed document in ruamel containers with field comments attached."""
    if isinstance(value, dict):
        result = CommentedMap()
        for key, child in value.items():
            spec = plan.by_key.get(key) if plan is not None else None
            child_plan = (
                get_model_plan(spec.nested_models[0])
                if spec and len(spec.nested_models) == 1
                else None
            )
            result[key] = commented_value(child, child_plan)
            comment = spec.get_comment(fmt="yaml") if spec else None
            if comment:
                result.yaml_add_eol_comment(comment, key)

        return result

    if isinstance(value, list):
        result = CommentedSeq()
        result.extend(commented_value(child, plan) for child in value)
        return result

    return value


class YAML:
    name = "yaml"
    extensions = (".yaml", ".yml")

    def decode(self, data: bytes) -> Mapping[str, Any]:
        yaml = RuamelYAML()
        yaml.preserve_quotes = True
        try:
            nodes = list(yaml.compose_all(decode_utf8_sig(data)))
            if len(nodes) > 1:
                raise MultipleDocumentsError(
                    "Multiple YAML documents found in single-document stream"
                )

            if not nodes or not isinstance(nodes[0], MappingNode):
                raise MalformedDocumentError("YAML document root must be a mapping")

            _check_nodes(nodes[0], set(), set())
            document = yaml.constructor.construct_document(nodes[0])
        except DuplicateKeyError:
            message = "Duplicate key in YAML document"
        except (YAMLError, TypeError, ValueError, IndexError):
            message = "Invalid YAML document"
        else:
            return document

        raise MalformedDocumentError(message)

    def encode(
        self, document: Mapping[str, Any], *, plan: ModelPlan | None, options: RenderOptions
    ) -> bytes:
        serialized = _quote_next_line(
            serialize_value(dict(document), options.serialize_unsupported)
        )
        if options.comments:
            serialized = commented_value(serialized, plan)
        yaml = RuamelYAML()
        yaml.indent(mapping=2, sequence=4, offset=2)
        yaml.preserve_quotes = True
        stream = StringIO()
        yaml.dump(serialized, stream)

        return stream.getvalue().encode("utf-8")

    def locate(self, decoded: Mapping[str, Any], path: Sequence[str]) -> ValueLocation | None:  # noqa: C901
        if not path:
            return None

        current: Any = decoded
        line: int | None = None
        for segment in path:
            if isinstance(current, Mapping) and segment in current:
                if isinstance(current, CommentedMap):
                    try:
                        position = current.lc.key(segment) or current.lc.value(segment)
                    except KeyError:
                        position = None

                    if position is not None:
                        line = position[0] + 1

                current = current[segment]
            elif isinstance(current, list) and segment.isdigit() and int(segment) < len(current):
                index = int(segment)
                if isinstance(current, CommentedSeq):
                    try:
                        position = current.lc.item(index)
                    except KeyError:
                        position = None

                    if position is not None:
                        line = position[0] + 1

                current = current[index]
            else:
                return None

        return ValueLocation(line, str(current))

    def is_merge_anchor(self, decoded: Mapping[str, Any], path: Sequence[str]) -> bool:
        """Whether this mapping key defines an anchor referenced by a YAML merge."""
        if not path:
            return False

        current: Any = decoded
        for segment in path:
            if not isinstance(current, Mapping) or segment not in current:
                return False

            current = current[segment]
        if not isinstance(current, (CommentedMap, CommentedSeq)) or not current.anchor.value:
            return False

        def used(node: Any, seen: set[int]) -> bool:  # noqa: ANN401
            if id(node) in seen:
                return False

            seen.add(id(node))
            if isinstance(node, Mapping):
                if isinstance(node, CommentedMap) and any(
                    source is current for source in node.merge
                ):
                    return True

                return any(used(child, seen) for child in node.values())

            if isinstance(node, (list, tuple)):
                return any(used(child, seen) for child in node)

            return False

        return used(decoded, set())

    def edit(self, data: bytes, path: Sequence[str], value: ConfigValue) -> bytes:
        if not path:
            raise InvalidKeyPathError("Invalid empty key path")

        self.decode(data)
        yaml = RuamelYAML()
        yaml.preserve_quotes = True
        doc, indent, offset = load_yaml_guess_indent(decode_utf8_sig(data), yaml=yaml)
        if indent is not None:
            yaml.indent(mapping=indent, sequence=indent, offset=offset or 0)

        current: Any = doc
        for segment in path[:-1]:
            if segment not in current:
                current[_quote_next_line(segment)] = CommentedMap()
            elif not isinstance(current[segment], Mapping):
                raise InvalidKeyPathError(
                    f"Cannot traverse non-object at segment '{segment}' in key path"
                )

            current = current[segment]

        current[_quote_next_line(path[-1])] = _quote_next_line(value)
        stream = StringIO()
        yaml.dump(doc, stream)

        return stream.getvalue().encode("utf-8")


def _quote_next_line(value: Any) -> Any:  # noqa: ANN401
    # ruamel writes NEL (U+0085) inside a single-quoted scalar as a line break,
    # so the value would not read back; only double quotes escape it as `\N`.
    if isinstance(value, str):
        return DoubleQuotedScalarString(value) if "\x85" in value else value

    if isinstance(value, dict):
        return {_quote_next_line(key): _quote_next_line(child) for key, child in value.items()}

    if isinstance(value, list):
        return [_quote_next_line(child) for child in value]

    return value


def _check_nodes(node: Node, active: set[int], visited: set[int]) -> None:
    if id(node) in active:
        raise MalformedDocumentError("Recursive YAML aliases are not supported")

    if id(node) in visited:
        return

    active.add(id(node))
    if isinstance(node, MappingNode):
        for key, value in node.value:
            if key.tag not in ("tag:yaml.org,2002:str", "tag:yaml.org,2002:merge"):
                raise MalformedDocumentError("Configuration mapping keys must be strings")

            _check_nodes(value, active, visited)
    elif isinstance(node, SequenceNode):
        for value in node.value:
            _check_nodes(value, active, visited)

    active.remove(id(node))
    visited.add(id(node))


__all__ = ["YAML", "commented_value"]
