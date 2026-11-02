from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import BaseModel, ValidationError

from confdantic.duration import Duration, format_duration, parse_duration


def test_parse_duration_units():
    assert parse_duration("1000ns") == timedelta(microseconds=1)
    assert parse_duration("5us") == timedelta(microseconds=5)
    assert parse_duration("5µs") == timedelta(microseconds=5)
    assert parse_duration("10ms") == timedelta(milliseconds=10)
    assert parse_duration("30s") == timedelta(seconds=30)
    assert parse_duration("5m") == timedelta(minutes=5)
    assert parse_duration("2h") == timedelta(hours=2)
    assert parse_duration("3d") == timedelta(days=3)
    assert parse_duration("1w") == timedelta(weeks=1)


def test_parse_duration_compound_and_decimals():
    assert parse_duration("1h30m") == timedelta(hours=1, minutes=30)
    assert parse_duration("1.5h") == timedelta(hours=1, minutes=30)
    assert parse_duration("2d 4h 30s") == timedelta(days=2, hours=4, seconds=30)
    assert parse_duration("0s") == timedelta(seconds=0)


def test_parse_duration_signs():
    assert parse_duration("+10s") == timedelta(seconds=10)
    assert parse_duration("-10s") == timedelta(seconds=-10)
    assert parse_duration("- 1h 30m") == timedelta(hours=-1, minutes=-30)


def test_parse_duration_rejects_sub_microsecond_precision():
    with pytest.raises(ValueError, match="Unrepresentable sub-microsecond precision"):
        parse_duration("500ns")

    with pytest.raises(ValueError, match="Unrepresentable sub-microsecond precision"):
        parse_duration("1.5us")

    with pytest.raises(ValueError, match="Unrepresentable sub-microsecond precision"):
        parse_duration("0.0001ms")


def test_parse_duration_rejects_invalid_syntax():
    with pytest.raises(ValueError, match="cannot be empty"):
        parse_duration("")

    with pytest.raises(ValueError, match="cannot be empty"):
        parse_duration("   ")

    with pytest.raises(ValueError, match="duplicate sign or empty magnitude"):
        parse_duration("--10s")

    with pytest.raises(ValueError, match="duplicate sign or empty magnitude"):
        parse_duration("-")

    with pytest.raises(ValueError, match="Invalid duration syntax"):
        parse_duration("10s trailing")

    with pytest.raises(ValueError, match="Invalid duration syntax"):
        parse_duration("10xyz")


def test_format_duration_canonical():
    assert format_duration(timedelta(0)) == "0s"
    assert format_duration(timedelta(seconds=90)) == "1m30s"
    assert format_duration(timedelta(hours=2)) == "2h"
    assert format_duration(timedelta(days=14)) == "2w"
    assert format_duration(timedelta(microseconds=500)) == "500us"
    assert format_duration(timedelta(seconds=-90)) == "-1m30s"
    assert format_duration(timedelta(hours=-1, minutes=-30)) == "-1h30m"


def test_duration_pydantic_field():
    class ServiceConfig(BaseModel):
        timeout: Duration
        retry_delay: Duration = timedelta(seconds=5)

    cfg = ServiceConfig.model_validate({"timeout": "1m30s"})
    assert cfg.timeout == timedelta(seconds=90)
    assert cfg.retry_delay == timedelta(seconds=5)

    dumped = cfg.model_dump()
    assert dumped["timeout"] == "1m30s"
    assert dumped["retry_delay"] == "5s"

    with pytest.raises(ValidationError):
        ServiceConfig.model_validate({"timeout": "invalid_duration"})
