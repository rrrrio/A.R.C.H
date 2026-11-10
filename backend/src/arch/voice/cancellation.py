"""Single-session utterance ownership used to drop late asynchronous output."""

from __future__ import annotations


class CancellationController:
    """Track exactly one active utterance and reject stale producer output."""

    def __init__(self) -> None:
        self._active_utterance_id: str | None = None

    @property
    def active_utterance_id(self) -> str | None:
        """Return the only utterance currently allowed to produce output."""
        return self._active_utterance_id

    def activate(self, utterance_id: str) -> str | None:
        """Make an utterance active and return the previous ID, if it was replaced."""
        previous = self._active_utterance_id
        self._active_utterance_id = utterance_id
        return previous if previous != utterance_id else None

    def accepts(self, utterance_id: str) -> bool:
        """Return whether a producer still owns the active output slot."""
        return self._active_utterance_id == utterance_id

    def cancel(self, utterance_id: str) -> bool:
        """Cancel only the currently active utterance, never a replacement."""
        if not self.accepts(utterance_id):
            return False
        self._active_utterance_id = None
        return True

    def finish(self, utterance_id: str) -> bool:
        """Finish only the active utterance and reject stale completion callbacks."""
        return self.cancel(utterance_id)
