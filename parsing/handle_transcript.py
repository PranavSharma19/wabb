from __future__ import annotations

import re


# X usernames are one to fifteen characters of ASCII letters, digits and
# underscore. Nothing else is a handle, however confidently it was spoken.
HANDLE_PATTERN = re.compile(r"^[a-z0-9_]{1,15}$")

# Lead-ins people say before the handle itself, longest first so "his handle is"
# never leaves a stray "is" behind.
_LEAD_INS = (
    "my handle is",
    "his handle is",
    "her handle is",
    "their handle is",
    "the handle is",
    "handle is",
    "my username is",
    "his username is",
    "her username is",
    "their username is",
    "the username is",
    "username is",
    "it's",
    "its",
)

# Spoken punctuation. Only the underscore survives: X allows no other symbol, so
# a spoken "dot" or "dash" should make the handle invalid rather than be
# translated into something the user did not say.
_SPOKEN_SYMBOLS = ((" underscore ", "_"), (" under score ", "_"))


def parse_handle(text: str) -> str | None:
    """Turn a spoken handle into an X username, or None if it is not one.

    Only ever called in handle mode, where the user has already said they are
    giving a handle. That is what makes a leading "at" unambiguously the '@'
    sign here, when the same token in a spoken description means "works at".
    """

    cleaned = re.sub(r"[.,!?]", " ", str(text or "").casefold())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return None

    for lead_in in _LEAD_INS:
        if cleaned.startswith(f"{lead_in} "):
            cleaned = cleaned[len(lead_in) + 1 :].strip()
            break
    if cleaned.startswith("at "):
        cleaned = cleaned[3:].strip()
    cleaned = cleaned.lstrip("@").strip()

    for spoken, symbol in _SPOKEN_SYMBOLS:
        cleaned = cleaned.replace(spoken, symbol)

    # A handle contains no spaces, so every space left is an artifact of
    # dictation: "j b a r t" and "jay bart" are each one handle. Anything long
    # enough that joining it produces more than fifteen characters was a
    # sentence, and falls out through the pattern check below.
    handle = cleaned.replace(" ", "")
    return handle if HANDLE_PATTERN.fullmatch(handle) else None
