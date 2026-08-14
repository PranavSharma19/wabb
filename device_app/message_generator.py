from __future__ import annotations

import re
from typing import Protocol

from .search_contract import ProfileCandidate


class MessageGenerator(Protocol):
    def generate(self, transcript: str, recipient: ProfileCandidate) -> str: ...
    def refine(
        self,
        draft: str,
        instruction: str,
        recipient: ProfileCandidate,
    ) -> str: ...


class StubMessageGenerator:
    """Deterministic placeholder that can later be replaced by an LLM adapter."""

    def generate(self, transcript: str, recipient: ProfileCandidate) -> str:
        text = _clean(transcript)
        if not text:
            return f"Hi {recipient.name.split()[0]}, I'd love to connect."
        return _sentence(text)

    def refine(
        self,
        draft: str,
        instruction: str,
        recipient: ProfileCandidate,
    ) -> str:
        request = _clean(instruction)
        lowered = request.casefold()
        if lowered in {"shorter", "make it shorter", "shorten it"}:
            first = re.split(r"(?<=[.!?])\s+", draft.strip(), maxsplit=1)[0]
            return _sentence(first)
        if lowered in {"more professional", "make it more professional"}:
            body = re.sub(r"^(hi|hey)\s+[^,]+,?\s*", "", draft, flags=re.I)
            return _sentence(f"Hello {recipient.name.split()[0]}, {body}")
        for prefix in ("replace with ", "say ", "change it to "):
            if lowered.startswith(prefix):
                return _sentence(request[len(prefix) :])
        return _sentence(request) if request else draft


def _clean(value: str) -> str:
    return " ".join(value.strip().split())


def _sentence(value: str) -> str:
    text = _clean(value)
    if not text:
        return text
    text = text[0].upper() + text[1:]
    if text[-1] not in ".!?":
        text += "."
    return text
