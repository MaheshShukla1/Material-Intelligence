# Material Intelligence

**Upload a messy site stock register — or connect a live Google Sheet — and get a material shortage forecast you can defend in a meeting. Then track real site progress against the BOQ, see whether the stock on hand is actually enough to finish the work, export the daily progress report, and build the RA bill from the same data. No data cleanup, no templates, no migration.**

<p align="center">
  <img src="docs/screenshots/02-forecast.png" alt="Forecast tab: KPI cards, material table with order-by dates and trust level" width="900">
</p>

Material Intelligence reads the real Excel stock registers that MEP (Mechanical, Electrical, Plumbing & Fire-fighting) contractors already keep — however inconsistent — and tells you which materials are about to run out, when, and when to order. Alongside that, it tracks real on-site progress room by room against the BOQ and combines it with the stock register to answer the question a plain consumption forecast can't: *is what's on the shelf enough to finish the planned work, or will it run short before the job is done?* The same progress data then feeds the daily progress report (DPR) and the running-account (RA) bills. It runs in the browser, works on the data you already have, and is honest about what it does and doesn't know.

Built for Indian construction sites where the stock register is a wide Excel sheet with `IN` / `OUT` / `BALANCE` repeating under every date, column names change from project to project, and Excel has silently swapped half the dates.

Four tabs, one dataset: **Forecast** (consumption-rate shortage forecast), **Site Progress** (BOQ + room-by-room progress + a realistic stock-vs-work forecast + DPR export), **Inventory** (Safety / Tools / PPE, tracked as plain stock, not forecast) and **Billing** (RA bills from challans and site progress).

> **About the screenshots.** Every image in this README is taken from the real app running on a fully fictional demo project, *Skyline Residency (Demo)* — a synthetic stock register and BOQ, placeholder company/client/GST details, and made-up worker names. No client data is shown.

---

## Contents

- [How it fits together](#how-it-fits-together)
- [Why this exists](#why-this-exists)
- [Product tour](#product-tour)
- [What it does](#what-it-does)
- [Honest accuracy](#honest-accuracy)
- [Quick start](#quick-start)
- [Two ways to load a stock register](#two-ways-to-load-a-stock-register)
- [How a Forecast run is built](#how-a-forecast-run-is-built)
- [How a Site Progress realistic forecast is built](#how-a-site-progress-realistic-forecast-is-built)
- [How an RA bill is built](#how-an-ra-bill-is-built)
- [Status, verdict and confidence tables](#status-meanings-forecast-tab)
- [Design decisions](#design-decisions)
- [Architecture](#architecture)
- [Tests](#tests)
- [Roadmap](#roadmap)

---

## How it fits together

The engineer writes progress once. Everything else reads it.

```mermaid
flowchart LR
    REG["Stock register<br/>Excel / Google Sheet"] --> FC["Forecast<br/>consumption-rate shortages"]
    BOQ["BOQ<br/>ProjectBase-style or raw"] --> SP["Site Progress<br/>room ticks / quantities"]
    ENG["Engineer's daily update"] --> SP
    SP --> RT["Realistic forecast<br/>work left vs stock on hand"]
    FC --> RT
    RT --> SH["Shortage history<br/>was a flagged shortage prevented?"]
    SP --> DPR["DPR export<br/>client copy / internal summary"]
    SP --> INS["Installation quantity"]
    CH["Delivery challans<br/>or stock register"] --> SUP["Supply quantity"]
    INS --> RA["RA bills<br/>Supply / Installation / Together"]
    SUP --> RA
```

---

## Why this exists

Every MEP contractor loses money the same way: a critical material runs out mid-activity, someone raises a rush order, the site sits idle, and margin quietly bleeds. The data to see it coming is already in the store keeper's Excel file and the site engineer's progress tracker — it's just unreadable by any existing tool, and the two never talk to each other.

Enterprise ERPs (SAP, Oracle) and construction suites all demand the same thing first: *put your data in our format.* That migration is where most contractors give up.

**Material Intelligence inverts that.** You drop in the file you already have, or connect the Google Sheet your team already updates. It figures out the format itself. And once the BOQ and the progress tracker are in too, it stops asking "how fast is this material being used" and starts answering the question that actually matters on site: "how much of this material do we still need, and does the stock on hand cover it?"

---

## Product tour

### Bring in a register

One Home screen: upload an Excel file or connect a published Google Sheet, with a **Recent projects** list so reopening a project is one click.

<p align="center"><img src="docs/screenshots/01-home.png" alt="Home screen: upload a stock register or connect a Google Sheet, with recent projects" width="800"></p>

### Forecast — what to order, and when

Six clickable KPI cards filter the table. Every row says what to do, how much is on hand, the consumption rate, when it runs out (as a range, not a single false-precise date) and how far to trust it. Click a row for the material's balance history and every IN / OUT movement behind the number.

<p align="center"><img src="docs/screenshots/03-forecast-material-history.png" alt="Material drawer: balance over time and the IN/OUT/BALANCE movements behind a forecast" width="900"></p>

### Site Progress — is the stock enough to finish the work?

**Overall** rolls every service up into value complete, work done and remaining in ₹, and how many stock shortages were flagged this month.

<p align="center"><img src="docs/screenshots/04-site-progress-overall.png" alt="Site Progress overall view: value complete, work done and remaining, per-service roll-up" width="900"></p>

Per service, BOQ items are grouped into the activities the engineer actually reports (Wire Pulling, Wall Conduiting, …) with per-item progress, ₹ done, the linked stock, and the project structure on the left.

<p align="center"><img src="docs/screenshots/05-site-progress-activities.png" alt="Site Progress activities view with per-item progress and the floor/room tree" width="900"></p>

Open any item to see the three-way verdict in plain words: planned vs used vs remaining, how many rooms are done / in progress / not started, the linked stock on hand and its rate, and what to order.

<p align="center"><img src="docs/screenshots/06-site-progress-item-forecast.png" alt="Item drawer: remaining work, rooms done, linked stock and an order quantity" width="900"></p>

<table>
<tr>
<td width="50%"><img src="docs/screenshots/07-site-progress-areas.png" alt="Areas dialog: tick rooms done, or type a partial quantity per room"></td>
<td width="50%"><img src="docs/screenshots/08-site-progress-link-stock.png" alt="Link stock dialog: connect a BOQ item to one or more register materials"></td>
</tr>
<tr>
<td><b>Areas.</b> Tick the rooms you are updating and mark them done, or type a real partial quantity for a room. Rooms already done show a ✓.</td>
<td><b>Link stock.</b> Connect a BOQ item to the register material(s) it consumes, with a per-link conversion factor when the units differ.</td>
</tr>
</table>

### DPR — the daily report, captured as a side effect

Every progress update is logged as it happens, so the DPR needs no extra typing. Export one day or a range, for one service or the whole project, for the whole building or just chosen rooms. Pick the client copy (the DPR sheet only) or add an internal summary with or without ₹ amounts.

<p align="center"><img src="docs/screenshots/09-dpr-export.png" alt="Export daily update dialog: date range, service, summary sheets and area picker" width="800"></p>

<p align="center"><img src="docs/screenshots/16-export-dpr-summary.png" alt="Exported DPR summary sheet: value complete, done, remaining, per-service table" width="700"></p>

### Inventory — Safety, Tools and PPE

Counted, not forecast. Safety and Tools filter by type and size; PPE is the per-person issue log.

<table>
<tr>
<td width="50%"><img src="docs/screenshots/10-inventory-safety.png" alt="Inventory: safety stock counts with type and size"></td>
<td width="50%"><img src="docs/screenshots/11-inventory-ppe.png" alt="Inventory: PPE issue log by person, contractor and date"></td>
</tr>
</table>

### Billing — RA bills that build themselves

Installation quantity comes from Site Progress; supply quantity from delivery challans (or the stock register); rates from your BOQ sheet. Each service has its own payment term, and a service with no term set is **not billed at all** — no percentage is ever guessed.

<p align="center"><img src="docs/screenshots/12-billing-home.png" alt="Billing home: contract value, invoiced so far, not invoiced yet, per-service drafts and RA list" width="900"></p>

A draft RA shows the annexure groups and every line: BOQ quantity, rate, quantity billed before, up to date, this bill and the amount. The draft is live — it follows Site Progress and challans until you lock it.

<p align="center"><img src="docs/screenshots/13-billing-draft-ra.png" alt="Draft Supply RA 2: annexure summary and line-by-line quantities and amounts" width="900"></p>

<table>
<tr>
<td width="50%"><img src="docs/screenshots/14-billing-challans.png" alt="Challans screen: RA-wise challans, item history search and a stock-register cross-check"></td>
<td width="50%"><img src="docs/screenshots/15-billing-setup.png" alt="Billing setup: bill type, supply source and payment terms per service"></td>
</tr>
<tr>
<td><b>Challans.</b> Each challan is tagged to the RA it is billed in, and challans in invoiced RAs are frozen. Search any BOQ line to see every RA it went into and the challans behind it.</td>
<td><b>Setup.</b> Per service: usual bill type, where supply quantity comes from, Supply % and Installation %. An unset % shows as “—”, never a default.</td>
</tr>
</table>

What comes out is the invoice workbook: Tax Invoice cover, Annexure, challan abstract and challan sheets.

<table>
<tr>
<td width="55%"><img src="docs/screenshots/17-export-ra-annexure.png" alt="Exported RA annexure sheet with previous, this and total bill value"></td>
<td width="45%"><img src="docs/screenshots/18-export-tax-invoice.png" alt="Exported Tax Invoice cover sheet marked DRAFT"></td>
</tr>
</table>

### On the phone

The same screens work at phone width — Forecast, Site Progress and Billing shown here.

<p align="center"><img src="docs/screenshots/19-mobile.png" alt="Forecast, Site Progress and Billing at phone width" width="900"></p>

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
- **Rooms or overall — the engineer's choice.** Progress can be ticked room by room, entered as a real partial quantity for a room, or recorded as one overall figure for an item. Marking rooms done never overwrites another room's recorded state.
- **A three-way "realistic" forecast**, the seam between Site Progress and the stock register: remaining work (from BOQ × real progress), stock on hand, and the register's own consumption rate — combined into one verdict: *ENOUGH*, *SHORTAGE* (with how much to order and by when), or an honest *stock has no rate/on-hand data yet*. Never a guess.
- **Matches BOQ items to stock materials automatically**, on size tokens, type words and grade/certification qualifiers (fire-rated vs plain, PVC vs GI) — a plain cable is never confidently linked to a fire-rated one just because the type and size match.
- **Never assumes a 1:1 unit conversion.** A BOQ item's unit and its linked stock material's unit are compared directly; when they genuinely differ (e.g. "Nos" of light points linked to "Rmt" of pipe), a real per-link conversion factor is required before a shortage number is shown — the item is flagged *needs a conversion factor* instead of silently guessing.
- **₹ value and waste tracking.** Work done / remaining converts to rupees wherever an install rate is entered (never invented for unrated items); real stock consumption vs. recorded progress surfaces over-consumption (waste) and under-consumption (savings), with a caveat when too little progress has been logged yet for the number to be trustworthy.
- **Room-level drill-down.** Every BOQ item's drawer shows done / in-progress / not-started rooms for that item specifically, and the realistic forecast is worded against those outstanding rooms, not a bare quantity.
- **Shortage history, from real data.** Each new forecast run snapshots the linked verdict, so "was a flagged shortage actually prevented?" is answered from recorded history — never by asking an engineer. It tracks forward from when it is first used; old runs are not reconstructed, because mixing an old stock reading with today's progress would be a guess dressed up as history.
- **Daily Progress Report (DPR).** Progress updates are logged as they are made (the same write the slider already performs), so nothing extra is typed. Export a day or a date range, scoped to a service and/or chosen rooms, as a client copy or with an internal summary. The client copy carries the client's own BOQ reference numbers where the BOQ sheet provides them, and a "—" where it can't be determined. Manpower and hold-ups / plan-for-tomorrow notes can be added per day.

### Inventory — Safety, Tools and PPE
- **Splits inventory from forecasts.** Safety gear (shoes, helmets, jackets), Tools (ladder, hammer, grinder), and PPE issue logs are shown as plain stock counts, not forecasts. Each is its own tab with tailored filters: Safety and Tools filter by type and size; PPE shows who received what, filtered by item type, shoe size, and contractor.
- **Classifies safety and tools correctly.** An "8 FEET LADDER" is tagged as a Ladder, not confused with cable types — safety and tool names use a dedicated classifier that works across any register and any spelling variant.
- **PPE issue-count summary** — a one-line headline (e.g. "42 Jackets issued") above the table, matching whatever filter is currently applied.

### Billing — RA bills from challans and site progress
- **Three bill types, chosen on every new RA.** Supply, Installation, or Supply + Installation together. A service's setting is only the default offered first; it never forbids another type.
- **Payment terms per service, and per line when needed.** Supply % and Installation % are set once per service; a single line can carry its own %. A % left blank stays *not set* and the service cannot be billed until it is — nothing is assumed.
- **Supply from delivery challans, or from the stock register.** Challans are tagged to the RA they are billed in, or held back. In stock-register mode the quantity is taken only where an item is linked to exactly one material, that material is linked to no other item, and the conversion factor is known; anything else is typed by the engineer, never guessed.
- **Installation from Site Progress**, capped at the BOQ quantity unless a line is explicitly accepted as an excess for that draft.
- **Cumulative method.** This bill = quantity up to date − quantity in earlier *locked* RAs of the same stage. Over-billing shows as a negative line.
- **Locked and invoiced RAs are snapshots.** Rate, %, quantity, amount and description are frozen, so later rate, progress or link edits never change a submitted bill.
- **Invoice and annexure workbook.** Tax Invoice cover (client, GST, SGST/CGST, round-off, amount in words), annexure groups read from your sheet's own “Cost of …” rows, challan abstract and challan sheets. Draft workbooks are marked as drafts.
- **Work Orders.** One service can carry several Work Orders (e.g. point wiring vs the rest), each with its own setup, challans, RA numbers and drafts.
- **Wire metres → points.** A line that takes its quantity from wire metres gets virtual per-RA challans; a line takes its supply from wire *or* from challans, never both.
- **Stock-register inbox and check.** Deliveries seen in the stock register are offered as challans to accept, mark or link — not silently added. A stock-register check also lists lines where the register and the challans disagree; it is information only, and the bill follows the challans.
- **ProjectBase billing-request sheet.** Reads the sheet, matches lines by Internal Ref No only (exact), and fills just the “Now” column for the stages MI bills. A line it cannot match is reported, not guessed.
- **Measurement-sheet backup.** Shows where the installed quantity was recorded (zone-wise, an area total, or overall) and never claims more than the site recorded.
- **Item history, over-BOQ and CHECK notes.** Search any BOQ line to see every RA it went into and the challans behind it; lines above BOQ are flagged.
- **Auto-drafts, upload and download.** A draft opens automatically once installation moves (switchable per service); a filled sheet can be uploaded; downloads come as one workbook or a zip of workbooks. A short summary can be copied for WhatsApp or email.

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

The BOQ (for Site Progress and Billing) and the progress tracker are uploaded separately, from inside the Site Progress tab once a project exists.

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

## How an RA bill is built

1. **Pick the stage(s).** An RA belongs to one service and one series: `supply`, `install` or `together`. Only one draft may be open per stage, and a new RA takes the next number not yet used on any stage it carries.
2. **Quantity up to date.** Installation = Site Progress “used”, capped at the BOQ quantity. Supply = delivery challans tagged to this RA or an earlier one (challan mode), or stock “received” ÷ link factor (stock-register mode, only for unambiguous one-to-one links with a known factor).
3. **This bill.** `this = up to date − quantity in earlier locked RAs of the same stage`.
4. **Amount.** `rate × quantity this bill × stage % ÷ 100`, per stage, using the service's (or the line's own) Supply % and Installation %.
5. **Mark as invoiced.** This locks the RA and stores a full snapshot of the bill. The invoice number and date can be added afterwards, even on a locked RA, for the Tax Invoice cover.

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
- **Never invent a number.** An unset install rate stays unrated rather than defaulting to zero or one; a unit mismatch stays unresolved rather than assuming a 1:1 factor; an unset payment term blocks billing rather than assuming a percentage; a room not covered by any quantity group falls back to the item's own per-room quantity rather than dropping out silently. Every "never guessed" in this document is a real, deliberate branch in the code, not a decorative claim.
- **Exact beats fuzzy where a wrong match is costly.** BOQ ↔ stock linking and ProjectBase line matching use exact or narrow deterministic checks; a missed match is reported, while a wrong match would silently corrupt a forecast or a bill.
- **Honesty over confidence.** The gate, the confidence levels, the paused-detection, the today-anchoring, and the validator all exist to make the tool admit what it doesn't know.

---

## Architecture

```
material-intelligence/
  main.py              entry point — binds 0.0.0.0, reads $PORT
  Procfile             deployment (python main.py)
  backend/
    schema.py          column detection by meaning (synonyms + fuzzy)
    engine.py          date repair, parsing, forecast maths, status, today-anchoring
    rate.py            EWMA + active-day + surge consumption rate
    subcat.py          material sub-category detection (MEP trades)
    toolcat.py         safety and tool type detection (ladder, helmet, grinder, etc.)
    health.py          data-quality gate (can block a run)
    leadtime.py        per-material lead time from PO → GRN history (sample-size gated)
    api.py             FastAPI: upload, sync-sheet, forecast, subcategories, delete, export
    boq.py             BOQ parsing, incl. a ProjectBase-export-aware detector
    structure.py       editable floor / room / zone project tree + templates
    activity.py        tracker activities + activity → BOQ item mapping (+ suggestion)
    progress.py        room-by-room tick grid → % complete and used-quantity per item
    itemprog.py        per-item / per-room progress rollups (rooms panel, room buckets)
    linkage.py         BOQ item ↔ stock material matching (size / type / qualifier tokens)
    realtime.py        the three-signal realistic forecast (progress + BOQ + stock)
    shortage_history.py  per-run snapshots of the linked verdict, for "was it prevented?"
    pnl.py             ₹ value roll-ups and waste / saving tracking
    dpr.py             DPR auto-capture log + export
    clientref.py       the client's own BOQ reference numbers, for the client DPR
    siteprogress.py    Site Progress API routes tying the above together
    billing.py         RA bills: setup, challans, Work Orders, wire → points, stock inbox, downloads
    billing_cover.py   the Tax Invoice cover sheet of a billing workbook
    projectbase.py     ProjectBase "Billing Request" sheet: read, match, fill the "Now" column
    locqty.py          where installed quantity was recorded, for the measurement-sheet backup
    test_*.py          pytest suites
  frontend/
    index.html         one page, no framework — Forecast / Site Progress / Inventory / Billing
    style.css          shared design tokens and the Forecast/Inventory/Home UI
    app.js             no build step; upload, live sync, auto-refresh, project switching
    siteprogress.js    Site Progress tab — additive, self-contained, its own API namespace
    siteprogress.css   Site Progress-specific styles (sp- prefixed, never touches the rest)
    billing.js         Billing tab, shown and hidden by the same tab switch
    billing.css        Billing styles, scoped to the Billing view
    test_*.mjs         jsdom-driven UI tests
  data/
    uploads/           raw files, kept for audit
    runs/              one folder per run: forecast + daily + meta
    projects/<slug>/   per-project files (see below)
  docs/screenshots/    the images used in this README
  validate.py          logic-invariant checker
  backtest.py          accuracy back-testing harness
  requirements.txt
  start.bat / start.sh
```

Per-project files under `data/projects/<slug>/`:

| File | Holds |
|---|---|
| `boq.xlsx`, `boq.parquet` | the uploaded BOQ and its parsed line items |
| `structure.json` | the floor / room / zone tree |
| `activities.json`, `mapping.json` | tracker activities and their BOQ-item mapping |
| `rates.json`, `install_pct.json`, `planned.json`, `settings.json` | rates, installation payment terms, planned totals, project settings |
| `item_progress.json`, `item_rooms.json`, `item_room_qty.json` | per-item, per-room progress, applicability and quantity groups |
| `links.json` | BOQ item ↔ stock material links, with conversion factors |
| `dpr_log.json` | captured daily progress actions, source of the DPR |
| `shortage_log.json` | shortage-history snapshots |
| `billing.json` | billing setup, annexure groups, RAs, Work Orders, challans |

Stack: **FastAPI · pandas · Parquet · vanilla JS**. No database — immutable run folders and plain per-project files, full audit trail.

---

## Tests

```bash
# backend (from the repo root)
pip install pytest httpx
python -m pytest backend -q --ignore-glob='*chromium*'

# frontend (jsdom, no browser) — each file prints its own pass / FAIL lines
npm install jsdom css          # there is no package.json yet
cd frontend && node test_billing_ui.mjs
```

- The backend suites drive the real FastAPI routers against temporary project folders, so nothing touches real data.
- The `backend/test_*_chromium.py` files are not pytest tests: they are scripts that drive a real Chromium against real project data, which is why the command above skips them.
- Some tests read real client workbooks that are not in this repository; they fail on a machine that does not have those files.

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
