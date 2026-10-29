"""Tests for the env.template credential guard (SR-12).

The guard is one checker, .githooks/check_env_template.py, run by BOTH the
pre-commit hook (on the STAGED blob, streamed on stdin) and the
check-env-template CI workflow (on the checked-out file). These tests run
the real checker as a subprocess, and drive the real hook end-to-end
through `git commit` in throwaway repositories.

Hermetic (SR-6): no network, no database, no global/system git config,
inherited GIT_* variables scrubbed, a timeout on every subprocess.

No secret-shaped literal appears in this file: every fake credential is
assembled at runtime from parts plus uuid4 randomness, so GitHub push
protection / secret scanning has nothing to match. Assertions on the REAL
env.template check return codes only, so a regression can never print
its contents into a public CI log.
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = REPO_ROOT / ".githooks"
HOOK = HOOKS_DIR / "pre-commit"
CHECKER = HOOKS_DIR / "check_env_template.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "check-env-template.yml"
REAL_TEMPLATE = REPO_ROOT / "env.template"

GIT = shutil.which("git")
BASH = shutil.which("bash")
TIMEOUT = 60

HOOK_MARKER = "pre-commit: refusing to commit env.template"

# Synthetic template with the real file's shape: an em dash (non-ASCII)
# and an '@' in comments, DATABASE_URL with a placeholder password slot,
# and an empty ANTHROPIC_API_KEY.
DB_LINE = 5
API_LINE = 8
_TEMPLATE = (
    "# Copy this file to .env and fill in real values.\n"
    "# .env is gitignored — never paste real credentials here.\n"
    "\n"
    "# URL-encode special characters (! -> %21, @ -> %40, : -> %3A).\n"
    "DATABASE_URL=postgresql://postgres.PROJECT_REF:{db}"
    "@aws-0-REGION.pooler.supabase.com:5432/postgres\n"
    "\n"
    "# Anthropic API key. Leave blank for Phase 1.\n"
    "ANTHROPIC_API_KEY={api}\n"
)


def _template(db: str = "URL_ENCODED_PASSWORD", api: str = "",
              extra: Sequence[str] = (), eol: str = "\n") -> bytes:
    text = _TEMPLATE.format(db=db, api=api) + "".join(line + "\n" for line in extra)
    return text.replace("\n", eol).encode("utf-8")


def _fake_db_password() -> Tuple[str, str]:
    """(password, random part) -- realistic-looking, built at runtime."""
    rand = uuid.uuid4().hex[:20]
    return "Pw" + rand + "%21", rand


def _fake_api_key() -> Tuple[str, str]:
    """(key, random part) in the provider's prefix format, built at runtime."""
    rand = uuid.uuid4().hex + uuid.uuid4().hex
    return "sk-" + "ant-" + "api03-" + rand, rand


def _fake_dsn(password: str, user: str = "u", host: str = "db.internal") -> str:
    return "postgresql" + "://" + user + ":" + password + "@" + host + "/db"


def _run_checker(data: Optional[bytes], *args: str,
                 env: Optional[dict] = None) -> Tuple[int, str]:
    """Run the checker the way CI does, on env.template in a temp dir.

    data=None leaves the file absent. Returns (rc, stdout+stderr).
    """
    with tempfile.TemporaryDirectory() as tmp:
        if data is not None:
            Path(tmp, "env.template").write_bytes(data)
        proc = subprocess.run(
            [sys.executable, "-I", str(CHECKER), *args, "env.template"],
            cwd=tmp, capture_output=True, timeout=TIMEOUT, env=env,
        )
    out = (proc.stdout.decode("utf-8", "replace")
           + proc.stderr.decode("utf-8", "replace"))
    return proc.returncode, out


class _LeakAssertions(unittest.TestCase):
    def assertNoLeak(self, out: str, secret: str, rand: str) -> None:
        self.assertNotIn(secret, out, "full secret value printed")
        for i in range(len(rand) - 7):
            self.assertNotIn(rand[i:i + 8], out,
                             "8-character window of the secret printed")

    def assertFlagged(self, rc: int, out: str, *, line: int,
                      name: Optional[str] = None) -> None:
        self.assertEqual(rc, 1, out)
        self.assertIn(":%d:" % line, out)
        if name is not None:
            self.assertIn(name, out)


# ---------------------------------------------------------------------------
# The repo's own env.template (rc-only assertions: never echo its content)
# ---------------------------------------------------------------------------
class TestRepoEnvTemplate(unittest.TestCase):
    def test_checker_exists_and_hook_is_executable(self) -> None:
        self.assertTrue(CHECKER.is_file(), "missing .githooks/check_env_template.py")
        # git silently SKIPS a hook without the exec bit.
        self.assertTrue(os.access(HOOK, os.X_OK), ".githooks/pre-commit lost its exec bit")

    def test_repo_env_template_passes(self) -> None:
        rc, _ = _run_checker(REAL_TEMPLATE.read_bytes())
        self.assertEqual(rc, 0, "repo env.template fails the credential checker")

    def test_repo_env_template_passes_under_c_locale(self) -> None:
        env = dict(os.environ, LC_ALL="C", LANG="C")
        rc, _ = _run_checker(REAL_TEMPLATE.read_bytes(), env=env)
        self.assertEqual(rc, 0, "repo env.template fails the checker under LC_ALL=C")

    def test_checker_is_python38_syntax(self) -> None:
        # Developer machines run the hook with their own python3 (macOS
        # ships 3.9); CI runs a newer one. Keep the grammar to 3.8.
        ast.parse(CHECKER.read_text(encoding="utf-8"), feature_version=(3, 8))


# ---------------------------------------------------------------------------
# Allowed placeholders (exact tokens)
# ---------------------------------------------------------------------------
class TestCheckerPlaceholders(unittest.TestCase):
    def test_db_slot_placeholders_pass(self) -> None:
        for slot in ("URL_ENCODED_PASSWORD", "CHANGE_ME", "PASSWORD", ""):
            with self.subTest(slot=slot):
                rc, out = _run_checker(_template(db=slot))
                self.assertEqual(rc, 0, out)
                self.assertEqual(out, "")

    def test_secret_key_placeholders_pass(self) -> None:
        for api in ("", '""', "''", "CHANGE_ME", '"CHANGE_ME"'):
            with self.subTest(api=api):
                rc, out = _run_checker(_template(api=api))
                self.assertEqual(rc, 0, out)

    def test_commented_placeholder_assignment_passes(self) -> None:
        rc, out = _run_checker(_template(extra=["# OTHER_API_KEY=", "# EXTRA_TOKEN=CHANGE_ME"]))
        self.assertEqual(rc, 0, out)

    def test_non_secret_keys_and_plain_urls_pass(self) -> None:
        rc, out = _run_checker(_template(extra=[
            "LOG_LEVEL=info",
            "ARCGIS_URL=https://gis.example.org/arcgis/rest/services",
            "TEST_DATABASE_URL=postgresql://lr:PASSWORD@127.0.0.1:5432/lr_test",
        ]))
        self.assertEqual(rc, 0, out)


# ---------------------------------------------------------------------------
# Real-looking secrets are flagged by key name + line, never by value
# ---------------------------------------------------------------------------
class TestCheckerSecrets(_LeakAssertions):
    def test_real_db_password_fails_without_leaking(self) -> None:
        pw, rand = _fake_db_password()
        rc, out = _run_checker(_template(db=pw))
        self.assertFlagged(rc, out, line=DB_LINE, name="DATABASE_URL")
        self.assertNoLeak(out, pw, rand)

    def test_real_api_key_fails_without_leaking(self) -> None:
        key, rand = _fake_api_key()
        rc, out = _run_checker(_template(api=key))
        self.assertFlagged(rc, out, line=API_LINE, name="ANTHROPIC_API_KEY")
        self.assertNoLeak(out, key, rand)

    def test_every_secret_name_pattern_is_checked(self) -> None:
        for name in ("SUPABASE_SERVICE_KEY", "GITHUB_TOKEN", "CLIENT_SECRET",
                     "SMTP_PASSWORD", "PGPASS", "api_key"):
            with self.subTest(name=name):
                value = "v" + uuid.uuid4().hex
                rc, out = _run_checker(_template(extra=[name + "=" + value]))
                self.assertFlagged(rc, out, line=API_LINE + 1)
                self.assertNoLeak(out, value, value[1:])

    def test_all_caps_value_is_not_treated_as_placeholder(self) -> None:
        # Shape rules ("ALL_CAPS looks like a placeholder") would admit
        # real all-caps access-key IDs; only exact tokens are allowed.
        value = uuid.uuid4().hex.upper()[:20]
        rc, out = _run_checker(_template(api=value))
        self.assertFlagged(rc, out, line=API_LINE, name="ANTHROPIC_API_KEY")
        self.assertNoLeak(out, value, value)

    def test_placeholder_lookalike_db_slot_fails(self) -> None:
        for slot in ("change_me", "URL_ENCODED_PASSWORD1", "CHANGE_ME "):
            with self.subTest(slot=slot):
                rc, out = _run_checker(_template(db=slot))
                self.assertFlagged(rc, out, line=DB_LINE)


# ---------------------------------------------------------------------------
# File structure: missing / empty / duplicate / unparseable
# ---------------------------------------------------------------------------
class TestCheckerStructure(_LeakAssertions):
    def test_missing_database_url_line_fails(self) -> None:
        data = _template().replace(b"DATABASE_URL=", b"DB_URL=")
        rc, out = _run_checker(data)
        self.assertEqual(rc, 1, out)
        self.assertIn("DATABASE_URL", out)

    def test_commented_only_database_url_fails(self) -> None:
        data = _template().replace(b"DATABASE_URL=", b"# DATABASE_URL=")
        rc, out = _run_checker(data)
        self.assertEqual(rc, 1, out)

    def test_database_url_without_credential_url_fails(self) -> None:
        for value in ("", "CHANGE_ME", "postgresql://db.internal/postgres"):
            with self.subTest(value=value):
                data = _template().replace(
                    b"DATABASE_URL=postgresql://postgres.PROJECT_REF:URL_ENCODED_PASSWORD"
                    b"@aws-0-REGION.pooler.supabase.com:5432/postgres",
                    b"DATABASE_URL=" + value.encode())
                rc, out = _run_checker(data)
                self.assertFlagged(rc, out, line=DB_LINE, name="DATABASE_URL")

    def test_duplicate_database_url_with_real_second_value_fails(self) -> None:
        pw, rand = _fake_db_password()
        rc, out = _run_checker(_template(extra=["DATABASE_URL=" + _fake_dsn(pw)]))
        self.assertFlagged(rc, out, line=API_LINE + 1, name="DATABASE_URL")
        self.assertNoLeak(out, pw, rand)

    def test_empty_file_fails(self) -> None:
        for data in (b"", b"\n\n", b"   \r\n"):
            with self.subTest(data=data):
                rc, out = _run_checker(data)
                self.assertEqual(rc, 1, out)
                self.assertIn("empty", out)

    def test_missing_file_fails(self) -> None:
        rc, out = _run_checker(None)
        self.assertEqual(rc, 1, out)
        self.assertIn("missing", out)

    def test_unparseable_line_fails_by_line_number_only(self) -> None:
        junk = "x" + uuid.uuid4().hex
        rc, out = _run_checker(_template(extra=[junk]))
        self.assertFlagged(rc, out, line=API_LINE + 1)
        self.assertNoLeak(out, junk, junk[1:])

    def test_unreadable_path_fails_closed_with_fixed_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "env.template").mkdir()
            proc = subprocess.run(
                [sys.executable, "-I", str(CHECKER), "env.template"],
                cwd=tmp, capture_output=True, timeout=TIMEOUT)
        self.assertEqual(proc.returncode, 2)
        self.assertIn(b"failing closed", proc.stderr)
        self.assertNotIn(b"Traceback", proc.stderr)


# ---------------------------------------------------------------------------
# Parsing variants that bypassed the old prefix-grep
# ---------------------------------------------------------------------------
class TestCheckerParsingVariants(_LeakAssertions):
    def _assert_extra_line_flagged(self, line: str, secret: str, rand: str,
                                   name: Optional[str] = None) -> None:
        rc, out = _run_checker(_template(extra=[line]))
        self.assertFlagged(rc, out, line=API_LINE + 1, name=name)
        self.assertNoLeak(out, secret, rand)

    def test_assignment_spellings_are_all_checked(self) -> None:
        key, rand = _fake_api_key()
        for line in ("export ANTHROPIC_API_KEY=" + key,
                     "   ANTHROPIC_API_KEY=" + key,
                     "ANTHROPIC_API_KEY = " + key,
                     'ANTHROPIC_API_KEY="' + key + '"',
                     "ANTHROPIC_API_KEY='" + key + "'",
                     "anthropic_api_key=" + key,
                     "# ANTHROPIC_API_KEY=" + key,
                     "#ANTHROPIC_API_KEY=" + key,
                     "  #  export ANTHROPIC_API_KEY=" + key):
            with self.subTest(form=line.replace(key, "<key>")):
                self._assert_extra_line_flagged(line, key, rand)

    def test_database_url_spellings_are_all_checked(self) -> None:
        pw, rand = _fake_db_password()
        dsn = _fake_dsn(pw)
        for line in ("export DATABASE_URL=" + dsn,
                     " DATABASE_URL=" + dsn,
                     'DATABASE_URL="' + dsn + '"',
                     "# DATABASE_URL=" + dsn):
            with self.subTest(form=line.replace(pw, "<pw>")):
                self._assert_extra_line_flagged(line, pw, rand)

    def test_credential_url_under_any_key_is_checked(self) -> None:
        pw, rand = _fake_db_password()
        for name in ("TEST_DATABASE_URL", "DATABASE_POOLER_URL", "DIRECT_URL"):
            with self.subTest(name=name):
                self._assert_extra_line_flagged(name + "=" + _fake_dsn(pw), pw, rand,
                                                name=name)

    def test_credential_url_in_prose_comment_is_checked(self) -> None:
        pw, rand = _fake_db_password()
        self._assert_extra_line_flagged("# e.g. " + _fake_dsn(pw), pw, rand)

    def test_password_with_slash_or_space_is_checked(self) -> None:
        pw, rand = _fake_db_password()
        for slot in ("a/" + pw, pw + " x"):
            with self.subTest(slot=slot.replace(pw, "<pw>")):
                line = 'DATABASE_POOLER_URL="' + _fake_dsn(slot) + '"'
                self._assert_extra_line_flagged(line, pw, rand)

    def test_password_query_parameter_is_checked(self) -> None:
        pw, rand = _fake_db_password()
        base = "postgresql" + "://postgres.PROJECT_REF:URL_ENCODED_PASSWORD@h:5432/postgres"
        for line in ("DATABASE_POOLER_URL=" + base + "?password=" + pw,
                     "DATABASE_POOLER_URL=" + base + "?sslmode=require&sslpassword=" + pw,
                     "PG_DSN=host=h user=u password=" + pw + " dbname=d"):
            with self.subTest(form=line.replace(pw, "<pw>")):
                self._assert_extra_line_flagged(line, pw, rand)

    def test_second_assignment_on_a_line_is_checked(self) -> None:
        # A shell sourcing the file reads both; prose before the key name
        # must not hide it either.
        value = "v" + uuid.uuid4().hex
        for line in ("LOG_LEVEL=info; SMTP_PASSWORD=" + value,
                     "LOG_LEVEL=info SMTP_PASSWORD=" + value,
                     "# old: SMTP_PASSWORD=" + value,
                     "ARCGIS_URL=https://gis.example.org/x?f=json&api_key=" + value):
            with self.subTest(form=line.replace(value, "<v>")):
                self._assert_extra_line_flagged(line, value, value[1:])

    def test_ambiguous_credential_urls_fail_closed(self) -> None:
        tok = "t" + uuid.uuid4().hex
        for line in (
            # two '@': placeholder first slot must not hide the second
            "DIRECT_URL=" + "postgresql" + "://u:PASSWORD@x:" + tok + "@h/db",
            # token used as the URL user (no ':' slot)
            "GIT_REMOTE=" + "https" + "://" + tok + "@git.example.org/r.git",
        ):
            with self.subTest(form=line.replace(tok, "<tok>")):
                self._assert_extra_line_flagged(line, tok, tok[1:])


# ---------------------------------------------------------------------------
# Encoding and line endings
# ---------------------------------------------------------------------------
class TestCheckerEncoding(_LeakAssertions):
    def test_crlf_clean_file_passes(self) -> None:
        rc, out = _run_checker(_template(eol="\r\n"))
        self.assertEqual(rc, 0, out)

    def test_bom_clean_file_passes(self) -> None:
        rc, out = _run_checker(b"\xef\xbb\xbf" + _template())
        self.assertEqual(rc, 0, out)

    def test_bom_on_first_assignment_line_is_parsed(self) -> None:
        key, rand = _fake_api_key()
        data = b"\xef\xbb\xbf" + ("ANTHROPIC_API_KEY=" + key + "\n").encode() + _template()
        rc, out = _run_checker(data)
        self.assertFlagged(rc, out, line=1, name="ANTHROPIC_API_KEY")
        self.assertNoLeak(out, key, rand)

    def test_crlf_with_secret_fails(self) -> None:
        key, rand = _fake_api_key()
        rc, out = _run_checker(_template(api=key, eol="\r\n"))
        self.assertFlagged(rc, out, line=API_LINE, name="ANTHROPIC_API_KEY")
        self.assertNoLeak(out, key, rand)

    def test_other_line_separators_do_not_hide_an_assignment(self) -> None:
        # A lone CR (or VT, FF, NEL, U+2028) ends a line for many readers;
        # the key after it must be checked, under git's LF line number.
        value = "v" + uuid.uuid4().hex
        for sep in ("\r", "\x0b", "\x0c", "\x85", "\u2028"):
            with self.subTest(sep=sep):
                line = "LOG_LEVEL=info" + sep + "SMTP_PASSWORD=" + value
                rc, out = _run_checker(_template(extra=[line]))
                self.assertFlagged(rc, out, line=API_LINE + 1)
                self.assertNoLeak(out, value, value[1:])

    def test_lone_cr_file_with_secret_fails(self) -> None:
        # To git this whole file is line 1.
        value = "v" + uuid.uuid4().hex
        data = ("DATABASE_URL=" + _fake_dsn("PASSWORD") + "\r"
                "SMTP_PASSWORD=" + value + "\r"
                "junk " + value + "\r").encode()
        rc, out = _run_checker(data)
        self.assertFlagged(rc, out, line=1)
        self.assertNoLeak(out, value, value[1:])

    def test_invalid_utf8_or_nul_with_secret_fails(self) -> None:
        pw, rand = _fake_db_password()
        for junk in (b"\xff\xfe", b"\x00"):
            with self.subTest(junk=junk):
                data = junk + b"\n" + _template(db=pw) + junk
                rc, out = _run_checker(data)
                self.assertFlagged(rc, out, line=DB_LINE + 1, name="DATABASE_URL")
                self.assertNoLeak(out, pw, rand)


# ---------------------------------------------------------------------------
# Leak side channels
# ---------------------------------------------------------------------------
class TestCheckerNoLeakChannels(_LeakAssertions):
    def test_bare_dsn_line_does_not_leak_through_key_name(self) -> None:
        # Split on its first '=', this line's "key" contains the password.
        pw, rand = _fake_db_password()
        line = _fake_dsn(pw) + "?sslmode=require"
        rc, out = _run_checker(_template(extra=[line]))
        self.assertFlagged(rc, out, line=API_LINE + 1)
        self.assertNoLeak(out, pw, rand)

    def test_non_identifier_key_name_is_not_printed(self) -> None:
        key, rand = _fake_api_key()
        name = "zz" + uuid.uuid4().hex[:12] + "_key"
        rc, out = _run_checker(_template(extra=[name + "=" + key]))
        self.assertFlagged(rc, out, line=API_LINE + 1)
        self.assertNotIn(name, out)
        self.assertNoLeak(out, key, rand)

    def test_provider_token_in_prose_comment_fails(self) -> None:
        key, rand = _fake_api_key()
        rc, out = _run_checker(_template(extra=["# my key: " + key]))
        self.assertFlagged(rc, out, line=API_LINE + 1)
        self.assertNoLeak(out, key, rand)

    def test_private_key_block_fails(self) -> None:
        rand = uuid.uuid4().hex
        block = ["-----" + "BEGIN PRIVATE KEY" + "-----", "MII" + rand,
                 "-----" + "END PRIVATE KEY" + "-----"]
        rc, out = _run_checker(_template(extra=block))
        self.assertFlagged(rc, out, line=API_LINE + 1)
        self.assertNoLeak(out, block[1], rand)

    def test_github_annotations_carry_line_and_name_only(self) -> None:
        key, rand = _fake_api_key()
        rc, out = _run_checker(_template(api=key), "--github")
        self.assertEqual(rc, 1, out)
        self.assertIn("::error file=env.template,line=%d::ANTHROPIC_API_KEY" % API_LINE, out)
        self.assertNoLeak(out, key, rand)

    def test_success_prints_nothing(self) -> None:
        rc, out = _run_checker(_template(db="CHANGE_ME"))
        self.assertEqual((rc, out), (0, ""))


# ---------------------------------------------------------------------------
# The CI workflow runs the same checker and never prints a value
# ---------------------------------------------------------------------------
class TestCheckEnvTemplateWorkflow(unittest.TestCase):
    def setUp(self) -> None:
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_runs_the_shared_checker(self) -> None:
        self.assertIn("python3 -I .githooks/check_env_template.py", self.text)

    def test_triggers_cover_hook_and_checker_on_every_branch(self) -> None:
        self.assertEqual(self.text.count("'.githooks/**'"), 2)
        self.assertEqual(self.text.count("'env.template'"), 2)
        self.assertNotIn("branches", self.text)

    def test_no_value_printing_or_injection_surface(self) -> None:
        for bad in ("$password", "add-mask", "set -x", "bash -x",
                    "${{ github.event", "setup-python"):
            self.assertNotIn(bad, self.text)
        self.assertIn("permissions:\n  contents: read", self.text)

    def test_failure_text_says_rotate(self) -> None:
        self.assertIn("rotate", self.text.lower())


# ---------------------------------------------------------------------------
# The pre-commit hook, end-to-end through `git commit` in a temp repo
# ---------------------------------------------------------------------------
def _git_env(home: Path, path: Optional[str] = None) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_EDITOR": "true",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
    })
    if path is not None:
        env["PATH"] = path
    return env


@unittest.skipUnless(GIT and BASH, "git and bash are required for hook tests")
class TestPreCommitHook(_LeakAssertions):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "home").mkdir()
        self.env = _git_env(self.root / "home")
        self.repo = self._make_repo(self.root / "repo")
        # Positive control in EVERY test: the repo's current env.template
        # commits cleanly through the hook (on an unborn branch).
        self._write(REAL_TEMPLATE.read_bytes())
        self.git("add", "env.template")
        rc, out = self.commit()
        self.assertEqual(rc, 0, "clean env.template commit failed: " + out)
        self.initial = self.head()

    # -- harness ----------------------------------------------------------
    def _make_repo(self, path: Path, hooks_path: Optional[Path] = None) -> Path:
        path.mkdir()
        self.git("-c", "init.defaultBranch=main", "init", "-q", cwd=path)
        for key, value in (("user.name", "Guard Test"),
                           ("user.email", "guard-test@example.invalid"),
                           ("commit.gpgsign", "false")):
            self.git("config", key, value, cwd=path)
        target = hooks_path or (path / ".githooks")
        shutil.copytree(str(HOOKS_DIR), str(target),
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.git("config", "core.hooksPath",
                 str(hooks_path) if hooks_path else ".githooks", cwd=path)
        return path

    def git(self, *args: str, cwd: Optional[Path] = None,
            env: Optional[dict] = None) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            [GIT, *args], cwd=str(cwd or self.repo), env=env or self.env,
            capture_output=True, timeout=TIMEOUT)
        return proc

    def commit(self, *extra: str, cwd: Optional[Path] = None,
               env: Optional[dict] = None) -> Tuple[int, str]:
        proc = self.git("commit", "-q", "-m", "test commit", *extra, cwd=cwd, env=env)
        return proc.returncode, (proc.stdout.decode("utf-8", "replace")
                                 + proc.stderr.decode("utf-8", "replace"))

    def head(self, cwd: Optional[Path] = None) -> str:
        return self.git("rev-parse", "HEAD", cwd=cwd).stdout.decode().strip()

    def _write(self, data: bytes, cwd: Optional[Path] = None) -> None:
        (cwd or self.repo).joinpath("env.template").write_bytes(data)

    def assertBlocked(self, rc: int, out: str, head_before: str,
                      cwd: Optional[Path] = None) -> None:
        self.assertNotEqual(rc, 0, "commit was not blocked: " + out)
        self.assertIn(HOOK_MARKER, out)
        self.assertEqual(self.head(cwd), head_before, "HEAD moved")

    def assertCommitted(self, rc: int, out: str, head_before: str,
                        cwd: Optional[Path] = None) -> None:
        self.assertEqual(rc, 0, out)
        self.assertNotIn(HOOK_MARKER, out)
        self.assertNotEqual(self.head(cwd), head_before, "HEAD did not move")

    # -- positive controls ------------------------------------------------
    def test_00_positive_controls(self) -> None:
        self.assertTrue(self.initial)
        self._write(_template(db="CHANGE_ME"))   # touches env.template; clean
        self.git("add", "env.template")
        rc, out = self.commit()
        self.assertCommitted(rc, out, self.initial)
        before = self.head()
        (self.repo / "notes.txt").write_text("x\n")
        self.git("add", "notes.txt")
        rc, out = self.commit()
        self.assertCommitted(rc, out, before)
        self.assertEqual(out, "")

    # -- blocking ---------------------------------------------------------
    def test_staged_db_password_is_blocked_without_leaking(self) -> None:
        pw, rand = _fake_db_password()
        self._write(_template(db=pw))
        self.git("add", "env.template")
        rc, out = self.commit()
        self.assertBlocked(rc, out, self.initial)
        self.assertIn("DATABASE_URL", out)
        self.assertNoLeak(out, pw, rand)

    def test_staged_api_key_is_blocked_without_leaking(self) -> None:
        key, rand = _fake_api_key()
        self._write(_template(api=key))
        self.git("add", "env.template")
        rc, out = self.commit()
        self.assertBlocked(rc, out, self.initial)
        self.assertIn("ANTHROPIC_API_KEY", out)
        self.assertNoLeak(out, key, rand)

    def test_unborn_branch_with_secret_is_blocked(self) -> None:
        other = self._make_repo(self.root / "fresh")
        key, _ = _fake_api_key()
        self._write(_template(api=key), cwd=other)
        self.git("add", "env.template", cwd=other)
        rc, out = self.commit(cwd=other)
        self.assertNotEqual(rc, 0, out)
        self.assertIn(HOOK_MARKER, out)
        self.assertNotEqual(self.git("rev-parse", "-q", "--verify", "HEAD",
                                     cwd=other).returncode, 0)

    # -- index vs worktree ------------------------------------------------
    def test_staged_secret_with_clean_worktree_is_blocked(self) -> None:
        key, rand = _fake_api_key()
        self._write(_template(api=key))
        self.git("add", "env.template")
        self._write(_template())                   # worktree clean again
        rc, out = self.commit()
        self.assertBlocked(rc, out, self.initial)
        self.assertNoLeak(out, key, rand)

    def test_clean_index_with_dirty_worktree_is_allowed(self) -> None:
        staged = _template(db="CHANGE_ME")
        self._write(staged)
        self.git("add", "env.template")
        key, _ = _fake_api_key()
        self._write(_template(api=key))            # never staged
        rc, out = self.commit()
        self.assertCommitted(rc, out, self.initial)
        self.assertEqual(self.git("cat-file", "blob", "HEAD:env.template").stdout, staged)

    def test_only_pathspec_commit_with_worktree_secret_is_blocked(self) -> None:
        key, rand = _fake_api_key()
        self._write(_template(api=key))            # not added
        rc, out = self.commit("env.template")      # --only mode, temp index
        self.assertBlocked(rc, out, self.initial)
        self.assertNoLeak(out, key, rand)

    # -- empty / deleted: same verdict as CI ------------------------------
    def test_deleted_env_template_is_blocked_like_ci(self) -> None:
        self.git("rm", "-q", "--cached", "env.template")   # worktree keeps it
        rc, out = self.commit()
        self.assertBlocked(rc, out, self.initial)
        self.assertEqual(_run_checker(None)[0], 1)

    def test_empty_env_template_is_blocked_like_ci(self) -> None:
        self._write(b"")
        self.git("add", "env.template")
        rc, out = self.commit()
        self.assertBlocked(rc, out, self.initial)
        self.assertEqual(_run_checker(b"")[0], 1)

    # -- regressions ------------------------------------------------------
    def test_large_staged_file_list_does_not_skip_the_check(self) -> None:
        # A long staged-name list sorting after env.template used to SIGPIPE
        # the `--name-only | grep -q` gate (exit 141) and skip the check
        # (reliably from ~1000 names of this length).
        for i in range(1500):
            (self.repo / ("z" * 240 + "%05d" % i)).write_text("x\n")
        pw, rand = _fake_db_password()
        self._write(_template(db=pw))
        self.git("add", "-A")
        rc, out = self.commit()
        self.assertBlocked(rc, out, self.initial)
        self.assertNoLeak(out, pw, rand)

    def test_commit_from_a_subdirectory_is_checked(self) -> None:
        sub = self.repo / "sub"
        sub.mkdir()
        key, rand = _fake_api_key()
        self._write(_template(api=key))
        self.git("add", "env.template")
        rc, out = self.commit(cwd=sub)
        self.assertBlocked(rc, out, self.initial)
        self.assertNoLeak(out, key, rand)

    def test_absolute_hooks_path_outside_repo(self) -> None:
        other = self._make_repo(self.root / "abs", hooks_path=self.root / "abs-hooks")
        self._write(_template(), cwd=other)
        self.git("add", "env.template", cwd=other)
        rc, out = self.commit(cwd=other)
        self.assertEqual(rc, 0, out)
        before = self.head(cwd=other)
        pw, rand = _fake_db_password()
        self._write(_template(db=pw), cwd=other)
        self.git("add", "env.template", cwd=other)
        rc, out = self.commit(cwd=other)
        self.assertBlocked(rc, out, before, cwd=other)
        self.assertNoLeak(out, pw, rand)

    # -- fail closed / fast path -----------------------------------------
    def test_missing_checker_blocks_env_commit_but_not_others(self) -> None:
        (self.repo / ".githooks" / "check_env_template.py").unlink()
        (self.repo / "notes.txt").write_text("x\n")
        self.git("add", "notes.txt")
        rc, out = self.commit()
        self.assertCommitted(rc, out, self.initial)
        self.assertEqual(out, "")
        before = self.head()
        self._write(_template(db="CHANGE_ME"))
        self.git("add", "env.template")
        rc, out = self.commit()
        self.assertBlocked(rc, out, before)

    def test_missing_python_blocks_env_commit_but_not_others(self) -> None:
        tools = self.root / "bin"
        tools.mkdir()
        for tool in ("bash", "git", "dirname", "cat"):
            found = shutil.which(tool)
            if found is None:
                self.skipTest(tool + " not found")
            os.symlink(found, str(tools / tool))
        env = _git_env(self.root / "home", path=str(tools))
        (self.repo / "notes.txt").write_text("x\n")
        self.git("add", "notes.txt", env=env)
        rc, out = self.commit(env=env)
        self.assertCommitted(rc, out, self.initial)
        self.assertEqual(out, "")
        before = self.head()
        self._write(_template(db="CHANGE_ME"))
        self.git("add", "env.template", env=env)
        rc, out = self.commit(env=env)
        self.assertBlocked(rc, out, before)

    # -- one rule engine: hook verdict == CI verdict ----------------------
    def test_hook_and_ci_reach_the_same_verdict(self) -> None:
        pw, _ = _fake_db_password()
        key, _ = _fake_api_key()
        fixtures: List[Tuple[str, Optional[bytes]]] = [
            ("placeholder", _template(db="CHANGE_ME")),
            ("crlf clean", _template(eol="\r\n")),
            ("db secret", _template(db=pw)),
            ("api secret", _template(api=key)),
            ("export", _template(extra=["export ANTHROPIC_API_KEY=" + key])),
            ("commented key", _template(extra=["# ANTHROPIC_API_KEY=" + key])),
            ("test db url", _template(extra=["TEST_DATABASE_URL=" + _fake_dsn(pw)])),
            ("crlf secret", _template(api=key, eol="\r\n")),
            ("no DATABASE_URL", _template().replace(b"DATABASE_URL=", b"DB_URL=")),
            ("empty", b""),
            ("deleted", None),
        ]
        for label, data in fixtures:
            with self.subTest(fixture=label):
                self.git("reset", "-q", "--hard", self.initial)
                if data is None:
                    self.git("rm", "-q", "env.template")
                else:
                    self._write(data)
                    self.git("add", "env.template")
                rc, out = self.commit()
                ci_rc, _ = _run_checker(data)
                self.assertEqual(rc == 0, ci_rc == 0, label + ": " + out)
                if rc != 0:
                    self.assertIn(HOOK_MARKER, out)


if __name__ == "__main__":
    unittest.main()
