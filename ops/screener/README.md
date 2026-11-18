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
  or mailing address: the owner columns are not copied, and the finished
  text is searched for every owner name and mailing address of both runs,
  each one found being replaced by "(name withheld)" (a listed
  contamination site is often named after the company that owns the
  parcel). A misspelt name is not caught. The line it prints holds
  counts only.
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
- The skeptic starts in a new folder after the investigator's is deleted,
  and what the agents' account left in `/tmp`, `/var/tmp` and `/dev/shm`
  is deleted between them. It gets the card and the checked findings.
- Before any command runs as a sandbox account, the supervisor leaves the
  checkout (a folder a process is already in stays readable whatever its
  parents' modes say) and sets its own umask; every mode in a sandbox
  folder is set by path.
- Each quote check has a time limit, the whole stage a budget
  (`INVESTIGATE_BUDGET`), and a lead that fails twice is left alone (the
  alert counts those; delete a lead's counter in
  `~/screener-ops/investigate/attempts/` to have it tried again).
- If an agent changes its own account's Claude settings, plugins or
  rules, its copy of Claude Code, its shell start-up files or its SSH
  folder, the stage stops, nothing of that lead is kept, an urgent alert
  goes out, and lead checks stay on hold until you remove
  `~/screener-ops/investigate/HOLD`. Look at `/home/screener-lead` first;
  when in doubt delete that user and make it again. A watch that cannot
  look counts as a change.
- The stage also stops, with an urgent alert and without counting a try,
  when a sandbox folder cannot be emptied or a sandbox process cannot be
  stopped, when the agent ends with an error within two minutes and
  leaves nothing (an expired login, an option Claude Code no longer
  accepts), and when the quote checker itself fails. A refusal of the
  findings by the checker, a time limit, or an agent error after real
  work is a try of that lead.
- `LEAD_HOME` must be the home of `LEAD_USER`, and the accounts are told
  apart by number: two names for one account, or root, are refused.

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
trial), a run that finished with warnings, a run whose output folder was
already there before the supervisor started (a second run on one day
writes into the first one's folder), and when a part is missing (an
alert says which).

### Not verified, and what is accepted

This was built and tested offline with stand-in agents
(`test_investigate.sh`); the quote checker in those tests is the real one.
**It has not been run on a server with the real Claude Code.** Check these
on the first run, with `INVESTIGATE_MAX=1`:

- that Claude Code accepts the options used (`--permission-mode dontAsk`,
  `--allowedTools` with one `Bash(...)` rule, `--disallowedTools`), and
  that the agent can run the fetch tool and nothing else. The agent's log
  shows a refused tool call;
- that the one `Bash(...)` rule does not also let through a command
  with `$(...)`, a redirection, a second line or a leading `VAR=value`.
  Claude Code documents that such a rule does not cover `cmd && other`;
  the rest was not verified. If any of them passes, the fetch rule is a
  shell: leave lead checks off;
- that `screener-lead` cannot write outside its lead folder in
  `/srv/screener-lead` (try it by hand as that user), and what the
  `Write` tool can reach beyond the working folder (its own home, at
  least);
- that a normal run does not trip the settings watch (Claude Code
  updating itself would: the stage sets `DISABLE_AUTOUPDATER=1`);
- the sandbox itself with three real accounts. The offline tests run
  everything as one user; an independent reviewer ran the scripts with
  three accounts and real `sudo` in a container, not on a server.

Accepted, because closing them needs more than this script:

- `screener-lead` owns its Claude login and its own copy of Claude Code.
  An agent that a web page takes over could misuse that login or change
  that copy. The settings watch catches a change to the files it lists,
  not every route (`~/.claude.json`, which Claude Code rewrites on every
  run, is not watched).
- Both agents are one account. An investigator set on it could leave a
  note for the skeptic somewhere in that account's home. The skeptic's
  independence rests on its fresh folder and context and on the quote
  check of its own claims, not on separate accounts.
- The quote checker keeps a copy of every page it fetches until its
  folder is removed, up to 200 MB a page. A free-disk floor
  (`LEAD_MIN_FREE_MB`, 5000) is asked before each agent and each check;
  there is no cap on one check's total.
- The quote checker looks a host name up twice (once to check that its
  addresses are public, once to connect). The agents' fetch tool pins
  the name to the addresses it checked; the checker itself
  (`screener/quotecheck.py`, also used by hand) does not yet.
- The request counter of the fetch tool, and the lock that makes two
  calls follow one another, sit in the agent's own folder: they stop
  drift, not an agent set on cheating. The time limit bounds that. Each
  call waits a second first; a site's own longer Crawl-delay holds
  inside one call, not from one call to the next.
- The search tool is the provider's: it can show a snippet of any site,
  and the rule against CoStar, LoopNet and Crexi there rests on the
  agent's instructions. The fetch tool and the quote checker refuse those
  sites themselves.
- A PDF the agents cite is read by `pdftotext` as `screener-check`. That
  account owns nothing, but a flaw in `pdftotext` would still run code on
  the server.

## Why leads fail (the tally)

`lead_tally.py` reads every lead folder's checked findings and writes
`screener/leads/_tally.md`: how many leads the skeptic killed, weakened or
let stand, the reasons with their leads, and for a reason that keeps
coming up, which screen or grade setting to look at.

```bash
python ops/screener/lead_tally.py screener/leads --run screener/output/fulton-2026-11-17
```

- A reason is read two ways: from the checked sections' verdicts (fixed
  values the quote checker kept only with a quote behind them), and from
  the skeptic's own words by fixed word patterns. The second is a reading
  aid, not a measurement: every lead is listed so a count can be checked
  against its `lead.md`.
- With `--run`, each reason says how many of its leads still come through
  as grade A or B in the newest run that has them, and how many are in
  none of the runs given. A run made before a lead was checked could not
  know of the check: the count says what the screens let through.
- No model proposes anything: "what to look at" is fixed text per reason.
  `screens.py` and `settings.toml` stay yours, and a change to them needs
  a new evaluation baseline.
- The file names companies where the skeptic's objection does, like
  `lead.md`, so it stays in the leads folder. The line it prints holds
  counts only. The supervisor refreshes it after the lead checks.

On the 29 leads checked by hand up to 2026-11-13, measured against the run
of that day: 27 of the 29 killed or weakened leads were still grade A or B.
By number of leads (a lead can have several reasons): held by an operating
company as buffer or expansion land (14), industrial use not by right
(12), neighbours or the road (12), a developer or investor already holds
or pursues it (11), the adopted plan is against it or the screener read a
superseded land-use layer (11), not raw land (8), a shape that cannot
hold a building (2).

An independent reader checked the word patterns against all 29
objections before they were last adjusted: about 9 in 10 of the reasons
they assigned were right, and they found about 7 in 10 of the reasons the
text states (8 in 10 with the checked sections). Treat a count as "about
this many", and read the leads.

## Agenda watch

Between the weekly runs, `agenda_watch.py` looks at the council and
planning agenda pages you list, once a day, for the parcels of the newest
run. No AI model is involved: fixed code fetches the index pages, reads
the documents that are new, and searches for

- a watched parcel's **id** (whatever separates its characters);
- a watched parcel's **site address** (a possible match, only in the
  sources that cover its city);
- in the **titles** the index page gives its documents: a street that
  watched parcels are on, and zoning case numbers;
- **industrial zoning words** close together on a page.

The watch list is the grade A and B leads, the parcels marked pursue or
maybe (in the run or since, in `decisions.csv`), and the top 50, of each
county's newest full run. It writes `~/screener-ops/agenda/agenda-watch.md`
and sends an alert only when there is something to say.

**What it is worth.** A hit says the words are in that document. It does
not say the item is about your parcel, that it is current, or what was
decided. And no hit does not mean no item: most cities post adopted
ordinances as scans without text (on South Fulton's page on 2026-11-18,
the first two documents read were both scans), so for those only the
title is searched; a source can be down or change its addresses; an
agenda amended under the same address after it was read is not read
again. The report counts what was not read, every time.

On that first trial against South Fulton's page, with the run of
2026-11-13 as the watch list, the titles alone gave four street matches,
among them the Spence Road rezoning (a lead marked maybe) and the Bishop
Road case (a grade B lead), which the document checks had earlier found
by hand.

### Setting it up

1. As root: `ops/screener/install.sh --agenda-watch`. It creates the
   `screener-agenda` account and `/srv/screener-agenda`, installs
   `pdftotext` and the daily timer (10:20 UTC).
2. Write `~/.config/screener-agenda-sources.json` from
   `agenda_sources.example.json` and `chmod 600` it. Per source: a name,
   the index page, the path prefixes its documents sit under, the cities
   it covers as `ranked.csv` writes them, and how often you expect it to
   post. **Before you list a site, read its robots.txt and terms
   yourself.** The watch honours robots.txt and never works around a
   wall (a site that refuses it is reported as not answering).
3. A trial by hand, as `screener`, in one process (no sandbox):

   ```bash
   python ops/screener/agenda_watch.py run --sources ~/.config/screener-agenda-sources.json --output-root screener/output --state ~/screener-ops/agenda-trial
   ```

   Then `systemctl start screener-agenda` and read
   `~/screener-ops/logs/agenda-*.log`.

Until the sources file exists the timer does nothing and says nothing.

### How it is kept safe and polite

- Agenda packets hold documents that applicants and the public filed. The
  account that makes the requests and runs `pdftotext` is
  `screener-agenda`, which owns nothing; `pdftotext` runs under limits on
  memory, processor time and output. The run user hands it the parcel ids
  and site addresses to search for (no owner, grade or decision), takes
  back one result file, and keeps of it only what fits the job: that
  source's host and paths, parcels of the watch list.
- Requests go through the quote checker's fetcher: robots.txt and its
  Crawl-delay, public https addresses only, no redirect to another host.
  On top: only links found on the listed index page, on its host, under
  the listed path prefixes; at most 10 new documents a source a run, 80
  requests and 300 MB a run.
- A source seen for the first time has its list noted and three documents
  read. A known source whose list is suddenly mostly new is noted, not
  crawled, and you are told. A document counts as seen only once its text
  was searched; one that cannot be read is tried on three runs and then
  listed for you.
- It never opens the supervisor's lock; it skips a day on which a
  screener run is active. It writes nothing into the checkout.
- The alert holds counts only. `agenda-watch.md` shows a few words of the
  document around a hit, as plain text: they are public record and can
  name an applicant or an owner. Tax-roll owner names of the runs are
  withheld where they appear in roll spelling, no more.

Not verified: a run on a server under the `screener-agenda` account. The
offline tests run the wrapper as one user with a stand-in for the scan;
the scan itself is tested against a made-up site and was run by hand
against South Fulton's page.

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
| `lead_tally.py` | `_tally.md`: why leads fail their document check, counted |
| `agenda_watch.py` | The agenda watch: prepare, scan, report |
| `agenda-watch.sh`, `screener-agenda.service`, `.timer` | Its daily run on the server, with the scan under its own account |
| `agenda_sources.example.json` | The shape of the sources file you write |
| `test_agenda.sh` | Offline tests of the agenda watch's wrapper |
| `test_changes.py`, `test_lead_tools.py`, `test_lead_tally.py`, `test_agenda_watch.py` | Offline tests of the Python tools |

Tests (offline, from the repository root):

```bash
ops/screener/test_supervise.sh
```

```bash
ops/screener/test_investigate.sh
```

```bash
ops/screener/test_agenda.sh
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
