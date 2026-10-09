"""Real-Chromium layout test for the Zones/Rooms dialog (done-quantity boxes).
Reproduces the reported screen: Thoth Mall, Electrical > Earthing Strip, item 13.2.1
(25 x 6 mm GI Strip), unit Rmt, 7500 planned over 21 zones (357.14 each), 27% recorded.
Needs the app running with that data and Playwright.
Usage:  BASE=http://localhost:8765 python3 -I test_zones_dialog_layout_chromium.py
"""
import os, sys
from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE", "http://localhost:8765")
RUN, SERVICE, ITEM = os.environ.get("RUN", "r_thoth"), os.environ.get("SERVICE", "Electrical"), os.environ.get("ITEM", "13.2.1")
fails = []
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: fails.append(m)

def open_dialog(pg):
    pg.goto(BASE); pg.wait_for_timeout(500)
    pg.evaluate(f"switchProject('{RUN}')"); pg.wait_for_timeout(1200)
    pg.click(".spnav button[data-v=siteprogress]"); pg.wait_for_timeout(1500)
    pg.click(f"#sp-pills .sp-pill[data-s={SERVICE}]"); pg.wait_for_timeout(1500)
    sel = f".sp-brow[data-code='{ITEM}'] [data-roomsedit]"
    if not pg.query_selector(sel):
        pg.click("#sp-acts .sp-cname"); pg.wait_for_timeout(600)
    pg.click(sel); pg.wait_for_timeout(1500)

GEOM = """() => {
  const q = s => document.querySelector(s), r = e => e.getBoundingClientRect();
  const card = r(q('#sp-modal .card')), list = q('.sp-maplist'), cell = q('.sp-roomcell');
  const cr = r(cell), inp = r(cell.querySelector('input[data-roomqty]')), u = r(cell.querySelector('.u'));
  const lab = r(cell.querySelector('label')), x = r(q('#sp-modal-x'));
  return {vw: innerWidth, docW: document.documentElement.scrollWidth,
    cardL: card.left, cardR: card.right, listScrollW: list.scrollWidth, listClientW: list.clientWidth,
    cellL: cr.left, cellR: cr.right, inpL: inp.left, inpR: inp.right, inpH: inp.height, inpW: inp.width,
    uR: u.right, uL: u.left, labR: lab.right, xH: x.height, headH: r(q('#sp-modal .sheethd')).height,
    cellsInRow: [...document.querySelectorAll('.sp-roomgroup')][0].querySelectorAll('.sp-roomcell').length}
}"""

with sync_playwright() as p:
    b = p.chromium.launch()
    for name, vp, mob in (("desktop 1440", {"width": 1440, "height": 889}, False), ("phone 412", {"width": 412, "height": 915}, True), ("phone 360", {"width": 360, "height": 740}, True),
                          ("small phone 320x568", {"width": 320, "height": 568}, True), ("landscape 844x390", {"width": 844, "height": 390}, True), ("landscape 667x375", {"width": 667, "height": 375}, True)):
        ctx = b.new_context(viewport=vp, is_mobile=mob, has_touch=mob)
        pg = ctx.new_page(); open_dialog(pg)
        check(pg.query_selector("#sp-modal.sp-zones") is not None, f"[{name}] dialog open")
        g = pg.evaluate(GEOM)
        check(g["listScrollW"] <= g["listClientW"] + 1, f"[{name}] no sideways scrollbar inside the dialog ({g['listScrollW']} vs {g['listClientW']})")
        check(g["docW"] <= g["vw"], f"[{name}] page does not scroll sideways")
        check(g["cardL"] >= 0 and g["cardR"] <= g["vw"] + 1, f"[{name}] dialog fits the screen")
        check(g["uR"] <= g["cellR"] and g["inpR"] <= g["cellR"] and g["inpL"] >= g["cellL"], f"[{name}] number box and 'of 357.14 Rmt' sit fully inside the zone card")
        check(g["inpW"] <= 120 and g["inpL"] > g["labR"] - 4, f"[{name}] number box is compact and to the right of the name ({round(g['inpW'])}px)")
        check(g["xH"] <= 50, f"[{name}] Close button is a normal height ({round(g['xH'])}px)")
        check(g["headH"] <= 130, f"[{name}] header is short ({round(g['headH'])}px)")
        if mob: check(g["inpH"] >= 43, f"[{name}] number box is a touch target ({round(g['inpH'])}px)")
        if name.startswith("desktop"): check(g["cellsInRow"] >= 7, "[desktop] all 7 zones of a level are listed")
        # nothing ticked -> nothing highlighted, recorded values shown muted
        check(len(pg.query_selector_all(".sp-roomcell.is-on")) == 0, f"[{name}] untouched zones are not highlighted")
        # tick one -> full plan, green; type a partial in another -> amber, auto-ticked
        pg.check("#sp-modal input[data-room]>>nth=0"); pg.fill("input[data-roomqty]>>nth=1", "200"); pg.wait_for_timeout(100)
        c0 = pg.eval_on_selector(".sp-roomcell>>nth=0", "e=>e.className"); c1 = pg.eval_on_selector(".sp-roomcell>>nth=1", "e=>e.className")
        check("is-on" in c0 and "is-full" in c0, f"[{name}] ticked zone = full ({c0})")
        check("is-on" in c1 and "is-partial" in c1, f"[{name}] typed 200 of 357.14 = partial ({c1})")
        check(pg.input_value("input[data-roomqty]>>nth=0") == "357.14", f"[{name}] ticking fills the full plan 357.14")
        # the planned-quantity box stays visible, labelled as the plan, inside the dialog
        check(pg.is_visible("#sp-roomqty"), f"[{name}] planned-quantity box is visible")
        pb = pg.eval_on_selector(".sp-planbox", "e=>{const r=e.getBoundingClientRect(),c=document.querySelector('#sp-modal .card').getBoundingClientRect(),i=document.querySelector('#sp-roomqty').getBoundingClientRect();return [r.left,r.right,c.left,c.right,i.width,i.height]}")
        check(pb[0] >= pb[2] and pb[1] <= pb[3] and pb[4] >= 80, f"[{name}] plan box fits the dialog and the input is usable ({round(pb[4])}px wide)")
        check("PLAN" in pg.inner_text("#sp-qty-hint"), f"[{name}] hint explains it sets the PLAN")
        # header + footer stay on screen without scrolling; only the zone list scrolls
        for sel in ("#sp-mark-done", "#sp-modal-save", "#sp-modal-x"):
            bx = pg.eval_on_selector(sel, "e=>{const r=e.getBoundingClientRect();return [r.left,r.right,r.top,r.bottom,innerWidth,innerHeight]}")
            check(bx[0] >= 0 and bx[1] <= bx[4] + 1 and bx[2] >= 0 and bx[3] <= bx[5] + 1, f"[{name}] {sel} on screen without scrolling")
        ctx.close()
    b.close()
print("FAILS:", len(fails)); sys.exit(1 if fails else 0)
