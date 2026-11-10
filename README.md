# screener-data

Data for the land screener's scheduled cloud runs. **No code lives here,
and this branch is never merged into `main`.** It exists because a cloud
session's files are discarded when the session ends.

It holds owner names, mailing addresses and your decisions. Keep the
repository private.

| Path | What it is |
|---|---|
| `decisions/decisions.csv` | Your pursue / pass / maybe / listed marks. Every run reads it. |
| `decisions/decision_features.jsonl` | Written by `python -m screener.decide` (absent until first used). |
| `runs/<county>-<date>/` | One folder per run: `leads.md` (start here), `summary.md`, `ranked.csv`, `briefs/`, `map.html`, `rejected.csv`, `ranked.geojson`, `features.json`, `facts.json`. |
| `digests/<county>-<date>.md` | What changed since the previous run. Start here. |
| `leads/<parcel>/` | An agent's check of one lead: `lead.md` (start there) and its quote-checked findings. The fetched pages are not kept here. |
| `cache/slope/` | Kept lidar slope answers (public data, about 2 KB per parcel). They spare each run about 50 minutes. |
| `RUNBOOK.md` | The steps the scheduled session follows. |

## Recording a decision

Add a row to `decisions/decisions.csv` (GitHub's web editor works), or ask
Claude to add it:

```csv
parcel_id,decision,date,note
09F030000140097,pursue,2026-11-02,called owner - open to offers
```

The parcel id must match `ranked.csv` exactly, spaces included. The last
row for a parcel wins. `pass` and `listed` drop the parcel from later runs;
`pursue` and `maybe` are marks only. Details: `screener/inputs/README.md`
on `main`.
