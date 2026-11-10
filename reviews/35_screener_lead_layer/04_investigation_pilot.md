# 35 — Investigation pilot (2026-11-10)

Two leads from the 2026-11-10 Fulton run were investigated by a
due-diligence agent each, following `screener/INVESTIGATE.md` and checked
by `screener/quotecheck.py`. The skeptic step was not run. `quotecheck`
and `INVESTIGATE.md` have NOT had an independent review yet.

## Results

| Lead | Grade | Time | Requests | Claims kept / dropped | Sections with a verdict |
|---|---|---|---|---|---|
| #6, 0 Irwin Rd, South Fulton, M-1, 47.5 ac | A | about 11 min | 29 + 8 searches | 25 / 0 | 4 of 5 |
| #44, 601 Senoia Rd, Fairburn, C-2, 13.5 ac | A | about 9 min | 27 + 5 searches | 11 / 6 | 1 of 5 |

- **#6 is probably already taken.** The M-1 zoning was granted on 24 March
  2026 by an ordinance that names this parcel id, for a 1.6 million SF
  project applied for by a developer acting as agent. The county
  development authority's release of 29 April 2026 gives an estimated
  closing in Q3 2026. No page shows the closing. The ordinance names seven
  parcel ids, and all seven are in the ranked list: #6 (A), #12 (A), #10
  (B), #32 (B), #60 (B), #177 (B) and #114 (C). Two of the run's 14 grade
  A leads are therefore one site that a developer has already entitled.
- **#44's "likely" zoning path rests on a superseded label.** The future
  land use the screener read is a 2021-plan category that the plan pairs
  with O&I, M-1 and M-2, not C-2. A 2025 plan has since been adopted; its
  file is 158 MB and could not be checked at the time (the size cap has
  since been raised). Four of five sections stayed `unknown`.

## What stopped a polite, plain fetch

- Municode: the zoning text of both cities. JavaScript-only; its API
  answers a bot challenge (403). No use table was read for either lead.
- Georgia Secretary of State: 403 even for robots.txt. No owner record
  was read for either lead.
- ArcGIS Online hosts answer robots.txt with 403, so `quotecheck` refuses
  layers the screener itself reads.
- Georgia DCA: robots.txt disallows everything.

## Slips by the agents (both reported by the agent itself)

- Each fetched one page in the same command as that host's robots.txt,
  before reading it. One of those hosts disallowed everything; the page
  was discarded and nothing from it was used.
- The first two requests to a city site ignored its 10-second
  Crawl-delay.

## Changes made after the pilot

- `quotecheck`: honours `Crawl-delay` (a host asking for more than 30 s
  is not fetched); the page size cap went from 25 MB to 200 MB.
- `INVESTIGATE.md`: read robots.txt first, as its own step; a "known
  walls" list; the budget counts every request; negative verdicts need a
  quoted page; a `signs_developer_pursuing` market verdict; water and
  sewer stated separately; submit only claims expected to pass.

## What this says about the design

- The most useful finding (a lead already in a developer's hands) came
  from city agenda files and a development-authority release, which a
  plain fetch can read. It took about 11 minutes.
- The zoning text and the owner record, the two things the design most
  wanted, cannot be read under these rules in these cities. They stay a
  person's job unless the operator decides otherwise for specific hosts.
- Open for the operator: whether a robots.txt that answers 403 on a
  public ArcGIS host should be treated as the root system's decision D1
  treats it (`reviews/29_operator_decisions`).
