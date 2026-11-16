"""
Logs client for Horux SDK
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import requests

from ._dispatch import Dispatcher, PushOutcome, QueueStats, do_push, wire_timestamp
from .config import HoruxConfig
from .types import LogBufferStats, LogEntry, LogLevel, LogsStats

logger = logging.getLogger(__name__)


class LogsClient:
    """Client for sending logs to Horux"""

    def __init__(self, config: HoruxConfig):
        """
        Initialize logs client

        Args:
            config: Horux configuration
        """
        config.validate()
        self.config = config
        self._session = requests.Session()
        self._dispatch: Dispatcher[LogEntry] = Dispatcher(config, "logs", self._send)

    def trace(
        self,
        message: str,
        fields: dict[str, Any] | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        request_id: str | None = None,
    ) -> None:
        """
        Log a trace message (most verbose)

        Args:
            message: Log message
            fields: Additional structured fields
            trace_id: Distributed trace ID
            span_id: Span ID
            parent_span_id: Parent span ID for caller detection
            request_id: Request ID
        """
        self._log(LogLevel.TRACE, message, fields, trace_id, span_id, parent_span_id, request_id)

    def debug(
        self,
        message: str,
        fields: dict[str, Any] | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        request_id: str | None = None,
    ) -> None:
        """
        Log a debug message

        Args:
            message: Log message
            fields: Additional structured fields
            trace_id: Distributed trace ID
            span_id: Span ID
            parent_span_id: Parent span ID for caller detection
            request_id: Request ID
        """
        self._log(LogLevel.DEBUG, message, fields, trace_id, span_id, parent_span_id, request_id)

    def info(
        self,
        message: str,
        fields: dict[str, Any] | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        request_id: str | None = None,
    ) -> None:
        """
        Log an info message

        Args:
            message: Log message
            fields: Additional structured fields
            trace_id: Distributed trace ID
            span_id: Span ID
            parent_span_id: Parent span ID for caller detection
            request_id: Request ID
        """
        self._log(LogLevel.INFO, message, fields, trace_id, span_id, parent_span_id, request_id)

    def warn(
        self,
        message: str,
        fields: dict[str, Any] | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        request_id: str | None = None,
    ) -> None:
        """
        Log a warning message

        Args:
            message: Log message
            fields: Additional structured fields
            trace_id: Distributed trace ID
            span_id: Span ID
            parent_span_id: Parent span ID for caller detection
            request_id: Request ID
        """
        self._log(LogLevel.WARN, message, fields, trace_id, span_id, parent_span_id, request_id)

    def error(
        self,
        message: str,
        fields: dict[str, Any] | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        request_id: str | None = None,
    ) -> None:
        """
        Log an error message

        Args:
            message: Log message
            fields: Additional structured fields
            trace_id: Distributed trace ID
            span_id: Span ID
            parent_span_id: Parent span ID for caller detection
            request_id: Request ID
        """
        self._log(LogLevel.ERROR, message, fields, trace_id, span_id, parent_span_id, request_id)

    def fatal(
        self,
        message: str,
        fields: dict[str, Any] | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        request_id: str | None = None,
    ) -> None:
        """
        Log a fatal message (most severe)

        Args:
            message: Log message
            fields: Additional structured fields
            trace_id: Distributed trace ID
            span_id: Span ID
            parent_span_id: Parent span ID for caller detection
            request_id: Request ID
        """
        self._log(LogLevel.FATAL, message, fields, trace_id, span_id, parent_span_id, request_id)

    def stats(self) -> LogsStats:
        """
        Fetch logs ingestion statistics from the data ingestor

        Returns:
            LogsStats with buffer and writer statistics

        Raises:
            requests.HTTPError: If the request fails
        """
        response = requests.get(
            f"{self.config.ingestor_url}/api/v1/logs/stats",
            headers={"Authorization": f"Bearer {self.config.api_token}"},
            timeout=self.config.http_timeout,
        )
        response.raise_for_status()
        data = response.json()

        # Only the buffer block comes back to a tenant token, and the server may
        # add fields to it; see MetricsClient.stats().
        buffer = data.get("buffer") or {}
        return LogsStats(
            buffer=LogBufferStats(
                queued_logs=buffer.get("queued_logs", 0),
                capacity=buffer.get("capacity", 0),
                utilization_percent=buffer.get("utilization_percent", 0.0),
                dropped_logs=buffer.get("dropped_logs", 0),
                dropped_batches=buffer.get("dropped_batches", 0),
            )
        )

    def _log(
        self,
        level: LogLevel,
        message: str,
        fields: dict[str, Any] | None,
        trace_id: str | None,
        span_id: str | None,
        parent_span_id: str | None,
        request_id: str | None,
    ) -> None:
        """Internal log method"""
        log_entry = LogEntry(
            level=level,
            message=message,
            timestamp=datetime.now(timezone.utc),
            fields=fields,
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            request_id=request_id,
        )
        self._record(log_entry)

    def _record(self, log_entry: LogEntry) -> None:
        """
        Queue a log entry for delivery.

        This never performs I/O: sending happens on the dispatcher's worker
        thread, so a throttled ingestor cannot slow down the caller.
        """
        self._dispatch.add(log_entry)

    def flush(self) -> None:
        """
        Flush buffered logs immediately.

        Logs are sent in batches of at most max_batch_size. If the ingestor can
        only take part of what is pending, the remainder stays queued for the
        next cycle.
        """
        self._dispatch.flush()

    def queue_stats(self) -> QueueStats:
        """
        The state of the client-side send queue: what is pending, what has been
        dropped, and whether the client is currently backing off.
        """
        return self._dispatch.stats()

    def _send(self, logs: list[LogEntry]) -> PushOutcome:
        """Deliver one batch to the ingestor."""
        payload = {
            "resource_id": self.config.resource_id,
            "format": "json",
            # Infrastructure/instance identifiers
            "dimensions": self.config.instance_metadata,
            "logs": [self._serialize(log) for log in logs],
        }

        return do_push(self._session, self.config, "/api/v1/logs/push", payload, len(logs))

    @staticmethod
    def _serialize(log: LogEntry) -> dict[str, Any]:
        """Convert a log entry to its wire format."""
        data: dict[str, Any] = {
            "level": log.level.value,
            "message": log.message,
            "timestamp": wire_timestamp(log.timestamp),
        }

        if log.fields:
            data["fields"] = log.fields

        if log.source:
            data["source"] = log.source

        if log.trace_id:
            data["trace_id"] = log.trace_id

        if log.span_id:
            data["span_id"] = log.span_id

        if log.parent_span_id:
            data["parent_span_id"] = log.parent_span_id

        if log.request_id:
            data["request_id"] = log.request_id

        return data

    def close(self) -> None:
        """Close client and flush remaining logs"""
        self._dispatch.close()
        self._session.close()
