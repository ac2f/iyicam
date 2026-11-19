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


def tool_groups(post: PostProcessor, toolpaths: list[Toolpath]) -> list[list[Toolpath]]:
    """The toolpaths of each file the post writes: all in one, or (``split_by_tool``) one file per run of one tool."""
    if not post.options.get("split_by_tool"):
        return [list(toolpaths)]
    groups: list[list[Toolpath]] = []
    for tp in toolpaths:
        if groups and groups[-1][-1].tool.number == tp.tool.number:
            groups[-1].append(tp)
        else:
            groups.append([tp])
    return groups


def named_files(project: Project, sheet: Sheet, toolpaths: list[Toolpath], post: PostProcessor, stem_of,
                program_number: int = 1000) -> list[tuple[str, str]]:
    """``[(file stem, gcode)]``: every file named by ``stem_of(its toolpaths)`` (with one file per tool each file is named by
    its own toolpaths; when two of them would have the same name the post's ``_1_T1`` ... is put after it)."""
    groups = tool_groups(post, toolpaths)
    stems = [stem_of(g) for g in groups]
    if len(groups) > 1 and len(set(s.lower() for s in stems)) < len(stems):
        stems = [f"{s}_{i + 1}_T{g[0].tool.number}" for i, (s, g) in enumerate(zip(stems, groups))]
    out = []
    for stem, g in zip(stems, groups):
        job = PostJob(g, project, sheet, stem, program_number)
        REGISTRY.emit("before_post", job, post)
        with post._lock:
            text = post.generate(job)
        REGISTRY.emit("after_post", job, post, [("", text)])
        out.append((stem, text))
    return out


def write_named(folder: str | Path, project: Project, sheet: Sheet, toolpaths: list[Toolpath], post: PostProcessor,
                stem_of, program_number: int = 1000) -> list[Path]:
    """:func:`named_files` written into ``folder``."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for stem, text in named_files(project, sheet, toolpaths, post, stem_of, program_number):
        out = folder / f"{stem}.{post.extension}"
        with open(out, "w", encoding="ascii", errors="replace", newline="") as fh:
            fh.write(text)
        written.append(out)
    return written
