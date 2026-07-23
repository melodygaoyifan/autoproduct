from autoproduct.deploy.probes import canary_scan
from autoproduct.diff import parse_unified_diff


def _diff(path, added=(), removed=()):
    body = "\n".join(f"-{r}" for r in removed) + "\n" + "\n".join(f"+{a}" for a in added)
    return (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
        f"@@ -1,{len(removed)} +1,{len(added)} @@\n{body.strip()}\n"
    )


def test_removed_analysis_flagged():
    diff = parse_unified_diff(
        _diff(
            "k8s/rollout.yaml",
            added=["kind: Rollout", "spec: {}"],
            removed=["kind: Rollout", "analysis:", "  templates: [error-rate]"],
        )
    )
    report = canary_scan(diff, ".")
    assert any("analysis removed" in f.title for f in report.findings)


def test_raised_first_step_flagged():
    diff = parse_unified_diff(
        _diff(
            "k8s/rollout.yaml",
            added=["kind: Rollout", "- setWeight: 50"],
            removed=["kind: Rollout", "- setWeight: 5"],
        )
    )
    report = canary_scan(diff, ".")
    assert any("traffic raised 5% → 50%" in f.title for f in report.findings)


def test_loosened_threshold_flagged():
    diff = parse_unified_diff(
        _diff(
            "flagger/canary.yaml",
            added=["kind: Canary", "threshold: 20"],
            removed=["kind: Canary", "threshold: 3"],
        )
    )
    report = canary_scan(diff, ".")
    assert any("loosened 3 → 20" in f.title for f in report.findings)


def test_non_canary_yaml_ignored():
    diff = parse_unified_diff(
        _diff("config/app.yaml", added=["threshold: 20"], removed=["threshold: 3"])
    )
    assert canary_scan(diff, ".").findings == []


def test_unchanged_canary_clean():
    diff = parse_unified_diff(
        _diff(
            "k8s/rollout.yaml",
            added=["kind: Rollout", "- setWeight: 5", "- pause: {duration: 10m}"],
        )
    )
    assert canary_scan(diff, ".").findings == []
