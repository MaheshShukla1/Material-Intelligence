# Material Intelligence — MVP

Upload one Excel file. Get a forecast you can defend in a meeting.

    material-intel/
      backend/
        engine.py       date repair, parsing, forecast maths
        health.py       data quality gate (can block a run)
        api.py          FastAPI: upload, forecast, history, export
      frontend/
        index.html      one page
        style.css
        app.js          no framework, no build step
      data/
        uploads/        raw files, kept for audit
        runs/           one folder per run: forecast + daily + meta
      requirements.txt
      start.bat         Windows
      start.sh          Mac / Linux

## Run it

Windows:

    start.bat

Mac / Linux:

    ./start.sh

Then open http://127.0.0.1:8000

First run takes a couple of minutes while the libraries install. After that it
starts in about two seconds.

## What it accepts

| File | Detected as | Notes |
|---|---|---|
| Site stock register (`IN`/`OUT`/`BALANCE` repeated per date) | `register` | Safety / PPE / Tools sheets skipped |
| ProjectBase `Material Inward and Outward` export | `projectbase` | Negative quantity read as an issue |

Detection is automatic — there is no format picker, because a format picker is
one more thing a user can get wrong.

## The gate

If fewer than 10 materials have 8 or more distinct days of recorded
consumption, every predicted date is blanked and the run is marked
`INSUFFICIENT_DATA`. The banner explains why. This is deliberate: one confident
wrong date costs more trust than ten honest blanks.

## Runs are immutable

Every upload writes a new folder under `data/runs/`. Nothing is overwritten, so
any number shown in a meeting can be reproduced later from the exact file it
came from.

## API

    POST /api/upload            file, lead_time, asof   -> run_id + summary
    GET  /api/runs                                      -> recent runs
    GET  /api/run/{id}                                  -> meta + summary
    GET  /api/forecast/{id}     ?status=&service=&q=    -> rows
    GET  /api/material/{id}     ?name=                  -> daily history
    GET  /api/export/{id}                               -> CSV

## Before trusting a new version

    python backtest.py <file.xlsx> --end 2026-07-22 \
        --cutoffs 2026-06-30,2026-07-07,2026-07-12

If recall drops, the change made things worse. Run this every time the maths
changes, and keep the output.
