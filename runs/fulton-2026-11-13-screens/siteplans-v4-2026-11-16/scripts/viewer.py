"""Site plan viewer: one self-contained HTML tool holding every site plan of
the run (briefs/*.svg) plus this session's test fits. No owner names or
mailing addresses are included. Usage:
    python3 viewer.py RUN_DIR DECISIONS_CSV TESTFITS_DIR OUT_HTML LEAD_ROOT...
"""
import csv, glob, html, json, re, sys
from pathlib import Path

run_dir, dec_csv, tf_dir, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
lead_roots = [Path(p) for p in sys.argv[5:7]]
land_json = Path(sys.argv[7]) if len(sys.argv) > 7 else None
land = {x["rank"]: x for x in json.load(open(land_json))} if land_json and land_json.exists() else {}
LAND_LABEL = {"raw_land": "Raw land", "partly_usable": "Partly usable", "not_raw_land": "Not raw land", "unclear": "Unclear"}

rows = {int(r["rank"]): r for r in csv.DictReader(open(run_dir / "ranked.csv", encoding="utf-8-sig"))}
owner_bits = {v for r in rows.values() for v in (r["owner"].strip(), r["owner_mailing"].strip()) if len(v) > 6}
decisions = {}
for r in csv.DictReader(open(dec_csv, encoding="utf-8-sig")):
    decisions[r["parcel_id"]] = r


def clean_svg(s):
    s = re.sub(r"<\?xml[^>]*>", "", s)
    low = s.lower()
    if "<script" in low or "foreignobject" in low or "href=" in low:
        raise SystemExit("unexpected SVG content")
    if re.search(r"\son\w+\s*=", s):
        raise SystemExit("event handler in SVG")
    return s.strip()


def svg_meta(s):
    """County-map acres from the legend and the scale bar (ft per SVG px)."""
    ac = re.search(r"Parcel \(county map\) ([\d,.]+) ac", s)
    bar = (re.search(r'class="scale" d="M([\d.]+),[\d.]+ L[\d.]+,[\d.]+ L([\d.]+),', s)
           or re.search(r'<path d="M([\d.]+),[\d.]+ L([\d.]+),[\d.]+" stroke="[^"]+" stroke-width="3"/><text[^>]*>[\d,]+ ft</text>', s))
    ft = re.search(r'>([\d,]+) ft</text>', s)
    fpp = None
    if bar and ft:
        px = float(bar.group(2)) - float(bar.group(1))
        fpp = float(ft.group(1).replace(",", "")) / px if px > 0 else None
    return (float(ac.group(1).replace(",", "")) if ac else None), fpp


def lead_check(pid):
    f = pid.replace(" ", "_")
    for root in lead_roots:
        d = root / f
        if (d / "findings.checked.json").exists() and (d / "lead.md").exists():
            c = json.loads((d / "findings.checked.json").read_text())
            md = (d / "lead.md").read_text(encoding="utf-8")
            m = re.search(r"\*\*In one line[^*]*\*\*\s*(.+)", md)
            return (c.get("skeptic") or {}).get("verdict"), (m.group(1).strip() if m else None)
    return None, None


def status(pid):
    d = decisions.get(pid)
    if not d:
        return "unchecked"
    if d["decision"] == "pass":
        return "drop"
    if d["decision"] == "pursue":
        return "open"
    if d["decision"] == "listed":
        return "listed"
    if "probably taken" in d["note"]:
        return "taken"
    return "doubt"


def r1(x):
    try:
        return round(float(x), 1)
    except (TypeError, ValueError):
        return None


plans = []
for svgp in sorted(glob.glob(str(run_dir / "briefs" / "*.svg"))):
    rank = int(Path(svgp).name.split("_", 1)[0])
    r = rows[rank]
    pid = r["parcel_id"]
    verdict, headline = lead_check(pid)
    svg = clean_svg(Path(svgp).read_text(encoding="utf-8"))
    map_ac, fpp = svg_meta(svg)
    std_fits = r["envelope_fit"].startswith(("cross", "front"))
    plans.append({
        "id": f"run-{rank}", "kind": "run", "rank": rank, "pid": pid,
        "title": f"#{rank} {r['site_address']}", "place": r["jurisdiction"],
        "taxAc": r1(r["acres"]), "mapAc": map_ac, "fpp": fpp,
        "zoning": f"{r['zoning'] or 'unknown'} ({r['entitlement'].replace('_', ' ')})",
        "grade": r["lead_grade"], "status": status(pid),
        "fit": r["envelope_fit"], "sf": int(float(r["envelope_building_sf"])) if r["envelope_building_sf"] else 0,
        "stdFits": std_fits, "pond": r["pond_fit"], "open": r1(r["largest_open_acres"]),
        "flood": r["flood_pct"], "wet": r["wetland_pct"], "steep": r["steep_pct"],
        "frontage": float(r["frontage_ft"] or 0), "frontageRoad": r["frontage_road"],
        "exit": f"{r['exit_miles']} mi straight line to {r['exit_label']}",
        "skeptic": verdict, "headline": headline, "note": (decisions.get(pid) or {}).get("note"),
        "maps": r["maps_link"], "svg": svg,
        "land": (land.get(rank) or {}).get("land_today"), "landWhat": (land.get(rank) or {}).get("what_is_there"),
    })

for t in json.load(open(tf_dir / "plans4.json")):
    if not t.get("svg") or not t.get("fits"):
        continue
    key = str(t["rank"])
    rank = int(key.split("+")[0])
    r = rows[rank]
    svg = clean_svg((tf_dir / t["svg"]).read_text(encoding="utf-8"))
    map_ac, fpp = svg_meta(svg)
    sev = t["flags"]
    plans.append({
        "id": f"v4-{t['id']}", "kind": "v4", "rank": rank, "pid": r["parcel_id"], "title": t["title"], "place": r["jurisdiction"],
        "taxAc": r1(r["acres"]) if "+" not in key else None, "mapAc": t["coverage"]["gross_ac"], "fpp": fpp,
        "zoning": f"{r['zoning'] or 'unknown'} ({r['entitlement'].replace('_', ' ')})", "grade": r["lead_grade"], "status": status(r["parcel_id"]),
        "fit": t["configuration"], "sf": t["total_sf"], "sfIfAccess": t["total_sf_if_access"], "stdFits": True,
        "feasible": t.get("feasible"), "hypothetical": t.get("hypothetical"),
        "pond": ("fits" if t["detention"]["fits"] else "does not fit (see flags)"), "open": None,
        "v4": {k: t[k] for k in ("building", "doors", "trailers", "cars", "coverage", "detention", "access", "grading", "second_building") if k in t},
        "flags1": sev.get("1", sev.get(1, [])), "flags2": sev.get("2", sev.get(2, [])), "flags3": sev.get("3", sev.get(3, [])),
        "alternatives": t.get("alternatives", [])[:6], "related": f"run-{rank}" if any(Path(x).name.startswith(f"{rank:03d}_") for x in glob.glob(str(run_dir / "briefs" / "*.svg"))) else None,
        "land": (land.get(rank) or {}).get("land_today"), "landWhat": (land.get(rank) or {}).get("what_is_there"),
        "svg": svg,
    })

blob = json.dumps(plans)
leak = [b for b in owner_bits if b in blob]
if leak:
    raise SystemExit(f"owner data in viewer: {len(leak)} strings")
data = blob.replace("</", "<\\/")
page = (Path(__file__).with_name("viewer_template.html").read_text(encoding="utf-8")
        .replace("__DATA__", data).replace("__RUN__", html.escape(run_dir.name)))
out.write_text(page, encoding="utf-8")
print(out, len(page), "bytes;", len(plans), "plans")
