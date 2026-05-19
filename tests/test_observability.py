"""Tests for metrics and observability."""

from __future__ import annotations

from claudify.app import _MAX_LATENCY, _Metrics


def test_metrics_record_and_render():
    m = _Metrics()
    m.record_request("/v1/messages", 0.5, 200)
    m.record_request("/v1/messages", 1.2, 500)
    m.record_request("/health", 0.01)
    text = m.render()
    assert 'claudify_requests_total{route="/v1/messages"}' in text
    assert 'claudify_requests_total{route="/health"}' in text
    assert "claudify_request_latency_seconds_bucket" in text
    assert "claudify_upstream_responses_total" in text


def test_metrics_upstream_buckets():
    m = _Metrics()
    m.record_request("/v1/messages", 0.1, 200)
    m.record_request("/v1/messages", 0.2, 429)
    m.record_request("/v1/messages", 0.3, 503)
    text = m.render()
    assert 'status="2xx"' in text
    assert 'status="4xx"' in text
    assert 'status="5xx"' in text


def test_metrics_empty():
    m = _Metrics()
    text = m.render()
    assert "claudify_request_latency_seconds_count" not in text


def test_metrics_ring_buffer():
    m = _Metrics()
    for i in range(_MAX_LATENCY + 500):
        m.record_request("/v1/messages", float(i) * 0.001, 200)
    assert len(m._latencies) == _MAX_LATENCY
    text = m.render()
    assert "claudify_request_latency_seconds" in text
