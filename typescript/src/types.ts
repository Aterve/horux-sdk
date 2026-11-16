/**
 * Which end of a full buffer is sacrificed when the ingestor cannot keep up.
 * 'oldest' (the default) keeps the most recent telemetry, which during an
 * outage is usually the more useful half.
 */
export type DropPolicy = 'oldest' | 'newest';

export interface HoruxConfig {
  /** Base URL of the data-ingestor service (default: https://ingest.horux.io) */
  ingestorUrl?: string;

  /** Base URL of the Gateway service (required for cron job check-ins) */
  gatewayUrl?: string;

  /** API authentication token (format: tenant_<tenant-id>_<secret>) */
  apiToken: string;

  /** UUID of the resource sending metrics/logs */
  resourceId: string;

  /**
   * What this process calls itself. Optional.
   *
   * Stored alongside `resourceId` rather than instead of it: `resourceId` names a
   * Horux-registered resource and is required, while this is the sender's own identity
   * — the equivalent of OpenTelemetry's `service.name` resource attribute or
   * Prometheus's `job` label. Set it when one resource receives telemetry from several
   * services and you want them separable; leave it unset and the column is stored NULL.
   *
   * Unlike `resourceId` this needs no lookup in the Horux console: it is a name the
   * process already knows about itself. Also read from HORUX_SERVICE_NAME.
   */
  serviceName?: string;

  /** How often to flush buffered data (milliseconds) */
  flushInterval?: number;

  /** Maximum number of items to send in one request */
  maxBatchSize?: number;

  /**
   * The most items held while the ingestor is throttling or unreachable. Past
   * it dropPolicy decides what goes. The bound is what keeps a rate limit from
   * turning into unbounded memory growth inside the host application.
   * Defaults to 10x maxBatchSize.
   */
  maxBufferSize?: number;

  /** Which items to discard once maxBufferSize is reached (default: 'oldest') */
  dropPolicy?: DropPolicy;

  /**
   * Bounds for the wait after a failed or throttled flush (milliseconds). A
   * server-supplied Retry-After takes precedence. Default to 1s and 60s.
   */
  minRetryBackoff?: number;
  maxRetryBackoff?: number;

  /**
   * Called when the client discards data, with the stream name, the number of
   * items and the reason. Notifications are throttled so a sustained outage
   * does not itself become a flood.
   */
  onDrop?: (stream: 'metrics' | 'logs', count: number, reason: string) => void;

  /** HTTP request timeout (milliseconds) */
  httpTimeout?: number;

  /** Instance-specific metadata (e.g., hostname, pod name, container ID) */
  instanceMetadata?: Record<string, any>;

  /** Automatically add instance metadata to metrics as labels */
  autoAddInstanceLabels?: boolean;
}

export type MetricType = 'counter' | 'gauge' | 'histogram' | 'summary';

export type LogLevel = 'trace' | 'debug' | 'info' | 'warn' | 'error' | 'fatal';

export interface Metric {
  name: string;
  type: MetricType;
  value?: number;
  dimensions?: Record<string, string>; // Infrastructure/instance identifiers
  labels?: Record<string, string>;     // Metric-specific labels
  timestamp?: Date;
  buckets?: HistogramBucket[];
  sum?: number;
  count?: number;
  quantiles?: SummaryQuantile[];
}

export interface HistogramBucket {
  upper_bound: number;
  count: number;
}

export interface SummaryQuantile {
  quantile: number;
  value: number;
}

export interface LogEntry {
  timestamp?: Date;
  level: LogLevel;
  message: string;
  fields?: Record<string, any>;
  source?: string;
  trace_id?: string;
  span_id?: string;
  parent_span_id?: string;
  request_id?: string;
}

export interface MetricsPushRequest {
  resource_id: string;
  /** Omitted when unset, so an existing client sends the body it always did. */
  service_name?: string;
  metrics: Metric[];
}

export interface MetricsPushResponse {
  /** How many metrics were taken, always the first `accepted` of the batch. */
  accepted: number;

  /** How many were not taken. */
  rejected: number;

  message: string;

  /**
   * Why items were rejected: rate_limit, storage_quota, buffer_full or
   * backend_unavailable. Only rate_limit and buffer_full clear by waiting.
   */
  reason?: string;

  /** How long to wait before retrying the rejected items. */
  retry_after_ms?: number;
}

export interface LogsPushRequest {
  resource_id: string;
  /** Omitted when unset — see MetricsPushRequest.service_name. */
  service_name?: string;
  format: string;
  dimensions?: Record<string, any>; // Infrastructure/instance identifiers
  logs: LogEntry[];
}

export interface LogsPushResponse {
  /** How many log entries were taken, always the first `accepted` of the batch. */
  accepted: number;

  /** How many were not taken. */
  rejected: number;

  message: string;

  /**
   * Why items were rejected: rate_limit, storage_quota, buffer_full or
   * backend_unavailable. Only rate_limit and buffer_full clear by waiting.
   */
  reason?: string;

  /** How long to wait before retrying the rejected items. */
  retry_after_ms?: number;
}

/**
 * Server-side ingestion statistics.
 *
 * Only the buffer block is returned to a tenant token. The writer counters the
 * endpoint used to include - total metrics written, batches written, write
 * errors - were server-wide, spanning every tenant, so they are now returned
 * only to the internal service key. They are deliberately absent from this type
 * rather than present and always zero: a counter that reads as fact and is not
 * is worse than no counter, and zero is indistinguishable from a genuinely idle
 * ingestor.
 */
export interface MetricsStats {
  /**
   * This ingestor instance's backpressure - the same condition a push response
   * reports as `buffer_full`, and the part a client can act on.
   */
  buffer: {
    queuedMetrics: number;
    capacity: number;
    utilizationPercent: number;

    /**
     * Data the server-side buffer took and then shed because its writer could
     * not keep up: loss that has already happened and that no client-side retry
     * can recover, so it is the figure worth alerting on.
     */
    droppedMetrics: number;
    droppedBatches: number;
  };
}

/** As {@link MetricsStats}, for the logs endpoint. */
export interface LogsStats {
  buffer: {
    queuedLogs: number;
    capacity: number;
    utilizationPercent: number;
    droppedLogs: number;
    droppedBatches: number;
  };
}

export interface CheckInResponse {
  success: boolean;
  message: string;
  nextExpectedAt: string;
}
