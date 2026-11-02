from __future__ import annotations

import sys
from pathlib import Path
from typing import IO

from confdantic.errors import FileTooLargeError, MalformedDocumentError


def read_bounded_bytes(
    source: Path | str | IO[bytes] | IO[str],
    max_file_size: int = 1_048_576,
    stdin: IO[bytes] | IO[str] | None = None,
) -> bytes:
    """Read at most max_file_size bytes from a path, stdin, or stream."""
    if max_file_size <= 0:
        raise ValueError(f"max_file_size must be positive, got {max_file_size}")

    if isinstance(source, (str, Path)):
        if str(source) == "-":
            stream = stdin if stdin is not None else sys.stdin
            return _read_from_stream(stream, max_file_size)

        path = Path(source)
        if path.is_dir():
            raise ValueError(f"Path '{path}' is a directory, expected a regular file")

        if not path.is_file():
            raise FileNotFoundError(f"Configuration file not found: '{path}'")

        with path.open("rb") as f:
            data = f.read(max_file_size + 1)

        if len(data) > max_file_size:
            raise FileTooLargeError(
                f"File '{path}' exceeds maximum permitted size of {max_file_size} bytes"
            )

        return data

    return _read_from_stream(source, max_file_size)


def decode_utf8_sig(data: bytes) -> str:
    """Decode raw bytes using UTF-8 with BOM tolerance."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass

    raise MalformedDocumentError("Invalid UTF-8 document encoding")


def _read_from_stream(
    stream: IO[bytes] | IO[str],
    max_file_size: int,
) -> bytes:
    raw_stream = getattr(stream, "buffer", stream)
    chunk = raw_stream.read(max_file_size + 1)

    if isinstance(chunk, str):
        data = chunk.encode("utf-8")
    elif isinstance(chunk, (bytes, bytearray)):
        data = bytes(chunk)
    else:
        raise TypeError("Configuration streams must return bytes or text")

    if len(data) > max_file_size:
        raise FileTooLargeError(
            f"Stream content exceeds maximum permitted size of {max_file_size} bytes"
        )

    return data


__all__ = [
    "decode_utf8_sig",
    "read_bounded_bytes",
]
