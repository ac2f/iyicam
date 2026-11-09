"""iyiCAM as a service: the engine behind a plain JSON interface.

No window and no Controller: every operation takes the project as data and returns data.  An interface that
keeps the project somewhere else (a phone in a browser, talking to this program on a computer) uses it like this::

    from iyicam.service import ServiceHost
    host = ServiceHost()
    schema = host.handle("schema")                       # forms, posts, file formats, tools
    out = host.handle("import", {"name": "a.dxf", "data_b64": "..."})
    job = host.handle("job.start", {"op": "compute", "payload": {"project": out["project"]}})
    host.handle("job.status", {"id": job["id"]})

Operations: ``ping schema validate import export command`` (quick) and ``compute gcode simulate nest`` (jobs).
See ``docs/service.md``.
"""
from .errors import JobCancelled, ServiceError
from .host import LONG, QUICK, ServiceHost
from .ops import API_VERSION

__all__ = ["API_VERSION", "JobCancelled", "LONG", "QUICK", "ServiceError", "ServiceHost"]
