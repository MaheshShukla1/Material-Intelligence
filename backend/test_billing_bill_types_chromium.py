import sys, json, os
from playwright.sync_api import sync_playwright
import urllib.request as u
"""Chromium test (desktop 1366 + phone 390) of the per-RA bill type, the unlocked 'Usually billed as' column and the
company-details prefill, against a SCRATCH copy of the app (real Thoth BOQ). Drives the real UI and the real backend.
env: BASE (default http://localhost:8765), ROOT (scratch app root), OUT (screenshots)."""
BASE = os.environ.get("BASE", "http://localhost:8765")
ROOT = os.environ["ROOT"]
OUT = os.environ.get("OUT", "/tmp/billing_types_shots")
os.makedirs(OUT, exist_ok=True)
P = "/api/billing/thoth-mall"
errs = []
CO = {"contractor": "Vedant Engitech And Infrastructure Pvt. Ltd.", "supplier_gstin": "24AADCV2799M1ZP", "supplier_state": "GUJARAT",
      "supplier_state_code": "24", "supplier_pan": "AADCV2799M", "supplier_category": "MEPF"}
def post(path, body):
    return json.load(u.urlopen(u.Request(BASE + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})))
def get(path):
    return json.load(u.urlopen(BASE + path))
def reset():
    f = os.path.join(ROOT, "data", "projects", "thoth-mall", "billing.json")
    if os.path.exists(f): os.remove(f)
    json.dump({"fields": CO, "from": "hyatt-hotel"}, open(os.path.join(ROOT, "data", "billing_profile.json"), "w"))
    post(P + "/setup", {"service": "HVAC", "mode": "together", "supply_pct": 75, "install_pct": 25, "supply_source": "challan", "auto_draft": False})
    post(P + "/setup", {"service": "Fire", "mode": "separate", "supply_pct": 70, "install_pct": 30, "auto_draft": False})
    post(P + "/ra", {"service": "Fire", "series": "supply"})            # Fire has an RA: its mode used to lock here
def noscroll(pg, label):
    assert 0 == pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth"), label + ": page scrolls sideways"
def fits(pg, label):
    assert pg.evaluate("(()=>{const m=document.querySelector('#bl-modal .card');const r=m.getBoundingClientRect();return m.scrollWidth<=m.clientWidth+1 && r.left>=0 && r.right<=innerWidth+1 && r.top>=0 && r.bottom<=innerHeight+1})()"), label + ": the dialog does not fit the screen / overflows sideways"
def inview(pg, sel, label):
    minh = 44 if "phone" in label else 30                                  # phone: a real touch target
    assert pg.evaluate("([s,m])=>{const e=document.querySelector(s);if(!e)return false;const r=e.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight&&r.width>=44&&r.height>=m}", [sel, minh]), label + ": button missing, off-screen or too small"
with sync_playwright() as p:
    b = p.chromium.launch()
    for name, (w, h) in {"desk": (1366, 900), "phone": (390, 844)}.items():
        reset()
        ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=1, has_touch=(name == "phone"), is_mobile=(name == "phone"))
        pg = ctx.new_page()
        pg.on("pageerror", lambda e, n=name: errs.append(("pageerror", n, str(e))))
        pg.on("console", lambda m, n=name: errs.append(("console", n, m.text)) if m.type == "error" else None)
        pg.goto(BASE); pg.wait_for_timeout(800)
        pg.click('.spnav button[data-v="billing"]'); pg.wait_for_selector(".bl-kpis")
        pg.screenshot(path=f"{OUT}/{name}_1_home.png", full_page=True); noscroll(pg, name + " home")
        assert "Usually one RA" in pg.inner_text("#billing") and "Usually separate RAs" in pg.inner_text("#billing")
        # ---- New RA: every bill type, usual one pre-selected
        pg.click("#bl-new"); pg.wait_for_selector("#bl-modal .bl-opt")
        types = pg.eval_on_selector_all("#bl-modal .bl-opt", "els=>els.map(e=>e.dataset.series)")
        assert types == ["supply", "install", "together"], types
        first = pg.inner_text("#bl-modal .bl-pill.on")
        sel = pg.eval_on_selector("#bl-modal .bl-opt.on", "e=>e.dataset.series")
        assert (first, sel) == ("Fire", "install"), (first, sel)               # first set service; its Supply draft is open, so the first free usual type
        pg.screenshot(path=f"{OUT}/{name}_2_newra_{first}.png"); fits(pg, name + " new RA")
        pg.click('#bl-modal [data-svc="HVAC"]'); pg.wait_for_timeout(100)
        assert pg.eval_on_selector("#bl-modal .bl-opt.on", "e=>e.dataset.series") == "together"
        pg.click('#bl-modal .bl-opt[data-series="supply"]'); pg.wait_for_timeout(100)
        assert pg.eval_on_selector("#bl-modal .bl-opt.on", "e=>e.dataset.series") == "supply"
        pg.screenshot(path=f"{OUT}/{name}_3_newra_hvac_supply.png"); fits(pg, name + " new RA hvac")
        pg.click('#bl-modal [data-svc="Fire"]'); pg.wait_for_timeout(100)
        assert pg.eval_on_selector("#bl-modal .bl-opt.on", "e=>e.dataset.series") == "install"
        assert "off" in pg.get_attribute('#bl-modal .bl-opt[data-series="together"]', "class")
        pg.screenshot(path=f"{OUT}/{name}_4_newra_fire.png"); fits(pg, name + " new RA fire")
        inview(pg, "#bl-modal .mact .primary", name + " Create draft")
        # create an HVAC Supply-only RA on a service that is 'usually together'
        pg.click('#bl-modal [data-svc="HVAC"]'); pg.click('#bl-modal .bl-opt[data-series="supply"]')
        pg.click("#bl-modal .mact .primary"); pg.wait_for_selector(".bl-lines")
        ras = {(r["service"], r["series"], r["no"]) for r in get(P)["ras"]}
        assert ("HVAC", "supply", 1) in ras and ("Fire", "supply", 1) in ras, ras
        assert "Supply" in pg.inner_text("#billing") and "RA 1" in pg.inner_text("#billing")
        # ---- Billing setup: 'Usually billed as' is not locked even for Fire (has an RA)
        pg.click("#bl-back"); pg.wait_for_selector(".bl-kpis"); pg.click("#bl-setup"); pg.wait_for_selector(".bl-sets")
        assert pg.locator('.bl-sr [data-f="mode"][disabled]').count() == 0
        pg.screenshot(path=f"{OUT}/{name}_5_setup.png", full_page=True); noscroll(pg, name + " setup")
        fire = pg.locator(".bl-sr", has_text="Fire")
        fire.locator('[data-f="mode"]').select_option("together"); fire.locator("[data-save]").click(); pg.wait_for_selector(".bl-sets"); pg.wait_for_timeout(200)
        assert [x["mode"] for x in get(P + "/setup")["services"] if x["service"] == "Fire"] == ["together"]
        fire = pg.locator(".bl-sr", has_text="Fire")
        fire.locator('[data-f="mode"]').select_option("separate"); fire.locator("[data-save]").click(); pg.wait_for_timeout(300)
        # ---- company details: offered on the card, prefilled in the form, clearable
        card = pg.inner_text(".bl-metac")
        assert "Your company details from Hyatt Hotel are ready" in card and CO["contractor"] in card, card
        assert get(P + "/setup")["meta"].get("contractor") in (None, "")                           # offered, NOT applied
        pg.screenshot(path=f"{OUT}/{name}_6_setup_card.png", full_page=True)
        pg.click("#bl-idet"); pg.wait_for_selector("#bl-modal .bl-idet"); pg.wait_for_timeout(200)
        assert pg.input_value('#bl-modal [data-m="contractor"]') == CO["contractor"]
        assert pg.input_value('#bl-modal [data-m="client"]') == ""
        assert pg.locator("#bl-inote").count() == 1
        pg.screenshot(path=f"{OUT}/{name}_7_details_prefilled.png"); fits(pg, name + " details")
        inview(pg, "#bl-isave", name + " Save"); inview(pg, "#bl-iclrall", name + " Clear all")
        pg.click("#bl-iclrco"); pg.wait_for_timeout(100)
        assert pg.input_value('#bl-modal [data-m="contractor"]') == "" and pg.locator("#bl-inote").count() == 0
        pg.screenshot(path=f"{OUT}/{name}_8_details_cleared.png")
        pg.click("#bl-isave"); pg.wait_for_selector(".bl-sets"); pg.wait_for_timeout(300)
        m = get(P + "/setup")
        assert m["meta"].get("contractor") == "" and m["profile"] is None, (m["meta"].get("contractor"), m["profile"])   # starts empty and is not offered again
        assert "are ready:" not in pg.inner_text(".bl-metac")
        # ---- a fresh project would be offered it again: simulate by wiping this project's flag, then 'Use them here'
        f = os.path.join(ROOT, "data", "projects", "thoth-mall", "billing.json")
        j = json.load(open(f)); j["meta"].pop("profile_skip", None); j["meta"]["contractor"] = ""; json.dump(j, open(f, "w"))
        pg.click("#bl-back"); pg.wait_for_selector(".bl-kpis"); pg.click("#bl-setup"); pg.wait_for_selector(".bl-sets")
        pg.click("#bl-iuse"); pg.wait_for_timeout(400)
        assert get(P + "/setup")["meta"]["contractor"] == CO["contractor"]
        assert "are ready:" not in pg.inner_text(".bl-metac")
        pg.screenshot(path=f"{OUT}/{name}_9_used.png", full_page=True); noscroll(pg, name + " setup after use")
        ctx.close()
    b.close()
assert not errs, errs
print("billing bill types chromium test: OK")
