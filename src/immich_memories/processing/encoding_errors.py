"""Controlled runtime encoder failures safe to cross user-facing boundaries."""

from __future__ import annotations


class EncoderFallbackError(RuntimeError):
    """A hardware encode and its single same-codec software retry both failed."""


class StreamingEncoderError(RuntimeError):
    """A failure produced specifically by the streaming encoder boundary."""

    def __init__(self, message: str, *, exit_code: int | None = None) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class StreamingEncoderStartError(StreamingEncoderError):
    """The streaming FFmpeg encoder could not be started."""


class StreamingEncoderWriteError(StreamingEncoderError):
    """The streaming FFmpeg encoder stopped accepting raw frames."""


class StreamingEncoderFinishError(StreamingEncoderError):
    """The streaming FFmpeg encoder failed while finalizing output."""


class StreamingEncoderCleanupError(StreamingEncoderError):
    """A failed attempt could not be cleaned safely before retry."""


def streaming_failure_diagnostic(error: StreamingEncoderError) -> str:
    """Return controlled encoder diagnostics without trusting exception text."""
    diagnostic = type(error).__name__
    if error.exit_code is not None:
        diagnostic += f" exit {error.exit_code}"
    return diagnostic
