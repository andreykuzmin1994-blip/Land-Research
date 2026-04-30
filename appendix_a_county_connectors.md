# Appendix A: County Data Connector Specifications — Atlanta Metro

> Scraper architecture and per-county connector specs for the Land Site Selection agent.
> Strategy: **API-first (Approach 3)** with **AI-assisted fallback (Approach 2)** on failure.
> Coverage: 8 counties — Fulton, DeKalb, Cobb, Gwinnett, Clayton, Henry, Spalding, Fayette.

---

## Coding Workflow: Three-Agent Code Team

All code referenced in this appendix — the connector harness, individual county connectors, the AI fallback layer, the scoring engine in `research.py`, and any future modifications — must be developed using a **three-agent code team** running Claude Opus 4.7. This is not optional. The complexity of this system, the number of integration points (8 counties, multiple data sources, autonomous loop, AI fallback), and the cost of silent failures (a connector returning bad data could populate the pipeline with garbage for weeks before detection) all justify the overhead of multi-agent review.

### The Three Roles

**Agent 1: Risk and Architecture Reviewer.**
This agent runs first. It reads the requirement, the relevant section of `program.md` or this appendix, and the existing codebase. Its job is to surface what could go wrong before any code is written. Specifically:
- What are the failure modes of this change? What happens if the API returns malformed JSON, if a field is missing, if the network times out, if the response is unexpectedly large?
- What downstream systems does this change affect? Does modifying the Fulton connector break the harness? Does changing the field mapping invalidate cached data?
- What are the security implications? Is the change introducing any path traversal, injection, or credential exposure risks?
- What are the architectural impacts? Does this change couple the harness to a specific county's behavior in a way that makes adding the next county harder?
- What testing is required to validate the change beyond the harness's standard checks?
- What are the rate limiting and politeness considerations for the county servers being queried?

Output: a structured risk and architecture review document with concrete concerns ranked by severity, recommended mitigations, and explicit go/no-go gates that must pass before implementation.

**Agent 2: Code Writer.**
This agent runs second, after reading both the requirement AND Agent 1's risk review. Its job is to write the code that satisfies the requirement while addressing every risk Agent 1 identified. Specifically:
- Implement the requested functionality
- Address each risk Agent 1 raised, either by mitigating it in code or by explicitly documenting why the risk is accepted
- Write the code with clear comments explaining non-obvious decisions
- Include tests that exercise the failure modes Agent 1 surfaced (not just the happy path)
- Follow the existing patterns in the codebase — if the harness uses dataclasses, this code uses dataclasses
- Keep the change minimal and focused — no opportunistic refactoring of unrelated code

Output: working code with tests, plus a written explanation of how each of Agent 1's concerns was addressed.

**Agent 3: Reviewer and Implementer.**
This agent runs last. Its job is to critically analyze both Agent 1's review and Agent 2's code, then make the final implementation decision. Specifically:
- Did Agent 1 miss any risks? If so, surface them now.
- Did Agent 2 actually address each risk Agent 1 raised, or did it pay lip service while leaving the core issue unmitigated?
- Is Agent 2's code consistent with the rest of the codebase? Does it introduce inconsistencies in style, error handling, or naming conventions?
- Does Agent 2's code over-engineer the solution? Could it be simpler without losing safety?
- Does Agent 2's code under-engineer the solution? Are there edge cases not handled?
- Are the tests Agent 2 wrote actually testing the right things, or are they testing the implementation rather than the behavior?
- Does the change require updates to documentation (this appendix, `program.md`, README) that Agent 2 didn't make?

Agent 3 has authority to: approve and commit Agent 2's code as-is, request specific revisions from Agent 2 with clear acceptance criteria, request that Agent 1 reconsider its risk assessment, or escalate to the human if any of the three agents disagree on a fundamental architectural question.

Output: either a committed change with a summary of what was implemented and why, or a structured request for revision with specific acceptance criteria.

### Why Three Agents and Not One

A single agent writing code tends to produce code that looks correct but has unexamined failure modes. The agent will write the happy path well, will write some defensive code for the obvious edge cases, but will systematically under-think the failure modes that require pessimistic imagination — what if the API silently changes its schema, what if owner names start coming back redacted, what if the parcel polygon crosses a county boundary, what if the same parcel ID appears in two counties.

Agent 1 exists to be pessimistic on purpose. Its job is not to write code; its job is to find what's wrong with the plan before the plan becomes code. The risk review document Agent 1 produces is itself a deliverable that has value independent of the code Agent 2 writes — it captures the team's collective understanding of the system's failure modes, and it accumulates over time into a body of institutional knowledge.

Agent 2 exists to write code under constraint. Knowing that Agent 1 has already enumerated the risks and Agent 3 will critique the implementation produces measurably better code than an unconstrained agent. Agent 2 is incentivized to address risks in code rather than ignore them, because Agent 3 will catch the omissions.

Agent 3 exists because two agents can collude on a flawed solution if neither has the authority or independence to reject it. The reviewer-implementer role provides that independence and forces a final adversarial check. Agent 3 also catches a failure mode that Agents 1 and 2 cannot catch on their own: cases where Agent 1's risk review missed something important and Agent 2 dutifully implemented something that addresses the wrong concern.

### Operational Notes

- All three agents must run Claude Opus 4.7. Do not substitute a smaller model for any of the three roles. The risk review and code review tasks require the same level of reasoning capability as the code-writing task.
- Each agent runs in a fresh context with only the relevant artifacts available. Agent 2 sees the requirement and Agent 1's review. Agent 3 sees the requirement, Agent 1's review, and Agent 2's code. None of the agents see the others' internal reasoning — only their outputs.
- The three-agent workflow is required for production code. For exploratory or throwaway scripts (e.g., a one-time data analysis to check a hypothesis), a single agent is acceptable, but the output of such scripts must not be committed to the production codebase without going through the three-agent workflow.
- When the human disagrees with Agent 3's decision, the human's decision wins. The three-agent workflow is a quality assurance system, not a governance system — final authority remains with the human operator.
- The Reviewer-Implementer (Agent 3) is the only agent that commits code to the repo. Neither Agent 1 nor Agent 2 has commit access. This enforces the review gate.

### When to Skip This Workflow

The three-agent workflow is overkill for:
- Documentation-only changes (updating this appendix, fixing typos, restructuring sections)
- Configuration changes that don't involve logic (adding a new county to the harness registry, adjusting a scoring weight in `parameters.json`)
- Trivially safe one-line fixes (renaming a variable for clarity, adding a comment)

The three-agent workflow is required for:
- New connectors (county or otherwise)
- Changes to the harness logic
- Changes to the scoring engine
- Changes to the AI fallback layer
- Changes to the autonomous loop in `research.py`
- Any change that affects how the agent commits results to git
- Any change to authentication, credentials, or external service integration

When in doubt, run the workflow. The cost of three agents reviewing a small change is low. The cost of a single agent shipping a subtle bug into autonomous overnight runs is high.

---

## Architecture Overview

### The Two-Layer Strategy

Every county connector follows the same pattern: try the structured API first, fall back to AI-assisted browser scraping if the API fails or doesn't exist.

```
┌─────────────────────────────────────────────────────────┐
│                  COUNTY CONNECTOR                        │
│                                                          │
│  ┌──────────────────────┐    ┌────────────────────────┐ │
│  │  LAYER 1: API-First  │───►│  LAYER 2: AI Fallback  │ │
│  │                      │fail│                         │ │
│  │  ArcGIS REST query   │───►│  Playwright + Claude    │ │
│  │  → structured JSON   │    │  → page screenshot      │ │
│  │  → parse fields      │    │  → Claude extracts data │ │
│  │  → return parcel obj │    │  → return parcel obj    │ │
│  └──────────────────────┘    └────────────────────────┘ │
│                                                          │
│  Output: standardized ParcelRecord object (same schema   │
│  regardless of which layer produced it)                  │
└─────────────────────────────────────────────────────────┘
```

### Why This Works

Most Georgia county GIS portals are built on Esri ArcGIS. The public-facing web app is a JavaScript viewer that makes REST API calls to a MapServer or FeatureServer backend. These backend endpoints are almost always publicly accessible — the county just doesn't advertise them. By hitting the REST endpoint directly, you get clean JSON with parcel geometry, owner info, acreage, zoning, and assessed values already structured. No HTML parsing, no brittle CSS selectors, no dealing with JavaScript-rendered pages.

When the API doesn't exist (smaller counties), is locked down, or changes without notice — the AI fallback kicks in. Playwright navigates to the county's web portal, takes a screenshot or extracts the DOM, and Claude reads the page and extracts the data like a human would. This is slower and costs API tokens, but it adapts to portal redesigns without code changes.

### Failure Modes and Recovery

| Failure | Detection | Recovery |
|---------|-----------|----------|
| ArcGIS endpoint returns 404/500 | HTTP status code | Switch to AI fallback |
| ArcGIS endpoint returns empty results | `features` array is empty | Verify query params, retry with broader bbox. If still empty, AI fallback. |
| ArcGIS endpoint schema changed | Expected fields missing from response | Log field diff, attempt field name fuzzy matching, flag for human review |
| County portal redesigned | AI fallback screenshot shows unexpected layout | Claude adapts (no code change needed). If extraction confidence < 0.7, flag. |
| County portal down entirely | Connection timeout or error page | Retry after 30 minutes. After 3 retries, skip county for this cycle. |
| Rate limited | 429 status or throttle response | Exponential backoff: 30s → 60s → 120s → skip |
| CAPTCHA or bot detection | Challenge page detected in response | Flag for human intervention. AI fallback cannot solve CAPTCHAs. |

---

## Standardized Output Schema

Every connector — regardless of county or layer — outputs this same `ParcelRecord` object:

```json
{
  "parcel_id": "string — county-assigned parcel ID / tax map number",
  "county": "string — county name (lowercase)",
  "state": "GA",
  "address": "string — site address (may be '0' or null for vacant land)",
  "city": "string — city or unincorporated",
  "zip": "string",
  "owner_name": "string",
  "owner_mailing_address": "string — full mailing address",
  "owner_type_inferred": "string — individual | trust | estate | llc | corporate | government | unknown",
  "acreage": "float",
  "land_sf": "float — land area in square feet",
  "zoning": "string — raw zoning code from county",
  "zoning_description": "string — human-readable zoning description",
  "land_use_code": "string — county land use classification code",
  "land_use_description": "string",
  "assessed_value_land": "float — assessed value of land only",
  "assessed_value_improvement": "float — assessed value of improvements",
  "assessed_value_total": "float",
  "fair_market_value": "float — FMV if available (GA: assessed = 40% of FMV)",
  "tax_year": "int",
  "tax_amount": "float — annual property tax bill",
  "tax_status": "string — current | delinquent | lien | exempt",
  "last_sale_date": "string — YYYY-MM-DD or null",
  "last_sale_price": "float or null",
  "deed_book_page": "string or null",
  "year_built": "int or null — 0 or null for vacant land",
  "improvement_sf": "float or null — 0 for vacant land",
  "geometry": {
    "type": "Polygon",
    "coordinates": "GeoJSON coordinates array"
  },
  "centroid_lat": "float",
  "centroid_lng": "float",
  "data_source": "string — arcgis_api | ai_fallback | qpublic | beacon",
  "data_source_url": "string — the URL queried",
  "extraction_confidence": "float 0.0–1.0 — 1.0 for API, variable for AI fallback",
  "extracted_at": "string — ISO 8601 timestamp",
  "raw_response_path": "string — path to cached raw response for audit"
}
```

### Owner Type Inference Rules

The agent infers `owner_type_inferred` from the `owner_name` string:

```
Contains "TRUST" or "TRUSTEE" or "TR "         → trust
Contains "ESTATE" or "ESTATE OF" or "DECD"     → estate
Contains "LLC" or "L L C" or "LP" or "LTD"     → llc
Contains "INC" or "CORP" or "CO " or "GROUP"   → corporate
Contains "COUNTY" or "CITY OF" or "STATE OF"
  or "UNITED STATES" or "BOARD OF"              → government
Else                                            → individual (default)
```

### Absentee Owner Detection

Compare `owner_mailing_address` against the `address` (site address) and county:
- If mailing ZIP differs from site ZIP by >50 miles → `absentee_distant`
- If mailing state ≠ GA → `absentee_out_of_state`
- If mailing address = site address → `owner_occupied_or_local`
- If site address is null/vacant → compare mailing ZIP to county centroid

---

## Layer 1: ArcGIS REST API Connectors

### How to Find Hidden ArcGIS Endpoints

Most counties don't list their REST endpoints publicly, but they're discoverable:

1. **Open the county's GIS portal** in Chrome
2. **Open DevTools → Network tab** → filter by "MapServer" or "FeatureServer" or "arcgis"
3. **Interact with the map** — pan, zoom, click a parcel
4. **Look for XHR/Fetch requests** to URLs like:
   - `https://{county-domain}/arcgis/rest/services/{ServiceName}/MapServer/{LayerID}/query`
   - `https://{county-domain}/arcgis/rest/services/{ServiceName}/FeatureServer/{LayerID}/query`
5. **Navigate to the service root** (strip `/query` and params) to see the layer schema, field names, and supported query formats

### Standard ArcGIS Query Pattern

All county ArcGIS connectors use this same query template:

```
GET {base_url}/{layer_id}/query
  ?where={filter_expression}
  &outFields={comma_separated_fields}
  &returnGeometry=true
  &outSR=4326
  &f=json
```

**Key parameters:**

| Parameter | Usage |
|-----------|-------|
| `where` | SQL-like filter. Examples: `ACRES >= 5 AND ACRES <= 50`, `ZONING LIKE 'AG%'`, `OWNER_NAME LIKE '%TRUST%'` |
| `outFields` | Comma-separated field names. Use `*` for all fields (heavier response). |
| `geometry` | Spatial filter as envelope: `{xmin},{ymin},{xmax},{ymax}` |
| `geometryType` | `esriGeometryEnvelope` for bounding box queries |
| `spatialRel` | `esriSpatialRelIntersects` for parcels touching the bbox |
| `returnGeometry` | `true` to get parcel polygon shapes |
| `outSR` | `4326` for WGS84 lat/lng (standard). Counties often store in State Plane (EPSG:2240 for GA West, 2239 for GA East). |
| `resultRecordCount` | Max records per request (usually capped at 1000–2000). |
| `resultOffset` | For pagination when results exceed max. |
| `f` | `json` or `geojson` |

**Pagination:** ArcGIS services cap results (typically 1000 or 2000 per request). To get all matching parcels:

```python
offset = 0
all_features = []
while True:
    response = query(where=filter, resultOffset=offset, resultRecordCount=1000)
    features = response["features"]
    all_features.extend(features)
    if len(features) < 1000:
        break
    offset += 1000
```

**Spatial queries for corridor-based discovery:**

Define industrial corridors as bounding box envelopes (xmin, ymin, xmax, ymax in EPSG:4326):

```json
{
  "south_fulton_campbellton": {
    "xmin": -84.62, "ymin": 33.52, "xmax": -84.50, "ymax": 33.58,
    "notes": "Campbellton Fairburn Rd corridor, South Fulton"
  },
  "west_atlanta_i20": {
    "xmin": -84.58, "ymin": 33.72, "xmax": -84.42, "ymax": 33.79,
    "notes": "Fulton Industrial Blvd / I-20 West corridor"
  },
  "i85_south_airport": {
    "xmin": -84.42, "ymin": 33.55, "xmax": -84.32, "ymax": 33.63,
    "notes": "I-85 South / Airport / Clayton County corridor"
  },
  "i75_south_henry": {
    "xmin": -84.32, "ymin": 33.32, "xmax": -84.18, "ymax": 33.48,
    "notes": "I-75 South / Henry County / Stockbridge-McDonough corridor"
  }
}
```

---

## Per-County Connector Specs

### 1. Fulton County

**Status:** ✅ ArcGIS REST API confirmed and publicly accessible.

**ArcGIS Service URL:**
```
https://gismaps.fultoncountyga.gov/arcgispub2/rest/services/PropertyMapViewer/PropertyMapViewer/MapServer
```

**Key Layers:**

| Layer | ID | Use |
|-------|------|-----|
| Tax Parcel | 11 | Parcel boundaries, parcel ID, owner, address, acreage |
| Zoning | 34 | Zoning designation for Fulton Industrial District and unincorporated areas |
| Elevation Contours | 25 | Topographic data for grading assessment |
| Tax Allocation Districts | 37 | TAD incentive overlay |
| Census Tracts | 29 | For OZ cross-reference |
| 2035 Future Land Use | 33 | Planned land use — signals rezoning direction |

**Parcel Query Example:**
```
https://gismaps.fultoncountyga.gov/arcgispub2/rest/services/PropertyMapViewer/PropertyMapViewer/MapServer/11/query
  ?where=1=1
  &geometry=-84.62,33.52,-84.50,33.58
  &geometryType=esriGeometryEnvelope
  &spatialRel=esriSpatialRelIntersects
  &outFields=*
  &returnGeometry=true
  &outSR=4326
  &f=json
  &resultRecordCount=1000
```

**MaxRecordCount:** 2000

**Spatial Reference:** 102667 (State Plane Georgia West, NAD83, feet). Set `outSR=4326` in queries to get WGS84.

**Field Mapping** (verify field names by hitting `{service_url}/11?f=pjson` — names may vary):

| ParcelRecord Field | Expected ArcGIS Field Name(s) |
|-------------------|-------------------------------|
| parcel_id | `PARCEL_ID` or `TAX_ID` or `PIN` |
| address | `SITE_ADDR` or `LOCATION` |
| owner_name | `OWNER` or `OWNER_NAME` |
| owner_mailing_address | `MAIL_ADDR` + `MAIL_CITY` + `MAIL_STATE` + `MAIL_ZIP` |
| acreage | `ACRES` or `ACREAGE` or `SHAPE.STArea()` / 43560 |
| zoning | Cross-query against Layer 34 using parcel centroid |
| assessed_value_land | `LAND_VAL` or `LAND_ASSESSED` |
| assessed_value_total | `TOTAL_VAL` or `TOTAL_ASSESSED` |
| last_sale_date | `SALE_DATE` or `TRANSFER_DATE` |
| last_sale_price | `SALE_PRICE` or `TRANSFER_PRICE` |
| year_built | `YEAR_BUILT` or `YR_BLT` |

**Tax / Assessment Data (supplemental):**
Fulton County uses qPublic (Schneider Corp) for detailed tax records:
```
https://qpublic.schneidercorp.com/Application.aspx?AppID=1010&LayerID=23170&PageTypeID=2&PageID=9753&Q=...
```
Use this for tax bill amounts, exemption status, and delinquency data that may not be in the ArcGIS service. This requires HTML scraping (AI fallback).

**AI Fallback Portal:**
```
https://gis.fultoncountyga.gov/Apps/PropertyMapViewer/
```

**Notes:**
- Fulton is split between multiple municipalities (Atlanta, South Fulton, Alpharetta, etc.). Zoning is administered by each municipality, not the county. The Zoning layer (34) covers only unincorporated Fulton and the Fulton Industrial District. For City of South Fulton zoning, query their separate portal.
- South Fulton has its own GIS: `https://www.cityofsouthfultonga.gov/2159/GIS` — verify if they expose a separate ArcGIS service.
- The Future Land Use layer (33) is valuable for identifying parcels planned for industrial use but not yet rezoned.

---

### 2. DeKalb County

**Status:** ✅ ArcGIS REST API confirmed. Multiple endpoints available.

**ArcGIS Service URLs:**
```
Primary:   https://dcgis.dekalbcountyga.gov/hosted/rest/services/Parcels/MapServer
Secondary: https://gis.dekalbcountyga.gov/arcgis/rest/services/Parcels/MapServer
Basemap:   https://gis.dekalbcountyga.gov/arcgis/rest/services/Basemap/MapServer
```

**Key Layers (verify via `/layers?f=pjson`):**

| Layer | Expected ID | Use |
|-------|-------------|-----|
| Parcels | 0 | Parcel boundaries, owner, assessed value |
| Zoning | (check Basemap service) | Zoning overlay |

**MaxRecordCount:** 1000 (primary), varies by service.

**Spatial Reference:** 102100 / 3857 (Web Mercator). Set `outSR=4326` for WGS84.

**Open Data Portal:**
```
https://dcgis-dekalbgis.hub.arcgis.com/
```
This ArcGIS Hub portal may offer direct shapefile/GeoJSON downloads of parcel and zoning layers — check for bulk download links. Bulk download is preferred over pagination when available.

**Tax / Assessment Data:**
DeKalb uses a custom public access portal:
```
https://publicaccess.claytoncountyga.gov/ — WRONG, that's Clayton
```
DeKalb Tax Assessor: search via `https://www.qpublic.net/ga/dekalb/` or the county's property search.

**AI Fallback Portal:**
```
https://dekalbgis.maps.arcgis.com/apps/webappviewer/index.html?id=f241af753f414cdfa31c1fdef0924584
```

**Notes:**
- DeKalb has limited industrial corridors (primarily along I-285 east, I-20 east, and pockets along Memorial Drive). Most land parcels will be smaller.
- The county's Open Data Hub is the cleanest entry point — check for downloadable parcel datasets before running per-parcel API queries.
- FMV in Georgia = Assessed Value / 0.40 (40% assessment ratio).

---

### 3. Cobb County

**Status:** ✅ ArcGIS infrastructure confirmed. Hub portal and parcel viewer available.

**ArcGIS Hub:**
```
https://geo-cobbcountyga.hub.arcgis.com/
https://geo-cobbcountyga.opendata.arcgis.com/
```

**Parcel Viewer (ArcGIS Web App):**
```
https://www.arcgis.com/apps/webappviewer/index.html?id=e22d8c597b4e4762bcd2caa6127696e4
```

**To discover REST endpoint:** Open the Parcel Viewer in Chrome DevTools → Network tab → look for MapServer/FeatureServer requests when clicking parcels. Cobb uses an ArcGIS Enterprise portal at `gis.cobbcounty.org/portal/` which may have login-protected services. The public Hub portal is the safer starting point.

**Open Data Downloads:**
Check `https://geo-cobbcountyga.opendata.arcgis.com/` for downloadable datasets (parcels, zoning, land use). If bulk GeoJSON or shapefile downloads are available, use those instead of API pagination.

**Tax / Assessment Data:**
```
https://cobbassessor.org/
https://qpublic.schneidercorp.com/Application.aspx?AppID=1051&LayerID=23951&PageTypeID=1&PageID=9966
```
qPublic (Schneider Corp) handles Cobb's property search. This is the definitive source for owner, tax bill, assessed value, and sale history.

**AI Fallback Portal:**
```
https://cobbassessor.org/ (qPublic interface)
```

**Notes:**
- Cobb is critical for your BTS pipeline — Acworth, Kennesaw, and the I-75 North corridor have active industrial development.
- The county provides 2-foot contour data from 2015 LiDAR, available as a purchasable dataset from the GIS office. For topography scoring, check if contour layers are in the map service.
- Cobb's GIS data is delivered in Georgia State Plane (NAD 83, feet). Ensure coordinate transformation to WGS84.

---

### 4. Gwinnett County

**Status:** ✅ ArcGIS confirmed. Open Data Portal available with downloadable datasets.

**Open Data Portal:**
```
https://gcgis-gwinnettcountyga.hub.arcgis.com/
```

**Zoning Data (confirmed on Hub):**
```
https://gcgis-gwinnettcountyga.hub.arcgis.com/items/aca675dc82a248a0adde4b70eaad0d8d
```
This is a direct download/API link for Gwinnett zoning layer.

**GIS Browser:**
```
https://www.gwinnettcounty.com/departments/informationtechnologyservices/geographicinformationsystems/gisbrowser
```

**To discover REST endpoint:** Gwinnett's GIS Browser is a custom app. Open in DevTools → Network → look for ArcGIS REST calls. The data is ArcGIS-backed (confirmed: county uses ESRI ArcGIS with ArcSDE + Oracle backend, 245,000+ parcels).

**Tax / Assessment Data:**
Gwinnett Tax Assessor property search — check for qPublic or custom portal.

**AI Fallback Portal:**
Use the GIS Browser app or the Hub portal's built-in viewer.

**Notes:**
- Gwinnett has industrial pockets along I-85 Northeast (Buford, Suwanee, Lawrenceville) and along GA-316. Less traditional industrial than South Fulton / Clayton but growing logistics demand.
- The Open Data Portal is the best starting point — look for parcels, zoning, and land use as downloadable feature layers with REST endpoints.
- 245,000+ parcels means pagination will be significant. Use spatial filters (bounding boxes around industrial corridors) to reduce result sets.

---

### 5. Clayton County

**Status:** ⚠️ ArcGIS Hub confirmed, but REST endpoint quality needs verification.

**ArcGIS Hub / Open Data:**
```
https://clayton-county-gis-data-portal-cccd-gis.hub.arcgis.com/
```

**Parcel Viewer:**
```
https://experience.arcgis.com/experience/daff6d6be7a14000a1595a99bb67b1f8
```
This is an ArcGIS Experience Builder app (newer framework). The underlying data services should be discoverable via DevTools.

**Tax / Assessment (Public Access):**
```
https://publicaccess.claytoncountyga.gov/
```
Older ASP.NET-based system with map integration. Contains parcel ID, owner, assessed value, tax history. Will require AI fallback for scraping.

**AI Fallback Portal:**
```
https://publicaccess.claytoncountyga.gov/maps/mapadv.aspx
```

**Notes:**
- Clayton is a priority county — I-85 South / Airport corridor is one of Atlanta's most active industrial submarkets.
- The county's tech stack appears older than Fulton/DeKalb. The public access portal is ASP.NET with server-rendered pages — no modern SPA, which means AI fallback is more likely needed here.
- Cross-reference with Regrid (`https://app.regrid.com/us/ga/clayton`) for standardized parcel data if county API is unreliable. Regrid has 91,049 Clayton County parcels.

---

### 6. Henry County

**Status:** ⚠️ ArcGIS web viewer confirmed, REST endpoint needs discovery.

**ArcGIS Web Viewer:**
```
https://www.arcgis.com/apps/webappviewer/index.html?id=8e3356a4e1954356a80ef35dbd1c0358
```
This is a standard ArcGIS Web AppBuilder app. REST endpoints are discoverable via DevTools.

**GIS Department:**
```
https://www.co.henry.ga.us/Departments/D-L/GIS
```
County GIS page lists available datasets and ESRI App viewer.

**Tax / Assessment:**
```
https://qpublic.schneidercorp.com/Application.aspx?App=HenryCountyGA&PageType=Search
```
qPublic (Schneider Corp) — same platform as Fulton and Cobb. Parcel search by owner, address, or parcel number. Contains assessed values, tax amounts, sale history, and zoning. Map integration available.

**AI Fallback Portal:**
qPublic portal is the most data-rich entry point for Henry County.

**Notes:**
- Henry County (Stockbridge, McDonough, Hampton) is emerging as a major industrial corridor along I-75 South. Significant new distribution development.
- The county has 93,599 parcels (Acres.com data). Median land price per acre is $13,232 — significantly below Fulton, making it attractive for BTS.
- qPublic will be the primary data source here. The ArcGIS viewer likely serves geometry/boundaries, while qPublic has the assessor/tax data.
- Verify zoning contact: 770-288-7526 (noted on qPublic portal) — this suggests zoning data may not be fully digitized in GIS.

---

### 7. Spalding County

**Status:** ⚠️ Limited GIS infrastructure. qPublic is primary source.

**Tax / Assessment / Property Search:**
```
https://qpublic.schneidercorp.com/Application.aspx?AppID=...
```
Spalding County uses qPublic (Schneider Corp). This will be the primary data source for parcel info, owner, assessed value, and potentially zoning.

**ArcGIS:** No confirmed public ArcGIS REST service found. Spalding is a smaller county (~67,000 population) and likely does not maintain a public-facing ArcGIS deployment.

**AI Fallback Portal:**
qPublic portal only. Expect to rely heavily on AI fallback (Playwright + Claude) for Spalding County.

**Alternative Data Sources:**
- Regrid: `https://app.regrid.com/store/us/ga/spalding` — standardized parcel data available for purchase.
- Acres.com: `https://www.acres.com/plat-map/map/ga/spalding-county-ga` — plat maps and basic parcel data.

**Notes:**
- Spalding (Griffin, GA) is at the southern edge of your Atlanta south corridor. I-75 access via Exit 205/212. Less developed than Henry/Clayton but lower land basis.
- Given limited GIS infrastructure, prioritize qPublic scraping and consider Regrid as a bulk data source for this county.
- Zoning information may require a phone call to the county planning department or a manual lookup in their zoning ordinance.

---

### 8. Fayette County

**Status:** ⚠️ Beacon (Schneider Corp) is primary source. Limited ArcGIS presence.

**Tax / Assessment / Property Search:**
```
https://beacon.schneidercorp.com/Application.aspx?AppID=...&LayerID=...
```
Fayette County uses Beacon (a Schneider Corp product, similar to qPublic). Property search with map integration, owner data, assessed values, tax history.

**County Maps:**
```
https://fayettecountyga.gov/information/county-maps
```
Check for downloadable GIS data or links to ArcGIS services.

**AI Fallback Portal:**
Beacon portal — requires AI fallback for data extraction.

**Notes:**
- Fayette County (Peachtree City, Fayetteville) has some industrial along GA-85 and the southern I-85 corridor, but it's primarily residential/commercial. Smaller opportunity set for BTS.
- Beacon is a slightly different platform from qPublic — the AI fallback agent may need different navigation patterns. Field names and page structure differ.
- Environmental constraint: Fayette has significant stream buffer and tree canopy ordinances that affect buildable area. Factor this into site scoring.

---

## Layer 2: AI-Assisted Fallback Architecture

### When Layer 2 Activates

The AI fallback activates automatically when:
1. ArcGIS REST query returns HTTP error (4xx, 5xx)
2. ArcGIS REST query returns empty features for a query that should have results
3. Required fields are missing from the ArcGIS response
4. No ArcGIS endpoint exists for the county (Spalding, Fayette)
5. CAPTCHA or authentication wall detected (flag for human, don't attempt to solve)

### Technology Stack

```
Playwright (browser automation)
    ↓
Navigate to county portal → interact with map/search
    ↓
Extract page content (screenshot + DOM text)
    ↓
Claude API (vision + text)
    ↓
Structured JSON extraction → ParcelRecord
```

### Implementation Pattern

```python
# Pseudocode — the agent implements this in research.py

import asyncio
from playwright.async_api import async_playwright
from anthropic import Anthropic

client = Anthropic()

async def ai_fallback_extract(county: str, portal_url: str, search_params: dict) -> dict:
    """
    Use Playwright + Claude to extract parcel data from a county portal.
    Returns a ParcelRecord dict or raises ExtractionFailed.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1920, "height": 1080})

        # Navigate to portal
        await page.goto(portal_url, wait_until="networkidle")

        # Strategy depends on portal type
        if county in QPUBLIC_COUNTIES:
            await navigate_qpublic(page, search_params)
        elif county in BEACON_COUNTIES:
            await navigate_beacon(page, search_params)
        elif county in ARCGIS_WEBAPP_COUNTIES:
            await navigate_arcgis_webapp(page, search_params)
        else:
            await navigate_generic(page, search_params)

        # Capture the result page
        screenshot = await page.screenshot(type="png", full_page=False)
        page_text = await page.inner_text("body")

        # Send to Claude for structured extraction
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64.b64encode(screenshot).decode()
                        }
                    },
                    {
                        "type": "text",
                        "text": f"""Extract parcel data from this county property portal screenshot.
                        
County: {county}
Search was for: {json.dumps(search_params)}

Extract and return ONLY a JSON object with these fields:
- parcel_id (tax ID / parcel number)
- address (site address)
- owner_name
- owner_mailing_address (full mailing address)
- acreage (numeric, in acres)
- zoning (zoning code)
- land_use (land use description)
- assessed_value_land (numeric, dollars)
- assessed_value_total (numeric, dollars)
- fair_market_value (numeric, dollars)
- tax_amount (annual tax, numeric)
- last_sale_date (YYYY-MM-DD)
- last_sale_price (numeric)
- year_built (numeric, 0 if vacant)

Also extract from the page text:
{page_text[:3000]}

Return ONLY valid JSON. No explanation. If a field is not visible, use null."""
                    }
                ]
            }]
        )

        # Parse Claude's response
        result = json.loads(response.content[0].text)
        result["data_source"] = "ai_fallback"
        result["data_source_url"] = portal_url
        result["extraction_confidence"] = estimate_confidence(result)
        result["county"] = county

        await browser.close()
        return result


def estimate_confidence(record: dict) -> float:
    """
    Estimate extraction confidence based on field completeness and consistency.
    """
    fields = ["parcel_id", "owner_name", "acreage", "assessed_value_total"]
    populated = sum(1 for f in fields if record.get(f) is not None)
    base_confidence = populated / len(fields)

    # Sanity checks
    if record.get("acreage") and (record["acreage"] < 0.01 or record["acreage"] > 10000):
        base_confidence -= 0.2  # Suspicious acreage
    if record.get("assessed_value_total") and record["assessed_value_total"] < 0:
        base_confidence -= 0.2  # Negative value

    return max(0.0, min(1.0, base_confidence * 0.85))  # Cap at 0.85 for AI extraction
```

### Portal-Specific Navigation Functions

Each portal type requires a different navigation pattern:

**qPublic (Fulton, Cobb, Henry):**
```python
async def navigate_qpublic(page, search_params):
    # qPublic has a search bar with owner/address/parcel ID tabs
    if "parcel_id" in search_params:
        await page.click("text=Parcel ID")
        await page.fill("#ctlBodyPane_ctl00_txtParcelID", search_params["parcel_id"])
    elif "address" in search_params:
        await page.click("text=Location Address")
        await page.fill("#ctlBodyPane_ctl01_txtAddress", search_params["address"])
    await page.click("button:has-text('Search')")
    await page.wait_for_load_state("networkidle")
    # Click first result if search returns a list
    results = await page.query_selector_all(".SearchResults a")
    if results:
        await results[0].click()
        await page.wait_for_load_state("networkidle")
```

**Beacon (Fayette):**
```python
async def navigate_beacon(page, search_params):
    # Beacon has a different layout but similar concept
    if "address" in search_params:
        await page.fill("#txtSearchString", search_params["address"])
        await page.click("#btnSearch")
        await page.wait_for_load_state("networkidle")
```

**ArcGIS Web AppBuilder (Henry, Clayton):**
```python
async def navigate_arcgis_webapp(page, search_params):
    # ArcGIS web apps usually have a search widget in the top bar
    search_widget = await page.query_selector(".esri-search__input")
    if search_widget:
        await search_widget.fill(search_params.get("address", ""))
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(3000)  # Wait for map to pan and popup
    # Click on the popup to expand parcel details
    popup = await page.query_selector(".esri-popup__content")
    if popup:
        pass  # Screenshot will capture the popup content
```

### Batch Discovery via AI Fallback

For counties without ArcGIS APIs, use the map-based approach:

1. Navigate to the county's parcel viewer
2. Zoom to a target industrial corridor (using the corridor bounding boxes defined above)
3. Take a screenshot of the map at a zoom level showing individual parcels
4. Send to Claude: "Identify all parcel IDs visible on this map screenshot"
5. For each identified parcel, run individual parcel lookups via qPublic/Beacon

This is slower than API pagination but works for any county with a web-based map viewer.

---

## Cross-County Data Sources

These sources provide data across ALL counties and should be queried in parallel with county-specific connectors:

### Regrid (Nationwide Parcel Aggregator)

```
https://app.regrid.com/us/ga/{county_name}
API: https://app.regrid.com/api/v2/parcels (requires API key)
```

Regrid standardizes parcel data nationwide. If you purchase an API subscription, this becomes a single connector for all 8 counties with consistent field names. Pricing: per-parcel or bulk by county.

**When to use:** As a validation layer (cross-reference county data) or as the primary source for counties with poor GIS infrastructure (Spalding, Fayette).

### Georgia Department of Revenue — Property Tax Data

The state collects annual property tax digests from all counties. Check for bulk downloadable files at the state level.

### Georgia Statewide GIS Clearinghouse

```
https://data-georgiagio.opendata.arcgis.com/
```

State-level GIS data including parcels (aggregated), roads, hydrology, and political boundaries. May lag behind county-level data but provides a uniform schema.

### qPublic / Beacon (Schneider Corp)

Schneider Corp hosts property data for many Georgia counties under two brands:
- **qPublic**: Fulton, Cobb, Henry, Spalding, and others
- **Beacon**: Fayette, and others

A single AI fallback module for each brand covers multiple counties. The page structure and field names are consistent within each brand.

---

## Connector Build Priority

Build connectors in this order based on industrial deal flow potential and data availability:

| Priority | County | Primary Approach | Estimated Effort | Industrial Relevance |
|----------|--------|-----------------|------------------|---------------------|
| 1 | **Fulton** | ArcGIS API (confirmed) + qPublic supplement | Low — API ready | Highest — Fulton Industrial Blvd, South Fulton, West Atlanta |
| 2 | **Clayton** | ArcGIS Hub discovery + PublicAccess AI fallback | Medium — needs endpoint discovery | High — Airport corridor, I-85 South |
| 3 | **Henry** | ArcGIS viewer discovery + qPublic AI fallback | Medium — qPublic is solid | High — I-75 South emerging corridor |
| 4 | **Cobb** | ArcGIS Hub/Open Data + qPublic AI fallback | Medium — Hub is clean | High — I-75 North, Acworth/Kennesaw |
| 5 | **Gwinnett** | ArcGIS Hub/Open Data + custom GIS Browser | Medium — good data portal | Medium — I-85 NE pockets |
| 6 | **DeKalb** | ArcGIS API (confirmed) + Hub downloads | Low — API ready | Medium — limited industrial land |
| 7 | **Fayette** | Beacon AI fallback only | High — no API | Low — limited industrial |
| 8 | **Spalding** | qPublic AI fallback + Regrid | High — no API | Low — fringe / long-term play |

---

## Connector Test Harness

Before any county connector goes into production use by the autonomous agent, it must pass a standardized test harness. Building this harness before building individual connectors is high-leverage — every county goes through the same validation flow, and the AI fallback layer needs the same flow to know when to activate. Without a harness, validation becomes manual URL-pasting, which doesn't scale beyond a few counties and provides no health signal once connectors are running in production.

### Why Build a Harness

- **Connector validation**: Confirms every new county connector returns the expected schema and populates required fields with real data before the agent uses it.
- **Production health monitoring**: Detects when a county endpoint changes, goes down, or starts redacting fields (e.g., a state passes legislation similar to NJ's Daniel's Law). The agent reads harness output to decide whether to retry, escalate to AI fallback, or flag for human review.
- **AI fallback trigger logic**: The harness defines what "API failure" means in concrete terms — not just HTTP errors, but missing fields, empty results on known-good queries, or schema drift. The fallback layer uses harness signals to decide when to activate.
- **Regression detection**: When a county redesigns their portal or schema, the harness catches it on the next scheduled run instead of on the morning the agent's pipeline goes silent.
- **Documentation in code**: The harness configuration becomes the canonical source of truth for which connectors exist, where they point, and what fields they map. New team members don't need to read 8 sections of an appendix to understand the connector inventory.

### Recommended Implementation

Build a single Python file (`connector_harness.py`) that operates as both a CLI tool and an importable module the agent can call. The harness should:

**Hold a registry of all county connectors as configuration objects.** Each connector configuration contains: county name, state, market, service URL, parcel layer ID, field mapping (logical name → actual API field name), test bounding box for known-good queries, optional test acreage range, fallback portal URL, and any county-specific notes. New connectors are added by appending to this registry — no code changes needed to add a county.

**Run a standard validation sequence per connector.** For each county, the harness should:

1. **Service alive check** — fetch `{service_url}?f=pjson` and confirm a 200 response with valid JSON service metadata
2. **Layer schema check** — fetch `{service_url}/{parcel_layer_id}?f=pjson` and confirm the layer exists, supports the required query operations (Query, Pagination, Spatial filtering), and exposes the fields the connector's field_mapping references
3. **Field mapping validation** — verify every field name in the connector's field_mapping actually exists in the layer schema. Flag missing fields immediately because they'll silently produce null values in production
4. **Known-good query test** — run a small spatial query against the test bounding box with the configured acreage filter, requesting only the mapped fields. Confirm the response returns features (non-empty) within the bounding box
5. **Field population check** — for each returned feature, calculate the percentage of mapped fields that are populated (non-null, non-empty). Flag any field with population rate below 80% across the test set as low-confidence
6. **Owner data sanity check** — confirm the owner name field returns real names (not redacted, not all-uppercase placeholder strings, length > 3 characters). This catches Daniel's Law-style redactions before they affect the pipeline
7. **Address parsing check** — confirm site addresses and owner mailing addresses parse cleanly (have street numbers, contain state codes, etc.)
8. **Geometry validation** — if geometry is requested, confirm returned polygons have valid coordinates within the county's expected geographic extent
9. **Pagination test** — request 1 record then 10 records on the same query, confirm the API respects `resultRecordCount`
10. **Performance baseline** — measure response time for the known-good query, log to track degradation over time

**Produce a structured health report.** For each county, the harness emits a report with: pass/fail status per check, populated field rates, sample feature output (1–3 records, redacted of any sensitive data), response time, and overall connector health rating (`healthy`, `degraded`, `failing`). Aggregate all county reports into a markets-wide dashboard the agent can read.

**Support multiple operating modes via CLI flags:**
- `--all` runs every connector in the registry
- `--county fulton` runs a single connector
- `--market atlanta` runs all connectors in a given market
- `--quick` skips the slower checks (geometry validation, performance baseline) for fast smoke tests
- `--verbose` prints raw API responses
- `--output report.md` writes a human-readable Markdown report instead of JSON

**Be safely runnable without side effects.** The harness must never write data into the agent's pipeline. It only reads from APIs, writes reports to a `harness_reports/` directory, and exits. The agent reads those reports before deciding whether to use a connector for production discovery.

### How the Agent Uses the Harness

The autonomous agent calls the harness at three points:

1. **On startup** — runs all connectors against the harness. Any connector with `failing` status is excluded from the discovery rotation for the cycle. Any connector with `degraded` status is used but flagged in the strategy memo.
2. **Before each discovery cycle for a specific county** — runs the harness for just that county to confirm it's still healthy. If health degraded since startup, switches to AI fallback for that county.
3. **On any production query failure** — runs the harness as a diagnostic. If the harness still passes, the failure was transient (retry). If the harness now fails, the connector has broken (switch to fallback, log to strategy memo, flag for human review).

### Harness Output Schema

Each county produces a JSON report with this shape:

```
{
  "county": "fulton",
  "market": "atlanta",
  "timestamp": "2026-04-29T03:14:00Z",
  "overall_health": "healthy",
  "checks": {
    "service_alive": { "status": "pass", "response_time_ms": 230 },
    "layer_schema": { "status": "pass", "fields_found": 35 },
    "field_mapping": { "status": "pass", "missing_fields": [] },
    "known_good_query": { "status": "pass", "features_returned": 10 },
    "field_population": {
      "status": "pass",
      "rates": { "Owner": 1.0, "OwnerAddr1": 1.0, "LandAcres": 1.0, "LUCode": 0.95 }
    },
    "owner_data_sanity": { "status": "pass", "redaction_detected": false },
    "address_parsing": { "status": "pass", "parse_rate": 0.98 },
    "geometry_validation": { "status": "skipped", "reason": "geometry not requested" },
    "pagination": { "status": "pass" },
    "performance_baseline": { "status": "pass", "avg_response_ms": 412 }
  },
  "sample_features": [
    { "ParcelID": "07 410001590039", "Owner": "[REDACTED]", "LandAcres": 15.2 }
  ],
  "warnings": [],
  "errors": []
}
```

### Markets-Wide Dashboard

Aggregate report across all counties for at-a-glance health monitoring. The harness writes this to `harness_reports/markets_dashboard.md`:

```
| County    | Market  | Status   | Last Check          | Pop. Rate | Response Time | Notes |
|-----------|---------|----------|---------------------|-----------|---------------|-------|
| Fulton    | Atlanta | Healthy  | 2026-04-29 03:14    | 99%       | 412ms         |       |
| DeKalb    | Atlanta | Healthy  | 2026-04-29 03:15    | 97%       | 380ms         |       |
| Cobb      | Atlanta | Degraded | 2026-04-29 03:15    | 78%       | 1240ms        | LUCode population dropped from 95% to 78% |
| Gwinnett  | Atlanta | Healthy  | 2026-04-29 03:16    | 98%       | 510ms         |       |
| Clayton   | Atlanta | Failing  | 2026-04-29 03:16    | —         | timeout       | Endpoint returning 503 — switched to AI fallback |
| Henry     | Atlanta | Healthy  | 2026-04-29 03:17    | 96%       | 445ms         |       |
| Spalding  | Atlanta | N/A      | —                   | —         | —             | No API; AI fallback only |
| Fayette   | Atlanta | N/A      | —                   | —         | —             | No API; AI fallback only |
```

### Building Order

1. Build the harness FIRST (before any individual connector). Use the Fulton County connector spec from this appendix as the seed entry in the registry.
2. Run the harness against Fulton — this validates both the harness itself and the Fulton connector simultaneously.
3. Add the next county to the registry, run the harness, fix any issues.
4. Continue until all 8 counties are validated.
5. Once all counties are passing, the harness becomes a continuous health monitor that the agent calls at the three integration points described above.

### Operational Cadence

- Run the harness in full nightly as part of the agent's autonomous loop (cheap, completes in under 2 minutes for 8 counties).
- Run a single-county harness check before each discovery cycle for that county.
- Run the harness manually after any change to a connector's configuration.
- Archive harness reports for 90 days to enable trend analysis on connector health and population rates over time.

---

## Legacy Smoke Test (manual, for one-off validation)

For ad-hoc connector validation outside the harness (e.g., evaluating a new county before adding it to the registry), the following manual sequence is sufficient:

1. **Service metadata**: Hit `{service_url}?f=pjson`, confirm valid response and identify parcel layer ID.
2. **Schema discovery**: Hit `{service_url}/{layer_id}?f=pjson`, document field names matching the logical schema.
3. **Known parcel test**: Query a parcel with known attributes (e.g., your firm's existing property). Verify all fields match.
4. **Acreage range test**: Query parcels with `acreage >= 5 AND acreage <= 50` in a known industrial corridor. Verify results are non-empty and acreage values are reasonable.
5. **Owner test**: Query a specific owner name. Verify results match county records.
6. **Geometry test**: Verify returned polygon coordinates plot correctly on a map (centroid falls within county boundaries, polygon area roughly matches reported acreage).
7. **AI fallback test**: Intentionally disable the API layer and verify the AI fallback produces the same results (within tolerance) for the known parcel.

Once a county passes manual smoke testing, add it to the harness registry for automated ongoing validation.

---

## Notes for Human Iterating on This Appendix

1. **Verify field names**: The ArcGIS field names listed above are educated guesses based on Georgia county conventions. The first task for each connector is to hit `{service_url}/{layer_id}?f=pjson` and map the actual field names. This is a 5-minute task per county.

2. **Regrid API**: If you subscribe to Regrid's API, it replaces the need for county-specific ArcGIS connectors for basic parcel data. The county APIs would still be needed for zoning, land use, and incentive overlays.

3. **Rate limiting**: Georgia county servers are not built for high-volume queries. Implement polite scraping: 1 request per second per county, with exponential backoff on errors. Run discovery cycles overnight.

4. **Caching**: Cache all API responses and AI extractions locally. Parcel data doesn't change frequently — a 30-day cache is reasonable for ownership/zoning, 90-day for geometry.

5. **qPublic selectors**: The HTML selectors in the navigation functions above are approximate. qPublic updates their frontend periodically. The AI fallback approach handles this gracefully — Claude reads the page regardless of selector changes.

6. **South Fulton**: The City of South Fulton (incorporated 2017) administers its own zoning separate from Fulton County. You may need a separate connector for South Fulton's zoning data, even though parcel/tax data flows through the county.

7. **Municipal overlaps**: Several target areas are in incorporated cities (Stockbridge in Henry, Jonesboro in Clayton, Kennesaw in Cobb). Zoning is controlled by the municipality, not the county. The agent should cross-reference municipality boundaries when scoring entitlement complexity.
