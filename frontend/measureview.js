/* Measurement grid: an Excel-like sheet on top of Site Progress. One BOQ line per row, one level / zone per column; click a box and type the
   quantity recorded there, Enter saves it. Every box is saved the moment it is entered, through the SAME Site Progress routes the Zones dialog
   uses (nothing is stored here), so the item rows, % complete, the DPR and the bill always show the same number.

   What it will not do silently: a box whose number is made of measurement entries, or sits inside a whole-level total, is locked (with the
   reason); a quantity above a zone's plan is never cut -- it asks before removing the item's plan.

   Shadow DOM, so its CSS cannot touch the app and the app's CSS cannot touch it.

   window.MIView.open({
     title, subtitle, backLabel, service, services:[names], groups:[{id,name,kids?:[{id,name}]}],
     load(service, code?)        -> Promise<{basis,rows,cells,locks}>   (GET /measure-grid/{service}[?code=])
     loadPlan(service, code)     -> Promise<{rooms:{id:{planned}}, free}> (GET /item-room-plan)
     save(service, code, entries:[{room,qty}]) -> Promise                (POST /progress/rooms)
     planOff(service, code)      -> Promise                               (POST /item-plan-off)
     loadRuns(service, code)     -> Promise<{runs_ok,scopes:[{key,label,runs,active,other,allowed,why,writable}]}>   (GET /progress/runs)
     addRun(service, code, {room,from,to,start?,end?,qty?,remarks?}) -> Promise   (POST /progress/runs; the length is ADDED to the box)
     cancelRun(service, code, {room,run_id,cancelled}) -> Promise         (POST /progress/runs/cancel)
     renamePlace(id, name)       -> Promise                               (structure save; ids never change)
     exportFile(service)         -> Promise<filename>                    (GET /measure-grid/{service}/export, then saves the file)
     importPreview(service, file)-> Promise<preview>                      (POST /measure-grid/{service}/import-preview; saves nothing)
     openEntries(service, row, room)                                      (the item's Measurement sheet; call view.refreshItem(service, code) after it saves)
     place, focus:{code,place}   (optional) open on that place, with that box selected -- used by an item's "Done where" line on Site Progress
     onClose(changed)
   }) */
(function () {
  "use strict";
  const CSS = `
:host{all:initial;display:block;position:fixed;inset:0;z-index:35;--bg:var(--mv-bg,#faf9f7);--panel:#fff;--line:#e7e5df;--line2:#f0eee9;--lock:#f3f2ee;--ink:#1c1c1b;--ink2:#5f5e59;--ink3:#6b6962;--acc:#453a9a;--accbg:#ebe8fa;--selbg:rgba(69,58,154,.09);--hov:#f4f2fb;--hovhead:#e7e3f6;--amber:#7d4c06;--amberbg:#fbf1da;--green:#39631a;--greenbg:#e8f2e0;--red:#a02622;--redbg:#fbe7e6;--sans:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
*{box-sizing:border-box}
[hidden]{display:none!important}
.root{position:relative;margin:0;background:var(--bg);color:var(--ink);font:13.5px/1.45 var(--sans);height:100%;display:flex;flex-direction:column;min-width:0;font-variant-numeric:tabular-nums}
button{font:inherit;color:inherit;cursor:pointer}
button:focus-visible,input:focus-visible,select:focus-visible,#grid:focus-visible{outline:2px solid var(--acc);outline-offset:1px}
.top{display:flex;align-items:center;gap:14px;padding:8px 16px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.top h1{font-size:14.5px;margin:0;font-weight:600}
.top p{margin:0;font-size:12px;color:var(--ink3)}
.sp{flex:1}
.pl{display:inline-flex;gap:3px;align-items:center}
.pl select{border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:8px;padding:5px 8px;font:inherit;max-width:190px}
.btn.on{background:var(--accbg);border-color:transparent;color:var(--acc)}
.cnt{margin-left:auto;color:var(--ink3);font-size:12px;white-space:nowrap}
th.lv{text-align:center;color:var(--ink);font-weight:600}
.toast{position:absolute;top:52px;right:16px;z-index:30;max-width:min(560px,calc(100% - 32px));padding:8px 14px;border-radius:10px;background:var(--ink);color:#fff;box-shadow:0 6px 20px rgba(0,0,0,.22);font-size:13px}
.toast:empty{display:none}
.toast.bad{background:var(--red)}
.toast button{border:none;background:none;color:#fff;text-decoration:underline;padding:0 0 0 8px}
.pill{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);background:var(--panel);border-radius:999px;padding:3px 11px;font-size:12px;color:var(--ink2)}
.pill.ok{background:var(--greenbg);color:var(--green);border-color:transparent}
.pill.busy{background:var(--accbg);color:var(--acc);border-color:transparent}
.pill.bad{background:var(--redbg);color:var(--red);border-color:transparent}
.btn{border:1px solid var(--line);background:var(--panel);border-radius:8px;padding:6px 12px;font-weight:500;display:inline-flex;gap:6px;align-items:center}
.btn:hover{background:var(--hov)}
.btn.sm{padding:3px 9px;font-size:12.5px}
.btn.pri{background:var(--ink);color:#fff;border-color:var(--ink)}
.tabs{display:flex;gap:2px;padding:6px 16px 0;border-bottom:1px solid var(--line);flex-wrap:wrap}
.tab{border:1px solid transparent;border-bottom:none;background:none;padding:6px 14px;border-radius:8px 8px 0 0;color:var(--ink2);font-weight:500;margin-bottom:-1px}
.tab[aria-selected="true"]{background:var(--panel);border-color:var(--line);color:var(--ink)}
.bar{display:flex;gap:8px;align-items:center;padding:8px 16px;flex-wrap:wrap}
.bar input[type=search]{border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:8px;padding:5px 10px;width:200px;font:inherit}
.legend{display:flex;gap:14px;font-size:12px;color:var(--ink3);flex-wrap:wrap;align-items:center;margin-left:auto}
.fbar{display:flex;margin:0 16px;border:1px solid var(--line);border-radius:8px;background:var(--panel);align-items:stretch;min-height:34px}
.fbar .nm{padding:6px 12px;border-right:1px solid var(--line);font-weight:500;min-width:230px;max-width:46%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--ink2)}
.fbar .fx{padding:6px 10px;color:var(--ink3);font-family:var(--mono);font-style:italic;border-right:1px solid var(--line)}
.fbar input{flex:1;border:none;background:none;color:var(--ink);padding:6px 10px;font:13px var(--mono);min-width:60px}
.fbar input:disabled{color:var(--ink3)}
.fbar input:focus-visible{outline:none}
.fbar:focus-within{border-color:var(--acc)}
.fbar .res{padding:6px 12px;color:var(--ink2);white-space:nowrap;border-left:1px solid var(--line);font-family:var(--mono)}
.fbar .res.err{color:var(--red);background:var(--redbg)}
#gridwrap{flex:1;min-height:120px;margin:8px 16px 8px;border:1px solid var(--line);border-radius:8px;background:var(--panel);overflow:auto;position:relative}
#grid{outline:none;display:inline-block;min-width:100%}
table{border-collapse:separate;border-spacing:0;font-size:13px}
th,td{border-right:1px solid var(--line2);border-bottom:1px solid var(--line2);height:30px;padding:0 8px;white-space:nowrap;background:var(--panel)}
thead th{position:sticky;background:var(--bg);font-weight:500;color:var(--ink2);height:28px;z-index:5;text-align:center;border-bottom:1px solid var(--line)}
thead tr:nth-child(1) th{top:0}
thead tr:nth-child(2) th{top:28px}
th.grp{cursor:pointer;user-select:none;color:var(--ink)}
th.grp:hover{background:var(--hovhead)}
th.leaf{min-width:84px;max-width:120px;overflow:hidden;text-overflow:ellipsis;font-size:12px}
th input.rn{width:100%;min-width:70px;border:none;background:var(--panel);font:inherit;color:var(--ink);padding:2px 4px;outline:2px solid var(--acc);outline-offset:-2px;text-align:center}
.sl{position:sticky;z-index:4}
thead .sl{z-index:8}
td.rl{background:var(--bg);color:var(--ink);cursor:default}
td.rl.on{background:var(--hovhead)}
.c0{left:0;width:62px;min-width:62px;max-width:62px;font-family:var(--mono);font-size:12px;color:var(--ink2)}
.c1{left:62px;width:270px;min-width:270px;max-width:270px;overflow:hidden;text-overflow:ellipsis}
.c2{left:332px;width:64px;min-width:64px;max-width:64px;border-right:1px solid var(--line)!important;text-align:left;color:var(--ink2);font-size:12px}
.q3{right:0;width:94px;min-width:94px;max-width:94px}
.q2{right:94px;width:86px;min-width:86px;max-width:86px}
.q1{right:180px;width:86px;min-width:86px;max-width:86px}
.q0{right:266px;width:86px;min-width:86px;max-width:86px}
td.sr{text-align:right;font-family:var(--mono);background:var(--lock);font-weight:500}
td.q0{border-left:1px solid var(--line)}
td.sr.dash{color:var(--ink3);font-weight:400}
td.sr.warn{color:var(--amber);background:var(--amberbg)}
td.c{text-align:right;font-family:var(--mono);cursor:cell;position:relative;min-width:84px;max-width:120px}
td.c.sum{background:var(--lock);color:var(--ink2);font-style:italic;cursor:default}
td.c.zero{color:var(--ink3)}
td.c.na{background:var(--bg);color:var(--ink3);cursor:not-allowed}
td.c.sum.na{background:var(--lock);cursor:default}
td.c.lock{background:var(--lock);color:var(--ink2);cursor:not-allowed}
td.c.lock::after{content:"";position:absolute;right:3px;top:3px;width:6px;height:6px;border-radius:50%;background:#b9b6ad}
td.c.fx::before{content:"";position:absolute;left:0;top:0;border-style:solid;border-width:7px 7px 0 0;border-color:var(--acc) transparent transparent transparent;opacity:.75}
td.c.saving{color:var(--acc)}
td.c.saving::before{content:"";position:absolute;left:4px;top:50%;margin-top:-3px;width:6px;height:6px;border-radius:50%;background:var(--acc);animation:pulse .8s ease-in-out infinite}
td.c.ok{background:var(--greenbg)}
td.c.bad{background:var(--redbg)!important;color:var(--red);box-shadow:inset 0 0 0 1px var(--red)}
@keyframes pulse{50%{opacity:.25}}
@media (prefers-reduced-motion: reduce){td.c.saving::before{animation:none}}
tr.hv td.c:not(.sel):not(.ok):not(.bad),td.c.hc:not(.sel):not(.ok):not(.bad){background:var(--hov)}
tr.hv td.c.hc:not(.sel):not(.ok):not(.bad){background:var(--hovhead)}
tr.hv td.rl,tr.hv td.sr:not(.warn){background:var(--hovhead)}
th.hh{background:var(--hovhead)!important;color:var(--ink)}
td.c.rng{background:var(--selbg)!important}
td.c.sel{background:var(--selbg)!important;box-shadow:inset 0 0 0 2px var(--acc)}
td.c input.ed{position:absolute;inset:0;width:100%;height:100%;border:none;background:var(--panel);color:var(--ink);font:13px var(--mono);text-align:right;padding:0 8px;outline:2px solid var(--acc);outline-offset:-2px;z-index:2}
.dock{margin:0 16px 8px;border:1px solid var(--line);border-radius:8px;background:var(--panel);padding:8px 16px;max-height:190px;overflow:auto}
.dock .hint{color:var(--ink3)}
.cap{font-size:12px;color:var(--ink3)}
.sec{font-size:11.5px;letter-spacing:.5px;color:var(--ink3);margin:0 0 4px}
.dk{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(0,1fr) minmax(0,1.2fr);gap:20px}
.dk h2{margin:0;font-size:14px;font-weight:600;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.big{font-size:20px;font-weight:700}
.meter{height:8px;border-radius:99px;background:#eceae4;overflow:hidden;margin:6px 0}
.meter i{display:block;height:100%;background:var(--green)}
.meter.over i{background:var(--amber)}
.loc{display:grid;grid-template-columns:70px minmax(0,1fr) 70px;gap:10px;align-items:center;height:22px}
.loc .b{height:7px;border-radius:99px;background:#ecece4;overflow:hidden}
.loc .b i{display:block;height:100%;background:var(--green)}
.loc .n{text-align:right;font-family:var(--mono);font-size:12.5px}
.tag{display:inline-flex;align-items:center;min-height:22px;padding:2px 9px;border-radius:99px;font-size:11.5px;font-weight:500;margin:0 6px 4px 0}
.tag.g{background:var(--greenbg);color:var(--green)}
.tag.a{background:var(--amberbg);color:var(--amber)}
.tag.s{background:var(--lock);color:var(--ink2)}
.ent{display:flex;justify-content:space-between;gap:10px;font-size:12.5px;padding:1px 0}
.runs{margin:0 16px 8px;border:1px solid var(--line);border-radius:8px;background:var(--panel);max-height:clamp(150px,30vh,236px);overflow:auto}
.runs .rh{display:flex;gap:12px;align-items:baseline;flex-wrap:wrap;padding:7px 12px;border-bottom:1px solid var(--line2);position:sticky;top:0;background:var(--panel);z-index:2}
.runs .rh b{font-weight:600}
.runs .rh .tot{margin-left:auto;font-family:var(--mono)}
.rt{border-collapse:collapse;width:100%;font-size:13px}
.rt th{position:sticky;top:34px;background:var(--bg);font-weight:500;color:var(--ink2);text-align:left;padding:3px 8px;height:26px;border-bottom:1px solid var(--line);z-index:1}
.rt td{height:30px;padding:0 8px;border-bottom:1px solid var(--line2);border-right:1px solid var(--line2);background:var(--panel)}
.rt tr:not(.nw):hover td{background:var(--hov)}
.rt td.n,.rt th.n{text-align:right;font-family:var(--mono)}
.rt td.i{color:var(--ink3);width:34px;text-align:right}
.rt tr.x td{color:var(--ink3);text-decoration:line-through}
.rt tr.x td.a{text-decoration:none}
.rt tr.ot td{color:var(--ink2);font-style:italic}
.rt tr.nw td{padding:0;background:var(--bg)}
.rt tr.nw input{width:100%;height:30px;border:none;background:transparent;color:var(--ink);font:13px var(--sans);padding:0 8px;min-width:60px}
.rt tr.nw input.n{text-align:right;font-family:var(--mono)}
.rt tr.nw input:focus{background:var(--panel);outline:2px solid var(--acc);outline-offset:-2px}
.rt tr.nw input:disabled{color:var(--ink2);background:var(--lock)}
.rt .a button{border:none;background:none;color:var(--acc);padding:2px 6px;font-size:12.5px;text-decoration:underline}
.rt .a button.go{background:var(--ink);color:#fff;border-radius:6px;text-decoration:none;padding:3px 12px;font-weight:500}
.runs .rf{padding:5px 12px;color:var(--ink3);font-size:12px}
.runs .rf.bad{color:var(--red)}
.mob{display:none}
@media (max-width:720px){
  #desk,.dock,.runs{display:none!important}
  .mob{display:flex;flex-direction:column;flex:1;min-height:0;overflow:auto;padding:8px 16px 12px;gap:10px}
  .top p{display:none}
  .dk{grid-template-columns:minmax(0,1fr)}
}
.layer{position:fixed;inset:0;background:rgba(28,28,27,.42);z-index:20;display:grid;place-items:center;padding:16px}
.sheet{background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:0 10px 36px rgba(0,0,0,.2);width:min(900px,100%);max-height:88vh;display:flex;flex-direction:column;min-width:0}
.sheet header{display:flex;align-items:center;gap:10px;padding:14px 18px;border-bottom:1px solid var(--line)}
.sheet header h2{margin:0;font-size:15px;font-weight:600;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sheet .bd{padding:14px 18px;overflow:auto;display:flex;flex-direction:column;gap:12px;min-width:0}
.sheet footer{padding:12px 18px;border-top:1px solid var(--line);display:flex;gap:10px;justify-content:flex-end;align-items:center;flex-wrap:wrap}
.sheet footer .why{margin-right:auto;color:var(--ink3);font-size:12.5px}
.x{border:none;background:none;font-size:22px;line-height:1;color:var(--ink2);padding:2px 8px}
.chips{display:flex;gap:8px;flex-wrap:wrap}
.chip{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12.5px;font-weight:500}
.chip.g{background:var(--greenbg);color:var(--green)}.chip.a{background:var(--amberbg);color:var(--amber)}.chip.r{background:var(--redbg);color:var(--red)}.chip.s{background:var(--lock);color:var(--ink2)}
.tw{overflow:auto;border:1px solid var(--line);border-radius:8px;max-height:38vh}
.tbl{border-collapse:collapse;width:100%}
.tbl th,.tbl td{border:1px solid var(--line2);padding:4px 8px;height:auto;background:var(--panel);text-align:left;white-space:normal}
.tbl th{position:sticky;top:0;background:var(--bg);font-size:12px;color:var(--ink2);z-index:1}
.tbl td.n{text-align:right;font-family:var(--mono);white-space:nowrap}
.sheet details summary{cursor:pointer;color:var(--ink2)}
.mcnt{display:flex;align-items:center;gap:10px}
.mpick{display:flex;gap:8px;align-items:center}
.mpick select,.mob input[type=search]{flex:1;min-width:0;border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:8px;padding:9px 10px;font:inherit}
.mrow{border:1px solid var(--line);background:var(--panel);border-radius:10px;padding:10px 12px;display:grid;grid-template-columns:minmax(0,1fr) 112px;gap:4px 10px;align-items:center}
.mrow .nm{font-weight:500;min-width:0}
.mrow .sub{grid-column:1/2;color:var(--ink3);font-size:12px}
.mrow input{border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:8px;padding:9px 10px;font:15px var(--mono);text-align:right;width:100%}
.mrow input:disabled{background:var(--lock);color:var(--ink3)}
.mrow input.bad{border-color:var(--red);background:var(--redbg)}
.mrow .why{grid-column:1/3;color:var(--ink3);font-size:12px}
.mrow .err-t{grid-column:1/3;color:var(--red);font-size:12px}
@media (prefers-reduced-motion:no-preference){
  td.c,td.rl,td.sr,th{transition:background-color .07s ease-out,color .07s ease-out,box-shadow .1s ease-out}
  td.c.sel{transition:background-color .05s ease-out,box-shadow .09s ease-out}
  td.c.ok{animation:okflash .9s ease-out}
  .btn,.tab,.rt .a button{transition:background-color .08s ease-out,border-color .08s ease-out,color .08s ease-out}
  .runs:not([hidden]){animation:rise .14s ease-out}
  .rt tr td{transition:background-color .07s ease-out}
  .rt tr.x td{transition:color .15s}
  .layer{animation:fadein .12s ease-out}
  .sheet{animation:pop .14s cubic-bezier(.2,.8,.3,1)}
  .meter i,.loc .b i{transition:width .22s ease-out}
  .ask:not([hidden]){animation:rise .14s ease-out}
  @keyframes okflash{from{background-color:var(--greenbg)}to{background-color:transparent}}
  @keyframes rise{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:none}}
  @keyframes fadein{from{opacity:0}to{opacity:1}}
  @keyframes pop{from{opacity:0;transform:scale(.975) translateY(4px)}to{opacity:1;transform:none}}
}
`;
  let live = null;

  // ---- the typed sum: numbers, + and x only (no minus, no letters), like a QS sheet's =112+155
  function calc(s) {
    let i = 0;
    const ws = () => { while (s[i] === " ") i++; };
    function num() { ws(); if (s[i] === "(") { i++; const v = add(); ws(); if (s[i] !== ")") throw 0; i++; return v; } const m = /^\d+(\.\d+)?|^\.\d+/.exec(s.slice(i)); if (!m) throw 0; i += m[0].length; return parseFloat(m[0]); }
    function mul() { let v = num(); ws(); while (s[i] === "*") { i++; v *= num(); ws(); } return v; }
    function add() { let v = mul(); ws(); while (s[i] === "+") { i++; v += mul(); ws(); } return v; }
    const v = add(); ws(); if (i !== s.length) throw 0; return v;
  }
  function splitTop(s) { const out = []; let d = 0, cur = ""; for (const ch of s) { if (ch === "(") d++; if (ch === ")") d--; if (ch === "+" && d === 0) { out.push(cur); cur = ""; } else cur += ch; } out.push(cur); return out; }
  // the sum to remember next to a typed quantity: "=112+155" for 112+155 / 112 + 155 / =112+155, nothing for a plain number
  function exprText(raw) {
    let s = String(raw == null ? "" : raw).trim().replace(/[×xX]/g, "*").replace(/\s+/g, "");
    if (s[0] === "=") s = s.slice(1);
    return /[+*(]/.test(s) ? "=" + s : null;
  }
  function evalExpr(raw, count) {
    let s = String(raw == null ? "" : raw).trim();
    if (s === "") return { ok: true, empty: true, v: null, pieces: 0 };
    if (s[0] === "=") s = s.slice(1).trim();
    s = s.replace(/[×xX]/g, "*");
    if (/^\d{1,3}(,\d{3})+(\.\d+)?$/.test(s)) s = s.replace(/,/g, "");
    if (/-/.test(s)) return { ok: false, err: "A minus does not work. Type the quantity, for example 40 or 37+9.4" };
    if (!/^[0-9+*().\s]+$/.test(s)) return { ok: false, err: "Only numbers, + and × are allowed" };
    let v; try { v = calc(s); } catch (e) { return { ok: false, err: "The sum is incomplete. For example 10+2+30" }; }
    v = Math.round(v * 1e6) / 1e6;
    if (count && !Number.isInteger(v)) return { ok: false, err: "This item is counted in whole numbers (12.5 is not allowed)" };
    if (v > 1e9) return { ok: false, err: "That is a very large number. Check it again" };
    return { ok: true, v, pieces: splitTop(s).length };
  }

  function open(OPTS) {
    if (live) live.close();
    const host = document.createElement("div"); host.id = "mv-host"; document.body.appendChild(host);
    const SR = host.attachShadow({ mode: "open" });
    const prevOv = document.body.style.overflow; document.body.style.overflow = "hidden";
    const $ = (s) => SR.querySelector(s);
    const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    const fmt = (v) => (v == null ? "—" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: 2 }));
    const raw = (v) => (v == null ? "" : String(Math.round(v * 1e4) / 1e4));
    const isMobile = () => { try { return !!(window.matchMedia && window.matchMedia("(max-width:720px)").matches); } catch (e) { return false; } };

    const groups = (OPTS.groups || []).map((g) => Object.assign({}, g, g.kids ? { open: false, kids: g.kids.map((k) => Object.assign({}, k)) } : {}));
    const services = (OPTS.services && OPTS.services.length ? OPTS.services : [OPTS.service]).filter(Boolean);
    const DS = {};                                      // per service: {rows, cells, locks, basis, by:Map}
    const setData = (svc, data) => { DS[svc] = { basis: data.basis, rows: data.rows || [], cells: data.cells || {}, locks: data.locks || {}, exprs: data.exprs || {}, by: new Map((data.rows || []).map((r) => [r.code, r])) }; };
    let svc = OPTS.service || services[0];
    setData(svc, OPTS.data || { rows: [], cells: {}, locks: {}, basis: "unknown" });
    const D = () => DS[svc] || { rows: [], cells: {}, locks: {}, exprs: {}, basis: "unknown", by: new Map() };

    let sel = { r: -1, c: -1, r2: -1, c2: -1 };        // range: anchor (r,c) .. (r2,c2) in visible rows / columns
    let q = "", hov = { code: null, col: null }, hovEls = [], colCache = null;
    let edit = null, drag = false, changed = false, mp = 0, mq = "", detailsOpen = false;
    let place = "", onlyWork = true;                    // place: show ONE place (a level > zone) as a list; onlyWork: lines with nothing recorded are hidden until searched for
    let undo = [], redo = [], pending = 0, failed = 0, chain = Promise.resolve();
    const planCache = new Map();
    let VC = [], VR = [];
    const mem = {};                                     // last message timers

    const onUp = () => { drag = false }; const onResize = () => paintMobile();
    const close = () => { document.removeEventListener("mouseup", onUp); window.removeEventListener("resize", onResize); host.remove(); live = null; document.body.style.overflow = prevOv; if (OPTS.onClose) OPTS.onClose(changed); };
    live = { close };

    SR.innerHTML = `<style>${CSS}</style><div class="root">
      <div class="top"><button class="btn sm" id="back">&larr; ${esc(OPTS.backLabel || "Back")}</button><div><h1>${esc(OPTS.title || "")}</h1><p id="sub"></p></div><div class="sp"></div><span class="pill ok" id="state" role="status">All changes saved</span></div>
      <div class="tabs" role="tablist" id="tabs"></div>
      <div id="desk" style="display:flex;flex-direction:column;flex:1;min-height:0">
        <div class="bar"><button class="btn sm" id="expand">Expand all</button><button class="btn sm" id="collapse">Collapse all</button>
          <span class="pl"><button class="btn sm" id="p0" aria-label="Previous place" title="Previous place">&lsaquo;</button><select id="plc" aria-label="Place"></select><button class="btn sm" id="p1" aria-label="Next place" title="Next place">&rsaquo;</button></span>
          <button class="btn sm" id="ow" aria-pressed="true" title="Hide the lines with nothing recorded. Searching still finds every line.">Only lines with work</button>
          <input type="search" id="q" placeholder="Find a BOQ line" aria-label="Find a BOQ line">
          <button class="btn sm" id="undo" title="Ctrl+Z" disabled>Undo</button><button class="btn sm" id="redo" title="Ctrl+Y" disabled>Redo</button>
          <button class="btn sm" id="xexp" title="Download this service as an Excel file">Export Excel</button><button class="btn sm" id="ximp" title="Fill the grid from an Excel file made by Export">Import Excel</button><input type="file" id="xfile" accept=".xlsx,.xlsm" hidden>
          <span class="cnt" id="cnt"></span><button class="btn sm" id="details" aria-pressed="false" title="Show how much is done, where, and what it is based on">Details</button></div>
        <div class="fbar" id="fbar"><div class="nm" id="nm">Click a box</div><div class="fx" title="The total recorded in that place to date: typing replaces it, it does not add. Double-click a level or zone name to rename it.">qty</div><input id="fx" autocomplete="off" spellcheck="false" aria-label="Quantity of the selected box" disabled><div class="res" id="res"></div></div>
        <div id="gridwrap"><div id="grid" tabindex="0" aria-label="Measurement grid"><div id="tbl"></div></div></div>
      </div>
      <div class="mob" id="mob"></div>
      <div id="layer"></div>
      <div class="runs" id="runs" hidden></div>
      <div class="dock" id="dock" aria-live="polite" hidden></div>
      <div class="toast" id="msg" role="status"></div>
    </div>`;
    $("#back").onclick = close;

    // ---------------- small helpers ----------------
    const leaves = () => { const a = []; groups.forEach((g) => { if (g.kids) g.kids.forEach((k) => a.push({ id: k.id, name: k.name, group: g.name })); else a.push({ id: g.id, name: g.name, group: "" }); }); return a; };
    const placeLabel = (id) => { const l = leaves().find((x) => x.id === id); return l ? (l.group && l.group !== l.name ? l.group + " ▸ " + l.name : l.name) : id; };
    const cellOf = (code, id) => { const v = D().cells[code + "|" + id]; return v == null ? null : v; };
    const lockOf = (code, id) => D().locks[code + "|" + id] || null;
    const exprOf = (code, id) => D().exprs[code + "|" + id] || null;                 // the sum behind a box ("=112+155"), only while it still adds up
    const sumKids = (code, g) => { let s = null; (g.kids || []).forEach((k) => { const v = cellOf(code, k.id); if (v != null) s = (s || 0) + v; }); return s; };
    const unitOf = (r) => (r && r.unit) || "";
    const vgroups = () => { if (!place) return groups; const l = leaves().find((x) => x.id === place); if (!l) { place = ""; return groups; }
      return [l.group && l.group !== l.name ? { id: "~pv", name: l.group, open: true, fixed: true, kids: [{ id: l.id, name: l.name }] } : { id: l.id, name: l.name }]; };
    const visCols = () => { const a = []; vgroups().forEach((g) => { if (!g.kids) a.push({ id: g.id, g: null, leaf: true }); else if (g.open) g.kids.forEach((k) => a.push({ id: k.id, g: g.id, leaf: true })); else a.push({ id: g.id, g: g.id, sum: true }); }); return a; };
    // a line "has work" when something is recorded for it (in the chosen place, or anywhere). A search always finds every line, so new work can be added.
    const hasWork = (r) => (place ? (cellOf(r.code, place) || 0) > 0 : (r.done || 0) > 0);
    const workAnywhere = () => D().rows.some((r) => (r.done || 0) > 0);
    const filtering = () => onlyWork && !q.trim() && workAnywhere();
    const visRows = () => { const t = q.trim().toLowerCase(); if (t) return D().rows.filter((r) => ((r.desc || "") + " " + r.code).toLowerCase().includes(t)); return filtering() ? D().rows.filter(hasWork) : D().rows; };
    const rowEl = (code) => [...$("#tbl").querySelectorAll("tr[data-code]")].find((r) => r.dataset.code === code);
    const tdAt = (ri, ci) => { const tb = $("#tbl tbody"); const tr = tb && tb.rows[ri]; return tr ? tr.cells[3 + ci] || null : null; };
    const selOn = () => sel.r >= 0 && sel.c >= 0 && VR[sel.r] && VC[sel.c];
    const rng = () => ({ r1: Math.min(sel.r, sel.r2), r2: Math.max(sel.r, sel.r2), c1: Math.min(sel.c, sel.c2), c2: Math.max(sel.c, sel.c2) });
    function say(text, bad, ms) { const m = $("#msg"); m.className = "toast" + (bad ? " bad" : ""); m.innerHTML = text || ""; clearTimeout(mem.t); if (text && ms !== 0) mem.t = setTimeout(() => { if (m.innerHTML === text) m.innerHTML = ""; }, ms || 5000); }
    function setState() {
      const s = $("#state");
      if (pending) { s.className = "pill busy"; s.textContent = "Saving…"; }
      else if (failed) { s.className = "pill bad"; s.textContent = failed + " change" + (failed > 1 ? "s" : "") + " not saved"; }
      else { s.className = "pill ok"; s.textContent = "All changes saved"; }
      $("#undo").disabled = !undo.length; $("#redo").disabled = !redo.length;
    }

    // recompute the right-hand numbers of a row from its boxes (the server's own figures replace them right after a save)
    function recalc(r) {
      let done = 0; leaves().forEach((l) => { const v = cellOf(r.code, l.id); if (v != null) done += v; });
      r.done = Math.round(done * 1e4) / 1e4;
      if (r.boq_qty != null) { r.pending = Math.round(Math.max(r.boq_qty - r.done, 0) * 1e4) / 1e4; r.pct = Math.round(1000 * r.done / r.boq_qty) / 10; r.done_above_boq = r.done > r.boq_qty + 1e-6; }
    }

    // ---------------- tabs + title ----------------
    function paintTabs() {
      $("#tabs").innerHTML = services.length > 1 ? services.map((s) => `<button class="tab" role="tab" aria-selected="${s === svc}" data-svc="${esc(s)}">${esc(s)}</button>`).join("") : "";
      $("#sub").textContent = `${svc} · ${D().rows.length} BOQ lines`;
    }
    $("#tabs").addEventListener("click", async (e) => {
      const b = e.target.closest("[data-svc]"); if (!b || b.dataset.svc === svc) return;
      if (edit) endEdit(false);
      const to = b.dataset.svc; svc = to; sel = { r: -1, c: -1, r2: -1, c2: -1 }; q = ""; $("#q").value = ""; mp = 0;
      if (!DS[to]) { DS[to] = { basis: "unknown", rows: [], cells: {}, locks: {}, exprs: {}, by: new Map() }; }
      paint();
      try { setData(to, await OPTS.load(to)); } catch (err) { say("Could not load " + esc(to) + ": " + esc(errText(err)), true, 8000); }
      if (svc === to) paint();
    });
    const errText = (e) => (e && (e.message || e.detail || e.body)) ? String(e.message || e.detail || e.body) : String(e || "error");

    // ---------------- grid ----------------
    const naWhy = (r) => (!(r.plan > 0) ? "This BOQ line has no quantity, so nothing can be recorded against it here." : "This item is not in this place.");
    function cellHTML(r, c) {
      let v, cls = "c", title = "";
      // One rule for every box: a number (0 too) = a quantity can be recorded here; a dash = it cannot, and the tooltip says why.
      const why = naWhy(r);
      if (c.sum) { v = sumKids(r.code, groups.find((g) => g.id === c.id)); cls += " sum"; if (v == null) { cls += " na"; title = why; } }
      else {
        v = cellOf(r.code, c.id);
        if (v == null) { cls += " na"; title = why; }
        else { const lk = lockOf(r.code, c.id); if (lk) { cls += " lock"; title = lk.why; } }
      }
      if (v === 0) cls += " zero";
      const ex = !c.sum && v != null ? exprOf(r.code, c.id) : null;
      if (ex) { cls += " fx"; title = "Formula " + ex; }
      return `<td class="${cls}" data-col="${esc(c.id)}"${c.sum ? ' data-sum="1"' : ""}${title ? ` title="${esc(title)}"` : ""}>${v == null ? "—" : fmt(v)}</td>`;
    }
    function rightHTML(r) {
      let h = `<td class="sl sr q0${r.boq_qty == null ? " dash" : ""}">${fmt(r.boq_qty)}</td>`;
      h += `<td class="sl sr q1${r.plan_above_boq ? " warn" : ""}"${r.plan_above_boq ? ` title="Site plan ${esc(fmt(r.plan))} is above the BOQ ${esc(fmt(r.boq_qty))}"` : ""}>${r.plan_above_boq ? "▲ " : ""}${fmt(r.plan)}</td>`;
      h += `<td class="sl sr q2">${fmt(r.done)}</td>`;
      h += `<td class="sl sr q3${r.pending == null ? " dash" : r.done_above_boq ? " warn" : ""}"${r.done_above_boq ? ' title="Done is above the BOQ quantity"' : ""}>${fmt(r.pending)}</td>`;
      return h;
    }
    function rowHTML(r, cols) {
      const on = VR[sel.r] && VR[sel.r].code === r.code ? " on" : "";
      return `<td class="sl rl c0${on}" data-row="1">${esc(r.code)}</td><td class="sl rl c1${on}" data-row="1" title="${esc(r.desc)}">${esc(r.desc)}</td><td class="sl rl c2${on}" data-row="1">${esc(unitOf(r))}</td>` + cols.map((c) => cellHTML(r, c)).join("") + rightHTML(r);
    }
    function paintGrid() {
      VC = visCols(); VR = visRows();
      let h = "<table><thead><tr>";
      h += `<th class="sl c0" rowspan="2">Sr</th><th class="sl c1" rowspan="2" style="text-align:left">BOQ line</th><th class="sl c2" rowspan="2" style="text-align:left">Unit</th>`;
      vgroups().forEach((g) => {
        if (g.fixed) { h += `<th class="lv" colspan="${g.kids.length}">${esc(g.name)}</th>`; return; }
        if (!g.kids) { h += `<th rowspan="2" class="leaf" data-lc="${esc(g.id)}" title="${esc(g.name)} (double-click to rename)">${esc(g.name)}</th>`; return; }
        h += `<th class="grp" colspan="${g.open ? g.kids.length : 1}" ${g.open ? "" : 'rowspan="2"'} data-g="${esc(g.id)}" title="${g.open ? "Collapse" : "Expand"} ${esc(g.name)} (double-click to rename)">${g.open ? "▾" : "▸"} ${esc(g.name)}${g.open ? "" : ` <span style="font-weight:400;color:var(--ink3)">(${g.kids.length})</span>`}</th>`;
      });
      h += `<th class="sl q0" rowspan="2" title="${D().basis === "total" ? "The BOQ's own quantity" : "This service's BOQ quantity is per room, so a whole-project figure is not known (a dash, never a guess)"}">BOQ qty</th><th class="sl q1" rowspan="2" title="The planned quantity Site Progress works with">Site plan</th><th class="sl q2" rowspan="2" title="Recorded in all places to date">To date</th><th class="sl q3" rowspan="2">Pending</th></tr><tr>`;
      vgroups().forEach((g) => { if (g.kids && g.open) g.kids.forEach((k) => { h += `<th class="leaf" data-lc="${esc(k.id)}" title="${esc(k.name)} (double-click to rename)">${esc(k.name)}</th>`; }); });
      h += "</tr></thead><tbody>";
      VR.forEach((r) => { h += `<tr data-code="${esc(r.code)}">${rowHTML(r, VC)}</tr>`; });
      if (!VR.length) h += `<tr><td class="sl rl c0"></td><td class="sl rl c1" style="color:var(--ink3);white-space:normal;height:auto;padding:10px 8px">${q.trim() ? "No BOQ line matches" : place ? "Nothing is recorded in " + esc(placeLabel(place)) + " yet. Search a BOQ line above to add its work." : "No BOQ line"}</td><td class="sl rl c2"></td></tr>`;
      $("#tbl").innerHTML = h + "</tbody></table>";
      colCache = null; hovEls = []; applyHover(hov.code, hov.col);
      paintBar(); paintSel();
    }
    function paintRow(code) {
      const r = D().by.get(code), tr = rowEl(code); if (!r || !tr) return;
      tr.innerHTML = rowHTML(r, VC);
      colCache = null; hovEls = hovEls.filter((e) => e.isConnected);
      applyHover(hov.code, hov.col); paintSel();
    }

    // ---------------- hover (the row and column under the mouse light up) ----------------
    const cellsOfCol = (id) => {
      if (!colCache) { colCache = new Map(); $("#tbl").querySelectorAll("td.c").forEach((td) => { const k = td.dataset.col; (colCache.get(k) || colCache.set(k, []).get(k)).push(td); }); }
      return colCache.get(id) || [];
    };
    function applyHover(code, col) {
      hovEls.forEach((e) => e.classList.remove("hv", "hc", "hh")); hovEls = [];
      hov = { code, col };
      if (!code) return;
      const tr = rowEl(code); if (tr) { tr.classList.add("hv"); hovEls.push(tr); }
      if (col) {
        cellsOfCol(col).forEach((td) => { td.classList.add("hc"); hovEls.push(td); });
        $("#tbl").querySelectorAll("thead th[data-lc],thead th[data-g]").forEach((th) => { if (th.dataset.lc === col || th.dataset.g === col) { th.classList.add("hh"); hovEls.push(th); } });
      }
    }

    // ---------------- selection ----------------
    function paintSel() {
      $("#tbl").querySelectorAll("td.sel,td.rng").forEach((e) => e.classList.remove("sel", "rng"));
      $("#tbl").querySelectorAll("td.rl.on").forEach((e) => e.classList.remove("on"));
      if (!selOn()) { paintFbar(); paintDock(); return; }
      const g = rng();
      for (let i = g.r1; i <= g.r2; i++) for (let j = g.c1; j <= g.c2; j++) { const td = tdAt(i, j); if (td) td.classList.add(i === sel.r && j === sel.c ? "sel" : "rng"); }
      const tb = $("#tbl tbody"); const tr = tb && tb.rows[sel.r]; if (tr) for (let k = 0; k < 3; k++) tr.cells[k].classList.add("on");
      const cells = (g.r2 - g.r1 + 1) * (g.c2 - g.c1 + 1);
      paintFbar(); paintDock();
      if (cells > 1) { let n = 0, s = 0; for (let i = g.r1; i <= g.r2; i++) for (let j = g.c1; j <= g.c2; j++) { const v = valueAt(i, j); if (v != null) { n++; s += v; } } $("#res").textContent = `${cells} boxes · ${n} with a quantity · Sum ${fmt(s)}`; }
    }
    function valueAt(ri, ci) { const r = VR[ri], c = VC[ci]; if (!r || !c) return null; return c.sum ? sumKids(r.code, groups.find((g) => g.id === c.id)) : cellOf(r.code, c.id); }
    function select(r, c, extend) {
      if (!VR.length || !VC.length) return;
      r = Math.max(0, Math.min(VR.length - 1, r)); c = Math.max(0, Math.min(VC.length - 1, c));
      if (extend && selOn()) { sel.r2 = r; sel.c2 = c; } else sel = { r, c, r2: r, c2: c };
      paintSel(); scrollTo(r, c);
    }
    function scrollTo(r, c) {
      const td = tdAt(r, c), w = $("#gridwrap"); if (!td) return;
      const L = td.offsetLeft, R = L + td.offsetWidth, sl = w.scrollLeft;
      if (L < sl + 400) w.scrollLeft = Math.max(0, L - 400); else if (R > sl + w.clientWidth - 356) w.scrollLeft = R - w.clientWidth + 356;
      const T = td.offsetTop, B = T + td.offsetHeight;
      if (T < w.scrollTop + 58) w.scrollTop = Math.max(0, T - 58); else if (B > w.scrollTop + w.clientHeight) w.scrollTop = B - w.clientHeight;
    }

    // ---------------- why a box cannot be typed into ----------------
    function whyNot(ri, ci) {
      const r = VR[ri], c = VC[ci]; if (!r || !c) return { why: "" };
      if (c.sum) return { why: "This is the total of the places under it. Expand it to edit a place." };
      if (cellOf(r.code, c.id) == null) return { why: naWhy(r) };
      const lk = lockOf(r.code, c.id); if (lk) return { why: lk.why, kind: lk.kind };
      return null;
    }
    function explain(ri, ci) {
      const w = whyNot(ri, ci); if (!w) return;
      const r = VR[ri], c = VC[ci];
      if (w.kind === "entries" && ri === sel.r && ci === sel.c && runsTarget()) { say(esc(w.why.replace(/ Add or cancel them in the item's Measurement sheet\.$/, "")) + ". Add or cancel entries in the measurement sheet below.", false, 9000); return; }
      say(esc(w.why) + (w.kind === "entries" && OPTS.openEntries ? ` <button type="button" data-open="${esc(r.code)}|${esc(c.id)}">Open measurement sheet</button>` : ""), true, 9000);
    }
    $("#msg").addEventListener("click", (e) => { const b = e.target.closest("[data-open]"); if (!b || !OPTS.openEntries) return; const [code, room] = b.dataset.open.split("|"); OPTS.openEntries(svc, D().by.get(code), room); });

    // ---------------- formula bar ----------------
    function paintFbar() {
      const fx = $("#fx"), res = $("#res"), nm = $("#nm");
      res.textContent = ""; res.className = "res";
      if (!selOn()) { nm.textContent = "Click a box"; fx.value = ""; fx.disabled = true; return; }
      const r = VR[sel.r], c = VC[sel.c];
      nm.textContent = `${r.desc} · ${c.sum ? (groups.find((g) => g.id === c.id) || {}).name : placeLabel(c.id)}`; nm.title = nm.textContent;
      const w = whyNot(sel.r, sel.c);
      if (w) { fx.value = c.sum ? raw(valueAt(sel.r, sel.c)) : raw(cellOf(r.code, c.id)); fx.disabled = true; res.textContent = w.why.length > 60 ? w.why.slice(0, 57) + "…" : w.why; res.title = w.why; return; }
      res.removeAttribute("title");
      if (document.activeElement !== host || SR.activeElement !== fx) fx.value = exprOf(r.code, c.id) || raw(cellOf(r.code, c.id));
      fx.disabled = false;
      res.textContent = "= " + fmt(cellOf(r.code, c.id)) + " " + unitOf(r) + (exprOf(r.code, c.id) ? " (formula kept)" : "");
    }
    $("#fx").addEventListener("input", (e) => { if (!selOn()) return; const r = VR[sel.r]; const x = evalExpr(e.target.value, r.count), res = $("#res"); if (!x.ok) { res.textContent = x.err; res.className = "res err"; } else { res.className = "res"; res.textContent = x.empty ? "" : "= " + fmt(x.v) + " " + unitOf(r) + (x.pieces > 1 ? " (" + x.pieces + " pieces)" : ""); } });
    $("#fx").addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); if (selOn()) { const r = sel.r, c = sel.c; commitText(r, c, e.target.value, 0); $("#grid").focus({ preventScroll: true }); select(r + 1, c); } }
      else if (e.key === "Escape") { paintFbar(); $("#grid").focus({ preventScroll: true }); }
    });

    // ---------------- editing in the box ----------------
    function startEdit(ri, ci, seed, caretEnd) {                  // caretEnd (F2, double-click): keep the text and put the cursor at its end, like Excel
      const w = whyNot(ri, ci); if (w) { if (!toRunSheet(ri, ci)) explain(ri, ci); return; }
      const td = tdAt(ri, ci); if (!td) return;
      const r = VR[ri], c = VC[ci];
      const orig = exprOf(r.code, c.id) || raw(cellOf(r.code, c.id));
      edit = { ri, ci, orig, done: false };
      td.innerHTML = ""; const inp = document.createElement("input"); inp.className = "ed"; inp.setAttribute("aria-label", "Quantity"); inp.setAttribute("inputmode", "decimal");
      inp.value = seed != null ? seed : (orig === "0" ? "" : orig); td.appendChild(inp); inp.focus();
      if (seed == null && !caretEnd) inp.select(); else inp.setSelectionRange(inp.value.length, inp.value.length);
      const live = () => { const x = evalExpr(inp.value, r.count), res = $("#res"); $("#fx").value = inp.value; if (!x.ok) { res.textContent = x.err; res.className = "res err"; } else { res.className = "res"; res.textContent = x.empty ? "" : "= " + fmt(x.v) + " " + unitOf(r) + (x.pieces > 1 ? " (" + x.pieces + " pieces)" : ""); } };
      inp.addEventListener("input", live); live();
      inp.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); finishEdit(e.shiftKey ? -1 : 1, 0); }
        else if (e.key === "Tab") { e.preventDefault(); e.stopPropagation(); finishEdit(0, e.shiftKey ? -1 : 1); }
        else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); endEdit(false); }
      });
      inp.addEventListener("blur", () => { if (edit && !edit.done) finishEdit(0, 0, true); });
    }
    function endEdit(commit) { if (!edit) return; const e = edit; edit = null; e.done = true; const inp = $("td input.ed"); const val = inp ? inp.value : e.orig; const td = tdAt(e.ri, e.ci); if (td && VR[e.ri]) paintRow(VR[e.ri].code); if (commit) commitText(e.ri, e.ci, val, 0); paintFbar(); return e; }
    function finishEdit(dr, dc, fromBlur) { const e = edit; if (!e) return; const r = e.ri, c = e.ci; endEdit(true); if (!fromBlur) { $("#grid").focus({ preventScroll: true }); select(r + dr, c + dc); } }

    // parse the typed text of ONE box and save it
    function commitText(ri, ci, text, _unused) {
      const r = VR[ri], c = VC[ci]; if (!r || !c) return;
      const x = evalExpr(text, r.count);
      if (!x.ok) { say(esc(x.err), true, 7000); return; }
      const cur = cellOf(r.code, c.id);
      const val = x.empty ? 0 : x.v, ex = x.empty ? null : exprText(text);
      if (cur != null && Math.abs(cur - val) < 1e-9 && (ex || null) === (exprOf(r.code, c.id) || null)) return;      // same number and same sum: nothing to save
      apply([{ code: r.code, col: c.id, qty: val, expr: ex }], "type");
    }

    // ---------------- saving: validate, ask before cutting a plan, save, refresh the line ----------------
    async function ensurePlan(code) {
      const key = svc + "|" + code; if (planCache.has(key)) return planCache.get(key);
      const p = await OPTS.loadPlan(svc, code); planCache.set(key, p); return p;
    }
    // A quantity above a zone's plan (an equal share of the item's total) is real work, not a mistake: the item's per-zone plan is removed by itself and
    // the item's own total stays the one limit. Nothing recorded changes. The person is told in one line, never asked.
    async function dropPlan(r, code) {
      await OPTS.planOff(svc, code); planCache.delete(svc + "|" + code); changed = true; r.free = true;
      return `Plan of ${code} removed: a zone can hold any quantity, the item's total${r.plan > 0 ? " (" + fmt(r.plan) + " " + unitOf(r) + ")" : ""} is the limit`;
    }
    // changes: [{code, col, qty}] ; kind: "type" | "undo" | "redo"
    function apply(changes, kind, note) {
      pending++; setState();
      chain = chain.then(() => run(changes, kind, note)).catch(() => {}).then(() => { pending--; setState(); });
      return chain;
    }
    async function run(changes, kind, note) {
      const byCode = new Map(); changes.forEach((ch) => { (byCode.get(ch.code) || byCode.set(ch.code, []).get(ch.code)).push(ch); });
      const hist = [], planNotes = []; let saved = 0, pieces = 0;
      for (const [code, list] of byCode) {
        const r = D().by.get(code); if (!r) continue;
        let plan;
        try { plan = await ensurePlan(code); } catch (err) { failed++; say("Could not check the plan of " + esc(code) + ": " + esc(errText(err)), true, 9000); continue; }
        // every box must be a place the item is in and not locked; the plan of the zone is a limit
        const ok = []; const over = [];
        for (const ch of list) {
          const lk = lockOf(code, ch.col);
          if (cellOf(code, ch.col) == null || lk) { say(esc(lk ? lk.why : "This item is not in " + placeLabel(ch.col) + "."), true, 8000); continue; }
          const pl = plan.rooms && plan.rooms[ch.col] ? plan.rooms[ch.col].planned : null;
          if (pl == null) { say(esc(placeLabel(ch.col)) + " has no planned quantity for " + esc(code) + ", so a quantity cannot be recorded there. Set the plan first.", true, 9000); continue; }
          if (!plan.free && ch.qty > pl + 1e-6) over.push({ col: ch.col, qty: ch.qty, planned: pl }); else ok.push(ch);
        }
        if (over.length) {
          try { planNotes.push(await dropPlan(r, code)); plan = await ensurePlan(code); }
          catch (err) { failed++; say(esc(errText(err)), true, 10000); continue; }
          over.forEach((o) => ok.push(list.find((x) => x.col === o.col)));
        }
        if (!ok.length) continue;
        // optimistic: show the numbers now, the server's own figures replace them in a moment
        const before = ok.map((ch) => ({ code, col: ch.col, from: cellOf(code, ch.col), fromExpr: exprOf(code, ch.col), to: ch.qty }));
        ok.forEach((ch) => { const k = code + "|" + ch.col; D().cells[k] = ch.qty; if (ch.expr) D().exprs[k] = ch.expr; else delete D().exprs[k]; }); recalc(r); paintRow(code);
        ok.forEach((ch) => { const td = cellEl(code, ch.col); if (td) td.classList.add("saving"); });
        try {
          await OPTS.save(svc, code, ok.map((ch) => ({ room: ch.col, qty: ch.qty, expr: ch.expr || null })));
          changed = true; planCache.delete(svc + "|" + code);
          const fresh = await OPTS.load(svc, code);
          mergeItem(code, fresh); runCache.delete(svc + "|" + code); rs.ver++; paintRow(code);
          ok.forEach((ch) => { const td = cellEl(code, ch.col); if (td) { td.classList.add("ok"); setTimeout(() => td.classList.remove("ok"), 900); } });
          saved += ok.length; pieces += ok.filter((ch) => ch.expr).length; hist.push(...before);
        } catch (err) {
          failed++; before.forEach((b) => { const k = code + "|" + b.col; if (b.from == null) delete D().cells[k]; else D().cells[k] = b.from; if (b.fromExpr) D().exprs[k] = b.fromExpr; else delete D().exprs[k]; });
          recalc(r); paintRow(code);
          ok.forEach((ch) => { const td = cellEl(code, ch.col); if (td) { td.classList.add("bad"); setTimeout(() => td.classList.remove("bad"), 4000); } });
          say("Not saved: " + esc(errText(err)), true, 12000);
          try { mergeItem(code, await OPTS.load(svc, code)); paintRow(code); } catch (e2) { /* keep the reverted figures */ }
          setTimeout(() => { if (failed > 0) { failed--; setState(); } }, 8000);
        }
      }
      if (hist.length) {
        if (kind === "type") { undo.push(hist); redo = []; } else if (kind === "undo") redo.push(hist); else if (kind === "redo") undo.push(hist);   // `from` is always the value to put back
        say(`Saved ${saved} box${saved > 1 ? "es" : ""}${pieces ? ". The sum is kept in the box: click it to see the formula" : ""}${note ? ". " + esc(note) : ""}${planNotes.length ? ". " + esc(planNotes.join(". ")) : ""}`, false, note || planNotes.length ? 9000 : 4500);
      }
      setState();
    }
    const cellEl = (code, col) => { const tr = rowEl(code); if (!tr) return null; const ci = VC.findIndex((c) => c.id === col); return ci < 0 ? null : tr.cells[3 + ci] || null; };
    function mergeItem(code, fresh) {
      const d = D(); const nr = (fresh.rows || []).find((x) => x.code === code); if (!nr) return;
      const i = d.rows.findIndex((x) => x.code === code); if (i >= 0) d.rows[i] = nr; d.by.set(code, nr);
      Object.keys(d.cells).forEach((k) => { if (k.startsWith(code + "|")) delete d.cells[k]; }); Object.assign(d.cells, fresh.cells || {});
      Object.keys(d.locks).forEach((k) => { if (k.startsWith(code + "|")) delete d.locks[k]; }); Object.assign(d.locks, fresh.locks || {});
      Object.keys(d.exprs).forEach((k) => { if (k.startsWith(code + "|")) delete d.exprs[k]; }); Object.assign(d.exprs, fresh.exprs || {});
    }
    function doUndo(from, to, kind) { const a = from.pop(); if (!a) return; apply(a.map((h) => ({ code: h.code, col: h.col, qty: h.from == null ? 0 : h.from, expr: h.fromExpr || null })), kind); setState(); }

    // ---------------- keyboard ----------------
    const printable = (e) => e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey;
    $("#grid").addEventListener("keydown", (e) => {
      if (edit) return;
      if (!selOn()) { if (["ArrowDown", "ArrowRight", "Tab", "Enter"].includes(e.key)) { e.preventDefault(); select(0, 0); } return; }
      const mod = e.ctrlKey || e.metaKey, ext = e.shiftKey;
      const base = ext ? { r: sel.r2, c: sel.c2 } : { r: sel.r, c: sel.c };
      switch (e.key) {
        case "ArrowDown": e.preventDefault(); select(mod ? VR.length - 1 : base.r + 1, base.c, ext); return;
        case "ArrowUp": e.preventDefault(); select(mod ? 0 : base.r - 1, base.c, ext); return;
        case "ArrowRight": e.preventDefault(); select(base.r, mod ? VC.length - 1 : base.c + 1, ext); return;
        case "ArrowLeft": e.preventDefault(); select(base.r, mod ? 0 : base.c - 1, ext); return;
        case "Tab": e.preventDefault(); select(sel.r, sel.c + (e.shiftKey ? -1 : 1)); return;
        case "Enter": e.preventDefault(); if (toRunSheet(sel.r, sel.c)) return; if (whyNot(sel.r, sel.c)) { explain(sel.r, sel.c); select(sel.r + (e.shiftKey ? -1 : 1), sel.c); } else startEdit(sel.r, sel.c); return;
        case "F2": e.preventDefault(); if (!toRunSheet(sel.r, sel.c)) startEdit(sel.r, sel.c, null, true); return;
        case "Home": e.preventDefault(); select(sel.r, 0, ext); return;
        case "End": e.preventDefault(); select(sel.r, VC.length - 1, ext); return;
        case "Escape": sel.r2 = sel.r; sel.c2 = sel.c; paintSel(); return;
        case "Delete": case "Backspace": e.preventDefault(); clearRange(); return;
      }
      if (mod) {
        const k = e.key.toLowerCase();
        if (k === "z" && !e.shiftKey) { e.preventDefault(); doUndo(undo, redo, "undo"); }
        else if (k === "y" || (k === "z" && e.shiftKey)) { e.preventDefault(); doUndo(redo, undo, "redo"); }
        else if (k === "d") { e.preventDefault(); fillDown(); }
        else if (k === "a") { e.preventDefault(); sel = { r: 0, c: 0, r2: VR.length - 1, c2: VC.length - 1 }; paintSel(); }
        return;
      }
      if (printable(e) && /[0-9.(=]/.test(e.key)) { e.preventDefault(); if (!toRunSheet(sel.r, sel.c)) startEdit(sel.r, sel.c, e.key); }
      else if (printable(e) && e.key === "-") { e.preventDefault(); say("A minus does not work. Type the quantity, for example 40 or 37+9.4", true, 5000); }
    });
    function editableAt(ri, ci) { return !whyNot(ri, ci); }
    function clearRange() {
      const g = rng(), ch = []; let skipped = 0;
      for (let i = g.r1; i <= g.r2; i++) for (let j = g.c1; j <= g.c2; j++) { if (editableAt(i, j)) { if ((cellOf(VR[i].code, VC[j].id) || 0) !== 0) ch.push({ code: VR[i].code, col: VC[j].id, qty: 0 }); } else skipped++; }
      if (!ch.length) { if (skipped && g.r1 === g.r2 && g.c1 === g.c2) explain(g.r1, g.c1); return; }
      apply(ch, "type", skipped ? `${skipped} locked box${skipped > 1 ? "es were" : " was"} skipped` : "");
    }
    function fillDown() {
      const g = rng(), ch = []; if (g.r1 === g.r2) { say("Select two or more boxes in a column to fill down", false, 4000); return; }
      for (let j = g.c1; j <= g.c2; j++) { const src = valueAt(g.r1, j); if (src == null) continue; for (let i = g.r1 + 1; i <= g.r2; i++) if (editableAt(i, j) && VR[i].count !== true || (editableAt(i, j) && Number.isInteger(src))) ch.push({ code: VR[i].code, col: VC[j].id, qty: src }); }
      if (ch.length) apply(ch, "type");
    }
    $("#undo").onclick = () => doUndo(undo, redo, "undo"); $("#redo").onclick = () => doUndo(redo, undo, "redo");

    // ---------------- clipboard (Excel <-> grid) ----------------
    $("#grid").addEventListener("copy", (e) => {
      if (edit || !selOn()) return; const g = rng(), rows = [];
      for (let i = g.r1; i <= g.r2; i++) { const a = []; for (let j = g.c1; j <= g.c2; j++) { const v = valueAt(i, j); a.push(v == null ? "" : raw(v)); } rows.push(a.join("\t")); }
      e.clipboardData.setData("text/plain", rows.join("\n")); e.preventDefault(); say(`${(g.r2 - g.r1 + 1) * (g.c2 - g.c1 + 1)} boxes copied`, false, 2500);
    });
    $("#grid").addEventListener("paste", (e) => {
      if (edit || !selOn()) return;
      e.preventDefault(); const text = ((e.clipboardData && e.clipboardData.getData("text/plain")) || "").replace(/\r/g, ""); if (!text.trim()) return;
      let rows = text.split("\n"); if (rows.length && rows[rows.length - 1] === "") rows.pop();
      const grid = rows.map((r) => r.split("\t").map((x) => x.trim().replace(/^"(.*)"$/, "$1")));
      const g = rng(), fillAll = grid.length === 1 && grid[0].length === 1 && (g.r2 > g.r1 || g.c2 > g.c1);
      const H = fillAll ? g.r2 - g.r1 + 1 : grid.length, W = fillAll ? g.c2 - g.c1 + 1 : Math.max(...grid.map((r) => r.length));
      const ch = []; let skipped = 0, bad = 0, clipped = 0, firstBad = "";
      for (let i = 0; i < H; i++) for (let j = 0; j < W; j++) {
        const ri = g.r1 + i, ci = g.c1 + j; if (ri >= VR.length || ci >= VC.length) { clipped++; continue; }
        const txt = fillAll ? grid[0][0] : (grid[i] && grid[i][j] != null ? grid[i][j] : "");
        if (txt === "") continue;
        if (!editableAt(ri, ci)) { skipped++; continue; }
        const x = evalExpr(txt.replace(/^(\d{1,3}(,\d{3})+(\.\d+)?)$/, (m) => m.replace(/,/g, "")), VR[ri].count);
        if (!x.ok || x.empty) { bad++; firstBad = firstBad || x.err || ""; continue; }
        if (Math.abs((cellOf(VR[ri].code, VC[ci].id) || 0) - x.v) < 1e-9) continue;
        ch.push({ code: VR[ri].code, col: VC[ci].id, qty: x.v, expr: exprText(txt) });
      }
      const bits = []; if (skipped) bits.push(`${skipped} locked or not in this place, skipped`); if (bad) bits.push(`${bad} not a valid quantity (${firstBad})`); if (clipped) bits.push(`${clipped} outside the sheet`);
      if (ch.length) apply(ch, "type", bits.join(" · "));
      else say(esc(["Nothing was pasted"].concat(bits).join(" · ")), true, 8000);
    });

    // ---------------- mouse ----------------
    const gridEl = $("#grid");
    gridEl.addEventListener("mousedown", (e) => {
      if (e.target.closest("input")) return;
      const th = e.target.closest("th[data-g]");
      if (th) return;
      const tr = e.target.closest("tr[data-code]"); if (!tr) return;
      const ri = VR.findIndex((r) => r.code === tr.dataset.code);
      const td = e.target.closest("td.c");
      if (edit) endEdit(true);
      gridEl.focus({ preventScroll: true });
      if (td) { e.preventDefault(); const ci = VC.findIndex((c) => c.id === td.dataset.col); select(ri, ci, e.shiftKey); drag = true; if (whyNot(ri, ci) && !e.shiftKey && !td.dataset.sum) explain(ri, ci); }
      else if (e.target.closest("td.rl")) { e.preventDefault(); sel = { r: ri, c: 0, r2: ri, c2: VC.length - 1 }; paintSel(); }
    });
    SR.addEventListener("mouseup", () => { drag = false; });
    document.addEventListener("mouseup", onUp);
    // a level heading opens / closes on a click; a double-click renames it, so the toggle waits a moment to see whether a second click follows
    let toggleT = null;
    gridEl.addEventListener("click", (e) => {
      const th = e.target.closest("th[data-g]"); if (!th || th.querySelector("input")) return;
      clearTimeout(toggleT); if (e.detail > 1) return;
      const id = th.dataset.g;
      toggleT = setTimeout(() => { const g = groups.find((x) => x.id === id); if (g) { g.open = !g.open; sel = { r: -1, c: -1, r2: -1, c2: -1 }; paintGrid(); } }, 220);
    });
    gridEl.addEventListener("mouseover", (e) => {
      const tr = e.target.closest("tr[data-code]");
      if (!tr) { if (hov.code) applyHover(null, null); return; }
      const td = e.target.closest("td.c"); const code = tr.dataset.code, col = td ? td.dataset.col : null;
      if (drag && td) { const ri = VR.findIndex((r) => r.code === code), ci = VC.findIndex((c) => c.id === col); if (ri >= 0 && ci >= 0) { sel.r2 = ri; sel.c2 = ci; paintSel(); } }
      if (code !== hov.code || col !== hov.col) applyHover(code, col);
    });
    gridEl.addEventListener("mouseleave", () => { if (hov.code) applyHover(null, null); });
    gridEl.addEventListener("dblclick", (e) => {
      const th = e.target.closest("th[data-lc],th[data-g]"); if (th && !th.querySelector("input")) { e.preventDefault(); clearTimeout(toggleT); startRename(th); return; }
      const td = e.target.closest("td.c"); if (td && !e.target.closest("input")) { const tr = td.closest("tr[data-code]"); const ri = VR.findIndex((r) => r.code === tr.dataset.code), ci = VC.findIndex((c) => c.id === td.dataset.col); if (ri >= 0 && ci >= 0) startEdit(ri, ci, null, true); }
    });
    $("#expand").onclick = () => { groups.forEach((g) => { if (g.kids) g.open = true; }); sel = { r: -1, c: -1, r2: -1, c2: -1 }; paintGrid(); };
    $("#collapse").onclick = () => { groups.forEach((g) => { if (g.kids) g.open = false; }); sel = { r: -1, c: -1, r2: -1, c2: -1 }; paintGrid(); };
    $("#q").addEventListener("input", (e) => { q = e.target.value; sel = { r: -1, c: -1, r2: -1, c2: -1 }; paintGrid(); paintDock(); });

    // ---------------- rename a level / zone (the id never changes, so recorded quantities stay) ----------------
    function startRename(th) {
      const id = th.dataset.lc || th.dataset.g; const leaf = leaves().find((l) => l.id === id), grp = groups.find((g) => g.id === id);
      const kidOf = groups.flatMap((g) => g.kids || []).find((k) => k.id === id); const cur = kidOf ? (kidOf.raw != null ? kidOf.raw : kidOf.name) : grp ? grp.name : ""; if (!OPTS.renamePlace) return;
      const inp = document.createElement("input"); inp.className = "rn"; inp.value = cur; inp.setAttribute("aria-label", "New name");
      th.textContent = ""; th.appendChild(inp); inp.focus(); inp.select();
      let done = false;
      const finish = async (commit) => {
        if (done) return; done = true; const name = inp.value.trim();
        if (!commit || !name || name === cur) { paintGrid(); return; }
        const parent = groups.find((g) => g.kids && g.kids.some((k) => k.id === id));
        const peers = parent ? parent.kids.filter((k) => k.id !== id).map((k) => k.raw != null ? k.raw : k.name) : groups.filter((g) => g.id !== id).map((g) => g.name);
        if (peers.some((n) => String(n).toLowerCase() === name.toLowerCase())) { say(esc(name) + " is already used " + (parent ? "in " + esc(parent.name) : "here") + ". Pick a different name.", true, 6000); paintGrid(); return; }
        try { await OPTS.renamePlace(id, name); } catch (err) { say("Could not rename: " + esc(errText(err)), true, 9000); paintGrid(); return; }
        groups.forEach((g) => { if (g.id === id) g.name = name; (g.kids || []).forEach((k) => { if (k.id === id) { const pre = k.raw != null ? k.name.slice(0, k.name.length - k.raw.length) : ""; k.raw = name; k.name = pre + name; } }); });
        changed = true; paintGrid(); say("Renamed to " + esc(name) + ". Recorded quantities are untouched.", false, 5000);
      };
      inp.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter") { e.preventDefault(); finish(true); } else if (e.key === "Escape") { e.preventDefault(); finish(false); } });
      inp.addEventListener("blur", () => finish(true));
      inp.addEventListener("click", (e) => e.stopPropagation());
    }

    // ---------------- Excel: export the service, import a file made by it (preview first, then the normal save) ----------------
    const closeLayer = () => { $("#layer").innerHTML = ""; };
    $("#xexp").onclick = async () => {
      if (!OPTS.exportFile) return;
      const b = $("#xexp"); b.disabled = true;
      try { say("Preparing the Excel file…", false, 0); await chain; const name = await OPTS.exportFile(svc); say("Downloaded " + esc(name || "the Excel file") + ". It holds what is saved right now.", false, 7000); }
      catch (err) { say("Could not export: " + esc(errText(err)), true, 9000); }
      b.disabled = false;
    };
    $("#ximp").onclick = () => { if (OPTS.importPreview) { $("#xfile").value = ""; $("#xfile").click(); } };
    $("#xfile").addEventListener("change", async (e) => {
      const f = e.target.files && e.target.files[0]; if (!f) return;
      if (edit) endEdit(true);
      say("Reading " + esc(f.name) + "…", false, 0);
      let pv; try { pv = await OPTS.importPreview(svc, f); } catch (err) { say("Could not import: " + esc(errText(err)), true, 12000); return; }
      say("", false, 1); showPreview(pv, f.name);
    });
    function showPreview(pv, fname) {
      const k = pv.counts || {}, ch = pv.changes || [], sk = pv.skipped || [];
      const chip = (n, cls, t) => (n ? `<span class="chip ${cls}">${n} ${t}</span>` : "");
      let h = `<div class="chips">${chip(k.change, "g", k.change === 1 ? "box will change" : "boxes will change")}${chip(k.same, "s", "already the same")}${chip(k.over_plan, "a", "above their zone's equal-share plan (the plan of that item is then removed; its total stays the limit)")}${chip(k.locked, "a", "locked, skipped")}${chip(k.not_in_place, "a", "not in that place, skipped")}${chip(k.bad, "r", "not a valid quantity, skipped")}${chip(k.unknown_items, "a", "lines not in this BOQ, skipped")}${chip(k.unknown_places, "a", "columns not recognised, skipped")}</div>`;
      if (!ch.length) h += `<p>${(k.same || 0) && !sk.length ? "Nothing to change: the file matches the grid." : "Nothing in this file can be applied."}</p>`;
      else {
        h += `<div class="tw"><table class="tbl"><thead><tr><th>BOQ line</th><th>Place</th><th>Now</th><th>In file</th><th></th></tr></thead><tbody>`;
        ch.slice(0, 200).forEach((c) => { h += `<tr><td>${esc(c.code)} · ${esc(c.desc)}</td><td>${esc(c.place)}</td><td class="n">${fmt(c.cur)}</td><td class="n"><b>${fmt(c.new)}</b> ${esc(c.unit || "")}</td><td>${c.over_plan ? '<span class="chip a">above plan</span>' : ""}</td></tr>`; });
        h += `</tbody></table></div>`; if (ch.length > 200) h += `<div class="cap">Showing the first 200 of ${ch.length}. All of them are applied.</div>`;
      }
      if (sk.length || (pv.unknown_items || []).length) {
        h += `<details><summary>Why ${sk.length + (pv.unknown_items || []).length} were skipped</summary><div class="tw"><table class="tbl"><thead><tr><th>BOQ line</th><th>Place</th><th>Reason</th></tr></thead><tbody>`;
        sk.slice(0, 100).forEach((x) => { h += `<tr><td>${esc(x.code)}</td><td>${esc(x.place)}</td><td>${esc(x.why)}</td></tr>`; });
        (pv.unknown_items || []).slice(0, 50).forEach((c) => { h += `<tr><td>${esc(c)}</td><td></td><td>This line is not in this service's BOQ.</td></tr>`; });
        h += `</tbody></table></div></details>`;
      }
      $("#layer").innerHTML = `<div class="layer" role="dialog" aria-modal="true" aria-label="Import preview"><div class="sheet"><header><h2>Import preview · ${esc(fname)}</h2><button class="x" id="pvx" aria-label="Close">&times;</button></header><div class="bd">${h}</div><footer><span class="why">Nothing is saved until you apply. It is applied like typing, in one step you can undo.</span><button class="btn" id="pvno">Cancel</button><button class="btn pri" id="pvok"${ch.length ? "" : " disabled"}>Apply ${ch.length} change${ch.length === 1 ? "" : "s"}</button></footer></div></div>`;
      $("#pvx").onclick = $("#pvno").onclick = closeLayer;
      $("#layer").querySelector(".layer").addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); closeLayer(); } });
      $("#pvok").onclick = () => {
        closeLayer(); const skipped = (k.locked || 0) + (k.not_in_place || 0) + (k.bad || 0) + (k.unknown_items || 0) + (k.unknown_places || 0);
        apply(ch.map((c) => ({ code: c.code, col: c.room, qty: c.new, expr: c.expr || null })), "type", skipped ? skipped + " skipped (see the preview reasons)" : "");
        $("#grid").focus({ preventScroll: true });
      };
      (ch.length ? $("#pvok") : $("#pvno")).focus();
    }

    // ---------------- details under the grid (compact) ----------------
    function dockHTML(r, colId) {
      const u = esc(unitOf(r)); const base = r.boq_qty != null ? r.boq_qty : (r.plan > 0 ? r.plan : null);
      const basis = r.boq_qty != null ? "of BOQ" : "of site plan"; const pct = base ? Math.min(100, 100 * r.done / base) : 0;
      let h = `<div class="dk"><div><div class="cap">${esc(r.code)} &middot; ${u}</div><h2 title="${esc(r.desc)}">${esc(r.desc)}</h2>`;
      h += `<div><span class="big">${fmt(r.done)}</span> <span class="cap">${u} to date${base ? " &middot; " + (Math.round(1000 * r.done / base) / 10) + "% " + basis : ""} &middot; pending ${fmt(r.pending)}</span></div>`;
      h += `<div class="meter${r.done_above_boq ? " over" : ""}" role="img" aria-label="${fmt(r.done)} ${basis} ${fmt(base)}"><i style="width:${pct}%"></i></div>`;
      if (r.plan_above_boq) h += `<span class="tag a">Site plan ${fmt(r.plan)} is above the BOQ ${fmt(r.boq_qty)}</span>`;
      if (r.done_above_boq) h += `<span class="tag a">Done is above the BOQ quantity</span>`;
      h += `</div><div><div class="sec">BY LOCATION</div>`; let any = false;
      groups.forEach((g) => { const v = g.kids ? sumKids(r.code, g) : cellOf(r.code, g.id); if (v == null) return; any = true; h += `<div class="loc"><span>${esc(g.name)}</span><div class="b"><i style="width:${r.done > 0 ? Math.min(100, 100 * v / r.done) : 0}%"></i></div><span class="n">${fmt(v)}</span></div>`; });
      if (!any) h += `<div class="cap">This item is not in any place of this project.</div>`;
      const e = r.entries || { n: 0, backed: 0, other: 0, latest: [] };
      h += `</div><div><div class="sec">EVIDENCE</div>`;
      if (!(r.done > 0)) h += `<span class="tag s">Nothing recorded yet</span>`;
      else if (r.runs_ok) { if (e.n > 0) h += `<span class="tag g">${fmt(e.backed)} ${u} backed by ${e.n} measurement entr${e.n === 1 ? "y" : "ies"}</span>`; if (e.other > 0) h += `<span class="tag a">${fmt(e.other)} ${u} recorded without entries (zone tick or typed quantity)</span>`; }
      else h += `<span class="tag s">Counted in Site Progress. Measurement entries are for length items (Mtr, Rmt)</span>`;
      (e.latest || []).slice(0, 3).forEach((x) => { const dt = x.at ? new Date(x.at) : null; const when = dt && !isNaN(dt) ? dt.toLocaleDateString("en-GB", { day: "numeric", month: "short" }) : ""; h += `<div class="ent"><span>${esc(x.from)} &rarr; ${esc(x.to)} &middot; <b>${fmt(x.qty)}</b></span><span class="cap">${esc(x.place)}${when ? " &middot; " + when : ""}</span></div>`; });
      if (e.n > 3) h += `<div class="cap">${e.n} entries in all, in the item's Measurement sheet.</div>`;
      return h + "</div></div>";
    }
    // ---------------- the bar: place, only-lines-with-work, count, details ----------------
    function paintBar() {
      const sel_ = $("#plc"); if (!sel_) return;
      let h = '<option value="">All places</option>';
      groups.forEach((g) => { if (g.kids) h += `<optgroup label="${esc(g.name)}">${g.kids.map((k) => `<option value="${esc(k.id)}">${esc(k.name)}</option>`).join("")}</optgroup>`; else h += `<option value="${esc(g.id)}">${esc(g.name)}</option>`; });
      if (sel_.dataset.h !== h) { sel_.innerHTML = h; sel_.dataset.h = h; }
      sel_.value = place;
      $("#p0").disabled = $("#p1").disabled = !leaves().length;
      $("#expand").hidden = $("#collapse").hidden = !!place;
      const ow = $("#ow"); ow.setAttribute("aria-pressed", String(onlyWork)); ow.classList.toggle("on", onlyWork);
      const total = D().rows.length;
      $("#cnt").textContent = VR.length === total ? `${total} lines` : `${VR.length} of ${total} lines`;
      $("#details").setAttribute("aria-pressed", String(detailsOpen)); $("#details").classList.toggle("on", detailsOpen);
    }
    function setPlace(id) { if (edit) endEdit(false); place = id || ""; sel = { r: -1, c: -1, r2: -1, c2: -1 }; paintGrid(); $("#grid").focus({ preventScroll: true }); }
    $("#plc").addEventListener("change", (e) => setPlace(e.target.value));
    const stepPlace = (d) => { const L = leaves(); if (!L.length) return; const i = L.findIndex((x) => x.id === place); setPlace(L[i < 0 ? (d > 0 ? 0 : L.length - 1) : (i + d + L.length) % L.length].id); };
    $("#p0").onclick = () => stepPlace(-1); $("#p1").onclick = () => stepPlace(1);
    $("#ow").onclick = () => { onlyWork = !onlyWork; if (edit) endEdit(false); sel = { r: -1, c: -1, r2: -1, c2: -1 }; paintGrid(); paintMobile(); };
    $("#details").onclick = () => { detailsOpen = !detailsOpen; $("#dock").hidden = !detailsOpen; paintBar(); paintDock(); };
    function paintDock() {
      if (!detailsOpen) { paintRuns(); return; }
      const r = selOn() ? VR[sel.r] : (VR.length === 0 ? null : null);
      $("#dock").innerHTML = r ? dockHTML(r, null) : `<span class="hint">Click a box to see its line here: how much is done, where, and what it is based on.</span>`;
      paintRuns();
    }

    // ---------------- inline measurement sheet (length items): From, To, Start, End, Length ----------------
    // The same rows the engineer's own sheet has: where the run starts and ends, the drum readings, and Length = |Start - End|. Each row is
    // ADDED to the box (the server does the adding and the plan check); a cancelled row stays on the sheet, struck through.
    const runCache = new Map();                                   // "svc|code" -> GET /progress/runs
    const rs = { drawn: "", ver: 0, loading: new Set(), last: { from: "", to: "" }, note: "" };
    function runsTarget() {
      if (!OPTS.loadRuns || !selOn()) return null;
      const r = VR[sel.r], c = VC[sel.c];
      if (!r || !c || c.sum || !r.runs_ok || cellOf(r.code, c.id) == null) return null;
      const lk = lockOf(r.code, c.id); if (lk && lk.kind !== "entries") return null;
      return { r, c };
    }
    const num = (t) => { const x = String(t == null ? "" : t).trim().replace(/,/g, ""); if (x === "") return null; const n = Number(x); return isFinite(n) ? n : NaN; };
    function runRowHTML(x, i, sc) {
      const cx = !!x.cancelled; const when = x.at ? String(x.at).slice(0, 10) : "";
      return `<tr class="${cx ? "x" : ""}" data-run="${esc(x.id)}"><td class="i">${i + 1}</td><td>${esc(x.from)}</td><td>${esc(x.to)}</td><td class="n">${x.start == null ? "" : esc(fmt(x.start))}</td><td class="n">${x.end == null ? "" : esc(fmt(x.end))}</td><td class="n" title="${x.start == null ? "typed length" : "|Start − End|"}">${esc(fmt(x.qty))}</td><td title="${esc(when)}">${esc(x.remarks || "")}${cx && x.reason ? " (" + esc(x.reason) + ")" : ""}</td><td class="a">${sc.writable || cx ? `<button type="button" data-${cx ? "restore" : "cancel"}="${esc(x.id)}">${cx ? "Restore" : "Cancel"}</button>` : ""}</td></tr>`;
    }
    function drawRuns(t, sc) {
      const el = $("#runs"), u = esc(unitOf(t.r)); const list = sc.runs || [];
      let h = `<div class="rh"><b>Measurement sheet</b><span>${esc(t.r.desc)} &middot; ${esc(placeLabel(t.c.id))}</span><span class="cap">Length = |Start &minus; End|. Each row is added to the box.</span><span class="tot">Entries ${esc(fmt(sc.active))} ${u} &middot; Box ${esc(fmt(cellOf(t.r.code, t.c.id)))} ${u}</span></div>`;
      h += `<table class="rt"><thead><tr><th class="n">#</th><th>From</th><th>To</th><th class="n">Start</th><th class="n">End</th><th class="n">Length (${u})</th><th>Remarks</th><th></th></tr></thead><tbody>`;
      list.forEach((x, i) => { h += runRowHTML(x, i, sc); });
      if (sc.other > 0) h += `<tr class="ot"><td class="i"></td><td colspan="4">Typed or ticked, no entries behind it</td><td class="n">${esc(fmt(sc.other))}</td><td colspan="2"></td></tr>`;
      if (sc.allowed) h += `<tr class="nw"><td class="i">+</td><td><input data-f="from" value="${esc(rs.last.from)}" placeholder="From" aria-label="From" maxlength="80"></td><td><input data-f="to" value="${esc(rs.last.to)}" placeholder="To" aria-label="To" maxlength="80"></td><td><input class="n" data-f="start" inputmode="decimal" placeholder="Start" aria-label="Start reading"></td><td><input class="n" data-f="end" inputmode="decimal" placeholder="End" aria-label="End reading"></td><td><input class="n" data-f="qty" inputmode="decimal" placeholder="type length" aria-label="Length"></td><td><input data-f="remarks" placeholder="Remarks" aria-label="Remarks" maxlength="200"></td><td class="a"><button type="button" class="go" data-add="1">Add</button></td></tr>`;
      h += "</tbody></table>";
      h += sc.allowed ? `<div class="rf" id="rmsg">Enter adds the row. Give both readings, or leave them empty and type the length.</div>` : `<div class="rf bad">${esc(sc.why || "New entries cannot be added here.")}</div>`;
      el.innerHTML = h;
    }
    function paintRuns() {
      const el = $("#runs"), root = $(".root"), t = runsTarget();
      if (!t) { rs.drawn = ""; el.hidden = true; el.innerHTML = ""; root.classList.remove("rp"); return; }
      el.hidden = false; root.classList.add("rp");
      const ck = svc + "|" + t.r.code, key = ck + "|" + t.c.id + "#" + rs.ver, data = runCache.get(ck);
      if (!data) {
        if (!rs.drawn.startsWith(ck + "|") && rs.drawn !== "load|" + ck) { rs.drawn = "load|" + ck; el.innerHTML = `<div class="rf">Loading the measurement sheet…</div>`; }
        if (!rs.loading.has(ck)) {
          rs.loading.add(ck);
          Promise.resolve().then(() => OPTS.loadRuns(svc, t.r.code)).then((d) => runCache.set(ck, d || { scopes: [] }), (err) => runCache.set(ck, { err: errText(err) }))
            .then(() => { rs.loading.delete(ck); rs.ver++; if (live) paintRuns(); });
        }
        return;
      }
      if (rs.drawn === key) return;
      rs.drawn = key;
      if (data.err) { el.innerHTML = `<div class="rf bad">Could not load the measurement sheet: ${esc(data.err)} <button type="button" class="btn sm" data-retry="1">Try again</button></div>`; return; }
      const sc = (data.scopes || []).find((x) => x.key === t.c.id);
      if (!sc) { el.innerHTML = `<div class="rf">This place does not take measurement entries for this item.</div>`; return; }
      drawRuns(t, sc);
    }
    function runField(f) { return $('#runs [data-f="' + f + '"]'); }
    function focusRunRow() { const i = runField("from"); if (!i) return false; i.focus(); i.select(); return true; }
    // the grid hands a locked-by-entries box to the sheet instead of only explaining why it is locked
    function toRunSheet(ri, ci) {
      const w = whyNot(ri, ci); if (!w || w.kind !== "entries" || !runsTarget()) return false;
      if (ri !== sel.r || ci !== sel.c) return false;
      return focusRunRow();
    }
    function readNewRun() {
      const v = (f) => { const i = runField(f); return i ? i.value : ""; };
      const start = num(v("start")), end = num(v("end")), qty = num(v("qty"));
      if ([start, end, qty].some((n) => Number.isNaN(n))) return { err: "Readings and length must be numbers" };
      const body = { from: v("from").trim(), to: v("to").trim(), remarks: v("remarks").trim() };
      if (!body.from || !body.to) return { err: "Say where the run starts and ends: fill From and To" };
      if (start != null || end != null) { if (start == null || end == null) return { err: "Give both readings (Start and End), or leave both empty and type the length" }; body.start = start; body.end = end; if (Math.abs(start - end) <= 0) return { err: "Start and End are the same, so the length is 0" }; }
      else if (qty != null) { if (!(qty > 0)) return { err: "The length must be more than 0" }; body.qty = qty; }
      else return { err: "Enter the Start and End readings, or type the length" };
      body.len = start != null ? Math.round(Math.abs(start - end) * 1e4) / 1e4 : qty;
      return { body };
    }
    const rmsg = (text, bad) => { const m = $("#rmsg"); if (m) { m.textContent = text; m.className = "rf" + (bad ? " bad" : ""); } };
    function mutateRun(kind, payload, len) {                                 // kind: "add" | "cancel"; serialized with the grid's own saves
      const t = runsTarget(); if (!t) return Promise.resolve();
      const s0 = svc, code = t.r.code, col = t.c.id; pending++; setState();
      chain = chain.then(async () => {
        let good = false, planNote = "";
        try {
          if (kind === "add") {
            // a run is a real measured length: above the zone's plan (an equal share) the plan is removed by itself, the item's total stays the limit
            let plan = null; try { plan = await ensurePlan(code); } catch (e3) { /* the server judges it */ }
            const pl = plan && plan.rooms && plan.rooms[col] ? plan.rooms[col].planned : null, have = cellOf(code, col) || 0;
            if (plan && !plan.free && pl != null && have + len > pl + 1e-6) planNote = await dropPlan(t.r, code);
            await OPTS.addRun(s0, code, Object.assign({ room: col }, payload));
          }
          else await OPTS.cancelRun(s0, code, Object.assign({ room: col }, payload));
          good = true; changed = true; planCache.delete(s0 + "|" + code); runCache.delete(s0 + "|" + code);
          const fresh = await OPTS.load(s0, code); const keep = svc; svc = s0; mergeItem(code, fresh); svc = keep;
          if (planNote) rs.note = planNote;
          try { runCache.set(s0 + "|" + code, await OPTS.loadRuns(s0, code)); } catch (e2) { /* the sheet reloads itself */ }
        } catch (err) { if (!good) { rmsg("Not saved: " + errText(err), true); say("Not saved: " + esc(errText(err)), true, 9000); } }
        if (good && svc === s0) {
          paintRow(code); const td = cellEl(code, col); if (td) { td.classList.add("ok"); setTimeout(() => td.classList.remove("ok"), 900); }
        }
        return good;
      }).catch(() => false).then((good) => { pending--; setState(); if (!good) return false; rs.ver++; rs.drawn = ""; paintFbar(); paintRuns(); if (kind === "add") { const i = runField("from"); if (i) { i.focus(); i.select(); } rmsg("Added. Next row: " + (rs.last.from || "From") + " → " + (rs.last.to || "To")); } return good; });
      return chain;
    }
    function submitRun() {
      const x = readNewRun(); if (x.err) { rmsg(x.err, true); return; }
      const b = x.body, len = b.len; delete b.len; rs.last = { from: b.from, to: b.to };
      mutateRun("add", b, len).then((good) => { if (good) { say("Added " + esc(fmt(len)) + " " + esc(unitOf(runsTarget() ? runsTarget().r : null)) + " to the box" + (rs.note ? ". " + esc(rs.note) : ""), false, rs.note ? 9000 : 4000); rs.note = ""; } });
    }
    $("#runs").addEventListener("input", (e) => {
      const f = e.target.dataset && e.target.dataset.f; if (f !== "start" && f !== "end") return;
      const a = num(runField("start").value), b = num(runField("end").value), q = runField("qty");
      if (a != null && b != null && !Number.isNaN(a) && !Number.isNaN(b)) { q.value = raw(Math.abs(a - b)); q.disabled = true; q.title = "|Start − End|"; }
      else { if (q.disabled) q.value = ""; q.disabled = false; q.title = ""; }
    });
    $("#runs").addEventListener("keydown", (e) => {
      const f = e.target.dataset && e.target.dataset.f; if (!f) return;
      if (e.key === "Enter") { e.preventDefault(); submitRun(); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); $("#grid").focus({ preventScroll: true }); }
    });
    $("#runs").addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b) return;
      if (b.dataset.add) submitRun();
      else if (b.dataset.cancel) mutateRun("cancel", { run_id: b.dataset.cancel, cancelled: true });
      else if (b.dataset.restore) mutateRun("cancel", { run_id: b.dataset.restore, cancelled: false });
      else if (b.dataset.retry) { const t = runsTarget(); if (t) { runCache.delete(svc + "|" + t.r.code); rs.drawn = ""; paintRuns(); } }
    });

    // ---------------- phone: pick a place, type a quantity per BOQ line ----------------
    function paintMobile() {
      const el = $("#mob"); if (!isMobile()) { el.innerHTML = ""; return; }
      const P = leaves(); if (!P.length) { el.innerHTML = '<div class="cap">No places in this project.</div>'; return; }
      if (mp >= P.length) mp = 0; const pl = P[mp];
      const t = mq.trim().toLowerCase(); const mfilter = onlyWork && !t && workAnywhere();
      const R = D().rows.filter((r) => t ? ((r.desc || "") + " " + r.code).toLowerCase().includes(t) : !mfilter || (cellOf(r.code, pl.id) || 0) > 0);
      let h = `<div class="mpick"><button class="btn" id="mp0" aria-label="Previous place">‹</button><select id="msel" aria-label="Place">${P.map((p, i) => `<option value="${i}"${i === mp ? " selected" : ""}>${esc(p.group && p.group !== p.name ? p.group + " ▸ " + p.name : p.name)}</option>`).join("")}</select><button class="btn" id="mp1" aria-label="Next place">›</button></div><input type="search" id="mq" placeholder="Find a BOQ line" value="${esc(mq)}" aria-label="Find a BOQ line"><div class="mcnt"><button class="btn sm${onlyWork ? " on" : ""}" id="mow" aria-pressed="${onlyWork}">Only lines with work</button><span class="cap">${R.length === D().rows.length ? R.length + " lines" : R.length + " of " + D().rows.length + " lines"}</span></div>${!R.length && mfilter ? '<div class="cap">Nothing is recorded in this place yet. Search a BOQ line to add its work.</div>' : ""}`;
      R.forEach((r) => {
        const v = cellOf(r.code, pl.id), lk = lockOf(r.code, pl.id);
        h += `<div class="mrow"><div class="nm">${esc(r.desc)}</div><input data-mr="${esc(r.code)}" value="${v == null ? "" : esc(exprOf(r.code, pl.id) || raw(v))}" inputmode="decimal" aria-label="${esc(r.desc)}"${v == null || lk ? " disabled" : ""}><div class="sub">${esc(r.code)} &middot; ${esc(unitOf(r))} &middot; to date ${fmt(r.done)}${r.boq_qty != null ? " of " + fmt(r.boq_qty) : ""}</div>${v == null ? `<div class="why">${esc(naWhy(r))}</div>` : lk ? `<div class="why">${esc(lk.why)}</div>` : ""}</div>`;
      });
      el.innerHTML = h;
      $("#mp0").onclick = () => { mp = (mp + P.length - 1) % P.length; paintMobile(); }; $("#mp1").onclick = () => { mp = (mp + 1) % P.length; paintMobile(); };
      $("#msel").onchange = (e) => { mp = +e.target.value; paintMobile(); };
      $("#mow").onclick = () => { onlyWork = !onlyWork; paintMobile(); paintGrid(); };
      $("#mq").addEventListener("input", (e) => { mq = e.target.value; const pos = e.target.selectionStart; paintMobile(); const n = $("#mq"); if (n) { n.focus(); n.setSelectionRange(pos, pos); } });
      el.querySelectorAll("input[data-mr]").forEach((inp) => inp.addEventListener("change", () => {
        const r = D().by.get(inp.dataset.mr); const x = evalExpr(inp.value, r && r.count);
        if (!x.ok) { inp.classList.add("bad"); say(esc(x.err), true, 6000); return; }
        inp.classList.remove("bad"); const val = x.empty ? 0 : x.v; const cur = cellOf(r.code, pl.id); if (cur != null && Math.abs(cur - val) < 1e-9 && (x.empty ? null : exprText(inp.value)) === exprOf(r.code, pl.id)) return;
        apply([{ code: r.code, col: pl.id, qty: val, expr: x.empty ? null : exprText(inp.value) }], "type").then(paintMobile);
      }));
    }

    function paint() { paintTabs(); paintGrid(); paintDock(); paintMobile(); setState(); }
    window.addEventListener("resize", onResize);
    if (OPTS.place) { place = String(OPTS.place); const k = leaves().findIndex((x) => x.id === place); if (k >= 0) mp = k; }    // opened from an item's "Done where": that place
    paint();
    if (OPTS.focus) { const ri = VR.findIndex((r) => r.code === OPTS.focus.code), ci = VC.findIndex((c) => c.id === OPTS.focus.place); if (ri >= 0 && ci >= 0) select(ri, ci); }
    live.refreshItem = async (service, code) => { if (!DS[service]) return; const fresh = await OPTS.load(service, code); const keep = svc; svc = service; mergeItem(code, fresh); svc = keep; planCache.delete(service + "|" + code); runCache.delete(service + "|" + code); rs.ver++; rs.drawn = ""; changed = true; if (svc === service) { paintRow(code); paintDock(); paintMobile(); } };
    live.__test = { select, groups, get sel() { return sel; }, get pending() { return pending; }, whenIdle: () => chain };
    return live;
  }
  window.MIView = { open };
})();
