# autoproduct (implementation)

Implementation of the design documented in the parent directory
(`../08-foundation.md` through `../17-domain-profiles.md`). Currently at the
**walking-skeleton milestone** (doc 10, Weeks 1–2 condensed): one voter
end-to-end through the full state machine.

## What works today

```
autoproduct review <target> [--provider mock] [--mode fast|standard|deep]
```

`<target>` is a GitHub PR URL (needs `gh` auth) or a local git range
(`main...HEAD`, `HEAD`, …).

Pipeline: **Gate 1 DoR → init → analyze (mode router) → tools → vote
(parallel ×6) → verify → leader → post**

- Deterministic mode router (§08.3.5.1) — conservative, escalates on
  auth/billing paths, new dependencies, safety-removal signatures. Fast mode
  runs the single cheap (Haiku) reviewer only.
- Spec-driven voter loading (doc 11): skills are markdown + YAML frontmatter,
  validated by `SpecValidator`; invalid specs refuse to load; voter tool risk
  ceiling ≤ L2 enforced at the schema level.
- Full six-voter roster (Correctness, Security, Performance, Context,
  Repo Graph, Style), each mapped to its DAPLab taxonomy slice, with
  `<untrusted_*>` prompt hygiene and BLOCKED_* statuses instead of silent
  empties. Heterogeneous providers (Anthropic, OpenAI, Google, xAI); when a
  provider's key is absent, the spec's declared fallback runs and the
  substitution is recorded in the output envelope — never silent.
- Deterministic tools node (§09.7.3): three always-on pure-Python probes —
  secret_scan, csrf_ssrf_probe (the two 100%-failure-rate AI-code
  categories), slopsquat_check (live PyPI presence/age + Damerau typosquat
  distance against popular packages) — plus availability-gated Semgrep,
  Bandit, pip-audit, and TruffleHog wrappers (absent binaries report
  `skipped`, never silently missing). Tool findings enter pre-verified,
  feed voter context, and corroborate voter findings in scoring.
- Fresh-agent verification (§09.4.6): every finding re-examined by an
  isolated verifier prompted to refute it; NOT_REPRODUCIBLE findings score 0.
- Composite confidence scoring (§09.4.7): self-confidence (40) +
  verification (40) + cross-voter corroboration (20), threshold-gated
  reporting (80 default / 60 for critical+high).
- Two-half Leader: deterministic score filter / exact dedupe / verdict
  selection (§09.4.4.7, escalation triggers exercised live), then LLM
  semantic merge — paraphrased same-defect findings from different voters
  cluster into one, corroborators credited, narrative summary written. The
  LLM half degrades to the deterministic result on any failure; it can
  improve the report but never gate the pipeline.
- YAML mirror audit trail per node under `.mas/reviews/<id>/`.
- Hermetic test suite (mock provider, no network): `uv run pytest`.

## What's next (per doc 10)

1. Voter tool access (read_file, grep, tree_sitter_query) so repo_graph can
   trace real cross-file references instead of returning BLOCKED.
2. PR comment posting; HITL via GitHub Issues; mutation testing in isolated
   worktrees; the compounding loop.

## Layout

```
skills/            voter skills (markdown + machine-checked frontmatter)
src/autoproduct/
  state.py         VoterFinding / VoterOutput / ReviewState / verdicts
  diff.py          unified-diff parsing + acquisition (git / gh)
  harness/         SpecValidator (ADR-008/009)
  voters/          uniform Voter class — voters differ only by skill file
  providers/       anthropic (real), mock (deterministic, for tests)
  leader.py        deterministic synthesis + verdict selection
  orchestrator/    LangGraph state machine + mode router
  mirror.py        YAML audit trail
  cli.py           `autoproduct review`
tests/             includes hermetic end-to-end run on a planted-bug diff
```
