"""Errors of the service layer.

A :class:`ServiceError` carries a stable machine readable ``code`` (the interface that talks to the service maps
it to its own language), an English ``message`` and optional ``params``.  Anything else that goes wrong is a bug
and is reported as ``internal_error``.
"""
from __future__ import annotations


class ServiceError(Exception):
    def __init__(self, code: str, message: str, **params):
        super().__init__(message)
        self.code = code
        self.message = message
        self.params = params

    def to_dict(self) -> dict:
        out = {"error": self.code, "message": self.message}
        if self.params:
            out["params"] = self.params
        return out


class JobCancelled(BaseException):
    """Raised inside a job when it noticed that it was cancelled.  Not an ``Exception``: the strategies' own
    ``except Exception`` must not swallow it (the same reason as ``ComputeCancelled``)."""
