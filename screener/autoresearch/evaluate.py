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
RANK_BANNED_STRINGS = ("autoresearch/data", "fulton_2016", "experiments.tsv", "best_ranking")
# What to use instead, in refusal messages.
RANK_HINTS = {"type": " (use isinstance)", "format": " (use an f-string)",
              "format_map": " (use an f-string)", "print": " (put reasons in why_site / why_seller)"}


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
