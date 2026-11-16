# @aterve/horux-client

Official TypeScript/JavaScript client library for Horux monitoring platform.

## Features

- ✅ **Metrics Support**: Counter, Gauge, Histogram, and Summary metric types
- ✅ **Logs Support**: Structured JSON logging with 6 severity levels
- ✅ **Auto-batching**: Automatic batching and periodic flushing
- ✅ **Instance Metadata**: Automatic instance labeling (hostname, pod, container ID)
- ✅ **Distributed Tracing**: Built-in support for trace_id, span_id, request_id
- ✅ **TypeScript**: Full TypeScript support with type definitions
- ✅ **Promise-based**: Modern async/await API

## Installation

```bash
npm install @aterve/horux-client
```

## Quick Start

```typescript
import { HoruxClient } from '@aterve/horux-client';

// Defaults to https://ingest.horux.io
const client = new HoruxClient({
  apiToken: 'tenant_<tenant-id>_<secret>',
  resourceId: 'my-resource-uuid',
  // ingestorUrl: 'http://localhost:3434', // Override for local testing
});

// Send metrics
await client.metrics.counter('requests_total', 1, {
  method: 'GET',
  status: '200',
});

// Send logs
await client.logs.info('Request processed', {
  duration_ms: 45,
  user_id: '123',
});

// Close when done
await client.close();
```

## Resource vs. service

Two different identifiers, and only the first is required.

| | `resourceId` | `serviceName` |
|---|---|---|
| What it is | the UUID of a Horux-registered **resource** | what this process **calls itself** |
| Required | yes | no |
| Where it comes from | the Horux console — it is assigned when you create the resource | your own deployment config; the process already knows it |
| Equivalent to | — | OpenTelemetry's `service.name`, Prometheus's `job` |
| Unset | the push is rejected | the column is stored `NULL` |

Set `serviceName` when one resource receives telemetry from several services and you want
them separable — logs and metrics can then be filtered by it, the same way spans always
could. Leaving it unset changes nothing about the request you send today.

```typescript
const client = new HoruxClient({
  apiToken: process.env.HORUX_API_TOKEN!,
  resourceId: process.env.HORUX_RESOURCE_ID!,   // required, a UUID
  serviceName: process.env.HORUX_SERVICE_NAME,  // optional, a name
});
```

## Configuration

```typescript
import { HoruxClient, HoruxConfig } from '@aterve/horux-client';

const config: HoruxConfig = {
  // ingestorUrl defaults to https://ingest.horux.io
  // ingestorUrl: 'http://localhost:3434', // Override for local testing
  apiToken: 'tenant_550e8400-e29b-41d4-a716-446655440000_secretkey123',
  resourceId: 'my-resource-uuid',
  serviceName: 'orders-api', // optional; see "Resource vs. service" below
  flushInterval: 10000, // 10 seconds (default)
  maxBatchSize: 1000, // Maximum items per batch (default)
  httpTimeout: 30000, // 30 seconds (default)
  instanceMetadata: {
    hostname: require('os').hostname(),
    version: '1.0.0',
    environment: 'production',
  },
  autoAddInstanceLabels: true, // Add instance metadata to metrics labels
};

const client = new HoruxClient(config);
```

### Environment Variables

```typescript
const config: HoruxConfig = {
  ingestorUrl: process.env.HORUX_INGESTOR_URL, // Defaults to https://ingest.horux.io
  apiToken: process.env.HORUX_API_TOKEN || '',
  resourceId: process.env.HORUX_RESOURCE_ID || '',
  serviceName: process.env.HORUX_SERVICE_NAME, // optional
  instanceMetadata: {
    hostname: require('os').hostname(),
    pod: process.env.POD_NAME,
    node: process.env.NODE_NAME,
    namespace: process.env.NAMESPACE,
  },
};
```

## Metrics Client

### Counter Metric

Counters always increase (e.g., total requests, errors).

```typescript
await client.metrics.counter('http_requests_total', 1, {
  method: 'GET',
  path: '/api/users',
  status: '200',
});
```

### Gauge Metric

Gauges can go up or down (e.g., memory usage, active connections).

```typescript
await client.metrics.gauge('memory_usage_bytes', 256 * 1024 * 1024, {
  type: 'heap',
});
```

### Histogram Metric

Histograms track distributions (e.g., request durations).

```typescript
await client.metrics.histogram(
  'http_request_duration_seconds',
  [
    { upper_bound: 0.1, count: 50 },   // 50 requests < 100ms
    { upper_bound: 0.5, count: 150 },  // 150 requests < 500ms
    { upper_bound: 1.0, count: 200 },  // 200 requests < 1s
  ],
  125.5, // sum of all durations
  250,   // total count
  { method: 'GET' }
);
```

### Summary Metric

Summaries track quantiles/percentiles.

```typescript
await client.metrics.summary(
  'http_request_duration_summary',
  [
    { quantile: 0.5, value: 0.2 },   // p50 = 200ms
    { quantile: 0.9, value: 0.8 },   // p90 = 800ms
    { quantile: 0.99, value: 2.5 },  // p99 = 2.5s
  ],
  125.5, // sum
  250,   // count
  { method: 'POST' }
);
```

### Manual Flush

```typescript
// Flush immediately (useful before shutdown)
await client.metrics.flush();
```

## Logs Client

### Log Levels

Six log levels supported: Trace, Debug, Info, Warn, Error, Fatal

```typescript
await client.logs.trace('Detailed trace information', fields);
await client.logs.debug('Debug information', fields);
await client.logs.info('Informational message', fields);
await client.logs.warn('Warning message', fields);
await client.logs.error('Error occurred', fields);
await client.logs.fatal('Fatal error', fields);
```

### Structured Logging

```typescript
await client.logs.info('Request processed', {
  method: 'GET',
  path: '/api/users',
  status_code: 200,
  duration_ms: 45,
  user_id: '12345',
});
```

### Distributed Tracing

```typescript
await client.logs.withTrace(
  'info',
  'Request processed successfully',
  { method: 'GET', duration_ms: 45 },
  'trace-id-abc123',  // trace_id
  'span-id-def456',   // span_id
  'req-789012'        // request_id
);
```

### Manual Flush

```typescript
// Flush immediately
await client.logs.flush();
```

## Express.js Middleware Example

```typescript
import express from 'express';
import { HoruxClient } from '@aterve/horux-client';

const app = express();
const horux = new HoruxClient({
  ingestorUrl: process.env.HORUX_INGESTOR_URL!,
  apiToken: process.env.HORUX_API_TOKEN!,
  resourceId: process.env.HORUX_RESOURCE_ID!,
});

// Logging middleware
app.use((req, res, next) => {
  const start = Date.now();

  res.on('finish', () => {
    const duration = Date.now() - start;

    // Log request
    horux.logs.info('HTTP request', {
      method: req.method,
      path: req.path,
      status_code: res.statusCode,
      duration_ms: duration,
      user_agent: req.get('user-agent'),
    });

    // Record metrics
    horux.metrics.counter('http_requests_total', 1, {
      method: req.method,
      path: req.path,
      status: String(res.statusCode),
    });

    horux.metrics.gauge('http_request_duration_seconds', duration / 1000, {
      method: req.method,
      path: req.path,
    });
  });

  next();
});

// Graceful shutdown
process.on('SIGTERM', async () => {
  await horux.close();
  process.exit(0);
});
```

## NestJS Example

```typescript
import { Injectable, OnModuleDestroy } from '@nestjs/common';
import { HoruxClient } from '@aterve/horux-client';

@Injectable()
export class HoruxService implements OnModuleDestroy {
  private client: HoruxClient;

  constructor() {
    this.client = new HoruxClient({
      ingestorUrl: process.env.HORUX_INGESTOR_URL!,
      apiToken: process.env.HORUX_API_TOKEN!,
      resourceId: process.env.HORUX_RESOURCE_ID!,
    });
  }

  async onModuleDestroy() {
    await this.client.close();
  }

  async logRequest(method: string, path: string, statusCode: number, duration: number) {
    await this.client.logs.info('HTTP request', {
      method,
      path,
      status_code: statusCode,
      duration_ms: duration,
    });

    await this.client.metrics.counter('http_requests_total', 1, {
      method,
      path,
      status: String(statusCode),
    });
  }
}
```

## Performance

The SDK uses automatic batching and background flushing for optimal performance:

- **Batch Size**: At most `maxBatchSize` items per request (default: 1000)
- **Flush Interval**: Periodic flush every `flushInterval` (default: 10s)
- **Immediate Flush**: Signalled when the queue reaches `maxBatchSize`; the
  request itself always runs on the flush cycle, never on the stack of whoever
  recorded the datapoint
- **Self-Pacing**: when the ingestor reports the tenant's allowance is spent
  (`X-RateLimit-Remaining: 0`), the cycle stops for the published
  `X-RateLimit-Reset` window instead of sending the next batch and being told
  429. It is pacing rather than a failure, so it does not advance the
  exponential retry schedule
- **Shutdown Flush**: `close()` sends everything still pending, waiting for a
  flush already in flight before running its own pass, and it is not bounded by
  the per-cycle batch cap
- **Exit Safety Net**: the flush timer is `unref()`ed so telemetry never holds
  the event loop open, and a `beforeExit` hook drains the queue for a process
  that simply ends. Still call `close()` where you can - the hook cannot run if
  the process is killed, and it is a backstop, not a substitute
- **After close()**: `add()` no longer queues. There is no flusher left, so an
  item would never be sent; it is reported through `onDrop` with the reason
  `client_closed` and counted in `stats().dropped`

## Backpressure and Rate Limits

Every tenant has a per-minute ingestion allowance. When it is reached the
ingestor accepts as much of the batch as fits and tells the client how long to
wait for the rest, so a backlog drains at the allowed rate instead of being
rejected whole:

- **Bounded queue**: at most `maxBufferSize` items are held (default: 100x
  `maxBatchSize`). Past that, `dropPolicy` decides what goes (`'oldest'` by
  default, so the most recent telemetry survives an outage). `onDrop` reports
  what was discarded.
- **Backoff**: after a throttled or failed flush the client waits - honouring
  the server's `Retry-After` when present, otherwise an exponential delay with
  jitter between `minRetryBackoff` and `maxRetryBackoff` (1s and 60s).
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

```typescript
const client = new HoruxClient({
  apiToken: process.env.HORUX_API_TOKEN!,
  resourceId: process.env.HORUX_RESOURCE_ID!,
  maxBufferSize: 100000,
  dropPolicy: 'oldest',
  onDrop: (stream, count, reason) =>
    console.warn(`horux dropped ${count} ${stream} (${reason})`),
});

const stats = client.metrics.queueStats();
// { pending, dropped, capacity, retryBackoffMs, shedding }
```

## Error Handling

The SDK throws errors for validation and network failures:

```typescript
try {
  await client.metrics.counter('requests', 1);
} catch (error) {
  console.error('Failed to record metric:', error);
  // The SDK will retry on next flush interval
}
```

## Best Practices

1. **Create client once**: Reuse the same client instance across your application
2. **Close on shutdown**: Always call `client.close()` before application exit
3. **Use labels/fields wisely**: Keep cardinality low for labels
4. **Handle errors**: Catch and log errors but don't let them crash your app
5. **Graceful shutdown**: Flush data before process termination

## TypeScript Support

The library is written in TypeScript and includes full type definitions:

```typescript
import { HoruxClient, HoruxConfig, LogLevel, MetricType } from '@aterve/horux-client';

const config: HoruxConfig = { /* ... */ };
const client = new HoruxClient(config);

const level: LogLevel = 'info';
const type: MetricType = 'counter';
```

## Development

```bash
npm install
npm test          # tsc -p tsconfig.test.json, then node --test on the built specs
npm run build
npx tsc --noEmit  # type-check without emitting
```

The `test` script passes an **unquoted** glob to `node --test`, so the shell
expands it into an explicit file list. Do not quote it: a quoted glob is handed
to Node to expand itself, which only Node 22+ can do, and the suite then fails
on Node 20 for a reason that has nothing to do with the tests.

Node 18 is the floor (`engines`): the client calls global `fetch`, which does
not exist before 18.

## License

Apache-2.0 © Aterve
