# Your decisions

`decisions.csv` (created on the first run, kept out of git) records what
you decided about parcels. Each run reads it:

```csv
parcel_id,decision,date,note
09F030000140097,pursue,2026-11-02,called owner - open to offers
07 290001330986,pass,2026-11-02,power line easement across the middle
14F0063  LL0219,listed,2026-11-03,on the market with a broker
```

- `pass`: drop it from future lists.
- `listed`: it's already for sale, so drop it (`exclude_listed` in settings).
- `pursue`: mark it in `ranked.csv`. If a later run screens it out (e.g.
  a new building or owner), `summary.md` calls that out and
  `rejected.csv` keeps the mark.

Copy parcel ids exactly as they appear in `ranked.csv`, including spaces.
These marks are also the future second half of the AutoResearch score
(see `screener/autoresearch/PROGRAM.md`).
