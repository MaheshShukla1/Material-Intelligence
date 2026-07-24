"""HTTP layer. Thin on purpose - all real work lives in engine.py / health.py."""
import io
import json
import shutil
import uuid
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import engine, health

ROOT = Path(__file__).resolve().parent.parent
UPLOADS = ROOT / "data" / "uploads"
RUNS = ROOT / "data" / "runs"
FRONTEND = ROOT / "frontend"
for p in (UPLOADS, RUNS):
    p.mkdir(parents=True, exist_ok=True)

SERVICE_HINTS = [("ELECTRIC", "Electrical"), ("PLUM", "Plumbing"),
                 ("PHE", "Plumbing"), ("FIRE", "Fire & HVAC"),
                 ("HVAC", "Fire & HVAC")]
SKIP_HINTS = ["SAFETY", "PPE", "TOOL"]

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
        u = s.upper()
        if any(h in u for h in SKIP_HINTS):
            skipped.append(s)
            continue
        keep[s] = next((v for k, v in SERVICE_HINTS if k in u), "Other")
    return keep, skipped


def jsonable(df):
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[c]):
            out[c] = out[c].dt.strftime("%Y-%m-%d")
    return json.loads(out.replace({np.nan: None}).to_json(orient="records"))


def load_run(run_id):
    d = RUNS / run_id
    if not d.exists():
        raise HTTPException(404, "run not found")
    f = pd.read_parquet(d / "forecast.parquet")
    meta = json.loads((d / "meta.json").read_text())
    return f, meta


# ------------------------------------------------------------------ routes
@app.post("/api/upload")
async def upload(file: UploadFile = File(...),
                 lead_time: int = Form(7),
                 asof: str = Form("")):
    if not file.filename.lower().endswith((".xlsx", ".xlsm", ".xls")):
        raise HTTPException(400, "upload an Excel file")

    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    raw = UPLOADS / f"{run_id}__{file.filename}"
    with raw.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)

    kind = detect(raw)
    try:
        if kind == "projectbase":
            mv, _ = engine.parse_projectbase_movement(raw)
            skipped = []
        else:
            plan, skipped = sheet_plan(raw)
            mv, _ = engine.parse_site_register(raw, plan)
    except Exception as e:
        raise HTTPException(422, f"could not read this file: {e}")

    if mv.empty:
        raise HTTPException(422, "no movement rows found in this file")

    daily = engine.build_daily(mv)
    moved = daily[(daily.qty_out > 0) | (daily.qty_in > 0)]
    default_asof = moved.date.max() if len(moved) else daily.date.max()
    asof_ts = pd.Timestamp(asof) if asof else default_asof

    issues, stats = health.check(mv, daily, asof_ts)
    f = engine.forecast(daily[daily.date <= asof_ts],
                        asof=asof_ts, lead_time=lead_time)
    if not stats["forecast_ready"]:
        f = health.suppress(f)

    d = RUNS / run_id
    d.mkdir(parents=True, exist_ok=True)
    f.to_parquet(d / "forecast.parquet")
    daily.to_parquet(d / "daily.parquet")
    meta = {"run_id": run_id, "filename": file.filename, "source": kind,
            "sheets_skipped": skipped, "lead_time": lead_time,
            "created": dt.datetime.now().isoformat(timespec="seconds"),
            "stats": stats, "issues": issues}
    (d / "meta.json").write_text(json.dumps(meta, indent=2))
    return {"run_id": run_id, "meta": meta, "summary": summarise(f)}


def summarise(f):
    counts = f.status.value_counts().to_dict()
    urgent = f[f.status.isin(["STOCKED_OUT", "RED"])]
    return {
        "counts": {k: int(v) for k, v in counts.items()},
        "materials": int(len(f)),
        "act_today": int(len(urgent)),
        "idle_lines": int(f.status.isin(["OVERSTOCK", "DEAD_STOCK"]).sum()),
        "services": sorted(f.service.dropna().unique().tolist()),
        "overdue_orders": int((f.order_by.notna() &
                               (f.order_by < pd.Timestamp.now())).sum()),
    }


@app.get("/api/runs")
def runs():
    out = []
    for d in sorted(RUNS.iterdir(), reverse=True):
        m = d / "meta.json"
        if m.exists():
            j = json.loads(m.read_text())
            out.append({k: j[k] for k in
                        ("run_id", "filename", "source", "created", "stats")})
    return out[:30]


@app.get("/api/run/{run_id}")
def run_detail(run_id: str):
    f, meta = load_run(run_id)
    return {"meta": meta, "summary": summarise(f)}


@app.get("/api/forecast/{run_id}")
def forecast_rows(run_id: str, status: str = "", service: str = "",
                  q: str = "", limit: int = 500):
    f, _ = load_run(run_id)
    if status:
        f = f[f.status.isin(status.split(","))]
    if service:
        f = f[f.service == service]
    if q:
        f = f[f.material.str.contains(q.upper(), regex=False)]
    return jsonable(f.head(limit))


@app.get("/api/material/{run_id}")
def material_history(run_id: str, name: str):
    d = RUNS / run_id
    daily = pd.read_parquet(d / "daily.parquet")
    g = daily[daily.material == name.upper()].sort_values("date")
    g = g[["date", "qty_in", "qty_out", "balance"]]
    return jsonable(g)


@app.get("/api/export/{run_id}")
def export_csv(run_id: str):
    d = RUNS / run_id
    f = pd.read_parquet(d / "forecast.parquet")
    out = d / "forecast.csv"
    f.to_csv(out, index=False)
    return FileResponse(out, filename=f"forecast_{run_id}.csv")


app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
