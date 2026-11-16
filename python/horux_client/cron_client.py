"""
Cron job check-in client for Horux SDK
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from .types import CheckInResponse

logger = logging.getLogger(__name__)


class CronClient:
    """Client for cron job check-ins via the Horux gateway"""

    def __init__(self, gateway_url: str, http_timeout: float = 30.0):
        """
        Initialize cron client

        Args:
            gateway_url: Base URL of the Horux gateway
            http_timeout: HTTP request timeout (seconds)
        """
        self._gateway_url = gateway_url.rstrip("/")
        self._http_timeout = http_timeout
        self._session = requests.Session()

    def check_in(
        self,
        check_in_key: str,
        metadata: dict[str, Any] | None = None,
    ) -> CheckInResponse:
        """
        Report a cron job check-in

        Args:
            check_in_key: The cron job's secret check-in key
            metadata: Optional metadata (e.g. duration_ms, exit_code, host)

        Returns:
            CheckInResponse with success status and next expected time

        Raises:
            requests.HTTPError: If the request fails
        """
        url = f"{self._gateway_url}/api/v1/cron-jobs/check-in/{check_in_key}"
        payload = None
        if metadata:
            payload = {"metadata": metadata}

        response = self._session.post(
            url,
            json=payload,
            timeout=self._http_timeout,
        )
        response.raise_for_status()
        data = response.json()
        return CheckInResponse(
            success=data["success"],
            message=data["message"],
            next_expected_at=data["next_expected_at"],
        )

    def close(self) -> None:
        """Close the HTTP session"""
        self._session.close()
