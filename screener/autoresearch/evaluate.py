"""Score screener/rank.py against the frozen answer key.

LOCKED: the AutoResearch agent runs this but never edits it, and never opens
the files in data/. Usage:

    python -m screener.autoresearch.evaluate             # dev score only
    python -m screener.autoresearch.evaluate --decide    # score, keep/discard, log it
    python -m screener.autoresearch.evaluate --test      # sealed test split (humans only)
    python -m screener.autoresearch.evaluate --control   # what random ranking scores

Metric: hits@K = how many parcels the 2016 owner sold to someone who built
an industrial building by 2025 land in the ranking's top K, where K is 2%
of the pool (about what a person can actually review). The hard screens
frozen into the evaluation set apply: a parcel they reject can't be a hit.

Parcels are ranked by rank.learned_score. The fixed part of the live score
(scoring.py: zoning, slope, utilities, incentives) is blank in the
evaluation set, so it is the same for every parcel and can't change the
order.

Keep rule (--decide): the experiment must add at least one hit over the
best kept run AND win in at least 90% of clustered bootstrap resamples.
Each resample draws whole ~5 km map cells, because positives come in
clusters, and scores both rankings on the same draw.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import importlib
import json
import random
import subprocess
import sys
import time
from pathlib import Path

from .. import screens

HERE = Path(__file__).resolve().parent
FEATURES_FILE = HERE / "data" / "fulton_2016_features.json.gz"
LABELS_FILE = HERE / "data" / "fulton_2016_labels.json"
LOG_FILE = HERE / "experiments.tsv"          # append-only, untracked
BEST_FILE = HERE / "best_ranking.json"       # ranking of the best kept run, untracked
LOG_COLUMNS = ["time", "commit", "parent", "fingerprint", "hits", "positives", "k",
               "recall", "win_share", "status", "description"]
# Files whose change alters what the score means. Changing one needs a
# human-recorded new baseline (--new-baseline).
EVAL_FILES = ("screener/autoresearch/evaluate.py", "screener/autoresearch/data/",
              "screener/screens.py")
K_SHARE = 0.02
BOOTSTRAP = 500
WIN_SHARE_TO_KEEP = 0.9
HIDDEN = ("key", "split", "block")           # never passed to the ranking


def load() -> tuple[list[dict], set[str], dict]:
    with gzip.open(FEATURES_FILE, "rt") as fh:
        data = json.load(fh)
    labels = json.loads(LABELS_FILE.read_text())
    return data["records"], set(labels["positives"]), data["meta"]


def fingerprint() -> str:
    """Identifies the evaluation set; rows from a different set never compare."""
    h = hashlib.sha256()
    for path in (FEATURES_FILE, LABELS_FILE):
        h.update(path.read_bytes())
    return h.hexdigest()[:12]


def _tiebreak(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def ranking(records: list[dict], split: str, scorer, settings: dict) -> tuple[list[str], tuple]:
    """Keys of the split's parcels that pass the screens, best first, and
    the (min, max) score seen."""
    scored = []
    for r in records:
        if r["split"] != split or screens.site_rejects(r, settings):
            continue
        features = {k: v for k, v in r.items() if k not in HIDDEN}
        scored.append((-float(scorer(features)["score"]), _tiebreak(r["key"]), r["key"]))
    scored.sort()
    span = (-scored[-1][0], -scored[0][0]) if scored else (0.0, 0.0)
    return [key for _, _, key in scored], span


def hits_at_k(order: list[str], pool_keys: list[str], positives: set[str]) -> tuple[int, int]:
    """Hits in the top K of `order` restricted to `pool_keys` (with repeats)."""
    k = max(1, round(K_SHARE * len(pool_keys)))
    counts: dict[str, int] = {}
    for key in pool_keys:
        counts[key] = counts.get(key, 0) + 1
    hits, taken = 0, 0
    for key in order:
        n = counts.get(key, 0)
        if not n:
            continue
        use = min(n, k - taken)
        if key in positives:
            hits += use
        taken += use
        if taken >= k:
            break
    return hits, k


def evaluate(records: list[dict], positives: set[str], split: str,
             scorer=None, settings: dict | None = None) -> dict:
    if scorer is None:
        scorer = importlib.import_module("screener.rank").learned_score
    if settings is None:
        settings = load_meta_screens()
    pool = [r["key"] for r in records if r["split"] == split]
    pos = {k for k in pool if k in positives}
    order, span = ranking(records, split, scorer, settings)
    hits, k = hits_at_k(order, pool, pos)
    return {
        "split": split, "pool": len(pool), "positives": len(pos), "k": k, "hits": hits,
        "recall": round(hits / len(pos), 3) if pos else 0.0,
        "positives_screened_out": len(pos - set(order)),
        "score_range": [round(span[0], 1), round(span[1], 1)], "order": order,
    }


def load_meta_screens() -> dict:
    with gzip.open(FEATURES_FILE, "rt") as fh:
        return json.load(fh)["meta"]["screens"]


def paired_win_share(records: list[dict], positives: set[str], new: list[str],
                     best: list[str], split: str = "dev") -> float:
    """Share of clustered bootstrap draws in which `new` beats `best`."""
    blocks: dict[str, list[str]] = {}
    for r in records:
        if r["split"] == split:
            blocks.setdefault(r["block"], []).append(r["key"])
    names = sorted(blocks)
    rng = random.Random(7)
    wins = 0
    for _ in range(BOOTSTRAP):
        draw = [key for _ in names for key in blocks[rng.choice(names)]]
        if hits_at_k(new, draw, positives)[0] > hits_at_k(best, draw, positives)[0]:
            wins += 1
    return wins / BOOTSTRAP


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _is_ancestor(commit: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", commit, "HEAD"],
                          capture_output=True).returncode == 0


def _rows() -> list[dict]:
    if not LOG_FILE.exists():
        return []
    with open(LOG_FILE, newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def _best_kept(fp: str) -> dict | None:
    """Best baseline/keep row for this evaluation set whose commit is still in history."""
    rows = [r for r in _rows() if r["status"] in ("baseline", "keep") and r["fingerprint"] == fp
            and _is_ancestor(r["commit"])]
    return max(rows, key=lambda r: int(r["hits"]), default=None)


def _append(row: dict) -> None:
    new = not LOG_FILE.exists()
    with open(LOG_FILE, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=LOG_COLUMNS, delimiter="\t")
        if new:
            w.writeheader()
        w.writerow(row)


def _refuse(msg: str) -> int:
    print(f"REFUSED: {msg}")
    return 3


def decide(records: list[dict], positives: set[str], dev: dict, new_baseline: bool) -> int:
    head = _git("rev-parse", "--short", "HEAD")
    parent = _git("rev-parse", "--short", "HEAD~1")
    fp = fingerprint()
    if _git("status", "--porcelain", "--untracked-files=no", "--", "screener"):
        return _refuse("uncommitted changes under screener/. Commit the experiment first.")
    if any(r["commit"] == head and r["fingerprint"] == fp for r in _rows()):
        return _refuse(f"commit {head} was already scored. Make a new experiment commit.")
    lo, hi = dev["score_range"]
    if lo < 0 or hi > 100:
        return _refuse(f"rank.learned_score ranges {lo}..{hi}; it must stay within 0-100 so "
                       "the fixed zoning/slope share of the live score is unchanged.")
    best = None if new_baseline else _best_kept(fp)
    if best is not None:
        touched = [f for f in _git("diff", "--name-only", "HEAD~1", "HEAD", "--",
                                   "screener").splitlines() if f]
        if touched != ["screener/rank.py"]:
            return _refuse("an experiment is one commit that edits only screener/rank.py; "
                           f"HEAD touches: {', '.join(touched) or 'nothing under screener/'}.")
        since = [f for f in _git("diff", "--name-only", best["commit"], "HEAD", "--",
                                 "screener").splitlines() if f.startswith(EVAL_FILES)]
        if since:
            return _refuse(f"the evaluation itself changed since the best kept run "
                           f"({', '.join(since)}). A human must record a new baseline: "
                           "python -m screener.autoresearch.evaluate --decide --new-baseline")

    if best is None or not BEST_FILE.exists():
        status, why, share = "baseline", "recorded as the baseline for this evaluation set", ""
    else:
        best_order = json.loads(BEST_FILE.read_text())["order"]
        gain = dev["hits"] - int(best["hits"])
        share = paired_win_share(records, positives, dev["order"], best_order)
        if gain >= 1 and share >= WIN_SHARE_TO_KEEP:
            status, why = "keep", (f"+{gain} hits over best kept ({best['hits']}); wins "
                                   f"{share:.0%} of resamples")
        else:
            status, why = "discard", (f"{dev['hits']} hits vs best kept {best['hits']}; wins "
                                      f"{share:.0%} of resamples (needs +1 and "
                                      f"{WIN_SHARE_TO_KEEP:.0%})")
        share = f"{share:.3f}"

    _append({
        "time": time.strftime("%Y-%m-%d %H:%M:%S"), "commit": head, "parent": parent,
        "fingerprint": fp, "hits": dev["hits"], "positives": dev["positives"], "k": dev["k"],
        "recall": dev["recall"], "win_share": share, "status": status,
        "description": _git("log", "-1", "--format=%s"),
    })
    if status in ("baseline", "keep"):
        BEST_FILE.write_text(json.dumps({"commit": head, "fingerprint": fp, "order": dev["order"]}))
    print(f"DECISION: {status.upper()} — {why}")
    if status == "discard":
        print(f"Revert the experiment: git reset --hard {parent}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--decide", action="store_true", help="compare to the best kept run and log")
    ap.add_argument("--test", action="store_true", help="also score the sealed test split")
    ap.add_argument("--control", action="store_true", help="what random ranking scores")
    ap.add_argument("--new-baseline", action="store_true",
                    help="(humans only) record the current ranking as a fresh baseline")
    args = ap.parse_args(argv)

    if not FEATURES_FILE.exists():
        print("No evaluation set yet: run `python -m screener.autoresearch.build_eval` first.")
        return 2
    records, positives, _ = load()
    public = lambda res: {k: v for k, v in res.items() if k != "order"}

    if args.control:
        dev = evaluate(records, positives, "dev", scorer=lambda f: {"score": 0.0})
        ranked_pos = dev["positives"] - dev["positives_screened_out"]
        ranked = len(dev["order"])
        expected = dev["k"] * ranked_pos / ranked if ranked else 0.0
        draws = []
        for seed in range(200):
            rng = random.Random(seed)
            draws.append(evaluate(records, positives, "dev",
                                  scorer=lambda f: {"score": rng.random()})["hits"])
        draws.sort()
        print(json.dumps({"control": {"k": dev["k"], "expected_hits": round(expected, 2),
                                      "mean_hits": round(sum(draws) / len(draws), 2),
                                      "p5_p95": [draws[10], draws[189]]}}))
        return 0

    dev = evaluate(records, positives, "dev")
    out = {"dev": public(dev)}
    if args.test:
        out["test"] = public(evaluate(records, positives, "test"))
    print(json.dumps(out))
    if args.new_baseline and not args.decide:
        print("--new-baseline only applies with --decide")
        return 2
    return decide(records, positives, dev, args.new_baseline) if args.decide else 0


if __name__ == "__main__":
    sys.exit(main())
