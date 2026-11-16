import { HoruxConfig } from './types';
import { MetricsClient } from './metrics-client';
import { LogsClient } from './logs-client';
import { CronClient } from './cron-client';

/**
 * Unified client for metrics, logs, and cron job check-ins
 */
export class HoruxClient {
  public readonly metrics: MetricsClient;
  public readonly logs: LogsClient;
  public readonly cron?: CronClient;

  constructor(config: HoruxConfig) {
    this.metrics = new MetricsClient(config);
    this.logs = new LogsClient(config);

    if (config.gatewayUrl) {
      this.cron = new CronClient(config.gatewayUrl, config.httpTimeout);
    }
  }

  /**
   * Close both metrics and logs clients
   */
  async close(): Promise<void> {
    await Promise.all([this.metrics.close(), this.logs.close()]);
  }
}
