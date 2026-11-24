"""The vector library: drawings kept to be put in again (logos, letters, parts, details), in groups that can hold groups.

The library is a tree: a group has a name, its groups and its items, in their order (what is stacked under what is as the person
put it). An item is a drawing: its vectors (moved so that their box starts at 0, 0), its size and the names of the layers it came
from. It is kept in ``vector_library.json`` in the user folder and shared by every project.
"""
from __future__ import annotations

import copy
import json
import os
import uuid
from pathlib import Path

from . import paths
from .document import VectorObject, new_id, vector_from_dict, vector_to_dict
from .geom import BBox, Transform

FILE_NAME = "vector_library.json"
ROOT = "root"


class LibraryError(ValueError):
    pass


def _id() -> str:
    return uuid.uuid4().hex[:12]


def group(name: str) -> dict:
    return {"id": _id(), "name": name, "groups": [], "items": []}


class VectorLibrary:
    def __init__(self, root: dict | None = None, path: Path | None = None):
        self.root = root if root is not None else {"id": ROOT, "name": "", "groups": [], "items": []}
        self.root["id"] = ROOT
        self.path = path

    # -- the file ------------------------------------------------------------------------------------------------------
    @staticmethod
    def default_path() -> Path:
        return paths.user_dir() / FILE_NAME

    @classmethod
    def load(cls, path: Path | None = None) -> "VectorLibrary":
        path = Path(path or cls.default_path())
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            root = data.get("root") if isinstance(data, dict) else None
            if isinstance(root, dict):
                return cls(_clean(root), path)
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        return cls(None, path)

    def save(self, path: Path | None = None) -> None:
        path = Path(path or self.path or self.default_path())
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"format": "iyicam-vector-library", "version": 1, "root": self.root}, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, path)
        self.path = path

    # -- finding -------------------------------------------------------------------------------------------------------
    def find(self, node_id: str) -> tuple[dict | None, dict | None]:
        """``(node, the group it is in)``; the root is ``(root, None)``."""
        if node_id == ROOT:
            return self.root, None

        def walk(g):
            for sub in g["groups"]:
                if sub["id"] == node_id:
                    return sub, g
                hit = walk(sub)
                if hit[0] is not None:
                    return hit
            for it in g["items"]:
                if it["id"] == node_id:
                    return it, g
            return None, None
        return walk(self.root)

    @staticmethod
    def is_group(node: dict) -> bool:
        return "groups" in node

    def path_of(self, node_id: str) -> list[str]:
        """The names of the groups down to a node (``["Logos", "Customers"]``)."""
        out = []
        node, parent = self.find(node_id)
        while parent is not None and parent["id"] != ROOT:
            out.insert(0, parent["name"])
            node, parent = self.find(parent["id"])
        return out

    def items(self, g: dict | None = None) -> list[dict]:
        """Every item, in the order of the tree."""
        g = g or self.root
        return list(g["items"]) + [it for sub in g["groups"] for it in self.items(sub)]

    # -- groups ----------------------------------------------------------------------------------------------------------
    def add_group(self, name: str, parent_id: str = ROOT) -> dict:
        parent, _ = self.find(parent_id)
        if parent is None or not self.is_group(parent):
            raise LibraryError("a group is made in a group")
        name = name.strip()
        if not name:
            raise LibraryError("a group needs a name")
        g = group(name)
        parent["groups"].append(g)
        return g

    def rename(self, node_id: str, name: str) -> None:
        node, _ = self.find(node_id)
        if node is None or node_id == ROOT:
            raise LibraryError("there is no such group or item")
        if not name.strip():
            raise LibraryError("a name is needed")
        node["name"] = name.strip()

    def remove(self, node_id: str) -> None:
        """Remove a group (with everything in it) or an item."""
        node, parent = self.find(node_id)
        if node is None or parent is None:
            raise LibraryError("there is no such group or item")
        key = "groups" if self.is_group(node) else "items"
        parent[key] = [x for x in parent[key] if x["id"] != node_id]

    def move(self, node_id: str, to_group: str, index: int | None = None) -> None:
        """Put a group or an item in another group (or elsewhere in its own): at ``index`` among the groups or the items."""
        node, parent = self.find(node_id)
        target, _ = self.find(to_group)
        if node is None or parent is None or target is None or not self.is_group(target):
            raise LibraryError("it cannot go there")
        if self.is_group(node) and (target is node or self._inside(target["id"], node)):
            raise LibraryError("a group cannot go into itself")
        key = "groups" if self.is_group(node) else "items"
        old = parent[key].index(node)
        parent[key].pop(old)
        if parent is target and index is not None and index > old:
            index -= 1
        lst = target[key]
        lst.insert(len(lst) if index is None else max(0, min(index, len(lst))), node)

    def shift(self, node_id: str, step: int) -> None:
        """Up (-1) or down (+1) among its neighbours."""
        node, parent = self.find(node_id)
        if node is None or parent is None:
            return
        lst = parent["groups" if self.is_group(node) else "items"]
        i = lst.index(node)
        j = i + step
        if 0 <= j < len(lst):
            lst[i], lst[j] = lst[j], lst[i]

    def _inside(self, node_id: str, g: dict) -> bool:
        return any(sub["id"] == node_id or self._inside(node_id, sub) for sub in g["groups"])

    # -- items ------------------------------------------------------------------------------------------------------------
    def add_item(self, name: str, objs: list[VectorObject], project=None, group_id: str = ROOT) -> dict:
        """Keep vectors as an item: moved so that their box starts at 0, 0."""
        parent, _ = self.find(group_id)
        if parent is None or not self.is_group(parent):
            raise LibraryError("an item is put in a group")
        objs = [o for o in objs if o.contours]
        if not objs:
            raise LibraryError("select the vectors to keep")
        bb = BBox()
        for o in objs:
            bb = bb.union(o.bbox())
        T = Transform.translate(-bb.xmin, -bb.ymin)
        layers = {l.id: l.name for l in project.layers} if project is not None else {}
        vectors = []
        for o in objs:
            c = o.copy()
            c.transform(T)
            d = vector_to_dict(c)
            d["layer_name"] = layers.get(o.layer_id, "")
            vectors.append(d)
        item = {"id": _id(), "name": name.strip() or "Drawing", "size": [round(bb.width, 4), round(bb.height, 4)],
                "vectors": vectors}
        parent["items"].append(item)
        return item

    @staticmethod
    def vectors_of(item: dict, layer_id: str, at: tuple[float, float] = (0.0, 0.0), project=None,
                   own_layers: bool = False) -> list[VectorObject]:
        """New vectors of an item (new ids) with their box starting at ``at``: on ``layer_id``, or (``own_layers``, with the
        project) on layers of the names they came from."""
        out = []
        T = Transform.translate(*at)
        for d in item["vectors"]:
            d = copy.deepcopy(d)
            d["id"] = new_id()
            lid = layer_id
            if own_layers and project is not None and d.get("layer_name"):
                lid = project.ensure_layer(d["layer_name"]).id
            d["layer"] = lid
            o = vector_from_dict(d)
            o.transform(T)
            out.append(o)
        return out

    # -- several libraries ---------------------------------------------------------------------------------------------
    def export_group(self, node_id: str) -> dict:
        node, _ = self.find(node_id)
        if node is None:
            raise LibraryError("there is no such group or item")
        root = copy.deepcopy(node) if self.is_group(node) else {"id": ROOT, "name": "", "groups": [], "items": [copy.deepcopy(node)]}
        return {"format": "iyicam-vector-library", "version": 1, "root": root}

    def import_data(self, data: dict, group_id: str = ROOT) -> dict:
        """Put an exported library (or group) in a group, as a group of its own (new ids)."""
        root = data.get("root") if isinstance(data, dict) else None
        if not isinstance(root, dict):
            raise LibraryError("this is not a vector library")
        g = _renew(_clean(copy.deepcopy(root)))
        g["name"] = g.get("name") or "Imported"
        parent, _ = self.find(group_id)
        if parent is None or not self.is_group(parent):
            raise LibraryError("it is put in a group")
        parent["groups"].append(g)
        return g


def _clean(g: dict) -> dict:
    """A group as read from a file: what is not a group or an item is left out."""
    out = {"id": str(g.get("id") or _id()), "name": str(g.get("name") or ""), "groups": [], "items": []}
    for sub in g.get("groups") or []:
        if isinstance(sub, dict):
            out["groups"].append(_clean(sub))
    for it in g.get("items") or []:
        if isinstance(it, dict) and isinstance(it.get("vectors"), list):
            out["items"].append({"id": str(it.get("id") or _id()), "name": str(it.get("name") or "Drawing"),
                                 "size": list(it.get("size") or [0, 0]), "vectors": it["vectors"]})
    return out


def _renew(g: dict) -> dict:
    g["id"] = _id()
    for sub in g["groups"]:
        _renew(sub)
    for it in g["items"]:
        it["id"] = _id()
    return g
