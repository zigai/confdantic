from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from confdantic.errors import format_key
from confdantic.keypath import parse_key

SEGMENTS = st.text(st.sampled_from(["a", "b", ".", "\\", " ", "é"]), max_size=6)


@given(st.lists(SEGMENTS, min_size=1, max_size=5).map(tuple))
def test_dotted_text_parses_back_to_the_same_key_path(path: tuple[str, ...]):
    if path == ("",):
        return

    assert parse_key(format_key(path)) == path
