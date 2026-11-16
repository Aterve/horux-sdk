## Horux - Python Client SDK

Official Python client library for sending metrics and logs to Horux monitoring platform.

### Features

- ✅ **Metrics Support**: Counter, Gauge, Histogram, and Summary metric types
- ✅ **Logs Support**: Structured JSON logging with 6 severity levels
- ✅ **Auto-batching**: Automatic batching and periodic flushing
- ✅ **Instance Metadata**: Automatic instance labeling (hostname, pod, container ID)
- ✅ **Distributed Tracing**: Built-in support for trace_id, span_id, request_id
- ✅ **Thread-safe**: Safe for concurrent use
- ✅ **Context Manager**: Automatic cleanup with Python `with` statement
- ✅ **Configurable**: Flexible configuration via code or environment variables
- ✅ **Type Hints**: Full type annotations for better IDE support

### Installation

```bash
pip install horux-client
```

Or install from source:

```bash
cd python
pip install -e .
```

### Quick Start

```python
from horux_client import HoruxClient, HoruxConfig

# Create configuration (defaults to https://ingest.horux.io)
config = HoruxConfig(
    api_token="tenant_<tenant-id>_<secret>",
    resource_id="my-resource-uuid"
    # ingestor_url="http://localhost:3434"  # Override for local testing
)

# Create unified client
with HoruxClient(config) as client:
    # Send metrics
    client.metrics.counter("requests_total", 1, {"method": "GET", "status": "200"})

    # Send logs
    client.logs.info("Request processed", {"duration_ms": 45, "user_id": "123"})
```

### Configuration

#### Code-based Configuration

```python
from horux_client import HoruxConfig

config = HoruxConfig(
    # ingestor_url defaults to https://ingest.horux.io
    # ingestor_url="http://localhost:3434",  # Override for local testing
    api_token="tenant_550e8400-e29b-41d4-a716-446655440000_secretkey123",
    resource_id="my-resource-uuid",
    flush_interval=10.0,  # seconds
    max_batch_size=1000,
    http_timeout=30.0,    # seconds
)

# Add custom instance metadata
config.add_instance_metadata("version", "1.0.0")
config.add_instance_metadata("environment", "production")
```

#### Environment Variables

```bash
# Override default URL for local testing (defaults to https://ingest.horux.io)
export HORUX_INGESTOR_URL="http://localhost:3434"
export HORUX_API_TOKEN="tenant_<tenant-id>_<secret>"
export HORUX_RESOURCE_ID="my-resource-uuid"

# Kubernetes metadata (automatically detected)
export POD_NAME="api-pod-123"
export NODE_NAME="worker-node-01"
export NAMESPACE="production"
export CONTAINER_ID="abc123..."
```

```python
from horux_client import HoruxConfig

# Load from environment variables
config = HoruxConfig.from_env()
```

### Metrics Client

#### Counter Metric

Counters always increase (e.g., total requests, errors).

```python
client.metrics.counter("http_requests_total", 1, {
    "method": "GET",
    "path": "/api/users",
    "status": "200"
})
```

#### Gauge Metric

Gauges can go up or down (e.g., memory usage, active connections).

```python
client.metrics.gauge("memory_usage_bytes", 256 * 1024 * 1024, {
    "type": "heap"
})
```

#### Histogram Metric

Histograms track distributions (e.g., request durations).

```python
from horux_client import HistogramBucket

buckets = [
    HistogramBucket(upper_bound=0.1, count=50),   # 50 requests < 100ms
    HistogramBucket(upper_bound=0.5, count=150),  # 150 requests < 500ms
    HistogramBucket(upper_bound=1.0, count=200),  # 200 requests < 1s
]

client.metrics.histogram(
    "http_request_duration_seconds",
    buckets=buckets,
    sum=125.5,  # sum of all durations
    count=250,  # total count
    labels={"method": "GET"}
)
```

#### Summary Metric

Summaries track quantiles/percentiles.

```python
from horux_client import SummaryQuantile

quantiles = [
    SummaryQuantile(quantile=0.5, value=0.2),   # p50 = 200ms
    SummaryQuantile(quantile=0.9, value=0.8),   # p90 = 800ms
    SummaryQuantile(quantile=0.99, value=2.5),  # p99 = 2.5s
]

client.metrics.summary(
    "http_request_duration_summary",
    quantiles=quantiles,
    sum=125.5,  # sum
    count=250,  # count
    labels={"method": "POST"}
)
```

#### Manual Flush

```python
# Flush immediately (useful before shutdown)
client.metrics.flush()
```

### Logs Client

#### Log Levels

Six log levels supported: Trace, Debug, Info, Warn, Error, Fatal

```python
client.logs.trace("Detailed trace information", fields)
client.logs.debug("Debug information", fields)
client.logs.info("Informational message", fields)
client.logs.warn("Warning message", fields)
client.logs.error("Error occurred", fields)
client.logs.fatal("Fatal error", fields)
```

#### Structured Logging

```python
client.logs.info("Request processed", {
    "method": "GET",
    "path": "/api/users",
    "status_code": 200,
    "duration_ms": 45,
    "user_id": "12345"
})
```

#### Distributed Tracing

```python
client.logs.info(
    "Request processed successfully",
    fields={"method": "GET", "duration_ms": 45},
    trace_id="trace-id-abc123",
    span_id="span-id-def456",
    request_id="req-789012"
)
```

#### Manual Flush

```python
# Flush immediately
client.logs.flush()
```

### Context Manager

Use as a context manager for automatic cleanup:

```python
from horux_client import HoruxClient, HoruxConfig

config = HoruxConfig.from_env()

with HoruxClient(config) as client:
    client.metrics.counter("requests_total", 1)
    client.logs.info("Request completed")
    # Automatic flush on exit
```

### Instance Metadata

Instance metadata is automatically added to all metrics (as labels) and logs (as metadata field).

**Automatic Metadata:**
- `hostname`: Automatically detected from system

**Kubernetes Metadata** (from environment variables):
- `pod`: Pod name
- `node`: Node name
- `namespace`: Kubernetes namespace
- `container_id`: Container ID

**Custom Metadata:**
```python
config.add_instance_metadata("version", "1.0.0")
config.add_instance_metadata("environment", "production")
config.add_instance_metadata("datacenter", "us-east-1")
```

### Performance

The SDK uses automatic batching and background flushing for optimal performance:

- **Batch Size**: At most `max_batch_size` items per request (default: 1000)
- **Flush Interval**: Periodic flush every `flush_interval` seconds (default: 10s)
- **Immediate Flush**: Signalled when the queue reaches `max_batch_size`; the
  request itself always runs on the client's single background thread, never on
  the thread that recorded the datapoint
- **Self-Pacing**: when the ingestor reports the tenant's allowance is spent
  (`X-RateLimit-Remaining: 0`), the cycle stops for the published
  `X-RateLimit-Reset` window instead of sending the next batch and being told
  429. It is pacing rather than a failure, so it does not advance the
  exponential retry schedule
- **Shutdown Flush**: `close()` sends everything still pending and is not
  bounded by the per-cycle batch cap. Its wait for a flush already in flight is
  bounded, so `close()` cannot hang a SIGTERM handler into a SIGKILL; if it has
  to give up it logs a WARNING naming how many items it left
- **Exit Safety Net**: the worker is a daemon thread, so telemetry never holds
  the interpreter open - but Python kills daemon threads at shutdown *without
  running them*, so an `atexit` hook flushes for a script that simply returns
  from `main()`. Prefer `close()` or the context manager: the hook is a
  backstop, and it cannot run if the process is killed
- **After close()**: `add()` no longer queues. There is no worker left, so an
  item would never be sent; it is reported through `on_drop` with the reason
  `client_closed` and counted in `stats().dropped`
- **Timestamps**: a naive `datetime` (what `datetime.now()` returns) is
  interpreted as local time and sent with a UTC offset. The ingestor requires
  RFC 3339 *with* an offset and rejects an offsetless timestamp outright

### Backpressure and Rate Limits

Every tenant has a per-minute ingestion allowance. When it is reached the
ingestor accepts as much of the batch as fits and tells the client how long to
wait for the rest, so a backlog drains at the allowed rate instead of being
rejected whole:

- **Bounded queue**: at most `max_buffer_size` items are held (default: 100x
  `max_batch_size`). Past that, `drop_policy` decides what goes (`"oldest"` by
  default, so the most recent telemetry survives an outage). `on_drop` reports
  what was discarded.
- **Backoff**: after a throttled or failed flush the client waits - honouring
  the server's `Retry-After` when present, otherwise an exponential delay with
  jitter between `min_retry_backoff` and `max_retry_backoff` (1s and 60s).
- **Shedding**: if the tenant is over its *storage* quota, waiting will not
  help, so the client drops pending data and stops buffering until it is worth
  trying again.
- **Permanent rejections**: a payload the ingestor can never accept (400, 413,
  422) is dropped rather than retried forever ahead of everything behind it.

  Sizing it: the queue grows on demand, so the default is a ceiling and not an
  allocation. At a measured ~832 bytes per queued metric with five labels,
  100,000 items is ~79MB at full stretch, and the outage it survives is
  `items / (items per second)` -- about 17 minutes at 100/s, or 2.8 hours at
  10/s. The bound is per client per **process**, so a pre-forking server holds
  one buffer per worker: check four workers x your setting against the
  container's memory limit before raising it, because being OOM-killed during
  the incident the buffer exists to survive is worse than dropping telemetry.

```python
config = HoruxConfig(
    api_token="...",
    resource_id="...",
    max_buffer_size=100000,
    drop_policy="oldest",
    on_drop=lambda stream, count, reason: logging.warning(
        "horux dropped %d %s (%s)", count, stream, reason
    ),
)

stats = client.metrics.queue_stats()
# QueueStats(pending=..., dropped=..., capacity=..., retry_backoff=..., shedding=...)
```

### Error Handling

The SDK logs errors but does not raise exceptions for network failures:

```python
import logging

# Enable debug logging to see SDK errors
logging.basicConfig(level=logging.DEBUG)

# SDK errors are logged, not raised
client.metrics.counter("requests", 1)
# If network fails, error is logged and SDK will retry on next flush
```

### Best Practices

1. **Use context manager**: Use `with` statement for automatic cleanup
2. **Create client once**: Reuse the same client instance across your application
3. **Use labels/fields wisely**: Keep cardinality low for labels
4. **Pre-flush on shutdown**: Call `flush()` or use context manager before application exit
5. **Handle errors gracefully**: SDK logs errors but doesn't crash your app
6. **Set appropriate batch sizes**: Larger batches = fewer requests but higher memory usage

### Examples

#### Django Integration

```python
# settings.py
from horux_client import HoruxClient, HoruxConfig

config = HoruxConfig.from_env()
HORUX_CLIENT = HoruxClient(config)

# middleware.py
from django.utils.deprecation import MiddlewareMixin
from django.conf import settings
import time

class HoruxMiddleware(MiddlewareMixin):
    def process_request(self, request):
        request._horus_start_time = time.time()

    def process_response(self, request, response):
        if hasattr(request, '_horus_start_time'):
            duration = time.time() - request._horus_start_time

            # Log metrics
            settings.HORUX_CLIENT.metrics.counter("http_requests_total", 1, {
                "method": request.method,
                "path": request.path,
                "status": str(response.status_code)
            })

            # Log request
            settings.HORUX_CLIENT.logs.info(f"{request.method} {request.path}", {
                "method": request.method,
                "path": request.path,
                "status_code": response.status_code,
                "duration_ms": duration * 1000
            })

        return response
```

#### Flask Integration

```python
from flask import Flask, request, g
from horux_client import HoruxClient, HoruxConfig
import time

app = Flask(__name__)
horux_client = HoruxClient(HoruxConfig.from_env())

@app.before_request
def before_request():
    g.start_time = time.time()

@app.after_request
def after_request(response):
    if hasattr(g, 'start_time'):
        duration = time.time() - g.start_time

        # Log metrics
        horux_client.metrics.counter("http_requests_total", 1, {
            "method": request.method,
            "endpoint": request.endpoint or "unknown",
            "status": str(response.status_code)
        })

        # Log request
        horux_client.logs.info(f"{request.method} {request.path}", {
            "method": request.method,
            "path": request.path,
            "status_code": response.status_code,
            "duration_ms": duration * 1000
        })

    return response

@app.teardown_appcontext
def teardown(exception=None):
    horux_client.close()
```

#### FastAPI Integration

```python
from fastapi import FastAPI, Request
from horux_client import HoruxClient, HoruxConfig
import time

app = FastAPI()
horux_client = HoruxClient(HoruxConfig.from_env())

@app.middleware("http")
async def horus_middleware(request: Request, call_next):
    start_time = time.time()

    response = await call_next(request)

    duration = time.time() - start_time

    # Log metrics
    horux_client.metrics.counter("http_requests_total", 1, {
        "method": request.method,
        "path": request.url.path,
        "status": str(response.status_code)
    })

    # Log request
    horux_client.logs.info(f"{request.method} {request.url.path}", {
        "method": request.method,
        "path": str(request.url.path),
        "status_code": response.status_code,
        "duration_ms": duration * 1000
    })

    return response

@app.on_event("shutdown")
async def shutdown_event():
    horux_client.close()
```

### Thread Safety

All client operations are thread-safe and can be called concurrently from multiple threads.

```python
from concurrent.futures import ThreadPoolExecutor

def worker(i):
    client.metrics.counter("worker_tasks", 1, {"worker_id": str(i)})
    client.logs.info(f"Worker {i} completed task")

with ThreadPoolExecutor(max_workers=10) as executor:
    for i in range(100):
        executor.submit(worker, i)
```

### Testing Utilities

The SDK includes utilities for testing and development:

#### Random Data Generator

Generate continuous random metrics and logs for testing:

```bash
python random_ingestion.py <your_api_token>
```

**Options:**
```bash
# Only generate metrics
python random_ingestion.py <token> --metrics-only

# Only generate logs
python random_ingestion.py <token> --logs-only

# Custom interval (default: 1 second)
python random_ingestion.py <token> --interval 0.5

# Limited iterations instead of infinite
python random_ingestion.py <token> --count 100

# Custom resource ID
python random_ingestion.py <token> --resource-id <your-resource-uuid>
```

**Resource ID:** required — pass `--resource-id` or set `HORUX_RESOURCE_ID`. There is
deliberately no default: a wrong-but-plausible UUID makes every push fail resource lookup.

**Note:** Project ID is automatically fetched by the ingestor based on the resource ID.

#### Authentication Testing

Comprehensive authentication test suite:

```bash
# Test with valid token
python test_auth.py <api_token_with_permissions>

# Test with viewer token (no permissions)
python test_auth.py <api_token> <viewer_token>
```

#### Token Creation Helper

Create API tokens programmatically:

```bash
# List available roles
python create_test_token.py --list-roles --session-cookie <cookie>

# Create token with specific role
python create_test_token.py \
  --session-cookie <cookie> \
  --role-id <role_uuid> \
  --name "Test Token"
```

See [TESTING.md](./TESTING.md) for complete testing documentation.

### Development

#### Running Tests

```bash
pip install -e ".[dev]"
pytest
```

#### Type Checking

```bash
mypy horux_client
```

#### Code Formatting

```bash
black horux_client
ruff check horux_client
```

### License

Copyright © 2025 Aterve
