#!/usr/bin/env python3
"""
Simple script to generate and ingest random metrics and logs

Usage:
    python random_ingestion.py <api_token>
    python random_ingestion.py <api_token> --metrics-only
    python random_ingestion.py <api_token> --logs-only
"""

import argparse
import os
import random
import sys
import time
from datetime import datetime

from horux_client import HistogramBucket, HoruxClient, HoruxConfig, SummaryQuantile

# Sample data for generating realistic logs
LOG_MESSAGES = [
    "Request processed successfully",
    "Database query executed",
    "Cache hit for key",
    "User authentication successful",
    "File uploaded to storage",
    "Background job completed",
    "API rate limit check passed",
    "Session created",
    "Email sent successfully",
    "Payment processed",
    "Order created",
    "Inventory updated",
    "Search query executed",
    "Configuration reloaded",
    "Health check passed",
    "WebSocket connection established",
    "Message published to queue",
    "Image processed and resized",
    "Report generated successfully",
    "Backup completed",
    "User logged out",
    "Password reset email sent",
    "Two-factor authentication verified",
    "API key validated",
    "Webhook delivered successfully",
]

LOG_ERRORS = [
    "Connection timeout to external service",
    "Failed to parse JSON payload",
    "Database connection lost",
    "Invalid user credentials",
    "File not found",
    "Permission denied",
    "Rate limit exceeded",
    "Timeout waiting for response",
    "Invalid configuration",
    "Out of memory",
    "Deadlock detected in transaction",
    "S3 bucket access denied",
    "Redis connection refused",
    "RabbitMQ channel closed unexpectedly",
    "Invalid JWT token signature",
    "Circuit breaker opened",
    "Upstream service unavailable",
    "Database query timeout",
    "SSL certificate validation failed",
    "Insufficient disk space",
]

LOG_WARNINGS = [
    "Slow database query detected",
    "Cache miss rate high",
    "Connection pool near capacity",
    "Disk usage above 80%",
    "Memory usage approaching limit",
    "API response time degraded",
    "Queue depth increasing",
    "Worker thread pool exhausted",
    "Deprecated API endpoint used",
    "Session about to expire",
    "Backup taking longer than expected",
    "Retry attempt initiated",
    "Falling back to default configuration",
    "Database replication lag detected",
]

HTTP_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]
HTTP_PATHS = [
    "/api/users",
    "/api/orders",
    "/api/products",
    "/api/auth",
    "/api/search",
    "/api/payments",
    "/api/inventory",
    "/api/reports",
    "/api/notifications",
    "/api/webhooks",
    "/api/analytics",
    "/api/settings",
    "/api/admin/users",
    "/api/v2/orders",
    "/api/health",
    "/api/metrics",
    "/graphql",
]
HTTP_STATUSES = [
    "200",
    "201",
    "204",
    "400",
    "401",
    "403",
    "404",
    "409",
    "422",
    "429",
    "500",
    "502",
    "503",
    "504",
]

# Additional metric components
SERVICES = ["api", "worker", "scheduler", "notifier", "processor", "gateway"]
COMPONENTS = ["database", "redis", "rabbitmq", "s3", "elasticsearch", "memcached"]
OPERATIONS = ["read", "write", "delete", "update", "list", "create"]
DATABASES = ["postgresql", "mysql", "mongodb", "redis"]
REGIONS = ["us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1"]
ENVIRONMENTS = ["production", "staging", "development"]


def generate_random_metrics(client: HoruxClient) -> int:
    """Generate and send random metrics. Returns count of metrics generated."""

    metrics_count = 0

    # Counter: HTTP requests
    method = random.choice(HTTP_METHODS)
    path = random.choice(HTTP_PATHS)
    status = random.choice(HTTP_STATUSES)
    client.metrics.counter(
        "http_requests_total",
        1,
        {
            "method": method,
            "path": path,
            "status": status,
            "service": random.choice(SERVICES),
            "region": random.choice(REGIONS),
        },
    )
    metrics_count += 1

    # Counter: Business events
    event_type = random.choice(
        ["order.created", "user.registered", "payment.processed", "email.sent", "job.completed"]
    )
    client.metrics.counter(
        "business_events_total",
        1,
        {"event_type": event_type, "environment": random.choice(ENVIRONMENTS)},
    )
    metrics_count += 1

    # Counter: Cache operations
    cache_op = random.choice(["hit", "miss", "set", "delete"])
    client.metrics.counter(
        "cache_operations_total",
        1,
        {"operation": cache_op, "cache_type": random.choice(["redis", "memcached", "local"])},
    )
    metrics_count += 1

    # Counter: Database queries
    db_operation = random.choice(OPERATIONS)
    client.metrics.counter(
        "database_queries_total",
        1,
        {
            "operation": db_operation,
            "database": random.choice(DATABASES),
            "table": random.choice(["users", "orders", "products", "sessions", "logs"]),
        },
    )
    metrics_count += 1

    # Gauge: Memory usage
    memory_mb = random.randint(100, 2048)
    client.metrics.gauge(
        "memory_usage_bytes",
        memory_mb * 1024 * 1024,
        {
            "type": random.choice(["heap", "stack", "cache", "rss"]),
            "service": random.choice(SERVICES),
        },
    )
    metrics_count += 1

    # Gauge: CPU usage
    cpu_percent = random.uniform(10, 95)
    client.metrics.gauge(
        "cpu_usage_percent",
        cpu_percent,
        {"core": str(random.randint(0, 7)), "service": random.choice(SERVICES)},
    )
    metrics_count += 1

    # Gauge: Active connections
    connections = random.randint(10, 500)
    client.metrics.gauge(
        "active_connections",
        connections,
        {"pool": random.choice(COMPONENTS), "region": random.choice(REGIONS)},
    )
    metrics_count += 1

    # Gauge: Queue depth
    queue_depth = random.randint(0, 1000)
    client.metrics.gauge(
        "queue_depth",
        queue_depth,
        {
            "queue": random.choice(["orders", "notifications", "emails", "webhooks", "analytics"]),
            "priority": random.choice(["high", "normal", "low"]),
        },
    )
    metrics_count += 1

    # Gauge: Disk usage
    disk_usage_gb = random.uniform(10, 500)
    client.metrics.gauge(
        "disk_usage_gigabytes",
        disk_usage_gb,
        {
            "mount": random.choice(["/", "/data", "/logs", "/var"]),
            "filesystem": random.choice(["ext4", "xfs", "btrfs"]),
        },
    )
    metrics_count += 1

    # Gauge: Worker threads
    threads = random.randint(1, 50)
    client.metrics.gauge(
        "worker_threads_active",
        threads,
        {
            "pool": random.choice(["request", "background", "scheduled"]),
            "service": random.choice(SERVICES),
        },
    )
    metrics_count += 1

    # Histogram: Request duration
    if random.random() > 0.6:  # Send histogram 40% of the time
        buckets = [
            HistogramBucket(upper_bound=0.01, count=random.randint(10, 50)),
            HistogramBucket(upper_bound=0.05, count=random.randint(50, 150)),
            HistogramBucket(upper_bound=0.1, count=random.randint(150, 300)),
            HistogramBucket(upper_bound=0.5, count=random.randint(300, 500)),
            HistogramBucket(upper_bound=1.0, count=random.randint(500, 800)),
            HistogramBucket(upper_bound=5.0, count=random.randint(800, 1000)),
        ]
        total_count = buckets[-1].count
        avg_duration = random.uniform(0.05, 2.0)
        client.metrics.histogram(
            "request_duration_seconds",
            buckets=buckets,
            sum=avg_duration * total_count,
            count=total_count,
            labels={
                "endpoint": random.choice(HTTP_PATHS),
                "method": random.choice(HTTP_METHODS),
                "service": random.choice(SERVICES),
            },
        )
        metrics_count += 1

    # Histogram: Database query duration
    if random.random() > 0.7:  # Send histogram 30% of the time
        buckets = [
            HistogramBucket(upper_bound=0.001, count=random.randint(100, 200)),
            HistogramBucket(upper_bound=0.005, count=random.randint(200, 400)),
            HistogramBucket(upper_bound=0.01, count=random.randint(400, 700)),
            HistogramBucket(upper_bound=0.05, count=random.randint(700, 900)),
            HistogramBucket(upper_bound=0.1, count=random.randint(900, 1000)),
        ]
        total_count = buckets[-1].count
        avg_duration = random.uniform(0.001, 0.05)
        client.metrics.histogram(
            "database_query_duration_seconds",
            buckets=buckets,
            sum=avg_duration * total_count,
            count=total_count,
            labels={"operation": random.choice(OPERATIONS), "database": random.choice(DATABASES)},
        )
        metrics_count += 1

    # Summary: Response time percentiles
    if random.random() > 0.75:  # Send summary 25% of the time
        quantiles = [
            SummaryQuantile(quantile=0.5, value=random.uniform(0.05, 0.2)),
            SummaryQuantile(quantile=0.9, value=random.uniform(0.2, 0.5)),
            SummaryQuantile(quantile=0.95, value=random.uniform(0.5, 1.0)),
            SummaryQuantile(quantile=0.99, value=random.uniform(1.0, 2.5)),
        ]
        count = random.randint(100, 1000)
        avg = random.uniform(0.1, 0.5)
        client.metrics.summary(
            "response_time_summary",
            quantiles=quantiles,
            sum=avg * count,
            count=count,
            labels={"service": random.choice(SERVICES), "region": random.choice(REGIONS)},
        )
        metrics_count += 1

    # Summary: Payload size
    if random.random() > 0.8:  # Send summary 20% of the time
        quantiles = [
            SummaryQuantile(quantile=0.5, value=random.uniform(100, 1000)),
            SummaryQuantile(quantile=0.9, value=random.uniform(1000, 10000)),
            SummaryQuantile(quantile=0.99, value=random.uniform(10000, 100000)),
        ]
        count = random.randint(500, 5000)
        avg = random.uniform(500, 5000)
        client.metrics.summary(
            "payload_size_bytes",
            quantiles=quantiles,
            sum=avg * count,
            count=count,
            labels={
                "direction": random.choice(["request", "response"]),
                "content_type": random.choice(["json", "xml", "binary", "text"]),
            },
        )
        metrics_count += 1

    return metrics_count


def generate_random_logs(client: HoruxClient) -> int:
    """Generate and send random logs. Returns count of logs generated."""

    logs_count = 0

    # Random log level distribution (more info, fewer errors)
    level_choice = random.random()

    if level_choice < 0.05:  # 5% trace
        functions = [
            "processOrder",
            "validateUser",
            "fetchData",
            "calculateTotal",
            "authenticateRequest",
            "loadConfiguration",
            "initializeCache",
            "establishConnection",
            "parsePayload",
            "serializeResponse",
        ]
        message = f"Entering function {random.choice(functions)}"
        client.logs.trace(
            message,
            {
                "function": random.choice(functions),
                "module": random.choice(SERVICES),
                "line": random.randint(10, 500),
            },
        )
        logs_count += 1

    elif level_choice < 0.15:  # 10% debug
        debug_messages = [
            f"Variable value: {random.randint(1, 1000)}",
            f"Cache lookup for key: user_{random.randint(1000, 9999)}",
            f"SQL query prepared: SELECT * FROM {random.choice(['users', 'orders', 'products'])}",
            f"Parsed {random.randint(1, 100)} records from response",
            f"Connection pool size: {random.randint(5, 50)}",
            f"Lock acquired for resource: {random.choice(['user_session', 'inventory_item', 'payment_queue'])}",
        ]
        message = random.choice(debug_messages)
        client.logs.debug(
            message,
            {
                "component": random.choice(SERVICES),
                "thread_id": random.randint(1, 100),
                "correlation_id": f"corr-{random.randint(10000, 99999)}",
            },
        )
        logs_count += 1

    elif level_choice < 0.70:  # 55% info
        message = random.choice(LOG_MESSAGES)
        method = random.choice(HTTP_METHODS)
        path = random.choice(HTTP_PATHS)
        status = random.choice(["200", "201", "204"])

        fields = {
            "method": method,
            "path": path,
            "status_code": int(status),
            "duration_ms": random.randint(10, 500),
            "service": random.choice(SERVICES),
            "region": random.choice(REGIONS),
        }

        # Add additional contextual fields sometimes
        if random.random() > 0.5:
            fields.update(
                {
                    "user_id": f"user_{random.randint(1000, 9999)}",
                    "ip_address": f"192.168.{random.randint(1, 255)}.{random.randint(1, 255)}",
                }
            )

        if random.random() > 0.7:
            fields.update(
                {
                    "bytes_sent": random.randint(100, 100000),
                    "bytes_received": random.randint(100, 50000),
                }
            )

        client.logs.info(message, fields)
        logs_count += 1

    elif level_choice < 0.90:  # 20% warn
        message = random.choice(LOG_WARNINGS)

        warn_fields = {
            "component": random.choice(COMPONENTS),
            "service": random.choice(SERVICES),
            "threshold": random.randint(80, 95),
            "current": random.randint(85, 100),
        }

        # Add specific warning context
        if "query" in message.lower():
            warn_fields.update(
                {
                    "query_duration_ms": random.randint(1000, 5000),
                    "query_type": random.choice(OPERATIONS),
                    "table": random.choice(["users", "orders", "products", "logs"]),
                }
            )
        elif "cache" in message.lower():
            warn_fields.update(
                {
                    "cache_hit_rate": random.uniform(0.5, 0.7),
                    "cache_size_mb": random.randint(100, 1000),
                }
            )
        elif "pool" in message.lower():
            warn_fields.update(
                {"pool_size": random.randint(20, 50), "active_connections": random.randint(18, 48)}
            )

        client.logs.warning(message, warn_fields)
        logs_count += 1

    else:  # 10% error
        message = random.choice(LOG_ERRORS)

        error_fields = {
            "error": message,
            "error_code": f"ERR_{random.randint(1000, 9999)}",
            "retry_count": random.randint(0, 3),
            "service": random.choice(SERVICES),
            "component": random.choice(COMPONENTS),
        }

        # Add error-specific context
        if "timeout" in message.lower():
            error_fields.update(
                {"timeout_ms": random.randint(5000, 30000), "endpoint": random.choice(HTTP_PATHS)}
            )
        elif "connection" in message.lower():
            error_fields.update(
                {
                    "host": f"{random.choice(COMPONENTS)}.{random.choice(REGIONS)}.internal",
                    "port": random.choice([5432, 6379, 5672, 9200]),
                }
            )
        elif "database" in message.lower():
            error_fields.update(
                {"database": random.choice(DATABASES), "query": "SELECT ...", "affected_rows": 0}
            )

        # Add stack trace for some errors
        if random.random() > 0.6:
            error_fields["stack_trace"] = (
                f"at {random.choice(SERVICES)}.handler.line_{random.randint(50, 500)}"
            )

        client.logs.error(message, error_fields)
        logs_count += 1

    # Occasionally add distributed tracing fields (30% of the time)
    if random.random() > 0.7:
        trace_id = f"trace-{random.randint(100000, 999999)}"
        span_id = f"span-{random.randint(10000, 99999)}"
        request_id = f"req-{random.randint(10000, 99999)}"

        client.logs.info(
            "Request completed with tracing",
            fields={
                "method": random.choice(HTTP_METHODS),
                "path": random.choice(HTTP_PATHS),
                "status_code": int(random.choice(["200", "201", "204"])),
                "duration_ms": random.randint(50, 500),
                "service": random.choice(SERVICES),
                "upstream_service": random.choice(SERVICES),
                "cache_hit": random.choice([True, False]),
            },
            trace_id=trace_id,
            span_id=span_id,
            request_id=request_id,
        )
        logs_count += 1

    # Business event logs (20% of the time)
    if random.random() > 0.8:
        business_events = [
            (
                "User registered",
                {
                    "user_id": f"user_{random.randint(1000, 9999)}",
                    "email": f"user{random.randint(1, 999)}@example.com",
                    "source": random.choice(["web", "mobile", "api"]),
                },
            ),
            (
                "Order placed",
                {
                    "order_id": f"ord_{random.randint(10000, 99999)}",
                    "amount": random.uniform(10, 1000),
                    "currency": "USD",
                    "items": random.randint(1, 10),
                },
            ),
            (
                "Payment processed",
                {
                    "payment_id": f"pay_{random.randint(10000, 99999)}",
                    "method": random.choice(["card", "paypal", "bank_transfer"]),
                    "status": "success",
                },
            ),
            (
                "Email sent",
                {
                    "to": f"user{random.randint(1, 999)}@example.com",
                    "template": random.choice(["welcome", "reset_password", "order_confirmation"]),
                    "provider": "sendgrid",
                },
            ),
            (
                "Export completed",
                {
                    "format": random.choice(["csv", "pdf", "xlsx"]),
                    "rows": random.randint(100, 100000),
                    "size_mb": random.uniform(1, 100),
                },
            ),
            (
                "File uploaded",
                {
                    "file_id": f"file_{random.randint(10000, 99999)}",
                    "size_bytes": random.randint(1024, 10485760),
                    "mime_type": random.choice(["image/jpeg", "application/pdf", "text/csv"]),
                },
            ),
            (
                "Notification sent",
                {
                    "notification_id": f"notif_{random.randint(10000, 99999)}",
                    "channel": random.choice(["push", "email", "sms"]),
                    "recipient_count": random.randint(1, 1000),
                },
            ),
            (
                "Report generated",
                {
                    "report_id": f"rep_{random.randint(10000, 99999)}",
                    "type": random.choice(["daily", "weekly", "monthly"]),
                    "duration_seconds": random.randint(5, 300),
                },
            ),
            (
                "Backup created",
                {
                    "backup_id": f"bak_{random.randint(10000, 99999)}",
                    "type": random.choice(["full", "incremental"]),
                    "size_gb": random.uniform(1, 500),
                },
            ),
            (
                "Webhook delivered",
                {
                    "webhook_id": f"hook_{random.randint(10000, 99999)}",
                    "url": f"https://example.com/webhook/{random.randint(1, 100)}",
                    "status_code": 200,
                },
            ),
        ]
        event, fields = random.choice(business_events)
        fields["environment"] = random.choice(ENVIRONMENTS)
        fields["region"] = random.choice(REGIONS)
        client.logs.info(event, fields)
        logs_count += 1

    # Database operation logs (15% of the time)
    if random.random() > 0.85:
        db_operations = [
            (
                "Database transaction committed",
                {
                    "transaction_id": f"tx_{random.randint(100000, 999999)}",
                    "duration_ms": random.randint(1, 100),
                    "operations": random.randint(1, 50),
                    "database": random.choice(DATABASES),
                },
            ),
            (
                "Database query executed",
                {
                    "query_id": f"q_{random.randint(100000, 999999)}",
                    "duration_ms": random.randint(5, 5000),
                    "rows_affected": random.randint(0, 10000),
                    "operation": random.choice(OPERATIONS),
                    "table": random.choice(
                        ["users", "orders", "products", "inventory", "sessions"]
                    ),
                },
            ),
            (
                "Database connection pool exhausted",
                {
                    "pool_size": 50,
                    "active_connections": 50,
                    "waiting_requests": random.randint(1, 20),
                    "database": random.choice(DATABASES),
                },
            ),
            (
                "Index rebuild completed",
                {
                    "index_name": f"idx_{random.choice(['users', 'orders', 'products'])}_{random.choice(['id', 'created_at', 'status'])}",
                    "duration_minutes": random.randint(1, 60),
                    "table": random.choice(["users", "orders", "products"]),
                },
            ),
        ]
        event, fields = random.choice(db_operations)
        fields["service"] = random.choice(SERVICES)
        client.logs.info(event, fields)
        logs_count += 1

    # Cache operation logs (15% of the time)
    if random.random() > 0.85:
        cache_operations = [
            (
                "Cache key expired",
                {
                    "key": f"session:{random.randint(10000, 99999)}",
                    "ttl_seconds": random.randint(60, 3600),
                    "cache_type": random.choice(["redis", "memcached"]),
                },
            ),
            (
                "Cache eviction triggered",
                {
                    "evicted_keys": random.randint(10, 1000),
                    "reason": random.choice(["memory_limit", "lru_policy", "ttl_expired"]),
                    "cache_type": random.choice(["redis", "memcached"]),
                },
            ),
            (
                "Cache warmed up",
                {
                    "keys_loaded": random.randint(1000, 100000),
                    "duration_seconds": random.randint(5, 300),
                    "hit_rate_before": random.uniform(0.6, 0.8),
                    "hit_rate_after": random.uniform(0.85, 0.95),
                },
            ),
            (
                "Cache invalidated",
                {
                    "pattern": f"user:*:{random.randint(1000, 9999)}",
                    "keys_deleted": random.randint(1, 100),
                    "reason": random.choice(["manual", "update", "consistency"]),
                },
            ),
        ]
        event, fields = random.choice(cache_operations)
        fields["service"] = random.choice(SERVICES)
        client.logs.info(event, fields)
        logs_count += 1

    # Queue/Message broker logs (15% of the time)
    if random.random() > 0.85:
        queue_operations = [
            (
                "Message published to queue",
                {
                    "queue_name": random.choice(
                        ["orders", "emails", "notifications", "webhooks", "analytics"]
                    ),
                    "message_id": f"msg_{random.randint(100000, 999999)}",
                    "size_bytes": random.randint(100, 50000),
                    "priority": random.choice(["high", "normal", "low"]),
                },
            ),
            (
                "Message consumed from queue",
                {
                    "queue_name": random.choice(
                        ["orders", "emails", "notifications", "webhooks", "analytics"]
                    ),
                    "message_id": f"msg_{random.randint(100000, 999999)}",
                    "processing_time_ms": random.randint(10, 5000),
                    "worker_id": f"worker-{random.randint(1, 10)}",
                },
            ),
            (
                "Dead letter queue threshold reached",
                {
                    "queue_name": random.choice(["orders", "emails", "notifications"]),
                    "failed_messages": random.randint(10, 100),
                    "threshold": 50,
                    "action": "alert_sent",
                },
            ),
            (
                "Queue consumer reconnected",
                {
                    "queue_name": random.choice(["orders", "emails", "notifications"]),
                    "consumer_id": f"consumer-{random.randint(1, 20)}",
                    "downtime_seconds": random.randint(1, 60),
                },
            ),
        ]
        event, fields = random.choice(queue_operations)
        fields["service"] = random.choice(SERVICES)
        client.logs.info(event, fields)
        logs_count += 1

    # Security/Auth logs (10% of the time)
    if random.random() > 0.9:
        security_events = [
            (
                "User login successful",
                {
                    "user_id": f"user_{random.randint(1000, 9999)}",
                    "ip_address": f"{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}",
                    "user_agent": random.choice(["Chrome/91.0", "Firefox/89.0", "Safari/14.1"]),
                    "method": random.choice(["password", "oauth", "saml", "2fa"]),
                },
            ),
            (
                "Failed login attempt",
                {
                    "username": f"user{random.randint(1, 999)}@example.com",
                    "ip_address": f"{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}",
                    "reason": random.choice(
                        ["invalid_password", "user_not_found", "account_locked"]
                    ),
                    "attempt_count": random.randint(1, 5),
                },
            ),
            (
                "API key rotated",
                {
                    "key_id": f"key_{random.randint(10000, 99999)}",
                    "user_id": f"user_{random.randint(1000, 9999)}",
                    "old_key_revoked": True,
                },
            ),
            (
                "Session expired",
                {
                    "session_id": f"sess_{random.randint(100000, 999999)}",
                    "user_id": f"user_{random.randint(1000, 9999)}",
                    "duration_minutes": random.randint(15, 480),
                },
            ),
            (
                "Suspicious activity detected",
                {
                    "user_id": f"user_{random.randint(1000, 9999)}",
                    "activity_type": random.choice(
                        ["unusual_location", "rate_limit_exceeded", "multiple_failed_logins"]
                    ),
                    "risk_score": random.uniform(0.7, 1.0),
                    "action": random.choice(["alert", "block", "require_2fa"]),
                },
            ),
        ]
        event, fields = random.choice(security_events)
        fields["region"] = random.choice(REGIONS)
        client.logs.info(event, fields)
        logs_count += 1

    # System/Infrastructure logs (10% of the time)
    if random.random() > 0.9:
        system_events = [
            (
                "Service health check passed",
                {
                    "service": random.choice(SERVICES),
                    "endpoint": "/health",
                    "response_time_ms": random.randint(1, 50),
                    "status": "healthy",
                },
            ),
            (
                "Container restarted",
                {
                    "container_id": f"cont_{random.randint(100000, 999999)}",
                    "service": random.choice(SERVICES),
                    "restart_count": random.randint(1, 5),
                    "reason": random.choice(["oom_killed", "crash", "deployment", "manual"]),
                },
            ),
            (
                "Auto-scaling triggered",
                {
                    "service": random.choice(SERVICES),
                    "current_instances": random.randint(2, 10),
                    "target_instances": random.randint(5, 20),
                    "reason": random.choice(["cpu_high", "memory_high", "request_rate_high"]),
                    "metric_value": random.uniform(80, 95),
                },
            ),
            (
                "Certificate renewal completed",
                {
                    "domain": f"{random.choice(['api', 'www', 'admin'])}.example.com",
                    "valid_until": "2025-12-31",
                    "provider": random.choice(["letsencrypt", "digicert"]),
                },
            ),
            (
                "Deployment completed",
                {
                    "service": random.choice(SERVICES),
                    "version": f"v{random.randint(1, 10)}.{random.randint(0, 50)}.{random.randint(0, 100)}",
                    "duration_seconds": random.randint(30, 600),
                    "strategy": random.choice(["rolling", "blue_green", "canary"]),
                },
            ),
        ]
        event, fields = random.choice(system_events)
        fields["environment"] = random.choice(ENVIRONMENTS)
        fields["region"] = random.choice(REGIONS)
        client.logs.info(event, fields)
        logs_count += 1

    return logs_count


def main():
    parser = argparse.ArgumentParser(description="Generate and ingest random metrics and logs")
    parser.add_argument("api_token", help="API token for authentication")
    parser.add_argument(
        "--ingestor-url",
        default="http://localhost:3434",
        help="Data-ingestor URL (default: http://localhost:3434)",
    )
    # No default resource id: the previous one was a real UUID out of someone's
    # tenant, and running against a different environment with it silently
    # pushes nothing that resolves.
    parser.add_argument(
        "--resource-id",
        default=os.environ.get("HORUX_RESOURCE_ID"),
        required=not os.environ.get("HORUX_RESOURCE_ID"),
        help="Resource ID to push under (or set HORUX_RESOURCE_ID)",
    )
    parser.add_argument("--metrics-only", action="store_true", help="Only generate metrics")
    parser.add_argument("--logs-only", action="store_true", help="Only generate logs")
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Interval between generations in seconds (default: 1.0)",
    )
    parser.add_argument(
        "--count", type=int, default=0, help="Number of iterations (0 = infinite, default: 0)"
    )

    args = parser.parse_args()

    # Validate that both flags aren't set
    if args.metrics_only and args.logs_only:
        print("Error: Cannot use both --metrics-only and --logs-only")
        sys.exit(1)

    print("=" * 60)
    print("Random Metrics & Logs Generator")
    print("=" * 60)
    print("\nConfiguration:")
    print(f"  Ingestor URL: {args.ingestor_url}")
    print(f"  Resource ID: {args.resource_id}")
    print(f"  API Token: {args.api_token[:20]}...")
    print(f"  Interval: {args.interval}s")
    print("  Mode: ", end="")
    if args.metrics_only:
        print("Metrics only")
    elif args.logs_only:
        print("Logs only")
    else:
        print("Both metrics and logs")
    print(f"  Iterations: {'Infinite' if args.count == 0 else args.count}")
    print()

    # Create client
    config = HoruxConfig(
        ingestor_url=args.ingestor_url,
        api_token=args.api_token,
        resource_id=args.resource_id,
        flush_interval=2.0,  # Flush every 2 seconds
        max_batch_size=100,
    )

    config.add_instance_metadata("generator", "random_ingestion")
    config.add_instance_metadata("hostname", f"test-host-{random.randint(1, 10):02d}")

    print("Starting generation... (Press Ctrl+C to stop)\n")

    iteration = 0
    total_metrics = 0
    total_logs = 0
    start_time = time.time()

    try:
        with HoruxClient(config) as client:
            while args.count == 0 or iteration < args.count:
                iteration += 1

                # Generate data based on mode
                if not args.logs_only:
                    metrics_count = generate_random_metrics(client)
                    total_metrics += metrics_count

                if not args.metrics_only:
                    logs_count = generate_random_logs(client)
                    total_logs += logs_count

                # Print progress with rates
                if iteration % 10 == 0:
                    elapsed = time.time() - start_time
                    timestamp = datetime.now().strftime("%H:%M:%S")

                    if not args.logs_only and not args.metrics_only:
                        metrics_rate_s = total_metrics / elapsed if elapsed > 0 else 0
                        metrics_rate_m = metrics_rate_s * 60
                        logs_rate_s = total_logs / elapsed if elapsed > 0 else 0
                        logs_rate_m = logs_rate_s * 60
                        print(
                            f"[{timestamp}] {iteration} batches | "
                            f"Metrics: {total_metrics} ({metrics_rate_s:.1f}/s, {metrics_rate_m:.0f}/min) | "
                            f"Logs: {total_logs} ({logs_rate_s:.1f}/s, {logs_rate_m:.0f}/min)"
                        )
                    elif args.metrics_only:
                        metrics_rate_s = total_metrics / elapsed if elapsed > 0 else 0
                        metrics_rate_m = metrics_rate_s * 60
                        print(
                            f"[{timestamp}] {iteration} batches | "
                            f"Metrics: {total_metrics} ({metrics_rate_s:.1f}/s, {metrics_rate_m:.0f}/min)"
                        )
                    else:  # logs_only
                        logs_rate_s = total_logs / elapsed if elapsed > 0 else 0
                        logs_rate_m = logs_rate_s * 60
                        print(
                            f"[{timestamp}] {iteration} batches | "
                            f"Logs: {total_logs} ({logs_rate_s:.1f}/s, {logs_rate_m:.0f}/min)"
                        )

                # Wait before next iteration
                time.sleep(args.interval)

            print(f"\nCompleted {iteration} iterations. Flushing final batch...")

    except KeyboardInterrupt:
        print(f"\n\nStopped after {iteration} iterations. Flushing final batch...")
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)

    # Print final summary with average rates
    elapsed = time.time() - start_time
    print("\n" + "=" * 60)
    print("Final Summary")
    print("=" * 60)
    print(f"Total time: {elapsed:.1f}s")
    print(f"Total iterations: {iteration}")

    if not args.logs_only:
        metrics_rate_s = total_metrics / elapsed if elapsed > 0 else 0
        metrics_rate_m = metrics_rate_s * 60
        print(
            f"Total metrics: {total_metrics} ({metrics_rate_s:.1f}/s, {metrics_rate_m:.0f}/min avg)"
        )

    if not args.metrics_only:
        logs_rate_s = total_logs / elapsed if elapsed > 0 else 0
        logs_rate_m = logs_rate_s * 60
        print(f"Total logs: {total_logs} ({logs_rate_s:.1f}/s, {logs_rate_m:.0f}/min avg)")

    print("=" * 60)
    print("Done!")


if __name__ == "__main__":
    main()
