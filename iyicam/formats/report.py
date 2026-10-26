"""What an import brought in, and what it could not.

An importer that silently drops a part of a drawing hands over a different job than the one on the
screen of the drawing program. Every importer therefore fills an ``ImportReport`` with what it left
out (kinds of things it does not read, things that failed) and the program shows it.
"""
from __future__ import annotations

import inspect
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class ImportReport:
    read: Counter = field(default_factory=Counter)      # kind of entity -> how many were imported
    skipped: Counter = field(default_factory=Counter)   # kind of entity -> how many were left out
    errors: list[str] = field(default_factory=list)     # things that failed (one line each)
    notes: list[str] = field(default_factory=list)      # things worth knowing that are not faults

    @property
    def clean(self) -> bool:
        """Nothing was left out and nothing failed."""
        return not self.skipped and not self.errors

    def skip(self, kind: str, n: int = 1) -> None:
        self.skipped[kind] += n

    def error(self, text: str) -> None:
        if len(self.errors) < 200:
            self.errors.append(text)

    def summary(self) -> str:
        lines = []
        if self.skipped:
            what = ", ".join(f"{n} x {k}" for k, n in sorted(self.skipped.items()))
            lines.append(f"Not imported (not supported): {what}.")
        if self.errors:
            lines.append(f"{len(self.errors)} item(s) could not be read:")
            lines.extend("  " + e for e in self.errors[:8])
            if len(self.errors) > 8:
                lines.append(f"  ... and {len(self.errors) - 8} more")
        lines.extend(self.notes)
        return "\n".join(lines)


def run_importer(func: Callable, path, project, sheet, report: ImportReport | None = None, **options):
    """Call an importer; the built-in ones take a ``report``, importers from plugins may not."""
    if report is not None:
        try:
            params = inspect.signature(func).parameters
        except (TypeError, ValueError):
            params = {}
        if "report" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
            options["report"] = report
    return func(path, project, sheet, **options)
