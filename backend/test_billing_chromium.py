import sys, json, re, os
from playwright.sync_api import sync_playwright
"""Chromium test of the Billing tab at desktop (1366) and phone (390) widths, against a
SCRATCH copy of the app seeded by seed_billing_case.py (it creates and locks RAs).
env: BASE (default http://localhost:8765), ROOT (scratch app root, to reset billing.json),
     OUT (screenshot folder)."""
import subprocess
BASE=os.environ.get("BASE","http://localhost:8765")
ROOT=os.environ["ROOT"]
OUT=os.environ.get("OUT","/tmp/billing_shots")
os.makedirs(OUT,exist_ok=True)
errs=[]
def reset():
    import urllib.request as u
    f=os.path.join(ROOT,"data","projects","thoth-mall","billing.json")
    if os.path.exists(f): os.remove(f)
    for svc,mode,s,i in (("Fire","separate",70,30),("HVAC","separate",75,None),("Electrical","together",60,40)):
        u.urlopen(u.Request(BASE+"/api/billing/thoth-mall/setup",data=json.dumps({"service":svc,"mode":mode,"supply_pct":s,"install_pct":i}).encode(),headers={"Content-Type":"application/json"}))
with sync_playwright() as p:
    b=p.chromium.launch()
    import subprocess
    for name,(w,h) in {"desk":(1366,900),"phone":(390,844)}.items():
        reset()
        ctx=b.new_context(viewport={"width":w,"height":h}, device_scale_factor=1, has_touch=(name=="phone"), is_mobile=(name=="phone"))
        pg=ctx.new_page(); pg.on("pageerror",lambda e,n=name:errs.append(("pageerror",n,str(e)))); pg.on("console",lambda m,n=name: errs.append(("console",n,m.text)) if m.type=="error" else None)
        pg.goto(BASE); pg.wait_for_timeout(800)
        pg.click('.spnav button[data-v="billing"]'); pg.wait_for_selector(".bl-kpis")
        pg.screenshot(path=f"{OUT}/{name}_1_home.png",full_page=True)
        assert 0==pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth"), name+": page scrolls sideways"
        pg.click("#bl-new"); pg.wait_for_selector("#bl-modal .bl-opt"); pg.screenshot(path=f"{OUT}/{name}_2_new.png")
        pg.click('#bl-modal [data-svc="Fire"]'); pg.click('#bl-modal .bl-opt[data-series="supply"]'); pg.click("#bl-modal .mact .primary"); pg.wait_for_selector(".bl-lines")
        pg.wait_for_timeout(300)
        pg.screenshot(path=f"{OUT}/{name}_3_ra.png",full_page=True)
        assert 0==pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth"), name+": RA page scrolls sideways"; assert pg.locator(".bl-lr").count()>0
        pg.locator(".bl-lr .d").first.click(); pg.wait_for_timeout(150); pg.screenshot(path=f"{OUT}/{name}_4_expand.png",full_page=True)
        pg.locator("[data-edit]").first.click(); pg.wait_for_selector("#bl-q"); pg.fill("#bl-q","1"); pg.fill("#bl-r","gate pass 7"); pg.screenshot(path=f"{OUT}/{name}_5_edit.png")
        pg.click("#bl-modal .mact .primary"); pg.wait_for_timeout(500)
        pg.click("#bl-lock"); pg.wait_for_selector("#bl-modal .bl-lockbox"); pg.screenshot(path=f"{OUT}/{name}_6_lock.png")
        pg.click("#bl-modal .mact .primary"); pg.wait_for_selector("#bl-reopen"); pg.screenshot(path=f"{OUT}/{name}_7_locked.png",full_page=True)
        pg.click("#bl-back"); pg.wait_for_selector(".bl-kpis"); pg.click("#bl-setup"); pg.wait_for_selector(".bl-sets"); pg.screenshot(path=f"{OUT}/{name}_8_setup.png",full_page=True)
        assert 0==pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth"), name+": page scrolls sideways"
        pg.locator("[data-groups]").nth(1).click(); pg.wait_for_selector(".bl-gt"); pg.screenshot(path=f"{OUT}/{name}_9_groups.png"); pg.click("#bl-mx")
        pg.click('.spnav button[data-v="forecast"]'); pg.wait_for_timeout(300)
        assert pg.evaluate("document.getElementById('billing').hidden")
        ctx.close()
    b.close()
assert not errs, errs
print("billing chromium test: OK")
