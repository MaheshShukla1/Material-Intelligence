# Material Intelligence

**Upload a messy site stock register — or connect a live Google Sheet — and get a material shortage forecast you can defend in a meeting. Then track real site progress against the BOQ and see whether the stock on hand is actually enough to finish the work. No data cleanup, no templates, no migration.**

Material Intelligence reads the real Excel stock registers that MEP (Mechanical, Electrical, Plumbing & Fire-fighting) contractors already keep — however inconsistent — and tells you which materials are about to run out, when, and when to order. Alongside that, it tracks real on-site progress room by room against the BOQ and combines it with the stock register to answer the question a plain consumption forecast can't: *is what's on the shelf enough to finish the planned work, or will it run short before the job is done?* It runs in the browser, works on the data you already have, and is honest about what it does and doesn't know.

Built for Indian construction sites where the stock register is a wide Excel sheet with `IN` / `OUT` / `BALANCE` repeating under every date, column names change from project to project, and Excel has silently swapped half the dates.

Three tabs, one dataset: **Forecast** (consumption-rate shortage forecast), **Site Progress** (BOQ + room-by-room progress + a realistic stock-vs-work forecast), **Inventory** (Safety / Tools / PPE, tracked as plain stock, not forecast).

---

## Why this exists

Every MEP contractor loses money the same way: a critical material runs out mid-activity, someone raises a rush order, the site sits idle, and margin quietly bleeds. The data to see it coming is already in the store keeper's Excel file and the site engineer's progress tracker — it's just unreadable by any existing tool, and the two never talk to each other.

Enterprise ERPs (SAP, Oracle) and construction suites all demand the same thing first: *put your data in our format.* That migration is where most contractors give up.

**Material Intelligence inverts that.** You drop in the file you already have, or connect the Google Sheet your team already updates. It figures out the format itself. And once the BOQ and the progress tracker are in too, it stops asking "how fast is this material being used" and starts answering the question that actually matters on site: "how much of this material do we still need, and does the stock on hand cover it?"

---

## What it does

### Forecast — consumption-rate shortage forecasting
- **Reads any register, automatically.** Columns are detected by meaning, not position — `MATERIAL DESCRIPTION`, `ITEM NAME`, `PARTICULARS` all resolve to the same thing. The header row is found, not assumed. Different projects with different layouts work out of the box.
- **Repairs corrupted dates.** Excel's day/month swaps are detected and fixed against unambiguous anchor dates.
- **Forecasts shortages with a trend-aware rate.** An EWMA-blended consumption rate weights recent usage and excludes idle days, so a material that speeds up as an activity ramps is caught — not lost in a flat average.
- **Reads the forecast from today, not from stale data.** If the register's last entry is a few days old, the forecast is still anchored to the real calendar day — so a runs-out date never shows up already in the past while the stock is visibly on the shelf.
- **Knows "running out" from "work paused."** A material that should already be empty but still has stock — or that's been idle far longer than its forecast — is flagged *No recent use* instead of a false *Order now*. This removes the single biggest source of false alarms.
- **Sub-categorises every material** (Cable, Wire, Conduit, Pipe, Duct, Valve, Saddle, Sprinkler, Data/CCTV, and more) so a 300-line register filters to just the cables in one click. Service-scoped: pick Electrical, see only electrical types; pick "All types" to look across every trade.
- **KPI cards are clickable filters.** Six cards show "Act today", "Already out", "Order date passed", "Order this week", "Stop ordering", and "No action" — click any card to apply that exact filter to the forecast, with live highlighting so you know which card is active.
- **Detects overdue orders.** Separate from status buckets, the "Order date passed" card catches materials whose order-by date was in the past, whether they're red-flagged or not — so overdue items never slip through mixed into another status.
- **Refuses to guess when data is thin.** If too few materials have real consumption history, predicted dates are blanked and the run is marked `INSUFFICIENT_DATA`. One confident wrong date costs more trust than ten honest blanks.
- **Says how much to trust each row.** Every forecast carries a confidence level (HIGH / MEDIUM / LOW), widened when usage is erratic.
- **Uses real per-material lead times where they exist.** When a material has enough real PO → GRN history, its own actual lead time drives the reorder point instead of the one global default — shown inline so it's obvious which numbers are real and which are the default.

### Site Progress — is the stock actually enough to finish the work?
- **Upload a BOQ and it's parsed automatically**, including a dedicated detector for real construction-management-software exports (ProjectBase-style, with Rate + Design Quantity columns) that a generic synonym-matching parser would silently corrupt. "RO" (Rate Only, no fixed quantity) line items are handled honestly instead of guessed.
- **Editable project structure** — Project → Floor → Room for a hotel, Level → Zone for a mall, Wing → Floor → Room for a hospital, or a fully custom tree — either picked from a template or rebuilt automatically from an uploaded progress tracker, so onboarding is zero-setup.
- **Activity mapping with a first-guess suggestion.** The tracker speaks in activities ("Wall Piping", "Wire Pulling"); the BOQ speaks in items. A keyword-based suggestion pre-ticks the likely BOQ items for each activity so the engineer starts from a mostly-filled grid, confirms or edits, and that confirmed mapping drives every number afterwards.
- **Per-room quantity groups**, not just one flat quantity per room. Real sites don't need the same amount of every material in every room — corner rooms need more conduit, mall zones vary in area. An item can carry several quantity groups (different room sets, different quantities) that sum to its true project-wide total, instead of forcing one number across every room.
- **A three-way "realistic" forecast**, the seam between Site Progress and the stock register: remaining work (from BOQ × real progress), stock on hand, and the register's own consumption rate — combined into one verdict: *ENOUGH*, *SHORTAGE* (with how much to order and by when), or an honest *stock has no rate/on-hand data yet*. Never a guess.
- **Matches BOQ items to stock materials automatically**, on size tokens, type words and grade/certification qualifiers (fire-rated vs plain, PVC vs GI) — a plain cable is never confidently linked to a fire-rated one just because the type and size match.
- **Never assumes a 1:1 unit conversion.** A BOQ item's unit and its linked stock material's unit are compared directly; when they genuinely differ (e.g. "Nos" of light points linked to "Rmt" of pipe), a real per-link conversion factor is required before a shortage number is shown — the item is flagged *needs a conversion factor* instead of silently guessing.
- **₹ value and waste tracking.** Work done / remaining converts to rupees wherever an install rate is entered (never invented for unrated items); real stock consumption vs. recorded progress surfaces over-consumption (waste) and under-consumption (savings), with a caveat when too little progress has been logged yet for the number to be trustworthy.
- **Room-level drill-down.** Every BOQ item's drawer shows done / in-progress / not-started rooms for that item specifically, and the realistic forecast is worded against those outstanding rooms, not a bare quantity.

### Inventory — Safety, Tools and PPE
- **Splits inventory from forecasts.** Safety gear (shoes, helmets, jackets), Tools (ladder, hammer, grinder), and PPE issue logs are shown as plain stock counts, not forecasts. Each is its own tab with tailored filters: Safety and Tools filter by type and size; PPE shows who received what, filtered by item type, shoe size, and contractor.
- **Classifies safety and tools correctly.** An "8 FEET LADDER" is tagged as a Ladder, not confused with cable types — safety and tool names use a dedicated classifier that works across any register and any spelling variant.
- **PPE issue-count summary** — a one-line headline (e.g. "42 Jackets issued") above the table, matching whatever filter is currently applied.

### Managing projects and uploads
- **One clean Home screen** to bring in data — upload a file or connect a live Google Sheet from a single segmented toggle — plus a **Recent projects** list so reopening a project already in use is one click, no re-uploading.
- **Manage uploads** from one panel: every project's upload history, with the most recent kept visible and older uploads tucked behind a "show older" toggle so a project with a long history doesn't turn into a wall of files. Deleting a whole project sits in its own clearly separated danger zone — a single upload or a whole project can be removed, both with a confirm step, both irreversible and explicitly labelled as such.
- **Connects to a live Google Sheet.** Publish a sheet once, paste the link, and the dashboard pulls the latest itself — and auto-refreshes every few minutes while open. A "synced X ago" line shows how fresh the data is, with a warning when it's stale.
- **Runs are immutable and auditable.** Every uploaded file or sync writes a new run; nothing is overwritten, so any number shown in a meeting can be reproduced from the exact file it came from.
- **Ships with a logic validator.** `validate.py` runs the engine against a set of invariants a self-contradicting forecast can never satisfy — run it on any new file before you trust it.

---

## Honest accuracy

Measured by back-testing on real project data — cut the history at a date, forecast forward, compare to what actually happened:

| Horizon | Shortages caught in time | Date accuracy (±7 days) |
|---|---|---|
| 10–15 days | ~90–95% | ~95–98% |
| 20 days | ~80% | ~93% |
| 27+ days | ~75% | drops with horizon |

Inside a 15-day window the forecast is strong. Beyond that, consumption becomes activity-driven — no rate-only model can see a phase change coming, and this one doesn't pretend to. That's exactly the gap Site Progress's BOQ-based realistic forecast is built to close: once real remaining work is known from the BOQ and progress tracker, "will the stock finish the job" no longer depends on a rate holding steady for weeks.

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

## Two ways to load a stock register

**1. Upload a file** — drag in an `.xlsx`/`.xlsm`/`.xls` on the Home screen. Optionally name the project.

**2. Connect a live Google Sheet** — in Google Sheets: `File → Share → Publish to web → whole document → Microsoft Excel (.xlsx)`. Paste that link, give the sheet a name, and hit *Sync now*. Re-sync anytime, or leave the dashboard open and it refreshes on its own.

| Source | Detected as | Notes |
|---|---|---|
| Site stock register (`IN`/`OUT`/`BALANCE` per date) | `register` | Safety / Tools / PPE sheets feed the Inventory tab; item master sheets are skipped automatically |
| ProjectBase *Material Inward and Outward* export | `projectbase` | Negative quantity read as an issue |

Detection is automatic — there is no format picker, because a format picker is one more thing a user can get wrong.

The BOQ (for Site Progress) and the progress tracker are uploaded separately, from inside the Site Progress tab once a project exists.

---

## How a Forecast run is built

1. **Parse & map** — detect the header row and every column by meaning; skip non-register sheets.
2. **Repair dates** — fix Excel day/month swaps against anchor dates.
3. **Current stock** — last recorded balance, or `opening + ΣIN − ΣOUT`.
4. **Consumption rate** — EWMA blend of active-day and calendar rates, with a surge detector for accelerating usage.
5. **Status** — decided against the reorder point (lead time + buffer), anchored to today, and guarded against stale rates and paused activity.
6. **Order-by date** — `runs-out − (lead time + buffer)`.

## How a Site Progress realistic forecast is built

1. **Progress → used quantity.** Room-by-room tick data (done / partial / pending) rolls up, per BOQ item, into `used = quantity × Σ(fraction complete across every applicable room)` — quantity-group-aware, so items with different quantities in different rooms sum correctly.
2. **Remaining work.** `remaining = planned total − used`, where the planned total already accounts for every quantity group and every room's real applicability.
3. **Stock signal.** The linked stock material's on-hand quantity and consumption rate, read straight from the same Forecast run — nothing recomputed, nothing duplicated.
4. **Unit reconciliation.** The BOQ item's unit and the stock material's unit are compared; a mismatch with no conversion factor stops the comparison rather than guessing.
5. **Verdict.** `ENOUGH` if on-hand covers the remaining need; `SHORTAGE` (with an order quantity) if it doesn't, or if the stock will run out before the work finishes at the current rate; `UNKNOWN` when linked but the register has no rate/on-hand data yet; `NOT_LINKED` until an engineer links the item to a stock material.

---

## Status meanings (Forecast tab)

| Status | Meaning |
|---|---|
| `STOCKED_OUT` | Zero on hand and still being consumed (checked first) |
| `RED` | Runs out within lead time + buffer — order now |
| `AMBER` | Runs out within twice the lead time — order this week |
| `GREEN` | Healthy cover |
| `OVERSTOCK` | More than 180 days of cover — capital locked up |
| `DEAD_STOCK` | Received, never issued |
| `NO_RECENT_USE` | Consumed before, but paused — not a real shortage |
| `INSUFFICIENT_DATA` | Reliability gate tripped, no forecast shown |

**Note:** The "Order date passed" KPI card is separate — it catches materials whose order-by date is in the past, across any status (RED, AMBER, or STOCKED_OUT). Use it to find overdue orders that may have mixed into the active forecast.

## Verdict meanings (Site Progress realistic forecast)

| Verdict | Meaning |
|---|---|
| `ENOUGH` | Stock on hand covers the remaining planned work |
| `SHORTAGE` | Stock will fall short — an order quantity and/or a run-out-before-finish date is shown |
| `UNKNOWN_FACTOR` | Linked, but the BOQ item's unit and the stock material's unit don't match and no conversion factor has been entered yet |
| `UNKNOWN` | Linked, but the register has no rate/on-hand figures for this material yet |
| `NOT_LINKED` | This BOQ item hasn't been linked to a stock material yet |

## Confidence (Forecast tab)

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
- **Never invent a number.** An unset install rate stays unrated rather than defaulting to zero or one; a unit mismatch stays unresolved rather than assuming a 1:1 factor; a room not covered by any quantity group falls back to the item's own per-room quantity rather than dropping out silently. Every "never guessed" in this document is a real, deliberate branch in the code, not a decorative claim.
- **Honesty over confidence.** The gate, the confidence levels, the paused-detection, the today-anchoring, and the validator all exist to make the tool admit what it doesn't know.

---

## Architecture

```
material-intel/
  main.py             entry point — binds 0.0.0.0, reads $PORT
  Procfile             deployment (python main.py)
  backend/
    schema.py         column detection by meaning (synonyms + fuzzy)
    engine.py         date repair, parsing, forecast maths, status, today-anchoring
    rate.py           EWMA + active-day + surge consumption rate
    subcat.py         material sub-category detection (MEP trades)
    toolcat.py        safety and tool type detection (ladder, helmet, grinder, etc.)
    health.py         data-quality gate (can block a run)
    api.py            FastAPI: upload, sync-sheet, forecast, subcategories, delete, export
    boq.py            BOQ parsing, incl. a ProjectBase-export-aware detector
    structure.py      editable floor / room / zone project tree + templates
    activity.py       tracker activities + activity → BOQ item mapping (+ suggestion)
    progress.py       room-by-room tick grid → % complete and used-quantity per item
    linkage.py        BOQ item ↔ stock material matching (size / type / qualifier tokens)
    realtime.py       the three-signal realistic forecast (progress + BOQ + stock)
    pnl.py            ₹ value roll-ups and waste / saving tracking
    itemprog.py        per-item / per-room progress rollups (rooms panel, room buckets)
    siteprogress.py    Site Progress API routes tying the above together
  frontend/
    index.html         one page, no framework — Forecast / Site Progress / Inventory
    style.css           shared design tokens and the Forecast/Inventory/Home UI
    app.js              no build step; upload, live sync, auto-refresh, project switching
    siteprogress.js      Site Progress tab — additive, self-contained, its own API namespace
    siteprogress.css     Site Progress-specific styles (sp- prefixed, never touches the rest)
  data/
    uploads/           raw files, kept for audit
    runs/               one folder per run: forecast + daily + meta
    projects/<slug>/    structure.json, mapping.json, item_progress.json,
                        item_room_qty.json, links.json, rates.json, planned.json
  validate.py           logic-invariant checker
  backtest.py           accuracy back-testing harness
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
- **Vendor price variance** — connect ProjectBase purchase data to compare unit rates paid across vendors for the same material, on top of the ₹ value tracking Site Progress already has.
- **Consumption benchmark library** — per-activity material norms built from real project history.
- **Open-PO-aware Site Progress shortages** — fold in-transit stock into the realistic forecast the same way it will land in the plain Forecast tab.

---

## Status

Working web app, deployed and tested on real multi-sheet registers and real BOQs across Electrical, Plumbing, Fire-fighting, HVAC and ELV, on real projects (Hyatt Hotel, Thoth Mall). Actively developed.

Built for the store keepers, site engineers and purchase teams who run construction sites on Excel — and deserve better than a rush order.
