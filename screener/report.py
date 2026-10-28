"""Write the run's results: ranked CSV, rejects CSV, map, briefs, summary."""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from typing import Any

from .checks import geojson_geometry

RANKED_COLUMNS = [
    "rank", "score", "fixed_score", "learned_score", "motivation_score", "parcel_id", "jurisdiction",
    "site_address", "acres", "usable_after_slope", "entitlement", "zoning", "flu",
    "exit_label", "exit_miles", "rail_ft", "industrial_within_1mi", "flood_pct",
    "wetland_pct", "steep_pct", "relief_ft", "owner", "owner_type", "owner_mailing",
    "owner_state", "years_held", "last_sale_date", "last_sale_price", "land_value",
    "opportunity_zone", "flags", "why_site", "why_seller", "decision", "maps_link",
]


def maps_link(p: dict[str, Any]) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={p['lat']},{p['lon']}"


def _cell(v: Any) -> Any:
    if isinstance(v, list):
        return "; ".join(map(str, v))
    if isinstance(v, bool):
        return "yes" if v else ""
    return "" if v is None else v


def write_all(out_dir: Path, ranked: list[dict[str, Any]], rejected: list[dict[str, Any]],
              summary: dict[str, Any], top_n: int) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in ranked:
        p["maps_link"] = maps_link(p)

    with open(out_dir / "ranked.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(RANKED_COLUMNS)
        for p in ranked:
            w.writerow([_cell(p.get(c)) for c in RANKED_COLUMNS])

    with open(out_dir / "rejected.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["parcel_id", "jurisdiction", "site_address", "acres", "owner", "reasons"])
        for p in rejected:
            w.writerow([p["parcel_id"], p.get("jurisdiction"), p.get("site_address"),
                        p.get("acres"), p.get("owner"), "; ".join(p.get("reject_reasons", []))])

    features = [{
        "type": "Feature",
        "geometry": geojson_geometry(p["geometry"]),
        "properties": {c: _cell(p.get(c)) for c in RANKED_COLUMNS},
    } for p in ranked]
    (out_dir / "ranked.geojson").write_text(json.dumps(
        {"type": "FeatureCollection", "features": features}))

    (out_dir / "map.html").write_text(_map_html(features[:max(top_n, 300)], summary))

    briefs = out_dir / "briefs"
    briefs.mkdir(exist_ok=True)
    for p in ranked[:top_n]:
        name = f"{p['rank']:03d}_{p['parcel_id'].replace(' ', '_')}.md"
        (briefs / name).write_text(brief(p))

    (out_dir / "summary.md").write_text(_summary_md(summary, ranked[:10]))


def _money(v: Any) -> str:
    return f"${v:,.0f}" if isinstance(v, (int, float)) and v else "n/a"


def brief(p: dict[str, Any]) -> str:
    lines = [
        f"# #{p['rank']} — {p.get('site_address') or 'no site address'} ({p['jurisdiction']})",
        "",
        f"**Score {p['score']}** (zoning & ground {p['fixed_score']}, access/land/seller "
        f"{p['learned_score']}, seller motivation {p['motivation_score']}) · "
        f"parcel `{p['parcel_id']}` · "
        f"[map]({p['maps_link']}) · county record: {p.get('assessor_portal', '')}",
        "",
        "## The site",
        f"- {p['acres']:.1f} acres; about {p.get('usable_after_slope', p['usable_acres']):.1f} "
        "usable after floodplain, wetlands, easements and steep ground",
        f"- Zoning: {p.get('zoning') or 'unknown'}"
        + (f" ({p['zoning_desc']})" if p.get("zoning_desc") else "")
        + f"; future land use: {p.get('flu') or 'unknown'}",
        f"- Entitlement path: **{p.get('entitlement')}** — {p.get('entitlement_note')}",
        f"- Access: {p.get('exit_miles')} mi (straight line) to {p.get('exit_label')}; "
        f"road: {p.get('road') or 'unknown'}",
        f"- Rail: " + (f"{p['rail_ft']:,} ft to {p.get('rail_owner') or 'a rail line'}"
                       if p.get("rail_ft") is not None else "none within about a mile"),
        f"- Industrial parcels within 1 mile: {p.get('industrial_within_1mi', 0)}",
        f"- Floodplain {p.get('flood_pct', 0):.0f}% ({', '.join(p.get('flood_zones') or []) or 'none'}), "
        f"floodway {p.get('floodway_pct', 0):.0f}%, wetlands {p.get('wetland_pct', 0):.0f}%, "
        f"ponds/streams {p.get('water_pct', 0):.0f}%",
        f"- Slope: {_fmt(p.get('steep_pct'))}% of the site steeper than 15%; "
        f"relief about {_fmt(p.get('relief_ft'))} ft (USGS lidar 2018-19, 10 m grid)",
        f"- Water / sewer per assessor: {p.get('water') or 'unknown'} / {p.get('sewer') or 'unknown'}",
        f"- Opportunity Zone: {'yes' if p.get('opportunity_zone') else 'no'}",
        "",
        "## The owner",
        f"- {p.get('owner') or 'unknown'} ({p.get('owner_type')})",
        f"- Mailing address: {p.get('owner_mailing') or 'unknown'}",
        f"- Last recorded sale: {p.get('last_sale_date') or 'none on file'}"
        + (f" for {_money(p.get('last_sale_price'))}" if p.get("last_sale_price") else "")
        + (f" ({p['last_sale_instrument']})" if p.get("last_sale_instrument") else ""),
        f"- Assessor land value: {_money(p.get('land_value'))} "
        f"({_money(p['land_value'] / p['acres']) if p.get('land_value') and p.get('acres') else 'n/a'}/acre)",
        "",
        "## Why it ranks here",
        *[f"- {x}" for x in p.get("why_site", []) + p.get("why_seller", [])],
    ]
    if p.get("flags"):
        lines += ["", "## Watch-outs", *[f"- {x}" for x in p["flags"]]]
    lines += [
        "", "## Next steps",
        f"- Confirm zoning and any conditions with {_planning_office(p['jurisdiction'])}",
        "- Ask the water/sewer provider about capacity and the nearest main",
        "- Pull title and recent deeds before approaching the owner",
        "- Drive the site: access, neighbours, visible fill or dumping",
        "",
        "_Screening estimate from public data (county assessor, FEMA, USFWS, USGS, EPA, "
        "GA EPD, GDOT). Not a survey, title search or environmental assessment._",
    ]
    return "\n".join(lines) + "\n"


def _planning_office(jurisdiction: str) -> str:
    if jurisdiction == "South Fulton / unincorporated":
        return "City of South Fulton planning (Fulton County if unincorporated)"
    if jurisdiction == "Fulton Industrial District":
        return "Fulton County planning (Fulton Industrial District)"
    if jurisdiction.startswith("district"):
        return "the county or city planning office"
    return f"{jurisdiction} planning"


def _fmt(v: Any) -> str:
    return "n/a" if v is None else f"{v:.0f}"


def _summary_md(s: dict[str, Any], top: list[dict[str, Any]]) -> str:
    lines = [f"# {s['county']} County land screen — {s['run_date']}", "",
             "| Stage | Parcels |", "|---|---|"]
    lines += [f"| {k} | {v:,} |" for k, v in s["funnel"]]
    lines += ["", "## Top 10", "", "| # | Score | Acres | Where | Entitlement | Owner type |",
              "|---|---|---|---|---|---|"]
    for p in top:
        lines.append(f"| {p['rank']} | {p['score']} | {p['acres']:.1f} | "
                     f"{p.get('site_address') or p['parcel_id']} ({p['jurisdiction']}) | "
                     f"{p.get('entitlement')} | {p.get('owner_type')} |")
    lines += ["", "## Most common reject reasons", ""]
    lines += [f"- {n:,} — {reason}" for reason, n in s["reject_reasons"][:12]]
    if s.get("notes"):
        lines += ["", "## Notes", "", *[f"- {n}" for n in s["notes"]]]
    return "\n".join(lines) + "\n"


def _map_html(features: list[dict[str, Any]], s: dict[str, Any]) -> str:
    data = json.dumps({"type": "FeatureCollection", "features": features})
    title = html.escape(f"{s['county']} land screen {s['run_date']}")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>html,body,#map{{height:100%;margin:0;font:14px system-ui,sans-serif}}
.legend{{background:#fff;padding:6px 8px;border-radius:4px;line-height:1.5}}
.legend i{{display:inline-block;width:12px;height:12px;margin-right:6px;vertical-align:middle}}</style>
</head><body><div id="map"></div><script>
const data = {data};
const map = L.map('map');
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',
  {{maxZoom: 19, attribution: '&copy; OpenStreetMap'}}).addTo(map);
const color = s => s >= 70 ? '#0b6e4f' : s >= 55 ? '#4f9d69' : s >= 40 ? '#e0a526' : '#b5534a';
const esc = v => String(v ?? '').replace(/[&<>"]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]));
const layer = L.geoJSON(data, {{
  style: f => ({{color: color(f.properties.score), weight: 2, fillOpacity: 0.35}}),
  onEachFeature: (f, l) => {{
    const p = f.properties;
    l.bindPopup(`<b>#${{esc(p.rank)}} · score ${{esc(p.score)}}</b><br>
      ${{esc(p.site_address)}} (${{esc(p.jurisdiction)}})<br>
      ${{esc(p.acres)}} ac · ${{esc(p.entitlement)}} · zoning ${{esc(p.zoning || 'unknown')}}<br>
      ${{esc(p.exit_miles)}} mi to ${{esc(p.exit_label)}}<br>
      Owner: ${{esc(p.owner)}} (${{esc(p.owner_type)}})<br>
      <a href="${{esc(p.maps_link)}}" target="_blank" rel="noopener">Google Maps</a>`);
  }}
}}).addTo(map);
if (data.features.length) map.fitBounds(layer.getBounds()); else map.setView([33.7, -84.4], 10);
const legend = L.control({{position: 'bottomright'}});
legend.onAdd = () => {{ const d = L.DomUtil.create('div', 'legend');
  d.innerHTML = '<b>Score</b><br><i style="background:#0b6e4f"></i>70+<br><i style="background:#4f9d69"></i>55-69<br><i style="background:#e0a526"></i>40-54<br><i style="background:#b5534a"></i>&lt;40';
  return d; }};
legend.addTo(map);
</script></body></html>
"""
