// jsdom test: the Site Progress "Rebuild" button (wipes ALL recorded progress) is hidden until the owner unlocks it (secret hold / Ctrl+Shift+A;
// the admin password is asked only when the server has MI_ADMIN_PASSWORD). The lock itself is the server's (backend/test_admin_lock.py). Here: nobody sees Rebuild by default; a wrong password, or a server with no
// password set, changes nothing; the right one shows it for this tab only; Rebuild sends the password; a stale password locks it again.
// Payloads: the REAL Fire state of activity_delete_fixture.json.
import { JSDOM } from "jsdom";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
let pass = 0, fail = 0;
function ok(cond, msg) { if (cond) pass++; else { fail++; console.error("FAIL:", msg); } }
const FX = JSON.parse(fs.readFileSync(path.join(__dirname, "activity_delete_fixture.json"), "utf8"));
const clone = (x) => JSON.parse(JSON.stringify(x));
const flush = async (n = 8) => { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); };
const SL = FX.slug, SP = "/api/siteprogress/" + SL, PW = "Owner@123 ₹";

async function boot(opts) {
  opts = opts || {};
  const html = `<!doctype html><html><body>
  <header class="top" id="top"><div class="brand"><h1>Material intelligence</h1><p id="ctx"></p></div>
    <div class="topact"><button id="pick"></button><button id="sync"></button><button id="syncgo"></button><button id="home" hidden></button><input id="file" type="file" hidden></div></header>
  <main><section id="drop"></section><section id="busy" hidden></section><section id="report" hidden><div class="bar"><div class="tabs" id="svc"></div></div></section></main></body></html>`;
  const dom = new JSDOM(html, { url: "http://localhost/", runScripts: "outside-only", pretendToBeVisual: true });
  const { window } = dom; const calls = [], confirms = [];
  if (opts.stored) window.sessionStorage.setItem("mi_admin", opts.stored);
  window.confirm = (m) => { confirms.push(m); return true; };
  window.alert = () => {};
  const server = { pw: opts.serverPw === undefined ? PW : opts.serverPw };           // null = MI_ADMIN_PASSWORD not set on the server
  const sentOk = (h) => server.pw && decodeURIComponent((h || {})["X-Mi-Admin"] || "") === server.pw;
  window.fetch = async (url, o) => {
    const u = new URL(url, "http://localhost"), method = (o && o.method) || "GET", h = (o && o.headers) || {};
    let data = {}, status = 200;
    calls.push({ method, path: u.pathname, h });
    if (u.pathname === "/api/projects") data = FX.projects;
    else if (u.pathname === SP) data = clone(FX.project);
    else if (u.pathname === SP + "/service/Fire") data = clone(FX.view_with_empty);
    else if (u.pathname === SP + "/pnl/Fire") data = clone(FX.pnl);
    else if (u.pathname === SP + "/admin/check" || u.pathname === SP + "/structure/reset") {
      if (opts.broken) { status = 500; data = { detail: "boom" }; }
      else if (server.pw && !sentOk(h)) { status = 403; data = { detail: "Wrong admin password." }; }   // no password on the server = nothing to check
      else data = u.pathname.endsWith("/reset") ? { reset: true } : { ok: true, protected: !!server.pw };
    }
    return { ok: status < 400, status, json: async () => data, text: async () => JSON.stringify(data) };
  };
  window.eval(fs.readFileSync(path.join(__dirname, "siteprogress.js"), "utf8"));
  Object.defineProperty(window.document, "readyState", { value: "complete", configurable: true });
  window.document.dispatchEvent(new window.Event("DOMContentLoaded"));
  await flush();
  window.document.querySelector('.spnav button[data-v="siteprogress"]').click();
  await flush(25);
  return { window, d: window.document, calls, confirms };
}
const T = (el) => (el ? el.textContent : "").replace(/\s+/g, " ").trim();
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
const type = (w, d, v) => { const i = d.getElementById("sp-adminpw"); i.value = v; };
const unlock = async (w, d) => { click(w, d.getElementById("sp-modal-save")); await flush(15); };
// the two secret entries: press-and-hold the "Structure" heading, or Ctrl+Shift+A
const ptr = (w, d, type) => d.getElementById("sp-railttl").dispatchEvent(new w.Event(type, { bubbles: true }));
const hold = async (w, d, ms) => { ptr(w, d, "pointerdown"); await new Promise((r) => setTimeout(r, ms)); ptr(w, d, "pointerup"); await flush(); };
const key = async (w, d, o) => { const ev = new w.KeyboardEvent("keydown", Object.assign({ bubbles: true, cancelable: true, ctrlKey: true, shiftKey: true, key: "A", code: "KeyA" }, o || {})); d.dispatchEvent(ev); await flush(); return ev; };

async function main() {
  // ---- by default nobody sees Rebuild, no visible trace (secret entry: long-press or Ctrl+Shift+A)
  let { window, d, calls, confirms } = await boot();
  ok(!!d.getElementById("sp-addfloor"), "the Structure panel is there (+ Add stays for everyone)");
  ok(!d.getElementById("sp-rebuildstruct") && !/Rebuild/.test(T(d.querySelector(".sp-rail"))), "Rebuild is NOT shown by default");
  ok(!d.getElementById("sp-adminlock") && !/Admin/i.test(T(d.querySelector(".sp-rail"))), "NO 'Admin' link or any other trace is on screen for a normal user");
  ok(!d.getElementById("sp-adminrow") || d.getElementById("sp-adminrow").hidden, "the admin row is hidden");

  // ---- the entry is secret: a normal tap, a short press, other keys, and Ctrl+Shift+A with another view on screen all open nothing
  click(window, d.getElementById("sp-railttl")); await flush();
  ok(!d.getElementById("sp-modal"), "a normal tap on 'Structure' opens nothing (also 5 quick taps, the old idea)");
  for (let i = 0; i < 5; i++) click(window, d.getElementById("sp-railttl"));
  ok(!d.getElementById("sp-modal"), "5 quick taps open nothing");
  await hold(window, d, 600);
  ok(!d.getElementById("sp-modal"), "a short press (0.6 s) opens nothing");
  ptr(window, d, "pointerdown"); await new Promise((r) => setTimeout(r, 900)); ptr(window, d, "pointerleave"); await new Promise((r) => setTimeout(r, 900));
  ok(!d.getElementById("sp-modal"), "holding then sliding the finger off the heading cancels it");
  await key(window, d, { shiftKey: false }); await key(window, d, { ctrlKey: false }); await key(window, d, { altKey: true }); await key(window, d, { key: "B", code: "KeyB" });
  ok(!d.getElementById("sp-modal"), "Ctrl+A, Shift+A, Ctrl+Alt+Shift+A and Ctrl+Shift+B open nothing");
  ok(calls.filter((c) => /admin/.test(c.path)).length === 0, "and the server was not called by any of those");
  { const ev = await key(window, d, { key: "a" }); ok(ev.defaultPrevented && !!d.getElementById("sp-adminpw"), "Ctrl+Shift+A (any letter case) opens the password dialog"); }
  ok(!!d.querySelector("#sp-modal .card.sp-admincard") && d.querySelector("#sp-modal .sheethd p").textContent === "Enter the admin password.", "the dialog has the compact-Close class and a one-line description");
  window.document.getElementById("sp-modal").remove();
  window.document.querySelector('.spnav button[data-v="forecast"]').click(); await flush();
  await key(window, d);
  ok(!d.getElementById("sp-modal"), "Ctrl+Shift+A while Site Progress is NOT on screen opens nothing");
  window.document.querySelector('.spnav button[data-v="siteprogress"]').click(); await flush(25);

  // ---- press and hold 'Structure' for 1.5 s: the password dialog; wrong password refused in place, still hidden
  await hold(window, d, 1700);
  let m = d.getElementById("sp-modal");
  ok(!!m && d.getElementById("sp-adminpw").type === "password", "Admin opens a dialog with a PASSWORD field (not shown on screen)");
  type(window, d, "wrong"); await unlock(window, d);
  ok(/Wrong password/.test(T(d.getElementById("sp-adminerr"))) && !!d.getElementById("sp-modal"), "wrong password: the dialog says so and stays open");
  ok(!d.getElementById("sp-rebuildstruct"), "…and Rebuild stays hidden");
  type(window, d, ""); await unlock(window, d);
  ok(/Type the admin password/.test(T(d.getElementById("sp-adminerr"))) && calls.filter((c) => /admin\/check/.test(c.path)).length === 3, "an empty box asks for the password without calling the server again (only the 2 entry checks and the wrong try were sent)");

  // ---- right password (with a space and a non-latin character): unlocked for this tab
  type(window, d, PW); await unlock(window, d);
  ok(!d.getElementById("sp-modal") && !!d.getElementById("sp-rebuildstruct"), "right password: dialog closes and Rebuild appears");
  const chk = calls.filter((c) => /admin\/check/.test(c.path)).pop();
  ok(chk.method === "POST" && chk.h["X-Mi-Admin"] === encodeURIComponent(PW), "the password travels URL-encoded in the X-Mi-Admin header, never in the URL");
  ok(!calls.some((c) => c.path.includes(encodeURIComponent(PW)) || c.path.includes("owner")), "and not in any URL");
  ok(T(d.getElementById("sp-adminlock")) === "Admin unlocked · Lock" && !d.getElementById("sp-adminrow").hidden, "after unlocking, this tab shows 'Admin unlocked · Lock'");
  ok(window.sessionStorage.getItem("mi_admin") === PW && window.localStorage.getItem("mi_admin") === null, "kept for this tab only (sessionStorage), never localStorage");

  // ---- Rebuild sends the password with the real reset call
  click(window, d.getElementById("sp-rebuildstruct")); await flush(30);
  ok(confirms.length === 1 && /ALL recorded progress/.test(confirms[0]), "the existing warning confirm still comes first");
  const rs = calls.find((c) => /structure\/reset/.test(c.path));
  ok(rs && rs.method === "POST" && rs.h["X-Mi-Admin"] === encodeURIComponent(PW), "the reset call carries the admin password");

  // ---- Lock again hides it
  ({ window, d, calls } = await boot({ stored: PW }));
  ok(!!d.getElementById("sp-rebuildstruct"), "a tab that was unlocked stays unlocked after a reload (same tab session)");
  click(window, d.getElementById("sp-adminlock")); await flush();
  ok(!d.getElementById("sp-rebuildstruct") && !d.getElementById("sp-adminlock") && window.sessionStorage.getItem("mi_admin") === null, "Lock hides Rebuild and the Lock link, and forgets the password (back to no trace)");
  await key(window, d);
  ok(!!d.getElementById("sp-adminpw"), "Ctrl+Shift+A brings the password dialog back after Lock");

  // ---- a NEW tab / another person: locked
  ({ window, d } = await boot());
  ok(!d.getElementById("sp-rebuildstruct"), "a fresh tab (another person) has no Rebuild");

  // ---- the owner changed the password on the server: the stale one is refused and locks the tab again
  ({ window, d, calls, confirms } = await boot({ stored: "old-password", serverPw: PW }));
  ok(!!d.getElementById("sp-rebuildstruct"), "a stale password still shows the button (the server is the judge)");
  click(window, d.getElementById("sp-rebuildstruct")); await flush(30);
  ok(!d.getElementById("sp-rebuildstruct") && /no longer matches/.test(T(d.getElementById("sp-toast") || d.body)), "the server's refusal locks the tab again and says why");
  ok(!/Structure cleared/.test(T(d.body)), "and nothing was cleared");

  // ---- server WITHOUT MI_ADMIN_PASSWORD: nothing to type. The secret entry shows Rebuild straight away; a normal tap still shows nothing
  ({ window, d, calls, confirms } = await boot({ serverPw: null }));
  ok(!d.getElementById("sp-rebuildstruct"), "no password on the server: Rebuild is still hidden by default");
  click(window, d.getElementById("sp-railttl")); await flush();
  ok(!d.getElementById("sp-rebuildstruct") && !d.getElementById("sp-modal"), "a normal tap still shows nothing");
  await key(window, d);
  ok(!d.getElementById("sp-modal") && !!d.getElementById("sp-rebuildstruct"), "Ctrl+Shift+A shows Rebuild right away, with NO password dialog");
  ok(T(d.getElementById("sp-adminlock")) === "Admin unlocked · Lock", "and the small Lock appears");
  click(window, d.getElementById("sp-rebuildstruct")); await flush(30);
  ok(confirms.length === 1 && calls.some((c) => /structure\/reset/.test(c.path)) && /Structure cleared/.test(T(d.body)), "Rebuild works (the existing warning confirm comes first)");
  ({ window, d } = await boot({ serverPw: null }));
  await hold(window, d, 1700);
  ok(!d.getElementById("sp-modal") && !!d.getElementById("sp-rebuildstruct"), "press and hold 'Structure' does the same");
  click(window, d.getElementById("sp-adminlock")); await flush();
  ok(!d.getElementById("sp-rebuildstruct") && !d.getElementById("sp-adminlock"), "Lock hides it again");

  // ---- the owner adds a password on the server later: a tab unlocked without one is refused and locks itself again
  ({ window, d, calls } = await boot({ stored: "-", serverPw: PW }));
  click(window, d.getElementById("sp-rebuildstruct")); await flush(30);
  ok(!d.getElementById("sp-rebuildstruct") && /no longer matches/.test(T(d.getElementById("sp-toast") || d.body)), "a stale unlock is refused by the server and the tab locks again");

  // ---- the server errors on the check: a message, nothing unlocks
  ({ window, d } = await boot({ broken: true }));
  await key(window, d);
  ok(!d.getElementById("sp-rebuildstruct") && !d.getElementById("sp-modal"), "a server error on the check unlocks nothing and opens no dialog");

  console.log(`admin lock UI: ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error(e); process.exit(1); });
