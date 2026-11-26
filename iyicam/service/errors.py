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


def as_list(payload: dict, key: str) -> list:
    """``payload[key]`` as a list (none: empty); anything else is a request of the wrong shape."""
    v = payload.get(key)
    if v is None or v == "":
        return []
    if not isinstance(v, (list, tuple)):
        raise ServiceError("bad_request", f"'{key}' must be a list", field=key)
    return list(v)


def as_dict(payload: dict, key: str) -> dict:
    """``payload[key]`` as an object (none: empty); anything else is a request of the wrong shape."""
    v = payload.get(key)
    if v is None or v == "":
        return {}
    if not isinstance(v, dict):
        raise ServiceError("bad_request", f"'{key}' must be an object", field=key)
    return v
