"""CI dotted-invocation guard (post-change adversarial review round 2,
reviews/33_hazards_enabled).

``python -m unittest discover -s tests`` (the full suite) puts ``tests/`` on
sys.path, so a bare sibling import such as ``from hazards_helpers import ...``
resolves there. A workflow that runs one module DOTTED (for example
discovery-fulton.yml's ``python -m unittest tests.test_discovery``) does not,
and the same import fails with ModuleNotFoundError. The full-suite run cannot
see that. This module finds every dotted ``python -m unittest tests.<module>``
invocation in .github/workflows/*.yml and the Makefile and runs that exact
command from the repo root in a clean subprocess. Offline (SR-6): the
invoked modules are offline suites; DATABASE_URL and PYTHONPATH are removed
from the child environment and the mirror kill switch is set.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_DOTTED = re.compile(r"python -m unittest\s+(tests\.[A-Za-z0-9_]+)")


def dotted_ci_modules() -> list[str]:
    files = sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))
    files.append(REPO_ROOT / "Makefile")
    found: set[str] = set()
    for f in files:
        if f.exists():
            found.update(_DOTTED.findall(f.read_text(encoding="utf-8")))
    return sorted(found)


def _child_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "DATABASE_URL")}
    env["EXPERIMENT_LOG_MIRROR_DISABLE"] = "1"
    return env


def _run_dotted(module: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "unittest", module],
                          cwd=str(cwd), env=_child_env(), capture_output=True,
                          text=True, timeout=300)


class TestCiDottedInvocations(unittest.TestCase):
    def test_the_known_ci_invocations_are_found(self) -> None:
        # A parsing regression must not make the guard below pass vacuously.
        mods = dotted_ci_modules()
        self.assertIn("tests.test_discovery", mods)
        self.assertIn("tests.test_prepare", mods)

    def test_every_dotted_ci_module_runs_green(self) -> None:
        for module in dotted_ci_modules():
            with self.subTest(module=module):
                proc = _run_dotted(module, REPO_ROOT)
                tail = (proc.stderr or "")[-2000:]
                self.assertNotIn("ModuleNotFoundError", tail)
                self.assertEqual(proc.returncode, 0, tail)
                self.assertRegex(tail, r"\nOK")

    def test_guard_detects_a_bare_sibling_import(self) -> None:
        # The failure mode this module exists for: a dotted run of a test
        # module whose bare sibling import resolves only under discover -s.
        with tempfile.TemporaryDirectory() as d:
            pkg = Path(d) / "pkg"
            pkg.mkdir()
            (pkg / "__init__.py").write_text("", encoding="utf-8")
            (pkg / "helper_mod.py").write_text("X = 1\n", encoding="utf-8")
            (pkg / "test_x.py").write_text(
                "import unittest\nfrom helper_mod import X\n\n"
                "class T(unittest.TestCase):\n"
                "    def test_x(self):\n        self.assertEqual(X, 1)\n",
                encoding="utf-8")
            proc = _run_dotted("pkg.test_x", Path(d))
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("ModuleNotFoundError", proc.stderr)


if __name__ == "__main__":
    unittest.main()
