"""Tests for metrics and observability."""

from __future__ import annotations

from claudify.metrics import Metrics


def test_metrics_record_and_render():
    m = Metrics()
    m.record_request("/v1/messages", 0.5, 200)
    m.record_request("/v1/messages", 1.2, 500)
    m.record_request("/health", 0.01)
    text = m.render()
    assert 'claudify_requests_total{route="/v1/messages"}' in text
    assert 'claudify_requests_total{route="/health"}' in text
    assert "claudify_request_latency_seconds_bucket" in text
    assert "claudify_upstream_responses_total" in text


def test_metrics_upstream_buckets():
    m = Metrics()
    m.record_request("/v1/messages", 0.1, 200)
    m.record_request("/v1/messages", 0.2, 429)
    m.record_request("/v1/messages", 0.3, 503)
    text = m.render()
    assert 'status="2xx"' in text
    assert 'status="4xx"' in text
    assert 'status="5xx"' in text


def test_metrics_empty():
    m = Metrics()
    text = m.render()
    assert "claudify_request_latency_seconds_count" not in text


def test_metrics_bucket_counts():
    m = Metrics()
    m.record_request("/v1/messages", 0.003, 200)
    m.record_request("/v1/messages", 0.05, 200)
    m.record_request("/v1/messages", 1.0, 200)
    text = m.render()
    # 0.003 <= 0.005, so le="0.005" should be >= 1
    assert 'le="0.005"' in text
    # All three <= +Inf
    assert 'le="+Inf"' in text


def test_metrics_thread_safety():
    import threading
    m = Metrics()
    errors = []
    def worker():
        try:
            for _ in range(100):
                m.record_request("/v1/messages", 0.01, 200)
        except Exception as e:
            errors.append(e)
    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert m._counts.get("/v1/messages") == 1000
