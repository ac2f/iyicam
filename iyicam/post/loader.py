"""Load post-processor definitions (TOML) with ``extends`` inheritance."""
from __future__ import annotations

import copy
import tomllib
from pathlib import Path
from typing import Iterable

from .engine import PostProcessor

BUILTIN_DIR = Path(__file__).with_name("builtin")


def read_definition(path: Path) -> dict:
    with open(path, "rb") as fh:
        d = tomllib.load(fh)
    d.setdefault("id", path.stem)
    return d


def merge(base: dict, child: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in child.items():
        if k == "extends":
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    return out


def resolve(defs: dict[str, dict]) -> dict[str, dict]:
    """Resolve ``extends`` chains (unknown parents are ignored, cycles are broken)."""
    done: dict[str, dict] = {}

    def get(pid: str, seen: tuple[str, ...]) -> dict:
        if pid in done:
            return done[pid]
        d = defs[pid]
        parent = d.get("extends")
        if parent and parent in defs and parent not in seen:
            d = merge(get(parent, seen + (pid,)), d)
        done[pid] = d
        return d

    for pid in defs:
        get(pid, ())
    return done


def load_posts(dirs: Iterable[Path], known: dict[str, dict] | None = None) -> list[PostProcessor]:
    """Load every ``*.toml`` post in ``dirs`` (later directories override earlier ones)."""
    defs: dict[str, dict] = dict(known or {})
    paths: dict[str, Path] = {}
    for d in dirs:
        d = Path(d)
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.toml")):
            try:
                dd = read_definition(p)
            except (OSError, tomllib.TOMLDecodeError) as exc:
                print(f"iyiCAM: cannot read post {p}: {exc}")
                continue
            defs[dd["id"]] = dd
            paths[dd["id"]] = p
    resolved = resolve(defs)
    return [PostProcessor(resolved[i], paths.get(i)) for i in resolved if i in paths]


def load_post_file(path: Path, known: dict[str, dict] | None = None) -> PostProcessor:
    d = read_definition(Path(path))
    defs = dict(known or {})
    defs[d["id"]] = d
    return PostProcessor(resolve(defs)[d["id"]], Path(path))


def builtin_definitions() -> dict[str, dict]:
    return {p.stem: read_definition(p) for p in BUILTIN_DIR.glob("*.toml")}
