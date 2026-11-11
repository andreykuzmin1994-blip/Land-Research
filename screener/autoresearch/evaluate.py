"""Score screener/rank.py against the frozen answer key.

LOCKED: the AutoResearch agent runs this but never edits it, and never opens
the files in data/. Usage:

    python -m screener.autoresearch.evaluate             # dev score only (logged as a look)
    python -m screener.autoresearch.evaluate --decide    # score, keep/discard, log it
    python -m screener.autoresearch.evaluate --decide --new-baseline   # humans: record a baseline
    python -m screener.autoresearch.evaluate --test      # sealed test split (humans only; logged)
    python -m screener.autoresearch.evaluate --control   # what random ranking scores
    python -m screener.autoresearch.evaluate --null-test # how often the keep rule keeps noise (humans; logged)

The key (build_eval.py): for each as-of year in COHORTS, the parcels that
year's owner (an owner from before the key's window, not a buyer inside
it) then sold, before any building, to someone who built an industrial
building by 2025. The same site is in several years; a positive record's
weight makes every distinct site sum to 1.

Metric, per as-of year and summed. Parcels are ranked within their year by
rank.learned_score. K is 2% of the year's pool (about what a person can
actually review).
- hits: positives in the top K, by weight.
- graded: each positive earns credit by where it ranks: 1 in the top K,
  falling on a log scale to 0.5 at about 3.2 x K and to 0 at 10 x K (the
  top 20% of the pool; K counts screened-out parcels, so nearer the top
  quarter of the parcels actually ranked). Below that it earns nothing. In
  units of sites.
The hard screens frozen into the evaluation set apply: a parcel they
reject earns nothing. The fixed part of the live score (scoring.py:
zoning, slope, utilities, incentives) is blank in the evaluation set, so
it is the same for every parcel and can't change the order.

Keep rule (--decide): graded must be at least MIN_GAIN above the best kept
run, hits must not be lower, AND the new ranking's graded must beat the
best kept run's in at least WIN_SHARE_TO_KEEP of clustered bootstrap
resamples. Each resample draws whole ~5 km map cells, every year of a cell
together, because positives come in clusters and a site repeats across
years; both rankings are scored on the same draw. The thresholds are
checked with --null-test, so that a change that is not an improvement is
kept about 2% of the time or less; the measured rates, and the key they
were measured on, are in reviews/36_screener_eval_cohorts_metric (07).

What the loop is shown. Scores are printed and logged to one decimal, and
a decision prints the rounded scores only: no win share, and the gain as
the difference of the rounded scores. A finer number would answer "is
there a known site in this band of parcels" one experiment at a time.
Comparisons use the exact values, recomputed from the best kept run's
saved ranking; the exact win share goes to the log, which the loop does
not open. --decide shows nothing until its guards have passed, so a
refused run is not a look. Every other scoring is logged (status "look",
"test" or "null"), with a hash of rank.py, so looks leave a trace.

Guards. Before rank.py runs, a static check (rank_problems) must pass, or
nothing is scored. The bytes that were checked are the bytes that run:
they are compiled here, never loaded through the import system, whose
bytecode cache can hold an older rank.py of the same size and second. The
ranking is handed only the feature fields named in the key's meta, never
a key, year, cell or weight. Every record is scored a second time, in
another order, by a fresh copy of rank.py in a new Python process with
another hash seed: a score that differs (state kept between calls, a
per-process salt) refuses the run. A module loaded from inside the
repository but outside screener/ refuses it too. Git is always asked
about the repository this file is in, whatever the working directory.
--decide also refuses (exit 3, logs nothing) on uncommitted changes
or untracked files under screener/, a commit that touches anything but
screener/rank.py, any other file under screener/ changed since the best
kept run, a score that isn't a finite number in 0-100, a log written for
another version of this evaluator, a commit that was already scored, no
baseline for this key, a key built
before the 2026-11-11 builder rules, or a best kept run whose
experiments.tsv row or best_ranking.json is missing, doesn't match or
doesn't reproduce its logged score. Only a human records a baseline (--new-baseline), the
first one included; it starts a new chain: later experiments are compared
with it, not with older runs.
"""

from __future__ import annotations

import __future__
import argparse
import ast
import codecs
import concurrent.futures
import csv
import gzip
import hashlib
import importlib
import importlib.machinery
import importlib.util
import inspect
import io
import json
import math
import os
import random
import re
import subprocess
import sys
import time
import tokenize
import types
import typing
from pathlib import Path

from .. import screens

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent                    # the repository: git is always asked here
DATA = HERE / "data"
# The as-of years of the key, named here (not found by a glob): a missing
# file is an error, never a smaller key.
COHORTS = (2015, 2016, 2017, 2018, 2019)
TEST_BLOCKS_FILE = DATA / "test_blocks.json"


def features_file(year: int) -> Path:
    return DATA / f"fulton_{year}_features.json.gz"


def labels_file(year: int) -> Path:
    return DATA / f"fulton_{year}_labels.json"


def data_files() -> list[Path]:
    return [f for year in COHORTS for f in (features_file(year), labels_file(year))] \
        + [TEST_BLOCKS_FILE]


LOG_FILE = HERE / "experiments.tsv"          # append-only, untracked
BEST_FILE = HERE / "best_ranking.json"       # ranking of the best kept run, untracked
RANK_FILE = HERE.parent / "rank.py"
# Uncommitted changes and untracked (not ignored) files under screener/.
# The "## <branch>" header line shows that git answered.
STATUS_ARGS = ("status", "--porcelain", "--branch", "--untracked-files=all", "--", "screener")
NEW_BASELINE = "python -m screener.autoresearch.evaluate --decide --new-baseline"
LOG_COLUMNS = ["time", "commit", "parent", "fingerprint", "graded", "hits", "sites",
               "positives", "k", "win_share", "status", "description"]
# The one file an experiment may change. Any other file under screener/
# changed since the best kept run (the evaluator, the key, the screens, a
# package __init__ that runs inside the evaluator) needs a human-recorded
# new baseline (--new-baseline). Documents and tests are not run by the
# evaluator, so they are let through (INERT).
EXPERIMENT_FILE = "screener/rank.py"
INERT_SUFFIX, INERT_FOLDER = ".md", "screener/tests/"
# Log rows that decide something; "look", "test" and "null" rows only leave a trace.
DECISIONS = ("baseline", "keep", "discard")
# What a key built by the current builder carries in its meta (reviews/36, 05
# finding 4): a key without them predates the rule and decides nothing.
BUILDER_META = ("window_start", "deed_file_sha256")
K_SHARE = 0.02
# Credit falls to 0 at CREDIT_SPAN x K: nothing ranked below about the top
# 20% is paid for, so lifting sites through the unread middle earns nothing.
CREDIT_SPAN = 10.0
BOOTSTRAP = 500
# Checked with --null-test (reviews/36, 05 to 07): see there before
# changing, and re-run --null-test after any change to these or to the key.
MIN_GAIN = 2.0
WIN_SHARE_TO_KEEP = 0.99
SHOWN_DECIMALS = 1                           # what the loop is shown; comparisons are exact
NULL_NOISE = (4.0, 8.0, 16.0)                # --null-test: score-point noise levels tried
# --null-test: noise drawn per record, or half of it shared by a map cell in
# every year (a real edit moves a site's years and its neighbours together).
NULL_MODES = ("independent", "cell-shared")
NULL_TRIALS = 1000                           # fewer cannot resolve a 2% target
# Said without the count: how many records disagreed is not the loop's to know.
UNSTABLE = ("rank.learned_score gave some records a different score the second time it was "
            "asked (by a fresh copy, in another order); a score may depend only on the record "
            "it is given.")
# Why --decide refuses for now ("" = it does not). Set while a change to this
# evaluation awaits its review; a human clears it in the change that records
# the review's outcome. Scoring without --decide still works.
REVIEW_PENDING = ("the evaluation's review findings are fixed in code (reviews/36, 05 to 07), "
                  "but the answer key in data/ has not been rebuilt under the new builder "
                  "rules, and the keep thresholds have not been checked on the rebuilt key. No "
                  "experiment may be kept or discarded yet. Stop and tell a human "
                  "(screener/AGENTS_DESIGN.md, \"Next session\")")
# Record fields the ranking never sees. The ranking is handed an allow-list
# (the key's meta "features" and "banned"), so a new record field is hidden
# unless it is added there; this tuple only documents the known ones.
HIDDEN = ("key", "split", "block", "cohort")
# What rank.py may import (PROGRAM.md: pure, no I/O). math is for smooth curves.
RANK_IMPORTS = ("__future__", "math", "typing")
# typing is for annotations only. Its other names build classes, metaclasses
# and forward references (get_origin(Type[int]) is type).
RANK_TYPING_NAMES = {"Any", "Optional", "Union", "Mapping", "Sequence", "Iterable", "Callable",
                     "Dict", "List", "Tuple", "Final", "Literal", "TYPE_CHECKING"}
# Builtins, helpers and attributes that open files, run code, print, build
# classes (type), or reach module internals or the evaluator's stack frames
# (str.format can walk attributes).
RANK_BANNED_NAMES = {"open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars",
                     "getattr", "setattr", "delattr", "breakpoint", "input", "get_type_hints",
                     "ForwardRef", "format", "format_map", "gi_frame", "cr_frame", "ag_frame",
                     "tb_frame", "f_back", "f_globals", "f_locals", "f_builtins", "type",
                     "print", "help", "exit", "quit", "license", "credits", "copyright",
                     # differ from run to run (hash of text is salted per process)
                     "hash", "id"}
# Attributes of generators (gi_), coroutines (cr_), async generators (ag_),
# tracebacks (tb_), frames (f_) and code objects (co_). They lead to the
# evaluator's frames, or rebuild a function so that it reads an attribute
# named by a string (gi_code.replace(co_names=...)).
RANK_FRAME_ATTR = re.compile(r"(gi|cr|ag|tb|f|co)_")
# Strings that are exactly a dunder, a frame/code attribute or one of these
# builtins. Defence in depth only: a computed string gets past it.
RANK_BANNED_STRING_NAMES = {"open", "exec", "eval", "compile", "__import__", "globals", "locals",
                            "vars", "getattr", "setattr", "delattr", "breakpoint"}
RANK_BANNED_STRINGS = ("autoresearch/data", "fulton_20", "test_blocks", "experiments.tsv",
                       "best_ranking")
# What to use instead, in refusal messages.
RANK_HINTS = {"type": " (use isinstance)", "format": " (use an f-string)",
              "format_map": " (use an f-string)", "print": " (put reasons in why_site / why_seller)"}


class SecondPassFailed(Exception):
    """The second scoring pass could not run at all (not: it disagreed)."""


class EvalSetError(Exception):
    """The evaluation files are missing, unreadable or disagree with each other."""


_SAME_IN_EVERY_YEAR = ("features", "banned", "screens", "label_year", "min_price", "cohorts",
                       "built_years", "window_start", "deed_file_sha256")


def load() -> tuple[list[dict], dict[str, float], dict]:
    """(records of every as-of year, weight of each positive key, meta).
    Every year must be present and built together."""
    records: list[dict] = []
    weights: dict[str, float] = {}
    meta0: dict | None = None
    for year in COHORTS:
        try:
            with gzip.open(features_file(year), "rt") as fh:
                data = json.load(fh)
            labels = json.loads(labels_file(year).read_text())
            meta, recs, pos = data["meta"], data["records"], labels["positives"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise EvalSetError(f"the {year} evaluation files are missing or unreadable ({exc})")
        if meta0 is None:
            meta0 = meta
            if tuple(meta.get("cohorts") or ()) != COHORTS:
                raise EvalSetError(f"the key was built for years {meta.get('cohorts')}, "
                                f"this evaluator expects {list(COHORTS)}")
        elif any(meta.get(k) != meta0.get(k) for k in _SAME_IN_EVERY_YEAR):
            raise EvalSetError(f"the {year} files were not built together with the {COHORTS[0]} files")
        if any(r.get("cohort") != year for r in recs) or not isinstance(pos, dict):
            raise EvalSetError(f"the {year} files are not in this evaluator's format")
        records += recs
        weights.update({k: float(w) for k, w in pos.items()})
    assert meta0 is not None
    return records, weights, meta0


def fingerprint() -> str:
    """Identifies the evaluation set (file names and contents); rows from a
    different set never compare."""
    h = hashlib.sha256()
    for path in data_files():
        h.update(path.name.encode() + b"\0")
        h.update(path.read_bytes())
    return h.hexdigest()[:12]


def _weights(positives) -> dict[str, float]:
    """A weight per positive key. A plain set of keys counts 1 each."""
    if isinstance(positives, dict):
        return positives
    return {k: 1.0 for k in positives}


def _tiebreak(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def ranking(records: list[dict], split: str, scorer, settings: dict,
            fields: tuple[str, ...] | list[str], recheck=None
            ) -> tuple[dict[int, list[str]], tuple, int, int]:
    """Per as-of year, the keys of the split's parcels that pass the screens,
    best first; the (min, max) finite score seen; how many scores were NaN
    or infinite; and how many records scored differently on a second pass.
    The scorer is handed only `fields` of a record: an allow-list, so a
    record field it does not name never reaches rank.py. One NaN anywhere
    scrambles the sort, so those rank last and --decide refuses the run.
    The second pass scores the same records in another order: a scorer
    that counts its calls, remembers what it saw or salts its answer gives
    itself away there, and the run is refused. `recheck` does that pass
    (it is handed the feature rows in the new order and returns their
    scores; main() gives one that asks a fresh rank.py in a new process).
    Without it the same scorer is asked again here."""
    ranked = [r for r in records if r["split"] == split and not screens.site_rejects(r, settings)]

    def features_of(r: dict) -> dict:
        return {k: r.get(k) for k in fields}

    first = [float(scorer(features_of(r))["score"]) for r in ranked]
    again = list(range(len(ranked)))
    # An order nobody can work out beforehand: with a fixed seed, a rank.py
    # could rebuild the shuffle and still know each record's place in the file.
    random.SystemRandom().shuffle(again)
    rows = [features_of(ranked[i]) for i in again]
    seconds = (recheck(rows) if recheck is not None
               else [float(scorer(row)["score"]) for row in rows])
    if seconds is None or len(seconds) != len(again):
        raise SecondPassFailed("the second scoring pass did not return one score per record")
    unstable = 0
    for i, second in zip(again, seconds):
        if second != first[i] and not (math.isnan(second) and math.isnan(first[i])):
            unstable += 1
    scored: dict[int, list[tuple]] = {}
    nonfinite = 0
    for r, score in zip(ranked, first):
        if not math.isfinite(score):
            nonfinite += 1
            score = -math.inf
        scored.setdefault(r.get("cohort", 0), []).append((-score, _tiebreak(r["key"]), r["key"]))
    finite = [-s for rows in scored.values() for s, _, _ in rows if math.isfinite(s)]
    span = (min(finite), max(finite)) if finite else (0.0, 0.0)
    return ({c: [key for _, _, key in sorted(rows)] for c, rows in scored.items()}, span,
            nonfinite, unstable)


def credit(ahead: int, k: int) -> float:
    """What a positive earns with `ahead` pool parcels ranked above it: 1 in
    the top k, then 1 - log(position / k) / log(CREDIT_SPAN), never below 0."""
    if ahead < k:
        return 1.0
    return max(0.0, 1.0 - math.log((ahead + 1) / k) / math.log(CREDIT_SPAN))


def _counts(pool_keys: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for key in pool_keys:
        counts[key] = counts.get(key, 0) + 1
    return counts


def score_orders(orders: dict[int, list[str]], counts: dict[int, dict[str, int]],
                 weights: dict[str, float]) -> tuple[float, float, int]:
    """(hits, graded, K summed over years) of per-year rankings against
    per-year pools given as key -> copies (a bootstrap draw repeats keys).
    A parcel missing from a ranking (screened out) earns nothing."""
    hits = graded = 0.0
    k_total = 0
    for cohort, pool in counts.items():
        size = sum(pool.values())
        if not size:
            continue
        k = max(1, round(K_SHARE * size))
        k_total += k
        stop = k * CREDIT_SPAN
        ahead = 0
        for key in orders.get(cohort, ()):
            n = pool.get(key, 0)
            if not n:
                continue
            w = weights.get(key)
            if w:
                hits += w * max(0, min(n, k - ahead))
                graded += w * sum(credit(ahead + i, k) for i in range(n))
            ahead += n
            if ahead >= stop:
                break
    return hits, graded, k_total


def hits_at_k(order: list[str], pool_keys: list[str], positives) -> tuple[float, int]:
    """Hits in the top K of one ranking restricted to `pool_keys` (with repeats)."""
    hits, _, k = score_orders({0: order}, {0: _counts(pool_keys)}, _weights(positives))
    return hits, k


def split_pools(records: list[dict], split: str) -> dict[int, dict[str, int]]:
    """Per as-of year, the split's pool as key -> 1."""
    pools: dict[int, dict[str, int]] = {}
    for r in records:
        if r["split"] == split:
            pools.setdefault(r.get("cohort", 0), {})[r["key"]] = 1
    return pools


def evaluate(records: list[dict], positives, split: str, *, fields: tuple[str, ...] | list[str],
             scorer=None, settings: dict | None = None, recheck=None) -> dict:
    """Score one split. "hits", "graded" and "score_span" are exact (decide
    uses them); main() rounds what it prints. `fields` is what the scorer
    may see of a record, and must be given. With no scorer, the checked
    rank.py is used (and refused, with ValueError, if it breaks the rules)."""
    if scorer is None:
        problems, source = rank_source()
        if problems:
            raise ValueError("screener/rank.py breaks the rules: " + "; ".join(problems[:3]))
        scorer = load_scorer(source)
    if settings is None:
        settings = load_meta_screens()
    weights = _weights(positives)
    pools = split_pools(records, split)
    in_split = {key for pool in pools.values() for key in pool}
    pos = {k for k in in_split if weights.get(k)}
    orders, span, nonfinite, unstable = ranking(records, split, scorer, settings, fields, recheck)
    hits, graded, k = score_orders(orders, pools, weights)
    ranked = {key for order in orders.values() for key in order}
    sites = sum(weights[k] for k in pos)
    return {
        "split": split, "pool": len(in_split), "positives": len(pos),
        "sites": round(sites, 2), "k": k, "hits": hits, "graded": graded,
        "positives_screened_out": len(pos - ranked),
        "score_range": [round(span[0], 1), round(span[1], 1)], "score_span": list(span),
        "nonfinite_scores": nonfinite, "unstable_scores": unstable,
        "order": orders,
    }


def load_meta() -> dict:
    with gzip.open(features_file(COHORTS[0]), "rt") as fh:
        return json.load(fh)["meta"]


def load_meta_screens() -> dict:
    return load_meta()["screens"]


def _blocks(records: list[dict], split: str) -> dict[str, dict[int, list[str]]]:
    """block -> year -> keys: a cell is drawn with every year it holds."""
    blocks: dict[str, dict[int, list[str]]] = {}
    for r in records:
        if r["split"] == split:
            blocks.setdefault(r["block"], {}).setdefault(r.get("cohort", 0), []).append(r["key"])
    return blocks


def _draws(blocks: dict[str, dict[int, list[str]]], rng: random.Random):
    """BOOTSTRAP clustered resamples, each as year -> key -> copies."""
    names = sorted(blocks)
    for _ in range(BOOTSTRAP):
        counts: dict[int, dict[str, int]] = {}
        for _ in names:
            for cohort, keys in blocks[rng.choice(names)].items():
                pool = counts.setdefault(cohort, {})
                for key in keys:
                    pool[key] = pool.get(key, 0) + 1
        yield counts


def paired_win_share(records: list[dict], positives, new: dict[int, list[str]],
                     best: dict[int, list[str]], split: str = "dev") -> float:
    """Share of clustered bootstrap draws in which `new` has more graded
    credit than `best`, both scored on the same draw."""
    weights = _weights(positives)
    wins = 0
    for counts in _draws(_blocks(records, split), random.Random(7)):
        if score_orders(new, counts, weights)[1] > score_orders(best, counts, weights)[1] + 1e-9:
            wins += 1
    return wins / BOOTSTRAP


def keep_verdict(records: list[dict], positives, new: dict[int, list[str]],
                 best: dict[int, list[str]], split: str = "dev") -> dict:
    """The keep rule, on exact values: both rankings scored here, now."""
    weights = _weights(positives)
    pools = split_pools(records, split)
    new_hits, new_graded, _ = score_orders(new, pools, weights)
    best_hits, best_graded, _ = score_orders(best, pools, weights)
    share = paired_win_share(records, weights, new, best, split)
    keep = (new_graded - best_graded >= MIN_GAIN - 1e-9 and new_hits >= best_hits - 1e-9
            and share >= WIN_SHARE_TO_KEEP)
    return {"keep": keep, "gain": new_graded - best_graded, "hits_change": new_hits - best_hits,
            "win_share": share, "best_graded": best_graded, "best_hits": best_hits}


def null_test(records: list[dict], positives, base: dict[str, float], trials: int,
              noise: float, split: str = "dev", mode: str = "independent") -> dict:
    """How often the keep rule keeps a change that is not an improvement.
    Each trial compares two rankings made the same way: the scores in
    `base` (key -> score) plus noise of `noise` score points. Neither is
    better, so every keep is a false keep. mode "independent" draws the
    noise per record; "cell-shared" gives half its variance to a draw
    shared by a map cell in every year, as a real edit moves a site's
    years and its neighbours together (the harder case: reviews/36, 05)."""
    if mode not in NULL_MODES:
        raise ValueError(f"mode must be one of {NULL_MODES}")
    by_cohort: dict[int, list[str]] = {}
    block_of: dict[str, str] = {}
    for r in records:
        if r["split"] == split and r["key"] in base:
            by_cohort.setdefault(r.get("cohort", 0), []).append(r["key"])
            block_of[r["key"]] = r["block"]
    blocks = sorted(set(block_of.values()))
    rng = random.Random(f"36:{mode}:{noise}")
    own = noise if mode == "independent" else noise * math.sqrt(0.5)

    def noisy() -> dict[int, list[str]]:
        shared = {b: (0.0 if mode == "independent" else rng.gauss(0.0, noise * math.sqrt(0.5)))
                  for b in blocks}
        return {c: sorted(keys, key=lambda k: (-(base[k] + shared[block_of[k]]
                                                 + rng.gauss(0.0, own)), _tiebreak(k)))
                for c, keys in by_cohort.items()}

    kept, gains = 0, []
    for _ in range(trials):
        verdict = keep_verdict(records, positives, noisy(), noisy(), split)
        kept += verdict["keep"]
        gains.append(verdict["gain"])
    gains.sort()
    return {"mode": mode, "noise": noise, "trials": trials, "false_keeps": kept,
            "false_keep_rate": round(kept / trials, 4),
            "gain_p5_p95": [round(gains[int(0.05 * (trials - 1))], 2),
                            round(gains[int(0.95 * (trials - 1))], 2)]}


def rank_problems(source: str | bytes) -> list[str]:
    """Why rank.py's source breaks PROGRAM.md rules 1-3, if it does.

    It reads the source before rank.py is run (bytes are parsed the way
    Python compiles them). It refuses:
    - imports other than __future__, math and typing; typing names outside
      RANK_TYPING_NAMES; module objects or private names through them;
    - the names in RANK_BANNED_NAMES (open, eval, exec, compile, getattr,
      globals, vars, type, print, str.format, frame attributes...), dunder
      names, and private (_x) or frame/code (gi_, cr_, ag_, tb_, f_, co_)
      attributes;
    - match statements, dunder function or class names, class keywords
      (metaclass=), and dunder or co_ keyword arguments;
    - binding a dunder name with import ... as or except ... as (import
      typing as __builtins__ swaps the builtins of later functions);
    - assigning to or deleting an attribute of an imported module or name
      (math.isfinite = ...), and global / nonlocal state;
    - strings (docstrings aside) that name the evaluation files, or that are
      exactly a dunder, a frame/code attribute or a banned builtin.
    It guards against drift and is not a sandbox: these are the routes known
    to read attributes by a string or to reach the evaluator's frames, not
    every possible one.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [f"does not parse ({e.msg}, line {e.lineno})"]
    except ValueError as e:                     # null bytes (Python 3.11)
        return [f"does not parse ({e})"]
    modules = {name for m in (__future__, math, typing) for name, value in vars(m).items()
               if inspect.ismodule(value)}
    typing_hint = f" (typing is for annotations: {', '.join(sorted(RANK_TYPING_NAMES))})"

    def dunder(name: str) -> bool:
        return name[:2] == name[-2:] == "__"

    def banned_attr(name: str) -> bool:
        return (name.startswith("_") or name in modules or name in RANK_BANNED_NAMES
                or bool(RANK_FRAME_ATTR.match(name)))

    # Each name an import binds, and what it binds ("typing" is the module).
    bound = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bound.update({a.asname or a.name.split(".")[0]: a.name for a in node.names})
        elif isinstance(node, ast.ImportFrom):
            bound.update({a.asname or a.name: f"{node.module}.{a.name}" for a in node.names})
    module_names = {name for name, what in bound.items() if what in RANK_IMPORTS}
    attr_bases = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                       ast.AsyncFunctionDef))
                  and node.body and isinstance(node.body[0], ast.Expr)}
    problems = []
    for node in ast.walk(tree):
        where = f"line {getattr(node, 'lineno', '?')}"
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            # import typing as __builtins__: functions defined after it look up
            # bare names (sys, operator) in typing's namespace.
            problems += [f"{where}: imports {a.name} as {a.asname} (no dunder names)"
                         for a in node.names if a.asname and dunder(a.asname)]
        if isinstance(node, ast.Import):
            problems += [f"{where}: imports {a.name}" for a in node.names
                         if a.name not in RANK_IMPORTS]
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module not in RANK_IMPORTS:
                problems.append(f"{where}: imports from {'.' * node.level}{node.module or ''}")
            for a in node.names:
                if a.name == "*" or banned_attr(a.name):
                    problems.append(f"{where}: imports {a.name} from {node.module}")
                elif node.module == "typing" and a.name not in RANK_TYPING_NAMES:
                    problems.append(f"{where}: imports {a.name} from typing{typing_hint}")
        elif isinstance(node, ast.Name):
            if node.id in RANK_BANNED_NAMES or dunder(node.id):
                problems.append(f"{where}: uses {node.id}{RANK_HINTS.get(node.id, '')}")
            elif (node.id in module_names and isinstance(node.ctx, ast.Load)
                  and id(node) not in attr_bases):     # m = math; m.isfinite = ...
                problems.append(f"{where}: uses the module {node.id} as a value "
                                f"(use {node.id}.<name>)")
        elif isinstance(node, ast.Attribute):
            if banned_attr(node.attr):
                problems.append(f"{where}: uses .{node.attr}{RANK_HINTS.get(node.attr, '')}")
            elif (isinstance(node.value, ast.Name) and bound.get(node.value.id) == "typing"
                  and node.attr not in RANK_TYPING_NAMES):
                problems.append(f"{where}: uses {node.value.id}.{node.attr}{typing_hint}")
            if (isinstance(node.ctx, (ast.Store, ast.Del)) and isinstance(node.value, ast.Name)
                    and node.value.id in bound):
                problems.append(f"{where}: changes {node.value.id}.{node.attr} (imported modules "
                                "and names are shared with the evaluator; leave them as they are)")
        elif isinstance(node, ast.Match):          # a class pattern reads attributes
            problems.append(f"{where}: uses a match statement (use if/elif)")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if dunder(node.name):                  # __getattr__, __instancecheck__, ...
                problems.append(f"{where}: defines {node.name} (no dunder methods or hooks; "
                                "use plain functions)")
            if isinstance(node, ast.ClassDef):
                problems += [f"{where}: class {node.name} takes {k.arg or '**'}= (no class "
                             "keywords such as metaclass=)" for k in node.keywords]
        elif (isinstance(node, ast.keyword) and node.arg
              and (dunder(node.arg) or node.arg.startswith("co_"))):
            problems.append(f"{where}: passes {node.arg}=")
        elif isinstance(node, ast.ExceptHandler) and node.name and dunder(node.name):
            problems.append(f"{where}: binds {node.name} (no dunder names)")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            problems.append(f"{where}: keeps state between calls ({', '.join(node.names)})")
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
              and id(node) not in docstrings):
            if any(name in node.value for name in RANK_BANNED_STRINGS):
                problems.append(f"{where}: names an evaluation file ({node.value!r})")
            elif (re.fullmatch(r"__\w+__", node.value) or node.value in RANK_BANNED_STRING_NAMES
                  or re.fullmatch(RANK_FRAME_ATTR.pattern + r"\w+", node.value)):
                problems.append(f"{where}: string {node.value!r} names a dunder, a frame/code "
                                "attribute or a refused builtin")
    return problems


def _rank_origin() -> str | None:
    """The file `import screener.rank` would load, found without running it."""
    try:
        spec = importlib.util.find_spec("screener.rank")
    except (ImportError, ValueError):
        return None
    return spec.origin if spec else None


def rank_source() -> tuple[list[str], bytes]:
    """(rank_problems for screener/rank.py, the bytes they were found in).
    The file is read once, the way the importer reads it: its bytes, decoded
    by their own coding declaration, which must be UTF-8. (Under
    '# coding: utf-7', text that reads as a comment can decode to a line of
    code.) Those bytes are what load_scorer runs."""
    origin = _rank_origin()
    if not origin or Path(origin).resolve() != RANK_FILE.resolve():
        return [f"screener.rank would load from {origin}, not screener/rank.py"], b""
    if RANK_FILE.is_symlink():
        return ["is a link to another file; rank.py must be the file itself"], b""
    data = RANK_FILE.read_bytes()
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
    except SyntaxError as e:
        return [f"has an unreadable coding declaration ({e})"], b""
    if codecs.lookup(encoding).name not in ("utf-8", "utf-8-sig"):
        return [f"declares the {encoding} encoding; rank.py must be UTF-8"], b""
    return rank_problems(data), data


def rank_import_problems() -> list[str]:
    """Why the rank.py on disk may not be run (rank_source without the bytes)."""
    return rank_source()[0]


def load_scorer(source: bytes):
    """rank.learned_score made from exactly these bytes. They are compiled
    and run here, never loaded through the import system: its bytecode cache
    is keyed by the file's size and whole-second time, so a rank.py edited
    within a second of the last run, to the same length, would otherwise
    score as the old one (reviews/36, 06, N2). A planted .pyc and a file
    changed between the check and the import are closed the same way."""
    module = types.ModuleType("screener.rank")
    module.__file__ = str(RANK_FILE)
    exec(compile(source, str(RANK_FILE), "exec", dont_inherit=True), module.__dict__)  # noqa: S102
    return module.learned_score


_SECOND_PASS = r"""
import json, sys, types
job = json.load(sys.stdin)
module = types.ModuleType("screener.rank")
exec(compile(job["source"], "rank.py", "exec", dont_inherit=True), module.__dict__)
out = []
for features in job["rows"]:
    try:
        out.append(float(module.learned_score(features)["score"]).hex())
    except Exception as exc:
        out.append("error " + type(exc).__name__)
json.dump(out, sys.stdout)
"""


def second_pass(source: bytes):
    """A recheck for ranking(): scores rows with a fresh copy of these
    rank.py bytes in a new Python process under another hash seed. A score
    that leans on what an earlier call saw, on the order of calls, or on
    anything that differs between processes (set order, an address in a
    repr) comes back different. A row that raises there counts as
    different. A pass that cannot run returns None, and nothing is scored:
    that is a broken setup, not an unstable rank.py. The new process gets
    no PYTHONPATH, so nothing beside the standard library is on its path."""
    text = source.decode("utf-8-sig")

    def recheck(rows: list[dict]) -> list[float] | None:
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONSTARTUP")}
        env["PYTHONHASHSEED"] = "1" if os.environ.get("PYTHONHASHSEED") != "1" else "2"
        try:
            done = subprocess.run([sys.executable, "-P", "-c", _SECOND_PASS],
                                  input=json.dumps({"source": text, "rows": rows}),
                                  capture_output=True, text=True, timeout=600, cwd=str(HERE),
                                  env=env)
            scores = json.loads(done.stdout) if done.returncode == 0 else None
        except (OSError, subprocess.SubprocessError, ValueError):
            scores = None
        if not isinstance(scores, list) or len(scores) != len(rows):
            return None
        out = []
        for value in scores:
            try:
                out.append(float.fromhex(value))
            except (TypeError, ValueError):
                out.append(math.inf)
        return out

    return recheck


def foreign_modules() -> list[str]:
    """What could run inside the evaluator from the repository but outside
    screener/, where the git checks do not look: a file or folder at the
    repository root named like a standard-library module (random.py,
    csv.py: it would be imported in its place, whatever it then says about
    itself), and any loaded module whose file is inside the repository but
    outside screener/ and is not an installed package."""
    inside = ROOT / "screener"
    installed = {Path(p).resolve() for p in (sys.prefix, sys.base_prefix, sys.exec_prefix)}
    out = []
    try:
        for entry in ROOT.iterdir():
            if entry.is_dir():
                # A folder is importable in a standard module's place only as
                # a package (venv/ or site/ without an __init__ is not).
                shadows = (entry.name in sys.stdlib_module_names
                           and any(entry.glob("__init__.*")))
            else:
                # csv.py, csv.pyc, csv.cpython-313-x86_64-linux-gnu.so, ...
                shadows = any(entry.name.endswith(suffix)
                              and entry.name[:-len(suffix)] in sys.stdlib_module_names
                              for suffix in importlib.machinery.all_suffixes())
            if shadows:
                out.append(f"{entry.name} (shadows a standard module)")
    except OSError:
        pass
    for name, module in list(sys.modules.items()):
        file = getattr(module, "__file__", None)
        if not isinstance(file, str):
            continue
        try:
            path = Path(file).resolve()
        except OSError:
            continue
        parents = set(path.parents)
        if (ROOT in parents and inside not in parents and "site-packages" not in path.parts
                and not parents & installed):
            out.append(name)
    return sorted(out)


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True,
                              check=True, cwd=str(ROOT)).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _is_ancestor(commit: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", commit, "HEAD"],
                          capture_output=True, cwd=str(ROOT)).returncode == 0


def _rows() -> list[dict]:
    if not LOG_FILE.exists():
        return []
    with open(LOG_FILE, newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def _log_header_problem() -> str | None:
    """Why the existing log can't be appended to (None if it can, or there is none)."""
    if not LOG_FILE.exists() or LOG_FILE.stat().st_size == 0:
        return None
    with open(LOG_FILE, newline="") as fh:
        header = next(csv.reader(fh, delimiter="\t"), None)
    if header != LOG_COLUMNS:
        return (f"{LOG_FILE.name} was written by another version of this evaluator (its "
                "columns differ). A human must move it aside (keep it: it is the record of "
                f"earlier runs) and record a new baseline: {NEW_BASELINE}")
    return None


def _decisions() -> list[dict]:
    """The log's baseline, keep and discard rows (not the looks)."""
    return [r for r in _rows() if r["status"] in DECISIONS]


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
    # Every keep beat the run before it by at least MIN_GAIN, so the last
    # row of the chain is the best. (The logged scores are rounded and are
    # never compared: decide() rescoring the saved ranking is.)
    return chain[-1]


def _saved_best() -> dict | None:
    """best_ranking.json, or None if it is missing or unreadable."""
    try:
        saved = json.loads(BEST_FILE.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(saved, dict) or not isinstance(saved.get("order"), dict):
        return None
    try:   # JSON keys are text; the as-of years are whole numbers
        saved["order"] = {int(c): [str(k) for k in keys] for c, keys in saved["order"].items()}
    except (TypeError, ValueError):
        return None
    return saved


def _append(row: dict) -> None:
    new = not LOG_FILE.exists() or LOG_FILE.stat().st_size == 0
    with open(LOG_FILE, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=LOG_COLUMNS, delimiter="\t")
        if new:
            w.writeheader()
        w.writerow(row)


def _refuse(msg: str) -> int:
    print(f"REFUSED: {msg}")
    return 3


def _shown(x: float) -> float:
    return round(x, SHOWN_DECIMALS)


def _note_look(status: str, res: dict) -> str | None:
    """Log a scoring that decided nothing ("look": dev without --decide;
    "test": the sealed split; "null": a --null-test, logged before it
    runs), with a hash of the rank.py that was scored.
    Returns why it could not be logged (then nothing is shown), or None."""
    problem = _log_header_problem()
    if problem:
        return problem
    try:
        source = hashlib.sha256(RANK_FILE.read_bytes()).hexdigest()[:12]
    except OSError:
        source = "unreadable"
    dirty = bool(_git("status", "--porcelain", "--", EXPERIMENT_FILE))
    _append({
        "time": time.strftime("%Y-%m-%d %H:%M:%S"), "commit": _git("rev-parse", "--short", "HEAD"),
        "parent": _git("rev-parse", "--short", "HEAD~1"), "fingerprint": fingerprint(),
        "graded": _shown(res["graded"]), "hits": _shown(res["hits"]), "sites": res["sites"],
        "positives": res["positives"], "k": res["k"], "win_share": "", "status": status,
        "description": f"rank.py {source}" + (" (uncommitted)" if dirty else ""),
    })
    return None


def decide(records: list[dict], positives, dev: dict, new_baseline: bool) -> int:
    if REVIEW_PENDING:
        return _refuse(REVIEW_PENDING + ".")
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
    header_problem = _log_header_problem()
    if header_problem:
        return _refuse(header_problem)
    # A human may re-record an already scored commit (the best kept run).
    if not new_baseline and any(r["commit"] == head and r["fingerprint"] == fp
                                for r in _decisions()):
        return _refuse(f"commit {head} was already scored. Make a new experiment commit.")
    if dev.get("nonfinite_scores"):
        return _refuse(f"rank.learned_score returned {dev['nonfinite_scores']} scores that are NaN "
                       "or infinite; every score must be a finite number.")
    if dev.get("unstable_scores"):
        return _refuse(UNSTABLE)
    lo, hi = dev.get("score_span") or dev["score_range"]
    if lo < 0 or hi > 100:
        return _refuse(f"rank.learned_score ranges {lo:.1f}..{hi:.1f}; it must stay within 0-100 "
                       "so the fixed zoning/slope share of the live score is unchanged.")
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
        if best is None and _decisions():
            # The log holds runs, all of another evaluation set: the key changed.
            return _refuse("the evaluation set changed since the runs in experiments.tsv. A "
                           f"human must record a new baseline: {NEW_BASELINE}")
        if best is None:
            # Never a baseline by default: the log and the saved ranking are
            # untracked, so a new container or a deleted log would otherwise
            # quietly reset the bar to whatever HEAD holds.
            return _refuse("there is no baseline for this evaluation set here (no "
                           "experiments.tsv row). Only a human records one, on the commit the "
                           f"loop starts from: {NEW_BASELINE}")
    if best is not None:
        touched = [f for f in _git("diff", "--name-only", "HEAD~1", "HEAD").splitlines() if f]
        if touched != [EXPERIMENT_FILE]:
            return _refuse("an experiment is one commit that edits only screener/rank.py; "
                           f"HEAD touches: {', '.join(touched) or 'nothing'}.")
        since = [f for f in _git("diff", "--name-only", "--no-renames", best["commit"], "HEAD",
                                 "--", "screener").splitlines()
                 if f and f != EXPERIMENT_FILE
                 and not f.endswith(INERT_SUFFIX) and not f.startswith(INERT_FOLDER)]
        if since:
            return _refuse(f"files other than screener/rank.py changed since the best kept run "
                           f"({', '.join(since[:5])}). A human must record a new baseline: "
                           f"{NEW_BASELINE}")
        if (saved is None or saved.get("commit") != best["commit"]
                or saved.get("fingerprint") != fp):
            return _refuse(f"best_ranking.json is missing, unreadable or not from the best kept "
                           f"run ({best['commit']}), so the keep bar can't be checked. A human "
                           f"must check out {best['commit']} and record it again: {NEW_BASELINE}")

    if best is None:
        status, share = "baseline", ""
        why = (f"recorded as the baseline for this evaluation set: graded "
               f"{_shown(dev['graded'])}, hits {_shown(dev['hits'])}")
        # A human runs this; say what it replaces, so a baseline is never a
        # silent way to bless whatever rank.py is at HEAD.
        before = _best_kept(_kept_rows(fp))
        if before is not None and before["commit"] != head:
            changed = bool(_git("diff", "--name-only", before["commit"], "HEAD", "--",
                                EXPERIMENT_FILE))
            print(f"NOTE: the best kept run before this was {before['commit']} (graded "
                  f"{before['graded']}, hits {before['hits']}). rank.py "
                  + ("DIFFERS from it: this baseline also accepts that change, unjudged."
                     if changed else "is the same as there."))
    else:
        # Both rankings are scored here, on exact values; the log's rounded
        # numbers are never compared.
        v = keep_verdict(records, positives, dev["order"], saved["order"])
        try:
            logged = (float(best["graded"]), float(best["hits"]))
        except (KeyError, TypeError, ValueError):
            logged = None
        if logged != (_shown(v["best_graded"]), _shown(v["best_hits"])):
            return _refuse(f"best_ranking.json does not give the score logged for the best kept "
                           f"run ({best['commit']}), so it is not that run's ranking. A human "
                           f"must check out {best['commit']} and record it again: {NEW_BASELINE}")
        # Shown: rounded scores and their difference, nothing finer (the
        # exact gain's sign and the win share would tell one experiment
        # whether a band of parcels holds a known site). The log has the share.
        shown_gain = _shown(_shown(dev["graded"]) - _shown(v["best_graded"]))
        detail = (f"graded {_shown(dev['graded'])} vs best kept {_shown(v['best_graded'])} "
                  f"({shown_gain:+.1f}); hits {_shown(dev['hits'])} vs {_shown(v['best_hits'])}")
        needs = (f"needs +{MIN_GAIN:g} graded, hits not lower, and a win in "
                 f"{WIN_SHARE_TO_KEEP:.0%} of resamples")
        if v["keep"]:
            status, why = "keep", detail
        else:
            status, why = "discard", f"{detail} ({needs})"
        share = f"{v['win_share']:.3f}"

    _append({
        "time": time.strftime("%Y-%m-%d %H:%M:%S"), "commit": head, "parent": parent,
        "fingerprint": fp, "graded": _shown(dev["graded"]), "hits": _shown(dev["hits"]),
        "sites": dev["sites"], "positives": dev["positives"], "k": dev["k"],
        "win_share": share, "status": status,
        "description": _git("log", "-1", "--format=%s"),
    })
    if status in ("baseline", "keep"):
        BEST_FILE.write_text(json.dumps({"commit": head, "fingerprint": fp, "order": dev["order"]}))
    print(f"DECISION: {status.upper()} — {why}")
    if status == "discard":
        # Take back the experiment and nothing else. HEAD~1 holds the best
        # kept rank.py unless an earlier discard was left in place; a human's
        # commit to a document or a test may sit between the two.
        if not _git("diff", "--name-only", best["commit"], parent, "--", EXPERIMENT_FILE):
            print(f"Revert the experiment: git reset --hard {parent}")
        else:
            # An earlier discard was left in place. Going back to the best
            # kept run removes every commit since: say so only when each one
            # is a discard this log recorded. Otherwise a human sorts it out.
            between = [line.split()[0] for line in
                       _git("log", "--format=%h", f"{best['commit']}..{parent}").splitlines()
                       if line.strip()]
            discards = {r["commit"] for r in _decisions()
                        if r["status"] == "discard" and r["fingerprint"] == fp}
            if between and all(c in discards for c in between):
                print(f"Revert the experiment: git reset --hard {best['commit']}")
                print(f"(this also takes back {len(between)} earlier discarded experiment(s) "
                      "that were left in place)")
            else:
                print("Do not reset. Stop and ask a human: commits that are not discarded "
                      f"experiments lie between the best kept run ({best['commit']}) and this "
                      "one, and undoing this experiment by a reset would remove them.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--decide", action="store_true", help="compare to the best kept run and log")
    ap.add_argument("--test", action="store_true", help="also score the sealed test split")
    ap.add_argument("--control", action="store_true", help="what random ranking scores")
    ap.add_argument("--null-test", type=int, nargs="?", const=NULL_TRIALS, default=0,
                    metavar="TRIALS",
                    help="(humans only) how often the keep rule keeps a change that is not an "
                         f"improvement, at several noise levels (default {NULL_TRIALS} trials "
                         "each; slow: tens of minutes)")
    ap.add_argument("--new-baseline", action="store_true",
                    help="(humans only) record the current ranking as a fresh baseline")
    args = ap.parse_args(argv)

    if not all(f.exists() for f in data_files()):
        print("No complete evaluation set: run `python -m screener.autoresearch.build_eval` "
              "first (it needs data/test_blocks.json from git).")
        return 2
    source = b""
    if not args.control:
        # Before rank.py runs: a refused file never does.
        problems, source = rank_source()
        if problems:
            return _refuse("screener/rank.py breaks PROGRAM.md rules 1-3, so it was not run: "
                           + "; ".join(problems[:5]))
    strays = foreign_modules()
    if strays:
        return _refuse("modules were loaded from inside the repository but outside screener/ ("
                       + ", ".join(strays[:5]) + "): run from a clean checkout; a file there "
                       "that shadows a standard module would run inside the evaluator.")
    try:
        records, positives, meta = load()
    except EvalSetError as exc:
        print(f"The evaluation set can't be used: {exc}. Re-run "
              "`python -m screener.autoresearch.build_eval` (humans).")
        return 2
    fields = list(meta["features"]) + list(meta["banned"])
    settings = meta["screens"]

    def public(res: dict) -> dict:
        out = {k: v for k, v in res.items() if k not in ("order", "score_span")}
        out["hits"], out["graded"] = _shown(res["hits"]), _shown(res["graded"])
        return out

    if args.control:
        base = evaluate(records, positives, "dev", scorer=lambda f: {"score": 0.0},
                        settings=settings, fields=fields)
        hits, graded = [], []
        for seed in range(200):
            rng = random.Random(seed)
            res = evaluate(records, positives, "dev", scorer=lambda f: {"score": rng.random()},
                           settings=settings, fields=fields)
            hits.append(res["hits"])
            graded.append(res["graded"])
        hits.sort()
        graded.sort()
        print(json.dumps({"control": {
            "k": base["k"], "sites": base["sites"],
            "mean_graded": round(sum(graded) / len(graded), 2),
            "graded_p5_p95": [round(graded[10], 2), round(graded[189], 2)],
            "mean_hits": round(sum(hits) / len(hits), 2),
            "hits_p5_p95": [round(hits[10], 2), round(hits[189], 2)]}}))
        return 0

    if args.new_baseline and not args.decide:
        print("--new-baseline only applies with --decide")
        return 2
    if args.decide and args.test:
        print("--test does not go with --decide: a decision shows only the dev scores")
        return 2
    if args.decide and REVIEW_PENDING:
        return _refuse(REVIEW_PENDING + ".")
    if args.decide and not all(meta.get(k) for k in BUILDER_META):
        return _refuse("the key in data/ was built before the 2026-11-11 builder rules (its meta "
                       "has no " + " / ".join(BUILDER_META) + "). A human must rebuild it "
                       "(python -m screener.autoresearch.build_eval) and record a new baseline.")
    scorer, recheck = load_scorer(source), second_pass(source)
    try:
        dev = evaluate(records, positives, "dev", scorer=scorer, settings=settings,
                       fields=fields, recheck=recheck)
        test = (evaluate(records, positives, "test", scorer=scorer, settings=settings,
                         fields=fields, recheck=recheck)
                if args.test and not args.null_test else None)
    except SecondPassFailed:
        return _refuse("the second scoring pass (a fresh rank.py in a new Python process) could "
                       "not run, so nothing was scored. Either this Python cannot start itself, "
                       "or rank.py fails or hangs when it is loaded on its own.")
    if dev["unstable_scores"] or (test and test["unstable_scores"]):
        return _refuse(UNSTABLE)
    if args.decide:
        # Nothing is shown before decide()'s guards pass: a refused run is not a look.
        return decide(records, positives, dev, args.new_baseline)
    if args.null_test:
        if args.null_test < 20:
            print("--null-test needs at least 20 trials")
            return 2
        problem = _note_look("null", dev)      # its output depends on the labels: leave a row
        if problem:
            return _refuse(problem)
        base = {}
        for r in records:
            if r["split"] == "dev" and not screens.site_rejects(r, settings):
                score = float(scorer({k: r.get(k) for k in fields})["score"])
                base[r["key"]] = score if math.isfinite(score) else 0.0
        jobs = [(records, positives, base, args.null_test, noise, "dev", mode)
                for mode in NULL_MODES for noise in NULL_NOISE]
        with concurrent.futures.ProcessPoolExecutor() as workers:
            results = list(workers.map(_null_job, jobs))
        print(json.dumps({"keep_rule": {"min_gain": MIN_GAIN, "win_share": WIN_SHARE_TO_KEEP,
                                        "hits_may_not_fall": True},
                          "fingerprint": fingerprint(), "null_test": results}))
        return 0
    # Every scoring leaves a row: looks are otherwise free, unlimited questions.
    problem = _note_look("look", dev) or (test and _note_look("test", test)) or None
    if problem:
        return _refuse(problem)
    out = {"dev": public(dev)}
    if test:
        out["test"] = public(test)
    print(json.dumps(out))
    return 0


def _null_job(job: tuple) -> dict:
    return null_test(*job)


if __name__ == "__main__":
    sys.exit(main())
