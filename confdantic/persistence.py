from __future__ import annotations

import errno
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from filelock import FileLock
from pydantic import BaseModel, SecretBytes, SecretStr, ValidationError

from confdantic.errors import (
    ConfdanticError,
    ConfigError,
    ConfigValidationError,
    UnsupportedFormatError,
)
from confdantic.formats.base import Format, RenderOptions, SupportsEncode
from confdantic.formats.jsonc import CommentPosition
from confdantic.formats.registry import BUILTIN_FORMATS, format_for_path, select_formats
from confdantic.io import read_bounded_bytes
from confdantic.keypath import KeyPath
from confdantic.plan import ModelPlan, get_model_plan
from confdantic.values import reveal_secrets

SCHEMA_DIRECTIVES = {
    "toml": "#:schema {url}\n",
    "yaml": "# yaml-language-server: $schema={url}\n",
}


class ConfigFile:
    """Coordinate configuration reads and atomic writes under one process-safe lock.

    A symlink is followed, so the file at the end of its chain is read and replaced.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = resolve_target(path)
        self.lock = FileLock(self.path.parent / f".{self.path.name}.lock")

    def __enter__(self) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.lock.release()

    def read(self) -> bytes:
        with self:
            return read_bounded_bytes(self.path)

    def write(
        self,
        data: bytes,
        *,
        overwrite: bool = True,
        mode: int | None = None,
    ) -> None:
        with self:
            if self.path.exists() and not self.path.is_file():
                raise ValueError(f"Target path '{self.path}' is not a regular file")

            if not overwrite and self.path.exists():
                raise FileExistsError(f"Target path '{self.path}' already exists")

            if self.path.exists():
                mode = self.path.stat().st_mode & 0o777
            elif mode is None:
                mode = 0o644

            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=self.path.parent, delete=False, prefix=".tmp_confdantic_"
                ) as tmp:
                    tmp_path = Path(tmp.name)
                    tmp.write(data)
                    tmp.flush()
                    tmp_path.chmod(mode)
                    os.fsync(tmp.fileno())

                if overwrite:
                    tmp_path.replace(self.path)
                else:
                    try:
                        os.link(tmp_path, self.path)
                    except FileExistsError:
                        raise FileExistsError(f"Target path '{self.path}' already exists") from None

                if os.name == "posix":
                    directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
            finally:
                # After a replace the temporary name is gone; otherwise it is an orphan or a link.
                if tmp_path is not None:
                    tmp_path.unlink(missing_ok=True)


def resolve_target(path: Path | str) -> Path:
    """The file a write to `path` replaces: the end of its symlink chain, even when missing."""
    resolved = Path(os.path.realpath(path))
    # A loop leaves realpath on a link instead of raising.
    if resolved.is_symlink():
        raise OSError(errno.ELOOP, os.strerror(errno.ELOOP), str(path))

    return resolved


def atomic_write_bytes(
    path: Path | str,
    data: bytes,
    *,
    overwrite: bool = True,
    mode: int | None = None,
) -> None:
    """Write through a synced temporary file under the configuration writer lock.

    An existing file keeps its permission bits; `mode` applies to a new one.
    """
    ConfigFile(path).write(data, overwrite=overwrite, mode=mode)


def encode_model(config: BaseModel, writer: Format, options: RenderOptions) -> bytes:
    """Encode a model under its config keys, with secret values revealed."""
    plan = get_model_plan(type(config))
    dump = config.model_dump(mode="python")

    return _encode(writer, plan.to_document(reveal_secrets(dump), model=config), plan, options)


def builtin_writer(format_name: str) -> Format:
    return select_formats((format_name,))[0].format


def save(
    config: BaseModel,
    path: Path | str,
    *,
    overwrite: bool = True,
    mode: int | None = None,
    comments: bool | None = None,
    serialize_unsupported: bool = False,
    comment_position: CommentPosition = "end_of_line",
    indent: int = 4,
    formats: Sequence[str | Format] = (),
) -> None:
    """Write the whole model, including secret values, to a file the program owns.

    The format comes from the file extension; `formats` adds custom formats.
    `comments=None` writes comments wherever the format supports them. A symlink
    is followed. An existing file keeps its permission bits; `mode` applies to a new one.
    """
    if not isinstance(config, BaseModel):
        raise TypeError("save() expects a BaseModel instance as first argument")

    target_path = resolve_target(path)
    writer = _writer(target_path, formats)
    options = RenderOptions(
        _comments_for(writer, comments), serialize_unsupported, comment_position, indent
    )
    atomic_write_bytes(
        target_path, encode_model(config, writer, options), overwrite=overwrite, mode=mode
    )


def init(
    model: type[BaseModel],
    path: Path | str,
    *,
    overwrite: bool = False,
    schema_url: str | None = None,
    comments: bool | None = None,
    mode: int | None = None,
    formats: Sequence[str | Format] = (),
) -> None:
    """Write a starter file of the model's defaults; secret fields are left out.

    A symlink is followed. A replaced file keeps its permission bits.
    """
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise TypeError("init() requires a BaseModel subclass as first argument")

    target_path = resolve_target(path)
    if not overwrite and target_path.exists():
        raise FileExistsError(f"Target path '{target_path}' already exists")

    writer = _writer(target_path, formats)
    plan = get_model_plan(model)
    document = _template_document(plan)
    directive = b""
    if schema_url is not None:
        if "\r" in schema_url or "\n" in schema_url:
            raise ValueError("schema_url cannot contain newline characters")

        if "$schema" in plan.by_key:
            raise ConfdanticError(
                "Model already defines a '$schema' field; cannot inject schema directive"
            )

        if writer.name in ("json", "jsonc"):
            document = {"$schema": schema_url, **document}
        elif writer.name in SCHEMA_DIRECTIVES:
            directive = SCHEMA_DIRECTIVES[writer.name].format(url=schema_url).encode()
        else:
            raise UnsupportedFormatError(
                f"Format '{writer.name}' does not support schema directives"
            )

    options = RenderOptions(comments=_comments_for(writer, comments))
    encoded = directive + _encode(writer, document, plan, options)
    atomic_write_bytes(target_path, encoded, overwrite=overwrite, mode=mode)


def _writer(path: Path, formats: Sequence[str | Format]) -> Format:
    """Pick the format for `path`: an explicitly given one first, else a built-in."""
    selected = select_formats(formats)
    if any(item.extension == path.suffix for item in selected):
        return format_for_path(path, selected)

    return format_for_path(path, select_formats(BUILTIN_FORMATS))


def _comments_for(writer: Format, comments: bool | None) -> bool:
    return writer in BUILTIN_FORMATS if comments is None else comments


def _encode(
    writer: Format, document: Mapping[str, Any], plan: ModelPlan, options: RenderOptions
) -> bytes:
    if not isinstance(writer, SupportsEncode):
        raise UnsupportedFormatError(f"Format '{writer.name}' does not support writing")

    if writer not in BUILTIN_FORMATS and options != RenderOptions():
        raise UnsupportedFormatError(
            f"Custom format '{writer.name}' does not support non-default render options"
        )

    return writer.encode(document, plan=plan, options=options)


def _template_document(plan: ModelPlan) -> dict[str, Any]:
    """Defaults without secrets; fails only when a non-secret required field has no default."""
    defaults = plan.default_document()
    errors: list[ConfigError] = []
    try:
        instance = plan.validate_document(defaults)
    except ValidationError as e:
        for error in e.errors():
            key = plan.key_for_loc(error["loc"])
            if not plan.is_secret_path(key):
                message = (
                    "Configuration validation failed"
                    if not key and plan.contains_secret(defaults)
                    else error["msg"]
                )
                errors.append(ConfigError(message, key=key))
    else:
        defaults = plan.to_document(instance.model_dump(mode="python"), model=instance)
        for spec in plan.fields.values():
            if spec.field_info.annotation is Any or spec.entry_type is Any:
                defaults[spec.key] = getattr(instance, spec.name)

    if errors:
        raise ConfigValidationError(errors)

    return _without_secrets(defaults, plan)


def _without_secrets(
    document: Mapping[str, Any], plan: ModelPlan, prefix: KeyPath = ()
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in document.items():
        path = (*prefix, key)
        if plan.is_secret_path(path) or isinstance(value, (SecretStr, SecretBytes)):
            continue

        if isinstance(value, BaseModel):
            child_plan = get_model_plan(type(value))
            raw = {spec.key: getattr(value, spec.name) for spec in child_plan.fields.values()}
            result[key] = _without_secrets(raw, child_plan)
        elif isinstance(value, Mapping):
            result[key] = _without_secrets(value, plan, path)
        elif isinstance(value, list):
            items = {}
            for index, item in enumerate(value):
                items.update(_without_secrets({str(index): item}, plan, path))

            result[key] = list(items.values())
        else:
            result[key] = value

    return result


__all__ = [
    "ConfigFile",
    "atomic_write_bytes",
    "builtin_writer",
    "encode_model",
    "init",
    "resolve_target",
    "save",
]
