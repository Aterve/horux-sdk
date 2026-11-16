"""
Configuration for Horux client SDK
"""

from __future__ import annotations

import os
import socket
from typing import Any, Callable

from ._dispatch import DROP_NEWEST, DROP_OLDEST


class HoruxConfig:
    """Configuration for Horux client"""

    def __init__(
        self,
        ingestor_url: str = "https://ingest.horux.io",
        api_token: str | None = None,
        resource_id: str | None = None,
        flush_interval: float = 10.0,
        max_batch_size: int = 1000,
        http_timeout: float = 30.0,
        instance_metadata: dict[str, Any] | None = None,
        auto_add_instance_labels: bool = True,
        gateway_url: str | None = None,
        max_buffer_size: int | None = None,
        drop_policy: str = DROP_OLDEST,
        min_retry_backoff: float = 1.0,
        max_retry_backoff: float = 60.0,
        on_drop: Callable[[str, int, str], None] | None = None,
    ):
        """
        Initialize Horux configuration

        Args:
            ingestor_url: Base URL of the data-ingestor service (default: https://ingest.horux.io)
            api_token: API authentication token (format: tenant_<tenant-id>_<secret>)
            resource_id: UUID of the resource sending metrics/logs
            flush_interval: How often to flush buffered data (seconds)
            max_batch_size: Maximum number of items to send in one request
            http_timeout: HTTP request timeout (seconds)
            instance_metadata: Instance-specific metadata
            auto_add_instance_labels: Automatically add instance metadata to metrics as labels
            gateway_url: Base URL of the Horux gateway (required for cron job check-ins)
            max_buffer_size: The most items held while the ingestor is throttling
                or unreachable; past it drop_policy decides what goes. The bound
                is what keeps a rate limit from turning into unbounded memory
                growth inside the host application. Defaults to 10x
                max_batch_size.
            drop_policy: Which items to discard once max_buffer_size is reached,
                "oldest" (default) or "newest"
            min_retry_backoff: Shortest wait after a failed flush (seconds)
            max_retry_backoff: Longest wait after a failed flush (seconds); a
                server-supplied Retry-After takes precedence
            on_drop: Called as on_drop(stream, count, reason) when the client
                discards data. Notifications are throttled so a sustained outage
                does not itself become a flood.
        """
        self.ingestor_url = ingestor_url
        self.api_token = api_token
        self.resource_id = resource_id
        self.flush_interval = flush_interval
        self.max_batch_size = max_batch_size
        self.http_timeout = http_timeout
        self.auto_add_instance_labels = auto_add_instance_labels
        self.gateway_url = gateway_url
        self.max_buffer_size = max_buffer_size or max_batch_size * 10
        self.max_buffer_size = max(self.max_buffer_size, max_batch_size)
        self.drop_policy = drop_policy
        self.min_retry_backoff = min_retry_backoff
        self.max_retry_backoff = max(max_retry_backoff, min_retry_backoff)
        self.on_drop = on_drop

        # Initialize instance metadata with hostname
        self.instance_metadata = instance_metadata or {}
        if "hostname" not in self.instance_metadata:
            try:
                self.instance_metadata["hostname"] = socket.gethostname()
            except OSError:
                # Best-effort metadata: a client that cannot read its own hostname
                # must still construct. OSError is what gethostname() raises; the
                # bare `except Exception: pass` this replaces also swallowed
                # programming errors in anything added to this block later.
                pass

    @classmethod
    def from_env(cls) -> HoruxConfig:
        """
        Create configuration from environment variables

        Environment variables:
            HORUX_INGESTOR_URL: Base URL of the data-ingestor service
            HORUX_API_TOKEN: API authentication token
            HORUX_RESOURCE_ID: Resource UUID
            HORUX_GATEWAY_URL: Base URL of the Horux gateway (for cron job check-ins)
            POD_NAME: Kubernetes pod name
            NODE_NAME: Kubernetes node name
            NAMESPACE: Kubernetes namespace
            CONTAINER_ID: Container ID

        Returns:
            HoruxConfig instance populated from environment
        """
        config = cls()

        # Load from environment
        if url := os.getenv("HORUX_INGESTOR_URL"):
            config.ingestor_url = url

        if token := os.getenv("HORUX_API_TOKEN"):
            config.api_token = token

        if resource_id := os.getenv("HORUX_RESOURCE_ID"):
            config.resource_id = resource_id

        if gateway_url := os.getenv("HORUX_GATEWAY_URL"):
            config.gateway_url = gateway_url

        # Add Kubernetes metadata from environment
        if pod_name := os.getenv("POD_NAME"):
            config.instance_metadata["pod"] = pod_name

        if node_name := os.getenv("NODE_NAME"):
            config.instance_metadata["node"] = node_name

        if namespace := os.getenv("NAMESPACE"):
            config.instance_metadata["namespace"] = namespace

        if container_id := os.getenv("CONTAINER_ID"):
            config.instance_metadata["container_id"] = container_id

        return config

    def add_instance_metadata(self, key: str, value: Any) -> None:
        """
        Add custom instance metadata

        Args:
            key: Metadata key
            value: Metadata value
        """
        self.instance_metadata[key] = value

    def validate(self) -> None:
        """
        Validate configuration

        Raises:
            ValueError: If configuration is invalid
        """
        if not self.ingestor_url:
            raise ValueError("ingestor_url is required")

        if not self.api_token:
            raise ValueError("api_token is required")

        if not self.resource_id:
            raise ValueError("resource_id is required")

        if self.flush_interval <= 0:
            raise ValueError("flush_interval must be positive")

        if self.max_batch_size <= 0:
            raise ValueError("max_batch_size must be positive")

        if self.http_timeout <= 0:
            raise ValueError("http_timeout must be positive")

        if self.max_buffer_size < self.max_batch_size:
            raise ValueError("max_buffer_size must be at least max_batch_size")

        if self.drop_policy not in (DROP_OLDEST, DROP_NEWEST):
            raise ValueError(f'drop_policy must be "{DROP_OLDEST}" or "{DROP_NEWEST}"')

        if self.min_retry_backoff <= 0:
            raise ValueError("min_retry_backoff must be positive")

        if self.max_retry_backoff < self.min_retry_backoff:
            raise ValueError("max_retry_backoff must be at least min_retry_backoff")
