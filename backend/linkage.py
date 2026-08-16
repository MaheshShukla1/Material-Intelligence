"""Linkage: join a BOQ item to the stock register material (and its forecast).

This is the seam with Material Intelligence. Site Progress knows a BOQ item by a
long prose description ("3 x 1.5 sq mm FRZH copper wires in 25 mm dia rigid PVC
conduit"); the stock register knows the same material by a terse name ("25MM PVC
PIPE", "2C X 1.5 SQMM FLEXIBLE CABLE"). A plain string-similarity match fails on
that gap. So we match on the parts that actually identify a material:

  * size tokens   — 1.5 SQMM, 25 MM, 6A, 2C, CAT6, 4 INCH   (strongest signal)
  * a type word   — PIPE / CONDUIT / WIRE / CABLE / BOX / SOCKET ...
  * sub-category  — from subcat.classify, so a wire never links to a pipe

The engine is never touched. This module only READS what Material Intelligence
already wrote (the per-run forecast frame: material, status, days_left, rate,
balance) and attaches the matching row to each BOQ item. Every match carries a
score and the tokens that drove it, and anything below the cutoff links to
nothing rather than to a wrong material — the same "never invent" rule the rest
of the codebase follows.
"""
import re

import pandas as pd

try:
    from . import schema, subcat
except ImportError:
    import schema, subcat


# --- size token extraction ------------------------------------------------
# Normalise the many spellings of the same size onto one token so "1.5 sq mm",
# "1.5sqmm", "1.5 sq. mm." all become "1.5SQMM".
def _norm(s):
    return re.sub(r"\s+", " ",
                  re.sub(r"[^A-Z0-9 ./]+", " ", str(s).upper())).strip()


_SIZE_PATTERNS = [
    # value + unit  (e.g. 1.5 SQMM, 25 MM, 4 INCH, 6 A, 2 C, 100 MM)
    (re.compile(r"(\d+(?:\.\d+)?)\s*SQ\.?\s*MM"), r"\1SQMM"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*SQMM"), r"\1SQMM"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*MM"), r"\1MM"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*INCH"), r"\1INCH"),
    (re.compile(r'(\d+(?:\.\d+)?)\s*"'), r"\1INCH"),
    (re.compile(r"\b(\d+)\s*A\b"), r"\1A"),          # 6A, 16A, 32A
    (re.compile(r"\b(\d+)\s*C\b"), r"\1C"),          # 2C, 3C core count
    (re.compile(r"\bCAT\s*6\s*A\b"), "CAT6A"),
    (re.compile(r"\bCAT\s*6\b"), "CAT6"),
    (re.compile(r"\bCAT\s*5\s*E?\b"), "CAT5"),
    (re.compile(r"\bRG\s*6\b"), "RG6"),
    (re.compile(r"\b(\d+)\s*M\b"), r"\1M"),          # 2M/3M metal box modules
]


def size_tokens(name):
    n = _norm(name)
    toks = set()
    for rx, repl in _SIZE_PATTERNS:
        for m in rx.finditer(n):
            toks.add(rx.sub(repl, m.group(0)).strip())
    # also bare "N x M" wire combos -> the individual sqmm values already caught
    return toks


# --- type words -----------------------------------------------------------
_TYPE_WORDS = ["CONDUIT", "PIPE", "WIRE", "CABLE", "BOX", "SOCKET", "SWITCH",
               "BEND", "COUPLER", "SADDLE", "CLAMP", "TRAY", "LIGHT", "LED",
               "FIXTURE", "METAL BOX", "BACK BOX", "MCB", "RCBO", "DB",
               "SPRINKLER", "VALVE", "DUCT", "INSULATION", "GRILLE", "DIFFUSER",
               "TAP", "BASIN", "TRAP", "GRIP", "SCREW", "CHANNEL", "PATTI"]


def type_tokens(name):
    n = " " + _norm(name) + " "
    return {w for w in _TYPE_WORDS if f" {w} " in n or w in n}


# --- grade / certification qualifiers --------------------------------------
# Two identically-sized, identically-typed items can still be fundamentally
# different products: a fire-rated cable vs a plain one, or a metal (GI/MS)
# pipe vs a plastic (CPVC) one. Real Hyatt data confirmed both: cables cost
# 2 days (plain) vs 13 days (fire-rated) pooled, and 61 confident PVC<->GI/MS
# fitting matches were found where the two sides are genuinely different
# materials.
#
# Words within one group are the same real thing - Indian trade naming for the
# same product varies ("PVC"/"UPVC"/"SWR" all commonly describe the same
# plastic drainage/plumbing pipe family). Words in DIFFERENT groups are a
# genuine material difference and must never be a confident match.
#
# Whole-word only, NEVER substring - short codes collide inside ordinary
# words otherwise ("ITEMS" contains "MS", "DIA"/"DIAMETER" contains "DI").
_QUALIFIER_GROUPS = {
    # cable / wire grade
    "FIRE-RATED": ["FIRE SURVIVAL", "FIRE RESISTANT", "FRLS", "FR LS", "LSZH",
                  "ZERO HALOGEN", "HRFR", "HR FR"],
    "SHIELDED":   ["SHIELDED", "SCREENED"],
    "ARMOURED":   ["ARMOURED"],
    "UNARMOURED": ["UNARMOURED"],
    "XLPE":       ["XLPE"],
    # pipe / fitting material - PVC/UPVC/SWR treated as one family (same
    # plastic pipe, different trade names for the same product)
    "PVC":        ["UPVC", "PVC", "SWR"],
    "CPVC":       ["CPVC"],
    "GI":         ["GI"],
    "MS":         ["MS"],
    "CI":         ["CI"],
    "DI":         ["DI"],
    "PPR":        ["PPR"],
    "HDPE":       ["HDPE"],
    # insulation material - different products/brands, not interchangeable
    "NITRILE":    ["NITRILE"],
    "AEROFLEX":   ["AEROFLEX", "AEROCELL"],
    "GLASSWOOL":  ["GLASSWOOL", "GLASS WOOL"],
    "ROCKWOOL":   ["ROCKWOOL", "ROCK WOOL"],
}


def qualifier_tokens(name):
    n = " " + _norm(name) + " "
    out = set()
    for canon, words in _QUALIFIER_GROUPS.items():
        if any(f" {w} " in n for w in words):
            out.add(canon)
    return out


def salient(name):
    return {"sizes": size_tokens(name),
            "types": type_tokens(name),
            "qualifiers": qualifier_tokens(name),
            "subcat": subcat.classify(name),
            "norm": _norm(name)}


# --- scoring --------------------------------------------------------------
def score(a, b):
    """0..1 similarity between two salient() dicts. Weighted toward the signals
    that actually identify a material: sub-category agreement and shared size.

    Size disagreement is decisive: if both names carry size tokens and none
    overlap (a 1.5 SQMM cable vs a 2.5 SQMM one), the score is capped below the
    confident threshold no matter how well the type/subcat agree — same family,
    wrong spec is a wrong link, not a confident one."""
    s = 0.0
    # sub-category agreement is a strong gate/booster
    if a["subcat"] != "Other" and a["subcat"] == b["subcat"]:
        s += 0.40
    # size overlap
    size_conflict = False
    if a["sizes"] and b["sizes"]:
        inter = a["sizes"] & b["sizes"]
        if inter:
            s += 0.40 * (len(inter) / max(len(a["sizes"]), len(b["sizes"])))
        else:
            size_conflict = True            # both sized, nothing shared
    # type-word overlap
    if a["types"] and b["types"]:
        inter = a["types"] & b["types"]
        if inter:
            s += 0.20 * (len(inter) / max(len(a["types"]), len(b["types"])))
    s = min(s, 1.0)
    # grade/certification qualifiers (FIRE SURVIVAL, ARMOURED, LSZH, ...) are
    # decisive like size: if either name carries one and they don't match
    # exactly, this is a different product, not a confident link - same
    # treatment as size_conflict below.
    # grade/certification qualifiers (FIRE SURVIVAL, ARMOURED, LSZH, ...) are
    # decisive like size: a plain cable and a fire-rated one must never be a
    # confident link. Intersection-based, same as sizes - real Tally naming
    # is inconsistent ("LSZH" vs "LSZH Fire Survival" for the same product
    # family), so sharing at least one qualifier still counts as compatible;
    # only a genuine mismatch (qualified vs plain, or disjoint qualifiers)
    # conflicts.
    qualifier_conflict = False
    if a["qualifiers"] or b["qualifiers"]:
        if a["qualifiers"] and b["qualifiers"]:
            if not (a["qualifiers"] & b["qualifiers"]):
                qualifier_conflict = True     # both qualified, nothing shared
        else:
            qualifier_conflict = True         # one qualified, one plain
    if size_conflict or qualifier_conflict:
        s = min(s, 0.45)                    # keep as review candidate, never confident
    return round(s, 3)


DEFAULT_CUTOFF = 0.35        # candidates worth showing for review
CONFIDENT = 0.60             # only above this do we assert a `best` link


def match(boq_items, stock_names, cutoff=DEFAULT_CUTOFF,
          confident=CONFIDENT, topn=3):
    """For each BOQ item, rank stock materials by score. Returns a dict:
        item_code -> {"best": name|None, "score": float, "confident": bool,
                      "candidates": [(name, score), ...]}

    Two tiers on purpose. Many BOQ electrical lines are *points* (a labour+
    material bundle: "SITC of 6A socket point"), not a single stock SKU, so a
    size word like "6A" can score against an unrelated "6A MCB". We therefore
    only assert `best` when score >= `confident`; weaker hits leave best=None
    (needs the engineer's eye) but still surface as `candidates`. Below `cutoff`
    nothing is shown. This never silently asserts a size-coincidence match.
    """
    stock_sal = [(nm, salient(nm)) for nm in stock_names]
    out = {}
    for _, it in boq_items.iterrows():
        code = "" if it.get("item_code") is None else str(it["item_code"]).strip()
        a = salient(it.get("description", ""))
        scored = sorted(((nm, score(a, sb)) for nm, sb in stock_sal),
                        key=lambda x: x[1], reverse=True)
        cands = [(nm, sc) for nm, sc in scored[:topn] if sc >= cutoff]
        top = scored[0] if scored else (None, 0.0)
        is_conf = top[1] >= confident
        out[code] = {"best": top[0] if is_conf else None,
                     "score": top[1], "confident": bool(is_conf),
                     "candidates": cands}
    return out


# --------------------------------------------------------------------------
# Read the stock register's material names (reuses schema detection). Handy for
# building a link table without going through a full forecast run.
# --------------------------------------------------------------------------
def stock_names_from_register(path, service=None):
    """{service: [material names]} for each register sheet that parses. If
    `service` is given, only that service's names are returned as a flat list."""
    xl = pd.ExcelFile(path)
    out = {}
    for sh in xl.sheet_names:
        raw = xl.parse(sheet_name=sh, header=None, dtype=object)
        try:
            m = schema.detect(raw)
        except Exception:
            m = None
        if not m:
            continue
        mc, hr = m["col_material"], m["header_row"]
        names = []
        for r in range(hr + 1, raw.shape[0]):
            v = raw.iat[r, mc]
            nm = "" if v is None else str(v).strip()
            if nm and nm.lower() != "nan":
                names.append(nm)
        out[schema.clean_service(sh)] = names
    if service is not None:
        # forecast side folds Fire+HVAC into "Fire & HVAC"; accept either label
        for k, v in out.items():
            if k == service or service in k or k in service:
                return v
        return []
    return out


# --------------------------------------------------------------------------
# Attach an existing forecast run to the link. READ-ONLY on engine output.
# --------------------------------------------------------------------------
# Real engine output columns (from engine.py): on-hand is `stock`, burn rate is
# `rate_per_day`, actual issued-to-date is `total_consumed`. Earlier names
# ("balance","rate") never existed in the frame, so those figures came back
# blank — this list matches what the run actually writes.
_FORECAST_COLS = ["material", "status", "days_left", "rate_per_day", "stock",
                  "total_consumed", "exhaust_date", "order_by", "unit", "trend",
                  "confidence"]


def attach_forecast(link, forecast_df):
    """Given a link table (from match) and a forecast DataFrame produced by the
    engine, return item_code -> forecast row (as a plain dict) for matched
    items. Missing columns are simply skipped, so a slimmer forecast frame still
    works. Nothing here computes a forecast — it only looks one up."""
    if forecast_df is None or len(forecast_df) == 0:
        return {}
    fdf = forecast_df.copy()
    fdf["_key"] = fdf["material"].astype(str).map(_norm)
    keep = [c for c in _FORECAST_COLS if c in fdf.columns]
    lut = {k: g.iloc[0][keep].to_dict() for k, g in fdf.groupby("_key")}
    out = {}
    for code, info in link.items():
        if info.get("best"):
            row = lut.get(_norm(info["best"]))
            if row is not None:
                out[code] = row
    return out
