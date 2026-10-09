"""Real-Chromium layout test: activity / item / Overall rows on a phone.
Reproduces the reported screens: Thoth Mall, service Fire, activity "Piping Work"
(8 items: QI1 "MS C CLASS PIPE 100MM" 589 of 796 MTR, 14 of 21 zones, 2 qty groups ...).
Before the fix, at ~390px the activity name got 3px, Overall activity/item names
40-45px (one word per line), the progress bar sat on the title, and the item
row's qty/icons floated beside a tall name block.
Setup:  python3 seed_fire_case.py <app root>   (adds the Fire data to the seeded Thoth project)
Usage:  BASE=http://localhost:8765 python3 -I test_phone_cards_chromium.py
"""
import os, sys
from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE", "http://localhost:8765")
RUN = os.environ.get("RUN", "r_thoth")
REAL = {"service": "Fire", "has_run": True, "run": RUN, "linked_items": 2, "shortages": 1, "items": [
    {"item_code": "QI1", "verdict": "OK", "links": [{"material": "MS C CLASS PIPE 100MM", "rate_per_day": 21.0, "factor": 1}]},
    {"item_code": "QI2", "verdict": "SHORTAGE", "links": [{"material": "MS C CLASS PIPE 150MM", "rate_per_day": 21.0, "factor": 1}]}]}
HIST = {"QI1": {"ongoing": False, "episodes": 1}, "QI2": {"ongoing": True, "episodes": 2}}
fails = []
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: fails.append(m)

def go(pg, view):
    pg.route("**/realistic/Fire", lambda r: r.fulfill(json=REAL))
    pg.route("**/shortage-items/Fire", lambda r: r.fulfill(json=HIST))
    pg.goto(BASE); pg.wait_for_timeout(500)
    pg.evaluate(f"switchProject('{RUN}')"); pg.wait_for_timeout(1200)
    pg.click(".spnav button[data-v=siteprogress]"); pg.wait_for_timeout(1500)
    if view == "overall":
        pg.click("#sp-pills .sp-pill[data-s=__overall__]"); pg.wait_for_timeout(1200)
        pg.click(".sp-ovrow >> text=Fire"); pg.wait_for_timeout(600)
        pg.click(".sp-ovactrow >> text=Piping Work"); pg.wait_for_timeout(600)
    else:
        pg.click("#sp-pills .sp-pill[data-s=Fire]"); pg.wait_for_timeout(1500)
        if not pg.query_selector(".sp-card.open"): pg.click("#sp-acts .sp-chd"); pg.wait_for_timeout(500)

GEOM = """() => { const r = e => e.getBoundingClientRect(), q = s => document.querySelector(s);
  const lines = e => Math.round(r(e).height / parseFloat(getComputedStyle(e).lineHeight || 18));
  const o = {vw: innerWidth, docW: document.documentElement.scrollWidth};
  const cn = q('.sp-cname'); if (cn) {
    const hd = q('.sp-chd'), bar = q('.sp-cbar'), mo = q('.sp-cmoney'), card = q('.sp-card.open'), row = q('.sp-brow'), nm = q('.sp-bname'), qty = q('.sp-qty'), pl = q('.sp-bqty'), mn = q('.sp-bmoney'), rm = q('[data-removeitem]');
    o.cname = r(cn).width; o.cnameBottom = r(cn).bottom; o.barTop = r(bar).top; o.barW = r(bar).width; o.hdD = getComputedStyle(hd).display;
    o.cmoneyR = r(mo).right; o.cardR = r(card).right; o.cardL = r(card).left;
    o.bname = r(nm).width; o.bnameBottom = r(nm).bottom;
    o.qtyClip = qty.scrollWidth > qty.clientWidth; o.qtyTop = r(qty).top;
    o.plTop = r(pl).top; o.mnTop = r(mn).top; o.rmTop = r(rm).top; o.mnR = r(mn).right; o.rmR = r(rm).right; o.rowR = r(row).right;
    o.chipsBottom = Math.max(...[...nm.querySelectorAll('.sp-linkchip')].map(c => r(c).bottom));
    o.chipsH = r(nm.querySelector('.sp-bmeta')).height;
  }
  const an = q('.sp-ovactrow > div:nth-child(2)'); if (an) {
    const it = q('.sp-ovitemrow > div'), ad = q('.sp-ovactmoney.done'), sd = q('.sp-ovrow > .sp-ovstat.done');
    o.ovAct = r(an).width; o.ovItem = r(it).width; o.ovItemLines = lines(it.querySelector('.sp-ovitemname'));
    o.ovActMoneyTop = r(ad).top; o.ovActNameBottom = r(an).bottom; o.ovSvcDone = r(sd).width;
  }
  return o; }"""

with sync_playwright() as p:
    b = p.chromium.launch()
    for W in (360, 412, 640):
        for view in ("service", "overall"):
            ctx = b.new_context(viewport={"width": W, "height": 915}, is_mobile=True, has_touch=True)
            pg = ctx.new_page(); go(pg, view); g = pg.evaluate(GEOM); tag = f"[{view} {W}px]"
            check(g["docW"] <= g["vw"], f"{tag} page does not scroll sideways")
            if view == "service":
                check(g["cname"] >= 140, f"{tag} activity name has room ({round(g['cname'])}px, was 3px)")
                check(g["barTop"] >= g["cnameBottom"] - 1, f"{tag} progress bar sits below the title, not on it")
                check(g["cmoneyR"] <= g["cardR"] and g["cname"] > 0, f"{tag} header money inside the card")
                check(g["bname"] >= 0.8 * (g["cardR"] - g["cardL"]) - 40, f"{tag} item name uses the full row width ({round(g['bname'])}px)")
                check(not g["qtyClip"], f"{tag} installed-quantity box shows its whole number")
                check(g["qtyTop"] >= g["bnameBottom"] - 1, f"{tag} quantity row starts below the name/chips block")
                check(abs(g["qtyTop"] - g["mnTop"]) < 24, f"{tag} quantity, % and rupees sit on one line")
                check(g["plTop"] > g["qtyTop"] and abs(g["plTop"] - g["rmTop"]) < 30, f"{tag} planned qty and icons share the last line (no floating gap)")
                check(g["rmR"] <= g["rowR"] + 1 and g["mnR"] <= g["rowR"] + 1, f"{tag} nothing leaves the row")
            else:
                check(g["ovAct"] >= 150, f"{tag} Overall activity name has room ({round(g['ovAct'])}px, was 43px)")
                check(g["ovItem"] >= 200 and g["ovItemLines"] <= 2, f"{tag} Overall item name is {round(g['ovItem'])}px wide, {g['ovItemLines']} line(s)")
                check(g["ovActMoneyTop"] >= g["ovActNameBottom"] - 1, f"{tag} done/remaining sit under the name")
                check(g["ovSvcDone"] >= 80, f"{tag} service 'done' figure is not crushed into the chevron column ({round(g['ovSvcDone'])}px)")
            ctx.close()
    # desktop must stay exactly the desktop layout (header is one flex row, bar 130px)
    ctx = b.new_context(viewport={"width": 1280, "height": 900}); pg = ctx.new_page(); go(pg, "service"); g = pg.evaluate(GEOM)
    check(g["hdD"] == "flex" and round(g["barW"]) == 130, f"[desktop 1280] activity header unchanged (flex row, bar {round(g['barW'])}px)")
    ctx.close(); b.close()
print("FAILS:", len(fails)); sys.exit(1 if fails else 0)
