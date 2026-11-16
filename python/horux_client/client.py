"""
Unified Horux client SDK
"""

from __future__ import annotations

from .config import HoruxConfig
from .cron_client import CronClient
from .logs_client import LogsClient
from .metrics_client import MetricsClient


class HoruxClient:
    """
    Unified client for metrics, logs, and cron job check-ins

    Example:
        >>> from horux_client import HoruxClient, HoruxConfig
        >>>
        >>> config = HoruxConfig.from_env()
        >>> client = HoruxClient(config)
        >>>
        >>> # Send metrics
        >>> client.metrics.counter("requests_total", 1, {"method": "GET"})
        >>>
        >>> # Send logs
        >>> client.logs.info("Request processed", {"duration_ms": 45})
        >>>
        >>> # Cron job check-in (requires gateway_url in config)
        >>> if client.cron:
        ...     client.cron.check_in("my-check-in-key")
        >>>
        >>> # Close and flush
        >>> client.close()
    """

    def __init__(self, config: HoruxConfig):
        """
        Initialize unified client

        Args:
            config: Horux configuration

        Raises:
            ValueError: If configuration is invalid
        """
        config.validate()
        self.config = config
        self.metrics = MetricsClient(config)
        self.logs = LogsClient(config)
        self.cron: CronClient | None = None
        if config.gateway_url:
            self.cron = CronClient(config.gateway_url, config.http_timeout)

    def close(self) -> None:
        """Close all clients, flushing remaining data"""
        self.metrics.close()
        self.logs.close()
        if self.cron:
            self.cron.close()

    def __enter__(self):
        """Context manager entry"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()
        return False
