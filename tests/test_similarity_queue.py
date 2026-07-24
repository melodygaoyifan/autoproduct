"""Deferral closures: lexical similarity matching + SQLite job queue."""

from autoproduct.jobqueue import Job, claim, complete, enqueue, pending, requeue_stale, worker_loop
from autoproduct.similarity import rank, tokenize
from autoproduct.upstream.blocks import matching_blocks


# --- similarity ---------------------------------------------------------------

def test_tokenize_mixed_cjk_latin():
    tokens = tokenize("用户 login 微信支付")
    assert "login" in tokens
    assert "支" in tokens and "付" in tokens  # unigrams
    assert "支付" in tokens and "微信" in tokens  # bigrams


def test_rank_prefers_semantic_match():
    docs = [
        "wechat pay payment checkout 支付付款收款",
        "subscribe notification 订阅消息提醒",
    ]
    best = rank("下单后需要付款结账", docs)[0]
    assert best[0] == 0


def test_paraphrased_fdr_matches_wxpay_without_exact_keyword():
    # 充值/收银 appear in no _KEYWORDS entry — the old exact matcher
    # returned nothing for this.
    hits = matching_blocks("miniprogram", "会员卡余额充值，收银台结账")
    assert "miniprogram/wxpay.js" in hits


def test_exact_keyword_still_first():
    hits = matching_blocks("miniprogram", "用户微信登录后订阅消息提醒")
    assert hits[0] in ("miniprogram/wxlogin.js", "miniprogram/subscribe.js")
    assert set(hits) >= {"miniprogram/wxlogin.js", "miniprogram/subscribe.js"}


def test_unrelated_text_matches_nothing():
    assert matching_blocks("miniprogram", "the weather is nice today") == []


# --- job queue ----------------------------------------------------------------

def test_enqueue_claim_complete(tmp_path):
    db = tmp_path / "q.db"
    job_id = enqueue(db, "review", ["review", "HEAD~1"])
    job = claim(db, "w1")
    assert job is not None and job.id == job_id and job.argv == ["review", "HEAD~1"]
    # Claimed job is invisible to a second worker.
    assert claim(db, "w2") is None
    complete(db, job.id, ok=True, detail="APPROVE")
    rows = pending(db)
    assert rows[0]["status"] == "done" and rows[0]["worker"] == "w1"


def test_two_workers_never_share_a_job(tmp_path):
    db = tmp_path / "q.db"
    for i in range(6):
        enqueue(db, "review", ["review", f"pr-{i}"])
    seen = []
    while (job := claim(db, "either")) is not None:
        assert job.id not in seen
        seen.append(job.id)
        complete(db, job.id, ok=True)
    assert len(seen) == 6


def test_stale_running_job_requeues(tmp_path):
    db = tmp_path / "q.db"
    enqueue(db, "triage", ["triage", "x.yaml"])
    assert claim(db, "dead-worker") is not None
    assert requeue_stale(db, max_age_s=0) == 1
    job = claim(db, "w2")
    assert job is not None and job.worker == "w2"


def test_worker_loop_drains_with_custom_runner(tmp_path):
    db = tmp_path / "q.db"
    for i in range(3):
        enqueue(db, "review", ["review", f"pr-{i}"])
    ran: list[Job] = []

    def runner(job: Job):
        ran.append(job)
        return True, "ok"

    done = worker_loop(db, tmp_path, max_jobs=10, runner=runner)
    assert done == 3 and len(ran) == 3
    assert all(r["status"] == "done" for r in pending(db))


def test_server_spawn_enqueues_when_env_set(tmp_path, monkeypatch):
    from autoproduct.server import _spawn

    db = tmp_path / "q.db"
    monkeypatch.setenv("AUTOPRODUCT_QUEUE_DB", str(db))
    ticket = _spawn(["review", "https://example/pr/1"], str(tmp_path))
    assert ticket < 0  # queue ticket, not a pid
    job = claim(db, "w1")
    assert job is not None and job.argv[0] == "review"
