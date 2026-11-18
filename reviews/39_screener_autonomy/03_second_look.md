# 39 — Lead checks: second look at the fixes

A fresh-context reviewer looked again after the fixes of the adversarial
review (`02_decision_note.md`). It was limited to reading the code, the
three offline test commands and mutations of copies of the scripts: an
earlier second-look reviewer, resumed twice, was lost each time to a
restart of the container, most likely to its own multi-account harness
(the stage force-stops every process of the sandbox account, and the
session runs as root). **Nothing below was run with separate accounts.**

## The first review's twelve findings

Ten closed, two partly:

- **5, DNS:** the pin worked for a plain name; a trailing dot reopened it
  (new finding 1, fixed). `quotecheck.py` itself is still not pinned.
- **10, pace:** the pause was per process. Now a lock beside the counter
  makes calls follow one another; a site's longer Crawl-delay is still
  not carried from one call to the next.

## New findings

| # | Finding | State |
|---|---|---|
| 1 | The pin was keyed by the raw host string: `name.` and `name` were two keys, so the connection looked the name up afresh (confirmed offline) | Fixed: the trailing dot is dropped, a host with `%` is refused |
| 2 | The card still named a contamination site in the wording "N ft away (NAME)", and a name with ")" leaked its tail | Fixed in the card and in `changes.py`: every wording `grade.py` uses, to the end of the item |
| 3 | `changes.py | head -1` under `pipefail`: the alert lost the digest and the log said no `changes.md` was written; 4 of 8 parallel test runs failed | Fixed (`sed -n 1p`); four parallel runs now pass |
| 4 | A failed kill returned before the settings were compared | Fixed: nothing is taken, the settings are still compared, then the stage stops |
| 5 | Any agent error without a file stopped the stage with no try, so one lead could block all leads on every run | Fixed: only an error within two minutes is "the agent did not run"; later it is a try |
| 6 | A crash of the quote checker counted a try against the lead | Fixed: only its refusal or its time limit does; a crash stops the stage, no try |
| 7 | Where `/proc` hides other accounts' processes, "none left" was read from nothing | Fixed: the sandbox account's own count must also read 0 |
| 8 | Accounts were compared by name | Fixed: by number; root is refused |
| 9 | `LEAD_HOME` was not checked to be the account's home | Fixed |
| 10 | After a clock step backwards an old output folder could pass the freshness test | Fixed: the folder must also not have been there when the run started |
| 11 | `sandbox_remove` would follow `..` in its argument (no caller passed one); the county name went unchecked into a path | Fixed |
| 12 | The stage's umask made published files unreadable to others (640) | Fixed: 644 in a 755 folder |

## Mutations

The tests failed, as they should, with `"Bash"` added to the agents'
tools, `-I` removed from the quote check, the freshness test removed,
`sandbox_seal` made a no-op, the settings comparison removed, and the
stage's umask removed. Not caught: the stage's own `cd /` removed (every
command as a sandbox account has its own), and the `chmod` pass before a
removal (as root the test passes without it).

## Still open

- Everything that needs real separate accounts (`sudo`, the kill, the
  sweep of the shared temporary folders, group modes), the real Claude
  Code (exit codes, what `Write` reaches, whether a normal run touches a
  watched path), and real DNS.
- No test reaches return code 4 (a sandbox folder that cannot be
  emptied): as root it cannot be made to happen.
- The fixes of this round were checked by the tests added with them, not
  by a further independent review.
