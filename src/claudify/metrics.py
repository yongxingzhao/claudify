"""Prometheus-text metrics collector."""

from __future__ import annotations

from collections import deque

_MAX_LATENCY = 10000


class Metrics:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._latencies: deque[tuple[float, str]] = deque(maxlen=_MAX_LATENCY)
        self._upstream: dict[str, int] = {}

    def record_request(self, route: str, latency: float, upstream_status: int = 0) -> None:
        self._counts[route] = self._counts.get(route, 0) + 1
        self._latencies.append((latency, route))
        if upstream_status:
            bucket = f"{upstream_status // 100}xx"
            key = f"{route}:{bucket}"
            self._upstream[key] = self._upstream.get(key, 0) + 1

    def render(self) -> str:
        lines: list[str] = []
        for route, count in sorted(self._counts.items()):
            lines.append(f'claudify_requests_total{{route="{route}"}} {count}')
        buckets = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
        by_route: dict[str, list[float]] = {}
        for lat, route in self._latencies:
            by_route.setdefault(route, []).append(lat)
        for route in sorted(by_route):
            lats = by_route[route]
            for b in buckets:
                cnt = sum(1 for lat in lats if lat <= b)
                lines.append(f'claudify_request_latency_seconds_bucket{{le="{b}",route="{route}"}} {cnt}')
            lines.append(f'claudify_request_latency_seconds_bucket{{le="+Inf",route="{route}"}} {len(lats)}')
            total = sum(lats)
            lines.append(f'claudify_request_latency_seconds_sum{{route="{route}"}} {total:.6f}')
            lines.append(f'claudify_request_latency_seconds_count{{route="{route}"}} {len(lats)}')
        for key, count in sorted(self._upstream.items()):
            route, bucket = key.rsplit(":", 1)
            lines.append(f'claudify_upstream_responses_total{{route="{route}",status="{bucket}"}} {count}')
        return "\n".join(lines) + "\n"
