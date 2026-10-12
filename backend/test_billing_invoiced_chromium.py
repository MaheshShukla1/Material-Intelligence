import sys, json, os, re
from playwright.sync_api import sync_playwright
import urllib.request as u
"""Chromium test (desktop 1366 + phone 390): the user's real situation -- a Supply + Installation RA whose installation has
no Site Progress yet. A frozen RA is called 'Invoiced', the RA screen explains Installation Rs 0, the Download dialog flags it,
and the downloaded file has a clean name (only a draft carries _DRAFT).
env: BASE (default http://localhost:8765), ROOT (scratch app root), OUT (screenshots)."""
BASE = os.environ.get("BASE", "http://localhost:8765")
ROOT = os.environ["ROOT"]
OUT = os.environ.get("OUT", "/tmp/billing_invoiced_shots")
os.makedirs(OUT, exist_ok=True)
P = "/api/billing/thoth-mall"
errs = []
def post(path, body):
    return json.load(u.urlopen(u.Request(BASE + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})))
def get(path):
    return json.load(u.urlopen(BASE + path))
def reset():
    f = os.path.join(ROOT, "data", "projects", "thoth-mall", "billing.json")
    if os.path.exists(f): os.remove(f)
    post(P + "/setup", {"service": "HVAC", "mode": "together", "supply_pct": 75, "install_pct": 25, "supply_source": "challan", "auto_draft": False})
    allc = [l["item_code"] for l in get(P + "/lines/HVAC")["lines"]]
    for c in allc[:3]:                                              # a clean slate: no installation logged in Site Progress (this test only touches these lines)
        post("/api/siteprogress/thoth-mall/progress/item", {"service": "HVAC", "item_code": c, "frac": 0})
    codes = allc[:3]
    post(P + "/challans/HVAC", {"no": "614", "date": "2026-07-04", "lines": [{"item_code": c, "qty": 2} for c in codes]})
    ra = post(P + "/ra", {"service": "HVAC", "series": "together"})["id"]
    return ra
def noscroll(pg, label):
    assert 0 == pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth"), label + ": page scrolls sideways"
with sync_playwright() as p:
    b = p.chromium.launch()
    for name, (w, h) in {"desk": (1366, 900), "phone": (390, 844)}.items():
        ra = reset()
        ctx = b.new_context(viewport={"width": w, "height": h}, has_touch=(name == "phone"), is_mobile=(name == "phone"), accept_downloads=True)
        pg = ctx.new_page()
        pg.on("pageerror", lambda e, n=name: errs.append(("pageerror", n, str(e))))
        pg.on("console", lambda m, n=name: errs.append(("console", n, m.text)) if m.type == "error" else None)
        pg.goto(BASE); pg.wait_for_timeout(800)
        pg.click('.spnav button[data-v="billing"]'); pg.wait_for_selector(".bl-rar")
        # ---- draft: explains the zero, says Draft
        pg.click(".bl-rar"); pg.wait_for_selector("#bl-noinst")
        t = pg.inner_text("#bl-noinst")
        assert "no installation progress is logged in Site Progress" in t, t
        assert pg.locator("#bl-lock").inner_text().strip() == "Mark as invoiced"
        pg.screenshot(path=f"{OUT}/{name}_1_draft.png", full_page=True); noscroll(pg, name + " draft")
        ov = pg.evaluate("(()=>{const c=document.querySelector('.bl-grid').parentElement;return c.scrollWidth-c.clientWidth})()")
        if name == "phone":
            assert ov == 0, "phone: the annexure must fit the screen (one card per group), it scrolls by " + str(ov)
            assert pg.locator(".bl-gr").first.locator("[data-l='Supply this bill']").is_visible()
        else:
            assert pg.locator(".bl-gh").is_visible()
        # ---- mark as invoiced
        pg.click("#bl-lock"); pg.wait_for_selector("#bl-modal .mact .primary")
        assert "Mark HVAC" in pg.inner_text("#bl-modal") and "as invoiced" in pg.inner_text("#bl-modal")
        pg.click("#bl-modal .mact .primary"); pg.wait_for_selector("#bl-reopen")
        body = pg.text_content("#billing")
        assert "Invoiced · cut-off" in body and "Locked" not in body, body[:200]
        assert "was invoiced" in pg.inner_text("#bl-noinst")
        pg.screenshot(path=f"{OUT}/{name}_2_invoiced.png", full_page=True); noscroll(pg, name + " invoiced")
        # ---- home
        pg.click("#bl-back"); pg.wait_for_selector(".bl-rar")
        home = pg.text_content("#billing")
        assert "Invoiced so far" in home and "RA invoiced" in home and "Locked" not in home, home[:300]
        assert pg.locator(".bl-rar .bl-chip").first.inner_text() == "Invoiced"
        pg.screenshot(path=f"{OUT}/{name}_3_home.png", full_page=True); noscroll(pg, name + " home")
        # ---- Download dialog + real file name
        pg.click("#bl-dl"); pg.wait_for_selector("#bl-modal .bl-dl"); pg.wait_for_timeout(200)
        dlg = pg.text_content("#bl-modal")
        assert "Installation ₹0" in dlg and "Locked" not in dlg and "LOCKED" not in dlg, dlg[:400]
        names = [re.sub(r"[​]", "", x) for x in pg.locator("#bl-modal .bl-fi b").all_inner_texts()]
        assert names == ["thoth-mall_HVAC_Supply+Installation_RA-01.xlsx"], names
        pg.screenshot(path=f"{OUT}/{name}_4_download.png"); noscroll(pg, name + " dialog")
        with pg.expect_download() as dl:
            pg.click("#bl-modal .mact .primary, #bl-modal button:has-text('Download .xlsx')")
        assert dl.value.suggested_filename == "thoth-mall_HVAC_Supply+Installation_RA-01.xlsx", dl.value.suggested_filename
        ctx.close()
    b.close()
if errs:
    print("ERRORS", errs); sys.exit(1)
print("billing invoiced chromium: all passed")
