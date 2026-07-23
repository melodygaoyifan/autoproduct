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

Pipeline: **Gate 1 DoR → init → analyze (mode router) → vote → leader → post**

- Deterministic mode router (§08.3.5.1) — conservative, escalates on
  auth/billing paths, new dependencies, safety-removal signatures.
- Spec-driven voter loading (doc 11): skills are markdown + YAML frontmatter,
  validated by `SpecValidator`; invalid specs refuse to load; voter tool risk
  ceiling ≤ L2 enforced at the schema level.
- Correctness voter (DAPLab P2/P3/P4/P9 slice) with `<untrusted_*>` prompt
  hygiene and BLOCKED_* statuses instead of silent empties.
- Deterministic Leader: evidence filter, dedupe, 8-verdict taxonomy
  (§09.4.4.7) with escalation triggers.
- YAML mirror audit trail per node under `.mas/reviews/<id>/`.
- Hermetic test suite (mock provider, no network): `uv run pytest`.

## What's next (per doc 10)

1. Day-0 calibration with a real `ANTHROPIC_API_KEY` (real PR, real voter).
2. Remaining five voters + heterogeneous providers (OpenAI, Google, xAI).
3. Fresh-agent verification pass (§09.4.6) and confidence scoring (§09.4.7).
4. Deterministic tools node: Semgrep, Bandit, TruffleHog, pip-audit,
   slopsquat_check, csrf_ssrf_probe.
5. HITL via GitHub Issues; mutation testing in isolated worktrees; the
   compounding loop.

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
