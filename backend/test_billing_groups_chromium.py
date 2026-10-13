import sys, os
from playwright.sync_api import sync_playwright
"""Chromium test: Billing setup -> Annexure groups. The app-wide `th{position:sticky;top:var(--hdr)}` used to stick the table header 64px
DOWN inside this dialog, so it sat on top of the first rows (the list looked like it started at row 3). The header must sit above the first
row, every row must be reachable by scrolling, and Save must stay in view. Desktop 1366 and phone 390, on the real Thoth BOQ.
env: BASE (default http://localhost:8765)."""
BASE = os.environ.get("BASE", "http://localhost:8765")
JS = """() => {
  const rows=[...document.querySelectorAll('#bl-gbody tr[data-i]')];
  const t=document.querySelector('#bl-gbody thead th').getBoundingClientRect();
  const hit=(r)=>{const b=r.querySelector('input').getBoundingClientRect();const el=document.elementFromPoint(b.left+b.width/2,b.top+b.height/2);return !!(el&&r.contains(el));};
  const out={rows:rows.length, headAbove:t.bottom<=rows[0].getBoundingClientRect().top+1, firstHit:hit(rows[0]), pos:getComputedStyle(document.querySelector('#bl-gbody thead th')).position, covered:[]};
  rows.forEach((r,i)=>{ r.scrollIntoView({block:'center'}); if(!hit(r)) out.covered.push(i+1); });
  return out; }"""
errs = []
with sync_playwright() as p:
    b = p.chromium.launch()
    for name, (w, h) in {"desk": (1366, 900), "phone": (390, 844)}.items():
        ctx = b.new_context(viewport={"width": w, "height": h}, is_mobile=(name == "phone"), has_touch=(name == "phone"))
        pg = ctx.new_page(); pg.on("pageerror", lambda e, n=name: errs.append((n, str(e))))
        pg.goto(BASE); pg.wait_for_timeout(800)
        pg.click('.spnav button[data-v="billing"]'); pg.wait_for_selector(".bl-kpis"); pg.click("#bl-setup"); pg.wait_for_selector("[data-groups]")
        n = pg.locator("[data-groups]").count(); best = (0, 0)
        for i in range(n):                                          # the service with the most groups shows the problem best
            pg.locator("[data-groups]").nth(i).click(); pg.wait_for_selector("#bl-gbody tr[data-i]")
            best = max(best, (pg.locator("#bl-gbody tr[data-i]").count(), i)); pg.click("#bl-mx"); pg.wait_for_timeout(80)
        pg.locator("[data-groups]").nth(best[1]).click(); pg.wait_for_selector("#bl-gbody tr[data-i]")
        pg.evaluate("document.querySelector('#bl-modal .bl-mbody').scrollTop=0")
        r = pg.evaluate(JS)
        assert r["rows"] >= 5, r
        assert r["pos"] == "static", f"{name}: the header must not be sticky inside the dialog: {r}"
        assert r["headAbove"] and r["firstHit"], f"{name}: the header covers the first row: {r}"
        assert not r["covered"], f"{name}: rows covered by the header while scrolling: {r}"
        assert pg.evaluate("(()=>{const r=document.querySelector('#bl-modal .mact .primary').getBoundingClientRect();return r.bottom<=innerHeight&&r.top>=0})()"), f"{name}: Save groups is off screen"
        assert pg.evaluate("document.documentElement.scrollWidth-document.documentElement.clientWidth") <= 0, f"{name}: page scrolls sideways"
        ctx.close()
    b.close()
assert not errs, errs
print("annexure groups chromium: all passed")
