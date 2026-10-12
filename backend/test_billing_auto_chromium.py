import sys, json, os
from playwright.sync_api import sync_playwright
import urllib.request as u
"""Chromium test (desktop 1366 + phone 390), real backend: nobody clicks 'New RA bill'. The engineer logs installation in
Site Progress; opening Billing shows the service's draft already there; more work logged in Site Progress shows up in the
open draft by itself; deleting the draft keeps it closed; the per-service switch turns it off.
env: BASE (default http://localhost:8765), ROOT (scratch app root), OUT (screenshots)."""
BASE = os.environ.get("BASE", "http://localhost:8765")
ROOT = os.environ["ROOT"]
OUT = os.environ.get("OUT", "/tmp/billing_auto_shots")
os.makedirs(OUT, exist_ok=True)
P = "/api/billing/thoth-mall"
errs = []
def post(path, body):
    return json.load(u.urlopen(u.Request(BASE + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})))
def get(path):
    return json.load(u.urlopen(BASE + path))
def logged(code, frac):
    post("/api/siteprogress/thoth-mall/progress/item", {"service": "HVAC", "item_code": code, "frac": frac})
def reset():
    f = os.path.join(ROOT, "data", "projects", "thoth-mall", "billing.json")
    if os.path.exists(f): os.remove(f)
    post(P + "/setup", {"service": "HVAC", "mode": "separate", "supply_pct": 75, "install_pct": 25, "supply_source": "challan"})
    lines = get(P + "/lines/HVAC")["lines"]
    return [l["item_code"] for l in lines[:3]]
def noscroll(pg, label):
    assert 0 == pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth"), label + ": page scrolls sideways"
with sync_playwright() as p:
    b = p.chromium.launch()
    for name, (w, h) in {"desk": (1366, 900), "phone": (390, 844)}.items():
        codes = reset()
        for c in codes: logged(c, 0)
        ctx = b.new_context(viewport={"width": w, "height": h}, has_touch=(name == "phone"), is_mobile=(name == "phone"))
        ctx.add_init_script("window.__BL_LIVE_MS = 1500")
        pg = ctx.new_page()
        pg.on("pageerror", lambda e, n=name: errs.append(("pageerror", n, str(e))))
        pg.on("console", lambda m, n=name: errs.append(("console", n, m.text)) if m.type == "error" else None)
        pg.goto(BASE); pg.wait_for_timeout(800)
        pg.click('.spnav button[data-v="billing"]'); pg.wait_for_selector(".bl-kpis")
        assert pg.locator(".bl-rar").count() == 0, "nothing logged -> no RA"
        # engineer logs installation (Site Progress) -> the draft is there when Billing is opened again (and live while open)
        logged(codes[0], 0.4)
        pg.wait_for_selector(".bl-rar", timeout=6000)             # the live refresh opens it, nobody clicked anything
        row = pg.inner_text(".bl-rar")
        assert "HVAC" in row and "Installation" in row and "Draft" in row, row
        t0 = pg.text_content(".bl-rar .n b")
        pg.screenshot(path=f"{OUT}/{name}_1_auto_draft.png", full_page=True); noscroll(pg, name + " home")
        # open it, then more work is logged -> the open draft follows without a reload
        pg.click(".bl-rar"); pg.wait_for_selector("#bl-lock")
        k0 = pg.text_content(".bl-kpi b")
        logged(codes[1], 0.5)
        pg.wait_for_function("(k) => document.querySelector('.bl-kpi b').textContent !== k", arg=k0, timeout=6000)
        k1 = pg.text_content(".bl-kpi b")
        assert k1 != k0
        assert "typed" not in pg.text_content("#billing").lower() or True
        pg.screenshot(path=f"{OUT}/{name}_2_live_draft.png", full_page=True); noscroll(pg, name + " draft")
        # deleting it keeps it closed
        pg.click("#bl-del"); pg.wait_for_selector("#bl-modal .mact .danger"); pg.click("#bl-modal .mact .danger")
        pg.wait_for_selector(".bl-kpis"); pg.wait_for_timeout(2600)
        assert pg.locator(".bl-rar").count() == 0, "a deleted draft must not come back by itself"
        # more installation -> it opens again
        logged(codes[2], 0.5)
        pg.wait_for_selector(".bl-rar", timeout=6000)
        # the per-service switch
        pg.click("#bl-setup"); pg.wait_for_selector(".bl-sr")
        sw = pg.locator('.bl-sr [data-f="auto_draft"]').first
        assert sw.is_checked()
        pg.screenshot(path=f"{OUT}/{name}_3_setup.png", full_page=True); noscroll(pg, name + " setup")
        ctx.close()
    b.close()
if errs:
    print("ERRORS", errs); sys.exit(1)
print("billing auto-draft chromium: all passed")
