"""Prometheus-text metrics collector."""

from __future__ import annotations

import threading

_MAX_LATENCY = 10000
_BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[str, int] = {}
        self._upstream: dict[str, int] = {}
        # Incremental latency stats
        self._lat_sum: dict[str, float] = {}
        self._lat_count: dict[str, int] = {}
        # Incremental histogram bucket counts per route
        self._bucket_counts: dict[str, list[int]] = {}

    def record_request(self, route: str, latency: float, upstream_status: int = 0) -> None:
        with self._lock:
            self._counts[route] = self._counts.get(route, 0) + 1
            self._lat_sum[route] = self._lat_sum.get(route, 0.0) + latency
            self._lat_count[route] = self._lat_count.get(route, 0) + 1
            # Incremental bucket update: O(len(BUCKETS)) per write
            bc = self._bucket_counts.setdefault(route, [0] * len(_BUCKETS))
            for i, b in enumerate(_BUCKETS):
                if latency <= b:
                    bc[i] += 1
            if upstream_status:
                bucket = f"{upstream_status // 100}xx"
                key = f"{route}:{bucket}"
                self._upstream[key] = self._upstream.get(key, 0) + 1

    def render(self) -> str:
        with self._lock:
            lines: list[str] = []
            for route, count in sorted(self._counts.items()):
                lines.append(f'claudify_requests_total{{route="{route}"}} {count}')
            for route in sorted(self._bucket_counts):
                bc = self._bucket_counts[route]
                for i, b in enumerate(_BUCKETS):
                    lines.append(f'claudify_request_latency_seconds_bucket{{le="{b}",route="{route}"}} {bc[i]}')
                total = self._lat_count.get(route, 0)
                lines.append(f'claudify_request_latency_seconds_bucket{{le="+Inf",route="{route}"}} {total}')
                lines.append(
                    f'claudify_request_latency_seconds_sum{{route="{route}"}} {self._lat_sum.get(route, 0.0):.6f}'
                )
                lines.append(
                    f'claudify_request_latency_seconds_count{{route="{route}"}} {total}'
                )
            for key, count in sorted(self._upstream.items()):
                route, bucket = key.rsplit(":", 1)
                lines.append(f'claudify_upstream_responses_total{{route="{route}",status="{bucket}"}} {count}')
            return "\n".join(lines) + "\n"
