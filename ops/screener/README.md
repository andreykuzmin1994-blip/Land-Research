# Unattended screener runs (always-on server)

`supervise.sh` runs the land screener (`screener/`) on a schedule, retries
when a run stops, sends alerts, and, when every retry fails, hands the
failure to Claude Code for a diagnosis and a proposed fix that a human
reviews. It never merges anything, never edits the checkout the runs use,
and never touches `main`.

These files live outside `screener/` on purpose: adding or changing them
does not reset the screener AutoResearch baseline.

## What happens in a run

1. Pull the latest `main` (fast-forward only; skipped, with an alert, if
   the server's checkout has local changes) and install requirements.
2. For each county settings file in `COUNTIES`, run
   `python -m screener.run --settings <file>`.
3. If it stops: wait 1 hour, run again, then 6 hours, then again
   (`RETRY_WAITS`). If the stop message mentions `--refresh`, the next
   try uses `--refresh`. Runs wait out the USDA soils service's nightly
   outage (about 00:00–02:00 Mountain). A settings or argument error
   (exit 2) and a run that screens out every parcel are reported at once,
   without retries or a hand-off: there is nothing to wait out.
4. All retries failed: Claude Code gets the error. It runs **as a separate
   user, `screener-fix`**, on a **plain copy of the code with no git
   directory** and its own copy of the map cache, in `/srv/screener-fix/`.
   So nothing it edits or runs can reach the live checkout, its git config
   or hooks, the live cache, or the `screener` user's files (config, SSH
   keys). It may change only data-access code (the county module,
   `arcgis.py`, `layers.py`, `zoning.py` code lists), and is told never to
   make a check pass by skipping it. The supervisor then copies the result
   into a fresh worktree on a new `screener-fix/<county>-<time>` branch
   and checks it with git (hooks and fsmonitor off):
   - any symlink, FIFO or other non-regular file in the copy, a file over
     5 MB or over 200 MB in all (refused unread), or a change touching `rank.py`,
     `scoring.py`, `screens.py`, any `screener/*.toml` or listed settings
     file, `autoresearch/`, `inputs/`, a `.gitignore`/`.gitattributes`,
     an existing test (renames count as delete + add), a file mode, or
     anything outside `screener/` → **refused and discarded** (the patch
     is kept in the logs for reading);
   - no change (an outage, or a source gone for good) → the diagnosis is
     sent to you, nothing else;
   - otherwise the unit tests run on a fresh copy of exactly what gets
     committed (as `screener-fix`, since the new code is untrusted; the
     result is advisory, as the code under test is the fix itself), the fix is committed to the branch, and you get "fix
     ready for review".

   The path rules cannot judge what allowed code does: an edit to an
   allowed module could still change results in a roundabout way. Your
   review of every fix before merging is the real control. Merge nothing
   you don't understand.
5. Other counties carry on regardless. A summary alert ends the run.
6. Each run that finished gets a `changes.md` in its output folder: what
   is new, gone or moved since the run before (see "What changed since
   the last run"). Its one line of counts goes in the summary alert.
7. With `INVESTIGATE=1` (off by default), the new grade A and B leads of
   each clean run are then checked against public documents (see "Lead
   checks").

After you merge a fix: any change under `screener/` other than `rank.py`
means re-recording the AutoResearch baseline
(`python -m screener.autoresearch.evaluate --decide --new-baseline`)
before the next experiment.

## Server setup (Ubuntu 24.04, e.g. Hetzner)

1. Base packages and firewall (as root):

   ```bash
   apt update && apt -y upgrade && apt -y install git python3-venv unattended-upgrades ufw curl sudo
   ```

   ```bash
   ufw allow OpenSSH && ufw --force enable
   ```

2. A user for the runs:

   ```bash
   adduser --disabled-password --gecos "" screener
   ```

3. As that user (`su - screener`): a read-only deploy key, then the clone.
   On GitHub: repository → Settings → Deploy keys → Add, paste the `.pub`,
   leave "Allow write access" unticked.

   ```bash
   ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519 && cat ~/.ssh/id_ed25519.pub
   ```

   ```bash
   git clone git@github.com:andreykuzmin1994-blip/Land-Research.git && cd Land-Research
   ```

   ```bash
   python3 -m venv .venv && .venv/bin/pip install -r screener/requirements.txt
   ```

4. A short trial, still as `screener`:

   ```bash
   ops/screener/supervise.sh --limit 300
   ```

   (Too small a limit can leave nothing after the screens; that is
   reported as "found nothing to screen".)

5. The sandbox user, settings and weekly timer (back as root):

   ```bash
   /home/screener/Land-Research/ops/screener/install.sh
   ```

   It creates the `screener-fix` user and `/srv/screener-fix` (owned by
   `screener`, so the fixer can't plant entries there), makes
   `/home/screener` private, denies `screener-fix` cron and at jobs (so
   nothing it runs outlives a hand-off), lets `screener` run commands as
   `screener-fix` (a step down, never up), gives `screener-fix` its own
   Python, copies `screener-ops.env.example` to
   `/home/screener/.config/screener-ops.env` if none exists, and enables
   `screener-supervise.timer` (Sundays 15:00 UTC; a run missed while the
   server was off starts at the next boot).

6. Claude Code for the hand-off, **as `screener-fix`**. Install it per
   <https://code.claude.com/docs> (the native installer puts it in
   `~/.local/bin/claude`), then run `claude` once and log in (the login
   link can be opened on any device). Skip this and set `CLAUDE_FIX=0` if
   you only want retries and alerts.

   ```bash
   sudo -iu screener-fix
   ```

7. Alerts: install the **ntfy** app on your phone, subscribe to a long
   random topic name, and put `NTFY_URL="https://ntfy.sh/<that-topic>"` in
   `/home/screener/.config/screener-ops.env`. Without it, alerts only go
   to `~/screener-ops/logs/alerts.log`.

8. Run one now and watch it:

   ```bash
   systemctl start screener-supervise
   ```

   ```bash
   journalctl -u screener-supervise -f
   ```

## What changed since the last run

`changes.py` compares a finished run folder with the run before it and
writes `changes.md` beside `leads.md`:

```bash
python ops/screener/changes.py screener/output/fulton-2026-11-17 --leads screener/leads
```

It lists the grade A and B leads that are new and the ones that are gone
(with what became of each), parcels that entered, left or moved 10 or
more places in the top 50, ranked parcels with a new deed or a new owner
on the tax roll, what happened to the parcels you marked pursue or maybe,
and the failed-check notes of `summary.md`. With `--leads`, each lead
shows its document check (killed, weakened or survives).

- The run before is the newest earlier run folder of the same county
  beside the run (`--previous DIR` names another). A trial run (`--limit`)
  is never taken as the run before a full run.
- It says so at the top when the code or the settings file differ between
  the two runs: a difference may then come from that, not from new data.
- `changes.md` holds site addresses and parcel ids, never an owner's name
  or mailing address. The line it prints holds counts only.
- It reads files only, and changes no score, grade, rank or decision. The
  supervisor runs it after every run that finished; the on-demand runbook
  can run the same command in place of its own comparison.

## Lead checks (off by default)

After a clean scheduled run, the supervisor can have each new grade A
lead, then each new grade B lead, checked the way
`screener/INVESTIGATE.md` describes: an investigator agent reads public
documents (ordinances, staff reports, plans, county GIS), the quote
checker re-fetches every page it cites and keeps only claims whose quote
is on the page, a skeptic agent in a fresh folder looks for the strongest
reason the lead fails, and the quote checker runs once more. The four
result files land in `screener/leads/<parcel>/` (kept out of git), where
`changes.md` and the viewer read the verdict. A lead that already has a
folder there is never checked again or overwritten.

A result is a reading aid. It changes no score, grade, rank or decision,
and "the skeptic raised no objection" is an agent's reading, not a
clearance. Read `lead.md` before acting on a lead.

### What leaves the server

- **To Anthropic (Claude):** the lead card of one parcel at a time: parcel
  id, site address, city, acres, zoning, the screener's map facts and the
  lead grade. Never the owner's name or mailing address, the flags, the
  seller notes or your decision: the card is built from an allow-list
  (`lead_queue.py`). The agents may still read an owner's name in a public
  document, as a person would.
- **To the web:** page requests to public sites, made only through
  `lead_fetch.py`, which applies the quote checker's rules for the agents
  (robots.txt, at least a second between requests to a site, public
  addresses only, never CoStar, LoopNet or Crexi), and web searches (at
  most 10 per agent by its instructions; the search tool itself has no
  counter).
- **In alerts:** fixed words and counts. No parcel, address, owner or
  agent text.

### How the parts are kept apart

| Account | Runs | Can reach |
|---|---|---|
| `screener` | the screener, `lead_queue.py`, `changes.py`, the copying in and out | everything of the runs |
| `screener-lead` | the two agents | one lead card, the rules, the fetch tool (all read-only) and one folder to write its one file in |
| `screener-check` | `quotecheck.py` with the system Python | the file being checked; it owns nothing and no agent ever runs as it |

- The agents do not run as `screener-fix`, so an agent that a web page
  talked into something cannot reach the code-fix hand-off.
- An agent has no shell command but the fetch tool, no `curl`, no
  `WebFetch`, no `quotecheck`. Its file is taken back only if it is one
  regular file of at most 1 MB that names this parcel and this run.
- The skeptic starts in a new folder after the investigator's is deleted.
  It gets the card and the checked findings, nothing else.
- Each quote check has a time limit, the whole stage a budget
  (`INVESTIGATE_BUDGET`), and a lead that fails twice is left alone.
- If an agent changes its own account's Claude settings, shell start-up
  files or SSH folder, the stage stops, nothing of that lead is kept, an
  urgent alert goes out, and lead checks stay on hold until you remove
  `~/screener-ops/investigate/HOLD`. Look at `/home/screener-lead` first;
  when in doubt delete that user and make it again.

### Turning it on

1. As root: `ops/screener/install.sh --lead-checks`. It creates the two
   accounts and their handover folders and installs `pdftotext`.
2. Install Claude Code as `screener-lead` and log in, as for the fixer
   (step 6 above): `sudo -iu screener-lead`.
3. Put `INVESTIGATE=1` in `~/.config/screener-ops.env`. Start with
   `INVESTIGATE_MAX=1` and `INVESTIGATE_GRADES="A"`.
4. Run the supervisor once and read `~/screener-ops/logs/lead-*.log` (the
   agents' own output and the quote checker's) and the lead's `lead.md`.

Lead checks are skipped for a run given extra arguments (a `--limit`
trial), a run that finished with warnings, and when a part is missing
(an alert says which).

### Not verified, and what is accepted

This was built and tested offline with stand-in agents
(`test_investigate.sh`); the quote checker in those tests is the real one.
**It has not been run on a server with the real Claude Code.** Check these
on the first run, with `INVESTIGATE_MAX=1`:

- that Claude Code accepts the options used (`--permission-mode dontAsk`,
  `--allowedTools` with one `Bash(...)` rule, `--disallowedTools`), and
  that the agent can run the fetch tool and nothing else. The agent's log
  shows a refused tool call;
- that `screener-lead` cannot write outside its lead folder in
  `/srv/screener-lead` (try it by hand as that user);
- that a normal run does not trip the settings watch.

Accepted, because closing them needs more than this script:

- `screener-lead` owns its Claude login and its own copy of Claude Code.
  An agent that a web page takes over could misuse that login or change
  that copy. Nothing else on the server is within its reach, and the
  settings watch catches the plain routes, not every one.
- The request counter of the fetch tool sits in the agent's own folder: it
  stops drift, not an agent set on cheating. The time limit bounds that.
- The search tool is the provider's: it can show a snippet of any site,
  and the rule against CoStar, LoopNet and Crexi there rests on the
  agent's instructions. The fetch tool and the quote checker refuse those
  sites themselves.
- A PDF the agents cite is read by `pdftotext` as `screener-check`. That
  account owns nothing, but a flaw in `pdftotext` would still run code on
  the server.

## Pushing fix branches to GitHub (optional)

By default a proposed fix stays on the server (`git -C ~/Land-Research
branch --list 'screener-fix/*'`, and a `.patch` in the logs). To have it
pushed so you can review it on GitHub:

1. Protect `main` first: repository → Settings → Branches → add a rule for
   `main` requiring a pull request. A write key can otherwise push to it.
2. Make a second key, `ssh-keygen -t ed25519 -N "" -f ~/.ssh/screener_fix_ed25519`,
   and add it as a deploy key **with** write access.
3. In `~/.config/screener-ops.env`: `FIX_PUSH=1` and
   `FIX_SSH_KEY="$HOME/.ssh/screener_fix_ed25519"`.

## Files

| File | Purpose |
|---|---|
| `supervise.sh` | The supervisor (all settings via `~/.config/screener-ops.env`) |
| `screener-ops.env.example` | Settings, with defaults and comments |
| `screener-supervise.service`, `.timer` | systemd units: weekly, no timeout |
| `install.sh` | Creates the `screener-fix` sandbox user and installs the units (root) |
| `test_supervise.sh` | Offline tests with a fake screener and fake Claude |
| `changes.py` | `changes.md`: what changed since the run before |
| `investigate.sh` | The lead checks, sourced by `supervise.sh` (off unless `INVESTIGATE=1`) |
| `lead_queue.py` | Picks the leads, writes their cards, takes files out of a sandbox, publishes a result |
| `lead_fetch.py` | The agents' one way to read a page, under the quote checker's rules |
| `test_investigate.sh` | Offline tests of the lead checks: fake agents, the real quote checker |
| `test_changes.py`, `test_lead_tools.py` | Offline tests of the three Python tools |

Tests (offline, from the repository root):

```bash
ops/screener/test_supervise.sh
```

```bash
ops/screener/test_investigate.sh
```

```bash
python -m unittest discover -s ops/screener -p 'test_*.py'
```

Logs: `~/screener-ops/logs/` (one file per run and attempt, Claude's
output and diagnosis per hand-off, one `lead-*.log` per lead check,
`alerts.log`), kept 90 days.
Screener outputs stay in `screener/output/` as usual.

Adding a county later: give it a settings file (e.g.
`screener/settings-will.toml` with `county = "will"`) and add it to
`COUNTIES`.
