from autoproduct.diff import parse_unified_diff
from autoproduct.tools import external, probes


def _diff(path: str, *added: str) -> str:
    body = "\n".join(f"+{line}" for line in added)
    return (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
        f"@@ -1,0 +1,{len(added)} @@\n{body}\n"
    )


def test_secret_scan_catches_aws_key():
    diff = parse_unified_diff(_diff("config.py", 'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"'))
    report = probes.secret_scan(diff, ".")
    assert len(report.findings) == 1
    assert report.findings[0].severity.value == "critical"
    assert report.findings[0].verification == "VERIFIED"


def test_secret_scan_clean_diff():
    diff = parse_unified_diff(_diff("config.py", "DEBUG = False"))
    assert probes.secret_scan(diff, ".").findings == []


def test_csrf_probe_flags_unprotected_endpoint():
    diff = parse_unified_diff(
        _diff("views.py", '@app.post("/orders/cancel")', "def cancel(): ...")
    )
    report = probes.csrf_ssrf_probe(diff, ".")
    assert any("CSRF" in f.title for f in report.findings)


def test_csrf_probe_quiet_when_protection_visible():
    diff = parse_unified_diff(
        _diff("views.py", '@app.post("/x")', "@csrf_protect", "def x(): ...")
    )
    assert probes.csrf_ssrf_probe(diff, ".").findings == []


def test_ssrf_probe_flags_variable_url():
    diff = parse_unified_diff(_diff("client.py", "resp = requests.get(user_url)"))
    report = probes.csrf_ssrf_probe(diff, ".")
    assert any("SSRF" in f.title for f in report.findings)


def test_ssrf_probe_allows_literal_url():
    diff = parse_unified_diff(
        _diff("client.py", 'resp = requests.get("https://api.example.com/v1")')
    )
    assert probes.csrf_ssrf_probe(diff, ".").findings == []


def _dep_diff(*lines: str) -> str:
    return _diff("requirements.txt", *lines)


def test_slopsquat_nonexistent_package():
    diff = parse_unified_diff(_dep_diff("definitely-hallucinated-pkg==1.0"))
    report = probes.slopsquat_check(diff, ".", fetcher=lambda name: None)
    assert len(report.findings) == 1
    assert "does not exist" in report.findings[0].title


def test_slopsquat_typosquat_detected_without_registry():
    calls = []
    diff = parse_unified_diff(_dep_diff("reqeusts==2.0"))
    report = probes.slopsquat_check(
        diff, ".", fetcher=lambda name: calls.append(name)
    )
    assert "typosquat" in report.findings[0].title
    assert calls == []  # typosquat verdict needs no network


def test_slopsquat_young_package_flagged():
    diff = parse_unified_diff(_dep_diff("brand-new-pkg==0.1"))
    report = probes.slopsquat_check(
        diff, ".", fetcher=lambda name: {"first_upload_days": 5}
    )
    assert "<30 days" in report.findings[0].title


def test_slopsquat_established_package_clean():
    diff = parse_unified_diff(_dep_diff("requests>=2.31"))
    report = probes.slopsquat_check(
        diff, ".", fetcher=lambda name: {"first_upload_days": 4000}
    )
    assert report.findings == []


def test_external_tools_skip_when_missing(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    diff = parse_unified_diff(_diff("a.py", "x = 1"))
    for runner in (external.semgrep, external.bandit, external.pip_audit, external.trufflehog):
        report = runner(diff, ".")
        assert report.status == "skipped"
        assert "not installed" in report.detail
