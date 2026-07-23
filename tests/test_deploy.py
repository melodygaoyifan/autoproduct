from pathlib import Path

import yaml

from autoproduct.deploy import DeployVerdict, detect_deploy_files, run_deploy_review
from autoproduct.deploy.probes import migration_scan, workflow_scan
from autoproduct.diff import parse_unified_diff

SKILLS = str(Path(__file__).parent.parent / "skills" / "deploy")


def _diff(path: str, *added: str) -> str:
    body = "\n".join(f"+{line}" for line in added)
    return (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
        f"@@ -1,0 +1,{len(added)} @@\n{body}\n"
    )


def test_detect_deploy_files():
    files = [
        "src/app.py",
        ".github/workflows/deploy.yml",
        "migrations/0042_drop_legacy.py",
        "terraform/prod/main.tf",
        "README.md",
    ]
    assert detect_deploy_files(files) == files[1:4]


def test_migration_scan_flags_drop():
    diff = parse_unified_diff(
        _diff("migrations/0042_cleanup.sql", "DROP TABLE legacy_orders;")
    )
    report = migration_scan(diff, ".")
    assert report.findings[0].severity.value == "critical"
    assert report.findings[0].taxonomy_hint == "deploy:migration"


def test_migration_scan_ignores_non_migration_paths():
    diff = parse_unified_diff(_diff("docs/history.md", "DROP TABLE legacy_orders;"))
    assert migration_scan(diff, ".").findings == []


def test_workflow_scan_flags_write_all_and_fork_trigger():
    diff = parse_unified_diff(
        _diff(
            ".github/workflows/ci.yml",
            "permissions: write-all",
            "on: pull_request_target",
        )
    )
    titles = [f.title for f in workflow_scan(diff, ".").findings]
    assert any("write-all" in t for t in titles)
    assert any("pull_request_target" in t for t in titles)


def test_deploy_review_escalates_destructive_migration(tmp_path):
    result = run_deploy_review(
        "bench://migration",
        repo_dir=str(tmp_path),
        skills_dir=SKILLS,
        provider_override="mock",
        diff_text=_diff("migrations/0099_drop.sql", "DROP TABLE users_backup;"),
    )
    assert result.verdict is DeployVerdict.ESCALATE_MIGRATION_DESTRUCTIVE
    assert result.tier == "insight"


def test_deploy_review_policy_violation_wins(tmp_path):
    # Policy violation outranks migration escalation in §09.11.6 priority.
    result = run_deploy_review(
        "bench://policy",
        repo_dir=str(tmp_path),
        skills_dir=SKILLS,
        provider_override="mock",
        diff_text=_diff(
            ".github/workflows/x.yml",
            "permissions: write-all",
        )
        + _diff("migrations/0100_drop.sql", "DROP TABLE a;"),
    )
    assert result.verdict is DeployVerdict.ESCALATE_POLICY_VIOLATION
    assert any(f.voter == "tool:deploy_policy" for f in result.findings)


def test_deploy_review_clean_change_promotes(tmp_path):
    result = run_deploy_review(
        "bench://clean",
        repo_dir=str(tmp_path),
        skills_dir=SKILLS,
        provider_override="mock",
        diff_text=_diff("helm/values.yaml", "replicaCount: 3"),
    )
    assert result.verdict is DeployVerdict.PROMOTE
    assert "recommendation only" in result.summary
    mirror = sorted(Path(result.artifacts_dir).glob("[0-9]*-*.yaml"))
    assert [p.name.split("-", 1)[1] for p in mirror] == [
        "probes.yaml", "vote.yaml", "final.yaml",
    ]


def test_custom_policy_forbidden_list(tmp_path):
    policy_dir = tmp_path / ".mas"
    policy_dir.mkdir()
    (policy_dir / "deploy-policy.yaml").write_text(
        yaml.safe_dump({"forbidden": ["image: latest"]})
    )
    result = run_deploy_review(
        "bench://custom",
        repo_dir=str(tmp_path),
        skills_dir=SKILLS,
        provider_override="mock",
        diff_text=_diff("k8s/app.yaml", "image: latest"),
    )
    assert result.verdict is DeployVerdict.ESCALATE_POLICY_VIOLATION
