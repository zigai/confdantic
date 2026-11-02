from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal
from typing import Annotated, Any, TypeAlias

from pydantic import BeforeValidator, PlainSerializer

UNIT_MICROSECONDS: dict[str, Decimal] = {
    "d": Decimal(86400000000),
    "h": Decimal(3600000000),
    "m": Decimal(60000000),
    "ms": Decimal(1000),
    "ns": Decimal("0.001"),
    "s": Decimal(1000000),
    "us": Decimal(1),
    "w": Decimal(604800000000),
    "µs": Decimal(1),
}

TERM_PATTERN = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(ns|us|µs|ms|s|m|h|d|w)")


def parse_duration(text: str) -> timedelta:
    """Parse a duration string into a standard datetime.timedelta."""
    s = text.strip()
    if not s:
        raise ValueError("Duration string cannot be empty")

    is_neg, s = _extract_sign(s)
    total_us = _parse_terms(s, text)

    int_us = int(total_us)
    if is_neg:
        int_us = -int_us

    return timedelta(microseconds=int_us)


def _extract_sign(s: str) -> tuple[bool, str]:
    is_neg = False
    if s.startswith("-"):
        is_neg = True
        s = s[1:].lstrip()
    elif s.startswith("+"):
        s = s[1:].lstrip()

    if not s or s.startswith(("-", "+")):
        raise ValueError("Invalid duration format: duplicate sign or empty magnitude")

    return is_neg, s


def _parse_terms(s: str, original_text: str) -> Decimal:
    idx = 0
    total_us = Decimal(0)
    terms_count = 0

    while idx < len(s):
        while idx < len(s) and s[idx].isspace():
            idx += 1

        if idx >= len(s):
            break

        m = TERM_PATTERN.match(s, idx)
        if not m:
            raise ValueError(f"Invalid duration syntax at: {s[idx:]}")

        num_str, unit = m.groups()
        num = Decimal(num_str)
        factor = UNIT_MICROSECONDS[unit]
        total_us += num * factor
        terms_count += 1
        idx = m.end()

    if terms_count == 0:
        raise ValueError(f"Invalid duration syntax in '{original_text}'")

    if total_us != total_us.to_integral_value():
        raise ValueError(
            f"Unrepresentable sub-microsecond precision in duration: '{original_text}'"
        )

    return total_us


def format_duration(td: timedelta) -> str:
    """Format a timedelta into its canonical duration string."""
    total_us = td.days * 86_400_000_000 + td.seconds * 1_000_000 + td.microseconds
    if total_us == 0:
        return "0s"

    is_neg = total_us < 0
    rem = abs(total_us)

    units: list[tuple[str, int]] = [
        ("w", 604_800_000_000),
        ("d", 86_400_000_000),
        ("h", 3_600_000_000),
        ("m", 60_000_000),
        ("s", 1_000_000),
        ("ms", 1_000),
        ("us", 1),
    ]
    parts: list[str] = []
    for u_name, u_factor in units:
        count = rem // u_factor
        if count > 0:
            parts.append(f"{count}{u_name}")
            rem %= u_factor

    res = "".join(parts)

    return f"-{res}" if is_neg else res


def _parse_duration_input(value: Any) -> Any:  # noqa: ANN401
    if isinstance(value, timedelta):
        return value

    if isinstance(value, str):
        return parse_duration(value)

    return value


Duration: TypeAlias = Annotated[
    timedelta,
    BeforeValidator(_parse_duration_input, json_schema_input_type=str | int | float),
    PlainSerializer(format_duration, return_type=str),
]

__all__ = ["Duration", "format_duration", "parse_duration"]
