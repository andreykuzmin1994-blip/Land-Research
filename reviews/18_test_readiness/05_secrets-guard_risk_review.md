# Risk and Architecture Review: secrets-guard (Tier 2, SR-12)

Role: Agent 1 (before code). Base 29c051e. No repo file changed.
Scope: `.githooks/pre-commit`, `.github/workflows/check-env-template.yml`, an optional shared checker under `.githooks/`, new `tests/test_env_template_guard.py`. `env.template` stays byte-identical.

## What was verified empirically (scratch repo, isolated git config)

| Probe | Current behavior |
|---|---|
| Real-looking `ANTHROPIC_API_KEY` staged | **Allowed**: confirms the requirement |
| Real-looking DB password staged | Blocked, but **the password is echoed to stderr**: confirmed |
| `export DATABASE_URL=...realpw...` or ` DATABASE_URL=...` appended (clean line 11 kept) | **Allowed** (grep `^DATABASE_URL=` only sees line 11) |
| `TEST_DATABASE_URL=postgresql://u:<realpw>@h/db` appended | **Allowed** |
| env.template staged with a secret + >64 KB of later-sorting staged filenames | **Allowed (fail-open)**: `git diff --cached --name-only \| grep -qx` under `pipefail` returns 141 (SIGPIPE), so `if ! ...; then exit 0` skips the check |
| Staged secret, clean worktree / `git commit <path>` (`--only`, temp `GIT_INDEX_FILE`) / `git commit -a` | Blocked (the current hook correctly reads the index; this must be preserved) |
| Hook file without the exec bit | **Silently skipped** (git prints only a hint), so the commit succeeds |
| Empty env.template / deleted env.template | Hook **allows** both; CI **fails** both, so the two already drift |
| Current env.template | UTF-8 with non-ASCII (em dash) in comments; LF; no commented assignments; no `://` outside line 11; assignments on lines 11 and 15 |

Environment trap: this container's global git config has `commit.gpgsign=true` with an external ssh signer, and this repo's `.git/config` sets an **absolute** `core.hooksPath` to the main checkout's `.githooks`. A commit made in a worktree runs the main checkout's (old) hook. Exercise the new hook only through temp repos.

## Risks, ranked

### Critical

**SG-1: The guard is itself a leak vector (SR-12, SR-8).** CI line ~38 prints `'$password'` in the failure branch, and the success branch (~35) also prints the slot. So any allowlist bug echoes a real value into the Actions log. The hook prints it to stderr, which ends up in agent transcripts and terminal scrollback. A fix that only removes the obvious `echo` still leaks through these side channels:
- *Malformed-line "key" channel.* Splitting an unparseable line on its first `=` can give a "key" that is secret material, e.g. a bare DSN `postgresql://u:<pw>@h/db?sslmode=require` yields key `postgresql://u:<pw>@h/db?sslmode`. Base64 continuation lines of a multi-line private key can also look like identifiers.
- *Partial-value channel.* Prefixes, suffixes, lengths or "sk-ant-…xyz" hints are still leaks. The original incident was a *fragment*.
- *Exceptions.* `ValueError(f"...{line}")`, `repr()` in an error, or a traceback with data in the message.
- *Tooling.* `set -x`/`bash -x`, `shell: bash -x` in the workflow, and `::add-mask::<value>`, which writes the value as a command line. Do not use add-mask as the fix.
- *Tests.* `assertEqual(value_from_real_env_template, "")` prints the real value into the public offline-tests log when it fails.
**Mitigation:** output carries only (a) the line number and (b) the key name, and only when the name matches a strict identifier such as `^[A-Z_][A-Z0-9_]{0,63}$` (case-insensitive is fine if length-capped). Otherwise print the line number alone. Use the same rule on the success path. Wrap `main` in a catch-all that prints a fixed message (no data) and exits non-zero. Tests that touch the real file assert only on return codes and sanitized output.

**SG-2: The refactor silently checks the wrong bytes (fail-open of the only preventive control).** A natural "shared script" refactor calls `checker env.template` from the hook. That checks the **worktree**, not the commit. A staged secret with a clean worktree (e.g. after `git add -p`, or edit, add, revert) then passes. Two related traps: writing the blob to a temp file inside the worktree leaves a secret-bearing untracked file behind on a crash (SR-7, picked up by a later `git add -A`), and unsetting or ignoring `GIT_INDEX_FILE` breaks `git commit <path>` (`--only` mode).
**Mitigation:** the hook streams the index blob via `git cat-file blob :env.template | <checker> -`, with no temp file. It must not touch `GIT_INDEX_FILE` or use `env -i`. Tests cover staged-only, worktree-only and `--only` cases.

### High

**SG-3: SIGPIPE fail-open in the gate (confirmed, existing bug).** `pipefail` combined with `grep -q` gives exit 141, which is read as "not staged".
**Mitigation:** `if git diff --cached --quiet -- env.template; then exit 0; fi`. Exit 1 means changed; any other code is an error and continues into the check, so it fails closed. This also works on an unborn branch (verified). Add a regression test using about 300 staged files with names of about 250 characters that sort after `env.template` (more than 64 KB of names, and fast).

**SG-4: A broken or slow hook halts every commit, including the loop's `exp:` commits.** AUTORESEARCH_MECHANICS step 4 has the agent run `git commit` on each experiment, and the loop is NEVER-STOP. Causes include a bash syntax error, `python3` missing from a GUI client's PATH, the checker not found via a cwd-relative path, or Python started on every commit. The opposite failure is a hook that loses its exec bit and is silently skipped (confirmed).
**Mitigation:** (a) the gate runs **before** any interpreter or checker lookup, and commits that don't touch env.template exit 0 with no output. (b) Locate the checker relative to the hook (`dirname -- "$0"`), never via cwd or `git rev-parse`, so it works with both the relative `.githooks` (documented) and absolute hooksPath. (c) A missing interpreter or checker **when env.template is staged** exits non-zero with a fixed message (fail closed). (d) Invoke the checker through an explicit interpreter so its mode bit doesn't matter. (e) Keep `.githooks/pre-commit` at mode 100755.

**SG-5: The parser is narrower than what leaks.** Bypasses confirmed or obvious:
- `export KEY=`, leading whitespace, `KEY = v`, quoted values (`"..."`, `'...'`), lower-case keys.
- Duplicate keys (the first clean, a later one real).
- Commented-out assignments (`# ANTHROPIC_API_KEY=<real>`, a common "parking" habit).
- Credential URLs under other names (`TEST_DATABASE_URL`, `DATABASE_POOLER_URL` and `DIRECT_URL`; reviews/01 already proposed the first two).
- A libpq `?password=` / `sslpassword=` query parameter.
**Mitigation:** check **every** assignment, including commented ones (`^\s*#?\s*(export\s+)?NAME\s*=`). Apply the password-slot rule to **any** value containing `scheme://user:pass@`, whatever the key, plus any `password=` query parameter. Lines that are neither blank, comment nor parseable fail closed (line number only). Treat quotes as a matched pair only (`""` is empty).

**SG-6: Hook and CI drift even with one script.** Drift lives in the wrappers:
- Empty or deleted file semantics, which already differ today.
- Gating.
- Exit-code mapping, e.g. `|| true` or `$(...)` swallowing errors.
- Interpreter differences: a developer's `python3` may be 3.8 or 3.9 (macOS system Python is 3.9), while CI runs 3.12.
**Mitigation:** all rules live in the one checker. The hook only gates, fetches the blob and propagates the exit code. The workflow only runs `python3 .githooks/<checker> env.template`. Empty and missing file both **fail**, identically in hook and CI (the hook distinguishes deleted from empty with `git cat-file -e :env.template` and passes the deletion case to the checker as missing, so it fails). The checker is stdlib-only, uses Python ≥3.8 syntax (no `match`, no runtime `X | Y`) and runs with `-I` or with no cwd imports.

**SG-7: Tests that pass vacuously or leak (SR-6).**
- In CI (no git identity) and in this container (`commit.gpgsign=true`), `git commit` fails for reasons that have nothing to do with the hook. A "blocked" assertion based only on `rc != 0` then **passes** with the hook broken.
- Inherited `GIT_DIR`/`GIT_INDEX_FILE`/`GIT_WORK_TREE` (e.g. tests run from inside a hook) send subprocess git into the **real** repo.
- Editor or credential prompts hang.
**Mitigation:**
- Positive control: a clean env.template commit **succeeds** in the same harness.
- "Blocked" asserts rc != 0, HEAD unchanged, **and** a hook-specific marker in stderr.
- Environment: `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`, `HOME=<tmp>`, `-c user.name/-c user.email -c commit.gpgsign=false`, always `-m`, `GIT_EDITOR=true`, `GIT_TERMINAL_PROMPT=0`.
- Scrub inherited `GIT_*` vars in the **test harness only** (not in the hook). Use `cwd=tmp`, a `timeout=` on every subprocess, and `TemporaryDirectory` cleanup.
- Pair every "secret absent from output" assertion with a positive "key name and line number present" assertion.

### Medium

**SG-8: Encoding and line endings.**
- The real file contains UTF-8 em dashes. A text-mode read under `LC_ALL=C` (Python < 3.7 or a misconfiguration) or `ascii` crashes on the clean file. Shell `grep` in "binary file matches" mode (invalid byte or NUL) prints no lines and so skips the key scan (fail-open).
- CRLF turns `ANTHROPIC_API_KEY=` into the value `"\r"`, a false positive. A BOM breaks the first key.
**Mitigation:** read bytes and decode UTF-8 with `errors="replace"` or `surrogateescape`, strip a trailing `\r` and a leading BOM. Tests: clean file under `LC_ALL=C` passes; CRLF clean passes; CRLF with a secret fails; an invalid byte plus a secret fails.

**SG-9: Workflow coverage and hardening.**
- Add `.githooks/**` (hook and checker) to **both** the push and pull_request `paths`, keeping env.template and the workflow file. Keep `push` unfiltered by branch so web-UI commits trigger it.
- Use preinstalled `python3`; avoid `setup-python` (it is slow against `timeout-minutes: 1`).
- Add `permissions: contents: read`.
- No `${{ github.event.* }}` in `run:`.
- Emit `::error file=env.template,line=N::<KEY> ...` only with identifier-validated names (prevents workflow-command injection).
- Add an unconditional backstop: the new test "repo env.template passes the checker" runs in `offline-tests.yml` on **every** push, so a path-filter mistake can't open a gap.

**SG-10: Secret-shaped literals in committed files.** GitHub push protection and partner secret scanning match full provider tokens (the Anthropic key format is covered) and may match credential URLs.
**Mitigation:** build fakes at runtime, e.g. `"sk-" + "ant-" + "api03-" + uuid4().hex * 2`, and a DSN from parts with a random password. Docstrings, comments, the checker's own messages and the commit message carry no full example token or realistic DSN. Bare prefixes (`"sk-ant-"`) in a detection list are fine.

**SG-11: CI is detection after the leak, not prevention.** By the time the workflow fails, the commit is already on GitHub (as in incident b87db2b). The failure message must say "treat the credential as leaked: rotate it, then fix forward" and must not imply that reverting is enough. The hook is the only preventer, which is why SG-2, SG-3 and SG-4 rank high.

**SG-12: Allowlist design.** Use **exact-token** allowlists taken from the current file:
- DB slot: `URL_ENCODED_PASSWORD`, `CHANGE_ME`, `PASSWORD`, and empty (the existing set).
- Other secret keys: empty (the current value), plus at most `CHANGE_ME`.

Do **not** use shape-based allowlists such as "ALL_CAPS looks like a placeholder": AWS access key IDs are all-caps alphanumeric. Key patterns `*PASS*` and `*KEY*` will flag `COMPASS`, `PGPASSFILE` and `KEYBOARD_*`. That is acceptable as fail-closed, provided the message names the `--no-verify` override in the hook only.

### Low

- **SG-13: DSN parse ambiguity.** Multiple `@`, a user with no `:`, other schemes, key/value DSNs. Fail closed on anything that is not a single-`@` `scheme://user:SLOT@host...`. Do not try to emulate libpq.
- **SG-14: Paths outside the hook.** Merges without conflicts, cherry-pick and `--no-verify` skip pre-commit, and a PR can edit the checker it is judged by. This is inherent; CI on main plus branch protection or CODEOWNERS (human) is the backstop.
- **SG-15: POSIX and macOS portability.** Keep the hook compatible with bash 3.2: no `${v,,}`, `mapfile` or associative arrays. Avoid `readlink -f`, `sed -i`, `grep -P` and `echo -e`; use `printf`. GUI git clients have a minimal PATH (`/usr/bin/python3` may be a CLT shim that fails, which is acceptable because it fails closed). Signals: with no temp files there is nothing to trap or clean up.
- **SG-16: Doc and process drift.** The SR-12 "Guard" column (STANDING_RISKS.md) and CLAUDE.md "Developer setup" don't mention the shared checker or test. They are canonical docs outside this track's file list, so flag them to the human rather than edit them. The Tier-2 decision note ("record which model ran", STANDING_RISKS § Change tiers) conflicts with this session's no-model-names rule and with the allowed file list. Agent 3 records the decision in its output and leaves the in-repo note to the human.

## Standing-risk mapping

- **Apply:** SR-12 (primary), SR-8 (never log credentials), SR-6 (offline hermetic tests, no network or DB), SR-7 (no temp files in the worktree; the checker writes nothing), SR-10 (stdlib only; no pre-commit framework, gitleaks or detect-secrets without a human decision).
- **Not applicable:** SR-1/3/4/5/9/13/14/15/16 (no metric, DB, loop or log code). Also SR-2 and SR-11 (no SQL, no network).

## Downstream effects

- Metric, keep-or-revert, purge, discovery gate, frozen screener key: **none** (no runtime module touched; screener/ untouched).
- Loop operator flow: the hook runs on every `exp:` commit (SG-4).
- CI: the new test file runs in offline-tests on every push and must pass on ubuntu-latest with no git identity (SG-7).
- Root count goes from 636 to 636+N; screener stays at 23.

## Required tests (beyond the happy path)

Run the **real checker as a subprocess** (the same entry point the hook and CI use).

1. Repo env.template passes, including under `LC_ALL=C`; there are no value-bearing assertion messages.
2. Each DB placeholder (`URL_ENCODED_PASSWORD`, `CHANGE_ME`, `PASSWORD`, empty) passes; `ANTHROPIC_API_KEY=` and `=""` pass.
3. A random DB password fails. Output has `DATABASE_URL` and `11`, and contains neither the full secret nor any 8-character window of its random part.
4. A random API key fails, with the same assertions on `ANTHROPIC_API_KEY` and `15`.
5. New `*KEY*`, `*TOKEN*`, `*SECRET*`, `*PASSWORD*` and `*PASS*` keys each fail with a non-placeholder value.
6. Each of these fails: missing DATABASE_URL, a commented-only DATABASE_URL, a duplicate DATABASE_URL whose second value is real, an empty file and a missing file.
7. Parsing variants each fail with a secret: `export`, leading spaces, ` = `, quoted, lower-case key, commented-out key, `TEST_DATABASE_URL` credential URL, `?password=` parameter.
8. Encoding: CRLF clean passes, CRLF with a secret fails, and an invalid UTF-8 byte or NUL plus a secret fails.
9. Leak channel: an unparseable bare-DSN line with `?sslmode=require` fails and its password is absent from the output.
10. Hook end-to-end in a temp repo:
    - Clean commit succeeds (positive control).
    - A staged DB secret is blocked with the marker in stderr, HEAD unchanged and the secret absent; the same holds for an API key.
    - A staged secret with a clean worktree is blocked; a clean index with a dirty worktree is allowed; `git commit -m x env.template` (`--only`) with a worktree secret is blocked.
    - It works with relative `.githooks` (copied into the temp repo) and with an absolute hooksPath.
    - With the checker absent: a commit not touching env.template succeeds silently, and an env.template commit is blocked.
    - SIGPIPE regression from SG-3.
    - Deletion semantics match the CI-mode result.

## Spec corrections

See the structured summary. In short:
- Scan every credential URL.
- Scan commented assignments.
- Validate key names before printing them.
- Make empty and deleted semantics identical (fail).
- Replace the `grep -q` gate.
- Check the index blob, not the worktree.
- Don't print on the success path, and never use add-mask.
- Use exact-token allowlists.
- Have CI messages instruct rotation.
- Add `.githooks/**` to the paths, not just the script.

## Go/no-go gates for Agent 3

See the structured summary (G1–G15).
