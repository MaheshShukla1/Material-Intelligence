"""Tool / safety type detection from item names.

The Safety and Tools tabs are plain inventory, not MEP material. Running the
material sub-category classifier on them gives nonsense - "8 FEET LADDER" matched
the ladder-type *cable tray*, "MEASURE TAP 5MTR" matched a *sanitary tap*,
"HAMMER WITH HANDDLE 2LB" matched a *drill bit*. Those rules exist for cable and
pipe; a ladder is not a cable tray.

This module is the tool/safety equivalent of subcat.py: it maps an item name to
a tool-native type (Ladder, Hammer, Grinder, Welding Machine, Safety Shoes,
Helmet, ...). Same design as subcat: ordered rules, first hit wins, shape/purpose
words before generic ones, synonym-based so it works on any register - never
hard-coded to one project's exact spelling. Anything with no clear type word
falls to "Other" rather than being forced into a wrong bucket.
"""
import re

# (label, [keywords]) - checked top to bottom, first hit wins. Order matters:
# more specific / higher-precedence words come first so a "ROTARY HAMMER DRILL"
# lands on Drill (a drill) not Hammer, and a "MEASURE TAP" lands on Measuring
# Tape not on any tap/tape rule.
RULES = [
    # --- measuring tape must beat every TAP / TAPE rule below and elsewhere.
    ("Measuring Tape", ["MEASURE TAP", "MEASURING TAPE", "MEASURE TAPE",
                        "MEASURING TAP", "TAPE MEASURE", "INCH TAPE"]),

    # --- welding: the machine (needs MACHINE) before welding consumables.
    ("Welding Machine", ["WELDING MACHINE", "WELD MACHINE", "MIG", "ARC WELDER",
                         "WELDING SET", "WELDING INVERTER"]),
    ("Welding Glass",   ["WELDING GLASS", "WELDING BLACK GLASS",
                         "WELDING WHITE GLASS", "WELDING SHADE"]),
    ("Welding Rod",     ["WELDING ROD", "WELD ROD", "ELECTRODE"]),

    # --- powered machines. Drill before Hammer (a hammer drill is a drill).
    ("Drill Machine",   ["ROTARY HAMMER", "HAMMER DRILL", "DRILL MACHINE",
                         "DRILL", "STAND DRILL", "IMPACT DRILL"]),
    ("Grinder",         ["GRINDER", "GRINDING MACHINE"]),
    ("Screw Machine",   ["SCREW MACHINE", "SELF SCREW", "SCREW GUN",
                         "SCREW DRIVER MACHINE"]),

    # --- cutting: consumable wheels/blades before the cutter machine, and
    # holesaw (a bit) before the generic cutter.
    ("Wheel/Blade",     ["CUTTING WHEEL", "GRINDING WHEEL", "CUTTING BLADE",
                         "CUT WHEEL", "FLAP WHEEL", "WHEEL"]),
    ("Holesaw Cutter",  ["HOLLSAW", "HOLESAW", "HOLE SAW", "HOLL SAW"]),
    ("Cutter Machine",  ["CUTTER MACHINE", "CUTTER", "CUUTER", "CUTTING MACHINE",
                         "MARBLE CUTTER", "PIPE CUTTER", "TILE CUTTER"]),

    # --- hand tools.
    ("Spanner",         ["SPANNER", "SPANER", "PANA", "PANNA"]),
    ("Wrench",          ["WRENCH", "RANCH"]),
    ("Plier",           ["PLIER", "PLAIER", "PLAYER", "PILER", "NOSE PLIER"]),
    ("Hammer",          ["HAMMER", "SLEDGE", "HAMER"]),
    ("Hacksaw",         ["HACKSAW", "HACK SAW", "HAKSAW"]),
    ("Chisel",          ["CHISEL", "CHISSEL", "CHHINI"]),
    ("Screwdriver",     ["SCREW DRIVER", "SCREWDRIVER", "PECH KASH", "TESTER SET"]),
    ("Tester",          ["TESTER", "LINE TESTER"]),
    ("Spirit Level",    ["LEVEL STRIP", "SPIRIT LEVEL", "LEVEL BOTTAL",
                         "LEVEL TUBE", "LEVEL"]),
    ("Try Square",      ["TRY ANGLE", "TRY SQUARE", "SQUARES", "SET SQUARE"]),
    ("File",            ["BASTARD", "FLAT FILE", "ROUND FILE", "RASP"]),
    ("Crimping Tool",   ["CRIMPING", "CRIMP TOOL", "CRIMPPING"]),

    # --- access / structure.
    ("Scaffolding",     ["SCAFOLDING", "SCAFFOLDING", "CUPLOCK", "CUP LOCK",
                         "LEDGER", "STANDARD PIPE"]),
    ("Ladder",          ["LADDER", "STEP LADDER", "TELESCOPIC LADDER"]),

    # --- misc tools / consumables that show up on tools tabs.
    ("Brush",           ["BRUSH"]),
    ("Fan",             ["FAN"]),
    ("Pump",            ["PUMP"]),
    ("Gauge/Meter",     ["GAUGE", "GAUE", "METER", "WEIGHT MACHINE",
                         "TEST MACHINE", "EARTH TEST"]),

    # --- safety / PPE gear.
    ("Safety Shoes",    ["SAFETY SHOES", "SAFTY SHOES", "SHOES", "SHOE",
                         "GUM BOOT", "GUMBOOT"]),
    ("Helmet",          ["HELMET", "HELMAT", "HARD HAT"]),
    ("Jacket",          ["JACKET", "JECKET", "VEST", "REFLECTIVE JACKET"]),
    ("Gloves",          ["GLOVES", "GLOVE", "HANDGLOVES", "HAND GLOVES",
                         "HANDGLOVE"]),
    ("Goggles",         ["GOGGLES", "GOGGALES", "GOGGLE", "GLASSES", "SPECTACLE"]),
    ("Face Shield",     ["FACE SHIELD", "FACE MASK SHIELD", "VISOR"]),
    ("Mask",            ["MASK", "RESPIRATOR", "DUST MASK", "N95"]),
    ("Ear Plug",        ["EAR PLUG", "EARPLUG", "EAR MUFF", "EARMUFF"]),
    ("Apron",           ["APRON"]),
    ("Fire Blanket",    ["FIRE BLANKET", "BLANKET"]),
    ("Fire Extinguisher", ["EXTINGUISHER", "EXTINGUSHER", "EXTINGISHER"]),
    ("Fire Bucket",     ["FIRE BUCKET", "SAND BUCKET", "BUCKET AND STAND",
                         "BUCKET"]),
    ("Safety Harness",  ["HARNESS", "SAFETY BELT", "SEFTY BELT", "BODY BELT",
                         "FALL ARREST", "LANYARD"]),
    ("First Aid",       ["FIRST AID", "FRIST AID", "FIRST-AID"]),
    ("Barrication Tape", ["BARRICATION", "BARRICADE", "BARICATION", "CAUTION TAPE"]),
]


def _norm(name):
    s = str(name).upper()
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# Matching is normally whole-word OR substring - the substring pass is what
# catches glued / mis-spelled real spellings ("HANDGLOVES", "LEVEL STRIPT").
# But a few short keywords collide inside unrelated longer words as substrings
# ("PANA" in PANASONIC, "VEST" in HARVEST, "MASK" in MASKING TAPE, "METER" in
# DIAMETER). Those must match as a whole word only, never as a substring, so a
# stray battery or masking tape on a tools tab is not mislabelled. This only
# ever affects the Safety/Tools *type label*, never the forecast.
WHOLEWORD_ONLY = {"PANA", "PANNA", "VEST", "MASK", "METER", "FAN", "WHEEL",
                  "BUCKET", "SHOE", "SHOES", "PLAYER", "RANCH", "LEVEL",
                  "BLANKET", "PUMP", "FILE"}


def classify(name):
    """Map a tool/safety item name to one tool-native type, or 'Other'."""
    n = " " + _norm(name) + " "
    for label, kws in RULES:
        for kw in kws:
            k = _norm(kw)
            if f" {k} " in n:                 # whole-word: always allowed
                return label
            if k not in WHOLEWORD_ONLY and k in n:   # substring: only for safe kws
                return label
    return "Other"


# Sizes are only reliable when written into the item name (the Hyatt safety
# register has "SAFETY SHOES SIZE 6 NO", "SIZE-8,9,10"). Pull the number(s) after
# a SIZE marker. If no size is present, return None - never invent one.
_SIZE_RE = re.compile(r"SIZE\s*[-:]?\s*([0-9]{1,2}(?:\s*[,/&and]+\s*[0-9]{1,2})*)",
                      re.I)


def extract_size(name):
    """Return a normalised size string from the item name, or None.

    "SAFETY SHOES SIZE 6 NO"        -> "6"
    "SAFETY SHOES LABOUR SIZE-8,9,10" -> "8, 9, 10"
    "ORANGE HELMET"                 -> None
    """
    s = str(name)
    m = _SIZE_RE.search(s)
    if not m:
        return None
    nums = re.findall(r"[0-9]{1,2}", m.group(1))
    if not nums:
        return None
    # de-dup while preserving order
    seen, out = set(), []
    for x in nums:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return ", ".join(out)


def add_tooltype(df, services, name_col="material", svc_col="service"):
    """Add 'tool_type' and 'tool_size' columns for rows whose service is one of
    `services` (the inventory-only services). MEP rows are left as None, so this
    can never affect the forecast table."""
    df = df.copy()
    # Need both a service column (to know which rows are inventory) and a name
    # column (to classify from). If either is missing, tag nothing rather than
    # crash - a synced register with odd headers must never 500 the upload.
    if svc_col not in df.columns or name_col not in df.columns:
        df["tool_type"] = None
        df["tool_size"] = None
        return df
    inv = df[svc_col].isin(tuple(services))
    df["tool_type"] = None
    df["tool_size"] = None
    if inv.any():
        df.loc[inv, "tool_type"] = df.loc[inv, name_col].map(classify)
        # Size is only meaningful for footwear. A "SIZE" number on a helmet,
        # jacket or fire blanket ("SIZE - 1MTRX2MTR") is not a shoe size, so we
        # only pull it where the type is Safety Shoes - never invent one.
        shoes = inv & (df["tool_type"] == "Safety Shoes")
        if shoes.any():
            df.loc[shoes, "tool_size"] = df.loc[shoes, name_col].map(extract_size)
    return df


# Shoe sizes in the PPE issue log are hand-typed and messy: "6 NUMBER",
# "6NUMBER", "7 NIMBER", "9*NUMBER", "9 SHOESH", "7 NUMBER\\". Reduce to just the
# leading 1-2 digit number so the size filter groups them. No size -> None.
def normalise_ppe_size(raw):
    if raw is None:
        return None
    m = re.search(r"([0-9]{1,2})", str(raw))
    return m.group(1) if m else None
