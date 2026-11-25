"""JSON operations of the job features of the web app (see ops.py: JSON in, JSON out, ServiceError for what is wrong).

Every `op_*` here is an operation of ops.py too (ops.py takes them in), so the browser's engine and any other interface
call them by name. Keep the work in iyicam.core / cam / nesting / post: an op only translates JSON to it and back."""
from __future__ import annotations

from .errors import ServiceError  # noqa: F401

__all__: list[str] = []          # the names of the ops of this module (op_...)
