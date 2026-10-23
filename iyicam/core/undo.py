"""Snapshot based undo/redo for :class:`~iyicam.core.document.Project`."""
from __future__ import annotations

from .document import Project


class UndoStack:
    def __init__(self, limit: int = 100):
        self.limit = limit
        self._undo: list[tuple[str, dict]] = []
        self._redo: list[tuple[str, dict]] = []

    def checkpoint(self, project: Project, label: str) -> None:
        """Record the state *before* a modification."""
        self._undo.append((label, project.to_dict()))
        if len(self._undo) > self.limit:
            self._undo.pop(0)
        self._redo.clear()

    def rollback(self, project: Project) -> None:
        """Restore the last checkpoint and forget it (a failed or cancelled action)."""
        if self._undo:
            project.load_state(self._undo.pop()[1])

    def discard_last(self) -> None:
        """Forget the last checkpoint (e.g. when the action was cancelled)."""
        if self._undo:
            self._undo.pop()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_label(self) -> str:
        return self._undo[-1][0] if self._undo else ""

    @property
    def redo_label(self) -> str:
        return self._redo[-1][0] if self._redo else ""

    def undo(self, project: Project) -> bool:
        if not self._undo:
            return False
        label, state = self._undo.pop()
        self._redo.append((label, project.to_dict()))
        project.load_state(state)
        return True

    def redo(self, project: Project) -> bool:
        if not self._redo:
            return False
        label, state = self._redo.pop()
        self._undo.append((label, project.to_dict()))
        project.load_state(state)
        return True

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
