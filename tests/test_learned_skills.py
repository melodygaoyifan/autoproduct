import subprocess
from pathlib import Path

import yaml

from autoproduct.maintenance import Incident, run_maintenance
from autoproduct.maintenance.skills_registry import (
    LearnedSkill,
    load_registry,
    match,
    maybe_draft_skill,
    record_incident,
)

INCIDENT = "Redis connection pool exhausted in checkout worker under load spike"


def _write_skill(repo: Path, status: str) -> None:
    skills_dir = repo / ".mas" / "learned-skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "name": "redis-pool-exhaustion",
        "description": "recurring pool exhaustion class",
        "status": status,
        "trigger_tokens": ["redis", "connection", "pool", "exhausted", "checkout"],
        "instances": [],
    }
    skills_dir.joinpath("redis-pool-exhaustion.md").write_text(
        f"---\n{yaml.safe_dump(meta, sort_keys=False)}---\n\nCheck pool max_connections first.\n"
    )


def test_approved_skill_matches(tmp_path):
    _write_skill(tmp_path, "approved")
    skill = match(INCIDENT, load_registry(tmp_path))
    assert skill is not None and skill.name == "redis-pool-exhaustion"
    assert "max_connections" in skill.body


def test_proposed_skill_never_injected(tmp_path):
    _write_skill(tmp_path, "proposed")
    assert match(INCIDENT, load_registry(tmp_path)) is None


def test_unrelated_incident_no_match(tmp_path):
    _write_skill(tmp_path, "approved")
    assert match("CSS misalignment on the about page footer", load_registry(tmp_path)) is None


def test_recurrence_drafts_proposed_skill(tmp_path):
    texts = [
        f"Redis connection pool exhausted in checkout worker, spike {i}"
        for i in range(3)
    ]
    for i, text in enumerate(texts[:2]):
        record_incident(tmp_path, f"inc{i}", text)
    similar = record_incident(tmp_path, "inc2", texts[2])
    assert len(similar) == 2
    drafted = maybe_draft_skill(tmp_path, texts, provider="mock", model="m")
    assert drafted is not None and drafted.status == "proposed"
    # A covering skill now exists: no duplicate drafts.
    assert maybe_draft_skill(tmp_path, texts, provider="mock", model="m") is None


def test_e2e_third_incident_drafts_and_approved_skill_applies(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)

    for i in range(3):
        incident = Incident(
            id=f"pool{i}",
            title="Redis connection pool exhausted in checkout worker",
            body=f"spike number {i}",
        )
        result = run_maintenance(incident, repo_dir=str(repo), provider="mock")

    assert "skill drafted: mock-recurring-class" in result.summary
    registry = load_registry(repo)
    assert registry[0].status == "proposed"

    # Human approves -> the next incident gets the skill injected.
    path = Path(registry[0].path)
    path.write_text(path.read_text().replace("status: proposed", "status: approved"))
    incident = Incident(
        id="pool3",
        title="Redis connection pool exhausted in checkout worker",
        body="again",
    )
    result = run_maintenance(incident, repo_dir=str(repo), provider="mock")
    assert "learned skill applied: mock-recurring-class" in result.summary
    mirror = tmp_path / "proj" / ".mas" / "incidents" / "pool3"
    assert list(mirror.glob("*learned_skill.yaml"))
