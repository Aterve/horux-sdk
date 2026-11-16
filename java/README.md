# Horux Java Client SDK

Official Java client library for sending metrics and logs to [Horux](https://horux.io) monitoring platform.

[![Maven Central](https://img.shields.io/maven-central/v/io.horux/horux-client.svg)](https://search.maven.org/artifact/io.horux/horux-client)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://www.apache.org/licenses/LICENSE-2.0)

## Features

- ✅ **Metrics**: Counter, Gauge, Histogram, Summary
- ✅ **Logs**: Structured logging with 6 severity levels
- ✅ **Auto-batching**: Automatic buffering and periodic flushing
- ✅ **Thread-safe**: Safe for concurrent use
- ✅ **Auto-discovery**: Hostname and Kubernetes metadata
- ✅ **Distributed Tracing**: Support for trace_id, span_id, request_id
- ✅ **Spring Boot Ready**: Easy integration with Spring applications
- ✅ **Zero Dependencies**: Uses only Java 11+ built-in HttpClient

## Requirements

- Java 11 or higher
- Maven or Gradle

## Installation

### Maven

```xml
<dependency>
    <groupId>io.horux</groupId>
    <artifactId>horux-client</artifactId>
    <version>1.1.0</version>
</dependency>
```

### Gradle

```gradle
implementation 'io.horux:horux-client:1.1.0'
```

## Quick Start

```java
import io.horux.client.*;

public class Main {
    public static void main(String[] args) {
        // Configure the client
        HoruxConfig config = HoruxConfig.builder()
            .apiToken(System.getenv("HORUX_API_TOKEN"))
            .resourceId(System.getenv("HORUX_RESOURCE_ID"))
            .build();

        // Create client (use try-with-resources for auto-cleanup)
        try (HoruxClient client = new HoruxClient(config)) {

            // Send metrics
            Map<String, String> labels = Map.of("method", "GET", "status", "200");
            client.metrics.counter("http.requests.total", 1, labels);
            client.metrics.gauge("memory.used.bytes", 1024.5, Map.of("type", "heap"));

            // Send logs
            Map<String, Object> fields = Map.of("userId", "123", "action", "login");
            client.logs.info("User logged in", fields);
            client.logs.error("Database connection failed", Map.of("error", "timeout"));
        }
    }
}
```

## Configuration

### Basic Configuration

```java
HoruxConfig config = HoruxConfig.builder()
    .apiToken("your-api-token")          // Required
    .resourceId("your-resource-id")         // Required
    .ingestorUrl("https://ingest.horux.io") // Optional, default shown
    .flushInterval(Duration.ofSeconds(10))  // Optional, default 10s
    .maxBatchSize(1000)                     // Optional, default 1000
    .httpTimeout(Duration.ofSeconds(30))    // Optional, default 30s
    .autoAddInstanceLabels(true)            // Optional, default true

    // Backpressure: how much to hold when the ingestor throttles, and what to
    // sacrifice once that is full.
    .maxBufferSize(100000)                  // Optional, default 100x maxBatchSize
    .dropPolicy(DropPolicy.OLDEST)          // Optional, default OLDEST
    .minRetryBackoff(Duration.ofSeconds(1)) // Optional, default 1s
    .maxRetryBackoff(Duration.ofSeconds(60))// Optional, default 60s
    .dropListener((stream, count, reason) ->
        log.warn("horux dropped {} {} ({})", count, stream, reason))
    .build();
```

### Backpressure and Rate Limits

Every tenant has a per-minute ingestion allowance. When it is reached the
ingestor accepts as much of the batch as fits and tells the client how long to
wait for the rest, so a backlog drains at the allowed rate instead of being
rejected whole:

- **Bounded queue**: at most `maxBufferSize` items are held. Past that, the
  drop policy decides what goes (`OLDEST` by default, so the most recent
  telemetry survives an outage). The drop listener reports what was discarded.
- **Backoff**: after a throttled or failed flush the client waits - honouring
  the server's `Retry-After` when present, otherwise an exponential delay with
  jitter between `minRetryBackoff` and `maxRetryBackoff`.
- **Shedding**: if the tenant is over its *storage* quota, waiting will not
  help, so the client drops pending data and stops buffering until it is worth
  trying again.
- **Permanent rejections**: a payload the ingestor can never accept (400, 413,
  422) is dropped rather than retried forever ahead of everything behind it.
- **Self-pacing**: when the ingestor reports the allowance is spent
  (`X-RateLimit-Remaining: 0`), the flush cycle stops for the published
  `X-RateLimit-Reset` window rather than sending the next batch and being told
  429. It is pacing, not a failure, so it does not advance the exponential
  retry schedule.

Recording never performs I/O: every request runs on the client's own flusher
thread, so a throttled ingestor cannot block application threads.

```java
QueueStats stats = client.metrics.getQueueStats();
// getPending(), getDropped(), getCapacity(), getRetryBackoff(), isShedding()
```

### Environment Variables

Load configuration from environment variables:

```java
HoruxConfig config = HoruxConfig.builder()
    .fromEnvironment() // Reads HORUX_API_TOKEN, HORUX_RESOURCE_ID, HORUX_INGESTOR_URL
    .build();
```

Supported environment variables:
- `HORUX_API_TOKEN` - API authentication token
- `HORUX_RESOURCE_ID` - Resource identifier
- `HORUX_INGESTOR_URL` - Ingestor service URL
- `POD_NAME`, `NODE_NAME`, `NAMESPACE`, `CONTAINER_ID` - Kubernetes metadata

### Custom Instance Metadata

```java
Map<String, String> metadata = new HashMap<>();
metadata.put("hostname", "server-01");
metadata.put("region", "us-east-1");
metadata.put("environment", "production");

HoruxConfig config = HoruxConfig.builder()
    .apiToken("...")
    .resourceId("...")
    .instanceMetadata(metadata)
    .build();
```

## Metrics

### Counter (Always Increasing)

Use for: request counts, error counts, events.

```java
client.metrics.counter("http.requests.total", 1, Map.of(
    "method", "GET",
    "status", "200"
));
```

### Gauge (Can Increase or Decrease)

Use for: memory usage, active connections, queue depth.

```java
client.metrics.gauge("memory.used.bytes", 1024.5, Map.of(
    "type", "heap"
));

client.metrics.gauge("active.connections", 42, Map.of(
    "service", "postgres"
));
```

### Histogram (Distribution Tracking)

Use for: request durations, response sizes.

```java
List<HistogramBucket> buckets = List.of(
    new HistogramBucket(0.1, 10),   // 10 requests < 100ms
    new HistogramBucket(0.5, 45),   // 45 requests < 500ms
    new HistogramBucket(1.0, 120),  // 120 requests < 1s
    new HistogramBucket(Double.POSITIVE_INFINITY, 150) // 150 total
);

client.metrics.histogram("http.request.duration", buckets, 67.5, 150, Map.of(
    "endpoint", "/api/users"
));
```

### Summary (Quantile Tracking)

Use for: p50, p90, p99 latencies.

```java
List<SummaryQuantile> quantiles = List.of(
    new SummaryQuantile(0.5, 100),  // p50 = 100ms
    new SummaryQuantile(0.9, 250),  // p90 = 250ms
    new SummaryQuantile(0.99, 500)  // p99 = 500ms
);

client.metrics.summary("response.time", quantiles, 15000, 150, Map.of(
    "service", "api"
));
```

## Logging

### Log Levels

```java
client.logs.trace("Detailed diagnostic info", fields);
client.logs.debug("Debug information", fields);
client.logs.info("Informational message", fields);
client.logs.warn("Warning message", fields);
client.logs.error("Error occurred", fields);
client.logs.fatal("Critical failure", fields);
```

### Structured Logging

```java
Map<String, Object> fields = new HashMap<>();
fields.put("userId", "123");
fields.put("action", "purchase");
fields.put("amount", 99.99);
fields.put("currency", "USD");

client.logs.info("Purchase completed", fields);
```

### Distributed Tracing

```java
client.logs.withTrace(
    LogLevel.INFO,
    "Request processed",
    Map.of("duration", 145, "status", "success"),
    "trace-123456",    // trace_id
    "span-789",        // span_id
    "req-abc"          // request_id
);
```

## Spring Boot Integration

### Configuration Bean

```java
@Configuration
public class HoruxConfiguration {

    @Bean
    public HoruxClient horuxClient() {
        HoruxConfig config = HoruxConfig.builder()
            .apiToken("${horux.api.token}")
            .resourceId("${horux.resource.id}")
            .fromEnvironment()
            .build();

        return new HoruxClient(config);
    }

    @PreDestroy
    public void cleanup() {
        horuxClient().close();
    }
}
```

### HTTP Request Monitoring Filter

```java
@Component
public class MetricsFilter implements Filter {

    @Autowired
    private HoruxClient horux;

    @Override
    public void doFilter(ServletRequest request, ServletResponse response, FilterChain chain)
            throws IOException, ServletException {

        HttpServletRequest httpRequest = (HttpServletRequest) request;
        HttpServletResponse httpResponse = (HttpServletResponse) response;

        long start = System.currentTimeMillis();

        try {
            chain.doFilter(request, response);
        } finally {
            long duration = System.currentTimeMillis() - start;

            Map<String, String> labels = Map.of(
                "method", httpRequest.getMethod(),
                "path", httpRequest.getRequestURI(),
                "status", String.valueOf(httpResponse.getStatus())
            );

            horux.metrics.counter("http.requests.total", 1, labels);
            horux.metrics.gauge("http.request.duration", duration, labels);
        }
    }
}
```

### Controller Example

```java
@RestController
public class OrderController {

    @Autowired
    private HoruxClient horux;

    @PostMapping("/orders")
    public Order createOrder(@RequestBody OrderRequest request) {
        try {
            Order order = orderService.create(request);

            // Track business metric
            horux.metrics.counter("orders.created", 1, Map.of(
                "payment_method", request.getPaymentMethod()
            ));

            // Log event
            horux.logs.info("Order created", Map.of(
                "orderId", order.getId(),
                "amount", order.getTotal()
            ));

            return order;

        } catch (Exception e) {
            horux.metrics.counter("errors.total", 1, Map.of(
                "type", e.getClass().getSimpleName()
            ));
            horux.logs.error("Order creation failed", Map.of(
                "error", e.getMessage()
            ));
            throw e;
        }
    }
}
```

## Best Practices

### 1. Use Try-With-Resources

Always use try-with-resources to ensure proper cleanup:

```java
try (HoruxClient client = new HoruxClient(config)) {
    // Your code here
} // Automatically flushes and closes
```

### 2. Limit Label Cardinality

Avoid high-cardinality labels (like user IDs, timestamps):

```java
// ❌ Bad - high cardinality
client.metrics.counter("requests", 1, Map.of("userId", userId));

// ✅ Good - low cardinality
client.metrics.counter("requests", 1, Map.of("user_type", "premium"));
```

### 3. Use Gauges for Latency

For simple latency tracking, use gauges instead of histograms:

```java
long start = System.currentTimeMillis();
processRequest();
long duration = System.currentTimeMillis() - start;

client.metrics.gauge("request.duration", duration, Map.of("endpoint", "/api/users"));
```

### 4. Structured Logging

Always use structured fields instead of string formatting:

```java
// ❌ Bad
client.logs.info("User " + userId + " logged in from " + ip, null);

// ✅ Good
client.logs.info("User logged in", Map.of("userId", userId, "ip", ip));
```

### 5. Error Handling

The client gracefully handles errors and won't crash your application:

```java
// If Horux is temporarily unavailable, metrics/logs are buffered
client.metrics.counter("requests", 1, labels); // Won't throw exception
client.logs.error("Something happened", fields); // Won't throw exception
```

## Batching and Performance

- **Auto-batching**: Metrics and logs are buffered in memory
- **Periodic flush**: Data is sent every 10 seconds (configurable)
- **Size-based flush**: Automatic flush when buffer reaches 1000 items (configurable)
- **Thread-safe**: All operations are safe for concurrent use
- **Background threads**: Flushing happens in background, won't block your app

## API Reference

### HoruxClient

- `HoruxClient(HoruxConfig)` - Create new client
- `metrics` - Access MetricsClient
- `logs` - Access LogsClient
- `close()` - Close the client and flush remaining data. It waits up to the
  configured `httpTimeout` for a request already in flight rather than
  interrupting it (an interrupted request looks like a transport failure, gets
  requeued, and is then written twice), and it drains until the queue is empty
  rather than stopping at the per-cycle batch cap

### MetricsClient

- `counter(String name, double value, Map<String, String> labels)`
- `gauge(String name, double value, Map<String, String> labels)`
- `histogram(String name, List<HistogramBucket> buckets, double sum, long count, Map<String, String> labels)`
- `summary(String name, List<SummaryQuantile> quantiles, double sum, long count, Map<String, String> labels)`
- `flush()` - Manually flush buffered metrics
- `close()` - Close client and flush

### LogsClient

- `trace(String message, Map<String, Object> fields)`
- `debug(String message, Map<String, Object> fields)`
- `info(String message, Map<String, Object> fields)`
- `warn(String message, Map<String, Object> fields)`
- `error(String message, Map<String, Object> fields)`
- `fatal(String message, Map<String, Object> fields)`
- `withTrace(LogLevel level, String message, Map<String, Object> fields, String traceId, String spanId, String requestId)`
- `flush()` - Manually flush buffered logs
- `close()` - Close client and flush

## Examples

See the [examples/](examples/) directory for complete working examples:
- [BasicExample.java](examples/BasicExample.java) - Simple standalone usage
- [SpringBootExample.java](examples/SpringBootExample.java) - Spring Boot integration

## License

Apache-2.0 - see [LICENSE](../LICENSE) for details.

## Support

- Documentation: [https://horux.io/docs](https://horux.io/docs)
- Issues: [https://github.com/Aterve/horux-sdk/issues](https://github.com/Aterve/horux-sdk/issues)
- Email: [support@aterve.com](mailto:support@aterve.com)
