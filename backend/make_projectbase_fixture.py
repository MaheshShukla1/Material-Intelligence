"""Builds frontend/projectbase_fixture.json from the REAL backend (scratch app on :8765 running the Thoth project) and the real
ProjectBase exports. Run: python3 frontend/make_projectbase_fixture.py   (the app must be up; nothing is changed in it)."""
import json, sys, urllib.request, urllib.parse, io, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import projectbase as pb

U = "/root/.claude/uploads/cc641dec-fdd2-5679-9e93-268c1cf0e51e/"
EL = U + "4e0aaadc-Billing_Request-undefined.xlsx"
H = "http://localhost:8765/api/billing/thoth-mall"


def get(p):
    return json.load(urllib.request.urlopen(H + p))


def post(p, body=None, raw=None, ctype="application/json"):
    data = raw if raw is not None else json.dumps(body).encode()
    req = urllib.request.Request(H + p, data=data, headers={"Content-Type": ctype}, method="POST")
    return json.load(urllib.request.urlopen(req))


def multipart(path):
    b = uuid.uuid4().hex
    data = (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="BR.xlsx"\r\nContent-Type: application/octet-stream\r\n\r\n').encode() + open(path, "rb").read() + f"\r\n--{b}--\r\n".encode()
    return data, f"multipart/form-data; boundary={b}"


out = {}
ov = get("")
out["ov0"] = {**ov, "pb": {}}
data, ct = multipart(EL)
out["inspect"] = post("/projectbase/Electrical/inspect", raw=data, ctype=ct)
out["connect"] = post("/projectbase/Electrical/connect", {"confirm": {}, "name": "BR.xlsx"})
out["ov1"] = get("")
ids = [r["id"] for r in ov["ras"]]
out["ids"] = ids
out["preview"] = get("/projectbase/preview?ras=" + ",".join(ids))
for r in out["preview"]["ras"]:
    print(r["id"], r["label"], r["expect_total"])
rep = {r["id"]: r for r in out["preview"]["ras"]}
a = ids[0]
mine = rep[a]["expect_total"]
out["check_ok"] = post(f"/ra/{a}/pb-check", {"subtotal": mine})
# a gap that exactly one line explains: 3 units of the first filled line
bill = get("/ra/" + a)
ln = next(l for l in bill["lines"] if (l.get("supply") or {}).get("this"))
step = ln["rate"] * (ln["supply"]["pct"]) / 100
out["check_bad"] = post(f"/ra/{a}/pb-check", {"subtotal": mine - 3 * step})
out["check_far"] = post(f"/ra/{a}/pb-check", {"subtotal": mine - 1})
# a near-match question, produced by the real matcher on the real file: one MI line without an Internal Ref
p = pb.read(open(EL, "rb").read())
ref, g = next((r, g) for r, g in p["groups"].items() if g["client"] and "#" not in g["client"] and isinstance(g["qty"], (int, float)))
m = pb.match([{"item_code": g["client"], "internal_ref": None, "desc": "MI wording of this line", "unit": g["unit"], "boq_qty": g["qty"]}], p)
out["ask"] = m["ask"]
print("ask", m["ask"][:1])
# put the scratch app back to 'not connected'
req = urllib.request.Request(H + "/projectbase/Electrical", method="DELETE")
urllib.request.urlopen(req)
Path(__file__).with_name("projectbase_fixture.json").write_text(json.dumps(out, ensure_ascii=False))
print("ok", {k: type(v).__name__ for k, v in out.items()})
