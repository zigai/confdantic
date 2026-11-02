from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from confdantic.discovery import discover_system_file, discover_user_file
from confdantic.env import bind_environment
from confdantic.errors import (
    ConfigError,
    ConfigValidationError,
    InvalidEnvironmentValueError,
    InvalidKeyPathError,
    NoFormatsError,
    format_key,
    quote,
)
from confdantic.formats.base import Format, SupportsLocate
from confdantic.formats.registry import (
    Selected,
    discovery_extensions,
    format_for_path,
    select_formats,
)
from confdantic.formats.yaml import YAML
from confdantic.io import read_bounded_bytes
from confdantic.keypath import KeyPath, to_key_path
from confdantic.merge import collect_origins, merge_overlay
from confdantic.plan import get_model_plan
from confdantic.provenance import REDACTED, Metadata, Origin, SourceKind, UnknownKey

M = TypeVar("M", bound=BaseModel)
DEFAULT_MAX_FILE_SIZE = 1_048_576


@dataclass(frozen=True, slots=True)
class Contribution:
    """A layer applied after the environment, such as parsed CLI flags.

    `values` maps config keys, dotted or as key paths, to values; `paths` maps a key
    to the name recorded as its `Origin.path`, such as `"--port"`.
    """

    values: Mapping[str | KeyPath, object]
    source: SourceKind = SourceKind.FLAG
    paths: Mapping[str | KeyPath, str] = field(default_factory=dict)


class Resolution(Generic[M]):
    """State for one load: the merged config-keyed document and its origins."""

    def __init__(
        self,
        model: type[M],
        selected: tuple[Selected, ...],
        max_file_size: int,
        environment: Mapping[str, str],
        stdin: IO[str] | IO[bytes] | None,
    ) -> None:
        self.model = model
        self.plan = get_model_plan(model)
        self.selected = selected
        self.max_file_size = max_file_size
        self.environment = environment
        self.stdin = stdin
        self.accumulated: dict[str, Any] = {}
        self.origins: dict[KeyPath, Origin] = {}
        self.active_files: list[str] = []
        self.unknown: list[UnknownKey] = []

    def apply_defaults(self, defaults: BaseModel | None) -> None:
        self._overlay(self.plan.default_document(), SourceKind.DEFAULT)
        if defaults is not None:
            dump = defaults.model_dump(mode="python")
            self._overlay(self.plan.to_document(dump, model=defaults), SourceKind.DEFAULT)

    def apply_file(self, path: str | Path, source: SourceKind, *, missing_ok: bool = False) -> None:
        is_stdin = source is SourceKind.STDIN
        if missing_ok and not is_stdin and not Path(path).exists():
            return

        if is_stdin or len({id(item.format) for item in self.selected}) == 1:
            fmt = self.selected[0].format
        else:
            fmt = format_for_path(path, self.selected)
        document = fmt.decode(
            read_bounded_bytes(path, max_file_size=self.max_file_size, stdin=self.stdin)
        )
        if "$schema" in document and "$schema" not in self.plan.by_key:
            document = {key: value for key, value in document.items() if key != "$schema"}
        location = None if is_stdin else str(Path(path).resolve())
        if location is not None:
            self.active_files.append(location)

        locator = fmt if isinstance(fmt, SupportsLocate) else None
        self._record_unknown_keys(fmt, document, source, location, locator)
        known = self._known_only(document)
        origins = collect_origins(
            known, source, location, self.plan, locator=locator, decoded=document
        )
        merge_overlay(self.accumulated, known, self.origins, origins, self.plan)

    def apply_environment(self, prefix: str) -> None:
        values, origins = bind_environment(self.plan, self.environment, prefix)
        merge_overlay(self.accumulated, values, self.origins, origins, self.plan)

    def apply_contributions(self, contributions: Sequence[Contribution]) -> None:
        for contribution in contributions:
            values = _contribution_values(contribution.values)
            overlay = _expand(values)
            unknown = self.plan.unknown_keys(overlay)
            if unknown:
                key = unknown[0]
                origin = Origin(key, contribution.source, None, None, None)
                raise UnknownKey(origin, self.plan.suggest(key)).error()

            paths = {to_key_path(key): path for key, path in contribution.paths.items()}
            origins = collect_origins(overlay, contribution.source, None, self.plan)
            for key, origin in origins.items():
                path = next(
                    (paths[key[:end]] for end in range(len(key), 0, -1) if key[:end] in paths),
                    None,
                )
                origins[key] = Origin(key, origin.source, path, origin.line, origin.raw_value)

            merge_overlay(self.accumulated, overlay, self.origins, origins, self.plan)

    def finish(self, *, strict: bool) -> tuple[M, Metadata]:
        if strict and self.unknown:
            raise ConfigValidationError([entry.error() for entry in self.unknown])

        try:
            instance = self.plan.validate_document(self.accumulated)
        except ValidationError as e:
            errors = [
                self._attribute(
                    error["loc"],
                    error["msg"],
                    error["type"],
                )
                for error in e.errors()
            ]
        else:
            resolved = self.plan.to_document(
                instance.model_dump(mode="python", include=set(self.plan.fields)), model=instance
            )
            for key, origin in collect_origins(
                resolved, SourceKind.DEFAULT, None, self.plan
            ).items():
                self.origins.setdefault(key, origin)

            meta = Metadata(self.origins, self.active_files, self.unknown, self.plan.keys())
            _run_validate_config(instance, meta)
            return instance, meta

        # Raised outside the handler so the input-bearing ValidationError is not chained.
        if len(errors) == 1 and isinstance(errors[0], InvalidEnvironmentValueError):
            raise errors[0]

        raise ConfigValidationError(errors)

    def _overlay(self, document: Mapping[str, Any], source: SourceKind) -> None:
        origins = collect_origins(document, source, None, self.plan)
        merge_overlay(self.accumulated, document, self.origins, origins, self.plan)

    def _record_unknown_keys(
        self,
        fmt: Format,
        document: Mapping[str, Any],
        source: SourceKind,
        location: str | None,
        locator: SupportsLocate | None,
    ) -> None:
        unknown = self.plan.unknown_keys(document)
        for key in unknown:
            if isinstance(fmt, YAML) and fmt.is_merge_anchor(document, key):
                continue

            found = locator.locate(document, key) if locator is not None else None
            origin = Origin(key, source, location, found.line if found else None, None)
            entry = UnknownKey(origin, self.plan.suggest(key))
            self.unknown.append(entry)
            if self.plan.target(key).forbidden:
                raise entry.error()

    def _known_only(self, mapping: Mapping[str, Any], prefix: KeyPath = ()) -> dict[str, Any]:
        """Drop unknown keys, keeping the document's config-key spelling."""
        result: dict[str, Any] = {}

        for name, value in mapping.items():
            key = (*prefix, name)
            target = self.plan.target(key)
            if not target.known:
                continue

            result[name] = self._known_value(value, key)

        return result

    def _known_value(self, value: Any, key: KeyPath) -> Any:  # noqa: ANN401
        if isinstance(value, Mapping):
            return self._known_only(value, key)

        if isinstance(value, list):
            return [self._known_value(item, (*key, str(index))) for index, item in enumerate(value)]

        return value

    def _attribute(self, loc: tuple[int | str, ...], message: str, error_type: str) -> ConfigError:
        key = self.plan.key_for_loc(loc)
        origin = None if error_type == "missing" else _nearest_origin(key, self.origins)
        if (
            self.plan.is_secret_path(key)
            or (origin is not None and origin.raw_value == REDACTED)
            or any(
                candidate[: len(key)] == key and value.raw_value == REDACTED
                for candidate, value in self.origins.items()
            )
            or (not key and self.plan.contains_secret(self.accumulated))
        ):
            message = "Invalid secret configuration value"

        if origin is not None and origin.source is SourceKind.ENV and origin.path is not None:
            return InvalidEnvironmentValueError(
                message, key=key, origin=origin, variable_name=origin.path
            )

        return ConfigError(message, key=key, origin=origin)


def _run_validate_config(instance: BaseModel, meta: Metadata) -> None:
    """Run an optional `validate_config(meta)` hook, collecting its failures."""
    validate = getattr(instance, "validate_config", None)
    if not callable(validate):
        return

    try:
        validate(meta)
    except ConfigValidationError as e:
        errors = list(e.errors)
    except ConfigError as e:
        errors = [e]
    except ExceptionGroup as e:
        leaves = _config_error_leaves(e)
        if leaves is None:
            raise

        errors = leaves
    except ValueError as e:
        message = (
            "Configuration validation failed"
            if get_model_plan(type(instance)).contains_secret(instance)
            else str(e)
        )
        errors = [ConfigError(message)]
    else:
        return

    plan = get_model_plan(type(instance))
    safe = []
    for error in errors:
        secret = plan.is_secret_path(error.key) if error.key else plan.contains_secret(instance)
        message = "Invalid secret configuration value" if secret else error.message
        origin = error.origin
        if secret and origin is not None:
            origin = Origin(origin.key, origin.source, origin.path, origin.line, REDACTED)

        safe.append(ConfigError(message, key=error.key, origin=origin))

    raise ConfigValidationError(safe)


def _config_error_leaves(group: ExceptionGroup[Exception]) -> list[ConfigError] | None:
    """Flatten a group whose leaves are all ConfigErrors, else None."""
    leaves: list[ConfigError] = []
    for error in group.exceptions:
        if isinstance(error, ExceptionGroup):
            nested = _config_error_leaves(error)
            if nested is None:
                return None

            leaves.extend(nested)
        elif isinstance(error, ConfigError):
            leaves.append(error)
        else:
            return None

    return leaves


def _contribution_values(values: Mapping[str | KeyPath, object]) -> dict[KeyPath, object]:
    """Key a contribution by key paths, rejecting a key that is also set below another."""
    result = {to_key_path(key): value for key, value in values.items()}
    for key in result:
        for end in range(1, len(key)):
            if key[:end] in result:
                raise InvalidKeyPathError(
                    f"Contribution sets both {quote(format_key(key[:end]))} "
                    f"and {quote(format_key(key))}"
                )

    return result


def _expand(values: Mapping[KeyPath, object]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for (*parents, leaf), value in values.items():
        cursor = result
        for segment in parents:
            cursor = cursor.setdefault(segment, {})

        cursor[leaf] = value

    return result


def _nearest_origin(key: KeyPath, origins: Mapping[KeyPath, Origin]) -> Origin | None:
    return next(
        (origins[key[:end]] for end in range(len(key), 0, -1) if key[:end] in origins), None
    )


def load_with_metadata(
    model: type[M],
    path: str | Path | None = None,
    *,
    missing_ok: bool = False,
    app_name: str | None = None,
    formats: Sequence[str | Format] = (),
    env_prefix: str = "",
    strict: bool = False,
    defaults: M | None = None,
    contributions: Sequence[Contribution] = (),
    max_file_size: int = DEFAULT_MAX_FILE_SIZE,
    environment: Mapping[str, str] | None = None,
    stdin: IO[str] | IO[bytes] | None = None,
) -> tuple[M, Metadata]:
    """Load `model` from every configured layer and report where each value came from.

    Precedence, lowest first: defaults, system file, user file, `path` (or stdin
    for `"-"`), environment, then `contributions` in order. With one format selected,
    `path` is read in it whatever its suffix; with several, the suffix picks one.
    """
    if max_file_size <= 0:
        raise ValueError("max_file_size must be positive")

    if app_name is not None and (
        not app_name or "/" in app_name or "\\" in app_name or ".." in app_name
    ):
        raise ValueError(f"Invalid app_name: {app_name!r}")

    if defaults is not None and not isinstance(defaults, model):
        raise TypeError(f"defaults must be an instance of {model.__name__}")

    if (path is not None or app_name is not None) and not formats:
        raise NoFormatsError("formats must be specified when reading files")

    selected = select_formats(formats)
    resolution = Resolution(
        model,
        selected,
        max_file_size,
        os.environ if environment is None else environment,
        stdin,
    )
    resolution.apply_defaults(defaults)
    if app_name is not None:
        extensions = discovery_extensions(selected)
        system = discover_system_file(app_name, extensions, resolution.environment)
        if system is not None:
            resolution.apply_file(system, SourceKind.SYSTEM)

        user = discover_user_file(app_name, extensions, resolution.environment)
        if user is not None:
            resolution.apply_file(user, SourceKind.USER)

    if path is not None:
        source = SourceKind.STDIN if str(path) == "-" else SourceKind.FILE
        resolution.apply_file(path, source, missing_ok=missing_ok)

    resolution.apply_environment(env_prefix)
    resolution.apply_contributions(contributions)

    return resolution.finish(strict=strict)


def load(
    model: type[M],
    path: str | Path | None = None,
    *,
    missing_ok: bool = False,
    app_name: str | None = None,
    formats: Sequence[str | Format] = (),
    env_prefix: str = "",
    strict: bool = False,
    defaults: M | None = None,
    contributions: Sequence[Contribution] = (),
    max_file_size: int = DEFAULT_MAX_FILE_SIZE,
    environment: Mapping[str, str] | None = None,
    stdin: IO[str] | IO[bytes] | None = None,
) -> M:
    """`load_with_metadata` without the metadata."""
    loaded: tuple[M, Metadata] = load_with_metadata(
        model,
        path,
        missing_ok=missing_ok,
        app_name=app_name,
        formats=formats,
        env_prefix=env_prefix,
        strict=strict,
        defaults=defaults,
        contributions=contributions,
        max_file_size=max_file_size,
        environment=environment,
        stdin=stdin,
    )
    return loaded[0]


__all__ = ["Contribution", "load", "load_with_metadata"]
