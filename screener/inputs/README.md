# Your decisions

`decisions.csv` (created on the first run, kept out of git) records what
you decided about parcels. Each run reads it:

```csv
parcel_id,decision,date,note
09F030000140097,pursue,2026-11-02,called owner - open to offers
07 290001330986,pass,2026-11-02,power line easement across the middle
14F0063  LL0219,listed,2026-11-03,on the market with a broker
14F0071 0002,maybe,2026-11-04,ask about the access road
```

- `pass`: drop it from future lists.
- `listed`: it's already for sale, so drop it (`exclude_listed` in settings).
- `pursue`: mark it in `ranked.csv`. If a later run screens it out (e.g.
  a new building or owner), `summary.md` calls that out and
  `rejected.csv` keeps the mark.
- `maybe`: a mark in `ranked.csv` / `rejected.csv` only. It changes no
  score, flag, order or screen.

The last row for a parcel wins, so add a row to change your mind (a
`maybe` after a `pass` brings the parcel back). Any other value in the
`decision` column is ignored.

## Recording a decision from the command line

```bash
python -m screener.decide "14F0063  LL0219" pursue called owner, open to offers
python -m screener.decide 09F030000140097 maybe
python -m screener.decide "07 290001330986" pass --run screener/output/fulton-2026-11-02
```

It uses the newest run under `screener/output/` (and prints which) unless
you give `--run`. The id must match that run's `ranked.csv` or
`rejected.csv` exactly, spaces included, so quote ids with spaces; an
unknown id is refused and close matches are shown, never picked. Decisions
are `pursue`, `pass`, `maybe` or `listed`, any case. A `pass` prints that
the parcel will drop out of future runs before it is written. Each call
appends one row (it never rewrites the file) and also copies the parcel's
features from the run's `features.json` into `decision_features.jsonl`
here, so they outlive the run folder: a same-day re-run, deleting
`output/`, or the parcel dropping out after a pass. The note stays in
`decisions.csv` only. Rejected parcels have no feature snapshot, so a
decision on one needs `--no-features` (the decision is written, without
features). It is offline.

## Editing the file by hand

Spreadsheets are fine: a UTF-8 byte-order mark, Windows line endings, a
last line without a newline, and notes with commas, quotes or line breaks
are all read correctly (save as CSV). Keep the `parcel_id` and `decision`
column names. Copy parcel ids exactly as they appear in `ranked.csv`,
including spaces.

Everything in this folder except this README is kept out of git
(`screener/.gitignore`): an untracked file here would make the
AutoResearch evaluator refuse to score.

These marks are also the future second half of the AutoResearch score
(see `screener/autoresearch/PROGRAM.md`). That step (Phase B) is a human
`--new-baseline` change and has not been made: today no decision reaches
the evaluation.
