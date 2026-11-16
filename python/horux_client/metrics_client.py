"""
Metrics client for Horux SDK
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import requests

from ._dispatch import Dispatcher, PushOutcome, QueueStats, do_push, wire_timestamp
from .config import HoruxConfig
from .types import BufferStats, HistogramBucket, Metric, MetricsStats, MetricType, SummaryQuantile

logger = logging.getLogger(__name__)


class MetricsClient:
    """Client for sending metrics to Horux"""

    def __init__(self, config: HoruxConfig):
        """
        Initialize metrics client

        Args:
            config: Horux configuration
        """
        config.validate()
        self.config = config
        self._session = requests.Session()
        self._dispatch: Dispatcher[Metric] = Dispatcher(config, "metrics", self._send)

    def counter(self, name: str, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
        """
        Record a counter metric (always increases)

        Args:
            name: Metric name
            value: Value to add (default: 1.0)
            labels: Optional labels/tags
        """
        dimensions, user_labels = self._get_dimensions_and_labels(labels)
        metric = Metric(
            name=name,
            type=MetricType.COUNTER,
            value=value,
            dimensions=dimensions,
            labels=user_labels,
            timestamp=datetime.now(timezone.utc),
        )
        self._record(metric)

    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """
        Record a gauge metric (can go up or down)

        Args:
            name: Metric name
            value: Current value
            labels: Optional labels/tags
        """
        dimensions, user_labels = self._get_dimensions_and_labels(labels)
        metric = Metric(
            name=name,
            type=MetricType.GAUGE,
            value=value,
            dimensions=dimensions,
            labels=user_labels,
            timestamp=datetime.now(timezone.utc),
        )
        self._record(metric)

    def histogram(
        self,
        name: str,
        buckets: list[HistogramBucket],
        sum: float,
        count: int,
        labels: dict[str, str] | None = None,
    ) -> None:
        """
        Record a histogram metric (distribution)

        Args:
            name: Metric name
            buckets: List of histogram buckets
            sum: Sum of all observed values
            count: Total count of observations
            labels: Optional labels/tags
        """
        dimensions, user_labels = self._get_dimensions_and_labels(labels)
        metric = Metric(
            name=name,
            type=MetricType.HISTOGRAM,
            buckets=buckets,
            sum=sum,
            count=count,
            dimensions=dimensions,
            labels=user_labels,
            timestamp=datetime.now(timezone.utc),
        )
        self._record(metric)

    def summary(
        self,
        name: str,
        quantiles: list[SummaryQuantile],
        sum: float,
        count: int,
        labels: dict[str, str] | None = None,
    ) -> None:
        """
        Record a summary metric (quantiles/percentiles)

        Args:
            name: Metric name
            quantiles: List of quantiles
            sum: Sum of all observed values
            count: Total count of observations
            labels: Optional labels/tags
        """
        dimensions, user_labels = self._get_dimensions_and_labels(labels)
        metric = Metric(
            name=name,
            type=MetricType.SUMMARY,
            quantiles=quantiles,
            sum=sum,
            count=count,
            dimensions=dimensions,
            labels=user_labels,
            timestamp=datetime.now(timezone.utc),
        )
        self._record(metric)

    def stats(self) -> MetricsStats:
        """
        Fetch metrics ingestion statistics from the data ingestor

        Returns:
            MetricsStats with this ingestor instance's buffer statistics

        Raises:
            requests.HTTPError: If the request fails
        """
        response = requests.get(
            f"{self.config.ingestor_url}/api/v1/metrics/stats",
            headers={"Authorization": f"Bearer {self.config.api_token}"},
            timeout=self.config.http_timeout,
        )
        response.raise_for_status()
        data = response.json()

        # Only the buffer block comes back to a tenant token, and the server may
        # add fields to it; take the ones this type declares rather than
        # splatting the dict, which raised TypeError the moment the server grew
        # a counter.
        buffer = data.get("buffer") or {}
        return MetricsStats(
            buffer=BufferStats(
                queued_metrics=buffer.get("queued_metrics", 0),
                capacity=buffer.get("capacity", 0),
                utilization_percent=buffer.get("utilization_percent", 0.0),
                dropped_metrics=buffer.get("dropped_metrics", 0),
                dropped_batches=buffer.get("dropped_batches", 0),
            )
        )

    def _record(self, metric: Metric) -> None:
        """
        Queue a metric for delivery.

        This never performs I/O: sending happens on the dispatcher's worker
        thread, so a throttled ingestor cannot slow down the caller.
        """
        self._dispatch.add(metric)

    def _get_dimensions_and_labels(
        self, user_labels: dict[str, str] | None
    ) -> tuple[dict[str, str] | None, dict[str, str] | None]:
        """
        Separate dimensions (instance metadata) from labels (user-provided).

        Returns:
            Tuple of (dimensions, labels)
        """
        dimensions = None
        labels = user_labels

        # Add instance metadata as dimensions if enabled
        if self.config.auto_add_instance_labels and self.config.instance_metadata:
            dimensions = {}
            for key, value in self.config.instance_metadata.items():
                dimensions[key] = str(value)

        return dimensions, labels

    def flush(self) -> None:
        """
        Flush buffered metrics immediately.

        Metrics are sent in batches of at most max_batch_size. If the ingestor
        can only take part of what is pending, the remainder stays queued for
        the next cycle.
        """
        self._dispatch.flush()

    def queue_stats(self) -> QueueStats:
        """
        The state of the client-side send queue: what is pending, what has been
        dropped, and whether the client is currently backing off.
        """
        return self._dispatch.stats()

    def _send(self, metrics: list[Metric]) -> PushOutcome:
        """Deliver one batch to the ingestor."""
        payload = {
            "resource_id": self.config.resource_id,
            "metrics": [self._serialize(metric) for metric in metrics],
        }

        return do_push(self._session, self.config, "/api/v1/metrics/push", payload, len(metrics))

    @staticmethod
    def _serialize(metric: Metric) -> dict[str, Any]:
        """Convert a metric to its wire format."""
        data: dict[str, Any] = {
            "name": metric.name,
            "type": metric.type.value,
            "timestamp": wire_timestamp(metric.timestamp),
        }

        if metric.value is not None:
            data["value"] = metric.value

        # Dimensions identify the instance; labels are metric-specific.
        if metric.dimensions:
            data["dimensions"] = metric.dimensions

        if metric.labels:
            data["labels"] = metric.labels

        if metric.buckets:
            data["buckets"] = [
                {"upper_bound": b.upper_bound, "count": b.count} for b in metric.buckets
            ]

        if metric.quantiles:
            data["quantiles"] = [
                {"quantile": q.quantile, "value": q.value} for q in metric.quantiles
            ]

        if metric.sum is not None:
            data["sum"] = metric.sum

        if metric.count is not None:
            data["count"] = metric.count

        return data

    def close(self) -> None:
        """Close client and flush remaining metrics"""
        self._dispatch.close()
        self._session.close()
