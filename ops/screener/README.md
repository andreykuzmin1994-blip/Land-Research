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
   - any symlink in the copy, or a change touching `rank.py`,
     `scoring.py`, `screens.py`, any `screener/*.toml` or listed settings
     file, `autoresearch/`, `inputs/`, a `.gitignore`/`.gitattributes`,
     an existing test (renames count as delete + add), a file mode, or
     anything outside `screener/` → **refused and discarded** (the patch
     is kept in the logs for reading);
   - no change (an outage, or a source gone for good) → the diagnosis is
     sent to you, nothing else;
   - otherwise the unit tests run (as `screener-fix`, since the new code
     is untrusted), the fix is committed to the branch, and you get "fix
     ready for review".

   The path rules cannot judge what allowed code does: an edit to an
   allowed module could still change results in a roundabout way. Your
   review of every fix before merging is the real control. Merge nothing
   you don't understand.
5. Other counties carry on regardless. A summary alert ends the run.

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

   It creates the `screener-fix` user and `/srv/screener-fix`, makes
   `/home/screener` private, lets `screener` run commands as
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

Logs: `~/screener-ops/logs/` (one file per run and attempt, Claude's
output and diagnosis per hand-off, `alerts.log`), kept 90 days.
Screener outputs stay in `screener/output/` as usual.

Adding a county later: give it a settings file (e.g.
`screener/settings-will.toml` with `county = "will"`) and add it to
`COUNTIES`.
