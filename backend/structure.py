"""Project structure: the editable floor / room / zone tree.

Site Progress needs a place to hang every number: a room. This module owns the
hierarchy — Project > Floor > Room for a hotel, Project > Level > Zone for a
mall, Project > Wing > Floor > Room for a hospital, or anything the engineer
builds by hand. It is deliberately dumb: it holds shape only. Progress, BOQ and
money live in other modules and reference these node ids.

Two ways to get a tree:
  1. A template  (hotel / mall / hospital / custom) — a quick starting shape.
  2. from_tracker(path) — read the real progress tracker and rebuild the exact
     floors and rooms it already contains, so onboarding is zero-setup: the
     engineer uploads the sheet they already keep and the structure appears.

Everything is plain dict/JSON so it round-trips to data/projects/<p>/structure.json
untouched. Node ids are stable across edits (a monotonic counter on the root),
so BOQ mappings and progress keyed by id never dangle when a room is renamed.
"""
import re
import json

import pandas as pd

try:
    from . import schema
except ImportError:
    import schema


# Node types allowed in the tree. 'project' is always the single root. The rest
# nest freely so hotel/mall/hospital all express with the same primitives.
LEAF = "room"                       # the unit everything rolls up from
CONTAINER_TYPES = ("project", "floor", "wing", "level", "zone", "block", "area")
ALL_TYPES = CONTAINER_TYPES + (LEAF,)


class Structure:
    """A mutable tree rooted at one project node.

    Node shape:  {"id": str, "type": str, "name": str, "children": [ ... ]}
    """

    def __init__(self, root):
        self.root = root
        # ensure a sequence counter exists so ids stay unique & stable
        self.root.setdefault("_seq", _max_seq(root))

    # ---- construction --------------------------------------------------
    @classmethod
    def new(cls, name="Project", kind="custom"):
        root = {"id": "p0", "type": "project", "name": name,
                "kind": kind, "children": [], "_seq": 0}
        return cls(root)

    # ---- id handling ---------------------------------------------------
    def _next_id(self, type_):
        self.root["_seq"] += 1
        return f"{type_[:3]}{self.root['_seq']}"

    def find(self, node_id, node=None):
        node = node or self.root
        if node.get("id") == node_id:
            return node
        for ch in node.get("children", []):
            hit = self.find(node_id, ch)
            if hit:
                return hit
        return None

    def _parent_of(self, node_id, node=None):
        node = node or self.root
        for ch in node.get("children", []):
            if ch.get("id") == node_id:
                return node
            hit = self._parent_of(node_id, ch)
            if hit:
                return hit
        return None

    # ---- edits (return the affected id, raise on bad input) ------------
    def add(self, parent_id, type_, name):
        if type_ not in ALL_TYPES:
            raise ValueError(f"unknown node type: {type_}")
        parent = self.find(parent_id)
        if parent is None:
            raise KeyError(f"no such parent: {parent_id}")
        if parent.get("type") == LEAF:
            raise ValueError("cannot add a child to a room")
        node = {"id": self._next_id(type_), "type": type_,
                "name": str(name).strip(), "children": []}
        parent.setdefault("children", []).append(node)
        return node["id"]

    def add_many(self, parent_id, type_, names):
        return [self.add(parent_id, type_, n) for n in names]

    def rename(self, node_id, name):
        node = self.find(node_id)
        if node is None:
            raise KeyError(node_id)
        node["name"] = str(name).strip()
        return node_id

    def remove(self, node_id):
        if node_id == self.root["id"]:
            raise ValueError("cannot remove the project root")
        parent = self._parent_of(node_id)
        if parent is None:
            raise KeyError(node_id)
        parent["children"] = [c for c in parent["children"]
                              if c.get("id") != node_id]
        return node_id

    # ---- reads ---------------------------------------------------------
    def rooms(self):
        """Every leaf room, in tree order, each with its ancestor path."""
        out = []

        def walk(node, path):
            if node.get("type") == LEAF:
                out.append({"id": node["id"], "name": node["name"],
                            "path": path})
                return
            for ch in node.get("children", []):
                walk(ch, path + [node["name"]])

        walk(self.root, [])
        return out

    def count_rooms(self):
        return len(self.rooms())

    def containers(self, type_):
        out = []

        def walk(n):
            if n.get("type") == type_:
                out.append(n)
            for ch in n.get("children", []):
                walk(ch)

        walk(self.root)
        return out

    # ---- serialisation -------------------------------------------------
    def to_dict(self):
        return self.root

    def to_json(self, **kw):
        return json.dumps(self.root, ensure_ascii=False, **kw)

    @classmethod
    def from_dict(cls, d):
        return cls(d)

    @classmethod
    def from_json(cls, s):
        return cls(json.loads(s))


def _max_seq(node):
    """Recover the highest numeric suffix already used, so re-loaded trees keep
    minting fresh ids instead of colliding."""
    best = 0
    m = re.search(r"(\d+)$", str(node.get("id", "")))
    if m:
        best = int(m.group(1))
    for ch in node.get("children", []):
        best = max(best, _max_seq(ch))
    return best


# --------------------------------------------------------------------------
# Templates — quick starting shapes. Names are plain so the engineer renames
# freely afterwards. None of these are Hyatt-specific.
# --------------------------------------------------------------------------
def hotel(name="Hotel", floors=None, rooms_per_floor=None, room_labels=None):
    """Project > Floor > Room. Give either room_labels (explicit, reused on
    every floor) or rooms_per_floor (auto Room 1..N)."""
    s = Structure.new(name, kind="hotel")
    floors = floors or ["Floor 1"]
    if room_labels is None:
        room_labels = [f"Room {i}" for i in range(1, (rooms_per_floor or 1) + 1)]
    for f in floors:
        fid = s.add(s.root["id"], "floor", f)
        s.add_many(fid, "room", room_labels)
    return s


def mall(name="Mall", levels=None, zones_per_level=None, zone_labels=None):
    """Project > Level > Zone (zones are the leaf 'room' unit)."""
    s = Structure.new(name, kind="mall")
    levels = levels or ["Ground", "Basement 1"]
    if zone_labels is None:
        zone_labels = [f"Zone {i}" for i in range(1, (zones_per_level or 1) + 1)]
    for lv in levels:
        lid = s.add(s.root["id"], "level", lv)
        # zones are leaves; use LEAF type so rollups treat them as rooms
        for z in zone_labels:
            s.add(lid, "room", z)
    return s


def hospital(name="Hospital", wings=None, floors=None, rooms_per_floor=None):
    """Project > Wing > Floor > Room."""
    s = Structure.new(name, kind="hospital")
    wings = wings or ["Wing A"]
    floors = floors or ["Floor 1"]
    labels = [f"Room {i}" for i in range(1, (rooms_per_floor or 1) + 1)]
    for w in wings:
        wid = s.add(s.root["id"], "wing", w)
        for f in floors:
            fid = s.add(wid, "floor", f)
            s.add_many(fid, "room", labels)
    return s


def custom(name="Project"):
    return Structure.new(name, kind="custom")


TEMPLATES = {"hotel": hotel, "mall": mall, "hospital": hospital, "custom": custom}


# --------------------------------------------------------------------------
# Zero-setup: rebuild the real structure from the progress tracker.
# --------------------------------------------------------------------------
_ROOMDETAIL_RE = re.compile(r"ROOM\s*DETAIL", re.I)
_TICKS = {"✓", "~", "✗", "√", "x", "X"}


def _room_detail_sheets(xl):
    return [s for s in xl.sheet_names if _ROOMDETAIL_RE.search(s)]


def _extract_floor_rooms(raw):
    """From one *-Room Detail sheet, return (floor_names, room_labels).

    Layout: repeating blocks. A header row has 'Activity' in col 1 and room
    labels across; the row under it (or the same block's first data row) names
    the floor in col 0. Room labels repeat per floor, so we take them from the
    first header and collect every distinct floor label."""
    room_labels, floors = None, []
    nrows, ncols = raw.shape
    _bare = {"FLOOR", "WING", "LEVEL", "BASEMENT", "ZONE", "BLOCK"}
    for r in range(nrows):
        row = [(_s(raw.iat[r, c])) for c in range(ncols)]
        is_header = len(row) > 1 and row[1].upper() == "ACTIVITY"
        if is_header:
            if room_labels is None:
                room_labels = [c for c in row[2:]
                               if c and c not in _TICKS
                               and not c.startswith("✓") and c not in ("✓", "~", "✗")]
            continue                       # header row: col0 'Floor' is a label, not a floor
        # a floor label sits in col 0 and reads like "13th Floor" — but not the
        # bare header word, and not the project/instructions banner
        c0 = row[0] if row else ""
        if re.search(r"\bFLOOR\b|\bBASEMENT\b|\bWING\b|\bLEVEL\b", c0, re.I) \
                and c0.upper() not in _bare \
                and "TICK" not in c0.upper() and "PROJECT" not in c0.upper():
            if c0 not in floors:
                floors.append(c0)
    return floors, (room_labels or [])


def _s(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("nan", "nat", "none") else s


def from_tracker(path, name=None):
    """Build a hotel-shaped Structure from a real progress tracker workbook.

    All service Room-Detail sheets share the same floors and rooms, so the
    first that yields both wins. Falls back to a bare custom project if no
    Room-Detail sheet parses — never raises, so a broken upload degrades to an
    empty editable tree rather than a 500."""
    xl = pd.ExcelFile(path)
    floors, rooms = [], []
    for sh in _room_detail_sheets(xl):
        raw = xl.parse(sheet_name=sh, header=None, dtype=object)
        f, rm = _extract_floor_rooms(raw)
        if f and rm:
            floors, rooms = f, rm
            break
    if name is None:
        name = "Project"
    if not (floors and rooms):
        return custom(name)
    return hotel(name, floors=floors, room_labels=rooms)
