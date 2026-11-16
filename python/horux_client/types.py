"""
Type definitions for Horux client SDK
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class MetricType(str, Enum):
    """Metric type enumeration"""

    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    SUMMARY = "summary"


class LogLevel(str, Enum):
    """Log level enumeration"""

    TRACE = "trace"
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    FATAL = "fatal"


@dataclass
class HistogramBucket:
    """Histogram bucket definition"""

    upper_bound: float
    count: int


@dataclass
class SummaryQuantile:
    """Summary quantile definition"""

    quantile: float
    value: float


@dataclass
class Metric:
    """Metric data structure"""

    name: str
    type: MetricType
    value: float | None = None
    dimensions: dict[str, str] | None = None  # Infrastructure/instance identifiers
    labels: dict[str, str] | None = None  # Metric-specific labels
    timestamp: datetime | None = None
    buckets: list[HistogramBucket] | None = None
    sum: float | None = None
    count: int | None = None
    quantiles: list[SummaryQuantile] | None = None


@dataclass
class LogEntry:
    """Log entry data structure"""

    level: LogLevel
    message: str
    timestamp: datetime | None = None
    fields: dict[str, Any] | None = None
    source: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    request_id: str | None = None


@dataclass
class MetricsPushRequest:
    """Metrics push request payload"""

    resource_id: str
    metrics: list[dict[str, Any]]


@dataclass
class LogsPushRequest:
    """Logs push request payload"""

    resource_id: str
    format: str
    dimensions: dict[str, Any] | None  # Infrastructure/instance identifiers
    logs: list[dict[str, Any]]


@dataclass
class PushResponse:
    """Response from push endpoints"""

    accepted: int
    rejected: int
    message: str


@dataclass
class BufferStats:
    """
    This ingestor instance's metrics backpressure.

    The same condition a push response reports as ``buffer_full``, and the part
    a client can actually act on.
    """

    queued_metrics: int
    capacity: int
    utilization_percent: float

    #: Data the server-side buffer took and then shed because its writer could
    #: not keep up: loss that has already happened and that no client-side retry
    #: can recover, so it is the figure worth alerting on.
    dropped_metrics: int = 0
    dropped_batches: int = 0


@dataclass
class MetricsStats:
    """
    Metrics ingestion statistics.

    Only the buffer block is returned to a tenant token. The writer counters
    this endpoint used to include - total metrics written, batches written,
    write errors - were server-wide, spanning every tenant, so they are now
    returned only to the internal service key. They are deliberately absent from
    this type rather than present and always zero: a counter that reads as fact
    and is not is worse than no counter, and zero is indistinguishable from a
    genuinely idle ingestor.
    """

    buffer: BufferStats


@dataclass
class LogBufferStats:
    """This ingestor instance's logs backpressure."""

    queued_logs: int
    capacity: int
    utilization_percent: float

    #: See BufferStats: loss already incurred server-side.
    dropped_logs: int = 0
    dropped_batches: int = 0


@dataclass
class LogsStats:
    """
    Logs ingestion statistics. See MetricsStats for why the server-wide writer
    counters are absent rather than zero.
    """

    buffer: LogBufferStats


@dataclass
class CheckInResponse:
    """Response from cron job check-in endpoint"""

    success: bool
    message: str
    next_expected_at: str
