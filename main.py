from __future__ import annotations

import sys
import logging
from collections.abc import Callable

from config import load_settings
from models.candidate import Candidate
from models.criteria import RecipientCriteria
from parsing.recipient_parser import parse_recipient_description, refine_criteria
from search.service import find_users
from search.x_client import XApiError
from voice.errors import VoiceInputError
from voice.service import OfflineVoiceInput


DIVIDER = "-" * 65
CRITERIA_FIELDS = (
    ("1", "name", "Name"),
    ("2", "company", "Company"),
    ("3", "role", "Role"),
    ("4", "location", "Location"),
    ("5", "school", "School"),
    ("6", "extra_clues", "Extra clues"),
)


def print_criteria(criteria: RecipientCriteria) -> None:
    print("\nSearch criteria:")
    fields = (
        ("Name", criteria.name),
        ("Company", criteria.company),
        ("Role", criteria.role),
        ("Location", criteria.location),
        ("School", criteria.school),
    )
    for label, value in fields:
        print(f"{label}: {value or '(not specified)'}")
    if criteria.extra_clues:
        print(f"Extra clues: {', '.join(criteria.extra_clues)}")


def print_results(candidates: list[Candidate]) -> None:
    print(f"\n{DIVIDER}")
    if not candidates:
        print("No candidates found.")
        print(DIVIDER)
        return

    for index, candidate in enumerate(candidates, start=1):
        verified = " [verified]" if candidate.verified else ""
        print(f"\n{index}. {candidate.name}{verified}")
        print(f"   @{candidate.username}")
        print(f"   {candidate.bio or '(no bio)'}")
        print(f"   {candidate.location or '(no location)'}")
        dm_status = "Yes" if candidate.can_dm is True else "No" if candidate.can_dm is False else "Unknown"
        print(f"   Can receive DM: {dm_status}")
        print(f"\n   Score: {candidate.score}")
        print("\n   Match reasons:")
        if candidate.match_reasons:
            for reason in candidate.match_reasons:
                print(f"   {reason}")
        else:
            print("   No weighted criteria matched")
        print(f"\n{DIVIDER}")


def run_search(criteria: RecipientCriteria, *, show_criteria: bool = True) -> bool:
    if show_criteria:
        print_criteria(criteria)
    try:
        print_results(find_users(criteria))
        return True
    except (ValueError, XApiError) as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return False


def capture_voice_text(voice_input: OfflineVoiceInput) -> str:
    while True:
        seconds = voice_input.settings.voice_record_seconds
        input(f"Press Enter when ready to record for {seconds:g} seconds...")
        print("[VOICE] Recording now. Speak clearly and naturally.")
        progress_shown = False

        def show_progress(remaining: int) -> None:
            nonlocal progress_shown
            progress_shown = True
            print(
                f"\r[VOICE] Recording... {remaining}s remaining — press Enter to stop early.   ",
                end="",
                flush=True,
            )

        def show_status(message: str) -> None:
            nonlocal progress_shown
            if progress_shown:
                print()
                progress_shown = False
            print(f"[VOICE] {message}")

        try:
            transcript = voice_input.listen(
                on_status=show_status,
                on_progress=show_progress,
            )
        except VoiceInputError as exc:
            if progress_shown:
                print()
            print(f"[VOICE] Error: {exc}", file=sys.stderr)
            return ""

        print(f"[VOICE] Transcript: {transcript}")
        while True:
            action = input("[Enter] Use  [E] Edit  [R] Record again  [C] Cancel: ").strip().casefold()
            if not action:
                return transcript
            if action in {"e", "edit"}:
                return input("Corrected text: ").strip()
            if action in {"r", "retry", "record"}:
                break
            if action in {"c", "cancel"}:
                return ""
            print("Please press Enter, or enter E, R, or C.")


def prompt_description(prompt: str, voice_input: OfflineVoiceInput) -> str:
    value = input(f"{prompt} (type text or /voice): ").strip()
    if value.casefold() in {"/voice", "/v"}:
        return capture_voice_text(voice_input)
    return value


def confirm_criteria(
    criteria: RecipientCriteria,
    *,
    input_fn: Callable[[str], str] = input,
) -> RecipientCriteria | None:
    """Let the user correct parsed fields before any search request is made."""

    edited = RecipientCriteria.from_dict(criteria.to_dict())
    while True:
        print_criteria(edited)
        action = input_fn("\n[Enter] Search  [E] Edit criteria  [C] Cancel: ").strip().casefold()
        if not action or action in {"s", "search"}:
            if _has_searchable_criteria(edited):
                return edited
            print("Add at least one search criterion before searching.")
            continue
        if action in {"c", "cancel"}:
            return None
        if action in {"e", "edit"}:
            _edit_one_criterion(edited, input_fn=input_fn)
            continue
        print("Please press Enter, or enter E or C.")


def _edit_one_criterion(
    criteria: RecipientCriteria,
    *,
    input_fn: Callable[[str], str],
) -> None:
    print("\nEdit which field?")
    for number, _attribute, label in CRITERIA_FIELDS:
        print(f"[{number}] {label}")
    print("[B] Back")

    choice = input_fn("Field: ").strip().casefold()
    if choice in {"b", "back", ""}:
        return

    selected = next(
        (
            (attribute, label)
            for number, attribute, label in CRITERIA_FIELDS
            if choice in {number, attribute.casefold(), label.casefold()}
        ),
        None,
    )
    if selected is None:
        print("Unknown field; nothing was changed.")
        return

    attribute, label = selected
    current = getattr(criteria, attribute)
    current_text = ", ".join(current) if isinstance(current, list) else current
    value = input_fn(
        f"{label} [{current_text or 'not specified'}] "
        "(Enter keeps it, - clears it): "
    ).strip()
    if not value:
        return
    if attribute == "extra_clues":
        setattr(
            criteria,
            attribute,
            [] if value == "-" else [item.strip() for item in value.split(",") if item.strip()],
        )
    else:
        setattr(criteria, attribute, "" if value == "-" else value)


def _has_searchable_criteria(criteria: RecipientCriteria) -> bool:
    return any(
        (
            criteria.name,
            criteria.company,
            criteria.role,
            criteria.location,
            criteria.school,
            criteria.extra_clues,
        )
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = load_settings()
    voice_input = OfflineVoiceInput(settings)
    mode = "free mock data" if settings.mock_x else "live X API"
    print("X User Finder Prototype")
    print(f"Mode: {mode}")
    print("Describe the person you want to find. Press Ctrl+C at any time to exit.\n")

    try:
        description = prompt_description("Person description", voice_input)
        if not description:
            print("No description entered.")
            return 1
        proposed_criteria = parse_recipient_description(description)
        confirmed = confirm_criteria(proposed_criteria)
        if confirmed is None:
            print("Search cancelled.")
            return 0
        criteria = confirmed
        run_search(criteria, show_criteria=False)

        while True:
            action = input("\n[R] Refine search  [N] New search  [Q] Quit: ").strip().casefold()
            if action in {"q", "quit"}:
                return 0
            if action in {"r", "refine"}:
                additional = prompt_description("Add identifying information", voice_input)
                if additional:
                    proposed_criteria = refine_criteria(criteria, additional)
                    confirmed = confirm_criteria(proposed_criteria)
                    if confirmed is not None:
                        criteria = confirmed
                        run_search(criteria, show_criteria=False)
                    else:
                        print("Refinement cancelled; existing criteria were kept.")
                else:
                    print("No refinement entered; criteria were unchanged.")
                continue
            if action in {"n", "new"}:
                description = prompt_description("Person description", voice_input)
                if description:
                    proposed_criteria = parse_recipient_description(description)
                    confirmed = confirm_criteria(proposed_criteria)
                    if confirmed is not None:
                        criteria = confirmed
                        run_search(criteria, show_criteria=False)
                    else:
                        print("New search cancelled; existing criteria were kept.")
                else:
                    print("No description entered; current search was kept.")
                continue
            print("Please enter R, N, or Q.")
    except (EOFError, KeyboardInterrupt):
        print("\nGoodbye.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
