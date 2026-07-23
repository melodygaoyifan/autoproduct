# autoproduct — project constraints

Hard constraints for anyone (human or agent) changing this codebase. The
Context voter enforces these as findings; the compounding loop appends its
learned section below.

## Architecture invariants

- Deterministic control flow, probabilistic analysis: LLMs never decide
  which node runs next, when to escalate, or whether to retry. Those
  decisions live in Python (`orchestrator/graph.py`).
- Agents communicate only through typed envelopes (`state.py`). Never add
  a free-form text channel between two LLM invocations.
- Voter tools are read-only, risk L0–L2, allowlisted in skill frontmatter,
  and budget-enforced at the `ToolBox` boundary. L3/L4 tools (secrets,
  migrations, deploys, auth) must not exist in any voter-reachable registry.
- The system never merges PRs, never pushes to main on its own, and the
  compounding loop only ever proposes CLAUDE.md changes via PR.
- A voter that cannot judge returns a `BLOCKED_*` status — never an empty
  findings list, never a guess. Findings require verbatim `evidence`.

## Engineering rules

- Python 3.12+, `uv` for everything (`uv run pytest`, `uv add`). No new
  runtime dependency without a one-line justification in the PR body.
- All LLM-response parsing goes through `yamlx.extract_mapping` — models
  narrate; never `yaml.safe_load` a raw response.
- Tests are hermetic: no network, no API keys, mock provider only.
  Anything touching a real provider is manual/live, not in `tests/`.
- External tool wrappers must be availability-gated and report `skipped`
  visibly — silent absence of a scanner reads as "scanned and clean".
- Never commit `.mas/` artifacts, checkpoints, or API keys. Secrets stay
  in the environment.
- Subprocess calls: list argv (no `shell=True`), explicit `timeout`,
  `capture_output=True`.

## Known accepted risks

- Gate 2's T3 container sandbox (network-disconnected docker) runs in deep
  mode when a docker daemon is available. Standard mode and docker-less
  hosts fall back to an unsandboxed subprocess worktree — visible in the
  report's `sandbox` field. Only review trusted repos on the fallback path.
