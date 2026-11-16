"""Build one self-contained lead pack HTML from a screener run folder.

Reads only: the run folder (leads.md, ranked.csv, ranked.geojson, briefs/,
summary.md), decisions.csv, and leads/<parcel>/ folders (lead.md and
findings.checked.json written by quotecheck). Writes one HTML file.
Every piece of text from those files is HTML-escaped; only URLs of kept
claims (quotecheck-verified) and the run's own map/county links become links.
"""
import csv, html, json, re, sys
from pathlib import Path

run_dir, decisions_csv, out_path = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
lead_roots = [Path(p) for p in sys.argv[4:]]
checked_today = set()  # parcel folders checked in this session (passed via env-like marker file)
marker = out_path.with_suffix(".today.json")
if marker.exists():
    checked_today = set(json.loads(marker.read_text()))

E = lambda s: html.escape("" if s is None else str(s), quote=True)

def folder(pid): return pid.replace(" ", "_")

LAND = {}
try:
    LAND = {x["rank"]: x for x in json.load(open("/home/user/Land-Research/screener/output/builtcheck-2026-11-16/land_today.json"))}
except Exception:
    pass
LANDL = {"raw_land": "raw land", "partly_usable": "partly usable", "not_raw_land": "NOT raw land", "unclear": "unclear"}
rows = {r["parcel_id"]: r for r in csv.DictReader(open(run_dir / "ranked.csv", encoding="utf-8-sig"))}
geo = {f["properties"]["parcel_id"]: f for f in json.load(open(run_dir / "ranked.geojson"))["features"]}
decisions = {}
for r in csv.DictReader(open(decisions_csv, encoding="utf-8-sig")):
    decisions[r["parcel_id"]] = r  # later rows win

# Which parcels leads.md lists, in its order, with its open-item text
leads_md = (run_dir / "leads.md").read_text(encoding="utf-8")
listed = []
for m in re.finditer(r"\(briefs/(\d+)_([^)]+)\.md\)", leads_md):
    brief = f"{m.group(1)}_{m.group(2)}.md"
    rank = int(m.group(1))
    pid = next((p for p, r in rows.items() if int(r["rank"]) == rank), None)
    if pid: listed.append((pid, brief))
_listed_ids = {p for p, _ in listed}
for _f in sorted(checked_today):
    _pid = next((p for p in rows if folder(p) == _f), None)
    if _pid and _pid not in _listed_ids:
        listed.append((_pid, None))  # checked this session but not in leads.md's list: no brief
grade_counts = re.search(r"\d[\d,]* of [\d,]+ ranked parcels were graded:[^\n]*", leads_md).group(0)

def brief_sections(brief):
    if brief is None:
        return {"Next steps": ["No brief: this parcel ranks outside the briefed set; see its row in ranked.csv"]}, None
    t = (run_dir / "briefs" / brief).read_text(encoding="utf-8")
    secs, cur = {}, None
    for line in t.splitlines():
        if line.startswith("## "):
            cur = line[3:].strip(); secs[cur] = []
        elif cur and line.startswith("- "):
            secs[cur].append(line[2:])
    county = re.search(r"county record: (https://\S+)", t)
    return secs, county.group(1) if county else None

def lead_check(pid):
    for root in lead_roots:
        d = root / folder(pid)
        cj = d / "findings.checked.json"
        if cj.exists() and (d / "lead.md").exists():
            c = json.loads(cj.read_text())
            md = (d / "lead.md").read_text(encoding="utf-8")
            head = re.search(r"\*\*In one line[^*]*\*\*\s*(.+)", md)
            return c, (head.group(1).strip() if head else None), d
    return None, None, None

def md_inline(s):
    # brief lines are code-written; keep **bold** only, escape everything else
    s = E(s)
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)

def svg_for(brief):
    if brief is None:
        return '<p class="mute">No site plan: this parcel ranks outside the briefed set, so the run drew none.</p>'
    p = run_dir / "briefs" / brief.replace(".md", ".svg")
    if not p.exists(): return '<p class="mute">No site plan drawn.</p>'
    s = p.read_text(encoding="utf-8")
    s = re.sub(r"<\?xml[^>]*>", "", s)
    if "<script" in s.lower() or "foreignobject" in s.lower() or "href" in s.lower():
        return '<p class="mute">Site plan withheld (unexpected content).</p>'
    s = re.sub(r'(<svg[^>]*?)\swidth="[^"]*"\s+height="[^"]*"', r'\1', s, count=1)
    return s

def status_of(pid, check):
    d = decisions.get(pid)
    note = d["note"] if d else ""
    sk = (check or {}).get("skeptic") or {}
    v = sk.get("verdict")
    if (d and d["decision"] == "pass") or v == "killed":
        return "killed", "Ruled out" if v == "killed" else "Marked pass"
    if "probably taken" in note:
        return "taken", "Probably taken"
    if v == "weakened" or (d and d["decision"] == "maybe"):
        return "weak", "Open doubt"
    if v == "survives":
        return "open", "Survived skeptic"
    if d and d["decision"] == "pursue":
        return "open", "Marked pursue"
    return "unchecked", "No document check yet"

SECTION_NAMES = {"zoning": "Zoning text", "plans": "Plans", "utilities": "Utilities", "market": "Market signs", "owner": "Owner"}

def check_block(pid, check, headline, today):
    if not check:
        return '<div class="check none"><b>Document check:</b> none yet. Next step: run one (screener/INVESTIGATE.md) before any outreach.</div>'
    sk = check.get("skeptic") or {}
    qc = check.get("quotecheck", {})
    when = qc.get("checked_at", "")[:10]
    out = [f'<div class="check"><div class="checkhead"><b>Document check</b> ({E(check.get("run"))}, quotes verified {E(when)}{", this session" if today else ""}): '
           f'{E(qc.get("kept"))} quoted claims kept, {E(qc.get("dropped"))} dropped.']
    if sk:
        out.append(f' Skeptic: <b class="v-{E(sk.get("verdict"))}">{E(sk.get("verdict"))}</b>'
                   f'{"" if sk.get("supported") else " (its own quotes not found: opinion only)"}.')
    out.append('</div>')
    if headline:
        out.append(f'<p class="headline"><span class="lbl">Act on first (session\'s reading, not checked):</span> {E(headline)}</p>')
    if sk.get("objection"):
        out.append(f'<p class="obj"><span class="lbl">Skeptic\'s objection (agent\'s words):</span> {E(sk["objection"])}</p>')
    out.append('<table class="sec"><tr><th>Section</th><th>Verdict</th><th>Strongest kept quote</th></tr>')
    for k, name in SECTION_NAMES.items():
        s = check.get("sections", {}).get(k, {})
        cl = (s.get("claims") or [])
        q = ""
        if cl:
            c = cl[0]
            url = c.get("source_url", "")
            safe = url if re.match(r"^https?://", url) else ""
            q = (f'“{E(c.get("quote", "")[:400])}{"…" if len(c.get("quote", "")) > 400 else ""}”'
                 + (f' <a href="{E(safe)}" rel="noopener noreferrer" target="_blank">source</a>' if safe else ""))
        out.append(f'<tr><td>{name}</td><td class="v-{E(s.get("verdict", "unknown"))}">{E(s.get("verdict", "unknown"))}</td><td>{q}</td></tr>')
    out.append("</table>")
    qs = []
    for k in list(SECTION_NAMES) :
        qs += (check.get("sections", {}).get(k, {}).get("open_items") or [])[:2]
    qs += (sk.get("open_items") or [])[:2]
    if qs:
        out.append('<details><summary>Questions for a person (agents\' own words, not checked)</summary><ul>'
                   + "".join(f"<li>{E(q[:400])}{'…' if len(q) > 400 else ''}</li>" for q in qs[:10]) + "</ul></details>")
    out.append("</div>")
    return "".join(out)

def fmt_int(v):
    try: return f"{int(float(v)):,}"
    except Exception: return v or "—"

cards, map_feats = {}, []
for pid, brief in listed:
    r = rows[pid]
    secs, county = brief_sections(brief)
    check, headline, _ = lead_check(pid)
    today = folder(pid) in checked_today
    cls, label = status_of(pid, check)
    d = decisions.get(pid)
    lat = lon = None
    m = re.search(r"query=([-\d.]+),([-\d.]+)", r.get("maps_link", ""))
    if m: lat, lon = float(m.group(1)), float(m.group(2))
    open_item = r.get("lead_found") or r.get("lead_not_checked") or ""
    lt = LAND.get(int(r["rank"]))
    land_line = (f'<p class="openitem"><b>Land today (2024 aerial + footprints, 2026-11-16):</b> {E(LANDL.get(lt["land_today"], lt["land_today"]))}: {E(lt["what_is_there"][:300])}</p>' if lt else "")
    facts = [
        ("Score", f'{r["score"]} (rank #{r["rank"]} of {len(rows):,})'),
        ("Acres", f'{r["acres"]} ({r["usable_after_slope"]} usable; largest open part {r["largest_open_acres"]})'),
        ("Zoning", f'{r["zoning"] or "unknown"}; path {r["entitlement"]}; future land use {r["flu"] or "—"}'),
        ("Building fit", f'{r["envelope_fit"]}' + (f' ({fmt_int(r["envelope_building_sf"])} SF)' if r["envelope_building_sf"] else "")),
        ("Detention", f'{r["pond_fit"]}' + (f' ({r["detention_pct"]}% of open ground)' if r["detention_pct"] else "")),
        ("Access", f'{r["exit_miles"]} mi to {r["exit_label"]}; rail {fmt_int(r["rail_ft"])} ft'),
        ("Frontage", f'{fmt_int(r["frontage_ft"])} ft ({r["frontage_road"] or "—"})' if r["frontage_ft"] not in ("", "0", "0.0") else "none within 50 ft"),
        ("Ground", f'flood {r["flood_pct"]}%, wetlands {r["wetland_pct"]}%, steep {r["steep_pct"]}%, earthwork ~{fmt_int(r["earthwork_cy"])} CY; soils HSG {r["soil_hsg"] or "—"}, hydric {r["soil_hydric_pct"] or "—"}%'),
        ("Owner", f'{r["owner"]} ({r["owner_type"]}, held {r["years_held"] or "?"} yr; last sale {r["last_sale_date"] or "—"})'),
        ("Mailing", r["owner_mailing"] or "—"),
        ("Land value", f'${fmt_int(r["land_value"])}'),
    ]
    links = []
    if r.get("maps_link"): links.append(f'<a href="{E(r["maps_link"])}" target="_blank" rel="noopener noreferrer">Google Maps</a>')
    if lat: links.append(f'<a href="https://www.google.com/maps/@?api=1&amp;map_action=map&amp;center={lat},{lon}&amp;zoom=17&amp;basemap=satellite" target="_blank" rel="noopener noreferrer">Satellite</a>')
    if county: links.append(f'<a href="{E(county)}" target="_blank" rel="noopener noreferrer">County record (qPublic)</a>')
    watch = secs.get("Watch-outs", [])
    nexts = secs.get("Next steps", [])
    card = f'''
<article class="card {cls}" id="p{E(r["rank"])}">
 <header><span class="rank">#{E(r["rank"])}</span><h3>{E(r["site_address"])} <small>{E(r["jurisdiction"])} · parcel {E(pid)}</small></h3>
 <span class="grade g{E(r["lead_grade"])}">Grade {E(r["lead_grade"])}</span><span class="badge">{E(label)}</span></header>
 {f'<p class="mark"><b>Your mark ({E(d["decision"])}, {E(d["date"])}):</b> {E(d["note"])}</p>' if d else ''}
 <p class="openitem"><b>Open item from the screen:</b> {E(open_item) if open_item else "none (every check clear)"}</p>
 {land_line}
 <div class="body">
  <figure class="plan">{svg_for(brief)}<figcaption>North up, county parcel map. Blue: floodplain / wetlands / protected land. Green: largest open part. Orange: fitted building envelope. Teal: detention pond allowance. Not a survey; steep ground not shown.</figcaption></figure>
  <div>
   <dl>{"".join(f"<dt>{E(k)}</dt><dd>{E(v)}</dd>" for k, v in facts)}</dl>
   <p class="links">{" · ".join(links)}</p>
  </div>
 </div>
 {check_block(pid, check, headline, today)}
 <div class="cols">
  <div><h4>Watch-outs</h4><ul>{"".join(f"<li>{md_inline(w)}</li>" for w in watch) or "<li>none listed</li>"}</ul></div>
  <div><h4>Next steps</h4><ol>{"".join(f"<li>{md_inline(n)}</li>" for n in nexts)}</ol></div>
 </div>
</article>'''
    cards[pid] = (cls, int(r["rank"]), r["lead_grade"], card, today)
    g = geo.get(pid)
    if g:
        def rnd(c):
            if isinstance(c[0], (int, float)): return [round(c[0], 5), round(c[1], 5)]
            return [rnd(x) for x in c]
        map_feats.append({"type": "Feature", "geometry": {"type": g["geometry"]["type"], "coordinates": rnd(g["geometry"]["coordinates"])},
                          "properties": {"rank": int(r["rank"]), "addr": r["site_address"], "cls": cls, "grade": r["lead_grade"], "acres": r["acres"]}})

today_cards = sorted([c for c in cards.values() if c[4]], key=lambda c: ({"open": 0, "unchecked": 1, "weak": 2, "taken": 3, "killed": 4}[c[0]], c[1]))
a_cards = sorted([c for c in cards.values() if not c[4] and c[2] == "A"], key=lambda c: c[1])
rest = [c for c in cards.values() if not c[4] and c[2] != "A"]
rest_clean = sorted([c for c in rest if c[0] == "unchecked"], key=lambda c: c[1])
rest_marked = sorted([c for c in rest if c[0] != "unchecked"], key=lambda c: c[1])

def summary_rows(cs):
    out = []
    for cls, rank, grade, _, _ in cs:
        pid = next(p for p, r in rows.items() if int(r["rank"]) == rank)
        r = rows[pid]
        out.append(f'<tr class="{cls}"><td><a href="#p{rank}">#{rank}</a></td><td>{E(r["site_address"])}, {E(r["jurisdiction"])}</td><td>{E(r["acres"])}</td>'
                   f'<td>{E(r["zoning"])} ({E(r["entitlement"])})</td><td>{E(r["envelope_fit"])}</td><td>{E(grade)}</td><td>{E(status_of(pid, lead_check(pid)[0])[1])}</td></tr>')
    return "".join(out)

funnel = re.search(r"\| Stage \| Parcels \|.*?\n\n", (run_dir / "summary.md").read_text(encoding="utf-8"), re.S).group(0)
funnel_rows = [l.strip("|").split("|") for l in funnel.strip().splitlines()[2:]]
funnel_html = "".join(f"<tr><td>{E(a.strip())}</td><td>{E(b.strip())}</td></tr>" for a, b in funnel_rows)
meta = json.load(open(run_dir / "features.json"))["meta"]

intro_bottom = sys.stdin.read() if not sys.stdin.isatty() else ""

page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fulton lead pack</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
:root{{--bg:#f6f5f1;--card:#fff;--ink:#1d2327;--mute:#5b656c;--line:#dcd9d0;--killed:#8a2d22;--taken:#b3402f;--weak:#a86a00;--open:#2f6f4f;--unchecked:#3f5f86}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{--bg:#16191b;--card:#1f2326;--ink:#e6e3dc;--mute:#9aa3a9;--line:#353a3e}}}}
:root[data-theme="dark"]{{--bg:#16191b;--card:#1f2326;--ink:#e6e3dc;--mute:#9aa3a9;--line:#353a3e}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15.5px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:24px 16px 60px}}h1{{font-size:26px;margin:0 0 4px}}h2{{font-size:21px;margin:34px 0 8px;border-bottom:2px solid var(--line);padding-bottom:4px}}
.mute,.lede{{color:var(--mute)}}.lede{{max-width:78ch}}
.bottom{{background:var(--card);border:1px solid var(--line);border-left:6px solid var(--weak);border-radius:8px;padding:12px 16px;margin:14px 0}}
table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{border-bottom:1px solid var(--line);padding:5px 6px;text-align:left;vertical-align:top}}
.tablewrap{{overflow-x:auto}}#map{{height:440px;border:1px solid var(--line);border-radius:8px;margin:10px 0}}
.card{{background:var(--card);border:1px solid var(--line);border-left:6px solid var(--unchecked);border-radius:8px;padding:14px 16px;margin:0 0 20px}}
.card.killed{{border-left-color:var(--killed)}}.card.taken{{border-left-color:var(--taken)}}.card.weak{{border-left-color:var(--weak)}}.card.open{{border-left-color:var(--open)}}
.card header{{display:flex;flex-wrap:wrap;gap:8px;align-items:baseline}}.rank{{font-weight:700;font-size:20px;color:var(--mute)}}
h3{{font-size:18px;margin:0;flex:1 1 300px}}h3 small{{display:block;font-weight:400;color:var(--mute);font-size:13.5px}}
.badge,.grade{{font-size:12.5px;font-weight:600;padding:2px 10px;border-radius:999px;color:#fff;background:var(--unchecked)}}
.grade{{background:#555}}.grade.gA{{background:#2f6f4f}}.grade.gB{{background:#6b5b95}}
.killed .badge{{background:var(--killed)}}.taken .badge{{background:var(--taken)}}.weak .badge{{background:var(--weak)}}.open .badge{{background:var(--open)}}
.mark,.openitem{{margin:6px 0;font-size:14.5px}}
.body{{display:grid;grid-template-columns:minmax(220px,360px) 1fr;gap:18px;align-items:start;margin-top:8px}}
@media (max-width:720px){{.body,.cols{{grid-template-columns:1fr!important}}}}
.plan{{margin:0}}.plan svg{{width:100%;height:auto;border:1px solid var(--line);border-radius:4px;background:#fff}}figcaption{{font-size:12px;color:var(--mute)}}
dl{{display:grid;grid-template-columns:max-content 1fr;gap:2px 12px;margin:0;font-size:14px}}dt{{color:var(--mute)}}dd{{margin:0}}
.links{{font-size:14px}}a{{color:#2b6cb0}}
.check{{background:color-mix(in srgb,var(--card) 92%,var(--ink));border-radius:6px;padding:10px 12px;margin:12px 0;font-size:14px}}
.check.none{{color:var(--mute)}}.headline{{font-weight:600}}.lbl{{color:var(--mute);font-weight:400;font-size:13px}}
.v-killed,.v-against,.v-blocked,.v-rezoning_needed,.v-signs_under_contract,.v-signs_developer_pursuing{{color:var(--killed);font-weight:600}}
.v-weakened,.v-conditional,.v-neutral,.v-not_shown{{color:var(--weak);font-weight:600}}.v-survives,.v-by_right,.v-supports,.v-at_site,.v-nearby{{color:var(--open);font-weight:600}}
.sec{{table-layout:fixed}}.sec td,.sec th{{overflow-wrap:anywhere}}.sec td:first-child,.sec th:first-child{{width:6.5em}}.sec td:nth-child(2),.sec th:nth-child(2){{width:8em}}.check,.obj,.headline,.mark,dd{{overflow-wrap:anywhere}}#map{{max-width:100%}}.cols{{display:grid;grid-template-columns:1fr 1fr;gap:18px;font-size:14px}}h4{{margin:8px 0 2px}}
ul,ol{{margin:0;padding-left:20px}}tr.killed td,tr.taken td{{color:var(--mute)}}
.legend span{{display:inline-block;width:12px;height:12px;border-radius:2px;margin:0 4px 0 12px;vertical-align:middle}}
</style></head><body><main>
<h1>Fulton County lead pack</h1>
<p class="lede">Run <b>{E(run_dir.name)}</b> ({E(meta["run_time"])}, code {E(str(meta.get("code_commit"))[:7])}; the code on <code>main</code> differs only by a doc). {E(grade_counts)} Every lead <code>leads.md</code> lists is below, with its site plan, your marks, and any document check.</p>
<div class="bottom">{intro_bottom}</div>
<div id="map"></div>
<p class="legend mute">Lead status:<span style="background:var(--open)"></span>survived skeptic<span style="background:var(--unchecked)"></span>no document check yet<span style="background:var(--weak)"></span>open doubt<span style="background:var(--taken)"></span>probably taken<span style="background:var(--killed)"></span>ruled out</p>

<h2>1. Checked this session</h2>
<p class="mute">Sixteen grade B leads in three batches on 2026-11-13: the digest\'s "worth a document check next" list (#11, #16, #18, #21, #23, #25); then #12, #22, #51, #65 and #115; then #170 (part of the #115 assemblage, not on the lead list, so no brief or plan), #33, #38, #39 and #55, which need a rezoning or carry conditions. Each got an investigator and a skeptic under screener/INVESTIGATE.md. Quotes were re-fetched and verified by quotecheck; verdicts, objections and headlines are agents\' readings.</p>
<div class="tablewrap"><table><tr><th>#</th><th>Site</th><th>Acres</th><th>Zoning</th><th>Building fit</th><th>Grade</th><th>Status</th></tr>{summary_rows(today_cards)}</table></div>
{"".join(c[3] for c in today_cards)}

<h2>2. Grade A — each already carries a mark</h2>
<div class="tablewrap"><table><tr><th>#</th><th>Site</th><th>Acres</th><th>Zoning</th><th>Building fit</th><th>Grade</th><th>Status</th></tr>{summary_rows(a_cards)}</table></div>
{"".join(c[3] for c in a_cards)}

<h2>3. Other grade B leads, not yet checked</h2>
<div class="tablewrap"><table><tr><th>#</th><th>Site</th><th>Acres</th><th>Zoning</th><th>Building fit</th><th>Grade</th><th>Status</th></tr>{summary_rows(rest_clean)}</table></div>
{"".join(c[3] for c in rest_clean)}

<h2>4. Grade B leads you already marked</h2>
<div class="tablewrap"><table><tr><th>#</th><th>Site</th><th>Acres</th><th>Zoning</th><th>Building fit</th><th>Grade</th><th>Status</th></tr>{summary_rows(rest_marked)}</table></div>
{"".join(c[3] for c in rest_marked)}

<h2>The funnel</h2>
<div class="tablewrap"><table><tr><th>Stage</th><th>Parcels</th></tr>{funnel_html}</table></div>
<h2>Limits</h2>
<ul class="mute">
<li>The grade is a fixed rule over the screen's own checks, not a recommendation. "No envelope found" is a lower bound; frontage is to a road centreline; soils are map-unit level.</li>
<li>Not checked by anything here: utilities and capacity, zoning conditions, easements other than conservation easements, curb cuts, whether a parcel is for sale, and who is behind a company owner.</li>
<li>A kept quote proves only that the words were on that page when fetched, not that the page is current or that the agent read it right.</li>
<li>No owner has been contacted. No CoStar, LoopNet or Crexi data is used.</li>
</ul>
</main>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
(function(){{
 var feats={json.dumps({"type": "FeatureCollection", "features": map_feats})};
 if(!window.L){{document.getElementById('map').innerHTML='<p style="padding:12px">Map needs a network connection (Leaflet from unpkg).</p>';return;}}
 var css=getComputedStyle(document.documentElement);
 function col(c){{return css.getPropertyValue('--'+c).trim()||'#3f5f86';}}
 var map=L.map('map',{{scrollWheelZoom:false}});
 L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:19,attribution:'&copy; OpenStreetMap contributors'}}).addTo(map);
 var layer=L.geoJSON(feats,{{style:function(f){{return {{color:col(f.properties.cls),weight:2,fillOpacity:0.35}};}},
  onEachFeature:function(f,l){{var p=f.properties,d=document.createElement('div');var a=document.createElement('a');a.href='#p'+p.rank;a.textContent='#'+p.rank+' '+p.addr;d.appendChild(a);d.appendChild(document.createTextNode(' · '+p.acres+' ac · grade '+p.grade));l.bindPopup(d);
   l.bindTooltip(String(p.rank),{{permanent:true,direction:'center',className:'rk'}});}}}}).addTo(map);
 map.fitBounds(layer.getBounds(),{{padding:[20,20]}});
}})();
</script>
<style>.rk{{background:transparent;border:0;box-shadow:none;font-weight:700;color:#111;text-shadow:0 0 3px #fff}}</style>
</body></html>'''
out_path.write_text(page, encoding="utf-8")
print(out_path, len(page), "bytes;", len(cards), "leads;", len(map_feats), "on map")
