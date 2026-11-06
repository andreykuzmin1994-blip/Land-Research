# Land Site Selector

Autonomous research agent for industrial real estate land sourcing.
Adapted from the [Karpathy AutoResearch](https://github.com/karpathy/autoresearch) paradigm.

## What This Does

This repository contains the specifications and (eventually) implementation for an autonomous agent that continuously discovers, screens, scores, and ranks land parcels for industrial real estate investment across target markets. The agent runs overnight, evaluates parcels against a configurable parameter stack, surfaces actionable opportunities, and produces investment thesis writeups for each qualified site.

The agent supports five investment strategies (BTS development, spec development, land banking, ground lease, land flip) and tags each qualifying parcel with the strategies it best fits. The team decides which strategy to pursue.

## Status

**2026-10-28: simpler path added — `screener/`.** A plain, database-free land
screen built on free public data (county GIS, FEMA, USFWS, USGS, EPA, GDOT)
that ranks buyable, buildable Fulton parcels and writes one-page briefs.
It has its own small AutoResearch loop scored against land developers
actually bought (`screener/autoresearch/PROGRAM.md`). Start there:
`screener/README.md`. Everything below describes the original system,
which is unchanged.

Phases 1–10 of `BUILD_PHASES.md` are shipped. The autonomous experiment loop runs end-to-end against Fulton County, with the full Karpathy-pattern infrastructure: setup-phase verifier, evaluator, append-only TSV experiment log, keep-or-revert decision logic, halt sentinel, advisory locking. Atlanta is the only configured market; Phase 11+ adds the remaining counties.

2026-07-07 restructure (see `reviews/14_streamlining_review/`):
- `research.py` was split — the experiment loop now lives in `runner.py`, CoStar ETL in `costar_ingest.py`, rendering in `reporting.py` (all immutable during a run); `research.py` holds only the agent's experiment surface.
- The metric is **run-scoped** (`prepare-mutation`): `parcel_scores` rows carry `run_tag` + `experiment_id`, the metric counts only the active run's rows, and the runner purges a discarded experiment's rows so the data ratchet mirrors the git ratchet. Pre-mutation metric values are not comparable to post-mutation values; the next run starts fresh.
- The review process is **tiered** (`STANDING_RISKS.md`): tests+CI for low-risk changes, one independent fresh-context reviewer for ordinary logic, the full three-agent workflow for metric/contract-touching work.

2026-11-06: flood (H4) and wetlands (H6) are built (`hazards.py`,
`make hazards-import`, `reviews/26_flood_wetlands/`) but **inert**: they
need a new table that only a human adds to `prepare.py` (the proposal is
`reviews/26_flood_wetlands/01_prepare_mutation_proposal.md`). Until then H4/H6
stay data_gap passes and every score is unchanged. See "Flood and wetlands".

2026-11-06: site-fit adjacency buffers (residential, other-district, ROW) are
built (`adjacency.py`, `make adjacency-import`, `reviews/27_site_fit_buffers/`)
but **inert**: they need a new table that only a human adds to `prepare.py`
(`reviews/27_site_fit_buffers/01_prepare_mutation_proposal.md`) and, for
residential buffers, a human-written neighbour-class table in
`zoning_rules.json`. Until then every projection and score is unchanged. See
"Adjacency buffers".

For live repo state, run `make status` (setup checks + last TSV rows) — prose snapshots of state in documents go stale; the command does not.

## Quick Start (Operator)

**Home Windows PC (recommended for long runs):** see `home_machine/README.md` —
fill in `settings.txt`, run `Install.bat` once, then double-click
`Start Land Research.bat`.

This codebase is operated through `make`. Open the repo in a Codespace and run:

```bash
make daily              # ONE COMMAND: cuts/resumes today's autoresearch branch,
                        # verifies Supabase, kicks the loop in a detached tmux
                        # session, prints attach + tail commands. Refuses
                        # (before starting tmux) until the run has a baseline.

# New run only (a new branch/TAG — make daily starts one each calendar day;
# pass TAG=<existing tag> to keep working on an earlier run):
make baseline           # Setup Step 5: verify_setup + loop lock, then record the
                        # run's baseline row (refuses a second one for the run).
                        # Review that row (Setup Step 6), then run make daily
                        # (or make loop-bg) again.

# In a second terminal:
make tail               # live-stream experiment_log.tsv as rows land
make status             # verify_setup + last 10 TSV rows
make db-stats           # per-table row counts (parcels, parcel_scores, etc.)

# To stop the loop cleanly:
make halt               # creates .halt sentinel; loop exits on next iteration

# To reattach to the running loop:
make loop-attach        # tmux attach -t loop  (Ctrl-B d to detach again)
```

**One-time setup per Codespace** (the devcontainer handles this for new Codespaces):

1. Add `DATABASE_URL` to your User-level Codespaces secrets at https://github.com/settings/codespaces — Supabase Session pooler DSN, format `postgresql://postgres.<ref>:<URL_ENCODED_PASSWORD>@aws-<n>-<region>.pooler.supabase.com:5432/postgres`. Grant the secret access to this repo.
2. Open the Codespace. The devcontainer's `post-start.sh` materializes `.env` from the secret automatically.
3. Verify: `make db-check` should print PostGIS version + `actionable_pipeline_count: 0`.

**Overnight runs and the Codespaces idle timeout**: GitHub stops idle
Codespaces — by default 30 minutes after the last connection, 240 minutes
maximum (Settings → Codespaces → default idle timeout, or per-machine
`gh codespace edit --idle-timeout 240m`). A detached tmux session does
NOT count as activity, so "kick the loop and close the laptop" yields at
most one idle-timeout window of experiments (~2 at 90 minutes each), not
a true overnight run. For real overnight autonomy, keep a terminal
attached, or run the loop on a durable host (the DigitalOcean droplet
already contemplated in the cost table — the repo is a plain git clone +
`.env` away anywhere). `make loop-bg` prints this warning when it detects
a Codespace.

**Baseline confirmation (Setup Step 6)**: `make loop`, `make loop-bg` and
`make daily` only iterate on a run that already has its `baseline` row;
otherwise they stop and point at `make baseline`. `CONFIRM=1` on the make
command line (e.g. `make loop MAX=2 CONFIRM=1`) lets the loop bootstrap the
baseline and start iterating without that review. It is the human's
consent for that one invocation: the agent never supplies it, and a
`CONFIRM` exported in the environment is ignored. Experiments (the
baseline included) are interrupted at 90 minutes and recorded as `timeout`.

**Karpathy iteration loop** (what the agent does between `make loop` runs):

1. Read `experiment_log.tsv`. The most recent `baseline` or `keep` row is the prior anchor.
2. Form a hypothesis. Edit `research.py` (the only file the agent edits). One focused change.
3. `git commit -m "exp: <description>"` on the active `autoresearch/<tag>` branch.
4. `make loop MAX=1` — runs one full cycle, appends a row to `experiment_log.tsv` with the keep-or-revert decision. (Needs the run's confirmed baseline row — see above.)
5. Read the new row. If `status=keep`, branch advances. If `status=discard`, `git reset --hard HEAD~1`. If `status=crash` or `timeout`, diagnose and retry.

Per `AUTORESEARCH_MECHANICS.md` "The Experiment Loop", the agent (Claude Code) drives the iteration; `make loop` provides the evaluator + decision recording. Auto-revert is intentionally NOT in `make loop` — that's the agent's responsibility (R-723 / D1).

`make help` shows every available target with descriptions.

## Zoning import (operator, between runs)

`make zoning-import` (`zoning_import.py`, `reviews/21_zoning/`) writes each
Fulton parcel's zoning district into `parcels.zoning` /
`parcels.zoning_description`. S9 (entitlement) and the H3 zoning flag read
it. Run it **between runs only**, after a new run's branch is cut and
before its baseline: it refuses (exit 3) once the TSV or the database's
experiment mirror shows the run's baseline. Re-run it at every new run so
rezonings are picked up.

One-time setup (sources.json is human-only):

1. Open `reviews/21_zoning/sources_json_snippet.json`.
2. Copy its `"zoning_sources": [...]` list into `sources.json` as a new
   key **inside** `county_parcel_data.fulton_ga` (next to
   `field_mapping`), not directly under `county_parcel_data`. The order of
   the list is the precedence order (for South Fulton parcels, the city
   layer before Fulton layer 34; Fulton Industrial District parcels are
   served by layer 34 only, exactly as in `screener/fulton.py`). Commit it
   like any `sources.json` change.

Then:

```bash
make zoning-import          # off a run branch, add ARGS=--no-open-run-check only if
                            # the newest run in the database is truly over
```

What it does: finds each parcel's city from its TaxDist on the Fulton
parcel layer, fetches each zoning source's features around the parcels
(1 request/second per host, no cache), and assigns a district by parcel id
(join) or largest polygon overlap in PostGIS (overlay). It updates only
those two columns, in one transaction; a parcel without a match keeps its
value; a source that fails or comes back truncated is skipped whole.
Future-land-use layers are never written as zoning. The console shows
counts only (per city, per source, per entitlement class, changed /
unchanged / unmatched).

Exit codes: 0 imported, 1 refused (sources.json key missing or malformed,
Fulton parcel layer down), 2 no database / database error, 3 a run has its
baseline, 4 imported **but at least one zoning source failed** (named on
the last line; that source's parcels kept their previous zoning, the other
sources' rows were written). Treat 4 as "re-run when the source is back",
not as success.

The overlay counts each district once even where a city layer has
overlapping polygons of the same district. Parcels the import could not
match (and parcels never imported) carry the H3 text "zoning not imported
or not matched by any zoning source". The first discovery after this
change opens one new H3 `data_gap` flag per parcel (the text changed);
the old "H3 zoning unjoined" flags stay open until resolved by hand. They
do not feed the metric.

**Zoning rules.** `zoning_rules.json` holds, per jurisdiction, a `status`
(`draft` or `verified`) and, per district, the warehouse/distribution use
status with citations (built from `reviews/21_zoning/01_zoning_rules_DRAFT.md`).
Everything ships as `draft`, and S9 ignores draft rules (it stays at its
old stub value), so importing zoning does not change the metric. To use a
jurisdiction's rules: check them against the official ordinance, set
`status` to `verified` with `verified_by` / `verified_on`, choose its
`unlisted_districts` (`not_permitted` or `unknown`), update the
all-draft expectation in `tests/test_zoning.py`, and commit **between
runs**; then start a new run and baseline (the change moves S9 and the
metric). The runner refuses to run an experiment while
`zoning_rules.json` differs from the committed version (git HEAD), so an
uncommitted edit fails loud instead of becoming a silent baseline.

Verification checklist, per jurisdiction, before flipping it to
`verified`:

- every district's warehouse/distribution status and citation against
  the current official ordinance;
- `unlisted_districts`;
- Atlanta I-1/I-2: the BeltLine 500 ft exception (Sec. 16-36.007) ships
  as `location_conditions` effect `ignored`, which the draft never stated;
  choose `ignored` or `downgrade_to_conditional`;
- Atlanta `-C` parcels (conditions of zoning, e.g. `I-1-C`) score as their
  base district (by right = S9 10 once verified); the draft says their
  site conditions may override the defaults; check those parcels by hand
  or accept the default knowingly;
- South Fulton: the county `M-1A` -> `M-1` conversion exists only on the
  `fid` entry; a South Fulton parcel carrying `M-1A` from Fulton layer 34
  reads `unknown` (S9 stub). None in today's data.

## Building projections (site fit)

`site_fit.py` (`reviews/22_site_fit/`) projects, per parcel, the largest
building of each of the operator's prototypes that fits:
`building_prototypes.json` holds the cross-dock (1,000 x 520 ft, 40 ft
clear, docks both sides), front-load and rear-load (640 x 260 ft, 36 ft
clear, docks one side) prototypes, each with a 130 ft truck court and 55 ft
trailer parking per dock side, 12 ft dock spacing and 60 ft end clearance
(the operator's SiteFit tool defaults). It is human-owned, frozen at load,
and checked against git HEAD by the runner, like `zoning_rules.json`.

How a projection is made: the parcel is buffered inward in PostGIS (SRID
2240, feet) by one uniform setback, the largest front/side/rear setback of
its zoning district in `zoning_rules.json`, or 25 ft when no district rule
applies (frontage is unknown). Each prototype keeps its depth and shrinks
its length in 40 ft steps (down to 600 ft cross-dock, 400 ft front/rear)
until its whole envelope (building + courts + trailer parking) fits at
some orientation; a district's lot coverage cap steps it down further.
Docks and trailer stalls follow SiteFit's formulas. The fit rule is
stricter than SiteFit's own check (which lets truck courts and trailer
parking sit in the setback), so a study refined in SiteFit may hold a
longer building. Not modelled, and said so on every projection:
adjacency buffers (residential or other-district neighbours; each buffer
the district's rules list is named), height (the clear height is stated,
with the district maximum when the rules have one), stormwater detention
(SiteFit's default pond is 400 x 200 ft, about 1.8 ac, and is not
placed), car parking, truck drives, flood (H4) and wetlands (H6). A
setback that is the 25 ft default is never a BTS input, even in a verified
jurisdiction ("verified jurisdiction, default setback": BTS N/A). Very
complex buffered outlines are simplified in PostGIS inside a slightly
deeper buffer (stated on the projection); beyond 600 vertices the
projection is an error rather than a slow search.

Where it shows: the snapshot's **Development Potential** section
(buildable area, max building SF, a table per prototype) and one line per
memo highlight. Where it counts: BTS strategy fit uses "projected building
>= 150K SF" instead of the 8.61-acre proxy, but **only** for parcels whose
zoning jurisdiction is `verified`; while all rules are draft the metric is
unchanged. After verifying a jurisdiction, or after editing
`building_prototypes.json` between runs, start a new run and baseline.

Operator command (read-only on the database; prints no owner names or
addresses):

```bash
make site-fit ARGS='--parcel "fulton-14 0144  LL0215"'   # one parcel
make site-fit ARGS='--top 10'                            # the 10 highest composites
make site-fit ARGS='--top 10 --export'                   # + SiteFit study files
```

`--export` writes `site_fit_studies/<parcel>-<hash>.sitefit.json`
(gitignored) holding the parcel boundary (largest part), the applied
setback on every edge and the fitted building, to open in SiteFit
(Studies > Open file…) and refine by hand. Keep the SiteFit tool file itself out of the repo
(`SiteFit.html` is gitignored).

## Flood and wetlands (H4/H6, operator, between runs)

`hazards.py` and `hazards_import.py` (`reviews/26_flood_wetlands/`)
implement program.md's H4 ("not in FEMA Zone A or AE"; Zone AE that lies
wholly within a LOMR passes with a review flag, while a LOMR elsewhere on
the lot relaxes nothing; a floodway always blocks;
LOMA is never checked) and H6 ("no NWI-mapped wetlands covering more than
20% of the parcel", from `parameters.json`), mirroring the operator's
SiteFit categories (floodway, SFHA, 0.2% zone). Shares are computed in
PostGIS (SRID 2240, feet) over the same parcel geometry as site fit. They
are enforced at scoring, through gate 4: a block or fail makes the parcel
`FAIL:deal_killer` with blocker text `H4: ...` / `H6: ...`. Once enabled,
`runner.evaluate` re-checks every parcel the metric would count, outside
the agent sandbox, and discards an experiment that counted an H4/H6
failure. Review flags
use `flagged_items.flag_type = 'review'`, which no gate reads. Site fit
then deducts the SFHA (floodway included) and NWI wetland area from the
buildable area; the 0.2% zone, fill and stream/state-waters buffers are
not considered (stated on every projection).

Nothing of this is active until a human:

1. applies `reviews/26_flood_wetlands/01_prepare_mutation_proposal.md` to
   `prepare.py` between runs (`make db-check`, which runs `python prepare.py`, then creates `parcel_hazards`);
2. downloads, in a browser, the FEMA NFHL county package for Fulton
   (13121C, Map Service Center) and the USFWS NWI Georgia shapefile, and
   unpacks them as `hazards_inbox/nfhl/S_FLD_HAZ_AR.*` (+ `S_LOMR.*`) and
   `hazards_inbox/nwi/<name>.shp` (+ .dbf, .prj; if the state package
   unpacks several shapefiles, the `*_Wetlands.shp` one is read);
3. runs `make hazards-import` (counts only on the console; exit 4 names a
   failed source and keeps its previous data), then starts a new run and
   baseline: the first populated import is a metric change.

`ARGS='--mode rest'` reads the public NFHL/NWI services instead, through
the scraper's robots.txt policy; on 2026-11-06 both hosts' robots.txt deny
those queries, so it fails both sources without a data request. The data
is screening only: not an official flood determination (the effective FIRM
governs) and not a jurisdictional wetland delineation.

## Adjacency buffers (site fit, operator, between runs)

`adjacency.py` and `adjacency_import.py` (`reviews/27_site_fit_buffers/`)
apply the zoning buffers each district's rules list (South Fulton / FID
residential side 50 / rear 100 ft plus a 10 ft improvement setback, Union
City 75 ft plus a 20 ft setback from it, Fairburn 50 ft from residential and
from any other district, Atlanta 20 ft transitional yards) per side, where
the neighbour's zoning is known. The import reads the zoning layers already
registered in `sources.json` (robots.txt permitting, 1 request/second per
host) and stores, per candidate parcel, which boundary parts face which raw
neighbour district code, or that nothing is known there. Site fit then
buffers each fresh, applicable part by max(uniform setback, buffer); it
never relaxes the uniform setback. Frontage is unknown, so the rear value is
applied to every abutting side; side parts with no zoning polygon read
"adjacency unknown" (never ROW, never "no neighbour"); every projection
says what was applied and what was not.

Nothing of this is active until a human:

1. applies `reviews/27_site_fit_buffers/01_prepare_mutation_proposal.md` to
   `prepare.py` between runs (`make db-check` then creates `parcel_adjacency`);
2. for residential/agricultural buffers, adds a cited `neighbour_classes`
   table per jurisdiction to `zoning_rules.json` (same proposal document);
3. runs `make adjacency-import` (counts only; exit 4 names a failed source,
   whose territory then reads as adjacency unknown), then, once any
   jurisdiction is verified, starts a new run and baseline.

## Zoning-code scraper (operator, between runs)

`make rules-scrape` (`rules_scrape.py`, `reviews/23_rules_scraper/`) reads
the zoning-code pages listed in `rules_sources.json` and drafts a
**candidate** rules file. It never writes `zoning_rules.json` and it is no
scoring input. What a human does with it:

1. First, once per host: a human reads the host's terms of use in a
   browser and fills `terms_reviewed_by` / `terms_reviewed_on` in
   `rules_sources.json` (commit the edit). Then run it between runs:
   `make rules-scrape`. Until a host's terms are reviewed it is skipped as
   unreachable; `--allow-unreviewed-terms` overrides that and is an
   exception for a one-off run, never a default. It writes, under the
   gitignored `rules_out/`,
   `zoning_rules.CANDIDATE.json` (every jurisdiction `draft`, every value
   with a cite, a short verbatim quote, `source_class` official or mirror
   and the source page's SHA-256) and `rules_diff_report.md` / `.json`
   (per jurisdiction, district and field: same, same but mirror vs mirror
   (not independent), same value with a new cite, changed, added, removed
   (section read, value not extracted), not found in source, source
   unreachable, needs extraction, ...). The candidate is a per-value draft
   to compare, not a drop-in replacement for `zoning_rules.json`. The
   console shows counts only.
2. Read the diff. Values marked needs_review carry a footnote,
   cross-reference, "whichever is greater", adjacency or limiting wording
   ("for initial ...", "may be reduced", a range), a section note about
   the field, a quote spanning several source lines or a unit
   conversion: read the cited section yourself. Agreement with a mirror is
   not verification.
3. Edit `zoning_rules.json` **by hand**, per value, between runs, and
   commit. Flipping a jurisdiction to `verified` is a human act; start a
   new run and baseline afterwards (`reviews/21_zoning`).

Other modes: `--offline` replays `rules_cache/` (no network; refuses a
cache entry whose bytes changed); `make rules-watch` re-fetches and
reports per source section, newest "Ord. No." date and GIS layer edits
whether each is changed / unchanged / unknown (never applies anything;
GIS checks are skipped while `.experiment_loop.lock` exists);
`--assisted FILE` reads hand-written extraction records that go through
the same verbatim-quote verifier; `--llm` extracts prose sections with the
Claude CLI (`claude -p`, no tools, fixed JSON schema, empty working
directory; needs `ANTHROPIC_API_KEY`, as it runs with `--bare`), otherwise
prose sections are reported "needs extraction"; `--flags` (read-only
database, refuses after a run's baseline like `make zoning-import`) lists
parcels with conditions of zoning on file (an Atlanta `-C` district or an
approved case polygon covering at least 5% of the parcel or of the case;
smaller overlaps are listed apart as adjacent cases), counts on the
console and parcel ids only in `rules_out/conditions_flags.json` (with
`--flags`, `--report` must point outside the repository or under
`rules_out/`). Pull those conditions by hand. Inside the repository the
tool writes only under `rules_out/`, `rules_cache/` and
`reviews/23_rules_scraper/`.

**Terms block and local official files** (`reviews/24_rules_local_files/`).
Zoneomics' terms of use prohibit automated access, so `zoneomics.com` and
every subdomain are blocked in the code (`TERMS_BLOCKED_DOMAINS`): never
requested (not even robots.txt or a redirect target), never replayed from
`rules_cache/`, never parsed. A registry source there is skipped with
"terms prohibit automated access" (no exit 4); nothing in the registry or
on the command line lifts the block. Official ordinance text you obtained
yourself (a city clerk's copy, or a Municode export you downloaded as a
human) goes into the gitignored `rules_inbox/` folder and is registered as
`"kind": "local_file"` (relative `path`, `format` pdf/html/txt,
`source_class` official, `title`, `obtained_from`, `obtained_on`, `sha256`
pinned or `"record on first use"`). The tool reads it with no network,
refuses symlinks, hard links, non-regular or oversized files and any file
that references a mirror host (a "Save As" of a mirror page), and cites the
document (`official: <title>, <section> (obtained from ..., <date>)`),
never a file path. A changed file is reported "changed (operator replaced
file)" by `make rules-watch`; a pinned sha256 that no longer matches is
refused (exit 4). Scanned PDFs without a text layer are reported "no text
layer (scanned PDF): ask for a text PDF; values cannot be verified" (exit 4;
an `--assisted` record for such a section is rejected and counted, because
there is no text to check its quote against). A text PDF or text file
prints a two-column table as label, spaces, value; `label_value` reads that
too. A cut section whose parser reads no value at all exits 4. Before `--llm` on an export,
check the export's terms. The registry edits to make are in
`reviews/24_rules_local_files/rules_sources_patch.json`.

The fetcher never contacts Municode (hard-coded denylist, also for
redirects), honours robots.txt for its own token and for AI-agent tokens,
sends one fixed User-Agent, waits 1 s between requests to a host (longer
when Crawl-delay asks) and treats any challenge or WAF page as
unreachable without retrying (a 429 or 5xx with a Retry-After above 30 s
too). The watch keeps the previous baseline for anything it could not
observe and reports a section whose markers vanished as "changed (section
lost)". Exit codes: 0 done, 1 refused, 2 database error (`--flags`), 3 run
has its baseline (`--flags`), 4 done but a source was unreachable or an
enabled section was not found, 5 request budget spent.

**Prerequisite**: PDF sources need `pdftotext` (poppler-utils, e.g.
`sudo apt-get install -y poppler-utils`); without it PDF sections are
reported "pdf text unavailable". No Python package is added
(`requirements.txt` is unchanged).

## Repository Structure

```
land-site-selector/
├── CLAUDE.md                          — Contract card: invariants + orientation tiers (agents read FIRST)
├── START_HERE.md                      — Full orientation chain (required before code/loop work)
├── README.md                          — This file
├── AUTORESEARCH_MECHANICS.md          — CANONICAL: how the Karpathy pattern is implemented
├── program.md                         — The agent's autonomous loop instructions
├── appendix_a_county_connectors.md    — County data source specs, tiered review workflow, harness design
├── STANDING_RISKS.md                  — Recurring risk checklist + review tiers (cited by ID in reviews)
├── COSTAR_INGESTION_CONTRACT.md       — How CoStar exports feed the agent
├── COSTAR_EXPORTS_README.md           — Operator guide for the CoStar saved-search setup
├── STORAGE_ARCHITECTURE.md            — Postgres + PostGIS schema decisions
├── BUILD_PHASES.md                    — Implementation roadmap
├── parameters.json                    — Scoring weights and filter thresholds (IMMUTABLE during run)
├── sources.json                       — Registry of data source URLs (LOCKED — agent never writes)
├── zoning_rules.json                  — Human-owned zoning facts per jurisdiction/district (agent never writes)
├── building_prototypes.json           — Human-owned building prototypes from the operator's SiteFit tool (agent never writes)
├── rules_sources.json                 — Human-owned source registry of the zoning-code scraper (agent never writes)
├── rules_inbox/                       — gitignored: official ordinance files the operator obtained by hand (local_file sources)
├── connector_registry.json            — Harness-only config overlay (test bboxes, expected extents)
├── prepare.py                         — IMMUTABLE: metric calculation, DDL, frozen parameters
├── research.py                        — Agent sandbox — the ONLY file the agent modifies in a run
├── runner.py                          — IMMUTABLE during run: experiment loop, setup checks, TSV I/O
├── costar_ingest.py                   — IMMUTABLE during run: CoStar export ETL
├── reporting.py                       — Snapshot + strategy memo rendering
├── pipeline_common.py                 — Shared paths/helpers/SQL for the pipeline modules
├── costar_import.py                   — IMMUTABLE during run: CoStar Excel import (operator, between runs)
├── zoning.py                          — IMMUTABLE during run: loads + freezes zoning_rules.json, classifies districts
├── zoning_import.py                   — IMMUTABLE during run: writes parcels.zoning (operator, between runs)
├── site_fit.py                        — IMMUTABLE during run: loads + freezes building_prototypes.json, projects buildings
├── rules_scrape.py                    — IMMUTABLE during run: zoning-code scraper, writes a CANDIDATE only (operator, between runs)
├── hazards.py                         — IMMUTABLE during run: FEMA NFHL / USFWS NWI facts and the H4/H6 decision (inert until the parcel_hazards proposal is applied)
├── hazards_import.py                  — IMMUTABLE during run: writes parcel_hazards from hazards_inbox/ files (operator, between runs)
├── hazards_inbox/                     — gitignored: NFHL county package + NWI state shapefile the operator downloads by hand
├── adjacency.py                       — IMMUTABLE during run: neighbour adjacency facts, buffer-key allow-list, per-side buffer plan (inert until the parcel_adjacency proposal is applied)
├── adjacency_import.py                — IMMUTABLE during run: writes parcel_adjacency from the registered zoning layers (operator, between runs)
├── connector_harness.py               — Connector validation framework
├── cli.py                             — Operator CLI (argparse; --json output)
├── Makefile                           — Operator targets (make help)
├── data/                              — Bundled reference data (OZ tract stub, etc.)
├── tests/                             — Offline suite (`make tests`, ~1s) + fixtures
├── reviews/                           — Review artifacts per change (decision notes; historical 3-agent docs)
├── .devcontainer/                     — Codespaces config: secret -> .env hydration
├── .githooks/ + .github/workflows/    — Credential guard, offline+live CI, harness CI
├── experiment_log.tsv                 — AutoResearch experiment log (UNTRACKED, runtime-only)
└── snapshots/ rankings/ harness_reports/ sources/
                                       — Generated runtime artifacts (gitignored, created on demand)
```

## For Claude Code (and any AI coding agent)

**Read `CLAUDE.md` first** — a one-screen contract card with the always-on file mutability invariants and a tier table telling you how much orientation the session needs. Read-only, diagnostic, and docs-only sessions proceed on the card alone (light orientation). Any session touching code, config, tests, the canonical spec, or the experiment loop must then complete the 6-step chain in `START_HERE.md` before acting — skipping it silently corrupts the experimental log.

## For Humans

`CLAUDE.md` is the one-screen version of the rules everything below justifies — start there. Then read for your need; new to the repo entirely, read the table top to bottom (it doubles as the onboarding order):

| If you want to… | Read |
|-----------------|------|
| Operate the loop day-to-day | This README § Quick Start, then `make help` |
| Understand how the Karpathy pattern is implemented — canonical, wins all conflicts | `AUTORESEARCH_MECHANICS.md` |
| See what the agent optimizes, how parcels are scored, and redirect it between runs | `program.md` |
| Add a county, fix a connector, or read the coding workflow spec | `appendix_a_county_connectors.md` |
| Review a code change (change tiers + recurring risk checklist) | `STANDING_RISKS.md` |
| Understand the database schema and storage decisions | `STORAGE_ARCHITECTURE.md` |
| Set up or debug CoStar exports | `COSTAR_EXPORTS_README.md` (ops) + `COSTAR_INGESTION_CONTRACT.md` (spec) |
| See the roadmap and how each phase was built and reviewed | `BUILD_PHASES.md` + `reviews/` |

## The Tiered Review Workflow

Code changes are reviewed by blast radius (full definition:
`STANDING_RISKS.md` § "Change tiers"; process spec:
`appendix_a_county_connectors.md` → "Coding Workflow"):

- **Tier 0** — docs, config, stubs, ops tooling: tests + CI only.
- **Tier 1** — ordinary `research.py` logic: ONE independent fresh-context reviewer + a short decision note.
- **Tier 2** — metric layer, runner decision logic, harness internals, credentials, external integrations: the full three-agent adversarial workflow (risk reviewer → code writer → reviewer-implementer with sole commit authority), each role in a genuinely separate context on the strongest available model.

The recurring risk checklist all tiers verify against lives in `STANDING_RISKS.md` (SR-1 … SR-15) — reviews cite IDs instead of restating it.

## Testing

**Offline suites** — no network, secrets or database (SR-6). Both run on
every push and pull request (`.github/workflows/offline-tests.yml`):

```bash
python -m unittest discover -s tests                 # root system (`make tests`)
python -m unittest discover -s screener/tests -t .   # screener/ (pip install -r screener/requirements.txt)
```

**Live PostGIS smoke test** — the offline suite never executes SQL;
`tests/test_postgis_smoke.py` runs the discovery UPSERT and the metric
query against real PostGIS (in CI: `.github/workflows/discovery-fulton.yml`).
It writes rows, so point it only at a throwaway local container, never at
Supabase: it refuses a non-local `DATABASE_URL` unless
`POSTGIS_SMOKE_ALLOW_NONLOCAL=1`. Pass the DSN inline; don't put it in `.env`.

```bash
docker run -d --rm --name lr-smoke -e POSTGRES_USER=lr -e POSTGRES_PASSWORD=lr_pw \
  -e POSTGRES_DB=lr_test -p 127.0.0.1:55432:5432 postgis/postgis:16-3.4
docker exec lr-smoke pg_isready -h 127.0.0.1 -U lr -d lr_test   # repeat until "accepting connections"
DATABASE_URL=postgresql://lr:lr_pw@localhost:55432/lr_test python tests/test_postgis_smoke.py
docker stop lr-smoke
```

**Live connector harness** — queries Fulton's public ArcGIS server
(rate-limited, SR-11) and writes a redacted report to `harness_reports/`
(gitignored). Design: `appendix_a_county_connectors.md` § "Connector Test
Harness"; in CI: `.github/workflows/harness-fulton.yml`.

```bash
python connector_harness.py --county fulton --quick
```

**Screener live trial** — downloads free public map data into
`screener/cache/` and writes `screener/output/` (both gitignored); see
`screener/README.md`. The two `evaluate` calls are read-only; `--decide`,
`--test` and `build_eval` belong to the loop and its humans
(`screener/autoresearch/PROGRAM.md`).

```bash
python -m screener.run --limit 200 --refresh
python -m screener.autoresearch.evaluate             # dev score
python -m screener.autoresearch.evaluate --control   # what random ranking scores
```

## Operating Cost (Estimated)

| Component | Cost |
|-----------|------|
| Infrastructure (Supabase free tier or DigitalOcean droplet) | $0–25/month |
| Claude API usage (absorbed by Max subscription) | $0/month |
| Data subscriptions (CoStar already in firm's stack, county APIs free) | $0/month |
| **Total** | **$0–25/month** |

## Target Markets

- **Tier 1**: Atlanta, Dallas-Fort Worth, Houston, Chicago
- **Tier 2 (Sun Belt secondaries)**: Nashville, Charlotte, Raleigh-Durham, Jacksonville, San Antonio, Columbus OH, Memphis
- **Future expansion**: Orlando, Lehigh Valley PA (NJ blocked by Daniel's Law without third-party data subscription)

Initial build targets Atlanta only.

## Key Decisions Made

- **Storage**: Postgres + PostGIS (Supabase free tier acceptable for initial build)
- **Coding workflow**: Tiered review (`STANDING_RISKS.md`); three-agent team at Tier 2, strongest available model
- **Data approach**: API-first (Approach 3) with AI-assisted fallback (Approach 2)
- **CoStar**: Manual scheduled export ingestion, no scraping (legal risk)
- **Primary metric**: `actionable_pipeline_count` (not just qualified — must pass actionability screen)
- **Strategy tagging**: Every qualifying parcel tagged with which of the 5 strategies it fits
- **Investment thesis**: Required for every actionable parcel, written in narrative form
- **Strategy memo**: Generated per market per cycle, explains agent's thought process

## License

Internal use only.
