"""Real-Chromium test: Forecast / Inventory table headers must not overlap.
Reported on a phone: header "TOTAL RCVD" printed over "RUNS OUT" (Forecast) and
over "TYPE" (Inventory). Cause: table-layout:fixed + percent <colgroup>
(Total rcvd = 10% of a 640px table = 64px) while the nowrap header text is
~77px wide + 28px padding.
Checks, per width and per tab: (1) no header's text box intersects another
header's text box; (2) each header's text stays inside its own cell (overflow
may not exceed the cell's side padding); (3) the Total rcvd header is present.
Usage: BASE=http://localhost:8771 RUN=r_hyatt python3 -I test_report_table_headers_chromium.py
Set OLD_CSS=<path> to run the same checks against an older style.css.
"""
import os, sys
from playwright.sync_api import sync_playwright
BASE = os.environ.get("BASE", "http://localhost:8765")
RUN = os.environ.get("RUN", "r_hyatt")
OLD = os.environ.get("OLD_CSS")
fails = []
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: fails.append(m)
JS = """() => { const heads = [...document.querySelectorAll('#report thead th')].map(th => {
    const r = document.createRange(); r.selectNodeContents(th);
    const rs = [...r.getClientRects()].filter(x => x.width > 0);
    const l = Math.min(...rs.map(x => x.left)), rt = Math.max(...rs.map(x => x.right));
    const b = th.getBoundingClientRect(), cs = getComputedStyle(th);
    return {h: th.textContent.trim(), l, r: rt, top: Math.min(...rs.map(x => x.top)), bot: Math.max(...rs.map(x => x.bottom)),
            cl: b.left, cr: b.right, pl: parseFloat(cs.paddingLeft), pr: parseFloat(cs.paddingRight)}; });
  return heads; }"""
SIZES = [(306, 640, True), (360, 740, True), (390, 844, True), (412, 915, True), (640, 960, True),
         (768, 1024, True), (844, 390, True), (1024, 768, False), (1280, 800, False), (1440, 900, False)]
with sync_playwright() as p:
    b = p.chromium.launch()
    for w, h, mob in SIZES:
        ctx = b.new_context(viewport={"width": w, "height": h}, is_mobile=mob, has_touch=mob)
        pg = ctx.new_page()
        if OLD:
            pg.route("**/style.css*", lambda r: r.fulfill(path=OLD, content_type="text/css"))
        pg.goto(BASE); pg.wait_for_timeout(400)
        pg.evaluate(f"switchProject('{RUN}')"); pg.wait_for_timeout(1400)
        for mode in ("forecast", "inventory"):
            pg.click(f".spnav button[data-v={mode}]"); pg.wait_for_timeout(1200)
            hs = pg.evaluate(JS)
            tag = f"{w}px {mode}"
            check(any(x["h"].lower() == "total rcvd" for x in hs), f"{tag}: Total rcvd header present")
            bad = []
            for i in range(len(hs) - 1):
                a, c = hs[i], hs[i + 1]
                vert = a["top"] < c["bot"] - 1 and c["top"] < a["bot"] - 1
                if vert and a["r"] > c["l"] - 1 and c["r"] > a["l"] + 1 and a["l"] < c["l"] + 1e-6 and a["r"] > c["l"] - 1:
                    bad.append(f"{a['h']}|{c['h']}")
            check(not bad, f"{tag}: no header text overlaps its neighbour {bad}")
            out = [f"{x['h']}({max(x['cl'] + x['pl'] - x['l'], x['r'] - (x['cr'] - x['pr'])):.0f}px)" for x in hs
                   if x["l"] < x["cl"] + x["pl"] - 0.5 or x["r"] > x["cr"] - x["pr"] + 0.5]
            # tolerance: spilling into own padding is invisible; spilling past the cell edge is not
            real = [x["h"] for x in hs if x["l"] < x["cl"] - 0.5 or x["r"] > x["cr"] + 0.5]
            check(not real, f"{tag}: every header text stays inside its own cell {real}")
        ctx.close()
    b.close()
print("\nFAILED:", len(fails)); sys.exit(1 if fails else 0)
