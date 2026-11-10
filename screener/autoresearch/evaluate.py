"""Score screener/rank.py against the frozen answer key.

LOCKED: the AutoResearch agent runs this but never edits it, and never opens
the files in data/. Usage:

    python -m screener.autoresearch.evaluate             # dev score only
    python -m screener.autoresearch.evaluate --decide    # score, keep/discard, log it
    python -m screener.autoresearch.evaluate --test      # sealed test split (humans only)
    python -m screener.autoresearch.evaluate --control   # what random ranking scores
    python -m screener.autoresearch.evaluate --null-test # how often the keep rule keeps noise (humans)

The key (build_eval.py): for each as-of year in COHORTS, the parcels that
year's owner then sold, before any building, to someone who built an
industrial building by 2025. The same site is in several years; a positive
record's weight makes every distinct site sum to 1.

Metric, per as-of year and summed. Parcels are ranked within their year by
rank.learned_score. K is 2% of the year's pool (about what a person can
actually review).
- hits: positives in the top K, by weight.
- graded: each positive earns credit by where it ranks: 1 in the top K,
  falling on a log scale to 0.5 at about 3.2 x K and to 0 at 10 x K (the
  top 20% or so). Below that it earns nothing. In units of sites.
The hard screens frozen into the evaluation set apply: a parcel they
reject earns nothing. The fixed part of the live score (scoring.py:
zoning, slope, utilities, incentives) is blank in the evaluation set, so
it is the same for every parcel and can't change the order.

Keep rule (--decide): graded must be at least MIN_GAIN above the best kept
run, hits must not be lower, AND the new ranking's graded must beat the
best kept run's in at least WIN_SHARE_TO_KEEP of clustered bootstrap
resamples. Each resample draws whole ~5 km map cells, every year of a cell
together, because positives come in clusters and a site repeats across
years; both rankings are scored on the same draw. The thresholds were set
with --null-test so that a change that is not an improvement is rarely
kept (reviews/36_screener_eval_cohorts_metric).

Scores are printed and logged to one decimal. Comparisons use the exact
values, recomputed from the best kept run's saved ranking.

Guards. Before rank.py is imported, a static check (rank_problems) must
pass, or nothing is scored. The ranking is handed only the feature fields
named in the key's meta, never a key, year, cell or weight. --decide also
refuses (exit 3, logs nothing) on uncommitted changes or untracked files
under screener/, a commit that touches anything but screener/rank.py, a
score that isn't a finite number in 0-100, a log written for another
version of this evaluator, a first run on a key the log has never seen
while it holds other keys' runs, or a best kept run whose experiments.tsv
row or best_ranking.json is missing or doesn't match. A human then records
a new baseline (--new-baseline), which starts a new chain: later
experiments are compared with it, not with older runs.
"""

from __future__ import annotations

import __future__
import argparse
import ast
import codecs
import csv
import gzip
import hashlib
import importlib
import importlib.util
import inspect
import io
import json
import math
import random
import re
import subprocess
import sys
import time
import tokenize
import typing
from pathlib import Path

from .. import screens

HERE = Path(__file__).resolve().parent
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
# Files whose change alters what the score means. Changing one needs a
# human-recorded new baseline (--new-baseline).
EVAL_FILES = ("screener/autoresearch/evaluate.py", "screener/autoresearch/data/",
              "screener/screens.py")
K_SHARE = 0.02
# Credit falls to 0 at CREDIT_SPAN x K: nothing ranked below about the top
# 20% is paid for, so lifting sites through the unread middle earns nothing.
CREDIT_SPAN = 10.0
BOOTSTRAP = 500
# Set with --null-test on the key as built (reviews/36): see there before changing.
MIN_GAIN = 2.0
WIN_SHARE_TO_KEEP = 0.95
SHOWN_DECIMALS = 1                           # what the loop is shown; comparisons are exact
NULL_NOISE = (2.0, 4.0, 8.0)                 # --null-test: score-point noise levels tried
# Why --decide refuses for now ("" = it does not). Set while a change to this
# evaluation awaits its review; a human clears it in the change that records
# the review's outcome. Scoring without --decide still works.
REVIEW_PENDING = ("the 2026-11-10 rebuild of this evaluation (five as-of years, graded score) "
                  "was merged before its adversarial review and its keep-threshold check were "
                  "finished. No experiment may be kept or discarded against it yet. Stop and "
                  "tell a human (screener/AGENTS_DESIGN.md, \"Next session\")")
# Record fields the ranking never sees. The ranking is handed an allow-list
# (the key's meta "features" and "banned"), so a new record field is hidden
# unless it is added there; this tuple documents the known ones.
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
                     "print", "help", "exit", "quit", "license", "credits", "copyright"}
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


class EvalSetError(Exception):
    """The evaluation files are missing, unreadable or disagree with each other."""


_SAME_IN_EVERY_YEAR = ("features", "banned", "screens", "label_year", "min_price", "cohorts",
                       "built_years")


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
            fields: tuple[str, ...] | list[str] | None = None
            ) -> tuple[dict[int, list[str]], tuple, int]:
    """Per as-of year, the keys of the split's parcels that pass the screens,
    best first; the (min, max) finite score seen; and how many scores were
    NaN or infinite. The scorer is handed only `fields` of a record (default:
    every field not in HIDDEN, for hand-built test pools; evaluate() passes
    the key's allow-list). One NaN anywhere scrambles the sort, so those
    rank last and --decide refuses the run."""
    scored: dict[int, list[tuple]] = {}
    nonfinite = 0
    for r in records:
        if r["split"] != split or screens.site_rejects(r, settings):
            continue
        if fields is None:
            features = {k: v for k, v in r.items() if k not in HIDDEN}
        else:
            features = {k: r.get(k) for k in fields}
        score = float(scorer(features)["score"])
        if not math.isfinite(score):
            nonfinite += 1
            score = -math.inf
        scored.setdefault(r.get("cohort", 0), []).append((-score, _tiebreak(r["key"]), r["key"]))
    finite = [-s for rows in scored.values() for s, _, _ in rows if math.isfinite(s)]
    span = (min(finite), max(finite)) if finite else (0.0, 0.0)
    return {c: [key for _, _, key in sorted(rows)] for c, rows in scored.items()}, span, nonfinite


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


def evaluate(records: list[dict], positives, split: str, scorer=None,
             settings: dict | None = None,
             fields: tuple[str, ...] | list[str] | None = None) -> dict:
    """Score one split. "hits" and "graded" are exact (decide compares them);
    main() rounds what it prints."""
    if scorer is None:
        scorer = importlib.import_module("screener.rank").learned_score
    if settings is None:
        settings = load_meta_screens()
    weights = _weights(positives)
    pools = split_pools(records, split)
    in_split = {key for pool in pools.values() for key in pool}
    pos = {k for k in in_split if weights.get(k)}
    orders, span, nonfinite = ranking(records, split, scorer, settings, fields)
    hits, graded, k = score_orders(orders, pools, weights)
    ranked = {key for order in orders.values() for key in order}
    sites = sum(weights[k] for k in pos)
    return {
        "split": split, "pool": len(in_split), "positives": len(pos),
        "sites": round(sites, 2), "k": k, "hits": hits, "graded": graded,
        "recall": round(graded / sites, 3) if sites else 0.0,
        "positives_screened_out": len(pos - ranked),
        "score_range": [round(span[0], 1), round(span[1], 1)], "nonfinite_scores": nonfinite,
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
              noise: float, split: str = "dev") -> dict:
    """How often the keep rule keeps a change that is not an improvement.
    Each trial compares two rankings made the same way: the scores in
    `base` (key -> score) plus independent noise of `noise` score points.
    Neither is better, so every keep is a false keep."""
    by_cohort: dict[int, list[str]] = {}
    for r in records:
        if r["split"] == split and r["key"] in base:
            by_cohort.setdefault(r.get("cohort", 0), []).append(r["key"])
    rng = random.Random(36)

    def noisy() -> dict[int, list[str]]:
        return {c: sorted(keys, key=lambda k: (-(base[k] + rng.gauss(0.0, noise)), _tiebreak(k)))
                for c, keys in by_cohort.items()}

    kept, gains = 0, []
    for _ in range(trials):
        verdict = keep_verdict(records, positives, noisy(), noisy(), split)
        kept += verdict["keep"]
        gains.append(verdict["gain"])
    gains.sort()
    return {"noise": noise, "trials": trials, "false_keeps": kept,
            "false_keep_rate": round(kept / trials, 4),
            "gain_p5_p95": [round(gains[int(0.05 * (trials - 1))], 2),
                            round(gains[int(0.95 * (trials - 1))], 2)]}


def rank_problems(source: str | bytes) -> list[str]:
    """Why rank.py's source breaks PROGRAM.md rules 1-3, if it does.

    It reads the source before rank.py is imported (bytes are parsed the way
    the importer compiles them). It refuses:
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


def rank_import_problems() -> list[str]:
    """rank_problems for the rank.py that would be imported, read the way the
    importer reads it: the file's bytes, decoded by their own coding
    declaration, which must be UTF-8. (Under '# coding: utf-7', text that
    reads as a comment can decode to a line of code.)"""
    origin = _rank_origin()
    if not origin or Path(origin).resolve() != RANK_FILE.resolve():
        return [f"screener.rank would load from {origin}, not screener/rank.py"]
    data = RANK_FILE.read_bytes()
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
    except SyntaxError as e:
        return [f"has an unreadable coding declaration ({e})"]
    if codecs.lookup(encoding).name not in ("utf-8", "utf-8-sig"):
        return [f"declares the {encoding} encoding; rank.py must be UTF-8"]
    return rank_problems(data)


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
    new = not LOG_FILE.exists()
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
        if best is None and _rows():
            # The log holds runs, all of another evaluation set: the key changed.
            return _refuse("the evaluation set changed since the runs in experiments.tsv. A "
                           f"human must record a new baseline: {NEW_BASELINE}")
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
        # Both rankings are scored here, on exact values; the log's rounded
        # numbers are never compared.
        v = keep_verdict(records, positives, dev["order"], saved["order"])
        detail = (f"graded {_shown(dev['graded'])} vs best kept {_shown(v['best_graded'])} "
                  f"({v['gain']:+.1f}); hits {_shown(dev['hits'])} vs {_shown(v['best_hits'])}; "
                  f"wins {v['win_share']:.0%} of resamples")
        if v["keep"]:
            status, why = "keep", detail
        else:
            status, why = "discard", (f"{detail} (needs +{MIN_GAIN:g} graded, hits not lower, "
                                      f"and {WIN_SHARE_TO_KEEP:.0%})")
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
        print(f"Revert the experiment: git reset --hard {parent}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--decide", action="store_true", help="compare to the best kept run and log")
    ap.add_argument("--test", action="store_true", help="also score the sealed test split")
    ap.add_argument("--control", action="store_true", help="what random ranking scores")
    ap.add_argument("--null-test", type=int, nargs="?", const=200, default=0, metavar="TRIALS",
                    help="(humans only) how often the keep rule keeps a change that is not an "
                         "improvement, at several noise levels (slow: minutes)")
    ap.add_argument("--new-baseline", action="store_true",
                    help="(humans only) record the current ranking as a fresh baseline")
    args = ap.parse_args(argv)

    if not all(f.exists() for f in data_files()):
        print("No complete evaluation set: run `python -m screener.autoresearch.build_eval` "
              "first (it needs data/test_blocks.json from git).")
        return 2
    if not args.control:
        # Before anything imports rank.py: a refused file never runs.
        problems = rank_import_problems()
        if problems:
            return _refuse("screener/rank.py breaks PROGRAM.md rules 1-3, so it was not run: "
                           + "; ".join(problems[:5]))
    try:
        records, positives, meta = load()
    except EvalSetError as exc:
        print(f"The evaluation set can't be used: {exc}. Re-run "
              "`python -m screener.autoresearch.build_eval` (humans).")
        return 2
    fields = list(meta["features"]) + list(meta["banned"])
    settings = meta["screens"]

    def public(res: dict) -> dict:
        out = {k: v for k, v in res.items() if k != "order"}
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

    dev = evaluate(records, positives, "dev", settings=settings, fields=fields)
    if args.null_test:
        if args.null_test < 20:
            print("--null-test needs at least 20 trials")
            return 2
        scorer = importlib.import_module("screener.rank").learned_score
        base = {}
        for r in records:
            if r["split"] == "dev" and not screens.site_rejects(r, settings):
                score = float(scorer({k: r.get(k) for k in fields})["score"])
                base[r["key"]] = score if math.isfinite(score) else 0.0
        print(json.dumps({"keep_rule": {"min_gain": MIN_GAIN, "win_share": WIN_SHARE_TO_KEEP,
                                        "hits_may_not_fall": True},
                          "null_test": [null_test(records, positives, base, args.null_test, n)
                                        for n in NULL_NOISE]}))
        return 0
    out = {"dev": public(dev)}
    if args.test:
        out["test"] = public(evaluate(records, positives, "test", settings=settings, fields=fields))
    print(json.dumps(out))
    if args.new_baseline and not args.decide:
        print("--new-baseline only applies with --decide")
        return 2
    return decide(records, positives, dev, args.new_baseline) if args.decide else 0


if __name__ == "__main__":
    sys.exit(main())
