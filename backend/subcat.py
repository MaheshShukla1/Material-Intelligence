"""Sub-category detection from material names.

A register has no "type" column - the material name is all we have. But the
name almost always carries the type word: "2CX1.5 FIRE SURVIVAL CABLE" is a
cable, "25MM PVC PIPE" is a pipe. This maps a name to one sub-category so the
UI can offer a "show only cables" filter within a service.

Order matters: the first rule that matches wins, so more specific words come
before generic ones (a "CABLE TRAY" is a tray, not a cable). Anything with no
clear type word falls to "Other" rather than being forced into a wrong bucket.
"""
import re

# (label, [keywords]) - checked top to bottom, first hit wins.
RULES = [
    ("Duct",         ["DUCT", "DUCTING", "GI DUCT", "SPIRAL DUCT", "FLEXIBLE DUCT"]),
    ("Damper",       ["DAMPER", "VCD", "FIRE DAMPER", "VOLUME CONTROL"]),
    ("Grille/Diffuser",["GRILLE", "GRILL", "DIFFUSER", "LOUVER", "LOUVRE", "REGISTER"]),
    ("Insulation",   ["INSULATION", "NITRILE", "GLASSWOOL", "ROCKWOOL", "XLPE SHEET",
                      "THERMAL", "ACOUSTIC"]),
    ("Cable tray",   ["CABLE TRAY", "TRAY", "LADDER", "RACEWAY", "TRUNKING"]),
    ("Cable",        ["CABLE", "LSZH", "ARMOURED", "XLPE", "FRLS CABLE"]),
    ("Wire",         ["WIRE", "FLEXIBLE WIRE", "FLEXIBLE CABLE", "FRLS WIRE",
                      "FLEXIBLE COPPER WIRE"]),
    ("Conduit",      ["CONDUIT", "FLEXIBLE PIPE", "PVC FLEXIBLE"]),
    ("Pipe",         ["PIPE", "SWR", "UPVC", "CPVC", "GI PIPE", "MS PIPE", "TUBE"]),
    ("Bend/Fitting", ["BEND", "ELBOW", "COUPLER", "COUPLING", "REDUCER", "TEE",
                      "SOCKET", "NIPPLE", "UNION", "FLANGE", "P TRAP", "END CAP",
                      "COPLER", "COPLING", "REDUCING",
                      "DOOR CAP", "CLEAN OUT", "Y PIECE", "DOUBLE Y", "SINGLE Y"]),
    ("Saddle/Clamp", ["SADDLE", "CLAMP", "U CLAMP", "HANGER", "SUPPORT", "SPACER"]),
    ("Box/JB",       ["JUNCTION", " JB", "J B", "GANG BOX", "MODULAR BOX",
                      "CONCEALED BOX", "DB ", "DISTRIBUTION BOARD", "ENCLOSURE",
                      "METAL BOX", "MOD BOX", "MODULAR PLATE", "MOD PLATE",
                      "MOD ", "MOD BOX", "MOD PLATE", "GANG", "BACK BOX"]),
    ("Switch/Socket",["SWITCH", "SOCKET", "MODULAR", "MCB", "MCCB", "RCCB", "RCCCB",
                      "RCBO", "ELCB", "ISOLATOR", "STARTER", "CONTACTOR", "CHANGEOVER"]),
    ("Light",        ["LIGHT", "LED", "LAMP", "LUMINAIRE", "DOWNLIGHT",
                      "FIXTURE", "FITTING", "SPOT"]),
    ("Data/Network", ["CAT6", "CAT5", "CAT 6", "CAT 5", "RJ45", "FIBER", "FIBRE",
                      "OPTIC", "PATCH CORD", "PATCH PANEL", "DATA RACK", "RACK",
                      "SWITCH 24 PORT", "NETWORK"]),
    ("Security/CCTV",["CCTV", "CAMERA", "NVR", "DVR", "ACCESS CONTROL",
                      "CARD READER", "MOTION SENSOR", "SMOKE DETECTOR", "PIR SENSOR",
                      "SIREN", "INTRUSION"]),
    ("Sprinkler",    ["SPRINKLER", "NOZZLE", "DELUGE"]),
    ("Valve",        ["VALVE", "HYDRANT", "GATE VALVE", "BALL VALVE", "NRV",
                      "BUTTERFLY"]),
    ("Support/Structural", ["GI ROD", "MS ROD", "THREADED ROD", "ALL THREAD",
                      "C-CHANNEL", "C CHANNEL", "CHANNEL", "PATTI", "GRIP",
                      "STRUT", "BRACKET", "ANGLE", "FLAT", "UNISTRUT"]),
    ("Fastener",     ["SCREW", "BOLT", "NUT", "WASHER", "WOSHER", "FASTNER",
                      "FASTENER", "ANCHOR", "RAWL", "DASH", "STUD", "RIVET"]),
    ("Consumable",   ["TAPE", "SOLVENT", "LUBRICANT", "PRIMER", "WELDON", "GLUE",
                      "BLADE", "WHEEL", "CUTTER", "GREASE", "OIL", "SEALANT",
                      "SILICON", "TEFLON", "COTTON", "CLOTH", "BRUSH", "TAP ROLL",
                      "BENDING SPRING", "SPRING", "ROLL", "THREAD SEAL"]),
    ("Drill/Bit",    ["RCC BIT", "BIT", "DRILL", "HAMMER", "CORE"]),
    ("Connector",    ["MALE TOP", "FEMALE TOP", "CONNECTOR", "PLUG", "COUPLER TOP",
                      "LUG", "LUGS", "RING TYPE", "GLAND", "FERRULE", "PIN TOP",
                      "THIMBLE", "SOCKET TERMINAL"]),
]


def _norm(name):
    s = str(name).upper()
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def classify(name):
    n = " " + _norm(name) + " "
    # Tape/sealant/lubricant are consumables even when the name also contains a
    # structural word (e.g. "DUCT TAPE", "PIPE SEALANT").
    if any(w in n for w in (" TAPE ", " SEALANT ", " LUBRICANT ", " SOLVENT ")):
        return "Consumable"
    for label, kws in RULES:
        for kw in kws:
            k = kw if kw.startswith(" ") or kw.endswith(" ") else f" {kw} "
            # allow keyword to match as a substring token-ish: pad both sides
            if f" {kw.strip()} " in n or kw.strip() in n.split():
                return label
    return "Other"


def add_subcategory(df, name_col="material"):
    """Add a 'subcategory' column to a forecast/records DataFrame."""
    df = df.copy()
    df["subcategory"] = df[name_col].map(classify)
    return df
