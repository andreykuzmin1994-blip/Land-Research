"""Site plan viewer: one self-contained HTML tool holding every site plan of
the run (briefs/*.svg) plus this session's test fits. No owner names or
mailing addresses are included. Usage:
    python3 viewer.py RUN_DIR DECISIONS_CSV TESTFITS_DIR OUT_HTML LEAD_ROOT...
"""
import csv, glob, html, json, re, sys
from pathlib import Path

run_dir, dec_csv, tf_dir, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
lead_roots = [Path(p) for p in sys.argv[5:]]

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
    bar = re.search(r'class="scale" d="M([\d.]+),[\d.]+ L[\d.]+,[\d.]+ L([\d.]+),', s)
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
    })

RELATED = {"21-small": "run-21", "21-assemblage": "test-21-small", "11-small": "run-11", "115-170-assemblage": "run-115",
           "25-cemetery": "run-25", "65-residential": "run-65", "170-standard": "run-115"}
EXPLAIN = {
    "21-small": "The operator's prototypes don't fit #21. This tries them and four smaller test buildings (not the operator's prototypes), keeps room for the 400 x 200 ft detention pond, and cuts residential zoning nearby plus a 100 ft test buffer (the houses across Waters Rd). The largest building that leaves pond room is drawn.",
    "21-assemblage": "#21 plus the lots the county parcel layer lists under the same owner name in a box around it. Two house lots and a vacant lot are zoned R-4 (1.3 ac together): the fit assumes they are bought and rezoned industrial; the rear lot is already I-1. Residential zoning outside the assemblage gets a 100 ft test buffer, and room is kept for the pond.",
    "11-small": "The operator's prototypes don't fit #11. This tries them and four smaller test buildings (not the operator's prototypes) and keeps room for the detention pond.",
    "115-170-assemblage": "#115, #170 and the same partnership's 7460 and 7490 Herndon Rd parcels, merged (7635 Bishop Rd, a different owner, left out). The operator's prototypes are fitted with room for a pond, then a second building and its own pond on what is left after an 80 ft drive strip around the first. No residential check here: only Atlanta's zoning layer is read.",
    "25-cemetery": "A 100 ft test buffer from the neighbouring cemetery parcel (09F350101520017, about 188 ft of shared line) is cut (red hatch). State law sets no buffer; 100 ft is an assumption, and unmarked burials may lie beyond the recorded line. Room is kept for the pond.",
    "65-residential": "The R-4 part of the parcel and a 100 ft test buffer from all residential zoning around it (city zoning layer) are cut (red hatch). Room is kept for the pond.",
    "170-standard": "The run ranked #170 outside the set that gets plans drawn. Drawn here with the screener's own constraint layers, fit search, pond placement and renderer; the result matches the run (640 x 260 ft, 166,400 SF, pond fits).",
}
for t in json.load(open(tf_dir / "testfits.json")):
    if not t.get("svg"):
        continue
    rank = int(re.match(r"\d+", t["id"]).group(0))
    r = rows[rank]
    svg = clean_svg((tf_dir / t["svg"]).read_text(encoding="utf-8"))
    map_ac, fpp = svg_meta(svg)
    dr = t["drawn"]
    std = t["standard_fit_after_test_cut"]
    plans.append({
        "id": f"test-{t['id']}", "kind": "test", "rank": rank, "pid": r["parcel_id"],
        "title": t["title"], "place": r["jurisdiction"],
        "taxAc": t.get("parcel_acres_tax_roll"), "mapAc": map_ac or t["parcel_acres_map"], "fpp": fpp,
        "zoning": f"{r['zoning'] or 'unknown'} ({r['entitlement'].replace('_', ' ')}) on the lead parcel",
        "grade": r["lead_grade"], "status": status(r["parcel_id"]),
        "fit": (f"{dr['label']} {dr['length_ft']:.0f} x {dr['width_ft']:.0f} ft" if dr["label"] else "none found"),
        "sf": t["total_sf"] or 0, "stdFits": std["state"] == "fits", "pond": t["pond"],
        "open": t["largest_open_acres"], "steep": t.get("steep_pct"), "cut": t.get("test_cut_pct"),
        "standard": std["text"] + (f" ({std['sf']:,} SF, before leaving room for the pond)" if std.get("sf") else "") + (" after the test cut" if t.get("test_cut_pct") else ""),
        "runFit": t.get("run_envelope_fit"),
        "maxNoPond": t.get("max_sf_ignoring_pond_same_building"), "candidates": t.get("candidates"), "second": t.get("second_building"),
        "explain": EXPLAIN.get(t["id"]), "scenario": t.get("note"), "related": RELATED.get(t["id"]), "svg": svg,
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
