## Horux - Go Client SDK

Official Go client library for sending metrics and logs to Horux monitoring platform.

### Features

- ✅ **Metrics Support**: Counter, Gauge, Histogram, and Summary metric types
- ✅ **Logs Support**: Structured JSON logging with 6 severity levels
- ✅ **Auto-batching**: Automatic batching and periodic flushing
- ✅ **Instance Metadata**: Automatic instance labeling (hostname, pod, container ID)
- ✅ **Distributed Tracing**: Built-in support for trace_id, span_id, request_id
- ✅ **Thread-safe**: Safe for concurrent use
- ✅ **Configurable**: Flexible configuration via code or environment variables

### Installation

```bash
go get github.com/Aterve/horux-sdk/go
```

### Quick Start

```go
package main

import (
    "log"
    "time"

    client "github.com/Aterve/horux-sdk/go"
)

func main() {
    // Create configuration (defaults to https://ingest.horux.io)
    config := client.DefaultConfig()
    config.APIToken = "tenant_<tenant-id>_<secret>"
    config.ResourceID = "my-resource-uuid"

    // For local testing, override the default URL:
    // config.IngestorURL = "http://localhost:3434"

    // Create unified client
    horusClient, err := client.NewClient(config)
    if err != nil {
        log.Fatal(err)
    }
    defer horusClient.Close()

    // Send metrics
    horusClient.Metrics.Counter("requests_total", 1, map[string]string{
        "method": "GET",
        "status": "200",
    })

    // Send logs
    horusClient.Logs.Info("Request processed", map[string]interface{}{
        "duration_ms": 45,
        "user_id": "123",
    })
}
```

### Configuration

#### Code-based Configuration

```go
config := client.DefaultConfig()
// IngestorURL defaults to https://ingest.horux.io
// Override for local testing:
// config.IngestorURL = "http://localhost:3434"
config.APIToken = "tenant_550e8400-e29b-41d4-a716-446655440000_secretkey123"
config.ResourceID = "my-resource-uuid"
config.ServiceName = "orders-api" // optional; see "Resource vs. service" below
config.FlushInterval = 10 * time.Second
config.MaxBatchSize = 1000
config.HTTPTimeout = 30 * time.Second

// Backpressure: how much to hold when the ingestor throttles, and what to
// sacrifice once that is full.
config.MaxBufferSize = 10000              // default: 10x MaxBatchSize
config.DropPolicy = client.DropOldest     // or client.DropNewest
config.MinRetryBackoff = 1 * time.Second
config.MaxRetryBackoff = 60 * time.Second
config.OnDrop = func(stream string, count int, reason string) {
    log.Printf("horux dropped %d %s (%s)", count, stream, reason)
}

// Add custom instance metadata
config.AddInstanceMetadata("version", "1.0.0")
config.AddInstanceMetadata("environment", "production")
```

#### Resource vs. service

Two different identifiers, and only the first is required.

| | `ResourceID` | `ServiceName` |
|---|---|---|
| What it is | the UUID of a Horux-registered **resource** | what this process **calls itself** |
| Required | yes | no |
| Where it comes from | the Horux console — assigned when you create the resource | your own deployment config |
| Equivalent to | — | OpenTelemetry's `service.name`, Prometheus's `job` |
| Unset | the push is rejected | the column is stored `NULL` |

Set `ServiceName` when one resource receives telemetry from several services and you want
them separable. Leaving it unset changes nothing about the request you send today.

#### Environment Variables

```bash
# Override default URL for local testing (defaults to https://ingest.horux.io)
export HORUX_INGESTOR_URL="http://localhost:3434"
export HORUX_API_TOKEN="tenant_<tenant-id>_<secret>"
export HORUX_RESOURCE_ID="my-resource-uuid"
# Optional: what this process calls itself (OTel `service.name` / Prometheus `job`)
export HORUX_SERVICE_NAME="orders-api"

# Kubernetes metadata (automatically detected)
export POD_NAME="api-pod-123"
export NODE_NAME="worker-node-01"
export NAMESPACE="production"
export CONTAINER_ID="abc123..."
```

```go
config := client.DefaultConfig()
config.FromEnv() // Load from environment variables
```

### Metrics Client

#### Counter Metric

Counters always increase (e.g., total requests, errors).

```go
metricsClient.Counter("http_requests_total", 1, map[string]string{
    "method": "GET",
    "path": "/api/users",
    "status": "200",
})
```

#### Gauge Metric

Gauges can go up or down (e.g., memory usage, active connections).

```go
metricsClient.Gauge("memory_usage_bytes", 1024*1024*256, map[string]string{
    "type": "heap",
})
```

#### Histogram Metric

Histograms track distributions (e.g., request durations).

```go
buckets := []client.HistogramBucket{
    {UpperBound: 0.1, Count: 50},   // 50 requests < 100ms
    {UpperBound: 0.5, Count: 150},  // 150 requests < 500ms
    {UpperBound: 1.0, Count: 200},  // 200 requests < 1s
}

metricsClient.Histogram(
    "http_request_duration_seconds",
    buckets,
    125.5, // sum of all durations
    250,   // total count
    map[string]string{"method": "GET"},
)
```

#### Summary Metric

Summaries track quantiles/percentiles.

```go
quantiles := []client.SummaryQuantile{
    {Quantile: 0.5, Value: 0.2},   // p50 = 200ms
    {Quantile: 0.9, Value: 0.8},   // p90 = 800ms
    {Quantile: 0.99, Value: 2.5},  // p99 = 2.5s
}

metricsClient.Summary(
    "http_request_duration_summary",
    quantiles,
    125.5, // sum
    250,   // count
    map[string]string{"method": "POST"},
)
```

#### Manual Flush

```go
// Flush immediately (useful before shutdown)
if err := metricsClient.Flush(); err != nil {
    log.Printf("Failed to flush metrics: %v", err)
}
```

### Logs Client

#### Log Levels

Six log levels supported: Trace, Debug, Info, Warn, Error, Fatal

```go
logsClient.Trace("Detailed trace information", fields)
logsClient.Debug("Debug information", fields)
logsClient.Info("Informational message", fields)
logsClient.Warn("Warning message", fields)
logsClient.Error("Error occurred", fields)
logsClient.Fatal("Fatal error", fields)
```

#### Structured Logging

```go
logsClient.Info("Request processed", map[string]interface{}{
    "method": "GET",
    "path": "/api/users",
    "status_code": 200,
    "duration_ms": 45,
    "user_id": "12345",
})
```

#### Distributed Tracing

```go
logsClient.WithTrace(
    client.LogLevelInfo,
    "Request processed successfully",
    map[string]interface{}{
        "method": "GET",
        "duration_ms": 45,
    },
    "trace-id-abc123",  // trace_id
    "span-id-def456",   // span_id
    "req-789012",       // request_id
)
```

#### Manual Flush

```go
// Flush immediately
if err := logsClient.Flush(); err != nil {
    log.Printf("Failed to flush logs: %v", err)
}
```

### Unified Client

Use a single client for both metrics and logs:

```go
horusClient, err := client.NewClient(config)
if err != nil {
    log.Fatal(err)
}
defer horusClient.Close()

// Access metrics client
horusClient.Metrics.Counter("requests_total", 1, nil)

// Access logs client
horusClient.Logs.Info("Request completed", nil)
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
```go
config.AddInstanceMetadata("version", "1.0.0")
config.AddInstanceMetadata("environment", "production")
config.AddInstanceMetadata("datacenter", "us-east-1")
```

### Performance

The SDK uses automatic batching and background flushing for optimal performance:

- **Batch Size**: At most `MaxBatchSize` items per request (default: 1000)
- **Flush Interval**: Periodic flush every `FlushInterval` (default: 10s)
- **Immediate Flush**: Signalled when the queue reaches `MaxBatchSize`; the
  request itself always runs on the background goroutine, never on the
  goroutine that recorded the datapoint
- **Self-Pacing**: when the ingestor reports the tenant's allowance is spent
  (`X-RateLimit-Remaining: 0`), the cycle stops for the published
  `X-RateLimit-Reset` window instead of sending the next batch and being told
  429. It is pacing rather than a failure, so it does not advance the
  exponential retry schedule
- **Shutdown Flush**: `Close()` sends everything still pending, waiting for a
  flush already in flight before running its own pass, and it is not bounded by
  the per-cycle batch cap
- **After Close()**: recording no longer queues. There is no flush loop left,
  so an item would never be sent; it is reported through `OnDrop` with the
  reason `client_closed` and counted in the queue stats. `defer Close()` stays
  the idiom

### Backpressure and Rate Limits

Every tenant has a per-minute ingestion allowance. When it is reached the
ingestor accepts as much of the batch as fits and tells the client how long to
wait for the rest, so a backlog drains at the allowed rate instead of being
rejected whole:

- **Bounded queue**: at most `MaxBufferSize` items are held. Past that,
  `DropPolicy` decides what goes (`DropOldest` by default, so the most recent
  telemetry survives an outage). `OnDrop` reports what was discarded.
- **Backoff**: after a throttled or failed flush the client waits - honouring
  the server's `Retry-After` when present, otherwise an exponential delay with
  jitter between `MinRetryBackoff` and `MaxRetryBackoff`.
- **Shedding**: if the tenant is over its *storage* quota, waiting will not
  help, so the client drops pending data and stops buffering until it is worth
  trying again.
- **Permanent rejections**: a payload the ingestor can never accept (400, 413,
  422) is dropped rather than retried forever ahead of everything behind it.

`QueueStats()` on either client reports what is pending, what has been dropped
and whether the client is currently backing off:

```go
stats := metricsClient.QueueStats()
log.Printf("pending=%d dropped=%d backoff=%s shedding=%t",
    stats.Pending, stats.Dropped, stats.RetryBackoff, stats.Shedding)
```

### Error Handling

The SDK returns errors for validation and network failures:

```go
if err := metricsClient.Counter("requests", 1, nil); err != nil {
    log.Printf("Failed to record metric: %v", err)
    // The SDK will retry on next flush interval
}
```

### Best Practices

1. **Create client once**: Reuse the same client instance across your application
2. **Defer Close()**: Always defer `client.Close()` to flush remaining data
3. **Use labels/fields wisely**: Keep cardinality low for labels
4. **Pre-flush on shutdown**: Call `Flush()` before application exit
5. **Handle errors**: Log errors but don't let them crash your app

### Examples

See the `/examples` directory for complete examples:

- `/examples/metrics` - Metrics-only example
- `/examples/logs` - Logs-only example
- `/examples/unified` - Combined metrics and logs

Run examples:
```bash
cd examples/unified
go run main.go
```

### Thread Safety

All client operations are thread-safe and can be called concurrently from multiple goroutines.

### License

Copyright © 2025 Aterve
