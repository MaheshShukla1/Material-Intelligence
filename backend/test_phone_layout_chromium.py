"""Real-Chromium layout test for the per-tab header + phone layout.
Needs the app running (BASE) with two seeded projects and Playwright.
Usage:  BASE=http://localhost:8765 python3 -I test_phone_layout.py
"""
import os, sys, json
from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE", "http://localhost:8765")
RUNS = {"Hyatt": "r_hyatt", "Thoth": "r_thoth"}
WIDTHS = [360, 412, 768, 1280]

UNCONTAINED = """() => {
  const vw = document.documentElement.clientWidth, out = [];
  document.querySelectorAll('body *').forEach(e => {
    const r = e.getBoundingClientRect(); const cs = getComputedStyle(e);
    if (!(r.width > 0) || cs.display === 'none' || cs.visibility === 'hidden') return;
    if (r.right <= vw + 1) return;
    let a = e.parentElement, contained = false;
    while (a && a !== document.body) {
      const ac = getComputedStyle(a);
      if (['auto','scroll','hidden'].includes(ac.overflowX) && a.getBoundingClientRect().right <= vw + 1) { contained = true; break; }
      a = a.parentElement;
    }
    if (!contained) out.push(e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + '.' + String(e.className).split(' ')[0] + ' right=' + Math.round(r.right));
  });
  return {vw, docScrollW: document.documentElement.scrollWidth, over: out.slice(0, 8), n: out.length};
}"""
VISIBLE = """(sel) => { const e = document.querySelector(sel); if (!e) return false;
  const cs = getComputedStyle(e); return cs.display !== 'none' && cs.visibility !== 'hidden' && e.getClientRects().length > 0; }"""

fails = []
def check(cond, msg):
    if not cond: fails.append(msg); print("FAIL:", msg)

def open_app(b, w, run):
    ctx = b.new_context(viewport={"width": w, "height": 915}, device_scale_factor=2,
                        is_mobile=(w < 800), has_touch=(w < 800))
    pg = ctx.new_page(); pg.goto(BASE); pg.wait_for_timeout(500)
    pg.evaluate(f"switchProject('{run}')"); pg.wait_for_timeout(1500)
    return pg

def vis(pg, sel): return pg.evaluate(VISIBLE, sel)

with sync_playwright() as p:
    b = p.chromium.launch()
    for name, run in RUNS.items():
        for w in WIDTHS:
            tag = f"{name}@{w}"
            pg = open_app(b, w, run)
            # ---- Forecast
            o = pg.evaluate(UNCONTAINED)
            check(o["n"] == 0 and o["docScrollW"] <= w, f"{tag} Forecast overflow {o}")
            for sel in ["#home", "#proj", "#leadwrap", "#pick", "#sync", "#dl", "#del"]:
                check(vis(pg, sel), f"{tag} Forecast: {sel} should be visible")
            check(vis(pg, "#report"), f"{tag} Forecast: report visible")
            # ---- Site Progress
            pg.click(".spnav button[data-v=siteprogress]"); pg.wait_for_timeout(1800)
            o = pg.evaluate(UNCONTAINED)
            check(o["n"] == 0 and o["docScrollW"] <= w, f"{tag} SiteProgress overflow {o}")
            for sel in ["#home", "#proj", "#leadwrap", "#pick", "#sync", "#dl", "#del", "#ctx", "#fresh", "#report"]:
                check(not vis(pg, sel), f"{tag} SiteProgress: {sel} must NOT be visible")
            check(vis(pg, "#sp-proj"), f"{tag} SiteProgress: own project select visible")
            pills = pg.evaluate("""() => { const ps=[...document.querySelectorAll('#sp-pills .sp-pill')]; const vw=document.documentElement.clientWidth;
                return {n: ps.length, allInside: ps.every(e=>{const r=e.getBoundingClientRect(); return r.left>=0 && r.right<=vw+1}),
                        minH: Math.min(...ps.map(e=>e.getBoundingClientRect().height)),
                        pillsTop: document.getElementById('sp-pills').getBoundingClientRect().top + scrollY,
                        treeTop: (document.getElementById('sp-tree')||{getBoundingClientRect:()=>({top:0})}).getBoundingClientRect().top + scrollY}; }""")
            check(pills["n"] == 6, f"{tag} SP expected 6 pills (Overall+5 services) got {pills}")  # Overall page has pills too
            check(pills["allInside"], f"{tag} SP all pills inside viewport {pills}")
            if w >= 800: pass
            # pills must be reachable in first screen on phone (Overall view shown first)
            check(pills["pillsTop"] < 600, f"{tag} SP pills too low on page: {pills}")
            # open a service so the structure tree view renders, re-check ordering + overflow
            pg.click("#sp-pills .sp-pill[data-s=Electrical]"); pg.wait_for_timeout(1800)
            o = pg.evaluate(UNCONTAINED)
            check(o["n"] == 0 and o["docScrollW"] <= w, f"{tag} SP/Electrical overflow {o}")
            pills = pg.evaluate("""() => ({n: document.querySelectorAll('#sp-pills .sp-pill').length,
                pillsTop: document.getElementById('sp-pills').getBoundingClientRect().top + scrollY,
                treeTop: document.getElementById('sp-tree').getBoundingClientRect().top + scrollY,
                minH: Math.min(...[...document.querySelectorAll('#sp-pills .sp-pill')].map(e=>e.getBoundingClientRect().height))})""")
            check(pills["n"] == 6, f"{tag} SP/Electrical pills count {pills}")
            if w <= 900:
                check(pills["pillsTop"] < pills["treeTop"], f"{tag} phone: pills must be above the structure tree {pills}")
                check(pills["pillsTop"] < 600, f"{tag} phone: pills within first screen-ish {pills}")
                if w < 800: check(pills["minH"] >= 43, f"{tag} touch pill height {pills}")
            # leak guard: programmatic run switch while on Site Progress must not show Forecast
            pg.evaluate("switchProject('r_thoth')"); pg.wait_for_timeout(1500)
            check(not vis(pg, "#report"), f"{tag} SP: Forecast report leaked after run switch")
            # ---- Inventory
            pg.click(".spnav button[data-v=inventory]"); pg.wait_for_timeout(1500)
            o = pg.evaluate(UNCONTAINED)
            check(o["n"] == 0 and o["docScrollW"] <= w, f"{tag} Inventory overflow {o}")
            check(not vis(pg, "#leadwrap"), f"{tag} Inventory: lead time hidden")
            for sel in ["#home", "#proj", "#pick", "#sync", "#dl", "#del", "#report"]:
                check(vis(pg, sel), f"{tag} Inventory: {sel} visible")
            check(not vis(pg, "#kpis"), f"{tag} Inventory: KPI row hidden")
            # ---- back to Forecast: lead time returns, report back
            pg.click(".spnav button[data-v=forecast]"); pg.wait_for_timeout(1000)
            check(vis(pg, "#leadwrap") and vis(pg, "#report"), f"{tag} back on Forecast: lead + report visible")
            # ---- double tap on Site Progress then back to Forecast must not leave a blank page
            pg.click(".spnav button[data-v=siteprogress]"); pg.wait_for_timeout(1200)
            pg.click(".spnav button[data-v=siteprogress]"); pg.wait_for_timeout(1200)
            pg.click(".spnav button[data-v=forecast]"); pg.wait_for_timeout(1000)
            check(vis(pg, "#report"), f"{tag} double-tap Site Progress then Forecast -> report must be visible")
            pg.context.close()
    b.close()
print("FAILS:", len(fails))
sys.exit(1 if fails else 0)
