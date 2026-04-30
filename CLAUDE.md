# CLAUDE.md

> **STOP.** Before doing anything else, read `START_HERE.md` and walk through the 6-step orientation chain it describes.
>
> This is not optional. This repo implements the Karpathy AutoResearch pattern, which depends on strict file mutability rules and a setup phase. Skipping orientation silently corrupts the system.
>
> START_HERE.md walks you through:
>
> 1. Reading AUTORESEARCH_MECHANICS.md (the canonical spec)
> 2. Reading program.md and the appendix
> 3. Reading the infrastructure docs
> 4. Inventorying the current repo state
> 5. Confirming what the human wants from this session — STOP and wait for confirmation
> 6. Executing the confirmed session type with the appropriate workflow
>
> Each step has explicit "confirm by stating" requirements that produce evidence the prior step was completed.
>
> Do not write code. Do not modify files. Do not act. Read START_HERE.md first.

---

## Developer setup (one-time per clone)

Run once after cloning the repo:

```bash
git config core.hooksPath .githooks
```

This activates the pre-commit hook at `.githooks/pre-commit` that blocks
commits which would put real credentials into `env.template`. The
server-side equivalent runs as a GitHub Actions workflow at
`.github/workflows/check-env-template.yml` and catches commits made via the
GitHub web UI or by clones without the local hook configured. See
`reviews/02_setup_phase/00_setup_status.md` "Incident" section for the
backstory.

Real credentials only ever go in `.env` (gitignored). The CI workflow at
`.github/workflows/validate-phase1.yml` re-runs `python prepare.py` against
the live Supabase project on every push that touches `prepare.py`,
`parameters.json`, `requirements.txt`, or `STORAGE_ARCHITECTURE.md`. It
needs a `DATABASE_URL` repository secret — set it once in GitHub Settings
-> Secrets and variables -> Actions.
