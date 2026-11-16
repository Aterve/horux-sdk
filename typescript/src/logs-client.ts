import { HoruxConfig, LogLevel, LogEntry, LogsPushRequest, LogsStats } from './types';
import { ResolvedHoruxConfig, resolveConfig } from './config';
import { Dispatcher, PushOutcome, QueueStats, doPush } from './dispatcher';

export class LogsClient {
  private config: ResolvedHoruxConfig;
  private dispatcher: Dispatcher<LogEntry>;

  constructor(config: HoruxConfig) {
    this.config = resolveConfig(config);
    this.dispatcher = new Dispatcher<LogEntry>(this.config, 'logs', (items) => this.send(items));
  }

  /**
   * Log a message with the specified level
   */
  async log(level: LogLevel, message: string, fields?: Record<string, any>): Promise<void> {
    return this.record({
      level,
      message,
      fields,
      timestamp: new Date(),
    });
  }

  /**
   * Log a trace-level message
   */
  async trace(message: string, fields?: Record<string, any>): Promise<void> {
    return this.log('trace', message, fields);
  }

  /**
   * Log a debug-level message
   */
  async debug(message: string, fields?: Record<string, any>): Promise<void> {
    return this.log('debug', message, fields);
  }

  /**
   * Log an info-level message
   */
  async info(message: string, fields?: Record<string, any>): Promise<void> {
    return this.log('info', message, fields);
  }

  /**
   * Log a warning-level message
   */
  async warn(message: string, fields?: Record<string, any>): Promise<void> {
    return this.log('warn', message, fields);
  }

  /**
   * Log an error-level message
   */
  async error(message: string, fields?: Record<string, any>): Promise<void> {
    return this.log('error', message, fields);
  }

  /**
   * Log a fatal-level message
   */
  async fatal(message: string, fields?: Record<string, any>): Promise<void> {
    return this.log('fatal', message, fields);
  }

  /**
   * Log with distributed tracing context
   */
  async withTrace(
    level: LogLevel,
    message: string,
    fields: Record<string, any> | undefined,
    traceId: string,
    spanId?: string,
    parentSpanId?: string,
    requestId?: string
  ): Promise<void> {
    return this.record({
      level,
      message,
      fields,
      trace_id: traceId,
      span_id: spanId,
      parent_span_id: parentSpanId,
      request_id: requestId,
      timestamp: new Date(),
    });
  }

  /**
   * Queue a log entry for delivery.
   *
   * This never performs I/O: sending happens on the flush cycle, so a
   * throttled ingestor cannot slow down or block the calling code.
   */
  private async record(entry: LogEntry): Promise<void> {
    this.dispatcher.add(entry);
  }

  /**
   * Flush all buffered logs to the ingestor.
   *
   * Logs are sent in batches of at most maxBatchSize. If the ingestor can only
   * take part of what is pending, the remainder stays queued for the next
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
  private async send(logs: LogEntry[]): Promise<PushOutcome> {
    const request: LogsPushRequest = {
      resource_id: this.config.resourceId,
      // See the note in metrics-client.ts.
      ...(this.config.serviceName ? { service_name: this.config.serviceName } : {}),
      format: 'json',
      dimensions: this.config.instanceMetadata, // Infrastructure/instance identifiers
      logs,
    };

    return doPush(this.config, '/api/v1/logs/push', request, logs.length);
  }

  /**
   * Get logs ingestion stats from the data ingestor
   */
  async stats(): Promise<LogsStats> {
    const url = `${this.config.ingestorUrl}/api/v1/logs/stats`;

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
          queuedLogs: buffer.queued_logs ?? 0,
          capacity: buffer.capacity ?? 0,
          utilizationPercent: buffer.utilization_percent ?? 0,
          droppedLogs: buffer.dropped_logs ?? 0,
          droppedBatches: buffer.dropped_batches ?? 0,
        },
      };
    } finally {
      clearTimeout(timeout);
    }
  }

  /**
   * Stop the flusher and flush remaining logs
   */
  async close(): Promise<void> {
    await this.dispatcher.close();
  }
}
