"""Column detection for site stock registers.

Every site keeps its register in the same shape but with its own words:
"Material Description" here, "Item Name" there, "Balance" on one project and
"Total" on the next. Hard-coding one project's spelling is why the second
project failed to load at all.

This module reads a sheet and works out what each column means, then reports
exactly what it decided so a human can check it before trusting the numbers.
"""
import re
import difflib
from functools import lru_cache

# Ordered best-first: earlier entries win when several match.
SYNONYMS_RAW = {
    "material": [
        "MATERIAL DESCRIPTION", "ITEM DESCRIPTION", "MATERIAL NAME", "ITEM NAME",
        "PRODUCT NAME", "PRODUCT DESCRIPTION", "PARTICULARS", "DESCRIPTION",
        "MATERIAL", "ITEM", "PRODUCT",
    ],
    "unit": ["UNIT", "UOM", "U O M", "UNITS", "MEASURE"],
    "opening": [
        "OPENING STOCK", "OPENING BALANCE", "OPENING QTY", "OPENING QUANTITY",
        "OP STOCK", "OPENING", "OPG STOCK", "QTY", "QUANTITY",
    ],
    "balance": [
        "BALANCE", "CLOSING STOCK", "CLOSING BALANCE", "CLOSING", "BAL",
        "TOTAL", "STOCK", "STOCK IN HAND", "CLOSING QTY",
    ],
    "qty_in": [
        "IN", "INWARD", "INWARDS", "RECEIPT", "RECEIPTS", "RECEIVED", "GRN",
        "RECD", "INCOMING",
    ],
    "qty_out": [
        "OUT", "OUTWARD", "OUTWARDS", "ISSUE", "ISSUED", "ISSUES",
        "CONSUMPTION", "CONSUMED", "USED", "OUTGOING",
    ],
    "group": [
        "GROUPS", "GROUP", "CATEGORY", "SERVICE", "TRADE", "DISCIPLINE",
        "SECTION", "HEAD",
    ],
    "code": [
        "PRODUCT CODE", "ITEM CODE", "MATERIAL CODE", "PART CODE", "SKU", "CODE",
    ],
}

# Words that must never be read as a material name even though they look like one.
NOT_MATERIAL = frozenset({"SR NO", "S NO", "SL NO", "SERIAL NO", "SR", "NO"})

SYNONYMS = {k: tuple(v) for k, v in SYNONYMS_RAW.items()}

FUZZY_CUTOFF = 0.86


@lru_cache(maxsize=20000)
def _norm_str(s):
    s = s.strip().upper()
    s = re.sub(r"[._:#]+", " ", s)
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm(v):
    return _norm_str(str(v))


@lru_cache(maxsize=40000)
def _match_norm(n, key, fuzzy):
    """Does this normalised header mean `key`? Exact first, then a tight fuzzy
    pass so 'Materal Description' or 'Openning Stock' still resolve."""
    if not n or n in NOT_MATERIAL:
        return False
    opts = SYNONYMS[key]
    if n in opts:
        return True
    if not fuzzy or len(n) < 4:
        return False
    return bool(difflib.get_close_matches(n, opts, n=1, cutoff=FUZZY_CUTOFF))


def match_key(cell, key, fuzzy=True):
    return _match_norm(norm(cell), key, fuzzy)


def _rank(cell, key):
    """Lower is better. Used to pick between two columns that both match."""
    n = norm(cell)
    opts = SYNONYMS[key]
    return opts.index(n) if n in opts else len(opts)


def find_header_row(raw, max_scan=30):
    """The header row is the one naming a material column and at least one
    in/out pair. Among candidates, the row with the most in/out columns wins -
    that is the row driving the repeating date blocks."""
    best, best_score = None, 0
    for r in range(min(max_scan, len(raw))):
        vals = raw.iloc[r].tolist()
        has_mat = any(match_key(v, "material") for v in vals)
        n_in = sum(1 for v in vals if match_key(v, "qty_in", fuzzy=False))
        n_out = sum(1 for v in vals if match_key(v, "qty_out", fuzzy=False))
        if not (has_mat and n_in and n_out):
            continue
        score = n_in + n_out
        if score > best_score:
            best, best_score = r, score
    return best


def _first_col(hdr, key, limit=None, exclude=()):
    """Leftmost column matching `key`, preferring the better synonym."""
    hits = []
    for c in range(len(hdr) if limit is None else limit):
        if c in exclude:
            continue
        if match_key(hdr[c], key):
            hits.append((_rank(hdr[c], key), c))
    if not hits:
        return None
    hits.sort()
    return hits[0][1]


def detect(raw):
    """Return a mapping describing this sheet, or None if it is not a register."""
    hdr_row = find_header_row(raw)
    if hdr_row is None:
        return None
    hdr = raw.iloc[hdr_row]

    in_cols = [c for c in range(raw.shape[1])
               if match_key(hdr[c], "qty_in", fuzzy=False)]
    if not in_cols:
        return None

    # Work out the repeating block: IN, OUT and possibly a running balance.
    first = in_cols[0]
    out_off = None
    for off in (1, 2):
        if first + off < raw.shape[1] and match_key(hdr[first + off], "qty_out", fuzzy=False):
            out_off = off
            break
    if out_off is None:
        return None

    bal_off = None
    cand = first + out_off + 1
    if cand < raw.shape[1] and match_key(hdr[cand], "balance", fuzzy=False):
        bal_off = out_off + 1

    stride = (bal_off or out_off) + 1

    # Item columns live to the left of the first block.
    c_mat = _first_col(hdr, "material", limit=first)
    if c_mat is None:
        return None
    c_unit = _first_col(hdr, "unit", limit=first, exclude={c_mat})
    c_group = _first_col(hdr, "group", limit=first, exclude={c_mat, c_unit})
    c_code = _first_col(hdr, "code", limit=first, exclude={c_mat, c_unit, c_group})
    c_open = _first_col(hdr, "opening", limit=first,
                        exclude={c_mat, c_unit, c_group, c_code})

    # The date row sits above the header. Some files put a blank spacer row
    # between them, so check two rows up and keep whichever parses more dates.
    from .engine import count_parseable_dates
    date_row, best = None, -1
    for r in (hdr_row - 1, hdr_row - 2):
        if r < 0:
            continue
        n = count_parseable_dates([raw.iloc[r][c] for c in in_cols])
        if n > best:
            date_row, best = r, n
    if best <= 0:
        return None

    return {
        "header_row": int(hdr_row),
        "date_row": int(date_row),
        "col_material": int(c_mat),
        "col_unit": None if c_unit is None else int(c_unit),
        "col_group": None if c_group is None else int(c_group),
        "col_code": None if c_code is None else int(c_code),
        "col_opening": None if c_open is None else int(c_open),
        "in_cols": [int(c) for c in in_cols],
        "out_offset": int(out_off),
        "bal_offset": None if bal_off is None else int(bal_off),
        "stride": int(stride),
        "names": {
            "material": str(hdr[c_mat]).strip(),
            "unit": None if c_unit is None else str(hdr[c_unit]).strip(),
            "group": None if c_group is None else str(hdr[c_group]).strip(),
            "opening": None if c_open is None else str(hdr[c_open]).strip(),
            "qty_in": str(hdr[first]).strip(),
            "qty_out": str(hdr[first + out_off]).strip(),
            "balance": None if bal_off is None else str(hdr[first + bal_off]).strip(),
        },
    }


# Sheet tabs and Groups columns spell the same trade many ways, and one project
# ships a typo ("Plumning"). Fold them onto one label so filters behave.
SERVICE_RULES = [
    ("FFTG", "Fire fighting"), ("FIRE FIGHT", "Fire fighting"),
    ("ELECTRIC", "Electrical"), ("ELE", "Electrical"),
    ("PLUM", "Plumbing"), ("PHE", "Plumbing"), ("SANITARY", "Plumbing"),
    ("HVAC", "Fire & HVAC"), ("FIRE", "Fire & HVAC"),
    ("ELV", "ELV"), ("BMS", "ELV"),
]
_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
           "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def clean_service(raw, fallback="Other"):
    n = norm(raw)
    if not n or n == "NAN":
        return fallback
    words = [w for w in n.split()
             if not (w[:3] in _MONTHS or w.isdigit())]
    n = " ".join(words) or n
    for key, label in SERVICE_RULES:
        if key in n:
            return label
    return " ".join(w.capitalize() for w in n.split())
