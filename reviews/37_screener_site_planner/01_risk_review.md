# 37 — Screener site planner, built check and viewer: risk review (before the port)

## How this was produced

Tier 2 workflow, Risk phase. Three independent, fresh-context reviewers
(engineering, integration, data safety) read the prototype
(`siteplan2/3/4.py`, `run_siteplans4.py`, `builtcheck.py`, `viewer.py`,
`viewer_template.html`, `aerials.py`), the design notes and the reference
outputs, and wrote no code. This document merges their findings (48 risks
into 28). Nothing of high severity was dropped. A fourth agent wrote this
synthesis. The Decide phase records the outcome in `02_decision_note.md`.

Scope: new standalone modules under `screener/` (`siteplan.py`,
`siteplan_inputs.py`, `siteplan_render.py`, `builtcheck.py`, `viewer.py`,
`viewer_template.html`), tests and docs. Every existing screener `.py`
file, `settings.toml`, `requirements.txt`, `autoresearch/` and every
root human-owned file stays unchanged.

Facts the reviewers checked:
- An offline replay of the unmodified prototype from `screener/cache/http`,
  with requests blocked, reproduced ranks 4, 33, 124 and 9 byte for byte
  (JSON and SVG), in 0.5 to 19 s per site.
- The union of the four listed parcel ids for `115+170` is 77.6 ac, which
  matches the reference `parcel_ac_map`. An explicit pid list therefore
  replaces the owner filter.
- A normalised scan of every reference output (plans, SVGs, built check,
  `land_today`, `visual.json`, notes, viewer HTML) against the `ranked.csv`
  owner and owner_mailing values found 0 hits. The PII risk is in the code,
  not in today's data.

Two choices the reviewers left open are settled here so the writers have
one rule each:
- **Not-checked inputs.** `feasible` becomes `None` ("incomplete"). Floor
  area is not counted, a named not-checked flag is set, and the record is
  never `True` and never `ACCESS_NONE`.
- **Order of work.** First port the prototype's behaviour and prove
  parity. Then make each behaviour fix (R4, R15–R18, R21) as a separate
  step whose deltas are listed in `02_decision_note.md`. The fail-closed
  input handling (R2) can go in with the port, because every input in the
  reference replay was answered and so parity is unaffected.

## Risks, by severity

### High (each one blocks the merge)

**R1. Owner data in code, queries or outputs (SR-8).**
How it happens:
- The prototype runner builds `115+170` by requesting `Owner` and keeping
  features whose owner starts with a hardcoded owner-name literal. Its
  title says "(same owner)".
- The viewer copies free text into the page: `decisions.csv` notes (the
  operator's owner look-ups), lead.md "In one line" headlines, and
  `visual.json` and `land_today` evidence. Nine headlines and seven notes
  in the reference viewer contain "owner" or an entity word.
- The transmission query asks for the utility field `OWNER`.
- The leak check is an exact, case-sensitive substring match on strings
  longer than 6 characters, taken from `ranked.csv` only. It runs on the
  plans JSON only, not on the final page, the SVGs, the plans JSON file
  as written, or the built-check and `land_today` CSV/JSON. Owners of
  assemblage members outside `ranked.csv` are unknown to it.

*Mitigation:*
- Do not port the owner filter, the hardcoded parcel ids or the titles.
  Assemblages come only from `--assemble NAME=pid,...`.
- Every outFields value is a module-level constant on an allow-list, with
  no `Owner*`, `Mail*` or `*`. Drop `OWNER` from the transmission query.
- One shared owner guard runs on the final bytes of every artifact before
  the atomic replace: SVG, plans JSON, builtcheck, `land_today` and the
  viewer HTML. It normalises with NFKC, casefold and collapsed
  punctuation and whitespace, and also checks the core name with
  LLC/INC/LP/TRUST and similar suffixes removed (8 characters or more,
  on word boundaries). It skips a mailing string that equals the same
  parcel's site_address. It fails closed: no file is written, and only
  the field and a count are reported, never the value.
- Allow-list what each artifact takes in. From decisions, only the
  decision value. From leads, only the verdict enum. Headlines and notes
  appear only behind explicit opt-in flags, and still pass the guard.
- Output columns are an explicit allow-list per file.

*Gate:*
- In all three test files, a synthetic owner ("TEST OWNER ALPHA LLC") is
  planted with different case and spacing in a decisions note, a
  headline, `visual.json` evidence, an SVG `<text>` and a plan title.
  Each run exits non-zero, writes no file and prints no owner text. A
  clean fixture passes, with no `note` key by default.
- A static/AST test finds no `(?i)owner|mail` in any outFields literal
  and no `startswith` on an owner attribute. A `request_json` spy sees no
  such fields at run time.
- `git grep` finds none of the prototype parcel ids or the owner prefix
  in `screener/*.py`.
- Before merge, one normalised scan of the real-run outputs gives 0 hits.

**R2. A failed or partial fetch reported as a clean result.**
How it happens:
- `frontage.check_parcel` returns `NOT_CHECKED` with no line. The planner
  then emits `ACCESS_NONE` ("landlocked", severity 1, 0 SF), so an outage
  reads as a landlocked parcel.
- `dem_samples` swallows `ArcGISError` per chunk and skips NoData, so
  missing ground reads as flat: no steep cells, a wrong relief and pond
  grid, and no flag.
- `county_near`, `transmission` and the built check's records-inside query
  use raw `request_json` with no `check=`. A truncated answer
  (`exceededTransferLimit`, no features list) is accepted and cached. That
  loses residential buffers, which overstates SF, or loses power lines,
  which lets a building sit under them. Transmission features without
  geometry are dropped silently.
- This breaks the screener invariant in `tests/test_integrity.py`.

*Mitigation:*
- Every fetch returns (data, state), where state is checked, none or
  not_checked with a reason. The state appears in `rec['inputs']` and in
  the SVG panel.
- Named flags: `FRONTAGE_NOT_CHECKED`, `DEM_NOT_CHECKED`, `DEM_PARTIAL`
  (coverage below about 95%), `NEIGHBOURS_NOT_CHECKED`,
  `TRANSMISSION_NOT_CHECKED`. Any of them sets `feasible=None`.
- `ACCESS_NONE` only when the frontage state is none.
- Add `check=` callbacks that require a features or samples list of the
  right length and no `exceededTransferLimit`. `check` is not part of the
  cache key, so parity holds. Prefer `features_in` (id-paged,
  BadAnswer-checked) for new queries outside the parity set.

*Gate:* `test_siteplan.py` with monkeypatched fetchers that raise
`ArcGISError` or return `{}`, a truncated answer, or features without
geometry, for each of DEM, frontage, neighbours, transmission and layers.
Each record carries the named flag and `feasible is None`, and never
`ACCESS_NONE`. A params test shows the inputs module sends exactly the
prototype's requests: URL, outFields, pads 0.0015/0.002, 7-decimal
rounding, 400-point chunks, returnFirstValueOnly, max_age_days 3650.

**R3. HTML/SVG injection in the viewer and renderer.**
How it happens:
- Every SVG goes into the page through `innerHTML` behind a blocklist
  `clean_svg`. Known bypasses: `<img/src/onerror=`, `href =` with a space,
  `<style>@import`, `<animate attributeName=href>`, `<use>`,
  DOCTYPE/ENTITY.
- The data JSON escapes only `</`, so `<!--<script>` still breaks the
  page.
- The two chained `.replace` calls (`__DATA__`, then `__RUN__`) let data
  carry a placeholder.
- The prototype `esc()` does not escape quotes or strip XML-invalid
  characters.
- SVG text comes from county addresses, GDOT road names, HIFLD values and
  jurisdiction names.

*Mitigation:*
- Render with `plan._esc` (import only). Emit no script, href or event
  attributes.
- The viewer sanitises with an allow-list. It parses with
  `xml.etree.ElementTree` after refusing DOCTYPE or ENTITY. It accepts
  only the emitted element and attribute set, with no `on*`, `href`,
  `xlink:href` or `style` element, and `url()` only as `url(#id)`.
  Anything else is refused.
- The JSON is escaped `<`, `>`, `&` and U+2028/2029 as unicode escapes.
- The template is filled in one pass. Numeric fields are coerced in
  Python.
- Add a CSP meta tag. Keep the Google Maps link regex.

*Gate:* `test_viewer.py` adversarial fixtures (each bypass above, plus
`<foreignObject>` and `javascript:` links) are all refused. An address of
`</script><!--<script>alert(1)//` and a run name containing `__DATA__`
give a page whose script block holds no literal `<` from the data, whose
JSON round-trips through `json.loads`, and where exactly one substitution
was made. A title containing `"<&\x01` gives an SVG that ElementTree
parses. The CSP tag is present.

**R4. Feasibility does not follow flag severity.**
`feasible` = pond fits and routes and driveway, so severity-1 flags do not
gate it. In the reference:
- #9 has `USE_PERMIT_REQUIRED` but is feasible with 254,000 SF counted.
- #44 has `HEIGHT_CAP` and is feasible.
- #124 is infeasible with no severity-1 reason.
- #9, #21, #64 and #100 have 0 trailers against a target of 7 to 51 and
  are feasible.

Coverage and height caps are computed in `report()` only for the variant
already chosen, so a cap-violating plan can beat a compliant smaller one.

*Mitigation:*
- One pure function decides feasibility from a declared `BLOCKING_FLAGS`
  list.
- Use-permit gates go in a separate `gated` field.
- Every infeasible record names a reason (`POND_DOES_NOT_FIT` promoted,
  or `NOT_FEASIBLE_REASON`).
- Evaluate coverage and height per alternative before the sort.
- Port the prototype behaviour first. Make this change as a separate
  step, with deltas for at least #9, #44 and #124.

*Gate:* An invariant test over every replayed and synthetic record:
`feasible == no blocking flag`; `total_sf == 0` unless feasible;
infeasible implies a named reason. A synthetic coverage-cap site picks
the compliant smaller variant.

**R5. Reuse-list name collisions and changes to existing modules.**
- `detention.place_pond` places one fixed 400×200 ft pond (about 9.1
  ac-ft). The prototype sizes 1 to 3 ponds from impervious area: #4 needs
  6.5 ac-ft and one pond, #33 needs two ponds.
- `plan.render` is the F12 brief renderer used by `report.write_all`.

Wiring either in mis-sizes detention, and bending it to fit changes every
run brief. The prototype also calls private `checks._overlap`. Its
`OPERATOR` constant is a hand copy holding rear-load and cross-dock only,
while `sitefit.PROTOTYPES` also has front-load.

*Mitigation:*
- Port `pond_dims`, `detention()` and sized pond placement into
  `siteplan.py` under distinct names (e.g. `place_sized_pond`).
- `siteplan_render.py` is self-contained and imports only pure helpers
  such as `plan._esc`.
- Use only `sitefit` (fit_lengths, normalise, polygon_parts,
  envelope_corners, PROTOTYPES/GROUPS), `checks._overlap` (wrapped in one
  place, with a pinning test), `layers`, `frontage`, `arcgis` and `geo`,
  all unchanged.
- Derive the operator variants from `sitefit.PROTOTYPES`/`GROUPS`.
- The prototypes the planner uses are a named constant, rear-load and
  cross-dock as in the prototype. Adding front-load is an operator
  decision (R15) and would be a listed delta.
- Market comps are a separate `MARKET_COMPS` constant marked
  `operator=False`.

*Gate:*
- `git diff --exit-code main -- screener/{detention,plan,sitefit,frontage,arcgis,geo,layers,checks,__init__,run,report,rank,scoring,screens}.py screener/settings.toml screener/requirements.txt screener/autoresearch/`
  prints nothing.
- A test shows the pond_dims prismoid holds at least v within 1%, and the
  #4 replay gives 6.5 ac-ft with 1 pond.
- Patching `sitefit.PROTOTYPES` changes the variants, and the defaults
  equal sitefit (rear W 260, 400–640; cross W 520, 600–1,000).
- A static test finds no private-attribute use outside the allow-list
  `{checks._overlap}`, and no `building_prototypes.json`,
  `zoning_rules.json`, `import site_fit` or `sys.path` strings.
- `test_sitefit` and `test_detention` pass unchanged.

**R6. Parity with the reviewed prototype cannot be shown.**
- The arcgis cache key is url + params, so any drift in outFields, pads,
  rounding, chunking or query style misses the cache and goes live.
- The prototype sets `arcgis.cache_days = 30` at import, while the
  default is 7, so the 2026-11-13 entries expire about **2026-11-20**.
- `115+170` used the owner filter and a hardcoded tax_acres of 73.4.
  Rank 25 uses a cemetery-parcel cut.
- The reference outputs are gitignored and out-of-repo, so a test that
  reads them is not hermetic.

*Mitigation:*
- Use a scratch parity script, not shipped:
  `python -m screener.siteplan RUN_DIR --ranks <all 33> --cache-days 30`
  with requests blocked. `--cache-days` is a CLI option, never a global.
  Compare each record with `json.dumps(sort_keys=True)` and each SVG byte
  for byte.
- For the assemblage:
  `--assemble 115+170=07 260001350151,07 260001221014,07 260001351027,07 260001350904`.
  Its query uses outFields ParcelID only, which is a known cache miss: one
  live fetch or a stored fixture. Tax acres come from the `ranked.csv`
  members present, or the value is "unknown" and ACREAGE_MISMATCH is
  skipped (a listed delta).
- `--cut-parcel 25=09F350101520017:100` sends the prototype's params.
- Run the replay before the cache expires, or record why it went live.
- Commit 2 or 3 trimmed, owner-free fixtures for a hermetic subset.

*Gate:*
1. The offline replay gives identical JSON and SVG for the 31 single-rank
   sites plus rank 25. `115+170` matches except for listed deltas. There
   are zero unexpected network calls. The table and delta list are in
   `02_decision_note.md`.
2. A hermetic fixture test asserts configuration, L, D, SF, doors,
   trailers, cars, ponds, storage, feasible and the flag set for at least
   2 reference sites.
3. No test reads `screener/output`, `screener-data` or `/tmp`.

**R7. AutoResearch evaluator baseline contract.**
- `evaluate.py --decide` refuses after any non-`.md` change under
  `screener/` outside `tests/` since the best kept run, so every new
  module and `viewer_template.html` forces a human `--new-baseline`.
- It also refuses on untracked files under `screener/`.
- `build_eval.py:336` copies `layers.LAYERS`, so a new endpoint there
  changes the frozen key.
- The score cannot move: the evaluator imports only `screens`.
- This container has no `experiments.tsv`.

*Mitigation:*
- Add new files only.
- New endpoints are constants in `siteplan_inputs.py`/`builtcheck.py`,
  never in `layers.LAYERS`.
- The PR body, README and `02_decision_note.md` state that a human runs
  `python -m screener.autoresearch.evaluate --decide --new-baseline` after
  the merge, with the score expected unchanged.

*Gate:*
- `git diff --name-only main...HEAD -- screener` lists only the new
  modules, the template, README.md, SITE_PLANNING.md, AGENTS_DESIGN.md
  and `screener/tests/*`.
- A static test shows that `autoresearch/*.py`, `rank.py`, `scoring.py`,
  `screens.py`, `run.py` and `report.py` never name the new modules, and
  that the `layers.LAYERS` keys and urls are unchanged.
- After the commit, `git status --porcelain --untracked-files=all -- screener`
  is empty.

**R8. Test hermeticity (SR-6).**
`request_json`, `features_in`, `layers.fetch` and `frontage.check_parcel`
all go to the network and write into the shared `screener/cache/http`.

*Mitigation:*
- Network I/O lives only in `siteplan_inputs.py` and builtcheck's fetch
  layer. `siteplan.py` and `siteplan_render.py` take plain data.
- Each new test module patches `arcgis._session` and
  `requests.Session.request` to raise, and points `arcgis.CACHE_DIR` at a
  temp dir (the `test_integrity.py` pattern).
- Fixtures are synthetic parcels of 25 ac or less, with synthetic owners.
- Never import from `/tmp`.

*Gate:*
- `HTTPS_PROXY=http://127.0.0.1:9 HTTP_PROXY=http://127.0.0.1:9 NO_PROXY= python -m unittest discover -s screener/tests -t .`
  passes: 661 existing tests plus the new ones, about 45 s or less in
  total and 15 s or less for the new tests.
- The number of files in `screener/cache/http` is unchanged.
- An AST test shows `siteplan.py`/`siteplan_render.py` import neither
  `requests`, `arcgis`, `layers` nor `frontage.check_parcel`.

### Medium

**R9. Built check and `land_today` classification.**
- A record query that failed (stored as a string), or a parcel beyond the
  top 300, falls through to "no building found".
- `land_today` could say `raw_land` from footprint absence alone, but
  footprints miss yards, quarries and landfills (#11, #29, #47).
- No prototype code produces `land_today`, so its logic is new and
  unreviewed.
- `visual.json` is keyed by rank only.
- `largest_footprint_sf` is the structure's whole SQFEET (for example
  330,617 SF on #1, with 21,947 SF inside the outline).

*Mitigation:*
- A separate state `not checked` that wins over "no building found".
- Without a visual entry, the parcel is `not_reviewed`. `raw_land` only
  when the visual review says raw and the check is not built.
- `visual.json` is checked against a schema (fixed enums, evidence up to
  about 600 characters) and joined by parcel_id (R10).
- Rename the field to `largest_structure_sf`, or clip it.
- Name the thresholds as constants (2,500 / 10,000 SF, $250,000) and make
  the record limit `--top N`.
- Word the verdict as "no mapped footprint (imagery dated X)".

*Gate:* `test_builtcheck.py` with fake fetchers:
- A query failure, or a parcel outside the top N, gives `not checked`.
- No `--visual` gives no `raw_land`.
- An unknown enum is refused.
- Built plus visual raw gives `unclear`/`partly_usable`.
- A missing `ImprAppr` is not read as $0.

Parity: an offline replay reproduces `builtcheck.csv`, and the
`land_today` values match all 250 rows, or the deltas are listed.

**R10. Joins by rank across runs.**
- Ranks are strings (`115+170`) and specific to one run.
- `visual.json`, `land_today` and the viewer join by rank. The viewer's
  `int(key.split('+')[0])` crashes on an `--assemble` NAME.
- Plans or aerial findings from another run attach silently to the wrong
  parcel.

*Mitigation:*
- Every output carries `schema_version` and the run identity (folder name
  plus the sha256 of `ranked.csv`).
- Every record carries `parcel_ids` and a `key` (rank or slugged NAME).
- Joins are by parcel_id. A foreign run fingerprint, or a mismatched rank
  and pid, is refused, or skipped with a visible note.

*Gate:* `test_viewer` (foreign run refused; an `--assemble north=pidA,pidB`
plan renders); `test_builtcheck` (mismatched visual is refused, with a
reason); a siteplan-to-viewer round-trip on a fixture.

**R11. Paths, atomic writes, idempotency (SR-7, SR-9).**
- `plans4.json` is rewritten in place, not atomically.
- Stale plans survive unless `--redo` is passed.
- Rank strings and `--assemble` NAME become filenames.
- The viewer reads `plans_dir / t['svg']` unchecked, so `../..` reads any
  file.
- Lead directories come from `pid.replace(' ', '_')`.
- builtcheck writes non-atomically.

*Mitigation:*
- One slug helper (`^[A-Za-z0-9_-]{1,64}$`, the briefs rule).
- Resolve every path and check `is_relative_to(base)`.
- SVG names must match `^plan-[A-Za-z0-9_-]+\.svg$` inside `--plans`.
- Writes go through a temp file and `os.replace`.
- Each run writes a complete plans JSON with the code hash and options.
  Merging needs an explicit `--merge`, for the same schema and run only.

*Gate:* Tests for `--assemble ../x=…`, `a/b`, a NUL, svg values of
`../../etc/passwd` or an absolute path, and parcel_id `../../tmp` with
`--leads`. Each refuses, and nothing is written outside `--out` (rglob
check). With `os.replace` patched to raise, the old file is
byte-identical. A builtcheck re-run replaces its outputs and does not add
to them.

**R12. Where-clause injection from CLI parcel ids.**
- `--assemble` and `--cut-parcel` ids go into `ParcelID IN ('…')`.
- Fulton ids contain inner and doubled spaces (`14F0053  LL0275`), so
  trimming them drops parcels silently.

*Mitigation:*
- A strict pattern (uppercase letters, digits, spaces), quotes doubled,
  at most about 12 ids.
- Compare exactly, and never trim inner spaces.
- Take geometry from `ranked.geojson` where present.
- Exactly one feature per id, otherwise refuse with a non-zero exit.

*Gate:*
- `x="A') OR (1=1"`, `;` and an over-cap list are refused before any
  request (the fake session is not called).
- An unknown pid, or a wrong feature count, exits non-zero with no plan.
- A double-space pid round-trips unchanged into the query (spy) and into
  `parcel_ids`.

**R13. Multi-part parcels and assemblage seams.**
- `parts()` silently drops parts under 40,000 SF.
- Routes cannot cross between parts (#4 has 2 parts).
- `clean()` is an opening, so a sub-foot seam between members stays an
  OTHER edge, with 10 ft setbacks on both sides: a dead strip about 20 ft
  wide.
- Non-adjacent members form a MultiPolygon silently.

*Mitigation:*
- Close seams with `buffer(+s).buffer(-s)`, s about 2–3 ft, and report
  `seam_ft_closed`.
- Flag `ASSEMBLAGE_NOT_CONTIGUOUS` (severity 1).
- Report the dropped parts in the inputs.

*Gate:*
- Two 600×800 ft squares with a 0.5 ft gap give one polygon, no interior
  OTHER edge, and the SF of a solid 1,200×800 ft rectangle.
- Squares 200 ft apart are flagged.
- A site where only the frontage-less part fits gives `ROUTE_INFEASIBLE`.

**R14. Polite fetching and process-global state (SR-11).**
- The prototype sets `arcgis.cache_days = 30` at import, for the whole
  process.
- `arcgis.py` does not pace requests and ignores Retry-After.
- The built check makes about 250 FEMA and 300 county queries back to
  back. The DEM adds 400-point chunks per site, cached for 3,650 days in
  the shared folder.

*Mitigation:*
- Never assign arcgis globals. Pass `max_age_days` per call.
- A per-host throttle in the new modules, not in `arcgis.py`: probe
  `cached_json`, and on a miss space requests to a host at least 1 s
  apart.
- No thread pools.
- Stop a host after N consecutive failures and mark the rest not checked.
- DEM cache lifetime as a named constant.
- Hosts are listed in SITE_PLANNING.md. Keep the existing USER_AGENT.

*Gate:*
- Importing all new modules leaves `arcgis.cache_days` unchanged.
- grep finds no `arcgis.cache_days *=` or `ThreadPoolExecutor`.
- A fake-clock test: 5 uncached county calls sleep 4 s or more in total,
  and cached calls do not sleep.
- After K failures, no further calls go to that host and the parcels
  read not checked.

**R15. Draft zoning labels and visible operator decisions.**
- `jurisdiction_rules` is a bare name with no draft status.
- An unknown jurisdiction silently falls back to South Fulton rules.
- The flag is `ATL_PARKING_VERIFY`, but the operator named it
  `ATL_PARKING_CODE`.
- Market parking (0.55/0.70/1.0 per 1,000 SF at 100k/250k SF), trailer
  tiers (0.5/1.0/1.3 per door at 250k/450k), the OTHER-edge 10 ft rule,
  clear heights, island factors, stall widths, HSG_K and corridor widths
  are inline.
- An override applied in one place only gives inconsistent numbers.

*Mitigation:*
- Named constants: `JUR_DRAFT` (every entry `status: 'draft'` with a
  source note), `MARKET_PARKING_TIERS`, `PARKING_RULE = max(code, market)`,
  `TRAILER_TARGET_TIERS`, `ATL_PARKING_CODE`.
- CLI `--parking {code,market,max}` and `--trailer-ratio`.
- Every record carries `zoning: {name, status: 'draft', values}`, the
  `ZONING_DRAFT` flag (never filtered), `JURISDICTION_DEFAULTED` on
  fallback, and `operator_decisions` with the values actually used.
- The SVG and viewer show DRAFT. SITE_PLANNING.md lists each open
  decision.

*Gate:*
- Every record has `zoning.status == 'draft'` and `ZONING_DRAFT`, and
  every SVG and the viewer page contain "DRAFT".
- An unknown jurisdiction gives `JURISDICTION_DEFAULTED`.
- An Atlanta fixture gives `ATL_PARKING_CODE`, and grep finds no
  `ATL_PARKING_VERIFY`.
- Parking equals max(code, market) at the three bands, tested at the
  100k and 250k boundaries.
- `--trailer-ratio` changes the target, the drop-yard size and
  `TRAILER_SHORT` consistently, and is recorded.

**R16. Routes are never validated.**
- v4 dropped the v3 `ROUTE_OUTSIDE_ENVELOPE` check.
- The snap segments (up to 8 cells) and `simplify(step*0.9)` are
  unchecked.
- Above 60k cells the grid coarsens, so narrow corridors appear or
  vanish.
- The diagonal penalty makes open-ground routes up to 1.41× longer (2,800
  ft against 1,980 ft measured). That inflates truck_route_ft, impervious
  area, `DEAD_END_ACCESS` and `SHORT_THROAT`. The search is Dijkstra, not
  A*.

*Mitigation:*
- Keep the algorithm byte-equal for parity.
- Add `validate_route` (`ROUTE_OUTSIDE_ENVELOPE`, severity 1, more than
  about 5 ft outside `walk.buffer(1) ∪ throat`).
- Fix the diagonal cost later, as a listed delta.

*Gate:*
- Rotated corridors from 0 to 85° with an obstacle stay inside.
- A wall is routed around, or gives None.
- An unreachable goal on 6,000×6,000 ft gives None in under 2 s.
- Zero violations over the 33 replays, or they are listed.

**R17. Plan geometry is not physically checked.**
- Entry discs and 90 ft throats are added after the wetland-buffer and
  setback subtraction, so pavement can enter the 25 ft buffer.
- Drop-yard links are added after detention is sized, and `drop_ac` is
  counted even when no yard is placed.
- Building 2 is checked only against the blocked set, and its
  fire/dead-end/throat checks are skipped.
- Union City coverage omits building 2's cars.

*Mitigation:*
- A pure `check_plan` covers: building ⊂ B; module ⊂ P; ponds disjoint
  from modules, routes, yards, wet_buf and trans; yards disjoint from
  ponds and routes; building 2 disjoint from building 1's footprint;
  everything inside the parcel. Violations become flags.
- Recompute impervious area after the links, and count only placed
  yards (a listed delta).

*Gate:*
- A count over the replays (target 0).
- Synthetic: building 2 is not placed over building 1's only pond area.
- Synthetic: a wetland at a court end gives route ∩ wet_buf within
  tolerance, or a flag.

**R18. Frontage GeometryCollection crash.**
- `boundary ∩ buffer` can contain Points.
- `linemerge(snap(...))` raises `GEOSException` (verified on shapely
  2.2.0).

*Mitigation:*
- Keep only LineString parts.
- Turn a per-site `GEOSException` into `FRONTAGE_GEOMETRY` (not checked),
  never `ACCESS_NONE`.

*Gate:* `frontage_runs(GeometryCollection([LineString, Point]))` returns
the run, and a planner test with that frontage does not raise.

**R19. Runtime blow-up.**
- There is no budget: 22 variants × 13 car-row counts × 60 shrinks, ×8
  plans × 10 refits × 3 pad exclusions, plus 6 second-building tries,
  each with up to about 5 grid routes.
- A 200 ac or larger assemblage, or a sliver, can appear to hang.

*Mitigation:*
- `--max-seconds-per-site` (default about 120), which emits
  `PLAN_BUDGET_EXCEEDED` (not feasible).
- Memoise `fit_lengths`.
- Log the grid step used.

*Gate:*
- A synthetic 300 ac square and a 1,000×20,000 ft strip each finish
  within the budget, or carry the flag.
- The unittest suite stays within R8's time.

**R20. DEM keyed by float tuples.**
- `steep_cells` works only because the coordinates are built by repeated
  `x += step`. Any refactor misses every lookup silently.
- Relief and cut/fill do not say how many samples they rest on.

*Mitigation:*
- Integer (i, j) keys.
- Report `dem_points` and `coverage_pct` per module.
- `DEM_PARTIAL` (R2).
- Keep the sampling grid identical.

*Gate:*
- A fake DEM: a 20% plane gives about 100% steep; a 5% plane gives 0;
  half the chunks failing gives `DEM_PARTIAL` with no crash.
- The replay keeps steep_ac, relief_ft and cut_fill_cy identical.

**R21. Constraint buffers clipped before buffering.**
- `checks._overlap` clips to the parcel before `buffer(25)`, so a creek
  just outside the line gets no buffer inside, which overstates SF.
- Streams are unmapped (`STREAMS_PIPELINES`).

*Mitigation:*
- Intersect with `parcel.buffer(25 ft)`, buffer, then clip.
- This is a listed delta after parity. Document it.

*Gate:* A wetland 5 ft outside removes a strip about 20 ft wide from P,
and the parity report lists the changed sites.

**R22. Schema drift and host retirement.**
- The HIFLD public layer may move or stop updating (HIFLD Open was wound
  down in 2025).
- A null or -999999 VOLTAGE is assumed silently to be 115 kV.
- USA Structures (`OCC_CLS`, `SQFEET`, `IMAGE_DATE`) or county
  (`ImprAppr`, `LUCode`) fields may be renamed, giving a crash or a false
  zero.

*Mitigation:*
- URLs and fields are named constants.
- A missing expected field gives not checked or degraded.
- A `VOLTAGE_ASSUMED` note.
- A meta block (URLs, query date, IMAGE_DATE range) in the plans and
  builtcheck JSON.

*Gate:*
- Fixtures: a missing VOLTAGE gives the assumed width plus the note.
- A missing SQFEET or OCC_CLS does not crash and marks the check
  degraded.
- `meta.sources` is present.

### Low

**R23. Determinism.**
- `edges_ft` is built from a set, so the key order depends on
  PYTHONHASHSEED.
- Tie-breaks rely on list order.

*Mitigation:* Sorted keys, and explicit tie-break keys.

*Gate:* PYTHONHASHSEED=1 and 2 subprocess runs give identical JSON and
SVG bytes, and two runs on one fixture are byte-identical.

**R24. Units, projection and degenerate geometry.**
- Square metres are mixed with square feet.
- `Plane.wgs` duplicates sitefit.
- `float(VOLTAGE)` raises on text.
- 'B/D' HSG is read as B, which is non-conservative.
- Empty P, all-wetland and sliver parcels take untested paths
  (`Polygon().bounds` is NaN).

*Mitigation:*
- One ft-plane helper from geo/sitefit.
- Defensive voltage parsing.
- An explicit, flagged dual-HSG choice.
- `NO_FIT` records for degenerate inputs.

*Gate:*
- A 10 ac square measures 10.0 ac ±0.5%, and the round trip is within
  0.01 ft.
- 'NOT AVAILABLE' gives 115 kV.
- 'B/D' gives the documented choice plus a flag.
- Degenerate parcels give `NO_FIT` without an exception.

**R25. Dependency creep and the aerial step (SR-10).**
- `aerials.py` needs Pillow and calls `requests.get` directly.
- numpy is tempting for slope.

*Mitigation:*
- Do not port `aerials.py`. The aerial review stays a documented manual
  step, and its only machine output is a schema-checked `visual.json`.
- The standard library only for slope.

*Gate:*
- `git diff main -- screener/requirements.txt requirements.txt` is empty.
- An AST test shows the new modules import only stdlib, shapely,
  `screener.*`, and `requests` only in the fetch modules.
- No `PIL`/`numpy` imports and no `requests.get/post` in the new
  modules.

**R26. CLI error handling and docs scope.**
- The prototype catches every exception, stores `str(exc)[:300]` and
  exits 0.
- An `--out` inside the repo leaves untracked files (R7).
- Docs could drift into full-tier files.

*Mitigation:*
- Catch only `ArcGISError`/`GEOSException` per site, and exit non-zero if
  any site failed.
- Refuse an `--out` equal to RUN_DIR, or a non-ignored `--out` inside the
  repo (`git check-ignore`).
- Docs go only to the screener README section, SITE_PLANNING.md,
  AGENTS_DESIGN.md and reviews/37.

*Gate:*
- A fetch failure exits 1, and a planner `ValueError` propagates.
- A tracked `--out` is refused.
- `git diff --name-only main...HEAD` holds no `program.md`,
  `STANDING_RISKS.md`, `CLAUDE.md`, `screener/autoresearch/PROGRAM.md` or
  root JSON.

**R27. Untrusted free text (visual.json, lead headlines, notes).**
Beyond owner names (R1), this text can carry markup, CSV formula prefixes
(`= + - @`) or very long strings.

*Mitigation:* Escape in HTML, cap the length, prefix formula-looking CSV
cells with `'`, and keep verdicts to an enum.

*Gate:*
- `=HYPERLINK("http://x")` is written as `'=HYPERLINK…` in
  `land_today.csv`.
- A 10 kB evidence string is truncated to the cap.

**R28. Viewer page size.**
- The reference page is 1.12 MB for about 86 plans, each embedded and
  rendered as a live inline thumbnail.
- A larger run gives a multi-MB page with hundreds of live SVG trees.

*Mitigation:*
- `MAX_PAGE_BYTES` (for example 15 MB): refuse above it, warn above
  about 5 MB.
- Strip whitespace from the SVGs and round coordinates.
- Lazy thumbnails.

*Gate:*
- An oversize synthetic set is refused, with nothing written.
- On the real run, the page size stays within 20% of 1,119,976 bytes for
  the same inputs.

## Standing risks that apply

- SR-1: no human-owned or immutable file is touched (root JSONs,
  `program.md`, the screener's own protected files).
- SR-2: by analogy, for ArcGIS where clauses (R12).
- SR-6: R8.
- SR-7: R11.
- SR-8: R1, R27.
- SR-9: R11.
- SR-10: R25.
- SR-11: R14.
- SR-15: by analogy, the screener evaluator boundary (R7).

## Gates checklist (the Decide phase ticks each one)

- [ ] **Protected files unchanged (R5, R7, SR-1).** The diff against main
      of the existing screener modules, `settings.toml`, `requirements.txt`,
      `autoresearch/` and root human-owned files is empty. The changed-file
      list is only the new modules, the template, the docs, the tests and
      reviews/37.
- [ ] **Suite passes offline (R8).** The full unittest suite passes with
      the proxy pointed at a dead port. The cache file count is unchanged
      and the runtime is within budget.
- [ ] **No owner data (R1).** The owner-guard tests pass on every artifact
      type. The static outFields/owner test passes. No hardcoded prototype
      parcel ids or owner prefix. A real-run normalised owner scan gives 0
      hits.
- [ ] **Not checked is never clean (R2, R9).** The fail-closed tests pass
      for DEM, frontage, neighbours, transmission, layers and the built
      check's records query. No `ACCESS_NONE` without a frontage state of
      none. No `raw_land` without a visual review.
- [ ] **Viewer safe (R3).** The adversarial SVG/JSON/template tests pass,
      the CSP tag is present, and the SVG output parses with ElementTree.
- [ ] **Parity recorded (R6).** An offline replay of all 33 sites gives
      identical JSON and SVG except listed deltas, with zero unexpected
      network calls. The table is in `02_decision_note.md`. The hermetic
      fixture test covers at least 2 reference sites.
- [ ] **Feasibility rule (R4).** The invariant holds on every record, the
      coverage-cap test passes, and the deltas (#9, #44, #124) are listed.
- [ ] **Pond reuse (R5).** The pond sizing test passes (#4: 6.5 ac-ft, 1
      pond). Variants derive from `sitefit.PROTOTYPES` and the
      private-access allow-list holds.
- [ ] **Joins and paths (R10–R12).** The run fingerprint, parcel_id joins,
      slugs, path containment, atomic writes and pid validation tests
      pass.
- [ ] **Geometry checks (R13, R16–R18, R20, R21).** The seam/contiguity,
      route, `check_plan`, GeometryCollection, DEM-key and buffer tests
      pass. Validator counts over the replays are recorded.
- [ ] **Politeness (R14).** `arcgis.cache_days` is unchanged after import,
      and the throttle and stop-after-failures tests pass.
- [ ] **Draft labels and decisions (R15).** `ZONING_DRAFT`, draft status,
      `JURISDICTION_DEFAULTED`, `ATL_PARKING_CODE`, the parking bands and
      `--trailer-ratio` consistency tests pass. SITE_PLANNING.md lists the
      open decisions.
- [ ] **Medium and low items.** Budget (R19), schema drift (R22),
      determinism (R23), degenerate geometry (R24), dependencies (R25),
      CLI exit codes and `--out` refusal (R26), CSV formula escaping
      (R27) and page size (R28) tests pass.
- [ ] **Baseline note (R7).** The PR body, the README and
      `02_decision_note.md` state the required human
      `--decide --new-baseline` and the expected unchanged score.
      `git status --porcelain --untracked-files=all -- screener` is empty
      after the commit.
- [ ] **No push and no PR merge** without operator approval.
