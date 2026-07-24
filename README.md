# autoproduct

**Write one document. Get a working product.**

autoproduct builds apps, web services, and 微信小程序 from a single
requirements document (the **FDR**) written by someone with **no coding or
product experience** — in their own words, in their own language. The
system coaches you until the FDR is buildable, confirms the plan back in
plain language, then designs, implements, tests, and reviews the product
through a multi-agent pipeline with every automated decision on the record.

## For founders (no technical background needed)

```bash
autoproduct studio myshop --profile miniprogram    # browser UI: the whole flow
```

or the same flow in the terminal:

```bash
autoproduct create myshop --profile miniprogram    # 1. writes FDR.md template + guide
# ← fill in FDR.md in your own words (Chinese or English)
autoproduct create myshop --profile miniprogram    # 2. asks questions OR confirms the plan
autoproduct create myshop --profile miniprogram --yes   # 3. builds everything
autoproduct preview                                # 4. try your product
autoproduct add feature.md --yes                   # 5. one small FDR per new feature
autoproduct ship                                   # 6. deploy artifacts + plain-language guide
```

- **If your FDR is unclear, the system asks — it never guesses** (at most
  5 questions a non-technical person can answer, in your language).
- **One FDR = one thing.** The first FDR is the smallest usable product;
  every later feature is its own small FDR via `add`. Granular builds are
  more accurate and fail more debuggably.
- **You confirm intent in plain language** before anything is built, and
  get a build report in your language after — including every automated
  approval the machine made on your behalf.
- **Real persistence out of the box**: a local SQLite database is
  provisioned automatically; cloud services (Supabase, 微信云开发) are a
  guided option with credentials in a vault that never enters prompts.
- Profiles carry domain rules: 小程序 (2MB package budget, domain
  whitelist, lazy 授权 with 隐私协议, WeChat review boundaries), web
  (CSRF/SSRF, a11y, E2E flows), app (store rules, offline behavior).

## What happens under the hood

Eight-stage multi-agent pipeline (design docs:
[autoproduct-design](https://github.com/melodygaoyifan/autoproduct-design)):

**Upstream:** Discovery (evidence-tagged hypotheses — fabricating user
evidence is a schema violation) → Planning (task DAG with cycle/lane/budget
checks, calibrated estimates) → Spec (EARS criteria machine-linted, every
criterion covered by a test skeleton, frozen behind SCRs once built) →
Coding (single-writer, test-first, existing tests read-only with
AST-checked no-weakening, sandboxed suite must pass, optional parallel
lane worktrees).

**Downstream:** Code Review (6 heterogeneous voters with investigation
tools incl. a tree-sitter symbol index, deterministic probes for secrets/
CSRF-SSRF/slopsquatting/frontend↔backend wireup drift, every finding
independently verified) → Test Gate (isolated worktree, python + JS
runners, mutation testing in deep mode) → Deploy Review → Maintenance
(incident triage → root cause → fix-PRs whose regression tests must fail
pre-fix; human-approved learned skills).

Serious review findings trigger a bounded repair iteration. Crashed runs
resume from checkpoints (`autoproduct recover`). Two human-gated learning
loops compound: review signals → CLAUDE.md constraints; recurring
incidents → investigator skills.

**Gate philosophy:** humans keep the judgments they're best at (is this my
intent?); machines keep the ones non-technical users can't make (EARS
validity, DAG soundness, tests) — every auto-approval is recorded. Nothing
auto-merges, nothing deploys to production autonomously.

## Measured

- **Review benchmark** (`autoproduct bench`): recall 100%, precision 67%
  on 13 labeled cases (bars: 40%/50%).
- **Product benchmark** (`autoproduct product-bench`): full FDR→product
  runs scored by *independent* behavioral probes executed against the
  built product ([WebGen-Bench](https://arxiv.org/abs/2505.03733)
  pattern) — build rate, probe pass rate, and clean-review rate reported
  unaveraged, with an honesty case proving probes can fail.
- ~190 hermetic tests (`uv run pytest`); every PR in this repo was
  reviewed by autoproduct itself, and five of those reviews caught real
  bugs in the features they were reviewing.

## For developers

| | |
|---|---|
| `discover / plan / spec / build` (+ `*-approve`) | upstream stages individually, gates U1–U4 |
| `scr` / `scr-approve` | the only legal way to change a built spec |
| `review` · `resume` · `recover` · `replay` | review pipeline, HITL, crash recovery, audit trail |
| `deploy-review` · `deploy-outcome` · `triage [--fix]` | Gates 5–6 |
| `serve` | webhook mode: PRs review themselves; incidents POST in |
| `worker` | queue worker — set `AUTOPRODUCT_QUEUE_DB` on `serve` and run N workers to drain bursts in parallel (SQLite, one host; multi-host needs a shared broker) |
| `bench` · `product-bench` · `compound --pr` | the two benchmarks + the compounding loop |

Setup: `uv sync`, `ANTHROPIC_API_KEY` (other provider keys optional —
voter specs declare per-family models with visible fallback), `gh` auth,
Docker optional (network-isolated test sandbox), Node optional (JS test
gate). Operations guide: [RUNBOOK.md](RUNBOOK.md).

## Honest limits (today)

- Screenshots, the in-Studio correction loop, generated 验收清单,
  built-in telemetry, and a pre-built 微信支付/登录 blocks catalog are the
  active roadmap (M2–M7) — the product works, but you validate it by
  using it, not yet by looking at it.
- Cloud services are guided, not auto-provisioned; deploys generate
  artifacts + instructions, the button stays yours.
- 小程序 page-level testing needs `miniprogram-simulate` installed;
  pure-logic modules are gated via `node --test` today.
- Single-machine operation; crash recovery is per-review, Celery/Redis
  multi-instance supervision is the documented upgrade path.

MIT · design docs: [autoproduct-design](https://github.com/melodygaoyifan/autoproduct-design)
