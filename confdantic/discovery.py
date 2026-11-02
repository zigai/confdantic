from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

import platformdirs

from confdantic.formats.base import Format
from confdantic.formats.registry import discovery_extensions, select_formats


def _first_regular_file(candidates: Iterable[Path]) -> Path | None:
    for candidate in candidates:
        if candidate.exists():
            if not candidate.is_file():
                raise ValueError(
                    f"Discovered configuration path '{candidate}' is not a regular file"
                )

            return candidate

    return None


def user_root(app_name: str, environment: Mapping[str, str]) -> Path:
    if os.name == "posix" and "XDG_CONFIG_HOME" in environment:
        return Path(environment["XDG_CONFIG_HOME"]) / app_name

    return platformdirs.user_config_path(app_name, appauthor=False)


def discover_user_file(
    app_name: str, extensions: Sequence[str], environment: Mapping[str, str]
) -> Path | None:
    root = user_root(app_name, environment)
    return _first_regular_file(root / f"config{extension}" for extension in extensions)


def discover_system_file(
    app_name: str, extensions: Sequence[str], environment: Mapping[str, str]
) -> Path | None:
    if os.name == "posix" and "XDG_CONFIG_DIRS" in environment:
        roots = [
            Path(part) / app_name
            for part in environment["XDG_CONFIG_DIRS"].split(os.pathsep)
            if part
        ]
    else:
        roots = [
            Path(part)
            for part in platformdirs.site_config_dir(
                app_name, appauthor=False, multipath=True
            ).split(os.pathsep)
            if part
        ]

    return _first_regular_file(
        root / f"config{extension}" for root in roots for extension in extensions
    )


def config_edit_path(
    path: str | Path | None = None,
    *,
    app_name: str | None = None,
    formats: Sequence[str | Format] = (),
) -> Path:
    if path is not None:
        if str(path) == "-":
            raise ValueError("standard input cannot be edited")

        return Path(path)

    if not app_name or not formats:
        raise ValueError("app_name and formats are required without an explicit path")

    selected = select_formats(formats)
    extensions = discovery_extensions(selected)
    environment = os.environ

    return (
        discover_user_file(app_name, extensions, environment)
        or user_root(app_name, environment) / f"config{extensions[0]}"
    )
