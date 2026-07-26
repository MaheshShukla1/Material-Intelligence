# Material Intelligence

**Upload a messy site stock register. Get a material shortage forecast you can defend in a meeting — in 30 seconds, with no data cleanup.**

Material Intelligence reads the real Excel stock registers that MEP (Mechanical, Electrical, Plumbing & Fire-fighting) contractors already keep — however inconsistent — and tells you which materials are about to run out, when, and when to order. No format templates. No manual mapping. No data-entry migration.

Built for Indian construction sites where the stock register is a wide Excel sheet with `IN` / `OUT` / `BALANCE` repeating under every date, column names change from project to project, and Excel has silently swapped half the dates.

---

## Why this exists

Every MEP contractor loses money the same way: a critical material runs out mid-activity, someone raises a rush order, the site sits idle, and margin quietly bleeds. The data to see it coming is already sitting in the store keeper's Excel file — it's just unreadable by any existing tool.

Enterprise ERPs (SAP, Oracle) and modern procurement suites all demand the same thing first: *put your data in our format.* That migration is where 90% of contractors give up.

**Material Intelligence inverts that.** You drop in the file you already have. It figures out the format itself.

---

## What it actually does

- **Reads any register, automatically.** Column names are detected by meaning, not position — `MATERIAL DESCRIPTION`, `ITEM NAME`, `PARTICULARS` all resolve to the same thing. Header row is found, not assumed. Works across projects with different layouts out of the box.
- **Repairs corrupted dates.** Excel's day/month swaps are detected and fixed by interpolating against unambiguous anchor dates.
- **Forecasts shortages with a trend-aware consumption rate.** An EWMA-blended rate weights recent usage and excludes idle days, so a material that speeds up as an activity ramps is caught — not lost in a flat 14-day average.
- **Knows the difference between "running out" and "work paused."** A material idle far longer than its own forecast is flagged *No recent use* instead of a false *Order now* — the single biggest source of false alarms in rate-only systems.
- **Sub-categorises every material** (Cable, Pipe, Conduit, Duct, Valve, Saddle, Sprinkler, Data/CCTV…) so a 300-line register filters down to just the cables in one click. Service-scoped: pick Electrical, see only electrical types.
- **Refuses to guess when the data is too thin.** If too few materials have real consumption history, predicted dates are blanked and the run is marked `INSUFFICIENT_DATA`. One confident wrong date costs more trust than ten honest blanks.
- **Says how much to trust each row.** Every forecast carries a confidence level (HIGH / MEDIUM / LOW) based on how many days of real consumption back it, widened further when usage is erratic.
- **Manages multiple projects and uploads.** Switch between sites, keep an audit trail of every upload, delete any single upload or a whole project from one panel.
- **Ships with a logic validator.** `validate.py` runs the engine against a set of invariants a self-contradicting forecast can never satisfy — run it on any new file before you trust it.

---

## Honest accuracy

Measured by back-testing on real project data (cut history at a date, forecast forward, compare to what actually happened):

| Horizon | Recall (caught before they ran out) | Date accuracy (±7 days) |
|---|---|---|
| 10–15 days | ~90–95% | ~95–98% |
| 20 days | ~80% | ~93% |
| 27+ days | ~75% | drops with horizon |

**What this means:** inside a 15-day window the forecast is strong. Beyond that, consumption becomes activity-driven — no rate-only model can see a phase change coming, and this one doesn't pretend to. Higher accuracy over longer horizons needs open-PO data and BOQ, which are on the roadmap.

Nothing here claims 100%. A tool that claims 100% on construction consumption is lying — rain, holidays, and phase changes are not predictable from a rate.

---

## Quick start

```bash
# Windows
start.bat

# Mac / Linux
./start.sh
```

Then open **http://127.0.0.1:8000**

First run installs dependencies (a minute or two). After that it starts in seconds. Requires Python 3.12.

Validate a file's forecast logic at any time:

```bash
python validate.py path/to/register.xlsx
```

---

## What it accepts

| File | Detected as | Notes |
|---|---|---|
| Site stock register (`IN`/`OUT`/`BALANCE` repeated per date) | `register` | Safety / PPE / Tools / item-master sheets skipped automatically |
| ProjectBase *Material Inward and Outward* export | `projectbase` | Negative quantity read as an issue |

Detection is automatic — there is no format picker, because a format picker is one more thing a user can get wrong.

---

## How a forecast is built

1. **Parse & map** — detect the header row and every column by meaning; skip non-register sheets.
2. **Repair dates** — fix Excel day/month swaps against anchor dates.
3. **Current stock** — last recorded balance, or `opening + ΣIN − ΣOUT`.
4. **Consumption rate** — EWMA blend of active-day and calendar rates, with a surge detector for accelerating usage.
5. **Status** — RED / AMBER / GREEN / OVERSTOCK / DEAD_STOCK / NO_RECENT_USE / STOCKED_OUT, decided against the reorder point and guarded against stale rates.
6. **Order-by date** — `runs-out − (lead time + buffer)`.

---

## Design decisions

- **Runs are immutable.** Every upload writes a new folder under `data/runs/`. Nothing is overwritten, so any number shown in a meeting can be reproduced from the exact file it came from.
- **No build step on the frontend.** Plain HTML / CSS / JS — it runs offline on a site laptop with nothing to compile.
- **Honesty over confidence.** The gate, the confidence levels, the paused-detection, and the validator all exist to make the tool admit what it doesn't know.

---

## Architecture

```
material-intel/
  main.py             entry point — binds 0.0.0.0, reads $PORT
  backend/
    schema.py         column detection by meaning (synonyms + fuzzy)
    engine.py         date repair, parsing, forecast maths, status logic
    rate.py           EWMA + active-day + surge consumption rate
    subcat.py         material sub-category detection from names
    health.py         data-quality gate (can block a run)
    api.py            FastAPI: upload, forecast, subcategories, delete, export
  frontend/
    index.html        one page, no framework
    style.css
    app.js            no build step
  data/
    uploads/          raw files, kept for audit
    runs/             one folder per run: forecast + daily + meta
  validate.py         logic-invariant checker
  backtest.py         accuracy back-testing harness
  requirements.txt
  Procfile            deployment (python main.py)
  start.bat / start.sh
```

Stack: **FastAPI · pandas · Parquet · vanilla JS**. No database — one project, immutable run folders, full audit trail.

---

## Roadmap

- **Open-PO awareness** — count in-transit stock so already-ordered material stops showing as a false emergency (biggest precision gain).
- **Real per-material lead times** — derived from PO → GRN history instead of one global number.
- **BOQ-based requirement forecasting** — see past the 15-day rate horizon.
- **Consumption benchmark library** — per-activity material norms built from real project history.

---

## Status

Working MVP, tested on real multi-sheet registers across Electrical, Plumbing, Fire-fighting, HVAC and ELV. Actively developed.

Built for the store keepers and purchase teams who run construction sites on Excel — and deserve better than a rush order.
