"""Auto-generated acceptance probes — from the USER'S PRD, not fixtures.

The hand-written probes in benchmarks/ are labeled regression fixtures
(deterministic scoring across runs). Real products can't have hand-setup:
this module generates behavioral probes from what the founder asked for
(FDR + built criteria) against what was actually built (the OBSERVED
route table) — the WebGen-Bench pattern, executor included.

Guardrails: the LLM writes only the call/assert body; the boot frame,
timeout, and teardown are templated; every body must parse (ast) or it is
dropped visibly.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import yaml
from pydantic import BaseModel

from autoproduct.providers import get_provider
from autoproduct.yamlx import extract_mapping

PROBEGEN_MARKER = "acceptance probe generator for built products"

BOOT_FRAME = '''import json, os, socket, subprocess, sys, time, urllib.request, urllib.error

PORT = 8646
BASE = f"http://127.0.0.1:{PORT}"
entry = next((e for e in ("app/main.py", "main.py", "app.py") if os.path.exists(e)), None)
assert entry, "no runnable entry point"
proc = subprocess.Popen([sys.executable, entry],
                        env={**os.environ, "PORT": str(PORT)},
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def wait():
    for _ in range(60):
        try:
            socket.create_connection(("127.0.0.1", PORT), 1).close()
            return
        except OSError:
            time.sleep(0.5)
    raise SystemExit("server never listened")


def call(method, path, body=None, expect_redirect=False):
    req = urllib.request.Request(BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    opener = (urllib.request.build_opener(NoRedirect())
              if expect_redirect else urllib.request.build_opener())
    try:
        resp = opener.open(req, timeout=10)
        raw = resp.read().decode() or "{}"
        try:
            data = json.loads(raw)
        except Exception:
            data = {"_raw": raw}
        return resp.status, data, dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, {}, dict(e.headers)


wait()
try:
{body}
finally:
    proc.terminate()
'''

_SYSTEM = f"""You are the {PROBEGEN_MARKER}. Write behavioral probes for
the product described below, using ONLY the observed routes.

Each probe body is python that runs inside a frame where these exist:
- call(method, path, body=None, expect_redirect=False) -> (status, data, headers)
- BASE, PORT (server already booted; do NOT boot or terminate anything)

Rules:
- Probe REAL user behaviors from the criteria (create → read back, math
  adds up, invalid input rejected). 2–5 probes, each independent.
- Only paths matching the observed routes. assert with messages.
- No imports, no file access, no sleeps.

Respond with ONLY YAML:
probes:
  - name: kebab-name
    body: |
      s, d, _ = call("POST", "/api/things", {{"name": "x"}})
      assert s in (200, 201), f"create returned {{s}}"
"""


class GeneratedProbe(BaseModel):
    name: str
    script: str


def generate_probes(
    workspace: str | Path,
    *,
    provider: str = "anthropic",
    model: str = "claude-opus-4-8",
    max_probes: int = 5,
) -> tuple[list[GeneratedProbe], list[str]]:
    """Returns (probes, notes). Invalid bodies are dropped VISIBLY via notes."""
    root = Path(workspace).resolve()
    fdr = (root / "FDR.md").read_text(encoding="utf-8") if (root / "FDR.md").exists() else ""
    from autoproduct.tools.wireup import collect_routes
    from autoproduct.upstream.walkthrough import built_criteria

    routes = sorted("/" + "/".join(r) for r in collect_routes(root))
    criteria = [c for _, c in built_criteria(root)]
    if not routes:
        return [], ["no observed backend routes — nothing to probe over HTTP"]

    raw = get_provider(provider).complete(
        model=model,
        system=_SYSTEM,
        user=yaml.safe_dump(
            {"fdr": fdr[:1500], "criteria": criteria[:20], "observed_routes": routes},
            sort_keys=False, allow_unicode=True,
        ),
        max_tokens=4096,
    )
    try:
        data = extract_mapping(raw, ("probes",))
    except ValueError as exc:
        return [], [f"probe generation unparseable: {exc}"]

    probes, notes = [], []
    for item in (data.get("probes") or [])[:max_probes]:
        name = str(item.get("name", "probe"))[:48]
        body = str(item.get("body", ""))
        try:
            ast.parse(body)
        except SyntaxError as exc:
            notes.append(f"dropped probe {name!r}: body does not parse ({exc.msg})")
            continue
        script = BOOT_FRAME.replace("{body}", textwrap.indent(body.rstrip(), "    "))
        probes.append(GeneratedProbe(name=name, script=script))
    if not probes:
        notes.append("no valid probes generated")
    return probes, notes


def verify_product(
    workspace: str | Path,
    *,
    provider: str = "anthropic",
    model: str = "claude-opus-4-8",
) -> Path:
    """Post-build acceptance verification for ANY user product: probes are
    generated from the FDR and executed against the booted product;
    results land in product/VERIFICATION.md in checklist form."""
    root = Path(workspace).resolve()
    from autoproduct.product_bench import Probe, run_probe

    probes, notes = generate_probes(root, provider=provider, model=model)
    lines = ["# 自动验证 / Automated verification", ""]
    passed = 0
    for generated in probes:
        result = run_probe(root, Probe(name=generated.name, script=generated.script))
        mark = "✅" if result.passed else "❌"
        passed += result.passed
        lines.append(f"- {mark} {generated.name}" + (f" — {result.detail}" if result.detail else ""))
    for note in notes:
        lines.append(f"- ⚠️ {note}")
    if probes:
        lines.insert(1, f"\n{passed}/{len(probes)} 项行为验证通过 / behaviors verified.\n")
    path = root / "product" / "VERIFICATION.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
