"""Activities and the activity -> BOQ mapping (the configure-once step).

The tracker speaks in ACTIVITIES ("Wall Piping", "Wire Pulling"). The BOQ speaks
in ITEMS ("25mm PVC conduit", "3x1.5 wire"). Which items belong to which
activity lives in the engineer's head. This module:

  1. parse_activities(tracker)  -> the real activity list per service, in the
     order the site tracks them.
  2. Mapping                    -> a plain, editable {service: {activity:
     [boq_item_codes]}} that saves to mapping.json and round-trips clean.
  3. suggest(activities, boq)   -> a first-guess mapping by keyword, so the
     engineer starts from mostly-ticked checkboxes instead of a blank grid.
     It is only a suggestion; the engineer confirms/edits, and that confirmed
     mapping — not this guess — is what drives every later number.

Everything here is generic. Nothing is hard-coded to Hyatt's exact spelling; the
activity keywords are trade words (piping, wiring, box, testing) that recur on
any MEP site.
"""
import re

import pandas as pd

try:
    from . import schema
except ImportError:
    import schema


# Sheet name -> Site-Progress service. Kept distinct (Fire != HVAC), matching
# boq._boq_service, so a service's activities and its BOQ items line up.
_SVC_RULES = [
    (re.compile(r"\bFAPA\b|FIRE ALARM|\bFAS\b", re.I), "FAPA"),
    (re.compile(r"FFTG|FIRE|SPRINKLER|HYDRANT", re.I), "Fire"),
    (re.compile(r"HVAC|CHILLED|\bCHW\b|DUCT|VENTILAT", re.I), "HVAC"),
    (re.compile(r"\bPHE\b|PLUMB|SANITARY|WATER|CPVC|PERT", re.I), "Plumbing"),
    (re.compile(r"ELE|ELECTRIC|\bELV\b", re.I), "Electrical"),
]
_ROOMDETAIL_RE = re.compile(r"ROOM\s*DETAIL", re.I)
_TICKS = {"✓", "~", "✗", "√", "x", "X"}


def _svc(sheet):
    for rx, label in _SVC_RULES:
        if rx.search(sheet):
            return label
    return schema.clean_service(sheet)


def _s(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("nan", "nat", "none") else s


def _activities_from_sheet(raw):
    """Ordered, de-duplicated activity names from one Room-Detail sheet.

    An activity row has a non-empty col-1 label that is not the word 'Activity'
    and carries tick cells (✓/~/✗) across its room columns. The first activity
    of each floor shares the floor's row, so we must not skip a row just because
    col 0 (the floor) is filled."""
    acts = []
    nrows, ncols = raw.shape
    for r in range(nrows):
        row = [_s(raw.iat[r, c]) for c in range(ncols)]
        if len(row) < 3:
            continue
        label = row[1]
        if not label or label.upper() == "ACTIVITY":
            continue
        has_tick = any(c in _TICKS for c in row[2:])
        if has_tick and label not in acts:
            acts.append(label)
    return acts


def parse_activities(path):
    """{service: [activity, ...]} from a progress tracker workbook."""
    xl = pd.ExcelFile(path)
    out = {}
    for sh in xl.sheet_names:
        if not _ROOMDETAIL_RE.search(sh):
            continue
        raw = xl.parse(sheet_name=sh, header=None, dtype=object)
        acts = _activities_from_sheet(raw)
        if acts:
            out[_svc(sh)] = acts
    return out


# --------------------------------------------------------------------------
# The mapping model.
# --------------------------------------------------------------------------
class Mapping:
    """{service: {activity: [item_code, ...]}} — editable, JSON-round-tripping.

    Item codes are BOQ item_code strings (e.g. '2.16'). Keeping codes, not
    descriptions, means a re-parsed BOQ with tweaked wording still resolves.
    """

    def __init__(self, data=None):
        self.data = data or {}

    def get(self, service, activity):
        return list(self.data.get(service, {}).get(activity, []))

    def set(self, service, activity, codes):
        self.data.setdefault(service, {})[activity] = list(dict.fromkeys(codes))
        return self

    def toggle(self, service, activity, code):
        cur = self.data.setdefault(service, {}).setdefault(activity, [])
        if code in cur:
            cur.remove(code)
        else:
            cur.append(code)
        return self

    def activities(self, service):
        return list(self.data.get(service, {}).keys())

    def unmapped(self, service, boq_codes):
        """BOQ item codes for this service not tied to any activity yet — the
        engineer's to-do list so nothing is silently forgotten."""
        used = set()
        for codes in self.data.get(service, {}).values():
            used.update(codes)
        return [c for c in boq_codes if c not in used]

    def to_dict(self):
        return self.data

    @classmethod
    def from_dict(cls, d):
        return cls(d or {})


# --------------------------------------------------------------------------
# Suggestion — generic keyword rules mapping an activity to likely BOQ items.
# Deliberately conservative: labour-only activities map to nothing; a testing
# step consumes no counted material. Better to under-suggest and let the
# engineer add than to over-suggest and have them hunt for wrong ticks.
# --------------------------------------------------------------------------
# activity keyword -> (item description/subcat keywords). '' target = labour,
# suggest nothing.
_ACT_RULES = [
    (("ZARI", "CHASING", "GROOV", "CHIPP", "CORE CUT", "CUTTING"), []),      # labour
    (("TEST", "PRESSURE", "LEAK", "HYDRO", "FLUSH"), []),                    # no material
    (("METAL BOX", "BACK BOX", "GANG", "MODULAR BOX"),
        ["BOX", "BACK BOX", "GI BOX", "MODULAR"]),
    (("WIRE", "WIRING", "PULING", "PULLING", "CABLE", "CAT6", "CAT 6"),
        ["WIRE", "SQ MM", "SQMM", "CABLE", "CAT6", "CAT 6", "CAT-6", "RJ", "FRZH", "ZHFR"]),
    (("WALL PIPING", "CELLING PIPING", "CEILING PIPING", "PIPING", "CONDUIT", "PIPE"),
        ["CONDUIT", "PVC PIPE", "PIPE", "RACE WAY", "RACEWAY", "RIGID PVC"]),
    (("DUCT", "FRESH AIR", "EXHAUST", "CHW"),
        ["DUCT", "PIPE", "DIA", "CHW", "COPPER PIPE"]),
    (("INSULAT",), ["INSULATION", "NITRILE", "AEROFLEX", "PUF", "THICK"]),
    (("DRAIN",), ["DRAIN", "PVC PIPE", "PIPE", "TRAP"]),
    (("SPRINKLER",), ["SPRINKLER", "PIPE", "DIA", "NOZZLE"]),
    (("LIGHT", "FIXTURE", "FITTING", "LUMINAIRE"),
        ["LIGHT", "LED", "FIXTURE", "LUMINAIRE", "DOWNLIGHT", "LAMP"]),
    (("SOCKET", "POWER POINT", "POINT"),
        ["SOCKET", "POINT", "SWITCH", "USB"]),
]


def _norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]+", " ", str(s).upper())).strip()


def _item_keywords(rule_targets):
    return [_norm(k) for k in rule_targets]


def suggest(activities, boq_items, service=None):
    """Return {activity: [item_code, ...]} first-guess ticks.

    activities : list of activity names (from parse_activities)
    boq_items  : DataFrame with item_code / description / subcategory
                 (from boq.parse_sheet)
    """
    if boq_items is None or len(boq_items) == 0:
        return {a: [] for a in activities}

    # pre-normalise each BOQ item's searchable text once
    codes = boq_items["item_code"].fillna("").astype(str).tolist()
    texts = [
        _norm(f"{d} {sc}")
        for d, sc in zip(boq_items["description"].fillna(""),
                         boq_items.get("subcategory", pd.Series([""] * len(boq_items))).fillna(""))
    ]

    out = {}
    for act in activities:
        an = _norm(act)
        targets = None
        for keys, tgt in _ACT_RULES:
            if any(_norm(k) in an for k in keys):
                targets = _item_keywords(tgt)
                break
        if not targets:                       # unknown or labour-only -> empty
            out[act] = []
            continue
        picked = []
        for code, txt in zip(codes, texts):
            if not code:
                continue
            if any(t and t in txt for t in targets):
                picked.append(code)
        out[act] = list(dict.fromkeys(picked))
    return out
