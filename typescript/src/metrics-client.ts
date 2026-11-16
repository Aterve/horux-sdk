import {
  HoruxConfig,
  Metric,
  HistogramBucket,
  SummaryQuantile,
  MetricsPushRequest,
  MetricsStats,
} from './types';
import { ResolvedHoruxConfig, resolveConfig } from './config';
import { Dispatcher, PushOutcome, QueueStats, doPush } from './dispatcher';

export class MetricsClient {
  private config: ResolvedHoruxConfig;
  private dispatcher: Dispatcher<Metric>;

  constructor(config: HoruxConfig) {
    this.config = resolveConfig(config);
    this.dispatcher = new Dispatcher<Metric>(this.config, 'metrics', (items) => this.send(items));
  }

  /**
   * Record a counter metric (value always increases)
   */
  async counter(name: string, value: number, labels?: Record<string, string>): Promise<void> {
    const [dimensions, userLabels] = this.getDimensionsAndLabels(labels);
    return this.record({
      name,
      type: 'counter',
      value,
      dimensions,
      labels: userLabels,
      timestamp: new Date(),
    });
  }

  /**
   * Record a gauge metric (value can go up or down)
   */
  async gauge(name: string, value: number, labels?: Record<string, string>): Promise<void> {
    const [dimensions, userLabels] = this.getDimensionsAndLabels(labels);
    return this.record({
      name,
      type: 'gauge',
      value,
      dimensions,
      labels: userLabels,
      timestamp: new Date(),
    });
  }

  /**
   * Record a histogram metric (for distributions)
   */
  async histogram(
    name: string,
    buckets: HistogramBucket[],
    sum: number,
    count: number,
    labels?: Record<string, string>
  ): Promise<void> {
    const [dimensions, userLabels] = this.getDimensionsAndLabels(labels);
    return this.record({
      name,
      type: 'histogram',
      buckets,
      sum,
      count,
      dimensions,
      labels: userLabels,
      timestamp: new Date(),
    });
  }

  /**
   * Record a summary metric (for quantiles/percentiles)
   */
  async summary(
    name: string,
    quantiles: SummaryQuantile[],
    sum: number,
    count: number,
    labels?: Record<string, string>
  ): Promise<void> {
    const [dimensions, userLabels] = this.getDimensionsAndLabels(labels);
    return this.record({
      name,
      type: 'summary',
      quantiles,
      sum,
      count,
      dimensions,
      labels: userLabels,
      timestamp: new Date(),
    });
  }

  /**
   * Queue a metric for delivery.
   *
   * This never performs I/O: sending happens on the flush cycle, so a
   * throttled ingestor cannot slow down or block the calling code.
   */
  private async record(metric: Metric): Promise<void> {
    this.dispatcher.add(metric);
  }

  /**
   * Separate dimensions (instance metadata) from labels (user-provided)
   * Returns [dimensions, labels]
   */
  private getDimensionsAndLabels(
    userLabels?: Record<string, string>
  ): [Record<string, string> | undefined, Record<string, string> | undefined] {
    let dimensions: Record<string, string> | undefined;

    // Add instance metadata as dimensions if enabled
    if (this.config.autoAddInstanceLabels && this.config.instanceMetadata) {
      dimensions = {};
      for (const [key, value] of Object.entries(this.config.instanceMetadata)) {
        dimensions[key] = String(value);
      }
    }

    return [dimensions, userLabels];
  }

  /**
   * Flush all buffered metrics to the ingestor.
   *
   * Metrics are sent in batches of at most maxBatchSize. If the ingestor can
   * only take part of what is pending, the remainder stays queued for the next
   * cycle and this throws to say why.
   */
  async flush(): Promise<void> {
    await this.dispatcher.flush();
  }

  /**
   * The state of the client-side send queue: what is pending, what has been
   * dropped, and whether the client is currently backing off.
   */
  queueStats(): QueueStats {
    return this.dispatcher.stats();
  }

  /**
   * Deliver one batch to the ingestor.
   */
  private async send(metrics: Metric[]): Promise<PushOutcome> {
    const request: MetricsPushRequest = {
      resource_id: this.config.resourceId,
      // Spread rather than `service_name: this.config.serviceName`, so the key is
      // genuinely absent when unset instead of present-and-undefined.
      ...(this.config.serviceName ? { service_name: this.config.serviceName } : {}),
      metrics,
    };

    return doPush(this.config, '/api/v1/metrics/push', request, metrics.length);
  }

  /**
   * Get metrics ingestion stats from the data ingestor
   */
  async stats(): Promise<MetricsStats> {
    const url = `${this.config.ingestorUrl}/api/v1/metrics/stats`;

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.config.httpTimeout);

    try {
      const response = await fetch(url, {
        method: 'GET',
        headers: {
          Authorization: `Bearer ${this.config.apiToken}`,
        },
        signal: controller.signal,
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(`Ingestor returned error: ${response.status} - ${JSON.stringify(error)}`);
      }

      const data = (await response.json()) as {
        buffer?: Record<string, number>;
      };
      const buffer = data.buffer ?? {};

      return {
        buffer: {
          queuedMetrics: buffer.queued_metrics ?? 0,
          capacity: buffer.capacity ?? 0,
          utilizationPercent: buffer.utilization_percent ?? 0,
          droppedMetrics: buffer.dropped_metrics ?? 0,
          droppedBatches: buffer.dropped_batches ?? 0,
        },
      };
    } finally {
      clearTimeout(timeout);
    }
  }

  /**
   * Stop the flusher and flush remaining metrics
   */
  async close(): Promise<void> {
    await this.dispatcher.close();
  }
}
