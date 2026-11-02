"""The Weekly Progress Report: a DPR export over a date range of EXACTLY 7 days. Same sheet as the daily DPR, "Week" wording instead of "Today",
Weekly Average instead of the manpower to-date columns, no colour, and week-to-week continuity (this week's "Before this week" is last week's
"To date"). Real flow, like test_dpr_client_export.py: BOQ through the real route, progress and manpower through the real routes, the xlsx read
back cell by cell."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from backend import dpr
from backend.test_dpr_client_export import (U, env, day, slider, export, rows_of, work_lines)  # noqa: F401  (env is a fixture)

W1 = ("2026-10-12", "2026-10-18")          # Mon - Sun
W2 = ("2026-10-19", "2026-10-25")


def mp(c, date, sup, sk, hp, svc="Electrical"):
    r = c.post(f"{U}/dpr/day", json={"date": date, "manpower": {svc: {"supervisor": sup, "skilled": sk, "helper": hp}}})
    assert r.status_code == 200, r.text


def week(c, span, mode="none"):
    return export(c, span[0], mode, end=span[1])[0]


def all_text(ws):
    return [str(v) for row in rows_of(ws) for v in row if v is not None]


def test_seven_days_is_the_weekly_report_with_week_wording_and_week_number(env):
    c = env
    slider(c, "tray1", 0.5)
    day(14)
    slider(c, "tray1", 0.8)
    ws = week(c, W1)["DPR"]
    assert ws["A1"].value == "WEEKLY PROGRESS REPORT"
    assert ws["E2"].value == "Week" and "12-Oct-2026 to 18-Oct-2026" in ws["G2"].value
    assert ws["J2"].value == "Week No." and ws["K2"].value == "Week 1"
    txt = " | ".join(all_text(ws))
    assert "Before this week" in txt and "This week" in txt and "THIS WEEK" in txt and "PLAN FOR NEXT WEEK" in txt
    assert "Today" not in txt and "TODAY" not in txt and "period" not in txt.lower() and "DPR No." not in txt
    ln = work_lines(ws)["10 (i)"]
    assert (ln["prev"], ln["today"], ln["cum"]) == (0, 80, 80)       # the whole week's work in ONE row, not 50 + 30 twice


def test_only_exactly_seven_days_is_weekly(env):
    c = env
    slider(c, "tray1", 0.5)
    for start, end, weekly in (("2026-10-12", "2026-10-17", False), ("2026-10-12", "2026-10-19", False),
                               ("2026-10-12", "2026-10-18", True), ("2026-10-12", "2026-10-12", False)):
        ws = export(c, start, "none", **({"end": end} if end != start else {}))[0]["DPR"]
        assert (ws["A1"].value == "WEEKLY PROGRESS REPORT") is weekly, (start, end)
    ws = export(c, "2026-10-12", "none", end="2026-10-17")[0]["DPR"]       # 6 days stays the ordinary period report
    assert ws["A1"].value == "DAILY PROGRESS REPORT" and ws["E2"].value == "Period" and ws["K2"].value == "—"


def test_next_week_starts_where_last_week_ended(env):
    c = env
    slider(c, "tray1", 0.5)                      # week 1: 50
    day(14)
    slider(c, "tray1", 0.7)                      # week 1: +20 -> 70
    day(21)
    slider(c, "tray1", 0.9)                      # week 2: +20 -> 90
    w1, w2 = week(c, W1)["DPR"], week(c, W2)["DPR"]
    a, b = work_lines(w1)["10 (i)"], work_lines(w2)["10 (i)"]
    assert (a["prev"], a["today"], a["cum"]) == (0, 70, 70)
    assert (b["prev"], b["today"], b["cum"]) == (70, 20, 90)          # Before this week (week 2) == Cumulative (week 1)
    assert w1["K2"].value == "Week 1" and w2["K2"].value == "Week 2"
    p1 = [r for r in rows_of(w1) if r[1] == "Electrical"][0]
    p2 = [r for r in rows_of(w2) if r[1] == "Electrical"][0]
    assert p2[4] == pytest.approx(p1[6])                                # progress status: Before this week == last week's To date
    assert p2[6] == pytest.approx(p2[4] + p2[5])


def test_manpower_total_man_days_and_weekly_average_over_the_days_typed(env):
    c = env
    slider(c, "tray1", 0.5)
    mp(c, "2026-10-12", 1, 8, 12)               # 21
    mp(c, "2026-10-13", 1, 9, 14)               # 24
    mp(c, "2026-10-15", 1, 7, 8)                # 16   (3 of the 7 days typed: 14, 16, 17, 18 not typed)
    mp(c, "2026-10-05", 5, 5, 5)                # the week BEFORE: must not leak into this week
    ws = week(c, W1)["DPR"]
    head = [r for r in rows_of(ws) if r[1] == "Service" and r[7] == "Total man-days"][0]
    assert head[8] == "Weekly Average" and head[9] is None                # no "Before this week" and no "To date" in manpower
    row = [r for r in rows_of(ws) if r[1] == "Electrical"][1]
    assert row[4:8] == [3, 24, 34, 61]                                     # supervisor / skilled / helper / man-days over the week
    assert row[8] == pytest.approx(61 / 3)                                 # over the 3 days typed, NOT /7
    tot = [r for r in rows_of(ws) if r[1] == "Total"][0]
    assert tot[7] == 61 and tot[8] == pytest.approx(61 / 3)


def test_weekly_average_is_a_dash_when_no_manpower_was_typed_never_a_zero(env):
    c = env
    slider(c, "tray1", 0.5)
    ws = week(c, W1)["DPR"]
    row = [r for r in rows_of(ws) if r[1] == "Electrical"][1]
    assert row[4:9] == ["—"] * 5
    tot = [r for r in rows_of(ws) if r[1] == "Total"][0]
    assert tot[8] == "—"


def test_total_weekly_average_is_total_man_days_over_days_with_any_manpower_not_a_sum_of_averages():
    """Two services typed on different days: the Total row must be 60 man-days over the 3 days that have any manpower (=20),
    not 20/2 + 40/1 (= 50, the sum of the services' own averages)."""
    data = {"weekly": True, "period": True, "week_label": "Week 1", "project": "P", "date_label": "x to y", "services": ["A", "B"],
            "progress": {"A": {"prev": 0, "today": .1, "todate": .1}, "B": {"prev": 0, "today": .1, "todate": .1}}, "overall": {},
            "manpower": {"A": {"supervisor": 0, "skilled": 20, "helper": 0, "total": 20, "avg": 10.0},
                         "B": {"supervisor": 0, "skilled": 40, "helper": 0, "total": 40, "avg": 40.0}},
            "manpower_total": 60, "manpower_avg": 20.0, "lines": [], "holdups": "", "plan": ""}
    ws = dpr.build_client_dpr(data)["DPR"]
    tot = [r for r in rows_of(ws) if r[1] == "Total"][0]
    assert tot[7] == 60 and tot[8] == 20.0


def test_the_weekly_report_has_no_colour_except_the_title_bar_and_the_daily_one_keeps_its_own(env):
    c = env
    slider(c, "tray1", 0.5)
    wb = week(c, W1, "without")
    assert wb.sheetnames == ["DPR", "Summary", "BOQ progress"]
    for ws in wb.worksheets:
        cells = [cell for row in ws.iter_rows() for cell in row]
        filled = {cell.coordinate for cell in cells if cell.fill is not None and cell.fill.fill_type == "solid"}
        white = {cell.coordinate for cell in cells if cell.value is not None and cell.font is not None and cell.font.color is not None
                 and cell.font.color.rgb in ("00FFFFFF", "FFFFFFFF")}
        if ws.title == "DPR":              # the one filled part: the "WEEKLY PROGRESS REPORT" bar (merged A1:L1), white text on it
            assert filled and all(ws[f].row == 1 for f in filled), sorted(filled)[:6]
            assert white == {"A1"}, white
        else:
            assert filled == set() and white == set(), (ws.title, sorted(filled)[:5], sorted(white)[:5])
    daily = export(c, "2026-10-12", "none")[0]["DPR"]
    assert daily["A1"].fill.fill_type == "solid"                      # day report untouched


def test_summary_and_boq_progress_sheets_use_week_words(env):
    c = env
    slider(c, "tray1", 0.5)
    mp(c, "2026-10-12", 1, 8, 12)
    wb = week(c, W1, "without")
    summ = " | ".join(all_text(wb["Summary"]))
    assert "Before this week" in summ and "This week" in summ and "Manpower this week" in summ
    assert "Today" not in summ and "This period" not in summ and "Manpower today" not in summ and "Manpower in period" not in summ


def test_a_day_and_a_free_range_are_byte_for_byte_what_they_were(env):
    c = env
    slider(c, "tray1", 0.5)
    mp(c, "2026-10-12", 1, 8, 12)
    for kw in ({}, {"end": "2026-10-14"}):
        ws = export(c, "2026-10-12", "without", **kw)[0]
        txt = " | ".join(all_text(ws["DPR"]) + all_text(ws["Summary"]))
        assert "Week" not in txt.replace("Weekday", "") and "Weekly" not in txt
        assert ws["DPR"]["A1"].fill.fill_type == "solid"
    ws = export(c, "2026-10-12", "none")[0]["DPR"]
    mprow = [r for r in rows_of(ws) if r[1] == "Electrical"][1]
    assert mprow[4:10] == [1, 8, 12, 21, "—", 21]                      # a single day keeps Total / Up to previous day / To date


def test_the_file_name_of_a_week_is_the_ranges_name(env):
    c = env
    slider(c, "tray1", 0.5)
    r = export(c, W1[0], "none", end=W1[1])[1]
    assert "DPR_thoth-mall_2026-10-12_to_2026-10-18.xlsx" in r.headers["content-disposition"]
