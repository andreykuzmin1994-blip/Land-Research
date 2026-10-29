# Running Land Research on a home Windows PC

Fill in one settings file, run the installer once, then double-click
**Start Land Research**. The PC runs the experiment loop day and night; you
check on it with **Status** and stop it with **Stop**.

## What you need

- A Windows 10 or 11 PC that stays on (set sleep to **Never**).
- Your Supabase **Session pooler** connection string (port 5432).
- A GitHub account with access to this repository.
- A Claude account (Claude Code signs in with it).

## One-time setup

1. Copy the `home_machine/windows/` folder anywhere on the PC, for example
   `Documents\Land Research`. It is the only folder you will use.
2. Open `settings.txt` in Notepad. Paste your `DATABASE_URL`, set
   `COSTAR_FOLDER` to the folder where you save CoStar exports, and save.
   This file holds your database password: keep it on that PC only.
3. Double-click `Install.bat`.
   - The first time, it installs Ubuntu on WSL (Windows asks for admin
     permission) and asks you to restart. After the restart, Ubuntu asks you
     to pick a username and password. Then run `Install.bat` again.
   - It then installs the tools, signs you in to GitHub (a code to enter in
     your browser), downloads the repository to Ubuntu's `~/Land-Research`,
     checks the database, and opens Claude Code once so you can sign in
     (type `/exit` when done).
   - It offers to start Land Research automatically when you sign in to
     Windows, so it resumes after updates and restarts.

## Every day

| Double-click | What it does |
|--------------|--------------|
| `Start Land Research.bat` | Starts or resumes the run. The first time, it creates the run's branch, records the baseline (up to 90 minutes) and asks you to type `yes` to confirm it. Then the loop runs; the window shows its log. Minimize it. |
| `Status.bat` | Running or stopped, the run, the last 15 experiments, recent activity. |
| `Stop Land Research.bat` | Stops after the experiment in progress. |
| `Start New Run.bat` | Starts a fresh run with a new baseline. Use it after `parameters.json` or `prepare.py` change, or when a run ended. |

**CoStar exports:** save the CSVs into your `COSTAR_FOLDER`. Names must start
with `submarket_stats_`, `land_sales_comps_`, `building_sales_comps_`,
`leasing_comps_` or `land_listings_`. New files are picked up before the next
experiment.

## How the loop works

`home_machine/autopilot.sh` runs one experiment per round, per
`AUTORESEARCH_MECHANICS.md`:

1. Claude Code, in a fresh session with `agent_prompt.md`, reads the history
   and commits ONE change to `research.py` as `exp: ...`.
2. The script refuses anything else: a commit that touches another file, more
   than one commit, or uncommitted edits are thrown away unevaluated.
3. `python cli.py loop --max 1` (the immutable runner) evaluates it and writes
   the keep/discard row to `experiment_log.tsv`.
4. Kept commits are pushed to the run's `autoresearch/<tag>` branch; anything
   else is reverted with `git reset --hard`.

It stops on Stop (`.halt`), after 7 days, after 5 crashed or timed-out
experiments in a row, or when the database or harness has failed for over an
hour. Logs: `~/.land-research/autopilot.log` and one file per agent round in
`~/.land-research/agent-logs/` (inside Ubuntu; from Windows Explorer,
`\\wsl$\Ubuntu\home\<you>\`).

## Limits

- The PC must stay on and awake. A restart interrupts the experiment in
  progress; it is dropped and the next one starts on the next Start.
- An experiment killed mid-way (power cut, the 120-minute safety stop) can
  leave score rows no purge targets (`AUTORESEARCH_MECHANICS.md`, "Purge scope
  and known residue paths").
- Each round runs Claude Code once, which counts toward your Claude plan's
  usage.
