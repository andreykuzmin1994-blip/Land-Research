# 16 — F9 SSURGO soils: reviewer decision (Tier 1)

Fresh reviewer. I read `15_f9_code_writer_response.md`, the diff of `c34be79`,
`soils.py`, `run.attach_soils`, the report and settings changes and the new
tests. I ran the suite, then re-ran the access check and a live probe myself.

## Access check (re-done 2026-11-10)

`https://sdmdataaccess.sc.egov.usda.gov/robots.txt` returns HTTP 404, so no
crawl rule applies. NRCS publishes `post.rest` as a public service for ad hoc
queries, with the 100,000-row cap. I found no term that forbids automated use.
The live check used `soils.check_parcel` with the screener's own helper, so the
User-Agent, cache and backoff are the same as in a run. A Fulton box and a New
Orleans box both came back checked with sane values, at about 100% coverage.
The extra `f=json` field that `request_json` adds is ignored by SDA.

## Verdict: GO, with the fix below

- **Leak rule holds.** `attach_soils` runs after the sort, the fit and the
  pond, and after `snapshot.take`. The spies on rank, scoring, site_rejects and
  flags pass with slope on and off. The patched-off run gives the same order,
  scores and flags, and the same requests apart from SDA.
- **Three states stay apart**: a value, `none rated`, and `not checked: <why>`
  with the share columns empty. Bad answers are never cached.
- **The SQL guard** (a regex on the WKT before formatting) admits only numbers
  and brackets.
- **Limits**: at most 3 workers, `max_parcels` caps by rank, and asking stops
  after 5 service failures in a row.
- No forbidden file, root file or dependency was touched.

## Fixed in this step

1. **The brief said "mostly B" for a group that covered a minority of the
   parcel.** In the live Fulton probe, 62% of the parcel was unrated urban land
   and B covered 30%, yet the brief read "mostly B (30% ...)". The brief now
   says "mostly" only above 50%, and "largest rated group" otherwise.
   - The unrated tail now uses readable labels (soil group, drainage, dwellings
     rating) instead of the internal keys.
   - Added a test and a README note.

## Noted, not changed

- The CSV `soil_hsg` still gives the largest rated group even when it covers a
  minority. The shares in the CSV are of all mapped soil, so unrated area
  counts as not limited. The brief gives the unrated share, and the README says
  the shares are of the mapped soil.
- SDA's `{}` reply (no map unit matched, e.g. open water) reads "not checked",
  which is what the brief asked for.
