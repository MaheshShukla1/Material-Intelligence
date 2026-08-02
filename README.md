# Material Intelligence

**Upload a messy site stock register — or connect a live Google Sheet — and get a material shortage forecast you can defend in a meeting. No data cleanup, no templates, no migration.**

Material Intelligence reads the real Excel stock registers that MEP (Mechanical, Electrical, Plumbing & Fire-fighting) contractors already keep — however inconsistent — and tells you which materials are about to run out, when, and when to order. It runs in the browser, works on the data you already have, and is honest about what it does and doesn't know.

Built for Indian construction sites where the stock register is a wide Excel sheet with `IN` / `OUT` / `BALANCE` repeating under every date, column names change from project to project, and Excel has silently swapped half the dates.

---

## Why this exists

Every MEP contractor loses money the same way: a critical material runs out mid-activity, someone raises a rush order, the site sits idle, and margin quietly bleeds. The data to see it coming is already in the store keeper's Excel file — it's just unreadable by any existing tool.

Enterprise ERPs (SAP, Oracle) and construction suites all demand the same thing first: *put your data in our format.* That migration is where most contractors give up.

**Material Intelligence inverts that.** You drop in the file you already have, or connect the Google Sheet your team already updates. It figures out the format itself.

---

## What it does

- **Reads any register, automatically.** Columns are detected by meaning, not position — `MATERIAL DESCRIPTION`, `ITEM NAME`, `PARTICULARS` all resolve to the same thing. The header row is found, not assumed. Different projects with different layouts work out of the box.
- **Repairs corrupted dates.** Excel's day/month swaps are detected and fixed against unambiguous anchor dates.
- **Forecasts shortages with a trend-aware rate.** An EWMA-blended consumption rate weights recent usage and excludes idle days, so a material that speeds up as an activity ramps is caught — not lost in a flat average.
- **Reads the forecast from today, not from stale data.** If the register's last entry is a few days old, the forecast is still anchored to the real calendar day — so a runs-out date never shows up already in the past while the stock is visibly on the shelf.
- **Knows "running out" from "work paused."** A material that should already be empty but still has stock — or that's been idle far longer than its forecast — is flagged *No recent use* instead of a false *Order now*. This removes the single biggest source of false alarms.
- **Sub-categorises every material** (Cable, Wire, Conduit, Pipe, Duct, Valve, Saddle, Sprinkler, Data/CCTV, and more) so a 300-line register filters to just the cables in one click. Service-scoped: pick Electrical, see only electrical types; pick "All types" to look across every trade.
- **Splits inventory from forecasts.** Safety gear (shoes, helmets, jackets), Tools (ladder, hammer, grinder), and PPE issue logs are shown as plain stock counts, not forecasts. Each is its own tab with tailored filters: Safety and Tools filter by type and size; PPE shows who received what, filtered by item type, shoe size, and contractor.
- **Classifies safety and tools correctly.** A "8 FEET LADDER" is tagged as a Ladder, not confused with cable types — safety and tool names use a dedicated classifier that works across any register and any spelling variant.
- **KPI cards are clickable filters.** Six cards show "Act today", "Already out", "Order date passed", "Order this week", "Stop ordering", and "No action" — click any card to apply that exact filter to the forecast, with live highlighting so you know which card is active.
- **Detects overdue orders.** Separate from status buckets, the "Order date passed" card catches materials whose order-by date was in the past, whether they're red-flagged or not — so overdue items never slip through mixed into another status.
- **Refuses to guess when data is thin.** If too few materials have real consumption history, predicted dates are blanked and the run is marked `INSUFFICIENT_DATA`. One confident wrong date costs more trust than ten honest blanks.
- **Says how much to trust each row.** Every forecast carries a confidence level (HIGH / MEDIUM / LOW), widened when usage is erratic.
- **Connects to a live Google Sheet.** Publish a sheet once, paste the link, and the dashboard pulls the latest itself — and auto-refreshes every few minutes while open. A "synced X ago" line shows how fresh the data is, with a warning when it's stale.
- **Manages multiple projects and uploads.** Each uploaded file or connected sheet is its own project. Switch between sites, keep an audit trail of every upload, and delete any single upload or a whole project from one panel.
- **Ships with a logic validator.** `validate.py` runs the engine against a set of invariants a self-contradicting forecast can never satisfy — run it on any new file before you trust it.

---

## Honest accuracy

Measured by back-testing on real project data — cut the history at a date, forecast forward, compare to what actually happened:

| Horizon | Shortages caught in time | Date accuracy (±7 days) |
|---|---|---|
| 10–15 days | ~90–95% | ~95–98% |
| 20 days | ~80% | ~93% |
| 27+ days | ~75% | drops with horizon |

Inside a 15-day window the forecast is strong. Beyond that, consumption becomes activity-driven — no rate-only model can see a phase change coming, and this one doesn't pretend to. Longer horizons need open-PO and BOQ data, which are on the roadmap.

**Nothing here claims 100%.** A tool that claims 100% on construction consumption is lying — rain, holidays and phase changes aren't predictable from a rate. The engine is built to admit what it doesn't know.

---

## Quick start

```bash
# Windows
start.bat

# Mac / Linux
./start.sh
```

Then open **http://127.0.0.1:8000**

First run installs dependencies (a minute or two); after that it starts in seconds. Requires Python 3.12.

Validate any file's forecast logic at any time:

```bash
python validate.py path/to/register.xlsx
```

---

## Two ways to load data

**1. Upload a file** — drag in an `.xlsx`/`.xlsm`/`.xls`. Optionally name the project.

**2. Connect a live Google Sheet** — in Google Sheets: `File → Share → Publish to web → whole document → Microsoft Excel (.xlsx)`. Paste that link, give the sheet a name, and hit *Sync now*. Re-sync anytime, or leave the dashboard open and it refreshes on its own.

| Source | Detected as | Notes |
|---|---|---|
| Site stock register (`IN`/`OUT`/`BALANCE` per date) | `register` | Safety / PPE / Tools / item-master sheets skipped automatically |
| ProjectBase *Material Inward and Outward* export | `projectbase` | Negative quantity read as an issue |

Detection is automatic — there is no format picker, because a format picker is one more thing a user can get wrong.

---

## How a forecast is built

1. **Parse & map** — detect the header row and every column by meaning; skip non-register sheets.
2. **Repair dates** — fix Excel day/month swaps against anchor dates.
3. **Current stock** — last recorded balance, or `opening + ΣIN − ΣOUT`.
4. **Consumption rate** — EWMA blend of active-day and calendar rates, with a surge detector for accelerating usage.
5. **Status** — decided against the reorder point (lead time + buffer), anchored to today, and guarded against stale rates and paused activity.
6. **Order-by date** — `runs-out − (lead time + buffer)`.

---

## Status meanings

| Status | Meaning |
|---|---|
| `INVENTORY` | Safety / Tools / PPE — plain stock, no forecast (count-only) |
| `STOCKED_OUT` | Zero on hand and still being consumed (checked first) |
| `RED` | Runs out within lead time + buffer — order now |
| `AMBER` | Runs out within twice the lead time — order this week |
| `GREEN` | Healthy cover |
| `OVERSTOCK` | More than 180 days of cover — capital locked up |
| `DEAD_STOCK` | Received, never issued |
| `NO_RECENT_USE` | Consumed before, but paused — not a real shortage |
| `INSUFFICIENT_DATA` | Reliability gate tripped, no forecast shown |

**Note:** The "Order date passed" KPI card is separate — it catches materials whose order-by date is in the past, across any status (RED, AMBER, or STOCKED_OUT). Use it to find overdue orders that may have mixed into the active forecast.

## Confidence

| Level | Basis | Range shown |
|---|---|---|
| HIGH | 8+ distinct consumption days | ±15% |
| MEDIUM | 4–7 days | ±35% |
| LOW | 1–3 days | ±60% |
| NONE | no consumption | no date shown |

## The reliability gate

If fewer than 10 materials have 8+ distinct days of recorded consumption, all exhaustion dates are suppressed and every item is marked `INSUFFICIENT_DATA`. The engine refuses to guess rather than print a number that will be wrong.

---

## Design decisions

- **Runs are immutable.** Every upload or sync writes a new folder under `data/runs/`. Nothing is overwritten, so any number shown in a meeting can be reproduced from the exact file it came from.
- **No build step on the frontend.** Plain HTML / CSS / JS — runs offline on a site laptop, opens on mobile, nothing to compile.
- **Honesty over confidence.** The gate, the confidence levels, the paused-detection, the today-anchoring, and the validator all exist to make the tool admit what it doesn't know.

---

## Architecture

```
material-intel/
  main.py             entry point — binds 0.0.0.0, reads $PORT
  Procfile            deployment (python main.py)
  backend/
    schema.py         column detection by meaning (synonyms + fuzzy)
    engine.py         date repair, parsing, forecast maths, status, today-anchoring
    rate.py           EWMA + active-day + surge consumption rate
    subcat.py         material sub-category detection (MEP trades)
    toolcat.py        safety and tool type detection (ladder, helmet, grinder, etc.)
    health.py         data-quality gate (can block a run)
    api.py            FastAPI: upload, sync-sheet, forecast, subcategories, delete, export
  frontend/
    index.html        one page, no framework
    style.css
    app.js            no build step; live sync, auto-refresh, project switching
  data/
    uploads/          raw files, kept for audit
    runs/             one folder per run: forecast + daily + meta
  validate.py         logic-invariant checker
  backtest.py         accuracy back-testing harness
  requirements.txt
  start.bat / start.sh
```

Stack: **FastAPI · pandas · Parquet · vanilla JS**. No database — immutable run folders, full audit trail.

---

## Back-test before trusting it

```bash
python backtest.py <file.xlsx> --end 2026-07-22 \
    --cutoffs 2026-06-30,2026-07-07,2026-07-12
```

- **recall** — of materials that really ran out, how many were warned
- **precision** — of alerts raised, how many were real
- **error** — how many days off the predicted date was

---

## Roadmap

- **Open-PO awareness** — count in-transit stock so already-ordered material stops showing as a false emergency (biggest precision gain).
- **Rupee value** — connect ProjectBase purchase data to show the ₹ value of dead stock and price variance across vendors.
- **Real per-material lead times** — derived from PO → GRN history instead of one global number.
- **BOQ-based requirement forecasting** — see past the 15-day rate horizon.
- **Consumption benchmark library** — per-activity material norms built from real project history.

---

## Status

Working web app, deployed and tested on real multi-sheet registers across Electrical, Plumbing, Fire-fighting, HVAC and ELV. Actively developed.

Built for the store keepers and purchase teams who run construction sites on Excel — and deserve better than a rush order.
