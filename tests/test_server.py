import hashlib
import hmac
import json

import pytest
import yaml
from fastapi.testclient import TestClient

from autoproduct.server import create_app

SECRET = "test-webhook-secret"


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOPRODUCT_WEBHOOK_SECRET", SECRET)
    spawned: list[list[str]] = []

    def fake_spawn(args, repo_dir):
        spawned.append(args)
        return 4242

    client = TestClient(create_app(str(tmp_path), spawn=fake_spawn))
    return client, spawned, tmp_path


def _signed(payload: dict, secret: str = SECRET):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return body, {"X-Hub-Signature-256": sig, "X-GitHub-Event": "pull_request"}


PR_PAYLOAD = {
    "action": "opened",
    "pull_request": {"html_url": "https://github.com/x/y/pull/7"},
}


def test_valid_webhook_queues_review(harness):
    client, spawned, _ = harness
    body, headers = _signed(PR_PAYLOAD)
    response = client.post("/webhook/github", content=body, headers=headers)
    assert response.status_code == 202
    assert response.json()["queued"] is True
    assert spawned == [["review", "https://github.com/x/y/pull/7"]]


def test_bad_signature_rejected_before_parsing(harness):
    client, spawned, _ = harness
    body, headers = _signed(PR_PAYLOAD, secret="wrong-secret")
    response = client.post("/webhook/github", content=body, headers=headers)
    assert response.status_code == 401
    assert spawned == []


def test_missing_secret_is_503(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTOPRODUCT_WEBHOOK_SECRET", raising=False)
    client = TestClient(create_app(str(tmp_path), spawn=lambda *a: 0))
    body, headers = _signed(PR_PAYLOAD)
    assert client.post("/webhook/github", content=body, headers=headers).status_code == 503


def test_non_review_actions_ignored(harness):
    client, spawned, _ = harness
    body, headers = _signed({"action": "labeled", "pull_request": {"html_url": "x"}})
    response = client.post("/webhook/github", content=body, headers=headers)
    assert response.status_code == 202
    assert response.json()["queued"] is False
    assert spawned == []


def test_incident_post_writes_inbox_and_spawns_triage(harness):
    client, spawned, tmp_path = harness
    response = client.post(
        "/incidents", json={"title": "Checkout 500s", "body": "spike", "source": "sentry"}
    )
    assert response.status_code == 202
    incident_id = response.json()["incident_id"]
    inbox_file = tmp_path / ".mas" / "inbox" / f"{incident_id}.yaml"
    data = yaml.safe_load(inbox_file.read_text())
    assert data["title"] == "Checkout 500s" and data["source"] == "sentry"
    assert spawned[-1][0] == "triage"


def test_incident_without_title_rejected(harness):
    client, spawned, _ = harness
    assert client.post("/incidents", json={"body": "??"}).status_code == 422
    assert spawned == []


def test_reviews_endpoints_read_mirrors(harness, planted_diff_text, skills_dir):
    client, _, tmp_path = harness
    from autoproduct.orchestrator import run_review

    _, state = run_review(
        "fixture://server",
        repo_dir=str(tmp_path),
        skills_dir=skills_dir,
        provider_override="mock",
        diff_text=planted_diff_text,
    )
    listing = client.get("/reviews").json()
    assert any(
        r["review_id"] == state["review_id"] and r["verdict"] == "REQUEST_CHANGES"
        for r in listing
    )
    detail = client.get(f"/reviews/{state['review_id']}").json()
    assert detail["verdict"] == "REQUEST_CHANGES"
    assert [s["node"] for s in detail["steps"]][0] == "dor_gate"
    assert client.get("/reviews/nope").status_code == 404
