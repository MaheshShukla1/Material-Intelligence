/* Measurement grid (Excel-like). Opens as a full-screen layer from Site Progress > Measurement.
   Shadow DOM: its CSS cannot touch the app and the app's CSS cannot touch it. Generated from the design prototype. */
(function () {
  "use strict";
  const CSS = ":host{all:initial;display:block;position:fixed;inset:0;z-index:70;--mg-bg:var(--bg,#faf9f7);--mg-panel:var(--panel,#fff);--mg-line:var(--line,#e7e5df);--mg-line2:var(--line2,#f0eee9);--mg-lock:#f3f2ee;--mg-ink:var(--ink,#1c1c1b);--mg-ink2:var(--ink2,#5f5e59);--mg-ink3:var(--ink3,#8b8983);--mg-acc:var(--violet,#453a9a);--mg-accbg:var(--violetbg,#ebe8fa);--mg-selbg:rgba(69,58,154,.09);--mg-hov:#f4f2fb;--mg-hovhead:#e7e3f6;--mg-dirty:#d98a00;--mg-red:var(--red,#a02622);--mg-redbg:var(--redbg,#fbe7e6);--mg-amber:var(--amber,#7d4c06);--mg-amberbg:var(--amberbg,#fbf1da);--mg-green:var(--green,#39631a);--mg-greenbg:var(--greenbg,#e8f2e0);--mg-btn:var(--ink,#1c1c1b);--mg-btnink:#fff;--mg-shadow:0 8px 28px rgba(0,0,0,.16);--mg-sans:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;--mg-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}\n\n\n*{box-sizing:border-box}\n[hidden]{display:none!important}\n.mgroot{margin:0;background:var(--mg-bg);color:var(--mg-ink);font:13.5px/1.45 var(--mg-sans);height:100%}\n#app{height:100%;display:flex;flex-direction:column;min-width:0}\nbutton{font:inherit;color:inherit;cursor:pointer}\nbutton:focus-visible,input:focus-visible,select:focus-visible,#grid:focus-visible{outline:2px solid var(--mg-acc);outline-offset:1px}\n.top{display:flex;align-items:center;gap:14px;padding:10px max(16px,env(safe-area-inset-left,0px));border-bottom:1px solid var(--mg-line);background:var(--mg-bg);flex-wrap:wrap}\n.mark{width:28px;height:28px;border-radius:7px;background:var(--mg-ink);color:var(--mg-bg);display:grid;place-items:center;font-weight:600;font-size:12px}\n.top h1{font-size:14.5px;margin:0;font-weight:600}\n.top p{margin:0;font-size:12px;color:var(--mg-ink3)}\n.sp{flex:1}\n.pill{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--mg-line);background:var(--mg-panel);border-radius:999px;padding:3px 10px;font-size:12px;color:var(--mg-ink2)}\n.pill b{font-weight:600;color:var(--mg-ink)}\n.dot{width:7px;height:7px;border-radius:50%;background:var(--mg-dirty)}\n.tabs{display:flex;gap:2px;padding:8px max(16px,env(safe-area-inset-left,0px)) 0;border-bottom:1px solid var(--mg-line);align-items:flex-end;flex-wrap:wrap}\n.tab{border:1px solid transparent;border-bottom:none;background:none;padding:8px 14px;border-radius:8px 8px 0 0;color:var(--mg-ink2);font-weight:500;margin-bottom:-1px}\n.tab[aria-selected=\"true\"]{background:var(--mg-panel);border-color:var(--mg-line);color:var(--mg-ink)}\n.tabs .sp{min-width:8px}\n.btn{border:1px solid var(--mg-line);background:var(--mg-panel);border-radius:8px;padding:6px 12px;font-weight:500;display:inline-flex;gap:6px;align-items:center}\n.btn:hover{background:var(--mg-hov)}\n.btn.pri{background:var(--mg-btn);color:var(--mg-btnink);border-color:var(--mg-btn)}\n.btn.pri[disabled]{opacity:.4;cursor:not-allowed}\n.btn.sm{padding:3px 9px;font-size:12.5px}\n.tabs .btn{margin-bottom:6px}\n.bar{display:flex;gap:8px;align-items:center;padding:8px max(16px,env(safe-area-inset-left,0px));flex-wrap:wrap}\n.bar input[type=search]{border:1px solid var(--mg-line);background:var(--mg-panel);color:var(--mg-ink);border-radius:8px;padding:5px 10px;width:190px;font:inherit}\n.legend{display:flex;gap:12px;font-size:12px;color:var(--mg-ink3);flex-wrap:wrap;align-items:center}\n.legend i{display:inline-block;width:11px;height:11px;border-radius:3px;border:1px solid var(--mg-line);vertical-align:-2px;margin-right:4px}\n.fbar{display:flex;gap:0;margin:0 max(16px,env(safe-area-inset-left,0px));border:1px solid var(--mg-line);border-radius:8px;background:var(--mg-panel);align-items:stretch;min-height:34px}\n.fbar .nm{padding:6px 12px;border-right:1px solid var(--mg-line);font-weight:500;min-width:220px;max-width:46%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--mg-ink2)}\n.fbar .fx{padding:6px 10px;color:var(--mg-ink3);font-family:var(--mg-mono);font-style:italic;border-right:1px solid var(--mg-line)}\n.fbar input{flex:1;border:none;background:none;color:var(--mg-ink);padding:6px 10px;font:13px var(--mg-mono);min-width:60px}\n.fbar input:focus-visible{outline:none}\n.fbar:focus-within{border-color:var(--mg-acc)}\n.fbar .res{padding:6px 12px;color:var(--mg-ink2);white-space:nowrap;border-left:1px solid var(--mg-line);font-family:var(--mg-mono)}\n.fbar .res.err{color:var(--mg-red);background:var(--mg-redbg)}\n.fbar .btn{border:none;border-left:1px solid var(--mg-line);border-radius:0 8px 8px 0}\n#gridwrap{flex:1;min-height:0;margin:10px max(16px,env(safe-area-inset-left,0px)) 0;border:1px solid var(--mg-line);border-radius:8px 8px 0 0;background:var(--mg-panel);overflow:auto;position:relative}\n#grid{outline:none;position:relative;display:inline-block;min-width:100%}\ntable{border-collapse:separate;border-spacing:0;font-size:13px}\nth,td{border-right:1px solid var(--mg-line2);border-bottom:1px solid var(--mg-line2);height:30px;padding:0 8px;white-space:nowrap;background:var(--mg-panel)}\nthead th{position:sticky;background:var(--mg-bg);font-weight:500;color:var(--mg-ink2);height:28px;z-index:5;text-align:center;border-bottom:1px solid var(--mg-line)}\nthead tr:nth-child(1) th{top:0}\nthead tr:nth-child(2) th{top:28px}\nth.grp{cursor:pointer;user-select:none;color:var(--mg-ink)}\nth.grp:hover{background:var(--mg-hovhead)}\nth.leaf{min-width:84px;max-width:120px;overflow:hidden;text-overflow:ellipsis;font-size:12px;cursor:pointer}\nth.hh{background:var(--mg-hovhead)!important;color:var(--mg-ink)}\n.sl{position:sticky;z-index:4}\nthead .sl{z-index:8}\ntd.rl{background:var(--mg-bg);color:var(--mg-ink);cursor:default}\ntd.rl.hh{background:var(--mg-hovhead)}\n.c0{left:0;width:62px;min-width:62px;max-width:62px;font-family:var(--mg-mono);font-size:12px;color:var(--mg-ink2)}\n.c1{left:62px;width:270px;min-width:270px;max-width:270px;overflow:hidden;text-overflow:ellipsis}\n.c2{left:332px;width:112px;min-width:112px;max-width:112px;border-right:1px solid var(--mg-line)!important;text-align:left}\n.r0{right:226px;width:68px;min-width:68px;max-width:68px}\n.r1{right:124px;width:102px;min-width:102px;max-width:102px}\n.r2{right:0;width:124px;min-width:124px;max-width:124px}\ntd.sr{border-left:1px solid var(--mg-line);text-align:right;font-family:var(--mg-mono);background:var(--mg-lock);font-weight:500}\ntd.sr.r1{border-left:1px solid var(--mg-line)}\n.u{display:inline-flex;gap:5px;align-items:center;font-size:11.5px;color:var(--mg-ink2)}\n.u b{font-weight:500;border:1px solid var(--mg-line);border-radius:5px;padding:0 6px;background:var(--mg-panel);color:var(--mg-ink)}\n.u s{text-decoration:none;color:var(--mg-acc);font-weight:500}\ntd.c{text-align:right;font-family:var(--mg-mono);cursor:cell;position:relative;min-width:84px;max-width:120px}\ntd.c.ro{background:var(--mg-lock);color:var(--mg-ink2);cursor:default}\ntd.c.sum{background:var(--mg-lock);color:var(--mg-ink2);font-style:italic}\ntd.c.hr:not(.sel){background:var(--mg-hov)}\ntd.c.hc:not(.sel){background:var(--mg-hov)}\ntd.c.hr.hc{background:var(--mg-hovhead)!important}\ntd.c.sel{background:var(--mg-selbg)}\ntd.c.dirty::after{content:\"\";position:absolute;right:0;top:0;border-style:solid;border-width:0 8px 8px 0;border-color:transparent var(--mg-dirty) transparent transparent}\ntd.c.err{background:var(--mg-redbg)!important;color:var(--mg-red);box-shadow:inset 0 0 0 1px var(--mg-red)}\ntd.c.multi{text-decoration:underline dotted var(--mg-ink3);text-underline-offset:3px}\ntd.c input.ed{position:absolute;inset:0;width:100%;height:100%;border:none;background:var(--mg-panel);color:var(--mg-ink);font:13px var(--mg-mono);text-align:right;padding:0 8px;outline:2px solid var(--mg-acc);outline-offset:-2px;z-index:2}\ntd.c.parts{cursor:pointer}\ntd.c.parts .pm{color:var(--mg-acc);font-size:10px;margin-right:5px}\ntd.warn{color:var(--mg-amber);background:var(--mg-amberbg)}\ntd.dash{color:var(--mg-ink3)}\n.ov{position:absolute;pointer-events:none;border:2px solid var(--mg-acc);z-index:3;display:none}\n.ov.cp{border-style:dashed;border-color:var(--mg-ink);z-index:3;animation:march .6s linear infinite}\n@keyframes march{to{border-color:var(--mg-acc)}}\n@media (prefers-reduced-motion: reduce){.ov.cp{animation:none}}\n.status{display:flex;gap:18px;align-items:center;padding:6px 14px;margin:0 max(16px,env(safe-area-inset-left,0px));border:1px solid var(--mg-line);border-top:none;background:var(--mg-bg);color:var(--mg-ink2);font-size:12px;flex-wrap:wrap;min-height:30px}\n.status b{color:var(--mg-ink);font-family:var(--mg-mono);font-weight:500}\n.status .keys{margin-left:auto;color:var(--mg-ink3)}\nkbd{font:11px var(--mg-mono);border:1px solid var(--mg-line);border-bottom-width:2px;border-radius:4px;padding:0 5px;background:var(--mg-panel);color:var(--mg-ink2)}\n.save{display:flex;gap:10px;align-items:center;padding:10px max(16px,env(safe-area-inset-left,0px));border-top:1px solid var(--mg-line);background:var(--mg-panel);flex-wrap:wrap}\n.save .msg{color:var(--mg-ink2)}\n.save .msg b{color:var(--mg-ink)}\n.save .bad{color:var(--mg-red);font-weight:500}\n.toast{position:fixed;left:50%;bottom:76px;transform:translateX(-50%);background:var(--mg-btn);color:var(--mg-btnink);padding:8px 14px;border-radius:8px;box-shadow:var(--mg-shadow);z-index:60;max-width:86vw;font-size:13px}\n.modal{position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:50;display:grid;place-items:center;padding:16px}\n.sheet{background:var(--mg-panel);border:1px solid var(--mg-line);border-radius:12px;box-shadow:var(--mg-shadow);width:min(1120px,100%);max-height:88vh;display:flex;flex-direction:column;min-width:0}\n.sheet header{display:flex;align-items:center;gap:10px;padding:14px 18px;border-bottom:1px solid var(--mg-line)}\n.sheet header h2{margin:0;font-size:15px;font-weight:600;flex:1}\n.sheet .bd{padding:16px 18px;overflow:auto;display:flex;flex-direction:column;gap:12px;min-width:0}\n.sheet footer{padding:12px 18px;border-top:1px solid var(--mg-line);display:flex;gap:10px;justify-content:flex-end;align-items:center}\n.x{border:none;background:none;font-size:20px;line-height:1;color:var(--mg-ink2)}\n.tw{overflow:auto;border:1px solid var(--mg-line);border-radius:8px}\n.tbl{border-collapse:collapse;width:100%}\n.tbl th,.tbl td{border:1px solid var(--mg-line2);padding:4px 8px;height:auto;background:var(--mg-panel);text-align:left}\n.tbl th{position:static;background:var(--mg-bg);font-size:12px;color:var(--mg-ink2)}\n.tbl td.n{text-align:right;font-family:var(--mg-mono)}\n.tbl input,.tbl select{width:68px;border:1px solid var(--mg-line);background:var(--mg-panel);color:var(--mg-ink);border-radius:5px;padding:3px 5px;font:12.5px var(--mg-mono);text-align:right}\n.tbl select{width:auto;max-width:240px;text-align:left;font-family:var(--mg-sans)}\n.tbl input.tx{width:170px;text-align:left;font-family:var(--mg-sans)}\n.tbl input[readonly]{background:var(--mg-lock);border-color:transparent}\n.chip{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px}\n.chip.g{background:var(--mg-greenbg);color:var(--mg-green)}\n.chip.a{background:var(--mg-amberbg);color:var(--mg-amber)}\n.chip.r{background:var(--mg-redbg);color:var(--mg-red)}\n.chip.s{background:var(--mg-lock);color:var(--mg-ink2)}\n.note{color:var(--mg-ink2);font-size:12.5px;margin:0}\n.drop{border:1.5px dashed var(--mg-line);border-radius:10px;padding:18px;text-align:center;color:var(--mg-ink2)}\n.drop.over{border-color:var(--mg-acc);background:var(--mg-accbg)}\n.mob{display:none}\n@media (max-width:720px){\n  .save{gap:6px}.save .sp{display:none}.save .msg{flex-basis:100%;order:-1;font-size:12.5px}.save .btn{padding:6px 11px}\n  #desk,.fbar,.bar .legend,.status .keys{display:none!important}\n  .mob{display:flex;flex-direction:column;flex:1;min-height:0;overflow:auto;padding:10px max(16px,env(safe-area-inset-left,0px)) 10px;gap:10px}\n  .top p{display:none}\n  .tab{padding:8px 10px}\n  .status{border:none;margin:0}\n  .tabs .btn span{display:none}\n}\n.mpick{display:flex;gap:8px;align-items:center}\n.mpick select{flex:1;min-width:0;border:1px solid var(--mg-line);background:var(--mg-panel);color:var(--mg-ink);border-radius:8px;padding:9px 10px;font:inherit}\n.mpick .btn{padding:9px 14px}\n.mrow{border:1px solid var(--mg-line);background:var(--mg-panel);border-radius:10px;padding:10px 12px;display:grid;grid-template-columns:minmax(0,1fr) 112px;gap:4px 10px;align-items:center}\n.mrow .nm{font-weight:500;min-width:0}\n.mrow .sub{grid-column:1/2;color:var(--mg-ink3);font-size:12px}\n.mrow input{border:1px solid var(--mg-line);background:var(--mg-panel);color:var(--mg-ink);border-radius:8px;padding:9px 10px;font:15px var(--mg-mono);text-align:right;width:100%}\n.mrow input.err{border-color:var(--mg-red);background:var(--mg-redbg)}\n.mrow .err-t{grid-column:1/3;color:var(--mg-red);font-size:12px}\n.mrow .btn{grid-column:2/3}\n";
  const BODY = "\n  <div class=\"top\" id=\"mgtop\"></div>\n  <div class=\"tabs\" role=\"tablist\" id=\"tabs\"></div>\n  <div id=\"desk\" style=\"display:flex;flex-direction:column;flex:1;min-height:0\">\n    <div class=\"bar\">\n      <button class=\"btn sm\" id=\"bExp\">Expand all</button>\n      <button class=\"btn sm\" id=\"bCol\">Collapse all</button>\n      <input type=\"search\" id=\"q\" placeholder=\"Item dhundo\" aria-label=\"Item dhundo\">\n      <button class=\"btn sm\" id=\"bUndo\" title=\"Ctrl+Z\">Undo</button>\n      <button class=\"btn sm\" id=\"bRedo\" title=\"Ctrl+Y\">Redo</button>\n      <button class=\"btn sm\" id=\"bPlace\" title=\"Nayi jagah jodo ya naam badlo\">+ Jagah / naam badlo</button>\n      <div class=\"legend\"><span><i style=\"background:var(--lock)\"></i>Auto / locked</span><span><i style=\"background:var(--selbg)\"></i>Selected</span><span><i style=\"border-color:var(--dirty);background:var(--dirty)\"></i>Not saved yet</span></div>\n    </div>\n    <div class=\"fbar\" id=\"fbar\">\n      <div class=\"nm\" id=\"nm\">Koi cell select nahi</div><div class=\"fx\">fx</div>\n      <input id=\"fx\" autocomplete=\"off\" spellcheck=\"false\" aria-label=\"Cell value\" placeholder=\"Cell select karo, ya seedha type karo\">\n      <div class=\"res\" id=\"res\"></div>\n      <button class=\"btn sm\" id=\"bParts\" hidden>Duct parts</button>\n    </div>\n    <div id=\"gridwrap\"><div id=\"grid\" tabindex=\"0\" aria-label=\"Measurement grid\"><div class=\"ov\" id=\"ov\"></div><div class=\"ov cp\" id=\"ovc\"></div><div id=\"tbl\"></div></div></div>\n    <div class=\"status\"><span id=\"hov\">Cell par jao</span><span id=\"stat\"></span><span class=\"keys\"><kbd>Ctrl</kbd>+<kbd>C</kbd> <kbd>V</kbd> <kbd>Z</kbd> \u00b7 <kbd>Backspace</kbd> clear \u00b7 <kbd>F2</kbd> edit \u00b7 <kbd>Ctrl</kbd>+<kbd>D</kbd> neeche bharo</span></div>\n  </div>\n  <div class=\"mob\" id=\"mob\"></div>\n  <div class=\"save\">\n    <button class=\"btn\" id=\"bExport\">Export Excel</button>\n    <button class=\"btn\" id=\"bImport\">Import Excel</button>\n    <div class=\"sp\"></div>\n    <div class=\"msg\" id=\"smsg\"></div>\n    <button class=\"btn\" id=\"bDisc\">Discard</button>\n    <button class=\"btn pri\" id=\"bSave\">Save</button>\n  </div>\n</div>\n<div id=\"layer\"></div>\n\n";
  let live = null;
  function ensureXLSX() {
    if (window.XLSX || document.getElementById("mg-xlsx")) return;
    const s = document.createElement("script"); s.id = "mg-xlsx"; s.src = "https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js"; s.async = true;
    document.head.appendChild(s);
  }
  function open(OPTS) {
    if (live) live.close();
    ensureXLSX();
    const host = document.createElement("div"); host.id = "mg-host"; document.body.appendChild(host);
    const SR = host.attachShadow({ mode: "open" });
    SR.innerHTML = `<style>${CSS}</style><div class="mgroot"><div id="app">${BODY}</div>`;
    const offs = [];
    const docOn = (n, f) => { document.addEventListener(n, f); offs.push(() => document.removeEventListener(n, f)); };
    const winOn = (n, f) => { window.addEventListener(n, f); offs.push(() => window.removeEventListener(n, f)); };
    const DATA = OPTS.datasets;
    const close = () => { offs.forEach((f) => f()); host.remove(); live = null; document.body.style.overflow = prevOv; if (OPTS.onClose) OPTS.onClose(); };
    const prevOv = document.body.style.overflow; document.body.style.overflow = "hidden";
    const esc0 = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    SR.getElementById("mgtop").innerHTML = `<button class="btn sm" id="mgback">&larr; ${esc0(OPTS.backLabel || "Back")}</button><div><h1>${esc0(OPTS.title || "")}</h1><p>${esc0(OPTS.subtitle || "")}</p></div><div class="sp"></div>${OPTS.pill ? `<span class="pill"><b>${esc0(OPTS.pill)}</b></span>` : ""}`;
    SR.getElementById("mgback").onclick = close;
    SR.addEventListener("keydown", (e) => { if (e.key === "Escape" && !SR.getElementById("layer").innerHTML) { /* Esc closes only when no dialog is open and grid is not editing */ } });
    live = { close };
    if (OPTS.start) { const i = DATA.findIndex((d) => d.id === OPTS.start); if (i > 0) DATA.unshift(DATA.splice(i, 1)[0]); }

'use strict';
const $=s=>SR.querySelector(s);
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmt=(v,dp)=>v==null?'':Number(v).toLocaleString('en-IN',{maximumFractionDigits:dp==null?2:dp});

/* ---------- state ---------- */
const T={}; DATA.forEach(t=>{t.runs=t.runs||{};t.saved=JSON.stringify({c:t.cells,p:t.parts,r:t.runs});t.undo=[];t.redo=[];t.sel={r:0,c:0,r2:0,c2:0,on:false};t.clip=null;T[t.id]=t});
let cur=DATA[0], edit=null, hov={r:-1,c:-1}, drag=false;
let VC=[], VR=[];           // visible columns / rows

function leafIds(t){const a=[];t.groups.forEach(g=>{if(g.kids)g.kids.forEach(k=>a.push(k));else a.push({id:g.id,name:g.name})});return a}
function visCols(t){const a=[];t.groups.forEach(g=>{
  if(!g.kids)a.push({id:g.id,name:g.name,g:null,single:true});
  else if(g.open)g.kids.forEach(k=>a.push({id:k.id,name:k.name,g:g.id}));
  else a.push({id:g.id,name:g.name,g:g.id,sum:true,kids:g.kids.map(k=>k.id)})});return a}

/* ---------- parsing ---------- */
function calc(s){ // + and * and ( ) only
  let i=0;
  const ws=()=>{while(s[i]===' ')i++};
  function num(){ws();if(s[i]==='('){i++;const v=add();ws();if(s[i]!==')')throw 0;i++;return v}
    const m=/^\d+(\.\d+)?|^\.\d+/.exec(s.slice(i));if(!m)throw 0;i+=m[0].length;return parseFloat(m[0])}
  function mul(){let v=num();ws();while(s[i]==='*'){i++;v*=num();ws()}return v}
  function add(){let v=mul();ws();while(s[i]==='+'){i++;v+=mul();ws()}return v}
  const v=add();ws();if(i!==s.length)throw 0;return v}
function splitTop(s){const out=[];let d=0,cur='';for(const ch of s){if(ch==='(')d++;if(ch===')')d--;if(ch==='+'&&d===0){out.push(cur);cur=''}else cur+=ch}out.push(cur);return out}
function evalRaw(raw,row){
  let s=String(raw==null?'':raw).trim();
  if(s==='')return{ok:true,v:null,pieces:[]};
  if(s[0]==='=')s=s.slice(1).trim();
  s=s.replace(/[×xX]/g,'*');
  if(/^\d{1,3}(,\d{3})+(\.\d+)?$/.test(s))s=s.replace(/,/g,'');
  if(/-/.test(s))return{ok:false,err:'Minus nahi chalta. Galat tukda hata ke sahi likho.'};
  if(!/^[0-9+*().\s]+$/.test(s))return{ok:false,err:'Sirf number, + aur × chalte hain.'};
  let v;try{v=calc(s)}catch(e){return{ok:false,err:'Formula adhoori hai. Jaise 10+2+30+7'}}
  v=Math.round(v*1e6)/1e6;
  const pieces=splitTop(s).map(p=>{try{return Math.round(calc(p.trim())*1e6)/1e6}catch(e){return NaN}});
  if(row.unit==='Nos'&&(pieces.some(p=>!Number.isInteger(p))))return{ok:false,err:'Nos me poora number chahiye (12.5 nahi).'};
  if(v>1e8)return{ok:false,err:'Itna bada number? Dobara dekho.'};
  return{ok:true,v,pieces}}

function partArea(p){const A=+p.A||0,B=+p.B||0,C=+p.C||0,D=+p.D||0,L=+p.L||0,Q=+p.Q||0,an=+p.ang||0;
  if(p.kind==='Elbow')return(((2*(A+B)*2*3.14*(L+A/2)*an/360+2*100*(A+B)))/1e6)+0.05;
  if(p.kind==='Dummy')return(A/1000)*(B/1000)*Q;
  return(A+B+C+D)*L*Q/1e6}
function partsOf(t,cid){return t.parts[cid]||[]}
function sqmCell(t,row,cid){const ps=partsOf(t,cid).filter(p=>+p.g===row.gauge);if(!ps.length)return null;return ps.reduce((a,p)=>a+partArea(p),0)}

/* value of one leaf cell in entry unit */
function runSum(t,row,cid){const es=(t.runs[cid]||[]).filter(e=>e.c===row.id);if(!es.length)return null;return es.reduce((a,e)=>a+(+e.n||0)*(+e.L||0),0)}
function cellVal(t,row,cid){
  if(row.parts)return{ok:true,v:sqmCell(t,row,cid),pieces:[],ro:true};
  if(row.run){const rs=runSum(t,row,cid);if(rs!=null)return{ok:true,v:Math.round(rs*1e6)/1e6,pieces:[],ro:true,list:true}}
  const raw=t.cells[row.id+'|'+cid];const r=evalRaw(raw,row);r.raw=raw||'';return r}
function groupSum(t,row,g){let s=null;for(const k of g.kids){const c=cellVal(t,row,k.id);if(c.ok&&c.v!=null)s=(s||0)+c.v}return s}
function rowTotal(t,row){let s=null,bad=false;for(const k of leafIds(t)){const c=cellVal(t,row,k.id);if(!c.ok){bad=true;continue}if(c.v!=null)s=(s||0)+c.v}
  if(s!=null&&row.mm)s=s/1000;return{v:s,bad}}
const num=(v,row)=>v==null?'':fmt(v,row.unit==='Nos'?0:2);

/* ---------- render ---------- */
function mount(){
  const t=cur;VC=visCols(t);
  const q=$('#q').value.trim().toLowerCase();
  VR=t.rows.filter(r=>!q||(r.name+' '+r.code).toLowerCase().includes(q));
  let h='<table><thead><tr>';
  h+='<th class="sl c0" rowspan="2">Sr</th><th class="sl c1" rowspan="2" style="text-align:left">BOQ line</th><th class="sl c2" rowspan="2" style="text-align:left">Unit</th>';
  t.groups.forEach(g=>{
    if(!g.kids){h+=`<th rowspan="2" class="leaf" data-lc="${esc(g.id)}" title="${esc(g.name)}">${esc(g.name)}</th>`;return}
    const span=g.open?g.kids.length:1;
    h+=`<th class="grp" colspan="${span}" ${g.open?'':'rowspan="2"'} data-g="${esc(g.id)}" title="${g.open?'Collapse':'Expand'} ${esc(g.name)}">${g.open?'▾':'▸'} ${esc(g.name)}${g.open?'':' <span style="color:var(--ink3);font-weight:400">('+g.kids.length+')</span>'}</th>`});
  h+='<th class="sl r0" rowspan="2">BOQ qty</th><th class="sl r1" rowspan="2">To date</th><th class="sl r2" rowspan="2">Prev · This bill</th></tr><tr>';
  t.groups.forEach(g=>{if(g.kids&&g.open)g.kids.forEach(k=>h+=`<th class="leaf" data-lc="${esc(k.id)}" title="${esc(k.name)}">${esc(k.name)}</th>`)});
  h+='</tr></thead><tbody>';
  VR.forEach((row,ri)=>{
    const rt=rowTotal(t,row);
    h+=`<tr data-r="${ri}"><td class="sl rl c0" data-rl="${ri}">${esc(row.code)}</td><td class="sl rl c1" data-rl="${ri}" title="${esc(row.name)}">${esc(row.name)}</td>`;
    h+=`<td class="sl rl c2" data-rl="${ri}"><span class="u"><b>${row.parts?'Sqm':esc(row.unit)}</b>${row.mm?'<s>mm likho</s>':row.parts?'<s>parts se</s>':row.run?'<s>type ya run list</s>':row.unit==='Nos'?'<s>poora no.</s>':''}</span></td>`;
    VC.forEach((col,ci)=>h+=`<td class="c" data-r="${ri}" data-c="${ci}"></td>`);
    h+=`<td class="sl sr r0 dash" data-b="${ri}">—</td><td class="sl sr r1" data-td="${ri}"></td><td class="sl sr r2" data-pt="${ri}"></td></tr>`});
  h+='</tbody></table>';
  $('#tbl').innerHTML=h;
  VR.forEach((r,ri)=>VC.forEach((c,ci)=>paint(ri,ci)));VR.forEach((r,ri)=>paintTotals(ri));
  paintSel();paintFbar();paintSave();
  $('#bUndo').disabled=!t.undo.length;$('#bRedo').disabled=!t.redo.length}
const tdAt=(r,c)=>SR.querySelector(`td.c[data-r="${r}"][data-c="${c}"]`);
function savedState(t){return JSON.parse(t.saved)}
function rawSaved(t,key){return (savedState(t).c[key])||''}
function paint(ri,ci){
  const td=tdAt(ri,ci);if(!td)return;const t=cur,row=VR[ri],col=VC[ci];
  let cls='c',txt='',title='';
  if(col.sum){const g=t.groups.find(g=>g.id===col.id);const s=groupSum(t,row,g);txt=s==null?'':num(s,row);cls+=' sum ro'}
  else{
    const c=cellVal(t,row,col.id);
    if(row.parts){cls+=' ro';if(c.v!=null){cls+=' parts';txt='<span class="pm">▦</span>'+fmt(c.v,2)}else{cls+=' parts'}}
    else if(c.list){cls+=' ro parts';txt='<span class="pm">▦</span>'+fmt(c.v,2)}
    else if(!c.ok){cls+=' err';txt=esc(c.raw)}
    else{txt=c.v==null?'':fmt(c.v,row.unit==='Nos'||row.mm?0:2);if(c.pieces&&c.pieces.length>1){cls+=' multi';title=c.pieces.join(' + ')}}
    if(!row.parts&&!c.list){const key=row.id+'|'+col.id;if((t.cells[key]||'')!==rawSaved(t,key))cls+=' dirty'}
    else if(listDirty(t,col.id)&&c.v!=null)cls+=' dirty'}
  td.className=cls;td.innerHTML=txt;if(title)td.title=title;else td.removeAttribute('title')}
function partsDirty(t,cid){const sp=savedState(t).p;return JSON.stringify(sp[cid]||[])!==JSON.stringify(t.parts[cid]||[])}
function runsDirty(t,cid){const sp=savedState(t).r||{};return JSON.stringify(sp[cid]||[])!==JSON.stringify(t.runs[cid]||[])}
function listDirty(t,cid){return partsDirty(t,cid)||runsDirty(t,cid)}
function paintTotals(ri){const t=cur,row=VR[ri];const rt=rowTotal(t,row);
  const td=SR.querySelector(`[data-td="${ri}"]`),pt=SR.querySelector(`[data-pt="${ri}"]`);
  td.textContent=rt.v==null?'—':fmt(rt.v,row.unit==='Nos'?0:2);td.title=row.mm?'mm ka jod ÷ 1000':'';
  td.className='sl sr r1'+(rt.v==null?' dash':'');
  let html='—',cls='sl sr r2 dash';
  if(rt.v!=null&&row.prev!=null){const d=Math.round((rt.v-row.prev)*100)/100;html=`<span style="color:var(--ink3);font-weight:400">${fmt(row.prev,0)} ·</span> ${fmt(d,2)}`;cls='sl sr r2';if(d<0){cls+=' warn';pt.title='To date pichhle RA se kam hai. Check karo.'}else pt.removeAttribute('title')}
  else if(row.prev==null)pt.title='Pichhle RA ka figure is file me nahi tha, isliye nahi dikhaya.';
  pt.className=cls;pt.innerHTML=html}
function repaintAll(){VR.forEach((r,ri)=>{VC.forEach((c,ci)=>paint(ri,ci));paintTotals(ri)});paintSel();paintFbar();paintSave();
  $('#bUndo').disabled=!cur.undo.length;$('#bRedo').disabled=!cur.redo.length;renderMobile()}

/* ---------- selection ---------- */
function rng(){const s=cur.sel;return{r1:Math.min(s.r,s.r2),r2:Math.max(s.r,s.r2),c1:Math.min(s.c,s.c2),c2:Math.max(s.c,s.c2)}}
function place(ov,r){const a=tdAt(r.r1,r.c1),b=tdAt(r.r2,r.c2);if(!a||!b){ov.style.display='none';return}
  const x=a.offsetLeft,y=a.offsetTop;ov.style.left=x-1+'px';ov.style.top=y-1+'px';
  ov.style.width=(b.offsetLeft+b.offsetWidth-x+1)+'px';ov.style.height=(b.offsetTop+b.offsetHeight-y+1)+'px';ov.style.display='block'}
function paintSel(){
  SR.querySelectorAll('td.sel').forEach(e=>e.classList.remove('sel'));
  const t=cur;if(!t.sel.on||!VR.length){$('#ov').style.display='none';placeClip();return}
  const r=rng();
  for(let i=r.r1;i<=r.r2;i++)for(let j=r.c1;j<=r.c2;j++){const td=tdAt(i,j);if(td)td.classList.add('sel')}
  place($('#ov'),r);placeClip();stats()}
function placeClip(){const c=cur.clip,ov=$('#ovc');if(!c){ov.style.display='none';return}place(ov,c.r)}
function select(r,c,extend){
  const t=cur;r=Math.max(0,Math.min(VR.length-1,r));c=Math.max(0,Math.min(VC.length-1,c));
  if(extend&&t.sel.on){t.sel.r2=r;t.sel.c2=c}else{t.sel={r,c,r2:r,c2:c,on:true}}
  paintSel();paintFbar();scrollTo(r,c)}
function scrollTo(r,c){const td=tdAt(r,c),w=$('#gridwrap');if(!td)return;
  const L=td.offsetLeft,R=L+td.offsetWidth,sl=w.scrollLeft;const left=444,right=296;
  if(L<sl+left)w.scrollLeft=L-left-4;else if(R>sl+w.clientWidth-right)w.scrollLeft=R-w.clientWidth+right+4;
  const T_=td.offsetTop,B=T_+td.offsetHeight;if(T_<w.scrollTop+58)w.scrollTop=T_-58;else if(B>w.scrollTop+w.clientHeight)w.scrollTop=B-w.clientHeight}
function stats(){const t=cur,r=rng();let n=0,s=0;
  for(let i=r.r1;i<=r.r2;i++)for(let j=r.c1;j<=r.c2;j++){const row=VR[i],col=VC[j];let v=null;
    if(col.sum){v=groupSum(t,row,t.groups.find(g=>g.id===col.id))}else{const c=cellVal(t,row,col.id);if(c.ok)v=c.v}
    if(v!=null){n++;s+=v}}
  const cells=(r.r2-r.r1+1)*(r.c2-r.c1+1);
  $('#stat').innerHTML=cells>1?`${cells} cells · Count <b>${n}</b> · Sum <b>${fmt(s,2)}</b>`+(n?` · Avg <b>${fmt(s/n,2)}</b>`:''):''}

/* ---------- formula bar ---------- */
function paintFbar(){const t=cur,fx=$('#fx'),res=$('#res'),nm=$('#nm'),bp=$('#bParts');
  bp.hidden=true;res.textContent='';res.className='res';
  if(!t.sel.on||!VR.length){nm.textContent='Koi cell select nahi';fx.value='';fx.disabled=true;return}
  const row=VR[t.sel.r],col=VC[t.sel.c];fx.disabled=false;
  const g=col.g?t.groups.find(g=>g.id===col.g):null;
  nm.textContent=`${row.name} · ${g&&!col.sum&&g.name!==col.name?g.name+' ▸ ':''}${col.name}`;nm.title=nm.textContent;
  if(col.sum){fx.value='';fx.disabled=true;res.textContent='Group total. Edit karne ke liye expand karo';return}
  if(row.parts){fx.value='';fx.disabled=true;bp.hidden=false;bp.textContent='Duct parts';const v=sqmCell(t,row,col.id);res.textContent=v==null?'Parts abhi nahi':'= '+fmt(v,2)+' Sqm (parts se)';return}
  if(row.run){bp.hidden=false;bp.textContent='Run list'}
  const c=cellVal(t,row,col.id);
  if(c.list){fx.value='';fx.disabled=true;res.textContent='= '+fmt(c.v,2)+' Mtr (run list se, '+(t.runs[col.id]||[]).filter(e=>e.c===row.id).length+' runs)';return}if(SR.activeElement!==fx)fx.value=c.raw;
  if(!c.ok){res.textContent=c.err;res.className='res err'}
  else if(c.v!=null){const mtr=row.mm?` = ${fmt(c.v/1000,3)} Mtr`:'';res.textContent=(c.pieces.length>1?c.pieces.length+' tukde · ':'')+'= '+fmt(c.v,row.unit==='Nos'||row.mm?0:2)+(row.mm?' mm'+mtr:' '+row.unit)}}

/* ---------- editing / undo ---------- */
function setCells(changes,label){ // changes: [{key,val}]
  const t=cur,rec=[];
  changes.forEach(ch=>{const old=t.cells[ch.key]||'';const nv=ch.val==null?'':String(ch.val);if(old===nv)return;rec.push({key:ch.key,old,nv});if(nv==='')delete t.cells[ch.key];else t.cells[ch.key]=nv});
  if(!rec.length)return 0;t.undo.push(rec);t.redo=[];repaintAll();return rec.length}
function undo(){const t=cur,rec=t.undo.pop();if(!rec)return;rec.forEach(x=>{if(x.old==='')delete t.cells[x.key];else t.cells[x.key]=x.old});t.redo.push(rec);repaintAll();toast('Undo')}
function redo(){const t=cur,rec=t.redo.pop();if(!rec)return;rec.forEach(x=>{if(x.nv==='')delete t.cells[x.key];else t.cells[x.key]=x.nv});t.undo.push(rec);repaintAll();toast('Redo')}
function keyOf(ri,ci){const row=VR[ri],col=VC[ci];return row.id+'|'+col.id}
function editable(ri,ci){const row=VR[ri],col=VC[ci];if(row.parts||col.sum)return false;if(row.run&&runSum(cur,row,col.id)!=null)return false;return true}
function clearSel(){const r=rng(),ch=[];for(let i=r.r1;i<=r.r2;i++)for(let j=r.c1;j<=r.c2;j++)if(editable(i,j))ch.push({key:keyOf(i,j),val:''});
  const n=setCells(ch);if(!n)toast(r.r1===r.r2&&r.c1===r.c2&&!editable(r.r1,r.c1)?'Yeh cell auto hai, clear nahi hota':'Kuch clear karne ko nahi tha')}
function startEdit(ri,ci,seed){
  if(!editable(ri,ci)){const row=VR[ri];if(row.parts)openParts(VC[ci].id);else if(row.run&&!VC[ci].sum)openRuns(VC[ci].id);else toast('Group total hai. Expand karke andar ka cell edit karo');return}
  const td=tdAt(ri,ci);if(!td)return;const key=keyOf(ri,ci);
  edit={ri,ci,key,orig:cur.cells[key]||''};
  td.innerHTML='';const inp=document.createElement('input');inp.className='ed';inp.id='ed';inp.setAttribute('aria-label','Cell edit');
  inp.value=seed!=null?seed:edit.orig;td.appendChild(inp);inp.focus();if(seed==null)inp.select();else inp.setSelectionRange(inp.value.length,inp.value.length);
  inp.addEventListener('input',()=>{$('#fx').value=inp.value;liveCheck(inp.value)});liveCheck(inp.value)}
function liveCheck(v){const row=VR[edit.ri],res=$('#res');const r=evalRaw(v,row);
  if(!r.ok){res.textContent=r.err;res.className='res err'}else{res.className='res';res.textContent=r.v==null?'':(r.pieces.length>1?r.pieces.length+' tukde · ':'')+'= '+fmt(r.v,row.unit==='Nos'||row.mm?0:2)+(row.mm?' mm = '+fmt(r.v/1000,3)+' Mtr':'')}}
function endEdit(commit){if(!edit)return;const e=edit;edit=null;const inp=$('#ed');const val=inp?inp.value:e.orig;
  if(commit&&val.trim()!==e.orig)setCells([{key:e.key,val:val.trim()}]);else{paint(e.ri,e.ci)}
  paintFbar();$('#grid').focus({preventScroll:true})}
function move(dr,dc,extend){const s=cur.sel;if(!s.on){select(0,0);return}
  const r=extend?s.r2:s.r,c=extend?s.c2:s.c;select(r+dr,c+dc,extend)}
$('#fx').addEventListener('keydown',e=>{
  if(e.key==='Enter'||e.key==='Tab'){e.preventDefault();const t=cur;if(!t.sel.on)return;const r=rng(),ri=r.r1,ci=r.c1;
    if(editable(ri,ci)&&e.target.value.trim()!==(t.cells[keyOf(ri,ci)]||''))setCells([{key:keyOf(ri,ci),val:e.target.value.trim()}]);$('#grid').focus()}
  if(e.key==='Escape'){paintFbar();$('#grid').focus()}});
$('#fx').addEventListener('input',e=>{const t=cur;if(!t.sel.on)return;const row=VR[t.sel.r];const r=evalRaw(e.target.value,row),res=$('#res');
  if(!r.ok){res.textContent=r.err;res.className='res err'}else{res.className='res';res.textContent=r.v==null?'':'= '+fmt(r.v,2)}});

/* ---------- keyboard ---------- */
const printable=e=>e.key.length===1&&!e.ctrlKey&&!e.metaKey&&!e.altKey;
$('#grid').addEventListener('keydown',e=>{
  const t=cur;
  if(edit){ // inside cell input
    if(e.key==='Enter'){e.preventDefault();const r=edit.ri,c=edit.ci;endEdit(true);select(r+(e.shiftKey?-1:1),c)}
    else if(e.key==='Tab'){e.preventDefault();const r=edit.ri,c=edit.ci;endEdit(true);select(r,c+(e.shiftKey?-1:1))}
    else if(e.key==='Escape'){e.preventDefault();endEdit(false)}
    return}
  if(!t.sel.on){if(['ArrowDown','ArrowRight','Tab','Enter'].includes(e.key)){e.preventDefault();select(0,0)}return}
  const mod=e.ctrlKey||e.metaKey;
  switch(e.key){
    case'ArrowDown':e.preventDefault();mod?jump(1,0,e.shiftKey):move(1,0,e.shiftKey);return;
    case'ArrowUp':e.preventDefault();mod?jump(-1,0,e.shiftKey):move(-1,0,e.shiftKey);return;
    case'ArrowRight':e.preventDefault();mod?jump(0,1,e.shiftKey):move(0,1,e.shiftKey);return;
    case'ArrowLeft':e.preventDefault();mod?jump(0,-1,e.shiftKey):move(0,-1,e.shiftKey);return;
    case'Tab':e.preventDefault();move(0,e.shiftKey?-1:1,false);return;
    case'Enter':e.preventDefault();{const r=rng();if(VR[t.sel.r].parts&&!VC[t.sel.c].sum){openParts(VC[t.sel.c].id)}else if(!editable(t.sel.r,t.sel.c)&&VR[t.sel.r].run&&!VC[t.sel.c].sum){openRuns(VC[t.sel.c].id)}else if(e.shiftKey)move(-1,0);else move(1,0)}return;
    case'F2':e.preventDefault();startEdit(t.sel.r,t.sel.c);return;
    case'Backspace':case'Delete':e.preventDefault();clearSel();return;
    case'Escape':t.clip=null;placeClip();return;
    case'Home':e.preventDefault();select(t.sel.r,0,e.shiftKey);return;
    case'End':e.preventDefault();select(t.sel.r,VC.length-1,e.shiftKey);return}
  if(mod){const k=e.key.toLowerCase();
    if(k==='z'&&!e.shiftKey){e.preventDefault();undo()}
    else if(k==='y'||(k==='z'&&e.shiftKey)){e.preventDefault();redo()}
    else if(k==='d'){e.preventDefault();fill('down')}
    else if(k==='r'){e.preventDefault();fill('right')}
    else if(k==='a'){e.preventDefault();t.sel={r:0,c:0,r2:VR.length-1,c2:VC.length-1,on:true};paintSel();paintFbar()}
    else if(k==='s'){e.preventDefault();doSave()}
    return}
  if(printable(e)&&/[0-9.(=]/.test(e.key)){e.preventDefault();if(e.key===' ')return;startEdit(t.sel.r,t.sel.c,e.key)}
  else if(printable(e)&&e.key==='-'){e.preventDefault();toast('Minus nahi chalta. Galat tukda hata ke sahi likho.')}});
function jump(dr,dc,ext){const t=cur,s=t.sel;let r=ext?s.r2:s.r,c=ext?s.c2:s.c;
  if(dr)r=dr>0?VR.length-1:0;if(dc)c=dc>0?VC.length-1:0;select(r,c,ext)}
function fill(dir){const t=cur,r=rng(),ch=[];
  if(dir==='down'){for(let j=r.c1;j<=r.c2;j++){if(r.r1===r.r2)continue;const src=cur.cells[keyOf(r.r1,j)]||'';for(let i=r.r1+1;i<=r.r2;i++)if(editable(i,j)&&editable(r.r1,j))ch.push({key:keyOf(i,j),val:src})}}
  else{for(let i=r.r1;i<=r.r2;i++){if(r.c1===r.c2)continue;const src=cur.cells[keyOf(i,r.c1)]||'';for(let j=r.c1+1;j<=r.c2;j++)if(editable(i,j)&&editable(i,r.c1))ch.push({key:keyOf(i,j),val:src})}}
  const n=setCells(ch);toast(n?`${n} cell bhare`:'Fill ke liye 2 ya zyada cells select karo')}

/* ---------- clipboard ---------- */
docOn('copy',e=>{if(edit||SR.activeElement.id==='fx'||!isGridFocus())return;doCopy(e,false)});
docOn('cut',e=>{if(edit||SR.activeElement.id==='fx'||!isGridFocus())return;doCopy(e,true)});
function isGridFocus(){return cur.sel.on&&(SR.activeElement===$('#grid')||$('#grid').contains(SR.activeElement))}
function doCopy(e,cut){const t=cur,r=rng(),rows=[],raws=[];
  for(let i=r.r1;i<=r.r2;i++){const a=[],b=[];for(let j=r.c1;j<=r.c2;j++){const row=VR[i],col=VC[j];let disp='',raw='';
    if(col.sum){const s=groupSum(t,row,t.groups.find(g=>g.id===col.id));disp=s==null?'':String(Math.round(s*1e6)/1e6)}
    else{const c=cellVal(t,row,col.id);raw=c.raw||'';disp=c.ok?(c.v==null?'':String(c.v)):raw}
    a.push(disp);b.push(raw)}rows.push(a);raws.push(b)}
  const text=rows.map(a=>a.join('\t')).join('\n');
  e.clipboardData.setData('text/plain',text);e.preventDefault();
  t.clip={r,text,raws};placeClip();
  if(cut){const ch=[];for(let i=r.r1;i<=r.r2;i++)for(let j=r.c1;j<=r.c2;j++)if(editable(i,j))ch.push({key:keyOf(i,j),val:''});setCells(ch)}
  toast((r.r2-r.r1+1)*(r.c2-r.c1+1)+' cells '+(cut?'cut':'copy')+' ho gaye')}
docOn('paste',e=>{if(edit||SR.activeElement.id==='fx'||!isGridFocus()){return}
  e.preventDefault();const text=(e.clipboardData.getData('text/plain')||'').replace(/\r/g,'');if(!text.trim())return;doPaste(text)});
function parseTSV(text){let rows=text.split('\n');if(rows.length&&rows[rows.length-1]==='')rows.pop();return rows.map(r=>r.split('\t').map(x=>x.trim().replace(/^"(.*)"$/,'$1')))}
function doPaste(text){const t=cur,r=rng();let grid,fromApp=false;
  if(t.clip&&t.clip.text===text){grid=t.clip.raws;fromApp=true}else grid=parseTSV(text);
  const h=grid.length,w=Math.max(...grid.map(x=>x.length));
  const ch=[];let skipped=0,clipped=0;
  const fillAll=h===1&&w===1&&(r.r2>r.r1||r.c2>r.c1);
  const R=fillAll?r.r2-r.r1+1:h,C=fillAll?r.c2-r.c1+1:w;
  for(let i=0;i<R;i++)for(let j=0;j<C;j++){const ri=r.r1+i,ci=r.c1+j;
    if(ri>=VR.length||ci>=VC.length){clipped++;continue}
    if(!editable(ri,ci)){skipped++;continue}
    let v=fillAll?grid[0][0]:(grid[i]&&grid[i][j]!=null?grid[i][j]:'');
    if(!fromApp&&/^\d{1,3}(,\d{3})+(\.\d+)?$/.test(v))v=v.replace(/,/g,'')
    ch.push({key:keyOf(ri,ci),val:v})}
  const n=setCells(ch);
  select(r.r1,r.c1);cur.sel.r2=Math.min(VR.length-1,r.r1+R-1);cur.sel.c2=Math.min(VC.length-1,r.c1+C-1);paintSel();
  const bad=ch.filter(c=>{const row=VR[VR.findIndex(x=>x.id===c.key.split('|')[0])];return!evalRaw(c.val,row).ok}).length;
  toast(`${n} cells paste ho gaye`+(bad?` · ${bad} galat (laal)`:'')+(skipped?` · ${skipped} auto cells chhode`:'')+(clipped?` · ${clipped} cells sheet ke bahar the`:''))}

/* ---------- mouse ---------- */
const gridEl=$('#grid');
gridEl.addEventListener('mousedown',e=>{
  const td=e.target.closest('td.c'),rl=e.target.closest('[data-rl]'),gh=e.target.closest('th[data-g]'),lh=e.target.closest('th[data-lc]');
  if(e.target.id==='ed')return;
  if(edit)endEdit(true);
  if(gh){toggleGroup(gh.dataset.g);return}
  if(td){e.preventDefault();gridEl.focus({preventScroll:true});const r=+td.dataset.r,c=+td.dataset.c;
    select(r,c,e.shiftKey);drag=true;return}
  if(rl){e.preventDefault();gridEl.focus({preventScroll:true});cur.sel={r:+rl.dataset.rl,c:0,r2:+rl.dataset.rl,c2:VC.length-1,on:true};paintSel();paintFbar();return}
  if(lh){e.preventDefault();gridEl.focus({preventScroll:true});const ci=VC.findIndex(c=>c.id===lh.dataset.lc);if(ci>=0){cur.sel={r:0,c:ci,r2:VR.length-1,c2:ci,on:true};paintSel();paintFbar()}}});
docOn('mouseup',()=>{drag=false});
gridEl.addEventListener('mouseover',e=>{const td=e.target.closest('td.c');
  if(drag&&td){cur.sel.r2=+td.dataset.r;cur.sel.c2=+td.dataset.c;paintSel()}
  if(td)setHover(+td.dataset.r,+td.dataset.c);else{const rl=e.target.closest('[data-rl]');if(rl)setHover(+rl.dataset.rl,-1);else setHover(-1,-1)}});
gridEl.addEventListener('mouseleave',()=>setHover(-1,-1));
gridEl.addEventListener('dblclick',e=>{const td=e.target.closest('td.c');if(td&&!e.target.matches('input'))startEdit(+td.dataset.r,+td.dataset.c)});
function setHover(r,c){if(hov.r===r&&hov.c===c)return;
  SR.querySelectorAll('.hr,.hc,.hh').forEach(e=>e.classList.remove('hr','hc','hh'));hov={r,c};
  if(r>=0){SR.querySelectorAll(`td.c[data-r="${r}"]`).forEach(e=>e.classList.add('hr'));SR.querySelectorAll(`[data-rl="${r}"]`).forEach(e=>e.classList.add('hh'))}
  if(c>=0){SR.querySelectorAll(`td.c[data-c="${c}"]`).forEach(e=>e.classList.add('hc'));const col=VC[c];const th=SR.querySelector(`th[data-lc="${col.id}"]`)||SR.querySelector(`th[data-g="${col.g}"]`);if(th)th.classList.add('hh')}
  const hs=$('#hov');if(r>=0&&c>=0){const row=VR[r],col=VC[c];const g=col.g&&!col.sum?cur.groups.find(g=>g.id===col.g).name+' ▸ ':'';hs.innerHTML=`<b>${esc(row.name)}</b> · ${esc(g+col.name)}`}else if(r>=0){hs.innerHTML=`<b>${esc(VR[r].name)}</b>`}else hs.textContent='Cell par jao'}
function toggleGroup(id){const g=cur.groups.find(g=>g.id===id);g.open=!g.open;cur.sel.on=false;mount()}
$('#bExp').onclick=()=>{cur.groups.forEach(g=>{if(g.kids)g.open=true});cur.sel.on=false;mount()};
$('#bCol').onclick=()=>{cur.groups.forEach(g=>{if(g.kids)g.open=false});cur.sel.on=false;mount()};
$('#q').addEventListener('input',()=>{cur.sel.on=false;mount()});
$('#bUndo').onclick=undo;$('#bRedo').onclick=redo;
$('#gridwrap').addEventListener('scroll',()=>{placeClip()});

/* ---------- save bar ---------- */
function dirtyInfo(t){const sv=savedState(t);let n=0,bad=0;const keys=new Set([...Object.keys(t.cells),...Object.keys(sv.c)]);
  const rowOf=id=>t.rows.find(r=>r.id===id);
  keys.forEach(k=>{if((t.cells[k]||'')!==(sv.c[k]||'')){n++}});
  Object.keys(t.cells).forEach(k=>{const row=rowOf(k.split('|')[0]);if(row&&!evalRaw(t.cells[k],row).ok)bad++});
  const pk=new Set([...Object.keys(t.parts),...Object.keys(sv.p)]);let pn=0;pk.forEach(k=>{if(JSON.stringify(t.parts[k]||[])!==JSON.stringify(sv.p[k]||[]))pn++});const rk=new Set([...Object.keys(t.runs),...Object.keys(sv.r||{})]);rk.forEach(k=>{if(JSON.stringify(t.runs[k]||[])!==JSON.stringify((sv.r||{})[k]||[]))pn++});
  return{n,bad,pn}}
function paintSave(){renderTabs();const d=dirtyInfo(cur),m=$('#smsg'),s=$('#bSave');const tot=d.n+d.pn;
  if(!tot){m.innerHTML='Sab saved. <span style="color:var(--ink3)">% complete, DPR aur bill isi se chalte hain.</span>';s.disabled=true;s.textContent='Save';$('#bDisc').disabled=true;return}
  m.innerHTML=`<b>${d.n?d.n+' cell'+(d.n>1?'s':''):''}${d.n&&d.pn?' + ':''}${d.pn?d.pn+' detail list':''}</b> badli hui, abhi save nahi.`+(d.bad?` <span class="bad">${d.bad} cell galat hai, pehle theek karo.</span>`:'');
  s.disabled=!!d.bad;s.textContent=`Save ${tot} change${tot>1?'s':''}`;$('#bDisc').disabled=false}
function doSave(){const d=dirtyInfo(cur);if(d.bad){toast('Pehle laal cells theek karo');return}if(!d.n&&!d.pn)return;
  const t=cur,commit=()=>{t.saved=JSON.stringify({c:t.cells,p:t.parts,r:t.runs});t.undo=[];t.redo=[];repaintAll();toast('Save ho gaya. Ab % complete, DPR aur bill in numbers se chalenge.')};
  if(typeof OPTS!=='undefined'&&OPTS.onSave){Promise.resolve(OPTS.onSave({service:t.id,cells:t.cells,parts:t.parts,runs:t.runs,places:t.groups})).then(commit,err=>toast('Save nahi hua: '+((err&&err.message)||err)))}
  else{commit();toast('Save ho gaya. (Prototype: asli app me yahan entries banengi)')}}
$('#bSave').onclick=doSave;
$('#bDisc').onclick=()=>{const sv=savedState(cur);cur.cells=JSON.parse(JSON.stringify(sv.c));cur.parts=JSON.parse(JSON.stringify(sv.p));cur.runs=JSON.parse(JSON.stringify(sv.r||{}));cur.undo=[];cur.redo=[];repaintAll();toast('Changes hata diye')};

/* ---------- tabs ---------- */
function renderTabs(){const el=$('#tabs');el.innerHTML=DATA.map(t=>`<button class="tab" role="tab" aria-selected="${t===cur}" data-t="${t.id}">${esc(t.name)}${dirtyInfo(t).n+dirtyInfo(t).pn?' <span class="dot" style="display:inline-block"></span>':''}</button>`).join('')+'<div class="sp"></div>';
  el.querySelectorAll('.tab').forEach(b=>b.onclick=()=>{if(edit)endEdit(true);cur=T[b.dataset.t];$('#q').value='';renderTabs();mount();renderMobile()})}
const _rs=repaintAll;

/* ---------- duct parts drawer ---------- */
function openParts(cid){const t=cur,col=leafIds(t).find(k=>k.id===cid);const gname=(t.groups.find(g=>g.kids&&g.kids.some(k=>k.id===cid))||{name:''}).name;
  if(!t.parts[cid])t.parts[cid]=[];
  const L=$('#layer');L.innerHTML=`<div class="modal" id="pm"><div class="sheet" role="dialog" aria-label="Duct parts"><header><h2>Duct parts · ${esc(gname)} ▸ ${esc(col.name)}</h2><button class="x" id="pmx" aria-label="Band karo">×</button></header><div class="bd"><p class="note">Excel ke WORKING sheet jaisa part list. Sqm khud nikalta hai (Straight, Taper, Elbow, Collar, Dummy ke formula tere file se). Gauge jis BOQ line ka hai, wahi cell bharta hai.</p><div class="tw"><table class="tbl" id="ptbl"></table></div><div><button class="btn sm" id="padd">+ Part jodo</button></div><div id="psum" class="note"></div></div><footer><button class="btn pri" id="pdone">Ho gaya</button></footer></div></div>`;
  const draw=()=>{const ps=t.parts[cid];let h='<thead><tr><th>#</th><th>Part</th><th>Gauge</th><th>A</th><th>B</th><th>C</th><th>D</th><th>Angle</th><th>Length mm</th><th>Qty</th><th>Sqm</th><th></th></tr></thead><tbody>';
    ps.forEach((p,i)=>{h+=`<tr data-i="${i}"><td>${i+1}</td><td><select data-f="kind">${['Straight','Taper','Elbow','Fan Connection','Collar','Dummy'].map(k=>`<option${k===p.kind?' selected':''}>${k}</option>`).join('')}</select></td><td><select data-f="g">${[18,20,22,24].map(g=>`<option${+g===+p.g?' selected':''}>${g}</option>`).join('')}</select></td>`+
      ['A','B','C','D','ang','L','Q'].map(f=>`<td><input data-f="${f}" value="${p[f]==null?'':p[f]}" inputmode="decimal"></td>`).join('')+`<td class="n" data-a>${fmt(partArea(p),2)}</td><td><button class="x" data-del aria-label="Part hatao">×</button></td></tr>`});
    h+='</tbody>';$('#ptbl').innerHTML=h;sum()};
  const sum=()=>{const ps=t.parts[cid];const by={};ps.forEach(p=>by[p.g]=(by[p.g]||0)+partArea(p));
    $('#psum').innerHTML='Total: '+Object.keys(by).sort().map(g=>`<b>${g} G = ${fmt(by[g],2)} Sqm</b>`).join(' · ')+' · '+ps.length+' parts';repaintAll()};
  draw();
  $('#ptbl').addEventListener('input',e=>{const tr=e.target.closest('tr');if(!tr)return;const p=t.parts[cid][+tr.dataset.i],f=e.target.dataset.f;
    p[f]=f==='kind'?e.target.value:(e.target.value===''?null:(f==='g'?+e.target.value:parseFloat(e.target.value)));if(isNaN(p[f]))p[f]=null;
    tr.querySelector('[data-a]').textContent=fmt(partArea(p),2);sum()});
  $('#ptbl').addEventListener('click',e=>{if(e.target.matches('[data-del]')){const i=+e.target.closest('tr').dataset.i;t.parts[cid].splice(i,1);draw()}});
  $('#padd').onclick=()=>{const ps=t.parts[cid];const last=ps[ps.length-1]||{};ps.push({kind:'Straight',g:last.g||22,A:null,B:null,C:null,D:null,ang:0,L:null,Q:1});draw()};
  const close=()=>{L.innerHTML='';$('#grid').focus({preventScroll:true})};$('#pdone').onclick=close;$('#pmx').onclick=close}

/* ---------- run list drawer (BMS / cable) ---------- */
function cableCode(txt,t){const x=String(txt||'').trim().toLowerCase();if(!x)return null;const rs=t.rows.filter(r=>r.run);
  let m=rs.find(r=>r.code.toLowerCase()===x||r.id.toLowerCase()===x||r.name.toLowerCase()===x);if(m)return m.id;
  const p=/^(\d+c)/.exec(x);if(p){m=rs.find(r=>r.name.toLowerCase().includes(p[1]));if(m)return m.id}return null}
function openRuns(cid){const t=cur,col=leafIds(t).find(k=>k.id===cid);const gname=(t.groups.find(g=>g.kids&&g.kids.some(k=>k.id===cid))||{name:''}).name;
  if(!t.runs[cid])t.runs[cid]=[];const rr=t.rows.filter(r=>r.run);
  const L=$('#layer');L.innerHTML=`<div class="modal"><div class="sheet" role="dialog" aria-label="Run list"><header><h2>Run list · ${esc(gname)} ▸ ${esc(col.name)}</h2><button class="x" id="pmx" aria-label="Band karo">×</button></header><div class="bd"><p class="note">Device wise list, jaise tere BMS sheet me: kya, kahan se kahan, kaun sa cable, kitne run, kitni length. Total khud (runs x length) aur cable ki BOQ line me jaata hai. Excel se rows copy karke yahan <kbd>Ctrl</kbd>+<kbd>V</kbd> karo (Description, From, To, Cable, Runs, Length ke order me).</p><div class="tw"><table class="tbl" id="ptbl"></table></div><div><button class="btn sm" id="padd">+ Run jodo</button></div><div id="psum" class="note"></div></div><footer><button class="btn pri" id="pdone">Ho gaya</button></footer></div></div>`;
  const draw=()=>{const es=t.runs[cid];let h='<thead><tr><th>#</th><th>Description</th><th>From</th><th>To</th><th>Cable (BOQ line)</th><th>Runs</th><th>Length m</th><th>Total m</th><th></th></tr></thead><tbody>';
    es.forEach((e,i)=>{h+=`<tr data-i="${i}"><td>${i+1}</td><td><input class="tx" data-f="desc" value="${esc(e.desc)}"></td><td><input class="tx" data-f="from" value="${esc(e.from)}"></td><td><input class="tx" data-f="to" value="${esc(e.to)}"></td><td><select data-f="c">${rr.map(r=>`<option value="${r.id}"${r.id===e.c?' selected':''}>${esc(r.code)} · ${esc(r.name)}</option>`).join('')}</select></td><td><input data-f="n" value="${e.n==null?'':e.n}" inputmode="decimal" style="width:52px"></td><td><input data-f="L" value="${e.L==null?'':e.L}" inputmode="decimal"></td><td class="n" data-a>${fmt((+e.n||0)*(+e.L||0),2)}</td><td><button class="x" data-del aria-label="Run hatao">×</button></td></tr>`});
    h+='</tbody>';$('#ptbl').innerHTML=h;sum()};
  const sum=()=>{const by={};t.runs[cid].forEach(e=>by[e.c]=(by[e.c]||0)+(+e.n||0)*(+e.L||0));
    $('#psum').innerHTML='Total: '+(Object.keys(by).length?Object.keys(by).map(c=>{const r=t.rows.find(x=>x.id===c);return `<b>${esc(r?r.code:c)} = ${fmt(by[c],2)} m</b>`}).join(' · '):'abhi koi run nahi')+' · '+t.runs[cid].length+' runs';repaintAll()};
  draw();
  $('#ptbl').addEventListener('input',e=>{const tr=e.target.closest('tr');if(!tr)return;const x=t.runs[cid][+tr.dataset.i],f=e.target.dataset.f;
    if(f==='n'||f==='L'){const v=e.target.value===''?null:parseFloat(e.target.value);x[f]=isNaN(v)?null:v}else x[f]=e.target.value;
    tr.querySelector('[data-a]').textContent=fmt((+x.n||0)*(+x.L||0),2);sum()});
  $('#ptbl').addEventListener('click',e=>{if(e.target.matches('[data-del]')){t.runs[cid].splice(+e.target.closest('tr').dataset.i,1);draw()}});
  $('#ptbl').addEventListener('paste',e=>{const inp=e.target.closest('input,select');if(!inp)return;const txt=(e.clipboardData.getData('text/plain')||'').replace(/\r/g,'');
    if(!/[\t\n]/.test(txt.trim()))return;e.preventDefault();const order=['desc','from','to','c','n','L'];let f0=order.indexOf(inp.dataset.f);if(f0<0)f0=0;
    const r0=+inp.closest('tr').dataset.i;const rows=parseTSV(txt);let bad=0;
    rows.forEach((cells,k)=>{let x=t.runs[cid][r0+k];if(!x){x={desc:'',from:'',to:'',c:(rr[0]||{}).id,n:1,L:null};t.runs[cid].push(x)}
      cells.forEach((v,j)=>{const f=order[f0+j];if(!f)return;if(f==='c'){const c=cableCode(v,t);if(c)x.c=c;else bad++}else if(f==='n'||f==='L'){const n=parseFloat(String(v).replace(/,/g,''));x[f]=isNaN(n)?null:n}else x[f]=v})});
    draw();toast(rows.length+' run paste hue'+(bad?` · ${bad} cable pehchana nahi (pehla rakha)`:''))});
  $('#padd').onclick=()=>{const es=t.runs[cid];const last=es[es.length-1]||{};es.push({desc:'',from:last.from||'',to:last.to||'',c:last.c||(rr[0]||{}).id,n:1,L:null});draw()};
  const close=()=>{L.innerHTML='';$('#grid').focus({preventScroll:true})};$('#pdone').onclick=close;$('#pmx').onclick=close}

$('#bParts').onclick=()=>{const c=VC[cur.sel.c];if(!c||c.sum)return;if(VR[cur.sel.r].run)openRuns(c.id);else openParts(c.id)};

/* ---------- export / import ---------- */
function modal(title,body,foot){$('#layer').innerHTML=`<div class="modal"><div class="sheet" role="dialog" aria-label="${esc(title)}"><header><h2>${esc(title)}</h2><button class="x" id="mx" aria-label="Band karo">×</button></header><div class="bd">${body}</div><footer>${foot||''}</footer></div></div>`;$('#mx').onclick=closeLayer}
function closeLayer(){$('#layer').innerHTML=''}
$('#bExport').onclick=()=>{modal('Export Excel',`<p>Export dabane par isi service ki <b>${esc(cur.name)}</b> workbook aati hai, isi grid jaisi: BOQ line neeche, jagah (floor / zone) saamne, har cell me jo likha hai wahi (jaise <code>10+2+30+7</code>), aur sab se aage Summary jisme sign ka box hai.</p><p class="note">Yeh preview artifact download nahi kar sakta. Same layout ki sample file chat me alag se bheji hai. Wahi file Excel me bharke neeche <b>Import Excel</b> se wapas daalo.</p>`,'<button class="btn pri" id="mok">Theek hai</button>');$('#mok').onclick=closeLayer};
$('#bImport').onclick=()=>importDialog();
let pending=null;const SAMPLE=(typeof OPTS==='undefined')||!!OPTS.sample||!!(window.MIGrid&&window.MIGrid.sample);
function importDialog(msg){modal('Import Excel',`<p class="note">Isi layout ki file jo Export se aayi thi. Sirf wahi cells padhe jaate hain jo yahan edit ho sakte hain. Formula chalaya nahi jaata, sirf tukde padhe jaate hain. Import se kuch save nahi hota, pehle preview aata hai.</p><div class="drop" id="drop"><div style="margin-bottom:8px">Excel (.xlsx) yahan chhodo</div><button class="btn" id="pick">File chuno</button><input type="file" id="file" accept=".xlsx,.xlsm" hidden></div>${msg?`<div class="chip r">${esc(msg)}</div>`:''}<div class="note">Excel se seedha cells copy karke grid me <kbd>Ctrl</kbd>+<kbd>V</kbd> bhi chalta hai. ${SAMPLE?'Kisi file ke bina try karna ho to <button class="btn sm" id="samp">sample preview dekho</button>':''}</div>`,'');
  $('#pick').onclick=()=>$('#file').click();$('#file').onchange=e=>e.target.files[0]&&readFile(e.target.files[0]);
  const d=$('#drop');['dragenter','dragover'].forEach(n=>d.addEventListener(n,e=>{e.preventDefault();d.classList.add('over')}));['dragleave','drop'].forEach(n=>d.addEventListener(n,e=>{e.preventDefault();d.classList.remove('over')}));
  d.addEventListener('drop',e=>{const f=e.dataTransfer.files[0];if(f)readFile(f)});
  if($('#samp'))$('#samp').onclick=()=>samplePreview()}
function readFile(f){if(!window.XLSX){importDialog('Excel reader load nahi hua. Net check karo.');return}
  const rd=new FileReader();rd.onload=()=>{try{const wb=XLSX.read(rd.result,{type:'array',cellFormula:true,sheetStubs:true});parseWB(wb)}catch(err){importDialog('Yeh file padhi nahi gayi: '+err.message)}};rd.readAsArrayBuffer(f)}
function parseWB(wb){const ws=wb.Sheets['Measurement'];if(!ws){importDialog('"Measurement" naam ki sheet nahi mili. Export wali file hi chahiye.');return}
  const rng=XLSX.utils.decode_range(ws['!ref']);let colRow=-1;
  for(let r=rng.s.r;r<=rng.e.r;r++){const c=ws[XLSX.utils.encode_cell({r,c:0})];if(c&&c.v==='~cols'){colRow=r;break}}
  if(colRow<0){importDialog('Is file me hidden key row nahi mili. Export wali file hi chahiye.');return}
  const svc=ws[XLSX.utils.encode_cell({r:colRow,c:1})];const t=DATA.find(x=>x.id===(svc&&svc.v));
  if(!t){importDialog('Yeh file kis service ki hai, pata nahi chala.');return}
  const colIds={};for(let c=2;c<=rng.e.c;c++){const x=ws[XLSX.utils.encode_cell({r:colRow,c})];if(x&&x.v)colIds[c]=String(x.v)}
  const leaf=leafIds(t).map(k=>k.id);const diffs=[],unk=[];let same=0,bad=0;const unkCol=[],noKey=new Set();
  Object.keys(colIds).forEach(ci=>{if(!leaf.includes(colIds[ci]))unkCol.push(colIds[ci])});
  for(let r=colRow+1;r<=rng.e.r;r++){const k=ws[XLSX.utils.encode_cell({r,c:0})];if(!k||typeof k.v!=='string'||!k.v.startsWith('~r:'))continue;const rid=k.v.slice(3);const row=t.rows.find(x=>x.id===rid);
    if(!row){unk.push(rid);continue}if(row.parts||row.run)continue;
    for(let c=4;c<colStop(ws,colRow,rng);c++){if(colIds[c])continue;const x=ws[XLSX.utils.encode_cell({r,c})];if(!x||x.v==null||x.v===''||(x.t==='z'&&!x.f))continue;if(x.f&&!/^[0-9+*().\s]+$/.test(x.f))continue;noKey.add(c)}
    Object.keys(colIds).forEach(ci=>{const cid=colIds[ci];if(!leaf.includes(cid))return;const cell=ws[XLSX.utils.encode_cell({r,c:+ci})];
      let raw='';if(cell&&!(cell.t==='z'&&!cell.f)){if(cell.f&&/^[0-9+*().\s]+$/.test(cell.f))raw=cell.f;else if(cell.f&&/^\((.*)\)\/1000$/.test(cell.f))raw=cell.f.match(/^\((.*)\)\/1000$/)[1];else if(cell.v!=null&&cell.v!=='')raw=typeof cell.v==='number'?String(Math.round(cell.v*1e6)/1e6):String(cell.v)}
      const key=rid+'|'+cid,old=t.cells[key]||'';
      const o=evalRaw(old,row),n=evalRaw(raw,row);
      if(old===raw||(o.ok&&n.ok&&o.v===n.v))same++;else{if(!n.ok)bad++;diffs.push({t,key,row,cid,old,raw,o,n})}})}
  const runCh=parseRunList(wb,t);pending={t,diffs,runCh};showPreview(t,diffs,same,bad,unk,false,{unkCol,noKey:[...noKey].map(c=>XLSX.utils.encode_col(c)),runCh})}
function parseRunList(wb,t){const ws=wb.Sheets['Run list'],ls=wb.Sheets['Lists'];if(!ws||!ls||!t.rows.some(r=>r.run))return[];
  const pm={},cm={};const lr=XLSX.utils.decode_range(ls['!ref']);
  for(let r=lr.s.r;r<=lr.e.r;r++){const a=ls[XLSX.utils.encode_cell({r,c:0})],b=ls[XLSX.utils.encode_cell({r,c:1})];if(a&&b)pm[a.v]=b.v;const d=ls[XLSX.utils.encode_cell({r,c:3})],e=ls[XLSX.utils.encode_cell({r,c:4})];if(d&&e)cm[d.v]=e.v}
  const g=(r,c)=>{const x=ws[XLSX.utils.encode_cell({r,c})];return x&&x.v!=null?x.v:''};const rg=XLSX.utils.decode_range(ws['!ref']);const by={};
  for(let r=7;r<=rg.e.r;r++){const pn=g(r,1),cn=g(r,6);if(!pn&&!cn&&g(r,3)===''&&g(r,7)==='')continue;const pid=pm[pn],cid=cm[cn];if(!pid||!cid)continue;
    const n=g(r,7),L=g(r,8);if(n===''&&L==='')continue;(by[pid]=by[pid]||[]).push({desc:String(g(r,3)),from:String(g(r,4)),to:String(g(r,5)),c:cid,n:n===''?null:+n,L:L===''?null:+L})}
  const out=[];const ids=new Set([...Object.keys(by),...Object.keys(t.runs).filter(k=>(t.runs[k]||[]).length)]);
  ids.forEach(pid=>{const a=JSON.stringify(t.runs[pid]||[]),b=JSON.stringify(by[pid]||[]);if(a!==b)out.push({pid,old:(t.runs[pid]||[]).length,list:by[pid]||[]})});return out}
function colStop(ws,colRow,rng){let last=1;for(let c=2;c<=rng.e.c;c++)if(ws[XLSX.utils.encode_cell({r:colRow,c})])last=c;return last+1}
function samplePreview(){const t=cur;if(t.rows.some(r=>!r.parts&&!r.run)){
  const diffs=[];const rows=t.rows.filter(r=>!r.parts),ids=leafIds(t);let same=0;
  const pick=[[0,0,'41'],[1,0,'52+3.5'],[2,1,'18'],[0,2,'12.5'],[3,1,'-4']];
  pick.forEach(([ri,ci,raw])=>{const row=rows[ri%rows.length],cid=ids[ci%ids.length].id;const key=row.id+'|'+cid,old=t.cells[key]||'';diffs.push({t,key,row,cid,old,raw,o:evalRaw(old,row),n:evalRaw(raw,row)})});
  const bad=diffs.filter(d=>!d.n.ok).length;pending={t,diffs};showPreview(t,diffs,Object.keys(t.cells).length-3,bad,[],true)}}
function showPreview(t,diffs,same,bad,unk,sample,warn){warn=warn||{unkCol:[],noKey:[]};
  const ok=diffs.filter(d=>d.n.ok);const nameOf=cid=>{const g=t.groups.find(g=>g.id===cid||(g.kids&&g.kids.some(k=>k.id===cid)));const kid=g&&g.kids&&g.kids.find(k=>k.id===cid);return(g&&kid?g.name+' ▸ '+kid.name:g?g.name:cid)};
  let rowsH=diffs.slice(0,40).map(d=>`<tr><td>${esc(d.row.name)}</td><td>${esc(nameOf(d.cid))}</td><td class="n">${esc(d.old||'—')}</td><td class="n">${esc(d.raw||'(khali)')}</td><td>${d.n.ok?(d.old===''?'<span class="chip g">Naya</span>':d.raw===''?'<span class="chip a">Hatega</span>':'<span class="chip a">Badlega</span>'):'<span class="chip r">'+esc(d.n.err)+'</span>'}</td></tr>`).join('');
  modal('Import preview',`${sample?'<div class="chip s">Sample preview, file nahi padhi gayi</div>':''}<div style="display:flex;gap:8px;flex-wrap:wrap"><span class="chip g">${ok.length} cells badlenge</span><span class="chip s">${same} same hain</span>${bad?`<span class="chip r">${bad} galat, chhode jayenge</span>`:''}${unk.length?`<span class="chip a">${unk.length} item pehchane nahi</span>`:''}${warn.unkCol.length?`<span class="chip a">${warn.unkCol.length} jagah (column) pehchani nahi, chhodi</span>`:''}${warn.noKey.length?`<span class="chip a">Column ${warn.noKey.join(', ')} me key nahi, padha nahi</span>`:''}${(warn.runCh||[]).length?`<span class="chip g">Run list badli: ${warn.runCh.map(x=>esc((leafIds(t).find(k=>k.id===x.pid)||{name:x.pid}).name)+' ('+x.old+' > '+x.list.length+' runs)').join(', ')}</span>`:''}</div>${(warn.unkCol.length||warn.noKey.length)?'<p class="note">Excel me jo column khud jodha ho, uske upar hidden key row me id nahi hoti. Pehle yahan "+ Jagah" se jodo aur wahi Export karo.</p>':''}${diffs.length?`<div class="tw"><table class="tbl"><thead><tr><th>BOQ line</th><th>Jagah</th><th>Abhi</th><th>File me</th><th>Kya hoga</th></tr></thead><tbody>${rowsH}</tbody></table></div>`:'<p>Koi fark nahi. File grid se milti hai.</p>'}<p class="note">Apply karne par yeh cells grid me peeli bindi ke saath aayenge. Save dabane tak kuch nahi badalta.</p>`,
    `<button class="btn" id="icx">Cancel</button><button class="btn pri" id="iok"${(ok.length||(warn.runCh||[]).length)?'':' disabled'}>${ok.length+(warn.runCh||[]).length?ok.length+' cells'+((warn.runCh||[]).length?' + '+warn.runCh.length+' run list':'')+' apply karo':'Kuch apply karne ko nahi'}</button>`);
  $('#icx').onclick=closeLayer;$('#iok').onclick=()=>{const {t,diffs,runCh}=pending;cur=t;(runCh||[]).forEach(x=>{t.runs[x.pid]=x.list});renderTabs();mount();setCells(diffs.filter(d=>d.n.ok).map(d=>({key:d.key,val:d.raw})));closeLayer();paintSave();toast(ok.length+' cells grid me aaye. Dekh ke Save karo.')}}


/* ---------- place add / rename (ids never change, so data and Excel match survive) ---------- */
function placeDialog(focusId){const t=cur;const P=leafIds(t);
  const grp=t.groups.filter(g=>g.kids);
  const rows=P.map(k=>{const g=t.groups.find(g=>g.kids&&g.kids.some(x=>x.id===k.id));return `<tr><td class="n" style="color:var(--ink3)">${esc(k.id)}</td><td>${g?esc(g.name):''}</td><td><input class="tx" data-pid="${esc(k.id)}" value="${esc(k.name)}"></td></tr>`}).join('');
  modal('Jagah ki list · '+t.name,`<p class="note">Naam badalne se data nahi tootta, kyunki har jagah ki ek pakki id hoti hai (pehla column). Excel me bhi isi id se match hota hai, naam se nahi.</p><div class="tw" style="max-height:300px"><table class="tbl" id="pltbl"><thead><tr><th>Id</th><th>Group</th><th>Naam</th></tr></thead><tbody>${rows}</tbody></table></div>
  <div style="display:flex;gap:8px;margin-top:12px;flex-wrap:wrap;align-items:center"><b>Nayi jagah</b>${grp.length?`<select id="plg">${grp.map(g=>`<option value="${esc(g.id)}">${esc(g.name)}</option>`).join('')}</select>`:''}<input class="tx" id="pln" placeholder="jaise F.11 ya Level 3 · Zone C" style="width:220px"><button class="btn sm" id="pla">Jodo</button></div>`,'<button class="btn pri" id="pld">Ho gaya</button>');
  $('#pltbl').addEventListener('change',e=>{const i=e.target.closest('[data-pid]');if(!i)return;const k=leafIds(t).find(x=>x.id===i.dataset.pid);const v=i.value.trim();if(!k||!v){if(k)i.value=k.name;return}
    k.name=v;const g=t.groups.find(g=>g.id===k.id);if(g)g.name=v;mount();toast('Naam badla. Data wahi hai.')});
  $('#pla').onclick=()=>{const n=$('#pln').value.trim();if(!n)return;const gsel=$('#plg');
    t.pseq=(t.pseq||0)+1;const id='P'+Date.now().toString(36)+t.pseq;
    if(gsel){const g=t.groups.find(g=>g.id===gsel.value);g.kids.push({id,name:n});g.open=true}else t.groups.push({id,name:n});
    mount();placeDialog();toast('Jagah jud gayi. Excel export me bhi aayegi.')};
  $('#pld').onclick=closeLayer}
const PM=(typeof OPTS!=='undefined'&&!!OPTS.placesManaged);
if(PM)$('#bPlace').hidden=true;else $('#bPlace').onclick=()=>placeDialog();
gridEl.addEventListener('dblclick',e=>{const lh=e.target.closest('th[data-lc],th[data-g]');if(!lh)return;if(PM)toast('Jagah ka naam / nayi jagah Site Progress ke Structure se badalti hai. Grid khud follow karega.');else if(lh.dataset.lc)placeDialog(lh.dataset.lc)});

/* ---------- mobile ---------- */
let mp=0;
function places(t){const a=[];t.groups.forEach(g=>{if(g.kids)g.kids.forEach(k=>a.push({id:k.id,label:g.name+' ▸ '+k.name}));else a.push({id:g.id,label:g.name})});return a}
function renderMobile(){const el=$('#mob');if(!el||getComputedStyle(el).display==='none')return;const t=cur,P=places(t);if(mp>=P.length)mp=0;const pl=P[mp];
  let h=`<div class="mpick"><button class="btn" id="mp0" aria-label="Pichhli jagah">‹</button><select id="msel" aria-label="Jagah">${P.map((p,i)=>`<option value="${i}"${i===mp?' selected':''}>${esc(p.label)}</option>`).join('')}</select><button class="btn" id="mp1" aria-label="Agli jagah">›</button></div>`;
  t.rows.forEach(row=>{const c=cellVal(t,row,pl.id);const unit=row.mm?'mm':row.unit;
    h+=`<div class="mrow"><div class="nm">${esc(row.name)}</div>`;
    if(row.run&&runSum(t,row,pl.id)!=null){const v=runSum(t,row,pl.id);h+=`<div class="n" style="text-align:right;font-family:var(--mono)">${fmt(v,2)}</div><div class="sub">${row.code} · Mtr · run list se</div><button class="btn sm" data-mr2="${esc(pl.id)}">Run list</button>`}
    else if(row.parts){const v=sqmCell(t,row,pl.id);h+=`<div class="n" style="text-align:right;font-family:var(--mono)">${v==null?'—':fmt(v,2)}</div><div class="sub">${row.code} · Sqm · parts se</div><button class="btn sm" data-mp="${esc(pl.id)}">Parts</button>`}
    else{h+=`<input data-mr="${esc(row.id)}" value="${esc(c.raw)}" inputmode="decimal" class="${c.ok?'':'err'}" aria-label="${esc(row.name)}"><div class="sub">${row.code} · ${esc(unit)}${row.mm?' (Mtr me total)':''}${c.ok&&c.pieces&&c.pieces.length>1?' · '+c.pieces.length+' tukde = '+fmt(c.v,2):''}</div>${c.ok?'':`<div class="err-t">${esc(c.err)}</div>`}`}
    h+='</div>'});
  el.innerHTML=h;
  $('#mp0').onclick=()=>{mp=(mp+P.length-1)%P.length;renderMobile()};$('#mp1').onclick=()=>{mp=(mp+1)%P.length;renderMobile()};$('#msel').onchange=e=>{mp=+e.target.value;renderMobile()};
  el.querySelectorAll('input[data-mr]').forEach(i=>i.addEventListener('change',()=>{const rid=i.dataset.mr;setCells([{key:rid+'|'+pl.id,val:i.value.trim()}])}));
  el.querySelectorAll('[data-mp]').forEach(b=>b.onclick=()=>openParts(b.dataset.mp));el.querySelectorAll('[data-mr2]').forEach(b=>b.onclick=()=>openRuns(b.dataset.mr2))}

/* ---------- toast ---------- */
let tt;function toast(m){let e=$('.toast');if(!e){e=document.createElement('div');e.className='toast';e.setAttribute('role','status');SR.querySelector('.mgroot').appendChild(e)}e.textContent=m;clearTimeout(tt);tt=setTimeout(()=>e.remove(),3200)}

OPTS.__test=window.__MG={T,get cur(){return cur},select,evalRaw,partArea,rowTotal,mount,repaintAll,cellVal,get VR(){return VR},get VC(){return VC},setCells};
renderTabs();mount();renderMobile();
winOn('resize',()=>{renderMobile();paintSel()});
if(matchMedia('(min-width:721px)').matches){select(1,2);}

    return live;
  }
  window.MIGrid = { open };
})();
