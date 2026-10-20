"""Real-Chromium check of the desktop-only widening of the Zones dialog (siteprogress.css, `.sp-zones .card.wide`).
Builds the dialog's real markup (same classes modal() writes) on the real style.css + siteprogress.css and measures it.
Desktop (>=1000px) must be wide with several zone cards per row; phones/tablets (<900px) must keep min(600px,94vw).
Usage: python3 -I test_zones_dialog_wide_chromium.py
"""
import os, sys, pathlib
from playwright.sync_api import sync_playwright

FE = pathlib.Path(__file__).resolve().parent.parent / "frontend"
cells = "".join(
    f'<div class="sp-roomcell"><label><input type="checkbox" data-room="r{i}"> Zone {i}</label>'
    f'<span class="sp-roomqty"><input class="ctl" type="number" value="100" data-roomqty="r{i}"><span class="u">of 357.14 Rmt</span></span></div>'
    for i in range(1, 22))
HTML = f"""<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="{(FE / 'style.css').as_uri()}"><link rel="stylesheet" href="{(FE / 'siteprogress.css').as_uri()}"></head>
<body><div class="sheet mid sp-zones" id="sp-modal"><div class="card wide" style="width:min(600px,94vw)">
<div class="sheethd"><div><h2>Zones → 13.2.1</h2><p>25 x 6 mm GI Strip</p></div><button class="btn" id="sp-modal-x">Close</button></div>
<div class="sp-maplist"><div class="sp-roomgroup"><div><b>Level 1</b></div>
<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:5px">{cells}</div></div></div>
<div class="mact"><button class="btn">Cancel</button><button class="btn primary">Save</button></div></div></div></body></html>"""

fails = []
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: fails.append(m)

JS = """() => { const card = document.querySelector('#sp-modal .card').getBoundingClientRect();
  const cells = [...document.querySelectorAll('.sp-roomcell')]; const top0 = cells[0].getBoundingClientRect().top;
  return {w: card.width, l: card.left, r: card.right, vw: innerWidth, docW: document.documentElement.scrollWidth,
          perRow: cells.filter(c => Math.abs(c.getBoundingClientRect().top - top0) < 2).length} }"""

with sync_playwright() as p:
    b = p.chromium.launch()
    tmp = pathlib.Path(os.environ.get("TMPDIR", "/tmp")) / "zones_wide_test.html"
    tmp.write_text(HTML)
    for name, w, h, expect_wide in (("desktop 1440", 1440, 900, True), ("desktop 1024", 1024, 768, True), ("laptop 1000", 1000, 700, True),
                                    ("tablet 999", 999, 800, False), ("tablet 700", 700, 900, False), ("phone 412", 412, 915, False), ("phone 360", 360, 740, False)):
        pg = b.new_page(viewport={"width": w, "height": h}); pg.goto(tmp.as_uri()); pg.wait_for_timeout(100)
        g = pg.evaluate(JS)
        if expect_wide:
            check(g["w"] >= min(1120, 0.92 * w) - 2 and g["w"] > 600, f"[{name}] dialog is wide ({round(g['w'])}px)")
            check(g["perRow"] >= 3, f"[{name}] zone cards sit {g['perRow']} per row (>=3)")
        else:
            want = min(600, 0.94 * w)
            check(abs(g["w"] - want) <= 2, f"[{name}] dialog keeps its old width min(600px,94vw) = {round(want)}px (got {round(g['w'])})")
        check(g["l"] >= 0 and g["r"] <= g["vw"] + 1 and g["docW"] <= g["vw"], f"[{name}] dialog fits, no sideways scroll")
        pg.close()
    b.close()
print("FAILS:", len(fails)); sys.exit(1 if fails else 0)
