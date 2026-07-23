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
autoproduct create myshop --profile miniprogram    # 1. writes FDR.md template + guide
# ← fill in FDR.md in your own words (Chinese or English)
autoproduct create myshop --profile miniprogram    # 2. asks questions OR confirms the plan
autoproduct create myshop --profile miniprogram --yes   # 3. builds everything
```

- **If your FDR is unclear, the system asks — it never guesses.** You get
  at most 5 specific questions a non-technical person can answer
  (`FDR-QUESTIONS.md`).
- **Before building, you confirm intent in plain language** — 会做什么 /
  这次不做 / 怎么算成功 (`product/CONFIRMATION.md`).
- **After building, you get `product/BUILD-REPORT.md`** in your language:
  what exists, what the automated reviewers flagged, what to do next.
- Profiles carry domain rules automatically: the 小程序 profile enforces
  the 2MB package budget, request-domain whitelisting, lazy permission
  requests with privacy declarations, and WeChat review-guideline
  boundaries; `web` enforces CSRF/SSRF discipline, a11y, and E2E flows;
  `app` enforces store-review and offline-behavior rules.

## What happens under the hood

Eight-stage multi-agent pipeline (design docs in the parent directory):

**Upstream (build):** Discovery (evidence-tagged hypotheses — fabricating
user evidence is a schema violation) → Planning (task DAG, cycle-checked) →
Spec (EARS acceptance criteria, machine-linted, every criterion covered by
a test skeleton) → Coding (single-writer, test-first, sandboxed suite must
pass before any commit).

**Downstream (judge):** Code Review (6 heterogeneous voters with
investigation tools, deterministic security probes, every finding
independently verified) → Test Gate (isolated worktree, mutation testing in
deep mode) → Deploy Review → Maintenance (incident triage → root cause →
fix-PRs with regression tests that must fail pre-fix).

Two learning loops compound over time, both human-gated: review signals
become CLAUDE.md constraints; recurring incidents become investigator
skills.

**Gate philosophy:** humans keep the judgments they're best at (is this my
intent? — asked in their language); machines keep the ones non-technical
users can't make (EARS validity, DAG soundness, tests) — and every
auto-approval is recorded in the build report, never silent. Nothing
auto-merges to main; nothing deploys to production autonomously; generated
fix-PRs re-enter review like any human PR.

## For developers

Every stage is also a standalone command:

| | |
|---|---|
| `autoproduct discover / plan / spec / build` | run upstream stages individually (gates U1–U4) |
| `autoproduct review <PR-URL \| git-range>` | multi-voter code review + test gate |
| `autoproduct deploy-review` · `triage [--fix]` | Gate 5 / Gate 6 stages |
| `autoproduct serve` | webhook mode: GitHub PRs review themselves; incidents POST in |
| `autoproduct bench` · `compound --pr` · `replay` | benchmark, compounding loop, audit trail |

Measured baseline: **recall 100%, precision 67%** on the labeled benchmark
(bars: 40%/50%); ~150 hermetic tests (`uv run pytest`). Operations guide in
[RUNBOOK.md](RUNBOOK.md).

Setup: `uv sync`, an `ANTHROPIC_API_KEY` (other provider keys optional —
voter specs declare per-family models with visible fallback), `gh` auth for
GitHub actions, Docker optional (enables the network-isolated test
sandbox).

## Honest limits (today)

- **No graphical UI yet** — the founder flow is CLI + generated markdown
  documents; the server exposes JSON endpoints a UI could sit on.
- **No external MCP / service provisioning yet** — generated products can
  call APIs in their code, but autoproduct does not yet provision
  databases, cloud services, or credentials, and the MCP tool transport
  from design doc 11 is not yet exposed externally.
- 小程序/JS builds pass on review alone (no JS test runner yet); Python
  builds get the full test gate.
- One machine, one workspace at a time; multi-instance supervision is a
  documented upgrade path.

MIT · design docs: [autoproduct-design](https://github.com/melodygaoyifan/autoproduct-design) · every PR in this
repo was reviewed by autoproduct itself.
