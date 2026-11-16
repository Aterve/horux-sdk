"""
Basic example of using the Horux Python client SDK
"""

import time

from horux_client import HistogramBucket, HoruxClient, HoruxConfig, SummaryQuantile


def main():
    # Create configuration (defaults to https://ingest.horux.io)
    # or provide explicit configuration
    config = HoruxConfig(
        # ingestor_url="http://localhost:3434",  # Override for local testing
        api_token="tenant_your-tenant-id_your-secret",
        resource_id="your-resource-uuid",
        flush_interval=5.0,  # Flush every 5 seconds
        max_batch_size=100,
    )

    # Add custom instance metadata
    config.add_instance_metadata("version", "1.0.0")
    config.add_instance_metadata("environment", "development")

    # Create client using context manager (recommended)
    with HoruxClient(config) as client:
        print("Sending metrics and logs to Horux...")

        # Example 1: Counter metrics
        for i in range(10):
            client.metrics.counter(
                "http_requests_total", 1, {"method": "GET", "path": "/api/users", "status": "200"}
            )
            time.sleep(0.1)

        # Example 2: Gauge metric
        client.metrics.gauge("memory_usage_bytes", 512 * 1024 * 1024, {"type": "heap"})

        # Example 3: Histogram metric
        buckets = [
            HistogramBucket(upper_bound=0.1, count=30),
            HistogramBucket(upper_bound=0.5, count=80),
            HistogramBucket(upper_bound=1.0, count=100),
        ]
        client.metrics.histogram(
            "http_request_duration_seconds",
            buckets=buckets,
            sum=42.5,
            count=100,
            labels={"method": "POST"},
        )

        # Example 4: Summary metric
        quantiles = [
            SummaryQuantile(quantile=0.5, value=0.25),
            SummaryQuantile(quantile=0.9, value=0.85),
            SummaryQuantile(quantile=0.99, value=2.1),
        ]
        client.metrics.summary(
            "request_duration_summary",
            quantiles=quantiles,
            sum=42.5,
            count=100,
            labels={"endpoint": "/api/users"},
        )

        # Example 5: Different log levels
        client.logs.trace("Detailed trace information", {"step": "initialization"})
        client.logs.debug("Debug information", {"variable": "value"})
        client.logs.info(
            "Request processed successfully",
            {"method": "GET", "path": "/api/users", "status_code": 200, "duration_ms": 45},
        )
        client.logs.warning("High memory usage detected", {"memory_mb": 512, "threshold_mb": 400})
        client.logs.error(
            "Database connection failed", {"error": "Connection timeout", "retry_count": 3}
        )

        # Example 6: Distributed tracing
        client.logs.info(
            "Request completed",
            fields={
                "method": "POST",
                "path": "/api/orders",
                "status_code": 201,
                "duration_ms": 123,
            },
            trace_id="trace-abc123",
            span_id="span-def456",
            request_id="req-789",
        )

        print("Waiting for final flush...")
        time.sleep(1)

    print("Client closed, all data flushed!")


if __name__ == "__main__":
    main()
