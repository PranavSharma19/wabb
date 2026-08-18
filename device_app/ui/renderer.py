from __future__ import annotations

import math
import io
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from device_app.state import AppState, DeviceController

from .components import visible_slice, wrap_text


WIDTH = 800
HEIGHT = 480


@dataclass(frozen=True, slots=True)
class Theme:
    background: tuple[int, int, int] = (11, 15, 22)
    panel: tuple[int, int, int] = (22, 29, 40)
    panel_light: tuple[int, int, int] = (32, 41, 55)
    text: tuple[int, int, int] = (239, 244, 250)
    muted: tuple[int, int, int] = (153, 166, 184)
    accent: tuple[int, int, int] = (72, 207, 173)
    accent_dark: tuple[int, int, int] = (30, 103, 90)
    danger: tuple[int, int, int] = (255, 103, 119)
    border: tuple[int, int, int] = (55, 68, 86)


class DeviceRenderer:
    def __init__(self) -> None:
        import pygame

        self.pg = pygame
        self.theme = Theme()
        self.font_small = pygame.font.SysFont("Segoe UI", 17)
        self.font_body = pygame.font.SysFont("Segoe UI", 21)
        self.font_label = pygame.font.SysFont("Segoe UI Semibold", 18)
        self.font_title = pygame.font.SysFont("Segoe UI Semibold", 34)
        self.font_large = pygame.font.SysFont("Segoe UI Semibold", 48)
        self._image_cache: dict[str, object | None] = {}
        self._image_pending: set[str] = set()
        self._image_downloads: queue.SimpleQueue[tuple[str, bytes | None]] = queue.SimpleQueue()

    def render(
        self,
        surface: object,
        controller: DeviceController,
        *,
        now: float | None = None,
    ) -> None:
        if surface.get_size() != (WIDTH, HEIGHT):
            raise ValueError(f"Device surface must be exactly {WIDTH}x{HEIGHT}.")
        surface.fill(self.theme.background)
        timestamp = time.monotonic() if now is None else now
        state = controller.state
        if state is AppState.HOME:
            self._home(surface)
        elif state is AppState.RECORD_RECIPIENT:
            self._recording(surface, controller, timestamp, "LISTENING", "Release when done")
        elif state is AppState.TRANSCRIBING:
            subject = "message" if controller.context.current_operation.startswith("message") else "recipient details"
            self._loading(surface, "TRANSCRIBING", f"Turning speech into {subject}", timestamp)
        elif state is AppState.RECIPIENT_REVIEW:
            self._recipient_review(surface, controller)
        elif state is AppState.REFINE_RECIPIENT:
            self._recipient_refinement(surface, controller, timestamp)
        elif state is AppState.SEARCHING:
            self._loading(surface, "SEARCHING FOR PEOPLE", "Checking the best matching profiles", timestamp)
        elif state is AppState.HANDLE_RECORDING:
            self._recording(surface, controller, timestamp, "SAY THE HANDLE", "Release when done")
        elif state is AppState.HANDLE_LOOKUP:
            self._loading(
                surface,
                "LOOKING UP HANDLE",
                f"Resolving @{controller.context.handle}",
                timestamp,
            )
        elif state is AppState.HANDLE_NOT_FOUND:
            self._handle_not_found(surface, controller)
        elif state is AppState.SELECT_PROFILE:
            self._profile(surface, controller)
        elif state is AppState.RECORD_MESSAGE:
            self._message_recording(surface, controller, timestamp)
        elif state is AppState.REVIEW_MESSAGE:
            self._message_review(surface, controller)
        elif state is AppState.REFINE_MESSAGE:
            self._message_refinement(surface, controller, timestamp)
        elif state is AppState.SENDING:
            self._loading(surface, "SENDING MOCK MESSAGE", "Saving to the local Mock X conversation", timestamp)
            self._footer(surface, "Delivery in progress", "Please wait")
        elif state is AppState.SUCCESS:
            self._success(surface, controller)
        elif state is AppState.ERROR:
            self._error(surface, controller)

    def _home(self, surface: object) -> None:
        self._eyebrow(surface, "OUTREACH")
        self._text(surface, "NEW MESSAGE", (34, 58), self.font_title)
        self._rounded_panel(surface, (30, 120, 740, 120), self.theme.panel)
        self._circle(surface, (82, 180), 27, self.theme.accent)
        self._text(surface, "Hold SPACE or ENTER to begin", (130, 150), self.font_title)
        self._text(surface, "Describe the person you want to reach", (132, 193), self.font_body, self.theme.muted)
        self._text(surface, "H  I already know their @handle", (132, 222), self.font_small, self.theme.accent)
        self._text(surface, "HARDWARE EMULATION", (34, 288), self.font_label, self.theme.muted)
        self._rounded_panel(surface, (30, 326, 740, 70), self.theme.panel)
        self._text(surface, "LEFT / RIGHT  Profiles", (52, 345), self.font_small)
        self._text(surface, "R  Refine", (316, 345), self.font_small)
        self._text(surface, "ESC  Back", (504, 345), self.font_small)

    def _recording(
        self,
        surface: object,
        controller: DeviceController,
        now: float,
        title: str,
        subtitle: str,
    ) -> None:
        self._center_text(surface, title, 70, self.font_title)
        pulse = 1.0 + 0.13 * math.sin(now * 5)
        self._circle(surface, (400, 208), int(58 * pulse), self.theme.accent_dark)
        self._circle(surface, (400, 208), 30, self.theme.accent)
        remaining = max(0.0, controller.record_limit_seconds - controller.recording_elapsed)
        self._center_text(surface, f"{remaining:0.1f}s", 283, self.font_large)
        self._center_text(surface, subtitle, 344, self.font_body, self.theme.muted)
        self._progress(surface, controller.recording_elapsed / max(0.1, controller.record_limit_seconds))
        self._footer(surface, "R / ESC  Cancel", "Release SPACE / ENTER  Finish")

    def _recipient_refinement(self, surface: object, controller: DeviceController, now: float) -> None:
        if controller.context.current_operation_id is not None:
            self._recording(surface, controller, now, "ADD RECIPIENT CLUES", "Release when done")
            return
        self._eyebrow(surface, "RECIPIENT REFINEMENT")
        self._center_text(surface, "ADD MORE INFORMATION", 92, self.font_title)
        self._rounded_panel(surface, (90, 160, 620, 128), self.theme.panel)
        self._center_text(surface, "Hold SPACE or ENTER and speak", 181, self.font_body)
        self._center_text(surface, '"Change company to ABC"', 224, self.font_body, self.theme.accent)
        self._center_text(surface, '"Also went to U of T"', 255, self.font_small, self.theme.muted)
        self._center_text(surface, "Refining a result reruns search automatically", 330, self.font_small, self.theme.muted)
        self._footer(surface, "ESC / R  Back", "Hold SPACE / ENTER  Speak")

    def _handle_not_found(self, surface: object, controller: DeviceController) -> None:
        handle = controller.context.handle
        self._eyebrow(surface, "HANDLE")
        self._center_text(surface, "NO ACCOUNT FOUND", 92, self.font_title)
        self._rounded_panel(surface, (90, 160, 620, 128), self.theme.panel)
        subject = f"@{handle}" if handle else "That did not sound like a handle"
        self._center_text(surface, subject, 190, self.font_body, self.theme.danger)
        self._center_text(surface, "Hold SPACE or ENTER to describe them instead", 232, self.font_body)
        self._center_text(surface, "H  Try another handle", 264, self.font_small, self.theme.muted)
        self._footer(surface, "ESC  Home", "SPACE / ENTER  Describe instead")

    def _loading(self, surface: object, title: str, subtitle: str, now: float) -> None:
        self._center_text(surface, title, 126, self.font_title)
        for index in range(3):
            phase = (now * 3 - index * 0.45) % 3
            radius = 9 if phase < 1 else 6
            color = self.theme.accent if phase < 1 else self.theme.border
            self._circle(surface, (360 + index * 40, 225), radius, color)
        self._center_text(surface, subtitle, 284, self.font_body, self.theme.muted)
        self._footer(surface, "ESC  Cancel", "Please wait")

    def _recipient_review(self, surface: object, controller: DeviceController) -> None:
        context = controller.context
        self._eyebrow(surface, "CHECK BEFORE SEARCHING")
        self._text(surface, "RECIPIENT DETAILS", (30, 50), self.font_title)
        self._rounded_panel(surface, (28, 104, 744, 306), self.theme.panel)
        self._text(surface, "TRANSCRIPT", (50, 124), self.font_label, self.theme.muted)
        transcript_lines = wrap_text(self.font_small, context.transcript or "No transcript", 300)
        for index, line in enumerate(visible_slice(transcript_lines, context.criteria_scroll, 4)):
            self._text(surface, line, (50, 158 + index * 24), self.font_small)
        self.pg.draw.line(surface, self.theme.border, (374, 124), (374, 385), 2)
        fields = (
            ("NAME", context.criteria.name),
            ("COMPANY", context.criteria.company),
            ("ROLE", context.criteria.role),
            ("LOCATION", context.criteria.location),
            ("SCHOOL", context.criteria.school),
            ("EXTRA", ", ".join(context.criteria.extra_clues)),
        )
        y = 122
        for label, value in fields:
            self._text(surface, label, (402, y), self.font_small, self.theme.muted)
            self._text(surface, value or "-", (402, y + 22), self.font_body)
            y += 43
        self._footer(surface, "R Correct   UP/DOWN Scroll", "SPACE / ENTER  Search")

    def _profile(self, surface: object, controller: DeviceController) -> None:
        candidate = controller.current_candidate
        self._eyebrow(surface, "SELECT PROFILE")
        if candidate is None:
            self._center_text(surface, "NO MATCHES FOUND", 175, self.font_title)
            self._center_text(surface, "Refine your description and try again", 230, self.font_body, self.theme.muted)
            self._footer(surface, "ESC  Recipient details", "R  Add clues")
            return

        self._rounded_panel(surface, (28, 68, 744, 342), self.theme.panel)
        initials = "".join(part[0] for part in candidate.name.split()[:2]).upper() or "?"
        self._profile_image(surface, candidate.profile_image_url, initials, (108, 142), 52)
        self._text(surface, candidate.name, (184, 92), self.font_title)
        self._text(surface, f"@{candidate.username}", (186, 134), self.font_body, self.theme.accent)
        if candidate.verified:
            self._badge(surface, "VERIFIED", (186, 170))

        details: list[str] = []
        company = getattr(candidate, "company", "")
        if company:
            details.append(f"Company  {company}")
        details.extend(wrap_text(self.font_body, candidate.bio or "No bio provided", 520))
        details.append(f"Location  {candidate.location or 'Not provided'}")
        dm = "Can receive DM" if candidate.can_dm is True else "Cannot receive DM" if candidate.can_dm is False else "DM availability unknown"
        details.append(dm)
        details.append(f"Match score  {candidate.score:g}")
        details.extend(candidate.match_reasons)
        for index, line in enumerate(visible_slice(details, controller.context.profile_scroll, 6)):
            self._text(surface, line, (184, 210 + index * 28), self.font_small if line.startswith("Matched") else self.font_body)

        count = len(controller.context.candidates)
        self._text(surface, f"{controller.context.selected_index + 1} / {count}", (52, 378), self.font_label, self.theme.muted)
        self._footer(surface, "LEFT/RIGHT Profiles   R Refine", "SPACE / ENTER  Select")

    def _message_recording(self, surface: object, controller: DeviceController, now: float) -> None:
        candidate = controller.context.selected_candidate
        name = candidate.name if candidate else "RECIPIENT"
        if controller.context.current_operation_id is not None:
            self._recording(surface, controller, now, f"MESSAGE {name.upper()}", "Release when done")
            return
        username = f"@{candidate.username}" if candidate else ""
        self._eyebrow(surface, "RECIPIENT SELECTED")
        self._center_text(surface, f"MESSAGE {name.upper()}", 88, self.font_title)
        self._center_text(surface, username, 134, self.font_body, self.theme.accent)
        self._rounded_panel(surface, (104, 198, 592, 120), self.theme.panel)
        self._center_text(surface, "What would you like to say?", 220, self.font_body)
        self._center_text(surface, "Hold SPACE or ENTER and speak", 266, self.font_small, self.theme.accent)
        self._center_text(surface, "The selected profile is preserved.", 345, self.font_small, self.theme.muted)
        self._footer(surface, "ESC  Back to profiles", "Hold SPACE / ENTER  Record")

    def _message_review(self, surface: object, controller: DeviceController) -> None:
        candidate = controller.context.selected_candidate
        name = candidate.name if candidate else "recipient"
        self._eyebrow(surface, f"MESSAGE FOR {name.upper()}")
        self._text(surface, "REVIEW MESSAGE", (30, 52), self.font_title)
        self._rounded_panel(surface, (30, 116, 740, 270), self.theme.panel)
        lines = wrap_text(self.font_body, controller.context.message_draft, 670)
        for index, line in enumerate(visible_slice(lines, controller.context.message_scroll, 8)):
            self._text(surface, line, (62, 148 + index * 29), self.font_body)
        self._text(surface, "Confirmation writes only to the local Mock X service.", (48, 400), self.font_small, self.theme.muted)
        self._footer(surface, "R Refine   ESC Re-record", "SPACE / ENTER  Send mock")

    def _message_refinement(self, surface: object, controller: DeviceController, now: float) -> None:
        if controller.context.current_operation_id is not None:
            self._recording(surface, controller, now, "REFINE MESSAGE", "Release when done")
            return
        self._eyebrow(surface, "MESSAGE REFINEMENT")
        self._center_text(surface, "HOW SHOULD IT CHANGE?", 92, self.font_title)
        self._rounded_panel(surface, (90, 160, 620, 128), self.theme.panel)
        self._center_text(surface, 'Try "make it shorter"', 188, self.font_body, self.theme.accent)
        self._center_text(surface, 'or "replace with ..."', 230, self.font_body)
        self._center_text(surface, "The local stub applies the spoken revision.", 330, self.font_small, self.theme.muted)
        self._footer(surface, "ESC / R  Back", "Hold SPACE / ENTER  Speak")

    def _success(self, surface: object, controller: DeviceController) -> None:
        candidate = controller.context.selected_candidate
        name = candidate.name if candidate else "recipient"
        self._center_text(surface, "MOCK MESSAGE SENT", 86, self.font_title, self.theme.accent)
        self._circle(surface, (400, 194), 48, self.theme.accent_dark)
        self._centered_at(surface, "OK", (400, 194), self.font_title, self.theme.accent)
        self._center_text(surface, f"Saved for {name}", 276, self.font_body)
        message_id = controller.context.sent_message.id if controller.context.sent_message else "-"
        self._center_text(surface, f"Local message ID: {message_id}", 318, self.font_small, self.theme.muted)
        self._footer(surface, "ESC  Home", "SPACE / ENTER  New message")

    def _error(self, surface: object, controller: DeviceController) -> None:
        self._center_text(surface, "SOMETHING WENT WRONG", 86, self.font_title, self.theme.danger)
        self._rounded_panel(surface, (90, 156, 620, 150), self.theme.panel)
        lines = wrap_text(self.font_body, controller.context.recoverable_error, 560)
        for index, line in enumerate(lines[:4]):
            self._center_text(surface, line, 178 + index * 30, self.font_body)
        self._center_text(surface, "Your current work is safe.", 345, self.font_small, self.theme.muted)
        self._footer(surface, "ESC  Back", "SPACE / ENTER  Retry")

    def _profile_image(
        self,
        surface: object,
        source: str,
        initials: str,
        center: tuple[int, int],
        radius: int,
    ) -> None:
        if source.startswith("mock://"):
            seed = sum(ord(char) for char in source)
            color = (70 + seed % 80, 90 + (seed * 3) % 80, 120 + (seed * 7) % 80)
            self._circle(surface, center, radius, color)
            self._circle(surface, (center[0], center[1] - 13), 17, self.theme.text)
            self.pg.draw.ellipse(surface, self.theme.text, (center[0] - 30, center[1] + 7, 60, 35))
            return
        image = self._load_profile_image(source)
        if image is not None:
            surface.blit(image, image.get_rect(center=center))
            return
        self._circle(surface, center, radius, self.theme.panel_light)
        self._centered_at(surface, initials, center, self.font_title, self.theme.accent)

    def _load_profile_image(self, source: str) -> object | None:
        self._consume_downloaded_images()
        if not source:
            return None
        if source.startswith(("http://", "https://")):
            if source not in self._image_cache and source not in self._image_pending:
                self._image_pending.add(source)
                threading.Thread(
                    target=self._download_image,
                    args=(source,),
                    name="profile-image",
                    daemon=True,
                ).start()
            return self._image_cache.get(source)
        if source not in self._image_cache:
            path = Path(source)
            if not path.is_file():
                self._image_cache[source] = None
            else:
                try:
                    loaded = self.pg.image.load(str(path)).convert_alpha()
                    self._image_cache[source] = self.pg.transform.smoothscale(loaded, (104, 104))
                except (OSError, self.pg.error):
                    self._image_cache[source] = None
        return self._image_cache[source]

    def _download_image(self, source: str) -> None:
        data: bytes | None = None
        try:
            from urllib.request import Request, urlopen

            request = Request(source, headers={"User-Agent": "wabb-device-prototype/1"})
            with urlopen(request, timeout=3.0) as response:
                content_length = int(response.headers.get("Content-Length", "0") or 0)
                if content_length <= 2_000_000:
                    downloaded = response.read(2_000_001)
                    if len(downloaded) <= 2_000_000:
                        data = downloaded
        except (OSError, ValueError):
            pass
        self._image_downloads.put((source, data))

    def _consume_downloaded_images(self) -> None:
        while True:
            try:
                source, data = self._image_downloads.get_nowait()
            except queue.Empty:
                return
            self._image_pending.discard(source)
            if data is None:
                self._image_cache[source] = None
                continue
            try:
                loaded = self.pg.image.load(io.BytesIO(data)).convert_alpha()
                self._image_cache[source] = self.pg.transform.smoothscale(loaded, (104, 104))
            except (OSError, self.pg.error):
                self._image_cache[source] = None

    def _eyebrow(self, surface: object, text: str) -> None:
        self._text(surface, text, (32, 22), self.font_label, self.theme.accent)

    def _footer(self, surface: object, left: str, right: str) -> None:
        self.pg.draw.rect(surface, self.theme.panel, (0, 432, WIDTH, 48))
        self._text(surface, left, (24, 447), self.font_small, self.theme.muted)
        width = self.font_label.size(right)[0]
        self._text(surface, right, (WIDTH - width - 24, 445), self.font_label, self.theme.accent)

    def _progress(self, surface: object, fraction: float) -> None:
        fraction = min(1.0, max(0.0, fraction))
        self.pg.draw.rect(surface, self.theme.border, (170, 390, 460, 8), border_radius=4)
        self.pg.draw.rect(surface, self.theme.accent, (170, 390, int(460 * fraction), 8), border_radius=4)

    def _badge(self, surface: object, text: str, position: tuple[int, int]) -> None:
        width = self.font_small.size(text)[0] + 20
        self.pg.draw.rect(surface, self.theme.accent_dark, (*position, width, 25), border_radius=12)
        self._text(surface, text, (position[0] + 10, position[1] + 3), self.font_small, self.theme.accent)

    def _rounded_panel(self, surface: object, rect: tuple[int, int, int, int], color: tuple[int, int, int]) -> None:
        self.pg.draw.rect(surface, color, rect, border_radius=18)
        self.pg.draw.rect(surface, self.theme.border, rect, width=1, border_radius=18)

    def _circle(self, surface: object, center: tuple[int, int], radius: int, color: tuple[int, int, int]) -> None:
        self.pg.draw.circle(surface, color, center, radius)

    def _text(
        self,
        surface: object,
        text: str,
        position: tuple[int, int],
        font: object,
        color: tuple[int, int, int] | None = None,
    ) -> None:
        surface.blit(font.render(str(text), True, color or self.theme.text), position)

    def _center_text(
        self,
        surface: object,
        text: str,
        y: int,
        font: object,
        color: tuple[int, int, int] | None = None,
    ) -> None:
        rendered = font.render(str(text), True, color or self.theme.text)
        surface.blit(rendered, ((WIDTH - rendered.get_width()) // 2, y))

    def _centered_at(
        self,
        surface: object,
        text: str,
        center: tuple[int, int],
        font: object,
        color: tuple[int, int, int],
    ) -> None:
        rendered = font.render(text, True, color)
        surface.blit(rendered, (center[0] - rendered.get_width() // 2, center[1] - rendered.get_height() // 2))
