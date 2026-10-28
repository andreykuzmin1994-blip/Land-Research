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

Guards. Before rank.py is imported, a static check (rank_problems) must
pass, or nothing is scored. --decide also refuses (exit 3, logs nothing)
on uncommitted changes or untracked files under screener/, a commit that
touches anything but screener/rank.py, a score that isn't a finite number
in 0-100, or a best kept run whose experiments.tsv row or
best_ranking.json is missing or doesn't match. A human then records a new
baseline (--new-baseline), which starts a new chain: later experiments are
compared with it, not with older runs.
"""

from __future__ import annotations

import __future__
import argparse
import ast
import csv
import gzip
import hashlib
import importlib
import importlib.util
import inspect
import json
import math
import random
import subprocess
import sys
import time
import typing
from pathlib import Path

from .. import screens

HERE = Path(__file__).resolve().parent
FEATURES_FILE = HERE / "data" / "fulton_2016_features.json.gz"
LABELS_FILE = HERE / "data" / "fulton_2016_labels.json"
LOG_FILE = HERE / "experiments.tsv"          # append-only, untracked
BEST_FILE = HERE / "best_ranking.json"       # ranking of the best kept run, untracked
RANK_FILE = HERE.parent / "rank.py"
# Uncommitted changes and untracked (not ignored) files under screener/.
# The "## <branch>" header line shows that git answered.
STATUS_ARGS = ("status", "--porcelain", "--branch", "--untracked-files=all", "--", "screener")
NEW_BASELINE = "python -m screener.autoresearch.evaluate --decide --new-baseline"
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
# What rank.py may import (PROGRAM.md: pure, no I/O). math is for smooth curves.
RANK_IMPORTS = ("__future__", "math", "typing")
# Builtins, helpers and attributes that open files, run code, or reach module
# internals or the evaluator's stack frames (str.format can walk attributes).
RANK_BANNED_NAMES = {"open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars",
                     "getattr", "setattr", "delattr", "breakpoint", "input", "get_type_hints",
                     "ForwardRef", "format", "format_map", "gi_frame", "cr_frame", "ag_frame",
                     "tb_frame", "f_back", "f_globals", "f_locals", "f_builtins"}
RANK_BANNED_STRINGS = ("autoresearch/data", "fulton_2016", "experiments.tsv", "best_ranking")


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


def ranking(records: list[dict], split: str, scorer, settings: dict
            ) -> tuple[list[str], tuple, int]:
    """Keys of the split's parcels that pass the screens, best first, the
    (min, max) finite score seen, and how many scores were NaN or infinite.
    One NaN anywhere scrambles the sort, so those rank last and --decide
    refuses the run."""
    scored, nonfinite = [], 0
    for r in records:
        if r["split"] != split or screens.site_rejects(r, settings):
            continue
        features = {k: v for k, v in r.items() if k not in HIDDEN}
        score = float(scorer(features)["score"])
        if not math.isfinite(score):
            nonfinite += 1
            score = -math.inf
        scored.append((-score, _tiebreak(r["key"]), r["key"]))
    scored.sort()
    finite = [-s for s, _, _ in scored if math.isfinite(s)]
    span = (min(finite), max(finite)) if finite else (0.0, 0.0)
    return [key for _, _, key in scored], span, nonfinite


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
    order, span, nonfinite = ranking(records, split, scorer, settings)
    hits, k = hits_at_k(order, pool, pos)
    return {
        "split": split, "pool": len(pool), "positives": len(pos), "k": k, "hits": hits,
        "recall": round(hits / len(pos), 3) if pos else 0.0,
        "positives_screened_out": len(pos - set(order)),
        "score_range": [round(span[0], 1), round(span[1], 1)], "nonfinite_scores": nonfinite,
        "order": order,
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


def rank_problems(source: str) -> list[str]:
    """Why rank.py's source breaks PROGRAM.md rules 1-3, if it does.

    rank.py may import only __future__, math and typing (no module objects
    or private names through them); may not use open, eval, exec, compile,
    getattr, globals, vars, str.format, frame attributes (gi_frame, f_back,
    f_locals...) and the like, dunder names, private attributes, or global /
    nonlocal state; and its strings (docstrings aside) may not name the
    evaluation files. It reads the source before rank.py is imported. It
    guards against drift and is not a sandbox.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [f"does not parse ({e.msg}, line {e.lineno})"]
    modules = {name for m in (__future__, math, typing) for name, value in vars(m).items()
               if inspect.ismodule(value)}

    def banned_attr(name: str) -> bool:
        return name.startswith("_") or name in modules or name in RANK_BANNED_NAMES

    docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                       ast.AsyncFunctionDef))
                  and node.body and isinstance(node.body[0], ast.Expr)}
    problems = []
    for node in ast.walk(tree):
        where = f"line {getattr(node, 'lineno', '?')}"
        if isinstance(node, ast.Import):
            problems += [f"{where}: imports {a.name}" for a in node.names
                         if a.name not in RANK_IMPORTS]
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module not in RANK_IMPORTS:
                problems.append(f"{where}: imports from {'.' * node.level}{node.module or ''}")
            problems += [f"{where}: imports {a.name} from {node.module}" for a in node.names
                         if a.name == "*" or banned_attr(a.name)]
        elif isinstance(node, ast.Name) and (node.id in RANK_BANNED_NAMES
                                             or node.id[:2] == node.id[-2:] == "__"):
            problems.append(f"{where}: uses {node.id}")
        elif isinstance(node, ast.Attribute) and banned_attr(node.attr):
            problems.append(f"{where}: uses .{node.attr}")
        elif isinstance(node, ast.MatchClass):           # case C(attr=x) reads C.attr
            problems += [f"{where}: uses .{a}" for a in node.kwd_attrs if banned_attr(a)]
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            problems.append(f"{where}: keeps state between calls ({', '.join(node.names)})")
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
              and id(node) not in docstrings
              and any(name in node.value for name in RANK_BANNED_STRINGS)):
            problems.append(f"{where}: names an evaluation file ({node.value!r})")
    return problems


def _rank_origin() -> str | None:
    """The file `import screener.rank` would load, found without running it."""
    try:
        spec = importlib.util.find_spec("screener.rank")
    except (ImportError, ValueError):
        return None
    return spec.origin if spec else None


def rank_import_problems() -> list[str]:
    """rank_problems for the rank.py that would be imported."""
    origin = _rank_origin()
    if not origin or Path(origin).resolve() != RANK_FILE.resolve():
        return [f"screener.rank would load from {origin}, not screener/rank.py"]
    return rank_problems(RANK_FILE.read_text())


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


def _kept_rows(fp: str) -> list[dict]:
    """Baseline and keep rows for this evaluation set, in log order."""
    return [r for r in _rows() if r["status"] in ("baseline", "keep") and r["fingerprint"] == fp]


def _best_kept(rows: list[dict]) -> dict | None:
    """Best run of the current chain: the latest baseline whose commit is
    still in history, and the keeps logged after it that are too. A new
    baseline starts a new chain, so --new-baseline resets the bar."""
    start = next((i for i in reversed(range(len(rows)))
                  if rows[i]["status"] == "baseline" and _is_ancestor(rows[i]["commit"])), None)
    if start is None:
        return None
    chain = [rows[start]] + [r for r in rows[start + 1:]
                             if r["status"] == "keep" and _is_ancestor(r["commit"])]
    return max(reversed(chain), key=lambda r: int(r["hits"]))


def _saved_best() -> dict | None:
    """best_ranking.json, or None if it is missing or unreadable."""
    try:
        saved = json.loads(BEST_FILE.read_text())
    except (OSError, ValueError):
        return None
    return saved if isinstance(saved, dict) and isinstance(saved.get("order"), list) else None


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
    if not head:
        return _refuse("git did not give the current commit. Run this inside the repository.")
    parent = _git("rev-parse", "--short", "HEAD~1")
    fp = fingerprint()
    git_status = _git(*STATUS_ARGS).splitlines()
    if not git_status or not git_status[0].startswith("##"):
        return _refuse("git did not give the status of screener/.")
    if git_status[1:]:
        return _refuse("uncommitted changes or untracked files under screener/ "
                       f"({', '.join(line[3:] for line in git_status[1:4])}). Commit the "
                       "experiment first; rank.py may not use any other file.")
    # A human may re-record an already scored commit (the best kept run).
    if not new_baseline and any(r["commit"] == head and r["fingerprint"] == fp for r in _rows()):
        return _refuse(f"commit {head} was already scored. Make a new experiment commit.")
    if dev.get("nonfinite_scores"):
        return _refuse(f"rank.learned_score returned {dev['nonfinite_scores']} scores that are NaN "
                       "or infinite; every score must be a finite number.")
    lo, hi = dev["score_range"]
    if lo < 0 or hi > 100:
        return _refuse(f"rank.learned_score ranges {lo}..{hi}; it must stay within 0-100 so "
                       "the fixed zoning/slope share of the live score is unchanged.")
    best, saved = None, None
    if not new_baseline:
        rows = _kept_rows(fp)
        best, saved = _best_kept(rows), _saved_best()
        if best is None and rows:
            return _refuse("experiments.tsv has kept runs for this evaluation set, but none is in "
                           "this branch's history (reset, or branched from before the baseline). "
                           f"A human must record a new baseline: {NEW_BASELINE}")
        if best is None and saved is not None and saved.get("fingerprint") == fp:
            return _refuse("best_ranking.json is from this evaluation set, but experiments.tsv has "
                           "no kept run for it. A human must record a new baseline: "
                           f"{NEW_BASELINE}")
    if best is not None:
        touched = [f for f in _git("diff", "--name-only", "HEAD~1", "HEAD").splitlines() if f]
        if touched != ["screener/rank.py"]:
            return _refuse("an experiment is one commit that edits only screener/rank.py; "
                           f"HEAD touches: {', '.join(touched) or 'nothing'}.")
        since = [f for f in _git("diff", "--name-only", best["commit"], "HEAD", "--",
                                 "screener").splitlines() if f.startswith(EVAL_FILES)]
        if since:
            return _refuse(f"the evaluation itself changed since the best kept run "
                           f"({', '.join(since)}). A human must record a new baseline: "
                           f"{NEW_BASELINE}")
        if (saved is None or saved.get("commit") != best["commit"]
                or saved.get("fingerprint") != fp):
            return _refuse(f"best_ranking.json is missing, unreadable or not from the best kept "
                           f"run ({best['commit']}), so the keep bar can't be checked. A human "
                           f"must check out {best['commit']} and record it again: {NEW_BASELINE}")

    if best is None:
        status, why, share = "baseline", "recorded as the baseline for this evaluation set", ""
    else:
        best_order = saved["order"]
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
    if not args.control:
        # Before anything imports rank.py: a refused file never runs.
        problems = rank_import_problems()
        if problems:
            return _refuse("screener/rank.py breaks PROGRAM.md rules 1-3, so it was not run: "
                           + "; ".join(problems[:5]))
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
