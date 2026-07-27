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
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import engine, health, schema, subcat

ROOT = Path(__file__).resolve().parent.parent
UPLOADS = ROOT / "data" / "uploads"
RUNS = ROOT / "data" / "runs"
FRONTEND = ROOT / "frontend"
for p in (UPLOADS, RUNS):
    p.mkdir(parents=True, exist_ok=True)

# Tabs that are never material movement: consumables logs and item masters.
SKIP_SHEETS = ["SAFETY", "PPE", "TOOL", "ITEMMASTER", "MASTER",
               "SUMMARY", "INDEX", "SHEET1"]

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
    f = engine.forecast(daily[daily.date <= asof_ts],
                        asof=asof_ts, lead_time=lead_time,
                        today=pd.Timestamp.now().normalize())
    f = subcat.add_subcategory(f)
    if not stats["forecast_ready"]:
        f = health.suppress(f)

    d = RUNS / run_id
    d.mkdir(parents=True, exist_ok=True)
    f.to_parquet(d / "forecast.parquet")
    daily.to_parquet(d / "daily.parquet")
    meta = {"run_id": run_id, "project": project, "project_slug": safe_slug(project),
            "filename": filename, "source": kind,
            "lead_time": lead_time, "mapping": rep,
            "created": dt.datetime.now().isoformat(timespec="seconds"),
            "stats": stats, "issues": issues}
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
                  subcategory: str = "", q: str = "", limit: int = 1000):
    f, _ = load_run(run_id)
    if "subcategory" not in f.columns:            # older runs, tag on the fly
        f = subcat.add_subcategory(f)
    if status:
        f = f[f.status.isin(status.split(","))]
    if service:
        f = f[f.service == service]
    if subcategory:
        f = f[f.subcategory == subcategory]
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
    by_service = {}
    for svc, g in f.groupby("service"):
        counts = g.subcategory.value_counts()
        by_service[svc] = [{"name": k, "count": int(v)} for k, v in counts.items()]
    allc = f.subcategory.value_counts()
    return {"by_service": by_service,
            "all": [{"name": k, "count": int(v)} for k, v in allc.items()]}


@app.get("/api/material/{run_id}")
def material_history(run_id: str, name: str):
    d = RUNS / run_id
    if not (d / "daily.parquet").exists():
        raise HTTPException(404, "run not found")
    daily = pd.read_parquet(d / "daily.parquet")
    g = daily[daily.material == name.strip().upper()].sort_values("date")
    return jsonable(g[["date", "qty_in", "qty_out", "balance"]])


@app.get("/api/export/{run_id}")
def export_csv(run_id: str):
    d = RUNS / run_id
    if not (d / "forecast.parquet").exists():
        raise HTTPException(404, "run not found")
    out = d / "forecast.csv"
    pd.read_parquet(d / "forecast.parquet").to_csv(out, index=False)
    return FileResponse(out, filename=f"forecast_{run_id}.csv")


app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
