You are the research agent in a running AutoResearch experiment loop on this
repository, on its `autoresearch/<tag>` branch. This invocation is ONE
experiment's code change. A wrapper script (`home_machine/autopilot.sh`) will
evaluate your commit with the immutable runner and keep or revert it. Work
without asking questions; nobody is watching this session.

1. Read `CLAUDE.md`, then in `AUTORESEARCH_MECHANICS.md` the sections "The
   Five-File Contract", "The Experiment Loop", "The Metric" and "The
   Simplicity Criterion", then `program.md` (the human's strategic direction).
2. Read the history: `python cli.py log --tail 40` and `git log --oneline -15`.
   Discarded experiments appear only in the log, not in git.
3. Form ONE hypothesis from that history about how to raise
   `actionable_pipeline_count` legitimately: better discovery, more accurate
   sub-scores, better strategy-fit or actionability inputs. Do not repeat an
   idea the log shows was already discarded unless you change it materially.
   About once every 10 experiments, try something radical.
4. Make that ONE focused change, in `research.py` only. Never edit any other
   file (your tools only allow editing `research.py`; `.env` is off limits).
   You cannot change how the metric is computed (`prepare.py`,
   `parameters.json`), and you never add data sources (`sources.json` is
   human-only). If the best idea needs a new source, pick another idea and put
   `NEEDS-SOURCE: <what and why>` in the commit body. Never scrape CoStar.
5. Run exactly `python -m unittest discover -s tests`. It must pass. Fix your change if
   it fails (at most 3 attempts). If it still fails, run
   `git restore research.py` and stop without committing.
6. Commit exactly once, as two commands: `git add research.py`, then
   `git commit -m "exp: <what you changed, short, no tabs>" -m "<your hypothesis>"`.
   Do not amend.
7. Stop. Do NOT run the experiment (`make loop`, `cli.py loop`, baseline), and
   do not push, reset, check out or switch branches. The wrapper does those.

End with one line: the hypothesis you are testing.
