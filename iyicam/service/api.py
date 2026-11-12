"""What ``/api/<operation>`` may call and how an operation is answered.

The server answers it on its own computer (``iyicam serve``); the agent answers the same operations on the computer
of the person while the server only passes them on (``iyicam serve --hub`` and ``iyicam agent``).  Either way it is
this one function, so a page cannot tell the two apart.
"""
from __future__ import annotations

from .files import FILE_OPS, Files
from .host import ServiceHost

# what /api/<operation> may call (the whole service interface, nothing else)
API_OPS = frozenset({"ping", "schema", "validate", "import", "export", "command", "compute", "gcode", "simulate",
                     "nest", "job.start", "job.status", "job.cancel", "job.result",
                     "posts", "post_save", "post_delete", "post_preview", "gcode_explain"})

# an error code that is not "the request was wrong" (400) answers with another HTTP status
ERROR_STATUS = {"unknown_operation": 404, "job_not_found": 404, "job_not_finished": 409, "too_busy": 503,
                "internal_error": 500,
                # files of the computer (iyicam.service.files)
                "path_not_allowed": 403, "files_disabled": 403, "folder_not_found": 404, "file_not_found": 404,
                "file_exists": 409, "file_changed": 409, "file_too_large": 413, "payload_too_large": 413,
                "os_error": 500,
                # a hub that cannot reach the agent (iyicam.service.hub)
                "agent_offline": 503, "agent_timeout": 504, "reply_too_large": 502}


def status_of(out: dict) -> int:
    """The HTTP status of an answer.  An answer is an error when it carries an error CODE; ``job.status`` has an
    ``error`` field of its own (null while the job runs, the job's error object after it failed) and is an answer
    like any other."""
    code = out.get("error")
    return ERROR_STATUS.get(code, 400) if isinstance(code, str) else 200


def call(service: ServiceHost, files: Files | None, name: str, payload: dict) -> tuple[int, dict]:
    """Answer one operation: ``(HTTP status, answer)``.  Never raises."""
    try:
        if name in FILE_OPS:
            if files is not None:
                out = files.handle(name, payload)
            elif name == "status":              # (the page asks first, and learns that there is no disk to browse)
                out = {"files": False, "roots": [], "limits": {}}
            else:
                out = {"error": "files_disabled", "message": "this server does not give access to files"}
        elif name in API_OPS:
            out = service.handle(name, payload)
        else:
            out = {"error": "unknown_operation", "message": f"there is no operation '{name}'", "operation": name}
    except Exception as exc:                    # noqa: BLE001  (the service promises not to; if it ever does, say so)
        out = {"error": "internal_error", "message": f"{type(exc).__name__}: {exc}"}
    return status_of(out), out
