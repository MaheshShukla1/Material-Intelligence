import sys, json
from pathlib import Path
import pandas as pd
ROOT=Path(sys.argv[1]); sys.path.insert(0,str(ROOT))
from backend import structure
p=ROOT/"data"/"projects"/"thoth-mall"
s=structure.mall("Thoth Mall",levels=["B3","B2","B1"],zone_labels=[f"Zone {i}" for i in range(1,8)])
rooms=[r["id"] for r in s.rooms()]            # 21 zones in tree order
A,B=rooms[:7],rooms[7:14]
b=pd.read_parquet(p/"boq.parquet"); b=b[b.service!="Fire"]
items=[("QI1","MS C CLASS PIPE 100MM",796.0),("QI2","MS C CLASS PIPE 150MM",5120.0),("QI3","MS C CLASS PIPE 200MM",3200.0),("QI4","MS C CLASS PIPE 250MM",1800.0),
       ("QI5","MS ELBOW 90 DEG 100MM",120.0),("QI6","MS TEE 100MM",80.0),("QI7","PIPE CLAMP 100MM",900.0),("QI8","FLANGE 100MM",60.0)]
rows=[{"service":"Fire","item_code":c,"description":d,"unit":("MTR" if i<4 else "Nos"),"qty":q,"section":None,"item_code_raw":c,"subcategory":"Pipe"} for i,(c,d,q) in enumerate(items)]
pd.concat([b,pd.DataFrame(rows)],ignore_index=True).to_parquet(p/"boq.parquet")
def upd(f,fn):
    x=json.load(open(p/f)) if (p/f).exists() else {}
    fn(x); json.dump(x,open(p/f,"w"))
upd("activities.json",lambda x:x.update({"Fire":["Piping Work","Sprinkler Heads"]}))
upd("mapping.json",lambda x:x.update({"Fire":{"Piping Work":[c for c,_,_ in items],"Sprinkler Heads":[]}}))
upd("rates.json",lambda x:x.update({"Fire":{c:3020.0 for c,_,_ in items}}))
upd("item_room_qty.json",lambda x:x.setdefault("Fire",{}).update({"QI1":[{"rooms":A,"qty":60.0},{"rooms":B,"qty":376/7}]}))
upd("planned.json",lambda x:x.setdefault("Fire",{}).update({"QI2":5120.0,"QI3":3200.0,"QI4":1800.0}))
upd("item_progress.json",lambda x:x.setdefault("Fire",{}).update({"QI1":{"*":0.7399},"QI2":{"*":0.113},"QI3":{"*":0.4}}))
