import pytest

from app.core import metrics


@pytest.fixture(autouse=True)
def fresh_metrics():
    metrics.reset()
    metrics.configure(0)
    yield
    metrics.reset()
    metrics.configure(metrics.DEFAULT_SNAPSHOT_SECONDS)


def test_aggregates_llm_calls_per_provider():
    metrics.record_llm_call("llama-local", "Qwen", "ok", 100.0)
    metrics.record_llm_call("llama-local", "Qwen", "ok", 300.0)
    metrics.record_llm_call("llama-local", "Qwen", "timeout", 600.0)

    lines = metrics.snapshot()

    assert len(lines) == 1
    assert "group=llm key=llama-local" in lines[0]
    assert "calls=3 failures=1" in lines[0]
    assert "failure_rate=0.333" in lines[0]
    assert "avg_ms=333.3" in lines[0]
    assert "max_ms=600.0" in lines[0]


def test_missing_provider_id_is_bucketed_as_unknown():
    metrics.record_llm_call(None, None, "ok", 10.0)
    metrics.record_llm_call("qwen", "qwen-max", "error", 20.0)

    lines = metrics.snapshot()

    assert len(lines) == 2
    assert any("key=qwen " in line for line in lines)
    assert any("key=unknown " in line for line in lines)


def test_generation_jobs_are_bucketed_by_kind():
    metrics.record_generation_job("outline", "ok", 1000.0)
    metrics.record_generation_job("classroom", "failed", 2000.0)

    lines = metrics.snapshot()

    assert any(
        "group=generation_job key=outline calls=1 failures=0" in line
        for line in lines
    )
    assert any(
        "group=generation_job key=classroom calls=1 failures=1" in line
        for line in lines
    )


def test_snapshot_is_logged_only_after_the_interval(monkeypatch):
    emitted = []
    monkeypatch.setattr(
        metrics.logger, "info", lambda message, *args: emitted.append(message % args)
    )
    metrics.configure(3600)

    metrics.record_llm_call("llama-local", "Qwen", "ok", 120.0)
    assert not [m for m in emitted if m.startswith("metrics_snapshot")]

    metrics.maybe_log_snapshot(force=True)
    assert [m for m in emitted if m.startswith("metrics_snapshot")]


def test_reset_clears_buckets():
    metrics.record_llm_call("llama-local", "Qwen", "ok", 1.0)

    metrics.reset()

    assert metrics.snapshot() == []
