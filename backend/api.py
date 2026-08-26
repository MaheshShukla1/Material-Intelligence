"""HTTP layer. Thin on purpose - all real work lives in engine.py / health.py."""
import json
import re
import shutil
import uuid
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import engine, health, leadtime, linkage, schema, subcat, toolcat

ROOT = Path(__file__).resolve().parent.parent
UPLOADS = ROOT / "data" / "uploads"
RUNS = ROOT / "data" / "runs"
# Real per-material lead time, baked in from Mahesh's PO+GRN registers as a
# plain JSON file that ships alongside the code - not an upload, not built at
# runtime. It is a property of supplier dispatch times, not of any one site,
# so one file covers every project (confirmed with Mahesh: no site filter).
# To refresh it later: rerun leadtime.build_aggregates() + to_json_dict() on
# a newer PO/GRN export and replace this file - still no upload UI needed.
LEADTIME_LOOKUP_PATH = Path(__file__).resolve().parent / "data" / "leadtime_lookup.json"
FRONTEND = ROOT / "frontend"
for p in (UPLOADS, RUNS):
    p.mkdir(parents=True, exist_ok=True)


def _load_leadtime_aggregates():
    """The baked-in PO->GRN aggregates, or None if the data file is absent /
    unreadable. Never raises - a missing/corrupt file just means every
    material falls back to the global lead_time, exactly like before this
    feature existed."""
    return leadtime.load_aggregates_json(LEADTIME_LOOKUP_PATH)

# Tabs the register parser must never treat as MEP material movement.
# Item masters and summary/index tabs carry no dated in/out, so they stay out.
# PPE is a per-person issue log, not a stock register - it is pulled separately
# by parse_ppe_log() and shown as an issue-log tab, so it is skipped here too.
# SAFETY and TOOL used to be skipped; they ARE stock registers (item + count),
# so they now flow through the normal parser and are marked inventory-only
# afterwards (INVENTORY_SERVICES) rather than being dropped.
SKIP_SHEETS = ["PPE", "ITEMMASTER", "MASTER",
               "SUMMARY", "INDEX", "SHEET1"]

# Services shown as plain stock counts: no forecast, no RED/AMBER, no runs-out
# date. Helmets and drills do not "run out" like cable, so a shortage alert on
# them would be noise. Populated by schema.clean_service folding Safety/Tools
# tabs (or Groups values) onto these exact labels.
INVENTORY_SERVICES = ("Safety", "Tools")

app = FastAPI(title="Material Intelligence")


# ------------------------------------------------------------------ helpers
def detect(path):
    try:
        head = pd.read_excel(path, nrows=1)
        if {"Material", "Quantity", "Document Date"}.issubset(head.columns):
            return "projectbase"
    except Exception:
        pass
    return "register"


def sheet_plan(path):
    xl = pd.ExcelFile(path)
    keep, skipped = {}, []
    for s in xl.sheet_names:
        flat = re.sub(r"[^A-Z]", "", s.upper())
        if any(h in flat for h in SKIP_SHEETS):
            skipped.append({"sheet": s, "why": "excluded by name"})
            continue
        keep[s] = s
    return keep, skipped


# Columns the PPE issue log is expected to carry. Matching is by meaning, not
# exact spelling, so "CONTARCTOR NAME" (a real typo in the field) still lands.
PPE_FIELDS = {
    "date": ["DATE"],
    "name": ["NAME", "WORKER", "EMPLOYEE", "PERSON"],
    "shoes_size": ["SHOES NUMBER", "SHOE SIZE", "SHOES SIZE", "SIZE"],
    "shoes": ["SHOES", "SHOE", "SAFETY SHOES"],
    "helmet": ["HELMET"],
    "jacket": ["JACKET", "VEST", "REFLECTIVE JACKET"],
    "blanket": ["FAIR BLANKET", "BLANKET", "FIRE BLANKET"],
    "unit": ["UNIT", "UOM"],
    "contractor": ["CONTRACTOR NAME", "CONTRACTOR", "CONTARCTOR NAME", "CONTARCTOR",
                   "AGENCY", "VENDOR"],
    "signature": ["SIGNATURE", "SIGN"],
}


def _ppe_sheet_names(path):
    """Names of tabs that are PPE issue logs (skipped by the register parser)."""
    out = []
    for s in pd.ExcelFile(path).sheet_names:
        flat = re.sub(r"[^A-Z]", "", s.upper())
        if "PPE" in flat:
            out.append(s)
    return out


def parse_ppe_log(path, max_rows=5000):
    """Read a per-person PPE issue sheet into a plain 'who got what' log.

    The PPE tab is NOT a stock register - it is one row per person per issue
    (DATE, NAME, SHOES, HELMET, JACKET, CONTRACTOR...). So it never goes through
    the forecast pipeline. We locate the header row by finding the row that
    names a NAME column plus at least one PPE item column, map columns by
    meaning, and return tidy records plus a small summary for the UI.

    Returns None when the file has no PPE tab, so callers can simply skip it.
    """
    names = _ppe_sheet_names(path)
    if not names:
        return None

    def col_for(hdr_vals, field):
        wanted = PPE_FIELDS[field]
        for c, v in enumerate(hdr_vals):
            n = schema.norm(v)
            if n in wanted or any(w == n for w in wanted):
                return c
        # loose contains-match as a fallback (e.g. "SHOES " with trailing space)
        for c, v in enumerate(hdr_vals):
            n = schema.norm(v)
            if n and any(w in n or n in w for w in wanted):
                return c
        return None

    records, sheets_used = [], []
    for sheet in names:
        raw = pd.ExcelFile(path).parse(sheet, header=None)
        if raw.empty:
            continue
        # find header row: the first row (scan up to 15) naming NAME + an item
        hdr_row = None
        for r in range(min(15, len(raw))):
            vals = raw.iloc[r].tolist()
            has_name = col_for(vals, "name") is not None
            has_item = any(col_for(vals, k) is not None
                           for k in ("shoes", "helmet", "jacket"))
            if has_name and has_item:
                hdr_row = r
                break
        if hdr_row is None:
            continue
        hdr = raw.iloc[hdr_row].tolist()
        cmap = {k: col_for(hdr, k) for k in PPE_FIELDS}
        body = raw.iloc[hdr_row + 1:]
        name_c = cmap["name"]
        for _, row in body.iterrows():
            nm = row.iloc[name_c] if name_c is not None else None
            if pd.isna(nm) or not str(nm).strip():
                continue
            rec = {}
            for k, c in cmap.items():
                if c is None or c >= len(row):
                    rec[k] = None
                    continue
                v = row.iloc[c]
                if pd.isna(v):
                    rec[k] = None
                elif isinstance(v, (dt.datetime, pd.Timestamp)):
                    rec[k] = pd.Timestamp(v).strftime("%Y-%m-%d")
                else:
                    rec[k] = str(v).strip()
            records.append(rec)
            if len(records) >= max_rows:
                break
        sheets_used.append(sheet)

    if not records:
        return None

    def issued_count(field):
        n = 0
        for r in records:
            v = (r.get(field) or "").strip()
            if v and v not in ("-", "0", "NAN", "NONE"):
                n += 1
        return n

    summary = {
        "people": len({r.get("name") for r in records if r.get("name")}),
        "issues": len(records),
        "shoes": issued_count("shoes"),
        "helmet": issued_count("helmet"),
        "jacket": issued_count("jacket"),
        "blanket": issued_count("blanket"),
        "sheets": sheets_used,
    }
    return {"records": records, "summary": summary}


NOISE = (r"stock|register|registers|material|materials|inward|outward|report|"
         r"data|sheet|final|copy|new|old|updated|availability|valuation|"
         r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|"
         r"and|of|the|for|with|20\d\d|\d{1,4}")


def project_from_filename(name):
    """Guess a project name from the file name, and admit when it cannot."""
    stem = re.sub(r"[_\-]+", " ", Path(name).stem)
    stem = re.sub(r"[^\w &]+", " ", stem)
    stem = re.sub(rf"\b({NOISE})\b", " ", stem, flags=re.I)
    stem = re.sub(r"\s+", " ", stem).strip()
    if len(re.sub(r"[^A-Za-z]", "", stem)) < 3:
        return "Untitled project"
    return stem.title()


def safe_slug(name):
    s = re.sub(r"[^A-Za-z0-9]+", "-", (name or "").strip()).strip("-").lower()
    return s or "untitled"


def jsonable(df):
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[c]):
            out[c] = out[c].dt.strftime("%Y-%m-%d")
    return json.loads(out.replace({np.nan: None}).to_json(orient="records"))


def load_run(run_id):
    d = RUNS / run_id
    if not d.exists() or not (d / "meta.json").exists():
        raise HTTPException(404, "run not found")
    return pd.read_parquet(d / "forecast.parquet"), json.loads((d / "meta.json").read_text())


def all_meta():
    out = []
    for d in RUNS.iterdir():
        m = d / "meta.json"
        if m.exists():
            try:
                out.append(json.loads(m.read_text()))
            except Exception:
                pass
    return sorted(out, key=lambda j: j.get("created", ""), reverse=True)


def summarise(f):
    counts = f.status.value_counts().to_dict()
    return {
        "counts": {k: int(v) for k, v in counts.items()},
        "materials": int(len(f)),
        "act_today": int(f.status.isin(["STOCKED_OUT", "RED"]).sum()),
        "idle_lines": int(f.status.isin(["OVERSTOCK", "DEAD_STOCK", "NO_RECENT_USE"]).sum()),
        "services": sorted(f.service.dropna().unique().tolist()),
        "overdue_orders": int((f.order_by.notna() &
                               (f.order_by < pd.Timestamp.now())).sum()),
    }


# ------------------------------------------------------------------ routes
@app.post("/api/upload")
async def upload(file: UploadFile = File(...),
                 lead_time: int = Form(7),
                 project: str = Form(""),
                 asof: str = Form("")):
    if not file.filename.lower().endswith((".xlsx", ".xlsm", ".xls")):
        raise HTTPException(400, "upload an Excel file (.xlsx, .xlsm or .xls)")

    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    raw = UPLOADS / f"{run_id}__{file.filename}"
    with raw.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)

    project = (project or "").strip() or project_from_filename(file.filename)
    return _process_file(raw, run_id, project, file.filename,
                         lead_time, asof, source_link=None)


def _process_file(raw, run_id, project, filename, lead_time, asof, source_link):
    """Parse a saved Excel file, build the forecast, and persist the run.

    Shared by manual upload and Google-Sheet sync so both go through exactly
    the same parsing, date-repair, forecast and gate logic - a synced run is
    never treated differently from an uploaded one.
    """
    kind = detect(raw)
    try:
        if kind == "projectbase":
            mv, rep = engine.parse_projectbase_movement(raw)
            rep = {"sheets": [], "skipped": [], "date_swaps": 0, **rep}
        else:
            plan, name_skips = sheet_plan(raw)
            mv, rep = engine.parse_site_register(raw, plan)
            rep["skipped"] = name_skips + rep.get("skipped", [])
    except Exception as e:
        raw.unlink(missing_ok=True)
        raise HTTPException(422, f"could not read this file: {e}")

    if mv.empty:
        tried = ", ".join(s["sheet"] for s in rep.get("skipped", [])) or "none"
        raw.unlink(missing_ok=True)
        raise HTTPException(
            422, "no stock movement found. Every sheet was skipped "
                 f"({tried}). A register needs a material column plus IN and "
                 "OUT columns under dates.")

    daily = engine.build_daily(mv)
    moved = daily[(daily.qty_out > 0) | (daily.qty_in > 0)]
    asof_ts = pd.Timestamp(asof) if asof else (
        moved.date.max() if len(moved) else daily.date.max())

    issues, stats = health.check(mv, daily, asof_ts)

    # Real per-material lead time (from actual PO->GRN history), when a
    # lookup has been built. A material with no usable history in it simply
    # is not in the dict, and engine.forecast() falls back to the global
    # `lead_time` for that one material - never invented, never blocked on.
    lt_lookup, lt_report = None, None
    agg = _load_leadtime_aggregates()
    if agg is not None:
        materials = sorted(daily.material.dropna().unique().tolist())
        lt_lookup = leadtime.match_materials(materials, agg)
        lt_report = {"materials_in_run": len(materials),
                     "materials_with_real_lead_time": len(lt_lookup)}

    f = engine.forecast(daily[daily.date <= asof_ts],
                        asof=asof_ts, lead_time=lead_time,
                        today=pd.Timestamp.now().normalize(),
                        lead_time_by_material=lt_lookup)
    f = subcat.add_subcategory(f)
    if not stats["forecast_ready"]:
        f = health.suppress(f)

    # Inventory-only override. Safety and Tools are stock counts, not burn-rate
    # forecasts: a helmet does not "run out" like cable, so any RED/AMBER/date on
    # them is noise that erodes trust. Runs AFTER forecast() and health.suppress()
    # so it is the final word, and touches ONLY rows whose service is Safety/Tools
    # - every MEP row is provably untouched (those services never carry this tag).
    if len(f) and "service" in f.columns:
        inv = f.service.isin(INVENTORY_SERVICES)
        if inv.any():
            f.loc[inv, "status"] = "INVENTORY"
            for c in ("days_left", "rate_per_day"):
                if c in f.columns:
                    f.loc[inv, c] = np.nan
            for c in ("exhaust_date", "exhaust_earliest", "exhaust_latest",
                      "order_by"):
                if c in f.columns:
                    f.loc[inv, c] = pd.NaT

    # Tag Safety/Tools rows with a tool-native type (Ladder, Hammer, Safety
    # Shoes...) using the dedicated tool classifier - NOT the material subcat,
    # which mislabels them (a ladder is not a cable tray). MEP rows are left
    # untouched (tool_type stays None), so the forecast table is unaffected.
    f = toolcat.add_tooltype(f, INVENTORY_SERVICES)

    # PPE is a per-person issue log, parsed separately and stored alongside the
    # run so the UI can show a "who was issued what" tab. Absent -> simply None.
    ppe = None
    if kind != "projectbase":
        try:
            ppe = parse_ppe_log(raw)
        except Exception:
            ppe = None

    d = RUNS / run_id
    d.mkdir(parents=True, exist_ok=True)
    f.to_parquet(d / "forecast.parquet")
    daily.to_parquet(d / "daily.parquet")
    if ppe:
        (d / "ppe.json").write_text(json.dumps(ppe))
    meta = {"run_id": run_id, "project": project, "project_slug": safe_slug(project),
            "filename": filename, "source": kind,
            "lead_time": lead_time, "mapping": rep,
            "created": dt.datetime.now().isoformat(timespec="seconds"),
            "stats": stats, "issues": issues,
            "has_ppe": bool(ppe),
            "ppe_summary": ppe["summary"] if ppe else None,
            "leadtime": lt_report}
    if source_link:
        meta["source_link"] = source_link
    (d / "meta.json").write_text(json.dumps(meta, indent=2))
    return {"run_id": run_id, "meta": meta, "summary": summarise(f)}


def _fetch_sheet_xlsx(link, dest):
    """Download a published Google Sheet (or any direct xlsx URL) to `dest`.

    Google 'Publish to web' links redirect to a googleusercontent host and need
    a browser-like User-Agent, so we follow redirects and set one. The result
    must actually be an xlsx (zip), not an HTML error page - we check the magic
    bytes so a bad link fails loudly here instead of deep in the parser.
    """
    import urllib.request

    req = urllib.request.Request(link, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
    except Exception as e:
        raise HTTPException(400, f"could not reach the sheet link: {e}")
    # xlsx files are zip archives and start with 'PK'. HTML error pages don't.
    if data[:2] != b"PK":
        raise HTTPException(
            400, "the link did not return an Excel file. Make sure the sheet is "
                 "Published to web as Microsoft Excel (.xlsx) and the link ends "
                 "in output=xlsx.")
    with open(dest, "wb") as fh:
        fh.write(data)


@app.post("/api/sync-sheet")
def sync_sheet(link: str = Form(...),
               lead_time: int = Form(7),
               project: str = Form(""),
               asof: str = Form("")):
    """Pull the latest data straight from a published Google Sheet and build a
    run from it - no manual download or upload. Same parser, same forecast.

    Each distinct sheet link is its own project, so syncing a second sheet does
    not overwrite the first. Re-syncing the *same* link reuses that project's
    name, so it updates in place (a new run under the same project) instead of
    piling up unrelated 'Live Sheet' runs.
    """
    link = link.strip()
    if not link.startswith("http"):
        raise HTTPException(400, "enter a valid https link to the published sheet")

    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    fname = "google-sheet.xlsx"
    raw = UPLOADS / f"{run_id}__{fname}"
    _fetch_sheet_xlsx(link, raw)

    project = (project or "").strip()
    if not project:
        # Re-use the project name already tied to this exact link, if any, so a
        # re-sync updates the same project rather than creating a new one.
        for j in all_meta():
            if j.get("source_link") == link and j.get("project"):
                project = j["project"]
                break
    if not project:
        # First time we see this link: give it a short stable label derived from
        # the link, so different sheets land in different projects.
        import hashlib
        tag = hashlib.md5(link.encode()).hexdigest()[:4].upper()
        project = f"Live Sheet {tag}"

    return _process_file(raw, run_id, project, fname,
                         lead_time, asof, source_link=link)


@app.get("/api/projects")
def projects():
    by = {}
    for j in all_meta():
        k = j.get("project_slug") or "untitled"
        e = by.setdefault(k, {"slug": k, "project": j.get("project", "Untitled"),
                              "runs": 0, "latest_run": None, "latest": None})
        e["runs"] += 1
        if e["latest_run"] is None:
            e["latest_run"] = j["run_id"]
            e["latest"] = j.get("created")
    return sorted(by.values(), key=lambda e: e["latest"] or "", reverse=True)


@app.get("/api/runs")
def runs(project: str = ""):
    out = [j for j in all_meta()
           if not project or j.get("project_slug") == project]
    return [{k: j.get(k) for k in
             ("run_id", "project", "project_slug", "filename", "source",
              "created", "stats")} for j in out[:50]]


@app.get("/api/run/{run_id}")
def run_detail(run_id: str):
    f, meta = load_run(run_id)
    return {"meta": meta, "summary": summarise(f)}


@app.get("/api/ppe/{run_id}")
def ppe_log(run_id: str):
    """The PPE issue log for this run, or an empty payload if the file had none.
    Kept separate from /forecast because PPE is a per-person log, not stock."""
    d = RUNS / run_id
    p = d / "ppe.json"
    if not p.exists():
        return {"records": [], "summary": None}
    return json.loads(p.read_text())


@app.delete("/api/run/{run_id}")
def delete_run(run_id: str):
    d = RUNS / run_id
    if not d.exists():
        raise HTTPException(404, "run not found")
    shutil.rmtree(d)
    for f in UPLOADS.glob(f"{run_id}__*"):
        f.unlink(missing_ok=True)
    return {"deleted": run_id}


@app.delete("/api/project/{slug}")
def delete_project(slug: str, confirm: str = ""):
    """Destructive, so the caller must echo the project name back."""
    metas = [j for j in all_meta() if j.get("project_slug") == slug]
    if not metas:
        raise HTTPException(404, "project not found")
    name = metas[0].get("project", "")
    if confirm != "__ui__" and confirm.strip().lower() != name.strip().lower():
        raise HTTPException(
            400, f'type the project name exactly ("{name}") to confirm')
    for j in metas:
        rid = j["run_id"]
        shutil.rmtree(RUNS / rid, ignore_errors=True)
        for f in UPLOADS.glob(f"{rid}__*"):
            f.unlink(missing_ok=True)
    return {"deleted": slug, "runs": len(metas)}


@app.get("/api/forecast/{run_id}")
def forecast_rows(run_id: str, status: str = "", service: str = "",
                  subcategory: str = "", size: str = "", q: str = "", overdue: int = 0,
                  limit: int = 1000):
    f, _ = load_run(run_id)
    if "subcategory" not in f.columns:            # older runs, tag on the fly
        f = subcat.add_subcategory(f)
    if "tool_type" not in f.columns:              # older runs, tag on the fly
        f = toolcat.add_tooltype(f, INVENTORY_SERVICES)
    # "Order date passed" (overdue) is defined by the order-by date being in the
    # past, NOT by a status set - an overdue item may be RED, STOCKED_OUT or even
    # AMBER. It must use the SAME test as summarise() so the KPI card count and
    # this filter's row count are identical.
    if overdue:
        f = f[f.order_by.notna() & (f.order_by < pd.Timestamp.now())]
    if status:
        f = f[f.status.isin(status.split(","))]
    if service:
        f = f[f.service == service]
    if subcategory:
        f = f[f.subcategory == subcategory]
    # Size is a token from linkage.size_tokens() (e.g. "1.5SQMM", "25MM"), the
    # SAME extraction linkage.py already uses for BOQ matching - never a
    # second, looser guess at what counts as a size. Recomputed per request
    # rather than baked into the parquet: cheap regex work over a few hundred
    # materials at most, same order of cost as subcat.add_subcategory below.
    if size:
        f = f[f.material.map(lambda m: size in linkage.size_tokens(m))]
    if q:
        f = f[f.material.str.contains(q.strip().upper(), regex=False)]
    return jsonable(f.head(limit))


@app.get("/api/subcategories/{run_id}")
def subcategories(run_id: str):
    """Which sub-categories exist in this run, grouped by service, with counts.
    Drives the type dropdown: a service shows only the types actually present."""
    f, _ = load_run(run_id)
    if "subcategory" not in f.columns:
        f = subcat.add_subcategory(f)
    # The material type filter is for MEP trades only. Safety/Tools carry their
    # own tool types (see /api/forecast tool_type), and running the material
    # classifier on them produces junk ("Cable tray" for a ladder), so keep them
    # out of this list entirely.
    if "service" in f.columns:
        f = f[~f.service.isin(INVENTORY_SERVICES)]
    by_service = {}
    for svc, g in f.groupby("service"):
        counts = g.subcategory.value_counts()
        by_service[svc] = [{"name": k, "count": int(v)} for k, v in counts.items()]
    allc = f.subcategory.value_counts()
    return {"by_service": by_service,
            "all": [{"name": k, "count": int(v)} for k, v in allc.items()]}


@app.get("/api/sizes/{run_id}")
def sizes(run_id: str, service: str = "", subcategory: str = ""):
    """Which size tokens exist among materials in this scope (service +
    optional type/subcategory), with counts. Drives the Forecast tab's
    cascading Size dropdown, which only appears once a Type is picked AND
    that type actually has extractable sizes among its materials - answered
    by the SAME linkage.size_tokens() extraction linkage.py already uses for
    BOQ matching (see its docstring), not a second guess at what a "size" is.
    Same scoping rules as /api/subcategories: MEP only, Safety/Tools excluded
    (a size token from a helmet's name would be meaningless noise here).
    """
    f, _ = load_run(run_id)
    if "subcategory" not in f.columns:
        f = subcat.add_subcategory(f)
    if "service" in f.columns:
        f = f[~f.service.isin(INVENTORY_SERVICES)]
    if service:
        f = f[f.service == service]
    if subcategory:
        f = f[f.subcategory == subcategory]
    counts = {}
    for name in f.material.dropna():
        for tok in linkage.size_tokens(name):
            counts[tok] = counts.get(tok, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"name": k, "count": v} for k, v in ordered]


@app.get("/api/material/{run_id}")
def material_history(run_id: str, name: str):
    d = RUNS / run_id
    if not (d / "daily.parquet").exists():
        raise HTTPException(404, "run not found")
    daily = pd.read_parquet(d / "daily.parquet")
    g = daily[daily.material == name.strip().upper()].sort_values("date")
    # "opening" is the register's own Opening Stock for this material --
    # constant across every date row (build_daily broadcasts it, see
    # engine.py). Included so "total received" can be opening + dated IN,
    # not dated IN alone: a material whose entire supply arrived as an
    # opening balance (no subsequent purchases) was showing "0 received"
    # despite clearly having stock, because dated-IN-only misses that.
    cols = ["date", "qty_in", "qty_out", "balance"]
    if "opening" in daily.columns:
        cols.append("opening")
    return jsonable(g[cols])


@app.get("/api/leadtime/status")
def leadtime_status():
    """Whether the baked-in real lead-time data is active, and its coverage -
    so the UI can show 'N materials covered from real PO/GRN history' or
    'not available, using the global lead time' rather than staying silent.
    Read-only: there is no upload here, the file ships with the code."""
    agg = _load_leadtime_aggregates()
    if agg is None:
        return {"available": False}
    return {"available": True, "report": agg["report"]}


@app.get("/api/export/{run_id}")
def export_csv(run_id: str):
    d = RUNS / run_id
    if not (d / "forecast.parquet").exists():
        raise HTTPException(404, "run not found")
    out = d / "forecast.csv"
    pd.read_parquet(d / "forecast.parquet").to_csv(out, index=False)
    return FileResponse(out, filename=f"forecast_{run_id}.csv")


# Site Progress (third tab) lives in its own router; forecast routes above are
# untouched. Included BEFORE the static mount so /api/siteprogress/* wins.
from . import siteprogress  # noqa: E402
app.include_router(siteprogress.router)

# Cache-busting, fully automatic: every local <link>/<script> reference in
# index.html gets "?v=<the file's own last-modified time>" stamped on by the
# SERVER on every request - never hand-typed, never forgotten. Replace
# app.js on disk and its mtime changes, so the very next page load gets a
# new ?v= and the browser is forced to fetch the new file instead of
# silently reusing a cached copy.
_ASSET_RE = re.compile(
    r'(href|src)="((?:style|siteprogress)\.css|(?:app|siteprogress)\.js)'
    r'(?:\?v=[^"]*)?"')


def _stamp_assets(html: str) -> str:
    def _sub(m):
        attr, fname = m.group(1), m.group(2)
        try:
            v = int((FRONTEND / fname).stat().st_mtime)
        except OSError:
            v = 0
        return f'{attr}="{fname}?v={v}"'
    return _ASSET_RE.sub(_sub, html)


@app.get("/", response_class=HTMLResponse)
@app.get("/index.html", response_class=HTMLResponse)
def index():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(_stamp_assets(html))


app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
