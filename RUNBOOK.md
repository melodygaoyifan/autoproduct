# autoproduct — operations runbook

Day-to-day operation of all four stages. Assumes `uv` and an
`ANTHROPIC_API_KEY` in the environment (other provider keys optional —
voters fall back visibly without them).

## Commands

| Command | What it does |
|---|---|
| `autoproduct review <PR-URL \| git-range>` | Code Review + Test stages (Gates 1–3) |
| `autoproduct resume <review-id> --decision ack\|override:<VERDICT>` | Continue a review paused at Gate 3 |
| `autoproduct deploy-review <target>` | Gate 5 — deploy recommendation (never deploys) |
| `autoproduct deploy-outcome <review-id> --outcome correct\|incorrect` | Record the human verdict; builds the trust-tier track record |
| `autoproduct triage <incident-file> [--fix]` | Gate 6 — triage + root cause; `--fix` approves a fix-PR attempt |
| `autoproduct replay [<review-id>]` | Audit trail of any past review |
| `autoproduct bench` | Regression benchmark (bars: recall ≥40%, precision ≥50%) |
| `autoproduct compound [--pr]` | Weekly signal aggregation → CLAUDE.md proposal |
| `autoproduct serve` | Webhook mode (needs `AUTOPRODUCT_WEBHOOK_SECRET`) |

## Weekly rhythm

1. `autoproduct compound --pr` — review and merge (or close) the proposal.
2. `autoproduct bench` — must PASS; a regression after merging a compound
   PR means Gate 4: revert the CLAUDE.md change.
3. Skim `.mas/voters/*/log.yaml` block rates; a voter blocking repeatedly
   is a prompt/tool problem, not noise.
4. Approve or delete any `status: proposed` files in
   `.mas/learned-skills/`.

## When a review escalates (Gate 3)

A GitHub Issue opens with the findings and a resume command. Decide:
- `--decision ack` — the verdict stands (it will block merge).
- `--decision override:<VERDICT>` — your call is recorded in the summary
  and `final.yaml`; the audit trail keeps both verdicts.

## Deploy trust tiers

Stage starts at `insight` (recommend only). After the configured streak of
correct PROMOTE marks (`promotion_track_record`, default 10), the summary
reports assistive-tier eligibility — graduating is your edit to
`.mas/deploy-policy.yaml`. Production deploys are never autonomous,
regardless of streak.

## Webhook mode

```
export AUTOPRODUCT_WEBHOOK_SECRET=<random>
autoproduct serve --port 8422
```

Point a GitHub webhook (pull_request events, JSON, the same secret) at
`/webhook/github`; POST incidents to `/incidents`. Workers run detached;
`GET /reviews` lists results. Multi-instance operation wants the Celery
supervisor from the design docs — not included yet.

## Safety boundaries (structural, not configurable)

- No auto-merge, no production deploys, no L3/L4 tools for any voter.
- Fix-PRs and compound PRs are proposals; humans merge.
- Deep-mode test runs use the docker T3 sandbox when available; the
  `sandbox` field in every test report says which path ran. Subprocess
  fallback = trusted repos only.

## Key hygiene

Provider keys live in the environment only. If a key may have leaked,
rotate it at the provider console and update `~/.zshrc` (or your secret
store); nothing under `.mas/` or git should ever contain one.
