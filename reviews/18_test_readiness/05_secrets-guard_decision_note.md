# Tier-2 Decision Note: env.template secrets guard

> Tier 2 per `STANDING_RISKS.md` § "Change tiers": credentials and
> secrets hygiene (SR-12). Between runs; no autoresearch run active.
> Risk review: `05_secrets-guard_risk_review.md` (Agent 1, SG-1..SG-16,
> gates G1..G15).

- **Roles and context independence**: risk reviewer (Agent 1), implementer
  (Agent 2) and adversarial reviewer/committer (Agent 3) each ran in an
  independent fresh context on the session's strongest available model.
  Agent 3 did not write the code. It applied Agent 2's patch to a clean
  worktree at `29c051e` and reviewed it there.
- **Verdict**: APPROVE, with two reviewer fixups (below). Gates G1..G15
  pass.

## What changed

1. **`.githooks/check_env_template.py` (new, stdlib only)**: the single
   rule engine. Every `scheme://user:SLOT@` URL on any line, including
   comments, must hold an exact DB placeholder (`URL_ENCODED_PASSWORD`,
   `CHANGE_ME`, `PASSWORD`, empty). The same applies to every
   `password=`/`sslpassword=` parameter. Every assignment whose name
   contains KEY, TOKEN, SECRET or PASS must be empty or `CHANGE_ME`. This
   holds for commented, exported, quoted and lower-case names, and for a
   later `NAME=` on the same line. Provider-token prefixes and PEM headers
   fail anywhere. Unparseable lines fail. An empty or missing file fails,
   and so does a file without an uncommented `DATABASE_URL`. Findings
   carry the line number and the key name only when it is a plain
   identifier. No value or fragment of one is printed. A catch-all exits
   2 with a fixed message.
2. **`.githooks/pre-commit`**: now only gates, streams the checker the
   INDEX blob and propagates the exit code. The gate is
   `git diff --cached --quiet -- env.template`, which replaces the
   SIGPIPE-prone `--name-only | grep -q`. The blob comes from
   `git cat-file blob :env.template | python3 -I checker -`, with no temp
   file, and `GIT_INDEX_FILE` is left untouched. A staged deletion is
   passed to the checker as `--missing`. A missing checker or `python3`
   fails closed, but only when env.template is staged.
3. **`.github/workflows/check-env-template.yml`**: runs the same checker
   with `--github`. `.githooks/**` is added to both trigger path lists,
   and push still has no branch filter, so web-UI commits trigger it. It
   adds `permissions: contents: read`. The `$password` echo on both
   branches is gone. The failure text says to treat the credential as
   leaked and rotate it.
4. **`tests/test_env_template_guard.py` (new, 61 tests)**: covers the
   checker on fixtures and the real hook end-to-end via `git commit` in
   throwaway repos. All fakes are built at runtime.

## Verification (Agent 3, independently)

- Suites: root `discover -s tests`: 697 OK (636 + 61). Screener: 23 OK.
  The guard module was also run on Python 3.11 and 3.12, and with a
  hostile `GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE` (61 OK, and the
  hostile dir stayed empty) (SR-6).
- Real `env.template` passes all three entry points: path, stdin,
  `--github`, and the path again under `LC_ALL=C`. It also passes through
  the hook in a temp repo. Rc 0 and no output.
- Tests fail on base: hook and workflow tests run against the base hook
  and workflow give 26 failures and 1 error. Mutation testing of the
  patched code was also run. Each of these was caught by at least one
  test:
  - the old grep gate;
  - the hook reading the worktree;
  - `unset GIT_INDEX_FILE`;
  - an ignored exit status;
  - deletion allowed;
  - a missing checker allowed;
  - printing any key name;
  - echoing raw lines;
  - dropping the URL rule or the inline scan;
  - a shape allowlist;
  - case-sensitive names;
  - empty allowed;
  - no BOM strip;
  - no catch-all;
  - strict decode.

  Two mutations survived, removing the `splitlines()` split and the
  commented-assignment group. Both are defense in depth: the inline
  `NAME=` scan still flags the same fixtures.
- Hook probes: all of these were blocked with HEAD unchanged and no leak:
  - `commit -a`;
  - `--amend`;
  - `git mv` away;
  - a conflicted merge resolved with a secret;
  - a symlinked env.template;
  - a repo path with spaces.

  A non-env commit gave empty output.
- CI simulation (step script under `bash -e`): the real file passes. A DB
  secret, an API key, a deleted file and an empty file each fail with
  `::error` lines that carry only the line number and name.
- SR-12 and SR-8: a grep of the diff for full token shapes, PEM blocks and
  non-placeholder DSN passwords finds only placeholders. No model names.
  SR-10: no new dependencies. SR-7: no temp files from the hook.
  Invariant files are byte-identical to `29c051e` and there is no
  `experiment_log.tsv`. `pre-commit` is at mode 100755.

## Phase-specific risks found and what changed as a result

- The old CI printed the password slot on both the success and failure
  branches (SG-1). Removed. The checker never prints values.
- A SIGPIPE fail-open in the old gate let a real DB password through with
  more than 64KB of staged names (SG-3). Fixed, with a regression test.
- The old hook and CI disagreed on an empty or deleted file (SG-6). Both
  now fail.
- The old checks were bypassed by `export`, leading whitespace, other DSN
  names and commented keys (SG-5). All are covered now.

## Reviewer fixups (Agent 3)

- Test file: a literal invisible U+2028 in a string replaced with
  `" "`.
- Test file: the ambiguous loop variable `l` renamed to `line`.

## Deviations

None from the track spec. Agent 1's spec corrections were adopted in
full: credential URLs under any key, commented assignments,
identifier-only names, empty and deleted files failing, the
index-blob hook, exact-token allowlists, and rotation text.

## Residual gaps and human follow-ups

- **Bypasses that skip the hook** (accepted, inherent): merges without
  conflicts, cherry-pick and `--no-verify` skip the hook, and a PR can
  edit its own checker. CI plus branch protection or CODEOWNERS is a
  repo-settings decision for the human.
- **Old Actions logs**: earlier `check-env-template` runs echoed the
  password slot. Glance at past failed runs.
- **Docs**: optionally mention `.githooks/check_env_template.py` and the
  test in the SR-12 Guard column of `STANDING_RISKS.md` and in
  `CLAUDE.md` "Developer setup". Both are still accurate as written. The
  "~1s" comment in `offline-tests.yml` is now stale: the suite takes
  about 7s.
- **Rule-based, not an entropy scanner**: a bare secret under a
  non-secret name with no known prefix still passes. So does a
  scheme-less `user:pass@host`.
- **False positives**: `COMPASS`/`PGPASSFILE`-style names, `key = x` in
  prose, and lower-case `password` in example DSNs all fail closed. CI
  has no override, so a legitimate future env.template entry of that
  kind needs a Tier-2 checker change.
- **Pathological input**: thousands of `NAME=` pairs on one line run in
  quadratic time (32KB takes about 1s; 1MB hangs). This fails closed: the
  hook blocks and CI times out.
- **Portability**: not run on macOS, bash 3.2 or Python 3.8. That rests
  on review plus an `ast` parse at `feature_version=(3, 8)`.
- **Hook installation**: a hook copied or symlinked into `.git/hooks`
  rather than installed via `core.hooksPath .githooks` fails closed with
  "checker not found", and only when env.template is staged.
- **This container**: the absolute `core.hooksPath` points at the main
  checkout, so until this lands there, commits there still run the old
  hook.
