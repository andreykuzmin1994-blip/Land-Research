# 39 — Unattended lead checks: risk review before the change (Tier 2)

An independent, fresh-context reviewer enumerated the risks of the lead
checks from a written design, before `ops/screener/investigate.sh` was
written. It read `ops/screener/supervise.sh`, `install.sh`,
`screener/INVESTIGATE.md` and `screener/quotecheck.py`; it wrote and ran
nothing. Its 15 risks and 21 required controls are summarised here with
what was done about each. The design changed because of it: the first
plan reused the fix hand-off's account, gave the agents `curl`, ran the
quote check as the run user and was on by default.

## The risks, most serious first

| # | Risk | What was done |
|---|---|---|
| 1 | The sandbox account can be taken over for good: `curl` reads, uploads and writes files; a writable copy plus a runnable `quotecheck` is arbitrary code; that account owns its Claude login and binary, so a change survives the kill and poisons later fix hand-offs | Agents run as their own account (`screener-lead`), never the fixer's. No `curl`, `pdftotext`, `WebFetch` or `quotecheck` tool: one fetch command (`lead_fetch.py`) run by the system Python in isolated mode from a folder the agent cannot write. The copy is read-only but for one leaf folder. A watch on the account's Claude settings, shell start-up files and `.ssh` puts the stage on hold. **Accepted:** the account still owns its login and its copy of Claude Code |
| 2 | Owner name and mailing address leave the server (model context, searches, URLs) | The agents get a lead card built from an allow-list: no owner name, mailing address, flags, seller notes, decision or price. The brief is not handed over |
| 3 | The quote check as the run user puts the account that owns the checkout, key and cache in front of attacker-chosen PDFs (`pdftotext`) and fetches | Option C: a third account (`screener-check`) with no home and nothing of its own runs `quotecheck.py` with the system Python, isolated, from a copy no agent touched |
| 4 | A failed kill is only logged; a live process can swap a file between check and copy | `kill_user` fails the lead when a process survives. Files are taken with one open: no link followed, regular file, one name, size cap |
| 5 | Leftover copies leak between leads, to the skeptic, and to or from the fix hand-off | Own handover folders. The folder must be empty before each agent starts; removal is verified; an EXIT / TERM trap removes copies |
| 6 | Default-on reaches existing servers by `git pull`, and fires on a `--limit` trial | `INVESTIGATE=0` by default. Skipped for runs with extra arguments, runs with warnings, and when a part is missing |
| 7 | Integrity beyond the quotes: an injected verdict; a file labelled with another parcel or run; a hash read from agent-influenced output | The file must name this parcel and this run. The findings hash is computed by trusted code from the staged file. `--headline` is never passed. The alert says "raised no objection (an agent's reading, not a clearance)". **Accepted:** the verdict is an agent's opinion |
| 8 | robots.txt and site terms rest on the agent's honour | The fetch tool is the quote checker's own fetcher: robots.txt, pace, refused names, public addresses. **Accepted:** the search tool can still show a snippet of any site |
| 9 | Runaway cost, time and disk | Timeout on each agent and each quote check, a stage budget, a free-disk floor, two tries per lead, `--max-turns` when set. The quote checker's page copies are deleted with its folder |
| 10 | Interaction with the supervisor (soils window, quota, exit code, stale folder) | The stage runs after every county and the summary alert, has its own alert, never changes the exit code, and takes only run folders written by this supervisor run |
| 11 | Partial failure: mixed or half results, a human's folder overwritten | Published only as a complete set of four files, in one rename, never over a folder that exists. A lead with any folder is skipped |
| 12 | Alerts that paste log tails or agent text | Fixed words and counts only |
| 13 | Folder-name rule differs from the quote checker's | The same patterns (a test compares them); two ids that would share a folder are skipped and counted |
| 14 | Retention of staging and logs | Staging is removed per county and pruned after 14 days; the agents' logs follow the 90-day rule |
| 15 | Tax-roll text in the prompt | County text is one line per field, markup removed, and labelled as data |

## Controls not taken, and why

- A throwaway home and a root-owned Claude binary for each agent run
  (control 4): a subscription login lives in that home and is refreshed
  there, so a copy per run would break it. Left as an accepted risk, with
  the settings watch and a documented reset (delete the user).
- A scan of the tool-call log for owner data (control 7): the agents are
  not given owner data, and a public document may rightly show an owner.
- Path-scoped `Read` / `Write` rules (control 3, second half): their
  exact behaviour could not be verified offline. The unix modes do the
  work; the README asks for a check by hand on the first run.
- A request counter outside the agent's reach (control 2): the counter is
  in the agent's own folder. The time limit and the per-site pace bound a
  cheat.
