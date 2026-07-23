import subprocess
from pathlib import Path

from autoproduct.maintenance import Incident, MaintenanceVerdict, run_maintenance
from autoproduct.maintenance.correlate import correlate


def _repo_with_history(tmp_path: Path) -> Path:
    repo = tmp_path / "proj"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)

    def commit(name: str, content: str, message: str):
        (repo / name).write_text(content)
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", message],
            cwd=repo, check=True,
        )

    commit("readme.md", "docs", "init")
    commit("billing.py", "def invoice_total(): ...", "add invoice_total to billing")
    commit("search.py", "def search(): ...", "search feature")
    return repo


INCIDENT_TEXT = (
    "TypeError in invoice_total\n"
    "Sentry reports TypeError: unsupported operand in billing invoice_total "
    "since the latest deploy."
)


def test_correlate_ranks_relevant_commit_first(tmp_path):
    repo = _repo_with_history(tmp_path)
    suspects = correlate(INCIDENT_TEXT, str(repo))
    assert suspects
    assert suspects[0].files == ["billing.py"]
    assert "invoice_total" in suspects[0].subject


def test_correlate_empty_outside_git(tmp_path):
    assert correlate(INCIDENT_TEXT, str(tmp_path)) == []


def test_maintenance_proposes_root_cause_with_suspects(tmp_path):
    repo = _repo_with_history(tmp_path)
    incident = Incident(id="inc1", title="TypeError in invoice_total", body=INCIDENT_TEXT)
    result = run_maintenance(incident, repo_dir=str(repo), provider="mock")
    assert result.verdict is MaintenanceVerdict.ROOT_CAUSE_PROPOSED
    assert result.triage.priority == "P2"
    assert result.root_cause.confidence >= 60
    mirror = sorted(Path(result.artifacts_dir).glob("[0-9]*-*.yaml"))
    assert [p.name.split("-", 1)[1] for p in mirror] == [
        "intake.yaml", "correlate.yaml", "triage.yaml", "root_cause.yaml", "final.yaml",
    ]


def test_maintenance_escalates_on_low_confidence(tmp_path):
    # Empty repo -> no suspects -> mock root-cause confidence 30 -> escalate.
    repo = tmp_path / "empty"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    incident = Incident(id="inc2", title="Mystery outage", body="everything is down")
    result = run_maintenance(incident, repo_dir=str(repo), provider="mock")
    assert result.verdict is MaintenanceVerdict.ESCALATE_INCIDENT_UNRESOLVED


def test_low_priority_skips_root_cause(tmp_path):
    repo = _repo_with_history(tmp_path)
    incident = Incident(id="inc3", title="cosmetic typo in footer", body="cosmetic only")
    result = run_maintenance(incident, repo_dir=str(repo), provider="mock")
    assert result.verdict is MaintenanceVerdict.TRIAGED_LOW_PRIORITY
    assert result.root_cause is None


def test_empty_incident_file_rejected_cleanly(tmp_path):
    import pytest

    path = tmp_path / "empty.txt"
    path.write_text("")
    with pytest.raises(ValueError, match="is empty"):
        Incident.load(path)


def test_incident_loads_from_text_file(tmp_path):
    path = tmp_path / "incident.txt"
    path.write_text(INCIDENT_TEXT)
    incident = Incident.load(path)
    assert incident.title.startswith("TypeError in invoice_total")
    assert "billing" in incident.body
