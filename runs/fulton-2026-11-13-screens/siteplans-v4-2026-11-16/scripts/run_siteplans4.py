import csv, json, sys, traceback
from pathlib import Path
sys.path.insert(0, "/tmp/claude-0/-home-user-Land-Research/65a03f9d-728a-5dc9-93af-1f252a5b2d29/scratchpad")
sys.path.insert(0, ".")
import siteplan4 as sp
import siteplan2 as sp2
from shapely.geometry import shape
from shapely.ops import unary_union
RUN = Path("/home/user/screener-data/runs/fulton-2026-11-13-screens")
OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
which = sys.argv[2].split(",")
feats = {f["properties"]["rank"]: f for f in json.load(open(RUN / "ranked.geojson"))["features"]}
rows = {int(r["rank"]): r for r in csv.DictReader(open(RUN / "ranked.csv", encoding="utf-8-sig"))}
recs = []
_done = {x.get("rank") for x in (json.load(open(OUT / "plans4.json")) if (OUT / "plans4.json").exists() else [])}
for w in which:
    if w in _done and "--redo" not in sys.argv:
        continue
    try:
        if w == "115+170":
            cand = sp2.arcgis.request_json(sp2.PARCELS + "/query", {"where": "ParcelID IN ('07 260001350151','07 260001221014','07 260001351027','07 260001350904')",
                       "outFields": "ParcelID,Owner", "returnGeometry": "true", "outSR": 4326})
            g = unary_union([sp2.esri_to_shape(f["geometry"]) for f in cand["features"] if (f["attributes"].get("Owner") or "").upper().startswith("BR FAIRBURN")])
            r = rows[115]; title = "#115 + #170 + 7460/7490 Herndon Rd (same owner), South Fulton"
        else:
            k = int(w); g = shape(feats[k]["geometry"]); r = rows[k]
            title = f"#{k} {r['site_address']}, {r['jurisdiction']} ({float(r['acres']):.1f} ac tax roll)"
        meta = {"jurisdiction": r["jurisdiction"], "hsg": r["soil_hsg"] or "B", "zoning": r["zoning"]}
        cut = None
        if w == "25":   # the neighbouring cemetery parcel + a 100 ft test buffer (see the test fits)
            cem = sp2.arcgis.request_json(sp2.PARCELS + "/query", {"where": "ParcelID = '09F350101520017'", "outFields": "ParcelID", "returnGeometry": "true", "outSR": 4326})
            cg = sp2.esri_to_shape(cem["features"][0]["geometry"]); pl = sp2.Plane(g)
            cut = pl.wgs(pl.ft(cg).buffer(100)); title += " - 100 ft cemetery test buffer"
        tax = float(r["acres"]) if w != "115+170" else 73.4
        rec, svg = sp.plan_site(w.replace("+", "-"), title, g, meta, extra_cut_wgs=cut, tax_acres=tax)
        rec["rank"] = w
        if svg:
            (OUT / f"plan4-{w.replace('+','-')}.svg").write_text(svg, encoding="utf-8")
            rec["svg"] = f"plan4-{w.replace('+','-')}.svg"
        recs.append(rec)
        _old = json.load(open(OUT / "plans4.json")) if (OUT / "plans4.json").exists() else []
        json.dump([x for x in _old if x.get("rank") != w] + [rec], open(OUT / "plans4.json", "w"), indent=1, default=str)
        b = rec.get("building", {})
        print(w, rec.get("configuration"), b.get("sf"), "pond", rec.get("detention", {}).get("fits"), "doors", rec.get("doors"),
              "trailers", rec.get("trailers", {}).get("total"), "cars", rec.get("cars", {}).get("provided"), flush=True)
    except Exception as exc:
        traceback.print_exc()
        recs.append({"rank": w, "error": str(exc)[:300]})
print("done")
