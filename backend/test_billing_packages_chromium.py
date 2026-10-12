import sys, json, os, io
from playwright.sync_api import sync_playwright
import urllib.request as u
"""Chromium test (desktop 1366 + phone 390), real backend and the real Thoth BOQ: Electrical is billed under two Work Orders.
The person splits Electrical in Billing setup (UI), an already invoiced RA moves into the Work Order it belongs to, the other Work Order gets
its own RA 1 from Site Progress, the overall annexure adds both up, and its Excel downloads. Phone: nothing scrolls sideways,
buttons are finger-sized.
env: BASE (default http://localhost:8765), ROOT (scratch app root), OUT (screenshots)."""
BASE = os.environ.get("BASE", "http://localhost:8765")
ROOT = os.environ["ROOT"]
OUT = os.environ.get("OUT", "/tmp/billing_packages_shots")
os.makedirs(OUT, exist_ok=True)
P = "/api/billing/thoth-mall"
EX, PW = "Electrical › Excl. point wiring", "Electrical › Only point wiring"
errs = []
def post(path, body):
    return json.load(u.urlopen(u.Request(BASE + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})))
def get(path):
    return json.load(u.urlopen(BASE + path))
def prog(code, frac):
    post("/api/siteprogress/thoth-mall/progress/item", {"service": "Electrical", "item_code": code, "frac": frac})
def noscroll(pg, label):
    assert 0 == pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth"), label + ": page scrolls sideways"
def fingers(pg, sel, label, min_h=40):
    for h in pg.eval_on_selector_all(sel, "els => els.filter(e => e.offsetParent).map(e => Math.round(e.getBoundingClientRect().height))"):
        assert h >= min_h, f"{label}: control only {h}px tall ({sel})"
def reset():
    f = os.path.join(ROOT, "data", "projects", "thoth-mall", "billing.json")
    if os.path.exists(f): os.remove(f)
    post(P + "/setup", {"service": "Electrical", "mode": "separate", "supply_pct": 75, "install_pct": 15, "supply_source": "challan"})
    g = get(P + "/packages/Electrical")["groups"]
    pw = next(x for x in g if x["name"].startswith("POINT WIRING")); ct = next(x for x in g if x["name"] == "Cable Tray")
    lines = get(P + "/lines/Electrical")["lines"]
    code_of = lambda ref_lo, ref_hi: None
    return g, pw, ct
with sync_playwright() as p:
    b = p.chromium.launch()
    for name, (w, h) in {"desk": (1366, 900), "phone": (390, 844)}.items():
        g, pw, ct = reset()
        # the point-wiring lines and one cable-tray line, by the sheet's own rows
        import importlib; sys.path.insert(0, ROOT)
        from backend import siteprogress as sp, billing
        d = sp._dir("thoth-mall")
        lines = billing._base_lines(d, "Electrical")
        pwl = [l for l in lines if pw["from"] <= l["src_row"] <= pw["to"]]
        ctl = [l for l in lines if ct["from"] <= l["src_row"] <= ct["to"]]
        for l in pwl + ctl: prog(l["item_code"], 0)
        # Electrical (still ONE Work Order) already has an invoiced Supply RA 1 made only of point-wiring lines
        post(P + "/challans/Electrical", {"no": "1207", "date": "2026-03-28", "lines": [{"item_code": pwl[0]["item_code"], "qty": min(2000, pwl[0]["boq_qty"])}]})
        ra = post(P + "/ra", {"service": "Electrical", "series": "supply"})["id"]
        post(P + f"/ra/{ra}/lock", {})
        invoiced = get(P + f"/ra/{ra}")["this_total"]
        assert invoiced > 0
        ctx = b.new_context(viewport={"width": w, "height": h}, has_touch=(name == "phone"), is_mobile=(name == "phone"), accept_downloads=True)
        ctx.add_init_script("window.__BL_LIVE_MS = 1500")
        pg = ctx.new_page()
        pg.on("pageerror", lambda e, n=name: errs.append(("pageerror", n, str(e))))
        pg.on("console", lambda m, n=name: errs.append(("console", n, m.text)) if m.type == "error" else None)
        pg.goto(BASE); pg.wait_for_timeout(800)
        pg.click('.spnav button[data-v="billing"]'); pg.wait_for_selector(".bl-kpis")
        assert pg.locator(".bl-wo").count() == 0
        # a plain service keeps its Overall annexure link but carries NO split link: splitting lives in Billing setup only
        assert pg.locator("[data-wo], .bl-svc >> text=Split into Work Orders").count() == 0
        assert pg.locator("[data-overall]").count() == pg.locator(".bl-svcs > .bl-svc").count() > 0
        pg.screenshot(path=f"{OUT}/{name}_1_home_before.png", full_page=True); noscroll(pg, name + " home before")
        # ---- split from the UI
        pg.click("#bl-setup"); pg.wait_for_selector("#bl-wosplit")
        assert pg.locator(".bl-sr [data-wo]").count() == 0
        if name == "phone": fingers(pg, "#bl-wosel, #bl-wosplit", "split card", 44)
        pg.select_option("#bl-wosel", "Electrical"); pg.click("#bl-wosplit"); pg.wait_for_selector("#bl-wobody [data-name]")
        names = pg.locator("#bl-wobody [data-name]")
        names.nth(0).fill("Excl. point wiring"); names.nth(1).fill("Only point wiring"); names.nth(1).press("Tab")
        gi = [i for i, x in enumerate(g) if x["name"].startswith("POINT WIRING")][0]
        pg.select_option(f'#bl-wobody [data-g="{gi}"]', "1")
        txt = pg.inner_text(".bl-wosum"); assert "Only point wiring" in txt and "1 group" in txt, txt
        pg.screenshot(path=f"{OUT}/{name}_2_workorders_modal.png"); noscroll(pg, name + " modal")
        if name == "phone": fingers(pg, "#bl-modal select, #bl-modal input[type=text], #bl-modal .mact .btn", "modal", 44)
        pg.click("#bl-wosave"); pg.wait_for_selector(".bl-sr:has-text('Only point wiring')")
        rows = pg.locator(".bl-sr b").all_inner_texts()
        assert any("Only point wiring" in t for t in rows) and any("Excl. point wiring" in t for t in rows), rows
        # payment term was carried over to both Work Orders
        st = get(P + "/setup")["services"]
        el = [x for x in st if x["base"] == "Electrical"]
        assert [x["package"] for x in el] == ["Excl. point wiring", "Only point wiring"] and all(x["supply_pct"] == 75 and x["install_pct"] == 15 for x in el), el
        pg.screenshot(path=f"{OUT}/{name}_3_setup.png", full_page=True)
        # ---- the invoiced RA 1 is now the Only point wiring RA 1; Excl. point wiring has none yet
        pg.click("#bl-back")
        pg.wait_for_selector(".bl-wo")
        assert pg.locator(".bl-wo").count() == 2
        tile = lambda n: pg.locator(".bl-wo", has=pg.locator("b", has_text=n)).first
        assert "Invoiced" in tile("Only point wiring").inner_text() and "RA 1" in tile("Only point wiring").inner_text()
        assert "No RA yet" in tile("Excl. point wiring").inner_text()
        # ---- installation on a cable-tray line: the Excl. Work Order gets ITS OWN RA 1 by itself (live refresh)
        prog(ctl[0]["item_code"], 0.5)
        pg.wait_for_function("document.querySelector('.bl-wo:nth-child(1)') && /Draft/.test(document.querySelector('.bl-wo:nth-child(1)').textContent)", timeout=8000)
        t1 = tile("Excl. point wiring").inner_text(); assert "RA 1" in t1 and "Draft" in t1, t1
        t2 = tile("Only point wiring").inner_text(); assert "RA 1" in t2 and "Invoiced" in t2, t2
        pg.screenshot(path=f"{OUT}/{name}_4_home_after.png", full_page=True); noscroll(pg, name + " home after")
        ras = get(P)["ras"]
        assert sorted((r["service"], r["no"], r["status"]) for r in ras) == sorted([(EX, 1, "draft"), (PW, 1, "locked")]), ras
        # ---- overall annexure
        pg.click('.bl-sph [data-overall="Electrical"]'); pg.wait_for_selector(".bl-wob")
        assert pg.locator(".bl-wob").count() == 2
        o = get(P + "/overall/Electrical")
        fmt = lambda v: "₹ " + format(round(v), ",")
        # Indian grouping check through the page itself: totals must match the API to the rupee
        body = pg.inner_text("#billing").replace(" ", " ")
        digits = lambda s: "".join(c for c in s if c.isdigit())
        kp = pg.locator(".bl-kpi b").all_inner_texts()
        assert digits(kp[1]) == str(round(o["totals"]["invoiced"])), (kp, o["totals"])
        assert digits(kp[2]) == str(round(o["totals"]["draft"])), (kp, o["totals"])
        assert abs(o["totals"]["invoiced"] - invoiced) < 1, (o["totals"], invoiced)
        hd = pg.locator(".bl-wob .bl-wohd").all_inner_texts()
        assert "Excl. point wiring" in hd[0] and "RA 1 · Draft" in hd[0], hd
        assert "Only point wiring" in hd[1] and "RA 1 · Invoiced" in hd[1], hd
        pg.screenshot(path=f"{OUT}/{name}_5_overall.png", full_page=True); noscroll(pg, name + " overall")
        if name == "phone":
            fingers(pg, ".bl-tgl button, #bl-ovdl, #bl-ovnew, .bl-woact .bl-link", "overall", 44)
            # the table is cards on a phone: the header row is gone, every figure is labelled
            assert not pg.locator(".bl-oh").first.is_visible()
            assert pg.locator(".bl-or [data-l]").first.evaluate("e => getComputedStyle(e, '::before').content") not in ("none", "normal")
        # toggle Invoiced only -> draft vanishes
        pg.click('[data-ovm="inv"]'); pg.wait_for_timeout(100)
        assert "In draft" not in pg.inner_text(".bl-kpis")
        pg.click('[data-ovm="all"]')
        # ---- Excel
        with pg.expect_download() as dl:
            pg.click("#bl-ovdl")
        path = os.path.join(OUT, f"{name}_" + dl.value.suggested_filename); dl.value.save_as(path)
        import openpyxl
        wb = openpyxl.load_workbook(path)
        ws = wb["Overall annexure"]
        cells = [str(c.value) for r in ws.iter_rows() for c in r if c.value is not None]
        assert any("Work Order: Only point wiring" in t for t in cells) and any("Work Order: Excl. point wiring" in t for t in cells), cells[:40]
        assert any("Electrical, all Work Orders" in t for t in cells)
        assert dl.value.suggested_filename.endswith("overall_annexure.xlsx"), dl.value.suggested_filename
        # ---- each Work Order's own RA opens from the overall screen
        pg.locator(".bl-wob").nth(1).locator("[data-ra]").click()
        pg.wait_for_selector("#bl-toverall")
        assert "Only point wiring" in pg.inner_text("#billing h2") and "RA 1" in pg.inner_text("#billing h2"), pg.inner_text("#billing h2")
        pg.click("#bl-toverall"); pg.wait_for_selector(".bl-wob")
        pg.locator(".bl-wob").nth(0).locator("[data-ra]").click(); pg.wait_for_selector("#bl-toverall")
        assert "Excl. point wiring" in pg.inner_text("#billing h2") and "RA 1" in pg.inner_text("#billing h2"), pg.inner_text("#billing h2")
        pg.screenshot(path=f"{OUT}/{name}_6_ra.png", full_page=True); noscroll(pg, name + " ra")
        ctx.close()
    b.close()
if errs:
    print("ERRORS", errs); sys.exit(1)
print("billing packages chromium: all passed")
