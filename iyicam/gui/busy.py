"""Long calculations: run in a thread while the program stays alive, with a Cancel button after a moment."""
from __future__ import annotations

import ctypes
import threading
import time

from PySide6.QtCore import QEventLoop, Qt
from PySide6.QtWidgets import QApplication, QProgressDialog


class _Worker(threading.Thread):
    def __init__(self, fn):
        super().__init__(daemon=True)
        self.fn, self.result, self.error = fn, None, None

    def run(self) -> None:
        try:
            self.result = self.fn()
        except BaseException as e:  # noqa: BLE001  (handed to the caller)
            self.error = e


def _raise_in(thread: threading.Thread, exc: type[BaseException]) -> None:
    """Make ``thread`` raise ``exc`` the next time it runs Python code (a calculation that never looks at a flag)."""
    if thread.ident is not None:
        ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(thread.ident), ctypes.py_object(exc))


def run_busy(fn, parent=None, label: str = "Calculating...", cancelled: type[BaseException] | None = None,
             delay: float = 0.4, state: dict | None = None):
    """Run ``fn()`` in a thread; the interface keeps running. After ``delay`` seconds a window with a Cancel
    button appears. Cancel sets ``state["cancel"]`` (a ``threading.Event``, for ``fn`` to look at) and raises
    ``cancelled`` in the thread, so even a calculation that does not look is stopped (when it next runs Python
    code). ``state["label"]`` / ``state["k"]`` / ``state["n"]`` are what ``fn`` tells about its progress.

    The result of ``fn`` is returned; what it raised is raised here."""
    state = state if state is not None else {}
    state.setdefault("cancel", threading.Event())
    state.setdefault("label", label)
    state.setdefault("k", 0)
    state.setdefault("n", 0)
    worker = _Worker(fn)
    t0 = time.perf_counter()
    prog: QProgressDialog | None = None
    worker.start()
    sent = False
    try:
        while worker.is_alive():
            if prog is None and time.perf_counter() - t0 > delay:
                prog = QProgressDialog(state["label"], "Cancel", 0, 0, parent)
                prog.setWindowTitle("Please wait")
                prog.setWindowModality(Qt.WindowModal)
                prog.setMinimumDuration(0)
                prog.setAutoClose(False)
                prog.setAutoReset(False)
                prog.show()
            if prog is None:   # before the window: the program repaints, but nothing can be clicked
                QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
            else:
                QApplication.processEvents()
                if prog.wasCanceled() and not sent:
                    state["cancel"].set()
                    if cancelled is not None:
                        _raise_in(worker, cancelled)
                    sent = True
                    prog.setLabelText("Cancelling...")
                else:
                    n = max(0, int(state["n"]))
                    prog.setMaximum(n)
                    prog.setValue(min(int(state["k"]), n) if n else 0)
                    prog.setLabelText(str(state["label"]))
            worker.join(0.02)
    finally:
        if prog is not None:
            prog.close()
        QApplication.processEvents()
    if worker.error is not None:
        raise worker.error
    return worker.result
