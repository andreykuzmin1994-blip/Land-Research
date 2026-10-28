"""Repo-hygiene and suite-hermeticity guards (SR-6, SR-8, SR-12, SR-13).

These regressions live between files rather than inside one module's
behaviour, so no other test sees them:

1. ``.gitignore`` has NO inline comments: ``pattern   # note`` is one
   pattern INCLUDING the note, so the file it names is silently not
   ignored. That is how ``experiment_log.tsv`` (append-only, never
   committed — SR-13) became committable, and why ``make setup`` saw a
   dirty tree after a baseline. The files that must never be committed
   are checked with ``git check-ignore`` itself, not by grepping.
2. The offline suite must not write into the repo: research's raw-response
   cache (``research._SOURCES_DIR``) is redirected to a temp dir by the
   ``tests`` package guard (tests/__init__.py).
3. The live-PostGIS smoke script refuses non-local databases, and reads
   the metric scoped to its OWN rows, so its result does not depend on
   which branch is checked out (an ``autoresearch/<tag>`` branch makes
   prepare derive a run scope) or on other rows in the database.
4. The always-on offline CI workflow runs the screener suite too.

Hermetic per SR-6: no network, no DATABASE_URL, no database connection.
"""

from __future__ import annotations

import ast
import io
import os
import shutil
import subprocess
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from typing import Iterator
from unittest import mock

import tests  # noqa: F401 — force the package guards (mirror kill switch,
              # sources/ redirect) to run even under top-level discovery,
              # which skips tests/__init__.py

import research
import runner

try:  # top-level discovery (`discover -s tests`) puts tests/ on sys.path
    import test_postgis_smoke as smoke
except ImportError:  # package-qualified run (`-m unittest tests.test_hermetic`)
    from tests import test_postgis_smoke as smoke

REPO_ROOT = Path(__file__).resolve().parent.parent
SMOKE_PY_SRC = Path(smoke.__file__).read_text(encoding="utf-8")
OFFLINE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "offline-tests.yml"

# Repo-relative paths that must never be committed, derived from the
# modules that write them where a constant exists.
_NEVER_COMMIT = (
    runner._EXPERIMENT_LOG_PATH.relative_to(runner._REPO_ROOT).as_posix(),
    "results.tsv",
    runner._HALT_SENTINEL_PATH.relative_to(runner._REPO_ROOT).as_posix(),
    runner._LOOP_LOCK_PATH.relative_to(runner._REPO_ROOT).as_posix(),
    ".env",
    # Raw ArcGIS responses carry owner PII (SR-8, R-30).
    "sources/disco-fulton-20260101T000000Z-abcd/south_fulton_campbellton_0.json",
)


class TestGitignore(unittest.TestCase):
    """SR-12/SR-13: never-commit files really are ignored."""

    def test_no_inline_comments(self) -> None:
        gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        for lineno, line in enumerate(gitignore.splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            self.assertNotRegex(
                line, r"\s#",
                f".gitignore:{lineno} has an inline comment; gitignore treats "
                f"it as part of the pattern, so the path is NOT ignored. "
                f"Put the comment on its own line: {line!r}",
            )

    def test_never_commit_paths_are_ignored(self) -> None:
        git = shutil.which("git")
        if git is None:
            self.skipTest("git not installed")
        proc = subprocess.run(
            [git, "check-ignore", "--verbose", "--non-matching",
             "--no-index", "--", *_NEVER_COMMIT],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
            timeout=30, check=False,
        )
        if proc.returncode not in (0, 1):
            self.skipTest(f"not a git work tree: {proc.stderr.strip()}")
        verdicts = {}
        for line in proc.stdout.splitlines():
            source_info, _, path = line.partition("\t")
            verdicts[path] = source_info
        for path in _NEVER_COMMIT:
            source_info = verdicts.get(path, "")
            source, _, rest = source_info.partition(":")
            _lineno, _, pattern = rest.partition(":")
            # Matched by the repo's own .gitignore (not a developer's global
            # excludes file) with a positive (non-"!") pattern.
            self.assertEqual(
                source, ".gitignore",
                f"{path} is not ignored by the repo .gitignore "
                f"(check-ignore said {source_info!r})",
            )
            self.assertFalse(pattern.startswith("!"), f"{path} is re-included")


class TestSourcesCacheRedirect(unittest.TestCase):
    """SR-6: the suite's raw-response cache writes stay out of the repo."""

    def test_sources_dir_is_outside_the_repo(self) -> None:
        sources = research._SOURCES_DIR.resolve()
        self.assertFalse(
            sources.is_relative_to(REPO_ROOT),
            f"research._SOURCES_DIR={sources} is inside the repo; the tests "
            f"package guard must redirect it",
        )

    def test_corridor_query_caches_into_redirected_dir(self) -> None:
        class _EmptySession:
            def get(self, url, params=None, timeout=None):
                return {"features": [], "exceededTransferLimit": False}

        cycle_id = research._make_cycle_id("fulton")
        self.addCleanup(
            shutil.rmtree, research._SOURCES_DIR / cycle_id, ignore_errors=True
        )
        feats = list(research._query_arcgis_corridor(
            _EmptySession(), "https://example.test/MapServer", 11,
            {"xmin": -84.62, "ymin": 33.52, "xmax": -84.50, "ymax": 33.58},
            {"acreage": "LandAcres", "parcel_id": "ParcelID"},
            {"hard_filters": {"acreage_min": 5, "acreage_max": 50}},
            cycle_id, "south_fulton_campbellton", page_size=1000,
        ))
        self.assertEqual(feats, [])
        cached = research._SOURCES_DIR / cycle_id / "south_fulton_campbellton_0.json"
        self.assertTrue(cached.is_file())
        self.assertFalse((REPO_ROOT / "sources" / cycle_id).exists())

    def test_path_traversal_still_rejected_under_redirect(self) -> None:
        cycle_id = research._make_cycle_id("fulton")
        path = research._safe_cache_path(cycle_id, "south_fulton_campbellton", 0)
        self.assertTrue(path.is_relative_to(research._SOURCES_DIR.resolve()))
        with self.assertRaises(ValueError):
            research._safe_cache_path("../../etc", "south_fulton_campbellton", 0)
        with self.assertRaises(ValueError):
            research._safe_cache_path(cycle_id, "../escape", 0)


@contextmanager
def _smoke_env(**values: str) -> Iterator[None]:
    """os.environ with the libpq host fallbacks and the opt-in cleared."""
    with mock.patch.dict(os.environ, values):
        for key in ("PGHOST", "PGHOSTADDR", "PGSERVICE",
                    smoke._ALLOW_NONLOCAL_ENV_VAR):
            if key not in values:
                os.environ.pop(key, None)
        yield


class TestSmokeHostGuard(unittest.TestCase):
    """The smoke script writes PASS rows and asserts counts: local DBs only."""

    def test_local_dsns_accepted(self) -> None:
        with _smoke_env():
            for dsn in (
                "postgresql://lr:lr_pw@localhost:5432/lr_test",
                "postgresql://lr:lr_pw@127.0.0.1:55441/lr_test",
                "postgresql://lr:lr_pw@[::1]:5432/lr_test",
                "host=localhost port=5432 dbname=lr_test user=lr",
                "postgresql:///lr_test?host=/var/run/postgresql",
                "postgresql:///lr_test",  # libpq default Unix socket
            ):
                self.assertEqual(smoke._nonlocal_hosts(dsn), [], dsn)

    def test_nonlocal_dsns_rejected(self) -> None:
        with _smoke_env():
            for dsn in (
                "postgresql://u:pw@db.abcdefghijkl.supabase.co:5432/postgres",
                "postgresql://u:pw@aws-0-us-east-1.pooler.supabase.com:6543/postgres",
                "postgresql://u:pw@10.0.0.5:5432/db",
                "host=localhost,db.example.com dbname=x",
                "postgresql://u:pw@localhost/db?hostaddr=10.0.0.1",
                "service=shared",
            ):
                self.assertNotEqual(smoke._nonlocal_hosts(dsn), [], dsn)

    def test_libpq_env_fallbacks_are_honored(self) -> None:
        with _smoke_env(PGHOST="db.example.com"):
            self.assertEqual(
                smoke._nonlocal_hosts("postgresql:///lr_test"), ["db.example.com"]
            )
        with _smoke_env(PGHOST="db.example.com"):
            # An explicit DSN host wins over PGHOST, exactly as in libpq.
            self.assertEqual(
                smoke._nonlocal_hosts("postgresql://u@localhost/lr_test"), []
            )
        with _smoke_env(PGSERVICE="shared"):
            self.assertNotEqual(smoke._nonlocal_hosts("dbname=lr_test"), [])

    def test_main_refuses_nonlocal_without_connecting(self) -> None:
        dsn = "postgresql://u:s3cret-pw@db.abcdefghijkl.supabase.co:5432/postgres"
        buf = io.StringIO()
        with _smoke_env(DATABASE_URL=dsn), \
                mock.patch.object(smoke.psycopg, "connect") as connect, \
                redirect_stdout(buf):
            with self.assertRaises(SystemExit) as ctx:
                smoke.main()
        self.assertNotEqual(ctx.exception.code, 0)
        connect.assert_not_called()
        self.assertIn("db.abcdefghijkl.supabase.co", buf.getvalue())
        self.assertNotIn("s3cret-pw", buf.getvalue())  # SR-8: host names only

    def test_refusals_never_echo_a_password(self) -> None:
        # prepare._mask_dsn masks URL-form DSNs only, and the conninfo
        # parser's error text quotes the offending token.
        for dsn in (
            "host=db.example.com password=s3cret-pw dbname=x",
            "host=localhost user=lr s3cret-pw",  # "password=" mistyped
        ):
            buf = io.StringIO()
            with _smoke_env(DATABASE_URL=dsn), \
                    mock.patch.object(smoke.psycopg, "connect") as connect, \
                    redirect_stdout(buf):
                with self.assertRaises(SystemExit):
                    smoke.main()
            connect.assert_not_called()
            self.assertNotIn("s3cret-pw", buf.getvalue(), dsn)

    def test_explicit_opt_in_allows_nonlocal(self) -> None:
        dsn = "postgresql://u:pw@db.example.com:5432/scratch"
        with _smoke_env(**{smoke._ALLOW_NONLOCAL_ENV_VAR: "1"}), \
                redirect_stdout(io.StringIO()):
            smoke._require_local_dsn(dsn)  # no SystemExit
        for value in ("", "0", "true"):
            with _smoke_env(**{smoke._ALLOW_NONLOCAL_ENV_VAR: value}), \
                    redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit, msg=repr(value)):
                    smoke._require_local_dsn(dsn)


class TestSmokeMetricScoping(unittest.TestCase):
    """The smoke script's metric reads are scoped to its own tagged rows.

    ``run_tag=None`` makes prepare derive the scope from the git branch:
    on ``autoresearch/<tag>`` it filters the smoke rows out (count 0, a
    false failure); elsewhere it counts the whole database.
    """

    _METRIC_FUNCS = (
        "calculate_actionable_pipeline_count",
        "calculate_confidence_weighted_pipeline",
    )

    def test_metric_calls_pass_an_explicit_run_tag(self) -> None:
        calls = [
            node for node in ast.walk(ast.parse(SMOKE_PY_SRC))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in self._METRIC_FUNCS
        ]
        self.assertEqual(len(calls), 2)
        for call in calls:
            tag_args = call.args[1:2] + [
                kw.value for kw in call.keywords if kw.arg == "run_tag"
            ]
            self.assertEqual(len(tag_args), 1, f"line {call.lineno}: no run_tag")
            self.assertFalse(
                isinstance(tag_args[0], ast.Constant) and tag_args[0].value is None,
                f"line {call.lineno}: run_tag=None derives scope from the branch",
            )

    def test_inserted_scores_are_stamped_with_run_tag(self) -> None:
        inserts = [
            node.value for node in ast.walk(ast.parse(SMOKE_PY_SRC))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and "INSERT INTO parcel_scores" in node.value
        ]
        self.assertTrue(inserts)
        for sql in inserts:
            self.assertIn("run_tag", sql)


class TestOfflineWorkflow(unittest.TestCase):
    """The unconditional CI workflow runs BOTH offline suites."""

    def test_screener_suite_runs_in_ci(self) -> None:
        workflow = OFFLINE_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("pip install -r requirements.txt", workflow)
        self.assertIn("python -m unittest discover -s tests -v", workflow)
        self.assertIn("pip install -r screener/requirements.txt", workflow)
        self.assertIn(
            "python -m unittest discover -s screener/tests -t . -v", workflow
        )


if __name__ == "__main__":
    unittest.main()
