from __future__ import annotations

import pytest

from parsing.handle_transcript import parse_handle


@pytest.mark.parametrize(
    "spoken, expected",
    [
        ("at jbart", "jbart"),
        ("@jbart", "jbart"),
        ("at jay bart", "jaybart"),
        ("j b a r t", "jbart"),
        ("jbart underscore dev", "jbart_dev"),
        ("his handle is jbart", "jbart"),
        ("her username is mayachen", "mayachen"),
        ("JBart", "jbart"),
        ("at jbart.", "jbart"),
    ],
)
def test_spoken_forms_resolve_to_one_handle(spoken: str, expected: str) -> None:
    assert parse_handle(spoken) == expected


@pytest.mark.parametrize(
    "spoken",
    [
        "",
        "   ",
        # Sixteen characters. X stops at fifteen, so this is not a handle at all.
        "abcdefghijklmnop",
        # A description, not a handle. Handle mode should reject it rather than
        # join it into a forty-character pseudo-handle and look it up.
        "joe bart a member of technical staff at meta",
        "jbart at gmail dot com",
        "j dot bart",
        "j dash bart",
        "jbart period dev",
    ],
)
def test_things_that_are_not_handles_are_rejected(spoken: str) -> None:
    assert parse_handle(spoken) is None
