"""
The server-side stats response, as this client now reads it.

The ingestor returns the ``writer`` block only to the internal service key: its
counters - total written, batches written, write errors - span *every* tenant,
and used to be handed to any tenant bearer token. This client read
``data["writer"]`` unconditionally, so against the current server ``stats()``
raised ``KeyError`` rather than returning anything; and because it splatted the
buffer dict into a dataclass, the new ``dropped_*`` counters raised ``TypeError``
on top. Both are pinned here.
"""

from __future__ import annotations

import os
import sys
import unittest
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from horux_client.logs_client import LogsClient
from horux_client.metrics_client import MetricsClient


class FakeConfig:
    ingestor_url = "http://ingestor.invalid"
    api_token = "tenant_x_secret"
    resource_id = "resource-1"
    http_timeout = 1.0
    flush_interval = 3600.0
    max_batch_size = 10
    max_buffer_size = 100
    drop_policy = "oldest"
    min_retry_backoff = 1.0
    max_retry_backoff = 60.0
    on_drop = None
    auto_add_instance_labels = False
    instance_metadata: dict[str, Any] = {}


class FakeResponse:
    def __init__(self, payload: Any):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


class StatsTest(unittest.TestCase):
    """Runs the real stats() body against a stubbed requests.get."""

    def test_metrics_stats_reads_the_buffer_block(self):
        payload = {
            "buffer": {
                "queued_metrics": 12,
                "capacity": 1000,
                "utilization_percent": 1.2,
                "dropped_metrics": 7,
                "dropped_batches": 1,
            }
        }

        stats = _call_stats(MetricsClient, payload)

        self.assertEqual(stats.buffer.queued_metrics, 12)
        self.assertEqual(stats.buffer.capacity, 1000)
        self.assertEqual(stats.buffer.dropped_metrics, 7)
        self.assertEqual(stats.buffer.dropped_batches, 1)
        self.assertFalse(
            hasattr(stats, "writer"),
            "the server-wide writer counters must be absent, not zero: a counter "
            "that reads as fact and is not is worse than no counter",
        )

    def test_metrics_stats_tolerates_a_server_that_adds_a_counter(self):
        # Splatting the dict into the dataclass raised TypeError the moment the
        # server grew a field, which is exactly what happened.
        payload = {
            "buffer": {
                "queued_metrics": 1,
                "capacity": 10,
                "utilization_percent": 10.0,
                "some_future_counter": 5,
            }
        }

        stats = _call_stats(MetricsClient, payload)

        self.assertEqual(stats.buffer.queued_metrics, 1)
        self.assertEqual(stats.buffer.dropped_metrics, 0)

    def test_metrics_stats_survives_a_response_with_no_buffer_block(self):
        stats = _call_stats(MetricsClient, {})

        self.assertEqual(stats.buffer.queued_metrics, 0)
        self.assertEqual(stats.buffer.utilization_percent, 0.0)

    def test_logs_stats_reads_the_buffer_block(self):
        payload = {
            "buffer": {
                "queued_logs": 3,
                "capacity": 500,
                "utilization_percent": 0.6,
                "dropped_logs": 2,
            }
        }

        stats = _call_stats(LogsClient, payload)

        self.assertEqual(stats.buffer.queued_logs, 3)
        self.assertEqual(stats.buffer.dropped_logs, 2)
        self.assertEqual(stats.buffer.dropped_batches, 0, "a field the server omits reads as 0")


def _call_stats(client_class: type, payload: Any):
    """Run the real stats() body against a stubbed requests.get."""
    import horux_client.logs_client as logs_module
    import horux_client.metrics_client as metrics_module

    module = metrics_module if client_class is MetricsClient else logs_module
    original = module.requests.get

    captured: dict[str, str | None] = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        return FakeResponse(payload)

    module.requests.get = fake_get
    try:
        client = client_class.__new__(client_class)
        client.config = FakeConfig()
        return client_class.stats(client)
    finally:
        module.requests.get = original


if __name__ == "__main__":
    unittest.main()
