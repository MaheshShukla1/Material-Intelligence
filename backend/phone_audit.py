"""Phone audit of the Billing screens (real backend, real Thoth BOQ). For each screen at each phone width: sideways overflow, content cut off,
tap targets, tiny text, inputs that make iOS zoom, modals that do not fit. Screenshots at 390. env: ROOT, BASE, OUT."""
import os, sys, json, io, urllib.parse as up, urllib.request as ur
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_billing_stockinbox_chromium.py")).read()
exec(src.split("with sync_playwright() as p:")[0])
from playwright.sync_api import sync_playwright
import pandas as pd
U = up.quote(PW)
CHECK = r"""
(() => {
  const vw = document.documentElement.clientWidth, vh = window.innerHeight;
  const out = {docOverflow: document.documentElement.scrollWidth - vw, cut: [], off: [], small: [], tiny: [], zoom: [], modal: null};
  const vis = (el) => { const r = el.getBoundingClientRect(), cs = getComputedStyle(el); return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && cs.opacity !== '0'; };
  const sig = (el) => { let s = el.tagName.toLowerCase(); if (el.id) s += '#' + el.id; else if (el.className && typeof el.className === 'string') s += '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.'); const t = (el.textContent || el.value || el.placeholder || el.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ').slice(0, 28); return s + (t ? ' "' + t + '"' : ''); };
  const clipper = (el) => { for (let p = el.parentElement; p && p !== document.documentElement; p = p.parentElement) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll') return {p, kind: 'scroll'}; if (o === 'hidden') return {p, kind: 'hidden'}; } return null; };
  const roots = [document.querySelector('#billing'), document.querySelector('#bl-modal')].filter(Boolean);
  const modal = document.querySelector('#bl-modal');
  const inModal = !!modal && getComputedStyle(modal).display !== 'none' && modal.getBoundingClientRect().width > 0;
  const scope = inModal ? [modal] : roots.filter((r) => r.id !== 'bl-modal');
  const seen = new Set();
  for (const root of scope) for (const el of root.querySelectorAll('*')) {
    if (!vis(el)) continue;
    const r = el.getBoundingClientRect(), c = clipper(el);
    if (r.right > vw + 1 || r.left < -1) {
      if (!c || c.kind !== 'scroll') { const k = sig(el); if (!seen.has('o' + k)) { seen.add('o' + k); out.off.push(k + ' right=' + Math.round(r.right)); } }
    }
    if (c && c.kind === 'hidden') { const pr = c.p.getBoundingClientRect(); if (r.right > pr.right + 2 && r.width < 4000) { const k = sig(el); if (!seen.has('c' + k)) { seen.add('c' + k); out.cut.push(k + ' over ' + sig(c.p)); } } }
    if (el.matches('button,a[href],select,input:not([type=hidden]):not([type=file]),[role=button],textarea')) {
      let h = r.height, w = r.width, t = el;
      if (el.type === 'checkbox' || el.type === 'radio') { const l = el.closest('label'); if (l) { const lr = l.getBoundingClientRect(); h = Math.max(h, lr.height); w = Math.max(w, lr.width); } }
      if (h < 44 || w < 44) { const k = sig(el); if (!seen.has('s' + k)) { seen.add('s' + k); out.small.push(k + ' ' + Math.round(w) + 'x' + Math.round(h)); } }
      if (el.matches('input,select,textarea')) { const fs = parseFloat(getComputedStyle(el).fontSize); if (fs < 16) { const k = sig(el); if (!seen.has('z' + k)) { seen.add('z' + k); out.zoom.push(k + ' ' + fs + 'px'); } } }
    }
    const direct = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim().length > 1);
    if (direct) { const fs = parseFloat(getComputedStyle(el).fontSize); if (fs < 12) { const k = sig(el); if (!seen.has('t' + k)) { seen.add('t' + k); out.tiny.push(k + ' ' + fs + 'px'); } } }
  }
  if (inModal) { const box = modal.querySelector('.bl-mbox, .bl-mcard, [class*=mbox], [class*=modal-box]') || modal.firstElementChild; const r = box.getBoundingClientRect(); const btns = [...modal.querySelectorAll('.mact .btn')].map((b) => b.getBoundingClientRect()); out.modal = {top: Math.round(r.top), bottom: Math.round(r.bottom), vh, w: Math.round(r.width), vw, actionsVisible: btns.every((b) => b.bottom <= vh + 1 && b.top >= 0), fits: r.top >= -1 && r.bottom <= vh + 1}; }
  return out;
})()
"""
FINDINGS = {}
def audit(pg, label, w, shot=False):
    pg.wait_for_timeout(250)
    r = pg.evaluate(CHECK)
    if shot: pg.screenshot(path=f"{OUT}/pa_{label}.png", full_page=True)
    FINDINGS.setdefault(label, {})[w] = r
    return r

def seed():
    reset()                                                  # register, packages, setup, links (from the stock inbox test)
    post(P + "/setup", {"service": PW, "mode": "separate", "supply_pct": 75, "install_pct": 15, "supply_source": "challan"})
    # a stock delivery accepted, then the register changes -> a "Register now shows" review
    ib = get(f"{P}/stock-inbox/{U}")
    sw = next(e for e in ib["entries"] if "SWITCH" in e["material"])
    post(f"{P}/stock-inbox/{U}/accept", {"items": [{"key": sw["key"], "no": "1188"}]})
    rd = os.path.join(ROOT, "data", "runs", "r_stockinbox"); df = pd.read_parquet(os.path.join(rd, "daily.parquet"))
    df.loc[df.material == "SWITCH 6A MODULE BOX", "qty_in"] = 4.0; df.to_parquet(os.path.join(rd, "daily.parquet"))
    # a typed challan of ordinary material (manual line) is not mixed with wire lines: use a different line
    post(f"{P}/challans/{U}", {"no": "875", "date": "2026-10-10", "lines": [{"item_code": "1.1.13", "qty": 10}]})
    for no, dte, cols in (("1207", "2026-03-28", dict(RED=300, YELLOW=600, BLUE=300)), ("615", "2026-07-07", dict(RED=3300, YELLOW=3000, BLUE=2700)), ("617", "2026-07-16", dict(RED=3900, YELLOW=3600, BLUE=2400))):
        post(f"{P}/wire/{U}/challans", {"no": no, "date": dte, "ra": 1, "sizes": [{"size": "1.5", "colours": cols}, {"size": "2.5", "colours": {"RED": 300, "YELLOW": 300, "BLUE": 300}}]})
    post(f"{P}/wire/{U}/alloc", {"rows": [{"item_code": "1.1.5", "size": "1.5", "metres": 20100}]}, "PUT")
    return post(P + "/ra", {"service": PW, "series": "supply"})["id"]

def goto_billing(pg):
    pg.goto(BASE); pg.wait_for_timeout(700)
    pg.click('.spnav button[data-v="billing"]'); pg.wait_for_selector(".bl-kpis")
def close_modal(pg):
    if pg.locator("#bl-modal .mact .btn:has-text('Cancel')").count(): pg.click("#bl-modal .mact .btn:has-text('Cancel')")
    elif pg.locator("#bl-modal .mact .btn:has-text('Close')").count(): pg.click("#bl-modal .mact .btn:has-text('Close')")
    pg.wait_for_timeout(150)

WIDTHS = [(320, 640), (360, 740), (390, 844), (412, 915)]
with sync_playwright() as p:
    b = p.chromium.launch()
    for (w, h) in WIDTHS:
        ra = seed()
        # invoice workbook of the draft, to import back
        xl = ur.urlopen(BASE + P + f"/invoice/{ra}").read(); xp = os.path.join(OUT, "pa_invoice.xlsx"); open(xp, "wb").write(xl)
        ctx = b.new_context(viewport={"width": w, "height": h}, has_touch=True, is_mobile=True, device_scale_factor=2)
        pg = ctx.new_page(); pg.on("pageerror", lambda e, ww=w: errs.append((ww, str(e))))
        shot = (w == 390)
        goto_billing(pg); audit(pg, "01_home", w, shot)
        pg.click("#bl-dl"); pg.wait_for_selector("#bl-modal .bl-dl"); audit(pg, "02_download_dialog(existing)", w, shot); close_modal(pg)
        pg.click(f'.bl-rar[data-ra="{ra}"]'); pg.wait_for_selector("#bl-dl"); audit(pg, "03_ra_draft", w, shot)
        pg.click("#bl-tochallans"); pg.wait_for_selector("#bl-ib .bl-ibr"); audit(pg, "04_challans_inbox", w, shot)
        pg.fill("#bl-ibq", "gi"); audit(pg, "05_inbox_search", w); pg.fill("#bl-ibq", "")
        pg.click("#bl-chadd"); pg.wait_for_selector("#bl-chmenu:not([hidden])"); audit(pg, "06_add_menu", w, shot)
        pg.click("#bl-chfrom")
        pg.locator("#bl-iblist .bl-ibr").first.locator("[data-ibadd],[data-iblink]").first.scroll_into_view_if_needed()
        # open a challan row (accepted, with register changed) and the typed + wire rows
        for r_ in pg.locator(".bl-ch").all(): r_.scroll_into_view_if_needed(); r_.click()
        pg.wait_for_timeout(200); audit(pg, "07_challans_list_expanded", w, shot)
        if pg.locator("[data-ibreview],[data-review]").count():
            pg.locator("[data-ibreview],[data-review]").first.click(); pg.wait_for_selector("#bl-modal"); audit(pg, "08_review_modal", w, shot); close_modal(pg)
        pg.click("#bl-chimp"); pg.wait_for_selector("#bl-file"); audit(pg, "09_import_empty", w, shot)
        pg.set_input_files("#bl-file", xp); pg.wait_for_selector("#bl-impres .bl-imp", timeout=15000); audit(pg, "10_import_preview", w, shot); close_modal(pg)
        pg.locator("[data-wdel]").first.scroll_into_view_if_needed(); pg.locator("[data-wdel]").first.click(); pg.wait_for_selector("#bl-modal"); audit(pg, "11_wire_delete_confirm", w, shot); close_modal(pg)
        pg.click("#bl-chwire") if pg.locator("#bl-chwire").count() else pg.locator("[data-towire]").first.click()
        pg.wait_for_selector(".bl-wtab"); audit(pg, "12_wire_typed", w, shot)
        pg.locator("[data-wch]").first.click(); pg.wait_for_selector("#bl-modal"); audit(pg, "13_wire_challan_edit", w, shot); close_modal(pg)
        # switching with metres already given is refused by the server (finding), so clear them first for the stock screens
        FINDINGS.setdefault("14x_switch_with_metres_given", {})[w] = {"toast": None}
        pg.locator('[data-wsrc="stock"]').click(); pg.wait_for_selector("#bl-modal .mact"); pg.click("#bl-modal .mact .btn:has-text('Switch')"); pg.wait_for_timeout(900)
        FINDINGS["14x_switch_with_metres_given"][w]["toast"] = pg.locator("#bl-toast").inner_text() if pg.locator("#bl-toast").count() else None
        FINDINGS["14x_switch_with_metres_given"][w]["still_typed"] = pg.locator(".bl-segb.on[data-wsrc='typed']").count() == 1
        FINDINGS["14x_switch_with_metres_given"][w]["modal_still_open"] = pg.locator("#bl-modal .mact").count() > 0
        if pg.locator("#bl-modal .mact").count(): close_modal(pg)
        if shot: pg.screenshot(path=f"{OUT}/pa_14x_switch_error.png")
        post(f"{P}/wire/{U}/alloc", {"rows": []}, "PUT")
        pg.click("#bl-wback"); pg.wait_for_selector("#bl-chwire"); pg.click("#bl-chwire"); pg.wait_for_selector(".bl-wtab")
        pg.locator('[data-wsrc="stock"]').click(); pg.wait_for_selector("#bl-modal .mact"); audit(pg, "14a_wire_switch_confirm", w, shot)
        pg.click("#bl-modal .mact .btn:has-text('Switch')"); pg.wait_for_selector(".bl-wmt"); audit(pg, "14_wire_stock_confirm", w, shot)
        pg.click("#bl-wmconf"); pg.wait_for_function("document.querySelectorAll('.bl-wtab thead [data-wch]').length >= 3"); audit(pg, "15_wire_stock_matrix", w, shot)
        pg.fill("#bl-wfind", "1.1.3"); pg.wait_for_selector('#bl-wfound .bl-hit[data-c="1.1.3"]'); audit(pg, "16_wire_line_search", w, shot)
        pg.click('#bl-wfound .bl-hit[data-c="1.1.3"]'); pg.wait_for_timeout(200); audit(pg, "17_wire_line_block", w, shot)
        ctx.close()
    b.close()
json.dump(FINDINGS, open(os.path.join(OUT, "pa_findings.json"), "w"), indent=1)
print("errors:", errs[:3])
print("done", len(FINDINGS), "screens")
