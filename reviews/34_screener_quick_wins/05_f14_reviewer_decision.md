# 05 — F14 decision capture (Phase A): reviewer decision (Agent 3)

> Fresh context. I did not write this code. I read the requirement, the risk
> review `01_risk_review.md` § F14, the code writer response
> `04_f14_code_writer_response.md` and the actual diff
> (`git diff 4fe8080..HEAD`; the F14 commits are `7cb435a`, `3c2ae4c`,
> `bd13659`), then re-ran the checks myself. Model names are omitted.

## Operator answers (2026-11-09, in chat, binding), as honoured here

1. **D3:** COUNT unlisted districts AND districts with unreadable setback
   values (answers D3-01 / D3-02). Root D3 round; nothing in this folder.
2. **D2 flood scope:** "just review". D2 stays as implemented (V/VE
   review-only, floodway still blocks). No flood-rule change. This diff
   touches no flood code.
3. **Null-beats-low:** NOT answered. No new root sub-score. F14 adds no
   score of any kind; `maybe` is a mark only.
4. **"Fix":** the research.py ArcGIS error-envelope bug, the audit-path bug
   and the D1-11 `zoning_import` fetcher round are root work, not in this
   folder.
5. **"Can you add this yourself":** the agent may apply the reviews/26
   hazards prepare-mutation to `prepare.py`, between runs, under the
   prepare-mutation protocol. Root work; `prepare.py` is not in this diff.
6. **Step 2, screener quick wins:** human-side screener edits are
   authorized except the eval files (`screens.py`,
   `autoresearch/evaluate.py`, `autoresearch/build_eval.py`,
   `autoresearch/data/`, `settings.toml` defaults may only gain new keys).
   No new eval baseline.

**Phase B is NOT done.** No decision reaches the evaluation. Turning the
decision-side store into labels stays a human `--new-baseline` step, with
the open questions listed in `01_risk_review.md` § F14 "Phase B".

## What I verified

| Gate | Result |
|---|---|
| H1 leak | `p["decision"]` is still set after `scoring.score` and `screens.flags`, at the same place as base. The snapshot is the local `snap`; `snapshot.take` reads `p.get(f)` for allow-listed names only and writes nothing to `p`. `maybe` appears only in `DECISION_VALUES`. `scoring.py`, `rank.py`, `screens.py` unchanged. Spy tests cover pursue / maybe / listed / pass, slope on and `--no-slope`. |
| H2 eval | `git diff --stat 4fe8080` over `screener/autoresearch`, `screens.py`, `rank.py`, `scoring.py`, `settings.toml`, `PROGRAM.md`, `requirements.txt`, `CLAUDE.md`, `prepare.py` is empty. I re-ran `python -m screener.autoresearch.evaluate`: `pool 2229, positives 34, k 45, hits 2, recall 0.059, positives_screened_out 1, score_range [6.5, 85.0], nonfinite 0`, identical to the writer's base line. `fingerprint()` is `4efd44280951`; `EVAL_FILES` unchanged. |
| H3 ignore | `inputs/*` + `!inputs/README.md`; the check-ignore tests use the code's own constants, and the README stays tracked. `git status` after the full suite shows only my edits. |
| H4 snapshot | Keys from `build_eval.FEATURES` / `BANNED_AS_NONE` by import (identity-tested). `BANNED_AS_NONE` holds no owner or mailing field (I read the list: zoning, flu, entitlement, entitlement_note, utilities, road, slope fields, contamination, opportunity_zone, jurisdiction). No geometry, earthwork, score or decision. Taken before scoring (mutation spy). `allow_nan=False`, NaN counted. |
| H5 store | `decide.py` writes the store before the CSV row, append-only, no note. Survives re-run, `output/` deletion and a pass (tested). |
| H6 file | BOM (`utf-8-sig` in both readers), missing final newline, CRLF / LF, quoted reason, header order, headerless refusal, absent / empty file: each tested. |
| H7 CLI | Exact ids after `strip()`, close matches shown never picked, no rank shorthand, newest-by-mtime with a tie refusal, `--run`, four values case-insensitive, `pass` consequence printed, last row wins. `python -m screener.decide --help` runs. |
| H8 real files | Paths resolve at call time; module setup/teardown fingerprints the real `decisions.csv`, store and `output/`. Imports are stdlib plus `run`, `snapshot`, `build_eval` (AST-checked). |
| H9 | See Tests below. Docs updated; `PROGRAM.md` untouched. |

Writer deviations 1-4 are accepted: whitespace-only near matches first is
the likelier mistake; `nargs="*"` reasons are documented; duplicate ids
carry all their records; the landing commit not being scorable by
`--decide` is the W6 wrinkle, as expected.

## Fix I made

**R-1 (MEDIUM, F14-3 mismatch).** A same-day re-run writes into the same
`output/<county>-<date>/`. `report.write_all` rewrites `ranked.csv` in
place, and `snapshot.write` only runs afterwards. If the re-run failed
between the two (in the reports, or in the snapshot write itself), the
first run's `features.json` stayed next to the second run's `ranked.csv`.
`decide.py` would then find the id in the new `ranked.csv` and store the
earlier run's features and `meta` for a decision made on the new run, which
is exactly the "joined to features from a different run" case F14-3 rules
out. The writer's crash test only covered a first run into an empty folder.

Change: new `snapshot.clear(out_dir)` (unlinks `features.json`,
`missing_ok`), called in `run.main` immediately before `report.write_all`.
A failed re-run now leaves no snapshot, so `decide.py` refuses unless
`--no-features` is given, and then records no features. README notes it.
New tests in `SnapshotTests`:

- `test_failed_rerun_never_pairs_new_ranked_with_old_snapshot`: a good
  run, then a re-run with changed acres whose `snapshot.write` raises. The
  new `ranked.csv` holds 30 acres, `features.json` is gone, `decide` exits 2
  naming `--no-features`, and the store stays empty. I confirmed it fails
  with the `clear` call removed.
- `test_clear_is_a_no_op_without_a_snapshot`.

No eval file, `rank.py`, `scoring.py`, `screens.py` or settings changed.

## Open items (not blocking)

- Guard limit (SR-16 analog), unchanged: the spy tests live in
  `screener/tests`, which the loop's `evaluate.py` does not run. They hold
  because the loop edits `rank.py` alone and every `exp:` diff gets a human
  read.
- `settings.toml` is read twice in `run.main` (text for parsing, bytes for
  the SHA-256). An edit between the two reads would mislabel `meta`; this
  is a local file the operator edits by hand, so I left it.
- Phase B, as above.

## Tests

- Root: `python -m unittest discover -s tests` gives **1744 OK** (base 1744).
- Screener: `python -m unittest discover -s screener/tests -t .` gives
  **205 OK** (134 at `4fe8080`, 165 after F8, 203 after the F14 writer,
  +2 here).
- I did not repeat the live run. The writer's single `--limit 30` run,
  counts only, is the sub-item's one allowed live check; the fix only
  deletes a file in the run folder before the reports are written.

## Decision

**APPROVED after a small fix (R-1).** Gates H1-H9 hold, every F14 risk is
addressed, the eval line and fingerprint are unchanged, no human-owned or
eval file changed, and the operator answers are honoured exactly.

Standing risks:

- SR-6: offline fakes; the real files are fingerprinted.
- SR-7: atomic snapshot under an ignored name; a stale snapshot is removed
  before the reports are rewritten.
- SR-8 analog: no owner names or notes in the store; counts only.
- SR-9: append-only, last row wins.
- SR-10 / SR-11: no new dependencies or requests.
- SR-15 / SR-16 analogs: the eval boundary is unchanged; decisions stay off
  `p` until after scoring; Phase B is the human baseline step.

## Post-change adversarial review round 1

Operator answers (2026-11-09, recorded as given): D3 counts unlisted
districts and unreadable setback values; D2 flood scope "just review" (no
flood-rule change); the null-beats-low question is not answered, so no new
root sub-score; "fix" the `research.py` ArcGIS error-envelope and audit-path
bugs and do the D1-11 `zoning_import` round; the agent may apply the
reviews/26 hazards prepare-mutation; build the screener quick wins. The fix
below touches no eval file, `rank.py`, `scoring.py`, `screens.py`,
`settings.toml` or `requirements.txt`; `evaluate.fingerprint()` and
`EVAL_FILES` are unchanged.

The round raised two findings. They describe the same defect, so they are
one row here.

| # | Finding | Verdict | What changed |
|---|---|---|---|
| 1 / 2 | `decide.py` strips header names when it checks and appends, but `run.load_decisions` keys rows by the raw `csv.DictReader` names. A hand-edited `parcel_id, decision, date, note` header passes `decide.py`, which appends the row and prints the pass consequence, but the run reads every row's decision as missing, so the pass (or listed) is silently never applied | Real. Confirmed offline: with the padded header and the run change reverted, the two new tests below fail (the decision is not found). | `load_decisions` now strips the reader's field names before reading rows, so the reader is exactly as lenient as `decide.py`'s check and column alignment. I chose this over making `decide.py` refuse the header, because refusing would still leave the run silently ignoring a file the operator padded in a spreadsheet; stripping makes both readers agree and makes the CLI's printed consequence true. New tests: `test_padded_header_names_are_read_and_the_pass_honoured` (decide records a pass against the padded header, an earlier padded `pursue` row is read with its date and note, and a re-run drops the passed parcel from `ranked.csv`); `test_padded_header_with_bom_and_tabs` (BOM plus space and tab padding); `test_header_only_file_loads_nothing`. `inputs/README.md` now says spaces around a column name are ignored by both. |

Not changed: header names stay case-sensitive in both readers (`Parcel_ID`
is refused by `decide.py` and ignored by the run, consistently), and a
header that names a column twice after stripping keeps `csv.DictReader`'s
last-wins behaviour. Neither was raised, and neither makes the two readers
disagree.

Tests after round 1:

- Root: `python -m unittest discover -s tests` gives **1744 OK**.
- Screener: `python -m unittest discover -s screener/tests -t .` gives
  **208 OK** (205 before round 1, +3).

No live run was repeated: the fix only changes how a local file's header
names are read.

**Round 1 decision: APPROVED after fixes.**
