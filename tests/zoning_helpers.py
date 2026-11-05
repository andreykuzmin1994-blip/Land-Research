"""Test helpers for the zoning rules (reviews/21_zoning). Offline (SR-6)."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Callable
from unittest import mock

import zoning

REPO_ROOT = Path(__file__).resolve().parent.parent
RULES_PATH = REPO_ROOT / "zoning_rules.json"

# Human-owned files, hashed when the suite LOADS (unittest imports every test
# module before running any test); tests/test_zz_human_files_unchanged.py
# compares them after every other test has run (Z-17: no test patches them).
HUMAN_OWNED = ("sources.json", "parameters.json", "program.md", "zoning_rules.json",
               "building_prototypes.json")
HUMAN_OWNED_AT_LOAD = {
    name: hashlib.sha256((REPO_ROOT / name).read_bytes()).hexdigest() for name in HUMAN_OWNED
}


def repo_rules() -> dict[str, Any]:
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


def state_from(data: dict[str, Any]) -> "zoning._State":
    raw = json.dumps(data).encode("utf-8")
    return zoning._State(zoning.parse_rules_bytes(raw), hashlib.sha256(raw).hexdigest(), None)


def verified(*keys: str, mutate: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """A copy of the repo rules with ``keys`` flipped to verified."""
    data = copy.deepcopy(repo_rules())
    for k in keys:
        j = data["jurisdictions"][k]
        j["status"] = "verified"
        j["verified_by"] = "test"
        j["verified_on"] = "2026-11-05"
    if mutate:
        mutate(data)
    return data


def patch_rules(data: dict[str, Any] | None = None, *, problem: str | None = None):
    """Context manager: zoning uses ``data`` (or is unavailable with ``problem``).

    The patched state carries the REAL file's SHA-256, so the cycle-entry
    sentinel (verify_rules_unchanged) still passes while a test swaps the
    in-memory rules."""
    real_sha = hashlib.sha256(RULES_PATH.read_bytes()).hexdigest()
    if problem is not None:
        st = zoning._State(None, real_sha, problem)
    else:
        st = state_from(data if data is not None else repo_rules())
        st = zoning._State(st.rules, real_sha, None)
    return mock.patch.object(zoning, "_STATE", st)


def stored(j: str, src: str, code: str, split: bool = False, desc: str = "-") -> tuple[str, str]:
    return zoning.encode_stored(j, src, code, split, desc)
