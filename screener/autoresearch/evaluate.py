"""Score screener/rank.py against the frozen answer key.

LOCKED: the AutoResearch agent runs this but never edits it, and never opens
the files in data/. Usage:

    python -m screener.autoresearch.evaluate             # dev score only
    python -m screener.autoresearch.evaluate --decide    # score, keep/discard, log it
    python -m screener.autoresearch.evaluate --test      # sealed test split (humans only)
    python -m screener.autoresearch.evaluate --control   # random-ranking baseline

Metric: hits@K = how many real developer purchases (2018 land, industrial
building by 2025) the ranking puts in its top K, where K is 2% of the
pool — about the number of parcels a person can actually review. The
user's hard screens (settings.toml) apply: a parcel they would reject
can't count as a hit.
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
import tomllib
from pathlib import Path

from .. import screens

HERE = Path(__file__).resolve().parent
FEATURES_FILE = HERE / "data" / "fulton_2018_features.json.gz"
LABELS_FILE = HERE / "data" / "fulton_2018_labels.json"
LOG_FILE = HERE / "experiments.tsv"   # append-only, untracked
LOG_COLUMNS = ["time", "commit", "hits", "positives", "k", "recall", "se", "status", "description"]
K_SHARE = 0.02
BOOTSTRAP = 300


def load() -> tuple[list[dict], set[str], dict]:
    with gzip.open(FEATURES_FILE, "rt") as fh:
        data = json.load(fh)
    labels = json.loads(LABELS_FILE.read_text())
    return data["records"], set(labels["positives"]), data["meta"]


def _tiebreak(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def evaluate(records: list[dict], positives: set[str], split: str,
             scorer=None, settings: dict | None = None) -> dict:
    if scorer is None:
        scorer = importlib.import_module("screener.rank").score
    if settings is None:
        settings = tomllib.loads((HERE.parent / "settings.toml").read_text())["screens"]
    pool = [r for r in records if r["split"] == split]
    pos = {r["key"] for r in pool if r["key"] in positives}
    scored = []
    for r in pool:
        if screens.site_rejects(r, settings):
            continue
        features = {k: v for k, v in r.items() if k not in ("key", "split")}
        scored.append((-float(scorer(features)["score"]), _tiebreak(r["key"]), r["key"]))
    scored.sort()
    k = max(1, round(K_SHARE * len(pool)))
    ranked_keys = [key for _, _, key in scored]
    hits = sum(1 for key in ranked_keys[:k] if key in pos)

    # Paired bootstrap over the pool: how much hits@K moves by chance.
    rng = random.Random(7)
    rank_of = {key: i for i, key in enumerate(ranked_keys)}
    unranked = len(ranked_keys) + 1
    samples = []
    for _ in range(BOOTSTRAP):
        draw = [rng.choice(pool)["key"] for _ in pool]
        order = sorted(draw, key=lambda key: rank_of.get(key, unranked))
        samples.append(sum(1 for key in order[:k] if key in pos))
    mean = sum(samples) / len(samples)
    se = (sum((s - mean) ** 2 for s in samples) / (len(samples) - 1)) ** 0.5
    return {
        "split": split, "pool": len(pool), "positives": len(pos), "k": k, "hits": hits,
        "recall": round(hits / len(pos), 3) if pos else 0.0, "se_hits": round(se, 2),
        "positives_screened_out": len(pos - set(ranked_keys)),
    }


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _best_kept() -> dict | None:
    if not LOG_FILE.exists():
        return None
    with open(LOG_FILE, newline="") as fh:
        rows = [r for r in csv.DictReader(fh, delimiter="\t") if r["status"] in ("baseline", "keep")]
    return max(rows, key=lambda r: int(r["hits"]), default=None)


def _append(row: dict) -> None:
    new = not LOG_FILE.exists()
    with open(LOG_FILE, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=LOG_COLUMNS, delimiter="\t")
        if new:
            w.writeheader()
        w.writerow(row)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--decide", action="store_true", help="compare to the best kept run and log")
    ap.add_argument("--test", action="store_true", help="also score the sealed test split")
    ap.add_argument("--control", action="store_true", help="random-ranking baseline")
    args = ap.parse_args(argv)

    if not FEATURES_FILE.exists():
        print("No evaluation set yet: run `python -m screener.autoresearch.build_eval` first.")
        return 2
    records, positives, meta = load()

    if args.control:
        rng = random.Random(11)
        res = evaluate(records, positives, "dev", scorer=lambda f: {"score": rng.random()})
        print(json.dumps({"control": res}))
        return 0

    dev = evaluate(records, positives, "dev")
    out = {"dev": dev}
    if args.test:
        out["test"] = evaluate(records, positives, "test")
    print(json.dumps(out))
    if not args.decide:
        return 0

    best = _best_kept()
    if best is None:
        status, why = "baseline", "first run: this is the baseline"
    else:
        gain = dev["hits"] - int(best["hits"])
        if gain >= 1 and gain > float(best["se"]):
            status, why = "keep", f"+{gain} hits over best kept ({best['hits']}), above noise ({best['se']})"
        else:
            status, why = "discard", (f"{dev['hits']} hits vs best kept {best['hits']} "
                                      f"(needs +1 and more than {best['se']})")
    _append({
        "time": time.strftime("%Y-%m-%d %H:%M:%S"), "commit": _git("rev-parse", "--short", "HEAD"),
        "hits": dev["hits"], "positives": dev["positives"], "k": dev["k"],
        "recall": dev["recall"], "se": dev["se_hits"], "status": status,
        "description": _git("log", "-1", "--format=%s"),
    })
    print(f"DECISION: {status.upper()} — {why}")
    if status == "discard":
        print("Revert the experiment commit: git reset --hard HEAD~1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
