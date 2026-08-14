class VoiceInputError(RuntimeError):
    """A recoverable recording or transcription failure."""


class VoiceInputCancelled(VoiceInputError):
    """The caller intentionally cancelled voice capture before transcription."""
