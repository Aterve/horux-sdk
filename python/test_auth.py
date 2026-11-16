#!/usr/bin/env python3
"""
Test script for Horux data-ingestor authentication and ingestion

This script tests:
1. Invalid token (401 Unauthorized)
2. Token without permissions (403 Forbidden)
3. Valid token with metrics.ingest permission
4. Valid token with logs.ingest permission
5. Complete metrics and logs ingestion

Prerequisites:
- Data-ingestor service running on http://localhost:3434
- Backend Nexus service with database
- API token created with appropriate permissions
"""

import os
import sys
import time

import requests

from horux_client import HistogramBucket, HoruxClient, HoruxConfig, SummaryQuantile

# ANSI color codes for output
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"


def print_test(message):
    print(f"\n{BLUE}[TEST]{RESET} {message}")


def print_success(message):
    print(f"{GREEN}✓{RESET} {message}")


def print_error(message):
    print(f"{RED}✗{RESET} {message}")


def print_warning(message):
    print(f"{YELLOW}⚠{RESET} {message}")


def test_invalid_token(ingestor_url, resource_id):
    """Test 1: Invalid token should return 401"""
    print_test("Testing invalid token (should get 401 Unauthorized)")

    config = HoruxConfig(
        ingestor_url=ingestor_url,
        api_token="horux_invalid_token_12345",
        resource_id=resource_id,
        flush_interval=1.0,
    )

    try:
        with HoruxClient(config) as client:
            # Try to send a metric
            client.metrics.counter("test_metric", 1)
            time.sleep(1.5)  # Wait for flush
        print_error("Expected 401 Unauthorized but request succeeded")
        return False
    except Exception as e:
        error_msg = str(e).lower()
        if "401" in error_msg or "unauthorized" in error_msg:
            print_success(f"Got expected 401 Unauthorized: {e}")
            return True
        else:
            print_error(f"Got unexpected error: {e}")
            return False


def test_token_without_permission(ingestor_url, resource_id, viewer_token):
    """Test 2: Token without ingestion permissions should return 403"""
    print_test("Testing token without ingestion permissions (should get 403 Forbidden)")

    if not viewer_token:
        print_warning("Skipping: No viewer token provided")
        return None

    config = HoruxConfig(
        ingestor_url=ingestor_url,
        api_token=viewer_token,
        resource_id=resource_id,
        flush_interval=1.0,
    )

    try:
        with HoruxClient(config) as client:
            # Try to send a metric (viewer role has no ingest permissions)
            client.metrics.counter("test_metric", 1)
            time.sleep(1.5)  # Wait for flush
        print_error("Expected 403 Forbidden but request succeeded")
        return False
    except Exception as e:
        error_msg = str(e).lower()
        if "403" in error_msg or "forbidden" in error_msg or "permission" in error_msg:
            print_success(f"Got expected 403 Forbidden: {e}")
            return True
        else:
            print_error(f"Got unexpected error: {e}")
            return False


def test_metrics_ingestion(ingestor_url, resource_id, valid_token):
    """Test 3: Valid token with metrics.ingest permission"""
    print_test("Testing metrics ingestion with valid token")

    config = HoruxConfig(
        ingestor_url=ingestor_url,
        api_token=valid_token,
        resource_id=resource_id,
        flush_interval=2.0,
        max_batch_size=100,
    )

    config.add_instance_metadata("test_run", "auth_test")
    config.add_instance_metadata("hostname", "test-host-01")

    try:
        with HoruxClient(config) as client:
            print("  → Sending counter metrics...")
            for i in range(5):
                client.metrics.counter(
                    "http_requests_total",
                    1,
                    {"method": "GET", "path": "/api/test", "status": "200"},
                )

            print("  → Sending gauge metric...")
            client.metrics.gauge("memory_usage_bytes", 256 * 1024 * 1024, {"type": "heap"})

            print("  → Sending histogram metric...")
            buckets = [
                HistogramBucket(upper_bound=0.1, count=10),
                HistogramBucket(upper_bound=0.5, count=25),
                HistogramBucket(upper_bound=1.0, count=30),
            ]
            client.metrics.histogram(
                "request_duration_seconds",
                buckets=buckets,
                sum=15.5,
                count=30,
                labels={"endpoint": "/api/test"},
            )

            print("  → Sending summary metric...")
            quantiles = [
                SummaryQuantile(quantile=0.5, value=0.15),
                SummaryQuantile(quantile=0.9, value=0.45),
                SummaryQuantile(quantile=0.99, value=0.95),
            ]
            client.metrics.summary(
                "response_time_summary",
                quantiles=quantiles,
                sum=15.5,
                count=30,
                labels={"service": "api"},
            )

            print("  → Waiting for flush...")
            time.sleep(2.5)

        print_success("Metrics ingestion successful")
        return True

    except Exception as e:
        print_error(f"Metrics ingestion failed: {e}")
        return False


def test_logs_ingestion(ingestor_url, resource_id, valid_token):
    """Test 4: Valid token with logs.ingest permission"""
    print_test("Testing logs ingestion with valid token")

    config = HoruxConfig(
        ingestor_url=ingestor_url,
        api_token=valid_token,
        resource_id=resource_id,
        flush_interval=2.0,
        max_batch_size=50,
    )

    config.add_instance_metadata("test_run", "auth_test")

    try:
        with HoruxClient(config) as client:
            print("  → Sending different log levels...")

            client.logs.trace("Test trace log", {"detail": "trace"})
            client.logs.debug("Test debug log", {"detail": "debug"})
            client.logs.info(
                "Test info log", {"method": "GET", "path": "/api/test", "status_code": 200}
            )
            client.logs.warning("Test warning log", {"threshold_exceeded": True})
            client.logs.error(
                "Test error log", {"error": "Test error", "stack_trace": "at test_logs_ingestion"}
            )

            print("  → Sending log with distributed tracing...")
            client.logs.info(
                "Request completed",
                fields={
                    "method": "POST",
                    "path": "/api/orders",
                    "status_code": 201,
                    "duration_ms": 123,
                },
                trace_id="test-trace-abc123",
                span_id="test-span-def456",
                request_id="test-req-789",
            )

            print("  → Waiting for flush...")
            time.sleep(2.5)

        print_success("Logs ingestion successful")
        return True

    except Exception as e:
        print_error(f"Logs ingestion failed: {e}")
        return False


def check_ingestor_health(ingestor_url):
    """Check if data-ingestor is running"""
    print_test(f"Checking data-ingestor health at {ingestor_url}")

    try:
        response = requests.get(f"{ingestor_url}/health", timeout=5)
        if response.status_code == 200:
            print_success("Data-ingestor is healthy")
            return True
        else:
            print_error(f"Data-ingestor returned status {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print_error("Cannot connect to data-ingestor. Is it running?")
        return False
    except Exception as e:
        print_error(f"Health check failed: {e}")
        return False


def main():
    print(f"{BLUE}{'='*60}{RESET}")
    print(f"{BLUE}Horux Data-Ingestor Authentication Test Suite{RESET}")
    print(f"{BLUE}{'='*60}{RESET}")

    # Configuration.
    #
    # 3434 is the port the ingestor actually listens on in the dev stack
    # (docker-compose.dev.yml); 8080 is only the SERVER_PORT fallback in
    # internal/config, which compose always overrides. Pointing at 8080 fails
    # with connection refused and reads like an auth problem, which is a poor
    # first experience for a script whose job is diagnosing auth.
    ingestor_url = os.environ.get("HORUX_INGESTOR_URL", "http://localhost:3434")

    # No default: the previous one was a real resource UUID from someone's
    # tenant, and a wrong-but-plausible id makes every push fail resource
    # lookup rather than exercising the token.
    resource_id = os.environ.get("HORUX_RESOURCE_ID", "")
    if not resource_id:
        print_warning("No resource id: set HORUX_RESOURCE_ID to a resource in your tenant")
        return 1

    # Get API token from command line or use default
    if len(sys.argv) > 1:
        valid_token = sys.argv[1]
    else:
        print_warning("No API token provided via command line")
        print(f"{YELLOW}Usage: {sys.argv[0]} <api_token> [viewer_token]{RESET}")
        print(f"{YELLOW}Using default token format for testing{RESET}")
        valid_token = "horux_example_token_with_permissions"

    viewer_token = sys.argv[2] if len(sys.argv) > 2 else None

    print("\nConfiguration:")
    print(f"  Ingestor URL: {ingestor_url}")
    print(f"  Resource ID: {resource_id}")
    print(
        f"  API Token: {valid_token[:20]}..."
        if len(valid_token) > 20
        else f"  API Token: {valid_token}"
    )
    if viewer_token:
        print(f"  Viewer Token: {viewer_token[:20]}...")

    # Run tests
    results = {}

    # Pre-flight check
    if not check_ingestor_health(ingestor_url):
        print_error("\nCannot proceed: Data-ingestor is not running")
        print(f"{YELLOW}Start the data-ingestor service and try again:{RESET}")
        print("  cd services/data-ingestor")
        print("  go run cmd/ingestor/main.go")
        sys.exit(1)

    # Test 1: Invalid token
    results["invalid_token"] = test_invalid_token(ingestor_url, resource_id)

    # Test 2: Token without permissions (optional)
    if viewer_token:
        results["no_permission"] = test_token_without_permission(
            ingestor_url, resource_id, viewer_token
        )

    # Test 3: Metrics ingestion
    results["metrics"] = test_metrics_ingestion(ingestor_url, resource_id, valid_token)

    # Test 4: Logs ingestion
    results["logs"] = test_logs_ingestion(ingestor_url, resource_id, valid_token)

    # Summary
    print(f"\n{BLUE}{'='*60}{RESET}")
    print(f"{BLUE}Test Results Summary{RESET}")
    print(f"{BLUE}{'='*60}{RESET}\n")

    passed = sum(1 for v in results.values() if v is True)
    failed = sum(1 for v in results.values() if v is False)
    skipped = sum(1 for v in results.values() if v is None)
    total = len(results)

    for test_name, result in results.items():
        if result is True:
            print_success(f"{test_name}: PASSED")
        elif result is False:
            print_error(f"{test_name}: FAILED")
        else:
            print_warning(f"{test_name}: SKIPPED")

    print(
        f"\n{BLUE}Total:{RESET} {total} | {GREEN}Passed:{RESET} {passed} | {RED}Failed:{RESET} {failed} | {YELLOW}Skipped:{RESET} {skipped}"
    )

    if failed > 0:
        sys.exit(1)
    else:
        print(f"\n{GREEN}All tests passed!{RESET}")
        sys.exit(0)


if __name__ == "__main__":
    main()
