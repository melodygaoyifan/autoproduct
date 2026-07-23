"""Pure-Python deterministic probes — always available, no binaries.

- secret_scan: credential material in added lines (P6)
- csrf_ssrf_probe (§09.7.3.6): the two 100%-failure-rate categories for
  AI-generated code — state-changing endpoints without visible CSRF
  protection, outbound HTTP with non-literal URLs
- slopsquat_check (§09.7.3.5): dependencies added by the diff that are
  missing from the registry, freshly registered, or a keystroke away from a
  popular package — the AI-hallucinated-package attack CVE checks can't see
"""

from __future__ import annotations

import datetime
import re
from typing import Callable

from autoproduct.diff import FileDiff, ParsedDiff
from autoproduct.tools.base import ToolReport, tool_finding

# --- secret_scan -----------------------------------------------------------

_SECRET_PATTERNS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key ID"),
    (re.compile(r"-----BEGIN (RSA|EC|OPENSSH|PGP|DSA)? ?PRIVATE KEY"), "Private key material"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"), "Provider API key (sk- prefix)"),
    (
        re.compile(r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*['\"][^'\"\s]{16,}['\"]"),
        "Hardcoded credential assignment",
    ),
]


# Documentation placeholders (e.g. AWS's official example key). Real
# credentials never carry these values; flagging them buries real findings
# in fixture noise (found by the self-review of PR #6).
_KNOWN_EXAMPLE_CREDENTIALS = {
    "AKIAIOSFODNN7EXAMPLE",
    "AKIAI44QH8DHBEXAMPLE",
    "wJalrXUtnFXKEXAMPLEKEY",
}

# For the credential-ASSIGNMENT pattern only (never for AWS/sk-/private-key
# matches): values that declare themselves fake are fixtures, not leaks
# (PR #16 self-review flagged tests' "test-webhook-secret").
_PLACEHOLDER_VALUE = re.compile(
    r"(?i)['\"][^'\"]*(test|example|dummy|placeholder|changeme|fake)[^'\"]*['\"]"
)


def secret_scan(diff: ParsedDiff, repo_dir: str) -> ToolReport:
    findings = []
    for file in diff.files:
        for lineno, text in file.added:
            if any(example in text for example in _KNOWN_EXAMPLE_CREDENTIALS):
                continue
            for pattern, label in _SECRET_PATTERNS:
                if pattern.search(text):
                    if label == "Hardcoded credential assignment" and _PLACEHOLDER_VALUE.search(text):
                        break
                    findings.append(
                        tool_finding(
                            "secret_scan",
                            title=f"{label} committed in diff",
                            severity="critical",
                            file_path=file.path,
                            line=lineno,
                            evidence=text.strip()[:200],
                            explanation=f"{label} matched in an added line; secrets in "
                            "version control must be revoked and moved to a secret store.",
                            taxonomy_hint="P6",
                        )
                    )
                    break
    return ToolReport(tool="secret_scan", status="ok", findings=findings)


# --- csrf_ssrf_probe ---------------------------------------------------------

_STATE_ENDPOINT = re.compile(
    r"@\w+\.(post|put|delete|patch)\(|methods\s*=\s*\[[^\]]*(POST|PUT|DELETE|PATCH)"
)
_CSRF_HINT = re.compile(r"(?i)csrf")
_OUTBOUND_NON_LITERAL = re.compile(
    r"\b(requests|httpx)\.(get|post|put|delete|patch|request)\(\s*(?!['\"f])(\w+)"
    r"|\burlopen\(\s*(?!['\"])(\w+)"
)


_CODE_SUFFIXES = (".py", ".js", ".ts", ".jsx", ".tsx")


def csrf_ssrf_probe(diff: ParsedDiff, repo_dir: str) -> ToolReport:
    findings = []
    for file in diff.files:
        if not file.path.endswith(_CODE_SUFFIXES):
            # Data/fixture files (.yaml, .md, .json) quote code without
            # executing it (found by the self-review of PR #6).
            continue
        added_text = "\n".join(text for _, text in file.added)
        file_has_csrf = bool(_CSRF_HINT.search(added_text))
        for lineno, text in file.added:
            if _STATE_ENDPOINT.search(text) and not file_has_csrf:
                findings.append(
                    tool_finding(
                        "csrf_ssrf_probe",
                        title="State-changing endpoint without visible CSRF protection",
                        severity="high",
                        file_path=file.path,
                        line=lineno,
                        evidence=text.strip()[:200],
                        explanation="The diff adds a state-changing route and no CSRF "
                        "middleware/decorator appears in the added code. Verify framework-"
                        "level CSRF coverage exists.",
                        confidence="likely",
                        taxonomy_hint="P6",
                    )
                )
            match = _OUTBOUND_NON_LITERAL.search(text)
            if match:
                findings.append(
                    tool_finding(
                        "csrf_ssrf_probe",
                        title="Outbound HTTP request with non-literal URL (SSRF surface)",
                        severity="high",
                        file_path=file.path,
                        line=lineno,
                        evidence=text.strip()[:200],
                        explanation="The URL argument is a variable; if it can carry "
                        "user-supplied input, this is an SSRF vector. An allowlist check "
                        "must gate it.",
                        confidence="likely",
                        taxonomy_hint="P6",
                    )
                )
    return ToolReport(tool="csrf_ssrf_probe", status="ok", findings=findings)


# --- slopsquat_check ---------------------------------------------------------

_DEP_FILES = re.compile(r"(^|/)(requirements[^/]*\.txt|pyproject\.toml)$")
_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._\-]*)")
_PYPROJECT_DEP = re.compile(r"^\s*\"([A-Za-z0-9][A-Za-z0-9._\-]*)")

POPULAR_PACKAGES = [
    "requests", "numpy", "pandas", "flask", "django", "pytest", "httpx",
    "pydantic", "sqlalchemy", "celery", "click", "typer", "fastapi",
    "boto3", "botocore", "urllib3", "certifi", "cryptography", "pillow",
    "scipy", "matplotlib", "openai", "anthropic", "langchain", "redis",
    "psycopg2", "pyyaml", "jinja2", "setuptools", "aiohttp",
]

RegistryFetcher = Callable[[str], dict | None]


def _pypi_fetcher(name: str) -> dict | None:
    import httpx

    response = httpx.get(f"https://pypi.org/pypi/{name}/json", timeout=15)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    data = response.json()
    uploads = [
        f["upload_time_iso_8601"]
        for files in data.get("releases", {}).values()
        for f in files
    ]
    first_upload_days = None
    if uploads:
        first = min(datetime.datetime.fromisoformat(u) for u in uploads)
        first_upload_days = (datetime.datetime.now(datetime.UTC) - first).days
    return {"first_upload_days": first_upload_days}


def _edit_distance_le_1(a: str, b: str) -> bool:
    """Damerau-style: one insert/delete/substitute OR one adjacent
    transposition (reqeusts→requests, the classic typosquat)."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diffs = [i for i in range(len(a)) if a[i] != b[i]]
        if len(diffs) == 2:
            i, j = diffs
            return j == i + 1 and a[i] == b[j] and a[j] == b[i]
    if len(a) > len(b):
        a, b = b, a
    i = j = edits = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
            j += 1
        else:
            edits += 1
            if edits > 1:
                return False
            if len(a) == len(b):
                i += 1
            j += 1
    return True


def added_dependencies(diff: ParsedDiff) -> list[tuple[FileDiff, int, str]]:
    deps = []
    for file in diff.files:
        if not _DEP_FILES.search(file.path):
            continue
        pattern = _PYPROJECT_DEP if file.path.endswith("pyproject.toml") else _REQ_LINE
        for lineno, text in file.added:
            match = pattern.match(text)
            if match and not text.lstrip().startswith("#"):
                deps.append((file, lineno, match.group(1).lower()))
    return deps


def slopsquat_check(
    diff: ParsedDiff, repo_dir: str, fetcher: RegistryFetcher = _pypi_fetcher
) -> ToolReport:
    deps = added_dependencies(diff)
    if not deps:
        return ToolReport(tool="slopsquat_check", status="ok", detail="no added dependencies")

    findings = []
    errors = []
    for file, lineno, name in deps:
        squatted = next(
            (p for p in POPULAR_PACKAGES if p != name and _edit_distance_le_1(name, p)),
            None,
        )
        if squatted:
            findings.append(
                tool_finding(
                    "slopsquat_check",
                    title=f"Dependency '{name}' is one keystroke from '{squatted}' (typosquat)",
                    severity="critical",
                    file_path=file.path,
                    line=lineno,
                    evidence=name,
                    explanation=f"'{name}' differs from the popular package '{squatted}' "
                    "by one edit — the classic slopsquat/typosquat attack shape.",
                    taxonomy_hint="P6",
                )
            )
            continue
        try:
            meta = fetcher(name)
        except Exception as exc:  # noqa: BLE001 — registry unreachable ≠ package malicious
            errors.append(f"{name}: {type(exc).__name__}")
            continue
        if meta is None:
            findings.append(
                tool_finding(
                    "slopsquat_check",
                    title=f"Dependency '{name}' does not exist on PyPI",
                    severity="critical",
                    file_path=file.path,
                    line=lineno,
                    evidence=name,
                    explanation="The added dependency is absent from the registry — either "
                    "a hallucinated package name (installable by an attacker who registers "
                    "it) or a typo. Do not merge until resolved.",
                    taxonomy_hint="P6",
                )
            )
        elif (meta.get("first_upload_days") or 10_000) < 30:
            findings.append(
                tool_finding(
                    "slopsquat_check",
                    title=f"Dependency '{name}' was first published <30 days ago",
                    severity="high",
                    file_path=file.path,
                    line=lineno,
                    evidence=name,
                    explanation="Freshly registered packages are the delivery vehicle for "
                    "slopsquatting; verify provenance before merging.",
                    confidence="likely",
                    taxonomy_hint="P6",
                )
            )
    status = "error" if errors and not findings else "ok"
    return ToolReport(
        tool="slopsquat_check",
        status=status,
        detail="; ".join(errors),
        findings=findings,
    )
