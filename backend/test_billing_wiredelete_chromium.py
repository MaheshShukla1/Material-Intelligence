import os, sys, json, urllib.parse as up
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_billing_stockinbox_chromium.py")).read()
exec(src.split("with sync_playwright() as p:")[0])
"""Chromium, real backend, real Thoth BOQ. 'Wire RA-01' in Challans has a Delete like every other challan: it takes the points out of the bill,
the wire deliveries stay, and giving the metres again brings everything back. Desktop 1366 + phone 390."""
from playwright.sync_api import sync_playwright
U = up.quote(PW)
def wire_setup():
    reset()
    call = lambda path, body=None, m=None: (post(path, body, m) if body is not None else get(path))
    post(P + "/setup", {"service": PW, "mode": "separate", "supply_pct": 75, "install_pct": 15, "supply_source": "challan"})
    for no, dte, cols in (("1207", "2026-03-28", dict(RED=300, YELLOW=600, BLUE=300)), ("615", "2026-07-07", dict(RED=3300, YELLOW=3000, BLUE=2700)), ("617", "2026-07-16", dict(RED=3900, YELLOW=3600, BLUE=2400))):
        post(f"{P}/wire/{U}/challans", {"no": no, "date": dte, "ra": 1, "sizes": [{"size": "1.5", "colours": cols}]})
    post(f"{P}/wire/{U}/alloc", {"rows": [{"item_code": "1.1.5", "size": "1.5", "metres": 20100}]}, "PUT")
with sync_playwright() as p:
    b = p.chromium.launch()
    for name, (w, h) in {"desk": (1366, 900), "phone": (390, 844)}.items():
        wire_setup()
        before = get(f"{P}/wire/{U}")
        assert len(before["challans"]) == 3 and {l["item_code"]: l["points"] for l in before["lines"]} == {"1.1.5": 4020}
        ctx = b.new_context(viewport={"width": w, "height": h}, has_touch=(name == "phone"), is_mobile=(name == "phone"))
        pg = ctx.new_page(); pg.on("pageerror", lambda e, n=name: errs.append((n, str(e))))
        pg.goto(BASE); pg.wait_for_timeout(800)
        pg.click('.spnav button[data-v="billing"]'); pg.wait_for_selector(".bl-kpis")
        pg.click(f'[data-ch="{PW}"]'); pg.wait_for_selector('.bl-ch:has-text("Wire RA-01")')
        row = pg.locator('.bl-ch:has-text("Wire RA-01")'); row.click()
        row.locator("[data-wdel]").wait_for()
        pg.screenshot(path=f"{OUT}/{name}_wd_1_row.png", full_page=True); noscroll(pg, name + " row")
        if name == "phone": fingers(pg, '[data-wdel]', "wire delete", 44)
        row.locator("[data-wdel]").click(); pg.wait_for_selector("#bl-modal")
        assert "4,020 points on 1 line" in pg.inner_text("#bl-modal"), pg.inner_text("#bl-modal")
        pg.screenshot(path=f"{OUT}/{name}_wd_2_confirm.png")
        pg.click("#bl-modal .mact .btn:has-text('Delete')")
        pg.wait_for_function("!document.querySelector('[data-wdel]')")
        pg.screenshot(path=f"{OUT}/{name}_wd_3_after.png", full_page=True); noscroll(pg, name + " after")
        after = get(f"{P}/wire/{U}")
        assert after["lines"] == [] and len(after["challans"]) == 3, "points gone, deliveries stay"
        ra = post(P + "/ra", {"service": PW, "series": "supply"})["id"]
        assert get(f"{P}/ra/{ra}")["this_total"] == 0
        post(f"{P}/wire/{U}/alloc", {"rows": [{"item_code": "1.1.5", "size": "1.5", "metres": 20100}]}, "PUT")      # give the metres again
        assert get(f"{P}/ra/{ra}")["this_total"] > 0
        ctx.close()
    b.close()
assert not errs, errs[:3]
print("billing wire delete chromium: all passed")
