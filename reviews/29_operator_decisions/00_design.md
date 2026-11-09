# Operator decisions of 2026-11-09 — design

> **Tier 2** (D1 touches external-service integration and the robots policy
> of three tools; D3 changes a BTS input of the metric once any jurisdiction
> is verified). The pre-change risk register and the disposition of every
> item is `02_risk_review.md`; the decisions, the evidence and the live
> counts are in `03_decision_note.md`. Model names are omitted.

## The four decisions (operator, 2026-11-09, binding)

The operator answered the four open questions of reviews/21-28:

| | Question | Answer |
|---|---|---|
| D1 | ArcGIS Online layers answer robots.txt with 403: read them? | **Allow** |
| D2 | Flood: AE with a LOMR, AH/AO/A99/AR: block or flag? | **Flag, don't block** |
| D3 | Districts with no setbacks of their own: BTS N/A or count? | **Do not block; investigate further** |
| D4 | The Zoneomics cache and the committed quotes: delete? | **Keep for now** |

D2 and D4 need no code: they are recorded and pinned by tests. D1 and D3
are the code changes below.

## D1: the narrow ArcGIS robots exception

### Before

`rules_scrape.Robots.verdict` was the verdict of rules_scrape; adjacency_import
and hazards_import each carried a copy of it (`robots_verdict`) with their own
token. `robots_for` turns any robots.txt answer that is not 404/410 or a clean
text 2xx into `deny_all`. The four ArcGIS Online hosts the zoning snippet
names (services, services3, services7, services8.arcgis.com) answer
`GET /robots.txt` with a plain `HTTP 403` and the body "Invalid URL" (no
Cloudflare/Akamai server, no challenge header or page; observed again on
2026-11-09). So South Fulton, Fairburn, Union City and East Point failed in
adjacency_import, and rules_scrape --watch / --flags could not read their
layers.

### After

One function, `rules_scrape.robots_verdict(rb, path, token)`, is the verdict
of all three tools (`Robots.verdict` calls it with rules_scrape's token;
adjacency_import and hazards_import call it from `_check` with theirs; they
define no verdict of their own, D1-01). It is the old body plus one early
branch, taken only when ALL of these hold (each a separate, explicit check):

1. `rb.deny_all` and `rb.unavailable_status in (401, 403)`. `robots_for`
   sets `unavailable_status` only when robots.txt of THIS origin answered on
   hop 0 and `_get` raised `Unreachable(plain_refusal=True)`: a 401/403 with
   no Akamai/Cloudflare server header, no `cf-mitigated`/`cf-chl*`/WAF header
   and no challenge title or body (D1-02). A redirect, a 5xx, a timeout, a
   connection error, 407, 429, an HTML 200, an off-site redirect and an
   above-cap Crawl-delay never set it. A parsed robots.txt never has
   `deny_all`, so a readable robots.txt always wins (D1-03).
2. `arcgis_exception_host(rb.origin_netloc)`: no userinfo, no `%`, no port
   other than 443; the hostname normalised with the LF-03 `norm_host`
   (lower case, IDNA, trailing dot stripped) must `re.fullmatch`
   `services[0-9]*\.arcgis\.com` (ASCII) or be in
   `OPERATOR_ACCEPTED_ARCGIS_HOSTS` (D1-04). `_check` runs on every hop, so
   every redirect target is judged on its own origin.
3. `arcgis_public_rest_path(host, path)`: the raw path has no `%`, `..`,
   `//`, backslash or `;`, and fullmatches
   `/<org>/arcgis/rest/services/<seg>(/<seg>)?/(FeatureServer|MapServer)/<n>(/query)?`
   (segments `[A-Za-z0-9_-]+`, case-sensitive, ASCII digits); exactly one
   `f`, in `{json, pjson}` for layer metadata and `{json, pjson, geojson}`
   for `/query`; no `token` parameter (D1-05). Service roots, `exportImage`,
   `export`, `tile`, `applyEdits`, `queryRelatedRecords`, `admin`,
   `sharing/rest`, `f=html` and encoded paths are refused.

`deny_all` stays True: the decision is per URL, the host never becomes
allow-all.

`OPERATOR_ACCEPTED_ARCGIS_HOSTS` ships EMPTY (D1-06). Evidence, 2026-11-09:
`https://gis.atlantaga.gov/robots.txt` answered `HTTP/2 404` (text/html IIS
404 page) and `https://gismaps.fultoncountyga.gov/robots.txt` answered
`HTTP/1.1 404 Not Found`: both are allow-all already and need no exception.
A host is added only with dated evidence of a plain 401/403 robots answer.
Its path form (`/<dir>/rest/services/...`) has its own regex, unused while the
constant is empty.

Unchanged: AI-agent tokens, Content-Signal ai-input=no, the Municode
denylist, the Zoneomics terms block (`TERMS_BLOCKED_DOMAINS`, `MIRROR_HOSTS`),
1 req/s per host, Retry-After and its cap, the 429 rule, the request budget,
the generic User-Agents, manual redirects, no cookies.

Etiquette (D1-08): every host `arcgis_exception_host` accepts is spaced on
one shared key, `arcgis.com`, so the four servicesN hosts together get at
most 1 request per second. This applies whenever such a host is contacted
(robots.txt included), which is stricter than "only once accepted".

Honesty of the outputs (D1-07): `robots_meta[host]` keeps `deny_all: True`
and gains `exception: "robots unavailable (403): ArcGIS public REST accepted
by operator 2026-11-09"` (the status from the actual answer). One line per
host, `robots: <host>: <text>`, is printed by adjacency_import,
hazards_import and rules_scrape (hostname and fixed text only). rules_scrape
--watch also writes `robots_exceptions` into `watch_state.json`, a section
into the report, and the text as the detail of each GIS row read under it.

Terms (D1-09): **not verified.** The Esri legal pages (full master agreement,
product-specific scope of use) answered 200 in an earlier session but render
by script, so no text about automated access was read from this
environment. Each ArcGIS Online item is also governed by its owner's licence
(`licenseInfo`), not only by Esri. The basis is the operator's acceptance
(D1) and the fact that anonymous REST query is the documented public access
pattern of ArcGIS Online layers. adjacency_import counts layers whose
metadata carries a non-empty `copyrightText` (counts only).

Side effect (D1-10): the exception is shared, so rules_scrape `--flags`
(the ZoningCases layer on services3) and `--watch` (the South Fulton,
Union City, East Point zoning layers and the cases layer) now read data
they were refused before. The loop-lock skip is unchanged: while the
experiment loop holds its flock, no GIS layer is queried (tests:
`WatchTests.test_watch_refuses_gis_while_the_loop_runs`,
`FlagTests.test_flags_skip_case_layers_while_loop_runs`, and
`test_operator_decisions.OutputTests.test_case_layers_still_skipped_while_the_loop_runs`
with a services3 host behind a 403 robots).

`zoning_import.py` (D1-11) is NOT changed. Its `ZoningSession` calls
`requests.get` without a robots check and with requests' default
`allow_redirects=True`. D1 makes its reads of the ArcGIS Online layers
acceptable; adopting the shared fetcher would change how stored zoning (a
scoring input) is ingested and many `test_zoning_import` pins, so it is an
open item for a separate Tier 2 round (`03_decision_note.md`).

## D3: listed district without setbacks counts for BTS

### Before

In a verified jurisdiction a district whose rules give no numeric
front/side/rear setback was projected with the 25 ft default, labelled
`verified jurisdiction, default setback`, and `meets_bts_footprint` returned
False (finding 6 of reviews/22_site_fit): BTS N/A however large the fit.

### After

- `site_fit._rules_for` sets `_Rules.default_listed` only when the district
  IS listed in `zoning_rules.json` (dimensions present) and all three keys
  are absent or null (D3-01, D3-02). An unlisted district (dimensions None,
  "district ... not listed in the rules") and a listed one with a present
  but unusable value ("see table", a negative number: "... is not a number:
  not applied") keep the old behaviour, BTS N/A. Today the only listed
  district of this kind is Fairburn PD.
- `Projection.setback_default_listed` = verified and default_listed.
- `meets_bts_footprint`: only the `setback_from_rules` condition is relaxed
  to `setback_from_rules or setback_default_listed`. The `type(p) is
  Projection` and `_ISSUED` identity checks, `rules_verified`, status ok and
  `>= BTS_MIN_FOOTPRINT_SF` are unchanged (D3-06).
- `site_fit.bts_review_text(p)` (new, protected) returns
  `BTS_REVIEW_TEMPLATE` formatted with the default used — exactly
  "investigate further: no district setbacks in zoning_rules.json; 25 ft
  default used" — only for an issued, verified, default-setback, status-ok
  projection, whatever its size; None otherwise (forged, draft, unlisted,
  district setbacks). The same text is a constraint line of that projection.
  `VERIFIED_DEFAULT_LABEL` keeps its value; the text is a separate constant
  (D3-04).
- `research.py` calls `site_fit.bts_review_text(projection)` (the only new
  name in `test_site_fit_scoring.StaticResearchTests.ALLOWED`, D3-05),
  appends the text to the scoring notes and writes it once through
  `_flag_data_gap_once` (flag_type `data_gap`, never `actionability_block`).
  The scoring function is shared by the per-parcel and the cycle path.
- `reporting`: memo and snapshot say "used for BTS: yes (default setback;
  investigate further: ...)"; the unlisted case keeps "yes, rated N/A
  (default setback ...)" (D3-07).

Everything is gated on a projection: research passes one only for a
verified jurisdiction with a known class (the S9 gate, unchanged). While
every jurisdiction is draft no code path changes: the site_fit, zoning,
adjacency and building_comps golden checks exit 0 unmodified, and the
hazards check is identical with the clock frozen at its record date.

The first jurisdiction verification after this change needs a new run and
baseline (it always did; this change moves BTS for Fairburn PD).
