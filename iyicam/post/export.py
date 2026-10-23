"""Write G-code files for a sheet using a post-processor."""
from __future__ import annotations

from pathlib import Path

from ..cam.moves import Toolpath
from ..core.document import Project, Sheet
from ..core.registry import REGISTRY
from .engine import PostJob, PostProcessor


def post_toolpaths(project: Project, sheet: Sheet, toolpaths: list[Toolpath], post: PostProcessor,
                   file_name: str = "output", program_number: int = 1000) -> list[tuple[str, str]]:
    """``[(file suffix, gcode)]`` for the toolpaths of one sheet."""
    job = PostJob(toolpaths, project, sheet, file_name, program_number)
    REGISTRY.emit("before_post", job, post)
    files = post.generate_files(job)
    REGISTRY.emit("after_post", job, post, files)
    return files


def write_gcode(path: str | Path, project: Project, sheet: Sheet, toolpaths: list[Toolpath],
                post: PostProcessor, program_number: int = 1000) -> list[Path]:
    path = Path(path)
    if not path.suffix:
        path = path.with_suffix("." + post.extension)
    files = post_toolpaths(project, sheet, toolpaths, post, path.stem, program_number)
    path.parent.mkdir(parents=True, exist_ok=True)
    written = []
    for suffix, text in files:
        out = path.with_name(path.stem + suffix + path.suffix)
        # newline="" keeps the post's line endings (CRLF for Windows controllers)
        with open(out, "w", encoding="ascii", errors="replace", newline="") as fh:
            fh.write(text)
        written.append(out)
    return written
